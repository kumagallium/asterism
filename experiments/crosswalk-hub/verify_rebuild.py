#!/usr/bin/env python3
"""Verify the crosswalk hub REBUILD end-to-end against real Oxigraph + Morph-KGC.

Proves the wiring added in the append-watcher follow-up (ADR incremental-ingest.md §7 /
crosswalk-hub.md #2): the hub is a derived projection over the live canonical scope, and
rebuilding it reads real observations, replaces the hub graph (PUT), flags it promoted,
and the shared entities are then queryable.

  1. ingest + promote dataset A (rows carry a composition via ex:comp)
  2. ingest + promote dataset B (rows carry a composition via ex:formula)
  3. write a crosswalk.yaml participation registry (A.comp / B.formula -> Composition)
  4. POST /api/crosswalk/rebuild -> the hub mints one shared Composition (Bi2Te3) +
     links from BOTH datasets; assert the hub graph is queryable
  5. APPEND a new shared composition (PbTe) to both feeds, rebuild -> the SAME hub grows
     to 2 shared compositions (the "it grows" property, now driven by appends)

Run with the api venv against a THROWAWAY Oxigraph (NOT the shared :7878):

  CSV2RDF_OXIGRAPH_URL=http://127.0.0.1:7879 \\
    api/.venv/bin/python experiments/crosswalk-hub/verify_rebuild.py
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
TOKEN = "verify-token"
AUTH = {"X-Asterism-Token": TOKEN}

XW = "https://kumagallium.github.io/asterism/crosswalk/ontology#"
HUB_GRAPH = "https://kumagallium.github.io/asterism/graph/canonical/crosswalk"


def _rml(source: str, predicate: str) -> str:
    return f"""
@prefix rr:   <http://www.w3.org/ns/r2rml#> .
@prefix rml:  <http://semweb.mmlab.be/ns/rml#> .
@prefix ql:   <http://semweb.mmlab.be/ns/ql#> .
@prefix ex:   <https://ex/> .
<#M> a rr:TriplesMap ;
  rml:logicalSource [ rml:source "{source}" ; rml:referenceFormulation ql:CSV ] ;
  rr:subjectMap [ rr:template "https://ex/{source}/{{id}}" ] ;
  rr:predicateObjectMap [ rr:predicate <{predicate}> ; rr:objectMap [ rml:reference "comp" ] ] .
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


def shared_compositions() -> list[str]:
    rows = sparql(
        f"SELECT ?label WHERE {{ GRAPH <{HUB_GRAPH}> {{ "
        f"?c a <{XW}Composition> ; <http://www.w3.org/2000/01/rdf-schema#label> ?label }} }} "
        "ORDER BY ?label"
    )["results"]["bindings"]
    return [r["label"]["value"] for r in rows]


def link_count() -> int:
    rows = sparql(
        f"SELECT (COUNT(*) AS ?n) WHERE {{ GRAPH <{HUB_GRAPH}> {{ ?e <{XW}hasComposition> ?c }} }}"
    )["results"]["bindings"]
    return int(rows[0]["n"]["value"])


def drive_job(client: TestClient, resp) -> None:
    assert resp.status_code == 202, (resp.status_code, resp.text)
    job_id = resp.json()["job_id"]
    body = client.get(f"/api/jobs/{job_id}/stream").text
    assert "event: done" in body or '"done"' in body, body


def setup_dataset(tmp: Path, name: str, source: str, predicate: str, comp: str) -> str:
    dataset_id = registry.save_dataset(
        tmp / "registry",
        name,
        {
            "diagram.md": "classDiagram\n  class R",
            "model.yaml": "- R:",
            "mie.yaml": "schema_info:\n  title: x",
            "ingester.py": "def go(): ...",
            "mapping.rml.ttl": _rml(source, predicate),
        },
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
        created_at="2026-06-11T00:00:00+00:00",
    )["id"]
    return dataset_id


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="xw-verify-"))
    settings = Settings(
        {
            "CSV2RDF_DROP_ROOT": str(tmp / "csv"),
            "CSV2RDF_RDF_ROOT": str(tmp / "rdf"),
            "CSV2RDF_ERROR_ROOT": str(tmp / "errors"),
            "CSV2RDF_JOBS_LOG": str(tmp / "jobs.jsonl"),
            "CSV2RDF_REGISTRY_ROOT": str(tmp / "registry"),
            "CSV2RDF_OXIGRAPH_URL": OXI,
            "ASTERISM_API_TOKEN": TOKEN,
        }
    )
    oxi = OxigraphClient(OxigraphConfig(base_url=OXI))
    app = build_app(settings, oxigraph_client=oxi, start_watcher=False)

    sd = "https://ex/sd#comp"
    mp = "https://ex/mp#formula"
    a_id = setup_dataset(tmp, "compa", "a.csv", sd, "Bi2Te3")
    b_id = setup_dataset(tmp, "compb", "b.csv", mp, "Bi2Te3")

    # crosswalk.yaml participation registry (default path = registry_root/crosswalk.yaml)
    (tmp / "registry").mkdir(parents=True, exist_ok=True)
    (tmp / "registry" / "crosswalk.yaml").write_text(
        "min_datasets: 2\n"
        "concepts:\n"
        "  - name: composition\n"
        f"    class_iri: {XW}Composition\n"
        f"    link_predicate: {XW}hasComposition\n"
        "    normalizer: composition\n"
        "    rules:\n"
        f"      - {{dataset: compa, predicate: {sd}}}\n"
        f"      - {{dataset: compb, predicate: {mp}}}\n",
        encoding="utf-8",
    )

    with TestClient(app, headers=AUTH) as client:
        for ds, src in ((a_id, "a.csv"), (b_id, "b.csv")):
            drive_job(
                client,
                client.post(
                    f"/api/datasets/{ds}/ingest",
                    files={"files": (src, b"id,comp\n1,Bi2Te3\n", "text/csv")},
                ),
            )
            assert client.post(f"/api/datasets/{ds}/promote").status_code == 200
        print(f"ingest+promote: {a_id}, {b_id} (both report Bi2Te3)")

        r = client.post("/api/crosswalk/rebuild").json()
        print(f"rebuild #1: built={r['built']} shared={r['shared']} links={r['links']}")
        assert r["built"] and r["shared"] == {"composition": 1}
        assert shared_compositions() == ["Bi2Te3"]
        assert link_count() == 2  # one entity from each dataset
        print(f"  hub queryable: shared={shared_compositions()} links={link_count()}")

        # Append a NEW shared composition to both live feeds, then rebuild.
        for ds, src in ((a_id, "a.csv"), (b_id, "b.csv")):
            ap = client.post(
                f"/api/datasets/{ds}/append",
                files={"files": (src, b"id,comp\n2,PbTe\n", "text/csv")},
            )
            assert ap.status_code == 200, ap.text
            assert ap.json()["crosswalk_stale"] is True
        r2 = client.post("/api/crosswalk/rebuild").json()
        print(f"rebuild #2 (after append): shared={r2['shared']}")
        assert r2["shared"] == {"composition": 2}
        assert shared_compositions() == ["Bi2Te3", "PbTe"]  # the SAME hub grew 1 -> 2
        assert link_count() == 4
        print(f"  hub grew: shared={shared_compositions()} links={link_count()}")

    print(
        "\nVERIFIED: crosswalk hub rebuilds from real observations, is queryable, and "
        "GROWS as appends add shared values (1 -> 2 compositions). Real Oxigraph + "
        "Morph-KGC, via /ingest -> /promote -> /append -> /crosswalk/rebuild."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
