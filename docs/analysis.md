# Analytical Outputs & Notebook Workflows

APEMAP provides deterministic analytical calculations through the `apemap.analysis` module, reproducible Jupyter notebooks, and portable output artifacts.

---

## 1. Supported Deterministic Analytics

APEMAP generates standardized analytical metrics across all supported parliaments:

### A. Parliament Demographics
Calculates age and demographic metrics benchmarked strictly to the official opening day of each parliament:
- **Opening-Day Average Age**: Arithmetic mean of member ages on opening day.
- **Opening-Day Median Age**: Median age of the opening-day cohort.
- **Chamber Breakdown**: Seat distribution across the House of Representatives and the Senate.
- **Gender Breakdown**: Count and percentage of male, female, and other representatives.
- **Party Representation**: Distribution by party abbreviation (`ALP`, `LP`, `NP`, `GRN`, `IND`, etc.).

### B. Secondary School Sector Distribution
Accurately categorizes secondary education attendance into Government, Catholic, and Independent sectors:
- **Unique Parliamentarians by Sector**: Counts unique members who attended at least one school in a given sector. (Note: members who attended both public and private schools are attributed proportionally or reflected across categories).
- **Attendance Instances by Sector**: Counts total secondary school enrolments, accounting for members who attended multiple institutions.
- **Percentage of Known Cohort**: Proportions computed against parliamentarians with verified secondary school records, explicitly separating unmatched or overseas schooling.

### C. Institutional Funding Metrics (2021 Baseline)
Summarizes ACARA 2021 financial metrics for schools attended by parliamentarians:
- **Gross Income per Student**: Average total gross income per student across attended schools by sector.
- **Net Recurrent Income per Student**: Average net recurrent income per student.
- **Reporting Sample Size**: Explicit $N$ counts indicating how many attended schools reported financial data to ACARA in 2021.

---

## 2. Generated Analytical Artifacts

When executing `apemap analyze` or `apemap export`, artifacts are generated in `data/processed/`:

### 1. `analysis_report.json`
A consolidated JSON payload containing demographic, sector, and financial aggregates for all specified parliaments. Ideal for web dashboards, static charts, or programmatic consumption.

### 2. `coverage_metrics.json`
Tracks the data completion status for each parliament:
- Total parliamentarians
- Opening-day members
- Current sitting members
- Matched domestic schools
- Unmatched domestic schools
- International schools

### 3. `unmatched_schools.csv`
A human-reviewable queue listing all raw school candidate strings that could not be resolved against ACARA registers, including confidence scores and detected international flags.

### 4. Parquet Files (`data/processed/*.parquet`)
High-performance, columnar representations of all canonical tables:
- `members.parquet`
- `parliament_service.parquet`
- `institutions.parquet`
- `member_education.parquet`
- `school_snapshots.parquet`
- `school_finances_2021.parquet`

### 5. GeoJSON Spatial Layers (`parliament_{num}_combined.geojson`)
Point feature collections containing every secondary institution attended by members of that parliament. Features include school name, sector, ICSEA, address, coordinates, and an array of attending parliamentarians with their parties and chambers.

---

## 3. Reproducible Analysis Notebooks

The canonical interactive analysis is implemented in the `notebooks/` directory. These notebooks import statistical calculations directly from `apemap.analysis` and open `data/aped.duckdb` read-only:

- [**`00_data_overview.ipynb`**](../notebooks/00_data_overview.ipynb): Database validation, snapshot dates, table counts, and coverage metrics.
- [**`01_demographics.ipynb`**](../notebooks/01_demographics.ipynb): Opening-day age brackets, chamber breakdowns, and party demographics.
- [**`02_education_sectors.ipynb`**](../notebooks/02_education_sectors.ipynb): Unique parliamentarians vs. attendance instances across Government, Catholic, and Independent sectors.
- [**`03_school_finance.ipynb`**](../notebooks/03_school_finance.ipynb): NULL-aware 2021 school income averages and per-student funding distributions.
- [**`04_parliament_comparison.ipynb`**](../notebooks/04_parliament_comparison.ipynb): Comparative longitudinal trends between the 46th, 47th, and 48th Parliaments.
- [**`marimo/analysis.py`**](../notebooks/marimo/analysis.py): Optional reactive Marimo notebook companion.

### Running Notebooks Locally

To launch Jupyter Lab with all analytical dependencies:

```bash
uv sync --extra analysis
uv run jupyter lab
```

To run the automated notebook execution test suite:

```bash
uv run pytest tests/test_notebook_execution.py
```

---

## 4. Historical Exploratory Results & Static Charts

Earlier versions of APEMAP generated static charts in `images/` (e.g. `school_sector_breakdown.png`, `gender_vs_party.png`, `high_school_46.png`). 
These static charts and early exploratory notebooks have been archived under `archive/notebooks/2026-09-22/`. 
For current, reproducible findings, rely on the numbered notebooks or `data/processed/analysis_report.json`.
Interactive visualizations are published at [cpoole.dev](https://cpoole.dev).
