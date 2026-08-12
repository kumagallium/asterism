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
import importlib.util
import ipaddress
import logging
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from fastapi import APIRouter, Request
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

if TYPE_CHECKING:
    from asterism.oxigraph_client import OxigraphClient
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


def default_data_home() -> Path:
    """OS-conventional per-user data directory (override: ``--data-dir``)."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Asterism"
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        return (Path(appdata) if appdata else Path.home()) / "Asterism"
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / "asterism"


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


def local_env(home: Path, oxigraph_url: str, token: str) -> dict[str, str]:
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
    """``demo-agent/app.py`` (repo checkout / editable-install layout only)."""
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
) -> FastAPI:
    """``build_app`` + optional /demo relay + SPA mount + token injection.

    Route order matters: ``build_app`` registers the api routes first, the
    ``/demo/*`` relay is included next, and the static catch-all mount goes
    last — so ``/api/*``, ``/jobs``, ``/health``, ``/describe``,
    ``/upload/{kind}`` and ``/demo/*`` all win over the SPA fallback.
    """
    from asterism_api.main import build_app

    app = build_app(
        settings, oxigraph_client=oxigraph_client, start_watcher=start_watcher
    )
    if demo_agent_url is not None:
        app.include_router(create_demo_relay(demo_agent_url, demo_relay_client))
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

    # timeout_graceful_shutdown: SIGTERM must reach main()'s finally (which
    # terminates the oxigraph / demo-agent children) BEFORE a supervising shell
    # (the desktop app) escalates to SIGKILL — unbounded graceful shutdown can
    # linger on keep-alive connections and orphan the grandchildren.
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level=log_level,
        timeout_graceful_shutdown=5,
    )
    server = uvicorn.Server(config)

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
        help="data home (default: OS user-data dir, e.g. ~/Library/Application "
        "Support/Asterism; env ASTERISM_LOCAL_HOME)",
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
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(message)s")
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

    for key, value in local_env(home, oxigraph_url, token).items():
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

        app = build_local_app(
            token=token,
            ui_dist=ui_dist,
            settings=Settings(),
            demo_agent_url=demo_url,
        )
        url = f"http://127.0.0.1:{args.port}/"
        logger.info("Asterism local: %s (data: %s)", url, home)
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
