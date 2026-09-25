"""Tests for Wikipedia/Wikidata enrichment client, caching, and CLI commands."""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import duckdb
import pytest
import requests
from typer.testing import CliRunner

from apemap.cli import app
from apemap.db import get_connection, init_schema
from apemap.ingest.review import (
    MEMBER_REVIEW_GENERATED_COLUMNS,
    MEMBER_REVIEW_MANUAL_COLUMNS,
    SCHOOL_REVIEW_GENERATED_COLUMNS,
    SCHOOL_REVIEW_MANUAL_COLUMNS,
    evaluate_school_candidate,
    merge_review_rows,
)
from apemap.ingest.wikimedia import (
    WikimediaClient,
    normalize_date,
    normalize_gender,
    normalize_qid,
    run_wikimedia_enrichment,
)

runner = CliRunner()


@pytest.fixture
def mock_session() -> MagicMock:
    """Fixture providing a mock requests.Session."""
    session = MagicMock(spec=requests.Session)
    session.headers = {}
    return session


@pytest.fixture
def test_db(tmp_path: Path) -> tuple[Path, duckdb.DuckDBPyConnection]:
    """Provide a minimal DuckDB database for enrichment tests."""
    db_file = tmp_path / "enrich_test.duckdb"
    conn = get_connection(db_file)
    init_schema(conn)

    # Insert test members
    # Member 1: Has APH ID, no wikidata_id, DOB matches Wikidata
    # Member 2: Has APH ID, has existing wikidata_id
    # Member 3: Has APH ID, DOB conflicts with Wikidata
    # Member 4: Name fallback needed (no APH ID on Wikidata), unambiguous
    # Member 5: Name fallback ambiguous
    members_data = [
        (
            "mem-1",
            "Albanese",
            "Anthony",
            "Anthony Albanese",
            "Male",
            date(1963, 3, 2),
            "R36",
            None,
        ),
        (
            "mem-2",
            "Bandt",
            "Adam",
            "Adam Bandt",
            "Male",
            date(1972, 3, 11),
            "M3C",
            "Q4678667",
        ),
        (
            "mem-3",
            "Smith",
            "Jane",
            "Jane Smith",
            "Female",
            date(1975, 1, 1),
            "J99",
            None,
        ),
        (
            "mem-4",
            "Taylor",
            "UniqueMP",
            "UniqueMP Taylor",
            "Male",
            date(1980, 5, 20),
            "T44",
            None,
        ),
        ("mem-5", "Jones", "John", "John Jones", "Male", date(1970, 1, 1), "J00", None),
    ]
    for row in members_data:
        conn.execute(
            """
            INSERT INTO members (member_id, family_name, given_name, display_name, gender, date_of_birth, aph_id, wikidata_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            list(row),
        )

    # Insert parliament service for 47th parliament
    for i, m in enumerate(members_data, start=1):
        conn.execute(
            """
            INSERT INTO parliament_service (
                service_id, member_id, parliament_number, chamber, party, party_abbrev,
                electorate, state_or_territory, service_start, is_opening_day_member, is_current_member
            )
            VALUES (?, ?, 47, 'representatives', 'Labor', 'ALP', 'Grayndler', 'NSW', '2022-07-26', TRUE, TRUE)
            """,
            [f"srv-{i}", m[0]],
        )

    # Insert an unconfirmed institution
    conn.execute(
        """
        INSERT INTO institutions (
            institution_id, acara_id, school_name, school_type, sector, campus_type,
            state, suburb, postcode, longitude, latitude
        )
        VALUES ('inst-unmatched-1', NULL, 'Eton College, Windsor', 'Secondary', 'Other', 'School Single', NULL, NULL, NULL, NULL, NULL)
        """
    )
    conn.execute(
        """
        INSERT INTO member_education (
            education_id, member_id, institution_id, level, attended_status, source_url, retrieved_at, confidence, reviewer_notes
        )
        VALUES ('edu-1', 'mem-1', 'inst-unmatched-1', 'secondary', 'graduated', 'https://handbook.aph.gov.au/individual/R36', CURRENT_TIMESTAMP, 'unconfirmed', 'Unmatched international institution')
        """
    )

    conn.close()
    return db_file, get_connection(db_file)


# --------------------------------------------------------------------------
# Normalization Helper Tests
# --------------------------------------------------------------------------


def test_normalize_qid() -> None:
    assert normalize_qid("Q4772000") == "Q4772000"
    assert normalize_qid("http://www.wikidata.org/entity/Q4772000") == "Q4772000"
    assert normalize_qid("https://query.wikidata.org/Q123") == "Q123"
    assert normalize_qid("NoQIDHere") is None
    assert normalize_qid(None) is None


def test_normalize_date() -> None:
    assert normalize_date("1963-03-02") == "1963-03-02"
    assert normalize_date("1963-03-02T00:00:00Z") == "1963-03-02"
    assert normalize_date("+1972-03-11T00:00:00Z") == "1972-03-11"
    assert normalize_date("InvalidDate") is None
    assert normalize_date(None) is None


def test_normalize_gender() -> None:
    assert normalize_gender("Male") == "male"
    assert normalize_gender("female") == "female"
    assert normalize_gender("Woman") == "female"
    assert normalize_gender("Q6581097") == "male"
    assert normalize_gender("http://www.wikidata.org/entity/Q6581072") == "female"
    assert normalize_gender("http://www.wikidata.org/entity/Q6581097") == "male"
    assert normalize_gender(None) is None


# --------------------------------------------------------------------------
# WikimediaClient Unit Tests
# --------------------------------------------------------------------------


def test_client_initialization_defaults(tmp_path: Path) -> None:
    client = WikimediaClient(cache_dir=tmp_path)
    assert client.timeout == 45
    assert client.rate_delay == 0.5
    assert "APEMAP" in client.user_agent
    assert (tmp_path / "members").exists()
    assert (tmp_path / "institutions").exists()


def test_lookup_member_by_aph_id_timeout_error(
    tmp_path: Path, mock_session: MagicMock
) -> None:
    client = WikimediaClient(cache_dir=tmp_path, session=mock_session, rate_delay=0.0)
    mock_session.get.side_effect = requests.exceptions.ReadTimeout(
        "Read timed out (read timeout=45)"
    )

    res = client.lookup_member_by_aph_id("00AOU")
    assert res is not None
    assert res["status"] == "error"
    assert res["wikidata_id"] is None
    assert "Read timed out" in res["notes"]

    # Verify no error file was cached to disk so subsequent runs can retry
    cache_file = tmp_path / "members" / "00AOU.json"
    assert not cache_file.exists()


def test_lookup_member_by_aph_id_success(
    tmp_path: Path, mock_session: MagicMock
) -> None:
    client = WikimediaClient(cache_dir=tmp_path, session=mock_session, rate_delay=0.0)

    mock_resp = MagicMock()
    mock_resp.url = "https://query.wikidata.org/sparql?query=..."
    mock_resp.json.return_value = {
        "results": {
            "bindings": [
                {
                    "item": {"value": "http://www.wikidata.org/entity/Q4772000"},
                    "dob": {"value": "1963-03-02T00:00:00Z"},
                    "genderLabel": {"value": "male"},
                    "article": {
                        "value": "https://en.wikipedia.org/wiki/Anthony_Albanese"
                    },
                }
            ]
        }
    }
    mock_session.get.return_value = mock_resp

    res = client.lookup_member_by_aph_id("R36")
    assert res is not None
    assert res["status"] == "matched"
    assert res["wikidata_id"] == "Q4772000"
    assert res["date_of_birth"] == "1963-03-02"
    assert res["gender"] == "male"
    assert res["wikipedia_title"] == "Anthony Albanese"

    # Verify cache file was written
    cache_file = tmp_path / "members" / "R36.json"
    assert cache_file.exists()
    cached_content = json.loads(cache_file.read_text(encoding="utf-8"))
    assert cached_content["wikidata_id"] == "Q4772000"

    # Subsequent call with refresh=False must NOT call session.get again
    mock_session.get.reset_mock()
    res2 = client.lookup_member_by_aph_id("R36", refresh=False)
    assert res2 is not None
    assert res2["wikidata_id"] == "Q4772000"
    mock_session.get.assert_not_called()

    # Subsequent call with refresh=True MUST call session.get
    res3 = client.lookup_member_by_aph_id("R36", refresh=True)
    assert res3 is not None
    assert res3["wikidata_id"] == "Q4772000"
    mock_session.get.assert_called_once()


def test_lookup_member_by_aph_id_conflict(
    tmp_path: Path, mock_session: MagicMock
) -> None:
    client = WikimediaClient(cache_dir=tmp_path, session=mock_session, rate_delay=0.0)

    mock_resp = MagicMock()
    mock_resp.url = "https://query.wikidata.org/sparql?query=..."
    mock_resp.json.return_value = {
        "results": {
            "bindings": [
                {"item": {"value": "http://www.wikidata.org/entity/Q11111"}},
                {"item": {"value": "http://www.wikidata.org/entity/Q22222"}},
            ]
        }
    }
    mock_session.get.return_value = mock_resp

    res = client.lookup_member_by_aph_id("CONFLICT_APH")
    assert res is not None
    assert res["status"] == "conflict"
    assert res["wikidata_id"] is None


def test_lookup_member_by_name_fallback_disambiguation(
    tmp_path: Path, mock_session: MagicMock
) -> None:
    client = WikimediaClient(cache_dir=tmp_path, session=mock_session, rate_delay=0.0)

    mock_resp = MagicMock()
    mock_resp.url = "https://query.wikidata.org/sparql?query=..."
    # Two candidates with different birth dates
    mock_resp.json.return_value = {
        "results": {
            "bindings": [
                {
                    "item": {"value": "http://www.wikidata.org/entity/Q100"},
                    "dob": {"value": "1960-01-01T00:00:00Z"},
                    "genderLabel": {"value": "male"},
                },
                {
                    "item": {"value": "http://www.wikidata.org/entity/Q200"},
                    "dob": {"value": "1980-05-20T00:00:00Z"},
                    "genderLabel": {"value": "male"},
                    "article": {
                        "value": "https://en.wikipedia.org/wiki/UniqueMP_Taylor"
                    },
                },
            ]
        }
    }
    mock_session.get.return_value = mock_resp

    # Disambiguates by birth date 1980-05-20
    res = client.lookup_member_by_name("UniqueMP Taylor", dob="1980-05-20")
    assert res is not None
    assert res["status"] == "matched"
    assert res["wikidata_id"] == "Q200"

    # Without matching birth date -> ambiguous
    client.members_cache_dir.joinpath("name_uniquemp_taylor.json").unlink(
        missing_ok=True
    )
    res_ambig = client.lookup_member_by_name("UniqueMP Taylor", dob="1999-09-09")
    assert res_ambig is not None
    assert res_ambig["status"] == "ambiguous"
    assert res_ambig["wikidata_id"] is None


def test_lookup_institution_with_redirect_and_coords(
    tmp_path: Path, mock_session: MagicMock
) -> None:
    client = WikimediaClient(cache_dir=tmp_path, session=mock_session, rate_delay=0.0)

    wiki_resp = MagicMock()
    wiki_resp.url = "https://en.wikipedia.org/w/api.php?..."
    wiki_resp.json.return_value = {
        "query": {
            "pages": {
                "123": {
                    "pageid": 123,
                    "title": "Eton College",
                    "pageprops": {"wikibase_item": "Q207448"},
                    "coordinates": [{"lat": 51.492, "lon": -0.608}],
                }
            }
        }
    }

    sparql_resp = MagicMock()
    sparql_resp.url = "https://query.wikidata.org/sparql?..."
    sparql_resp.json.return_value = {
        "results": {
            "bindings": [
                {
                    "countryLabel": {"value": "United Kingdom"},
                    "adminLabel": {"value": "Windsor and Maidenhead"},
                    "typeLabel": {"value": "independent boarding school"},
                }
            ]
        }
    }

    def side_effect(url: str, **kwargs: Any) -> MagicMock:
        if "wikidata.org" in url:
            return sparql_resp
        return wiki_resp

    mock_session.get.side_effect = side_effect

    res = client.lookup_institution("Eton College, Windsor")
    assert res is not None
    assert res["confidence"] == "suggested"
    assert res["suggested_institution_name"] == "Eton College"
    assert res["wikidata_id"] == "Q207448"
    assert res["country"] == "United Kingdom"
    assert res["locality"] == "Windsor and Maidenhead"
    assert res["latitude"] == 51.492
    assert res["longitude"] == -0.608
    assert res["institution_type"] == "independent boarding school"


# --------------------------------------------------------------------------
# Pipeline and Review CSV Tests
# --------------------------------------------------------------------------


def test_run_wikimedia_enrichment_end_to_end(
    tmp_path: Path,
    test_db: tuple[Path, duckdb.DuckDBPyConnection],
    mock_session: MagicMock,
) -> None:
    db_file, _ = test_db
    cache_dir = tmp_path / "cache"
    output_dir = tmp_path / "processed"

    client = WikimediaClient(cache_dir=cache_dir, session=mock_session)

    def mock_get(
        url: str, params: dict[str, Any] | None = None, **kwargs: Any
    ) -> MagicMock:
        resp = MagicMock()
        resp.url = url
        params = params or {}

        if "wikidata.org" in url:
            query = params.get("query", "")
            if 'wdt:P10020 "R36"' in query:
                # Anthony Albanese: match, DOB matches APH
                resp.json.return_value = {
                    "results": {
                        "bindings": [
                            {
                                "item": {
                                    "value": "http://www.wikidata.org/entity/Q4772000"
                                },
                                "dob": {"value": "1963-03-02"},
                                "genderLabel": {"value": "male"},
                                "article": {
                                    "value": "https://en.wikipedia.org/wiki/Anthony_Albanese"
                                },
                            }
                        ]
                    }
                }
            elif 'wdt:P10020 "M3C"' in query:
                # Adam Bandt: already has Q4678667
                resp.json.return_value = {
                    "results": {
                        "bindings": [
                            {
                                "item": {
                                    "value": "http://www.wikidata.org/entity/Q4678667"
                                },
                                "dob": {"value": "1972-03-11"},
                                "genderLabel": {"value": "male"},
                            }
                        ]
                    }
                }
            elif 'wdt:P10020 "J99"' in query:
                # Jane Smith: DOB discrepancy (APH: 1975-01-01 vs Wiki: 1975-02-02)
                resp.json.return_value = {
                    "results": {
                        "bindings": [
                            {
                                "item": {
                                    "value": "http://www.wikidata.org/entity/Q99999"
                                },
                                "dob": {"value": "1975-02-02"},
                                "genderLabel": {"value": "female"},
                            }
                        ]
                    }
                }
            elif 'wdt:P10020 "T44"' in query or 'wdt:P10020 "J00"' in query:
                # No APH ID on Wikidata
                resp.json.return_value = {"results": {"bindings": []}}
            elif 'rdfs:label "UniqueMP Taylor"@en' in query:
                # Unambiguous name match
                resp.json.return_value = {
                    "results": {
                        "bindings": [
                            {
                                "item": {
                                    "value": "http://www.wikidata.org/entity/Q88888"
                                },
                                "dob": {"value": "1980-05-20"},
                                "genderLabel": {"value": "male"},
                            }
                        ]
                    }
                }
            elif 'rdfs:label "John Jones"@en' in query:
                # Ambiguous name match
                resp.json.return_value = {
                    "results": {
                        "bindings": [
                            {"item": {"value": "http://www.wikidata.org/entity/Q1"}},
                            {"item": {"value": "http://www.wikidata.org/entity/Q2"}},
                        ]
                    }
                }
            else:
                resp.json.return_value = {
                    "results": {
                        "bindings": [
                            {
                                "countryLabel": {"value": "United Kingdom"},
                                "typeLabel": {"value": "boarding school"},
                            }
                        ]
                    }
                }
        else:
            # Wikipedia API query
            resp.json.return_value = {
                "query": {
                    "pages": {
                        "99": {
                            "title": "Eton College",
                            "pageprops": {"wikibase_item": "Q207448"},
                            "coordinates": [{"lat": 51.492, "lon": -0.608}],
                        }
                    }
                }
            }
        return resp

    mock_session.get.side_effect = mock_get

    results = run_wikimedia_enrichment(
        parliaments=[47],
        refresh=False,
        enrich_members=True,
        enrich_schools=True,
        db_path=db_file,
        output_dir=output_dir,
        client=client,
    )

    assert results["members_processed"] == 5
    assert (
        results["members_enriched"] >= 2
    )  # Albanese (Q4772000), Jane Smith (Q99999), Taylor (Q88888)
    assert results["member_discrepancies"] >= 1  # Jane Smith DOB discrepancy
    assert results["schools_suggested"] >= 1

    # Check Database Updates
    conn = get_connection(db_file)
    try:
        mem1 = conn.execute(
            "SELECT wikidata_id, date_of_birth FROM members WHERE member_id = 'mem-1'"
        ).fetchone()
        assert mem1 is not None
        assert mem1[0] == "Q4772000"

        mem2 = conn.execute(
            "SELECT wikidata_id FROM members WHERE member_id = 'mem-2'"
        ).fetchone()
        assert mem2 is not None
        assert mem2[0] == "Q4678667"  # Existing QID was preserved

        mem3 = conn.execute(
            "SELECT wikidata_id, date_of_birth FROM members WHERE member_id = 'mem-3'"
        ).fetchone()
        assert mem3 is not None
        assert mem3[0] == "Q99999"
        # Crucial check: APH DOB was NOT modified by Wikidata discrepancy!
        assert str(mem3[1]) == "1975-01-01"

        mem5 = conn.execute(
            "SELECT wikidata_id FROM members WHERE member_id = 'mem-5'"
        ).fetchone()
        assert mem5 is not None
        assert mem5[0] is None  # Ambiguous match was NOT auto-linked

        # Canonical school remains unconfirmed in DB
        edu = conn.execute(
            "SELECT confidence FROM member_education WHERE education_id = 'edu-1'"
        ).fetchone()
        assert edu is not None
        assert edu[0] == "unconfirmed"
    finally:
        conn.close()

    # Check Review Artifacts
    member_csv = Path(results["member_review_csv"])
    assert member_csv.exists()
    with open(member_csv, encoding="utf-8") as f:
        reader = list(csv.DictReader(f))
        discrepancies = [r for r in reader if r["status"] == "discrepancy"]
        assert len(discrepancies) >= 1
        d = discrepancies[0]
        assert d["aph_id"] == "J99"
        assert d["field"] == "date_of_birth"
        assert d["aph_value"] == "1975-01-01"
        assert d["wikidata_value"] == "1975-02-02"

        ambig = [r for r in reader if r["status"] == "ambiguous"]
        assert len(ambig) >= 1
        assert ambig[0]["aph_id"] == "J00"

    school_csv = Path(results["school_review_csv"])
    assert school_csv.exists()
    with open(school_csv, encoding="utf-8") as f:
        s_reader = list(csv.DictReader(f))
        assert len(s_reader) >= 1
        assert s_reader[0]["wikidata_id"] == "Q207448"
        assert s_reader[0]["suggested_institution_name"] == "Eton College"
        assert s_reader[0]["confidence"] == "suggested"


# --------------------------------------------------------------------------
# CLI Integration Tests
# --------------------------------------------------------------------------


def test_cli_ingest_wikimedia_help() -> None:
    res = runner.invoke(app, ["ingest", "wikimedia", "--help"])
    assert res.exit_code == 0
    assert "Enrich canonical members and review unmatched schools" in res.output
    assert "--parliament" in res.output
    assert "--refresh" in res.output
    assert "--members" in res.output
    assert "--schools" in res.output
    assert "--timeout" in res.output


def test_cli_ingest_wikimedia_execution(
    tmp_path: Path, test_db: tuple[Path, duckdb.DuckDBPyConnection]
) -> None:
    db_file, _ = test_db
    out_dir = tmp_path / "cli_out"
    cache_dir = tmp_path / "cli_cache"

    # Pre-populate cache for mem-1 so CLI runs completely offline
    cache_dir.mkdir(parents=True, exist_ok=True)
    members_cache = cache_dir / "members"
    members_cache.mkdir(parents=True, exist_ok=True)
    (members_cache / "R36.json").write_text(
        json.dumps(
            {
                "aph_id": "R36",
                "wikidata_id": "Q4772000",
                "wikipedia_title": "Anthony Albanese",
                "date_of_birth": "1963-03-02",
                "gender": "male",
                "status": "matched",
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "ingest",
            "wikimedia",
            "--parliament",
            "47",
            "--db-path",
            str(db_file),
            "--output-dir",
            str(out_dir),
            "--cache-dir",
            str(cache_dir),
            "--no-schools",
        ],
    )
    assert result.exit_code == 0
    assert "Wikimedia Enrichment Complete!" in result.output
    assert "Members Processed" in result.output


def test_cli_run_all_includes_enrich_wikimedia_flag() -> None:
    res = runner.invoke(app, ["run-all", "--help"])
    assert res.exit_code == 0
    assert "--enrich-wikimedia" in res.output


# --------------------------------------------------------------------------
# Review Merging and Manual Column Persistence Tests
# --------------------------------------------------------------------------


def test_merge_review_rows_preserves_manual_decisions(tmp_path: Path) -> None:
    csv_file = tmp_path / "member_review.csv"

    # 1. Initial generation
    initial_generated = [
        {
            "member_id": "mem-1",
            "field": "wikidata_id",
            "aph_id": "R36",
            "display_name": "Anthony Albanese",
            "wikidata_id": "",
            "aph_value": "",
            "wikidata_value": "Q4772000",
            "wikipedia_title": "Anthony Albanese",
            "source_url": "https://query.wikidata.org",
            "status": "ambiguous",
            "notes": "Ambiguous initial",
            "historical_value": "",
            "historical_source": "",
            "review_status": "pending",
        },
        {
            "member_id": "mem-2",
            "field": "date_of_birth",
            "aph_id": "M3C",
            "display_name": "Adam Bandt",
            "wikidata_id": "Q4678667",
            "aph_value": "1972-03-11",
            "wikidata_value": "1972-03-12",
            "wikipedia_title": "Adam Bandt",
            "source_url": "https://query.wikidata.org",
            "status": "discrepancy",
            "notes": "DOB discrepancy",
            "historical_value": "1972-03-11",
            "historical_source": "APH Parliamentary Handbook",
            "review_status": "pending",
        },
    ]

    merge_review_rows(
        generated_rows=initial_generated,
        existing_path=csv_file,
        key_columns=("member_id", "field"),
        generated_columns=MEMBER_REVIEW_GENERATED_COLUMNS,
        manual_columns=MEMBER_REVIEW_MANUAL_COLUMNS,
    )

    # Simulate reviewer editing mem-1 to accepted, adding manual notes and resolved value
    rows = []
    with open(csv_file, mode="r", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["member_id"] == "mem-1":
                r["review_status"] = "accepted"
                r["resolved_value"] = "Q4772000"
                r["manual_source_url"] = "https://handbook.aph.gov.au/individual/R36"
                r["review_notes"] = "Confirmed against official bio"
            rows.append(r)

    with open(csv_file, mode="w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=MEMBER_REVIEW_GENERATED_COLUMNS + MEMBER_REVIEW_MANUAL_COLUMNS
        )
        writer.writeheader()
        writer.writerows(rows)

    # 2. Subsequent pipeline run with updated generated evidence
    updated_generated = [
        {
            "member_id": "mem-1",
            "field": "wikidata_id",
            "aph_id": "R36",
            "display_name": "Anthony Albanese",
            "wikidata_id": "",
            "aph_value": "",
            "wikidata_value": "Q4772000",
            "wikipedia_title": "Anthony Albanese",
            "source_url": "https://query.wikidata.org/updated",
            "status": "ambiguous",
            "notes": "Updated generated notes",
            "historical_value": "",
            "historical_source": "",
            "review_status": "pending",
        },
        # mem-2 is no longer emitted by generator (e.g. resolved in APH)
        # mem-3 is newly emitted
        {
            "member_id": "mem-3",
            "field": "wikidata_id",
            "aph_id": "T44",
            "display_name": "New MP",
            "wikidata_id": "",
            "aph_value": "",
            "wikidata_value": "Q999",
            "wikipedia_title": "New MP",
            "source_url": "https://query.wikidata.org",
            "status": "ambiguous",
            "notes": "Brand new",
            "historical_value": "",
            "historical_source": "",
            "review_status": "pending",
        },
    ]

    merged = merge_review_rows(
        generated_rows=updated_generated,
        existing_path=csv_file,
        key_columns=("member_id", "field"),
        generated_columns=MEMBER_REVIEW_GENERATED_COLUMNS,
        manual_columns=MEMBER_REVIEW_MANUAL_COLUMNS,
    )

    merged_by_key = {(r["member_id"], r["field"]): r for r in merged}

    # Verify mem-1 retained manual decisions while generated fields updated
    mem1 = merged_by_key[("mem-1", "wikidata_id")]
    assert mem1["review_status"] == "accepted"
    assert mem1["resolved_value"] == "Q4772000"
    assert mem1["manual_source_url"] == "https://handbook.aph.gov.au/individual/R36"
    assert mem1["review_notes"] == "Confirmed against official bio"
    assert mem1["source_url"] == "https://query.wikidata.org/updated"
    assert mem1["notes"] == "Updated generated notes"

    # Verify mem-2 was unreviewed and obsolete so dropped cleanly
    assert ("mem-2", "date_of_birth") not in merged_by_key

    # Verify mem-3 is present with default pending review_status
    mem3 = merged_by_key[("mem-3", "wikidata_id")]
    assert mem3["review_status"] == "pending"
    assert mem3["notes"] == "Brand new"


def test_merge_review_rows_preserves_reviewed_rows_not_in_generator(
    tmp_path: Path,
) -> None:
    csv_file = tmp_path / "school_review.csv"

    # Pre-populate CSV with a row that was previously reviewed as 'accepted'
    initial_row = {
        "institution_id": "inst-old-1",
        "raw_school_text": "Old Closed School",
        "suggested_institution_name": "Old School",
        "wikidata_id": "Q12345",
        "wikipedia_url": "https://en.wikipedia.org/wiki/Old_School",
        "country": "Australia",
        "locality": "Sydney",
        "latitude": "-33.8",
        "longitude": "151.2",
        "institution_type": "high school",
        "confidence": "suggested",
        "notes": "Historical note",
        "historical_match_name": "Old School Canonical",
        "historical_acara_id": "40001",
        "historical_source": "school_aliases.json",
        "review_status": "accepted",
        "resolved_school_name": "Old School Canonical",
        "resolved_acara_id": "40001",
        "resolved_wikidata_id": "Q12345",
        "resolved_country": "Australia",
        "resolved_state": "NSW",
        "resolved_suburb": "Sydney",
        "resolved_postcode": "2000",
        "resolved_address": "123 School St",
        "resolved_latitude": "-33.8",
        "resolved_longitude": "151.2",
        "manual_source_url": "https://acara.edu.au",
        "address_source_url": "https://acara.edu.au",
        "review_notes": "Accepted historical mapping",
    }
    with open(csv_file, mode="w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=SCHOOL_REVIEW_GENERATED_COLUMNS + SCHOOL_REVIEW_MANUAL_COLUMNS
        )
        writer.writeheader()
        writer.writerow(initial_row)

    # Generator emits an empty list (inst-old-1 no longer appears in unmatched query)
    merged = merge_review_rows(
        generated_rows=[],
        existing_path=csv_file,
        key_columns=("institution_id",),
        generated_columns=SCHOOL_REVIEW_GENERATED_COLUMNS,
        manual_columns=SCHOOL_REVIEW_MANUAL_COLUMNS,
    )

    # Row must be preserved because it was accepted by a reviewer
    assert len(merged) == 1
    assert merged[0]["institution_id"] == "inst-old-1"
    assert merged[0]["review_status"] == "accepted"
    assert merged[0]["resolved_acara_id"] == "40001"


# --------------------------------------------------------------------------
# Candidate Filtering & Geographic Sanity Tests
# --------------------------------------------------------------------------


def test_evaluate_school_candidate_filtering() -> None:
    # 1. Human candidate rejected
    cand_human = {
        "suggested_institution_name": "Sally Beauman",
        "institution_type": "human",
        "country": "United Kingdom",
    }
    acc, reason = evaluate_school_candidate("Beaumans High School", cand_human)
    assert not acc
    assert "non-school" in reason

    # 2. Town / Suburb candidate rejected
    cand_town = {
        "suggested_institution_name": "Alice Springs",
        "institution_type": "town",
        "country": "Australia",
    }
    acc, reason = evaluate_school_candidate("Alice Springs High School", cand_town)
    assert not acc
    assert "non-school" in reason

    # 3. Disambiguation page rejected
    cand_disambig = {
        "suggested_institution_name": "Grammar School (disambiguation)",
        "institution_type": "Wikimedia disambiguation page",
    }
    acc, reason = evaluate_school_candidate("Grammar School", cand_disambig)
    assert not acc

    # 4. List article rejected
    cand_list = {
        "suggested_institution_name": "List of schools in Victoria",
        "institution_type": "Wikimedia list article",
    }
    acc, reason = evaluate_school_candidate("Victoria School", cand_list)
    assert not acc

    # 5. Plausible Australian school retained
    cand_good = {
        "suggested_institution_name": "Academy of Mary Immaculate",
        "institution_type": "high school",
        "country": "Australia",
        "latitude": -37.8,
        "longitude": 144.97,
    }
    acc, reason = evaluate_school_candidate(
        "Academy of Mary Immaculate Fitzroy", cand_good
    )
    assert acc
    assert "Accepted" in reason

    # 6. Domestic Australian school matched to overseas country rejected
    cand_us = {
        "suggested_institution_name": "Lincoln High School",
        "institution_type": "high school",
        "country": "United States",
        "latitude": 40.7,
        "longitude": -74.0,
    }
    acc, reason = evaluate_school_candidate("Lincoln High School", cand_us)
    assert not acc
    assert "non-Australian" in reason

    # 7. Coordinates outside broad Australian bounding box rejected
    cand_bad_coords = {
        "suggested_institution_name": "Perth Modern School",
        "institution_type": "high school",
        "country": "Australia",
        "latitude": 51.5,
        "longitude": -0.1,  # London coords
    }
    acc, reason = evaluate_school_candidate("Perth Modern School", cand_bad_coords)
    assert not acc
    assert "outside Australian bounds" in reason

    # 8. International school retained where appropriate
    cand_intl = {
        "suggested_institution_name": "Belilios Public School",
        "institution_type": "secondary school",
        "country": "Hong Kong",
        "latitude": 22.28,
        "longitude": 114.19,
    }
    acc, reason = evaluate_school_candidate(
        "Belilios Public Girls School, Hong Kong", cand_intl
    )
    assert acc
    assert "Accepted" in reason

    # 9. Low name similarity (< 70) rejected
    cand_dissimilar = {
        "suggested_institution_name": "Hunter School of the Performing Arts",
        "institution_type": "high school",
        "country": "Australia",
        "latitude": -32.9,
        "longitude": 151.7,
    }
    acc, reason = evaluate_school_candidate(
        "Broadmeadows Technical School", cand_dissimilar
    )
    assert not acc
    assert "low name similarity" in reason

    # 10. Historical evidence allows borderline candidate to proceed
    hist_ev = {
        "historical_match_name": "Hunter Performing Arts Secondary",
        "historical_acara_id": "50000",
        "historical_source": "school_aliases.json",
    }
    acc, reason = evaluate_school_candidate(
        "Broadmeadows Technical School", cand_dissimilar, historical_evidence=hist_ev
    )
    assert acc
    assert "historical evidence" in reason


# --------------------------------------------------------------------------
# Member Fallback Tightening Tests
# --------------------------------------------------------------------------


def test_member_name_fallback_single_unconfirmed_is_ambiguous(
    tmp_path: Path, mock_session: MagicMock
) -> None:
    client = WikimediaClient(cache_dir=tmp_path, session=mock_session, rate_delay=0.0)

    # Exactly 1 exact name match returned by Wikidata, but without DOB and without political article title
    mock_resp = MagicMock()
    mock_resp.url = "https://query.wikidata.org/sparql?query=..."
    mock_resp.json.return_value = {
        "results": {
            "bindings": [
                {
                    "item": {"value": "http://www.wikidata.org/entity/Q999999"},
                    "article": {"value": "https://en.wikipedia.org/wiki/John_Smith"},
                }
            ]
        }
    }
    mock_session.get.return_value = mock_resp

    res = client.lookup_member_by_name("John Smith", dob=None)
    assert res is not None
    assert res["status"] == "ambiguous"
    assert res["wikidata_id"] is None
    assert "lacks corroborating" in res["notes"]
