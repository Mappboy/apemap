"""Constants and configuration for APEMAP parliament data and ingestion."""

from __future__ import annotations

from pathlib import Path
from typing import TypedDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_APH_DIR = DATA_DIR / "raw" / "aph"
RAW_WIKIMEDIA_DIR = DATA_DIR / "raw" / "wikimedia"
RAW_WIKIMEDIA_MEMBERS_DIR = RAW_WIKIMEDIA_DIR / "members"
RAW_WIKIMEDIA_INSTITUTIONS_DIR = RAW_WIKIMEDIA_DIR / "institutions"
RAW_AEC_DIR = DATA_DIR / "raw" / "aec"
RAW_AEC_2025_DIR = RAW_AEC_DIR / "2025"
PROCESSED_DIR = DATA_DIR / "processed"
EXTERNAL_DIR = DATA_DIR / "external"
REFERENCE_DIR = DATA_DIR / "reference"

ACARA_PROFILE_2025_URL = "https://dataandreporting.blob.core.windows.net/anrdataportal/Data-Access-Program/School%20Profile%202025.xlsx"
ACARA_PROFILE_LONGITUDINAL_URL = "https://dataandreporting.blob.core.windows.net/anrdataportal/Data-Access-Program/School%20Profile%202008-2025.xlsx"
ACARA_LOCATION_2025_URL = "https://dataandreporting.blob.core.windows.net/anrdataportal/Data-Access-Program/School%20Location%202025.xlsx"

AEC_2025_SHAPEFILE_URL = (
    "https://www.aec.gov.au/Electorates/files/2025/AUS-March-2025-esri.zip"
)
AEC_2025_SOURCE_DATASET = (
    "Australian Electoral Commission (AEC) 2025 Federal Electoral Boundaries "
    "(National ESRI Shapefile, 4 March 2025)"
)
AEC_2025_RETRIEVED_AT = "2025-03-04T00:00:00+11:00"

ABS_SCHOOLS_2025_URL = "https://www.abs.gov.au/statistics/people/education/schools/2025"
ABS_SCHOOLS_2025_TITLE = "Schools, 2025"
ABS_SCHOOLS_2025_RELEASED_AT = "2026-03-05T00:00:00+11:00"


class SectorBenchmark(TypedDict):
    student_enrolments: int
    student_enrolment_share: float


class AbsBenchmarkData(TypedDict):
    total_student_enrolments: int
    sectors: dict[str, SectorBenchmark]


# ABS Schools, 2025 official benchmark: student enrolments by school affiliation
ABS_BENCHMARK_2025: AbsBenchmarkData = {
    "total_student_enrolments": 4_160_918,
    "sectors": {
        "Government": {
            "student_enrolments": 2_613_404,
            "student_enrolment_share": 0.628,
        },
        "Catholic": {
            "student_enrolments": 831_692,
            "student_enrolment_share": 0.200,
        },
        "Independent": {
            "student_enrolments": 715_822,
            "student_enrolment_share": 0.172,
        },
    },
}


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

WIKIDATA_SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
WIKIPEDIA_API_ENDPOINT = "https://en.wikipedia.org/w/api.php"
WIKIDATA_API_ENDPOINT = "https://www.wikidata.org/w/api.php"
DEFAULT_WIKIMEDIA_TIMEOUT = 45

WIKIDATA_APH_ID_PROPERTY = "P10020"
WIKIDATA_DOB_PROPERTY = "P569"
WIKIDATA_GENDER_PROPERTY = "P21"
WIKIDATA_COORDINATES_PROPERTY = "P625"
WIKIDATA_COUNTRY_PROPERTY = "P17"
WIKIDATA_ADMIN_TERRITORY_PROPERTY = "P131"
WIKIDATA_INSTANCE_OF_PROPERTY = "P31"

CANONICAL_SECTORS = ("Government", "Catholic", "Independent", "Tertiary", "Other")
CANONICAL_CHAMBERS = ("representatives", "senate")
CANONICAL_LEVELS = ("secondary", "tertiary")
ATTENDED_STATUSES = (
    "graduated",
    "attended_did_not_graduate",
    "attended_unspecified",
)
CONFIDENCE_LEVELS = ("verified", "provisional", "unconfirmed")
