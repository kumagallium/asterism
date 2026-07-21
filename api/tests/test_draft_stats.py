"""Tests for GET /api/datasets/{id}/draft-stats (kantan-mode ADR K12).

The correspondence card's data source: per-class DISTINCT-subject counts of the
dataset's staged draft graph (the same ``meta.graph_iri`` resolution alignment /
promote use) + header-excluded data-row counts of the persisted tabular source.
Forgiving by contract: no draft or an unreachable Oxigraph → 200 with
``classes: []`` (the UI hides the card); only an unknown dataset 404s.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
from asterism.oxigraph_client import OxigraphClient, OxigraphConfig
from fastapi.testclient import TestClient

from asterism_api import registry
from asterism_api.main import Settings, build_app

_TEST_TOKEN = "test-token"
_AUTH = {"X-Asterism-Token": _TEST_TOKEN}


def _settings(tmp: Path) -> Settings:
    env = {
        "CSV2RDF_DROP_ROOT": str(tmp / "csv"),
        "CSV2RDF_RDF_ROOT": str(tmp / "rdf"),
        "CSV2RDF_ERROR_ROOT": str(tmp / "errors"),
        "CSV2RDF_JOBS_LOG": str(tmp / "jobs.jsonl"),
        "CSV2RDF_REGISTRY_ROOT": str(tmp / "registry"),
        "CSV2RDF_OXIGRAPH_URL": "http://test",
        "CSV2RDF_SETTLE_S": "0.0",
    }
    s = Settings(env)
    s.api_token = _TEST_TOKEN
    return s


_RML = """\
@prefix rr:  <http://www.w3.org/ns/r2rml#> .
@prefix rml: <http://semweb.mmlab.be/ns/rml#> .
@prefix ql:  <http://semweb.mmlab.be/ns/ql#> .
@prefix ex:  <https://example.org/onto#> .

<#SampleMap> a rr:TriplesMap ;
  rml:logicalSource [ rml:source "samples.csv" ; rml:referenceFormulation ql:CSV ] ;
  rr:subjectMap [ rr:template "https://example.org/resource/sample/{sid}" ;
    rr:class ex:Sample ] ;
  rr:predicateObjectMap [ rr:predicate ex:label ;
    rr:objectMap [ rml:reference "name" ] ] .
"""

_ARTIFACTS = {
    "diagram.md": "```mermaid\nclassDiagram\n  class Sample\n```\n",
    "model.yaml": "",
    "mie.yaml": "",
    "ingester.py": "",
    "mapping.rml.ttl": _RML,
}


def _save(tmp: Path) -> dict:
    meta = registry.save_dataset(
        tmp / "registry",
        "Samples",
        _ARTIFACTS,
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
        created_at="2026-07-21T00:00:00+00:00",
        proposal_md="# design v1\n",
    )
    # Persist a design-time source set: two countable tabular files (one with a
    # quoted embedded newline — a raw line count would overcount) + one JSON
    # that the row counter must skip.
    sdir = tmp / "registry" / meta["id"] / "source"
    sdir.mkdir(parents=True)
    (sdir / "samples.csv").write_text(
        'sid,name\ns1,"pellet\nA"\ns2,pellet B\n\n', encoding="utf-8"
    )
    (sdir / "extra.tsv").write_text("sid\tv\ns1\t1\n", encoding="utf-8")
    (sdir / "notes.json").write_text("{}", encoding="utf-8")
    return meta


def _oxigraph(handler) -> OxigraphClient:
    inner = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    )
    return OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner)


def test_draft_stats_counts_classes_and_source_rows(tmp_path: Path) -> None:
    meta = _save(tmp_path)
    staged_iri = registry.mark_ingested(
        tmp_path / "registry",
        meta["id"],
        graph_iri=f"https://kumagallium.github.io/asterism/graph/canonical/{meta['id']}/v3",
        triple_count=42,
        ingested_at="2026-07-21T00:00:00+00:00",
        data_seq=3,
    )["graph_iri"]

    queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/query":
            queries.append(request.content.decode("utf-8"))
            rows = [
                {
                    "class": {"type": "uri", "value": "https://example.org/onto#Sample"},
                    "n": {"type": "literal", "value": "24"},
                },
                {
                    # A blank-node "class" must be dropped, not crash the parse.
                    "class": {"type": "bnode", "value": "b0"},
                    "n": {"type": "literal", "value": "1"},
                },
            ]
            return httpx.Response(
                200,
                text=json.dumps({"results": {"bindings": rows}}),
                headers={"content-type": "application/sparql-results+json"},
            )
        return httpx.Response(204)

    app = build_app(_settings(tmp_path), oxigraph_client=_oxigraph(handler), start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.get(f"/api/datasets/{meta['id']}/draft-stats")
        assert r.status_code == 200, r.text
        body = r.json()
        # The count ran against the STAGED version graph recorded at ingest.
        assert any(f"GRAPH <{staged_iri}>" in q for q in queries)
        assert body["classes"] == [
            {"iri": "https://example.org/onto#Sample", "curie": "ex:Sample", "n": 24}
        ]
        # Data rows exclude the header; the quoted embedded newline is ONE row;
        # the trailing blank line and the .json source are not counted.
        assert body["source_rows"] == {"samples.csv": 2, "extra.tsv": 1}


def test_draft_stats_without_draft_returns_empty_classes(tmp_path: Path) -> None:
    """A design-stage dataset (never ingested) must not query the store at all."""
    meta = _save(tmp_path)
    queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/query":
            queries.append(request.content.decode("utf-8"))
        return httpx.Response(204)

    app = build_app(_settings(tmp_path), oxigraph_client=_oxigraph(handler), start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.get(f"/api/datasets/{meta['id']}/draft-stats")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["classes"] == []
        assert body["source_rows"] == {"samples.csv": 2, "extra.tsv": 1}
        assert not any("COUNT(DISTINCT ?s)" in q for q in queries)

        assert client.get("/api/datasets/nope/draft-stats").status_code == 404


def test_draft_stats_degrades_when_store_is_down(tmp_path: Path) -> None:
    meta = _save(tmp_path)
    registry.mark_ingested(
        tmp_path / "registry",
        meta["id"],
        graph_iri=f"https://kumagallium.github.io/asterism/graph/canonical/{meta['id']}/v1",
        triple_count=1,
        ingested_at="2026-07-21T00:00:00+00:00",
        data_seq=1,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/query":
            return httpx.Response(500, text="boom")
        return httpx.Response(204)

    app = build_app(_settings(tmp_path), oxigraph_client=_oxigraph(handler), start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.get(f"/api/datasets/{meta['id']}/draft-stats")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["classes"] == []  # 200 + empty, so the UI just hides the card
        assert body["source_rows"] == {"samples.csv": 2, "extra.tsv": 1}
