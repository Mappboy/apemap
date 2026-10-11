# Guided research batches

Research proposals live under `data/reference/review/research/<batch-id>/`.
They are research artifacts, not decisions. The evidence and decision ledgers
remain authoritative. The local reviewer provides **Research batches** pages
using the same service as the CLI.

Prepare separate member and school batches:

```text
uv run apemap review research-batch prepare members-example --kind members --limit 10
uv run apemap review research-batch prepare schools-example --kind schools --limit 5
uv run apemap review research-batch import members-example results.json
uv run apemap review research-batch show members-example
```

Preparation freezes membership and exports local candidates, retained evidence,
decision heads, member aliases, birth dates, official profiles and service history,
or school attendance context. It excludes accepted decisions (including explicit
nulls), rejected cases, members with effective Wikidata IDs, and schools with
current matcher targets. Parliamentary geography is labelled separately from
school location.

Run the exported `instructions.md` with at most two `gpt-6-luna` workers at medium
reasoning: five members or one school per task, at most three searches and six
source opens per case. Reuse sufficient local evidence. An unresolved or failed
case may receive one `gpt-6.1-sol` medium retry, with two searches and four opens.
The caller runs the workers; these commands neither invoke paid APIs nor launch
agents. Keep both attempts and earlier evidence when importing a retry. Mark
token usage `null` when unavailable; never estimate it.
`research-batch retry-tasks BATCH_ID` exports an evidence-and-gaps packet for
cases still eligible for that single retry.

Result JSON is a list of objects with `review_id`, `outcome`, `value`, `reasoning`,
`alternatives`, `identity`, `payload`, `manual_institutions`, `evidence`, and
`attempts`. Evidence uses the existing `EvidenceRecord` schema, with
`generated_by="agent-search"`, claim-linked URLs and brief excerpts or paraphrases.
Identity `evidence_refs` link facts to evidence IDs. Attempts record model,
resolved/unresolved/failed status, actual search/open counts, gaps and token usage.
Use complete ISO dates for service intervals. The pilot supplies concrete examples.
Imports validate case membership, evidence, existing payload schemas and budgets.
Versioned `proposals-*.json` files preserve earlier attempts. Reviewed contents
cannot be replaced; corrections require a new batch.

Member sampled approval requires a valid QID, consistent identity, supporting
official and exact Wikidata evidence, and either a sourced exact APH identity
link or two matching non-name facts. At least one fact must establish birth date
or seat/chamber with overlapping service dates. Conflicts, duplicate assignments,
alternatives, prior decisions and unsupported matches require individual review.
Names and confidence scores cannot establish eligibility.

The frozen eligible set determines the reproducible sample:
`min(N, max(5, ceil(0.20 * N)))`. Inspect every attached source for every sampled
proposal before accepting it. An identity error in any sample permanently blocks
sample approval for that revision; review the remaining proposals individually.
Sampled and unsampled approvals are recorded separately in decision payloads.

Schools always require individual review and a disposition for **every attached
evidence item**. Inspect supporting, contrary and contextual evidence. An unavailable
source blocks acceptance. Retain closure, rename, merger, successor, historical
institution and unresolved findings. New targets may be proposed outside the
initial candidate list using existing mapping/manual-institution payloads.
A closure without a documented successor must not invent a current ACARA target.

Record an individual review using an inspection JSON object mapping every evidence
ID to `supports`, `contradicts`, `contextual` or `unavailable`:

```text
uv run apemap review research-batch review members-example PROPOSAL_ID accept --inspected inspections.json
uv run apemap review research-batch retain members-example --approve-sampled
uv run apemap review research-batch preview members-example --approve-sampled --out preview.json
uv run apemap review research-batch apply members-example preview.json --approve SHA256
```

Use `reject` or `research` with notes to leave an inspected proposal pending or
rejected within the research batch. A review does not write a decision. `retain`
copies evidence for approved proposals to the evidence ledger; it does not apply
corrections. Omit `--approve-sampled` for individually approved members and all
schools. The flag is explicit approval of the named frozen eligible member set
after its sample passes. It never approves ineligible proposals.

Inspect the exact preview before applying its SHA-256. The preview includes ledger
events, canonical effects, affected school assertions and expected queue effects.
Changed source inputs, decisions, batch contents or reviews require a fresh
preview. Explicit prior-head replacement requires individual review and
`--supersedes DECISION_ID`. Atomic application preserves existing history and
writes an application receipt with measured pending-queue changes. Rebuild derived
data through the existing review pipeline after applying decisions.

The issue #77 pilot contains ten members and five schools. Its proposals remain
pending human approval; it changes no canonical data or dataset release. Review
its separate pilot record for gaps and prospective effects.
