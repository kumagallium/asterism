"""list_relations — 関係の目録を構造のまま返す道具（ストアが正本・読めなければコード）。"""

from __future__ import annotations

from typing import Any

import pyoxigraph
from asterism import relation_catalog, relation_store
from asterism.oxigraph_client import OxigraphClient, OxigraphConfig
from fastmcp import Client

from asterism_mcp.server import Settings, build_server
from asterism_mcp.tools import list_relations

AT = "2026-11-20T00:00:00+00:00"


class _Store:
    """pyoxigraph の Store を OxigraphClient 風に包んだ偽のストア。"""

    def __init__(self) -> None:
        self.store = pyoxigraph.Store()

    async def sparql_update(self, update: str) -> None:
        self.store.update(update)

    async def sparql_select(self, query: str) -> dict:
        result = self.store.query(query, use_default_graph_as_union=True)
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


class _Broken:
    async def sparql_select(self, query: str) -> dict:
        raise RuntimeError("store down")


async def test_reads_the_store_catalog_in_the_api_shape() -> None:
    client = _Store()
    await relation_store.seed_builtin(client, at=AT)
    out = await list_relations(client)  # type: ignore[arg-type]
    assert out["source"] == "store"
    code = relation_catalog.catalog_to_json()
    assert {k: out[k] for k in ("relations", "predicates")} == code
    assert len(out["relations"]) == 12 and len(out["predicates"]) == 15


async def test_empty_store_falls_back_to_code() -> None:
    out = await list_relations(_Store())  # type: ignore[arg-type]
    assert out["source"] == "code"
    assert out["relations"] == relation_catalog.catalog_to_json()["relations"]


async def test_unreadable_store_falls_back_to_code() -> None:
    out = await list_relations(_Broken())  # type: ignore[arg-type]
    assert out["source"] == "code"
    assert out["predicates"] == relation_catalog.catalog_to_json()["predicates"]


async def test_malformed_read_back_falls_back_to_code(monkeypatch) -> None:
    async def bad(client: Any, **kw: Any) -> dict:
        return {"relations": [{"label": "no name"}], "predicates": []}

    monkeypatch.setattr(relation_store, "read_catalog", bad)
    out = await list_relations(_Store())  # type: ignore[arg-type]
    assert out["source"] == "code"


async def test_tool_is_registered_in_both_exposure_profiles() -> None:
    client = OxigraphClient(OxigraphConfig(base_url="http://127.0.0.1:1"))
    for env in ({}, {"ASTERISM_EXPOSE_RAW_SPARQL": "0"}):
        mcp = build_server(Settings(env), oxigraph_client=client)
        assert "list_relations" in {t.name for t in await mcp.list_tools()}


async def test_tool_call_returns_the_catalog_with_source() -> None:
    client = _Store()
    await relation_store.seed_builtin(client, at=AT)
    mcp = build_server(Settings({}), oxigraph_client=client)  # type: ignore[arg-type]
    async with Client(mcp) as c:
        res = await c.call_tool("list_relations", {})
    body = res.structured_content
    assert body is not None and body["source"] == "store"
    assert set(body) == {"relations", "predicates", "source"}
