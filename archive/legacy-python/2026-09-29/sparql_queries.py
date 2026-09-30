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
  SERVICE wikibase:label { bd:serviceParam wikibase:language "[AUTO_LANGUAGE],en". }
}
ORDER BY ?start ?end
"""
