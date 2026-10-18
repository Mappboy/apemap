"""Offline guardrails for the bounded, evidence-only survey reconciliation."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import runpy
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
BATCH = ROOT / "data/reference/review/research/issue-104-survey-20261019"
EXTRACT = runpy.run_path(str(BATCH / "extract.py"))
RECONCILE = runpy.run_path(str(BATCH / "reconcile.py"))


def test_extract_is_bounded_and_excludes_children_and_unrelated_fields() -> None:
    survey = {
        "respondents": [
            {
                "name": "Example Person",
                "aph_id": "ONE",
                "response_kind": "named",
                "claims": [{"name": "Example High School"}],
            }
        ]
    }
    html = """<h3>Dr Example Person</h3><p><strong>Electorate</strong> Example</p>
    <p><strong>School</strong> Example High School</p>
    <p><strong>Are your children in a public, private or independent school?</strong> Other School</p>
    <p><strong>Religion</strong> Unrelated</p>"""
    result = EXTRACT["extract"](html, survey)
    assert result[0]["institution_names_as_reported"] == ["Example High School"]
    assert result[0]["electorate_as_reported"] == "Example"
    assert "Other School" not in str(result)
    assert "Unrelated" not in str(result)
    with pytest.raises(ValueError, match="population"):
        EXTRACT["extract"](
            html.replace("<strong>School</strong>", "<strong>Other</strong>"), survey
        )


def test_extraction_rejects_changed_claims_and_repeated_fields() -> None:
    survey = {
        "respondents": [
            {
                "name": "Example",
                "aph_id": "ONE",
                "response_kind": "named",
                "claims": [{"name": "Expected School"}],
            }
        ]
    }
    html = "<h3>Example</h3><p>Electorate Seat</p><p>School Different School</p>"
    with pytest.raises(ValueError, match="missing from source"):
        EXTRACT["extract"](html, survey)
    with pytest.raises(ValueError, match="Repeated"):
        EXTRACT["extract"](html + "<p>School Expected School</p>", survey)


def test_grade_levels_require_source_support_and_are_not_calendar_years() -> None:
    survey = {
        "respondents": [
            {
                "name": "Example",
                "aph_id": "ONE",
                "response_kind": "named",
                "claims": [{"name": "Example School", "grade_levels": [8, 9, 10]}],
            }
        ]
    }
    html = "<h3>Example</h3><p>Electorate Seat</p><p>School Example School (grades 8-10)</p>"
    assert EXTRACT["extract"](html, survey)[0]["aph_id"] == "ONE"
    with pytest.raises(ValueError, match="grade context"):
        EXTRACT["extract"](html.replace("grades 8-10", "1998-2000"), survey)


def test_grade_ranges_cannot_be_swapped_between_named_institutions() -> None:
    survey = {
        "respondents": [
            {
                "name": "Example",
                "aph_id": "ONE",
                "response_kind": "named",
                "claims": [
                    {"name": "First School", "grade_levels": [8, 9, 10]},
                    {"name": "Second School", "grade_levels": [11, 12]},
                ],
            }
        ]
    }
    html = "<h3>Example</h3><p>Electorate Seat</p><p>School First School (grades 8-10), Second School (grades 11-12)</p>"
    assert EXTRACT["extract"](html, survey)[0]["aph_id"] == "ONE"
    swapped = (
        html.replace("8-10", "temporary")
        .replace("11-12", "8-10")
        .replace("temporary", "11-12")
    )
    with pytest.raises(ValueError, match="grade context"):
        EXTRACT["extract"](swapped, survey)


def identity_fixture() -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        {
            "name": "Example Person",
            "aph_id": "ONE",
            "chamber": "representatives",
            "electorate": "Seat",
        },
        {
            "members": [
                {
                    "aph_id": "ONE",
                    "member_id": "aph-one",
                    "display_name": "PERSON, Example",
                }
            ],
            "parliament_service": [
                {
                    "member_id": "aph-one",
                    "parliament_number": 48,
                    "is_opening_day_member": True,
                    "chamber": "representatives",
                    "electorate": "Seat",
                }
            ],
        },
    )


def test_identity_requires_unique_aph_person_and_matching_service_not_name_only() -> (
    None
):
    person, records = identity_fixture()
    assert RECONCILE["identify"](person, records)["aph_id"] == "ONE"
    for field, value in (
        ("aph_id", "TWO"),
        ("electorate", "Other"),
        ("chamber", "senate"),
        ("name", "Other Person"),
    ):
        changed = {**person, field: value}
        with pytest.raises(ValueError):
            RECONCILE["identify"](changed, records)
    repeated = deepcopy(records)
    repeated["members"].append(repeated["members"][0])
    with pytest.raises(ValueError, match="repeated"):
        RECONCILE["identify"](person, repeated)


@pytest.mark.parametrize(
    "level, expected",
    [
        ("primary", "primary_evidence_only_contract_limit"),
        ("unspecified", "named_attendance_stage_unverified"),
        ("tertiary", "tertiary_not_school"),
        ("secondary", "missing_secondary_attendance_proposed"),
    ],
)
def test_stage_is_not_inferred_or_promoted(level: str, expected: str) -> None:
    result = RECONCILE["compare_claim"]({"name": "Example College", "level": level}, [])
    assert result["classification"] == expected
    assert result["proposed_institution_ref"] is None


def test_name_variants_and_mapping_history_are_not_automatic_matches() -> None:
    existing = [{"school_name_as_recorded": "Expanded School Name"}]
    result = RECONCILE["compare_claim"](
        {
            "name": "Short School",
            "level": "secondary",
            "possible_existing_name": "Expanded School Name",
        },
        existing,
    )
    assert result["classification"] == "possible_name_variant_identity_review"
    assert not result["current_assertions"]
    assert result["possible_existing_assertions"] == existing
    history = RECONCILE["compare_claim"](
        {
            "name": "Expanded School Name",
            "level": "secondary",
            "mapping_history_review": True,
        },
        existing,
    )
    assert history["classification"] == "existing_attendance_mapping_history_review"
    assert history["proposed_institution_ref"] is None
