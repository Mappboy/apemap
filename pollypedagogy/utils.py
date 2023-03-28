import re
import sys

import pandas as pd
import requests
import urllib3
import wikipediaapi
from SPARQLWrapper import SPARQLWrapper, JSON

urllib3.disable_warnings()

WIKI_URL = "https://en.wikipedia.org/w/api.php?action=query&prop=pageprops&ppprop=wikibase_item&redirects=1&titles={title}&format=json"
PARLIMENTARIAN_HANDBOOK = "https://handbookapi.aph.gov.au/api/individuals?$orderby=FamilyName,GivenName &$filter=PHID eq '{ph_id}'"


def get_aph_df(mp_id: str) -> pd.DataFrame:
    """Fetch data from APH"""
    r = requests.get(PARLIMENTARIAN_HANDBOOK.format(ph_id=mp_id), verify=False)
    r.raise_for_status()
    query = r.json()
    return pd.json_normalize(query, "value")


def fetch_handbook_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Given a dataframe with mp_id pull down all information for each row and create an aph dataframe

    :param df: Dataframe contain mp_ids
    :return: All details from APH ID
    """
    aph_df = pd.DataFrame()
    assert "mp_id" in df.columns
    for row in df.drop_duplicates("mp_id").iterrows():
        data = get_aph_df(row[1]["mp_id"])
        if aph_df.empty:
            aph_df = data
        else:
            aph_df = pd.concat([aph_df, data])
    return aph_df


def parlimentarian_handbook_secondary_school(ph_id):
    """Only fetching Secondaryy School"""
    r = requests.get(PARLIMENTARIAN_HANDBOOK.format(ph_id=ph_id), verify=False)
    r.raise_for_status()
    query = r.json()
    values = query.get("value", [])
    if len(values):
        return ",".join([value.get("SecondarySchool") for value in values])


def get_ph_id_from_wikidata(entitiy_id_or_str):
    """Parse entityid to mp_id

    :param entitiy_id_or_str: http://www.wikidata.org/entity/Q16732352 or Q16732352
    :return:
    """
    entitiy_id = re.search(r"(Q\d+)", entitiy_id_or_str).group(0)
    mp_id_query = f"""SELECT ?mp_id WHERE {{
  wd:{entitiy_id} wdt:P10020 ?mp_id .
    }}"""

    mp_res = get_results(query=mp_id_query)
    try:
        return clean_results(mp_res)["mp_id"][0]
    except KeyError:
        return ""


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
    wiki_wiki = wikipediaapi.Wikipedia("en")
    a = wiki_wiki.page(title)
    if a.exists():
        r = requests.get(
            f"https://www.wikidata.org/w/api.php?action=wbgetentities&sites=enwiki&titles={a.title}&normalize=1&format=json"
        )
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


def clean_results(results: dict, dataframe=True) -> pd.DataFrame | dict:
    if not dataframe:
        for result in results["results"]["bindings"]:
            return result
    output = pd.json_normalize(results["results"]["bindings"])
    col_vals = [c for c in output.columns if c.endswith(".value")]
    output_cleaned = output[col_vals]

    output_cleaned.columns = [c.replace(".value", "") for c in col_vals]
    return output_cleaned
