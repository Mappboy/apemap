"""Provision pinned longitudinal inputs and a fresh recipe for CI.

Run online during setup, then use the generated recipe for offline validation.
Historical manifests, recipes, external sources and review events stay untouched.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import shutil
from typing import Any

from apemap.constants import PROJECT_ROOT
from apemap.ingest.acara import convert_xlsx_to_csv, download_acara_dataset
from apemap.inputs import compute_sha256, restore_inputs, verify_inputs_manifest
from apemap.release.recipe import pin_recipe


def _verify(path: Path, entry: dict[str, Any]) -> None:
    if (
        path.stat().st_size != entry["size_bytes"]
        or compute_sha256(path) != entry["sha256"]
    ):
        raise ValueError(f"Pinned CI input differs: {path.name}")


def prepare_ci_inputs(*, root: Path = PROJECT_ROOT) -> Path:
    """Restore upstream pins and freeze current review bytes into an ignored recipe."""
    root = root.resolve()
    base_path = root / "data/inputs-manifest.json"
    restore_inputs(base_path, base_dir=root)
    valid, errors = verify_inputs_manifest(base_path, base_dir=root)
    if not valid:
        raise ValueError(f"Standard CI input verification failed: {errors}")
    base = json.loads(base_path.read_bytes())
    historical = json.loads(
        (root / "data/historical-inputs-manifest.json").read_bytes()
    )
    template = json.loads((root / "data/release-recipes/historical.json").read_bytes())
    staging = root / "data/raw/ci"
    recipe_path = staging / "recipe.json"
    if recipe_path.exists():
        raise FileExistsError(f"Use fresh CI output paths: {recipe_path}")
    input_dir = staging / "acara"
    input_dir.mkdir(parents=True, exist_ok=True)
    files = dict(base["files"])
    for workbook, csv_name in (
        ("School Location 2025.xlsx", "school-location-2025.csv"),
        ("School Profile 2008-2025.xlsx", "school-profile-2008-2025.csv"),
    ):
        entries = {
            name: historical["files"][f"{template['acara_input_dir']}/{name}"]
            for name in (workbook, csv_name)
        }
        source = input_dir / workbook
        download_acara_dataset(entries[workbook]["source"], source)
        _verify(source, entries[workbook])
        converted = input_dir / csv_name
        convert_xlsx_to_csv(source, converted)
        _verify(converted, entries[csv_name])
        for name, entry in entries.items():
            files[(input_dir / name).relative_to(root).as_posix()] = entry
    # The standard archive does not contain the old historical JSON cache.
    # Use the separately verified, tracked 2022 cache, recording its own pin.
    # The longitudinal profiles supply closed schools absent from that cache.
    json_name = "data/external/acara_school_results.json"
    shutil.copyfile(root / json_name, input_dir / "acara_school_results.json")
    files[(input_dir / "acara_school_results.json").relative_to(root).as_posix()] = (
        base["files"][json_name]
    )
    manifest_path = staging / "inputs-manifest.json"
    manifest = {
        "schema_version": "1.0",
        "created_at": historical["created_at"],
        "description": "CI: standard pinned APH/AEC and 2022 cache with pinned ACARA 2008–2025 profiles and 2025 locations",
        "licensing": historical["licensing"],
        "files": files,
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    valid, errors = verify_inputs_manifest(manifest_path, base_dir=root)
    if not valid:
        raise ValueError(f"Longitudinal CI input verification failed: {errors}")
    template["input_manifest"] = manifest_path.relative_to(root).as_posix()
    template["acara_input_dir"] = input_dir.relative_to(root).as_posix()
    template_path = staging / "template.json"
    template_path.write_text(
        json.dumps(template, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    pin_recipe(template_path, recipe_path, root=root)
    return recipe_path


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    print("Provisioning pinned CI inputs and current review recipe...", flush=True)
    print(f"Prepared pinned CI recipe: {prepare_ci_inputs()}", flush=True)
