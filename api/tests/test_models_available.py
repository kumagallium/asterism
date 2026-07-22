"""POST /api/models/available (model picker #②) + the SSRF guard.

No key/network needed: the SSRF guard uses a monkeypatched resolver and the
model listing is monkeypatched.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from asterism_api import main as main_mod
from asterism_api.main import build_app
from tests.test_main import _mock_client, _settings


def _healthy(_tmp: Path) -> object:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/query":
            return httpx.Response(
                200,
                text=json.dumps({"head": {}, "boolean": True}),
                headers={"content-type": "application/sparql-results+json"},
            )
        return httpx.Response(204)

    return _mock_client(handler)


# --- SSRF guard ---------------------------------------------------------------


def test_validate_rejects_non_http() -> None:
    with pytest.raises(HTTPException) as e:
        main_mod._validate_llm_api_base("ftp://example/v1")
    assert e.value.status_code == 400


def test_validate_rejects_private(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("10.0.0.5", 0))])
    with pytest.raises(HTTPException) as e:
        main_mod._validate_llm_api_base("https://internal.example/v1")
    assert e.value.status_code == 400


def test_validate_rejects_cloud_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("169.254.169.254", 0))]
    )
    with pytest.raises(HTTPException):
        main_mod._validate_llm_api_base("http://metadata/v1")


def test_validate_allows_public(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))]
    )
    main_mod._validate_llm_api_base("https://api.openai.com/v1")  # no raise


def test_validate_private_allowed_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 0))])
    monkeypatch.setenv("ASTERISM_ALLOW_PRIVATE_LLM_BASE", "1")
    main_mod._validate_llm_api_base("http://localhost:11434/v1")  # no raise


# --- POST /api/models/available ----------------------------------------------


def test_models_available_anthropic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        main_mod,
        "list_available_models",
        lambda provider, api_key=None, api_base=None: [
            {"id": "claude-opus-4-8", "display_name": "Claude Opus 4.8"}
        ],
    )
    app = build_app(_settings(tmp_path), oxigraph_client=_healthy(tmp_path), start_watcher=False)
    with TestClient(app) as client:
        r = client.post("/api/models/available", json={"provider": "anthropic", "api_key": "sk"})
        assert r.status_code == 200
        assert r.json()["models"][0]["id"] == "claude-opus-4-8"


def test_models_available_ssrf_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("169.254.169.254", 0))]
    )
    called = {"n": 0}

    def spy(provider, api_key=None, api_base=None):  # pragma: no cover - must not run
        called["n"] += 1
        return []

    monkeypatch.setattr(main_mod, "list_available_models", spy)
    app = build_app(_settings(tmp_path), oxigraph_client=_healthy(tmp_path), start_watcher=False)
    with TestClient(app) as client:
        r = client.post(
            "/api/models/available",
            json={"provider": "openai", "api_key": "sk", "api_base": "http://169.254.169.254/v1"},
        )
        assert r.status_code == 400
        assert called["n"] == 0  # guard fired before the network call


def test_models_available_provider_error_maps_502(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(provider, api_key=None, api_base=None):
        raise RuntimeError("bad key")

    monkeypatch.setattr(main_mod, "list_available_models", boom)
    app = build_app(_settings(tmp_path), oxigraph_client=_healthy(tmp_path), start_watcher=False)
    with TestClient(app) as client:
        r = client.post("/api/models/available", json={"provider": "anthropic", "api_key": "bad"})
        assert r.status_code == 502


def test_models_available_omitted_base_adopts_pinned_server_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Operator has a shared openai-compatible key pinned to Sakura (file store —
    # the only place a pinned base lives). With no key AND no base in the request,
    # the picker adopts both so it works key-lessly against the pinned endpoint.
    from asterism_api import server_keys

    server_keys.set_server_key(
        tmp_path / "registry", "openai-compatible", "sk-sakura", "https://api.ai.sakura.ad.jp/v1"
    )
    # Deterministic SSRF check for the (public) pinned base — no real DNS.
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))]
    )
    seen: dict = {}

    def spy(provider, api_key=None, api_base=None):
        seen.update(provider=provider, api_key=api_key, api_base=api_base)
        return [{"id": "sakura-model", "display_name": "Sakura"}]

    monkeypatch.setattr(main_mod, "list_available_models", spy)
    app = build_app(_settings(tmp_path), oxigraph_client=_healthy(tmp_path), start_watcher=False)
    with TestClient(app) as client:
        r = client.post("/api/models/available", json={"provider": "openai-compatible"})
        assert r.status_code == 200
    assert seen == {
        "provider": "openai-compatible",
        "api_key": "sk-sakura",
        "api_base": "https://api.ai.sakura.ad.jp/v1",
    }


def test_models_available_different_base_does_not_borrow_server_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Operator's shared key is pinned to Sakura, but the request explicitly names
    # a DIFFERENT (local LM Studio) endpoint with no key. The pinned key must NOT
    # be borrowed for another base (no key leak) AND the user's endpoint must be
    # honored — not silently replaced by the pinned Sakura URL (the bug that
    # returned Sakura's models against a localhost request).
    from asterism_api import server_keys

    server_keys.set_server_key(
        tmp_path / "registry", "openai-compatible", "sk-sakura", "https://api.ai.sakura.ad.jp/v1"
    )
    monkeypatch.setenv("ASTERISM_ALLOW_PRIVATE_LLM_BASE", "1")  # allow the localhost target
    seen: dict = {}

    def spy(provider, api_key=None, api_base=None):
        seen.update(provider=provider, api_key=api_key, api_base=api_base)
        return [{"id": "local-model", "display_name": "Local"}]

    monkeypatch.setattr(main_mod, "list_available_models", spy)
    app = build_app(_settings(tmp_path), oxigraph_client=_healthy(tmp_path), start_watcher=False)
    with TestClient(app) as client:
        r = client.post(
            "/api/models/available",
            json={"provider": "openai-compatible", "api_base": "http://localhost:1234/v1"},
        )
        assert r.status_code == 200
    assert seen == {
        "provider": "openai-compatible",
        "api_key": None,
        "api_base": "http://localhost:1234/v1",
    }
