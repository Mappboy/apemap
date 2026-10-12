"""Optional, offline, server-rendered interface to the shared review service."""

from __future__ import annotations

import base64
from dataclasses import asdict
from collections import Counter
from collections.abc import Callable, Mapping
import hashlib
import hmac
import json
import logging
import secrets
import time
from pathlib import Path
from threading import Lock
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

from flask import Flask, abort, g, jsonify, redirect, render_template, request, url_for
from waitress import serve as waitress_serve

from apemap.review.store import ReviewBusyError, StaleReviewError
from apemap.review.schools import (
    RELATIONSHIPS,
    evidence_links,
    group_school_rows,
    school_comparison,
    school_view,
)
from apemap.review.members import MemberFieldDraft, group_member_rows
from apemap.review.navigation import (
    choose_successor,
    normalize_queue_context,
    queue_position,
)
from apemap.review.workspace import compare_values, extract_queue, summarize_evidence
from apemap.education_context import SCHOOL_CONTEXT_FIELDS
from apemap.review.context_evidence import ROLE_TYPES
from apemap.review.research import ResearchJobs, ResearchProvider
from apemap.review.scoring import score_candidates
from apemap.review.diagnostics import (
    Operation,
    operation,
    report_error,
    terminal_logging,
    timed,
)

logger = logging.getLogger(__name__)

PAGE_SIZE = 50
PREVIEW_TTL = 15 * 60
ENTITY_LABELS = {
    "school": "School mappings",
    "member": "Member corrections",
    "member_education": "Education assertions",
    "service": "Service intervals",
    "manual_institution": "Manual institutions",
}
STATUSES = (
    "pending",
    "accepted",
    "rejected",
    "needs_research",
    "needs_individual_review",
    "resolved_individually",
    "conflict",
    "superseded",
)
OPEN_MEMBER_STATUSES = ("conflict", "pending", "needs_research")
UNMATCHED_SCHOOL_STATUSES = (
    "conflict",
    "needs_individual_review",
    "pending",
    "needs_research",
    "rejected",
    "superseded",
)
ACTIONS = ("accept", "map", "reject", "research", "supersede")
ACTION_LABELS = {
    "accept": "Accept",
    "map": "Map school",
    "reject": "Reject",
    "research": "Needs research",
    "supersede": "Supersede earlier decision",
}
RESOLUTION_REASONS = {
    "ambiguous_name": "Ambiguous name—resolve per member",
    "no_suitable_candidate": "No suitable candidate—resolve per member",
}
RESOLUTION_FIELDS = (
    "institution_ref",
    "relationship_type",
    "candidate_id",
    *SCHOOL_CONTEXT_FIELDS,
)


def _open_member(row: Mapping[str, Any]) -> bool:
    return row["entity_type"] == "member" and row["status"] in OPEN_MEMBER_STATUSES


def _unmatched_school(row: Mapping[str, Any]) -> bool:
    return row["entity_type"] == "school" and row["status"] not in (
        "accepted",
        "resolved_individually",
    )


def _queue_order(
    row: Mapping[str, Any], statuses: tuple[str, ...]
) -> tuple[int, str, str]:
    name = row["school"].name if "school" in row else row["display_name"]
    return statuses.index(row["status"]), str(name).casefold(), str(row["review_id"])


def _member_preview_rows(
    member: Mapping[str, Any], preview: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Present the signed projection, including retained and conflicting values."""
    canonical = preview.get("changes", {}).get("canonical") or {}
    difference = canonical.get("after", {}).get("members", {})
    projected = next(
        (
            row
            for row in difference.get("after", [])
            if row["aph_id"] == member["aph_id"]
        ),
        None,
    )
    rows = []
    fields = {field["field"]: field for field in member["fields"]}
    for event in preview["events"]:
        name = event["payload"]["field"]
        field = fields[name]
        action = event.get("replacement_action") or event["action"]
        value = (
            projected.get(name)
            if projected is not None
            else event["payload"].get("value")
            if action == "accept"
            else field["source_value"]
            if canonical.get("baseline") == "source"
            else field["effective_value"]
        )
        rows.append(
            {
                "field": name,
                "before": field["effective_value"],
                "conflict": bool(field["conflicts"]),
                "after": value,
                "action": action,
                "label": {
                    "accept": "Accept",
                    "reject": "Reject proposal",
                    "research": "Needs research",
                }[action],
            }
        )
    return rows


def _guided_action(action: str, payload: Mapping[str, Any]) -> str:
    """Reload a typed research outcome without changing the stored event action."""
    reason = payload.get("resolution_reason")
    return (
        str(reason) if action == "research" and reason in RESOLUTION_REASONS else action
    )


def _clear_resolution_fields(payload: dict[str, Any]) -> None:
    payload.pop("context_evidence_refs", None)
    for key in RESOLUTION_FIELDS:
        payload.pop(key, None)


# (name, label, input type, options). Unknown or complex fields stay available
# through the complete JSON editor, rather than acquiring a second GUI schema.
FIELDS: dict[str, tuple[tuple[str, str, str, tuple[str, ...]], ...]] = {
    "school": (
        ("recorded_name", "Recorded school name", "text", ()),
        ("institution_ref", "Institution reference", "text", ()),
        (
            "relationship_type",
            "Relationship",
            "select",
            ("direct", "alias", "rename", "successor"),
        ),
        (
            "attended_institution_ref",
            "Original institution reference (optional)",
            "text",
            (),
        ),
        ("attended_identity_source_url", "Original identity evidence URL", "url", ()),
        ("historical_latitude", "Original latitude", "number", ()),
        ("historical_longitude", "Original longitude", "number", ()),
        ("historical_location_source_url", "Original location evidence URL", "url", ()),
        (
            "campus_continuity",
            "Campus continuity",
            "select",
            ("same_campus", "different_campus"),
        ),
        ("campus_continuity_source_url", "Campus continuity evidence URL", "url", ()),
        (
            "historical_broad_sector",
            "Historical broad sector",
            "select",
            ("Government", "Non-government"),
        ),
        (
            "historical_broad_sector_source_url",
            "Historical broad sector evidence URL",
            "url",
            (),
        ),
        (
            "historical_detailed_sector",
            "Historical detailed sector",
            "select",
            ("Catholic", "Independent"),
        ),
        (
            "historical_detailed_sector_source_url",
            "Historical detailed sector evidence URL",
            "url",
            (),
        ),
        (
            "historical_scope_confirmed",
            "Evidence covers all attendance records and verified aliases",
            "checkbox",
            (),
        ),
    ),
    "member": (
        ("aph_id", "APH identifier", "text", ()),
        ("field", "Member field", "select", ("date_of_birth", "gender", "wikidata_id")),
        ("value", "Corrected value", "text", ()),
    ),
    "member_education": (
        ("aph_id", "APH identifier", "text", ()),
        ("recorded_school_name", "School name as recorded", "text", ()),
        (
            "institution_ref",
            "Institution reference (required to map this assertion)",
            "text",
            (),
        ),
        (
            "relationship_type",
            "Relationship (required to map; optional when accepting attendance)",
            "select",
            ("direct", "alias", "rename", "successor"),
        ),
        (
            "attended_status",
            "Attendance evidence",
            "select",
            ("attended_unspecified", "graduated", "attended_did_not_graduate"),
        ),
        (
            "confidence",
            "Confidence",
            "select",
            ("verified", "provisional", "unconfirmed"),
        ),
        (
            "retrieved_at",
            "Evidence retrieved at (ISO timestamp with offset)",
            "text",
            (),
        ),
    ),
    "manual_institution": (
        ("institution_ref", "Institution reference (manual:school-slug)", "text", ()),
        ("school_name", "Institution name", "text", ()),
        (
            "sector",
            "Sector",
            "select",
            ("Government", "Catholic", "Independent", "Other"),
        ),
        ("country", "Country", "text", ()),
        (
            "institution_status",
            "Institution status (optional; defaults to manual)",
            "select",
            ("manual", "unknown", "current", "historical_only", "closed", "merged"),
        ),
        ("school_type", "School type (optional)", "text", ()),
        ("state", "State (optional)", "text", ()),
        ("suburb", "Suburb (optional)", "text", ()),
        ("postcode", "Postcode (optional)", "text", ()),
        ("latitude", "Latitude (optional)", "number", ()),
        ("longitude", "Longitude (optional)", "number", ()),
        (
            "location_source_url",
            "Location evidence URL (required for coordinates)",
            "url",
            (),
        ),
    ),
}


class ReviewServiceLike(Protocol):
    """The same review operations used by the command-line interface."""

    def candidates(
        self,
        entity_type: str | None = None,
        status: str | None = None,
        parliament: int | None = None,
        search: str | None = None,
    ) -> list[dict[str, Any]]: ...

    def show(self, review_id: str) -> dict[str, Any]: ...

    def review_revision(self) -> str: ...

    def school_decisions(self) -> dict[str, list[dict[str, Any]]]: ...

    def resolve_institution(self, reference: str) -> dict[str, Any] | None: ...

    def institution_resolver(self) -> Callable[[str], dict[str, Any] | None]: ...

    def lookup_institutions(
        self, query: str, *, limit: int = 20
    ) -> list[dict[str, Any]]: ...

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
    ) -> dict[str, Any]: ...

    def save(self, preview: dict[str, Any]) -> Any: ...

    def member(self, aph_id: str) -> dict[str, Any]: ...

    def prepare_batch(self, aph_id: str, drafts: list[Any]) -> dict[str, Any]: ...

    def save_batch(self, preview: dict[str, Any]) -> list[Any]: ...


def json_text(value: Any) -> str:
    """Show full evidence and semantic diffs as escaped readable JSON."""
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


def source_link(value: Any) -> str | None:
    """Only turn ordinary HTTP(S) evidence URLs into clickable links."""
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    return value


def _evidence_links(value: Any) -> list[str]:
    """Collect source links without ever downloading or interpreting evidence."""
    found: set[str] = set()

    def collect(part: Any) -> None:
        link = source_link(part)
        if link:
            found.add(link)
        elif isinstance(part, Mapping):
            for child in part.values():
                collect(child)
        elif isinstance(part, (list, tuple)):
            for child in part:
                collect(child)

    collect(value)
    return sorted(found)


def _sign_preview(preview: dict[str, Any], secret: bytes) -> str:
    # Canonical display diffs may contain large source snapshots. Only the exact
    # proposed event, revisions and optional school guard are needed to save.
    commit = {key: preview[key] for key in ("event", "revision", "source_revision")}
    if "school_guard" in preview:
        commit["school_guard"] = preview["school_guard"]
    event = preview["event"]
    if (
        event.get("entity_type") == "school"
        and (event.get("replacement_action") or event.get("action"))
        in {"accept", "map"}
        and preview.get("default_application") is not None
    ):
        commit["default_application_available"] = bool(
            preview["default_application"].get("available")
        )
    return _sign_commit(commit, secret)


def _sign_evidence_preview(preview: dict[str, Any], secret: bytes) -> str:
    """Bind only a validated evidence record and its immutable input revisions."""
    return _sign_commit(
        {
            key: preview[key]
            for key in ("kind", "review_id", "record", "revision", "source_revision")
        }
        | (
            {"candidate_revision": preview["candidate_revision"]}
            if "candidate_revision" in preview
            else {}
        )
        | (
            {"research_revisions": preview["research_revisions"]}
            if "research_revisions" in preview
            else {}
        ),
        secret,
    )


def _sign_member_batch_preview(preview: dict[str, Any], secret: bytes) -> str:
    commit = {
        "kind": "member_batch",
        "aph_id": preview["aph_id"],
        "drafts": preview["drafts"],
        "events": preview["events"],
        "event_ids": preview["event_ids"],
        "revision": preview["revision"],
        "source_revision": preview["source_revision"],
        "effects": preview["effects"],
        "changes": preview["changes"],
        "validation_outcome": preview["validation_outcome"],
        "unresolved_fields": preview.get("unresolved_fields", []),
    }
    return _sign_commit(commit, secret)


def _sign_commit(commit: dict[str, Any], secret: bytes) -> str:
    data = json.dumps(
        {"issued_at": int(time.time()), "preview": commit},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(data).decode("ascii")
    signature = hmac.new(secret, encoded.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def _read_preview(token: str, secret: bytes) -> dict[str, Any]:
    """Verify the exact preview, including revisions, without a draft store."""
    try:
        encoded, signature = token.rsplit(".", 1)
        expected = hmac.new(secret, encoded.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature.encode("utf-8"), expected.encode("ascii")):
            raise ValueError("Invalid preview signature")
        signed = json.loads(base64.b64decode(encoded, altchars=b"-_", validate=True))
        age = time.time() - signed["issued_at"]
        if age < 0 or age > PREVIEW_TTL:
            raise ValueError("Preview expired; preview the decision again")
        preview = signed["preview"]
        if not isinstance(preview, dict):
            raise ValueError("Invalid preview")
        return preview
    except (KeyError, TypeError, UnicodeError, json.JSONDecodeError) as err:
        raise ValueError("Invalid preview; preview the decision again") from err


def _initial_payload(item: dict[str, Any]) -> dict[str, Any]:
    decision = item.get("decision") or {}
    if isinstance(decision.get("payload"), dict):
        return dict(decision["payload"])
    for candidate in item.get("candidates", []):
        if isinstance(candidate.get("payload"), dict):
            payload = dict(candidate["payload"])
            evidence = candidate.get("evidence", {})
            if item["entity_type"] == "service" and isinstance(
                evidence.get("intervals"), list
            ):
                payload.setdefault("intervals", evidence["intervals"])
            elif item["entity_type"] == "member_education":
                for field in ("attended_status", "confidence", "retrieved_at"):
                    if evidence.get(field) is not None:
                        payload.setdefault(field, evidence[field])
            return payload
    return {}


def _payload_from_form(entity_type: str) -> dict[str, Any]:
    try:
        payload = json.loads(request.form.get("payload", "{}"))
    except json.JSONDecodeError as err:
        raise ValueError(f"Payload is not valid JSON: {err.msg}") from err
    if not isinstance(payload, dict):
        raise ValueError("Payload must be a JSON object")
    if request.form.get("payload_mode") == "guided":
        for name, _label, kind, _choices in FIELDS.get(entity_type, ()):
            value = request.form.get(f"field_{name}", "").strip()
            if not value:
                payload.pop(name, None)
            elif kind == "checkbox":
                payload[name] = value == "1"
            elif kind == "number":
                try:
                    payload[name] = float(value)
                except ValueError as err:
                    raise ValueError(f"{name} must be a number") from err
            else:
                payload[name] = value
        if entity_type == "member" and request.form.get("clear_value") == "1":
            payload["value"] = None
        if entity_type in {"school", "member_education"} and request.form.get(
            "evidence_selection"
        ):
            selected = sorted(set(request.form.getlist("evidence_refs")))
            if selected:
                payload["evidence_refs"] = selected
            else:
                payload.pop("evidence_refs", None)
    return payload


def _evidence_payload_from_form() -> dict[str, Any]:
    """Keep manual research data separate from a proposed review disposition."""
    payload: dict[str, Any] = {
        name: request.form.get("evidence_" + name, "").strip()
        for name in (
            "candidate_institution_ref",
            "source_title",
            "source_type",
            "source_url",
            "retrieved_at",
            "claim_type",
            "claim_value",
            "stance",
            "excerpt_or_note",
        )
    }
    payload["generated_by"] = "manual"
    if not payload["candidate_institution_ref"]:
        payload["candidate_institution_ref"] = None
    if request.form.get("evidence_claim_value_json", "").strip():
        try:
            payload["claim_value"] = json.loads(
                request.form["evidence_claim_value_json"]
            )
        except json.JSONDecodeError as err:
            raise ValueError(f"Structured claim is not valid JSON: {err.msg}") from err
    for name in (
        "source_quality",
        "match_strength",
        "temporal_relevance",
        "geographic_relevance",
    ):
        value = request.form.get("evidence_" + name, "").strip()
        if value:
            try:
                payload[name] = float(value)
            except ValueError as err:
                raise ValueError(f"{name.replace('_', ' ')} must be a number") from err
    return payload


def _comparison_members(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Group assertion sources without hiding differing relationship resolutions."""
    members = []
    for member in item.get("context", {}).get("members", []):
        grouped: dict[str, dict[str, Any]] = {}
        for education in member.get("education", []):
            key = education.get("review_id") or education.get("recorded_name", "")
            if key not in grouped:
                grouped[key] = {
                    **education,
                    "source_records": [],
                    "current_resolutions": [],
                }
            grouped[key]["source_records"].append(education)
            resolution = education.get("current_resolution") or {}
            if resolution not in grouped[key]["current_resolutions"]:
                grouped[key]["current_resolutions"].append(resolution)
        members.append({**member, "education": list(grouped.values())})
    return members


class ReviewApp(Flask):
    def log_exception(self, exc_info: tuple[Any, Any, Any]) -> None:
        """Report internal failures without arbitrary exception or request values."""
        activity = getattr(g, "review_operation", None)
        if activity is not None:
            activity.finish(status=500, error=exc_info[1])
        elif exc_info[1] is not None:
            report_error("Review request", exc_info[1])


def create_app(
    service: ReviewServiceLike,
    *,
    research_providers: dict[str, ResearchProvider] | None = None,
) -> Flask:
    """Build an isolated Flask app with no separate decision persistence."""
    app = ReviewApp(__name__)
    app.config.update(
        TRUSTED_HOSTS=["127.0.0.1", "localhost"],
        MAX_CONTENT_LENGTH=256 * 1024,
    )
    csrf = secrets.token_urlsafe(32)
    signing_secret = secrets.token_bytes(32)
    queue_lock = Lock()
    queue_cache: tuple[Any, list[dict[str, Any]]] | None = None
    research = ResearchJobs(research_providers or {})
    app.extensions["research_jobs"] = research
    asset_versions = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        for path in Path(app.static_folder or "").glob("*")
        if path.is_file()
    }

    @app.url_defaults
    def version_assets(endpoint: str, values: dict[str, Any]) -> None:
        if endpoint == "static" and values.get("filename") in asset_versions:
            values.setdefault("v", asset_versions[values["filename"]])

    app.jinja_env.filters["json_text"] = json_text
    app.jinja_env.filters["source_link"] = source_link

    @app.url_defaults
    def preserve_queue_context(endpoint: str, values: dict[str, Any]) -> None:
        context = getattr(g, "queue_navigation_context", {})
        if not context:
            return
        for key, value in context.items():
            values.setdefault("queue_" + key, value)

    def resolve_reference(reference: str) -> dict[str, Any] | None:
        if "institution_resolver" not in g:
            g.institution_resolver = service.institution_resolver()
        return g.institution_resolver(reference)

    def sign_workspace_draft(review_id: str, form: Any) -> str:
        allowed = (
            "action",
            "advanced_action",
            "payload",
            "payload_mode",
            "school_workflow",
            "source_url",
            "notes",
            "reviewer",
            "supersedes",
            "replacement_action",
            "clear_value",
            "field_",
            "action_",
            "value_",
            "notes_",
            "source_url_",
            "candidate_id_",
            "retained_decision_id_",
            "evidence_refs_",
            "evidence_refs",
            "evidence_selection",
            "context_role:",
        )
        values: dict[str, Any] = {}
        if callable(getattr(form, "getlist", None)):
            values = {
                key: form.getlist(key)
                for key in form
                if key != "csrf_token"
                and any(key == prefix or key.startswith(prefix) for prefix in allowed)
            }
        elif isinstance(form, Mapping):
            values = {
                key: value
                for key, value in form.items()
                if key != "csrf_token"
                and any(key == prefix or key.startswith(prefix) for prefix in allowed)
            }
        return _sign_commit(
            {
                "kind": "workspace_draft",
                "review_id": review_id,
                "values": values,
                "context": dict(getattr(g, "queue_navigation_context", {})),
            },
            signing_secret,
        )

    def normalized_form_values(form: Any) -> dict[str, Any]:
        """Keep repeated evidence-reference controls as lists and other fields singular."""
        values: dict[str, Any] = {}
        for key in form:
            entries = form.getlist(key)
            values[key] = (
                entries
                if key == "evidence_refs"
                or key.startswith("evidence_refs_")
                or len(entries) != 1
                else entries[0]
            )
        return values

    def read_workspace_draft(review_id: str, token: str) -> dict[str, Any]:
        if not token:
            return {}
        value = _read_preview(token, signing_secret)
        if (
            value.get("kind") != "workspace_draft"
            or value.get("review_id") != review_id
            or not isinstance(value.get("values"), dict)
        ):
            raise ValueError("Workspace draft does not match this review item")
        g.queue_navigation_context = normalize_queue_context(value.get("context", {}))
        return {
            key: vals
            if key == "evidence_refs"
            or key.startswith("evidence_refs_")
            or len(vals) != 1
            else vals[0]
            for key, vals in value["values"].items()
        }

    def load_item(review_id: str) -> dict[str, Any]:
        if not review_id.startswith("school:"):
            return service.show(review_id)
        for _ in range(2):
            revision = service.review_revision()
            item = service.show(review_id)
            if revision == service.review_revision():
                return {**item, "form_revision": revision}
        raise StaleReviewError("Decisions keep changing while loading; retry this item")

    def form_state(item: dict[str, Any]) -> dict[str, Any]:
        heads = item.get("conflicts") or (
            [item["decision"]] if item.get("decision") else []
        )
        return {
            "review_id": item["review_id"],
            "heads": sorted(event["decision_id"] for event in heads),
            # The global revision is still used to lock the validated preview.
            # An open form only goes stale when its own visible context changes.
            "page_revision": hashlib.sha256(
                json.dumps(
                    {
                        "heads": heads,
                        "candidates": item.get("candidates", []),
                        "context": item.get("context", {}),
                        "ranked_candidates": item.get("ranked_candidates", []),
                        "retained_evidence": item.get("retained_evidence", []),
                        "school": asdict(school_view(item, resolve_reference)),
                    },
                    sort_keys=True,
                    default=str,
                ).encode()
            ).hexdigest(),
        }

    @app.context_processor
    def template_context() -> dict[str, Any]:
        return {
            "csrf_token": csrf,
            "entity_labels": ENTITY_LABELS,
            "statuses": STATUSES,
            "actions": ACTIONS,
            "action_labels": ACTION_LABELS,
            "resolution_reasons": RESOLUTION_REASONS,
            "relationships": RELATIONSHIPS,
            "resolution_fields": RESOLUTION_FIELDS,
            "evidence_enabled": callable(getattr(service, "prepare_evidence", None))
            and callable(getattr(service, "save_evidence", None)),
        }

    @app.before_request
    def request_started() -> None:
        # Endpoints are code-defined; paths, query strings and bodies may contain
        # signed previews, credentials or research content and must not be logged.
        level = (
            logging.DEBUG
            if request.endpoint in {"static", "research_status"}
            else logging.INFO
        )
        g.review_operation = Operation(
            f"Request {request.method} {request.endpoint or 'unmatched route'}",
            level=level,
        )

    @app.after_request
    def request_finished(response: Any) -> Any:
        activity = getattr(g, "review_operation", None)
        if activity is not None:
            activity.finish(status=response.status_code)
        return response

    @app.teardown_request
    def request_failed(error: BaseException | None) -> None:
        activity = getattr(g, "review_operation", None)
        if activity is not None:
            activity.finish(error=error)

    @app.before_request
    def protect_writes() -> None:
        raw_context = {
            key.removeprefix("queue_"): value
            for key, value in request.args.items()
            if key.startswith("queue_")
        }
        try:
            g.queue_navigation_context = normalize_queue_context(raw_context)
        except ValueError as err:
            abort(400, str(err))
        if request.method != "POST":
            return
        token = request.form.get("csrf_token", "")
        if not hmac.compare_digest(token.encode("utf-8"), csrf.encode("ascii")):
            abort(403, "Invalid request token; reload the page")
        origin = request.headers.get("Origin")
        if origin:
            try:
                parsed = urlsplit(origin)
            except ValueError:
                abort(403, "Invalid request origin")
            if parsed.scheme != request.scheme or parsed.netloc != request.host:
                abort(403, "The request must come from this local review interface")

    @app.after_request
    def response_headers(response: Any) -> Any:
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'self'; script-src 'self'; "
            "connect-src 'self'; frame-src 'self'; form-action 'self'; "
            "base-uri 'none'; frame-ancestors 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        if request.endpoint == "research_search_suggestions":
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; style-src 'unsafe-inline'; img-src https://www.gstatic.com data:; frame-ancestors 'self'; base-uri 'none'; form-action 'none'"
            )
            response.headers["X-Frame-Options"] = "SAMEORIGIN"
        if request.endpoint == "static":
            response.headers["Cache-Control"] = "private, max-age=3600"
        else:
            response.headers["Cache-Control"] = "no-store"
        return response

    def item_page(
        item: dict[str, Any],
        *,
        payload: dict[str, Any] | None = None,
        preview: dict[str, Any] | None = None,
        error: str | None = None,
        new_entity_type: str | None = None,
        draft_values: Mapping[str, Any] | None = None,
        retry_preview: dict[str, Any] | None = None,
        retry_token: str | None = None,
        evidence_preview: dict[str, Any] | None = None,
        evidence_error: str | None = None,
        evidence_values: Mapping[str, Any] | None = None,
        research_case: Mapping[str, Any] | None = None,
        research_result_data: Mapping[str, Any] | None = None,
        workspace_draft_token: str | None = None,
        evidence_saved: bool = False,
    ) -> str:
        chosen = _initial_payload(item) if payload is None else payload
        values: Mapping[str, Any] = (
            request.form if draft_values is None else draft_values
        )
        evidence_context = {
            "comparison_members": _comparison_members(item),
            "evidence_preview": evidence_preview,
            "evidence_preview_token": _sign_evidence_preview(
                evidence_preview, signing_secret
            )
            if evidence_preview
            else None,
            "evidence_error": evidence_error,
            "evidence_values": evidence_values or {},
            "selected_evidence_refs": chosen.get("evidence_refs", []),
            "context_roles": ROLE_TYPES,
            "selected_context_refs": chosen.get("context_evidence_refs", {}),
            "research_providers": {
                key: {
                    "model": getattr(provider, "model", "external"),
                    "key_env": getattr(
                        provider, "api_key_env", "externally configured"
                    ),
                }
                for key, provider in research.providers.items()
            },
            "research_case": research_case
            or {
                "review_id": item.get("review_id", ""),
                "retained_evidence": item.get("retained_evidence", []),
                "job_id": request.args.get("job_id", ""),
            },
            "research_result": research_result_data,
            "workspace_draft_token": workspace_draft_token or "",
        }
        if item["entity_type"] == "school":
            school = school_view(item, resolve_reference)
            if not values:
                decision = item.get("decision") or {}
                action = decision.get("replacement_action") or decision.get("action")
                values = {
                    "action": _guided_action("research", chosen)
                    if action in {"research", "reject"}
                    else "map",
                    "advanced_action": action or "map",
                    "payload_mode": "guided",
                    "source_url": decision.get("source_url", ""),
                    "notes": decision.get("notes", ""),
                }
                if not school.saved_reference:
                    chosen.pop("institution_ref", None)
                    chosen.pop("relationship_type", None)
            elif (
                values.get("school_workflow") and values.get("payload_mode") == "guided"
            ):
                chosen = dict(chosen)
                for name in (
                    "recorded_name",
                    "institution_ref",
                    "relationship_type",
                    *SCHOOL_CONTEXT_FIELDS,
                ):
                    chosen[name] = values.get("field_" + name, chosen.get(name, ""))
            unresolved = values.get("payload_mode", "guided") == "guided" and (
                values.get("action") in {"research", "reject", *RESOLUTION_REASONS}
            )
            if unresolved:
                chosen = dict(chosen)
                _clear_resolution_fields(chosen)
            ref = str(chosen.get("institution_ref", ""))
            selected = resolve_reference(ref) if ref else None
            navigation = workspace_navigation(item["review_id"], "school")
            evidence_records = [
                candidate.get("evidence", {})
                for candidate in item.get("candidates", [])
                if candidate.get("evidence", {}).get("stance")
            ] + list(item.get("retained_evidence", []))
            evidence_view = summarize_evidence(
                evidence_records, ("identity", "temporal", "geographic")
            )
            token = _sign_preview(
                {
                    "event": form_state(item),
                    "revision": item.get("form_revision", service.review_revision()),
                    "source_revision": "school-form",
                },
                signing_secret,
            )
            return render_template(
                "school.html",
                item=item,
                school=school,
                payload=chosen,
                form_values=values,
                form_token=token,
                selected=selected,
                workspace_rows=school_comparison(asdict(school), selected),
                workspace_evidence=evidence_records,
                workspace_unknown_gaps=evidence_view["unknown_gaps"],
                workspace_position=navigation["position"],
                workspace_actions=navigation["actions"],
                navigation_token=navigation["token"]
                if navigation["in_queue"]
                else None,
                preview=preview,
                preview_token=_sign_preview(preview, signing_secret)
                if preview
                else None,
                error=error,
                retry_preview=retry_preview,
                retry_token=retry_token,
                relationships=RELATIONSHIPS,
                context_fields=FIELDS["school"][3:],
                resolution_fields_disabled=unresolved,
                original_selected=resolve_reference(
                    str(chosen.get("attended_institution_ref", ""))
                )
                if chosen.get("attended_institution_ref")
                else None,
                evidence_links=_evidence_links(item),
                preview_target=resolve_reference(
                    str(preview["event"]["payload"].get("institution_ref", ""))
                )
                if preview
                else None,
                **evidence_context,
            )
        if item.get("conflicts") and not values:
            values = {
                "action": "supersede",
                "supersedes": ", ".join(
                    event["decision_id"] for event in item["conflicts"]
                ),
                "replacement_action": "accept",
            }
        elif item["entity_type"] == "member_education" and not values:
            decision = item.get("decision") or {}
            has_attendance = any(
                row.get("payload", {}).get("recorded_school_name")
                and not row.get("evidence", {}).get("decision_only")
                for row in item.get("candidates", [])
            )
            action = decision.get("replacement_action") or decision.get("action")
            if (
                has_attendance
                and not new_entity_type
                and action in {None, "accept", "map"}
            ):
                action = "map"
            values = {
                "action": _guided_action(action or "accept", chosen),
                "source_url": decision.get("source_url", ""),
                "notes": decision.get("notes", ""),
            }
        named_education = item["entity_type"] == "member_education" and bool(
            chosen.get("recorded_school_name")
        )
        guided_outcome = (
            values.get("replacement_action", "accept")
            if values.get("action") == "supersede"
            else values.get("action", "accept")
        )
        unresolved = (
            named_education
            and values.get("payload_mode", "guided") == "guided"
            and guided_outcome in {"research", "reject", *RESOLUTION_REASONS}
        )
        if unresolved:
            chosen = dict(chosen)
            _clear_resolution_fields(chosen)
        navigation = (
            workspace_navigation(item.get("review_id", ""), item.get("entity_type", ""))
            if item.get("review_id")
            else {"position": {"in_queue": False}, "actions": [], "token": None}
        )
        evidence_records = [
            candidate.get("evidence", {})
            for candidate in item.get("candidates", [])
            if candidate.get("evidence", {}).get("stance")
        ]
        evidence_view = summarize_evidence(evidence_records)
        decision_payload = (item.get("decision") or {}).get("payload", {})
        effective_assertion_metadata: dict[str, Any] | None = None
        effective_assertion_ref = ""
        if item.get("entity_type") == "member_education":
            source_payload = next(
                (
                    candidate.get("payload", {})
                    for candidate in item.get("candidates", [])
                    if candidate.get("payload", {}).get("recorded_school_name")
                ),
                {},
            )
            school_name = source_payload.get("recorded_school_name") or item.get(
                "context", {}
            ).get("recorded_school_name", "")
            exact_sources = [
                education
                for member in item.get("context", {}).get("members", [])
                for education in member.get("education", [])
                if education.get("review_id") == item.get("review_id")
            ]
            exact_resolutions = {
                resolution.get("institution_ref", "")
                for education in exact_sources
                if (resolution := education.get("current_resolution") or {}).get(
                    "institution_ref"
                )
            }
            decision_action = (item.get("decision") or {}).get(
                "replacement_action"
            ) or (item.get("decision") or {}).get("action")
            if decision_action in {"research", "reject", *RESOLUTION_REASONS}:
                current_ref = ""
            elif len(exact_resolutions) == 1:
                current_ref = next(iter(exact_resolutions))
            elif len(exact_resolutions) > 1:
                current_ref = ""
            else:
                current_ref = decision_payload.get("institution_ref", "")
            proposed_ref = chosen.get("institution_ref", "")
            current_metadata = resolve_reference(current_ref) if current_ref else None
            proposed_metadata = (
                resolve_reference(proposed_ref) if proposed_ref else None
            )
            effective_assertion_metadata = current_metadata
            effective_assertion_ref = current_ref
            evidence_view_records = [
                candidate.get("evidence", {})
                for candidate in item.get("candidates", [])
            ] + list(item.get("retained_evidence", []))
            source_records = [
                candidate.get("payload", {})
                for candidate in item.get("candidates", [])
                if candidate.get("payload")
            ] + [
                source
                for education in exact_sources
                for source in education.get("source_records", [education])
            ]
            synthetic_school = {
                "name": school_name,
                "source_records": source_records,
                "saved_reference": current_ref,
                "targets": [
                    {
                        "reference": current_ref,
                        "metadata": current_metadata or {},
                    }
                ]
                if current_ref
                else [],
            }
            comparison_rows = school_comparison(synthetic_school, proposed_metadata)
            summary_rows = [
                compare_values(
                    row["field"],
                    row.get("source"),
                    row.get("reviewed"),
                    row.get("proposed"),
                    provenance=evidence_view_records,
                )
                for row in comparison_rows
            ]
            comparison_rows = summary_rows
            evidence_view = summarize_evidence(
                evidence_view_records,
                ("identity", "temporal", "geographic"),
            )
            evidence_records = [
                record for record in evidence_view_records if record.get("stance")
            ]
        else:
            source_payload = next(
                (
                    candidate.get("payload", {})
                    for candidate in item.get("candidates", [])
                    if candidate.get("payload")
                ),
                {},
            )
            comparison_rows = [
                {
                    "field": name,
                    "source": source_payload.get(name),
                    "reviewed": decision_payload.get(name),
                    "proposed": chosen.get(name),
                    "comparison": "unknown",
                    "summary": "Unknown",
                }
                for name in set(source_payload) | set(decision_payload) | set(chosen)
                if name not in {"legacy"}
            ]
        return render_template(
            "item.html",
            item=item,
            payload=chosen,
            fields=FIELDS.get(item["entity_type"], ()),
            preview=preview,
            preview_token=_sign_preview(preview, signing_secret) if preview else None,
            evidence_links=_evidence_links(item),
            error=error,
            retry_preview=retry_preview,
            retry_token=retry_token,
            new_entity_type=new_entity_type,
            form_values=values,
            named_education=named_education,
            resolution_fields_disabled=unresolved,
            workspace_rows=comparison_rows,
            workspace_evidence=evidence_records,
            workspace_unknown_gaps=evidence_view["unknown_gaps"],
            effective_assertion_metadata=effective_assertion_metadata,
            effective_assertion_ref=effective_assertion_ref,
            workspace_position=navigation["position"],
            workspace_actions=navigation["actions"],
            navigation_token=navigation["token"]
            if navigation["position"].get("in_queue")
            else None,
            evidence_saved=evidence_saved,
            **evidence_context,
        )

    @timed("Building review queue rows")
    def build_queue_rows() -> list[dict[str, Any]]:
        all_rows = group_school_rows(group_member_rows(service.candidates()))
        decisions = service.school_decisions()
        records_reader = getattr(service, "evidence_records", None)
        retained_records = records_reader() if callable(records_reader) else []
        metadata_cache: dict[str, dict[str, Any] | None] = {}

        def resolve(reference: str) -> dict[str, Any] | None:
            if reference not in metadata_cache:
                metadata_cache[reference] = resolve_reference(reference)
            return metadata_cache[reference]

        for row in all_rows:
            if row["entity_type"] == "school":
                heads = decisions.get(row["review_id"], [])
                row["school"] = school_view(
                    {
                        **row,
                        "decision": heads[0] if len(heads) == 1 else None,
                        "conflicts": heads if len(heads) > 1 else [],
                    },
                    resolve,
                )
                row["status"] = row["school"].status
                evidence = [
                    candidate.get("evidence", {})
                    for candidate in row.get("candidates", [])
                ] + row["school"].source_records
                has_source = any(evidence_links(part) for part in evidence)
                ambiguity_reason = any(
                    (head.get("payload") or {}).get("resolution_reason")
                    == "ambiguous_name"
                    for head in heads
                )
                row["ambiguous"] = (
                    row["status"] == "conflict"
                    or bool(row.get("conflicts"))
                    or ambiguity_reason
                )
                score_inputs = [
                    {
                        **target.metadata,
                        "institution_ref": target.reference,
                        "institution_name": target.name,
                        "recorded_name": row["school"].name,
                    }
                    for target in row["school"].targets
                ]
                scored = (
                    score_candidates(row["review_id"], score_inputs, retained_records)
                    if score_inputs
                    else []
                )
                bands = [
                    str(candidate.get("score_band", "insufficient"))
                    for candidate in scored
                ]
                row["evidence_bands"] = bands
                row["evidence_band"] = bands[0] if bands else "insufficient"
                row["low_evidence"] = (
                    row["evidence_band"] in {"low", "name_only", "insufficient"}
                    or not has_source
                )
                scored_ties = any(
                    candidate.get("tied") and candidate.get("score_band") == "strong"
                    for candidate in scored
                )
                retained_conflict = any(
                    getattr(
                        record,
                        "stance",
                        record.get("stance") if isinstance(record, Mapping) else None,
                    )
                    in {"contradicts", "conflicts"}
                    and getattr(
                        record,
                        "review_id",
                        record.get("review_id")
                        if isinstance(record, Mapping)
                        else None,
                    )
                    == row["review_id"]
                    and getattr(
                        record,
                        "claim_type",
                        record.get("claim_type")
                        if isinstance(record, Mapping)
                        else None,
                    )
                    == "identity"
                    for record in retained_records
                )
                row["ambiguous"] = (
                    row.get("ambiguous", False) or scored_ties or retained_conflict
                )
                row["unknown_evidence_dimensions"] = sorted(
                    {
                        dimension
                        for candidate in scored
                        for dimension, value in candidate.get("components", {}).items()
                        if dimension != "name_match" and value == 0
                    }
                )
        return all_rows

    def queue_rows() -> list[dict[str, Any]]:
        nonlocal queue_cache
        revision_reader = getattr(service, "queue_revision", None)
        if revision_reader is None:
            return build_queue_rows()
        # Hash outside the queue lock. Lookup, details, previews and saves never
        # take this lock or consume this presentation cache.
        with operation("Checking review queue revision", level=logging.DEBUG):
            revision = revision_reader()
        with operation("Waiting for review queue lock", level=logging.DEBUG):
            queue_lock.acquire()
        try:
            if queue_cache is not None and queue_cache[0] == revision:
                logger.debug("Review queue cache hit (%d rows)", len(queue_cache[1]))
                return queue_cache[1]
            logger.info("Review queue cache miss; rebuilding rows")
            rows = build_queue_rows()
            if revision_reader() == revision:
                queue_cache = (revision, rows)
            logger.info("Review queue built (%d rows)", len(rows))
            return rows
        finally:
            queue_lock.release()

    def workspace_navigation(current_id: str, entity: str) -> dict[str, Any]:
        context = dict(getattr(g, "queue_navigation_context", {}))
        view = context.get("view") or (
            "members"
            if entity == "member"
            else "schools"
            if entity == "school"
            else "all"
        )
        context["view"] = view
        valid_statuses = (
            OPEN_MEMBER_STATUSES
            if view == "members"
            else UNMATCHED_SCHOOL_STATUSES
            if view == "schools"
            else STATUSES
        )
        try:
            context = normalize_queue_context(context, statuses=valid_statuses)
        except ValueError as err:
            abort(400, str(err))
        queue = extract_queue(
            queue_rows(),
            view=view,
            entity=context.get("entity"),
            status=context.get("status") or None,
            parliament=int(context["parliament"])
            if context.get("parliament")
            else None,
            q=context.get("q", ""),
            unmatched_schools=context.get("unmatched_schools") == "1"
            or view == "schools",
            preset=context.get("preset") or None,
            page=int(context.get("page", "1")),
            per_page=PAGE_SIZE,
        )
        position = queue_position(queue["ids"], current_id)
        successors = (
            queue["ids"][queue["ids"].index(current_id) + 1 :]
            if position["in_queue"]
            else []
        )
        nav_token = _sign_commit(
            {
                "kind": "queue_navigation",
                "context": context,
                "current_id": current_id,
                "successors": successors,
            },
            signing_secret,
        )

        def action(
            key: str, label: str, target_id: str | None
        ) -> dict[str, str] | None:
            if not target_id:
                return None
            target = next(
                (row for row in queue["all_rows"] if row.get("review_id") == target_id),
                {},
            )
            query: dict[str, Any] = {
                "queue_" + name: value for name, value in context.items()
            }
            if target.get("entity_type") == "member":
                href = url_for(
                    "member_detail",
                    aph_id=target.get("aph_id", ""),
                    **query,
                )
            else:
                href = url_for(
                    "detail",
                    review_id=target_id,
                    **query,
                )
            return {"key": key, "label": label, "href": href}

        actions = [
            value
            for value in (
                action("previous", "Previous", position["previous_id"]),
                action("next", "Next", position["next_id"]),
            )
            if value
        ]
        if position["in_queue"]:
            skipped = action("skip", "Skip", position["next_id"])
            actions.append(
                skipped or {"key": "skip", "label": "Skip", "href": queue_href(context)}
            )
        actions.append(
            {
                "key": "queue",
                "label": "Back to filtered queue",
                "href": queue_href(context),
            }
        )
        return {
            "position": position,
            "actions": actions,
            "token": nav_token,
            "context": context,
            "in_queue": position["in_queue"],
        }

    def queue_href(context: Mapping[str, str]) -> str:
        endpoints = {
            "all": "queue",
            "members": "member_queue",
            "schools": "unmatched_school_queue",
        }
        view = context.get("view", "all")
        values: dict[str, Any] = {
            "status": context.get("status", ""),
            "parliament": context.get("parliament", ""),
            "q": context.get("q", ""),
            "page": context.get("page", "1"),
            "preset": context.get("preset", ""),
        }
        if view == "all":
            values["entity_type"] = context.get("entity", "")
            values["unmatched_schools"] = context.get("unmatched_schools", "")
        return url_for(endpoints.get(view, "queue"), **values)

    def submitted_navigation(token: str, current_id: str) -> dict[str, Any]:
        if not token:
            raise ValueError(
                "Queue navigation expired; return to the queue before advancing"
            )
        value = _read_preview(token, signing_secret)
        if (
            value.get("kind") != "queue_navigation"
            or value.get("current_id") != current_id
            or not isinstance(value.get("successors"), list)
            or any(not isinstance(item, str) for item in value.get("successors", []))
        ):
            raise ValueError("Queue navigation does not match this review item")
        value["context"] = normalize_queue_context(value.get("context", {}))
        return value

    def advance_redirect(
        nav: dict[str, Any],
        current_id: str,
        entity: str,
        *,
        saved_count: int = 1,
        remaining_count: int = 0,
        saved_member: str = "",
    ) -> Any:
        context = nav["context"]
        post_save = extract_queue(
            queue_rows(),
            view=context.get("view", "all"),
            entity=context.get("entity") or None,
            status=context.get("status") or None,
            parliament=int(context["parliament"])
            if context.get("parliament")
            else None,
            q=context.get("q", ""),
            unmatched_schools=context.get("unmatched_schools") == "1"
            or context.get("view") == "schools",
            preset=context.get("preset") or None,
            per_page=PAGE_SIZE,
        )
        target_id = choose_successor(
            [current_id, *nav["successors"]], post_save["ids"], current_id
        )
        query: dict[str, Any] = {
            "queue_" + key: value for key, value in context.items()
        }
        query["saved"] = "1"
        query["saved_count"] = str(saved_count)
        query["remaining_count"] = str(remaining_count)
        if saved_member:
            query["saved_member"] = saved_member
        if target_id:
            target = next(
                row
                for row in post_save["all_rows"]
                if row.get("review_id") == target_id
            )
            if target.get("entity_type") == "member":
                return redirect(
                    url_for("member_detail", aph_id=target.get("aph_id", ""), **query),
                    code=303,
                )
            return redirect(url_for("detail", review_id=target_id, **query), code=303)
        queue_query: dict[str, Any] = {
            "status": context.get("status", ""),
            "parliament": context.get("parliament", ""),
            "q": context.get("q", ""),
            "page": context.get("page", "1"),
            "preset": context.get("preset", ""),
            "saved": "1",
            "saved_count": str(saved_count),
            "remaining_count": str(remaining_count),
        }
        if context.get("view") == "all":
            queue_query["entity_type"] = context.get("entity", "")
            queue_query["unmatched_schools"] = context.get("unmatched_schools", "")
        if saved_member:
            queue_query["saved_member"] = saved_member
        return redirect(
            url_for(
                {
                    "all": "queue",
                    "members": "member_queue",
                    "schools": "unmatched_school_queue",
                }.get(context.get("view"), "queue"),
                **queue_query,
            ),
            code=303,
        )

    def render_queue(view: Literal["all", "members", "schools"] = "all") -> str:
        endpoint = {
            "all": "queue",
            "members": "member_queue",
            "schools": "unmatched_school_queue",
        }[view]
        entity_type = {"members": "member", "schools": "school"}.get(view)
        if view == "all":
            entity_type = request.args.get("entity_type") or None
        statuses = (
            OPEN_MEMBER_STATUSES
            if view == "members"
            else UNMATCHED_SCHOOL_STATUSES
            if view == "schools"
            else STATUSES
        )
        status = request.args.get("status") or None
        try:
            parliament = (
                int(request.args["parliament"])
                if request.args.get("parliament")
                else None
            )
            page = max(1, int(request.args.get("page", "1")))
        except ValueError:
            abort(400, "Parliament and page must be integers")
        if entity_type and entity_type not in ENTITY_LABELS:
            abort(400, "Unknown review type")
        if status and status not in statuses:
            abort(400, "Unknown status")
        search = request.args.get("q") or None
        preset = request.args.get("preset") or None
        if preset and preset not in {
            "ambiguous_schools",
            "member_identity_conflicts",
            "needs_research",
            "low_evidence",
            "unresolved",
            "member_corrections",
        }:
            abort(400, "Unknown review preset")
        unmatched_schools = view == "schools" or (
            view == "all" and request.args.get("unmatched_schools") == "1"
        )
        all_rows = queue_rows()
        try:
            queue_result = extract_queue(
                all_rows,
                view=view,
                entity=entity_type,
                status=status,
                parliament=parliament,
                q=search or "",
                unmatched_schools=unmatched_schools,
                preset=preset,
                page=page,
                per_page=PAGE_SIZE,
            )
        except ValueError as err:
            abort(400, str(err))
        rows = queue_result["all_rows"]
        totals = Counter(row.get("status", "pending") for row in rows)
        pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, pages)
        filters = {
            "entity_type": entity_type or "",
            "status": status or "",
            "parliament": parliament or "",
            "q": search or "",
            "unmatched_schools": unmatched_schools,
            "preset": preset or "",
        }
        # Carry the queue's active filters to detail links and the workspace
        # return action using the same normalized whitelist as signed navigation.
        g.queue_navigation_context = normalize_queue_context(
            {
                "view": view,
                "entity": entity_type or "",
                "status": status or "",
                "parliament": str(parliament or ""),
                "q": search or "",
                "unmatched_schools": "1" if unmatched_schools else "",
                "page": str(page),
                "preset": preset or "",
            },
            statuses=statuses,
        )

        def page_url(number: int) -> str:
            values: dict[str, Any] = {
                "status": status or "",
                "parliament": parliament or "",
                "q": search or "",
                "page": number,
                "preset": preset or "",
            }
            if view == "all":
                values.update(
                    entity_type=entity_type or "",
                    unmatched_schools="1" if unmatched_schools else "",
                )
            return url_for(endpoint, **values)

        return render_template(
            "queue.html",
            queue_view=view,
            queue_endpoint=endpoint,
            page_title={
                "all": "Review queues",
                "members": "Members to review",
                "schools": "Unmatched schools",
            }[view],
            page_description={
                "all": "",
                "members": "Members with pending corrections, research tasks or conflicts. Completed decisions remain available in the general queue and member history.",
                "schools": "Schools awaiting a reviewed resolution, including those with suggested institutions. Accepted mappings and fully resolved individual reviews are excluded.",
            }[view],
            empty_message={
                "all": "No candidates match these filters.",
                "members": "No members require review",
                "schools": "No unmatched schools match these filters.",
            }[view],
            statuses=statuses,
            workload_counts={
                "members": sum(_open_member(row) for row in all_rows),
                "schools": sum(_unmatched_school(row) for row in all_rows),
            }
            if view == "all"
            else {},
            rows=queue_result["rows"],
            totals=totals,
            total=len(rows),
            queue_label="members"
            if view == "members"
            else "unmatched schools"
            if view == "schools"
            else "school mappings"
            if entity_type == "school"
            else "review entries",
            page=queue_result["page"],
            pages=queue_result["pages"],
            filters=filters,
            previous_url=page_url(page - 1) if page > 1 else None,
            next_url=page_url(page + 1) if page < pages else None,
        )

    @app.get("/")
    def queue() -> str:
        return render_queue()

    @app.get("/members")
    def member_queue() -> str:
        return render_queue("members")

    @app.get("/schools/unmatched")
    def unmatched_school_queue() -> str:
        return render_queue("schools")

    @app.get("/open")
    def open_item() -> Any:
        review_id = request.args.get("review_id", "").strip()
        if not review_id:
            abort(400, "Enter a review identifier")
        return redirect(url_for("detail", review_id=review_id))

    @app.get("/institutions/lookup")
    def lookup_institutions() -> Any:
        query = request.args.get("q", "").strip()
        if len(query) > 200:
            abort(400, "School name must be 200 characters or fewer")
        try:
            results = service.lookup_institutions(query, limit=20) if query else []
        except ValueError as err:
            return jsonify({"error": str(err), "results": []}), 400
        return jsonify({"query": query, "results": results[:20]})

    @app.get("/readiness")
    def readiness_page() -> Any:
        try:
            report = getattr(service, "readiness")()
            return render_template("readiness.html", report=report)
        except (OSError, ValueError) as err:
            return render_template("error.html", message=str(err)), 400

    def member_page(
        member_view: dict[str, Any],
        *,
        preview: dict[str, Any] | None = None,
        error: str | None = None,
        form_values: Mapping[str, Any] | None = None,
        research_jobs: Mapping[str, str] | None = None,
        research_results: Mapping[str, dict[str, Any]] | None = None,
        workspace_draft_token: str | None = None,
        research_evidence: Mapping[str, Any] | None = None,
    ) -> str:
        values = request.form if form_values is None else form_values
        preview_token = (
            _sign_member_batch_preview(preview, signing_secret) if preview else None
        )
        current_id = f"member:{str(member_view.get('aph_id', '')).strip().lower()}"
        navigation = workspace_navigation(current_id, "member")
        actionable = [
            field
            for field in member_view.get("fields", [])
            if field.get("status") in {"conflict", "pending", "needs_research"}
        ]
        workspace_rows = []
        for field in actionable:
            field_evidence = [
                candidate.get("evidence", {})
                for candidate in field.get("candidates", [])
                if candidate.get("evidence", {}).get("stance")
            ] + list(field.get("retained_evidence", []))
            workspace_rows.append(
                compare_values(
                    str(field.get("field", "value")),
                    field.get("source_value"),
                    field.get("effective_value"),
                    field.get("proposed_value"),
                    provenance=field_evidence,
                )
            )
        evidence_records = [
            candidate.get("evidence", {})
            for field in member_view.get("fields", [])
            for candidate in field.get("candidates", [])
            if candidate.get("evidence", {}).get("stance")
        ] + [
            record
            for field in member_view.get("fields", [])
            for record in field.get("retained_evidence", [])
        ]
        evidence_view = summarize_evidence(
            evidence_records, [str(field.get("field")) for field in actionable]
        )
        return render_template(
            "member.html",
            member=member_view,
            preview=preview,
            preview_rows=_member_preview_rows(member_view, preview) if preview else [],
            preview_token=preview_token,
            error=error,
            form_values=values,
            workspace_rows=workspace_rows,
            workspace_evidence=evidence_records,
            workspace_unknown_gaps=evidence_view["unknown_gaps"],
            workspace_position=navigation["position"],
            workspace_actions=navigation["actions"],
            navigation_token=navigation["token"]
            if navigation["position"]["in_queue"]
            else None,
            research_jobs=research_jobs or {},
            research_results=research_results or {},
            workspace_draft_token=workspace_draft_token or "",
            research_evidence=research_evidence or {},
            related_unresolved_count=sum(
                row.get("status")
                in {
                    "pending",
                    "conflict",
                    "needs_research",
                    "needs_individual_resolution",
                }
                for row in member_view.get("education", [])
                + member_view.get("services", [])
            ),
            research_providers={
                key: {"model": getattr(provider, "model", "external")}
                for key, provider in research.providers.items()
            },
        )

    @app.get("/members/<aph_id>")
    def member_detail(aph_id: str) -> str:
        try:
            member_view = service.member(aph_id)
        except (KeyError, ValueError) as err:
            abort(404, str(err))
        return member_page(member_view)

    @app.post("/members/<aph_id>/preview")
    def preview_member_batch(aph_id: str) -> str:
        try:
            member_view = service.member(aph_id)
        except (KeyError, ValueError) as err:
            abort(404, str(err))
        drafts: list[MemberFieldDraft] = []
        for field in ("gender", "date_of_birth", "wikidata_id"):
            action = request.form.get(f"action_{field}", "unchanged").strip()
            if action != "unchanged":
                val = request.form.get(f"value_{field}", "").strip() or None
                source_url = request.form.get(f"source_url_{field}", "").strip()
                notes = request.form.get(f"notes_{field}", "").strip()
                reviewer = (
                    request.form.get(f"reviewer_{field}", "").strip()
                    or request.form.get("reviewer", "").strip()
                    or None
                )
                drafts.append(
                    MemberFieldDraft(
                        field=field,
                        action=action,
                        value=val,
                        source_url=source_url,
                        notes=notes,
                        reviewer=reviewer,
                        candidate_id=request.form.get(f"candidate_id_{field}") or None,
                        retained_decision_id=request.form.get(
                            f"retained_decision_id_{field}"
                        )
                        or None,
                        evidence_refs=request.form.getlist(f"evidence_refs_{field}"),
                    )
                )
        try:
            preview = service.prepare_batch(aph_id, drafts)
            return member_page(member_view, preview=preview, form_values=request.form)
        except (ValueError, OSError) as err:
            return member_page(member_view, error=str(err), form_values=request.form)

    @app.post("/members/<aph_id>/save")
    def save_member_batch(aph_id: str) -> Any:
        try:
            member_view = service.member(aph_id)
        except (KeyError, ValueError) as err:
            abort(404, str(err))
        navigation = None
        if request.form.get("advance") == "next":
            try:
                navigation = submitted_navigation(
                    request.form.get("navigation_token", ""),
                    f"member:{str(member_view.get('aph_id', aph_id)).strip().lower()}",
                )
            except ValueError as err:
                return member_page(
                    member_view, error=str(err), form_values=request.form
                ), 400
        token = request.form.get("preview_token", "").strip()
        if not token:
            return member_page(
                member_view,
                error="Preview the changes before saving",
                form_values=request.form,
            )
        try:
            preview = _read_preview(token, signing_secret)
        except ValueError as err:
            return member_page(member_view, error=str(err), form_values=request.form)

        if (
            preview.get("kind") != "member_batch"
            or preview.get("aph_id") != member_view["aph_id"]
        ):
            return member_page(
                member_view,
                error="Preview token does not match this member batch",
                form_values=request.form,
            )

        submitted_drafts: list[dict[str, Any]] = []
        for field in ("gender", "date_of_birth", "wikidata_id"):
            action = request.form.get(f"action_{field}", "unchanged").strip()
            if action != "unchanged":
                val = request.form.get(f"value_{field}", "").strip() or None
                source_url = request.form.get(f"source_url_{field}", "").strip()
                notes = request.form.get(f"notes_{field}", "").strip()
                reviewer = (
                    request.form.get(f"reviewer_{field}", "").strip()
                    or request.form.get("reviewer", "").strip()
                    or None
                )
                submitted_drafts.append(
                    MemberFieldDraft(
                        field=field,
                        action=action,
                        value=val,
                        source_url=source_url,
                        notes=notes,
                        reviewer=reviewer,
                        candidate_id=request.form.get(f"candidate_id_{field}") or None,
                        retained_decision_id=request.form.get(
                            f"retained_decision_id_{field}"
                        )
                        or None,
                        evidence_refs=request.form.getlist(f"evidence_refs_{field}"),
                    ).to_dict()
                )

        preview_drafts = [
            d for d in preview.get("drafts", []) if d.get("action") != "unchanged"
        ]

        def _draft_key(d: dict[str, Any]) -> tuple[Any, ...]:
            return (
                d.get("field"),
                d.get("action"),
                d.get("value"),
                d.get("source_url"),
                d.get("notes"),
                d.get("reviewer"),
                d.get("candidate_id"),
                d.get("retained_decision_id"),
                d.get("evidence_refs", []),
            )

        if [_draft_key(d) for d in submitted_drafts] != [
            _draft_key(d) for d in preview_drafts
        ]:
            return member_page(
                member_view,
                error="Draft inputs changed after preview was generated; preview again before saving.",
                form_values=request.form,
            )

        try:
            service.save_batch(preview)
            if navigation:
                return advance_redirect(
                    navigation,
                    f"member:{str(member_view.get('aph_id', aph_id)).strip().lower()}",
                    "member",
                    saved_count=len(submitted_drafts),
                    remaining_count=len(preview.get("unresolved_fields", [])),
                    saved_member=str(member_view.get("aph_id", aph_id)),
                )
            return redirect(url_for("member_detail", aph_id=aph_id, saved="1"))
        except (ValueError, OSError) as err:
            message = (
                str(err)
                if not isinstance(err, OSError)
                else "The save could not be confirmed because of a file error. Check history before retrying. Your draft is retained."
            )
            return member_page(
                member_view,
                preview=preview if isinstance(err, ReviewBusyError) else None,
                error=message,
                form_values=request.form,
            )

    @app.get("/items/<path:review_id>")
    def detail(review_id: str) -> Any:
        if review_id.startswith("member:"):
            parts = review_id.split(":")
            if len(parts) >= 3:
                return redirect(
                    url_for("member_detail", aph_id=parts[1]) + f"#field-{parts[2]}"
                )
            if len(parts) == 2:
                return redirect(url_for("member_detail", aph_id=parts[1]))
        try:
            item = load_item(review_id)
        except ValueError as err:
            abort(404, str(err))
        return item_page(item)

    @app.get("/items/<path:review_id>/comparison")
    def school_comparison_json(review_id: str) -> Any:
        try:
            item = load_item(review_id)
            if item.get("entity_type") not in {"school", "member_education"}:
                abort(404)
            candidate_ref = request.args.get("candidate_ref", "").strip()
            selected = resolve_reference(candidate_ref) if candidate_ref else None
            if candidate_ref and selected is None:
                return jsonify({"error": "Selected candidate is unavailable"}), 400
            if item.get("entity_type") == "school":
                school = school_view(item, resolve_reference)
                rows = school_comparison(asdict(school), selected)
                evidence = [
                    row.get("evidence", {})
                    for row in item.get("candidates", [])
                    if row.get("evidence", {}).get("stance")
                ] + list(item.get("retained_evidence", []))
            else:
                source = next(
                    (
                        education
                        for member in item.get("context", {}).get("members", [])
                        for education in member.get("education", [])
                        if education.get("review_id") == review_id
                    ),
                    {},
                )
                decision = item.get("decision") or {}
                action = decision.get("replacement_action") or decision.get("action")
                resolution = source.get("current_resolution") or {}
                current_ref = ""
                if action not in {"research", "reject", *RESOLUTION_REASONS}:
                    if resolution.get("institution_ref"):
                        current_ref = resolution["institution_ref"]
                    elif not resolution.get("conflicts"):
                        current_ref = decision.get("payload", {}).get(
                            "institution_ref", ""
                        )
                name = source.get("recorded_name") or item.get("context", {}).get(
                    "recorded_school_name", ""
                )
                sources = source.get("source_records", [source])
                current = resolve_reference(current_ref) if current_ref else None
                rows = school_comparison(
                    {
                        "name": name,
                        "source_records": sources,
                        "saved_reference": current_ref,
                        "targets": [
                            {"reference": current_ref, "metadata": current or {}}
                        ]
                        if current_ref
                        else [],
                    },
                    selected,
                )
                evidence = [
                    row.get("evidence", {})
                    for row in item.get("candidates", [])
                    if row.get("evidence", {}).get("stance")
                ] + list(item.get("retained_evidence", []))
            summary = summarize_evidence(
                evidence, ("identity", "temporal", "geographic")
            )
            return jsonify(
                {
                    "rows": rows,
                    "evidence": evidence,
                    "unknown_gaps": summary["unknown_gaps"],
                }
            )
        except ValueError as err:
            return jsonify({"error": str(err)}), 400

    @app.post("/items/<path:review_id>/evidence/preview")
    def preview_evidence(review_id: str) -> Any:
        prepare = getattr(service, "prepare_evidence", None)
        if not callable(prepare):
            abort(404)
        try:
            item = load_item(review_id)
        except ValueError as err:
            abort(404, str(err))
        try:
            if item["entity_type"] not in {"school", "member_education", "member"}:
                raise ValueError(
                    "Research evidence belongs to a school, education assertion, or exact member field"
                )
            payload = _evidence_payload_from_form()
            research_id = request.form.get("research_job_id", "")
            revisions = None
            if research_id:
                revisions = getattr(service, "research_revisions")()
                result = research.result(research_id, review_id, revisions)
                index = int(request.form.get("research_suggestion_index", "-1"))
                if result["status"] != "complete" or not 0 <= index < len(
                    result["suggestions"]
                ):
                    raise ValueError("Research suggestion is unavailable")
                if request.form.get("source_inspected") != "yes":
                    raise ValueError(
                        "Inspect the source before previewing retained evidence"
                    )
                payload["generated_by"] = "agent-search"
            preview = prepare(review_id, payload)
            if revisions is not None:
                preview["research_revisions"] = revisions
            if request.accept_mimetypes.best == "application/json":
                return jsonify(
                    {
                        "evidence_preview_token": _sign_evidence_preview(
                            preview, signing_secret
                        ),
                        "record": preview.get("record"),
                        "validation": preview.get("validation_outcome", "valid"),
                    }
                )
            draft_token = request.form.get("workspace_draft_token", "")
            draft = read_workspace_draft(review_id, draft_token) if draft_token else {}
            if review_id.startswith("member:"):
                parts = review_id.split(":")
                return member_page(
                    service.member(parts[1]),
                    form_values={**draft, **normalized_form_values(request.form)},
                    workspace_draft_token=draft_token,
                    research_evidence={
                        parts[-1]: {
                            "preview": preview,
                            "values": request.form,
                            "preview_token": _sign_evidence_preview(
                                preview, signing_secret
                            ),
                        }
                    },
                )
            return item_page(
                item,
                draft_values={**draft, **normalized_form_values(request.form)},
                evidence_preview=preview,
                evidence_values=request.form,
                workspace_draft_token=draft_token,
            )
        except ValueError as err:
            if request.accept_mimetypes.best == "application/json":
                return jsonify({"error": str(err)}), 400
            draft_token = request.form.get("workspace_draft_token", "")
            draft = read_workspace_draft(review_id, draft_token) if draft_token else {}
            if review_id.startswith("member:"):
                parts = review_id.split(":")
                return member_page(
                    service.member(parts[1]),
                    error=str(err),
                    form_values={**draft, **normalized_form_values(request.form)},
                    workspace_draft_token=draft_token,
                    research_evidence={
                        parts[-1]: {"values": request.form, "error": str(err)}
                    },
                ), 400
            return item_page(
                item,
                draft_values={**draft, **normalized_form_values(request.form)},
                evidence_error=str(err),
                evidence_values=request.form,
                workspace_draft_token=draft_token,
            ), 400

    @app.post("/items/<path:review_id>/evidence/save")
    def save_evidence(review_id: str) -> Any:
        save = getattr(service, "save_evidence", None)
        if not callable(save):
            abort(404)
        preview: dict[str, Any] | None = None
        try:
            preview = _read_preview(
                request.form.get("evidence_preview_token", ""), signing_secret
            )
            if preview.get("kind") != "evidence":
                raise ValueError(
                    "Preview a research evidence record before retaining it"
                )
            if (
                preview.get("review_id") != review_id
                or preview.get("record", {}).get("review_id") != review_id
            ):
                raise ValueError("Evidence preview belongs to a different review item")
            save(preview)
        except (OSError, ValueError) as err:
            if request.accept_mimetypes.best == "application/json":
                return jsonify({"error": str(err)}), 409 if isinstance(
                    err, StaleReviewError
                ) else 400
            try:
                item = load_item(review_id)
            except ValueError:
                return render_template("error.html", message=str(err)), 400
            record = preview.get("record", {}) if preview else {}
            values = {
                "evidence_" + key: str(value) if value is not None else ""
                for key, value in record.items()
            }
            if record.get("claim_value") is not None and not isinstance(
                record["claim_value"], str
            ):
                values["evidence_claim_value_json"] = json_text(record["claim_value"])
            message = (
                "The evidence save could not be confirmed. Check retained records before retrying."
                if isinstance(err, OSError)
                else f"{err}. Nothing was retained; review and preview the evidence again."
            )
            draft_token = request.form.get("workspace_draft_token", "")
            draft = read_workspace_draft(review_id, draft_token) if draft_token else {}
            if review_id.startswith("member:"):
                parts = review_id.split(":")
                response = member_page(
                    service.member(parts[1]),
                    error=message,
                    form_values=draft,
                    workspace_draft_token=draft_token,
                    research_evidence={parts[-1]: {"values": values, "error": message}},
                )
            else:
                response = item_page(
                    item,
                    draft_values=draft,
                    evidence_error=message,
                    evidence_values=values,
                    workspace_draft_token=draft_token,
                )
            return response, (
                503
                if isinstance(err, (OSError, ReviewBusyError))
                else 409
                if isinstance(err, StaleReviewError)
                else 400
            )
        if request.accept_mimetypes.best == "application/json":
            return jsonify({"record": preview["record"]})
        draft_token = request.form.get("workspace_draft_token", "")
        draft = read_workspace_draft(review_id, draft_token) if draft_token else {}
        if review_id.startswith("member:"):
            parts = review_id.split(":")
            return member_page(
                service.member(parts[1]),
                form_values=draft,
                workspace_draft_token=draft_token,
                research_evidence={parts[-1]: {"values": {"evidence_saved": "1"}}},
            )
        if draft_token and review_id.startswith(("school:", "education:")):
            return item_page(
                load_item(review_id),
                draft_values=draft,
                workspace_draft_token=draft_token,
                evidence_saved=True,
            )
        return redirect(
            url_for("detail", review_id=review_id, evidence_saved="1"), code=303
        )

    @app.post("/items/<path:review_id>/research")
    def find_evidence(review_id: str) -> Any:
        try:
            if not research.providers:
                raise ValueError(
                    "Configure a research provider when starting the reviewer"
                )
            member_parts = review_id.split(":")
            member_field = (
                member_parts[-1]
                if len(member_parts) == 3 and member_parts[0] == "member"
                else None
            )
            provider_id = request.form.get("provider_id", "")
            if member_field:
                provider_id = request.form.get(
                    f"research_provider_id_{member_field}", provider_id
                )
            else:
                provider_id = request.form.get("research_provider_id", provider_id)
            candidate_id = request.form.get("candidate_id") or (
                request.form.get(f"candidate_id_{member_field}")
                if member_field
                else None
            )
            if review_id.startswith("school:"):
                candidate_id = request.form.get("field_institution_ref") or candidate_id
            elif not member_field:
                candidate_id = request.form.get("field_institution_ref") or candidate_id
            context = getattr(service, "research_context")(
                review_id, candidate_id=candidate_id or None
            )
            identifier = research.start(provider_id, context)
            draft_token = sign_workspace_draft(review_id, request.form)
            if request.accept_mimetypes.best == "application/json":
                return jsonify(
                    {
                        "job_id": identifier,
                        "detail_url": url_for(
                            "research_detail", review_id=review_id, job_id=identifier
                        ),
                        "status_url": url_for(
                            "research_status", review_id=review_id, job_id=identifier
                        ),
                        "workspace_draft_token": draft_token,
                    }
                )
            if member_field:
                member_view = service.member(member_parts[1])
                return member_page(
                    member_view,
                    form_values=normalized_form_values(request.form),
                    research_jobs={member_field: identifier},
                    research_results={
                        member_field: research_result(review_id, identifier)
                    },
                    workspace_draft_token=draft_token,
                )
            if not member_field:
                item = load_item(review_id)
                return item_page(
                    item,
                    draft_values=normalized_form_values(request.form),
                    research_case={
                        "review_id": review_id,
                        "job_id": identifier,
                        "retained_evidence": item.get("retained_evidence", []),
                    },
                    research_result_data=research_result(review_id, identifier),
                    workspace_draft_token=draft_token,
                )
            return redirect(
                url_for("research_detail", review_id=review_id, job_id=identifier),
                code=303,
            )
        except ValueError as err:
            if request.accept_mimetypes.best == "application/json":
                return jsonify({"error": str(err)}), 400
            if review_id.startswith("member:"):
                parts = review_id.split(":")
                token = sign_workspace_draft(review_id, request.form)
                return member_page(
                    service.member(parts[1]),
                    error=str(err),
                    form_values=normalized_form_values(request.form),
                    workspace_draft_token=token,
                ), 400
            try:
                item = load_item(review_id)
            except (KeyError, ValueError):
                return render_template("error.html", message=str(err)), 400
            return item_page(
                item,
                error=str(err),
                draft_values=normalized_form_values(request.form),
                workspace_draft_token=sign_workspace_draft(review_id, request.form),
            ), 400

    def research_result(review_id: str, job_id: str) -> dict[str, Any]:
        return research.result(
            job_id, review_id, getattr(service, "research_revisions")()
        )

    @app.get("/items/<path:review_id>/research/<job_id>")
    def research_detail(review_id: str, job_id: str) -> Any:
        try:
            result = research_result(review_id, job_id)
            token = request.args.get("workspace_draft_token", "")
            if token and review_id.startswith("member:"):
                draft = read_workspace_draft(review_id, token)
                parts = review_id.split(":")
                return member_page(
                    service.member(parts[1]),
                    form_values=draft,
                    research_jobs={parts[-1]: job_id},
                    research_results={parts[-1]: result},
                    workspace_draft_token=token,
                )
            if token:
                draft = read_workspace_draft(review_id, token)
                item = load_item(review_id)
                return item_page(
                    item,
                    draft_values=draft,
                    research_case={
                        "review_id": review_id,
                        "job_id": job_id,
                        "retained_evidence": item.get("retained_evidence", []),
                    },
                    research_result_data=result,
                    workspace_draft_token=token,
                )
            return render_template(
                "research.html", review_id=review_id, job_id=job_id, result=result
            )
        except ValueError as err:
            return render_template("error.html", message=str(err)), 409

    @app.get("/items/<path:review_id>/research/<job_id>/status")
    def research_status(review_id: str, job_id: str) -> Any:
        try:
            return jsonify(research_result(review_id, job_id))
        except ValueError as err:
            return jsonify({"error": str(err)}), 409

    @app.get("/items/<path:review_id>/research/<job_id>/search-suggestions")
    def research_search_suggestions(review_id: str, job_id: str) -> Any:
        try:
            result = research_result(review_id, job_id)
            # Sandboxed in its own CSP-protected frame: no scripts, forms or parent access.
            return app.response_class(
                result.get("search_entry_point", ""), mimetype="text/html"
            )
        except ValueError:
            abort(409)

    @app.post("/items/<path:review_id>/research/<job_id>/suggestions/<int:index>")
    def inspect_suggestion(review_id: str, job_id: str, index: int) -> Any:
        try:
            if request.form.get("source_inspected") != "yes":
                raise ValueError(
                    "Inspect the cited source before reviewing its evidence"
                )
            result = research_result(review_id, job_id)
            if result["status"] != "complete" or not 0 <= index < len(
                result["suggestions"]
            ):
                raise ValueError("Research suggestion is unavailable")
            suggestion = result["suggestions"][index]
            values = {
                "evidence_" + key: value if isinstance(value, str) else ""
                for key, value in suggestion.items()
            }
            if not isinstance(suggestion["claim_value"], str):
                values["evidence_claim_value_json"] = json.dumps(
                    suggestion["claim_value"]
                )
            values.update(research_job_id=job_id, research_suggestion_index=str(index))
            if request.accept_mimetypes.best == "application/json":
                return jsonify({"evidence_values": values, "suggestion": suggestion})
            token = request.form.get("workspace_draft_token", "")
            saved_draft = read_workspace_draft(review_id, token) if token else {}
            saved_draft.update(
                {
                    key: request.form.getlist(key)
                    for key in request.form
                    if key != "csrf_token"
                }
            )
            token = sign_workspace_draft(review_id, request.form)
            if review_id.startswith("member:"):
                parts = review_id.split(":")
                member_view = service.member(parts[1])
                field = parts[-1]
                return member_page(
                    member_view,
                    form_values={**saved_draft, **normalized_form_values(request.form)},
                    research_jobs={field: job_id},
                    research_results={field: result},
                    workspace_draft_token=token,
                    research_evidence={field: {"values": values}},
                )
            return item_page(
                load_item(review_id),
                draft_values={**saved_draft, **normalized_form_values(request.form)},
                evidence_values=values,
                research_case={"review_id": review_id, "job_id": job_id},
                research_result_data=result,
                workspace_draft_token=token,
            )
        except ValueError as err:
            if request.accept_mimetypes.best == "application/json":
                return jsonify({"error": str(err)}), 409
            token = request.form.get("workspace_draft_token", "")
            draft = read_workspace_draft(review_id, token) if token else {}
            draft.update(normalized_form_values(request.form))
            if review_id.startswith("member:"):
                parts = review_id.split(":")
                return member_page(
                    service.member(parts[1]),
                    error=str(err),
                    form_values=draft,
                    workspace_draft_token=token,
                ), 409
            return item_page(
                load_item(review_id),
                error=str(err),
                draft_values=draft,
                workspace_draft_token=token,
            ), 409

    @app.post("/items/<path:review_id>/evidence/<evidence_id>/copy")
    def copy_member_evidence(review_id: str, evidence_id: str) -> Any:
        try:
            is_member_field = review_id.startswith("member:")
            value = getattr(
                service,
                "copy_member_evidence_value"
                if is_member_field
                else "copy_school_evidence_value",
            )(review_id, evidence_id)
            record = next(
                (
                    row
                    for row in getattr(service, "retained_evidence")(review_id)
                    if row.get("evidence_id") == evidence_id
                ),
                None,
            )
            if record is None:
                raise ValueError("Retained evidence is no longer available")
            if request.accept_mimetypes.best == "application/json":
                result = {
                    "value": value,
                    "evidence_id": evidence_id,
                    "source_url": record.get("source_url", ""),
                }
                if is_member_field:
                    result["field"] = review_id.rsplit(":", 1)[-1]
                else:
                    result["institution_ref"] = value
                return jsonify(result)
            if is_member_field:
                parts = review_id.split(":")
                draft_token = request.form.get("workspace_draft_token", "")
                draft = (
                    read_workspace_draft(review_id, draft_token) if draft_token else {}
                )
                draft.update(normalized_form_values(request.form))
                field = parts[-1]
                draft[f"value_{field}"] = value
                draft[f"source_url_{field}"] = record.get("source_url", "")
                refs = list(draft.get(f"evidence_refs_{field}", []))
                if evidence_id not in refs:
                    refs.append(evidence_id)
                draft[f"evidence_refs_{field}"] = refs
                return member_page(
                    service.member(parts[1]),
                    form_values=draft,
                    workspace_draft_token=sign_workspace_draft(review_id, draft),
                )
            draft_token = request.form.get("workspace_draft_token", "")
            draft = read_workspace_draft(review_id, draft_token) if draft_token else {}
            draft.update(normalized_form_values(request.form))
            draft["field_institution_ref"] = value
            draft["source_url"] = record.get("source_url", "")
            refs = list(draft.get("evidence_refs", []))
            if evidence_id not in refs:
                refs.append(evidence_id)
            draft["evidence_refs"] = refs
            draft["evidence_selection"] = "1"
            item = load_item(review_id)
            return item_page(
                item,
                draft_values=draft,
                workspace_draft_token=sign_workspace_draft(review_id, draft),
            )
        except (KeyError, ValueError) as err:
            if request.accept_mimetypes.best == "application/json":
                return jsonify({"error": str(err)}), 409
            token = request.form.get("workspace_draft_token", "")
            try:
                draft = read_workspace_draft(review_id, token) if token else {}
            except ValueError:
                draft = {}
            draft.update(normalized_form_values(request.form))
            if review_id.startswith("member:"):
                _, aph_id, _field = review_id.split(":", 2)
                return member_page(
                    service.member(aph_id),
                    error=str(err),
                    form_values=draft,
                    workspace_draft_token=sign_workspace_draft(review_id, draft),
                ), 409
            return item_page(
                load_item(review_id),
                error=str(err),
                draft_values=draft,
                workspace_draft_token=sign_workspace_draft(review_id, draft),
            ), 409

    @app.get("/new/<entity_type>")
    def new_item(entity_type: str) -> str:
        if entity_type not in {"manual_institution", "member_education"}:
            abort(404)
        payload = (
            {"country": "Australia"}
            if entity_type == "manual_institution"
            else {"aph_id": request.args.get("aph_id", "")}
        )
        return item_page(
            {
                "review_id": "",
                "entity_type": entity_type,
                "candidates": [],
                "decision": None,
                "history": [],
                "context": {},
            },
            payload=payload,
            new_entity_type=entity_type,
        )

    def prepare_form(
        review_id: str, item: dict[str, Any], *, retried: bool = False
    ) -> Any:
        payload: dict[str, Any] = {}
        bound: dict[str, Any] | None = None
        try:
            action = request.form.get("action", "accept")
            guided = request.form.get("payload_mode") == "guided"
            school_workflow = (
                item["entity_type"] == "school"
                and request.form.get("school_workflow") == "1"
            )
            if school_workflow and not guided:
                action = request.form.get("advanced_action", action)
            payload = _payload_from_form(item["entity_type"])
            reason = action if action in RESOLUTION_REASONS else None
            if (
                reason
                and guided
                and (
                    school_workflow
                    or (
                        item["entity_type"] == "member_education"
                        and payload.get("recorded_school_name")
                    )
                )
            ):
                action = "research"
            if action not in ACTIONS:
                raise ValueError("Unknown review action")
            if item["entity_type"] == "member_education" and request.form.get(
                "choose_ref"
            ):
                ref = request.form["choose_ref"]
                if resolve_reference(ref) is None:
                    raise ValueError(
                        "Choose an institution available in the local register"
                    )
                payload["institution_ref"] = ref
                draft = dict(request.form)
                draft["field_institution_ref"] = ref
                draft["payload"] = json_text(payload)
                return item_page(item, payload=payload, draft_values=draft)
            supersedes = [
                value.strip()
                for value in request.form.get("supersedes", "").split(",")
                if value.strip()
            ]
            if school_workflow:
                bound = _read_preview(
                    request.form.get("form_token", ""), signing_secret
                )
                if (
                    bound.get("source_revision") != "school-form"
                    or bound.get("event", {}).get("review_id") != review_id
                ):
                    raise ValueError("Invalid school form; reload this item")
                if bound["event"] != form_state(item):
                    raise StaleReviewError(
                        "Decisions changed or school context changed while your form was open. Your draft is retained; review the current page and preview again"
                    )
                draft = dict(request.form)
                if request.form.get("choose_ref"):
                    ref = request.form["choose_ref"]
                    if resolve_reference(ref) is None:
                        raise ValueError(
                            "Institution reference is unavailable in the local register or active manual definitions"
                        )
                    payload["institution_ref"] = ref
                    draft["field_institution_ref"] = ref
                    draft["payload"] = json_text(payload)
                    return item_page(item, payload=payload, draft_values=draft)
                if request.form.get("use_source"):
                    link = source_link(request.form["use_source"])
                    if not link:
                        raise ValueError("Choose an HTTP(S) evidence source")
                    draft["source_url"] = link
                    return item_page(item, payload=payload, draft_values=draft)
                if "find_school" in request.form:
                    query = request.form["find_school"] or request.form.get(
                        "lookup_query", ""
                    )
                    draft["lookup_query"] = query
                    item["lookup_results"] = service.lookup_institutions(
                        query, limit=20
                    )
                    return item_page(item, payload=payload, draft_values=draft)
                if guided:
                    if action not in {"map", "research", "reject"}:
                        raise ValueError("Choose a school mapping disposition")
                    supersedes = bound["event"]["heads"]
                    for key in (
                        "resolution_reason",
                        "requires_individual_resolution",
                        "resolution_only",
                    ):
                        payload.pop(key, None)
                    if reason:
                        payload["resolution_reason"] = reason
                        payload["requires_individual_resolution"] = True
                    if action in {"research", "reject"}:
                        _clear_resolution_fields(payload)
                    if supersedes:
                        replacement_action, action = action, "supersede"
                    else:
                        replacement_action = None
                else:
                    replacement_action = (
                        request.form.get("replacement_action", "accept")
                        if action == "supersede"
                        else None
                    )
            else:
                replacement_action = (
                    request.form.get("replacement_action", "accept")
                    if action == "supersede"
                    else None
                )
            if item["entity_type"] == "member_education" and guided:
                outcome = replacement_action or action
                reason = outcome if outcome in RESOLUTION_REASONS else reason
                if reason and not payload.get("recorded_school_name"):
                    raise ValueError(
                        "Per-member resolution needs a recorded school name"
                    )
                if outcome in RESOLUTION_REASONS:
                    replacement_action = "research"
                    outcome = "research"
                # The guided disposition controls this research-only marker.
                # A previous decision's metadata must not change the new action.
                for key in (
                    "resolution_only",
                    "resolution_reason",
                    "requires_individual_resolution",
                ):
                    payload.pop(key, None)
                if reason:
                    payload["resolution_reason"] = reason
                if outcome == "research" and payload.get("recorded_school_name"):
                    payload["resolution_only"] = True
                if outcome in {"research", "reject"}:
                    _clear_resolution_fields(payload)
                if outcome == "map":
                    # A populated attendance form can also resolve school identity.
                    # Mapping must never submit changes to its source attendance.
                    for key in (
                        "attended_status",
                        "confidence",
                        "retrieved_at",
                        "years_attended",
                        "graduation_year",
                    ):
                        payload.pop(key, None)
            if guided and (replacement_action or action) in {"accept", "map"}:
                roles: dict[str, list[str]] = {}
                for key in request.form:
                    if key.startswith("context_role:") and request.form[key]:
                        role = request.form[key]
                        if role not in ROLE_TYPES:
                            raise ValueError("Unknown reviewed context role")
                        roles.setdefault(role, []).append(
                            key.removeprefix("context_role:")
                        )
                payload.pop("context_evidence_refs", None)
                if roles:
                    payload["context_evidence_refs"] = roles
            preview = service.prepare(
                review_id,
                action,
                payload,
                source_url=request.form.get("source_url", "").strip(),
                reviewer=request.form.get("reviewer", "").strip() or None,
                notes=request.form.get("notes", "").strip(),
                supersedes=supersedes or None,
                replacement_action=replacement_action,
            )
            if school_workflow and preview["revision"] != item["form_revision"]:
                raise StaleReviewError(
                    "Decisions changed while previewing; review and preview again"
                )
            # Present current target metadata even if inputs moved just before
            # preparation. Save still verifies the exact preview's source bytes.
            g.pop("institution_resolver", None)
            # A discovered school for a missing-education case receives its own
            # immutable identity in the shared service before it is recorded.
            item = {**item, "review_id": preview["event"]["review_id"]}
            return item_page(
                item,
                payload=payload,
                preview=preview,
                workspace_draft_token=sign_workspace_draft(review_id, request.form),
            )
        except ValueError as err:
            error = err
            if isinstance(err, StaleReviewError) and item["entity_type"] == "school":
                g.pop("institution_resolver", None)
                item = load_item(review_id)
                if (
                    not retried
                    and bound is not None
                    and bound.get("event") == form_state(item)
                ):
                    # Reload/retry a raced preview only if the reviewer's page
                    # is unchanged. Changed heads never get silently replaced.
                    return prepare_form(review_id, item, retried=True)
            if item[
                "entity_type"
            ] == "school" and service.review_revision() != item.get("form_revision"):
                item = load_item(review_id)
                error = StaleReviewError(
                    "Decisions changed. Nothing was saved; review the current decisions and preview again"
                )
            return item_page(
                item,
                payload=payload,
                error=str(error),
                draft_values=normalized_form_values(request.form),
                workspace_draft_token=sign_workspace_draft(review_id, request.form),
            ), (409 if isinstance(error, StaleReviewError) else 400)

    @app.post("/items/<path:review_id>/preview")
    def preview_item(review_id: str) -> Any:
        try:
            item = load_item(review_id)
        except ValueError as err:
            abort(404, str(err))
        return prepare_form(review_id, item)

    @app.post("/new/<entity_type>/preview")
    def preview_new(entity_type: str) -> Any:
        if entity_type not in {"manual_institution", "member_education"}:
            abort(404)
        try:
            payload = _payload_from_form(entity_type)
            if entity_type == "manual_institution":
                from apemap.review.model import institution_review_id

                review_id = institution_review_id(
                    str(payload.get("institution_ref", ""))
                )
            else:
                from apemap.review.model import education_review_id

                review_id = education_review_id(
                    str(payload.get("aph_id", "")),
                    str(payload.get("recorded_school_name", "")),
                )
            item = service.show(review_id)
        except ValueError as err:
            return item_page(
                {
                    "review_id": "",
                    "entity_type": entity_type,
                    "candidates": [],
                    "decision": None,
                    "history": [],
                    "context": {},
                },
                error=str(err),
                new_entity_type=entity_type,
            ), 400
        return prepare_form(review_id, item)

    @app.post("/items/<path:review_id>/save")
    def save_item(review_id: str) -> Any:
        preview: dict[str, Any] | None = None
        navigation = None
        saved_draft: dict[str, Any] = {}
        try:
            draft_token = request.form.get("workspace_draft_token", "")
            if draft_token:
                saved_draft = read_workspace_draft(review_id, draft_token)
            preview = _read_preview(
                request.form.get("preview_token", ""), signing_secret
            )
            if request.form.get("advance") == "next":
                navigation = submitted_navigation(
                    request.form.get("navigation_token", ""), review_id
                )
            if preview.get("kind") == "evidence":
                raise ValueError("Evidence retention cannot save a review decision")
            if preview.get("default_application_available") is False:
                raise ValueError(
                    "Run a review build and inspect the affected assertions before saving a school-wide default"
                )
            if preview.get("source_revision") == "school-form":
                raise ValueError("A school draft must be previewed before saving")
            if preview.get("event", {}).get("review_id") != review_id:
                raise ValueError("Preview belongs to a different review item")
            service.save(preview)
        except (OSError, ValueError) as err:
            if isinstance(err, (StaleReviewError, OSError)) or saved_draft:
                retryable = isinstance(err, (ReviewBusyError, OSError))
                event = preview["event"] if preview else {}
                draft = {
                    **saved_draft,
                    **normalized_form_values(request.form),
                }
                fallback = {
                    "payload_mode": "json",
                    "payload": json_text(event.get("payload", {})),
                    "action": event.get("action", "accept"),
                    "source_url": event.get("source_url", ""),
                    "reviewer": event.get("reviewer", ""),
                    "notes": event.get("notes", ""),
                    "supersedes": ", ".join(event.get("supersedes", [])),
                    "replacement_action": event.get("replacement_action") or "accept",
                }
                for key, value in fallback.items():
                    draft.setdefault(key, value)
                if event.get("entity_type") == "school":
                    if not saved_draft:
                        draft.update(
                            {
                                "school_workflow": "1",
                                "payload_mode": "guided",
                                "action": event.get("replacement_action")
                                or event.get("action", "map"),
                                **{
                                    "field_" + key: str(
                                        event.get("payload", {}).get(key, "")
                                    )
                                    for key in (
                                        "recorded_name",
                                        "institution_ref",
                                        "relationship_type",
                                        *SCHOOL_CONTEXT_FIELDS,
                                    )
                                },
                            }
                        )
                        draft["action"] = _guided_action(
                            draft["action"], event.get("payload", {})
                        )
                try:
                    item = load_item(review_id)
                except (ValueError, OSError):
                    item = {
                        "review_id": review_id,
                        "entity_type": event.get("entity_type", "school"),
                        "candidates": [],
                        "decision": None,
                        "history": [],
                        "context": {},
                    }
                if isinstance(err, ReviewBusyError):
                    message = "Another writer is busy. Nothing was saved. Retry this preview after it finishes."
                elif isinstance(err, OSError):
                    message = "The save could not be confirmed because of a file error. Check the current history before retrying this preview. Your draft is retained."
                else:
                    message = f"{err}. Nothing was saved. Review the current evidence and preview again."
                response = item_page(
                    item,
                    payload=event.get("payload"),
                    error=message,
                    draft_values=draft,
                    workspace_draft_token=sign_workspace_draft(review_id, draft),
                    retry_preview=preview if retryable else None,
                    retry_token=request.form.get("preview_token")
                    if retryable
                    else None,
                )
                return (
                    (response, 503, {"Retry-After": "1"})
                    if retryable
                    else (response, 409)
                )
            return render_template("error.html", message=str(err)), 400
        if navigation:
            return advance_redirect(
                navigation,
                review_id,
                preview.get("event", {}).get("entity_type", ""),
                saved_count=1,
                remaining_count=0,
            )
        return redirect(url_for("detail", review_id=review_id, saved="1"), code=303)

    return app


def serve(
    service: ReviewServiceLike,
    port: int = 8765,
    workers: int = 4,
    *,
    research_providers: dict[str, ResearchProvider] | None = None,
) -> None:
    """Run a single loopback process with a bounded pool of request threads."""
    if not 1 <= port <= 65535:
        raise ValueError("Port must be between 1 and 65535")
    if workers < 1:
        raise ValueError("Workers must be a positive integer")
    with terminal_logging():
        with operation("Initializing review UI"):
            app = (
                create_app(service)
                if research_providers is None
                else create_app(service, research_providers=research_providers)
            )
        logger.info(
            "Starting review server at http://127.0.0.1:%d with %d workers; "
            "press Ctrl+C to stop",
            port,
            workers,
        )
        try:
            waitress_serve(
                app,
                host="127.0.0.1",
                port=port,
                threads=workers,
                max_request_body_size=256 * 1024,
            )
        except Exception as exc:
            report_error("Review server", exc)
            raise
        finally:
            jobs = getattr(app, "extensions", {}).get("research_jobs")
            if jobs is not None:
                jobs.close()
            logger.info("Review server stopped")
