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

These are research artifacts for individual human inspection. They do not
change either authority ledger, rebuild canonical data, or publish a dataset.
Every attached school source needs an individual disposition before acceptance.
The evidence workflow is documented in
[Guided research batches](../../../../../docs/guided-research-batches.md).

Package version remains 0.10.2: research artifacts do not change package
behavior. No dataset release is built or published by this run.
