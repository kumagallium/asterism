"""design §21 — 「作る AI」と「答える AI」。ヘッダの畳み方・話の手番での使い分け・Ask の中継。"""
# ruff: noqa: RUF001

from __future__ import annotations

import json
from pathlib import Path

import httpx
import rdflib
from fastapi.testclient import TestClient

from asterism_api import llm_roles
from asterism_api.main import build_app
from tests.test_chat_api import _new, _say
from tests.test_chat_scope import _approved_session, _ScopeLLM
from tests.test_crosswalk_api import _AUTH, _DatasetClient, _settings
from tests.test_themes_api import _LLM, AI_JSON, KEY, Q_LOWER

ANSWER = {
    "X-LLM-Answer-Provider": "openai-compatible",
    "X-LLM-Answer-Model": "small",
    "X-LLM-Answer-Api-Base": "https://small.example/v1",
    "X-LLM-Answer-Key": "answer-key",
}

# ---- 純関数 ---------------------------------------------------------------------------------


def test_answer_headers_fall_back_to_the_make_set() -> None:
    plain = {"X-API-Key": "k", "X-LLM-Provider": "anthropic", "X-LLM-Model": "big"}
    assert llm_roles.answer_headers(plain) == {
        "x-api-key": "k", "x-llm-provider": "anthropic", "x-llm-model": "big",
    }  # fmt: skip
    assert not llm_roles.has_answer_role(plain)


def test_answer_headers_never_mix_the_make_key_or_base() -> None:
    both = {
        "X-API-Key": "make-key", "X-LLM-Provider": "anthropic", "X-LLM-Api-Base": "https://big",
        "X-LLM-Max-Tokens": "9000", "X-LLM-Answer-Provider": "openai", "X-LLM-Answer-Model": "m",
    }  # fmt: skip
    # 答える AI の鍵が無ければ鍵は無し（作る AI の鍵を別の提供元へ送らない）
    assert llm_roles.answer_headers(both) == {"x-llm-provider": "openai", "x-llm-model": "m"}
    assert llm_roles.has_answer_role(both)


# ---- 話の手番 ---------------------------------------------------------------------------------


def _two(tmp_path: Path, make: _LLM, answer: _LLM, seen: list | None = None) -> TestClient:
    # 提供元・base・鍵を受け取って作り分ける（答える AI の組がそのまま渡ることを固定する）
    def resolve(provider, model, api_base, key, max_tokens=None):  # type: ignore[no-untyped-def]
        if seen is not None:
            seen.append((provider, model, api_base, key))
        if (provider, model, api_base, key) == (
            "openai-compatible", "small", "https://small.example/v1", "answer-key",
        ):  # fmt: skip
            return answer
        assert key == "test-key" and api_base is None  # 作る AI の組
        return make

    app = build_app(
        _settings(tmp_path),
        oxigraph_client=_DatasetClient(rdflib.Dataset()),
        start_watcher=False,
        llm_resolver=resolve,
    )
    return TestClient(app, headers={**_AUTH, **KEY, **ANSWER})


def test_router_uses_the_answer_ai_and_proposal_uses_the_make_ai(tmp_path: Path) -> None:
    make = _LLM(json.dumps(AI_JSON), route="chat", route_reply="make が振り分けた")
    answer = _LLM(route="propose")
    with _two(tmp_path, make, answer) as client:
        sid = _new(client)
        t = _say(client, sid, Q_LOWER)["turn"]
    assert len(answer.route_calls) == 1 and answer.calls == []
    assert make.route_calls == [] and len(make.calls) == 1
    assert t["state"] == "proposed"


def test_router_reply_comes_from_the_answer_ai(tmp_path: Path) -> None:
    make = _LLM(route="propose")
    answer = _LLM(route="chat", route_reply="どういたしまして。")
    with _two(tmp_path, make, answer) as client:
        sid = _new(client)
        t = _say(client, sid, "ありがとう")["turn"]
    assert t["reply"]["text"] == "どういたしまして。"
    assert make.calls == [] and make.route_calls == []


def test_scoped_fix_uses_the_answer_ai(tmp_path: Path) -> None:
    make = _LLM(json.dumps(AI_JSON))
    answer = _ScopeLLM()
    answer.calls.append(("seed", ""))  # 最初の AI_JSON を飛ばして、範囲の差分を返させる
    with _two(tmp_path, make, answer) as client:
        sid, iris = _approved_session(client)
        n_make = len(make.calls)
        out = _say(client, sid, "ナノ析出物を足して", scope={"node": "t|" + iris["格子熱伝導率"]})
    assert len(make.calls) == n_make  # 作る AI は呼ばない
    assert "# Scope — change only this" in answer.calls[-1][1]
    assert [o["op"] for o in out["turn"]["reply"]["ops"]] == ["add_concept", "add_relation"]


def test_without_answer_headers_one_ai_does_both(tmp_path: Path) -> None:
    llm = _LLM(json.dumps(AI_JSON), route="propose")
    app = build_app(
        _settings(tmp_path),
        oxigraph_client=_DatasetClient(rdflib.Dataset()),
        start_watcher=False,
        llm_factory=lambda key: llm,
    )
    with TestClient(app, headers={**_AUTH, **KEY}) as client:
        sid = _new(client)
        _say(client, sid, Q_LOWER)
    assert len(llm.route_calls) == 1 and len(llm.calls) == 1


def test_ask_gets_only_the_answer_ai(tmp_path: Path) -> None:
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(dict(request.headers))
        return httpx.Response(200, json={"answer": "3 件です。", "citations": []})

    with _two(tmp_path, _LLM(), _LLM()) as client:
        client.app.state.demo_agent_url = "http://demo.test"
        client.app.state.ask_transport = httpx.MockTransport(handler)
        sid = _new(client)
        _say(client, sid, "何件ある？", intent="ask")
    (h,) = calls
    assert h["x-api-key"] == "answer-key" and h["x-llm-provider"] == "openai-compatible"
    assert h["x-llm-api-base"] == "https://small.example/v1" and h["x-llm-model"] == "small"
    assert not any(k.startswith("x-llm-answer") for k in h)
