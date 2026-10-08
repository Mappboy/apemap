"""Deterministic tamper detection for published successor sensitivity."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from apemap.analysis import (
    classify_attendance_person,
    compute_sector_summary,
    get_opening_day_education_context,
    get_opening_day_members,
)
from apemap.db import get_connection
from apemap.sensitivity_contract import (
    audit_successor_sensitivity,
    audit_sensitivity_publication,
)
from tests.test_analysis import successor_analysis_fixture


@pytest.fixture
def sensitivity_publication(
    tmp_path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    with get_connection(
        successor_analysis_fixture(tmp_path / "sensitivity.duckdb")
    ) as conn:
        rows = get_opening_day_education_context(conn, 47)
        people = get_opening_day_members(conn, 47)
        for person in people:
            person.update(
                classify_attendance_person(
                    [row for row in rows if row["member_id"] == person["member_id"]]
                )
            )
        report = compute_sector_summary(conn, 47)["successor_sensitivity"]
    return report, people, rows


def test_valid_sensitivity_deduplicates_service_copies(
    sensitivity_publication: tuple[
        dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]
    ],
) -> None:
    report, people, rows = sensitivity_publication
    assert audit_successor_sensitivity(report, people, rows) == []
    assert (
        audit_successor_sensitivity(
            report, list(reversed(people)), [*reversed(rows), rows[0]]
        )
        == []
    )
    assert audit_sensitivity_publication(report, deepcopy(report)) == []


@pytest.mark.parametrize(
    "path",
    [
        ("baseline", "total_parliamentarians"),
        ("without_successor_assumptions", "known_school_denominator"),
        ("without_successor_assumptions", "total_attendance_instances"),
        ("without_successor_assumptions", "total_unique_schools"),
        ("affected_members",),
        ("affected_assertions",),
        ("affected_schools",),
        (
            "without_successor_assumptions",
            "members_with_incomplete_broad_sector_evidence",
        ),
        (
            "without_successor_assumptions",
            "government_non_government",
            "government_only",
        ),
        (
            "difference_percentage_points",
            "government_non_government_percentages",
            "mixed",
        ),
        ("baseline", "school_identity_counts", "recorded_name_provisional"),
    ],
)
def test_sensitivity_rejects_changed_counts_flags_and_deltas(
    sensitivity_publication: tuple[
        dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]
    ],
    path: tuple[str, ...],
) -> None:
    report, people, rows = sensitivity_publication
    altered = deepcopy(report)
    target = altered
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] += 1
    errors = audit_successor_sensitivity(altered, people, rows)
    assert any(".".join(path) in error for error in errors)
    assert audit_sensitivity_publication(report, altered)


def test_sensitivity_rejects_dropped_people_or_assertions(
    sensitivity_publication: tuple[
        dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]
    ],
) -> None:
    report, people, rows = sensitivity_publication
    assert audit_successor_sensitivity(report, people[:-1], rows)
    assert audit_successor_sensitivity(report, people, rows[:-1])
    altered = deepcopy(rows[0])
    altered["broad_sector"] = "Non-government"
    assert any(
        "conflicting repeated context" in error
        for error in audit_successor_sensitivity(report, people, [*rows, altered])
    )


def test_sensitivity_checks_published_person_labels_and_completeness(
    sensitivity_publication: tuple[
        dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]
    ],
) -> None:
    report, people, rows = sensitivity_publication
    altered = deepcopy(people)
    person = next(person for person in altered if person["member_id"] == "m-both")
    person["incomplete_detailed_sector_evidence"] = False
    person["education_classification_label"] = "Independent only"
    errors = audit_successor_sensitivity(report, altered, rows)
    assert any("incomplete_detailed_sector_evidence" in error for error in errors)
    assert any("education_classification_label" in error for error in errors)


def test_sensitivity_rejects_missing_context_and_boolean_counts(
    sensitivity_publication: tuple[
        dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]
    ],
) -> None:
    report, people, rows = sensitivity_publication
    altered = deepcopy(rows)
    del altered[0]["sector_basis"]
    assert any(
        "missing fields sector_basis" in error
        for error in audit_successor_sensitivity(report, people, altered)
    )
    altered_report = deepcopy(report)
    altered_report["baseline"]["total_parliamentarians"] = True
    assert audit_successor_sensitivity(altered_report, people, rows)
