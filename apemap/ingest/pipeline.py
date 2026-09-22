"""Canonical ingestion pipeline for 48th Parliament and historical rebuilds (46th/47th)."""

from __future__ import annotations

import csv
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from apemap.constants import (
    EXTERNAL_DIR,
    PROCESSED_DIR,
    RAW_APH_DIR,
)
from apemap.db import export_to_parquet, get_connection, init_schema
from apemap.ingest.aph import AphClient, parse_individual
from apemap.ingest.matching import (
    SchoolMatcher,
    extract_schools_from_bio_text,
    split_school_string,
)

logger = logging.getLogger(__name__)


def run_aph_ingestion(
    parliaments: list[int] | None = None,
    refresh: bool = False,
    db_path: Path | str | None = None,
    raw_individuals: list[dict[str, Any]] | None = None,
    export_parquet_files: bool = False,
    output_dir: Path | str | None = None,
    cache_dir: Path | str | None = None,
    external_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Execute APH ingestion for specified parliaments and populate DuckDB.

    Args:
        parliaments: List of parliament numbers to ingest (default: [46, 47, 48]).
        refresh: Force re-fetching live from APH API if True.
        db_path: DuckDB file path or None for in-memory.
        raw_individuals: Pre-loaded list of raw individual dicts (e.g. for testing).
        export_parquet_files: Whether to export canonical tables to Parquet.
        output_dir: Directory to store generated reports and Parquet exports.
        cache_dir: Raw APH cache directory.
        external_dir: Directory containing ACARA reference CSV files.

    Returns:
        Structured dictionary with metrics, counts, and artifact paths.
    """
    if parliaments is None:
        parliaments = [46, 47, 48]
    target_parls_set = set(parliaments)

    out_dir = Path(output_dir or PROCESSED_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Initialize components
    client = AphClient(cache_dir=cache_dir or RAW_APH_DIR)
    matcher = SchoolMatcher(external_dir=external_dir or EXTERNAL_DIR)

    if raw_individuals is None:
        raw_individuals = client.fetch_individuals(refresh=refresh)

    conn = get_connection(db_path)
    init_schema(conn)

    # Coverage counters per parliament
    coverage_metrics: dict[int, dict[str, Any]] = {}
    for p in parliaments:
        coverage_metrics[p] = {
            "parliament_number": p,
            "total_parliamentarians": 0,
            "opening_day_parliamentarians": 0,
            "current_parliamentarians": 0,
            "matched_schools": 0,
            "unmatched_schools": 0,
            "international_schools": 0,
            "unclassified_sectors": 0,
            "sectors": {
                "Government": 0,
                "Catholic": 0,
                "Independent": 0,
                "Other": 0,
            },
        }

    members_map: dict[str, dict[str, Any]] = {}
    service_map: dict[str, dict[str, Any]] = {}
    institutions_map: dict[str, dict[str, Any]] = {}
    education_records: list[dict[str, Any]] = []
    snapshots_map: dict[tuple[str, int], dict[str, Any]] = {}
    unmatched_reviews: list[dict[str, Any]] = []

    retrieval_time = datetime.now(timezone.utc)

    for item in raw_individuals:
        parsed = parse_individual(item, target_parliaments=target_parls_set)
        if parsed is None or not parsed.services:
            continue

        mem = parsed.demographics
        members_map[mem.member_id] = {
            "member_id": mem.member_id,
            "family_name": mem.family_name,
            "given_name": mem.given_name,
            "display_name": mem.display_name,
            "gender": mem.gender,
            "date_of_birth": mem.date_of_birth,
            "aph_id": mem.aph_id,
            "wikidata_id": mem.wikidata_id,
        }

        # Track parliament memberships
        member_parls = {s.parliament_number for s in parsed.services}
        for stint in parsed.services:
            service_map[stint.service_id] = {
                "service_id": stint.service_id,
                "member_id": stint.member_id,
                "parliament_number": stint.parliament_number,
                "chamber": stint.chamber,
                "party": stint.party,
                "party_abbrev": stint.party_abbrev,
                "electorate": stint.electorate,
                "state_or_territory": stint.state_or_territory,
                "service_start": stint.service_start,
                "service_end": stint.service_end,
                "is_opening_day_member": stint.is_opening_day_member,
                "is_current_member": stint.is_current_member,
            }
            # Increment counts
            p_metrics = coverage_metrics[stint.parliament_number]
            p_metrics["total_parliamentarians"] += 1
            if stint.is_opening_day_member:
                p_metrics["opening_day_parliamentarians"] += 1
            if stint.is_current_member:
                p_metrics["current_parliamentarians"] += 1

        # Extract secondary schools
        school_candidates: list[str] = []
        if parsed.secondary_school_raw:
            school_candidates = split_school_string(parsed.secondary_school_raw)
        if not school_candidates and parsed.bio_texts:
            school_candidates = extract_schools_from_bio_text(parsed.bio_texts)

        # Match schools
        for idx, cand in enumerate(school_candidates):
            matched = matcher.match(cand)
            inst_id = matched.institution_id

            # Store institution
            if inst_id not in institutions_map:
                institutions_map[inst_id] = {
                    "institution_id": inst_id,
                    "acara_id": matched.acara_id,
                    "school_name": matched.school_name,
                    "school_type": matched.school_type,
                    "sector": matched.sector,
                    "campus_type": matched.campus_type,
                    "state": matched.state,
                    "suburb": matched.suburb,
                    "postcode": matched.postcode,
                    "longitude": matched.longitude,
                    "latitude": matched.latitude,
                }

            # Store school snapshot if metrics exist
            if matched.total_enrolments is not None or matched.icsea is not None:
                snap_key = (inst_id, 2022)
                if snap_key not in snapshots_map:
                    snapshots_map[snap_key] = {
                        "institution_id": inst_id,
                        "snapshot_year": 2022,
                        "total_enrolments": matched.total_enrolments,
                        "icsea": matched.icsea,
                        "financial_profile_2021": None,
                    }

            # Create member education link
            edu_id = f"edu-{mem.aph_id.lower()}-{idx}-{inst_id[:30]}"
            education_records.append(
                {
                    "education_id": edu_id,
                    "member_id": mem.member_id,
                    "institution_id": inst_id,
                    "level": "secondary",
                    "years_attended": None,
                    "graduation_year": None,
                    "attended_status": "graduated",
                    "source_url": parsed.source_url,
                    "retrieved_at": retrieval_time,
                    "confidence": matched.confidence,
                    "reviewer_notes": (
                        f"Parsed from APH Handbook text '{cand}'; "
                        f"matched status: {matched.confidence}"
                        + (
                            f"; {matched.reviewer_notes}"
                            if matched.reviewer_notes
                            else ""
                        )
                    ),
                }
            )

            # Update parliament coverage metrics
            for p in member_parls:
                p_metrics = coverage_metrics[p]
                if matched.confidence in ("verified", "provisional"):
                    p_metrics["matched_schools"] += 1
                else:
                    p_metrics["unmatched_schools"] += 1

                if matched.is_international:
                    p_metrics["international_schools"] += 1

                sec = matched.sector
                if sec in p_metrics["sectors"]:
                    p_metrics["sectors"][sec] += 1
                else:
                    p_metrics["sectors"]["Other"] += 1
                    p_metrics["unclassified_sectors"] += 1

            # Log unmatched / international schools to review list
            if matched.confidence == "unconfirmed" or matched.is_international:
                for p in member_parls:
                    unmatched_reviews.append(
                        {
                            "parliament_number": p,
                            "member_id": mem.member_id,
                            "member_display_name": mem.display_name,
                            "raw_school_text": cand,
                            "source_url": parsed.source_url,
                            "is_international": matched.is_international,
                            "suggested_action": (
                                "Confirm overseas school"
                                if matched.is_international
                                else "Lookup in ACARA historical register"
                            ),
                            "notes": f"Provisional institution ID: {inst_id}",
                        }
                    )

    # Database insertion
    if members_map:
        members_df = pd.DataFrame(list(members_map.values()))
        conn.register("tmp_members", members_df)
        conn.execute(
            """
            INSERT INTO members (
                member_id, family_name, given_name, display_name,
                gender, date_of_birth, aph_id, wikidata_id
            )
            SELECT
                member_id, family_name, given_name, display_name,
                gender, date_of_birth, aph_id, wikidata_id
            FROM tmp_members
            ON CONFLICT (member_id) DO NOTHING
            """
        )
        conn.unregister("tmp_members")

    if service_map:
        services_df = pd.DataFrame(list(service_map.values()))
        conn.register("tmp_services", services_df)
        conn.execute(
            """
            INSERT INTO parliament_service (
                service_id, member_id, parliament_number, chamber,
                party, party_abbrev, electorate, state_or_territory,
                service_start, service_end, is_opening_day_member, is_current_member
            )
            SELECT
                service_id, member_id, parliament_number, chamber,
                party, party_abbrev, electorate, state_or_territory,
                service_start, service_end, is_opening_day_member, is_current_member
            FROM tmp_services
            ON CONFLICT (service_id) DO UPDATE SET
                member_id = EXCLUDED.member_id,
                parliament_number = EXCLUDED.parliament_number,
                chamber = EXCLUDED.chamber,
                party = EXCLUDED.party,
                party_abbrev = EXCLUDED.party_abbrev,
                electorate = EXCLUDED.electorate,
                state_or_territory = EXCLUDED.state_or_territory,
                service_start = EXCLUDED.service_start,
                service_end = EXCLUDED.service_end,
                is_opening_day_member = EXCLUDED.is_opening_day_member,
                is_current_member = EXCLUDED.is_current_member
            """
        )
        conn.unregister("tmp_services")

    if institutions_map:
        inst_df = pd.DataFrame(list(institutions_map.values()))
        conn.register("tmp_institutions", inst_df)
        conn.execute(
            """
            INSERT INTO institutions (
                institution_id, acara_id, school_name, school_type,
                sector, campus_type, state, suburb, postcode, longitude, latitude
            )
            SELECT
                institution_id, acara_id, school_name, school_type,
                sector, campus_type, state, suburb, postcode, longitude, latitude
            FROM tmp_institutions
            ON CONFLICT (institution_id) DO NOTHING
            """
        )
        conn.unregister("tmp_institutions")

    if snapshots_map:
        snaps_df = pd.DataFrame(list(snapshots_map.values()))
        conn.register("tmp_snapshots", snaps_df)
        conn.execute(
            """
            INSERT INTO school_snapshots (
                institution_id, snapshot_year, total_enrolments, icsea, financial_profile_2021
            )
            SELECT
                institution_id, snapshot_year, total_enrolments, icsea, financial_profile_2021
            FROM tmp_snapshots
            ON CONFLICT (institution_id, snapshot_year) DO UPDATE SET
                total_enrolments = EXCLUDED.total_enrolments,
                icsea = EXCLUDED.icsea,
                financial_profile_2021 = EXCLUDED.financial_profile_2021
            """
        )
        conn.unregister("tmp_snapshots")

    if education_records:
        edu_df = pd.DataFrame(education_records)
        conn.register("tmp_edu", edu_df)
        # Preserve the original source retrieval time so reruns remain idempotent.
        conn.execute(
            """
            INSERT INTO member_education (
                education_id, member_id, institution_id, level,
                years_attended, graduation_year, attended_status,
                source_url, retrieved_at, confidence, reviewer_notes
            )
            SELECT
                education_id, member_id, institution_id, level,
                years_attended, graduation_year, attended_status,
                source_url, retrieved_at, confidence, reviewer_notes
            FROM tmp_edu
            ON CONFLICT (education_id) DO UPDATE SET
                member_id = EXCLUDED.member_id,
                institution_id = EXCLUDED.institution_id,
                level = EXCLUDED.level,
                years_attended = EXCLUDED.years_attended,
                graduation_year = EXCLUDED.graduation_year,
                attended_status = EXCLUDED.attended_status,
                source_url = EXCLUDED.source_url,
                confidence = EXCLUDED.confidence,
                reviewer_notes = EXCLUDED.reviewer_notes
            """
        )
        conn.unregister("tmp_edu")

    # Write review CSV
    unmatched_csv_path = out_dir / "unmatched_schools.csv"
    fieldnames = [
        "parliament_number",
        "member_id",
        "member_display_name",
        "raw_school_text",
        "source_url",
        "is_international",
        "suggested_action",
        "notes",
    ]
    with unmatched_csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in unmatched_reviews:
            writer.writerow(row)

    # Write coverage metrics JSON
    metrics_json_path = out_dir / "coverage_metrics.json"
    metrics_json_path.write_text(
        json.dumps(coverage_metrics, indent=2), encoding="utf-8"
    )

    parquet_paths: dict[str, Path] = {}
    if export_parquet_files:
        parquet_paths = export_to_parquet(conn, out_dir)

    return {
        "parliaments": parliaments,
        "members_count": len(members_map),
        "service_count": len(service_map),
        "institutions_count": len(institutions_map),
        "education_count": len(education_records),
        "snapshots_count": len(snapshots_map),
        "unmatched_count": len(unmatched_reviews),
        "unmatched_csv": unmatched_csv_path,
        "coverage_metrics_json": metrics_json_path,
        "coverage_metrics": coverage_metrics,
        "parquet_paths": parquet_paths,
        "connection": conn,
    }
