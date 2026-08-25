"""POST /api/design/consult (ADR design-consult-chat.md): stateless, tool-free
design-consult chat turn. Mock LLM, no oxigraph client needed (this route never
touches the store)."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from asterism_api.main import Settings, build_app

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
