"""``resync_rows``: 全量の上げ直し（ingest → promote）。実 registry・実 store スタブで固定する。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from asterism import substrate
from asterism.oxigraph_client import OxigraphClient, OxigraphConfig

from asterism_api import registry
from asterism_api.main import build_app
from asterism_api.mcp_write_tools import register_write_tools

from .test_ingest import _AUTH, _settings
from .test_local import _LOCAL_URL
from .test_local_mcp import _INITIALIZE, _MCP_HEADERS, _app, _initialize_result
from .test_mcp_append_rows import _promoted_json_dataset

fastmcp = pytest.importorskip("fastmcp", reason="MCP tools need the [local] extra")


class _RecOxi:
    """store への POST を graph ごとに溜め、update の順も覚える。live 指しは promote が書く。"""

    def __init__(self) -> None:
        self.stored: dict[str, str] = {}
        self.events: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/store":
                g = request.url.params.get("graph") or ""
                self.stored[g] = self.stored.get(g, "") + request.content.decode()
                self.events.append(f"store:{g}")
                return httpx.Response(204)
            if request.url.path == "/update":
                self.events.append("update:" + request.content.decode())
                return httpx.Response(204)
            q = request.content.decode()
            rows = [{"c": {"value": "0"}}] if "COUNT" in q else []
            return httpx.Response(
                200,
                text=json.dumps({"results": {"bindings": rows}}),
                headers={"content-type": "application/sparql-results+json"},
            )

        inner = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
        self.client = OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner)


def _json_materializer(rml_ttl, csv_dir, *, udfs_path=None, work_dir=None, run_id=None,
                       should_cancel=None) -> Path:
    """source/*.json の各行を 1 トリプルにする（主語は id・値は v）。"""
    lines = []
    for p in sorted(Path(csv_dir).glob("*.json")):
        for row in json.loads(p.read_text()):
            lines.append(f'<https://example.test/{p.stem}/{row["id"]}> '
                         f'<https://schema.org/name> "{row.get("v", "")}" .\n')
    out = Path(work_dir) / "out.nt"
    out.write_text("".join(lines))
    return out


async def _resync(tmp: Path, oxi: _RecOxi, dataset_id: str, files: dict):
    settings = _settings(tmp)
    app = build_app(settings, oxigraph_client=oxi.client, start_watcher=False)
    server = fastmcp.FastMCP("t")
    register_write_tools(
        server,
        registry_root=tmp / "registry",
        get_client=lambda: oxi.client,
        get_app=lambda: app,
        token=_AUTH["X-Asterism-Token"],
    )
    async with app.router.lifespan_context(app), fastmcp.Client(server) as c:
        res = await c.call_tool("resync_rows", {"dataset_id": dataset_id, "files": files})
    return res.data


@pytest.mark.asyncio
async def test_resync_replaces_source_ingests_then_promotes(tmp_path: Path, monkeypatch) -> None:
    dataset_id, old_live = _promoted_json_dataset(tmp_path)
    monkeypatch.setattr(substrate, "materialize_to_nt_file", _json_materializer)
    oxi = _RecOxi()
    out = await _resync(
        tmp_path,
        oxi,
        dataset_id,
        {"judgments.json": [{"id": "a", "v": "NEW"}], "rules.json": []},
    )
    assert out.get("error") is None, out
    assert out["promoted"] is True and out["triples"] == 1
    new_live = substrate.versioned_graph_iri(dataset_id, 2)
    assert out["version"] == new_live != old_live
    # 新しい版には新しい値だけ（古い b / r1 は無い）・主語 IRI は id から同じ
    body = oxi.stored[new_live]
    assert 'judgments/a> <https://schema.org/name> "NEW"' in body
    assert "judgments/b" not in body and "rules/r1" not in body
    # ingest（store への書き込み）→ promote（live の指し替え）の順
    first_store = next(i for i, e in enumerate(oxi.events) if e == f"store:{new_live}")
    live_switch = [
        i for i, e in enumerate(oxi.events) if e.startswith("update:")
        and substrate.LIVE_GRAPH_PREDICATE in e and new_live in e
    ]
    assert live_switch and all(first_store < i for i in live_switch), oxi.events
    meta = registry.load_dataset(tmp_path / "registry", dataset_id)["meta"]
    assert meta["promoted"] and meta["live_graph"] == new_live
    # source は全置換（rules.json は [] になる・applied_batches は消える）
    src = tmp_path / "registry" / dataset_id / "source"
    assert json.loads((src / "rules.json").read_text()) == []
    assert [r["id"] for r in json.loads((src / "judgments.json").read_text())] == ["a"]


@pytest.mark.asyncio
async def test_resync_ingest_validation_error_is_returned(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(substrate, "materialize_to_nt_file", _json_materializer)
    out = await _resync(tmp_path, _RecOxi(), "nope-00000000", {"a.json": [{"id": "1"}]})
    assert out["status"] == 404 and out["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "files",
    [{"x.csv": [{"a": 1}]}, {}, {"a.json": [], "A.JSON": []}],
    ids=["not-json", "empty", "duplicate"],
)
async def test_resync_refusals_are_400(tmp_path: Path, files: dict) -> None:
    dataset_id, _ = _promoted_json_dataset(tmp_path)
    out = await _resync(tmp_path, _RecOxi(), dataset_id, files)
    assert out["status"] == 400 and out["error"]


def test_resync_rows_only_on_the_app_mcp(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from .test_mcp_append_rows import _tool_names

    with TestClient(_app(tmp_path), base_url=_LOCAL_URL) as client:
        assert "resync_rows" in _tool_names(client)

    import asyncio

    from asterism_mcp.server import build_server

    async def standalone() -> set[str]:
        async with fastmcp.Client(build_server()) as c:
            return {t.name for t in await c.list_tools()}

    assert "resync_rows" not in asyncio.run(standalone())


def test_resync_token_reaches_the_write_routes_through_the_mounted_mcp(
    tmp_path: Path, monkeypatch
) -> None:
    """マウントされた /mcp 経由でも 404 まで届く（401 でない）＝ループバック注入が効く。"""
    from fastapi.testclient import TestClient

    dataset_id, _ = _promoted_json_dataset(tmp_path)
    monkeypatch.setattr(substrate, "materialize_to_nt_file", _json_materializer)
    with TestClient(_app(tmp_path), base_url=_LOCAL_URL) as client:
        r = client.post("/mcp", headers=_MCP_HEADERS, json=_INITIALIZE)
        headers = {**_MCP_HEADERS, "mcp-session-id": r.headers["mcp-session-id"]}
        client.post("/mcp", headers=headers,
                    json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        call = client.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                  "params": {"name": "resync_rows",
                             "arguments": {"dataset_id": dataset_id,
                                           "files": {"judgments.json": [{"id": "z"}],
                                                     "rules.json": []}}}},
        )
        out = _initialize_result(call)["result"]["structuredContent"]
    assert out.get("promoted") is True, out


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["x/retract?", "a/../../x", "A B", ""])
async def test_resync_and_append_refuse_unsafe_dataset_ids(tmp_path: Path, bad: str) -> None:
    out = await _resync(tmp_path, _RecOxi(), bad, {"a.json": [{"id": "1"}]})
    assert out["status"] == 400
    server = fastmcp.FastMCP("t")
    register_write_tools(
        server, registry_root=tmp_path / "registry", get_client=lambda: _RecOxi().client
    )
    async with fastmcp.Client(server) as c:
        res = await c.call_tool(
            "append_rows", {"dataset_id": bad, "file_name": "a.json", "rows": [{"id": "1"}]}
        )
    assert res.data["status"] == 400


@pytest.mark.asyncio
async def test_resync_refuses_unpublished_retracted_and_all_empty(tmp_path: Path) -> None:
    dataset_id, _ = _promoted_json_dataset(tmp_path)
    ok_files = {"judgments.json": [{"id": "a"}]}
    allempty = await _resync(tmp_path, _RecOxi(), dataset_id, {"judgments.json": [], "r.json": []})
    assert allempty["status"] == 400
    root = tmp_path / "registry"
    registry.update_meta_atomic(root, dataset_id, {"status": "retracted"})
    retracted = await _resync(tmp_path, _RecOxi(), dataset_id, ok_files)
    assert retracted["status"] == 409
    registry.update_meta_atomic(root, dataset_id, {"status": "active", "promoted": False})
    unpublished = await _resync(tmp_path, _RecOxi(), dataset_id, ok_files)
    assert unpublished["status"] == 409


@pytest.mark.asyncio
async def test_resync_real_morph_kgc_path_with_empty_kind(tmp_path: Path) -> None:
    """materialize を差し替えない: prune_empty_sources → tabularize → Morph-KGC。
    設計は .csv 名の source・ファイルは .json・`[]` の種類は 0 件。"""
    pytest.importorskip("morph_kgc")
    dataset_id, _ = _promoted_json_dataset(tmp_path)
    root = tmp_path / "registry"
    rml = registry.load_dataset(root, dataset_id)["artifacts"]["mapping.rml.ttl"]
    rml = rml.replace(
        '  rr:subjectMap [ rr:template "https://example.test/judgment/{id}" ] .',
        '  rr:subjectMap [ rr:template "https://example.test/judgment/{id}" ] ;\n'
        '  rr:predicateObjectMap [ rr:predicate <https://schema.org/name> ;'
        ' rr:objectMap [ rml:reference "v" ] ] .',
    )
    (root / dataset_id / "mapping.rml.ttl").write_text(rml)
    oxi = _RecOxi()
    out = await _resync(
        tmp_path, oxi, dataset_id, {"judgments.json": [{"id": "a", "v": "NEW"}], "rules.json": []}
    )
    assert out.get("error") is None, out
    new_live = substrate.versioned_graph_iri(dataset_id, 2)
    assert out["version"] == new_live and out["triples"] == 1
    body = oxi.stored[new_live]
    assert "https://example.test/judgment/a" in body and "NEW" in body
    assert "judgment/b" not in body and "rule/" not in body


def _server_and_app(tmp: Path, oxi: _RecOxi):
    app = build_app(_settings(tmp), oxigraph_client=oxi.client, start_watcher=False)
    server = fastmcp.FastMCP("t")
    register_write_tools(
        server,
        registry_root=tmp / "registry",
        get_client=lambda: oxi.client,
        get_app=lambda: app,
        token=_AUTH["X-Asterism-Token"],
    )
    return server, app


def _pointed_live(oxi: _RecOxi, dataset_id: str) -> bool:
    """新しい版（v2）へ liveGraph を向ける update があったか。"""
    v2 = substrate.versioned_graph_iri(dataset_id, 2)
    return any(
        e.startswith("update:") and substrate.LIVE_GRAPH_PREDICATE in e and v2 in e
        for e in oxi.events
    )


_FILES = {"judgments.json": [{"id": "a", "v": "NEW"}], "rules.json": []}


@pytest.mark.asyncio
async def test_resync_ingest_job_error_does_not_promote(tmp_path: Path, monkeypatch) -> None:
    dataset_id, old_live = _promoted_json_dataset(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("morph exploded")

    monkeypatch.setattr(substrate, "materialize_to_nt_file", boom)
    oxi = _RecOxi()
    out = await _resync(tmp_path, oxi, dataset_id, _FILES)
    assert out["status"] == 500 and "morph exploded" in out["error"]
    assert not _pointed_live(oxi, dataset_id)
    meta = registry.load_dataset(tmp_path / "registry", dataset_id)["meta"]
    assert meta["live_graph"] == old_live


@pytest.mark.asyncio
async def test_resync_promote_failure_says_ingested_not_promoted(
    tmp_path: Path, monkeypatch
) -> None:
    from fastapi import HTTPException

    dataset_id, _ = _promoted_json_dataset(tmp_path)
    monkeypatch.setattr(substrate, "materialize_to_nt_file", _json_materializer)

    async def refuse(*a, **k):
        raise HTTPException(503, "store busy")

    monkeypatch.setattr(substrate, "promote_to_canonical", refuse)
    out = await _resync(tmp_path, _RecOxi(), dataset_id, _FILES)
    assert out["status"] == 503 and out["ingested"] is True and out["promoted"] is False
    assert f"/api/datasets/{dataset_id}/promote" in out["hint"]


@pytest.mark.asyncio
async def test_resync_retracted_during_ingest_is_not_promoted(
    tmp_path: Path, monkeypatch
) -> None:
    dataset_id, _ = _promoted_json_dataset(tmp_path)

    def retract_midway(*a, **k):
        registry.update_meta_atomic(tmp_path / "registry", dataset_id, {"status": "retracted"})
        return _json_materializer(*a, **k)

    monkeypatch.setattr(substrate, "materialize_to_nt_file", retract_midway)
    oxi = _RecOxi()
    out = await _resync(tmp_path, oxi, dataset_id, _FILES)
    assert out["status"] == 409 and out["ingested"] is True and out["promoted"] is False
    assert not _pointed_live(oxi, dataset_id)


@pytest.mark.asyncio
async def test_resync_runs_to_promote_even_if_the_caller_is_cancelled(
    tmp_path: Path, monkeypatch
) -> None:
    dataset_id, _ = _promoted_json_dataset(tmp_path)
    started = asyncio.Event()
    loop = asyncio.get_running_loop()

    def slow(*a, **k):
        loop.call_soon_threadsafe(started.set)
        import time

        time.sleep(0.5)
        return _json_materializer(*a, **k)

    monkeypatch.setattr(substrate, "materialize_to_nt_file", slow)
    oxi = _RecOxi()
    server, app = _server_and_app(tmp_path, oxi)
    new_live = substrate.versioned_graph_iri(dataset_id, 2)
    async with app.router.lifespan_context(app), fastmcp.Client(server) as c:
        call = asyncio.create_task(
            c.call_tool("resync_rows", {"dataset_id": dataset_id, "files": _FILES})
        )
        await asyncio.wait_for(started.wait(), 10)
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call
        for _ in range(100):
            meta = registry.load_dataset(tmp_path / "registry", dataset_id)["meta"]
            if meta.get("live_graph") == new_live:
                break
            await asyncio.sleep(0.1)
    assert meta["promoted"] is True and meta["live_graph"] == new_live
