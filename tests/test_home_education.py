"""Home education is sourced attendance without a school identity."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import duckdb
import pytest

from apemap.db import backfill_recorded_school_ids, get_connection, init_schema
from apemap.ingest.matching import SchoolMatcher, is_home_educated
from apemap.ingest.pipeline import run_aph_ingestion
from apemap.review.integration import (
    _individual_source_facts,
    apply_review_events,
    capture_review_sources,
    project_review_records,
)
from apemap.review.candidates import annotate_candidates, build_candidates
from apemap.review.manual_institutions import build_manual_institution_candidate
from apemap.review.model import ReviewEvent, education_review_id, school_review_id
from apemap.review.research_batches import ResearchBatches
from apemap.review.service import ReviewService, _coverage_counts
from apemap.review.store import encode_event
from apemap.validate import validate_database
from tests.test_review_integration import event
from tests.test_review_service import review_service as review_service


STAMP = datetime(2026, 10, 2, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "text", ["Home educated", " HOME\tEducated ", "home\neducated"]
)
def test_home_education_classification(text: str) -> None:
    assert is_home_educated(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Home educated College",
        "Home education",
        "Home-educated",
        "Distance education",
    ],
)
def test_other_descriptions_remain_school_candidates(text: str) -> None:
    assert not is_home_educated(text)


@pytest.fixture
def home_raw() -> dict[str, Any]:
    sample = Path(__file__).parent / "fixtures/aph_sample.json"
    raw = json.loads(sample.read_text(encoding="utf-8"))[0]
    raw["SecondarySchool"] = "Home educated"
    return raw


@pytest.mark.parametrize("mixed", [False, True])
def test_home_education_ingestion_and_source_reconstruction(
    home_raw: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    empty_review_log: Path,
    mixed: bool,
) -> None:
    if mixed:
        home_raw["SecondarySchool"] += " / Actual School"
    calls: list[str] = []
    original = SchoolMatcher.match

    def match(self: SchoolMatcher, name: str) -> Any:
        assert not is_home_educated(name), "Home education must bypass matching"
        calls.append(name)
        return original(self, name)

    monkeypatch.setattr(SchoolMatcher, "match", match)
    db_path = tmp_path / "home.duckdb"
    result = run_aph_ingestion(
        parliaments=[48],
        raw_individuals=[home_raw],
        db_path=db_path,
        output_dir=tmp_path / "output",
        external_dir=tmp_path / "external",
        decision_log_path=empty_review_log,
        retrieved_at=STAMP,
        export_parquet_files=True,
    )
    with get_connection(db_path) as conn:
        cursor = conn.execute(
            "SELECT * FROM member_education WHERE institution_resolution='home_educated'"
        )
        values = cursor.fetchone()
        assert values is not None
        home = dict(zip([col[0] for col in cursor.description], values))
        assert (
            home["institution_id"]
            is home["recorded_school_id"]
            is home["attended_institution_id"]
            is None
        )
        assert home["school_name_as_recorded"] == "Home educated"
        assert home["retrieved_at"] == STAMP
        assert home["evidence_origin"] == "aph"
        assert home["source_url"]
        assert conn.execute("SELECT count(*) FROM institutions").fetchone() == (
            int(mixed),
        )
        assert conn.execute("SELECT count(*) FROM member_education").fetchone() == (
            1 + int(mixed),
        )
        backfill_recorded_school_ids(conn)
        init_schema(conn)
        assert conn.execute(
            "SELECT recorded_school_id FROM member_education WHERE education_id=?",
            [home["education_id"]],
        ).fetchone() == (None,)
        context = conn.execute(
            "SELECT attended_school_id, attended_school_name, identity_basis, attendance_longitude, profile_institution_id, finance_institution_id FROM v_education_attendance_context WHERE education_id=?",
            [home["education_id"]],
        ).fetchone()
        assert context == (None, "Home educated", "non_institution", None, None, None)
        assert conn.execute(
            "SELECT institution_resolution FROM get_parliament_education(48) WHERE education_id=?",
            [home["education_id"]],
        ).fetchone() == ("home_educated",)
        assert conn.execute(
            "SELECT unmatched_education_records FROM v_coverage_metrics WHERE parliament_number=48"
        ).fetchone() == (int(mixed),)
        assert conn.execute(
            "SELECT institution_id FROM read_parquet(?) WHERE education_id=?",
            [str(result["parquet_paths"]["member_education"]), home["education_id"]],
        ).fetchone() == (None,)
    assert result["unmatched_count"] == int(mixed)
    assert "Home educated" not in result["unmatched_csv"].read_text(encoding="utf-8")
    facts = _individual_source_facts(
        home_raw, SchoolMatcher(external_dir=tmp_path / "external"), STAMP
    )
    reconstructed = next(
        row
        for row in facts["member_education"]
        if row["school_name_as_recorded"] == "Home educated"
    )
    for field in (
        "institution_id",
        "institution_resolution",
        "recorded_school_id",
        "source_url",
        "retrieved_at",
        "evidence_origin",
        "confidence",
    ):
        assert reconstructed[field] == home[field]
    assert len(facts["institutions"]) == int(mixed)
    assert calls == (["Actual School", "Actual School"] if mixed else [])


def test_existing_database_allows_null_home_institution_after_upgrade(
    db_conn: duckdb.DuckDBPyConnection,
) -> None:
    db_conn.execute(
        "ALTER TABLE member_education ALTER COLUMN institution_id SET NOT NULL"
    )
    db_conn.execute(
        "INSERT INTO members (member_id, family_name, given_name, display_name) VALUES ('school', 'School', 'Person', 'School Person')"
    )
    db_conn.execute(
        "INSERT INTO institutions (institution_id, school_name) VALUES ('school', 'Actual School')"
    )
    db_conn.execute(
        "INSERT INTO member_education (education_id, member_id, institution_id, level, attended_status, source_url, retrieved_at, confidence) VALUES ('school', 'school', 'school', 'secondary', 'attended_unspecified', 'https://example.org/school', ?, 'verified')",
        [STAMP],
    )
    db_conn.execute("CREATE INDEX education_member ON member_education(member_id)")
    for _ in range(2):
        init_schema(db_conn)
    assert db_conn.execute(
        "SELECT institution_id FROM member_education"
    ).fetchone() == ("school",)
    assert db_conn.execute(
        "SELECT count(*) FROM duckdb_indexes() WHERE index_name='education_member'"
    ).fetchone() == (1,)
    db_conn.execute(
        "INSERT INTO members (member_id, family_name, given_name, display_name) VALUES ('home', 'Home', 'Person', 'Home Person')"
    )
    db_conn.execute(
        "INSERT INTO member_education (education_id, member_id, institution_id, level, attended_status, source_url, retrieved_at, confidence, school_name_as_recorded, institution_resolution) VALUES ('home', 'home', NULL, 'secondary', 'attended_unspecified', 'https://example.org/source', ?, 'unconfirmed', 'Home educated', 'home_educated')",
        [STAMP],
    )
    with pytest.raises(duckdb.ConstraintException):
        db_conn.execute(
            "UPDATE member_education SET institution_id='missing' WHERE education_id='home'"
        )


@pytest.fixture
def home_service(review_service: ReviewService) -> ReviewService:
    with get_connection(review_service.db_path) as conn:
        conn.execute(
            "UPDATE member_education SET school_name_as_recorded='Home educated', institution_id=NULL, institution_resolution='home_educated', recorded_school_id=NULL, confidence='unconfirmed'"
        )
        capture_review_sources(conn)
    return review_service


def home_events() -> list[ReviewEvent]:
    attendance = event(
        "member_education",
        {
            "aph_id": "TEST",
            "recorded_school_name": "Home educated",
            "attended_status": "attended_unspecified",
            "confidence": "verified",
            "retrieved_at": STAMP.isoformat(),
        },
    )
    research = replace(
        attendance,
        decision_id="research",
        action="supersede",
        replacement_action="research",
        notes="Home education has no suitable institutional candidate",
        payload={
            "aph_id": "TEST",
            "recorded_school_name": "Home educated",
            "resolution_only": True,
            "resolution_reason": "no_suitable_candidate",
        },
        supersedes=[attendance.decision_id],
    )
    school = replace(
        event("school", {"recorded_name": "Home educated"}, "school-research"),
        action="research",
        notes="Preserved school research predating home education classification",
    )
    return [attendance, research, school]


@pytest.mark.parametrize("reviewed", ["source", "accepted", "research"])
def test_replay_preserves_home_attendance_and_bypasses_matcher(
    home_service: ReviewService,
    monkeypatch: pytest.MonkeyPatch,
    reviewed: str,
) -> None:
    events = (
        []
        if reviewed == "source"
        else home_events()[:1]
        if reviewed == "accepted"
        else home_events()
    )
    matcher = home_service.matcher()

    def unexpected(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Home education must not resolve to an institution")

    monkeypatch.setattr(matcher, "match", unexpected)
    with get_connection(home_service.db_path) as conn:
        first = project_review_records(conn, events, matcher)
        home = first["member_education"][0]
        assert home["institution_id"] is home["recorded_school_id"] is None
        assert home["institution_resolution"] == "home_educated"
        assert not any(
            is_home_educated(row["school_name"]) for row in first["institutions"]
        )
        counts = _coverage_counts(first)
        assert counts["unresolved_institutions"] == 0
        assert counts["members_with_resolved_education"] == 1
        if reviewed != "source":
            assert home["source_url"] == events[0].source_url
            assert home["retrieved_at"] == STAMP
            assert home["confidence"] == "verified"
            assert home["reviewer_notes"] == events[0].notes
        else:
            assert home["confidence"] == "unconfirmed"
        apply_review_events(conn, events=events, matcher=matcher)
        assert project_review_records(conn, events, matcher) == first


def test_stale_school_candidates_cannot_reenter_home_review_workflows(
    home_service: ReviewService,
    tmp_path: Path,
) -> None:
    events = home_events()
    home_service.log_path.write_bytes(b"".join(encode_event(item) for item in events))
    before = home_service.log_path.read_bytes()
    cache = tmp_path / "cache/institutions"
    cache.mkdir(parents=True)
    (cache / "home.json").write_text(
        json.dumps(
            {
                "raw_school_text": "Home educated",
                "suggested_institution_name": "Home educated",
                "status": "candidate",
                "source_url": "https://example.org/home",
            }
        ),
        encoding="utf-8",
    )
    school_id = school_review_id("Home educated")
    education_id = education_review_id("TEST", "Home educated")
    with get_connection(home_service.db_path) as conn:
        rows = annotate_candidates(
            build_candidates(conn, cache_dir=cache.parent), events
        )
        assert not [row for row in rows if row["entity_type"] == "school"]
        assert (
            next(row for row in rows if row["review_id"] == education_id)["status"]
            == "accepted"
        )
        assert build_candidates(conn, cache_dir=cache.parent, review_id=school_id) == []
    detail = home_service.show(education_id)
    assert detail["status"] == "accepted"
    assert detail["context"]["institution_resolution"] == "home_educated"
    assert detail["ranked_candidates"] == []
    assert home_service.rank_school_candidates(school_id, "Home educated") == []
    assert home_service.member("TEST")["education"][0]["status"] == "accepted"
    batches = ResearchBatches(home_service)
    with pytest.raises(ValueError, match="No eligible unresolved cases"):
        batches.prepare("schools", "schools")
    with pytest.raises(ValueError, match="Every requested review case must exist"):
        batches.prepare(
            "exact-home",
            "schools",
            review_ids=[education_id],
            _origin="agent-session",
            _provenance={
                "generated_by": "codex-session",
                "model": None,
                "token_usage": None,
                "availability_note": "Fixture has no model usage",
            },
        )
    with pytest.raises(ValueError, match="Home education has no institution"):
        batches.prepare_manual_proposal(
            "manual-home", school_id, {"name": "Home educated"}, []
        )
    assert home_service.log_path.read_bytes() == before


def test_home_education_has_no_manual_institution() -> None:
    with pytest.raises(ValueError, match="Home education has no institution"):
        build_manual_institution_candidate({"name": "HOME  EDUCATED"}, [], [])


def test_review_ui_labels_home_education_resolved(home_service: ReviewService) -> None:
    pytest.importorskip("flask")
    from apemap.review.gui import create_app

    home_service.log_path.write_bytes(
        b"".join(encode_event(item) for item in home_events())
    )
    with create_app(home_service).test_client() as client:
        response = client.get("/items/" + education_review_id("TEST", "Home educated"))
    assert response.status_code == 200
    assert "Resolved — Home educated" in response.get_data(as_text=True)


def test_rejecting_home_attendance_does_not_show_resolved_label(
    home_service: ReviewService,
) -> None:
    pytest.importorskip("flask")
    from apemap.review.gui import create_app

    rejection = replace(
        home_events()[0],
        action="reject",
        notes="Attendance claim withdrawn",
        payload={"aph_id": "TEST", "recorded_school_name": "Home educated"},
    )
    home_service.log_path.write_bytes(encode_event(rejection))
    with get_connection(home_service.db_path) as conn:
        assert project_review_records(conn, [rejection])["member_education"] == []
    with create_app(home_service).test_client() as client:
        response = client.get("/items/" + rejection.review_id)
    assert response.status_code == 200
    assert "Resolved — Home educated" not in response.get_data(as_text=True)


@pytest.mark.parametrize(
    "resolution,valid", [("home_educated", True), ("unresolved", False), (None, False)]
)
def test_only_home_education_allows_a_null_institution(
    home_service: ReviewService,
    resolution: str | None,
    valid: bool,
) -> None:
    with get_connection(home_service.db_path) as conn:
        conn.execute(
            "UPDATE member_education SET institution_resolution=?", [resolution]
        )
        report = validate_database(conn, [48])
    failures = [
        failure
        for failure in report.failures
        if "member_education -> institutions" in failure
    ]
    assert bool(failures) is not valid
