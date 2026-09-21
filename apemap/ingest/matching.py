"""Secondary school extraction, text parsing, and ACARA institutional matching."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from apemap.constants import EXTERNAL_DIR

INTERNATIONAL_KEYWORDS = {
    "uk",
    "united kingdom",
    "england",
    "scotland",
    "wales",
    "ireland",
    "northern ireland",
    "usa",
    "united states",
    "america",
    "new zealand",
    "nz",
    "singapore",
    "malaysia",
    "hong kong",
    "south africa",
    "zimbabwe",
    "canada",
    "india",
    "switzerland",
    "germany",
    "france",
    "papua new guinea",
    "fiji",
    "philippines",
    "indonesia",
    "sri lanka",
    "kenya",
}

SCHOOL_KEYWORDS = (
    "school",
    "college",
    "grammar",
    "high",
    "academy",
    "secondary",
    "matriculation",
    "seminary",
)

TERTIARY_EXCLUSIONS = (
    "university",
    "bachelor",
    "master",
    "doctor",
    "phd",
    "diploma",
    "certificate",
    "institute of technology",
    "tafe",
    "electoral college",
    "college of law",
    "college of general practitioners",
    "college of surgeons",
    "college of physicians",
    "college of advanced education",
    "royal military college",
)


@dataclass
class MatchedInstitution:
    institution_id: str
    acara_id: str | None
    school_name: str
    school_type: str | None
    sector: str
    campus_type: str | None
    state: str | None
    suburb: str | None
    postcode: str | None
    longitude: float | None
    latitude: float | None
    confidence: str
    is_international: bool
    raw_input: str
    total_enrolments: int | None = None
    icsea: int | None = None


@dataclass
class UnmatchedRecord:
    parliament_number: int
    member_id: str
    member_display_name: str
    raw_school_text: str
    source_url: str
    is_international: bool
    notes: str


def normalize_text(text: str) -> str:
    """Normalize unicode and standardise whitespace and quotes."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_school_key(name: str) -> str:
    """Produce a canonical lookup key for school names."""
    norm = normalize_text(name).lower()
    norm = norm.replace("&", "and")
    norm = re.sub(r"\bst\.?\b", "saint", norm)
    norm = re.sub(r"\bmt\.?\b", "mount", norm)
    norm = re.sub(r"\bc of e\b", "church of england", norm)
    norm = re.sub(r"[^\w\s]", "", norm)
    norm = re.sub(r"\s+", " ", norm)
    return norm.strip()


def is_international_text(text: str) -> bool:
    """Check if the raw string indicates an international/overseas institution."""
    low = normalize_text(text).lower()
    for kw in INTERNATIONAL_KEYWORDS:
        if re.search(rf"\b{re.escape(kw)}\b", low):
            return True
    return False


def split_school_string(raw: str) -> list[str]:
    """Split a combined SecondarySchool field string into individual school names.

    Handles '/', ';', ' and ', and selective comma separation when separating schools.
    """
    cleaned = normalize_text(raw)
    if not cleaned:
        return []

    # First split on slashes and semicolons
    primary_parts = re.split(r"\s*[/;]\s*", cleaned)
    candidates: list[str] = []

    for part in primary_parts:
        part = part.strip()
        if not part:
            continue

        # Handle "School A and School B"
        if " and " in part.lower():
            subparts = re.split(r"\s+\band\b\s+", part, flags=re.IGNORECASE)
            # If both subparts contain school keywords, treat as separate
            if (
                len(subparts) == 2
                and any(k in subparts[0].lower() for k in SCHOOL_KEYWORDS)
                and any(k in subparts[1].lower() for k in SCHOOL_KEYWORDS)
            ):
                candidates.extend(s.strip() for s in subparts if s.strip())
                continue

        # Handle comma separation: "School A, School B" vs "School A, Suburb"
        if "," in part:
            comma_parts = [p.strip() for p in part.split(",") if p.strip()]
            # If multiple parts and at least two contain school keywords
            school_parts_count = sum(
                1 for cp in comma_parts if any(k in cp.lower() for k in SCHOOL_KEYWORDS)
            )
            if school_parts_count >= 2:
                candidates.extend(comma_parts)
                continue

        candidates.append(part)

    return [c for c in candidates if c]


LEAD_STRIP_PATTERNS = re.compile(
    r"^(?:attended|educated\s+at|educated|student\s+at|at|graduated\s+from|completed\s+(?:secondary\s+)?education\s+at|schooling\s+at)\s+",
    re.IGNORECASE,
)


def extract_schools_from_bio_text(texts: list[str]) -> list[str]:
    """Fallback scanner looking for secondary school names in biographical text entries."""
    candidates: list[str] = []
    pattern = re.compile(
        r"\b([A-Z][A-Za-z0-9\s'’-]+(?:High School|Secondary College|Senior College|State High School|Agricultural College|Boys High School|Girls High School|Grammar School))\b"
    )

    for item in texts:
        if not item or not isinstance(item, str):
            continue
        cleaned = normalize_text(item)
        # Skip if tertiary / degree
        low = cleaned.lower()
        if any(term in low for term in TERTIARY_EXCLUSIONS):
            continue

        matches = pattern.findall(cleaned)
        for match in matches:
            match_str = match.strip().rstrip(",.")
            match_str = LEAD_STRIP_PATTERNS.sub("", match_str).strip()
            match_low = match_str.lower()
            if not any(term in match_low for term in TERTIARY_EXCLUSIONS):
                if match_str and match_str not in candidates:
                    candidates.append(match_str)

    return candidates


class SchoolMatcher:
    """Matches candidate school names against canonical ACARA reference data."""

    def __init__(self, external_dir: Path | str | None = None) -> None:
        self.external_dir = Path(external_dir or EXTERNAL_DIR)
        self.location_file = self.external_dir / "school-location-2022.csv"
        self.profile_file = self.external_dir / "school-profile-2022.csv"

        self.exact_map: dict[str, dict[str, Any]] = {}
        self.norm_map: dict[str, dict[str, Any]] = {}
        self.acara_id_map: dict[str, dict[str, Any]] = {}
        self._load_reference_data()

    def _load_reference_data(self) -> None:
        if not self.location_file.exists():
            return

        loc_df = pd.read_csv(self.location_file, dtype=str)
        profile_df: pd.DataFrame | None = None
        if self.profile_file.exists():
            profile_df = pd.read_csv(self.profile_file, dtype=str)

        # Merge profile stats if available
        metrics: dict[str, tuple[int | None, int | None]] = {}
        if profile_df is not None and "ACARA SML ID" in profile_df.columns:
            for _, row in profile_df.iterrows():
                acara_id = str(row["ACARA SML ID"])
                icsea_val = None
                if (
                    pd.notna(row.get("ICSEA"))
                    and str(row.get("ICSEA")).strip().isdigit()
                ):
                    icsea_val = int(row["ICSEA"])
                enrol_val = None
                if (
                    pd.notna(row.get("Total Enrolments"))
                    and str(row.get("Total Enrolments")).strip().isdigit()
                ):
                    enrol_val = int(row["Total Enrolments"])
                metrics[acara_id] = (enrol_val, icsea_val)

        for _, row in loc_df.iterrows():
            acara_id = str(row["ACARA SML ID"])
            school_name = str(row["School Name"]).strip()
            sector = str(row["School Sector"]).strip()
            # Map sector to canonical
            if sector not in ("Government", "Catholic", "Independent"):
                sector = "Other"

            lat = float(row["Latitude"]) if pd.notna(row.get("Latitude")) else None
            lon = float(row["Longitude"]) if pd.notna(row.get("Longitude")) else None
            enrol, icsea = metrics.get(acara_id, (None, None))

            info: dict[str, Any] = {
                "acara_id": acara_id,
                "school_name": school_name,
                "sector": sector,
                "school_type": str(row.get("School Type", "")).strip() or None,
                "campus_type": str(row.get("Campus Type", "")).strip() or None,
                "state": str(row.get("State", "")).strip() or None,
                "suburb": str(row.get("Suburb", "")).strip() or None,
                "postcode": str(row.get("Postcode", "")).strip() or None,
                "latitude": lat,
                "longitude": lon,
                "total_enrolments": enrol,
                "icsea": icsea,
            }

            self.acara_id_map[acara_id] = info
            self.exact_map[school_name.lower()] = info
            norm_key = normalize_school_key(school_name)
            if norm_key:
                self.norm_map[norm_key] = info

    def match(self, raw_school: str) -> MatchedInstitution:
        """Attempt to match a raw school name against ACARA reference schools."""
        cleaned = normalize_text(raw_school)
        is_intl = is_international_text(cleaned)

        # 1. Exact match on raw cleaned string
        low = cleaned.lower()
        if low in self.exact_map:
            ref = self.exact_map[low]
            return MatchedInstitution(
                institution_id=f"acara-{ref['acara_id']}",
                acara_id=ref["acara_id"],
                school_name=ref["school_name"],
                school_type=ref["school_type"],
                sector=ref["sector"],
                campus_type=ref["campus_type"],
                state=ref["state"],
                suburb=ref["suburb"],
                postcode=ref["postcode"],
                longitude=ref["longitude"],
                latitude=ref["latitude"],
                confidence="verified",
                is_international=False,
                raw_input=raw_school,
                total_enrolments=ref["total_enrolments"],
                icsea=ref["icsea"],
            )

        # 2. Normalized key match
        norm_key = normalize_school_key(cleaned)
        if norm_key in self.norm_map:
            ref = self.norm_map[norm_key]
            return MatchedInstitution(
                institution_id=f"acara-{ref['acara_id']}",
                acara_id=ref["acara_id"],
                school_name=ref["school_name"],
                school_type=ref["school_type"],
                sector=ref["sector"],
                campus_type=ref["campus_type"],
                state=ref["state"],
                suburb=ref["suburb"],
                postcode=ref["postcode"],
                longitude=ref["longitude"],
                latitude=ref["latitude"],
                confidence="verified",
                is_international=False,
                raw_input=raw_school,
                total_enrolments=ref["total_enrolments"],
                icsea=ref["icsea"],
            )

        # 3. Location-aware stripping: e.g. "Wesley College, Melbourne" -> search "Wesley College"
        if "," in cleaned:
            base_part = cleaned.split(",")[0].strip()
            base_key = normalize_school_key(base_part)
            if base_key in self.norm_map:
                ref = self.norm_map[base_key]
                return MatchedInstitution(
                    institution_id=f"acara-{ref['acara_id']}",
                    acara_id=ref["acara_id"],
                    school_name=ref["school_name"],
                    school_type=ref["school_type"],
                    sector=ref["sector"],
                    campus_type=ref["campus_type"],
                    state=ref["state"],
                    suburb=ref["suburb"],
                    postcode=ref["postcode"],
                    longitude=ref["longitude"],
                    latitude=ref["latitude"],
                    confidence="provisional",
                    is_international=False,
                    raw_input=raw_school,
                    total_enrolments=ref["total_enrolments"],
                    icsea=ref["icsea"],
                )

        # 4. Unmatched / International provisional record
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", cleaned).strip("-").lower()
        if not slug:
            slug = "unknown"
        institution_id = f"inst-unmatched-{slug[:40]}"

        sector = "Other"
        return MatchedInstitution(
            institution_id=institution_id,
            acara_id=None,
            school_name=cleaned,
            school_type="Secondary",
            sector=sector,
            campus_type=None,
            state=None,
            suburb=None,
            postcode=None,
            longitude=None,
            latitude=None,
            confidence="unconfirmed",
            is_international=is_intl,
            raw_input=raw_school,
            total_enrolments=None,
            icsea=None,
        )
