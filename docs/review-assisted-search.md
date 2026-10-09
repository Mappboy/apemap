# Assisted research and review readiness

Package 0.7.0 adds disposable assisted research to the local assertion reviewer.
Start it with a provider configuration:

```powershell
uv sync --extra review-ui
# Set the keys for providers you intend to use in this shell.
$env:OPENROUTER_API_KEY = '<your key>'
$env:GEMINI_API_KEY = '<your key>'
$env:OPENAI_API_KEY = '<your key>'
uv run apemap review serve --research-config docs/research-providers.example.json
```

The [example configuration](research-providers.example.json) offers OpenRouter,
Gemini and OpenAI in the provider selector. Each entry has a unique `id`, provider,
explicit model ID, credential environment name and timeout (1–120 seconds).
You can add several entries for the same provider to compare models. Remove entries
you do not use. Configuration contains no keys or custom API endpoints. Model IDs
are examples; choose a model available to your account that supports the provider's
search tool. Requests can incur the provider's model and search charges.

OpenAI uses the Responses API's
[web search tool](https://developers.openai.com/api/docs/guides/tools-web-search).
OpenRouter uses its
[server web search tool](https://openrouter.ai/docs/guides/features/server-tools/web-search).
Gemini uses
[Google Search grounding](https://ai.google.dev/gemini-api/docs/generate-content/google-search).
Provider source citations remain linked in the results; Gemini Search suggestions
appear in a sandboxed frame. Adapter tests use mocked HTTP responses, not paid
requests, and do not establish account or model availability.

## Search, inspect, retain, decide

Open **Resolve this member's school**, choose a provider and select **Find evidence**.
The request contains that member's public name, birth date, parliamentary service,
recorded attendance sources, candidate institution names/localities and research
periods. State and electorate remain parliamentary context. It does not send the
whole comparison group, API credentials as prompt text, or an entire research log.

Results are temporary in-memory jobs. Restarting the reviewer or waiting 30 minutes
discards them. Only cited HTTP(S) sources and candidates supplied for the case can
be suggested. Model quality scores are discarded; unknown dimensions stay unknown.
Jobs are bounded, have request timeouts and do not run in offline mode
(`APEMAP_OFFLINE=1`). A manual reviewer works without configured providers.
OpenRouter requests set a total server-tool budget of two calls (`max_tool_calls`)
and retain the ten-result limit. The provider-specific `max_uses` setting alone
does not limit native search for every model; see the linked OpenRouter guide.
Retrieval timestamps come from the local application clock. Timestamps supplied
in model or provider JSON are ignored, including during job validation.

Citation validation establishes that a URL appears in the provider's citations,
not that its page supports an individual suggestion. Source inspection remains
mandatory. Retaining provider-specific claim-to-source grounding, where available,
is a follow-up improvement.

1. Open the cited source and assess what it actually establishes.
2. Select **Inspect and edit evidence draft**. Correct the claim, stance and quality
   dimensions as needed, then confirm you inspected the source.
3. Preview the evidence and explicitly retain the signed record. This appends only
   evidence, with `generated_by: agent-search`.
4. Select retained evidence for a separate mapping preview and decision save.

Searching, polling and editing a draft change no canonical rows or decision log.
Changed source/candidate data or decisions invalidate a search and retention draft.
Retaining another source leaves other suggestions available, but each new retention
preview uses the current evidence revision. Errors and timeouts retain nothing.

## Separate approval for school-wide CLI changes

All school-wide CLI decisions, including imports and supersessions, need a separate
preview artifact and its SHA-256 approval. Member-specific decisions retain their
existing workflow.

```powershell
uv run apemap review accept <school-review-id> --institution-ref <institution-ref> --preview-out preview.json
# Inspect events, canonical changes and the affected/protected assertion inventory.
uv run apemap review apply-preview preview.json --approve <sha256-from-preview>
uv run apemap review import reviewed.jsonl --preview-out import-preview.json
```

The artifact binds exact events and input revisions. Changed bytes, stale inputs,
missing inventories and changed effects prevent the whole batch from appending.
Create a new preview after a change. `--dry-run` remains read-only.

## Independent successor evidence roles

Successor decisions may select a `context_evidence_refs` object mapping each role
to one or more retained evidence IDs. Select these roles beside retained source
cards, or supply them in a CLI payload. Each role requires supporting evidence for
the selected successor and an eligible assertion or reusable school scope.

| Role | Claim type | Structured `claim_value` example |
| --- | --- | --- |
| `original_identity` | `original_identity` | `{"institution_ref":"acara:123"}` |
| `location` | `location` | `{"latitude":-42.8,"longitude":147.3}` |
| `campus` | `campus_continuity` | `{"value":"different_campus"}` |
| `broad_sector` | `sector` | `{"broad_sector":"Non-government"}` |
| `detailed_sector` | `sector` | `{"detailed_sector":"Catholic"}` |
| `relationship_timing` | `relationship_timing` | `{"start_year":1990,"end_year":null}` |
| `profile_proxy` | `profile_proxy` | `{"status":"unsuitable","reporting_year":2025}` |
| `finance_proxy` | `finance_proxy` | `{"status":"approved","reporting_year":2023}` |

Original identity, location, campus and sectors adapt to the existing historical
context fields during replay. Explicit old fields must agree with selected claims.
Conflicting claims fail validation. School-wide roles require
`historical_scope_confirmed: true`; individual assertions have their own scope.
Existing serialized events and historical releases are not rewritten.

Timing and proxy suitability remain independent reviewed annotations in the UI and
`review/successor-context.json` release report. They do not manufacture attendance
dates, historical profile values or finance amounts. Profiles and finance still
describe the reporting institution in their stated reporting year. Recorded
attendance years take precedence; a graduation year supplies an endpoint. Otherwise
birth year +12 through +18 is labelled an estimate for research only.

## Advisory analytical impact

```powershell
uv run apemap review readiness --parliament 47 --parliament 48
```

The UI's **Review readiness** link shows the same working projection. It reports
candidate scores, sector count changes, shared-school top-ten counts/order/membership,
ties and conservative joint bounds for unresolved people. It counts distinct people
for alumni groups and preserves attendance assertions as a separate denominator.
Successor sector sensitivity removes assumed sector values without removing people.
Unreviewed successor targets do not verify original school identity or campus.

Local candidate discovery is not exhaustive. Cases with no candidates or no detected
single-candidate impact remain **unknown**. Joint bounds can identify several cases
that could together change the top ten even when none does so alone. Bounds are
conservative possibilities, not predicted outcomes or simultaneous independent
category maxima. Missing review identities or recorded names remain context gaps.

New release builds include `review/readiness.json` and `review/successor-context.json`,
with schema/scoring versions, deterministic input digests and consumed snapshot
provenance. Release reports use only canonical database and archived decision/evidence
bytes; they do not read a newer working log or invoke a provider. Working reports can
also search the pinned local register. Candidate coverage can therefore differ and
is disclosed. Reports are advisory and do not block building or publishing a release.
Existing release checksums, directories and recipes remain unchanged.
