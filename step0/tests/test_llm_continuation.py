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
The OpenAI script also accepts Exception instances (raised by ``create()``)
and prebuilt ``_OAIResponse`` objects, to exercise the compat-server
robustness paths: token-param fallback, context-length downgrade, reasoning
(<think>) handling, cancel, and progress reporting.
"""

from __future__ import annotations

import sys
import types

import pytest

from asterism_step0.llm import (
    DEFAULT_REQUEST_TIMEOUT,
    DEFAULT_SDK_RETRIES,
    AnthropicLLMClient,
    LLMCancelledError,
    LLMEmptyOutputError,
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
        # kwargs the SDK constructor was called with (timeout / max_retries …).
        self.constructor_kwargs: dict[str, object] = {}


def _install_fake_anthropic(
    monkeypatch: pytest.MonkeyPatch, scripted: list[tuple[str, str]]
) -> _FakeAnthropicClient:
    fake_client = _FakeAnthropicClient(scripted)
    module = types.ModuleType("anthropic")

    def _Anthropic(**kwargs: object) -> _FakeAnthropicClient:
        fake_client.constructor_kwargs = dict(kwargs)
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


class _FakeBadRequestError(Exception):
    """Stands in for ``openai.BadRequestError`` in the stub module."""


class _OAIMessage:
    def __init__(
        self,
        content: str,
        reasoning_content: str | None = None,
        model_extra: dict[str, object] | None = None,
    ) -> None:
        self.content = content
        self.reasoning_content = reasoning_content
        self.model_extra = model_extra


class _OAIChoice:
    def __init__(self, message: _OAIMessage, finish_reason: str) -> None:
        self.message = message
        self.finish_reason = finish_reason


class _OAIUsage:
    def __init__(self) -> None:
        self.prompt_tokens = 10
        self.completion_tokens = 5
        self.prompt_tokens_details = None


class _OAIResponse:
    def __init__(
        self,
        content: str,
        finish_reason: str,
        *,
        reasoning_content: str | None = None,
        model_extra: dict[str, object] | None = None,
    ) -> None:
        message = _OAIMessage(content, reasoning_content, model_extra)
        self.choices = [_OAIChoice(message, finish_reason)]
        self.usage = _OAIUsage()


# A scripted item is a (content, finish_reason) tuple, a prebuilt _OAIResponse
# (for reasoning_content / model_extra cases), or an Exception to raise.
_ScriptItem = tuple[str, str] | _OAIResponse | Exception


class _OAICompletions:
    def __init__(self, scripted: list[_ScriptItem]) -> None:
        self._scripted = iter(scripted)
        self.calls: list[list[dict[str, object]]] = []
        # Full kwargs of each create() call, so tests can assert which token
        # parameter was sent and with what value.
        self.kwargs_calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> _OAIResponse:
        self.calls.append(list(kwargs["messages"]))  # type: ignore[arg-type]
        self.kwargs_calls.append(dict(kwargs))
        item = next(self._scripted)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, _OAIResponse):
            return item
        content, finish = item
        return _OAIResponse(content, finish)


class _OAIChat:
    def __init__(self, scripted: list[_ScriptItem]) -> None:
        self.completions = _OAICompletions(scripted)


class _FakeOpenAIClient:
    def __init__(self, scripted: list[_ScriptItem]) -> None:
        self.chat = _OAIChat(scripted)
        # kwargs the SDK constructor was called with (timeout / max_retries …).
        self.constructor_kwargs: dict[str, object] = {}


def _install_fake_openai(
    monkeypatch: pytest.MonkeyPatch, scripted: list[_ScriptItem]
) -> _FakeOpenAIClient:
    fake_client = _FakeOpenAIClient(scripted)
    module = types.ModuleType("openai")

    def _OpenAI(**kwargs: object) -> _FakeOpenAIClient:
        fake_client.constructor_kwargs = dict(kwargs)
        return fake_client

    module.OpenAI = _OpenAI  # type: ignore[attr-defined]
    module.BadRequestError = _FakeBadRequestError  # type: ignore[attr-defined]
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


# ----------------------------------------------------------------------------
# Explicit SDK client settings (timeout / max_retries)
# ----------------------------------------------------------------------------


def test_anthropic_passes_explicit_timeout_and_retries_to_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ASTERISM_LLM_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("ASTERISM_LLM_MAX_RETRIES", raising=False)
    fake = _install_fake_anthropic(monkeypatch, [("ok", "end_turn")])
    AnthropicLLMClient(api_key="k").complete("sys", "user")
    assert fake.constructor_kwargs["timeout"] == DEFAULT_REQUEST_TIMEOUT
    assert fake.constructor_kwargs["max_retries"] == DEFAULT_SDK_RETRIES


def test_openai_passes_explicit_timeout_and_retries_to_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ASTERISM_LLM_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("ASTERISM_LLM_MAX_RETRIES", raising=False)
    fake = _install_fake_openai(monkeypatch, [("ok", "stop")])
    OpenAICompatibleLLMClient(api_key="k").complete("sys", "user")
    assert fake.constructor_kwargs["timeout"] == DEFAULT_REQUEST_TIMEOUT
    assert fake.constructor_kwargs["max_retries"] == DEFAULT_SDK_RETRIES


def test_sdk_settings_env_overrides_are_read_lazily(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The env is read inside complete(), not at import/construct time, so a
    # monkeypatched override set after the client is built still applies.
    fake = _install_fake_openai(monkeypatch, [("ok", "stop")])
    client = OpenAICompatibleLLMClient(api_key="k")
    monkeypatch.setenv("ASTERISM_LLM_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("ASTERISM_LLM_MAX_RETRIES", "0")
    client.complete("sys", "user")
    assert fake.constructor_kwargs["timeout"] == 30.0
    assert fake.constructor_kwargs["max_retries"] == 0


# ----------------------------------------------------------------------------
# Cooperative cancel + generation progress
# ----------------------------------------------------------------------------


def test_openai_cancel_raises_before_any_http_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _install_fake_openai(monkeypatch, [("never used", "stop")])
    client = OpenAICompatibleLLMClient(api_key="k")
    client.should_cancel = lambda: True
    with pytest.raises(LLMCancelledError):
        client.complete("sys", "user")
    assert fake.chat.completions.calls == []


def test_anthropic_cancel_raises_before_any_http_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _install_fake_anthropic(monkeypatch, [("never used", "end_turn")])
    client = AnthropicLLMClient(api_key="k")
    client.should_cancel = lambda: True
    with pytest.raises(LLMCancelledError):
        client.complete("sys", "user")
    assert fake.messages.calls == []


def test_openai_cancel_between_continuations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # First poll allows generation 1; the second poll (before generation 2)
    # cancels — exactly one HTTP call is made.
    fake = _install_fake_openai(monkeypatch, [("head ", "length"), ("tail", "stop")])
    client = OpenAICompatibleLLMClient(api_key="k")
    answers = iter([False, True])
    client.should_cancel = lambda: next(answers)
    with pytest.raises(LLMCancelledError):
        client.complete("sys", "user")
    assert len(fake.chat.completions.calls) == 1


def test_openai_on_generation_reports_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _install_fake_openai(monkeypatch, [("head ", "length"), ("tail", "stop")])
    client = OpenAICompatibleLLMClient(api_key="k")
    seen: list[tuple[int, int]] = []
    client.on_generation = lambda current, total: seen.append((current, total))
    out = client.complete("sys", "user")
    assert out.text == "head tail"
    # Default max_continuations=5 → 6 possible generations in total.
    assert seen == [(1, 6), (2, 6)]
    assert len(fake.chat.completions.calls) == 2


def test_anthropic_on_generation_called_and_broken_callback_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_anthropic(monkeypatch, [("ok", "end_turn")])
    client = AnthropicLLMClient(api_key="k")
    seen: list[tuple[int, int]] = []

    def broken(current: int, total: int) -> None:
        seen.append((current, total))
        raise RuntimeError("boom")

    client.on_generation = broken
    out = client.complete("sys", "user")
    # The callback raised but the completion still succeeded.
    assert out.text == "ok"
    assert seen == [(1, 6)]


# ----------------------------------------------------------------------------
# OpenAI-compatible robustness: token-param fallback
# ----------------------------------------------------------------------------


def test_openai_switches_to_max_completion_tokens_and_remembers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    err = _FakeBadRequestError(
        "Unsupported parameter: 'max_tokens' is not supported with this model. "
        "Use 'max_completion_tokens' instead."
    )
    fake = _install_fake_openai(monkeypatch, [err, ("head ", "length"), ("tail", "stop")])
    client = OpenAICompatibleLLMClient(api_key="k")
    out = client.complete("sys", "user")
    assert out.text == "head tail"
    kw = fake.chat.completions.kwargs_calls
    # First attempt used max_tokens; the retry did not consume a continuation
    # slot and every later request (including the continuation) uses the
    # switched parameter.
    assert "max_tokens" in kw[0] and "max_completion_tokens" not in kw[0]
    for later in kw[1:]:
        assert "max_completion_tokens" in later and "max_tokens" not in later
    assert len(kw) == 3  # rejected attempt + retry + one continuation


def test_openai_token_param_fallback_checked_before_context_downgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A message matching BOTH patterns must switch the parameter, not halve the
    # cap (the "use max_completion_tokens" wording also talks about tokens).
    err = _FakeBadRequestError(
        "unsupported_parameter: max_tokens — too many tokens for this endpoint"
    )
    fake = _install_fake_openai(monkeypatch, [err, ("ok", "stop")])
    client = OpenAICompatibleLLMClient(api_key="k")
    out = client.complete("sys", "user")
    assert out.text == "ok"
    assert fake.chat.completions.kwargs_calls[1]["max_completion_tokens"] == 96000
    assert client.last_notes == []


# ----------------------------------------------------------------------------
# OpenAI-compatible robustness: context-length auto-downgrade
# ----------------------------------------------------------------------------


def test_openai_context_length_downgrade_halves_and_notes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    err = _FakeBadRequestError(
        "This model's maximum context length is 32768 tokens; please reduce "
        "the length of the messages or completion."
    )
    fake = _install_fake_openai(monkeypatch, [err, ("ok", "stop")])
    client = OpenAICompatibleLLMClient(api_key="k")  # max_tokens = 96000
    live_notes: list[str] = []
    client.on_note = live_notes.append
    out = client.complete("sys", "user")
    assert out.text == "ok"
    kw = fake.chat.completions.kwargs_calls
    assert kw[0]["max_tokens"] == 96000
    assert kw[1]["max_tokens"] == 48000
    assert client.last_notes == [
        "max_tokens 96000 -> 48000 after provider context-length rejection"
    ]
    assert live_notes == client.last_notes


def test_openai_context_length_downgrade_floors_at_4096(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    err = _FakeBadRequestError("context_length_exceeded")
    fake = _install_fake_openai(monkeypatch, [err, ("ok", "stop")])
    client = OpenAICompatibleLLMClient(api_key="k", max_tokens=6000)
    out = client.complete("sys", "user")
    assert out.text == "ok"
    # 6000 // 2 = 3000 < 4096 → floored.
    assert fake.chat.completions.kwargs_calls[1]["max_tokens"] == 4096


def test_openai_context_length_gives_up_after_four_halvings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    errs: list[_ScriptItem] = [
        _FakeBadRequestError("context_length_exceeded") for _ in range(5)
    ]
    fake = _install_fake_openai(monkeypatch, errs)
    client = OpenAICompatibleLLMClient(api_key="k")
    # After 4 halvings the 5th rejection is re-raised unchanged.
    with pytest.raises(_FakeBadRequestError):
        client.complete("sys", "user")
    caps = [kw["max_tokens"] for kw in fake.chat.completions.kwargs_calls]
    assert caps == [96000, 48000, 24000, 12000, 6000]
    assert len(client.last_notes) == 4


# ----------------------------------------------------------------------------
# OpenAI-compatible robustness: reasoning models (<think> leakage, empty output)
# ----------------------------------------------------------------------------


def test_openai_strips_closed_think_tags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_openai(
        monkeypatch, [("<think>secret chain of thought</think>\nreal answer", "stop")]
    )
    out = OpenAICompatibleLLMClient(api_key="k").complete("sys", "user")
    assert out.text == "real answer"


def test_openai_continuation_turn_uses_stripped_part(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _install_fake_openai(
        monkeypatch, [("<think>r</think>head ", "length"), ("tail", "stop")]
    )
    out = OpenAICompatibleLLMClient(api_key="k").complete("sys", "user")
    assert out.text == "head tail"
    # The assistant turn fed back for continuation must be the stripped part.
    second_call = fake.chat.completions.calls[1]
    assert second_call[2] == {"role": "assistant", "content": "head "}


def test_openai_unclosed_think_tag_is_all_reasoning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An unclosed <think> means the answer never started; with a natural stop
    # on the first generation there is no usable output at all.
    _install_fake_openai(monkeypatch, [("<think>still thinking about the schema", "stop")])
    with pytest.raises(LLMEmptyOutputError, match="empty response"):
        OpenAICompatibleLLMClient(api_key="k").complete("sys", "user")


def test_openai_unclosed_think_with_length_raises_reasoning_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _install_fake_openai(monkeypatch, [("<think>reason reason reason", "length")])
    with pytest.raises(LLMTruncatedError, match="output budget"):
        OpenAICompatibleLLMClient(api_key="k").complete("sys", "user")
    # It must NOT loop with an empty assistant turn.
    assert len(fake.chat.completions.calls) == 1


def test_openai_empty_content_with_length_raises_reasoning_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _install_fake_openai(monkeypatch, [("", "length")])
    client = OpenAICompatibleLLMClient(api_key="k")
    with pytest.raises(LLMTruncatedError, match="reasoning"):
        client.complete("sys", "user")
    assert len(fake.chat.completions.calls) == 1


def test_openai_reasoning_only_response_raises_empty_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resp = _OAIResponse("", "stop", reasoning_content="I pondered the schema at length")
    _install_fake_openai(monkeypatch, [resp])
    with pytest.raises(LLMEmptyOutputError, match="only reasoning"):
        OpenAICompatibleLLMClient(api_key="k").complete("sys", "user")


def test_openai_model_extra_reasoning_detected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resp = _OAIResponse("", "stop", model_extra={"reasoning": "chain of thought"})
    _install_fake_openai(monkeypatch, [resp])
    with pytest.raises(LLMEmptyOutputError, match="only reasoning"):
        OpenAICompatibleLLMClient(api_key="k").complete("sys", "user")


def test_openai_plain_empty_response_raises_empty_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_openai(monkeypatch, [("", "stop")])
    with pytest.raises(LLMEmptyOutputError, match="empty response"):
        OpenAICompatibleLLMClient(api_key="k").complete("sys", "user")


def test_openai_empty_continuation_is_natural_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A continuation that returns no new text with a natural stop means the
    # document was already complete — return the collected text, no error.
    fake = _install_fake_openai(monkeypatch, [("head ", "length"), ("", "stop")])
    out = OpenAICompatibleLLMClient(api_key="k").complete("sys", "user")
    assert out.text == "head "
    assert len(fake.chat.completions.calls) == 2
