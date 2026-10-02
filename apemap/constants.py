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

DEFAULT_USER_AGENT = (
    "APEMAP/0.2.0 (Research data pipeline; https://github.com/Mappboy/apemap)"
)

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
    parliament_number: int
    general_election_date: str
    opening_date: str
    end_date: str | None
    description: str
    expected_representatives: int
    expected_senators: int
    source_url: str


PARLIAMENT_CHRONOLOGY_URL = (
    "https://www.aph.gov.au/About_Parliament/House_of_Representatives/"
    "Powers_practice_and_procedure/00_-_Infosheets/"
    "Infosheet_25_-_Prorogation_and_dissolution"
)
TEMPORAL_WARNING = (
    "School profile and finance values describe the institution in their reporting "
    "year, not the resources available when the parliamentarian attended. "
    "Reviewed successor mappings describe the successor institution."
)


PARLIAMENT_METADATA: dict[int, ParliamentInfo] = {
    42: {
        "parliament_number": 42,
        "general_election_date": "2007-11-24",
        "opening_date": "2008-02-12",
        "end_date": "2010-07-19",
        "description": "42nd Parliament of Australia (2008-2010)",
        "expected_representatives": 150,
        "expected_senators": 76,
        "source_url": PARLIAMENT_CHRONOLOGY_URL,
    },
    43: {
        "parliament_number": 43,
        "general_election_date": "2010-08-21",
        "opening_date": "2010-09-28",
        "end_date": "2013-08-05",
        "description": "43rd Parliament of Australia (2010-2013)",
        "expected_representatives": 150,
        "expected_senators": 76,
        "source_url": PARLIAMENT_CHRONOLOGY_URL,
    },
    44: {
        "parliament_number": 44,
        "general_election_date": "2013-09-07",
        "opening_date": "2013-11-12",
        "end_date": "2016-05-09",
        "description": "44th Parliament of Australia (2013-2016)",
        "expected_representatives": 150,
        "expected_senators": 74,
        "source_url": PARLIAMENT_CHRONOLOGY_URL,
    },
    45: {
        "parliament_number": 45,
        "general_election_date": "2016-07-02",
        "opening_date": "2016-08-30",
        "end_date": "2019-04-11",
        "description": "45th Parliament of Australia (2016-2019)",
        "expected_representatives": 150,
        "expected_senators": 76,
        "source_url": PARLIAMENT_CHRONOLOGY_URL,
    },
    46: {
        "parliament_number": 46,
        "general_election_date": "2019-05-18",
        "opening_date": "2019-07-02",
        "end_date": "2022-04-11",
        "description": "46th Parliament of Australia (2019-2022)",
        "expected_representatives": 151,
        "expected_senators": 76,
        "source_url": PARLIAMENT_CHRONOLOGY_URL,
    },
    47: {
        "parliament_number": 47,
        "general_election_date": "2022-05-21",
        "opening_date": "2022-07-26",
        "end_date": "2025-03-28",
        "description": "47th Parliament of Australia (2022-2025)",
        "expected_representatives": 151,
        "expected_senators": 76,
        "source_url": PARLIAMENT_CHRONOLOGY_URL,
    },
    48: {
        "parliament_number": 48,
        "general_election_date": "2025-05-03",
        "opening_date": "2025-07-22",
        "end_date": None,
        "description": "48th Parliament of Australia (2025-present)",
        "expected_representatives": 150,
        "expected_senators": 76,
        "source_url": PARLIAMENT_CHRONOLOGY_URL,
    },
}


def supported_parliaments() -> list[int]:
    """Return supported terms in chronological order from the canonical metadata."""
    return sorted(PARLIAMENT_METADATA)


def current_parliament() -> int:
    """Return the single open parliament; reject inconsistent metadata."""
    current = [p for p, info in PARLIAMENT_METADATA.items() if info["end_date"] is None]
    if len(current) != 1:
        raise ValueError("Parliament metadata must identify exactly one current term")
    return current[0]


def select_parliaments(parliaments: list[int] | None = None) -> list[int]:
    """Resolve defaults and reject empty or unsupported selections."""
    values = (
        supported_parliaments() if parliaments is None else sorted(set(parliaments))
    )
    if not values or any(p not in PARLIAMENT_METADATA for p in values):
        raise ValueError(f"Select supported parliaments: {supported_parliaments()}")
    return values


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
