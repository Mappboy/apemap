"""Separate school-wide CLI approval and atomic, source-bound imports."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from apemap.cli import app
from apemap.review.model import ReviewEvent, school_review_id
from apemap.review.preview import apply_preview, write_preview
from apemap.review.service import ReviewService
from apemap.review.store import StaleReviewError
from tests.test_review_service import review_service as review_service

pytestmark = pytest.mark.integration


def command(service: ReviewService) -> list[str]:
    return [
        "review",
        "--log-path",
        str(service.log_path),
        "--db-path",
        str(service.db_path),
        "--external-dir",
        str(service.external_dir),
    ]


def school_preview(service: ReviewService) -> dict[str, Any]:
    return service.prepare(
        school_review_id("Test High School"),
        "accept",
        {
            "recorded_name": "Test High School",
            "institution_ref": "acara:2",
            "relationship_type": "alias",
        },
        reviewer="Fixture reviewer",
    )


def test_cli_requires_separately_approved_school_preview(
    review_service: ReviewService, tmp_path: Path
) -> None:
    service = review_service
    runner = CliRunner()
    args = [
        *command(service),
        "accept",
        school_review_id("Test High School"),
        "--institution-ref",
        "acara:2",
    ]
    blocked = runner.invoke(app, args)
    assert blocked.exit_code == 1 and "--preview-out" in blocked.output
    assert not service.log_path.exists()
    path = tmp_path / "preview.json"
    result = runner.invoke(app, [*args, "--preview-out", str(path)])
    assert result.exit_code == 0, result.output
    preview = json.loads(result.output)
    assert preview["preview"]["changes"]["default_applications"][0]["assertions"][0][
        "applies"
    ]
    assert not service.log_path.exists()
    result = runner.invoke(
        app,
        [*command(service), "apply-preview", str(path), "--approve", preview["sha256"]],
    )
    assert result.exit_code == 0, result.output
    assert service.events()[0].payload["institution_ref"] == "acara:2"


@pytest.mark.parametrize(
    "change", ["digest", "source", "ledger", "effects", "inventory"]
)
def test_changed_preview_or_inputs_never_append(
    review_service: ReviewService, tmp_path: Path, change: str
) -> None:
    service = review_service
    path = tmp_path / "preview.json"
    result = write_preview(
        service, [ReviewEvent.from_dict(school_preview(service)["event"])], path
    )
    digest = result["sha256"]
    if change in {"digest", "effects", "inventory"}:
        value = json.loads(path.read_text())
        if change == "inventory":
            value["school_assertions"] = {}
        else:
            value["changes"]["default_applications"] = []
        path.write_text(json.dumps(value))
        if change in {"effects", "inventory"}:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
    elif change == "source":
        with (service.external_dir / "school-location-2025.csv").open("a") as stream:
            stream.write("3,New School,Government,Secondary,TAS,-42,147\n")
    else:
        service.save(
            service.prepare(
                "member:test:gender",
                "research",
                {"aph_id": "TEST", "field": "gender"},
                notes="Research gender",
            )
        )
    before = service.log_path.read_bytes() if service.log_path.exists() else b""
    with pytest.raises((ValueError, StaleReviewError)):
        apply_preview(service, path, digest)
    assert (
        service.log_path.read_bytes() if service.log_path.exists() else b""
    ) == before


def test_missing_inventory_and_authority_destinations_are_blocked(
    review_service: ReviewService, tmp_path: Path
) -> None:
    service = review_service
    event = ReviewEvent.from_dict(school_preview(service)["event"])
    with pytest.raises(ValueError, match="authority"):
        write_preview(service, [event], service.log_path)
    service.db_path = tmp_path / "missing.duckdb"
    result = write_preview(service, [event], tmp_path / "preview.json")
    with pytest.raises(ValueError, match="affected assertions"):
        apply_preview(service, Path(result["preview_path"]), result["sha256"])
    assert not service.log_path.exists()


def test_batch_is_atomic_and_preserves_preexisting_bytes(
    review_service: ReviewService, tmp_path: Path
) -> None:
    service = review_service
    service.save(
        service.prepare(
            "member:test:gender",
            "research",
            {"aph_id": "TEST", "field": "gender"},
            notes="Research gender",
        )
    )
    before = service.log_path.read_bytes()
    event = ReviewEvent.from_dict(school_preview(service)["event"])
    second = ReviewEvent.from_dict(
        service.prepare(
            "member:test:date_of_birth",
            "research",
            {"aph_id": "TEST", "field": "date_of_birth"},
            notes="Research date",
        )["event"]
    )
    path = tmp_path / "batch.json"
    result = write_preview(service, [event, second], path)
    saved = apply_preview(service, path, result["sha256"])
    assert saved["new_events"] == 2
    assert service.log_path.read_bytes().startswith(before)
    assert len(service.events()) == 3
    with pytest.raises(StaleReviewError):
        apply_preview(service, path, result["sha256"])


def test_import_apply_cannot_bypass_school_approval(
    review_service: ReviewService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from apemap.review import migration

    event = ReviewEvent.from_dict(school_preview(review_service)["event"])
    monkeypatch.setattr(
        migration,
        "propose_review_csv_import",
        lambda *args, **kwargs: ([event], {"errors": []}),
    )
    runner = CliRunner()
    args = [
        *command(review_service),
        "import",
        "--source",
        str(tmp_path / "source.csv"),
    ]
    result = runner.invoke(app, [*args, "--apply"])
    assert result.exit_code == 1 and "preview-out" in result.output
    assert not review_service.log_path.exists()
    result = runner.invoke(app, [*args, "--preview-out", str(tmp_path / "import.json")])
    assert result.exit_code == 0, result.output
