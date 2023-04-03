# Australian Parliamentarian Education Map (APEMAP)

## Background
Simply put I wanted to know where all the Parliamentarian's went to school and any links that exist between them.
The closest approximation to this was this [Sydney Morning Herald article](https://www.smh.com.au/interactive/2021/careers-before-politics/) the which I used as a starting point.
There's a number of fascinating questions which come out of this:
 - Does the location of the school impact party and policy ?
 - Is school funding affected by an MP's secondary school ?
 - Are there schools which routinely have MPs ?
 - Does any of it matter?
 - Also I think it's just cool

## TODO
- Convert datasets to standard outputs
- Convert ministry table from 46th parliament to 47th parliament
- Add theyvote for you link https://theyvoteforyou.org.au/people/representatives/grayndler/anthony_albanese
- Add openpolitics https://openpolitics.au/member/penny-allman-payne
- https://github.com/openaustralia/openaustralia-parser


- https://www.abc.net.au/news/2018-03-09/politicians-professions-do-mps-know-how-to-do-your-job/9360836
- https://www.torrens.edu.au/blog/what-degrees-ministers-australia-have-and-why-it-matters

## Data sources
### Schools search
- SMH article
- Wikipedia
- This one is invaluable - https://handbook.aph.gov.au/Parliamentarian
- Manual search where no school found (Wikipedia text search, LinkedIn, Facebook, Google, APH, blog posts and articles etc) I should've kept track of this to start with
- School information https://www.acara.edu.au/contact-us/acara-data-access
### Spatial Datasets


## Fun Facts
- I can never remember how SPARQL entities work exactly so I just got ChatGPT to write them for me
- Some universities have multiple locations and campuses which may have lead to incorrect locations I use headquarter locations in this case https://www.wikidata.org/wiki/Property:P159
- As above some may be online in which case I will just pick a headquarters of campus

## Datasets
### aped.gpkg tables
- acara_school_locations_2022 - School locations from ACARA
- acara_school_profile_2022 - School profiles from ACARA
- careers - Career data from SMH
- education - Education data compiled from multiple sources (Originally Wikipedia+APH)
- education__acara - Matched datasets to ACARA data
- ministers - Ministers from Wikipedia
- ministers_aph - Ministers from APH
- ministers_occupations - Ministers occupations from APH
- ministers_secondary_occupations - Miniiters secondary occupations
- ministers_aph_secondary_school - Ministers secondary school from APH (split by "/")

### aped.db views
-ministers_combined_all_education - Combined education data from all sources
-ministers_combined_secondary_education - Combined education data from all sources


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
- Datasette
- DuckDB
- Jupyter
- KeplerGL
- Pandas + Geopandas

## Credits
### Sydney Morning Herald - Article
*Developers*: Daniel Carter, Noah YimEditors Fleta Page, Rob Harris
*Design/Production*: Mark Stehle, Matthew Absalom-Wong
