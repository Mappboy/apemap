# Website release contract

The [explorer integration handoff](explorer-handoff.md) describes the complete
member/list join, service-aware filter examples and offline semantic audit.

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

New exports declare web schema **2.0.0**. This is a breaking join migration: a
school feature's `institution_id` is its attended-school key (also exposed as
`attended_school_id`), while `resolved_institution_id` identifies the current
institution supplying profile and finance context. Two predecessors sharing a
successor remain distinct schools, even when their display points coincide.
Canonical `member_education.institution_id` retains the resolved reference.
Existing 1.0.0 release files, manifests and hashes remain unchanged and can still
be verified. Consumers must pin a new release and migrate joins together.

The [successor context policy](successor-context.md) defines the evidence rules,
independent uncertainty flags and conservative sector interpretation.

All website attendance outputs use `is_opening_day_member = TRUE` for the selected
parliaments. Later service records are excluded. Missing secondary education still
contributes to the summary's total member count, but produces no attendance row.

| File | Row or feature grain | Profile selection |
| :--- | :--- | :--- |
| `results-summary.json` | One result object per selected parliament | Source years of the latest profile per represented school, including unmapped schools |
| `schools.geojson` | One feature per distinct attended school with display coordinates | Latest available annual profile from `profile_institution_id`; missing fields are null |
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

`members.json` retains every attendance assertion, including schools without map
coordinates. Its school records carry the same context fields and actual profile
year as the mapped records; a missing map point does not discard attendance or
the provider's profile. Deduplicate school lists by `institution_id`, people by
`member_id`, and service entries by `service_id`.

Successor records expose attended and resolved names, `display_school_name`
(`Original → Successor*`), `identity_basis`, `location_basis`, broad and detailed
sector values with their separate bases, evidence URLs, and profile/finance
provider IDs and bases. `location_institution_id` identifies the point's provider
when known. Original verified coordinates take priority; their locality fields
are unavailable when no original institution reference supplies locality metadata.
Explicit `resolved_state`, `resolved_suburb` and `resolved_postcode` remain current
reference context, independently of historical attendance geography.

`successor_unverified` means the display point is a successor campus, with
`attendance_location_eligible=false` and null attendance coordinates. Draw this
point hollow and display `location_warning`; it contributes no attendance
geography. Location evidence does not change attendance confidence, sector
verification or finance status. When an attended identity has several reporting
counterparts, `provider_contexts` lists each actual provider and year. Each
`education_assertion` keeps its own complete context; ambiguous aggregate provider
IDs and values are null with `multiple_providers` bases. Independently verified
original coordinates still take priority. Conflicting points at the same evidence
strength suppress the aggregate point while retaining all searchable assertions.

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

For successors, `profile_basis` and `finance_basis` are `successor_context`;
`profile_institution_id` and `finance_institution_id` identify the provider.
`finance_year`, status, method and source remain separate from `profile_year` and
attendance dates. Sector summaries export `successor_sensitivity`, comparing the
baseline with classification excluding broad successor assumptions on identical
people and recorded-school denominators.

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
