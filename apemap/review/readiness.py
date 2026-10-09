"""Deterministic advisory impact scenarios over fixed opening-day cohorts."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import hashlib
import json
from typing import Any

from duckdb import DuckDBPyConnection

from apemap.analysis import (
    _attended_school_key,
    _sector_counts,
    _successor_sensitivity,
    get_opening_day_education_context,
    get_opening_day_members,
    summarize_shared_schools,
)
from apemap.review.evidence import (
    EvidenceRecord,
    evidence_applies,
    legacy_evidence,
    parse_evidence,
)
from apemap.review.model import (
    ReviewEvent,
    education_review_id,
    parse_events,
    resolve_events,
    school_review_id,
)
from apemap.review.resolution import requires_individual_resolution
from apemap.review.scoring import SCORING_VERSION, score_candidates

READINESS_VERSION = 1


def _top(summary: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "institution_id": row["institution_id"],
            "school_name": row["school_name"],
            "member_count": row["member_count"],
            "member_ids": sorted(member["member_id"] for member in row["members"]),
        }
        for row in summary["schools"][:10]
    ]


def _hypothetical(
    rows: list[dict[str, Any]], selected: set[str], candidate: dict[str, Any]
) -> list[dict[str, Any]]:
    copied = deepcopy(rows)
    sector = candidate.get("sector")
    for row in copied:
        if row["education_id"] not in selected:
            continue
        # An unreviewed successor cannot verify original identity, campus or sector.
        if row["is_successor"]:
            row.update(
                broad_sector=None, detailed_sector=None, sector_basis="unresolved"
            )
        else:
            row.update(
                attended_school_id=candidate["institution_id"],
                attended_school_name=candidate["school_name"],
                identity_basis="original_reference",
                broad_sector="Government"
                if sector == "Government"
                else "Non-government"
                if sector in {"Catholic", "Independent"}
                else None,
                detailed_sector=sector
                if sector in {"Government", "Catholic", "Independent"}
                else None,
                sector_basis="original_reference",
                resolved_institution_id=candidate["institution_id"],
                resolved_institution_name=candidate["school_name"],
                resolved_state=candidate.get("state"),
            )
    return copied


def cohort_readiness(
    members: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    institutions: list[dict[str, Any]],
    events: list[ReviewEvent],
    records: list[EvidenceRecord],
    parliament: int,
    aph_ids: dict[str, str],
) -> dict[str, Any]:
    """Compare candidate assignments without changing assertions or person denominators."""
    ids = {member["member_id"] for member in members}
    baseline = _sector_counts(ids, rows)
    shared = summarize_shared_schools(members, rows, parliament)
    top = _top(shared)
    decisions = resolve_events(events)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    context_gaps = []
    for row in rows:
        aph = aph_ids.get(row["member_id"])
        if not aph or not row.get("school_name_as_recorded"):
            context_gaps.append(
                {
                    "education_id": row["education_id"],
                    "impact": "unknown",
                    "reason": "Missing member review identity or recorded school name",
                }
            )
            continue
        case = education_review_id(aph, row["school_name_as_recorded"])
        own = decisions.get(case)
        default = decisions.get(school_review_id(row["school_name_as_recorded"]))
        selected = own or default
        # Both provisional source matches and explicit research dispositions remain unresolved.
        reviewed = (
            selected
            and selected.effective_action in {"accept", "map"}
            and selected.payload.get("institution_ref")
            and (own or not requires_individual_resolution(default))
        )
        if not reviewed:
            grouped[case].append(row)
    inventory = {row["institution_id"]: row for row in institutions}
    cases = []
    joint_members: dict[str, set[str]] = defaultdict(set)
    joint_assertions: dict[str, set[str]] = defaultdict(set)
    joint_names: dict[str, str] = {}
    for case, case_rows in sorted(grouped.items()):
        name = case_rows[0]["school_name_as_recorded"]
        refs = {row["resolved_institution_id"] for row in case_rows}
        refs.update(
            record.candidate_institution_ref.replace("acara:", "acara-", 1)
            for record in records
            if evidence_applies(record, case) and record.candidate_institution_ref
        )
        normalized = name.casefold()
        found = [
            row
            for row in institutions
            if row["institution_id"] in refs
            or normalized in str(row.get("school_name") or "").casefold()
        ]
        found = sorted(found, key=lambda row: row["institution_id"])
        truncated = len(found) > 50
        found = found[:50]
        proposals = [
            {
                **candidate,
                "institution_ref": candidate["institution_id"].replace(
                    "acara-", "acara:", 1
                ),
                "recorded_name": name,
            }
            for candidate in found
        ]
        ranked = score_candidates(case, proposals, records)
        selected_ids = {row["education_id"] for row in case_rows}
        scenarios = []
        for candidate in found:
            hypothetical = _hypothetical(rows, selected_ids, candidate)
            counts = _sector_counts(ids, hypothetical)
            candidate_top = _top(
                summarize_shared_schools(members, hypothetical, parliament)
            )
            differences = {
                field: {
                    key: counts[field][key] - baseline[field][key]
                    for key in baseline[field]
                }
                for field in (
                    "unique_parliamentarians_by_sector",
                    "government_non_government",
                    "attendance_instances_by_sector",
                )
            }
            sector_changed = any(
                value for values in differences.values() for value in values.values()
            )
            scenarios.append(
                {
                    "candidate_institution_ref": candidate["institution_id"].replace(
                        "acara-", "acara:", 1
                    ),
                    "sector_delta": differences,
                    "sector_impact": sector_changed,
                    "shared_top_ten_impact": candidate_top != top,
                    "shared_top_ten": candidate_top,
                }
            )
            if not any(row["is_successor"] for row in case_rows):
                key = candidate["institution_id"]
                joint_members[key].update(row["member_id"] for row in case_rows)
                joint_assertions[key].update(selected_ids)
                joint_names[key] = candidate["school_name"]
        cases.append(
            {
                "review_id": case,
                "recorded_name": name,
                "education_ids": sorted(selected_ids),
                "member_ids": sorted({row["member_id"] for row in case_rows}),
                "candidate_coverage": "bounded_local_search"
                if truncated
                else "local_search_only",
                "impact": "demonstrated_possible"
                if any(
                    scenario["sector_impact"] or scenario["shared_top_ten_impact"]
                    for scenario in scenarios
                )
                else "unknown",
                "unknown_reason": "Candidate discovery is not exhaustive; no candidate does not establish no impact.",
                "ranked_candidates": ranked,
                "scenarios": scenarios,
            }
        )
    baseline_members: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        baseline_members[_attended_school_key(row)].add(row["member_id"])
    threshold = top[-1]["member_count"] if len(top) == 10 else 2
    bounds = []
    for key, member_ids in sorted(joint_members.items()):
        upper = len(baseline_members[key] | member_ids)
        bounds.append(
            {
                "institution_id": key,
                "school_name": joint_names[key],
                "current_member_count": len(baseline_members[key]),
                "upper_member_count": upper,
                "unresolved_assertions": sorted(joint_assertions[key]),
                "can_reach_top_ten_threshold_or_tie": upper >= threshold,
                "interpretation": "Conservative simultaneous candidate bound; distinct people, not summed attendance rows.",
            }
        )
    canonical_inputs = json.dumps(
        {
            "members": members,
            "rows": rows,
            "institutions": list(inventory.values()),
            "events": [event.to_dict() for event in events],
            "evidence": [record.to_dict() for record in records],
        },
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    unresolved_people = {
        row["member_id"] for case_rows in grouped.values() for row in case_rows
    }
    unresolved_people.update(
        row["member_id"]
        for row in rows
        if row["education_id"] in {gap["education_id"] for gap in context_gaps}
    )
    missing_attendance = sorted(ids - {row["member_id"] for row in rows})
    return {
        "parliament_number": parliament,
        "cohort": "opening_day",
        "inputs_sha256": hashlib.sha256(canonical_inputs.encode()).hexdigest(),
        "baseline": baseline,
        "shared_top_ten": top,
        "shared_top_ten_tie_threshold": threshold,
        "cases": cases,
        "context_gaps": context_gaps,
        "missing_attendance_members": {
            "member_ids": missing_attendance,
            "impact": "unknown",
            "interpretation": "No secondary attendance assertion; potential new claims are outside fixed-assertion scenarios.",
        },
        "joint_sector_bounds": {
            "unresolved_people": len(unresolved_people),
            "interpretation": "Conservative bounds allowing unresolved people to change category together; category extrema are not independently achievable.",
            "counts": {
                field: {
                    key: {
                        "lower": max(0, value - len(unresolved_people)),
                        "upper": min(len(ids), value + len(unresolved_people)),
                    }
                    for key, value in baseline[field].items()
                }
                for field in (
                    "unique_parliamentarians_by_sector",
                    "government_non_government",
                )
            },
        },
        "joint_shared_school_bounds": bounds,
        "successor_sensitivity": _successor_sensitivity(ids, rows, baseline),
        "status": "unresolved_impact_or_unknown"
        if cases or context_gaps or missing_attendance
        else "no_unresolved_cases_detected",
    }


def readiness_report(
    conn: DuckDBPyConnection,
    events: list[ReviewEvent],
    records: list[EvidenceRecord],
    parliaments: list[int],
    provenance: dict[str, Any],
    extra_institutions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    cursor = conn.execute("SELECT * FROM institutions ORDER BY institution_id")
    columns = [item[0] for item in cursor.description]
    institutions = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
    inventory = {row["institution_id"]: row for row in institutions}
    for row in extra_institutions or []:
        inventory[row["institution_id"]] = row
    institutions = [inventory[key] for key in sorted(inventory)]
    aph_ids = dict(
        conn.execute(
            "SELECT member_id, aph_id FROM members WHERE aph_id IS NOT NULL"
        ).fetchall()
    )
    return {
        "schema_version": READINESS_VERSION,
        "scoring_version": SCORING_VERSION,
        "advisory": True,
        "policy": "Hypothetical candidate assignments; fixed people and attendance denominators. Local candidates are not exhaustive. Unknown cases remain review priorities; this report does not gate releases.",
        "provenance": provenance,
        "parliaments": [
            cohort_readiness(
                get_opening_day_members(conn, parliament),
                get_opening_day_education_context(conn, parliament),
                institutions,
                events,
                records,
                parliament,
                aph_ids,
            )
            for parliament in sorted(set(parliaments))
        ],
    }


def consumed_review_inputs(
    conn: DuckDBPyConnection,
) -> tuple[list[ReviewEvent], list[EvidenceRecord], dict[str, Any]]:
    from apemap.review.integration import (
        review_snapshot_metadata,
        review_snapshot_evidence,
    )

    provenance = review_snapshot_metadata(conn)
    if not provenance:
        return [], [], {"basis": "canonical_database_without_review_snapshot"}
    snapshot_row = conn.execute(
        "SELECT decision_log_jsonl FROM review_build_snapshot WHERE snapshot_id=1"
    ).fetchone()
    if snapshot_row is None:
        raise ValueError("Consumed review snapshot is unavailable")
    raw = snapshot_row[0]
    events = parse_events(bytes(raw))
    evidence = review_snapshot_evidence(conn)
    records = (
        parse_evidence(evidence) if evidence is not None else []
    ) + legacy_evidence(events)
    return events, records, provenance
