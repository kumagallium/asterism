"""GET /api/classes/schema (契約メモ contract_pr_c.md §2/§3.4。担当 C1-schema).

``asterism.class_schema.class_schema`` を HTTP に載せただけの薄いルートなので、
ここでは配線（クエリパラメータ・200 の形・404）だけを検証する。値そのものの
決定論・優先順位・kind 判定の網羅は ``ingest/tests/test_class_schema.py`` が担う。

§0.1 のとおり、並列段のテストは ``build_app`` の後に自分の
``register_class_schema(app, cfg)`` を呼んでから ``TestClient(app)`` を作る
（``main.py`` はまだこのルートを配線していない）。

架空 2 分野（種苗カタログ・気象観測ログ）を使い、分野固有の名詞は書かない。
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pyoxigraph
import pytest
from asterism.oxigraph_client import OxigraphClient, OxigraphConfig
from asterism.substrate import (
    CANONICAL_GRAPH_BASE,
    CONTROL_GRAPH_IRI,
    ONTOLOGY_GRAPH_BASE,
    STATUS_PREDICATE,
    STATUS_PROMOTED,
    canonical_graph_iri,
)
from fastapi.testclient import TestClient

from asterism_api import class_schema_routes
from asterism_api.class_schema_routes import register_class_schema
from asterism_api.main import build_app
from tests.test_main import _AUTH, _settings

EX_SEED = "https://ex/seed#"
SEED_CLASS = EX_SEED + "Seed"
VARIETY_PRED = EX_SEED + "variety"
MASS_PRED = EX_SEED + "massG"
SEED_DATASET = "seed-catalogue-aaaa"

EX_WEATHER = "https://ex/weather#"
READING_CLASS = EX_WEATHER + "Reading"
STATION_PRED = EX_WEATHER + "station"
WEATHER_DATASET = "weather-log-bbbb"

_SEED_MAPPING_YAML = """
version: 1
prefixes:
  ex: "https://ex/seed#"
  exr: "https://ex/seed/resource/"
  xsd: "http://www.w3.org/2001/XMLSchema#"
maps:
  - name: seed
    source: seeds.csv
    subject:
      template: "exr:seed/{code}"
      classes: [ex:Seed]
    properties:
      - predicate: ex:variety
        column: variety
      - predicate: ex:massG
        column: mass_g
        datatype: xsd:double
        unit: "g"
"""

_SEED_TTL = "\n".join(
    [
        f'<https://ex/seed/resource/seed/1> a <{SEED_CLASS}> ; <{VARIETY_PRED}> "Alpha" .',
        f'<https://ex/seed/resource/seed/2> a <{SEED_CLASS}> ; <{VARIETY_PRED}> "Beta" .',
    ]
)

_WEATHER_ONTOLOGY_TTL = f"""
@prefix ex: <{EX_WEATHER}> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .

ex:Reading a rdfs:Class ; rdfs:label "観測記録" .
ex:station a rdf:Property ; rdfs:domain ex:Reading ; rdfs:label "観測地点" .
"""

_WEATHER_CANONICAL_TTL = f"""
@prefix ex: <{EX_WEATHER}> .

<https://ex/weather/resource/reading-1> a <{READING_CLASS}> ;
    ex:station <https://ex/weather/resource/station-1> .
"""


def _write_registry(root: Path) -> None:
    dest = root / SEED_DATASET
    dest.mkdir(parents=True)
    meta = {
        "id": SEED_DATASET,
        "promoted": True,
        "promoted_at": "2026-01-01T00:00:00Z",
        "version": 1,
    }
    (dest / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    (dest / "mapping.yaml").write_text(_SEED_MAPPING_YAML, encoding="utf-8")
    # weather-log-bbbb: registered in the store's control graph but declares no
    # mapping.yaml — exercises class_schema's ontology-projection fallback (§2 ④).
    (root / WEATHER_DATASET).mkdir(parents=True)


def _pyoxi_store() -> pyoxigraph.Store:
    store = pyoxigraph.Store()
    graphs = {
        canonical_graph_iri(SEED_DATASET): _SEED_TTL,
        ONTOLOGY_GRAPH_BASE + WEATHER_DATASET: _WEATHER_ONTOLOGY_TTL,
        canonical_graph_iri(WEATHER_DATASET): _WEATHER_CANONICAL_TTL,
    }
    for giri, ttl in graphs.items():
        store.load(
            ttl.encode("utf-8"), mime_type="text/turtle", to_graph=pyoxigraph.NamedNode(giri)
        )
        if giri.startswith(CANONICAL_GRAPH_BASE):
            store.add(
                pyoxigraph.Quad(
                    pyoxigraph.NamedNode(giri),
                    pyoxigraph.NamedNode(STATUS_PREDICATE),
                    pyoxigraph.Literal(STATUS_PROMOTED),
                    pyoxigraph.NamedNode(CONTROL_GRAPH_IRI),
                )
            )
    return store


def _store_backed_client(store: pyoxigraph.Store) -> OxigraphClient:
    """A real ``OxigraphClient`` whose HTTP transport runs every query against
    an in-process ``pyoxigraph.Store`` (same SPARQL-JSON shape a live Oxigraph
    endpoint returns, datatype/lang included) — needed because ``class_schema``
    issues several distinct SELECTs depending on which of §2's 4 sources answer."""

    def handler(request: httpx.Request) -> httpx.Response:
        query = (request.content or b"").decode("utf-8")
        result = store.query(query)
        names = [v.value for v in result.variables]
        bindings = []
        for solution in result:
            row: dict[str, dict[str, str]] = {}
            for name in names:
                term = solution[name]
                if term is None:
                    continue
                if isinstance(term, pyoxigraph.NamedNode):
                    row[name] = {"type": "uri", "value": term.value}
                elif isinstance(term, pyoxigraph.Literal):
                    cell = {"type": "literal", "value": term.value}
                    if term.language:
                        cell["xml:lang"] = term.language
                    elif term.datatype:
                        cell["datatype"] = term.datatype.value
                    row[name] = cell
            bindings.append(row)
        body = json.dumps({"head": {}, "results": {"bindings": bindings}})
        return httpx.Response(
            200, text=body, headers={"content-type": "application/sparql-results+json"}
        )

    inner = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
    return OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner)


def _build(tmp_path: Path) -> TestClient:
    settings = _settings(tmp_path)
    settings.registry_root.mkdir(parents=True, exist_ok=True)
    _write_registry(settings.registry_root)
    app = build_app(
        settings, oxigraph_client=_store_backed_client(_pyoxi_store()), start_watcher=False
    )
    register_class_schema(app, settings)
    return TestClient(app, headers=_AUTH)


def test_classes_schema_returns_registry_backed_schema(tmp_path: Path) -> None:
    with _build(tmp_path) as client:
        r = client.get("/api/classes/schema", params={"class_iri": SEED_CLASS})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["class_iri"] == SEED_CLASS
        assert body["dataset_id"] == SEED_DATASET
        props = {p["iri"]: p for p in body["properties"]}
        assert props[MASS_PRED]["kind"] == "quantity"
        assert props[MASS_PRED]["unit"] == "g"
        assert props[VARIETY_PRED]["kind"] == "category"


def test_classes_schema_ontology_fallback(tmp_path: Path) -> None:
    with _build(tmp_path) as client:
        r = client.get("/api/classes/schema", params={"class_iri": READING_CLASS})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["dataset_id"] == WEATHER_DATASET
        assert body["label"] == "観測記録"  # ontology graph の rdfs:label（K4: IRI ではない）
        props = {p["iri"]: p for p in body["properties"]}
        assert props[STATION_PRED]["kind"] == "link"


def test_classes_schema_unknown_class_is_404(tmp_path: Path) -> None:
    with _build(tmp_path) as client:
        r = client.get("/api/classes/schema", params={"class_iri": "https://ex/nowhere#Nothing"})
        assert r.status_code == 404


def test_classes_schema_malformed_iri_is_404(tmp_path: Path) -> None:
    # class_schema() treats an unsafe/malformed IRI as "not found" rather than
    # querying the store with it (K4-adjacent safety, same as prov/graph's 400
    # for a bad IRI — here it surfaces as 404 since the underlying function
    # returns None rather than raising).
    with _build(tmp_path) as client:
        r = client.get("/api/classes/schema", params={"class_iri": "not-an-iri"})
        assert r.status_code == 404


def test_classes_schema_unsafe_sparql_chars_in_class_iri_is_404(tmp_path: Path) -> None:
    # These previously slipped past the weak `<`/`>`/`"`/space-only check
    # (no `{`/`}` in the deny-list) and reached a query built with a raw
    # f-string ``<{class_iri}>``, producing a 500 instead of the intended 404.
    with _build(tmp_path) as client:
        for bad_iri in ('https://ex/x" ?bad ?y ?z . }', "http://x/y}"):
            r = client.get("/api/classes/schema", params={"class_iri": bad_iri})
            assert r.status_code == 404, (bad_iri, r.status_code, r.text)


def test_classes_schema_missing_param_is_422(tmp_path: Path) -> None:
    with _build(tmp_path) as client:
        r = client.get("/api/classes/schema")
        assert r.status_code == 422


def test_classes_schema_store_syntax_error_is_400(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """checker finding: this route built its own 500 for any exception that
    leaked past ``class_schema`` — a store-rejected-query ``SyntaxError``
    must map to 400 the same way ``cards_run`` already did."""

    async def boom(client, registry_root, class_iri):
        raise SyntaxError("bad query")

    monkeypatch.setattr(class_schema_routes, "class_schema", boom)
    with _build(tmp_path) as client:
        r = client.get("/api/classes/schema", params={"class_iri": SEED_CLASS})
        assert r.status_code == 400, r.text
