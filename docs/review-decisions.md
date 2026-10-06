# Review decisions

The authoritative correction history is
`data/reference/review/decisions.jsonl`. Each line is one immutable event. The
manual institution registry is projected from definition events in that same log
and gives non-ACARA schools stable identities. APH and
ACARA remain source material; replay applies supported corrections after loading
those sources. Generated CSV queues and website exports are views, not writable
authorities.

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

## Migration and pins

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

Install `uv sync --extra review-ui` and run the review server through the review
CLI. The reviewer binds to loopback and uses the same service, validation,
locking and replay rules as the CLI. It provides member and school queues,
evidence and history, institution lookup, correction forms and a before/after
preview. Browser edits append decisions to the same ledger; there is no second
database of review authority. Existing generated CSVs remain available for offline
inspection.

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
effects when a review database is available. **Save mapping** appends a decision;
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
