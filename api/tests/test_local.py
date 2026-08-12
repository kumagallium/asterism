"""Local mode (``asterism-local``) — ADR ``local-first-distribution.md``.

Covers the pieces local mode adds AROUND the untouched api:

* loopback token injection (the in-process caddy ``header_up`` equivalent):
  replace semantics, loopback-only, end-to-end through ``require_write_auth``;
* SPA serving with the caddy split (``/assets/*`` real 404s for the
  stale-chunk self-heal; index.html fallback with no-cache; api routes never
  shadowed);
* user-data env defaults + 0600 write token;
* oxigraph binary discovery failure is a clear, non-crashing exit.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
from pathlib import Path

import httpx
import pytest
from asterism.oxigraph_client import OxigraphClient, OxigraphConfig
from fastapi.testclient import TestClient

from asterism_api.local import (
    LoopbackTokenInjector,
    build_local_app,
    default_data_home,
    ensure_write_token,
    find_demo_agent_dir,
    find_oxigraph_binary,
    find_ui_dist,
    local_env,
    main,
)
from asterism_api.main import Settings

_TEST_TOKEN = "local-test-token"


def _settings(tmp: Path) -> Settings:
    env = {
        "CSV2RDF_DROP_ROOT": str(tmp / "csv"),
        "CSV2RDF_RDF_ROOT": str(tmp / "rdf"),
        "CSV2RDF_ERROR_ROOT": str(tmp / "errors"),
        "CSV2RDF_JOBS_LOG": str(tmp / "jobs.jsonl"),
        "CSV2RDF_REGISTRY_ROOT": str(tmp / "registry"),
        "CSV2RDF_OXIGRAPH_URL": "http://test",
        "CSV2RDF_SETTLE_S": "0.0",
    }
    s = Settings(env)
    # Local mode enables the raw-SPARQL relay (single-user box); the tests use
    # POST /api/sparql as the token-gated probe route.
    s.expose_raw_sparql = True
    s.api_token = _TEST_TOKEN
    return s


def _fake_oxigraph() -> OxigraphClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/query":
            body = {"head": {"vars": []}, "results": {"bindings": []}}
            return httpx.Response(
                200, content=json.dumps(body), headers={"Content-Type": "application/json"}
            )
        return httpx.Response(204)

    inner = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    )
    return OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner)


def _make_dist(tmp: Path) -> Path:
    dist = tmp / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>asterism-local-spa</html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (dist / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    return dist


def _request_as(
    app: object, client_addr: tuple[str, int], method: str, path: str, **kwargs: object
) -> httpx.Response:
    """Drive the app with a chosen ASGI client address (lifespan included).

    ``TestClient`` hardcodes ``client=("testclient", 50000)``, which is not a
    loopback IP — the injector must NOT fire for it. To exercise the loopback
    branch the test needs control over the client address, which
    ``httpx.ASGITransport`` provides.
    """

    async def _run() -> httpx.Response:
        async with app.router.lifespan_context(app):  # type: ignore[attr-defined]
            transport = httpx.ASGITransport(app=app, client=client_addr)  # type: ignore[arg-type]
            async with httpx.AsyncClient(
                transport=transport, base_url="http://local"
            ) as client:
                return await client.request(method, path, **kwargs)  # type: ignore[arg-type]

    return asyncio.run(_run())


# ---------------------------------------------------------------------------
# token injection


def test_injector_adds_token_and_strips_client_auth() -> None:
    captured: dict[str, list[tuple[bytes, bytes]]] = {}

    async def inner(scope, receive, send) -> None:  # type: ignore[no-untyped-def]
        captured["headers"] = list(scope["headers"])

    mw = LoopbackTokenInjector(inner, "tok")
    scope = {
        "type": "http",
        "client": ("127.0.0.1", 1234),
        "headers": [(b"authorization", b"Bearer stray"), (b"accept", b"*/*")],
    }
    asyncio.run(mw(scope, None, None))  # type: ignore[arg-type]
    assert (b"x-asterism-token", b"tok") in captured["headers"]
    assert not any(k == b"authorization" for k, _ in captured["headers"])
    assert (b"accept", b"*/*") in captured["headers"]


def test_injector_ignores_non_loopback_clients() -> None:
    captured: dict[str, list[tuple[bytes, bytes]]] = {}

    async def inner(scope, receive, send) -> None:  # type: ignore[no-untyped-def]
        captured["headers"] = list(scope["headers"])

    mw = LoopbackTokenInjector(inner, "tok")
    for client in (("10.0.0.5", 1), ("testclient", 2), None):
        scope = {"type": "http", "client": client, "headers": [(b"accept", b"*/*")]}
        asyncio.run(mw(scope, None, None))  # type: ignore[arg-type]
        assert captured["headers"] == [(b"accept", b"*/*")]


def test_loopback_request_passes_write_auth_without_pasting_token(
    tmp_path: Path,
) -> None:
    app = build_local_app(
        token=_TEST_TOKEN,
        ui_dist=None,
        settings=_settings(tmp_path),
        oxigraph_client=_fake_oxigraph(),
        start_watcher=False,
    )
    r = _request_as(
        app,
        ("127.0.0.1", 4321),
        "POST",
        "/api/sparql",
        json={"query": "SELECT * WHERE { ?s ?p ?o } LIMIT 1"},
    )
    assert r.status_code == 200, r.text
    # A stray wrong Bearer header is replaced, not trusted (caddy semantics).
    r = _request_as(
        app,
        ("127.0.0.1", 4321),
        "POST",
        "/api/sparql",
        json={"query": "SELECT * WHERE { ?s ?p ?o } LIMIT 1"},
        headers={"Authorization": "Bearer wrong"},
    )
    assert r.status_code == 200, r.text


def test_non_loopback_request_is_still_token_gated(tmp_path: Path) -> None:
    app = build_local_app(
        token=_TEST_TOKEN,
        ui_dist=None,
        settings=_settings(tmp_path),
        oxigraph_client=_fake_oxigraph(),
        start_watcher=False,
    )
    r = _request_as(
        app,
        ("10.0.0.5", 4321),
        "POST",
        "/api/sparql",
        json={"query": "SELECT * WHERE { ?s ?p ?o } LIMIT 1"},
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# SPA serving


def test_spa_serving_replicates_caddy_split(tmp_path: Path) -> None:
    dist = _make_dist(tmp_path)
    app = build_local_app(
        token=_TEST_TOKEN,
        ui_dist=dist,
        settings=_settings(tmp_path),
        oxigraph_client=_fake_oxigraph(),
        start_watcher=False,
    )
    with TestClient(app) as client:
        # index at /
        r = client.get("/")
        assert r.status_code == 200
        assert "asterism-local-spa" in r.text
        assert r.headers.get("cache-control") == "no-cache"
        # real asset
        assert client.get("/assets/app.js").status_code == 200
        # missing chunk stays a REAL 404 (vite:preloadError self-heal)
        assert client.get("/assets/missing-chunk.js").status_code == 404
        # any other path falls back to index.html (hash-routed SPA)
        r = client.get("/some/typoed/path")
        assert r.status_code == 200
        assert "asterism-local-spa" in r.text
        assert r.headers.get("cache-control") == "no-cache"
        # api routes are not shadowed by the mount
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
        assert client.get("/jobs").status_code == 200


def test_api_only_mode_keeps_api_routes(tmp_path: Path) -> None:
    app = build_local_app(
        token=_TEST_TOKEN,
        ui_dist=None,
        settings=_settings(tmp_path),
        oxigraph_client=_fake_oxigraph(),
        start_watcher=False,
    )
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/").status_code == 404


# ---------------------------------------------------------------------------
# data home, env defaults, token file


def test_local_env_points_everything_under_home(tmp_path: Path) -> None:
    home = tmp_path / "home"
    env = local_env(home, "http://127.0.0.1:17878", "tok")
    sources = home / "sources"
    assert env["CSV2RDF_DROP_ROOT"] == str(sources / "csv")
    assert env["CSV2RDF_RDF_ROOT"] == str(sources / "rdf")
    assert env["CSV2RDF_ERROR_ROOT"] == str(sources / "errors")
    assert env["CSV2RDF_JOBS_LOG"] == str(sources / "jobs.jsonl")
    assert env["CSV2RDF_REGISTRY_ROOT"] == str(sources / "registry")
    assert env["CSV2RDF_OXIGRAPH_URL"] == "http://127.0.0.1:17878"
    assert env["ASTERISM_API_TOKEN"] == "tok"
    assert env["ASTERISM_EXPOSE_RAW_SPARQL"] == "1"
    assert env["ASTERISM_ALLOW_PRIVATE_LLM_BASE"] == "1"


def test_write_token_is_minted_once_and_private(tmp_path: Path) -> None:
    home = tmp_path / "home"
    first = ensure_write_token(home)
    second = ensure_write_token(home)
    assert first == second
    assert len(first) >= 32
    mode = stat.S_IMODE(os.stat(home / "write_token").st_mode)
    assert mode == 0o600


def test_default_data_home_is_user_scoped() -> None:
    home = default_data_home()
    assert Path.home() in home.parents


def test_find_ui_dist_requires_index(tmp_path: Path) -> None:
    assert find_ui_dist(str(tmp_path / "nope")) is None
    dist = _make_dist(tmp_path)
    assert find_ui_dist(str(dist)) == dist


# ---------------------------------------------------------------------------
# /demo relay (Ask)


def _relay_app(tmp_path: Path, handler) -> object:  # type: ignore[no-untyped-def]
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://demo"
    )
    return build_local_app(
        token=_TEST_TOKEN,
        ui_dist=None,
        settings=_settings(tmp_path),
        oxigraph_client=_fake_oxigraph(),
        start_watcher=False,
        demo_agent_url="http://demo",
        demo_relay_client=client,
    )


def test_demo_relay_forwards_ask_headers_but_never_the_write_token(
    tmp_path: Path,
) -> None:
    seen: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["req"] = request
        return httpx.Response(200, json={"answer": "ok", "citations": []})

    app = _relay_app(tmp_path, handler)
    r = _request_as(
        app,
        ("127.0.0.1", 999),
        "POST",
        "/demo/ask",
        json={"question": "ZT top3?"},
        headers={"X-API-Key": "sk-local", "X-LLM-Provider": "openai-compatible"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["answer"] == "ok"
    fwd = seen["req"]
    assert fwd.url.path == "/demo/ask"
    assert fwd.headers["x-api-key"] == "sk-local"
    assert fwd.headers["x-llm-provider"] == "openai-compatible"
    # The loopback injector added X-Asterism-Token to the inbound request;
    # the relay must NOT leak the write token to the child.
    assert "x-asterism-token" not in fwd.headers
    assert json.loads(fwd.content)["question"] == "ZT top3?"


def test_demo_relay_passes_query_params_and_status(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("iri") == "https://x/1":
            return httpx.Response(200, json={"iri": "https://x/1", "chain": []})
        return httpx.Response(404, json={"error": "nope"})

    app = _relay_app(tmp_path, handler)
    r = _request_as(
        app, ("127.0.0.1", 9), "GET", "/demo/provenance?iri=https://x/1"
    )
    assert r.status_code == 200
    r = _request_as(app, ("127.0.0.1", 9), "GET", "/demo/provenance?iri=other")
    assert r.status_code == 404


def test_demo_relay_unreachable_child_is_502(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    app = _relay_app(tmp_path, handler)
    r = _request_as(app, ("127.0.0.1", 9), "GET", "/demo/schema")
    assert r.status_code == 502
    assert "unreachable" in r.json()["error"]


def test_no_relay_when_ask_disabled(tmp_path: Path) -> None:
    app = build_local_app(
        token=_TEST_TOKEN,
        ui_dist=None,
        settings=_settings(tmp_path),
        oxigraph_client=_fake_oxigraph(),
        start_watcher=False,
    )
    with TestClient(app) as client:
        assert client.get("/demo/schema").status_code == 404


def test_find_demo_agent_dir_in_checkout() -> None:
    found = find_demo_agent_dir()
    assert found is not None
    assert (found / "app.py").is_file()


# ---------------------------------------------------------------------------
# oxigraph discovery


def test_missing_oxigraph_binary_is_a_clear_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CSV2RDF_OXIGRAPH_URL", raising=False)
    monkeypatch.delenv("ASTERISM_OXIGRAPH_BIN", raising=False)
    monkeypatch.setattr("asterism_api.local.shutil.which", lambda _name: None)
    assert find_oxigraph_binary() is None
    prev_umask = os.umask(0o022)
    try:
        rc = main(["--data-dir", str(tmp_path / "home"), "--no-browser"])
    finally:
        os.umask(prev_umask)
    assert rc == 2


def test_oxigraph_binary_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "oxigraph-bin"
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("ASTERISM_OXIGRAPH_BIN", str(fake))
    assert find_oxigraph_binary() == str(fake)
