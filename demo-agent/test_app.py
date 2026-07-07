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
    # The raw-SPARQL escape now defaults CLOSED; the real-mode relay tests need it
    # on. Tests that assert the withheld (topology-B) behaviour override this back
    # to False explicitly.
    monkeypatch.setattr(demo, "_EXPOSE_RAW_SPARQL", True)
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


def test_demo_sparql_disabled_returns_403(monkeypatch) -> None:
    """Exposure profile = typed-only: /demo/sparql is withheld (ADR)."""
    monkeypatch.setattr(demo, "_EXPOSE_RAW_SPARQL", False)
    r = TestClient(demo.app).post(
        "/demo/sparql", json={"query": "SELECT ?s WHERE { ?s ?p ?o } LIMIT 1"}
    )
    assert r.status_code == 403


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
    monkeypatch.delenv("ASTERISM_LLM_KEY_ANTHROPIC", raising=False)  # no server fallback
    fake = _FakeAnthropic([])
    client = _custom_schema_client(monkeypatch, fake)
    body = client.post("/demo/ask", json={"question": "Widget は何件？"}).json()
    assert fake.calls == []  # the LLM was never invoked
    assert any("API キー" in n for n in body["notes"])


def test_server_key_fallback_fires_without_header_key(monkeypatch) -> None:
    # Option A: no X-API-Key header, but the operator configured a server-side key
    # (ASTERISM_LLM_KEY_ANTHROPIC) → the escape must fire using that fallback key.
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
                        input={
                            "answer": "Widget は 2 件あります。",
                            "citations": [{"iri": f"{_EX}w1", "kind": "Widget", "label": "alpha"}],
                        },
                    )
                ]
            ),
        ]
    )
    monkeypatch.setenv("ASTERISM_LLM_KEY_ANTHROPIC", "sk-operator")
    client = _custom_schema_client(monkeypatch, fake)
    body = client.post("/demo/ask", json={"question": "Widget は何件？"}).json()  # no header
    assert fake.calls  # the LLM WAS invoked, via the server-side fallback key
    assert "Widget" in body["answer"]


def test_no_key_typed_showcase_answers_zt(monkeypatch) -> None:
    # No key -> the free, deterministic typed showcase answers a ZT question with no
    # LLM (and reports the excluded outlier as a data-quality note).
    g = rdflib.ConjunctiveGraph()  # quad store: canonical-scope reads use GRAPH (#20 P3)
    g.parse(data=_TTL, format="turtle")
    demo._state["client"] = _LocalClient(g)
    monkeypatch.setattr(demo, "_REAL", True)
    fake = _FakeAnthropic([])
    monkeypatch.setitem(demo._state, "anthropic_factory", lambda _key: fake)
    body = TestClient(demo.app).post(
        "/demo/ask", json={"question": "ZTが最も高い材料は？"}  # no X-API-Key
    ).json()
    assert "ZT" in body["answer"]
    assert body["citations"]
    assert fake.calls == []  # no key -> typed path, LLM untouched


def test_key_present_llm_picks_typed_property_ranking(monkeypatch) -> None:
    # P4-2b: with a key, the LLM does the routing — for a clean "highest ZT" question
    # it can CALL the deterministic property_ranking tool (not only raw SPARQL). The
    # tool runs for real and its result is fed back before the model submits.
    g = rdflib.ConjunctiveGraph()
    g.parse(data=_TTL, format="turtle")
    demo._state["client"] = _LocalClient(g)
    monkeypatch.setattr(demo, "_REAL", True)
    fake = _FakeAnthropic(
        [
            _block(content=[_block(type="tool_use", id="t1",
                                   name="starrydata__property_ranking",
                                   input={"property_y": "ZT", "max_plausible": 3.5})]),
            _block(content=[_block(type="tool_use", id="t2", name="submit_answer",
                                   input={"answer": "最大 ZT の材料は …", "citations": []})]),
        ]
    )
    monkeypatch.setitem(demo._state, "anthropic_factory", lambda _key: fake)
    body = TestClient(demo.app).post(
        "/demo/ask", json={"question": "ZTが最も高い熱電材料は？"},
        headers={"X-API-Key": "sk-test"},
    ).json()
    assert len(fake.calls) == 2  # called starrydata__property_ranking, then submitted
    # the 2nd model call carries the property_ranking tool_result (it really ran)
    second = fake.calls[1]["messages"]
    results = [
        b for m in second if isinstance(m.get("content"), list)
        for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result"
    ]
    assert any(r["tool_use_id"] == "t1" for r in results)
    assert "最大 ZT" in body["answer"]
    # P4-2b provenance: property_ranking is a VERIFIED tool, not an escape.
    assert [v["name"] for v in body["verified_tools"]] == ["property_ranking"]
    assert body["unverified_sparql"] is False


def test_ask_llm_excludes_run_sparql_when_exposure_off(monkeypatch) -> None:
    """With raw SPARQL disabled, the Ask LLM is handed ONLY the typed tools."""
    g = rdflib.ConjunctiveGraph()
    g.parse(data=_TTL, format="turtle")
    demo._state["client"] = _LocalClient(g)
    monkeypatch.setattr(demo, "_REAL", True)
    monkeypatch.setattr(demo, "_EXPOSE_RAW_SPARQL", False)
    fake = _FakeAnthropic(
        [
            _block(content=[_block(type="tool_use", id="t1", name="submit_answer",
                                   input={"answer": "型付きツールでは回答できません。",
                                          "citations": []})]),
        ]
    )
    monkeypatch.setitem(demo._state, "anthropic_factory", lambda _key: fake)
    TestClient(demo.app).post(
        "/demo/ask", json={"question": "結晶構造とZTの相関は？"},
        headers={"X-API-Key": "sk-test"},
    )
    offered = {t["name"] for t in fake.calls[0]["tools"]}
    assert "run_sparql" not in offered  # the raw escape is withheld
    # the DECLARED typed tools remain — starrydata's included, as content like
    # every other dataset's (no hardcoded per-vocabulary tools anymore)
    assert {"starrydata__property_ranking", "starrydata__sample_search"} <= offered


def test_generic_question_falls_through_not_canned_samples(monkeypatch) -> None:
    # Regression: a complex question with no ZT/Seebeck keyword and no formula token
    # must NOT be answered by sample_search(composition=None) — that matches EVERY
    # sample, so it always returned citations and permanently blocked the LLM
    # fallback (every such question got the same arbitrary "all samples" list). It
    # must fall through instead; with no key -> the hint, with a key -> the escape.
    g = rdflib.ConjunctiveGraph()  # real starrydata fixture: sample_search(None) WOULD match all
    g.parse(data=_TTL, format="turtle")
    demo._state["client"] = _LocalClient(g)
    monkeypatch.setattr(demo, "_REAL", True)
    fake = _FakeAnthropic([])
    monkeypatch.setitem(demo._state, "anthropic_factory", lambda _key: fake)
    client = TestClient(demo.app)
    q = "熱電材料の性能と結晶構造のリストを出してほしい"

    # no key -> NOT the canned 20-sample answer; falls through to the hint
    body = client.post("/demo/ask", json={"question": q}).json()
    assert body["citations"] == []
    assert "None" not in body["answer"]  # the old "組成 'None' に一致…" bug is gone
    assert any("API キー" in n for n in body["notes"])
    assert fake.calls == []  # no key -> LLM not invoked

    # with a key -> the LLM escape IS invoked for the same generic question
    fake2 = _FakeAnthropic(
        [
            _block(content=[_block(type="tool_use", id="t1", name="submit_answer",
                                   input={"answer": "（探索結果）", "citations": []})]),
        ]
    )
    monkeypatch.setitem(demo._state, "anthropic_factory", lambda _key: fake2)
    client.post("/demo/ask", json={"question": q}, headers={"X-API-Key": "sk-test"}).json()
    assert fake2.calls, "the LLM escape must engage for a generic question when a key is given"


def test_crossdataset_question_defers_despite_zt_keyword(monkeypatch) -> None:
    # Regression: a cross-dataset question (ZT *by crystal structure*) contains the
    # "ZT" keyword, which used to short-circuit to the single-property ZT ranking,
    # ignoring the crystal-structure half. It must defer to the LLM escape instead
    # (which can join starrydata composition == Materials Project formula).
    g = rdflib.ConjunctiveGraph()
    g.parse(data=_TTL, format="turtle")
    demo._state["client"] = _LocalClient(g)
    monkeypatch.setattr(demo, "_REAL", True)
    fake = _FakeAnthropic(
        [
            _block(content=[_block(type="tool_use", id="t1", name="submit_answer",
                                   input={"answer": "（横断結果）", "citations": []})]),
        ]
    )
    monkeypatch.setitem(demo._state, "anthropic_factory", lambda _key: fake)
    body = TestClient(demo.app).post(
        "/demo/ask",
        json={"question": "どんな結晶構造だとどんなZTを示すのでしょうか"},
        headers={"X-API-Key": "sk-test"},
    ).json()
    # Did NOT return the typed ZT ranking; the escape engaged.
    assert fake.calls, "a crystal-structure / ZT cross question must reach the LLM escape"
    assert "最大" not in (body.get("answer") or "")  # not the typed ranking phrasing


# --- P4-2b: every dataset's verified tools route + per-answer provenance -----

_MP = "https://kumagallium.github.io/asterism/materials_project/ontology#"
_MPR = "https://kumagallium.github.io/asterism/materials_project/resource/"


def _promote(ds: rdflib.ConjunctiveGraph, graph_iri: str) -> None:
    from asterism.substrate import CONTROL_GRAPH_IRI, STATUS_PREDICATE, STATUS_PROMOTED

    ds.get_context(rdflib.URIRef(CONTROL_GRAPH_IRI)).add(
        (rdflib.URIRef(graph_iri), rdflib.URIRef(STATUS_PREDICATE), rdflib.Literal(STATUS_PROMOTED))
    )


def test_router_routes_to_mp_verified_content_tool(monkeypatch) -> None:
    # The generality + provenance headline: a structure-property question routes to
    # the Materials Project dataset's OWN declared tool (thermoelectric_structure,
    # loaded from datasets/materials_project/query_tools.yaml — NOT hardcoded), which
    # joins ZT (starrydata) with the crystal structure (MP) across the FROM-merge.
    # It is marked VERIFIED, not an unverified escape.
    from asterism.substrate import canonical_graph_iri

    sd_graph = canonical_graph_iri("starrydata")
    mp_graph = canonical_graph_iri("materials_project")
    sd_ttl = f"""
    @prefix sd: <{SD}> .
    @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
    <{SDR}sample/x> a sd:Sample ; sd:compositionString "SnSe" .
    <{SDR}curve/x> a sd:Curve ; sd:propertyY "ZT" ; sd:yMax "0.82"^^xsd:double ;
        sd:ofSample <{SDR}sample/x> .
    """
    mp_ttl = f"""
    @prefix mp: <{_MP}> .
    <{_MPR}material/mp-691> a mp:Material ; mp:formula "SnSe" ; mp:mpId "mp-691" ;
        mp:hasCrystalStructure <{_MPR}structure/mp-691> .
    <{_MPR}structure/mp-691> a mp:CrystalStructure ; mp:spaceGroupSymbol "Pnma" ;
        mp:spaceGroupNumber 62 ; mp:crystalSystem "Orthorhombic" .
    """
    ds = rdflib.ConjunctiveGraph()
    ds.get_context(rdflib.URIRef(sd_graph)).parse(data=sd_ttl, format="turtle")
    ds.get_context(rdflib.URIRef(mp_graph)).parse(data=mp_ttl, format="turtle")
    _promote(ds, sd_graph)
    _promote(ds, mp_graph)
    demo._state["client"] = _LocalClient(ds)
    monkeypatch.setattr(demo, "_REAL", True)

    fake = _FakeAnthropic(
        [
            _block(content=[_block(
                type="tool_use", id="t1", name="materials_project__thermoelectric_structure",
                input={"property_y": "ZT", "max_plausible": 3.5},
            )]),
            _block(content=[_block(
                type="tool_use", id="t2", name="submit_answer",
                input={"answer": "SnSe（Pnma, 直方晶）が ZT 0.82。", "citations": []},
            )]),
        ]
    )
    monkeypatch.setitem(demo._state, "anthropic_factory", lambda _key: fake)
    body = TestClient(demo.app).post(
        "/demo/ask", json={"question": "高 ZT の熱電材料はどんな結晶構造？"},
        headers={"X-API-Key": "sk-test"},
    ).json()

    assert {"dataset": "materials_project", "name": "thermoelectric_structure",
            "title": "Rank thermoelectric materials with the crystal structure that explains them"} in body["verified_tools"]
    assert body["unverified_sparql"] is False
    # The cross-dataset join actually ran: the MP structure reached the model.
    fed_back = json.dumps(fake.calls[1]["messages"], ensure_ascii=False)
    assert "Pnma" in fed_back and "SnSe" in fed_back


def test_router_escape_marks_unverified(monkeypatch) -> None:
    # When the LLM falls back to run_sparql (no verified tool fits), the answer is
    # flagged unverified with no verified tool — the amber badge in the UI.
    g = rdflib.ConjunctiveGraph()
    g.parse(data=_CUSTOM_TTL, format="turtle")
    demo._state["client"] = _LocalClient(g)
    monkeypatch.setattr(demo, "_REAL", True)
    select = f"SELECT ?w ?n WHERE {{ ?w a <{_EX}Widget> ; <{_EX}name> ?n }}"
    fake = _FakeAnthropic(
        [
            _block(content=[_block(type="tool_use", id="t1", name="run_sparql",
                                   input={"query": select})]),
            _block(content=[_block(type="tool_use", id="t2", name="submit_answer",
                                   input={"answer": "Widget は 2 件。", "citations": []})]),
        ]
    )
    monkeypatch.setitem(demo._state, "anthropic_factory", lambda _key: fake)
    body = TestClient(demo.app).post(
        "/demo/ask", json={"question": "Widget は何件？"}, headers={"X-API-Key": "sk-test"},
    ).json()
    assert body["unverified_sparql"] is True
    assert body["verified_tools"] == []


def test_content_tool_defs_includes_registry_tools(monkeypatch, tmp_path) -> None:
    # P1: a tool saved on a registry (workbench) dataset — registry/<id>/query_tools.yaml
    # — is offered by the router too, not only the repo example datasets.
    reg = tmp_path / "registry"
    ds = reg / "my-dataset-abc12345"
    ds.mkdir(parents=True)
    (ds / "query_tools.yaml").write_text(
        "tools:\n  - name: t1\n    title: T1\n"
        "    query: 'SELECT ?s WHERE { ?s ?p ?o } LIMIT 1'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CSV2RDF_REGISTRY_ROOT", str(reg))
    defs, registry_map = demo._content_tool_defs()
    names = {d["name"] for d in defs}
    assert "my-dataset-abc12345__t1" in names
    assert registry_map["my-dataset-abc12345__t1"][0] == "my-dataset-abc12345"
    # No dataset is privileged: starrydata's declared tools ride the same path.
    assert "starrydata__property_ranking" in names


# --- multi-provider Ask: OpenAI-compatible (Sakura AI Engine etc.) -----------


def _oai_tool_call(call_id: str, name: str, args: dict) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        id=call_id,
        type="function",
        function=types.SimpleNamespace(name=name, arguments=json.dumps(args)),
    )


def _oai_resp(tool_calls=None, content=None, usage=None) -> types.SimpleNamespace:
    msg = types.SimpleNamespace(content=content, tool_calls=tool_calls)
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=msg)], usage=usage
    )


class _FakeOpenAICompletions:
    def __init__(self, outer: "_FakeOpenAI") -> None:
        self._outer = outer

    def create(self, **kwargs):
        self._outer.calls.append(kwargs)
        resp = self._outer.scripted[self._outer.i]
        self._outer.i += 1
        return resp


class _FakeOpenAI:
    """Scripts chat.completions.create responses (OpenAI function-calling shape)."""

    def __init__(self, scripted: list) -> None:
        self.scripted = scripted
        self.i = 0
        self.calls: list[dict] = []
        self.chat = types.SimpleNamespace(completions=_FakeOpenAICompletions(self))


def test_openai_compatible_ask_routes_executes_and_answers(monkeypatch) -> None:
    # The Sakura/OpenAI path: the model function-calls run_sparql, the result is
    # fed back, then it submit_answers — the same contract as the Anthropic loop.
    select = f"SELECT ?w ?n WHERE {{ ?w a <{_EX}Widget> ; <{_EX}name> ?n }}"
    captured_base: list = []
    fake = _FakeOpenAI(
        [
            _oai_resp(
                tool_calls=[_oai_tool_call("c1", "run_sparql", {"query": select})],
                usage=types.SimpleNamespace(prompt_tokens=100, completion_tokens=20),
            ),
            _oai_resp(
                tool_calls=[
                    _oai_tool_call(
                        "c2",
                        "submit_answer",
                        {
                            "answer": "Widget は 2 件あります。",
                            "citations": [{"iri": f"{_EX}w1", "kind": "Widget", "label": "alpha"}],
                        },
                    )
                ],
                usage=types.SimpleNamespace(prompt_tokens=130, completion_tokens=15),
            ),
        ]
    )
    g = rdflib.ConjunctiveGraph()
    g.parse(data=_CUSTOM_TTL, format="turtle")
    demo._state["client"] = _LocalClient(g)
    monkeypatch.setattr(demo, "_REAL", True)

    def factory(api_key, base_url):
        captured_base.append(base_url)
        return fake

    monkeypatch.setitem(demo._state, "openai_factory", factory)

    body = TestClient(demo.app).post(
        "/demo/ask",
        json={"question": "Widget は何件？"},
        headers={
            "X-API-Key": "sk-sakura",
            "X-LLM-Provider": "openai-compatible",
            "X-LLM-Model": "gpt-oss-120b",
            "X-LLM-Api-Base": "https://api.ai.sakura.ad.jp/v1",
        },
    ).json()

    assert "Widget" in body["answer"]
    assert body["citations"][0]["iri"] == f"{_EX}w1"
    assert select in body["sparql"]
    # The base_url from the header reached the client factory.
    assert captured_base == ["https://api.ai.sakura.ad.jp/v1"]
    # The first call used OpenAI function-calling tool shape + the pinned model.
    first = fake.calls[0]
    assert first["model"] == "gpt-oss-120b"
    assert first["tools"][0]["type"] == "function"
    # The tool result was fed back as a role=tool message on turn 2.
    fed_back = json.dumps(fake.calls[1]["messages"], ensure_ascii=False)
    assert "alpha" in fed_back


def test_ask_records_usage_to_ledger(monkeypatch) -> None:
    # Accumulated token usage is reported to the api ledger (here captured instead
    # of POSTed). The Anthropic path's usage fields map to the ledger event.
    captured: dict = {}

    async def fake_post(provider, model, usage):
        captured.update({"provider": provider, "model": model, "usage": usage})

    monkeypatch.setattr(demo, "_post_usage", fake_post)
    fake = _FakeAnthropic(
        [
            _block(
                content=[
                    _block(
                        type="tool_use",
                        id="t1",
                        name="submit_answer",
                        input={"answer": "ok", "citations": []},
                    )
                ],
                usage=types.SimpleNamespace(
                    input_tokens=200,
                    output_tokens=40,
                    cache_read_input_tokens=10,
                    cache_creation_input_tokens=5,
                ),
            )
        ]
    )
    client = _custom_schema_client(monkeypatch, fake)
    client.post(
        "/demo/ask", json={"question": "Widget は何件？"}, headers={"X-API-Key": "sk-test"}
    )
    assert captured["provider"] == "anthropic"
    assert captured["usage"]["input_tokens"] == 200
    assert captured["usage"]["output_tokens"] == 40
    assert captured["usage"]["cache_read_tokens"] == 10
    assert captured["usage"]["cache_write_tokens"] == 5
