# APEMAP Documentation Index

Welcome to the documentation for the Australian Parliamentarians Education Map (APEMAP).
This index provides an overview of available technical guides, methodologies, and data specifications.

## Documentation Navigation

| Document | Description | Target Audience |
| :--- | :--- | :--- |
| [**Quickstart**](quickstart.md) | Clone-to-useful-result guide covering prerequisites, installation with `uv`, data initialization, and running pipeline stages. | New users, evaluators, developers |
| [**CLI Reference**](cli.md) | Comprehensive command-line reference for the `apemap` CLI (`ingest`, `transform`, `validate`, `analyze`, `export`, `run-all`). | Data engineers, automated workflows |
| [**Python Package**](package.md) | Reusable Python library architecture, public API surface (`get_connection`, `build_database`, `init_schema`), and internal modules. | Software engineers, tool builders |
| [**Methodology**](methodology.md) | Ingestion logic, APH text parsing, institutional matching against ACARA, cohort definitions, and research caveats. | Researchers, political scientists, data analysts |
| [**Data Model**](data-model.md) | Canonical DuckDB relational schema, entity relationships, views, parameterized macros, and repository storage layout. | Database administrators, analysts |
| [**Data Sources & Provenance**](data-sources.md) | Upstream data inventory, retrieval mechanisms, licensing, copyright attribution, and historical references. | Researchers, compliance, librarians |
| [**Analysis & Outputs**](analysis.md) | Deterministic demographic, sector, and financial metrics, export artifacts (Parquet, GeoJSON, JSON), and notebook workflows. | Policy researchers, visualizers, journalists |
| [**Reproducibility Guide**](reproducibility.md) | Deterministic guarantees, virtual environment locking, network-isolated stages, and validation gates. | Auditors, peer reviewers, CI/CD |
| [**Development Guide**](development.md) | Contributing guide, local environment setup, running tests, code formatting, linting, type-checking, and schema evolution. | Contributors, developers |

## Getting Started

If you are new to APEMAP:
1. Start with the [Quickstart Guide](quickstart.md) to set up your environment with `uv` and run your first pipeline query.
2. Review the [Methodology](methodology.md) to understand how secondary education data is linked to parliamentarians and matched against ACARA reference registers.
3. Consult the [Data Sources & Provenance](data-sources.md) document for citations and attribution requirements.

## Historical Documentation

Pre-modernisation exploratory documentation and notes from earlier project iterations are preserved in the [Legacy Documentation Archive](../archive/legacy-docs/2026-09-23/README.md).
