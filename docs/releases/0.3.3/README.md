# Reviewed historical dataset v0.3.3

This delivery incorporates the committed review decisions through source revision
`013a8df810ce22442801b4040b01297979a569e1`. It is an unpublished draft for review,
prepared on 6 October 2026. Older 0.3.0 and 0.3.1 artifacts remain unchanged.

The historical recipe covers parliaments 42–48, ACARA 2008–2025 profiles and 2024
finance analysis. This uses cached sources and does not acquire Finance 2024 or
refresh APH/ACARA. Source reporting years describe institutions in those years,
not their resources when members attended.

## Reproduce the reviewed bundle

Use the recorded source revision and matching committed ledger, provision the
pinned historical inputs and the DuckDB spatial extension, and choose fresh paths.
Do not refresh sources during replay. From the repository root:

```powershell
$env:APEMAP_OFFLINE = '1'
uv run apemap inputs verify --manifest data/historical-inputs-manifest.json
uv run apemap review --external-dir data/raw/historical/acara check
uv run python -m apemap.historical --db-path data/aped-historical-0.3.3-replay.duckdb --output-dir data/processed/releases/0.3.3-replay --input-dir data/raw/historical/acara --manifest-path data/historical-inputs-manifest.json --version 0.3.3
uv run apemap release verify data/processed/releases/0.3.3-replay --strict-assertions
uv run python -m apemap.release.package data/processed/releases/0.3.3-replay --output-dir data/processed/candidates/0.3.3-replay
```

The database stores the exact consumed ledger and input-manifest bytes. The public
manifest records `review_snapshot` separately from upstream source pins. Its fixed
generation timestamp comes from the historical input manifest, rather than the
date this draft was prepared. The APH acquisition date remains unknown/null.

## Validation and delivery

The bundle and its extracted archive each pass **217/217 checks across 50 files**.
The archive is **32,607,470 bytes**, with SHA-256
`af5432b39bd6a428b9476fce935b19d202c0c66cad036e5f0f87fc68d098cba8`.
Its consumed ledger contains **163 events and 103 effective decisions**, hash
`9376bb877fd7ae66620fe547baca46538ced25df62102f5b43ef0592c790e600`.

Against the recorded 0.3.0 baseline, all 580 members, 1,754 service records and
352 education assertion IDs remain. There are 66 changed institution assignments;
The institution inventory has a net decrease of 18: 27 unmatched placeholders
disappear and nine institution identities are added. Opening-day membership is
unchanged in every parliament. Verified/provisional education coverage improves:

| Parliament | Opening-day members | Matched members: baseline -> 0.3.3 | Unresolved school records: baseline -> 0.3.3 |
| --- | ---: | ---: | ---: |
| 42 | 226 | 32 -> 36 | 21 -> 15 |
| 43 | 226 | 49 -> 57 | 35 -> 22 |
| 44 | 224 | 63 -> 79 | 58 -> 35 |
| 45 | 226 | 93 -> 114 | 79 -> 49 |
| 46 | 227 | 123 -> 151 | 103 -> 63 |
| 47 | 227 | 121 -> 151 | 112 -> 68 |
| 48 | 226 | 116 -> 144 | 101 -> 58 |

This compares the whole reviewed snapshot with 0.3.0; it does not isolate only
the last day's decisions. The 171,822 annual profile rows and 5,184 finance
benchmark rows are unchanged. Finance 2024 remains benchmark-based where there
are no observed school records; a selected analysis year does not imply those
private observations were acquired. Unresolved cases remain explicit.

Build verification, archive hashes and baseline comparisons are recorded in
[delivery-record.json](delivery-record.json). The copied [manifest.json](manifest.json)
and [SHA256SUMS](SHA256SUMS) pin payload bytes; [SHA256SUMS.dist](SHA256SUMS.dist)
pins the downloadable archive. Do not put this documentation inside the release
payload, which would invalidate its bidirectional inventory.

## Publication and frontend handoff

The current GitHub release Action rebuilds the standard recipe with 2021 finance.
It cannot publish this historical candidate equivalently without a reviewed recipe
change and the reviewed sources on `main`. The draft supplies verifiable bytes
for review; it does not update the public website.

For the next correction cycle, follow [Decisions to a new release](../../decisions-to-release.md)
and choose an unused dataset version. After approved publication, pin the exact
archive/version in cpoole.dev, recheck corrected records, service-aware filters,
downloads, source links and reporting years, then deploy through that repository.
