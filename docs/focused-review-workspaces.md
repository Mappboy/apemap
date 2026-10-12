# Focused review workspaces

Package 0.9.0 adds compact review workspaces to the optional local Flask/Jinja
reviewer. They present the source value, current reviewed value, proposal,
comparison and available evidence together for school mappings, individual
education assertions and member corrections. The school and assertion views
show the member and attendance context; the member view groups actionable fields
under the same APH identifier. The comparison and evidence summary are aids to
inspection, not an automatic conclusion.

Install the UI extra, build or select the review database and start the server as
described in [Review decisions](review-decisions.md#optional-local-reviewer).
The reviewer uses the existing decision ledger and validation rules. This change
does not refresh source data, build a dataset release or publish one.

## School and assertion decisions

The school workspace offers four explicit outcomes:

| Outcome | Effect |
| --- | --- |
| **Confirm selected mapping** | Records the selected school relationship after preview and validation. |
| **Name is ambiguous** | Records that the name cannot identify one school; it does not choose a target. |
| **No suitable candidate** | Records that the current candidates do not fit; it does not choose a target. |
| **Needs research** | Retains the unresolved case and reason without choosing a target. |

The latter three outcomes are useful partial review results and do not assert
attendance at a candidate. At the education assertion level, **Resolve this
member's school** applies a school institution to that member's assertion while
preserving the attendance claim and its provenance. **Needs research** leaves
attendance unresolved; **Reject attendance claim** rejects the claim. Ambiguous
name and no-suitable-candidate outcomes preserve attendance while leaving the
institution unresolved. Resolve another member with the same recorded name in
that member's own assertion workspace.

The primary decision panel also offers **Different schools for different members /
Split required**. It jumps directly to links for the affected member assertions.
Choose each member's school in that assertion workspace; following the split
path does not record a global mapping or any decision.

The selected school comparison shows concise **Supports**, **Conflicts**,
**Context** and **Unknown** explanations before the candidate alternatives.
Each populated evidence group shows up to two findings with source links;
**Full evidence and provenance** retains every finding and its metadata.
Changing candidates updates both displays and excludes evidence specific to
another candidate. Normalized name similarity is labelled as context, not
attendance evidence. Full evidence tools remain available separately.

## Member fields and partial batches

The member workspace compares source, reviewed and proposed values separately for
each actionable field. Each field begins with no selected outcome. Its radio
choices are **Accept proposal**, **Reject proposal and retain current**, **Needs
research**, and **Leave unchanged**. Choose independently for each field. Preview
validates the selected set as one batch; saving appends only selected decisions.
Leaving a field unchanged does not create a decision for it, so a batch can save
one or more reviewed fields while other fields remain open. Research and rejection
preserve the relevant proposal or current value according to the existing member
decision rules; a research outcome is not an accepted correction.

## Queue position and save-next

Detail pages display a position in the current filtered queue. Queue context
includes the active view, entity, status, parliament, search text, unmatched
school toggle, page and focus preset. Available focus presets are ambiguous
schools, member identity conflicts, needs research, low evidence, unresolved and
member corrections. **Save and next** requires the signed preview, saves through
the normal validation path, then selects the first item after the current item
that still survives in the updated queue. It does not wrap to the beginning. If
no later item survives, the reviewer automatically returns to the filtered queue
with a saved notice. **Skip temporarily** records no event and no decision; it
advances the current pass while leaving the item in the queue, with no remembered
deferral state. If a detail page is no longer in the filtered queue, it says
**Outside the filtered queue**; saving does not silently change the filters.

Editing a draft invalidates its signed preview and disables its save controls
when JavaScript is enabled. Preview again after changes. The browser warns before
leaving a dirty draft. The shortcuts are **Alt+Shift+P** (previous),
**Alt+Shift+N** (next), **Alt+Shift+V** (preview) and **Alt+Shift+R** (research).
They are ignored while focus is in a text field, select, button, or editable
content, and during IME composition. Save remains an explicit button action; no
shortcut saves a decision.

The pages are ordinary server-rendered forms. Without JavaScript, users can
inspect comparisons, select outcomes, copy retained values through the ordinary
copy form, preview and save, and follow ordinary navigation links. Shortcut keys
and automatic dirty-state invalidation and warnings require JavaScript. Always
preview the current draft before saving.

## Research, inspection and retention

Research is contextual to the open review. A school case searches claims about
the recorded school identity or relationship; an education assertion case
searches that member's attendance and candidate institutions; a member correction
case identifies the exact member field, such as birth date or Wikidata ID. The
provider configuration controls which providers can be selected. Requests need
configured credentials and network access, may incur provider charges, and are
unavailable in offline mode. Without a configured provider, manual review and
retained evidence inspection remain available. See [Assisted research and
readiness](review-assisted-search.md) for setup and provider-specific limits.

Suggestions are temporary job results. They do not append a decision or retained
evidence. Open and inspect the cited public source, correct the evidence draft,
then preview and explicitly retain it. Retention appends a record to the separate
schema-1 `evidence.jsonl` store. A final decision still needs its own preview and
explicit save. The source link's presence in a provider response does not prove
that the page supports the claim; assess the page itself and cite what it
establishes. Missing facts remain unknown.

Evidence for member corrections is scoped to the exact member field. A supported
`member_value` record must name that field and contain a value accepted by its
normalizer. **Copy retained value to member draft** copies the normalized typed
value into that field's draft; it does not select Accept, preview a decision or
save one. Review the copied value and make the decision explicitly. The decision
can separately reference retained evidence through `evidence_refs`. School and
attendance evidence have their own case scopes; a source about one member must not
be treated as evidence about another member's attendance. Institution identity
and relationship evidence is distinct from evidence that a member attended it.

Supporting retained institution-identity evidence can also offer an explicit
copy action for a locally known candidate reference. Copying fills the target in
the exact school or education-assertion draft; it does not choose an outcome or
save a decision. Member-value copying remains restricted to the exact member and
field for which the claim was retained. All copy actions have ordinary form
fallbacks and work without JavaScript.

Comparison summaries and retained-evidence stances are separate displays. A
scalar comparison can report a value match, difference or unknown; a difference
alone does not establish that sources conflict. Retained records are grouped by
their recorded `supports`, `contradicts` or `contextual` stance. Known missing
dimensions are shown as gaps; their absence does not imply support or contradiction.

## Version and acceptance record

This is compatible reviewer functionality: package version 0.8.0 → 0.9.0 (minor
feature release while the package is 0.x). The package metadata and root package
entry in `uv.lock` target 0.9.0. No dataset schema, source data, dataset release
version or historical artifact is changed by the documentation or workspace
feature. Dataset publication remains a separate workflow.

Acceptance record for the issue #82 implementation (complete with final branch
validation results before handoff):

| Check | Result |
| --- | --- |
| Focused UI regressions | School/assertion research-route regressions: 3 passed; workspace Node tests: 6/6 passed. |
| Backend research/evidence/member subset | Earlier checkpoint: 179 passed; selected local-alternative subset: 4 passed. Candidate-dedup/context delta: 5 focused tests passed. |
| Full test suite | Initial full run: 1,084 collected, 1,064 passed, 14 failed, 6 skipped; one benign Windows ZMQ selector warning; 1,481.56 seconds. The current suite collects 1,085 after the dedup regression was added. The full suite was not rerun wholesale after subsequent fixes. |
| Affected-module rerun | `APEMAP_OFFLINE=1 uv run pytest tests/test_review_evidence_gui.py tests/test_review_lookup.py tests/test_review_member_gui.py tests/test_review_research.py tests/test_review_resolution_outcomes_gui.py tests/test_review_schools.py tests/test_review_member_research_nojs.py --basetemp .venv/t82r -v` — 156 collected, 156 passed, 0 failed, 0 skipped; 162.04 seconds. This covers all 12 review-related failures from the initial full run and the dedup regression. |
| Ruff lint | `uv --cache-dir .uv-cache run --frozen --no-sync ruff check .` — passed. |
| Ruff format | `uv --cache-dir .uv-cache run --frozen --no-sync ruff format --check .` — 182 files passed. |
| Scoped typing | `uv --cache-dir .uv-cache run --frozen --no-sync ty check apemap/` — passed. |
| Dependency hygiene | `uv --cache-dir .uv-cache run --frozen --no-sync deptry apemap` — 59 files passed. |
| Lock and package version | `uv --cache-dir .uv-cache lock --check --offline` — resolved 150 packages; passed. `pyproject.toml` and the root `apemap` lock entry both report 0.9.0. No dependency changes. |
| Unscoped typing | `ty check` reports 157 pre-existing diagnostics in legacy, `app/`, archive and existing test code; no new issue-82 diagnostics. The baseline is recorded in `docs/ci.md`. |
| Review ledger append-only check | `uv --cache-dir .uv-cache run --frozen --no-sync apemap review check --base origin/main` — valid; 297 events, 204 effective, append-only true; ledger hash `9a03ab1dd31e82cd380ebad8c9dfb44940aa9f23ec3b8658faac4bb9b8f7d62b` unchanged. Existing warnings: incomplete pinned APH inventory and explicit assertion `education:300707:cd56814316f5b0ad` mapping to `acara:49128` overrides school default `acara:49103`. |
| Input verification | The single command `uv run apemap inputs verify` fails because six ignored pinned cache files are absent locally: `data/raw/aph/individuals.json`, `data/raw/aec/2025/AUS-March-2025-esri.zip`, and `AUS_ELB_region.shp`, `.shx`, `.dbf`, `.prj` in that same AEC directory. These caches were missing at task start. The full pytest run has two affected failures, `test_preflight_offline_inputs_passes_on_repo` and `test_cli_inputs_verify`, and six AEC/spatial skips for the unavailable inputs. No cache restoration or source refresh was performed. |
| Dataset and package scope | Package 0.8.0 → 0.9.0; no dependency change and no dataset release or source refresh. |
| Repository hooks | Passed: trailing whitespace, EOF, large files, Ruff lint/format, `ty check apemap/` and deptry. |

### UI evidence

The viewport dimensions and measured outcomes below come from the browser checks.
No screenshot files were exported.

| Check | Viewport / evidence | Result |
| --- | --- | --- |
| Desktop synthetic school workspace | 1280×720. Primary comparison bounds y=498.7–628.8; six additional facts y=641.6–665.6; concise evidence y=678.4–717.2; preview button y=518.9–564.1. Decision-critical comparison, evidence and preview controls fit in the initial viewport without horizontal overflow. The live nine-dimension comparison showed normalized name, local TAS, sector, type and coordinates; preview showed one affected row and explicit protected rules. | Passed |
| Desktop synthetic member workspace | 1280×720. Preview batch button y=302.8–348.0. All field radios began unselected. Needs-research without a reason preserved selected Gender and unrelated fields. Valid preview retained Male and showed all three unresolved fields. | Passed |
| Narrow layout and research polling | 390×844 same-origin synthetic test fixture iframe (in-app viewport override unsupported). Member comparison stacked with Source, Reviewed, Proposed and Comparison labels; scroll width 375 px, action panel static and unobscured, long claim disclosure 318.6 px with internal scrolling, no console errors; polling completed in place. | Passed in fixture frame |
| No-JavaScript member workflow | Actual scripts-disabled 1280×844 browser flow: Gender Reject draft survived research, source inspection, evidence editor and separately signed retention. Explicit ordinary copy set Female override/source/reference without changing Reject. Then Needs research with notes and reviewer preview showed retained Male and three unresolved fields; Save-next returned the exhausted queue with “Saved 1 decision(s). 3 fields remain unresolved.” | Passed |
| JavaScript school research and retained evidence | Actual school flow completed search, source inspection, signed retention and explicit candidate-reference copy. The new source checkbox was checked; comparison showed one support and two gaps without replacing the page or draft. No provider or external research traffic was used. | Passed |
| Dirty draft and asynchronous research | Browser recheck confirmed selecting a candidate then starting Find sources preserves the unsaved draft at the same URL with dirty state set while polling completes. | Passed |
| Keyboard shortcuts and typing exclusions | Node tests passed 6/6. Browser check: Alt+Shift+R on the page opened the school research disclosure without submitting; the same chord in Review notes did nothing. Editing notes disabled Save mapping and Save & next and showed the draft-changed notice. | Passed |
| Browser screenshots | No screenshot files were exported. Measurements and behaviors above were recorded from the actual browser session. | Recorded without exported images |

The 12 review-related failures from the initial full run were resolved and covered
by the affected-module rerun above: three evidence-save redirect tests, two
duplicate-assertion selected-reference tests, one expanded-lookup-dictionary
expectation, three member-presentation disclosure-state helpers, one JavaScript
resolution harness and two school-presentation label tests. These fixes do not
relax the declared behavior. The full suite was not rerun wholesale after these
fixes. In the initial full run, the two failures still attributable to the
environment are the input-verification tests tied to the six absent ignored
caches; six AEC/spatial tests skip for those unavailable inputs. The standalone
input-verification command reports those same files. The narrow viewport check
used a same-origin fixture iframe because the
in-app browser viewport override was unsupported; it was not a resized production
app viewport.
