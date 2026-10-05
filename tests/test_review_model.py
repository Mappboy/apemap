"""Immutable review identities and order-independent effective decisions."""

from dataclasses import replace
import json
from typing import Any

import pytest

from apemap.review.model import (
    ReviewEvent,
    education_review_id,
    institution_review_id,
    member_review_id,
    parse_events,
    resolve_events,
    school_review_id,
    service_review_id,
    validate_events,
)
from apemap.review.store import encode_event

pytestmark = pytest.mark.unit


def school_event(decision_id: str = "first", **changes: Any) -> ReviewEvent:
    values: dict[str, Any] = dict(
        decision_id=decision_id,
        review_id=school_review_id("Saint Mary's"),
        entity_type="school",
        action="map",
        payload={
            "recorded_name": "Saint Mary's",
            "institution_ref": "acara:123",
            "relationship_type": "alias",
        },
        source_url="https://example.org/history",
        reviewer="Researcher",
        reviewed_at="2026-10-05",
        recorded_at="2026-10-05T12:00:00+00:00",
    )
    values.update(changes)
    return ReviewEvent.from_dict(values)


def test_stable_ids_ignore_source_order_and_punctuation() -> None:
    assert school_review_id("St. Mary's") == school_review_id("Saint Marys")
    assert education_review_id("83T", "St. Mary's") == education_review_id(
        "83t", "Saint Marys"
    )
    assert member_review_id("83T", "gender") == "member:83t:gender"
    assert service_review_id("83T", 48) == "service:83t:48"


@pytest.mark.parametrize(
    "data",
    [
        b"not json\n",
        b"[]\n",
        b"{}\n",
        b'{"decision_id":"a","decision_id":"b"}\n',
        b"\n",
        b"\xff",
    ],
)
def test_malformed_jsonl_identifies_location(data: bytes) -> None:
    with pytest.raises(ValueError, match="line 1|UTF-8"):
        parse_events(data)


def test_parse_supersession_independent_of_line_order() -> None:
    first = school_event()
    second = replace(
        first,
        decision_id="second",
        action="supersede",
        replacement_action="reject",
        supersedes=["first"],
        notes="Incorrect school",
    )
    events = parse_events(encode_event(second) + encode_event(first))
    assert resolve_events(events)[first.review_id].status == "rejected"
    assert len(events) == 2


def test_duplicate_missing_cycle_and_competing_heads_rejected() -> None:
    first = school_event()
    with pytest.raises(ValueError, match="Duplicate"):
        resolve_events([first, first])
    with pytest.raises(ValueError, match="Conflicting"):
        resolve_events([first, replace(first, decision_id="independent")])
    with pytest.raises(ValueError, match="Missing"):
        resolve_events(
            [
                replace(
                    first,
                    action="supersede",
                    replacement_action="map",
                    supersedes=["missing"],
                )
            ]
        )
    a = replace(
        first, action="supersede", replacement_action="map", supersedes=["second"]
    )
    b = replace(a, decision_id="second", supersedes=["first"])
    with pytest.raises(ValueError, match="cycle"):
        resolve_events([a, b])


def test_multiple_parents_resolve_branch_conflict_without_last_writer_wins() -> None:
    a, b = school_event("a"), school_event("b")
    resolved = replace(
        a,
        decision_id="resolved",
        action="supersede",
        replacement_action="map",
        supersedes=["a", "b"],
    )
    assert resolve_events([resolved, b, a])[a.review_id].decision_id == "resolved"


@pytest.mark.parametrize(
    "changes",
    [
        {"entity_type": "member"},
        {"action": "unknown"},
        {"reviewed_at": "05/10/2026"},
        {"source_url": "javascript:alert(1)"},
        {"reviewer": ""},
        {"recorded_at": "2026-10-05T12:00:00"},
        {
            "payload": {
                "recorded_name": "Another school",
                "institution_ref": "acara:123",
                "relationship_type": "alias",
            }
        },
    ],
)
def test_invalid_schema_fields_rejected(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        school_event(**changes)


@pytest.mark.parametrize("action", ["accept", "map", "supersede"])
def test_school_source_url_can_be_omitted(action: str) -> None:
    value = school_event().to_dict()
    value.pop("source_url")
    value["action"] = action
    if action == "supersede":
        value.update(supersedes=["earlier"], replacement_action="map")
    assert ReviewEvent.from_dict(value).source_url == ""


@pytest.mark.parametrize(
    "source", [None, 0, False, " ", "not a URL", "https://user:secret@example.org"]
)
def test_optional_school_source_is_validated_when_supplied(source: Any) -> None:
    with pytest.raises(ValueError, match="source_url"):
        school_event(source_url=source)


@pytest.mark.parametrize(
    "entity", ["member", "member_education", "service", "manual_institution"]
)
def test_other_accepted_entities_still_require_source_url(entity: str) -> None:
    prefixes = {"member_education": "education", "manual_institution": "institution"}
    value = school_event().to_dict()
    value.update(
        entity_type=entity,
        review_id=f"{prefixes.get(entity, entity)}:test",
        action="accept",
        source_url="",
    )
    with pytest.raises(ValueError, match="source_url must be a nonempty string"):
        ReviewEvent.from_dict(value)


def test_real_acara_and_manual_registry_references_required() -> None:
    event = school_event()
    with pytest.raises(ValueError, match="does not exist"):
        validate_events([event], acara_ids={"999"})
    manual = replace(event, payload={**event.payload, "institution_ref": "manual:eton"})
    with pytest.raises(ValueError, match="Missing active manual"):
        validate_events([manual])
    definition = replace(
        event,
        decision_id="institution",
        entity_type="manual_institution",
        review_id=institution_review_id("manual:eton"),
        action="accept",
        payload={
            "institution_ref": "manual:eton",
            "school_name": "Eton College",
            "country": "United Kingdom",
            "sector": "Other",
        },
    )
    validate_events([manual, definition])


def test_education_does_not_infer_graduation() -> None:
    event = school_event()
    education = replace(
        event,
        entity_type="member_education",
        action="accept",
        review_id=education_review_id("83T", "Saint Mary's"),
        payload={
            "aph_id": "83T",
            "recorded_school_name": "Saint Mary's",
            "attended_status": "attended_unspecified",
            "confidence": "verified",
            "retrieved_at": "2026-10-02T00:00:00+10:00",
            "graduation_year": 1974,
        },
    )
    with pytest.raises(ValueError, match="explicit graduated"):
        validate_events([education])


@pytest.mark.parametrize("status", ["closed", "historical_only", "invalid"])
def test_manual_institution_status_is_explicit_and_validated(status: str) -> None:
    event = school_event().to_dict()
    event.update(
        decision_id="institution-status",
        entity_type="manual_institution",
        review_id=institution_review_id("manual:old-school"),
        action="accept",
        payload={
            "institution_ref": "manual:old-school",
            "school_name": "Old School",
            "country": "Australia",
            "sector": "Other",
            "institution_status": status,
        },
    )
    if status == "invalid":
        with pytest.raises(ValueError, match="institution status"):
            ReviewEvent.from_dict(event)
    else:
        assert ReviewEvent.from_dict(event).payload["institution_status"] == status


def test_nonfinite_payload_rejected() -> None:
    value = school_event().to_dict()
    value["legacy"] = {"latitude": float("nan")}
    with pytest.raises(ValueError):
        parse_events((json.dumps(value) + "\n").encode())


@pytest.mark.parametrize(
    "field", ["reviewed_at", "date_of_birth", "service_start", "service_end"]
)
def test_compact_dates_rejected_before_canonical_replay(field: str) -> None:
    event = school_event().to_dict()
    if field == "reviewed_at":
        event[field] = "20261005"
    elif field == "date_of_birth":
        event.update(
            entity_type="member",
            review_id=member_review_id("TEST", field),
            action="accept",
            payload={"aph_id": "TEST", "field": field, "value": "19800101"},
        )
    else:
        interval = {
            "service_start": "2025-07-22",
            "service_end": "2025-08-01",
            "chamber": "representatives",
            "party": "Labor",
            "party_abbrev": "ALP",
            "state_or_territory": "TAS",
            "source_url": "https://example.org/service",
            "retrieved_at": "2026-10-05T00:00:00+00:00",
        }
        interval[field] = interval[field].replace("-", "")
        event.update(
            entity_type="service",
            review_id=service_review_id("TEST", 48),
            action="accept",
            payload={
                "aph_id": "TEST",
                "parliament_number": 48,
                "intervals": [interval],
            },
        )
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        ReviewEvent.from_dict(event)
