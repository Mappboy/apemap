"""Disposable candidate projections and DuckDB views over tracked events."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

import duckdb

from apemap.constants import RAW_WIKIMEDIA_DIR
from apemap.review.model import (
    ReviewEvent,
    active_heads,
    education_review_id,
    member_review_id,
    resolve_events,
    school_review_id,
    service_review_id,
)


def json_value(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str, allow_nan=False))


def candidate(
    review_id: str,
    entity_type: str,
    payload: dict[str, Any],
    evidence: dict[str, Any],
    parliaments: list[int],
) -> dict[str, Any]:
    evidence = json_value(evidence)
    # Retrieval timestamps do not make identical facts a new alternative.
    identity = {
        key: value
        for key, value in evidence.items()
        if key not in ("retrieved_at", "generated_at")
    }
    digest = hashlib.sha256(
        json.dumps(
            [review_id, payload, identity],
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()
    return {
        "review_id": review_id,
        "candidate_id": f"candidate:{digest}",
        "entity_type": entity_type,
        "payload": json_value(payload),
        "evidence": evidence,
        "parliaments": sorted(set(parliaments)),
    }


def _rows(conn: duckdb.DuckDBPyConnection, table: str) -> list[dict[str, Any]]:
    # Table names originate only in this module's fixed canonical inventory.
    source = f"review_source_{table}"
    found = conn.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?", [source]
    ).fetchone()
    if found and found[0]:
        table = source
    result = conn.execute(f'SELECT * FROM "{table}"')
    columns = [item[0] for item in result.description]
    return [dict(zip(columns, row)) for row in result.fetchall()]


def _cache_object(path: Path) -> dict[str, Any]:
    cached = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cached, dict):
        raise ValueError(f"Invalid Wikimedia cache object: {path}")
    cached = cached.get("result", cached.get("data", cached))
    if not isinstance(cached, dict):
        raise ValueError(f"Invalid Wikimedia cache object: {path}")
    return cached


def school_member_context(
    conn: duckdb.DuckDBPyConnection, review_id: str
) -> list[dict[str, Any]]:
    """Find source attendance and service context by the immutable school key."""
    members = {row["member_id"]: row for row in _rows(conn, "members")}
    institutions = {row["institution_id"]: row for row in _rows(conn, "institutions")}
    services: dict[str, list[dict[str, Any]]] = {}
    for row in _rows(conn, "parliament_service"):
        services.setdefault(row["member_id"], []).append(row)
    result: dict[str, dict[str, Any]] = {}
    for row in _rows(conn, "member_education"):
        institution = institutions.get(row["institution_id"], {})
        name = row.get("school_name_as_recorded") or institution.get("school_name", "")
        if not name or school_review_id(name) != review_id:
            continue
        member = members.get(row["member_id"])
        if not member:
            continue
        aph_id = member.get("aph_id")
        if row["member_id"] not in result:
            service_rows = services.get(row["member_id"], [])
            terms = {
                (
                    term["parliament_number"],
                    term["chamber"],
                    term.get("electorate") or "",
                    term["state_or_territory"],
                )
                for term in service_rows
            }
            result[row["member_id"]] = {
                "display_name": member["display_name"],
                "aph_id": aph_id,
                "biography_url": f"https://handbook.aph.gov.au/individual/{quote(aph_id, safe='')}"
                if aph_id
                else None,
                "services": [
                    {
                        "parliament": parliament,
                        "chamber": chamber,
                        "electorate": electorate,
                        "state": state,
                    }
                    for parliament, chamber, electorate, state in sorted(terms)
                ],
                "education": [],
            }
        result[row["member_id"]]["education"].append(
            {
                "review_id": education_review_id(aph_id, name) if aph_id else None,
                "recorded_name": name,
                "source_url": row.get("source_url"),
                "attended_status": row.get("attended_status"),
                "years_attended": row.get("years_attended"),
                "location": " · ".join(
                    str(institution[key])
                    for key in ("suburb", "state", "country")
                    if institution.get(key)
                ),
            }
        )
    return json_value(
        sorted(result.values(), key=lambda member: member["display_name"])
    )


def build_candidates(
    conn: duckdb.DuckDBPyConnection, *, cache_dir: Path = RAW_WIKIMEDIA_DIR
) -> list[dict[str, Any]]:
    """Generate cases from canonical source snapshots and cached evidence only."""
    members = _rows(conn, "members")
    services = _rows(conn, "parliament_service")
    education = _rows(conn, "member_education")
    institutions = {row["institution_id"]: row for row in _rows(conn, "institutions")}
    by_member: dict[str, list[int]] = {}
    member_index = {row["member_id"]: row for row in members}
    for row in services:
        by_member.setdefault(row["member_id"], []).append(row["parliament_number"])
    result: dict[str, dict[str, Any]] = {}
    educated: set[str] = set()
    for row in education:
        member = member_index.get(row["member_id"])
        if not member or not member.get("aph_id"):
            continue
        educated.add(row["member_id"])
        institution = institutions.get(row["institution_id"], {})
        name = row.get("school_name_as_recorded") or institution.get("school_name", "")
        payload = {"aph_id": member["aph_id"], "recorded_school_name": name}
        item = candidate(
            education_review_id(member["aph_id"], name),
            "member_education",
            payload,
            {**row, "display_name": member["display_name"]},
            by_member.get(row["member_id"], []),
        )
        result[item["candidate_id"]] = item
        school_id = school_review_id(name)
        school_item = candidate(
            school_id,
            "school",
            {"recorded_name": name},
            institution,
            by_member.get(row["member_id"], []),
        )
        if school_item["candidate_id"] in result:
            school_item["parliaments"] = sorted(
                set(
                    school_item["parliaments"]
                    + result[school_item["candidate_id"]]["parliaments"]
                )
            )
        result[school_item["candidate_id"]] = school_item
    for member in members:
        aph_id = member.get("aph_id")
        if not aph_id:
            continue
        parliaments = by_member.get(member["member_id"], [])
        if member["member_id"] not in educated:
            item = candidate(
                education_review_id(aph_id, ""),
                "member_education",
                {"aph_id": aph_id, "recorded_school_name": ""},
                {
                    "display_name": member["display_name"],
                    "notes": "No secondary education assertion",
                },
                parliaments,
            )
            result[item["candidate_id"]] = item
        member_cache = cache_dir / "members"
        identity_path = member_cache / f"{aph_id}.json"
        if not identity_path.exists():
            identity_path = member_cache / f"{aph_id.lower()}.json"
        name_key = re.sub(r"[^A-Za-z0-9_-]", "_", member["display_name"].lower())[:120]
        name_path = member_cache / f"name_{name_key or 'unknown'}.json"
        sources = [
            (path, kind)
            for path, kind in ((identity_path, "aph_id"), (name_path, "name"))
            if path.exists()
        ]
        caches = [(path.name, kind, _cache_object(path)) for path, kind in sources]
        if not caches:
            caches = [(None, "aph_id", {})]
        for filename, kind, cached in caches:
            for member_field in ("date_of_birth", "gender", "wikidata_id"):
                proposed = cached.get(member_field)
                if (
                    proposed is None
                    and member.get(member_field) is not None
                    and not cached.get("candidates")
                    and cached.get("status") != "conflict"
                ):
                    continue
                evidence = {
                    "display_name": member["display_name"],
                    "aph_value": member.get(member_field),
                    "proposed_value": proposed,
                    "source_url": cached.get("source_url"),
                    "source_query": cached.get("source_query"),
                    "status": cached.get("status"),
                    "notes": cached.get("notes"),
                    "match_method": kind,
                    "cache_file": filename,
                    "retrieved_at": cached.get("retrieved_at"),
                    "alternatives": cached.get("candidates", []),
                    "cache_missing": not bool(cached),
                }
                item = candidate(
                    member_review_id(aph_id, member_field),
                    "member",
                    {"aph_id": aph_id, "field": member_field},
                    evidence,
                    parliaments,
                )
                result[item["candidate_id"]] = item
    grouped_services: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for row in services:
        member = member_index.get(row["member_id"])
        if member and member.get("aph_id"):
            grouped_services.setdefault(
                (member["aph_id"], row["parliament_number"]), []
            ).append(row)
    for (aph_id, parliament), rows in grouped_services.items():
        rows.sort(key=lambda row: row["service_id"])
        item = candidate(
            service_review_id(aph_id, parliament),
            "service",
            {"aph_id": aph_id, "parliament_number": parliament},
            {"intervals": rows},
            [parliament],
        )
        result[item["candidate_id"]] = item
    # Retain cached school suggestions without HTTP or editing the cache.
    cache_institutions = cache_dir / "institutions"
    if cache_institutions.exists():
        from apemap.ingest.review import evaluate_school_candidate

        for path in sorted(cache_institutions.glob("*.json")):
            cached = _cache_object(path)
            name = cached.get("raw_school_text", "")
            if name and evaluate_school_candidate(name, cached)[0]:
                review_id = school_review_id(name)
                parliaments = [
                    p
                    for item in result.values()
                    if item["review_id"] == review_id
                    for p in item["parliaments"]
                ]
                item = candidate(
                    review_id, "school", {"recorded_name": name}, cached, parliaments
                )
                result[item["candidate_id"]] = item
    return sorted(
        result.values(), key=lambda item: (item["review_id"], item["candidate_id"])
    )


def annotate_candidates(
    items: list[dict[str, Any]], events: list[ReviewEvent]
) -> list[dict[str, Any]]:
    heads = active_heads(events)
    result = [dict(item) for item in items]
    known = {item["review_id"] for item in result}
    for review_id, alternatives in heads.items():
        event = alternatives[0]
        if review_id not in known:
            result.append(
                candidate(
                    review_id,
                    event.entity_type,
                    event.payload,
                    {"decision_only": True, "legacy": event.legacy},
                    [event.payload["parliament_number"]]
                    if event.payload.get("parliament_number")
                    else [],
                )
            )
    for item in result:
        alternatives = heads.get(item["review_id"], [])
        decision = alternatives[0] if len(alternatives) == 1 else None
        item["status"] = (
            "conflict"
            if len(alternatives) > 1
            else (decision.status if decision else "pending")
        )
        item["conflicts"] = (
            [event.decision_id for event in alternatives]
            if len(alternatives) > 1
            else []
        )
        item["decision_id"] = decision.decision_id if decision else None
        item["accepted_candidate"] = bool(
            decision
            and decision.status == "accepted"
            and decision.payload.get("candidate_id") == item["candidate_id"]
        )
    return sorted(result, key=lambda item: (item["review_id"], item["candidate_id"]))


def load_into_duckdb(
    conn: duckdb.DuckDBPyConnection,
    events: list[ReviewEvent],
    items: list[dict[str, Any]] | None = None,
) -> None:
    """Materialize disposable tables; source decisions always remain JSONL."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS review_decision_events (decision_id VARCHAR PRIMARY KEY, review_id VARCHAR, entity_type VARCHAR, action VARCHAR, status VARCHAR, event_json JSON)"
    )
    conn.execute("DELETE FROM review_decision_events")
    if events:
        conn.executemany(
            "INSERT INTO review_decision_events VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    e.decision_id,
                    e.review_id,
                    e.entity_type,
                    e.effective_action,
                    e.status,
                    json.dumps(e.to_dict(), allow_nan=False),
                )
                for e in events
            ],
        )
    effective = resolve_events(events)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS review_effective_ids (decision_id VARCHAR PRIMARY KEY)"
    )
    conn.execute("DELETE FROM review_effective_ids")
    if effective:
        conn.executemany(
            "INSERT INTO review_effective_ids VALUES (?)",
            [(e.decision_id,) for e in effective.values()],
        )
    conn.execute(
        "CREATE OR REPLACE VIEW review_effective_decisions AS SELECT e.* FROM review_decision_events e JOIN review_effective_ids i USING (decision_id)"
    )
    for view_name, entity in (
        ("resolved_school_mappings", "school"),
        ("resolved_member_education", "member_education"),
        ("resolved_service_corrections", "service"),
        ("review_manual_institutions", "manual_institution"),
    ):
        conn.execute(
            f"CREATE OR REPLACE VIEW {view_name} AS SELECT * FROM review_effective_decisions WHERE entity_type = '{entity}' AND status = 'accepted'"
        )
    if items is not None:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS review_candidates (candidate_id VARCHAR PRIMARY KEY, review_id VARCHAR, entity_type VARCHAR, candidate_json JSON)"
        )
        conn.execute("DELETE FROM review_candidates")
        if items:
            conn.executemany(
                "INSERT INTO review_candidates VALUES (?, ?, ?, ?)",
                [
                    (
                        x["candidate_id"],
                        x["review_id"],
                        x["entity_type"],
                        json.dumps(x, sort_keys=True),
                    )
                    for x in items
                ],
            )


def export_candidates(
    conn: duckdb.DuckDBPyConnection, items: list[dict[str, Any]], output_dir: Path
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    names = {
        "school": "school",
        "member": "member",
        "member_education": "education",
        "service": "service",
        "manual_institution": "institution",
    }
    for entity, name in names.items():
        selected = [item for item in items if item["entity_type"] == entity]
        conn.execute(
            "CREATE OR REPLACE TEMP TABLE review_export (review_id VARCHAR, candidate_id VARCHAR, entity_type VARCHAR, status VARCHAR, payload JSON, evidence JSON, parliaments JSON, decision_id VARCHAR)"
        )
        if selected:
            conn.executemany(
                "INSERT INTO review_export VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        x["review_id"],
                        x["candidate_id"],
                        entity,
                        x["status"],
                        json.dumps(x["payload"]),
                        json.dumps(x["evidence"]),
                        json.dumps(x["parliaments"]),
                        x["decision_id"],
                    )
                    for x in selected
                ],
            )
        conn.execute(
            "COPY (SELECT * FROM review_export ORDER BY review_id, candidate_id) TO ? (FORMAT PARQUET)",
            [str(output_dir / f"{name}_candidates.parquet")],
        )
        conn.execute(
            "COPY (SELECT * FROM review_export ORDER BY review_id, candidate_id) TO ? (FORMAT CSV, HEADER TRUE)",
            [str(output_dir / f"{name}_candidates.csv")],
        )
    with (output_dir / "review_summary.csv").open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        writer = csv.DictWriter(
            stream, fieldnames=["review_id", "entity_type", "status", "decision_id"]
        )
        writer.writeheader()
        seen: set[str] = set()
        for item in items:
            if item["review_id"] not in seen:
                writer.writerow({key: item[key] for key in writer.fieldnames})
                seen.add(item["review_id"])
