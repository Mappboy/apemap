"""Shared offline fixtures; complete collection stays enabled by default."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest
import requests

from apemap.db import get_connection
from tests.db_fixtures import DatabaseFactory, build_template, copy_database


@pytest.fixture(autouse=True)
def isolate_ingestion_review_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default ingestion to private authority; explicit review fixtures still replay.

    Reading and snapshot capture must use the same path throughout ingestion,
    even when a developer saves decisions while the test suite is running.
    """
    monkeypatch.setattr(
        "apemap.review.integration.DEFAULT_LOG_PATH", tmp_path / "decisions.jsonl"
    )


@pytest.fixture(autouse=True)
def block_http_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail immediately when a test misses a mock for a live HTTP client."""

    def unexpected_request(*args: object, **kwargs: object) -> None:
        raise AssertionError("Unexpected HTTP request: use a mock or local fixture")

    monkeypatch.setattr(requests.sessions.Session, "request", unexpected_request)


@pytest.fixture(scope="session")
def canonical_db_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Initialize the canonical schema once, without sharing an open connection."""
    return build_template(tmp_path_factory.mktemp("canonical") / "empty.duckdb")


@pytest.fixture
def database_factory(
    tmp_path: Path,
    canonical_db_template: Path,
) -> Iterator[DatabaseFactory]:
    """Open private copies and close all factory-owned connections on teardown."""
    connections: list[duckdb.DuckDBPyConnection] = []

    def create(template: Path | None = None) -> tuple[Path, duckdb.DuckDBPyConnection]:
        path = tmp_path / f"database-{len(connections)}.duckdb"
        copy_database(template or canonical_db_template, path)
        conn = get_connection(path)
        connections.append(conn)
        return path, conn

    try:
        yield create
    finally:
        for conn in reversed(connections):
            conn.close()


@pytest.fixture
def db_conn(database_factory: DatabaseFactory) -> duckdb.DuckDBPyConnection:
    """Provide an empty schema with independent state for each test."""
    return database_factory(None)[1]
