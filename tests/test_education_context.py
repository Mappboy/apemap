"""Offline historical-school identity, consensus, and upgrade regressions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pytest

from apemap.db import get_connection, init_schema
from apemap.education_context import CONTEXT_FIELDS
from apemap.review.model import school_review_id


@pytest.fixture
def context_db(db_conn: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    db_conn.execute(
        "INSERT INTO members (member_id, family_name, given_name, display_name) VALUES ('person', 'Person', 'One', 'One Person')"
    )
    db_conn.executemany(
        "INSERT INTO institutions (institution_id, school_name, sector, longitude, latitude) VALUES (?, ?, ?, ?, ?)",
        [
            ("original", "Original School", "Government", 130, -20),
            ("successor", "Successor College", "Independent", 140, -30),
            ("other-successor", "Other Successor", "Government", 141, -31),
        ],
    )
    return db_conn


def education(
    conn: duckdb.DuckDBPyConnection,
    name: str | None,
    identifier: str = "one",
    **extra: Any,
) -> None:
    values = {
        "education_id": identifier,
        "member_id": "person",
        "institution_id": "successor",
        "level": "secondary",
        "attended_status": "attended_unspecified",
        "source_url": "https://example.org/attendance",
        "retrieved_at": "2026-10-01T00:00:00+00:00",
        "confidence": "verified",
        "school_name_as_recorded": name,
        "institution_resolution": "successor",
        **extra,
    }
    conn.execute(
        f"INSERT INTO member_education ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})",
        list(values.values()),
    )
    init_schema(conn)


def row(conn: duckdb.DuckDBPyConnection, identifier: str = "one") -> dict[str, Any]:
    result = conn.execute(
        "SELECT * FROM v_education_attendance_context WHERE education_id = ?",
        [identifier],
    )
    values = result.fetchone()
    assert values is not None
    return dict(zip([item[0] for item in result.description], values))


ORIGINAL: dict[str, Any] = {
    "attended_institution_id": "original",
    "attended_identity_source_url": "https://example.org/identity",
    "historical_scope_confirmed": True,
}


def test_original_identity_does_not_verify_register_location_or_sector(
    context_db: duckdb.DuckDBPyConnection,
) -> None:
    education(context_db, "Old School", **ORIGINAL)
    actual = row(context_db)
    assert actual["attended_school_id"] == "original"
    assert actual["attended_school_name"] == "Original School"
    assert actual["resolved_institution_id"] == "successor"
    assert actual["location_basis"] == "successor_unverified"
    assert actual["display_longitude"] == 140
    assert actual["attendance_longitude"] is None
    assert actual["attendance_location_eligible"] is False
    assert actual["broad_sector"] == "Non-government"
    assert actual["sector_basis"] == "successor_assumption"
    assert actual["detailed_sector"] is None
    assert actual["profile_basis"] == actual["finance_basis"] == "successor_context"


def test_verified_school_wide_facts_apply_across_original_aliases(
    context_db: duckdb.DuckDBPyConnection,
) -> None:
    education(
        context_db,
        "Old School",
        **ORIGINAL,
        historical_latitude=-25,
        historical_longitude=135,
        historical_location_source_url="https://example.org/location",
        historical_broad_sector="Government",
        historical_broad_sector_source_url="https://example.org/sector",
    )
    education(context_db, "Old School alias", "two", **ORIGINAL)
    for identifier in ("one", "two"):
        actual = row(context_db, identifier)
        assert actual["location_basis"] == "original_verified"
        assert actual["attendance_longitude"] == 135
        assert actual["broad_sector"] == actual["detailed_sector"] == "Government"
        assert (
            actual["sector_basis"]
            == actual["detailed_sector_basis"]
            == "historical_verified"
        )
        assert actual["continuity_discrepancy"] is True
        assert actual["sector_source_url"] == "https://example.org/sector"


@pytest.mark.parametrize(
    "campus,eligible", [("same_campus", True), ("different_campus", False)]
)
def test_campus_continuity_has_its_own_evidence(
    context_db: duckdb.DuckDBPyConnection, campus: str, eligible: bool
) -> None:
    education(
        context_db,
        "Old School",
        **ORIGINAL,
        campus_continuity=campus,
        campus_continuity_source_url="https://example.org/campus",
    )
    actual = row(context_db)
    assert actual["attendance_location_eligible"] is eligible
    assert actual["location_basis"] == (
        "successor_verified_same_campus" if eligible else "successor_unverified"
    )


def test_historical_conflicts_suppress_only_affected_dimensions(
    context_db: duckdb.DuckDBPyConnection,
) -> None:
    for identifier, name, detail, longitude in (
        ("one", "Old School", "Catholic", 135),
        ("two", "Old alias", "Independent", 136),
    ):
        education(
            context_db,
            name,
            identifier,
            **ORIGINAL,
            historical_latitude=-25,
            historical_longitude=longitude,
            historical_location_source_url="https://example.org/location",
            historical_broad_sector="Non-government",
            historical_broad_sector_source_url="https://example.org/broad",
            historical_detailed_sector=detail,
            historical_detailed_sector_source_url="https://example.org/detail",
        )
    actual = row(context_db)
    assert actual["location_conflict"] is actual["sector_conflict"] is True
    assert actual["display_longitude"] is actual["attendance_longitude"] is None
    assert actual["broad_sector"] == "Non-government"
    assert actual["detailed_sector"] is None
    assert actual["sector_basis"] == "historical_verified"
    assert actual["detailed_sector_basis"] == "unresolved"


def test_contradictory_successor_assumptions_need_review(
    context_db: duckdb.DuckDBPyConnection,
) -> None:
    education(context_db, "Old School", **ORIGINAL)
    education(
        context_db, "Old alias", "two", **ORIGINAL, institution_id="other-successor"
    )
    actual = row(context_db)
    assert actual["sector_conflict"] is True
    assert actual["broad_sector"] is actual["detailed_sector"] is None
    assert actual["sector_basis"] == "unresolved"


def test_frozen_recorded_identity_survives_unicode_backfill(
    context_db: duckdb.DuckDBPyConnection,
) -> None:
    name = "Ｓｔ. Márie's C of E & Mt. School"
    education(context_db, name)
    identity = school_review_id(name)
    assert row(context_db)["attended_school_id"] == identity
    init_schema(context_db)
    assert row(context_db)["attended_school_id"] == identity
    education(context_db, None, "missing")
    assert row(context_db, "missing")["attended_school_id"] is None


def test_verified_original_location_wins_over_continuity_disagreement(
    context_db: duckdb.DuckDBPyConnection,
) -> None:
    for identifier, campus in (("one", "same_campus"), ("two", "different_campus")):
        education(
            context_db,
            f"Old School {identifier}",
            identifier,
            **ORIGINAL,
            historical_latitude=-25,
            historical_longitude=135,
            historical_location_source_url="https://example.org/location",
            campus_continuity=campus,
            campus_continuity_source_url="https://example.org/campus",
        )
    actual = row(context_db)
    assert actual["campus_continuity_conflict"] is True
    assert actual["location_conflict"] is False
    assert actual["location_basis"] == "original_verified"
    assert actual["attendance_longitude"] == 135


def test_detailed_historical_evidence_implies_broad_and_detects_cross_alias_conflict(
    context_db: duckdb.DuckDBPyConnection,
) -> None:
    education(
        context_db,
        "Old School",
        **ORIGINAL,
        historical_detailed_sector="Catholic",
        historical_detailed_sector_source_url="https://example.org/catholic",
    )
    actual = row(context_db)
    assert actual["broad_sector"] == "Non-government"
    assert actual["sector_basis"] == "historical_verified"
    assert actual["sector_source_url"] == "https://example.org/catholic"
    education(
        context_db,
        "Old alias",
        "two",
        **ORIGINAL,
        historical_broad_sector="Government",
        historical_broad_sector_source_url="https://example.org/government",
    )
    actual = row(context_db)
    assert actual["sector_conflict"] is True
    assert actual["broad_sector"] is actual["detailed_sector"] is None


def test_original_reference_survives_contradictory_successor_assumption(
    context_db: duckdb.DuckDBPyConnection,
) -> None:
    education(context_db, "Old School", **ORIGINAL)
    education(
        context_db,
        "Original School",
        "two",
        institution_id="original",
        institution_resolution="direct",
    )
    assert row(context_db)["broad_sector"] is None
    assert row(context_db)["sector_conflict"] is True
    actual = row(context_db, "two")
    assert actual["broad_sector"] == actual["detailed_sector"] == "Government"
    assert actual["sector_basis"] == "original_reference"


def test_old_review_source_schema_upgrade_preserves_rows(
    context_db: duckdb.DuckDBPyConnection,
) -> None:
    education(context_db, "Old School")
    excluded = ", ".join(CONTEXT_FIELDS)
    context_db.execute(
        f"CREATE TABLE review_source_member_education AS SELECT * EXCLUDE ({excluded}) FROM member_education"
    )
    before = context_db.execute(
        "SELECT education_id, school_name_as_recorded FROM review_source_member_education"
    ).fetchall()
    for _ in range(2):
        init_schema(context_db)
        assert (
            context_db.execute(
                "SELECT education_id, school_name_as_recorded FROM review_source_member_education"
            ).fetchall()
            == before
        )
        assert context_db.execute(
            "SELECT recorded_school_id, historical_scope_confirmed FROM review_source_member_education"
        ).fetchone() == (school_review_id("Old School"), None)


def test_context_view_reopens_without_python_functions(tmp_path: Path) -> None:
    path = tmp_path / "portable.duckdb"
    with get_connection(path) as conn:
        init_schema(conn)
    with duckdb.connect(str(path), read_only=True) as conn:
        assert conn.execute(
            "SELECT count(*) FROM v_education_attendance_context"
        ).fetchone() == (0,)
