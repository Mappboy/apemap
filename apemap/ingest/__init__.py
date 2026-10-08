"""APEMAP Ingestion subpackage."""

from typing import TYPE_CHECKING, Any

from apemap.ingest.aph import AphClient, parse_individual
from apemap.ingest.matching import SchoolMatcher, split_school_string

if TYPE_CHECKING:
    from apemap.ingest.pipeline import run_aph_ingestion


def __getattr__(name: str) -> Any:
    # Review projection imports the parser/matcher while the pipeline consumes
    # that projection. Keep the public pipeline export without an import cycle.
    if name == "run_aph_ingestion":
        from apemap.ingest.pipeline import run_aph_ingestion

        return run_aph_ingestion
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AphClient",
    "SchoolMatcher",
    "parse_individual",
    "run_aph_ingestion",
    "split_school_string",
]
