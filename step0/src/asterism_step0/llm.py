"""Multi-provider LLM client seam for asterism Step 0.

A tiny, mockable abstraction over chat LLMs. The api builds one client per
request from user-brought coordinates (provider / model / api_base / key) via
:func:`make_llm` and passes it to the Step 0 functions (propose / refine /
tool.propose / crosswalk.propose), each of which makes a single
``complete(system, user)`` call and uses the returned text.

Providers:
  * ``anthropic`` (default) — Claude via the Anthropic SDK, streamed, with
    adaptive thinking + prompt caching. ``make_llm(None, ...)`` reproduces the
    original Anthropic-only behavior byte-for-byte, so requests that send no
    provider header are unaffected.
  * ``openai`` / ``openai-compatible`` — any OpenAI Chat Completions endpoint.
    ``openai-compatible`` is how a custom ``base_url`` plugs in: Sakura AI Engine
    (国内向け), Groq, Ollama, vLLM, LM Studio, … No Anthropic-only params
    (``thinking`` / ``output_config`` / ``cache_control``) are ever sent there.

Token usage (input / output / cache) is captured off each response and exposed
two ways: on the returned :class:`LLMCompletion` AND on the client's
``last_usage`` attribute. The api reads ``last_usage`` to append one event to the
usage ledger (:mod:`asterism_api.usage`) without threading usage through every
Step 0 return type — cost itself is computed in the UI from a user-editable rate
table at display time.

The SDKs are lazy-imported inside ``complete()`` so this module — and the whole
``asterism_step0`` package — stays importable (and unit-testable with a mock)
without ``anthropic`` / ``openai`` present.
"""

from __future__ import annotations

import contextlib
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

# Default model ids per provider, used when the caller does not pin one. The
# Anthropic default matches the historical hard-coded value so the no-provider
# path is unchanged.
DEFAULT_ANTHROPIC_MODEL = "claude-opus-4-7"
DEFAULT_OPENAI_MODEL = "gpt-4o"

# Output token cap for a single generation. A full schema proposal ends with the
# §9 RML mapping (the longest block), so the cap must be generous: a small cap
# truncates the response mid-RML and yields an EMPTY mapping.rml.ttl with no
# clear signal (the bug this default guards against). claude-opus-4-7 / 4-8 both
# support up to 128K output tokens; we leave headroom below that for adaptive
# thinking tokens and rely on streaming (no ~10-min non-streaming timeout) plus
# the continuation loop below to finish proposals that still exceed one cap.
DEFAULT_MAX_TOKENS = 96000

# How many times complete() will ask the model to CONTINUE after a max_tokens
# stop before giving up. A large proposal usually finishes within one or two
# continuations; the cap bounds cost and prevents an infinite loop if the model
# never reaches a natural stop.
MAX_CONTINUATIONS = 5

# Explicit SDK client settings. These equal the SDKs' own defaults (600s request
# timeout, 2 retries) — passing them explicitly makes the behavior visible and
# tunable without changing anything by default. Read lazily inside complete()
# via _sdk_client_settings() so operators (and tests) can override per-process
# with ASTERISM_LLM_TIMEOUT_SECONDS / ASTERISM_LLM_MAX_RETRIES.
DEFAULT_REQUEST_TIMEOUT = 600.0
DEFAULT_SDK_RETRIES = 2


class LLMTruncatedError(RuntimeError):
    """Raised when an LLM response is still truncated after the continuation cap.

    The Step 0 callers (propose / refine) let this propagate so the api surfaces
    a CLEAR message — "the design was too large to generate fully; try a smaller
    or simpler input" — instead of silently yielding a partial proposal whose
    §RML block (and thus ``mapping.rml.ttl``) is empty.
    """


class LLMCancelledError(RuntimeError):
    """Raised when a cooperative cancel is requested mid-completion.

    The api sets ``should_cancel`` on the client before a job runs; when the
    user cancels, the next continuation-loop iteration raises this instead of
    burning more tokens on a run nobody is waiting for. Callers surface it as
    "the run was cancelled" — no partial output is returned.
    """


class LLMEmptyOutputError(RuntimeError):
    """Raised when the model returns no usable answer text.

    Some OpenAI-compatible reasoning models (gpt-oss / qwen3 / DeepSeek-R1
    style) put everything into a reasoning channel and leave the answer content
    empty. The Step 0 callers let this propagate so the api shows an actionable
    message — lower the model's reasoning effort, pick a non-thinking model, or
    simply retry — instead of failing later on an empty document.
    """


@dataclass(frozen=True)
class LLMUsage:
    """Token counts for one LLM call. Cache fields are 0 when unsupported."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @classmethod
    def zero(cls) -> LLMUsage:
        return cls()

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )

    def __add__(self, other: LLMUsage) -> LLMUsage:
        return LLMUsage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
            self.cache_write_tokens + other.cache_write_tokens,
        )


@dataclass(frozen=True)
class LLMCompletion:
    """One LLM call's text output plus its token usage."""

    text: str
    usage: LLMUsage = field(default_factory=LLMUsage.zero)


def as_completion(value: LLMCompletion | str) -> LLMCompletion:
    """Normalize a ``complete()`` return into an :class:`LLMCompletion`.

    Real clients return :class:`LLMCompletion`; test mocks (and any legacy
    client) may return a bare ``str`` — wrap those as zero-usage so the Step 0
    callers can uniformly use ``.text``.
    """
    if isinstance(value, LLMCompletion):
        return value
    return LLMCompletion(text=value, usage=LLMUsage.zero())


@runtime_checkable
class LLMClient(Protocol):
    """Minimal protocol for the single chat call the Step 0 functions make.

    The real implementations return an :class:`LLMCompletion`; mocks may return a
    bare ``str`` (normalized via :func:`as_completion`). Implementations should
    return ONLY the assistant's text (concatenated text blocks, no thinking).
    """

    def complete(self, system_prompt: str, user_message: str) -> LLMCompletion | str:
        ...


def _anthropic_usage(u: object) -> LLMUsage:
    if u is None:
        return LLMUsage.zero()
    return LLMUsage(
        input_tokens=getattr(u, "input_tokens", 0) or 0,
        output_tokens=getattr(u, "output_tokens", 0) or 0,
        cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
    )


def _openai_usage(u: object) -> LLMUsage:
    if u is None:
        return LLMUsage.zero()
    # OpenAI's automatic prompt caching, when present, surfaces as a nested
    # ``prompt_tokens_details.cached_tokens``; most compat servers omit it.
    cached = 0
    details = getattr(u, "prompt_tokens_details", None)
    if details is not None:
        cached = getattr(details, "cached_tokens", 0) or 0
    return LLMUsage(
        input_tokens=getattr(u, "prompt_tokens", 0) or 0,
        output_tokens=getattr(u, "completion_tokens", 0) or 0,
        cache_read_tokens=cached,
        cache_write_tokens=0,
    )


def _sdk_client_settings() -> tuple[float, int]:
    """Resolve the SDK request timeout / retry count at call time.

    Reads ``ASTERISM_LLM_TIMEOUT_SECONDS`` / ``ASTERISM_LLM_MAX_RETRIES``
    lazily (once per ``complete()`` call) so tests can monkeypatch the env and
    operators can tune a deployment without code changes. Invalid values fall
    back to the defaults — a bad env var must not take the LLM path down.
    """
    timeout = DEFAULT_REQUEST_TIMEOUT
    retries = DEFAULT_SDK_RETRIES
    raw_timeout = os.environ.get("ASTERISM_LLM_TIMEOUT_SECONDS")
    if raw_timeout:
        with contextlib.suppress(ValueError):
            timeout = float(raw_timeout)
    raw_retries = os.environ.get("ASTERISM_LLM_MAX_RETRIES")
    if raw_retries:
        with contextlib.suppress(ValueError):
            retries = int(raw_retries)
    return timeout, retries


@dataclass
class AnthropicLLMClient:
    """Default :class:`LLMClient` — wraps the Anthropic SDK.

    Caches the system prompt via ``cache_control: ephemeral`` (large + stable)
    and **streams** the response so a generous ``max_tokens`` does not risk the
    SDK's ~10-minute non-streaming timeout on long schema proposals. Lazy-imports
    the SDK so the package stays installable without ``anthropic``.

    When ``api_key`` is None the SDK reads ``ANTHROPIC_API_KEY`` from the
    environment (the CLI / dogfood path). The Phase 4 UI passes a user-brought
    key per request and never persists it (design doc D7).
    """

    model: str = DEFAULT_ANTHROPIC_MODEL
    max_tokens: int = DEFAULT_MAX_TOKENS
    effort: str = "xhigh"
    api_key: str | None = None
    max_continuations: int = MAX_CONTINUATIONS
    # Cooperative cancel / progress hooks, set as mutable attributes by the api
    # per job — the LLMClient protocol's complete() signature stays unchanged.
    # should_cancel is polled before each generation; on_generation reports
    # (current, total) generation counts and is guarded so a broken callback
    # can never kill the call.
    should_cancel: Callable[[], bool] | None = field(default=None, compare=False)
    on_generation: Callable[[int, int], None] | None = field(default=None, compare=False)
    last_usage: LLMUsage | None = field(default=None, init=False, compare=False)

    def complete(self, system_prompt: str, user_message: str) -> LLMCompletion:
        import anthropic

        timeout, max_retries = _sdk_client_settings()
        client = (
            anthropic.Anthropic(api_key=self.api_key, timeout=timeout, max_retries=max_retries)
            if self.api_key
            else anthropic.Anthropic(timeout=timeout, max_retries=max_retries)
        )

        # A large proposal ends with the §9 RML block (the longest section), so a
        # single generation can hit max_tokens and stop mid-RML. We CONTINUE on a
        # max_tokens stop: append the partial assistant text + a short "continue"
        # user turn and ask the model to resume, concatenating the parts until it
        # reaches a normal stop or we hit the safety cap (then fail loud).
        messages: list[dict[str, object]] = [{"role": "user", "content": user_message}]
        parts: list[str] = []
        total_usage = LLMUsage.zero()
        stop_reason: str | None = None

        for i in range(self.max_continuations + 1):
            if self.should_cancel and self.should_cancel():
                raise LLMCancelledError("cancelled")
            if self.on_generation:
                with contextlib.suppress(Exception):
                    self.on_generation(i + 1, self.max_continuations + 1)
            with client.messages.stream(
                model=self.model,
                max_tokens=self.max_tokens,
                thinking={"type": "adaptive"},
                output_config={"effort": self.effort},
                system=[
                    {
                        "type": "text",
                        "text": system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=messages,
            ) as stream:
                message = stream.get_final_message()

            part = "\n".join(b.text for b in message.content if b.type == "text")
            parts.append(part)
            total_usage = total_usage + _anthropic_usage(getattr(message, "usage", None))
            stop_reason = getattr(message, "stop_reason", None)

            if stop_reason != "max_tokens":
                break

            # Truncated: feed the partial back as an assistant turn and ask the
            # model to continue from exactly where it stopped (no overlap, no gap).
            messages.append({"role": "assistant", "content": part})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Your previous message was cut off because it hit the output "
                        "token limit. Continue the document from exactly where you "
                        "stopped — do not repeat any text you already wrote and do not "
                        "add a preamble. Pick up mid-token if necessary."
                    ),
                }
            )

        self.last_usage = total_usage

        if stop_reason == "max_tokens":
            raise LLMTruncatedError(
                "The schema design was too large to generate fully: the model's "
                f"output was still truncated after {self.max_continuations} "
                "continuation(s). Try a smaller or simpler input (fewer columns / "
                "sources), then re-run."
            )

        # The continuation prompt asks for no overlap, so plain concatenation
        # reassembles the full document.
        return LLMCompletion("".join(parts), total_usage)


# OpenAI-compatible servers disagree on the token-cap parameter name and on how
# they phrase a context-length rejection. These patterns classify a 400
# (BadRequestError) by its message so complete() can retry the SAME request
# with an adjusted parameter instead of failing the whole job. The token-param
# pattern is checked FIRST — its error text ("use 'max_completion_tokens'…")
# can also mention tokens and must not be mistaken for a context overflow.
_TOKEN_PARAM_ERROR_RE = re.compile(
    r"max_completion_tokens|unsupported_parameter.*max_tokens|max_tokens.*not supported",
    re.IGNORECASE,
)
_CONTEXT_LENGTH_ERROR_RE = re.compile(
    r"context.length|context_length_exceeded|maximum context|reduce the length"
    r"|too many tokens|input or output tokens",
    re.IGNORECASE,
)

# Reasoning models (qwen3 / DeepSeek-R1 style) served over plain Chat
# Completions can leak chain-of-thought inline as a <think>…</think> prefix in
# message.content instead of a separate reasoning field — strip it so only the
# answer text reaches the Step 0 parsers.
_THINK_TAG_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)

# Context-length auto-downgrade bounds: halve the effective cap at most this
# many times per complete() call, never below this floor (a proposal that
# cannot fit in 4096 output tokens will fail loud via the truncation path).
_MAX_CONTEXT_DOWNGRADES = 4
_MIN_DOWNGRADED_MAX_TOKENS = 4096


@dataclass
class OpenAICompatibleLLMClient:
    """:class:`LLMClient` for any OpenAI Chat Completions endpoint.

    ``base_url`` selects the endpoint: None → ``api.openai.com``; set it for
    Sakura AI Engine / Groq / Ollama / vLLM / LM Studio. Only the portable
    Chat Completions surface is used — no Anthropic-only parameters. Non-streaming
    by default for the widest compatibility (some compat servers reject
    ``stream_options``) and so ``response.usage`` is always available.

    Known per-server divergences are auto-handled inside ``complete()`` (each
    adjustment retries the SAME request without consuming a continuation slot):

    * token-param fallback — servers that reject ``max_tokens`` and demand
      ``max_completion_tokens`` are detected from the 400 message and the
      parameter is switched once for the rest of the call.
    * context-length auto-downgrade — servers that reject a generous cap
      outright (vLLM: "maximum context length is …") get the cap halved
      (floor 4096, at most 4 times); each downgrade is recorded in
      ``last_notes`` and surfaced live via ``on_note``.
    * reasoning models — inline ``<think>…</think>`` leakage is stripped, and
      a response whose whole budget went to reasoning fails loud
      (:class:`LLMTruncatedError` / :class:`LLMEmptyOutputError`) instead of
      returning an empty document.
    """

    model: str = DEFAULT_OPENAI_MODEL
    max_tokens: int = DEFAULT_MAX_TOKENS
    api_key: str | None = None
    base_url: str | None = None
    max_continuations: int = MAX_CONTINUATIONS
    # Cooperative cancel / progress / note hooks, set as mutable attributes by
    # the api per job — the LLMClient protocol's complete() signature stays
    # unchanged. All callbacks are guarded so a broken one can never kill the
    # call (should_cancel is the exception: raising LLMCancelledError is its
    # whole job).
    should_cancel: Callable[[], bool] | None = field(default=None, compare=False)
    on_generation: Callable[[int, int], None] | None = field(default=None, compare=False)
    on_note: Callable[[str], None] | None = field(default=None, compare=False)
    last_usage: LLMUsage | None = field(default=None, init=False, compare=False)
    # Human-readable notes about auto-adjustments made during the last
    # complete() call (context-length downgrades); reset on each call.
    last_notes: list[str] = field(default_factory=list, init=False, compare=False)

    def complete(self, system_prompt: str, user_message: str) -> LLMCompletion:
        from openai import OpenAI

        try:
            from openai import BadRequestError
        except ImportError:  # pragma: no cover — minimal stubs / very old SDKs

            class BadRequestError(Exception):  # type: ignore[no-redef]
                """Placeholder so the except clause below stays valid."""

        timeout, max_retries = _sdk_client_settings()
        client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url or None,
            timeout=timeout,
            max_retries=max_retries,
        )

        # Per-call auto-adjustment state (see the class docstring): which token
        # parameter this server accepts, the effective output cap after any
        # context-length downgrades, and the notes surfaced to the caller.
        token_param = "max_tokens"
        effective_max_tokens = self.max_tokens
        downgrades = 0
        self.last_notes = []

        # Same continuation-on-truncation strategy as the Anthropic client: the
        # OpenAI analog of stop_reason == "max_tokens" is finish_reason == "length".
        messages: list[dict[str, object]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
        parts: list[str] = []
        total_usage = LLMUsage.zero()
        finish_reason: str | None = None

        for i in range(self.max_continuations + 1):
            if self.should_cancel and self.should_cancel():
                raise LLMCancelledError("cancelled")
            if self.on_generation:
                with contextlib.suppress(Exception):
                    self.on_generation(i + 1, self.max_continuations + 1)

            # Issue the request, retrying the SAME request (no continuation
            # slot consumed) across the two known compat-server divergences:
            # token-param fallback, then context-length downgrade.
            while True:
                try:
                    resp = client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        **{token_param: effective_max_tokens},
                    )
                    break
                except BadRequestError as exc:
                    err_text = str(exc)
                    # Some servers (OpenAI o-series, various proxies) reject
                    # max_tokens and demand max_completion_tokens. Switch once
                    # and remember for all later generations.
                    if token_param == "max_tokens" and _TOKEN_PARAM_ERROR_RE.search(err_text):
                        token_param = "max_completion_tokens"
                        continue
                    # Weak/compat servers (vLLM etc.) reject a generous cap
                    # outright when it exceeds the model context. Halve until
                    # it fits — floor 4096 — and give up (re-raise the provider
                    # error unchanged) after _MAX_CONTEXT_DOWNGRADES halvings.
                    if downgrades < _MAX_CONTEXT_DOWNGRADES and _CONTEXT_LENGTH_ERROR_RE.search(
                        err_text
                    ):
                        downgrades += 1
                        lowered = max(effective_max_tokens // 2, _MIN_DOWNGRADED_MAX_TOKENS)
                        note = (
                            f"max_tokens {effective_max_tokens} -> {lowered} "
                            "after provider context-length rejection"
                        )
                        effective_max_tokens = lowered
                        self.last_notes.append(note)
                        if self.on_note:
                            with contextlib.suppress(Exception):
                                self.on_note(note)
                        continue
                    raise

            choice = resp.choices[0]
            message = choice.message
            part = message.content or ""
            # Reasoning models leaking chain-of-thought inline: drop closed
            # <think>…</think> blocks; a remaining leading <think> means the
            # block never closed — the whole part is reasoning, no answer yet.
            part = _THINK_TAG_RE.sub("", part)
            if part.startswith("<think>"):
                part = ""
            # Out-of-band reasoning (vLLM reasoning parsers etc.) arrives as
            # message.reasoning_content or under model_extra; it is only used
            # to pick the more precise error message below, never as output.
            extra = getattr(message, "model_extra", None) or {}
            reasoning_text = (
                getattr(message, "reasoning_content", None)
                or extra.get("reasoning_content")
                or extra.get("reasoning")
            )

            total_usage = total_usage + _openai_usage(getattr(resp, "usage", None))
            finish_reason = getattr(choice, "finish_reason", None)

            if not part.strip():
                if finish_reason == "length":
                    # The whole output budget went to (hidden) reasoning before
                    # any answer text appeared — continuing with an empty
                    # assistant turn would only burn more tokens, so fail loud.
                    self.last_usage = total_usage
                    raise LLMTruncatedError(
                        "The model spent its entire output budget before emitting "
                        "any answer text — this usually means a reasoning/thinking "
                        "model used all its tokens on reasoning. Lower the model's "
                        "reasoning effort, raise the max output tokens setting, or "
                        "choose a non-thinking model, then re-run."
                    )
                if not any(p.strip() for p in parts):
                    # The FIRST generation produced nothing usable.
                    self.last_usage = total_usage
                    if reasoning_text:
                        raise LLMEmptyOutputError(
                            "The model returned only reasoning text and no answer. "
                            "Lower the model's reasoning effort or choose a "
                            "non-thinking model, then re-run."
                        )
                    raise LLMEmptyOutputError(
                        "The model returned an empty response. Re-run the request, "
                        "or choose a different model."
                    )
                # A LATER generation stopped naturally without new text: the
                # document is simply complete — not an error.
                break

            parts.append(part)
            if finish_reason != "length":
                break

            messages.append({"role": "assistant", "content": part})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Your previous message was cut off because it hit the output "
                        "token limit. Continue the document from exactly where you "
                        "stopped — do not repeat any text you already wrote and do not "
                        "add a preamble. Pick up mid-token if necessary."
                    ),
                }
            )

        self.last_usage = total_usage

        if finish_reason == "length":
            raise LLMTruncatedError(
                "The schema design was too large to generate fully: the model's "
                f"output was still truncated after {self.max_continuations} "
                "continuation(s). Try a smaller or simpler input (fewer columns / "
                "sources), then re-run."
            )

        return LLMCompletion("".join(parts), total_usage)


# Provider aliases accepted by make_llm (case-insensitive). Everything in the
# OpenAI-compatible family routes to OpenAICompatibleLLMClient; the only thing
# that differs at runtime is the base_url the caller supplies.
_ANTHROPIC_ALIASES = frozenset({"", "anthropic", "claude"})
_OPENAI_ALIASES = frozenset(
    {"openai", "openai-compatible", "openai_compatible", "compatible", "sakura", "groq", "ollama"}
)


def make_llm(
    provider: str | None,
    *,
    model: str | None = None,
    api_base: str | None = None,
    api_key: str | None = None,
    max_tokens: int | None = None,
) -> LLMClient:
    """Build an :class:`LLMClient` for the given provider coordinates.

    ``provider`` None / "" / "anthropic" returns the default Anthropic client
    (byte-for-byte the historical behavior). "openai" / "openai-compatible"
    returns an :class:`OpenAICompatibleLLMClient`; pass ``api_base`` for a custom
    endpoint (Sakura AI Engine etc.). Unknown providers raise ``ValueError``.

    ``max_tokens`` (a positive int) overrides the single-generation output cap
    for either provider — useful for weak/compat models whose context window is
    smaller than :data:`DEFAULT_MAX_TOKENS`. None (or a non-positive value)
    keeps the default.
    """
    p = (provider or "anthropic").strip().lower()
    resolved_max_tokens = (
        max_tokens if max_tokens is not None and max_tokens > 0 else DEFAULT_MAX_TOKENS
    )
    if p in _ANTHROPIC_ALIASES:
        return AnthropicLLMClient(
            model=model or DEFAULT_ANTHROPIC_MODEL,
            api_key=api_key,
            max_tokens=resolved_max_tokens,
        )
    if p in _OPENAI_ALIASES:
        return OpenAICompatibleLLMClient(
            model=model or DEFAULT_OPENAI_MODEL,
            api_key=api_key,
            base_url=api_base or None,
            max_tokens=resolved_max_tokens,
        )
    raise ValueError(f"unknown LLM provider: {provider!r}")


def list_available_models(
    provider: str | None,
    *,
    api_key: str | None = None,
    api_base: str | None = None,
) -> list[dict[str, str]]:
    """List the models the given credentials can use (model picker #②).

    Mirrors :func:`make_llm`'s provider aliases:
    - anthropic → Anthropic SDK ``client.models.list()``.
    - openai / openai-compatible → OpenAI SDK ``client.models.list().data``
      (``api_base`` selects the endpoint: Sakura AI Engine / Groq / Ollama / …).

    Returns ``[{"id", "display_name"}]`` sorted as the provider returns them.
    Network / auth errors propagate to the caller (the API layer maps them to a
    4xx/5xx). **The caller must SSRF-validate ``api_base`` before calling this
    for an openai-compatible endpoint** — this helper trusts the URL it is given.
    """
    p = (provider or "anthropic").strip().lower()
    if p in _ANTHROPIC_ALIASES:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        return [
            {"id": m.id, "display_name": getattr(m, "display_name", None) or m.id}
            for m in client.models.list()
        ]
    if p in _OPENAI_ALIASES:
        from openai import OpenAI

        client = OpenAI(api_key=api_key, base_url=api_base or None)
        return [
            {"id": m.id, "display_name": m.id}
            for m in client.models.list().data
            if getattr(m, "id", None)
        ]
    raise ValueError(f"unknown LLM provider: {provider!r}")
