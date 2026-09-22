# Reproducible analysis workflow

The numbered Jupyter notebooks are the canonical narrative analysis. They use
the read-only canonical DuckDB database and import calculations from
`apemap.analysis`; they do not mutate source data or make network requests.

## Run the notebooks

From the repository root:

```bash
uv sync --extra analysis
uv run jupyter lab
```

Set `APEMAP_DB_PATH` when analysing a database outside the default
`data/aped.duckdb` path. The database must already have the canonical schema
and data loaded. Static chart JSON is written by the CLI/export layer to
`data/processed/analysis/`.

The archived pre-migration notebooks are preserved under
`archive/notebooks/2026-09-22/` and should not be used as the current
analytical implementation.

## Notebook map

- `00_data_overview.ipynb` — database, snapshot dates, row counts, and coverage.
- `01_demographics.ipynb` — fixed-date ages, brackets, chamber, gender, and party.
- `02_education_sectors.ipynb` — unique parliamentarians versus attendance instances.
- `03_school_finance.ipynb` — NULL-aware 2021 finance means, medians, and samples.
- `04_parliament_comparison.ipynb` — shared summaries across supported parliaments.
- `marimo/analysis.py` — optional interactive companion using the same API.

Notebook smoke tests run top-to-bottom against a deterministic temporary fixture:

```bash
uv run pytest tests/test_notebook_execution.py
```
