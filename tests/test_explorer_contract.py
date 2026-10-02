"""Offline reconciliation tests using small synthetic canonical releases."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apemap.db import get_connection
from apemap.explorer_contract import audit_explorer_contract
from apemap.release import build_release
from tests.test_analysis import create_analysis_fixture


@pytest.fixture
def explorer_release(tmp_path: Path) -> Path:
    conn = get_connection(create_analysis_fixture(tmp_path / "explorer.duckdb"))
    conn.execute(
        "UPDATE institutions SET longitude = 145, latitude = -37 WHERE institution_id != 'school-ind'"
    )
    conn.execute("""INSERT INTO parliament_service (service_id, member_id, parliament_number, chamber, party, party_abbrev, state_or_territory, is_opening_day_member)
        SELECT '48-' || service_id, member_id, 48, 'senate', 'Changed', 'NEW', state_or_territory, TRUE FROM parliament_service WHERE parliament_number = 47""")
    release = tmp_path / "release"
    build_release(conn=conn, output_dir=release, parliaments=[47, 48], strict=False)
    conn.close()
    return release


def test_reconciliation_preserves_unmapped_and_changed_service(
    explorer_release: Path,
) -> None:
    result = audit_explorer_contract(explorer_release)
    assert result["valid"], result["errors"]
    assert result["parliaments"]["47"] == {
        "people": 5,
        "represented_schools": 3,
        "mapped_schools": 2,
        "unmapped_schools": 1,
    }
    assert (
        result["assets"]["web/members.json"]["gzip_bytes"]
        < result["assets"]["web/members.json"]["raw_bytes"]
    )


def test_reconciliation_detects_wrong_parliament_service(
    explorer_release: Path,
) -> None:
    path = explorer_release / "web/schools.geojson"
    payload = json.loads(path.read_text())
    payload["features"][0]["properties"]["members"][0]["services"][0]["party"] = (
        "Incorrect"
    )
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = audit_explorer_contract(explorer_release)
    assert not result["valid"]
    assert any("service context" in error for error in result["errors"])


def test_reconciliation_detects_duplicate_markers(explorer_release: Path) -> None:
    path = explorer_release / "web/parliament_47_combined.geojson"
    payload = json.loads(path.read_text())
    payload["features"].append(payload["features"][0])
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert any(
        "map layer" in error
        for error in audit_explorer_contract(explorer_release)["errors"]
    )


def test_reconciliation_detects_lost_unmapped_denominator(
    explorer_release: Path,
) -> None:
    path = explorer_release / "web/results-summary.json"
    payload = json.loads(path.read_text())
    payload["parliaments"]["47"]["unmapped_schools_count"] = 0
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert any(
        "mapped/unmapped" in error
        for error in audit_explorer_contract(explorer_release)["errors"]
    )


@pytest.mark.parametrize(
    "field",
    ["known_school_denominator", "known_education_count", "missing_education_count"],
)
def test_reconciliation_detects_wrong_person_denominators(
    explorer_release: Path, field: str
) -> None:
    path = explorer_release / "web/results-summary.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["parliaments"]["47"][field] = 999
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = audit_explorer_contract(explorer_release)
    assert not result["valid"]
    assert any("denominator" in error for error in result["errors"])


@pytest.mark.parametrize("field", ["government_non_government", "detailed_sector"])
def test_reconciliation_requires_complete_classification_counts(
    explorer_release: Path, field: str
) -> None:
    path = explorer_release / "web/results-summary.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["parliaments"]["47"][field] = {}
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert not audit_explorer_contract(explorer_release)["valid"]


@pytest.mark.parametrize("name", ["schools.geojson", "parliament_47_combined.geojson"])
def test_reconciliation_detects_missing_member_services(
    explorer_release: Path, name: str
) -> None:
    path = explorer_release / "web" / name
    payload = json.loads(path.read_text(encoding="utf-8"))
    member = payload["features"][0]["properties"]["members"][0]
    member["services"] = []
    member["parliaments"] = []
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = audit_explorer_contract(explorer_release)
    assert not result["valid"]
    assert any("service" in error for error in result["errors"])


def test_reconciliation_checks_term_layer_member_context(
    explorer_release: Path,
) -> None:
    path = explorer_release / "web/parliament_47_combined.geojson"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["features"][0]["properties"]["members"][0]["services"][0]["party"] = (
        "Incorrect"
    )
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = audit_explorer_contract(explorer_release)
    assert not result["valid"]
    assert any("service context" in error for error in result["errors"])


@pytest.mark.parametrize("name", ["schools.geojson", "parliament_47_combined.geojson"])
def test_reconciliation_detects_wrong_member_names(
    explorer_release: Path, name: str
) -> None:
    path = explorer_release / "web" / name
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["features"][0]["properties"]["members"][0]["name"] = "Another person"
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = audit_explorer_contract(explorer_release)
    assert not result["valid"]
    assert any("display name" in error for error in result["errors"])


def test_reconciliation_accepts_party_name_when_abbreviation_is_null(
    explorer_release: Path,
) -> None:
    path = explorer_release / "web/members.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    for people in payload["parliaments"].values():
        for member in people:
            member["party_abbrev"] = member["party"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    for path in (explorer_release / "web").glob("*.geojson"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for feature in payload["features"]:
            for member in feature["properties"]["members"]:
                for service in member["services"]:
                    service["party_abbrev"] = None
        path.write_text(json.dumps(payload), encoding="utf-8")
    result = audit_explorer_contract(explorer_release)
    assert result["valid"], result["errors"]
