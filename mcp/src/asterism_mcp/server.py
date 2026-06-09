"""FastMCP server entry point for asterism self-built tools.

We keep this module thin on purpose: tool *bodies* live in
:mod:`asterism_mcp.tools` and stay testable without a transport. This file
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

import contextlib
import inspect
import logging
import os
from typing import Annotated, Final, Literal

from asterism.oxigraph_client import OxigraphClient, OxigraphConfig
from asterism.query_tools import (
    QueryTool,
    QueryToolError,
    ToolParam,
    load_all_query_tools,
    run_query_tool,
)
from fastmcp import FastMCP
from fastmcp.tools import Tool
from pydantic import Field

from asterism_mcp.tools import (
    CurveNotFoundError,
    SparqlNotReadOnlyError,
    provenance_of,
    schema_summary,
    sparql_query,
    template_curve_fetch,
)

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
    mcp: FastMCP = FastMCP(name="asterism-mcp-tools")

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
            return await template_curve_fetch(curve_iri, client, max_points=max_points)
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

    # #20 P4-2: sample_search / property_ranking are no longer registered here as
    # hardcoded tools — they are declared as CONTENT in
    # datasets/starrydata/query_tools.yaml and registered dynamically below
    # (``_register_declared_query_tools``), so any dataset gets the same typed
    # surface by shipping its own query_tools.yaml. The hardcoded bodies remain in
    # asterism_mcp.tools (kept by the equivalence test that pins content==hardcoded).

    @mcp.tool(
        name="provenance_of",
        description=(
            "Return the PROV chain behind a starrydata entity IRI (curve, "
            "sample, or paper): curve -> sample -> paper -> digitization -> "
            "ingestion. Use this to show where a cited number came from "
            "(which figure was digitized, from which paper, when ingested)."
        ),
    )
    async def _provenance_of(iri: str) -> dict[str, object]:
        try:
            return await provenance_of(iri, get_client())
        except ValueError as exc:
            return {"iri": iri, "found": False, "error": str(exc)}

    # ----- #18 generic Ask layer: schema-agnostic foundation (LLM-free) -----

    @mcp.tool(
        name="schema_summary",
        description=(
            "Introspect the vocabulary ACTUALLY present in the store — classes, "
            "predicates, and per-class predicate shapes, with usage counts — "
            "making NO starrydata assumptions. Use this FIRST when answering "
            "questions over a user-designed schema you have not seen, so you can "
            "write correct sparql_query calls instead of guessing predicate "
            "names. graph=None inspects the default (canonical) graph."
        ),
    )
    async def _schema_summary(
        graph: str | None = None,
        max_classes: int = 50,
        max_predicates: int = 100,
        predicates_per_class: int = 25,
    ) -> dict[str, object]:
        return await schema_summary(
            get_client(),
            graph=graph,
            max_classes=max_classes,
            max_predicates=max_predicates,
            predicates_per_class=predicates_per_class,
        )

    @mcp.tool(
        name="sparql_query",
        description=(
            "Run an arbitrary READ-ONLY SPARQL SELECT/ASK against the store and "
            "get back flat rows ({columns, rows, count, truncated}). The "
            "schema-agnostic escape hatch: pair it with schema_summary (call "
            "that first to learn the vocabulary) to answer questions over any "
            "graph, including user-designed schemas. Update-form queries "
            "(INSERT/DELETE/...) are rejected — read-only by contract."
        ),
    )
    async def _sparql_query(query: str, max_rows: int = 200) -> dict[str, object]:
        try:
            return await sparql_query(query, get_client(), max_rows=max_rows)
        except (ValueError, SparqlNotReadOnlyError) as exc:
            return {"error": str(exc), "columns": [], "rows": [], "count": 0}

    # ----- #20 P4-2: per-dataset typed tools declared as content -----
    _register_declared_query_tools(mcp, get_client)

    return mcp


# ----------------------------------------------------------------------------
# #20 P4-2: register content-declared query tools as typed FastMCP tools
# ----------------------------------------------------------------------------
#
# Each dataset's datasets/{name}/query_tools.yaml declares named, parameterized,
# read-only SPARQL operations (the typed path, generalized beyond starrydata).
# We turn each into a real FastMCP tool whose input schema is SYNTHESIZED from
# the declared parameters (so an agent sees property_ranking(property_y, top_n,
# max_plausible), not a generic dict). No code is generated: the handler is a
# fixed closure over the declared QueryTool, and we only set its __signature__.

_PY_TYPE: Final[dict[str, type]] = {"integer": int, "number": float}


def _param_annotation(p: ToolParam) -> object:
    """Base Python annotation for a declared parameter (enum -> Literal)."""
    if p.type == "enum" and p.enum:
        return Literal[tuple(p.enum)]  # type: ignore[misc]
    return _PY_TYPE.get(p.type, str)


def _make_query_tool_handler(tool: QueryTool, get_client):
    """An async handler with a signature synthesized from ``tool``'s parameters."""

    async def handler(**kwargs: object) -> dict[str, object]:
        # Drop unset optionals so the engine applies declared defaults / OPTIONAL.
        args = {k: v for k, v in kwargs.items() if v is not None}
        try:
            return await run_query_tool(get_client(), tool, args)
        except QueryToolError as exc:
            return {"tool": tool.name, "error": str(exc), "count": 0, "items": []}

    sig_params: list[inspect.Parameter] = []
    annotations: dict[str, object] = {}
    for p in tool.params:
        base = _param_annotation(p)
        if p.required:
            ann = Annotated[base, Field(description=p.description)]
            default: object = inspect.Parameter.empty
        elif p.default is not None:
            ann = Annotated[base, Field(description=p.description)]
            default = p.default
        else:
            ann = Annotated[base | None, Field(description=p.description)]
            default = None
        sig_params.append(
            inspect.Parameter(
                p.name, inspect.Parameter.KEYWORD_ONLY, default=default, annotation=ann
            )
        )
        annotations[p.name] = ann
    annotations["return"] = dict
    handler.__signature__ = inspect.Signature(sig_params)  # type: ignore[attr-defined]
    handler.__annotations__ = annotations
    handler.__name__ = tool.name
    handler.__doc__ = tool.description or tool.title
    return handler


def _register_declared_query_tools(mcp: FastMCP, get_client) -> None:
    """Register every dataset's declared query tools as typed FastMCP tools.

    Tool names are the declared names; if two datasets declare the same name the
    later one is prefixed with ``{dataset}_`` to avoid a collision. Names already
    taken by a hardcoded tool are likewise prefixed (defensive).
    """
    taken = {"template_curve_fetch", "provenance_of", "schema_summary", "sparql_query"}
    # Tools come from BOTH the repo example datasets (datasets/<name>/) AND the
    # workbench registry (registry/<id>/query_tools.yaml) — a tool a researcher
    # saved on their onboarded dataset becomes a typed MCP tool too (P1).
    sources = dict(load_all_query_tools())
    reg_root = os.environ.get("CSV2RDF_REGISTRY_ROOT")
    if reg_root:
        with contextlib.suppress(Exception):
            sources.update(load_all_query_tools(reg_root))
    for dataset, tools in sources.items():
        for tool in tools:
            name = tool.name if tool.name not in taken else f"{dataset}_{tool.name}"
            taken.add(name)
            mcp.add_tool(
                Tool.from_function(
                    _make_query_tool_handler(tool, get_client),
                    name=name,
                    description=tool.description or tool.title,
                )
            )


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

_DEFAULT_HOST: Final[str] = "0.0.0.0"
_DEFAULT_PORT: Final[int] = 8002


def _main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        prog="asterism",
        description="asterism self-built FastMCP tools server.",
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
