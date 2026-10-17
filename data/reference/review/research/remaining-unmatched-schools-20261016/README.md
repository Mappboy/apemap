# Remaining unmatched schools, all parliaments

Prepared on 16 October 2026 from every name in the live Unmatched schools queue,
without a parliament filter. Open the ordinary [Research batches list](http://localhost:8765/research-batches)
and select `remaining-unmatched-schools-20261016`, or open
[the normal batch workspace](http://localhost:8765/research-batches/remaining-unmatched-schools-20261016).

The **27 school descriptions** contain **28 outstanding member attendance
records**. MLC remains in the queue because of its authoritative shared-name
rejection, although its three individual mappings are already accepted. Those
three records are included as explicitly labelled supplemental verification
cases, giving **31 researched proposals** in the normal batch.

There are **16 proposed mappings, 15 unresolved findings and 92 evidence records**.
One mapping includes a separately sourced historical institution definition.
All proposals require individual source inspection and review. At delivery,
there are zero reviews and zero applied proposals in this batch. Existing
decisions, retained evidence, source database and historical research are unchanged.

| Recorded school | Member | Finding |
| --- | --- | --- |
| Assumption College | Damian Drum | Kilmore, `acara:45647`; own boarding-school recollection, biography mirror and college identity |
| Box Hill Technical College | Steve Irons | Unresolved College-versus-School identity and possible tertiary classification |
| Bunbury Techincal School | Nola Marino | Unresolved historical identity and school level; original spelling preserved |
| Corpus Christi College | Emma McBride | Unresolved Tuggerah predecessor lead; suggested Bateman WA target is unsupported |
| De La Salle College | Fraser Anning | Unresolved campus identity |
| De La Salle College | Chris Hayes | Revesby Heights, `acara:43472`; Lasallian alumni publication contradicts the suggested Malvern target |
| Fitzroy and Collingwood Secondary Schools | Lidia Thorpe | Unresolved combined description; no unsupported split into modern schools |
| Gosford High School | Pat Conroy | `acara:42090`; speaker biography and official school identity |
| Gosford High School | Tyron Whitten | `acara:42090`; pinned APH attendance and official identity; no second person-specific source retrieved |
| Haileybury College | Alan Tudge | `acara:46189`; college entity supported by interview and school history, no personal campus or attendance dates added |
| Home educated | Jordon Steele-John | Primary oral history confirms home education; no institutional target exists |
| John Paul College | Jessica Collins | Unresolved person-specific campus evidence, including maiden-name/New Zealand lead |
| Marist College, Canberra | Daniel Mulino | Pearce, `acara:40333`; exact APH name and school identity; newsletter does not independently assign him the alumnus role |
| MLC | Mary Aldred | Supplemental verification unresolved; current Kew acceptance remains authoritative |
| MLC | Marise Payne | Supplemental primary verification incomplete; current Burwood acceptance remains authoritative and secondary alumni lead is retained |
| MLC | Kate Thwaites | Supplemental verification unresolved; current Kew acceptance remains authoritative |
| Newington College | Trent Zimmerman | Stanmore, `acara:43877`; secondary attendance account and official secondary-campus identity |
| Santa Sabina College | Fiona Martin | Strathfield, `acara:43673`; interview-host biography, secondary account and official school identity |
| Secondary school teacher at Bankstown Girls' School and Queanbeyan High School | Jennie George | Employment parsed as attendance; primary interview corroborates teaching work; no attendance mapping or split |
| St Bernard's College | Scott Ryan | Unresolved conflicting school accounts; school-magazine mirror lead retained |
| St Colman's School | Damian Drum | Unresolved historical Shepparton lead; suggested Mortlake primary school is the wrong level |
| St George Girls High School | Jenny Ware | Kogarah, `acara:41148`; exact source attendance and official school identity |
| St Peters Catholic College | Zed Seselja | Proposed historical St Peter's at Isabella Plains, ACT; paired APH Padua/St Peters field and official successor history; separate manual definition |
| St. Michael's Collegiate School, Hobart | Claire Chandler | `acara:40035`; school-owned alumni source |
| St Patrick's College | Luke Howarth | Unresolved campus attendance; Shorncliffe identity alone does not prove attendance |
| St Thomas | David Coleman | Unresolved primary-versus-secondary classification conflict |
| Swan Hill College | Jana Stewart | Main college, `acara:45540`; official member release and school newsletter |
| Warilla High School | Sharon Bird | `acara:42919`; own parliamentary speech |
| Wilderness School | Barbara Pocock | Medindie, `acara:49818`; own university-hosted CV and official institution identity |
| Woonona High School | Alison Byrnes | `acara:42918`; own parliamentary speech |
| Wynyard High School | Gavin Pearce | `acara:40053`; own parliamentary speech |

MLC's new findings do not invalidate the old decisions. A current biography that
omits a school is not proof of non-attendance. Accepting a new unresolved outcome
as a replacement would require explicit individual supersession and a reviewed
application preview. The shared MLC rejection is preserved. Likewise, no new
finding creates a shared-school default for other members with the same label.

## Provenance and preservation

`scope.json` freezes the 27 names, all 31 exact assertions, member identities,
parliament memberships, prior decision heads and the supplemental case labels.
`baseline.json` records source and decision revisions plus the evidence, decision
and database SHA-256 values. `manifest.json`, `tasks.json` and
`proposals-0001.json` are native batch artifacts; `results.json` contains the
imported findings and their evidence records.

`research-log.json` records **80 fresh query objects and 78 source-open attempts**,
including failures. Each exact assertion used at most three queries and six
opens. Reused fetched pages are attributed to the original fetch rather than
counted as a new request. Earlier research retains its original dates, attempts
and counts in `prior-research.json`, the referenced immutable artifacts and
`results.json`. Prior importer evidence for MLC is preserved verbatim separately
from the new session's evidence; it is not relabelled as newly researched evidence.

Source records carry publisher URLs, retrieval dates and short paraphrases;
third-party ownership and attribution remain with the cited publishers. Local
APH inspection is explicitly distinguished from live requests. ACARA identifiers
come from the pinned historical register. The historical St Peter's definition
uses field-specific official history evidence and omits coordinates and exact
opening/closing dates. No attendance dates or graduation claims are changed.

Research was performed by this Codex session and its research workers.
Exact model identifiers and token usage are unavailable and explicitly null.
The native `agent-session` origin does not invent guided Luna/Sol attempts or
relax the validation of existing guided batches. Previous frozen proposals,
reviews, applications and authority ledgers are preserved.

## Verification

From the repository root:

```text
uv run python data/reference/review/research/remaining-unmatched-schools-20261016/verify.py
uv run python data/reference/review/research/remaining-unmatched-schools-20261016/verify.py --preview-check
```

The verifier checks complete live queue coverage, exact assertion coverage,
native proposal IDs, evidence hashes, per-case budgets, historical artifact
hashes and unchanged authority/database files. The optional workflow check uses
temporary copied ledgers and synthetic approvals with explicit prior heads. It
exercises retention, decision preview and application only in that isolated
copy; no human review is fabricated in the real batch. A later change to live
authority or source scope intentionally requires a new baseline snapshot.

Delivery verification passed for all 31 proposals and all 92 evidence hashes.
The isolated workflow applied 32 synthetic events (31 assertion outcomes and one
historical institution definition), with all prior heads explicitly identified.
This is workflow validation, not a recommendation to accept all unresolved
replacements. The full offline suite passed **1,335 tests**, with three optional
browser tests skipped and one existing Windows notebook/ZMQ warning. Ruff lint,
format, maintained-code/verifier typing and the lock check passed. Repository-wide
typing still reports 158 pre-existing diagnostics. Browser verification confirmed
the normal list entry, 31/31 researched, zero reviewed/applied and the Kilmore
proposal with its source inspection controls.

Package **0.14.1 remains 0.14.1**: this checkpoint adds unapplied research data
without changing application behavior, dependencies or data contracts. There is
no new dataset release or publication. Existing published manifests and tags
remain unchanged.
