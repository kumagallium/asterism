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
The OpenAI script also accepts Exception instances (raised by ``create()``),
prebuilt ``_OAIResponse`` objects, and ``_OAIStream`` objects (a fake
``openai.Stream``: a context manager over scripted chunks that records
close/exit), to exercise the compat-server robustness paths: token-param
fallback, context-length downgrade, reasoning (<think>) handling, cancel,
progress reporting, and the streaming transport (assembly across chunks,
``stream_options`` / ``stream`` rejection fallbacks, mid-stream cancel, the
``ASTERISM_LLM_STREAM=0`` kill-switch).

The OpenAI-compatible client streams BY DEFAULT; tests that script plain
non-streaming responses pin the legacy transport via the ``nonstreaming``
fixture, which doubles as coverage of the kill-switch path.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Iterator

import pytest

from asterism_step0.llm import (
    DEFAULT_REQUEST_TIMEOUT,
    DEFAULT_SDK_RETRIES,
    AnthropicLLMClient,
    LLMCancelledError,
    LLMEmptyOutputError,
    LLMTruncatedError,
    OpenAICompatibleLLMClient,
    _streaming_enabled,
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


class _OAIStreamDelta:
    def __init__(
        self,
        content: str | None = None,
        reasoning_content: str | None = None,
        model_extra: dict[str, object] | None = None,
    ) -> None:
        self.content = content
        self.reasoning_content = reasoning_content
        self.model_extra = model_extra


class _OAIStreamChoice:
    def __init__(self, delta: _OAIStreamDelta, finish_reason: str | None = None) -> None:
        self.delta = delta
        self.finish_reason = finish_reason


class _OAIStreamChunk:
    """One SSE chunk: a content/reasoning delta, a finish_reason, and/or usage.

    Pass ``choices=[]`` (with ``usage=``) to build the FINAL include_usage
    chunk, whose choices list is empty in the real protocol.
    """

    def __init__(
        self,
        content: str | None = None,
        *,
        reasoning_content: str | None = None,
        model_extra: dict[str, object] | None = None,
        finish_reason: str | None = None,
        usage: _OAIUsage | None = None,
        choices: list[_OAIStreamChoice] | None = None,
    ) -> None:
        if choices is None:
            choices = [
                _OAIStreamChoice(
                    _OAIStreamDelta(content, reasoning_content, model_extra), finish_reason
                )
            ]
        self.choices = choices
        self.usage = usage


def _usage_chunk() -> _OAIStreamChunk:
    """The final include_usage chunk: EMPTY choices list + the usage payload."""
    return _OAIStreamChunk(choices=[], usage=_OAIUsage())


class _OAIStream:
    """Fake ``openai.Stream``: a context manager iterating scripted chunks.

    Records enter/close so tests can assert the client used the ``with`` block
    (closing the HTTP stream is what aborts an in-flight generation on cancel).
    """

    def __init__(self, chunks: list[_OAIStreamChunk]) -> None:
        self._chunks = chunks
        self.entered = False
        self.closed = False

    def __enter__(self) -> _OAIStream:
        self.entered = True
        return self

    def __exit__(self, *exc: object) -> None:
        self.closed = True
        return None

    def __iter__(self) -> Iterator[_OAIStreamChunk]:
        return iter(self._chunks)


# A scripted item is a (content, finish_reason) tuple, a prebuilt _OAIResponse
# (for reasoning_content / model_extra cases), a prebuilt _OAIStream (for the
# streaming transport), or an Exception to raise.
_ScriptItem = tuple[str, str] | _OAIResponse | _OAIStream | Exception


class _OAICompletions:
    def __init__(self, scripted: list[_ScriptItem]) -> None:
        self._scripted = iter(scripted)
        self.calls: list[list[dict[str, object]]] = []
        # Full kwargs of each create() call, so tests can assert which token
        # parameter was sent and with what value — and whether stream /
        # stream_options were passed.
        self.kwargs_calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> _OAIResponse | _OAIStream:
        self.calls.append(list(kwargs["messages"]))  # type: ignore[arg-type]
        self.kwargs_calls.append(dict(kwargs))
        item = next(self._scripted)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, _OAIResponse | _OAIStream):
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


@pytest.fixture
def nonstreaming(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the non-streaming transport (kill-switch) for tests whose scripts
    return plain ``_OAIResponse`` objects — they keep covering that path now
    that the client streams by default."""
    monkeypatch.setenv("ASTERISM_LLM_STREAM", "0")


@pytest.mark.usefixtures("nonstreaming")
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


@pytest.mark.usefixtures("nonstreaming")
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


@pytest.mark.usefixtures("nonstreaming")
def test_openai_passes_explicit_timeout_and_retries_to_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ASTERISM_LLM_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("ASTERISM_LLM_MAX_RETRIES", raising=False)
    fake = _install_fake_openai(monkeypatch, [("ok", "stop")])
    OpenAICompatibleLLMClient(api_key="k").complete("sys", "user")
    assert fake.constructor_kwargs["timeout"] == DEFAULT_REQUEST_TIMEOUT
    assert fake.constructor_kwargs["max_retries"] == DEFAULT_SDK_RETRIES


@pytest.mark.usefixtures("nonstreaming")
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


@pytest.mark.usefixtures("nonstreaming")
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


@pytest.mark.usefixtures("nonstreaming")
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


@pytest.mark.usefixtures("nonstreaming")
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


@pytest.mark.usefixtures("nonstreaming")
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


@pytest.mark.usefixtures("nonstreaming")
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


@pytest.mark.usefixtures("nonstreaming")
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


@pytest.mark.usefixtures("nonstreaming")
def test_openai_strips_closed_think_tags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_openai(
        monkeypatch, [("<think>secret chain of thought</think>\nreal answer", "stop")]
    )
    out = OpenAICompatibleLLMClient(api_key="k").complete("sys", "user")
    assert out.text == "real answer"


@pytest.mark.usefixtures("nonstreaming")
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


@pytest.mark.usefixtures("nonstreaming")
def test_openai_unclosed_think_tag_is_all_reasoning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An unclosed <think> means the answer never started; with a natural stop
    # on the first generation there is no usable output at all.
    _install_fake_openai(monkeypatch, [("<think>still thinking about the schema", "stop")])
    with pytest.raises(LLMEmptyOutputError, match="empty response"):
        OpenAICompatibleLLMClient(api_key="k").complete("sys", "user")


@pytest.mark.usefixtures("nonstreaming")
def test_openai_unclosed_think_with_length_raises_reasoning_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _install_fake_openai(monkeypatch, [("<think>reason reason reason", "length")])
    with pytest.raises(LLMTruncatedError, match="output budget"):
        OpenAICompatibleLLMClient(api_key="k").complete("sys", "user")
    # It must NOT loop with an empty assistant turn.
    assert len(fake.chat.completions.calls) == 1


@pytest.mark.usefixtures("nonstreaming")
def test_openai_empty_content_with_length_raises_reasoning_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _install_fake_openai(monkeypatch, [("", "length")])
    client = OpenAICompatibleLLMClient(api_key="k")
    with pytest.raises(LLMTruncatedError, match="reasoning"):
        client.complete("sys", "user")
    assert len(fake.chat.completions.calls) == 1


@pytest.mark.usefixtures("nonstreaming")
def test_openai_reasoning_only_response_raises_empty_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resp = _OAIResponse("", "stop", reasoning_content="I pondered the schema at length")
    _install_fake_openai(monkeypatch, [resp])
    with pytest.raises(LLMEmptyOutputError, match="only reasoning"):
        OpenAICompatibleLLMClient(api_key="k").complete("sys", "user")


@pytest.mark.usefixtures("nonstreaming")
def test_openai_model_extra_reasoning_detected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resp = _OAIResponse("", "stop", model_extra={"reasoning": "chain of thought"})
    _install_fake_openai(monkeypatch, [resp])
    with pytest.raises(LLMEmptyOutputError, match="only reasoning"):
        OpenAICompatibleLLMClient(api_key="k").complete("sys", "user")


@pytest.mark.usefixtures("nonstreaming")
def test_openai_plain_empty_response_raises_empty_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_openai(monkeypatch, [("", "stop")])
    with pytest.raises(LLMEmptyOutputError, match="empty response"):
        OpenAICompatibleLLMClient(api_key="k").complete("sys", "user")


@pytest.mark.usefixtures("nonstreaming")
def test_openai_empty_continuation_is_natural_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A continuation that returns no new text with a natural stop means the
    # document was already complete — return the collected text, no error.
    fake = _install_fake_openai(monkeypatch, [("head ", "length"), ("", "stop")])
    out = OpenAICompatibleLLMClient(api_key="k").complete("sys", "user")
    assert out.text == "head "
    assert len(fake.chat.completions.calls) == 2


# ----------------------------------------------------------------------------
# OpenAI-compatible streaming transport (the default)
# ----------------------------------------------------------------------------


def _default_stream_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure the streaming default is in effect regardless of the host env."""
    monkeypatch.delenv("ASTERISM_LLM_STREAM", raising=False)


def test_streaming_enabled_env_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    _default_stream_env(monkeypatch)
    assert _streaming_enabled() is True
    for off in ("0", "false", "FALSE", "no", "No"):
        monkeypatch.setenv("ASTERISM_LLM_STREAM", off)
        assert _streaming_enabled() is False, off
    # Invalid / affirmative values fall back to the default (streaming ON).
    for on in ("1", "true", "yes", "banana", ""):
        monkeypatch.setenv("ASTERISM_LLM_STREAM", on)
        assert _streaming_enabled() is True, on


def test_openai_streams_by_default_and_assembles_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _default_stream_env(monkeypatch)
    # finish_reason arrives on a content chunk; usage arrives later, on the
    # final chunk whose choices list is EMPTY — both must be picked up.
    stream = _OAIStream(
        [
            _OAIStreamChunk("Hello "),
            _OAIStreamChunk("world", finish_reason="stop"),
            _usage_chunk(),
        ]
    )
    fake = _install_fake_openai(monkeypatch, [stream])
    client = OpenAICompatibleLLMClient(api_key="k")
    out = client.complete("sys", "user")
    assert out.text == "Hello world"
    assert out.usage.input_tokens == 10
    assert out.usage.output_tokens == 5
    assert client.last_usage == out.usage
    kw = fake.chat.completions.kwargs_calls[0]
    assert kw["stream"] is True
    assert kw["stream_options"] == {"include_usage": True}
    assert stream.entered and stream.closed  # consumed via the context manager


def test_openai_stream_options_rejected_retries_streaming_without_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _default_stream_env(monkeypatch)
    err = _FakeBadRequestError("Unknown parameter: 'stream_options'.")
    # The retry stays streaming; this server never reports usage (no usage chunk).
    stream = _OAIStream([_OAIStreamChunk("answer", finish_reason="stop")])
    fake = _install_fake_openai(monkeypatch, [err, stream])
    client = OpenAICompatibleLLMClient(api_key="k")
    out = client.complete("sys", "user")
    assert out.text == "answer"
    kw = fake.chat.completions.kwargs_calls
    assert kw[0]["stream"] is True and "stream_options" in kw[0]
    assert kw[1]["stream"] is True and "stream_options" not in kw[1]
    assert client.last_notes == [
        "usage reporting disabled (server rejected stream_options)"
    ]
    # Zero usage is tolerated — the ledger records nothing rather than failing.
    assert out.usage.total_tokens == 0


def test_openai_streaming_rejected_falls_back_to_non_streaming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _default_stream_env(monkeypatch)
    err = _FakeBadRequestError("This model does not support streaming.")
    fake = _install_fake_openai(monkeypatch, [err, ("answer", "stop")])
    client = OpenAICompatibleLLMClient(api_key="k")
    live_notes: list[str] = []
    client.on_note = live_notes.append
    out = client.complete("sys", "user")
    assert out.text == "answer"
    kw = fake.chat.completions.kwargs_calls
    assert kw[0]["stream"] is True
    assert "stream" not in kw[1] and "stream_options" not in kw[1]
    assert client.last_notes == ["streaming disabled (server rejected stream=true)"]
    assert live_notes == client.last_notes
    # The non-streaming fallback still reports usage off the response object.
    assert out.usage.output_tokens == 5


def test_openai_kill_switch_env_disables_streaming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _install_fake_openai(monkeypatch, [("ok", "stop")])
    monkeypatch.setenv("ASTERISM_LLM_STREAM", "0")
    out = OpenAICompatibleLLMClient(api_key="k").complete("sys", "user")
    assert out.text == "ok"
    kw = fake.chat.completions.kwargs_calls[0]
    assert "stream" not in kw and "stream_options" not in kw


def test_openai_think_tag_split_across_chunks_is_stripped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _default_stream_env(monkeypatch)
    # The <think> tag straddles chunk boundaries; the strip runs on the JOINED
    # text so it must still be removed.
    stream = _OAIStream(
        [
            _OAIStreamChunk("<thi"),
            _OAIStreamChunk("nk>x</think>ans"),
            _OAIStreamChunk("wer", finish_reason="stop"),
            _usage_chunk(),
        ]
    )
    _install_fake_openai(monkeypatch, [stream])
    out = OpenAICompatibleLLMClient(api_key="k").complete("sys", "user")
    assert out.text == "answer"


def test_openai_streamed_reasoning_only_with_stop_raises_empty_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _default_stream_env(monkeypatch)
    # Out-of-band reasoning deltas, no content deltas, natural stop: the model
    # reasoned but never answered.
    stream = _OAIStream(
        [
            _OAIStreamChunk(reasoning_content="pondering "),
            _OAIStreamChunk(reasoning_content="the schema", finish_reason="stop"),
            _usage_chunk(),
        ]
    )
    _install_fake_openai(monkeypatch, [stream])
    with pytest.raises(LLMEmptyOutputError, match="only reasoning"):
        OpenAICompatibleLLMClient(api_key="k").complete("sys", "user")


def test_openai_streamed_model_extra_reasoning_detected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _default_stream_env(monkeypatch)
    stream = _OAIStream(
        [
            _OAIStreamChunk(model_extra={"reasoning": "chain of thought"}),
            _OAIStreamChunk(finish_reason="stop"),
            _usage_chunk(),
        ]
    )
    _install_fake_openai(monkeypatch, [stream])
    with pytest.raises(LLMEmptyOutputError, match="only reasoning"):
        OpenAICompatibleLLMClient(api_key="k").complete("sys", "user")


def test_openai_streamed_reasoning_only_with_length_raises_reasoning_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _default_stream_env(monkeypatch)
    stream = _OAIStream(
        [
            _OAIStreamChunk(reasoning_content="reason reason", finish_reason="length"),
            _usage_chunk(),
        ]
    )
    fake = _install_fake_openai(monkeypatch, [stream])
    with pytest.raises(LLMTruncatedError, match="output budget"):
        OpenAICompatibleLLMClient(api_key="k").complete("sys", "user")
    # It must NOT loop with an empty assistant turn.
    assert len(fake.chat.completions.calls) == 1


def test_openai_streamed_continuation_across_generations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _default_stream_env(monkeypatch)
    first = _OAIStream(
        [
            _OAIStreamChunk("head "),
            _OAIStreamChunk(finish_reason="length"),
            _usage_chunk(),
        ]
    )
    second = _OAIStream(
        [
            _OAIStreamChunk("tail", finish_reason="stop"),
            _usage_chunk(),
        ]
    )
    fake = _install_fake_openai(monkeypatch, [first, second])
    client = OpenAICompatibleLLMClient(api_key="k")
    out = client.complete("sys", "user")
    assert out.text == "head tail"
    calls = fake.chat.completions.calls
    assert len(calls) == 2
    # The continuation appended the partial as an assistant turn + the
    # "continue" user turn after the original system+user pair.
    assert calls[1][2] == {"role": "assistant", "content": "head "}
    assert calls[1][3]["role"] == "user"
    assert "cut off" in calls[1][3]["content"]  # type: ignore[operator]
    for kw in fake.chat.completions.kwargs_calls:
        assert kw["stream"] is True
    # Usage summed across both generations (2 x completion_tokens=5).
    assert out.usage.output_tokens == 10


def test_openai_cancel_mid_stream_raises_and_closes_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _default_stream_env(monkeypatch)
    stream = _OAIStream(
        [
            _OAIStreamChunk("head "),
            _OAIStreamChunk("tail", finish_reason="stop"),
            _usage_chunk(),
        ]
    )
    fake = _install_fake_openai(monkeypatch, [stream])
    client = OpenAICompatibleLLMClient(api_key="k")
    # Poll #1 = loop top (before the HTTP call), poll #2 = before chunk 1,
    # poll #3 = before chunk 2 → cancel takes effect between chunks.
    answers = iter([False, False, True])
    client.should_cancel = lambda: next(answers)
    with pytest.raises(LLMCancelledError):
        client.complete("sys", "user")
    assert len(fake.chat.completions.calls) == 1
    # The context manager exited → the HTTP stream was closed, which is what
    # aborts the in-flight generation on vLLM-class servers.
    assert stream.closed is True


def test_openai_token_param_and_context_downgrade_work_while_streaming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _default_stream_env(monkeypatch)
    param_err = _FakeBadRequestError(
        "Unsupported parameter: 'max_tokens' is not supported with this model. "
        "Use 'max_completion_tokens' instead."
    )
    ctx_err = _FakeBadRequestError(
        "This model's maximum context length is 32768 tokens; please reduce "
        "the length of the messages or completion."
    )
    stream = _OAIStream(
        [_OAIStreamChunk("ok", finish_reason="stop"), _usage_chunk()]
    )
    fake = _install_fake_openai(monkeypatch, [param_err, ctx_err, stream])
    client = OpenAICompatibleLLMClient(api_key="k")
    out = client.complete("sys", "user")
    assert out.text == "ok"
    kw = fake.chat.completions.kwargs_calls
    assert kw[0]["max_tokens"] == 96000 and kw[0]["stream"] is True
    assert kw[1]["max_completion_tokens"] == 96000 and kw[1]["stream"] is True
    assert kw[2]["max_completion_tokens"] == 48000 and kw[2]["stream"] is True
    assert client.last_notes == [
        "max_tokens 96000 -> 48000 after provider context-length rejection"
    ]


def test_openai_stream_without_usage_chunk_records_zero_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _default_stream_env(monkeypatch)
    stream = _OAIStream([_OAIStreamChunk("ok", finish_reason="stop")])
    _install_fake_openai(monkeypatch, [stream])
    client = OpenAICompatibleLLMClient(api_key="k")
    out = client.complete("sys", "user")
    assert out.text == "ok"
    assert out.usage.total_tokens == 0
    assert client.last_usage is not None
    assert client.last_usage.total_tokens == 0


# ---------------------------------------------------------------------------
# Structured output (Phase 2 guided repair): response_format wiring + degrade
# ---------------------------------------------------------------------------

_SCHEMA = {"type": "object", "properties": {"x": {"type": "string"}}}


@pytest.mark.usefixtures("nonstreaming")
def test_response_schema_sends_json_schema_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _install_fake_openai(monkeypatch, [('{"x": "ok"}', "stop")])
    client = OpenAICompatibleLLMClient(model="m", api_key="k")
    client.response_schema = _SCHEMA
    out = client.complete("sys", "user")
    assert out.text == '{"x": "ok"}'
    sent = fake.chat.completions.kwargs_calls[0]["response_format"]
    assert sent == {
        "type": "json_schema",
        "json_schema": {"name": "mapping_spec", "schema": _SCHEMA},
    }


@pytest.mark.usefixtures("nonstreaming")
def test_response_schema_degrades_to_json_object_then_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _install_fake_openai(
        monkeypatch,
        [
            _FakeBadRequestError("response_format json_schema is not supported"),
            _FakeBadRequestError("response_format is not supported"),
            ('{"x": "ok"}', "stop"),
        ],
    )
    client = OpenAICompatibleLLMClient(model="m", api_key="k")
    client.response_schema = _SCHEMA
    out = client.complete("sys", "user")
    assert out.text == '{"x": "ok"}'
    kw = fake.chat.completions.kwargs_calls
    assert kw[0]["response_format"]["type"] == "json_schema"  # type: ignore[index]
    assert kw[1]["response_format"] == {"type": "json_object"}
    assert "response_format" not in kw[2]
    assert any("json_object" in n for n in client.last_notes)
    assert any("structured output disabled" in n for n in client.last_notes)


@pytest.mark.usefixtures("nonstreaming")
def test_no_response_schema_sends_no_response_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _install_fake_openai(monkeypatch, [("plain", "stop")])
    client = OpenAICompatibleLLMClient(model="m", api_key="k")
    client.complete("sys", "user")
    assert "response_format" not in fake.chat.completions.kwargs_calls[0]
