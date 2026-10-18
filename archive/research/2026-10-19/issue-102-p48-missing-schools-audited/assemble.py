"""Convert retained research notes to individually reviewable batch proposals."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from apemap.review.evidence import EvidenceRecord
from apemap.review.model import education_review_id, member_review_id


ROOT = Path(__file__).resolve().parent


def completion(document: dict[str, Any]) -> str:
    window = document.get("retrieval_window", {})
    values = [window.get("completed_at"), window.get("end")]
    values.extend(
        value.get("completed_at")
        for value in document.get("retrieval_windows", {}).values()
    )
    return max(value for value in values if value)


def normalize(document: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    identity = raw["identity"]
    if "search_log" in raw:
        positive = bool(raw["schools"])
        skipped = raw["final_search"]["status"] in {"not_required", "not_needed"}
        uncertain = bool(raw.get("school_leads")) or any(
            word in raw.get("result_status", "")
            for word in ("incomplete", "further", "uncertain")
        )
        return {
            "name": raw["name"],
            "qid": raw["qid"],
            "qid_confirmed": identity.get("live_qid_status", "").startswith(
                "confirmed"
            ),
            "wikipedia_url": raw["wikipedia_url"],
            "identity_note": identity["corroboration"],
            "observed_at": identity["confirmed_at"],
            "schools": raw["schools"],
            "alternatives": raw.get("school_leads", []),
            "limitations": raw.get("limitations", []),
            "search_queries": raw["search_log"],
            "opened_sources": [
                {**entry, "status": entry["outcome"]} for entry in raw["access_log"]
            ],
            "reused_sources": [],
            "stages": {
                "wikipedia": {
                    "status": "completed",
                    "outcome": "found"
                    if positive and skipped
                    else "uncertain"
                    if uncertain
                    else "not_found",
                    "notes": raw["wikipedia"]["outcome"],
                },
                "final_broad": {
                    "status": "not_needed" if skipped else "completed",
                    "outcome": "not_needed"
                    if skipped
                    else "found"
                    if positive
                    else "uncertain"
                    if uncertain
                    else "not_found",
                    "notes": raw["final_search"]["outcome"],
                },
            },
        }
    if "external_search" in raw:
        positive = bool(raw["schools"])
        skipped = raw["external_search"]["status"] == "not_needed"
        sources = []
        for entry in raw["sources"] + raw["access_failures"]:
            note = entry.get("note", "")
            stage = (
                "wikipedia"
                if skipped
                or entry.get("source_type") == "wikipedia"
                or "Wikipedia-cited" in note
                else "final_broad"
            )
            sources.append(
                {
                    "url": entry.get("source_url"),
                    "stage": stage,
                    "status": entry["access_status"],
                    "note": note,
                }
            )
        sources.append(
            {
                "url": identity["source_url"],
                "stage": "wikipedia",
                "status": identity["status"],
                "note": identity["claim"],
            }
        )
        uncertain = bool(raw["unverified_leads"])
        return {
            "name": raw["member_name"],
            "qid": identity.get("wikidata_id"),
            "qid_confirmed": identity["status"] == "verified",
            "wikipedia_url": raw["wikipedia_url"],
            "identity_note": identity["claim"]
            + " "
            + json.dumps(identity.get("conflicts", []), ensure_ascii=False),
            "observed_at": completion(document),
            "schools": raw["schools"],
            "alternatives": raw["unverified_leads"],
            "limitations": raw.get("limitations", []),
            "search_queries": [
                {"stage": "final_broad", "query": query}
                for query in raw["external_search"]["queries"]
            ],
            "opened_sources": sources,
            "reused_sources": [],
            "stages": {
                "wikipedia": {
                    "status": "completed",
                    "outcome": "found"
                    if positive and skipped
                    else "uncertain"
                    if uncertain
                    else "not_found",
                    "notes": raw["wikipedia"]["notes"],
                },
                "final_broad": {
                    "status": "not_needed" if skipped else "completed",
                    "outcome": "not_needed"
                    if skipped
                    else "found"
                    if positive
                    else "uncertain"
                    if uncertain
                    else "not_found",
                    "notes": raw["external_search"]["notes"],
                },
            },
        }
    return {
        "name": raw["person"],
        "qid": identity.get("wikidata_id") or identity.get("cached_qid_hint"),
        "qid_confirmed": identity["wikidata_status"] == "confirmed",
        "wikipedia_url": identity["wikipedia_url"],
        "identity_note": identity["reasoning"],
        "observed_at": raw.get("collection_completed_at") or completion(document),
        "schools": [
            {
                **school,
                "recorded_school_name": school["name"],
                "source_url": school["attendance_url"],
                "paraphrase": school["attendance_note"],
            }
            for school in raw["schools"]
        ],
        "alternatives": raw["alternatives"],
        "limitations": raw["limitations"],
        "search_queries": raw["research_log"]["search_queries"],
        "opened_sources": raw["research_log"]["opened_sources"],
        "reused_sources": raw["research_log"].get("reused_sources", []),
        "stages": raw["stages"],
    }


def evidence(
    review_id: str,
    source_url: str,
    timestamp: str,
    claim_type: str,
    claim: dict[str, Any],
    note: str,
    source_title: str,
) -> dict[str, Any]:
    return EvidenceRecord(
        review_id=review_id,
        candidate_institution_ref=None,
        source_title=source_title,
        source_type="agent-research",
        source_url=source_url,
        retrieved_at=timestamp,
        claim_type=claim_type,
        claim_value=claim,
        stance="supports",
        excerpt_or_note=note,
        generated_by="agent-search",
    ).to_dict()


def proposal(
    task: dict[str, Any], item: dict[str, Any], artifact: str
) -> dict[str, Any]:
    aph_id = task["context"]["member"]["aph_id"]
    timestamp = item["observed_at"]
    records = []
    identity: dict[str, Any] = {
        "name": task["context"]["member"]["display_name"],
        "aph_id": aph_id,
        "wikidata_id": item["qid"] if item["qid_confirmed"] else None,
        "wikidata_status": "confirmed" if item["qid_confirmed"] else "unavailable",
        "cached_qid_hint": item["qid"],
        "wikipedia_url": item["wikipedia_url"],
        "wikipedia_status": "confirmed",
        "reasoning": item["identity_note"],
        "evidence_refs": {},
    }
    for field, url in [("wikipedia_url", item["wikipedia_url"])] + (
        [("wikidata_id", "https://www.wikidata.org/wiki/" + item["qid"])]
        if item["qid_confirmed"]
        else []
    ):
        record = evidence(
            member_review_id(aph_id, "wikidata_id"),
            url,
            timestamp,
            "member_identity",
            {"aph_id": aph_id, field: identity[field]},
            item["identity_note"],
            item["name"] + " — " + field,
        )
        records.append(record)
        identity["evidence_refs"][field] = [record["evidence_id"]]
    education = []
    for school in item["schools"]:
        name = school["recorded_school_name"]
        attendance = school.get("attended_status", "attended_unspecified")
        captured = school.get("retrieved_at", timestamp)
        claim = {
            "aph_id": aph_id,
            "recorded_school_name": name,
            "level": "secondary",
            "attended_status": attendance,
        }
        if school.get("graduation_year") is not None:
            claim["graduation_year"] = school["graduation_year"]
        note = school.get("paraphrase") or school.get("note")
        record = evidence(
            education_review_id(aph_id, name),
            school["source_url"],
            captured,
            "attendance",
            claim,
            note,
            school.get("source_title", item["name"] + " — sourced school attendance"),
        )
        records.append(record)
        education.append(
            {
                **claim,
                "confidence": "verified",
                "retrieved_at": captured,
                "evidence_refs": [record["evidence_id"]],
            }
        )
    sources = []
    broad = item["stages"]["final_broad"]["status"] == "completed"
    for original in item["opened_sources"]:
        entry = dict(original)
        phase = entry["stage"]
        entry["stage"] = (
            "wikipedia"
            if phase == "wikipedia"
            else "final_broad"
            if broad
            else "wikipedia"
        )
        if not entry.get("url"):
            entry.update(
                url=item["wikipedia_url"],
                target_url=None,
                operation="citation_access_target_unavailable",
            )
        if phase != entry["stage"]:
            entry["reported_phase"] = phase
        sources.append(entry)
    sources.sort(key=lambda entry: entry["stage"] != "wikipedia")
    notes = [
        item["stages"]["wikipedia"]["notes"],
        item["stages"]["final_broad"]["notes"],
        *item["limitations"],
    ]
    return {
        "review_id": task["review_id"],
        "outcome": "attendance" if education else "unresolved",
        "value": None,
        "payload": None,
        "reasoning": " ".join(notes),
        "identity": identity,
        "alternatives": item["alternatives"],
        "manual_institutions": [],
        "education_proposals": education,
        "evidence": records,
        "attempts": [],
        "stages": item["stages"],
        "research_log": {
            "searches": len(item["search_queries"]),
            "source_opens": len(sources),
            "search_queries": item["search_queries"],
            "opened_sources": sources,
            "reused_sources": item["reused_sources"],
            "accounting_note": "Operations grouped by research phase; the original raw artifact preserves reported phases and observed retrieval windows. Live identity verification can be a later cross-check. Reused sources are attributed separately and are not counted as new fetches. No exact per-request chronology is invented.",
        },
        "source_artifact": artifact,
        "review_status": "unreviewed",
    }


def assemble() -> list[dict[str, Any]]:
    tasks = {
        task["context"]["member"]["aph_id"].lower(): task
        for task in json.loads((ROOT / "tasks.json").read_bytes())
    }
    results = {}
    for path in sorted(ROOT.glob("research-*.json")):
        document = json.loads(path.read_bytes())
        for raw in (
            document.get("records") or document.get("members") or document["cases"]
        ):
            key = raw["aph_id"].lower()
            if key not in tasks or key in results:
                raise ValueError(
                    "Research contains an unknown or repeated member: " + key
                )
            results[key] = proposal(tasks[key], normalize(document, raw), path.name)
    if set(results) != set(tasks):
        raise ValueError("Research does not cover the complete frozen task population")
    return [results[key] for key in sorted(results)]


if __name__ == "__main__":
    print(json.dumps(assemble(), ensure_ascii=False, sort_keys=True, indent=2))
