"""Server-rendered batch review using the same operations as the CLI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from flask import Flask, abort, redirect, render_template, request, url_for

from apemap.review.research_batches import ResearchBatches, _digest
from apemap.review.service import ReviewService


def register_batch_routes(app: Flask, service: ReviewService) -> None:
    batches = ResearchBatches(service)

    @app.context_processor
    def research_navigation() -> dict[str, bool]:
        return {"research_batches_enabled": True}

    @app.get("/research-batches")
    def research_batch_list() -> str:
        return render_template("research-batches.html", batches=batches.list_batches())

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
            abort(400, str(exc))
        return redirect(url_for("research_batch_detail", batch_id=batch_id), code=303)

    @app.get("/research-batches/<batch_id>")
    def research_batch_detail(batch_id: str) -> str:
        try:
            state = batches.show(batch_id)
            binding = _digest([state["content_sha256"], state["review_revision"]])
            return render_template(
                "research-batch.html", batch=state, binding=binding, preview=None
            )
        except FileNotFoundError:
            abort(404)
        except (OSError, ValueError, KeyError) as exc:
            abort(400, str(exc))

    @app.post("/research-batches/<batch_id>/import")
    def research_batch_import(batch_id: str) -> Any:
        try:
            upload = request.files.get("results")
            if upload is None:
                raise ValueError("Choose a research result JSON file")
            batches.import_results(batch_id, json.loads(upload.read()))
        except (OSError, ValueError, KeyError) as exc:
            abort(400, str(exc))
        return redirect(url_for("research_batch_detail", batch_id=batch_id), code=303)

    @app.post("/research-batches/<batch_id>/review/<proposal_id>")
    def research_batch_review(batch_id: str, proposal_id: str) -> Any:
        try:
            state = batches.show(batch_id)
            if not request.form.get("binding"):
                raise ValueError("Reload the batch before reviewing")
            proposal = next(
                p for p in state["proposals"] if p["proposal_id"] == proposal_id
            )
            inspected = {
                evidence["evidence_id"]: request.form.get(
                    "disposition_" + evidence["evidence_id"], ""
                )
                for evidence in proposal["evidence"]
                if request.form.get("inspected_" + evidence["evidence_id"]) == "yes"
            }
            batches.record_review(
                batch_id,
                proposal_id,
                request.form.get("action", ""),
                inspected,
                notes=request.form.get("notes", ""),
                identity_error=request.form.get("identity_error") == "yes",
                supersedes=request.form.getlist("supersedes"),
                expected_revision=request.form.get("binding", ""),
            )
        except StopIteration:
            abort(404)
        except (OSError, ValueError, KeyError) as exc:
            abort(400, str(exc))
        return redirect(url_for("research_batch_detail", batch_id=batch_id), code=303)

    @app.post("/research-batches/<batch_id>/retain")
    def research_batch_retain(batch_id: str) -> Any:
        try:
            batches.retain(
                batch_id, approve_sampled=request.form.get("approve_sampled") == "yes"
            )
        except (OSError, ValueError, KeyError) as exc:
            abort(400, str(exc))
        return redirect(url_for("research_batch_detail", batch_id=batch_id), code=303)

    @app.post("/research-batches/<batch_id>/preview")
    def research_batch_preview(batch_id: str) -> str:
        try:
            directory = batches.path(batch_id) / "previews"
            directory.mkdir(exist_ok=True)
            name = str(uuid4()) + ".json"
            result = batches.preview(
                batch_id,
                directory / name,
                approve_sampled=request.form.get("approve_sampled") == "yes",
            )
            state = batches.show(batch_id)
            return render_template(
                "research-batch.html",
                batch=state,
                binding=_digest([state["content_sha256"], state["review_revision"]]),
                preview=result,
                preview_name=name,
            )
        except (OSError, ValueError, KeyError) as exc:
            abort(400, str(exc))

    @app.post("/research-batches/<batch_id>/apply")
    def research_batch_apply(batch_id: str) -> Any:
        try:
            name = request.form.get("preview_name", "")
            if Path(name).name != name or not name.endswith(".json"):
                raise ValueError("Invalid preview file")
            batches.apply(
                batch_id,
                batches.path(batch_id) / "previews" / name,
                request.form.get("approval", ""),
            )
        except (OSError, ValueError, KeyError) as exc:
            abort(400, str(exc))
        return redirect(url_for("research_batch_detail", batch_id=batch_id), code=303)
