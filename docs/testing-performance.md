# Pytest performance measurements — issue #39

Measured on 1 October 2026 on the same Windows machine with Python 3.12.2 and
pytest 9.1.1. The baseline is the user-selected active branch at `39ef65b`, after
the merged PR #38 fixes and the packaging/collector work. Runs were sequential;
pytest-xdist was not installed or enabled. Existing local source caches and the
installed DuckDB spatial extension were used without refreshing research data.

## Complete suite

| Run | Result | Pytest elapsed time |
| --- | --- | ---: |
| Original branch | 170 passed, 1 failed | 457.47 s |
| Updated branch | 173 passed, including all five notebooks | 244.99 s |

The complete suite is **46.4% faster**, despite adding two fixture/HTTP guard
regressions. The original failure was DuckDB fetching a `TIMESTAMPTZ` value
without `pytz`; the explicit runtime dependency fixes that failure. The original
full timing command used `-vv --durations=15 -o faulthandler_timeout=30`; the
updated command used `--durations=25 -o faulthandler_timeout=60`. Neither option
selects or excludes tests. Both runs emitted the existing Windows Jupyter
event-loop warning.

## Matching priority-module profiles

The original tests and their schema files were extracted from `39ef65b` into an
ignored local snapshot. Original and updated CLI, funding, web-release, and
Wikimedia modules were run with `uv run pytest <four module paths>
--durations=25`, using the same installed environment, including the restored
`pytz` dependency. Both profiles passed the same 78 tests. The original profile
took **277.36 s** and the updated profile **47.09 s**, an **83.0% reduction**.

The table sums pytest's setup, call, and teardown durations for each module;
collection and process overhead account for the difference from elapsed time.

| Module | Original phases | Updated phases | Reduction |
| --- | ---: | ---: | ---: |
| `test_cli_pipeline.py` | 81.11 s | 22.27 s | 72.5% |
| `test_funding_benchmarks.py` | 158.57 s | 6.09 s | 96.2% |
| `test_web_release_contract.py` | 13.55 s | 8.49 s | 37.4% |
| `test_wikimedia_ingest.py` | 13.45 s | 7.63 s | 43.3% |

The original CLI fixture was rebuilt for each consumer, costing 4.15–4.92 s per
setup in this profile. The updated profile builds its seeded template once
(2.22 s, including shared schema setup), then opens independent per-test copies.
Web and Wikimedia templates similarly retain their original domain seeds and
avoid repeated construction. No mutable connection is shared between tests.

The two original funding orchestration calls took **92.77 s** and **62.34 s**.
With small synthetic inputs covering all six funding sources, they took
**0.59 s** and **0.72 s**. Exact row counts, funding validation, source-specific
unit coverage, and rerun idempotency assertions remain covered. Production
benchmark CSVs remain unchanged.

Timing varies between runs; these measurements support the fixture and input
changes without establishing a fixed runtime threshold. Remaining full-suite
costs are dominated by spatial ingestion/roundtrips and notebook kernels.

## Fast development loop

`uv run pytest -m "not integration and not notebook and not slow" --durations=25`
passed **94 tests in 29.96 s**, with 79 integration/notebook tests deselected.
Static notebook mutation checks and the new fixture/HTTP regressions participate
in this loop. The complete run above includes all deselected coverage.

See the [Development Guide](development.md#7-writing-tests--working-with-fixtures)
for marker commands and fixture rules. `uv run pytest` continues to run the
complete suite without default exclusions.
