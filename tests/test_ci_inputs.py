"""CI provisions closed-school identities without changing research source pins."""

from __future__ import annotations

import csv
import json
from pathlib import Path
import shutil
from unittest.mock import Mock

import openpyxl
import pytest

from apemap.constants import PROJECT_ROOT
from apemap.ingest.acara import convert_xlsx_to_csv
from apemap.ingest.matching import SchoolMatcher
from apemap.inputs import compute_sha256, verify_inputs_manifest
from apemap.release.ci import prepare_ci_inputs
from apemap.release.recipe import load_recipe
from apemap.review.model import validate_events
from tests.test_release_recipe import recipe_fixture as recipe_fixture
from tests.test_review_integration import event

pytestmark = pytest.mark.unit


@pytest.fixture
def ci_checkout(
    recipe_fixture: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Mock]:
    root, recipe = recipe_fixture
    template_path = root / "data/release-recipes/historical.json"
    template_path.parent.mkdir(parents=True)
    shutil.copyfile(recipe, template_path)
    historical_path = root / "data/historical-inputs-manifest.json"
    historical = json.loads(historical_path.read_bytes())
    historical["licensing"] = "Synthetic fixture"
    register = root / "data/raw/historical/acara"
    profile = register / "school-profile-2008-2025.csv"
    with profile.open("a", encoding="utf-8", newline="") as output:
        output.write(
            "40335,Alice Springs High School,Government,Secondary,NT,2008,100\n"
        )
    for workbook, csv_name in (
        ("School Location 2025.xlsx", "school-location-2025.csv"),
        ("School Profile 2008-2025.xlsx", "school-profile-2008-2025.csv"),
    ):
        source, converted = register / workbook, register / csv_name
        book = openpyxl.Workbook()
        sheet = book.active
        assert sheet is not None
        with converted.open(encoding="utf-8", newline="") as rows:
            for row in csv.reader(rows):
                sheet.append(row)
        book.save(source)
        book.close()
        convert_xlsx_to_csv(source, converted, force=True)
        for path in (source, converted):
            historical["files"][path.relative_to(root).as_posix()] = {
                "size_bytes": path.stat().st_size,
                "sha256": compute_sha256(path),
                "required": True,
                "source": f"https://example.org/{workbook}",
            }
    historical_path.write_text(json.dumps(historical), encoding="utf-8")
    tracked = root / "data/external/acara_school_results.json"
    tracked.parent.mkdir(parents=True)
    tracked.write_bytes(b"[]")
    base = {
        **historical,
        "files": {
            key: value
            for key, value in historical["files"].items()
            if "raw/historical/acara" not in key
        },
    }
    base["files"][tracked.relative_to(root).as_posix()] = {
        "size_bytes": 2,
        "sha256": compute_sha256(tracked),
        "source": "Synthetic tracked 2022 cache",
        "required": True,
    }
    (root / "data/inputs-manifest.json").write_text(json.dumps(base), encoding="utf-8")
    monkeypatch.setattr("apemap.release.ci.restore_inputs", Mock())

    def download(url: str, destination: Path) -> Path:
        shutil.copyfile(register / destination.name, destination)
        return destination

    downloader = Mock(side_effect=download)
    monkeypatch.setattr("apemap.release.ci.download_acara_dataset", downloader)
    return root, downloader


def test_ci_recipe_validates_historical_review_and_preserves_sources(
    ci_checkout: tuple[Path, Mock],
) -> None:
    root, downloader = ci_checkout
    preserved = {
        path: path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and "raw/ci" not in path.as_posix()
    }
    recipe = load_recipe(prepare_ci_inputs(root=root), root=root)
    assert downloader.call_count == 2
    assert verify_inputs_manifest(root / recipe["input_manifest"], base_dir=root) == (
        True,
        [],
    )
    matcher = SchoolMatcher(external_dir=root / recipe["acara_input_dir"])
    assert matcher.acara_id_map["40335"]["school_name"] == "Alice Springs High School"
    assert "40335" not in matcher.current_ids
    validate_events(
        [
            event(
                "school",
                {
                    "recorded_name": "Alice Springs High School",
                    "institution_ref": "acara:40335",
                    "relationship_type": "direct",
                },
            )
        ],
        acara_ids=set(matcher.acara_id_map),
    )
    assert recipe["decision_log_sha256"] == compute_sha256(
        root / recipe["decision_log"]
    )
    assert all(path.read_bytes() == original for path, original in preserved.items())


@pytest.mark.parametrize("corrupt", ["workbook", "csv", "tracked"])
def test_ci_provisioning_rejects_changed_input_bytes(
    ci_checkout: tuple[Path, Mock], monkeypatch: pytest.MonkeyPatch, corrupt: str
) -> None:
    root, downloader = ci_checkout
    if corrupt == "workbook":
        downloader.side_effect = lambda url, path: path.write_bytes(b"changed source")
    elif corrupt == "csv":
        monkeypatch.setattr(
            "apemap.release.ci.convert_xlsx_to_csv",
            lambda source, target: target.write_bytes(b"changed conversion"),
        )
    else:
        (root / "data/external/acara_school_results.json").write_bytes(b"changed cache")
    with pytest.raises(ValueError, match="input.*(differs|verification failed)"):
        prepare_ci_inputs(root=root)
    assert not (root / "data/raw/ci/recipe.json").exists()
    if corrupt == "tracked":
        downloader.assert_not_called()


def test_ci_provisioning_refuses_to_replace_a_pinned_recipe(
    ci_checkout: tuple[Path, Mock],
) -> None:
    root, downloader = ci_checkout
    recipe_path = prepare_ci_inputs(root=root)
    original = recipe_path.read_bytes()
    downloader.reset_mock()
    with pytest.raises(FileExistsError, match="fresh CI output paths"):
        prepare_ci_inputs(root=root)
    assert recipe_path.read_bytes() == original
    downloader.assert_not_called()


def test_ci_workflow_uses_provisioned_register_and_offline_recipes() -> None:
    workflow = (PROJECT_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert workflow.count("uv run python -m apemap.release.ci") == 3
    assert workflow.count("apemap review --external-dir data/raw/ci/acara check") == 2
    assert workflow.count("--recipe data/raw/ci/recipe.json") == 2
    assert "apemap run-all" not in workflow
    assert "--strict-assertions" in workflow
