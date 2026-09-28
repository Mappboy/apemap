-- Canonical Relational Schema for APEMAP
-- Backed by DuckDB and Parquet

-- 1. Members: Unique individual parliamentarians across all terms
CREATE TABLE IF NOT EXISTS members (
    member_id VARCHAR PRIMARY KEY,
    family_name VARCHAR NOT NULL,
    given_name VARCHAR NOT NULL,
    display_name VARCHAR NOT NULL,
    gender VARCHAR,
    date_of_birth DATE,
    aph_id VARCHAR UNIQUE,
    wikidata_id VARCHAR UNIQUE
);

-- 2. Parliament Service: Per-parliament terms and seat/party representation
-- Replaces duplicated physical tables per parliament (e.g. member_aph_46, member_aph_47)
CREATE TABLE IF NOT EXISTS parliament_service (
    service_id VARCHAR PRIMARY KEY,
    member_id VARCHAR NOT NULL REFERENCES members(member_id),
    parliament_number INTEGER NOT NULL,
    chamber VARCHAR NOT NULL CHECK (chamber IN ('representatives', 'senate')),
    party VARCHAR NOT NULL,
    party_abbrev VARCHAR NOT NULL,
    electorate VARCHAR,
    state_or_territory VARCHAR NOT NULL,
    service_start DATE,
    service_end DATE,
    is_opening_day_member BOOLEAN DEFAULT FALSE,
    is_current_member BOOLEAN DEFAULT FALSE
);

-- 3. Institutions: Educational institutions (schools, universities, colleges)
CREATE TABLE IF NOT EXISTS institutions (
    institution_id VARCHAR PRIMARY KEY,
    acara_id VARCHAR,
    school_name VARCHAR NOT NULL,
    school_type VARCHAR,
    sector VARCHAR NOT NULL CHECK (sector IN ('Government', 'Catholic', 'Independent', 'Tertiary', 'Other')),
    campus_type VARCHAR,
    state VARCHAR,
    suburb VARCHAR,
    postcode VARCHAR,
    longitude DOUBLE,
    latitude DOUBLE
);

-- 4. Member Education: Link table recording member attendance at institutions
-- Includes mandatory audit provenance fields on all assertions
CREATE TABLE IF NOT EXISTS member_education (
    education_id VARCHAR PRIMARY KEY,
    member_id VARCHAR NOT NULL REFERENCES members(member_id),
    institution_id VARCHAR NOT NULL REFERENCES institutions(institution_id),
    level VARCHAR NOT NULL CHECK (level IN ('secondary', 'tertiary')),
    years_attended VARCHAR,
    graduation_year INTEGER,
    attended_status VARCHAR NOT NULL CHECK (attended_status IN ('graduated', 'attended_did_not_graduate', 'attended_unspecified')),
    -- Audit Provenance Fields
    source_url VARCHAR NOT NULL,
    retrieved_at TIMESTAMPTZ NOT NULL,
    confidence VARCHAR NOT NULL CHECK (confidence IN ('verified', 'provisional', 'unconfirmed')),
    reviewer_notes VARCHAR
);

-- 5. School Snapshots: Annual school metrics (enrolments, ICSEA, finances)
CREATE TABLE IF NOT EXISTS school_snapshots (
    institution_id VARCHAR NOT NULL REFERENCES institutions(institution_id),
    snapshot_year INTEGER NOT NULL,
    total_enrolments INTEGER,
    icsea INTEGER,
    financial_profile_2021 JSON,
    PRIMARY KEY (institution_id, snapshot_year)
);

-- 6. School Finances: Annual school-level income and recurrent funding metrics
-- Generalised annual finance table keyed by (institution_id, reporting_year)
CREATE TABLE IF NOT EXISTS school_finances (
    institution_id VARCHAR NOT NULL REFERENCES institutions(institution_id),
    acara_id VARCHAR NOT NULL,
    reporting_year INTEGER NOT NULL,
    recurrent_funding_gov_total BIGINT,
    recurrent_funding_state_total BIGINT,
    fees_charges_parent_total BIGINT,
    other_private_sources_total BIGINT,
    total_gross_income_total BIGINT,
    total_net_recurrent_income_total BIGINT,
    recurrent_funding_gov_per_student BIGINT,
    recurrent_funding_state_per_student BIGINT,
    fees_charges_parent_per_student BIGINT,
    other_private_sources_per_student BIGINT,
    total_gross_income_per_student BIGINT,
    total_net_recurrent_income_per_student BIGINT,
    -- Multi-campus rolled reporting flags
    is_rolled_reporting BOOLEAN DEFAULT FALSE,
    parent_acara_id VARCHAR,
    -- Audit Provenance & Licensing Fields
    source_dataset VARCHAR NOT NULL DEFAULT 'ACARA My School Finance',
    source_url VARCHAR NOT NULL DEFAULT 'https://myschool.edu.au',
    licence VARCHAR NOT NULL DEFAULT 'ACARA My School Terms of Use (July 2020)',
    retrieved_at TIMESTAMPTZ,
    notes VARCHAR,
    PRIMARY KEY (institution_id, reporting_year)
);

-- 7. Electoral Boundaries: Official Commonwealth electoral division boundaries
CREATE TABLE IF NOT EXISTS electoral_boundaries (
    boundary_id VARCHAR PRIMARY KEY,
    election_year INTEGER NOT NULL,
    electorate VARCHAR NOT NULL,
    state_or_territory VARCHAR NOT NULL,
    geometry GEOMETRY NOT NULL,
    source_url VARCHAR NOT NULL,
    source_dataset VARCHAR NOT NULL,
    retrieved_at TIMESTAMPTZ NOT NULL
);

-- 8. Education Sector Benchmarks: Official statistical reference benchmarks (e.g. ABS Schools)
CREATE TABLE IF NOT EXISTS education_sector_benchmarks (
    benchmark_year INTEGER NOT NULL,
    sector VARCHAR NOT NULL CHECK (sector IN ('Government', 'Catholic', 'Independent')),
    student_enrolment_share DOUBLE NOT NULL,
    student_enrolments BIGINT NOT NULL,
    total_student_enrolments BIGINT NOT NULL,
    source_title VARCHAR NOT NULL,
    source_url VARCHAR NOT NULL,
    released_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (benchmark_year, sector)
);

-- 9. School Finance Benchmarks: Official aggregated school finance benchmarks (ACARA National Report on Schooling)
CREATE TABLE IF NOT EXISTS school_finance_benchmarks (
    reporting_year INTEGER NOT NULL,
    state_or_territory VARCHAR NOT NULL,
    sector VARCHAR NOT NULL CHECK (sector IN ('Government', 'Catholic', 'Independent', 'All')),
    geolocation VARCHAR NOT NULL CHECK (geolocation IN ('Major Cities', 'Inner Regional', 'Outer Regional', 'Remote', 'Very Remote', 'All')),
    metric VARCHAR NOT NULL,
    value DOUBLE NOT NULL,
    unit VARCHAR NOT NULL DEFAULT 'AUD_per_student',
    source_dataset VARCHAR NOT NULL DEFAULT 'ACARA National Report on Schooling',
    source_url VARCHAR NOT NULL,
    retrieved_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (reporting_year, state_or_territory, sector, geolocation, metric)
);

-- 10. School Public Funding: Jurisdictional school-level public funding and resourcing allocations
CREATE TABLE IF NOT EXISTS school_public_funding (
    institution_id VARCHAR NOT NULL REFERENCES institutions(institution_id),
    reporting_year INTEGER NOT NULL,
    jurisdiction VARCHAR NOT NULL,
    metric VARCHAR NOT NULL,
    value DOUBLE NOT NULL,
    unit VARCHAR NOT NULL,
    funding_model VARCHAR NOT NULL,
    source_dataset VARCHAR NOT NULL,
    source_url VARCHAR NOT NULL,
    source_record_id VARCHAR,
    retrieved_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (institution_id, reporting_year, metric, source_dataset)
);


