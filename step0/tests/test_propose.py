"""Tests for asterism_step0.propose.

These exercise the prompt-building / orchestration layer without making real
LLM calls. The :class:`LLMClient` protocol is mocked so the tests are
deterministic, fast, and don't need an Anthropic API key.

Coverage:
  - propose_schema runs Step 1 (inspect) then Step 3 (LLM) in order
  - The system prompt is the cacheable one (large, byte-stable across calls)
  - The user message embeds both the inspection markdown and the domain hint
  - 8-trap validators (T1, T2, T3, T4, T6, T7) appear in the system prompt
  - The default AnthropicLLMClient lazy-imports anthropic (we don't actually
    call it; just verify the wiring)
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from asterism_step0.propose import (
    SYSTEM_PROMPT,
    AnthropicLLMClient,
    LLMClient,
    SchemaProposal,
    propose_schema,
)

# ----------------------------------------------------------------------------
# Mock LLM client
# ----------------------------------------------------------------------------


class _RecordingLLM:
    """Mock LLM that records the prompts it was called with and returns canned text."""

    def __init__(self, canned_response: str = "# Mock proposal") -> None:
        self.canned_response = canned_response
        self.system_prompts: list[str] = []
        self.user_messages: list[str] = []

    def complete(self, system_prompt: str, user_message: str) -> str:
        self.system_prompts.append(system_prompt)
        self.user_messages.append(user_message)
        return self.canned_response


def _write_csv(path: Path, content: str) -> Path:
    path.write_text(dedent(content).lstrip("\n"), encoding="utf-8")
    return path


def _write_json(path: Path, data: object) -> Path:
    import json

    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


# ----------------------------------------------------------------------------
# propose_schema end-to-end (with mock LLM)
# ----------------------------------------------------------------------------


def test_propose_schema_passes_inspection_to_llm(tmp_path: Path) -> None:
    csv_path = _write_csv(
        tmp_path / "samples.csv",
        """
        SID,sample_id,composition
        1,10,Bi2Te3
        1,11,PbTe
        2,10,SnSe
        """,
    )
    mock = _RecordingLLM(canned_response="# Proposal\n...")
    proposal = propose_schema(
        [csv_path],
        domain_hint="materials science thermoelectric dataset",
        fk_hint_columns=["SID"],
        llm=mock,
    )
    assert isinstance(proposal, SchemaProposal)
    assert len(mock.user_messages) == 1
    user_msg = mock.user_messages[0]
    # Inspection markdown is embedded
    assert "## CSV: samples.csv" in user_msg
    assert "### Uniqueness" in user_msg
    # Composite key result is in there
    assert "SID" in user_msg
    assert "sample_id" in user_msg
    # Domain hint is embedded
    assert "thermoelectric" in user_msg


def test_propose_schema_routes_json_source_to_llm(tmp_path: Path) -> None:
    """A JSON source flows through the JSON inspector and reaches the LLM as a
    `## JSON:` block carrying the iterator + dot-path leaves."""
    json_path = _write_json(
        tmp_path / "mp.json",
        [
            {"mp_id": "mp-1", "formula": "PbTe", "structure": {"spacegroup": "Fm-3m"}},
            {"mp_id": "mp-2", "formula": "SnSe", "structure": {"spacegroup": "Pnma"}},
        ],
    )
    mock = _RecordingLLM()
    propose_schema([json_path], domain_hint="crystal structures", llm=mock)
    user_msg = mock.user_messages[0]
    assert "## JSON: mp.json" in user_msg
    assert "iterator `$[*]`" in user_msg
    # Dot-path leaf is presented for use as an rml:reference.
    assert "`structure.spacegroup`" in user_msg
    assert "crystal structures" in user_msg


def test_propose_schema_json_record_path(tmp_path: Path) -> None:
    json_path = _write_json(
        tmp_path / "wrapped.json",
        {"data": [{"mp_id": "mp-1"}, {"mp_id": "mp-2"}]},
    )
    mock = _RecordingLLM()
    propose_schema([json_path], domain_hint="x", record_path="data", llm=mock)
    assert "iterator `$.data[*]`" in mock.user_messages[0]


def test_system_prompt_teaches_json_source_as_csv() -> None:
    """§9 must teach that JSON sources are tabularized to CSV at ingest, so the
    LLM writes the derived `.csv` as the map's `source` (no iterator) and
    explodes array columns with the Tier 0 exploders
    (native-json-denormalization)."""
    assert "tabulariz" in SYSTEM_PROMPT  # JSON → CSV normalization is taught
    assert "dot-path" in SYSTEM_PROMPT  # columns are the flattened leaf fields
    # The native JSONPath path is superseded: the prompt must NOT steer to it.
    assert "JSONPath" not in SYSTEM_PROMPT


def test_propose_schema_returns_canned_llm_output(tmp_path: Path) -> None:
    csv_path = _write_csv(
        tmp_path / "tiny.csv",
        """
        id,name
        1,a
        2,b
        """,
    )
    mock = _RecordingLLM(canned_response="THIS IS THE PROPOSAL")
    proposal = propose_schema([csv_path], domain_hint="anything", llm=mock)
    assert proposal.proposal_md == "THIS IS THE PROPOSAL"


def test_propose_schema_records_metadata(tmp_path: Path) -> None:
    csv_path = _write_csv(
        tmp_path / "x.csv",
        """
        id
        1
        2
        """,
    )
    mock = _RecordingLLM()
    proposal = propose_schema([csv_path], domain_hint="x", llm=mock)
    assert proposal.metadata["llm_class"] == "_RecordingLLM"


def test_propose_schema_language_rides_user_message_only(tmp_path: Path) -> None:
    """language= appends the Output-language block to the USER message; the
    cacheable system prompt stays byte-stable (prompt-caching contract)."""
    csv_path = _write_csv(
        tmp_path / "x.csv",
        """
        id
        1
        """,
    )
    mock = _RecordingLLM()
    propose_schema([csv_path], domain_hint="x", llm=mock, language="ja")
    assert "# Output language" in mock.user_messages[0]
    assert "Japanese (日本語)" in mock.user_messages[0]
    assert mock.system_prompts[0] == SYSTEM_PROMPT


def test_propose_schema_no_language_keeps_legacy_message(tmp_path: Path) -> None:
    csv_path = _write_csv(
        tmp_path / "x.csv",
        """
        id
        1
        """,
    )
    mock = _RecordingLLM()
    propose_schema([csv_path], domain_hint="x", llm=mock)
    assert "# Output language" not in mock.user_messages[0]


# ----------------------------------------------------------------------------
# System prompt invariants (the cacheable surface)
# ----------------------------------------------------------------------------


def test_system_prompt_embeds_trap_validators() -> None:
    """The system prompt's self-check section must mention every trap (incl. T9 RML)."""
    for trap in ("T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8", "T9"):
        assert trap in SYSTEM_PROMPT, f"trap {trap} missing from system prompt"


def test_system_prompt_emits_mapping_spec_section_with_tier0_menu() -> None:
    """§9 must instruct a yaml MAPPING SPEC (not raw RML) restricted to the
    vetted Tier 0 menu (ADR mapping-ir-compiler.md)."""
    # A yaml mapping-spec artifact is requested under a header materialize's
    # _MAPPING_IR_HEADERS recognizes.
    assert "Declarative mapping spec" in SYSTEM_PROMPT
    assert "```yaml" in SYSTEM_PROMPT
    # The guardrails: Tier 0 only, raw-string fallback, no inline code.
    assert "Tier 0" in SYSTEM_PROMPT
    assert "fallback" in SYSTEM_PROMPT.lower()


def test_system_prompt_menu_covers_the_whole_registry() -> None:
    """Every vetted Tier-0 function must be on the §9 menu (bare name) — the
    menu is the closed set the LLM chooses from, so an unlisted function is
    unreachable and a listed-but-unregistered one would fail compilation."""
    functions = pytest.importorskip("asterism.functions")
    for spec in functions.REGISTRY:
        assert (
            f"`{spec.name}`" in SYSTEM_PROMPT
        ), f"Tier-0 function {spec.name} missing from the §9 menu"


def test_system_prompt_mentions_rdf_config_output_format() -> None:
    """Phase 3 #3 decided rdf-config; the prompt must instruct that format."""
    assert "rdf-config" in SYSTEM_PROMPT
    assert "model.yaml" in SYSTEM_PROMPT


def test_system_prompt_forbids_bnodes() -> None:
    """T3: blank node prohibition must be in the prompt."""
    # Loose substring check; we want any wording forbidding bnodes
    lower = SYSTEM_PROMPT.lower()
    assert "blank node" in lower or "bnode" in lower or "rdflib.bnode()" in lower


def test_system_prompt_is_byte_stable_across_calls(tmp_path: Path) -> None:
    """Caching invariant: the system prompt must NOT change between calls.

    If propose_schema interpolates dynamic content (timestamps, user IDs, the
    inspection itself) into the system prompt, prompt caching fails. We
    verify by running propose_schema twice on different inputs and checking
    that the system prompts the LLM saw are byte-identical.
    """
    csv_a = _write_csv(tmp_path / "a.csv", "id\n1\n2\n")
    csv_b = _write_csv(tmp_path / "b.csv", "id\n10\n20\n30\n")
    mock = _RecordingLLM()
    propose_schema([csv_a], domain_hint="domain A", llm=mock)
    propose_schema([csv_b], domain_hint="domain B", llm=mock)
    assert len(mock.system_prompts) == 2
    assert mock.system_prompts[0] == mock.system_prompts[1]
    # Sanity: the user messages SHOULD differ (that's where variable content goes)
    assert mock.user_messages[0] != mock.user_messages[1]


# ----------------------------------------------------------------------------
# AnthropicLLMClient — wiring check (no real API call)
# ----------------------------------------------------------------------------


def test_anthropic_client_satisfies_llm_protocol() -> None:
    """AnthropicLLMClient must satisfy the LLMClient Protocol structurally."""
    client = AnthropicLLMClient()
    assert isinstance(client, LLMClient)
    # Default values per the claude-api skill (Opus 4.7 + xhigh effort)
    assert client.model == "claude-opus-4-7"
    assert client.effort == "xhigh"
    # 96000 (not 16000/32000) — a full proposal ends with the long §9 RML block,
    # which truncated at the smaller caps in dogfooding. The client streams (so
    # the larger cap dodges the non-streaming timeout) and continues on a
    # max_tokens stop, so the generous cap is safe.
    assert client.max_tokens == 96000


def test_anthropic_client_lazy_imports_anthropic() -> None:
    """The propose module must be importable without anthropic installed.

    We don't have anthropic installed in this venv; if the import wasn't lazy,
    `import asterism_step0.propose` would have failed at test collection time.
    Getting here proves it stayed lazy.
    """
    from asterism_step0 import propose

    assert hasattr(propose, "AnthropicLLMClient")
    assert hasattr(propose, "propose_schema")


def test_system_prompt_carries_no_rml_syntax() -> None:
    """The RML/FnO syntax knowledge moved INTO the compiler (ADR
    mapping-ir-compiler.md): the prompt must not teach namespaces, parameter
    IRIs or term-map shapes anymore — the LLM only picks from closed menus.
    (The namespace pin now lives in rml_compile and is asserted by its tests.)"""
    for syntax in (
        "http://w3id.org/rml/",
        "semweb.mmlab.be",
        "functionExecution",
        "p_value",
        "rr:template",
        "rml:reference",
        "rmlf:",
        "```turtle",
    ):
        assert syntax not in SYSTEM_PROMPT, f"RML syntax leaked into the prompt: {syntax}"
