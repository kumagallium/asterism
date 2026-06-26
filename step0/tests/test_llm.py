"""Tests for asterism_step0.llm — the multi-provider client seam.

No network: these exercise provider routing, the usage value types, and the
``as_completion`` normalization shim. The real SDKs are lazy-imported inside
``complete()`` so importing this module needs neither ``anthropic`` nor
``openai``.
"""

from __future__ import annotations

import pytest

from asterism_step0.llm import (
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_OPENAI_MODEL,
    AnthropicLLMClient,
    LLMCompletion,
    LLMUsage,
    OpenAICompatibleLLMClient,
    as_completion,
    make_llm,
)


def test_llmusage_zero_total_and_add() -> None:
    assert LLMUsage.zero() == LLMUsage(0, 0, 0, 0)
    u = LLMUsage(input_tokens=10, output_tokens=5, cache_read_tokens=2, cache_write_tokens=1)
    assert u.total_tokens == 18
    combined = u + LLMUsage(input_tokens=1, output_tokens=1)
    assert combined.input_tokens == 11
    assert combined.output_tokens == 6
    assert combined.total_tokens == 20


def test_as_completion_wraps_str_as_zero_usage() -> None:
    c = as_completion("hello")
    assert isinstance(c, LLMCompletion)
    assert c.text == "hello"
    assert c.usage == LLMUsage.zero()


def test_as_completion_passes_through_completion() -> None:
    original = LLMCompletion("x", LLMUsage(input_tokens=3))
    assert as_completion(original) is original


def test_make_llm_default_is_anthropic_with_default_model() -> None:
    # provider=None must reproduce the historical Anthropic default byte-for-byte.
    client = make_llm(None, api_key="sk-test")
    assert isinstance(client, AnthropicLLMClient)
    assert client.model == DEFAULT_ANTHROPIC_MODEL
    assert client.api_key == "sk-test"
    assert client.max_tokens == 96000
    assert client.effort == "xhigh"


def test_make_llm_anthropic_alias_and_model_override() -> None:
    client = make_llm("anthropic", model="claude-sonnet-4-6", api_key="k")
    assert isinstance(client, AnthropicLLMClient)
    assert client.model == "claude-sonnet-4-6"


@pytest.mark.parametrize("provider", ["openai", "openai-compatible", "sakura"])
def test_make_llm_openai_family_routes_to_compatible_client(provider: str) -> None:
    client = make_llm(provider, api_base="https://api.ai.sakura.ad.jp/v1", api_key="k")
    assert isinstance(client, OpenAICompatibleLLMClient)
    assert client.base_url == "https://api.ai.sakura.ad.jp/v1"
    assert client.model == DEFAULT_OPENAI_MODEL
    assert client.api_key == "k"


def test_make_llm_openai_model_override() -> None:
    client = make_llm("openai", model="gpt-4o-mini")
    assert isinstance(client, OpenAICompatibleLLMClient)
    assert client.model == "gpt-4o-mini"
    assert client.base_url is None  # default endpoint


def test_make_llm_unknown_provider_raises() -> None:
    with pytest.raises(ValueError, match="unknown LLM provider"):
        make_llm("not-a-provider")


def test_clients_start_with_no_recorded_usage() -> None:
    # last_usage is the seam the api reads to record a usage event; it's None
    # until complete() runs, so nothing is recorded for a never-called client.
    assert make_llm(None).last_usage is None  # type: ignore[union-attr]
    assert make_llm("openai").last_usage is None  # type: ignore[union-attr]
