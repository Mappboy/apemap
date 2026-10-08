"""Generate the offline editorial reference from a verified immutable release.

Run ``uv run python -m apemap.prototype --release-dir PATH --output-dir PATH``.
This is a design artifact, not the production cpoole.dev implementation.
"""

from __future__ import annotations

import argparse
from html import escape
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from apemap.analysis import summarize_classified_members
from apemap.contracts import (
    SUCCESSOR_FOOTNOTE,
    SUCCESSOR_LOCATION_WARNING,
    WEB_SCHEMA_VERSION,
)
from apemap.release.verify import verify_release
from apemap.export import EVIDENCE_COLUMNS, merge_school_contexts

ASSETS = Path(__file__).with_name("prototype_assets")
HEADLINE = {
    "government_only": "Government among classified schools",
    "non_government_only": "Non-government among classified schools",
    "mixed": "Mixed Government + Non-government",
    "other": "Other / unresolved sector",
}
DETAIL = {
    "Government": "Government",
    "Catholic": "Catholic",
    "Independent": "Independent",
    "Combined/Multiple": "Combined/Multiple",
    "Other": "Other",
}
COLOURS = ["#2f7142", "#8c664c", "#7351a6", "#737373"]
SECTOR_COLOURS = dict(zip(HEADLINE, COLOURS, strict=True)) | {
    "Government": "#2f7142",
    "Catholic": "#8c664c",
    "Independent": "#64748b",
    "Combined/Multiple": "#7351a6",
    "Other": "#737373",
}
PROFILE_FIELDS = (
    "profile_year",
    "icsea",
    "icsea_percentile",
    "total_enrolments",
    "sea_bottom_quarter_pct",
    "sea_lower_middle_quarter_pct",
    "sea_upper_middle_quarter_pct",
    "sea_top_quarter_pct",
    "indigenous_enrolments_pct",
    "lbote_pct",
    "remoteness_category",
)


def read_json(root: Path, name: str) -> Any:
    """Read a UTF-8 release artifact."""
    return json.loads((root / name).read_text(encoding="utf-8"))


def ordinal(number: int) -> str:
    """Label a term without hard-coding its number or English suffix."""
    suffix = (
        "th"
        if 10 <= number % 100 <= 20
        else {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    )
    return f"{number}{suffix}"


def table(headers: list[str], rows: list[list[Any]], caption: str) -> str:
    """Produce a semantic table with escaped source values."""
    head = "".join(f"<th scope='col'>{escape(h)}</th>" for h in headers)
    body_rows = []
    for row in rows:
        cells = []
        for i, value in enumerate(row):
            tag = "th" if i == 0 else "td"
            scope = " scope='row'" if i == 0 else ""
            cells.append(f"<{tag}{scope}>{escape(str(value))}</{tag}>")
        body_rows.append("<tr>" + "".join(cells) + "</tr>")
    body = "".join(body_rows)
    return f"<div class='table-wrap'><table><caption>{escape(caption)}</caption><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"


def bars(
    counts: dict[str, int],
    labels: dict[str, str],
    denominator: int,
    title: str,
    denominator_label: str = "people with recorded schooling",
    neutral: bool = False,
) -> str:
    """Generate labelled SVG geometry at build time; all values also have a table."""
    rows = []
    svg = []
    for i, (key, label) in enumerate(labels.items()):
        count = counts.get(key, 0)
        pct = count / denominator * 100 if denominator else None
        display = f"{pct:.1f}%" if pct is not None else "Unavailable"
        y = i * 52
        colour = "#49624d" if neutral else SECTOR_COLOURS.get(key, "#737373")
        svg.append(
            f"<text x='0' y='{y + 15}'>{escape(label)} · {count} ({display})</text><rect x='0' y='{y + 24}' width='{(pct or 0) * 3:.2f}' height='14' fill='{colour}'/>"
        )
        rows.append([label, count, display])
    return (
        f"<svg class='chart' viewBox='0 0 410 {len(labels) * 52}' role='img' aria-label='{escape(title, quote=True)}'>"
        + "".join(svg)
        + "</svg>"
        + table(
            ["Category", "People", "Share"],
            rows,
            f"{title}: denominator {denominator} {denominator_label}",
        )
    )


def explorer_payload(root: Path, metadata: dict[str, Any]) -> dict[str, Any]:
    """Join published member-school records with mapped profiles; omit finance."""
    members = read_json(root, "web/members.json")["parliaments"]
    features = read_json(root, "web/schools.geojson")["features"]
    schools: dict[str, dict[str, Any]] = {}
    relation_contexts: dict[str, list[dict[str, Any]]] = {}
    # Member assertions contain unmapped institutions and their actual profile
    # context. Preserve that complete source rather than deriving it from points.
    omitted = {
        "finance_value",
        "finance_metric",
        "education_id",
        "member_id",
        "retrieved_at",
    }
    for people in members.values():
        for member in people:
            for relation in member["schools"]:
                iid = relation["institution_id"]
                relation_contexts.setdefault(iid, []).append(relation)
                school = schools.setdefault(
                    iid,
                    {
                        **{
                            key: value
                            for key, value in relation.items()
                            if key not in omitted
                        },
                        "school_sector": relation.get(
                            "school_sector", relation.get("sector")
                        )
                        or "Other",
                        "coordinates": None,
                        "education_assertions": [],
                    },
                )
                evidence = {key: relation.get(key) for key in EVIDENCE_COLUMNS}
                if (
                    evidence["education_id"]
                    and evidence not in school["education_assertions"]
                ):
                    school["education_assertions"].append(evidence)
    if metadata.get("web_schema_version") == WEB_SCHEMA_VERSION:
        for iid, school in schools.items():
            merge_school_contexts(school, relation_contexts[iid])
    for feature in features:
        props = feature["properties"]
        iid = props["institution_id"]
        if iid in schools:
            schools[iid].update(
                {
                    key: value
                    for key, value in props.items()
                    if key not in omitted | {"members", "member_count", "parliaments"}
                }
            )
            schools[iid]["coordinates"] = feature["geometry"]["coordinates"]
    # The member payload also carries finance values in v2; this reference only
    # exposes provider, reporting year and status as source context.
    members = {
        p: [
            {
                **member,
                "schools": [
                    {
                        key: value
                        for key, value in relation.items()
                        if key not in {"finance_value", "finance_metric"}
                    }
                    for relation in member["schools"]
                ],
            }
            for member in people
        ]
        for p, people in members.items()
    }
    return _without_finance_values(
        {
            "parliaments": metadata["parliaments"],
            "members": members,
            "schools": dict(sorted(schools.items())),
            "successor_footnote": SUCCESSOR_FOOTNOTE,
            "successor_location_warning": SUCCESSOR_LOCATION_WARNING,
        }
    )


def _without_finance_values(value: Any) -> Any:
    """Strip values recursively from assertions and alternate provider context."""
    if isinstance(value, dict):
        return {
            key: _without_finance_values(item)
            for key, item in value.items()
            if key not in {"finance_value", "finance_metric"}
        }
    if isinstance(value, list):
        return [_without_finance_values(item) for item in value]
    return value


def school_title(school: dict[str, Any]) -> str:
    """Return the explicit original-to-successor title with a shared footnote."""
    return school.get("display_school_name") or school["school_name"]


def school_evidence_html(school: dict[str, Any]) -> str:
    """Render independently labelled flags and safe evidence links without JS."""
    facts = [
        ("Broad sector", school.get("broad_sector")),
        ("Broad sector basis", school.get("sector_basis")),
        ("Detailed sector", school.get("detailed_sector")),
        ("Detailed sector basis", school.get("detailed_sector_basis")),
        ("Location basis", school.get("location_basis")),
        ("Attendance location eligible", school.get("attendance_location_eligible")),
        ("Campus continuity conflict", school.get("campus_continuity_conflict")),
        ("Continuity discrepancy", school.get("continuity_discrepancy")),
        ("Profile provider", school.get("profile_institution_id")),
        ("Profile basis", school.get("profile_basis")),
        ("Profile year", school.get("profile_year")),
        ("Finance provider", school.get("finance_institution_id")),
        ("Finance basis", school.get("finance_basis")),
        ("Finance year", school.get("finance_year")),
        ("Finance status", school.get("finance_status")),
    ]
    html = "<details><summary>" + escape(school_title(school)) + "</summary>"
    if school.get("is_successor"):
        html += f"<p>{escape(SUCCESSOR_FOOTNOTE)}</p>"
    html += "".join(
        f"<p>{label}: {escape(str(value)) + ('*' if school.get('is_successor') and label.startswith(('Profile', 'Finance')) else '') if value is not None else 'Unavailable'}</p>"
        for label, value in facts
    )
    for provider in school.get("provider_contexts", []):
        marker = "*" if provider["profile_basis"] == "successor_context" else ""
        html += f"<p>Reporting provider: {escape(provider['resolved_institution_name'])}{marker}; profile year: {provider['profile_year'] or 'Unavailable'}{marker}; finance year: {provider['finance_year'] or 'Unavailable'}{marker}.</p>"
    if school.get("location_warning"):
        html += f"<p>{escape(school['location_warning'])}</p>"
    links = [
        ("Original identity evidence", school.get("attended_identity_source_url")),
        ("Location evidence", school.get("location_source_url")),
        ("Broad sector evidence", school.get("sector_source_url")),
        ("Detailed sector evidence", school.get("detailed_sector_source_url")),
    ]
    for evidence in school.get("education_assertions", []):
        html += f"<p>Recorded school name: {escape(str(evidence.get('school_name_as_recorded') or 'Unavailable'))}.</p>"
        html += f"<p>Attendance: {escape(str(evidence.get('attended_status') or 'Unavailable'))}; confidence: {escape(str(evidence.get('confidence') or 'Unavailable'))}.</p>"
        links += [
            (
                "Original identity evidence",
                evidence.get("attended_identity_source_url"),
            ),
            (
                "Original location evidence",
                evidence.get("historical_location_source_url"),
            ),
            (
                "Campus continuity evidence",
                evidence.get("campus_continuity_source_url"),
            ),
            (
                "Historical broad sector evidence",
                evidence.get("historical_broad_sector_source_url"),
            ),
            (
                "Historical detailed sector evidence",
                evidence.get("historical_detailed_sector_source_url"),
            ),
            ("Attendance evidence", evidence.get("source_url")),
            ("Relationship evidence", evidence.get("resolution_source_url")),
        ]
    for label, url in dict.fromkeys(links):
        if url and urlsplit(url).scheme in ("http", "https"):
            html += f'<p><a href="{escape(url, quote=True)}">{label}</a></p>'
    return html + "</details>"


def render_editorial(root: Path, metadata: dict[str, Any]) -> str:
    """Render all terms without needing JavaScript to select a finding."""
    summaries = read_json(root, "web/results-summary.json")["parliaments"]
    coverage = {
        str(p["parliament"]): p
        for p in read_json(root, "analysis/parliament_coverage.json")["parliaments"]
    }
    demographics = read_json(root, "analysis/demographics.json")["parliaments"]
    parties = read_json(root, "analysis/party_sectors.json")["parliaments"]
    shared = read_json(root, "analysis/shared_schools.json")["parliaments"]
    school_context = explorer_payload(root, metadata)["schools"]
    members = read_json(root, "web/members.json")["parliaments"]
    chamber_path = root / "analysis/chamber_sectors.json"
    chambers = (
        read_json(root, "analysis/chamber_sectors.json")["parliaments"]
        if chamber_path.exists()
        else {
            p: summarize_classified_members(people, int(p))
            for p, people in members.items()
        }
    )
    latest = str(max(metadata["parliaments"]))
    current = summaries[latest]
    chunks = [
        f"<div class='stats'><div><strong>{current['total_parliamentarians']}</strong>Opening-day members</div><div><strong>{current['known_school_denominator']} / {current['total_parliamentarians']}</strong>Members with recorded schooling</div><div><strong>{current['number_of_represented_schools']}</strong>Distinct represented institutions</div></div>"
    ]
    chunks.append(
        "<section><h2>Read coverage before comparing proportions</h2><p>Recorded schooling includes unresolved school names. Verified/provisional coverage requires a resolved institution. Neither percentage describes the whole parliament when schooling is missing.</p>"
    )
    chunks.append(
        table(
            [
                "Parliament",
                "All people",
                "Recorded schooling",
                "Verified/provisional",
                "No recorded school",
                "Mapped / represented schools",
            ],
            [
                [
                    p,
                    s["total_parliamentarians"],
                    s["known_school_denominator"],
                    coverage[p]["members_with_secondary_school"],
                    s["missing_education_count"],
                    f"{s['mapped_schools_count']} / {s['number_of_represented_schools']}",
                ]
                for p, s in summaries.items()
            ],
            "Opening-day evidence coverage; people and institutions have separate denominators",
        )
    )
    chunks.append(
        "</section><section><h2>How does the recorded sector mix change?</h2><p>Discrete opening-day snapshots. Coverage changes between terms; this is not an estimate of all members’ schooling.</p>"
    )
    for p, summary in summaries.items():
        count = summary["government_non_government"]
        known = summary["known_school_denominator"]
        segments = []
        for i, key in enumerate(HEADLINE):
            share = count[key] / known * 100 if known else 0
            segments.append(
                f"<span style='width:{share:.4f}%;background:{COLOURS[i]}' title='{escape(HEADLINE[key])}: {count[key]}'></span>"
            )
        chunks.append(
            f"<div class='history-row'><strong>{p} · n={known}</strong><div class='stack' aria-hidden='true'>{''.join(segments)}</div></div>"
        )
    chunks.append(
        table(
            ["Parliament", *HEADLINE.values(), "Known denominator"],
            [
                [
                    p,
                    *[s["government_non_government"][k] for k in HEADLINE],
                    s["known_school_denominator"],
                ]
                for p, s in summaries.items()
            ],
            "Historical sector counts; Government / Non-government / Mixed / Other",
        )
    )
    chunks.append(
        "</section><section><h2>Findings for each parliament</h2><p>The latest snapshot opens first. Every term’s charts and tables are available without JavaScript.</p>"
    )
    for p, summary in summaries.items():
        opening = metadata["parliament_metadata"][p]["opening_date"]
        chunks.append(
            f"<details class='term' {'open' if p == latest else ''}><summary>{ordinal(int(p))} Parliament · opening {opening}</summary><p>Opening-day cohort: {summary['total_parliamentarians']} people; sector denominator: {summary['known_school_denominator']} with recorded schooling; {summary['missing_education_count']} without a recorded school.</p><h3>Which sectors did members attend?</h3>"
        )
        chunks.append(
            bars(
                summary["government_non_government"],
                HEADLINE,
                summary["known_school_denominator"],
                "Headline school-sector classification",
            )
        )
        chunks.append(
            "<details><summary>Government / Catholic / Independent detail</summary>"
        )
        detailed = {
            label: summary["detailed_sector"][key]
            for label, key in zip(
                DETAIL,
                ("government", "catholic", "independent", "combined_multiple", "other"),
                strict=True,
            )
        }
        chunks.append(
            bars(
                detailed,
                DETAIL,
                summary["known_school_denominator"],
                "Detailed person sectors",
            )
            + "</details>"
        )
        benchmark = summary["abs_sector_benchmark"]
        sensitivity = summary.get("successor_sensitivity")
        if sensitivity:
            baseline = sensitivity["baseline"]
            strict = sensitivity["without_successor_assumptions"]
            chunks.append(
                "<h3>How much depends on successor sector assumptions?</h3>"
                f"<p>Both classifications retain {baseline['total_parliamentarians']} people "
                f"and recorded-school n={baseline['known_school_denominator']}. "
                f"Successor assumptions affect {sensitivity['affected_members']} people, "
                f"{sensitivity['affected_assertions']} assertions and "
                f"{sensitivity['affected_schools']} attended schools. Removing assumptions "
                "keeps their attendance and moves unavailable sector values into uncertainty.</p>"
                + table(
                    ["Classification", "Baseline people", "Without assumptions"],
                    [
                        [
                            label,
                            baseline["government_non_government"][key],
                            strict["government_non_government"][key],
                        ]
                        for key, label in HEADLINE.items()
                    ],
                    "Successor sensitivity on the same people and recorded-school denominator",
                )
            )
        chunks.append(
            "<h3>Compare with today’s students</h3><p>The student population is a separate comparison, not the school environment at attendance. Mixed and Other people remain in the parliamentary denominator.</p>"
        )
        chunks.append(
            table(
                ["Sector", "Parliament share", "Student share", "Benchmark year"],
                [
                    [
                        k,
                        f"{v['parliamentary_share'] * 100:.1f}%",
                        f"{v['student_enrolment_share'] * 100:.1f}%",
                        v["benchmark_year"],
                    ]
                    for k, v in benchmark.items()
                ],
                "ABS comparison population, Schools publication",
            )
        )
        for title, groups in (
            ("Party", parties[p]["parties"]),
            ("Chamber", chambers[p]["chambers"]),
        ):
            chunks.append(
                f"<h3>Does the pattern differ by {title.lower()}?</h3><div class='multiples'>"
            )
            for name, group in groups.items():
                known = group["known_school_denominator"]
                chunks.append(
                    f"<article class='group'><h4>{escape(name)}</h4><p>Total n={group['total_parliamentarians']}; recorded-school n={known}; missing={group['parliamentarians_without_known_schools']}.</p>"
                )
                if known < 5:
                    chunks.append(
                        "<p>Fewer than five recorded-school members: counts only.</p>"
                        + table(
                            ["Category", "People"],
                            [
                                [HEADLINE[k], group["government_non_government"][k]]
                                for k in HEADLINE
                            ],
                            f"{title} subgroup counts",
                        )
                    )
                else:
                    chunks.append(
                        bars(
                            group["government_non_government"],
                            HEADLINE,
                            known,
                            f"{name} sectors",
                        )
                    )
                chunks.append("</article>")
            chunks.append("</div>")
        demo = demographics[p]
        chunks.append("<h3>Who makes up the cohort?</h3>")
        for label, key in (
            ("Age at opening", "age_brackets"),
            ("Gender", "genders"),
            ("Chamber", "chambers"),
        ):
            chunks.append(
                bars(
                    demo[key],
                    {k: k for k in demo[key]},
                    summary["total_parliamentarians"],
                    label,
                    denominator_label="opening-day people",
                    neutral=True,
                )
            )
        chunks.append(
            f"<p>Age known for {demo['known_age_sample_size']} people; missing for {demo['missing_age_count']}.</p><h3>Which schools are most commonly shared?</h3><p>Ranked by distinct people. Attendance does not equate to graduation.</p><ol class='ranked'>"
        )
        for school in shared[p]["schools"][:10]:
            names = ", ".join(m["display_name"] for m in school["members"])
            context = school_context.get(school["institution_id"], school)
            chunks.append(
                f"<li><details><summary>{escape(school_title(context))} · {school['member_count']} people</summary><p>{escape(names)}</p><p>{escape(str(school['sector']))} · {escape(str(school['state']))}</p>"
                + (
                    f"<p>{escape(SUCCESSOR_FOOTNOTE)}</p>"
                    if context.get("is_successor")
                    else ""
                )
                + "</details></li>"
            )
        chunks.append(
            f"</ol><p>Latest profile years represented: {escape(', '.join(map(str, summary['source_years']['profile_years'])) or 'Unavailable')}.</p></details>"
        )
    chunks.append("</section>")
    return "".join(chunks)


def build_prototype(release_dir: Path, output_dir: Path) -> Path:
    """Verify input, then write a deterministic offline reference outside its bundle."""
    root, out = release_dir.resolve(), output_dir.resolve()
    if out.is_relative_to(root):
        raise ValueError("Prototype output must be outside the immutable release")
    verified = verify_release(root, strict_assertions=True)
    if not verified["valid"]:
        raise ValueError(f"Release verification failed: {verified['errors']}")
    metadata = read_json(root, "web/metadata.json")
    payload = explorer_payload(root, metadata)
    script_data = json.dumps(payload, sort_keys=True, ensure_ascii=True).replace(
        "<", "\\u003c"
    )
    options = "".join(
        f"<option value='{p}' {'selected' if p == max(metadata['parliaments']) else ''}>{ordinal(p)} Parliament</option>"
        for p in metadata["parliaments"]
    )
    fallback = table(
        ["School", "Sector", "State", "Location"],
        [
            [
                school_title(s),
                s["school_sector"],
                s["state"] or "Unavailable",
                s.get("location_warning")
                or ("Mapped" if s["coordinates"] else "Coordinates unavailable"),
            ]
            for s in sorted(
                payload["schools"].values(), key=lambda s: s["school_name"] or ""
            )
        ],
        "All represented schools across the release; use browser Find without JavaScript",
    )
    fallback += f"<p>{escape(SUCCESSOR_FOOTNOTE)}</p>"
    fallback += "".join(
        school_evidence_html(school) for school in payload["schools"].values()
    )
    fallback += (
        "<details><summary>Complete opening-day member table</summary>"
        + table(
            [
                "Parliament",
                "Member",
                "Party / chamber",
                "Recorded schooling",
                "Sector context",
            ],
            [
                [
                    p,
                    m["display_name"],
                    f"{m['party_abbrev']} / {m['chamber']}",
                    ", ".join(sorted({school_title(s) for s in m["schools"]}))
                    or "No School Recorded",
                    (
                        m.get("education_classification_label")
                        or m["education_classification"]
                    )
                    + (
                        "; incomplete sector evidence"
                        if m.get("incomplete_sector_evidence")
                        else ""
                    ),
                ]
                for p, people in payload["members"].items()
                for m in people
            ],
            "People serving on opening day, including members without recorded schooling",
        )
        + "</details>"
    )
    version = escape(metadata["release_version"])
    terms = sorted(metadata["parliaments"])
    term_label = (
        f"{ordinal(terms[0])} Parliament"
        if len(terms) == 1
        else f"{ordinal(terms[0])}–{ordinal(terms[-1])} Parliaments"
    )
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; object-src 'none'">
<title>APEMAP editorial reference · {version}</title><style>{(ASSETS / "style.css").read_text(encoding="utf-8")}</style></head><body>
<a class="skip" href="#main">Skip to results</a><main id="main"><header><p class="eyebrow">APEMAP · WORKING DESIGN REFERENCE</p><h1>Where did our parliamentarians go to school?</h1><p class="standfirst">Secondary schooling, representation and the limits of the available evidence.</p><p>Dataset v{version} · opening-day cohorts · {term_label}</p><p>Source commit <code>{escape(metadata["source_commit"])}</code></p><nav><a href="#explorer">Explore schools and members</a> · <a href="#methodology">Read methodology</a></nav></header>
{render_editorial(root, metadata)}
<section id="explorer"><h2>Explore schools and members</h2><p>Filters apply only here. Sector filters describe schools, not Mixed person classifications. Unmapped schools and members without recorded schooling remain accessible.</p>
<form id="filters" hidden><div class="controls"><label>Parliament<select name="parliament">{options}</select></label><label>School sector<select name="sector"><option value="">All sectors</option><option>Government</option><option>Catholic</option><option>Independent</option><option>Other</option></select></label><label>Party<select name="party"></select></label><label>Chamber<select name="chamber"></select></label><label class="search">School or member search<input name="q" type="search" autocomplete="off"></label></div><button type="reset">Clear filters</button></form>
<p id="successor-footnote">{escape(SUCCESSOR_FOOTNOTE)}</p>
<div id="interactive" hidden><p id="result-count" role="status" aria-live="polite"></p><div class="explorer-grid"><div><h3>Schools</h3><div id="school-list"></div><details><summary>Matching members, including missing schooling</summary><div id="member-list"></div></details></div><aside><h3>Selected school</h3><div id="school-detail">Select a school from the list.</div><details id="locator"><summary>Show geographic locator</summary><p>Coordinates only; no basemap. Overseas locations remain included. National analytical totals never come from map points. Hollow points mean: {escape(SUCCESSOR_LOCATION_WARNING)}</p><div id="locator-content"></div><button id="locator-failure" type="button">Preview map unavailable</button></details></aside></div></div>
<details id="static-schools" open><summary>Complete school table · usable without JavaScript</summary>{fallback}</details></section>
<section id="methodology"><h2>Methodology, sources and handoff</h2><p>School assertions count attendance evidence, not graduation. Combined/Multiple means several classified detailed sectors; Mixed means classified Government and Non-government attendance. Other includes unavailable sectors. Incomplete sector evidence is reported separately. No School Recorded is shown separately.</p><p>ICSEA describes socio-educational context, not school quality. SEA quarters, Indigenous enrolment and LBOTE are percentages; absent fields remain unavailable.</p><p>{escape(metadata["temporal_warning"])}</p><p>Finance values and charts are omitted from this reference; provider, year and status remain source context. Profile fields come from the verified release.</p><p>Sources: Parliament of Australia, ACARA, ABS and AEC. Retain each source’s attribution and terms. Release generation time is not an upstream retrieval date.</p><p>Research downloads, source snapshot dates and hashes are listed in the supplied release manifest. This offline reference does not publish or host those downloads. Production routes and map hosting belong in cpoole-dev.</p></section></main>
<script id="explorer-data" type="application/json">{script_data}</script><script>{(ASSETS / "explorer.js").read_text(encoding="utf-8")}</script></body></html>"""
    out.mkdir(parents=True, exist_ok=True)
    target = out / "prototype.html"
    target.write_text(html + "\n", encoding="utf-8", newline="\n")
    return target


def main() -> None:
    """Parse local build inputs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(build_prototype(args.release_dir, args.output_dir))


if __name__ == "__main__":
    main()
