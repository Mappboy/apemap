# Resolve a school for one education assertion

Package 0.6.0 begins [issue #66](https://github.com/Mappboy/apemap/issues/66) with
assertion-specific institution relationships. A decision for
`education:<aph-id>:<recorded-name-digest>` can resolve the school attended by
that member without assigning the same institution to every use of that school
name. Source attendance evidence and institution relationship evidence remain
separate.

This provides scoped mappings, unresolved/research states, replay, conflict
diagnostics, and a local review form. The follow-up adds
[structured retained evidence and advisory scoring](review-evidence.md).
Assisted external search and analytical-impact readiness remain future work;
the [acceptance audit](issue-66-acceptance.md) records every outstanding criterion.
The review tools continue to use pinned local sources.

## Map an existing assertion

List and inspect the education review item before choosing a target. The complete
review ID comes from `list` or the local reviewer; its digest is frozen and must
not be constructed from a replacement school name.

```powershell
uv run apemap review list education
uv run apemap review show <education-review-id>
uv run apemap review map-education <education-review-id> --institution-ref acara:12345 --relationship-type direct --source https://example.org/member-school-evidence --dry-run
```

Remove `--dry-run` after reviewing the preview to append the decision. The
relationship is `direct`, `alias`, `rename` or `successor`; the target is a real
`acara:` reference or an accepted `manual:` institution definition. A scoped map
requires relationship evidence, unlike a school-wide map whose source URL can be
optional. Mapping changes the institution assignment, relationship and its source;
it retains the assertion's recorded name, attendance status, years, graduation,
attendance confidence, source URL, retrieval time and evidence origin. It does
not add a new claim of attendance.

A complex scoped successor can use a JSON payload with `--payload`. Identity
fields identify the existing assertion; each historical fact needs its own
evidence, and original identity differs from the successor:

```json
{
  "aph_id": "example-member",
  "recorded_school_name": "Old School",
  "institution_ref": "acara:12345",
  "relationship_type": "successor",
  "attended_institution_ref": "manual:old-school",
  "attended_identity_source_url": "https://example.org/original-identity",
  "historical_latitude": -42.0,
  "historical_longitude": 147.0,
  "historical_location_source_url": "https://example.org/historical-campus",
  "historical_broad_sector": "Government",
  "historical_broad_sector_source_url": "https://example.org/historical-sector"
}
```

```powershell
uv run apemap review map-education <education-review-id> --payload scoped-successor.json --institution-ref acara:12345 --relationship-type successor --source https://example.org/successor-relationship --dry-run
```

The accepted `manual:old-school` definition must already exist. These facts apply
to this assertion. The projector records the generated
`historical_context_scope: "assertion"` marker and confirms the scope of supplied
historical evidence. Authors do not set that marker. A school-wide successor
still requires `historical_scope_confirmed: true` to attest that its historical
facts cover the associated attendance records and verified aliases.

Scoped historical facts cannot fill another person's missing campus or sector
evidence, even if both assertions identify the same original institution.
School-wide facts cannot fill a scoped assertion's omitted historical fields.
Without an explicit original institution, scoped successor attendance retains a
separate provisional assertion identity; the same recorded name or successor
does not prove that two members attended the same original school. With explicit
matching original identities, counting can still identify the shared school.
The [successor guide](successor-context.md) explains the map, profile and finance
interpretation.

## Leave an assertion unresolved

```powershell
uv run apemap review research <education-review-id> --note "Two same-name schools remain plausible" --dry-run
```

For a named source assertion, research preserves the attendance row and its
evidence but clears the reviewed institution relationship and historical context.
New CLI and guided-form research decisions automatically record
`resolution_only: true` to distinguish this institution-resolution decision from
older research that withdrew an accepted attendance claim.
Replay creates an unresolved institution placeholder and bypasses the school-wide
default and automatic match for that assertion. Other members with the same
recorded school name keep their own applicable resolutions.
Unresolved scoped assertions retain separate provisional identities. Coverage
counts them as unmatched institution records even when the attendance evidence
itself remains verified.

`reject` retains its existing attendance meaning: it removes the reviewed
attendance claim from effective replay. Use research when the attendance claim
is retained but its institution identity is uncertain. The append-only source
baseline and decision history remain available for later correction.

## Legacy defaults and migration

An assertion relationship takes precedence over a school-wide default. General
school mappings remain fallback relationships for assertions without their own
mapping or named research override. A disagreement is a review diagnostic,
not a failure to save a valid assertion-specific mapping. `review check`, detail
and preview output identify the assertion, school review item, competing targets
and relationship types so reviewers can reconsider an overbroad default.

Schema version 1 events remain readable. Existing education accepts without a
`relationship_type` retain their previous fallback behavior: an omitted reference
uses the default, and an explicit reference equal to the default's target inherits
its relationship and historical context. A different explicit reference retains
its own direct relationship. To give a new accepted attendance claim independent
relationship meaning, include both `institution_ref` and `relationship_type`
(or use `accept --relationship-type`). Use `map-education` to resolve an existing
claim while retaining its attendance facts. Replacing a prior decision requires
an appended superseding event; do not edit old ledger lines.
Legacy research events without `resolution_only: true` retain their previous
attendance-withdrawal behavior. Superseding a manual acceptance with such an event
restores the source attendance and ordinary matching fallback; it does not recover
the withdrawn manual claim. New resolution-only research can retain the accepted
attendance evidence while marking its institution relationship unresolved.

A read-only audit at implementation time found 297 retained events, 204 active
decisions and nine active education accepts, eight with explicit references.
Legacy accepts sharing a target with a school-wide rename or successor keep that
fallback interpretation. No migration rewrites the retained ledger, source
databases, historical release manifests, checksums or research outputs. The new
nullable `historical_context_scope` column upgrades existing canonical and review
source education tables; legacy null scope preserves school-wide consensus.

A read-only replay comparison used the preserved source tables in
`data/aped-review.duckdb`, the pinned historical ACARA register and the retained
ledger with SHA-256
`9a03ab1dd31e82cd380ebad8c9dfb44940aa9f23ec3b8658faac4bb9b8f7d62b`.
Against the projector at commit `3253bea5c63d6e699956d553186cff9711e43d17`, all
580 member, 1,754 service, 391 institution and 352 education rows matched after
excluding the new generated scope column. The audit also verified that the
ledger bytes were unchanged. Fixture coverage preserves the legacy institution
inventory, including a default target retained by older replay even when a
later attendance acceptance chooses another target.

This package MINOR change from 0.5.0 to 0.6.0 adds observable review behavior.
It does not publish a dataset release or refresh upstream data. Rebuild with the
pinned inputs and saved ledger, inspect the derived differences, and prepare an
unused dataset version through [decisions to a new release](decisions-to-release.md)
when producing a new delivery. Existing historical deliveries remain unchanged.

The checked-in historical release recipe retains its package 0.5.0 pin. To
build a new delivery with 0.6.0, pin a fresh recipe from that template before
building; do not rewrite an existing release's recipe or switch its package
version in place:

```powershell
uv run apemap release recipe pin --template data/release-recipes/historical.json --output <fresh-recipe.json>
```

Validation uses deterministic fixtures for legacy fallback, source fact parity,
scoped research and rejection, split diagnostics, context isolation, provisional
identities, schema upgrade and successor interpretation. Run:

```powershell
uv run pytest tests/test_assertion_migration.py tests/test_assertion_resolution.py tests/test_education_context.py
uv lock --check
```
