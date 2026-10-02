# Explorer contract handoff

APEMAP supplies the pinned data, reference filtering behaviour and offline audit
for #12. Implement the production MapLibre island, routing, self-hosted basemap,
Cloudflare/R2 and mobile runtime performance checks in cpoole-dev.

## Join and filter rules

- Read selected terms from `web/metadata.json`. Default to its highest available
  parliament; opening-day service is the common chart/list/map cohort.
- `web/members.json` has one person per parliament. Its `schools` can contain
  several attendance assertions; deduplicate by institution ID for list display.
  Members without recorded schooling have an empty schools array and stay visible
  when no school-sector filter is active.
- Union those member-school records for the complete school list. Enrich mapped
  IDs from `web/schools.geojson`; retain unmapped IDs with name, sector and state.
  Retrieve additional unmapped profiles from research tables at build time only
  if needed. Do not infer a profile from another school’s coordinates/name.
- Party and chamber belong to a parliament service. When using GeoJSON arrays,
  require parliament, party and chamber to match the **same service entry**. Do
  not match party from one term and chamber from another. The reference uses the
  selected term’s member records directly.
- Sector filters describe institutions: Government, Catholic, Independent, Other.
  Mixed and Combined/Multiple are person classifications, not school sectors.
- Search matches a school name or member name, case-insensitively with Unicode
  normalization. Member search returns their matching schools; school search
  returns the school and its cohort members. No school-sector filter means members
  without schooling can still match a member-name search.
- Invalid URL options reset; valid selections survive reload and compatible
  filters. Clear a selected institution when it is excluded. Keep editorials
  independent of explorer filters.

The #33 prototype exposes `ApemapExplorer.parseState`, `encodeState` and `select`
as a plain JavaScript reference. It embeds one release and requires no runtime
backend, DuckDB-Wasm or external map service.

## Deterministic examples

`tests/prototype.test.cjs` uses two synthetic terms and two schools:

| Selection | Expected result |
| --- | --- |
| 47 + ALP + senate | Two schools; Changing member appears once at each school |
| Same filters switched to 48 | Zero matches: that person is IND/representatives in 48 |
| 47, unfiltered | Two schools and two people, including No schooling |
| 47 + Independent + search “changing” | Independent school and its one member |
| 47 + Independent + search “government” | No matches |
| 47 + search “government” | Government school, retaining unavailable coordinates |
| Unsupported parliament/party/chamber/Mixed sector | Latest parliament with invalid filters cleared |

Duplicate attendance records do not duplicate markers or member details. Historical
institutions and long/unknown names use the same ID-based join. Missing coordinates
never imply missing attendance or change national denominators.

## Audit and measurement

```text
uv run python -m apemap.explorer_contract data/processed/releases/0.3.1-candidate --report data/processed/explorer-audit.json
uv run pytest tests/test_explorer_contract.py tests/test_prototype.py
node --test tests/prototype.test.cjs
```

The audit reconciles distinct people, classifications, represented schools,
mapped/unmapped counts, school/member arrays, service context and per-term layers.
It requires complete headline/detail classification counts, reconciles known and
missing schooling denominators, and checks every expected member/term relationship
in both the aggregate and term-specific maps. Party names remain valid when the
source has no abbreviation.
It records raw and gzip bytes for each web asset. Run strict release verification
first: the semantic audit does not replace hash, privacy or geometry verification.

Keep school points as GeoJSON for this handoff. Load only the chosen term’s map
layer when useful, and measure browser parsing and mobile interaction in cpoole-dev
before considering point tiling. The 32 MB research archive is not an initial
page-load asset. Gzip figures describe compression potential, not observed HTTP
transfer until hosting compression is verified.

The populated candidate passes the audit for all seven terms. For the 48th it
contains 226 people, 212 represented institutions, 113 mapped and 99 unmapped.

| Asset | Raw bytes | Gzip bytes |
| --- | ---: | ---: |
| `web/results-summary.json` | 21,339 | 2,467 |
| `web/members.json` | 1,189,022 | 98,488 |
| `web/schools.geojson` | 657,716 | 46,268 |
| `web/parliament_48_combined.geojson` | 348,641 | 24,006 |

Browser parsing/interaction measurements and HTTP compression verification remain
production frontend work; these measurements justify retaining the simpler points
format for the APEMAP handoff.

## Accessibility and failure states

Mobile starts with search/list. Maps activate explicitly; never load MapLibre into
the static editorial experience. Keep keyboard controls, focus movement to selected
details, complete HTML tables, no-results messaging and a usable map-unavailable
state. Profile nulls read “Unavailable”; show the actual profile year. ICSEA is
context, not performance. Respect reduced motion and keep essential facts out of
hover-only popups. The prototype exercises these states using an offline SVG
locator; it does not claim a production WebGL or Lighthouse audit.
