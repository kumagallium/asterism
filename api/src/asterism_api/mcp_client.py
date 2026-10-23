"""汎用の MCP クライアント（呼ぶ側）——ADR ``source-switch-mcp-client.md``。

Asterism はこれまで MCP を**出す**だけだった（``mcp_mount.py``）。ここは逆向きで、
利用者が設定画面に登録した MCP サーバ（stdio／streamable HTTP）の道具を列挙し、
呼び出す。最初の用途は 📎 の「取り込み元」（別アプリが持つ表のファイルを一覧し、
その bytes を設計の列に流す）。

このモジュールが引き受けること:

* 設定（``appdata/settings.json`` の ``mcpServers``）を読んで **transport を組み立てる**。
  組み立てだけを単体で検査できるよう、接続とは分けてある。
* 接続はリクエストごとに**開いて閉じる**。stdio は ``keep_alive=False`` で、
  呼び出しのたびに子プロセスを起こし、終わったら必ず止める（残さない）。
* 列挙・呼び出しに**タイムアウト**を付ける（一覧は短く、bytes を運ぶ取得は長く）。
* 「取り込み元プロトコル」（§5）の返事を読む: 3 道具とも **JSON 文字列を
  ``content[0].text`` に 1 本**返し、失敗は ``isError`` と ``{error}``。JSON として
  読めない本文は失敗として扱う。

このモジュールが引き受けないこと（信頼境界は ``mcp_client_routes.py``）:

* どのサーバに繋いでよいか（有効化の有無・単一ユーザーか）の判断。
* どの道具を HTTP から呼ばせるか（allowlist）。
* ``settings.json`` を書くこと（書き手は UI の ``putAppSetting`` だけ）。

不変条件: 出す側（``mcp_mount.py``・``asterism_mcp``）からこのモジュールを import
しない。Graphium 固有の名前を置かない（道具名は設定の ``sourceProvider`` から来る）。
``fastmcp`` は関数の中で遅延 import する（api の起動を fastmcp に依存させない）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("asterism.mcp_client")

# ---- 設定の形 -----------------------------------------------------------------

#: settings.json の最上位キー。書くのは UI だけ（``ui/src/settings/mcpServersApi.ts``）。
SETTINGS_KEY_SERVERS = "mcpServers"
SETTINGS_KEY_SOURCE_HOME = "sourceHome"
SETTINGS_KEY_SOURCE_PROVIDER = "sourceProvider"
SETTINGS_KEY_SEND_ORIGINALS = "sendOriginalsToHome"

#: サーバ名。設定のキーであり URL のパスにも入るので、英数字と ``_ . -`` だけ。
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
#: 環境変数の名前。
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
#: ``appdata._SECRET_KEY_MARKERS`` と同じ語。設定に書くと api 側で黙って落ちるので、
#: 組み立ての段で**警告**にして画面に出す（落とすのは appdata の仕事のまま）。
_SECRET_MARKERS = ("apikey", "api_key", "token", "secret", "password", "passwd", "credential")

#: 一覧（``list_tools``・取り込み元の一覧）に使う既定の秒数。
DEFAULT_LIST_TIMEOUT_S = 20.0
#: 1 件の取得（最大 32 MiB の bytes を運ぶ）に使う既定の秒数。
DEFAULT_GET_TIMEOUT_S = 180.0
#: 接続（stdio の起動・initialize）の既定の秒数。
DEFAULT_CONNECT_TIMEOUT_S = 20.0

#: 取り込み元プロトコルの ``get`` が返してよい bytes の上限（§5）。
MAX_SOURCE_FILE_BYTES = 32 * 1024 * 1024

#: 直近の呼び出しを画面に出す件数。
RECENT_CALLS_MAX = 20


class McpConfigError(ValueError):
    """設定が読めない（形が違う・禁止の項目がある）。"""


class McpConnectError(RuntimeError):
    """サーバに繋げなかった（起動失敗・URL に届かない・initialize が返らない）。"""


class McpTimeout(TimeoutError):
    """列挙または呼び出しが時間内に返らなかった。"""


class McpToolError(RuntimeError):
    """道具が失敗を返した（``isError``）、または返事がプロトコルの形でない。"""

    def __init__(self, message: str, *, text: str | None = None) -> None:
        super().__init__(message)
        self.text = text


@dataclass(frozen=True)
class McpServerConfig:
    """設定 1 件の正規化した形。``transport`` は ``"stdio"`` か ``"http"``。"""

    name: str
    transport: str
    enabled: bool = False
    command: str | None = None
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    url: str | None = None

    def command_line(self) -> str:
        """画面に出す「コマンド全文」。stdio だけ（http は URL）。"""
        if self.transport != "stdio" or not self.command:
            return ""
        return " ".join([self.command, *self.args])

    def public(self) -> dict[str, Any]:
        """画面と API に返す形（``env`` は名前と値をそのまま。秘密は設定に入れない前提）。"""
        out: dict[str, Any] = {
            "name": self.name,
            "transport": self.transport,
            "enabled": self.enabled,
        }
        if self.transport == "stdio":
            out["command"] = self.command
            out["args"] = list(self.args)
            out["env"] = dict(self.env)
            if self.cwd:
                out["cwd"] = self.cwd
        else:
            out["url"] = self.url
        return out


@dataclass(frozen=True)
class SourceProvider:
    """取り込み元プロトコルの道具名（§5）。既定は空＝取り込み元は使えない。"""

    list: str = ""
    get: str = ""
    add: str = ""


def _looks_secret(key: str) -> bool:
    lowered = key.lower()
    return lowered == "key" or any(m in lowered for m in _SECRET_MARKERS)


def parse_server(name: str, raw: Any) -> tuple[McpServerConfig, list[str]]:
    """設定の 1 件を読む。返りは ``(config, warnings)``。

    形が違えば :class:`McpConfigError`。``headers``／``auth`` は**拒否**する
    （``_strip_secrets`` は ``Authorization`` を落とさないので平文で残ってしまう。
    HTTP の認証は v1 の範囲外）。秘密に見える ``env`` の名前は警告にする
    （api の ``_strip_secrets`` が保存時に黙って落とすので、そのまま使うと起動時に
    その変数が無い）。
    """
    if not _NAME_RE.match(name or ""):
        raise McpConfigError(f"サーバ名 {name!r} は使えません (英数字と _ . - だけ・64 文字まで)")
    if _looks_secret(name):
        raise McpConfigError(f"サーバ名 {name!r} は秘密の名前に見えるため保存できません")
    if not isinstance(raw, dict):
        raise McpConfigError(f"{name}: 設定は JSON オブジェクトで書きます")
    for forbidden in ("headers", "auth"):
        if forbidden in raw:
            raise McpConfigError(
                f"{name}: {forbidden} は保存できません (認証つきの HTTP サーバは未対応です)"
            )
    transport = raw.get("transport")
    if transport not in ("stdio", "http"):
        raise McpConfigError(f'{name}: transport は "stdio" か "http" です')
    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise McpConfigError(f"{name}: enabled は true か false です")

    warnings: list[str] = []
    if transport == "stdio":
        command = raw.get("command")
        if not isinstance(command, str) or not command.strip():
            raise McpConfigError(f"{name}: command (起動するプログラム) が要ります")
        args_raw = raw.get("args", [])
        if not isinstance(args_raw, list) or not all(isinstance(a, str) for a in args_raw):
            raise McpConfigError(f"{name}: args は文字列の配列です")
        env_raw = raw.get("env", {}) or {}
        if not isinstance(env_raw, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in env_raw.items()
        ):
            raise McpConfigError(f"{name}: env は {{名前: 値}} の文字列です")
        for k in env_raw:
            if not _ENV_NAME_RE.match(k):
                raise McpConfigError(f"{name}: 環境変数の名前 {k!r} は使えません")
            if _looks_secret(k):
                warnings.append(
                    f"環境変数 {k} は秘密の名前に見えるため保存されません"
                    " (設定に秘密は入れられません)"
                )
        cwd = raw.get("cwd")
        if cwd is not None and (not isinstance(cwd, str) or not cwd.strip()):
            raise McpConfigError(f"{name}: cwd は文字列です")
        return (
            McpServerConfig(
                name=name,
                transport="stdio",
                enabled=enabled,
                command=command.strip(),
                args=tuple(args_raw),
                env=dict(env_raw),
                cwd=cwd.strip() if isinstance(cwd, str) else None,
            ),
            warnings,
        )

    url = raw.get("url")
    if not isinstance(url, str) or not re.match(r"^https?://", url.strip()):
        raise McpConfigError(f"{name}: url は http:// か https:// で始めます")
    return McpServerConfig(name=name, transport="http", enabled=enabled, url=url.strip()), warnings


def parse_servers(settings: dict[str, Any]) -> tuple[dict[str, McpServerConfig], dict[str, str]]:
    """settings.json 全体から ``mcpServers`` を読む。

    返りは ``(読めた物 {name: config}, 読めなかった物 {name: 理由})``。1 件が壊れていても
    他は使えるようにする（UI は壊れた 1 件を理由つきで見せる）。
    """
    raw = settings.get(SETTINGS_KEY_SERVERS)
    ok: dict[str, McpServerConfig] = {}
    bad: dict[str, str] = {}
    if not isinstance(raw, dict):
        return ok, bad
    for name, entry in raw.items():
        try:
            cfg, _warnings = parse_server(str(name), entry)
        except McpConfigError as exc:
            bad[str(name)] = str(exc)
            continue
        ok[cfg.name] = cfg
    return ok, bad


def parse_source_provider(settings: dict[str, Any]) -> SourceProvider:
    raw = settings.get(SETTINGS_KEY_SOURCE_PROVIDER)
    if not isinstance(raw, dict):
        return SourceProvider()

    def _s(key: str) -> str:
        v = raw.get(key)
        return v.strip() if isinstance(v, str) else ""

    return SourceProvider(list=_s("list"), get=_s("get"), add=_s("add"))


def parse_source_home(settings: dict[str, Any]) -> str | None:
    """取り込み元の家（サーバ名）。未設定なら None。"""
    raw = settings.get(SETTINGS_KEY_SOURCE_HOME)
    if isinstance(raw, dict):
        server = raw.get("server")
        if isinstance(server, str) and server.strip():
            return server.strip()
    return None


def send_originals_enabled(settings: dict[str, Any]) -> bool:
    """✓ の後にローカル原本を家へ送るか。**既定オン**（利用者の裁定 2026-10-23）。"""
    raw = settings.get(SETTINGS_KEY_SEND_ORIGINALS)
    return raw if isinstance(raw, bool) else True


# ---- transport の組み立て -------------------------------------------------------


def build_transport(cfg: McpServerConfig) -> Any:
    """設定 1 件から fastmcp の transport を作る。接続はしない。

    stdio は ``keep_alive=False``: fastmcp 既定の True だと最初の接続で起こした子が
    プロセスの寿命まで残る。取り込み元は時々しか呼ばないので、呼ぶたびに起こして
    閉じるほうが「子プロセスが残らない」を保てる。
    """
    from fastmcp.client.transports import StdioTransport, StreamableHttpTransport

    if cfg.transport == "stdio":
        assert cfg.command is not None
        return StdioTransport(
            command=cfg.command,
            args=list(cfg.args),
            env=dict(cfg.env) or None,
            cwd=cfg.cwd,
            keep_alive=False,
        )
    assert cfg.url is not None
    return StreamableHttpTransport(url=cfg.url)


# ---- 呼び出し -------------------------------------------------------------------


@dataclass(frozen=True)
class ToolInfo:
    name: str
    description: str
    input_schema: dict[str, Any]

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


@dataclass(frozen=True)
class ToolResult:
    """道具 1 回の返事。``text`` は ``content[0].text``、``data`` はそれを JSON として読んだ物。"""

    is_error: bool
    text: str
    data: Any


@dataclass
class RecentCall:
    """画面の「直近の呼び出し」1 行。中身（bytes・一覧の本文）は持たない。"""

    server: str
    tool: str
    ok: bool
    count: int | None
    at: float
    error: str | None = None

    def public(self) -> dict[str, Any]:
        return {
            "server": self.server,
            "tool": self.tool,
            "ok": self.ok,
            "count": self.count,
            "at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self.at)),
            "error": self.error,
        }


def _attr(obj: Any, new: str, old: str) -> Any:
    """mcp SDK v2 は ``isError``→``is_error``・``inputSchema``→``input_schema`` に改名した
    (旧名は非推奨の別名) 。fastmcp>=2.0 の幅で動くよう両方を見る。"""
    if hasattr(obj, new):
        return getattr(obj, new)
    return getattr(obj, old, None)


def _first_text(content: Any) -> str:
    if not content:
        return ""
    first = content[0]
    text = getattr(first, "text", None)
    return text if isinstance(text, str) else ""


class McpClient:
    """設定から transport を組み立てて、列挙・呼び出しを 1 回ずつ行う。

    ``transport_factory`` を差し替えると、テストはインプロセスの偽サーバ
    （``FastMCPTransport``）で同じ経路を通せる。
    """

    def __init__(
        self,
        transport_factory: Callable[[McpServerConfig], Any] = build_transport,
        *,
        connect_timeout_s: float = DEFAULT_CONNECT_TIMEOUT_S,
        list_timeout_s: float = DEFAULT_LIST_TIMEOUT_S,
        get_timeout_s: float = DEFAULT_GET_TIMEOUT_S,
    ) -> None:
        self._factory = transport_factory
        self.connect_timeout_s = connect_timeout_s
        self.list_timeout_s = list_timeout_s
        self.get_timeout_s = get_timeout_s
        self.recent: deque[RecentCall] = deque(maxlen=RECENT_CALLS_MAX)

    def _client(self, cfg: McpServerConfig, timeout_s: float) -> Any:
        from fastmcp import Client

        transport = self._factory(cfg)
        return Client(transport, timeout=timeout_s, init_timeout=self.connect_timeout_s)

    async def list_tools(self, cfg: McpServerConfig) -> list[ToolInfo]:
        """サーバの道具を列挙する。繋げなければ :class:`McpConnectError`。"""
        try:
            async with self._client(cfg, self.list_timeout_s) as client:
                tools = await asyncio.wait_for(client.list_tools(), self.list_timeout_s)
        except TimeoutError as exc:
            self._remember(cfg.name, "(tools)", ok=False, error="timeout")
            raise McpTimeout(
                f"{cfg.name}: 道具の一覧が {self.list_timeout_s:.0f} 秒で返りませんでした"
            ) from exc
        except Exception as exc:  # 起動失敗・接続拒否・initialize 失敗など transport 由来
            if _is_timeout(exc):
                self._remember(cfg.name, "(tools)", ok=False, error="timeout")
                raise McpTimeout(
                    f"{cfg.name}: 道具の一覧が {self.list_timeout_s:.0f} 秒で返りませんでした"
                ) from exc
            self._remember(cfg.name, "(tools)", ok=False, error=_short(exc))
            raise McpConnectError(f"{cfg.name}: 繋げませんでした ({_short(exc)})") from exc
        out = [
            ToolInfo(
                name=str(t.name),
                description=str(getattr(t, "description", "") or ""),
                input_schema=dict(_attr(t, "input_schema", "inputSchema") or {}),
            )
            for t in tools
        ]
        self._remember(cfg.name, "(tools)", ok=True, count=len(out))
        return out

    async def call_tool(
        self,
        cfg: McpServerConfig,
        tool: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout_s: float | None = None,
    ) -> ToolResult:
        """道具を 1 回呼ぶ。

        * 繋げない → :class:`McpConnectError`
        * 時間内に返らない → :class:`McpTimeout`
        * 道具が ``isError`` を返した、または本文が JSON でない → :class:`McpToolError`
          （プロトコル §5: 3 道具とも JSON 文字列を 1 本返す。それ以外は失敗）
        """
        limit = timeout_s if timeout_s is not None else self.list_timeout_s
        try:
            async with self._client(cfg, limit) as client:
                result = await asyncio.wait_for(
                    client.call_tool_mcp(tool, dict(arguments or {})), limit
                )
        except TimeoutError as exc:
            self._remember(cfg.name, tool, ok=False, error="timeout")
            raise McpTimeout(f"{cfg.name}/{tool}: {limit:.0f} 秒で返りませんでした") from exc
        except Exception as exc:
            if _is_timeout(exc):
                self._remember(cfg.name, tool, ok=False, error="timeout")
                raise McpTimeout(f"{cfg.name}/{tool}: {limit:.0f} 秒で返りませんでした") from exc
            self._remember(cfg.name, tool, ok=False, error=_short(exc))
            raise McpConnectError(f"{cfg.name}: 繋げませんでした ({_short(exc)})") from exc

        is_error = bool(_attr(result, "is_error", "isError"))
        text = _first_text(getattr(result, "content", None))
        data: Any = None
        try:
            data = json.loads(text) if text else None
        except ValueError:
            data = None
        if is_error:
            message = ""
            if isinstance(data, dict) and isinstance(data.get("error"), str):
                message = data["error"]
            self._remember(cfg.name, tool, ok=False, error=message or text[:120] or "error")
            raise McpToolError(
                f"{cfg.name}/{tool}: {message or text[:200] or '失敗しました'}", text=text
            )
        if data is None:
            self._remember(cfg.name, tool, ok=False, error="not JSON")
            raise McpToolError(f"{cfg.name}/{tool}: 返事が JSON ではありませんでした", text=text)
        items = data.get("items") if isinstance(data, dict) else None
        count = len(items) if isinstance(items, list) else None
        self._remember(cfg.name, tool, ok=True, count=count)
        return ToolResult(is_error=False, text=text, data=data)

    def _remember(
        self,
        server: str,
        tool: str,
        *,
        ok: bool,
        count: int | None = None,
        error: str | None = None,
    ) -> None:
        self.recent.appendleft(
            RecentCall(server=server, tool=tool, ok=ok, count=count, at=time.time(), error=error)
        )


def _innermost(exc: BaseException) -> BaseException:
    inner: BaseException = exc
    while isinstance(inner, BaseExceptionGroup) and inner.exceptions:
        inner = inner.exceptions[0]
    return inner


def _short(exc: BaseException) -> str:
    """例外を画面に出せる 1 行に（ExceptionGroup は中身の最初の 1 件）。"""
    inner = _innermost(exc)
    text = f"{type(inner).__name__}: {inner}".strip()
    return text[:200]


def _is_timeout(exc: BaseException) -> bool:
    """fastmcp は自分の ``timeout`` で先に切り、``McpError('... timed out')`` を投げる。
    ``asyncio.wait_for`` の TimeoutError と合わせて、どちらも「時間切れ」として扱う。"""
    inner = _innermost(exc)
    if isinstance(inner, TimeoutError):
        return True
    return "timed out" in str(inner).lower()


# ---- 取り込み元プロトコルの返事を読む（§5） ------------------------------------------


@dataclass(frozen=True)
class SourceItem:
    id: str
    name: str
    mime_type: str
    size: int | None = None
    uploaded_at: str | None = None
    folder: str | None = None

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "mimeType": self.mime_type,
            "size": self.size,
            "uploadedAt": self.uploaded_at,
            "folder": self.folder,
        }


def parse_source_items(data: Any) -> list[SourceItem]:
    """``list`` の返り ``{items: [{id, name, mimeType, size?, uploadedAt?, folder?}]}`` を読む。

    Asterism が読むのはこの項目だけ。``id``／``name`` の無い行は飛ばす（壊れた 1 行で
    一覧全体を失敗にしない）。形そのものが違えば :class:`McpToolError`。
    """
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise McpToolError("取り込み元の一覧の形が違います ({items: [...]} が要ります)")
    out: list[SourceItem] = []
    for row in data["items"]:
        if not isinstance(row, dict):
            continue
        rid = row.get("id")
        name = row.get("name")
        if not isinstance(rid, str) or not rid or not isinstance(name, str) or not name:
            continue
        size = row.get("size")
        out.append(
            SourceItem(
                id=rid,
                name=name,
                mime_type=str(row.get("mimeType") or "application/octet-stream"),
                size=int(size) if isinstance(size, int) and not isinstance(size, bool) else None,
                uploaded_at=(
                    str(row["uploadedAt"]) if isinstance(row.get("uploadedAt"), str) else None
                ),
                folder=str(row["folder"]) if isinstance(row.get("folder"), str) else None,
            )
        )
    return out


@dataclass(frozen=True)
class SourceFile:
    id: str
    name: str
    mime_type: str
    size: int
    data: bytes


def parse_source_file(data: Any) -> SourceFile:
    """``get`` の返り ``{id, name, mimeType, size, bytes_base64?}`` を読み、bytes に戻す。

    ``bytes_base64`` が無い（``inline`` を頼んだのに本文が無い＝32 MiB 超など）ときと、
    ``size`` が上限を超えるときは :class:`McpToolError`。
    """
    import base64

    if not isinstance(data, dict):
        raise McpToolError("取り込み元のファイルの形が違います")
    rid = data.get("id")
    name = data.get("name")
    if not isinstance(rid, str) or not isinstance(name, str) or not name:
        raise McpToolError("取り込み元のファイルに id か name がありません")
    size = data.get("size")
    if isinstance(size, int) and size > MAX_SOURCE_FILE_BYTES:
        raise McpToolError(
            f"{name} は大きすぎて取れません ({size} bytes・上限 {MAX_SOURCE_FILE_BYTES})"
        )
    b64 = data.get("bytes_base64")
    if not isinstance(b64, str) or not b64:
        raise McpToolError(
            f"{name} の本文が返りませんでした (大きすぎるか、inline に対応していません)"
        )
    try:
        raw = base64.b64decode(b64, validate=True)
    except ValueError as exc:
        raise McpToolError(f"{name} の本文 (base64) が読めません") from exc
    if len(raw) > MAX_SOURCE_FILE_BYTES:
        raise McpToolError(f"{name} は大きすぎて取れません ({len(raw)} bytes)")
    return SourceFile(
        id=rid,
        name=name,
        mime_type=str(data.get("mimeType") or "application/octet-stream"),
        size=len(raw),
        data=raw,
    )
