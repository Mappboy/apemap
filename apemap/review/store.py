"""Atomic logical append: preserve every prior byte and protect stale revisions."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterator

import portalocker

from apemap.review.model import (
    DEFAULT_LOG_PATH,
    ReviewEvent,
    load_events,
    validate_events,
)


class StaleReviewError(ValueError):
    """The preview no longer describes the current decision/source state."""


class ReviewBusyError(StaleReviewError):
    """Another local writer holds the decision lock."""


@contextmanager
def _decision_lock(path: Path) -> Iterator[None]:
    lock = portalocker.Lock(str(path), mode="a", timeout=5)
    try:
        lock.acquire()
    except portalocker.exceptions.LockException as exc:
        raise ReviewBusyError(
            "Another review writer is busy; retry after it finishes"
        ) from exc
    try:
        yield
    finally:
        lock.release()


def log_revision(path: Path = DEFAULT_LOG_PATH) -> str:
    return hashlib.sha256(path.read_bytes() if path.exists() else b"").hexdigest()


def encode_event(event: ReviewEvent) -> bytes:
    return (
        json.dumps(
            event.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def append_events(
    path: Path,
    events: list[ReviewEvent],
    *,
    expected_revision: str,
    validator: Callable[[list[ReviewEvent]], Any] = validate_events,
    source_check: Callable[[], None] | None = None,
) -> None:
    """Commit an all-or-nothing batch; no existing event is reserialized."""
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with _decision_lock(lock_path):
        previous = path.read_bytes() if path.exists() else b""
        if hashlib.sha256(previous).hexdigest() != expected_revision:
            raise StaleReviewError("Decision log changed; refresh and preview again")
        if source_check is not None:
            source_check()
        if previous and not previous.endswith(b"\n"):
            raise ValueError("Decision log must end with a newline before appending")
        existing = load_events(path, allow_conflicts=True)
        validator(existing + events)
        if not events:
            if source_check is not None:
                source_check()
            return
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(previous)
                for event in events:
                    stream.write(encode_event(event))
                stream.flush()
                os.fsync(stream.fileno())
            # Readers see either complete revision; prior bytes are unchanged.
            if source_check is not None:
                source_check()
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def check_append_only(baseline: bytes, current: bytes) -> None:
    """Allow Git union line ordering while rejecting edited/deleted old events."""

    def records(data: bytes) -> dict[str, bytes]:
        result: dict[str, bytes] = {}
        for line in data.splitlines():
            value = json.loads(line)
            key = value["decision_id"]
            if key in result:
                raise ValueError(f"Duplicate decision_id {key}")
            result[key] = line
        return result

    old, new = records(baseline), records(current)
    for key, line in old.items():
        if new.get(key) != line:
            raise ValueError(f"Historical event modified or removed: {key}")
