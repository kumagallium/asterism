"""関係の目録のストア（R0d）— 起動で種がまかれる・失敗しても起動する・``source=store`` の一致。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from asterism import relation_catalog as rc
from asterism import relation_store, substrate
from fastapi.testclient import TestClient

from asterism_api.main import build_app
from tests.test_main import _settings, healthy_client, unreachable_client  # noqa: F401

# ruff: noqa: F811  — fixtures reused by name.

pyoxigraph = pytest.importorskip("pyoxigraph")


class _StoreClient:
    """実物の pyoxigraph.Store 上の SELECT / UPDATE（起動時の健康確認の ASK も通す）。"""

    def __init__(self) -> None:
        self.store = pyoxigraph.Store()
        self.updates = 0

    async def aclose(self) -> None:
        return None

    async def sparql_update(self, update: str) -> None:
        self.updates += 1
        self.store.update(update)

    async def sparql_select(self, query: str) -> dict:
        result = self.store.query(query, use_default_graph_as_union=True)
        if isinstance(result, bool):
            return {"boolean": result}
        names = [v.value for v in result.variables]
        bindings = []
        for solution in result:
            row: dict[str, Any] = {}
            for name in names:
                term = solution[name]
                if term is None:
                    continue
                kind = "uri" if isinstance(term, pyoxigraph.NamedNode) else "literal"
                row[name] = {"type": kind, "value": term.value}
            bindings.append(row)
        return {"results": {"bindings": bindings}}


def test_startup_seeds_the_catalog_and_store_matches_code(tmp_path: Path) -> None:
    store = _StoreClient()
    app = build_app(_settings(tmp_path), oxigraph_client=store, start_watcher=False)  # type: ignore[arg-type]
    with TestClient(app) as client:
        from_store = client.get("/api/relations", params={"source": "store"})
        from_code = client.get("/api/relations")
    assert from_store.status_code == 200
    assert from_store.json() == from_code.json() == rc.catalog_to_json()
    graphs = {q.graph_name.value for q in store.store}
    assert substrate.RELATION_CATALOG_GRAPH in graphs


def test_second_startup_writes_nothing(tmp_path: Path) -> None:
    store = _StoreClient()
    seeded = 0
    for n in range(2):
        app = build_app(_settings(tmp_path), oxigraph_client=store, start_watcher=False)  # type: ignore[arg-type]
        with TestClient(app):
            pass
        if n == 0:
            seeded = _count(store, relation_store.CATALOG_GRAPH)
    assert seeded > 0
    assert _count(store, relation_store.CATALOG_GRAPH) == seeded


def _count(store: _StoreClient, graph: str) -> int:
    return sum(1 for q in store.store if q.graph_name.value == graph)


def test_default_source_is_code_and_bad_source_is_rejected(tmp_path: Path, healthy_client) -> None:
    app = build_app(_settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False)
    with TestClient(app) as client:
        assert client.get("/api/relations").json() == rc.catalog_to_json()
        assert client.get("/api/relations", params={"source": "nope"}).status_code == 422


def test_seed_failure_does_not_stop_startup(
    tmp_path: Path, unreachable_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*a: Any, **k: Any) -> bool:
        raise RuntimeError("store down")

    monkeypatch.setattr(relation_store, "seed_builtin", boom)
    app = build_app(_settings(tmp_path), oxigraph_client=unreachable_client, start_watcher=False)
    with TestClient(app) as client:  # 起動できる
        assert client.get("/api/relations").json() == rc.catalog_to_json()


def test_unreachable_store_still_starts(tmp_path: Path, unreachable_client) -> None:
    app = build_app(_settings(tmp_path), oxigraph_client=unreachable_client, start_watcher=False)
    with TestClient(app) as client:
        assert client.get("/api/relations").status_code == 200
