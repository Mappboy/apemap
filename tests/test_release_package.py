"""Archive determinism and immutable-output regression tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import os
import tarfile

import pytest

from apemap.release.package import package_release


def minimal_verified_release(root: Path) -> None:
    (root / "web").mkdir(parents=True)
    payload = b'{"passed": true}\n'
    (root / "web/assertions.json").write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "data_release_version": "0.3.1",
                "source_commit": "a" * 40,
                "files": {
                    "web/assertions.json": {
                        "size_bytes": len(payload),
                        "sha256": digest,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (root / "SHA256SUMS").write_text(
        f"{digest}  web/assertions.json\n", encoding="utf-8"
    )


def test_archive_bytes_ignore_file_times_and_directory(tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    minimal_verified_release(first)
    minimal_verified_release(second)
    os.utime(second / "web/assertions.json", (123456, 123456))
    result = package_release(first, tmp_path / "one")
    replay = package_release(second, tmp_path / "two")
    assert result == replay
    archive = tmp_path / "one" / result["archive"]
    assert archive.read_bytes() == (tmp_path / "two" / result["archive"]).read_bytes()
    with tarfile.open(archive) as bundle:
        assert bundle.getnames() == [
            "SHA256SUMS",
            "manifest.json",
            "web/assertions.json",
        ]
        assert all(
            member.mtime == 0 and member.uid == 0 for member in bundle.getmembers()
        )
    with pytest.raises(FileExistsError):
        package_release(first, tmp_path / "one")


def test_package_rejects_failed_verification_and_nested_output(tmp_path: Path) -> None:
    root = tmp_path / "source"
    minimal_verified_release(root)
    with pytest.raises(ValueError, match="outside the immutable"):
        package_release(root, root / "out")
    (root / "web/assertions.json").write_text('{"passed":false}')
    with pytest.raises(ValueError, match="verification failed"):
        package_release(root, tmp_path / "out")
