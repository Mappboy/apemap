# Australian Parliamentarians Education Map (APEMAP)

[![Binder](https://mybinder.org/badge_logo.svg)](https://mybinder.org/v2/gh/Mappboy/apemap/HEAD?urlpath=lab/tree/notebooks/00_data_overview.ipynb)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![License: CC BY 4.0](https://img.shields.io/badge/Data-CC_BY_4.0-lightgrey.svg)](LICENSE)

![Australian Politicians Education Map](Australian%20Politicians%20Education%20Map.png "Australian Politicians Education Map")

APEMAP (**Australian Parliamentarians Education Map**) is a Python data and analytics project for collecting, normalising, and analysing Australian federal parliamentarians' educational histories and related secondary school information.

---

## What Does APEMAP Do?

APEMAP provides a deterministic, reproducible pipeline to map the secondary education of federal parliamentarians:

- **Parliamentarian Ingestion**: Ingests member biographies and dated service histories from the official Australian Parliament House (APH) Parliamentary Handbook API across the 42nd–48th Parliaments, with opening-day cohorts and explicit historical evidence gaps.
- **ACARA School Registers**: Ingests authoritative Australian Curriculum, Assessment and Reporting Authority (ACARA) School Location and Longitudinal School Profile datasets.
- **Deterministic School Matching**: Resolves noisy biographical school names against ACARA school registers using normalized keys, reviewed decision-log mappings, and RapidFuzz token matching.
- **Canonical DuckDB Storage**: Consolidates members, service periods, institutions, education assertions, and historical 2021 financial profiles into an audited relational schema.
- **Integrity Validation**: Enforces relational integrity, non-null constraints, and opening-day seat benchmarks via automated validation gates.
- **Reviewed Corrections**: An append-only decision log records sourced member, education, institution and service corrections and replays them after ingestion.
- **Deterministic Analytics & Exports**: Generates sector distributions, demographic benchmarks, MySchool funding comparisons, Parquet tables, and spatial GeoJSON layers.

---

## Project Status

- **Maintained CLI & Package**: Fully supported modern Python package managed with `uv` and Hatchling, featuring the `apemap` Typer CLI.
- **Canonical Pipeline**: Deterministic end-to-end pipeline backed by DuckDB and portable Parquet/GeoJSON exports.
- **Analysis Notebooks**: Clean, reproducible Jupyter notebooks in `notebooks/` querying DuckDB read-only.
- **Legacy Components**: The prototype Dash web application (`app/`) and earlier SQLite/PostGIS workflows are preserved as historical research artifacts; static visualization results are published on [cpoole.dev](https://cpoole.dev).

---

## Pipeline Overview

```text
APH API / Cache ────────┐
                        ├──> Canonical DuckDB ──> Validate ──> Analyze ──> Export
ACARA Registers ────────┘    (data/aped.duckdb)                         (Parquet / GeoJSON / JSON)
```

---

## Quick Start

### Installation

APEMAP requires Python `>= 3.11` and [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/Mappboy/apemap.git
cd apemap
uv sync
```

Inspect available commands with the Typer CLI:

```bash
uv run apemap --help
```

For a fast development test loop, use
`uv run pytest -m "not integration and not notebook and not slow"`.
`uv run pytest` runs the complete suite; see the
[Development Guide](docs/development.md#7-writing-tests--working-with-fixtures)
for integration and notebook commands and fixture isolation rules.

### Running the Pipeline

Execute individual pipeline stages:

```bash
uv run apemap ingest acara      # Ingest ACARA school data & isolate 2021 finances
uv run apemap ingest aph        # Ingest parliamentarians, match schools & replay decisions
uv run apemap transform         # Initialize canonical schema, views, and macros
uv run apemap validate          # Run database integrity and coverage checks
uv run apemap analyze           # Compute demographic, sector, and funding stats
uv run apemap export            # Export Parquet tables and GeoJSON layers
```

Or run the entire coordinated pipeline in a single step:

```bash
uv run apemap run-all
```

For detailed instructions on using local cached data versus live network refreshes, see the [Quickstart Guide](docs/quickstart.md).

Review corrections with `uv run apemap review --help`. Generated CSVs are queue
views; decisions live in `data/reference/review/decisions.jsonl`. Install
`uv sync --extra review-ui` for the optional local reviewer, which supports
concurrent school reviews through four local request workers. Decisions save
immediately to the ledger. See
[Review decisions](docs/review-decisions.md) for evidence, imports and replay.
After saving decisions, follow [Decisions to a new release](docs/decisions-to-release.md)
to rebuild from pinned sources, verify the consumed ledger, package the public
bundle and prepare publication. Saving a mapping alone does not update released
data or the website.

Package 0.6.0 adds assertion-specific school resolution. Use **Map** on an
education assertion to preserve attendance provenance while choosing its
institution independently of other members with the same recorded school name.
School relationships remain reusable defaults. **Ambiguous name** and
**No suitable candidate** preserve attendance and require separate member
resolutions; the shared-name case completes when its active assertions are
individually reviewed. See
[Assertion-level resolution](docs/assertion-resolution.md) for precedence,
migration compatibility, [retained evidence and advisory scoring](docs/review-evidence.md),
and the [issue #66 acceptance audit](docs/issue-66-acceptance.md).

Package 0.4.0 distinguishes the school attended from a reviewed successor used
for matching, profiles and finance. Web and analysis contracts are `2.0.0`;
historical releases retain their existing contracts. See
[Original schools and reviewed successors](docs/successor-context.md) for campus
and sector evidence, map markers, counting rules and the sensitivity comparison.

---

## Documentation

Comprehensive guides, specifications, and methodologies are available in the [`docs/`](docs/README.md) directory:

| Guide | Description |
| :--- | :--- |
| [**Quickstart Guide**](docs/quickstart.md) | Clone-to-useful-result walkthrough and troubleshooting. |
| [**Review, Update & Publish**](docs/review-update-publish.md) | Step-by-step review of schools and members, data corrections, validation, and publication. |
| [**CLI Reference**](docs/cli.md) | Full command documentation, arguments, and options. |
| [**Python Package**](docs/package.md) | Programmatic Python API and subsystem architecture. |
| [**Research Methodology**](docs/methodology.md) | Matching algorithm, cohort definitions, and research caveats. |
| [**Data Model**](docs/data-model.md) | Canonical DuckDB relational schema, views, and ER diagram. |
| [**Data Sources & Provenance**](docs/data-sources.md) | Source inventory, licenses, citations, and attributions. |
| [**Analytical Outputs**](docs/analysis.md) | Analytical metrics, report schemas, and notebook workflows. |
| [**Reproducibility Guide**](docs/reproducibility.md) | Deterministic reproduction protocol and validation gates. |
| [**Development Guide**](docs/development.md) | Contributing guidelines, quality gates, and testing procedures. |

*Pre-modernisation documentation and exploratory notes are archived in [`archive/legacy-docs/2026-09-23/`](archive/legacy-docs/2026-09-23/README.md).*

---

## Data Quality & Research Caveats

> [!WARNING]
> This dataset was collated for research and civic analytics. Secondary schooling data is based on self-reported parliamentary biographies and automated matching against ACARA registers. For high-stakes or formal research applications, independent quality assurance of specific records is strongly recommended.
>
> Furthermore, historical 2021 school financial data reflects MySchool metrics for 2021 and does not represent school funding levels contemporaneous with when parliamentarians attended school decades ago. See [Research Methodology](docs/methodology.md) for detailed limitations.

The [historical coverage guide](docs/historical-coverage.md) describes the populated
42nd–48th release, replay commands, reviewed successor mappings and website
contract. Coverage, evidence review queues and seven map layers are delivered
separately from earlier research artifacts.

---

## Licensing & Attribution

APEMAP is dual-licensed under open terms:
- **Code & Pipeline Tooling**: [MIT License](LICENSE)
- **Compiled Datasets & Derived Data**: [Creative Commons Attribution 4.0 International (CC BY 4.0)](LICENSE)

### Third-Party Data Attributions
- **Parliament of Australia (APH)**: Parliamentary Handbook data, © Commonwealth of Australia.
- **ACARA**: School Location and Profile data, © Australian Curriculum, Assessment and Reporting Authority.
- **AEC**: Division boundaries and party registrations, © Commonwealth of Australia.
- **ABS**: ASGS geographic structures, © Commonwealth of Australia.
- **SMH Baseline**: Initial investigative baseline based on reporting by Daniel Carter, Noah Yim, Fleta Page, Rob Harris, Mark Stehle, and Matthew Absalom-Wong (Sydney Morning Herald, 2021).

For full details, see [Data Sources & Attribution](docs/data-sources.md).
