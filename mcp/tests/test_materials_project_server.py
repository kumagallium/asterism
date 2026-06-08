"""#19: the materials_project dataset's declared typed tools publish onto the
MCP surface via the same dynamic registration as starrydata (P4-2a) — proving
the typed path generalizes to a second, non-starrydata dataset with no engine
change. The engine knows no materials vocabulary; the tools come from
datasets/materials_project/query_tools.yaml.
"""
from __future__ import annotations

import json

import httpx
from asterism.oxigraph_client import OxigraphClient, OxigraphConfig

from asterism_mcp.server import Settings, build_server


def _mock_client() -> OxigraphClient:
    inner = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200,
                text=json.dumps({"head": {"vars": []}, "results": {"bindings": []}}),
                headers={"content-type": "application/sparql-results+json"},
            )
        ),
        base_url="http://test",
    )
    return OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner)


async def test_materials_project_typed_tools_register() -> None:
    mcp = build_server(Settings({}), oxigraph_client=_mock_client())
    names = {t.name for t in await mcp.list_tools()}
    assert {
        "structure_by_composition",
        "materials_by_space_group",
        "materials_by_crystal_system",
        "thermoelectric_structure",
    } <= names
