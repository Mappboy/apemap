"""Offline input manifest verification, safe archive extraction, and offline preflight."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
import tarfile
from typing import Any
import zipfile

from apemap.constants import DATA_DIR

logger = logging.getLogger(__name__)

INPUTS_MANIFEST_SCHEMA_VERSION = "1.0"
DEFAULT_MANIFEST_PATH = DATA_DIR / "inputs-manifest.json"


def compute_sha256(path: Path | str) -> str:
    """Compute the SHA-256 hex digest of a file in streaming 64 KiB chunks."""
    target = Path(path).resolve()
    hasher = hashlib.sha256()
    with target.open("rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def is_safe_relative_path(path_str: str) -> bool:
    """Validate that a relative path does not escape the target directory (path traversal check).

    Rejects:
    - Absolute paths (starting with / or \\)
    - Windows drive prefixes (e.g. C:)
    - Paths containing '..' components
    """
    if not path_str or not isinstance(path_str, str):
        return False

    cleaned = path_str.strip().replace("\\", "/")
    if cleaned.startswith("/"):
        return False
    if len(cleaned) >= 2 and cleaned[1] == ":":
        return False

    parts = [p for p in cleaned.split("/") if p and p != "."]
    if ".." in parts:
        return False

    return True


def verify_inputs_manifest(
    manifest_path: Path | str,
    base_dir: Path | str | None = None,
) -> tuple[bool, list[str]]:
    """Verify integrity of input files against a pinned input manifest.

    Args:
        manifest_path: Path to inputs-manifest.json.
        base_dir: Root directory for relative file paths (defaults to repository root).

    Returns:
        Tuple of (is_valid, list of failure messages).
    """
    m_path = Path(manifest_path).resolve()
    if not m_path.exists():
        return False, [f"Inputs manifest file not found: {m_path}"]

    try:
        manifest_data = json.loads(m_path.read_text(encoding="utf-8"))
    except Exception as e:
        return False, [f"Failed to parse manifest JSON at {m_path}: {e}"]

    root = Path(base_dir).resolve() if base_dir else DATA_DIR.parent.resolve()
    errors: list[str] = []
    files_dict: dict[str, dict[str, Any]] = manifest_data.get("files", {})

    if not files_dict:
        return False, [f"Manifest {m_path} contains no files entry."]

    for rel_path, file_info in files_dict.items():
        if not is_safe_relative_path(rel_path):
            errors.append(f"Unsafe path in manifest: '{rel_path}'")
            continue

        target_file = (root / rel_path).resolve()
        if not str(target_file).startswith(str(root)):
            errors.append(f"Path escapes root directory: '{rel_path}'")
            continue

        required = file_info.get("required", True)
        if not target_file.exists():
            if required:
                errors.append(f"Required input file missing: '{rel_path}'")
            continue

        expected_size = file_info.get("size_bytes")
        actual_size = target_file.stat().st_size
        if expected_size is not None and actual_size != expected_size:
            errors.append(
                f"File size mismatch for '{rel_path}': expected {expected_size} bytes, got {actual_size} bytes"
            )
            continue

        expected_sha = file_info.get("sha256")
        if expected_sha:
            actual_sha = compute_sha256(target_file)
            if actual_sha.lower() != expected_sha.lower():
                errors.append(
                    f"Checksum mismatch for '{rel_path}': expected {expected_sha}, got {actual_sha}"
                )

    return len(errors) == 0, errors


def extract_inputs_archive(
    archive_path: Path | str,
    target_dir: Path | str,
) -> list[Path]:
    """Safely extract an inputs archive (zip or tar.gz) preventing directory traversal.

    Args:
        archive_path: Path to archive file.
        target_dir: Directory to extract contents into.

    Returns:
        List of extracted file paths.
    """
    src = Path(archive_path).resolve()
    dst = Path(target_dir).resolve()
    dst.mkdir(parents=True, exist_ok=True)

    if not src.exists():
        raise FileNotFoundError(f"Archive file not found: {src}")

    extracted: list[Path] = []

    if src.suffix.lower() == ".zip" or src.name.lower().endswith(".zip"):
        with zipfile.ZipFile(src, "r") as zf:
            for info in zf.infolist():
                if not is_safe_relative_path(info.filename):
                    raise ValueError(f"Unsafe file path in archive: '{info.filename}'")
                target_path = (dst / info.filename).resolve()
                if not str(target_path).startswith(str(dst)):
                    raise ValueError(
                        f"Archive path traversal attempt: '{info.filename}'"
                    )
                zf.extract(info, dst)
                extracted.append(target_path)
    elif src.name.lower().endswith((".tar.gz", ".tgz", ".tar.bz2", ".tar")):
        with tarfile.open(src, "r:*") as tf:
            for member in tf.getmembers():
                if not is_safe_relative_path(member.name):
                    raise ValueError(
                        f"Unsafe file path in tar archive: '{member.name}'"
                    )
                target_path = (dst / member.name).resolve()
                if not str(target_path).startswith(str(dst)):
                    raise ValueError(f"Archive path traversal attempt: '{member.name}'")
                if member.islnk() or member.issym():
                    link_target = (target_path.parent / member.linkname).resolve()
                    if not str(link_target).startswith(str(dst)):
                        raise ValueError(
                            f"Symlink escapes target directory: '{member.name}' -> '{member.linkname}'"
                        )
                tf.extract(member, dst)
                extracted.append(target_path)
    else:
        raise ValueError(f"Unsupported archive format: {src.name}")

    return extracted


def preflight_offline_inputs(
    base_dir: Path | str | None = None,
    manifest_path: Path | str | None = None,
) -> None:
    """Preflight check required inputs for offline pipeline execution.

    Validates:
    - Input manifest checksums and file presence.
    - DuckDB spatial extension loads offline without network installs.

    Raises:
        RuntimeError: If any required input or extension is missing or invalid.
    """
    root = Path(base_dir).resolve() if base_dir else DATA_DIR.parent.resolve()
    target_manifest = (
        Path(manifest_path).resolve() if manifest_path else DEFAULT_MANIFEST_PATH
    )

    if target_manifest.exists():
        valid, errors = verify_inputs_manifest(target_manifest, base_dir=root)
        if not valid:
            bullet_errors = "\n".join(f"  - {err}" for err in errors)
            raise RuntimeError(
                f"Offline input manifest verification failed with {len(errors)} error(s):\n{bullet_errors}\n"
                "Please verify or restore the input files from the authoritative inputs release archive."
            )
    else:
        # Check core files directly if manifest file not provided
        required_relative = [
            "data/external/school-location-2022.csv",
            "data/external/school-profile-2022.csv",
            "data/external/acara_school_results.json",
            "data/reference/acara_school_finance_benchmarks.csv",
            "data/raw/aph/individuals.json",
        ]
        missing = [p for p in required_relative if not (root / p).exists()]
        if missing:
            bullet_missing = "\n".join(f"  - {m}" for m in missing)
            raise RuntimeError(
                f"Missing required offline inputs:\n{bullet_missing}\n"
                "To run in offline mode, ensure cached input files are present."
            )

    # Preflight DuckDB spatial extension offline
    from apemap.db import ensure_spatial, get_connection

    test_conn = get_connection(None)
    try:
        ensure_spatial(test_conn, allow_install=False)
    except Exception as e:
        raise RuntimeError(
            "DuckDB spatial extension is not available offline. "
            "Ensure the spatial extension is pre-installed in the DuckDB extension directory."
        ) from e
    finally:
        test_conn.close()
