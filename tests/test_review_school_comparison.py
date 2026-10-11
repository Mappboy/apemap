from apemap.review.schools import school_comparison


def by_field(rows: list[dict[str, str]], field: str) -> dict[str, str]:
    return next(row for row in rows if row["field"] == field)


def test_school_comparison_uses_saved_mapping_and_normalized_name() -> None:
    school = {
        "name": "St Clare's College (ACT)",
        "saved_reference": "acara:1",
        "targets": [
            {
                "reference": "acara:1",
                "metadata": {
                    "school_name": "St Clare's College",
                    "suburb": "Griffith",
                    "state": "ACT",
                },
            }
        ],
    }

    rows = school_comparison(
        school,
        {"school_name": "Saint Clare's College", "suburb": "Griffith", "state": "ACT"},
    )

    name = by_field(rows, "name")
    assert name["comparison"] == "matches"
    assert name["source"] == "St Clare's College (ACT)"
    assert name["reviewed"] == "St Clare's College"
    assert by_field(rows, "locality")["comparison"] == "matches"
    assert by_field(rows, "ACARA identifier")["comparison"] == "unknown"


def test_school_comparison_does_not_infer_current_from_candidates_or_original() -> None:
    school = {
        "name": "Old College",
        "saved_reference": "",
        "targets": [
            {"reference": "acara:2", "metadata": {"school_name": "Old College"}}
        ],
        "original_metadata": {"school_name": "Old College"},
    }

    rows = school_comparison(school, {"school_name": "New College"})

    name = by_field(rows, "name")
    assert name["comparison"] == "different"
    assert name["source"] == "Old College"
    assert name["reviewed"] == ""
    assert name["proposed"] == "New College"


def test_school_comparison_marks_material_conflicts_and_missing_metadata() -> None:
    school = {
        "name": "Example School",
        "saved_reference": "manual:example",
        "targets": [
            {
                "reference": "manual:example",
                "metadata": {"school_name": "Example School", "sector": "Government"},
            }
        ],
    }

    rows = school_comparison(
        school, {"school_name": "Different School", "sector": "Catholic"}
    )

    assert by_field(rows, "name")["comparison"] == "different"
    assert by_field(rows, "sector")["comparison"] == "different"
    assert by_field(rows, "locality")["comparison"] == "unknown"


def test_manual_reference_is_not_reported_as_acara_identifier() -> None:
    rows = school_comparison(
        {
            "name": "Example School",
            "saved_reference": "manual:example",
            "targets": [
                {
                    "reference": "manual:example",
                    "metadata": {"school_name": "Example School"},
                }
            ],
        },
        {"institution_ref": "manual:other", "school_name": "Other School"},
    )

    identifier = by_field(rows, "ACARA identifier")
    assert identifier["reviewed"] == ""
    assert identifier["proposed"] == ""
    assert identifier["comparison"] == "unknown"


def test_comparison_only_displays_complete_recorded_coordinates() -> None:
    rows = school_comparison(
        {
            "name": "Example School",
            "source_records": [{"latitude": 0, "longitude": 147}],
        },
        {"latitude": 0, "longitude": 147},
    )

    coordinates = by_field(rows, "coordinates")
    assert coordinates["source"] == "0, 147"
    assert coordinates["proposed"] == "0, 147"
    assert coordinates["comparison"] == "unknown"

    partial = school_comparison(
        {"name": "Example School", "source_records": [{"latitude": 0}]},
        {"latitude": 0},
    )
    assert by_field(partial, "coordinates")["source"] == ""
