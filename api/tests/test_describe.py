"""Tests for GET /describe — IRI dereference over the published scope
(ADR instance-iri-base.md phase 2).

The Oxigraph side is a scripted httpx.MockTransport: the handler answers the
control-graph enumeration (canonical_graphs), the ontology-graph enumeration,
and the description SELECT/CONSTRUCT queries, so the tests pin the REAL query
composition (FROM NAMED over the published graphs only) without a store.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
from asterism.oxigraph_client import OxigraphClient, OxigraphConfig
from fastapi.testclient import TestClient

from asterism_api.main import Settings, build_app

_CANONICAL = "https://kumagallium.github.io/asterism/graph/canonical/dataset-x/v1"
_ONTOLOGY = "https://kumagallium.github.io/asterism/graph/ontology/dataset-x"
_ENTITY = "https://data.lab.jp/asterism/datasets/xrd/resource/point/S1-10.00"
_UNKNOWN = "https://data.lab.jp/asterism/datasets/xrd/resource/point/nope"


def _settings(tmp: Path) -> Settings:
    return Settings(
        {
            "CSV2RDF_DROP_ROOT": str(tmp / "csv"),
            "CSV2RDF_RDF_ROOT": str(tmp / "rdf"),
            "CSV2RDF_ERROR_ROOT": str(tmp / "errors"),
            "CSV2RDF_JOBS_LOG": str(tmp / "jobs.jsonl"),
            "CSV2RDF_REGISTRY_ROOT": str(tmp / "registry"),
            "CSV2RDF_OXIGRAPH_URL": "http://test",
            "CSV2RDF_SETTLE_S": "0.0",
        }
    )


def _select_json(rows: list[dict[str, dict[str, str]]]) -> httpx.Response:
    return httpx.Response(
        200,
        json={"head": {"vars": []}, "results": {"bindings": rows}},
        headers={"Content-Type": "application/sparql-results+json"},
    )


def _uri(v: str) -> dict[str, str]:
    return {"type": "uri", "value": v}


def _lit(v: str) -> dict[str, str]:
    return {"type": "literal", "value": v}


def _mock_client(*, promoted: bool = True) -> tuple[OxigraphClient, list[str]]:
    """A scripted store: one promoted dataset (canonical + ontology graph) and
    one entity with a label, a type and one inbound reference. Records every
    query so tests can assert the composed scope."""
    queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        q = request.content.decode()
        queries.append(q)
        accept = request.headers.get("Accept", "")
        if "promoted" in q:  # canonical_graphs (control-graph enumeration)
            rows = [{"g": _uri(_CANONICAL)}] if promoted else []
            return _select_json(rows)
        if "GRAPH ?g {}" in q:  # ontology_graphs (empty-group enumeration)
            rows = [{"g": _uri(_ONTOLOGY)}] if promoted else []
            return _select_json(rows)
        if q.lstrip().startswith("CONSTRUCT"):
            assert "text/turtle" in accept
            if f"<{_ENTITY}> ?p ?o" in q:
                return httpx.Response(
                    200,
                    text=f"<{_ENTITY}> <https://schema.org/name> \"S1 point\" .\n",
                    headers={"Content-Type": "text/turtle"},
                )
            return httpx.Response(200, text="", headers={"Content-Type": "text/turtle"})
        # description SELECTs
        if f"<{_ENTITY}> ?p ?o" in q:  # outbound
            return _select_json(
                [
                    {
                        "p": _uri("http://www.w3.org/1999/02/22-rdf-syntax-ns#type"),
                        "o": _uri("https://data.lab.jp/asterism/datasets/xrd/ontology#DiffractionPoint"),
                        "g": _uri(_CANONICAL),
                    },
                    {
                        "p": _uri("http://www.w3.org/2000/01/rdf-schema#label"),
                        "o": _lit("S1 @ 10.00°"),
                        "g": _uri(_CANONICAL),
                    },
                ]
            )
        if f"?s ?p <{_ENTITY}>" in q:  # inbound
            return _select_json(
                [
                    {
                        "s": _uri("https://data.lab.jp/asterism/datasets/xrd/resource/scan/S1"),
                        "p": _uri("https://data.lab.jp/asterism/datasets/xrd/ontology#hasPoint"),
                        "g": _uri(_CANONICAL),
                    }
                ]
            )
        return _select_json([])  # unknown IRI: empty either way

    inner = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
    return OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner), queries


def _app_client(tmp: Path, oxi: OxigraphClient) -> TestClient:
    app = build_app(_settings(tmp), oxigraph_client=oxi, start_watcher=False)
    return TestClient(app)


def test_describe_html_renders_published_description(tmp_path: Path) -> None:
    oxi, queries = _mock_client()
    with _app_client(tmp_path, oxi) as client:
        r = client.get("/describe", params={"iri": _ENTITY})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    body = r.text
    assert "S1 @ 10.00°" in body  # label became the title
    assert "DiffractionPoint" in body  # type chip
    assert "/describe?iri=" in body  # object IRIs dereference further
    assert "hasPoint" in body  # inbound reference listed
    # The description queries were scoped to the published graphs only.
    scoped = [q for q in queries if q.lstrip().startswith("SELECT ?p ?o ?g")]
    assert scoped and all(f"FROM NAMED <{_CANONICAL}>" in q for q in scoped)


def test_describe_turtle_via_accept_and_format(tmp_path: Path) -> None:
    oxi, _ = _mock_client()
    with _app_client(tmp_path, oxi) as client:
        r = client.get(
            "/describe", params={"iri": _ENTITY}, headers={"Accept": "text/turtle"}
        )
        r2 = client.get("/describe", params={"iri": _ENTITY, "format": "ttl"})
    for res in (r, r2):
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/turtle")
        assert "S1 point" in res.text


def test_describe_unknown_iri_is_404_html(tmp_path: Path) -> None:
    oxi, _ = _mock_client()
    with _app_client(tmp_path, oxi) as client:
        r = client.get("/describe", params={"iri": _UNKNOWN})
    assert r.status_code == 404
    assert "Not in this instance's published data" in r.text


def test_describe_rejects_non_http_iri(tmp_path: Path) -> None:
    oxi, _ = _mock_client()
    with _app_client(tmp_path, oxi) as client:
        assert client.get("/describe", params={"iri": "urn:uuid:x"}).status_code == 400
        assert (
            client.get("/describe", params={"iri": "https://a b/c"}).status_code == 400
        )
        assert (
            client.get(
                "/describe", params={"iri": "https://x/> } UNION { ?s ?p ?o "}
            ).status_code
            == 400
        )


def test_describe_no_published_data_is_404(tmp_path: Path) -> None:
    oxi, _ = _mock_client(promoted=False)
    with _app_client(tmp_path, oxi) as client:
        r = client.get("/describe", params={"iri": _ENTITY})
    assert r.status_code == 404


def test_describe_html_escapes_hostile_literals(tmp_path: Path) -> None:
    """A literal containing markup must render inert (the page is served from
    the api origin — an XSS here would run inside the product)."""
    queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        q = request.content.decode()
        queries.append(q)
        if "promoted" in q:
            return _select_json([{"g": _uri(_CANONICAL)}])
        if "GRAPH ?g {}" in q:
            return _select_json([])
        if "?p ?o" in q and _ENTITY in q:
            return _select_json(
                [
                    {
                        "p": _uri("http://www.w3.org/2000/01/rdf-schema#label"),
                        "o": _lit("<script>alert(1)</script>"),
                        "g": _uri(_CANONICAL),
                    }
                ]
            )
        return _select_json([])

    inner = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
    oxi = OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner)
    with _app_client(tmp_path, oxi) as client:
        r = client.get("/describe", params={"iri": _ENTITY})
    assert r.status_code == 200
    assert "<script>" not in r.text
    assert "&lt;script&gt;" in r.text


def test_instance_endpoint_still_reports_base(tmp_path: Path) -> None:
    """Companion sanity: /api/instance (previous PR) keeps serving the base the
    dereference story starts from."""
    oxi, _ = _mock_client()
    with _app_client(tmp_path, oxi) as client:
        body = client.get("/api/instance").json()
    assert json.loads(json.dumps(body)) == {
        "iri_base": "https://asterism.invalid",
        "iri_base_configured": False,
    }
