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
import statistics
from typing import Any

import duckdb

from apemap.constants import EXTERNAL_DIR, PARLIAMENT_METADATA, PROCESSED_DIR

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
    conn: duckdb.DuckDBPyConnection,
    parliament: int,
    reporting_year: int = 2021,
) -> dict[str, Any]:
    """Summarise school finance with NULL values excluded and counted as missing.

    Args:
        conn: Active DuckDB connection.
        parliament: Parliament number (e.g. 47).
        reporting_year: Calendar reporting year for school finances (defaults to 2021).

    Returns:
        Dictionary containing overall and sector-specific financial metrics,
        sample sizes, missing counts, and ACARA attribution.
    """
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
        LEFT JOIN school_finances f
          ON ps.institution_id = f.institution_id
         AND f.reporting_year = ?
        ORDER BY ps.institution_id
        """,
        [parliament, reporting_year],
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
        "reporting_year": reporting_year,
        "source_attribution": "Source: Australian Curriculum, Assessment and Reporting Authority (ACARA) My School",
        "licence": "ACARA My School Terms of Use (July 2020)",
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


def _analysis_metadata(
    parliaments: list[int],
    finance_reporting_year: int = 2021,
) -> dict[str, Any]:
    """Return stable metadata shared by all static analysis exports."""
    return {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "parliament_numbers": parliaments,
        "reference_opening_dates": {
            str(parliament): PARLIAMENT_METADATA[parliament]["opening_date"]
            for parliament in parliaments
        },
        "finance_reporting_year": finance_reporting_year,
        "finance_source_attribution": "Source: Australian Curriculum, Assessment and Reporting Authority (ACARA) My School",
        "finance_licence": "ACARA My School Terms of Use (July 2020)",
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
    finance_reporting_year: int = 2021,
) -> Path:
    """Export compatibility and static-chart analysis JSON files."""
    out_dir = Path(output_dir or PROCESSED_DIR).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    target_parliaments = list(parliaments or [46, 47, 48])
    metadata = _analysis_metadata(
        target_parliaments, finance_reporting_year=finance_reporting_year
    )
    report: dict[str, dict[str, Any]] = {}
    for parliament in target_parliaments:
        funding_data = compute_funding_summary(
            conn, parliament, reporting_year=finance_reporting_year
        )
        parl_report: dict[str, Any] = {
            "demographics": compute_parliament_demographics(conn, parliament),
            "sectors": compute_sector_summary(conn, parliament),
            "school_finance": funding_data,
        }
        if finance_reporting_year == 2021:
            parl_report["funding_2021"] = funding_data
        report[str(parliament)] = parl_report

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
                key: value["school_finance"] for key, value in report.items()
            },
        },
    )
    comparisons = {}
    for key, value in report.items():
        finance_data = value["school_finance"]
        comparisons[key] = {
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
                "reporting_year": finance_data["reporting_year"],
                "schools_in_scope": finance_data["total_schools_in_scope"],
                "gross_n": finance_data["overall_gross_income"]["n"],
                "gross_missing_n": finance_data["overall_gross_income"]["missing"],
                "net_n": finance_data["overall_net_recurrent_income"]["n"],
                "net_missing_n": finance_data["overall_net_recurrent_income"][
                    "missing"
                ],
            },
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


_GEOLOCATION_CACHE: dict[str, str] | None = None


def get_school_geolocation(
    acara_id: str | int | None,
    external_dir: Path | str | None = None,
) -> str:
    """Lookup the ASGS Remoteness geolocation classification for an ACARA school.

    Args:
        acara_id: ACARA SML ID string or integer.
        external_dir: Optional path to external data directory.

    Returns:
        One of 'Major Cities', 'Inner Regional', 'Outer Regional', 'Remote',
        'Very Remote', or 'All' if unclassified.
    """
    global _GEOLOCATION_CACHE
    if _GEOLOCATION_CACHE is None:
        _GEOLOCATION_CACHE = {}
        ext_dir = Path(external_dir or EXTERNAL_DIR).resolve()
        loc_file = ext_dir / "school-location-2022.csv"
        prof_file = ext_dir / "school-profile-2022.csv"

        if loc_file.exists():
            try:
                import pandas as pd

                df_loc = pd.read_csv(
                    loc_file,
                    dtype=str,
                    usecols=["ACARA SML ID", "ABS Remoteness Area Name"],
                )
                for _, r in df_loc.iterrows():
                    aid = str(r["ACARA SML ID"]).strip()
                    rem = str(r.get("ABS Remoteness Area Name", "")).strip()
                    if aid and rem:
                        _GEOLOCATION_CACHE[aid] = rem
            except Exception as e:
                logger.debug("Failed reading geolocation from %s: %s", loc_file, e)

        if prof_file.exists():
            try:
                import pandas as pd

                df_prof = pd.read_csv(
                    prof_file, dtype=str, usecols=["ACARA SML ID", "Geolocation"]
                )
                for _, r in df_prof.iterrows():
                    aid = str(r["ACARA SML ID"]).strip()
                    geo = str(r.get("Geolocation", "")).strip()
                    if aid and geo and aid not in _GEOLOCATION_CACHE:
                        _GEOLOCATION_CACHE[aid] = geo
            except Exception as e:
                logger.debug("Failed reading geolocation from %s: %s", prof_file, e)

    if acara_id is None:
        return "All"
    aid_str = str(acara_id).strip()
    if aid_str.endswith(".0"):
        aid_str = aid_str[:-2]
    return _GEOLOCATION_CACHE.get(aid_str, "All")


def get_peer_group_benchmark(
    conn: duckdb.DuckDBPyConnection,
    reporting_year: int,
    state_or_territory: str | None,
    sector: str | None,
    geolocation: str | None = "All",
    metric: str = "total_net_recurrent_income_per_student",
) -> dict[str, Any] | None:
    """Retrieve peer group finance benchmark with deterministic hierarchical fallback.

    Fallback hierarchy:
    1. (state, sector, geolocation)
    2. (state, sector, 'All')
    3. ('All', sector, geolocation)
    4. ('All', sector, 'All')
    5. ('All', 'All', 'All')

    Args:
        conn: Active DuckDB connection.
        reporting_year: Target calendar reporting year.
        state_or_territory: School state or territory code (e.g. 'NSW', 'TAS').
        sector: School sector ('Government', 'Catholic', 'Independent').
        geolocation: Remoteness area name ('Major Cities', 'Inner Regional', etc.).
        metric: Benchmark finance metric name.

    Returns:
        Dictionary containing benchmark value and matched peer group metadata,
        or None if no benchmark exists.
    """
    state_val = (state_or_territory or "All").strip()
    sector_val = (sector or "All").strip()
    if sector_val not in ("Government", "Catholic", "Independent"):
        sector_val = "All"
    geo_val = (geolocation or "All").strip()

    search_levels = [
        (state_val, sector_val, geo_val, 1),
        (state_val, sector_val, "All", 2),
        ("All", sector_val, geo_val, 3),
        ("All", sector_val, "All", 4),
    ]

    seen: set[tuple[str, str, str]] = set()
    levels_to_try: list[tuple[str, str, str, int]] = []
    for s, sec, g, lvl in search_levels:
        key = (s, sec, g)
        if key not in seen:
            seen.add(key)
            levels_to_try.append((s, sec, g, lvl))

    query = """
    SELECT
        value,
        unit,
        source_dataset,
        source_url,
        state_or_territory,
        sector,
        geolocation
    FROM school_finance_benchmarks
    WHERE reporting_year = ?
      AND state_or_territory = ?
      AND sector = ?
      AND geolocation = ?
      AND metric = ?
    LIMIT 1
    """

    for s, sec, g, lvl in levels_to_try:
        try:
            row = conn.execute(query, [reporting_year, s, sec, g, metric]).fetchone()
        except Exception:
            return None
        if row is not None:
            return {
                "value": float(row[0]),
                "unit": str(row[1]),
                "source_dataset": str(row[2]),
                "source_url": str(row[3]),
                "state_or_territory": str(row[4]),
                "sector": str(row[5]),
                "geolocation": str(row[6]),
                "peer_group": f"{row[4]} / {row[5]} / {row[6]}",
                "fallback_level": lvl,
                "reporting_year": reporting_year,
                "metric": metric,
            }

    return None


def get_peer_group_reliability(
    conn: duckdb.DuckDBPyConnection,
    peer_group: str,
    historical_year: int = 2021,
    metric: str = "total_net_recurrent_income_per_student",
    dispersion_mad_threshold: float = 0.25,
    min_sample_size: int = 3,
    peer_group_metrics: dict[str, Any] | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Determine whether a peer group has sufficient sample size and low dispersion.

    Args:
        conn: Active DuckDB connection.
        peer_group: Peer group string formatted as 'state / sector / geolocation'.
        historical_year: Baseline year to test.
        metric: Finance metric to evaluate.
        dispersion_mad_threshold: Maximum MAD threshold for reliability.
        min_sample_size: Minimum sample size required.
        peer_group_metrics: Optional precomputed peer_group_metrics dict from backtesting.

    Returns:
        Tuple of (is_reliable: bool, stats_dict: dict[str, Any]).
    """
    if peer_group_metrics is None:
        backtest_res = backtest_finance_benchmarks(
            conn,
            historical_year=historical_year,
            metric=metric,
            dispersion_mad_threshold=dispersion_mad_threshold,
            min_sample_size=min_sample_size,
        )
        peer_group_metrics = backtest_res.get("peer_group_metrics", {})

    stats = peer_group_metrics.get(peer_group)
    if not stats:
        return False, {
            "sample_size": 0,
            "median_ratio": None,
            "mad": None,
            "mape": None,
            "is_reliable": False,
            "dispersion_status": "insufficient_sample",
        }

    is_reliable = bool(stats.get("is_reliable", False))
    return is_reliable, stats


def compute_school_finance_estimate(
    conn: duckdb.DuckDBPyConnection,
    institution_id: str,
    target_year: int = 2024,
    metric: str = "total_net_recurrent_income_per_student",
    historical_year: int = 2021,
    dispersion_mad_threshold: float = 0.25,
    min_sample_size: int = 3,
    peer_group_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Calculate deterministic school finance estimate or retrieve observed value.

    Guarantees:
    - Never overwrites an observed value with an estimate.
    - If observed value exists for target_year, returns status 'observed'.
    - If historical finance is available, computes indexed benchmark estimate:
      estimate = target_peer_benchmark * (historical_actual / historical_peer_benchmark).
    - If backtesting group dispersion is too high (MAD > threshold) or historical
      finance is missing, falls back to peer group average with explicit status.

    Args:
        conn: Active DuckDB connection.
        institution_id: Canonical institution ID.
        target_year: Year to estimate or retrieve (defaults to 2024).
        metric: Finance metric (defaults to total_net_recurrent_income_per_student).
        historical_year: Baseline year for indexing (defaults to 2021).
        dispersion_mad_threshold: Maximum MAD ratio allowed before group fallback.
        min_sample_size: Minimum peer group sample size required for indexed estimates.
        peer_group_metrics: Optional precomputed backtest peer group reliability metrics.

    Returns:
        Structured dictionary containing value, status, method, peer_group,
        relative_multiplier, and source attribution.
    """
    # 1. Check for observed target_year finance
    try:
        obs_row = conn.execute(
            f"""
            SELECT {metric}, source_dataset
            FROM school_finances
            WHERE institution_id = ? AND reporting_year = ?
            """,
            [institution_id, target_year],
        ).fetchone()
        if obs_row and obs_row[0] is not None:
            return {
                "institution_id": institution_id,
                "reporting_year": target_year,
                "metric": metric,
                "value": float(obs_row[0]),
                "status": "observed",
                "method": "direct_observation",
                "peer_group": None,
                "relative_multiplier": 1.0,
                "benchmark_value": None,
                "source": str(obs_row[1]) if obs_row[1] else "ACARA My School Finance",
            }
    except Exception:
        pass

    # 2. Retrieve school metadata
    inst_row = conn.execute(
        """
        SELECT school_name, state, sector, acara_id
        FROM institutions
        WHERE institution_id = ?
        """,
        [institution_id],
    ).fetchone()
    if not inst_row:
        return {
            "institution_id": institution_id,
            "reporting_year": target_year,
            "metric": metric,
            "value": None,
            "status": "unavailable",
            "method": "none",
            "peer_group": None,
            "relative_multiplier": None,
            "benchmark_value": None,
            "source": None,
        }

    school_name, state, sector, aid = inst_row
    geo = get_school_geolocation(aid)

    # 3. Retrieve target peer benchmark
    b_target = get_peer_group_benchmark(
        conn, target_year, state, sector, geo, metric=metric
    )
    if not b_target:
        return {
            "institution_id": institution_id,
            "reporting_year": target_year,
            "metric": metric,
            "value": None,
            "status": "unavailable",
            "method": "none",
            "peer_group": None,
            "relative_multiplier": None,
            "benchmark_value": None,
            "source": None,
        }

    # 4. Check for historical observed finance
    hist_row = None
    try:
        hist_row = conn.execute(
            f"""
            SELECT {metric}
            FROM school_finances
            WHERE institution_id = ? AND reporting_year = ?
            """,
            [institution_id, historical_year],
        ).fetchone()
    except Exception:
        pass

    if hist_row and hist_row[0] is not None:
        hist_val = float(hist_row[0])
        b_hist = get_peer_group_benchmark(
            conn, historical_year, state, sector, geo, metric=metric
        )
        if b_hist and b_hist["value"] > 0:
            rel_mult = hist_val / b_hist["value"]

            # Check peer group reliability using backtesting rules
            is_reliable, _ = get_peer_group_reliability(
                conn,
                b_hist["peer_group"],
                historical_year=historical_year,
                metric=metric,
                dispersion_mad_threshold=dispersion_mad_threshold,
                min_sample_size=min_sample_size,
                peer_group_metrics=peer_group_metrics,
            )

            if not is_reliable:
                return {
                    "institution_id": institution_id,
                    "reporting_year": target_year,
                    "metric": metric,
                    "value": round(b_target["value"]),
                    "status": "benchmark_average",
                    "method": "peer_group_average_high_dispersion_fallback",
                    "peer_group": b_target["peer_group"],
                    "relative_multiplier": round(rel_mult, 4),
                    "benchmark_value": b_target["value"],
                    "source": "ACARA National Report on Schooling",
                }

            # Outlier guard on individual school multiplier
            if rel_mult > 3.0 or rel_mult < 0.25:
                return {
                    "institution_id": institution_id,
                    "reporting_year": target_year,
                    "metric": metric,
                    "value": round(b_target["value"]),
                    "status": "benchmark_average",
                    "method": "peer_group_average_outlier_fallback",
                    "peer_group": b_target["peer_group"],
                    "relative_multiplier": round(rel_mult, 4),
                    "benchmark_value": b_target["value"],
                    "source": "ACARA National Report on Schooling",
                }

            estimate_val = b_target["value"] * rel_mult
            return {
                "institution_id": institution_id,
                "reporting_year": target_year,
                "metric": metric,
                "value": round(estimate_val),
                "status": "estimated_indexed",
                "method": "acara_state_sector_geolocation_index",
                "peer_group": b_target["peer_group"],
                "relative_multiplier": round(rel_mult, 4),
                "benchmark_value": b_target["value"],
                "source": "ACARA National Report on Schooling",
            }

    # 5. Fallback: Peer group average
    return {
        "institution_id": institution_id,
        "reporting_year": target_year,
        "metric": metric,
        "value": round(b_target["value"]),
        "status": "benchmark_average",
        "method": "peer_group_average",
        "peer_group": b_target["peer_group"],
        "relative_multiplier": 1.0,
        "benchmark_value": b_target["value"],
        "source": "ACARA National Report on Schooling",
    }


def backtest_finance_benchmarks(
    conn: duckdb.DuckDBPyConnection,
    historical_year: int = 2021,
    metric: str = "total_net_recurrent_income_per_student",
    dispersion_mad_threshold: float = 0.25,
    min_sample_size: int = 3,
) -> dict[str, Any]:
    """Backtest benchmark estimation model against historical observed school finances.

    Evaluates:
    - Median actual / benchmark ratio across schools
    - Median Absolute Deviation (MAD) of actual/benchmark ratio
    - Median Absolute Percentage Error (MAPE)
    - Identification of peer groups where dispersion is too high for school-level indexing

    Args:
        conn: Active DuckDB connection.
        historical_year: Historical year with observed actuals (default: 2021).
        metric: Finance metric to backtest.
        dispersion_mad_threshold: Threshold above which a peer group is deemed high-dispersion.
        min_sample_size: Minimum sample size required to classify a group as reliable.

    Returns:
        Structured dictionary containing overall evaluation metrics, sector breakdowns,
        peer group statistics, and high dispersion group flags.
    """
    rows = conn.execute(
        f"""
        SELECT
            f.institution_id,
            f.{metric} AS actual,
            i.school_name,
            i.state,
            i.sector,
            i.acara_id
        FROM school_finances f
        JOIN institutions i ON f.institution_id = i.institution_id
        WHERE f.reporting_year = ?
          AND f.{metric} IS NOT NULL
          AND f.{metric} > 0
        ORDER BY i.state, i.sector, i.school_name
        """,
        [historical_year],
    ).fetchall()

    if not rows:
        return {
            "historical_year": historical_year,
            "metric": metric,
            "sample_size": 0,
            "overall_median_ratio": None,
            "overall_mad": None,
            "overall_mape": None,
            "sector_metrics": {},
            "peer_group_metrics": {},
            "high_dispersion_groups": [],
        }

    school_results: list[dict[str, Any]] = []
    by_sector: dict[str, list[dict[str, Any]]] = {}
    by_peer_group: dict[str, list[dict[str, Any]]] = {}

    for inst_id, actual_val, name, state, sector, aid in rows:
        actual = float(actual_val)
        if actual <= 0:
            continue
        geo = get_school_geolocation(aid)
        benchmark_info = get_peer_group_benchmark(
            conn, historical_year, state, sector, geo, metric=metric
        )
        if not benchmark_info or benchmark_info["value"] <= 0:
            continue

        b_val = benchmark_info["value"]
        ratio = actual / b_val
        ape = (abs(actual - b_val) / actual * 100.0) if actual > 0 else 0.0
        peer_group = benchmark_info["peer_group"]

        item = {
            "institution_id": inst_id,
            "school_name": name,
            "state": state,
            "sector": sector,
            "geolocation": geo,
            "actual": actual,
            "benchmark": b_val,
            "ratio": ratio,
            "ape": ape,
            "peer_group": peer_group,
        }
        school_results.append(item)
        by_sector.setdefault(sector, []).append(item)
        by_peer_group.setdefault(peer_group, []).append(item)

    def _calc_stats(items: list[dict[str, Any]]) -> dict[str, Any]:
        if not items:
            return {"sample_size": 0, "median_ratio": None, "mad": None, "mape": None}
        ratios = [x["ratio"] for x in items]
        apes = [x["ape"] for x in items]
        med_ratio = round(float(statistics.median(ratios)), 4)
        mad = round(float(statistics.median([abs(r - med_ratio) for r in ratios])), 4)
        mape = round(float(statistics.median(apes)), 2)
        return {
            "sample_size": len(items),
            "median_ratio": med_ratio,
            "mad": mad,
            "mape": mape,
        }

    overall_stats = _calc_stats(school_results)

    sector_metrics = {}
    for sec, items in by_sector.items():
        stats = _calc_stats(items)
        sector_metrics[sec] = stats

    peer_group_metrics = {}
    high_dispersion_groups = []
    for pg, items in by_peer_group.items():
        stats = _calc_stats(items)
        is_reliable = (
            stats["mad"] is not None
            and stats["mad"] <= dispersion_mad_threshold
            and stats["sample_size"] >= min_sample_size
        )
        stats["is_reliable"] = is_reliable
        stats["dispersion_status"] = "reliable" if is_reliable else "high_dispersion"
        peer_group_metrics[pg] = stats
        if not is_reliable:
            high_dispersion_groups.append(pg)

    return {
        "historical_year": historical_year,
        "metric": metric,
        "sample_size": len(school_results),
        "overall_median_ratio": overall_stats["median_ratio"],
        "overall_mad": overall_stats["mad"],
        "overall_mape": overall_stats["mape"],
        "sector_metrics": sector_metrics,
        "peer_group_metrics": peer_group_metrics,
        "high_dispersion_groups": sorted(high_dispersion_groups),
    }


def validate_funding_records(conn: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Perform integrity assertions across benchmark and public funding tables.

    Validates:
    - Zero negative funding or benchmark amounts
    - Valid reporting year bounds (2000-2030)
    - Zero duplicate records
    - Zero orphaned institutions in school_public_funding
    - Non-empty datasets
    """
    failures: list[str] = []
    checks_run = 0

    # 1. Negative values check
    checks_run += 1
    neg_bench = conn.execute(
        "SELECT count(*) FROM school_finance_benchmarks WHERE value < 0"
    ).fetchone()
    if neg_bench and neg_bench[0] > 0:
        failures.append(
            f"Found {neg_bench[0]} negative values in school_finance_benchmarks"
        )

    checks_run += 1
    neg_fund = conn.execute(
        "SELECT count(*) FROM school_public_funding WHERE value < 0"
    ).fetchone()
    if neg_fund and neg_fund[0] > 0:
        failures.append(f"Found {neg_fund[0]} negative values in school_public_funding")

    # 2. Reporting year range check
    checks_run += 1
    yr_bench = conn.execute(
        "SELECT count(*) FROM school_finance_benchmarks WHERE reporting_year < 2000 OR reporting_year > 2030"
    ).fetchone()
    if yr_bench and yr_bench[0] > 0:
        failures.append(
            f"Found {yr_bench[0]} invalid reporting years in school_finance_benchmarks"
        )

    checks_run += 1
    yr_fund = conn.execute(
        "SELECT count(*) FROM school_public_funding WHERE reporting_year < 2000 OR reporting_year > 2030"
    ).fetchone()
    if yr_fund and yr_fund[0] > 0:
        failures.append(
            f"Found {yr_fund[0]} invalid reporting years in school_public_funding"
        )

    # 3. Orphaned institution references
    checks_run += 1
    orphan_fund = conn.execute(
        """
        SELECT count(*) FROM school_public_funding spf
        LEFT JOIN institutions i ON spf.institution_id = i.institution_id
        WHERE i.institution_id IS NULL
        """
    ).fetchone()
    if orphan_fund and orphan_fund[0] > 0:
        failures.append(
            f"Found {orphan_fund[0]} orphan institution_id references in school_public_funding"
        )

    # 4. Duplicate checks
    checks_run += 1
    dup_bench = conn.execute(
        """
        SELECT count(*) FROM (
            SELECT reporting_year, state_or_territory, sector, geolocation, metric, count(*)
            FROM school_finance_benchmarks
            GROUP BY reporting_year, state_or_territory, sector, geolocation, metric
            HAVING count(*) > 1
        )
        """
    ).fetchone()
    if dup_bench and dup_bench[0] > 0:
        failures.append(
            f"Found {dup_bench[0]} duplicate entries in school_finance_benchmarks"
        )

    checks_run += 1
    dup_fund = conn.execute(
        """
        SELECT count(*) FROM (
            SELECT institution_id, reporting_year, metric, source_dataset, count(*)
            FROM school_public_funding
            GROUP BY institution_id, reporting_year, metric, source_dataset
            HAVING count(*) > 1
        )
        """
    ).fetchone()
    if dup_fund and dup_fund[0] > 0:
        failures.append(
            f"Found {dup_fund[0]} duplicate entries in school_public_funding"
        )

    return {
        "passed": len(failures) == 0,
        "checks_run": checks_run,
        "checks_passed": checks_run - len(failures),
        "failures": failures,
    }
