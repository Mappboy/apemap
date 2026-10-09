"""Immutable evidence retention, reference scope, and legacy citation parity."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterator

import pytest

from apemap.review.evidence import (
    EvidenceRecord,
    append_evidence,
    evidence_revision,
    legacy_evidence,
    load_evidence,
    parse_evidence,
    validate_evidence_references,
)
from apemap.review.model import ReviewEvent, education_review_id, school_review_id
from apemap.review.store import StaleReviewError


def record(**values: Any) -> EvidenceRecord:
    return EvidenceRecord.from_dict(
        {
            "review_id": education_review_id("ONE", "John Paul College"),
            "candidate_institution_ref": "acara:48982",
            "source_title": "School history",
            "source_type": "school-history",
            "source_url": "https://example.org/history",
            "retrieved_at": "2026-10-09T00:00:00+00:00",
            "claim_type": "identity",
            "claim_value": {"school": "John Paul College", "dates": [1980, 2000]},
            "stance": "supports",
            "excerpt_or_note": "The source identifies the Kalgoorlie school",
            "generated_by": "manual",
            **values,
        }
    )


def event(**payload: Any) -> ReviewEvent:
    return ReviewEvent(
        decision_id="decision-one",
        review_id=education_review_id("ONE", "John Paul College"),
        entity_type="member_education",
        action="map",
        payload={"institution_ref": "acara:48982", **payload},
        source_url="https://example.org/decision",
        reviewer="Fixture reviewer",
        reviewed_at="2026-10-09",
        recorded_at="2026-10-09T00:00:00+00:00",
    )


def encode(item: EvidenceRecord) -> bytes:
    return (json.dumps(item.to_dict(), sort_keys=True) + "\n").encode()


def test_content_id_and_nested_values_are_immutable_and_detached() -> None:
    original = {"school": "John Paul College", "dates": [1980, 2000]}
    item = record(claim_value=original)
    same = record(claim_value={"dates": [1980, 2000], "school": "John Paul College"})
    assert item.evidence_id == same.evidence_id
    original["dates"].append(2026)
    exported = item.to_dict()
    exported["claim_value"]["dates"].append(2027)
    assert item.to_dict()["claim_value"]["dates"] == [1980, 2000]
    with pytest.raises(TypeError):
        item.claim_value["school"] = "Changed"
    with pytest.raises(FrozenInstanceError):
        setattr(item, "stance", "contradicts")
    assert record(stance="contradicts").evidence_id != item.evidence_id


@pytest.mark.parametrize(
    "values, message",
    [
        ({"source_url": "https://user:secret@example.org"}, "without credentials"),
        ({"retrieved_at": "2026-10-09"}, "timezone-aware"),
        ({"review_id": "school:garbage"}, "review_id"),
        ({"source_title": " "}, "nonempty"),
        ({"stance": "accepted"}, "stance"),
        ({"generated_by": "web"}, "generated_by"),
        ({"candidate_institution_ref": "Q123"}, "institution_ref"),
        ({"source_quality": True}, "source_quality"),
        ({"source_quality": -0.1}, "source_quality"),
        ({"source_quality": 1.1}, "source_quality"),
        ({"match_strength": float("nan")}, "match_strength"),
        ({"claim_value": [float("inf")]}, "finite JSON"),
        ({"schema_version": True}, "schema_version"),
        ({"evidence_id": "evidence:wrong"}, "immutable evidence content"),
        ({"evidence_id": None}, "must be a string"),
        ({"unknown": "ignored"}, "Unknown evidence fields"),
    ],
)
def test_invalid_records_are_rejected(values: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        record(**values)


def test_parser_rejects_content_changes_and_duplicate_json_keys() -> None:
    item = record()
    changed = item.to_dict()
    changed["stance"] = "contradicts"
    with pytest.raises(ValueError, match="immutable evidence content"):
        parse_evidence(encode(item) + (json.dumps(changed) + "\n").encode())
    with pytest.raises(ValueError, match="Duplicate evidence JSON field"):
        parse_evidence(b'{"review_id":"one","review_id":"two"}\n')
    assert parse_evidence(encode(item) + encode(item)) == [item]


def test_atomic_append_is_idempotent_and_preserves_old_bytes(tmp_path: Path) -> None:
    path = tmp_path / "evidence.jsonl"
    item = record()
    old = encode(item)
    path.write_bytes(old)
    revision = evidence_revision(path)
    other = record(
        stance="contradicts", excerpt_or_note="Locality contradicts candidate"
    )
    assert append_evidence(path, [item, other, other], expected_revision=revision) == [
        item,
        other,
    ]
    assert path.read_bytes().startswith(old)
    assert load_evidence(path) == [item, other]
    final = path.read_bytes()
    append_evidence(path, [other], expected_revision=evidence_revision(path))
    assert path.read_bytes() == final
    with pytest.raises(StaleReviewError, match="Evidence log changed"):
        append_evidence(
            path,
            [record(source_url="https://example.org/new")],
            expected_revision=revision,
        )
    assert path.read_bytes() == final


def test_failed_replacement_does_not_damage_retained_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "evidence.jsonl"
    old = encode(record())
    path.write_bytes(old)

    def fail_replace(*_: Any) -> None:
        raise OSError("Replacement failed")

    monkeypatch.setattr("apemap.review.evidence.os.replace", fail_replace)
    with pytest.raises(OSError, match="Replacement failed"):
        append_evidence(
            path,
            [record(stance="contradicts")],
            expected_revision=evidence_revision(path),
        )
    assert path.read_bytes() == old
    assert list(tmp_path.glob("*.tmp")) == []


def test_source_guard_runs_under_lock_and_rechecks_before_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from apemap.review import evidence as module

    path = tmp_path / "evidence.jsonl"
    original = encode(record())
    path.write_bytes(original)
    state = {"locked": False, "source_changed": False}
    checks: list[bool] = []
    real_lock = module._decision_lock
    real_fsync = os.fsync

    @contextmanager
    def marked_lock(lock_path: Path) -> Iterator[None]:
        with real_lock(lock_path):
            state["locked"] = True
            try:
                yield
            finally:
                state["locked"] = False

    def change_after_write(descriptor: int) -> None:
        real_fsync(descriptor)
        state["source_changed"] = True

    def check_sources() -> None:
        checks.append(state["locked"])
        if state["source_changed"]:
            raise StaleReviewError("Candidate source changed")

    monkeypatch.setattr(module, "_decision_lock", marked_lock)
    monkeypatch.setattr(module.os, "fsync", change_after_write)
    with pytest.raises(StaleReviewError, match="Candidate source changed"):
        append_evidence(
            path,
            [record(stance="contradicts")],
            expected_revision=evidence_revision(path),
            source_check=check_sources,
        )
    assert checks == [True, True]
    assert path.read_bytes() == original
    assert list(tmp_path.glob("*.tmp")) == []


def test_missing_store_has_empty_revision_without_creating_file(tmp_path: Path) -> None:
    path = tmp_path / "evidence.jsonl"
    assert load_evidence(path) == []
    assert evidence_revision(path) == hashlib.sha256(b"").hexdigest()
    assert not path.exists()


def test_legacy_sources_are_deterministic_without_mutating_events() -> None:
    accepted = replace(
        event(),
        action="accept",
        payload={
            "institution_ref": "acara:48982",
            "retrieved_at": "2026-10-02T10:00:00+10:00",
        },
    )
    raw = json.dumps(accepted.to_dict(), sort_keys=True)
    records = legacy_evidence([accepted])
    assert legacy_evidence([accepted]) == records
    assert records[0].source_url == accepted.source_url
    assert records[0].retrieved_at == accepted.payload["retrieved_at"]
    assert records[0].claim_type == "attendance"
    assert json.dumps(accepted.to_dict(), sort_keys=True) == raw


def test_references_allow_opposing_sources_and_reusable_school_context() -> None:
    support = record()
    opposing = record(stance="contradicts")
    school = record(
        review_id=school_review_id("John Paul College"), claim_type="locality"
    )
    validate_evidence_references(
        [
            event(
                evidence_refs=[
                    support.evidence_id,
                    opposing.evidence_id,
                    school.evidence_id,
                ]
            )
        ],
        [support, opposing, school],
    )
    other = replace(
        event(),
        review_id=education_review_id("OTHER", "John Paul College"),
        payload={
            "institution_ref": "acara:48982",
            "evidence_refs": [school.evidence_id],
        },
    )
    validate_evidence_references([other], [school])


def test_legacy_fallback_acceptance_cannot_cite_a_specific_candidate() -> None:
    specific = record()
    accepted = replace(
        event(evidence_refs=[specific.evidence_id]),
        action="accept",
        payload={"evidence_refs": [specific.evidence_id]},
    )
    with pytest.raises(ValueError, match="requires an explicit institution_ref"):
        validate_evidence_references([accepted], [specific])
    contextual = record(candidate_institution_ref=None, claim_type="attendance")
    accepted = replace(accepted, payload={"evidence_refs": [contextual.evidence_id]})
    validate_evidence_references([accepted], [contextual])


@pytest.mark.parametrize("action", ["research", "reject"])
def test_non_accepting_outcomes_can_cite_different_candidate_evidence(
    action: str,
) -> None:
    other = record(candidate_institution_ref="acara:45994", stance="contradicts")
    outcome = replace(event(evidence_refs=[other.evidence_id]), action=action)
    validate_evidence_references([outcome], [other])


@pytest.mark.parametrize(
    "kind", ["unknown", "member", "candidate", "school-attendance", "different-school"]
)
def test_references_cannot_escape_case_or_candidate_scope(kind: str) -> None:
    item = record()
    if kind == "member":
        item = record(review_id=education_review_id("OTHER", "John Paul College"))
    elif kind == "candidate":
        item = record(candidate_institution_ref="acara:45994")
    elif kind == "school-attendance":
        item = record(
            review_id=school_review_id("John Paul College"), claim_type="attendance"
        )
    elif kind == "different-school":
        item = record(review_id=school_review_id("Different College"))
    with pytest.raises(
        ValueError, match="Unknown evidence|different case|different candidate"
    ):
        validate_evidence_references(
            [
                event(
                    evidence_refs=["unknown" if kind == "unknown" else item.evidence_id]
                )
            ],
            [item],
        )


@pytest.mark.parametrize("refs", [None, "evidence:one", [None], ["same", "same"]])
def test_malformed_reference_lists_are_rejected(refs: Any) -> None:
    with pytest.raises(ValueError, match="evidence_refs"):
        validate_evidence_references([event(evidence_refs=refs)], [])
