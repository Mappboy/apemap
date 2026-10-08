"""Queue reuse must retain fresh authority and leave other workers available."""

from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier, Event
from typing import Any

import pytest

pytest.importorskip("flask")
pytest.importorskip("waitress")

from apemap.review.gui import create_app
from apemap.review.model import ReviewEvent
from apemap.review.service import ReviewService
from apemap.review.store import encode_event
from tests.test_review_concurrency import mapping, service as service


def test_queue_reuses_rows_but_refreshes_after_save_and_source_edit(
    service: ReviewService, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = service.candidates
    calls = 0

    def counted(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "candidates", counted)
    client = create_app(service).test_client()
    assert client.get("/?entity_type=school&status=pending").status_code == 200
    assert client.get("/?entity_type=school&q=School&page=2").status_code == 200
    assert calls == 1
    service.save(mapping(service, "School A", "acara:3"))
    accepted = client.get("/?entity_type=school&status=accepted")
    assert b"Saved mapping: acara:3" in accepted.data and calls == 2
    path = service.external_dir / "school-location-2025.csv"
    stat = path.stat()
    path.write_bytes(path.read_bytes().replace(b"Target A", b"Target X"))
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert b"1 matching" in client.get("/?status=accepted&q=Target+X").data
    assert calls == 3


def test_changed_inputs_during_queue_build_are_not_cached(
    service: ReviewService, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = service.candidates
    calls = 0

    def changing(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        nonlocal calls
        calls += 1
        rows = original(*args, **kwargs)
        if calls == 1:
            (service.external_dir / "school-extra.csv").write_text("changed")
        return rows

    monkeypatch.setattr(service, "candidates", changing)
    client = create_app(service).test_client()
    for _ in range(3):
        assert client.get("/").status_code == 200
    assert calls == 2


def test_concurrent_queue_build_is_shared_without_blocking_lookup(
    service: ReviewService, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = Event(), Event()
    ready = Barrier(3)
    original = service.candidates
    calls = 0

    def blocked(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "candidates", blocked)
    app = create_app(service)

    def queue() -> int:
        ready.wait(10)
        return app.test_client().get("/").status_code

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(queue) for _ in range(2)]
        ready.wait(10)
        try:
            assert entered.wait(10)
            result = app.test_client().get("/institutions/lookup?q=Target")
            assert result.status_code == 200 and b"Target A" in result.data
            assert not any(future.done() for future in futures)
        finally:
            release.set()
        assert [future.result(15) for future in futures] == [200, 200]
    assert calls == 1


@pytest.mark.parametrize(
    "operation",
    [
        "matcher",
        "_institution_lookup_rows",
        "resolve_institution",
        "institution_resolver",
    ],
)
def test_register_hashing_leaves_other_cache_readers_available(
    service: ReviewService, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    entered, release = Event(), Event()
    original = service._register_revision

    def blocked_first_hash() -> tuple[tuple[str, str], ...]:
        if not entered.is_set():
            entered.set()
            assert release.wait(10)
        return original()

    def read_register() -> Any:
        if operation == "resolve_institution":
            return service.resolve_institution("acara:3")
        return getattr(service, operation)()

    monkeypatch.setattr(service, "_register_revision", blocked_first_hash)
    with ThreadPoolExecutor(2) as pool:
        blocked = pool.submit(read_register)
        try:
            assert entered.wait(10)
            assert pool.submit(service.matcher).result(5).acara_id_map
            assert not blocked.done()
        finally:
            release.set()
        assert blocked.result(15) is not None


def test_event_cache_is_content_bound_and_private(
    service: ReviewService, monkeypatch: pytest.MonkeyPatch
) -> None:
    from apemap.review import service as module

    service.save(mapping(service, "School A", "acara:3"))
    original = module.parse_events
    calls = 0

    def counted(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "parse_events", counted)
    first = service.events()
    first[0].payload["institution_ref"] = "acara:4"
    first[0].supersedes.append("invalid-parent")
    assert service.events()[0].payload["institution_ref"] == "acara:3"
    assert service.events()[0].supersedes == [] and calls == 1
    stat = service.log_path.stat()
    service.log_path.write_bytes(
        service.log_path.read_bytes().replace(b"Confirmed", b"Rechecked")
    )
    os.utime(service.log_path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert service.events()[0].notes == "Rechecked School A" and calls == 2
    service.log_path.write_bytes(b"invalid JSON\n")
    with pytest.raises(ValueError, match="Decision log line"):
        service.events()


def test_permissive_event_cache_never_bypasses_strict_conflict_validation(
    service: ReviewService,
) -> None:
    previews = [mapping(service, "School A", ref) for ref in ("acara:3", "acara:4")]
    service.log_path.write_bytes(
        b"".join(
            encode_event(ReviewEvent.from_dict(preview["event"]))
            for preview in previews
        )
    )
    assert len(service.events(allow_conflicts=True)) == 2
    with pytest.raises(ValueError, match="Conflict"):
        service.events()


def test_assets_are_cached_and_versioned_but_review_pages_are_not(
    service: ReviewService,
) -> None:
    client = create_app(service).test_client()
    page = client.get("/")
    assert page.headers["Cache-Control"] == "no-store"
    assert b"/static/review.css?v=" in page.data
    asset = client.get("/static/review.css")
    assert asset.headers["Cache-Control"] == "private, max-age=3600"
    assert (
        client.get(
            "/static/review.css", headers={"If-None-Match": asset.headers["ETag"]}
        ).status_code
        == 304
    )
