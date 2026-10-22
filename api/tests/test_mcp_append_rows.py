"""``append_rows``: the write tool on the app's own /mcp (not the standalone server)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from asterism import substrate
from fastapi.testclient import TestClient

from asterism_api import registry
from asterism_api.main import _accumulate_source_batch
from asterism_api.mcp_write_tools import register_write_tools

from .test_ingest import _fake_nt_materializer, _FeedOxi
from .test_local import _LOCAL_URL
from .test_local_mcp import _INITIALIZE, _MCP_HEADERS, _app, _initialize_result

fastmcp = pytest.importorskip("fastmcp", reason="MCP tools need the [local] extra")

_RML_JSON = (
    "@prefix rr:  <http://www.w3.org/ns/r2rml#> .\n"
    "@prefix rml: <http://semweb.mmlab.be/ns/rml#> .\n"
    "@prefix ql:  <http://semweb.mmlab.be/ns/ql#> .\n"
    "<#J> a rr:TriplesMap ;\n"
    '  rml:logicalSource [ rml:source "judgments.csv" ; '
    'rml:referenceFormulation ql:CSV ] ;\n'
    '  rr:subjectMap [ rr:template "https://example.test/judgment/{id}" ] .\n'
    "<#R> a rr:TriplesMap ;\n"
    '  rml:logicalSource [ rml:source "rules.csv" ; '
    'rml:referenceFormulation ql:CSV ] ;\n'
    '  rr:subjectMap [ rr:template "https://example.test/rule/{id}" ] .\n'
)


def _promoted_json_dataset(tmp: Path) -> tuple[str, str]:
    root = tmp / "registry"
    dataset_id = registry.save_dataset(
        root,
        "demo",
        {
            "diagram.md": "classDiagram\n  class Judgment",
            "model.yaml": "- Judgment:",
            "mie.yaml": "schema_info:\n  title: x",
            "mapping.rml.ttl": _RML_JSON,
        },
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
        created_at="2026-06-03T00:00:00+00:00",
    )["id"]
    live = substrate.versioned_graph_iri(dataset_id, 1)
    sdir = registry.source_dir(root, dataset_id)
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "judgments.json").write_text(json.dumps([{"id": "a"}, {"id": "b"}]))
    (sdir / "rules.json").write_text(json.dumps([{"id": "r1"}]))
    registry.mark_source_saved(root, dataset_id, ["judgments.json", "rules.json"])
    registry.mark_ingested(
        root, dataset_id, graph_iri=live, triple_count=1,
        ingested_at="2026-06-10T00:00:00+00:00", data_seq=1,
    )
    registry.mark_promoted(
        root, dataset_id, triples_promoted=1, alignment={},
        promoted_at="2026-06-10T00:01:00+00:00",
        canonical_graph=substrate.canonical_graph_iri(dataset_id), live_graph=live,
    )
    return dataset_id, live


async def _call(tmp: Path, args: dict, jobs: list | None = None) -> dict:
    dataset_id = args["dataset_id"]
    oxi = _FeedOxi(substrate.versioned_graph_iri(dataset_id, 1))
    server = fastmcp.FastMCP("t")
    register_write_tools(
        server,
        registry_root=tmp / "registry",
        get_client=lambda: oxi.client,
        log_job=(jobs.append if jobs is not None else None),
    )
    async with fastmcp.Client(server) as c:
        res = await c.call_tool("append_rows", args)
    return res.data


@pytest.mark.asyncio
async def test_append_rows_idempotent_and_merges_source(tmp_path: Path, monkeypatch) -> None:
    dataset_id, _ = _promoted_json_dataset(tmp_path)
    monkeypatch.setattr(substrate, "materialize_to_nt_file", _fake_nt_materializer(triples=2))
    args = {"dataset_id": dataset_id, "file_name": "judgments.json", "rows": [{"id": "c"}]}
    jobs: list = []
    first = await _call(tmp_path, args, jobs)
    assert first["idempotent_replay"] is False
    assert first["append_seq"] == 1
    second = await _call(tmp_path, args, jobs)
    assert second["idempotent_replay"] is True
    # 穴 A: 3 行の配列（上書きで 1 行にならない・重複もしない）
    src = tmp_path / "registry" / dataset_id / "source" / "judgments.json"
    assert [r["id"] for r in json.loads(src.read_text())] == ["a", "b", "c"]
    assert jobs[0]["kind"] == "append" and jobs[0]["status"] == "ok"


@pytest.mark.asyncio
async def test_append_rows_refusals(tmp_path: Path) -> None:
    dataset_id, _ = _promoted_json_dataset(tmp_path)
    base = {"dataset_id": dataset_id, "file_name": "judgments.json"}
    bad = await _call(tmp_path, {**base, "file_name": "x.csv", "rows": [{"a": 1}]})
    assert bad["status"] == 400
    empty = await _call(tmp_path, {**base, "rows": []})
    assert empty["status"] == 400
    jobs: list = []
    missing = await _call(
        tmp_path, {"dataset_id": "nope-00000000", "file_name": "a.json", "rows": [{"a": 1}]}, jobs
    )
    assert missing["status"] == 404 and missing["error"]
    assert jobs[0]["status"] == "error"


@pytest.mark.asyncio
async def test_append_rows_stands_in_empty_array_for_uncovered_json_source(
    tmp_path: Path, monkeypatch
) -> None:
    """穴 B: rules.json だけの追記でも judgments.json は `[]` の代役で通る。"""
    dataset_id, _ = _promoted_json_dataset(tmp_path)
    seen: dict[str, bytes] = {}
    fake = _fake_nt_materializer(triples=1)

    def _spy(rml_ttl, csv_dir, **kw):
        for p in Path(csv_dir).iterdir():
            seen[p.name] = p.read_bytes()
        return fake(rml_ttl, csv_dir, **kw)

    monkeypatch.setattr(substrate, "materialize_to_nt_file", _spy)
    out = await _call(
        tmp_path, {"dataset_id": dataset_id, "file_name": "rules.json", "rows": [{"id": "r2"}]}
    )
    assert "error" not in out, out
    assert seen["judgments.json"] == b"[]"
    assert json.loads(seen["rules.json"]) == [{"id": "r2"}]


@pytest.mark.asyncio
async def test_append_rows_child_files_in_one_batch(tmp_path: Path, monkeypatch) -> None:
    dataset_id, _ = _promoted_json_dataset(tmp_path)
    monkeypatch.setattr(substrate, "materialize_to_nt_file", _fake_nt_materializer(triples=1))
    out = await _call(
        tmp_path,
        {
            "dataset_id": dataset_id,
            "file_name": "judgments.json",
            "rows": [{"id": "c"}],
            "child_files": {"rules.json": [{"id": "r2"}]},
        },
    )
    assert out["append_seq"] == 1
    rules = tmp_path / "registry" / dataset_id / "source" / "rules.json"
    assert [r["id"] for r in json.loads(rules.read_text())] == ["r1", "r2"]


def test_accumulate_json_non_array_overwrites(tmp_path: Path) -> None:
    sdir = tmp_path / "s"
    sdir.mkdir()
    (sdir / "o.json").write_text('{"a": 1}')
    _accumulate_source_batch(sdir, "o.json", b'[{"a": 2}]')
    assert json.loads((sdir / "o.json").read_text()) == [{"a": 2}]


def _tool_names(client: TestClient) -> set[str]:
    r = client.post("/mcp", headers=_MCP_HEADERS, json=_INITIALIZE)
    headers = {**_MCP_HEADERS, "mcp-session-id": r.headers["mcp-session-id"]}
    client.post(
        "/mcp", headers=headers,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    listed = _initialize_result(
        client.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        )
    )
    return {t["name"] for t in listed["result"]["tools"]}


def test_append_rows_only_on_the_app_mcp(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path), base_url=_LOCAL_URL) as client:
        assert "append_rows" in _tool_names(client)

    import asyncio

    from asterism_mcp.server import build_server

    async def standalone() -> set[str]:
        async with fastmcp.Client(build_server()) as c:
            return {t.name for t in await c.list_tools()}

    assert "append_rows" not in asyncio.run(standalone())


@pytest.mark.asyncio
async def test_append_rows_refuses_duplicate_file_names(tmp_path: Path) -> None:
    dataset_id, _ = _promoted_json_dataset(tmp_path)
    out = await _call(
        tmp_path,
        {
            "dataset_id": dataset_id,
            "file_name": "judgments.json",
            "rows": [{"id": "c"}],
            "child_files": {"Judgments.JSON": [{"id": "d"}]},
        },
    )
    assert out["status"] == 400


@pytest.mark.asyncio
async def test_json_batch_onto_real_csv_source_is_refused(tmp_path: Path) -> None:
    """実 CSV として永続化された source に .json は当てない（再取り込みで消えるため）。"""
    dataset_id, _ = _promoted_json_dataset(tmp_path)
    sdir = tmp_path / "registry" / dataset_id / "source"
    (sdir / "judgments.json").unlink()
    (sdir / "judgments.csv").write_text("id\na\n")
    out = await _call(
        tmp_path, {"dataset_id": dataset_id, "file_name": "judgments.json", "rows": [{"id": "c"}]}
    )
    assert out["status"] == 400


def test_accumulate_json_lone_object_is_one_more_row(tmp_path: Path) -> None:
    sdir = tmp_path / "s"
    sdir.mkdir()
    (sdir / "o.json").write_text('[{"a": 1}]')
    _accumulate_source_batch(sdir, "o.json", b'{"a": 2}')
    assert json.loads((sdir / "o.json").read_text()) == [{"a": 1}, {"a": 2}]
