"""Wait for Oxigraph, then bulk-load every ``*.ttl`` in the seed dir into the
``canonical/legacy`` named graph (SPARQL 1.1 Graph Store Protocol).

Used by the ``oxigraph-seed`` one-shot service in compose.demo.yaml. Pure
stdlib (urllib) so the python:slim image needs no pip install. Idempotent:
Oxigraph's set semantics dedupe on re-run.

#20 FROM-merge: Ask reads a cross-dataset FROM-merge over the canonical graphs,
which excludes the raw default graph. So the seed lands directly in
``canonical/legacy`` (a canonical named graph) rather than the default graph —
otherwise the seeded demo data would be invisible to Ask once any dataset is
promoted. The IRI is fixed (Asterism IRIs are immutable) and mirrors
``asterism.substrate.canonical_graph_iri("legacy")``; override via argv if
needed. We deliberately do NOT import the asterism package (stdlib-only image).

Usage: python load.py <oxigraph_url> [seed_dir] [graph_iri]
"""

from __future__ import annotations

import glob
import os
import sys
import time
import urllib.parse
import urllib.request

# Must match asterism.substrate.canonical_graph_iri(LEGACY_DATASET_ID).
LEGACY_CANONICAL_GRAPH = "https://kumagallium.github.io/asterism/graph/canonical/legacy"


def _wait(url: str, tries: int = 60) -> bool:
    for _ in range(tries):
        try:
            req = urllib.request.Request(
                url + "/query",
                data=b"ASK { ?s ?p ?o }",
                headers={
                    "Content-Type": "application/sparql-query",
                    "Accept": "application/sparql-results+json",
                },
            )
            urllib.request.urlopen(req, timeout=5)
            return True
        except Exception:
            time.sleep(2)
    return False


def _post_turtle(url: str, data: bytes, graph_iri: str) -> int:
    # Graph Store Protocol: ?graph=<uri> targets a named graph.
    target = url + "/store?" + urllib.parse.urlencode({"graph": graph_iri})
    req = urllib.request.Request(
        target,
        data=data,
        headers={"Content-Type": "text/turtle; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.status


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: load.py <oxigraph_url> [seed_dir] [graph_iri]", file=sys.stderr)
        return 2
    url = sys.argv[1].rstrip("/")
    seed_dir = sys.argv[2] if len(sys.argv) > 2 else "/seed"
    graph_iri = sys.argv[3] if len(sys.argv) > 3 else LEGACY_CANONICAL_GRAPH
    if not _wait(url):
        print(f"oxigraph not reachable at {url}", file=sys.stderr)
        return 1
    files = sorted(glob.glob(os.path.join(seed_dir, "*.ttl")))
    if not files:
        print(
            f"no .ttl in {seed_dir} — run scripts/make_demo_subset.py first",
            file=sys.stderr,
        )
        return 1
    for f in files:
        with open(f, "rb") as fh:
            data = fh.read()
        status = _post_turtle(url, data, graph_iri)
        print(
            f"loaded {f} ({len(data)} bytes) -> {graph_iri} HTTP {status}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
