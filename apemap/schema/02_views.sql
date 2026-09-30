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

-- Opening Day Baseline Members View
CREATE OR REPLACE VIEW v_parliament_members_opening AS
SELECT * FROM v_parliament_members WHERE is_opening_day_member = TRUE;

-- Current Members View
CREATE OR REPLACE VIEW v_parliament_members_current AS
SELECT * FROM v_parliament_members WHERE is_current_member = TRUE;

-- Backward-compatibility view for 2021 financial baseline
CREATE OR REPLACE VIEW school_finances_2021 AS
SELECT
    institution_id,
    acara_id,
    recurrent_funding_gov_total,
    recurrent_funding_state_total,
    fees_charges_parent_total,
    other_private_sources_total,
    total_gross_income_total,
    total_net_recurrent_income_total,
    recurrent_funding_gov_per_student,
    recurrent_funding_state_per_student,
    fees_charges_parent_per_student,
    other_private_sources_per_student,
    total_gross_income_per_student,
    total_net_recurrent_income_per_student,
    reporting_year
FROM school_finances
WHERE reporting_year = 2021;

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
    ss.girls_enrolments,
    ss.boys_enrolments,
    ss.fte_enrolments,
    ss.icsea,
    ss.icsea_percentile,
    ss.sea_bottom_quarter_pct,
    ss.sea_lower_middle_quarter_pct,
    ss.sea_upper_middle_quarter_pct,
    ss.sea_top_quarter_pct,
    ss.indigenous_enrolments_pct,
    ss.lbote_pct,
    ss.year_range,
    ss.remoteness_category,
    ss.financial_profile_2021,
    sf.recurrent_funding_gov_total AS historical_2021_gov_funding_total,
    sf.fees_charges_parent_total AS historical_2021_fees_parent_total,
    sf.total_net_recurrent_income_total AS historical_2021_net_recurrent_income_total,
    sf.total_net_recurrent_income_per_student AS historical_2021_net_recurrent_income_per_student
FROM member_education me
JOIN members m ON me.member_id = m.member_id
JOIN parliament_service ps ON m.member_id = ps.member_id
JOIN institutions i ON me.institution_id = i.institution_id
LEFT JOIN school_snapshots ss ON i.institution_id = ss.institution_id
LEFT JOIN school_finances_2021 sf ON i.institution_id = sf.institution_id
WHERE me.level = 'secondary';

-- DuckDB Parameterized Table Macros
CREATE OR REPLACE MACRO get_parliament_members(parl_num) AS TABLE
SELECT * FROM v_parliament_members WHERE parliament_number = parl_num;

CREATE OR REPLACE MACRO get_parliament_education(parl_num) AS TABLE
SELECT * FROM v_member_secondary_education WHERE parliament_number = parl_num;

-- Compatibility views for 46th, 47th, and 48th parliaments (dynamically filtered)
CREATE OR REPLACE VIEW member_aph_46 AS
SELECT * FROM v_parliament_members WHERE parliament_number = 46;

CREATE OR REPLACE VIEW member_aph_47 AS
SELECT * FROM v_parliament_members WHERE parliament_number = 47;

CREATE OR REPLACE VIEW member_aph_48 AS
SELECT * FROM v_parliament_members WHERE parliament_number = 48;

CREATE OR REPLACE VIEW member_secondary_school_education_46 AS
SELECT * FROM v_member_secondary_education WHERE parliament_number = 46;

CREATE OR REPLACE VIEW member_secondary_school_education_47 AS
SELECT * FROM v_member_secondary_education WHERE parliament_number = 47;

CREATE OR REPLACE VIEW member_secondary_school_education_48 AS
SELECT * FROM v_member_secondary_education WHERE parliament_number = 48;

-- Coverage and Gap Metrics View
CREATE OR REPLACE VIEW v_coverage_metrics AS
SELECT
    ps.parliament_number,
    COUNT(DISTINCT ps.member_id) AS total_parliamentarians,
    COUNT(DISTINCT CASE WHEN ps.is_opening_day_member THEN ps.member_id END) AS opening_day_parliamentarians,
    COUNT(DISTINCT CASE WHEN ps.is_current_member THEN ps.member_id END) AS current_parliamentarians,
    COUNT(DISTINCT CASE WHEN me.confidence IN ('verified', 'provisional') THEN me.education_id END) AS matched_education_records,
    COUNT(DISTINCT CASE WHEN me.confidence = 'unconfirmed' THEN me.education_id END) AS unmatched_education_records,
    COUNT(DISTINCT CASE WHEN i.sector = 'Government' THEN me.education_id END) AS government_records,
    COUNT(DISTINCT CASE WHEN i.sector = 'Catholic' THEN me.education_id END) AS catholic_records,
    COUNT(DISTINCT CASE WHEN i.sector = 'Independent' THEN me.education_id END) AS independent_records,
    COUNT(DISTINCT CASE WHEN i.sector NOT IN ('Government', 'Catholic', 'Independent') THEN me.education_id END) AS other_unclassified_records
FROM parliament_service ps
LEFT JOIN member_education me ON ps.member_id = me.member_id AND me.level = 'secondary'
LEFT JOIN institutions i ON me.institution_id = i.institution_id
GROUP BY ps.parliament_number
ORDER BY ps.parliament_number;

-- House Electorates Spatial Link View
-- Connects House of Representatives service records to canonical electoral boundary polygons
CREATE OR REPLACE VIEW v_house_electorates AS
SELECT
    ps.service_id,
    ps.member_id,
    m.display_name,
    m.family_name,
    m.given_name,
    ps.parliament_number,
    ps.chamber,
    ps.party,
    ps.party_abbrev,
    ps.electorate,
    ps.state_or_territory,
    ps.service_start,
    ps.service_end,
    ps.is_opening_day_member,
    ps.is_current_member,
    eb.boundary_id,
    eb.election_year,
    eb.geometry,
    eb.source_url AS boundary_source_url,
    eb.source_dataset AS boundary_source_dataset,
    eb.retrieved_at AS boundary_retrieved_at
FROM parliament_service ps
JOIN members m ON ps.member_id = m.member_id
JOIN electoral_boundaries eb ON (
    LOWER(ps.electorate) = LOWER(eb.electorate)
    AND eb.election_year = CASE
        WHEN ps.parliament_number = 48 THEN 2025
        WHEN ps.parliament_number = 47 THEN 2022
        WHEN ps.parliament_number = 46 THEN 2019
        ELSE 2025
    END
)
WHERE ps.chamber = 'representatives';

-- Unified School Public Funding View
CREATE OR REPLACE VIEW v_school_public_funding AS
SELECT
    spf.institution_id,
    spf.reporting_year,
    spf.jurisdiction,
    spf.metric,
    spf.value,
    spf.unit,
    spf.funding_model,
    spf.source_dataset,
    spf.source_url,
    spf.source_record_id,
    spf.retrieved_at,
    i.acara_id,
    i.school_name,
    i.school_type,
    i.sector AS school_sector,
    i.state AS school_state,
    i.suburb AS school_suburb,
    i.postcode AS school_postcode,
    i.longitude,
    i.latitude
FROM school_public_funding spf
JOIN institutions i ON spf.institution_id = i.institution_id;

-- Unified School Finance Benchmarks View
CREATE OR REPLACE VIEW v_school_finance_benchmarks AS
SELECT
    reporting_year,
    state_or_territory,
    sector,
    geolocation,
    metric,
    value,
    unit,
    source_dataset,
    source_url,
    retrieved_at
FROM school_finance_benchmarks
ORDER BY reporting_year DESC, state_or_territory, sector, geolocation, metric;
