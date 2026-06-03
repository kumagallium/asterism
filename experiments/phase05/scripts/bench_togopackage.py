"""togopackage 素振り計測スクリプト。

設計プラン §10 / handoff §4.1, §4.4 に従って:
- /sparql の生存確認 + 5 クエリレイテンシ
- 既存 source への SPARQL UPDATE が効くか (QLever デフォルト構成では PERSIST_UPDATES false)

複数 source の追加 / reload の挙動は test_reload.sh で手動確認する
(restart が必要なため Python だけで完結しない)。
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from urllib import error, parse, request

SPARQL = "http://localhost:10005/sparql"


def get_query(q: str, timeout: float = 30.0) -> tuple[float, dict]:
    url = f"{SPARQL}?{parse.urlencode({'query': q})}"
    req = request.Request(url, method="GET")
    req.add_header("Accept", "application/sparql-results+json")
    t0 = time.perf_counter()
    with request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        body = resp.read()
    dt = time.perf_counter() - t0
    return dt, json.loads(body)


def update(u: str, timeout: float = 30.0) -> tuple[int, str]:
    req = request.Request(SPARQL, data=u.encode(), method="POST")
    req.add_header("Content-Type", "application/sparql-update")
    try:
        with request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


QUERIES = {
    "Q1_count": "SELECT (COUNT(*) AS ?c) WHERE { ?s ?p ?o }",
    "Q2_papers": (
        "PREFIX sd: <http://localhost/asterism/starrydata/ontology#>\n"
        "SELECT ?p WHERE { ?p a sd:Paper } LIMIT 100"
    ),
    "Q3_titles": (
        "PREFIX schema: <https://schema.org/>\n"
        "SELECT ?p ?t WHERE { ?p a <http://localhost/asterism/starrydata/ontology#Paper> ; "
        "schema:name ?t } LIMIT 100"
    ),
    "Q4_authors_per_paper": (
        "PREFIX schema: <https://schema.org/>\n"
        "PREFIX sd: <http://localhost/asterism/starrydata/ontology#>\n"
        "SELECT ?p (COUNT(?a) AS ?n) WHERE { ?p a sd:Paper ; schema:author ?a } "
        "GROUP BY ?p ORDER BY DESC(?n) LIMIT 20"
    ),
    "Q5_filter_year": (
        "PREFIX schema: <https://schema.org/>\n"
        "PREFIX sd: <http://localhost/asterism/starrydata/ontology#>\n"
        "SELECT ?p ?d WHERE { ?p a sd:Paper ; schema:datePublished ?d "
        "FILTER (?d >= \"2015-01-01\"^^<http://www.w3.org/2001/XMLSchema#date>) } LIMIT 50"
    ),
}


def main() -> int:
    print(f"== togopackage bench (endpoint={SPARQL})", file=sys.stderr)

    dt, j = get_query(QUERIES["Q1_count"])
    n0 = int(j["results"]["bindings"][0]["c"]["value"])
    print(f"baseline COUNT: {n0} ({dt*1000:.2f}ms)")

    print("\n-- query latency (3 warmup + 5 measured) --")
    summary = []
    for name, q in QUERIES.items():
        for _ in range(3):
            get_query(q)
        ts = [get_query(q)[0] for _ in range(5)]
        mean = statistics.mean(ts)
        p95 = max(ts)
        summary.append((name, mean, p95))
        print(f"  {name}: mean={mean*1000:.2f}ms p95={p95*1000:.2f}ms")

    print("\n-- SPARQL UPDATE attempt (default QLever does NOT persist) --")
    status, body = update(
        "PREFIX sd: <http://localhost/asterism/starrydata/ontology#>\n"
        "INSERT DATA { GRAPH <http://localhost/asterism/starrydata/graph/subset-a> "
        "{ <http://localhost/asterism/starrydata/resource/paper/spike-1> a sd:Paper ; "
        "<https://schema.org/name> \"INSERTED VIA SPARQL UPDATE\" } }"
    )
    print(f"UPDATE status={status}, body[:200]={body[:200]!r}")

    dt2, j2 = get_query(QUERIES["Q1_count"])
    n1 = int(j2["results"]["bindings"][0]["c"]["value"])
    print(f"COUNT after UPDATE attempt: {n1} (delta {n1 - n0})")

    print("\n== summary ==")
    print(json.dumps(
        {
            "baseline_count": n0,
            "after_update_count": n1,
            "update_status": status,
            "queries": [
                {"name": n, "mean_ms": m * 1000, "p95_ms": p * 1000}
                for n, m, p in summary
            ],
        },
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
