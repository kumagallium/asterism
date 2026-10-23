"""MCP クライアント（呼ぶ側）と ``/api/mcp-servers/*``——ADR ``source-switch-mcp-client.md``。

ここで固定するのは**信頼境界**: 単一ユーザーでなければ 404・登録しただけでは繋がない
（409）・コマンドは設定からだけ・``/call`` は取り込み元の一覧と取得の 2 名だけ・
stdio の子プロセスは呼び出しのあと残らない。

``fastmcp`` は api の本体依存（``importorskip`` で逃げない。CI でも走る）。
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import textwrap
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fastmcp import FastMCP
from fastmcp.client.transports import FastMCPTransport, StdioTransport, StreamableHttpTransport
from fastmcp.exceptions import ToolError

from asterism_api import appdata
from asterism_api.main import build_app
from asterism_api.mcp_client import (
    MAX_SOURCE_FILE_BYTES,
    McpClient,
    McpConfigError,
    McpServerConfig,
    McpToolError,
    build_transport,
    parse_server,
    parse_servers,
    parse_source_file,
    parse_source_home,
    parse_source_items,
    parse_source_provider,
    send_originals_enabled,
)
from tests.test_main import _AUTH, _settings, healthy_client  # noqa: F401

# ruff: noqa: F811  — `healthy_client` is a pytest fixture reused by name.

# ---- (a) 設定 → transport ---------------------------------------------------------


def test_parse_stdio_server_builds_a_transport_that_does_not_keep_the_child() -> None:
    cfg, warnings = parse_server(
        "graphium",
        {
            "transport": "stdio",
            "command": "/usr/local/bin/node",
            "args": ["/x/dist-mcp/graphium-mcp.mjs"],
            "env": {"GRAPHIUM_ROOT": "/tmp/vault"},
            "enabled": True,
        },
    )
    assert warnings == []
    assert cfg.command_line() == "/usr/local/bin/node /x/dist-mcp/graphium-mcp.mjs"
    t = build_transport(cfg)
    assert isinstance(t, StdioTransport)
    assert t.keep_alive is False  # 呼ぶたびに起こして閉じる（子を残さない）
    assert t.env == {"GRAPHIUM_ROOT": "/tmp/vault"}


def test_parse_http_server() -> None:
    cfg, _ = parse_server("remote", {"transport": "http", "url": " http://127.0.0.1:9999/mcp "})
    assert cfg.url == "http://127.0.0.1:9999/mcp"
    assert cfg.enabled is False  # 既定は無効（登録しただけでは動かない）
    assert isinstance(build_transport(cfg), StreamableHttpTransport)
    assert cfg.public() == {
        "name": "remote",
        "transport": "http",
        "enabled": False,
        "url": "http://127.0.0.1:9999/mcp",
    }


@pytest.mark.parametrize(
    "raw",
    [
        {"transport": "http", "url": "http://x/mcp", "headers": {"Authorization": "Bearer t"}},
        {"transport": "http", "url": "http://x/mcp", "auth": "oauth"},
        {"transport": "ws", "url": "ws://x"},
        {"transport": "http", "url": "ftp://x"},
        {"transport": "stdio", "command": ""},
        {"transport": "stdio", "command": "node", "args": "a b"},
        {"transport": "stdio", "command": "node", "env": {"1BAD": "x"}},
        {"transport": "stdio", "command": "node", "enabled": "yes"},
        "not a dict",
    ],
)
def test_parse_rejects_bad_shapes_and_forbidden_keys(raw: object) -> None:
    with pytest.raises(McpConfigError):
        parse_server("s", raw)


@pytest.mark.parametrize("name", ["", "has space", "../x", "a" * 65, "api_token", "key"])
def test_parse_rejects_bad_names(name: str) -> None:
    with pytest.raises(McpConfigError):
        parse_server(name, {"transport": "http", "url": "http://x/mcp"})


def test_parse_warns_about_secret_looking_env_names() -> None:
    _, warnings = parse_server(
        "s",
        {"transport": "stdio", "command": "node", "env": {"GRAPHIUM_ROOT": "/v", "API_TOKEN": "x"}},
    )
    assert len(warnings) == 1
    assert "API_TOKEN" in warnings[0]


def test_parse_servers_keeps_the_good_ones_and_names_the_broken() -> None:
    ok, bad = parse_servers(
        {
            "mcpServers": {
                "a": {"transport": "http", "url": "http://x/mcp"},
                "b": {"transport": "http", "url": "http://x", "headers": {}},
                "c": 1,
            }
        }
    )
    assert set(ok) == {"a"}
    assert set(bad) == {"b", "c"}
    assert parse_servers({}) == ({}, {})
    assert parse_servers({"mcpServers": []}) == ({}, {})


def test_source_settings_defaults() -> None:
    assert parse_source_provider({}).list == ""
    assert parse_source_home({}) is None
    assert parse_source_home({"sourceHome": {"server": " g "}}) == "g"
    assert send_originals_enabled({}) is True  # 既定オン（裁定 ③）
    assert send_originals_enabled({"sendOriginalsToHome": False}) is False
    p = parse_source_provider({"sourceProvider": {"list": "list_media", "get": "get_media_file"}})
    assert (p.list, p.get, p.add) == ("list_media", "get_media_file", "")


# ---- プロトコルの返事を読む -------------------------------------------------------


def test_parse_source_items_reads_only_the_contract_fields() -> None:
    items = parse_source_items(
        {
            "items": [
                {"id": "f1", "name": "price.csv", "mimeType": "text/csv", "size": 12, "extra": 1},
                {"id": "f2", "name": "no-mime"},
                {"name": "no id"},
                "junk",
            ]
        }
    )
    assert [i.public() for i in items] == [
        {
            "id": "f1",
            "name": "price.csv",
            "mimeType": "text/csv",
            "size": 12,
            "uploadedAt": None,
            "folder": None,
        },
        {
            "id": "f2",
            "name": "no-mime",
            "mimeType": "application/octet-stream",
            "size": None,
            "uploadedAt": None,
            "folder": None,
        },
    ]
    with pytest.raises(McpToolError):
        parse_source_items({"rows": []})


def test_parse_source_file_decodes_and_caps() -> None:
    f = parse_source_file(
        {
            "id": "f1",
            "name": "p.csv",
            "mimeType": "text/csv",
            "size": 3,
            "bytes_base64": base64.b64encode(b"a,b").decode(),
        }
    )
    assert (f.id, f.name, f.size, f.data) == ("f1", "p.csv", 3, b"a,b")
    with pytest.raises(McpToolError, match="大きすぎ"):
        parse_source_file({"id": "f", "name": "big", "size": MAX_SOURCE_FILE_BYTES + 1})
    with pytest.raises(McpToolError, match="本文"):
        parse_source_file({"id": "f", "name": "x", "size": 1})
    with pytest.raises(McpToolError, match="base64"):
        parse_source_file({"id": "f", "name": "x", "size": 1, "bytes_base64": "@@"})


# ---- (b) インプロセスの偽サーバで 4 ルート ---------------------------------------------


def _fake_server() -> tuple[FastMCP, dict[str, object]]:
    """取り込み元プロトコル（§5）を実装した偽サーバ。受け取った引数を ``seen`` に残す。"""
    seen: dict[str, object] = {}
    srv = FastMCP("fake-source")

    @srv.tool
    def list_media(exts: list[str] | None = None, limit: int = 100) -> str:
        seen["list"] = {"exts": exts, "limit": limit}
        return json.dumps(
            {"items": [{"id": "f1", "name": "price.csv", "mimeType": "text/csv", "size": 3}]}
        )

    @srv.tool
    def get_media_file(id: str, inline: bool = False) -> str:
        seen["get"] = {"id": id, "inline": inline}
        return json.dumps(
            {
                "id": id,
                "name": "price.csv",
                "mimeType": "text/csv",
                "size": 3,
                "bytes_base64": base64.b64encode(b"a,b").decode(),
            }
        )

    @srv.tool
    def add_material(bytes_base64: str, name: str, mimeType: str | None = None) -> str:
        seen["add"] = {"name": name}
        return json.dumps({"id": "new", "duplicate": False, "indexed": False})

    @srv.tool
    def failing() -> str:
        raise ToolError(json.dumps({"error": "nope"}))

    @srv.tool
    def not_json() -> str:
        return "plain words"

    @srv.tool
    async def slow() -> str:
        await asyncio.sleep(2)
        return "{}"

    return srv, seen


_SERVERS = {
    "fake": {"transport": "http", "url": "http://127.0.0.1:1/mcp", "enabled": True},
    "off": {"transport": "http", "url": "http://127.0.0.1:1/mcp", "enabled": False},
    "broken": {"transport": "http", "url": "http://x", "headers": {}},
}
_PROVIDER = {"list": "list_media", "get": "get_media_file", "add": "add_material"}


def _single_user_settings(tmp_path: Path):
    s = _settings(tmp_path)
    s.single_user = True
    s.appdata_root = tmp_path / "appdata"
    s.appdata_root.mkdir(parents=True, exist_ok=True)
    return s


def _write_settings(tmp_path: Path, payload: dict) -> None:
    appdata.write_settings(tmp_path / "appdata", payload)


def _client_with_fake(
    tmp_path: Path, healthy_client, *, settings_payload: dict | None = None, **client_kwargs
) -> tuple[TestClient, dict[str, object]]:
    """偽サーバを transport に差し込んだ TestClient。設定は settings.json に実際に書く。"""
    settings = _single_user_settings(tmp_path)
    _write_settings(
        tmp_path,
        settings_payload
        if settings_payload is not None
        else {"mcpServers": _SERVERS, "sourceProvider": _PROVIDER},
    )
    app = build_app(settings, oxigraph_client=healthy_client, start_watcher=False)
    srv, seen = _fake_server()
    registry = app.state.mcp_registry
    assert registry is not None
    registry.client = McpClient(
        transport_factory=lambda cfg: FastMCPTransport(srv), **client_kwargs
    )
    return TestClient(app, headers=_AUTH), seen


def test_shared_api_404s_every_mcp_server_route(tmp_path: Path, healthy_client) -> None:
    app = build_app(_settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False)
    assert app.state.mcp_registry is None
    with TestClient(app, headers=_AUTH) as client:
        assert client.get("/api/mcp-servers").status_code == 404
        assert client.post("/api/mcp-servers/fake/tools").status_code == 404
        assert (
            client.post("/api/mcp-servers/fake/call", json={"tool": "list_media"}).status_code
            == 404
        )
        assert (
            client.post("/api/mcp-servers/fake/source-items", json={"exts": [".csv"]}).status_code
            == 404
        )


def test_routes_require_the_write_token(tmp_path: Path, healthy_client) -> None:
    client, _ = _client_with_fake(tmp_path, healthy_client)
    with client:
        assert client.get("/api/mcp-servers", headers={"X-Asterism-Token": ""}).status_code == 401
        assert (
            client.post(
                "/api/mcp-servers/fake/tools", headers={"X-Asterism-Token": "no"}
            ).status_code
            == 401
        )


def test_list_reports_settings_without_connecting(tmp_path: Path, healthy_client) -> None:
    client, seen = _client_with_fake(tmp_path, healthy_client)
    with client:
        r = client.get("/api/mcp-servers")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [s["name"] for s in body["servers"]] == ["fake", "off"]
    assert body["invalid"][0]["name"] == "broken"
    assert "headers" in body["invalid"][0]["error"]
    assert body["source_provider"] == _PROVIDER
    assert body["source_home"] is None
    assert body["send_originals_to_home"] is True
    assert body["recent"] == []
    assert seen == {}  # GET は繋がない


def test_tools_lists_the_fake_servers_tools(tmp_path: Path, healthy_client) -> None:
    client, _ = _client_with_fake(tmp_path, healthy_client)
    with client:
        r = client.post("/api/mcp-servers/fake/tools")
        assert r.status_code == 200, r.text
        names = {t["name"] for t in r.json()["tools"]}
        assert {"list_media", "get_media_file", "add_material"} <= names
        assert all("input_schema" in t for t in r.json()["tools"])
        recent = client.get("/api/mcp-servers").json()["recent"]
    assert recent[0]["server"] == "fake" and recent[0]["ok"] is True
    assert recent[0]["count"] == len(names)


def test_disabled_and_unknown_and_broken_servers_are_never_contacted(
    tmp_path: Path, healthy_client
) -> None:
    client, seen = _client_with_fake(tmp_path, healthy_client)
    with client:
        assert client.post("/api/mcp-servers/off/tools").status_code == 409
        assert client.post("/api/mcp-servers/nope/tools").status_code == 404
        assert client.post("/api/mcp-servers/broken/tools").status_code == 400
        assert (
            client.post("/api/mcp-servers/off/call", json={"tool": "list_media"}).status_code == 409
        )
        assert (
            client.post("/api/mcp-servers/off/source-items", json={"exts": [".csv"]}).status_code
            == 409
        )
    assert seen == {}


def test_call_allows_only_the_source_list_and_get_tools(tmp_path: Path, healthy_client) -> None:
    client, seen = _client_with_fake(tmp_path, healthy_client)
    with client:
        r = client.post(
            "/api/mcp-servers/fake/call", json={"tool": "list_media", "args": {"exts": [".csv"]}}
        )
        assert r.status_code == 200, r.text
        assert r.json()["result"]["items"][0]["id"] == "f1"
        assert seen["list"] == {"exts": [".csv"], "limit": 100}

        r = client.post(
            "/api/mcp-servers/fake/call", json={"tool": "get_media_file", "args": {"id": "f1"}}
        )
        assert r.status_code == 200, r.text
        assert r.json()["result"]["bytes_base64"]

        # 書き込みの道具は HTTP から呼べない（呼び手は ✓ の後処理だけ）。
        r = client.post(
            "/api/mcp-servers/fake/call",
            json={"tool": "add_material", "args": {"bytes_base64": "", "name": "x"}},
        )
        assert r.status_code == 403
        assert "add" not in seen
        # allowlist 外は、存在する道具でも呼べない。
        assert (
            client.post("/api/mcp-servers/fake/call", json={"tool": "failing"}).status_code == 403
        )
        # 形の違う本文。
        assert client.post("/api/mcp-servers/fake/call", json={"args": {}}).status_code == 400
        assert (
            client.post(
                "/api/mcp-servers/fake/call", json={"tool": "list_media", "args": []}
            ).status_code
            == 400
        )


def test_call_without_a_source_provider_allows_nothing(tmp_path: Path, healthy_client) -> None:
    client, seen = _client_with_fake(
        tmp_path, healthy_client, settings_payload={"mcpServers": _SERVERS}
    )
    with client:
        assert (
            client.post("/api/mcp-servers/fake/call", json={"tool": "list_media"}).status_code
            == 403
        )
        r = client.post("/api/mcp-servers/fake/source-items", json={"exts": [".csv"]})
        assert r.status_code == 409
    assert seen == {}


def test_source_items_forwards_lowercased_exts_and_parses_the_contract(
    tmp_path: Path, healthy_client
) -> None:
    client, seen = _client_with_fake(tmp_path, healthy_client)
    with client:
        r = client.post("/api/mcp-servers/fake/source-items", json={"exts": [".CSV", ".xlsx"]})
        assert r.status_code == 200, r.text
        assert r.json()["items"] == [
            {
                "id": "f1",
                "name": "price.csv",
                "mimeType": "text/csv",
                "size": 3,
                "uploadedAt": None,
                "folder": None,
            }
        ]
        assert seen["list"] == {"exts": [".csv", ".xlsx"], "limit": 100}
        assert (
            client.post("/api/mcp-servers/fake/source-items", json={"exts": "csv"}).status_code
            == 400
        )


def test_tool_errors_and_non_json_answers_are_502(tmp_path: Path, healthy_client) -> None:
    client, _ = _client_with_fake(
        tmp_path,
        healthy_client,
        settings_payload={
            "mcpServers": _SERVERS,
            "sourceProvider": {"list": "failing", "get": "not_json"},
        },
    )
    with client:
        r = client.post("/api/mcp-servers/fake/call", json={"tool": "failing"})
        assert r.status_code == 502
        assert "nope" in r.json()["detail"]
        r = client.post("/api/mcp-servers/fake/call", json={"tool": "not_json"})
        assert r.status_code == 502
        assert "JSON" in r.json()["detail"]
        recent = client.get("/api/mcp-servers").json()["recent"]
    assert [c["ok"] for c in recent[:2]] == [False, False]


def test_slow_tool_is_504(tmp_path: Path, healthy_client) -> None:
    client, _ = _client_with_fake(
        tmp_path,
        healthy_client,
        settings_payload={
            "mcpServers": _SERVERS,
            "sourceProvider": {"list": "slow", "get": "slow"},
        },
        list_timeout_s=0.3,
        get_timeout_s=0.3,
    )
    with client:
        started = time.monotonic()
        r = client.post("/api/mcp-servers/fake/call", json={"tool": "slow"})
        assert r.status_code == 504, r.text
        assert time.monotonic() - started < 1.9


def test_unreachable_http_server_is_502(tmp_path: Path, healthy_client) -> None:
    """transport は本物（閉じたポートの URL）。繋げない → 502 で、固まらない。"""
    settings = _single_user_settings(tmp_path)
    _write_settings(tmp_path, {"mcpServers": _SERVERS, "sourceProvider": _PROVIDER})
    app = build_app(settings, oxigraph_client=healthy_client, start_watcher=False)
    app.state.mcp_registry.client = McpClient(connect_timeout_s=3, list_timeout_s=3)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post("/api/mcp-servers/fake/tools")
        assert r.status_code == 502, r.text
        assert "繋げませんでした" in r.json()["detail"]


# ---- (c) stdio: 本物の子プロセスを起こし、終わったら残らない ------------------------------

_STDIO_SCRIPT = textwrap.dedent(
    """
    import json, os, pathlib
    from fastmcp import FastMCP
    pathlib.Path(os.environ["ASTERISM_TEST_PID_FILE"]).write_text(str(os.getpid()))
    srv = FastMCP("stdio-fake")

    @srv.tool
    def list_media(exts: list[str] | None = None, limit: int = 100) -> str:
        return json.dumps({"items": [{"id": "s1", "name": "from-stdio.csv",
                                      "mimeType": "text/csv",
                                      "folder": os.environ.get("FAKE_ROOT")}]})

    srv.run()
    """
)


def _wait_gone(pid: int, seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def test_stdio_server_is_started_from_settings_and_the_child_does_not_linger(
    tmp_path: Path, healthy_client
) -> None:
    script = tmp_path / "fake_stdio_server.py"
    script.write_text(_STDIO_SCRIPT, encoding="utf-8")
    pid_file = tmp_path / "child.pid"
    settings = _single_user_settings(tmp_path)
    _write_settings(
        tmp_path,
        {
            "mcpServers": {
                "py": {
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": [str(script)],
                    "env": {
                        "ASTERISM_TEST_PID_FILE": str(pid_file),
                        "FAKE_ROOT": "/vault",
                        # fastmcp の子は親の環境を継がない（StdioTransport は env を
                        # そのまま渡す）ので、import に要る PATH 系は明示する。
                        "PATH": os.environ.get("PATH", ""),
                        "PYTHONPATH": os.pathsep.join(sys.path),
                    },
                    "enabled": True,
                }
            },
            "sourceProvider": {"list": "list_media", "get": "get_media_file"},
        },
    )
    app = build_app(settings, oxigraph_client=healthy_client, start_watcher=False)
    app.state.mcp_registry.client = McpClient(connect_timeout_s=30, list_timeout_s=30)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post("/api/mcp-servers/py/source-items", json={"exts": [".csv"]})
        assert r.status_code == 200, r.text
        assert r.json()["items"][0] == {
            "id": "s1",
            "name": "from-stdio.csv",
            "mimeType": "text/csv",
            "size": None,
            "uploadedAt": None,
            "folder": "/vault",  # 設定の env が子に届いた
        }
    pid = int(pid_file.read_text())
    assert _wait_gone(pid), f"stdio child {pid} is still alive after the call"


def test_build_transport_for_stdio_uses_the_configured_command_only() -> None:
    """リクエストで受けた物ではなく、設定の command/args/cwd がそのまま transport に乗る。"""
    cfg = McpServerConfig(
        name="s", transport="stdio", enabled=True, command="node", args=("a", "b"), cwd="/w"
    )
    t = build_transport(cfg)
    assert (t.command, t.args, t.cwd, t.keep_alive) == ("node", ["a", "b"], "/w", False)


# ---- レビュー（2026-10-23）で足した穴 ------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    ["http://user:pw@127.0.0.1/mcp", "http://127.0.0.1/mcp?token=abc", "http://x/mcp?api_key=1"],
)
def test_parse_rejects_urls_that_would_leak_a_secret_into_error_text(url: str) -> None:
    with pytest.raises(McpConfigError, match="url"):
        parse_server("s", {"transport": "http", "url": url})
    parse_server("s", {"transport": "http", "url": "http://127.0.0.1/mcp?lang=ja"})


def test_parse_names_do_not_accept_a_trailing_newline() -> None:
    with pytest.raises(McpConfigError):
        parse_server("abc\n", {"transport": "http", "url": "http://x/mcp"})
    with pytest.raises(McpConfigError):
        parse_server("s", {"transport": "stdio", "command": "node", "env": {"FOO\n": "1"}})


def test_timeout_is_recognised_through_the_cause_chain() -> None:
    from asterism_api.mcp_client import _is_timeout

    try:
        try:
            raise TimeoutError("init")
        except TimeoutError as inner:
            raise RuntimeError("Client failed to connect: ") from inner
    except RuntimeError as outer:
        assert _is_timeout(outer)
    assert not _is_timeout(RuntimeError("connection refused"))
    assert _is_timeout(ExceptionGroup("g", [RuntimeError("Request 'tools/call' timed out")]))


def test_call_only_forwards_the_contract_arguments(tmp_path: Path, healthy_client) -> None:
    client, seen = _client_with_fake(tmp_path, healthy_client)
    with client:
        r = client.post(
            "/api/mcp-servers/fake/call",
            json={"tool": "get_media_file", "args": {"id": "f1", "path": "/etc/hosts"}},
        )
        assert r.status_code == 400
        assert "path" in r.json()["detail"]
        r = client.post(
            "/api/mcp-servers/fake/call", json={"tool": "list_media", "args": {"limit": 5}}
        )
        assert r.status_code == 200
    assert "get" not in seen
    assert seen["list"]["limit"] == 5
