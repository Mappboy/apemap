"""Typer adapters for the shared review service."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Annotated, Any, Callable

import typer

from apemap.constants import PROJECT_ROOT
from apemap.review.model import (
    DEFAULT_LOG_PATH,
    education_review_id,
    institution_review_id,
    parse_events,
)
from apemap.review.service import ReviewService, default_reviewer
from apemap.review.store import append_events, check_append_only, log_revision

review_app = typer.Typer(
    help="Append-only research decisions and disposable candidates.",
    no_args_is_help=True,
)


@review_app.callback()
def review_options(
    ctx: typer.Context,
    log_path: Annotated[
        Path, typer.Option(help="Git-tracked decision log.")
    ] = DEFAULT_LOG_PATH,
    db_path: Annotated[
        Path | None, typer.Option(help="Disposable review DuckDB database.")
    ] = None,
    external_dir: Annotated[
        Path | None, typer.Option(help="Pinned ACARA register directory.")
    ] = None,
) -> None:
    ctx.obj = ReviewService(
        log_path=log_path, db_path=db_path, external_dir=external_dir
    )


def _service(ctx: typer.Context) -> ReviewService:
    if not isinstance(ctx.obj, ReviewService):
        raise ValueError("Review service context is missing")
    return ctx.obj


def _run(action: Callable[[], Any]) -> None:
    try:
        value = action()
        typer.echo(
            json.dumps(
                value, ensure_ascii=False, indent=2, default=str, allow_nan=False
            )
        )
    except (OSError, ValueError, RuntimeError) as exc:
        typer.echo(f"Review failed: {exc}", err=True)
        raise typer.Exit(1) from exc


def _payload(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Payload file must contain a JSON object")
    return value


def git_baseline(path: Path, base: str) -> bytes:
    relative = path.resolve().relative_to(PROJECT_ROOT).as_posix()
    command = ["git", "-c", f"safe.directory={PROJECT_ROOT.as_posix()}"]
    revision = subprocess.run(
        command + ["rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
    )
    if revision.returncode:
        raise ValueError(f"Invalid baseline commit: {base}")
    result = subprocess.run(
        command + ["show", f"{revision.stdout.decode().strip()}:{relative}"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
    )
    return result.stdout if result.returncode == 0 else b""


@review_app.command("build")
def build_cmd(
    ctx: typer.Context,
    inputs_manifest: Path | None = None,
    output_dir: Path | None = None,
    db_path: Path | None = None,
    external_dir: Path | None = None,
) -> None:
    """Regenerate offline candidate data; never write decisions."""
    service = _service(ctx)
    if db_path is not None:
        service.db_path = db_path
    if external_dir is not None:
        service.external_dir = external_dir
    _run(lambda: service.build(inputs_manifest=inputs_manifest, output_dir=output_dir))


@review_app.command("list")
def list_cmd(
    ctx: typer.Context,
    entity: Annotated[str | None, typer.Argument()] = None,
    status: str | None = None,
    parliament: int | None = None,
    search: str | None = None,
) -> None:
    """List candidates, including decisions whose source candidate disappeared."""
    _run(lambda: _service(ctx).candidates(entity, status, parliament, search))


@review_app.command("show")
def show_cmd(ctx: typer.Context, review_id: str) -> None:
    _run(lambda: _service(ctx).show(review_id))


@review_app.command("history")
def history_cmd(ctx: typer.Context, review_id: str) -> None:
    _run(lambda: _service(ctx).history(review_id))


@review_app.command("status")
def status_cmd(ctx: typer.Context) -> None:
    _run(lambda: _service(ctx).status())


@review_app.command("check")
def check_cmd(
    ctx: typer.Context,
    base: str | None = None,
    log_path: Path | None = None,
    external_dir: Path | None = None,
    baseline_log: Path | None = None,
) -> None:
    """Validate references and optionally verify append-only history against Git."""

    def check() -> dict[str, Any]:
        service = _service(ctx)
        if log_path:
            service.log_path = log_path
        if external_dir:
            service.external_dir = external_dir
        result = service.check()
        if base or baseline_log:
            baseline = (
                baseline_log.read_bytes()
                if baseline_log
                else git_baseline(service.log_path, base or "HEAD")
            )
            check_append_only(baseline, service.log_path.read_bytes())
            result["append_only"] = True
        return result

    _run(check)


@review_app.command("diff")
def diff_cmd(
    ctx: typer.Context, base: str = "HEAD", baseline_log: Path | None = None
) -> None:
    def diff() -> dict[str, Any]:
        service = _service(ctx)
        baseline = (
            baseline_log.read_bytes()
            if baseline_log
            else git_baseline(service.log_path, base)
        )
        return service.semantic_diff(parse_events(baseline), service.events())

    _run(diff)


def _record(
    ctx: typer.Context,
    review_id: str,
    action: str,
    payload_path: Path | None,
    source: str,
    reviewer: str | None,
    note: str,
    dry_run: bool,
    **values: Any,
) -> Any:
    payload = _payload(payload_path)
    for key, value in values.items():
        if value is not None:
            payload[key] = value
    service = _service(ctx)
    preview = service.prepare(
        review_id, action, payload, source_url=source, reviewer=reviewer, notes=note
    )
    return preview if dry_run else service.save(preview).to_dict()


@review_app.command("accept")
def accept_cmd(
    ctx: typer.Context,
    review_id: str,
    payload: Path | None = None,
    source: str = "",
    reviewer: str | None = None,
    note: str = "",
    dry_run: bool = False,
    acara_id: str | None = None,
    institution_ref: str | None = None,
    school: str | None = None,
    attended_status: str | None = None,
    confidence: str | None = None,
    retrieved_at: str | None = None,
    relationship_type: str | None = None,
) -> None:
    """Accept explicit facts, using a JSON payload for complex correction types."""

    def accept() -> Any:
        values: dict[str, Any] = {}
        supplied = _payload(payload)
        reference = f"acara:{acara_id}" if acara_id else institution_ref
        if reference:
            values["institution_ref"] = reference
        if review_id.startswith("school:"):
            values["relationship_type"] = relationship_type or supplied.get(
                "relationship_type", "alias"
            )
            if school:
                values["recorded_name"] = school
        if review_id.startswith("education:"):
            if relationship_type:
                values["relationship_type"] = relationship_type
            values.update(
                attended_status=attended_status
                or supplied.get("attended_status", "attended_unspecified"),
                confidence=confidence or supplied.get("confidence", "verified"),
            )
            if school:
                values["recorded_school_name"] = school
            if retrieved_at:
                values["retrieved_at"] = retrieved_at
        return _record(
            ctx, review_id, "accept", payload, source, reviewer, note, dry_run, **values
        )

    _run(accept)


@review_app.command("map-education")
def map_education_cmd(
    ctx: typer.Context,
    review_id: str,
    institution_ref: Annotated[str, typer.Option()],
    relationship_type: Annotated[str, typer.Option()],
    source: Annotated[str, typer.Option()],
    payload: Path | None = None,
    reviewer: str | None = None,
    note: str = "",
    dry_run: bool = False,
) -> None:
    """Resolve one existing attendance assertion without replacing its evidence."""
    if not review_id.startswith("education:"):
        raise typer.BadParameter("Use an education:<aph-id>:<school-digest> review ID")
    _run(
        lambda: _record(
            ctx,
            review_id,
            "map",
            payload,
            source,
            reviewer,
            note,
            dry_run,
            institution_ref=institution_ref,
            relationship_type=relationship_type,
        )
    )


@review_app.command("reject")
def reject_cmd(
    ctx: typer.Context,
    review_id: str,
    reason: Annotated[str, typer.Option()],
    payload: Path | None = None,
    reviewer: str | None = None,
    dry_run: bool = False,
) -> None:
    _run(
        lambda: _record(
            ctx, review_id, "reject", payload, "", reviewer, reason, dry_run
        )
    )


@review_app.command("research")
def research_cmd(
    ctx: typer.Context,
    review_id: str,
    note: Annotated[str, typer.Option()],
    payload: Path | None = None,
    reviewer: str | None = None,
    dry_run: bool = False,
) -> None:
    _run(
        lambda: _record(
            ctx, review_id, "research", payload, "", reviewer, note, dry_run
        )
    )


@review_app.command("supersede")
def supersede_cmd(
    ctx: typer.Context,
    decision_id: str,
    payload: Annotated[Path, typer.Option()],
    replacement_action: Annotated[str, typer.Option()],
    source: str = "",
    reviewer: str | None = None,
    note: str = "",
    also_supersede: Annotated[list[str] | None, typer.Option()] = None,
    dry_run: bool = False,
) -> None:
    def supersede() -> Any:
        service = _service(ctx)
        events = service.events(allow_conflicts=True)
        target = next(
            (event for event in events if event.decision_id == decision_id), None
        )
        if target is None:
            raise ValueError(f"Unknown decision_id {decision_id}")
        preview = service.prepare(
            target.review_id,
            "supersede",
            _payload(payload),
            source_url=source,
            reviewer=reviewer,
            notes=note,
            supersedes=[decision_id, *(also_supersede or [])],
            replacement_action=replacement_action,
        )
        return preview if dry_run else service.save(preview).to_dict()

    _run(supersede)


@review_app.command("add-education")
def add_education_cmd(
    ctx: typer.Context,
    aph_id: str,
    school: str,
    payload: Annotated[Path, typer.Option()],
    source: Annotated[str, typer.Option()],
    reviewer: str | None = None,
    note: str = "",
    dry_run: bool = False,
) -> None:
    _run(
        lambda: _record(
            ctx,
            education_review_id(aph_id, school),
            "accept",
            payload,
            source,
            reviewer,
            note,
            dry_run,
            aph_id=aph_id,
            recorded_school_name=school,
        )
    )


@review_app.command("add-institution")
def add_institution_cmd(
    ctx: typer.Context,
    institution_ref: str,
    payload: Annotated[Path, typer.Option()],
    source: Annotated[str, typer.Option()],
    reviewer: str | None = None,
    note: str = "",
    dry_run: bool = False,
) -> None:
    _run(
        lambda: _record(
            ctx,
            institution_review_id(institution_ref),
            "accept",
            payload,
            source,
            reviewer,
            note,
            dry_run,
            institution_ref=institution_ref,
        )
    )


@review_app.command("import")
def import_cmd(
    ctx: typer.Context,
    source: Annotated[
        Path | None,
        typer.Option(
            help="Explicit legacy review CSV; omit for archived reference migration."
        ),
    ] = None,
    legacy_dir: Path | None = None,
    reviewer: str | None = None,
    apply: bool = False,
    incomplete_as_research: bool = False,
) -> None:
    """Validate deterministic proposals; default to preview without writing."""

    def import_records() -> dict[str, Any]:
        from apemap.review.migration import (
            DEFAULT_LEGACY_DIR,
            migration_report,
            propose_legacy_migration,
            propose_review_csv_import,
        )

        service = _service(ctx)
        actor = reviewer or default_reviewer()
        if source:
            proposals, report = propose_review_csv_import(
                source, reviewer=actor, incomplete_as_research=incomplete_as_research
            )
        else:
            source_dir = legacy_dir or DEFAULT_LEGACY_DIR
            proposals, audit = propose_legacy_migration(
                source_dir, service.external_dir, reviewer=actor
            )
            report = migration_report(proposals, audit, source_dir)
        if apply and report.get("errors"):
            raise ValueError(
                "Import contains blocked rows; resolve their evidence or explicitly use --incomplete-as-research. "
                + json.dumps(report["errors"])
            )
        existing = {event.decision_id: event.to_dict() for event in service.events()}
        additions = []
        for event in proposals:
            if event.decision_id not in existing:
                additions.append(event)
        service.check(service.events() + additions)
        if apply:
            append_events(
                service.log_path,
                additions,
                expected_revision=log_revision(service.log_path),
                validator=lambda events: service.check(events),
            )
        return {
            "applied": apply,
            "new_events": len(additions),
            "proposals": [event.to_dict() for event in additions],
            "report": report,
        }

    _run(import_records)


@review_app.command("serve")
def serve_cmd(
    ctx: typer.Context,
    port: int = 8765,
    workers: Annotated[
        int, typer.Option(min=1, help="Local request worker threads.")
    ] = 4,
) -> None:
    """Serve the optional local GUI on loopback only."""
    if not 1 <= port <= 65535:
        raise typer.BadParameter("Port must be between 1 and 65535")
    try:
        from apemap.review.gui import serve
    except ImportError as exc:
        typer.echo("Install the optional GUI: uv sync --extra review-ui", err=True)
        raise typer.Exit(1) from exc
    serve(_service(ctx), port=port, workers=workers)
