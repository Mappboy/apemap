# Final Parliament 48 school research

Research completed on 15 October 2026 (Australia/Hobart), in
`feat/manual-institution-evidence-drafts`, against the decisions at `c534834`.
The scope is the five remaining unresolved education assertions in the current
decision projection, across all Parliament 48 service records. Earlier accepted
mappings and rejected attendance assertions are excluded. Home education remains
in scope so the final inventory explains every unresolved assertion.

| Member | Recorded school | Final finding |
| --- | --- | --- |
| Matt Canavan | Chisholm Catholic College | Propose exact assertion mapping to `acara:47888`, Cornubia QLD. |
| Jason Wood | Ferntree Gully Technical School | Propose a historical manual definition and exact assertion mapping to `manual:ferntree-gully-technical-school`. |
| Lidia Thorpe | Fitzroy and Collingwood Secondary Schools | Unresolved combined label; no single target or supported split proposed. |
| Jessica Collins | John Paul College | Unresolved campus identity, including after a Jessica Bird query. |
| Jordon Steele-John | Home educated | Non-institutional education; no school target is appropriate. |

Canavan's [original 2016 interview](https://catholicleader.com.au/people/queensland-senator-matthew-canavan-found-god-in-holy-matrimony/)
explicitly identifies Cornubia. The [official college](https://www.chisholm.qld.edu.au/)
and its [2021 report](https://www.bne.catholic.edu.au/schools-curriculum/school-reporting/Annual%20Reports/2021/Annual%20Report%202021%20-%20CORNUBIA%20-%20Chisholm%20Catholic%20College.pdf)
establish the institution matching the pinned register's ACARA 47888. The similarly
named Bedford WA school, ACARA 48902, is separate. No attendance dates or graduation
year are inferred.

Wood's [own maiden speech](https://www.jasonwood.com.au/maiden_speech) establishes
attendance. The [Victorian historical State Schools Directory](https://www.vic.gov.au/glenfern-secondary-college-number-7145)
identifies No. 7145, its 1990 rename to Glenfern, and the 1997 merger with a
separately numbered school. The proposed original has no coordinates. The
directory's Glenfern address is not asserted to be valid during Wood's attendance.
The manual definition is assembled with the existing institution-fact builder;
its name, country, sector, type and status have individually linked evidence.

Thorpe's historical Fitzroy closure is confirmed by the
[government directory](https://www.vic.gov.au/fitzroy-secondary-college-number-7830).
The secondary attendance-sequence lead still lacks retrieved original reporting.
Collins's [first speech, accessed through OpenAustralia](https://www.openaustralia.org.au/senate/?id=2025-07-30.189.1),
describes moving from Queensland to NSW at 13 but names no school. This does not
identify her college or disprove earlier schooling in New Zealand. Existing
research decisions, ambiguous-name policies and contrary historical evidence
remain intact.

## Research record and review boundary

- `scope.json` freezes the current exact education identities, source rows and
  current school/assertion decision heads.
- `results.json` contains the five findings, immutable `EvidenceRecord` objects,
  two exact mapping payloads and one proposed historical institution definition.
- `prior-research.json` preserves the complete earlier results for these cases,
  including both exhausted retries and both advisory budget exceptions. Original
  evidence IDs and retrieval dates are preserved.
- `research-log.json` records this explicitly requested follow-up: 11 search
  queries and 18 source-access attempts, including failures. Each case stays
  within three queries and six accesses. Finds within an already opened page do
  not count as a new source, except the failed APH access counted conservatively.
  The unsuccessful reconstructed newspaper URL is explicitly unverified and
  supplies no evidence. Home education required no new external research.
- `inputs.json` pins the database, ledgers, APH snapshot, register, previous
  artifacts and package metadata by SHA-256.

This is a standalone research packet for individual inspection, not a guided
Luna/Sol import or a saved approval preview. This follow-up used the current
Codex session; its exact model identifier and token usage are unavailable. The
guided importer only accepts the prescribed Luna/Sol attempt sequence. No
fictional model attempts are added, and exhausted or over-budget earlier attempts
are not relabelled to pass that importer. This packet therefore does not appear
as a completed proposal revision in the Research batches UI.

Inspect the evidence, retain approved records explicitly, and use the
[exact assertion workflow](../../../../../docs/assertion-resolution.md) for any
accepted mapping. Wood's historical institution definition must also be accepted.
Generate and approve a fresh application preview against the then-current inputs.
School-wide defaults are not proposed. Neither authority ledger has been changed,
and none of these proposals has been accepted or applied.

## Verification and versions

Run the read-only verifier from the repository root:

```powershell
uv run python data/reference/review/research/p48-final-schools-20261015/verify.py
```

It checks all pinned input hashes, complete prior-result preservation, budgets,
evidence hashes, case attribution, event schemas and selected evidence references.
It replays the two proposed mappings and one definition in memory using the
canonical projector. Expected effects are exactly two changed assertions, one
additional historical institution, and unresolved assertions falling from five to
three. Those three consist of two unresolved identities and home education.
The actual ledgers and database remain unchanged.

Validation on this checkout used `uv run --no-sync`, a workspace UV cache and the
provisioned environment. The verifier passed. `ruff check .`,
`ruff format --check .`, `uv lock --check`, and `ty check apemap` plus this
packet's verifier passed. Repository-wide `ty check --output-format concise`
reported 158 existing diagnostics in legacy app/archive/notebooks and test typing;
none is in this packet or the maintained package. The full parallel pytest run
encountered a Windows native worker crash (`0xc0000008`) in DuckDB spatial setup.
After later progress ceased, the run was interrupted; no complete-suite pass is
claimed. Rerunning `tests/test_cli_pipeline.py` outside the sandbox passed all
28 tests. Focused `test_research_batches.py`, `test_manual_research_batches.py`,
`test_institution_drafts.py`, and `test_review_evidence_service.py` also passed
outside the sandbox: 91 passed, one opt-in browser test skipped.

Package and lock versions remain **0.12.0 -> 0.12.0**. Research artifacts and their
verification introduce no package behavior change. No dataset correction is
applied and no dataset version is allocated, built or published. Existing release
artifacts and tags remain intact. Sources retain linked provenance and short
paraphrases; no full pages or new raw-source redistribution are included.
