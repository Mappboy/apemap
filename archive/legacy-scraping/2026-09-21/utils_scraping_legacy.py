import os
import random
import re
import sys
import time

import pandas as pd
import requests
import urllib3
import wikipediaapi
from SPARQLWrapper import SPARQLWrapper, JSON
from bs4 import BeautifulSoup

urllib3.disable_warnings()

WIKI_URL = "https://en.wikipedia.org/w/api.php?action=query&prop=pageprops&ppprop=wikibase_item&redirects=1&titles={title}&format=json"
PARLIMENTARIAN_HANDBOOK = "https://handbookapi.aph.gov.au/api/individuals?$orderby=FamilyName,GivenName &$filter=PHID eq '{ph_id}'"

endpoint_url = "https://query.wikidata.org/sparql"


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
        return ",".join(
            [value.get("parlimentarian_handbook_secondary_school") for value in values]
        )


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


def get_dob_gender_from_wikidata(entitiy_id_or_str):
    """Parse entityid to gender, and dob"""
    entitiy_id = re.search(r"(Q\d+)", entitiy_id_or_str).group(0)
    gender_query = f"""SELECT ?genderLabel ?dob WHERE {{
     wd:{entitiy_id} wdt:P21 ?gender .
              SERVICE wikibase:label {{
                 bd:serviceParam wikibase:language "[AUTO_LANGUAGE],en" .
            ?gender rdfs:label ?genderLabel .
        }}
     OPTIONAL {{ wd:{entitiy_id} wdt:P569 ?dob . }}
      }}"""

    dob_gender = get_results(query=gender_query)
    try:
        clean_res = clean_results(dob_gender)
        if "genderLabel" in clean_res.columns and "dob" in clean_res.columns:
            return clean_res.iloc[0][["genderLabel", "dob"]]
        if "genderLabel" in clean_res.columns:
            ret_series = clean_res.iloc[0]
            ret_series["dob"] = ""
            return ret_series
        if "dob" in clean_res.columns:
            ret_series = clean_res.iloc[0]
            ret_series["genderLabel"] = ""
            return ret_series
    except KeyError:
        return ""


def get_wikipedia_entity_id(title):
    """Get wikipedia entity id from wikipedia api"""
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
    """Use wikipedia api to get wikidata entity id"""
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
    """Fetch a result from wikidata"""
    user_agent = "WDQS Python/%s.%s" % (sys.version_info[0], sys.version_info[1])
    # TODO adjust user agent; see https://w.wiki/CX6
    sparql = SPARQLWrapper(endpoint_url, agent=user_agent)
    sparql.setQuery(query)
    sparql.setReturnFormat(JSON)
    return sparql.query().convert()


def clean_results(results: dict, dataframe=True) -> pd.DataFrame | dict:
    """Clean up a result returned from wikidata"""
    if not dataframe:
        for result in results["results"]["bindings"]:
            return result
    output = pd.json_normalize(results["results"]["bindings"])
    col_vals = [c for c in output.columns if c.endswith(".value")]
    output_cleaned = output[col_vals]

    output_cleaned.columns = [c.replace(".value", "") for c in col_vals]
    return output_cleaned


def get_google_geocode(location: str) -> str:
    """Get google geocode for location"""
    import geopy

    google_geo = geopy.geocoders.GoogleV3(api_key=os.environ.get("GOOGLE_API_KEY"))
    try:
        address = google_geo.geocode(location, region="AU")
        if address:
            return f"Point({address[1][1]} {address[1][0]})"
    except Exception as exc:
        print(f"Error: {exc}")


# finances are doable for 2014-2021
class MissingSchool(Exception):
    def __init__(self, msg, school_id):
        super().__init__(msg)
        self.school_id = school_id


def get_finances(school_id: int, year: int = 2021):  # noqa C901
    """Download financial data from ACARA"""
    cols = [
        "australian_government_recurrent_funding",
        "state__territory_government_recurring_funding",
        "fees_charges_and_parent_contributions",
        "other_private_sources",
        "total_gross_income",
        "total_net_recurrent_income",
    ]
    time.sleep(random.randrange(2, 10))
    cookies = {
        ".ESAPI_SESSIONID": os.environ.get("ESAPI_SESSIONID"),
    }
    print(f"Getting finances for {school_id} - {year}")
    if os.environ.get("RECAPTCHA"):
        cookies["__RequestVerificationToken"] = os.environ.get("RECAPTCHA")

    r = requests.get(
        f"https://www.myschool.edu.au/school/{school_id}/finances/{year}",
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36",
            "Referer": f"https://www.myschool.edu.au/school/{school_id}/profile/{year}",
        },
        cookies=cookies,
    )
    if not r.ok:
        raise MissingSchool(f"School {school_id} not found", school_id)
    soup = BeautifulSoup(r.text, "html.parser")
    ul = soup.find("ul", {"class": "table-content table-border"})
    # Find all the <li> elements
    if not ul:
        # manually check recaptcha
        input("Please solve the recaptcha and press enter")

        cookies = {
            ".ESAPI_SESSIONID": os.environ.get("ESAPI_SESSIONID"),
        }
        if os.environ.get("RECAPTCHA"):
            cookies["__RequestVerificationToken"] = os.environ.get("RECAPTCHA")
        r = requests.get(
            f"https://www.myschool.edu.au/school/{school_id}/finances/{year}",
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36",
                "Referer": f"https://www.myschool.edu.au/school/{school_id}/profile/{year}",
            },
            cookies=cookies,
        )
        soup = BeautifulSoup(r.text, "html.parser")
        ul = soup.find("ul", {"class": "table-content table-border"})
    if not ul:
        raise Exception("no ul found, no new captcha")
    li_elements = ul.find_all("li")

    # Create an empty list to hold the extracted data
    data = []

    # Loop through the <li> elements and extract the data
    # might need to be more flexible here
    for li in li_elements:
        try:
            row = []
            for div in li.find_all("div", class_="col-title"):
                row.append(div.get_text(strip=True))
            for div in li.find_all("div", class_="col-value"):
                row.append(div.get_text(strip=True))
        except Exception as e:
            print("problem with row", e)
            continue
        data.append(row)

    # Create a pandas DataFrame from the extracted data
    df = pd.DataFrame(data, columns=["Title", "Total", "Per Student"])
    fdf = df.T
    fdf.columns = fdf.loc["Title", :]
    fdf.drop("Title", inplace=True, axis=0)
    fdf.columns = (
        fdf.columns.str.lower()
        .str.replace("/", "")
        .str.replace(",", "")
        .str.replace(" ", "_")
    )
    # convert integers with , to Decimal and remove $
    fdf = fdf.applymap(
        lambda x: x.replace("$", "").replace(",", "").strip()
        if isinstance(x, str)
        else x
    )
    fdf.convert_dtypes()
    fdf = fdf[cols]
    o = {
        f"{c}_{row[0].lower().replace(' ', '_')}": row[1][c]
        for row in fdf.iterrows()
        for c in cols
    }
    df = pd.DataFrame.from_dict(o, orient="index", columns=["Value"])
    df.index.name = "Category"
    df["Value"] = df["Value"].replace("-", 0)
    df["Value"] = df["Value"].astype(int)
    df = df.T
    df["school_id"] = school_id
    df["year"] = year
    df.set_index("school_id", inplace=True)
    return df
