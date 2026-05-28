"""Phase 1 E2E 検証: MIE の代表クエリを Oxigraph に投げて結果を確認。

設計プラン §10 Phase 1 成功基準 2 「代表的な 5 クエリが結果を返す」に対応。

前提:
- Oxigraph が http://localhost:7878 で起動済み
- papers/samples/curves の ttl がすべて投入済み (subset/ttl/*.ttl)
"""
from __future__ import annotations

import json
import sys
import time
from urllib import parse, request

ENDPOINT = "http://localhost:7878/query"

QUERIES = [
    (
        "Q1 papers count",
        """
        PREFIX sd: <https://kumagallium.github.io/csv2rdf-mcp/starrydata/ontology#>
        SELECT (COUNT(?p) AS ?n) WHERE { ?p a sd:Paper }
        """,
    ),
    (
        "Q2 samples per paper top-3",
        """
        PREFIX sd: <https://kumagallium.github.io/csv2rdf-mcp/starrydata/ontology#>
        PREFIX schema: <https://schema.org/>
        SELECT ?paper ?title (COUNT(?sample) AS ?nSamples) WHERE {
            ?sample a sd:Sample ; sd:fromPaper ?paper .
            ?paper schema:name ?title .
        }
        GROUP BY ?paper ?title ORDER BY DESC(?nSamples) LIMIT 3
        """,
    ),
    (
        "Q3 Bi2Te3-like samples and their curves",
        """
        PREFIX sd: <https://kumagallium.github.io/csv2rdf-mcp/starrydata/ontology#>
        PREFIX schema: <https://schema.org/>
        SELECT ?sample ?name ?comp ?curve ?propY WHERE {
            ?sample a sd:Sample ;
                    sd:compositionString ?comp ;
                    schema:name ?name .
            FILTER(CONTAINS(LCASE(STR(?comp)), "bi2te3"))
            OPTIONAL { ?curve sd:ofSample ?sample ; sd:propertyY ?propY }
        }
        LIMIT 5
        """,
    ),
    (
        "Q4 Seebeck curves with largest |yMax|",
        """
        PREFIX sd: <https://kumagallium.github.io/csv2rdf-mcp/starrydata/ontology#>
        SELECT ?curve ?yMax ?fig WHERE {
            ?curve sd:propertyY "Seebeck coefficient" ;
                   sd:yMax ?yMax ;
                   sd:figureName ?fig .
        }
        ORDER BY DESC(ABS(?yMax)) LIMIT 5
        """,
    ),
    (
        "Q5 PROV-O ingestion run summary",
        """
        PREFIX prov: <http://www.w3.org/ns/prov#>
        PREFIX sd:   <https://kumagallium.github.io/csv2rdf-mcp/starrydata/ontology#>
        SELECT ?activity ?startedAt ?endedAt (COUNT(?entity) AS ?nGenerated) WHERE {
            ?activity a sd:IngestionActivity ;
                      prov:atTime ?startedAt .
            OPTIONAL { ?activity prov:endedAtTime ?endedAt }
            ?entity prov:wasGeneratedBy ?activity .
        }
        GROUP BY ?activity ?startedAt ?endedAt
        ORDER BY ?startedAt
        """,
    ),
]


def run(query: str) -> tuple[float, dict]:
    url = f"{ENDPOINT}?{parse.urlencode({'query': query})}"
    req = request.Request(url, method="GET")
    req.add_header("Accept", "application/sparql-results+json")
    t0 = time.perf_counter()
    with request.urlopen(req, timeout=30) as resp:  # noqa: S310
        body = resp.read()
    dt = time.perf_counter() - t0
    return dt, json.loads(body)


def main() -> int:
    print("Phase 1 E2E queries")
    print("=" * 60)
    for name, q in QUERIES:
        try:
            dt, result = run(q)
            n = len(result["results"]["bindings"])
            print(f"\n[{name}] {dt*1000:.1f}ms, {n} bindings")
            for binding in result["results"]["bindings"][:3]:
                items = []
                for var, val in binding.items():
                    v = val["value"]
                    if len(v) > 60:
                        v = v[:55] + "..."
                    items.append(f"{var}={v!r}")
                print("  " + ", ".join(items))
        except Exception as exc:  # noqa: BLE001
            print(f"\n[{name}] FAIL: {exc!r}")
            return 1
    print()
    print("=" * 60)
    print("ALL 5 QUERIES SUCCEEDED — Phase 1 §10 成功基準 2 達成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
