"""Typed unresolved outcomes retain reasons through the public review CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from apemap.cli import app
from apemap.review.model import education_review_id, load_events, school_review_id
from apemap.review.service import ReviewService
from tests.test_review_service import review_service as review_service


@pytest.mark.parametrize("reason", ["ambiguous_name", "no_suitable_candidate"])
@pytest.mark.parametrize("entity", ["school", "member_education"])
def test_cli_previews_and_retains_typed_resolution_reasons(
    review_service: ReviewService, tmp_path: Path, reason: str, entity: str
) -> None:
    name = "Test High School"
    review_id = (
        school_review_id(name)
        if entity == "school"
        else education_review_id("TEST", name)
    )
    payload = (
        {"recorded_name": name}
        if entity == "school"
        else {"aph_id": "TEST", "recorded_school_name": name}
    ) | {"resolution_reason": reason}
    payload_path = tmp_path / "outcome.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")
    command = [
        "review",
        "--log-path",
        str(review_service.log_path),
        "--db-path",
        str(review_service.db_path),
        "--external-dir",
        str(review_service.external_dir),
        "research",
        review_id,
        "--payload",
        str(payload_path),
        "--note",
        "Retain attendance while reviewing the school identity",
        "--reviewer",
        "Fixture reviewer",
    ]
    runner = CliRunner()
    dry = runner.invoke(app, [*command, "--dry-run"])
    assert dry.exit_code == 0, dry.output
    preview = json.loads(dry.output)
    proposed = preview["event"]["payload"]
    assert proposed["resolution_reason"] == reason
    flag = "requires_individual_resolution" if entity == "school" else "resolution_only"
    assert proposed[flag] is True
    assert not review_service.log_path.exists()
    if entity == "school":
        progress = preview["individual_resolution"]["after"]
        assert progress["available"] and progress["unresolved_count"] == 1
        assert progress["status"] == "needs_individual_review"

    saved = runner.invoke(app, command)
    assert saved.exit_code == 0, saved.output
    events = load_events(review_service.log_path)
    assert len(events) == 1 and events[0].payload == proposed
    assert events[0].status == "needs_research"
    assert review_service.show(review_id)["decision"]["payload"] == proposed
