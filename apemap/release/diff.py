"""Comparative analysis and diffing between dataset releases."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def diff_releases(
    old_release_dir: Path | str,
    new_release_dir: Path | str,
) -> dict[str, Any]:
    """Compare two releases and produce a detailed structural, inventory, and metric diff.

    Args:
        old_release_dir: Directory containing baseline release artifacts and manifest.json.
        new_release_dir: Directory containing target release artifacts and manifest.json.

    Returns:
        Structured dictionary reporting added, removed, modified, and unchanged files,
        size deltas, and metric variations.
    """
    old_root = Path(old_release_dir).resolve()
    new_root = Path(new_release_dir).resolve()

    old_manifest_path = old_root / "manifest.json"
    new_manifest_path = new_root / "manifest.json"

    if not old_manifest_path.exists():
        raise FileNotFoundError(f"Old release manifest not found: {old_manifest_path}")
    if not new_manifest_path.exists():
        raise FileNotFoundError(f"New release manifest not found: {new_manifest_path}")

    old_manifest = json.loads(old_manifest_path.read_text(encoding="utf-8"))
    new_manifest = json.loads(new_manifest_path.read_text(encoding="utf-8"))

    old_files: dict[str, dict[str, Any]] = old_manifest.get("files", {})
    new_files: dict[str, dict[str, Any]] = new_manifest.get("files", {})

    old_keys = set(old_files.keys())
    new_keys = set(new_files.keys())

    added_files = sorted(new_keys - old_keys)
    removed_files = sorted(old_keys - new_keys)
    common_keys = old_keys & new_keys

    modified_files: list[dict[str, Any]] = []
    unchanged_files: list[str] = []

    for k in sorted(common_keys):
        o_meta = old_files[k]
        n_meta = new_files[k]
        if o_meta.get("sha256") != n_meta.get("sha256") or o_meta.get(
            "size_bytes"
        ) != n_meta.get("size_bytes"):
            modified_files.append(
                {
                    "path": k,
                    "old_sha256": o_meta.get("sha256"),
                    "new_sha256": n_meta.get("sha256"),
                    "old_size_bytes": o_meta.get("size_bytes"),
                    "new_size_bytes": n_meta.get("size_bytes"),
                    "size_delta_bytes": (n_meta.get("size_bytes") or 0)
                    - (o_meta.get("size_bytes") or 0),
                }
            )
        else:
            unchanged_files.append(k)

    old_total_size = sum(f.get("size_bytes", 0) for f in old_files.values())
    new_total_size = sum(f.get("size_bytes", 0) for f in new_files.values())

    # Metric Comparisons (if results-summary.json exists in both)
    summary_diff: dict[str, Any] = {}
    old_summary_path = old_root / "web" / "results-summary.json"
    new_summary_path = new_root / "web" / "results-summary.json"
    if old_summary_path.exists() and new_summary_path.exists():
        try:
            o_sum = json.loads(old_summary_path.read_text(encoding="utf-8"))
            n_sum = json.loads(new_summary_path.read_text(encoding="utf-8"))
            summary_diff = _diff_summaries(o_sum, n_sum)
        except Exception as e:
            logger.debug("Failed diffing results-summary.json: %s", e)

    return {
        "old_version": old_manifest.get("data_release_version"),
        "new_version": new_manifest.get("data_release_version"),
        "old_commit": old_manifest.get("source_commit"),
        "new_commit": new_manifest.get("source_commit"),
        "added_files": added_files,
        "removed_files": removed_files,
        "modified_files": modified_files,
        "unchanged_files": unchanged_files,
        "old_total_bytes": old_total_size,
        "new_total_bytes": new_total_size,
        "size_delta_bytes": new_total_size - old_total_size,
        "summary_diff": summary_diff,
    }


def _diff_summaries(old_sum: dict[str, Any], new_sum: dict[str, Any]) -> dict[str, Any]:
    """Calculate differences in high-level metrics across parliaments."""
    diff: dict[str, Any] = {}
    o_parls = old_sum.get("parliaments", {})
    n_parls = new_sum.get("parliaments", {})

    all_p_keys = set(o_parls.keys()) | set(n_parls.keys())
    for p_key in sorted(all_p_keys):
        o_p = o_parls.get(p_key, {})
        n_p = n_parls.get(p_key, {})
        if not o_p or not n_p:
            continue

        o_tot = o_p.get("total_parliamentarians", 0)
        n_tot = n_p.get("total_parliamentarians", 0)
        o_known = o_p.get("parliamentarians_with_known_schools", 0)
        n_known = n_p.get("parliamentarians_with_known_schools", 0)

        diff[p_key] = {
            "total_parliamentarians_delta": n_tot - o_tot,
            "known_schools_delta": n_known - o_known,
        }

    return diff
