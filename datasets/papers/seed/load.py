"""Wait for Oxigraph, then bulk-load every ``*.ttl`` in the seed dir into the
``canonical/papers`` named graph (SPARQL 1.1 Graph Store Protocol).

The papers example (document-ontology layer) is the structured full text of a
research paper. Its seed (``paper.ttl``) is committed because it is built from a
CC-BY source (PMC5951533) — like materials_project's ``mp.ttl`` (also CC-BY) and
unlike the licensed starrydata demo seed. This loader pushes the committed
``paper.ttl`` into the dataset's own canonical named graph so it joins the others
across the #20 FROM-merge (it is "just another promoted graph").

The graph IRI is fixed (Asterism IRIs are immutable) and mirrors
``asterism.substrate.canonical_graph_iri("papers")``; override via argv if needed.
Pure stdlib (urllib) so the python:slim image needs no pip install. Idempotent:
Oxigraph's set semantics dedupe on re-run (the seed is byte-stable; the only
timestamp, the parse activity's prov:endedAtTime, is a fixed constant).

Usage: python load.py <oxigraph_url> [seed_dir] [graph_iri]
"""

from __future__ import annotations

import glob
import os
import sys
import time
import urllib.parse
import urllib.request

# Must match asterism.substrate.canonical_graph_iri("papers").
PAPERS_CANONICAL_GRAPH = "https://kumagallium.github.io/asterism/graph/canonical/papers"


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
    seed_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.dirname(os.path.abspath(__file__))
    graph_iri = sys.argv[3] if len(sys.argv) > 3 else PAPERS_CANONICAL_GRAPH
    if not _wait(url):
        print(f"oxigraph not reachable at {url}", file=sys.stderr)
        return 1
    files = sorted(glob.glob(os.path.join(seed_dir, "*.ttl")))
    if not files:
        print(f"no .ttl in {seed_dir} — run seed/build_paper_graph.py first", file=sys.stderr)
        return 1
    for f in files:
        with open(f, "rb") as fh:
            data = fh.read()
        status = _post_turtle(url, data, graph_iri)
        print(f"loaded {f} ({len(data)} bytes) -> {graph_iri} HTTP {status}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
