"""Real-mode endpoint tests for the demo-agent.

``scripts/verify_demo.py`` exercises the routing + compose helpers directly;
this hits the actual FastAPI endpoints in **real** mode by injecting an
in-memory SPARQL client (rdflib) into the app, so the ``/demo/ask`` and
``/demo/provenance`` real branches are covered without a live Oxigraph.

Run: pytest demo-agent/test_app.py
"""

from __future__ import annotations

import json
import re

import rdflib
from fastapi.testclient import TestClient

import app as demo
from asterism.starrydata import DEFAULT_ONTOLOGY as SD
from asterism.starrydata import DEFAULT_RESOURCE as SDR

_TTL = f"""
@prefix sd: <{SD}> .
@prefix schema: <https://schema.org/> .
@prefix prov: <http://www.w3.org/ns/prov#> .
@prefix dcterms: <http://purl.org/dc/terms/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

<{SDR}paper/1> a sd:Paper ; schema:name "SnSe paper" ; dcterms:identifier "10.1/x" ;
    prov:wasGeneratedBy <{SDR}ingestion/1> .
<{SDR}sample/1-1> a sd:Sample ; sd:compositionString "SnSe" ; schema:name "s" ;
    sd:fromPaper <{SDR}paper/1> ; prov:wasGeneratedBy <{SDR}ingestion/1> .
<{SDR}curve/1-1-1> a sd:Curve ; sd:propertyY "ZT" ; sd:yMax "2.6"^^xsd:double ;
    sd:figureName "Fig.3" ; sd:ofSample <{SDR}sample/1-1> ;
    prov:wasGeneratedBy <{SDR}ingestion/1> , <{SDR}digitization/1> .
<{SDR}curve/1-1-2> a sd:Curve ; sd:propertyY "ZT" ; sd:yMax "13000.0"^^xsd:double ;
    sd:ofSample <{SDR}sample/1-1> ; prov:wasGeneratedBy <{SDR}ingestion/1> .
<{SDR}ingestion/1> a sd:IngestionActivity ;
    prov:atTime "2026-05-01T00:00:00Z"^^xsd:dateTime .
<{SDR}digitization/1> a sd:DigitizationActivity ;
    prov:atTime "2020-01-01T00:00:00Z"^^xsd:dateTime .
"""


class _LocalClient:
    def __init__(self, graph: rdflib.Graph) -> None:
        self._g = graph

    async def sparql_select(self, query: str) -> dict:
        raw = self._g.query(query).serialize(format="json")
        if isinstance(raw, bytes):
            raw = raw.decode()
        return json.loads(raw)


def _real_client(monkeypatch) -> TestClient:
    g = rdflib.Graph()
    g.parse(data=_TTL, format="turtle")
    demo._state["client"] = _LocalClient(g)
    monkeypatch.setattr(demo, "_REAL", True)
    return TestClient(demo.app)


def test_ask_zt_real_endpoint(monkeypatch) -> None:
    client = _real_client(monkeypatch)
    body = client.post(
        "/demo/ask", json={"question": "ZTが最も高い熱電材料は？"}
    ).json()
    assert "ZT" in body["answer"]
    assert body["citations"], "expected at least one citation"
    # the 13000 outlier must be excluded and reported honestly
    assert body["notes"], "expected a data-quality note for the excluded outlier"


def test_provenance_real_endpoint(monkeypatch) -> None:
    client = _real_client(monkeypatch)
    body = client.get("/demo/provenance", params={"iri": f"{SDR}curve/1-1-1"}).json()
    steps = [s["step"] for s in body["chain"]]
    assert steps[0] == "curve"
    assert "ingestion" in steps


def test_health_reports_mode() -> None:
    assert TestClient(demo.app).get("/health").json()["mode"] in {"mock", "real"}


# --- #18 schema-agnostic foundation endpoints (real mode) ------------------


def test_schema_endpoint_introspects_vocabulary(monkeypatch) -> None:
    client = _real_client(monkeypatch)
    body = client.get("/demo/schema").json()
    class_names = {re.split(r"[/#]", c["iri"])[-1] for c in body["classes"]}
    # The fixture has Curve / Sample / Paper plus the activity classes.
    assert {"Curve", "Sample", "Paper"} <= class_names
    pred_iris = {p["iri"] for p in body["predicates"]}
    assert f"{SD}propertyY" in pred_iris
    # Every class carries a (possibly empty) predicate shape.
    assert all("predicates" in s for s in body["class_shapes"])


def test_sparql_endpoint_runs_select(monkeypatch) -> None:
    client = _real_client(monkeypatch)
    q = (
        f"SELECT ?c ?y WHERE {{ ?c <{SD}propertyY> \"ZT\" ; "
        f"<{SD}yMax> ?y }} ORDER BY DESC(?y)"
    )
    body = client.post("/demo/sparql", json={"query": q}).json()
    assert body["columns"] == ["c", "y"]
    assert body["count"] == 2  # curve 1-1-1 and the 13000 outlier
    assert body["rows"][0]["y"]["value"].startswith("13000")


def test_sparql_endpoint_rejects_update(monkeypatch) -> None:
    client = _real_client(monkeypatch)
    body = client.post(
        "/demo/sparql", json={"query": "DELETE WHERE { ?s ?p ?o }"}
    ).json()
    assert "error" in body and body["rows"] == []
