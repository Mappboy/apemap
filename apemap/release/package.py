"""Package a verified release deterministically, without publishing or tagging."""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
import shutil
import tarfile
from typing import Any

from apemap.export import _file_sha256
from apemap.release.verify import verify_release


def package_release(release_dir: Path, output_dir: Path) -> dict[str, Any]:
    """Write sorted, normalized tar/gzip bytes and separate archive checksums.

    Existing archives and inventories are immutable. Source provenance stays in the
    bundled manifest; filesystem times and user names never affect archive bytes.
    """
    root, out = release_dir.resolve(), output_dir.resolve()
    if out.is_relative_to(root):
        raise ValueError("Package output must be outside the immutable release")
    verification = verify_release(root, strict_assertions=True)
    if not verification["valid"]:
        raise ValueError(f"Release verification failed: {verification['errors']}")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    version = manifest["data_release_version"]
    if (
        not isinstance(version, str)
        or not version
        or any(
            c not in "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ.-+"
            for c in version
        )
    ):
        raise ValueError("Release version cannot form a safe archive filename")
    out.mkdir(parents=True, exist_ok=True)
    archive = out / f"apemap-release-v{version}.tar.gz"
    for target in (
        archive,
        *(out / name for name in ("manifest.json", "SHA256SUMS", "SHA256SUMS.dist")),
    ):
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"Package output already exists: {target}")
    files = sorted(["manifest.json", "SHA256SUMS", *manifest["files"]])
    # Validate every path before producing any archive content.
    for name in files:
        source = root / name
        if not source.resolve().is_relative_to(root) or source.is_symlink():
            raise ValueError(f"Unsafe archive source: {name}")
    temporary = archive.with_suffix(".tmp")
    owns_temporary = False
    try:
        with temporary.open("xb") as raw:
            owns_temporary = True
            with gzip.GzipFile(
                filename="", fileobj=raw, mode="wb", mtime=0
            ) as compressed:
                with tarfile.open(
                    fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT
                ) as bundle:
                    for name in files:
                        source = root / name
                        info = tarfile.TarInfo(name)
                        info.size = source.stat().st_size
                        info.mtime = 0
                        info.mode = 0o644
                        with source.open("rb") as content:
                            bundle.addfile(info, content)
        temporary.replace(archive)
    finally:
        if owns_temporary:
            temporary.unlink(missing_ok=True)
    digest = _file_sha256(archive)
    for name in ("manifest.json", "SHA256SUMS"):
        with (root / name).open("rb") as source, (out / name).open("xb") as target:
            shutil.copyfileobj(source, target)
    with (out / "SHA256SUMS.dist").open("x", encoding="utf-8", newline="\n") as target:
        target.write(f"{digest}  {archive.name}\n")
    return {
        "data_release_version": version,
        "source_commit": manifest["source_commit"],
        "archive": archive.name,
        "archive_sha256": digest,
        "archive_size_bytes": archive.stat().st_size,
        "verified_files_count": verification["verified_files_count"],
        "checks_passed": verification["checks_passed"],
    }


def main() -> None:
    """Package a local candidate; uploading remains a separate operation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release_dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(package_release(args.release_dir, args.output_dir), indent=2))


if __name__ == "__main__":
    main()
