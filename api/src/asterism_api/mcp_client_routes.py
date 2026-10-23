"""``register_mcp_client_routes(app, cfg)`` — 登録した MCP サーバの道具を列挙・呼び出す
4 ルート（ADR ``source-switch-mcp-client.md`` §信頼境界）。

``appdata_cards_routes.py`` と同じ書き方: own の ``@app.get``／``@app.post`` を own で
登録し、``main.py`` には呼び出し 1 行だけ。``require_write_auth`` は ``main.py`` の
closure で import できないので verbatim コピーを持つ（理由は ``cards_routes.py`` の
モジュール docstring）。

信頼境界（利用者の裁定 2026-10-23・②）
--------------------------------------
* **単一ユーザーモード限定**: ``cfg.single_user`` が偽か ``cfg.appdata_root`` が None なら
  4 本すべて 404（``_appdata_root_or_404`` と同じ条件）。共有 api から他人の PC で
  子プロセスを起こす口にならない。
* **コマンドは設定からだけ読む**: リクエストにコマンド・URL・引数を載せる口は無い。
  ``settings.json`` を書けるのは UI（``putAppSetting``）だけで、api はここで読むだけ。
* **登録しただけでは動かない**: ``enabled: true`` でないサーバには ``tools``・``call``・
  ``source-items`` のどれも繋がない（409）。有効化は画面でコマンド全文を見てから。
* **``/call`` は allowlist 2 名だけ**: ``sourceProvider.list`` と ``.get``。``add``（書き込み）
  は ✓ の後処理の送り込み（内部コード）からだけ呼び、HTTP からは呼べない（403）。
* 起動を伴う経路はすべて **POST**（GET は状態を変えない前提・``local.py``）。
* 4 本とも ``_write_auth``（ループバックでは ``LoopbackTokenInjector`` が足す）。

HTTP の対応表: 未登録 404・設定が読めない 400・未有効 409・allowlist 外 403・
繋げない 502・道具の失敗と形違い 502（本文に道具の文言）・時間切れ 504。
"""

from __future__ import annotations

import glob
import hmac
import logging
import os
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request

from asterism_api import appdata
from asterism_api.mcp_client import (
    MAX_SOURCE_FILE_BYTES,
    McpClient,
    McpConnectError,
    McpServerConfig,
    McpTimeout,
    McpToolError,
    SourceFile,
    SourceProvider,
    parse_servers,
    parse_source_file,
    parse_source_home,
    parse_source_items,
    parse_source_provider,
    send_originals_enabled,
)

if TYPE_CHECKING:  # pragma: no cover
    from asterism_api.main import Settings

logger = logging.getLogger(__name__)

#: ``/call`` の本文（JSON）の上限。道具の引数は小さい（id・拡張子の配列）。
MAX_CALL_BODY_BYTES = 64 * 1024

#: ``/call`` が通す引数の名前（取り込み元プロトコル §3 の入力だけ）。道具名の allowlist
#: だけだと、相手が ``path`` のような引数を解釈する場合に HTTP から任意のファイルを読ませる
#: 口になる（レビュー M3）。
CALL_ARGS_ALLOWED = {"list": {"exts", "folder", "limit"}, "get": {"id", "inline"}}


def _require_write_auth(cfg: Settings):
    """``cards_routes._require_write_auth`` の verbatim コピー。"""

    def _dep(
        authorization: str | None = Header(default=None),
        x_asterism_token: str | None = Header(default=None),
    ) -> None:
        token = cfg.api_token
        if not token:
            logger.warning(
                "write route refused: ASTERISM_API_TOKEN is unset "
                "(fail-closed against anonymous writes)"
            )
            raise HTTPException(
                503,
                "利用許可コード (管理者が設定する API token) が未設定のため、この操作はできません",
            )
        presented: str | None = None
        if authorization and authorization.startswith("Bearer "):
            presented = authorization[len("Bearer ") :].strip()
        elif x_asterism_token:
            presented = x_asterism_token.strip()
        if not (presented and hmac.compare_digest(presented, token)):
            raise HTTPException(401, "利用許可コードが違います")

    return _dep


def _reject_if_content_length_exceeds(request: Request, limit: int) -> None:
    raw = request.headers.get("content-length")
    if raw is None:
        return
    try:
        declared = int(raw)
    except ValueError:
        return
    if declared > limit:
        raise HTTPException(413, f"body is {declared} bytes, over the {limit} limit")


class McpServerRegistry:
    """設定（``settings.json``）を読んで、繋いでよいサーバを選ぶ。

    リクエストごとに読み直す（UI が ``putAppSetting`` で書いた直後の値を使う。
    キャッシュすると「有効化したのに 409」になる）。``client`` はプロセスで 1 つ
    （直近の呼び出しの一覧を持つため）。
    """

    def __init__(self, appdata_root: Path, client: McpClient) -> None:
        self.appdata_root = appdata_root
        self.client = client

    def settings(self) -> dict[str, Any]:
        return appdata.read_settings(self.appdata_root)

    def servers(self) -> tuple[dict[str, McpServerConfig], dict[str, str]]:
        return parse_servers(self.settings())

    def provider(self) -> SourceProvider:
        return parse_source_provider(self.settings())

    def source_home(self) -> str | None:
        return parse_source_home(self.settings())

    def send_originals(self) -> bool:
        return send_originals_enabled(self.settings())

    def enabled_server_or_raise(self, name: str) -> McpServerConfig:
        """登録済みで有効なサーバだけ返す。無ければ 404・読めなければ 400・未有効は 409。"""
        ok, bad = self.servers()
        if name in bad:
            raise HTTPException(400, f"MCP サーバ {name} の設定が読めません: {bad[name]}")
        cfg = ok.get(name)
        if cfg is None:
            raise HTTPException(404, f"MCP サーバ {name} は登録されていません")
        if not cfg.enabled:
            raise HTTPException(
                409, f"MCP サーバ {name} はまだ有効にされていません (設定画面で有効にしてください)"
            )
        return cfg

    # ---- 取り込み元プロトコル（内部コードからも呼ぶ） ----

    async def list_source_items(self, name: str, exts: list[str]) -> list[dict[str, Any]]:
        cfg = self.enabled_server_or_raise(name)
        provider = self.provider()
        if not provider.list:
            raise HTTPException(
                409, "取り込み元の一覧の道具 (sourceProvider.list) が設定されていません"
            )
        result = await _call_or_http_error(
            self.client, cfg, provider.list, {"exts": [e.lower() for e in exts]}
        )
        return [item.public() for item in parse_source_items(result.data)]

    async def get_source_file(self, name: str, file_id: str) -> SourceFile:
        cfg = self.enabled_server_or_raise(name)
        provider = self.provider()
        if not provider.get:
            raise HTTPException(
                409, "取り込み元の取得の道具 (sourceProvider.get) が設定されていません"
            )
        result = await _call_or_http_error(
            self.client,
            cfg,
            provider.get,
            {"id": file_id, "inline": True},
            timeout_s=self.client.get_timeout_s,
        )
        try:
            return parse_source_file(result.data)
        except McpToolError as exc:
            raise HTTPException(502, str(exc)) from exc


async def _call_or_http_error(
    client: McpClient,
    cfg: McpServerConfig,
    tool: str,
    arguments: dict[str, Any],
    *,
    timeout_s: float | None = None,
):
    try:
        return await client.call_tool(cfg, tool, arguments, timeout_s=timeout_s)
    except McpTimeout as exc:
        raise HTTPException(504, str(exc)) from exc
    except McpConnectError as exc:
        raise HTTPException(502, str(exc)) from exc
    except McpToolError as exc:
        raise HTTPException(502, str(exc)) from exc


def register_mcp_client_routes(
    app: FastAPI, cfg: Settings, *, client: McpClient | None = None
) -> McpServerRegistry | None:
    """4 ルートを登録する。単一ユーザーでないときも登録はする（404 を返すため）。

    返りは :class:`McpServerRegistry`（``chat_routes`` が取り込み元と送り込みで使う。
    ``app.state.mcp_registry`` にも置く）。単一ユーザーでなければ None。
    """
    write_auth = [Depends(_require_write_auth(cfg))]
    registry: McpServerRegistry | None = None
    if cfg.single_user and cfg.appdata_root is not None:
        registry = McpServerRegistry(cfg.appdata_root, client or McpClient())
    app.state.mcp_registry = registry

    def _registry_or_404() -> McpServerRegistry:
        if registry is None:
            raise HTTPException(404, "MCP servers are only available in single-user mode")
        return registry

    @app.get("/api/mcp-servers", dependencies=write_auth)
    async def mcp_servers_list() -> dict[str, Any]:
        """登録済みのサーバ（設定の写し）と、取り込み元の設定、直近の呼び出し。繋がない。"""
        reg = _registry_or_404()
        ok, bad = reg.servers()
        provider = reg.provider()
        return {
            "servers": [c.public() for c in ok.values()],
            "invalid": [{"name": n, "error": e} for n, e in bad.items()],
            "source_home": reg.source_home(),
            "source_provider": {"list": provider.list, "get": provider.get, "add": provider.add},
            "send_originals_to_home": reg.send_originals(),
            "max_source_file_bytes": MAX_SOURCE_FILE_BYTES,
            "recent": [r.public() for r in reg.client.recent],
        }

    @app.post("/api/mcp-servers/locate", dependencies=write_auth)
    async def mcp_servers_locate(request: Request) -> dict[str, Any]:
        """連携のプリセット（「Graphium と連携する」）が、node のような実行ファイルの
        **在りか**を探す口。読むだけ（存在と実行可能の検査）で、起動も保存もしない。
        本文 ``{command: "node", candidates: ["/opt/homebrew/bin/node", …]}``。候補を順に
        見て、無ければ PATH（``shutil.which``）。デスクトップ版は PATH を引き継がないことが
        あるので候補が先。返り ``{found: path | null, user_home: "<ホーム>"}``（``user_home`` は
        既定の vault の場所を UI が組み立てるため）。"""
        _registry_or_404()
        _reject_if_content_length_exceeds(request, MAX_CALL_BODY_BYTES)
        try:
            body = await request.json()
        except ValueError as exc:
            raise HTTPException(400, "body must be JSON") from exc
        command = body.get("command") if isinstance(body, dict) else None
        candidates = body.get("candidates") if isinstance(body, dict) else None
        if not isinstance(command, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", command):
            raise HTTPException(400, "command は実行ファイルの名前です (例: node)")
        if not isinstance(candidates, list) or not all(isinstance(c, str) for c in candidates):
            raise HTTPException(400, "candidates は文字列の配列です")
        found: str | None = None
        for cand in candidates[:32]:
            expanded = os.path.expanduser(cand)
            # nvm のように版ごとのフォルダに入る物は * で受け、新しい版（名前の降順）を先に
            paths = sorted(glob.glob(expanded), reverse=True) if "*" in expanded else [expanded]
            for p in paths:
                path = Path(p)
                if path.is_file() and os.access(path, os.X_OK):
                    found = str(path)
                    break
            if found:
                break
        if found is None:
            found = shutil.which(command)
        return {"found": found, "user_home": str(Path.home())}

    @app.post("/api/mcp-servers/{name}/tools", dependencies=write_auth)
    async def mcp_servers_tools(name: str) -> dict[str, Any]:
        """有効なサーバに繋いで道具を列挙する（stdio は子を起こすので POST）。"""
        reg = _registry_or_404()
        server = reg.enabled_server_or_raise(name)
        try:
            tools = await reg.client.list_tools(server)
        except McpTimeout as exc:
            raise HTTPException(504, str(exc)) from exc
        except McpConnectError as exc:
            raise HTTPException(502, str(exc)) from exc
        return {"server": name, "tools": [t.public() for t in tools]}

    @app.post("/api/mcp-servers/{name}/call", dependencies=write_auth)
    async def mcp_servers_call(name: str, request: Request) -> dict[str, Any]:
        """道具を 1 回呼ぶ。呼べるのは ``sourceProvider.list``／``.get`` の 2 名だけ。"""
        reg = _registry_or_404()
        _reject_if_content_length_exceeds(request, MAX_CALL_BODY_BYTES)
        try:
            body = await request.json()
        except ValueError as exc:
            raise HTTPException(400, "body must be JSON") from exc
        if not isinstance(body, dict) or not isinstance(body.get("tool"), str):
            raise HTTPException(400, "body は {tool, args} の JSON オブジェクトです")
        tool = body["tool"].strip()
        args = body.get("args")
        if args is None:
            args = {}
        if not isinstance(args, dict):
            raise HTTPException(400, "args は JSON オブジェクトです")
        provider = reg.provider()
        allowed = {n for n in (provider.list, provider.get) if n}
        if tool not in allowed:
            raise HTTPException(
                403,
                f"道具 {tool} はこの経路から呼べません (呼べるのは取り込み元の一覧と取得だけです)",
            )
        role = "get" if tool == provider.get else "list"
        extra = set(args) - CALL_ARGS_ALLOWED[role]
        if extra:
            raise HTTPException(400, f"引数 {', '.join(sorted(extra))} はこの経路から渡せません")
        server = reg.enabled_server_or_raise(name)
        timeout = reg.client.get_timeout_s if tool == provider.get else None
        result = await _call_or_http_error(reg.client, server, tool, args, timeout_s=timeout)
        return {"server": name, "tool": tool, "is_error": result.is_error, "result": result.data}

    @app.post("/api/mcp-servers/{name}/source-items", dependencies=write_auth)
    async def mcp_servers_source_items(name: str, request: Request) -> dict[str, Any]:
        """取り込み元のファイル一覧（📎 の 2 択が使う）。``exts`` はドット付き小文字。"""
        reg = _registry_or_404()
        _reject_if_content_length_exceeds(request, MAX_CALL_BODY_BYTES)
        try:
            body = await request.json()
        except ValueError as exc:
            raise HTTPException(400, "body must be JSON") from exc
        exts_raw = body.get("exts") if isinstance(body, dict) else None
        if not isinstance(exts_raw, list) or not all(isinstance(e, str) for e in exts_raw):
            raise HTTPException(400, 'exts は文字列の配列です (例: [".csv", ".xlsx"])')
        try:
            items = await reg.list_source_items(name, exts_raw)
        except McpToolError as exc:
            raise HTTPException(502, str(exc)) from exc
        return {"server": name, "items": items}

    return registry
