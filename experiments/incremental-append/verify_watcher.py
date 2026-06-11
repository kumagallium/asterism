#!/usr/bin/env python3
"""Verify the per-dataset APPEND WATCHER end-to-end (real awatch + Oxigraph + Morph-KGC).

Companion to ``verify.py`` (which drives the ``/append`` endpoint). This one proves the
unattended device-feed path (ADR ``incremental-ingest.md`` §6): a CSV **dropped** into
``<append_drop_root>/<dataset_id>/`` is picked up by the running append watcher, appended
to the dataset's live feed, and the transient drop file is consumed.

  1. register + ingest + promote a dataset (establish a live feed)
  2. start the app with the append watcher running (``start_watcher=True``)
  3. drop a NEW batch CSV into the dataset's append inbox
  4. wait for the watcher to consume it -> the live graph grows, base IRI unchanged

This is timing-sensitive (it relies on real filesystem events + the settle delay), so it
polls with a timeout; it is a manual repro tool, NOT a CI test (the watcher loop's logic
is covered deterministically by injected-events unit tests).

Run with the api venv against a THROWAWAY Oxigraph (NOT the shared :7878):

  docker run -d --name oxi -p 7879:7878 ghcr.io/oxigraph/oxigraph:latest \\
    serve --location /data --bind 0.0.0.0:7878
  CSV2RDF_OXIGRAPH_URL=http://127.0.0.1:7879 \\
    api/.venv/bin/python experiments/incremental-append/verify_watcher.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from asterism.oxigraph_client import OxigraphClient, OxigraphConfig
from fastapi.testclient import TestClient

from asterism_api import registry
from asterism_api.main import Settings, build_app

OXI = os.environ.get("CSV2RDF_OXIGRAPH_URL", "http://127.0.0.1:7879")

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


def drive_job(client: TestClient, resp) -> dict:
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
    assert done is not None, body
    return done


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="append-wverify-"))
    settings = Settings(
        {
            "CSV2RDF_DROP_ROOT": str(tmp / "csv"),
            "CSV2RDF_RDF_ROOT": str(tmp / "rdf"),
            "CSV2RDF_ERROR_ROOT": str(tmp / "errors"),
            "CSV2RDF_JOBS_LOG": str(tmp / "jobs.jsonl"),
            "CSV2RDF_REGISTRY_ROOT": str(tmp / "registry"),
            "CSV2RDF_OXIGRAPH_URL": OXI,
            "CSV2RDF_SETTLE_S": "0.1",
            "ASTERISM_APPEND_DROP_ROOT": str(tmp / "append"),
        }
    )
    oxi = OxigraphClient(OxigraphConfig(base_url=OXI))
    # start_watcher=True so the real append watcher (awatch on the inbox) runs.
    app = build_app(settings, oxigraph_client=oxi, start_watcher=True)

    dataset_id = registry.save_dataset(
        tmp / "registry",
        "wfeed",
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
        created_at="2026-06-11T00:00:00+00:00",
    )["id"]

    # Pre-create the dataset's inbox BEFORE the watcher starts, so the drop is a plain
    # file-add into an already-watched dir (avoids a new-subdir recursive-watch race).
    inbox = settings.append_drop_root / dataset_id
    inbox.mkdir(parents=True, exist_ok=True)

    with TestClient(app) as client:
        drive_job(
            client,
            client.post(
                f"/api/datasets/{dataset_id}/ingest",
                files={"files": ("d.csv", b"id,name\n1,a\n2,b\n", "text/csv")},
            ),
        )
        promote = client.post(f"/api/datasets/{dataset_id}/promote").json()
        live = promote["live_graph"]
        print(f"promote: live = {live} ({count_graph(live)} triples)")
        assert count_graph(live) == 2

        # Drop a NEW batch into the dataset's append inbox; the running watcher picks
        # it up (no HTTP call).
        drop = inbox / "d.csv"
        tmp_drop = inbox / "d.csv.partial"
        tmp_drop.write_bytes(b"id,name\n3,c\n")
        os.replace(tmp_drop, drop)  # atomic publish (a single awatch "added" event)
        print(f"dropped: {drop}")

        # Wait for the watcher to consume the drop (file deleted) and the graph to grow.
        deadline = time.monotonic() + 20.0
        consumed = False
        grew = False
        while time.monotonic() < deadline:
            if not drop.exists():
                consumed = True
            if count_graph(live) == 3:
                grew = True
            if consumed and grew:
                break
            time.sleep(0.25)

        n = count_graph(live)
        base_ok = bool(
            sparql(f'ASK {{ GRAPH <{live}> {{ <https://ex/r/1> <https://ex/name> "a" }} }}').get(
                "boolean"
            )
        )
        print(f"after watcher: live = {n} triples; drop consumed = {consumed}; base IRI ok = {base_ok}")
        meta = registry.load_dataset(settings.registry_root, dataset_id)["meta"]
        print(f"meta: feed={meta.get('feed')} append_seq={meta.get('append_seq')} appends={len(meta.get('appends', []))}")
        assert grew and n == 3, f"live did not grow to 3 (got {n})"
        assert consumed, "drop file was not consumed by the watcher"
        assert base_ok, "base IRI changed"
        assert meta.get("feed") is True and meta.get("append_seq") == 1

    print(
        "\nVERIFIED: a dropped CSV was auto-appended by the watcher -> live 2 -> 3, "
        "base IRI unchanged, drop consumed. Real awatch + Morph-KGC + Oxigraph."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
