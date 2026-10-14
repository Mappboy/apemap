"""Deterministic, evidence-linked manual institution proposals and unknown sectors."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb
import pytest

from apemap.db import export_to_parquet
from apemap.review.evidence import EvidenceRecord
from apemap.review.integration import _write_records, apply_review_events
from apemap.review.manual_institutions import (
    build_manual_institution_candidate,
    validate_institution_fact_references,
)
from apemap.review.model import school_review_id, validate_event
from tests.test_review_integration import event, seeded_review as seeded_review

if TYPE_CHECKING:
    from duckdb import DuckDBPyConnection

    from apemap.ingest.matching import SchoolMatcher


def fact(field: str, value: Any, **changes: Any) -> EvidenceRecord:
    return EvidenceRecord(
        review_id=changes.pop("review_id", school_review_id("Historical School")),
        candidate_institution_ref=None,
        source_title="School history",
        source_type="institution",
        source_url=changes.pop("source_url", "https://example.org/school/history"),
        retrieved_at="2026-10-01T00:00:00+00:00",
        claim_type=changes.pop("claim_type", "institution_fact"),
        claim_value=changes.pop("claim_value", {"field": field, "value": value}),
        stance=changes.pop("stance", "supports"),
        excerpt_or_note="Cited institution fact.",
        generated_by="agent-search",
        **changes,
    )


def candidate(**optional: Any) -> tuple[dict[str, Any], list[EvidenceRecord]]:
    fields = {"name": "Historical School", "country": "Australia", **optional}
    evidence = [fact(field, value) for field, value in fields.items()]
    return {
        **fields,
        "evidence_refs": {
            record.claim_value["field"]: [record.evidence_id] for record in evidence
        },
    }, evidence


def test_minimum_supported_facts_propose_unknown_sector_without_authority() -> None:
    facts, evidence = candidate()
    original = deepcopy(facts)
    built = build_manual_institution_candidate(facts, evidence, [])
    assert built["state"] == "create"
    assert built["institution_ref"] == "manual:historical-school"
    definition = built["definition"]
    assert definition["sector"] is None
    assert definition["institution_status"] == "unknown"
    assert definition["institution_fact_evidence_refs"] == facts["evidence_refs"]
    assert definition["institution_evidence_review_id"] == evidence[0].review_id
    assert facts == original
    validate_event(event("manual_institution", definition))
    assert validate_institution_fact_references(definition, evidence) == {
        record.evidence_id for record in evidence
    }


@pytest.mark.parametrize("missing", ["name", "country"])
def test_missing_required_support_keeps_research_packet(missing: str) -> None:
    facts, evidence = candidate()
    facts["evidence_refs"].pop(missing)
    built = build_manual_institution_candidate(facts, evidence, [])
    assert built["state"] == "needs_research"
    assert built["definition"] is built["institution_ref"] is None
    assert any(missing in problem for problem in built["problems"])


def test_unsupported_optional_values_are_not_asserted() -> None:
    facts, evidence = candidate()
    facts.update(sector="Independent", status="closed", state="TAS")
    built = build_manual_institution_candidate(facts, evidence, [])
    assert built["definition"]["sector"] is None
    assert built["definition"]["institution_status"] == "unknown"
    assert "state" not in built["definition"]
    assert "sector" not in built["evidence_refs"]


def test_nullable_reuse_reference_does_not_request_existing_identity_reuse() -> None:
    facts, evidence = candidate()
    facts["reuse_ref"] = None
    assert build_manual_institution_candidate(facts, evidence, [])["state"] == "create"


def test_supported_closed_status_and_sector_remain_independent_facts() -> None:
    facts, evidence = candidate(status="closed", sector="Government", suburb="Hobart")
    built = build_manual_institution_candidate(facts, evidence, [])
    assert built["definition"]["institution_status"] == "closed"
    assert built["definition"]["sector"] == "Government"
    assert built["definition"]["suburb"] == "Hobart"
    assert not built["definition"].get("acara_id")


def test_contradictory_field_evidence_blocks_creation() -> None:
    facts, evidence = candidate()
    contrary = fact("country", "Australia", stance="contradicts")
    evidence.append(contrary)
    built = build_manual_institution_candidate(facts, evidence, [])
    assert built["state"] == "needs_research"
    assert any("country" in problem for problem in built["problems"])


def test_unlinked_conflicting_support_in_the_same_case_blocks_creation() -> None:
    facts, evidence = candidate()
    disputed = fact("country", "Canada")
    built = build_manual_institution_candidate(facts, [*evidence, disputed], [])
    assert built["state"] == "needs_research"
    assert any("country" in problem for problem in built["problems"])


@pytest.mark.parametrize("stance", ["supports", "contradicts"])
@pytest.mark.parametrize("different", ["case", "candidate"])
def test_other_case_or_known_candidate_facts_do_not_contaminate_identity(
    stance: str, different: str
) -> None:
    facts, evidence = candidate()
    unrelated = fact(
        "country",
        "Canada",
        stance=stance,
        review_id=school_review_id("Other School")
        if different == "case"
        else evidence[0].review_id,
    )
    if different == "candidate":
        unrelated = EvidenceRecord.from_dict(
            {
                **unrelated.to_dict(),
                "candidate_institution_ref": "acara:123",
                "evidence_id": "",
            }
        )
    built = build_manual_institution_candidate(facts, [*evidence, unrelated], [])
    assert built["state"] == "create"


def test_member_attributed_facts_cannot_define_a_manual_institution() -> None:
    facts, evidence = candidate()
    attributed = fact(
        "country",
        None,
        claim_value={"field": "country", "value": "Australia", "aph_id": "UNSUPPLIED"},
    )
    facts["evidence_refs"]["country"] = [attributed.evidence_id]
    built = build_manual_institution_candidate(facts, [evidence[0], attributed], [])
    assert built["state"] == "needs_research"
    facts, evidence = candidate()
    definition = build_manual_institution_candidate(facts, evidence, [])["definition"]
    definition["institution_fact_evidence_refs"]["country"] = [attributed.evidence_id]
    with pytest.raises(ValueError, match="country"):
        validate_institution_fact_references(definition, [evidence[0], attributed])


def test_location_coordinates_require_explicit_paired_location_claim() -> None:
    facts, evidence = candidate(latitude=-42.0, longitude=147.0)
    unsupported = build_manual_institution_candidate(facts, evidence, [])
    assert "latitude" not in unsupported["definition"]
    location = fact(
        "location",
        None,
        claim_type="location",
        claim_value={"latitude": -42.0, "longitude": 147.0},
    )
    facts["evidence_refs"]["latitude"] = [location.evidence_id]
    facts["evidence_refs"]["longitude"] = [location.evidence_id]
    built = build_manual_institution_candidate(facts, [*evidence, location], [])
    assert built["definition"]["latitude"] == -42.0
    assert built["definition"]["location_source_url"] == location.source_url


def test_conflicting_location_pairs_do_not_construct_an_unsourced_point() -> None:
    facts, evidence = candidate(latitude=-42.0, longitude=147.0)
    latitude = fact(
        "location",
        None,
        claim_type="location",
        claim_value={"latitude": -42.0, "longitude": 146.0},
    )
    longitude = fact(
        "location",
        None,
        claim_type="location",
        claim_value={"latitude": -41.0, "longitude": 147.0},
    )
    facts["evidence_refs"].update(
        latitude=[latitude.evidence_id], longitude=[longitude.evidence_id]
    )
    built = build_manual_institution_candidate(
        facts, [*evidence, latitude, longitude], []
    )
    assert built["state"] == "needs_research"


def test_coordinate_limits_require_further_research() -> None:
    facts, evidence = candidate(latitude=-100, longitude=147)
    location = fact(
        "location",
        None,
        claim_type="location",
        claim_value={"latitude": -100, "longitude": 147},
    )
    facts["evidence_refs"].update(
        latitude=[location.evidence_id], longitude=[location.evidence_id]
    )
    assert (
        build_manual_institution_candidate(facts, [*evidence, location], [])["state"]
        == "needs_research"
    )


def test_exact_identity_evidence_reuses_existing_definition() -> None:
    facts, evidence = candidate(reuse_ref="manual:existing-school")
    existing = {
        "institution_ref": "manual:existing-school",
        "school_name": "Historical School",
        "country": "Australia",
        "sector": "Other",
    }
    before = deepcopy(existing)
    built = build_manual_institution_candidate(facts, evidence, [existing])
    assert built["state"] == "reuse"
    assert built["institution_ref"] == existing["institution_ref"]
    assert built["definition"] is None
    assert existing == before
    facts["evidence_refs"].pop("reuse_ref")
    assert (
        build_manual_institution_candidate(facts, evidence, [existing])["state"]
        == "needs_research"
    )


def test_reuse_cannot_silently_replace_established_sector() -> None:
    facts, evidence = candidate(
        reuse_ref="manual:existing-school", sector="Independent"
    )
    existing = {
        "institution_ref": "manual:existing-school",
        "school_name": "Historical School",
        "country": "Australia",
        "sector": "Government",
    }
    assert (
        build_manual_institution_candidate(facts, evidence, [existing])["state"]
        == "needs_research"
    )


def test_name_and_country_do_not_automatically_merge_an_existing_identity() -> None:
    facts, evidence = candidate()
    existing = {
        "institution_ref": "manual:historical-school",
        "school_name": "Historical School",
        "country": "Australia",
        "suburb": "Hobart",
    }
    built = build_manual_institution_candidate(facts, evidence, [existing])
    assert built["state"] == "needs_research"


def test_actual_slug_collision_has_stable_fact_digest() -> None:
    facts, evidence = candidate(country="Canada")
    existing = {
        "institution_ref": "manual:historical-school",
        "school_name": "Historical School",
        "country": "Australia",
    }
    first = build_manual_institution_candidate(facts, evidence, [existing])
    assert first["state"] == "create"
    assert first["institution_ref"].startswith("manual:historical-school-")
    changed_evidence = [
        fact(
            record.claim_value["field"],
            record.claim_value["value"],
            review_id=school_review_id("Historical School alias"),
            source_url="https://example.org/a-different-source",
        )
        for record in evidence
    ]
    changed_facts = deepcopy(facts)
    changed_facts["evidence_refs"] = {
        record.claim_value["field"]: [record.evidence_id] for record in changed_evidence
    }
    second = build_manual_institution_candidate(
        changed_facts, changed_evidence, [existing]
    )
    assert first["institution_ref"] == second["institution_ref"]


def test_scoped_fact_links_reject_changed_values_other_cases_and_missing_refs() -> None:
    facts, evidence = candidate()
    definition = build_manual_institution_candidate(facts, evidence, [])["definition"]
    changed = {**definition, "country": "Canada"}
    with pytest.raises(ValueError, match="country"):
        validate_institution_fact_references(changed, evidence)
    other = fact("country", "Australia", review_id=school_review_id("Other School"))
    changed = deepcopy(definition)
    changed["institution_fact_evidence_refs"]["country"] = [other.evidence_id]
    with pytest.raises(ValueError, match="case"):
        validate_institution_fact_references(changed, [*evidence, other])
    with pytest.raises(ValueError, match="Unknown"):
        validate_institution_fact_references(definition, evidence[:1])
    with pytest.raises(ValueError, match="sector"):
        validate_institution_fact_references(
            {**definition, "sector": "Independent"}, evidence
        )


def test_successor_candidate_facts_do_not_define_the_original_manual_school() -> None:
    facts, evidence = candidate()
    successor = EvidenceRecord.from_dict(
        {
            **evidence[1].to_dict(),
            "candidate_institution_ref": "acara:123",
            "evidence_id": "",
        }
    )
    facts["evidence_refs"]["country"] = [successor.evidence_id]
    built = build_manual_institution_candidate(facts, [evidence[0], successor], [])
    assert built["state"] == "needs_research"
    facts, evidence = candidate()
    definition = build_manual_institution_candidate(facts, evidence, [])["definition"]
    definition["institution_fact_evidence_refs"]["country"] = [successor.evidence_id]
    with pytest.raises(ValueError, match="candidate"):
        validate_institution_fact_references(definition, [evidence[0], successor])


@pytest.mark.integration
def test_unknown_sector_replays_as_null_and_legacy_other_stays_other(
    seeded_review: tuple[DuckDBPyConnection, SchoolMatcher],
    tmp_path: Path,
) -> None:
    conn, matcher = seeded_review
    unknown = event(
        "manual_institution",
        {
            "institution_ref": "manual:unknown-sector",
            "school_name": "Unknown Sector School",
            "country": "Canada",
            "sector": None,
        },
        "unknown-sector",
    )
    legacy = event(
        "manual_institution",
        {
            "institution_ref": "manual:legacy-other",
            "school_name": "Legacy Other School",
            "country": "Canada",
            "sector": "Other",
        },
        "legacy-other",
    )
    mapping = event(
        "school",
        {
            "recorded_name": "First High School",
            "institution_ref": "manual:unknown-sector",
            "relationship_type": "direct",
        },
        "school-mapping",
    )
    conn.execute("BEGIN")
    apply_review_events(conn, events=[unknown, legacy, mapping], matcher=matcher)
    conn.execute("COMMIT")
    assert conn.execute(
        "SELECT institution_id, sector FROM institutions WHERE acara_id IS NULL ORDER BY institution_id"
    ).fetchall() == [("manual:legacy-other", "Other"), ("manual:unknown-sector", None)]
    assert conn.execute(
        "SELECT broad_sector, detailed_sector, sector_basis FROM v_education_attendance_context"
    ).fetchall() == [(None, None, "unresolved")]
    exported = export_to_parquet(conn, tmp_path / "parquet")
    assert conn.execute(
        "SELECT sector FROM read_parquet(?) WHERE institution_id='manual:unknown-sector'",
        [str(exported["institutions"])],
    ).fetchone() == (None,)


def test_legacy_not_null_schema_requires_fresh_rebuild_before_mutation() -> None:
    with duckdb.connect() as conn:
        conn.execute(
            "CREATE TABLE institutions(institution_id VARCHAR PRIMARY KEY, sector VARCHAR NOT NULL)"
        )
        records = {
            "institutions": [
                {"institution_id": "manual:unknown-sector", "sector": None}
            ]
        }
        with pytest.raises(ValueError, match="fresh.*pinned-source"):
            _write_records(conn, records)
        assert conn.execute("SELECT count(*) FROM institutions").fetchone() == (0,)
