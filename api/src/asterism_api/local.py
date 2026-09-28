"""Local mode — one-command, loopback-only Asterism for a single machine.

``asterism-local`` turns the hosted stack into a local-first run (ADR
``docs/architecture/local-first-distribution.md``):

* user-data directory defaults (macOS / Linux / Windows) instead of the
  compose ``/data/sources`` tree,
* a per-install write token minted once and injected server-side for
  loopback clients — the in-process equivalent of production caddy's
  ``header_up X-Asterism-Token {$ASTERISM_API_TOKEN}``, so the browser
  never sees or pastes a token,
* the built SPA (``ui/dist``) served same-origin, replicating the caddy
  split: ``/assets/*`` misses stay real 404s (the ``vite:preloadError``
  stale-chunk self-heal depends on that), everything else falls back to
  ``index.html`` with no-cache,
* an Oxigraph child process on a free loopback port (no Docker),
* a browser tab opened once the server is up.

``main.py`` is deliberately untouched: everything here decorates the app
``build_app`` returns. ``asterism_api.main`` must be imported only AFTER
the env defaults are applied — it reads ``ASTERISM_MAX_UPLOAD_BYTES`` at
module import time.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import ipaddress
import logging
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from asterism import subjects as subjects_mod
from asterism import substrate
from asterism.oxigraph_client import OxigraphClient, OxigraphConfig
from fastapi import APIRouter, Request
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from asterism_api import appdata, exchange, registry
from asterism_api.mcp_mount import MCP_PATH, attach_mcp

if TYPE_CHECKING:
    from fastapi import FastAPI

    from asterism_api.main import Settings

logger = logging.getLogger("asterism.local")

_DEFAULT_PORT = 8080

_NO_OXIGRAPH_MSG = (
    "oxigraph binary not found. Local mode runs Oxigraph (a single static binary) "
    "as a child process.\n"
    "  install:  brew install oxigraph            (macOS)\n"
    "            cargo install oxigraph-cli       (any platform)\n"
    "            https://github.com/oxigraph/oxigraph/releases\n"
    "  or point at a running server:  asterism-local --oxigraph-url "
    "http://127.0.0.1:7878\n"
    "  (ASTERISM_OXIGRAPH_BIN overrides binary discovery)"
)


# ---------------------------------------------------------------------------
# user-data directory + write token


def legacy_data_home() -> Path:
    """The pre-``~/Documents/Asterism`` OS-conventional data directory.

    Existing installs keep using this location (see ``default_data_home``);
    it is only a fresh-install default that moved.
    """
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Asterism"
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        return (Path(appdata) if appdata else Path.home()) / "Asterism"
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / "asterism"


def _has_existing_data(home: Path) -> bool:
    """True if ``home`` exists and is non-empty (used, not just created)."""
    try:
        return home.is_dir() and any(home.iterdir())
    except OSError:
        return False


def default_data_home() -> Path:
    """Default per-user data directory (override: ``--data-dir``).

    Fresh installs default to ``~/Documents/Asterism`` — a visible,
    Finder-browsable location shared with the sister app Graphium
    (``~/Documents/Graphium``). Installs that already have data under the
    legacy OS-conventional location (macOS ``~/Library/Application
    Support/Asterism``, Windows ``%APPDATA%/Asterism``) keep using it
    unchanged; nothing is moved or copied.
    """
    legacy = legacy_data_home()
    if _has_existing_data(legacy):
        logger.info("data home: using existing legacy location %s", legacy)
        return legacy
    try:
        documents = Path.home() / "Documents" / "Asterism"
    except RuntimeError:
        # Path.home() can raise if $HOME is unresolvable.
        logger.info(
            "data home: could not resolve ~/Documents, falling back to %s",
            legacy,
        )
        return legacy
    logger.info("data home: using %s", documents)
    return documents


def ensure_write_token(home: Path) -> str:
    """Read or mint the per-install write token (0600, ``<home>/write_token``).

    The token gates the same routes as in the hosted deployment
    (``require_write_auth`` is fail-closed without one); local mode mints it so
    zero-config startup still has working writes.
    """
    token_file = home / "write_token"
    if token_file.is_file():
        existing = token_file.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    token = secrets.token_urlsafe(32)
    home.mkdir(parents=True, exist_ok=True)
    fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(token + "\n")
    return token


def local_env(
    home: Path, oxigraph_url: str, token: str, *, mcp_url: str | None = None
) -> dict[str, str]:
    """Env defaults for a local run, applied with ``setdefault`` (user wins).

    ``ASTERISM_EXPOSE_RAW_SPARQL=1``: the single-user loopback box is the
    "co-located / open store" end of the exposure profile (ADR
    store-mcp-split.md) — the operator owns the store, and the SPA's SPARQL
    view is part of the product. ``ASTERISM_ALLOW_PRIVATE_LLM_BASE=1``: a
    local Ollama / LM Studio base URL must be listable in the model picker.
    """
    sources = home / "sources"
    return {
        "CSV2RDF_DROP_ROOT": str(sources / "csv"),
        "CSV2RDF_RDF_ROOT": str(sources / "rdf"),
        "CSV2RDF_ERROR_ROOT": str(sources / "errors"),
        "CSV2RDF_JOBS_LOG": str(sources / "jobs.jsonl"),
        "CSV2RDF_REGISTRY_ROOT": str(sources / "registry"),
        "CSV2RDF_OXIGRAPH_URL": oxigraph_url,
        "ASTERISM_API_TOKEN": token,
        "ASTERISM_EXPOSE_RAW_SPARQL": "1",
        "ASTERISM_ALLOW_PRIVATE_LLM_BASE": "1",
        # Single-user mode (ADR app-data-on-disk.md): Ask chat history
        # and app settings get a server-side home under the data dir instead
        # of the browser's localStorage. Unset in the shared/hosted api, so
        # the appdata routes there stay 404 and behaviour is unchanged.
        "ASTERISM_SINGLE_USER": "1",
        "ASTERISM_APPDATA_ROOT": str(home / "appdata"),
        # The MCP endpoint to register in an external AI client (ADR
        # mcp-endpoint-on-the-app.md). Built from the port that was actually
        # bound, not from the 8765 preference: the settings screen shows this
        # string verbatim, and a confident wrong URL is worse than none.
        **({"ASTERISM_MCP_URL": mcp_url} if mcp_url else {}),
    }


# ---------------------------------------------------------------------------
# loopback token injection (caddy header_up equivalent)


class LoopbackTokenInjector:
    """Inject the write token server-side for loopback clients.

    Mirrors production caddy's ``header_up X-Asterism-Token`` *replace*
    semantics: client-sent ``Authorization`` / ``X-Asterism-Token`` headers are
    dropped first, because ``require_write_auth`` prefers ``Authorization`` —
    a stray Bearer header would otherwise shadow the injected token.

    Injection only happens when the ASGI ``client`` address is loopback.
    Local mode always binds 127.0.0.1, so this is defense in depth (and the
    reason local mode has no ``--host``: a wider bind would hand write access
    to any LAN peer).
    """

    _STRIP = (b"authorization", b"x-asterism-token")

    def __init__(self, app: ASGIApp, token: str) -> None:
        self._app = app
        self._token = token.encode("ascii")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket") and _is_loopback(scope.get("client")):
            headers = [
                (name, value)
                for name, value in scope["headers"]
                if name.lower() not in self._STRIP
            ]
            headers.append((b"x-asterism-token", self._token))
            scope = dict(scope, headers=headers)
        await self._app(scope, receive, send)


def _is_loopback(client: tuple[str, int] | None) -> bool:
    if client is None:
        return False
    try:
        return ipaddress.ip_address(client[0]).is_loopback
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# SPA serving (caddy split replicated)


class SpaStaticFiles(StaticFiles):
    """``ui/dist`` with the production caddy split replicated.

    * ``/assets/*`` misses stay REAL 404s — the SPA reloads once on
      ``vite:preloadError`` (``ui/src/main.tsx``) and that self-heal breaks if
      a missing chunk resolves to index.html (the 2026-07 stale-chunk
      incident, ``infra/caddy/Caddyfile``).
    * every other unmatched path serves ``index.html`` (the SPA routes by
      hash, so this is only reached for hard reloads and typos) with
      no-cache, so a redeployed page always fetches the current chunk map.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            response = await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404 or _is_asset_path(path):
                raise
            response = await super().get_response("index.html", scope)
        else:
            if response.status_code == 404 and not _is_asset_path(path):
                response = await super().get_response("index.html", scope)
        served = getattr(response, "path", None)
        if served is not None and str(served).endswith("index.html"):
            response.headers["Cache-Control"] = "no-cache"
        return response


def _is_asset_path(path: str) -> bool:
    return path == "assets" or path.startswith("assets/")


def find_ui_dist(explicit: str | None = None) -> Path | None:
    """Locate the built SPA: flag > ``ASTERISM_UI_DIST`` > repo ``ui/dist``."""
    env = (os.environ.get("ASTERISM_UI_DIST") or "").strip()
    if explicit or env:
        # An explicit location that is wrong must NOT silently fall back to the
        # repo default — the caller warns and runs API-only instead.
        chosen = Path(explicit or env).expanduser()
        return chosen if (chosen / "index.html").is_file() else None
    repo_dist = Path(__file__).resolve().parents[3] / "ui" / "dist"
    return repo_dist if (repo_dist / "index.html").is_file() else None


# ---------------------------------------------------------------------------
# oxigraph child process


def find_oxigraph_binary() -> str | None:
    override = (os.environ.get("ASTERISM_OXIGRAPH_BIN") or "").strip()
    if override:
        path = Path(override).expanduser()
        if path.is_file():
            return str(path)
        return shutil.which(override)
    return shutil.which("oxigraph")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def spawn_oxigraph(
    binary: str, store_dir: Path, log_path: Path, port: int
) -> subprocess.Popen[bytes]:
    """Start ``oxigraph serve`` on loopback (fixed argv, no shell)."""
    store_dir.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    argv = [
        binary,
        "serve",
        "--location",
        str(store_dir),
        "--bind",
        f"127.0.0.1:{port}",
    ]
    with open(log_path, "ab") as log_file:
        return subprocess.Popen(
            argv,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )


def wait_oxigraph_ready(
    url: str, process: subprocess.Popen[bytes], timeout_s: float = 20.0
) -> bool:
    """Poll the child with the same trivial ASK the api's /health uses."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            response = httpx.post(
                url + "/query",
                content="ASK { ?s ?p ?o }",
                headers={
                    "Content-Type": "application/sparql-query",
                    "Accept": "application/sparql-results+json",
                },
                timeout=2.0,
            )
            if response.status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    return False


def _terminate(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


# ---------------------------------------------------------------------------
# demo-agent (Ask) child + same-origin /demo relay

_DEMO_FORWARD_HEADERS = frozenset(
    {
        "accept",
        "content-type",
        "x-api-key",
        "x-llm-provider",
        "x-llm-model",
        "x-llm-api-base",
        "x-llm-max-tokens",
    }
)


def find_demo_agent_dir() -> Path | None:
    """``demo-agent/app.py`` — env override (bundled .app layout) first, then
    the repo-checkout location."""
    override = (os.environ.get("ASTERISM_DEMO_AGENT_DIR") or "").strip()
    if override:
        explicit = Path(override).expanduser()
        return explicit if (explicit / "app.py").is_file() else None
    candidate = Path(__file__).resolve().parents[3] / "demo-agent"
    return candidate if (candidate / "app.py").is_file() else None


def ask_dependencies_available() -> bool:
    """Real-mode Ask lazily imports ``asterism_mcp.tools`` (which pulls fastmcp).

    Neither is a base api dependency — the ``[local]`` extra installs them.
    """
    return all(
        importlib.util.find_spec(name) is not None
        for name in ("asterism_mcp", "fastmcp")
    )


def spawn_demo_agent(
    app_dir: Path, log_path: Path, port: int, env: dict[str, str]
) -> subprocess.Popen[bytes]:
    """Start demo-agent the way prod does (``uvicorn app:app``), loopback-only."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    argv = [
        sys.executable,
        "-m",
        "uvicorn",
        "app:app",
        "--app-dir",
        str(app_dir),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]
    with open(log_path, "ab") as log_file:
        return subprocess.Popen(
            argv,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=env,
        )


def wait_demo_agent_ready(
    url: str, process: subprocess.Popen[bytes], timeout_s: float = 30.0
) -> bool:
    """Poll the child's own ``/health`` (deliberately NOT relayed — the api owns
    ``/health``; the child's lives outside the ``/demo/*`` prefix)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            if httpx.get(url + "/health", timeout=2.0).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    return False


def create_demo_relay(
    demo_base_url: str, client: httpx.AsyncClient | None = None
) -> APIRouter:
    """Same-origin ``/demo/*`` relay — the in-process equivalent of prod caddy's
    ``reverse_proxy /demo/* demo-agent:8090`` (no prefix strip).

    The prod SPA is built with ``VITE_DEMO_AGENT_URL='/'`` and fetches
    ``/demo/*`` on its own origin; this router forwards those calls to the
    demo-agent child. Only the Ask contract headers are forwarded — notably
    NOT the injected ``X-Asterism-Token`` (the child must never receive the
    write token). ``/demo/ask`` may run several blocking LLM calls with no
    internal timeout, so the read timeout is unlimited.
    """
    http = client or httpx.AsyncClient(
        base_url=demo_base_url,
        timeout=httpx.Timeout(connect=5.0, read=None, write=30.0, pool=None),
    )
    router = APIRouter()

    @router.api_route("/demo/{path:path}", methods=["GET", "POST"])
    async def relay(path: str, request: Request) -> Response:
        headers = {
            name: value
            for name, value in request.headers.items()
            if name.lower() in _DEMO_FORWARD_HEADERS
        }
        body = await request.body()
        try:
            upstream = await http.request(
                request.method,
                f"/demo/{path}",
                params=request.url.query or None,
                content=body or None,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            return JSONResponse(
                {"error": f"demo-agent unreachable: {exc}"}, status_code=502
            )
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type"),
        )

    return router


# ---------------------------------------------------------------------------
# demo dataset seed（契約メモ contract_pr_e.md §2）
#
# 初回起動で見本データセット「世界の国」を自動で載せる。「本物の取り込み経路」の
# 最後の 2 段（snapshot import → promote）を、main.py の HTTP ルートを経由せず、
# そのルートが呼ぶのと同じ内部関数を直接呼んで行う。

_DEMO_SEED_MARKER = "demo-seeded"
_DEMO_SNAPSHOT_ENV = "ASTERISM_DEMO_SNAPSHOT"
_DEMO_DATASET_ENV = "ASTERISM_DEMO_DATASET"
# api/src/asterism_api/cards_routes.py の _SUBJECTS_NAMESPACE と同じ文字列
# （appdata 上のディレクトリ名）。cards_routes.py は担当外なので値だけ揃える。
_DEMO_SUBJECTS_NAMESPACE = "subjects"

# 見本データ（datasets/world/、契約メモ §1）の中身に依存する値。分野固有名詞では
# なく見本データそのものの中身なので、コードに書いてよい（契約メモ §0）。
_DEMO_JAPAN_NAME = "Japan"  # world.csv の schema:name（英語国名）
_DEMO_JAPAN_LABEL_JA = "日本"
_DEMO_COUNTRY_LABEL_JA = "国"
_DEMO_REGION_JA_VALUE = "東アジア・太平洋"
_DEMO_SET_LABEL_JA = "東アジア・太平洋の国"
_DEMO_SET_LIMIT = 20

_SCHEMA_NAME_PREDICATES = ("http://schema.org/name", "https://schema.org/name")


def _bundled_world_snapshot_candidates() -> list[Path]:
    """``.app`` 同梱時、見本 snapshot がこのインタプリタの近くに来る場所。

    ``mcp/src/asterism_mcp/agent_cli.py`` の ``_bundled_oxigraph_candidates`` と
    同じ考え方: ``sys.executable`` の祖先ディレクトリを一定数だけ辿って探す —
    バンドルの正確な深さをハードコードしない。
    """
    exe = Path(sys.executable).resolve()
    return [
        ancestor / "datasets" / "world" / "snapshot.tar"
        for ancestor in list(exe.parents)[:6]
    ]


def find_world_snapshot() -> Path | None:
    """見本 snapshot の在り処: repo チェックアウト → 同梱 ``.app`` → 環境変数
    ``ASTERISM_DEMO_SNAPSHOT``（契約メモ §2 の 3 候補、この順）。"""
    repo_relative = (
        Path(__file__).resolve().parents[3] / "datasets" / "world" / "snapshot.tar"
    )
    if repo_relative.is_file():
        return repo_relative
    for candidate in _bundled_world_snapshot_candidates():
        if candidate.is_file():
            return candidate
    override = (os.environ.get(_DEMO_SNAPSHOT_ENV) or "").strip()
    if override:
        path = Path(override).expanduser()
        if path.is_file():
            return path
    return None


async def _find_demo_japan_subject(
    client: Any, graph_iri: str
) -> tuple[str, str, str] | None:
    """見本の 2 主語を組み立てるのに要る 3 つ組
    ``(japan_iri, country_class_iri, region_property_iri)`` を、1 本の SPARQL で
    引く。IRI を直書きしない（契約メモ §2）— 日本の IRI・国クラスの IRI・
    「地域（日本語）」述語の IRI は、すべてこの関数がストアに聞いて答える。
    見つからなければ ``None``。
    """
    names = " ".join(f"<{p}>" for p in _SCHEMA_NAME_PREDICATES)
    query = (
        f"SELECT ?japan ?class ?regionProp WHERE {{ "
        f"GRAPH <{graph_iri}> {{ "
        f"VALUES ?nameProp {{ {names} }} "
        f'?japan ?nameProp "{_DEMO_JAPAN_NAME}" . '
        f"?japan a ?class . "
        f'?japan ?regionProp "{_DEMO_REGION_JA_VALUE}" . '
        f"}} }} LIMIT 1"
    )
    result = await client.sparql_select(query)
    bindings = (
        result.get("results", {}).get("bindings", [])
        if isinstance(result, dict)
        else []
    )
    if not bindings:
        return None
    row = bindings[0]
    try:
        return (row["japan"]["value"], row["class"]["value"], row["regionProp"]["value"])
    except (KeyError, TypeError):
        return None


def _demo_subject_items(
    japan_iri: str, country_class_iri: str, region_property_iri: str
) -> list[dict[str, Any]]:
    """見本の 2 主語（``ui/src/cards/cardsApi.ts`` の ``SubjectItem`` の形）を
    組み立てる。``thread_id`` は appdata のファイル名（uuid4）— ``SubjectItem.id``
    （IRI/set_id）とは別に持つ規律（契約メモ §5.4）。
    """
    now = datetime.now(UTC).isoformat()
    individual = {
        "kind": "individual",
        "id": japan_iri,
        "label": _DEMO_JAPAN_LABEL_JA,
        "class_label": _DEMO_COUNTRY_LABEL_JA,
        "source": "open",
        "card_count": None,
        "match": None,
        "subject_key": subjects_mod.subject_key_string(
            {"kind": "individual", "iri": japan_iri}
        ),
        "created_at": now,
        "thread_id": str(uuid.uuid4()),
    }
    spec = subjects_mod.normalize_set_spec(
        {
            "class": country_class_iri,
            "where": [
                {
                    "property": region_property_iri,
                    "op": "in",
                    "value": [_DEMO_REGION_JA_VALUE],
                }
            ],
            "order_by": None,
            "limit": _DEMO_SET_LIMIT,
            "source_scope": "open",
        }
    )
    set_id = subjects_mod.set_id_of(spec)
    region_set = {
        "kind": "set",
        "id": set_id,
        "label": _DEMO_SET_LABEL_JA,
        "class_label": _DEMO_COUNTRY_LABEL_JA,
        "source": "open",
        "card_count": None,
        "match": None,
        "subject_key": subjects_mod.subject_key_string(
            {"kind": "set", "set_id": set_id, "spec": spec}
        ),
        "spec": spec,
        "created_at": now,
        "thread_id": str(uuid.uuid4()),
    }
    return [individual, region_set]


async def _seed_demo_subjects(cfg: Settings, client: Any, graph_iri: str) -> None:
    """appdata の ``subjects`` に見本 2 件を書く（best-effort）。日本が見つから
    なければ 2 件とも書かない（契約メモ §2 の逃げ道 — IRI を直書きしない代わり
    に、引けなかったときは書かない）。
    """
    if cfg.appdata_root is None:
        return
    found = await _find_demo_japan_subject(client, graph_iri)
    if found is None:
        logger.warning(
            'seed_demo_dataset: schema:name "Japan" not found in the seeded '
            "graph — skipping the 2 starter subjects"
        )
        return
    for item in _demo_subject_items(*found):
        appdata.write_thread(
            cfg.appdata_root,
            item["thread_id"],
            item,
            namespace=_DEMO_SUBJECTS_NAMESPACE,
        )


async def seed_demo_dataset(home: Path, cfg: Settings, client: Any) -> None:
    """初回起動で見本データセット「世界の国」を自動で載せる（契約メモ §2）。

    ``asterism-local`` の起動後（oxigraph ready・app 構築後）、ブラウザを開く前に
    一度だけ呼ぶ（``main()``）。条件: 単一ユーザー ∧ registry が空 ∧
    ``home/demo-seeded`` マーカーが無い ∧ ``ASTERISM_DEMO_DATASET`` が ``"0"``
    でない。中身は ``datasets/world/snapshot.tar`` を ``exchange.import_snapshot``
    で取り込み、``main.py`` の ``POST /api/datasets/{id}/promote`` ルートが呼ぶ
    のと同じ内部関数（``registry.load_dataset``・``substrate.alignment_report``・
    ``substrate.promote_to_canonical``・``registry.mark_promoted``）を HTTP を
    経由せず直接呼んで公開する。全体 best-effort — どこで失敗しても
    ``log.warning`` するだけで起動は止めない。
    """
    if not cfg.single_user:
        return
    if (os.environ.get(_DEMO_DATASET_ENV) or "").strip() == "0":
        return
    marker = home / _DEMO_SEED_MARKER
    if marker.is_file():
        return
    if registry.list_datasets(cfg.registry_root):
        return

    snapshot_path = find_world_snapshot()
    if snapshot_path is None:
        logger.warning("seed_demo_dataset: no bundled world snapshot found — skipping")
        return

    try:
        payload = snapshot_path.read_bytes()
        # main.py の POST /api/datasets/import ルートと同じ内部関数（HTTP は
        # 叩かない）。
        from asterism_api.main import _MAX_UPLOAD_BYTES, _subjects_of_design

        imported = await exchange.import_snapshot(
            cfg, client, payload, max_extracted_bytes=_MAX_UPLOAD_BYTES
        )
        dataset_id = imported["dataset_id"]
        staged_iri = imported["staged_graph"]

        # main.py の POST /api/datasets/{id}/promote ルートが呼ぶのと同じ内部
        # 関数、同じ順番（HTTP は叩かない）。ontology/meta グラフ投影・クエリ
        # ツール合成・crosswalk 再構築・togomcp 配信は促進の副作用であり公開に
        # 必須ではないので呼ばない — クエリツールは見本の query_tools.yaml
        # （人が vet 済み、契約メモ §1）をそのまま使う。
        data = registry.load_dataset(cfg.registry_root, dataset_id)
        dataset_key = substrate.canonical_graph_iri(dataset_id)
        alignment = await substrate.alignment_report(client, staged_iri)
        await substrate.promote_to_canonical(client, dataset_key, staged_iri)
        triples_promoted = int((data or {}).get("meta", {}).get("triple_count") or 0)
        registry.mark_promoted(
            cfg.registry_root,
            dataset_id,
            triples_promoted=triples_promoted,
            alignment=alignment,
            promoted_at=datetime.now(UTC).isoformat(),
            canonical_graph=dataset_key,
            live_graph=staged_iri,
            published_subjects=_subjects_of_design((data or {}).get("artifacts", {})),
        )
    except Exception:
        logger.warning(
            "seed_demo_dataset: snapshot import/promote failed (continuing)",
            exc_info=True,
        )
        return

    try:
        await _seed_demo_subjects(cfg, client, staged_iri)
    except Exception:
        logger.warning(
            "seed_demo_dataset: writing the starter subjects failed (continuing)",
            exc_info=True,
        )

    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()


# ---------------------------------------------------------------------------
# app assembly + entrypoint


def build_local_app(
    *,
    token: str,
    ui_dist: Path | None,
    settings: Settings | None = None,
    oxigraph_client: OxigraphClient | None = None,
    start_watcher: bool = True,
    demo_agent_url: str | None = None,
    demo_relay_client: httpx.AsyncClient | None = None,
    mcp: bool = True,
) -> FastAPI:
    """``build_app`` + optional /demo relay + /mcp + SPA mount + token injection.

    Route order matters: ``build_app`` registers the api routes first, the
    ``/demo/*`` relay is included next, the MCP endpoint after that, and the
    static catch-all mount goes last — so ``/api/*``, ``/jobs``, ``/health``,
    ``/describe``, ``/upload/{kind}``, ``/demo/*`` and ``/mcp`` all win over
    the SPA fallback. (Before ``/mcp`` had a route of its own, that fallback
    answered it with the SPA's index.html — a 200 full of HTML, which is why
    the endpoint looked present and was not.)
    """
    from asterism_api.main import build_app

    app = build_app(
        settings, oxigraph_client=oxigraph_client, start_watcher=start_watcher
    )
    if demo_agent_url is not None:
        app.include_router(create_demo_relay(demo_agent_url, demo_relay_client))
    if mcp:
        attach_mcp(app)
    if ui_dist is not None:
        app.mount("/", SpaStaticFiles(directory=str(ui_dist), html=True), name="spa")
    app.add_middleware(LoopbackTokenInjector, token=token)
    return app


async def _open_when_ready(server: Any, url: str) -> None:
    while not server.started:
        await asyncio.sleep(0.1)
    webbrowser.open(url)


def _serve(app: FastAPI, *, port: int, log_level: str, open_url: str | None) -> None:
    import uvicorn

    # _Server is defined here, not at module scope, because it subclasses
    # uvicorn.Server and uvicorn is only imported lazily inside this function
    # — the rest of this module (CLI arg parsing, env setup) must stay
    # importable without pulling in uvicorn.
    class _Server(uvicorn.Server):
        """``uvicorn.Server`` without the terminal signal re-raise.

        Upstream's ``capture_signals`` (``uvicorn/server.py`` — private, no
        public hook exists for this) installs ``self.handle_exit`` for the
        duration of ``serve()``, then on the way out restores the *original*
        handlers (for SIGTERM that is the default, i.e. terminate-immediately)
        and re-raises the captured signal at itself with ``signal.raise_signal``.
        That re-raise exists so a process killed by SIGTERM also *reports*
        SIGTERM in its exit status — reasonable in isolation, but it kills the
        process before control ever returns to the ``await server.serve()``
        caller, which for local mode is ``main()``'s ``finally`` — the place
        that ``_terminate()``s the Oxigraph / demo-agent children. With the
        re-raise, that ``finally`` never runs and the children are orphaned
        (confirmed in production: v0.22.1, twice, both Oxigraph and
        demo-agent). This process's job is to not leave children behind, not
        to report an accurate signal-based exit status, so the re-raise is
        dropped. Handler installation/removal and ``handle_exit``'s graceful-
        shutdown semantics are copied from upstream unchanged; only a log
        line is added so a future incident shows *why* the process is going
        down instead of only uvicorn's own "Shutting down".
        """

        @contextlib.contextmanager
        def capture_signals(self) -> Iterator[None]:
            # Mirrors uvicorn.Server.capture_signals: signals can only be
            # listened to from the main thread.
            if threading.current_thread() is not threading.main_thread():
                yield
                return

            def _handle_and_log(sig: int, frame: Any) -> None:
                logger.warning(
                    "received %s (signal %d) — shutting down and stopping "
                    "children (pid %d, ppid %d)",
                    signal.Signals(sig).name,
                    sig,
                    os.getpid(),
                    os.getppid(),
                )
                self.handle_exit(sig, frame)

            # uvicorn.server.HANDLED_SIGNALS, not a local copy: this stays in
            # step with whatever signal set upstream decides to handle
            # (SIGINT/SIGTERM today, plus SIGBREAK on Windows).
            original_handlers = {
                sig: signal.signal(sig, _handle_and_log)
                for sig in uvicorn.server.HANDLED_SIGNALS
            }
            try:
                yield
            finally:
                for sig, handler in original_handlers.items():
                    signal.signal(sig, handler)
            # Deliberately no ``signal.raise_signal`` here — see the class
            # docstring above.

    # timeout_graceful_shutdown: SIGTERM must reach main()'s finally (which
    # terminates the oxigraph / demo-agent children) BEFORE a supervising shell
    # (the desktop app) escalates to SIGKILL — unbounded graceful shutdown can
    # linger on keep-alive connections and orphan the grandchildren. _Server
    # above is what makes that true: without it, uvicorn's own signal handling
    # re-kills the process on the way out and main()'s finally never runs.
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level=log_level,
        timeout_graceful_shutdown=5,
    )
    server = _Server(config)

    async def _run() -> None:
        opener = (
            asyncio.create_task(_open_when_ready(server, open_url))
            if open_url
            else None
        )
        try:
            await server.serve()
        finally:
            if opener is not None and not opener.done():
                opener.cancel()

    asyncio.run(_run())


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="asterism-local",
        description=(
            "Run Asterism locally: loopback-only api + SPA + Oxigraph child "
            "process, data under a per-user directory."
        ),
    )
    parser.add_argument("--port", type=int, default=_DEFAULT_PORT)
    parser.add_argument(
        "--data-dir",
        default=None,
        help="data home (default: ~/Documents/Asterism, or the legacy OS "
        "user-data dir if it already has data; env ASTERISM_LOCAL_HOME)",
    )
    parser.add_argument(
        "--oxigraph-url",
        default=None,
        help="use a running Oxigraph instead of spawning one "
        "(also honored: env CSV2RDF_OXIGRAPH_URL)",
    )
    parser.add_argument(
        "--ui-dist", default=None, help="path to the built SPA (default: repo ui/dist)"
    )
    parser.add_argument(
        "--demo-agent-url",
        default=None,
        help="relay /demo/* to an already-running demo-agent instead of "
        "spawning one",
    )
    parser.add_argument(
        "--no-ask",
        action="store_true",
        help="do not spawn the demo-agent child (Ask view degrades)",
    )
    parser.add_argument(
        "--no-mcp",
        action="store_true",
        help="do not serve the MCP endpoint at /mcp",
    )
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(message)s")
    if args.log_level.upper() != "DEBUG":
        # httpx logs one INFO line per request, and the Oxigraph readiness
        # probe (wait_oxigraph_ready) polls it every ~10s for as long as the
        # process runs. In a real backend.log this was 180,621 of 183,451
        # lines (98.5%) — pure liveness-check noise that buried the handful
        # of lines an actual investigation needed (see PR #430/#431). Leave
        # it alone under --log-level debug, where seeing every request is
        # the point.
        logging.getLogger("httpx").setLevel(logging.WARNING)
    # Private-by-default at-rest, same rationale as asterism-api (_main).
    os.umask(0o077)

    home = Path(
        args.data_dir or os.environ.get("ASTERISM_LOCAL_HOME") or default_data_home()
    ).expanduser()
    home.mkdir(parents=True, exist_ok=True)

    token = (os.environ.get("ASTERISM_API_TOKEN") or "").strip() or ensure_write_token(
        home
    )

    oxigraph_url = args.oxigraph_url or os.environ.get("CSV2RDF_OXIGRAPH_URL")
    child: subprocess.Popen[bytes] | None = None
    if not oxigraph_url:
        binary = find_oxigraph_binary()
        if binary is None:
            logger.error(_NO_OXIGRAPH_MSG)
            return 2
        oxi_port = _free_port()
        oxigraph_url = f"http://127.0.0.1:{oxi_port}"
        oxi_log = home / "logs" / "oxigraph.log"
        child = spawn_oxigraph(binary, home / "oxigraph_store", oxi_log, oxi_port)
        if not wait_oxigraph_ready(oxigraph_url, child):
            _terminate(child)
            logger.error("oxigraph did not become ready — see %s", oxi_log)
            return 2
        logger.info(
            "oxigraph: %s (pid %d, store %s)",
            oxigraph_url,
            child.pid,
            home / "oxigraph_store",
        )

    mcp_url = None if args.no_mcp else f"http://127.0.0.1:{args.port}{MCP_PATH}"
    for key, value in local_env(home, oxigraph_url, token, mcp_url=mcp_url).items():
        os.environ.setdefault(key, value)

    ui_dist = find_ui_dist(args.ui_dist)
    if ui_dist is None:
        logger.warning(
            "ui/dist not found — running API-only. Build the SPA with: "
            "cd ui && VITE_DEMO_MODE=live VITE_DEMO_AGENT_URL=/ npm run build"
        )

    demo_url: str | None = args.demo_agent_url
    demo_child: subprocess.Popen[bytes] | None = None
    if demo_url is None and not args.no_ask:
        demo_dir = find_demo_agent_dir()
        if demo_dir is None:
            logger.warning("demo-agent/app.py not found — Ask relay disabled")
        elif not ask_dependencies_available():
            logger.warning(
                "Ask needs asterism-mcp-tools + fastmcp — install them with: "
                "uv pip install -e '.[local]' — Ask relay disabled"
            )
        else:
            demo_port = _free_port()
            child_env = dict(os.environ)
            # The child's best-effort usage ledger POST targets this api.
            child_env["ASTERISM_API_URL"] = f"http://127.0.0.1:{args.port}"
            demo_child = spawn_demo_agent(
                demo_dir, home / "logs" / "demo-agent.log", demo_port, child_env
            )
            demo_url = f"http://127.0.0.1:{demo_port}"
            if not wait_demo_agent_ready(demo_url, demo_child):
                _terminate(demo_child)
                demo_child = None
                demo_url = None
                logger.warning(
                    "demo-agent did not become ready — Ask relay disabled (see %s)",
                    home / "logs" / "demo-agent.log",
                )
            else:
                logger.info(
                    "demo-agent: %s (pid %d)", demo_url, demo_child.pid
                )

    try:
        # Import AFTER the env defaults: asterism_api.main reads
        # ASTERISM_MAX_UPLOAD_BYTES at module import time.
        from asterism_api.main import Settings

        settings = Settings()
        app = build_local_app(
            token=token,
            ui_dist=ui_dist,
            settings=settings,
            demo_agent_url=demo_url,
            mcp=not args.no_mcp,
        )
        url = f"http://127.0.0.1:{args.port}/"
        logger.info("Asterism local: %s (data: %s)", url, home)
        if mcp_url:
            # The literal string a person registers in their AI client.
            logger.info("MCP endpoint: %s", mcp_url)

        # 見本データセット「世界の国」の初回自動取り込み（契約メモ
        # contract_pr_e.md §2）: oxigraph ready・app 構築後、ブラウザを開く前に
        # 一度だけ。app.state.client は ASGI lifespan の startup で初めて生える
        # ため、ここでは同じ oxigraph へ向けた別クライアントを使う。best-effort
        # — 失敗しても起動は止めない（seed_demo_dataset 自身が既に best-effort
        # だが、クライアントの生成・破棄まわりの不測の失敗もここで飲み込む）。
        async def _run_demo_seed() -> None:
            seed_client = OxigraphClient(OxigraphConfig(base_url=oxigraph_url))
            try:
                await seed_demo_dataset(home, settings, seed_client)
            finally:
                await seed_client.aclose()

        try:
            asyncio.run(_run_demo_seed())
        except Exception:
            logger.warning("seed_demo_dataset: setup failed (continuing)", exc_info=True)

        _serve(
            app,
            port=args.port,
            log_level=args.log_level,
            open_url=None if args.no_browser else url,
        )
    finally:
        if demo_child is not None:
            _terminate(demo_child)
        if child is not None:
            _terminate(child)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
