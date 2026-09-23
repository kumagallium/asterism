"""GET /api/prov/graph (object-cards-ui.md §3+§4): the generic PROV-O graph
reader (asterism.prov_graph.prov_graph) exposed over HTTP, unauthenticated.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
from asterism.oxigraph_client import OxigraphClient, OxigraphConfig
from asterism.substrate import canonical_graph_iri
from fastapi.testclient import TestClient

from asterism_api.main import build_app
from tests.test_main import _AUTH, _settings, healthy_client  # noqa: F401 (fixture)

# ruff: noqa: F811  — `healthy_client` is a pytest fixture reused by name.

_STATION = "http://example.org/station-a"
_GRAPH = canonical_graph_iri("weather-log") + "/v1"


def _uri(value: str) -> dict[str, str]:
    return {"type": "uri", "value": value}


def _lit(value: str) -> dict[str, str]:
    return {"type": "literal", "value": value}


def _bindings(rows: list[dict[str, dict[str, str]]]) -> str:
    return json.dumps({"head": {}, "results": {"bindings": rows}})


def _found_client() -> OxigraphClient:
    """A store where ``_STATION`` exists in one canonical version graph, with a
    single ``rdfs:label`` and no PROV edges — the smallest non-empty answer."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = (request.content or b"").decode("utf-8")
        headers = {"content-type": "application/sparql-results+json"}
        if "BIND(COALESCE(?lg, ?c) AS ?g)" in body:
            # canonical_graphs(): one promoted version graph.
            return httpx.Response(200, text=_bindings([{"g": _uri(_GRAPH)}]), headers=headers)
        if "__prov_p" in body:
            # _exists_query(): the station is present in that graph.
            return httpx.Response(200, text=_bindings([{"g": _uri(_GRAPH)}]), headers=headers)
        if "VALUES ?pred" in body:
            # _edge_step_query(): no PROV edges touch it.
            return httpx.Response(200, text=_bindings([]), headers=headers)
        if "?prop ?val ?lang" in body:
            # _info_query(): a type and an rdfs:label.
            return httpx.Response(
                200,
                text=_bindings(
                    [
                        {
                            "n": _uri(_STATION),
                            "prop": _lit("type"),
                            "val": _uri("http://example.org/WeatherStation"),
                        },
                        {
                            "n": _uri(_STATION),
                            "prop": _lit("rdfs_label"),
                            "val": _lit("Station A"),
                        },
                    ]
                ),
                headers=headers,
            )
        return httpx.Response(200, text=_bindings([]), headers=headers)

    inner = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
    return OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner)


def _build(tmp_path: Path, client: OxigraphClient):
    return build_app(_settings(tmp_path), oxigraph_client=client, start_watcher=False)


def test_prov_graph_found(tmp_path: Path) -> None:
    app = _build(tmp_path, _found_client())
    # The route reads ``app.state.client``, which the app only sets during ASGI
    # lifespan startup — the ``with`` block is what triggers that (bare
    # ``TestClient(app)`` never runs it, see the other tools tests' bare form).
    with TestClient(app, headers=_AUTH) as client:
        r = client.get("/api/prov/graph", params={"iri": _STATION})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["iri"] == _STATION
        assert body["found"] is True
        node = body["graph"]["nodes"][0]
        assert node["id"] == _STATION
        assert node["label"] == "Station A"  # rdfs:label wins, never the raw IRI (K4)
        assert node["props"]["dataset_id"] == "weather-log"
        assert node["props"]["snapshot"] == "v1"
        assert body["materials"] == [
            {"dataset_id": "weather-log", "snapshot": "v1", "graph": _GRAPH}
        ]


def test_prov_graph_not_found_is_200(tmp_path: Path, healthy_client: OxigraphClient) -> None:
    # healthy_client answers every SELECT with no bindings — a well-formed IRI
    # absent from the citable scope is a legitimate 200 with found: false, not
    # an error.
    app = _build(tmp_path, healthy_client)
    with TestClient(app, headers=_AUTH) as client:
        r = client.get("/api/prov/graph", params={"iri": "http://example.org/nothing-here"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["found"] is False
        assert body["graph"] == {"nodes": [], "edges": []}
        assert body["materials"] == []


def test_prov_graph_bad_iri_is_400(tmp_path: Path, healthy_client: OxigraphClient) -> None:
    app = _build(tmp_path, healthy_client)
    with TestClient(app, headers=_AUTH) as client:
        r = client.get("/api/prov/graph", params={"iri": "not-an-iri"})
        assert r.status_code == 400
