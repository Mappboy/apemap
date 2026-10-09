# Retained evidence and advisory scoring

Package 0.6.0 supports distinct institution resolutions for education assertions
sharing recorded school text. On the school page, choose **Resolve this member's
school** for the member concerned. The institution's reference and locality
distinguish identical names. Source attendance rows remain visible as provenance;
their old automatic institution matches are not a reviewed conclusion.

John Paul College (`school:0477b661fc755dd3`) illustrates this distinction:
`acara:45994` identifies Frankston and `acara:48982` identifies Kalgoorlie. A
read-only verification confirmed that assertions for APH 297964 and 298800 can
resolve separately, and research on one leaves the other unchanged. This verifies
the workflow; it does not establish which school either person attended or append
any real decision.

## Retain sources before deciding

Evidence is stored separately in append-only `evidence.jsonl`, beside the chosen
decision log. `--evidence-path` selects another retained store. Each immutable
record has a content-derived ID and contains the case, candidate reference, source
URL/title/type, retrieval time, claim type/value, stance, excerpt or note and
origin. Stance is `supports`, `contradicts` or `contextual`. Optional source-quality,
match-strength, temporal and geographic relevance values are in [0, 1]; omitted
values mean unknown. A record can retain opposing evidence for a candidate.

Use public, sourced claim descriptions. Represented state and electorate describe
parliamentary service; they never establish school location. Case-wide attendance
evidence stays attached to its member. Only explicitly reusable school-identity or
relationship claims can supply another assertion using the same frozen school ID.

```json
{
  "candidate_institution_ref": "acara:12345",
  "source_url": "https://example.org/member-school-source",
  "source_title": "Member biography with school locality",
  "source_type": "biography",
  "retrieved_at": "2026-10-09T00:00:00+00:00",
  "claim_type": "identity",
  "claim_value": "The biography identifies the school and its locality",
  "stance": "supports",
  "excerpt_or_note": "Summarize the relevant public evidence",
  "generated_by": "manual"
}
```

```powershell
uv run apemap review retain-evidence <education-review-id> --payload source.json --dry-run
uv run apemap review retain-evidence <education-review-id> --payload source.json
uv run apemap review evidence <education-review-id>
uv run apemap review rank-education <education-review-id>
```

The GUI has separate evidence preview and retention controls. Retaining evidence
never creates a decision. Select retained records when preparing a reviewed
mapping; its `evidence_refs` can contain multiple supporting or contradicting IDs.
The selected records must belong to the assertion and candidate, or be eligible
school-wide relationship context. Unknown IDs and incorrect scopes fail validation.
Accepted attendance referencing candidate-specific evidence must select an explicit
institution reference; it cannot inherit a different school default. Research and
rejection can retain evidence about competing candidates within the same case.
Keep the decision's primary source URL for existing provenance consumers.

Existing event source URLs are represented as stable initial evidence derived
from the retained event, including its timestamp. Their quality dimensions remain
unknown. This neither rewrites old events nor treats a URL as verified evidence of
attendance. A changed claim or corrected record gets a new evidence ID; existing
retained bytes are preserved. Evidence edits invalidate stale decision previews;
changed source inputs or a replaced manual candidate definition also require a
fresh evidence preview before retention.

## Explain the ranking

Scoring version 1 uses a 0–100 advisory index, not a calibrated probability:

| Component | Maximum points | Basis |
| --- | ---: | --- |
| Name match | 40 | Normalized recorded/candidate name similarity |
| Identity | 24 | Explicit retained match strength |
| Source quality | 12 | Explicit retained quality value |
| Temporal | 12 | Explicit retained temporal relevance |
| Geographic | 12 | Explicit retained geographic relevance |

Supporting dimensions add points; contradicting dimensions subtract them.
Contextual and candidate-unspecified records add none. Each source URL, ignoring
its fragment, contributes only once per dimension: strongest support minus
strongest contradiction. Independent sources sum within the component bounds;
the total is bounded to [0, 100]. Missing dimensions contribute zero and appear as
unknown. Cards expose component totals, the retained records and the rule.

Identical exact names without directional evidence tie at 40. Ties remain visible,
and reference IDs provide a stable display order. Scores never accept a mapping,
change attendance confidence, choose an institution for replay, or write decisions.
All source retention and final conclusions require separate reviewer actions.

## Offline release provenance

Replay validates decision references using retained evidence and deterministic
legacy-source records. A source build archives the consumed evidence bytes and
hash with its review snapshot. Release exports copy those archived bytes to
`review/evidence.jsonl`; they do not reread the current working store. Verification
checks the frozen provenance. A fresh recipe pins the evidence store when present,
and builds copy the exact pinned bytes before ingestion. Legacy recipes without an
evidence pin preserve their original contract.

No online source, agent or model is required for scoring or release replay.
Historical deliveries, recipes and checked-in research artifacts are preserved.
See the [issue #66 acceptance audit](issue-66-acceptance.md) for remaining work.
