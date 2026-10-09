"""Malformed recipe provenance produces verification errors, not exceptions."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from apemap.release.verify import verify_release


@pytest.mark.unit
@pytest.mark.parametrize("invalid_field", ["web_metadata", "review_snapshot"])
def test_recipe_verification_rejects_non_object_provenance(
    invalid_field: str, tmp_path: Path
) -> None:
    recipe = {"data_release_version": "0.5.0", "parliaments": []}
    web_metadata: dict[str, object] | list[object] = {
        "release_recipe": recipe,
        "cohort": "opening_day",
        "temporal_warning": "Fixture historical context",
        "parliament_metadata": {},
    }
    if invalid_field == "web_metadata":
        web_metadata = []
    web_dir = tmp_path / "web"
    web_dir.mkdir()
    raw = json.dumps(web_metadata).encode()
    (web_dir / "metadata.json").write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    manifest = {
        "data_release_version": "0.5.0",
        "release_recipe": recipe,
        "review_snapshot": None if invalid_field == "review_snapshot" else {},
        "supported_parliaments": [],
        "parliaments": [],
        "parliament_metadata": {},
        "files": {"web/metadata.json": {"sha256": digest, "size_bytes": len(raw)}},
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "SHA256SUMS").write_text(
        f"{digest}  web/metadata.json\n", encoding="utf-8"
    )

    result = verify_release(tmp_path, strict_assertions=True)

    assert not result["valid"]
    assert any(
        (
            "Web metadata must be a JSON object"
            if invalid_field == "web_metadata"
            else "Release recipe review snapshot must be an object"
        )
        in error
        for error in result["errors"]
    )
