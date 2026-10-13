# Issue #77 research pilot

## Original delivery: 12 October 2026

Prepared and researched on 12 October 2026 (Australia/Hobart). These versioned
artifacts contained proposals only at delivery: **zero reviews, zero applied pilot decisions**.
Task snapshots include the existing local source and decision revisions, including
the user's uncommitted decision history. Authority ledgers are unchanged by this pilot.

| Batch | Cases | Evidence items | Attempts | Current findings |
| --- | ---: | ---: | ---: | --- |
| `issue-77-members-pilot` | 10 | 33 | 12 | 10 suggested QIDs; 7 sample eligible, 3 individual-only |
| `issue-77-schools-pilot` | 5 | 26 | 7 | 3 mappings, 1 closure, 1 unresolved |

Luna ran at medium reasoning with at most two research workers concurrently,
five members or one school per task. Each case was limited to three searches and
six source opens. Unresolved Luke Hartsuyker, Sharon Grierson, Five Dock and
St Thomas each received one Sol medium retry with two searches and four opens.
Earlier evidence and attempts remain in the proposals. Actual token usage was
unavailable and is explicitly `null`, not an estimate or zero.

The frozen member sample contains five of seven eligible proposals. Inspect all
attached sources for those five before explicitly approving the named eligible
set. Dutton, Ley and Farmer need individual review: official page retrieval gaps
and name/fact differences prevent sample eligibility. APH indexed search evidence
is labelled separately from successfully opened pages and retained local context.

All five schools need individual disposition of every evidence item:

- **MacKillop College:** proposed rename to Werribee's MacKillop Catholic Regional
  College (`acara:46002`). Historical rename evidence is on a UAT site and needs
  verification; the proposal preserves that caveat.
- **Geilston Bay High School:** documented 2013 closure, no current-school target.
  A closed manual institution is proposed. Exact closure day conflicts (9 versus
  19 December); later Archives use of the site does not establish a successor.
- **St Andrew Muar Johor:** proposed Malaysian manual secondary institution,
  distinguished from the similarly named primary school.
- **Five Dock:** the Sol retry found Rosebank College's official alumni newsletter
  naming Fiona Martin as a graduate, plus ACARA identification (`acara:43664`).
  Earlier Rosebank/Domremy alternatives and locality-only evidence are retained.
- **St Thomas:** unresolved after retry. The abbreviated source's secondary label
  conflicts with the biography identifying Saint Thomas à Becket as primary.
  No institution mapping or attendance correction is proposed.

Prospective effects, conditional on human review, are ten member Wikidata
corrections, three school mappings, two manual definitions and one retained
closure conclusion (16 ledger events). St Thomas remains unresolved. These are
proposal counts, not measured queue reductions. At delivery the local canonical
projection could not validate because existing decision `school:5456673f9606de0b`
referenced `acara:40335`, absent from the supplied local register. Existing history
was preserved. Exact queue effects must be previewed after that baseline issue
is resolved; no dataset release was published.

Evidence records preserve source titles, URLs, retrieval times, claims and brief
quoted excerpts or paraphrases, with agent provenance. Sources include public
parliamentary records, Wikidata, schools and education directories; they remain
individually attributed in each proposal. See
[the workflow guide](../../../../docs/guided-research-batches.md) for commands,
review rules and separate evidence/decision application.

## Reviewed follow-up: 13 October 2026

Cam recorded six accepted member reviews: Zoe Daniel, Anthony Byrne, George
Brandis, Kerryn Phelps, Tony Windsor and Luke Hartsuyker. All five sample items
passed, but this follow-up applies only individually accepted proposals. Peter
Dutton, Sussan Ley, Patrick Farmer and Sharon Grierson remain unreviewed.

Four school proposals have individual acceptance: MacKillop College's rename,
Geilston Bay High School's closure, St Andrew Muar Johor's manual mapping and
Five Dock's Rosebank College mapping. St Thomas remains unreviewed and unresolved.
The original proposals, prior attempts and saved source inspection records remain
intact.

The original source hash still reproduces with `data/external`. The frozen decision
revision is an exact 324-event prefix of the 326-event ledger audited on 13 October.
Only unrelated Beerwah and Cheltenham mappings were appended; all ten accepted
proposal decision heads and both proposed manual-definition heads are unchanged.
The 21 approved member evidence records were already retained at audit; the 19
approved school evidence records still required retention.

The explicit pinned register at `data/raw/historical/acara` contains Alice Springs
High School's `acara:40335` profiles for 2008-2010. Its retained CSV and JSON hashes
match `data/historical-inputs-manifest.json`, and it validates the existing ledger.
Revalidation compares the reproduced original context with that register before
making a fresh approval preview. Approved member identity and service facts,
school attendance context, and target identity, sector, state, type and location
remain unchanged. MacKillop and Rosebank's register status is enriched from
`unknown` to `current`.

The selected subset was applied on 13 October 2026 as **12 ledger events**: six
QID corrections, three school mappings, two manual definitions and one closure
research event. The six accepted member proposals and all four accepted school
proposals are applied. Retention appended 19 school evidence records; the 21 member
records were already retained and deduplicated without another append. Copied-ledger
rehearsal and actual application produced the same measured results, and repeating
each exact application appended zero events.

| Measured local result | Before | After |
| --- | ---: | ---: |
| Pending member cases | 626 | 620 |
| Pending school cases | 85 | 81 |
| Accepted school cases | 219 | 222 |
| School cases needing individual resolution | 1 | 2 |
| Member/school decision conflicts | 0 | 0 |
| Members missing Wikidata IDs | 580 | 574 |
| Unresolved attendance assertions | 29 | 26 |
| Unresolved institutions | 25 | 22 |

All 580 members, 352 attendance assertions and 1,754 service intervals were
preserved. These counts come from actual receipts and regenerated local queues,
not the earlier projections. The measured local ledger grew from 326 to 338 events
and preserved every prior byte as an exact prefix. Its baseline includes an
unrelated, pre-existing uncommitted Cheltenham mapping, which is preserved locally
but excluded from this PR. The committed PR ledger therefore grows from 325 to 337
events; the local absolute queue counts above are not clean-checkout counts.

Application receipts preserve the approved hashes and measured queues:

- [Member receipt](issue-77-members-pilot/application-295322d09e7ae7928234a94dbcf0f00d74d3a9ddad62d129c46e56ae712ca99b.json),
  approval hash `295322d09e7ae7928234a94dbcf0f00d74d3a9ddad62d129c46e56ae712ca99b`.
- [School receipt](issue-77-schools-pilot/application-c3409cb7e1b19ca5171ab0ea4dc27b824277723c78c3d215140554c9c9b5aef8.json),
  approval hash `c3409cb7e1b19ca5171ab0ea4dc27b824277723c78c3d215140554c9c9b5aef8`.

Geilston remains unresolved for mapping purposes. Its accepted closure finding
retains a closed manual institution and a research-needed event without inventing
a successor or attaching the attendance assertion to a current school. Its queue
status is `needs_individual_review`, separate from the three accepted mappings.

Follow the [revalidation workflow](../../../../docs/guided-research-batches.md#revalidate-saved-reviews-against-current-inputs)
with the individually accepted scope. Applying decisions does not publish a dataset
or update a released website. The [acceptance record](../../../../docs/issue-77-acceptance.md)
records source provenance, remaining cases and validation.
