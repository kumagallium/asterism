"""Copilot サブスク経路と Gemini（固定 base）のテスト。

公式 SDK ``copilot`` は実機の CLI を起動するので、``sys.modules`` に偽物を差し込む。
"""

from __future__ import annotations

import asyncio
import sys
import types
from typing import ClassVar

import pytest

from asterism_step0 import copilot_support as cs
from asterism_step0.llm import (
    GEMINI_DEFAULT_MAX_TOKENS,
    GEMINI_OPENAI_BASE,
    CopilotCliMissingError,
    CopilotLLMClient,
    CopilotNotSignedInError,
    CopilotTimeoutError,
    LLMCancelledError,
    LLMEmptyOutputError,
    OpenAICompatibleLLMClient,
    error_code,
    list_available_models,
    make_llm,
)


class _Ev:
    def __init__(self, type_: str, **data: object) -> None:
        self.type = types.SimpleNamespace(value=type_)
        self.data = types.SimpleNamespace(**data)


class _FakeSession:
    def __init__(self, outer: _FakeClient, kwargs: dict) -> None:
        self.outer = outer
        self.kwargs = kwargs
        self.handlers: list = []
        self.disconnected = False
        self.aborted = False

    def on(self, handler):
        self.handlers.append(handler)
        return lambda: None

    async def send_and_wait(self, prompt, *, timeout=60.0):
        self.outer.prompts.append(prompt)
        if self.outer.hang:
            await asyncio.sleep(30)
        for h in self.handlers:
            h(
                _Ev(
                    "assistant.usage",
                    model="gpt-x",
                    input_tokens=11,
                    output_tokens=7,
                    cache_read_tokens=3,
                    cache_write_tokens=None,
                )
            )
        return _Ev("assistant.message", content=self.outer.reply)

    async def abort(self):
        self.aborted = True

    async def disconnect(self):
        self.disconnected = True


class _FakeClient:
    instances: ClassVar[list] = []
    authed = True
    reply = "OK"
    hang = False

    def __init__(self, **kwargs: object) -> None:
        self.init = kwargs
        self.sessions: list[_FakeSession] = []
        self.prompts: list[str] = []
        self.stopped = False
        type(self).instances.append(self)
        self.reply = type(self).reply
        self.hang = type(self).hang

    async def start(self):
        pass

    async def stop(self):
        self.stopped = True

    async def get_auth_status(self):
        return types.SimpleNamespace(isAuthenticated=type(self).authed)

    async def list_models(self):
        return [
            types.SimpleNamespace(id="auto", name="Auto"),
            types.SimpleNamespace(id="gpt-5.6-sol", name="GPT 5.6 Sol"),
        ]

    async def create_session(self, **kwargs):
        s = _FakeSession(self, kwargs)
        self.sessions.append(s)
        return s


@pytest.fixture
def fake_sdk(monkeypatch: pytest.MonkeyPatch, tmp_path):
    _FakeClient.instances = []
    _FakeClient.authed = True
    _FakeClient.reply = "OK"
    _FakeClient.hang = False
    cli = tmp_path / "copilot"
    cli.write_text("#!/bin/sh\n")
    cli.chmod(0o755)
    monkeypatch.setenv("ASTERISM_COPILOT_CLI", str(cli))

    mod = types.ModuleType("copilot")
    mod.CopilotClient = _FakeClient  # type: ignore[attr-defined]
    conn = types.SimpleNamespace(for_stdio=lambda path=None, args=(): ("stdio", path))
    mod.RuntimeConnection = conn  # type: ignore[attr-defined]
    gen = types.ModuleType("copilot.generated")
    rpc = types.ModuleType("copilot.generated.rpc")

    class PermissionDecisionReject:
        def __init__(self, feedback: str = "") -> None:
            self.feedback = feedback

    rpc.PermissionDecisionReject = PermissionDecisionReject  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "copilot", mod)
    monkeypatch.setitem(sys.modules, "copilot.generated", gen)
    monkeypatch.setitem(sys.modules, "copilot.generated.rpc", rpc)
    return str(cli)


def test_complete_returns_text_and_usage(fake_sdk) -> None:
    client = make_llm("copilot-subscription")
    assert isinstance(client, CopilotLLMClient)
    out = client.complete("SYS", "hi")
    assert out.text == "OK"
    assert (out.usage.input_tokens, out.usage.output_tokens) == (11, 7)
    assert out.usage.cache_read_tokens == 3 and out.usage.cache_write_tokens == 0
    assert client.last_usage == out.usage

    fc = _FakeClient.instances[-1]
    assert fc.stopped and fc.sessions[0].disconnected
    assert fc.init["connection"] == ("stdio", fake_sdk)
    assert fc.init["env"]["COPILOT_SKIP_CLI_DOWNLOAD"] == "1"


def test_builtin_tools_are_never_enabled(fake_sdk) -> None:
    CopilotLLMClient(model="auto").complete("SYS", "hi")
    kw = _FakeClient.instances[-1].sessions[0].kwargs
    assert kw["available_tools"] == []
    assert kw["system_message"] == {"mode": "replace", "content": "SYS"}
    assert kw["model"] == "auto"
    # 権限要求は全拒否
    assert kw["on_permission_request"]("req", {}).feedback


def test_works_inside_a_running_event_loop(fake_sdk) -> None:
    async def go() -> str:
        return CopilotLLMClient().complete("S", "u").text

    assert asyncio.run(go()) == "OK"


def test_empty_answer_raises(fake_sdk) -> None:
    _FakeClient.reply = "  "
    with pytest.raises(LLMEmptyOutputError):
        CopilotLLMClient().complete("S", "u")


def test_cancel_before_start(fake_sdk) -> None:
    c = CopilotLLMClient()
    c.should_cancel = lambda: True
    with pytest.raises(LLMCancelledError):
        c.complete("S", "u")


def test_cancel_during_call_aborts_session(fake_sdk) -> None:
    _FakeClient.hang = True
    calls = {"n": 0}

    def should_cancel() -> bool:
        calls["n"] += 1
        return calls["n"] > 1  # 開始前は通し、待機中に立てる

    c = CopilotLLMClient()
    c.should_cancel = should_cancel
    with pytest.raises(LLMCancelledError):
        c.complete("S", "u")
    assert _FakeClient.instances[-1].sessions[0].aborted


def test_cli_missing(monkeypatch: pytest.MonkeyPatch, fake_sdk) -> None:
    monkeypatch.setenv("ASTERISM_COPILOT_CLI", "/nonexistent/copilot")
    with pytest.raises(CopilotCliMissingError) as ei:
        CopilotLLMClient().complete("S", "u")
    assert ei.value.code == "copilot_cli_missing"
    assert error_code(ei.value) == "llm.copilot_cli_missing"


def test_not_signed_in(fake_sdk) -> None:
    _FakeClient.authed = False
    with pytest.raises(CopilotNotSignedInError) as ei:
        CopilotLLMClient().complete("S", "u")
    assert ei.value.code == "copilot_not_signed_in"
    assert _FakeClient.instances[-1].stopped


def test_list_models(fake_sdk) -> None:
    out = list_available_models("copilot-subscription")
    assert out == [
        {"id": "auto", "display_name": "Auto"},
        {"id": "gpt-5.6-sol", "display_name": "GPT 5.6 Sol"},
    ]


def test_child_env_adds_path_and_blocks_download(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin")
    env = cs.child_env("/opt/x/bin/copilot")
    parts = env["PATH"].split(":")
    assert parts[0] == "/opt/x/bin" and "/usr/bin" in parts and "/opt/homebrew/bin" in parts
    assert env["COPILOT_SKIP_CLI_DOWNLOAD"] == "1"


# --- Gemini ---------------------------------------------------------------


def test_gemini_uses_fixed_base_and_ignores_user_base() -> None:
    c = make_llm("gemini", api_key="k", api_base="http://evil.example/v1")
    assert isinstance(c, OpenAICompatibleLLMClient)
    assert c.base_url == GEMINI_OPENAI_BASE
    assert c.model.startswith("gemini-")


def test_gemini_models_strip_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    class _M:
        def __init__(self, id_: str) -> None:
            self.id = id_

    class _OAI:
        def __init__(self, **kw: object) -> None:
            seen.update(kw)
            self.models = types.SimpleNamespace(
                list=lambda: types.SimpleNamespace(
                    data=[_M("models/gemini-2.5-flash"), _M("gemini-2.5-pro")]
                )
            )

    mod = types.ModuleType("openai")
    mod.OpenAI = _OAI  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", mod)
    out = list_available_models("gemini", api_key="k", api_base="http://ignored")
    assert [m["id"] for m in out] == ["gemini-2.5-flash", "gemini-2.5-pro"]
    assert seen == {"api_key": "k", "base_url": GEMINI_OPENAI_BASE}


def test_gemini_default_max_tokens_fits_gemini_limit() -> None:
    assert make_llm("gemini", api_key="k").max_tokens == GEMINI_DEFAULT_MAX_TOKENS == 65536
    assert make_llm("gemini", api_key="k", max_tokens=1000).max_tokens == 1000


@pytest.mark.parametrize("key", [None, "", "  "])
def test_gemini_without_key_never_reaches_the_sdk_env_fallback(
    monkeypatch: pytest.MonkeyPatch, key: str | None
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    built: list = []

    class _OAI:
        def __init__(self, **kw: object) -> None:
            built.append(kw)

    mod = types.ModuleType("openai")
    mod.OpenAI = _OAI  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", mod)
    with pytest.raises(ValueError, match="API key"):
        make_llm("gemini", api_key=key).complete("S", "u")
    with pytest.raises(ValueError, match="API key"):
        list_available_models("gemini", api_key=key)
    assert built == []  # SDK を作る前に止まる（OPENAI_API_KEY は Google に渡らない）


# --- timeout / 実モデル名 -------------------------------------------------


def test_start_hang_times_out_stops_child_and_raises(
    fake_sdk, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def hang(self):
        await asyncio.sleep(30)

    monkeypatch.setattr(_FakeClient, "start", hang)
    monkeypatch.setenv(cs.START_TIMEOUT_ENV, "0.6")
    with pytest.raises(CopilotTimeoutError) as ei:
        CopilotLLMClient().complete("S", "u")
    assert ei.value.code == "copilot_error" and "応答しません" in str(ei.value)
    assert _FakeClient.instances[-1].stopped


def test_auth_hang_times_out(fake_sdk, monkeypatch: pytest.MonkeyPatch) -> None:
    async def hang(self):
        await asyncio.sleep(30)

    monkeypatch.setattr(_FakeClient, "get_auth_status", hang)
    monkeypatch.setenv(cs.START_TIMEOUT_ENV, "0.6")
    with pytest.raises(CopilotTimeoutError):
        CopilotLLMClient().complete("S", "u")
    assert _FakeClient.instances[-1].stopped


def test_cancel_works_while_waiting_for_start(
    fake_sdk, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def hang(self):
        await asyncio.sleep(30)

    monkeypatch.setattr(_FakeClient, "start", hang)
    calls = {"n": 0}

    def should_cancel() -> bool:
        calls["n"] += 1
        return calls["n"] > 1

    c = CopilotLLMClient()
    c.should_cancel = should_cancel
    with pytest.raises(LLMCancelledError):
        c.complete("S", "u")
    assert _FakeClient.instances[-1].stopped


def test_list_models_hang_times_out(fake_sdk, monkeypatch: pytest.MonkeyPatch) -> None:
    async def hang(self):
        await asyncio.sleep(30)

    monkeypatch.setattr(_FakeClient, "list_models", hang)
    monkeypatch.setenv(cs.LIST_TIMEOUT_ENV, "0.6")
    with pytest.raises(CopilotTimeoutError):
        list_available_models("copilot-subscription")


def test_run_sync_timeout_when_the_coroutine_never_returns() -> None:
    async def never():
        await asyncio.sleep(30)

    with pytest.raises(CopilotTimeoutError):
        cs.run_sync(never, timeout=0.3)


def test_actual_model_name_is_kept_for_the_ledger(fake_sdk) -> None:
    c = make_llm("copilot-subscription", model="auto")
    c.complete("S", "u")
    assert c.last_model == "gpt-x"  # 偽 SDK の assistant.usage.model
