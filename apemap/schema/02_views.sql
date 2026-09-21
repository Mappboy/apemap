-- Canonical Views and Parameterized Macros for APEMAP
-- Eliminates duplicated physical tables (e.g., member_aph_46, member_aph_47)

-- Unified Parliament Members View
CREATE OR REPLACE VIEW v_parliament_members AS
SELECT
    ps.service_id,
    m.member_id,
    m.family_name,
    m.given_name,
    m.display_name,
    m.gender,
    m.date_of_birth,
    m.aph_id,
    m.wikidata_id,
    ps.parliament_number,
    ps.chamber,
    ps.party,
    ps.party_abbrev,
    ps.electorate,
    ps.state_or_territory,
    ps.service_start,
    ps.service_end,
    ps.is_opening_day_member,
    ps.is_current_member
FROM parliament_service ps
JOIN members m ON ps.member_id = m.member_id;

-- Unified Member Secondary Education View
CREATE OR REPLACE VIEW v_member_secondary_education AS
SELECT
    me.education_id,
    m.member_id,
    m.display_name,
    m.family_name,
    m.given_name,
    m.gender,
    m.date_of_birth,
    m.aph_id,
    m.wikidata_id,
    ps.service_id,
    ps.parliament_number,
    ps.chamber,
    ps.party,
    ps.party_abbrev,
    ps.electorate,
    ps.state_or_territory,
    ps.is_opening_day_member,
    ps.is_current_member,
    i.institution_id,
    i.acara_id,
    i.school_name,
    i.school_type,
    i.sector AS school_sector,
    i.campus_type,
    i.state AS school_state,
    i.suburb AS school_suburb,
    i.postcode AS school_postcode,
    i.longitude,
    i.latitude,
    me.level,
    me.years_attended,
    me.graduation_year,
    me.attended_status,
    me.source_url,
    me.retrieved_at,
    me.confidence,
    me.reviewer_notes,
    ss.snapshot_year,
    ss.total_enrolments,
    ss.icsea,
    ss.financial_profile_2021
FROM member_education me
JOIN members m ON me.member_id = m.member_id
JOIN parliament_service ps ON m.member_id = ps.member_id
JOIN institutions i ON me.institution_id = i.institution_id
LEFT JOIN school_snapshots ss ON i.institution_id = ss.institution_id
WHERE me.level = 'secondary';

-- DuckDB Parameterized Table Macros
CREATE OR REPLACE MACRO get_parliament_members(parl_num) AS TABLE
SELECT * FROM v_parliament_members WHERE parliament_number = parl_num;

CREATE OR REPLACE MACRO get_parliament_education(parl_num) AS TABLE
SELECT * FROM v_member_secondary_education WHERE parliament_number = parl_num;

-- Compatibility views for 46th and 47th parliaments (dynamically filtered)
CREATE OR REPLACE VIEW member_aph_46 AS
SELECT * FROM v_parliament_members WHERE parliament_number = 46;

CREATE OR REPLACE VIEW member_aph_47 AS
SELECT * FROM v_parliament_members WHERE parliament_number = 47;

CREATE OR REPLACE VIEW member_secondary_school_education_46 AS
SELECT * FROM v_member_secondary_education WHERE parliament_number = 46;

CREATE OR REPLACE VIEW member_secondary_school_education_47 AS
SELECT * FROM v_member_secondary_education WHERE parliament_number = 47;
