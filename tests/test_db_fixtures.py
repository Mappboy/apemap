"""Regression coverage for independent database copies and offline test clients."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import requests

from tests.db_fixtures import DatabaseFactory


@pytest.mark.unit
def test_database_copies_preserve_template_and_isolate_mutations(
    canonical_db_template: Path,
    database_factory: DatabaseFactory,
) -> None:
    before = hashlib.sha256(canonical_db_template.read_bytes()).digest()
    first_path, first = database_factory(None)
    second_path, second = database_factory(None)
    assert first_path != second_path
    first.execute(
        "INSERT INTO members (member_id, family_name, given_name, display_name) "
        "VALUES ('test-member', 'Test', 'Member', 'Test Member')"
    )
    assert first.execute("SELECT count(*) FROM members").fetchone() == (1,)
    assert second.execute("SELECT count(*) FROM members").fetchone() == (0,)
    first.close()
    _, third = database_factory(None)
    assert third.execute("SELECT count(*) FROM members").fetchone() == (0,)
    assert hashlib.sha256(canonical_db_template.read_bytes()).digest() == before


@pytest.mark.unit
def test_unmocked_http_fails_immediately() -> None:
    with requests.Session() as session:
        with pytest.raises(AssertionError, match="Unexpected HTTP request"):
            session.get("https://example.invalid/apemap-test", timeout=1)
