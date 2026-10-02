"""Ingestion pipelines for public school funding and ACARA benchmark datasets.

Handles ingestion of:
- ACARA National Report on Schooling public finance benchmarks
- NSW Resource Allocation Model (RAM) government school funding
- Tasmania DECYP School Resource Package (Fairer Funding Model) allocations
- Northern Territory annual school resourcing and per-student funding formula rates
- Queensland non-state school recurrent grants
- Authoritative manual school funding disclosures (e.g. SA, WA, ACT annual reports)
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

if TYPE_CHECKING:
    from duckdb import DuckDBPyConnection

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
REFERENCE_DIR = PROJECT_ROOT / "data" / "reference"


def resolve_institution_id(
    conn: DuckDBPyConnection,
    acara_id: str | int | None = None,
    school_name: str | None = None,
    state: str | None = None,
) -> str | None:
    """Deterministically resolve an institution_id from ACARA ID or name/state.

    Resolution hierarchy:
    1. ACARA SML ID direct match (highest precedence).
    2. If state is supplied: match exact (LOWER(school_name), state).
       Must match exactly one institution; multiple or zero returns None.
       Never falls back to another state when state is specified.
    3. If state is NOT supplied: match exact LOWER(school_name).
       Must match exactly one institution; multiple (ambiguous) or zero returns None.

    Args:
        conn: Active DuckDB connection.
        acara_id: ACARA SML ID string or integer.
        school_name: Optional school name to match if ACARA ID is missing or unmatched.
        state: Optional state or territory to disambiguate school names.

    Returns:
        Matched institution_id or None if unmatched/ambiguous.
    """
    if acara_id is not None:
        aid_clean = str(acara_id).strip()
        if aid_clean.endswith(".0"):
            aid_clean = aid_clean[:-2]
        if aid_clean and aid_clean.lower() != "nan":
            # Direct ACARA ID lookup
            rows = conn.execute(
                "SELECT institution_id FROM institutions WHERE acara_id = ?",
                [aid_clean],
            ).fetchall()
            if len(rows) == 1:
                return str(rows[0][0])
            elif len(rows) > 1:
                logger.warning(
                    "Ambiguous ACARA ID %s matched multiple institutions", aid_clean
                )
                return None

    if school_name is not None:
        name_clean = school_name.strip()
        if name_clean:
            if state:
                state_clean = state.strip()
                rows = conn.execute(
                    """
                    SELECT institution_id FROM institutions
                    WHERE LOWER(school_name) = LOWER(?) AND state = ?
                    """,
                    [name_clean, state_clean],
                ).fetchall()
                if len(rows) == 1:
                    return str(rows[0][0])
                if len(rows) > 1:
                    logger.warning(
                        "Ambiguous school name '%s' in state '%s' matched %d institutions",
                        name_clean,
                        state_clean,
                        len(rows),
                    )
                # When state is supplied, do NOT fall back to matching outside the state
                return None

            # Name-only matching (when state is NOT supplied)
            rows = conn.execute(
                """
                SELECT institution_id FROM institutions
                WHERE LOWER(school_name) = LOWER(?)
                """,
                [name_clean],
            ).fetchall()
            if len(rows) == 1:
                return str(rows[0][0])
            elif len(rows) > 1:
                logger.warning(
                    "Ambiguous school name '%s' without state matched %d institutions",
                    name_clean,
                    len(rows),
                )
                return None

    return None


def ingest_acara_benchmarks(
    conn: DuckDBPyConnection,
    csv_path: Path | str | None = None,
    *,
    retrieved_at: datetime | None = None,
) -> int:
    """Ingest ACARA National Report on Schooling public finance benchmarks.

    Args:
        conn: Active DuckDB connection.
        csv_path: Optional path to benchmarks CSV (defaults to reference dataset).

    Returns:
        Number of benchmark rows ingested.
    """
    path = Path(
        csv_path or (REFERENCE_DIR / "acara_school_finance_benchmarks.csv")
    ).resolve()
    if not path.exists():
        logger.warning("ACARA benchmarks CSV not found at %s", path)
        return 0

    df = pd.read_csv(path)
    required_cols = {
        "reporting_year",
        "state_or_territory",
        "sector",
        "geolocation",
        "metric",
        "value",
    }
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"ACARA benchmarks CSV missing required columns: {missing}")

    records: list[tuple[Any, ...]] = []
    now_iso = (retrieved_at or datetime.now(timezone.utc)).isoformat()

    for _, row in df.iterrows():
        year = int(row["reporting_year"])
        state = str(row["state_or_territory"]).strip()
        sector = str(row["sector"]).strip()
        geo = str(row["geolocation"]).strip()
        metric = str(row["metric"]).strip()
        val = float(row["value"])
        unit = str(row.get("unit", "AUD_per_student")).strip()
        source_dataset = str(
            row.get("source_dataset", "ACARA National Report on Schooling")
        ).strip()
        source_url = str(
            row.get(
                "source_url",
                "https://www.acara.edu.au/reporting/national-report-on-schooling-in-australia/school-income",
            )
        ).strip()
        source_retrieved_at = str(row.get("retrieved_at", now_iso)).strip()

        records.append(
            (
                year,
                state,
                sector,
                geo,
                metric,
                val,
                unit,
                source_dataset,
                source_url,
                source_retrieved_at,
            )
        )

    conn.executemany(
        """
        INSERT INTO school_finance_benchmarks (
            reporting_year,
            state_or_territory,
            sector,
            geolocation,
            metric,
            value,
            unit,
            source_dataset,
            source_url,
            retrieved_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (reporting_year, state_or_territory, sector, geolocation, metric)
        DO UPDATE SET
            value = EXCLUDED.value,
            unit = EXCLUDED.unit,
            source_dataset = EXCLUDED.source_dataset,
            source_url = EXCLUDED.source_url,
            retrieved_at = EXCLUDED.retrieved_at
        """,
        records,
    )
    logger.info("Ingested %d ACARA finance benchmark records", len(records))
    return len(records)


def ingest_nsw_ram(
    conn: DuckDBPyConnection,
    csv_path: Path | str | None = None,
    *,
    retrieved_at: datetime | None = None,
) -> int:
    """Ingest Data.NSW Resource Allocation Model (RAM) school-level allocations.

    Preserves source-native RAM metric names and reporting year. State funding
    metrics are stored in school_public_funding and kept distinct from ACARA finance.

    Args:
        conn: Active DuckDB connection.
        csv_path: Optional path to NSW RAM CSV.

    Returns:
        Number of funding records inserted.
    """
    path = Path(csv_path or (REFERENCE_DIR / "nsw_ram_allocations.csv")).resolve()
    if not path.exists():
        logger.warning("NSW RAM allocations CSV not found at %s", path)
        return 0

    df = pd.read_csv(path)
    now_iso = (retrieved_at or datetime.now(timezone.utc)).isoformat()
    source_dataset = "Data.NSW Education Resource Allocation Model"
    source_url = (
        "https://data.nsw.gov.au/data/dataset/nsw-education-resource-allocation-model"
    )
    funding_model = "Resource Allocation Model (RAM)"

    metric_mapping = {
        "ram_allocation_total": ("AUD", "ram_allocation_total"),
        "ram_base_allocation": ("AUD", "ram_base_allocation"),
        "ram_equity_loading": ("AUD", "ram_equity_loading"),
        "ram_operational_funding": ("AUD", "ram_operational_funding"),
    }

    records: list[tuple[Any, ...]] = []
    unmatched: list[str] = []

    for _, row in df.iterrows():
        aid = row.get("acara_id")
        name = str(row.get("school_name", "")).strip()
        code = str(row.get("school_code", "")).strip()
        year = int(row.get("reporting_year", 2024))

        inst_id = resolve_institution_id(conn, aid, name, state="NSW")
        if not inst_id:
            unmatched.append(f"{name} (Code: {code}, ACARA: {aid})")
            continue

        for col, (unit, metric_name) in metric_mapping.items():
            if col in df.columns and pd.notna(row[col]):
                val = float(row[col])
                records.append(
                    (
                        inst_id,
                        year,
                        "NSW",
                        metric_name,
                        val,
                        unit,
                        funding_model,
                        source_dataset,
                        source_url,
                        code,
                        now_iso,
                    )
                )

    if unmatched:
        logger.warning(
            "NSW RAM ingestion: %d unmatched schools: %s",
            len(unmatched),
            ", ".join(unmatched[:5]),
        )

    if records:
        conn.executemany(
            """
            INSERT INTO school_public_funding (
                institution_id,
                reporting_year,
                jurisdiction,
                metric,
                value,
                unit,
                funding_model,
                source_dataset,
                source_url,
                source_record_id,
                retrieved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (institution_id, reporting_year, metric, source_dataset)
            DO UPDATE SET
                value = EXCLUDED.value,
                unit = EXCLUDED.unit,
                funding_model = EXCLUDED.funding_model,
                source_record_id = EXCLUDED.source_record_id,
                retrieved_at = EXCLUDED.retrieved_at
            """,
            records,
        )

    logger.info("Ingested %d NSW RAM public funding records", len(records))
    return len(records)


def ingest_tasmania_srp(
    conn: DuckDBPyConnection,
    csv_path: Path | str | None = None,
    *,
    retrieved_at: datetime | None = None,
) -> int:
    """Ingest Tasmania DECYP School Resource Package (Fairer Funding Model) allocations.

    Args:
        conn: Active DuckDB connection.
        csv_path: Optional path to Tasmania SRP CSV.

    Returns:
        Number of funding records inserted.
    """
    path = Path(csv_path or (REFERENCE_DIR / "tasmania_srp_allocations.csv")).resolve()
    if not path.exists():
        logger.warning("Tasmania SRP allocations CSV not found at %s", path)
        return 0

    df = pd.read_csv(path)
    now_iso = (retrieved_at or datetime.now(timezone.utc)).isoformat()
    source_dataset = "Tasmania DECYP School Resourcing Data"
    source_url = "https://www.decyp.tas.gov.au/about-us/policies-legislation-data/data-and-statistics/school-resourcing-data/"
    funding_model = "School Resource Package (Fairer Funding Model)"

    metric_mapping = {
        "srp_allocation_total": ("AUD", "srp_allocation_total"),
        "srp_core_staffing": ("AUD", "srp_core_staffing"),
        "srp_operational_allocation": ("AUD", "srp_operational_allocation"),
    }

    records: list[tuple[Any, ...]] = []
    unmatched: list[str] = []

    for _, row in df.iterrows():
        aid = row.get("acara_id")
        name = str(row.get("school_name", "")).strip()
        code = str(row.get("school_code", "")).strip()
        year = int(row.get("reporting_year", 2024))

        inst_id = resolve_institution_id(conn, aid, name, state="TAS")
        if not inst_id:
            unmatched.append(f"{name} (Code: {code}, ACARA: {aid})")
            continue

        for col, (unit, metric_name) in metric_mapping.items():
            if col in df.columns and pd.notna(row[col]):
                val = float(row[col])
                records.append(
                    (
                        inst_id,
                        year,
                        "TAS",
                        metric_name,
                        val,
                        unit,
                        funding_model,
                        source_dataset,
                        source_url,
                        code,
                        now_iso,
                    )
                )

    if unmatched:
        logger.warning(
            "Tasmania SRP ingestion: %d unmatched schools: %s",
            len(unmatched),
            ", ".join(unmatched),
        )

    if records:
        conn.executemany(
            """
            INSERT INTO school_public_funding (
                institution_id,
                reporting_year,
                jurisdiction,
                metric,
                value,
                unit,
                funding_model,
                source_dataset,
                source_url,
                source_record_id,
                retrieved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (institution_id, reporting_year, metric, source_dataset)
            DO UPDATE SET
                value = EXCLUDED.value,
                unit = EXCLUDED.unit,
                funding_model = EXCLUDED.funding_model,
                source_record_id = EXCLUDED.source_record_id,
                retrieved_at = EXCLUDED.retrieved_at
            """,
            records,
        )

    logger.info("Ingested %d Tasmania SRP public funding records", len(records))
    return len(records)


def ingest_nt_funding(
    conn: DuckDBPyConnection,
    csv_path: Path | str | None = None,
    *,
    retrieved_at: datetime | None = None,
) -> int:
    """Ingest Northern Territory School Needs Based Funding Formula data.

    Args:
        conn: Active DuckDB connection.
        csv_path: Optional path to NT school funding CSV.

    Returns:
        Number of funding records inserted.
    """
    path = Path(csv_path or (REFERENCE_DIR / "nt_school_funding.csv")).resolve()
    if not path.exists():
        logger.warning("NT school funding CSV not found at %s", path)
        return 0

    df = pd.read_csv(path)
    now_iso = (retrieved_at or datetime.now(timezone.utc)).isoformat()
    source_dataset = "NT Department of Education School Funding"
    source_url = "https://education.nt.gov.au/statistics-research-and-strategies/increasing-school-autonomy/school-funding"
    funding_model = "School Needs Based Funding Formula"

    metric_mapping = {
        "annual_school_resourcing_allocation": (
            "AUD",
            "annual_school_resourcing_allocation",
        ),
        "per_student_funding_rate": ("AUD_per_student", "per_student_funding_rate"),
    }

    records: list[tuple[Any, ...]] = []
    unmatched: list[str] = []

    for _, row in df.iterrows():
        aid = row.get("acara_id")
        name = str(row.get("school_name", "")).strip()
        year = int(row.get("reporting_year", 2024))

        inst_id = resolve_institution_id(conn, aid, name, state="NT")
        if not inst_id:
            unmatched.append(f"{name} (ACARA: {aid})")
            continue

        for col, (unit, metric_name) in metric_mapping.items():
            if col in df.columns and pd.notna(row[col]):
                val = float(row[col])
                records.append(
                    (
                        inst_id,
                        year,
                        "NT",
                        metric_name,
                        val,
                        unit,
                        funding_model,
                        source_dataset,
                        source_url,
                        str(aid) if aid else None,
                        now_iso,
                    )
                )

    if unmatched:
        logger.warning(
            "NT funding ingestion: %d unmatched schools: %s",
            len(unmatched),
            ", ".join(unmatched),
        )

    if records:
        conn.executemany(
            """
            INSERT INTO school_public_funding (
                institution_id,
                reporting_year,
                jurisdiction,
                metric,
                value,
                unit,
                funding_model,
                source_dataset,
                source_url,
                source_record_id,
                retrieved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (institution_id, reporting_year, metric, source_dataset)
            DO UPDATE SET
                value = EXCLUDED.value,
                unit = EXCLUDED.unit,
                funding_model = EXCLUDED.funding_model,
                source_record_id = EXCLUDED.source_record_id,
                retrieved_at = EXCLUDED.retrieved_at
            """,
            records,
        )

    logger.info("Ingested %d NT school public funding records", len(records))
    return len(records)


def ingest_qld_grants(
    conn: DuckDBPyConnection,
    csv_path: Path | str | None = None,
    *,
    retrieved_at: datetime | None = None,
) -> int:
    """Ingest Queensland State Recurrent Grant Scheme data for non-state schools.

    Args:
        conn: Active DuckDB connection.
        csv_path: Optional path to QLD grants CSV.

    Returns:
        Number of funding records inserted.
    """
    path = Path(csv_path or (REFERENCE_DIR / "qld_non_state_grants.csv")).resolve()
    if not path.exists():
        logger.warning("QLD non-state grants CSV not found at %s", path)
        return 0

    df = pd.read_csv(path)
    now_iso = (retrieved_at or datetime.now(timezone.utc)).isoformat()
    source_dataset = "Queensland Non-State Schools Recurrent Grants"
    source_url = "https://education.qld.gov.au/about-us/budgets-funding-grants/grants/non-state-school/state-recurrent-grant"
    funding_model = "State Recurrent Grant Scheme for Non-State Schools"

    metric_mapping = {
        "state_recurrent_grant_rate_secondary": (
            "AUD_per_student",
            "state_recurrent_grant_rate_secondary",
        ),
        "state_recurrent_grant_rate_primary": (
            "AUD_per_student",
            "state_recurrent_grant_rate_primary",
        ),
        "state_recurrent_grant_total": ("AUD", "state_recurrent_grant_total"),
    }

    records: list[tuple[Any, ...]] = []
    unmatched: list[str] = []

    for _, row in df.iterrows():
        aid = row.get("acara_id")
        name = str(row.get("school_name", "")).strip()
        year = int(row.get("reporting_year", 2024))

        inst_id = resolve_institution_id(conn, aid, name, state="QLD")
        if not inst_id:
            unmatched.append(f"{name} (ACARA: {aid})")
            continue

        for col, (unit, metric_name) in metric_mapping.items():
            if col in df.columns and pd.notna(row[col]):
                val = float(row[col])
                records.append(
                    (
                        inst_id,
                        year,
                        "QLD",
                        metric_name,
                        val,
                        unit,
                        funding_model,
                        source_dataset,
                        source_url,
                        str(aid) if aid else None,
                        now_iso,
                    )
                )

    if unmatched:
        logger.warning(
            "QLD grants ingestion: %d unmatched schools: %s",
            len(unmatched),
            ", ".join(unmatched),
        )

    if records:
        conn.executemany(
            """
            INSERT INTO school_public_funding (
                institution_id,
                reporting_year,
                jurisdiction,
                metric,
                value,
                unit,
                funding_model,
                source_dataset,
                source_url,
                source_record_id,
                retrieved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (institution_id, reporting_year, metric, source_dataset)
            DO UPDATE SET
                value = EXCLUDED.value,
                unit = EXCLUDED.unit,
                funding_model = EXCLUDED.funding_model,
                source_record_id = EXCLUDED.source_record_id,
                retrieved_at = EXCLUDED.retrieved_at
            """,
            records,
        )

    logger.info("Ingested %d QLD non-state grants records", len(records))
    return len(records)


def ingest_manual_school_funding(
    conn: DuckDBPyConnection,
    csv_path: Path | str | None = None,
    *,
    retrieved_at: datetime | None = None,
) -> int:
    """Ingest authoritative manual school funding records (e.g. from annual reports).

    Args:
        conn: Active DuckDB connection.
        csv_path: Optional path to manual school funding CSV.

    Returns:
        Number of funding records inserted.
    """
    path = Path(csv_path or (REFERENCE_DIR / "manual_school_funding.csv")).resolve()
    if not path.exists():
        logger.warning("Manual school funding CSV not found at %s", path)
        return 0

    df = pd.read_csv(path)
    now_iso = (retrieved_at or datetime.now(timezone.utc)).isoformat()
    records: list[tuple[Any, ...]] = []
    unmatched: list[str] = []

    for _, row in df.iterrows():
        aid = row.get("acara_id")
        year = int(row.get("reporting_year", 2024))
        metric = str(row.get("metric", "")).strip()
        val = float(row.get("value", 0.0))
        unit = str(row.get("unit", "AUD_per_student")).strip()
        src_url = str(row.get("source_url", "")).strip()
        src_type = str(row.get("source_type", "annual_report")).strip()
        source_retrieved_at = str(row.get("reviewed_at", now_iso)).strip()

        inst_id = resolve_institution_id(conn, aid)
        if not inst_id:
            unmatched.append(f"ACARA ID: {aid}")
            continue

        funding_model = f"Authoritative School Disclosure ({src_type})"
        source_dataset = f"School Published Disclosure ({src_type})"

        records.append(
            (
                inst_id,
                year,
                "School Published",
                metric,
                val,
                unit,
                funding_model,
                source_dataset,
                src_url,
                str(aid),
                source_retrieved_at,
            )
        )

    if unmatched:
        logger.warning(
            "Manual funding ingestion: %d unmatched schools: %s",
            len(unmatched),
            ", ".join(unmatched),
        )

    if records:
        conn.executemany(
            """
            INSERT INTO school_public_funding (
                institution_id,
                reporting_year,
                jurisdiction,
                metric,
                value,
                unit,
                funding_model,
                source_dataset,
                source_url,
                source_record_id,
                retrieved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (institution_id, reporting_year, metric, source_dataset)
            DO UPDATE SET
                value = EXCLUDED.value,
                unit = EXCLUDED.unit,
                funding_model = EXCLUDED.funding_model,
                source_record_id = EXCLUDED.source_record_id,
                retrieved_at = EXCLUDED.retrieved_at
            """,
            records,
        )

    logger.info("Ingested %d manual school funding records", len(records))
    return len(records)


def ingest_all_funding(
    conn: DuckDBPyConnection,
    benchmarks_path: Path | str | None = None,
    nsw_path: Path | str | None = None,
    tas_path: Path | str | None = None,
    nt_path: Path | str | None = None,
    qld_path: Path | str | None = None,
    manual_path: Path | str | None = None,
    *,
    retrieved_at: datetime | None = None,
) -> dict[str, int]:
    """Execute all public funding and ACARA benchmark ingestion routines.

    Args:
        conn: Active DuckDB connection.
        benchmarks_path: Optional custom path for ACARA benchmarks CSV.
        nsw_path: Optional custom path for NSW RAM CSV.
        tas_path: Optional custom path for TAS SRP CSV.
        nt_path: Optional custom path for NT funding CSV.
        qld_path: Optional custom path for QLD grants CSV.
        manual_path: Optional custom path for manual enrichment CSV.

    Returns:
        Dictionary summarizing ingested record counts by dataset.
    """
    counts = {
        "acara_benchmarks": ingest_acara_benchmarks(
            conn, benchmarks_path, retrieved_at=retrieved_at
        ),
        "nsw_ram": ingest_nsw_ram(conn, nsw_path, retrieved_at=retrieved_at),
        "tasmania_srp": ingest_tasmania_srp(conn, tas_path, retrieved_at=retrieved_at),
        "nt_funding": ingest_nt_funding(conn, nt_path, retrieved_at=retrieved_at),
        "qld_grants": ingest_qld_grants(conn, qld_path, retrieved_at=retrieved_at),
        "manual_enrichment": ingest_manual_school_funding(
            conn, manual_path, retrieved_at=retrieved_at
        ),
    }
    return counts
