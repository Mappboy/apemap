"""Canonical export pipelines for APEMAP.

Exports deterministic Parquet tables, GeoJSON spatial layers per parliament term,
and analytical summaries to data/processed/.
"""

from __future__ import annotations

from apemap.constants import supported_parliaments
from apemap.constants import PARLIAMENT_METADATA, TEMPORAL_WARNING, select_parliaments
from apemap.coverage import export_parliament_coverage

from collections.abc import Mapping
from datetime import date, datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
from typing import Any

import duckdb

from apemap.analysis import (
    backtest_finance_benchmarks,
    compute_school_finance_estimate,
    compute_sector_summary,
    export_analysis_report,
)
from apemap.constants import PROCESSED_DIR, PROJECT_ROOT
from apemap.db import export_to_parquet

logger = logging.getLogger(__name__)


def export_canonical_parquet(
    conn: duckdb.DuckDBPyConnection,
    output_dir: Path | str | None = None,
) -> dict[str, Path]:
    """Export all canonical tables to Parquet files.

    Args:
        conn: DuckDB database connection.
        output_dir: Destination directory.

    Returns:
        Mapping of table names to output file paths.
    """
    out_dir = Path(output_dir or PROCESSED_DIR).resolve()
    return export_to_parquet(conn, out_dir)


def export_spatial_geojson(
    conn: duckdb.DuckDBPyConnection,
    output_dir: Path | str | None = None,
    parliaments: list[int] | None = None,
    *,
    cohort: str = "opening_day",
    finance_reporting_year: int = 2024,
) -> dict[int, Path]:
    """Export secondary school attendance spatial layers to GeoJSON.

    Produces RFC 7946 GeoJSON FeatureCollections for each parliament term
    containing school geographic coordinates and parliamentarian metadata.

    Args:
        conn: DuckDB database connection.
        output_dir: Destination directory.
        parliaments: List of parliaments to export (default: supported_parliaments()).

    Returns:
        Mapping of parliament number to GeoJSON file path.
    """
    out_dir = Path(output_dir or PROCESSED_DIR).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    target_parls = select_parliaments(parliaments)
    finance_cache: dict[str, dict[str, Any]] = {}
    peer_metrics = backtest_finance_benchmarks(conn).get("peer_group_metrics", {})
    if cohort not in ("opening_day", "all_service"):
        raise ValueError("cohort must be opening_day or all_service")

    exported: dict[int, Path] = {}

    query = """
    SELECT
        education_id,
        service_id,
        member_id,
        display_name,
        family_name,
        given_name,
        gender,
        date_of_birth,
        chamber,
        party,
        party_abbrev,
        electorate,
        state_or_territory,
        is_opening_day_member,
        is_current_member,
        institution_id,
        acara_id,
        school_name,
        school_type,
        school_sector,
        campus_type,
        school_state,
        school_suburb,
        school_postcode,
        longitude,
        latitude,
        years_attended,
        graduation_year,
        attended_status,
        confidence,
        source_url,
        retrieved_at,
        service_source_url,
        service_start,
        service_end,
        school_name_as_recorded,
        institution_resolution,
        resolution_source_url,
        snapshot_year,
        historical_2021_net_recurrent_income_per_student
    FROM v_member_secondary_education
    WHERE parliament_number = ?
      AND longitude IS NOT NULL
      AND latitude IS NOT NULL
      AND (? = 'all_service' OR is_opening_day_member)
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY education_id, service_id ORDER BY snapshot_year DESC NULLS LAST
    ) = 1
    ORDER BY display_name, school_name, education_id, member_id, institution_id
    """

    for p in target_parls:
        df = conn.execute(query, [p, cohort]).df().astype(object)
        df = df.where(df.notna(), None)

        features: list[dict[str, Any]] = []
        for _, row in df.iterrows():
            lon = float(row["longitude"])
            lat = float(row["latitude"])
            props: dict[str, Any] = {
                "education_id": row["education_id"],
                "service_id": row["service_id"],
                "cohort": cohort,
                "parliament_number": p,
                "source_url": row["source_url"],
                "retrieved_at": str(row["retrieved_at"])
                if row["retrieved_at"]
                else None,
                "service_source_url": row["service_source_url"],
                "service_start": str(row["service_start"])
                if row["service_start"]
                else None,
                "service_end": str(row["service_end"]) if row["service_end"] else None,
                "school_name_as_recorded": row["school_name_as_recorded"],
                "institution_resolution": row["institution_resolution"],
                "resolution_source_url": row["resolution_source_url"],
                "profile_year": int(row["snapshot_year"])
                if row["snapshot_year"]
                else None,
                "temporal_warning": TEMPORAL_WARNING,
                "member_id": row["member_id"],
                "member": row["display_name"],
                "family_name": row["family_name"],
                "given_name": row["given_name"],
                "gender": row["gender"],
                "dob": str(row["date_of_birth"])
                if row["date_of_birth"] is not None
                else None,
                "chamber": row["chamber"],
                "party": row["party"],
                "party_abbrev": row["party_abbrev"],
                "electorate": row["electorate"],
                "state_or_territory": row["state_or_territory"],
                "is_opening_day_member": bool(row["is_opening_day_member"]),
                "is_current_member": bool(row["is_current_member"]),
                "institution_id": row["institution_id"],
                "acara_id": row["acara_id"],
                "school_name": row["school_name"],
                "school_type": row["school_type"],
                "school_sector": row["school_sector"],
                "campus_type": row["campus_type"],
                "school_state": row["school_state"],
                "school_suburb": row["school_suburb"],
                "school_postcode": row["school_postcode"],
                "years_attended": row["years_attended"],
                "graduation_year": int(row["graduation_year"])
                if row["graduation_year"] is not None
                and str(row["graduation_year"]).isdigit()
                else None,
                "attended_status": row["attended_status"],
                "confidence": row["confidence"],
                "historical_2021_net_recurrent_income_per_student": int(
                    row["historical_2021_net_recurrent_income_per_student"]
                )
                if row["historical_2021_net_recurrent_income_per_student"] is not None
                and str(
                    row["historical_2021_net_recurrent_income_per_student"]
                ).isdigit()
                else None,
            }

            # Retrieve finance estimate or observed value for 2024
            inst_id = str(row["institution_id"])
            if inst_id not in finance_cache:
                finance_cache[inst_id] = compute_school_finance_estimate(
                    conn,
                    inst_id,
                    target_year=finance_reporting_year,
                    peer_group_metrics=peer_metrics,
                )
            fin_est = finance_cache[inst_id]

            # Retrieve jurisdictional public funding if available
            pub_fund_row = conn.execute(
                """
                SELECT
                    reporting_year,
                    jurisdiction,
                    funding_model,
                    metric,
                    value,
                    unit,
                    source_dataset
                FROM school_public_funding
                WHERE institution_id = ?
                ORDER BY reporting_year DESC, CASE WHEN metric LIKE '%total%' THEN 0 ELSE 1 END, value DESC
                LIMIT 1
                """,
                [inst_id],
            ).fetchone()

            props.update(
                {
                    "finance_year": fin_est["reporting_year"],
                    "finance_metric": fin_est["metric"],
                    "finance_value": fin_est["value"],
                    "finance_status": fin_est["status"],
                    "finance_method": fin_est["method"],
                    "finance_peer_group": fin_est["peer_group"],
                    "finance_source": fin_est["source"],
                    "public_funding_year": int(pub_fund_row[0])
                    if pub_fund_row
                    else None,
                    "public_funding_jurisdiction": str(pub_fund_row[1])
                    if pub_fund_row
                    else None,
                    "public_funding_model": str(pub_fund_row[2])
                    if pub_fund_row
                    else None,
                    "public_funding_metric": str(pub_fund_row[3])
                    if pub_fund_row
                    else None,
                    "public_funding_value": float(pub_fund_row[4])
                    if pub_fund_row
                    else None,
                    "public_funding_unit": str(pub_fund_row[5])
                    if pub_fund_row
                    else None,
                    "public_funding_source": str(pub_fund_row[6])
                    if pub_fund_row
                    else None,
                }
            )
            feature = {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [lon, lat],
                },
                "properties": props,
            }
            features.append(feature)

        geojson_data = {
            "type": "FeatureCollection",
            "name": f"parliament_{p}_combined",
            "crs": {
                "type": "name",
                "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"},
            },
            "features": features,
        }

        geojson_path = out_dir / f"parliament_{p}_combined.geojson"
        geojson_path.write_text(json.dumps(geojson_data, indent=2), encoding="utf-8")
        exported[p] = geojson_path
        logger.info(
            "Exported Parliament %d GeoJSON layer to %s (%d features)",
            p,
            geojson_path,
            len(features),
        )

    return exported


def _file_sha256(path: Path) -> str:
    """Compute hex SHA-256 digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _get_git_commit() -> str:
    """Identify this source checkout, never the caller's repository."""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--show-toplevel", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        root, commit = res.stdout.strip().splitlines()
        return commit if Path(root).resolve() == PROJECT_ROOT.resolve() else "unknown"
    except (OSError, ValueError, subprocess.SubprocessError):
        return "unknown"


def _web_parliaments(parliaments: list[int] | None) -> list[int]:
    """Normalize release ordering and reject empty or non-integer selections."""
    values = supported_parliaments() if parliaments is None else parliaments
    if not values or any(type(p) is not int for p in values):
        raise ValueError(
            "A web release requires at least one integer parliament number"
        )
    return sorted(set(values))


def validate_source_snapshot_dates(
    dates: Mapping[str, str | None] | None,
) -> dict[str, str | None]:
    """Retain supplied upstream dates, marking unrecorded source dates as null."""
    message = (
        "Source snapshot dates must be a JSON object mapping source names "
        "to YYYY-MM-DD strings or null"
    )
    if dates is not None and not isinstance(dates, Mapping):
        raise ValueError(message)
    normalized: dict[str, str | None] = dict.fromkeys(("aph", "acara", "abs", "aec"))
    for source, value in (dates or {}).items():
        if not isinstance(source, str) or not source:
            raise ValueError(message)
        if value is not None:
            if not isinstance(value, str):
                raise ValueError(message)
            try:
                valid_date = date.fromisoformat(value).isoformat() == value
            except ValueError:
                valid_date = False
            if not valid_date:
                raise ValueError(message)
        normalized[source] = value
    return normalized


def export_results_summary(
    conn: duckdb.DuckDBPyConnection,
    output_dir: Path | str | None = None,
    parliaments: list[int] | None = None,
) -> Path:
    """Export lightweight static results summary JSON for server-free rendering.

    Args:
        conn: DuckDB database connection.
        output_dir: Destination directory.
        parliaments: Target parliament numbers.

    Returns:
        Path to generated results-summary.json.
    """
    out_dir = Path(output_dir or PROCESSED_DIR).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    target_parls = _web_parliaments(parliaments)

    summary_by_parl = {}
    for p in target_parls:
        sec = compute_sector_summary(conn, p)

        # Unique school counts and mapped coordinates
        schools_query = """
        SELECT
            i.institution_id,
            i.sector,
            i.longitude,
            i.latitude,
            profiles.profile_year
        FROM member_education me
        JOIN institutions i ON me.institution_id = i.institution_id
        JOIN parliament_service ps ON me.member_id = ps.member_id
        LEFT JOIN (
            SELECT institution_id, MAX(snapshot_year) AS profile_year
            FROM school_snapshots GROUP BY institution_id
        ) profiles ON i.institution_id = profiles.institution_id
        WHERE me.level = 'secondary'
          AND ps.parliament_number = ?
          AND ps.is_opening_day_member = TRUE
        GROUP BY i.institution_id, i.sector, i.longitude, i.latitude, profiles.profile_year
        """
        school_rows = conn.execute(schools_query, [p]).fetchall()
        total_schools = len(school_rows)
        mapped_count = sum(
            1 for r in school_rows if r[2] is not None and r[3] is not None
        )
        unmapped_count = total_schools - mapped_count
        profile_years = sorted({r[4] for r in school_rows if r[4] is not None})
        benchmark_years = {
            row["benchmark_year"] for row in sec["benchmark_comparison"].values()
        }

        summary_by_parl[str(p)] = {
            "parliament_number": p,
            "cohort": "opening_day",
            "total_parliamentarians": sec["total_parliamentarians"],
            "known_education_count": sec["known_school_denominator"],
            "missing_education_count": sec["parliamentarians_without_known_schools"],
            "known_school_denominator": sec["known_school_denominator"],
            "government_non_government": sec["government_non_government"],
            "government_non_government_percentages": sec[
                "government_non_government_percentages"
            ],
            "detailed_sector": sec["detailed_sector"],
            "detailed_sector_percentages": sec["percentage_of_known_parliamentarians"],
            "abs_sector_benchmark": sec.get("benchmark_comparison", {}),
            "number_of_represented_schools": total_schools,
            "represented_schools_by_sector": sec["unique_schools"]["by_sector"],
            "represented_schools_government_non_government": sec["unique_schools"][
                "government_non_government"
            ],
            "mapped_schools_count": mapped_count,
            "unmapped_schools_count": unmapped_count,
            "source_years": {
                "abs_benchmark_year": next(iter(benchmark_years))
                if len(benchmark_years) == 1
                else None,
                "profile_year": profile_years[0] if len(profile_years) == 1 else None,
                "profile_years": profile_years,
            },
        }

    payload = {
        "web_schema_version": "1.0.0",
        "cohort": "opening_day",
        "supported_parliaments": supported_parliaments(),
        "parliament_metadata": {str(p): PARLIAMENT_METADATA[p] for p in target_parls},
        "temporal_warning": TEMPORAL_WARNING,
        "parliaments": summary_by_parl,
    }

    target_path = out_dir / "results-summary.json"
    target_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    logger.info("Exported results summary to %s", target_path)
    return target_path


def export_web_schools_geojson(
    conn: duckdb.DuckDBPyConnection,
    output_dir: Path | str | None = None,
    parliaments: list[int] | None = None,
    *,
    finance_reporting_year: int = 2024,
) -> Path:
    """Export deduplicated school-level GeoJSON with attendance context for explorer.

    Produces one GeoJSON Feature per unique educational institution.

    Args:
        conn: DuckDB database connection.
        output_dir: Destination directory.
        parliaments: Target parliament numbers.

    Returns:
        Path to generated schools.geojson.
    """
    out_dir = Path(output_dir or PROCESSED_DIR).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    target_parls = _web_parliaments(parliaments)

    schools_query = """
    SELECT DISTINCT
        i.institution_id,
        i.acara_id,
        i.school_name,
        i.sector AS school_sector,
        CASE
            WHEN i.sector = 'Government' THEN 'Government'
            WHEN i.sector IN ('Catholic', 'Independent') THEN 'Non-government'
            ELSE 'Other'
        END AS government_non_government,
        i.school_type,
        i.campus_type,
        i.state,
        i.suburb,
        i.postcode,
        i.longitude,
        i.latitude
    FROM member_education me
    JOIN institutions i ON me.institution_id = i.institution_id
    JOIN parliament_service ps ON me.member_id = ps.member_id
    WHERE me.level = 'secondary'
      AND ps.parliament_number IN (SELECT UNNEST(?))
      AND ps.is_opening_day_member = TRUE
      AND i.longitude IS NOT NULL
      AND i.latitude IS NOT NULL
    ORDER BY i.school_name, i.institution_id
    """
    school_rows = conn.execute(schools_query, [target_parls]).fetchall()
    peer_metrics = backtest_finance_benchmarks(conn).get("peer_group_metrics", {})

    # Load latest snapshot metrics per institution
    snapshots_by_inst: dict[str, dict[str, Any]] = {}
    snap_rows = conn.execute(
        """
        SELECT
            institution_id,
            snapshot_year,
            total_enrolments,
            girls_enrolments,
            boys_enrolments,
            fte_enrolments,
            icsea,
            icsea_percentile,
            sea_bottom_quarter_pct,
            sea_lower_middle_quarter_pct,
            sea_upper_middle_quarter_pct,
            sea_top_quarter_pct,
            indigenous_enrolments_pct,
            lbote_pct,
            year_range,
            remoteness_category
        FROM school_snapshots
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY institution_id ORDER BY snapshot_year DESC
        ) = 1
        ORDER BY institution_id
        """
    ).fetchall()
    for s in snap_rows:
        iid = s[0]
        if iid not in snapshots_by_inst:
            snapshots_by_inst[iid] = {
                "profile_year": s[1],
                "total_enrolments": s[2],
                "girls_enrolments": s[3],
                "boys_enrolments": s[4],
                "fte_enrolments": s[5],
                "icsea": s[6],
                "icsea_percentile": s[7],
                "sea_bottom_quarter_pct": s[8],
                "sea_lower_middle_quarter_pct": s[9],
                "sea_upper_middle_quarter_pct": s[10],
                "sea_top_quarter_pct": s[11],
                "indigenous_enrolments_pct": s[12],
                "lbote_pct": s[13],
                "year_range": s[14],
                "remoteness_category": s[15],
            }

    # Load parliamentarian attendances for these schools
    att_query = """
    SELECT DISTINCT
        me.institution_id,
        m.member_id,
        m.display_name,
        ps.party,
        ps.party_abbrev,
        ps.chamber,
        ps.parliament_number,
        ps.service_id
    FROM member_education me
    JOIN members m ON me.member_id = m.member_id
    JOIN parliament_service ps ON m.member_id = ps.member_id
    WHERE me.level = 'secondary'
      AND ps.parliament_number IN (SELECT UNNEST(?))
      AND ps.is_opening_day_member = TRUE
    ORDER BY me.institution_id, m.display_name, m.member_id, ps.parliament_number, ps.service_id
    """
    members_by_inst: dict[str, dict[str, dict[str, Any]]] = {}
    parliaments_by_inst: dict[str, set[int]] = {}
    assertions_by_inst: dict[str, list[dict[str, Any]]] = {}
    evidence_columns = [
        "institution_id",
        "education_id",
        "member_id",
        "school_name_as_recorded",
        "source_url",
        "retrieved_at",
        "confidence",
        "attended_status",
        "institution_resolution",
        "resolution_source_url",
    ]
    evidence_rows = conn.execute(
        """SELECT DISTINCT me.institution_id, me.education_id, me.member_id,
        me.school_name_as_recorded, me.source_url, me.retrieved_at, me.confidence,
        me.attended_status, me.institution_resolution, me.resolution_source_url
        FROM member_education me WHERE me.level='secondary' AND EXISTS (
            SELECT 1 FROM parliament_service ps WHERE ps.member_id=me.member_id
            AND ps.parliament_number IN (SELECT UNNEST(?)) AND ps.is_opening_day_member)
        ORDER BY me.institution_id, me.education_id""",
        [target_parls],
    ).fetchall()
    for evidence_row in evidence_rows:
        evidence = dict(zip(evidence_columns, evidence_row, strict=True))
        if evidence["retrieved_at"]:
            evidence["retrieved_at"] = evidence["retrieved_at"].isoformat()
        iid = evidence.pop("institution_id")
        assertions_by_inst.setdefault(iid, []).append(evidence)

    for row in conn.execute(att_query, [target_parls]).fetchall():
        iid, mid, name, party, abbrev, chamber, pnum, service_id = row
        parliaments_by_inst.setdefault(iid, set()).add(pnum)
        inst_mems = members_by_inst.setdefault(iid, {})
        if mid not in inst_mems:
            inst_mems[mid] = {
                "member_id": mid,
                "name": name,
                "parliaments": [],
                "services": [],
            }
        member = inst_mems[mid]
        if pnum not in member["parliaments"]:
            member["parliaments"].append(pnum)
        member["services"].append(
            {
                "service_id": service_id,
                "parliament_number": pnum,
                "party": party,
                "party_abbrev": abbrev,
                "chamber": chamber,
            }
        )

    features: list[dict[str, Any]] = []
    for s in school_rows:
        iid = s[0]
        aid = s[1]
        name = s[2]
        sector = s[3]
        gov_non_gov = s[4]
        stype = s[5]
        campus = s[6]
        state = s[7]
        suburb = s[8]
        postcode = s[9]
        lon = float(s[10])
        lat = float(s[11])

        snap = snapshots_by_inst.get(iid, {})
        mems = list(members_by_inst.get(iid, {}).values())
        parls = sorted(parliaments_by_inst.get(iid, set()))

        fin_est = compute_school_finance_estimate(
            conn,
            iid,
            target_year=finance_reporting_year,
            peer_group_metrics=peer_metrics,
        )

        props: dict[str, Any] = {
            "institution_id": iid,
            "acara_id": aid,
            "school_name": name,
            "school_sector": sector,
            "government_non_government": gov_non_gov,
            "school_type": stype,
            "campus_type": campus,
            "year_range": snap.get("year_range"),
            "state": state,
            "suburb": suburb,
            "postcode": postcode,
            "remoteness_category": snap.get("remoteness_category"),
            "longitude": lon,
            "latitude": lat,
            "profile_year": snap.get("profile_year"),
            "total_enrolments": snap.get("total_enrolments"),
            "girls_enrolments": snap.get("girls_enrolments"),
            "boys_enrolments": snap.get("boys_enrolments"),
            "fte_enrolments": snap.get("fte_enrolments"),
            "icsea": snap.get("icsea"),
            "icsea_percentile": snap.get("icsea_percentile"),
            "sea_bottom_quarter_pct": snap.get("sea_bottom_quarter_pct"),
            "sea_lower_middle_quarter_pct": snap.get("sea_lower_middle_quarter_pct"),
            "sea_upper_middle_quarter_pct": snap.get("sea_upper_middle_quarter_pct"),
            "sea_top_quarter_pct": snap.get("sea_top_quarter_pct"),
            "indigenous_enrolments_pct": snap.get("indigenous_enrolments_pct"),
            "lbote_pct": snap.get("lbote_pct"),
            "member_count": len(mems),
            "members": mems,
            "education_assertions": assertions_by_inst.get(iid, []),
            "parliaments": parls,
            "finance_year": fin_est.get("reporting_year"),
            "finance_metric": fin_est.get("metric"),
            "finance_value": fin_est.get("value"),
            "finance_status": fin_est.get("status"),
            "finance_method": fin_est.get("method"),
            "finance_source": fin_est.get("source"),
            "temporal_warning": TEMPORAL_WARNING,
        }

        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [lon, lat],
                },
                "properties": props,
            }
        )

    geojson_data = {
        "type": "FeatureCollection",
        "name": "schools",
        "crs": {
            "type": "name",
            "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"},
        },
        "features": features,
    }

    target_path = out_dir / "schools.geojson"
    target_path.write_text(json.dumps(geojson_data, indent=2) + "\n", encoding="utf-8")
    logger.info("Exported %d school features to %s", len(features), target_path)
    return target_path


def export_research_downloads(
    conn: duckdb.DuckDBPyConnection,
    output_dir: Path | str | None = None,
    parliaments: list[int] | None = None,
) -> dict[str, Path]:
    """Export opening-day attendance CSV and annual school profiles.

    CSV grain is (education_id, service_id), enriched with the latest available
    profile per school. All annual profile rows remain in the separate Parquet.

    Args:
        conn: DuckDB database connection.
        output_dir: Destination directory.
        parliaments: Target parliament numbers.

    Returns:
        Mapping of download filenames to output Paths.
    """
    out_dir = Path(output_dir or PROCESSED_DIR).resolve()
    downloads_dir = out_dir / "downloads"
    downloads_dir.mkdir(parents=True, exist_ok=True)
    target_parls = _web_parliaments(parliaments)

    # 1. parliament-education.csv
    csv_path = downloads_dir / "parliament-education.csv"
    edu_df = conn.execute(
        """
        SELECT *
        FROM v_member_secondary_education
        WHERE parliament_number IN (SELECT UNNEST(?))
          AND is_opening_day_member = TRUE
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY education_id, service_id ORDER BY snapshot_year DESC NULLS LAST
        ) = 1
        ORDER BY parliament_number, display_name, school_name, education_id, service_id
        """,
        [target_parls],
    ).df()
    edu_df.to_csv(csv_path, index=False, encoding="utf-8")

    # 2. school-profiles.parquet
    parquet_path = downloads_dir / "school-profiles.parquet"
    conn.execute(
        """
        COPY (
            SELECT
                s.*,
                i.school_name,
                i.sector,
                i.school_type,
                i.campus_type,
                i.state,
                i.suburb,
                i.postcode,
                i.longitude,
                i.latitude
            FROM school_snapshots s
            JOIN institutions i ON s.institution_id = i.institution_id
            ORDER BY s.institution_id, s.snapshot_year
        ) TO ? (FORMAT PARQUET)
        """,
        [str(parquet_path)],
    )

    logger.info("Exported research downloads to %s", downloads_dir)
    return {
        "parliament-education.csv": csv_path,
        "school-profiles.parquet": parquet_path,
    }


def export_web_release_manifest(
    output_dir: Path | str | None = None,
    files: dict[str, Path] | None = None,
    parliaments: list[int] | None = None,
    data_release_version: str = "0.2.0",
    generated_at: str | None = None,
    *,
    source_commit: str | None = None,
    source_snapshot_dates: Mapping[str, str | None] | None = None,
) -> Path:
    """Generate cryptographic manifest.json for published web release datasets.

    Args:
        output_dir: Destination directory.
        files: Mapping of relative release paths to local file Paths.
        parliaments: Included parliament numbers.
        data_release_version: Release semantic version.
        generated_at: ISO 8601 timestamp string (uses SOURCE_DATE_EPOCH or now if None).
        source_commit: Explicit source SHA for builds without the source checkout.
        source_snapshot_dates: Recorded upstream dates; absent dates remain null.

    Returns:
        Path to generated manifest.json.
    """
    out_dir = Path(output_dir or PROCESSED_DIR).resolve()
    target_parls = _web_parliaments(parliaments)
    snapshot_dates = validate_source_snapshot_dates(source_snapshot_dates)
    out_dir.mkdir(parents=True, exist_ok=True)
    target_files = files or {}

    file_entries: dict[str, dict[str, Any]] = {}
    for rel_name, fpath in target_files.items():
        if fpath.exists():
            file_entries[rel_name] = {
                "path": rel_name,
                "size_bytes": fpath.stat().st_size,
                "sha256": _file_sha256(fpath),
            }

    commit_sha = source_commit if source_commit is not None else _get_git_commit()
    if generated_at is not None:
        gen_timestamp = generated_at
    elif "SOURCE_DATE_EPOCH" in os.environ:
        gen_timestamp = datetime.fromtimestamp(
            int(os.environ["SOURCE_DATE_EPOCH"]), tz=timezone.utc
        ).isoformat()
    else:
        gen_timestamp = datetime.now(timezone.utc).isoformat()

    manifest_data = {
        "web_schema_version": "1.0.0",
        "data_release_version": data_release_version,
        "source_commit": commit_sha,
        "generated_at": gen_timestamp,
        "source_snapshot_dates": snapshot_dates,
        "cohort_definition": "opening_day",
        "parliaments": target_parls,
        "supported_parliaments": supported_parliaments(),
        "parliament_metadata": {str(p): PARLIAMENT_METADATA[p] for p in target_parls},
        "temporal_warning": TEMPORAL_WARNING,
        "files": file_entries,
        "sources": {
            "aph": "Parliamentary Handbook of the Commonwealth of Australia",
            "acara": "Australian Curriculum, Assessment and Reporting Authority (ACARA)",
            "abs": "Australian Bureau of Statistics (ABS) Schools, 2025",
            "aec": "Australian Electoral Commission (AEC) 2025 Federal Electoral Boundaries",
        },
        "licensing": "Creative Commons Attribution 4.0 International / ACARA / APH",
        "attribution": "APEMAP — Australian Parliamentarians Education Map",
    }

    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest_data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    logger.info("Exported release manifest to %s", manifest_path)
    return manifest_path


def export_web_release_bundle(
    conn: duckdb.DuckDBPyConnection,
    output_dir: Path | str | None = None,
    parliaments: list[int] | None = None,
    data_release_version: str = "0.2.0",
    generated_at: str | None = None,
    *,
    source_commit: str | None = None,
    source_snapshot_dates: Mapping[str, str | None] | None = None,
) -> dict[str, Any]:
    """Export complete website release bundle (results summary, schools GeoJSON, downloads, and manifest).

    Args:
        conn: DuckDB database connection.
        output_dir: Destination directory.
        parliaments: Target parliament numbers.
        data_release_version: Release version.
        generated_at: Optional fixed ISO timestamp for deterministic manifest generation.
        source_commit: Explicit source SHA, otherwise resolved from this checkout.
        source_snapshot_dates: Recorded upstream dates; absent dates remain null.

    Returns:
        Mapping of generated web release artifacts.
    """
    out_dir = Path(output_dir or PROCESSED_DIR).resolve()
    target_parls = _web_parliaments(parliaments)
    snapshot_dates = validate_source_snapshot_dates(source_snapshot_dates)

    results_summary_path = export_results_summary(conn, out_dir, target_parls)
    schools_geojson_path = export_web_schools_geojson(conn, out_dir, target_parls)
    downloads = export_research_downloads(conn, out_dir, target_parls)

    manifest_files = {
        "results-summary.json": results_summary_path,
        "schools.geojson": schools_geojson_path,
        "downloads/parliament-education.csv": downloads["parliament-education.csv"],
        "downloads/school-profiles.parquet": downloads["school-profiles.parquet"],
    }
    manifest_path = export_web_release_manifest(
        out_dir,
        files=manifest_files,
        parliaments=target_parls,
        data_release_version=data_release_version,
        generated_at=generated_at,
        source_commit=source_commit,
        source_snapshot_dates=snapshot_dates,
    )

    return {
        "results_summary": str(results_summary_path),
        "schools_geojson": str(schools_geojson_path),
        "research_downloads": {k: str(v) for k, v in downloads.items()},
        "manifest": str(manifest_path),
    }


def export_all_artifacts(
    conn: duckdb.DuckDBPyConnection,
    output_dir: Path | str | None = None,
    parliaments: list[int] | None = None,
    finance_reporting_year: int = 2021,
    include_web_release: bool = False,
    *,
    data_release_version: str = "0.2.0",
    source_commit: str | None = None,
    source_snapshot_dates: Mapping[str, str | None] | None = None,
    cohort: str = "opening_day",
) -> dict[str, Any]:
    """Export all canonical artifacts: Parquet, GeoJSON, and analytical metrics.

    Args:
        conn: DuckDB database connection.
        output_dir: Destination directory.
        parliaments: List of parliaments to process.
        finance_reporting_year: Calendar reporting year for school finances (defaults to 2021).
        include_web_release: If True, also generates the website release bundle.
        data_release_version: Version of the optional web bundle.
        source_commit: Explicit source SHA for the optional web manifest.
        source_snapshot_dates: Recorded upstream dates for the optional web manifest.

    Returns:
        Summary dictionary with paths to all generated artifacts.
    """
    out_dir = Path(output_dir or PROCESSED_DIR).resolve()
    target_parls = parliaments or supported_parliaments()

    parquet_paths = export_canonical_parquet(conn, out_dir)
    geojson_paths = export_spatial_geojson(
        conn,
        out_dir,
        target_parls,
        cohort=cohort,
        finance_reporting_year=finance_reporting_year,
    )
    coverage_paths = export_parliament_coverage(
        conn, out_dir, target_parls, finance_reporting_year
    )
    analysis_path = export_analysis_report(
        conn,
        out_dir,
        target_parls,
        finance_reporting_year=finance_reporting_year,
    )

    artifacts: dict[str, Any] = {
        "output_directory": str(out_dir),
        "parquet_files": {k: str(v) for k, v in parquet_paths.items()},
        "geojson_layers": {str(k): str(v) for k, v in geojson_paths.items()},
        "analysis_report": str(analysis_path),
        **{key: str(path) for key, path in coverage_paths.items()},
    }

    if include_web_release:
        web_bundle = export_web_release_bundle(
            conn,
            out_dir,
            target_parls,
            data_release_version=data_release_version,
            source_commit=source_commit,
            source_snapshot_dates=source_snapshot_dates,
        )
        artifacts.update(web_bundle)

    return artifacts
