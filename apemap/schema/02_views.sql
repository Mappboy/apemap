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

-- One attendance row, with original-school facts separate from reporting metadata.
-- School-wide evidence is grouped only by explicit original identity or its
-- frozen recorded-name review identity. Contradictions never choose a winner.
CREATE OR REPLACE VIEW v_education_attendance_context AS
WITH identities AS (
    SELECT e.*,
        r.institution_id AS resolved_institution_id,
        r.school_name AS resolved_institution_name,
        r.longitude AS resolved_longitude, r.latitude AS resolved_latitude,
        r.sector AS resolved_sector,
        COALESCE(e.institution_resolution = 'successor', FALSE) AS is_successor,
        CASE
            WHEN e.institution_resolution = 'successor' THEN
                COALESCE(e.attended_institution_id, e.recorded_school_id)
            WHEN e.institution_resolution = 'unresolved' THEN e.recorded_school_id
            ELSE e.institution_id
        END AS attended_school_id,
        CASE
            WHEN e.institution_resolution = 'successor' THEN
                COALESCE(a.school_name, NULLIF(TRIM(e.school_name_as_recorded), ''))
            WHEN e.institution_resolution = 'unresolved' THEN
                NULLIF(TRIM(e.school_name_as_recorded), '')
            ELSE r.school_name
        END AS attended_school_name,
        CASE
            WHEN e.institution_resolution = 'successor' AND e.attended_institution_id IS NOT NULL
                THEN 'original_verified'
            WHEN e.institution_resolution IN ('successor', 'unresolved') AND e.recorded_school_id IS NOT NULL
                THEN 'recorded_name_provisional'
            WHEN e.institution_resolution IN ('successor', 'unresolved') THEN 'unresolved'
            ELSE 'original_reference'
        END AS identity_basis
    FROM member_education e
    JOIN institutions r ON e.institution_id = r.institution_id
    LEFT JOIN institutions a ON e.attended_institution_id = a.institution_id
), consensus AS (
    SELECT attended_school_id,
        COUNT(DISTINCT CASE WHEN historical_scope_confirmed AND historical_location_source_url IS NOT NULL
            AND historical_longitude IS NOT NULL AND historical_latitude IS NOT NULL
            THEN STRUCT_PACK(longitude := historical_longitude, latitude := historical_latitude) END) > 1
            AS original_location_conflict,
        MIN(historical_longitude) FILTER (WHERE historical_scope_confirmed AND historical_location_source_url IS NOT NULL AND historical_longitude IS NOT NULL AND historical_latitude IS NOT NULL) AS original_longitude,
        MIN(historical_latitude) FILTER (WHERE historical_scope_confirmed AND historical_location_source_url IS NOT NULL AND historical_longitude IS NOT NULL AND historical_latitude IS NOT NULL) AS original_latitude,
        MIN(historical_location_source_url) FILTER (WHERE historical_scope_confirmed) AS original_location_source,
        COUNT(DISTINCT campus_continuity) FILTER (WHERE historical_scope_confirmed AND campus_continuity_source_url IS NOT NULL) > 1 AS campus_conflict,
        MIN(campus_continuity) FILTER (WHERE historical_scope_confirmed AND campus_continuity_source_url IS NOT NULL) AS campus,
        MIN(campus_continuity_source_url) FILTER (WHERE historical_scope_confirmed) AS campus_source,
        COUNT(DISTINCT STRUCT_PACK(longitude := resolved_longitude, latitude := resolved_latitude))
            FILTER (WHERE is_successor AND resolved_longitude IS NOT NULL AND resolved_latitude IS NOT NULL) > 1 AS successor_location_conflict,
        MIN(resolved_longitude) FILTER (WHERE is_successor AND resolved_longitude IS NOT NULL AND resolved_latitude IS NOT NULL) AS successor_longitude,
        MIN(resolved_latitude) FILTER (WHERE is_successor AND resolved_longitude IS NOT NULL AND resolved_latitude IS NOT NULL) AS successor_latitude,
        COUNT(DISTINCT COALESCE(historical_broad_sector, CASE WHEN historical_detailed_sector IN ('Catholic', 'Independent') THEN 'Non-government' END))
            FILTER (WHERE historical_scope_confirmed AND (historical_broad_sector_source_url IS NOT NULL OR historical_detailed_sector_source_url IS NOT NULL)) > 1 AS broad_conflict,
        MIN(COALESCE(historical_broad_sector, CASE WHEN historical_detailed_sector IN ('Catholic', 'Independent') THEN 'Non-government' END))
            FILTER (WHERE historical_scope_confirmed AND (historical_broad_sector_source_url IS NOT NULL OR historical_detailed_sector_source_url IS NOT NULL)) AS historical_broad,
        MIN(COALESCE(historical_broad_sector_source_url, historical_detailed_sector_source_url)) FILTER (WHERE historical_scope_confirmed) AS broad_source,
        COUNT(DISTINCT CASE resolved_sector WHEN 'Government' THEN 'Government' WHEN 'Catholic' THEN 'Non-government' WHEN 'Independent' THEN 'Non-government' END)
            FILTER (WHERE institution_resolution IS DISTINCT FROM 'unresolved') > 1 AS assumed_broad_conflict,
        MIN(CASE resolved_sector WHEN 'Government' THEN 'Government' WHEN 'Catholic' THEN 'Non-government' WHEN 'Independent' THEN 'Non-government' END)
            FILTER (WHERE is_successor) AS assumed_broad,
        BOOL_OR(historical_scope_confirmed) AS has_historical_scope,
        COUNT(DISTINCT historical_detailed_sector) FILTER (WHERE historical_scope_confirmed AND historical_detailed_sector_source_url IS NOT NULL) > 1 AS detailed_conflict,
        MIN(historical_detailed_sector) FILTER (WHERE historical_scope_confirmed AND historical_detailed_sector_source_url IS NOT NULL) AS historical_detailed,
        MIN(historical_detailed_sector_source_url) FILTER (WHERE historical_scope_confirmed) AS detailed_source
    FROM identities WHERE attended_school_id IS NOT NULL
    GROUP BY attended_school_id
), facts AS (
    SELECT i.*,
        COALESCE(c.original_location_conflict, FALSE)
            OR (c.original_longitude IS NULL AND (COALESCE(c.campus_conflict, FALSE)
                OR (c.campus = 'same_campus' AND COALESCE(c.successor_location_conflict, FALSE)))) AS location_conflict,
        COALESCE(c.campus_conflict, FALSE) AS campus_continuity_conflict,
        COALESCE(c.broad_conflict, FALSE) OR COALESCE(c.detailed_conflict, FALSE)
            OR (c.historical_broad IS NULL AND COALESCE(c.assumed_broad_conflict, FALSE)) AS sector_conflict,
        COALESCE(c.historical_broad IS NOT NULL AND i.is_successor AND
            c.historical_broad <> CASE i.resolved_sector WHEN 'Government' THEN 'Government' WHEN 'Catholic' THEN 'Non-government' WHEN 'Independent' THEN 'Non-government' END, FALSE)
            AS continuity_discrepancy,
        CASE
            WHEN c.broad_conflict THEN NULL
            WHEN c.historical_broad IS NOT NULL THEN c.historical_broad
            WHEN i.is_successor AND c.assumed_broad_conflict THEN NULL
            WHEN i.is_successor THEN c.assumed_broad
            WHEN NOT i.is_successor AND i.institution_resolution IS DISTINCT FROM 'unresolved' AND i.resolved_sector = 'Government' THEN 'Government'
            WHEN NOT i.is_successor AND i.institution_resolution IS DISTINCT FROM 'unresolved' AND i.resolved_sector IN ('Catholic', 'Independent') THEN 'Non-government'
        END AS broad_sector,
        CASE
            WHEN c.broad_conflict OR c.detailed_conflict OR (i.is_successor AND c.historical_broad IS NULL AND c.assumed_broad_conflict) THEN NULL
            WHEN c.historical_broad = 'Government' THEN 'Government'
            WHEN c.historical_detailed IS NOT NULL THEN c.historical_detailed
            WHEN NOT i.is_successor AND i.institution_resolution IS DISTINCT FROM 'unresolved' AND i.resolved_sector IN ('Government', 'Catholic', 'Independent') THEN i.resolved_sector
        END AS detailed_sector,
        CASE
            WHEN c.broad_conflict THEN 'unresolved'
            WHEN c.historical_broad IS NOT NULL THEN 'historical_verified'
            WHEN i.is_successor AND c.assumed_broad_conflict THEN 'unresolved'
            WHEN i.is_successor AND c.assumed_broad IS NOT NULL THEN 'successor_assumption'
            WHEN NOT i.is_successor AND i.institution_resolution IS DISTINCT FROM 'unresolved' AND i.resolved_sector IN ('Government', 'Catholic', 'Independent') THEN 'original_reference'
            ELSE 'unresolved'
        END AS sector_basis,
        CASE
            WHEN c.broad_conflict OR c.detailed_conflict OR (i.is_successor AND c.historical_broad IS NULL AND c.assumed_broad_conflict) THEN 'unresolved'
            WHEN c.historical_broad = 'Government' OR c.historical_detailed IS NOT NULL THEN 'historical_verified'
            WHEN NOT i.is_successor AND i.institution_resolution IS DISTINCT FROM 'unresolved' AND i.resolved_sector IN ('Government', 'Catholic', 'Independent') THEN 'original_reference'
            ELSE 'unresolved'
        END AS detailed_sector_basis,
        c.broad_source AS sector_source_url,
        CASE WHEN c.historical_broad = 'Government' THEN c.broad_source ELSE c.detailed_source END AS detailed_sector_source_url,
        c.original_longitude, c.original_latitude, c.original_location_source,
        c.campus, c.campus_source, c.successor_longitude, c.successor_latitude
    FROM identities i LEFT JOIN consensus c USING (attended_school_id)
), locations AS (
    SELECT f.*,
        CASE
            WHEN location_conflict THEN 'unresolved'
            WHEN original_longitude IS NOT NULL AND original_latitude IS NOT NULL THEN 'original_verified'
            WHEN is_successor AND campus = 'same_campus' AND successor_longitude IS NOT NULL AND successor_latitude IS NOT NULL THEN 'successor_verified_same_campus'
            WHEN is_successor AND resolved_longitude IS NOT NULL AND resolved_latitude IS NOT NULL THEN 'successor_unverified'
            WHEN NOT is_successor AND institution_resolution IS DISTINCT FROM 'unresolved' AND resolved_longitude IS NOT NULL AND resolved_latitude IS NOT NULL THEN 'original_reference'
            ELSE 'unresolved'
        END AS location_basis
    FROM facts f
)
SELECT * EXCLUDE (resolved_longitude, resolved_latitude, resolved_sector,
        original_longitude, original_latitude, original_location_source,
        campus, campus_source, successor_longitude, successor_latitude),
    CASE location_basis
        WHEN 'original_verified' THEN original_longitude
        WHEN 'successor_verified_same_campus' THEN successor_longitude
        WHEN 'successor_unverified' THEN resolved_longitude
        WHEN 'original_reference' THEN resolved_longitude
    END AS display_longitude,
    CASE location_basis
        WHEN 'original_verified' THEN original_latitude
        WHEN 'successor_verified_same_campus' THEN successor_latitude
        WHEN 'successor_unverified' THEN resolved_latitude
        WHEN 'original_reference' THEN resolved_latitude
    END AS display_latitude,
    CASE location_basis
        WHEN 'original_verified' THEN original_longitude
        WHEN 'successor_verified_same_campus' THEN successor_longitude
        WHEN 'original_reference' THEN resolved_longitude
    END AS attendance_longitude,
    CASE location_basis
        WHEN 'original_verified' THEN original_latitude
        WHEN 'successor_verified_same_campus' THEN successor_latitude
        WHEN 'original_reference' THEN resolved_latitude
    END AS attendance_latitude,
    location_basis IN ('original_verified', 'successor_verified_same_campus', 'original_reference') AS attendance_location_eligible,
    CASE location_basis WHEN 'original_verified' THEN original_location_source WHEN 'successor_verified_same_campus' THEN campus_source END AS location_source_url,
    resolved_institution_id AS profile_institution_id,
    CASE WHEN is_successor THEN 'successor_context' ELSE 'current_reference' END AS profile_basis,
    resolved_institution_id AS finance_institution_id,
    CASE WHEN is_successor THEN 'successor_context' ELSE 'current_reference' END AS finance_basis
FROM locations;

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
    ps.source_url AS service_source_url,
    ps.retrieved_at AS service_retrieved_at,
    ps.service_start,
    ps.service_end,
    i.institution_id,
    i.acara_id,
    c.attended_school_name AS school_name,
    i.school_type,
    COALESCE(c.detailed_sector, 'Other') AS school_sector,
    i.campus_type,
    i.state AS school_state,
    i.suburb AS school_suburb,
    i.postcode AS school_postcode,
    c.attendance_longitude AS longitude,
    c.attendance_latitude AS latitude,
    i.longitude AS resolved_longitude,
    i.latitude AS resolved_latitude,
    i.sector AS resolved_sector,
    me.level,
    me.years_attended,
    me.graduation_year,
    me.attended_status,
    me.source_url,
    me.retrieved_at,
    me.confidence,
    me.reviewer_notes,
    me.school_name_as_recorded,
    me.institution_resolution,
    me.resolution_source_url,
    i.country,
    i.institution_status,
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
    sf.total_net_recurrent_income_per_student AS historical_2021_net_recurrent_income_per_student,
    c.attended_school_id, c.attended_school_name, c.identity_basis,
    c.attended_institution_id, c.attended_identity_source_url,
    c.recorded_school_id, c.historical_scope_confirmed,
    c.historical_latitude, c.historical_longitude, c.historical_location_source_url,
    c.campus_continuity, c.campus_continuity_source_url,
    c.historical_broad_sector, c.historical_broad_sector_source_url,
    c.historical_detailed_sector, c.historical_detailed_sector_source_url,
    c.resolved_institution_id, c.resolved_institution_name, c.is_successor,
    c.display_longitude, c.display_latitude,
    c.attendance_longitude, c.attendance_latitude, c.attendance_location_eligible,
    c.location_basis, c.location_source_url,
    c.broad_sector, c.detailed_sector, c.sector_basis, c.detailed_sector_basis,
    c.sector_source_url, c.detailed_sector_source_url,
    c.location_conflict, c.sector_conflict, c.continuity_discrepancy, c.campus_continuity_conflict,
    c.profile_institution_id, c.profile_basis, c.finance_institution_id, c.finance_basis
FROM member_education me
JOIN members m ON me.member_id = m.member_id
JOIN parliament_service ps ON m.member_id = ps.member_id
JOIN institutions i ON me.institution_id = i.institution_id
JOIN v_education_attendance_context c ON me.education_id = c.education_id
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
    COUNT(DISTINCT CASE WHEN c.detailed_sector = 'Government' THEN me.education_id END) AS government_records,
    COUNT(DISTINCT CASE WHEN c.detailed_sector = 'Catholic' THEN me.education_id END) AS catholic_records,
    COUNT(DISTINCT CASE WHEN c.detailed_sector = 'Independent' THEN me.education_id END) AS independent_records,
    COUNT(DISTINCT CASE WHEN c.detailed_sector IS NULL THEN me.education_id END) AS other_unclassified_records
FROM parliament_service ps
LEFT JOIN member_education me ON ps.member_id = me.member_id AND me.level = 'secondary'
LEFT JOIN institutions i ON me.institution_id = i.institution_id
LEFT JOIN v_education_attendance_context c ON me.education_id = c.education_id
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
JOIN parliament_metadata pm ON pm.parliament_number = ps.parliament_number
JOIN electoral_boundaries eb ON (
    LOWER(ps.electorate) = LOWER(eb.electorate)
    AND eb.election_year = year(pm.general_election_date)
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
