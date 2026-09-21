"""Tests for upgraded SchoolMatcher with disambiguation overrides and fuzzy token matching."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from apemap.ingest.matching import SchoolMatcher

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def matcher_with_sample_fixtures(tmp_path: Path) -> SchoolMatcher:
    """Create a SchoolMatcher instance backed by sample test fixtures."""
    ext_dir = tmp_path / "external"
    ext_dir.mkdir(parents=True)
    ref_dir = tmp_path / "reference"
    ref_dir.mkdir(parents=True)

    # Copy sample fixture files
    shutil.copy(
        FIXTURES_DIR / "acara_sample.json", ext_dir / "acara_school_results.json"
    )
    shutil.copy(
        FIXTURES_DIR / "acara_profile_sample.csv", ext_dir / "school-profile-2025.csv"
    )

    # Create a test aliases file
    aliases_content = """{
      "version": "1.0.0",
      "aliases": {
        "hollywood high school": {
          "canonical_acara_id": "48250",
          "canonical_name": "Shenton College",
          "state": "WA",
          "notes": "Amalgamated into Shenton College in 2001"
        },
        "swanbourne high school": {
          "canonical_acara_id": "48250",
          "canonical_name": "Shenton College",
          "state": "WA",
          "notes": "Amalgamated into Shenton College in 2001"
        },
        "shepparton high school": {
          "canonical_acara_id": "53105",
          "canonical_name": "Greater Shepparton Secondary College",
          "state": "VIC",
          "notes": "Amalgamated into Greater Shepparton Secondary College in 2020"
        },
        "mlc": {
          "canonical_acara_id": "46144",
          "canonical_name": "Methodist Ladies' College",
          "state": "VIC",
          "notes": "Acronym for Methodist Ladies' College"
        },
        "gawler high school": {
          "canonical_acara_id": "49458",
          "canonical_name": "Gawler and District College B-12",
          "state": "SA",
          "notes": "Renamed to Gawler and District College B-12 in 2013"
        }
      }
    }"""
    aliases_file = ref_dir / "school_aliases.json"
    aliases_file.write_text(aliases_content, encoding="utf-8")

    return SchoolMatcher(
        external_dir=ext_dir,
        reference_dir=ref_dir,
        aliases_file=aliases_file,
    )


def test_historical_amalgamation_override(
    matcher_with_sample_fixtures: SchoolMatcher,
) -> None:
    """Verify historical amalgamations are resolved to current canonical institutions."""
    res_hollywood = matcher_with_sample_fixtures.match("Hollywood High School")
    assert res_hollywood.institution_id == "acara-48250"
    assert res_hollywood.school_name == "Shenton College"
    assert res_hollywood.confidence == "verified"
    assert res_hollywood.sector == "Government"
    assert "Shenton College in 2001" in (res_hollywood.reviewer_notes or "")

    res_swanbourne = matcher_with_sample_fixtures.match("Swanbourne High School")
    assert res_swanbourne.institution_id == "acara-48250"
    assert res_swanbourne.confidence == "verified"

    res_shepparton = matcher_with_sample_fixtures.match("Shepparton High School")
    assert res_shepparton.institution_id == "acara-53105"
    assert res_shepparton.school_name == "Greater Shepparton Secondary College"
    assert res_shepparton.confidence == "verified"
    assert res_shepparton.icsea == 950
    assert res_shepparton.total_enrolments == 2100


def test_abbreviation_override(matcher_with_sample_fixtures: SchoolMatcher) -> None:
    """Verify acronym override mappings (e.g. MLC)."""
    res = matcher_with_sample_fixtures.match("MLC")
    assert res.institution_id == "acara-46144"
    assert res.school_name == "Methodist Ladies' College"
    assert res.confidence == "verified"
    assert res.sector == "Independent"
    assert res.icsea == 1180
    assert res.total_enrolments == 2150


def test_exact_and_normalized_matching(
    matcher_with_sample_fixtures: SchoolMatcher,
) -> None:
    """Verify exact and normalized key matches against canonical ACARA data."""
    res_exact = matcher_with_sample_fixtures.match("Shenton College")
    assert res_exact.institution_id == "acara-48250"
    assert res_exact.confidence == "verified"
    assert res_exact.is_international is False

    res_case = matcher_with_sample_fixtures.match("shenton college")
    assert res_case.institution_id == "acara-48250"
    assert res_case.confidence == "verified"


def test_fuzzy_token_matching(matcher_with_sample_fixtures: SchoolMatcher) -> None:
    """Verify RapidFuzz token matching resolves minor variations with provisional confidence."""
    res_fuzzy = matcher_with_sample_fixtures.match("Greater Shepparton College")
    assert res_fuzzy.institution_id == "acara-53105"
    assert res_fuzzy.confidence == "provisional"
    assert "Fuzzy token-match" in (res_fuzzy.reviewer_notes or "")


def test_unmatched_and_international(
    matcher_with_sample_fixtures: SchoolMatcher,
) -> None:
    """Verify unmatched institutions and international tagging."""
    res_intl = matcher_with_sample_fixtures.match("Eton College, UK")
    assert res_intl.is_international is True
    assert res_intl.confidence == "unconfirmed"
    assert res_intl.institution_id.startswith("inst-unmatched-")

    res_unknown = matcher_with_sample_fixtures.match("Fictional Grammar School Nowhere")
    assert res_unknown.is_international is False
    assert res_unknown.confidence == "unconfirmed"
    assert res_unknown.acara_id is None
