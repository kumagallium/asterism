"""Phase 1 smoke test: Oxigraph + togomcp standalone + MIE.

検証目的: Option B (Oxigraph backend + togomcp 経由 MCP 公開) が実環境で動くか。
失敗すれば Option A (自作 MCP) に降りる撤退判断ゲート (option-b-architecture.md §8)。

セットアップ:
    cd experiments/phase1-smoke-test
    uv venv .venv --python 3.11
    . .venv/bin/activate
    uv pip install 'git+https://github.com/dbcls/togomcp.git' rdflib fastmcp

    # Oxigraph 起動 + papers_100 投入 (Phase 0.5 環境を再利用)
    docker compose -f ../phase05/oxigraph/compose.yaml up -d
    curl -X POST --data-binary @../phase05/data/papers_100.ttl \
      'http://localhost:7878/store?default' -H 'Content-Type: text/turtle'

    python test_smoke.py

togomcp-data/ には endpoints.csv (starrydata 行を追加済み) と
mie/starrydata.yaml (smoke-test 用 minimal MIE) が含まれている。
これは dbcls/togomcp v0.1.0 の togo_mcp/data/ を雛形にしたもので、
TOGOMCP_DIR 環境変数で togomcp に渡される。

前提:
- Oxigraph が http://localhost:7878 で起動中、papers_100.ttl が default graph にロード済
- togomcp が pip install 済
- TOGOMCP_DIR が togomcp-data/ を指す環境変数で渡される

挙動:
- FastMCP Client を stdio で togo-mcp-local に接続
- ツール一覧を表示
- get_MIE_file(database="starrydata") で MIE が読めることを確認
- run_sparql(database="starrydata", sparql_query=...) で round-trip 確認
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

HERE = Path(__file__).resolve().parent
TOGOMCP_DIR = HERE / "togomcp-data"


async def main() -> int:
    env = os.environ.copy()
    env["TOGOMCP_DIR"] = str(TOGOMCP_DIR)

    transport = StdioTransport(
        command=str(HERE / ".venv" / "bin" / "togo-mcp-local"),
        args=[],
        env=env,
    )

    async with Client(transport) as client:
        print("== connected ==")

        # 1. List tools
        tools = await client.list_tools()
        tool_names = [t.name for t in tools]
        print(f"tools ({len(tool_names)}): {tool_names[:10]}{'...' if len(tool_names) > 10 else ''}")
        assert "get_MIE_file" in tool_names, "get_MIE_file tool not registered"
        assert "run_sparql" in tool_names, "run_sparql tool not registered"

        # 2. get_MIE_file(starrydata)
        print()
        print("== get_MIE_file(starrydata) ==")
        result = await client.call_tool("get_MIE_file", {"database": "starrydata"})
        text = _result_text(result)
        assert "Starrydata" in text, f"MIE not loaded: {text[:200]!r}"
        print(f"  OK (length {len(text)} chars, starts with: {text[:80]!r})")

        # 3. run_sparql(starrydata, COUNT)
        print()
        print("== run_sparql(starrydata, COUNT(*)) ==")
        result = await client.call_tool(
            "run_sparql",
            {
                "database": "starrydata",
                "sparql_query": "SELECT (COUNT(*) AS ?c) WHERE { ?s ?p ?o }",
            },
        )
        text = _result_text(result)
        assert "3715" in text, f"unexpected count, got: {text[:300]!r}"
        print(f"  OK: {text[:200]!r}")

        # 4. run_sparql(starrydata, paper titles)
        print()
        print("== run_sparql(starrydata, paper titles LIMIT 3) ==")
        result = await client.call_tool(
            "run_sparql",
            {
                "database": "starrydata",
                "sparql_query": (
                    "PREFIX sd: <http://localhost/asterism/starrydata/ontology#>\n"
                    "PREFIX schema: <https://schema.org/>\n"
                    "SELECT ?p ?t WHERE { ?p a sd:Paper ; schema:name ?t } LIMIT 3"
                ),
            },
        )
        text = _result_text(result)
        assert "Decoupling" in text or "Thermoelectric" in text or "schema" in text, (
            f"unexpected result: {text[:500]!r}"
        )
        print(f"  OK (first 300 chars): {text[:300]!r}")

        print()
        print("== ALL ASSERTIONS PASSED — Option B is viable ==")
        return 0


def _result_text(result: object) -> str:
    """FastMCP の CallToolResult から text を抽出 (3.x 系の content list)。"""
    if hasattr(result, "content"):
        parts = []
        for c in result.content:
            if hasattr(c, "text"):
                parts.append(c.text)
        return "\n".join(parts)
    if hasattr(result, "structured_content"):
        return str(result.structured_content)
    return str(result)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
