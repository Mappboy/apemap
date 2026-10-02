# Archived Legacy Python Modules (2026-09-29)

These modules represent legacy prototype tooling, ad-hoc SPARQL scripts, and uncurated database query functions preserved from earlier iterations of APEMAP:

- `main.py`: Legacy pipeline entrypoint superseded by the canonical Typer CLI in `apemap/cli.py`.
- `utils.py`: Legacy utility functions superseded by modular implementations under `apemap/db.py`, `apemap/analysis.py`, and `apemap/ingest/`.
- `database_queries.py`: Prototype database query helpers superseded by DuckDB views (`apemap/schema/02_views.sql`) and canonical analysis helpers (`apemap/analysis.py`).
- `sparql_queries.py`: Unstructured SPARQL queries superseded by the resilient, identifier-linked Wikidata client in `apemap/ingest/wikimedia.py`.
- `download_mp_images_to_assets.py`: Standalone prototype script for downloading MP portraits, superseded by direct handbook assets and canonical review artifacts.

These files have been preserved exactly as authored to ensure historical provenance and auditability. They are no longer part of the active, importable `apemap` Python package.
