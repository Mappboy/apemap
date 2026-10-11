"""Terminal feedback for offline review requests and background operations."""

from __future__ import annotations

import logging
from threading import Event
from typing import Any

import pytest

from apemap.review.diagnostics import Operation, operation, terminal_logging, timed
from apemap.review.research import ResearchJobs


def test_terminal_logging_flushes_and_restores_configuration(
    capsys: pytest.CaptureFixture[str],
) -> None:
    loggers = [logging.getLogger(name) for name in ("apemap.review", "waitress")]
    original = [(item.level, item.propagate, list(item.handlers)) for item in loggers]
    root = logging.getLogger()
    root_state = (root.level, list(root.handlers))
    for _ in range(2):
        with pytest.raises(RuntimeError), terminal_logging():
            logging.getLogger("apemap.review.gui").info("Review feedback")
            logging.getLogger("waitress.queue").warning("Task queue depth is 1")
            # A StreamHandler flush makes output visible before server shutdown.
            output = capsys.readouterr().err
            assert output.count("INFO apemap.review.gui: Review feedback") == 1
            assert "WARNING waitress.queue: Task queue depth is 1" in output
            raise RuntimeError("fixture")
        assert [
            (item.level, item.propagate, list(item.handlers)) for item in loggers
        ] == original
        assert (root.level, list(root.handlers)) == root_state


def test_long_running_operation_reports_before_completion_and_stops(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="apemap.review")
    monkeypatch.setattr("apemap.review.diagnostics.PROGRESS_INTERVAL", 0.01)
    reported = Event()

    class ProgressHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if "still running" in record.getMessage():
                reported.set()

    handler = ProgressHandler()
    log = logging.getLogger("apemap.review.diagnostics")
    log.addHandler(handler)
    activity = Operation("Fixture work")
    try:
        assert reported.wait(5), "Progress must appear while work is still running"
        assert "finished" not in caplog.text
    finally:
        activity.finish()
        log.removeHandler(handler)
    assert activity.monitor is not None and not activity.monitor.is_alive()
    assert "Fixture work finished in" in caplog.text
    # Flask teardown can run after after_request; completion must appear once.
    activity.finish(status=200)
    assert caplog.text.count("Fixture work finished in") == 1


def test_slow_stage_warns_and_keeps_return_value(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="apemap.review")
    times = iter([100.0, 101.25])
    monkeypatch.setattr(
        "apemap.review.diagnostics.time.perf_counter", lambda: next(times)
    )

    @timed("Hashing fixture sources")
    def work(value: int) -> int:
        return value + 1

    assert work(2) == 3
    assert "Hashing fixture sources finished in 1.25s" in caplog.text
    assert caplog.records[-1].levelno == logging.WARNING


def test_operation_error_retains_type_and_location_but_redacts_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="apemap.review")
    with pytest.raises(ValueError, match="private-test-secret"):
        with operation("Fixture validation"):
            raise ValueError("provider body private-test-secret")
    assert "Fixture validation failed: ValueError" in caplog.text
    assert "test_review_diagnostics.py:" in caplog.text
    assert "private-test-secret" not in caplog.text
    assert "provider body" not in caplog.text


def test_research_failure_is_logged_once_without_polling_or_provider_values(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("APEMAP_OFFLINE", raising=False)
    caplog.set_level(logging.INFO, logger="apemap.review")

    class BrokenProvider:
        def search(self, context: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("private-test-secret upstream response")

    jobs = ResearchJobs({"fixture": BrokenProvider()})
    try:
        identifier = jobs.start("fixture", {"review_id": "fixture"})
        with pytest.raises(RuntimeError):
            jobs.get(identifier, "fixture").future.result(timeout=5)
        assert f"Research job {identifier} queued for provider fixture" in caplog.text
        assert f"Research job {identifier} failed: RuntimeError" in caplog.text
        for _ in range(2):
            assert jobs.result(identifier, "fixture", {})["status"] == "failed"
        assert caplog.text.count("failed: RuntimeError") == 1
        assert "private-test-secret" not in caplog.text
        assert "upstream response" not in caplog.text
    finally:
        jobs.close()


def test_request_feedback_includes_status_and_omits_request_contents(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pytest.importorskip("flask")
    pytest.importorskip("waitress")
    from apemap.review.gui import create_app
    from tests.test_review_gui import FixtureService

    caplog.set_level(logging.INFO, logger="apemap.review")
    app = create_app(FixtureService())
    client = app.test_client()
    try:
        response = client.get("/institutions/lookup?q=private-query&token=secret-token")
        assert response.status_code == 200
        assert "Request GET lookup_institutions started" in caplog.text
        assert "HTTP 200" in caplog.text
        assert "finished in" in caplog.text
        assert "private-query" not in caplog.text
        assert "secret-token" not in caplog.text
        caplog.clear()
        response = client.post(
            "/items/private-id/save", data={"payload": "private-body"}
        )
        assert response.status_code == 403
        assert "HTTP 403" in caplog.text
        assert caplog.records[-1].levelno == logging.WARNING
        assert "private-id" not in caplog.text
        assert "private-body" not in caplog.text
        caplog.clear()
        assert client.get("/static/review.css").status_code == 200
        assert not caplog.records
        # Trusted-host rejection can happen before any before_request hook.
        assert client.get("/", headers={"Host": "untrusted.example"}).status_code == 400
    finally:
        app.extensions["research_jobs"].close()


def test_unhandled_http_failure_reports_safe_stack_and_preserves_error_response(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pytest.importorskip("flask")
    pytest.importorskip("waitress")
    from apemap.review.gui import create_app
    from tests.test_review_gui import FixtureService

    caplog.set_level(logging.INFO, logger="apemap.review")
    app = create_app(FixtureService())

    @app.get("/fixture-failure")
    def failure() -> str:
        raise RuntimeError("private-test-secret")

    try:
        assert app.test_client().get("/fixture-failure").status_code == 500
        assert "HTTP 500" in caplog.text
        assert "failed: RuntimeError" in caplog.text
        assert "in failure" in caplog.text
        assert "private-test-secret" not in caplog.text
        assert caplog.text.count("failed: RuntimeError") == 1
    finally:
        app.extensions["research_jobs"].close()


@pytest.mark.parametrize("fail", [False, True])
def test_server_start_stop_and_bind_errors_are_visible_and_jobs_are_closed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], fail: bool
) -> None:
    pytest.importorskip("flask")
    pytest.importorskip("waitress")
    from apemap.review.gui import create_app, serve
    from tests.test_review_gui import FixtureService

    app = create_app(FixtureService())
    closed = Event()
    monkeypatch.setattr(app.extensions["research_jobs"], "close", closed.set)
    monkeypatch.setattr("apemap.review.gui.create_app", lambda service: app)

    def server(application: Any, **kwargs: Any) -> None:
        output = capsys.readouterr().err
        assert "Initializing review UI started" in output
        assert "http://127.0.0.1:8766 with 2 workers" in output
        assert "press Ctrl+C to stop" in output
        if fail:
            raise OSError("private-test-secret bind failure")

    monkeypatch.setattr("apemap.review.gui.waitress_serve", server)
    if fail:
        with pytest.raises(OSError):
            serve(FixtureService(), port=8766, workers=2)
    else:
        serve(FixtureService(), port=8766, workers=2)
    output = capsys.readouterr().err
    assert "Review server stopped" in output
    assert closed.is_set()
    if fail:
        assert "Review server failed: OSError" in output
        assert "private-test-secret" not in output
