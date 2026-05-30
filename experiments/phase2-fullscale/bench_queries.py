"""Phase 2 #5 full-scale benchmark — stage 3: SPARQL query latency.

Runs a set of representative queries against a (already-loaded) Oxigraph and
records latency (median of N runs). Mixes Phase 1 queries with the Phase 2
feature queries (QUDT cross-synonym, digitization provenance) so we measure
them at full scale.

Usage:
    python bench_queries.py --url http://localhost:7900 --runs 5 \
        --out experiments/phase2-fullscale/work/query_results.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.request
from urllib.parse import urlencode

SD = "https://kumagallium.github.io/csv2rdf-mcp/starrydata/ontology#"

# NOTE: the Phase 2 watcher loads each kind into a NAMED graph
# (sd:graph/{papers,samples,curves}). SPARQL only sees named-graph triples via
# a GRAPH clause, so every pattern below is wrapped in `GRAPH ?g { ... }`.
# `count_default_graph` is kept deliberately UNwrapped to demonstrate the
# named-graph vs default-graph gap that this benchmark surfaced.
QUERIES: dict[str, str] = {
    "count_default_graph": "SELECT (COUNT(*) AS ?c) WHERE { ?s ?p ?o }",
    "count_all_named": "SELECT (COUNT(*) AS ?c) WHERE { GRAPH ?g { ?s ?p ?o } }",
    "count_by_class": f"""
        PREFIX sd: <{SD}>
        SELECT (COUNT(DISTINCT ?paper) AS ?papers)
               (COUNT(DISTINCT ?sample) AS ?samples)
               (COUNT(DISTINCT ?curve) AS ?curves) WHERE {{ GRAPH ?g {{
          {{ ?paper a sd:Paper }} UNION {{ ?sample a sd:Sample }}
          UNION {{ ?curve a sd:Curve }}
        }} }}""",
    "composition_substring": f"""
        PREFIX sd: <{SD}>
        SELECT ?sample ?comp WHERE {{ GRAPH ?g {{
          ?sample a sd:Sample ; sd:compositionString ?comp .
          FILTER(CONTAINS(LCASE(STR(?comp)), "bi2te3"))
        }} }} LIMIT 20""",
    "qudt_seebeck_crosssynonym": f"""
        PREFIX sd: <{SD}>
        PREFIX qk: <http://qudt.org/vocab/quantitykind/>
        SELECT (COUNT(?curve) AS ?n) WHERE {{ GRAPH ?g {{
          ?curve sd:propertyYQuantity qk:SeebeckCoefficient .
        }} }}""",
    "highest_seebeck_ymax": f"""
        PREFIX sd: <{SD}>
        PREFIX qk: <http://qudt.org/vocab/quantitykind/>
        SELECT ?curve ?yMax WHERE {{ GRAPH ?g {{
          ?curve sd:propertyYQuantity qk:SeebeckCoefficient ; sd:yMax ?yMax .
        }} }} ORDER BY DESC(ABS(?yMax)) LIMIT 10""",
    "digitization_provenance": f"""
        PREFIX prov: <http://www.w3.org/ns/prov#>
        PREFIX sd: <{SD}>
        SELECT ?curve ?atTime WHERE {{ GRAPH ?g {{
          ?curve a sd:Curve ; prov:wasGeneratedBy ?act .
          ?act a sd:DigitizationActivity ; prov:atTime ?atTime .
        }} }} LIMIT 10""",
    "samples_per_paper_top": f"""
        PREFIX sd: <{SD}>
        PREFIX schema: <https://schema.org/>
        SELECT ?paper (COUNT(?sample) AS ?n) WHERE {{ GRAPH ?g {{
          ?sample a sd:Sample ; sd:fromPaper ?paper .
        }} }} GROUP BY ?paper ORDER BY DESC(?n) LIMIT 10""",
}


def _run_query(url: str, query: str) -> tuple[float, int, str]:
    """Return (seconds, result_row_count, first_value_preview)."""
    endpoint = url.rstrip("/") + "/query?" + urlencode({"query": query})
    req = urllib.request.Request(
        endpoint, headers={"Accept": "application/sparql-results+json"}
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=300) as resp:
        body = json.loads(resp.read())
    dt = time.perf_counter() - t0
    bindings = body.get("results", {}).get("bindings", [])
    preview = ""
    if bindings:
        first = bindings[0]
        preview = ", ".join(f"{k}={v.get('value')}" for k, v in first.items())[:90]
    return dt, len(bindings), preview


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:7900")
    p.add_argument("--runs", type=int, default=5)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    results = []
    for name, q in QUERIES.items():
        times = []
        rows = 0
        preview = ""
        for _ in range(args.runs):
            dt, rows, preview = _run_query(args.url, q)
            times.append(dt * 1000)  # ms
        row = {
            "query": name,
            "runs": args.runs,
            "rows": rows,
            "first": preview,
            "ms_median": round(statistics.median(times), 1),
            "ms_min": round(min(times), 1),
            "ms_max": round(max(times), 1),
        }
        results.append(row)
        print(
            f"{name:28s} median={row['ms_median']:8.1f}ms "
            f"min={row['ms_min']:7.1f} max={row['ms_max']:8.1f} rows={rows:<3d} {preview}",
            flush=True,
        )

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
