# Legacy Scraping Archive (2026-09-21)

## Background and Rationale
In earlier versions of APEMAP, school financial data was retrieved via runtime web-scraping of `myschool.edu.au` in `apemap/utils.py` (`get_finances`). This method relied on extracting dynamic session cookies (`.ESAPI_SESSIONID`), anti-forgery tokens (`__RequestVerificationToken`), manual reCAPTCHA solving, and parsing raw HTML tables with BeautifulSoup.

This approach was deprecated and removed in Issue #4 for the following reasons:
1. **Compliance with ACARA Data Access Policies**: ACARA terms of use and data access protocols require using official data products (via the ACARA Data Access Program) rather than automated site scraping.
2. **Fragility & Reliability**: Session tokens expired unpredictably and reCAPTCHA prompts blocked non-interactive pipelines.
3. **Data Integrity & Isolation**: Issue #4 establishes a requirement of "Zero runtime web-scraping of myschool.edu.au". Historical 2021 financial data is preserved in a standalone table `school_finances_2021` derived from verified historical baselines rather than runtime requests.

## Superseded Code
The file `utils_scraping_legacy.py` in this directory is an exact copy of `apemap/utils.py` immediately prior to the removal of `get_finances` and `MissingSchool`.
