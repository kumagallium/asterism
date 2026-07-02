"""AI tool-draft (P2): propose_query_tool turns intent + vocab into a query_tool
dict via an LLMClient. The LLM is faked here (no network)."""
from __future__ import annotations

import pytest

from asterism_step0.tool_propose import _SYSTEM, propose_query_tool

_VALID = (
    '{"name":"by_formula","title":"By formula",'
    '"description":"Find a material by reduced formula.",'
    '"parameters":[{"name":"f","type":"string","required":true,"description":"formula"}],'
    '"query":"PREFIX mp: <https://ex/mp#> SELECT ?m WHERE { ?m mp:formula {{f}} }",'
    '"result":{"item":{"iri":"m"}}}'
)


class _FakeLLM:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_message: str) -> str:
        self.calls.append((system_prompt, user_message))
        return self.response


def test_parses_json_draft_and_passes_context() -> None:
    llm = _FakeLLM(_VALID)
    tool = propose_query_tool(
        llm, intent="find a material by formula", model_yaml="MODEL_X", mie_yaml="MIE_Y"
    )
    assert tool["name"] == "by_formula"
    assert tool["query"].startswith("PREFIX mp:")
    # the intent + the dataset vocabulary + examples reached the model
    user = llm.calls[0][1]
    assert "find a material by formula" in user
    assert "MODEL_X" in user and "MIE_Y" in user


def test_rml_grounds_the_draft_when_model_is_thin() -> None:
    # The real failure mode: a workbench-seeded dataset ships a stub model.yaml
    # (bare class names, no namespace), so the model must ground in the RML — the
    # source of truth for the real namespaces/predicates — instead of inventing a
    # placeholder namespace. Assert the RML's namespace + predicates reach the LLM.
    llm = _FakeLLM(_VALID)
    rml = (
        "@prefix sd: <https://kumagallium.github.io/asterism/starrydata/ontology#> .\n"
        "<#Curve> rr:class sd:Curve ; rr:predicate sd:propertyY, sd:yMax ."
    )
    propose_query_tool(
        llm, intent="rank by thermal conductivity", model_yaml="- Curve:", rml_ttl=rml
    )
    user = llm.calls[0][1]
    assert "starrydata/ontology#" in user
    assert "sd:propertyY" in user and "sd:yMax" in user


def test_tolerates_code_fence_and_prose() -> None:
    llm = _FakeLLM("Sure, here is the tool:\n```json\n" + _VALID + "\n```\nHope that helps!")
    assert propose_query_tool(llm, intent="x")["name"] == "by_formula"


def test_missing_required_keys_raises() -> None:
    with pytest.raises(ValueError, match="name, query"):
        propose_query_tool(_FakeLLM('{"title":"no name or query"}'), intent="x")


def test_unparseable_output_raises() -> None:
    with pytest.raises(ValueError, match="JSON object"):
        propose_query_tool(_FakeLLM("I cannot help with that."), intent="x")


def test_empty_intent_raises() -> None:
    with pytest.raises(ValueError, match="intent"):
        propose_query_tool(_FakeLLM(_VALID), intent="   ")


def test_language_rides_user_message_only() -> None:
    """language= appends the Output-language block to the USER message; the
    cacheable system prompt stays byte-stable (prompt-caching contract)."""
    llm = _FakeLLM(_VALID)
    propose_query_tool(llm, intent="x", language="ja")
    system, user = llm.calls[0]
    assert "# Output language" in user
    assert "Japanese (日本語)" in user
    assert system == _SYSTEM
    assert "# Output language" not in system


def test_no_language_keeps_legacy_message() -> None:
    llm = _FakeLLM(_VALID)
    propose_query_tool(llm, intent="x")
    assert "# Output language" not in llm.calls[0][1]
