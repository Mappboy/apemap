"""Versioned decision events, immutable case identities, and graph validation."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from apemap.constants import (
    ATTENDED_STATUSES,
    CANONICAL_CHAMBERS,
    CANONICAL_SECTORS,
    CONFIDENCE_LEVELS,
    PARLIAMENT_METADATA,
    REFERENCE_DIR,
)
from apemap.education_context import SCHOOL_CONTEXT_FIELDS

DEFAULT_LOG_PATH = REFERENCE_DIR / "review" / "decisions.jsonl"
ENTITY_TYPES = ("school", "member", "member_education", "service", "manual_institution")
ACTIONS = ("accept", "map", "reject", "research", "supersede")
MEMBER_FIELDS = ("date_of_birth", "gender", "wikidata_id")
RESOLUTION_REASONS = ("ambiguous_name", "no_suitable_candidate")


def school_key(name: str) -> str:
    """Frozen v1 normalization; never change without an identity migration."""
    value = unicodedata.normalize("NFKD", name).lower()
    value = value.replace("&", "and")
    value = re.sub(r"\bst\.?\b", "saint", value)
    value = re.sub(r"\bmt\.?\b", "mount", value)
    value = re.sub(r"\bc of e\b", "church of england", value)
    return " ".join(re.sub(r"[^\w\s]", "", value).split())


def school_digest(name: str) -> str:
    return hashlib.sha256(school_key(name).encode("utf-8")).hexdigest()[:16]


def school_review_id(name: str) -> str:
    return f"school:{school_digest(name)}"


def education_review_id(aph_id: str, school_name: str) -> str:
    return f"education:{aph_id.lower()}:{school_digest(school_name) if school_name else 'missing'}"


def member_review_id(aph_id: str, member_field: str) -> str:
    return f"member:{aph_id.lower()}:{member_field}"


def service_review_id(aph_id: str, parliament: int) -> str:
    return f"service:{aph_id.lower()}:{parliament}"


def institution_review_id(institution_ref: str) -> str:
    return f"institution:{institution_ref}"


def entity_for_review_id(review_id: str) -> str:
    prefixes = {"education": "member_education", "institution": "manual_institution"}
    prefix = review_id.split(":", 1)[0]
    entity = prefixes.get(prefix, prefix)
    if entity not in ENTITY_TYPES:
        raise ValueError(f"Unknown review ID: {review_id}")
    return entity


@dataclass(frozen=True)
class ReviewEvent:
    decision_id: str
    review_id: str
    entity_type: str
    action: str
    payload: dict[str, Any]
    schema_version: int = 1
    source_url: str = ""
    reviewed_at: str = ""
    reviewer: str = ""
    notes: str = ""
    supersedes: list[str] = field(default_factory=list)
    replacement_action: str | None = None
    recorded_at: str = ""
    legacy: dict[str, Any] | None = None

    @property
    def effective_action(self) -> str:
        return (
            (self.replacement_action or "")
            if self.action == "supersede"
            else self.action
        )

    @property
    def status(self) -> str:
        return {
            "accept": "accepted",
            "map": "accepted",
            "reject": "rejected",
            "research": "needs_research",
        }[self.effective_action]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ReviewEvent:
        if not isinstance(value, dict):
            raise ValueError("Event must be a JSON object")
        unknown = set(value) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError(f"Unknown event fields: {', '.join(sorted(unknown))}")
        try:
            event = cls(**value)
        except TypeError as exc:
            raise ValueError(f"Invalid event shape: {exc}") from exc
        validate_event(event)
        return event


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _date(value: Any, label: str) -> date:
    try:
        result = date.fromisoformat(_text(value, label))
        if value != result.isoformat():
            raise ValueError("Noncanonical date form")
        return result
    except ValueError as exc:
        raise ValueError(f"Invalid {label}: expected YYYY-MM-DD date") from exc


def _timestamp(value: Any, label: str) -> datetime:
    try:
        result = datetime.fromisoformat(_text(value, label))
        if result.utcoffset() is None:
            raise ValueError("Missing timezone")
        return result
    except ValueError as exc:
        raise ValueError(
            f"Invalid {label}: expected timezone-aware ISO timestamp"
        ) from exc


def evidence_url(value: Any) -> str:
    value = _text(value, "source_url")
    parsed = urlparse(value)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.netloc
        or parsed.username
        or parsed.password
    ):
        raise ValueError("source_url must be an HTTP(S) URL without credentials")
    return value


def institution_reference(value: Any) -> str:
    value = _text(value, "institution_ref")
    if not re.fullmatch(r"acara:[0-9]+|manual:[a-z0-9]+(?:-[a-z0-9]+)*", value):
        raise ValueError(
            "institution_ref must be acara:<real ID> or manual:<stable-slug>"
        )
    return value


def validate_event(event: ReviewEvent) -> None:
    """Validate syntax and complete domain payload independently of source files."""
    if type(event.schema_version) is not int or event.schema_version != 1:
        raise ValueError("Unsupported event schema_version")
    for name in ("decision_id", "review_id", "entity_type", "action", "reviewer"):
        _text(getattr(event, name), name)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:_-]{0,127}", event.decision_id):
        raise ValueError("Invalid decision_id")
    if event.entity_type not in ENTITY_TYPES or event.action not in ACTIONS:
        raise ValueError("Invalid action/entity combination")
    if entity_for_review_id(event.review_id) != event.entity_type:
        raise ValueError("review_id and entity_type disagree")
    if not isinstance(event.payload, dict) or not isinstance(event.notes, str):
        raise ValueError("Invalid payload or notes")
    if event.legacy is not None and not isinstance(event.legacy, dict):
        raise ValueError("legacy must be an object")
    # Reject non-finite numbers anywhere, including preserved legacy metadata.
    json.dumps(event.to_dict(), allow_nan=False)
    _date(event.reviewed_at, "reviewed_at")
    _timestamp(event.recorded_at, "recorded_at")
    if not isinstance(event.supersedes, list) or any(
        not isinstance(x, str) for x in event.supersedes
    ):
        raise ValueError("supersedes must be a list of decision IDs")
    if len(set(event.supersedes)) != len(event.supersedes):
        raise ValueError("Duplicate supersession reference")
    if event.action == "supersede":
        if not event.supersedes or event.replacement_action not in ACTIONS[:-1]:
            raise ValueError("Supersede requires parents and a replacement_action")
    elif event.supersedes or event.replacement_action is not None:
        raise ValueError("Only supersede events may replace prior decisions")
    action = event.effective_action
    if action == "map" and event.entity_type not in {"school", "member_education"}:
        raise ValueError("map is only valid for schools and education assertions")
    if action in ("reject", "research") and not event.notes.strip():
        raise ValueError("Rejection/research requires a reason or note")
    if not isinstance(event.source_url, str):
        raise ValueError(
            "source_url must be a string; omit it or use an empty string when optional"
        )
    if event.source_url:
        evidence_url(event.source_url)
    if action in ("accept", "map") and event.entity_type != "school":
        evidence_url(event.source_url)
    payload = event.payload
    if "resolution_reason" in payload:
        if (
            payload["resolution_reason"] not in RESOLUTION_REASONS
            or event.entity_type not in {"school", "member_education"}
            or action != "research"
        ):
            raise ValueError(
                "resolution_reason requires school or named education research "
                "and must be ambiguous_name or no_suitable_candidate"
            )
        if event.entity_type == "member_education":
            _text(payload.get("recorded_school_name"), "recorded_school_name")
            if payload.get("resolution_only") is not True:
                raise ValueError(
                    "Typed education research requires resolution_only: true"
                )
        elif payload.get("requires_individual_resolution") is not True:
            raise ValueError(
                "Typed school research requires requires_individual_resolution: true"
            )
    if "requires_individual_resolution" in payload:
        if type(payload["requires_individual_resolution"]) is not bool:
            raise ValueError("requires_individual_resolution must be a boolean")
        if event.entity_type != "school" or action != "research":
            raise ValueError(
                "requires_individual_resolution is only valid for school research"
            )
        if (
            payload["requires_individual_resolution"]
            and payload.get("resolution_reason") not in RESOLUTION_REASONS
        ):
            raise ValueError("Individual school review requires a resolution_reason")
    if "evidence_refs" in payload:
        references = payload["evidence_refs"]
        if event.entity_type not in {"school", "member_education"}:
            raise ValueError(
                "Institution evidence references require a school or education assertion"
            )
        if (
            not isinstance(references, list)
            or any(
                not isinstance(reference, str) or not reference.strip()
                for reference in references
            )
            or len(references) != len(set(references))
        ):
            raise ValueError("evidence_refs must contain unique nonempty evidence IDs")
    expected_id = event.review_id
    if event.entity_type == "school":
        name = _text(payload.get("recorded_name"), "recorded_name")
        expected_id = school_review_id(name)
        if action in ("accept", "map"):
            institution_reference(payload.get("institution_ref"))
            if payload.get("relationship_type") not in (
                "direct",
                "alias",
                "rename",
                "successor",
            ):
                raise ValueError("School mapping requires a relationship_type")
            _validate_school_context(payload)
    elif event.entity_type == "member":
        aph_id = _text(payload.get("aph_id"), "aph_id")
        member_field = payload.get("field")
        if member_field not in MEMBER_FIELDS:
            raise ValueError("Unsupported member field")
        expected_id = member_review_id(aph_id, member_field)
        if action == "accept":
            value = payload.get("value")
            if member_field == "date_of_birth" and value is not None:
                _date(value, "date_of_birth")
            if member_field == "gender" and value not in (
                None,
                "Male",
                "Female",
                "Other",
            ):
                raise ValueError("Invalid gender")
            if (
                member_field == "wikidata_id"
                and value is not None
                and not re.fullmatch(r"Q[1-9][0-9]*", str(value))
            ):
                raise ValueError("Invalid wikidata_id")
            if "value" not in payload:
                raise ValueError(
                    "Member correction requires value (null clears a field)"
                )
        if "proposal_only" in payload:
            if (
                type(payload["proposal_only"]) is not bool
                or payload["proposal_only"] is not True
            ):
                raise ValueError("proposal_only must be true")
            if action not in ("reject", "research"):
                raise ValueError(
                    "proposal_only is only valid for member rejection or research"
                )
            if "retained_decision_id" in payload:
                ret_id = payload["retained_decision_id"]
                if ret_id is not None:
                    _text(ret_id, "retained_decision_id")
                    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:_-]{0,127}", ret_id):
                        raise ValueError("Invalid retained_decision_id")
        elif "retained_decision_id" in payload:
            raise ValueError("retained_decision_id requires proposal_only: true")
    elif event.entity_type == "member_education":
        aph_id = _text(payload.get("aph_id"), "aph_id")
        name = payload.get("recorded_school_name", "")
        if not isinstance(name, str):
            raise ValueError("recorded_school_name must be a string")
        expected_id = education_review_id(aph_id, name)
        if "resolution_only" in payload:
            if type(payload["resolution_only"]) is not bool:
                raise ValueError("resolution_only must be a boolean")
            if action != "research":
                raise ValueError("resolution_only is only valid for education research")
            if payload["resolution_only"]:
                _text(name, "recorded_school_name")
        if action == "map":
            _text(name, "recorded_school_name")
            institution_reference(payload.get("institution_ref"))
            _validate_relationship(payload)
            _validate_school_context(payload, assertion_scope=True)
            if any(
                name in payload
                for name in (
                    "attended_status",
                    "confidence",
                    "retrieved_at",
                    "years_attended",
                    "graduation_year",
                )
            ):
                raise ValueError("Education mapping cannot change attendance facts")
        if action == "accept":
            _text(name, "recorded_school_name")
            if payload.get("institution_ref"):
                institution_reference(payload["institution_ref"])
            if "relationship_type" in payload:
                institution_reference(payload.get("institution_ref"))
                _validate_relationship(payload)
                _validate_school_context(payload, assertion_scope=True)
            elif any(payload.get(name) is not None for name in SCHOOL_CONTEXT_FIELDS):
                raise ValueError(
                    "Assertion historical context requires an explicit relationship_type"
                )
            if (
                payload.get("attended_status") not in ATTENDED_STATUSES
                or payload.get("confidence") not in CONFIDENCE_LEVELS
            ):
                raise ValueError("Invalid education attendance/confidence")
            _timestamp(payload.get("retrieved_at"), "retrieved_at")
            graduation = payload.get("graduation_year")
            if graduation is not None and (
                type(graduation) is not int
                or not 1700 <= graduation <= date.today().year
            ):
                raise ValueError("Invalid graduation_year")
            if graduation is not None and payload["attended_status"] != "graduated":
                raise ValueError(
                    "graduation_year requires explicit graduated attendance"
                )
    elif event.entity_type == "service":
        aph_id = _text(payload.get("aph_id"), "aph_id")
        parliament = payload.get("parliament_number")
        if type(parliament) is not int or parliament not in PARLIAMENT_METADATA:
            raise ValueError("Invalid parliament_number")
        expected_id = service_review_id(aph_id, parliament)
        if action == "accept":
            validate_intervals(payload.get("intervals"), parliament)
    else:
        ref = institution_reference(payload.get("institution_ref"))
        if not ref.startswith("manual:"):
            raise ValueError("Manual institution must use manual: identity")
        expected_id = institution_review_id(ref)
        if action == "accept":
            _text(payload.get("school_name"), "school_name")
            _text(payload.get("country"), "country")
            if payload.get("sector") not in CANONICAL_SECTORS:
                raise ValueError("Invalid institution sector")
            if payload.get("institution_status", "manual") not in (
                "manual",
                "unknown",
                "current",
                "historical_only",
                "closed",
                "merged",
            ):
                raise ValueError("Invalid institution status")
            for name, bound in (("latitude", 90), ("longitude", 180)):
                value = payload.get(name)
                if value is not None and (
                    type(value) not in (int, float)
                    or not math.isfinite(value)
                    or abs(value) > bound
                ):
                    raise ValueError(f"Invalid {name}")
            if (payload.get("latitude") is None) != (payload.get("longitude") is None):
                raise ValueError("Supply both coordinates or neither")
            if payload.get("latitude") is not None:
                evidence_url(payload.get("location_source_url"))
    if expected_id != event.review_id:
        raise ValueError(f"Payload identity requires review_id {expected_id}")


def _validate_relationship(payload: dict[str, Any]) -> None:
    if payload.get("relationship_type") not in (
        "direct",
        "alias",
        "rename",
        "successor",
    ):
        raise ValueError("Education mapping requires a relationship_type")


def _validate_school_context(
    payload: dict[str, Any], *, assertion_scope: bool = False
) -> None:
    """Validate independent historical evidence within the decision's scope."""
    if (
        assertion_scope
        and payload.get("historical_scope_confirmed") is not None
        and type(payload["historical_scope_confirmed"]) is not bool
    ):
        raise ValueError("historical_scope_confirmed must be a boolean")
    supplied = {name for name in SCHOOL_CONTEXT_FIELDS if payload.get(name) is not None}
    if not supplied:
        return
    if payload.get("relationship_type") != "successor":
        raise ValueError("Historical school context requires a successor relationship")
    if not assertion_scope and payload.get("historical_scope_confirmed") is not True:
        raise ValueError(
            "Confirm historical evidence covers all attendance records and verified aliases"
        )
    reference = payload.get("attended_institution_ref")
    if reference is not None:
        institution_reference(reference)
        if reference == payload["institution_ref"]:
            raise ValueError(
                "Original institution reference must differ from its successor"
            )
        evidence_url(payload.get("attended_identity_source_url"))
    elif payload.get("attended_identity_source_url") is not None:
        raise ValueError("Original identity evidence requires attended_institution_ref")
    coordinates = (
        payload.get("historical_latitude"),
        payload.get("historical_longitude"),
    )
    if (coordinates[0] is None) != (coordinates[1] is None):
        raise ValueError("Supply both historical coordinates or neither")
    if coordinates[0] is not None:
        for name, bound in (("historical_latitude", 90), ("historical_longitude", 180)):
            value = payload[name]
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or abs(value) > bound
            ):
                raise ValueError(f"Invalid {name}")
        evidence_url(payload.get("historical_location_source_url"))
    elif payload.get("historical_location_source_url") is not None:
        raise ValueError("Historical location evidence requires coordinates")
    for name, choices, source in (
        (
            "campus_continuity",
            ("same_campus", "different_campus"),
            "campus_continuity_source_url",
        ),
        (
            "historical_broad_sector",
            ("Government", "Non-government"),
            "historical_broad_sector_source_url",
        ),
        (
            "historical_detailed_sector",
            ("Catholic", "Independent"),
            "historical_detailed_sector_source_url",
        ),
    ):
        value = payload.get(name)
        if value is not None:
            if value not in choices:
                raise ValueError(f"Invalid {name}")
            evidence_url(payload.get(source))
        elif payload.get(source) is not None:
            raise ValueError(f"{source} requires {name}")
    if (
        payload.get("historical_detailed_sector") is not None
        and payload.get("historical_broad_sector") == "Government"
    ):
        raise ValueError(
            "Catholic/Independent historical detail contradicts Government broad sector"
        )


def validate_intervals(intervals: Any, parliament: int) -> None:
    if not isinstance(intervals, list):
        raise ValueError("Service decision requires the complete intervals list")
    info = PARLIAMENT_METADATA[parliament]
    ordered: list[tuple[date, date | None]] = []
    for row in intervals:
        if not isinstance(row, dict):
            raise ValueError("Invalid service interval")
        for name in ("party", "state_or_territory"):
            _text(row.get(name), name)
        if row.get("chamber") not in CANONICAL_CHAMBERS:
            raise ValueError("Invalid service chamber")
        start = _date(row.get("service_start"), "service_start")
        end = (
            _date(row["service_end"], "service_end") if row.get("service_end") else None
        )
        if start < date.fromisoformat(info["opening_date"]) or (end and end < start):
            raise ValueError("Invalid service interval dates")
        if info["end_date"] and (
            end is None or end > date.fromisoformat(info["end_date"])
        ):
            raise ValueError("Service interval falls outside parliament")
        evidence_url(row.get("source_url"))
        _timestamp(row.get("retrieved_at"), "retrieved_at")
        ordered.append((start, end))
    ordered.sort(key=lambda item: item[0])
    for left, right in zip(ordered, ordered[1:]):
        if left[1] is None or left[1] >= right[0]:
            raise ValueError("Overlapping service intervals")


def accepted_education_ancestor(
    event: ReviewEvent, events: list[ReviewEvent]
) -> ReviewEvent | None:
    """Recover attendance evidence retained by a later resolution-only decision.

    An accepted claim supersedes older claims. Rejection and untagged legacy
    research stop inheritance; map and tagged research replace resolution only.
    Competing attendance facts need an explicit acceptance rather than an
    arbitrary event-order winner.
    """
    if (
        event.effective_action == "research"
        and event.payload.get("resolution_only") is not True
    ):
        return None
    by_id = {item.decision_id: item for item in events}
    claims: dict[str, ReviewEvent] = {}
    pending = list(event.supersedes)
    visited: set[str] = set()
    while pending:
        identifier = pending.pop()
        if identifier in visited:
            continue
        visited.add(identifier)
        parent = by_id[identifier]
        if parent.effective_action == "accept":
            claims[identifier] = parent
        elif parent.effective_action != "reject" and not (
            parent.effective_action == "research"
            and parent.payload.get("resolution_only") is not True
        ):
            pending.extend(parent.supersedes)
    if not claims:
        return None
    facts = {
        json.dumps(
            {
                "source_url": claim.source_url,
                "notes": claim.notes,
                # Legacy acceptance caps attendance confidence by its match.
                # A reviewed reference and an automatic match can impose
                # different caps even when the explicit attendance facts agree.
                "reviewed_confidence_basis": bool(claim.payload.get("institution_ref")),
                **{
                    name: claim.payload.get(name)
                    for name in (
                        "recorded_school_name",
                        "attended_status",
                        "confidence",
                        "retrieved_at",
                        "years_attended",
                        "graduation_year",
                    )
                },
            },
            sort_keys=True,
        )
        for claim in claims.values()
    }
    if len(facts) != 1:
        raise ValueError("Resolve conflicting attendance facts with an accept decision")
    return claims[min(claims)]


def resolve_events(
    events: list[ReviewEvent], *, allow_conflicts: bool = False
) -> dict[str, ReviewEvent]:
    """Replay the supersession DAG independent of JSONL order."""
    by_id: dict[str, ReviewEvent] = {}
    for event in events:
        validate_event(event)
        if event.decision_id in by_id:
            raise ValueError(f"Duplicate decision_id {event.decision_id}")
        by_id[event.decision_id] = event
    replaced: set[str] = set()
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(event: ReviewEvent) -> None:
        if event.decision_id in visiting:
            raise ValueError(f"Supersession cycle at {event.decision_id}")
        if event.decision_id in visited:
            return
        visiting.add(event.decision_id)
        for parent_id in event.supersedes:
            parent = by_id.get(parent_id)
            if parent is None:
                raise ValueError(f"Missing superseded event {parent_id}")
            if (
                parent.review_id != event.review_id
                or parent.entity_type != event.entity_type
            ):
                raise ValueError("Supersession must remain within the same review key")
            replaced.add(parent_id)
            visit(parent)
        visiting.remove(event.decision_id)
        visited.add(event.decision_id)

    for event in events:
        visit(event)
    effective: dict[str, ReviewEvent] = {}
    for event in sorted(events, key=lambda item: item.decision_id):
        if event.decision_id in replaced:
            continue
        if event.review_id in effective and not allow_conflicts:
            raise ValueError(f"Conflicting active decisions for {event.review_id}")
        effective[event.review_id] = event
    return effective


def member_value_provider(
    event: ReviewEvent, events: list[ReviewEvent]
) -> ReviewEvent | None:
    """Find the accepted fact retained by a member disposition, validating ancestry."""
    if event.entity_type != "member":
        raise ValueError("Expected a member decision")
    if event.effective_action == "accept":
        return event
    if event.payload.get("proposal_only") is not True:
        return None
    retained_id = event.payload.get("retained_decision_id")
    if retained_id is None:
        return None
    by_id = {item.decision_id: item for item in events}
    retained = by_id.get(retained_id)
    if retained is None:
        raise ValueError(f"Missing retained decision {retained_id}")
    if (
        retained.review_id != event.review_id
        or retained.entity_type != "member"
        or retained.payload.get("field") != event.payload.get("field")
        or retained.effective_action != "accept"
    ):
        raise ValueError(
            f"Retained decision {retained_id} must be an accepted ancestor for {event.review_id}"
        )
    pending = list(event.supersedes)
    visited: set[str] = set()
    while pending:
        identifier = pending.pop()
        if identifier == retained_id:
            return retained
        if identifier not in visited:
            visited.add(identifier)
            parent = by_id.get(identifier)
            if parent is not None:
                pending.extend(parent.supersedes)
    raise ValueError(
        f"Retained decision {retained_id} is not an ancestor of {event.decision_id}"
    )


def validate_events(
    events: list[ReviewEvent],
    *,
    acara_ids: set[str] | None = None,
    member_ids: set[str] | None = None,
    allow_conflicts: bool = False,
) -> None:
    effective = resolve_events(events, allow_conflicts=allow_conflicts)
    registry = {
        event.payload["institution_ref"]
        for event in effective.values()
        if event.entity_type == "manual_institution"
        and event.effective_action == "accept"
    }
    qids: dict[str, str] = {}
    for event in events:
        if event.entity_type == "member":
            member_value_provider(event, events)
    for event in effective.values():
        if event.entity_type == "member":
            aph_id = event.payload["aph_id"]
            if member_ids is not None and aph_id.lower() not in {
                item.lower() for item in member_ids
            }:
                raise ValueError(f"{event.review_id}: Unknown member {aph_id}")
            provider = member_value_provider(event, events)
            if provider is not None and event.payload["field"] == "wikidata_id":
                qid = provider.payload["value"]
                if qid and qid in qids and qids[qid] != aph_id.lower():
                    raise ValueError(f"Conflicting member Wikidata ID {qid}")
                if qid:
                    qids[qid] = aph_id.lower()
            continue
        if event.effective_action not in ("accept", "map"):
            continue
        for field_name in ("institution_ref", "attended_institution_ref"):
            ref = event.payload.get(field_name)
            if (
                ref
                and ref.startswith("acara:")
                and acara_ids is not None
                and ref.split(":", 1)[1] not in acara_ids
            ):
                raise ValueError(f"{event.review_id}: ACARA ID does not exist: {ref}")
            if ref and ref.startswith("manual:") and ref not in registry:
                raise ValueError(
                    f"{event.review_id}: Missing active manual institution {ref}"
                )
        aph_id = event.payload.get("aph_id")
        if (
            aph_id
            and member_ids is not None
            and aph_id.lower() not in {x.lower() for x in member_ids}
        ):
            raise ValueError(f"{event.review_id}: Unknown member {aph_id}")


def active_heads(events: list[ReviewEvent]) -> dict[str, list[ReviewEvent]]:
    """Return all surviving alternatives, including conflicts, without a winner."""
    resolve_events(events, allow_conflicts=True)
    replaced = {parent for event in events for parent in event.supersedes}
    result: dict[str, list[ReviewEvent]] = {}
    for event in sorted(events, key=lambda item: item.decision_id):
        if event.decision_id not in replaced:
            result.setdefault(event.review_id, []).append(event)
    return result


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key {key}")
        result[key] = value
    return result


def parse_events(data: bytes, *, allow_conflicts: bool = False) -> list[ReviewEvent]:
    try:
        lines = data.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError(f"Invalid UTF-8 decision log: {exc}") from exc
    result: list[ReviewEvent] = []
    for number, line in enumerate(lines, 1):
        try:
            value = json.loads(line, object_pairs_hook=_unique_object)
            result.append(ReviewEvent.from_dict(value))
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Decision log line {number}: {exc}") from exc
    resolve_events(result, allow_conflicts=allow_conflicts)
    return result


def load_events(
    path: Path | str = DEFAULT_LOG_PATH, *, allow_conflicts: bool = False
) -> list[ReviewEvent]:
    path = Path(path)
    if not path.exists():
        return []
    return parse_events(path.read_bytes(), allow_conflicts=allow_conflicts)
