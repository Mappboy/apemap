# Australian Parliamentarian Education Map (APEMAP)

[![Binder](https://mybinder.org/badge_logo.svg)](https://mybinder.org/v2/gh/Mappboy/apemap/HEAD?urlpath=lab/tree/notebooks/analysis.ipynb)


![Australian Politicians Education Map.png](Austalian%20Politicians%20Education%20Map.png)

## Background
Simply put I wanted to know where all the Parliamentarian's went to school and any links that exist between them.
The closest approximation to this was this [Sydney Morning Herald article](https://www.smh.com.au/interactive/2021/careers-before-politics/) the which I used as a starting point.

The article addressed There are several intriguing questions that arise from this research:
 - Where did my local MP and [insert name of any politician my friends keep asking me about] go to school?
 - Which members have attended the same school?
 - Has school funding been affected by the number of politicians as alumni ?
 - Has there been a shift between parliaments ?
 - Are there schools which routinely have MPs ?
 - Does the location of the school impact party and policy ?

The other good reason for doing this make Australian political data easier to find and update.
There's a really good library for it in R https://github.com/RohanAlexander/AustralianPoliticians, but I wanted to make the data a little more agnostic.

See [analysis](notebooks/analysis.html) or [notebook](notebooks/analysis.ipynb) for initial analysis.

View the [Felt Map here](https://felt.com/map/Austalian-Politicians-Education-Map-mAKBz3XhRQ9BJ0jEkdVb39CB?lat=-28.585924&lon=131.326302&zoom=4.49)

_*Please note that I collated this data to the best of my ability in my free time and for fun. If you plan to use it for research purposes, I recommend conducting some quality assurance and contributing to the dataset. Additionally, please ensure that you provide proper attribution and consult the copyright and licensing information. [copyright](#Copyright-&-Licensing)*_

## Missing Ministers
We are currently missing the exact school for the following ministers and senators.

### Missing Seneators
- Alex Antic,senate,Public
- Arthur Sinodinos,senate,Public
- Jana Stewart,senate,?
- Jess Walsh,senate,Non-government
- Karen Grogan,senate,?
- Peter Whish-Wilson,senate,Both
- Marielle Smith,senate,Both

### Missing MPs
- Graham Perrett,house,Public
- Peter Khalil,house,Non-government
- Milton Dick,house,Non-government
- Rob Mitchell,house,Public
- Llew O'Brien,house, Did not graduate but left school in year 9
- Michelle Ananda-Rajah,house,?
- Sam Birrell,house,?
- Susan Templeman,house,Public
- Stephen Bates,house, International probably
- Tracey Roberts,house,?
- Vince Connelly,house,Non-government
- Julian Simmonds,house,Non-government
- Nicolle Flint,house,Non-government

## Data sources
### Caveats
Wikipedia has the best linkage between a member and school attended, it is not as rich as APH.
The school names in APH are looked up against acara_school_locations_2022 to see if we can find a match.
Locations are derived from Wikipedia and Google maps.
Some universities have multiple locations and campuses which may have lead to incorrect locations I use headquarter locations in this case https://www.wikidata.org/wiki/Property:P159
As above some may be online in which case I will just pick a headquarters of campus

### Schools search
- Wikipedia
- This one is invaluable - https://handbook.aph.gov.au/Parliamentarian
- Manual search where no school found (Wikipedia text search, LinkedIn, Facebook, Google, APH, blog posts and articles etc) I should've kept track of this to start with
- School information https://www.acara.edu.au/contact-us/acara-data-access
- SMH article


## Datasets

### External Datasets

- acara_education_finances - Financial data from ACARA only (2021)
- acara_finance_missing - Schools where no financial data exists
- acara_school_locations_2022 - School locations from ACARA
- acara_school_profile_2022 - School profiles from ACARA
- aec_parties - Party lookup from AEC
- aec_elb_2021 - AEC 2021 Electoral Boundaries
- aph_parliamentarians - All Parlmentarians downloaded from APH
- education - Education data compiled from multiple sources (Originally Wikipedia+APH)
- education_acara - Matched datasets to ACARA data
- members_wiki - Members from Wikipedia
- members_aph - Members from APH
- members_occupations - Members occupations from APH
- members_secondary_occupations - Miniiters secondary occupations
- members_secondary_school - Members secondary school from APH (split by "/,")
- smh_careers - Career data from SMH
- smh_ministry - Ministry data from SMH

### Compiled Datasets
-  members - All members compiled from multiple sources
-  education - All education compiled from multiple sources
-  member_education - Linking table between members and education

### Views
- member_aph
- member_aph_47
- member_aph_46
- member_secondary_school_education_47
- member_secondary_school_education_46

## Installation

### Python + Poetry
```bash
pip install poetry
poetry install
```
### Viewing Data

Using [QGIS](https://qgis.org/en/site/forusers/download.html) open the [data/analysis.qgz](data/analysis.qgz) file.

Using [Datasette](https://datasette.io/) run the following command from the root directory
```bash
datasette install
datasette data/aped.db --load-extension spatialite
```

### Copyright & Licensing
- AEC : © Commonwealth of Australia (Australian Electoral Commission)2023
- ACARA : © Australian Curriculum, Assessment and Reporting Authority (ACARA) 2023
- ABS : © Commonwealth of Australia (Australian Bureau of Statistics) 2023


### Online Layers
- https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs-edition-3/jul2021-jun2026/access-and-downloads/data-services-and-apis
- Mapbox
- Openstreetmap
- Map tiler
- Linked Data ? https://asgs.linked.fsdf.org.au/dataset/asgsed3/collections

## Built with
- Pandas + Geopandas
- QGIS
- PostgreSQL + PostGIS
- Wikidata
- Jupyter
- Plotly + Dash
- Mapbox
- Felt

## TODO
- Convert to issues
- Convert QGIS to using aped.gpkg
- Add high_school_international to members table
- Add issue templates for suggesting member data
- Add at a glance
- Add related news
- Fix Age or make generated column
- Finish Plotly map see [app](/app)
- Switch to Indigenous names for Capital cities (Because 2023)
- Add theyvote for you link https://theyvoteforyou.org.au/people/representatives/grayndler/anthony_albanese
- Add openpolitics https://openpolitics.au/member/penny-allman-payne
- https://github.com/openaustralia/openaustralia-parser

## Fun Facts
- I can never remember how SPARQL entities work exactly so I just got ChatGPT to write them for me

## PostGIS to Geopackage

`ogr2ogr -f GPKG aped.gpkg PG:"service=ape" -oo LIST_ALL_TABLES=YES  -mapFieldType "StringList=String,IntegerList=String" -oo SCHEMAS="public"`

## Existing Data and Articles
- [Schools should be publicly funded](https://www.theage.com.au/politics/victoria/schools-should-be-publicly-funded-free-and-open-to-all-researchers-20230418-p5d1a6.html)
- [Latest School Statistics](https://www.abs.gov.au/statistics/people/education/schools/latest-release)
- [Do politicians know what it's like to do your job?](https://www.abc.net.au/news/2018-03-09/politicians-professions-do-mps-know-how-to-do-your-job/9360836)
- [Politician Degrees](https://www.torrens.edu.au/blog/what-degrees-ministers-australia-have-and-why-it-matters)
- [Women in parliament](https://data.ipu.org/node/9/data-on-women?chamber_id=13325)
- [Demographics of 46th Parliament](https://percapita.org.au/wp-content/uploads/2022/05/The-Way-In-46th-Parliament-May-2022-UPDATED.pdf)
- [ABS Education Stats](https://www.abs.gov.au/statistics/people/education/schools/latest-release)


## Credits
### Sydney Morning Herald - Article
*Developers*: Daniel Carter, Noah YimEditors Fleta Page, Rob Harris
*Design/Production*: Mark Stehle, Matthew Absalom-Wong
