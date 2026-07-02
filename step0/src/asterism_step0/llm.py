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


class LLMTruncatedError(RuntimeError):
    """Raised when an LLM response is still truncated after the continuation cap.

    The Step 0 callers (propose / refine) let this propagate so the api surfaces
    a CLEAR message — "the design was too large to generate fully; try a smaller
    or simpler input" — instead of silently yielding a partial proposal whose
    §RML block (and thus ``mapping.rml.ttl``) is empty.
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
    last_usage: LLMUsage | None = field(default=None, init=False, compare=False)

    def complete(self, system_prompt: str, user_message: str) -> LLMCompletion:
        import anthropic

        client = (
            anthropic.Anthropic(api_key=self.api_key) if self.api_key else anthropic.Anthropic()
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

        for _ in range(self.max_continuations + 1):
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


@dataclass
class OpenAICompatibleLLMClient:
    """:class:`LLMClient` for any OpenAI Chat Completions endpoint.

    ``base_url`` selects the endpoint: None → ``api.openai.com``; set it for
    Sakura AI Engine / Groq / Ollama / vLLM / LM Studio. Only the portable
    Chat Completions surface is used — no Anthropic-only parameters. Non-streaming
    by default for the widest compatibility (some compat servers reject
    ``stream_options``) and so ``response.usage`` is always available.

    Note: a few compat servers expect ``max_completion_tokens`` instead of
    ``max_tokens``, or cap it lower; that is a known per-server divergence.
    """

    model: str = DEFAULT_OPENAI_MODEL
    max_tokens: int = DEFAULT_MAX_TOKENS
    api_key: str | None = None
    base_url: str | None = None
    max_continuations: int = MAX_CONTINUATIONS
    last_usage: LLMUsage | None = field(default=None, init=False, compare=False)

    def complete(self, system_prompt: str, user_message: str) -> LLMCompletion:
        from openai import OpenAI

        client = OpenAI(api_key=self.api_key, base_url=self.base_url or None)

        # Same continuation-on-truncation strategy as the Anthropic client: the
        # OpenAI analog of stop_reason == "max_tokens" is finish_reason == "length".
        messages: list[dict[str, object]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
        parts: list[str] = []
        total_usage = LLMUsage.zero()
        finish_reason: str | None = None

        for _ in range(self.max_continuations + 1):
            resp = client.chat.completions.create(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=messages,
            )
            choice = resp.choices[0]
            part = choice.message.content or ""
            parts.append(part)
            total_usage = total_usage + _openai_usage(getattr(resp, "usage", None))
            finish_reason = getattr(choice, "finish_reason", None)

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
) -> LLMClient:
    """Build an :class:`LLMClient` for the given provider coordinates.

    ``provider`` None / "" / "anthropic" returns the default Anthropic client
    (byte-for-byte the historical behavior). "openai" / "openai-compatible"
    returns an :class:`OpenAICompatibleLLMClient`; pass ``api_base`` for a custom
    endpoint (Sakura AI Engine etc.). Unknown providers raise ``ValueError``.
    """
    p = (provider or "anthropic").strip().lower()
    if p in _ANTHROPIC_ALIASES:
        return AnthropicLLMClient(model=model or DEFAULT_ANTHROPIC_MODEL, api_key=api_key)
    if p in _OPENAI_ALIASES:
        return OpenAICompatibleLLMClient(
            model=model or DEFAULT_OPENAI_MODEL,
            api_key=api_key,
            base_url=api_base or None,
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
