# Parliament 48 school evidence: completed research run

This is the active batch for individual source inspection. It contains 28 school
cases covering 30 currently unmapped education assertions. The original scope
was 32 assertions across 30 school labels; two attempts exceeded the query
budget and are preserved separately as advisory exceptions.

Nineteen exact education mappings are proposed, eight assertions remain
unresolved after retry, and three have other findings: Geilston Bay closure
without a successor, home education, and a possible primary-level correction
for Dai Le. None of these findings has been accepted or applied. The decision
and evidence ledgers are unchanged.

`proposals-0001.json` is the frozen completed proposal revision, including all
14 Sol retries. `assertion-summary.json` covers the full 32-assertion scope and
links exact education identities to proposed targets and caveats. `tasks.json`
contains the frozen source context, including the unparsed APH school field.
Input hashes, worker outputs, earlier curations and the two budget exceptions
are preserved in the [original scope](../p48-unmapped-schools-20261014/README.md).
The two superseded drafts are archived under `archive/research/2026-10-14/`.

Research used the user-approved `gpt-6-luna` medium attempts and one
`gpt-6.1-sol` medium retry per unresolved case, with at most two concurrent
research workers. Standard attempts observe the manifest's 3/6 and 2/4
search/open limits; failures count toward those limits. Token usage is unknown
and remains null. Ferntree Gully's five-query and Chisholm's four-query Luna
attempts remain outside this approval batch with their actual counts intact.

## Findings requiring particular care

- Shared names remain individually scoped. Marist identifies Pagewood for
  Thistlethwaite and Canberra for Smith; Scotch identifies Adelaide for Wong and
  Melbourne for Dreyfus. Dreyfus's location evidence is secondary, and the cited
  newspaper could not be opened. Wong's Torrens Park campus uses a school-stage
  inference and needs human inspection.
- Hill's official first speech names Glen Waverley, supporting `acara:46133`.
  The earlier `acara:46132` candidate and immutable sources remain audit history
  and are excluded from the new mapping's selected evidence references.
- Henderson's new speaker biography explicitly connects Sacred Heart attendance
  to Geelong. The other archive establishes kindergarten attendance only;
  Sacred Heart's secondary attendance span remains unverified.
- Pagewood's historical manual definition has no historical coordinates or
  verified sector. `Other` is an unverified required-field fallback. Possible
  overlap with Corpus Christi College Maroubra remains an identity-review lead.
  Holland Park's `Other` sector is also an unverified categorical fallback.
- Hawke's raw APH field already names Cumberland High School. The derived
  `Carlingford & Hills Grammar School` label needs source/parser review; it must
  not create unsupported Carlingford High School attendance.
- Dai Le's named institution appears to be primary. Opened sources establish
  the Good Samaritan succession, but historical level evidence includes blocked
  documents and snippets. Review the level before considering a primary target.
- Conaghan's official alumni/timeline page could not be opened; its indexed
  snippet is disclosed. Rowland's attendance speech was opened through an
  OpenAustralia mirror after the official transcript failed. These are proposed
  findings with access limitations, not claims of fully verified official text.
- Stirling's current Canberra College successor does not establish continuity
  between the historical Weston campus and current Phillip coordinates.
  Historical dates and competing Fitzroy/Collingwood accounts remain preserved.
- Closed institutions and home education can remain unmapped. No school was
  invented for home education, and no site reuse was treated as succession.

## Review and application boundary

Inspect every attached institutional source and every exact member attendance
record in `assertion_evidence`. The batch UI's normal school sources and preview
do not automatically retain or apply the extra `assertion_evidence` and
`assertion_proposals` fields. Use the JSON and summary for the complete record.
Review and retain scoped evidence explicitly, accept any required manual
definition, then preview the selected exact education mapping using the
[assertion workflow](../../../../../docs/assertion-resolution.md) and
[evidence workflow](../../../../../docs/review-evidence.md).

School-wide payloads must also be inspected for their wider attendance context;
the 19 assertion proposals are not an authorization to map every person with
the same recorded school name. Follow the
[guided batch workflow](../../../../../docs/guided-research-batches.md) for
individual school reviews. No sampled school approval is available.

To inspect the frozen batch with the same local inputs:

```powershell
uv run apemap review --db-path data/aped-p48-research.duckdb --external-dir data/raw/historical/acara research-batch show p48-unmapped-schools-20261014-ready
```

The ignored database is the untouched copied historical baseline identified by
`inputs.json`, projected through the current committed decisions. Do not replace
it with a differently hashed database and claim the same source revision.

## Verification and versions

- All 28 standard results passed the shared batch importer. Evidence hashes,
  exact education identities, manual definitions and registry references were
  validated; all 19 proposed map events passed canonical
  `validate_evidence_references` with their selected evidence. No events were
  appended. Every earlier evidence record and model attempt survived the retries.
- The two budget exceptions were checked to be rejected by the standard
  importer, while their evidence and payloads were independently validated.
- All input hashes were rechecked, including the unchanged authority ledgers.
- `uv run pytest -o addopts='-ra --strict-markers' --basetemp .cache/t`:
  1,186 passed, three skipped. Skips were missing optional `nbclient`, opt-in
  browser verification, and a spatial test requiring `data/aped.duckdb`.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv lock --check` and
  `uv run ty check apemap --output-format concise` passed. Repository-wide
  `uv run ty check --output-format concise` reported 172 existing diagnostics
  in legacy app/archive/notebooks and test typing/optional dependencies.

Commands used the provisioned `.venv/Scripts/python.exe` with `uv run --no-sync`
and a workspace UV cache. The full suite needed filesystem elevation for
Windows atomic ledger replacement. Its pinned AEC fixtures were restored from
the main checkout after hash validation. A short temporary directory avoided
CLI error-text wrapping caused by long Windows paths. No application or test
code was changed to obtain these results.

Package and lock versions remain 0.10.2; evidence artifacts introduce no package
behavior change. Published dataset 0.3.3 remains intact. No dataset version is
allocated, built or published, since this run applies no data corrections.

## Assertion inventory

| Member | Recorded school | Finding | Proposed target |
| --- | --- | --- | --- |
| Jessica Collins | John Paul College | Unresolved | — |
| Richard Dowling | Geilston Bay High School | Closed; no successor | — |
| Andrew Wilkie | Christian Brothers' College | Unresolved | — |
| Alexander Hawke | Carlingford & Hills Grammar School | Unresolved | — |
| Karen Grogan | Holland Park Comprehensive | Proposed mapping | manual:holland-park-comprehensive |
| Jason Wood | Ferntree Gully Technical School | Advisory budget exception | manual:ferntree-gully-technical-school |
| Patrick Conaghan | St Paul's College, Kempsey | Proposed mapping | acara:43543 |
| Kara Cook | St Ursula's College, Yeppoon | Proposed mapping | acara:47906 |
| Louise Miller-Frost | Seymour College | Unresolved | — |
| Katherine Gallagher | Stirling College | Proposed mapping | acara:40330 |
| Lidia Thorpe | Fitzroy and Collingwood Secondary Schools | Unresolved | — |
| Matthew Thistlethwaite | Marist College | Proposed mapping | manual:marist-college-pagewood |
| David Smith | Marist College | Proposed mapping | acara:40333 |
| Robert Mitchell | Broadmeadows Technical School | Proposed mapping | manual:broadmeadows-technical-school |
| Penelope Wong | Scotch College | Proposed mapping | acara:49815 |
| Mark Dreyfus | Scotch College | Proposed mapping | acara:46172 |
| Jordon Steele-John | Home educated | Home education | — |
| Andrew Mclachlan | St Peter's College | Proposed mapping | acara:49813 |
| Daniel Tehan | Xavier College | Unresolved | — |
| Sarah Henderson | Sacred Heart College | Proposed mapping | acara:45672 |
| Matthew Canavan | Chisholm Catholic College | Advisory budget exception | acara:47888 |
| Luke Gosling | Marcellin College | Unresolved | — |
| Marion Scrymgour | St John's College | Proposed mapping | acara:40350 |
| Julian Hill | Wesley College | Proposed mapping | acara:46133 |
| Claire Clutterham | St Mary's College | Unresolved | — |
| Bridget Mckenzie | Tintern Church of England Girls Grammar School | Proposed mapping | acara:46175 |
| Barnaby Joyce | St Ignatius College | Proposed mapping | acara:43668 |
| Dai Le | St John Vianney Fairy Meadow | Review primary level | — |
| Marion Scrymgour | Our Lady of the Sacred Heart College | Proposed mapping | acara:43473 |
| Dan Repacholi | Wilson Park Secondary College | Proposed mapping | acara:45336 |
| Michelle Rowland | Our Lady of Mercy College | Proposed mapping | acara:43683 |
| Leon Rebello | St Edmunds College | Proposed mapping | acara:40334 |
