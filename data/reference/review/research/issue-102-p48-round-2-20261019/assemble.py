"""Assemble bounded session observations into ordinary review proposals."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from apemap.review.evidence import EvidenceRecord
from apemap.review.model import education_review_id
from apemap.review.research_batches import _write


ROOT = Path(__file__).resolve().parent


def evidence_record(
    review_id: str,
    source: dict[str, Any],
    claim_type: str,
    claim: dict[str, Any],
    target: str | None = None,
) -> dict[str, Any]:
    return EvidenceRecord(
        review_id=review_id,
        candidate_institution_ref=target,
        source_title=source["title"],
        source_type="agent-research",
        source_url=source["url"],
        retrieved_at=source["retrieved_at"],
        claim_type=claim_type,
        claim_value=claim,
        stance="supports",
        excerpt_or_note=source["note"],
        generated_by="agent-search",
    ).to_dict()


def assemble(task: dict[str, Any], observation: dict[str, Any]) -> dict[str, Any]:
    proposal = deepcopy(task["prior_research"]["proposal"])
    aph_id = str(task["context"]["member"]["aph_id"])
    assert str(observation["aph_id"]).casefold() == aph_id.casefold()
    proposal.pop("proposal_id", None)
    proposal.pop("eligibility_reasons", None)
    proposal.pop("review_status", None)
    proposal.pop("source_artifact", None)
    proposal["evidence"] = [
        record
        for record in proposal["evidence"]
        if record["claim_type"] == "member_identity"
    ]
    proposal["education_proposals"] = []
    proposal["manual_institutions"] = []
    proposal["identity"]["reasoning"] += (
        " Identity evidence and its original retrieval dates are reused from the parent; this round's access checks are logged separately."
    )
    for school in observation["schools"]:
        name = school["recorded_school_name"]
        review_id = education_review_id(aph_id, name)
        claim = {"aph_id": aph_id, "recorded_school_name": name, "level": "secondary"}
        payload = {
            "aph_id": aph_id,
            "recorded_school_name": name,
            "attended_status": school.get("attended_status", "attended_unspecified"),
            "confidence": "verified",
            "retrieved_at": school["attendance_source"]["retrieved_at"],
        }
        for field in ("years_attended", "graduation_year"):
            if school.get(field) is not None:
                claim[field] = payload[field] = school[field]
        if payload["attended_status"] != "attended_unspecified":
            claim["attended_status"] = payload["attended_status"]
        if school.get("grade_levels") is not None:
            claim["grade_levels"] = school["grade_levels"]
        retained = [
            record
            for record in task["context"]["retained_evidence"]
            if record["claim_type"] == "attendance"
            and isinstance(record["claim_value"], dict)
            and record["claim_value"].get("recorded_school_name") == name
            and record["claim_value"].get("level") == "secondary"
            and record["source_url"] == school["attendance_source"]["url"]
            and record["retrieved_at"] == school["attendance_source"]["retrieved_at"]
        ]
        assert len(retained) <= 1
        attendance = (
            deepcopy(retained[0])
            if retained
            else evidence_record(
                review_id, school["attendance_source"], "attendance", claim
            )
        )
        proposal["evidence"].append(attendance)
        payload["evidence_refs"] = [attendance["evidence_id"]]
        target = school.get("target_candidate_reference")
        if target:
            source = school["institution_source"]
            assert school.get("candidate_verification"), (
                "Targets require a checked local candidate"
            )
            identity = evidence_record(
                review_id,
                source,
                "institution_identity",
                {
                    "name": school.get(
                        "canonical_name",
                        school["candidate_verification"]["school_name"],
                    ),
                    "country": school.get("country", "Australia"),
                    "recorded_school_name": name,
                },
                target,
            )
            proposal["evidence"].append(identity)
            payload.update(institution_ref=target, relationship_type="direct")
            payload["evidence_refs"].append(identity["evidence_id"])
        proposal["education_proposals"].append(payload)
    proposal["outcome"] = (
        "attendance" if proposal["education_proposals"] else "unresolved"
    )
    assert proposal["outcome"] == observation["outcome"]
    proposal["reasoning"] = observation["reasoning"]
    operations = deepcopy(observation["operations"])
    broad_started = False
    for operation in operations:
        if operation["stage"] == "final_broad":
            broad_started = True
        elif broad_started:
            operation["reported_stage"] = operation["stage"]
            operation["stage"] = "final_broad"
            operation["stage_note"] = (
                "Citation recheck during the final repair pass; original reported stage and chronology remain preserved."
            )
    searches = [
        {**operation, "stage": operation["stage"]}
        for operation in operations
        if operation["kind"] == "search"
    ]
    opens = []
    for operation in operations:
        if operation["kind"] != "open":
            continue
        opened = deepcopy(operation)
        if not opened.get("url"):
            opened["target_url"] = None
            opened["url"] = opened.get("referrer_url") or opened["referrer"]
            opened["status"] = (
                "Failed citation click; URL records the actual referrer, not an unknown target. "
                + opened["status"]
            )
        opens.append(opened)
    proposal["research_log"] = {
        "searches": len(searches),
        "source_opens": len(opens),
        "search_queries": searches,
        "opened_sources": opens,
        "reused_sources": observation.get("reused_sources", []),
        "parent": task["prior_research"]["parent"],
        "accounting_note": "Only this round's actual operations are counted. Prior findings and operations remain in tasks.json prior_research. Batched timestamps are observed request/group bounds, not invented per-request retrieval times. Explicitly labelled cached reads or screenshots conservatively consume source-open budget without asserting a new network fetch. Late Wikipedia citation rechecks belong to the final repair pass; reported_stage preserves the researcher's original label without reordering operations.",
    }
    found = bool(proposal["education_proposals"])
    proposal["stages"] = {
        "wikipedia": {
            "status": "completed",
            "outcome": "uncertain",
            "notes": "Opened the exact person's article first; identity evidence is reused with its original dates. Wikipedia and its leads alone do not establish an independently sourced new secondary assertion.",
        },
        "final_broad": {
            "status": "completed",
            "outcome": "found"
            if found
            else observation.get("final_broad_outcome", "uncertain"),
            "notes": observation["reasoning"],
        },
    }
    proposal["alternatives"] = observation.get("exclusions", [])
    proposal["attempts"] = []
    return proposal


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("research", nargs="+", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    tasks = json.loads((ROOT / "tasks.json").read_bytes())
    observations = [
        row for path in args.research for row in json.loads(path.read_bytes())
    ]
    by_member = {str(row["aph_id"]).casefold(): row for row in observations}
    assert len(by_member) == len(observations) == len(tasks) == 30
    assert set(by_member) == {
        str(task["context"]["member"]["aph_id"]).casefold() for task in tasks
    }
    ordered = [
        by_member[str(task["context"]["member"]["aph_id"]).casefold()] for task in tasks
    ]
    results = [assemble(task, row) for task, row in zip(tasks, ordered, strict=True)]
    for name, value in (("research.json", ordered), ("results.json", results)):
        if args.check:
            assert json.loads((ROOT / name).read_bytes()) == value
        elif (ROOT / name).exists():
            assert json.loads((ROOT / name).read_bytes()) == value
        else:
            _write(ROOT / name, value)
    comparison = (
        "# Round 2 case outcomes\n\nThese are unreviewed research results, not attendance or mapping decisions.\n\n| Member (APH ID) | Research outcome | Conclusion / remaining gap |\n| --- | --- | --- |\n"
        + "".join(
            f"| {row['name']} ({row['aph_id']}) | {row['outcome']} | {row['reasoning'].replace('|', '/').replace(chr(10), ' ')} |\n"
            for row in ordered
        )
    )
    comparison_path = ROOT / "comparison.md"
    if args.check or comparison_path.exists():
        assert comparison_path.read_text(encoding="utf-8") == comparison
    else:
        with comparison_path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(comparison)
    print(
        json.dumps(
            {
                "cases": len(results),
                "attendance_supported": sum(
                    bool(row["education_proposals"]) for row in results
                ),
                "school_assertions": sum(
                    len(row["education_proposals"]) for row in results
                ),
            }
        )
    )


if __name__ == "__main__":
    main()
