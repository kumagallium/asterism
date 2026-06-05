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
from asterism import substrate
from asterism.oxigraph_client import OxigraphClient, OxigraphConfig
from fastapi.testclient import TestClient

from asterism_api import registry
from asterism_api.main import Settings, build_app


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


# ---- design-time source persistence (Task E) --------------------------------


def test_attach_source_persists_csv_and_flags_meta(tmp_path: Path) -> None:
    dataset_id = _save_dataset_with_rml(tmp_path)
    oxi = _RecordingOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app) as client:
        r = client.post(
            f"/api/datasets/{dataset_id}/source",
            files={"files": ("papers.csv", b"SID\n1\n", "text/csv")},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["source_files"] == ["papers.csv"]
        assert body["dataset"]["has_source"] is True
        assert body["dataset"]["source_files"] == ["papers.csv"]
    # Persisted under <id>/source/ with the exact bytes.
    src = tmp_path / "registry" / dataset_id / "source" / "papers.csv"
    assert src.is_file()
    assert src.read_bytes() == b"SID\n1\n"


def test_attach_source_unknown_dataset_404(tmp_path: Path) -> None:
    oxi = _RecordingOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app) as client:
        r = client.post(
            "/api/datasets/nope-00000000/source",
            files={"files": ("papers.csv", b"SID\n1\n", "text/csv")},
        )
        assert r.status_code == 404


def test_ingest_uses_persisted_source_when_no_upload(tmp_path: Path, monkeypatch) -> None:
    """Task E: a design-stage dataset with persisted source ingests with no re-attach."""
    dataset_id = _save_dataset_with_rml(tmp_path)
    monkeypatch.setattr(substrate, "materialize_to_graph", lambda *a, **k: _fake_graph())
    oxi = _RecordingOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app) as client:
        assert (
            client.post(
                f"/api/datasets/{dataset_id}/source",
                files={"files": ("papers.csv", b"SID\n1\n", "text/csv")},
            ).status_code
            == 200
        )
        # Ingest with NO files uploaded — the persisted source is used.
        r = client.post(f"/api/datasets/{dataset_id}/ingest")
        assert r.status_code == 200, r.text
        assert r.json()["triple_count"] == 1
        assert r.json()["graph_kind"] == "draft"
    assert oxi.store_calls == [substrate.draft_graph_iri(dataset_id)]


def test_ingest_upload_persists_source_for_reuse(tmp_path: Path, monkeypatch) -> None:
    """An ingest WITH an upload also persists that CSV as the dataset's source."""
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
    meta = json.loads((tmp_path / "registry" / dataset_id / "meta.json").read_text())
    assert meta["has_source"] is True
    assert meta["source_files"] == ["papers.csv"]
    assert (tmp_path / "registry" / dataset_id / "source" / "papers.csv").is_file()


def test_ingest_without_upload_or_source_400(tmp_path: Path) -> None:
    """No upload and no persisted source -> 400 (nothing to ingest from)."""
    dataset_id = _save_dataset_with_rml(tmp_path)  # RML present, no source attached
    oxi = _RecordingOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app) as client:
        r = client.post(f"/api/datasets/{dataset_id}/ingest")
        assert r.status_code == 400
        assert "CSV" in r.json()["detail"]
    assert oxi.store_calls == []


# ---- promotion: draft -> canonical (#15 S4) ---------------------------------


class _PromoteOxi:
    """Oxigraph fake for promote/alignment: canned SELECTs + records /update."""

    def __init__(self) -> None:
        self.updates: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/update":
                body = request.content.decode()
                self.updates.append(body)
                return httpx.Response(204)
            # /query: alignment SELECTs and the promote COUNT
            q = request.content.decode()
            if "COUNT" in q and "GRAPH" not in q:
                # The #20 FROM-merge startup migration's default-graph count.
                # Default graph is empty in these tests -> migration is a no-op.
                rows = [{"c": {"value": "0"}}]
            elif "COUNT" in q:
                rows = [{"c": {"value": "1640"}}]
            elif "?__cg" in q:  # canonical-scope side (default + canonical/*) — empty
                rows = []
            elif "GRAPH <" in q:  # draft side names a graph literally
                rows = [{"x": {"type": "uri", "value": "https://ex#draftProp"}}]
            else:
                rows = []
            return httpx.Response(
                200,
                text=json.dumps({"results": {"bindings": rows}}),
                headers={"content-type": "application/sparql-results+json"},
            )

        inner = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://test"
        )
        self.client = OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner)


def _ingested_dataset(tmp: Path) -> str:
    dataset_id = _save_dataset_with_rml(tmp)
    registry.mark_ingested(
        tmp / "registry",
        dataset_id,
        graph_iri=f"https://kumagallium.github.io/asterism/starrydata/graph/draft/{dataset_id}",
        triple_count=1640,
        ingested_at="2026-06-03T00:10:00+00:00",
    )
    return dataset_id


_TBOX_RML = (
    "@prefix sd: <https://kumagallium.github.io/asterism/starrydata/ontology#> .\n"
    "@prefix sdr: <https://kumagallium.github.io/asterism/starrydata/resource/> .\n"
    "@prefix schema: <https://schema.org/> .\n"
)
_TBOX_MODEL = (
    "- Paper <https://ex/paper/1>:\n"
    "    - a: sd:Paper\n"
    "    - schema:name?:\n"
    "        - title: \"t\"\n"
    "- Sample <https://ex/sample/1>:\n"
    "    - a: sd:Sample\n"
    "    - sd:fromPaper:\n"
    "        - sp: Paper\n"
)


class _ProjectOxi:
    """Promote fake that also records /store POSTs (for ontology projection)."""

    def __init__(self) -> None:
        self.updates: list[str] = []
        self.stores: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/update":
                self.updates.append(request.content.decode())
                return httpx.Response(204)
            if request.url.path == "/store":
                self.stores.append(request.url.params.get("graph"))
                return httpx.Response(204)
            q = request.content.decode()
            if "COUNT" in q and "GRAPH" not in q:
                rows = [{"c": {"value": "0"}}]  # migration default-count
            elif "COUNT" in q:
                rows = [{"c": {"value": "1640"}}]
            else:
                rows = []
            return httpx.Response(
                200,
                text=json.dumps({"results": {"bindings": rows}}),
                headers={"content-type": "application/sparql-results+json"},
            )

        inner = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
        self.client = OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner)


def test_promote_projects_tbox_into_ontology_graph(tmp_path: Path) -> None:
    # A dataset with a real model.yaml -> promote projects RDFS into ontology/{id}.
    dataset_id = registry.save_dataset(
        tmp_path / "registry",
        "tbox",
        {
            "diagram.md": "classDiagram\n  class Paper",
            "model.yaml": _TBOX_MODEL,
            "mie.yaml": "schema_info:\n  title: x",
            "ingester.py": "def go(): ...",
            "mapping.rml.ttl": _TBOX_RML,
        },
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
        created_at="2026-06-05T00:00:00+00:00",
    )["id"]
    registry.mark_ingested(
        tmp_path / "registry",
        dataset_id,
        graph_iri=f"https://kumagallium.github.io/asterism/starrydata/graph/draft/{dataset_id}",
        triple_count=1640,
        ingested_at="2026-06-05T00:10:00+00:00",
    )
    oxi = _ProjectOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app) as client:
        body = client.post(f"/api/datasets/{dataset_id}/promote").json()
    ontology_iri = f"https://kumagallium.github.io/asterism/graph/ontology/{dataset_id}"
    assert body["ontology_graph"] == ontology_iri
    assert body["ontology_triples"] > 0  # TBox projected
    # The ontology graph was replaced (DROP) then loaded (POST /store?graph=...).
    assert any("DROP" in u and ontology_iri in u for u in oxi.updates)
    assert ontology_iri in oxi.stores


def test_alignment_preview_classifies_draft(tmp_path: Path) -> None:
    dataset_id = _ingested_dataset(tmp_path)
    oxi = _PromoteOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app) as client:
        r = client.get(f"/api/datasets/{dataset_id}/alignment")
        assert r.status_code == 200, r.text
        al = r.json()["alignment"]
        # draft uses a predicate not in (empty) canonical -> New
        assert al["predicates"]["new"] == ["https://ex#draftProp"]
        assert al["predicates"]["reuse"] == []


def test_promote_moves_to_canonical_and_marks_meta(tmp_path: Path) -> None:
    dataset_id = _ingested_dataset(tmp_path)
    oxi = _PromoteOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app) as client:
        r = client.post(f"/api/datasets/{dataset_id}/promote")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["promoted"] is True
        # #20 P3: promote now targets the dataset's per-dataset canonical graph.
        expected_canon = f"https://kumagallium.github.io/asterism/graph/canonical/{dataset_id}"
        assert body["canonical_graph"] == expected_canon
        assert body["triples_promoted"] == 1640
        assert body["dataset"]["promoted"] is True
        assert body["dataset"]["ingested"] is False  # draft consumed
    # A MOVE ... TO GRAPH <canonical/{id}> was issued (not TO DEFAULT).
    assert any(
        "MOVE GRAPH" in u and f"TO GRAPH <{expected_canon}>" in u for u in oxi.updates
    )
    # Meta on disk reflects promotion.
    meta = json.loads((tmp_path / "registry" / dataset_id / "meta.json").read_text())
    assert meta["promoted"] is True
    assert meta["triples_promoted"] == 1640
    assert meta["canonical_graph"] == expected_canon
    # #20 P3: first promotion is version 1 with one entry in the version log.
    assert meta["version"] == 1
    assert len(meta["versions"]) == 1
    assert meta["versions"][0]["version"] == 1
    assert meta["versions"][0]["triples_promoted"] == 1640


def test_mark_promoted_bumps_version_on_repromote(tmp_path: Path) -> None:
    """#20 P3: re-promoting the same dataset bumps a monotonic version + logs it."""
    dataset_id = _save_dataset_with_rml(tmp_path)
    root = tmp_path / "registry"
    align = {"predicates": {"reuse": [], "new": []}, "classes": {"reuse": [], "new": []}}

    m1 = registry.mark_promoted(
        root, dataset_id, triples_promoted=100, alignment=align, promoted_at="2026-01-01T00:00:00"
    )
    assert m1 is not None and m1["version"] == 1 and len(m1["versions"]) == 1

    m2 = registry.mark_promoted(
        root, dataset_id, triples_promoted=120, alignment=align, promoted_at="2026-01-02T00:00:00"
    )
    assert m2 is not None and m2["version"] == 2
    # Append-only log keeps both promotions, in order.
    assert [v["version"] for v in m2["versions"]] == [1, 2]
    assert m2["versions"][1]["triples_promoted"] == 120


def test_promote_requires_ingested_draft(tmp_path: Path) -> None:
    dataset_id = _save_dataset_with_rml(tmp_path)  # has RML but never ingested
    oxi = _PromoteOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app) as client:
        r = client.post(f"/api/datasets/{dataset_id}/promote")
        assert r.status_code == 400
    assert oxi.updates == []  # nothing moved


def test_ingest_morph_kgc_error_returns_422(tmp_path: Path, monkeypatch) -> None:
    # A Morph-KGC failure on malformed/unsupported RML must surface as 422
    # (user-data error) with the cause — not an opaque 500.
    dataset_id = _save_dataset_with_rml(tmp_path)

    def _boom(*_a, **_k):
        raise KeyError("object")

    monkeypatch.setattr(substrate, "materialize_to_graph", _boom)
    oxi = _RecordingOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app) as client:
        r = client.post(
            f"/api/datasets/{dataset_id}/ingest",
            files={"files": ("papers.csv", b"SID\n1\n", "text/csv")},
        )
        assert r.status_code == 422
        assert "KeyError" in r.json()["detail"]
    assert oxi.store_calls == []  # nothing loaded


def test_retract_then_reinstate_roundtrip(tmp_path: Path) -> None:
    """#20 P3 step3: retract tombstones the canonical graph; reinstate clears it."""
    dataset_id = _ingested_dataset(tmp_path)
    oxi = _PromoteOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    canon = f"https://kumagallium.github.io/asterism/graph/canonical/{dataset_id}"
    with TestClient(app) as client:
        assert client.post(f"/api/datasets/{dataset_id}/promote").status_code == 200
        r = client.post(f"/api/datasets/{dataset_id}/retract")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "retracted"
        assert r.json()["dataset"]["status"] == "retracted"
        r2 = client.post(f"/api/datasets/{dataset_id}/reinstate")
        assert r2.status_code == 200
        assert r2.json()["status"] == "active"
    # retract wrote a control-graph tombstone for the canonical graph...
    assert any("INSERT DATA" in u and '"retracted"' in u and canon in u for u in oxi.updates)
    # ...and meta ends active again after reinstate.
    meta = json.loads((tmp_path / "registry" / dataset_id / "meta.json").read_text())
    assert meta["status"] == "active"


def test_retract_requires_promoted(tmp_path: Path) -> None:
    dataset_id = _save_dataset_with_rml(tmp_path)  # designed but never promoted
    oxi = _PromoteOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app) as client:
        r = client.post(f"/api/datasets/{dataset_id}/retract")
        assert r.status_code == 400
    assert oxi.updates == []  # nothing tombstoned


def test_delete_draft_only_dataset_no_force(tmp_path: Path) -> None:
    """A never-promoted (design/draft) dataset deletes freely; registry dir removed."""
    dataset_id = _ingested_dataset(tmp_path)  # ingested draft, not promoted
    oxi = _PromoteOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app) as client:
        r = client.delete(f"/api/datasets/{dataset_id}")
        assert r.status_code == 200, r.text
        assert r.json()["deleted"] is True
        assert r.json()["was_promoted"] is False
    # draft graph dropped, registry dir gone
    assert any("DROP SILENT GRAPH" in u and "draft/" in u for u in oxi.updates)
    assert not (tmp_path / "registry" / dataset_id).exists()


def test_delete_promoted_requires_force(tmp_path: Path) -> None:
    dataset_id = _ingested_dataset(tmp_path)
    oxi = _PromoteOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app) as client:
        assert client.post(f"/api/datasets/{dataset_id}/promote").status_code == 200
        r = client.delete(f"/api/datasets/{dataset_id}")  # no force
        assert r.status_code == 409
    # nothing dropped, registry dir still present
    assert not any("DROP SILENT GRAPH" in u for u in oxi.updates)
    assert (tmp_path / "registry" / dataset_id).exists()


def test_delete_promoted_with_force_drops_canonical_and_tombstones(tmp_path: Path) -> None:
    dataset_id = _ingested_dataset(tmp_path)
    oxi = _PromoteOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    canon = f"https://kumagallium.github.io/asterism/graph/canonical/{dataset_id}"
    with TestClient(app) as client:
        assert client.post(f"/api/datasets/{dataset_id}/promote").status_code == 200
        r = client.delete(f"/api/datasets/{dataset_id}?force=true")
        assert r.status_code == 200, r.text
        assert r.json()["deleted"] is True and r.json()["was_promoted"] is True
    assert any("DROP SILENT GRAPH" in u and canon in u for u in oxi.updates)
    assert any("INSERT DATA" in u and '"deleted"' in u and canon in u for u in oxi.updates)
    assert not (tmp_path / "registry" / dataset_id).exists()


def test_delete_unknown_dataset_404(tmp_path: Path) -> None:
    oxi = _PromoteOxi()
    app = build_app(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False)
    with TestClient(app) as client:
        assert client.delete("/api/datasets/nope-00000000").status_code == 404
