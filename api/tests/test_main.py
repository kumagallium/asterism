"""FastAPI surface tests.

We start the app with ``start_watcher=False`` and an injected mock
OxigraphClient so the test stays inside a single process and doesn't touch
the filesystem outside ``tmp_path``.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from csv2rdf.oxigraph_client import OxigraphClient, OxigraphConfig
from fastapi.testclient import TestClient

from csv2rdf_api.main import Settings, build_app


def _settings(tmp: Path) -> Settings:
    env = {
        "CSV2RDF_DROP_ROOT": str(tmp / "csv"),
        "CSV2RDF_RDF_ROOT": str(tmp / "rdf"),
        "CSV2RDF_ERROR_ROOT": str(tmp / "errors"),
        "CSV2RDF_JOBS_LOG": str(tmp / "jobs.jsonl"),
        "CSV2RDF_OXIGRAPH_URL": "http://test",
        "CSV2RDF_SETTLE_S": "0.0",
    }
    return Settings(env)


def _mock_client(handler) -> OxigraphClient:
    inner = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    )
    return OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner)


@pytest.fixture
def healthy_client() -> OxigraphClient:
    def handler(request: httpx.Request) -> httpx.Response:
        # Used by /health (ASK) and POST /store; both succeed.
        if request.url.path == "/query":
            return httpx.Response(
                200,
                text=json.dumps({"head": {}, "boolean": True}),
                headers={"content-type": "application/sparql-results+json"},
            )
        return httpx.Response(204)

    return _mock_client(handler)


@pytest.fixture
def unreachable_client() -> OxigraphClient:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    return _mock_client(handler)


def test_health_ok(tmp_path: Path, healthy_client: OxigraphClient) -> None:
    app = build_app(
        _settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False
    )
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["oxigraph"] is True


def test_health_degraded_when_oxigraph_unreachable(
    tmp_path: Path, unreachable_client: OxigraphClient
) -> None:
    app = build_app(
        _settings(tmp_path), oxigraph_client=unreachable_client, start_watcher=False
    )
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 503
        body = r.json()
        assert body["status"] == "degraded"
        assert body["oxigraph"] is False


def test_upload_writes_to_drop_dir(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    app = build_app(
        _settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False
    )
    with TestClient(app) as client:
        r = client.post(
            "/upload/papers",
            files={"file": ("hello.csv", b"SID,DOI\n1,10.x\n", "text/csv")},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["kind"] == "papers"
        assert body["queued"] is True

    dest = tmp_path / "csv" / "papers" / "hello.csv"
    assert dest.exists()
    assert dest.read_bytes() == b"SID,DOI\n1,10.x\n"
    # No leftover .tmp
    assert not dest.with_suffix(".csv.tmp").exists()


def test_upload_rejects_bad_kind(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    app = build_app(
        _settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False
    )
    with TestClient(app) as client:
        r = client.post(
            "/upload/junk",
            files={"file": ("x.csv", b"a,b\n", "text/csv")},
        )
        assert r.status_code == 400


def test_upload_rejects_unsafe_filename(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    app = build_app(
        _settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False
    )
    with TestClient(app) as client:
        r = client.post(
            "/upload/papers",
            files={"file": ("../etc/passwd.csv", b"x\n", "text/csv")},
        )
        assert r.status_code == 400


def test_upload_rejects_non_csv_suffix(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    app = build_app(
        _settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False
    )
    with TestClient(app) as client:
        r = client.post(
            "/upload/papers",
            files={"file": ("notes.txt", b"x\n", "text/plain")},
        )
        assert r.status_code == 400


def test_jobs_returns_recent(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    s = _settings(tmp_path)
    s.jobs_log.parent.mkdir(parents=True, exist_ok=True)
    s.jobs_log.write_text(
        json.dumps({"kind": "papers", "status": "ok", "rows_in": 1}) + "\n"
        + json.dumps({"kind": "samples", "status": "ok", "rows_in": 2}) + "\n",
        encoding="utf-8",
    )
    app = build_app(s, oxigraph_client=healthy_client, start_watcher=False)
    with TestClient(app) as client:
        r = client.get("/jobs", params={"limit": 1})
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 1
        assert body["jobs"][0]["kind"] == "samples"


def test_jobs_rejects_invalid_limit(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    app = build_app(
        _settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False
    )
    with TestClient(app) as client:
        r = client.get("/jobs", params={"limit": 0})
        assert r.status_code == 400
