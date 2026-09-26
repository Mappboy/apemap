"""AEC 2025 federal electoral boundaries ingestion module.

Downloads and caches the official national 2025 Australian Electoral Commission (AEC)
federal election boundaries ESRI Shapefile, ingests polygons into DuckDB using DuckDB Spatial
and ST_Read(), links divisions to canonical states, and exports canonical GeoParquet.
"""

from __future__ import annotations

import logging
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any
import zipfile

import requests

from apemap.constants import (
    AEC_2025_RETRIEVED_AT,
    AEC_2025_SHAPEFILE_URL,
    AEC_2025_SOURCE_DATASET,
    DATA_DIR,
    PROCESSED_DIR,
    RAW_AEC_2025_DIR,
)
from apemap.db import ensure_spatial, export_to_parquet, get_connection, init_schema

if TYPE_CHECKING:
    import duckdb

logger = logging.getLogger(__name__)

# Complete authoritative mapping of 2025 federal divisions to canonical casing and state/territory
DIVISION_METADATA_2025: dict[str, tuple[str, str]] = {
    "bean": ("Bean", "ACT"),
    "canberra": ("Canberra", "ACT"),
    "fenner": ("Fenner", "ACT"),
    "banks": ("Banks", "NSW"),
    "barton": ("Barton", "NSW"),
    "bennelong": ("Bennelong", "NSW"),
    "berowra": ("Berowra", "NSW"),
    "blaxland": ("Blaxland", "NSW"),
    "bradfield": ("Bradfield", "NSW"),
    "calare": ("Calare", "NSW"),
    "chifley": ("Chifley", "NSW"),
    "cook": ("Cook", "NSW"),
    "cowper": ("Cowper", "NSW"),
    "cunningham": ("Cunningham", "NSW"),
    "dobell": ("Dobell", "NSW"),
    "eden-monaro": ("Eden-Monaro", "NSW"),
    "farrer": ("Farrer", "NSW"),
    "fowler": ("Fowler", "NSW"),
    "gilmore": ("Gilmore", "NSW"),
    "grayndler": ("Grayndler", "NSW"),
    "greenway": ("Greenway", "NSW"),
    "hughes": ("Hughes", "NSW"),
    "hume": ("Hume", "NSW"),
    "hunter": ("Hunter", "NSW"),
    "kingsford smith": ("Kingsford Smith", "NSW"),
    "lindsay": ("Lindsay", "NSW"),
    "lyne": ("Lyne", "NSW"),
    "macarthur": ("Macarthur", "NSW"),
    "mackellar": ("Mackellar", "NSW"),
    "macquarie": ("Macquarie", "NSW"),
    "mcmahon": ("McMahon", "NSW"),
    "mitchell": ("Mitchell", "NSW"),
    "new england": ("New England", "NSW"),
    "newcastle": ("Newcastle", "NSW"),
    "page": ("Page", "NSW"),
    "parkes": ("Parkes", "NSW"),
    "parramatta": ("Parramatta", "NSW"),
    "paterson": ("Paterson", "NSW"),
    "reid": ("Reid", "NSW"),
    "richmond": ("Richmond", "NSW"),
    "riverina": ("Riverina", "NSW"),
    "robertson": ("Robertson", "NSW"),
    "shortland": ("Shortland", "NSW"),
    "sydney": ("Sydney", "NSW"),
    "warringah": ("Warringah", "NSW"),
    "watson": ("Watson", "NSW"),
    "wentworth": ("Wentworth", "NSW"),
    "werriwa": ("Werriwa", "NSW"),
    "whitlam": ("Whitlam", "NSW"),
    "lingiari": ("Lingiari", "NT"),
    "solomon": ("Solomon", "NT"),
    "blair": ("Blair", "Qld"),
    "bonner": ("Bonner", "Qld"),
    "bowman": ("Bowman", "Qld"),
    "brisbane": ("Brisbane", "Qld"),
    "capricornia": ("Capricornia", "Qld"),
    "dawson": ("Dawson", "Qld"),
    "dickson": ("Dickson", "Qld"),
    "fadden": ("Fadden", "Qld"),
    "fairfax": ("Fairfax", "Qld"),
    "fisher": ("Fisher", "Qld"),
    "flynn": ("Flynn", "Qld"),
    "forde": ("Forde", "Qld"),
    "griffith": ("Griffith", "Qld"),
    "groom": ("Groom", "Qld"),
    "herbert": ("Herbert", "Qld"),
    "hinkler": ("Hinkler", "Qld"),
    "kennedy": ("Kennedy", "Qld"),
    "leichhardt": ("Leichhardt", "Qld"),
    "lilley": ("Lilley", "Qld"),
    "longman": ("Longman", "Qld"),
    "maranoa": ("Maranoa", "Qld"),
    "mcpherson": ("McPherson", "Qld"),
    "moncrieff": ("Moncrieff", "Qld"),
    "moreton": ("Moreton", "Qld"),
    "oxley": ("Oxley", "Qld"),
    "petrie": ("Petrie", "Qld"),
    "rankin": ("Rankin", "Qld"),
    "ryan": ("Ryan", "Qld"),
    "wide bay": ("Wide Bay", "Qld"),
    "wright": ("Wright", "Qld"),
    "adelaide": ("Adelaide", "SA"),
    "barker": ("Barker", "SA"),
    "boothby": ("Boothby", "SA"),
    "grey": ("Grey", "SA"),
    "hindmarsh": ("Hindmarsh", "SA"),
    "kingston": ("Kingston", "SA"),
    "makin": ("Makin", "SA"),
    "mayo": ("Mayo", "SA"),
    "spence": ("Spence", "SA"),
    "sturt": ("Sturt", "SA"),
    "bass": ("Bass", "Tas"),
    "braddon": ("Braddon", "Tas"),
    "clark": ("Clark", "Tas"),
    "franklin": ("Franklin", "Tas"),
    "lyons": ("Lyons", "Tas"),
    "aston": ("Aston", "Vic"),
    "ballarat": ("Ballarat", "Vic"),
    "bendigo": ("Bendigo", "Vic"),
    "bruce": ("Bruce", "Vic"),
    "calwell": ("Calwell", "Vic"),
    "casey": ("Casey", "Vic"),
    "chisholm": ("Chisholm", "Vic"),
    "cooper": ("Cooper", "Vic"),
    "corangamite": ("Corangamite", "Vic"),
    "corio": ("Corio", "Vic"),
    "deakin": ("Deakin", "Vic"),
    "dunkley": ("Dunkley", "Vic"),
    "flinders": ("Flinders", "Vic"),
    "fraser": ("Fraser", "Vic"),
    "gellibrand": ("Gellibrand", "Vic"),
    "gippsland": ("Gippsland", "Vic"),
    "goldstein": ("Goldstein", "Vic"),
    "gorton": ("Gorton", "Vic"),
    "hawke": ("Hawke", "Vic"),
    "holt": ("Holt", "Vic"),
    "hotham": ("Hotham", "Vic"),
    "indi": ("Indi", "Vic"),
    "isaacs": ("Isaacs", "Vic"),
    "jagajaga": ("Jagajaga", "Vic"),
    "kooyong": ("Kooyong", "Vic"),
    "la trobe": ("La Trobe", "Vic"),
    "lalor": ("Lalor", "Vic"),
    "macnamara": ("Macnamara", "Vic"),
    "mallee": ("Mallee", "Vic"),
    "maribyrnong": ("Maribyrnong", "Vic"),
    "mcewen": ("McEwen", "Vic"),
    "melbourne": ("Melbourne", "Vic"),
    "menzies": ("Menzies", "Vic"),
    "monash": ("Monash", "Vic"),
    "nicholls": ("Nicholls", "Vic"),
    "scullin": ("Scullin", "Vic"),
    "wannon": ("Wannon", "Vic"),
    "wills": ("Wills", "Vic"),
    "brand": ("Brand", "WA"),
    "bullwinkel": ("Bullwinkel", "WA"),
    "burt": ("Burt", "WA"),
    "canning": ("Canning", "WA"),
    "cowan": ("Cowan", "WA"),
    "curtin": ("Curtin", "WA"),
    "durack": ("Durack", "WA"),
    "forrest": ("Forrest", "WA"),
    "fremantle": ("Fremantle", "WA"),
    "hasluck": ("Hasluck", "WA"),
    "moore": ("Moore", "WA"),
    "o'connor": ("O'Connor", "WA"),
    "pearce": ("Pearce", "WA"),
    "perth": ("Perth", "WA"),
    "swan": ("Swan", "WA"),
    "tangney": ("Tangney", "WA"),
}


def slugify(text: str) -> str:
    """Produce deterministic, URL/identifier-safe lowercase slugs."""
    return re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()


def download_and_extract_aec_boundaries(
    target_dir: Path | str | None = None,
    force: bool = False,
    timeout: int = 120,
) -> Path:
    """Download and extract national 2025 AEC boundary Shapefile archive.

    Args:
        target_dir: Directory to extract shapefile into (defaults to data/raw/aec/2025).
        force: If True, re-download and re-extract even if files exist.
        timeout: HTTP request timeout in seconds.

    Returns:
        Path to the primary extracted .shp file.
    """
    raw_dir = Path(target_dir or RAW_AEC_2025_DIR).resolve()
    raw_dir.mkdir(parents=True, exist_ok=True)
    shp_path = raw_dir / "AUS_ELB_region.shp"

    if shp_path.exists() and not force:
        logger.info("Using cached AEC 2025 shapefile at %s", shp_path)
        return shp_path

    zip_path = raw_dir / "AUS-March-2025-esri.zip"
    logger.info(
        "Downloading AEC 2025 boundary zip from %s to %s",
        AEC_2025_SHAPEFILE_URL,
        zip_path,
    )

    response = requests.get(AEC_2025_SHAPEFILE_URL, stream=True, timeout=timeout)
    response.raise_for_status()

    with zip_path.open("wb") as f:
        for chunk in response.iter_content(chunk_size=65536):
            if chunk:
                f.write(chunk)

    logger.info("Extracting %s into %s", zip_path, raw_dir)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(raw_dir)

    if not shp_path.exists():
        raise FileNotFoundError(
            f"Expected shapefile not found after extracting AEC archive: {shp_path}"
        )

    return shp_path


def ingest_aec_boundaries(
    conn: duckdb.DuckDBPyConnection,
    shapefile_path: Path | str,
    election_year: int = 2025,
) -> int:
    """Ingest electoral boundaries from an ESRI Shapefile into DuckDB.

    Args:
        conn: Active DuckDB connection.
        shapefile_path: Path to ESRI .shp file.
        election_year: Target election year (default: 2025).

    Returns:
        Number of inserted boundary records.
    """
    path = Path(shapefile_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"AEC shapefile not found at: {path}")

    ensure_spatial(conn)

    # Read division name and binary WKB geometry from shapefile via ST_Read
    rows = conn.execute(
        "SELECT Elect_div, geom FROM ST_Read(?)", [str(path)]
    ).fetchall()

    inserted = 0
    for raw_div, geom_bytes in rows:
        norm_key = str(raw_div).strip().lower()
        if norm_key in DIVISION_METADATA_2025:
            canonical_name, state = DIVISION_METADATA_2025[norm_key]
        else:
            canonical_name = str(raw_div).strip()
            state = "Other"

        boundary_id = f"{election_year}-{slugify(canonical_name)}-{slugify(state)}"

        conn.execute(
            """
            INSERT OR REPLACE INTO electoral_boundaries (
                boundary_id,
                election_year,
                electorate,
                state_or_territory,
                geometry,
                source_url,
                source_dataset,
                retrieved_at
            ) VALUES (?, ?, ?, ?, ST_GeomFromWKB(?), ?, ?, ?)
            """,
            [
                boundary_id,
                election_year,
                canonical_name,
                state,
                bytes(geom_bytes),
                AEC_2025_SHAPEFILE_URL,
                AEC_2025_SOURCE_DATASET,
                AEC_2025_RETRIEVED_AT,
            ],
        )
        inserted += 1

    logger.info(
        "Ingested %d boundaries for election year %d from %s",
        inserted,
        election_year,
        path.name,
    )
    return inserted


def run_aec_ingestion(
    election_year: int = 2025,
    refresh: bool = False,
    db_path: Path | str | None = None,
    raw_dir: Path | str | None = None,
    export_parquet_files: bool = True,
    output_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Execute end-to-end AEC boundary ingestion pipeline.

    Args:
        election_year: Target election year (default: 2025).
        refresh: Force re-downloading AEC zip archive even if cached.
        db_path: Target DuckDB database path (defaults to data/aped.duckdb).
        raw_dir: Directory for cached raw shapefile archive.
        export_parquet_files: Export updated canonical tables to Parquet.
        output_dir: Destination directory for Parquet exports.

    Returns:
        Summary metrics dictionary.
    """
    effective_db_path = db_path or (DATA_DIR / "aped.duckdb")
    effective_out_dir = output_dir or PROCESSED_DIR

    shp_path = download_and_extract_aec_boundaries(
        target_dir=raw_dir or RAW_AEC_2025_DIR, force=refresh
    )

    conn = get_connection(effective_db_path)
    try:
        init_schema(conn)
        divisions_loaded = ingest_aec_boundaries(conn, shp_path, election_year)

        parquet_paths: dict[str, Path] = {}
        if export_parquet_files:
            parquet_paths = export_to_parquet(conn, effective_out_dir)
    finally:
        conn.close()

    return {
        "election_year": election_year,
        "divisions_loaded": divisions_loaded,
        "shapefile_path": str(shp_path),
        "source_dataset": AEC_2025_SOURCE_DATASET,
        "parquet_exported": bool(parquet_paths),
        "parquet_path": str(parquet_paths.get("electoral_boundaries", "")),
    }
