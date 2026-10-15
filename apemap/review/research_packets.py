"""Read-only access to frozen research packets with their actual provenance."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from apemap.review.evidence import EvidenceRecord
from apemap.review.model import education_review_id, school_review_id

PACKET_FILES = (
    "results.json",
    "scope.json",
    "research-log.json",
    "prior-research.json",
)
OUTCOME_LABELS = {
    "proposed_mapping": "Proposed mapping",
    "unresolved": "Needs further research",
    "non_institutional_education": "Home education; no institution",
}


def load_research_packet(path: Path) -> dict[str, Any]:
    manifest = json.loads((path / "packet.json").read_bytes())
    if (
        manifest["schema_version"] != 1
        or manifest["batch_id"] != path.name
        or manifest["kind"] != "schools"
        or set(manifest["files"]) != set(PACKET_FILES)
    ):
        raise ValueError("Unsupported research packet manifest")
    contents = {}
    for name in PACKET_FILES:
        # Git may translate text line endings between Windows and Linux.
        raw = (path / name).read_bytes().replace(b"\r\n", b"\n")
        if hashlib.sha256(raw).hexdigest() != manifest["files"][name]:
            raise ValueError(f"Research packet file changed: {name}")
        contents[name] = json.loads(raw)
    scope = {row["review_id"]: row for row in contents["scope.json"]}
    results = contents["results.json"]
    if (
        len(scope) != len(contents["scope.json"])
        or len(results) != manifest["case_count"]
        or len(results) != len(scope)
        or {result["review_id"] for result in results} != scope.keys()
    ):
        raise ValueError("Research packet findings differ from its frozen scope")
    for result in results:
        row = scope[result["review_id"]]
        if (
            result["review_id"] != education_review_id(row["aph_id"], row["school"])
            or result["recorded_school_name"] != row["school"]
            or result["school_review_id"] != school_review_id(row["school"])
            or result["outcome"] not in OUTCOME_LABELS
        ):
            raise ValueError(
                "Research packet finding has a different education identity"
            )
        for value in result["evidence"]:
            record = EvidenceRecord.from_dict(value)
            if record.review_id not in {
                result["review_id"],
                result["school_review_id"],
            }:
                raise ValueError("Research packet evidence belongs to another case")
        mapping = result.get("mapping")
        if (
            bool(mapping) != (result["outcome"] == "proposed_mapping")
            or mapping
            and (
                mapping["review_id"] != result["review_id"]
                or mapping["payload"]["aph_id"] != row["aph_id"]
                or mapping["payload"]["recorded_school_name"] != row["school"]
            )
        ):
            raise ValueError(
                "Research packet mapping has a different education identity"
            )
    return {
        "manifest": manifest,
        "results": results,
        "outcome_labels": OUTCOME_LABELS,
        "research_log": contents["research-log.json"],
        "case_logs": {
            case["review_id"]: case for case in contents["research-log.json"]["cases"]
        },
        "prior_research": {
            case["review_id"]: case for case in contents["prior-research.json"]
        },
    }
