"""Optional, offline, server-rendered interface to the shared review service."""

from __future__ import annotations

import base64
from collections import Counter
from collections.abc import Mapping
import hashlib
import hmac
import json
import secrets
import time
from typing import Any, Protocol
from urllib.parse import urlsplit

from flask import Flask, abort, jsonify, redirect, render_template, request, url_for

from apemap.review.store import StaleReviewError

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
    ),
    "member": (
        ("aph_id", "APH identifier", "text", ()),
        ("field", "Member field", "select", ("date_of_birth", "gender", "wikidata_id")),
        ("value", "Corrected value", "text", ()),
    ),
    "member_education": (
        ("aph_id", "APH identifier", "text", ()),
        ("recorded_school_name", "School name as recorded", "text", ()),
        ("institution_ref", "Institution reference (optional)", "text", ()),
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
    # proposed event and its two optimistic-lock revisions are needed to save.
    commit = {key: preview[key] for key in ("event", "revision", "source_revision")}
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
            elif kind == "number":
                try:
                    payload[name] = float(value)
                except ValueError as err:
                    raise ValueError(f"{name} must be a number") from err
            else:
                payload[name] = value
        if entity_type == "member" and request.form.get("clear_value") == "1":
            payload["value"] = None
    return payload


def create_app(service: ReviewServiceLike) -> Flask:
    """Build an isolated Flask app with no separate decision persistence."""
    app = Flask(__name__)
    app.config.update(
        TRUSTED_HOSTS=["127.0.0.1", "localhost"],
        MAX_CONTENT_LENGTH=256 * 1024,
    )
    csrf = secrets.token_urlsafe(32)
    signing_secret = secrets.token_bytes(32)
    app.jinja_env.filters["json_text"] = json_text
    app.jinja_env.filters["source_link"] = source_link

    @app.context_processor
    def template_context() -> dict[str, Any]:
        return {
            "csrf_token": csrf,
            "entity_labels": ENTITY_LABELS,
            "statuses": STATUSES,
            "actions": ACTIONS,
            "action_labels": ACTION_LABELS,
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
            "connect-src 'self'; form-action 'self'; "
            "base-uri 'none'; frame-ancestors 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
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
    ) -> str:
        chosen = _initial_payload(item) if payload is None else payload
        values: Mapping[str, Any] = (
            request.form if draft_values is None else draft_values
        )
        if item.get("conflicts") and not values:
            values = {
                "action": "supersede",
                "supersedes": ", ".join(
                    event["decision_id"] for event in item["conflicts"]
                ),
                "replacement_action": "accept",
            }
        return render_template(
            "item.html",
            item=item,
            payload=chosen,
            fields=FIELDS.get(item["entity_type"], ()),
            preview=preview,
            preview_token=_sign_preview(preview, signing_secret) if preview else None,
            evidence_links=_evidence_links(item),
            error=error,
            new_entity_type=new_entity_type,
            form_values=values,
        )

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
        rows = service.candidates(entity_type, status, parliament, search)
        totals = Counter(row.get("status", "pending") for row in service.candidates())
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

    @app.get("/items/<path:review_id>")
    def detail(review_id: str) -> str:
        try:
            item = service.show(review_id)
        except ValueError as err:
            abort(404, str(err))
        return item_page(item)

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

    def prepare_form(review_id: str, item: dict[str, Any]) -> Any:
        payload: dict[str, Any] = {}
        try:
            action = request.form.get("action", "accept")
            if action not in ACTIONS:
                raise ValueError("Unknown review action")
            payload = _payload_from_form(item["entity_type"])
            supersedes = [
                value.strip()
                for value in request.form.get("supersedes", "").split(",")
                if value.strip()
            ]
            preview = service.prepare(
                review_id,
                action,
                payload,
                source_url=request.form.get("source_url", "").strip(),
                reviewer=request.form.get("reviewer", "").strip() or None,
                notes=request.form.get("notes", "").strip(),
                supersedes=supersedes or None,
                replacement_action=request.form.get("replacement_action", "accept")
                if action == "supersede"
                else None,
            )
            # A discovered school for a missing-education case receives its own
            # immutable identity in the shared service before it is recorded.
            item = {**item, "review_id": preview["event"]["review_id"]}
            return item_page(item, payload=payload, preview=preview)
        except ValueError as err:
            return item_page(item, payload=payload, error=str(err)), (
                409 if isinstance(err, StaleReviewError) else 400
            )

    @app.post("/items/<path:review_id>/preview")
    def preview_item(review_id: str) -> Any:
        try:
            item = service.show(review_id)
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
            if preview.get("event", {}).get("review_id") != review_id:
                raise ValueError("Preview belongs to a different review item")
            service.save(preview)
        except ValueError as err:
            if isinstance(err, StaleReviewError):
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
                try:
                    item = service.show(review_id)
                except ValueError:
                    item = {
                        "review_id": review_id,
                        "entity_type": event.get("entity_type", "school"),
                        "candidates": [],
                        "decision": None,
                        "history": [],
                        "context": {},
                    }
                return item_page(
                    item,
                    payload=event.get("payload"),
                    error=f"{err}. Nothing was saved. Review the current evidence and preview again.",
                    draft_values=draft,
                ), 409
            return render_template("error.html", message=str(err)), 400
        return redirect(url_for("detail", review_id=review_id, saved="1"), code=303)

    return app


def serve(service: ReviewServiceLike, port: int = 8765) -> None:
    """Run only on loopback, with no debugger, reloader or background workers."""
    if not 1 <= port <= 65535:
        raise ValueError("Port must be between 1 and 65535")
    create_app(service).run(
        host="127.0.0.1", port=port, debug=False, use_reloader=False, threaded=False
    )
