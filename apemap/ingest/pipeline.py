"""Canonical APH ingestion for all supported parliament cohorts."""

from __future__ import annotations

from apemap.constants import supported_parliaments

import csv
import json
import logging
import hashlib
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

import pandas as pd

if TYPE_CHECKING:
    from duckdb import DuckDBPyConnection

from apemap.constants import (
    EXTERNAL_DIR,
    PROCESSED_DIR,
    RAW_APH_DIR,
)
from apemap.db import (
    export_to_parquet,
    get_connection,
    init_schema,
    temporary_dataframe_view,
)
from apemap.ingest.aph import AphClient, parse_individual
from apemap.ingest.history import (
    HISTORICAL_REVIEW_COLUMNS,
    load_manual_education,
    load_service_overrides,
    write_review_queue,
)
from apemap.ingest.matching import (
    SchoolMatcher,
    extract_schools_from_bio_text,
    split_school_string,
    normalize_school_key,
)

logger = logging.getLogger(__name__)


class AphIngestResult(TypedDict):
    """Structured result contract for APH ingestion pipeline."""

    parliaments: list[int]
    members_count: int
    service_count: int
    institutions_count: int
    education_count: int
    snapshots_count: int
    unmatched_count: int
    unmatched_csv: Path
    coverage_metrics_json: Path
    coverage_metrics: dict[int, dict[str, Any]]
    parquet_paths: dict[str, Path]


def run_aph_ingestion(
    parliaments: list[int] | None = None,
    refresh: bool = False,
    db_path: Path | str | None = None,
    conn: DuckDBPyConnection | None = None,
    raw_individuals: list[dict[str, Any]] | None = None,
    export_parquet_files: bool = False,
    output_dir: Path | str | None = None,
    cache_dir: Path | str | None = None,
    external_dir: Path | str | None = None,
    manual_education_path: Path | None = None,
    retrieved_at: datetime | None = None,
    service_overrides_path: Path | None = None,
) -> AphIngestResult:
    """Execute APH ingestion for specified parliaments and populate DuckDB.

    Args:
        parliaments: List of parliament numbers to ingest (default: supported_parliaments()).
        refresh: Force re-fetching live from APH API if True.
        db_path: DuckDB file path or None for in-memory (ignored if conn is provided).
        conn: Optional caller-managed DuckDB connection. If provided, caller retains ownership.
        raw_individuals: Pre-loaded list of raw individual dicts (e.g. for testing).
        export_parquet_files: Whether to export canonical tables to Parquet.
        output_dir: Directory to store generated reports and Parquet exports.
        cache_dir: Raw APH cache directory.
        external_dir: Directory containing ACARA reference CSV files.

    Returns:
        Structured dictionary with metrics, counts, and artifact paths.
    """
    if parliaments is None:
        parliaments = supported_parliaments()
    target_parls_set = set(parliaments)

    out_dir = Path(output_dir or PROCESSED_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Initialize components
    client = AphClient(cache_dir=cache_dir or RAW_APH_DIR)
    try:
        if raw_individuals is None:
            raw_individuals = client.fetch_individuals(refresh=refresh)
    finally:
        client.close()

    matcher = SchoolMatcher(
        external_dir=external_dir or EXTERNAL_DIR,
        require_alias_sources=any(p < 46 for p in parliaments),
    )

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
    unmatched_reviews: list[dict[str, Any]] = []

    retrieval_time = retrieved_at or datetime.now(timezone.utc)
    manual_education = load_manual_education(manual_education_path)
    service_overrides = load_service_overrides(service_overrides_path)
    service_reviews: list[dict[str, Any]] = []

    for item in raw_individuals:
        parsed = parse_individual(item, target_parliaments=target_parls_set)
        if parsed:
            overrides = {
                p: rows
                for (phid, p), rows in service_overrides.items()
                if phid == parsed.demographics.aph_id.lower() and p in target_parls_set
            }
            parsed.services = [
                s for s in parsed.services if s.parliament_number not in overrides
            ]
            parsed.services.extend(s for rows in overrides.values() for s in rows)
            parsed.service_reviews = [
                r
                for r in parsed.service_reviews
                if r["parliament_number"] not in overrides
            ]
            service_reviews.extend(parsed.service_reviews)
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
                **asdict(stint),
                "retrieved_at": stint.retrieved_at or retrieval_time,
            }
            # Increment counts
            p_metrics = coverage_metrics[stint.parliament_number]
            if stint.is_opening_day_member:
                p_metrics["opening_day_parliamentarians"] += 1
            if stint.is_current_member:
                p_metrics["current_parliamentarians"] += 1
        for p in member_parls:
            coverage_metrics[p]["total_parliamentarians"] += 1

        # Extract secondary schools
        school_candidates: list[str] = []
        if parsed.secondary_school_raw:
            school_candidates = split_school_string(parsed.secondary_school_raw)
        if not school_candidates and parsed.bio_texts:
            school_candidates = extract_schools_from_bio_text(parsed.bio_texts)
        manual_by_name = {
            row["school_name"]: row
            for row in manual_education.get(mem.aph_id.lower(), [])
        }
        school_candidates = list(
            dict.fromkeys(school_candidates + list(manual_by_name))
        )
        if not school_candidates:
            for p in member_parls:
                unmatched_reviews.append(
                    {
                        "parliament_number": p,
                        "member_id": mem.member_id,
                        "member_display_name": mem.display_name,
                        "raw_school_text": "",
                        "source_url": parsed.source_url,
                        "is_international": False,
                        "suggested_action": "Research missing secondary education",
                        "notes": "No APH secondary-school evidence",
                    }
                )

        # Match schools
        for cand in school_candidates:
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
                    "country": "overseas"
                    if matched.is_international
                    else ("Australia" if matched.acara_id else None),
                    "institution_status": matched.institution_status,
                }

            # Create member education link
            digest = hashlib.sha256(normalize_school_key(cand).encode()).hexdigest()[
                :16
            ]
            edu_id = f"edu-{mem.aph_id.lower()}-{digest}"
            manual = manual_by_name.get(cand)
            alias = (
                matcher.aliases.get(cand.lower())
                or matcher.aliases.get(normalize_school_key(cand))
                or {}
            )
            if alias and not alias.get("source_url") and matcher.require_alias_sources:
                alias = {}
            resolution = (
                alias.get(
                    "relationship_type",
                    "successor"
                    if alias.get("notes") and "amalgamat" in alias["notes"].lower()
                    else "rename",
                )
                if alias
                else ("direct" if matched.acara_id else "unresolved")
            )
            education_records.append(
                {
                    "education_id": edu_id,
                    "member_id": mem.member_id,
                    "institution_id": inst_id,
                    "level": "secondary",
                    "years_attended": None,
                    "graduation_year": None,
                    "attended_status": manual["attended_status"]
                    if manual
                    else "attended_unspecified",
                    "source_url": manual["source_url"] if manual else parsed.source_url,
                    "retrieved_at": manual["retrieved_at"]
                    if manual
                    else retrieval_time,
                    "confidence": min(
                        (manual["confidence"], matched.confidence),
                        key=lambda value: {
                            "unconfirmed": 0,
                            "provisional": 1,
                            "verified": 2,
                        }[value],
                    )
                    if manual
                    else matched.confidence,
                    "school_name_as_recorded": cand,
                    "institution_resolution": resolution,
                    "resolution_source_url": alias.get("source_url"),
                    "evidence_origin": "manual" if manual else "aph",
                    "reviewer_notes": (
                        f"Parsed from APH Handbook text '{cand}'; "
                        f"matched status: {matched.confidence}"
                        + (
                            f"; Manual evidence: {manual['reviewer_notes']}"
                            if manual
                            else ""
                        )
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
    should_close_conn = conn is None
    active_conn = conn or get_connection(db_path)
    conn = active_conn
    try:
        init_schema(conn)
        conn.execute("BEGIN TRANSACTION")
        previous_times = dict(
            conn.execute(
                "SELECT service_id, retrieved_at FROM parliament_service"
            ).fetchall()
        )
        # The source scope includes people whose corrected history now has no stint.
        # Deleting only incoming stint IDs would leave obsolete rows behind.
        source_ids = [
            f"aph-{str(item['PHID']).lower()}"
            for item in raw_individuals
            if item.get("PHID")
        ]
        conn.execute(
            """DELETE FROM parliament_service
            WHERE member_id IN (SELECT UNNEST(?))
              AND parliament_number IN (SELECT UNNEST(?))
              AND (source_url LIKE 'https://handbookapi.aph.gov.au/%' OR service_id LIKE 'srv-%')""",
            [source_ids, parliaments],
        )

        if members_map:
            members_df = pd.DataFrame(list(members_map.values()))
            with temporary_dataframe_view(conn, "tmp_members", members_df):
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
                conn.execute(
                    """DELETE FROM member_education me
                    WHERE me.member_id IN (SELECT member_id FROM tmp_members)
                      AND (me.evidence_origin = 'aph' OR
                           (me.evidence_origin IS NULL AND me.reviewer_notes LIKE 'Parsed from APH Handbook%'))
                      AND me.education_id NOT IN (SELECT UNNEST(?))""",
                    [[row["education_id"] for row in education_records]],
                )

        if service_map:
            services_df = pd.DataFrame(list(service_map.values()))
            services_df["retrieved_at"] = [
                previous_times.get(sid) or stamp
                for sid, stamp in zip(
                    services_df["service_id"], services_df["retrieved_at"]
                )
            ]
            with temporary_dataframe_view(conn, "tmp_services", services_df):
                conn.execute(
                    """
                    INSERT INTO parliament_service (
                        service_id, member_id, parliament_number, chamber,
                        party, party_abbrev, electorate, state_or_territory,
                        service_start, service_end, is_opening_day_member, is_current_member,
                        source_url, retrieved_at, source_service_start, source_service_end
                    )
                    SELECT
                        service_id, member_id, parliament_number, chamber,
                        party, party_abbrev, electorate, state_or_territory,
                        service_start, service_end, is_opening_day_member, is_current_member,
                        source_url, retrieved_at, source_service_start, source_service_end
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

        if institutions_map:
            inst_df = pd.DataFrame(list(institutions_map.values()))
            with temporary_dataframe_view(conn, "tmp_institutions", inst_df):
                conn.execute(
                    """
                    INSERT INTO institutions (
                        institution_id, acara_id, school_name, school_type,
                        sector, campus_type, state, suburb, postcode, longitude, latitude, country, institution_status
                    )
                    SELECT
                        institution_id, acara_id, school_name, school_type,
                        sector, campus_type, state, suburb, postcode, longitude, latitude, country, institution_status
                    FROM tmp_institutions
                    ON CONFLICT (institution_id) DO NOTHING
                    """
                )

        if education_records:
            edu_df = pd.DataFrame(education_records)
            with temporary_dataframe_view(conn, "tmp_edu", edu_df):
                conn.execute("""DELETE FROM member_education me
                    WHERE me.member_id IN (SELECT member_id FROM tmp_edu)
                      AND me.education_id NOT IN (SELECT education_id FROM tmp_edu)
                      AND (me.evidence_origin = 'aph' OR
                           (me.evidence_origin IS NULL AND me.reviewer_notes LIKE 'Parsed from APH Handbook%'))""")
                # Preserve the original source retrieval time so reruns remain idempotent.
                conn.execute(
                    """
                    INSERT INTO member_education (
                        education_id, member_id, institution_id, level,
                        years_attended, graduation_year, attended_status,
                        source_url, retrieved_at, confidence, reviewer_notes,
                        school_name_as_recorded, institution_resolution, resolution_source_url, evidence_origin
                    )
                    SELECT
                        education_id, member_id, institution_id, level,
                        years_attended, graduation_year, attended_status,
                        source_url, retrieved_at, confidence, reviewer_notes,
                        school_name_as_recorded, institution_resolution, resolution_source_url, evidence_origin
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
                        reviewer_notes = EXCLUDED.reviewer_notes,
                        school_name_as_recorded = EXCLUDED.school_name_as_recorded,
                        institution_resolution = EXCLUDED.institution_resolution,
                        resolution_source_url = EXCLUDED.resolution_source_url,
                        evidence_origin = EXCLUDED.evidence_origin
                    """
                )

        conn.execute("COMMIT")
        write_review_queue(
            out_dir / "historical_service_review.csv",
            service_reviews,
            ["member_id", "parliament_number"],
            [
                "member_id",
                "parliament_number",
                "source_url",
                "notes",
                "review_status",
                "resolved_value",
                "manual_source_url",
                "review_notes",
            ],
        )
        write_review_queue(
            out_dir / "historical_education_review.csv",
            unmatched_reviews,
            ["member_id", "parliament_number", "raw_school_text"],
            HISTORICAL_REVIEW_COLUMNS,
        )
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
            "snapshots_count": 0,
            "unmatched_count": len(unmatched_reviews),
            "unmatched_csv": unmatched_csv_path,
            "coverage_metrics_json": metrics_json_path,
            "coverage_metrics": coverage_metrics,
            "parquet_paths": parquet_paths,
        }
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        if should_close_conn:
            active_conn.close()
