# Working editorial reference

Open [prototype.html](prototype.html) locally. It is self-contained and makes no
network requests. This reference implements the Editorial direction from #33;
the production Astro, Preact and MapLibre implementation belongs in cpoole-dev.
The original three-layout wireframes remain preserved one directory above.

## Reproduce

```text
uv run python -m apemap.prototype --release-dir data/processed/releases/0.3.0 --output-dir docs/design/issue-33/prototype
```

The input must pass strict release verification. Output cannot be inside the
immutable bundle. Identical input bytes produce identical HTML. The pinned v0.3.0
bundle contains 49 files and passes 213 checks. Its exact source commit, generation
time and dataset version appear in the reference and supplied manifest. The
historical dataset preview is a draft; it is not a public production data URL.

v0.3.0 predates `analysis/chamber_sectors.json`. For that baseline only, the
generator uses the canonical Python grouping helper on the published person
classifications in `members.json`. New releases export the aggregate directly.
No browser code reclassifies schooling or computes editorial findings.

## Chart grammar

- Layout: restrained hero → coverage → historical comparison → one term’s
  findings → explorer → methodology. The latest available term opens first;
  every term remains available through native disclosures without JavaScript.
- Headline categories: Government only, Non-government only, Mixed, Other.
  Missing schooling is separate. Detailed categories retain Combined/Multiple.
- Government uses eucalypt `#2f7142`; Non-government/Catholic uses bark `#8c664c`;
  Independent uses slate `#64748b`; Mixed/Combined uses violet `#7351a6`;
  Other uses grey `#737373`. Party names are labels, not sector colour encodings.
- Direct labels, counts and tables accompany every chart. Historical bars use
  the same category order. Demographic bars use neutral `#49624d`.
- Every subgroup shows total N, recorded-school N and missing N. Groups with
  recorded-school N below five show counts only. Zero denominators are unavailable.
- Student benchmarks are separate tables with population/year labels. Modern
  school profiles do not describe conditions when members attended.
- Use Inter/system sans-serif with readable prose width, wrapping grids and
  stacked mobile panels. No animations or horizontal page scrolling are required.

## Explorer behaviour

`#parliament=48&sector=Government&party=ALP&chamber=senate&q=school&school=<institution_id>`

All fields except parliament are optional. Unsupported options reset to the
unfiltered value; unsupported parliament defaults to the latest available term.
Filters use one parliament’s service context. Sector means school sector, not a
person’s Mixed classification. Search matches school or member names. Repeated
attendance records do not duplicate a school or a person within school details.
Selection survives reload and compatible filters; an excluded selection clears.

The complete school/member tables remain available without JavaScript. With
JavaScript, mobile still starts with list/search. The optional SVG locator plots
coordinates, including overseas points, without a basemap. It has a demonstrable
unavailable state. Lists and analytical denominators retain unmapped institutions.
Unmapped profile detail is not invented from the mapped GeoJSON payload.

No finance values are embedded. Profile detail explains ICSEA as context rather
than quality and labels percentage fields and their reporting year.

## Validation

Focused pytest tests cover canonical chamber counts, zero denominators, escaped
source text, verification rejection and deterministic generation. The optional
Node test checks the reference filter/URL contract offline:

```text
uv run pytest tests/test_prototype.py
node --test tests/prototype.test.cjs
```

Browser review covers 320, 390 and 1120 px, desktop/mobile previews, filtering,
empty results, selection reload, locator failure, reduced motion and no-JavaScript
fallback. The SVG locator is a design reference, not a performance test of MapLibre.
