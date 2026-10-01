"""Tests for offline inputs manifest verification and archive extraction safety."""

from __future__ import annotations

import io
import json
from pathlib import Path
import tarfile
import zipfile

import pytest

from apemap.inputs import (
    compute_sha256,
    extract_inputs_archive,
    is_safe_relative_path,
    preflight_offline_inputs,
    verify_inputs_manifest,
)


@pytest.mark.unit
def test_compute_sha256(tmp_path: Path) -> None:
    """Verify deterministic SHA-256 calculation."""
    f = tmp_path / "sample.txt"
    f.write_bytes(b"hello apemap\n")
    expected = "795fb4afb6ed2e8f7651dd15f59bffc49fcd7ce9bea638d1160e2b7fe7dae008"
    assert compute_sha256(f) == expected


@pytest.mark.unit
def test_is_safe_relative_path() -> None:
    """Verify rejection of directory traversal and absolute path attempts."""
    # Safe paths
    assert is_safe_relative_path("data/external/test.csv")
    assert is_safe_relative_path("file.txt")
    assert is_safe_relative_path("a/b/c/d.json")

    # Unsafe paths
    assert not is_safe_relative_path("../evil.txt")
    assert not is_safe_relative_path("data/../../evil.txt")
    assert not is_safe_relative_path("/etc/passwd")
    assert not is_safe_relative_path("\\windows\\system32")
    assert not is_safe_relative_path("C:\\Windows\\system32")
    assert not is_safe_relative_path("")
    assert not is_safe_relative_path(None)  # type: ignore[arg-type]


@pytest.mark.unit
def test_verify_inputs_manifest_success_and_failures(tmp_path: Path) -> None:
    """Verify manifest validation succeeds with valid files and catches tampering."""
    root = tmp_path / "repo"
    root.mkdir()
    ext_dir = root / "data" / "external"
    ext_dir.mkdir(parents=True)

    file_a = ext_dir / "a.csv"
    file_a.write_text("col1,col2\n1,2\n", encoding="utf-8")
    hash_a = compute_sha256(file_a)
    size_a = file_a.stat().st_size

    manifest = {
        "schema_version": "1.0",
        "files": {
            "data/external/a.csv": {
                "sha256": hash_a,
                "size_bytes": size_a,
                "required": True,
            }
        },
    }
    m_path = tmp_path / "manifest.json"
    m_path.write_text(json.dumps(manifest), encoding="utf-8")

    # 1. Valid case
    valid, errors = verify_inputs_manifest(m_path, base_dir=root)
    assert valid is True
    assert errors == []

    # 2. Corrupted file content
    file_a.write_text("corrupted content", encoding="utf-8")
    valid_corrupt, errors_corrupt = verify_inputs_manifest(m_path, base_dir=root)
    assert valid_corrupt is False
    assert any("Checksum mismatch" in e or "size mismatch" in e for e in errors_corrupt)

    # 3. Missing required file
    file_a.unlink()
    valid_missing, errors_missing = verify_inputs_manifest(m_path, base_dir=root)
    assert valid_missing is False
    assert any("Required input file missing" in e for e in errors_missing)


@pytest.mark.unit
def test_extract_inputs_archive_zip_slip_prevention(tmp_path: Path) -> None:
    """Ensure safe extraction blocks zip slip path traversal attacks."""
    dest = tmp_path / "extracted"

    # Create zip with malicious path
    malicious_zip = tmp_path / "evil.zip"
    with zipfile.ZipFile(malicious_zip, "w") as zf:
        zf.writestr("../evil.txt", "pwned")

    with pytest.raises(ValueError, match="Unsafe file path"):
        extract_inputs_archive(malicious_zip, dest)

    # Create valid zip
    good_zip = tmp_path / "good.zip"
    with zipfile.ZipFile(good_zip, "w") as zf:
        zf.writestr("data/sample.csv", "id,name\n1,test\n")

    extracted = extract_inputs_archive(good_zip, dest)
    assert len(extracted) == 1
    assert (dest / "data" / "sample.csv").exists()


@pytest.mark.unit
def test_extract_inputs_archive_tar_slip_prevention(tmp_path: Path) -> None:
    """Ensure safe extraction blocks tar slip path traversal attacks."""
    dest = tmp_path / "extracted_tar"

    # Create tar with malicious path
    malicious_tar = tmp_path / "evil.tar.gz"
    with tarfile.open(malicious_tar, "w:gz") as tf:
        data = b"malicious content"
        ti = tarfile.TarInfo(name="../escape.txt")
        ti.size = len(data)
        tf.addfile(ti, io.BytesIO(data))

    with pytest.raises(ValueError, match="Unsafe file path"):
        extract_inputs_archive(malicious_tar, dest)


@pytest.mark.unit
def test_preflight_offline_inputs_passes_on_repo() -> None:
    """Preflight check passes on the canonical repository inputs."""
    # Should not raise
    preflight_offline_inputs()


@pytest.mark.unit
def test_preflight_offline_inputs_fails_when_files_missing(tmp_path: Path) -> None:
    """Preflight check raises RuntimeError when required inputs are missing."""
    empty_dir = tmp_path / "empty_repo"
    empty_dir.mkdir()
    with pytest.raises(
        RuntimeError, match="verification failed|Missing required offline inputs"
    ):
        preflight_offline_inputs(base_dir=empty_dir)


@pytest.mark.unit
def test_cli_inputs_verify() -> None:
    """Test apemap inputs verify CLI command."""
    from typer.testing import CliRunner
    from apemap.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["inputs", "verify"])
    assert result.exit_code == 0
    assert "Inputs manifest verification passed successfully!" in result.output


@pytest.mark.unit
def test_cli_run_all_offline_flags() -> None:
    """Test apemap run-all --offline rejects incompatible flags."""
    from typer.testing import CliRunner
    from apemap.cli import app

    runner = CliRunner()
    # Combining --offline with --download must fail
    res_dl = runner.invoke(app, ["run-all", "--offline", "--download"])
    assert res_dl.exit_code != 0
    assert "Cannot combine --offline with --download" in res_dl.output

    # Combining --offline with --refresh must fail
    res_rf = runner.invoke(app, ["run-all", "--offline", "--refresh"])
    assert res_rf.exit_code != 0
    assert "Cannot combine --offline with --refresh" in res_rf.output
