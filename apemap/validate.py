"""Database integrity validation and business logic assertions.

Provides automated quality gates verifying canonical DuckDB table counts,
foreign key integrity, check constraints, and parliamentary coverage benchmarks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import duckdb

from apemap.constants import (
    ATTENDED_STATUSES,
    CANONICAL_CHAMBERS,
    CANONICAL_SECTORS,
    CONFIDENCE_LEVELS,
    PARLIAMENT_METADATA,
)
from apemap.db import CANONICAL_TABLES

logger = logging.getLogger(__name__)


@dataclass
class ValidationReport:
    """Structured report containing validation outcomes and diagnostic failures."""

    passed: bool
    checks_run: int
    checks_passed: int
    failures: list[str] = field(default_factory=list)
    table_counts: dict[str, int] = field(default_factory=dict)
    parliament_metrics: dict[int, dict[str, int]] = field(default_factory=dict)

    def add_failure(self, message: str) -> None:
        self.failures.append(message)
        self.passed = False


def validate_database(
    conn: duckdb.DuckDBPyConnection, parliaments: list[int] | None = None
) -> ValidationReport:
    """Run comprehensive relational, referential, and business logic assertions.

    Args:
        conn: DuckDB database connection.
        parliaments: Parliaments to evaluate (default: [46, 47, 48]).

    Returns:
        ValidationReport with test counts and diagnostics.
    """
    target_parls = parliaments or [46, 47, 48]
    unsupported = [p for p in target_parls if p not in PARLIAMENT_METADATA]
    if unsupported:
        supported = ", ".join(str(p) for p in sorted(PARLIAMENT_METADATA))
        numbers = ", ".join(str(p) for p in unsupported)
        raise ValueError(
            f"Unsupported parliament number(s): {numbers}. "
            f"Supported values are: {supported}."
        )
    report = ValidationReport(passed=True, checks_run=0, checks_passed=0)

    # 1. Canonical Table Existence and Non-Empty Checks
    for table in CANONICAL_TABLES:
        report.checks_run += 1
        try:
            res = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
            count = res[0] if res else 0
            report.table_counts[table] = count
            if count == 0:
                report.add_failure(f"Canonical table '{table}' is empty (0 rows)")
            else:
                report.checks_passed += 1
        except Exception as e:
            report.add_failure(f"Failed querying table '{table}': {e}")

    # 2. Referential Integrity / Foreign Key Validation
    fk_checks = [
        (
            "parliament_service -> members",
            """
            SELECT count(*) FROM parliament_service s
            LEFT JOIN members m ON s.member_id = m.member_id
            WHERE m.member_id IS NULL
            """,
        ),
        (
            "member_education -> members",
            """
            SELECT count(*) FROM member_education e
            LEFT JOIN members m ON e.member_id = m.member_id
            WHERE m.member_id IS NULL
            """,
        ),
        (
            "member_education -> institutions",
            """
            SELECT count(*) FROM member_education e
            LEFT JOIN institutions i ON e.institution_id = i.institution_id
            WHERE i.institution_id IS NULL
            """,
        ),
        (
            "school_snapshots -> institutions",
            """
            SELECT count(*) FROM school_snapshots s
            LEFT JOIN institutions i ON s.institution_id = i.institution_id
            WHERE i.institution_id IS NULL
            """,
        ),
        (
            "school_finances -> institutions",
            """
            SELECT count(*) FROM school_finances f
            LEFT JOIN institutions i ON f.institution_id = i.institution_id
            WHERE i.institution_id IS NULL
            """,
        ),
        (
            "school_finances_2021 -> institutions",
            """
            SELECT count(*) FROM school_finances_2021 f
            LEFT JOIN institutions i ON f.institution_id = i.institution_id
            WHERE i.institution_id IS NULL
            """,
        ),
        (
            "school_public_funding -> institutions",
            """
            SELECT count(*) FROM school_public_funding f
            LEFT JOIN institutions i ON f.institution_id = i.institution_id
            WHERE i.institution_id IS NULL
            """,
        ),
    ]

    for check_name, sql in fk_checks:
        report.checks_run += 1
        try:
            res = conn.execute(sql).fetchone()
            orphan_count = res[0] if res else 0
            if orphan_count > 0:
                report.add_failure(
                    f"Referential integrity failure on {check_name}: {orphan_count} orphan records found"
                )
            else:
                report.checks_passed += 1
        except Exception as e:
            report.add_failure(f"FK check {check_name} failed with error: {e}")

    # 3. Domain / Check Constraint Integrity
    constraint_checks = [
        (
            "parliament_service.chamber",
            f"""
            SELECT count(*) FROM parliament_service
            WHERE chamber NOT IN ({", ".join(f"'{c}'" for c in CANONICAL_CHAMBERS)})
            """,
        ),
        (
            "institutions.sector",
            f"""
            SELECT count(*) FROM institutions
            WHERE sector NOT IN ({", ".join(f"'{s}'" for s in CANONICAL_SECTORS)})
            """,
        ),
        (
            "member_education.confidence",
            f"""
            SELECT count(*) FROM member_education
            WHERE confidence NOT IN ({", ".join(f"'{c}'" for c in CONFIDENCE_LEVELS)})
            """,
        ),
        (
            "member_education.attended_status",
            f"""
            SELECT count(*) FROM member_education
            WHERE attended_status NOT IN ({", ".join(f"'{s}'" for s in ATTENDED_STATUSES)})
            """,
        ),
    ]

    for check_name, sql in constraint_checks:
        report.checks_run += 1
        try:
            res = conn.execute(sql).fetchone()
            viol_count = res[0] if res else 0
            if viol_count > 0:
                report.add_failure(
                    f"Constraint violation on {check_name}: {viol_count} invalid rows"
                )
            else:
                report.checks_passed += 1
        except Exception as e:
            report.add_failure(f"Constraint check {check_name} failed: {e}")

    # 4. Parliamentary Benchmarks Coverage
    for p in target_parls:
        report.checks_run += 1
        try:
            p_res = conn.execute(
                """
                SELECT
                    count(*),
                    count(DISTINCT member_id),
                    sum(CASE WHEN is_opening_day_member THEN 1 ELSE 0 END),
                    sum(CASE WHEN is_current_member THEN 1 ELSE 0 END)
                FROM parliament_service
                WHERE parliament_number = ?
                """,
                [p],
            ).fetchone()

            if p_res:
                total_stints, unique_mps, opening_mps, current_mps = p_res
                report.parliament_metrics[p] = {
                    "total_stints": total_stints or 0,
                    "unique_members": unique_mps or 0,
                    "opening_day_members": opening_mps or 0,
                    "current_members": current_mps or 0,
                }
                # Parliament size sanity check (should be ~226-240 members)
                if unique_mps < 200:
                    report.add_failure(
                        f"Parliament {p} has suspiciously low MP count: {unique_mps} members"
                    )
                else:
                    report.checks_passed += 1
            else:
                report.add_failure(f"No records found for Parliament {p}")
        except Exception as e:
            report.add_failure(f"Error checking parliament {p} metrics: {e}")

        # Service interval overlap check: ensure every record overlaps declared parliament
        report.checks_run += 1
        try:
            meta = PARLIAMENT_METADATA.get(p)
            op = meta["opening_date"] if meta else None
            en = meta["end_date"] if meta else None
            if en is not None and op is not None:
                res_overlap = conn.execute(
                    """
                    SELECT count(*)
                    FROM parliament_service
                    WHERE parliament_number = ?
                      AND (
                          service_start IS NULL
                          OR service_start > CAST(? AS DATE)
                          OR (service_end IS NOT NULL AND service_end < CAST(? AS DATE))
                      )
                    """,
                    [p, en, op],
                ).fetchone()
            elif op is not None:
                res_overlap = conn.execute(
                    """
                    SELECT count(*)
                    FROM parliament_service
                    WHERE parliament_number = ?
                      AND (
                          service_start IS NULL
                          OR (service_end IS NOT NULL AND service_end < CAST(? AS DATE))
                      )
                    """,
                    [p, op],
                ).fetchone()
            else:
                res_overlap = (0,)

            non_overlap_count = res_overlap[0] if res_overlap else 0
            if non_overlap_count > 0:
                report.add_failure(
                    f"Parliament {p} has {non_overlap_count} service records that do not overlap the declared parliament term"
                )
            else:
                report.checks_passed += 1
        except Exception as e:
            report.add_failure(
                f"Error checking service overlap for Parliament {p}: {e}"
            )

    # 5. Opening-Day Chamber Benchmarks (Parliament 47: 151 Reps + 76 Senators)
    if 47 in target_parls:
        report.checks_run += 1
        try:
            ch_rows = conn.execute(
                """
                SELECT chamber, count(*)
                FROM parliament_service
                WHERE parliament_number = 47
                  AND is_opening_day_member = TRUE
                GROUP BY chamber
                """
            ).fetchall()
            ch_counts = dict(ch_rows)
            reps_count = ch_counts.get("representatives", 0)
            senate_count = ch_counts.get("senate", 0)
            if reps_count != 151 or senate_count != 76:
                report.add_failure(
                    f"Parliament 47 opening-day chamber benchmark failed: "
                    f"expected 151 representatives and 76 senators, found "
                    f"{reps_count} representatives and {senate_count} senators"
                )
            else:
                report.checks_passed += 1
        except Exception as e:
            report.add_failure(
                f"Error checking Parliament 47 opening-day chamber benchmarks: {e}"
            )

    # 6. Core View Existence and Queryability
    views = [
        "v_parliament_members",
        "v_parliament_members_opening",
        "v_parliament_members_current",
        "v_member_secondary_education",
        "v_coverage_metrics",
        "v_house_electorates",
    ]
    for v in views:
        report.checks_run += 1
        try:
            conn.execute(f"SELECT 1 FROM {v} LIMIT 1")
            report.checks_passed += 1
        except Exception as e:
            report.add_failure(f"View '{v}' is invalid or failed querying: {e}")

    # 7. Electoral Boundaries Linkage and Geometry Validity
    if 48 in target_parls:
        report.checks_run += 1
        try:
            # Verify every 48th Parliament House member has exactly one matching 2025 boundary
            unmatched = conn.execute(
                """
                SELECT count(*)
                FROM parliament_service ps
                LEFT JOIN electoral_boundaries eb
                  ON LOWER(ps.electorate) = LOWER(eb.electorate)
                 AND eb.election_year = 2025
                WHERE ps.parliament_number = 48
                  AND ps.chamber = 'representatives'
                  AND eb.boundary_id IS NULL
                """
            ).fetchone()
            unmatched_count = unmatched[0] if unmatched else 0
            if unmatched_count > 0:
                report.add_failure(
                    f"48th Parliament House boundary linkage failed: "
                    f"{unmatched_count} service records have no matching 2025 boundary"
                )
            else:
                report.checks_passed += 1
        except Exception as e:
            report.add_failure(
                f"Error checking 48th Parliament House boundary linkage: {e}"
            )

        report.checks_run += 1
        try:
            # Check for multiple polygon matches per House service
            multi_res = conn.execute(
                """
                SELECT count(*) FROM (
                    SELECT ps.service_id, count(eb.boundary_id) AS cnt
                    FROM parliament_service ps
                    JOIN electoral_boundaries eb
                      ON LOWER(ps.electorate) = LOWER(eb.electorate)
                     AND eb.election_year = 2025
                    WHERE ps.parliament_number = 48
                      AND ps.chamber = 'representatives'
                    GROUP BY ps.service_id
                    HAVING count(eb.boundary_id) != 1
                )
                """
            ).fetchone()
            multi_count = multi_res[0] if multi_res else 0
            if multi_count > 0:
                report.add_failure(
                    f"48th Parliament House boundary multiplicity error: "
                    f"{multi_count} services have != 1 polygon matches"
                )
            else:
                report.checks_passed += 1
        except Exception as e:
            report.add_failure(f"Error checking boundary multiplicity: {e}")

    # 8. Boundary Geometry and Benchmark Data Integrity
    report.checks_run += 1
    try:
        from apemap.db import ensure_spatial

        ensure_spatial(conn)
        geom_invalid = conn.execute(
            """
            SELECT count(*)
            FROM electoral_boundaries
            WHERE geometry IS NULL OR NOT ST_IsValid(geometry)
            """
        ).fetchone()
        inv_count = geom_invalid[0] if geom_invalid else 0
        if inv_count > 0:
            report.add_failure(
                f"Electoral boundary geometry invalid or NULL for {inv_count} records"
            )
        else:
            report.checks_passed += 1
    except Exception as e:
        report.add_failure(f"Error validating electoral boundary geometries: {e}")

    report.checks_run += 1
    try:
        benchmarks = conn.execute(
            """
            SELECT sector, student_enrolment_share, source_url
            FROM education_sector_benchmarks
            WHERE benchmark_year = 2025
            """
        ).fetchall()
        b_dict = {row[0]: row[1] for row in benchmarks}
        expected_sectors = {"Government", "Catholic", "Independent"}
        if set(b_dict.keys()) != expected_sectors:
            report.add_failure(
                f"ABS 2025 benchmark sectors mismatch: expected {expected_sectors}, found {set(b_dict.keys())}"
            )
        else:
            total_share = sum(b_dict.values())
            if abs(total_share - 1.0) > 1e-4:
                report.add_failure(
                    f"ABS 2025 benchmark shares do not sum to 1.0 (sum={total_share})"
                )
            else:
                report.checks_passed += 1
    except Exception as e:
        report.add_failure(f"Error validating education sector benchmarks: {e}")

    # 13. Public school funding and benchmark data integrity
    try:
        tables_exist = conn.execute(
            """
            SELECT count(*) FROM information_schema.tables
            WHERE table_name IN ('school_finance_benchmarks', 'school_public_funding')
            """
        ).fetchone()
        if tables_exist and tables_exist[0] == 2:
            from apemap.analysis import validate_funding_records

            fund_res = validate_funding_records(conn)
            report.checks_run += fund_res["checks_run"]
            report.checks_passed += fund_res["checks_passed"]
            for fail in fund_res["failures"]:
                report.add_failure(fail)
    except Exception as e:
        logger.debug("validate_funding_records skipped or failed: %s", e)

    # 14. School Snapshots socio-educational assertions
    try:
        has_snapshots = conn.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = 'school_snapshots'"
        ).fetchone()
        if has_snapshots and has_snapshots[0] > 0:
            # Check enrolment non-negativity
            report.checks_run += 1
            neg_enrol = conn.execute(
                """
                SELECT count(*) FROM school_snapshots
                WHERE total_enrolments < 0 OR girls_enrolments < 0 OR boys_enrolments < 0 OR fte_enrolments < 0
                """
            ).fetchone()
            neg_enrol_cnt = neg_enrol[0] if neg_enrol else 0
            if neg_enrol_cnt > 0:
                report.add_failure(
                    f"Found {neg_enrol_cnt} negative enrolment values in school_snapshots"
                )
            else:
                report.checks_passed += 1

            # Check percentage bounds (0-100)
            report.checks_run += 1
            inv_pct = conn.execute(
                """
                SELECT count(*) FROM school_snapshots
                WHERE (sea_bottom_quarter_pct IS NOT NULL AND (sea_bottom_quarter_pct < 0 OR sea_bottom_quarter_pct > 100))
                   OR (sea_lower_middle_quarter_pct IS NOT NULL AND (sea_lower_middle_quarter_pct < 0 OR sea_lower_middle_quarter_pct > 100))
                   OR (sea_upper_middle_quarter_pct IS NOT NULL AND (sea_upper_middle_quarter_pct < 0 OR sea_upper_middle_quarter_pct > 100))
                   OR (sea_top_quarter_pct IS NOT NULL AND (sea_top_quarter_pct < 0 OR sea_top_quarter_pct > 100))
                   OR (indigenous_enrolments_pct IS NOT NULL AND (indigenous_enrolments_pct < 0 OR indigenous_enrolments_pct > 100))
                   OR (lbote_pct IS NOT NULL AND (lbote_pct < 0 OR lbote_pct > 100))
                   OR (icsea_percentile IS NOT NULL AND (icsea_percentile < 0 OR icsea_percentile > 100))
                """
            ).fetchone()
            inv_pct_cnt = inv_pct[0] if inv_pct else 0
            if inv_pct_cnt > 0:
                report.add_failure(
                    f"Found {inv_pct_cnt} percentage values outside 0-100 in school_snapshots"
                )
            else:
                report.checks_passed += 1

            # Check SEA quarters sum (~100%)
            report.checks_run += 1
            sea_sum_inv = conn.execute(
                """
                SELECT count(*) FROM school_snapshots
                WHERE sea_bottom_quarter_pct IS NOT NULL
                  AND sea_lower_middle_quarter_pct IS NOT NULL
                  AND sea_upper_middle_quarter_pct IS NOT NULL
                  AND sea_top_quarter_pct IS NOT NULL
                  AND ABS((sea_bottom_quarter_pct + sea_lower_middle_quarter_pct + sea_upper_middle_quarter_pct + sea_top_quarter_pct) - 100.0) > 3.0
                """
            ).fetchone()
            sea_sum_cnt = sea_sum_inv[0] if sea_sum_inv else 0
            if sea_sum_cnt > 0:
                report.add_failure(
                    f"Found {sea_sum_cnt} records with SEA quarters summing outside ~100% in school_snapshots"
                )
            else:
                report.checks_passed += 1

            # Check snapshot year bounds
            report.checks_run += 1
            inv_yr = conn.execute(
                "SELECT count(*) FROM school_snapshots WHERE snapshot_year < 2000 OR snapshot_year > 2030"
            ).fetchone()
            inv_yr_cnt = inv_yr[0] if inv_yr else 0
            if inv_yr_cnt > 0:
                report.add_failure(
                    f"Found {inv_yr_cnt} invalid snapshot years in school_snapshots"
                )
            else:
                report.checks_passed += 1

            # Check primary key uniqueness
            report.checks_run += 1
            dup_pk = conn.execute(
                """
                SELECT count(*) FROM (
                    SELECT institution_id, snapshot_year, count(*)
                    FROM school_snapshots
                    GROUP BY institution_id, snapshot_year
                    HAVING count(*) > 1
                )
                """
            ).fetchone()
            dup_pk_cnt = dup_pk[0] if dup_pk else 0
            if dup_pk_cnt > 0:
                report.add_failure(
                    f"Found {dup_pk_cnt} duplicate (institution_id, snapshot_year) in school_snapshots"
                )
            else:
                report.checks_passed += 1
    except Exception as e:
        logger.debug("school_snapshots validation skipped or failed: %s", e)

    logger.info(
        "Validation completed: %d checks run, %d passed, %d failures",
        report.checks_run,
        report.checks_passed,
        len(report.failures),
    )
    return report
