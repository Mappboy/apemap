"""Unit tests for public school funding ingestion, ACARA benchmarks, and estimation.

Validates:
- Schema tables (school_finance_benchmarks, school_public_funding) and views
- Ingestion of ACARA benchmarks, NSW RAM, TAS SRP, NT funding, QLD grants, and manual disclosures
- Deterministic hierarchical peer benchmark retrieval
- Indexed estimation with peer-average fallbacks
- Model backtesting against historical observed finances
- Relational integrity validation assertions
- Enriched GeoJSON spatial exports
All tests run locally using private DuckDB fixtures without external network calls.
"""

from __future__ import annotations

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
from tests.db_fixtures import DatabaseFactory, build_template

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


def seed_funding_db(conn: duckdb.DuckDBPyConnection) -> None:
    """Seed the small funding fixture without unrelated canonical data."""
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


@pytest.fixture(scope="session")
def funding_db_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_template(
        tmp_path_factory.mktemp("funding") / "seed.duckdb", seed_funding_db
    )


@pytest.fixture
def db_conn(
    funding_db_template: Path,
    database_factory: DatabaseFactory,
) -> duckdb.DuckDBPyConnection:
    return database_factory(funding_db_template)[1]


@pytest.fixture
def funding_inputs(tmp_path: Path) -> dict[str, Path]:
    """Cover every orchestration input with small, synthetic source-native CSVs."""
    samples = {
        "benchmarks_path": (
            "reporting_year,state_or_territory,sector,geolocation,metric,value,unit,source_dataset,source_url,retrieved_at\n"
            "2024,NSW,Government,All,total_net_recurrent_income_per_student,20000,AUD_per_student,Test benchmark,https://example.invalid,2026-09-28T00:00:00Z\n"
        ),
        "nsw_path": (
            "school_name,reporting_year,ram_base_allocation,ram_equity_loading,ram_operational_funding,ram_allocation_total,acara_id\n"
            "Sydney Test High,2024,1000000,200000,100000,1300000,1001\n"
        ),
        "tas_path": (
            "school_name,reporting_year,srp_core_staffing,srp_operational_allocation,srp_allocation_total,acara_id\n"
            "Hobart Test College,2024,1000000,200000,1200000,1002\n"
        ),
        "nt_path": (
            "school_name,reporting_year,annual_school_resourcing_allocation,per_student_funding_rate,acara_id\n"
            "Darwin Test High,2024,1000000,20000,1003\n"
        ),
        "qld_path": (
            "school_name,reporting_year,state_recurrent_grant_rate_primary,state_recurrent_grant_rate_secondary,state_recurrent_grant_total,acara_id\n"
            "Brisbane Test Grammar,2024,1000,2000,1000000,1004\n"
        ),
        "manual_path": (
            "acara_id,reporting_year,metric,value,unit,source_url,source_type,reviewed_at,notes\n"
            "1005,2024,total_net_recurrent_income_per_student,30000,AUD_per_student,https://example.invalid,annual_report,2026-09-28T00:00:00Z,Synthetic disclosure\n"
        ),
    }
    paths = {}
    for name, content in samples.items():
        path = tmp_path / f"{name}.csv"
        path.write_text(content, encoding="utf-8")
        paths[name] = path
    return paths


@pytest.mark.unit
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


@pytest.mark.unit
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


@pytest.mark.unit
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


@pytest.mark.unit
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


@pytest.mark.unit
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


@pytest.mark.unit
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


@pytest.mark.unit
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


@pytest.mark.unit
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


@pytest.mark.unit
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


@pytest.mark.unit
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

    # 1. School with historical 2021 finance in a reliable group -> should compute indexed estimate
    db_conn.execute(
        """
        INSERT INTO institutions (
            institution_id, acara_id, school_name, school_type, sector, campus_type, state, suburb, postcode
        ) VALUES
            ('acara-1006', '1006', 'Sydney Test 6', 'Secondary', 'Government', 'School Single Entity', 'NSW', 'Sydney', '2000'),
            ('acara-1007', '1007', 'Sydney Test 7', 'Secondary', 'Government', 'School Single Entity', 'NSW', 'Sydney', '2000');
        INSERT INTO school_finances (
            institution_id, acara_id, reporting_year, total_net_recurrent_income_per_student
        ) VALUES
            ('acara-1001', '1001', 2021, 17600), -- 10% above benchmark (17600 / 16000 = 1.10)
            ('acara-1006', '1006', 2021, 17400),
            ('acara-1007', '1007', 2021, 17800);
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
    # acara-1005 is SA Independent with no 2021 finance record
    db_conn.execute(
        """
        INSERT INTO school_finance_benchmarks (
            reporting_year, state_or_territory, sector, geolocation, metric, value, unit, source_dataset, source_url, retrieved_at
        ) VALUES (2024, 'SA', 'Independent', 'All', 'total_net_recurrent_income_per_student', 25000.0, 'AUD_per_student', 'ACARA', 'https://example.com', '2026-09-28T00:00:00Z');
        """
    )
    est2 = compute_school_finance_estimate(db_conn, "acara-1005", target_year=2024)
    assert est2["status"] == "benchmark_average"
    assert est2["method"] == "peer_group_average"
    assert est2["value"] == 25000

    # 4. Unknown institution -> unavailable
    est_none = compute_school_finance_estimate(
        db_conn, "inst-unknown", target_year=2024
    )
    assert est_none["status"] == "unavailable"
    assert est_none["value"] is None


@pytest.mark.unit
def test_peer_group_dispersion_reliability_controls_estimates(
    db_conn: duckdb.DuckDBPyConnection,
) -> None:
    """Test that peer group dispersion and sample size strictly control estimate generation."""
    # Seed benchmarks for 2021 and 2024
    db_conn.execute(
        """
        UPDATE institutions SET state = 'NSW' WHERE institution_id IN ('acara-1002', 'acara-1003');
        INSERT INTO school_finance_benchmarks (
            reporting_year, state_or_territory, sector, geolocation, metric, value, unit, source_dataset, source_url, retrieved_at
        ) VALUES
            (2021, 'NSW', 'Government', 'All', 'total_net_recurrent_income_per_student', 15000.0, 'AUD_per_student', 'ACARA', 'https://example.com', '2026-09-28T00:00:00Z'),
            (2024, 'NSW', 'Government', 'All', 'total_net_recurrent_income_per_student', 20000.0, 'AUD_per_student', 'ACARA', 'https://example.com', '2026-09-28T00:00:00Z');
        """
    )

    # Scenario A: Sample size below threshold (only 1 school, but min_sample_size=3)
    db_conn.execute(
        """
        DELETE FROM school_finances;
        INSERT INTO school_finances (
            institution_id, acara_id, reporting_year, total_net_recurrent_income_per_student
        ) VALUES ('acara-1001', '1001', 2021, 16500); -- ratio = 1.10
        """
    )
    est_low_n = compute_school_finance_estimate(
        db_conn, "acara-1001", target_year=2024, min_sample_size=3
    )
    assert est_low_n["status"] == "benchmark_average"
    assert est_low_n["method"] == "peer_group_average_high_dispersion_fallback"
    assert est_low_n["value"] == 20000

    # Scenario B: High dispersion (sample size = 3, but ratios vary wildly: 0.5, 1.0, 2.0 -> MAD > 0.25)
    db_conn.execute(
        """
        DELETE FROM school_finances;
        INSERT INTO school_finances (
            institution_id, acara_id, reporting_year, total_net_recurrent_income_per_student
        ) VALUES
            ('acara-1001', '1001', 2021, 7500),   -- ratio = 0.50
            ('acara-1002', '1002', 2021, 15000),  -- ratio = 1.00
            ('acara-1003', '1003', 2021, 30000);  -- ratio = 2.00
        """
    )
    est_high_mad = compute_school_finance_estimate(
        db_conn,
        "acara-1002",
        target_year=2024,
        dispersion_mad_threshold=0.25,
        min_sample_size=3,
    )
    assert est_high_mad["status"] == "benchmark_average"
    assert est_high_mad["method"] == "peer_group_average_high_dispersion_fallback"
    assert est_high_mad["value"] == 20000

    # Scenario C: Reliable peer group (sample size = 3, ratios 1.0, 1.02, 0.98 -> MAD = 0.02 <= 0.25)
    db_conn.execute(
        """
        DELETE FROM school_finances;
        INSERT INTO school_finances (
            institution_id, acara_id, reporting_year, total_net_recurrent_income_per_student
        ) VALUES
            ('acara-1001', '1001', 2021, 15000),  -- ratio = 1.00
            ('acara-1002', '1002', 2021, 15300),  -- ratio = 1.02
            ('acara-1003', '1003', 2021, 14700);  -- ratio = 0.98
        """
    )
    est_reliable = compute_school_finance_estimate(
        db_conn,
        "acara-1002",
        target_year=2024,
        dispersion_mad_threshold=0.25,
        min_sample_size=3,
    )
    assert est_reliable["status"] == "estimated_indexed"
    assert est_reliable["method"] == "acara_state_sector_geolocation_index"
    assert est_reliable["value"] == 20400  # 20000 * 1.02

    # Scenario D: Extreme individual multiplier outlier guard (multiplier > 3.0)
    db_conn.execute(
        """
        DELETE FROM school_finances;
        INSERT INTO school_finances (
            institution_id, acara_id, reporting_year, total_net_recurrent_income_per_student
        ) VALUES
            ('acara-1001', '1001', 2021, 50000),  -- ratio = 3.33 (> 3.0 outlier)
            ('acara-1002', '1002', 2021, 50000),
            ('acara-1003', '1003', 2021, 50000);
        """
    )
    # Even if group has low MAD among themselves, individual multiplier > 3.0 triggers outlier fallback
    est_outlier = compute_school_finance_estimate(
        db_conn,
        "acara-1001",
        target_year=2024,
        dispersion_mad_threshold=0.5,
        min_sample_size=3,
    )
    assert est_outlier["status"] == "benchmark_average"
    assert est_outlier["method"] == "peer_group_average_outlier_fallback"


@pytest.mark.unit
def test_resolve_institution_id_hardening(db_conn: duckdb.DuckDBPyConnection) -> None:
    """Test resolution hierarchy: ACARA ID precedence, state strictness, and ambiguity rejection."""
    # Seed institutions with duplicate school names across states
    db_conn.execute(
        """
        INSERT INTO institutions (institution_id, acara_id, school_name, state, sector)
        VALUES
            ('inst-nsw-1', '8001', 'Trinity Grammar School', 'NSW', 'Independent'),
            ('inst-vic-1', '8002', 'Trinity Grammar School', 'VIC', 'Independent'),
            ('inst-unique', '8003', 'Unique Regional High', 'QLD', 'Government');
        """
    )

    # 1. ACARA ID is highest precedence
    assert resolve_institution_id(db_conn, acara_id="8001") == "inst-nsw-1"
    assert resolve_institution_id(db_conn, acara_id="8002") == "inst-vic-1"

    # 2. Supplied state matches correct institution
    assert (
        resolve_institution_id(
            db_conn, school_name="Trinity Grammar School", state="NSW"
        )
        == "inst-nsw-1"
    )
    assert (
        resolve_institution_id(
            db_conn, school_name="Trinity Grammar School", state="VIC"
        )
        == "inst-vic-1"
    )

    # 3. Supplied state mismatch does NOT fall back to another state
    assert (
        resolve_institution_id(
            db_conn, school_name="Trinity Grammar School", state="WA"
        )
        is None
    )
    assert (
        resolve_institution_id(
            db_conn, school_name="Trinity Grammar School", state="TAS"
        )
        is None
    )

    # 4. Ambiguous name-only match (2 candidates in different states) returns None
    assert resolve_institution_id(db_conn, school_name="Trinity Grammar School") is None

    # 5. Unique name-only match succeeds
    assert (
        resolve_institution_id(db_conn, school_name="Unique Regional High")
        == "inst-unique"
    )

    # 6. Unknown school returns None
    assert resolve_institution_id(db_conn, school_name="Nonexistent School") is None


@pytest.mark.unit
def test_benchmark_hierarchy_and_unsupported_sectors(
    db_conn: duckdb.DuckDBPyConnection,
) -> None:
    """Test that unsupported sectors produce unavailable and do not fall back to All/All/All."""
    db_conn.execute(
        """
        INSERT INTO school_finance_benchmarks (
            reporting_year, state_or_territory, sector, geolocation, metric, value, unit, source_dataset, source_url, retrieved_at
        ) VALUES
            (2024, 'NSW', 'Government', 'Major Cities', 'total_net_recurrent_income_per_student', 21000.0, 'AUD_per_student', 'ACARA', 'https://example.com', '2026-09-28T00:00:00Z'),
            (2024, 'All', 'Government', 'All', 'total_net_recurrent_income_per_student', 20000.0, 'AUD_per_student', 'ACARA', 'https://example.com', '2026-09-28T00:00:00Z');

        INSERT INTO institutions (institution_id, school_name, sector, state)
        VALUES ('inst-other-sec', 'Other Sector School', 'Other', 'NSW');
        """
    )

    # Sector 'Other' or 'Tertiary' is unsupported -> produces None
    b_unsupported = get_peer_group_benchmark(
        db_conn, 2024, "NSW", "Other", "Major Cities"
    )
    assert b_unsupported is None

    # School with unsupported sector produces status 'unavailable'
    est = compute_school_finance_estimate(db_conn, "inst-other-sec", target_year=2024)
    assert est["status"] == "unavailable"
    assert est["value"] is None


@pytest.mark.unit
def test_validate_funding_records_comprehensive(
    db_conn: duckdb.DuckDBPyConnection,
) -> None:
    """Test full spectrum of data integrity assertions."""
    # 1. Clean state passes
    assert validate_funding_records(db_conn)["passed"] is True

    # 2. Negative funding value in school_public_funding
    db_conn.execute(
        """
        INSERT INTO school_public_funding (
            institution_id, reporting_year, jurisdiction, metric, value, unit, funding_model, source_dataset, source_url, retrieved_at
        ) VALUES ('acara-1001', 2024, 'NSW', 'ram_total', -100.0, 'AUD', 'RAM', 'NSW', 'url', '2026-09-28T00:00:00Z');
        """
    )
    res = validate_funding_records(db_conn)
    assert res["passed"] is False
    assert any("negative values in school_public_funding" in f for f in res["failures"])
    db_conn.execute("DELETE FROM school_public_funding;")

    # 3. Invalid reporting year (< 2000)
    db_conn.execute(
        """
        INSERT INTO school_public_funding (
            institution_id, reporting_year, jurisdiction, metric, value, unit, funding_model, source_dataset, source_url, retrieved_at
        ) VALUES ('acara-1001', 1980, 'NSW', 'ram_total', 1000.0, 'AUD', 'RAM', 'NSW', 'url', '2026-09-28T00:00:00Z');
        """
    )
    res = validate_funding_records(db_conn)
    assert res["passed"] is False
    assert any("invalid reporting years" in f for f in res["failures"])
    db_conn.execute("DELETE FROM school_public_funding;")

    # 4. Orphan institution reference (recreate without FK to test validator)
    db_conn.execute("DROP TABLE school_public_funding;")
    db_conn.execute(
        """
        CREATE TABLE school_public_funding (
            institution_id VARCHAR NOT NULL,
            reporting_year INTEGER NOT NULL,
            jurisdiction VARCHAR NOT NULL,
            metric VARCHAR NOT NULL,
            value DOUBLE NOT NULL,
            unit VARCHAR NOT NULL,
            funding_model VARCHAR NOT NULL,
            source_dataset VARCHAR NOT NULL,
            source_url VARCHAR NOT NULL,
            source_record_id VARCHAR,
            retrieved_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (institution_id, reporting_year, metric, source_dataset)
        );
        """
    )
    db_conn.execute(
        """
        INSERT INTO school_public_funding (
            institution_id, reporting_year, jurisdiction, metric, value, unit, funding_model, source_dataset, source_url, retrieved_at
        ) VALUES ('inst-ghost', 2024, 'NSW', 'ram_total', 1000.0, 'AUD', 'RAM', 'NSW', 'url', '2026-09-28T00:00:00Z');
        """
    )
    res = validate_funding_records(db_conn)
    assert res["passed"] is False
    assert any("orphan institution_id" in f for f in res["failures"])
    db_conn.execute("DROP TABLE school_public_funding;")
    db_conn.execute(
        """
        CREATE TABLE school_public_funding (
            institution_id VARCHAR NOT NULL REFERENCES institutions(institution_id),
            reporting_year INTEGER NOT NULL,
            jurisdiction VARCHAR NOT NULL,
            metric VARCHAR NOT NULL,
            value DOUBLE NOT NULL,
            unit VARCHAR NOT NULL,
            funding_model VARCHAR NOT NULL,
            source_dataset VARCHAR NOT NULL,
            source_url VARCHAR NOT NULL,
            source_record_id VARCHAR,
            retrieved_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (institution_id, reporting_year, metric, source_dataset)
        );
        """
    )


@pytest.mark.integration
def test_clean_db_run_all_with_funding_tables(
    tmp_path: Path,
    funding_inputs: dict[str, Path],
) -> None:
    """Integration test proving clean run-all populates funding tables and passes strict validation."""
    db_path = tmp_path / "clean_run_all.duckdb"

    # Run the ingestion stages on a fresh database:
    # Ingest ACARA institutions, then public funding & benchmarks, then validate
    conn = duckdb.connect(str(db_path))
    init_schema(conn)

    # Seed test institution corresponding to NSW RAM reference data
    conn.execute(
        """
        INSERT INTO institutions (
            institution_id, acara_id, school_name, school_type, sector, campus_type, state, suburb, postcode
        ) VALUES
            ('acara-1001', '1001', 'Sydney Test High', 'Secondary', 'Government', 'School Single Entity', 'NSW', 'Sydney', '2000');
        """
    )

    # Ingest reference funding and benchmarks
    counts = ingest_all_funding(conn, **funding_inputs)
    assert counts["acara_benchmarks"] == 1
    assert counts["nsw_ram"] == 4

    # Ensure tables are non-empty
    bench_row = conn.execute(
        "SELECT count(*) FROM school_finance_benchmarks"
    ).fetchone()
    assert bench_row is not None
    assert bench_row[0] > 0

    fund_row = conn.execute("SELECT count(*) FROM school_public_funding").fetchone()
    assert fund_row is not None
    assert fund_row[0] > 0

    # Validate funding records directly
    fund_val = validate_funding_records(conn)
    assert fund_val["passed"] is True, fund_val["failures"]

    conn.close()


@pytest.mark.unit
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


@pytest.mark.integration
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


@pytest.mark.unit
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


@pytest.mark.integration
def test_ingest_all_funding_orchestration(
    db_conn: duckdb.DuckDBPyConnection,
    funding_inputs: dict[str, Path],
) -> None:
    """Assert ingest_all_funding coordinates all ingestion steps."""
    counts = ingest_all_funding(db_conn, **funding_inputs)
    assert counts == {
        "acara_benchmarks": 1,
        "nsw_ram": 4,
        "tasmania_srp": 3,
        "nt_funding": 2,
        "qld_grants": 3,
        "manual_enrichment": 1,
    }
    assert db_conn.execute("SELECT count(*) FROM school_public_funding").fetchone() == (
        13,
    )
    assert validate_funding_records(db_conn)["passed"] is True
    # All six sources must remain idempotent on a second orchestration run.
    assert ingest_all_funding(db_conn, **funding_inputs) == counts
    assert db_conn.execute("SELECT count(*) FROM school_public_funding").fetchone() == (
        13,
    )
