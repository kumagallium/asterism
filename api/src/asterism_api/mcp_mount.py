"""Serve the MCP endpoint from the app itself, at ``/mcp``.

Why this exists
---------------
The MCP tools already ship in the desktop bundle, but reaching them meant
spawning ``asterism_mcp.server`` as a *separate* stdio subprocess — which
forces the person registering the server to supply, by hand:

* the interpreter path inside ``Asterism.app`` (the bundled console script's
  shebang points at the build machine),
* ``CSV2RDF_REGISTRY_ROOT`` (``<data home>/sources/registry`` — not the data
  home itself),
* ``CSV2RDF_OXIGRAPH_URL`` — whose port :func:`asterism_api.local._free_port`
  picks fresh on *every* launch, so any value written into a client config is
  stale after the next restart.

None of those are knowable without reading this source tree. Mounted here they
are not asked at all: the app already holds them, and the endpoint rides the
same fixed port as the SPA the user is looking at. Registration collapses to
one URL — ``http://127.0.0.1:8765/mcp`` — which is the whole point (ADR
mcp-endpoint-on-the-app.md).

Scope: local / single-user mode only. The hosted deployment keeps the separate
MCP front container, where the store↔MCP split is the security boundary (ADR
store-mcp-split.md); nothing here changes that app.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger(__name__)

MCP_PATH = "/mcp"


class _FixedPathApp:
    """Call ``app`` as if the request had arrived at ``path``.

    Starlette's ``Mount("/mcp", …)`` only matches ``/mcp/**`` — a bare ``/mcp``
    falls through to ``redirect_slashes`` and answers 307. A 307 preserves
    method and body, so *some* MCP clients survive it, but the URL we hand out
    must work in the client that does not follow redirects on POST. So the
    exact path gets its own route, and this shim rewrites the scope the way a
    Mount would have.
    """

    def __init__(self, app: Any, path: str, mount_path: str) -> None:
        self._app = app
        self._path = path
        self._mount_path = mount_path

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") == "http":
            scope = dict(
                scope,
                path=self._path,
                root_path=scope.get("root_path", "") + self._mount_path,
            )
        await self._app(scope, receive, send)


def attach_mcp(app: FastAPI) -> bool:
    """Mount the MCP streamable-HTTP endpoint on ``app`` at ``/mcp``.

    Returns False (and logs why) when the MCP tools are not installed — an api
    venv without the ``local`` extra must still start, minus this endpoint.

    Two routes are appended, both before the SPA catch-all mount that
    ``build_local_app`` adds afterwards: an exact ``/mcp`` (via
    :class:`_FixedPathApp`) and ``/mcp/**`` for clients that write the trailing
    slash. The session manager's lifespan is chained onto the app's own — the
    streamable-HTTP transport refuses to serve without it.
    """
    try:
        from asterism_mcp.server import build_server
    except ImportError as exc:  # pragma: no cover - depends on install extras
        logger.warning(
            "MCP endpoint disabled: %s — install with: uv pip install -e '.[local]'",
            exc,
        )
        return False

    from starlette.routing import Mount, Route

    mcp_app = build_server().http_app(path="/")

    app.router.routes.append(
        Route(
            MCP_PATH,
            endpoint=_FixedPathApp(mcp_app, "/", MCP_PATH),
            methods=["GET", "POST", "DELETE"],
        )
    )
    app.router.routes.append(Mount(MCP_PATH, app=mcp_app))

    outer = app.router.lifespan_context

    @asynccontextmanager
    async def with_mcp(app_: FastAPI) -> AsyncIterator[None]:
        async with mcp_app.lifespan(app_), outer(app_):
            yield

    app.router.lifespan_context = with_mcp
    return True
