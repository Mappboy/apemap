"""Command-line handoff and explicit approval for guided research batches."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from apemap.review.research_batches import ResearchBatches

batch_app = typer.Typer(
    help="Persistent guided research with sampled member and full school review."
)


def _batches(ctx: typer.Context) -> ResearchBatches:
    from apemap.review.cli import _service

    return ResearchBatches(_service(ctx))


@batch_app.command("prepare")
def prepare_cmd(
    ctx: typer.Context,
    batch_id: str,
    kind: Annotated[str, typer.Option()],
    limit: int = 10,
) -> None:
    from apemap.review.cli import _run

    _run(lambda: _batches(ctx).prepare(batch_id, kind, limit))


@batch_app.command("import")
def import_cmd(ctx: typer.Context, batch_id: str, results: Path) -> None:
    from apemap.review.cli import _run

    _run(
        lambda: _batches(ctx).import_results(batch_id, json.loads(results.read_bytes()))
    )


@batch_app.command("show")
def show_cmd(ctx: typer.Context, batch_id: str) -> None:
    from apemap.review.cli import _run

    _run(lambda: _batches(ctx).show(batch_id))


@batch_app.command("retry-tasks")
def retry_cmd(ctx: typer.Context, batch_id: str) -> None:
    from apemap.review.cli import _run

    _run(lambda: _batches(ctx).retry_tasks(batch_id))


@batch_app.command("list")
def list_cmd(ctx: typer.Context) -> None:
    from apemap.review.cli import _run

    _run(lambda: _batches(ctx).list_batches())


@batch_app.command("review")
def review_cmd(
    ctx: typer.Context,
    batch_id: str,
    proposal_id: str,
    action: str,
    inspected: Annotated[
        Path,
        typer.Option(
            help="JSON object mapping every evidence ID to supports/contradicts/contextual/unavailable."
        ),
    ],
    reviewer: str | None = None,
    notes: str = "",
    identity_error: bool = False,
    supersedes: Annotated[list[str] | None, typer.Option()] = None,
) -> None:
    from apemap.review.cli import _run

    _run(
        lambda: _batches(ctx).record_review(
            batch_id,
            proposal_id,
            action,
            json.loads(inspected.read_bytes()),
            reviewer=reviewer,
            notes=notes,
            identity_error=identity_error,
            supersedes=supersedes,
        )
    )


@batch_app.command("retain")
def retain_cmd(
    ctx: typer.Context, batch_id: str, approve_sampled: bool = False
) -> None:
    from apemap.review.cli import _run

    _run(lambda: _batches(ctx).retain(batch_id, approve_sampled=approve_sampled))


@batch_app.command("preview")
def preview_cmd(
    ctx: typer.Context,
    batch_id: str,
    out: Annotated[Path, typer.Option()],
    approve_sampled: bool = False,
    reviewer: str | None = None,
) -> None:
    from apemap.review.cli import _run

    _run(
        lambda: _batches(ctx).preview(
            batch_id, out, approve_sampled=approve_sampled, reviewer=reviewer
        )
    )


@batch_app.command("apply")
def apply_cmd(
    ctx: typer.Context,
    batch_id: str,
    preview: Path,
    approve: Annotated[str, typer.Option(help="SHA-256 of the inspected preview.")],
) -> None:
    from apemap.review.cli import _run

    _run(lambda: _batches(ctx).apply(batch_id, preview, approve))


@batch_app.command("revalidate")
def revalidate_cmd(
    ctx: typer.Context,
    batch_id: str,
    original_external_dir: Annotated[Path, typer.Option()],
    out: Annotated[Path, typer.Option()],
    approve_sampled: bool = False,
    reviewer: str | None = None,
) -> None:
    from apemap.review.cli import _run

    _run(
        lambda: _batches(ctx).revalidate(
            batch_id,
            original_external_dir=original_external_dir,
            destination=out,
            approve_sampled=approve_sampled,
            reviewer=reviewer,
        )
    )
