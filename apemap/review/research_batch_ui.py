"""Focused batch review using the same operations as the CLI."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from flask import Flask, abort, redirect, render_template, request, url_for

from apemap.review.research_batches import ResearchBatches, _digest
from apemap.review.service import ReviewService
from apemap.review.store import StaleReviewError


FILTERS = {
    "all": "All proposals",
    "unreviewed": "Unreviewed",
    "sample": "Sample items",
    "individual": "Individual review required",
    "accept": "Accepted",
    "reject": "Rejected",
    "research": "Needs research",
    "applied": "Applied",
}


def _rows(state: dict[str, Any]) -> list[dict[str, Any]]:
    tasks = {task["review_id"]: task for task in state["tasks"]}
    rows = []
    for proposal in state["proposals"]:
        task = tasks[proposal["review_id"]]
        context = task["context"]
        review = state["reviews"].get(proposal["proposal_id"])
        applied = state.get("applied_proposals", {}).get(proposal["proposal_id"], [])
        status = "applied" if applied else review["action"] if review else "unreviewed"
        rows.append(
            {
                "proposal": proposal,
                "task": task,
                "name": (
                    context["member"]["display_name"]
                    if state["manifest"]["kind"] == "members"
                    else context["assertion"]["recorded_school_name"]
                ),
                "review": review,
                "status": status,
                "status_label": FILTERS[status],
                "sample": proposal["proposal_id"] in state["sample"],
                "applied": applied,
                "mapping_problems": ResearchBatches._mapping_problems(state, proposal),
                "eligibility_reasons": state.get("current_eligibility", {}).get(
                    proposal["proposal_id"], proposal["eligibility_reasons"]
                ),
            }
        )
    return rows


def _summary(state: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {key: sum(row["status"] == key for row in rows) for key in FILTERS}
    sample_reviewed = sum(row["sample"] and row["review"] is not None for row in rows)
    sample_accepted = sum(
        bool(row["sample"] and row["review"] and row["review"]["action"] == "accept")
        for row in rows
    )
    return {
        **counts,
        "researched": len(rows),
        "reviewed": sum(row["review"] is not None for row in rows),
        "sample_reviewed": sample_reviewed,
        "sample_accepted": sample_accepted,
        "case_count": state["manifest"]["case_count"],
    }


def register_batch_routes(app: Flask, service: ReviewService) -> None:
    batches = ResearchBatches(service)

    def scope() -> str:
        return (
            "sampled"
            if request.values.get("scope") == "sampled"
            or request.form.get("approve_sampled") == "yes"
            else "individual"
        )

    def filtered_rows(
        rows: list[dict[str, Any]], filter_name: str
    ) -> list[dict[str, Any]]:
        if filter_name == "sample":
            return [row for row in rows if row["sample"]]
        if filter_name == "individual":
            return [
                row for row in rows if row["eligibility_reasons"] and not row["applied"]
            ]
        return (
            rows
            if filter_name == "all"
            else [row for row in rows if row["status"] == filter_name]
        )

    def location(batch_id: str, proposal_id: str = "", **kwargs: Any) -> str:
        return url_for(
            "research_batch_detail",
            batch_id=batch_id,
            proposal=proposal_id or request.values.get("proposal", ""),
            filter=request.values.get("filter", "all"),
            scope=scope(),
            **kwargs,
        )

    def listing(error: str = "") -> str:
        saved = []
        for manifest in batches.list_batches():
            try:
                state = batches.show(manifest["batch_id"])
                saved.append(
                    {
                        "manifest": manifest,
                        "summary": _summary(state, _rows(state)),
                        "applications": state.get("applications", []),
                        "blockers": state.get("sample_approval_blockers", []),
                    }
                )
            except (OSError, ValueError, KeyError) as exc:
                saved.append({"manifest": manifest, "error": str(exc)})
        return render_template(
            "research-batches.html",
            batches=saved,
            operation_error=error,
            draft=request.form,
        )

    def workspace(
        batch_id: str,
        *,
        proposal_id: str = "",
        operation_error: str = "",
        review_error: str = "",
        draft_review: dict[str, Any] | None = None,
        preview_name: str = "",
        notice: str = "",
        load_preview: bool = True,
    ) -> str:
        state = batches.show(batch_id)
        rows = _rows(state)
        filter_name = request.values.get("filter", "all")
        if filter_name not in FILTERS:
            filter_name = "all"
        visible = filtered_rows(rows, filter_name)
        selected_id = proposal_id or request.values.get("proposal", "")
        selected = next(
            (row for row in rows if row["proposal"]["proposal_id"] == selected_id), None
        )
        if selected_id and selected is None:
            abort(404)
        if selected is None and visible:
            selected = next(
                (row for row in visible if row["status"] == "unreviewed"), visible[0]
            )
        navigation = visible if selected in visible else rows
        position = navigation.index(selected) if selected else -1
        preview = None
        name = preview_name or request.values.get("preview", "")
        if name and load_preview:
            try:
                path = preview_path(batch_id, name)
                raw = path.read_bytes()
                value = json.loads(raw)
                binding = value.get("research_batch", {})
                if (
                    binding.get("batch_id") != batch_id
                    or binding.get("content_sha256") != state["content_sha256"]
                    or binding.get("review_revision") != state["review_revision"]
                ):
                    raise StaleReviewError(
                        "Research contents or reviews changed; preview again"
                    )
                saved = {
                    event.decision_id: event.to_dict()
                    for event in service.events(allow_conflicts=True)
                }
                applied = bool(value["events"]) and all(
                    saved.get(event["decision_id"]) == event
                    for event in value["events"]
                )
                if not applied:
                    if (
                        value.get("revision") != service.review_revision()
                        or value.get("source_revision") != service.source_revision()
                    ):
                        raise StaleReviewError(
                            "Sources or decision history changed; prepare a fresh exact preview"
                        )
                    batches.validate_preview(value)
                sha256 = hashlib.sha256(raw).hexdigest()
                preview = {
                    "preview": value,
                    "sha256": sha256,
                    "applied": applied,
                    "receipt_saved": any(
                        receipt["approval_sha256"] == sha256
                        for receipt in state.get("applications", [])
                    ),
                }
            except (OSError, ValueError, KeyError) as exc:
                operation_error = operation_error or str(exc)
        return render_template(
            "research-batch.html",
            batch=state,
            rows=visible,
            all_rows=rows,
            summary=_summary(state, rows),
            selected=selected,
            previous=navigation[position - 1] if position > 0 else None,
            following=navigation[position + 1]
            if 0 <= position < len(navigation) - 1
            else None,
            position=position + 1,
            navigation_count=len(navigation),
            filter_name=filter_name,
            filters=FILTERS,
            scope=scope(),
            approval_state=state.get("approval_state", {}).get(scope(), {}),
            binding=_digest([state["content_sha256"], state["review_revision"]]),
            preview=preview,
            preview_name=name,
            operation_error=operation_error,
            review_error=review_error,
            draft_review=draft_review,
            notice=notice or request.args.get("notice", ""),
            original_external_dir=request.values.get("original_external_dir", ""),
        )

    def failure(batch_id: str, exc: Exception, **kwargs: Any) -> tuple[str, int]:
        return workspace(batch_id, operation_error=str(exc), **kwargs), (
            409 if isinstance(exc, StaleReviewError) else 400
        )

    def preview_path(batch_id: str, name: str) -> Path:
        if not name or Path(name).name != name or not name.endswith(".json"):
            raise ValueError("Invalid preview file")
        return batches.path(batch_id) / "previews" / name

    @app.context_processor
    def research_navigation() -> dict[str, bool]:
        return {"research_batches_enabled": True}

    @app.template_filter("source_host")
    def source_host(value: str) -> str:
        return urlsplit(value).hostname or "Source"

    @app.get("/research-batches")
    def research_batch_list() -> str:
        return listing()

    @app.post("/research-batches/prepare")
    def research_batch_prepare() -> Any:
        try:
            batch_id = request.form.get("batch_id", "")
            batches.prepare(
                batch_id,
                request.form.get("kind", ""),
                int(request.form.get("limit", "10")),
            )
        except (OSError, ValueError, KeyError) as exc:
            return listing(str(exc)), 400
        return redirect(location(batch_id, notice="Research tasks prepared."), code=303)

    @app.get("/research-batches/<batch_id>")
    def research_batch_detail(batch_id: str) -> Any:
        try:
            return workspace(batch_id)
        except FileNotFoundError:
            abort(404)
        except (OSError, ValueError, KeyError) as exc:
            return render_template("error.html", message=str(exc)), 400

    @app.post("/research-batches/<batch_id>/import")
    def research_batch_import(batch_id: str) -> Any:
        try:
            upload = request.files.get("results")
            if upload is None:
                raise ValueError("Choose a research result JSON file")
            batches.import_results(batch_id, json.loads(upload.read()))
        except (OSError, ValueError, KeyError) as exc:
            return failure(batch_id, exc)
        return redirect(
            location(batch_id, notice="Research proposals imported."), code=303
        )

    @app.post("/research-batches/<batch_id>/review/<proposal_id>")
    def research_batch_review(batch_id: str, proposal_id: str) -> Any:
        state = batches.show(batch_id)
        proposal = next(
            (p for p in state["proposals"] if p["proposal_id"] == proposal_id), None
        )
        if proposal is None:
            abort(404)
        inspected = {
            evidence["evidence_id"]: request.form.get(
                "disposition_" + evidence["evidence_id"], ""
            )
            for evidence in proposal["evidence"]
            if request.form.get("inspected_" + evidence["evidence_id"]) == "yes"
        }
        draft: dict[str, Any] = {
            "proposal_id": proposal_id,
            "action": request.form.get("action", ""),
            "inspected": inspected,
            "dispositions": {
                evidence["evidence_id"]: request.form.get(
                    "disposition_" + evidence["evidence_id"], ""
                )
                for evidence in proposal["evidence"]
            },
            "notes": request.form.get("notes", ""),
            "identity_error": request.form.get("identity_error") == "yes",
            "supersedes": request.form.getlist("supersedes"),
        }
        if proposal.get("manual_candidate"):
            draft["institution_action"] = request.form.get(
                "institution_action", "create_map"
            )
        try:
            if not request.form.get("binding"):
                raise ValueError("Reload the batch before reviewing")
            batches.record_review(
                batch_id,
                proposal_id,
                draft["action"],
                inspected,
                notes=draft["notes"],
                identity_error=draft["identity_error"],
                supersedes=draft["supersedes"],
                institution_action=draft.get("institution_action"),
                expected_revision=request.form["binding"],
            )
        except (OSError, ValueError, KeyError) as exc:
            return workspace(
                batch_id,
                proposal_id=proposal_id,
                review_error=str(exc),
                draft_review=draft,
                load_preview=False,
            ), 409 if isinstance(exc, StaleReviewError) else 400
        following_id = proposal_id
        if request.form.get("advance") == "next":
            filter_name = request.form.get("filter", "all")
            original_rows = filtered_rows(
                _rows(state), filter_name if filter_name in FILTERS else "all"
            )
            ids = [row["proposal"]["proposal_id"] for row in original_rows]
            if proposal_id in ids and ids.index(proposal_id) + 1 < len(ids):
                following_id = ids[ids.index(proposal_id) + 1]
        return redirect(
            location(batch_id, following_id, notice="Individual review saved."),
            code=303,
        )

    @app.post("/research-batches/<batch_id>/retain")
    def research_batch_retain(batch_id: str) -> Any:
        try:
            result = batches.retain(batch_id, approve_sampled=scope() == "sampled")
        except (OSError, ValueError, KeyError) as exc:
            return failure(batch_id, exc)
        return redirect(
            location(
                batch_id, notice=f"Retained {result['retained']} evidence records."
            ),
            code=303,
        )

    @app.post("/research-batches/<batch_id>/preview")
    def research_batch_preview(batch_id: str) -> Any:
        try:
            directory = batches.path(batch_id) / "previews"
            directory.mkdir(exist_ok=True)
            name = str(uuid4()) + ".json"
            if request.path.endswith("/revalidate"):
                original = request.form.get("original_external_dir", "").strip()
                if not original:
                    raise ValueError(
                        "Choose the original external source directory before revalidation"
                    )
                batches.revalidate(
                    batch_id,
                    original_external_dir=Path(original),
                    destination=directory / name,
                    approve_sampled=scope() == "sampled",
                )
            else:
                batches.preview(
                    batch_id, directory / name, approve_sampled=scope() == "sampled"
                )
        except (OSError, ValueError, KeyError) as exc:
            return failure(batch_id, exc, load_preview=False)
        return redirect(
            location(
                batch_id,
                preview=name,
                original_external_dir=request.form.get("original_external_dir", ""),
            ),
            code=303,
        )

    @app.post("/research-batches/<batch_id>/revalidate")
    def research_batch_revalidate(batch_id: str) -> Any:
        return research_batch_preview(batch_id)

    @app.post("/research-batches/<batch_id>/apply")
    def research_batch_apply(batch_id: str) -> Any:
        try:
            result = batches.apply(
                batch_id,
                preview_path(batch_id, request.form.get("preview_name", "")),
                request.form.get("approval", ""),
            )
        except (OSError, ValueError, KeyError) as exc:
            return failure(batch_id, exc, load_preview=False)
        if result.get("receipt_error"):
            return workspace(
                batch_id,
                operation_error=result["receipt_error"],
                preview_name=request.form["preview_name"],
            )
        return redirect(
            location(
                batch_id,
                notice="Decisions applied. Measured results are recorded below.",
            ),
            code=303,
        )
