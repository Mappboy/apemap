# Australian Parliamentarian Education Map (APEMAP)

## Background
Simply put I wanted to know where all the Parliamentarian's went to school and any links that exist between them.
There's a number of super interesting questions which come out of this:
 - Does the location of the school impact party and policy ?
 - Is school funding affected by an MP's secondary school ?
 - Are there schools which routinely have MPs ?
 - Does any of it matter?
 - Also I think it's just cool

## TODO
- Finish data cleaning and converting to a relational dataset.
- Add full address and state to education
- Add on sector from school-location or profile
- Add on extra data - compare any missing schools or uni's
- Tidy up old data.
- Convert datasets to standard outputs
- Add theyvote for you link https://theyvoteforyou.org.au/people/representatives/grayndler/anthony_albanese
- Add openpolitics https://openpolitics.au/member/penny-allman-payne
- Get missing poly school data
- Pull missing from https://github.com/tomquirk/linkedin-api
- https://github.com/openaustralia/openaustralia-parser
- https://www.abc.net.au/news/2018-03-09/politicians-professions-do-mps-know-how-to-do-your-job/9360836
- https://www.torrens.edu.au/blog/what-degrees-ministers-australia-have-and-why-it-matters

## Data sources
- SMH article
- Wikipedia
- This one is invaluable - https://handbook.aph.gov.au/Parliamentarian
- Manual search where no school found (Wikipedia text search, LinkedIn, Facebook, Google, APH, blog posts and articles etc) I should've kept track of this to start with
- School information https://www.acara.edu.au/contact-us/acara-data-access

## Fun Facts
- I can never remember how SPARQL entities work exactly so I just got ChatGPT to write them for me
- Some universities have multiple locations and campuses which may have lead to incorrect locations I use headquarter locations in this case https://www.wikidata.org/wiki/Property:P159
- As above some may be online in which case I will just pick a headquarters of campus


## Built with
- Datasette
- DuckDB
- Jupyter
- KeplerGL
- Pandas + Geopandas
