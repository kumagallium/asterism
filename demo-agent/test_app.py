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
import types

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
    g = rdflib.ConjunctiveGraph()  # quad store: canonical-scope reads use GRAPH (#20 P3)
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


# --- #18 LLM NL->SPARQL escape (auto-fallback) ------------------------------

_EX = "https://example.org/"
_CUSTOM_TTL = f"""
@prefix ex: <{_EX}> .
ex:w1 a ex:Widget ; ex:name "alpha" .
ex:w2 a ex:Widget ; ex:name "beta" .
"""


def _block(**kw) -> types.SimpleNamespace:
    return types.SimpleNamespace(**kw)


class _FakeMessages:
    def __init__(self, outer: "_FakeAnthropic") -> None:
        self._outer = outer

    def create(self, **kwargs):
        self._outer.calls.append(kwargs)
        resp = self._outer.scripted[self._outer.i]
        self._outer.i += 1
        return resp


class _FakeAnthropic:
    """Scripts a fixed sequence of Messages API responses, records every call."""

    def __init__(self, scripted: list) -> None:
        self.scripted = scripted
        self.i = 0
        self.calls: list[dict] = []
        self.messages = _FakeMessages(self)


def _custom_schema_client(monkeypatch, fake: _FakeAnthropic) -> TestClient:
    g = rdflib.ConjunctiveGraph()  # quad store: canonical-scope reads use GRAPH (#20 P3)
    g.parse(data=_CUSTOM_TTL, format="turtle")
    demo._state["client"] = _LocalClient(g)
    monkeypatch.setattr(demo, "_REAL", True)
    monkeypatch.setitem(demo._state, "anthropic_factory", lambda _key: fake)
    return TestClient(demo.app)


def test_llm_escape_fires_when_typed_path_empty(monkeypatch) -> None:
    # Typed tools look for sd:Sample etc., absent in this custom schema → the
    # escape must engage, run the model's SPARQL on the real graph, and answer.
    select = (
        f"SELECT ?w ?n WHERE {{ ?w a <{_EX}Widget> ; <{_EX}name> ?n }}"
    )
    fake = _FakeAnthropic(
        [
            _block(content=[_block(type="tool_use", id="t1", name="run_sparql", input={"query": select})]),
            _block(
                content=[
                    _block(
                        type="tool_use",
                        id="t2",
                        name="submit_answer",
                        input={
                            "answer": "Widget は 2 件あります（alpha, beta）。",
                            "citations": [{"iri": f"{_EX}w1", "kind": "Widget", "label": "alpha"}],
                        },
                    )
                ]
            ),
        ]
    )
    client = _custom_schema_client(monkeypatch, fake)

    body = client.post(
        "/demo/ask", json={"question": "Widget は何件ありますか？"},
        headers={"X-API-Key": "sk-test"},
    ).json()

    assert "Widget" in body["answer"]
    assert body["citations"][0]["iri"] == f"{_EX}w1"
    # The model's query is disclosed via the dedicated `sparql` field (UI panel),
    # and it was actually executed + fed back to the model.
    assert select in body["sparql"]
    fed_back = json.dumps(fake.calls[1]["messages"], ensure_ascii=False)
    assert "alpha" in fed_back  # the real rows reached the model on turn 2


def test_llm_escape_discloses_from_merged_query(monkeypatch) -> None:
    # When the data lives in a canonical named graph, the escape's SELECT is
    # rewritten to read the cross-dataset FROM-merge (#20); the disclosure must
    # show that effective query (with FROM), not the raw model SELECT.
    from asterism.substrate import (
        CONTROL_GRAPH_IRI,
        LEGACY_DATASET_ID,
        STATUS_PREDICATE,
        STATUS_PROMOTED,
        canonical_graph_iri,
    )

    legacy = canonical_graph_iri(LEGACY_DATASET_ID)
    ds = rdflib.ConjunctiveGraph()
    ds.get_context(rdflib.URIRef(legacy)).parse(data=_CUSTOM_TTL, format="turtle")
    # Flag the canonical graph promoted (as ingest+promote would) so the FROM-merge
    # — which enumerates only promoted canonical graphs — includes it.
    ds.get_context(rdflib.URIRef(CONTROL_GRAPH_IRI)).add(
        (rdflib.URIRef(legacy), rdflib.URIRef(STATUS_PREDICATE), rdflib.Literal(STATUS_PROMOTED))
    )
    demo._state["client"] = _LocalClient(ds)
    monkeypatch.setattr(demo, "_REAL", True)

    select = f"SELECT ?w ?n WHERE {{ ?w a <{_EX}Widget> ; <{_EX}name> ?n }}"
    fake = _FakeAnthropic(
        [
            _block(content=[_block(type="tool_use", id="t1", name="run_sparql", input={"query": select})]),
            _block(
                content=[
                    _block(
                        type="tool_use",
                        id="t2",
                        name="submit_answer",
                        input={"answer": "Widget は 2 件です。", "citations": []},
                    )
                ]
            ),
        ]
    )
    monkeypatch.setitem(demo._state, "anthropic_factory", lambda _key: fake)
    body = TestClient(demo.app).post(
        "/demo/ask", json={"question": "Widget は何件？"}, headers={"X-API-Key": "sk-test"}
    ).json()

    assert "Widget" in body["answer"]
    assert any(f"FROM <{legacy}>" in s for s in body["sparql"])  # effective query disclosed
    assert select not in body["sparql"]  # the raw SELECT was rewritten
    # The FROM-merged query actually returned the rows (fed back to the model).
    fed_back = json.dumps(fake.calls[1]["messages"], ensure_ascii=False)
    assert "alpha" in fed_back


def test_llm_escape_requires_key(monkeypatch) -> None:
    # Same empty-typed-path situation, but no API key → no LLM, just a hint.
    fake = _FakeAnthropic([])
    client = _custom_schema_client(monkeypatch, fake)
    body = client.post("/demo/ask", json={"question": "Widget は何件？"}).json()
    assert fake.calls == []  # the LLM was never invoked
    assert any("API キー" in n for n in body["notes"])


def test_typed_path_still_short_circuits_without_llm(monkeypatch) -> None:
    # Starrydata fixture + a ZT question → typed path answers; LLM untouched even
    # when a key is present.
    g = rdflib.ConjunctiveGraph()  # quad store: canonical-scope reads use GRAPH (#20 P3)
    g.parse(data=_TTL, format="turtle")
    demo._state["client"] = _LocalClient(g)
    monkeypatch.setattr(demo, "_REAL", True)
    fake = _FakeAnthropic([])
    monkeypatch.setitem(demo._state, "anthropic_factory", lambda _key: fake)
    client = TestClient(demo.app)
    body = client.post(
        "/demo/ask", json={"question": "ZTが最も高い材料は？"},
        headers={"X-API-Key": "sk-test"},
    ).json()
    assert "ZT" in body["answer"]
    assert fake.calls == []  # typed path won; no escape
