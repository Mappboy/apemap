"""Verify bounded survey facts against a locally retrieved source HTML file."""

from __future__ import annotations

import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from typing import Any


def normalized(value: str) -> str:
    return " ".join(value.replace("’", "'").replace("\xa0", " ").split())


class SurveyFields(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tag: str | None = None
        self.parts: list[str] = []
        self.person: str | None = None
        self.rows: dict[str, dict[str, str]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"h3", "p"}:
            self.tag = tag
            self.parts = []

    def handle_data(self, data: str) -> None:
        if self.tag:
            self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != self.tag:
            return
        text = normalized("".join(self.parts))
        if tag == "h3":
            self.person = re.sub(r"^Dr\s+", "", text)
        elif self.person and tag == "p":
            for label in ("Electorate", "School"):
                if text.startswith(label + " "):
                    field = label.lower()
                    row = self.rows.setdefault(self.person, {})
                    if field in row:
                        raise ValueError("Repeated survey field: " + self.person)
                    row[field] = text[len(label) + 1 :]
        self.tag = None
        self.parts = []


def extract(html: str, survey: dict[str, Any]) -> list[dict[str, Any]]:
    parser = SurveyFields()
    parser.feed(html)
    rows = {name: row for name, row in parser.rows.items() if "school" in row}
    expected = {row["name"] for row in survey["respondents"]}
    if set(rows) != expected:
        raise ValueError("Source respondent population differs from the saved survey")
    result = []
    for person in survey["respondents"]:
        row = rows[person["name"]]
        for claim in person["claims"]:
            if normalized(claim["name"]) not in row["school"]:
                raise ValueError(
                    "Reported school missing from source: " + claim["name"]
                )
            grades = claim.get("grade_levels")
            if grades:
                start = row["school"].find(normalized(claim["name"]))
                following = [
                    row["school"].find(normalized(other["name"]), start + 1)
                    for other in person["claims"]
                    if other is not claim
                ]
                end = min(
                    (position for position in following if position > start),
                    default=len(row["school"]),
                )
                context = (
                    row["school"]
                    if len(person["claims"]) == 1
                    else row["school"][start:end]
                )
                pattern = (
                    r"\byear\s+" + str(grades[0]) + r"\b"
                    if len(grades) == 1
                    else r"\bgrades\s+"
                    + str(grades[0])
                    + r"[-–]"
                    + str(grades[-1])
                    + r"\b"
                )
                if not re.search(pattern, context, re.IGNORECASE):
                    raise ValueError("Reported grade context missing from source")
        if (
            person.get("response_excerpt")
            and person["response_excerpt"] != row["school"]
        ):
            raise ValueError("Non-specific response differs from the source")
        result.append(
            {
                "name": person["name"],
                "aph_id": person["aph_id"],
                "electorate_as_reported": row["electorate"],
                "school_response_sha256": hashlib.sha256(
                    row["school"].encode()
                ).hexdigest(),
                "institution_names_as_reported": [
                    claim["name"] for claim in person["claims"]
                ],
                "response_kind": person["response_kind"],
                "locator": person["name"] + " profile / School field",
            }
        )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parent
    source = json.loads((root / "survey.json").read_bytes())
    payload = extract(arguments.html.read_text(encoding="utf-8"), source)
    arguments.out.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
