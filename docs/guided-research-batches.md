# Guided research batches

Research proposals live under `data/reference/review/research/<batch-id>/`.
They are research artifacts, not decisions. The evidence and decision ledgers
remain authoritative. The local reviewer provides **Research batches** pages
using the same service as the CLI.

To accept a researched change, follow this sequence:
**inspect sources → record reviews → retain approved evidence → preview decisions
→ approve and apply the exact preview**. An accepted review does not apply the
change. Applying decisions saves them to the ledger; rebuilding derived data and
publishing a dataset are separate steps.

## Review an existing batch in the local reviewer

### 1. Open the batch

Choose **Research batches** in the running reviewer's navigation. To start the
reviewer, run these commands from the repository root and leave the server running:

```text
uv sync --extra review-ui
uv run apemap review --external-dir data/raw/historical/acara serve --port 8777
```

Open [Research batches](http://127.0.0.1:8777/research-batches). The issue #77 pilot
is already researched; you do not need to prepare it or import results again:

- [Member pilot](http://127.0.0.1:8777/research-batches/issue-77-members-pilot):
  ten proposals, seven eligible for sampled approval, with five marked **Sample item**.
  Dutton, Ley and Farmer require individual review.
- [School pilot](http://127.0.0.1:8777/research-batches/issue-77-schools-pilot):
  five cases requiring individual review. St Thomas remains unresolved.

Read the [pilot findings and caveats](../data/reference/review/research/README.md)
alongside the proposals. The pilot was delivered with no human reviews or applied
decisions. Cam subsequently accepted six member proposals and four school proposals.
The four remaining members and St Thomas are unreviewed. These saved reviews are
separate from decision application. The accepted subset was applied on 13 October
2026; see the [acceptance record](issue-77-acceptance.md) for measured local results
and the ledger-baseline caveat.

The original pilot used `data/external`, whose 2022 register omits the historical
Alice Springs High School ID `acara:40335` referenced by an existing decision.
The explicit `data/raw/historical/acara` register above includes its 2008-2010
profiles and validates that decision. Changing registers requires the revalidation
workflow below; the reviewer does not silently substitute sources.

### 2. Inspect the proposal and every attached source

The batch shows one proposal at a time. Use the proposal list, status filters and
**Previous**/**Next** navigation to select a case. **Save and next** records the
current review before advancing. The batch summary keeps accepted, unreviewed,
research-needed and applied counts separate.

Read the outcome and reasoning. Expand **Alternatives, mapping payload and model
attempts** to check the exact QID or institution target, alternatives, manual
institution definitions and research gaps. **Agent handoff and saved task context**
contains the original local context.

For each proposal you review, open every source and check the actual contents
against the cited claim. The agent's excerpt is a starting point for inspection.
Record each source inspection using **Inspection completed or access attempted**
and **Source disposition**:

Each disposition defaults to its saved evidence stance; prior review selections
take precedence. The saved conclusion is shown beside the source. Defaults leave
inspection checkboxes unchecked. Confirm the disposition after inspecting the source.
If you change a conclusion, an inline warning blocks acceptance before submission;
you can record research-needed or rejection with notes instead. Validation failures
also keep your draft on the batch page rather than replacing it with a Bad Request page.

| Disposition | Meaning |
| --- | --- |
| Supports | The source supports the attached claim. |
| Contradicts | The source provides contrary evidence. |
| Contextual | The source adds context without establishing the claim. |
| Unavailable | The source could not be accessed or verified; acceptance is blocked. |

For members, verify the person behind the QID using official and Wikidata identity
evidence. Names alone are insufficient. For schools, check attendance, institution
identity, historical names and any proposed successor. Inspect contrary and contextual
evidence too. Parliamentary seat geography does not establish school location;
reuse of a closed school's site does not establish an educational successor.

### 3. Record an individual review

Choose a **Proposal disposition**, add notes, then click **Record individual review**:

- **Accept researched proposal:** you agree with the saved proposal and evidence
  dispositions. Every attached evidence item must have a disposition; an unavailable
  source prevents acceptance.
- **Reject proposal:** records rejection within the batch, with required notes.
  It does not append an authoritative rejection decision.
- **Needs further research:** leaves the proposal unresolved within the batch,
  with required notes. Use this for St Thomas rather than forcing a mapping.

For sampled member approval, inspect and accept every **Sample item**. The pilot
requires five. When the page says **All sample items passed**, approval of the
eligible remainder becomes available. The three individual-only proposals still
need their own reviews; passing the sample does not approve them.

If a sampled proposal identifies the wrong person, select **This proposal identifies
the wrong person** and record a rejection or research-needed review with notes.
This permanently blocks sampled approval for that revision; review the remainder
individually. Any sample item that has not passed also prevents sampled approval.

For schools, repeat full evidence inspection and individual review for every
proposal you want to approve. There is no sampled approval route. You may leave
unresolved or rejected proposals unapplied while proceeding with accepted proposals.
Accepting a documented closure without a successor retains the historical conclusion;
its preview can contain a manual definition and a research event without a current
school mapping. Check saved actions in **Review audit**.

### 4. Retain evidence, then inspect the preview

The shared **Approval scope** defaults to **Individually accepted proposals**.
It controls both evidence retention and decision preview. For members only, once
the sample passes, **Named batch approval: eligible unsampled remainder** explicitly
includes eligible unreviewed proposals. Ineligible proposals still need individual
acceptance, and rejected or research-needed proposals are excluded. The issue #77
follow-up uses individually accepted proposals only, even though the member sample
passed.

Click **Retain approved evidence**. This copies evidence for selected accepted
proposals to the evidence ledger and preserves agent provenance. It does not apply
member corrections or school mappings. Then click **Preview approved decisions**.

Inspect **Decision application preview**, especially:

- `events`: exact ledger events, QIDs, mapping payloads and manual definitions.
- `changes`: projected canonical effects.
- `school_assertions`: attendance assertions affected by shared school mappings.
- `research_queue_expectation`: expected queue counts and removed/added review IDs.
- `research_batch`: the batch contents, reviews and sampling scope bound to approval.

Retain newly accepted evidence again before making a fresh preview. You can preview
an accepted subset without approving every proposal in the batch.

### 5. Apply the preview you inspected

When the preview contains the changes you intend, click **Approve and apply this
exact preview**. This atomically appends decisions to the ledger, preserving prior
history. The batch's `application-<sha256>.json` receipt records applied decision IDs
and measured `queue_changes`. Saved previews and receipts remain available after
reload. Repeating application of the same successfully applied preview recovers
its receipt instead of appending duplicate decisions. Previously applied proposals
are excluded from later previews.

If decisions were appended but receipt writing failed, reopen the saved exact
preview and choose **Recover this application receipt**, or repeat the same CLI
apply command and approval hash. Recovery appends zero events. A missing receipt
can only be reconstructed while the preview's source and retained-evidence revisions
still match; changing those inputs can block recovery even when its decisions are
already present. Preserve the preview and inputs, and do not submit a new preview
to duplicate the application. An existing saved receipt remains the audit record.

Compare measured results with the preview's expectations. Pending queue reductions,
mapped school coverage and research-needed cases describe different outcomes.
Geilston's accepted closure finding retains a manual definition and an unresolved
research event; it does not count as a school mapping. Prospective counts are not
measured results.

Saving decisions does not rebuild DuckDB, update released data or change the website.
When you intend to produce updated data, follow
[Decisions to a new release](decisions-to-release.md) to validate, commit, rebuild
from pinned sources and verify the consumed ledger. Publication requires its own
review; applying the pilot does not require a dataset release.

## Revalidate saved reviews against current inputs

Use explicit revalidation when the original source snapshot can still be reproduced
and the reviewed facts remain unchanged. It preserves the old manifest, proposals,
source inspections and review timestamps. It does not approve unreviewed proposals.

For issue #77, the original directory is `data/external`; the current register is
`data/raw/historical/acara`. Revalidation checks the original source hash and ledger
prefix, current decision heads, member identity and service facts, school attendance
context, and the identity and classification of proposed institution targets.
The historical register enriches the two approved ACARA targets' status from
`unknown` to `current`; their names, sector, state, type and location are unchanged.
Inspect that source change in the new preview before applying it.

Changed identity, attendance, target facts or manual decisions require a fresh
research batch and human review. Revalidation cannot replace those reviews or
authorize a new target. Source, evidence, review or decision changes after a preview
still invalidate its approval.

In the reviewer, keep **Individually accepted proposals** selected, retain the
approved evidence, enter the original external directory and choose **Revalidate
and preview**. Inspect both the revalidation report and the exact events before
applying the preview.

The same CLI flow uses a fresh output path for each batch:

```powershell
$Batch = 'issue-77-schools-pilot'
$Preview = '.cache/issue-77-schools-revalidated-1.json'
uv run apemap review --external-dir data/raw/historical/acara research-batch retain $Batch
uv run apemap review --external-dir data/raw/historical/acara research-batch revalidate $Batch --original-external-dir data/external --out $Preview
```

Read the returned `preview`, including `research_batch.revalidation`, `events`,
`changes`, `school_assertions` and `research_queue_expectation`. Then apply only
that file with its returned SHA-256:

```powershell
uv run apemap review --external-dir data/raw/historical/acara research-batch apply $Batch $Preview --approve SHA256_FROM_REVALIDATION_OUTPUT
```

Repeat for `issue-77-members-pilot` with a separate new preview path. Omit
`--approve-sampled` for this follow-up. The selected subset contains six member
corrections, three school mappings, two manual definitions and one closure research
event. Dutton, Ley, Farmer, Grierson and St Thomas remain pending review.

## Prepare and import new research

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

## Approval rules

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

## CLI review and application

Run commands from the repository root using the same database, register and ledgers
as the reviewer. Custom `--db-path`, `--external-dir`, `--log-path` and
`--evidence-path` options belong after `review`, before `research-batch` or `serve`.
The examples below assume a fresh batch with unchanged sources and decisions.
For the saved issue #77 reviews, use the explicit revalidation commands above.

First inspect the batch to get the exact proposal and evidence IDs:

```powershell
$Batch = 'schools-example'
uv run apemap review research-batch show $Batch
```

After inspecting sources, create a separate inspection JSON file for each proposal.
Map **every attached evidence ID** to your actual disposition. Replace this example
key with the real ID and include all the proposal's evidence items:

```json
{
  "EXACT_EVIDENCE_ID_FROM_PROPOSAL": "supports"
}
```

Replace `PROPOSAL_ID` and the inspection file path with your actual values. Repeat
this command for each proposal you want to accept:

```powershell
uv run apemap review research-batch review $Batch PROPOSAL_ID accept --inspected inspections.json --notes "Sources inspected; proposal confirmed."
```

Use `reject` or `research` instead of `accept`, with explanatory `--notes`, for
other dispositions. Add `--identity-error` for a member proposal identifying the
wrong person. These commands record batch reviews only.

For schools or individually accepted members, retain evidence and create a preview
at a **new** output path:

```powershell
$Preview = '.cache/schools-reviewed-preview-1.json'
uv run apemap review research-batch retain $Batch
uv run apemap review research-batch preview $Batch --out $Preview
```

For members only, after every sample item passes and you approve the eligible
remainder, use the member batch ID and add `--approve-sampled` to **both** commands:

```powershell
$Batch = 'members-example'
$Preview = '.cache/members-reviewed-preview-1.json'
uv run apemap review research-batch retain $Batch --approve-sampled
uv run apemap review research-batch preview $Batch --approve-sampled --out $Preview
```

The preview command returns `preview_path`, `preview` and `sha256`. Read the saved
file and check the same fields listed in reviewer step 4. Only after successful
preview generation and inspection, copy its returned `sha256` into the apply command:

```powershell
uv run apemap review research-batch apply $Batch $Preview --approve SHA256_FROM_PREVIEW_OUTPUT
```

Keep the same batch ID and preview path. Do not reuse an old hash or edit the preview.
The CLI returns applied events and measured `queue_changes`; the batch retains an
application receipt too.

## If review or application is blocked

- **Incomplete imports:** import a result for every batch case, including unresolved
  cases, before recording any reviews.
- **No approved proposals selected:** accept at least one individual proposal, or
  complete the member sample before requesting sampled approval.
- **Unavailable source or changed evidence conclusion:** choose research-needed or
  reject, with notes. Acceptance requires dispositions consistent with saved evidence.
  Once reviews exist, corrected claims, targets or evidence require a new batch;
  preserve the reviewed revision.
- **Missing retained evidence:** run **Retain approved evidence** (or `retain`) with
  the same sampling scope before previewing.
- **Source inputs or prior decisions changed since research:** use explicit
  revalidation only when the original sources can be reproduced and reviewed facts
  are unchanged. Otherwise prepare and review a fresh batch using current context.
  A regular preview cannot refresh an old research snapshot.
- **Reviews changed after preview:** generate and inspect a fresh preview at a new
  path, then approve its new hash. Reload stale reviewer forms before recording reviews.
- **Prior manual decision:** replacement needs individual review and explicit
  **Explicitly replace prior decision** selection, or CLI `--supersedes DECISION_ID`.
  Sampled approval cannot authorize replacement.
- **Canonical validation error, including the pilot's missing `acara:40335`:**
  supply the explicit pinned register that contains the reviewed identity, such as
  `--external-dir data/raw/historical/acara` for this pilot, and revalidate saved
  reviews against it. Do not change a valid historical decision to fit a current-only
  register. Other corrections use the existing
  [review decision workflow](review-decisions.md), preserving prior events.
  Apply only after a valid preview can be generated.

The issue #77 pilot contains ten members and five schools. Review the
[pilot record](../data/reference/review/research/README.md) for the reviewed subset,
application results and remaining gaps. Decision application, rebuilding derived
data and publishing a dataset remain separate operations.
