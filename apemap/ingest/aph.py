"""APH Handbook API client, cache manager, and individual record parser."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import requests

from apemap.constants import (
    APH_DEFAULT_ORDERBY,
    APH_HANDBOOK_API_ENDPOINT,
    DEFAULT_USER_AGENT,
    RAW_APH_DIR,
)
from apemap.ingest.http import create_retry_session
from apemap.ingest.service import reconstruct_services

logger = logging.getLogger(__name__)

USER_AGENT = DEFAULT_USER_AGENT


@dataclass
class MemberDemographics:
    member_id: str
    aph_id: str
    family_name: str
    given_name: str
    display_name: str
    gender: str | None
    date_of_birth: str | None
    wikidata_id: str | None = None


@dataclass
class ServiceStint:
    service_id: str
    member_id: str
    parliament_number: int
    chamber: str
    party: str
    party_abbrev: str
    electorate: str | None
    state_or_territory: str
    service_start: str | None
    service_end: str | None
    is_opening_day_member: bool
    is_current_member: bool
    source_url: str = ""
    source_service_start: str | None = None
    source_service_end: str | None = None
    retrieved_at: str | None = None


@dataclass
class ParsedIndividual:
    demographics: MemberDemographics
    services: list[ServiceStint] = field(default_factory=list)
    secondary_school_raw: str = ""
    bio_texts: list[str] = field(default_factory=list)
    source_url: str = ""
    service_reviews: list[dict[str, Any]] = field(default_factory=list)


class AphClient:
    """Client for querying and caching Parliamentary Handbook individuals data."""

    def __init__(
        self,
        cache_dir: Path | str | None = None,
        endpoint_url: str = APH_HANDBOOK_API_ENDPOINT,
        session: requests.Session | None = None,
        timeout: int = 60,
    ) -> None:
        self.cache_dir = Path(cache_dir or RAW_APH_DIR)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.endpoint_url = endpoint_url
        self.cache_file = self.cache_dir / "individuals.json"
        self.timeout = timeout
        self._owned_session = session is None
        self.session = session or create_retry_session(accept="application/json")

    def close(self) -> None:
        """Close client resources."""
        if self._owned_session:
            self.session.close()

    def __enter__(self) -> AphClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def fetch_individuals(self, refresh: bool = False) -> list[dict[str, Any]]:
        """Retrieve list of individuals from local disk cache or live API."""
        if not refresh and self.cache_file.exists():
            try:
                data = json.loads(self.cache_file.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data
                if isinstance(data, dict) and "value" in data:
                    return data["value"]
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(
                    "Failed to read cache at %s: %s. Fetching live.",
                    self.cache_file,
                    e,
                )
        if os.environ.get("APEMAP_OFFLINE") == "1":
            raise RuntimeError(
                f"APH cache file not found at {self.cache_file} and offline mode prevents querying live API: {self.endpoint_url}"
            )

        # Query live API
        params = {
            "$orderby": APH_DEFAULT_ORDERBY,
        }

        response = self.session.get(
            self.endpoint_url,
            params=params,
            timeout=self.timeout,
        )
        response.raise_for_status()
        res_json = response.json()

        individuals = res_json.get("value", [])
        # Cache to disk atomically
        temp_cache = self.cache_file.with_suffix(".tmp")
        temp_cache.write_text(
            json.dumps(individuals, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        temp_cache.replace(self.cache_file)
        return individuals


def parse_date(date_str: str | None) -> date | None:
    """Safely parse ISO date strings YYYY-MM-DD."""
    if not date_str or not isinstance(date_str, str):
        return None
    date_str = date_str.strip()
    if not date_str:
        return None
    try:
        # Check if full timestamp
        if "T" in date_str:
            date_str = date_str.split("T")[0]
        return datetime.strptime(date_str[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def normalize_chamber(raw_chamber: str | None, electorate: str | None) -> str:
    """Normalize chamber to 'representatives' or 'senate'."""
    if raw_chamber:
        raw_chamber = raw_chamber.lower()
        if "senat" in raw_chamber:
            return "senate"
        if "repres" in raw_chamber or "member" in raw_chamber:
            return "representatives"
    if electorate:
        return "representatives"
    return "senate"


def parse_individual(
    raw: dict[str, Any], target_parliaments: set[int] | None = None
) -> ParsedIndividual | None:
    """Parse raw APH individual JSON payload into domain models."""
    phid = str(raw.get("PHID", "")).strip()
    if not phid:
        return None

    member_id = f"aph-{phid.lower()}"
    family_name = str(raw.get("FamilyName", "")).strip().title() or "Unknown"
    given_name = str(raw.get("GivenName", "")).strip() or "Unknown"
    display_name = (
        str(raw.get("DisplayName", "")).strip() or f"{given_name} {family_name}"
    )

    gender_raw = raw.get("Gender")
    gender = str(gender_raw).strip() if gender_raw else None

    dob_val = parse_date(raw.get("DateOfBirth"))
    dob_str = dob_val.isoformat() if dob_val else None

    demographics = MemberDemographics(
        member_id=member_id,
        aph_id=phid,
        family_name=family_name,
        given_name=given_name,
        display_name=display_name,
        gender=gender,
        date_of_birth=dob_str,
    )

    # Extract biographical text entries for fallback school matching
    bio_texts: list[str] = []
    for field_name in (
        "Qualifications",
        "Occupations",
        "SecondaryOccupations",
        "Honours",
    ):
        vals = raw.get(field_name, [])
        if isinstance(vals, list):
            bio_texts.extend(str(v) for v in vals if v)

    secondary_school_raw = str(raw.get("SecondarySchool", "")).strip()
    source_url = f"{APH_HANDBOOK_API_ENDPOINT}?$filter=PHID eq '{phid}'"

    service_rows, reviews = reconstruct_services(raw, target_parliaments)
    services = [ServiceStint(**row) for row in service_rows]

    return ParsedIndividual(
        demographics=demographics,
        services=services,
        secondary_school_raw=secondary_school_raw,
        bio_texts=bio_texts,
        source_url=source_url,
        service_reviews=reviews,
    )
