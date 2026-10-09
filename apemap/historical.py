"""Reproducible historical input acquisition and a separately staged release.

Run ``uv run python -m apemap.historical --download`` once to acquire ACARA;
subsequent runs with the pinned manifest use cached inputs without the network.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from typing import Any

from apemap.constants import (
    ACARA_LOCATION_2025_URL,
    ACARA_PROFILE_LONGITUDINAL_URL,
    DATA_DIR,
    EXTERNAL_DIR,
    PROJECT_ROOT,
    RAW_APH_DIR,
    REFERENCE_DIR,
)
from apemap.coverage import export_parliament_coverage
from apemap.db import get_connection, init_schema
from apemap.ingest.abs import run_abs_ingestion
from apemap.ingest.acara import (
    download_acara_dataset,
    convert_xlsx_to_csv,
    run_acara_ingestion,
)
from apemap.ingest.aec import run_aec_ingestion
from apemap.ingest.funding import ingest_all_funding
from apemap.ingest.pipeline import run_aph_ingestion
from apemap.inputs import compute_sha256, preflight_offline_inputs
from apemap.release.build import build_release
from apemap.release.verify import verify_release
from apemap.review.integration import attach_source_manifest


def prepare_historical_inputs(
    input_dir: Path,
    manifest_path: Path,
    *,
    download: bool = False,
    project_root: Path = PROJECT_ROOT,
) -> None:
    """Acquire into a separate cache, leaving checked-in external inputs intact."""
    input_dir.mkdir(parents=True, exist_ok=True)
    if download:
        for url, xlsx, csv_name in (
            (
                ACARA_PROFILE_LONGITUDINAL_URL,
                "School Profile 2008-2025.xlsx",
                "school-profile-2008-2025.csv",
            ),
            (
                ACARA_LOCATION_2025_URL,
                "School Location 2025.xlsx",
                "school-location-2025.csv",
            ),
        ):
            download_acara_dataset(url, input_dir / xlsx)
            convert_xlsx_to_csv(input_dir / xlsx, input_dir / csv_name)
        cached_json = input_dir / "acara_school_results.json"
        if not cached_json.exists():
            shutil.copyfile(EXTERNAL_DIR / "acara_school_results.json", cached_json)
        files: dict[str, Any] = {}
        sources = {
            input_dir / "School Profile 2008-2025.xlsx": ACARA_PROFILE_LONGITUDINAL_URL,
            input_dir / "school-profile-2008-2025.csv": ACARA_PROFILE_LONGITUDINAL_URL,
            input_dir / "School Location 2025.xlsx": ACARA_LOCATION_2025_URL,
            input_dir / "school-location-2025.csv": ACARA_LOCATION_2025_URL,
            cached_json: "Existing ACARA cache; original retrieval date unrecorded",
            RAW_APH_DIR
            / "individuals.json": "https://handbookapi.aph.gov.au/api/individuals",
        }
        for path in sorted(REFERENCE_DIR.iterdir()):
            if path.suffix in (".json", ".csv"):
                sources[path] = (
                    "Reviewed APEMAP reference input; individual source URLs in records"
                )
        # The mutable review ledger/registry lives in a subdirectory and is
        # captured separately by replay, rather than pinned as an upstream source.
        # Include the existing AEC cache required by the coordinated pipeline.
        for path in sorted((DATA_DIR / "raw/aec/2025").iterdir()):
            if path.is_file():
                sources[path] = (
                    "https://www.aec.gov.au/Electorates/files/2025/AUS-March-2025-esri.zip"
                )
        for path, source in sources.items():
            if not path.exists():
                raise FileNotFoundError(path)
            files[path.relative_to(PROJECT_ROOT).as_posix()] = {
                "sha256": compute_sha256(path),
                "size_bytes": path.stat().st_size,
                "source": source,
                "required": True,
            }
        now = datetime.now(timezone.utc).isoformat()
        manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "created_at": now,
                    "description": f"Historical inputs: existing APH/AEC snapshots and ACARA downloaded on {now[:10]}; CSV conversion via apemap.ingest.acara",
                    "licensing": "APH Commonwealth copyright; ACARA attribution and Data Access terms; AEC CC BY 4.0; project reference data CC BY 4.0",
                    "files": files,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
    preflight_offline_inputs(manifest_path=manifest_path, base_dir=project_root)
    for name in ("school-profile-2008-2025.csv", "school-location-2025.csv"):
        if not (input_dir / name).exists():
            raise FileNotFoundError(
                f"Historical input {name} is missing; acquire with --download"
            )


def build_historical_release(
    db_path: Path,
    output_dir: Path,
    input_dir: Path,
    manifest_path: Path,
    version: str,
    *,
    download: bool = False,
    parliaments: list[int] | None = None,
    decision_log_path: Path | None = None,
    finance_year: int = 2024,
    legacy_finance_path: Path | None = None,
    release_recipe: dict[str, Any] | None = None,
    source_commit: str | None = None,
    project_root: Path = PROJECT_ROOT,
) -> dict[str, Any]:
    """Populate all terms and verify a public release without modifying old artifacts."""
    prepare_historical_inputs(
        input_dir, manifest_path, download=download, project_root=project_root
    )
    manifest_bytes = manifest_path.read_bytes()
    input_manifest = json.loads(manifest_bytes)
    source_time = datetime.fromisoformat(input_manifest["created_at"])
    if db_path.exists():
        raise FileExistsError(f"Use a fresh historical database path: {db_path}")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(
            f"Use an empty historical release directory: {output_dir}"
        )
    with get_connection(db_path) as conn:
        review_dir = output_dir / "review"
        init_schema(conn)
        run_aec_ingestion(
            conn=conn,
            election_year=2025,
            refresh=False,
            export_parquet_files=False,
            raw_dir=project_root / "data/raw/aec/2025",
        )
        run_abs_ingestion(conn=conn, export_parquet_files=False)
        run_acara_ingestion(
            conn=conn,
            download_latest=False,
            external_dir=input_dir,
            output_dir=review_dir,
            export_parquet_files=False,
            decision_log_path=decision_log_path,
            gpkg_path=legacy_finance_path,
        )
        run_aph_ingestion(
            conn=conn,
            external_dir=input_dir,
            output_dir=review_dir,
            retrieved_at=source_time,
            parliaments=parliaments,
            decision_log_path=decision_log_path,
            cache_dir=project_root / "data/raw/aph",
        )
        attach_source_manifest(conn, manifest_path, manifest_bytes=manifest_bytes)
        # Historical profile-only institutions must exist before finance/funding resolution.
        reference_dir = project_root / "data/reference"
        ingest_all_funding(
            conn,
            retrieved_at=source_time,
            benchmarks_path=reference_dir / "acara_school_finance_benchmarks.csv",
            nsw_path=reference_dir / "nsw_ram_allocations.csv",
            tas_path=reference_dir / "tasmania_srp_allocations.csv",
            nt_path=reference_dir / "nt_school_funding.csv",
            qld_path=reference_dir / "qld_non_state_grants.csv",
            manual_path=reference_dir / "manual_school_funding.csv",
        )
        export_parliament_coverage(
            conn, review_dir, parliaments, finance_year=finance_year
        )
        result = build_release(
            conn=conn,
            output_dir=output_dir,
            version=version,
            finance_reporting_year=finance_year,
            parliaments=parliaments,
            source_commit=source_commit,
            release_recipe=release_recipe,
            strict=True,
            generated_at=input_manifest["created_at"],
            source_snapshot_dates={
                "aph": None,
                "acara": source_time.date().isoformat(),
                "aec": "2025-03-04",
                "abs": "2026-03-05",
            },
        )
    verified = verify_release(output_dir, strict_assertions=True)
    if not verified["valid"]:
        raise RuntimeError(
            f"Historical release verification failed: {verified['errors']}"
        )
    return {
        **result,
        "verified": verified,
        "db_path": str(db_path),
        "input_manifest": str(manifest_path),
    }


def main() -> None:
    """Build or replay the historical release from pinned source inputs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    parser.add_argument(
        "--db-path", type=Path, default=DATA_DIR / "aped-historical.duckdb"
    )
    parser.add_argument(
        "--output-dir", type=Path, default=DATA_DIR / "processed/releases/0.3.0"
    )
    parser.add_argument(
        "--input-dir", type=Path, default=DATA_DIR / "raw/historical/acara"
    )
    parser.add_argument(
        "--manifest-path",
        type=Path,
        default=DATA_DIR / "historical-inputs-manifest.json",
    )
    parser.add_argument("--version", default="0.3.0")
    args = parser.parse_args()
    print(
        json.dumps(
            build_historical_release(
                args.db_path,
                args.output_dir,
                args.input_dir,
                args.manifest_path,
                args.version,
                download=args.download,
            ),
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
