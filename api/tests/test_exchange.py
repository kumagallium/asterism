"""Snapshot exchange (ADR ``local-first-distribution.md`` §5) — export/import.

The full round-trip is exercised over two real rdflib-backed stores: instance
A (unconfigured ``.invalid`` base) exports a promoted dataset; instance B
(configured base) imports it — the minted IRIs are deterministically rebased,
the dataset lands as ingested (unpublished), and B's existing promote gate
makes it citable.
"""

from __future__ import annotations

import asyncio
import io
import json
import tarfile
from pathlib import Path

import rdflib
from asterism import substrate
from fastapi.testclient import TestClient

from asterism_api.main import Settings, build_app

_TEST_TOKEN = "test-token"
_AUTH = {"X-Asterism-Token": _TEST_TOKEN}


def _settings(tmp: Path, *, iri_base: str | None = None) -> Settings:
    env = {
        "CSV2RDF_DROP_ROOT": str(tmp / "csv"),
        "CSV2RDF_RDF_ROOT": str(tmp / "rdf"),
        "CSV2RDF_ERROR_ROOT": str(tmp / "errors"),
        "CSV2RDF_JOBS_LOG": str(tmp / "jobs.jsonl"),
        "CSV2RDF_REGISTRY_ROOT": str(tmp / "registry"),
        "CSV2RDF_OXIGRAPH_URL": "http://test",
        "CSV2RDF_SETTLE_S": "0.0",
    }
    if iri_base is not None:
        env["ASTERISM_IRI_BASE"] = iri_base
    s = Settings(env)
    s.api_token = _TEST_TOKEN
    return s


class _DatasetClient:
    """OxigraphClient stand-in over a real rdflib Dataset (query/update/GSP
    POST + the dump/count surface exchange uses). Duck-typed into
    ``build_app(oxigraph_client=...)``."""

    def __init__(self, ds: rdflib.Dataset) -> None:
        self.ds = ds

    async def sparql_select(self, query: str) -> dict:
        raw = self.ds.query(query).serialize(format="json")
        return json.loads(raw.decode() if isinstance(raw, bytes) else raw)

    async def sparql_update(self, update: str) -> None:
        self.ds.update(update)

    async def sparql_construct(self, query: str) -> str:
        result = self.ds.query(query)
        raw = result.serialize(format="turtle")
        if raw is None:
            return ""
        return raw.decode() if isinstance(raw, bytes) else raw

    async def post_turtle_bytes(self, payload: bytes, graph_iri: str | None = None) -> int:
        g = self.ds.graph(rdflib.URIRef(graph_iri)) if graph_iri else self.ds.default_graph
        g.parse(data=payload.decode("utf-8"), format="turtle")
        return len(payload)

    async def graph_triple_count(self, graph_iri: str) -> int:
        return len(self.ds.graph(rdflib.URIRef(graph_iri)))

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None


_INVALID_BASE = "https://asterism.invalid"
_LOCAL_BASE = "https://lab.example.jp"


def _seed_promoted(tmp: Path, ds: rdflib.Dataset, dataset_id: str, *, base: str) -> None:
    """A minimal PROMOTED dataset: registry dir + live v1 graph + control flags."""
    root = tmp / "registry"
    ddir = root / dataset_id
    (ddir / "source").mkdir(parents=True)
    turtle = (
        f"@prefix r: <{base}/datasets/zem/resource/> .\n"
        f"@prefix o: <{base}/datasets/zem/ontology#> .\n"
        "r:m1 a o:Measurement ; o:temperatureC 25.0 .\n"
        "r:m2 a o:Measurement ; o:temperatureC 300.0 .\n"
    )
    key = substrate.canonical_graph_iri(dataset_id)
    v1 = substrate.versioned_graph_iri(dataset_id, 1)
    ds.graph(rdflib.URIRef(v1)).parse(data=turtle, format="turtle")
    control = ds.graph(rdflib.URIRef(substrate.CONTROL_GRAPH_IRI))
    control.add(
        (
            rdflib.URIRef(key),
            rdflib.URIRef(substrate.STATUS_PREDICATE),
            rdflib.Literal("promoted"),
        )
    )
    control.add(
        (
            rdflib.URIRef(key),
            rdflib.URIRef(substrate.LIVE_GRAPH_PREDICATE),
            rdflib.URIRef(v1),
        )
    )
    meta = {
        "id": dataset_id,
        "name": "ZEM 熱電測定",
        "created_at": "2026-08-01T00:00:00+00:00",
        "classes": ["Measurement"],
        "class_count": 1,
        "ingested": False,
        "promoted": True,
        "status": "active",
        "data_seq": 1,
        "canonical_graph": key,
        "live_graph": v1,
        "version": 1,
    }
    (ddir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    (ddir / "model.yaml").write_text("classes: {}\n", encoding="utf-8")
    (ddir / "mapping.yaml").write_text(
        f"prefixes:\n  o: {base}/datasets/zem/ontology#\n", encoding="utf-8"
    )
    (ddir / "query_tools.yaml").write_text("tools: []\n", encoding="utf-8")


def _export_snapshot(tmp_path: Path, *, base_env: str | None = _INVALID_BASE) -> bytes:
    """Build instance A and export its promoted dataset."""
    a_dir = tmp_path / "a"
    a_dir.mkdir(exist_ok=True)
    ds = rdflib.Dataset()
    _seed_promoted(a_dir, ds, "zem-11112222", base=_INVALID_BASE)
    kwargs = {} if base_env is None else {"iri_base": base_env}
    app = build_app(
        _settings(a_dir, **kwargs),
        oxigraph_client=_DatasetClient(ds),
        start_watcher=False,
    )
    with TestClient(app, headers=_AUTH) as client:
        r = client.get("/api/datasets/zem-11112222/snapshot")
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "application/gzip"
        assert "asterism-snapshot-zem-11112222" in r.headers["content-disposition"]
        return r.content


def test_export_contains_manifest_graph_and_registry(tmp_path: Path) -> None:
    payload = _export_snapshot(tmp_path)
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tar:
        names = tar.getnames()
        manifest = json.loads(tar.extractfile("manifest.json").read())
        ttl = tar.extractfile("graphs/canonical.ttl").read().decode()
    assert manifest["format"] == "asterism-snapshot"
    assert manifest["dataset_id"] == "zem-11112222"
    assert manifest["origin_iri_base"] == _INVALID_BASE
    assert manifest["canonical_triples"] == 4
    assert "asterism.invalid/datasets/zem/resource/m1" in ttl
    assert "registry/meta.json" in names
    assert "registry/query_tools.yaml" in names


def test_export_refuses_unpromoted(tmp_path: Path) -> None:
    a_dir = tmp_path / "a"
    a_dir.mkdir()
    ds = rdflib.Dataset()
    _seed_promoted(a_dir, ds, "zem-11112222", base=_INVALID_BASE)
    meta_path = a_dir / "registry" / "zem-11112222" / "meta.json"
    meta = json.loads(meta_path.read_text())
    meta["promoted"] = False
    meta_path.write_text(json.dumps(meta))
    app = build_app(
        _settings(a_dir, iri_base=_INVALID_BASE),
        oxigraph_client=_DatasetClient(ds),
        start_watcher=False,
    )
    with TestClient(app, headers=_AUTH) as client:
        assert client.get("/api/datasets/zem-11112222/snapshot").status_code == 409


def test_roundtrip_import_rebases_and_promote_makes_citable(tmp_path: Path) -> None:
    payload = _export_snapshot(tmp_path)
    b_dir = tmp_path / "b"
    b_dir.mkdir()
    ds = rdflib.Dataset()
    app = build_app(
        _settings(b_dir, iri_base=_LOCAL_BASE),
        oxigraph_client=_DatasetClient(ds),
        start_watcher=False,
    )
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/datasets/import",
            files={"file": ("snap.tar.gz", payload, "application/gzip")},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["dataset_id"] == "zem-11112222"
        assert body["rebased"] is True
        assert body["triples"] == 4
        assert body["status"] == "ingested"

        # Landed unpublished, with sanitized meta + import provenance.
        detail = client.get("/api/datasets/zem-11112222").json()
        meta = detail["meta"]
        assert meta["ingested"] is True
        assert meta["promoted"] is False
        assert meta["imported"]["origin_iri_base"] == _INVALID_BASE
        assert meta["imported"]["rebased"] is True
        # Registry artifacts were rebased too.
        assert _LOCAL_BASE in detail["artifacts"]["mapping.yaml"]

        # Staged graph holds the REBASED IRIs (receiver becomes the issuer).
        staged = body["staged_graph"]
        g = ds.graph(rdflib.URIRef(staged))
        subjects = {str(s) for s in g.subjects()}
        assert f"{_LOCAL_BASE}/datasets/zem/resource/m1" in subjects
        assert not any("asterism.invalid" in s for s in subjects)

        # Not citable yet…
        assert asyncio.run(substrate.canonical_graphs(_DatasetClient(ds))) == []

        # …until the EXISTING promote gate publishes it.
        r = client.post("/api/datasets/zem-11112222/promote")
        assert r.status_code == 200, r.text
        assert asyncio.run(substrate.canonical_graphs(_DatasetClient(ds))) == [staged]
        meta = client.get("/api/datasets/zem-11112222").json()["meta"]
        assert meta["promoted"] is True


def test_import_conflict_on_existing_dataset(tmp_path: Path) -> None:
    payload = _export_snapshot(tmp_path)
    b_dir = tmp_path / "b"
    b_dir.mkdir()
    (b_dir / "registry" / "zem-11112222").mkdir(parents=True)
    app = build_app(
        _settings(b_dir, iri_base=_LOCAL_BASE),
        oxigraph_client=_DatasetClient(rdflib.Dataset()),
        start_watcher=False,
    )
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/datasets/import",
            files={"file": ("snap.tar.gz", payload, "application/gzip")},
        )
        assert r.status_code == 409


def test_import_keeps_foreign_real_base(tmp_path: Path) -> None:
    """A published (non-.invalid) origin base is identity — never rewritten."""
    a_dir = tmp_path / "a"
    a_dir.mkdir()
    ds = rdflib.Dataset()
    _seed_promoted(a_dir, ds, "zem-11112222", base="https://other-lab.example.org")
    app = build_app(
        _settings(a_dir, iri_base="https://other-lab.example.org"),
        oxigraph_client=_DatasetClient(ds),
        start_watcher=False,
    )
    with TestClient(app, headers=_AUTH) as client:
        payload = client.get("/api/datasets/zem-11112222/snapshot").content

    b_dir = tmp_path / "b"
    b_dir.mkdir()
    ds_b = rdflib.Dataset()
    app_b = build_app(
        _settings(b_dir, iri_base=_LOCAL_BASE),
        oxigraph_client=_DatasetClient(ds_b),
        start_watcher=False,
    )
    with TestClient(app_b, headers=_AUTH) as client:
        r = client.post(
            "/api/datasets/import",
            files={"file": ("snap.tar.gz", payload, "application/gzip")},
        )
        assert r.status_code == 200, r.text
        assert r.json()["rebased"] is False
        g = ds_b.graph(rdflib.URIRef(r.json()["staged_graph"]))
        assert any("other-lab.example.org" in str(s) for s in g.subjects())


def test_import_rejects_traversal_and_garbage(tmp_path: Path) -> None:
    b_dir = tmp_path / "b"
    b_dir.mkdir()
    app = build_app(
        _settings(b_dir, iri_base=_LOCAL_BASE),
        oxigraph_client=_DatasetClient(rdflib.Dataset()),
        start_watcher=False,
    )
    evil = io.BytesIO()
    with tarfile.open(fileobj=evil, mode="w:gz") as tar:
        info = tarfile.TarInfo("../evil.txt")
        payload = b"boom"
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/datasets/import",
            files={"file": ("snap.tar.gz", evil.getvalue(), "application/gzip")},
        )
        assert r.status_code == 400
        r = client.post(
            "/api/datasets/import",
            files={"file": ("snap.tar.gz", b"not a tarball", "application/gzip")},
        )
        assert r.status_code == 400
        assert (b_dir / "registry").exists() is False or not any(
            (b_dir / "registry").iterdir()
        )
