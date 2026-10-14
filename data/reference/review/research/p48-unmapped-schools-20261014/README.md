# Parliament 48 unmapped education research

Prepared on 14 October 2026 (Australia/Hobart) against package 0.10.2 and the
committed review ledgers. The scope is all parliament 48 service records,
including later entrants, rather than only the opening-day cohort.

The current decision projection contains 230 parliament 48 members and 236
education assertions. Of these, 32 assertions have `institution_resolution`
equal to `unresolved`, grouped into 30 recorded school identities. Members with
no education assertion are outside this school-identity research run.

Selection used `project_review_records` with the current decisions, retained
evidence and pinned historical ACARA register. It did not use the stale database
institution links as the eligibility authority. The historical working database
was copied from the main checkout into the ignored
`data/aped-p48-research.duckdb`; the original remains intact. `inputs.json`
records the exact database, ledger, APH and register hashes used here.

The CLI's generic school preparation has no parliament filter and excludes source
rows with ACARA targets. This batch instead freezes the explicit school IDs from
the unresolved projection, using the shared batch context builder. It includes
Marist College's individually unresolved assertions despite its source matcher
target, and Geilston Bay's reviewed closure despite the continuing mapping gap.
Each task adds `p48_unmapped_assertions` containing the precise member education
identities. The normal batch manifest and importer validate the frozen tasks and
proposals; no application code was changed to prepare the scope.

The two Marist College assertions and two Scotch College assertions must remain
separate. Other members in `attendance_context` are retained to expose the risk
of applying a school-wide target to ambiguous recorded names. Named
`assertion_proposals`, when supplied, are proposals for those exact education
identities and do not apply through a school-wide batch preview. Home education
must not be assigned an invented institution. A closure with no documented
successor can remain unmapped after its evidence is resolved.

Research uses at most two concurrent `gpt-6-luna` medium workers, one school per
task, with three searches and six source opens per school. An unresolved or
failed case may receive one `gpt-6.1-sol` medium retry, limited to two searches
and four opens. Earlier attempts and evidence remain preserved. Actual token
usage is unavailable and is recorded as null.
Every query within a batched search call counts against the per-case limit.

These are research artifacts for individual human inspection. They do not
change either authority ledger, rebuild canonical data, or publish a dataset.
Every attached school source needs an individual disposition before acceptance.
The evidence workflow is documented in
[Guided research batches](../../../../../docs/guided-research-batches.md).

Package version remains 0.10.2: research artifacts do not change package
behavior. No dataset release is built or published by this run.

The completed [active review batch](../p48-unmapped-schools-20261014-ready/README.md)
contains 28 school cases covering 30 assertions, including 14 Sol retries.
Its summary distinguishes 19 proposed assertion mappings, eight unresolved
assertions, one documented closure without a successor, one home-education
finding and one possible primary-level correction. These are prospective
findings; zero mappings were applied and both authority ledger hashes remain
unchanged.

Two Luna attempts exceeded the prescribed query budget: Ferntree Gully Technical
School used five queries and Chisholm Catholic College used four. Their actual
counts and evidence are preserved in `budget-exceptions.json` as advisory
research, outside the normal importer and approval batch. They account for the
other two assertions in the original 32-assertion scope. They were not given
fresh attempts that would conceal the excess.

`raw-luna-results.json` and `raw-sol-results.json` preserve worker outputs.
`curated-luna-results.json` preserves the earlier curation;
`curated-luna-results-corrected.json` and `curated-sol-results-corrected.json` record the
final evidence scoping, original retrieval dates, registry corrections and QA
caveats. Earlier evidence and first attempts are retained in every retry. The
two earlier review drafts are preserved under `archive/research/2026-10-14/`.

Web sources retain their linked provenance and short research notes rather than
full copied pages. The existing APH/ACARA attribution and source-use requirements
continue to apply; no new raw-source redistribution or licensing grant is made.
