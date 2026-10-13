"""Member candidate builds share one validated, build-local ledger replay."""

from dataclasses import replace
import json
from pathlib import Path
from typing import Any

import duckdb
import pytest

from apemap.review import candidates, member_comparison
from apemap.review.model import ReviewEvent, active_heads, member_review_id


def member_event(aph_id: str, field: str, value: Any) -> ReviewEvent:
    return ReviewEvent.from_dict(
        {
            "decision_id": f"{aph_id}-{field}",
            "review_id": member_review_id(aph_id, field),
            "entity_type": "member",
            "action": "accept",
            "payload": {"aph_id": aph_id, "field": field, "value": value},
            "source_url": "https://example.org/bio",
            "reviewer": "Reviewer",
            "reviewed_at": "2026-10-13",
            "recorded_at": "2026-10-13T00:00:00Z",
        }
    )


@pytest.fixture
def member_events() -> list[ReviewEvent]:
    accepted = member_event("A", "gender", "Female")
    retained = replace(
        accepted,
        decision_id="retained",
        action="supersede",
        replacement_action="reject",
        notes="Reject the proposal while retaining the accepted baseline",
        supersedes=[accepted.decision_id],
        payload={
            "aph_id": "A",
            "field": "gender",
            "value": None,
            "proposal_only": True,
            "retained_decision_id": accepted.decision_id,
        },
    )
    conflict = member_event("B", "gender", "Male")
    return [
        accepted,
        retained,
        member_event("A", "wikidata_id", None),
        conflict,
        replace(
            conflict,
            decision_id="conflict",
            payload={"aph_id": "B", "field": "gender", "value": "Female"},
        ),
    ]


def test_precomputed_member_heads_match_default_resolution(
    member_events: list[ReviewEvent], monkeypatch: pytest.MonkeyPatch
) -> None:
    heads = active_heads(member_events)
    fields = [
        (aph_id, field)
        for aph_id in ("A", "B")
        for field in ("date_of_birth", "gender", "wikidata_id")
    ]
    expected = [
        member_comparison.effective_member_value(member_events, aph_id, field, "Male")
        for aph_id, field in fields
    ]
    expected_ambiguity = [
        member_comparison.check_identity_ambiguity([], member_events, aph_id)
        for aph_id in ("A", "B")
    ]

    def unexpected_replay(events: list[ReviewEvent]) -> dict[str, list[ReviewEvent]]:
        raise AssertionError("Precomputed decision heads must not replay the ledger")

    monkeypatch.setattr(member_comparison, "active_heads", unexpected_replay)
    assert [
        member_comparison.effective_member_value(
            member_events, aph_id, field, "Male", decision_heads=heads
        )
        for aph_id, field in fields
    ] == expected
    assert [
        member_comparison.check_identity_ambiguity(
            [], member_events, aph_id, decision_heads=heads
        )
        for aph_id in ("A", "B")
    ] == expected_ambiguity
    assert expected[1][0] == "Female"
    assert expected[2] == (None, member_events[2], True)
    assert expected_ambiguity == [False, True]


def test_member_candidate_build_replays_once_and_never_caches_between_builds(
    db_conn: duckdb.DuckDBPyConnection,
    tmp_path: Path,
    member_events: list[ReviewEvent],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    members = [
        {"member_id": "aph-a", "aph_id": "A", "display_name": "First Person"},
        {"member_id": "aph-b", "aph_id": "B", "display_name": "Second Person"},
    ]
    db_conn.executemany(
        "INSERT INTO members (member_id, aph_id, display_name, family_name, given_name) VALUES (?, ?, ?, 'Person', 'Test')",
        [(row["member_id"], row["aph_id"], row["display_name"]) for row in members],
    )
    cache = tmp_path / "members"
    cache.mkdir()
    for member in members:
        (cache / f"{member['aph_id']}.json").write_text(
            json.dumps({"gender": "Female", "wikidata_id": "Q42"}),
            encoding="utf-8",
        )
    expected = sorted(
        [
            item
            for member in members
            for item in candidates.member_candidates(
                member, [], member_events, tmp_path
            )
        ],
        key=lambda item: (item["review_id"], item["candidate_id"]),
    )
    replays: list[list[ReviewEvent]] = []

    def counted(events: list[ReviewEvent]) -> dict[str, list[ReviewEvent]]:
        replays.append(events)
        return active_heads(events)

    def unexpected_replay(events: list[ReviewEvent]) -> dict[str, list[ReviewEvent]]:
        raise AssertionError("Member comparisons must share the build's decision heads")

    monkeypatch.setattr(candidates, "active_heads", counted)
    monkeypatch.setattr(member_comparison, "active_heads", unexpected_replay)
    actual = candidates.build_candidates(
        db_conn, cache_dir=tmp_path, events=member_events
    )
    assert [item for item in actual if item["entity_type"] == "member"] == expected
    assert replays == [member_events]

    changed = [*member_events, member_event("B", "wikidata_id", "Q42")]
    second = candidates.build_candidates(db_conn, cache_dir=tmp_path, events=changed)
    assert replays == [member_events, changed]
    changed_case = member_review_id("B", "wikidata_id")
    assert (
        next(item for item in second if item["review_id"] == changed_case)["evidence"][
            "effective_value"
        ]
        == "Q42"
    )


def test_member_candidate_build_still_validates_ledger(
    db_conn: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    db_conn.execute(
        "INSERT INTO members (member_id, aph_id, display_name, family_name, given_name) VALUES ('aph-a', 'A', 'First Person', 'Person', 'First')"
    )
    invalid = replace(member_event("A", "gender", "Male"), reviewer="")
    with pytest.raises(ValueError, match="reviewer"):
        candidates.build_candidates(db_conn, cache_dir=tmp_path, events=[invalid])
