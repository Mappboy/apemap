# Polly Pedagogy Map

## Background
Simply put I wanted to know where all the polys went and the links between them attending schools.
I also have been lazy on my Data analytics of late so great excuse

## TODO
- Add theyvote for you link https://theyvoteforyou.org.au/people/representatives/grayndler/anthony_albanese
- Add openpolitics https://openpolitics.au/member/penny-allman-payne
- Get missing poly school data
- Pull missing from https://github.com/tomquirk/linkedin-api
- https://github.com/openaustralia/openaustralia-parser
- https://www.abc.net.au/news/2018-03-09/politicians-professions-do-mps-know-how-to-do-your-job/9360836
- https://www.torrens.edu.au/blog/what-degrees-ministers-australia-have-and-why-it-matters
- Extract Qualifications data from https://www.aph.gov.au/Senators_and_Members/Parliamentarian

## Data sources
- SMH article
- Wikipedia
- Manual search where no school found (Wikipedia text search, LinkedIn, Facebook, Google, APH, blog posts and articles etc) I should've kept track of this to start with

## Fun Facts
- I can never remember how SPARQL entities work exactly so I just got ChatGPT to write them for me
- Some universities have multiple locations and campuses which may have lead to incorrect locations I use headquarter locations in this case https://www.wikidata.org/wiki/Property:P159
- As above some may be online in which case I will just pick a headquarters of campus 
## Built with
- Datasette
- DuckDB
- Jupyter
- Pandas + Geopandas