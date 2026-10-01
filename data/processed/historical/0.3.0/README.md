# Historical dataset report v0.3.0

This populated report covers the 42nd–48th Parliaments. It includes opening-day
coverage, persistent evidence review queues, static analyses, seven public map
layers and canonical member/service/education CSVs. Earlier processed artifacts
are preserved in their original locations.

| Parliament | Opening-day members | Known DOB | Known school | Missing/unresolved school |
| --- | ---: | ---: | ---: | ---: |
| 42 | 226 | 223 | 32 | 194 |
| 43 | 226 | 223 | 49 | 177 |
| 44 | 224 | 222 | 63 | 161 |
| 45 | 226 | 219 | 93 | 133 |
| 46 | 227 | 221 | 123 | 104 |
| 47 | 227 | 215 | 121 | 106 |
| 48 | 226 | 194 | 116 | 110 |

Known school means at least one verified/provisional assertion; unresolved
assertions remain separate in the detailed coverage files. The 44th has two
documented Senate vacancies. Counts refer to distinct people; assertions and
institutions have their own denominators. Recent profile/finance values describe
their reporting years and reviewed successors, not expenditure at attendance.

The existing sector summaries' `known_school_denominator` counts any recorded
school name, including unresolved institution matches. Coverage's
`members_with_secondary_school` requires verified/provisional resolution, so
these totals can differ. Compare the definitions before comparing percentages.

The complete 49-file release includes 171,822 annual school profiles and public
CSV/Parquet tables. It is packaged as `apemap-historical-v0.3.0.tar.gz` in the
[issue 30 draft dataset preview](https://github.com/Mappboy/apemap/releases/tag/untagged-8d0230ee92e2a4bb8f17).
The preview remains a draft for review; publication follows the main-branch
release policy. Its asset checksum and source commit are in `report-manifest.json`.
Draft assets require repository access until publication.

Both fresh builds pass all 213 release checks, and their release manifests,
payloads and deterministically packaged archives match byte for byte. This
checked-in subset has a separate inventory; it does not pretend to be the full
release bundle. See [the methodology and replay guide](../../../../docs/historical-coverage.md)
for pinned source inputs, reviewed mappings, upstream anomalies and website
integration. The legacy source-cache archive referenced by the general input
manifest is not currently published; replays require the listed local raw inputs
or deliberate upstream acquisition. The verified populated bundle is supplied
independently of that earlier CI-input distribution limitation.

Validation: `uv run pytest` passes 202 tests; Ruff lint and format pass;
`uv run ty check apemap tests/test_historical_coverage.py` passes. Repository-wide
`uv run ty check` reports 157 diagnostics in the legacy app, preserved archives
and existing test typing. Those do not affect the verified package scope.
