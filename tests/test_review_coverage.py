"""Coverage classifications for reviewed manual institutions."""

from __future__ import annotations

import pytest

from apemap import coverage
from apemap.db import get_connection, init_schema


@pytest.mark.unit
def test_overseas_coverage_counts_manual_countries_and_preserves_unknowns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(coverage, "backtest_finance_benchmarks", lambda conn: {})
    monkeypatch.setattr(
        coverage,
        "compute_school_finance_estimate",
        lambda conn, iid, **kwargs: {"status": "unavailable"},
    )
    schools = [
        ("manual:canadian-school", None, "Canada"),
        ("manual:australian-school", None, "Australia"),
        ("inst-overseas-legacy", None, "overseas"),
        ("inst-unmatched-no-country", None, None),
        ("manual:unknown-country", None, "unknown"),
        ("acara-10", "10", "Australia"),
    ]
    with get_connection() as conn:
        init_schema(conn)
        conn.execute(
            """INSERT INTO members(member_id,family_name,given_name,display_name)
            VALUES ('aph-example','Example','Member','Member Example')"""
        )
        conn.execute(
            """INSERT INTO parliament_service(
                service_id,member_id,parliament_number,chamber,party,party_abbrev,
                state_or_territory,is_opening_day_member
            ) VALUES ('service-example','aph-example',47,'senate','Independent','IND','TAS',TRUE)"""
        )
        conn.executemany(
            """INSERT INTO institutions(institution_id,acara_id,school_name,sector,country)
            VALUES (?,?,'Example School','Other',?)""",
            schools,
        )
        # Two assertions at the Canadian school still represent one overseas school.
        institution_ids = [row[0] for row in schools] + [schools[0][0]]
        conn.executemany(
            """INSERT INTO member_education(
                education_id,member_id,institution_id,level,attended_status,
                source_url,retrieved_at,confidence
            ) VALUES (?,'aph-example',?,'secondary','attended_unspecified',
                'https://example.org/attendance','2026-10-05T00:00:00Z','verified')""",
            [(f"education-{index}", iid) for index, iid in enumerate(institution_ids)],
        )
        result = coverage.compute_parliament_coverage(conn, [47])[0]
    assert result["overseas_schools"] == 2
    assert result["domestic_schools_matched_acara"] == 1
    assert result["represented_schools"] == 6
    assert result["secondary_education_assertions"] == 7
