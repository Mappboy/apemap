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
| **School Aliases** | APEMAP Project | `data/reference/school_aliases.json` | Project repository | CC BY 4.0 |

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

### 3. ACARA 2021 School Financial Snapshot
- **Publisher**: ACARA / MySchool
- **Purpose**: Institutional income and recurrent funding metrics per student.
- **Attribution**: *© Australian Curriculum, Assessment and Reporting Authority (ACARA).*
- **Limitations**: Reflects the 2021 reporting year only. Does **not** reflect funding levels when parliamentarians attended school.

---

## 3. Reference & Historical Research Sources

These sources informed the project's background research, baseline validation, or spatial boundaries:

### 1. Sydney Morning Herald (SMH) 2021 Investigation
- **Reference**: [Careers Before Politics (SMH, 2021)](https://www.smh.com.au/interactive/2021/careers-before-politics/)
- **Authors & Team**: Daniel Carter, Noah Yim (Developers); Fleta Page, Rob Harris (Editors); Mark Stehle, Matthew Absalom-Wong (Design/Production).
- **Role in APEMAP**: Provided the conceptual inspiration and early manual baseline for linking 46th/47th parliamentarians to secondary institutions.
- **Attribution**: *Sydney Morning Herald investigative baseline (Carter, Yim et al.).*

### 2. Australian Electoral Commission (AEC)
- **Portal**: [AEC Boundary Downloads](https://www.aec.gov.au/Electorates/gis/gis_datadownload.htm)
- **Datasets**: 2021 Commonwealth Electoral Boundaries (`aec_elb_2021`), Registered Political Parties (`aec_parties`).
- **Attribution**: *© Commonwealth of Australia (Australian Electoral Commission).*

### 3. Australian Bureau of Statistics (ABS)
- **Portal**: [ASGS Edition 3 Boundaries](https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs-edition-3/jul2021-jun2026/access-and-downloads/data-services-and-apis)
- **Datasets**: ASGS Edition 3 Remoteness Areas and Statistical Areas.
- **Attribution**: *© Commonwealth of Australia (Australian Bureau of Statistics).*

### 4. Wikidata & Wikipedia
- **Purpose**: Cross-reference for parliamentarian Q-identifiers (`wikidata_id`), university headquarters coordinates ([Wikidata Property P159](https://www.wikidata.org/wiki/Property:P159)), and disambiguation of international institutions.

---

## 4. Background Literature & External Articles

For researchers interested in the broader context of parliamentary demographics and school funding:
- The Age: [Schools should be publicly funded, free and open to all: researchers (2023)](https://www.theage.com.au/politics/victoria/schools-should-be-publicly-funded-free-and-open-to-all-researchers-20230418-p5d1a6.html)
- ABS: [Schools Australia Statistical Release](https://www.abs.gov.au/statistics/people/education/schools/latest-release)
- ABC News: [Do politicians know what it's like to do your job? (2018)](https://www.abc.net.au/news/2018-03-09/politicians-professions-do-mps-know-how-to-do-your-job/9360836)
- Torrens University: [What degrees do Australian ministers have?](https://www.torrens.edu.au/blog/what-degrees-ministers-australia-have-and-why-it-matters)
- Inter-Parliamentary Union (IPU): [Women in Parliament Open Data](https://data.ipu.org/node/9/data-on-women?chamber_id=13325)
- Per Capita: [The Way In: Representation in the 46th Parliament (2022)](https://percapita.org.au/wp-content/uploads/2022/05/The-Way-In-46th-Parliament-May-2022-UPDATED.pdf)
