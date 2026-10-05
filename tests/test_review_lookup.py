"""Read-only ACARA school-name search over small local registers."""

from pathlib import Path

import pytest

from apemap.ingest.matching import SchoolMatcher
from apemap.review.service import ReviewService

pytestmark = pytest.mark.unit


@pytest.fixture
def lookup_service(tmp_path: Path) -> ReviewService:
    external = tmp_path / "external"
    external.mkdir()
    (external / "school-location-2025.csv").write_text(
        "ACARA SML ID,School Name,School Sector,School Type,State,Suburb\n"
        "103,Fairfield School,Government,Secondary,TAS,Hobart\n"
        "101,Fairfield School,Independent,Secondary,VIC,Fairfield\n"
        "102,Fairfield Secondary School,Government,Secondary,NSW,Sydney\n"
        "104,Saint Mary's College,Catholic,Combined,TAS,Hobart\n"
        "105,North Fairfield School,Government,Secondary,QLD,Brisbane\n"
        "106,Fairfield School East,Government,Primary,VIC,Melbourne\n"
        "107,Café Hill College,Independent,Secondary,SA,Adelaide\n"
        "108,Remote School,Government,,,\n"
        "bad,Bad School,Government,Primary,TAS,Hobart\n"
        ",Missing ID School,Government,Primary,TAS,Hobart\n"
        "109,,Government,Primary,TAS,Hobart\n"
        " 110 ,Whitespace School,Government,Primary,TAS,Hobart\n",
        encoding="utf-8",
    )
    (external / "school-profile-2008-2025.csv").write_text(
        "ACARA SML ID,School Name,School Sector,School Type,State,Suburb,Calendar Year\n"
        "101,Earlier School,Independent,Secondary,VIC,Fairfield,2008\n"
        "103,Earlier School,Government,Secondary,TAS,Hobart,2008\n"
        "101,Fairfield School,Independent,Secondary,VIC,Fairfield,2025\n"
        "300,Old Fairfield High,Government,Secondary,TAS,Launceston,2008\n",
        encoding="utf-8",
    )
    return ReviewService(
        log_path=tmp_path / "decisions.jsonl",
        db_path=tmp_path / "review.duckdb",
        external_dir=external,
    )


def test_lookup_keeps_same_name_ids_and_ranks_matches(
    lookup_service: ReviewService,
) -> None:
    results = lookup_service.lookup_institutions("  FAIRFIELD   school ")
    assert [row["acara_id"] for row in results] == ["103", "101", "106", "105", "102"]
    assert results[0] == {
        "institution_ref": "acara:103",
        "acara_id": "103",
        "school_name": "Fairfield School",
        "state": "TAS",
        "suburb": "Hobart",
        "sector": "Government",
        "school_type": "Secondary",
        "institution_status": "current",
    }
    assert results[1]["state"] == "VIC"
    assert (
        lookup_service.lookup_institutions("fairfield school", limit=2) == results[:2]
    )


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("ST. MARY'S", "104"),
        ("cafe hill", "107"),
        ("school secondary fairfield", "102"),
    ],
)
def test_lookup_normalizes_school_names(
    lookup_service: ReviewService, query: str, expected: str
) -> None:
    assert lookup_service.lookup_institutions(query)[0]["acara_id"] == expected


def test_lookup_exposes_historical_snapshot_status(
    lookup_service: ReviewService,
) -> None:
    result = lookup_service.lookup_institutions("old fairfield")[0]
    assert result["institution_ref"] == "acara:300"
    assert result["institution_status"] == "historical_only"
    # An older snapshot cannot establish present-day operation.
    (lookup_service.external_dir / "school-location-2025.csv").rename(
        lookup_service.external_dir / "school-location-2022.csv"
    )
    assert (
        lookup_service.lookup_institutions("fairfield school")[0]["institution_status"]
        == "unknown"
    )


def test_lookup_finds_annual_names_without_duplicate_ids_or_losing_ambiguity(
    lookup_service: ReviewService,
) -> None:
    results = lookup_service.lookup_institutions("earlier")
    assert [row["institution_ref"] for row in results] == ["acara:103", "acara:101"]
    assert all(row["school_name"] == "Fairfield School" for row in results)
    assert all(row["institution_status"] == "current" for row in results)
    results = lookup_service.lookup_institutions("school")
    assert len({row["acara_id"] for row in results}) == len(results)


def test_lookup_cleans_missing_metadata_and_ignores_invalid_ids(
    lookup_service: ReviewService,
) -> None:
    result = lookup_service.lookup_institutions("remote")[0]
    assert result["state"] is None
    assert result["suburb"] is None
    assert result["school_type"] is None
    assert lookup_service.lookup_institutions("bad school") == []
    assert lookup_service.lookup_institutions("missing id") == []
    whitespace = lookup_service.lookup_institutions("whitespace")[0]
    assert whitespace["institution_ref"] == "acara:110"
    assert whitespace["institution_status"] == "current"


@pytest.mark.parametrize("query", ["", "  ", "a", "...", "no such school"])
def test_lookup_empty_short_and_unmatched_queries(
    lookup_service: ReviewService, query: str
) -> None:
    assert lookup_service.lookup_institutions(query) == []


@pytest.mark.parametrize("limit", [-1, 0, 51])
def test_lookup_rejects_unbounded_limits(
    lookup_service: ReviewService, limit: int
) -> None:
    with pytest.raises(ValueError, match="between 1 and 50"):
        lookup_service.lookup_institutions("school", limit=limit)


def test_lookup_rejects_long_queries(lookup_service: ReviewService) -> None:
    with pytest.raises(ValueError, match="at most 200"):
        lookup_service.lookup_institutions("a" * 201)


def test_lookup_is_read_only_and_does_not_promote_aliases(
    lookup_service: ReviewService,
) -> None:
    alias_path = lookup_service.external_dir / "school_aliases.json"
    alias_path.write_text(
        '{"aliases":{"Imaginary School":{"canonical_acara_id":"103"}}}',
        encoding="utf-8",
    )
    before = {path: path.read_bytes() for path in lookup_service.external_dir.iterdir()}
    assert lookup_service.lookup_institutions("imaginary") == []
    assert lookup_service.lookup_institutions("fairfield")
    assert not lookup_service.log_path.exists()
    assert not lookup_service.db_path.exists()
    assert {path: path.read_bytes() for path in before} == before


def test_lookup_reuses_register_and_reloads_changed_sources(
    lookup_service: ReviewService, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0
    original_matcher = lookup_service.matcher

    def counted_matcher() -> SchoolMatcher:
        nonlocal calls
        calls += 1
        return original_matcher()

    monkeypatch.setattr(lookup_service, "matcher", counted_matcher)
    result = lookup_service.lookup_institutions("fairfield")
    result[0]["school_name"] = "Client mutation"
    assert (
        lookup_service.lookup_institutions("fairfield")[0]["school_name"]
        != "Client mutation"
    )
    assert calls == 1
    with (lookup_service.external_dir / "school-location-2025.csv").open(
        "a", encoding="utf-8"
    ) as stream:
        stream.write("400,Newly Added School,Government,Secondary,TAS,Hobart\n")
    assert (
        lookup_service.lookup_institutions("newly")[0]["institution_ref"] == "acara:400"
    )
    assert calls == 2


def test_lookup_without_local_register_returns_no_results(tmp_path: Path) -> None:
    service = ReviewService(external_dir=tmp_path)
    assert service.lookup_institutions("school") == []
