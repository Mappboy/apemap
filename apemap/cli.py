"""APEMAP Command-Line Interface powered by Typer."""

from __future__ import annotations

from apemap.constants import supported_parliaments

import json
import re
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from apemap.analysis import (
    backtest_finance_benchmarks,
    compute_funding_summary,
    compute_parliament_demographics,
    compute_sector_summary,
    export_analysis_report,
)
from apemap.constants import (
    DATA_DIR,
    DEFAULT_WIKIMEDIA_TIMEOUT,
    PARLIAMENT_METADATA,
    PROCESSED_DIR,
    RAW_WIKIMEDIA_DIR,
)
from apemap.db import (
    export_to_parquet,
    get_connection,
    init_schema,
    migrate_historical_finances,
)
from apemap.export import export_all_artifacts, validate_source_snapshot_dates
from apemap.ingest.abs import run_abs_ingestion
from apemap.ingest.acara import ingest_school_finances, run_acara_ingestion
from apemap.ingest.aec import run_aec_ingestion
from apemap.ingest.funding import ingest_all_funding
from apemap.ingest.pipeline import run_aph_ingestion
from apemap.ingest.wikimedia import run_wikimedia_enrichment
from apemap.inputs import (
    DEFAULT_MANIFEST_PATH,
    preflight_offline_inputs,
    restore_inputs,
    verify_inputs_manifest,
)
from apemap.release import (
    build_release,
    diff_releases,
    verify_release,
)
from apemap.review.cli import review_app
from apemap.release.recipe_cli import recipe_app
from apemap.validate import validate_database


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

app.add_typer(review_app, name="review")

inputs_app = typer.Typer(
    name="inputs",
    help="Source input archive and manifest verification commands.",
    no_args_is_help=True,
)
app.add_typer(inputs_app, name="inputs")


@inputs_app.command(name="restore")
def restore_inputs_cmd(
    manifest_path: Annotated[
        Path, typer.Option("--manifest", "-m", help="Pinned input manifest.")
    ] = DEFAULT_MANIFEST_PATH,
    archive_path: Annotated[
        Path | None,
        typer.Option(
            "--archive", help="Local bundle; omit to download the pinned URL."
        ),
    ] = None,
) -> None:
    """Restore ignored raw inputs after verifying the pinned archive and members."""
    try:
        restored = restore_inputs(manifest_path, archive_path=archive_path)
    except (OSError, ValueError) as err:
        console.print(f"[bold red]Input restoration failed:[/bold red] {err}")
        raise typer.Exit(code=1) from err
    console.print(f"[green]Restored {len(restored)} verified raw input files.[/green]")


@inputs_app.command(name="verify")
def verify_inputs_cmd(
    manifest_path: Annotated[
        Path | None,
        typer.Option(
            "--manifest",
            "-m",
            help="Path to inputs-manifest.json (defaults to data/inputs-manifest.json).",
        ),
    ] = None,
) -> None:
    """Verify input files against the source inputs manifest."""
    target_manifest = manifest_path or DEFAULT_MANIFEST_PATH
    if not target_manifest.exists():
        console.print(
            f"[bold red]Inputs manifest not found:[/bold red] {target_manifest}"
        )
        raise typer.Exit(code=1)

    console.print(f"[bold]Verifying inputs manifest:[/bold] {target_manifest}")
    valid, errors = verify_inputs_manifest(target_manifest)
    if not valid:
        console.print(
            f"[bold red]Manifest verification failed with {len(errors)} error(s):[/bold red]"
        )
        for err in errors:
            console.print(f"  [red]- {err}[/red]")
        raise typer.Exit(code=1)

    console.print(
        "[bold green]Inputs manifest verification passed successfully![/bold green]"
    )


release_app = typer.Typer(
    name="release",
    help="Immutable dataset release commands: build, verify, and diff.",
    no_args_is_help=True,
)
app.add_typer(release_app, name="release")
release_app.add_typer(recipe_app, name="recipe")


@release_app.command(name="build")
def release_build_cmd(
    db_path: Annotated[
        Path | None,
        typer.Option(
            "--db-path",
            help="Path to DuckDB database file (defaults to data/aped.duckdb).",
        ),
    ] = None,
    output_dir: Annotated[
        Path | None,
        typer.Option(
            "--output-dir",
            "-o",
            help="Destination directory for the release.",
        ),
    ] = None,
    version: Annotated[
        str,
        typer.Option(
            "--version",
            "-v",
            help="Semantic release version string (e.g. 1.0.0).",
        ),
    ] = "1.0.0",
    parliament: Annotated[
        str,
        typer.Option(
            "--parliament",
            "-p",
            help="Comma- or space-separated parliament numbers (e.g. '46,47,48').",
        ),
    ] = ",".join(map(str, supported_parliaments())),
    finance_year: Annotated[
        int,
        typer.Option(
            "--finance-year",
            help="Calendar reporting year for school finances.",
        ),
    ] = 2021,
    strict: Annotated[
        bool,
        typer.Option(
            "--strict/--no-strict",
            help="Halt with non-zero exit code if database validation fails.",
        ),
    ] = True,
) -> None:
    """Build complete, validated, immutable release dataset bundle."""
    effective_db = db_path or (DATA_DIR / "aped.duckdb")
    parls = parse_parliament_args(parliament)
    validate_supported_parliaments(parls)

    console.print(Panel(f"[bold blue]Building APEMAP Release v{version}[/bold blue]"))
    try:
        results = build_release(
            db_path=effective_db,
            output_dir=output_dir,
            version=version,
            parliaments=parls,
            finance_reporting_year=finance_year,
            strict=strict,
        )
    except Exception as e:
        console.print(f"[bold red]Release build failed:[/bold red] {e}")
        raise typer.Exit(code=1)

    console.print(f"[bold green]Release v{version} built successfully![/bold green]")
    console.print(f"Directory: [cyan]{results['output_directory']}[/cyan]")
    console.print(f"Files: {results['files_count']} ({results['total_bytes']:,} bytes)")
    console.print(f"Manifest: [yellow]{results['manifest']}[/yellow]")
    console.print(f"Checksums: [yellow]{results['sha256sums']}[/yellow]")


@release_app.command(name="verify")
def release_verify_cmd(
    release_dir: Annotated[
        Path,
        typer.Argument(
            help="Path to release directory containing manifest.json and SHA256SUMS.",
        ),
    ],
    strict_assertions: Annotated[
        bool,
        typer.Option(
            "--strict-assertions/--no-strict-assertions",
            help="Enforce that database assertions report passed in web/assertions.json.",
        ),
    ] = False,
) -> None:
    """Verify integrity, inventory, coordinate bounds, and privacy compliance of a release."""
    console.print(f"[bold]Verifying release directory:[/bold] {release_dir}")
    report = verify_release(release_dir, strict_assertions=strict_assertions)
    if not report["valid"]:
        console.print(
            f"[bold red]Release verification failed with {len(report['errors'])} error(s):[/bold red]"
        )
        for err in report["errors"]:
            console.print(f"  [red]- {err}[/red]")
        raise typer.Exit(code=1)

    console.print(
        f"[bold green]Release v{report['release_version']} verified successfully! "
        f"({report['checks_passed']}/{report['checks_run']} checks passed, "
        f"{report['verified_files_count']} files verified)[/bold green]"
    )


@release_app.command(name="diff")
def release_diff_cmd(
    old_release_dir: Annotated[
        Path,
        typer.Argument(
            help="Path to old/baseline release directory.",
        ),
    ],
    new_release_dir: Annotated[
        Path,
        typer.Argument(
            help="Path to new/target release directory.",
        ),
    ],
    as_json: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Output diff report as formatted JSON.",
        ),
    ] = False,
) -> None:
    """Generate comparative diff between two release dataset bundles."""
    try:
        report = diff_releases(old_release_dir, new_release_dir)
    except Exception as e:
        console.print(f"[bold red]Failed diffing releases:[/bold red] {e}")
        raise typer.Exit(code=1)

    if as_json:
        console.print(json.dumps(report, indent=2, sort_keys=True))
        return

    console.print(
        Panel(
            f"[bold blue]Release Diff: v{report['old_version']} -> v{report['new_version']}[/bold blue]"
        )
    )
    console.print(
        f"Size Change: {report['size_delta_bytes']:+,} bytes ({report['old_total_bytes']:,} -> {report['new_total_bytes']:,})"
    )
    console.print(
        f"Added Files ({len(report['added_files'])}): {report['added_files']}"
    )
    console.print(
        f"Removed Files ({len(report['removed_files'])}): {report['removed_files']}"
    )
    console.print(
        f"Modified Files ({len(report['modified_files'])}): {[f['path'] for f in report['modified_files']]}"
    )
    console.print(f"Unchanged Files: {len(report['unchanged_files'])}")


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


def validate_supported_parliaments(parliaments: list[int]) -> None:
    """Reject parliament numbers without fixed project metadata at the CLI boundary."""
    unsupported = [p for p in parliaments if p not in PARLIAMENT_METADATA]
    if unsupported:
        supported = ", ".join(str(p) for p in sorted(PARLIAMENT_METADATA))
        numbers = ", ".join(str(p) for p in unsupported)
        raise typer.BadParameter(
            f"Unsupported parliament number(s): {numbers}. "
            f"Supported values are: {supported}."
        )


@ingest_app.command(name="aph")
def ingest_aph(
    parliament: Annotated[
        str,
        typer.Option(
            "--parliament",
            "-p",
            help="Comma- or space-separated parliament numbers (e.g. '46,47,48').",
        ),
    ] = ",".join(map(str, supported_parliaments())),
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
    decision_log: Annotated[
        Path | None,
        typer.Option(
            "--decision-log",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="Explicit review ledger; defaults to the working review log.",
        ),
    ] = None,
) -> None:
    """Ingest parliamentarian demographics and secondary education records from APH."""
    parl_list = parse_parliament_args(parliament)
    validate_supported_parliaments(parl_list)
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
        decision_log_path=decision_log,
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
    finance_file: Annotated[
        Path | None,
        typer.Option(
            "--finance-file",
            help="Path to local authorised school finances CSV or Parquet file.",
        ),
    ] = None,
    finance_year: Annotated[
        int,
        typer.Option(
            "--finance-year",
            help="Reporting calendar year for school finances (defaults to 2021).",
        ),
    ] = 2021,
    decision_log: Annotated[
        Path | None,
        typer.Option(
            "--decision-log",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="Explicit review ledger; defaults to the working review log.",
        ),
    ] = None,
) -> None:
    """Ingest official 2025 ACARA datasets and isolate or ingest annual school finances."""
    effective_db_path = db_path or (DATA_DIR / "aped.duckdb")
    effective_out_dir = output_dir or PROCESSED_DIR

    console.print(
        "[bold blue]Starting ACARA Ingestion Pipeline[/bold blue] "
        f"(download={download}, longitudinal={longitudinal})"
    )
    with console.status(
        "[bold green]Processing ACARA datasets and updating finances..."
    ):
        results = run_acara_ingestion(
            decision_log_path=decision_log,
            download_latest=download,
            use_longitudinal=longitudinal,
            db_path=effective_db_path,
            finance_path=finance_file,
            finance_year=finance_year,
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
        "School Finances Loaded",
        f"{results.get('school_finances_loaded', results.get('school_finances_2021_migrated', 0)):,}",
    )
    if "school_finances_2021_migrated" in results:
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


@ingest_app.command(name="finances")
def ingest_finances_cmd(
    source_file: Annotated[
        Path,
        typer.Option(
            "--source-file",
            "-f",
            help="Path to local authorised school finances CSV or Parquet file.",
        ),
    ],
    reporting_year: Annotated[
        int,
        typer.Option(
            "--year",
            "-y",
            help="Reporting calendar year (e.g. 2021, 2024).",
        ),
    ] = 2021,
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
    """Ingest authorised school finance records into canonical DuckDB."""
    effective_db_path = db_path or (DATA_DIR / "aped.duckdb")
    effective_out_dir = output_dir or PROCESSED_DIR

    console.print(
        f"[bold blue]Ingesting authorised school finances for {reporting_year}[/bold blue] "
        f"from [cyan]{source_file}[/cyan]"
    )
    conn = get_connection(effective_db_path)
    try:
        init_schema(conn)
        loaded = ingest_school_finances(
            conn, source_file, reporting_year=reporting_year
        )
        if export_parquet:
            export_to_parquet(conn, effective_out_dir)
    finally:
        conn.close()

    console.print(
        f"[bold green]Successfully ingested {loaded:,} finance records![/bold green]"
    )


@ingest_app.command(name="wikimedia")
def ingest_wikimedia(
    parliament: Annotated[
        str,
        typer.Option(
            "--parliament",
            "-p",
            help="Comma- or space-separated parliament numbers (e.g. '46,47,48').",
        ),
    ] = ",".join(map(str, supported_parliaments())),
    refresh: Annotated[
        bool,
        typer.Option(
            "--refresh/--no-refresh",
            help="Force re-fetching live from Wikimedia API instead of local disk cache.",
        ),
    ] = False,
    members: Annotated[
        bool,
        typer.Option(
            "--members/--no-members",
            help="Enrich canonical member records with Wikidata identifiers and cross-check demographics.",
        ),
    ] = True,
    schools: Annotated[
        bool,
        typer.Option(
            "--schools/--no-schools",
            help="Generate suggestions for unconfirmed and international schools via Wikimedia.",
        ),
    ] = True,
    db_path: Annotated[
        Path | None,
        typer.Option(
            "--db-path",
            help="Path to DuckDB database file (defaults to data/aped.duckdb).",
        ),
    ] = None,
    output_dir: Annotated[
        Path | None,
        typer.Option(
            "--output-dir",
            help="Directory to save generated CSV review files (defaults to data/processed).",
        ),
    ] = None,
    cache_dir: Annotated[
        Path | None,
        typer.Option(
            "--cache-dir",
            help="Directory for local raw disk cache (defaults to data/raw/wikimedia).",
        ),
    ] = None,
    timeout: Annotated[
        int,
        typer.Option(
            "--timeout",
            help="Timeout in seconds for Wikimedia HTTP requests.",
        ),
    ] = DEFAULT_WIKIMEDIA_TIMEOUT,
    decision_log: Annotated[
        Path | None,
        typer.Option(
            "--decision-log",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="Explicit review ledger; defaults to the working review log.",
        ),
    ] = None,
) -> None:
    """Enrich canonical members and review unmatched schools using Wikipedia and Wikidata."""
    parl_list = parse_parliament_args(parliament)
    validate_supported_parliaments(parl_list)
    effective_db_path = db_path or (DATA_DIR / "aped.duckdb")
    effective_out_dir = output_dir or PROCESSED_DIR

    console.print(
        f"[bold blue]Starting Wikimedia Ingestion & Enrichment[/bold blue] for Parliaments: "
        f"[green]{parl_list}[/green]"
    )
    if refresh:
        console.print(
            "[yellow]Notice:[/yellow] Live refresh from Wikimedia API requested."
        )
    else:
        console.print("[dim]Using local disk cache if available.[/dim]")

    results = run_wikimedia_enrichment(
        decision_log_path=decision_log,
        parliaments=parl_list,
        refresh=refresh,
        enrich_members=members,
        enrich_schools=schools,
        db_path=effective_db_path,
        output_dir=effective_out_dir,
        cache_dir=cache_dir or RAW_WIKIMEDIA_DIR,
        timeout=timeout,
    )

    console.print()
    console.print("[bold green]Wikimedia Enrichment Complete![/bold green]")
    table = Table(title="Wikimedia Enrichment Summary", header_style="bold magenta")
    table.add_column("Metric", style="cyan")
    table.add_column("Count / Status", justify="right", style="green")

    if members:
        table.add_row("Members Processed", str(results["members_processed"]))
        table.add_row("New Wikidata IDs Populated", str(results["members_enriched"]))
        table.add_row("Member Identifier Conflicts", str(results["members_conflicts"]))
        table.add_row("Demographic Discrepancies", str(results["member_discrepancies"]))
        table.add_row("Member Review CSV", results["member_review_csv"])

    if schools:
        table.add_row("Schools Processed", str(results["schools_processed"]))
        table.add_row("School Suggestions Generated", str(results["schools_suggested"]))
        table.add_row("School Review CSV", results["school_review_csv"])

    console.print(table)


@ingest_app.command(name="aec")
@app.command(name="ingest-aec")
def ingest_aec(
    election_year: Annotated[
        int,
        typer.Option(
            "--election-year",
            "-y",
            help="Federal election year for boundaries (default: 2025).",
        ),
    ] = 2025,
    refresh: Annotated[
        bool,
        typer.Option(
            "--refresh",
            help="Force re-fetching AEC boundary shapefile archive instead of local disk cache.",
        ),
    ] = False,
    db_path: Annotated[
        Path | None,
        typer.Option(
            "--db-path",
            help="Path to DuckDB database file (defaults to data/aped.duckdb).",
        ),
    ] = None,
    raw_dir: Annotated[
        Path | None,
        typer.Option(
            "--raw-dir",
            help="Directory for cached raw shapefile archive.",
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
    """Ingest official AEC 2025 federal electoral boundaries and export canonical GeoParquet."""
    effective_db_path = db_path or (DATA_DIR / "aped.duckdb")
    effective_out_dir = output_dir or PROCESSED_DIR

    console.print(
        f"[bold blue]Starting AEC Ingestion[/bold blue] for Election Year: "
        f"[green]{election_year}[/green]"
    )
    if refresh:
        console.print("[yellow]Notice:[/yellow] Live refresh from AEC requested.")
    else:
        console.print("[dim]Using local disk cache if available.[/dim]")

    results = run_aec_ingestion(
        election_year=election_year,
        refresh=refresh,
        db_path=effective_db_path,
        raw_dir=raw_dir,
        export_parquet_files=export_parquet,
        output_dir=effective_out_dir,
    )

    console.print()
    console.print("[bold green]AEC Boundary Ingestion Complete![/bold green]")
    console.print(
        f"Database: [cyan]{effective_db_path}[/cyan] | "
        f"Divisions Loaded: [green]{results['divisions_loaded']}[/green] | "
        f"Source: [dim]{results['source_dataset']}[/dim]"
    )
    if export_parquet and results.get("parquet_path"):
        console.print(f"GeoParquet written to: [cyan]{results['parquet_path']}[/cyan]")


@ingest_app.command(name="benchmarks")
@app.command(name="ingest-benchmarks")
def ingest_benchmarks(
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
    """Ingest official ABS statistical education sector benchmarks into canonical tables."""
    effective_db_path = db_path or (DATA_DIR / "aped.duckdb")
    effective_out_dir = output_dir or PROCESSED_DIR

    console.print(
        "[bold blue]Starting ABS Reference Benchmarks Ingestion...[/bold blue]"
    )

    results = run_abs_ingestion(
        db_path=effective_db_path,
        export_parquet_files=export_parquet,
        output_dir=effective_out_dir,
    )

    console.print()
    console.print("[bold green]ABS Benchmarks Ingestion Complete![/bold green]")
    console.print(
        f"Database: [cyan]{effective_db_path}[/cyan] | "
        f"Benchmark Year: [green]{results['benchmark_year']}[/green] | "
        f"Records Loaded: [green]{results['benchmarks_loaded']}[/green] | "
        f"Source: [dim]{results['source_title']} ({results['source_url']})[/dim]"
    )
    if export_parquet and results.get("parquet_path"):
        console.print(f"Parquet written to: [cyan]{results['parquet_path']}[/cyan]")


@ingest_app.command(name="funding")
@app.command(name="ingest-funding")
def ingest_funding_cmd(
    benchmarks_file: Annotated[
        Path | None,
        typer.Option(
            "--benchmarks-file",
            help="Path to ACARA finance benchmarks CSV.",
        ),
    ] = None,
    nsw_file: Annotated[
        Path | None,
        typer.Option(
            "--nsw-file",
            help="Path to NSW RAM allocations CSV.",
        ),
    ] = None,
    tas_file: Annotated[
        Path | None,
        typer.Option(
            "--tas-file",
            help="Path to Tasmania SRP allocations CSV.",
        ),
    ] = None,
    nt_file: Annotated[
        Path | None,
        typer.Option(
            "--nt-file",
            help="Path to NT school funding CSV.",
        ),
    ] = None,
    qld_file: Annotated[
        Path | None,
        typer.Option(
            "--qld-file",
            help="Path to QLD non-state grants CSV.",
        ),
    ] = None,
    manual_file: Annotated[
        Path | None,
        typer.Option(
            "--manual-file",
            help="Path to manual school funding CSV.",
        ),
    ] = None,
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
    """Ingest public school funding data and ACARA finance benchmarks."""
    effective_db_path = db_path or (DATA_DIR / "aped.duckdb")
    effective_out_dir = output_dir or PROCESSED_DIR

    console.print(
        "[bold blue]Starting Public School Funding & Benchmarks Ingestion...[/bold blue]"
    )

    conn = get_connection(effective_db_path)
    try:
        init_schema(conn)
        counts = ingest_all_funding(
            conn,
            benchmarks_path=benchmarks_file,
            nsw_path=nsw_file,
            tas_path=tas_file,
            nt_path=nt_file,
            qld_path=qld_file,
            manual_path=manual_file,
        )

        table = Table(
            title="Public School Funding & Benchmarks Ingestion Summary",
            header_style="bold magenta",
        )
        table.add_column("Dataset / Stream", style="cyan")
        table.add_column("Records Ingested", justify="right", style="green")

        table.add_row("ACARA Finance Benchmarks", f"{counts['acara_benchmarks']:,}")
        table.add_row("NSW RAM Public Funding", f"{counts['nsw_ram']:,}")
        table.add_row(
            "Tasmania DECYP Fairer Funding SRP", f"{counts['tasmania_srp']:,}"
        )
        table.add_row(
            "Northern Territory Needs-Based Resourcing",
            f"{counts['nt_funding']:,}",
        )
        table.add_row(
            "Queensland Non-State Recurrent Grants", f"{counts['qld_grants']:,}"
        )
        table.add_row(
            "Manual Authoritative Disclosures", f"{counts['manual_enrichment']:,}"
        )

        console.print()
        console.print(table)
        console.print()

        if export_parquet:
            console.print("[dim]Exporting canonical parquet tables...[/dim]")
            export_to_parquet(conn, effective_out_dir)
            console.print(
                f"Parquet exports written to: [cyan]{effective_out_dir}[/cyan]"
            )
    finally:
        conn.close()

    console.print("[bold green]Funding & Benchmarks Ingestion Complete![/bold green]")


@app.command(name="backtest-benchmarks")
def backtest_benchmarks_cmd(
    year: Annotated[
        int,
        typer.Option(
            "--year",
            "-y",
            help="Historical calendar year for backtesting (defaults to 2021).",
        ),
    ] = 2021,
    metric: Annotated[
        str,
        typer.Option(
            "--metric",
            "-m",
            help="Finance metric to backtest.",
        ),
    ] = "total_net_recurrent_income_per_student",
    db_path: Annotated[
        Path | None,
        typer.Option(
            "--db-path",
            help="Path to DuckDB database file (defaults to data/aped.duckdb).",
        ),
    ] = None,
) -> None:
    """Backtest benchmark estimation model against historical observed school finances."""
    effective_db_path = db_path or (DATA_DIR / "aped.duckdb")
    conn = get_connection(effective_db_path, read_only=True)
    try:
        results = backtest_finance_benchmarks(conn, historical_year=year, metric=metric)

        console.print(
            f"[bold blue]Backtesting Results for Historical Year {year}[/bold blue] "
            f"(Metric: [cyan]{metric}[/cyan])"
        )
        console.print(
            f"Evaluated Schools: [green]{results['sample_size']}[/green] | "
            f"Overall Median Ratio: [green]{results['overall_median_ratio']}[/green] | "
            f"Overall MAD: [green]{results['overall_mad']}[/green] | "
            f"Overall MAPE: [green]{results['overall_mape']}%[/green]"
        )

        table = Table(title="Sector Backtest Performance", header_style="bold magenta")
        table.add_column("Sector", style="cyan")
        table.add_column("Sample Size", justify="right", style="white")
        table.add_column("Median Ratio", justify="right", style="green")
        table.add_column("MAD", justify="right", style="green")
        table.add_column("MAPE (%)", justify="right", style="yellow")

        for sec, stats in results["sector_metrics"].items():
            table.add_row(
                sec,
                str(stats["sample_size"]),
                str(stats["median_ratio"]),
                str(stats["mad"]),
                f"{stats['mape']}%",
            )
        console.print()
        console.print(table)
        console.print()

        if results["high_dispersion_groups"]:
            console.print(
                f"[bold yellow]Identified {len(results['high_dispersion_groups'])} High-Dispersion Groups (Fallback to Peer Average):[/bold yellow]"
            )
            for grp in results["high_dispersion_groups"][:10]:
                console.print(f"  - [dim]{grp}[/dim]")
    finally:
        conn.close()


@app.command(name="transform")
def transform_cmd(
    db_path: Annotated[
        Path | None,
        typer.Option(
            "--db-path",
            help="Path to DuckDB database file (defaults to data/aped.duckdb).",
        ),
    ] = None,
    gpkg_path: Annotated[
        Path | None,
        typer.Option(
            "--gpkg-path",
            help="Optional path to legacy aped.gpkg to migrate historical 2021 finances.",
        ),
    ] = None,
) -> None:
    """Initialize canonical relational schema, analytical views, and macros idempotently."""
    effective_db_path = db_path or (DATA_DIR / "aped.duckdb")
    console.print(
        f"[bold blue]Initializing schema and views on:[/bold blue] [cyan]{effective_db_path}[/cyan]"
    )

    conn = get_connection(effective_db_path)
    try:
        init_schema(conn)
        console.print("[green]Schema DDL and views applied successfully.[/green]")

        res = conn.execute("SELECT count(*) FROM school_finances").fetchone()
        fin_count = res[0] if res else 0
        if fin_count == 0:
            console.print("[dim]Migrating historical school finances...[/dim]")
            migrated = migrate_historical_finances(conn, gpkg_path)
            console.print(
                f"[green]Migrated {migrated:,} historical finance records.[/green]"
            )
        else:
            console.print(
                f"[dim]Historical finances already populated ({fin_count:,} records).[/dim]"
            )
    finally:
        conn.close()

    console.print("[bold green]Transform step complete![/bold green]")


@app.command(name="validate")
def validate_cmd(
    db_path: Annotated[
        Path | None,
        typer.Option(
            "--db-path",
            help="Path to DuckDB database file (defaults to data/aped.duckdb).",
        ),
    ] = None,
    parliament: Annotated[
        str,
        typer.Option(
            "--parliament",
            "-p",
            help="Comma- or space-separated parliament numbers (e.g. '46,47,48').",
        ),
    ] = ",".join(map(str, supported_parliaments())),
    strict: Annotated[
        bool,
        typer.Option(
            "--strict/--no-strict",
            help="Exit with non-zero code if any validation check fails.",
        ),
    ] = True,
) -> None:
    """Execute database integrity validation, FK constraints, and parliament coverage gates."""
    effective_db_path = db_path or (DATA_DIR / "aped.duckdb")
    parl_list = parse_parliament_args(parliament)
    validate_supported_parliaments(parl_list)

    console.print(
        f"[bold blue]Running Validation on:[/bold blue] [cyan]{effective_db_path}[/cyan]"
    )
    conn = get_connection(effective_db_path)
    try:
        report = validate_database(conn, parl_list)
    finally:
        conn.close()

    # Table counts summary
    tbl_summary = Table(
        title="Canonical Database Table Counts", header_style="bold cyan"
    )
    tbl_summary.add_column("Canonical Table")
    tbl_summary.add_column("Row Count", justify="right")
    for tbl, cnt in report.table_counts.items():
        tbl_summary.add_row(tbl, f"{cnt:,}")
    console.print(tbl_summary)

    # Parliament metrics summary
    if report.parliament_metrics:
        p_tbl = Table(
            title="Parliament Benchmark Coverage", header_style="bold magenta"
        )
        p_tbl.add_column("Parliament", justify="center")
        p_tbl.add_column("Total Stints", justify="right")
        p_tbl.add_column("Unique Members", justify="right")
        p_tbl.add_column("Opening Day Members", justify="right")
        p_tbl.add_column("Current Members", justify="right")
        for p_num, m in report.parliament_metrics.items():
            p_tbl.add_row(
                str(p_num),
                f"{m['total_stints']:,}",
                f"{m['unique_members']:,}",
                f"{m['opening_day_members']:,}",
                f"{m['current_members']:,}",
            )
        console.print(p_tbl)

    console.print()
    if report.passed:
        console.print(
            Panel(
                f"[bold green]ALL {report.checks_run} VALIDATION CHECKS PASSED![/bold green]",
                style="green",
            )
        )
    else:
        console.print(
            Panel(
                f"[bold red]VALIDATION FAILED:[/bold red] "
                f"{report.checks_passed}/{report.checks_run} checks passed, "
                f"{len(report.failures)} failures detected.",
                style="red",
            )
        )
        for idx, fail in enumerate(report.failures, 1):
            console.print(f"  [red]{idx}. {fail}[/red]")

        if strict:
            raise typer.Exit(code=1)


@app.command(name="analyze")
def analyze_cmd(
    db_path: Annotated[
        Path | None,
        typer.Option(
            "--db-path",
            help="Path to DuckDB database file (defaults to data/aped.duckdb).",
        ),
    ] = None,
    parliament: Annotated[
        str,
        typer.Option(
            "--parliament",
            "-p",
            help="Comma- or space-separated parliament numbers (e.g. '46,47,48').",
        ),
    ] = ",".join(map(str, supported_parliaments())),
    output_dir: Annotated[
        Path | None,
        typer.Option(
            "--output-dir",
            help="Directory to save analytical metrics JSON.",
        ),
    ] = None,
    finance_year: Annotated[
        int,
        typer.Option(
            "--finance-year",
            "--finance-reporting-year",
            help="Calendar reporting year for school finances (defaults to 2021).",
        ),
    ] = 2021,
) -> None:
    """Compute deterministic demographic, sector distribution, and school funding statistics."""
    effective_db_path = db_path or (DATA_DIR / "aped.duckdb")
    effective_out_dir = output_dir or PROCESSED_DIR
    parl_list = parse_parliament_args(parliament)
    validate_supported_parliaments(parl_list)

    console.print(
        f"[bold blue]Running Deterministic Analysis on:[/bold blue] [cyan]{effective_db_path}[/cyan]"
    )
    conn = get_connection(effective_db_path)
    try:
        json_path = export_analysis_report(
            conn,
            effective_out_dir,
            parl_list,
            finance_reporting_year=finance_year,
        )

        for p in parl_list:
            dem = compute_parliament_demographics(conn, p)
            sec = compute_sector_summary(conn, p)
            fund = compute_funding_summary(conn, p, reporting_year=finance_year)

            console.print()
            console.print(
                f"[bold cyan]--- Parliament {p} Demographic & Education Analysis ---[/bold cyan]"
            )
            console.print(
                f"Opening Benchmark: [green]{dem['reference_opening_date']}[/green] | "
                f"Total MPs: {dem['total_parliamentarians']} | "
                f"Avg Age: {dem['average_age_at_opening']} | "
                f"Median Age: {dem['median_age_at_opening']}"
            )

            # Sector breakdown table
            sec_tbl = Table(
                title=f"Parliament {p} Secondary Sector Distribution",
                header_style="bold blue",
            )
            sec_tbl.add_column("Sector / Category")
            sec_tbl.add_column("Unique MPs", justify="right")
            sec_tbl.add_column("% of Known MPs", justify="right")
            sec_tbl.add_column("Attendance Instances", justify="right")

            pcts = sec["percentage_of_known_parliamentarians"]
            insts = sec["attendance_instances_by_sector"]
            for cat, cnt in sec["unique_parliamentarians_by_sector"].items():
                sec_tbl.add_row(
                    cat,
                    str(cnt),
                    f"{pcts.get(cat, 0.0):.1f}%" if cat in pcts else "-",
                    str(insts.get(cat, "-")),
                )
            console.print(sec_tbl)

            # Funding table
            fund_tbl = Table(
                title=f"Parliament {p} School {finance_year} Financial Averages (N reported)",
                header_style="bold yellow",
            )
            fund_tbl.add_column("Sector")
            fund_tbl.add_column("Avg Gross Income / Student", justify="right")
            fund_tbl.add_column("N (Gross)", justify="right")
            fund_tbl.add_column("Avg Net Recurrent / Student", justify="right")
            fund_tbl.add_column("N (Net)", justify="right")

            for sname, sdata in fund["by_sector"].items():
                g_avg = (
                    f"${sdata['gross_income_per_student_avg']:,.0f}"
                    if sdata["gross_income_per_student_avg"]
                    else "N/A"
                )
                n_avg = (
                    f"${sdata['net_recurrent_income_per_student_avg']:,.0f}"
                    if sdata["net_recurrent_income_per_student_avg"]
                    else "N/A"
                )
                fund_tbl.add_row(
                    sname,
                    g_avg,
                    str(sdata["gross_income_sample_size"]),
                    n_avg,
                    str(sdata["net_recurrent_income_sample_size"]),
                )
            console.print(fund_tbl)

    finally:
        conn.close()

    console.print()
    console.print(
        f"[bold green]Analysis Complete![/bold green] Report saved to: [cyan]{json_path}[/cyan]"
    )


@app.command(name="export")
def export_cmd(
    cohort: Annotated[
        str,
        typer.Option("--cohort", help="Spatial cohort: opening_day or all_service."),
    ] = "opening_day",
    db_path: Annotated[
        Path | None,
        typer.Option(
            "--db-path",
            help="Path to DuckDB database file (defaults to data/aped.duckdb).",
        ),
    ] = None,
    parliament: Annotated[
        str,
        typer.Option(
            "--parliament",
            "-p",
            help="Comma- or space-separated parliament numbers (e.g. '46,47,48').",
        ),
    ] = ",".join(map(str, supported_parliaments())),
    output_dir: Annotated[
        Path | None,
        typer.Option(
            "--output-dir",
            help="Directory to save Parquet and GeoJSON files.",
        ),
    ] = None,
    finance_year: Annotated[
        int,
        typer.Option(
            "--finance-year",
            "--finance-reporting-year",
            help="Calendar reporting year for school finances (defaults to 2021).",
        ),
    ] = 2021,
    web_release: Annotated[
        bool,
        typer.Option(
            "--web-release/--no-web-release",
            help="Also export results-summary.json, schools.geojson, downloads, and manifest.json.",
        ),
    ] = False,
    data_release_version: Annotated[
        str,
        typer.Option(
            "--data-release-version",
            help="Version recorded in the web release manifest.",
        ),
    ] = "0.2.0",
    source_commit: Annotated[
        str | None,
        typer.Option(
            "--source-commit", help="Source commit SHA override for packaged builds."
        ),
    ] = None,
    source_snapshot_dates: Annotated[
        Path | None,
        typer.Option(
            "--source-snapshot-dates",
            exists=True,
            dir_okay=False,
            readable=True,
            help="JSON object of upstream source names and YYYY-MM-DD snapshot dates (or null).",
        ),
    ] = None,
) -> None:
    """Export canonical Parquet files, GeoJSON layers, and JSON analytical metrics."""
    effective_db_path = db_path or (DATA_DIR / "aped.duckdb")
    effective_out_dir = output_dir or PROCESSED_DIR
    parl_list = parse_parliament_args(parliament)
    validate_supported_parliaments(parl_list)
    snapshot_dates = None
    if source_snapshot_dates is not None:
        try:
            snapshot_dates = validate_source_snapshot_dates(
                json.loads(source_snapshot_dates.read_text(encoding="utf-8"))
            )
        except (OSError, ValueError) as e:
            raise typer.BadParameter(
                f"Invalid source snapshot dates: {e}",
                param_hint="--source-snapshot-dates",
            ) from e

    console.print(
        f"[bold blue]Exporting Artifacts from:[/bold blue] [cyan]{effective_db_path}[/cyan]"
    )
    conn = get_connection(effective_db_path)
    try:
        results = export_all_artifacts(
            conn,
            effective_out_dir,
            parl_list,
            finance_reporting_year=finance_year,
            include_web_release=web_release,
            data_release_version=data_release_version,
            source_commit=source_commit,
            source_snapshot_dates=snapshot_dates,
            cohort=cohort,
        )
    finally:
        conn.close()

    console.print("[bold green]Export Complete![/bold green]")
    console.print(f"Output Directory: [cyan]{results['output_directory']}[/cyan]")
    console.print(f"Parquet Tables Exported: {len(results['parquet_files'])}")
    for tbl, pth in results["parquet_files"].items():
        console.print(f"  - {tbl}: [dim]{pth}[/dim]")
    console.print(f"GeoJSON Spatial Layers: {len(results['geojson_layers'])}")
    for p_num, pth in results["geojson_layers"].items():
        console.print(f"  - Parliament {p_num}: [dim]{pth}[/dim]")
    console.print(f"Analysis Report: [yellow]{results['analysis_report']}[/yellow]")
    if web_release:
        console.print(f"Web Release Manifest: [yellow]{results['manifest']}[/yellow]")


@app.command(name="run-all")
def run_all_cmd(
    db_path: Annotated[
        Path | None,
        typer.Option(
            "--db-path",
            help="Path to DuckDB database file (defaults to data/aped.duckdb).",
        ),
    ] = None,
    output_dir: Annotated[
        Path | None,
        typer.Option(
            "--output-dir",
            help="Directory to save generated artifacts.",
        ),
    ] = None,
    parliament: Annotated[
        str,
        typer.Option(
            "--parliament",
            "-p",
            help="Comma- or space-separated parliament numbers (e.g. '46,47,48').",
        ),
    ] = ",".join(map(str, supported_parliaments())),
    download: Annotated[
        bool,
        typer.Option(
            "--download/--no-download",
            help="Download latest official 2025 ACARA datasets from ACARA Data Access portal.",
        ),
    ] = False,
    refresh: Annotated[
        bool,
        typer.Option(
            "--refresh/--no-refresh",
            help="Force re-fetching from live APH Handbook API instead of local disk cache.",
        ),
    ] = False,
    longitudinal: Annotated[
        bool,
        typer.Option(
            "--longitudinal/--single-year",
            help="Download 2008-2025 longitudinal profile dataset or 2025 single-year profile.",
        ),
    ] = True,
    strict: Annotated[
        bool,
        typer.Option(
            "--strict/--no-strict",
            help="Exit with non-zero status code if validation checks fail.",
        ),
    ] = True,
    enrich_wikimedia: Annotated[
        bool,
        typer.Option(
            "--enrich-wikimedia/--no-enrich-wikimedia",
            help="Enrich canonical members and unmatched schools with Wikimedia data.",
        ),
    ] = False,
    finance_year: Annotated[
        int,
        typer.Option(
            "--finance-year",
            "--finance-reporting-year",
            help="Calendar reporting year for school finances (defaults to 2021).",
        ),
    ] = 2021,
    offline: Annotated[
        bool,
        typer.Option(
            "--offline/--no-offline",
            help="Run pipeline in strict offline mode using verified local inputs with zero network requests.",
        ),
    ] = False,
    inputs_manifest: Annotated[
        Path | None,
        typer.Option(
            "--inputs-manifest",
            help="Path to inputs-manifest.json for offline input verification.",
        ),
    ] = None,
    decision_log: Annotated[
        Path | None,
        typer.Option(
            "--decision-log",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="Explicit review ledger; defaults to the working review log.",
        ),
    ] = None,
) -> None:
    """Execute end-to-end pipeline deterministically from raw inputs to exported artifacts."""
    effective_db_path = db_path or (DATA_DIR / "aped.duckdb")
    effective_out_dir = output_dir or PROCESSED_DIR
    parl_list = parse_parliament_args(parliament)
    validate_supported_parliaments(parl_list)

    consumed_manifest: bytes | None = None
    if offline:
        if download:
            raise typer.BadParameter("Cannot combine --offline with --download.")
        if refresh:
            raise typer.BadParameter("Cannot combine --offline with --refresh.")
        import os

        os.environ["APEMAP_OFFLINE"] = "1"
        console.print("[dim]Preflighting offline inputs against manifest...[/dim]")
        try:
            preflight_offline_inputs(manifest_path=inputs_manifest)
            consumed_manifest = (inputs_manifest or DEFAULT_MANIFEST_PATH).read_bytes()
            console.print("[green]Offline input preflight passed.[/green]")
        except RuntimeError as err:
            console.print(f"[bold red]Offline Preflight Failed:[/bold red] {err}")
            raise typer.Exit(code=1)

    console.print(
        Panel("[bold blue]Starting Full APEMAP End-to-End Pipeline[/bold blue]")
    )

    # Step 1: Reference Data Ingestion (AEC Boundaries & ABS Benchmarks)
    console.print("\n[bold]1. Running Reference Data Ingestion...[/bold]")
    console.print("[dim]1a. Ingesting AEC Federal Electoral Boundaries...[/dim]")
    run_aec_ingestion(
        election_year=2025,
        refresh=refresh,
        db_path=effective_db_path,
        export_parquet_files=False,
        output_dir=effective_out_dir,
    )
    console.print("[dim]1b. Ingesting ABS Schools Sector Benchmarks...[/dim]")
    run_abs_ingestion(
        db_path=effective_db_path,
        export_parquet_files=False,
        output_dir=effective_out_dir,
    )

    # Step 2: ACARA Ingestion
    console.print("\n[bold]2. Running ACARA Ingestion...[/bold]")
    run_acara_ingestion(
        decision_log_path=decision_log,
        download_latest=download,
        use_longitudinal=longitudinal,
        db_path=effective_db_path,
        export_parquet_files=False,
        output_dir=effective_out_dir,
    )

    # Step 2b: Public Funding & ACARA Finance Benchmarks Ingestion
    console.print(
        "\n[bold]2b. Running Public Funding & Finance Benchmarks Ingestion...[/bold]"
    )
    funding_conn = get_connection(effective_db_path)
    try:
        init_schema(funding_conn)
        counts = ingest_all_funding(funding_conn)
        console.print(
            f"[dim]Ingested {counts['acara_benchmarks']} benchmarks and "
            f"{sum(v for k, v in counts.items() if k != 'acara_benchmarks')} public funding records.[/dim]"
        )
    finally:
        funding_conn.close()

    # Step 3: APH Ingestion
    console.print("\n[bold]3. Running APH Ingestion...[/bold]")
    run_aph_ingestion(
        decision_log_path=decision_log,
        parliaments=parl_list,
        refresh=refresh,
        db_path=effective_db_path,
        export_parquet_files=False,
        output_dir=effective_out_dir,
    )

    # Step 3b: Optional Wikimedia Enrichment
    if enrich_wikimedia:
        console.print("\n[bold]3b. Running Wikimedia Enrichment...[/bold]")
        run_wikimedia_enrichment(
            decision_log_path=decision_log,
            parliaments=parl_list,
            refresh=refresh,
            db_path=effective_db_path,
            output_dir=effective_out_dir,
        )

    # Step 4: Schema Transform and Views
    console.print("\n[bold]4. Initializing Schema & Views...[/bold]")
    conn = get_connection(effective_db_path)
    try:
        init_schema(conn)
        if consumed_manifest is not None:
            from apemap.review.integration import attach_source_manifest

            attach_source_manifest(
                conn,
                inputs_manifest or DEFAULT_MANIFEST_PATH,
                manifest_bytes=consumed_manifest,
            )
        res = conn.execute("SELECT count(*) FROM school_finances").fetchone()
        fin_count = res[0] if res else 0
        if fin_count == 0:
            migrate_historical_finances(conn)

        # Step 5: Validation Gate
        console.print("\n[bold]5. Validating Canonical Database...[/bold]")
        report = validate_database(conn, parl_list)
        if not report.passed:
            console.print(
                f"[bold red]Validation failed with {len(report.failures)} errors:[/bold red]"
            )
            for f in report.failures:
                console.print(f"  [red]- {f}[/red]")
            if strict:
                raise typer.Exit(code=1)
        else:
            console.print(
                f"[green]Validation passed ({report.checks_run} checks passed).[/green]"
            )

        # Step 6: Export Artifacts
        console.print(
            "\n[bold]6. Exporting Parquet, GeoJSON, and Analysis Metrics...[/bold]"
        )
        export_results = export_all_artifacts(
            conn,
            effective_out_dir,
            parl_list,
            finance_reporting_year=finance_year,
        )
    finally:
        conn.close()

    console.print()
    console.print(
        Panel("[bold green]APEMAP Pipeline Completed Successfully![/bold green]")
    )
    console.print(f"Database: [cyan]{effective_db_path}[/cyan]")
    console.print(f"Artifacts: [cyan]{effective_out_dir}[/cyan]")
    console.print(f"Report: [yellow]{export_results['analysis_report']}[/yellow]")


def main() -> None:
    """Entrypoint function for console script."""
    app()


if __name__ == "__main__":
    main()
