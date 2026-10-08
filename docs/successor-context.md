# Original schools and reviewed successors

Package 0.4.0 introduces web and analysis contracts `2.0.0`. Older release
payloads remain unchanged. Rebuild into an unused dataset MINOR version before
updating a frontend pin; upgrading the package does not update released data.

## Identity and evidence

An education assertion keeps its original `school_name_as_recorded`, stable
`education_id`, attendance evidence, confidence and resolved `institution_id`.
The resolved institution supplies contemporary profiles and finance context.
It is not automatically the school or campus attended.

A successor decision can supply `attended_institution_ref` with a separate
`attended_identity_source_url`. Use a real historical ACARA reference or an
accepted manual institution definition. The original reference must differ from
the successor; use a sourced manual predecessor when an ACARA registration was
reused. Verified alternate names sharing an original reference share an attended
school identity. Without that evidence, retain a provisional recorded-name
identity using the frozen review key. Missing original names remain unresolved.

The original identity source establishes identity, not historical coordinates or
sector. Supply independent evidence through these optional school-decision fields:

| Dimension | Fields |
| --- | --- |
| Original campus | `historical_latitude`, `historical_longitude`, `historical_location_source_url` |
| Campus continuity | `campus_continuity` (`same_campus` or `different_campus`), `campus_continuity_source_url` |
| Broad sector | `historical_broad_sector` (`Government` or `Non-government`), `historical_broad_sector_source_url` |
| Detailed sector | `historical_detailed_sector` (`Catholic` or `Independent`), `historical_detailed_sector_source_url` |

Evidence applies school-wide, covering the associated attendance records and
verified original-name aliases shown in the preview. Leave a dimension unresolved
when a source establishes only one era and cannot support that scope. This version
does not model attendance-specific exceptions or effective date ranges. Matching
confidence, a relationship URL and registry presence do not verify campus or sector
continuity. Conflicting reviewed facts suppress the affected dimension and surface
review diagnostics instead of selecting a value by event or row order.

Historical evidence payloads explicitly set `historical_scope_confirmed` to `true`;
the guided form provides the same school-wide scope confirmation. This records the
scope of the evidence in the accepted decision, rather than inventing attendance
dates or silently treating a contemporary source as historical verification.

Use the existing CLI `review accept --payload` workflow or the guided local school
form, preview the complete scope, then save. Corrections append superseding events;
do not edit historical ledger lines. Rebuilding applies the saved evidence.

## Map and result interpretation

Successor-linked titles use **Original school → Successor school***. Search includes
recorded, original and resolved names. Different predecessors remain separate even
when their successor coordinates coincide. Missing-coordinate records remain in
searchable results and attendance downloads.

| Location basis | Map display | Attendance geography |
| --- | --- | --- |
| `original_verified` | Verified original campus | Eligible |
| `successor_verified_same_campus` | Successor coordinates with reviewed continuity | Eligible |
| `successor_unverified` | Hollow successor-campus marker | Excluded |
| `unresolved` | Result without a map marker | Excluded |
| `original_reference` | Existing non-successor reference coordinates | Existing reference interpretation |

The hollow marker explanation is **Successor campus; original attendance location
unverified.** Geographic attendance calculations must use the separate attendance
coordinates and eligibility flag, never an unverified successor display point.
Displayed mapped-school totals include hollow markers; verified attendance
geography is reported separately.

Use this explanation beside successor names and affected profile or finance values:

> * Linked to a reviewed successor institution. Its campus may differ from the school attended. Profile and finance figures describe the successor in the stated reporting year.

The successor flag is separate from attendance confidence and observed/estimated
finance status. Profiles retain their actual profile year. Finance retains the
reporting institution, requested reporting year, method and sources. Neither
describes resources at the time a member attended. Contemporary locality and
remoteness are reporting-institution context unless historical evidence establishes
their attendance interpretation.

## Sector analysis and counting

Verified historical classification takes precedence. Where it is unavailable,
the successor's Government/non-government classification can be used as an
explicit `successor_assumption`. Catholic/Independent is independently reviewed;
an assumed non-government classification cannot establish either detailed sector.
Historical Government establishes the detailed Government category. Independently
reviewed Catholic or Independent evidence establishes Non-government using the
same historical source. Contradictions
are exposed for review; unresolved conflicting evidence cannot fall back to a
successor assumption.

Count distinct attended-school identities, identifying provisional recorded-name
identities separately. Missing identities retain separate unresolved assertion
keys and an explicit unresolved count; they do not become verified institutions.
Count distinct people in member totals and shared-school
associations. Sharing a successor does not establish shared original attendance.
Financial samples instead count reporting institutions: two predecessors sharing
one successor finance record contribute two attended schools and one finance sample.

The sensitivity comparison removes successor-assumed sector values, retaining
every person, education assertion and recorded-school denominator. Classify the
remaining evidence, report incomplete histories, and use Other when schooling is
recorded but no usable classification remains. Unknown values do not create Mixed
or Combined/Multiple classifications. Labels such as **Government among classified
schools** indicate partial evidence. Report affected people/assertions/schools,
baseline and assumption-excluded counts, classified denominators, and percentage
point differences using the same recorded-school denominator.

See [the website contract](web-release.md) and [frontend handoff](explorer-handoff.md)
for joins and exported fields, and [review decisions](review-decisions.md) for
authoring and provenance. Production cpoole.dev implementation and the next dataset
build are separate from this package change.
