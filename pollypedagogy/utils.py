import sys

import pandas as pd
import requests
import wikipediaapi
from SPARQLWrapper import SPARQLWrapper, JSON

WIKI_URL = "https://en.wikipedia.org/w/api.php?action=query&prop=pageprops&ppprop=wikibase_item&redirects=1&titles={title}&format=json"


def get_wikipedia_entity_id(title):
    r = requests.get(WIKI_URL.format(title=title))
    r.raise_for_status()
    query = r.json()
    pages = query.get("query", {}).get("pages", {})
    page_ids = pages.keys()
    if page_ids:
        keys = list(page_ids)
        entity_id = pages[keys[0]].get("pageprops", {}).get("wikibase_item")
        return f"http://www.wikidata.org/entity/{entity_id}"


def get_wikidata_entity_id(title):
    wiki_wiki = wikipediaapi.Wikipedia('en')
    a = wiki_wiki.page(title)
    if a.exists():
        r = requests.get(
            f"https://www.wikidata.org/w/api.php?action=wbgetentities&sites=enwiki&titles={a.title}&normalize=1&format=json")
        r.raise_for_status()
        entities = r.json()
        return f"http://www.wikidata.org/entity/{list(entities['entities'].keys())[0]}"


def get_results(endpoint_url="https://query.wikidata.org/sparql", query=""):
    user_agent = "WDQS Python/%s.%s" % (sys.version_info[0], sys.version_info[1])
    # TODO adjust user agent; see https://w.wiki/CX6
    sparql = SPARQLWrapper(endpoint_url, agent=user_agent)
    sparql.setQuery(query)
    sparql.setReturnFormat(JSON)
    return sparql.query().convert()


def clean_results(results: dict) -> pd.DataFrame:
    output = pd.json_normalize(results)
    col_vals = [c for c in output.columns if c.endswith(".value")]
    output_cleaned = output[col_vals]

    output_cleaned.columns = [c.replace(".value", "") for c in col_vals]
    return output_cleaned
