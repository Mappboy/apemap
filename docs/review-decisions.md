# Review decisions

The authoritative correction history is
`data/reference/review/decisions.jsonl`. Each line is one immutable event. The
manual institution registry is projected from definition events in that same log
and gives non-ACARA schools stable identities. APH and
ACARA remain source material; replay applies supported corrections after loading
those sources. Generated CSV queues and website exports are views, not writable
authorities.

Commands in this guide were checked against the 0.7.0 review branch on
10 October 2026. The [local 0.6.1 build record](releases/0.6.1/README.md) preserves
the earlier frozen source snapshot and its validation limits.

After saving decisions, use [Decisions to a new release](decisions-to-release.md).
Rebuild through ingestion before packaging: `release build` reads the database's
already consumed review snapshot and cannot apply newer ledger entries by itself.

## Review and replay

Use `uv run apemap review --help` to inspect the review commands. Export a queue,
inspect the candidate and evidence, then record a decision through the CLI or
optional local reviewer. An accepted decision requires an explicit reviewer,
review date; explanatory notes are optional. Source URLs are optional for school
mappings when the relationship is obvious from the available context. Other
accepted decision types require a source URL. Omit an optional URL or use an empty
string in JSON; any supplied URL must be a valid HTTP(S) URL. Rejection and research
require a reason. Institution relationship evidence is separate
from evidence that a member attended a school. Unknown graduation, location and
historical-continuity facts remain unknown.

```powershell
uv run apemap review build --db-path data/aped-review.duckdb
uv run apemap review --db-path data/aped-review.duckdb list --status pending
uv run apemap review check
```

Use `show <review-id>` and `history <review-id>` to inspect an assertion. The
`accept`, `reject`, `research` and `supersede` commands use explicit payload files
and evidence; `add-education` and `add-institution` record new assertions and
registry entries. Consult each command's `--help` for its required fields.

School relationships identify the name as recorded, an `acara:ID` or `manual:ID`
target and whether the relationship is direct, an alias, a rename or a successor. Manual
schools may be overseas, closed, or outside ACARA; they do not receive an invented
Australian identifier or map point. Their coordinates need location evidence.
Successor mappings can also name an optional `attended_institution_ref` identifying
the original school, with its independent `attended_identity_source_url`. Use an
existing historical ACARA reference or an accepted manual definition, different
from the successor. Original references group verified name aliases; without one,
the frozen recorded-name review identity remains provisional. Registry metadata
alone does not verify historical location or sector.

Historical successor payloads and guided forms accept paired
`historical_latitude`/`historical_longitude` with
`historical_location_source_url`; `campus_continuity` (`same_campus` or
`different_campus`) with `campus_continuity_source_url`;
`historical_broad_sector` (`Government` or `Non-government`) with
`historical_broad_sector_source_url`; and independently
`historical_detailed_sector` (`Catholic` or `Independent`) with
`historical_detailed_sector_source_url`. Government broad evidence also establishes
Government detail. Catholic/Independent evidence also establishes Non-government
broad sector and must not contradict explicit Government evidence.
Every supplied historical field requires `historical_scope_confirmed: true`:
evidence covers all associated attendance records and verified aliases of the
original school displayed in the preview. Mixed-era histories remain unresolved;
school-wide historical fields do not select date ranges or per-person exceptions.
The independent timing claims described in
[assisted research and readiness](review-assisted-search.md) remain reviewed
annotations and do not automatically select attendance mappings.

Consistent reviewed facts apply school-wide across verified aliases. Contradictory
location or sector facts suppress the affected dimension and produce review
diagnostics; missing facts do not override reviewed evidence. Historical sector
facts take precedence over successor assumptions, with discrepancies flagged.
Without historical evidence, successor broad sector remains an explicit working
assumption; Catholic/Independent detail remains unknown. Successor coordinates
can be displayed as context, but attendance geography requires verified original
coordinates or reviewed same-campus continuity.
The optional `institution_status` preserves a sourced `current`, `historical_only`,
`closed` or `merged` status; otherwise a definition keeps its `manual` status.
Coverage recognizes explicit foreign countries as well as the legacy overseas marker.

Member corrections cover supported demographic fields. Education decisions can
add or reject individual assertions. Service corrections replace the complete
member/parliament interval group. Keep every interval that should survive and
provide nonoverlapping, term-bounded dates. Rejection and research decisions retain
the claim and reasoning without promoting it into canonical facts.

Accepted education notes are published as assertion provenance in `reviewer_notes`.
Use them for public evidence explanations; the full ledger and reviewer identities
remain in the working review database.

Correct an existing decision by appending an explicit superseding event. Do not
edit or delete an earlier line. Conflicting terminal decisions stop validation;
they require an explicit reviewed resolution. Stable entity and assertion IDs
survive changes to matching, provenance and canonical display names.

Validate the ledger, rebuild through ingestion/replay and inspect the preview
before publishing. Replay is transactional and idempotent. The normal ingestion
paths replay accepted corrections so a fresh pipeline run reproduces them.
Existing unambiguous APH-ID Wikidata linking remains automatic; name-only candidates
and demographic discrepancies still require review.

## Existing CSV annotations

School changes, school supersessions and imports containing school events require
a saved affected-record preview before append. Supply `--preview-out` on the
proposing command, inspect the event, source revisions and affected/protected
assertions, then approve that exact file using its printed SHA-256:

```powershell
uv run apemap review --db-path data/aped.duckdb --external-dir data/raw/historical/acara accept <school-review-id> --payload <payload.json> --reviewer <name> --preview-out data/processed/review/school-preview.json
uv run apemap review --db-path data/aped.duckdb --external-dir data/raw/historical/acara apply-preview data/processed/review/school-preview.json --approve <printed-sha256>
```

Replace placeholders with actual values. `--dry-run` only shows a proposal;
it does not approve it. Changed source, candidate, evidence or decision revisions
invalidate a saved preview. A school save requires the canonical affected-assertion
inventory from a review database. Assertion-specific `map-education` remains
separate from school defaults and preserves attendance provenance.

The explicit CSV import workflow starts with a dry run. It lists proposals,
missing evidence, invalid dates and conflicts. Applying an import appends events;
it does not rewrite the CSV or automatically promote every `accepted` row. In
particular, accepted rows with blank evidence URLs still need evidence. Retain
the original CSV and use import lineage to prevent duplicate imports.

```powershell
uv run apemap review import --source data/processed/reviews/member-review.csv --reviewer Cam
# Apply only after inspecting the dry-run report.
uv run apemap review import --source data/processed/reviews/member-review.csv --reviewer Cam --apply
```

An incomplete accepted row blocks applying the import. An explicit
`--incomplete-as-research` retains that annotation as a research lead instead.
Member imports use `resolved_value`, not a generated Wikidata candidate. Education
imports retain the original `raw_school_text` assertion identity; a changed display
label requires an explicit `resolved_acara_id` or `resolved_institution_ref`.
Service imports require the complete interval list in `resolved_value` as JSON.
School imports require an explicit ACARA target or a manual institution name and
country; coordinates require a separate `address_source_url`.

The `--apply` example is for a member-only import. If proposals include school
events, export a fresh preview with `--preview-out` and use `apply-preview` after
inspection; do not treat the dry-run report as approval.

## Migration and pins

Schema initialization adds nullable successor-context fields to both canonical
education assertions and preserved `review_source_member_education` rows. Source
imports and copies insert by column name, so older fixtures remain readable. It
backfills missing recorded-school IDs with the frozen Python `school_review_id`
normalizer; existing IDs survive subsequent initialization and replay. Read-only
previews of older databases supply the same missing fields and IDs in memory,
without changing source files or the database. Replay clears superseded
historical evidence before applying the current decision, while retaining the
recorded-school identity. Blank recorded names remain unresolved.

Exact old aliases, manual education and service overrides are preserved in
`archive/reference/2026-10-05/`. The migration imports three education assertions
and four sourced school relationships. It retained all 89 unsourced aliases as
research decisions; later reviewed supersessions can resolve individual items.
Matching target metadata does not demonstrate that a historical name
and a school are related. See the [migration audit](review-migration.json) for every
target. The [independent parity report](review-migration-parity.json) compares the
archived APH pipeline, matcher and loaders with the new projection using separate
temporary databases for standard, historical and contemporary-only recipes.
It records source hashes, row counts, opening-day coverage and each declared
research-policy change. A third archived source-only projection verifies the exact
identity and metadata of retained institutions. Research exemptions require an
unresolved identity and no relationship URL. Evidence, stable IDs and all other
facts must agree.

Standard and historical recipes preserve member, service and education row counts
and opening-day coverage. The approved contemporary-only policy leaves 97
unsupported mappings unresolved. Matched secondary-school member counts fall from
179 to 134 in parliament 46, 182 to 136 in 47 and 169 to 126 in 48; education
assertions remain 243, 249 and 234 respectively.

Reproduce the archived-reference proposal audit against its pinned register with
`uv run apemap review --external-dir data/raw/historical/acara import --reviewer Cam`.

Migration IDs derive from exact original records. The importer and migration date
are new attestations; original review and retrieval dates are retained separately.
Absent historic reviewers and retrieval dates are not invented.

The working ledger and registry are mutable review inputs and are not pinned by
the static upstream input manifests. Appending a decision therefore does not
invalidate an APH/ACARA snapshot. Each ingestion captures the exact consumed
review hashes; release metadata records those hashes with the upstream source
pins. Reproduce a historical release using its captured review snapshot and source
revision. The build database retains the exact consumed JSONL and input-manifest
bytes; release metadata identifies their hashes. Retain that database or the Git
revision containing the matching ledger for reproduction. CI validates the tracked
ledger and verifies that base-branch ledger
events retain their exact serialized bytes. Independent branch appends can merge;
the event graph resolves explicitly without a line-order tie breaker.

## Optional local reviewer

Package 0.6.0 supports resolution-only decisions on individual education
assertions. **Map** preserves attendance evidence; **Needs research** withholds
institution resolution for that assertion while keeping its attendance claim.
School-wide relationships remain defaults, with explicit assertion decisions
taking precedence. The comparison panel shows other members with the same
recorded school name, their resolutions and relationship disagreements. A
school mapping preview lists which assertions use the default and which have
their own decisions. See [Assertion-level resolution](assertion-resolution.md)
for legacy fallback and scoped successor evidence. See
[retained evidence and advisory scoring](review-evidence.md) for immutable sources,
multiple evidence references and explained ranking. Assisted search and analytical
release readiness are implemented in the 0.7.0 follow-up. See
[assisted research and readiness](review-assisted-search.md) for provider choices,
source inspection, separate retention and analytical limits, and the
[acceptance audit](issue-66-acceptance.md) for implementation status.

Install `uv sync --extra review-ui` and run the review server through the review
CLI. The reviewer binds to loopback and uses the same service, validation,
locking and replay rules as the CLI. It provides member and school queues,
evidence and history, institution lookup, correction forms and a before/after
preview. Browser edits append decisions to the same ledger; there is no second
database of review authority. Existing generated CSVs remain available for offline
inspection.

To review the historical working database with the correct register:

```powershell
uv sync --extra review-ui --frozen
uv run apemap review --db-path data/aped.duckdb --external-dir data/raw/historical/acara serve
```

The server defaults to `http://127.0.0.1:8765` and four workers. Saving decisions
updates the ledger immediately; the database and release retain their consumed
snapshot until rebuilt. Close the server before replacing its database.

Package 0.8.0 groups demographic and identity candidates into one queue row and
one page per APH member. Gender aliases, valid ISO birth dates and Wikidata IDs
are compared against the effective reviewed value, with the preserved APH value
shown separately. Matching proposals and absent proposals against populated
values appear only as collapsed context. Empty fields without proposals remain
research tasks; invalid proposals and identity conflicts remain reviewable.
Every available identity/name cache, its alternatives, query, retrieval date and
field decision history can be inspected on the member page. Education and service
records link to their existing separate review forms.

Choose an action independently for each actionable field: accept, reject, needs
research or leave unchanged. Select the proposal evidence when several caches
exist. Ambiguous identities require an explicit valid value before acceptance;
rejecting or researching conflicting decision heads requires an explicit accepted
ancestor to retain. **Preview batch** shows the exact field events, combined
decision/canonical changes, validation and remaining research tasks. A compact
comparison table and status badges summarize the member before the field cards.
The preview describes each accepted or retained value in plain language; exact
events, combined changes, history and candidate alternatives remain available
under **Technical details**. **Save all
decisions** appends all selected events atomically. A review database is required
to compute the canonical preview. Edits invalidate the preview, and a changed
ledger, source/candidate evidence, projected effects or validation outcome rejects
the complete save. A busy writer permits retrying the same preview; after a stale
preview, review the retained draft and preview again. After a file error, check
history before retrying because the commit may already have reached disk.

Member batch rejection/research events use `proposal_only: true` and
`retained_decision_id` to preserve an accepted ancestor, including explicit nulls,
through subsequent proposal reviews. Without an accepted correction they retain
the source baseline. Existing withdrawal events and single-field CLI withdrawals
keep their source-fallback semantics. Events also retain reviewed candidate IDs
and the selected proposal evidence; new evidence returns to pending instead of
inheriting an earlier proposal's disposition. Queue regeneration suppresses
satisfied cases without appending cleanup events or changing historical events.
CLI/export candidates and ledger events retain their field-level formats.

The **Unmatched schools** checkbox intersects with type, status, parliament and
search filters. It excludes effective accepted mappings and individually resolved
schools, retaining unresolved and conflicting cases. Counts reflect the filtered
grouped rows before pagination. Applying or clearing filters does not change
decisions or school resolution semantics.

Package 0.7.0 accepts `serve --research-config <providers.json>`
for optional assisted research. Select a configured provider, inspect suggestions,
preview and explicitly retain evidence, then separately preview/save a decision.
Provider credentials come from environment variables named by the configuration;
suggestions remain disposable until retained. Provider/model availability must be
checked when configuring acquisition. Offline ingestion, replay and release
verification do not require provider access. `review readiness` is advisory and
projects working decisions; release `review/readiness.json` uses consumed
snapshots. Neither report certifies the research claims or automatically resolves
an assertion.

School relationship and education forms include a school-name search over the
local ACARA register selected by `--external-dir`. Choose a result to fill the
institution reference as `acara:ID`; suburb, state, sector and school type help
distinguish schools with the same name. The lookup reads pinned source files and
includes earlier names from the annual register when available. Results display
the canonical name for each distinct ACARA ID. It does not fetch ACARA data or
save a decision. Snapshot status describes register presence, not proof of closure
or historical continuity. Supply the relationship or attendance evidence and
preview the decision before saving it.

### School mapping chooser

School queues show one row per recorded-name review item, with possible-target
and unresolved-evidence-lead counts. Filters search the whole item; opening it
shows all of its evidence. Other review types retain candidate rows.

On a school page, choose **Use this school**, or **Find another school** in the
local ACARA register. Unmatched source records appear separately. Legacy targets
are earlier mapping leads needing evidence; Wikipedia/Wikidata suggestions without
confirmed institution references remain leads, never automatic ACARA matches.
Only evidence with the same explicit reference is grouped under one target.

Choose **Same school**, **Alternate name**, **Renamed school**, or **Successor
institution**, then optionally supply a supporting source URL. **Use this source** explicitly
copies an evidence link; selecting a school preserves your source, reviewer and
notes. For non-ACARA schools, enter an existing `manual:` reference, or open the
manual-institution form in another tab and save its definition first.

The school page shows associated parliamentarians with links to their Parliamentary
Handbook biographies, attendance review items and original attendance sources.
Parliamentary service includes chamber, electorate and represented state/territory;
source institution location includes known suburb, state and country. These are
source snapshot contexts, not inferred school locations. A represented state does
not establish where the member attended school. Context is unavailable without
the local review database; an omitted mapping source URL is explicit in the preview.

**Preview mapping** shows the relationship, replacements and canonical education
effects when a review database is available. Affected education assertions show
the recorded school name, falling back to the school review's name for older
records without that field. This display fallback does not alter attendance data.
**Save mapping** appends a decision;
it does not rebuild the database. The saved mapping is applied on the next source
build. Guided decisions replace all active decisions for the same review item
while retaining history. An open school form compares its decisions, candidates,
member context and visible institution metadata with the current page. Decisions
for an unrelated item do not invalidate that form. If this school's context
changes, the response refreshes the page with your draft retained and a fresh
form token; review the changes and preview again. A preview that races an unrelated
change retries once when that page context remains identical. Saving checks source
revisions under the writer lock. If other decisions have been appended since a
school preview, saving also verifies that the prior ledger bytes are unchanged,
this school's active decisions and selected manual definition are unchanged, and
the exact proposed event still produces the same decision and canonical row
effects. Independent school previews can therefore both save without repeating
the preview. Shared targets or attendance corrections that change those effects
require another preview. Dataset-wide totals describe the time of preview and
may change through independent reviews. Other decision types and older previews
retain strict global revision checks.
Changing a draft disables saving its earlier preview when
JavaScript is enabled; without JavaScript, the save button still saves only the
exact displayed preview, so preview again after edits.

**Needs research** and **Reject this mapping claim** require a reason, not a
target. Rejection applies to the review item, not to one candidate. **Technical
details** retains source JSON, history and an explicit advanced payload editor;
advanced mode uses its own action and supersession controls. The chooser and
explicit reference entry also work without JavaScript.

The service caches the compiled ACARA register by content digest and builds one
institution reference index per presentation request. Register changes invalidate
the cache even when file size and modification time are unchanged. School detail
pages generate only their own candidates; the complete queue/export behavior is
unchanged. Preview performs one canonical replay for the previous decisions and
one for the proposal. Source bytes are hashed afresh before and after preview,
and before save validation and immediately before the atomic ledger replacement.
These optimizations add no persistent index, database schema or data refresh.

Queue navigation reuses a complete in-memory presentation snapshot while source
and ledger content hashes are unchanged. Filters and pagination use that snapshot;
saving a decision or changing source bytes rebuilds it on the next queue request.
Ledger parsing is also reused by content hash, with private event copies for each
caller. Details, previews and saves do not use the queue snapshot. Local CSS and
JavaScript assets have content-versioned URLs and browser caching; review pages,
lookup responses and previews remain uncached in the browser.

The local server uses one Waitress process with four request worker threads by
default. Several browser tabs or clients can load schools, search the register
and preview decisions concurrently. Register caches publish complete snapshots;
each request has its own database connection and mutable replay state. Writes
remain serialized by the same cross-process ledger lock used by the CLI. A save
is successful only after the complete ledger has been flushed, synced and
atomically replaced. There is no staging database or later flush step.

A changed school, target, source or mapping effect returns a conflict with the
draft retained for another preview. If a writer is busy, the page retains the
exact signed preview and offers **Retry saving this decision**. File errors also
retain the draft and retry token; inspect the current history before retrying an
unconfirmed save. Retries keep the original 15-minute expiry and run the same
validation. Duplicate submissions cannot append the same decision twice. Restart
the server after upgrading; previews from the previous server must be recreated.
Rebuild the review database separately when ready to apply saved decisions.

```powershell
uv run apemap review --db-path data/aped-review.duckdb serve --port 8765 --workers 4
```

`WARNING:waitress.queue:Task queue depth is 1` means a request briefly waited
while every worker was busy. It does not mean a decision was lost. Sustained
warnings can occur when several clients preview at once; `--workers 8` provides
more request capacity, although canonical preview validation still takes work.
Restart after upgrading to pick up queue and asset caching. Keep the preview and
save steps, and use the retry button when a writer is busy.
