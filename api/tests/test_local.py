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
import logging
import os
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
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
    legacy_data_home,
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


def test_default_data_home_prefers_documents_for_fresh_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    home = default_data_home()
    assert home == tmp_path / "Documents" / "Asterism"
    # No side effects: calling the function does not create the directory.
    assert not home.exists()


def test_default_data_home_keeps_legacy_when_populated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    legacy = legacy_data_home()
    legacy.mkdir(parents=True)
    (legacy / "write_token").write_text("token\n", encoding="utf-8")

    home = default_data_home()

    assert home == legacy


def test_default_data_home_ignores_empty_legacy_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    legacy = legacy_data_home()
    legacy.mkdir(parents=True)  # exists, but empty

    home = default_data_home()

    assert home == tmp_path / "Documents" / "Asterism"


def test_default_data_home_has_no_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    default_data_home()
    assert list(tmp_path.iterdir()) == []


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


def test_find_demo_agent_dir_env_override_wins_and_never_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundled = tmp_path / "bundle-demo"
    bundled.mkdir()
    (bundled / "app.py").write_text("app = None\n", encoding="utf-8")
    monkeypatch.setenv("ASTERISM_DEMO_AGENT_DIR", str(bundled))
    assert find_demo_agent_dir() == bundled
    # A wrong explicit location must NOT silently fall back to the checkout.
    monkeypatch.setenv("ASTERISM_DEMO_AGENT_DIR", str(tmp_path / "missing"))
    assert find_demo_agent_dir() is None


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


def test_default_log_level_quiets_httpx(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """httpx's per-request INFO line (the Oxigraph liveness probe, every
    ~10s) was 98.5% of a real backend.log (180,621 of 183,451 lines) and
    buried the handful of lines an actual investigation needed. Default
    runs must drop it to WARNING; ``--log-level debug`` must not touch it.
    """
    monkeypatch.delenv("CSV2RDF_OXIGRAPH_URL", raising=False)
    monkeypatch.delenv("ASTERISM_OXIGRAPH_BIN", raising=False)
    monkeypatch.setattr("asterism_api.local.shutil.which", lambda _name: None)
    httpx_logger = logging.getLogger("httpx")
    prev_level = httpx_logger.level
    try:
        httpx_logger.setLevel(logging.NOTSET)
        rc = main(["--data-dir", str(tmp_path / "home"), "--no-browser"])
        assert rc == 2
        assert httpx_logger.level == logging.WARNING
    finally:
        httpx_logger.setLevel(prev_level)


def test_debug_log_level_leaves_httpx_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CSV2RDF_OXIGRAPH_URL", raising=False)
    monkeypatch.delenv("ASTERISM_OXIGRAPH_BIN", raising=False)
    monkeypatch.setattr("asterism_api.local.shutil.which", lambda _name: None)
    httpx_logger = logging.getLogger("httpx")
    prev_level = httpx_logger.level
    try:
        httpx_logger.setLevel(logging.NOTSET)
        rc = main(
            [
                "--data-dir",
                str(tmp_path / "home"),
                "--no-browser",
                "--log-level",
                "debug",
            ]
        )
        assert rc == 2
        assert httpx_logger.level == logging.NOTSET
    finally:
        httpx_logger.setLevel(prev_level)


def test_oxigraph_binary_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "oxigraph-bin"
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("ASTERISM_OXIGRAPH_BIN", str(fake))
    assert find_oxigraph_binary() == str(fake)


# ---------------------------------------------------------------------------
# SIGTERM must not orphan children (real subprocess — this is a regression
# test for a bug that only exists in uvicorn's actual signal handling, so a
# mock/TestClient run cannot see it: see _Server in asterism_api.local).

_HAS_PS = shutil.which("ps") is not None


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_health(url: str, process: subprocess.Popen[bytes], timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            if httpx.get(url, timeout=2.0).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    return False


def _descendant_pids(parent_pid: int) -> set[int]:
    """Every live pid whose ppid is ``parent_pid`` (``ps`` — no new dependency;
    ``psutil`` is not in the api's dependencies)."""
    out = subprocess.run(
        ["ps", "-eo", "pid=,ppid="], capture_output=True, text=True, check=True
    ).stdout
    children = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        pid, ppid = parts
        if ppid.isdigit() and int(ppid) == parent_pid and pid.isdigit():
            children.add(int(pid))
    return children


@pytest.mark.skipif(
    find_oxigraph_binary() is None, reason="no oxigraph binary (ASTERISM_OXIGRAPH_BIN / PATH)"
)
@pytest.mark.skipif(not _HAS_PS, reason="no `ps` on this platform")
def test_sigterm_does_not_orphan_children(tmp_path: Path) -> None:
    """Reproduces the production incident (v0.22.1, Oxigraph + demo-agent left
    running as pid 1 orphans): start ``asterism-local`` for real, SIGTERM the
    parent, and require that every child it spawned (at least Oxigraph) is
    also gone — not just the parent."""
    port = _free_port()
    home = tmp_path / "home"
    env = dict(os.environ)
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "asterism_api.local",
            "--port",
            str(port),
            "--data-dir",
            str(home),
            "--no-browser",
            "--log-level",
            "warning",
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        ready = _wait_for_health(f"http://127.0.0.1:{port}/health", proc, timeout_s=30.0)
        if not ready:
            output = proc.stdout.read().decode("utf-8", "replace") if proc.stdout else ""
            pytest.fail(f"asterism-local did not become ready:\n{output}")

        children_before = _descendant_pids(proc.pid)
        assert children_before, (
            "no children detected under the asterism-local process — the test "
            "cannot exercise the orphan bug (expected at least the Oxigraph child)"
        )

        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            output = proc.stdout.read().decode("utf-8", "replace") if proc.stdout else ""
            proc.kill()
            proc.wait(timeout=5.0)
            pytest.fail(f"asterism-local did not exit within 10s of SIGTERM:\n{output}")

        # Give the OS a moment to reap; then NONE of the children spawned
        # before SIGTERM may still be alive (as themselves, or reparented to
        # pid 1 — either way they are still descendants of nothing we own).
        deadline = time.monotonic() + 5.0
        survivors = children_before
        while time.monotonic() < deadline:
            survivors = {
                pid
                for pid in children_before
                if subprocess.run(
                    ["ps", "-p", str(pid)], capture_output=True
                ).returncode
                == 0
            }
            if not survivors:
                break
            time.sleep(0.2)
        assert not survivors, f"orphaned child pid(s) after SIGTERM: {survivors}"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5.0)
        if proc.stdout:
            proc.stdout.close()
