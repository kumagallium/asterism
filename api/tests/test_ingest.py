"""Tests for the human-gated substrate ingest (Phase 5 #15).

``POST /api/datasets/{id}/ingest`` runs the dataset's persisted RML through
Morph-KGC and loads the result into a draft named graph. We monkeypatch the
Morph-KGC step (``substrate.materialize_to_graph``) so the tests need neither the
optional ``morph-kgc`` extra nor real CSVs, and use the MockTransport Oxigraph
client so the ``/store`` POST is observable.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import rdflib
from csv2rdf import substrate
from csv2rdf.oxigraph_client import OxigraphClient, OxigraphConfig
from fastapi.testclient import TestClient

from csv2rdf_api import registry
from csv2rdf_api.main import Settings, build_app


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


class _RecordingOxi:
    """OxigraphClient backed by a transport that records /store graph params."""

    def __init__(self) -> None:
        self.store_calls: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/store":
                self.store_calls.append(request.url.params.get("graph"))
                return httpx.Response(204)
            return httpx.Response(
                200,
                text=json.dumps({"head": {}, "boolean": True}),
                headers={"content-type": "application/sparql-results+json"},
            )

        inner = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://test"
        )
        self.client = OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner)


_RML = (
    "@prefix rr:  <http://www.w3.org/ns/r2rml#> .\n"
    "@prefix rml: <http://semweb.mmlab.be/ns/rml#> .\n"
    "@prefix ql:  <http://semweb.mmlab.be/ns/ql#> .\n"
    '<#M> a rr:TriplesMap ;\n'
    '  rml:logicalSource [ rml:source "papers.csv" ; rml:referenceFormulation ql:CSV ] ;\n'
    '  rr:subjectMap [ rr:template "https://ex/paper/{SID}" ] .\n'
)


def _save_dataset_with_rml(tmp: Path, rml: str = _RML) -> str:
    """Persist a dataset carrying an RML mapping; return its id."""
    return registry.save_dataset(
        tmp / "registry",
        "demo",
        {
            "diagram.md": "classDiagram\n  class Paper",
            "model.yaml": "- Paper:",
            "mie.yaml": "schema_info:\n  title: x",
            "ingester.py": "def go(): ...",
            "mapping.rml.ttl": rml,
        },
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
        created_at="2026-06-03T00:00:00+00:00",
    )["id"]


def _fake_graph() -> rdflib.Graph:
    g = rdflib.Graph()
    g.add(
        (
            rdflib.URIRef("https://ex/paper/1"),
            rdflib.URIRef("https://schema.org/name"),
            rdflib.Literal("A paper"),
        )
    )
    return g


def test_save_dataset_persists_rml_and_flags_has_rml(tmp_path: Path) -> None:
    dataset_id = _save_dataset_with_rml(tmp_path)
    loaded = registry.load_dataset(tmp_path / "registry", dataset_id)
    assert loaded is not None
    assert "rr:TriplesMap" in loaded["artifacts"]["mapping.rml.ttl"]
    assert loaded["meta"]["has_rml"] is True
    assert loaded["meta"]["ingested"] is False


def test_ingest_happy_path_loads_draft_graph(tmp_path: Path, monkeypatch) -> None:
    dataset_id = _save_dataset_with_rml(tmp_path)
    monkeypatch.setattr(substrate, "materialize_to_graph", lambda *a, **k: _fake_graph())
    oxi = _RecordingOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app) as client:
        r = client.post(
            f"/api/datasets/{dataset_id}/ingest",
            files={"files": ("papers.csv", b"SID\n1\n", "text/csv")},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["graph_kind"] == "draft"
        assert body["graph_iri"].endswith(f"/graph/draft/{dataset_id}")
        assert body["triple_count"] == 1
        assert body["dataset"]["ingested"] is True
    # The triples were POSTed to the draft named graph (not the default graph).
    assert oxi.store_calls == [body["graph_iri"]]
    # Meta on disk reflects the ingest.
    meta = json.loads((tmp_path / "registry" / dataset_id / "meta.json").read_text())
    assert meta["ingested"] is True
    assert meta["triple_count"] == 1


def test_ingest_unknown_dataset_404(tmp_path: Path) -> None:
    oxi = _RecordingOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app) as client:
        r = client.post(
            "/api/datasets/does-not-exist/ingest",
            files={"files": ("papers.csv", b"SID\n1\n", "text/csv")},
        )
        assert r.status_code == 404


def test_ingest_dataset_without_rml_400(tmp_path: Path) -> None:
    dataset_id = _save_dataset_with_rml(tmp_path, rml="   ")  # blank RML
    oxi = _RecordingOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app) as client:
        r = client.post(
            f"/api/datasets/{dataset_id}/ingest",
            files={"files": ("papers.csv", b"SID\n1\n", "text/csv")},
        )
        assert r.status_code == 400
        assert "no declarative RML" in r.json()["detail"]
    assert oxi.store_calls == []  # nothing loaded


def test_ingest_without_morph_kgc_returns_501(tmp_path: Path, monkeypatch) -> None:
    dataset_id = _save_dataset_with_rml(tmp_path)

    def _raise(*_a, **_k):
        raise RuntimeError("morph-kgc is required for substrate ingestion; install ...")

    monkeypatch.setattr(substrate, "materialize_to_graph", _raise)
    oxi = _RecordingOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app) as client:
        r = client.post(
            f"/api/datasets/{dataset_id}/ingest",
            files={"files": ("papers.csv", b"SID\n1\n", "text/csv")},
        )
        assert r.status_code == 501
        assert "morph-kgc" in r.json()["detail"]
    assert oxi.store_calls == []
