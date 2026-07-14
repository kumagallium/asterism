"""FastMCP server wiring tests.

Verify that ``build_server`` registers the expected tool and that a
``call_tool`` round-trip surfaces the underlying implementation's result.
"""
from __future__ import annotations

import json

import httpx
from asterism.oxigraph_client import OxigraphClient, OxigraphConfig
from asterism.starrydata import DEFAULT_ONTOLOGY, DEFAULT_RESOURCE

from asterism_mcp.server import Settings, build_server

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


# ---- #20 P4-2: content-declared query tools registered dynamically ----------


def _rows_response(bindings: list[dict], variables: list[str]) -> httpx.Response:
    return httpx.Response(
        200,
        text=json.dumps({"head": {"vars": variables}, "results": {"bindings": bindings}}),
        headers={"content-type": "application/sparql-results+json"},
    )


async def test_declared_query_tools_registered_with_typed_schema(monkeypatch) -> None:
    monkeypatch.setenv("ASTERISM_BUNDLED_TOOLS", "1")  # exercises the bundled examples
    mcp = build_server(
        Settings({}), oxigraph_client=_mock_client(lambda r: _rows_response([], ["g"]))
    )
    tools = {t.name: t for t in await mcp.list_tools()}
    # starrydata's content tools are exposed (no longer hardcoded in server.py).
    assert "property_ranking" in tools and "sample_search" in tools
    schema = tools["property_ranking"].to_mcp_tool().inputSchema
    assert schema["required"] == ["property_y"]  # required param surfaced
    assert schema["properties"]["top_n"]["default"] == 10  # default surfaced
    assert "max_plausible" in schema["properties"]  # optional param present


async def test_declared_property_ranking_call_round_trip(monkeypatch) -> None:
    monkeypatch.setenv("ASTERISM_BUNDLED_TOOLS", "1")  # exercises the bundled examples
    def handler(request: httpx.Request) -> httpx.Response:
        q = request.content.decode()
        if "SELECT DISTINCT ?g" in q:  # canonical-graph enumeration -> none
            return _rows_response([], ["g"])
        row = {
            "curve": {"type": "uri", "value": f"{SDR}curve/1"},
            "ymax": {"type": "literal", "value": "2.6"},
            "s": {"type": "uri", "value": f"{SDR}sample/1"},
            "comp": {"type": "literal", "value": "SnSe"},
            "p": {"type": "uri", "value": f"{SDR}paper/1"},
            "title": {"type": "literal", "value": "A paper"},
        }
        return _rows_response([row], ["curve", "ymax", "s", "comp", "p", "title"])

    mcp = build_server(Settings({}), oxigraph_client=_mock_client(handler))
    result = await mcp.call_tool("property_ranking", {"property_y": "ZT", "max_plausible": 3.5})
    body = result.structured_content
    assert body["tool"] == "property_ranking"
    assert body["count"] == 1
    assert body["items"][0]["curve_iri"] == f"{SDR}curve/1"
    assert body["items"][0]["value"] == 2.6  # number-coerced per result mapping


# ---- exposure profile: gate the raw SPARQL escape (ADR store-mcp-split) ------


async def test_exposure_on_registers_sparql_query() -> None:
    mcp = build_server(
        Settings({"ASTERISM_EXPOSE_RAW_SPARQL": "1"}),  # explicit opt-in
        oxigraph_client=_mock_client(lambda r: _rows_response([], ["g"])),
    )
    names = {t.name for t in await mcp.list_tools()}
    assert "sparql_query" in names


async def test_exposure_default_withholds_sparql_query() -> None:
    # Safe-by-default: with no env var set, the raw escape is NOT registered.
    mcp = build_server(
        Settings({}),
        oxigraph_client=_mock_client(lambda r: _rows_response([], ["g"])),
    )
    names = {t.name for t in await mcp.list_tools()}
    assert "sparql_query" not in names
    assert "schema_summary" in names  # typed tools stay on


async def test_exposure_off_withholds_only_sparql_query(monkeypatch) -> None:
    monkeypatch.setenv("ASTERISM_BUNDLED_TOOLS", "1")  # exercises the bundled examples
    mcp = build_server(
        Settings({"ASTERISM_EXPOSE_RAW_SPARQL": "0"}),
        oxigraph_client=_mock_client(lambda r: _rows_response([], ["g"])),
    )
    names = {t.name for t in await mcp.list_tools()}
    # The arbitrary-SPARQL escape is gone...
    assert "sparql_query" not in names
    # ...but every typed / vetted tool is still exposed.
    assert {"template_curve_fetch", "provenance_of", "schema_summary"} <= names
    assert "property_ranking" in names and "sample_search" in names


async def test_typed_tool_returns_same_result_regardless_of_exposure(monkeypatch) -> None:
    """The whole point: closing raw SPARQL does not change typed-tool answers."""
    monkeypatch.setenv("ASTERISM_BUNDLED_TOOLS", "1")  # exercises the bundled examples

    def handler(request: httpx.Request) -> httpx.Response:
        q = request.content.decode()
        if "SELECT DISTINCT ?g" in q:  # canonical-graph enumeration -> none
            return _rows_response([], ["g"])
        row = {
            "curve": {"type": "uri", "value": f"{SDR}curve/1"},
            "ymax": {"type": "literal", "value": "2.6"},
            "s": {"type": "uri", "value": f"{SDR}sample/1"},
            "comp": {"type": "literal", "value": "SnSe"},
            "p": {"type": "uri", "value": f"{SDR}paper/1"},
            "title": {"type": "literal", "value": "A paper"},
        }
        return _rows_response([row], ["curve", "ymax", "s", "comp", "p", "title"])

    args = {"property_y": "ZT", "max_plausible": 3.5}
    on = build_server(Settings({}), oxigraph_client=_mock_client(handler))
    off = build_server(
        Settings({"ASTERISM_EXPOSE_RAW_SPARQL": "0"}),
        oxigraph_client=_mock_client(handler),
    )
    body_on = (await on.call_tool("property_ranking", args)).structured_content
    body_off = (await off.call_tool("property_ranking", args)).structured_content
    assert body_on == body_off
    assert body_off["items"][0]["curve_iri"] == f"{SDR}curve/1"


async def test_declared_tool_name_collision_is_prefixed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ASTERISM_BUNDLED_TOOLS", "1")  # exercises the bundled examples
    # Two datasets declaring the same tool name -> the second is {dataset}_-prefixed.
    doc = "tools:\n  - name: dup\n    query: 'SELECT ?s WHERE { ?s ?p ?o }'\n"
    for ds in ("alpha", "beta"):
        (tmp_path / ds).mkdir()
        (tmp_path / ds / "query_tools.yaml").write_text(doc, encoding="utf-8")
    monkeypatch.setenv("ASTERISM_DATASETS_ROOT", str(tmp_path))

    mcp = build_server(
        Settings({}), oxigraph_client=_mock_client(lambda r: _rows_response([], ["g"]))
    )
    names = {t.name for t in await mcp.list_tools()}
    assert "dup" in names  # first dataset keeps the bare name
    assert "beta_dup" in names  # second is prefixed to avoid the collision


async def test_declared_bundled_tools_hidden_by_default(monkeypatch, tmp_path) -> None:
    # Real-user feedback (2026-07-14): the MCP surface serves only the workbench
    # registry by default — the repo-bundled example datasets need an explicit
    # ASTERISM_BUNDLED_TOOLS=1 opt-in, so no tool is listed for a dataset that
    # exists nowhere in the user's catalog.
    monkeypatch.delenv("ASTERISM_BUNDLED_TOOLS", raising=False)
    reg = tmp_path / "registry"
    ds = reg / "my-dataset-abc12345"
    ds.mkdir(parents=True)
    (ds / "query_tools.yaml").write_text(
        "tools:\n  - name: t1\n    query: 'SELECT ?s WHERE { ?s ?p ?o }'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CSV2RDF_REGISTRY_ROOT", str(reg))
    mcp = build_server(
        Settings({}), oxigraph_client=_mock_client(lambda r: _rows_response([], ["g"]))
    )
    names = {t.name for t in await mcp.list_tools()}
    assert "t1" in names  # the user's registry tool serves
    assert "property_ranking" not in names and "sample_search" not in names
    assert "schema_summary" in names  # the generic hardcoded surface is intact
