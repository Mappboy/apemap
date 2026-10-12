# Issue #77 acceptance record

The 13 October 2026 follow-up completes the existing reviewed subset from the
issue #77 pilots and improves the local research reviewer. The original research
artifacts and saved human inspections remain preserved. Application results below
come from actual receipts and regenerated local queues, not projected counts.

| Acceptance criterion | Evidence and verification |
| --- | --- |
| Research unmatched school strings | The school pilot freezes five unmatched strings with attendance context. Both original and historical matchers leave the four reviewed strings unmatched before decision application. |
| Distinguish closure, rename, merger/successor, manual mapping and unresolved outcomes | The proposal schema supports these outcomes. The pilot retains MacKillop's rename, Geilston's closure without a successor, St Andrew's manual mapping, Five Dock's alias and unresolved St Thomas. |
| Emit the existing school mapping format | Three reviewed ACARA/manual mappings were applied using existing institution references and relationship types. Canonical validation passes and mapped attendance assertions increase from 323 to 326. |
| Research missing member Wikidata IDs | Ten member cases were researched; six individually accepted QID corrections were applied. Missing QIDs decrease from 580 to 574 in the local canonical projection. |
| Distinguish members from namesakes | Saved proposals link official/Wikidata evidence to identity facts. Revalidation confirms unchanged identity, DOB, seat/chamber, party and service intervals for the six accepted members. |
| Provide reviewable batch application/export | Exact previews contain events, affected assertions, canonical changes, approval scope and revalidation evidence. Both applied batches retain measured receipts; exact replays append zero events. |
| Preserve ambiguous candidates and evidence | Versioned proposals preserve alternatives, contrary/contextual evidence and retries. Dutton, Ley, Farmer, Grierson and St Thomas remain unreviewed; no follow-up decision was applied to them. |
| Preserve existing manual decisions | All prior local ledger bytes are preserved as an exact prefix. Beerwah, the unrelated local Cheltenham mapping and existing assertion overrides remain intact; Cheltenham is not added to this PR. |
| Demonstrate reduction in regenerated review queues | Actual pending member cases decrease from 626 to 620 and pending school cases from 85 to 81. Three school cases become accepted mappings; Geilston becomes research-needed, not mapped. |

## Source and approval checks

The original `data/external` source revision still reproduces the pilot manifests.
Canonical validation uses the explicitly configured `data/raw/historical/acara`
register, whose CSV/JSON hashes match `data/historical-inputs-manifest.json`.
Alice Springs High School's historical `acara:40335` identity is present there;
the existing decision needs no replacement.

Revalidation preserves original manifest/proposal/review hashes, compares
reviewed semantic facts, and binds the current source and decision revisions into
the fresh exact preview. The two approved ACARA targets change only register
status from `unknown` to `current`. Changed identity, attendance, classification,
location, decision heads or manual definitions must block reuse of saved approval.

## Application results

The copied-ledger rehearsal and actual application agree. Six individually
accepted member proposals and four individually accepted school proposals produced
12 new events: six QID corrections, three mappings, two manual definitions and one
closure research event. No sampled remainder or unreviewed proposal was applied.
Evidence retention appended 19 approved school records and deduplicated the 21
already retained member records without appending them again.

| Measured local result | Before | After |
| --- | ---: | ---: |
| Decision events | 326 | 338 |
| Pending member cases | 626 | 620 |
| Pending school cases | 85 | 81 |
| Accepted school cases | 219 | 222 |
| School cases needing individual resolution | 1 | 2 |
| Member/school decision conflicts | 0 | 0 |
| Members missing Wikidata IDs | 580 | 574 |
| Mapped attendance assertions | 323 | 326 |
| Unresolved attendance assertions | 29 | 26 |
| Unresolved institutions | 25 | 22 |

Member, attendance and service row counts remain 580, 352 and 1,754 respectively.
School rejection count remains one. Geilston's closure remains canonically
unresolved with queue status `needs_individual_review`; its manual definition and
research event do not invent a successor or count as a mapped attendance assertion.
Dutton, Ley, Farmer, Grierson and St Thomas remain pending and unreviewed.

The local before/after summaries and regenerated queues are retained under
`.cache/issue77-completion/actual/{before,after}/`. They are verification outputs,
not source artifacts for this PR. Actual application receipts are:

- [Member receipt](../data/reference/review/research/issue-77-members-pilot/application-295322d09e7ae7928234a94dbcf0f00d74d3a9ddad62d129c46e56ae712ca99b.json),
  approval hash `295322d09e7ae7928234a94dbcf0f00d74d3a9ddad62d129c46e56ae712ca99b`.
- [School receipt](../data/reference/review/research/issue-77-schools-pilot/application-c3409cb7e1b19ca5171ab0ea4dc27b824277723c78c3d215140554c9c9b5aef8.json),
  approval hash `c3409cb7e1b19ca5171ab0ea4dc27b824277723c78c3d215140554c9c9b5aef8`.

The school preview was regenerated after member application changed the ledger;
the inspected final preview is bound to that intermediate revision. Both exact
applications were replayed successfully with zero new events.

### Local baseline provenance

The original pilot ledger revision was an exact 324-event prefix of the 326-event
local audit ledger. The two later events were unrelated Beerwah and Cheltenham
mappings. Cheltenham was already an uncommitted user change when this work began
and remains preserved locally but excluded from this PR. Consequently the measured
local ledger grows `326 -> 338`, while the committed PR ledger grows `325 -> 337`.
The local absolute queue and canonical counts above must not be presented as
clean-checkout totals. All prior local ledger bytes remain an exact prefix after
application; none were rewritten to remove this baseline distinction.

The local measured ledger revisions are
`884a5d588c51e73efc71c8e64e011ddfcaf2faa88d7502daf5a2a1aed1122ee1`
before and
`d5e3cdd7e6d3351df3457a778bcacbb567cd83ac692dc6a331bf1bf2335b8306`
after. The receipts preserve their actual approval context rather than claiming
to have been generated against a different ledger.

## Validation and release scope

The full offline CI suite passes at checkpoint `5e77134`: 1,153 tests passed and
one skipped. Code quality/dependency audits, offline pipeline execution, release
contract verification and secret checks also pass. Final-head CI results are
recorded in PR #85 after the acceptance commit.

Local verification includes Ruff lint/format checks, `uv run deptry apemap`,
`uv run ty check apemap/`, `uv lock --check`, the 19 GUI tests with both Git identity
fields empty, and the focused candidate replay/comparison tests (13 tests).
Whole-repository `uv run ty check` still reports 157 pre-existing legacy
diagnostics; it is not a passing check. A sandboxed local full-suite attempt
stalled while starting a Windows notebook kernel and was interrupted; this is
separate from the passing full offline CI run.

Package version remains `0.10.0`, the target already selected for this same
unreleased job from `0.9.0`; this follow-up does not make a second version bump.
Authoritative decision and evidence schemas are unchanged. No dataset release
was published, and the released website was not updated.

A receipt measures the exact preview append against its bound ledger baseline,
not unrelated decisions committed concurrently. A missing application receipt can
be recovered by replaying its exact saved preview and approval hash without
appending events. Recovery still checks the
preview's source and retained-evidence revisions: changing either can block
reconstruction after decisions are committed. Preserve those inputs and the exact
preview rather than creating duplicate decisions to work around a missing receipt.

See [Guided research batches](guided-research-batches.md) for the review and explicit
revalidation workflow. Decision application and local rebuilding do not publish a
dataset release or update the released website.
