"""APEMAP Command-Line Interface powered by Typer."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from apemap.analysis import (
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
from apemap.db import get_connection, init_schema, migrate_historical_finances
from apemap.export import export_all_artifacts
from apemap.ingest.acara import run_acara_ingestion
from apemap.ingest.pipeline import run_aph_ingestion
from apemap.ingest.wikimedia import run_wikimedia_enrichment
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


@ingest_app.command(name="wikimedia")
def ingest_wikimedia(
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

        res = conn.execute("SELECT count(*) FROM school_finances_2021").fetchone()
        fin_count = res[0] if res else 0
        if fin_count == 0:
            console.print("[dim]Migrating historical 2021 school finances...[/dim]")
            migrated = migrate_historical_finances(conn, gpkg_path)
            console.print(
                f"[green]Migrated {migrated:,} historical 2021 finance records.[/green]"
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
    ] = "46,47,48",
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
    ] = "46,47,48",
    output_dir: Annotated[
        Path | None,
        typer.Option(
            "--output-dir",
            help="Directory to save analytical metrics JSON.",
        ),
    ] = None,
) -> None:
    """Compute deterministic demographic, sector distribution, and 2021 funding statistics."""
    effective_db_path = db_path or (DATA_DIR / "aped.duckdb")
    effective_out_dir = output_dir or PROCESSED_DIR
    parl_list = parse_parliament_args(parliament)
    validate_supported_parliaments(parl_list)

    console.print(
        f"[bold blue]Running Deterministic Analysis on:[/bold blue] [cyan]{effective_db_path}[/cyan]"
    )
    conn = get_connection(effective_db_path)
    try:
        json_path = export_analysis_report(conn, effective_out_dir, parl_list)

        for p in parl_list:
            dem = compute_parliament_demographics(conn, p)
            sec = compute_sector_summary(conn, p)
            fund = compute_funding_summary(conn, p)

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
                title=f"Parliament {p} School 2021 Financial Averages (N reported)",
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
    ] = "46,47,48",
    output_dir: Annotated[
        Path | None,
        typer.Option(
            "--output-dir",
            help="Directory to save Parquet and GeoJSON files.",
        ),
    ] = None,
) -> None:
    """Export canonical Parquet files, GeoJSON layers, and JSON analytical metrics."""
    effective_db_path = db_path or (DATA_DIR / "aped.duckdb")
    effective_out_dir = output_dir or PROCESSED_DIR
    parl_list = parse_parliament_args(parliament)
    validate_supported_parliaments(parl_list)

    console.print(
        f"[bold blue]Exporting Artifacts from:[/bold blue] [cyan]{effective_db_path}[/cyan]"
    )
    conn = get_connection(effective_db_path)
    try:
        results = export_all_artifacts(conn, effective_out_dir, parl_list)
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
    ] = "46,47,48",
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
) -> None:
    """Execute end-to-end pipeline deterministically from raw inputs to exported artifacts."""
    effective_db_path = db_path or (DATA_DIR / "aped.duckdb")
    effective_out_dir = output_dir or PROCESSED_DIR
    parl_list = parse_parliament_args(parliament)
    validate_supported_parliaments(parl_list)

    console.print(
        Panel("[bold blue]Starting Full APEMAP End-to-End Pipeline[/bold blue]")
    )

    # Step 1: ACARA Ingestion
    console.print("\n[bold]1. Running ACARA Ingestion...[/bold]")
    run_acara_ingestion(
        download_latest=download,
        use_longitudinal=longitudinal,
        db_path=effective_db_path,
        export_parquet_files=False,
        output_dir=effective_out_dir,
    )

    # Step 2: APH Ingestion
    console.print("\n[bold]2. Running APH Ingestion...[/bold]")
    run_aph_ingestion(
        parliaments=parl_list,
        refresh=refresh,
        db_path=effective_db_path,
        export_parquet_files=False,
        output_dir=effective_out_dir,
    )

    # Step 2b: Optional Wikimedia Enrichment
    if enrich_wikimedia:
        console.print("\n[bold]2b. Running Wikimedia Enrichment...[/bold]")
        run_wikimedia_enrichment(
            parliaments=parl_list,
            refresh=refresh,
            db_path=effective_db_path,
            output_dir=effective_out_dir,
        )

    # Step 3: Schema Transform and Views
    console.print("\n[bold]3. Initializing Schema & Views...[/bold]")
    conn = get_connection(effective_db_path)
    try:
        init_schema(conn)
        res = conn.execute("SELECT count(*) FROM school_finances_2021").fetchone()
        fin_count = res[0] if res else 0
        if fin_count == 0:
            migrate_historical_finances(conn)

        # Step 4: Validation Gate
        console.print("\n[bold]4. Validating Canonical Database...[/bold]")
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

        # Step 5: Export Artifacts
        console.print(
            "\n[bold]5. Exporting Parquet, GeoJSON, and Analysis Metrics...[/bold]"
        )
        export_results = export_all_artifacts(conn, effective_out_dir, parl_list)
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
