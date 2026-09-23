"""Focused regression tests for the issue #6 analytical contract."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from apemap.analysis import (
    compute_age_at_date,
    compute_funding_summary,
    compute_sector_summary,
    export_analysis_report,
)
from apemap.db import get_connection, init_schema


def create_analysis_fixture(db_path: Path) -> Path:
    """Create a small deterministic fixture for analysis and notebook tests."""
    conn = get_connection(db_path)
    init_schema(conn)
    members = [
        ("m-gov", "Gov", "One", "Gov One", date(1980, 7, 26)),
        ("m-ind", "Ind", "Two", "Ind Two", date(1980, 7, 27)),
        ("m-both", "Both", "Three", "Both Three", date(2000, 1, 1)),
        ("m-none", "None", "Four", "None Four", None),
        ("m-cath", "Cath", "Five", "Cath Five", date(1975, 5, 5)),
    ]
    for member_id, family, given, display, birth_date in members:
        conn.execute(
            """
            INSERT INTO members (
                member_id, family_name, given_name, display_name, gender,
                date_of_birth, aph_id
            ) VALUES (?, ?, ?, ?, 'Other', ?, ?)
            """,
            [member_id, family, given, display, birth_date, f"APH-{member_id}"],
        )
        conn.execute(
            """
            INSERT INTO parliament_service (
                service_id, member_id, parliament_number, chamber, party,
                party_abbrev, state_or_territory, is_opening_day_member,
                is_current_member
            ) VALUES (?, ?, 47, 'representatives', 'Test', 'TST', 'NSW', TRUE, TRUE)
            """,
            [f"service-{member_id}", member_id],
        )

    institutions = [
        ("school-gov", "Government School", "Government"),
        ("school-cath", "Catholic School", "Catholic"),
        ("school-ind", "Independent School", "Independent"),
    ]
    for institution_id, school_name, sector in institutions:
        conn.execute(
            """
            INSERT INTO institutions (institution_id, school_name, sector)
            VALUES (?, ?, ?)
            """,
            [institution_id, school_name, sector],
        )

    education = [
        ("education-gov", "m-gov", "school-gov"),
        ("education-ind", "m-ind", "school-ind"),
        ("education-both-gov", "m-both", "school-gov"),
        ("education-both-ind", "m-both", "school-ind"),
        ("education-cath", "m-cath", "school-cath"),
    ]
    for education_id, member_id, institution_id in education:
        conn.execute(
            """
            INSERT INTO member_education (
                education_id, member_id, institution_id, level, attended_status,
                source_url, retrieved_at, confidence
            ) VALUES (?, ?, ?, 'secondary', 'graduated', 'fixture',
                      '2025-01-01 00:00:00+00', 'verified')
            """,
            [education_id, member_id, institution_id],
        )

    conn.execute(
        """
        INSERT INTO school_finances_2021 (
            institution_id, acara_id, total_gross_income_per_student,
            total_net_recurrent_income_per_student, reporting_year
        ) VALUES
            ('school-gov', '100', 100, 80, 2021),
            ('school-cath', '200', 200, 180, 2021),
            ('school-ind', '300', NULL, NULL, 2021)
        """
    )
    conn.close()
    return db_path


def test_age_is_independent_of_execution_date() -> None:
    """Birthday and leap-year boundaries use only the supplied reference date."""
    assert compute_age_at_date("1980-07-27", "2022-07-26") == 41
    assert compute_age_at_date("1980-07-26", "2022-07-26") == 42
    assert compute_age_at_date("2000-02-29", "2021-02-28") == 20
    assert compute_age_at_date("2000-02-29", "2021-03-01") == 21


def test_finance_nulls_are_missing_not_zero(tmp_path: Path) -> None:
    """The finance contract exposes mean, valid N, and missing N explicitly."""
    db_path = create_analysis_fixture(tmp_path / "analysis.duckdb")
    conn = get_connection(db_path, read_only=True)
    summary = compute_funding_summary(conn, 47)
    conn.close()

    gross = summary["overall_gross_income"]
    assert gross == {"mean": 150.0, "median": 150.0, "n": 2, "missing": 1}
    assert summary["overall_gross_income_avg"] == 150.0
    assert summary["overall_gross_income_missing_count"] == 1


def test_sector_people_and_attendance_have_distinct_denominators(
    tmp_path: Path,
) -> None:
    """A multi-school person is counted once while relationships remain separate."""
    db_path = create_analysis_fixture(tmp_path / "analysis.duckdb")
    conn = get_connection(db_path, read_only=True)
    summary = compute_sector_summary(conn, 47)
    conn.close()

    unique = summary["unique_parliamentarians_by_sector"]
    assert unique["Government"] == 1
    assert unique["Independent"] == 1
    assert unique["Combined/Multiple"] == 1
    assert unique["Catholic"] == 1
    assert unique["No School Recorded"] == 1
    assert summary["known_school_percentage_denominator"] == 4
    assert sum(summary["percentage_of_known_parliamentarians"].values()) == 100.0
    assert summary["total_attendance_instances"] == 5
    assert summary["attendance_instance_percentage_denominator"] == 5


def test_analysis_exports_are_schema_versioned_and_deterministic(
    tmp_path: Path,
) -> None:
    """Static chart exports include metadata and remain byte-for-byte stable."""
    db_path = create_analysis_fixture(tmp_path / "analysis.duckdb")
    output_one = tmp_path / "output-one"
    output_two = tmp_path / "output-two"
    conn = get_connection(db_path, read_only=True)
    export_analysis_report(conn, output_one, [47])
    export_analysis_report(conn, output_two, [47])
    conn.close()

    files = (
        "metadata.json",
        "demographics.json",
        "education_sectors.json",
        "school_finance.json",
        "parliament_comparison.json",
    )
    for filename in files:
        first = (output_one / "analysis" / filename).read_bytes()
        second = (output_two / "analysis" / filename).read_bytes()
        assert first == second

    metadata = json.loads((output_one / "analysis" / "metadata.json").read_text())
    assert metadata["schema_version"] == "1.0"
    assert metadata["parliament_numbers"] == [47]
    assert metadata["finance_reporting_year"] == 2021
