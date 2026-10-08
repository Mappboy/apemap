"""Offline recipe replay, provisioning and provenance regressions."""

from __future__ import annotations

from functools import partial
import json
import sqlite3
from pathlib import Path
from typing import Any
import zipfile

import duckdb
import pytest
from typer.testing import CliRunner

from apemap.cli import app
from apemap.constants import PARLIAMENT_METADATA, PROJECT_ROOT
from apemap.inputs import compute_sha256
from apemap.db import ensure_spatial
from apemap.release.recipe import (
    build_recipe_release,
    bundle_recipe_inputs,
    compare_recipe_releases,
    load_recipe,
    pin_recipe,
    restore_recipe_inputs,
)
from apemap.release.verify import verify_release
from tests.test_review_ingestion import event, write_log
from apemap.review.model import school_review_id


@pytest.fixture
def recipe_fixture(tmp_path: Path) -> tuple[Path, Path]:
    """Tiny source files plus realistic seat counts, independent of working data."""
    root = tmp_path / "checkout"
    template = json.loads(
        (PROJECT_ROOT / "data/release-recipes/historical.json").read_text()
    )
    inputs: dict[str, bytes] = {}
    for suffix in ("shp", "shx", "dbf", "prj"):
        inputs[f"data/raw/aec/2025/AUS_ELB_region.{suffix}"] = b"fixture-boundary"
    acara_dir = template["acara_input_dir"]
    inputs[f"{acara_dir}/acara_school_results.json"] = b"[]"
    inputs[f"{acara_dir}/school-location-2025.csv"] = (
        b"ACARA SML ID,School Name,School Sector,School Type,State,Suburb,Postcode,Longitude,Latitude\n"
        b"1,Fixture High School,Government,Secondary,VIC,Melbourne,3000,145,-37\n"
    )
    inputs[f"{acara_dir}/school-profile-2008-2025.csv"] = (
        "ACARA SML ID,School Name,School Sector,School Type,State,Calendar Year,Total Enrolments\n"
        + "".join(
            f"1,Fixture High School,Government,Secondary,VIC,{year},300\n"
            for year in range(2008, 2026)
        )
    ).encode()
    records: list[dict[str, Any]] = []
    for p in template["parliaments"]:
        meta = PARLIAMENT_METADATA[p]
        start, end = meta["opening_date"], meta["end_date"] or "2026-09-21"
        for chamber, count in (
            ("Member", meta["expected_representatives"]),
            ("Senator", meta["expected_senators"]),
        ):
            for i in range(count):
                records.append(
                    {
                        "PHID": f"P{p}{chamber}{i}",
                        "GivenName": "Fixture",
                        "FamilyName": str(i),
                        "RepresentedParliaments": [p],
                        "MPorSenator": [chamber],
                        "SenateState": "Victoria",
                        "Party": "Independent",
                        "InCurrentParliament": "False",
                        "ServiceHistory_Start": start,
                        "ServiceHistory_End": end,
                        "ElectorateService": [
                            {
                                "Electorate": "Fixture Seat",
                                "State": "Victoria",
                                "ServiceStart": start,
                                "ServiceEnd": end,
                            }
                        ]
                        if chamber == "Member"
                        else [],
                        "PartyParliamentaryService": [
                            {
                                "DateStart": start,
                                "DateEnd": end,
                                "SecondaryService": [
                                    {
                                        "RoSType": "Parties Represented",
                                        "Value": "Independent",
                                        "DateStart": start,
                                        "DateEnd": end,
                                    }
                                ],
                            }
                        ],
                        "SecondarySchool": "Fixture High School",
                    }
                )
    inputs["data/raw/aph/individuals.json"] = json.dumps(records).encode()
    for name in (
        "acara_school_finance_benchmarks",
        "manual_school_funding",
        "nsw_ram_allocations",
        "nt_school_funding",
        "qld_non_state_grants",
        "tasmania_srp_allocations",
    ):
        inputs[f"data/reference/{name}.csv"] = b"acara_id,reporting_year\n"
    inputs["data/reference/acara_school_finance_benchmarks.csv"] = (
        b"reporting_year,state_or_territory,sector,geolocation,metric,value\n"
        b"2024,VIC,Government,All,total_net_recurrent_income_per_student,20000\n"
    )
    inputs["data/reference/manual_school_funding.csv"] = (
        b"acara_id,reporting_year,metric,value,unit,source_url,source_type,reviewed_at\n"
        b"1,2024,total_net_recurrent_income_per_student,20000,AUD_per_student,https://example.org/disclosure,annual_report,2026-10-01T00:00:00Z\n"
    )
    files = {}
    for name, data in inputs.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        files[name] = {
            "required": True,
            "sha256": compute_sha256(path),
            "size_bytes": len(data),
        }
    manifest = root / template["input_manifest"]
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "created_at": "2026-10-01T00:00:00+00:00",
                "files": files,
            }
        ),
        newline="\n",
    )
    finance_path = root / template["legacy_finance_input"]
    with sqlite3.connect(finance_path) as finance:
        finance.execute(
            "CREATE TABLE acara_education_finances(acara_id INTEGER, year INTEGER, total_net_recurrent_income_per_student INTEGER, total_gross_income_total INTEGER)"
        )
        finance.execute(
            "INSERT INTO acara_education_finances VALUES (1, 2021, 20000, 6000000)"
        )
    ledger = root / template["decision_log"]
    ledger.parent.mkdir(parents=True, exist_ok=True)
    write_log(
        ledger,
        [
            event(
                "review-fixture",
                school_review_id("Unresolved Fixture School"),
                "school",
                {
                    "recorded_name": "Unresolved Fixture School",
                },
                action="research",
            )
        ],
    )
    for key in ("input_manifest", "decision_log", "legacy_finance_input"):
        template[key + "_sha256"] = compute_sha256(root / template[key])
    recipe = root / "recipe.json"
    recipe.write_text(json.dumps(template, sort_keys=True) + "\n", newline="\n")
    return root, recipe


@pytest.mark.unit
@pytest.mark.parametrize(
    "field,value",
    [
        ("finance_year", 2021),
        ("acara_location_year", 2022),
        ("acara_profile_years", [2022]),
        ("profile_selection", "current"),
        ("web_schema_version", "1.0.0"),
        ("parliaments", [999]),
    ],
)
def test_recipe_rejects_silent_source_defaults(
    recipe_fixture: tuple[Path, Path], field: str, value: object
) -> None:
    root, recipe = recipe_fixture
    data = json.loads(recipe.read_bytes())
    data[field] = value
    recipe.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Unsupported recipe|Recipe parliaments"):
        load_recipe(recipe, root=root)


@pytest.mark.unit
def test_recipe_requires_explicit_year_and_exact_review_revision(
    recipe_fixture: tuple[Path, Path],
) -> None:
    root, recipe = recipe_fixture
    original = recipe.read_bytes()
    data = json.loads(original)
    del data["finance_year"]
    recipe.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="configuration fields"):
        load_recipe(recipe, root=root)
    recipe.write_bytes(original)
    (root / data["decision_log"]).write_bytes(b"")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        load_recipe(recipe, root=root)
    pinned = root / "new-recipe.json"
    pin_recipe(recipe, pinned, root=root)
    assert load_recipe(pinned, root=root)["decision_log_sha256"] == compute_sha256(
        root / data["decision_log"]
    )
    with pytest.raises(FileExistsError):
        pin_recipe(recipe, pinned, root=root)


@pytest.mark.unit
def test_recipe_inputs_are_deterministic_and_restored_by_hash(
    recipe_fixture: tuple[Path, Path], tmp_path: Path
) -> None:
    root, recipe = recipe_fixture
    first, second = tmp_path / "first.tar.gz", tmp_path / "second.tar.gz"
    bundle_recipe_inputs(recipe, first, root=root)
    bundle_recipe_inputs(recipe, second, root=root)
    assert first.read_bytes() == second.read_bytes()
    source = root / "data/raw/aph/individuals.json"
    original = source.read_bytes()
    source.write_bytes(b"changed")
    restore_recipe_inputs(recipe, archive=first, root=root)
    assert source.read_bytes() == original
    with pytest.raises(FileExistsError):
        bundle_recipe_inputs(recipe, first, root=root)


@pytest.mark.unit
def test_recipe_restoration_rejects_tampered_inputs_before_copy(
    recipe_fixture: tuple[Path, Path], tmp_path: Path
) -> None:
    root, recipe = recipe_fixture
    data = load_recipe(recipe, root=root)
    manifest = json.loads((root / data["input_manifest"]).read_bytes())
    archive = tmp_path / "tampered.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for name in manifest["files"]:
            if name.startswith("data/raw/"):
                raw = (root / name).read_bytes()
                bundle.writestr(
                    name, b"x" * len(raw) if name.endswith("individuals.json") else raw
                )
    source = root / "data/raw/aph/individuals.json"
    original = source.read_bytes()
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        restore_recipe_inputs(recipe, archive=archive, root=root)
    assert source.read_bytes() == original


@pytest.mark.unit
def test_recipe_copies_review_state_before_ingestion(
    recipe_fixture: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, recipe = recipe_fixture
    configuration = load_recipe(recipe, root=root)
    working = root / configuration["decision_log"]
    reviewed = working.read_bytes()

    def frozen_build(
        *args: Any,
        decision_log_path: Path,
        release_recipe: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        assert decision_log_path != working
        assert decision_log_path.read_bytes() == reviewed
        working.write_bytes(b"")
        assert decision_log_path.read_bytes() == reviewed
        assert (
            release_recipe["decision_log_sha256"]
            == configuration["decision_log_sha256"]
        )
        return {"verified": {"valid": True}}

    monkeypatch.setattr("apemap.release.recipe.build_historical_release", frozen_build)
    result = build_recipe_release(
        recipe,
        tmp_path / "db.duckdb",
        tmp_path / "out",
        "0.4.0-rc.1",
        root=root,
        source_commit="a" * 40,
    )
    assert result["verified"]["valid"]


@pytest.mark.integration
@pytest.mark.slow
def test_local_and_cli_recipe_replay_match_offline(
    recipe_fixture: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, recipe = recipe_fixture

    def fixture_boundaries(*, conn: duckdb.DuckDBPyConnection, **kwargs: Any) -> None:
        # Source-native boundary readers have separate fixture coverage. Seed one
        # polygon here so strict release validation still exercises spatial joins.
        ensure_spatial(conn, allow_install=False)
        conn.execute(
            "INSERT INTO electoral_boundaries(boundary_id,electorate,state_or_territory,election_year,geometry,source_url,source_dataset,retrieved_at) VALUES ('fixture','Fixture Seat','VIC',2025,ST_GeomFromText('POLYGON((144 -38,146 -38,146 -36,144 -36,144 -38))'),'https://example.org/boundary','AEC fixture','2026-10-01T00:00:00Z')"
        )

    from apemap.db import migrate_historical_finances

    consumed_finance_paths: list[Path] = []

    def pinned_finances(conn: duckdb.DuckDBPyConnection, gpkg_path: Path) -> int:
        consumed_finance_paths.append(gpkg_path)
        assert gpkg_path == root / "data/aped.gpkg"
        return migrate_historical_finances(conn, gpkg_path)

    monkeypatch.setenv("APEMAP_OFFLINE", "1")
    monkeypatch.setattr("apemap.historical.run_aec_ingestion", fixture_boundaries)
    monkeypatch.setattr(
        "apemap.ingest.acara.migrate_historical_finances", pinned_finances
    )
    monkeypatch.setattr("apemap.release.recipe._get_git_commit", lambda: "a" * 40)
    local, ci = tmp_path / "local", tmp_path / "ci"
    build_recipe_release(
        recipe, tmp_path / "local.duckdb", local, "0.4.0-rc.1", root=root
    )
    monkeypatch.setattr(
        "apemap.release.recipe_cli.build_recipe_release",
        partial(build_recipe_release, root=root),
    )
    result = CliRunner().invoke(
        app,
        [
            "release",
            "recipe",
            "build",
            "--recipe",
            str(recipe),
            "--db-path",
            str(tmp_path / "ci.duckdb"),
            "--output-dir",
            str(ci),
            "--version",
            "0.4.0-rc.1",
        ],
    )
    assert result.exit_code == 0, result.output
    assert consumed_finance_paths == [root / "data/aped.gpkg"] * 2
    compare_recipe_releases(local, ci)
    manifest = json.loads((ci / "manifest.json").read_bytes())
    effective = manifest["release_recipe"]
    assert effective["parliaments"] == list(range(42, 49))
    assert effective["finance_year"] == 2024
    assert effective["acara_profile_years"] == list(range(2008, 2026))
    assert effective["profile_selection"] == "latest_available"
    assert (
        effective["decision_log_sha256"]
        == manifest["review_snapshot"]["decision_log_sha256"]
    )
    assert (
        effective["input_manifest_sha256"]
        == manifest["review_snapshot"]["source_manifest_sha256"]
    )
    assert effective["data_release_version"] == "0.4.0-rc.1"
    assert effective["package_version"] == "0.5.0"
    assert verify_release(ci, strict_assertions=True)["valid"]
    for path in ci.rglob("*.json"):
        assert b"\r\n" not in path.read_bytes()
    for path in (ci / "data").glob("*.csv"):
        assert b"\r\n" not in path.read_bytes()
    manifest["release_recipe"]["finance_year"] = 2021
    (ci / "manifest.json").write_text(json.dumps(manifest))
    assert not verify_release(ci, strict_assertions=True)["valid"]


@pytest.mark.unit
def test_workflow_uses_only_the_canonical_recipe() -> None:
    workflow = (PROJECT_ROOT / ".github/workflows/release.yml").read_text()
    assert "apemap release recipe build" in workflow
    assert "apemap release recipe inputs-restore" in workflow
    assert "apemap release recipe compare" in workflow
    assert "apemap.release.package" in workflow
    assert "--strict-assertions" in workflow
    assert "apemap run-all" not in workflow
    assert "finance-year" not in workflow
