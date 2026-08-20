"""GET /api/units/resolve — does a typed unit reach a real standard unit?"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from asterism.oxigraph_client import OxigraphClient, OxigraphConfig
from fastapi.testclient import TestClient

from asterism_api.main import Settings, build_app

_TEST_TOKEN = "test-token"
_AUTH = {"X-Asterism-Token": _TEST_TOKEN}


def _settings(tmp: Path) -> Settings:
    s = Settings(
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
    s.api_token = _TEST_TOKEN
    return s


@pytest.fixture
def client(tmp_path: Path):
    inner = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(204)), base_url="http://test"
    )
    app = build_app(
        _settings(tmp_path),
        oxigraph_client=OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner),
        start_watcher=False,
    )
    with TestClient(app, headers=_AUTH) as c:
        yield c


def test_resolves_a_standard_spelling(client: TestClient) -> None:
    body = client.get("/api/units/resolve", params={"q": "V/K"}).json()
    assert body["status"] == "resolved"
    assert body["exact"][0]["iri"] == "http://qudt.org/vocab/unit/V-PER-K"


def test_resolves_a_spelling_only_real_files_use(client: TestClient) -> None:
    """The spelling starrydata's own files carry — not a QUDT string at all."""
    body = client.get("/api/units/resolve", params={"q": "W*m^(-1)*K^(-1)"}).json()
    assert body["status"] == "resolved"
    assert body["exact"][0]["curie"] == "unit:W-PER-M-K"
    assert body["exact"][0]["matched_on"] == "alias"


def test_shared_symbol_is_settled_by_si(client: TestClient) -> None:
    body = client.get("/api/units/resolve", params={"q": "K"}).json()
    assert body["status"] == "resolved"
    assert body["si_settled"] is True
    assert body["exact"][0]["label"] == "kelvin"


def test_unknown_unit_says_so(client: TestClient) -> None:
    """A miss is an answer, not an error — this is the whole point of the endpoint."""
    r = client.get("/api/units/resolve", params={"q": "µV/K"})
    assert r.status_code == 200
    assert r.json()["status"] == "unknown"


def test_response_carries_catalog_provenance(client: TestClient) -> None:
    """Which QUDT release answered — the verdict is only citable with it."""
    cat = client.get("/api/units/resolve", params={"q": "K"}).json()["catalog"]
    assert cat["source"].startswith("https://qudt.org/")
    assert cat["version"] and cat["retrieved"] and cat["license"]


def test_missing_query_is_a_client_error(client: TestClient) -> None:
    assert client.get("/api/units/resolve").status_code == 422
