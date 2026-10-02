"""Reconstruct dated APH representation without carrying current facts backwards."""

from __future__ import annotations

from datetime import date, timedelta
import hashlib
import json
from typing import Any

from apemap.constants import PARLIAMENT_METADATA, current_parliament, select_parliaments

STATE_CODES = {
    "new south wales": "NSW",
    "nsw": "NSW",
    "victoria": "VIC",
    "vic": "VIC",
    "queensland": "QLD",
    "qld": "QLD",
    "south australia": "SA",
    "sa": "SA",
    "western australia": "WA",
    "wa": "WA",
    "tasmania": "TAS",
    "tas": "TAS",
    "northern territory": "NT",
    "nt": "NT",
    "australian capital territory": "ACT",
    "act": "ACT",
}
PARTY_CODES = {
    "Australian Labor Party": "ALP",
    "Liberal Party of Australia": "LP",
    "The Nationals": "NP",
    "National Party of Australia": "NP",
    "Australian Greens": "GRN",
    "Independent": "IND",
    "Liberal National Party of Queensland": "LNP",
    "Australian Democrats": "AD",
    "Pauline Hanson's One Nation": "PHON",
    "One Nation": "ON",
}


def service_date(value: Any, *, end: bool = False) -> date | None:
    """Parse service dates, treating APH's open-end sentinel as missing."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = date.fromisoformat(value[:10])
    except ValueError:
        return None
    return None if end and parsed == date(1900, 1, 1) else parsed


def _active(item: dict[str, Any], point: date, start: str, end: str) -> bool:
    first = service_date(item.get(start))
    last = service_date(item.get(end), end=True)
    return first is not None and first <= point and (last is None or last >= point)


def _latest(
    items: list[dict[str, Any]], point: date, start: str, end: str
) -> dict[str, Any] | None:
    active = [item for item in items if _active(item, point, start, end)]
    return (
        max(active, key=lambda i: (str(i.get(start)), json.dumps(i, sort_keys=True)))
        if active
        else None
    )


def reconstruct_services(
    raw: dict[str, Any], parliaments: set[int] | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Intersect service and representation histories with supported parliament terms.

    APH shares transition dates between adjoining terms. The later start wins on
    that day; segments have inclusive ends and cannot overlap one another.
    """
    phid = str(raw.get("PHID", "")).lower()
    source_url = f"https://handbookapi.aph.gov.au/api/individuals?$filter=PHID eq '{raw.get('PHID')}'"
    selected = select_parliaments(
        list(parliaments) if parliaments is not None else None
    )
    histories = [
        i for i in raw.get("PartyParliamentaryService", []) if isinstance(i, dict)
    ]
    if not histories:
        histories = [
            {
                "DateStart": raw.get("ServiceHistory_Start"),
                "DateEnd": raw.get("ServiceHistory_End"),
                "SecondaryService": [],
            }
        ]
    electorates = [i for i in raw.get("ElectorateService", []) if isinstance(i, dict)]
    parties = [
        i
        for h in histories
        for i in (h.get("SecondaryService") or [])
        if isinstance(i, dict) and i.get("RoSType") == "Parties Represented"
    ]
    in_current = str(raw.get("InCurrentParliament", "")).lower() == "true"
    original_ends = {str(h.get("DateStart")): h.get("DateEnd") for h in histories}
    as_of = (
        service_date(raw.get("ServiceHistory_End"), end=True) if in_current else None
    )
    latest_start = max((str(i.get("DateStart") or "") for i in histories), default="")
    # Current records sometimes end at the API snapshot date rather than a departure.
    histories = [
        dict(h, DateEnd=None)
        if in_current and str(h.get("DateStart")) == latest_start
        else h
        for h in histories
    ]
    reviews: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    represented = {
        int(p) for p in raw.get("RepresentedParliaments", []) if str(p).isdigit()
    }
    for parliament in selected:
        if parliament not in represented:
            continue
        info = PARLIAMENT_METADATA[parliament]
        opening = date.fromisoformat(info["opening_date"])
        term_end = service_date(info["end_date"], end=True)
        boundaries = {opening}
        if term_end:
            boundaries.add(term_end + timedelta(days=1))
        for items, first_key, last_key in (
            (histories, "DateStart", "DateEnd"),
            (parties, "DateStart", "DateEnd"),
            (electorates, "ServiceStart", "ServiceEnd"),
        ):
            for item in items:
                first = service_date(item.get(first_key))
                last = service_date(item.get(last_key), end=True)
                for point in (first, last + timedelta(days=1) if last else None):
                    if (
                        point
                        and point > opening
                        and (term_end is None or point <= term_end + timedelta(days=1))
                        and (as_of is None or point <= as_of)
                    ):
                        boundaries.add(point)
        points = sorted(boundaries)
        segments: list[dict[str, Any]] = []
        for index, point in enumerate(points):
            if term_end and point > term_end:
                continue
            history = _latest(histories, point, "DateStart", "DateEnd")
            if history is None:
                continue
            seat = _latest(electorates, point, "ServiceStart", "ServiceEnd")
            chamber_hints = raw.get("MPorSenator", [])
            if seat:
                chamber = "representatives"
                electorate = seat.get("Electorate")
                state = seat.get("State")
            elif chamber_hints == ["Member"] and not electorates:
                chamber = "representatives"
                electorate = raw.get("Electorate") or None
                state = raw.get("StateAbbrev") or raw.get("State")
            elif "Senator" in chamber_hints:
                chamber = "senate"
                electorate = None
                state = (
                    raw.get("SenateState") or raw.get("StateAbbrev") or raw.get("State")
                )
            else:
                reviews.append(
                    {
                        "member_id": f"aph-{phid}",
                        "parliament_number": parliament,
                        "source_url": source_url,
                        "notes": "No dated electorate/chamber evidence",
                        "review_status": "needs_research",
                    }
                )
                continue
            party_record = _latest(parties, point, "DateStart", "DateEnd")
            if party_record:
                party = str(party_record.get("Value") or "Unknown")
            elif not parties:
                # Single-party biographies/fixtures can safely use their one affiliation.
                represented_parties = raw.get("RepresentedParties", [])
                party = (
                    str(raw.get("Party") or "Unknown")
                    if len(represented_parties) <= 1
                    else "Unknown"
                )
            else:
                party = "Unknown"
            finish = (
                points[index + 1] - timedelta(days=1)
                if index + 1 < len(points)
                else term_end
            )
            state_code = STATE_CODES.get(str(state or "").lower(), "Unknown")
            segment = {
                "member_id": f"aph-{phid}",
                "parliament_number": parliament,
                "chamber": chamber,
                "party": party,
                "party_abbrev": PARTY_CODES.get(
                    party,
                    str(raw.get("PartyAbbrev") or party)
                    if party == raw.get("Party")
                    else party,
                ),
                "electorate": electorate,
                "state_or_territory": state_code,
                "service_start": point.isoformat(),
                "service_end": finish.isoformat() if finish else None,
                "is_opening_day_member": point == opening,
                "is_current_member": False,
                "source_url": source_url,
                "source_service_start": history.get("DateStart"),
                "source_service_end": original_ends.get(str(history.get("DateStart"))),
            }
            context = ("chamber", "party", "electorate", "state_or_territory")
            if (
                segments
                and all(segments[-1][key] == segment[key] for key in context)
                and segments[-1]["service_end"]
                == (point - timedelta(days=1)).isoformat()
            ):
                segments[-1]["service_end"] = segment["service_end"]
                segments[-1]["source_service_end"] = segment["source_service_end"]
            else:
                segments.append(segment)
        if parliament == current_parliament() and in_current and segments:
            segments[-1]["is_current_member"] = True
        for segment in segments:
            if (
                segment["party"] == "Unknown"
                or segment["state_or_territory"] == "Unknown"
            ):
                reviews.append(
                    {
                        "member_id": f"aph-{phid}",
                        "parliament_number": parliament,
                        "source_url": source_url,
                        "notes": f"Missing dated representation at {segment['service_start']}",
                        "review_status": "needs_research",
                    }
                )
            identity = [
                segment[k]
                for k in (
                    "service_start",
                    "chamber",
                    "party",
                    "electorate",
                    "state_or_territory",
                )
            ]
            digest = hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:16]
            segment["service_id"] = f"srv-{phid}-{parliament}-{digest}"
            results.append(segment)
        if not segments:
            reviews.append(
                {
                    "member_id": f"aph-{phid}",
                    "parliament_number": parliament,
                    "source_url": source_url,
                    "notes": "No service interval overlaps this parliament",
                    "review_status": "needs_research",
                }
            )
    return results, reviews
