"""Tests for GET /api/datasets/{id}/trial-queries (kantan-mode ADR K9).

The S7 ためす screen's data source: deterministic read-only aggregates over the
staged draft graph — per-kind entity counts, the busiest numeric field's range,
the entity holding its maximum (its subject IRI is the citation), and an
entity-IRI fallback when no numeric field exists. Labels/units come from the
reviewed Mapping IR (K8), never from an AI. Forgiving like /draft-stats: a
never-ingested dataset or an unreachable store → 200 with ``available: false``;
only an unknown dataset 404s.
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
  rr:predicateObjectMap [ rr:predicate ex:temperature ;
    rr:objectMap [ rml:reference "temp" ] ] .
"""

# The reviewed IR carries the display metadata (label/unit) the screen shows.
_MAPPING_IR = """\
version: 1
prefixes:
  ex: "https://example.org/onto#"
  exr: "https://example.org/resource/"
maps:
  - name: SampleMap
    source: samples.csv
    subject:
      template: "exr:sample/{sid}"
      classes: [ex:Sample]
    properties:
      - predicate: ex:temperature
        column: temp
        label: "測定温度"
        unit: "K"
      - predicate: ex:zt
        column: zt
        label: "ZT"
      - predicate: ex:name
        column: name
        label: "試料名"
"""

_ARTIFACTS = {
    "diagram.md": "```mermaid\nclassDiagram\n  class Sample\n```\n",
    "model.yaml": "",
    "mie.yaml": "",
    "ingester.py": "",
    "mapping.rml.ttl": _RML,
    "mapping.yaml": _MAPPING_IR,
}

_EX = "https://example.org/onto#"
_TOP_SUBJECT = "https://example.org/resource/sample/S-004"


def _save(tmp: Path) -> dict:
    return registry.save_dataset(
        tmp / "registry",
        "Samples",
        _ARTIFACTS,
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
        created_at="2026-07-22T00:00:00+00:00",
        proposal_md="# design v1\n",
    )


def _ingest(tmp: Path, dataset_id: str) -> str:
    return registry.mark_ingested(
        tmp / "registry",
        dataset_id,
        graph_iri=f"https://kumagallium.github.io/asterism/graph/canonical/{dataset_id}/v1",
        triple_count=42,
        ingested_at="2026-07-22T00:00:00+00:00",
        data_seq=1,
    )["graph_iri"]


def _oxigraph(handler) -> OxigraphClient:
    inner = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    )
    return OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner)


def _sparql_json(rows: list[dict]) -> httpx.Response:
    return httpx.Response(
        200,
        text=json.dumps({"results": {"bindings": rows}}),
        headers={"content-type": "application/sparql-results+json"},
    )


def test_trial_queries_full_shape(tmp_path: Path) -> None:
    """Counts + range + top (with citation IRI and context) over the staged graph."""
    meta = _save(tmp_path)
    staged_iri = _ingest(tmp_path, meta["id"])
    queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/query":
            return httpx.Response(204)
        q = request.content.decode("utf-8")
        queries.append(q)
        if "?s a ?class" in q:
            return _sparql_json(
                [
                    {
                        "class": {"type": "uri", "value": f"{_EX}Sample"},
                        "n": {"type": "literal", "value": "24"},
                    }
                ]
            )
        if "GROUP BY ?p" in q:
            # Usage-ordered numeric aggregate: the busiest field has NO spread
            # (a constant) and must be skipped; the next two carry the range
            # (temperature) and top (zt) questions.
            return _sparql_json(
                [
                    {
                        "p": {"type": "uri", "value": f"{_EX}runNo"},
                        "n": {"type": "literal", "value": "999"},
                        "min": {"type": "literal", "value": "5"},
                        "max": {"type": "literal", "value": "5"},
                    },
                    {
                        "p": {"type": "uri", "value": f"{_EX}temperature"},
                        "n": {"type": "literal", "value": "500"},
                        "min": {"type": "literal", "value": "300"},
                        "max": {"type": "literal", "value": "800"},
                    },
                    {
                        "p": {"type": "uri", "value": f"{_EX}zt"},
                        "n": {"type": "literal", "value": "500"},
                        "min": {"type": "literal", "value": "0.08"},
                        "max": {"type": "literal", "value": "1.42"},
                    },
                ]
            )
        if "ORDER BY DESC(?num)" in q:
            return _sparql_json(
                [
                    {
                        "s": {"type": "uri", "value": _TOP_SUBJECT},
                        "v": {"type": "literal", "value": "1.42"},
                    }
                ]
            )
        if f"<{_TOP_SUBJECT}>" in q:
            # Context literals of the top entity; the answer predicate itself
            # (ex:zt) must be dropped from the context list.
            return _sparql_json(
                [
                    {
                        "p": {"type": "uri", "value": f"{_EX}name"},
                        "v": {"type": "literal", "value": "BiTe-04"},
                    },
                    {
                        "p": {"type": "uri", "value": f"{_EX}zt"},
                        "v": {"type": "literal", "value": "1.42"},
                    },
                ]
            )
        return _sparql_json([])

    app = build_app(_settings(tmp_path), oxigraph_client=_oxigraph(handler), start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        queries.clear()  # drop the app-startup migration probes
        r = client.get(f"/api/datasets/{meta['id']}/trial-queries")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["available"] is True
        # Every query ran against the staged version graph recorded at ingest.
        assert queries and all(f"GRAPH <{staged_iri}>" in q for q in queries)

        assert body["classes"] == [{"iri": f"{_EX}Sample", "n": 24}]
        assert "COUNT(DISTINCT ?s)" in body["count_sparql"]
        assert body["entities"] is None  # classes exist → no plain-count fallback
        # Numbers are found by cast attempt, not isNumeric (plain-string numbers).
        assert any("xsd:double(str(?v))" in q for q in queries)

        rng = body["range"]
        assert rng["predicate_iri"] == f"{_EX}temperature"
        assert (rng["min"], rng["max"], rng["n"]) == ("300", "800", 500)
        assert (rng["label"], rng["unit"]) == ("測定温度", "K")  # from the IR (K8)

        top = body["top"]
        assert top["predicate_iri"] == f"{_EX}zt"
        assert top["value"] == "1.42"
        assert top["label"] == "ZT"
        assert "unit" not in top  # the IR authored no unit for zt
        assert top["subject_iri"] == _TOP_SUBJECT  # the citation
        assert top["subject_details"] == [
            {"predicate_iri": f"{_EX}name", "value": "BiTe-04", "label": "試料名"}
        ]

        assert body["samples"] is None  # numeric fields exist → no fallback


def test_trial_queries_falls_back_to_entity_ids(tmp_path: Path) -> None:
    """No numeric field at all → real entity IRIs of the biggest kind (ADR K9)."""
    meta = _save(tmp_path)
    _ingest(tmp_path, meta["id"])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/query":
            return httpx.Response(204)
        q = request.content.decode("utf-8")
        if "?s a ?class" in q:
            return _sparql_json(
                [
                    {
                        "class": {"type": "uri", "value": f"{_EX}Sample"},
                        "n": {"type": "literal", "value": "3"},
                    }
                ]
            )
        if "GROUP BY ?p" in q:
            return _sparql_json([])  # nothing numeric
        if "?s a <" in q:
            return _sparql_json(
                [
                    {"s": {"type": "uri", "value": f"https://example.org/resource/sample/s{i}"}}
                    for i in (1, 2, 3)
                ]
            )
        return _sparql_json([])

    app = build_app(_settings(tmp_path), oxigraph_client=_oxigraph(handler), start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.get(f"/api/datasets/{meta['id']}/trial-queries")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["available"] is True
        assert body["range"] is None and body["top"] is None
        samples = body["samples"]
        assert samples["class_iri"] == f"{_EX}Sample"
        assert samples["iris"] == [
            "https://example.org/resource/sample/s1",
            "https://example.org/resource/sample/s2",
            "https://example.org/resource/sample/s3",
        ]


def test_trial_queries_untyped_string_number_shape(tmp_path: Path) -> None:
    """The real weak-model shape: no rr:class anywhere and numbers stored as
    plain string literals. The cast-based detection still finds the range/top
    questions, and the plain entity count replaces the per-kind one — the
    screen never opens empty-handed on a legal design."""
    meta = _save(tmp_path)
    _ingest(tmp_path, meta["id"])
    queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/query":
            return httpx.Response(204)
        q = request.content.decode("utf-8")
        queries.append(q)
        if "?s a ?class" in q:
            return _sparql_json([])  # no typed classes at all
        if "COUNT(DISTINCT ?s)" in q:  # the plain entity-count fallback
            return _sparql_json([{"n": {"type": "literal", "value": "13"}}])
        if "GROUP BY ?p" in q:
            return _sparql_json(
                [
                    {
                        "p": {"type": "uri", "value": f"{_EX}temperature"},
                        "n": {"type": "literal", "value": "13"},
                        "min": {"type": "literal", "value": "300"},
                        "max": {"type": "literal", "value": "800"},
                    }
                ]
            )
        if "ORDER BY DESC(?num)" in q:
            return _sparql_json(
                [
                    {
                        "s": {"type": "uri", "value": _TOP_SUBJECT},
                        "v": {"type": "literal", "value": "800"},
                    }
                ]
            )
        return _sparql_json([])

    app = build_app(_settings(tmp_path), oxigraph_client=_oxigraph(handler), start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.get(f"/api/datasets/{meta['id']}/trial-queries")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["available"] is True
        assert body["classes"] == []
        assert body["entities"]["n"] == 13
        # A single numeric field carries BOTH the range and the top questions.
        assert body["range"]["predicate_iri"] == f"{_EX}temperature"
        assert body["top"]["predicate_iri"] == f"{_EX}temperature"
        assert body["top"]["subject_iri"] == _TOP_SUBJECT
        # Numeric detection is the cast attempt — plain-string numbers included.
        assert any("xsd:double(str(?v))" in q for q in queries)


def test_trial_queries_before_ingest_and_unknown_dataset(tmp_path: Path) -> None:
    """Design-stage → available:false without touching the store; unknown → 404."""
    meta = _save(tmp_path)
    queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/query":
            queries.append(request.content.decode("utf-8"))
        return httpx.Response(204)

    app = build_app(_settings(tmp_path), oxigraph_client=_oxigraph(handler), start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        queries.clear()  # drop the app-startup migration probes
        r = client.get(f"/api/datasets/{meta['id']}/trial-queries")
        assert r.status_code == 200, r.text
        assert r.json()["available"] is False
        assert queries == []

        assert client.get("/api/datasets/nope/trial-queries").status_code == 404


def test_trial_queries_works_after_promote(tmp_path: Path) -> None:
    """Promote clears ingested/graph_iri and records live_graph (the SAME
    version graph, O(1) pointer flip) — the S9 done screen re-fetches its
    question chips after a reload, so the endpoint must keep answering."""
    meta = _save(tmp_path)
    staged_iri = _ingest(tmp_path, meta["id"])
    registry.mark_promoted(
        tmp_path / "registry",
        meta["id"],
        triples_promoted=42,
        alignment={"predicates": {"reuse": [], "new": []}, "classes": {"reuse": [], "new": []}},
        promoted_at="2026-07-22T01:00:00+00:00",
        canonical_graph=f"https://kumagallium.github.io/asterism/graph/canonical/{meta['id']}",
        live_graph=staged_iri,
    )
    queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/query":
            return httpx.Response(204)
        q = request.content.decode("utf-8")
        queries.append(q)
        if "?s a ?class" in q:
            return _sparql_json(
                [
                    {
                        "class": {"type": "uri", "value": f"{_EX}Sample"},
                        "n": {"type": "literal", "value": "24"},
                    }
                ]
            )
        return _sparql_json([])

    app = build_app(_settings(tmp_path), oxigraph_client=_oxigraph(handler), start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        queries.clear()
        r = client.get(f"/api/datasets/{meta['id']}/trial-queries")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["available"] is True
        assert body["classes"] == [{"iri": f"{_EX}Sample", "n": 24}]
        # Queries target the live version graph promote pointed at.
        trial_queries = [q for q in queries if "?s a ?class" in q]
        assert trial_queries and all(f"GRAPH <{staged_iri}>" in q for q in trial_queries)


def test_trial_queries_degrades_when_store_is_down(tmp_path: Path) -> None:
    meta = _save(tmp_path)
    _ingest(tmp_path, meta["id"])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/query":
            return httpx.Response(500, text="boom")
        return httpx.Response(204)

    app = build_app(_settings(tmp_path), oxigraph_client=_oxigraph(handler), start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        r = client.get(f"/api/datasets/{meta['id']}/trial-queries")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["available"] is False  # the UI shows a plain retry note
        assert body["classes"] == []
