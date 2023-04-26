# Australian Parliamentarian Education Map (APEMAP)

## Background
Simply put I wanted to know where all the Parliamentarian's went to school and any links that exist between them.
The closest approximation to this was this [Sydney Morning Herald article](https://www.smh.com.au/interactive/2021/careers-before-politics/) the which I used as a starting point.

There's a number of fascinating questions which come out of this:
 - Which Members have attended the same school?
 - Shift between parliaments ?
 - Has school funding been affected by the number of politicians as alumni ?
 - Are there schools which routinely have MPs ?
 - Does the location of the school impact party and policy ?

The other good reason for doing this make Australian political data easier to find and update.
There's a really good library for it in R https://github.com/RohanAlexander/AustralianPoliticians, but I wanted to make the data a little more agnostic.

See [analysis](notebooks/analysis.ipynb) for initial analysis.

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

### Online Layers
- https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs-edition-3/jul2021-jun2026/access-and-downloads/data-services-and-apis
- Mapbox
- Openstreetmap
- Map tiler

### Copyright & Licensing
- AEC : © Commonwealth of Australia (Australian Electoral Commission)2023
- ACARA : © Australian Curriculum, Assessment and Reporting Authority (ACARA) 2023
- ABS : © Commonwealth of Australia (Australian Bureau of Statistics) 2023

Linked Data ? https://asgs.linked.fsdf.org.au/dataset/asgsed3/collections

## Built with
- Pandas + Geopandas
- QGIS
- PostgreSQL + PostGIS
- Wikidata
- Jupyter

## TODO
- Convert QGIS to using aped.gpkg
- Add issue templates for suggesting member data
- Add at a glance
- Add related news
- Finish Plotly map see app
- Switch to Indigenous names for Capital cities (Because 2023)
- Add theyvote for you link https://theyvoteforyou.org.au/people/representatives/grayndler/anthony_albanese
- Add openpolitics https://openpolitics.au/member/penny-allman-payne
- https://github.com/openaustralia/openaustralia-parser

## Fun Facts
- I can never remember how SPARQL entities work exactly so I just got ChatGPT to write them for me

## PostGIS to Geopackage

`ogr2ogr -f GPKG aped.gpkg PG:"service=ape" -oo LIST_ALL_TABLES=YES  -mapFieldType "StringList=String,IntegerList=String" -oo SCHEMAS="public"`

## Existing Data and Articles
- https://www.abs.gov.au/statistics/people/education/schools/latest-release
- https://www.abc.net.au/news/2018-03-09/politicians-professions-do-mps-know-how-to-do-your-job/9360836
- https://www.torrens.edu.au/blog/what-degrees-ministers-australia-have-and-why-it-matters
- https://data.ipu.org/node/9/data-on-women?chamber_id=13325
- https://percapita.org.au/wp-content/uploads/2022/05/The-Way-In-46th-Parliament-May-2022-UPDATED.pdf
- https://www.abs.gov.au/statistics/people/education/schools/latest-release


## Credits
### Sydney Morning Herald - Article
*Developers*: Daniel Carter, Noah YimEditors Fleta Page, Rob Harris
*Design/Production*: Mark Stehle, Matthew Absalom-Wong
