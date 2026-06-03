"""OxigraphClient: drive it with httpx.MockTransport — no real Oxigraph needed."""
from __future__ import annotations

import json

import httpx
import pytest

from asterism.oxigraph_client import OxigraphClient, OxigraphConfig

GRAPH = "https://example.org/g/papers"


def _make_client(handler: httpx.MockTransport) -> OxigraphClient:
    transport = handler
    inner = httpx.AsyncClient(transport=transport, base_url="http://test")
    return OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner)


async def test_post_turtle_bytes_sends_correct_request() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["graph_param"] = request.url.params.get("graph")
        seen["method"] = request.method
        seen["ctype"] = request.headers.get("content-type")
        seen["body"] = bytes(request.content)
        return httpx.Response(204)

    payload = b"@prefix ex: <#> . ex:a ex:b ex:c ."
    async with _make_client(httpx.MockTransport(handler)) as client:
        n = await client.post_turtle_bytes(payload, GRAPH)

    assert n == len(payload)
    assert seen["method"] == "POST"
    assert seen["path"] == "/store"
    assert seen["graph_param"] == GRAPH
    assert "text/turtle" in str(seen["ctype"])
    assert seen["body"] == payload


async def test_post_turtle_bytes_default_graph() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["query"] = request.url.query.decode()
        seen["graph_param"] = request.url.params.get("graph")
        return httpx.Response(204)

    payload = b"@prefix ex: <#> . ex:a ex:b ex:c ."
    async with _make_client(httpx.MockTransport(handler)) as client:
        # No graph_iri -> default graph.
        n = await client.post_turtle_bytes(payload)

    assert n == len(payload)
    assert seen["path"] == "/store"
    # Default-graph indirect identification: a valueless ``default`` flag,
    # and no ``graph`` parameter.
    assert "default" in str(seen["query"])
    assert seen["graph_param"] is None


async def test_post_turtle_retries_then_succeeds() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(500, text="boom")
        return httpx.Response(201)

    cfg = OxigraphConfig(base_url="http://test", retries=3, backoff_s=0.0)
    inner = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
    async with OxigraphClient(cfg, client=inner) as client:
        n = await client.post_turtle_bytes(b"x", GRAPH)

    assert n == 1
    assert attempts["n"] == 3


async def test_post_turtle_raises_after_exhausting_retries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="always fails")

    cfg = OxigraphConfig(base_url="http://test", retries=2, backoff_s=0.0)
    inner = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
    async with OxigraphClient(cfg, client=inner) as client:
        with pytest.raises(httpx.HTTPError):
            await client.post_turtle_bytes(b"x", GRAPH)


async def test_ping_returns_true_on_200() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=json.dumps({"head": {}, "boolean": True}),
            headers={"content-type": "application/sparql-results+json"},
        )

    async with _make_client(httpx.MockTransport(handler)) as client:
        assert await client.ping() is True


async def test_ping_returns_false_on_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    async with _make_client(httpx.MockTransport(handler)) as client:
        assert await client.ping() is False


async def test_graph_triple_count_parses_results() -> None:
    body = {
        "head": {"vars": ["c"]},
        "results": {"bindings": [{"c": {"type": "literal", "value": "42"}}]},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text=json.dumps(body), headers={"content-type": "application/sparql-results+json"}
        )

    async with _make_client(httpx.MockTransport(handler)) as client:
        assert await client.graph_triple_count(GRAPH) == 42
