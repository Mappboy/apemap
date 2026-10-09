"""Offline regression coverage for historical cohorts, schools and releases."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from apemap.constants import (
    PARLIAMENT_METADATA,
    current_parliament,
    supported_parliaments,
)
from apemap.coverage import compute_parliament_coverage
from apemap.db import (
    get_connection,
    init_schema,
    export_to_parquet,
    load_parquet_sources,
)
from apemap.export import export_spatial_geojson, export_results_summary
from apemap.ingest.acara import run_acara_ingestion
from apemap.ingest.aph import parse_individual
from apemap.ingest.matching import SchoolMatcher
from apemap.ingest.pipeline import run_aph_ingestion
from apemap.ingest.service import service_date

pytestmark = pytest.mark.integration


def historical_member() -> dict[str, Any]:
    return {
        "PHID": "HIST",
        "GivenName": "Historical",
        "FamilyName": "Member",
        "RepresentedParliaments": supported_parliaments(),
        "RepresentedParties": ["Australian Labor Party", "Independent"],
        "MPorSenator": ["Member", "Senator"],
        "SenateState": "Victoria",
        "Party": "Independent",
        "InCurrentParliament": "True",
        "ServiceHistory_Start": "2007-11-24",
        "ServiceHistory_End": "2026-09-21",
        "ElectorateService": [
            {
                "Electorate": "Old Seat",
                "State": "Victoria",
                "ServiceStart": "2007-11-24",
                "ServiceEnd": "2014-06-30",
            }
        ],
        "PartyParliamentaryService": [
            {
                "DateStart": "2007-11-24",
                "DateEnd": "2014-06-30",
                "SecondaryService": [
                    {
                        "RoSType": "Parties Represented",
                        "Value": "Australian Labor Party",
                        "DateStart": "2007-11-24",
                        "DateEnd": "2014-06-30",
                    }
                ],
            },
            {
                "DateStart": "2016-07-02",
                "DateEnd": "2026-09-21",
                "SecondaryService": [
                    {
                        "RoSType": "Parties Represented",
                        "Value": "Independent",
                        "DateStart": "2016-07-02",
                        "DateEnd": "2026-09-21",
                    }
                ],
            },
        ],
        "SecondarySchool": "Historic High School",
    }


def test_metadata_and_dated_chamber_history() -> None:
    assert supported_parliaments() == list(range(42, 49))
    assert current_parliament() == 48
    assert service_date("1900-01-01", end=True) is None
    for p, info in PARLIAMENT_METADATA.items():
        assert info["general_election_date"] < info["opening_date"]
        assert info["end_date"] is None or info["opening_date"] < info["end_date"]
        assert info["parliament_number"] == p
    parsed = parse_individual(historical_member())
    assert parsed
    opening = {
        s.parliament_number: s for s in parsed.services if s.is_opening_day_member
    }
    assert opening[42].electorate == "Old Seat"
    assert opening[42].party == "Australian Labor Party"
    assert opening[45].chamber == "senate"
    assert opening[45].electorate is None
    assert not opening[45].is_current_member
    assert opening[48].is_current_member
    assert opening[48].party == "Independent"
    reordered = historical_member()
    reordered["PartyParliamentaryService"].reverse()
    reparsed = parse_individual(reordered)
    assert reparsed and parsed.services == reparsed.services


def test_late_entry_gap_and_party_change() -> None:
    raw = historical_member()
    raw["PartyParliamentaryService"] = [
        {"DateStart": "2008-07-01", "DateEnd": "2009-01-01", "SecondaryService": []},
        {"DateStart": "2009-06-01", "DateEnd": "2010-06-01", "SecondaryService": []},
    ]
    raw["InCurrentParliament"] = "False"
    parsed = parse_individual(raw, {42})
    assert parsed and len(parsed.services) == 2
    assert all(not s.is_opening_day_member for s in parsed.services)
    assert parsed.services[0].service_end == "2009-01-01"
    raw = historical_member()
    raw["PartyParliamentaryService"][0]["SecondaryService"].append(
        {
            "RoSType": "Parties Represented",
            "Value": "Independent",
            "DateStart": "2009-01-01",
            "DateEnd": "2014-06-30",
        }
    )
    parsed = parse_individual(raw, {42})
    assert parsed and len(parsed.services) == 2
    assert parsed.services[0].is_opening_day_member
    assert not parsed.services[1].is_opening_day_member
    assert parsed.services[1].party == "Independent"


def test_longitudinal_only_school_and_ambiguous_name(
    empty_review_log: Path, tmp_path: Path
) -> None:
    profile = tmp_path / "school-profile-2008-2025.csv"
    profile.write_text(
        "ACARA SML ID,School Name,School Sector,School Type,State,Calendar Year,Total Enrolments\n1,Historic High School,Government,Secondary,VIC,2008,300\n1,Renamed High School,Government,Secondary,VIC,2009,350\n2,Ambiguous High School,Government,Secondary,VIC,2008,100\n3,Ambiguous High School,Government,Secondary,NSW,2008,200\n",
        newline="\n",
        encoding="utf-8",
    )
    matcher = SchoolMatcher(external_dir=tmp_path, reference_dir=tmp_path)
    matched = matcher.match("Historic High School")
    assert matched.acara_id == "1"
    assert matched.institution_status == "historical_only"
    assert matcher.match("Ambiguous High School").confidence == "unconfirmed"
    with get_connection() as conn:
        run_acara_ingestion(
            decision_log_path=empty_review_log,
            download_latest=False,
            conn=conn,
            external_dir=tmp_path,
            gpkg_path=tmp_path / "missing.gpkg",
            export_parquet_files=False,
        )
        assert conn.execute("SELECT count(*) FROM school_snapshots").fetchone() == (4,)
        result = run_aph_ingestion(
            decision_log_path=empty_review_log,
            raw_individuals=[historical_member()],
            conn=conn,
            external_dir=tmp_path,
            output_dir=tmp_path / "out",
        )
        assert result["snapshots_count"] == 0
        assert conn.execute("SELECT count(*) FROM members").fetchone() == (1,)
        rows = compute_parliament_coverage(conn, [42, 45])
        assert all(r["opening_day_members"] == 1 for r in rows)
        assert all(r["historical_schools"] == 1 for r in rows)
        assert all(r["schools_with_estimated_finance"] == 0 for r in rows)
        assert conn.execute(
            "SELECT DISTINCT attended_status FROM member_education"
        ).fetchall() == [("attended_unspecified",)]


def test_all_layers_and_latest_profile_grain(
    empty_review_log: Path, tmp_path: Path
) -> None:
    with get_connection() as conn:
        init_schema(conn)
        result = run_aph_ingestion(
            decision_log_path=empty_review_log,
            raw_individuals=[historical_member()],
            conn=conn,
            external_dir=tmp_path,
            output_dir=tmp_path / "out",
        )
        assert result["service_count"] >= 7
        conn.execute(
            """UPDATE member_education SET historical_scope_confirmed=TRUE,
            historical_longitude=145, historical_latitude=-37,
            historical_location_source_url='https://example.org/original-location'"""
        )
        iid = conn.execute("SELECT institution_id FROM institutions").fetchone()
        assert iid
        conn.execute(
            "INSERT INTO school_snapshots(institution_id,snapshot_year) VALUES (?,2008),(?,2025)",
            [iid[0], iid[0]],
        )
        layers = export_spatial_geojson(conn, tmp_path / "layers")
        assert set(layers) == set(supported_parliaments())
        for path in layers.values():
            features = json.loads(path.read_text())["features"]
            assert len(features) == 1
            assert features[0]["properties"]["profile_year"] == 2025
            assert features[0]["properties"]["source_url"]
        summary = json.loads(export_results_summary(conn, tmp_path).read_text())
        assert (
            sorted(map(int, summary["parliament_metadata"])) == supported_parliaments()
        )
        export_to_parquet(conn, tmp_path / "parquet")
        with get_connection() as rebuilt:
            init_schema(rebuilt)
            load_parquet_sources(rebuilt, tmp_path / "parquet")
            assert (
                rebuilt.execute("SELECT count(*) FROM parliament_service").fetchone()
                == conn.execute("SELECT count(*) FROM parliament_service").fetchone()
            )


def test_missing_education_export_discards_local_edits(
    empty_review_log: Path, tmp_path: Path
) -> None:
    raw = historical_member()
    raw["SecondarySchool"] = ""
    run_aph_ingestion(
        decision_log_path=empty_review_log,
        raw_individuals=[raw],
        db_path=tmp_path / "db.duckdb",
        external_dir=tmp_path,
        output_dir=tmp_path,
    )
    path = tmp_path / "historical_education_review.csv"
    path.write_text(
        "review_status,review_notes\naccepted,Local CSV edit\n", newline="\n"
    )
    run_aph_ingestion(
        decision_log_path=empty_review_log,
        raw_individuals=[raw],
        db_path=tmp_path / "db.duckdb",
        external_dir=tmp_path,
        output_dir=tmp_path,
    )
    assert "Local CSV edit" not in path.read_text()
    assert "review_status" not in path.read_text()
    assert "No APH secondary-school evidence" in path.read_text()


def test_corrected_source_removes_stale_generated_rows(
    empty_review_log: Path, tmp_path: Path
) -> None:
    raw = historical_member()
    with get_connection() as conn:
        run_aph_ingestion(
            [42],
            decision_log_path=empty_review_log,
            raw_individuals=[raw],
            conn=conn,
            external_dir=tmp_path,
            output_dir=tmp_path,
        )
        raw["SecondarySchool"] = ""
        run_aph_ingestion(
            [42],
            decision_log_path=empty_review_log,
            raw_individuals=[raw],
            conn=conn,
            external_dir=tmp_path,
            output_dir=tmp_path,
        )
        assert conn.execute("SELECT count(*) FROM member_education").fetchone() == (0,)
        raw["RepresentedParliaments"] = [48]
        run_aph_ingestion(
            [42],
            decision_log_path=empty_review_log,
            raw_individuals=[raw],
            conn=conn,
            external_dir=tmp_path,
            output_dir=tmp_path,
        )
        assert conn.execute("SELECT count(*) FROM parliament_service").fetchone() == (
            0,
        )


def test_sourced_service_override_replaces_whole_term(
    empty_review_log: Path, tmp_path: Path
) -> None:
    path = tmp_path / "overrides.csv"
    path.write_text(
        "aph_id,parliament_number,service_start,service_end,chamber,party,party_abbrev,electorate,state_or_territory,source_url,retrieved_at,reviewer_notes\n"
        "HIST,42,2008-02-12,2010-07-19,representatives,Reviewed Party,RP,Reviewed Seat,VIC,https://example.org/primary,2026-10-02T00:00:00Z,Reviewed primary evidence\n",
        newline="\n",
    )
    with get_connection() as conn:
        for _ in range(2):
            run_aph_ingestion(
                [42],
                decision_log_path=empty_review_log,
                raw_individuals=[historical_member()],
                conn=conn,
                external_dir=tmp_path,
                output_dir=tmp_path,
                service_overrides_path=path,
            )
        rows = conn.execute(
            "SELECT party, electorate, source_url, is_opening_day_member FROM parliament_service"
        ).fetchall()
        assert rows == [
            ("Reviewed Party", "Reviewed Seat", "https://example.org/primary", True)
        ]


def test_sourced_successor_alias(tmp_path: Path) -> None:
    (tmp_path / "school-location-2025.csv").write_text(
        "ACARA SML ID,School Name,School Sector,State,Latitude,Longitude\n123,Successor College,Government,VIC,-37,145\n",
        newline="\n",
    )
    aliases = tmp_path / "aliases.json"
    aliases.write_text(
        json.dumps(
            {
                "aliases": {
                    "old college": {
                        "canonical_acara_id": "123",
                        "canonical_name": "Successor College",
                        "sector": "Government",
                        "state": "VIC",
                        "relationship_type": "successor",
                        "source_url": "https://example.org/history",
                    }
                }
            }
        ),
        newline="\n",
    )
    matcher = SchoolMatcher(
        external_dir=tmp_path, aliases_file=aliases, require_alias_sources=True
    )
    matched = matcher.match("Old College")
    assert matched.acara_id == "123"
    assert matched.confidence != "unconfirmed"


def test_invalid_source_sea_is_withheld_with_audit(tmp_path: Path) -> None:
    from apemap.ingest.acara import load_snapshots_dataframe

    (tmp_path / "school-profile-2008-2025.csv").write_text(
        "Calendar Year,ACARA SML ID,Total Enrolments,Bottom SEA Quarter,Lower Middle SEA Quarter,Upper Middle SEA Quarter,Top SEA Quarter\n"
        "2010,123,100,14,7,12,10\n",
        newline="\n",
    )
    audit = tmp_path / "audit.csv"
    frame = load_snapshots_dataframe(tmp_path, review_path=audit)
    assert frame.iloc[0]["total_enrolments"] == 100
    assert frame.iloc[0]["sea_bottom_quarter_pct"] is None
    assert "14.0, 7.0, 12.0, 10.0" in audit.read_text()


def test_manual_biography_does_not_verify_unresolved_institution(
    empty_review_log: Path,
    tmp_path: Path,
) -> None:
    path = tmp_path / "manual.csv"
    path.write_text(
        "aph_id,school_name,source_url,retrieved_at,confidence,reviewer_notes,attended_status\n"
        "HIST,Unresolved Academy,https://example.org/biography,2026-10-02T00:00:00Z,verified,Explicit attendance,attended_unspecified\n",
        newline="\n",
    )
    raw = historical_member()
    raw["SecondarySchool"] = ""
    with get_connection() as conn:
        run_aph_ingestion(
            [42],
            decision_log_path=empty_review_log,
            raw_individuals=[raw],
            conn=conn,
            external_dir=tmp_path,
            output_dir=tmp_path,
            manual_education_path=path,
        )
        assert conn.execute(
            "SELECT confidence, attended_status, source_url, evidence_origin FROM member_education"
        ).fetchall() == [
            (
                "unconfirmed",
                "attended_unspecified",
                "https://example.org/biography",
                "manual",
            )
        ]


def test_pinned_funding_timestamp_and_historical_rows(tmp_path: Path) -> None:
    from datetime import datetime, timezone
    from apemap.ingest.funding import ingest_nsw_ram

    path = tmp_path / "ram.csv"
    path.write_text(
        "school_code,school_name,reporting_year,ram_allocation_total,acara_id\n"
        "1,Example School,2024,1000,123\n",
        newline="\n",
    )
    stamp = datetime(2026, 10, 2, tzinfo=timezone.utc)
    with get_connection() as conn:
        init_schema(conn)
        conn.execute(
            "INSERT INTO institutions(institution_id,acara_id,school_name,sector) VALUES ('acara-123','123','Example School','Government')"
        )
        for _ in range(2):
            ingest_nsw_ram(conn, path, retrieved_at=stamp)
        assert conn.execute(
            "SELECT count(*), min(retrieved_at), max(retrieved_at) FROM school_public_funding"
        ).fetchone() == (1, stamp, stamp)
