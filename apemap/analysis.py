"""Deterministic statistical calculations and analytical exports.

The functions in this module are the source of truth for the Jupyter and
Marimo analysis workflows. They use fixed parliament snapshot dates, explicit
denominators, and NULL-aware finance summaries so derived charts can be
reproduced without mutating the canonical database.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb

from apemap.constants import PARLIAMENT_METADATA, PROCESSED_DIR

logger = logging.getLogger(__name__)

ANALYSIS_SCHEMA_VERSION = "1.0"


def parse_date_safe(val: date | str | None) -> date | None:
    """Parse a date from a string (YYYY-MM-DD) or return an existing date."""
    if val is None:
        return None
    if isinstance(val, date):
        return val
    value = str(val).strip()
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def compute_age_at_date(
    birth_date: date | str | None, reference_date: date | str
) -> int | None:
    """Compute completed years of age at a fixed reference date."""
    birth = parse_date_safe(birth_date)
    reference = parse_date_safe(reference_date)
    if birth is None or reference is None:
        return None
    return (
        reference.year
        - birth.year
        - ((reference.month, reference.day) < (birth.month, birth.day))
    )


def get_age_bracket(age: int | None) -> str:
    """Assign an age to the stable demographic brackets used in exports."""
    if age is None:
        return "Unknown"
    if age < 30:
        return "Under 30"
    if age < 40:
        return "30-39"
    if age < 50:
        return "40-49"
    if age < 60:
        return "50-59"
    if age < 70:
        return "60-69"
    return "70+"


def compute_parliament_demographics(
    conn: duckdb.DuckDBPyConnection, parliament: int
) -> dict[str, Any]:
    """Compute opening-day demographics against a fixed parliament date."""
    meta = PARLIAMENT_METADATA.get(parliament)
    if meta is None:
        supported = ", ".join(str(number) for number in sorted(PARLIAMENT_METADATA))
        raise ValueError(
            f"Unsupported parliament number {parliament}; supported values are: "
            f"{supported}."
        )
    reference_date = meta["opening_date"]

    query = """
    WITH opening_day_services AS (
        SELECT
            s.*,
            ROW_NUMBER() OVER (
                PARTITION BY s.member_id
                ORDER BY COALESCE(s.service_start, CAST(? AS DATE)), s.service_id
            ) AS service_rank
        FROM parliament_service s
        WHERE s.parliament_number = ?
          AND s.is_opening_day_member = TRUE
    )
    SELECT
        m.member_id,
        m.gender,
        m.date_of_birth,
        s.chamber,
        s.party,
        s.party_abbrev,
        s.is_opening_day_member,
        s.is_current_member
    FROM members m
    JOIN opening_day_services s ON m.member_id = s.member_id
    WHERE s.service_rank = 1
    ORDER BY m.member_id
    """
    rows = conn.execute(query, [reference_date, parliament]).fetchall()

    total_parliamentarians = len(rows)
    current_count = sum(1 for row in rows if row[7] is True)
    genders: dict[str, int] = {}
    chambers: dict[str, int] = {}
    parties: dict[str, int] = {}
    ages: list[int] = []
    age_brackets = {
        "Under 30": 0,
        "30-39": 0,
        "40-49": 0,
        "50-59": 0,
        "60-69": 0,
        "70+": 0,
        "Unknown": 0,
    }

    for row in rows:
        gender = row[1] or "Unknown"
        genders[gender] = genders.get(gender, 0) + 1
        chamber = row[3] or "Unknown"
        chambers[chamber] = chambers.get(chamber, 0) + 1
        party = row[5] or row[4] or "Unknown"
        parties[party] = parties.get(party, 0) + 1

        age = compute_age_at_date(row[2], reference_date)
        if age is None:
            age_brackets["Unknown"] += 1
        else:
            ages.append(age)
            age_brackets[get_age_bracket(age)] += 1

    ages.sort()
    age_missing = total_parliamentarians - len(ages)
    return {
        "parliament_number": parliament,
        "reference_opening_date": reference_date,
        "total_parliamentarians": total_parliamentarians,
        "opening_day_parliamentarians": total_parliamentarians,
        "opening_day_current_parliamentarians": current_count,
        "average_age_at_opening": round(sum(ages) / len(ages), 1) if ages else None,
        "median_age_at_opening": _median(ages),
        "min_age": ages[0] if ages else None,
        "max_age": ages[-1] if ages else None,
        "known_age_sample_size": len(ages),
        "missing_age_count": age_missing,
        "age_percentage_denominator": total_parliamentarians,
        "age_brackets": age_brackets,
        "genders": genders,
        "chambers": chambers,
        "parties": parties,
    }


def compute_sector_summary(
    conn: duckdb.DuckDBPyConnection, parliament: int
) -> dict[str, Any]:
    """Return mutually-exclusive person counts and separate attendance counts."""
    all_mps = {
        row[0]
        for row in conn.execute(
            """
            SELECT DISTINCT member_id
            FROM parliament_service
            WHERE parliament_number = ?
              AND is_opening_day_member = TRUE
            """,
            [parliament],
        ).fetchall()
    }
    edu_rows = conn.execute(
        """
        SELECT e.member_id, i.sector
        FROM member_education e
        JOIN institutions i ON e.institution_id = i.institution_id
        JOIN (
            SELECT DISTINCT member_id
            FROM parliament_service
            WHERE parliament_number = ?
              AND is_opening_day_member = TRUE
        ) s ON e.member_id = s.member_id
        WHERE e.level = 'secondary'
        ORDER BY e.member_id, e.education_id
        """,
        [parliament],
    ).fetchall()

    known_sectors = ("Government", "Catholic", "Independent", "Other")
    mp_sectors: dict[str, set[str]] = {}
    attendance_instances = {sector: 0 for sector in known_sectors}
    for member_id, sector in edu_rows:
        sector_name = sector if sector in known_sectors[:3] else "Other"
        mp_sectors.setdefault(member_id, set()).add(sector_name)
        attendance_instances[sector_name] += 1

    unique_counts = {
        "Government": 0,
        "Catholic": 0,
        "Independent": 0,
        "Combined/Multiple": 0,
        "Other": 0,
        "No School Recorded": 0,
    }
    for member_id in all_mps:
        sectors = mp_sectors.get(member_id, set())
        if not sectors:
            unique_counts["No School Recorded"] += 1
        elif len(sectors) > 1:
            unique_counts["Combined/Multiple"] += 1
        else:
            unique_counts[next(iter(sectors))] += 1

    known_count = len(all_mps) - unique_counts["No School Recorded"]
    unique_percentages = {
        sector: unique_counts[sector] / known_count * 100 if known_count else 0.0
        for sector in (
            "Government",
            "Catholic",
            "Independent",
            "Combined/Multiple",
            "Other",
        )
    }
    attendance_count = sum(attendance_instances.values())
    attendance_percentages = {
        sector: count / attendance_count * 100 if attendance_count else 0.0
        for sector, count in attendance_instances.items()
    }
    benchmark_comparison = compute_sector_benchmarks(conn, parliament)

    return {
        "parliament_number": parliament,
        "total_parliamentarians": len(all_mps),
        "parliamentarians_with_known_schools": known_count,
        "parliamentarians_without_known_schools": unique_counts["No School Recorded"],
        "known_school_percentage_denominator": known_count,
        "unique_parliamentarians_by_sector": unique_counts,
        "percentage_of_known_parliamentarians": unique_percentages,
        "total_attendance_instances": attendance_count,
        "attendance_instance_percentage_denominator": attendance_count,
        "attendance_instances_by_sector": attendance_instances,
        "percentage_of_attendance_instances": attendance_percentages,
        "benchmark_comparison": benchmark_comparison,
    }


def compute_sector_benchmarks(
    conn: duckdb.DuckDBPyConnection,
    parliament: int,
    benchmark_year: int = 2025,
) -> dict[str, dict[str, Any]]:
    """Compare parliamentary education sector representation against statistical benchmarks.

    Returns for each benchmarked sector:
    - parliamentary_share (float proportion)
    - student_enrolment_share (float proportion)
    - difference_percentage_points (float percentage points)
    - benchmark_year (int)
    - benchmark_source (str)
    """
    all_mps = {
        row[0]
        for row in conn.execute(
            """
            SELECT DISTINCT member_id
            FROM parliament_service
            WHERE parliament_number = ?
              AND is_opening_day_member = TRUE
            """,
            [parliament],
        ).fetchall()
    }
    edu_rows = conn.execute(
        """
        SELECT e.member_id, i.sector
        FROM member_education e
        JOIN institutions i ON e.institution_id = i.institution_id
        JOIN (
            SELECT DISTINCT member_id
            FROM parliament_service
            WHERE parliament_number = ?
              AND is_opening_day_member = TRUE
        ) s ON e.member_id = s.member_id
        WHERE e.level = 'secondary'
        ORDER BY e.member_id, e.education_id
        """,
        [parliament],
    ).fetchall()

    known_sectors = ("Government", "Catholic", "Independent")
    mp_sectors: dict[str, set[str]] = {}
    for member_id, sector in edu_rows:
        sector_name = sector if sector in known_sectors else "Other"
        mp_sectors.setdefault(member_id, set()).add(sector_name)

    unique_counts: dict[str, int] = {s: 0 for s in known_sectors}
    no_school = 0
    for member_id in all_mps:
        sectors = mp_sectors.get(member_id, set())
        if not sectors:
            no_school += 1
        elif len(sectors) == 1:
            sec = next(iter(sectors))
            if sec in unique_counts:
                unique_counts[sec] += 1

    known_count = len(all_mps) - no_school

    # Check if education_sector_benchmarks table exists and has rows
    try:
        benchmarks_rows = conn.execute(
            """
            SELECT sector, student_enrolment_share, source_title
            FROM education_sector_benchmarks
            WHERE benchmark_year = ?
            ORDER BY sector
            """,
            [benchmark_year],
        ).fetchall()
    except Exception:
        benchmarks_rows = []

    result: dict[str, dict[str, Any]] = {}
    for sector, benchmark_share, source_title in benchmarks_rows:
        parl_count = unique_counts.get(sector, 0)
        parl_share = round(parl_count / known_count, 3) if known_count else 0.0
        bench_share = round(float(benchmark_share), 3)
        diff_pts = round((parl_share - bench_share) * 100, 1)

        result[sector] = {
            "parliamentary_share": parl_share,
            "student_enrolment_share": bench_share,
            "difference_percentage_points": diff_pts,
            "benchmark_year": benchmark_year,
            "benchmark_source": source_title,
        }

    return result


def compute_funding_summary(
    conn: duckdb.DuckDBPyConnection, parliament: int
) -> dict[str, Any]:
    """Summarise 2021 finance with NULL values excluded and counted as missing."""
    rows = conn.execute(
        """
        WITH parliament_schools AS (
            SELECT DISTINCT e.institution_id
            FROM member_education e
            JOIN (
                SELECT DISTINCT member_id
                FROM parliament_service
                WHERE parliament_number = ?
                  AND is_opening_day_member = TRUE
            ) s ON e.member_id = s.member_id
            WHERE e.level = 'secondary'
        )
        SELECT
            ps.institution_id,
            i.sector,
            f.total_gross_income_per_student,
            f.total_net_recurrent_income_per_student
        FROM parliament_schools ps
        JOIN institutions i ON ps.institution_id = i.institution_id
        LEFT JOIN school_finances_2021 f ON ps.institution_id = f.institution_id
        ORDER BY ps.institution_id
        """,
        [parliament],
    ).fetchall()

    sector_gross: dict[str, list[int]] = {}
    sector_net: dict[str, list[int]] = {}
    sector_scope: dict[str, int] = {}
    schools_with_finance_data: set[str] = set()
    for institution_id, sector, gross, net in rows:
        sector_name = (
            sector if sector in ("Government", "Catholic", "Independent") else "Other"
        )
        sector_scope[sector_name] = sector_scope.get(sector_name, 0) + 1
        if gross is not None or net is not None:
            schools_with_finance_data.add(institution_id)
        if gross is not None:
            sector_gross.setdefault(sector_name, []).append(gross)
        if net is not None:
            sector_net.setdefault(sector_name, []).append(net)

    metrics_by_sector: dict[str, dict[str, Any]] = {}
    for sector in ("Government", "Catholic", "Independent", "Other"):
        gross = sector_gross.get(sector, [])
        net = sector_net.get(sector, [])
        scope_n = sector_scope.get(sector, 0)
        gross_summary = _metric_summary(gross, scope_n - len(gross))
        net_summary = _metric_summary(net, scope_n - len(net))
        metrics_by_sector[sector] = {
            "gross_income_per_student_avg": gross_summary["mean"],
            "gross_income_per_student_median": gross_summary["median"],
            "gross_income_sample_size": gross_summary["n"],
            "gross_income_missing_count": gross_summary["missing"],
            "gross_income": gross_summary,
            "net_recurrent_income_per_student_avg": net_summary["mean"],
            "net_recurrent_income_per_student_median": net_summary["median"],
            "net_recurrent_income_sample_size": net_summary["n"],
            "net_recurrent_income_missing_count": net_summary["missing"],
            "net_recurrent_income": net_summary,
            "schools_in_scope": scope_n,
        }

    total_gross = [value for values in sector_gross.values() for value in values]
    total_net = [value for values in sector_net.values() for value in values]
    total_scope = sum(sector_scope.values())
    overall_gross = _metric_summary(total_gross, total_scope - len(total_gross))
    overall_net = _metric_summary(total_net, total_scope - len(total_net))
    return {
        "parliament_number": parliament,
        "reporting_year": 2021,
        "total_schools_in_scope": total_scope,
        "total_schools_with_finance_data": len(schools_with_finance_data),
        "overall_gross_income_sample_size": overall_gross["n"],
        "overall_gross_income_missing_count": overall_gross["missing"],
        "overall_gross_income_avg": overall_gross["mean"],
        "overall_gross_income_median": overall_gross["median"],
        "overall_gross_income": overall_gross,
        "overall_net_recurrent_income_sample_size": overall_net["n"],
        "overall_net_recurrent_income_missing_count": overall_net["missing"],
        "overall_net_recurrent_avg": overall_net["mean"],
        "overall_net_recurrent_income_median": overall_net["median"],
        "overall_net_recurrent_income": overall_net,
        "by_sector": metrics_by_sector,
    }


def _median(values: list[int]) -> float | int | None:
    """Return a deterministic median for a non-null numeric sample."""
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return round((ordered[middle - 1] + ordered[middle]) / 2, 1)


def _metric_summary(values: list[int], missing: int) -> dict[str, Any]:
    """Build a NULL-aware summary with explicit valid and missing counts."""
    return {
        "mean": round(sum(values) / len(values), 0) if values else None,
        "median": _median(values),
        "n": len(values),
        "missing": missing,
    }


def _analysis_metadata(parliaments: list[int]) -> dict[str, Any]:
    """Return stable metadata shared by all static analysis exports."""
    return {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "parliament_numbers": parliaments,
        "reference_opening_dates": {
            str(parliament): PARLIAMENT_METADATA[parliament]["opening_date"]
            for parliament in parliaments
        },
        "finance_reporting_year": 2021,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write stable, newline-terminated JSON for reviewable Git diffs."""
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def export_analysis_report(
    conn: duckdb.DuckDBPyConnection,
    output_dir: Path | str | None = None,
    parliaments: list[int] | None = None,
) -> Path:
    """Export compatibility and static-chart analysis JSON files."""
    out_dir = Path(output_dir or PROCESSED_DIR).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    target_parliaments = list(parliaments or [46, 47, 48])
    metadata = _analysis_metadata(target_parliaments)
    report: dict[str, dict[str, Any]] = {}
    for parliament in target_parliaments:
        report[str(parliament)] = {
            "demographics": compute_parliament_demographics(conn, parliament),
            "sectors": compute_sector_summary(conn, parliament),
            "funding_2021": compute_funding_summary(conn, parliament),
        }

    _write_json(
        out_dir / "analysis_metrics.json",
        {"metadata": metadata, "parliaments": report},
    )
    analysis_dir = out_dir / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    _write_json(analysis_dir / "metadata.json", metadata)
    _write_json(
        analysis_dir / "demographics.json",
        {
            "metadata": metadata,
            "parliaments": {
                key: value["demographics"] for key, value in report.items()
            },
        },
    )
    _write_json(
        analysis_dir / "education_sectors.json",
        {
            "metadata": metadata,
            "parliaments": {key: value["sectors"] for key, value in report.items()},
        },
    )
    _write_json(
        analysis_dir / "school_finance.json",
        {
            "metadata": metadata,
            "parliaments": {
                key: value["funding_2021"] for key, value in report.items()
            },
        },
    )
    comparisons = {
        key: {
            "parliament_number": int(key),
            "reference_opening_date": value["demographics"]["reference_opening_date"],
            "total_parliamentarians": value["demographics"]["total_parliamentarians"],
            "known_age_n": value["demographics"]["known_age_sample_size"],
            "missing_age_n": value["demographics"]["missing_age_count"],
            "known_school_n": value["sectors"]["parliamentarians_with_known_schools"],
            "missing_school_n": value["sectors"][
                "parliamentarians_without_known_schools"
            ],
            "finance": {
                "reporting_year": value["funding_2021"]["reporting_year"],
                "schools_in_scope": value["funding_2021"]["total_schools_in_scope"],
                "gross_n": value["funding_2021"]["overall_gross_income"]["n"],
                "gross_missing_n": value["funding_2021"]["overall_gross_income"][
                    "missing"
                ],
                "net_n": value["funding_2021"]["overall_net_recurrent_income"]["n"],
                "net_missing_n": value["funding_2021"]["overall_net_recurrent_income"][
                    "missing"
                ],
            },
        }
        for key, value in report.items()
    }
    _write_json(
        analysis_dir / "parliament_comparison.json",
        {"metadata": metadata, "parliaments": comparisons},
    )
    logger.info(
        "Wrote deterministic analysis report to %s",
        out_dir / "analysis_metrics.json",
    )
    return out_dir / "analysis_metrics.json"
