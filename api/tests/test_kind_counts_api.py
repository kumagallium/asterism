"""GET /api/kinds/counts: 種類ごとの件数をデータセット単位で返す読み手
(asterism.kind_counts.kind_counts) を HTTP に出す。MockTransport の流儀は
test_prov_graph_api と同じ。
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

_GRAPH = canonical_graph_iri("weather-log") + "/v1"
_KIND = "http://example.org/WeatherStation"


def _uri(value: str) -> dict[str, str]:
    return {"type": "uri", "value": value}


def _bindings(rows: list[dict[str, dict[str, str]]]) -> str:
    return json.dumps({"head": {}, "results": {"bindings": rows}})


def _client() -> OxigraphClient:
    def handler(request: httpx.Request) -> httpx.Response:
        body = (request.content or b"").decode("utf-8")
        headers = {"content-type": "application/sparql-results+json"}
        if "BIND(COALESCE(?lg, ?c) AS ?g)" in body:
            return httpx.Response(200, text=_bindings([{"g": _uri(_GRAPH)}]), headers=headers)
        if "COUNT(DISTINCT ?s)" in body:
            row = {
                "g": _uri(_GRAPH),
                "c": _uri(_KIND),
                "n": {"type": "literal", "value": "7"},
            }
            return httpx.Response(200, text=_bindings([row]), headers=headers)
        return httpx.Response(200, text=_bindings([]), headers=headers)

    inner = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
    return OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner)


def test_kind_counts_route_returns_shape(tmp_path: Path) -> None:
    app = build_app(_settings(tmp_path), oxigraph_client=_client(), start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.get("/api/kinds/counts")
        assert r.status_code == 200, r.text
        assert r.json() == {
            "graphs": [
                {
                    "graph": _GRAPH,
                    "dataset_id": "weather-log",
                    "hub": False,
                    "kinds": [{"class_iri": _KIND, "count": 7}],
                }
            ],
            "truncated": False,
        }


def test_kind_counts_route_empty_when_nothing_published(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    app = build_app(_settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.get("/api/kinds/counts")
        assert r.status_code == 200, r.text
        assert r.json() == {"graphs": [], "truncated": False}
