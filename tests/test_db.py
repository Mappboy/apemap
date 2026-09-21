"""Tests for DuckDB canonical schema, parameterized views, and database builder."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pytest

from apemap.db import (
    CANONICAL_TABLES,
    build_database,
    export_to_parquet,
    get_connection,
    get_members_by_parliament,
    get_secondary_education_by_parliament,
    init_schema,
)


@pytest.fixture
def db_conn() -> duckdb.DuckDBPyConnection:
    """Fixture providing an in-memory DuckDB connection with canonical schema."""
    conn = get_connection()
    init_schema(conn)
    return conn


def test_canonical_tables_exist(db_conn: duckdb.DuckDBPyConnection) -> None:
    """Verify all five canonical tables are created and queryable."""
    for table in CANONICAL_TABLES:
        result = db_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        assert result is not None
        assert result[0] == 0


def test_views_and_macros_exist(db_conn: duckdb.DuckDBPyConnection) -> None:
    """Verify canonical views and backwards-compatibility views exist."""
    views = [
        "v_parliament_members",
        "v_member_secondary_education",
        "member_aph_46",
        "member_aph_47",
        "member_secondary_school_education_46",
        "member_secondary_school_education_47",
    ]
    for view in views:
        result = db_conn.execute(f"SELECT COUNT(*) FROM {view}").fetchone()
        assert result is not None
        assert result[0] == 0


def test_check_constraints_enforced(db_conn: duckdb.DuckDBPyConnection) -> None:
    """Verify CHECK constraints on chamber, level, attended_status, and confidence."""
    # 1. Chamber check
    db_conn.execute(
        """
        INSERT INTO members (member_id, family_name, given_name, display_name)
        VALUES ('mem-1', 'Smith', 'John', 'John Smith');
        """
    )
    with pytest.raises(duckdb.ConstraintException):
        db_conn.execute(
            """
            INSERT INTO parliament_service (
                service_id, member_id, parliament_number, chamber,
                party, party_abbrev, state_or_territory
            ) VALUES (
                'srv-1', 'mem-1', 47, 'invalid_chamber', 'Labor', 'ALP', 'NSW'
            );
            """
        )

    # 2. Institution sector check
    with pytest.raises(duckdb.ConstraintException):
        db_conn.execute(
            """
            INSERT INTO institutions (institution_id, school_name, sector)
            VALUES ('inst-1', 'Invalid School', 'Private_Invalid');
            """
        )

    db_conn.execute(
        """
        INSERT INTO institutions (institution_id, school_name, sector)
        VALUES ('inst-1', 'Valid High School', 'Government');
        """
    )

    # 3. Education level check
    with pytest.raises(duckdb.ConstraintException):
        db_conn.execute(
            """
            INSERT INTO member_education (
                education_id, member_id, institution_id, level, attended_status,
                source_url, retrieved_at, confidence
            ) VALUES (
                'edu-1', 'mem-1', 'inst-1', 'primary', 'graduated',
                'https://example.com', CURRENT_TIMESTAMP, 'verified'
            );
            """
        )

    # 4. Attended status check
    with pytest.raises(duckdb.ConstraintException):
        db_conn.execute(
            """
            INSERT INTO member_education (
                education_id, member_id, institution_id, level, attended_status,
                source_url, retrieved_at, confidence
            ) VALUES (
                'edu-1', 'mem-1', 'inst-1', 'secondary', 'dropped_out',
                'https://example.com', CURRENT_TIMESTAMP, 'verified'
            );
            """
        )

    # 5. Provenance confidence check
    with pytest.raises(duckdb.ConstraintException):
        db_conn.execute(
            """
            INSERT INTO member_education (
                education_id, member_id, institution_id, level, attended_status,
                source_url, retrieved_at, confidence
            ) VALUES (
                'edu-1', 'mem-1', 'inst-1', 'secondary', 'graduated',
                'https://example.com', CURRENT_TIMESTAMP, 'high'
            );
            """
        )


def test_audit_provenance_fields_and_data_flow(
    db_conn: duckdb.DuckDBPyConnection,
) -> None:
    """Verify education assertions capture and preserve provenance fields."""
    retrieved_time = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)

    # Insert test data
    db_conn.execute(
        """
        INSERT INTO members (
            member_id, family_name, given_name, display_name,
            aph_id, wikidata_id
        ) VALUES (
            'alb-1', 'Albanese', 'Anthony', 'Anthony Albanese',
            'R36', 'Q4772000'
        );
        """
    )
    db_conn.execute(
        """
        INSERT INTO parliament_service (
            service_id, member_id, parliament_number, chamber,
            party, party_abbrev, electorate, state_or_territory,
            is_current_member
        ) VALUES
        ('srv-alb-46', 'alb-1', 46, 'representatives',
         'Australian Labor Party', 'ALP', 'Grayndler', 'NSW', false),
        ('srv-alb-47', 'alb-1', 47, 'representatives',
         'Australian Labor Party', 'ALP', 'Grayndler', 'NSW', true);
        """
    )
    db_conn.execute(
        """
        INSERT INTO institutions (
            institution_id, acara_id, school_name, school_type,
            sector, suburb, state
        ) VALUES (
            'sch-st-marys', '40001', 'St Mary’s Cathedral College',
            'Secondary', 'Catholic', 'Sydney', 'NSW'
        );
        """
    )
    db_conn.execute(
        """
        INSERT INTO member_education (
            education_id, member_id, institution_id, level,
            graduation_year, attended_status, source_url,
            retrieved_at, confidence, reviewer_notes
        ) VALUES (
            'edu-alb-1', 'alb-1', 'sch-st-marys', 'secondary', 1980, 'graduated',
            'https://handbookapi.aph.gov.au/api/individuals/R36', ?, 'verified',
            'Cross-checked with handbook biography'
        );
        """,
        [retrieved_time],
    )
    db_conn.execute(
        """
        INSERT INTO school_snapshots (
            institution_id, snapshot_year, total_enrolments, icsea,
            financial_profile_2021
        ) VALUES (
            'sch-st-marys', 2021, 800, 1100, '{"recurrent_income": 15000000}'
        );
        """
    )

    # Query using parameterized helper
    df_46 = get_members_by_parliament(db_conn, 46)
    assert len(df_46) == 1
    assert df_46.iloc[0]["display_name"] == "Anthony Albanese"
    assert df_46.iloc[0]["parliament_number"] == 46

    df_47 = get_members_by_parliament(db_conn, 47)
    assert len(df_47) == 1
    assert bool(df_47.iloc[0]["is_current_member"]) is True

    # Check non-existent parliament
    df_48 = get_members_by_parliament(db_conn, 48)
    assert len(df_48) == 0

    # Query secondary education via macro
    edu_47 = get_secondary_education_by_parliament(db_conn, 47)
    assert len(edu_47) == 1
    row = edu_47.iloc[0]
    assert row["display_name"] == "Anthony Albanese"
    assert row["school_name"] == "St Mary’s Cathedral College"
    assert row["school_sector"] == "Catholic"
    assert row["graduation_year"] == 1980
    assert row["attended_status"] == "graduated"
    assert row["confidence"] == "verified"
    assert row["reviewer_notes"] == "Cross-checked with handbook biography"
    assert row["source_url"] == "https://handbookapi.aph.gov.au/api/individuals/R36"

    # Verify compatibility views
    res_46 = db_conn.execute("SELECT * FROM member_secondary_school_education_46").df()
    assert len(res_46) == 1
    assert res_46.iloc[0]["school_name"] == "St Mary’s Cathedral College"


def test_parquet_export_and_build(
    db_conn: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    """Verify exporting database to Parquet and rebuilding into a new database."""
    # Seed minimal data
    db_conn.execute(
        """
        INSERT INTO members (member_id, family_name, given_name, display_name)
        VALUES ('mem-bandt', 'Bandt', 'Adam', 'Adam Bandt');
        """
    )
    db_conn.execute(
        """
        INSERT INTO parliament_service (
            service_id, member_id, parliament_number, chamber,
            party, party_abbrev, state_or_territory
        ) VALUES (
            'srv-bandt-47', 'mem-bandt', 47, 'representatives',
            'Australian Greens', 'GRN', 'VIC'
        );
        """
    )
    db_conn.execute(
        """
        INSERT INTO institutions (institution_id, school_name, sector)
        VALUES ('sch-modbury', 'Modbury High School', 'Government');
        """
    )
    db_conn.execute(
        """
        INSERT INTO member_education (
            education_id, member_id, institution_id, level, attended_status,
            source_url, retrieved_at, confidence
        ) VALUES (
            'edu-bandt', 'mem-bandt', 'sch-modbury', 'secondary', 'graduated',
            'https://handbookapi.aph.gov.au/api/individuals/M3C',
            CURRENT_TIMESTAMP, 'verified'
        );
        """
    )
    db_conn.execute(
        """
        INSERT INTO school_snapshots (
            institution_id, snapshot_year, total_enrolments, icsea
        ) VALUES ('sch-modbury', 2021, 750, 990);
        """
    )

    parquet_dir = tmp_path / "parquet"
    exported = export_to_parquet(db_conn, parquet_dir)

    for table in CANONICAL_TABLES:
        assert table in exported
        assert exported[table].exists()
        assert exported[table].stat().st_size > 0

    # Rebuild from Parquet in a brand new database
    new_db_file = tmp_path / "rebuilt.duckdb"
    new_conn = build_database(db_path=new_db_file, parquet_dir=parquet_dir)

    # Verify rows in new database
    row = new_conn.execute("SELECT COUNT(*) FROM members").fetchone()
    assert row is not None
    mem_count = row[0]
    assert mem_count == 1

    edu_df = get_secondary_education_by_parliament(new_conn, 47)
    assert len(edu_df) == 1
    assert edu_df.iloc[0]["display_name"] == "Adam Bandt"
    assert edu_df.iloc[0]["school_name"] == "Modbury High School"

    new_conn.close()
