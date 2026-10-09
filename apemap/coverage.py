"""Distinct-denominator historical coverage reports from canonical records."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import duckdb

from apemap.analysis import (
    compute_school_finance_estimate,
    backtest_finance_benchmarks,
    get_opening_day_education_context,
)
from apemap.constants import PARLIAMENT_METADATA, TEMPORAL_WARNING, select_parliaments
from apemap.contracts import ANALYSIS_SCHEMA_VERSION
from apemap.education_context import resolve_school_display_locations


def _is_overseas_country(country: str | None) -> bool:
    """Recognize explicit foreign countries and the legacy overseas sentinel."""
    normalized = (country or "").strip().casefold()
    return normalized not in {"", "australia", "au", "aus", "unknown"}


def compute_parliament_coverage(
    conn: duckdb.DuckDBPyConnection,
    parliaments: list[int] | None = None,
    finance_year: int = 2024,
) -> list[dict[str, Any]]:
    """Count people, assertions and institutions separately in the opening cohort."""
    output: list[dict[str, Any]] = []
    estimates: dict[str, dict[str, Any]] = {}
    peer_metrics = backtest_finance_benchmarks(conn).get("peer_group_metrics", {})
    for p in select_parliaments(parliaments):
        people = conn.execute(
            """SELECT m.member_id, m.date_of_birth FROM members m
            WHERE EXISTS (SELECT 1 FROM parliament_service ps WHERE ps.member_id=m.member_id
                AND ps.parliament_number=? AND ps.is_opening_day_member) ORDER BY m.member_id""",
            [p],
        ).fetchall()
        assertions = get_opening_day_education_context(conn, p)

        def school_key(row: dict[str, Any]) -> str:
            return row["attended_school_id"] or f"unresolved:{row['education_id']}"

        schools = {school_key(row) for row in assertions}
        school_contexts: dict[str, list[dict[str, Any]]] = {}
        for row in assertions:
            school_contexts.setdefault(school_key(row), []).append(row)
        displayed_schools = 0
        for contexts in school_contexts.values():
            selected, conflicting = resolve_school_display_locations(
                contexts,
                longitude_key="display_longitude",
                latitude_key="display_latitude",
            )
            if (
                not conflicting
                and selected
                and selected[0]["display_longitude"] is not None
                and selected[0]["display_latitude"] is not None
            ):
                displayed_schools += 1
        institutions = {row["resolved_institution_id"] for row in assertions}
        metadata = {
            row[0]: {"acara_id": row[1], "country": row[2], "status": row[3]}
            for row in conn.execute(
                "SELECT institution_id, acara_id, country, institution_status FROM institutions"
            ).fetchall()
        }

        def original_metadata(row: dict[str, Any]) -> dict[str, Any]:
            original_id = row.get("attended_institution_id") or (
                row["resolved_institution_id"] if not row["is_successor"] else None
            )
            return metadata.get(original_id, {})

        for iid in institutions:
            if iid not in estimates:
                estimates[iid] = compute_school_finance_estimate(
                    conn, iid, target_year=finance_year, peer_group_metrics=peer_metrics
                )
        funded = {
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT institution_id FROM school_public_funding WHERE reporting_year=?",
                [finance_year],
            ).fetchall()
        }
        known_people = {
            row["member_id"]
            for row in assertions
            if row["confidence"] in ("verified", "provisional")
        }
        output.append(
            {
                "parliament": p,
                "opening_date": PARLIAMENT_METADATA[p]["opening_date"],
                "cohort": "opening_day",
                "opening_day_members": len(people),
                "members_with_dob": sum(row[1] is not None for row in people),
                "members_with_secondary_school": len(known_people),
                "members_without_secondary_school": len(people) - len(known_people),
                "secondary_education_assertions": len(assertions),
                "represented_schools": len(schools),
                "domestic_schools_matched_acara": len(
                    {
                        school_key(row)
                        for row in assertions
                        if original_metadata(row).get("acara_id")
                        and not _is_overseas_country(
                            original_metadata(row).get("country")
                        )
                    }
                ),
                "schools_linked_to_successor_acara": len(
                    {
                        school_key(row)
                        for row in assertions
                        if row["is_successor"]
                        and metadata[row["resolved_institution_id"]]["acara_id"]
                    }
                ),
                "historical_schools": len(
                    {
                        school_key(row)
                        for row in assertions
                        if original_metadata(row).get("status")
                        in ("historical_only", "closed", "merged")
                        or row["institution_resolution"] in ("rename", "successor")
                    }
                ),
                "overseas_schools": len(
                    {
                        school_key(row)
                        for row in assertions
                        if _is_overseas_country(original_metadata(row).get("country"))
                    }
                ),
                "unresolved_school_records": sum(
                    row["confidence"] == "unconfirmed" for row in assertions
                ),
                "provisional_original_school_identities": len(
                    {
                        school_key(row)
                        for row in assertions
                        if row["identity_basis"] == "recorded_name_provisional"
                    }
                ),
                "unresolved_original_school_identities": sum(
                    row["attended_school_id"] is None for row in assertions
                ),
                "displayed_mapped_schools": displayed_schools,
                "schools_with_verified_attendance_geography": len(
                    {
                        school_key(row)
                        for row in assertions
                        if row["location_basis"]
                        in ("original_verified", "successor_verified_same_campus")
                    }
                ),
                "assertions_with_verified_attendance_geography": sum(
                    row["location_basis"]
                    in ("original_verified", "successor_verified_same_campus")
                    for row in assertions
                ),
                "schools_eligible_attendance_geography": len(
                    {
                        school_key(row)
                        for row in assertions
                        if row["attendance_location_eligible"]
                    }
                ),
                "assertions_eligible_attendance_geography": sum(
                    bool(row["attendance_location_eligible"]) for row in assertions
                ),
                "schools_with_successor_sector_assumptions": len(
                    {
                        school_key(row)
                        for row in assertions
                        if row["sector_basis"] == "successor_assumption"
                    }
                ),
                "finance_reporting_institutions": len(institutions),
                "schools_with_observed_finance": sum(
                    estimates[iid]["status"] == "observed" for iid in institutions
                ),
                "schools_with_estimated_finance": sum(
                    estimates[iid]["status"] not in ("observed", "unavailable")
                    for iid in institutions
                ),
                "schools_with_public_funding": len(institutions & funded),
                "attended_schools_with_finance_context": len(
                    {
                        school_key(row)
                        for row in assertions
                        if estimates[row["resolved_institution_id"]]["status"]
                        != "unavailable"
                    }
                ),
                "finance_reporting_year": finance_year,
            }
        )
    return output


def export_parliament_coverage(
    conn: duckdb.DuckDBPyConnection,
    output_dir: Path,
    parliaments: list[int] | None = None,
    finance_year: int = 2024,
) -> dict[str, Path]:
    """Write deterministic CSV/JSON with explicit denominator definitions."""
    rows = compute_parliament_coverage(conn, parliaments, finance_year)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "parliament_coverage.json"
    csv_path = output_dir / "parliament_coverage.csv"
    json_path.write_text(
        json.dumps(
            {
                "cohort": "opening_day",
                "schema_version": ANALYSIS_SCHEMA_VERSION,
                "temporal_warning": TEMPORAL_WARNING,
                "denominators": {
                    "members": "distinct people",
                    "assertions": "distinct secondary education assertions",
                    "schools": "distinct original attended schools; unresolved assertions remain separate",
                    "finance": "distinct resolved reporting institutions",
                    "mapped": "displayed school points, including successor fallback",
                    "eligible_attendance_geography": "original reference or reviewed original/same-campus locations",
                    "verified_attendance_geography": "independently reviewed historical original/same-campus locations",
                },
                "parliaments": rows,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        newline="\n",
        encoding="utf-8",
    )
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return {"coverage_json": json_path, "coverage_csv": csv_path}
