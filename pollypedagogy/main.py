# pip install sparqlwrapper
# https://rdflib.github.io/sparqlwrapper/

import sys

import pandas as pd
from SPARQLWrapper import SPARQLWrapper, JSON

endpoint_url = "https://query.wikidata.org/sparql"

query_reps = """SELECT ?item ?itemLabel ?group ?groupLabel ?district ?districtLabel ?term ?termLabel ?edu ?eduLabel ?start ?end
WHERE
{
  ?item p:P39 ?statement .
  ?statement ps:P39/wdt:P279* wd:Q18912794 ; pq:P580 ?start .
  OPTIONAL { ?statement pq:P2937 ?term }
  OPTIONAL { ?statement pq:P582  ?end }
  OPTIONAL { ?statement pq:P768  ?district }
  OPTIONAL { ?statement pq:P4100 ?group }
  OPTIONAL { ?item wdt:P69 ?edu .
             ?edu rdfs:label ?eduLabel FILTER (lang(?eduLabel) = "en")}
  FILTER(!BOUND(?end) || ?end > NOW())
  SERVICE wikibase:label { bd:serviceParam wikibase:language "[AUTO_LANGUAGE],en". }
}
ORDER BY ?start ?end"""

query_senate = """
SELECT ?item ?itemLabel ?group ?groupLabel ?district ?districtLabel ?term ?termLabel ?edu ?eduLabel ?start ?end
WHERE
{
  ?item p:P39 ?statement .
  ?statement ps:P39/wdt:P279* wd:Q6814428 ; pq:P580 ?start .
  OPTIONAL { ?statement pq:P2937 ?term }
  OPTIONAL { ?statement pq:P582  ?end }
  OPTIONAL { ?statement pq:P768  ?district }
  OPTIONAL { ?statement pq:P4100 ?group }
  OPTIONAL { ?item wdt:P69 ?edu .
             ?edu rdfs:label ?eduLabel FILTER (lang(?eduLabel) = "en")}
  FILTER(!BOUND(?end) || ?end > NOW())
  SERVICE wikibase:label { bd:serviceParam wikibase:language "[AUTO_LANGUAGE],en". }
}
ORDER BY ?start ?end
"""


def get_results(endpoint_url, query):
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


senate_results = get_results(endpoint_url, query_senate)
senate_df = clean_results(senate_results["results"]["bindings"])

reps_results = get_results(endpoint_url, query_reps)
reps_df = clean_results(reps_results["results"]["bindings"])

edu_cols = [["edu" "eduLabel"]]
education_df: pd.DataFrame = pd.concat(
    [reps_df[["edu", "eduLabel"]], senate_df[["edu", "eduLabel"]]]
)
education_df.dropna().drop_duplicates(["edu"], inplace=True)
education_df.sort_values(["eduLabel"], inplace=True)
education_df["is_university"] = education_df["eduLabel"].str.contains("University")
