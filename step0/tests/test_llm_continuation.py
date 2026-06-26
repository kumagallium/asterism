"""Tests for the truncation/continuation behavior of the real LLM clients.

The bug these guard against: a large schema proposal ends with the §9 RML block
(the longest section). When the model hits ``max_tokens`` it stops mid-RML; the
old code returned that partial silently, so ``mapping.rml.ttl`` came out empty
with no signal. The fix raises the cap AND continues on a ``max_tokens`` stop,
splicing the parts into the full document — failing loud only if it's *still*
truncated after a safety cap.

These mock the SDK clients entirely (no network, no API key): a fake
``anthropic``/``openai`` module is injected into ``sys.modules`` so the lazy
import inside ``complete()`` picks up the stub. The stub yields a scripted
sequence of (text, stop_reason) tuples so we can simulate
``max_tokens`` → ``max_tokens`` → ``end_turn`` and a never-finishing case.
"""

from __future__ import annotations

import sys
import types

import pytest

from asterism_step0.llm import (
    AnthropicLLMClient,
    LLMTruncatedError,
    OpenAICompatibleLLMClient,
)

# ----------------------------------------------------------------------------
# Anthropic SDK stub
# ----------------------------------------------------------------------------


class _Block:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _Usage:
    def __init__(self) -> None:
        self.input_tokens = 10
        self.output_tokens = 5
        self.cache_read_input_tokens = 0
        self.cache_creation_input_tokens = 0


class _Message:
    def __init__(self, text: str, stop_reason: str) -> None:
        self.content = [_Block(text)]
        self.stop_reason = stop_reason
        self.usage = _Usage()


class _Stream:
    def __init__(self, message: _Message) -> None:
        self._message = message

    def __enter__(self) -> _Stream:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def get_final_message(self) -> _Message:
        return self._message


class _Messages:
    def __init__(self, scripted: list[tuple[str, str]]) -> None:
        self._scripted = iter(scripted)
        self.calls: list[list[dict[str, object]]] = []

    def stream(self, **kwargs: object) -> _Stream:
        # Record the messages passed so the test can assert the continuation
        # turns were spliced in.
        self.calls.append(list(kwargs["messages"]))  # type: ignore[arg-type]
        text, stop = next(self._scripted)
        return _Stream(_Message(text, stop))


class _FakeAnthropicClient:
    def __init__(self, scripted: list[tuple[str, str]]) -> None:
        self.messages = _Messages(scripted)


def _install_fake_anthropic(
    monkeypatch: pytest.MonkeyPatch, scripted: list[tuple[str, str]]
) -> _FakeAnthropicClient:
    fake_client = _FakeAnthropicClient(scripted)
    module = types.ModuleType("anthropic")

    def _Anthropic(**kwargs: object) -> _FakeAnthropicClient:
        return fake_client

    module.Anthropic = _Anthropic  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", module)
    return fake_client


# ----------------------------------------------------------------------------
# Anthropic continuation behavior
# ----------------------------------------------------------------------------


def test_anthropic_no_continuation_when_first_stop_is_end_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _install_fake_anthropic(monkeypatch, [("complete proposal", "end_turn")])
    client = AnthropicLLMClient(api_key="k")
    out = client.complete("sys", "user")
    assert out.text == "complete proposal"
    # Only one generation call (no continuation needed).
    assert len(fake.messages.calls) == 1


def test_anthropic_continues_on_max_tokens_then_concatenates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # First two generations hit the cap; the third finishes naturally. The §RML
    # block lives in the tail parts — the splice must include all three.
    fake = _install_fake_anthropic(
        monkeypatch,
        [
            ("## 1 head ", "max_tokens"),
            ("## 9 RML middle ", "max_tokens"),
            ("```turtle tail```", "end_turn"),
        ],
    )
    client = AnthropicLLMClient(api_key="k")
    out = client.complete("sys", "user")

    # The full document is the parts concatenated in order, no overlap/gap.
    assert out.text == "## 1 head ## 9 RML middle ```turtle tail```"
    # Three generation calls: the original + two continuations.
    assert len(fake.messages.calls) == 3

    # Continuation #1 fed the first partial back as an assistant turn + a
    # "continue" user turn (so the model resumes from where it stopped).
    second_call = fake.messages.calls[1]
    assert second_call[0] == {"role": "user", "content": "user"}
    assert second_call[1] == {"role": "assistant", "content": "## 1 head "}
    assert second_call[2]["role"] == "user"
    assert "cut off" in second_call[2]["content"]  # type: ignore[operator]

    # Usage is summed across all three generations (3 x output_tokens=5).
    assert out.usage.output_tokens == 15
    assert client.last_usage is not None
    assert client.last_usage.output_tokens == 15


def test_anthropic_raises_clear_error_when_never_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Every generation hits the cap and never reaches a natural stop. After the
    # continuation safety cap, fail LOUD rather than return a partial proposal.
    _install_fake_anthropic(
        monkeypatch, [("chunk", "max_tokens")] * 50
    )
    client = AnthropicLLMClient(api_key="k", max_continuations=3)
    with pytest.raises(LLMTruncatedError, match="too large to generate fully"):
        client.complete("sys", "user")


def test_anthropic_continuation_loop_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # With max_continuations=2 the loop makes at most 1 + 2 = 3 generations
    # before giving up — it must not spin forever on an always-truncated model.
    fake = _install_fake_anthropic(
        monkeypatch, [("chunk", "max_tokens")] * 50
    )
    client = AnthropicLLMClient(api_key="k", max_continuations=2)
    with pytest.raises(LLMTruncatedError):
        client.complete("sys", "user")
    assert len(fake.messages.calls) == 3  # 1 original + 2 continuations


# ----------------------------------------------------------------------------
# OpenAI-compatible SDK stub + continuation behavior
# ----------------------------------------------------------------------------


class _OAIMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _OAIChoice:
    def __init__(self, content: str, finish_reason: str) -> None:
        self.message = _OAIMessage(content)
        self.finish_reason = finish_reason


class _OAIUsage:
    def __init__(self) -> None:
        self.prompt_tokens = 10
        self.completion_tokens = 5
        self.prompt_tokens_details = None


class _OAIResponse:
    def __init__(self, content: str, finish_reason: str) -> None:
        self.choices = [_OAIChoice(content, finish_reason)]
        self.usage = _OAIUsage()


class _OAICompletions:
    def __init__(self, scripted: list[tuple[str, str]]) -> None:
        self._scripted = iter(scripted)
        self.calls: list[list[dict[str, object]]] = []

    def create(self, **kwargs: object) -> _OAIResponse:
        self.calls.append(list(kwargs["messages"]))  # type: ignore[arg-type]
        content, finish = next(self._scripted)
        return _OAIResponse(content, finish)


class _OAIChat:
    def __init__(self, scripted: list[tuple[str, str]]) -> None:
        self.completions = _OAICompletions(scripted)


class _FakeOpenAIClient:
    def __init__(self, scripted: list[tuple[str, str]]) -> None:
        self.chat = _OAIChat(scripted)


def _install_fake_openai(
    monkeypatch: pytest.MonkeyPatch, scripted: list[tuple[str, str]]
) -> _FakeOpenAIClient:
    fake_client = _FakeOpenAIClient(scripted)
    module = types.ModuleType("openai")

    def _OpenAI(**kwargs: object) -> _FakeOpenAIClient:
        return fake_client

    module.OpenAI = _OpenAI  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", module)
    return fake_client


def test_openai_continues_on_length_then_concatenates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _install_fake_openai(
        monkeypatch,
        [
            ("head ", "length"),
            ("tail", "stop"),
        ],
    )
    client = OpenAICompatibleLLMClient(api_key="k")
    out = client.complete("sys", "user")
    assert out.text == "head tail"
    assert len(fake.chat.completions.calls) == 2
    # Continuation appended the partial as an assistant turn after the original
    # system+user pair.
    second_call = fake.chat.completions.calls[1]
    assert second_call[0]["role"] == "system"
    assert second_call[1] == {"role": "user", "content": "user"}
    assert second_call[2] == {"role": "assistant", "content": "head "}


def test_openai_raises_clear_error_when_never_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_openai(monkeypatch, [("chunk", "length")] * 50)
    client = OpenAICompatibleLLMClient(api_key="k", max_continuations=3)
    with pytest.raises(LLMTruncatedError, match="too large to generate fully"):
        client.complete("sys", "user")
