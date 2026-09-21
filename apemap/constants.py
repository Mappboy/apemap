"""Constants and configuration for APEMAP parliament data and ingestion."""

from __future__ import annotations

from pathlib import Path
from typing import TypedDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_APH_DIR = DATA_DIR / "raw" / "aph"
PROCESSED_DIR = DATA_DIR / "processed"
EXTERNAL_DIR = DATA_DIR / "external"


class ParliamentInfo(TypedDict):
    opening_date: str
    end_date: str | None
    description: str


PARLIAMENT_METADATA: dict[int, ParliamentInfo] = {
    46: {
        "opening_date": "2019-07-02",
        "end_date": "2022-04-11",
        "description": "46th Parliament of Australia (2019-2022)",
    },
    47: {
        "opening_date": "2022-07-26",
        "end_date": "2025-04-11",
        "description": "47th Parliament of Australia (2022-2025)",
    },
    48: {
        "opening_date": "2025-07-22",
        "end_date": None,
        "description": "48th Parliament of Australia (2025-present)",
    },
}

APH_HANDBOOK_API_ENDPOINT = "https://handbookapi.aph.gov.au/api/individuals"
APH_DEFAULT_ORDERBY = "FamilyName,GivenName"

CANONICAL_SECTORS = ("Government", "Catholic", "Independent", "Tertiary", "Other")
CANONICAL_CHAMBERS = ("representatives", "senate")
CANONICAL_LEVELS = ("secondary", "tertiary")
ATTENDED_STATUSES = (
    "graduated",
    "attended_did_not_graduate",
    "attended_unspecified",
)
CONFIDENCE_LEVELS = ("verified", "provisional", "unconfirmed")
