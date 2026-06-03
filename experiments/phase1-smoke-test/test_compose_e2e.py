"""Phase 1 compose E2E: docker compose up で立ち上がる togomcp HTTP 経由で SPARQL。

`test_smoke.py` は host から togo-mcp-local (stdio) で叩く前提だったが、こちらは
**compose で立ち上がった togomcp container** に HTTP/MCP で接続して、Oxigraph
に対する SPARQL クエリが round-trip するか検証する。

前提:
    docker compose up -d --build
    (papers/samples/curves の Turtle が Oxigraph にロード済み)
"""
from __future__ import annotations

import asyncio
import sys

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

TOGOMCP_URL = "http://localhost:8000/mcp"


async def main() -> int:
    transport = StreamableHttpTransport(url=TOGOMCP_URL)
    async with Client(transport) as client:
        print(f"== connected to {TOGOMCP_URL} ==")

        tools = await client.list_tools()
        names = [t.name for t in tools]
        print(f"tools ({len(names)}): {', '.join(names[:8])}...")
        assert "run_sparql" in names
        assert "get_MIE_file" in names

        # 1. MIE が読める
        r = await client.call_tool("get_MIE_file", {"database": "starrydata"})
        text = "\n".join(c.text for c in r.content if hasattr(c, "text"))
        assert "Starrydata" in text and "shape_expressions" in text
        print(f"  get_MIE_file: OK ({len(text)} chars)")

        # 2. COUNT(*) が 49449 を返す
        r = await client.call_tool(
            "run_sparql",
            {
                "database": "starrydata",
                "sparql_query": "SELECT (COUNT(*) AS ?c) WHERE { ?s ?p ?o }",
            },
        )
        text = "\n".join(c.text for c in r.content if hasattr(c, "text"))
        print(f"  COUNT(*): {text.strip()}")
        assert "49449" in text

        # 3. Bi2Te3 検索
        r = await client.call_tool(
            "run_sparql",
            {
                "database": "starrydata",
                "sparql_query": (
                    "PREFIX sd: <https://kumagallium.github.io/asterism/starrydata/ontology#>\n"
                    "PREFIX schema: <https://schema.org/>\n"
                    "SELECT ?sample ?comp WHERE {\n"
                    "  ?sample a sd:Sample ; sd:compositionString ?comp .\n"
                    "  FILTER(CONTAINS(LCASE(STR(?comp)), \"bi2te3\"))\n"
                    "} LIMIT 3"
                ),
            },
        )
        text = "\n".join(c.text for c in r.content if hasattr(c, "text"))
        assert "Bi2Te3" in text or "bi2te3" in text.lower()
        print(f"  Bi2Te3 query: OK ({len(text.splitlines())} CSV lines)")

        print()
        print("== compose-mode E2E PASSED — Oxigraph + togomcp HTTP MCP 経路成立 ==")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
