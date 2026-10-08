"""Focused regression tests for the issue #6 analytical contract."""

from __future__ import annotations

import pytest

import json
from datetime import date
from pathlib import Path

from apemap.analysis import (
    classify_person_education,
    classify_attendance_person,
    compute_age_at_date,
    compute_cross_parliament_summary,
    compute_chamber_sector_summary,
    compute_funding_summary,
    compute_party_sector_summary,
    compute_sector_summary,
    compute_shared_school_summary,
    export_analysis_report,
    get_opening_day_members,
    _school_sector_values,
)
from apemap.db import get_connection, init_schema
from apemap.coverage import compute_parliament_coverage
from apemap.review.model import school_review_id


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
        INSERT INTO school_finances (
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


def successor_analysis_fixture(db_path: Path) -> Path:
    """Two different predecessors share finance and unverified display coordinates."""
    create_analysis_fixture(db_path)
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE institutions SET longitude=150, latitude=-35 WHERE institution_id='school-gov'"
        )
        for education_id, name in (
            ("education-gov", "First Predecessor"),
            ("education-both-gov", "Second Predecessor"),
        ):
            conn.execute(
                """UPDATE member_education SET school_name_as_recorded=?,
                recorded_school_id=?, institution_resolution='successor',
                resolution_source_url='https://example.org/history'
                WHERE education_id=?""",
                [name, school_review_id(name), education_id],
            )
    return db_path


@pytest.mark.unit
@pytest.mark.parametrize(
    ("known_broad", "known_detail", "headline", "detail", "label"),
    [
        (
            "Government",
            "Government",
            "government_only",
            "Government",
            "Government among classified schools",
        ),
        (
            "Non-government",
            "Catholic",
            "non_government_only",
            "Catholic",
            "Non-government among classified schools",
        ),
        (None, None, "other", "Other", "Sector unavailable"),
    ],
)
def test_missing_sector_is_completeness_not_an_extra_category(
    known_broad: str | None,
    known_detail: str | None,
    headline: str,
    detail: str,
    label: str,
) -> None:
    rows = [
        {
            "broad_sector": known_broad,
            "detailed_sector": known_detail,
            "sector_basis": "original_reference",
        },
        {"broad_sector": None, "detailed_sector": None, "sector_basis": "unresolved"},
    ]
    result = classify_attendance_person(rows)
    assert result["government_non_government"] == headline
    assert result["education_classification"] == detail
    assert result["government_non_government_label"] == label
    assert result["incomplete_broad_sector_evidence"]
    assert result["incomplete_detailed_sector_evidence"]
    assert (
        classify_attendance_person([])["education_classification"]
        == "No School Recorded"
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ([("Government", "Government"), ("Non-government", None)], (None, None)),
        (
            [("Non-government", "Catholic"), (None, None)],
            ("Non-government", "Catholic"),
        ),
        (
            [("Non-government", "Catholic"), ("Non-government", "Independent")],
            ("Non-government", None),
        ),
    ],
)
def test_school_reducer_never_chooses_an_assertion_order_winner(
    values: list[tuple[str | None, str | None]], expected: tuple[str | None, str | None]
) -> None:
    rows = [
        {
            "broad_sector": broad,
            "detailed_sector": detailed,
            "sector_basis": "original_reference",
        }
        for broad, detailed in values
    ]
    assert _school_sector_values(rows) == expected
    assert _school_sector_values(list(reversed(rows))) == expected


@pytest.mark.unit
def test_strict_classification_retains_other_verified_schools() -> None:
    rows = [
        {
            "broad_sector": "Government",
            "detailed_sector": "Government",
            "sector_basis": "original_reference",
        },
        {
            "broad_sector": "Non-government",
            "detailed_sector": None,
            "sector_basis": "successor_assumption",
        },
    ]
    baseline = classify_attendance_person(rows)
    strict = classify_attendance_person(rows, exclude_successor_assumptions=True)
    assert baseline["government_non_government"] == "mixed"
    assert baseline["education_classification"] == "Government"
    assert strict["government_non_government"] == "government_only"
    assert (
        strict["government_non_government_label"]
        == "Government among classified schools"
    )
    assert strict["education_classification"] == "Government"
    assert strict["incomplete_broad_sector_evidence"]
    assert strict["successor_assumption_affected"]


@pytest.mark.unit
def test_successor_sensitivity_keeps_people_and_recorded_denominators(
    tmp_path: Path,
) -> None:
    with get_connection(
        successor_analysis_fixture(tmp_path / "successor.duckdb")
    ) as conn:
        summary = compute_sector_summary(conn, 47)
        sensitivity = summary["successor_sensitivity"]
        baseline = sensitivity["baseline"]
        strict = sensitivity["without_successor_assumptions"]
        for key in (
            "total_parliamentarians",
            "known_school_denominator",
            "total_attendance_instances",
            "total_unique_schools",
        ):
            assert baseline[key] == strict[key]
        assert baseline["total_parliamentarians"] == 5
        assert baseline["known_school_denominator"] == 4
        assert baseline["total_attendance_instances"] == 5
        assert baseline["total_unique_schools"] == 4
        assert summary["unique_schools"]["identity_counts"] == {
            "original_verified": 0,
            "original_reference": 2,
            "recorded_name_provisional": 2,
            "unresolved": 0,
        }
        assert baseline["broad_sector_evidence_denominator"] == 4
        assert baseline["detailed_sector_evidence_denominator"] == 3
        assert strict["broad_sector_evidence_denominator"] == 3
        assert baseline["government_non_government"]["mixed"] == 1
        assert strict["government_non_government"]["mixed"] == 0
        assert baseline["government_non_government"]["government_only"] == 1
        assert strict["government_non_government"]["other"] == 1
        assert (
            baseline["unique_parliamentarians_by_sector"]
            == strict["unique_parliamentarians_by_sector"]
        )
        assert baseline["unique_parliamentarians_by_sector"]["Independent"] == 2
        assert baseline["unique_parliamentarians_by_sector"]["Other"] == 1
        assert sensitivity["affected_members"] == 2
        assert sensitivity["affected_assertions"] == 2
        assert sensitivity["affected_schools"] == 2
        assert (
            sensitivity["difference_percentage_points"][
                "government_non_government_percentages"
            ]["mixed"]
            == -25.0
        )
        assert strict["members_with_incomplete_broad_sector_evidence"] == 2
        for grouped in (
            compute_party_sector_summary(conn, 47)["parties"]["TST"],
            compute_chamber_sector_summary(conn, 47)["chambers"]["representatives"],
        ):
            assert (
                grouped["government_non_government"]
                == baseline["government_non_government"]
            )
            assert (
                grouped["successor_sensitivity"]["without_successor_assumptions"][
                    "government_non_government"
                ]
                == strict["government_non_government"]
            )


@pytest.mark.unit
def test_predecessor_school_counts_and_finance_use_different_grains(
    tmp_path: Path,
) -> None:
    with get_connection(successor_analysis_fixture(tmp_path / "grains.duckdb")) as conn:
        shared = compute_shared_school_summary(conn, 47)
        assert shared["total_shared_schools"] == 1
        assert shared["schools"][0]["institution_id"] == "school-ind"
        assert shared["schools"][0]["member_count"] == 2
        finance = compute_funding_summary(conn, 47)
        assert finance["represented_attended_schools"] == 4
        assert finance["finance_reporting_institution_count"] == 3
        assert finance["overall_gross_income"] == {
            "mean": 150.0,
            "median": 150.0,
            "n": 2,
            "missing": 1,
        }
        coverage = compute_parliament_coverage(conn, [47], finance_year=2021)[0]
        assert coverage["represented_schools"] == 4
        assert coverage["finance_reporting_institutions"] == 3
        assert coverage["displayed_mapped_schools"] == 2
        assert coverage["schools_with_verified_attendance_geography"] == 0
        assert coverage["schools_eligible_attendance_geography"] == 0
        conn.execute(
            "UPDATE institutions SET longitude=151, latitude=-34 WHERE institution_id='school-cath'"
        )
        reference_coverage = compute_parliament_coverage(conn, [47], finance_year=2021)[
            0
        ]
        assert reference_coverage["schools_eligible_attendance_geography"] == 1
        assert reference_coverage["schools_with_verified_attendance_geography"] == 0
        conn.execute(
            """UPDATE member_education SET historical_scope_confirmed=TRUE,
            historical_longitude=149, historical_latitude=-36,
            historical_location_source_url='https://example.org/original-location'
            WHERE education_id='education-gov'"""
        )
        historical_coverage = compute_parliament_coverage(
            conn, [47], finance_year=2021
        )[0]
        assert historical_coverage["displayed_mapped_schools"] == 3
        assert historical_coverage["schools_eligible_attendance_geography"] == 2
        assert historical_coverage["schools_with_verified_attendance_geography"] == 1


@pytest.mark.unit
def test_verified_original_unifies_aliases_and_sector_evidence(tmp_path: Path) -> None:
    with get_connection(
        successor_analysis_fixture(tmp_path / "aliases.duckdb")
    ) as conn:
        conn.execute(
            "INSERT INTO institutions(institution_id,school_name,sector) VALUES ('manual:original','Original School','Other')"
        )
        conn.execute("""UPDATE member_education SET attended_institution_id='manual:original',
            attended_identity_source_url='https://example.org/identity', historical_scope_confirmed=TRUE
            WHERE institution_resolution='successor'""")
        conn.execute("""UPDATE member_education SET historical_broad_sector='Government',
            historical_broad_sector_source_url='https://example.org/sector'
            WHERE education_id='education-gov'""")
        summary = compute_sector_summary(conn, 47)
        assert summary["unique_schools"]["total_unique_schools"] == 3
        assert summary["unique_schools"]["by_sector"]["Government"] == 1
        assert summary["unique_schools"]["identity_counts"]["original_verified"] == 1
        assert summary["successor_sensitivity"]["affected_members"] == 0
        shared = compute_shared_school_summary(conn, 47)
        original = next(
            s for s in shared["schools"] if s["institution_id"] == "manual:original"
        )
        assert original["school_name"] == "Original School"
        assert original["member_count"] == 2
        assert original["sector"] == "Government"
        conn.execute("""UPDATE member_education SET historical_broad_sector='Non-government',
            historical_broad_sector_source_url='https://example.org/conflicting-sector'
            WHERE education_id='education-both-gov'""")
        conflict = compute_sector_summary(conn, 47)
        assert conflict["unique_schools"]["by_sector"]["Government"] == 0
        assert conflict["successor_sensitivity"]["affected_members"] == 0
        assert conflict["government_non_government"]["mixed"] == 0


@pytest.mark.unit
def test_unresolved_original_identity_does_not_merge_null_assertions(
    tmp_path: Path,
) -> None:
    with get_connection(
        successor_analysis_fixture(tmp_path / "unresolved.duckdb")
    ) as conn:
        conn.execute(
            "UPDATE member_education SET recorded_school_id=NULL WHERE institution_resolution='successor'"
        )
        summary = compute_sector_summary(conn, 47)
        assert summary["unique_schools"]["total_unique_schools"] == 4
        assert summary["unique_schools"]["identity_counts"]["unresolved"] == 2
        assert (
            summary["unique_schools"]["identity_counts"]["recorded_name_provisional"]
            == 0
        )
        shared = compute_shared_school_summary(conn, 47)
        assert shared["total_shared_schools"] == 1
        assert shared["schools"][0]["attended_school_id"] == "school-ind"


@pytest.mark.unit
def test_shared_school_never_uses_successor_as_an_unknown_original_name(
    tmp_path: Path,
) -> None:
    with get_connection(
        successor_analysis_fixture(tmp_path / "unknown-name.duckdb")
    ) as conn:
        conn.execute(
            "UPDATE member_education SET school_name_as_recorded=NULL WHERE education_id='education-gov'"
        )
        shared = compute_shared_school_summary(conn, 47, min_members=1)
        original = next(
            school
            for school in shared["schools"]
            if school["attended_school_id"] == school_review_id("First Predecessor")
        )
        assert original["school_name"] == "Original school unknown"
        assert original["resolved_institution_ids"] == ["school-gov"]


@pytest.mark.unit
def test_coverage_display_count_matches_web_when_successor_points_conflict(
    tmp_path: Path,
) -> None:
    from apemap.export import web_school_records

    with get_connection(
        successor_analysis_fixture(tmp_path / "display-conflict.duckdb")
    ) as conn:
        conn.execute("""INSERT INTO institutions(institution_id,school_name,sector,longitude,latitude)
            VALUES ('manual:original','Original School','Other',NULL,NULL),
                   ('other-successor','Other Successor','Government',151,-36)""")
        conn.execute("""UPDATE member_education SET attended_institution_id='manual:original',
            attended_identity_source_url='https://example.org/original-identity',
            historical_scope_confirmed=TRUE WHERE education_id='education-gov'""")
        conn.execute(
            """INSERT INTO member_education(education_id,member_id,institution_id,
            level,attended_status,source_url,retrieved_at,confidence,
            school_name_as_recorded,recorded_school_id,institution_resolution,
            resolution_source_url,attended_institution_id,attended_identity_source_url,historical_scope_confirmed)
            VALUES ('other-alias','m-gov','other-successor','secondary','attended_unspecified',
            'https://example.org/attendance','2025-01-01','verified',
            'Original Alias',?,'successor','https://example.org/relationship',
            'manual:original','https://example.org/alias-identity',TRUE)""",
            [school_review_id("Original Alias")],
        )
        records = web_school_records(conn, [47], finance_reporting_year=2021)
        conflicted = records["manual:original"]
        assert conflicted["longitude"] is None
        assert conflicted["latitude"] is None
        assert conflicted["location_conflict"]
        mapped = sum(
            record["longitude"] is not None and record["latitude"] is not None
            for record in records.values()
        )
        assert mapped == 1
        coverage = compute_parliament_coverage(conn, [47], finance_year=2021)[0]
        assert coverage["displayed_mapped_schools"] == mapped
        assert coverage["represented_schools"] == len(records)


@pytest.mark.unit
def test_age_is_independent_of_execution_date() -> None:
    """Birthday and leap-year boundaries use only the supplied reference date."""
    assert compute_age_at_date("1980-07-27", "2022-07-26") == 41
    assert compute_age_at_date("1980-07-26", "2022-07-26") == 42
    assert compute_age_at_date("2000-02-29", "2021-02-28") == 20
    assert compute_age_at_date("2000-02-29", "2021-03-01") == 21


@pytest.mark.unit
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


@pytest.mark.unit
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


@pytest.mark.unit
def test_sector_and_funding_summary_exclude_non_opening_day_members(
    tmp_path: Path,
) -> None:
    """Verify sector and funding summaries exclude later entrants (non-opening day members)."""
    db_path = create_analysis_fixture(tmp_path / "analysis_late.duckdb")
    conn = get_connection(db_path)
    # Insert a non-opening day member who attended a new Government school
    conn.execute(
        """
        INSERT INTO members (member_id, family_name, given_name, display_name, gender, aph_id)
        VALUES ('m-late', 'Late', 'Entrant', 'Late Entrant', 'Female', 'APH-late')
        """
    )
    conn.execute(
        """
        INSERT INTO parliament_service (
            service_id, member_id, parliament_number, chamber, party, party_abbrev,
            state_or_territory, service_start, is_opening_day_member, is_current_member
        ) VALUES (
            'srv-late', 'm-late', 47, 'representatives', 'Test', 'TST',
            'NSW', '2023-06-01', FALSE, TRUE
        )
        """
    )
    conn.execute(
        """
        INSERT INTO institutions (institution_id, school_name, sector)
        VALUES ('school-late', 'Late School', 'Government')
        """
    )
    conn.execute(
        """
        INSERT INTO member_education (
            education_id, member_id, institution_id, level, attended_status,
            source_url, retrieved_at, confidence
        ) VALUES ('edu-late', 'm-late', 'school-late', 'secondary', 'graduated', 'fixture',
                  '2025-01-01 00:00:00+00', 'verified')
        """
    )
    conn.execute(
        """
        INSERT INTO school_finances (
            institution_id, acara_id, total_gross_income_per_student,
            total_net_recurrent_income_per_student, reporting_year
        ) VALUES ('school-late', '400', 9999, 9999, 2021)
        """
    )

    sector_summary = compute_sector_summary(conn, 47)
    # The late entrant should NOT increase total_parliamentarians (should still be 5)
    assert sector_summary["total_parliamentarians"] == 5
    assert sector_summary["unique_parliamentarians_by_sector"]["Government"] == 1

    funding_summary = compute_funding_summary(conn, 47)
    # The late school should NOT be in scope for Parliament 47 opening-day baseline
    assert funding_summary["total_schools_in_scope"] == 3
    conn.close()


@pytest.mark.integration
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
        "successor_sensitivity.json",
        "school_finance.json",
        "parliament_comparison.json",
        "party_sectors.json",
        "shared_schools.json",
        "cross_parliament.json",
    )
    for filename in files:
        first = (output_one / "analysis" / filename).read_bytes()
        second = (output_two / "analysis" / filename).read_bytes()
        assert first == second

    metadata = json.loads((output_one / "analysis" / "metadata.json").read_text())
    assert metadata["schema_version"] == "2.0.0"
    assert metadata["parliament_numbers"] == [47]
    assert metadata["finance_reporting_year"] == 2021
    assert "ACARA" in metadata["finance_source_attribution"]
    assert "Terms of Use" in metadata["finance_licence"]


@pytest.mark.unit
def test_compute_funding_summary_explicit_reporting_year(tmp_path: Path) -> None:
    """compute_funding_summary accurately filters by explicit reporting_year and includes attribution."""
    db_path = create_analysis_fixture(tmp_path / "analysis_multi_year.duckdb")
    conn = get_connection(db_path)

    # Insert 2024 finance record for school-gov
    conn.execute(
        """
        INSERT INTO school_finances (
            institution_id, acara_id, total_gross_income_per_student,
            total_net_recurrent_income_per_student, reporting_year
        ) VALUES ('school-gov', '100', 500, 450, 2024)
        """
    )

    # 2021 query
    summary_2021 = compute_funding_summary(conn, 47, reporting_year=2021)
    assert summary_2021["reporting_year"] == 2021
    assert "ACARA" in summary_2021["source_attribution"]
    assert (
        summary_2021["by_sector"]["Government"]["gross_income_per_student_avg"] == 100.0
    )

    # 2024 query
    summary_2024 = compute_funding_summary(conn, 47, reporting_year=2024)
    assert summary_2024["reporting_year"] == 2024
    assert (
        summary_2024["by_sector"]["Government"]["gross_income_per_student_avg"] == 500.0
    )
    conn.close()


@pytest.mark.unit
def test_government_non_government_and_three_denominators(tmp_path: Path) -> None:
    """Verify Government vs Non-government / Mixed classification and 3 distinct denominators."""
    db_path = create_analysis_fixture(tmp_path / "analysis_denoms.duckdb")
    conn = get_connection(db_path)

    # Add member attending both Catholic and Independent (should be Non-government only, NOT Mixed)
    conn.execute(
        """
        INSERT INTO members (member_id, family_name, given_name, display_name, gender, aph_id)
        VALUES ('m-multi-nongov', 'Multi', 'NonGov', 'Multi NonGov', 'Female', 'APH-multi-ng')
        """
    )
    conn.execute(
        """
        INSERT INTO parliament_service (
            service_id, member_id, parliament_number, chamber, party, party_abbrev,
            state_or_territory, service_start, is_opening_day_member, is_current_member
        ) VALUES (
            'srv-multi-ng', 'm-multi-nongov', 47, 'representatives', 'Test', 'TST',
            'NSW', '2022-07-26', TRUE, TRUE
        )
        """
    )
    conn.execute(
        """
        INSERT INTO member_education (
            education_id, member_id, institution_id, level, attended_status,
            source_url, retrieved_at, confidence
        ) VALUES
            ('edu-ng-cath', 'm-multi-nongov', 'school-cath', 'secondary', 'graduated', 'fixture', '2025-01-01', 'verified'),
            ('edu-ng-ind', 'm-multi-nongov', 'school-ind', 'secondary', 'graduated', 'fixture', '2025-01-01', 'verified')
        """
    )

    summary = compute_sector_summary(conn, 47)
    conn.close()

    # Total MPs = 6 (5 original + 1 multi-ng)
    assert summary["total_parliamentarians"] == 6
    assert summary["known_school_denominator"] == 5
    assert summary["parliamentarians_without_known_schools"] == 1

    # Member-level Government vs Non-government / Mixed
    gng = summary["government_non_government"]
    assert gng["government_only"] == 1  # m-gov
    assert gng["non_government_only"] == 3  # m-ind, m-cath, m-multi-nongov
    assert gng["mixed"] == 1  # m-both (Gov + Ind)
    assert gng["other"] == 0
    assert gng["no_school_recorded"] == 1  # m-none

    gng_pct = summary["government_non_government_percentages"]
    assert gng_pct["government_only"] == 20.0
    assert gng_pct["non_government_only"] == 60.0
    assert gng_pct["mixed"] == 20.0

    # Detailed sector
    ds = summary["detailed_sector"]
    assert ds["government"] == 1
    assert ds["catholic"] == 1
    assert ds["independent"] == 1
    assert ds["combined_multiple"] == 2  # m-both, m-multi-nongov
    assert ds["other"] == 0
    assert ds["no_school_recorded"] == 1

    # Denominator 2: Attendance instances (7 total: 5 original + 2 for multi-ng)
    assert summary["total_attendance_instances"] == 7
    att_gng = summary["attendance_instances_government_non_government"]
    assert att_gng["government"] == 2  # m-gov, m-both
    assert (
        att_gng["non_government"] == 5
    )  # m-ind (1), m-cath (2), m-both (1), m-multi-nongov (1 ind, 1 cath)

    # Denominator 3: Unique schools (3 unique institutions: school-gov, school-cath, school-ind)
    uniq = summary["unique_schools"]
    assert uniq["total_unique_schools"] == 3
    assert uniq["government_non_government"]["government"] == 1
    assert uniq["government_non_government"]["non_government"] == 2
    assert uniq["percentages_government_non_government"]["government"] == round(
        1 / 3 * 100, 2
    )
    assert uniq["percentages_government_non_government"]["non_government"] == round(
        2 / 3 * 100, 2
    )


@pytest.mark.unit
def test_classify_person_education() -> None:
    """Verify person classification into standard and gov/non-gov categories."""
    assert classify_person_education(set()) == (
        "No School Recorded",
        "no_school_recorded",
    )
    assert classify_person_education({"Government"}) == (
        "Government",
        "government_only",
    )
    assert classify_person_education({"Catholic"}) == (
        "Catholic",
        "non_government_only",
    )
    assert classify_person_education({"Independent"}) == (
        "Independent",
        "non_government_only",
    )
    assert classify_person_education({"Catholic", "Independent"}) == (
        "Combined/Multiple",
        "non_government_only",
    )
    assert classify_person_education({"Government", "Catholic"}) == (
        "Combined/Multiple",
        "mixed",
    )
    assert classify_person_education({"Government", "Independent"}) == (
        "Combined/Multiple",
        "mixed",
    )
    assert classify_person_education({"Other"}) == ("Other", "other")
    assert classify_person_education({"Government", "Other"}) == (
        "Government",
        "government_only",
    )
    assert classify_person_education({"Catholic", "Other"}) == (
        "Catholic",
        "non_government_only",
    )


@pytest.mark.unit
def test_get_opening_day_members_deduplication(tmp_path: Path) -> None:
    """Verify deduplication when an MP has multiple opening day service records."""
    db_path = create_analysis_fixture(tmp_path / "analysis_dedup.duckdb")
    conn = get_connection(db_path)
    conn.execute(
        """
        INSERT INTO parliament_service (
            service_id, member_id, parliament_number, chamber, party,
            party_abbrev, state_or_territory, service_start, is_opening_day_member,
            is_current_member
        ) VALUES ('service-m-gov-2', 'm-gov', 47, 'representatives', 'Different Party',
                  'DIF', 'NSW', '2022-08-01', TRUE, TRUE)
        """
    )
    members = get_opening_day_members(conn, 47)
    conn.close()

    assert len(members) == 5
    m_gov = [m for m in members if m["member_id"] == "m-gov"][0]
    assert m_gov["party_abbrev"] == "TST"


@pytest.mark.unit
def test_compute_party_sector_summary(tmp_path: Path) -> None:
    """Verify party-by-sector breakdown and percentage calculations."""
    db_path = create_analysis_fixture(tmp_path / "analysis_party.duckdb")
    conn = get_connection(db_path)

    conn.execute(
        "UPDATE parliament_service SET party_abbrev = 'ALP', party = 'Labor' WHERE member_id = 'm-ind'"
    )
    conn.execute(
        "UPDATE parliament_service SET party_abbrev = 'LP', party = 'Liberal' WHERE member_id = 'm-cath'"
    )

    summary = compute_party_sector_summary(conn, 47)
    conn.close()

    assert summary["parliament_number"] == 47
    assert summary["total_parliamentarians"] == 5
    parties = summary["parties"]

    assert "ALP" in parties
    assert "LP" in parties
    assert "TST" in parties

    alp = parties["ALP"]
    assert alp["total_parliamentarians"] == 1
    assert alp["known_school_denominator"] == 1
    assert alp["unique_parliamentarians_by_sector"]["Independent"] == 1
    assert alp["percentage_of_known_parliamentarians"]["Independent"] == 100.0
    assert alp["government_non_government"]["non_government_only"] == 1

    tst = parties["TST"]
    assert tst["total_parliamentarians"] == 3
    assert tst["known_school_denominator"] == 2
    assert tst["parliamentarians_without_known_schools"] == 1
    assert tst["unique_parliamentarians_by_sector"]["Government"] == 1
    assert tst["unique_parliamentarians_by_sector"]["Combined/Multiple"] == 1
    assert tst["percentage_of_known_parliamentarians"]["Government"] == 50.0
    assert tst["percentage_of_known_parliamentarians"]["Combined/Multiple"] == 50.0


@pytest.mark.unit
def test_compute_shared_school_summary(tmp_path: Path) -> None:
    """Verify shared school identification and bipartisanship detection."""
    db_path = create_analysis_fixture(tmp_path / "analysis_shared.duckdb")
    conn = get_connection(db_path)

    conn.execute(
        "UPDATE parliament_service SET party_abbrev = 'ALP', party = 'Labor' WHERE member_id = 'm-gov'"
    )
    conn.execute(
        "UPDATE parliament_service SET party_abbrev = 'LP', party = 'Liberal' WHERE member_id = 'm-both'"
    )

    shared = compute_shared_school_summary(conn, 47, min_members=2)
    conn.close()

    assert shared["parliament_number"] == 47
    assert shared["min_members_threshold"] == 2
    assert shared["total_shared_schools"] == 2

    school_ids = [s["institution_id"] for s in shared["schools"]]
    assert "school-gov" in school_ids
    assert "school-ind" in school_ids

    gov_school = [s for s in shared["schools"] if s["institution_id"] == "school-gov"][
        0
    ]
    assert gov_school["member_count"] == 2
    assert gov_school["is_bipartisan"] is True
    assert gov_school["parties"] == {"ALP": 1, "LP": 1}
    assert len(gov_school["members"]) == 2


@pytest.mark.unit
def test_compute_cross_parliament_summary(tmp_path: Path) -> None:
    """Verify longitudinal summary across parliaments."""
    db_path = create_analysis_fixture(tmp_path / "analysis_cross.duckdb")
    conn = get_connection(db_path)

    conn.execute(
        """
        INSERT INTO parliament_service (
            service_id, member_id, parliament_number, chamber, party,
            party_abbrev, state_or_territory, service_start, is_opening_day_member,
            is_current_member
        )
        SELECT
            'p48-' || service_id, member_id, 48, chamber, party,
            party_abbrev, state_or_territory, '2025-07-26', TRUE, TRUE
        FROM parliament_service
        WHERE parliament_number = 47
        """
    )
    cross = compute_cross_parliament_summary(conn, [47, 48])
    conn.close()

    assert cross["schema_version"] == "2.0.0"
    assert cross["parliaments"] == [47, 48]
    assert "47" in cross["by_parliament"]
    assert "48" in cross["by_parliament"]
    assert "trends" in cross
    assert cross["trends"]["baseline_parliament"] == 47
    assert cross["trends"]["latest_parliament"] == 48
