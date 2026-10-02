"""Audit explorer semantics and measure static payloads without a browser/backend."""

from __future__ import annotations

import argparse
from collections import Counter
import gzip
import json
from pathlib import Path
from typing import Any


def audit_explorer_contract(release_dir: Path) -> dict[str, Any]:
    """Reconcile published person/service/school grains; do not derive new metrics."""
    root = release_dir.resolve()

    def read(name: str) -> Any:
        return json.loads((root / name).read_text(encoding="utf-8"))

    meta = read("web/metadata.json")
    summaries = read("web/results-summary.json")["parliaments"]
    members = read("web/members.json")["parliaments"]
    features = read("web/schools.geojson")["features"]
    errors: list[str] = []
    selected = {str(p) for p in meta["parliaments"]}
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
    expected_people: dict[str, set[str]] = {}
    expected_terms: dict[str, set[int]] = {}
    cohort_rows: dict[tuple[str, str], dict[str, Any]] = {}
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
        if any(
            categories[key] != value
            for key, value in summary.get("government_non_government", {}).items()
        ):
            errors.append(f"Parliament {p}: person sector counts do not reconcile")
        school_ids: set[str] = set()
        for member in people:
            cohort_rows[p, member["member_id"]] = member
            if member["parliament_number"] != int(p):
                errors.append(
                    f"Parliament {p}: member has another parliament's service context"
                )
            for school in member["schools"]:
                iid = school["institution_id"]
                school_ids.add(iid)
                expected_people.setdefault(iid, set()).add(member["member_id"])
                expected_terms.setdefault(iid, set()).add(int(p))
        mapped_ids = school_ids & mapped.keys()
        if len(school_ids) != summary.get("number_of_represented_schools"):
            errors.append(
                f"Parliament {p}: represented school count does not reconcile"
            )
        if len(mapped_ids) != summary.get("mapped_schools_count") or len(
            school_ids - mapped.keys()
        ) != summary.get("unmapped_schools_count"):
            errors.append(f"Parliament {p}: mapped/unmapped counts do not reconcile")
        layer = read(f"web/parliament_{p}_combined.geojson")["features"]
        layer_ids = [f["properties"]["institution_id"] for f in layer]
        if len(layer_ids) != len(set(layer_ids)) or set(layer_ids) != mapped_ids:
            errors.append(f"Parliament {p}: map layer does not match its cohort")
        for feature in layer:
            iid = feature["properties"]["institution_id"]
            if iid in mapped and feature["geometry"] != mapped[iid]["geometry"]:
                errors.append(
                    f"Parliament {p}: school coordinates disagree across layers"
                )
        counts[p] = {
            "people": len(people),
            "represented_schools": len(school_ids),
            "mapped_schools": len(mapped_ids),
            "unmapped_schools": len(school_ids - mapped.keys()),
        }
    for iid, feature in mapped.items():
        props = feature["properties"]
        people = props["members"]
        ids = [m["member_id"] for m in people]
        if (
            len(ids) != len(set(ids))
            or set(ids) != expected_people.get(iid, set())
            or props["member_count"] != len(ids)
        ):
            errors.append(f"School {iid}: distinct member relationship counts disagree")
        if set(props["parliaments"]) != expected_terms.get(iid, set()):
            errors.append(f"School {iid}: parliament arrays disagree")
        for member in people:
            service_terms: set[int] = set()
            for service in member["services"]:
                p = str(service["parliament_number"])
                service_terms.add(int(p))
                row = cohort_rows.get((p, member["member_id"]))
                if row is None or any(
                    service[key] != row[key]
                    for key in ("party", "party_abbrev", "chamber")
                ):
                    errors.append(
                        f"School {iid}: member {member['member_id']} service context disagrees in parliament {p}"
                    )
                elif iid not in {s["institution_id"] for s in row["schools"]}:
                    errors.append(
                        f"School {iid}: attendance relationship missing from member list"
                    )
            if service_terms != set(member["parliaments"]):
                errors.append(
                    f"School {iid}: member service/parliament arrays disagree"
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
