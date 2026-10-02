"""Offline checks for canonical chamber aggregates and the design reference."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from apemap.analysis import compute_chamber_sector_summary, export_analysis_report
from apemap.db import get_connection
from apemap.prototype import bars, build_prototype, explorer_payload, table
from apemap.release import build_release
from tests.test_analysis import create_analysis_fixture


def test_chamber_sectors_keep_missing_and_multiple_attendance(tmp_path: Path) -> None:
    conn = get_connection(create_analysis_fixture(tmp_path / "chambers.duckdb"))
    conn.execute(
        "UPDATE parliament_service SET chamber = 'senate' WHERE member_id IN ('m-both', 'm-none')"
    )
    summary = compute_chamber_sector_summary(conn, 47)
    assert summary["total_parliamentarians"] == 5
    senate = summary["chambers"]["senate"]
    assert senate["total_parliamentarians"] == 2
    assert senate["known_school_denominator"] == 1
    assert senate["government_non_government"]["mixed"] == 1
    assert senate["parliamentarians_without_known_schools"] == 1
    assert senate["government_non_government_percentages"]["mixed"] == 100
    export_analysis_report(conn, tmp_path / "analysis", [47])
    exported = json.loads(
        (tmp_path / "analysis/analysis/chamber_sectors.json").read_text()
    )
    assert exported["parliaments"]["47"] == summary
    conn.close()


def test_markup_escapes_source_text_and_handles_zero_denominator() -> None:
    markup = table(["School"], [["</script><script>alert(1)</script>"]], "Names")
    assert "<script>" not in markup
    assert "scope='row'" in markup
    assert "Unavailable" in bars({"unknown": 0}, {"unknown": "Unknown"}, 0, "Empty")


def test_prototype_gate_rejects_unverified_input(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="verification failed"):
        build_prototype(tmp_path / "missing", tmp_path / "out")
    with pytest.raises(ValueError, match="outside the immutable"):
        build_prototype(tmp_path, tmp_path / "out")


def test_release_reference_is_offline_deterministic_and_keeps_unmapped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = get_connection(create_analysis_fixture(tmp_path / "source.duckdb"))
    release = tmp_path / "release"
    build_release(
        conn=conn,
        output_dir=release,
        version="fixture",
        parliaments=[47],
        strict=False,
        source_commit="a" * 40,
        generated_at="2026-10-02T00:00:00Z",
    )
    conn.close()
    # Tiny analytical fixtures cannot satisfy national seat benchmarks. The
    # generation tests isolate rendering; gate rejection is tested separately.
    monkeypatch.setattr(
        "apemap.prototype.verify_release", lambda *a, **k: {"valid": True}
    )
    first = build_prototype(release, tmp_path / "one").read_bytes()
    second = build_prototype(release, tmp_path / "two").read_bytes()
    assert first == second
    html = first.decode()
    assert 'src="http' not in html
    assert "application/json" in html
    assert "finance_value" not in html
    assert "42nd–48th Parliaments" not in html
    assert "47th Parliament" in html
    metadata = json.loads((release / "web/metadata.json").read_text())
    payload = explorer_payload(release, metadata)
    assert len(payload["schools"]) == 3
    assert all(s["coordinates"] is None for s in payload["schools"].values())
    assert len(payload["members"]["47"]) == 5


def test_explorer_javascript_contract() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Optional Node runtime for offline reference JS tests")
    result = subprocess.run(
        [node, "--test", str(Path(__file__).with_name("prototype.test.cjs"))],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
