"""APEMAP Command-Line Interface powered by Typer."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from apemap.constants import DATA_DIR, PROCESSED_DIR
from apemap.ingest.acara import run_acara_ingestion
from apemap.ingest.pipeline import run_aph_ingestion

app = typer.Typer(
    name="apemap",
    help="Australian Parliamentarians Education Map data and analytics CLI.",
    no_args_is_help=True,
)

ingest_app = typer.Typer(
    name="ingest",
    help="Ingestion pipelines for official data sources (APH, ACARA, etc.).",
    no_args_is_help=True,
)
app.add_typer(ingest_app, name="ingest")

console = Console()


def parse_parliament_args(raw: str) -> list[int]:
    """Parse parliament numbers from comma- or space-delimited string."""
    tokens = re.split(r"[\s,]+", raw.strip())
    parls: list[int] = []
    for tok in tokens:
        if not tok:
            continue
        try:
            val = int(tok)
            if val not in parls:
                parls.append(val)
        except ValueError:
            raise typer.BadParameter(f"Invalid parliament number: '{tok}'")
    if not parls:
        raise typer.BadParameter("At least one parliament number must be specified.")
    return sorted(parls)


@ingest_app.command(name="aph")
def ingest_aph(
    parliament: Annotated[
        str,
        typer.Option(
            "--parliament",
            "-p",
            help="Comma- or space-separated parliament numbers (e.g. '46,47,48').",
        ),
    ] = "46,47,48",
    refresh: Annotated[
        bool,
        typer.Option(
            "--refresh",
            help="Force re-fetching from live APH Handbook API instead of local disk cache.",
        ),
    ] = False,
    db_path: Annotated[
        Path | None,
        typer.Option(
            "--db-path",
            help="Path to DuckDB database file (defaults to data/aped.duckdb).",
        ),
    ] = None,
    export_parquet: Annotated[
        bool,
        typer.Option(
            "--export-parquet/--no-export-parquet",
            help="Export updated canonical tables to Parquet files.",
        ),
    ] = True,
    output_dir: Annotated[
        Path | None,
        typer.Option(
            "--output-dir",
            help="Directory to save generated CSV/JSON reports and Parquet exports.",
        ),
    ] = None,
) -> None:
    """Ingest parliamentarian demographics and secondary education records from APH."""
    parl_list = parse_parliament_args(parliament)
    effective_db_path = db_path or (DATA_DIR / "aped.duckdb")
    effective_out_dir = output_dir or PROCESSED_DIR

    console.print(
        f"[bold blue]Starting APH Ingestion[/bold blue] for Parliaments: "
        f"[green]{parl_list}[/green]"
    )
    if refresh:
        console.print("[yellow]Notice:[/yellow] Live refresh from APH API requested.")
    else:
        console.print("[dim]Using local disk cache if available.[/dim]")

    results = run_aph_ingestion(
        parliaments=parl_list,
        refresh=refresh,
        db_path=effective_db_path,
        export_parquet_files=export_parquet,
        output_dir=effective_out_dir,
    )

    console.print()
    console.print("[bold green]Ingestion Complete![/bold green]")
    console.print(
        f"Database: [cyan]{effective_db_path}[/cyan] | "
        f"Total Unique Members: {results['members_count']} | "
        f"Service Records: {results['service_count']} | "
        f"Institutions: {results['institutions_count']} | "
        f"Education Assertions: {results['education_count']}"
    )

    # Pretty-print coverage metrics table
    table = Table(title="Parliament Coverage & Gap Metrics")
    table.add_column("Parliament", justify="center", style="cyan")
    table.add_column("Total MPs", justify="right")
    table.add_column("Opening Day", justify="right")
    table.add_column("Current", justify="right")
    table.add_column("Matched Schools", justify="right", style="green")
    table.add_column("Unmatched", justify="right", style="red")
    table.add_column("Overseas", justify="right", style="magenta")
    table.add_column("Gov / Cath / Ind / Other", justify="center")

    cov = results["coverage_metrics"]
    for p in parl_list:
        p_cov = cov.get(p, {})
        secs = p_cov.get("sectors", {})
        sector_str = (
            f"{secs.get('Government', 0)} / "
            f"{secs.get('Catholic', 0)} / "
            f"{secs.get('Independent', 0)} / "
            f"{secs.get('Other', 0)}"
        )
        table.add_row(
            str(p),
            str(p_cov.get("total_parliamentarians", 0)),
            str(p_cov.get("opening_day_parliamentarians", 0)),
            str(p_cov.get("current_parliamentarians", 0)),
            str(p_cov.get("matched_schools", 0)),
            str(p_cov.get("unmatched_schools", 0)),
            str(p_cov.get("international_schools", 0)),
            sector_str,
        )

    console.print(table)
    console.print()
    console.print(
        f"Unmatched Schools Review File: [yellow]{results['unmatched_csv']}[/yellow]"
    )
    console.print(
        f"Coverage Metrics JSON: [yellow]{results['coverage_metrics_json']}[/yellow]"
    )
    if export_parquet and results.get("parquet_paths"):
        console.print(f"Parquet exports written to: [cyan]{effective_out_dir}[/cyan]")


@ingest_app.command(name="acara")
def ingest_acara(
    download: Annotated[
        bool,
        typer.Option(
            "--download/--no-download",
            help="Download latest official 2025 ACARA datasets from ACARA Data Access portal.",
        ),
    ] = True,
    longitudinal: Annotated[
        bool,
        typer.Option(
            "--longitudinal/--single-year",
            help="Download 2008-2025 longitudinal profile dataset or 2025 single-year profile.",
        ),
    ] = True,
    db_path: Annotated[
        Path | None,
        typer.Option(
            "--db-path",
            help="Path to DuckDB database file (defaults to data/aped.duckdb).",
        ),
    ] = None,
    export_parquet: Annotated[
        bool,
        typer.Option(
            "--export-parquet/--no-export-parquet",
            help="Export updated canonical tables to Parquet files.",
        ),
    ] = True,
    output_dir: Annotated[
        Path | None,
        typer.Option(
            "--output-dir",
            help="Directory to save Parquet exports.",
        ),
    ] = None,
) -> None:
    """Ingest official 2025 ACARA datasets and isolate 2021 historical finances."""
    effective_db_path = db_path or (DATA_DIR / "aped.duckdb")
    effective_out_dir = output_dir or PROCESSED_DIR

    console.print(
        "[bold blue]Starting ACARA Ingestion Pipeline[/bold blue] "
        f"(download={download}, longitudinal={longitudinal})"
    )
    with console.status(
        "[bold green]Processing ACARA datasets and migrating finances..."
    ):
        results = run_acara_ingestion(
            download_latest=download,
            use_longitudinal=longitudinal,
            db_path=effective_db_path,
            export_parquet_files=export_parquet,
            output_dir=effective_out_dir,
        )

    table = Table(
        title="ACARA Ingestion & Finance Isolation Summary",
        header_style="bold magenta",
    )
    table.add_column("Metric", style="cyan")
    table.add_column("Count / Status", justify="right", style="green")

    table.add_row(
        "Canonical Institutions Loaded", f"{results['institutions_loaded']:,}"
    )
    table.add_row("School Snapshots Loaded", f"{results['school_snapshots_loaded']:,}")
    table.add_row(
        "2021 Historical Finances Migrated",
        f"{results['school_finances_2021_migrated']:,}",
    )
    table.add_row(
        "Parquet Exports Generated",
        "Yes" if results.get("parquet_exported") else "No",
    )

    console.print()
    console.print(table)
    console.print()
    console.print(f"Database: [green]{effective_db_path}[/green]")
    if export_parquet:
        console.print(f"Parquet exports written to: [cyan]{effective_out_dir}[/cyan]")


def main() -> None:
    """Entrypoint function for console script."""
    app()


if __name__ == "__main__":
    main()
