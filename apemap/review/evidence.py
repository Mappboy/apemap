"""Immutable, separately retained evidence for advisory school resolution."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from types import MappingProxyType
from typing import Any

from apemap.review.model import (
    DEFAULT_LOG_PATH,
    ReviewEvent,
    _text,
    _timestamp,
    entity_for_review_id,
    evidence_url,
    institution_reference,
)
from apemap.review.store import StaleReviewError, _decision_lock

DEFAULT_EVIDENCE_PATH = DEFAULT_LOG_PATH.with_name("evidence.jsonl")
STANCES = ("supports", "contradicts", "contextual")
GENERATORS = ("manual", "wikimedia", "agent-search", "importer")
QUALITY_FIELDS = (
    "source_quality",
    "match_strength",
    "temporal_relevance",
    "geographic_relevance",
)
# Only institutional facts can be reused across members. An attendance claim
# remains evidence for the member named by its education review identity.
SCHOOL_CONTEXT_CLAIM_TYPES = frozenset(
    {
        "identity",
        "institution_identity",
        "school_identity",
        "name",
        "locality",
        "location",
        "geography",
        "temporal",
        "operating_dates",
        "relationship",
        "school_relationship",
        "rename",
        "successor",
        "historical_context",
        "sector",
        "school_type",
    }
)


def evidence_path(ledger_path: Path | str = DEFAULT_LOG_PATH) -> Path:
    return Path(ledger_path).with_name("evidence.jsonl")


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


@dataclass(frozen=True)
class EvidenceRecord:
    review_id: str
    candidate_institution_ref: str | None
    source_title: str
    source_type: str
    source_url: str
    retrieved_at: str
    claim_type: str
    claim_value: Any
    stance: str
    excerpt_or_note: str
    generated_by: str
    source_quality: float | None = None
    match_strength: float | None = None
    temporal_relevance: float | None = None
    geographic_relevance: float | None = None
    schema_version: int = 1
    evidence_id: str = ""

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("Unsupported evidence schema_version")
        for name in (
            "review_id",
            "source_title",
            "source_type",
            "claim_type",
            "excerpt_or_note",
        ):
            _text(getattr(self, name), name)
        entity_for_review_id(self.review_id)
        if not re.fullmatch(
            r"school:[0-9a-f]{16}|education:[a-z0-9_-]+:(?:[0-9a-f]{16}|missing)"
            r"|member:[a-z0-9_-]+:(?:date_of_birth|gender|wikidata_id)"
            r"|service:[a-z0-9_-]+:[1-9][0-9]*"
            r"|institution:(?:acara:[0-9]+|manual:[a-z0-9]+(?:-[a-z0-9]+)*)",
            self.review_id,
        ):
            raise ValueError("Invalid evidence review_id")
        if self.candidate_institution_ref is not None:
            institution_reference(self.candidate_institution_ref)
        evidence_url(self.source_url)
        _timestamp(self.retrieved_at, "retrieved_at")
        if self.stance not in STANCES:
            raise ValueError("Invalid evidence stance")
        if self.generated_by not in GENERATORS:
            raise ValueError("Invalid evidence generated_by")
        for name in QUALITY_FIELDS:
            value = getattr(self, name)
            if value is not None:
                if (
                    type(value) not in (int, float)
                    or not math.isfinite(value)
                    or not 0 <= value <= 1
                ):
                    raise ValueError(f"{name} must be a finite number between 0 and 1")
                object.__setattr__(self, name, float(value))
        # Round-trip to reject non-JSON objects and detach every caller-owned
        # collection before freezing nested claim values.
        try:
            detached = json.loads(json.dumps(_thaw(self.claim_value), allow_nan=False))
        except (TypeError, ValueError) as exc:
            raise ValueError("claim_value must contain finite JSON data") from exc
        object.__setattr__(self, "claim_value", _freeze(detached))
        content = self.to_dict()
        content.pop("evidence_id")
        identifier = "evidence:" + hashlib.sha256(_canonical(content)).hexdigest()
        if not isinstance(self.evidence_id, str):
            raise ValueError("evidence_id must be a string")
        if self.evidence_id and self.evidence_id != identifier:
            raise ValueError("evidence_id does not match immutable evidence content")
        object.__setattr__(self, "evidence_id", identifier)

    def to_dict(self) -> dict[str, Any]:
        return {item.name: _thaw(getattr(self, item.name)) for item in fields(self)}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> EvidenceRecord:
        if not isinstance(value, dict):
            raise ValueError("Evidence must be a JSON object")
        unknown = set(value) - {item.name for item in fields(cls)}
        if unknown:
            raise ValueError(f"Unknown evidence fields: {', '.join(sorted(unknown))}")
        try:
            return cls(**value)
        except TypeError as exc:
            raise ValueError(f"Invalid evidence shape: {exc}") from exc


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate evidence JSON field: {key}")
        result[key] = value
    return result


def parse_evidence(raw: bytes) -> list[EvidenceRecord]:
    records: dict[str, EvidenceRecord] = {}
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("Evidence must be UTF-8 JSONL") from exc
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            record = EvidenceRecord.from_dict(
                json.loads(line, object_pairs_hook=_object)
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid evidence line {number}: {exc}") from exc
        prior = records.get(record.evidence_id)
        if prior is not None and prior != record:
            raise ValueError(f"Conflicting evidence_id: {record.evidence_id}")
        records[record.evidence_id] = record
    return list(records.values())


def load_evidence(path: Path | str = DEFAULT_EVIDENCE_PATH) -> list[EvidenceRecord]:
    path = Path(path)
    return parse_evidence(path.read_bytes() if path.exists() else b"")


def evidence_revision(path: Path | str = DEFAULT_EVIDENCE_PATH) -> str:
    path = Path(path)
    return hashlib.sha256(path.read_bytes() if path.exists() else b"").hexdigest()


def append_evidence(
    path: Path | str,
    records: list[EvidenceRecord],
    *,
    expected_revision: str,
    source_check: Callable[[], None] | None = None,
) -> list[EvidenceRecord]:
    """Append a complete batch, retaining prior bytes and identical IDs once."""
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _decision_lock(path.with_suffix(path.suffix + ".lock")):
        raw = path.read_bytes() if path.exists() else b""
        if hashlib.sha256(raw).hexdigest() != expected_revision:
            raise StaleReviewError("Evidence log changed; refresh and preview again")
        if source_check is not None:
            source_check()
        if raw and not raw.endswith(b"\n"):
            raise ValueError("Evidence log must end with a newline before appending")
        existing = parse_evidence(raw)
        by_id = {record.evidence_id: record for record in existing}
        additions: list[EvidenceRecord] = []
        for candidate in records:
            record = EvidenceRecord.from_dict(candidate.to_dict())
            prior = by_id.get(record.evidence_id)
            if prior is not None:
                if prior != record:
                    raise ValueError(f"Conflicting evidence_id: {record.evidence_id}")
                continue
            by_id[record.evidence_id] = record
            additions.append(record)
        if not additions:
            if source_check is not None:
                source_check()
            return existing
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                for record in additions:
                    stream.write(_canonical(record.to_dict()) + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
            if source_check is not None:
                source_check()
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return existing + additions


def legacy_evidence(events: list[ReviewEvent]) -> list[EvidenceRecord]:
    """Expose retained decision citations without changing their historical bytes."""
    records: dict[str, EvidenceRecord] = {}
    for event in events:
        if not event.source_url:
            continue
        education = event.entity_type == "member_education"
        record = EvidenceRecord(
            review_id=event.review_id,
            candidate_institution_ref=event.payload.get("institution_ref"),
            source_title=f"Retained decision {event.decision_id}",
            source_type="legacy-decision",
            source_url=event.source_url,
            retrieved_at=event.payload.get("retrieved_at") or event.recorded_at,
            claim_type="attendance"
            if education and event.effective_action == "accept"
            else "relationship"
            if event.entity_type == "school"
            else "review_context",
            claim_value={
                "decision_id": event.decision_id,
                "action": event.effective_action,
                "payload": event.payload,
            },
            stance="supports"
            if event.effective_action in {"accept", "map"}
            else "contextual",
            excerpt_or_note=event.notes
            or f"Source retained with {event.effective_action} decision",
            generated_by="importer",
        )
        records[record.evidence_id] = record
    return sorted(records.values(), key=lambda record: record.evidence_id)


def evidence_applies(record: EvidenceRecord, review_id: str) -> bool:
    """Allow the same case and explicitly reusable same-name school facts."""
    if record.review_id == review_id:
        return True
    return (
        review_id.startswith("education:")
        and record.review_id == "school:" + review_id.rsplit(":", 1)[-1]
        and record.claim_type in SCHOOL_CONTEXT_CLAIM_TYPES
    )


def validate_evidence_references(
    events: list[ReviewEvent], records: list[EvidenceRecord]
) -> None:
    by_id: dict[str, EvidenceRecord] = {}
    for record in records:
        prior = by_id.get(record.evidence_id)
        if prior is not None and prior != record:
            raise ValueError(f"Conflicting evidence_id: {record.evidence_id}")
        by_id[record.evidence_id] = record
    for event in events:
        refs = event.payload.get("evidence_refs", [])
        if not isinstance(refs, list) or any(
            not isinstance(ref, str) or not ref for ref in refs
        ):
            raise ValueError("evidence_refs must be a list of evidence IDs")
        if len(set(refs)) != len(refs):
            raise ValueError("Duplicate evidence_refs")
        for ref in refs:
            record = by_id.get(ref)
            if record is None:
                raise ValueError(f"Unknown evidence reference: {ref}")
            if not evidence_applies(record, event.review_id):
                raise ValueError(
                    f"Evidence reference belongs to a different case: {ref}"
                )
            target = event.payload.get("institution_ref")
            accepted = event.effective_action in {"accept", "map"}
            if (
                accepted
                and event.entity_type == "member_education"
                and not target
                and record.candidate_institution_ref is not None
            ):
                raise ValueError(
                    "Candidate-specific evidence requires an explicit institution_ref"
                )
            if (
                accepted
                and target
                and record.candidate_institution_ref not in (None, target)
            ):
                raise ValueError(
                    f"Evidence reference belongs to a different candidate: {ref}"
                )
