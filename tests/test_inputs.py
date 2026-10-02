"""Tests for offline inputs manifest verification and archive extraction safety."""

from __future__ import annotations

import io
import json
from pathlib import Path
import tarfile
from unittest.mock import MagicMock, patch
import zipfile

import pytest
from rich.text import Text

from apemap.inputs import (
    compute_sha256,
    extract_inputs_archive,
    is_safe_relative_path,
    preflight_offline_inputs,
    restore_inputs,
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
    assert not is_safe_relative_path("data\\raw\\file")
    assert not is_safe_relative_path("data/raw/file:stream")
    assert not is_safe_relative_path("data/./raw/file")


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
@pytest.mark.parametrize("force_color", [None, "1"])
def test_cli_run_all_offline_flags(force_color: str | None) -> None:
    """Test apemap run-all --offline rejects incompatible flags."""
    from typer.testing import CliRunner
    from apemap.cli import app

    runner = CliRunner()
    # Combining --offline with --download must fail
    res_dl = runner.invoke(
        app, ["run-all", "--offline", "--download"], env={"FORCE_COLOR": force_color}
    )
    assert res_dl.exit_code != 0
    assert (
        "Cannot combine --offline with --download"
        in Text.from_ansi(res_dl.output).plain
    )

    # Combining --offline with --refresh must fail
    res_rf = runner.invoke(
        app, ["run-all", "--offline", "--refresh"], env={"FORCE_COLOR": force_color}
    )
    assert res_rf.exit_code != 0
    assert (
        "Cannot combine --offline with --refresh" in Text.from_ansi(res_rf.output).plain
    )


@pytest.mark.unit
@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE])
def test_extract_rejects_links_and_special_members(tmp_path: Path, kind: bytes) -> None:
    archive = tmp_path / "links.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        good = tarfile.TarInfo("data/good.txt")
        good.size = 4
        tar.addfile(good, io.BytesIO(b"good"))
        member = tarfile.TarInfo("data/link")
        member.type = kind
        member.linkname = "good.txt"
        tar.addfile(member)
    destination = tmp_path / "extracted"
    with pytest.raises(ValueError, match="Non-regular"):
        extract_inputs_archive(archive, destination)
    assert not (destination / "data/good.txt").exists()


@pytest.mark.unit
def test_extract_rejects_zip_symlink(tmp_path: Path) -> None:
    archive = tmp_path / "links.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        info = zipfile.ZipInfo("data/link")
        info.create_system = 3
        info.external_attr = 0o120777 << 16
        zf.writestr(info, "../../escape")
    with pytest.raises(ValueError, match="Non-regular"):
        extract_inputs_archive(archive, tmp_path / "extracted")


@pytest.fixture
def input_bundle(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Tiny pinned bundle with one raw file and one tracked fixture."""
    root = tmp_path / "repo"
    tracked = root / "data/external/tracked.csv"
    tracked.parent.mkdir(parents=True)
    tracked.write_bytes(b"tracked\n")
    raw_name = "data/raw/aph/individuals.json"
    content = b'{"members": []}\n'
    archive = tmp_path / "inputs.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        member = tarfile.TarInfo(raw_name)
        member.size = len(content)
        tar.addfile(member, io.BytesIO(content))
    import hashlib

    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "archive_url": "https://example.test/inputs.tar.gz",
                "archive_sha256": compute_sha256(archive),
                "files": {
                    raw_name: {
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "size_bytes": len(content),
                        "required": True,
                    },
                    "data/external/tracked.csv": {
                        "sha256": compute_sha256(tracked),
                        "size_bytes": tracked.stat().st_size,
                        "required": True,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    return manifest, archive, root


@pytest.mark.unit
def test_restore_local_bundle_offline(
    input_bundle: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, archive, root = input_bundle
    monkeypatch.setenv("APEMAP_OFFLINE", "1")
    restored = restore_inputs(manifest, archive_path=archive, base_dir=root)
    assert len(restored) == 1
    assert verify_inputs_manifest(manifest, base_dir=root) == (True, [])
    assert (root / "data/external/tracked.csv").read_bytes() == b"tracked\n"


@pytest.mark.unit
def test_restore_checks_archive_before_extraction(
    input_bundle: tuple[Path, Path, Path],
) -> None:
    manifest, archive, root = input_bundle
    archive.write_bytes(b"tampered archive")
    with patch("apemap.inputs.extract_inputs_archive") as extract:
        with pytest.raises(ValueError, match="SHA-256 mismatch"):
            restore_inputs(manifest, archive_path=archive, base_dir=root)
        extract.assert_not_called()
    assert not (root / "data/raw").exists()


@pytest.mark.unit
@pytest.mark.parametrize("failure", ["missing", "unexpected", "member_hash"])
def test_restore_validates_all_members_before_copy(
    input_bundle: tuple[Path, Path, Path], failure: str
) -> None:
    manifest, archive, root = input_bundle
    metadata = json.loads(manifest.read_text(encoding="utf-8"))
    if failure == "member_hash":
        metadata["files"]["data/raw/aph/individuals.json"]["sha256"] = "0" * 64
    else:
        mode = "w:gz"
        with tarfile.open(archive, mode) as tar:
            if failure == "unexpected":
                member = tarfile.TarInfo("data/external/tracked.csv")
                member.size = 7
                tar.addfile(member, io.BytesIO(b"changed"))
        metadata["archive_sha256"] = compute_sha256(archive)
    manifest.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(ValueError, match="inventory|bytes differ"):
        restore_inputs(manifest, archive_path=archive, base_dir=root)
    assert not (root / "data/raw").exists()
    assert (root / "data/external/tracked.csv").read_bytes() == b"tracked\n"


@pytest.mark.unit
def test_restore_download_and_http_failure(
    input_bundle: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    import requests

    manifest, archive, root = input_bundle
    monkeypatch.setenv("APEMAP_OFFLINE", "0")
    response = MagicMock()
    response.__enter__.return_value = response
    response.iter_content.return_value = [archive.read_bytes()]
    with patch("apemap.inputs.create_retry_session") as create_session:
        session = create_session.return_value.__enter__.return_value
        session.get.return_value = response
        response.raise_for_status.side_effect = requests.HTTPError("404")
        with pytest.raises(requests.HTTPError):
            restore_inputs(manifest, base_dir=root)
        assert not (root / "data/raw").exists()
        response.raise_for_status.side_effect = None
        assert len(restore_inputs(manifest, base_dir=root)) == 1
        session.get.assert_called_with(
            "https://example.test/inputs.tar.gz", stream=True, timeout=(10, 120)
        )
        response.raise_for_status.assert_called()
    monkeypatch.setenv("APEMAP_OFFLINE", "1")
    with pytest.raises(ValueError, match="online setup"):
        restore_inputs(manifest, base_dir=root)


@pytest.mark.unit
def test_cli_inputs_restore(input_bundle: tuple[Path, Path, Path]) -> None:
    from typer.testing import CliRunner
    from apemap.cli import app

    manifest, archive, _ = input_bundle
    with patch("apemap.cli.restore_inputs", return_value=[Path("restored")]) as restore:
        result = CliRunner().invoke(
            app,
            [
                "inputs",
                "restore",
                "--manifest",
                str(manifest),
                "--archive",
                str(archive),
            ],
        )
        assert result.exit_code == 0
        restore.assert_called_once_with(manifest, archive_path=archive)
        restore.side_effect = ValueError("SHA-256 mismatch")
        result = CliRunner().invoke(
            app,
            [
                "inputs",
                "restore",
                "--manifest",
                str(manifest),
                "--archive",
                str(archive),
            ],
        )
        assert result.exit_code == 1
        assert "SHA-256 mismatch" in result.output


@pytest.mark.unit
def test_spatial_offline_does_not_install_in_empty_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import duckdb
    from apemap.db import ensure_spatial

    monkeypatch.setenv(
        "APEMAP_DUCKDB_EXTENSION_DIR", str(tmp_path / "empty-extensions")
    )
    with duckdb.connect() as conn:
        with pytest.raises(RuntimeError, match="offline mode prevents"):
            ensure_spatial(conn, allow_install=False)
    assert not list(tmp_path.rglob("*.duckdb_extension"))
