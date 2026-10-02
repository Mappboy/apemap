"""Offline input manifest verification, safe archive extraction, and offline preflight."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import stat
import tarfile
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import urlparse
import zipfile

from apemap.constants import DATA_DIR
from apemap.ingest.http import create_retry_session

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

    cleaned = path_str.removesuffix("/")
    if "\\" in cleaned or "\x00" in cleaned or ":" in cleaned:
        return False
    if cleaned.startswith("/"):
        return False
    if len(cleaned) >= 2 and cleaned[1] == ":":
        return False

    parts = cleaned.split("/")
    if any(p in ("", ".", "..") or p != p.strip() for p in parts):
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
        if not target_file.is_relative_to(root):
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


def _archive_target(root: Path, name: str) -> Path:
    """Reject traversal and existing symlinks before writing archive contents."""
    if not is_safe_relative_path(name):
        raise ValueError(f"Unsafe file path in archive: '{name}'")
    target = root / name
    if not target.resolve().is_relative_to(root):
        raise ValueError(f"Archive path traversal attempt: '{name}'")
    if any(p.is_symlink() for p in (target, *target.parents) if p != root):
        raise ValueError(f"Archive path contains a symlink: '{name}'")
    return target


def extract_inputs_archive(
    archive_path: Path | str,
    target_dir: Path | str,
) -> list[Path]:
    """Extract regular files only; reject traversal, links and special members.

    Validate all member paths/types before writing. Do not delegate extraction
    to tar/zip APIs that can create links or apply untrusted filesystem metadata.
    Returns the extracted regular files (directories are omitted).
    """
    src = Path(archive_path).resolve()
    dst = Path(target_dir).resolve()
    dst.mkdir(parents=True, exist_ok=True)

    if not src.exists():
        raise FileNotFoundError(f"Archive file not found: {src}")

    extracted: list[Path] = []

    if src.suffix.lower() == ".zip":
        with zipfile.ZipFile(src, "r") as zf:
            seen: set[Path] = set()
            for info in zf.infolist():
                target_path = _archive_target(dst, info.filename)
                mode = stat.S_IFMT(info.external_attr >> 16)
                if mode not in (0, stat.S_IFREG, stat.S_IFDIR):
                    raise ValueError(f"Non-regular archive member: '{info.filename}'")
                if target_path in seen:
                    raise ValueError(f"Duplicate archive member: '{info.filename}'")
                seen.add(target_path)
            for info in zf.infolist():
                target_path = _archive_target(dst, info.filename)
                if info.is_dir():
                    target_path.mkdir(parents=True, exist_ok=True)
                    continue
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as source, target_path.open("wb") as output:
                    shutil.copyfileobj(source, output)
                extracted.append(target_path)
    elif src.name.lower().endswith((".tar.gz", ".tgz", ".tar.bz2", ".tar")):
        with tarfile.open(src, "r:*") as tf:
            seen = set()
            for member in tf.getmembers():
                target_path = _archive_target(dst, member.name)
                if not (member.isfile() or member.isdir()):
                    raise ValueError(f"Non-regular archive member: '{member.name}'")
                if target_path in seen:
                    raise ValueError(f"Duplicate archive member: '{member.name}'")
                seen.add(target_path)
            for member in tf.getmembers():
                target_path = _archive_target(dst, member.name)
                if member.isdir():
                    target_path.mkdir(parents=True, exist_ok=True)
                    continue
                source = tf.extractfile(member)
                if source is None:
                    raise ValueError(f"Unreadable archive member: '{member.name}'")
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with source, target_path.open("wb") as output:
                    shutil.copyfileobj(source, output)
                extracted.append(target_path)
    else:
        raise ValueError(f"Unsupported archive format: {src.name}")

    return extracted


def restore_inputs(
    manifest_path: Path | str = DEFAULT_MANIFEST_PATH,
    *,
    archive_path: Path | str | None = None,
    base_dir: Path | str | None = None,
) -> list[Path]:
    """Restore only the manifest's required raw inputs from a SHA-pinned bundle.

    Download requires online setup; a supplied local archive works offline.
    Verify the bundle before extraction, then its exact file inventory and each
    member's bytes in a staging directory before copying to the repository.
    Tracked inputs are never restored from the archive; verify them separately.
    """
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    digest = manifest.get("archive_sha256", "")
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("Manifest must pin a valid archive SHA-256")
    raw_files = {
        name: info
        for name, info in manifest.get("files", {}).items()
        if name.startswith("data/raw/") and info.get("required", True)
    }
    if not raw_files:
        raise ValueError("Manifest contains no required raw inputs")
    root = Path(base_dir).resolve() if base_dir else DATA_DIR.parent.resolve()
    targets = {name: _archive_target(root, name) for name in raw_files}
    with TemporaryDirectory(prefix="apemap-inputs-") as temporary:
        staging = Path(temporary).resolve()
        if archive_path is None:
            if os.environ.get("APEMAP_OFFLINE") == "1":
                raise ValueError("Input download requires online setup or --archive")
            url = manifest.get("archive_url", "")
            if urlparse(url).scheme != "https":
                raise ValueError("Input archive URL must use HTTPS")
            archive = staging / Path(urlparse(url).path).name
            with create_retry_session() as session:
                with session.get(url, stream=True, timeout=(10, 120)) as response:
                    response.raise_for_status()
                    with archive.open("wb") as output:
                        for chunk in response.iter_content(chunk_size=65536):
                            output.write(chunk)
        else:
            archive = Path(archive_path).resolve()
        if compute_sha256(archive) != digest:
            raise ValueError("Input archive SHA-256 mismatch; refusing extraction")
        extracted_root = staging / "extracted"
        extracted = extract_inputs_archive(archive, extracted_root)
        inventory = {p.relative_to(extracted_root).as_posix() for p in extracted}
        if inventory != set(raw_files):
            raise ValueError("Input archive inventory differs from required raw inputs")
        for name, info in raw_files.items():
            source = extracted_root / name
            if source.stat().st_size != info.get("size_bytes") or compute_sha256(
                source
            ) != info.get("sha256"):
                raise ValueError(f"Restored input bytes differ from manifest: '{name}'")
        for name, target in targets.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(extracted_root / name, target)
    return list(targets.values())


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
