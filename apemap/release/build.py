"""Build immutable, validated release dataset bundles."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import duckdb

from apemap.analysis import (
    classify_attendance_person,
    get_opening_day_education_context,
    export_analysis_report,
)
from apemap.constants import (
    PROCESSED_DIR,
    PARLIAMENT_METADATA,
    TEMPORAL_WARNING,
    supported_parliaments,
)
from apemap.coverage import export_parliament_coverage
from apemap.contracts import WEB_SCHEMA_VERSION
from apemap.db import (
    ensure_spatial,
    get_connection,
    init_schema,
)
from apemap.export import (
    _file_sha256,
    _get_git_commit,
    _web_parliaments,
    export_results_summary,
    export_web_schools_geojson,
    validate_source_snapshot_dates,
    web_school_records,
)
from apemap.validate import validate_database

logger = logging.getLogger(__name__)

RELEASE_SCHEMA_VERSION = "1.0.0"

# Tables included in public data/ export
PUBLIC_CANONICAL_TABLES = [
    "members",
    "parliament_service",
    "institutions",
    "member_education",
    "school_snapshots",
    "electoral_boundaries",
    "education_sector_benchmarks",
    "school_finance_benchmarks",
    "school_public_funding",
]

# Sensitive or restricted finance fields that MUST NOT leak into public data/
RESTRICTED_FINANCE_COLUMNS = {
    "fees_charges_parent_total",
    "other_private_sources_total",
    "total_gross_income_total",
    "total_net_recurrent_income_total",
    "recurrent_funding_gov_per_student",
    "recurrent_funding_state_per_student",
    "fees_charges_parent_per_student",
    "other_private_sources_per_student",
    "total_gross_income_per_student",
    "total_net_recurrent_income_per_student",
    "financial_profile_2021",
}


def build_release(
    conn: duckdb.DuckDBPyConnection | None = None,
    db_path: Path | str | None = None,
    output_dir: Path | str | None = None,
    version: str = "1.0.0",
    parliaments: list[int] | None = None,
    finance_reporting_year: int = 2021,
    strict: bool = True,
    *,
    source_commit: str | None = None,
    generated_at: str | None = None,
    source_snapshot_dates: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    """Execute complete release build for public analysis, web, and data artifacts.

    Args:
        conn: Optional active DuckDB connection.
        db_path: Path to DuckDB file (ignored if conn is provided).
        output_dir: Root release output directory.
        version: Release semantic version.
        parliaments: Target parliaments.
        finance_reporting_year: Reporting year for school finances.
        strict: If True, fails if database validation asserts any failures.
        source_commit: Explicit Git commit SHA.
        generated_at: Fixed ISO 8601 timestamp for deterministic builds.
        source_snapshot_dates: Recorded upstream source snapshot dates.

    Returns:
        Structured summary dictionary of release build outputs.
    """
    out_dir = Path(output_dir or (PROCESSED_DIR / f"release-v{version}")).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    target_parls = _web_parliaments(parliaments)
    snapshot_dates = validate_source_snapshot_dates(source_snapshot_dates)

    should_close_conn = conn is None
    active_conn = conn or get_connection(db_path)
    try:
        init_schema(active_conn)
        ensure_spatial(active_conn)
        from apemap.review.integration import review_snapshot_metadata

        # Read the revision actually consumed by ingestion, never a newer working log.
        review_snapshot = review_snapshot_metadata(active_conn)

        # 1. Validation Gate
        logger.info("Validating canonical database before release build...")
        val_report = validate_database(active_conn, target_parls)
        if strict and not val_report.passed:
            bullet_errors = "\n".join(f"  - {f}" for f in val_report.failures)
            raise RuntimeError(
                f"Release build aborted: database validation failed with {len(val_report.failures)} errors:\n{bullet_errors}"
            )

        # 2. Build Analysis Artifacts
        logger.info("Building analysis artifacts in %s/analysis...", out_dir)
        export_analysis_report(
            active_conn,
            output_dir=out_dir,
            parliaments=target_parls,
            finance_reporting_year=finance_reporting_year,
        )

        # 3. Build Web Artifacts
        web_dir = out_dir / "web"
        web_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Building web artifacts in %s...", web_dir)

        # 3a. web/assertions.json
        assertions_path = web_dir / "assertions.json"
        assertions_data = {
            "schema_version": "1.0.0",
            "passed": val_report.passed,
            "checks_run": val_report.checks_run,
            "checks_passed": val_report.checks_passed,
            "failures": val_report.failures,
            "validated_parliaments": target_parls,
        }
        assertions_path.write_text(
            json.dumps(assertions_data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        # 3b. web/schools.geojson
        export_web_schools_geojson(
            active_conn,
            web_dir,
            target_parls,
            finance_reporting_year=finance_reporting_year,
        )

        # 3c. web/results-summary.json
        export_results_summary(active_conn, web_dir, target_parls)
        export_parliament_coverage(
            active_conn, out_dir / "analysis", target_parls, finance_reporting_year
        )
        # Public historical layers retain the existing school-feature web contract.
        for p in target_parls:
            term_dir = web_dir / f"parliament_{p}"
            layer_path = export_web_schools_geojson(
                active_conn,
                term_dir,
                [p],
                finance_reporting_year=finance_reporting_year,
            )
            layer_path.replace(web_dir / f"parliament_{p}_combined.geojson")
            term_dir.rmdir()

        # 3d. web/members.json
        members_path = web_dir / "members.json"
        _export_web_members(
            active_conn, members_path, target_parls, finance_reporting_year
        )

        # 3e. web/metadata.json
        commit_sha = source_commit if source_commit is not None else _get_git_commit()
        if generated_at is not None:
            gen_timestamp = generated_at
        elif "SOURCE_DATE_EPOCH" in os.environ:
            gen_timestamp = datetime.fromtimestamp(
                int(os.environ["SOURCE_DATE_EPOCH"]), tz=timezone.utc
            ).isoformat()
        else:
            gen_timestamp = datetime.now(timezone.utc).isoformat()

        web_metadata = {
            "web_schema_version": WEB_SCHEMA_VERSION,
            "release_version": version,
            "source_commit": commit_sha,
            "generated_at": gen_timestamp,
            "parliaments": target_parls,
            "supported_parliaments": supported_parliaments(),
            "parliament_metadata": {
                str(p): PARLIAMENT_METADATA[p] for p in target_parls
            },
            "temporal_warning": TEMPORAL_WARNING,
            "cohort": "opening_day",
            "licensing": "Creative Commons Attribution 4.0 International / ACARA / APH",
            "attribution": "APEMAP — Australian Parliamentarians Education Map",
            "review_snapshot": review_snapshot,
        }
        (web_dir / "metadata.json").write_text(
            json.dumps(web_metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        # 4. Build Data Artifacts (Public CSV and Parquet)
        data_dir = out_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Building public data artifacts in %s...", data_dir)
        _export_public_data_tables(active_conn, data_dir, target_parls)

        # 5. Inventory and Manifest Generation
        logger.info("Indexing release files and generating manifest.json...")
        manifest_files: dict[str, dict[str, Any]] = {}
        sha256sums_lines: list[str] = []

        # Find all files in out_dir relative to out_dir
        all_files = sorted(
            [
                p
                for p in out_dir.rglob("*")
                if p.is_file() and p.name not in ("manifest.json", "SHA256SUMS")
            ]
        )

        for fpath in all_files:
            rel_path = fpath.relative_to(out_dir).as_posix()
            f_size = fpath.stat().st_size
            f_sha = _file_sha256(fpath)

            manifest_files[rel_path] = {
                "path": rel_path,
                "size_bytes": f_size,
                "sha256": f_sha,
            }
            sha256sums_lines.append(f"{f_sha}  {rel_path}\n")

        manifest_data = {
            "release_schema_version": RELEASE_SCHEMA_VERSION,
            "web_schema_version": WEB_SCHEMA_VERSION,
            "data_release_version": version,
            "source_commit": commit_sha,
            "generated_at": gen_timestamp,
            "source_snapshot_dates": snapshot_dates,
            "review_snapshot": review_snapshot,
            "cohort_definition": "opening_day",
            "parliaments": target_parls,
            "supported_parliaments": supported_parliaments(),
            "parliament_metadata": {
                str(p): PARLIAMENT_METADATA[p] for p in target_parls
            },
            "files": manifest_files,
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
            encoding="utf-8",
        )

        sha256sums_path = out_dir / "SHA256SUMS"
        sha256sums_path.write_text("".join(sha256sums_lines), encoding="utf-8")

        return {
            "version": version,
            "output_directory": str(out_dir),
            "manifest": str(manifest_path),
            "sha256sums": str(sha256sums_path),
            "files_count": len(manifest_files),
            "total_bytes": sum(f["size_bytes"] for f in manifest_files.values()),
        }

    finally:
        if should_close_conn:
            active_conn.close()


def _export_web_members(
    conn: duckdb.DuckDBPyConnection,
    output_path: Path,
    parliaments: list[int],
    finance_reporting_year: int = 2024,
) -> None:
    """Export opening-day members list and their secondary education summary to JSON."""
    schools = web_school_records(conn, parliaments, finance_reporting_year)
    member_schools: dict[str, list[dict[str, Any]]] = {}
    for school in schools.values():
        context = {
            key: value
            for key, value in school.items()
            if key
            not in (
                "members",
                "member_count",
                "education_assertions",
                "parliaments",
                "provider_contexts",
            )
        }
        context["sector"] = school["school_sector"]
        for evidence in school["education_assertions"]:
            member_schools.setdefault(evidence["member_id"], []).append(
                {**context, **evidence, "sector": evidence["school_sector"]}
            )
    contexts = {p: get_opening_day_education_context(conn, p) for p in parliaments}

    members_query = """
    WITH ranked_service AS (
        SELECT
            s.service_id,
            s.member_id,
            s.parliament_number,
            s.chamber,
            s.party,
            s.party_abbrev,
            s.electorate,
            s.state_or_territory,
            ROW_NUMBER() OVER (
                PARTITION BY s.member_id, s.parliament_number
                ORDER BY COALESCE(s.service_start, '1900-01-01'::DATE), s.service_id
            ) AS rank
        FROM parliament_service s
        WHERE s.parliament_number IN (SELECT UNNEST(?))
          AND s.is_opening_day_member = TRUE
    )
    SELECT
        m.member_id,
        m.display_name,
        m.family_name,
        m.given_name,
        m.gender,
        m.date_of_birth,
        s.parliament_number,
        s.chamber,
        s.party,
        s.party_abbrev,
        s.electorate,
        s.state_or_territory
    FROM ranked_service s
    JOIN members m ON s.member_id = m.member_id
    WHERE s.rank = 1
    ORDER BY s.parliament_number, m.family_name, m.given_name, m.member_id
    """
    rows = conn.execute(members_query, [parliaments]).fetchall()

    members_by_parl: dict[str, list[dict[str, Any]]] = {}
    for (
        mid,
        display,
        fam,
        giv,
        gender,
        dob,
        p_num,
        chamber,
        party,
        party_abbrev,
        electorate,
        state,
    ) in rows:
        classification = classify_attendance_person(
            [context for context in contexts[p_num] if context["member_id"] == mid]
        )
        p_key = str(p_num)

        members_by_parl.setdefault(p_key, []).append(
            {
                "member_id": mid,
                "display_name": display,
                "family_name": fam,
                "given_name": giv,
                "gender": gender,
                "date_of_birth": str(dob) if dob else None,
                "parliament_number": p_num,
                "chamber": chamber,
                "party": party,
                "party_abbrev": party_abbrev or party,
                "electorate": electorate,
                "state_or_territory": state,
                **classification,
                "schools": member_schools.get(mid, []),
            }
        )

    output_path.write_text(
        json.dumps(
            {"web_schema_version": WEB_SCHEMA_VERSION, "parliaments": members_by_parl},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _export_public_data_tables(
    conn: duckdb.DuckDBPyConnection,
    data_dir: Path,
    parliaments: list[int],
) -> None:
    """Export canonical public tables to CSV and Parquet omitting restricted finance fields."""
    # 1. School Snapshots: explicitly omit financial_profile_2021
    snap_query = """
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
    ORDER BY institution_id, snapshot_year
    """
    snap_df = conn.execute(snap_query).df()
    snap_df.to_csv(data_dir / "school_snapshots.csv", index=False, encoding="utf-8")
    conn.execute(
        f"COPY ({snap_query}) TO ? (FORMAT PARQUET)",
        [str(data_dir / "school_snapshots.parquet")],
    )

    # 2. Other Public Canonical Tables
    tables_to_export = [
        ("parliament_metadata", "parliament_number"),
        ("members", "member_id"),
        ("parliament_service", "service_id"),
        ("institutions", "institution_id"),
        ("member_education", "education_id"),
        ("education_sector_benchmarks", "benchmark_year, sector"),
        (
            "school_finance_benchmarks",
            "reporting_year, state_or_territory, sector, geolocation, metric",
        ),
        (
            "school_public_funding",
            "institution_id, reporting_year, metric, source_dataset",
        ),
    ]

    for tbl_name, order_cols in tables_to_export:
        # Check if table exists in active connection
        exists = conn.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
            [tbl_name],
        ).fetchone()
        if not exists or exists[0] == 0:
            continue

        q = f"SELECT * FROM {tbl_name} ORDER BY {order_cols}"
        tbl_df = conn.execute(q).df()
        tbl_df.to_csv(data_dir / f"{tbl_name}.csv", index=False, encoding="utf-8")
        conn.execute(
            f"COPY ({q}) TO ? (FORMAT PARQUET)",
            [str(data_dir / f"{tbl_name}.parquet")],
        )

    # 3. Electoral Boundaries (Parquet only, as it contains spatial geometries)
    b_exists = conn.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'electoral_boundaries'"
    ).fetchone()
    if b_exists and b_exists[0] > 0:
        conn.execute(
            """
            COPY (
                SELECT boundary_id, election_year, electorate, state_or_territory, geometry,
                       source_url, source_dataset, retrieved_at
                FROM electoral_boundaries
                ORDER BY boundary_id
            ) TO ? (FORMAT PARQUET)
            """,
            [str(data_dir / "electoral_boundaries.parquet")],
        )
