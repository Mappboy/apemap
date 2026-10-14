"""School-to-institution draft routes sharing the guided editor and review service."""

from __future__ import annotations

from collections.abc import Callable
import json
from typing import Any

from flask import Flask, g, jsonify, redirect, render_template, request, url_for

from apemap.review.evidence import evidence_revision
from apemap.review.institution_drafts import (
    eligible_sources,
    validate_draft_provenance,
    matching_manual_institutions,
)
from apemap.review.manual_institutions import FIELDS, manual_institution_reference
from apemap.review.model import active_heads, institution_review_id
from apemap.review.research import ResearchJobs
from apemap.review.service import ReviewService
from apemap.review.store import StaleReviewError


def register_institution_draft_routes(
    app: Flask,
    service: ReviewService,
    jobs: ResearchJobs,
    item_page: Callable[..., str],
    load_item: Callable[[str], dict[str, Any]],
    sign_workspace: Callable[..., str],
    read_workspace: Callable[..., dict[str, Any]],
    normalize_form: Callable[..., dict[str, Any]],
    sign: Callable[[dict[str, Any]], str],
    read: Callable[[str], dict[str, Any]],
) -> None:
    def revisions() -> dict[str, str]:
        return {
            **service.research_revisions(),
            "evidence_revision": evidence_revision(service.evidence_path),
        }

    def definitions() -> list[dict[str, Any]]:
        return [
            heads[0].payload
            for heads in active_heads(service.events(allow_conflicts=True)).values()
            if len(heads) == 1
            and heads[0].entity_type == "manual_institution"
            and heads[0].effective_action == "accept"
        ]

    def return_to_school(case: str, token: str, reference: str) -> str:
        draft = read_workspace(case, token)
        item = load_item(case)
        draft["field_institution_ref"] = reference
        draft["action"] = "map"
        draft["school_workflow"] = "1"
        draft.setdefault("payload_mode", "guided")
        payload = None
        error = None
        if draft["payload_mode"] == "json":
            try:
                parsed = json.loads(draft.get("payload") or "{}")
                if not isinstance(parsed, dict):
                    raise ValueError("School payload must be an object")
                payload = {**parsed, "institution_ref": reference}
                draft["payload"] = json.dumps(payload)
            except ValueError:
                error = "Institution selected. Your original JSON draft is preserved; correct it before previewing the school mapping."
        return item_page(
            item,
            payload=payload,
            error=error,
            draft_values=draft,
            workspace_draft_token=sign_workspace(case, draft),
        )

    def draft_data(case: str, token: str) -> dict[str, Any]:
        data = read(token)
        if data.get("kind") != "institution_draft" or data.get("review_id") != case:
            raise ValueError("Institution draft belongs to a different school")
        read_workspace(case, data["return_token"])
        return {**data, "token": token}

    def check_revisions(data: dict[str, Any]) -> None:
        if data["revisions"] != revisions():
            raise StaleReviewError(
                "Institution evidence or decisions changed; generate a fresh draft before previewing"
            )

    def draft_page(
        data: dict[str, Any],
        *,
        payload: dict[str, Any] | None = None,
        error: str | None = None,
        preview: dict[str, Any] | None = None,
    ) -> str:
        chosen = data["result"]["payload"] if payload is None else payload
        item = {
            "review_id": preview["event"]["review_id"] if preview else "",
            "entity_type": "manual_institution",
            "candidates": [],
            "decision": None,
            "history": [],
            "context": {},
        }
        sources = eligible_sources(
            data["review_id"], service.retained_evidence(data["review_id"])
        )
        display = {
            **data,
            "sources": [record.to_dict() for record in sources.values()],
            "manual_matches": matching_manual_institutions(
                service, str(chosen.get("school_name") or "")
            ),
        }
        if request.endpoint == "preview_institution_draft":
            display["result"] = {
                **data["result"],
                "field_sources": {
                    field: request.form.getlist("institution_sources_" + field)
                    for field in FIELDS
                },
            }
        elif chosen.get("institution_draft_provenance"):
            display["result"] = {
                **data["result"],
                "field_sources": {
                    field: fact["evidence_ids"]
                    for field, fact in chosen["institution_draft_provenance"][
                        "fields"
                    ].items()
                },
            }
        initial = read_workspace(data["review_id"], data["return_token"])
        name_refs = data["result"]["field_sources"].get("name", [])
        values = (
            {**data.get("saved_form", {}), **normalize_form(request.form)}
            if request.method == "POST"
            else {
                "source_url": sources[name_refs[0]].source_url
                if name_refs and name_refs[0] in sources
                else "",
                "reviewer": initial.get("reviewer", ""),
            }
        )
        return item_page(
            item,
            payload=chosen,
            new_entity_type=None if preview else "manual_institution",
            error=error,
            preview=preview,
            institution_draft=display,
            draft_values=values,
        )

    @app.post("/items/<path:review_id>/institution-use")
    def use_manual_institution(review_id: str) -> Any:
        item = None
        try:
            if not review_id.startswith("school:"):
                raise ValueError("Institution reuse requires a school case")
            item = load_item(review_id)
            reference = request.form.get("manual_reference", "")
            if (
                not reference.startswith("manual:")
                or service.resolve_institution(reference) is None
            ):
                raise ValueError("Saved manual institution is unavailable")
            token = request.form.get("institution_return_token") or sign_workspace(
                review_id, request.form
            )
            read_workspace(review_id, token)
            return return_to_school(review_id, token, reference)
        except ValueError as exc:
            if item is None:
                return render_template("error.html", message=str(exc)), 400
            return item_page(
                item,
                error=str(exc),
                draft_values=normalize_form(request.form),
            ), 400

    @app.post("/items/<path:review_id>/institution-draft")
    def generate_institution_draft(review_id: str) -> Any:
        item = None
        try:
            if not review_id.startswith("school:"):
                raise ValueError("Institution generation requires a school case")
            item = load_item(review_id)
            source = revisions()
            context = {
                "review_id": review_id,
                "recorded_name": item["context"].get("recorded_name")
                or next(
                    (
                        row["payload"].get("recorded_name")
                        for row in item["candidates"]
                        if row.get("payload", {}).get("recorded_name")
                    ),
                    "",
                ),
                "retained_evidence": service.retained_evidence(review_id),
                "manual_institutions": definitions(),
                **source,
            }
            if not eligible_sources(review_id, context["retained_evidence"]):
                raise ValueError(
                    "Retain institution evidence for this school before generating a draft"
                )
            if source != revisions():
                raise StaleReviewError("Institution inputs changed; reload and retry")
            context["return_token"] = sign_workspace(review_id, request.form)
            job_id = jobs.start(
                request.form.get("institution_provider_id", ""),
                context,
                institution_draft=True,
            )
            location = url_for(
                "institution_draft_result", review_id=review_id, job_id=job_id
            )
            if request.accept_mimetypes.best == "application/json":
                return jsonify(
                    {"job_id": job_id, "status_url": location, "review_url": location}
                )
            return redirect(location, code=303)
        except ValueError as exc:
            status = 409 if isinstance(exc, StaleReviewError) else 400
            if request.accept_mimetypes.best == "application/json":
                return jsonify({"error": str(exc)}), status
            if item is None:
                return render_template("error.html", message=str(exc)), status
            return item_page(
                item,
                error=str(exc),
                draft_values=normalize_form(request.form),
            ), status

    @app.get("/items/<path:review_id>/institution-draft/<job_id>")
    def institution_draft_result(review_id: str, job_id: str) -> Any:
        job = None
        try:
            job = jobs.get(job_id, review_id)
            if job.context.get("operation") != "institution_draft":
                raise ValueError("This job is not an institution interpretation")
            read_workspace(review_id, job.context["return_token"])
            result = jobs.result(job_id, review_id, revisions())
            if request.accept_mimetypes.best == "application/json":
                return jsonify(
                    {"status": result["status"], "error": result.get("error", "")}
                )
            if result["status"] == "running":
                return render_template(
                    "institution-draft-running.html", review_id=review_id, job_id=job_id
                )
            if result["status"] != "complete":
                return item_page(
                    load_item(review_id),
                    error=result["error"],
                    draft_values=read_workspace(review_id, job.context["return_token"]),
                ), 400
            data = {
                "kind": "institution_draft",
                "review_id": review_id,
                "result": result,
                "provider": job.provenance.get("provider") or job.provider_id,
                "model": job.provenance.get("model") or "external",
                "return_token": job.context["return_token"],
                "revisions": {key: job.context[key] for key in revisions()},
            }
            return draft_page({**data, "token": sign(data)})
        except ValueError as exc:
            if request.accept_mimetypes.best == "application/json":
                return jsonify({"error": str(exc)}), 409
            if job is not None and job.context.get("return_token"):
                try:
                    return item_page(
                        load_item(review_id),
                        error=str(exc),
                        draft_values=read_workspace(
                            review_id, job.context["return_token"]
                        ),
                    ), 409
                except ValueError:
                    pass
            return render_template("error.html", message=str(exc)), 409

    @app.get("/items/<path:review_id>/institution-draft/<job_id>/cancel")
    def cancel_institution_draft(review_id: str, job_id: str) -> Any:
        try:
            job = jobs.get(job_id, review_id)
            if job.context.get("operation") != "institution_draft":
                raise ValueError("This job is not an institution interpretation")
            return item_page(
                load_item(review_id),
                draft_values=read_workspace(review_id, job.context["return_token"]),
            )
        except ValueError as exc:
            return render_template("error.html", message=str(exc)), 409

    @app.post("/items/<path:review_id>/institution-draft-preview")
    def preview_institution_draft(review_id: str) -> Any:
        from apemap.review.gui import _payload_from_form

        data, payload = None, None
        try:
            data = draft_data(
                review_id, request.form.get("institution_generation_token", "")
            )
            payload = _payload_from_form("manual_institution")
            check_revisions(data)
            if (
                request.form.get("action", "accept") != "accept"
                or request.form.get("supersedes", "").strip()
            ):
                raise ValueError(
                    "This workflow previews a new institution definition; use the school review for other outcomes"
                )
            if request.form.get("institution_sources_confirmed") != "yes":
                raise ValueError(
                    "Inspect and confirm the cited institution facts before previewing"
                )
            payload.pop("institution_fact_evidence_refs", None)
            payload.pop("institution_evidence_review_id", None)
            fields = {
                field: {
                    "value": payload[column],
                    "evidence_ids": sorted(
                        request.form.getlist("institution_sources_" + field)
                    ),
                }
                for field, column in FIELDS.items()
                if payload.get(column) is not None
                and not (
                    field == "status"
                    and payload[column] == "unknown"
                    and not request.form.getlist("institution_sources_" + field)
                )
            }
            payload["institution_draft_provenance"] = {
                "review_id": review_id,
                "provider": data["provider"],
                "model": data["model"],
                "generated_at": data["result"]["generated_at"],
                "fields": fields,
            }
            validate_draft_provenance(payload, service.evidence_records())
            if payload.get("postcode"):
                raise ValueError(
                    "Leave postcode blank in this evidence workflow; postcode requires separate manual source research"
                )
            facts = {
                field: payload[column]
                for field, column in FIELDS.items()
                if payload.get(column) is not None
            }
            reference = manual_institution_reference(facts, definitions())
            if payload.get("institution_ref") != reference:
                raise ValueError(
                    f"Reviewed institution identity requires reference {reference}"
                )
            preview = service.prepare(
                institution_review_id(reference),
                "accept",
                payload,
                source_url=request.form.get("source_url", ""),
                reviewer=request.form.get("reviewer") or None,
                notes=request.form.get("notes", ""),
            )
            check_revisions(data)
            return draft_page(data, payload=payload, preview=preview)
        except (OSError, ValueError) as exc:
            status = 409 if isinstance(exc, StaleReviewError) else 400
            if data is not None:
                return draft_page(data, payload=payload, error=str(exc)), status
            return render_template("error.html", message=str(exc)), status

    @app.post("/items/<path:review_id>/institution-draft-save")
    def save_institution_draft(review_id: str) -> Any:
        data, preview = None, None
        try:
            data = draft_data(
                review_id, request.form.get("institution_generation_token", "")
            )
            preview = read(request.form.get("preview_token", ""))
            event = preview["event"]
            if (
                event.get("entity_type") != "manual_institution"
                or event["payload"]
                .get("institution_draft_provenance", {})
                .get("review_id")
                != review_id
            ):
                raise ValueError("Institution preview belongs to another school")
            check_revisions(data)
            service.save(preview)
            g.pop("institution_resolver", None)
            return return_to_school(
                review_id, data["return_token"], event["payload"]["institution_ref"]
            )
        except (OSError, ValueError, KeyError) as exc:
            if data is not None:
                payload = preview.get("event", {}).get("payload") if preview else None
                if preview and preview.get("event"):
                    data["saved_form"] = {
                        key: preview["event"].get(key, "")
                        for key in ("source_url", "reviewer", "notes")
                    }
                return draft_page(data, payload=payload, error=str(exc)), 409
            return render_template("error.html", message=str(exc)), 400
