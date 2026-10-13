"""Regression coverage for independent database copies and offline test clients."""

from __future__ import annotations

import hashlib
from pathlib import Path

import duckdb
import pytest
import requests

from tests.db_fixtures import DatabaseFactory


@pytest.mark.unit
def test_fixture_thread_budget_preserves_explicit_connection_settings() -> None:
    with duckdb.connect() as conn:
        assert conn.execute("SELECT current_setting('threads')").fetchone() == (1,)
    settings = {"threads": 2, "enable_external_access": False}
    with duckdb.connect(config=settings) as conn:
        assert conn.execute("SELECT current_setting('threads')").fetchone() == (2,)
        assert conn.execute(
            "SELECT current_setting('enable_external_access')"
        ).fetchone() == (False,)
    assert settings == {"threads": 2, "enable_external_access": False}


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


@pytest.mark.integration
def test_review_database_copies_keep_seed_and_source_snapshot_private(
    review_db_template: Path,
    database_factory: DatabaseFactory,
) -> None:
    before = hashlib.sha256(review_db_template.read_bytes()).digest()
    first_path, first = database_factory(review_db_template)
    second_path, second = database_factory(review_db_template)
    assert first_path != second_path
    assert first.execute(
        "SELECT display_name FROM members WHERE member_id = 'aph-test'"
    ).fetchone() == ("Test Person",)
    assert first.execute(
        "SELECT display_name FROM review_source_members WHERE member_id = 'aph-test'"
    ).fetchone() == ("Test Person",)

    first.execute(
        "UPDATE members SET display_name = 'First copy' WHERE member_id = 'aph-test'"
    )
    first.execute(
        "UPDATE review_source_members SET display_name = 'First copy' "
        "WHERE member_id = 'aph-test'"
    )

    assert first.execute(
        "SELECT display_name FROM members WHERE member_id = 'aph-test'"
    ).fetchone() == ("First copy",)
    assert first.execute(
        "SELECT display_name FROM review_source_members WHERE member_id = 'aph-test'"
    ).fetchone() == ("First copy",)
    assert second.execute(
        "SELECT display_name FROM members WHERE member_id = 'aph-test'"
    ).fetchone() == ("Test Person",)
    assert second.execute(
        "SELECT display_name FROM review_source_members WHERE member_id = 'aph-test'"
    ).fetchone() == ("Test Person",)
    assert hashlib.sha256(review_db_template.read_bytes()).digest() == before


@pytest.mark.unit
def test_unmocked_http_fails_immediately() -> None:
    with requests.Session() as session:
        with pytest.raises(AssertionError, match="Unexpected HTTP request"):
            session.get("https://example.invalid/apemap-test", timeout=1)
