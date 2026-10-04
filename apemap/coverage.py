"""Distinct-denominator historical coverage reports from canonical records."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import duckdb

from apemap.analysis import compute_school_finance_estimate, backtest_finance_benchmarks
from apemap.constants import PARLIAMENT_METADATA, TEMPORAL_WARNING, select_parliaments


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
        assertions = conn.execute(
            """SELECT DISTINCT me.education_id, me.member_id, me.institution_id, me.confidence,
                i.acara_id, i.country, i.institution_status, me.institution_resolution
            FROM member_education me JOIN institutions i USING (institution_id)
            WHERE me.level='secondary' AND EXISTS (SELECT 1 FROM parliament_service ps
                WHERE ps.member_id=me.member_id AND ps.parliament_number=? AND ps.is_opening_day_member)
            ORDER BY me.education_id""",
            [p],
        ).fetchall()
        institutions = {r[2] for r in assertions}
        for iid in institutions:
            if iid not in estimates:
                estimates[iid] = compute_school_finance_estimate(
                    conn, iid, target_year=finance_year, peer_group_metrics=peer_metrics
                )
        funded = {
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT institution_id FROM school_public_funding WHERE reporting_year=?",
                [finance_year],
            ).fetchall()
        }
        known_people = {r[1] for r in assertions if r[3] in ("verified", "provisional")}
        output.append(
            {
                "parliament": p,
                "opening_date": PARLIAMENT_METADATA[p]["opening_date"],
                "cohort": "opening_day",
                "opening_day_members": len(people),
                "members_with_dob": sum(r[1] is not None for r in people),
                "members_with_secondary_school": len(known_people),
                "members_without_secondary_school": len(people) - len(known_people),
                "secondary_education_assertions": len(assertions),
                "represented_schools": len(institutions),
                "domestic_schools_matched_acara": len(
                    {
                        r[2]
                        for r in assertions
                        if r[4] and not _is_overseas_country(r[5])
                    }
                ),
                "historical_schools": len(
                    {
                        r[2]
                        for r in assertions
                        if r[6] in ("historical_only", "closed", "merged")
                        or r[7] in ("rename", "successor")
                    }
                ),
                "overseas_schools": len(
                    {r[2] for r in assertions if _is_overseas_country(r[5])}
                ),
                "unresolved_school_records": sum(
                    r[3] == "unconfirmed" for r in assertions
                ),
                "schools_with_observed_finance": sum(
                    estimates[iid]["status"] == "observed" for iid in institutions
                ),
                "schools_with_estimated_finance": sum(
                    estimates[iid]["status"] not in ("observed", "unavailable")
                    for iid in institutions
                ),
                "schools_with_public_funding": len(institutions & funded),
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
                "temporal_warning": TEMPORAL_WARNING,
                "denominators": {
                    "members": "distinct people",
                    "assertions": "distinct secondary education assertions",
                    "schools": "distinct canonical institutions",
                },
                "parliaments": rows,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return {"coverage_json": json_path, "coverage_csv": csv_path}
