"""CLI entrypoints for the shared historical recipe."""

from pathlib import Path
from typing import Annotated

import typer

from apemap.release.recipe import (
    build_recipe_release,
    compare_recipe_releases,
    bundle_recipe_inputs,
    pin_recipe,
    restore_recipe_inputs,
)

recipe_app = typer.Typer(help="Pin, provision and build the historical release recipe.")


@recipe_app.command("compare")
def compare_cmd(first: Path, second: Path) -> None:
    """Require matching release metadata and bytes from equivalent recipe builds."""
    try:
        compare_recipe_releases(first, second)
    except (OSError, ValueError) as err:
        raise typer.BadParameter(str(err)) from err
    typer.echo("Recipe metadata and artifact hashes match")


@recipe_app.command("pin")
def pin_cmd(
    template: Annotated[Path, typer.Option("--template")],
    output: Annotated[Path, typer.Option("--output")],
) -> None:
    """Pin the current review/source hashes into a fresh recipe file."""
    try:
        pin_recipe(template, output)
    except (OSError, ValueError) as err:
        raise typer.BadParameter(str(err)) from err
    typer.echo(f"Pinned recipe: {output}")


@recipe_app.command("inputs-bundle")
def bundle_cmd(
    recipe: Annotated[Path, typer.Option("--recipe")],
    archive: Annotated[Path, typer.Option("--archive")],
) -> None:
    """Create an immutable, deterministic bundle of the pinned raw inputs."""
    try:
        bundle_recipe_inputs(recipe, archive)
    except (OSError, ValueError) as err:
        raise typer.BadParameter(str(err)) from err
    typer.echo(f"Bundled inputs: {archive}")


@recipe_app.command("inputs-restore")
def restore_cmd(
    recipe: Annotated[Path, typer.Option("--recipe")],
    archive: Annotated[Path | None, typer.Option("--archive")] = None,
    archive_url: Annotated[str | None, typer.Option("--archive-url")] = None,
) -> None:
    """Restore only the exact pinned files from a local or HTTPS input bundle."""
    try:
        restore_recipe_inputs(recipe, archive=archive, archive_url=archive_url)
    except (OSError, ValueError) as err:
        raise typer.BadParameter(str(err)) from err
    typer.echo("Restored and verified recipe inputs")


@recipe_app.command("build")
def build_cmd(
    recipe: Annotated[Path, typer.Option("--recipe")],
    db_path: Annotated[Path, typer.Option("--db-path")],
    output_dir: Annotated[Path, typer.Option("--output-dir")],
    version: Annotated[str, typer.Option("--version")],
) -> None:
    """Build and strictly verify a new dataset offline from an explicit recipe."""
    try:
        build_recipe_release(recipe, db_path, output_dir, version)
    except (OSError, ValueError, RuntimeError) as err:
        raise typer.BadParameter(str(err)) from err
    typer.echo(f"Release v{version} built and strictly verified: {output_dir}")
