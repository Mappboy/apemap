"""Canonical export pipelines for APEMAP.

Exports deterministic Parquet tables, GeoJSON spatial layers per parliament term,
and analytical summaries to data/processed/.
"""

from __future__ import annotations

from apemap.constants import supported_parliaments
from apemap.constants import PARLIAMENT_METADATA, TEMPORAL_WARNING, select_parliaments
from apemap.coverage import export_parliament_coverage
from apemap.contracts import (
    WEB_SCHEMA_VERSION,
    SUCCESSOR_FOOTNOTE,
    SUCCESSOR_LOCATION_WARNING,
)

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
from apemap.education_context import resolve_school_display_locations

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

    query = f"""
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
        attended_school_name AS school_name,
        school_type,
        school_sector,
        campus_type,
        school_state,
        school_suburb,
        school_postcode,
        display_longitude AS longitude,
        display_latitude AS latitude,
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
        historical_2021_net_recurrent_income_per_student,
        {", ".join(CONTEXT_COLUMNS)},
        {", ".join(HISTORICAL_EVIDENCE_COLUMNS)}
    FROM v_member_secondary_education
    WHERE parliament_number = ?
      AND display_longitude IS NOT NULL
      AND display_latitude IS NOT NULL
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
                "institution_id": row["attended_school_id"] or row["education_id"],
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

            props.update({key: row[key] for key in CONTEXT_COLUMNS})
            props.update({key: row[key] for key in HISTORICAL_EVIDENCE_COLUMNS})
            # Finance context is independent of the attendance location.
            # Retrieve finance estimate or observed value for the requested year
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
        geojson_path.write_text(
            json.dumps(geojson_data, indent=2), newline="\n", encoding="utf-8"
        )
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

        schools = web_school_records(conn, [p])
        total_schools = len(schools)
        mapped_count = sum(
            school["longitude"] is not None and school["latitude"] is not None
            for school in schools.values()
        )
        unmapped_count = total_schools - mapped_count
        profile_years = sorted(
            {
                provider["profile_year"]
                for school in schools.values()
                for provider in school["provider_contexts"]
                if provider["profile_year"] is not None
            }
        )
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
            "successor_sensitivity": sec.get("successor_sensitivity", {}),
            "source_years": {
                "abs_benchmark_year": next(iter(benchmark_years))
                if len(benchmark_years) == 1
                else None,
                "profile_year": profile_years[0] if len(profile_years) == 1 else None,
                "profile_years": profile_years,
            },
        }

    payload = {
        "web_schema_version": WEB_SCHEMA_VERSION,
        "cohort": "opening_day",
        "supported_parliaments": supported_parliaments(),
        "parliament_metadata": {str(p): PARLIAMENT_METADATA[p] for p in target_parls},
        "temporal_warning": TEMPORAL_WARNING,
        "parliaments": summary_by_parl,
    }

    target_path = out_dir / "results-summary.json"
    target_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        newline="\n",
        encoding="utf-8",
    )
    logger.info("Exported results summary to %s", target_path)
    return target_path


PROFILE_COLUMNS = (
    "total_enrolments",
    "girls_enrolments",
    "boys_enrolments",
    "fte_enrolments",
    "icsea",
    "icsea_percentile",
    "sea_bottom_quarter_pct",
    "sea_lower_middle_quarter_pct",
    "sea_upper_middle_quarter_pct",
    "sea_top_quarter_pct",
    "indigenous_enrolments_pct",
    "lbote_pct",
    "year_range",
    "remoteness_category",
)
CONTEXT_COLUMNS = (
    "attended_school_id",
    "attended_school_name",
    "identity_basis",
    "attended_institution_id",
    "resolved_institution_id",
    "resolved_institution_name",
    "is_successor",
    "location_basis",
    "location_source_url",
    "attendance_longitude",
    "attendance_latitude",
    "attendance_location_eligible",
    "broad_sector",
    "detailed_sector",
    "sector_basis",
    "detailed_sector_basis",
    "sector_source_url",
    "detailed_sector_source_url",
    "sector_conflict",
    "location_conflict",
    "campus_continuity_conflict",
    "continuity_discrepancy",
    "profile_institution_id",
    "profile_basis",
    "finance_institution_id",
    "finance_basis",
)
HISTORICAL_EVIDENCE_COLUMNS = (
    "recorded_school_id",
    "attended_identity_source_url",
    "historical_scope_confirmed",
    "historical_context_scope",
    "historical_latitude",
    "historical_longitude",
    "historical_location_source_url",
    "campus_continuity",
    "campus_continuity_source_url",
    "historical_broad_sector",
    "historical_broad_sector_source_url",
    "historical_detailed_sector",
    "historical_detailed_sector_source_url",
)
EVIDENCE_COLUMNS = (
    *HISTORICAL_EVIDENCE_COLUMNS,
    "education_id",
    "member_id",
    "school_name_as_recorded",
    "source_url",
    "retrieved_at",
    "confidence",
    "attended_status",
    "institution_resolution",
    "resolution_source_url",
)


def web_education_context_rows(
    conn: duckdb.DuckDBPyConnection, parliaments: list[int]
) -> list[dict[str, Any]]:
    """Read assertions with opening-day service and independent reviewed context."""
    cursor = conn.execute(
        """SELECT c.*, i.acara_id AS resolved_acara_id,
            i.state AS resolved_state, i.suburb AS resolved_suburb,
            i.postcode AS resolved_postcode, i.school_type AS resolved_school_type,
            i.campus_type AS resolved_campus_type,
            original.state AS original_state, original.suburb AS original_suburb,
            original.postcode AS original_postcode, original.school_type AS original_school_type,
            original.campus_type AS original_campus_type,
            m.display_name AS member_name, ps.service_id, ps.parliament_number,
            ps.party, ps.party_abbrev, ps.chamber
        FROM v_education_attendance_context c
        JOIN institutions i ON c.resolved_institution_id = i.institution_id
        LEFT JOIN institutions original ON c.attended_institution_id = original.institution_id
        JOIN members m ON c.member_id = m.member_id
        JOIN parliament_service ps ON c.member_id = ps.member_id
        WHERE c.level = 'secondary'
          AND ps.parliament_number IN (SELECT UNNEST(?)) AND ps.is_opening_day_member
        ORDER BY c.attended_school_id, c.education_id, ps.parliament_number, ps.service_id""",
        [parliaments],
    )
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def merge_school_contexts(
    school: dict[str, Any], contexts: list[dict[str, Any]]
) -> None:
    """Retain assertion providers and suppress ambiguous aggregate dimensions."""
    provider_keys = (
        "resolved_institution_id",
        "resolved_institution_name",
        "profile_institution_id",
        "profile_basis",
        "profile_year",
        "finance_institution_id",
        "finance_basis",
        "finance_year",
        "finance_status",
    )
    providers: list[dict[str, Any]] = []
    for context in contexts:
        provider = {key: context[key] for key in provider_keys}
        if provider not in providers:
            providers.append(provider)
    providers.sort(
        key=lambda provider: (
            provider["resolved_institution_id"] or "",
            provider["profile_basis"],
            provider["profile_year"] or -1,
            provider["finance_basis"],
            provider["finance_year"] or -1,
            provider["finance_status"] or "",
        )
    )
    school["provider_contexts"] = providers
    identity_rank = {
        "original_verified": 4,
        "original_reference": 3,
        "recorded_name_provisional": 2,
        "unresolved": 0,
    }
    identity = max(
        contexts,
        key=lambda context: (
            identity_rank[context["identity_basis"]],
            context["school_name"],
        ),
    )
    for key in ("identity_basis", "attended_school_name", "school_name"):
        school[key] = identity[key]
    original_ids = {
        context["attended_institution_id"]
        for context in contexts
        if context["attended_institution_id"] is not None
    }
    school["attended_institution_id"] = (
        next(iter(original_ids)) if len(original_ids) == 1 else None
    )
    school["is_successor"] = any(context["is_successor"] for context in contexts)
    for flag in (
        "sector_conflict",
        "location_conflict",
        "campus_continuity_conflict",
        "continuity_discrepancy",
    ):
        school[flag] = any(context[flag] for context in contexts)
    if len({context["resolved_institution_id"] for context in contexts}) > 1:
        for key in (
            *PROFILE_COLUMNS,
            "resolved_institution_id",
            "acara_id",
            "profile_institution_id",
            "finance_institution_id",
            "finance_value",
            "finance_metric",
            "finance_status",
            "finance_method",
            "finance_source",
            "profile_year",
            "finance_year",
            "resolved_state",
            "resolved_suburb",
            "resolved_postcode",
        ):
            school[key] = None
        school["resolved_institution_name"] = " / ".join(
            sorted({context["resolved_institution_name"] for context in contexts})
        )
        school["profile_basis"] = school["finance_basis"] = "multiple_providers"
    for value_key, basis_key, source_key in (
        ("broad_sector", "sector_basis", "sector_source_url"),
        ("detailed_sector", "detailed_sector_basis", "detailed_sector_source_url"),
    ):
        known = [context for context in contexts if context[value_key] is not None]
        values = {context[value_key] for context in known}
        if len(values) > 1:
            school[value_key], school[basis_key], school[source_key] = (
                None,
                "unresolved",
                None,
            )
            school["sector_conflict"] = True
        elif known:
            rank = {
                "historical_verified": 3,
                "original_reference": 2,
                "successor_assumption": 1,
            }
            chosen = max(known, key=lambda context: rank.get(context[basis_key], 0))
            for key in (value_key, basis_key, source_key):
                school[key] = chosen[key]
    school["school_sector"] = school["detailed_sector"] or "Other"
    school["government_non_government"] = school["broad_sector"] or "Other"
    candidates, location_conflict = resolve_school_display_locations(contexts)
    location_keys = (
        "location_basis",
        "longitude",
        "latitude",
        "attendance_longitude",
        "attendance_latitude",
        "attendance_location_eligible",
        "location_source_url",
        "location_institution_id",
        "location_institution_name",
        "state",
        "suburb",
        "postcode",
        "location_warning",
    )
    if location_conflict:
        for key in location_keys:
            school[key] = None
        school["location_basis"] = "unresolved"
        school["attendance_location_eligible"] = False
        school["location_conflict"] = True
    else:
        for key in location_keys:
            values = {context[key] for context in candidates}
            school[key] = next(iter(values)) if len(values) == 1 else None
        school["location_source_url"] = min(
            (
                context["location_source_url"]
                for context in candidates
                if context["location_source_url"]
            ),
            default=None,
        )
    school["display_school_name"] = (
        f"{school['school_name']} → {school['resolved_institution_name']}*"
        if school["is_successor"]
        else school["school_name"]
    )
    school["successor_footnote"] = (
        SUCCESSOR_FOOTNOTE if school["is_successor"] else None
    )


def web_school_records(
    conn: duckdb.DuckDBPyConnection,
    parliaments: list[int],
    finance_reporting_year: int = 2024,
) -> dict[str, dict[str, Any]]:
    """Build complete attended-school records; coordinates only govern map display."""
    cursor = conn.execute(
        """SELECT * FROM school_snapshots QUALIFY ROW_NUMBER() OVER (
        PARTITION BY institution_id ORDER BY snapshot_year DESC) = 1"""
    )
    names = [column[0] for column in cursor.description]
    snapshots = {
        row[0]: dict(zip(names, row, strict=True)) for row in cursor.fetchall()
    }
    peer_metrics = backtest_finance_benchmarks(conn).get("peer_group_metrics", {})
    finance: dict[str, dict[str, Any]] = {}
    schools: dict[str, dict[str, Any]] = {}
    contexts_by_school: dict[str, list[dict[str, Any]]] = {}
    assertion_ids: dict[str, set[str]] = {}
    members: dict[str, dict[str, dict[str, Any]]] = {}
    for row in web_education_context_rows(conn, parliaments):
        # A legacy assertion lacking a portable original ID is still searchable,
        # but must not merge with other unresolved assertions.
        iid = row["attended_school_id"] or row["education_id"]
        profile_id, finance_id = (
            row["profile_institution_id"],
            row["finance_institution_id"],
        )
        if finance_id not in finance:
            finance[finance_id] = compute_school_finance_estimate(
                conn,
                finance_id,
                target_year=finance_reporting_year,
                peer_group_metrics=peer_metrics,
            )
        fin = finance[finance_id]
        snap = snapshots.get(profile_id, {})
        school = {key: row[key] for key in CONTEXT_COLUMNS}
        school.update({key: snap.get(key) for key in PROFILE_COLUMNS})
        original_location = row["location_basis"] == "original_verified" or (
            row["is_successor"] and row["location_basis"] == "unresolved"
        )
        locality = "original" if original_location else "resolved"
        attended_name = (
            row["attended_school_name"]
            or row["school_name_as_recorded"]
            or ("Original school unknown" if row["is_successor"] else "Unknown school")
        )
        school.update(
            {
                "institution_id": iid,
                "acara_id": row["resolved_acara_id"],
                "school_name": attended_name,
                "display_school_name": (
                    f"{attended_name} → {row['resolved_institution_name']}*"
                    if row["is_successor"]
                    else row["attended_school_name"]
                    or row["school_name_as_recorded"]
                    or "Unknown school"
                ),
                "school_sector": row["detailed_sector"] or "Other",
                "government_non_government": row["broad_sector"] or "Other",
                "school_type": row[f"{locality}_school_type"],
                "campus_type": row[f"{locality}_campus_type"],
                "state": row[f"{locality}_state"],
                "suburb": row[f"{locality}_suburb"],
                "postcode": row[f"{locality}_postcode"],
                "resolved_state": row["resolved_state"],
                "resolved_suburb": row["resolved_suburb"],
                "resolved_postcode": row["resolved_postcode"],
                "location_institution_id": row["attended_institution_id"]
                if original_location
                else row["resolved_institution_id"],
                "location_institution_name": row["attended_school_name"]
                if original_location
                else row["resolved_institution_name"],
                "longitude": row["display_longitude"],
                "latitude": row["display_latitude"],
                "profile_year": snap.get("snapshot_year"),
                "finance_year": fin.get("reporting_year"),
                "finance_metric": fin.get("metric"),
                "finance_value": fin.get("value"),
                "finance_status": fin.get("status"),
                "finance_method": fin.get("method"),
                "finance_source": fin.get("source"),
                "temporal_warning": TEMPORAL_WARNING,
                "successor_footnote": SUCCESSOR_FOOTNOTE
                if row["is_successor"]
                else None,
                "location_warning": SUCCESSOR_LOCATION_WARNING
                if row["location_basis"] == "successor_unverified"
                else None,
                "education_assertions": [],
                "parliaments": [],
            }
        )
        incoming = school
        incoming.pop("education_assertions")
        incoming.pop("parliaments")
        if iid not in schools:
            schools[iid] = {**incoming, "education_assertions": [], "parliaments": []}
            assertion_ids[iid], members[iid] = set(), {}
        if incoming not in contexts_by_school.setdefault(iid, []):
            contexts_by_school[iid].append(incoming)
        school = schools[iid]
        if row["education_id"] not in assertion_ids[iid]:
            evidence = {**incoming, **{key: row[key] for key in EVIDENCE_COLUMNS}}
            if evidence["retrieved_at"] is not None:
                evidence["retrieved_at"] = evidence["retrieved_at"].isoformat()
            school["education_assertions"].append(evidence)
            assertion_ids[iid].add(row["education_id"])
        pnum = row["parliament_number"]
        if pnum not in school["parliaments"]:
            school["parliaments"].append(pnum)
        person = members[iid].setdefault(
            row["member_id"],
            {
                "member_id": row["member_id"],
                "name": row["member_name"],
                "parliaments": [],
                "services": [],
            },
        )
        if pnum not in person["parliaments"]:
            person["parliaments"].append(pnum)
        if not any(
            service["service_id"] == row["service_id"] for service in person["services"]
        ):
            person["services"].append(
                {
                    key: row[key]
                    for key in (
                        "service_id",
                        "parliament_number",
                        "party",
                        "party_abbrev",
                        "chamber",
                    )
                }
            )
    for iid, school in schools.items():
        merge_school_contexts(school, contexts_by_school[iid])
        school["parliaments"].sort()
        school["members"] = sorted(
            members[iid].values(), key=lambda m: (m["name"], m["member_id"])
        )
        school["member_count"] = len(school["members"])
        for person in school["members"]:
            person["parliaments"].sort()
    return schools


def export_web_schools_geojson(
    conn: duckdb.DuckDBPyConnection,
    output_dir: Path | str | None = None,
    parliaments: list[int] | None = None,
    *,
    finance_reporting_year: int = 2024,
) -> Path:
    """Export one mapped feature per distinct attended school, retaining context."""
    out_dir = Path(output_dir or PROCESSED_DIR).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    schools = web_school_records(
        conn, _web_parliaments(parliaments), finance_reporting_year
    )
    features = [
        {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [school["longitude"], school["latitude"]],
            },
            "properties": school,
        }
        for school in sorted(
            schools.values(),
            key=lambda school: (school["school_name"], school["institution_id"]),
        )
        if school["longitude"] is not None and school["latitude"] is not None
    ]
    target = out_dir / "schools.geojson"
    target.write_text(
        json.dumps(
            {
                "web_schema_version": WEB_SCHEMA_VERSION,
                "type": "FeatureCollection",
                "name": "schools",
                "features": features,
            },
            indent=2,
        )
        + "\n",
        newline="\n",
        encoding="utf-8",
    )
    return target


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
    edu_df.to_csv(csv_path, index=False, encoding="utf-8", lineterminator="\n")

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
        "web_schema_version": WEB_SCHEMA_VERSION,
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
        json.dumps(manifest_data, indent=2, sort_keys=True) + "\n",
        newline="\n",
        encoding="utf-8",
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
