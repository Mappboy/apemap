"""Tests for member normalizers, semantic comparison, suppression, and DAG resolution."""

from __future__ import annotations

import pytest
from datetime import datetime
from dataclasses import replace

from apemap.review.model import ReviewEvent, member_review_id, validate_events
from apemap.review.member_comparison import (
    AMBIGUOUS,
    DIFFERENT,
    MATCHES,
    PROPOSED_INVALID,
    PROPOSED_MISSING,
    check_identity_ambiguity,
    compare_member_proposal,
    effective_member_value,
    is_member_candidate_suppressed,
    normalize_date_of_birth,
    normalize_gender,
    normalize_member_value,
    normalize_wikidata_id,
)


def test_normalize_gender_aliases() -> None:
    # Male aliases
    for val in (
        "Male",
        "male",
        "man",
        "MAN",
        "m",
        "M",
        "Q6581097",
        "q6581097",
        "http://www.wikidata.org/entity/Q6581097",
    ):
        assert normalize_gender(val) == "Male"

    # Female aliases
    for val in (
        "Female",
        "female",
        "woman",
        "WOMAN",
        "f",
        "F",
        "Q6581072",
        "q6581072",
        "https://www.wikidata.org/wiki/Q6581072",
    ):
        assert normalize_gender(val) == "Female"

    # Other aliases
    for val in ("Other", "other", "non-binary", "Non-Binary", "nonbinary", "NONBINARY"):
        assert normalize_gender(val) == "Other"

    # Empty / None
    assert normalize_gender(None) is None
    assert normalize_gender("") is None
    assert normalize_gender("   ") is None

    # Invalid
    with pytest.raises(ValueError, match="Invalid gender"):
        normalize_gender("unknown_value")
    with pytest.raises(ValueError, match="Invalid gender"):
        normalize_gender("robot")


def test_normalize_date_of_birth() -> None:
    # Valid calendar dates
    assert normalize_date_of_birth("1975-04-12") == "1975-04-12"
    assert normalize_date_of_birth(datetime(1975, 4, 12, 12, 30)) == "1975-04-12"
    assert normalize_date_of_birth("+1975-04-12") == "1975-04-12"

    # Valid ISO timestamps
    assert normalize_date_of_birth("1975-04-12T00:00:00Z") == "1975-04-12"
    assert normalize_date_of_birth("+1975-04-12T00:00:00+00:00") == "1975-04-12"
    assert normalize_date_of_birth("1980-11-23T14:30:00") == "1980-11-23"

    # Empty / None
    assert normalize_date_of_birth(None) is None
    assert normalize_date_of_birth("") is None

    # Partial dates rejected
    with pytest.raises(ValueError, match="Partial dates"):
        normalize_date_of_birth("1975")
    with pytest.raises(ValueError, match="Partial dates"):
        normalize_date_of_birth("1975-04")

    # Ambiguous numeric dates rejected
    with pytest.raises(ValueError, match="Ambiguous numeric dates"):
        normalize_date_of_birth("12/04/1975")
    with pytest.raises(ValueError, match="Ambiguous numeric dates"):
        normalize_date_of_birth("04/12/1975")

    # Malformed dates rejected
    with pytest.raises(ValueError, match="Malformed date of birth"):
        normalize_date_of_birth("not-a-date")
    with pytest.raises(ValueError, match="Invalid date"):
        normalize_date_of_birth("1975-02-31")


def test_normalize_wikidata_id() -> None:
    # Bare QIDs
    assert normalize_wikidata_id("Q12345") == "Q12345"
    assert normalize_wikidata_id("Q1") == "Q1"
    assert normalize_wikidata_id("Q987654321") == "Q987654321"

    # URLs
    assert normalize_wikidata_id("https://www.wikidata.org/wiki/Q12345") == "Q12345"
    assert normalize_wikidata_id("http://www.wikidata.org/entity/Q999") == "Q999"
    assert normalize_wikidata_id("https://wikidata.org/wiki/Q42/") == "Q42"

    # None / Empty
    assert normalize_wikidata_id(None) is None
    assert normalize_wikidata_id("") is None

    # Invalid identifiers
    with pytest.raises(ValueError, match="Invalid Wikidata identifier"):
        normalize_wikidata_id("P123")
    with pytest.raises(ValueError, match="Invalid Wikidata identifier"):
        normalize_wikidata_id("Q0")
    with pytest.raises(ValueError, match="Invalid Wikidata identifier"):
        normalize_wikidata_id("Qabc")
    with pytest.raises(ValueError, match="Invalid Wikidata identifier"):
        normalize_wikidata_id("12345")
    with pytest.raises(ValueError, match="Invalid Wikidata identifier"):
        normalize_wikidata_id("https://example.com/Q123")
    with pytest.raises(ValueError, match="Invalid Wikidata identifier"):
        normalize_wikidata_id("https://wikidata.org.example.com/entity/Q123")
    with pytest.raises(ValueError, match="Invalid gender"):
        normalize_gender("https://wikidata.org.example.com/entity/Q6581097")
    assert normalize_wikidata_id("q123") == "Q123"


def test_normalize_member_value_dispatcher() -> None:
    assert normalize_member_value("gender", "man") == "Male"
    assert normalize_member_value("date_of_birth", "1980-05-01") == "1980-05-01"
    assert (
        normalize_member_value("wikidata_id", "https://www.wikidata.org/wiki/Q100")
        == "Q100"
    )
    with pytest.raises(ValueError, match="Unsupported member field"):
        normalize_member_value("unknown_field", "value")


def test_compare_member_proposal() -> None:
    # Matches
    comp, n_eff, n_prop = compare_member_proposal("gender", "Male", "man")
    assert comp == MATCHES
    assert n_eff == "Male"
    assert n_prop == "Male"

    # Different
    comp, n_eff, n_prop = compare_member_proposal("gender", "Male", "Female")
    assert comp == DIFFERENT
    assert n_eff == "Male"
    assert n_prop == "Female"

    # Proposed missing
    comp, n_eff, n_prop = compare_member_proposal("gender", "Male", None)
    assert comp == PROPOSED_MISSING
    assert n_eff == "Male"
    assert n_prop is None

    # Proposed invalid
    comp, n_eff, n_prop = compare_member_proposal("gender", "Male", "invalid_value")
    assert comp == PROPOSED_INVALID
    assert n_eff == "Male"
    assert n_prop is None

    # Ambiguous flag overrides
    comp, n_eff, n_prop = compare_member_proposal(
        "gender", "Male", "Male", is_ambiguous=True
    )
    assert comp == AMBIGUOUS

    # Explicit null comparison: explicit null baseline vs proposed missing
    comp, n_eff, n_prop = compare_member_proposal(
        "gender", None, None, is_explicit_null=True
    )
    assert comp == PROPOSED_MISSING
    assert is_member_candidate_suppressed(comp, None, is_explicit_null=True) is True


def test_is_member_candidate_suppressed() -> None:
    # Matches are suppressed
    assert is_member_candidate_suppressed(MATCHES, "Male") is True

    # Proposed missing against populated baseline is suppressed
    assert is_member_candidate_suppressed(PROPOSED_MISSING, "Male") is True
    assert (
        is_member_candidate_suppressed(PROPOSED_MISSING, None, is_explicit_null=True)
        is True
    )

    # Proposed missing against unpopulated baseline (empty research task) is PRESERVED
    assert (
        is_member_candidate_suppressed(PROPOSED_MISSING, None, is_explicit_null=False)
        is False
    )

    # Discrepancies, invalid proposals, ambiguity are PRESERVED
    assert is_member_candidate_suppressed(DIFFERENT, "Male") is False
    assert is_member_candidate_suppressed(PROPOSED_INVALID, "Male") is False
    assert is_member_candidate_suppressed(AMBIGUOUS, "Male") is False


def test_check_identity_ambiguity() -> None:
    # Conflicting heads trigger ambiguity
    e1 = ReviewEvent(
        decision_id="d1",
        review_id=member_review_id("M1", "gender"),
        entity_type="member",
        action="accept",
        payload={"aph_id": "M1", "field": "gender", "value": "Male"},
        source_url="https://example.org",
        reviewer="R",
        notes="",
        reviewed_at="2026-01-01",
        recorded_at="2026-01-01T00:00:00Z",
    )
    e2 = ReviewEvent(
        decision_id="d2",
        review_id=member_review_id("M1", "gender"),
        entity_type="member",
        action="accept",
        payload={"aph_id": "M1", "field": "gender", "value": "Female"},
        source_url="https://example.org",
        reviewer="R",
        notes="",
        reviewed_at="2026-01-02",
        recorded_at="2026-01-02T00:00:00Z",
    )
    assert check_identity_ambiguity([], [e1, e2], "M1") is True

    # Cache status conflict triggers ambiguity
    cache_conflict = [("f1.json", "aph_id", {"status": "conflict"})]
    assert check_identity_ambiguity(cache_conflict, [], "M1") is True

    # Multiple candidates in cache triggers ambiguity
    cache_multi = [("f1.json", "aph_id", {"candidates": [{"id": 1}, {"id": 2}]})]
    assert check_identity_ambiguity(cache_multi, [], "M1") is True

    # Inter-cache QID conflict triggers ambiguity
    cache_qid_conflict = [
        ("f1.json", "aph_id", {"wikidata_id": "Q1"}),
        ("f2.json", "name", {"wikidata_id": "Q2"}),
    ]
    assert check_identity_ambiguity(cache_qid_conflict, [], "M1") is True

    # Clean caches and single head -> not ambiguous
    clean_caches = [
        ("f1.json", "aph_id", {"wikidata_id": "Q1", "gender": "Male"}),
        ("f2.json", "name", {"wikidata_id": "Q1", "gender": "Male"}),
    ]
    assert check_identity_ambiguity(clean_caches, [e1], "M1") is False


def test_effective_member_value_dag() -> None:
    # 1. No events -> source value
    eff, prov, is_null = effective_member_value([], "M1", "gender", "Male")
    assert eff == "Male"
    assert prov is None
    assert is_null is False

    # 2. Accepted event -> event value
    e_accept = ReviewEvent(
        decision_id="d1",
        review_id=member_review_id("M1", "gender"),
        entity_type="member",
        action="accept",
        payload={"aph_id": "M1", "field": "gender", "value": "Female"},
        source_url="https://example.org",
        reviewer="R",
        notes="",
        reviewed_at="2026-01-01",
        recorded_at="2026-01-01T00:00:00Z",
    )
    eff, prov, is_null = effective_member_value([e_accept], "M1", "gender", "Male")
    assert eff == "Female"
    assert prov == e_accept
    assert is_null is False

    # 3. Accepted explicit null
    e_null = ReviewEvent(
        decision_id="d2",
        review_id=member_review_id("M1", "gender"),
        entity_type="member",
        action="accept",
        payload={"aph_id": "M1", "field": "gender", "value": None},
        source_url="https://example.org",
        reviewer="R",
        notes="",
        reviewed_at="2026-01-01",
        recorded_at="2026-01-01T00:00:00Z",
    )
    eff, prov, is_null = effective_member_value([e_null], "M1", "gender", "Male")
    assert eff is None
    assert prov == e_null
    assert is_null is True

    # 4. Proposal-only reject referencing accepted ancestor
    e_rej_prop_only = ReviewEvent(
        decision_id="d3",
        review_id=member_review_id("M1", "gender"),
        entity_type="member",
        action="supersede",
        payload={
            "aph_id": "M1",
            "field": "gender",
            "value": None,
            "proposal_only": True,
            "retained_decision_id": "d1",
        },
        source_url="",
        reviewer="R",
        notes="Reject new proposal and retain d1",
        reviewed_at="2026-01-02",
        recorded_at="2026-01-02T00:00:00Z",
        supersedes=["d1"],
        replacement_action="reject",
    )
    events = [e_accept, e_rej_prop_only]
    eff, prov, is_null = effective_member_value(events, "M1", "gender", "Male")
    # Retains accepted ancestor value 'Female'
    assert eff == "Female"
    assert prov == e_accept
    assert is_null is False

    # 5. Proposal-only reject with retained_decision_id=None (source fallback)
    e_rej_fallback = ReviewEvent(
        decision_id="d4",
        review_id=member_review_id("M1", "gender"),
        entity_type="member",
        action="supersede",
        payload={
            "aph_id": "M1",
            "field": "gender",
            "value": None,
            "proposal_only": True,
            "retained_decision_id": None,
        },
        source_url="",
        reviewer="R",
        notes="Reject proposal and fallback to source",
        reviewed_at="2026-01-03",
        recorded_at="2026-01-03T00:00:00Z",
        supersedes=["d3"],
        replacement_action="reject",
    )
    events = [e_accept, e_rej_prop_only, e_rej_fallback]
    eff, prov, is_null = effective_member_value(events, "M1", "gender", "Male")
    # Falls back to source value 'Male'
    assert eff == "Male"
    assert prov is None
    assert is_null is False

    # 6. Historical withdrawal without proposal_only (legacy behavior: withdraws and falls back to source)
    e_legacy_withdrawal = ReviewEvent(
        decision_id="d5",
        review_id=member_review_id("M1", "gender"),
        entity_type="member",
        action="supersede",
        payload={"aph_id": "M1", "field": "gender", "value": None},
        source_url="",
        reviewer="R",
        notes="Historical withdrawal note",
        reviewed_at="2026-01-02",
        recorded_at="2026-01-02T00:00:00Z",
        supersedes=["d1"],
        replacement_action="reject",
    )
    events_legacy = [e_accept, e_legacy_withdrawal]
    eff, prov, is_null = effective_member_value(events_legacy, "M1", "gender", "Male")
    assert eff == "Male"
    assert prov is None
    assert is_null is False


def test_retained_qid_participates_in_effective_uniqueness() -> None:
    accepted = ReviewEvent.from_dict(
        {
            "decision_id": "accepted",
            "review_id": member_review_id("A", "wikidata_id"),
            "entity_type": "member",
            "action": "accept",
            "payload": {"aph_id": "A", "field": "wikidata_id", "value": "Q42"},
            "source_url": "https://example.org/bio",
            "reviewer": "R",
            "reviewed_at": "2026-01-01",
            "recorded_at": "2026-01-01T00:00:00Z",
        }
    )
    rejected = replace(
        accepted,
        decision_id="rejected",
        action="supersede",
        payload={
            "aph_id": "A",
            "field": "wikidata_id",
            "value": None,
            "proposal_only": True,
            "retained_decision_id": "accepted",
        },
        supersedes=["accepted"],
        replacement_action="reject",
        notes="Reject proposal",
    )
    other = replace(
        accepted,
        decision_id="other",
        review_id=member_review_id("B", "wikidata_id"),
        payload={"aph_id": "B", "field": "wikidata_id", "value": "Q42"},
    )
    with pytest.raises(ValueError, match="Conflicting member Wikidata ID"):
        validate_events([accepted, rejected, other])


def test_semantic_cache_agreement_and_explicit_ambiguity() -> None:
    caches = [
        (
            "a.json",
            "aph_id",
            {"gender": "Male", "date_of_birth": "1970-01-01", "wikidata_id": "Q42"},
        ),
        (
            "b.json",
            "name",
            {
                "gender": "male",
                "date_of_birth": "+1970-01-01T00:00:00Z",
                "wikidata_id": "https://www.wikidata.org/entity/Q42",
            },
        ),
    ]
    assert not check_identity_ambiguity(caches, [], "A")
    assert check_identity_ambiguity(
        [
            (
                "a.json",
                "aph_id",
                {"status": "ambiguous", "candidates": [{"wikidata_id": "Q42"}]},
            )
        ],
        [],
        "A",
    )
    assert not is_member_candidate_suppressed(PROPOSED_MISSING, "")
