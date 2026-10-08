"""Offline school-grain regressions for successor context and the v2 handoff."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apemap.contracts import SUCCESSOR_FOOTNOTE, SUCCESSOR_LOCATION_WARNING
from apemap.db import get_connection
from apemap.explorer_contract import audit_explorer_contract
from apemap.export import (
    export_research_downloads,
    export_web_schools_geojson,
    web_school_records,
)
from apemap.prototype import explorer_payload, school_evidence_html
from apemap.release import build_release, verify_release
from apemap.review.model import school_review_id
from tests.test_analysis import successor_analysis_fixture


def successor_web_fixture(path: Path) -> Path:
    """Keep two predecessors, alias assertions, actual profiles and observed finance."""
    successor_analysis_fixture(path)
    with get_connection(path) as conn:
        conn.execute("""UPDATE parliament_service SET
            service_start=(SELECT opening_date FROM parliament_metadata WHERE parliament_number=47),
            service_end=(SELECT end_date FROM parliament_metadata WHERE parliament_number=47),
            is_current_member=FALSE""")
        conn.execute("""INSERT INTO institutions(institution_id, school_name, sector, institution_status)
            VALUES ('original-a', 'First Predecessor', 'Other', 'historical_only'),
                   ('original-b', 'Second Predecessor', 'Other', 'historical_only')""")
        conn.execute("""UPDATE member_education SET attended_institution_id='original-a',
            attended_identity_source_url='https://example.org/first-identity',
            historical_scope_confirmed=TRUE, historical_longitude=141, historical_latitude=-40,
            historical_location_source_url='https://example.org/original-location'
            WHERE education_id='education-gov'""")
        conn.execute("""UPDATE member_education SET attended_institution_id='original-b',
            attended_identity_source_url='https://example.org/second-identity', historical_scope_confirmed=TRUE
            WHERE education_id='education-both-gov'""")
        conn.execute(
            """INSERT INTO member_education(education_id, member_id, institution_id,
            level, attended_status, source_url, retrieved_at, confidence,
            school_name_as_recorded, recorded_school_id, institution_resolution,
            resolution_source_url, attended_institution_id, attended_identity_source_url, historical_scope_confirmed)
            VALUES ('alias-a', 'm-gov', 'school-gov', 'secondary', 'attended_unspecified',
            'https://example.org/alias-attendance', '2025-01-01', 'provisional',
            'First Old Name', ?, 'successor', 'https://example.org/alias-relationship',
            'original-a', 'https://example.org/alias-identity', TRUE)""",
            [school_review_id("First Old Name")],
        )
        conn.execute("""INSERT INTO school_snapshots(institution_id, snapshot_year, icsea)
            VALUES ('school-gov', 2023, 1000), ('school-gov', 2025, 1010),
                   ('school-ind', 2022, 990)""")
        conn.execute("""INSERT INTO school_finances(institution_id, acara_id, reporting_year,
            total_net_recurrent_income_per_student, source_dataset)
            VALUES ('school-gov', '100', 2024, 120, 'fixture observed 2024')""")
    return path


@pytest.fixture
def successor_release(tmp_path: Path) -> Path:
    with get_connection(successor_web_fixture(tmp_path / "source.duckdb")) as conn:
        root = tmp_path / "release"
        build_release(
            conn=conn,
            output_dir=root,
            parliaments=[47],
            strict=False,
            version="fixture",
            source_commit="a" * 40,
            generated_at="2026-10-08T00:00:00Z",
        )
        failures = json.loads((root / "web/assertions.json").read_text())["failures"]
        allowed_prefixes = (
            "Canonical table 'electoral_boundaries' is empty",
            "Canonical table 'education_sector_benchmarks' is empty",
            "Canonical table 'school_finance_benchmarks' is empty",
            "Canonical table 'school_public_funding' is empty",
            "Parliament 47 has suspiciously low MP count",
            "Parliament 47 opening-day chamber benchmark failed",
            "ABS 2025 benchmark sectors mismatch",
        )
        assert all(failure.startswith(allowed_prefixes) for failure in failures), (
            failures
        )
    return root


def test_original_coordinates_and_aliases_do_not_merge_predecessors(
    tmp_path: Path,
) -> None:
    with get_connection(successor_web_fixture(tmp_path / "source.duckdb")) as conn:
        records = web_school_records(conn, [47])
        original, fallback = records["original-a"], records["original-b"]
        assert (
            original["resolved_institution_id"]
            == fallback["resolved_institution_id"]
            == "school-gov"
        )
        assert (original["longitude"], original["latitude"]) == (141, -40)
        assert original["location_basis"] == "original_verified"
        assert original["attendance_location_eligible"]
        assert (
            original["state"] is None
        )  # Successor locality cannot fill original locality.
        assert original["member_count"] == 1
        assert len(original["members"][0]["services"]) == 1
        assert len(original["education_assertions"]) == 2
        assert fallback["location_basis"] == "successor_unverified"
        assert not fallback["attendance_location_eligible"]
        assert fallback["attendance_longitude"] is None
        assert fallback["location_warning"] == SUCCESSOR_LOCATION_WARNING
        assert (
            fallback["display_school_name"] == "Second Predecessor → Government School*"
        )
        assert (
            original["sector_basis"]
            == fallback["sector_basis"]
            == "successor_assumption"
        )
        assert original["detailed_sector"] is None
        for record in (original, fallback):
            assert (
                record["profile_institution_id"]
                == record["finance_institution_id"]
                == "school-gov"
            )
            assert (
                record["profile_basis"]
                == record["finance_basis"]
                == "successor_context"
            )
            assert record["profile_year"] == 2025
            assert record["finance_year"] == 2024
            assert record["finance_status"] == "observed"
        features = json.loads(
            export_web_schools_geojson(conn, tmp_path / "map", [47]).read_text()
        )["features"]
        assert {feature["properties"]["institution_id"] for feature in features} == {
            "original-a",
            "original-b",
        }
        downloads = export_research_downloads(conn, tmp_path / "downloads", [47])
        import csv

        with downloads["parliament-education.csv"].open(
            encoding="utf-8", newline=""
        ) as stream:
            rows = list(csv.DictReader(stream))
        assert (
            len(rows)
            == len({(row["education_id"], row["service_id"]) for row in rows})
            == 6
        )
        assert (
            next(row for row in rows if row["education_id"] == "education-both-gov")[
                "longitude"
            ]
            == ""
        )


def test_complete_unmapped_profiles_and_independent_evidence_survive_handoff(
    successor_release: Path,
) -> None:
    verification = verify_release(successor_release, strict_assertions=False)
    assert verification["valid"], verification["errors"]
    result = audit_explorer_contract(successor_release)
    assert result["valid"], result["errors"]
    assert result["parliaments"]["47"] == {
        "people": 5,
        "represented_schools": 4,
        "mapped_schools": 2,
        "unmapped_schools": 2,
    }
    metadata = json.loads((successor_release / "web/metadata.json").read_text())
    assert metadata["web_schema_version"] == "2.0.0"
    payload = explorer_payload(successor_release, metadata)
    assert payload["schools"]["school-ind"]["coordinates"] is None
    assert payload["schools"]["school-ind"]["profile_year"] == 2022
    assert "finance_value" not in json.dumps(payload)
    original = payload["schools"]["original-a"]
    html = school_evidence_html(original)
    assert "Location basis: original_verified" in html
    assert "Broad sector basis: successor_assumption" in html
    assert "Profile year: 2025" in html
    assert 'href="https://example.org/original-location"' in html
    assert 'href="https://example.org/alias-attendance"' in html
    assert payload["successor_footnote"] == SUCCESSOR_FOOTNOTE
    assert {row["confidence"] for row in original["education_assertions"]} == {
        "verified",
        "provisional",
    }


def test_audit_detects_context_and_provider_year_corruption(
    successor_release: Path,
) -> None:
    path = successor_release / "web/schools.geojson"
    payload = json.loads(path.read_text())
    feature = payload["features"][0]["properties"]
    feature["attendance_location_eligible"] = not feature[
        "attendance_location_eligible"
    ]
    feature["profile_year"] = 1900
    path.write_text(json.dumps(payload), encoding="utf-8")
    errors = audit_explorer_contract(successor_release)["errors"]
    assert any("eligibility" in error for error in errors)
    assert any("context fields" in error for error in errors)


def test_multiple_reporting_counterparts_keep_assertion_providers(
    tmp_path: Path,
) -> None:
    with get_connection(successor_web_fixture(tmp_path / "source.duckdb")) as conn:
        conn.execute(
            "UPDATE member_education SET institution_id='school-cath' WHERE education_id='alias-a'"
        )
        school = web_school_records(conn, [47])["original-a"]
        assert school["resolved_institution_id"] is None
        assert (
            school["profile_basis"] == school["finance_basis"] == "multiple_providers"
        )
        assert school["profile_year"] is None
        assert {
            provider["resolved_institution_id"]
            for provider in school["provider_contexts"]
        } == {"school-gov", "school-cath"}
        assert {
            row["finance_institution_id"] for row in school["education_assertions"]
        } == {"school-gov", "school-cath"}
        assert school["location_basis"] == "original_verified"
        assert (school["longitude"], school["latitude"]) == (141, -40)
        assert school["continuity_discrepancy"] == any(
            row["continuity_discrepancy"] for row in school["education_assertions"]
        )
        conn.execute("""INSERT INTO member_education(education_id, member_id, institution_id,
            level, attended_status, source_url, retrieved_at, confidence, institution_resolution)
            VALUES ('direct-original', 'm-ind', 'original-a', 'secondary', 'attended_unspecified',
            'https://example.org/direct-original', '2025-01-01', 'verified', 'direct')""")
        conn.execute(
            "INSERT INTO school_snapshots(institution_id, snapshot_year, icsea) VALUES ('original-a', 2010, 950)"
        )
        combined = web_school_records(conn, [47])["original-a"]
        assert len(combined["provider_contexts"]) == 3
        direct = next(
            row
            for row in combined["education_assertions"]
            if row["education_id"] == "direct-original"
        )
        assert direct["profile_institution_id"] == "original-a"
        assert direct["profile_year"] == 2010
        assert direct["profile_basis"] == "current_reference"
        assert combined["location_basis"] == "original_verified"
        assert combined["identity_basis"] == "original_verified"
        assert combined["attended_institution_id"] == "original-a"
        conn.execute(
            "UPDATE member_education SET education_id='aaa-direct' WHERE education_id='direct-original'"
        )
        reversed_order = web_school_records(conn, [47])["original-a"]
        assert reversed_order["identity_basis"] == combined["identity_basis"]
        assert (
            reversed_order["attended_institution_id"]
            == combined["attended_institution_id"]
        )
        assert (reversed_order["longitude"], reversed_order["latitude"]) == (141, -40)
        root = tmp_path / "mixed-release"
        build_release(
            conn=conn,
            output_dir=root,
            parliaments=[47],
            strict=False,
            version="fixture",
            source_commit="a" * 40,
            generated_at="2026-10-08T00:00:00Z",
        )
        report = verify_release(root, strict_assertions=False)
        assert report["valid"], report["errors"]
