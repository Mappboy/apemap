"""Tests for web release contract: results-summary.json, schools.geojson, downloads, and manifest.json."""

from __future__ import annotations

import hashlib
import csv
import json
from pathlib import Path
import subprocess

import duckdb
import pytest

from tests.db_fixtures import DatabaseFactory, build_template

from apemap.db import ensure_spatial
from apemap.export import (
    export_all_artifacts,
    export_research_downloads,
    export_results_summary,
    export_web_release_bundle,
    export_web_release_manifest,
    export_web_schools_geojson,
)
from apemap.constants import PROJECT_ROOT


def _csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def seed_web_db(conn: duckdb.DuckDBPyConnection) -> None:
    """Seed the domain fixture once inside the template transaction."""
    ensure_spatial(conn)
    # 1. Institutions
    conn.execute(
        """
        INSERT INTO institutions (
            institution_id, school_name, sector, school_type, campus_type,
            state, suburb, postcode, longitude, latitude, acara_id
        ) VALUES
        ('inst-gov', 'Sydney Boys High', 'Government', 'Secondary', 'School Single', 'NSW', 'Surry Hills', '2010', 151.216, -33.892, '40001'),
        ('inst-cath', 'St Aloysius College', 'Catholic', 'Combined', 'School Single', 'NSW', 'Milsons Point', '2061', 151.213, -33.849, '40002'),
        ('inst-ind', 'The Kings School', 'Independent', 'Combined', 'School Single', 'NSW', 'North Parramatta', '2151', 151.011, -33.791, '40003'),
        ('inst-unmapped', 'Unmapped Academy', 'Government', 'Secondary', 'School Single', 'VIC', 'Melbourne', '3000', NULL, NULL, '40004')
        """
    )

    # 2. School Snapshots (Priority A + B fields)
    conn.execute(
        """
        INSERT INTO school_snapshots (
            institution_id, snapshot_year, total_enrolments,
            girls_enrolments, boys_enrolments, fte_enrolments,
            icsea, icsea_percentile,
            sea_bottom_quarter_pct, sea_lower_middle_quarter_pct,
            sea_upper_middle_quarter_pct, sea_top_quarter_pct,
            indigenous_enrolments_pct, lbote_pct,
            year_range, remoteness_category
        ) VALUES
        ('inst-gov', 2025, 1200, 0, 1200, 1195.0, 1180, 95, 2.0, 8.0, 20.0, 70.0, 1.0, 75.0, '7-12', 'Major Cities'),
        ('inst-cath', 2025, 1100, 0, 1100, 1090.0, 1170, 94, 1.0, 5.0, 24.0, 70.0, 0.5, 30.0, '3-12', 'Major Cities'),
        ('inst-ind', 2025, 1800, 0, 1800, 1780.0, 1160, 92, 3.0, 7.0, 25.0, 65.0, 2.0, 25.0, 'K-12', 'Major Cities')
        """
    )

    # 3. School Finances (for 2021/2024 finance estimates)
    conn.execute(
        """
        INSERT INTO school_finances (
            institution_id, acara_id, reporting_year, total_net_recurrent_income_per_student,
            total_gross_income_total, source_dataset
        ) VALUES
        ('inst-gov', '40001', 2021, 16500, 19800000, 'ACARA My School Finance 2021'),
        ('inst-cath', '40002', 2021, 22000, 24200000, 'ACARA My School Finance 2021'),
        ('inst-ind', '40003', 2021, 35000, 63000000, 'ACARA My School Finance 2021')
        """
    )

    # 4. Members and Service
    members = [
        ("m-1", "Albo", "Anthony", "Anthony Albanese", "Male"),
        ("m-2", "Dutton", "Peter", "Peter Dutton", "Male"),
        ("m-3", "Bandt", "Adam", "Adam Bandt", "Male"),
        ("m-4", "Both", "Member", "Both Member", "Other"),
        ("m-5", "Unknown", "Member", "Unknown Member", "Female"),
        ("m-6", "Unmapped", "Member", "Unmapped Member", "Female"),
    ]
    for mid, fam, giv, disp, gen in members:
        conn.execute(
            """
            INSERT INTO members (member_id, family_name, given_name, display_name, gender, aph_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [mid, fam, giv, disp, gen, f"aph-{mid}"],
        )
        conn.execute(
            """
            INSERT INTO parliament_service (
                service_id, member_id, parliament_number, chamber, party,
                party_abbrev, state_or_territory, is_opening_day_member, is_current_member
            ) VALUES (?, ?, 47, 'representatives', 'Labor', 'ALP', 'NSW', TRUE, TRUE)
            """,
            [f"srv-{mid}", mid],
        )

    # 5. Member Education
    # m-1 attended Gov
    # m-2 attended Cath
    # m-3 attended Ind
    # m-4 attended Gov AND Ind (mixed / non-government + government)
    # m-5 has no secondary education
    # m-6 attended unmapped school
    edu = [
        ("edu-1", "m-1", "inst-gov"),
        ("edu-2", "m-2", "inst-cath"),
        ("edu-3", "m-3", "inst-ind"),
        ("edu-4a", "m-4", "inst-gov"),
        ("edu-4b", "m-4", "inst-ind"),
        ("edu-6", "m-6", "inst-unmapped"),
    ]
    for eid, mid, iid in edu:
        conn.execute(
            """
            INSERT INTO member_education (
                education_id, member_id, institution_id, level,
                attended_status, source_url, retrieved_at, confidence
            ) VALUES (?, ?, ?, 'secondary', 'graduated', 'https://example.com', '2025-01-01 00:00:00+00', 'verified')
            """,
            [eid, mid, iid],
        )

    # 6. ABS sector benchmarks
    conn.execute(
        """
        INSERT INTO education_sector_benchmarks (
            benchmark_year, sector, student_enrolment_share,
            student_enrolments, total_student_enrolments,
            source_title, source_url, released_at
        ) VALUES
        (2025, 'Government', 0.628, 2613404, 4160918, 'Schools, 2025', 'https://abs.gov.au', '2026-02-18 00:00:00+00'),
        (2025, 'Catholic', 0.200, 832183, 4160918, 'Schools, 2025', 'https://abs.gov.au', '2026-02-18 00:00:00+00'),
        (2025, 'Independent', 0.172, 715331, 4160918, 'Schools, 2025', 'https://abs.gov.au', '2026-02-18 00:00:00+00')
        """
    )


@pytest.fixture(scope="session")
def web_db_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build a closed, immutable seeded template once per test session."""
    return build_template(
        tmp_path_factory.mktemp("web_db_template") / "seed.duckdb", seed_web_db
    )


@pytest.fixture
def web_contract_db(
    web_db_template: Path,
    database_factory: DatabaseFactory,
) -> tuple[Path, duckdb.DuckDBPyConnection]:
    """Give every test a separate database and finalized connection."""
    return database_factory(web_db_template)


@pytest.mark.integration
def test_export_results_summary(
    web_contract_db: tuple[Path, duckdb.DuckDBPyConnection],
    tmp_path: Path,
) -> None:
    """Verify results-summary.json matches the web release contract schema and metrics."""
    _db_path, conn = web_contract_db
    out_dir = tmp_path / "web_release"

    summary_file = export_results_summary(conn, output_dir=out_dir, parliaments=[47])
    assert summary_file.exists()
    assert summary_file.name == "results-summary.json"

    data = json.loads(summary_file.read_text(encoding="utf-8"))
    assert data["web_schema_version"] == "1.0.0"
    assert data["cohort"] == "opening_day"
    assert "47" in data["parliaments"]

    p47 = data["parliaments"]["47"]
    assert p47["parliament_number"] == 47
    assert p47["total_parliamentarians"] == 6
    # 5 parliamentarians have known schools (m-1, m-2, m-3, m-4, m-6)
    assert p47["known_education_count"] == 5
    assert p47["missing_education_count"] == 1
    assert p47["known_school_denominator"] == 5

    # Government vs non-government
    gov_non_gov = p47["government_non_government"]
    assert gov_non_gov["government_only"] == 2  # m-1, m-6
    assert gov_non_gov["non_government_only"] == 2  # m-2, m-3
    assert gov_non_gov["mixed"] == 1  # m-4 (Gov + Ind)

    pcts = p47["government_non_government_percentages"]
    assert pcts["government_only"] == 40.0
    assert pcts["non_government_only"] == 40.0
    assert pcts["mixed"] == 20.0

    # Represented schools
    # Total schools represented by opening day members in P47: inst-gov, inst-cath, inst-ind, inst-unmapped (4)
    assert p47["number_of_represented_schools"] == 4
    # Mapped vs unmapped
    assert p47["mapped_schools_count"] == 3
    assert p47["unmapped_schools_count"] == 1


@pytest.mark.integration
def test_export_web_schools_geojson(
    web_contract_db: tuple[Path, duckdb.DuckDBPyConnection],
    tmp_path: Path,
) -> None:
    """Verify schools.geojson deduplicates institutions and enriches with attendances and snapshot metrics."""
    _db_path, conn = web_contract_db
    out_dir = tmp_path / "web_release"

    geojson_file = export_web_schools_geojson(
        conn, output_dir=out_dir, parliaments=[47]
    )
    assert geojson_file.exists()
    assert geojson_file.name == "schools.geojson"

    data = json.loads(geojson_file.read_text(encoding="utf-8"))
    assert data["type"] == "FeatureCollection"
    assert data["name"] == "schools"

    # Only mapped schools (inst-gov, inst-cath, inst-ind) are exported to schools.geojson
    features = data["features"]
    assert len(features) == 3

    features_by_id = {f["properties"]["institution_id"]: f for f in features}
    assert "inst-gov" in features_by_id
    assert "inst-cath" in features_by_id
    assert "inst-ind" in features_by_id

    # Verify Gov school attributes
    gov_feat = features_by_id["inst-gov"]
    props = gov_feat["properties"]
    assert props["school_name"] == "Sydney Boys High"
    assert props["school_sector"] == "Government"
    assert props["government_non_government"] == "Government"
    assert props["icsea"] == 1180
    assert props["icsea_percentile"] == 95
    assert props["total_enrolments"] == 1200
    assert props["remoteness_category"] == "Major Cities"
    assert props["year_range"] == "7-12"
    assert props["sea_top_quarter_pct"] == 70.0
    assert props["indigenous_enrolments_pct"] == 1.0

    # Verify attendances: both m-1 and m-4 attended inst-gov
    assert props["member_count"] == 2
    attendee_ids = {m["member_id"] for m in props["members"]}
    assert attendee_ids == {"m-1", "m-4"}
    assert props["parliaments"] == [47]


@pytest.mark.integration
def test_export_research_downloads(
    web_contract_db: tuple[Path, duckdb.DuckDBPyConnection],
    tmp_path: Path,
) -> None:
    """Verify research download files: parliament-education.csv and school-profiles.parquet."""
    _db_path, conn = web_contract_db
    out_dir = tmp_path / "web_release"

    downloads = export_research_downloads(conn, output_dir=out_dir, parliaments=[47])
    csv_file = downloads["parliament-education.csv"]
    parquet_file = downloads["school-profiles.parquet"]

    assert csv_file.exists()
    assert parquet_file.exists()

    csv_lines = csv_file.read_text(encoding="utf-8").splitlines()
    assert len(csv_lines) > 1  # Header + rows
    header = csv_lines[0].split(",")
    assert "member_id" in header
    assert "school_name" in header
    assert "parliament_number" in header

    # Inspect parquet file via DuckDB
    parquet_rows = conn.execute(
        f"SELECT COUNT(*) FROM '{parquet_file.as_posix()}'"
    ).fetchone()
    assert parquet_rows is not None
    assert parquet_rows[0] == 3  # 3 snapshots inserted in fixture


@pytest.mark.integration
def test_web_exports_reconcile_cohort_and_attendance_grain(
    web_contract_db: tuple[Path, duckdb.DuckDBPyConnection], tmp_path: Path
) -> None:
    """Later services and extra profile years cannot multiply published attendances."""
    _, conn = web_contract_db
    conn.execute(
        """
        INSERT INTO school_snapshots (institution_id, snapshot_year, icsea)
        VALUES ('inst-gov', 2022, 1000);
        INSERT INTO parliament_service (
            service_id, member_id, parliament_number, chamber, party,
            party_abbrev, state_or_territory, is_opening_day_member
        ) VALUES ('later-service', 'm-5', 47, 'senate', 'Independent', 'IND', 'NSW', FALSE);
        INSERT INTO member_education (
            education_id, member_id, institution_id, level, attended_status,
            source_url, retrieved_at, confidence
        ) VALUES ('later-education', 'm-5', 'inst-gov', 'secondary', 'graduated',
                  'https://example.com', '2025-01-01', 'verified');
        UPDATE parliament_service SET is_opening_day_member = FALSE WHERE member_id = 'm-5';
        """
    )
    bundle = export_web_release_bundle(conn, tmp_path / "release", [47])
    summary = json.loads(Path(bundle["results_summary"]).read_text())["parliaments"][
        "47"
    ]
    geojson = json.loads(Path(bundle["schools_geojson"]).read_text())
    rows = _csv_rows(Path(bundle["research_downloads"]["parliament-education.csv"]))
    assert len(rows) == 6
    assert len({(r["education_id"], r["service_id"]) for r in rows}) == len(rows)
    assert {r["member_id"] for r in rows} == {"m-1", "m-2", "m-3", "m-4", "m-6"}
    assert all(r["is_opening_day_member"].lower() == "true" for r in rows)
    assert {r["snapshot_year"] for r in rows if r["institution_id"] == "inst-gov"} == {
        "2025"
    }
    assert summary["known_education_count"] == len({r["member_id"] for r in rows})
    assert summary["number_of_represented_schools"] == len(
        {r["institution_id"] for r in rows}
    )
    assert summary["mapped_schools_count"] == len(geojson["features"])
    assert summary["unmapped_schools_count"] == 1
    assert all(
        m["member_id"] != "m-5"
        for f in geojson["features"]
        for m in f["properties"]["members"]
    )


@pytest.mark.integration
def test_school_members_keep_service_context_across_parliaments(
    web_contract_db: tuple[Path, duckdb.DuckDBPyConnection], tmp_path: Path
) -> None:
    """A member changes party/chamber while remaining one attendee of a school."""
    _, conn = web_contract_db
    conn.execute(
        """
        INSERT INTO parliament_service (
            service_id, member_id, parliament_number, chamber, party,
            party_abbrev, state_or_territory, is_opening_day_member
        ) VALUES ('srv-m-1-48', 'm-1', 48, 'senate', 'Independent', 'IND', 'NSW', TRUE);
        INSERT INTO member_education (
            education_id, member_id, institution_id, level, attended_status,
            source_url, retrieved_at, confidence
        ) VALUES ('edu-1-second', 'm-1', 'inst-gov', 'secondary', 'attended_unspecified',
                  'https://example.com/second', '2025-01-01', 'verified');
        """
    )
    path = export_web_schools_geojson(conn, tmp_path, [48, 47, 48])
    features = json.loads(path.read_text())["features"]
    school = next(
        f["properties"]
        for f in features
        if f["properties"]["institution_id"] == "inst-gov"
    )
    assert school["member_count"] == 2
    member = next(m for m in school["members"] if m["member_id"] == "m-1")
    assert member["parliaments"] == [47, 48]
    assert [
        (s["parliament_number"], s["party"], s["party_abbrev"], s["chamber"])
        for s in member["services"]
    ] == [(47, "Labor", "ALP", "representatives"), (48, "Independent", "IND", "senate")]
    assert [s["service_id"] for s in member["services"]] == ["srv-m-1", "srv-m-1-48"]
    assert "party" not in member
    assert "chamber" not in member
    downloads = export_research_downloads(conn, tmp_path, [48, 47, 48])
    rows = [
        r
        for r in _csv_rows(downloads["parliament-education.csv"])
        if r["member_id"] == "m-1"
    ]
    assert len(rows) == 4  # Two education assertions across two services.
    assert {(r["parliament_number"], r["party"], r["chamber"]) for r in rows} == {
        ("47", "Labor", "representatives"),
        ("48", "Independent", "senate"),
    }


@pytest.mark.integration
@pytest.mark.parametrize("profile_years", [(2022, 2024), (2024, 2024), (None, None)])
def test_summary_source_years_match_selected_profiles(
    web_contract_db: tuple[Path, duckdb.DuckDBPyConnection],
    tmp_path: Path,
    profile_years: tuple[int | None, int | None],
) -> None:
    """Report latest represented profiles, excluding older and unrelated snapshots."""
    _, conn = web_contract_db
    conn.execute("DELETE FROM school_snapshots")
    for iid, year in zip(("inst-gov", "inst-cath"), profile_years):
        if year is not None:
            conn.execute(
                "INSERT INTO school_snapshots (institution_id, snapshot_year) VALUES (?, ?), (?, ?)",
                [iid, year, iid, year - 1],
            )
    conn.execute(
        "INSERT INTO institutions (institution_id, school_name, sector) VALUES ('unrelated', 'Unrelated', 'Government')"
    )
    conn.execute(
        "INSERT INTO school_snapshots (institution_id, snapshot_year) VALUES ('unrelated', 2025)"
    )
    conn.execute("DELETE FROM education_sector_benchmarks")
    summary_path = export_results_summary(conn, tmp_path, [47])
    years = json.loads(summary_path.read_text())["parliaments"]["47"]["source_years"]
    expected = sorted({y for y in profile_years if y is not None})
    assert years["profile_years"] == expected
    assert years["profile_year"] == (expected[0] if len(expected) == 1 else None)
    assert years["abs_benchmark_year"] is None
    geo_path = export_web_schools_geojson(conn, tmp_path, [47])
    map_years = sorted(
        {
            f["properties"]["profile_year"]
            for f in json.loads(geo_path.read_text())["features"]
            if f["properties"]["profile_year"] is not None
        }
    )
    assert map_years == expected


@pytest.mark.integration
@pytest.mark.parametrize("has_source_checkout", [True, False])
def test_manifest_commit_is_independent_of_callers_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    has_source_checkout: bool,
) -> None:
    """Generating a release from another repository must identify APEMAP's commit."""
    caller = tmp_path / "other-repo"
    caller.mkdir()
    subprocess.run(["git", "init", str(caller)], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(caller),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "--allow-empty",
            "-m",
            "Unrelated",
        ],
        check=True,
        capture_output=True,
    )
    expected = subprocess.run(
        ["git", "-C", str(PROJECT_ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not has_source_checkout:
        # A package installed below another repository must not inherit its HEAD.
        package_root = caller / "installed-package"
        package_root.mkdir()
        monkeypatch.setattr("apemap.export.PROJECT_ROOT", package_root)
        expected = "unknown"
    monkeypatch.chdir(caller)
    manifest = export_web_release_manifest(output_dir=tmp_path, parliaments=[47])
    assert json.loads(manifest.read_text())["source_commit"] == expected


@pytest.mark.integration
def test_bundle_preserves_explicit_provenance_and_reproducibility(
    web_contract_db: tuple[Path, duckdb.DuckDBPyConnection],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Packaged builds can supply a commit and actual upstream snapshot dates."""
    _, conn = web_contract_db
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1780000000")
    dates = {"aph": "2026-09-28", "acara": "2026-09-29"}
    manifests = []
    for name, parliaments in (("first", [48, 47, 48]), ("second", [47, 48])):
        bundle = export_web_release_bundle(
            conn,
            tmp_path / name,
            parliaments,
            data_release_version="2026.09.30",
            source_commit="a" * 40,
            source_snapshot_dates=dates,
        )
        manifests.append(Path(bundle["manifest"]).read_bytes())
    assert manifests[0] == manifests[1]
    manifest = json.loads(manifests[0])
    assert manifest["source_commit"] == "a" * 40
    assert manifest["data_release_version"] == "2026.09.30"
    assert manifest["source_snapshot_dates"] == {
        "aph": "2026-09-28",
        "acara": "2026-09-29",
        "abs": None,
        "aec": None,
    }


@pytest.mark.integration
def test_export_web_release_manifest(
    tmp_path: Path,
) -> None:
    """Verify manifest.json calculates accurate SHA-256 digests and file sizes."""
    out_dir = tmp_path / "manifest_test"
    out_dir.mkdir(parents=True)

    dummy1 = out_dir / "sample1.json"
    dummy1.write_text('{"test": 1}', encoding="utf-8")
    dummy2 = out_dir / "sample2.csv"
    dummy2.write_text("a,b\n1,2\n", encoding="utf-8")

    files = {
        "sample1.json": dummy1,
        "sample2.csv": dummy2,
    }

    manifest_path = export_web_release_manifest(
        output_dir=out_dir,
        files=files,
        parliaments=[47],
        data_release_version="1.0.0",
    )
    assert manifest_path.exists()
    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest_data["web_schema_version"] == "1.0.0"
    assert manifest_data["data_release_version"] == "1.0.0"
    assert manifest_data["parliaments"] == [47]
    assert "sample1.json" in manifest_data["files"]
    assert "sample2.csv" in manifest_data["files"]

    sample1_entry = manifest_data["files"]["sample1.json"]
    assert sample1_entry["size_bytes"] == dummy1.stat().st_size
    assert sample1_entry["sha256"] == hashlib.sha256(dummy1.read_bytes()).hexdigest()


@pytest.mark.integration
def test_export_web_release_bundle(
    web_contract_db: tuple[Path, duckdb.DuckDBPyConnection],
    tmp_path: Path,
) -> None:
    """Verify export_web_release_bundle generates all web release artifacts."""
    _db_path, conn = web_contract_db
    out_dir = tmp_path / "web_bundle"

    results = export_web_release_bundle(
        conn,
        output_dir=out_dir,
        parliaments=[47],
        generated_at="2026-09-30T00:00:00+00:00",
    )

    assert Path(results["results_summary"]).exists()
    assert Path(results["schools_geojson"]).exists()
    assert Path(results["manifest"]).exists()
    assert Path(results["research_downloads"]["parliament-education.csv"]).exists()
    assert Path(results["research_downloads"]["school-profiles.parquet"]).exists()

    manifest_data = json.loads(Path(results["manifest"]).read_text(encoding="utf-8"))
    assert manifest_data["generated_at"] == "2026-09-30T00:00:00+00:00"
    assert "results-summary.json" in manifest_data["files"]
    assert "schools.geojson" in manifest_data["files"]


@pytest.mark.integration
def test_export_all_artifacts_includes_web_contract(
    web_contract_db: tuple[Path, duckdb.DuckDBPyConnection],
    tmp_path: Path,
) -> None:
    """Verify export_all_artifacts outputs the entire canonical bundle when include_web_release is True."""
    _db_path, conn = web_contract_db
    out_dir = tmp_path / "all_artifacts"

    results = export_all_artifacts(
        conn, output_dir=out_dir, parliaments=[47], include_web_release=True
    )

    assert Path(results["results_summary"]).exists()
    assert Path(results["schools_geojson"]).exists()
    assert Path(results["manifest"]).exists()
    assert Path(results["research_downloads"]["parliament-education.csv"]).exists()
    assert Path(results["research_downloads"]["school-profiles.parquet"]).exists()

    manifest_data = json.loads(Path(results["manifest"]).read_text(encoding="utf-8"))
    assert "results-summary.json" in manifest_data["files"]
    assert "schools.geojson" in manifest_data["files"]
