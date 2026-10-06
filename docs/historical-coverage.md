# Historical coverage: 42nd–48th Parliaments

Issue [30](https://github.com/Mappboy/apemap/issues/30) extends the canonical
dataset to the opening of the 42nd Parliament on 12 February 2008. Terms are
defined in `apemap.constants.PARLIAMENT_METADATA`; package defaults iterate that
metadata. `current_parliament()` identifies its single open term.

## Membership and evidence

APH IDs identify people across terms. Dated `PartyParliamentaryService`, nested
party histories and `ElectorateService` identify service intervals. Intersections
with each parliament preserve changes of party, seat and chamber, late entry,
departures and gaps. Later starts win on shared transition dates. Inclusive end
dates are clipped to dissolution, independently of election dates. APH's
`1900-01-01` end sentinel means open-ended service. Current records may end at
the API snapshot date; original dates remain in separate provenance fields.
Only the latest segment in the open parliament can be current.

The principal cohort is distinct people serving on the opening date. The
House/Senate occupied-seat benchmarks are 150/76 for 42, 43 and 45; 150/74 for 44;
151/76 for 46 and 47; and 150/76 for 48. The 44th Senate had two casual vacancies,
rather than two missing ingestion records. Chronology comes from the
[APH dissolution chronology](https://www.aph.gov.au/About_Parliament/House_of_Representatives/Powers_practice_and_procedure/00_-_Infosheets/Infosheet_25_-_Prorogation_and_dissolution).
The cached APH dated histories reproduce these occupied totals. Historical seats
are not attached to 2025 boundaries: joins use the election year in metadata,
and unavailable historical boundaries stay absent.

The two 44th vacancies were Carr's and Joyce's: O'Neill was appointed on
[13 November 2013](https://www.aph.gov.au/Senators_and_Members/Parliamentarian?MPID=140651)
and O'Sullivan on
[11 February 2014](https://www.aph.gov.au/~/media/05%20About%20Parliament/54%20Parliamentary%20Depts/544%20Parliamentary%20Library/Handbook/handbook_44th_parliament.pdf),
both after opening day. The Handbook supplies the occupied-membership evidence;
constitutional seat capacity alone would be the wrong denominator for that term.

The authoritative [review log](review-decisions.md) supplements APH by identifier
with sourced evidence. Gillard and Rudd records retain National Archives biographies.
Attendance remains `attended_unspecified` unless evidence supports another status.
Education IDs depend on the member and original recorded school name, so improving
a match does not create a new assertion. School names as recorded remain separate
from canonical institution names.

Uncertain service histories and education gaps remain queue views. Supported
corrections are appended to the decision log and replayed after ingestion. Service
corrections replace the entire member/term interval group and must be sourced,
nonoverlapping and inside its dates. Legacy inputs and their exact prior values
are preserved in `archive/reference/2026-10-05/`.

## Historical institutions and financial interpretation

ACARA 2008–2025 profiles retain institutions absent from the 2025 location register
as `historical_only`. Missing coordinates remain null; these schools contribute
to coverage but cannot appear as map points. Ambiguous historical names go to
review. Reviewed relationship decisions drive corrections after source ingestion.
Renames and successors retain `institution_resolution` and any supplied
`resolution_source_url`; obvious school mappings may omit a relationship URL
under the [review rules](review-decisions.md). Attendance evidence remains separate.
The migration retained all 89 unsourced legacy aliases as research decisions
rather than accepted relationships. Later reviewed supersessions in the ledger
can resolve those items. The reviewed migration report declares contemporary coverage
changes; historical review blocks prevent unrelated fuzzy assignments.

Reviewed successors include Ogilvie High → Hobart City High and Nambour High →
Nambour State College. The prior Nambour aliases incorrectly identified unrelated
Flagstone State Community College; their exact previous file is preserved under
`archive/reference/2026-10-02/`. A sourced rename also connects Marist Brothers
College Ashgrove to Marist College Ashgrove.

Recent finance describes the institution or its reviewed successor in its own
reporting year; it does not describe expenditure when the parliamentarian
attended. Institutions without a defensible current counterpart receive no
invented current peer estimate. Observed records in their own year remain usable.

The source has two invalid 2010 SEA quartets: Sirius College (46335, 43%) and
Amity College, Prestons (44001, 81%). Raw files and hashes remain intact. All four
invalid percentages are null in the canonical profile, with original values in
`review/upstream_profile_review.csv`. Other metrics remain, and the validator
continues to reject malformed canonical quartets.

## Acquire, replay and verify

Restore the pinned APH/AEC inputs and preinstall DuckDB spatial as described in
[reproducibility](reproducibility.md), then acquire the additional ACARA files:

```bash
uv run python -m apemap.historical --download \
  --db-path data/aped-historical-acquisition-0.3.4.duckdb \
  --output-dir data/processed/releases/0.3.4-acquisition --version 0.3.4
```

The version and paths above are examples; choose an unused version and fresh paths.
This stages a fresh database and release under the explicit output directory,
preserving existing external inputs and processed artifacts. ACARA loads before
APH, with finance/public funding afterwards. `data/historical-inputs-manifest.json`
pins source files, converted CSVs, references and existing AEC/APH caches by hash
and size. Unknown APH acquisition dates remain null in release provenance;
generated APH assertion timestamps record ingestion, not a newly asserted upstream
acquisition. ACARA's acquisition date comes from the input manifest. Replays
require fresh paths and identical inputs:

```bash
uv run python -m apemap.historical \
  --db-path data/aped-historical-0.3.4-replay.duckdb \
  --output-dir data/processed/releases/0.3.4-replay --version 0.3.4
uv run apemap release verify data/processed/releases/0.3.4-replay --strict-assertions
```

The fixed input-manifest timestamp controls ingestion/release timestamps. Identical
bundle bytes also require the same source commit. ACARA inputs retain their Data
Access terms and attribution; Commonwealth biographies retain source copyright.
Public exports omit restricted private finance fields. Full bundles are versioned
release assets, avoiding duplicate annual tables and spatial binaries in Git.
The checked-in historical report contains coverage, review queues, metadata,
analytical summaries and seven maps.

For saved decisions with inputs already present, skip acquisition and use
[Decisions to a new release](decisions-to-release.md). The
[0.3.3 delivery record](releases/0.3.3/README.md) records the latest reviewed
historical draft. Original 0.3.0 artifacts remain historical evidence; replaying
them exactly requires their recorded source revision and matching ledger, rather
than today's working decisions.

## Website contract and follow-up

Web metadata, summary and manifest declare `supported_parliaments`, selected
`parliaments` and `parliament_metadata`. Release layers
`parliament_<number>_combined.geojson` use the existing web school-feature exporter:
one school per feature, latest actual profile and opening-day service context.
`education_assertions` retains source, confidence, attendance status, recorded name
and mapping evidence. Generic spatial exports instead use one feature per
education/service relationship, with explicit `--cohort all_service` support;
the default is `opening_day`.

The cpoole.dev follow-up should build its selector from metadata, load the chosen
layer, filter party/chamber on the same parliament service, and show profile and
finance years, status, mapping evidence and the temporal warning. This repository
supplies and validates that contract; frontend changes belong in its repository.

Coverage separates distinct people, assertions and institutions.
`members_with_secondary_school` counts people with verified or provisional
assertions; unresolved assertions appear separately. Finance coverage counts
schools, not attendance relationships. This evidence pass does not claim every
member's education has been researched.

The sector summaries retain their existing denominator of people with any
recorded school name, including unconfirmed institution matches in `Other`.
Their `known_school_denominator` can exceed the coverage report's count of people
with verified/provisional resolution. A biography's recorded attendance and a
resolved institutional identity are separate evidence questions.
