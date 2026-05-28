"""FastMCP server entry point for csv2rdf-mcp self-built tools.

We keep this module thin on purpose: tool *bodies* live in
:mod:`csv2rdf_mcp.tools` and stay testable without a transport. This file
only:

1. Reads environment-driven config (Oxigraph URL).
2. Constructs a :class:`fastmcp.FastMCP` instance.
3. Registers each tool body as an MCP-exposed callable.
4. Provides a CLI entry that picks HTTP or stdio transport.

The HTTP transport is what compose / Crucible / Dify connect to (port 8002
by default). The stdio transport is for Claude Desktop / Cline / Cursor
local users who spawn the server as a subprocess.
"""
from __future__ import annotations

import logging
import os
from typing import Final

from csv2rdf.oxigraph_client import OxigraphClient, OxigraphConfig
from fastmcp import FastMCP

from csv2rdf_mcp.tools import CurveNotFoundError, template_curve_fetch

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------------


class Settings:
    """Resolve from environment, mirroring the upload-api conventions."""

    def __init__(self, env: dict[str, str] | None = None) -> None:
        e = env if env is not None else os.environ
        self.oxigraph_url = e.get("CSV2RDF_OXIGRAPH_URL", "http://oxigraph:7878")


# ----------------------------------------------------------------------------
# build_server: assemble a FastMCP instance + tool bindings
# ----------------------------------------------------------------------------


def build_server(
    settings: Settings | None = None,
    *,
    oxigraph_client: OxigraphClient | None = None,
) -> FastMCP:
    """Construct the FastMCP server with all tools registered.

    ``oxigraph_client`` can be injected for tests (using
    ``httpx.MockTransport``). In production we lazily build one from
    ``settings`` on first use.
    """
    cfg = settings or Settings()
    mcp: FastMCP = FastMCP(name="csv2rdf-mcp-tools")

    # FastMCP runs each tool call through asyncio anyway; keeping the
    # client at module scope on the server lets us share the HTTPX pool
    # across tool invocations.
    state: dict[str, OxigraphClient | None] = {"client": oxigraph_client}

    def get_client() -> OxigraphClient:
        existing = state["client"]
        if existing is not None:
            return existing
        new = OxigraphClient(OxigraphConfig(base_url=cfg.oxigraph_url))
        state["client"] = new
        return new

    @mcp.tool(
        name="template_curve_fetch",
        description=(
            "Fetch the raw x[]/y[] arrays plus contextual metadata "
            "(property names, units, sample IRI, figure label, aggregate "
            "min/max) for a single starrydata curve, given its IRI. "
            "Use this when the AI needs point-level reasoning over a curve "
            "(e.g. 'what is Seebeck at 300 K?') beyond what xMin/xMax/yMin/yMax "
            "alone can answer."
        ),
    )
    async def _template_curve_fetch(
        curve_iri: str,
        max_points: int | None = None,
    ) -> dict[str, object]:
        client = get_client()
        try:
            return await template_curve_fetch(
                curve_iri, client, max_points=max_points
            )
        except CurveNotFoundError as exc:
            return {
                "iri": str(exc),
                "found": False,
                "error": "curve not found in Oxigraph",
            }
        except ValueError as exc:
            return {
                "iri": curve_iri,
                "found": False,
                "error": str(exc),
            }

    return mcp


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

_DEFAULT_HOST: Final[str] = "0.0.0.0"
_DEFAULT_PORT: Final[int] = 8002


def _main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        prog="csv2rdf-mcp",
        description="csv2rdf-mcp self-built FastMCP tools server.",
    )
    p.add_argument(
        "--transport",
        choices=("http", "stdio"),
        default="http",
        help="MCP transport. http for compose / Crucible / Dify; stdio for local agents.",
    )
    p.add_argument("--host", default=_DEFAULT_HOST)
    p.add_argument("--port", type=int, default=_DEFAULT_PORT)
    p.add_argument("--log-level", default="info")
    args = p.parse_args(argv)

    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(message)s")
    mcp = build_server()

    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        # FastMCP's HTTP transport listens on ``/mcp`` by default.
        mcp.run(transport="http", host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
