#!/usr/bin/env python3
"""Verify the incremental APPEND path end-to-end against REAL Oxigraph + Morph-KGC.

Drives the actual API (``build_app`` + ``TestClient``) wired to a real
``OxigraphClient``, exercising the live HTTP Graph Store path (not a stub):

  1. register a dataset (RML keyed on ``id`` + base CSV source)
  2. ingest (snapshot)            -> stage version graph v1
  3. promote                      -> v1 becomes the live, citable graph
  4. append a NEW batch CSV       -> the live graph grows by O(new) rows
  5. assert a base IRI is UNCHANGED (append only adds; existing triples untouched)
  6. re-append the SAME batch     -> idempotent (deterministic IRIs dedupe, no growth)

This is the empirical proof for ADR ``docs/architecture/incremental-ingest.md``:
"CSV added -> triples increase, existing IRIs unchanged". Trust model is unchanged —
Morph-KGC interprets the RML, only Tier 0 functions run, and the append is a Graph
Store POST (the ingest write path), never a SPARQL UPDATE.

Run with the api venv against a THROWAWAY Oxigraph (NOT the shared :7878):

  docker run -d --name oxi -p 7879:7878 ghcr.io/oxigraph/oxigraph:latest \\
    serve --location /data --bind 0.0.0.0:7878
  CSV2RDF_OXIGRAPH_URL=http://127.0.0.1:7879 \\
    api/.venv/bin/python experiments/incremental-append/verify.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

from asterism.oxigraph_client import OxigraphClient, OxigraphConfig
from fastapi.testclient import TestClient

from asterism_api import registry
from asterism_api.main import Settings, build_app

OXI = os.environ.get("CSV2RDF_OXIGRAPH_URL", "http://127.0.0.1:7879")

# A deterministic RML: subject IRI is templated from the key column ``id`` (so the
# same row always yields the same IRI — the premise that makes append idempotent),
# with one plain-reference predicate. No functions needed for the shape under test.
RML = """
@prefix rr:   <http://www.w3.org/ns/r2rml#> .
@prefix rml:  <http://semweb.mmlab.be/ns/rml#> .
@prefix ql:   <http://semweb.mmlab.be/ns/ql#> .
@prefix ex:   <https://ex/> .
<#M> a rr:TriplesMap ;
  rml:logicalSource [ rml:source "d.csv" ; rml:referenceFormulation ql:CSV ] ;
  rr:subjectMap [ rr:template "https://ex/r/{id}" ] ;
  rr:predicateObjectMap [ rr:predicate ex:name ; rr:objectMap [ rml:reference "name" ] ] .
"""


def sparql(query: str) -> dict:
    """Synchronous SPARQL query to Oxigraph (avoids cross-event-loop httpx reuse)."""
    req = urllib.request.Request(
        f"{OXI}/query",
        data=query.encode(),
        headers={
            "Content-Type": "application/sparql-query",
            "Accept": "application/sparql-results+json",
        },
    )
    with urllib.request.urlopen(req) as resp:
        return json.load(resp)


def count_graph(graph_iri: str) -> int:
    d = sparql(f"SELECT (COUNT(*) AS ?c) WHERE {{ GRAPH <{graph_iri}> {{ ?s ?p ?o }} }}")
    return int(d["results"]["bindings"][0]["c"]["value"])


def ask_triple(graph_iri: str, s: str, p: str, o: str) -> bool:
    d = sparql(f'ASK {{ GRAPH <{graph_iri}> {{ <{s}> <{p}> "{o}" }} }}')
    return bool(d.get("boolean"))


def drive_job(client: TestClient, resp) -> dict:
    """For a 202 background job (ingest), drain the SSE stream and return the done result."""
    assert resp.status_code == 202, (resp.status_code, resp.text)
    job_id = resp.json()["job_id"]
    body = client.get(f"/api/jobs/{job_id}/stream").text
    name = ""
    done = None
    for line in body.splitlines():
        if line.startswith("event:"):
            name = line[len("event:") :].strip()
        elif line.startswith("data:") and name == "done":
            done = json.loads(line[len("data:") :].strip())["result"]
    assert done is not None, f"no done event in stream:\n{body}"
    return done


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="append-verify-"))
    settings = Settings(
        {
            "CSV2RDF_DROP_ROOT": str(tmp / "csv"),
            "CSV2RDF_RDF_ROOT": str(tmp / "rdf"),
            "CSV2RDF_ERROR_ROOT": str(tmp / "errors"),
            "CSV2RDF_JOBS_LOG": str(tmp / "jobs.jsonl"),
            "CSV2RDF_REGISTRY_ROOT": str(tmp / "registry"),
            "CSV2RDF_OXIGRAPH_URL": OXI,
        }
    )
    oxi = OxigraphClient(OxigraphConfig(base_url=OXI))
    app = build_app(settings, oxigraph_client=oxi, start_watcher=False)

    dataset_id = registry.save_dataset(
        tmp / "registry",
        "append-feed",
        {
            "diagram.md": "classDiagram\n  class R",
            "model.yaml": "- R:",
            "mie.yaml": "schema_info:\n  title: feed",
            "ingester.py": "def go(): ...",
            "mapping.rml.ttl": RML,
        },
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
        created_at="2026-06-10T00:00:00+00:00",
    )["id"]

    base_csv = b"id,name\n1,a\n2,b\n"
    batch_csv = b"id,name\n3,c\n"  # ONLY the new row (O(new) materialize)

    with TestClient(app) as client:
        # 2. ingest (snapshot) the base CSV -> stage v1
        done = drive_job(
            client,
            client.post(
                f"/api/datasets/{dataset_id}/ingest",
                files={"files": ("d.csv", base_csv, "text/csv")},
            ),
        )
        print(f"ingest:  staged {done['graph_iri']} ({done['triple_count']} triples)")

        # 3. promote v1 -> live, citable
        promote = client.post(f"/api/datasets/{dataset_id}/promote").json()
        live = promote["live_graph"]
        print(f"promote: live graph = {live} ({promote['triples_promoted']} triples)")

        n_before = count_graph(live)
        base_iri_before = ask_triple(live, "https://ex/r/1", "https://ex/name", "a")
        print(f"before:  live = {n_before} triples; base IRI r/1 present = {base_iri_before}")
        assert n_before == 2, n_before
        assert base_iri_before

        # 4. append a NEW batch -> live grows by O(new)
        ap = client.post(
            f"/api/datasets/{dataset_id}/append",
            files={"files": ("d.csv", batch_csv, "text/csv")},
        )
        assert ap.status_code == 200, ap.text
        apb = ap.json()
        print(
            f"append1: +{apb['triples_in_batch']} into {apb['live_graph']} "
            f"(seq={apb['append_seq']}, crosswalk_stale={apb['crosswalk_stale']})"
        )
        assert apb["live_graph"] == live

        n_after = count_graph(live)
        base_iri_after = ask_triple(live, "https://ex/r/1", "https://ex/name", "a")
        print(f"after:   live = {n_after} triples; base IRI r/1 unchanged = {base_iri_after}")
        assert n_after == 3, n_after  # grew by exactly the new row
        assert base_iri_after  # existing IRI untouched

        # 5. re-append the SAME batch -> idempotent (deterministic IRI dedupes)
        ap2 = client.post(
            f"/api/datasets/{dataset_id}/append",
            files={"files": ("d.csv", batch_csv, "text/csv")},
        )
        assert ap2.status_code == 200, ap2.text
        n_re = count_graph(live)
        print(f"append2: same batch -> live = {n_re} triples (idempotent, no growth)")
        assert n_re == 3, n_re

        # snapshot reproducibility: the batch was accumulated into the source set.
        src = (tmp / "registry" / dataset_id / "source" / "d.csv").read_text().split()
        print(f"source:  accumulated d.csv rows = {src}")
        assert src == ["id,name", "1,a", "2,b", "3,c", "3,c"], src

    print(
        "\nVERIFIED: CSV added -> triples 2 -> 3 (+1 new), base IRI unchanged, "
        "re-append idempotent.\nReal Morph-KGC + real Oxigraph, via the actual "
        "/ingest -> /promote -> /append API."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
