"""Tests for member review presentation, batch prepare, atomic save, and DAG retention."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import pytest

from apemap.review.model import ReviewEvent, member_review_id, resolve_events
from apemap.review.members import MemberFieldDraft
from apemap.review.service import ReviewService
from apemap.review.store import StaleReviewError, log_revision
from tests.test_review_service import review_service as review_service


@pytest.fixture(autouse=True)
def member_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cache = tmp_path / "wikimedia"
    (cache / "members").mkdir(parents=True)
    path = cache / "members" / "TEST.json"
    path.write_text(
        json.dumps(
            {
                "status": "matched",
                "gender": "Female",
                "date_of_birth": "1972-02-03",
                "wikidata_id": "Q123",
                "source_url": "https://example.org/bio",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("apemap.review.service.RAW_WIKIMEDIA_DIR", cache)
    return path


def test_member_view_presentation(review_service: ReviewService) -> None:
    service = review_service
    # Member TEST is present in the fixture database
    member_data = service.member("TEST")
    assert member_data["aph_id"] == "TEST"
    assert member_data["name"] == "Test Person"
    assert isinstance(member_data["fields"], list)
    assert len(member_data["fields"]) == 3
    # Check fields are sorted by order
    orders = [f["order"] for f in member_data["fields"]]
    assert orders == sorted(orders)
    # Check aggregate status
    assert member_data["status"] in (
        "pending",
        "accepted",
        "rejected",
        "needs_research",
        "conflict",
    )


def test_member_prepare_and_save_batch_mixed_decisions(
    review_service: ReviewService,
) -> None:
    service = review_service
    # First accept a date of birth
    drafts_init = [
        MemberFieldDraft(
            field="date_of_birth",
            action="accept",
            value="1971-02-03",
            source_url="https://example.org/bio",
            notes="Accepted valid DOB",
            reviewer="Reviewer",
        ),
        MemberFieldDraft(
            field="gender",
            action="unchanged",
        ),
    ]
    preview_init = service.prepare_batch("TEST", drafts_init)
    assert preview_init["kind"] == "member_batch"
    assert len(preview_init["events"]) == 1
    assert preview_init["events"][0]["payload"]["value"] == "1971-02-03"
    events_saved = service.save_batch(preview_init)
    assert len(events_saved) == 1
    dob_event_id = events_saved[0].decision_id

    # Now verify that member view shows effective value for date_of_birth
    member_data = service.member("TEST")
    dob_field = next(f for f in member_data["fields"] if f["field"] == "date_of_birth")
    assert dob_field["effective_value"] == "1971-02-03"
    assert dob_field["retained_decision_id"] == dob_event_id

    # Now do a mixed batch:
    # 1. Reject date_of_birth proposal (retaining prior accepted ancestor dob_event_id)
    # 2. Accept gender
    # 3. Needs research for wikidata_id
    drafts_mixed = [
        MemberFieldDraft(
            field="date_of_birth",
            action="reject",
            notes="New proposed date is wrong; retain prior accepted",
            reviewer="Reviewer",
        ),
        MemberFieldDraft(
            field="gender",
            action="accept",
            value="Female",
            source_url="https://example.org/gender",
            notes="Confirmed female",
            reviewer="Reviewer",
        ),
        MemberFieldDraft(
            field="wikidata_id",
            action="research",
            notes="Need to search wikidata",
            reviewer="Reviewer",
        ),
    ]
    preview_mixed = service.prepare_batch("TEST", drafts_mixed)
    assert len(preview_mixed["events"]) == 3
    # Check that rejection event has proposal_only=True and retained_decision_id=dob_event_id
    dob_event = next(
        e for e in preview_mixed["events"] if e["payload"]["field"] == "date_of_birth"
    )
    assert dob_event["payload"]["proposal_only"] is True
    assert dob_event["payload"]["retained_decision_id"] == dob_event_id
    assert dob_event["supersedes"] == [dob_event_id]

    saved_mixed = service.save_batch(preview_mixed)
    assert len(saved_mixed) == 3

    # Check effective resolution in ledger
    all_events = service.events()
    resolved = resolve_events(all_events)
    assert member_review_id("TEST", "gender") in resolved
    assert member_review_id("TEST", "date_of_birth") in resolved
    assert member_review_id("TEST", "wikidata_id") in resolved

    # Check member view retains the ancestor DOB value
    member_after = service.member("TEST")
    dob_after = next(f for f in member_after["fields"] if f["field"] == "date_of_birth")
    assert dob_after["effective_value"] == "1971-02-03"


def test_batch_validation_rejections(review_service: ReviewService) -> None:
    service = review_service

    # 1. Duplicate fields in drafts
    with pytest.raises(ValueError, match="Duplicate decision"):
        service.prepare_batch(
            "TEST",
            [
                {"field": "gender", "action": "accept", "value": "Male"},
                {"field": "gender", "action": "reject", "notes": "No"},
            ],
        )

    # 2. Unsupported member field
    with pytest.raises(ValueError, match="Unsupported member field"):
        service.prepare_batch(
            "TEST",
            [
                {"field": "favorite_color", "action": "accept", "value": "Blue"},
            ],
        )

    # 3. Invalid action
    with pytest.raises(ValueError, match="Invalid draft action"):
        service.prepare_batch(
            "TEST",
            [
                {"field": "gender", "action": "destroy"},
            ],
        )

    # 4. No decisions selected (all unchanged)
    with pytest.raises(ValueError, match="No field decisions selected"):
        service.prepare_batch(
            "TEST",
            [
                {"field": "gender", "action": "unchanged"},
                {"field": "date_of_birth", "action": "unchanged"},
            ],
        )

    # 5. Invalid date of birth format when accepting
    with pytest.raises(ValueError, match="Malformed date of birth"):
        service.prepare_batch(
            "TEST",
            [
                {"field": "date_of_birth", "action": "accept", "value": "bad-date"},
            ],
        )


def test_strict_ledger_revision_on_member_save(review_service: ReviewService) -> None:
    service = review_service
    drafts = [
        MemberFieldDraft(
            field="gender",
            action="accept",
            value="Male",
            source_url="https://example.org",
            notes="Accepted",
            reviewer="Reviewer",
        ),
    ]
    preview = service.prepare_batch("TEST", drafts)

    # Interfere with decision log by appending an event directly
    other_event = ReviewEvent(
        decision_id="interfering-event",
        review_id=member_review_id("OTHER", "gender"),
        entity_type="member",
        action="accept",
        payload={"aph_id": "OTHER", "field": "gender", "value": "Female"},
        source_url="https://example.org",
        reviewer="Reviewer",
        notes="",
        reviewed_at="2026-01-01",
        recorded_at="2026-01-01T00:00:00Z",
    )
    with service.log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(other_event.to_dict()) + "\n")

    # Now attempt save_batch with stale preview -> must raise StaleReviewError
    with pytest.raises(StaleReviewError):
        service.save_batch(preview)


def test_prepare_batch_requires_review_db(review_service: ReviewService) -> None:
    service = review_service
    # Set non-existent db_path
    service.db_path = service.db_path.parent / "nonexistent.duckdb"
    drafts = [
        MemberFieldDraft(field="gender", action="accept", value="Male"),
    ]
    with pytest.raises(ValueError, match="Review database required"):
        service.prepare_batch("TEST", drafts)


def test_member_uses_preserved_baseline_after_withdrawal(
    review_service: ReviewService,
) -> None:
    from apemap.db import get_connection

    service = review_service
    accepted = service.save_batch(
        service.prepare_batch(
            "TEST",
            [
                MemberFieldDraft(
                    field="gender",
                    action="accept",
                    value="Female",
                    source_url="https://example.org/bio",
                )
            ],
        )
    )[0]
    with get_connection(service.db_path) as conn:
        conn.execute("UPDATE members SET gender = 'Female'")
    service.save(
        service.prepare(
            accepted.review_id,
            "reject",
            {"aph_id": "TEST", "field": "gender", "value": None},
            notes="Withdraw correction",
            supersedes=[accepted.decision_id],
        )
    )
    field = next(
        row for row in service.member("TEST")["fields"] if row["field"] == "gender"
    )
    assert field["source_value"] == "Male"
    assert field["effective_value"] == "Male"
    assert field["comparison"] == "different"


def test_every_cache_option_and_distinct_fields_are_shared(
    review_service: ReviewService, member_cache: Path
) -> None:
    from apemap.review.members import group_member_rows

    name_path = member_cache.parent / "name_test_person.json"
    data = json.loads(member_cache.read_text())
    data["gender"] = "female"
    name_path.write_text(json.dumps(data))
    view = review_service.member("TEST")
    assert not view["ambiguous"]
    assert all(len(field["candidates"]) == 2 for field in view["fields"])
    group = next(
        row
        for row in group_member_rows(review_service.candidates())
        if row["entity_type"] == "member"
    )
    assert group["actionable_count"] == view["actionable_count"] == 3
    assert group["field_statuses"] == view["field_statuses"]
    data["wikidata_id"] = "Q999"
    name_path.write_text(json.dumps(data))
    assert review_service.member("TEST")["ambiguous"]


def test_refreshed_proposal_returns_to_pending_and_retention_chains(
    review_service: ReviewService, member_cache: Path
) -> None:
    service = review_service
    first = service.save_batch(
        service.prepare_batch(
            "TEST",
            [
                MemberFieldDraft(
                    field="date_of_birth",
                    action="accept",
                    value="1971-02-03",
                    source_url="https://example.org/bio",
                )
            ],
        )
    )[0]
    for action in ("reject", "research"):
        preview = service.prepare_batch(
            "TEST",
            [
                MemberFieldDraft(
                    field="date_of_birth", action=action, notes="Retain corrected date"
                )
            ],
        )
        if action == "research":
            assert "date_of_birth" in preview["unresolved_fields"]
        service.save_batch(preview)
        field = next(
            row
            for row in service.member("TEST")["fields"]
            if row["field"] == "date_of_birth"
        )
        assert field["effective_value"] == "1971-02-03"
        assert field["retained_decision_id"] == first.decision_id
        assert field["status"] == (
            "rejected" if action == "reject" else "needs_research"
        )
    data = json.loads(member_cache.read_text())
    data["date_of_birth"] = "1973-01-01"
    member_cache.write_text(json.dumps(data))
    assert (
        next(
            row
            for row in service.member("TEST")["fields"]
            if row["field"] == "date_of_birth"
        )["status"]
        == "pending"
    )


def test_suppressed_fields_have_no_batch_controls(
    review_service: ReviewService, member_cache: Path
) -> None:
    data = json.loads(member_cache.read_text())
    data.pop("gender")
    data["date_of_birth"] = "1970-01-01T00:00:00Z"
    member_cache.write_text(json.dumps(data))
    view = review_service.member("TEST")
    assert view["actionable_count"] == 1
    for field in ("gender", "date_of_birth"):
        with pytest.raises(ValueError, match="Suppressed field"):
            review_service.prepare_batch(
                "TEST",
                [MemberFieldDraft(field=field, action="research", notes="Research")],
            )


@pytest.mark.parametrize("changed", ["validation", "effects", "source"])
def test_changed_batch_conclusions_never_append(
    review_service: ReviewService,
    member_cache: Path,
    monkeypatch: pytest.MonkeyPatch,
    changed: str,
) -> None:
    service = review_service
    preview = service.prepare_batch(
        "TEST", [MemberFieldDraft(field="gender", action="research", notes="Research")]
    )
    before = log_revision(service.log_path)
    if changed == "validation":
        original = service._check_references

        def changed_validation(*args: Any, **kwargs: Any) -> dict[str, Any]:
            result = original(*args, **kwargs)
            result["warnings"] = ["Changed validation warning"]
            return result

        monkeypatch.setattr(service, "_check_references", changed_validation)
    elif changed == "effects":
        original_diff = service.semantic_diff

        def changed_diff(*args: Any, **kwargs: Any) -> dict[str, Any]:
            result = original_diff(*args, **kwargs)
            result["decisions"] = []
            return result

        monkeypatch.setattr(service, "semantic_diff", changed_diff)
    else:
        member_cache.write_text(member_cache.read_text() + "\n")
    with pytest.raises(StaleReviewError):
        service.save_batch(preview)
    assert log_revision(service.log_path) == before
    if changed == "source":
        fresh = service.prepare_batch(
            "TEST",
            [MemberFieldDraft(field="gender", action="research", notes="Research")],
        )
        service.save_batch(fresh)
        assert len(service.events()) == 1


def test_invalid_one_field_prevents_entire_batch(review_service: ReviewService) -> None:
    before = log_revision(review_service.log_path)
    with pytest.raises(ValueError):
        review_service.prepare_batch(
            "TEST",
            [
                MemberFieldDraft(
                    field="gender",
                    action="accept",
                    value="Female",
                    source_url="https://example.org/bio",
                ),
                MemberFieldDraft(
                    field="date_of_birth",
                    action="accept",
                    value="1970-02-31",
                    source_url="https://example.org/bio",
                ),
            ],
        )
    assert log_revision(review_service.log_path) == before


def test_conflicting_heads_require_explicit_retention(
    review_service: ReviewService,
) -> None:
    from dataclasses import replace
    from apemap.review.store import encode_event

    first = review_service.save_batch(
        review_service.prepare_batch(
            "TEST",
            [
                MemberFieldDraft(
                    field="gender",
                    action="accept",
                    value="Female",
                    source_url="https://example.org/bio",
                )
            ],
        )
    )[0]
    second = replace(
        first,
        decision_id="parallel-head",
        payload={"aph_id": "TEST", "field": "gender", "value": "Other"},
    )
    review_service.log_path.write_bytes(encode_event(first) + encode_event(second))
    with pytest.raises(ValueError, match="Select an accepted value"):
        review_service.prepare_batch(
            "TEST",
            [
                MemberFieldDraft(
                    field="gender", action="research", notes="Resolve identity"
                )
            ],
        )
    preview = review_service.prepare_batch(
        "TEST",
        [
            MemberFieldDraft(
                field="gender",
                action="research",
                notes="Retain accepted value",
                retained_decision_id=first.decision_id,
            )
        ],
    )
    assert set(preview["events"][0]["supersedes"]) == {
        first.decision_id,
        second.decision_id,
    }
    review_service.save_batch(preview)
    field = next(
        row
        for row in review_service.member("TEST")["fields"]
        if row["field"] == "gender"
    )
    assert field["effective_value"] == "Female"
