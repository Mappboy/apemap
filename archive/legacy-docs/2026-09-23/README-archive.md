# Legacy Documentation Archive (2026-09-23)

This is the project README immediately prior to the 2026 documentation restructuring. It is retained for historical context and includes legacy installation instructions, exploratory results, data notes and workflows that are no longer the canonical project documentation.

Current documentation starts at [/README.md](../../../README.md) and [/docs/README.md](../../../docs/README.md).

## Archive Context

- **Archived Date**: 2026-09-23
- **Reason**: Documentation modernisation separating concise project orientation (`README.md`) from modular architecture, data provenance, methodology, CLI reference, and reproducibility guides (`docs/`).
- **Superseded Workflows**:
  - Legacy SQLite / SpatiaLite (`data/aped.db`) and Datasette commands superseded by canonical DuckDB (`data/aped.duckdb`) and Typer CLI (`apemap`).
  - Historical PostGIS to GeoPackage commands superseded by deterministic export pipeline (`apemap export`).
  - Exploratory manual scraping notes superseded by deterministic ingestion pipeline (`apemap ingest aph`, `apemap ingest acara`).
  - Stale list of missing MPs/Senators replaced by programmatic coverage reporting (`apemap validate`, `data/processed/coverage_metrics.json`, `data/processed/unmatched_schools.csv`).
