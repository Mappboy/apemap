# Saturday Paper education survey reconciliation — issue 104

This delivery compares **all 18 respondents** in Karen Barlow's
[28 June 2025 survey](https://www.thesaturdaypaper.com.au/news/politics/2025/06/28/exclusive-labor-has-first-left-majority-caucus)
with the current decision/evidence-projected historical database, not an old CSV
export. Matching uses an exact APH person plus the reported chamber/seat/state;
all 18 match the Parliament 48 opening-day cohort. This is not a new audit of
political service as of October 2026.

Open the ordinary **Research batches** page at
`/research-batches/issue-104-survey-20261019`. Its 27 proposals concern source
attribution, not executable attendance or mapping decisions. Package 0.17.0 blocks
preview, revalidation and application for this purpose, including the generic
`apply-preview` validation route. The reviewer exposes source inspection and
retention, with an explicit inspector, while hiding decision controls.

## Approval status

The user explicitly approved the exact source-only preview on **19 October
2026**. All **29 records are retained**: 27 survey claims approved individually
through the normal batch review API, plus two separately previewed contextual
histories. Source inspection is attributed to `Codex source inspection (issue
104)`; the user's approval is recorded separately, without impersonating them.

`evidence-preview.json` records the exact approved append, with SHA-256
`05905d917892a93dead2a6d4bb3ce251fdc235503c5a857cfd1e01ddb16766c3`.
`retention.json` records the explicit approval, retained IDs, before/after ledger
hashes and separate history-source inspections. The original ledger is an exact
byte prefix; exactly 29 records were appended. The earlier blocked attempt made
no changes and was retried only after the user's approval.

There are **zero attendance or mapping decisions applied**. Source approval does
not approve attendance, aliases, mappings, dates, sectors or geographic claims.

## Results

- 31 named institutions extracted: **27 school/unclear-stage claims**, four
  tertiary institutions kept outside school proposals.
- **Eight exact current assertions** have direct survey corroboration; three
  additional reported/recorded name variants remain unapproved identity links.
- **Five secondary attendance additions** are proposed for Julie-Ann Campbell,
  Ali France (two schools), Matt Gregg and Rowan Holzberger. No attendance is
  accepted or applied by this delivery.
- Five explicit-primary claims and six unclear-stage named claims stay
  evidence-only. The current canonical schema cannot store primary/unknown stage;
  do not pass them through generic education accepts, which assume secondary.
- Alice Jordan-Baird's sector-only answer and Matt Smith's missing answer create
  no named-school claim. The 13 people who did not complete the survey are listed
  separately, not treated as respondents or negative education evidence.
- No direct attendance contradiction was found in the manual source comparison.
  Four current assertions omitted from the survey are preserved: Claire's
  Renmark/St Mary's, Sarah's Scoresby, and Alice's Lauriston. Omission is not
  evidence that an existing assertion is false.

**Trish Cook requires historical mapping review.** The survey names Highgate;
the current rename decision points to Sorrento. Two school-owned histories
describe a campus move (1980/1980s), so a relationship is plausible, but neither
source establishes her attendance dates, Sorrento attendance or same-campus
continuity. `history-evidence.json` holds these two separate contextual sources;
they are not silently added to the 27 frozen survey source cards or used to
change the existing mapping.

Nineteen reported school names lack an exact current assertion, including the
three possible variants. These are research work items, not 19 proven new
secondary schools. The five primary, six unclear-stage and three variant cases
must not be counted as accepted additions. All institution targets remain unset.

The five additions are relative to the **current database**. Two already occur
verbatim in issue 102's unreviewed research; Rowan's locality-qualified prior name
is a third **possible** overlap. `prior-research.json` distinguishes these cases.
Do not apply both batches as additive records or silently approve a name alias.

## Artifacts and reproduction

`survey.json` retains factual names, reported stage/grade/locality facts, source
URL, publication/retrieval dates and source HTML hash. Longer expressive answers
are paraphrased, not copied. The full copyrighted article, unrelated personal
responses and images are not redistributed. `source-fields.json` records all 18
profile locators, exact institutional names and **normalized school-response
hashes**. The normalization is stated in `survey.json`.

`extract.py` reproduces those fields from a locally retrieved UTF-8 article HTML;
it verifies respondent completeness, school names and institution-associated
grade context. It does not infer calendar years or institution identity. Use a
fresh accessible copy from the source for a new reconciliation, rather than
changing this frozen snapshot to accommodate changed content.

```powershell
uv run python data/reference/review/research/issue-104-survey-20261019/extract.py --html .cache/issue-104-source.html --out .cache/issue-104-source-fields-reproduced.json
uv run python data/reference/review/research/issue-104-survey-20261019/verify.py
uv run apemap review --db-path data/aped-historical-v0.7.0.duckdb --external-dir data/raw/historical/acara serve --port 8783
```

`reconcile.py` projects the actual database and current ledgers, checks respondent
identity/service, compares exact assertions and possible variants separately,
and prepares the ordinary native batch. It refuses to replace a frozen manifest.
`comparison.md` is the readable table; `reconciliation.json` retains full current
assertion context, and `tasks.json` also captures current institutions and
applicable prior evidence. `attendance-proposals.json` is an inspectable list of
five non-executable future attendance proposals, not an import/apply payload.

`research-log.json` records the single bounded historical-school query, actual
source access failures/successes, and delegated access-accounting limits. Exact
model/token provenance is unavailable and remains null. `baseline.json` pins the
preparation inputs and unchanged database/decision ledger. `verify.py` checks
regeneration, native proposal equality, immutable IDs, source dates, primary and
tertiary exclusions, prior-research overlaps and zero canonical changes. The
retention receipt proves explicit user approval, append-only evidence,
per-source inspection and the exact 29 retained IDs; it does not authorize a
decision preview.

## Evidence versus decisions

Inspect each source and claim before recording a source review and retaining
approved evidence. Approving a source in this batch is **not** approval of a new
attendance, name alias or mapping. The additional two history sources were
separately inspected, previewed and retained through the normal evidence APIs.
No reviewer is inferred from Git identity or impersonated.

New attendance/mapping work requires a separate reviewed decision workflow and
an inspectable exact preview. In particular, a generic no-target education accept
can invoke automatic matching: do not use it as an unmapped survey attendance
import. No database rebuild, queue removal or dataset publication occurs here.
The 352 canonical attendance rows, 580 members and 1,754 service rows remain
unchanged. No school leaves an unresolved queue merely because evidence is saved.

Package **0.16.0 → 0.17.0** adds the compatible evidence-only batch safety guard;
the package and root lock versions move together. Dataset stays **0.7.0**.
Later approved corrections require a fresh dataset PATCH release and separate
rebuild/publication authorization. Historical manifests and issue 102's frozen
records are not rewritten; a real evidence append intentionally makes its old
unchanged-authority delivery verifier stale.
