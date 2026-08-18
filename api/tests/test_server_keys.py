"""Server-side operator LLM keys (Option A) — the opt-in "configure once" path.

Off by default: ``server_key_for`` returns ``None`` unless
``ASTERISM_LLM_KEY_<PROVIDER>`` is set. When set, ``_llm_coords`` /
``/api/models/available`` / the AI endpoints fall back to it, and a
browser-supplied key still wins.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from asterism_api import main as main_mod
from asterism_api import server_keys
from asterism_api.main import _llm_coords, build_app
from tests.test_main import _AUTH, _mock_client, _settings

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


# --- file store (UI-set shared keys) ------------------------------------------


def test_set_and_resolve_file_store(tmp_path: Path) -> None:
    server_keys.set_server_key(tmp_path, "anthropic", "sk-file")
    assert server_keys.resolve("anthropic", tmp_path) == ("sk-file", None)
    assert server_keys.server_key_for("anthropic", tmp_path) == "sk-file"


def test_file_store_wins_over_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASTERISM_LLM_KEY_ANTHROPIC", "sk-env")
    server_keys.set_server_key(tmp_path, "anthropic", "sk-file")
    assert server_keys.server_key_for("anthropic", tmp_path) == "sk-file"


def test_set_blank_clears(tmp_path: Path) -> None:
    server_keys.set_server_key(tmp_path, "anthropic", "sk-file")
    server_keys.set_server_key(tmp_path, "anthropic", "")
    assert server_keys.server_key_for("anthropic", tmp_path) is None


def test_openai_compatible_pins_base(tmp_path: Path) -> None:
    server_keys.set_server_key(tmp_path, "openai-compatible", "sk-c", "https://api.x/v1")
    assert server_keys.resolve("openai-compatible", tmp_path) == ("sk-c", "https://api.x/v1")


def test_store_file_is_0600(tmp_path: Path) -> None:
    server_keys.set_server_key(tmp_path, "anthropic", "sk-file")
    mode = stat.S_IMODE((tmp_path / "_llm" / "server_keys.json").stat().st_mode)
    assert mode == 0o600


def test_coords_pins_openai_compatible_base(tmp_path: Path) -> None:
    server_keys.set_server_key(tmp_path, "openai-compatible", "sk-c", "https://pinned/v1")
    # No request key + a user-controlled base → the pinned base wins (no leak).
    _provider, _model, api_base, key = _llm_coords(
        None, "openai-compatible", None, "https://user-controlled/v1", tmp_path
    )
    assert key == "sk-c"
    assert api_base == "https://pinned/v1"


# --- POST /api/llm/server-keys (write-gated) ----------------------------------


def test_post_sets_key_and_get_reflects(tmp_path: Path) -> None:
    app = build_app(_settings(tmp_path), oxigraph_client=_healthy(tmp_path), start_watcher=False)
    with TestClient(app) as client:
        r = client.post(
            "/api/llm/server-keys",
            json={"provider": "anthropic", "api_key": "sk-op"},
            headers=_AUTH,
        )
        assert r.status_code == 200
        assert r.json()["providers"]["anthropic"] is True
        assert client.get("/api/llm/server-keys").json()["providers"]["anthropic"] is True
        # Blank key clears it.
        client.post(
            "/api/llm/server-keys", json={"provider": "anthropic", "api_key": ""}, headers=_AUTH
        )
        assert client.get("/api/llm/server-keys").json()["providers"]["anthropic"] is False


def test_post_requires_write_auth(tmp_path: Path) -> None:
    app = build_app(_settings(tmp_path), oxigraph_client=_healthy(tmp_path), start_watcher=False)
    with TestClient(app) as client:
        r = client.post(
            "/api/llm/server-keys", json={"provider": "anthropic", "api_key": "sk-op"}
        )  # no token
        assert r.status_code == 401


def test_post_openai_compatible_requires_base(tmp_path: Path) -> None:
    app = build_app(_settings(tmp_path), oxigraph_client=_healthy(tmp_path), start_watcher=False)
    with TestClient(app) as client:
        r = client.post(
            "/api/llm/server-keys",
            json={"provider": "openai-compatible", "api_key": "sk-c"},  # no api_base
            headers=_AUTH,
        )
        assert r.status_code == 400
