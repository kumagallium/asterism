"""FastMCP server wiring tests.

Verify that ``build_server`` registers the expected tool and that a
``call_tool`` round-trip surfaces the underlying implementation's result.
"""
from __future__ import annotations

import json

import httpx
from csv2rdf.oxigraph_client import OxigraphClient, OxigraphConfig
from csv2rdf.starrydata import DEFAULT_ONTOLOGY, DEFAULT_RESOURCE

from csv2rdf_mcp.server import Settings, build_server

SD = DEFAULT_ONTOLOGY
SDR = DEFAULT_RESOURCE


def _mock_client(handler) -> OxigraphClient:
    inner = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    )
    return OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner)


def _sparql_select_response(bindings: list[dict]) -> httpx.Response:
    return httpx.Response(
        200,
        text=json.dumps(
            {"head": {"vars": ["p", "o"]}, "results": {"bindings": bindings}}
        ),
        headers={"content-type": "application/sparql-results+json"},
    )


async def test_build_server_registers_template_curve_fetch() -> None:
    client = _mock_client(lambda r: _sparql_select_response([]))
    mcp = build_server(Settings({}), oxigraph_client=client)
    tools = await mcp.list_tools()
    names = [t.name for t in tools]
    assert "template_curve_fetch" in names


async def test_server_tool_returns_not_found_when_missing() -> None:
    mcp = build_server(
        Settings({}),
        oxigraph_client=_mock_client(lambda r: _sparql_select_response([])),
    )
    result = await mcp.call_tool(
        "template_curve_fetch", {"curve_iri": f"{SDR}curve/missing"}
    )
    body = result.structured_content
    assert body is not None
    assert body["found"] is False
    assert "not found" in body["error"]


async def test_server_tool_returns_payload_when_curve_exists() -> None:
    bindings = [
        {
            "p": {"type": "uri", "value": f"{SD}propertyX"},
            "o": {"type": "literal", "value": "Temperature"},
        },
        {
            "p": {"type": "uri", "value": f"{SD}xValuesJSON"},
            "o": {"type": "literal", "value": "[1, 2, 3]"},
        },
        {
            "p": {"type": "uri", "value": f"{SD}yValuesJSON"},
            "o": {"type": "literal", "value": "[10, 20, 30]"},
        },
    ]
    mcp = build_server(
        Settings({}),
        oxigraph_client=_mock_client(lambda r: _sparql_select_response(bindings)),
    )
    result = await mcp.call_tool(
        "template_curve_fetch", {"curve_iri": f"{SDR}curve/1-1-1"}
    )
    body = result.structured_content
    assert body is not None
    assert body["found"] is True
    assert body["property_x"] == "Temperature"
    assert body["x"] == [1.0, 2.0, 3.0]
    assert body["y"] == [10.0, 20.0, 30.0]
