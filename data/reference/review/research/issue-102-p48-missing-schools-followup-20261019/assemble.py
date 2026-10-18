"""Overlay four user-led findings without rewriting the original research."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any

from apemap.review.evidence import EvidenceRecord
from apemap.review.model import education_review_id

ROOT = Path(__file__).resolve().parent
BASELINE = (
    ROOT.parents[4]
    / "archive/research/2026-10-19/issue-102-p48-missing-schools-audited"
)


def assemble() -> list[dict[str, Any]]:
    baseline = (BASELINE / "results.json").read_bytes()
    if hashlib.sha256(baseline).hexdigest() != (
        "9c535ca0637a6ea961d3c83509b1ecd515569028cab1c7bb43ffa89f21199810"
    ):
        raise ValueError("Archived baseline research changed")
    results = json.loads(baseline)
    by_id = {row["review_id"]: row for row in results}
    followup = json.loads((ROOT / "follow-up.json").read_bytes())
    for update in followup["members"]:
        result = by_id[education_review_id(update["aph_id"], "")]
        original_stages = deepcopy(result["stages"])
        result["reasoning"] += " User-led follow-up: " + update["conclusion"]
        result["stages"]["final_broad"].update(
            outcome=update["outcome"],
            notes=result["stages"]["final_broad"]["notes"]
            + " Later user-led verification, not a second broad pass: "
            + update["conclusion"],
        )
        result["alternatives"].extend(update["alternatives"])
        result["research_log"]["followup"] = {
            "artifact": "follow-up.json",
            "original_stages": original_stages,
            "query_accounting": followup["query_accounting"],
            "member_findings": update,
        }
        for source in update["evidence"]:
            claim = {"aph_id": update["aph_id"], **source["claim_value"]}
            attendance = source["claim_type"] == "attendance"
            review_id = (
                education_review_id(update["aph_id"], claim["recorded_school_name"])
                if attendance
                else result["review_id"]
            )
            record = EvidenceRecord(
                review_id=review_id,
                candidate_institution_ref=None,
                source_type="agent-research",
                generated_by="agent-search",
                **{key: value for key, value in source.items() if key != "claim_value"},
                claim_value=claim,
            ).to_dict()
            result["evidence"].append(record)
            if attendance:
                result["education_proposals"].append(
                    {
                        **claim,
                        "confidence": "verified",
                        "retrieved_at": source["retrieved_at"],
                        "evidence_refs": [record["evidence_id"]],
                    }
                )
        result["outcome"] = (
            "attendance" if result["education_proposals"] else "unresolved"
        )
        result["source_artifact"] = (
            "follow-up.json; archived " + result["source_artifact"]
        )
    return results


if __name__ == "__main__":
    (ROOT / "results.json").write_text(
        json.dumps(assemble(), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
