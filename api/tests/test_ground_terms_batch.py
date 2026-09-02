"""Tests for POST /api/ground/terms（共通の言葉の地図の一括接地）.

The map draws candidate edges only for near-exact matches (score >= 90 by
default) — the batch endpoint must apply that bar server-side and drop terms
with no surviving candidate, so the UI can draw exactly what comes back.
Deterministic + closed catalog (shared-vocab-graph.md §3).
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from asterism_api.main import Settings, build_app


def _client(tmp: Path) -> TestClient:
    env = {
        "CSV2RDF_DROP_ROOT": str(tmp / "csv"),
        "CSV2RDF_RDF_ROOT": str(tmp / "rdf"),
        "CSV2RDF_ERROR_ROOT": str(tmp / "errors"),
        "CSV2RDF_JOBS_LOG": str(tmp / "jobs.jsonl"),
        "CSV2RDF_REGISTRY_ROOT": str(tmp / "registry"),
        "CSV2RDF_OXIGRAPH_URL": "http://test",
        "CSV2RDF_SETTLE_S": "0.0",
    }
    return TestClient(build_app(Settings(env)))


def test_batch_grounds_exact_terms_and_drops_weak_ones(tmp_path: Path) -> None:
    client = _client(tmp_path)
    res = client.post(
        "/api/ground/terms",
        json={
            "terms": [
                # "name" is an exact schema.org property in the curated catalog.
                {"name": "name", "kind": "property"},
                # Gibberish must not appear in the reply at all.
                {"name": "zzqqxxnothing"},
            ]
        },
    )
    assert res.status_code == 200
    terms = res.json()["terms"]
    assert "zzqqxxnothing" not in terms
    assert [c["name"] for c in terms.get("name", [])], "exact term should ground"
    for c in terms["name"]:
        assert c["score"] >= 90
        assert c["iri"].startswith("http")


def test_batch_dedupes_and_respects_limit(tmp_path: Path) -> None:
    client = _client(tmp_path)
    res = client.post(
        "/api/ground/terms",
        json={
            "terms": [{"name": "name"}, {"name": "name"}],
            "limit_per_term": 1,
            "min_score": 90,
        },
    )
    assert res.status_code == 200
    terms = res.json()["terms"]
    assert len(terms.get("name", [])) == 1


def test_batch_rejects_bad_kind(tmp_path: Path) -> None:
    client = _client(tmp_path)
    res = client.post(
        "/api/ground/terms",
        json={"terms": [{"name": "name", "kind": "nonsense"}]},
    )
    assert res.status_code == 400
