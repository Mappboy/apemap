"""Deterministic statistical calculations and demographic aggregations.

Provides reproducible demographic and education summaries evaluated against
fixed parliament opening dates rather than dynamic runtime clocks.
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


def parse_date_safe(val: date | str | None) -> date | None:
    """Parse a date from a string (YYYY-MM-DD) or return existing date object."""
    if val is None:
        return None
    if isinstance(val, date):
        return val
    s = str(val).strip()
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def compute_age_at_date(
    birth_date: date | str | None, reference_date: date | str
) -> int | None:
    """Compute exact age in years at a fixed reference date deterministically.

    Args:
        birth_date: Date of birth (string YYYY-MM-DD or date object).
        reference_date: Benchmark date (e.g. parliament opening day).

    Returns:
        Age in completed years, or None if birth_date cannot be parsed.
    """
    b = parse_date_safe(birth_date)
    r = parse_date_safe(reference_date)
    if b is None or r is None:
        return None
    return r.year - b.year - ((r.month, r.day) < (b.month, b.day))


def get_age_bracket(age: int | None) -> str:
    """Assign age to standard demographic bracket."""
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
    """Compute opening-day demographic breakdowns at a fixed benchmark date.

    Args:
        conn: DuckDB database connection.
        parliament: Parliament number (e.g. 46, 47, 48).

    Returns:
        Dictionary of deterministic metrics for one person per opening-day member.

    Raises:
        ValueError: If ``parliament`` is not present in ``PARLIAMENT_METADATA``.
    """
    meta = PARLIAMENT_METADATA.get(parliament)
    if meta is None:
        supported = ", ".join(str(number) for number in sorted(PARLIAMENT_METADATA))
        raise ValueError(
            f"Unsupported parliament number {parliament}; "
            f"supported values are: {supported}."
        )
    ref_date_str = meta["opening_date"]

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
    rows = conn.execute(query, [ref_date_str, parliament]).fetchall()

    total_mps = len(rows)
    opening_day_count = total_mps
    opening_day_current_count = sum(1 for r in rows if r[7] is True)

    genders: dict[str, int] = {}
    chambers: dict[str, int] = {}
    parties: dict[str, int] = {}
    ages: list[int] = []
    age_brackets: dict[str, int] = {
        "Under 30": 0,
        "30-39": 0,
        "40-49": 0,
        "50-59": 0,
        "60-69": 0,
        "70+": 0,
        "Unknown": 0,
    }

    for r in rows:
        gen = r[1] or "Unknown"
        genders[gen] = genders.get(gen, 0) + 1

        cham = r[3] or "Unknown"
        chambers[cham] = chambers.get(cham, 0) + 1

        pabbrev = r[5] or r[4] or "Unknown"
        parties[pabbrev] = parties.get(pabbrev, 0) + 1

        dob = r[2]
        age = compute_age_at_date(dob, ref_date_str)
        if age is not None:
            ages.append(age)
            bracket = get_age_bracket(age)
            age_brackets[bracket] += 1
        else:
            age_brackets["Unknown"] += 1

    ages.sort()
    avg_age = round(sum(ages) / len(ages), 1) if ages else None
    median_age = (
        ages[len(ages) // 2]
        if len(ages) % 2 == 1
        else round((ages[len(ages) // 2 - 1] + ages[len(ages) // 2]) / 2, 1)
        if ages
        else None
    )

    return {
        "parliament_number": parliament,
        "reference_opening_date": ref_date_str,
        "total_parliamentarians": total_mps,
        "opening_day_parliamentarians": opening_day_count,
        "opening_day_current_parliamentarians": opening_day_current_count,
        "average_age_at_opening": avg_age,
        "median_age_at_opening": median_age,
        "min_age": ages[0] if ages else None,
        "max_age": ages[-1] if ages else None,
        "known_age_sample_size": len(ages),
        "age_brackets": age_brackets,
        "genders": genders,
        "chambers": chambers,
        "parties": parties,
    }


def compute_sector_summary(
    conn: duckdb.DuckDBPyConnection, parliament: int
) -> dict[str, Any]:
    """Compute secondary school sector breakdown distinguishing persons from instances.

    Corrects attendance vs person counting by classifying multi-school attendees into
    a distinct 'Combined/Multiple' category so unique parliamentarian percentages sum to 100%.

    Args:
        conn: DuckDB database connection.
        parliament: Parliament number.

    Returns:
        Dictionary of sector distributions with counts, instances, and percentages.
    """
    # 1. Total distinct MPs in this parliament
    mp_query = """
    SELECT DISTINCT member_id FROM parliament_service WHERE parliament_number = ?
    """
    all_mps = {row[0] for row in conn.execute(mp_query, [parliament]).fetchall()}
    total_mps = len(all_mps)

    # 2. Get all secondary school attendance records for these MPs
    edu_query = """
    SELECT
        e.member_id,
        i.sector,
        e.confidence
    FROM member_education e
    JOIN institutions i ON e.institution_id = i.institution_id
    JOIN parliament_service s ON e.member_id = s.member_id
    WHERE s.parliament_number = ? AND e.level = 'secondary'
    """
    edu_rows = conn.execute(edu_query, [parliament]).fetchall()

    # MP -> set of sectors attended
    mp_sectors: dict[str, set[str]] = {}
    attendance_instances_by_sector: dict[str, int] = {
        "Government": 0,
        "Catholic": 0,
        "Independent": 0,
        "Other": 0,
    }

    for mid, sec, _conf in edu_rows:
        sector_name = (
            sec if sec in ("Government", "Catholic", "Independent") else "Other"
        )
        attendance_instances_by_sector[sector_name] = (
            attendance_instances_by_sector.get(sector_name, 0) + 1
        )
        if mid not in mp_sectors:
            mp_sectors[mid] = set()
        mp_sectors[mid].add(sector_name)

    unique_mps_by_sector: dict[str, int] = {
        "Government": 0,
        "Catholic": 0,
        "Independent": 0,
        "Combined/Multiple": 0,
        "Other": 0,
        "No School Recorded": 0,
    }

    for mid in all_mps:
        secs = mp_sectors.get(mid)
        if not secs:
            unique_mps_by_sector["No School Recorded"] += 1
        elif len(secs) > 1:
            unique_mps_by_sector["Combined/Multiple"] += 1
        else:
            single_sec = next(iter(secs))
            unique_mps_by_sector[single_sec] += 1

    mps_with_known_schools = total_mps - unique_mps_by_sector["No School Recorded"]

    percentages_of_known: dict[str, float] = {}
    if mps_with_known_schools > 0:
        for cat in (
            "Government",
            "Catholic",
            "Independent",
            "Combined/Multiple",
            "Other",
        ):
            count = unique_mps_by_sector[cat]
            percentages_of_known[cat] = round((count / mps_with_known_schools) * 100, 1)

    return {
        "parliament_number": parliament,
        "total_parliamentarians": total_mps,
        "parliamentarians_with_known_schools": mps_with_known_schools,
        "unique_parliamentarians_by_sector": unique_mps_by_sector,
        "percentage_of_known_parliamentarians": percentages_of_known,
        "total_attendance_instances": sum(attendance_instances_by_sector.values()),
        "attendance_instances_by_sector": attendance_instances_by_sector,
    }


def compute_funding_summary(
    conn: duckdb.DuckDBPyConnection, parliament: int
) -> dict[str, Any]:
    """Compute financial statistics over valid reporting schools reporting sample sizes (N).

    Excludes NULL/missing values rather than imputing zero.

    Args:
        conn: DuckDB database connection.
        parliament: Parliament number.

    Returns:
        Dictionary of financial metrics with explicit sample size N and averages by sector.
    """
    query = """
    WITH parliament_schools AS (
        SELECT DISTINCT e.institution_id
        FROM member_education e
        JOIN parliament_service s ON e.member_id = s.member_id
        WHERE s.parliament_number = ? AND e.level = 'secondary'
    )
    SELECT
        ps.institution_id,
        i.sector,
        f.total_gross_income_per_student,
        f.total_net_recurrent_income_per_student
    FROM school_finances_2021 f
    JOIN parliament_schools ps ON f.institution_id = ps.institution_id
    JOIN institutions i ON ps.institution_id = i.institution_id
    ORDER BY ps.institution_id
    """
    rows = conn.execute(query, [parliament]).fetchall()

    sector_gross: dict[str, list[int]] = {}
    sector_net: dict[str, list[int]] = {}
    schools_with_finance_data: set[str] = set()

    for institution_id, sec, gross, net in rows:
        sec_name = sec if sec in ("Government", "Catholic", "Independent") else "Other"
        if gross is not None or net is not None:
            schools_with_finance_data.add(institution_id)
        if gross is not None:
            sector_gross.setdefault(sec_name, []).append(gross)
        if net is not None:
            sector_net.setdefault(sec_name, []).append(net)

    metrics_by_sector: dict[str, dict[str, Any]] = {}
    for sec_name in ("Government", "Catholic", "Independent", "Other"):
        g_vals = sector_gross.get(sec_name, [])
        n_vals = sector_net.get(sec_name, [])

        metrics_by_sector[sec_name] = {
            "gross_income_per_student_avg": round(sum(g_vals) / len(g_vals), 0)
            if g_vals
            else None,
            "gross_income_sample_size": len(g_vals),
            "net_recurrent_income_per_student_avg": round(sum(n_vals) / len(n_vals), 0)
            if n_vals
            else None,
            "net_recurrent_income_sample_size": len(n_vals),
        }

    total_gross_vals = [g for vals in sector_gross.values() for g in vals]
    total_net_vals = [n for vals in sector_net.values() for n in vals]

    return {
        "parliament_number": parliament,
        "reporting_year": 2021,
        "total_schools_with_finance_data": len(schools_with_finance_data),
        "overall_gross_income_sample_size": len(total_gross_vals),
        "overall_net_recurrent_income_sample_size": len(total_net_vals),
        "overall_gross_income_avg": round(
            sum(total_gross_vals) / len(total_gross_vals), 0
        )
        if total_gross_vals
        else None,
        "overall_net_recurrent_avg": round(sum(total_net_vals) / len(total_net_vals), 0)
        if total_net_vals
        else None,
        "by_sector": metrics_by_sector,
    }


def export_analysis_report(
    conn: duckdb.DuckDBPyConnection,
    output_dir: Path | str | None = None,
    parliaments: list[int] | None = None,
) -> Path:
    """Compute and export comprehensive statistical report to JSON.

    Args:
        conn: DuckDB database connection.
        output_dir: Destination directory.
        parliaments: List of parliaments to analyze (default: [46, 47, 48]).

    Returns:
        Path to generated analysis_metrics.json file.
    """
    out_dir = Path(output_dir or PROCESSED_DIR).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    target_parls = parliaments or [46, 47, 48]

    report: dict[str, Any] = {
        "parliaments": {},
    }

    for p in target_parls:
        report["parliaments"][str(p)] = {
            "demographics": compute_parliament_demographics(conn, p),
            "sectors": compute_sector_summary(conn, p),
            "funding_2021": compute_funding_summary(conn, p),
        }

    json_path = out_dir / "analysis_metrics.json"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    logger.info("Wrote deterministic analysis report to %s", json_path)
    return json_path
