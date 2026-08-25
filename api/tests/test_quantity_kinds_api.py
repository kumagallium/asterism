"""GET /api/quantitykinds/resolve — what quantity is this column measuring?"""
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


def test_a_named_column_resolves(client: TestClient) -> None:
    body = client.get("/api/quantitykinds/resolve", params={"q": "thermalConductivity"}).json()
    top = body["candidates"][0]
    assert top["iri"] == "http://qudt.org/vocab/quantitykind/ThermalConductivity"
    assert top["match"] == "exact"


def test_an_abbreviation_resolves_through_its_unit(client: TestClient) -> None:
    """The column is called `S`; only the unit says it is a Seebeck coefficient."""
    body = client.get(
        "/api/quantitykinds/resolve", params={"q": "S", "unit": "V-PER-K"}
    ).json()
    assert "SeebeckCoefficient" in [c["name"] for c in body["candidates"]]


def test_a_column_that_is_not_a_quantity_gets_no_candidates(client: TestClient) -> None:
    body = client.get("/api/quantitykinds/resolve", params={"q": "sampleName"}).json()
    assert body["candidates"] == []


def test_response_carries_catalog_provenance(client: TestClient) -> None:
    """Which QUDT release answered — the candidate is only citable with it."""
    cat = client.get("/api/quantitykinds/resolve", params={"q": "temperature"}).json()["catalog"]
    assert cat["source"].startswith("https://qudt.org/")
    assert cat["version"] and cat["retrieved"] and cat["license"]


def test_neither_query_nor_unit_is_a_client_error(client: TestClient) -> None:
    assert client.get("/api/quantitykinds/resolve").status_code == 400
    assert client.get("/api/quantitykinds/resolve", params={"q": " "}).status_code == 400
