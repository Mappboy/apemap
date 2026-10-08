"""Independent local reviews preserve exact previews and durable ledger writes."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event, Thread
from typing import Any
import urllib.request

import pytest

pytest.importorskip("flask")
pytest.importorskip("waitress")

from waitress.server import create_server

from apemap.cli import app as cli_app
from apemap.review.gui import create_app, _read_preview
from apemap.review.integration import capture_review_sources
from apemap.review.model import (
    education_review_id,
    institution_review_id,
    member_review_id,
    school_review_id,
)
from apemap.review.service import ReviewService
from apemap.review.store import ReviewBusyError, StaleReviewError
from tests.db_fixtures import DatabaseFactory
from tests.test_review_gui import Inputs
from typer.testing import CliRunner

pytestmark = pytest.mark.integration


@pytest.fixture
def service(
    tmp_path: Path, database_factory: DatabaseFactory, monkeypatch: pytest.MonkeyPatch
) -> ReviewService:
    monkeypatch.setattr("apemap.review.service.RAW_APH_DIR", tmp_path / "aph")
    monkeypatch.setattr("apemap.review.service.RAW_WIKIMEDIA_DIR", tmp_path / "wiki")
    path, conn = database_factory(None)
    for index, name in enumerate(("School A", "School B"), 1):
        aph_id = f"TEST{index}"
        conn.execute(
            "INSERT INTO members (member_id, aph_id, family_name, given_name, display_name) VALUES (?, ?, 'Person', 'Test', 'Test Person')",
            [f"member-{index}", aph_id],
        )
        conn.execute(
            "INSERT INTO parliament_service (service_id, member_id, parliament_number, chamber, service_start, party, party_abbrev, state_or_territory) VALUES (?, ?, 48, 'representatives', '2025-07-22', 'Labor', 'ALP', 'TAS')",
            [f"service-{index}", f"member-{index}"],
        )
        conn.execute(
            "INSERT INTO institutions (institution_id, acara_id, school_name, sector) VALUES (?, ?, ?, 'Government')",
            [f"acara-{index}", str(index), name],
        )
        conn.execute(
            "INSERT INTO member_education (education_id, member_id, institution_id, level, attended_status, source_url, retrieved_at, confidence, school_name_as_recorded, evidence_origin) VALUES (?, ?, ?, 'secondary', 'attended_unspecified', 'https://example.org/bio', '2026-10-02T00:00:00+10:00', 'verified', ?, 'aph')",
            [f"edu-{index}", f"member-{index}", f"acara-{index}", name],
        )
    capture_review_sources(conn)
    conn.close()
    external = tmp_path / "external"
    external.mkdir()
    (external / "school-location-2025.csv").write_text(
        "ACARA SML ID,School Name,School Sector,School Type,State,Latitude,Longitude\n"
        "1,School A,Government,Secondary,TAS,-42,147\n"
        "2,School B,Government,Secondary,TAS,-42,147\n"
        "3,Target A,Independent,Secondary,TAS,-42,147\n"
        "4,Target B,Catholic,Secondary,TAS,-42,147\n",
        encoding="utf-8",
    )
    return ReviewService(tmp_path / "decisions.jsonl", path, external)


def mapping(service: ReviewService, name: str, reference: str) -> dict[str, Any]:
    return service.prepare(
        school_review_id(name),
        "map",
        {
            "recorded_name": name,
            "institution_ref": reference,
            "relationship_type": "alias",
        },
        reviewer="Reviewer",
        notes=f"Confirmed {name}",
    )


@pytest.mark.parametrize("concurrent", [False, True])
@pytest.mark.parametrize("database", [False, True])
def test_independent_mappings_save_exact_events(
    service: ReviewService, concurrent: bool, database: bool
) -> None:
    if not database:
        service.db_path = service.db_path.with_name("absent.duckdb")
    previews = [
        mapping(service, "School A", "acara:3"),
        mapping(service, "School B", "acara:4"),
    ]
    assert previews[0]["revision"] == previews[1]["revision"]
    if concurrent:
        ready = Barrier(3)

        def save(preview: dict[str, Any]) -> None:
            ready.wait(10)
            service.save(preview)

        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(save, preview) for preview in previews]
            ready.wait(10)
            for future in futures:
                future.result(20)
    else:
        for preview in previews:
            service.save(preview)
    assert {event.decision_id: event.to_dict() for event in service.events()} == {
        preview["event"]["decision_id"]: preview["event"] for preview in previews
    }


def test_competing_school_and_duplicate_saves_conflict(service: ReviewService) -> None:
    previews = [
        mapping(service, "School A", "acara:3"),
        mapping(service, "School A", "acara:4"),
    ]
    ready = Barrier(3)

    def save(preview: dict[str, Any]) -> str:
        ready.wait(10)
        try:
            service.save(preview)
            return "saved"
        except StaleReviewError:
            return "stale"

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(save, preview) for preview in previews]
        ready.wait(10)
        assert sorted(future.result(20) for future in futures) == ["saved", "stale"]
    baseline = service.log_path.read_bytes()
    for preview in previews:
        with pytest.raises(StaleReviewError):
            service.save(preview)
    assert service.log_path.read_bytes() == baseline


def test_legacy_preview_keeps_strict_revision_check(service: ReviewService) -> None:
    preview = mapping(service, "School A", "acara:3")
    preview.pop("school_guard")
    service.save(mapping(service, "School B", "acara:4"))
    with pytest.raises(StaleReviewError, match="Decision log changed"):
        service.save(preview)


def test_non_school_preview_keeps_strict_revision_check(service: ReviewService) -> None:
    preview = service.prepare(
        member_review_id("TEST1", "gender"),
        "accept",
        {"aph_id": "TEST1", "field": "gender", "value": "Other"},
        source_url="https://example.org/bio",
        reviewer="Reviewer",
    )
    assert "school_guard" not in preview
    service.save(mapping(service, "School B", "acara:4"))
    with pytest.raises(StaleReviewError, match="Decision log changed"):
        service.save(preview)


def test_rewritten_history_rejects_independent_save(service: ReviewService) -> None:
    service.save(mapping(service, "School B", "acara:4"))
    preview = mapping(service, "School A", "acara:3")
    raw = service.log_path.read_bytes()
    # Even a whitespace-only reserialization must not pass the prefix guard.
    service.log_path.write_bytes(
        raw.replace(b'"reviewer":"Reviewer"', b'"reviewer": "Reviewer"')
    )
    with pytest.raises(StaleReviewError, match="history"):
        service.save(preview)


def test_source_edit_rejects_independent_save(service: ReviewService) -> None:
    preview = mapping(service, "School A", "acara:3")
    service.save(mapping(service, "School B", "acara:4"))
    path = service.external_dir / "school-location-2025.csv"
    path.write_bytes(path.read_bytes().replace(b"Target A", b"Target C"))
    with pytest.raises(StaleReviewError, match="Source"):
        service.save(preview)


def test_changed_selected_manual_definition_rejects_save(
    service: ReviewService,
) -> None:
    reference = "manual:overseas"
    payload = {
        "institution_ref": reference,
        "school_name": "Overseas",
        "country": "Ireland",
        "sector": "Other",
    }
    definition = service.save(
        service.prepare(
            institution_review_id(reference),
            "accept",
            payload,
            source_url="https://example.org/school",
            reviewer="Reviewer",
        )
    )
    preview = mapping(service, "School A", reference)
    service.save(
        service.prepare(
            definition.review_id,
            "accept",
            {**payload, "country": "England"},
            source_url="https://example.org/school",
            reviewer="Reviewer",
            supersedes=[definition.decision_id],
        )
    )
    with pytest.raises(StaleReviewError, match="definition"):
        service.save(preview)


def test_changed_education_effects_reject_school_save(service: ReviewService) -> None:
    preview = mapping(service, "School A", "acara:3")
    service.save(
        service.prepare(
            education_review_id("TEST1", "School A"),
            "reject",
            {"aph_id": "TEST1", "recorded_school_name": "School A"},
            reviewer="Reviewer",
            notes="Attendance was mistaken",
        )
    )
    with pytest.raises(StaleReviewError, match="effects"):
        service.save(preview)


def school_form(client: Any, name: str, reference: str) -> dict[str, str]:
    page = client.get(f"/items/{school_review_id(name)}").get_data(as_text=True)
    fields = Inputs(page).values
    return {
        "csrf_token": fields["csrf_token"],
        "form_token": fields["form_token"],
        "school_workflow": "1",
        "payload_mode": "guided",
        "action": "map",
        "field_recorded_name": name,
        "field_institution_ref": reference,
        "field_relationship_type": "alias",
        "reviewer": "Reviewer",
        "notes": "Confirmed mapping",
    }


def preview_token(client: Any, name: str, reference: str) -> dict[str, str]:
    response = client.post(
        f"/items/{school_review_id(name)}/preview",
        data=school_form(client, name, reference),
    )
    assert response.status_code == 200
    fields = Inputs(response.get_data(as_text=True)).values
    return {key: fields[key] for key in ("csrf_token", "preview_token")}


def test_two_clients_can_save_previews_from_same_revision(
    service: ReviewService,
) -> None:
    app = create_app(service)
    clients = [app.test_client(), app.test_client()]
    tokens = [
        preview_token(client, name, reference)
        for client, name, reference in zip(
            clients, ("School A", "School B"), ("acara:3", "acara:4"), strict=True
        )
    ]
    for client, name, token in zip(
        clients, ("School A", "School B"), tokens, strict=True
    ):
        assert (
            client.post(f"/items/{school_review_id(name)}/save", data=token).status_code
            == 303
        )
    assert len(service.events()) == 2


@pytest.mark.parametrize(
    "failure", [ReviewBusyError("busy"), OSError("disk unavailable")]
)
def test_retry_keeps_exact_preview_and_draft(
    service: ReviewService, monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    client = create_app(service).test_client()
    token = preview_token(client, "School A", "acara:3")
    original = service.save

    def fail(preview: dict[str, Any]) -> None:
        raise failure

    monkeypatch.setattr(service, "save", fail)
    response = client.post(f"/items/{school_review_id('School A')}/save", data=token)
    assert response.status_code == 503 and response.headers["Retry-After"] == "1"
    html = response.get_data(as_text=True)
    assert "Confirmed mapping" in html and "Retry saving this decision" in html
    assert Inputs(html).values["preview_token"] == token["preview_token"]
    assert not service.log_path.exists()
    monkeypatch.setattr(service, "save", original)
    assert (
        client.post(
            f"/items/{school_review_id('School A')}/save", data=token
        ).status_code
        == 303
    )


def test_signed_guard_cannot_be_tampered(service: ReviewService) -> None:
    from apemap.review.gui import _sign_preview

    preview = mapping(service, "School A", "acara:3")
    secret = b"test signing secret"
    token = _sign_preview(preview, secret)
    assert _read_preview(token, secret)["school_guard"] == preview["school_guard"]
    import base64
    import json

    encoded, signature = token.split(".")
    signed = json.loads(base64.urlsafe_b64decode(encoded))
    signed["preview"]["school_guard"]["effects"] = "tampered"
    damaged = (
        base64.urlsafe_b64encode(json.dumps(signed).encode()).decode() + "." + signature
    )
    with pytest.raises(ValueError, match="signature"):
        _read_preview(damaged, secret)


def test_failed_replacement_retains_history_and_retry(
    service: ReviewService, monkeypatch: pytest.MonkeyPatch
) -> None:
    service.save(mapping(service, "School B", "acara:4"))
    client = create_app(service).test_client()
    token = preview_token(client, "School A", "acara:3")
    baseline = service.log_path.read_bytes()

    def unavailable(source: str, destination: Path) -> None:
        raise OSError("disk unavailable")

    monkeypatch.setattr("apemap.review.store.os.replace", unavailable)
    response = client.post(f"/items/{school_review_id('School A')}/save", data=token)
    assert response.status_code == 503
    assert "Confirmed mapping" in response.get_data(as_text=True)
    assert (
        Inputs(response.get_data(as_text=True)).values["preview_token"]
        == token["preview_token"]
    )
    assert service.log_path.read_bytes() == baseline
    assert not list(service.log_path.parent.glob("*.tmp"))


def test_conflict_response_retains_draft_without_retry(service: ReviewService) -> None:
    client = create_app(service).test_client()
    token = preview_token(client, "School A", "acara:3")
    service.save(mapping(service, "School A", "acara:4"))
    baseline = service.log_path.read_bytes()
    response = client.post(f"/items/{school_review_id('School A')}/save", data=token)
    html = response.get_data(as_text=True)
    assert response.status_code == 409 and "Confirmed mapping" in html
    assert "Retry saving this decision" not in html and "data-save-school" not in html
    assert service.log_path.read_bytes() == baseline


def test_lookup_completes_while_preview_is_blocked(
    service: ReviewService, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = Event(), Event()
    original = service.semantic_diff

    def blocked(*args: Any, **kwargs: Any) -> dict[str, Any]:
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "semantic_diff", blocked)
    app = create_app(service)
    draft = school_form(app.test_client(), "School A", "acara:3")
    server = create_server(app, host="127.0.0.1", port=0, threads=4)
    thread = Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.effective_port}"

    def preview() -> int:
        import urllib.parse

        request = urllib.request.Request(
            base + f"/items/{school_review_id('School A')}/preview",
            data=urllib.parse.urlencode(draft).encode(),
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status

    try:
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(preview)
            try:
                assert entered.wait(10)
                with urllib.request.urlopen(
                    base + "/institutions/lookup?q=Target", timeout=5
                ) as response:
                    assert response.status == 200
                    assert b"Target A" in response.read()
                assert not future.done()
            finally:
                release.set()
            assert future.result(15) == 200
    finally:
        release.set()
        server.close()
        server.task_dispatcher.shutdown()
        thread.join(5)


def test_concurrent_register_caches_publish_complete_snapshots(
    service: ReviewService,
) -> None:
    ready = Barrier(5)

    def lookup(index: int) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        ready.wait(10)
        matcher = service.matcher()
        matcher.review_blocked_keys.add(f"private-{index}")
        return service.lookup_institutions("Target"), service.institution_resolver()(
            "acara:3"
        )

    with ThreadPoolExecutor(4) as pool:
        futures = [pool.submit(lookup, index) for index in range(4)]
        ready.wait(10)
        results = [future.result(15) for future in futures]
    assert all(result == results[0] for result in results)
    assert len(results[0][0]) == 2
    assert results[0][1] is not None and results[0][1]["school_name"] == "Target A"
    assert not service.matcher().review_blocked_keys
    results[0][0][0]["school_name"] = "Changed response"
    assert service.lookup_institutions("Target")[0]["school_name"] != "Changed response"


def test_worker_cli_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    monkeypatch.setattr(
        "apemap.review.gui.serve", lambda service, **kwargs: seen.update(kwargs)
    )
    runner = CliRunner()
    assert runner.invoke(cli_app, ["review", "serve", "--workers", "2"]).exit_code == 0
    assert seen == {"port": 8765, "workers": 2}
    assert runner.invoke(cli_app, ["review", "serve", "--workers", "0"]).exit_code != 0
