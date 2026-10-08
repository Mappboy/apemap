"""Audit explorer semantics and measure static payloads without a browser/backend."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
import gzip
import json
from pathlib import Path
from typing import Any

from apemap.contracts import (
    WEB_SCHEMA_VERSION,
    SUCCESSOR_FOOTNOTE,
    SUCCESSOR_LOCATION_WARNING,
)
from apemap.export import (
    CONTEXT_COLUMNS,
    EVIDENCE_COLUMNS,
    PROFILE_COLUMNS,
    merge_school_contexts,
)


def audit_school_context(props: dict[str, Any], label: str) -> list[str]:
    """Validate v2 identities and each independently evidenced dimension."""
    required = {
        *CONTEXT_COLUMNS,
        "institution_id",
        "school_name",
        "display_school_name",
        "longitude",
        "latitude",
        "profile_year",
        "finance_year",
        "finance_status",
    }
    missing = required - props.keys()
    if missing:
        return [f"{label}: missing context fields {sorted(missing)}"]
    errors: list[str] = []
    if (
        props["attended_school_id"] is not None
        and props["institution_id"] != props["attended_school_id"]
    ):
        errors.append(f"{label}: attended identity disagrees with school key")
    successor = props["is_successor"]
    expected_title = (
        f"{props['school_name']} → {props['resolved_institution_name']}*"
        if successor
        else props["school_name"]
    )
    if props["display_school_name"] != expected_title:
        errors.append(f"{label}: successor display label disagrees")
    if successor and props.get("successor_footnote") != SUCCESSOR_FOOTNOTE:
        errors.append(f"{label}: successor footnote missing or inconsistent")
    multiple_providers = (
        props["resolved_institution_id"] is None
        and len(props.get("provider_contexts", [])) > 1
    )
    for dimension in ("profile", "finance"):
        if multiple_providers:
            if (
                props[f"{dimension}_institution_id"] is not None
                or props[f"{dimension}_basis"] != "multiple_providers"
            ):
                errors.append(
                    f"{label}: ambiguous {dimension} aggregate chooses a provider"
                )
            continue
        if props[f"{dimension}_institution_id"] != props[
            "resolved_institution_id"
        ] or props[f"{dimension}_basis"] != (
            "successor_context" if successor else "current_reference"
        ):
            errors.append(f"{label}: {dimension} provider/basis disagrees")
    basis = props["location_basis"]
    display = (props["longitude"], props["latitude"])
    attendance = (props["attendance_longitude"], props["attendance_latitude"])
    eligible = basis in (
        "original_verified",
        "successor_verified_same_campus",
        "original_reference",
    )
    if (
        basis
        not in (
            "original_verified",
            "successor_verified_same_campus",
            "original_reference",
            "successor_unverified",
            "unresolved",
        )
        or props["attendance_location_eligible"] != eligible
    ):
        errors.append(f"{label}: location basis and eligibility disagree")
    if eligible and (None in attendance or display != attendance):
        errors.append(
            f"{label}: attendance coordinates disagree with eligible display point"
        )
    if not eligible and attendance != (None, None):
        errors.append(f"{label}: ineligible attendance coordinates are populated")
    if basis == "unresolved" and display != (None, None):
        errors.append(f"{label}: unresolved location has display coordinates")
    if (
        basis in ("original_verified", "successor_verified_same_campus")
        and not props["location_source_url"]
    ):
        errors.append(f"{label}: verified location lacks evidence")
    if basis == "successor_unverified" and (
        not successor
        or None in display
        or props.get("location_warning") != SUCCESSOR_LOCATION_WARNING
    ):
        errors.append(f"{label}: successor fallback warning/coordinates disagree")
    if (
        props["sector_basis"] == "historical_verified"
        and not props["sector_source_url"]
    ):
        errors.append(f"{label}: historical broad sector lacks evidence")
    if (
        props["detailed_sector_basis"] == "historical_verified"
        and not props["detailed_sector_source_url"]
    ):
        errors.append(f"{label}: historical detailed sector lacks evidence")
    if props["sector_basis"] == "successor_assumption" and (
        not successor or props["broad_sector"] not in ("Government", "Non-government")
    ):
        errors.append(f"{label}: successor broad sector assumption disagrees")
    if (
        successor
        and not multiple_providers
        and props["detailed_sector_basis"] == "original_reference"
    ):
        errors.append(
            f"{label}: successor detailed sector lacks historical verification"
        )
    return errors


def _expected_school_context(rows: list[dict[str, Any]]) -> dict[str, Any]:
    context = dict(rows[0])
    merge_school_contexts(context, rows)
    return context


def _audit_feature_context(
    feature: dict[str, Any], rows: list[dict[str, Any]], label: str
) -> list[str]:
    props = feature["properties"]
    errors = audit_school_context(props, label)
    if not rows:
        return [*errors, f"{label}: no expected attendance assertions"]
    expected_context = _expected_school_context(rows)
    keys = (
        *CONTEXT_COLUMNS,
        *PROFILE_COLUMNS,
        "school_name",
        "display_school_name",
        "longitude",
        "latitude",
        "profile_year",
        "finance_year",
        "finance_status",
        "provider_contexts",
    )
    if any(props.get(key) != expected_context.get(key) for key in keys):
        errors.append(f"{label}: map/member context fields disagree")
    expected = {row["education_id"]: row for row in rows}
    published_rows = props.get("education_assertions", [])
    published = {row["education_id"]: row for row in published_rows}
    assertion_keys = (
        *CONTEXT_COLUMNS,
        *EVIDENCE_COLUMNS,
        "profile_year",
        "finance_year",
        "finance_status",
        "longitude",
        "latitude",
    )
    if (
        len(published_rows) != len(published)
        or set(published) != set(expected)
        or any(
            any(row.get(key) != expected[eid].get(key) for key in assertion_keys)
            for eid, row in published.items()
            if eid in expected
        )
    ):
        errors.append(f"{label}: map/member attendance evidence disagrees")
    if feature["geometry"]["coordinates"] != [
        props.get("longitude"),
        props.get("latitude"),
    ]:
        errors.append(f"{label}: geometry and display coordinates disagree")
    return errors


def _audit_school_relationships(
    props: dict[str, Any],
    expected: dict[str, set[int]],
    cohort_rows: dict[tuple[str, str], dict[str, Any]],
    label: str,
) -> list[str]:
    """Check every school/member/term relationship in either public map grain."""
    errors: list[str] = []
    people = props["members"]
    ids = [member["member_id"] for member in people]
    if (
        len(ids) != len(set(ids))
        or set(ids) != set(expected)
        or props["member_count"] != len(ids)
    ):
        errors.append(f"{label}: distinct member relationship counts disagree")
    expected_terms = {term for terms in expected.values() for term in terms}
    if set(props["parliaments"]) != expected_terms:
        errors.append(f"{label}: parliament arrays disagree")
    for member in people:
        mid = member["member_id"]
        service_terms = {service["parliament_number"] for service in member["services"]}
        if service_terms != expected.get(mid, set()) or service_terms != set(
            member["parliaments"]
        ):
            errors.append(f"{label}: member {mid} service/parliament arrays disagree")
        for service in member["services"]:
            p = str(service["parliament_number"])
            row = cohort_rows.get((p, mid))
            # members.json uses the full party name when no abbreviation exists.
            abbrev = service["party_abbrev"] or service["party"]
            if (
                row is None
                or any(service[key] != row[key] for key in ("party", "chamber"))
                or abbrev != row["party_abbrev"]
            ):
                errors.append(
                    f"{label}: member {mid} service context disagrees in parliament {p}"
                )
            elif member["name"] != row["display_name"]:
                errors.append(f"{label}: member {mid} display name disagrees")
    return errors


def audit_explorer_contract(release_dir: Path) -> dict[str, Any]:
    """Reconcile published person/service/school grains; do not derive new metrics."""
    root = release_dir.resolve()

    def read(name: str) -> Any:
        return json.loads((root / name).read_text(encoding="utf-8"))

    meta = read("web/metadata.json")
    summary_payload = read("web/results-summary.json")
    member_payload = read("web/members.json")
    school_payload = read("web/schools.geojson")
    summaries = summary_payload["parliaments"]
    members = member_payload["parliaments"]
    features = school_payload["features"]
    errors: list[str] = []
    v2 = meta.get("web_schema_version") == WEB_SCHEMA_VERSION
    if v2 and any(
        payload.get("web_schema_version") != WEB_SCHEMA_VERSION
        for payload in (summary_payload, member_payload, school_payload)
    ):
        errors.append("Web schema declarations disagree")
    assertions: dict[str, dict[str, dict[str, Any]]] = {}
    term_assertions: dict[str, dict[str, dict[str, dict[str, Any]]]] = {}
    profile_years: dict[str, int] = {}
    snapshot_path = root / "data/school_snapshots.csv"
    if v2 and snapshot_path.exists():
        with snapshot_path.open(encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                iid, year = row["institution_id"], int(row["snapshot_year"])
                profile_years[iid] = max(profile_years.get(iid, year), year)
    selected = {str(p) for p in meta["parliaments"]}
    sensitivity_reports: list[tuple[str, dict[str, Any]]] = []
    if v2:
        for name, embedded in (
            ("analysis/successor_sensitivity.json", False),
            ("analysis/education_sectors.json", True),
        ):
            try:
                payload = read(name)["parliaments"]
                sensitivity_reports.append(
                    (
                        name,
                        {
                            p: row["successor_sensitivity"] if embedded else row
                            for p, row in payload.items()
                        },
                    )
                )
                if set(payload) != selected:
                    errors.append(f"{name}: sensitivity parliament selection disagrees")
            except (OSError, ValueError, KeyError) as exc:
                errors.append(
                    f"Cannot read required sensitivity publication {name}: {exc}"
                )
    if (
        meta.get("cohort") != "opening_day"
        or selected != set(summaries)
        or selected != set(members)
    ):
        errors.append(
            "Metadata, summaries and members disagree on opening-day parliament selection"
        )
    mapped = {f["properties"]["institution_id"]: f for f in features}
    if len(mapped) != len(features):
        errors.append("School features contain duplicate institution IDs")
    expected_relationships: dict[str, dict[str, set[int]]] = {}
    cohort_rows: dict[tuple[str, str], dict[str, Any]] = {}
    layers: dict[str, list[dict[str, Any]]] = {}
    counts: dict[str, Any] = {}
    for p in sorted(selected, key=int):
        people = members.get(p, [])
        summary = summaries.get(p, {})
        ids = {m["member_id"] for m in people}
        if len(ids) != len(people) or len(people) != summary.get(
            "total_parliamentarians"
        ):
            errors.append(
                f"Parliament {p}: distinct opening-day people do not reconcile"
            )
        categories = Counter(m["government_non_government"] for m in people)
        headline_keys = (
            "government_only",
            "non_government_only",
            "mixed",
            "other",
            "no_school_recorded",
        )
        detailed_keys = {
            "Government": "government",
            "Catholic": "catholic",
            "Independent": "independent",
            "Combined/Multiple": "combined_multiple",
            "Other": "other",
            "No School Recorded": "no_school_recorded",
        }
        detailed = Counter(m["education_classification"] for m in people)
        if (
            set(categories) - set(headline_keys)
            or {key: categories[key] for key in headline_keys}
            != summary.get("government_non_government")
            or set(detailed) - set(detailed_keys)
            or {key: detailed[label] for label, key in detailed_keys.items()}
            != summary.get("detailed_sector")
        ):
            errors.append(f"Parliament {p}: person sector counts do not reconcile")
        missing = sum(not member["schools"] for member in people)
        known = len(people) - missing
        if (
            summary.get("known_school_denominator") != known
            or summary.get("known_education_count") != known
            or summary.get("missing_education_count") != missing
            or categories["no_school_recorded"] != missing
            or detailed["No School Recorded"] != missing
        ):
            errors.append(
                f"Parliament {p}: known/missing person denominators do not reconcile"
            )
        school_ids: set[str] = set()
        for member in people:
            cohort_rows[p, member["member_id"]] = member
            if member["parliament_number"] != int(p):
                errors.append(
                    f"Parliament {p}: member has another parliament's service context"
                )
            for school in member["schools"]:
                iid = school["institution_id"]
                if v2:
                    errors.extend(
                        audit_school_context(
                            school, f"Member {member['member_id']} school {iid}"
                        )
                    )
                    assertions.setdefault(iid, {})[school["education_id"]] = school
                    term_assertions.setdefault(p, {}).setdefault(iid, {})[
                        school["education_id"]
                    ] = school
                    if snapshot_path.exists() and school.get(
                        "profile_year"
                    ) != profile_years.get(school.get("profile_institution_id")):
                        errors.append(
                            f"School {iid}: profile year does not match actual provider snapshot"
                        )
                school_ids.add(iid)
                expected_relationships.setdefault(iid, {}).setdefault(
                    member["member_id"], set()
                ).add(int(p))
        mapped_ids = school_ids & mapped.keys()
        if v2:
            mapped_ids = {
                iid
                for iid, rows in term_assertions.get(p, {}).items()
                if _expected_school_context(list(rows.values()))["longitude"]
                is not None
                and _expected_school_context(list(rows.values()))["latitude"]
                is not None
            }
        if len(school_ids) != summary.get("number_of_represented_schools"):
            errors.append(
                f"Parliament {p}: represented school count does not reconcile"
            )
        if len(mapped_ids) != summary.get("mapped_schools_count") or len(
            school_ids - mapped_ids
        ) != summary.get("unmapped_schools_count"):
            errors.append(f"Parliament {p}: mapped/unmapped counts do not reconcile")
        if v2:
            from apemap.sensitivity_contract import (
                audit_successor_sensitivity,
                audit_sensitivity_publication,
            )

            sensitivity = summary.get("successor_sensitivity", {})
            cohort_assertions = [
                row
                for rows in term_assertions.get(p, {}).values()
                for row in rows.values()
            ]
            errors.extend(
                f"Parliament {p}: {error}"
                for error in audit_successor_sensitivity(
                    sensitivity, people, cohort_assertions
                )
            )
            for name, report in sensitivity_reports:
                errors.extend(
                    audit_sensitivity_publication(
                        sensitivity, report.get(p, {}), label=f"{name} Parliament {p}"
                    )
                )
            selected_years = sorted(
                {
                    row["profile_year"]
                    for rows in term_assertions.get(p, {}).values()
                    for row in rows.values()
                    if row["profile_year"] is not None
                }
            )
            declared = summary.get("source_years", {})
            if declared.get("profile_years") != selected_years or declared.get(
                "profile_year"
            ) != (selected_years[0] if len(selected_years) == 1 else None):
                errors.append(f"Parliament {p}: selected profile source years disagree")
        layer = read(f"web/parliament_{p}_combined.geojson")["features"]
        layers[p] = layer
        layer_ids = [f["properties"]["institution_id"] for f in layer]
        if len(layer_ids) != len(set(layer_ids)) or set(layer_ids) != mapped_ids:
            errors.append(f"Parliament {p}: map layer does not match its cohort")
        for feature in layer:
            iid = feature["properties"]["institution_id"]
            if (
                not v2
                and iid in mapped
                and feature["geometry"] != mapped[iid]["geometry"]
            ):
                errors.append(
                    f"Parliament {p}: school coordinates disagree across layers"
                )
        counts[p] = {
            "people": len(people),
            "represented_schools": len(school_ids),
            "mapped_schools": len(mapped_ids),
            "unmapped_schools": len(school_ids - mapped_ids),
        }
    for iid, feature in mapped.items():
        if v2:
            errors.extend(
                _audit_feature_context(
                    feature, list(assertions.get(iid, {}).values()), f"School {iid}"
                )
            )
        errors.extend(
            _audit_school_relationships(
                feature["properties"],
                expected_relationships.get(iid, {}),
                cohort_rows,
                f"School {iid}",
            )
        )
    for p, layer in layers.items():
        for feature in layer:
            iid = feature["properties"]["institution_id"]
            if v2:
                rows = list(term_assertions.get(p, {}).get(iid, {}).values())
                errors.extend(
                    _audit_feature_context(
                        feature, rows, f"Parliament {p} school {iid}"
                    )
                )
            expected = {
                mid: {int(p)}
                for mid, terms in expected_relationships.get(iid, {}).items()
                if int(p) in terms
            }
            errors.extend(
                _audit_school_relationships(
                    feature["properties"],
                    expected,
                    cohort_rows,
                    f"Parliament {p} school {iid}",
                )
            )
    if v2:
        expected_mapped = {
            iid
            for iid, rows in assertions.items()
            if _expected_school_context(list(rows.values()))["longitude"] is not None
            and _expected_school_context(list(rows.values()))["latitude"] is not None
        }
        if set(mapped) != expected_mapped:
            errors.append(
                "Aggregate mapped schools do not reconcile with attendance contexts"
            )
    assets = {}
    for path in sorted((root / "web").iterdir()):
        if path.suffix in (".json", ".geojson"):
            payload = path.read_bytes()
            assets[path.relative_to(root).as_posix()] = {
                "raw_bytes": len(payload),
                "gzip_bytes": len(gzip.compress(payload, mtime=0)),
            }
    return {
        "valid": not errors,
        "errors": errors,
        "release_version": meta["release_version"],
        "source_commit": meta["source_commit"],
        "parliaments": counts,
        "assets": assets,
    }


def main() -> None:
    """Write a local audit report and exit unsuccessfully on semantic disagreement."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release_dir", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = audit_explorer_contract(args.release_dir)
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text, encoding="utf-8", newline="\n")
    print(text, end="")
    raise SystemExit(0 if result["valid"] else 1)


if __name__ == "__main__":
    main()
