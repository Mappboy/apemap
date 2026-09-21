# Legacy Packaging Archive (2026-09-21)

This directory preserves the historical packaging and configuration files:
- `pyproject.toml` (legacy Poetry format)
- `poetry.lock`
- `requirements.txt`
- `.flake8`
- `.pytest.ini`

## Reason for Migration
These files were superseded during the work on **Issue #2: Data Architecture: Design Canonical Relational Schema & Migrate to DuckDB/Parquet**.

The repository has transitioned to standard PEP 621 packaging with `uv` managing environments, dependencies, locking, formatting, linting (via Ruff), and testing (via Pytest configuration in `pyproject.toml`).

The active canonical configuration resides at the repository root in `pyproject.toml`.
