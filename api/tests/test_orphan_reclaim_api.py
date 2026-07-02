"""Orphaned version-graph reclamation (part5 storage-leak fix).

Closes the "a re-created dataset multiplies version graphs" leak (audit residual #2):
a re-ingest before promotion, or a crash / cancelled job, leaves a
``…/canonical/{id}/v{n}`` graph no pointer references, and delete only reclaimed the
current live/staged pointers — so orphaned versions accumulated unbounded.

The store here is a real in-memory ``rdflib.Dataset`` injected as the app's client, so
the enumeration + control writes + lifespan reconciliation run for real through the
endpoints (no triplestore, no network) — the same harness style as
``test_crosswalk_api.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import rdflib
from asterism import substrate
from fastapi.testclient import TestClient

from asterism_api import registry
from asterism_api.main import Settings, build_app

_TEST_TOKEN = "test-token"
_AUTH = {"X-Asterism-Token": _TEST_TOKEN}
_EX = rdflib.Namespace("https://ex#")


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


class _DatasetClient:
    """OxigraphClient stand-in over a real rdflib Dataset (SELECT/UPDATE + Graph-Store
    POST). Duck-typed into ``build_app(oxigraph_client=...)``."""

    def __init__(self, ds: rdflib.Dataset) -> None:
        self.ds = ds

    async def sparql_select(self, query: str) -> dict:
        raw = self.ds.query(query).serialize(format="json")
        return json.loads(raw.decode() if isinstance(raw, bytes) else raw)

    async def sparql_update(self, update: str) -> None:
        self.ds.update(update)

    async def post_turtle_bytes(self, payload: bytes, graph_iri: str | None = None) -> int:
        g = self.ds.graph(rdflib.URIRef(graph_iri)) if graph_iri else self.ds.default_graph
        g.parse(data=payload.decode("utf-8"), format="turtle")
        return len(payload)

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None


def _add_version(ds: rdflib.Dataset, iri: str) -> None:
    ds.graph(rdflib.URIRef(iri)).add((_EX.s, _EX.p, rdflib.Literal(iri)))


def _promote_pointer(ds: rdflib.Dataset, dataset_id: str, live_iri: str) -> None:
    """Flag ``dataset_id`` promoted with ``liveGraph`` -> ``live_iri`` (referenced)."""
    key = substrate.canonical_graph_iri(dataset_id)
    ds.update(
        f"INSERT DATA {{ GRAPH <{substrate.CONTROL_GRAPH_IRI}> {{ "
        f'<{key}> <{substrate.STATUS_PREDICATE}> "{substrate.STATUS_PROMOTED}" ; '
        f"<{substrate.LIVE_GRAPH_PREDICATE}> <{live_iri}> }} }}"
    )


def _has_pending_drop(ds: rdflib.Dataset, graph_iri: str) -> bool:
    control = ds.graph(rdflib.URIRef(substrate.CONTROL_GRAPH_IRI))
    return any(
        control.triples(
            (rdflib.URIRef(graph_iri), rdflib.URIRef(substrate.PENDING_DROP_PREDICATE), None)
        )
    )


# ---- ⑤ reserve-fresh version number (registry) ------------------------------


def test_reserve_data_seq_persists_a_fresh_number_per_attempt(tmp_path: Path) -> None:
    # ⑤: reserve_data_seq advances AND persists data_seq, so every ingest attempt —
    # success or a failure that never reached mark_ingested — gets a FRESH version
    # number. A retry therefore never reuses (and merges into) a previous attempt's
    # partial version graph, unlike next_data_seq which is a pure non-persisting peek.
    root = tmp_path / "registry"
    ds_id = "demo-00000001"
    (root / ds_id).mkdir(parents=True)
    (root / ds_id / "meta.json").write_text(json.dumps({"id": ds_id}), encoding="utf-8")

    assert registry.next_data_seq(root, ds_id) == 1  # peek does not persist
    assert registry.next_data_seq(root, ds_id) == 1  # ...so it stays 1

    assert registry.reserve_data_seq(root, ds_id) == 1  # reserve persists the bump
    assert registry.next_data_seq(root, ds_id) == 2  # peek now reflects it
    # a failed attempt (no mark_ingested) still burned v1 -> the retry gets a fresh v2
    assert registry.reserve_data_seq(root, ds_id) == 2
    assert registry.reserve_data_seq(root, ds_id) == 3  # monotonic, never reused
    # unsafe / absent id -> 1 WITHOUT persisting (the caller validates the id separately)
    assert registry.reserve_data_seq(root, "Bad Id!") == 1
    assert registry.reserve_data_seq(tmp_path / "nope", ds_id) == 1


# ---- ② startup reconciliation (lifespan) ------------------------------------


def test_startup_reconciles_orphan_version_graphs(tmp_path: Path) -> None:
    # ②: at startup an orphaned version graph (no live/staged pointer) — left by a
    # crash before the pointer was written, or predating the fix — is enqueued for a
    # background drop, while a promoted dataset's live version is kept.
    ds = rdflib.Dataset()
    orphan = substrate.versioned_graph_iri("ghost", 1)  # no pointer -> reclaim
    live = substrate.versioned_graph_iri("real", 2)  # liveGraph -> keep
    _add_version(ds, orphan)
    _add_version(ds, live)
    _promote_pointer(ds, "real", live)
    client = _DatasetClient(ds)

    app = build_app(_settings(tmp_path), oxigraph_client=client, start_watcher=False)
    with TestClient(app, headers=_AUTH):
        pass  # lifespan startup runs reconcile_orphan_versions

    assert _has_pending_drop(ds, orphan)  # orphan enqueued for reclaim
    assert not _has_pending_drop(ds, live)  # the live version is never touched


# ---- ④ delete reclaims every version graph ----------------------------------


def test_delete_reclaims_every_version_graph(tmp_path: Path) -> None:
    # ④: force-deleting a dataset enqueues EVERY version graph it owns — the current
    # live version AND superseded re-ingest orphans no pointer names — so a
    # re-ingested-then-deleted dataset (the reported "datasets multiply" symptom)
    # leaves nothing behind, without waiting for a restart's reconciliation pass.
    ds = rdflib.Dataset()
    ds_id = "demo-00000002"
    v1 = substrate.versioned_graph_iri(ds_id, 1)  # superseded orphan (no pointer)
    v2 = substrate.versioned_graph_iri(ds_id, 2)  # current live
    _add_version(ds, v1)
    _add_version(ds, v2)
    _promote_pointer(ds, ds_id, v2)
    root = tmp_path / "registry" / ds_id
    root.mkdir(parents=True)
    (root / "meta.json").write_text(
        json.dumps(
            {
                "id": ds_id,
                "name": ds_id,
                "created_at": "2026-07-02T00:00:00+00:00",
                "promoted": True,
                "ingested": True,
                "status": "active",
                "graph_iri": v2,
                "data_seq": 2,
            }
        ),
        encoding="utf-8",
    )
    client = _DatasetClient(ds)

    app = build_app(_settings(tmp_path), oxigraph_client=client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as c:
        # promoted -> hard delete requires ?force=true
        assert c.delete(f"/api/datasets/{ds_id}").status_code == 409
        r = c.delete(f"/api/datasets/{ds_id}?force=true")
        assert r.status_code == 200
        assert r.json()["was_promoted"] is True

    # BOTH the live version AND the superseded orphan are enqueued for reclaim
    assert _has_pending_drop(ds, v1)
    assert _has_pending_drop(ds, v2)
    # the registry entry is gone
    assert registry.load_dataset(tmp_path / "registry", ds_id) is None
