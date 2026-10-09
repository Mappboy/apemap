"""Optional, offline, server-rendered interface to the shared review service."""

from __future__ import annotations

import base64
from dataclasses import asdict
from collections import Counter
from collections.abc import Callable, Mapping
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path
from threading import Lock
from typing import Any, Protocol
from urllib.parse import urlsplit

from flask import Flask, abort, g, jsonify, redirect, render_template, request, url_for
from waitress import serve as waitress_serve

from apemap.review.store import ReviewBusyError, StaleReviewError
from apemap.review.schools import RELATIONSHIPS, group_school_rows, school_view
from apemap.education_context import SCHOOL_CONTEXT_FIELDS
from apemap.review.context_evidence import ROLE_TYPES
from apemap.review.research import ResearchJobs, ResearchProvider

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


def create_app(
    service: ReviewServiceLike,
    *,
    research_providers: dict[str, ResearchProvider] | None = None,
) -> Flask:
    """Build an isolated Flask app with no separate decision persistence."""
    app = Flask(__name__)
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

    def resolve_reference(reference: str) -> dict[str, Any] | None:
        if "institution_resolver" not in g:
            g.institution_resolver = service.institution_resolver()
        return g.institution_resolver(reference)

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
            "resolution_fields": RESOLUTION_FIELDS,
            "evidence_enabled": callable(getattr(service, "prepare_evidence", None))
            and callable(getattr(service, "save_evidence", None)),
        }

    @app.before_request
    def protect_writes() -> None:
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
            **evidence_context,
        )

    def build_queue_rows() -> list[dict[str, Any]]:
        all_rows = group_school_rows(service.candidates())
        decisions = service.school_decisions()
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
        return all_rows

    def queue_rows() -> list[dict[str, Any]]:
        nonlocal queue_cache
        revision_reader = getattr(service, "queue_revision", None)
        if revision_reader is None:
            return build_queue_rows()
        # Hash outside the queue lock. Lookup, details, previews and saves never
        # take this lock or consume this presentation cache.
        revision = revision_reader()
        with queue_lock:
            if queue_cache is not None and queue_cache[0] == revision:
                return queue_cache[1]
            rows = build_queue_rows()
            if revision_reader() == revision:
                queue_cache = (revision, rows)
            return rows

    @app.get("/")
    def queue() -> str:
        entity_type = request.args.get("entity_type") or None
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
        if status and status not in STATUSES:
            abort(400, "Unknown status")
        search = request.args.get("q") or None
        all_rows = queue_rows()
        rows = [
            row
            for row in all_rows
            if (not entity_type or row["entity_type"] == entity_type)
            and (not status or row["status"] == status)
            and (parliament is None or parliament in row["parliaments"])
            and (
                not search
                or search.casefold()
                in json.dumps(row, default=str, ensure_ascii=False).casefold()
            )
        ]
        totals = Counter(row.get("status", "pending") for row in all_rows)
        pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, pages)
        filters = {
            "entity_type": entity_type or "",
            "status": status or "",
            "parliament": parliament or "",
            "q": search or "",
        }

        def page_url(number: int) -> str:
            return url_for(
                "queue",
                entity_type=entity_type or "",
                status=status or "",
                parliament=parliament or "",
                q=search or "",
                page=number,
            )

        return render_template(
            "queue.html",
            rows=rows[(page - 1) * PAGE_SIZE : page * PAGE_SIZE],
            totals=totals,
            total=len(rows),
            queue_label="school mappings"
            if entity_type == "school"
            else "review entries",
            page=page,
            pages=pages,
            filters=filters,
            previous_url=page_url(page - 1) if page > 1 else None,
            next_url=page_url(page + 1) if page < pages else None,
        )

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

    @app.get("/items/<path:review_id>")
    def detail(review_id: str) -> str:
        try:
            item = load_item(review_id)
        except ValueError as err:
            abort(404, str(err))
        return item_page(item)

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
            if item["entity_type"] not in {"school", "member_education"}:
                raise ValueError(
                    "Research evidence belongs to a school or education assertion"
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
            return item_page(
                item,
                draft_values={},
                evidence_preview=preview,
                evidence_values=request.form,
            )
        except ValueError as err:
            return item_page(
                item,
                draft_values={},
                evidence_error=str(err),
                evidence_values=request.form,
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
            return item_page(
                item,
                draft_values={},
                evidence_error=message,
                evidence_values=values,
            ), (
                503
                if isinstance(err, (OSError, ReviewBusyError))
                else 409
                if isinstance(err, StaleReviewError)
                else 400
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
            context = getattr(service, "research_context")(review_id)
            identifier = research.start(request.form.get("provider_id", ""), context)
            return redirect(
                url_for("research_detail", review_id=review_id, job_id=identifier),
                code=303,
            )
        except ValueError as err:
            return render_template("error.html", message=str(err)), 400

    def research_result(review_id: str, job_id: str) -> dict[str, Any]:
        return research.result(
            job_id, review_id, getattr(service, "research_revisions")()
        )

    @app.get("/items/<path:review_id>/research/<job_id>")
    def research_detail(review_id: str, job_id: str) -> Any:
        try:
            result = research_result(review_id, job_id)
            return render_template(
                "research.html", review_id=review_id, job_id=job_id, result=result
            )
        except ValueError as err:
            return render_template("error.html", message=str(err)), 409

    @app.get("/items/<path:review_id>/research/<job_id>/status")
    def research_status(review_id: str, job_id: str) -> Any:
        try:
            return jsonify({"status": research_result(review_id, job_id)["status"]})
        except ValueError:
            return jsonify({"status": "stale"}), 409

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
            return item_page(
                load_item(review_id), draft_values={}, evidence_values=values
            )
        except ValueError as err:
            return render_template("error.html", message=str(err)), 409

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
            return item_page(item, payload=payload, preview=preview)
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
            return item_page(item, payload=payload, error=str(error)), (
                409 if isinstance(error, StaleReviewError) else 400
            )

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
        try:
            preview = _read_preview(
                request.form.get("preview_token", ""), signing_secret
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
            if isinstance(err, (StaleReviewError, OSError)):
                retryable = isinstance(err, (ReviewBusyError, OSError))
                event = preview["event"] if preview else {}
                draft = {
                    "payload_mode": "json",
                    "payload": json_text(event.get("payload", {})),
                    "action": event.get("action", "accept"),
                    "source_url": event.get("source_url", ""),
                    "reviewer": event.get("reviewer", ""),
                    "notes": event.get("notes", ""),
                    "supersedes": ", ".join(event.get("supersedes", [])),
                    "replacement_action": event.get("replacement_action") or "accept",
                }
                if event.get("entity_type") == "school":
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
    app = (
        create_app(service)
        if research_providers is None
        else create_app(service, research_providers=research_providers)
    )
    try:
        waitress_serve(
            app,
            host="127.0.0.1",
            port=port,
            threads=workers,
            max_request_body_size=256 * 1024,
        )
    finally:
        jobs = getattr(app, "extensions", {}).get("research_jobs")
        if jobs is not None:
            jobs.close()
