"""Verification engine for released dataset artifacts."""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Any

from apemap.export import _file_sha256
from apemap.contracts import WEB_SCHEMA_VERSION
from apemap.release.build import RESTRICTED_FINANCE_COLUMNS

logger = logging.getLogger(__name__)


def verify_release(
    release_dir: Path | str, strict_assertions: bool = False
) -> dict[str, Any]:
    """Execute rigorous read-back verification on a release directory.

    Checks:
    - Existence of manifest.json and SHA256SUMS.
    - Full bidirectional inventory matching between manifest, checksums, and disk.
    - File size and cryptographic hash integrity for every artifact.
    - Valid GeoJSON structure and geographical coordinate bounds.
    - Complete absence of restricted private finance fields in public data and web layers.
    - Positive validation gate assertions.

    Args:
        release_dir: Root release directory to verify.

    Returns:
        Structured dictionary reporting validation results and any errors.
    """
    root = Path(release_dir).resolve()
    errors: list[str] = []
    checks_run = 0

    checks_run += 1
    if not root.exists() or not root.is_dir():
        return {
            "valid": False,
            "release_version": None,
            "checks_run": checks_run,
            "checks_passed": 0,
            "verified_files_count": 0,
            "errors": [
                f"Release directory does not exist or is not a directory: {root}"
            ],
        }

    manifest_path = root / "manifest.json"
    sha256sums_path = root / "SHA256SUMS"

    checks_run += 1
    if not manifest_path.exists():
        errors.append(f"Missing required release manifest: {manifest_path}")

    checks_run += 1
    if not sha256sums_path.exists():
        errors.append(f"Missing required release checksums: {sha256sums_path}")

    if errors:
        return {
            "valid": False,
            "release_version": None,
            "checks_run": checks_run,
            "checks_passed": checks_run - len(errors),
            "verified_files_count": 0,
            "errors": errors,
        }

    # 1. Parse manifest.json
    checks_run += 1
    try:
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as e:
        errors.append(f"Failed to parse manifest JSON: {e}")
        manifest_data = {}

    manifest_files = manifest_data.get("files", {})
    version = manifest_data.get("data_release_version")

    recipe = manifest_data.get("release_recipe")
    if recipe is not None:
        checks_run += 1
        try:
            web_metadata = json.loads(
                (root / "web/metadata.json").read_text(encoding="utf-8")
            )
            if not isinstance(web_metadata, dict):
                raise ValueError("Web metadata must be a JSON object")
            web_recipe = web_metadata.get("release_recipe")
            if not isinstance(recipe, dict) or web_recipe != recipe:
                errors.append("Web and release recipe metadata disagree")
            else:
                for field in (
                    "data_release_version",
                    "source_commit",
                    "generated_at",
                    "parliaments",
                    "web_schema_version",
                ):
                    if recipe.get(field) != manifest_data.get(field):
                        errors.append(f"Release recipe {field} disagrees with manifest")
                snapshot = manifest_data.get("review_snapshot", {})
                if not isinstance(snapshot, dict):
                    errors.append("Release recipe review snapshot must be an object")
                else:
                    if recipe.get("decision_log_sha256") != snapshot.get(
                        "decision_log_sha256"
                    ):
                        errors.append("Release recipe review revision was not consumed")
                    if recipe.get("input_manifest_sha256") != snapshot.get(
                        "source_manifest_sha256"
                    ):
                        errors.append("Release recipe source manifest was not consumed")
                    if recipe.get("evidence_log_sha256") != snapshot.get(
                        "evidence_log_sha256"
                    ):
                        errors.append(
                            "Release recipe evidence revision was not consumed"
                        )
        except (OSError, ValueError) as exc:
            errors.append(f"Cannot read release recipe metadata: {exc}")

    snapshot = manifest_data.get("review_snapshot", {})
    if isinstance(snapshot, dict) and "evidence_log_sha256" in snapshot:
        checks_run += 1
        retained = root / "review/evidence.jsonl"
        if not retained.is_file():
            errors.append("Release is missing its retained review evidence")
        elif _file_sha256(retained) != snapshot["evidence_log_sha256"]:
            errors.append("Released evidence differs from the consumed review snapshot")

    # Historical releases declare centrally configured terms and one public layer each.
    if "supported_parliaments" in manifest_data:
        checks_run += 1
        from apemap.constants import PARLIAMENT_METADATA

        selected = manifest_data.get("parliaments", [])
        metadata = manifest_data.get("parliament_metadata", {})
        for p in selected:
            if metadata.get(str(p)) != PARLIAMENT_METADATA.get(p):
                errors.append(
                    f"Parliament {p} metadata differs from canonical chronology"
                )
            if not (root / "web" / f"parliament_{p}_combined.geojson").exists():
                errors.append(f"Missing public layer for Parliament {p}")
        metadata_path = root / "web" / "metadata.json"
        try:
            web_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if not isinstance(web_metadata, dict):
                raise ValueError("Web metadata must be a JSON object")
            if web_metadata.get("parliament_metadata") != metadata:
                errors.append("Web and release parliament metadata disagree")
            if web_metadata.get("cohort") != "opening_day" or not web_metadata.get(
                "temporal_warning"
            ):
                errors.append("Historical release lacks its cohort or temporal warning")
        except (OSError, ValueError) as exc:
            errors.append(f"Cannot read historical web metadata: {exc}")

    # 2. Parse SHA256SUMS
    checks_run += 1
    sums_map: dict[str, str] = {}
    try:
        for line in sha256sums_path.read_text(encoding="utf-8").splitlines():
            line_str = line.strip()
            if not line_str:
                continue
            parts = line_str.split(None, 1)
            if len(parts) == 2:
                h, p = parts
                sums_map[p.replace("\\", "/")] = h.lower()
    except Exception as e:
        errors.append(f"Failed to parse SHA256SUMS: {e}")

    # 3. Check every file in manifest against disk and SHA256SUMS
    verified_files = 0
    for rel_path, meta in manifest_files.items():
        checks_run += 1
        fpath = root / rel_path
        if not fpath.exists():
            errors.append(f"Manifested file missing on disk: '{rel_path}'")
            continue

        checks_run += 1
        expected_size = meta.get("size_bytes")
        actual_size = fpath.stat().st_size
        if expected_size is not None and actual_size != expected_size:
            errors.append(
                f"File size mismatch for '{rel_path}': expected {expected_size} bytes, got {actual_size} bytes"
            )

        checks_run += 1
        expected_sha = meta.get("sha256", "").lower()
        actual_sha = _file_sha256(fpath).lower()
        if actual_sha != expected_sha:
            errors.append(
                f"Hash mismatch for '{rel_path}': expected {expected_sha}, got {actual_sha}"
            )

        checks_run += 1
        sums_sha = sums_map.get(rel_path)
        if sums_sha is None:
            errors.append(f"File '{rel_path}' missing from SHA256SUMS")
        elif sums_sha != expected_sha:
            errors.append(
                f"SHA256SUMS mismatch for '{rel_path}': sums has {sums_sha}, manifest has {expected_sha}"
            )

        verified_files += 1

    # 4. Reverse Inventory Check: No stray files on disk
    checks_run += 1
    on_disk_files = {
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and p.name not in ("manifest.json", "SHA256SUMS")
    }
    unmanifested = on_disk_files - set(manifest_files.keys())
    if unmanifested:
        for u in sorted(unmanifested):
            errors.append(f"Unmanifested file found on disk: '{u}'")

    # 5. GeoJSON Coordinate and Format Validation
    for geojson_path in sorted((root / "web").glob("*.geojson")):
        checks_run += 1
        try:
            gj_data = json.loads(geojson_path.read_text(encoding="utf-8"))
            if gj_data.get("type") != "FeatureCollection":
                errors.append(
                    f"{geojson_path.name} type is '{gj_data.get('type')}', expected 'FeatureCollection'"
                )
            features = gj_data.get("features", [])
            if not features and gj_data.get("web_schema_version") != WEB_SCHEMA_VERSION:
                errors.append(f"{geojson_path.name} features list is empty")
            for idx, feat in enumerate(features):
                if gj_data.get("web_schema_version") == WEB_SCHEMA_VERSION:
                    from apemap.explorer_contract import audit_school_context

                    errors.extend(
                        audit_school_context(
                            feat.get("properties", {}),
                            f"{geojson_path.name} feature {idx}",
                        )
                    )
                geom = feat.get("geometry")
                if not geom or geom.get("type") != "Point":
                    errors.append(
                        f"{geojson_path.name} feature {idx} missing Point geometry"
                    )
                    continue
                coords = geom.get("coordinates")
                if not coords or len(coords) < 2:
                    errors.append(
                        f"{geojson_path.name} feature {idx} has invalid coordinates: {coords}"
                    )
                    continue
                lon, lat = float(coords[0]), float(coords[1])
                # Global coordinate validity
                if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
                    errors.append(
                        f"{geojson_path.name} feature {idx} out of range coordinates: ({lon}, {lat})"
                    )
        except Exception as e:
            errors.append(f"Failed parsing {geojson_path.name}: {e}")

    # 6. Restricted Field Leakage Audit
    checks_run += 1
    for rel_path in manifest_files:
        if not (rel_path.startswith("data/") or rel_path.startswith("web/")):
            continue

        fpath = root / rel_path
        if rel_path.endswith(".csv"):
            try:
                with fpath.open("r", encoding="utf-8") as f:
                    reader = csv.reader(f)
                    header = next(reader, [])
                    leaked = set(header) & RESTRICTED_FINANCE_COLUMNS
                    if leaked:
                        errors.append(
                            f"Restricted finance column(s) {sorted(leaked)} leaked into public CSV: '{rel_path}'"
                        )
            except Exception as e:
                errors.append(f"Failed reading CSV header for '{rel_path}': {e}")
        elif rel_path.endswith(".parquet"):
            try:
                import duckdb

                temp_c = duckdb.connect(":memory:")
                cols = [
                    row[0]
                    for row in temp_c.execute(
                        "DESCRIBE SELECT * FROM read_parquet(?)", [str(fpath)]
                    ).fetchall()
                ]
                temp_c.close()
                leaked = set(cols) & RESTRICTED_FINANCE_COLUMNS
                if leaked:
                    errors.append(
                        f"Restricted finance column(s) {sorted(leaked)} leaked into public Parquet: '{rel_path}'"
                    )
            except Exception as e:
                errors.append(f"Failed reading Parquet schema for '{rel_path}': {e}")

    # 7. Validation Assertions Gate
    assertions_path = root / "web" / "assertions.json"
    if assertions_path.exists():
        checks_run += 1
        try:
            assert_data = json.loads(assertions_path.read_text(encoding="utf-8"))
            if strict_assertions and not assert_data.get("passed", False):
                errors.append(
                    "Release assertions in web/assertions.json report failed database validation."
                )
        except Exception as e:
            errors.append(f"Failed reading web/assertions.json: {e}")

    metadata_path = root / "web/metadata.json"
    if metadata_path.exists():
        try:
            web_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if not isinstance(web_metadata, dict):
                raise ValueError("Web metadata must be a JSON object")
            if web_metadata.get("web_schema_version") == WEB_SCHEMA_VERSION:
                from apemap.explorer_contract import audit_explorer_contract

                checks_run += 1
                errors.extend(audit_explorer_contract(root)["errors"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append(f"Cannot reconcile v2 web semantics: {exc}")

    return {
        "valid": len(errors) == 0,
        "release_version": version,
        "checks_run": checks_run,
        "checks_passed": checks_run - len(errors),
        "verified_files_count": verified_files,
        "errors": errors,
    }
