#!/usr/bin/env python3
"""Demo: the exposure profile + "togomcp removed, typed-only, same answer".

Proves the controlled-exposure claim of the store/MCP split (ADR
``store-mcp-split.md``) without needing Docker or a live store:

1. Builds the **same** asterism MCP front two ways — raw-SPARQL exposure ON
   (topology A / open) and OFF (topology B / sensitive) — and prints the tool
   surface each consumer would see. OFF withholds ``sparql_query`` (and there is
   no togomcp ``run_sparql`` in this front at all) while keeping every typed,
   vetted tool.
2. Calls a typed tool (``property_ranking``) through BOTH servers and shows the
   answer is byte-for-byte identical — i.e. closing the raw escape costs nothing
   for the questions the typed tools cover.

Run (self-contained, mock store)::

    mcp/.venv/bin/python scripts/demo_exposure_profile.py

Run against a REAL Oxigraph (the genuine end-to-end)::

    CSV2RDF_OXIGRAPH_URL=http://localhost:7878 mcp/.venv/bin/python \
        scripts/demo_exposure_profile.py

The compose counterpart is ``compose.mcp-front.yaml`` (no oxigraph, no togomcp).
"""
from __future__ import annotations

import asyncio
import json
import os

import httpx
from asterism.oxigraph_client import OxigraphClient, OxigraphConfig

from asterism_mcp.server import Settings, build_server

_SDR = "https://kumagallium.github.io/asterism/starrydata/resource/"

# A canned property_ranking result so the demo runs with no live store. A real
# Oxigraph (CSV2RDF_OXIGRAPH_URL set) is queried for real instead.
_FAKE_ROW = {
    "curve": {"type": "uri", "value": f"{_SDR}curve/1-2-3"},
    "ymax": {"type": "literal", "value": "2.6"},
    "s": {"type": "uri", "value": f"{_SDR}sample/1-2"},
    "comp": {"type": "literal", "value": "SnSe"},
    "p": {"type": "uri", "value": f"{_SDR}paper/1"},
    "title": {"type": "literal", "value": "A thermoelectric paper"},
}


def _mock_handler(request: httpx.Request) -> httpx.Response:
    q = request.content.decode()
    # canonical-graph enumeration -> none (single default graph in the mock)
    bindings = [] if "SELECT DISTINCT ?g" in q else [_FAKE_ROW]
    variables = ["g"] if "SELECT DISTINCT ?g" in q else list(_FAKE_ROW)
    return httpx.Response(
        200,
        text=json.dumps({"head": {"vars": variables}, "results": {"bindings": bindings}}),
        headers={"content-type": "application/sparql-results+json"},
    )


def _make_client() -> tuple[OxigraphClient, str]:
    url = os.environ.get("CSV2RDF_OXIGRAPH_URL")
    if url:
        return OxigraphClient(OxigraphConfig(base_url=url)), f"real store @ {url}"
    inner = httpx.AsyncClient(
        transport=httpx.MockTransport(_mock_handler), base_url="http://mock"
    )
    return OxigraphClient(OxigraphConfig(base_url="http://mock"), client=inner), "mock store"


async def _tool_names(server) -> set[str]:
    return {t.name for t in await server.list_tools()}


async def main() -> int:
    client, where = _make_client()
    print(f"# Exposure-profile demo  ({where})\n")

    on = build_server(Settings({"ASTERISM_EXPOSE_RAW_SPARQL": "1"}), oxigraph_client=client)
    off = build_server(Settings({"ASTERISM_EXPOSE_RAW_SPARQL": "0"}), oxigraph_client=client)

    names_on, names_off = await _tool_names(on), await _tool_names(off)

    print("## 1. Tool surface a consumer sees")
    print(f"  exposure ON  ({len(names_on)} tools): {', '.join(sorted(names_on))}")
    print(f"  exposure OFF ({len(names_off)} tools): {', '.join(sorted(names_off))}")
    withheld = names_on - names_off
    print(f"  -> withheld when OFF: {', '.join(sorted(withheld)) or '(none)'}")
    assert "sparql_query" in names_on, "raw escape should be exposed when ON"
    assert "sparql_query" not in names_off, "raw escape must be withheld when OFF"
    typed = {"property_ranking", "sample_search", "template_curve_fetch", "provenance_of"}
    assert typed <= names_off, "typed tools must survive the switch"
    print("  -> typed tools survive the switch: "
          f"{', '.join(sorted(typed & names_off))}\n")

    print("## 2. Same typed answer, raw escape on vs off")
    args = {"property_y": "ZT", "max_plausible": 3.5}
    a = (await on.call_tool("property_ranking", args)).structured_content
    b = (await off.call_tool("property_ranking", args)).structured_content
    same = a == b
    top = (b.get("items") or [{}])[0]
    print(f"  property_ranking(ZT) top item: {top.get('composition')} "
          f"= {top.get('value')}  ({top.get('curve_iri')})")
    print(f"  answer identical (ON == OFF): {same}\n")
    assert same, "typed-tool answer changed when the raw escape was closed!"

    print("✅ Controlled exposure verified: closing raw SPARQL removes the "
          "arbitrary-extraction surface while every cited, typed answer is "
          "unchanged. (compose.mcp-front.yaml also drops togomcp + oxigraph.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
