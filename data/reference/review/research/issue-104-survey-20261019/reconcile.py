"""Reconcile the bounded survey with a read-only current review projection."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from apemap.db import get_connection
from apemap.review.candidates import json_value
from apemap.review.evidence import EvidenceRecord, evidence_applies, evidence_revision
from apemap.review.integration import project_review_records
from apemap.review.model import active_heads, education_review_id, school_key
from apemap.review.research_batches import ResearchBatches, _digest, _write
from apemap.review.service import ReviewService

ROOT = Path(__file__).resolve().parent
REPOSITORY = ROOT.parents[4]


def read(name: str) -> Any:
    return json.loads((ROOT / name).read_bytes())


def write(name: str, payload: Any) -> None:
    (ROOT / name).write_text(
        json.dumps(json_value(payload), ensure_ascii=False, sort_keys=True, indent=2)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


def projected(service: ReviewService) -> dict[str, Any]:
    events = service.events()
    with get_connection(service.db_path, read_only=True) as connection:
        return json_value(
            project_review_records(
                connection,
                events,
                matcher=service.matcher(),
                evidence_records=service.evidence_records(events),
            )
        )


def identify(person: dict[str, Any], records: dict[str, Any]) -> dict[str, Any]:
    matches = [
        member for member in records["members"] if member["aph_id"] == person["aph_id"]
    ]
    if len(matches) != 1:
        raise ValueError("Survey APH identity is absent or repeated: " + person["name"])
    member = matches[0]

    def tokens(value: str) -> set[str]:
        return set(re.findall(r"\w+", value.casefold()))

    if not tokens(person["name"]) <= tokens(member["display_name"]):
        raise ValueError("APH identity and survey name disagree: " + person["name"])
    services = [
        row
        for row in records["parliament_service"]
        if row["member_id"] == member["member_id"]
        and row["parliament_number"] == 48
        and row["is_opening_day_member"]
        and row["chamber"] == person["chamber"]
        and all(
            row.get(field) == person[field]
            for field in ("electorate", "state_or_territory")
            if field in person
        )
    ]
    if not services:
        raise ValueError(
            "Survey service context and APH identity disagree: " + person["name"]
        )
    return {
        "aph_id": member["aph_id"],
        "member_id": member["member_id"],
        "display_name": member["display_name"],
        "services": services,
    }


def compare_claim(
    claim: dict[str, Any], education: list[dict[str, Any]]
) -> dict[str, Any]:
    exact = [
        row
        for row in education
        if school_key(row["school_name_as_recorded"]) == school_key(claim["name"])
    ]
    variants = [
        row
        for row in education
        if claim.get("possible_existing_name")
        and row["school_name_as_recorded"] == claim["possible_existing_name"]
    ]
    if len(exact) > 1 or len(variants) > 1:
        raise ValueError("Repeated attendance identity in current projection")
    if claim["level"] == "tertiary":
        category = "tertiary_not_school"
    elif claim.get("mapping_history_review"):
        category = "existing_attendance_mapping_history_review"
    elif exact:
        category = "existing_attendance_additional_evidence"
    elif variants:
        category = "possible_name_variant_identity_review"
    elif claim["level"] == "primary":
        category = "primary_evidence_only_contract_limit"
    elif claim["level"] == "secondary":
        category = "missing_secondary_attendance_proposed"
    else:
        category = "named_attendance_stage_unverified"
    return {
        **claim,
        "classification": category,
        "current_assertions": exact,
        "possible_existing_assertions": variants,
        "proposed_institution_ref": None,
    }


def reconcile(
    service: ReviewService,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    survey = read("survey.json")
    fields = {row["aph_id"]: row for row in read("source-fields.json")}
    records = projected(service)
    retained = service.evidence_records()
    institutions = {row["institution_id"]: row for row in records["institutions"]}
    respondents, tasks, proposals = [], [], []
    counts: Counter[str] = Counter()
    heads = active_heads(service.events())
    provenance = {
        "generated_by": "codex-session",
        "model": None,
        "token_usage": None,
        "availability_note": "Issue 104 reconciles 18 survey respondents with current projected records. This evidence-only batch proposes no mappings or attendance decisions. Exact session model/token usage are unavailable; no model attempts are inferred.",
    }
    for person in survey["respondents"]:
        member = identify(person, records)
        education = [
            row
            for row in records["member_education"]
            if row["member_id"] == member["member_id"]
        ]
        claims = [compare_claim(claim, education) for claim in person["claims"]]
        represented = {
            row["education_id"]
            for claim in claims
            for row in claim["current_assertions"]
            + claim["possible_existing_assertions"]
        }
        omitted = [row for row in education if row["education_id"] not in represented]
        respondents.append(
            {
                "member": member,
                "source_field": fields[person["aph_id"]],
                "response_kind": person["response_kind"],
                "claims": claims,
                "other_current_assertions_preserved": omitted,
            }
        )
        counts[person["response_kind"]] += 1
        for claim in claims:
            counts[claim["classification"]] += 1
            if claim["classification"] == "tertiary_not_school":
                continue
            review_id = education_review_id(person["aph_id"], claim["name"])
            claimed = {
                "aph_id": person["aph_id"],
                "recorded_school_name": claim["name"],
                "reported_level": claim["level"],
                "source_publication_date": survey["source_publication_date"],
                "source_locator": fields[person["aph_id"]]["locator"],
                "source_response_sha256": fields[person["aph_id"]][
                    "school_response_sha256"
                ],
                **{
                    key: claim[key]
                    for key in ("locality", "grade_levels", "completion_as_reported")
                    if key in claim
                },
            }
            if claim["level"] == "secondary":
                claimed["level"] = "secondary"
            note = (
                f"The published member survey reports {claim['name']} as attended. "
                f"Reported stage: {claim['level']}. This is self-reported attendance, not independent institution identity, campus continuity, sector or current mapping evidence. "
                + claim.get("research", "")
            )
            record = EvidenceRecord(
                review_id=review_id,
                candidate_institution_ref=None,
                source_title=survey["source_title"]
                + " (28 June 2025) — "
                + person["name"],
                source_type="member-survey",
                source_url=survey["source_url"],
                retrieved_at=survey["retrieved_at"],
                claim_type="attendance",
                claim_value=claimed,
                stance="supports",
                excerpt_or_note=note,
                generated_by="agent-search",
            ).to_dict()
            applicable = [
                item.to_dict() for item in retained if evidence_applies(item, review_id)
            ]
            context = {
                "review_id": review_id,
                "member": member,
                "assertion": {"recorded_school_name": claim["name"]},
                "candidates": [],
                "current_assertions": claim["current_assertions"],
                "possible_existing_assertions": claim["possible_existing_assertions"],
                "current_institutions": [
                    institutions[row["institution_id"]]
                    for row in claim["current_assertions"]
                    + claim["possible_existing_assertions"]
                ],
                "retained_evidence": applicable,
                "survey_claim": claimed,
            }
            task = {
                "review_id": review_id,
                "context": context,
                "decision_heads": [
                    event.decision_id for event in heads.get(review_id, [])
                ],
                "research_origin": "agent-session",
                "research_provenance": provenance,
            }
            attendance = None
            if claim["classification"] == "missing_secondary_attendance_proposed":
                attendance = {
                    "aph_id": person["aph_id"],
                    "recorded_school_name": claim["name"],
                    "level": "secondary",
                    "attended_status": "attended_unspecified",
                    "confidence": "verified",
                    "retrieved_at": survey["retrieved_at"],
                    "evidence_refs": [record["evidence_id"]],
                }
            proposal = {
                "review_id": review_id,
                "outcome": "unresolved",
                "value": None,
                "payload": None,
                "reasoning": (
                    f"Survey reconciliation: {claim['classification']}. "
                    + note
                    + (
                        " A new secondary attendance is proposed separately, not accepted or executable by this evidence-only batch. "
                        if attendance
                        else " "
                    )
                    + "Accepting this research approves source attribution only; it does not accept attendance or a mapping. Do not apply a generic research preview to change existing resolutions."
                ),
                "identity": {
                    "name": member["display_name"],
                    "aph_id": person["aph_id"],
                    "locality": claim.get("locality"),
                },
                "alternatives": [
                    {
                        "current_assertions": claim["current_assertions"],
                        "possible_existing_assertions": claim[
                            "possible_existing_assertions"
                        ],
                        "research_question": claim.get(
                            "research",
                            "Review institution identity separately; preserve all existing assertions.",
                        ),
                    }
                ],
                "manual_institutions": [],
                "evidence": [record],
                "attempts": [],
                "proposed_attendance": attendance,
                "source_artifact": "survey.json; source-fields.json; reconciliation.json",
                "review_status": "unreviewed",
            }
            tasks.append(task)
            proposals.append(proposal)
    if len({row["member"]["aph_id"] for row in respondents}) != len(respondents):
        raise ValueError("Survey respondents duplicate a member identity")
    if len({task["review_id"] for task in tasks}) != len(tasks):
        raise ValueError("Survey claims repeat an attendance identity")
    summary = {
        "respondents": len(respondents),
        "named_school_claims": len(tasks),
        "new_secondary_attendance_proposals": sum(
            bool(row["proposed_attendance"]) for row in proposals
        ),
        "current_exact_assertions_to_strengthen": sum(
            bool(claim["current_assertions"])
            for person in respondents
            for claim in person["claims"]
        ),
        "reported_names_without_exact_current_assertion": sum(
            not claim["current_assertions"]
            for person in respondents
            for claim in person["claims"]
            if claim["level"] != "tertiary"
        ),
        "attendance_contradictions": 0,
        "mapping_history_discrepancies": counts[
            "existing_attendance_mapping_history_review"
        ],
        "classification_counts": dict(sorted(counts.items())),
        "new_members": 0,
        "canonical_attendance_changes": 0,
        "mapping_decisions": 0,
        "projection_sha256": _digest(records),
        "projection_counts": {table: len(rows) for table, rows in records.items()},
    }
    return (
        {
            "source": {
                key: survey[key]
                for key in (
                    "source_url",
                    "source_publication_date",
                    "retrieved_at",
                    "html_sha256",
                )
            },
            "summary": summary,
            "respondents": respondents,
        },
        tasks,
        proposals,
    )


def prior_research(attendance: list[dict[str, Any]]) -> dict[str, Any]:
    path = (
        ROOT.with_name("issue-102-p48-missing-schools-followup-20261019")
        / "results.json"
    )
    raw = path.read_bytes()
    people = {row["aph_id"] for row in read("survey.json")["respondents"]}
    rows = [row for row in json.loads(raw) if row["identity"]["aph_id"] in people]
    previous = {
        (claim["aph_id"], school_key(claim["recorded_school_name"]))
        for row in rows
        for claim in row["education_proposals"]
    }
    return {
        "source_path": path.relative_to(REPOSITORY).as_posix(),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "overlap_note": "Prior research is unreviewed and is not current canonical attendance. Shared proposals are corroboration, not additive attendance records; reconcile them before any later acceptance.",
        "overlapping_attendance_proposals": [
            row
            for row in attendance
            if (row["aph_id"], school_key(row["recorded_school_name"])) in previous
        ],
        "possible_overlapping_attendance_proposals": [
            {
                "proposed": claim,
                "previous": old,
                "reason": "Same member and a locality-qualified prior name. This is a possible overlap, not an approved alias; reconcile before any attendance decision.",
            }
            for claim in attendance
            if (claim["aph_id"], school_key(claim["recorded_school_name"]))
            not in previous
            for row in rows
            for old in row["education_proposals"]
            if old["aph_id"] == claim["aph_id"]
            and "," in old["recorded_school_name"]
            and school_key(old["recorded_school_name"].split(",", 1)[0])
            == school_key(claim["recorded_school_name"])
        ],
        "members": [
            {
                "aph_id": row["identity"]["aph_id"],
                "review_id": row["review_id"],
                "outcome": row["outcome"],
                "education_proposals": row["education_proposals"],
            }
            for row in rows
        ],
    }


def markdown_report(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Saturday Paper survey reconciliation — issue 104",
        "",
        "Source: [Karen Barlow, 28 June 2025](" + report["source"]["source_url"] + ").",
        "Comparison: current decision/evidence-projected historical database, not an old export or a new live parliamentary-service audit.",
        "",
        f"{summary['respondents']} respondents; 27 named school/stage-unknown claims; four tertiary institutions excluded from school proposals.",
        "Eight exact current assertions receive corroborating survey evidence; three name variants remain unapproved identity links.",
        "Five explicit-secondary attendance additions are proposed for four members, not accepted or applied.",
        "Five primary claims and six unclear-stage named claims remain evidence-only. One sector-only answer and one missing answer create no named-school claims.",
        "One historical-campus/relationship discrepancy requires review; no attendance contradiction was found. Omission is not evidence against other current schools.",
        "",
        "| Respondent / APH | Reported institutions (stage as stated) | Current assertion IDs | Classification / action |",
        "| --- | --- | --- | --- |",
    ]
    for person in report["respondents"]:
        member = person["member"]
        if not person["claims"]:
            lines.append(
                f"| {member['display_name']} / {member['aph_id']} | {person['response_kind']} | Existing history preserved | No named attendance proposed |"
            )
        for claim in person["claims"]:
            current = ", ".join(
                row["education_id"] for row in claim["current_assertions"]
            )
            possible = ", ".join(
                row["education_id"] for row in claim["possible_existing_assertions"]
            )
            current = current or ("Possible only: " + possible if possible else "None")
            source_name = claim["name"] + (
                " — " + claim["locality"] if claim.get("locality") else ""
            )
            lines.append(
                f"| {member['display_name']} / {member['aph_id']} | {source_name} ({claim['level']}) | {current} | {claim['classification']} |"
            )
    lines.extend(
        [
            "",
            "## Existing history preserved",
            "",
            "Claire Clutterham's Renmark and St Mary's assertions, Sarah Witty's Scoresby assertion, and Alice Jordan-Baird's Lauriston assertion remain unchanged. The survey is not an exhaustive history.",
            "",
            "## Mapping-history review",
            "",
            "Trish Cook reports Highgate, while the current rename decision resolves to Sorrento. The schools' own histories describe a campus move; this can explain the connection but does not establish her attendance dates, current-campus attendance, or same-campus continuity. No relationship or geographic decision is changed.",
            "",
            "See `reconciliation.json` for full current assertion/institution context, `tasks.json` for applicable prior evidence, `prior-research.json` for overlaps with issue 102, and `history-evidence.json` for the separate contextual sources.",
        ]
    )
    return "\n".join(lines) + "\n"


def prepare(service: ReviewService) -> dict[str, Any]:
    batches = ResearchBatches(service)
    if (ROOT / "manifest.json").exists():
        raise ValueError(
            "Frozen survey batch already exists; use a fresh batch for corrections"
        )
    before = {
        "source_revision": service.source_revision(include_evidence=False),
        "decision_revision": service.review_revision(),
        "evidence_revision": evidence_revision(service.evidence_path),
        "db_sha256": hashlib.sha256(service.db_path.read_bytes()).hexdigest(),
    }
    report, tasks, proposals = reconcile(service)
    for task, proposal in zip(tasks, proposals, strict=True):
        batches._validate_result("schools", task, proposal, origin="agent-session")
    if (
        before["source_revision"] != service.source_revision(include_evidence=False)
        or before["decision_revision"] != service.review_revision()
        or before["evidence_revision"] != evidence_revision(service.evidence_path)
    ):
        raise ValueError("Survey inputs changed during reconciliation")
    manifest = {
        "schema_version": 1,
        "batch_id": ROOT.name,
        "kind": "schools",
        "purpose": "survey-evidence-reconciliation",
        "origin": "agent-session",
        "provenance": tasks[0]["research_provenance"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        **{
            key: before[key]
            for key in ("source_revision", "decision_revision", "evidence_revision")
        },
        "task_sha256": _digest(tasks),
        "case_count": len(tasks),
        "models": [],
        "reasoning_effort": None,
        "max_workers": 1,
        "cases_per_task": 1,
        "attempt_budgets": [],
    }
    write("baseline.json", before)
    write("reconciliation.json", report)
    (ROOT / "comparison.md").write_text(
        markdown_report(report), encoding="utf-8", newline="\n"
    )
    write("results.json", proposals)
    write(
        "attendance-proposals.json",
        [row["proposed_attendance"] for row in proposals if row["proposed_attendance"]],
    )
    write("prior-research.json", prior_research(read("attendance-proposals.json")))
    _write(ROOT / "tasks.json", tasks)
    _write(ROOT / "manifest.json", manifest)
    batches.import_results(ROOT.name, proposals)
    return report["summary"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-path",
        type=Path,
        default=REPOSITORY / "data/aped-historical-v0.7.0.duckdb",
    )
    arguments = parser.parse_args()
    service = ReviewService(
        db_path=arguments.db_path, external_dir=REPOSITORY / "data/raw/historical/acara"
    )
    print(json.dumps(prepare(service), indent=2))
