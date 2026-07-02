"""Server-side operator LLM keys (Option A) — the opt-in "configure once" path.

Off by default: ``server_key_for`` returns ``None`` unless
``ASTERISM_LLM_KEY_<PROVIDER>`` is set. When set, ``_llm_coords`` /
``/api/models/available`` / the AI endpoints fall back to it, and a
browser-supplied key still wins.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from asterism_api import main as main_mod
from asterism_api import server_keys
from asterism_api.main import _llm_coords, build_app
from tests.test_main import _mock_client, _settings

_PROVIDER_ENVS = (
    "ASTERISM_LLM_KEY_ANTHROPIC",
    "ASTERISM_LLM_KEY_OPENAI",
    "ASTERISM_LLM_KEY_OPENAI_COMPATIBLE",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start each test with no operator keys, so 'off by default' is the baseline."""
    for name in _PROVIDER_ENVS:
        monkeypatch.delenv(name, raising=False)


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


# --- server_keys module -------------------------------------------------------


def test_unset_returns_none() -> None:
    assert server_keys.server_key_for("anthropic") is None


def test_set_returns_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASTERISM_LLM_KEY_ANTHROPIC", "sk-op")
    assert server_keys.server_key_for("anthropic") == "sk-op"


def test_openai_compatible_env_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASTERISM_LLM_KEY_OPENAI_COMPATIBLE", "sk-c")
    assert server_keys.server_key_for("openai-compatible") == "sk-c"


def test_blank_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASTERISM_LLM_KEY_ANTHROPIC", "   ")
    assert server_keys.server_key_for("anthropic") is None


def test_configured_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASTERISM_LLM_KEY_OPENAI", "sk-o")
    assert server_keys.configured_providers() == {
        "anthropic": False,
        "openai": True,
        "openai-compatible": False,
    }


# --- _llm_coords fallback -----------------------------------------------------


def test_browser_key_wins_over_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASTERISM_LLM_KEY_ANTHROPIC", "sk-op")
    _, _, _, key = _llm_coords("sk-browser", None, None, None)
    assert key == "sk-browser"


def test_falls_back_to_server_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASTERISM_LLM_KEY_ANTHROPIC", "sk-op")
    _, _, _, key = _llm_coords(None, None, None, None)
    assert key == "sk-op"


def test_none_when_unset() -> None:
    _, _, _, key = _llm_coords(None, None, None, None)
    assert key is None


# --- endpoints ----------------------------------------------------------------


def test_server_keys_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASTERISM_LLM_KEY_ANTHROPIC", "sk-op")
    app = build_app(_settings(tmp_path), oxigraph_client=_healthy(tmp_path), start_watcher=False)
    with TestClient(app) as client:
        r = client.get("/api/llm/server-keys")
        assert r.status_code == 200
        provs = r.json()["providers"]
        assert provs["anthropic"] is True
        assert provs["openai"] is False


def test_models_available_uses_server_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ASTERISM_LLM_KEY_ANTHROPIC", "sk-op")
    seen: dict[str, str | None] = {}

    def fake(provider: str, api_key: str | None = None, api_base: str | None = None):
        seen["key"] = api_key
        return [{"id": "claude-x", "display_name": "Claude X"}]

    monkeypatch.setattr(main_mod, "list_available_models", fake)
    app = build_app(_settings(tmp_path), oxigraph_client=_healthy(tmp_path), start_watcher=False)
    with TestClient(app) as client:
        r = client.post("/api/models/available", json={"provider": "anthropic"})  # no api_key
        assert r.status_code == 200
        assert seen["key"] == "sk-op"  # fell back to the operator key
