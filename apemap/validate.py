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
            "school_finances_2021 -> institutions",
            """
            SELECT count(*) FROM school_finances_2021 f
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

    # 5. Core View Existence and Queryability
    views = [
        "v_parliament_members",
        "v_parliament_members_opening",
        "v_parliament_members_current",
        "v_member_secondary_education",
        "v_coverage_metrics",
    ]
    for v in views:
        report.checks_run += 1
        try:
            conn.execute(f"SELECT 1 FROM {v} LIMIT 1")
            report.checks_passed += 1
        except Exception as e:
            report.add_failure(f"View '{v}' is invalid or failed querying: {e}")

    logger.info(
        "Validation completed: %d checks run, %d passed, %d failures",
        report.checks_run,
        report.checks_passed,
        len(report.failures),
    )
    return report
