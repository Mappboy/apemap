"""Replay review authority over preserved, reproducible source-derived facts.

All persistence helpers participate in the caller's transaction. The pure preview
uses the same projection as ingestion without changing either the log or database.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from apemap.constants import PARLIAMENT_METADATA, current_parliament
from apemap.education_context import CONTEXT_FIELDS, SCHOOL_CONTEXT_FIELDS
from apemap.ingest.aph import parse_individual
from apemap.ingest.matching import (
    SchoolMatcher,
    extract_schools_from_bio_text,
    is_international_text,
    normalize_school_key,
    split_school_string,
)
from apemap.review.model import (
    DEFAULT_LOG_PATH,
    ReviewEvent,
    accepted_education_ancestor,
    education_review_id,
    load_events,
    parse_events,
    resolve_events,
    school_digest,
    school_key,
    school_review_id,
    validate_events,
)
from apemap.review.evidence import (
    EvidenceRecord,
    evidence_path,
    legacy_evidence,
    parse_evidence,
    validate_evidence_references,
)
from apemap.review.store import StaleReviewError

if TYPE_CHECKING:
    from duckdb import DuckDBPyConnection

    from apemap.ingest.matching import MatchedInstitution

SOURCE_TABLES = ("members", "institutions", "parliament_service", "member_education")
KEYS = {
    "members": "member_id",
    "institutions": "institution_id",
    "parliament_service": "service_id",
    "member_education": "education_id",
}
Records = dict[str, list[dict[str, Any]]]
RawInventory = dict[str, tuple[dict[str, Any], datetime]]


class _ReviewEventSnapshot(list[ReviewEvent]):
    """Carry the exact offline evidence revision consumed with the authority."""

    def __init__(
        self, events: list[ReviewEvent], path: Path, raw: bytes | None
    ) -> None:
        super().__init__(events)
        self.evidence_path = path
        self.evidence_raw = raw


def _review_evidence(
    events: list[ReviewEvent],
    *,
    decision_log_path: Path | str | None = None,
    evidence_log_path: Path | str | None = None,
) -> tuple[Path, bytes | None, list[EvidenceRecord]]:
    if isinstance(events, _ReviewEventSnapshot):
        if (
            evidence_log_path is not None
            and Path(evidence_log_path).resolve() != events.evidence_path.resolve()
        ):
            raise ValueError("Evidence log differs from the consumed review snapshot")
        path, raw = events.evidence_path, events.evidence_raw
    else:
        path = (
            Path(evidence_log_path)
            if evidence_log_path is not None
            else evidence_path(
                Path(decision_log_path)
                if decision_log_path is not None
                else DEFAULT_LOG_PATH
            )
        )
        raw = path.read_bytes() if path.exists() else None
    records = parse_evidence(raw or b"") + legacy_evidence(events)
    validate_evidence_references(events, records)
    return path, raw, records


def read_review_events(
    path: Path | str | None = None,
    *,
    evidence_log_path: Path | str | None = None,
) -> list[ReviewEvent]:
    """Read and validate local authority and evidence without external access."""
    ledger = Path(path) if path is not None else DEFAULT_LOG_PATH
    events = load_events(ledger)
    retained_path, raw, _ = _review_evidence(
        events, decision_log_path=ledger, evidence_log_path=evidence_log_path
    )
    return _ReviewEventSnapshot(events, retained_path, raw)


def configure_review_matcher(matcher: SchoolMatcher, events: list[ReviewEvent]) -> None:
    """Prevent unresolved review cases from silently gaining fuzzy matches."""
    matcher.review_blocked_keys = {
        normalize_school_key(str(event.payload["recorded_name"]))
        for event in resolve_events(events).values()
        if event.entity_type == "school"
        and event.effective_action in {"research", "reject"}
        and event.payload.get("recorded_name")
    }


def _records(conn: DuckDBPyConnection, *, source: bool = False) -> Records:
    result: Records = {}
    existing = {
        row[0]
        for row in conn.execute(
            "SELECT table_name FROM information_schema.tables"
        ).fetchall()
    }
    for table in SOURCE_TABLES:
        name = (
            f"review_source_{table}"
            if source and f"review_source_{table}" in existing
            else table
        )
        cursor = conn.execute(f"SELECT * FROM {name} ORDER BY {KEYS[table]}")
        columns = [column[0] for column in cursor.description]
        result[table] = [dict(zip(columns, row)) for row in cursor.fetchall()]
        if table == "member_education":
            for row in result[table]:
                for name in CONTEXT_FIELDS:
                    row.setdefault(name, None)
                recorded = row.get("school_name_as_recorded")
                if (
                    isinstance(recorded, str)
                    and recorded.strip()
                    and not row.get("recorded_school_id")
                ):
                    row["recorded_school_id"] = school_review_id(str(recorded))
    return result


def capture_review_sources(
    conn: DuckDBPyConnection, *, parliaments: list[int] | None = None
) -> None:
    """Replace derived source baselines after source ingestion, before overlays."""
    from apemap.db import backfill_recorded_school_ids

    backfill_recorded_school_ids(conn)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS review_source_cohorts (parliament_number INTEGER PRIMARY KEY)"
    )
    scope = set(parliaments or []) | {
        int(row[0])
        for row in conn.execute(
            "SELECT DISTINCT parliament_number FROM parliament_service"
        ).fetchall()
    }
    for parliament in sorted(scope):
        conn.execute(
            "INSERT INTO review_source_cohorts VALUES (?) ON CONFLICT DO NOTHING",
            [parliament],
        )
    for table in SOURCE_TABLES:
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS review_source_{table} AS SELECT * FROM {table} WHERE FALSE"
        )
        conn.execute(f"DELETE FROM review_source_{table}")
        conn.execute(f"INSERT INTO review_source_{table} BY NAME SELECT * FROM {table}")


def _source_cohorts(conn: DuckDBPyConnection) -> set[int]:
    exists = conn.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'review_source_cohorts'"
    ).fetchone()
    if exists and exists[0]:
        return {
            int(row[0])
            for row in conn.execute(
                "SELECT parliament_number FROM review_source_cohorts"
            ).fetchall()
        }
    # Compatibility with databases built before cohort metadata existed.
    return {
        int(row["parliament_number"])
        for row in _records(conn, source=True)["parliament_service"]
    }


def capture_raw_individual_inventory(
    conn: DuckDBPyConnection,
    raw_individuals: list[dict[str, Any]],
    *,
    retrieved_at: datetime,
) -> None:
    """Retain private source identity/evidence for omitted cohort memberships.

    Identical raw records retain their original acquisition time, so a preview
    and its subsequent rebuild produce the same source education facts.
    """
    conn.execute(
        """CREATE TABLE IF NOT EXISTS review_source_individuals (
            aph_id VARCHAR PRIMARY KEY, raw_json VARCHAR NOT NULL,
            retrieved_at TIMESTAMPTZ NOT NULL)"""
    )
    rows = [
        (
            str(raw["PHID"]).strip().lower(),
            json.dumps(raw, sort_keys=True, default=str),
            retrieved_at,
        )
        for raw in raw_individuals
        if str(raw.get("PHID") or "").strip()
    ]
    if rows:
        conn.executemany(
            """INSERT INTO review_source_individuals VALUES (?, ?, ?)
            ON CONFLICT (aph_id) DO UPDATE SET
                retrieved_at = CASE WHEN review_source_individuals.raw_json = EXCLUDED.raw_json
                    THEN review_source_individuals.retrieved_at ELSE EXCLUDED.retrieved_at END,
                raw_json = EXCLUDED.raw_json""",
            rows,
        )


def _raw_inventory(conn: DuckDBPyConnection) -> RawInventory:
    exists = conn.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'review_source_individuals'"
    ).fetchone()
    if not exists or not exists[0]:
        return {}
    return {
        str(aph_id): (json.loads(raw_json), stamp)
        for aph_id, raw_json, stamp in conn.execute(
            "SELECT aph_id, raw_json, retrieved_at FROM review_source_individuals"
        ).fetchall()
    }


def _individual_source_facts(
    raw: dict[str, Any], matcher: SchoolMatcher, retrieved_at: datetime
) -> Records:
    """Use one parser/matcher for both preview and omitted-membership rebuilds."""
    facts: Records = {table: [] for table in SOURCE_TABLES}
    parsed = parse_individual(raw)
    if parsed is None:
        return facts
    member = parsed.demographics
    facts["members"].append(
        {
            "member_id": member.member_id,
            "family_name": member.family_name,
            "given_name": member.given_name,
            "display_name": member.display_name,
            "gender": member.gender,
            "date_of_birth": date.fromisoformat(member.date_of_birth)
            if member.date_of_birth
            else None,
            "aph_id": member.aph_id,
            "wikidata_id": member.wikidata_id,
        }
    )
    schools = (
        split_school_string(parsed.secondary_school_raw)
        if parsed.secondary_school_raw
        else []
    )
    if not schools and parsed.bio_texts:
        schools = extract_schools_from_bio_text(parsed.bio_texts)
    institutions: dict[str, dict[str, Any]] = {}
    for recorded in dict.fromkeys(schools):
        match = matcher.match(recorded)
        institutions.setdefault(match.institution_id, _matched_institution(match))
        alias = (
            matcher.aliases.get(recorded.lower())
            or matcher.aliases.get(normalize_school_key(recorded))
            or {}
        )
        if alias and matcher.require_alias_sources and not alias.get("source_url"):
            alias = {}
        resolution = (
            alias.get(
                "relationship_type",
                "successor"
                if "amalgamat" in str(alias.get("notes", "")).lower()
                else "rename",
            )
            if alias
            else ("direct" if match.acara_id else "unresolved")
        )
        digest = hashlib.sha256(normalize_school_key(recorded).encode()).hexdigest()[
            :16
        ]
        facts["member_education"].append(
            {
                "education_id": f"edu-{member.aph_id.lower()}-{digest}",
                "member_id": member.member_id,
                "institution_id": match.institution_id,
                "level": "secondary",
                "years_attended": None,
                "graduation_year": None,
                "attended_status": "attended_unspecified",
                "source_url": parsed.source_url,
                "retrieved_at": retrieved_at,
                "confidence": match.confidence,
                "reviewer_notes": f"Parsed from APH Handbook text '{recorded}'; matched status: {match.confidence}"
                + (f"; {match.reviewer_notes}" if match.reviewer_notes else ""),
                "school_name_as_recorded": recorded,
                "institution_resolution": resolution,
                "resolution_source_url": alias.get("source_url"),
                "evidence_origin": "aph",
                **dict.fromkeys(CONTEXT_FIELDS),
                "recorded_school_id": school_review_id(recorded),
            }
        )
    facts["institutions"] = list(institutions.values())
    return facts


def _add_known_service_people(
    base: Records,
    events: list[ReviewEvent],
    matcher: SchoolMatcher,
    source_cohorts: set[int],
    inventory: RawInventory,
) -> Records:
    result = {table: [dict(row) for row in rows] for table, rows in base.items()}
    known = {
        str(row["aph_id"]).lower() for row in result["members"] if row.get("aph_id")
    }
    for event in resolve_events(events).values():
        if (
            event.entity_type != "service"
            or event.effective_action != "accept"
            or event.payload["parliament_number"] not in source_cohorts
            or not event.payload["intervals"]
        ):
            continue
        aph_id = str(event.payload["aph_id"]).lower()
        if aph_id in known or aph_id not in inventory:
            continue
        raw, stamp = inventory[aph_id]
        facts = _individual_source_facts(raw, matcher, stamp)
        for table in SOURCE_TABLES:
            keys = {row[KEYS[table]] for row in result[table]}
            result[table].extend(
                row for row in facts[table] if row[KEYS[table]] not in keys
            )
        known.add(aph_id)
    return result


def bootstrap_reviewed_source_people(
    conn: DuckDBPyConnection,
    events: list[ReviewEvent],
    matcher: SchoolMatcher,
    *,
    parliaments: list[int],
    refreshed_aph_ids: set[str] | None = None,
) -> None:
    """Import known APH facts before capturing a corrected membership's baseline."""
    configure_review_matcher(matcher, events)
    inventory = _raw_inventory(conn)
    base = _refresh_retained_source_people(
        _records(conn), matcher, inventory, refreshed_aph_ids or set()
    )
    facts = _add_known_service_people(
        base,
        events,
        matcher,
        _source_cohorts(conn) | set(parliaments),
        inventory,
    )
    _write_records(
        conn,
        {
            table: sorted(rows, key=lambda row: str(row[KEYS[table]]))
            for table, rows in facts.items()
        },
    )


def _refresh_retained_source_people(
    base: Records,
    matcher: SchoolMatcher,
    inventory: RawInventory,
    refreshed_aph_ids: set[str],
) -> Records:
    """Refresh APH facts for retained people with no source cohort membership."""
    source_members = {row["member_id"] for row in base["parliament_service"]}
    institutions = {row["institution_id"]: row for row in base["institutions"]}
    education = {row["education_id"]: row for row in base["member_education"]}
    for member in base["members"]:
        aph_id = str(member.get("aph_id") or "").lower()
        if (
            aph_id not in refreshed_aph_ids
            or aph_id not in inventory
            or member["member_id"] in source_members
        ):
            continue
        raw, stamp = inventory[aph_id]
        source = _individual_source_facts(raw, matcher, stamp)
        if not source["members"]:
            continue
        automatic_qid = member.get("wikidata_id")
        member.update(source["members"][0])
        member["wikidata_id"] = automatic_qid
        for row in source["institutions"]:
            institutions.setdefault(row["institution_id"], row)
        original_times = {
            key: row["retrieved_at"]
            for key, row in education.items()
            if row["member_id"] == member["member_id"]
        }
        education = {
            key: row
            for key, row in education.items()
            if not (
                row["member_id"] == member["member_id"]
                and (
                    row.get("evidence_origin") == "aph"
                    or (
                        row.get("evidence_origin") is None
                        and str(row.get("reviewer_notes") or "").startswith(
                            "Parsed from APH Handbook"
                        )
                    )
                )
            )
        }
        for row in source["member_education"]:
            row["retrieved_at"] = (
                original_times.get(row["education_id"]) or row["retrieved_at"]
            )
            education.setdefault(row["education_id"], row)
    base["institutions"] = list(institutions.values())
    base["member_education"] = list(education.values())
    return base


@contextmanager
def detached_member_children(
    conn: DuckDBPyConnection, *, replacement: Records | None = None
) -> Iterator[None]:
    """Detach child schemas for indexed parent updates in the caller transaction.

    DuckDB eagerly checks foreign-key indexes after child DELETEs. Dropping and
    recreating the two child tables inside the same transaction permits parent
    updates while preserving their exact constraints, explicit indexes and rows.
    The caller must roll back its transaction if the body or restoration fails.
    """
    children = ("parliament_service", "member_education")
    schemas: dict[str, str] = {}
    indexes: list[str] = []
    backups: dict[str, str] = {}
    suffix = uuid4().hex
    for table in children:
        row = conn.execute(
            "SELECT sql FROM duckdb_tables() WHERE schema_name = 'main' AND table_name = ? AND NOT temporary",
            [table],
        ).fetchone()
        if row is None or not row[0]:
            raise ValueError(f"Missing canonical child schema: {table}")
        schemas[table] = row[0]
        indexes.extend(
            row[0]
            for row in conn.execute(
                "SELECT sql FROM duckdb_indexes() WHERE schema_name = 'main' AND table_name = ? AND sql IS NOT NULL ORDER BY index_name",
                [table],
            ).fetchall()
        )
        backup = f"review_detached_{table}_{suffix}"
        backups[table] = backup
        conn.execute(f"CREATE TEMPORARY TABLE {backup} AS SELECT * FROM {table}")
    for table in reversed(children):
        conn.execute(f"DROP TABLE {table}")
    yield
    for table in children:
        conn.execute(schemas[table])
        if replacement is None:
            conn.execute(f"INSERT INTO {table} BY NAME SELECT * FROM {backups[table]}")
        else:
            for row in replacement[table]:
                columns = list(row)
                placeholders = ", ".join("?" for _ in columns)
                conn.execute(
                    f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
                    [row[column] for column in columns],
                )
        conn.execute(f"DROP TABLE {backups[table]}")
    for statement in indexes:
        conn.execute(statement)


def _write_records(conn: DuckDBPyConnection, records: Records) -> None:
    """Reconcile canonical rows without weakening their foreign-key constraints."""
    previous = _records(conn)
    if records == previous:
        return
    with detached_member_children(conn, replacement=records):
        for table in ("members", "institutions"):
            key = KEYS[table]
            old = {row[key]: row for row in previous[table]}
            changed = [row for row in records[table] if row != old.get(row[key])]
            if table == "members":
                for row in changed:
                    prior = old.get(row[key])
                    if prior and prior.get("wikidata_id") != row.get("wikidata_id"):
                        conn.execute(
                            "UPDATE members SET wikidata_id = NULL WHERE member_id = ?",
                            [row[key]],
                        )
            for row in changed:
                columns = list(row)
                if row[key] in old:
                    fields = [
                        column
                        for column in columns
                        if column != key and row[column] != old[row[key]].get(column)
                    ]
                    assignments = ", ".join(f"{column} = ?" for column in fields)
                    conn.execute(
                        f"UPDATE {table} SET {assignments} WHERE {key} = ?",
                        [row[column] for column in fields] + [row[key]],
                    )
                else:
                    placeholders = ", ".join("?" for _ in columns)
                    conn.execute(
                        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
                        [row[column] for column in columns],
                    )
            if table == "institutions":
                retained = {row[key] for row in records[table]}
                for identifier in old.keys() - retained:
                    if str(identifier).startswith("manual:"):
                        conn.execute(
                            "DELETE FROM institutions WHERE institution_id = ?",
                            [identifier],
                        )


def restore_review_sources(conn: DuckDBPyConnection) -> None:
    """Remove earlier overlays before a refresh, including rejected assertions."""
    exists = conn.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'review_source_members'"
    ).fetchone()
    if exists and exists[0]:
        _write_records(conn, _records(conn, source=True))


def _institution(
    reference: str,
    institutions: dict[str, dict[str, Any]],
    definitions: dict[str, ReviewEvent],
    matcher: SchoolMatcher,
) -> tuple[str, str]:
    if reference.startswith("acara:"):
        acara_id = reference.removeprefix("acara:")
        if acara_id not in matcher.acara_id_map:
            raise ValueError(f"Unknown ACARA institution reference: {reference}")
        identifier = f"acara-{acara_id}"
        ref = matcher.acara_id_map[acara_id]
        if identifier not in institutions:
            institutions[identifier] = {
                "institution_id": identifier,
                "acara_id": acara_id,
                "school_name": ref["school_name"],
                "school_type": ref.get("school_type"),
                "sector": ref["sector"],
                "campus_type": ref.get("campus_type"),
                "state": ref.get("state"),
                "suburb": ref.get("suburb"),
                "postcode": ref.get("postcode"),
                "longitude": ref.get("longitude"),
                "latitude": ref.get("latitude"),
                "country": "Australia",
                "institution_status": "unknown"
                if acara_id in matcher.current_ids
                else "historical_only",
            }
        return identifier, "verified"
    definition = definitions.get(reference)
    if not definition:
        raise ValueError(
            f"Manual institution is not defined by an accepted decision: {reference}"
        )
    payload = definition.payload
    institutions[reference] = {
        "institution_id": reference,
        "acara_id": None,
        "school_name": payload["school_name"],
        "sector": payload["sector"],
        "school_type": payload.get("school_type"),
        "campus_type": None,
        **{
            field: payload.get(field)
            for field in (
                "country",
                "state",
                "suburb",
                "postcode",
                "longitude",
                "latitude",
            )
        },
        "institution_status": payload.get("institution_status", "manual"),
    }
    return reference, "verified"


def _matched_institution(match: MatchedInstitution) -> dict[str, Any]:
    return {
        "institution_id": match.institution_id,
        "acara_id": match.acara_id,
        "school_name": match.school_name,
        "school_type": match.school_type,
        "sector": match.sector,
        "campus_type": match.campus_type,
        "state": match.state,
        "suburb": match.suburb,
        "postcode": match.postcode,
        "longitude": match.longitude,
        "latitude": match.latitude,
        "country": "overseas"
        if match.is_international
        else ("Australia" if match.acara_id else None),
        "institution_status": match.institution_status,
    }


def _apply_school_context(
    row: dict[str, Any],
    decision: ReviewEvent | None,
    institutions: dict[str, dict[str, Any]],
    definitions: dict[str, ReviewEvent],
    matcher: SchoolMatcher,
) -> None:
    """Replace all reviewed context, so later decisions cannot retain stale facts."""
    recorded_id = row.get("recorded_school_id")
    row.update(dict.fromkeys(CONTEXT_FIELDS))
    row["recorded_school_id"] = recorded_id
    if decision is None:
        return
    assertion_scope = decision.entity_type == "member_education"
    row["historical_context_scope"] = "assertion" if assertion_scope else "school"
    if decision.payload.get("relationship_type", "direct") != "successor":
        return
    payload = decision.payload
    for field_name in CONTEXT_FIELDS:
        if field_name not in {
            "recorded_school_id",
            "attended_institution_id",
            "historical_context_scope",
        }:
            row[field_name] = payload.get(field_name)
    if assertion_scope and any(
        payload.get(name) is not None
        for name in SCHOOL_CONTEXT_FIELDS
        if name != "historical_scope_confirmed"
    ):
        row["historical_scope_confirmed"] = True
    if payload.get("attended_institution_ref"):
        row["attended_institution_id"], _ = _institution(
            payload["attended_institution_ref"], institutions, definitions, matcher
        )


def _recorded_education_name(
    row: dict[str, Any], institutions: dict[str, dict[str, Any]]
) -> str:
    return str(
        row.get("school_name_as_recorded")
        or institutions[row["institution_id"]]["school_name"]
    )


def _education_claim(
    event: ReviewEvent, member_id: str, confidence: str
) -> dict[str, Any]:
    """Construct attendance evidence independently of its institution resolution."""
    payload = event.payload
    recorded = str(payload["recorded_school_name"])
    ranks = {"unconfirmed": 0, "provisional": 1, "verified": 2}
    return {
        "education_id": f"edu-{str(payload['aph_id']).lower()}-{school_digest(recorded)}",
        "member_id": member_id,
        "level": "secondary",
        "years_attended": payload.get("years_attended"),
        "graduation_year": payload.get("graduation_year"),
        "attended_status": payload["attended_status"],
        "source_url": event.source_url,
        "retrieved_at": datetime.fromisoformat(payload["retrieved_at"]),
        "confidence": min(
            (str(payload["confidence"]), confidence), key=lambda value: ranks[value]
        ),
        "reviewer_notes": event.notes,
        "school_name_as_recorded": recorded,
        "evidence_origin": "manual",
        **dict.fromkeys(CONTEXT_FIELDS),
        "recorded_school_id": school_review_id(recorded),
    }


def _unresolve_education(
    row: dict[str, Any],
    recorded: str,
    institutions: dict[str, dict[str, Any]],
    definitions: dict[str, ReviewEvent],
    matcher: SchoolMatcher,
) -> None:
    """Leave attendance evidence intact while withholding institution identity."""
    identifier = f"inst-unmatched-reviewed-{school_digest(recorded)}"
    institutions.setdefault(
        identifier,
        {
            "institution_id": identifier,
            "acara_id": None,
            "school_name": recorded,
            "school_type": "Secondary",
            "sector": "Other",
            "campus_type": None,
            "state": None,
            "suburb": None,
            "postcode": None,
            "longitude": None,
            "latitude": None,
            "country": "overseas" if is_international_text(recorded) else None,
            "institution_status": "unknown",
        },
    )
    row.update(
        institution_id=identifier,
        institution_resolution="unresolved",
        resolution_source_url=None,
    )
    _apply_school_context(row, None, institutions, definitions, matcher)
    row["historical_context_scope"] = "assertion"


def _project(
    base: Records,
    events: list[ReviewEvent],
    matcher: SchoolMatcher,
    source_cohorts: set[int],
    inventory: RawInventory,
) -> Records:
    # Copy every row so preview cannot mutate records read from the source baseline.
    result = _add_known_service_people(base, events, matcher, source_cohorts, inventory)
    effective = list(resolve_events(events).values())
    accepted = [
        event for event in effective if event.effective_action in {"accept", "map"}
    ]
    definitions = {
        str(event.payload["institution_ref"]): event
        for event in accepted
        if event.entity_type == "manual_institution"
    }
    institutions = {row["institution_id"]: row for row in result["institutions"]}
    for reference in sorted(definitions):
        _institution(reference, institutions, definitions, matcher)
    members = {
        str(row["aph_id"]).lower(): row
        for row in result["members"]
        if row.get("aph_id")
    }
    schools = {
        school_key(str(event.payload["recorded_name"])): event
        for event in accepted
        if event.entity_type == "school"
    }
    assertions = {
        event.review_id: event
        for event in effective
        if event.entity_type == "member_education"
    }
    member_aph_ids = {row["member_id"]: str(row["aph_id"]) for row in members.values()}
    for row in result["member_education"]:
        raw_name = _recorded_education_name(row, institutions)
        aph_id = member_aph_ids.get(row["member_id"])
        assertion = (
            assertions.get(education_review_id(aph_id, raw_name)) if aph_id else None
        )
        if assertion and (
            assertion.effective_action == "map"
            or (
                assertion.effective_action == "research"
                and assertion.payload.get("resolution_only") is True
            )
            or (
                assertion.effective_action == "accept"
                and "relationship_type" in assertion.payload
            )
        ):
            continue  # Individual decisions own both resolution and its context.
        decision = schools.get(school_key(str(raw_name)))
        if decision:
            identifier, confidence = _institution(
                str(decision.payload["institution_ref"]),
                institutions,
                definitions,
                matcher,
            )
            row.update(
                institution_id=identifier,
                confidence=confidence,
                institution_resolution=decision.payload["relationship_type"],
                resolution_source_url=decision.source_url,
            )
            _apply_school_context(row, decision, institutions, definitions, matcher)
        elif normalize_school_key(str(raw_name)) in matcher.review_blocked_keys:
            match = matcher.match(str(raw_name))
            if match.acara_id is None:
                institutions.setdefault(
                    match.institution_id, _matched_institution(match)
                )
                row.update(
                    institution_id=match.institution_id,
                    confidence=match.confidence,
                    institution_resolution="unresolved",
                    resolution_source_url=None,
                )
                _apply_school_context(row, None, institutions, definitions, matcher)
    for event in effective:
        payload = event.payload
        if (
            event.entity_type == "service"
            and payload["parliament_number"] not in source_cohorts
        ):
            continue
        member = members.get(str(payload.get("aph_id", "")).lower())
        if (
            event.entity_type in {"member", "member_education", "service"}
            and member is None
        ):
            continue  # A pinned cohort can legitimately omit the reviewed member.
        if event.entity_type == "member_education" and (
            event.effective_action == "map"
            or (
                event.effective_action == "research"
                and payload.get("resolution_only") is True
            )
        ):
            assert member is not None
            recorded = str(payload["recorded_school_name"])
            if not recorded.strip():
                continue  # A missing-education research case has no named claim.
            rows = [
                row
                for row in result["member_education"]
                if row["member_id"] == member["member_id"]
                and school_key(_recorded_education_name(row, institutions))
                == school_key(recorded)
            ]
            ancestor = accepted_education_ancestor(event, events)
            if ancestor is not None:
                match_confidence = (
                    "verified"
                    if ancestor.payload.get("institution_ref")
                    or schools.get(school_key(recorded))
                    else matcher.match(recorded).confidence
                )
                claim = _education_claim(
                    ancestor, str(member["member_id"]), match_confidence
                )
                result["member_education"] = [
                    row for row in result["member_education"] if row not in rows
                ]
                rows = [claim]
                result["member_education"].append(claim)
            if event.effective_action == "map":
                if not rows:
                    raise ValueError(
                        f"{event.review_id}: Mapping requires an existing attendance assertion"
                    )
                identifier, _ = _institution(
                    str(payload["institution_ref"]), institutions, definitions, matcher
                )
                for row in rows:
                    row.update(
                        institution_id=identifier,
                        institution_resolution=payload["relationship_type"],
                        resolution_source_url=event.source_url,
                    )
                    _apply_school_context(
                        row, event, institutions, definitions, matcher
                    )
            else:
                for row in rows:
                    _unresolve_education(
                        row,
                        _recorded_education_name(row, institutions)
                        if row.get("institution_id")
                        else str(row["school_name_as_recorded"]),
                        institutions,
                        definitions,
                        matcher,
                    )
            continue
        if (
            event.entity_type == "member_education"
            and event.effective_action == "reject"
        ):
            assert member is not None
            key = school_key(str(payload["recorded_school_name"]))
            result["member_education"] = [
                row
                for row in result["member_education"]
                if not (
                    row["member_id"] == member["member_id"]
                    and school_key(
                        str(
                            row.get("school_name_as_recorded")
                            or institutions[row["institution_id"]]["school_name"]
                        )
                    )
                    == key
                )
            ]
        if event not in accepted:
            continue
        if event.entity_type == "member":
            assert member is not None
            value = payload["value"]
            if payload["field"] == "date_of_birth" and value is not None:
                value = date.fromisoformat(value)
            member[str(payload["field"])] = value
        elif event.entity_type == "member_education":
            assert member is not None
            recorded = str(payload["recorded_school_name"])
            school = schools.get(school_key(recorded))
            reference = payload.get("institution_ref") or (
                school.payload["institution_ref"] if school else None
            )
            own_resolution = "relationship_type" in payload or (
                bool(payload.get("institution_ref"))
                and (school is None or reference != school.payload["institution_ref"])
            )
            resolution = event if own_resolution else school
            if reference:
                identifier, match_confidence = _institution(
                    str(reference), institutions, definitions, matcher
                )
            else:
                match = matcher.match(recorded)
                identifier, match_confidence = match.institution_id, match.confidence
                if identifier not in institutions:
                    institutions[identifier] = _matched_institution(match)
            claim = _education_claim(event, str(member["member_id"]), match_confidence)
            result["member_education"] = [
                row
                for row in result["member_education"]
                if row["education_id"] != claim["education_id"]
                and not (
                    row["member_id"] == member["member_id"]
                    and school_key(
                        str(
                            row.get("school_name_as_recorded")
                            or institutions[row["institution_id"]]["school_name"]
                        )
                    )
                    == school_key(recorded)
                )
            ]
            result["member_education"].append(
                {
                    **claim,
                    "institution_id": identifier,
                    "institution_resolution": resolution.payload.get(
                        "relationship_type", "direct"
                    )
                    if resolution
                    else ("direct" if reference else "unresolved"),
                    "resolution_source_url": resolution.source_url
                    if resolution is not None
                    and (resolution is school or "relationship_type" in payload)
                    else None,
                }
            )
            _apply_school_context(
                result["member_education"][-1],
                resolution,
                institutions,
                definitions,
                matcher,
            )
        elif event.entity_type == "service":
            assert member is not None
            parliament = int(payload["parliament_number"])
            result["parliament_service"] = [
                row
                for row in result["parliament_service"]
                if not (
                    row["member_id"] == member["member_id"]
                    and row["parliament_number"] == parliament
                )
            ]
            for interval in payload["intervals"]:
                start = date.fromisoformat(interval["service_start"])
                end = (
                    date.fromisoformat(interval["service_end"])
                    if interval.get("service_end")
                    else None
                )
                identity = [payload["aph_id"], parliament, interval]
                digest = hashlib.sha256(
                    json.dumps(identity, sort_keys=True).encode()
                ).hexdigest()[:16]
                result["parliament_service"].append(
                    {
                        "service_id": f"srv-{str(payload['aph_id']).lower()}-{parliament}-reviewed-{digest}",
                        "member_id": member["member_id"],
                        "parliament_number": parliament,
                        **{
                            field: interval.get(field)
                            for field in (
                                "chamber",
                                "party",
                                "electorate",
                                "state_or_territory",
                            )
                        },
                        "party_abbrev": interval.get("party_abbrev")
                        or interval["party"],
                        "service_start": start,
                        "service_end": end,
                        "is_opening_day_member": start.isoformat()
                        == PARLIAMENT_METADATA[parliament]["opening_date"],
                        "is_current_member": parliament == current_parliament()
                        and end is None,
                        "source_url": interval.get("source_url") or event.source_url,
                        "retrieved_at": datetime.fromisoformat(
                            interval["retrieved_at"]
                        ),
                        "source_service_start": start,
                        "source_service_end": end,
                    }
                )
    result["institutions"] = list(institutions.values())
    qids = [row["wikidata_id"] for row in result["members"] if row.get("wikidata_id")]
    if len(qids) != len(set(qids)):
        raise ValueError(
            "Reviewed Wikidata identifier is already assigned to another member"
        )
    return {
        table: sorted(rows, key=lambda row: str(row[KEYS[table]]))
        for table, rows in result.items()
    }


def project_review_records(
    conn: DuckDBPyConnection,
    events: list[ReviewEvent],
    matcher: SchoolMatcher | None = None,
    *,
    evidence_records: list[EvidenceRecord] | None = None,
) -> Records:
    """Return replayed source rows without changing the connection or authority.

    Complete institution validation belongs to the review service. A pinned cohort
    may omit reviewed schools and people, so the projector checks ACARA identity
    when that reference is actually applied, rather than against unrelated events.
    """
    active_matcher = matcher or SchoolMatcher()
    configure_review_matcher(active_matcher, events)
    validate_events(events)
    if evidence_records is not None:
        validate_evidence_references(events, evidence_records)
    elif isinstance(events, _ReviewEventSnapshot):
        _review_evidence(events)
    else:
        validate_evidence_references(events, legacy_evidence(events))
    return _project(
        _records(conn, source=True),
        events,
        active_matcher,
        _source_cohorts(conn),
        _raw_inventory(conn),
    )


def compare_review_records(before: Records, after: Records) -> dict[str, Any]:
    """Describe only changed rows while retaining total canonical table counts."""
    differences: dict[str, Any] = {}
    for table in SOURCE_TABLES:
        key = KEYS[table]
        old = {row[key]: row for row in before[table]}
        new = {row[key]: row for row in after[table]}
        changed = sorted(
            (
                identity
                for identity in old.keys() | new.keys()
                if old.get(identity) != new.get(identity)
            ),
            key=str,
        )
        if changed:
            differences[table] = {
                "before": [old[identity] for identity in changed if identity in old],
                "after": [new[identity] for identity in changed if identity in new],
                "before_count": len(old),
                "after_count": len(new),
                "added_count": len(new.keys() - old.keys()),
                "removed_count": len(old.keys() - new.keys()),
                "updated_count": sum(
                    identity in old and identity in new for identity in changed
                ),
            }
    return differences


def preview_review_events(
    conn: DuckDBPyConnection,
    events: list[ReviewEvent],
    matcher: SchoolMatcher | None = None,
) -> dict[str, Any]:
    """Return compact canonical changes using the same pure replay as ingestion."""
    return compare_review_records(
        _records(conn, source=True), project_review_records(conn, events, matcher)
    )


def capture_review_snapshot(
    conn: DuckDBPyConnection,
    events: list[ReviewEvent],
    *,
    decision_log_path: Path | str | None = None,
    evidence_log_path: Path | str | None = None,
    source_provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Archive the exact consumed ledger and its provenance in the caller transaction.

    Capture fails if the working log no longer contains the consumed events.
    Release metadata reads this stored revision rather than a mutable later log.
    """
    path = (
        Path(decision_log_path) if decision_log_path is not None else DEFAULT_LOG_PATH
    )
    raw = path.read_bytes() if path.exists() else b""
    if parse_events(raw) != events:
        raise StaleReviewError("Decision log changed during ingestion; rebuild")
    retained_path, evidence_raw, _ = _review_evidence(
        events,
        decision_log_path=path,
        evidence_log_path=evidence_log_path,
    )
    current_evidence = retained_path.read_bytes() if retained_path.exists() else None
    if current_evidence != evidence_raw:
        raise StaleReviewError("Evidence log changed during ingestion; rebuild")
    metadata: dict[str, Any] = {
        "schema_version": 1,
        "decision_log_sha256": hashlib.sha256(raw).hexdigest(),
        "event_count": len(events),
        "effective_decisions": len(resolve_events(events)),
        "source_parliaments": sorted(_source_cohorts(conn)),
        "source_provenance": source_provenance or {},
    }
    if evidence_raw is not None:
        metadata["evidence_log_sha256"] = hashlib.sha256(evidence_raw).hexdigest()
        metadata["evidence_count"] = len(parse_evidence(evidence_raw))
        metadata["evidence_source"] = "retained_review_evidence"
    if any(event.payload.get("evidence_refs") for event in events):
        metadata["referenced_evidence_count"] = len(
            {ref for event in events for ref in event.payload.get("evidence_refs", [])}
        )
    manifests = metadata["source_provenance"].get("input_manifests", {})
    manifest_digests = (
        {
            manifest.get("sha256")
            for manifest in manifests.values()
            if isinstance(manifest, dict) and isinstance(manifest.get("sha256"), str)
        }
        if isinstance(manifests, dict)
        else set()
    )
    previous_digest = review_snapshot_metadata(conn).get("source_manifest_sha256")
    if previous_digest in manifest_digests:
        metadata["source_manifest_sha256"] = previous_digest
    elif len(manifest_digests) == 1:
        metadata["source_manifest_sha256"] = next(iter(manifest_digests))
    encoded_metadata = json.dumps(metadata, sort_keys=True, allow_nan=False)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS review_build_snapshot (
            snapshot_id INTEGER PRIMARY KEY CHECK (snapshot_id = 1),
            decision_log_jsonl BLOB NOT NULL,
            metadata_json VARCHAR NOT NULL
        )"""
    )
    conn.execute(
        "INSERT OR REPLACE INTO review_build_snapshot VALUES (1, ?, ?)",
        [raw, encoded_metadata],
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS review_build_evidence (
            snapshot_id INTEGER PRIMARY KEY CHECK (snapshot_id = 1),
            evidence_jsonl BLOB NOT NULL
        )"""
    )
    conn.execute("DELETE FROM review_build_evidence")
    if evidence_raw is not None:
        conn.execute("INSERT INTO review_build_evidence VALUES (1, ?)", [evidence_raw])
    return metadata


def review_snapshot_metadata(conn: DuckDBPyConnection) -> dict[str, Any]:
    """Read provenance from the database's consumed revision, without file access."""
    exists = conn.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'review_build_snapshot'"
    ).fetchone()
    if not exists or not exists[0]:
        return {}
    row = conn.execute(
        "SELECT decision_log_jsonl, metadata_json FROM review_build_snapshot WHERE snapshot_id = 1"
    ).fetchone()
    if row is None:
        return {}
    metadata = json.loads(row[1])
    if (
        not isinstance(metadata, dict)
        or metadata.get("decision_log_sha256") != hashlib.sha256(row[0]).hexdigest()
    ):
        raise ValueError(
            "Stored review snapshot metadata does not match its archived ledger"
        )
    retained_records = []
    if "evidence_log_sha256" in metadata:
        evidence_exists = conn.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = 'review_build_evidence'"
        ).fetchone()
        retained = (
            conn.execute(
                "SELECT evidence_jsonl FROM review_build_evidence WHERE snapshot_id = 1"
            ).fetchone()
            if evidence_exists and evidence_exists[0]
            else None
        )
        if (
            retained is None
            or metadata["evidence_log_sha256"]
            != hashlib.sha256(retained[0]).hexdigest()
        ):
            raise ValueError(
                "Stored review snapshot metadata does not match its archived evidence"
            )
        retained_records = parse_evidence(retained[0])
        if metadata.get("evidence_count") != len(retained_records):
            raise ValueError("Stored review snapshot evidence count is inconsistent")
    events = parse_events(row[0])
    validate_evidence_references(events, retained_records + legacy_evidence(events))
    return metadata


def review_snapshot_evidence(conn: DuckDBPyConnection) -> bytes | None:
    """Return verified archived evidence bytes without reading working files."""
    metadata = review_snapshot_metadata(conn)
    if "evidence_log_sha256" not in metadata:
        return None
    row = conn.execute(
        "SELECT evidence_jsonl FROM review_build_evidence WHERE snapshot_id = 1"
    ).fetchone()
    return bytes(row[0]) if row else None


def attach_source_manifest(
    conn: DuckDBPyConnection,
    path: Path | str,
    *,
    manifest_bytes: bytes | None = None,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    """Attach consumed source provenance without rereading the decision ledger.

    Callers that already read the manifest can supply those exact bytes. A known
    digest additionally rejects source-manifest edits during a long-running build.
    """
    metadata = review_snapshot_metadata(conn)
    if not metadata:
        raise ValueError("Build the review snapshot before attaching an input manifest")
    path = Path(path)
    raw = path.read_bytes() if manifest_bytes is None else manifest_bytes
    digest = hashlib.sha256(raw).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise StaleReviewError("Input manifest changed during ingestion; rebuild")
    manifest = json.loads(raw)
    if not isinstance(manifest, dict):
        raise ValueError("Input manifest must be a JSON object")
    provenance = metadata.setdefault("source_provenance", {})
    manifests = provenance.setdefault("input_manifests", {})
    manifests[path.name] = {"sha256": digest, "manifest": manifest}
    metadata["source_manifest_sha256"] = digest
    encoded = json.dumps(metadata, sort_keys=True, allow_nan=False)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS review_build_input_manifests (
            manifest_sha256 VARCHAR PRIMARY KEY,
            manifest_json BLOB NOT NULL
        )"""
    )
    conn.execute(
        "INSERT INTO review_build_input_manifests VALUES (?, ?) ON CONFLICT DO NOTHING",
        [digest, raw],
    )
    conn.execute(
        "UPDATE review_build_snapshot SET metadata_json = ? WHERE snapshot_id = 1",
        [encoded],
    )
    return metadata


def apply_review_events(
    conn: DuckDBPyConnection,
    *,
    decision_log_path: Path | str | None = None,
    evidence_log_path: Path | str | None = None,
    events: list[ReviewEvent] | None = None,
    matcher: SchoolMatcher | None = None,
) -> dict[str, Any]:
    """Validate and reconcile all effective reviews within a caller transaction."""
    authority = (
        events
        if events is not None
        else read_review_events(decision_log_path, evidence_log_path=evidence_log_path)
    )
    _, _, records = _review_evidence(
        authority,
        decision_log_path=decision_log_path,
        evidence_log_path=evidence_log_path,
    )
    active_matcher = matcher or SchoolMatcher()
    projected = project_review_records(
        conn, authority, active_matcher, evidence_records=records
    )
    preview = compare_review_records(_records(conn, source=True), projected)
    _write_records(conn, projected)
    from apemap.review.candidates import load_into_duckdb

    load_into_duckdb(conn, authority)
    return preview
