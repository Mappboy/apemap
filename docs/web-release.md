# Website release contract

Supported terms now come from canonical metadata (42–48). The
[historical guide](historical-coverage.md) describes the populated release and
cpoole.dev follow-up. Summary and metadata declare supported terms, selected terms,
opening dates and descriptions. Public releases contain one school-feature
`parliament_<number>_combined.geojson` per term, with source/confidence in
`education_assertions`, service context, finance years and a temporal warning.

Generate the browser and research files from the canonical database:

```bash
uv run apemap transform --db-path data/aped.duckdb
uv run apemap validate --db-path data/aped.duckdb -p "46,47,48" --strict
uv run apemap export --db-path data/aped.duckdb -p "46,47,48" \
  --web-release --data-release-version 2026.09.30 \
  --output-dir data/processed/releases/2026.09.30
```

Use a new release directory for each published data version and pin that version
in the frontend. The exporter writes files locally; uploading and publishing are
separate steps. Plain `apemap export` retains the existing canonical output set.

## Cohort and row grain

All website attendance outputs use `is_opening_day_member = TRUE` for the selected
parliaments. Later service records are excluded. Missing secondary education still
contributes to the summary's total member count, but produces no attendance row.

| File | Row or feature grain | Profile selection |
| :--- | :--- | :--- |
| `results-summary.json` | One result object per selected parliament | Source years of the latest profile per represented school, including unmapped schools |
| `schools.geojson` | One feature per represented school with coordinates | Latest available annual profile per school; missing profile fields are null |
| `downloads/parliament-education.csv` | One row per `(education_id, service_id)` in the opening-day cohort | Latest available profile per school; a missing profile retains the attendance row |
| `downloads/school-profiles.parquet` | One row per `(institution_id, snapshot_year)` across the canonical profile table | All years, including schools outside the selected cohort |
| `manifest.json` | One release manifest | Version, commit, generation time, recorded source dates, file paths, sizes and SHA-256 hashes |

A member with several school records contributes several CSV rows. Annual profile
history does not multiply those rows. A member serving in two selected parliaments
has separate service context in each parliament. Count distinct members and
institutions when reconciling the CSV with person and school denominators.

The canonical `v_member_secondary_education` view retains all annual profile rows
for research compatibility. The web CSV deliberately selects the latest profile.

## Member context in the school explorer

`member_count` counts distinct people at a school across the selected parliaments.
Each entry in `members` contains `member_id`, `name`, sorted `parliaments`, and
`services`. Party and chamber belong to each service, rather than to the person:

```json
{
  "member_id": "example-member",
  "name": "Example Member",
  "parliaments": [47, 48],
  "services": [
    {"service_id": "example-47", "parliament_number": 47, "party": "Labor", "party_abbrev": "ALP", "chamber": "representatives"},
    {"service_id": "example-48", "parliament_number": 48, "party": "Independent", "party_abbrev": "IND", "chamber": "senate"}
  ]
}
```

Explorer party/chamber filters must match the same service entry as the selected
parliament. Multiple education assertions for the same school do not duplicate
the member or their service entries.

## Source years and release provenance

Each summary's `source_years.profile_years` is the sorted set of latest available
profile years for its represented schools. `profile_year` is populated only when
that set contains one year; it is null for mixed or missing years. A school's
`profile_year` in GeoJSON and the CSV's `snapshot_year` identify the actual selected
profile. Profile values describe that year's student population, rather than the
school at the time a parliamentarian attended.

`source_years.abs_benchmark_year` comes from the benchmark results actually included
in the summary. It is null when the selected ABS comparison is unavailable.

`source_commit` defaults to the APEMAP source checkout's HEAD, independently of
the shell working directory. Installed packages without that checkout report
`unknown`; supply `--source-commit` when building from an archive or wheel.

Supply recorded upstream snapshot/retrieval dates as a JSON object through
`--source-snapshot-dates source-dates.json`. Values must be `YYYY-MM-DD` or null.
For example, this object explicitly states that the dates have not been recorded:

```json
{"aph": null, "acara": null, "abs": null, "aec": null}
```

Replace nulls with dates from the source acquisition records when available.
The manifest retains null for unrecorded source dates. These dates are independent
of profile reporting years and the release generation timestamp.

For repeatable bundle bytes, set `SOURCE_DATE_EPOCH` to the same release timestamp
and use the same source commit, data, source dates, and release version. Python
callers can also supply `generated_at` to `export_web_release_bundle()`.

## Schema upgrades and validation

Run `apemap transform` before validating an older database. Added profile columns
are nullable, and existing enrolment, ICSEA and legacy JSON values are preserved.
Parquet rebuilds match columns by name, allowing both upgraded exports and older
five-column snapshot files to load into a fresh database.

Each profile validation query records its own result. Missing columns or query
failures fail validation and include a migration instruction; the remaining
checks still run. Source nulls are allowed, and SEA sums are evaluated only when
all four percentages are present.
