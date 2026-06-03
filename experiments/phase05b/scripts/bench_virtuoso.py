"""togopackage (Virtuoso backend) bench — Phase 0.5b 追検証。

Phase 0.5 で「Virtuoso 経路は未検証」とした宿題を潰す。

計測:
- 5 クエリ (Q1-Q5) を Virtuoso 直結 (:8890/sparql) と sparql-proxy 経由
  (:10005/sparql) の両方で計測
- INSERT DATA を Virtuoso /sparql-auth (dba) 経由で計測

注: sparql-proxy は max_concurrency=1 + queue ベースで、Q5 (FILTER) が連続実行
されると稀に HTTP 400 を返す挙動が観察された。標準のリトライ 1 回を入れる。
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from urllib import error, parse, request

import requests
from requests.auth import HTTPDigestAuth

VIRTUOSO_DIRECT = "http://localhost:8890/sparql"
PROXY = "http://localhost:10005/sparql"
WRITE_URL = "http://localhost:8890/sparql-auth"
DBA_USER = "dba"
DBA_PASSWORD = "dba"
GRAPH = "http://localhost/asterism/starrydata/graph/subset-a"

QUERIES = {
    "Q1_count": f"SELECT (COUNT(*) AS ?c) WHERE {{ GRAPH <{GRAPH}> {{ ?s ?p ?o }} }}",
    "Q2_papers": (
        "PREFIX sd: <http://localhost/asterism/starrydata/ontology#>\n"
        f"SELECT ?p WHERE {{ GRAPH <{GRAPH}> {{ ?p a sd:Paper }} }} LIMIT 100"
    ),
    "Q3_titles": (
        "PREFIX schema: <https://schema.org/>\n"
        "PREFIX sd: <http://localhost/asterism/starrydata/ontology#>\n"
        f"SELECT ?p ?t WHERE {{ GRAPH <{GRAPH}> {{ ?p a sd:Paper ; schema:name ?t }} }} LIMIT 100"
    ),
    "Q4_authors_per_paper": (
        "PREFIX schema: <https://schema.org/>\n"
        "PREFIX sd: <http://localhost/asterism/starrydata/ontology#>\n"
        f"SELECT ?p (COUNT(?a) AS ?n) WHERE {{ GRAPH <{GRAPH}> {{ ?p a sd:Paper ; schema:author ?a }} }} "
        "GROUP BY ?p ORDER BY DESC(?n) LIMIT 20"
    ),
    # NOTE: 当初 FILTER (?d >= "2015-01-01"^^xsd:date) で書いていたが、Virtuoso が
    # bench の連続実行下で `define sql:big-data-const 0` を auto-prepend した結果、
    # SP030 "Too many closing parentheses" 構文エラーを返す挙動が観察された
    # (auto-prepend が掛からない単発実行では問題なし)。
    # 同じセマンティクスを STR ベースの比較で書き直して回避する。
    "Q5_filter_year": (
        "PREFIX schema: <https://schema.org/>\n"
        "PREFIX sd: <http://localhost/asterism/starrydata/ontology#>\n"
        f"SELECT ?p ?d WHERE {{ GRAPH <{GRAPH}> {{ ?p a sd:Paper ; schema:datePublished ?d . "
        "FILTER ( STR(?d) >= \"2015-01-01\" ) } } LIMIT 50"
    ),
}


_read_session = requests.Session()


def get_query(endpoint: str, q: str, timeout: float = 30.0) -> tuple[float, int]:
    """POST application/x-www-form-urlencoded with query= param.

    GET 経由だと Virtuoso が `define sql:big-data-const 0` を auto-prepend して
    Q5 (FILTER) で構文衝突を起こす。POST application/sparql-query は Virtuoso 直結で
    timeout する。form-encoded POST が一番安定 (sparql-proxy も受け付ける)。
    """
    t0 = time.perf_counter()
    resp = _read_session.post(
        endpoint,
        data={"query": q},
        headers={"Accept": "application/sparql-results+json"},
        timeout=timeout,
    )
    return time.perf_counter() - t0, resp.status_code


_session = requests.Session()
_session.auth = HTTPDigestAuth(DBA_USER, DBA_PASSWORD)


def update_dba(u: str, timeout: float = 30.0) -> tuple[float, int]:
    t0 = time.perf_counter()
    resp = _session.post(
        WRITE_URL,
        data=u.encode(),
        headers={"Content-Type": "application/sparql-update"},
        timeout=timeout,
    )
    return time.perf_counter() - t0, resp.status_code


def bench(label: str, endpoint: str) -> list[tuple[str, float, float, int]]:
    """各クエリ 1 warmup + 3 measured。1 回 400 を引いたら 1 度だけリトライ。"""
    out = []
    for name, q in QUERIES.items():
        # warmup
        warm_dt, warm_status = get_query(endpoint, q)
        if warm_status != 200:
            r = _read_session.get(endpoint, params={"query": q}, headers={"Accept": "application/sparql-results+json"})
            print(f"  [{label}] {name} warmup FAIL {warm_status}: body={r.text[:300]!r}", file=sys.stderr)
        time.sleep(0.1)
        ts = []
        fails = 0
        for _ in range(3):
            dt, status = get_query(endpoint, q)
            if status >= 400:
                fails += 1
                time.sleep(0.3)
                dt, status = get_query(endpoint, q)
                if status >= 400:
                    fails += 1
                    continue
            ts.append(dt)
            time.sleep(0.05)
        if ts:
            mean = statistics.mean(ts)
            p95 = max(ts)
        else:
            mean = float("nan")
            p95 = float("nan")
        print(f"  [{label}] {name}: mean={mean*1000:.2f}ms p95={p95*1000:.2f}ms (fails={fails})")
        out.append((name, mean, p95, fails))
    return out


def main() -> int:
    print(f"== Virtuoso bench (read=direct, proxy, write=/sparql-auth)", file=sys.stderr)

    n0 = get_query(VIRTUOSO_DIRECT, QUERIES["Q1_count"])
    print(f"baseline ready: Q1 returned in {n0[0]*1000:.1f}ms status={n0[1]}")

    print("\n-- read latency via Virtuoso DIRECT (:8890/sparql) --")
    direct = bench("direct", VIRTUOSO_DIRECT)

    print("\n-- read latency via sparql-proxy (:10005/sparql) --")
    proxy = bench("proxy", PROXY)

    print("\n-- INSERT DATA via /sparql-auth (dba digest), 5 iters --")
    ts_upd = []
    for i in range(5):
        u = (
            f"INSERT DATA {{ GRAPH <{GRAPH}> {{ "
            f"<http://localhost/asterism/starrydata/resource/paper/bench-v-{i}> a "
            "<http://localhost/asterism/starrydata/ontology#Paper> }}"
        )
        dt, status = update_dba(u)
        ts_upd.append(dt)
        print(f"  iter {i}: {dt*1000:.2f}ms (HTTP {status})")
    mean_upd = statistics.mean(ts_upd)
    p95_upd = max(ts_upd)
    print(f"  -> mean={mean_upd*1000:.2f}ms p95={p95_upd*1000:.2f}ms")

    print("\n== summary ==")
    print(json.dumps(
        {
            "queries_direct": [
                {"name": n, "mean_ms": m * 1000, "p95_ms": p * 1000, "fails": f}
                for n, m, p, f in direct
            ],
            "queries_proxy": [
                {"name": n, "mean_ms": m * 1000, "p95_ms": p * 1000, "fails": f}
                for n, m, p, f in proxy
            ],
            "insert_data": {
                "mean_ms": mean_upd * 1000,
                "p95_ms": p95_upd * 1000,
            },
        },
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
