"""The MCP endpoint the app serves itself (ADR ``mcp-endpoint-on-the-app.md``).

What these pin is the *registration story*, not the tools: an AI client is
handed one URL — ``http://127.0.0.1:<port>/mcp`` — and that URL has to answer
``initialize`` with a session, on the exact spelling the person pasted, even
though a static SPA is mounted over the same prefix space.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from asterism_api.local import build_local_app

from .test_local import _TEST_TOKEN, _fake_oxigraph, _make_dist, _settings

pytest.importorskip("fastmcp", reason="MCP endpoint needs the [local] extra")

_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "1"},
    },
}
_MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


def _app(tmp_path: Path, *, mcp: bool = True, ui: bool = True):
    return build_local_app(
        token=_TEST_TOKEN,
        ui_dist=_make_dist(tmp_path) if ui else None,
        settings=_settings(tmp_path),
        oxigraph_client=_fake_oxigraph(),
        start_watcher=False,
        mcp=mcp,
    )


def _initialize_result(response) -> dict:
    """Pull the JSON-RPC result out of the SSE frame the transport returns."""
    assert response.status_code == 200, response.text
    for line in response.text.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    raise AssertionError(f"no SSE data frame in: {response.text!r}")


@pytest.mark.parametrize("path", ["/mcp", "/mcp/"])
def test_initialize_over_http(tmp_path: Path, path: str) -> None:
    """Both spellings work: no client is asked to guess the trailing slash."""
    with TestClient(_app(tmp_path)) as client:
        r = client.post(path, headers=_MCP_HEADERS, json=_INITIALIZE)
        body = _initialize_result(r)
        assert body["result"]["serverInfo"]["name"] == "asterism-mcp-tools"
        assert r.headers.get("mcp-session-id")


def test_mcp_is_not_shadowed_by_the_spa(tmp_path: Path) -> None:
    """The regression this endpoint was born from.

    With only the SPA catch-all mounted, ``GET /mcp`` answered 200 with
    index.html and ``POST /mcp`` answered 405 — the endpoint looked reachable
    to anyone probing with a browser and was not there at all.
    """
    with TestClient(_app(tmp_path)) as client:
        r = client.post("/mcp", headers=_MCP_HEADERS, json=_INITIALIZE)
        assert "asterism-local-spa" not in r.text
        assert r.headers["content-type"].startswith("text/event-stream")
        # ...and the SPA still owns everything else.
        assert "asterism-local-spa" in client.get("/some/typoed/path").text


def test_mcp_can_be_turned_off(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path, mcp=False)) as client:
        r = client.post("/mcp", headers=_MCP_HEADERS, json=_INITIALIZE)
        assert r.status_code == 405  # SPA mount: GET-only static files


def test_tools_are_listed(tmp_path: Path) -> None:
    """A registered client sees the typed tools, not an empty server."""
    with TestClient(_app(tmp_path)) as client:
        r = client.post("/mcp", headers=_MCP_HEADERS, json=_INITIALIZE)
        session = r.headers["mcp-session-id"]
        headers = {**_MCP_HEADERS, "mcp-session-id": session}
        client.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        listed = _initialize_result(
            client.post(
                "/mcp",
                headers=headers,
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            )
        )
        names = {t["name"] for t in listed["result"]["tools"]}
        # find_datasets is the entry point an AI with no prior knowledge calls
        # first (#438); without it a fresh client cannot discover anything.
        assert "find_datasets" in names
        assert "schema_summary" in names


def test_api_routes_survive_the_mount(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/jobs").status_code == 200
