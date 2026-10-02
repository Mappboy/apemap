# Frontend dataset candidate v0.3.1

This is an unpublished, verified APEMAP candidate for #11. It follows the #33
Editorial reference and supplies the existing #12 explorer contract. Production
frontend routes, hosting and live-map measurements remain in cpoole-dev.

## Build and verify

Use a clean source checkout, restore the pinned APH/AEC inputs, acquire the pinned
historical ACARA files according to `docs/historical-coverage.md`, and verify
`data/historical-inputs-manifest.json`. Do not refresh upstream data during replay.

```text
uv run python -m apemap.historical --db-path data/candidate-first.duckdb --output-dir data/processed/releases/0.3.1-candidate --version 0.3.1
uv run python -m apemap.historical --db-path data/candidate-replay.duckdb --output-dir data/processed/releases/0.3.1-replay --version 0.3.1
uv run apemap release verify data/processed/releases/0.3.1-candidate --strict-assertions
uv run apemap release diff data/processed/releases/0.3.1-candidate data/processed/releases/0.3.1-replay --json
uv run python -m apemap.release.package data/processed/releases/0.3.1-candidate --output-dir data/processed/candidates/0.3.1
```

Both builds must use the same source commit and fixed input-manifest timestamp.
Packaging sorts paths and normalizes tar ownership, permissions, timestamps and
gzip headers. It refuses to overwrite an existing archive. `SHA256SUMS` verifies
the bundle payload; `SHA256SUMS.dist` verifies the downloadable archive. No new
manifest format, publication workflow or canonical schema is introduced.

## Consumption order

1. Download the candidate archive and verify `SHA256SUMS.dist` before extracting.
2. Run strict read-back verification, then pin the exact version/source commit.
3. Read `web/metadata.json` for the selected parliaments, opening dates,
   `web_schema_version`, cohort and temporal warning. Use manifest snapshot dates
   separately from generation time; the APH source date remains unknown/null.
4. Build headline/history findings from `web/results-summary.json`; use
   `analysis/party_sectors.json`, **`analysis/chamber_sectors.json`**,
   `analysis/demographics.json` and `analysis/shared_schools.json` for detail.
5. Use `analysis/parliament_coverage.json` for verified/provisional coverage,
   keeping its definition separate from recorded-school sector denominators.
6. Enhance lookup from `web/members.json` and `web/schools.geojson`. Research
   CSV/Parquet downloads remain separate from display geometry and initial assets.

All school-point assets retain one feature per institution. Missing coordinates
never remove people from analytical denominators. Profile/finance source years
are not attendance-era dates. This candidate does not acquire Finance 2024.

The original v0.3.0 bundle and draft preview remain unchanged. Publication is a
separate main-branch operation; this candidate is supplied only as a draft preview.
The adjacent candidate record and manifest pin the delivered bytes and source.

## Delivered candidate

Both independent builds pass **217/217 checks across 50 files**. All payloads,
inventories and packaged archive bytes match. The archive is **32,545,583 bytes**;
SHA-256 is `2a9e52f3c476db4066da2df0e10cf8d972a08b068c8f222d1506b9e723d7cc3c`.
The data source commit is `7e566d732f60283c86326aa761d3531327bc43e2`.

See [candidate-record.json](candidate-record.json), [manifest.json](manifest.json)
and [SHA256SUMS](SHA256SUMS). The manifest pins every file. The input acquisition
timestamp is fixed; APH’s original upstream retrieval date remains unknown.
