"""POST /api/design/consult (ADR design-consult-chat.md): stateless, tool-free
design-consult chat turn. Mock LLM, no oxigraph client needed (this route never
touches the store)."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from asterism_api.main import CONSULT_SYSTEM_PROMPT, Settings, build_app

_TEST_TOKEN = "test-token"
_AUTH = {"X-Asterism-Token": _TEST_TOKEN}


def _settings(tmp: Path) -> Settings:
    s = Settings(
        {
            "CSV2RDF_DROP_ROOT": str(tmp / "csv"),
            "CSV2RDF_RDF_ROOT": str(tmp / "rdf"),
            "CSV2RDF_ERROR_ROOT": str(tmp / "errors"),
            "CSV2RDF_JOBS_LOG": str(tmp / "jobs.jsonl"),
            "CSV2RDF_REGISTRY_ROOT": str(tmp / "registry"),
            "CSV2RDF_OXIGRAPH_URL": "http://test",
            "CSV2RDF_SETTLE_S": "0.0",
        }
    )
    s.api_token = _TEST_TOKEN
    return s


class _MockLLM:
    """Records the rendered prompt and returns canned text (no network)."""

    def __init__(self, captured: dict[str, object]) -> None:
        self.captured = captured

    def complete(self, system_prompt: str, user_message: str) -> str:
        self.captured["system"] = system_prompt
        self.captured["user"] = user_message
        return "Quality はデータの信頼度を表す 5 段階の等級です。"


def _app(tmp_path: Path, captured: dict[str, object]):
    return build_app(
        _settings(tmp_path),
        start_watcher=False,
        llm_factory=lambda key: _MockLLM(captured),
    )


def test_consult_returns_reply(tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    app = _app(tmp_path, captured)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/design/consult",
            json={"messages": [{"role": "user", "content": "Quality 列って何?"}]},
            headers={"X-API-Key": "sk-user-test"},
        )
        assert r.status_code == 200
        body = r.json()
        assert "reply" in body
        assert "Quality" in body["reply"]


def test_consult_rejects_empty_messages(tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    app = _app(tmp_path, captured)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post("/api/design/consult", json={"messages": []})
        assert r.status_code == 400
        r2 = client.post(
            "/api/design/consult", json={"messages": [{"role": "user", "content": "   "}]}
        )
        assert r2.status_code == 400


def test_consult_weaves_context_into_prompt(tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    app = _app(tmp_path, captured)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/design/consult",
            json={
                "messages": [{"role": "user", "content": "この列はどういう意味?"}],
                "context": {
                    "step": "項目の意味",
                    "dataset": "XRDサンプル",
                    "skeleton_summary": "1行=1測定点",
                    "focus_column": {"name": "Quality", "samples": ["A", "B", "C"]},
                },
            },
            headers={"X-API-Key": "sk-user-test"},
        )
        assert r.status_code == 200
        user_message = captured["user"]
        assert "Quality" in user_message
        assert "A" in user_message and "B" in user_message and "C" in user_message
        assert "項目の意味" in user_message
        assert "XRDサンプル" in user_message
        assert "この列はどういう意味" in user_message


def test_consult_system_prompt_names_real_navigation() -> None:
    """The AI must guide with buttons that actually exist on screen (real-LLM
    dogfood 2026-08-25: the model invented "左側メニューの「データ設計」" and
    "プロジェクト一覧の「設定をリセット」" — neither exists). This pins the
    catalog's real UI copy (grounded against the ja i18n locales / GalleryView)
    and the guardrail against inventing names, so a future prompt edit cannot
    silently drop either."""
    catalog_phrases = [
        "設計を見直す",  # ui/src/i18n/locales/ja/gallery.json:272,276 (redesign.head/open)
        "最初から",  # ui/src/i18n/locales/ja/kantan.json:15 (recipe.backToStartShort)
        "戻ってやり直す",  # ui/src/i18n/locales/ja/kantan.json:109,122 (s3.back / s4.discard)
        "データの数えかたに戻る",  # ui/src/i18n/locales/ja/kantan.json:276 (s6.backToGate)
        "データを追加",  # ui/src/i18n/locales/ja/common.json:19 (nav.workbench)
        "公開する",  # ui/src/i18n/locales/ja/kantan.json:366,379 (s8.title/publish)
        "質問する",  # ui/src/i18n/locales/ja/common.json:16 (nav.ask)
        "利用許可コード",  # ui/src/i18n/locales/ja/settings.json:119,121 (write-token section)
    ]
    for phrase in catalog_phrases:
        assert phrase in CONSULT_SYSTEM_PROMPT, f"catalog missing real UI phrase: {phrase!r}"
    assert "発明してはいけません" in CONSULT_SYSTEM_PROMPT
    assert "聞き返して" in CONSULT_SYSTEM_PROMPT
