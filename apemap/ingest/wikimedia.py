"""Wikipedia/Wikidata ingestion client, cache manager, and identity/school enrichment."""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from apemap.constants import (
    DEFAULT_WIKIMEDIA_TIMEOUT,
    PROCESSED_DIR,
    RAW_WIKIMEDIA_DIR,
    WIKIDATA_ADMIN_TERRITORY_PROPERTY,
    WIKIDATA_APH_ID_PROPERTY,
    WIKIDATA_COUNTRY_PROPERTY,
    WIKIDATA_DOB_PROPERTY,
    WIKIDATA_GENDER_PROPERTY,
    WIKIDATA_INSTANCE_OF_PROPERTY,
    WIKIDATA_SPARQL_ENDPOINT,
    WIKIPEDIA_API_ENDPOINT,
)
from apemap.db import get_connection
from apemap.ingest.matching import is_international_text, normalize_school_key
from apemap.ingest.review import (
    MEMBER_REVIEW_GENERATED_COLUMNS,
    MEMBER_REVIEW_MANUAL_COLUMNS,
    SCHOOL_REVIEW_GENERATED_COLUMNS,
    SCHOOL_REVIEW_MANUAL_COLUMNS,
    evaluate_school_candidate,
    load_historical_school_aliases,
    merge_review_rows,
)

logger = logging.getLogger(__name__)

USER_AGENT = "APEMAP/0.2.0 (Research data pipeline; https://github.com/Mappboy/apemap)"


def normalize_qid(value: str | None) -> str | None:
    """Normalize a Wikidata entity URL or identifier to a bare QID (e.g. Q4772000)."""
    if not value:
        return None
    match = re.search(r"(Q\d+)", str(value).strip())
    return match.group(1) if match else None


def normalize_date(value: str | None) -> str | None:
    """Extract standard ISO YYYY-MM-DD date representation."""
    if not value:
        return None
    match = re.search(r"(\d{4}-\d{2}-\d{2})", str(value).strip())
    return match.group(1) if match else None


def normalize_gender(value: str | None) -> str | None:
    """Normalize gender strings or Wikidata entity URIs/QIDs for deterministic cross-source comparisons."""
    if not value:
        return None
    val = str(value).strip().lower()
    if val in ("female", "woman", "q6581072") or val.endswith("/q6581072"):
        return "female"
    if val in ("male", "man", "q6581097") or val.endswith("/q6581097"):
        return "male"
    return val


def _sanitize_filename(name: str) -> str:
    """Convert arbitrary identifiers into safe filesystem filenames."""
    sanitized = re.sub(r"[^A-Za-z0-9_-]", "_", name)
    return sanitized[:120] if sanitized else "unknown"


class WikimediaClient:
    """Supported Wikimedia HTTP client with disk caching, bounded retries, and rate handling."""

    def __init__(
        self,
        cache_dir: Path | str | None = None,
        timeout: int = DEFAULT_WIKIMEDIA_TIMEOUT,
        rate_delay: float = 0.5,
        user_agent: str = USER_AGENT,
        session: requests.Session | None = None,
    ) -> None:
        self.cache_dir = Path(cache_dir or RAW_WIKIMEDIA_DIR)
        self.members_cache_dir = self.cache_dir / "members"
        self.institutions_cache_dir = self.cache_dir / "institutions"
        self.members_cache_dir.mkdir(parents=True, exist_ok=True)
        self.institutions_cache_dir.mkdir(parents=True, exist_ok=True)

        self.timeout = timeout
        self.rate_delay = rate_delay
        self.user_agent = user_agent

        if session is not None:
            self.session = session
        else:
            self.session = requests.Session()
            self.session.headers.update(
                {
                    "User-Agent": self.user_agent,
                    "Accept": "application/json",
                }
            )
            retries = Retry(
                total=3,
                read=3,
                connect=3,
                backoff_factor=1.5,
                status_forcelist=[429, 500, 502, 503, 504],
                raise_on_status=False,
                respect_retry_after_header=True,
            )
            adapter = HTTPAdapter(max_retries=retries)
            self.session.mount("https://", adapter)
            self.session.mount("http://", adapter)

    def _polite_delay(self) -> None:
        """Enforce polite rate limiting between consecutive network requests."""
        if self.rate_delay > 0:
            time.sleep(self.rate_delay)

    def lookup_member_by_aph_id(
        self, aph_id: str, refresh: bool = False
    ) -> dict[str, Any] | None:
        """Query Wikidata entity matching Parliament of Australia MP identifier (P10020)."""
        if not aph_id:
            return None

        clean_aph_id = aph_id.strip()
        cache_file = self.members_cache_dir / f"{_sanitize_filename(clean_aph_id)}.json"

        if not refresh and cache_file.exists():
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                return cached
            except Exception as exc:
                logger.warning("Failed to read member cache %s: %s", cache_file, exc)

        sparql_query = f"""
        SELECT ?item ?dob ?gender ?genderLabel ?article WHERE {{
          ?item wdt:{WIKIDATA_APH_ID_PROPERTY} "{clean_aph_id}" .
          OPTIONAL {{ ?item wdt:{WIKIDATA_DOB_PROPERTY} ?dob . }}
          OPTIONAL {{ ?item wdt:{WIKIDATA_GENDER_PROPERTY} ?gender . }}
          OPTIONAL {{
            ?article schema:about ?item ;
                     schema:isPartOf <https://en.wikipedia.org/> .
          }}
        }}
        """

        try:
            self._polite_delay()
            resp = self.session.get(
                WIKIDATA_SPARQL_ENDPOINT,
                params={"query": sparql_query, "format": "json"},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.error(
                "Wikidata SPARQL request failed for APH ID %s: %s", clean_aph_id, exc
            )
            return {
                "aph_id": clean_aph_id,
                "wikidata_id": None,
                "wikipedia_title": None,
                "wikipedia_url": None,
                "date_of_birth": None,
                "gender": None,
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "source_url": WIKIDATA_SPARQL_ENDPOINT,
                "source_query": sparql_query.strip(),
                "status": "error",
                "notes": f"Request failed: {exc}",
            }

        bindings = data.get("results", {}).get("bindings", [])
        retrieval_iso = datetime.now(timezone.utc).isoformat()

        if not bindings:
            payload: dict[str, Any] = {
                "aph_id": clean_aph_id,
                "wikidata_id": None,
                "wikipedia_title": None,
                "wikipedia_url": None,
                "date_of_birth": None,
                "gender": None,
                "retrieved_at": retrieval_iso,
                "source_url": resp.url,
                "source_query": sparql_query.strip(),
                "status": "not_found",
            }
            cache_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            return payload

        # Collect distinct QIDs
        raw_qids = {
            normalize_qid(b.get("item", {}).get("value"))
            for b in bindings
            if b.get("item", {}).get("value")
        }
        non_null_qids = sorted([q for q in raw_qids if q is not None])

        if len(non_null_qids) > 1:
            payload = {
                "aph_id": clean_aph_id,
                "wikidata_id": None,
                "wikipedia_title": None,
                "wikipedia_url": None,
                "date_of_birth": None,
                "gender": None,
                "retrieved_at": retrieval_iso,
                "source_url": resp.url,
                "source_query": sparql_query.strip(),
                "status": "conflict",
                "notes": f"Multiple Wikidata entities claim APH ID {clean_aph_id}: {non_null_qids}",
            }
            cache_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            return payload

        target_qid = non_null_qids[0] if non_null_qids else None

        dob_val: str | None = None
        gender_val: str | None = None
        article_url: str | None = None

        for b in bindings:
            if not dob_val and "dob" in b:
                dob_val = normalize_date(b["dob"].get("value"))
            if not gender_val and "gender" in b:
                gender_val = normalize_gender(b["gender"].get("value"))
            if not gender_val and "genderLabel" in b:
                gender_val = normalize_gender(b["genderLabel"].get("value"))
            if not article_url and "article" in b:
                article_url = b["article"].get("value")

        wiki_title: str | None = None
        if article_url:
            wiki_title = unquote(article_url.split("/wiki/")[-1]).replace("_", " ")

        payload = {
            "aph_id": clean_aph_id,
            "wikidata_id": target_qid,
            "wikipedia_title": wiki_title,
            "wikipedia_url": article_url,
            "date_of_birth": dob_val,
            "gender": gender_val,
            "retrieved_at": retrieval_iso,
            "source_url": resp.url,
            "source_query": sparql_query.strip(),
            "status": "matched",
        }
        cache_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    def lookup_member_by_name(
        self,
        display_name: str,
        dob: str | None = None,
        refresh: bool = False,
    ) -> dict[str, Any] | None:
        """Fallback lookup by human name confirming Australian political context."""
        if not display_name:
            return None

        clean_name = display_name.strip()
        safe_key = f"name_{_sanitize_filename(clean_name.lower())}"
        cache_file = self.members_cache_dir / f"{safe_key}.json"

        if not refresh and cache_file.exists():
            try:
                return json.loads(cache_file.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning(
                    "Failed to read member name cache %s: %s", cache_file, exc
                )

        escaped_name = clean_name.replace('"', '\\"')
        sparql_query = f"""
        SELECT ?item ?dob ?gender ?genderLabel ?article WHERE {{
          ?item rdfs:label "{escaped_name}"@en .
          ?item wdt:P31 wd:Q5 .
          OPTIONAL {{ ?item wdt:{WIKIDATA_DOB_PROPERTY} ?dob . }}
          OPTIONAL {{ ?item wdt:{WIKIDATA_GENDER_PROPERTY} ?gender . }}
          OPTIONAL {{
            ?article schema:about ?item ;
                     schema:isPartOf <https://en.wikipedia.org/> .
          }}
        }} LIMIT 10
        """

        try:
            self._polite_delay()
            resp = self.session.get(
                WIKIDATA_SPARQL_ENDPOINT,
                params={"query": sparql_query, "format": "json"},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.error("Wikidata name search failed for %s: %s", clean_name, exc)
            return {
                "display_name": clean_name,
                "wikidata_id": None,
                "wikipedia_title": None,
                "wikipedia_url": None,
                "date_of_birth": None,
                "gender": None,
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "source_url": WIKIDATA_SPARQL_ENDPOINT,
                "source_query": sparql_query.strip(),
                "status": "error",
                "notes": f"Request failed: {exc}",
            }

        bindings = data.get("results", {}).get("bindings", [])
        retrieval_iso = datetime.now(timezone.utc).isoformat()

        if not bindings:
            payload: dict[str, Any] = {
                "display_name": clean_name,
                "wikidata_id": None,
                "wikipedia_title": None,
                "wikipedia_url": None,
                "date_of_birth": None,
                "gender": None,
                "retrieved_at": retrieval_iso,
                "source_url": resp.url,
                "source_query": sparql_query.strip(),
                "status": "not_found",
            }
            cache_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            return payload

        # Group bindings by QID
        candidate_items: dict[str, dict[str, Any]] = {}
        for b in bindings:
            qid = normalize_qid(b.get("item", {}).get("value"))
            if not qid:
                continue
            if qid not in candidate_items:
                g_val = normalize_gender(
                    b.get("gender", {}).get("value")
                    or b.get("genderLabel", {}).get("value")
                )
                candidate_items[qid] = {
                    "qid": qid,
                    "dob": normalize_date(b.get("dob", {}).get("value")),
                    "gender": g_val,
                    "article": b.get("article", {}).get("value"),
                }

        qids = list(candidate_items.keys())
        norm_target_dob = normalize_date(dob)

        chosen_candidate: dict[str, Any] | None = None
        status = "ambiguous"
        notes = ""

        if len(qids) == 1:
            cand = candidate_items[qids[0]]
            if norm_target_dob and cand["dob"]:
                if cand["dob"] == norm_target_dob:
                    chosen_candidate = cand
                    status = "matched"
                    notes = f"Corroborated by birth date match {norm_target_dob}"
                else:
                    status = "ambiguous"
                    notes = f"Single candidate {qids[0]} has birth date {cand['dob']} differing from APH {norm_target_dob}"
            elif cand.get("article") and any(
                term in str(cand["article"]).lower()
                for term in (
                    "australian",
                    "politician",
                    "parliament",
                    "senator",
                    "member_of",
                )
            ):
                chosen_candidate = cand
                status = "matched"
                notes = (
                    "Corroborated by Australian political context in Wikipedia article"
                )
            else:
                chosen_candidate = cand
                status = "ambiguous"
                notes = (
                    f"Single candidate {qids[0]} found by name but lacks corroborating birth date "
                    "or parliamentary context; manual review required"
                )
        else:
            # Multiple candidates: check if exactly one matches birth date
            matching_dob = [
                c
                for c in candidate_items.values()
                if norm_target_dob and c["dob"] == norm_target_dob
            ]
            if len(matching_dob) == 1:
                chosen_candidate = matching_dob[0]
                status = "matched"
                notes = f"Disambiguated by birth date match {norm_target_dob}"
            else:
                status = "ambiguous"
                notes = f"Multiple candidate entities found ({qids}); manual review required"

        wiki_title = None
        if chosen_candidate and chosen_candidate.get("article"):
            wiki_title = unquote(
                chosen_candidate["article"].split("/wiki/")[-1]
            ).replace("_", " ")

        payload = {
            "display_name": clean_name,
            "wikidata_id": (
                chosen_candidate["qid"]
                if (chosen_candidate and status == "matched")
                else None
            ),
            "wikipedia_title": wiki_title,
            "wikipedia_url": (
                chosen_candidate.get("article") if chosen_candidate else None
            ),
            "date_of_birth": (
                chosen_candidate.get("dob") if chosen_candidate else None
            ),
            "gender": chosen_candidate.get("gender") if chosen_candidate else None,
            "retrieved_at": retrieval_iso,
            "source_url": resp.url,
            "source_query": sparql_query.strip(),
            "status": status,
            "notes": notes,
        }
        cache_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    def lookup_institution(
        self, school_name: str, refresh: bool = False
    ) -> dict[str, Any] | None:
        """Search Wikipedia/Wikidata for unmatched educational institution suggestions."""
        if not school_name:
            return None

        clean_name = school_name.strip()
        safe_key = _sanitize_filename(clean_name.lower())
        cache_file = self.institutions_cache_dir / f"{safe_key}.json"

        if not refresh and cache_file.exists():
            try:
                return json.loads(cache_file.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning(
                    "Failed to read institution cache %s: %s", cache_file, exc
                )

        retrieval_iso = datetime.now(timezone.utc).isoformat()

        # Step 1: Direct Wikipedia pageprops / coordinates query with redirects enabled
        wiki_params: dict[str, Any] = {
            "action": "query",
            "titles": clean_name,
            "prop": "pageprops|coordinates",
            "ppprop": "wikibase_item",
            "redirects": 1,
            "format": "json",
        }

        try:
            self._polite_delay()
            resp = self.session.get(
                WIKIPEDIA_API_ENDPOINT,
                params=wiki_params,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.error("Wikipedia query failed for '%s': %s", clean_name, exc)
            return {
                "raw_school_text": clean_name,
                "suggested_institution_name": None,
                "wikidata_id": None,
                "wikipedia_url": None,
                "country": None,
                "locality": None,
                "latitude": None,
                "longitude": None,
                "institution_type": None,
                "confidence": "unconfirmed",
                "retrieved_at": retrieval_iso,
                "source_url": WIKIPEDIA_API_ENDPOINT,
                "status": "error",
                "notes": f"Request failed: {exc}",
            }

        pages = data.get("query", {}).get("pages", {})
        page = next(iter(pages.values())) if pages else {}

        # If direct title was missing, try Wikipedia search
        if not page or "missing" in page or "pageprops" not in page:
            search_params = {
                "action": "query",
                "list": "search",
                "srsearch": clean_name,
                "srlimit": 1,
                "format": "json",
            }
            try:
                s_resp = self.session.get(
                    WIKIPEDIA_API_ENDPOINT,
                    params=search_params,
                    timeout=self.timeout,
                )
                s_resp.raise_for_status()
                s_data = s_resp.json()
                results = s_data.get("query", {}).get("search", [])
                if results:
                    found_title = results[0]["title"]
                    # Follow-up fetch pageprops for top search title
                    f_resp = self.session.get(
                        WIKIPEDIA_API_ENDPOINT,
                        params={
                            "action": "query",
                            "titles": found_title,
                            "prop": "pageprops|coordinates",
                            "ppprop": "wikibase_item",
                            "redirects": 1,
                            "format": "json",
                        },
                        timeout=self.timeout,
                    )
                    f_resp.raise_for_status()
                    f_pages = f_resp.json().get("query", {}).get("pages", {})
                    page = next(iter(f_pages.values())) if f_pages else {}
            except Exception as exc:
                logger.debug("Wikipedia search fallback failed: %s", exc)

        wikibase_item = page.get("pageprops", {}).get("wikibase_item")
        if not wikibase_item:
            payload: dict[str, Any] = {
                "raw_school_text": clean_name,
                "suggested_institution_name": None,
                "wikidata_id": None,
                "wikipedia_url": None,
                "country": None,
                "locality": None,
                "latitude": None,
                "longitude": None,
                "institution_type": None,
                "confidence": "unconfirmed",
                "retrieved_at": retrieval_iso,
                "source_url": resp.url,
                "notes": "No matching Wikipedia or Wikidata item located",
            }
            cache_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            return payload

        target_qid = normalize_qid(wikibase_item)
        target_title = page.get("title", clean_name)
        target_url = (
            f"https://en.wikipedia.org/wiki/{quote(target_title.replace(' ', '_'))}"
        )

        # Extract coordinates if present on page
        coords = page.get("coordinates", [])
        lat = coords[0].get("lat") if coords else None
        lon = coords[0].get("lon") if coords else None

        # Step 2: Fetch institution metadata from Wikidata
        country_name: str | None = None
        admin_locality: str | None = None
        inst_type: str | None = None

        if target_qid:
            detail_sparql = f"""
            SELECT ?countryLabel ?adminLabel ?typeLabel WHERE {{
              OPTIONAL {{
                wd:{target_qid} wdt:{WIKIDATA_COUNTRY_PROPERTY} ?country .
                ?country rdfs:label ?countryLabel FILTER(LANG(?countryLabel) = "en")
              }}
              OPTIONAL {{
                wd:{target_qid} wdt:{WIKIDATA_ADMIN_TERRITORY_PROPERTY} ?admin .
                ?admin rdfs:label ?adminLabel FILTER(LANG(?adminLabel) = "en")
              }}
              OPTIONAL {{
                wd:{target_qid} wdt:{WIKIDATA_INSTANCE_OF_PROPERTY} ?type .
                ?type rdfs:label ?typeLabel FILTER(LANG(?typeLabel) = "en")
              }}
            }} LIMIT 1
            """
            try:
                d_resp = self.session.get(
                    WIKIDATA_SPARQL_ENDPOINT,
                    params={"query": detail_sparql, "format": "json"},
                    timeout=self.timeout,
                )
                d_resp.raise_for_status()
                d_bindings = d_resp.json().get("results", {}).get("bindings", [])
                if d_bindings:
                    first = d_bindings[0]
                    if "countryLabel" in first:
                        country_name = first["countryLabel"].get("value")
                    if "adminLabel" in first:
                        admin_locality = first["adminLabel"].get("value")
                    if "typeLabel" in first:
                        inst_type = first["typeLabel"].get("value")
            except Exception as exc:
                logger.debug(
                    "Failed fetching entity details for %s: %s", target_qid, exc
                )

        payload = {
            "raw_school_text": clean_name,
            "suggested_institution_name": target_title,
            "wikidata_id": target_qid,
            "wikipedia_url": target_url,
            "country": country_name,
            "locality": admin_locality,
            "latitude": lat,
            "longitude": lon,
            "institution_type": inst_type,
            "confidence": "suggested",
            "retrieved_at": retrieval_iso,
            "source_url": resp.url,
            "notes": f"Derived from Wikipedia/Wikidata page {target_title} ({target_qid})",
        }
        cache_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload


def run_wikimedia_enrichment(
    parliaments: list[int] | None = None,
    refresh: bool = False,
    enrich_members: bool = True,
    enrich_schools: bool = True,
    db_path: Path | str | None = None,
    output_dir: Path | str | None = None,
    cache_dir: Path | str | None = None,
    timeout: int = DEFAULT_WIKIMEDIA_TIMEOUT,
    rate_delay: float = 0.5,
    client: WikimediaClient | None = None,
) -> dict[str, Any]:
    """Execute Wikimedia enrichment pipeline for canonical members and unmatched schools."""
    if parliaments is None:
        parliaments = [46, 47, 48]

    out_dir = Path(output_dir or PROCESSED_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    wm_client = client or WikimediaClient(
        cache_dir=cache_dir, timeout=timeout, rate_delay=rate_delay
    )
    conn = get_connection(db_path)

    member_reviews: list[dict[str, Any]] = []
    school_reviews: list[dict[str, Any]] = []

    members_processed = 0
    members_enriched = 0
    members_conflicts = 0
    member_discrepancies = 0
    schools_processed = 0
    schools_suggested = 0

    try:
        parl_placeholders = ", ".join(str(p) for p in parliaments)

        # -------------------------------------------------------------
        # Part 1: Member Enrichment & Demographic Cross-Checking
        # -------------------------------------------------------------
        if enrich_members:
            members_query = f"""
            SELECT DISTINCT
                m.member_id,
                m.aph_id,
                m.display_name,
                m.gender,
                CAST(m.date_of_birth AS VARCHAR) AS date_of_birth,
                m.wikidata_id
            FROM members m
            JOIN parliament_service ps ON m.member_id = ps.member_id
            WHERE ps.parliament_number IN ({parl_placeholders})
            ORDER BY m.display_name
            """
            member_rows = conn.execute(members_query).fetchall()
            members_to_update: dict[str, str] = {}

            for row in member_rows:
                members_processed += 1
                m_id, aph_id, disp_name, gender, dob, current_qid = row

                result: dict[str, Any] | None = None
                if aph_id:
                    result = wm_client.lookup_member_by_aph_id(aph_id, refresh=refresh)

                # Fallback to name search only if APH ID was missing or confirmed not found on Wikidata
                # (Do not fallback on network errors or timeouts to prevent compounding server load)
                if (
                    not aph_id or (result and result.get("status") == "not_found")
                ) and disp_name:
                    result = wm_client.lookup_member_by_name(
                        disp_name, dob=dob, refresh=refresh
                    )

                if not result:
                    continue

                status = result.get("status", "unknown")
                if status == "error":
                    logger.warning(
                        "Wikimedia enrichment skipped %s (%s) due to request error: %s",
                        disp_name,
                        aph_id,
                        result.get("notes"),
                    )
                    continue

                matched_qid = result.get("wikidata_id")
                wiki_title = result.get("wikipedia_title")
                wiki_url = result.get("wikipedia_url") or result.get("source_url")
                wiki_dob = result.get("date_of_birth")
                wiki_gender = result.get("gender")

                if status == "conflict":
                    members_conflicts += 1
                    member_reviews.append(
                        {
                            "member_id": m_id,
                            "aph_id": aph_id or "",
                            "display_name": disp_name,
                            "wikidata_id": "",
                            "field": "wikidata_id",
                            "aph_value": current_qid or "",
                            "wikidata_value": "",
                            "wikipedia_title": wiki_title or "",
                            "source_url": wiki_url or "",
                            "status": "conflict",
                            "notes": result.get("notes")
                            or "Multiple conflicting Wikidata entities",
                            "historical_value": current_qid or "",
                            "historical_source": "members.wikidata_id"
                            if current_qid
                            else "",
                            "review_status": "pending",
                            "resolved_value": "",
                            "manual_source_url": "",
                            "review_notes": "",
                        }
                    )
                elif status == "ambiguous":
                    member_reviews.append(
                        {
                            "member_id": m_id,
                            "aph_id": aph_id or "",
                            "display_name": disp_name,
                            "wikidata_id": "",
                            "field": "wikidata_id",
                            "aph_value": current_qid or "",
                            "wikidata_value": "",
                            "wikipedia_title": wiki_title or "",
                            "source_url": wiki_url or "",
                            "status": "ambiguous",
                            "notes": result.get("notes")
                            or "Ambiguous name match; manual review required",
                            "historical_value": current_qid or "",
                            "historical_source": "members.wikidata_id"
                            if current_qid
                            else "",
                            "review_status": "pending",
                            "resolved_value": "",
                            "manual_source_url": "",
                            "review_notes": "",
                        }
                    )
                elif status == "matched" and matched_qid:
                    # Queue database update if wikidata_id is not already set
                    if not current_qid:
                        members_to_update[m_id] = matched_qid
                        members_enriched += 1
                    elif current_qid != matched_qid:
                        members_conflicts += 1
                        member_reviews.append(
                            {
                                "member_id": m_id,
                                "aph_id": aph_id or "",
                                "display_name": disp_name,
                                "wikidata_id": current_qid,
                                "field": "wikidata_id",
                                "aph_value": current_qid,
                                "wikidata_value": matched_qid,
                                "wikipedia_title": wiki_title or "",
                                "source_url": wiki_url or "",
                                "status": "conflict",
                                "notes": f"Wikidata entity {matched_qid} conflicts with existing {current_qid}; preserved original",
                                "historical_value": current_qid,
                                "historical_source": "members.wikidata_id",
                                "review_status": "pending",
                                "resolved_value": "",
                                "manual_source_url": "",
                                "review_notes": "",
                            }
                        )

                    # Cross-check Date of Birth
                    if dob and wiki_dob:
                        norm_aph_dob = normalize_date(dob)
                        norm_wiki_dob = normalize_date(wiki_dob)
                        if norm_aph_dob != norm_wiki_dob:
                            member_discrepancies += 1
                            member_reviews.append(
                                {
                                    "member_id": m_id,
                                    "aph_id": aph_id or "",
                                    "display_name": disp_name,
                                    "wikidata_id": matched_qid,
                                    "field": "date_of_birth",
                                    "aph_value": str(dob),
                                    "wikidata_value": str(wiki_dob),
                                    "wikipedia_title": wiki_title or "",
                                    "source_url": wiki_url or "",
                                    "status": "discrepancy",
                                    "notes": "Date of birth discrepancy between APH and Wikidata",
                                    "historical_value": str(dob),
                                    "historical_source": "APH Parliamentary Handbook",
                                    "review_status": "pending",
                                    "resolved_value": "",
                                    "manual_source_url": "",
                                    "review_notes": "",
                                }
                            )
                    elif not dob and wiki_dob:
                        member_reviews.append(
                            {
                                "member_id": m_id,
                                "aph_id": aph_id or "",
                                "display_name": disp_name,
                                "wikidata_id": matched_qid,
                                "field": "date_of_birth",
                                "aph_value": "",
                                "wikidata_value": str(wiki_dob),
                                "wikipedia_title": wiki_title or "",
                                "source_url": wiki_url or "",
                                "status": "supplemental_available",
                                "notes": "Date of birth present in Wikidata but missing in APH record",
                                "historical_value": "",
                                "historical_source": "",
                                "review_status": "pending",
                                "resolved_value": "",
                                "manual_source_url": "",
                                "review_notes": "",
                            }
                        )

                    # Cross-check Gender
                    if gender and wiki_gender:
                        norm_aph_gender = normalize_gender(gender)
                        norm_wiki_gender = normalize_gender(wiki_gender)
                        if norm_aph_gender != norm_wiki_gender:
                            member_discrepancies += 1
                            member_reviews.append(
                                {
                                    "member_id": m_id,
                                    "aph_id": aph_id or "",
                                    "display_name": disp_name,
                                    "wikidata_id": matched_qid,
                                    "field": "gender",
                                    "aph_value": str(gender),
                                    "wikidata_value": str(wiki_gender),
                                    "wikipedia_title": wiki_title or "",
                                    "source_url": wiki_url or "",
                                    "status": "discrepancy",
                                    "notes": "Gender discrepancy between APH and Wikidata",
                                    "historical_value": str(gender),
                                    "historical_source": "APH Parliamentary Handbook",
                                    "review_status": "pending",
                                    "resolved_value": "",
                                    "manual_source_url": "",
                                    "review_notes": "",
                                }
                            )
                    elif not gender and wiki_gender:
                        member_reviews.append(
                            {
                                "member_id": m_id,
                                "aph_id": aph_id or "",
                                "display_name": disp_name,
                                "wikidata_id": matched_qid,
                                "field": "gender",
                                "aph_value": "",
                                "wikidata_value": str(wiki_gender),
                                "wikipedia_title": wiki_title or "",
                                "source_url": wiki_url or "",
                                "status": "supplemental_available",
                                "notes": "Gender present in Wikidata but missing in APH record",
                                "historical_value": "",
                                "historical_source": "",
                                "review_status": "pending",
                                "resolved_value": "",
                                "manual_source_url": "",
                                "review_notes": "",
                            }
                        )

            if members_to_update:
                # DuckDB limitation: UPDATE on a table referenced by foreign keys triggers
                # an internal delete-insert cycle that checks FK constraints. To update members
                # safely, backup referencing child tables to temp tables, detach rows, perform
                # the update, and restore referencing rows.
                conn.execute(
                    "CREATE TEMP TABLE _backup_ps AS SELECT * FROM parliament_service;"
                )
                conn.execute(
                    "CREATE TEMP TABLE _backup_me AS SELECT * FROM member_education;"
                )
                conn.execute("DELETE FROM parliament_service;")
                conn.execute("DELETE FROM member_education;")
                try:
                    for mem_id, qid in members_to_update.items():
                        conn.execute(
                            "UPDATE members SET wikidata_id = ? WHERE member_id = ?",
                            [qid, mem_id],
                        )
                finally:
                    conn.execute(
                        "INSERT INTO parliament_service SELECT * FROM _backup_ps;"
                    )
                    conn.execute(
                        "INSERT INTO member_education SELECT * FROM _backup_me;"
                    )
                    conn.execute("DROP TABLE _backup_ps;")
                    conn.execute("DROP TABLE _backup_me;")

        # -------------------------------------------------------------
        # Part 2: Unmatched-School Wikimedia Suggestions
        # -------------------------------------------------------------
        if enrich_schools:
            school_aliases = load_historical_school_aliases()
            schools_query = f"""
            SELECT
                i.institution_id,
                i.school_name,
                string_agg(DISTINCT me.confidence, '; ') AS confidence,
                string_agg(DISTINCT COALESCE(me.reviewer_notes, ''), '; ') AS reviewer_notes
            FROM institutions i
            JOIN member_education me ON i.institution_id = me.institution_id
            JOIN parliament_service ps ON me.member_id = ps.member_id
            WHERE (me.confidence = 'unconfirmed' OR i.acara_id IS NULL)
              AND ps.parliament_number IN ({parl_placeholders})
            GROUP BY i.institution_id, i.school_name
            ORDER BY i.school_name
            """
            school_rows = conn.execute(schools_query).fetchall()

            for inst_id, school_name, confidence, reviewer_notes in school_rows:
                schools_processed += 1

                # Lookup supporting historical evidence if present
                norm_key = normalize_school_key(school_name)
                hist_alias = school_aliases.get(norm_key) or school_aliases.get(
                    school_name.strip().lower()
                )
                hist_evidence: dict[str, Any] | None = None
                if hist_alias:
                    hist_evidence = {
                        "historical_match_name": hist_alias.get("canonical_name", ""),
                        "historical_acara_id": str(
                            hist_alias.get("canonical_acara_id", "")
                        ),
                        "historical_source": "data/reference/school_aliases.json",
                    }

                is_intl = is_international_text(school_name) or bool(
                    reviewer_notes and "international" in reviewer_notes.lower()
                )

                suggestion = wm_client.lookup_institution(school_name, refresh=refresh)
                if suggestion and suggestion.get("wikidata_id"):
                    accepted_for_review, reason = evaluate_school_candidate(
                        school_name,
                        suggestion,
                        is_international=is_intl,
                        historical_evidence=hist_evidence,
                    )
                    if not accepted_for_review:
                        logger.info(
                            "Filtered school candidate for '%s': %s",
                            school_name,
                            reason,
                        )
                        continue

                    schools_suggested += 1
                    school_reviews.append(
                        {
                            "institution_id": inst_id,
                            "raw_school_text": school_name,
                            "suggested_institution_name": (
                                suggestion.get("suggested_institution_name")
                                or school_name
                            ),
                            "wikidata_id": suggestion.get("wikidata_id") or "",
                            "wikipedia_url": suggestion.get("wikipedia_url") or "",
                            "country": suggestion.get("country") or "",
                            "locality": suggestion.get("locality") or "",
                            "latitude": (
                                suggestion.get("latitude")
                                if suggestion.get("latitude") is not None
                                else ""
                            ),
                            "longitude": (
                                suggestion.get("longitude")
                                if suggestion.get("longitude") is not None
                                else ""
                            ),
                            "institution_type": (
                                suggestion.get("institution_type") or ""
                            ),
                            "confidence": "suggested",
                            "notes": suggestion.get("notes") or "",
                            # Historical supporting evidence
                            "historical_match_name": (
                                hist_evidence["historical_match_name"]
                                if hist_evidence
                                else ""
                            ),
                            "historical_acara_id": (
                                hist_evidence["historical_acara_id"]
                                if hist_evidence
                                else ""
                            ),
                            "historical_source": (
                                hist_evidence["historical_source"]
                                if hist_evidence
                                else ""
                            ),
                            # Manual review columns with defaults
                            "review_status": "pending",
                            "resolved_school_name": "",
                            "resolved_acara_id": "",
                            "resolved_wikidata_id": "",
                            "resolved_country": "",
                            "resolved_state": "",
                            "resolved_suburb": "",
                            "resolved_postcode": "",
                            "resolved_address": "",
                            "resolved_latitude": "",
                            "resolved_longitude": "",
                            "manual_source_url": "",
                            "address_source_url": "",
                            "review_notes": "",
                        }
                    )

    finally:
        conn.close()

    # Write review artifacts preserving manual review decisions across runs
    member_review_path = out_dir / "wikimedia_member_review.csv"
    merge_review_rows(
        generated_rows=member_reviews,
        existing_path=member_review_path,
        key_columns=("member_id", "field"),
        generated_columns=MEMBER_REVIEW_GENERATED_COLUMNS,
        manual_columns=MEMBER_REVIEW_MANUAL_COLUMNS,
    )

    school_review_path = out_dir / "wikimedia_school_review.csv"
    merge_review_rows(
        generated_rows=school_reviews,
        existing_path=school_review_path,
        key_columns=("institution_id",),
        generated_columns=SCHOOL_REVIEW_GENERATED_COLUMNS,
        manual_columns=SCHOOL_REVIEW_MANUAL_COLUMNS,
    )

    return {
        "members_processed": members_processed,
        "members_enriched": members_enriched,
        "members_conflicts": members_conflicts,
        "member_discrepancies": member_discrepancies,
        "schools_processed": schools_processed,
        "schools_suggested": schools_suggested,
        "member_review_csv": str(member_review_path),
        "school_review_csv": str(school_review_path),
    }
