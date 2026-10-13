# Test performance evidence — issue #88

This note records the evidence behind the test-fixture and CI changes for
issue #88. Measurements are observations from specific runs, not runtime
targets. Hosted runner timing varies, and no hard timing assertions were added.

## Hosted CI runs

| Revision | Result | Pytest elapsed | Workflow wall time | Sum of job times |
| --- | --- | ---: | ---: | ---: |
| Clean baseline `c0fc295` | 1,186 passed, 2 skipped | 251.04 s | 283 s | 587 s |
| Intermediate `96ebf24` | 1,189 passed, 2 skipped | 231.42 s | 262 s | 443 s |
| Source-cache guard `3d36d9a` | 1,191 passed, 2 skipped | 262.30 s | 303 s | 542 s |
| Two workers + serial notebooks `2de0f7f` | 1,191 passed, 2 skipped | 138.43 + 9.65 = 148.08 s | 182 s | 399 s |
| Repeated two-worker run `287a2c8` | 1,191 passed, 2 skipped | 139.63 + 14.60 = 154.23 s | 188 s | 421 s |
| Final marker checkpoint `e1399d3` | 1,191 passed, 2 skipped | 138.11 + 8.73 = 146.84 s | 180 s | 358 s |

The clean baseline is [CI run 38335962958](https://github.com/Mappboy/apemap/actions/runs/38335962958).
Its job-time sum is quality 59 s, tests 278 s, release 142 s, and pipeline
108 s. The [intermediate run](https://github.com/Mappboy/apemap/actions/runs/38337638727)
and [source-cache-guard run](https://github.com/Mappboy/apemap/actions/runs/38339536356)
show hosted-runner variation: the later serial pytest run was slower than the
baseline. In particular, the recipe replay test was 31.72 s in the intermediate
run and 49.73 s in the source-cache-guard run. Fixture cleanup alone did not
establish a consistent full-suite speedup on these runners.

The [two-worker run](https://github.com/Mappboy/apemap/actions/runs/38341283370)
passed 1,186 non-notebook cases with two skips, followed by all five notebook
cases serially. These disjoint selections retain the full 1,193-case collection.
Compared with the clean baseline, combined pytest elapsed fell 41.0%, workflow
wall time fell 35.7%, and summed job time fell 32.0%. Five new isolation and
configuration cases account for the larger collection.

The [repeat run](https://github.com/Mappboy/apemap/actions/runs/38342207902)
passed the same selections without ordering or shared-state failures. Across
these two runs, full-collection pytest elapsed was 38.6–41.0% below baseline,
workflow wall time was 33.6–35.7% lower, and summed job time was 28.3–32.0% lower.
The [final marker checkpoint](https://github.com/Mappboy/apemap/actions/runs/38343096008)
also passed all three jobs and the full collection after the final 21 custom
fixture cases received integration labels.

| CI job / major step | Baseline | Two-worker run |
| --- | ---: | ---: |
| Quality job | 59 s | 49 s |
| Quality input provisioning | 37 s | 29 s |
| Tests job | 278 s | 177 s |
| Test preparation, before pytest | 23 s | 23 s |
| Pytest steps (GitHub step time) | 253 s | 139 + 11 s |
| Duplicate pipeline job | 108 s | Removed |
| Release-contract job | 142 s | 173 s |
| Release input provisioning | 36 s | 46 s |
| Release build | 82 s | 101 s |
| Strict release verify | 3 s | 3 s |

Quality, tests, and release-contract remain independent parallel jobs. The
duplicate pipeline job ran the same recipe build as release-contract; removing
it preserves pipeline validation inside that build. A shared preparation job
would serialize downstream jobs behind input setup, so it was not added. The
existing uv cache, pinned input verification, spatial installation, ledger/base
validation, strict release verification, and separate release workflow's
double-build comparison remain enabled.

## Local Windows evidence

The pre-change full run on Windows 3.12.2 was canceled after more than 33 minutes
at 36%. It included a workflow assertion failure caused by editing that file
after test collection. It is not a valid full-suite timing baseline, so no full
Windows speedup is claimed; the clean immutable CI baseline supplies the paired
comparison.

The final complete serial run passed 1,192 cases with one optional browser skip
in 1,883.09 s (31m23s). The clean Linux checkout also skips the optional check
against an existing working `data/aped.duckdb`. The local environment was created
afresh with `uv sync --all-groups --extra review-ui --frozen`, Python 3.12.2,
DuckDB 1.5.5, pytest 9.1.1, and pytest-xdist 3.8.0. Windows used the task-private
`UV_PROJECT_ENVIRONMENT=.cache/issue88-env` while the live reviewer continued
using the existing environment.

The same final serial suite took 262.30 s on Linux CI: this Windows workstation
was 7.2 times slower. Its recipe replay test alone took 341.18 s, and notebook
calls ranged from 10.27 to 25.53 s. This is a machine/platform comparison, not a
controlled operating-system benchmark. A local two-worker safety check passed
68 focused cases in 110.34 s while the serial suite was also running.

The final documented fast selection passed all 783 cases, with 410 deselected,
in 320.60 s (5m20s). It retains all 461 explicitly marked unit cases, the 322
remaining unmarked cases, and static notebook checks. The full suite continues
to exercise every ingestion, database, release, and notebook contract.

## Categories and slow phases

The final collection contains 461 unit, 410 integration, 5 notebook, 6 slow, and
322 unmarked cases. Notebook/slow markers overlap integration. The following
exclusive groups sum to the full 1,193 cases; times sum JUnit test-case durations,
including setup and teardown. Parallel worker durations overlap, so their sum
is not pytest wall time. The serial Linux column is run `3d36d9a`; the parallel
column is `2de0f7f`, grouped with the final marker audit.

| Exclusive category | Cases | Windows serial | Linux serial | Linux two workers + notebooks |
| --- | ---: | ---: | ---: | ---: |
| Unit | 461 | 127.43 s | 12.53 s | 16.95 s |
| Integration, ordinary | 404 | 1,141.13 s | 159.43 s | 185.09 s |
| Integration + slow recipe replay | 1 | 341.65 s | 49.75 s | 41.80 s |
| Integration + notebook + slow | 5 | 78.75 s | 10.11 s | 8.22 s |
| Unmarked | 322 | 169.49 s | 25.65 s | 20.70 s |

The largest Linux parallel module totals were release recipe 43.99 s, school
review 17.56 s, AEC ingestion 15.90 s, review concurrency 14.05 s, and release
contracts 12.18 s. The slowest call was the full local/CLI recipe replay at
41.77 s (52.94 s on the repeat run), followed by AEC idempotence at 6.57 s.
These full-contract cases stay enabled. Windows' slowest recorded setup phases
were the explorer release template at 9.53 s and successor web-contract audit
at 7.29 s. No teardown appeared among its slowest 40 phases.

An initial fast-profile run passed 890 cases in 682.69 s before the final source
cache guard and marker audit. A later 804-case selection passed in 401.59 s and
exposed another 21 custom-fixture ingestion/export/release cases needing
integration labels. These selections differ from the final fast profile, so
they are not paired speedup baselines.

## Changes and evidence

- Review tests now build the domain-seeded database and captured source rows in
  one closed session template. Each of 140 review test cases receives a
  private database copy, ledger, and source files.
- Explorer contract tests now build the complete release once per session and
  copy it for each of the 15 existing contract cases plus an isolation
  regression, which checks that edits to one copy do not alter another copy or
  the template. Existing contract assertions are unchanged. CI release-contract
  still builds the pinned recipe and
  verifies its bundle with `--strict-assertions`.
- A session fixture sets DuckDB to one thread for small test databases while
  preserving explicit connection configuration. A regression covers explicit
  settings.
- Test defaults route raw APH and Wikimedia caches to per-test temporary
  directories. Two stale-cache guard regressions cover this isolation. Before
  this guard, `py-spy` sampled `file_digest` within `source_revision` while it
  hashed 459 Wikimedia JSON files (568,936 bytes) and the APH individuals file
  (10,741,777 bytes) from the real cache.
- A query probe returned identical outputs with 20 DuckDB threads and one
  thread. The two sampled query times were 2.328 s and 0.956 s with 20 threads,
  and 0.245 s and 0.218 s with one thread. This is a microbenchmark, not a
  full-suite timing result.
- Added development-only `pytest-xdist>=3.8.0`, locked at 3.8.0 with `execnet`
  2.1.2. The isolation audit found per-test temporary paths for mutable test
  outputs, dynamic ports for live servers, and no shared test write that blocks
  the two-worker non-notebook selection. The suite's default command remains
  serial. `--dist=loadfile` keeps a test module on one worker, and notebooks
  continue to run serially.
- Integration markers now identify the shared review-service, resolved database
  fixture, explorer audit, and full release cases. Pure helpers and explicitly
  isolated unit cases retain their selections. Markers exclude nothing from
  default pytest or the two complementary CI commands.

## Reproduction commands

These commands use Bash/Git Bash. Run from the repository root after installing
the locked test environment and provisioning verified offline inputs. JUnit
output is under ignored `.cache/`. In PowerShell, set `$env:APEMAP_OFFLINE='1'`
and run the equivalent `uv` commands.
The first command is the serial full-suite timing; the next two match the CI
split and together retain the full suite.

```bash
mkdir -p .cache
APEMAP_OFFLINE=1 uv run --frozen pytest --durations=40 \
  --junitxml=.cache/issue-88-serial.xml
APEMAP_OFFLINE=1 uv run --frozen pytest -n 2 --dist=loadfile \
  -m "not notebook" --durations=40 \
  --junitxml=.cache/issue-88-parallel.xml
APEMAP_OFFLINE=1 uv run --frozen pytest -m notebook --durations=10 \
  --junitxml=.cache/issue-88-notebooks.xml
APEMAP_OFFLINE=1 uv run --frozen pytest \
  -m "not integration and not notebook and not slow" --durations=25 \
  --junitxml=.cache/issue-88-fast.xml
```

## Version impact

The branch preserves its existing issue #89 compatible provider correction.
That unpublished fix requires package `0.10.1` → `0.10.2`; `pyproject.toml`, the
root `uv.lock` entry, and the clean installed environment match. Issue #88 adds
no further package bump. Dataset versions, schema, source pins, historical
manifests, and published releases are unchanged.

## Validation

Full serial Windows pytest, the final fast selection, repeated full-collection
CI runs, strict marker collection, Ruff lint/format, CI-scoped `ty check apemap/`,
deptry, all repository
hooks, and `uv lock --check` pass. Documentation/input/workflow checks also pass
(10 cases). Repository-wide `uv run ty check` retains 157 pre-existing diagnostics
in legacy code, archives, and existing tests; CI uses the passing package scope.
Concurrent working review-ledger/evidence appends are outside this change.
