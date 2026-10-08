"""Shared offline review API for CLI and optional local forms."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter
from collections.abc import Callable
from copy import copy
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from apemap.constants import (
    DATA_DIR,
    EXTERNAL_DIR,
    PROJECT_ROOT,
    PROCESSED_DIR,
    RAW_APH_DIR,
    RAW_WIKIMEDIA_DIR,
)
from apemap.db import get_connection
from apemap.ingest.matching import SchoolMatcher, normalize_school_key
from apemap.review.candidates import (
    annotate_candidates,
    build_candidates,
    export_candidates,
    json_value,
    load_into_duckdb,
    school_member_context,
)
from apemap.review.model import (
    DEFAULT_LOG_PATH,
    ReviewEvent,
    active_heads,
    education_review_id,
    entity_for_review_id,
    load_events,
    parse_events,
    resolve_events,
    validate_events,
)
from apemap.review.store import StaleReviewError, append_events, log_revision

DEFAULT_REVIEW_DB = DATA_DIR / "aped-review.duckdb"
REGISTER_FILES = (
    "acara_school_results.json",
    "school-location-2025.csv",
    "school-location-2022.csv",
    "school-profile-2025.csv",
    "school-profile-2008-2025.csv",
    "school-profile-2022.csv",
    ".no-review-aliases.json",
)


def _register_text(value: Any) -> str | None:
    """Keep missing CSV metadata out of lookup labels."""
    if value is None:
        return None
    text = str(value).strip()
    return text if text and text.casefold() not in {"nan", "none", "<na>"} else None


def _school_lookup_rank(query: str, name: str) -> int | None:
    if query == name:
        return 0
    if name.startswith(query):
        return 1
    if query in name:
        return 2
    if all(token in name for token in query.split()):
        return 3
    return None


def default_reviewer() -> str:
    result = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={PROJECT_ROOT.as_posix()}",
            "config",
            "user.name",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    reviewer = result.stdout.strip()
    if not reviewer:
        raise ValueError("Supply --reviewer or configure git user.name")
    return reviewer


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _value_revision(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _school_state(events: list[ReviewEvent], event: ReviewEvent) -> dict[str, Any]:
    """Bind the school and its selected manual definition to immutable heads."""
    heads = active_heads(events)
    reference = str(event.payload.get("institution_ref", ""))
    return {
        "heads": sorted(head.decision_id for head in heads.get(event.review_id, [])),
        "manual_definition": sorted(
            head.decision_id for head in heads.get(f"institution:{reference}", [])
        )
        if reference.startswith("manual:")
        else [],
    }


def _effect_revision(changes: dict[str, Any]) -> str:
    """Compare actual decision/row effects, excluding dataset-wide totals."""
    canonical = changes.get("canonical")
    effects = None
    if canonical is not None:
        effects = {
            "baseline": canonical["baseline"],
            "before": canonical["before"],
            "after": {
                table: {side: change[side] for side in ("before", "after")}
                for table, change in canonical["after"].items()
            },
        }
    return _value_revision({"decisions": changes["decisions"], "canonical": effects})


class ReviewService:
    def __init__(
        self,
        log_path: Path = DEFAULT_LOG_PATH,
        db_path: Path | None = None,
        external_dir: Path | None = None,
    ) -> None:
        self.log_path = Path(log_path).resolve()
        self.db_path = (
            Path(db_path) if db_path is not None else DEFAULT_REVIEW_DB
        ).resolve()
        self.external_dir = Path(external_dir or EXTERNAL_DIR).resolve()
        self._register_lock = RLock()
        self._lookup_revision: tuple[tuple[str, str], ...] | None = None
        self._lookup_rows: list[tuple[tuple[str, ...], dict[str, Any]]] = []
        self._lookup_refs: dict[str, dict[str, Any]] = {}
        self._matcher_revision: tuple[tuple[str, str], ...] | None = None
        self._matcher: SchoolMatcher | None = None

    def events(self, *, allow_conflicts: bool = False) -> list[ReviewEvent]:
        return load_events(self.log_path, allow_conflicts=allow_conflicts)

    def _register_revision(self) -> tuple[tuple[str, str], ...]:
        paths = [self.external_dir / name for name in REGISTER_FILES]
        return tuple((str(path), file_digest(path)) for path in paths if path.is_file())

    def matcher(self) -> SchoolMatcher:
        # Cache compiled register data by content, not timestamps: same-size edits
        # and restored modification times must not retain an obsolete register.
        with self._register_lock:
            revision = self._register_revision()
            if self._matcher is None or revision != self._matcher_revision:
                self._matcher = SchoolMatcher(
                    external_dir=self.external_dir,
                    aliases_file=self.external_dir / ".no-review-aliases.json",
                )
                self._matcher_revision = revision
            matcher = copy(self._matcher)
        # Replay sets request-specific blocked keys. Never share that mutable
        # state between previews, lookup, ingestion or concurrent requests.
        matcher.review_blocked_keys = set()
        return matcher

    def _institution_lookup_rows(self) -> list[tuple[tuple[str, ...], dict[str, Any]]]:
        """Cache register metadata until a local source file changes."""
        with self._register_lock:
            return self._build_institution_lookup_rows()

    def _build_institution_lookup_rows(
        self,
    ) -> list[tuple[tuple[str, ...], dict[str, Any]]]:
        """Build and publish a complete snapshot while holding the cache lock."""
        source_revision = self._register_revision()
        if source_revision != self._lookup_revision:
            matcher = self.matcher()
            current_ids = {aid.strip() for aid in matcher.current_ids}
            has_current_register = (
                self.external_dir / "school-location-2025.csv"
            ).is_file()
            rows = []
            for aid, ref in matcher.acara_id_map.items():
                if not aid.isascii() or not aid.isdecimal():
                    continue
                name = _register_text(ref.get("school_name"))
                if not name:
                    continue
                rows.append(
                    (
                        tuple(
                            sorted(
                                {
                                    normalize_school_key(variant)
                                    for variant in matcher.registered_names.get(
                                        aid, {name}
                                    )
                                    if _register_text(variant)
                                }
                            )
                        ),
                        {
                            "institution_ref": f"acara:{aid}",
                            "acara_id": aid,
                            "school_name": name,
                            **{
                                field: _register_text(ref.get(field))
                                for field in (
                                    "state",
                                    "suburb",
                                    "sector",
                                    "school_type",
                                )
                            },
                            "institution_status": (
                                "current" if aid in current_ids else "historical_only"
                            )
                            if has_current_register
                            else "unknown",
                        },
                    )
                )
            self._lookup_rows = rows
            self._lookup_refs = {str(row["institution_ref"]): row for _, row in rows}
            self._lookup_revision = source_revision
        return self._lookup_rows

    def lookup_institutions(
        self, query: str, *, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Find ACARA school names locally without resolving or saving a decision.

        Return distinct IDs even when names collide. Status describes presence in
        the pinned 2025 register, never inferred closure or a successor mapping.
        """
        if len(query) > 200:
            raise ValueError("School-name queries must be at most 200 characters")
        if not 1 <= limit <= 50:
            raise ValueError("School lookup limit must be between 1 and 50")
        key = normalize_school_key(query)
        if len(key) < 2:
            return []
        ranked: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        for names, row in self._institution_lookup_rows():
            ranks = [
                rank
                for name in names
                if (rank := _school_lookup_rank(key, name)) is not None
            ]
            if not ranks:
                continue
            order = (
                min(ranks),
                row["school_name"].casefold(),
                row["state"] or "",
                row["suburb"] or "",
                row["acara_id"],
            )
            ranked.append((order, row))
        return [
            dict(row) for _, row in sorted(ranked, key=lambda item: item[0])[:limit]
        ]

    def source_revision(self) -> str:
        paths = [
            path
            for path in sorted(self.external_dir.glob("school-*.csv"))
            if path.is_file()
        ]
        register_json = self.external_dir / "acara_school_results.json"
        if register_json.exists():
            paths.append(register_json)
        paths.extend(sorted(RAW_WIKIMEDIA_DIR.rglob("*.json")))
        if self.db_path.exists():
            paths.append(self.db_path)
        wal_path = self.db_path.with_suffix(self.db_path.suffix + ".wal")
        if wal_path.exists():
            paths.append(wal_path)
        aph_path = RAW_APH_DIR / "individuals.json"
        if aph_path.exists():
            paths.append(aph_path)
        # These roots are resolved once, rather than resolving hundreds of cache
        # file paths on every guard. Every file's bytes are still hashed afresh.
        values = [(str(path.absolute()), file_digest(path)) for path in paths]
        return hashlib.sha256(json.dumps(values).encode()).hexdigest()

    def review_revision(self) -> str:
        """Read the ledger revision used to bind a rendered school draft."""
        return log_revision(self.log_path)

    def school_decisions(self) -> dict[str, list[dict[str, Any]]]:
        """Read all active school decisions once for grouped queue presentation."""
        return {
            key: [event.to_dict() for event in heads]
            for key, heads in active_heads(self.events(allow_conflicts=True)).items()
            if key.startswith("school:")
        }

    def resolve_institution(self, reference: str) -> dict[str, Any] | None:
        """Describe an exact local reference without granting mapping authority."""
        if reference.startswith("acara:"):
            with self._register_lock:
                self._institution_lookup_rows()
                row = self._lookup_refs.get(reference)
            return dict(row) if row is not None else None
        if reference.startswith("manual:"):
            heads = active_heads(self.events(allow_conflicts=True)).get(
                f"institution:{reference}", []
            )
            if len(heads) == 1 and heads[0].effective_action == "accept":
                return dict(heads[0].payload)
        return None

    def institution_resolver(self) -> Callable[[str], dict[str, Any] | None]:
        """Snapshot reference metadata once for a single presentation request."""
        with self._register_lock:
            self._institution_lookup_rows()
            references = dict(self._lookup_refs)
        for heads in active_heads(self.events(allow_conflicts=True)).values():
            if len(heads) == 1:
                event = heads[0]
                if (
                    event.entity_type == "manual_institution"
                    and event.effective_action == "accept"
                ):
                    references[event.payload["institution_ref"]] = event.payload

        def resolve(reference: str) -> dict[str, Any] | None:
            metadata = references.get(reference)
            return dict(metadata) if metadata is not None else None

        return resolve

    def check(self, events: list[ReviewEvent] | None = None) -> dict[str, Any]:
        selected = self.events() if events is None else events
        matcher = self.matcher()
        result = self._check_references(selected, matcher)
        # Projector also checks uniqueness against source QIDs, not just events.
        if self.db_path.exists():
            from apemap.review.integration import project_review_records

            with get_connection(self.db_path, read_only=True) as conn:
                project_review_records(conn, selected, matcher=matcher)
        return result

    def _check_references(
        self, selected: list[ReviewEvent], matcher: SchoolMatcher
    ) -> dict[str, Any]:
        """Validate event/reference integrity without repeating canonical replay."""
        refs = [
            e
            for e in resolve_events(selected).values()
            if e.effective_action in ("accept", "map")
            and str(e.payload.get("institution_ref", "")).startswith("acara:")
        ]
        if refs and not matcher.acara_id_map:
            raise ValueError(
                "No pinned ACARA register available; supply --external-dir"
            )
        member_ids: set[str] = set()
        aph_path = RAW_APH_DIR / "individuals.json"
        has_full_inventory = aph_path.exists()
        if has_full_inventory:
            raw = json.loads(aph_path.read_text(encoding="utf-8"))
            member_ids.update(
                str(row["PHID"]).lower() for row in raw if row.get("PHID")
            )
        if self.db_path.exists():
            with get_connection(self.db_path, read_only=True) as conn:
                member_ids.update(
                    str(row[0]).lower()
                    for row in conn.execute(
                        "SELECT aph_id FROM members WHERE aph_id IS NOT NULL"
                    ).fetchall()
                )
        validate_events(
            selected,
            acara_ids=set(matcher.acara_id_map),
            member_ids=member_ids if member_ids else None,
        )
        return {
            "valid": True,
            "events": len(selected),
            "effective_decisions": len(resolve_events(selected)),
            "revision": log_revision(self.log_path),
            "warnings": []
            if has_full_inventory
            else [
                "Complete pinned APH inventory unavailable; member existence checked only where local source records are available"
            ],
        }

    def candidates(
        self,
        entity_type: str | None = None,
        status: str | None = None,
        parliament: int | None = None,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        if self.db_path.exists():
            with get_connection(self.db_path, read_only=True) as conn:
                items = build_candidates(conn)
        items = annotate_candidates(items, self.events(allow_conflicts=True))
        aliases = {
            "schools": "school",
            "members": "member",
            "education": "member_education",
            "institutions": "manual_institution",
        }
        entity_type = aliases.get(entity_type, entity_type)
        return [
            item
            for item in items
            if (not entity_type or item["entity_type"] == entity_type)
            and (not status or item["status"] == status)
            and (parliament is None or parliament in item["parliaments"])
            and (
                not search
                or search.lower() in json.dumps(item, ensure_ascii=False).lower()
            )
        ]

    def history(self, review_id: str) -> list[dict[str, Any]]:
        return [
            event.to_dict()
            for event in sorted(
                self.events(allow_conflicts=True),
                key=lambda event: (event.recorded_at, event.decision_id),
            )
            if event.review_id == review_id
        ]

    def _case_candidates(
        self, review_id: str, events: list[ReviewEvent]
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        if self.db_path.exists():
            with get_connection(self.db_path, read_only=True) as conn:
                items = build_candidates(conn, review_id=review_id)
        return [
            item
            for item in annotate_candidates(items, events)
            if item["review_id"] == review_id
        ]

    def show(self, review_id: str) -> dict[str, Any]:
        entity = entity_for_review_id(review_id)
        events = self.events(allow_conflicts=True)
        candidates = self._case_candidates(review_id, events)
        heads = active_heads(events).get(review_id, [])
        decision = heads[0] if len(heads) == 1 else None
        context = dict(candidates[0]["payload"]) if candidates else {}
        if entity == "school":
            context["members"] = []
            if self.db_path.exists():
                with get_connection(self.db_path, read_only=True) as conn:
                    context["members"] = school_member_context(conn, review_id)
        return {
            "review_id": review_id,
            "entity_type": entity,
            "candidates": candidates,
            "decision": decision.to_dict() if decision else None,
            "history": [
                event.to_dict()
                for event in sorted(
                    events, key=lambda event: (event.recorded_at, event.decision_id)
                )
                if event.review_id == review_id
            ],
            "conflicts": [event.to_dict() for event in heads] if len(heads) > 1 else [],
            "context": context,
        }

    def status(self) -> dict[str, Any]:
        counts: Counter[tuple[str, str, int | None]] = Counter()
        seen: set[tuple[str, int | None]] = set()
        for item in self.candidates():
            parliaments = item["parliaments"] or [None]
            for parliament in parliaments:
                key = (item["review_id"], parliament)
                if key not in seen:
                    counts[(item["entity_type"], item["status"], parliament)] += 1
                    seen.add(key)
        return {
            "counts": [
                {
                    "entity_type": entity,
                    "status": status,
                    "parliament": parliament,
                    "count": count,
                }
                for (entity, status, parliament), count in sorted(
                    counts.items(), key=lambda item: str(item[0])
                )
            ],
            "revision": log_revision(self.log_path),
        }

    def semantic_diff(
        self,
        before: list[ReviewEvent],
        after: list[ReviewEvent],
        *,
        matcher: SchoolMatcher | None = None,
    ) -> dict[str, Any]:
        old_heads = active_heads(before)
        new = resolve_events(after)
        changes = []
        for key in sorted(set(old_heads) | set(new)):
            alternatives = old_heads.get(key, [])
            left, right = (
                alternatives[0] if len(alternatives) == 1 else None,
                new.get(key),
            )
            left_value = (
                {
                    "action": left.effective_action,
                    "payload": left.payload,
                    "source_url": left.source_url,
                }
                if left
                else None
            )
            right_value = (
                {
                    "action": right.effective_action,
                    "payload": right.payload,
                    "source_url": right.source_url,
                }
                if right
                else None
            )
            if len(alternatives) > 1:
                left_value = {
                    "conflicts": [
                        {
                            "decision_id": event.decision_id,
                            "action": event.effective_action,
                            "payload": event.payload,
                            "source_url": event.source_url,
                        }
                        for event in alternatives
                    ]
                }
            if left_value != right_value:
                changes.append(
                    {"review_id": key, "before": left_value, "after": right_value}
                )
        result: dict[str, Any] = {"decisions": changes, "canonical": None}
        if self.db_path.exists():
            from apemap.review.integration import (
                compare_review_records,
                project_review_records,
            )

            with get_connection(self.db_path, read_only=True) as conn:
                matcher = matcher or self.matcher()
                conflicts = [key for key, heads in old_heads.items() if len(heads) > 1]
                old_records = project_review_records(
                    conn, [] if conflicts else before, matcher=matcher
                )
                new_records = project_review_records(conn, after, matcher=matcher)
                differences = compare_review_records(old_records, new_records)
            result["canonical"] = {
                "baseline": "source" if conflicts else "prior_effective_decisions",
                "before": {"conflicts": conflicts}
                if conflicts
                else {table: change["before"] for table, change in differences.items()},
                "after": differences,
                "counts": {
                    "before": None if conflicts else _coverage_counts(old_records),
                    "after": _coverage_counts(new_records),
                },
            }
        else:
            result["canonical_unavailable"] = (
                "Run review build to preview canonical effects"
            )
        return json_value(result)

    def prepare(
        self,
        review_id: str,
        action: str,
        payload: dict[str, Any],
        source_url: str = "",
        reviewer: str | None = None,
        notes: str = "",
        supersedes: list[str] | None = None,
        replacement_action: str | None = None,
    ) -> dict[str, Any]:
        raw_log = self.log_path.read_bytes() if self.log_path.exists() else b""
        revision = hashlib.sha256(raw_log).hexdigest()
        source_revision = self.source_revision()
        events = parse_events(raw_log, allow_conflicts=bool(supersedes))
        entity = entity_for_review_id(review_id)
        # Candidate identity is a convenience, never an acceptance of proposed facts.
        identity: dict[str, Any] = {}
        if not supersedes:
            options = self._case_candidates(review_id, events)
            if options:
                identity = options[0]["payload"]
        proposed = {**identity, **payload}
        if (
            entity == "member_education"
            and review_id.endswith(":missing")
            and proposed.get("recorded_school_name")
        ):
            review_id = education_review_id(
                proposed["aph_id"], proposed["recorded_school_name"]
            )
        parents = list(supersedes or [])
        if parents and action != "supersede":
            replacement_action, action = action, "supersede"
        now = datetime.now(timezone.utc)
        event = ReviewEvent(
            decision_id=str(uuid4()),
            review_id=review_id,
            entity_type=entity,
            action=action,
            payload=proposed,
            source_url=source_url,
            reviewer=reviewer or default_reviewer(),
            notes=notes,
            reviewed_at=now.date().isoformat(),
            recorded_at=now.isoformat(),
            supersedes=parents,
            replacement_action=replacement_action,
        )
        matcher = self.matcher()
        validation_result = self._check_references(events + [event], matcher)
        # The diff's after projection performs canonical integrity validation;
        # check() would perform the same projection a second time.
        changes = self.semantic_diff(events, events + [event], matcher=matcher)
        if (
            self.source_revision() != source_revision
            or log_revision(self.log_path) != revision
        ):
            raise StaleReviewError("Review inputs changed while previewing; retry")
        preview = {
            "event": event.to_dict(),
            "revision": revision,
            "source_revision": source_revision,
            "changes": changes,
            "validation": [
                "Event, available source references and effective state validated",
                "Canonical projection validated"
                if self.db_path.exists()
                else "Canonical preview unavailable: run review build",
                *validation_result["warnings"],
            ],
        }
        if entity == "school":
            preview["school_guard"] = {
                "version": 1,
                "log_size": len(raw_log),
                "state": _school_state(events, event),
                "effects": _effect_revision(changes),
            }
        return preview

    def save(self, preview: dict[str, Any]) -> ReviewEvent:
        event = ReviewEvent.from_dict(preview["event"])

        def check_sources() -> None:
            if preview.get("source_revision") != self.source_revision():
                raise StaleReviewError("Source/candidate data changed; preview again")

        def check_independent_append(raw: bytes, current: list[ReviewEvent]) -> None:
            guard = preview["school_guard"]
            size = guard.get("log_size")
            if (
                guard.get("version") != 1
                or type(size) is not int
                or not 0 <= size <= len(raw)
                or hashlib.sha256(raw[:size]).hexdigest() != preview["revision"]
            ):
                raise StaleReviewError("Decision history changed; preview again")
            if _school_state(current, event) != guard.get("state"):
                raise StaleReviewError(
                    "School or target definition changed; preview again"
                )
            # Recompute the exact signed event, never prepare a replacement event.
            try:
                changes = self.semantic_diff(current, current + [event])
            except ValueError as exc:
                raise StaleReviewError(
                    "Mapping validation changed; preview again"
                ) from exc
            if _effect_revision(changes) != guard.get("effects"):
                raise StaleReviewError("Mapping effects changed; preview again")

        append_events(
            self.log_path,
            [event],
            expected_revision=preview["revision"],
            validator=lambda events: self.check(events),
            source_check=check_sources,
            revision_mismatch_check=check_independent_append
            if event.entity_type == "school"
            and isinstance(preview.get("school_guard"), dict)
            else None,
        )
        return event

    def build(
        self, *, inputs_manifest: Path | None = None, output_dir: Path | None = None
    ) -> dict[str, Any]:
        from apemap.ingest.pipeline import run_aph_ingestion
        from apemap.inputs import verify_inputs_manifest
        from apemap.review.integration import (
            attach_source_manifest,
            review_snapshot_metadata,
        )

        manifest_path = inputs_manifest or DATA_DIR / "inputs-manifest.json"
        valid, errors = verify_inputs_manifest(manifest_path)
        if not valid:
            raise ValueError("Pinned input verification failed: " + "; ".join(errors))
        raw_path = RAW_APH_DIR / "individuals.json"
        if not raw_path.exists():
            raise FileNotFoundError("Restore pinned APH inputs before review build")
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes)
        output = output_dir or PROCESSED_DIR / "review"
        before = log_revision(self.log_path)
        events = self.events()
        self.check(events)
        with get_connection(self.db_path) as conn:
            run_aph_ingestion(
                conn=conn,
                raw_individuals=json.loads(raw_path.read_text(encoding="utf-8")),
                external_dir=self.external_dir,
                output_dir=output,
                decision_log_path=self.log_path,
                retrieved_at=datetime.fromisoformat(manifest["created_at"]),
            )
            if review_snapshot_metadata(conn).get("decision_log_sha256") != before:
                raise StaleReviewError(
                    "Ingestion consumed a newer decision log; rebuild before exporting reviews"
                )
            attach_source_manifest(conn, manifest_path, manifest_bytes=manifest_bytes)
            items = annotate_candidates(build_candidates(conn), events)
            load_into_duckdb(conn, events, items)
            export_candidates(conn, items, output)
        if log_revision(self.log_path) != before:
            raise StaleReviewError(
                "Decision log changed during build; rerun against the new revision"
            )
        metadata = {
            "decision_log_sha256": before,
            "source_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "source_revision": self.source_revision(),
            "offline": True,
            "cache_gaps": sum(bool(x["evidence"].get("cache_missing")) for x in items),
            "candidates": len(items),
        }
        (output / "build-metadata.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return metadata


def _coverage_counts(records: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    education = records["member_education"]
    used = {row["institution_id"] for row in education}
    resolved = {
        row["institution_id"]
        for row in records["institutions"]
        if row.get("acara_id") or str(row["institution_id"]).startswith("manual:")
    }
    return {
        "members": len(records["members"]),
        "members_with_education": len({row["member_id"] for row in education}),
        "members_with_resolved_education": len(
            {row["member_id"] for row in education if row["institution_id"] in resolved}
        ),
        "resolved_institutions": len(used & resolved),
        "unresolved_institutions": len(used - resolved),
        "education_assertions": len(education),
        "service_intervals": len(records["parliament_service"]),
    }
