"""One pinned historical release recipe for local builds and GitHub Actions."""

from __future__ import annotations

import gzip
import hashlib
from importlib.metadata import version as package_version
import json
from pathlib import Path
import re
import shutil
import sqlite3
import tarfile
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import urlparse

import pandas as pd

from apemap.constants import PROJECT_ROOT, supported_parliaments
from apemap.contracts import ANALYSIS_SCHEMA_VERSION, WEB_SCHEMA_VERSION
from apemap.export import _get_git_commit
from apemap.historical import build_historical_release
from apemap.ingest.http import create_retry_session
from apemap.inputs import compute_sha256, extract_inputs_archive, verify_inputs_manifest
from apemap.review.integration import read_review_events
from apemap.release.verify import verify_release


def compare_recipe_releases(first: Path, second: Path) -> None:
    """Require identical metadata and payload hashes after strict privacy verification."""
    for root in (first, second):
        report = verify_release(root, strict_assertions=True)
        if not report["valid"]:
            raise ValueError(
                f"Recipe comparison requires valid releases: {report['errors']}"
            )
    manifests = [
        json.loads((root / "manifest.json").read_bytes()) for root in (first, second)
    ]
    if not all(manifest.get("release_recipe") for manifest in manifests):
        raise ValueError("Recipe comparison requires recorded effective recipes")
    if manifests[0] != manifests[1]:
        raise ValueError(
            "Recipe replay differs: release metadata or deterministic artifact hashes changed"
        )


def _path(root: Path, name: str) -> Path:
    """Resolve recipe paths against the checkout, rejecting traversal and links."""
    path = (root / name).resolve()
    if not name or Path(name).is_absolute() or not path.is_relative_to(root.resolve()):
        raise ValueError(f"Recipe path must stay within the checkout: {name}")
    return path


def _check_hash(path: Path, digest: str) -> None:
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError(f"Invalid SHA-256 pin for {path.name}")
    if compute_sha256(path) != digest:
        raise ValueError(
            f"Recipe SHA-256 mismatch for {path.name}; use its pinned revision"
        )


def load_recipe(recipe_path: Path, *, root: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Require every analytical choice; unsupported vintages fail instead of falling back."""
    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    required = {
        "schema_version",
        "recipe",
        "parliaments",
        "cohort",
        "input_manifest",
        "input_manifest_sha256",
        "decision_log",
        "decision_log_sha256",
        "acara_input_dir",
        "acara_location_year",
        "acara_profile_years",
        "profile_selection",
        "finance_year",
        "finance_mode",
        "legacy_finance_input",
        "legacy_finance_input_sha256",
        "legacy_finance_year",
        "aec_year",
        "abs_year",
        "web_schema_version",
        "analysis_schema_version",
        "package_version",
    }
    if not isinstance(recipe, dict) or set(recipe) != required:
        raise ValueError(
            "Recipe must contain exactly the documented configuration fields"
        )
    supported = {
        "schema_version": 1,
        "recipe": "historical-v1",
        "cohort": "opening_day",
        "acara_location_year": 2025,
        "acara_profile_years": list(range(2008, 2026)),
        "profile_selection": "latest_available",
        "finance_year": 2024,
        "finance_mode": "historical_finance_and_public_funding",
        "legacy_finance_year": 2021,
        "aec_year": 2025,
        "abs_year": 2025,
        "web_schema_version": WEB_SCHEMA_VERSION,
        "analysis_schema_version": ANALYSIS_SCHEMA_VERSION,
        "package_version": package_version("apemap"),
    }
    for field, value in supported.items():
        if type(recipe[field]) is not type(value) or recipe[field] != value:
            raise ValueError(
                f"Unsupported recipe {field}: {recipe[field]!r}; expected {value!r}"
            )
    parliaments = recipe["parliaments"]
    if (
        not isinstance(parliaments, list)
        or not parliaments
        or any(
            type(p) is not int or p not in supported_parliaments() for p in parliaments
        )
        or sorted(set(parliaments)) != parliaments
    ):
        raise ValueError("Recipe parliaments must be a sorted, unique supported cohort")
    for key in (
        "input_manifest",
        "decision_log",
        "acara_input_dir",
        "legacy_finance_input",
    ):
        if not isinstance(recipe[key], str):
            raise ValueError(f"Recipe {key} must be a relative path")
        _path(root, recipe[key])
    for key in ("input_manifest", "decision_log", "legacy_finance_input"):
        _check_hash(_path(root, recipe[key]), recipe[f"{key}_sha256"])
    read_review_events(_path(root, recipe["decision_log"]))
    finance_path = _path(root, recipe["legacy_finance_input"])
    with sqlite3.connect(finance_path.as_uri() + "?mode=ro", uri=True) as finance:
        finance_years = finance.execute(
            "SELECT DISTINCT year FROM acara_education_finances"
        ).fetchall()
    if finance_years != [(recipe["legacy_finance_year"],)]:
        raise ValueError(
            "Pinned legacy finance rows do not match the recipe's explicit year"
        )
    manifest = json.loads(_path(root, recipe["input_manifest"]).read_bytes())
    files = manifest["files"]
    expected = {
        "data/raw/aph/individuals.json",
        *(
            f"{recipe['acara_input_dir']}/{name}"
            for name in (
                "acara_school_results.json",
                "school-location-2025.csv",
                "school-profile-2008-2025.csv",
            )
        ),
        *(
            f"data/raw/aec/2025/AUS_ELB_region.{suffix}"
            for suffix in ("shp", "shx", "dbf", "prj")
        ),
        *(
            f"data/reference/{name}.csv"
            for name in (
                "acara_school_finance_benchmarks",
                "manual_school_funding",
                "nsw_ram_allocations",
                "nt_school_funding",
                "qld_non_state_grants",
                "tasmania_srp_allocations",
            )
        ),
    }
    for name in expected:
        entry = files.get(name, {})
        if not entry.get("required") or not re.fullmatch(
            r"[0-9a-f]{64}", entry.get("sha256", "")
        ):
            raise ValueError(f"Recipe input manifest must pin required source: {name}")
    return recipe


def pin_recipe(template: Path, output: Path, *, root: Path = PROJECT_ROOT) -> None:
    """Freeze new reviewed bytes without changing the template or historical records."""
    recipe = json.loads(template.read_text(encoding="utf-8"))
    recipe["package_version"] = package_version("apemap")
    for key in ("input_manifest", "decision_log", "legacy_finance_input"):
        recipe[f"{key}_sha256"] = compute_sha256(_path(root, recipe[key]))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="\n") as target:
        target.write(json.dumps(recipe, indent=2, sort_keys=True) + "\n")
    load_recipe(output, root=root)


def _raw_files(recipe: dict[str, Any], root: Path) -> dict[str, Any]:
    manifest = json.loads(_path(root, recipe["input_manifest"]).read_bytes())
    return {
        name: entry
        for name, entry in manifest["files"].items()
        if name.startswith("data/raw/") and entry.get("required", True)
    }


def bundle_recipe_inputs(
    recipe_path: Path, archive: Path, *, root: Path = PROJECT_ROOT
) -> None:
    """Bundle exact raw inputs for provisioning; never publish or overwrite an asset."""
    recipe = load_recipe(recipe_path, root=root)
    valid, errors = verify_inputs_manifest(
        _path(root, recipe["input_manifest"]), base_dir=root
    )
    if not valid:
        raise ValueError(f"Recipe inputs are invalid: {errors}")
    archive.parent.mkdir(parents=True, exist_ok=True)
    with archive.open("xb") as raw:
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as compressed:
            with tarfile.open(
                fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT
            ) as bundle:
                for name in sorted(_raw_files(recipe, root)):
                    source = _path(root, name)
                    info = tarfile.TarInfo(name)
                    info.size, info.mtime, info.mode = source.stat().st_size, 0, 0o644
                    with source.open("rb") as content:
                        bundle.addfile(info, content)


def restore_recipe_inputs(
    recipe_path: Path,
    *,
    archive: Path | None = None,
    archive_url: str | None = None,
    root: Path = PROJECT_ROOT,
) -> None:
    """Restore a supplied bundle after validating its complete per-file hash inventory."""
    recipe = load_recipe(recipe_path, root=root)
    if (archive is None) == (archive_url is None):
        raise ValueError("Supply exactly one input --archive or --archive-url")
    raw_files = _raw_files(recipe, root)
    # Validate targets before extraction/copy, including symlink escapes.
    targets = {name: _path(root, name) for name in raw_files}
    with TemporaryDirectory(prefix="apemap-recipe-inputs-") as temp:
        staging = Path(temp).resolve()
        if archive_url is not None:
            if urlparse(archive_url).scheme != "https":
                raise ValueError("Recipe input archive URL must use HTTPS")
            archive = staging / "inputs.tar.gz"
            with create_retry_session() as session:
                with session.get(
                    archive_url, stream=True, timeout=(10, 120)
                ) as response:
                    response.raise_for_status()
                    with archive.open("wb") as output:
                        for chunk in response.iter_content(chunk_size=65536):
                            output.write(chunk)
        assert archive is not None
        extracted_root = staging / "extracted"
        extracted = extract_inputs_archive(archive, extracted_root)
        if {p.relative_to(extracted_root).as_posix() for p in extracted} != set(
            raw_files
        ):
            raise ValueError(
                "Recipe archive inventory differs from the pinned raw inputs"
            )
        for name, entry in raw_files.items():
            source = extracted_root / name
            if source.stat().st_size != entry["size_bytes"]:
                raise ValueError(f"Recipe input size mismatch: {name}")
            _check_hash(source, entry["sha256"])
        for name, target in targets.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(extracted_root / name, target)
    valid, errors = verify_inputs_manifest(
        _path(root, recipe["input_manifest"]), base_dir=root
    )
    if not valid:
        raise ValueError(f"Restored recipe inputs are invalid: {errors}")


def build_recipe_release(
    recipe_path: Path,
    db_path: Path,
    output_dir: Path,
    version: str,
    *,
    root: Path = PROJECT_ROOT,
    source_commit: str | None = None,
) -> dict[str, Any]:
    """Execute the same strictly verified, offline historical build locally and in CI."""
    if not re.fullmatch(
        r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?", version
    ):
        raise ValueError(
            "Dataset version must be Semantic Versioning, optionally a prerelease"
        )
    recipe = load_recipe(recipe_path, root=root)
    manifest_path = _path(root, recipe["input_manifest"])
    manifest = json.loads(manifest_path.read_bytes())
    valid, errors = verify_inputs_manifest(manifest_path, base_dir=root)
    if not valid:
        raise ValueError(f"Recipe input verification failed: {errors}")
    input_dir = _path(root, recipe["acara_input_dir"])
    expected_names = {
        "acara_school_results.json",
        "school-location-2025.csv",
        "school-profile-2008-2025.csv",
    }
    actual_names = {
        p.name for p in input_dir.iterdir() if p.suffix in (".csv", ".json")
    }
    if actual_names != expected_names:
        raise ValueError(
            "ACARA directory must contain only the recipe's pinned CSV/JSON inputs"
        )
    years = pd.read_csv(
        input_dir / "school-profile-2008-2025.csv",
        usecols=["Calendar Year"],
        dtype={"Calendar Year": "Int64"},
    )["Calendar Year"]
    if (
        years.isna().any()
        or sorted(years.unique().tolist()) != recipe["acara_profile_years"]
    ):
        raise ValueError(
            "Pinned profile rows do not match the recipe's explicit ACARA years"
        )
    effective_recipe = {
        **recipe,
        "data_release_version": version,
        "source_commit": source_commit or _get_git_commit(),
        "generated_at": manifest["created_at"],
        "recipe_sha256": hashlib.sha256(recipe_path.read_bytes()).hexdigest(),
    }
    # Preserve exact ledger bytes throughout both ingestion stages, even if a
    # reviewer appends to the working log concurrently. Snapshots contain no temp path.
    with TemporaryDirectory(prefix="apemap-release-review-") as temp:
        ledger = Path(temp) / "decisions.jsonl"
        ledger.write_bytes(_path(root, recipe["decision_log"]).read_bytes())
        _check_hash(ledger, recipe["decision_log_sha256"])
        result = build_historical_release(
            db_path,
            output_dir,
            input_dir,
            manifest_path,
            version,
            parliaments=recipe["parliaments"],
            decision_log_path=ledger,
            finance_year=recipe["finance_year"],
            legacy_finance_path=_path(root, recipe["legacy_finance_input"]),
            release_recipe=effective_recipe,
            source_commit=effective_recipe["source_commit"],
            project_root=root,
        )
    return result
