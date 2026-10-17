"""Saved research findings remain visible without becoming guided approvals."""

import json
from html.parser import HTMLParser
from pathlib import Path
import shutil
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

pytest.importorskip("flask", reason="Install the review-ui extra for GUI tests")

from apemap.constants import PROJECT_ROOT
from apemap.review.gui import create_app
from apemap.review.research_batches import ResearchBatches
from tests.test_research_batch_ui import Controls, prepare_ui
from tests.test_research_batches import batches as batches

PACKET_ID = "p48-final-schools-20261015"
CONTEXT = {
    "queue_view": ["schools"],
    "queue_entity": ["school"],
    "queue_unmatched_schools": ["1"],
    "queue_page": ["1"],
}


class Links(HTMLParser):
    def __init__(self, html: str) -> None:
        super().__init__()
        self.hrefs: list[str] = []
        self.feed(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        href = dict(attrs).get("href")
        if tag == "a" and href:
            self.hrefs.append(href)


@pytest.fixture
def packet(batches: ResearchBatches) -> Path:
    source = PROJECT_ROOT / "data/reference/review/research" / PACKET_ID
    destination = batches.path(PACKET_ID)
    destination.mkdir(parents=True)
    metadata = json.loads((source / "packet.json").read_bytes())
    for name in ("packet.json", *metadata["files"]):
        shutil.copyfile(source / name, destination / name)
    return destination


def test_final_packet_is_visible_with_exact_review_links_and_no_apply_controls(
    batches: ResearchBatches, packet: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepare_ui(batches, monkeypatch, "schools")
    frozen = {path.name: path.read_bytes() for path in packet.iterdir()}
    authority = {
        path: path.read_bytes() if path.exists() else None
        for path in (batches.service.log_path, batches.service.evidence_path)
    }
    with create_app(batches.service).test_client() as client:
        index = client.get("/research-batches", query_string=CONTEXT)
        assert index.status_code == 200
        html = index.get_data(as_text=True)
        assert PACKET_ID in html
        assert "/research-batches/ui?" in html
        href = next(
            href
            for href in Links(html).hrefs
            if urlsplit(href).path == "/research-packets/" + PACKET_ID
        )
        assert parse_qs(urlsplit(href).query) == CONTEXT
        response = client.get(href)
        assert response.status_code == 200
        detail = response.get_data(as_text=True)
        links = Links(detail).hrefs
        for result in json.loads((packet / "results.json").read_bytes()):
            assert result["member"] in detail
            assert result["recorded_school_name"] in detail
            review_link = next(
                href
                for href in links
                if unquote(urlsplit(href).path) == "/items/" + result["review_id"]
            )
            assert parse_qs(urlsplit(review_link).query) == CONTEXT
            for evidence in result["evidence"]:
                assert evidence["evidence_id"] in detail
        assert "acara:47888" in detail
        assert "manual:ferntree-gully-technical-school" in detail
        assert "Home educated" in detail
        assert "<form" not in detail
        token = Controls(html).inputs["csrf_token"]["value"]
        assert client.post(href, data={"csrf_token": token}).status_code == 405
    assert frozen == {path.name: path.read_bytes() for path in packet.iterdir()}
    assert authority == {
        path: path.read_bytes() if path.exists() else None for path in authority
    }


def test_changed_packet_is_reported_and_cannot_be_opened(
    batches: ResearchBatches, packet: Path
) -> None:
    results = packet / "results.json"
    results.write_bytes(results.read_bytes() + b" ")
    with create_app(batches.service).test_client() as client:
        index = client.get("/research-batches")
        assert index.status_code == 200
        assert b"Research packet file changed: results.json" in index.data
        detail = client.get("/research-packets/" + PACKET_ID)
        assert detail.status_code == 400
        assert b"Research packet file changed: results.json" in detail.data


@pytest.mark.parametrize("line_ending", [b"\n", b"\r\n"])
def test_packet_hashes_survive_checkout_line_endings(
    batches: ResearchBatches, packet: Path, line_ending: bytes
) -> None:
    metadata = json.loads((packet / "packet.json").read_bytes())
    for name in metadata["files"]:
        path = packet / name
        path.write_bytes(
            path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", line_ending)
        )
    with create_app(batches.service).test_client() as client:
        assert client.get("/research-packets/" + PACKET_ID).status_code == 200


def test_missing_research_packet_returns_not_found(batches: ResearchBatches) -> None:
    with create_app(batches.service).test_client() as client:
        assert client.get("/research-packets/absent").status_code == 404
