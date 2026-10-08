"""Offline reconciliation of published successor sensitivity and attendance evidence."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from apemap.analysis import classify_attendance_person


DETAILED_KEYS = (
    "Government",
    "Catholic",
    "Independent",
    "Combined/Multiple",
    "Other",
    "No School Recorded",
)
HEADLINE_KEYS = (
    "government_only",
    "non_government_only",
    "mixed",
    "other",
    "no_school_recorded",
)
IDENTITY_KEYS = (
    "original_verified",
    "original_reference",
    "recorded_name_provisional",
    "unresolved",
)
ASSERTION_KEYS = (
    "education_id",
    "member_id",
    "attended_school_id",
    "identity_basis",
    "broad_sector",
    "detailed_sector",
    "sector_basis",
)


def _school_key(row: Mapping[str, Any]) -> tuple[str, str]:
    if row["attended_school_id"] is not None:
        return "school", row["attended_school_id"]
    return "unresolved_assertion", row["education_id"]


def _compare(path: str, actual: Any, expected: Any) -> list[str]:
    if isinstance(expected, Mapping):
        if not isinstance(actual, Mapping):
            return [f"{path}: missing or invalid mapping"]
        errors = []
        if (
            expected
            and all(
                isinstance(value, (int, float)) and not isinstance(value, bool)
                for value in expected.values()
            )
            and actual.keys() != expected.keys()
        ):
            errors.append(f"{path}: count categories do not match")
        return errors + [
            error
            for key, value in expected.items()
            for error in _compare(f"{path}.{key}", actual.get(key), value)
        ]
    if isinstance(expected, bool):
        if type(actual) is not bool or actual != expected:
            return [f"{path}: expected {expected!r}, found {actual!r}"]
    elif isinstance(expected, int):
        if type(actual) is not int or actual != expected:
            return [f"{path}: expected {expected!r}, found {actual!r}"]
    elif isinstance(actual, bool) and isinstance(expected, float) or actual != expected:
        return [f"{path}: expected {expected!r}, found {actual!r}"]
    return []


def _expected_counts(
    member_ids: set[str], rows: list[dict[str, Any]], *, strict: bool
) -> dict[str, Any]:
    """Reconcile the three published grains from assertion evidence, without a database."""
    by_member: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_school: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    attendance: Counter[str] = Counter()
    attendance_broad: Counter[str] = Counter()
    broad_keys = {"Government": "government", "Non-government": "non_government"}
    for row in rows:
        by_member[row["member_id"]].append(row)
        by_school[_school_key(row)].append(row)
        attendance[row["detailed_sector"] or "Other"] += 1
        broad = (
            None
            if strict and row["sector_basis"] == "successor_assumption"
            else row["broad_sector"]
        )
        attendance_broad[broad_keys.get(broad, "other")] += 1
    classified = [
        classify_attendance_person(
            by_member[member_id], exclude_successor_assumptions=strict
        )
        for member_id in sorted(member_ids)
    ]
    detailed_people = Counter(
        person["education_classification"] for person in classified
    )
    broad_people = Counter(person["government_non_government"] for person in classified)
    known = len(member_ids) - detailed_people["No School Recorded"]
    school_detail: Counter[str] = Counter()
    school_broad: Counter[str] = Counter()
    school_identities: Counter[str] = Counter()
    for school_rows in by_school.values():
        details = {
            row["detailed_sector"]
            for row in school_rows
            if row["detailed_sector"] is not None
        }
        broad = {
            row["broad_sector"]
            for row in school_rows
            if row["broad_sector"] is not None
            and not (strict and row["sector_basis"] == "successor_assumption")
        }
        implied = {
            "Government" if value == "Government" else "Non-government"
            for value in details
        }
        conflicting_broad = len(broad | implied) > 1
        detail_value = (
            next(iter(details))
            if len(details) == 1 and not conflicting_broad
            else "Other"
        )
        broad_value = (
            next(iter(broad)) if len(broad) == 1 and not conflicting_broad else None
        )
        school_detail[detail_value] += 1
        school_broad[broad_keys.get(broad_value, "other")] += 1
        basis = next(
            (
                key
                for key in IDENTITY_KEYS
                if any(row["identity_basis"] == key for row in school_rows)
            ),
            "unresolved",
        )
        school_identities[basis] += 1
    return {
        "total_parliamentarians": len(member_ids),
        "known_school_denominator": known,
        "unique_parliamentarians_by_sector": {
            key: detailed_people[key] for key in DETAILED_KEYS
        },
        "government_non_government": {key: broad_people[key] for key in HEADLINE_KEYS},
        "percentage_of_known_parliamentarians": {
            key: round(detailed_people[key] / known * 100, 2) if known else 0.0
            for key in DETAILED_KEYS
            if key != "No School Recorded"
        },
        "government_non_government_percentages": {
            key: round(broad_people[key] / known * 100, 2) if known else 0.0
            for key in HEADLINE_KEYS
            if key != "no_school_recorded"
        },
        "attendance_instances_by_sector": {
            key: attendance[key]
            for key in ("Government", "Catholic", "Independent", "Other")
        },
        "attendance_instances_government_non_government": {
            key: attendance_broad[key]
            for key in ("government", "non_government", "other")
        },
        "total_attendance_instances": len(rows),
        "unique_schools_by_sector": {
            key: school_detail[key]
            for key in ("Government", "Catholic", "Independent", "Other")
        },
        "unique_schools_government_non_government": {
            key: school_broad[key] for key in ("government", "non_government", "other")
        },
        "total_unique_schools": len(by_school),
        "school_identity_counts": {
            key: school_identities[key] for key in IDENTITY_KEYS
        },
        "broad_sector_evidence_denominator": sum(
            person["has_broad_sector_evidence"] for person in classified
        ),
        "detailed_sector_evidence_denominator": sum(
            person["has_detailed_sector_evidence"] for person in classified
        ),
        **{
            f"members_with_incomplete_{dimension}_evidence": sum(
                person[f"incomplete_{dimension}_evidence"] for person in classified
            )
            for dimension in ("sector", "broad_sector", "detailed_sector")
        },
    }


def audit_successor_sensitivity(
    report: Mapping[str, Any],
    people: Sequence[Mapping[str, Any]],
    assertions: Sequence[Mapping[str, Any]],
) -> list[str]:
    """Check exported sensitivity against distinct published people and assertions.

    Repeated service rows may repeat the same assertion; conflicting copies fail.
    Call with each party/chamber's people and only its assertions to audit that
    group's report as well.
    """
    errors: list[str] = []
    member_ids = {person["member_id"] for person in people}
    if len(member_ids) != len(people):
        errors.append("Sensitivity: published people contain duplicate member IDs")
    rows_by_id: dict[str, dict[str, Any]] = {}
    for assertion in assertions:
        missing = set(ASSERTION_KEYS) - assertion.keys()
        if missing:
            errors.append(
                f"Sensitivity assertion: missing fields {', '.join(sorted(missing))}"
            )
            continue
        row = {key: assertion[key] for key in ASSERTION_KEYS}
        if row["member_id"] not in member_ids:
            errors.append(
                f"Sensitivity assertion {row['education_id']}: person outside published cohort"
            )
            continue
        if row["broad_sector"] not in (None, "Government", "Non-government") or row[
            "detailed_sector"
        ] not in (None, "Government", "Catholic", "Independent"):
            errors.append(
                f"Sensitivity assertion {row['education_id']}: invalid sector"
            )
            continue
        if row["identity_basis"] not in IDENTITY_KEYS or row["sector_basis"] not in (
            "historical_verified",
            "successor_assumption",
            "original_reference",
            "unresolved",
        ):
            errors.append(
                f"Sensitivity assertion {row['education_id']}: invalid evidence basis"
            )
            continue
        previous = rows_by_id.get(row["education_id"])
        if previous is not None and previous != row:
            errors.append(
                f"Sensitivity assertion {row['education_id']}: conflicting repeated context"
            )
            continue
        rows_by_id[row["education_id"]] = row
    rows = list(rows_by_id.values())
    baseline = _expected_counts(member_ids, rows, strict=False)
    strict = _expected_counts(member_ids, rows, strict=True)
    assumed = [row for row in rows if row["sector_basis"] == "successor_assumption"]
    expected = {
        "affected_members": len({row["member_id"] for row in assumed}),
        "affected_assertions": len(assumed),
        "affected_schools": len({_school_key(row) for row in assumed}),
        "baseline": baseline,
        "without_successor_assumptions": strict,
        "difference_percentage_points": {
            field: {
                key: round(strict[field][key] - value, 2)
                for key, value in baseline[field].items()
            }
            for field in (
                "percentage_of_known_parliamentarians",
                "government_non_government_percentages",
            )
        },
    }
    errors.extend(_compare("Sensitivity", report, expected))
    by_member: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_member[row["member_id"]].append(row)
    for person in people:
        classification = classify_attendance_person(by_member[person["member_id"]])
        errors.extend(
            _compare(
                f"Sensitivity person {person['member_id']}", person, classification
            )
        )
    return errors


def audit_sensitivity_publication(
    expected: Mapping[str, Any],
    published: Mapping[str, Any],
    *,
    label: str = "Sensitivity publication",
) -> list[str]:
    """Reconcile an embedded cohort/group report with its standalone publication."""
    return _compare(label, published, expected)
