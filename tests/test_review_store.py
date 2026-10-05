"""Atomic batch writes, revision checks, and Git event preservation."""

from dataclasses import replace
from pathlib import Path
import multiprocessing
import subprocess
from typing import Any
from unittest.mock import patch

import pytest
import portalocker

from apemap.review.model import load_events, school_review_id
from apemap.review.store import (
    ReviewBusyError,
    StaleReviewError,
    append_events,
    check_append_only,
    encode_event,
    log_revision,
)
from tests.test_review_model import school_event

pytestmark = pytest.mark.unit


def _concurrent_writer(
    path: str, revision: str, ready: Any, start: Any, results: Any
) -> None:
    ready.put(True)
    start.wait(10)
    try:
        append_events(Path(path), [school_event()], expected_revision=revision)
        results.put("saved")
    except StaleReviewError:
        results.put("stale")


def test_append_preserves_existing_bytes_and_rejects_stale_preview(
    tmp_path: Path,
) -> None:
    path = tmp_path / "decisions.jsonl"
    empty = log_revision(path)
    first = school_event("first")
    append_events(path, [first], expected_revision=empty)
    prior = path.read_bytes()
    second = replace(
        first,
        decision_id="second",
        action="supersede",
        replacement_action="reject",
        supersedes=[first.decision_id],
        notes="Wrong identity",
    )
    append_events(path, [second], expected_revision=log_revision(path))
    assert path.read_bytes() == prior + encode_event(second)
    with pytest.raises(StaleReviewError):
        append_events(path, [first], expected_revision=empty)
    assert len(load_events(path)) == 2


def test_failed_replace_leaves_log_intact_and_removes_temporary(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    first = school_event()
    append_events(path, [first], expected_revision=log_revision(path))
    prior = path.read_bytes()
    new = replace(
        first,
        decision_id="new",
        action="supersede",
        supersedes=[first.decision_id],
        replacement_action="map",
    )
    with patch(
        "apemap.review.store.os.replace", side_effect=OSError("disk unavailable")
    ):
        with pytest.raises(OSError):
            append_events(path, [new], expected_revision=log_revision(path))
    assert path.read_bytes() == prior
    assert not list(tmp_path.glob("*.tmp"))


def test_invalid_batch_cannot_partially_append(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    with pytest.raises(ValueError, match="Duplicate"):
        append_events(
            path, [school_event(), school_event()], expected_revision=log_revision(path)
        )
    assert not path.exists()


def test_source_guards_check_before_validation_and_before_commit(
    tmp_path: Path,
) -> None:
    path = tmp_path / "decisions.jsonl"
    checks: list[str] = []

    def guard() -> None:
        checks.append("guard")

    def validate(events: Any) -> None:
        checks.append("validate")

    append_events(
        path,
        [school_event()],
        expected_revision=log_revision(path),
        source_check=guard,
        validator=validate,
    )
    assert checks == ["guard", "validate", "guard"]
    assert len(load_events(path)) == 1


def test_source_changed_during_validation_prevents_commit(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    changed = False

    def guard() -> None:
        if changed:
            raise StaleReviewError("Source changed")

    def validate(events: Any) -> None:
        nonlocal changed
        changed = True

    with pytest.raises(StaleReviewError):
        append_events(
            path,
            [school_event()],
            expected_revision=log_revision(path),
            source_check=guard,
            validator=validate,
        )
    assert not path.exists() and not list(tmp_path.glob("*.tmp"))


def test_busy_writer_reports_retry_without_writing(tmp_path: Path) -> None:
    path = tmp_path / "decisions.jsonl"
    with patch(
        "apemap.review.store.portalocker.Lock.acquire",
        side_effect=portalocker.exceptions.LockException("locked"),
    ):
        with pytest.raises(ReviewBusyError, match="busy"):
            append_events(path, [school_event()], expected_revision=log_revision(path))
    assert not path.exists()


def test_two_processes_cannot_commit_the_same_preview(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    ready, results, start = context.Queue(), context.Queue(), context.Event()
    path = tmp_path / "decisions.jsonl"
    workers = [
        context.Process(
            target=_concurrent_writer,
            args=(str(path), log_revision(path), ready, start, results),
        )
        for _ in range(2)
    ]
    try:
        for worker in workers:
            worker.start()
        for _ in workers:
            assert ready.get(timeout=15)
        start.set()
        assert sorted(results.get(timeout=15) for _ in workers) == ["saved", "stale"]
        for worker in workers:
            worker.join(15)
            assert worker.exitcode == 0
        assert len(load_events(path)) == 1
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
            worker.join()


def test_base_check_allows_union_order_but_never_edit_or_delete() -> None:
    first = encode_event(school_event("a"))
    second = encode_event(school_event("b"))
    # Record order is irrelevant; historical bytes are the invariant.
    check_append_only(first, second + first)
    with pytest.raises(ValueError, match="modified or removed"):
        check_append_only(first, b"")
    with pytest.raises(ValueError, match="modified or removed"):
        check_append_only(first, first.replace(b"Researcher", b"Other Name"))


def test_independent_git_branches_union_without_losing_events(tmp_path: Path) -> None:
    def git(*arguments: str) -> str:
        result = subprocess.run(
            ["git", "-c", f"safe.directory={tmp_path.as_posix()}", *arguments],
            cwd=tmp_path,
            text=True,
            capture_output=True,
            check=True,
        )
        return result.stdout

    git("init", "-b", "main")
    git("config", "user.name", "Review Test")
    git("config", "user.email", "review-test@example.org")
    (tmp_path / ".gitattributes").write_text(
        "decisions.jsonl text eol=lf merge=union\n", encoding="utf-8"
    )
    path = tmp_path / "decisions.jsonl"
    path.write_bytes(b"")
    git("add", ".gitattributes", "decisions.jsonl")
    git("commit", "-m", "Initialize fixture")
    git("switch", "-c", "review-a")
    first = school_event("a")
    path.write_bytes(encode_event(first))
    git("add", "decisions.jsonl")
    git("commit", "-m", "Add first independent review")
    git("switch", "main")
    git("switch", "-c", "review-b")
    second = school_event(
        "b",
        review_id=school_review_id("Other School"),
        payload={
            "recorded_name": "Other School",
            "institution_ref": "acara:123",
            "relationship_type": "alias",
        },
    )
    path.write_bytes(encode_event(second))
    git("add", "decisions.jsonl")
    git("commit", "-m", "Add second independent review")
    git("merge", "review-a", "--no-edit")
    assert {event.decision_id for event in load_events(path)} == {"a", "b"}
