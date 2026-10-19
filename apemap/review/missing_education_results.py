"""Validate bounded missing-school research without creating attendance decisions."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any
from urllib.parse import urlsplit

from apemap.education_context import SCHOOL_CONTEXT_FIELDS
from apemap.ingest.matching import is_home_educated
from apemap.review.candidates import json_value
from apemap.review.evidence import EvidenceRecord, evidence_applies
from apemap.review.manual_institutions import validate_institution_fact_references
from apemap.review.model import (
    _timestamp,
    education_review_id,
    institution_review_id,
    member_review_id,
    school_key,
    school_review_id,
    validate_event,
)
from apemap.review.service import build_review_event

PURPOSE = "missing-secondary-education"
METADATA_FIELDS = {
    "purpose",
    "research_batch_id",
    "research_proposal_id",
    "research_approval_scope",
    "evidence_refs",
}


def is_missing_education(value: Mapping[str, Any]) -> bool:
    return value.get("purpose") == PURPOSE


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing-school research requires {field}")
    return value


def _refs(value: Any, records: Mapping[str, EvidenceRecord]) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(ref, str) or ref not in records for ref in value)
        or len(value) != len(set(value))
    ):
        raise ValueError(
            "Missing-school facts require unique cited evidence references"
        )
    return value


def attendance_source(proposal: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    references = set(payload["evidence_refs"])
    return next(
        record["source_url"]
        for record in proposal["evidence"]
        if record["evidence_id"] in references
        and record["stance"] == "supports"
        and record["claim_type"] in {"attendance", "member_attendance"}
        and isinstance(record["claim_value"], Mapping)
        and str(record["claim_value"].get("aph_id", "")).lower()
        == str(payload["aph_id"]).lower()
        and record["claim_value"].get("recorded_school_name")
        == payload["recorded_school_name"]
        and record["claim_value"].get("level") == "secondary"
    )


def decision_payloads(proposal: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {key: value for key, value in payload.items() if key not in METADATA_FIELDS}
        for payload in proposal["education_proposals"]
    ]


def _validate_identity(
    task: Mapping[str, Any], identity: Any, records: Mapping[str, EvidenceRecord]
) -> None:
    member = task["context"]["member"]
    aph_id = str(member["aph_id"]).lower()
    if (
        not isinstance(identity, dict)
        or str(identity.get("aph_id", "")).lower() != aph_id
        or school_key(_text(identity.get("name"), "person name"))
        not in {
            school_key(name)
            for name in [member["display_name"], *member.get("aliases", [])]
        }
    ):
        raise ValueError("Missing-school identity must identify the frozen member")
    _text(identity.get("reasoning"), "identity reasoning")
    references = identity.get("evidence_refs")
    if not isinstance(references, dict):
        raise ValueError("Identity evidence_refs must be an object")
    if set(references) - {"wikidata_id", "wikipedia_url"}:
        raise ValueError(
            "Identity references must identify the QID or Wikipedia article"
        )
    for field, status_field, domain in (
        ("wikidata_id", "wikidata_status", "www.wikidata.org"),
        ("wikipedia_url", "wikipedia_status", "en.wikipedia.org"),
    ):
        status = identity.get(status_field)
        value = identity.get(field)
        if status not in {"confirmed", "absent", "unavailable"}:
            raise ValueError(
                "Record confirmed, absent or unavailable person identities"
            )
        if status != "confirmed":
            if value is not None or references.get(field):
                raise ValueError("Unconfirmed person identities cannot supply a value")
            continue
        if field == "wikidata_id":
            if not isinstance(value, str) or not re.fullmatch(r"Q[1-9][0-9]*", value):
                raise ValueError(
                    "Confirmed person Wikidata identity requires a valid QID"
                )
            expected_path = "/wiki/" + value
            if member.get("wikidata_id") and member["wikidata_id"] != value:
                raise ValueError(
                    "Confirmed identity differs from the frozen member QID"
                )
        else:
            parsed = urlsplit(_text(value, "Wikipedia URL"))
            if parsed.scheme != "https" or parsed.hostname != domain:
                raise ValueError(
                    "Confirmed Wikipedia identity requires its article URL"
                )
            expected_path = parsed.path
            if not expected_path.startswith("/wiki/") or ":" in expected_path[6:]:
                raise ValueError("Wikipedia identity must identify a person article")
        refs = _refs(references.get(field), records)
        if not any(
            records[ref].review_id == member_review_id(aph_id, "wikidata_id")
            and records[ref].claim_type == "member_identity"
            and records[ref].stance == "supports"
            and isinstance(records[ref].claim_value, Mapping)
            and str(records[ref].claim_value.get("aph_id", "")).lower() == aph_id
            and records[ref].claim_value.get(field) == value
            and (urlsplit(records[ref].source_url).hostname or "").removeprefix("www.")
            == domain.removeprefix("www.")
            and urlsplit(records[ref].source_url).path == expected_path
            for ref in refs
        ):
            raise ValueError("Person identity requires exact cited identity evidence")


def _validate_stages(proposal: Mapping[str, Any]) -> None:
    stages = proposal.get("stages")
    if not isinstance(stages, dict) or set(stages) != {"wikipedia", "final_broad"}:
        raise ValueError("Record the Wikipedia-first and final broad research stages")
    wikipedia, broad = stages["wikipedia"], stages["final_broad"]
    if not isinstance(wikipedia, dict) or not isinstance(broad, dict):
        raise ValueError("Research stages must be objects")
    for stage in (wikipedia, broad):
        _text(stage.get("notes"), "research stage notes")
    if wikipedia.get("status") not in {"completed", "no_article", "unavailable"}:
        raise ValueError("The Wikipedia-first pass must be completed or accounted for")
    if wikipedia.get("outcome") not in {"found", "not_found", "uncertain"}:
        raise ValueError("Invalid Wikipedia research outcome")
    if wikipedia["status"] != "completed" and wikipedia["outcome"] == "found":
        raise ValueError("An unavailable Wikipedia article cannot establish attendance")
    if broad.get("status") not in {"completed", "not_needed"}:
        raise ValueError("The final broad pass must be completed once or not needed")
    if broad.get("outcome") not in {"found", "not_found", "uncertain", "not_needed"}:
        raise ValueError("Invalid final broad research outcome")
    if (broad["status"] == "not_needed") != (wikipedia["outcome"] == "found"):
        raise ValueError(
            "Only an unresolved Wikipedia pass permits the final broad pass"
        )
    if (broad["status"] == "not_needed") != (broad["outcome"] == "not_needed"):
        raise ValueError("A skipped broad pass must be marked not needed")
    found = wikipedia["outcome"] == "found" or broad["outcome"] == "found"
    if found != bool(proposal["education_proposals"]):
        raise ValueError(
            "Research stage conclusions must agree with attendance proposals"
        )
    wikipedia_status = proposal["identity"]["wikipedia_status"]
    if (
        wikipedia["status"]
        != {
            "confirmed": "completed",
            "absent": "no_article",
            "unavailable": "unavailable",
        }[wikipedia_status]
    ):
        raise ValueError(
            "Wikipedia stage must agree with the recorded article identity"
        )
    log = proposal.get("research_log")
    if not isinstance(log, dict):
        raise ValueError("Save the actual missing-school research log")
    for count, entries, field in (
        ("searches", "search_queries", "query"),
        ("source_opens", "opened_sources", "url"),
    ):
        values = log.get(entries)
        if (
            not isinstance(values, list)
            or type(log.get(count)) is not int
            or log[count] != len(values)
        ):
            raise ValueError("Research log counts must equal recorded operations")
        for entry in values:
            if not isinstance(entry, dict) or entry.get("stage") not in stages:
                raise ValueError("Research operations require their actual stage")
            _text(entry.get(field), "research operation " + field)
            if field == "url":
                _text(entry.get("status"), "source access result")
            if entry["stage"] == "final_broad" and broad["status"] == "not_needed":
                raise ValueError(
                    "A skipped broad pass cannot contain research operations"
                )
        if any(
            previous["stage"] == "final_broad" and current["stage"] == "wikipedia"
            for previous, current in zip(values, values[1:])
        ):
            raise ValueError("Wikipedia operations must precede the final broad pass")
    if broad["status"] == "completed" and not any(
        entry["stage"] == "final_broad"
        for entry in log["search_queries"] + log["opened_sources"]
    ):
        raise ValueError("A completed broad pass requires actual recorded operations")
    if sum(entry["stage"] == "final_broad" for entry in log["search_queries"]) > 6:
        raise ValueError(
            "The final broad pass permits at most six actual search queries"
        )
    if wikipedia_status == "confirmed" and not any(
        entry["stage"] == "wikipedia"
        and urlsplit(entry["url"]).hostname == "en.wikipedia.org"
        and urlsplit(entry["url"]).path
        == urlsplit(proposal["identity"]["wikipedia_url"]).path
        for entry in log["opened_sources"]
    ):
        raise ValueError("The Wikipedia-first log must include the person's article")


def validate_missing_education_result(
    task: Mapping[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    proposal = json_value(raw)
    member = task["context"]["member"]
    aph_id = str(member["aph_id"])
    if task["review_id"] != education_review_id(aph_id, ""):
        raise ValueError(
            "Missing-school tasks must preserve the missing member identity"
        )
    if raw.get("review_id") != task["review_id"] or raw.get("payload") is not None:
        raise ValueError("Missing-school results preserve their task identity")
    if raw.get("outcome") not in {"attendance", "unresolved"}:
        raise ValueError("Missing-school outcome must be attendance or unresolved")
    _text(raw.get("reasoning"), "research reasoning")
    for field in (
        "alternatives",
        "attempts",
        "manual_institutions",
        "evidence",
        "education_proposals",
    ):
        if not isinstance(raw.get(field), list):
            raise ValueError(f"{field} must be a list")
    if raw["attempts"] or (raw["outcome"] == "attendance") != bool(
        raw["education_proposals"]
    ):
        raise ValueError(
            "Session research must record actual outcomes and no inferred attempts"
        )
    records: dict[str, EvidenceRecord] = {}
    names = []
    for payload in raw["education_proposals"]:
        if (
            not isinstance(payload, dict)
            or str(payload.get("aph_id", "")).lower() != aph_id.lower()
        ):
            raise ValueError("Attendance proposals must identify the frozen member")
        name = _text(payload.get("recorded_school_name"), "secondary-school name")
        if is_home_educated(name) or school_key(name) in {
            school_key(value) for value in names
        }:
            raise ValueError(
                "Attendance proposals require distinct institutional schools"
            )
        names.append(name)
    allowed = {
        task["review_id"],
        member_review_id(aph_id, "wikidata_id"),
        *(education_review_id(aph_id, name) for name in names),
        *(school_review_id(name) for name in names),
    }
    for value in raw["evidence"]:
        if not isinstance(value, dict):
            raise ValueError("Evidence must be structured records")
        record = EvidenceRecord.from_dict(value)
        if (
            record.review_id not in allowed
            or record.generated_by != "agent-search"
            or record.evidence_id in records
            or any(
                getattr(record, field) is not None
                for field in (
                    "source_quality",
                    "match_strength",
                    "temporal_relevance",
                    "geographic_relevance",
                )
            )
        ):
            raise ValueError(
                "Missing-school evidence must preserve case and session provenance"
            )
        if (
            isinstance(record.claim_value, Mapping)
            and record.claim_value.get("aph_id") is not None
            and str(record.claim_value["aph_id"]).lower() != aph_id.lower()
        ):
            raise ValueError("Evidence belongs to a different member")
        records[record.evidence_id] = record
    _validate_identity(task, raw.get("identity"), records)
    _validate_stages(proposal)
    budget = task.get("research_budget")
    if budget is not None and any(
        type(budget.get(field)) is not int
        or budget[field] < 0
        or proposal["research_log"][field] > budget[field]
        for field in ("searches", "source_opens")
    ):
        raise ValueError(
            "Missing-school follow-up exceeds its new-round research budget"
        )
    for payload in proposal["education_proposals"]:
        review_id = education_review_id(aph_id, payload["recorded_school_name"])
        if set(payload) & (METADATA_FIELDS - {"evidence_refs"}):
            raise ValueError(
                "Attendance payloads cannot supply research approval metadata"
            )
        if payload.get("relationship_type") not in (None, "direct") or any(
            payload.get(field) is not None for field in SCHOOL_CONTEXT_FIELDS
        ):
            raise ValueError(
                "Missing-school proposals support direct institution identity only; resolve historical context separately"
            )
        refs = _refs(payload.get("evidence_refs"), records)
        selected = [records[ref] for ref in refs]
        if any(
            not evidence_applies(record, review_id)
            or record.candidate_institution_ref
            not in (None, payload.get("institution_ref"))
            for record in selected
        ):
            raise ValueError(
                "Attendance evidence must identify this exact assertion and target"
            )
        attendance = [
            record
            for record in selected
            if record.review_id == review_id
            and record.claim_type in {"attendance", "member_attendance"}
            and record.stance == "supports"
            and isinstance(record.claim_value, Mapping)
            and str(record.claim_value.get("aph_id", "")).lower() == aph_id.lower()
            and record.claim_value.get("recorded_school_name")
            == payload["recorded_school_name"]
            and record.claim_value.get("level") == "secondary"
            and record.candidate_institution_ref is None
        ]
        if not attendance or payload.get("confidence") != "verified":
            raise ValueError(
                "New attendance requires supporting person-specific secondary evidence"
            )
        if _timestamp(payload.get("retrieved_at"), "retrieved_at") != _timestamp(
            attendance[0].retrieved_at, "retrieved_at"
        ):
            raise ValueError(
                "Attendance retrieval time must match its supporting source"
            )
        if payload.get("level", "secondary") != "secondary":
            raise ValueError("New school attendance must be secondary education")
        for field in ("years_attended", "graduation_year", "attended_status"):
            value = payload.get(field)
            if (
                value is not None
                and not (field == "attended_status" and value == "attended_unspecified")
                and not any(
                    record.claim_value.get(field) == value for record in attendance
                )
            ):
                raise ValueError("Attendance dates and graduation cannot be inferred")
        if payload.get("institution_ref") and not any(
            record.stance == "supports"
            and record.claim_type in {"identity", "institution_identity"}
            and record.candidate_institution_ref == payload["institution_ref"]
            for record in selected
        ):
            raise ValueError(
                "Attendance targets require separate institution identity evidence"
            )
        validate_event(
            build_review_event(
                review_id,
                "accept",
                payload,
                source_url=attendance[0].source_url,
                reviewer="missing-school-validation",
            )
        )
    targets = {
        payload.get("institution_ref") for payload in proposal["education_proposals"]
    }
    definitions = set()
    for definition in proposal["manual_institutions"]:
        if (
            not isinstance(definition, dict)
            or definition.get("institution_ref") not in targets
            or definition.get("institution_ref") in definitions
        ):
            raise ValueError(
                "Manual definitions must identify a distinct proposed attendance target"
            )
        definitions.add(definition["institution_ref"])
        validate_event(
            build_review_event(
                institution_review_id(definition["institution_ref"]),
                "accept",
                definition,
                source_url=next(iter(records.values())).source_url,
                reviewer="missing-school-validation",
            )
        )
        if not validate_institution_fact_references(definition, records.values()):
            raise ValueError(
                "Manual definitions require individually cited institution facts"
            )
    proposal["evidence"] = [record.to_dict() for record in records.values()]
    proposal["purpose"] = PURPOSE
    proposal.pop("proposal_id", None)
    proposal.pop("eligibility_reasons", None)
    return proposal
