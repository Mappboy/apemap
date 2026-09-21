"""APH Handbook API client, cache manager, and individual record parser."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import requests

from apemap.constants import (
    APH_DEFAULT_ORDERBY,
    APH_HANDBOOK_API_ENDPOINT,
    PARLIAMENT_METADATA,
    RAW_APH_DIR,
)

logger = logging.getLogger(__name__)

USER_AGENT = "APEMAP/0.2.0 (Research data pipeline; https://github.com/Mappboy/apemap)"


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


@dataclass
class ParsedIndividual:
    demographics: MemberDemographics
    services: list[ServiceStint] = field(default_factory=list)
    secondary_school_raw: str = ""
    bio_texts: list[str] = field(default_factory=list)
    source_url: str = ""


class AphClient:
    """Client for querying and caching Parliamentary Handbook individuals data."""

    def __init__(
        self,
        cache_dir: Path | str | None = None,
        endpoint_url: str = APH_HANDBOOK_API_ENDPOINT,
    ) -> None:
        self.cache_dir = Path(cache_dir or RAW_APH_DIR)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.endpoint_url = endpoint_url
        self.cache_file = self.cache_dir / "individuals.json"

    def fetch_individuals(self, refresh: bool = False) -> list[dict[str, Any]]:
        """Retrieve list of individuals from local disk cache or live API."""
        if not refresh and self.cache_file.exists():
            try:
                data = json.loads(self.cache_file.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data
                if isinstance(data, dict) and "value" in data:
                    return data["value"]
            except Exception as e:
                logger.warning(
                    f"Failed to read cache at {self.cache_file}: {e}. Fetching live."
                )

        # Query live API
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }
        params = {
            "$orderby": APH_DEFAULT_ORDERBY,
        }

        response = requests.get(
            self.endpoint_url,
            headers=headers,
            params=params,
            timeout=60,
        )
        response.raise_for_status()
        res_json = response.json()

        individuals = res_json.get("value", [])
        # Cache to disk atomically
        self.cache_file.write_text(
            json.dumps(individuals, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
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

    # Determine parliaments served
    represented_parls: list[int] = []
    for p in raw.get("RepresentedParliaments", []):
        try:
            represented_parls.append(int(p))
        except (ValueError, TypeError):
            continue

    services: list[ServiceStint] = []
    parls_to_process = (
        represented_parls
        if target_parliaments is None
        else [p for p in represented_parls if p in target_parliaments]
    )

    # General metadata on state, party, electorate
    party = str(raw.get("Party", "")).strip() or "Independent"
    party_abbrev = str(raw.get("PartyAbbrev", "")).strip() or "IND"
    electorate = str(raw.get("Electorate", "")).strip() or None
    state_or_territory = str(
        raw.get("StateAbbrev") or raw.get("State") or "ACT"
    ).strip()
    if state_or_territory == "State" or not state_or_territory:
        state_or_territory = "Unknown"

    raw_mp_senator = raw.get("MPorSenator", [])
    chamber_hint = (
        "senate"
        if "Senator" in raw_mp_senator and "Member" not in raw_mp_senator
        else None
    )
    chamber = normalize_chamber(chamber_hint, electorate)

    overall_start = parse_date(raw.get("ServiceHistory_Start"))
    overall_end = parse_date(raw.get("ServiceHistory_End"))
    in_curr = str(raw.get("InCurrentParliament", "")).lower() == "true"

    for parl_num in parls_to_process:
        parl_info = PARLIAMENT_METADATA.get(parl_num)
        opening_d = parse_date(parl_info["opening_date"]) if parl_info else None
        end_d = parse_date(parl_info["end_date"]) if parl_info else None

        # Determine opening day membership:
        # Active if start <= opening_date and (end is null or end >= opening_date)
        is_opening = False
        if opening_d:
            st = overall_start or opening_d
            en = overall_end
            if st <= opening_d and (en is None or en >= opening_d):
                is_opening = True

        # Determine current member status
        is_current = False
        if parl_num == 48:
            is_current = in_curr or overall_end is None
        elif end_d:
            # For past parliaments, active on dissolution/end
            st = overall_start or opening_d
            en = overall_end
            if st and st <= end_d and (en is None or en >= end_d):
                is_current = True

        stint = ServiceStint(
            service_id=f"srv-{phid.lower()}-{parl_num}",
            member_id=member_id,
            parliament_number=parl_num,
            chamber=chamber,
            party=party,
            party_abbrev=party_abbrev,
            electorate=electorate,
            state_or_territory=state_or_territory,
            service_start=overall_start.isoformat()
            if overall_start
            else (opening_d.isoformat() if opening_d else None),
            service_end=overall_end.isoformat()
            if overall_end
            else (end_d.isoformat() if end_d else None),
            is_opening_day_member=is_opening,
            is_current_member=is_current,
        )
        services.append(stint)

    return ParsedIndividual(
        demographics=demographics,
        services=services,
        secondary_school_raw=secondary_school_raw,
        bio_texts=bio_texts,
        source_url=source_url,
    )
