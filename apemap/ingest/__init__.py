"""APEMAP Ingestion subpackage."""

from apemap.ingest.aph import AphClient, parse_individual
from apemap.ingest.matching import SchoolMatcher, split_school_string
from apemap.ingest.pipeline import run_aph_ingestion

__all__ = [
    "AphClient",
    "SchoolMatcher",
    "parse_individual",
    "run_aph_ingestion",
    "split_school_string",
]
