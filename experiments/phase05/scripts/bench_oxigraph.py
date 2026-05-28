"""Oxigraph 素振り計測スクリプト。

handoff §4.2 / §4.4 に従って以下を計測する:
- 初回ロード時間 (papers_100.ttl)
- 追記 (SPARQL 1.1 Update INSERT DATA) が効くか
- 5 クエリ平均レイテンシ
- 同じ Turtle を再投入したときの動作 (graph 単位の上書き / 単純追記の挙動)

Oxigraph は SPARQL 1.1 Protocol を素直に喋るので requests で十分。
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path
from urllib import error, parse, request

ENDPOINT = "http://localhost:7878"
HERE = Path(__file__).resolve().parent
TTL = HERE.parent / "data" / "papers_100.ttl"


def post(url: str, data: bytes, content_type: str) -> tuple[int, bytes]:
    req = request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", content_type)
    with request.urlopen(req) as resp:  # noqa: S310 (trusted local URL)
        return resp.status, resp.read()


def get(url: str, accept: str = "application/sparql-results+json") -> tuple[int, bytes]:
    req = request.Request(url, method="GET")
    req.add_header("Accept", accept)
    with request.urlopen(req) as resp:  # noqa: S310
        return resp.status, resp.read()


def wait_ready(timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with request.urlopen(f"{ENDPOINT}/", timeout=2) as _:  # noqa: S310
                return
        except (error.URLError, ConnectionError):
            time.sleep(0.5)
    raise SystemExit(f"Oxigraph did not become ready within {timeout}s")


def load_ttl(path: Path) -> float:
    data = path.read_bytes()
    t0 = time.perf_counter()
    status, _ = post(
        f"{ENDPOINT}/store?default", data, "text/turtle; charset=utf-8"
    )
    dt = time.perf_counter() - t0
    if status not in (200, 201, 204):
        raise SystemExit(f"load failed: HTTP {status}")
    return dt


def query(q: str) -> tuple[float, dict]:
    url = f"{ENDPOINT}/query?{parse.urlencode({'query': q})}"
    t0 = time.perf_counter()
    status, body = get(url)
    dt = time.perf_counter() - t0
    if status != 200:
        raise SystemExit(f"query failed: HTTP {status}: {body[:200]!r}")
    return dt, json.loads(body)


def update(u: str) -> float:
    url = f"{ENDPOINT}/update"
    t0 = time.perf_counter()
    status, _ = post(url, u.encode(), "application/sparql-update")
    dt = time.perf_counter() - t0
    if status not in (200, 204):
        raise SystemExit(f"update failed: HTTP {status}")
    return dt


def count() -> int:
    _, j = query("SELECT (COUNT(*) AS ?c) WHERE { ?s ?p ?o }")
    return int(j["results"]["bindings"][0]["c"]["value"])


QUERIES = {
    "Q1_count": "SELECT (COUNT(*) AS ?c) WHERE { ?s ?p ?o }",
    "Q2_papers": (
        "PREFIX sd: <http://localhost/csv2rdf/starrydata/ontology#>\n"
        "SELECT ?p WHERE { ?p a sd:Paper } LIMIT 100"
    ),
    "Q3_titles": (
        "PREFIX schema: <https://schema.org/>\n"
        "SELECT ?p ?t WHERE { ?p a <http://localhost/csv2rdf/starrydata/ontology#Paper> ; "
        "schema:name ?t } LIMIT 100"
    ),
    "Q4_authors_per_paper": (
        "PREFIX schema: <https://schema.org/>\n"
        "PREFIX sd: <http://localhost/csv2rdf/starrydata/ontology#>\n"
        "SELECT ?p (COUNT(?a) AS ?n) WHERE { ?p a sd:Paper ; schema:author ?a } "
        "GROUP BY ?p ORDER BY DESC(?n) LIMIT 20"
    ),
    "Q5_filter_year": (
        "PREFIX schema: <https://schema.org/>\n"
        "PREFIX sd: <http://localhost/csv2rdf/starrydata/ontology#>\n"
        "SELECT ?p ?d WHERE { ?p a sd:Paper ; schema:datePublished ?d "
        "FILTER (?d >= \"2015-01-01\"^^<http://www.w3.org/2001/XMLSchema#date>) } LIMIT 50"
    ),
}


def main() -> int:
    print(f"== Oxigraph bench (endpoint={ENDPOINT}, ttl={TTL})", file=sys.stderr)
    wait_ready()

    n0 = count()
    print(f"baseline triples: {n0}")

    dt_load = load_ttl(TTL)
    n1 = count()
    print(f"load: {dt_load:.3f}s, triples now: {n1} (delta {n1 - n0})")

    # 5 クエリ平均 (3 回 warm-up + 5 計測)
    print("\n-- query latency (3 warmup + 5 measured) --")
    summary = []
    for name, q in QUERIES.items():
        for _ in range(3):
            query(q)
        ts = [query(q)[0] for _ in range(5)]
        mean = statistics.mean(ts)
        p95 = max(ts)
        summary.append((name, mean, p95))
        print(f"  {name}: mean={mean*1000:.2f}ms p95={p95*1000:.2f}ms")

    # 追記 (INSERT DATA)
    print("\n-- SPARQL 1.1 Update INSERT DATA --")
    dt_upd = update(
        "PREFIX sd: <http://localhost/csv2rdf/starrydata/ontology#>\n"
        "INSERT DATA { <http://localhost/csv2rdf/starrydata/resource/paper/spike-1> "
        "a sd:Paper ; <https://schema.org/name> \"INSERTED VIA SPARQL UPDATE\" }"
    )
    n2 = count()
    print(f"INSERT DATA: {dt_upd*1000:.2f}ms, triples now: {n2} (delta {n2 - n1})")

    # 同じ Turtle をもう一度 POST (オプションで graph 指定なし → default graph に merge)
    print("\n-- re-POST same Turtle (incremental?) --")
    dt_re = load_ttl(TTL)
    n3 = count()
    print(f"re-load: {dt_re:.3f}s, triples now: {n3} (delta {n3 - n2})")
    print(
        "  -> default graph に冪等 (set semantics) であれば delta=0、"
        "merge with bnode 重複なら +bnode 数, 完全追記なら +3715 になる"
    )

    print("\n== summary ==")
    print(json.dumps(
        {
            "initial_load_seconds": dt_load,
            "insert_data_ms": dt_upd * 1000,
            "reload_same_ttl_seconds": dt_re,
            "queries": [
                {"name": n, "mean_ms": m * 1000, "p95_ms": p * 1000}
                for n, m, p in summary
            ],
            "triples": {"baseline": n0, "after_load": n1, "after_insert": n2, "after_reload": n3},
        },
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
