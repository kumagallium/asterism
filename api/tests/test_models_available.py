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
