"""Wait for Oxigraph, then bulk-load every ``*.ttl`` in the seed dir into its
default graph (SPARQL 1.1 Graph Store Protocol).

Used by the ``oxigraph-seed`` one-shot service in compose.demo.yaml. Pure
stdlib (urllib) so the python:slim image needs no pip install. Idempotent:
Oxigraph's set semantics dedupe on re-run.

Usage: python load.py <oxigraph_url> [seed_dir]
"""

from __future__ import annotations

import glob
import os
import sys
import time
import urllib.request


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


def _post_turtle(url: str, data: bytes) -> int:
    req = urllib.request.Request(
        url + "/store?default",
        data=data,
        headers={"Content-Type": "text/turtle; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.status


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: load.py <oxigraph_url> [seed_dir]", file=sys.stderr)
        return 2
    url = sys.argv[1].rstrip("/")
    seed_dir = sys.argv[2] if len(sys.argv) > 2 else "/seed"
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
        status = _post_turtle(url, data)
        print(f"loaded {f} ({len(data)} bytes) -> HTTP {status}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
