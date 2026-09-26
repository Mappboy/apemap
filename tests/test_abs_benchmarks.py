"""Tests for official ABS Schools statistical benchmark ingestion and analytical exposure."""

from __future__ import annotations

from pathlib import Path

import pytest

from apemap.analysis import compute_sector_benchmarks, compute_sector_summary
from apemap.db import get_connection, init_schema
from apemap.ingest.abs import ingest_abs_benchmarks, run_abs_ingestion


def test_abs_benchmark_rows_and_shares(tmp_path: Path) -> None:
    """ABS benchmark rows are exactly Government, Catholic, Independent with correct shares summing to 1.0."""
    db_path = tmp_path / "test_abs.duckdb"
    conn = get_connection(db_path)
    init_schema(conn)

    count = ingest_abs_benchmarks(conn, 2025)
    assert count == 3

    rows = conn.execute(
        """
        SELECT
            sector,
            student_enrolment_share,
            student_enrolments,
            total_student_enrolments,
            source_title,
            source_url,
            released_at
        FROM education_sector_benchmarks
        WHERE benchmark_year = 2025
        ORDER BY sector
        """
    ).fetchall()

    assert len(rows) == 3
    bench_dict = {r[0]: r for r in rows}

    # Verify sectors are exactly Government, Catholic, Independent
    assert set(bench_dict.keys()) == {"Government", "Catholic", "Independent"}

    # Verify enrolments and proportions
    gov = bench_dict["Government"]
    assert gov[1] == 0.628
    assert gov[2] == 2_613_404
    assert gov[3] == 4_160_918

    cath = bench_dict["Catholic"]
    assert cath[1] == 0.200
    assert cath[2] == 831_692
    assert cath[3] == 4_160_918

    ind = bench_dict["Independent"]
    assert ind[1] == 0.172
    assert ind[2] == 715_822
    assert ind[3] == 4_160_918

    # Verify shares sum to 1.0
    total_share = sum(r[1] for r in rows)
    assert pytest.approx(total_share, rel=1e-5) == 1.0

    # Verify source metadata
    for r in rows:
        assert r[4] == "Schools, 2025"
        assert "abs.gov.au/statistics/people/education/schools/2025" in r[5]
        assert r[6] is not None

    conn.close()


def test_abs_ingestion_idempotence(tmp_path: Path) -> None:
    """Ingesting ABS benchmarks multiple times is deterministic and maintains primary key constraint."""
    db_path = tmp_path / "test_idempotent.duckdb"
    conn = get_connection(db_path)
    init_schema(conn)

    ingest_abs_benchmarks(conn, 2025)
    ingest_abs_benchmarks(conn, 2025)

    row = conn.execute("SELECT count(*) FROM education_sector_benchmarks").fetchone()
    assert row is not None
    assert row[0] == 3
    conn.close()


def test_abs_benchmark_comparison_analysis(tmp_path: Path) -> None:
    """Analysis functions return the authoritative comparison fields with correct domain naming."""
    db_path = tmp_path / "test_analysis_bench.duckdb"
    conn = get_connection(db_path)
    init_schema(conn)
    ingest_abs_benchmarks(conn, 2025)

    # Seed 10 members: 6 Government, 2 Catholic, 2 Independent
    for i in range(10):
        m_id = f"m-{i}"
        conn.execute(
            """
            INSERT INTO members (member_id, family_name, given_name, display_name)
            VALUES (?, 'Test', 'MP', ?)
            """,
            [m_id, f"MP {i}"],
        )
        conn.execute(
            """
            INSERT INTO parliament_service (
                service_id, member_id, parliament_number, chamber, party,
                party_abbrev, state_or_territory, is_opening_day_member
            ) VALUES (?, ?, 48, 'representatives', 'Labor', 'ALP', 'NSW', TRUE)
            """,
            [f"srv-{i}", m_id],
        )

        if i < 6:
            sec = "Government"
        elif i < 8:
            sec = "Catholic"
        else:
            sec = "Independent"

        inst_id = f"inst-{sec.lower()}"
        conn.execute(
            """
            INSERT INTO institutions (institution_id, school_name, sector)
            VALUES (?, ?, ?)
            ON CONFLICT (institution_id) DO NOTHING
            """,
            [inst_id, f"{sec} High", sec],
        )
        conn.execute(
            """
            INSERT INTO member_education (
                education_id, member_id, institution_id, level, attended_status,
                source_url, retrieved_at, confidence
            ) VALUES (?, ?, ?, 'secondary', 'graduated', 'test', '2025-01-01 00:00:00+00', 'verified')
            """,
            [f"edu-{i}", m_id, inst_id],
        )

    bench = compute_sector_benchmarks(conn, 48, 2025)
    assert set(bench.keys()) == {"Government", "Catholic", "Independent"}

    gov = bench["Government"]
    assert gov["parliamentary_share"] == 0.6  # 6 / 10
    assert gov["student_enrolment_share"] == 0.628
    assert gov["difference_percentage_points"] == -2.8  # (0.6 - 0.628) * 100
    assert gov["benchmark_year"] == 2025
    assert gov["benchmark_source"] == "Schools, 2025"

    summary = compute_sector_summary(conn, 48)
    assert "benchmark_comparison" in summary
    assert summary["benchmark_comparison"]["Catholic"]["parliamentary_share"] == 0.2

    # Lock down domain terminology: no misleading population_share field in output
    for sector_data in bench.values():
        assert "population_share" not in sector_data
        assert "student_enrolment_share" in sector_data

    conn.close()


def test_run_abs_ingestion_pipeline(tmp_path: Path) -> None:
    """run_abs_ingestion executes end-to-end and produces canonical Parquet export."""
    db_path = tmp_path / "test_pipeline.duckdb"
    out_dir = tmp_path / "processed"

    results = run_abs_ingestion(
        db_path=db_path,
        export_parquet_files=True,
        output_dir=out_dir,
    )

    assert results["benchmarks_loaded"] == 3
    assert results["benchmark_year"] == 2025
    assert results["parquet_exported"] is True

    parquet_file = out_dir / "education_sector_benchmarks.parquet"
    assert parquet_file.exists()
