"""Explicit CLI review authorities fail closed before ingestion begins."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from apemap.cli import app


COMMANDS = [
    ["ingest", "aph"],
    ["ingest", "acara"],
    ["ingest", "wikimedia"],
    ["run-all"],
]


@pytest.mark.unit
@pytest.mark.parametrize("command", COMMANDS)
@pytest.mark.parametrize("invalid_kind", ["missing", "directory", "unreadable"])
def test_explicit_decision_log_is_validated_before_ingestion(
    command: list[str],
    invalid_kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = tmp_path / "review.jsonl"
    if invalid_kind == "directory":
        ledger.mkdir()
    elif invalid_kind == "unreadable":
        ledger.write_bytes(b"")
        original_access = os.access

        def deny_ledger_read(path: str | bytes | Path, mode: int) -> bool:
            if path == str(ledger) and mode == os.R_OK:
                return False
            return original_access(path, mode)

        monkeypatch.setattr("click.types.os.access", deny_ledger_read)

    def unexpected_ingestion(**kwargs: Any) -> None:
        raise AssertionError("Invalid review authority reached ingestion")

    for name in (
        "run_aph_ingestion",
        "run_acara_ingestion",
        "run_wikimedia_enrichment",
        "run_aec_ingestion",
    ):
        monkeypatch.setattr(f"apemap.cli.{name}", unexpected_ingestion)

    result = CliRunner().invoke(app, [*command, "--decision-log", str(ledger)])
    assert result.exit_code == 2, result.output
    assert "--decision-log" in result.output
    assert {
        "missing": "does not exist",
        "directory": "is a directory",
        "unreadable": "is not readable",
    }[invalid_kind] in result.output


@pytest.mark.unit
@pytest.mark.parametrize("command", COMMANDS)
def test_explicit_empty_decision_log_is_accepted(
    command: list[str],
    empty_review_log: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class IngestionReached(Exception):
        pass

    def stop_ingestion(**kwargs: Any) -> None:
        if command != ["run-all"]:
            assert kwargs["decision_log_path"] == empty_review_log
        raise IngestionReached

    entrypoint = {
        ("ingest", "aph"): "run_aph_ingestion",
        ("ingest", "acara"): "run_acara_ingestion",
        ("ingest", "wikimedia"): "run_wikimedia_enrichment",
        ("run-all",): "run_aec_ingestion",
    }[tuple(command)]
    monkeypatch.setattr(f"apemap.cli.{entrypoint}", stop_ingestion)

    result = CliRunner().invoke(
        app, [*command, "--decision-log", str(empty_review_log)]
    )
    assert isinstance(result.exception, IngestionReached), result.output
