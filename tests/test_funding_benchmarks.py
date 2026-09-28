"""Unit tests for public school funding ingestion, ACARA benchmarks, and estimation.

Validates:
- Schema tables (school_finance_benchmarks, school_public_funding) and views
- Ingestion of ACARA benchmarks, NSW RAM, TAS SRP, NT funding, QLD grants, and manual disclosures
- Deterministic hierarchical peer benchmark retrieval
- Indexed estimation with peer-average fallbacks
- Model backtesting against historical observed finances
- Relational integrity validation assertions
- Enriched GeoJSON spatial exports
All tests run locally using in-memory DuckDB fixtures without external network calls.
"""

from __future__ import annotations

from collections.abc import Generator
import json
from pathlib import Path

import duckdb
import pytest

from apemap.analysis import (
    backtest_finance_benchmarks,
    compute_school_finance_estimate,
    get_peer_group_benchmark,
    get_school_geolocation,
    validate_funding_records,
)
from apemap.db import init_schema
from apemap.export import export_spatial_geojson
from apemap.ingest.funding import (
    ingest_acara_benchmarks,
    ingest_all_funding,
    ingest_manual_school_funding,
    ingest_nsw_ram,
    ingest_nt_funding,
    ingest_qld_grants,
    ingest_tasmania_srp,
    resolve_institution_id,
)


@pytest.fixture
def db_conn() -> Generator[duckdb.DuckDBPyConnection, None, None]:
    """Fixture providing an in-memory DuckDB connection with initialized schema and test institutions."""
    conn = duckdb.connect(":memory:")
    init_schema(conn)

    # Seed minimal canonical members, parliament, and institutions for testing
    conn.execute(
        """
        INSERT INTO members (member_id, family_name, given_name, display_name)
        VALUES ('mem-1', 'Smith', 'John', 'SMITH, John');

        INSERT INTO parliament_service (
            service_id, member_id, parliament_number, chamber, party, party_abbrev, state_or_territory, is_opening_day_member
        ) VALUES ('serv-1', 'mem-1', 47, 'representatives', 'Labor', 'ALP', 'NSW', TRUE);

        INSERT INTO institutions (
            institution_id, acara_id, school_name, school_type, sector, campus_type, state, suburb, postcode, longitude, latitude
        ) VALUES
            ('acara-1001', '1001', 'Sydney Test High', 'Secondary', 'Government', 'School Single Entity', 'NSW', 'Sydney', '2000', 151.2, -33.8),
            ('acara-1002', '1002', 'Hobart Test College', 'Secondary', 'Government', 'School Single Entity', 'TAS', 'Hobart', '7000', 147.3, -42.8),
            ('acara-1003', '1003', 'Darwin Test High', 'Secondary', 'Government', 'School Single Entity', 'NT', 'Darwin', '0800', 130.8, -12.4),
            ('acara-1004', '1004', 'Brisbane Test Grammar', 'Secondary', 'Independent', 'School Single Entity', 'QLD', 'Brisbane', '4000', 153.0, -27.4),
            ('acara-1005', '1005', 'Adelaide Test College', 'Secondary', 'Independent', 'School Single Entity', 'SA', 'Adelaide', '5000', 138.6, -34.9);

        INSERT INTO member_education (
            education_id, member_id, institution_id, level, attended_status, source_url, retrieved_at, confidence
        ) VALUES (
            'edu-1', 'mem-1', 'acara-1001', 'secondary', 'graduated', 'https://example.com', '2026-09-28T00:00:00+00:00', 'verified'
        );
        """
    )
    yield conn
    conn.close()


def test_schema_tables_and_views(db_conn: duckdb.DuckDBPyConnection) -> None:
    """Assert canonical funding tables and views exist and have proper column schemas."""
    # Tables exist
    res = db_conn.execute("SELECT count(*) FROM school_finance_benchmarks").fetchone()
    assert res is not None and res[0] == 0

    res = db_conn.execute("SELECT count(*) FROM school_public_funding").fetchone()
    assert res is not None and res[0] == 0

    # Views exist
    res = db_conn.execute("SELECT count(*) FROM v_school_finance_benchmarks").fetchone()
    assert res is not None and res[0] == 0

    res = db_conn.execute("SELECT count(*) FROM v_school_public_funding").fetchone()
    assert res is not None and res[0] == 0


def test_ingest_acara_benchmarks(
    db_conn: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """Assert ACARA benchmarks can be ingested deterministically and updated on conflict."""
    csv_file = tmp_path / "test_benchmarks.csv"
    csv_file.write_text(
        "reporting_year,state_or_territory,sector,geolocation,metric,value,unit,source_dataset,source_url,retrieved_at\n"
        "2024,NSW,Government,Major Cities,total_net_recurrent_income_per_student,20000.0,AUD_per_student,ACARA Test,https://example.com,2026-09-28T00:00:00Z\n"
        "2024,NSW,Government,All,total_net_recurrent_income_per_student,19500.0,AUD_per_student,ACARA Test,https://example.com,2026-09-28T00:00:00Z\n"
        "2021,NSW,Government,Major Cities,total_net_recurrent_income_per_student,16000.0,AUD_per_student,ACARA Test,https://example.com,2026-09-28T00:00:00Z\n",
        encoding="utf-8",
    )

    count = ingest_acara_benchmarks(db_conn, csv_file)
    assert count == 3

    # Verify query
    row = db_conn.execute(
        """
        SELECT value, unit FROM school_finance_benchmarks
        WHERE reporting_year = 2024 AND state_or_territory = 'NSW' AND sector = 'Government' AND geolocation = 'Major Cities'
        """
    ).fetchone()
    assert row is not None
    assert row[0] == 20000.0
    assert row[1] == "AUD_per_student"

    # Re-ingest with updated value (upsert)
    csv_file.write_text(
        "reporting_year,state_or_territory,sector,geolocation,metric,value,unit,source_dataset,source_url,retrieved_at\n"
        "2024,NSW,Government,Major Cities,total_net_recurrent_income_per_student,20500.0,AUD_per_student,ACARA Test,https://example.com,2026-09-28T00:00:00Z\n",
        encoding="utf-8",
    )
    count2 = ingest_acara_benchmarks(db_conn, csv_file)
    assert count2 == 1

    row2 = db_conn.execute(
        """
        SELECT value FROM school_finance_benchmarks
        WHERE reporting_year = 2024 AND state_or_territory = 'NSW' AND sector = 'Government' AND geolocation = 'Major Cities'
        """
    ).fetchone()
    assert row2 is not None and row2[0] == 20500.0


def test_resolve_institution_id(db_conn: duckdb.DuckDBPyConnection) -> None:
    """Assert institution resolution matches by ACARA ID, or name and state."""
    # Match by ACARA ID
    assert resolve_institution_id(db_conn, acara_id="1001") == "acara-1001"
    assert resolve_institution_id(db_conn, acara_id=1001) == "acara-1001"
    assert resolve_institution_id(db_conn, acara_id="1001.0") == "acara-1001"

    # Match by school name + state
    assert (
        resolve_institution_id(db_conn, school_name="Sydney Test High", state="NSW")
        == "acara-1001"
    )
    assert (
        resolve_institution_id(db_conn, school_name="sydney test high", state="NSW")
        == "acara-1001"
    )

    # Unmatched
    assert resolve_institution_id(db_conn, acara_id="9999") is None
    assert resolve_institution_id(db_conn, school_name="Unknown School XYZ") is None


def test_ingest_nsw_ram(db_conn: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    """Assert NSW RAM allocations are ingested with native metrics and matched to institutions."""
    csv_file = tmp_path / "nsw_ram.csv"
    csv_file.write_text(
        "school_code,school_name,reporting_year,ram_base_allocation,ram_equity_loading,ram_operational_funding,ram_allocation_total,acara_id\n"
        "8100,Sydney Test High,2024,5000000.0,1200000.0,400000.0,6600000.0,1001\n"
        "9999,Unmatched NSW School,2024,1000000.0,200000.0,100000.0,1300000.0,9999\n",
        encoding="utf-8",
    )

    count = ingest_nsw_ram(db_conn, csv_file)
    assert count == 4  # 4 metrics for the 1 matched school

    rows = db_conn.execute(
        """
        SELECT metric, value, unit, funding_model, source_dataset
        FROM school_public_funding
        WHERE institution_id = 'acara-1001'
        ORDER BY metric
        """
    ).fetchall()
    assert len(rows) == 4
    metrics = {r[0]: r[1] for r in rows}
    assert metrics["ram_allocation_total"] == 6600000.0
    assert metrics["ram_base_allocation"] == 5000000.0
    assert rows[0][3] == "Resource Allocation Model (RAM)"
    assert rows[0][4] == "Data.NSW Education Resource Allocation Model"


def test_ingest_tasmania_srp(
    db_conn: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """Assert Tasmania SRP allocations are ingested with Fairer Funding terminology."""
    csv_file = tmp_path / "tas_srp.csv"
    csv_file.write_text(
        "school_code,school_name,reporting_year,srp_core_staffing,srp_operational_allocation,srp_allocation_total,acara_id\n"
        "5100,Hobart Test College,2024,4000000.0,500000.0,4500000.0,1002\n",
        encoding="utf-8",
    )

    count = ingest_tasmania_srp(db_conn, csv_file)
    assert count == 3

    row = db_conn.execute(
        """
        SELECT metric, value, funding_model
        FROM school_public_funding
        WHERE institution_id = 'acara-1002' AND metric = 'srp_allocation_total'
        """
    ).fetchone()
    assert row is not None
    assert row[1] == 4500000.0
    assert row[2] == "School Resource Package (Fairer Funding Model)"


def test_ingest_nt_funding(db_conn: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    """Assert Northern Territory resourcing and per-student rates are ingested."""
    csv_file = tmp_path / "nt_funding.csv"
    csv_file.write_text(
        "school_name,reporting_year,annual_school_resourcing_allocation,per_student_funding_rate,acara_id\n"
        "Darwin Test High,2024,8000000.0,24000.0,1003\n",
        encoding="utf-8",
    )

    count = ingest_nt_funding(db_conn, csv_file)
    assert count == 2

    rows = db_conn.execute(
        """
        SELECT metric, value, unit, funding_model
        FROM school_public_funding
        WHERE institution_id = 'acara-1003'
        ORDER BY metric
        """
    ).fetchall()
    assert len(rows) == 2
    metrics = {r[0]: (r[1], r[2]) for r in rows}
    assert metrics["annual_school_resourcing_allocation"] == (8000000.0, "AUD")
    assert metrics["per_student_funding_rate"] == (24000.0, "AUD_per_student")
    assert rows[0][3] == "School Needs Based Funding Formula"


def test_ingest_qld_grants(db_conn: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    """Assert Queensland non-state recurrent grants are ingested."""
    csv_file = tmp_path / "qld_grants.csv"
    csv_file.write_text(
        "school_name,reporting_year,state_recurrent_grant_rate_primary,state_recurrent_grant_rate_secondary,state_recurrent_grant_total,acara_id\n"
        "Brisbane Test Grammar,2024,1800.0,2900.0,3500000.0,1004\n",
        encoding="utf-8",
    )

    count = ingest_qld_grants(db_conn, csv_file)
    assert count == 3

    row = db_conn.execute(
        """
        SELECT metric, value, unit
        FROM school_public_funding
        WHERE institution_id = 'acara-1004' AND metric = 'state_recurrent_grant_rate_secondary'
        """
    ).fetchone()
    assert row is not None
    assert row[1] == 2900.0
    assert row[2] == "AUD_per_student"


def test_ingest_manual_school_funding(
    db_conn: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """Assert authoritative manual school funding records are ingested."""
    csv_file = tmp_path / "manual_funding.csv"
    csv_file.write_text(
        "acara_id,reporting_year,metric,value,unit,source_url,source_type,reviewed_at,notes\n"
        "1005,2024,total_net_recurrent_income_per_student,30000.0,AUD_per_student,https://example.com/report,annual_report,2026-09-28T00:00:00Z,Test annual report\n",
        encoding="utf-8",
    )

    count = ingest_manual_school_funding(db_conn, csv_file)
    assert count == 1

    row = db_conn.execute(
        """
        SELECT value, unit, funding_model, source_dataset
        FROM school_public_funding
        WHERE institution_id = 'acara-1005'
        """
    ).fetchone()
    assert row is not None
    assert row[0] == 30000.0
    assert row[1] == "AUD_per_student"
    assert "annual_report" in row[2]


def test_hierarchical_peer_benchmark(
    db_conn: duckdb.DuckDBPyConnection,
) -> None:
    """Assert hierarchical benchmark retrieval falls back gracefully when specific combinations are missing."""
    # Seed benchmarks at various levels
    db_conn.execute(
        """
        INSERT INTO school_finance_benchmarks (
            reporting_year, state_or_territory, sector, geolocation, metric, value, unit, source_dataset, source_url, retrieved_at
        ) VALUES
            (2024, 'NSW', 'Government', 'Major Cities', 'total_net_recurrent_income_per_student', 21000.0, 'AUD_per_student', 'ACARA', 'https://example.com', '2026-09-28T00:00:00Z'),
            (2024, 'NSW', 'Government', 'All', 'total_net_recurrent_income_per_student', 20500.0, 'AUD_per_student', 'ACARA', 'https://example.com', '2026-09-28T00:00:00Z'),
            (2024, 'All', 'Government', 'All', 'total_net_recurrent_income_per_student', 20000.0, 'AUD_per_student', 'ACARA', 'https://example.com', '2026-09-28T00:00:00Z'),
            (2024, 'All', 'All', 'All', 'total_net_recurrent_income_per_student', 19000.0, 'AUD_per_student', 'ACARA', 'https://example.com', '2026-09-28T00:00:00Z');
        """
    )

    # Level 1: exact match
    b1 = get_peer_group_benchmark(db_conn, 2024, "NSW", "Government", "Major Cities")
    assert b1 is not None
    assert b1["value"] == 21000.0
    assert b1["fallback_level"] == 1

    # Level 2: state + sector + All
    b2 = get_peer_group_benchmark(db_conn, 2024, "NSW", "Government", "Very Remote")
    assert b2 is not None
    assert b2["value"] == 20500.0
    assert b2["fallback_level"] == 2

    # Level 4: All state + sector + All
    b4 = get_peer_group_benchmark(db_conn, 2024, "WA", "Government", "Very Remote")
    assert b4 is not None
    assert b4["value"] == 20000.0
    assert b4["fallback_level"] == 4


def test_compute_school_finance_estimate_precedence(
    db_conn: duckdb.DuckDBPyConnection,
) -> None:
    """Assert estimation respects precedence: observed > indexed estimate > peer group average."""
    # Seed benchmarks
    db_conn.execute(
        """
        INSERT INTO school_finance_benchmarks (
            reporting_year, state_or_territory, sector, geolocation, metric, value, unit, source_dataset, source_url, retrieved_at
        ) VALUES
            (2021, 'NSW', 'Government', 'All', 'total_net_recurrent_income_per_student', 16000.0, 'AUD_per_student', 'ACARA', 'https://example.com', '2026-09-28T00:00:00Z'),
            (2024, 'NSW', 'Government', 'All', 'total_net_recurrent_income_per_student', 20000.0, 'AUD_per_student', 'ACARA', 'https://example.com', '2026-09-28T00:00:00Z'),
            (2024, 'TAS', 'Government', 'All', 'total_net_recurrent_income_per_student', 21000.0, 'AUD_per_student', 'ACARA', 'https://example.com', '2026-09-28T00:00:00Z');
        """
    )

    # 1. School with historical 2021 finance -> should compute indexed estimate
    db_conn.execute(
        """
        INSERT INTO school_finances (
            institution_id, acara_id, reporting_year, total_net_recurrent_income_per_student
        ) VALUES ('acara-1001', '1001', 2021, 17600); -- 10% above benchmark (17600 / 16000 = 1.10)
        """
    )
    est1 = compute_school_finance_estimate(db_conn, "acara-1001", target_year=2024)
    assert est1["status"] == "estimated_indexed"
    assert est1["method"] == "acara_state_sector_geolocation_index"
    assert est1["relative_multiplier"] == 1.10
    assert est1["value"] == 22000  # 20000 * 1.10

    # 2. School with observed target year finance -> must return exact observed value
    db_conn.execute(
        """
        INSERT INTO school_finances (
            institution_id, acara_id, reporting_year, total_net_recurrent_income_per_student
        ) VALUES ('acara-1001', '1001', 2024, 23500);
        """
    )
    est_obs = compute_school_finance_estimate(db_conn, "acara-1001", target_year=2024)
    assert est_obs["status"] == "observed"
    assert est_obs["method"] == "direct_observation"
    assert est_obs["value"] == 23500.0

    # 3. School without historical finance -> should fall back to peer group average
    est2 = compute_school_finance_estimate(db_conn, "acara-1002", target_year=2024)
    assert est2["status"] == "benchmark_average"
    assert est2["method"] == "peer_group_average"
    assert est2["value"] == 21000

    # 4. Unknown institution -> unavailable
    est_none = compute_school_finance_estimate(
        db_conn, "inst-unknown", target_year=2024
    )
    assert est_none["status"] == "unavailable"
    assert est_none["value"] is None


def test_backtest_finance_benchmarks(
    db_conn: duckdb.DuckDBPyConnection,
) -> None:
    """Assert backtesting computes ratio, MAD, MAPE, and identifies high-dispersion groups."""
    db_conn.execute(
        """
        INSERT INTO school_finance_benchmarks (
            reporting_year, state_or_territory, sector, geolocation, metric, value, unit, source_dataset, source_url, retrieved_at
        ) VALUES
            (2021, 'NSW', 'Government', 'All', 'total_net_recurrent_income_per_student', 16000.0, 'AUD_per_student', 'ACARA', 'https://example.com', '2026-09-28T00:00:00Z'),
            (2021, 'All', 'Government', 'All', 'total_net_recurrent_income_per_student', 16000.0, 'AUD_per_student', 'ACARA', 'https://example.com', '2026-09-28T00:00:00Z');

        INSERT INTO school_finances (
            institution_id, acara_id, reporting_year, total_net_recurrent_income_per_student
        ) VALUES
            ('acara-1001', '1001', 2021, 16000),
            ('acara-1002', '1002', 2021, 16800),
            ('acara-1003', '1003', 2021, 15200);
        """
    )

    report = backtest_finance_benchmarks(db_conn, historical_year=2021)
    assert report["sample_size"] == 3
    assert report["overall_median_ratio"] is not None
    assert report["overall_mad"] is not None
    assert report["overall_mape"] is not None
    assert "Government" in report["sector_metrics"]


def test_validate_funding_records(db_conn: duckdb.DuckDBPyConnection) -> None:
    """Assert integrity assertions detect negative values, invalid years, and orphans."""
    # 1. Initially valid (empty or clean)
    res = validate_funding_records(db_conn)
    assert res["passed"] is True

    # 2. Insert negative value
    db_conn.execute(
        """
        INSERT INTO school_finance_benchmarks (
            reporting_year, state_or_territory, sector, geolocation, metric, value, unit, source_dataset, source_url, retrieved_at
        ) VALUES (2024, 'NSW', 'Government', 'All', 'total_net_recurrent_income_per_student', -500.0, 'AUD_per_student', 'ACARA', 'https://example.com', '2026-09-28T00:00:00Z');
        """
    )
    res_bad = validate_funding_records(db_conn)
    assert res_bad["passed"] is False
    assert any("negative values" in f for f in res_bad["failures"])


def test_export_spatial_geojson_properties(
    db_conn: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """Assert export_spatial_geojson attaches finance and public funding metadata."""
    # Seed benchmarks and public funding
    db_conn.execute(
        """
        INSERT INTO school_finance_benchmarks (
            reporting_year, state_or_territory, sector, geolocation, metric, value, unit, source_dataset, source_url, retrieved_at
        ) VALUES
            (2024, 'NSW', 'Government', 'All', 'total_net_recurrent_income_per_student', 20000.0, 'AUD_per_student', 'ACARA', 'https://example.com', '2026-09-28T00:00:00Z');

        INSERT INTO school_public_funding (
            institution_id, reporting_year, jurisdiction, metric, value, unit, funding_model, source_dataset, source_url, retrieved_at
        ) VALUES
            ('acara-1001', 2024, 'NSW', 'ram_allocation_total', 7500000.0, 'AUD', 'Resource Allocation Model (RAM)', 'Data.NSW', 'https://example.com', '2026-09-28T00:00:00Z');
        """
    )

    exported = export_spatial_geojson(db_conn, output_dir=tmp_path, parliaments=[47])
    assert 47 in exported

    geojson_data = json.loads(exported[47].read_text(encoding="utf-8"))
    features = geojson_data["features"]
    assert len(features) >= 1

    props = features[0]["properties"]
    assert "finance_year" in props
    assert props["finance_year"] == 2024
    assert props["finance_status"] in (
        "observed",
        "estimated_indexed",
        "benchmark_average",
    )
    assert "public_funding_year" in props
    assert props["public_funding_jurisdiction"] == "NSW"
    assert props["public_funding_value"] == 7500000.0
    assert props["public_funding_model"] == "Resource Allocation Model (RAM)"


def test_get_school_geolocation_fallback() -> None:
    """Assert geolocation returns 'All' for unmapped ACARA ID or None."""
    assert get_school_geolocation(None) == "All"
    assert get_school_geolocation("999999") in (
        "Major Cities",
        "Inner Regional",
        "Outer Regional",
        "Remote",
        "Very Remote",
        "All",
    )


def test_ingest_all_funding_orchestration(
    db_conn: duckdb.DuckDBPyConnection,
) -> None:
    """Assert ingest_all_funding coordinates all ingestion steps."""
    counts = ingest_all_funding(db_conn)
    assert isinstance(counts, dict)
    assert "acara_benchmarks" in counts
    assert "nsw_ram" in counts
    assert "tasmania_srp" in counts
    assert "nt_funding" in counts
    assert "qld_grants" in counts
    assert "manual_enrichment" in counts
