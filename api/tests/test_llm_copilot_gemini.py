"""copilot-subscription / gemini プロバイダの api 側の振る舞い。

SDK・ネットワークは使わない（list_available_models と LLM は差し替え）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from asterism_step0.llm import CopilotCliMissingError, CopilotNotSignedInError
from fastapi.testclient import TestClient

from asterism_api import main as main_mod
from asterism_api import server_keys
from asterism_api.main import build_app
from tests.test_main import _settings
from tests.test_models_available import _healthy
from tests.test_query_tools_store import _AUTH, _VALID_DRAFT, _DraftLLM, _seed_dataset

COPILOT = "copilot-subscription"


def _app(tmp_path: Path, *, single_user: bool, **kw):
    cfg = _settings(tmp_path)
    cfg.single_user = single_user
    return build_app(cfg, oxigraph_client=_healthy(tmp_path), start_watcher=False, **kw)


# --- capabilities ---------------------------------------------------------


@pytest.mark.parametrize("single_user", [True, False])
def test_capabilities_follow_single_user(tmp_path: Path, single_user: bool) -> None:
    with TestClient(_app(tmp_path, single_user=single_user)) as client:
        r = client.get("/api/llm/capabilities")  # 認証ヘッダなし
    assert r.status_code == 200
    assert r.json() == {"copilot_subscription": single_user}


# --- models/available -----------------------------------------------------


def test_models_available_copilot_without_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict = {}

    def fake(provider, api_key=None, api_base=None):
        seen.update(provider=provider, api_key=api_key, api_base=api_base)
        return [{"id": "auto", "display_name": "Auto"}]

    monkeypatch.setattr(main_mod, "list_available_models", fake)
    with TestClient(_app(tmp_path, single_user=True)) as client:
        r = client.post("/api/models/available", json={"provider": COPILOT})
    assert r.status_code == 200
    assert r.json()["models"] == [{"id": "auto", "display_name": "Auto"}]
    assert seen == {"provider": COPILOT, "api_key": None, "api_base": None}


def test_models_available_copilot_rejected_when_not_single_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        main_mod, "list_available_models", lambda *a, **k: pytest.fail("must not run")
    )
    with TestClient(_app(tmp_path, single_user=False)) as client:
        r = client.post("/api/models/available", json={"provider": COPILOT})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "copilot_unavailable"


@pytest.mark.parametrize(
    "exc",
    [CopilotCliMissingError("no cli"), CopilotNotSignedInError("not signed in")],
)
def test_models_available_copilot_error_codes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    def boom(*a, **k):
        raise exc

    monkeypatch.setattr(main_mod, "list_available_models", boom)
    with TestClient(_app(tmp_path, single_user=True)) as client:
        r = client.post("/api/models/available", json={"provider": COPILOT})
    assert r.status_code == 400
    assert r.json()["detail"] == {"code": exc.code, "message": str(exc)}


def test_models_available_gemini_ignores_user_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict = {}

    def fake(provider, api_key=None, api_base=None):
        seen.update(provider=provider, api_key=api_key, api_base=api_base)
        return []

    monkeypatch.setattr(main_mod, "list_available_models", fake)
    with TestClient(_app(tmp_path, single_user=False)) as client:
        r = client.post(
            "/api/models/available",
            json={"provider": "gemini", "api_key": "k", "api_base": "http://169.254.169.254/v1"},
        )
    # SSRF 検査に掛からず（base は捨てられる）、キー必須の共有サーバでも通る
    assert r.status_code == 200
    assert seen["api_base"] is None and seen["api_key"] == "k"


# --- server keys ----------------------------------------------------------


def test_server_keys_cover_gemini_not_copilot(tmp_path: Path) -> None:
    assert "gemini" in server_keys.PROVIDERS
    assert COPILOT not in server_keys.PROVIDERS
    with TestClient(_app(tmp_path, single_user=False)) as client:
        body = client.get("/api/llm/server-keys").json()
    assert body["providers"]["gemini"] is False
    assert body["default_models"]["gemini"].startswith("gemini-")
    assert COPILOT not in body["providers"]


# --- LLM を作る経路 -------------------------------------------------------


def test_copilot_llm_rejected_when_not_single_user(tmp_path: Path) -> None:
    client = TestClient(
        _app(tmp_path, single_user=False, llm_factory=lambda key: _DraftLLM(_VALID_DRAFT)),
        headers=_AUTH,
    )
    ds = _seed_dataset(tmp_path / "registry")
    with client:
        r = client.post(
            f"/api/datasets/{ds}/tools/propose",
            json={"intent": "x"},
            headers={"X-LLM-Provider": COPILOT},
        )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "copilot_unavailable"


def test_copilot_needs_no_key_when_single_user(tmp_path: Path) -> None:
    client = TestClient(
        _app(tmp_path, single_user=True, llm_factory=lambda key: _DraftLLM(_VALID_DRAFT)),
        headers=_AUTH,
    )
    ds = _seed_dataset(tmp_path / "registry")
    with client:
        r = client.post(
            f"/api/datasets/{ds}/tools/propose",
            json={"intent": "x"},
            headers={"X-LLM-Provider": COPILOT},  # X-API-Key なし
        )
        assert r.status_code == 200, r.text
        # 他のプロバイダは従来どおりキー必須
        r2 = client.post(
            f"/api/datasets/{ds}/tools/propose",
            json={"intent": "x"},
            headers={"X-LLM-Provider": "gemini"},
        )
    assert r2.status_code == 400


def test_gemini_without_key_is_400_before_any_llm_is_built(tmp_path: Path) -> None:
    client = TestClient(
        _app(tmp_path, single_user=False, llm_factory=lambda key: _DraftLLM(_VALID_DRAFT)),
        headers=_AUTH,
    )
    ds = _seed_dataset(tmp_path / "registry")
    with client:
        r = client.post(
            f"/api/datasets/{ds}/tools/propose",
            json={"intent": "x"},
            headers={"X-LLM-Provider": "gemini"},
        )
    assert r.status_code == 400
