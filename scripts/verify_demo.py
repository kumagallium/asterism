#!/usr/bin/env python3
"""Verify the demo end to end against the REAL generated seed — no Docker.

Loads ``datasets/starrydata/seed/*.ttl`` into an in-memory rdflib graph, then drives the
demo-agent's own routing + the asterism typed tools over it: the exact path the
live stack runs, minus HTTP/containers. Proves the demo questions produce
grounded answers + a provenance chain on real ingested data.

Run ``scripts/make_demo_subset.py`` first, then:
    python scripts/verify_demo.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
for _p in ("ingest/src", "mcp/src", "demo-agent"):
    sys.path.insert(0, str(_REPO / _p))

import app as demo  # demo-agent/app.py  # noqa: E402
import rdflib  # noqa: E402

from asterism_mcp.tools import (  # noqa: E402
    property_ranking,
    provenance_of,
    sample_search,
)

SEED = _REPO / "datasets" / "starrydata" / "seed"


class _LocalClient:
    def __init__(self, graph: rdflib.Graph) -> None:
        self._g = graph

    async def sparql_select(self, query: str) -> dict:
        raw = self._g.query(query).serialize(format="json")
        if isinstance(raw, bytes):
            raw = raw.decode()
        return json.loads(raw)


def _load_graph() -> rdflib.Graph:
    ttls = sorted(SEED.glob("*.ttl"))
    if not ttls:
        raise SystemExit("no seed found — run scripts/make_demo_subset.py first")
    g = rdflib.Graph()
    for t in ttls:
        g.parse(t, format="turtle")
    return g


async def _amain() -> int:
    client = _LocalClient(_load_graph())

    # Q1: highest ZT, with honest exclusion of digitization-error outliers.
    kind, arg, mp = demo._route("ZTが最も高い熱電材料は？")
    assert kind == "rank" and arg == "ZT", (kind, arg)
    rank = await property_ranking(client, property_y=arg, top_n=5, max_plausible=mp)
    ans = demo._compose_rank(rank)
    print("Q1 (ZT):", ans["answer"])
    print(f"        excluded={rank['excluded_implausible']} top={rank['results'][:1]}")
    assert rank["results"], "no ZT results in seed"
    assert all(r["value"] <= 3.5 for r in rank["results"]), "outlier leaked past filter"

    # Q2: composition search.
    _, comp, _ = demo._route("Bi2Te3のサンプルはある？")
    res = await sample_search(client, composition=comp or "Bi2Te3", limit=10)
    print("Q2 (search):", demo._compose_search(comp, res)["answer"])

    # Q3: provenance trace of a cited curve from Q1.
    cite = next((c for c in ans["citations"] if c["kind"] == "curve"), None)
    assert cite, "Q1 produced no curve citation to trace"
    prov = await provenance_of(cite["iri"], client)
    steps = [s["step"] for s in prov["chain"]]
    print("Q3 (provenance):", " -> ".join(steps))
    assert prov["found"] and steps[0] == "curve", steps

    print("OK: demo path verified on the real seed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_amain()))
