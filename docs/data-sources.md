# Data Sources, Provenance & Attribution

This document details the upstream data sources consumed by APEMAP, their retrieval mechanisms, licenses, attribution terms, and specific limitations.

---

## 1. Project Licensing Summary

APEMAP is dual-licensed under open-source and open-data standards:
- **Code & Pipeline Tooling**: [MIT License](../LICENSE)
- **Compiled Datasets & Derived Tables**: [Creative Commons Attribution 4.0 International (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/)

When citing or reusing derived datasets from APEMAP, attribute as:
> *APEMAP: Australian Parliamentarians Education Map, compiled from official Australian Parliament House and ACARA public registers under CC BY 4.0.*

---

## 2. Currently Consumed Pipeline Data Sources

These sources are actively fetched or read during pipeline execution:

| Source | Organization | Dataset / Endpoint | Retrieval | License / Terms |
| :--- | :--- | :--- | :--- | :--- |
| **APH Handbook API** | Parliament of Australia | Individuals biographical API (`/api/individuals`) | HTTPS GET / Disk cache | Commonwealth Copyright / Public Handbook |
| **ACARA School Location** | ACARA | School Location 2022 & 2025 (`.csv`/`.xlsx`) | Portal HTTPS GET | © ACARA (CC BY 4.0 / Data Access) |
| **ACARA School Profile** | ACARA | Longitudinal School Profile 2008–2025 (`.xlsx`) | Portal HTTPS GET | © ACARA (CC BY 4.0 / Data Access) |
| **ACARA 2021 Finances** | ACARA | 2021 School Financial Snapshot | Isolated snapshot in `school_finances_2021` | © ACARA MySchool |
| **ACARA Finance Benchmarks** | ACARA | National Report on Schooling Public School Income Benchmarks | Reference CSV in `school_finance_benchmarks` | © ACARA (National Report on Schooling) |
| **NSW RAM Funding** | NSW Department of Education | Data.NSW Education Resource Allocation Model | Downloadable CSV / API in `school_public_funding` | © State of New South Wales (CC BY 4.0) |
| **Tasmania DECYP SRP** | Tasmania DECYP | School Resource Package (Fairer Funding Model) | Resourcing Workbooks in `school_public_funding` | © State of Tasmania (CC BY 4.0) |
| **NT School Funding** | NT Department of Education | School Needs Based Funding Formula Allocations | Public Resourcing Releases in `school_public_funding` | © Northern Territory Government (CC BY 4.0) |
| **Queensland Grants** | QLD Department of Education | State Recurrent Grant Scheme for Non-State Schools | Public Allocation Tables in `school_public_funding` | © State of Queensland (CC BY 4.0) |
| **Manual Disclosures** | Authoritative School Reports | Annual report financial statements | Canonical CSV `manual_school_funding.csv` | Authoritative Public Disclosures |
| **School Aliases** | APEMAP Project | `data/reference/school_aliases.json` | Project repository | CC BY 4.0 |
| **Wikipedia / Wikidata** | Wikimedia Foundation | MediaWiki Action API & Wikidata SPARQL | HTTPS GET / Disk cache (`data/raw/wikimedia/`) | CC0 / CC BY-SA 4.0 |
| **AEC Federal Boundaries** | Australian Electoral Commission | National 2025 Federal Boundaries (`AUS-March-2025-esri.zip`) | HTTPS GET / Disk cache (`data/raw/aec/2025/`) | © Commonwealth of Australia (CC BY 4.0) |
| **ABS Schools Benchmark** | Australian Bureau of Statistics | Schools, 2025 Statistical Release | Canonical reference in `education_sector_benchmarks` | © Commonwealth of Australia (CC BY 4.0) |

### Detailed Source Profiles

### 1. Australian Parliament House (APH) Parliamentary Handbook
- **Publisher**: Department of Parliamentary Services, Commonwealth of Australia
- **Endpoint**: `https://handbookapi.aph.gov.au/api/individuals`
- **Handbook Portal**: [APH Parliamentary Handbook](https://handbook.aph.gov.au/Parliamentarian)
- **Purpose**: Authoritative source for parliamentarian biographical details, birthdates, gender, chambers, party affiliations, electorates, states, and self-reported education history.
- **Attribution**: *Parliament of Australia Parliamentary Handbook, © Commonwealth of Australia.*
- **Limitations**: Education fields are unstructured text entered by parliamentarians or handbook staff. Completeness varies across members, and secondary schooling is occasionally omitted.

### 2. Australian Curriculum, Assessment and Reporting Authority (ACARA)
- **Publisher**: ACARA
- **Portal**: [ACARA Data Access Program](https://www.acara.edu.au/contact-us/acara-data-access)
- **Datasets**:
  - `School Location 2025` / `2022`: Authoritative names, School SML IDs (`acara_id`), sectors (`Government`, `Catholic`, `Independent`), addresses, and geographic coordinates (latitude, longitude).
  - `School Profile 2008-2025`: Longitudinal annual enrolments, ICSEA index values, and school classifications.
  - `acara_school_results.json`: Cached school register export.
- **Attribution**: *© Australian Curriculum, Assessment and Reporting Authority (ACARA).*
- **Limitations**: SML IDs reflect schools active during reporting years. Closed or merged schools may not appear in single-year files, necessitating longitudinal registers and alias mappings.

### 3. ACARA My School Financial Data (`school_finances`)
- **Publisher**: Australian Curriculum, Assessment and Reporting Authority (ACARA)
- **Portal**: [My School](https://myschool.edu.au) / [ACARA Data Access](https://www.acara.edu.au/contact-us/acara-data-access)
- **Table**: Canonical `school_finances` table (keyed by `institution_id, reporting_year`) with backward-compatible view `school_finances_2021`.
- **Purpose**: Institutional income and recurrent funding metrics per student (total gross income, total net recurrent income, government funding allocations, fees/parent contributions).
- **Required Attribution**:
  > *Source: Australian Curriculum, Assessment and Reporting Authority (ACARA)*
- **Terms of Use & Legal Restrictions**:
  My School site content is governed by specific [My School Terms of Use](https://myschool.edu.au/copyright) (July 2020), which are distinct from generic CC BY releases:
  - **Clause 6.1**: Permits reproduction/distribution for personal, private, non-commercial educational use within an organisation.
  - **Clause 6.2**: Mandates preservation of copyright notices and explicit ACARA attribution.
  - **Clause 6.4**: Explicitly states that permission in 6.1 does **not** extend to reproducing or distributing content on a publicly accessible website or media without prior written consent.
  - **Clause 6.6**: Requires written approval from ACARA for any use outside the permitted scope.
  - **Do not label school-level My School finance data as CC BY 4.0** unless ACARA explicitly confirms that licence applies.
- **Acquisition Protocol & Decision**:
  - Zero runtime web scraping of `myschool.edu.au`.
  - Acquisition seeks written ACARA approval for the **smallest necessary dataset/use**: financial data for the specific list of ACARA SML IDs associated with parliamentarians, or permission for controlled programmatic retrieval.
  - Retain school-level values locally for non-commercial research; publish aggregated/derived APEMAP analysis without redistributing restricted raw school-level records in Git.
  - Ingestion supports local authorised files (`--finance-file` or `data/restricted/` which is ignored in Git) keyed strictly by ACARA SML ID.
  - Tests and CI use synthetic fixtures and do not access restricted live data.
- **Multi-Campus Rolled Reporting**:
  - Certain multi-campus schools report finances combined at the parent entity or main campus level.
  - APEMAP flags rolled reporting using `is_rolled_reporting = TRUE` and `parent_acara_id = <parent_id>` rather than silently classifying secondary campuses as missing finance.
- **Distinction from Supplementary Funding Datasets**:
  - Jurisdictional funding datasets (such as NSW RAM, Tasmanian SRP, or Department of Education Commonwealth funding) measure specific government funding allocations under distinct jurisdictional models.
  - These metrics are **not equivalent** to ACARA total gross income or net recurrent income and must remain separately modelled and labelled.

### 4. ACARA National Report on Schooling Finance Benchmarks (`school_finance_benchmarks`)
- **Publisher**: Australian Curriculum, Assessment and Reporting Authority (ACARA)
- **Portal**: [National Report on Schooling in Australia Data Portal](https://www.acara.edu.au/reporting/national-report-on-schooling-in-australia/school-income)
- **Table**: Canonical `school_finance_benchmarks` table (keyed by `reporting_year, state_or_territory, sector, geolocation, metric`).
- **Purpose**: Authoritative statistical school income benchmarks derived from the National Report on Schooling, reporting average per-student recurrent and gross income metrics disaggregated by calendar year, state/territory, school sector, and ASGS Remoteness geolocation.
- **Attribution**: *Source: Australian Curriculum, Assessment and Reporting Authority (ACARA) National Report on Schooling.*
- **Role in Estimation**: Provides the empirical foundation for indexed benchmark estimates and peer-group averages when individual-school ACARA financial disclosures are unavailable or restricted.

### 5. Jurisdictional Public School Funding Datasets (`school_public_funding`)

#### A. New South Wales — Resource Allocation Model (RAM)
- **Publisher**: NSW Department of Education, State of New South Wales
- **Portal**: [Data.NSW Education Resource Allocation Model](https://data.nsw.gov.au/data/dataset/nsw-education-resource-allocation-model)
- **Licence**: Creative Commons Attribution 4.0 International (CC BY 4.0)
- **Model**: Resource Allocation Model (RAM)
- **Metrics**:
  - `ram_allocation_total`: Total school-level RAM allocation (AUD).
  - `ram_base_allocation`: Base operational funding component (AUD).
  - `ram_equity_loading`: Targeted socio-economic, Aboriginal background, English proficiency, and disability loadings (AUD).
  - `ram_operational_funding`: Site-specific operational funding allocation (AUD).
- **Matching Protocol**: Deterministic matching on stable NSW School Codes and ACARA SML IDs.

#### B. Tasmania — School Resource Package (SRP)
- **Publisher**: Department for Education, Children and Young People (DECYP), State of Tasmania
- **Portal**: [Tasmania DECYP School Resourcing Data](https://www.decyp.tas.gov.au/about-us/policies-legislation-data/data-and-statistics/school-resourcing-data/)
- **Licence**: Creative Commons Attribution 4.0 International (CC BY 4.0)
- **Model**: School Resource Package (Fairer Funding Model)
- **Metrics**:
  - `srp_allocation_total`: Total School Resource Package allocation (AUD).
  - `srp_core_staffing`: Core staffing allocation (AUD).
  - `srp_operational_allocation`: Operational resourcing allocation (AUD).
- **Matching Protocol**: Deterministic matching via DECYP school identifiers and ACARA SML IDs.

#### C. Northern Territory — School Needs Based Funding Formula
- **Publisher**: Department of Education, Northern Territory Government
- **Portal**: [NT Department of Education School Funding](https://education.nt.gov.au/statistics-research-and-strategies/increasing-school-autonomy/school-funding)
- **Licence**: Creative Commons Attribution 4.0 International (CC BY 4.0)
- **Model**: School Needs Based Funding Formula
- **Metrics**:
  - `annual_school_resourcing_allocation`: Total annual school resourcing allocation (AUD).
  - `per_student_funding_rate`: Published per-student formula funding rate (AUD per student).

#### D. Queensland — Non-State School Recurrent Grants
- **Publisher**: Department of Education, State of Queensland
- **Portal**: [Queensland Non-State School Recurrent Grants](https://education.qld.gov.au/about-us/budgets-funding-grants/grants/non-state-school/state-recurrent-grant) / [Queensland Non-State Schools Dataset](https://www.data.qld.gov.au/dataset/queensland-non-state-schools)
- **Licence**: Creative Commons Attribution 4.0 International (CC BY 4.0)
- **Model**: State Recurrent Grant Scheme for Non-State Schools
- **Metrics**:
  - `state_recurrent_grant_rate_secondary`: Published per-student secondary recurrent grant rate (AUD per student).
  - `state_recurrent_grant_rate_primary`: Published per-student primary recurrent grant rate (AUD per student).
  - `state_recurrent_grant_total`: Estimated/actual school-level State recurrent grant allocation (AUD).

#### E. Victoria — Public Lookup Investigation & No-Ingest Decision
- **Portal**: [Find Your School's Funding (Victoria)](https://www.vic.gov.au/find-your-schools-funding)
- **Findings**: The Victorian Department of Education publishes an interactive public lookup service for individual government school allocations (displaying total funding, equity funding, and student enrolment counts). However, no documented, versioned public API or bulk CSV download exists.
- **Architecture Decision**: In accordance with project policy against fragile, unmaintained browser scraping, APEMAP adopts a **documented no-ingest decision** for bulk Victorian government allocations. Victorian schools rely on canonical ACARA benchmark estimates and reviewed manual disclosures.

#### F. South Australia, Western Australia, and Australian Capital Territory Fallback
- **Status**: While public funding formula frameworks (e.g. Student Centred Funding Model in WA, Resource Allocation Model in SA) are published as policy guidelines, complete, official school-level allocation datasets are not published as bulk open-data releases.
- **Architecture Decision**: APEMAP does not attempt to reverse-engineer formula allocations from policy papers without official inputs. These jurisdictions standardise on:
  1. ACARA peer-group benchmark estimates; and
  2. Authoritative manual enrichment from published school annual reports.

#### G. Authoritative Manual School Funding Enrichment (`data/reference/manual_school_funding.csv`)
- **Format**: Reviewable CSV file stored in Git.
- **Columns**: `acara_id, reporting_year, metric, value, unit, source_url, source_type, reviewed_at, notes`.
- **Criteria**: Authoritative public publications only (e.g. school annual financial statements, official governance disclosures).
- **Semantics**: Preserves original metric meanings without synthetic conversion to ACARA net recurrent income.

### 6. Wikipedia & Wikidata (Supplementary Enrichment Source)
- **Publisher**: Wikimedia Foundation
- **Endpoints**:
  - Wikidata SPARQL: `https://query.wikidata.org/sparql`
  - Wikipedia Action API: `https://en.wikipedia.org/w/api.php`
- **Purpose**:
  - Populates canonical `members.wikidata_id` using identifier-first linking via Parliament of Australia MP identifier ([Wikidata Property P10020](https://www.wikidata.org/wiki/Property:P10020)).
  - Provides QA and demographic cross-checking (dates of birth, gender) against APH records without silently overwriting authoritative values.
  - Suggests institution names, QIDs, Wikipedia URLs, geographic coordinates, and localities for unmatched/international schools for manual reviewer inspection.
- **Caching & Provenance**: Cached on local disk under `data/raw/wikimedia/members/` and `data/raw/wikimedia/institutions/`. Normal pipeline runs execute completely offline from cache; live network requests occur only when `--refresh` is explicitly supplied.
- **Attribution & Licensing**: *Data from Wikidata is available under CC0 Public Domain Dedication; text and sitelinks from Wikipedia are available under Creative Commons Attribution-ShareAlike 4.0 International (CC BY-SA 4.0).*
- **Limitations**: Crowdsourced and secondary. Used strictly for cross-referencing and supplementary enrichment; never treated as an authoritative replacement for official APH or ACARA data.

### 5. Australian Electoral Commission (AEC) 2025 Federal Electoral Boundaries
- **Publisher**: Australian Electoral Commission, Commonwealth of Australia
- **Portal**: [AEC GIS Data Download](https://www.aec.gov.au/electorates/gis/gis_datadownload.htm)
- **Dataset**: National 2025 Federal Electoral Boundaries ESRI Shapefile (`AUS-March-2025-esri.zip`, current as 4 March 2025).
- **Purpose**: Authoritative spatial boundaries for the 150 federal electoral divisions applied at the 2025 federal election, linked to House of Representatives service records via `v_house_electorates` and exported as canonical GeoParquet (`data/processed/electoral_boundaries.parquet`).
- **Caching & Provenance**: Cached on local disk under `data/raw/aec/2025/`. Pipeline runs use the cached shapefile by default; `--refresh` re-downloads the archive.
- **Attribution**: *© Commonwealth of Australia (Australian Electoral Commission 2025).*
- **Limitations**: Reflects the boundary redistribution for the 2025 federal election (150 divisions).

### 6. Australian Bureau of Statistics (ABS) Schools, 2025 Benchmark
- **Publisher**: Australian Bureau of Statistics, Commonwealth of Australia
- **Release**: [ABS Schools, 2025](https://www.abs.gov.au/statistics/people/education/schools/2025) (released 5 March 2026).
- **Purpose**: Official national benchmark measuring student enrolments by school affiliation, establishing baseline sector shares: Government 62.8% (2,613,404 enrolments), Catholic 20.0% (831,692 enrolments), and Independent 17.2% (715,822 enrolments) from 4,160,918 total enrolments.
- **Crucial Distinction**: Measures **student enrolments by school affiliation**, not Australian population shares. Consistently labeled as "Student Enrolment Share" throughout APEMAP.
- **Attribution**: *© Commonwealth of Australia (Australian Bureau of Statistics 2026, Schools 2025).*
- **Limitations**: Reflects 2025 annual census enrolment counts; does not reflect historical student proportions when older parliamentarians attended school.

---

## 3. Reference & Historical Research Sources

These sources informed the project's background research, baseline validation, or spatial boundaries:

### 1. Sydney Morning Herald (SMH) 2021 Investigation
- **Reference**: [Careers Before Politics (SMH, 2021)](https://www.smh.com.au/interactive/2021/careers-before-politics/)
- **Authors & Team**: Daniel Carter, Noah Yim (Developers); Fleta Page, Rob Harris (Editors); Mark Stehle, Matthew Absalom-Wong (Design/Production).
- **Role in APEMAP**: Provided the conceptual inspiration and early manual baseline for linking 46th/47th parliamentarians to secondary institutions.
- **Attribution**: *Sydney Morning Herald investigative baseline (Carter, Yim et al.).*

### 2. Australian Bureau of Statistics (ABS) Statistical Geography
- **Portal**: [ASGS Edition 3 Boundaries](https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs-edition-3/jul2021-jun2026/access-and-downloads/data-services-and-apis)
- **Datasets**: ASGS Edition 3 Remoteness Areas and Statistical Areas.
- **Attribution**: *© Commonwealth of Australia (Australian Bureau of Statistics).*


---

## 4. Background Literature & External Articles

For researchers interested in the broader context of parliamentary demographics and school funding:
- The Age: [Schools should be publicly funded, free and open to all: researchers (2023)](https://www.theage.com.au/politics/victoria/schools-should-be-publicly-funded-free-and-open-to-all-researchers-20230418-p5d1a6.html)
- ABS: [Schools Australia Statistical Release](https://www.abs.gov.au/statistics/people/education/schools/latest-release)
- ABC News: [Do politicians know what it's like to do your job? (2018)](https://www.abc.net.au/news/2018-03-09/politicians-professions-do-mps-know-how-to-do-your-job/9360836)
- Torrens University: [What degrees do Australian ministers have?](https://www.torrens.edu.au/blog/what-degrees-ministers-australia-have-and-why-it-matters)
- Inter-Parliamentary Union (IPU): [Women in Parliament Open Data](https://data.ipu.org/node/9/data-on-women?chamber_id=13325)
- Per Capita: [The Way In: Representation in the 46th Parliament (2022)](https://percapita.org.au/wp-content/uploads/2022/05/The-Way-In-46th-Parliament-May-2022-UPDATED.pdf)
