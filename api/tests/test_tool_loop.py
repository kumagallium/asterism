"""Self-correcting query-tool draft loop (asterism_api.tool_loop).

The design loop's little sibling: propose -> deterministic vet (parse + lint +
RML-closed-vocabulary) -> targeted feedback + closed-menu oracle -> refine.
All tests use a scripted fake LLM — determinism is the whole point.
"""
from __future__ import annotations

import json

import pytest

from asterism_api.tool_loop import (
    build_vocab_oracle,
    propose_tool_with_correction,
    vet_tool_draft,
)

_RML = """
@prefix rr: <http://www.w3.org/ns/r2rml#> .
@prefix rml: <http://semweb.mmlab.be/ns/rml#> .
@prefix sd: <https://ex/sd#> .
<#Curves> rml:logicalSource [ rml:source "curves.csv" ] ;
  rr:subjectMap [ rr:template "https://ex/curve/{id}" ; rr:class sd:Curve ] ;
  rr:predicateObjectMap [ rr:predicate sd:propertyY ; rr:objectMap [ rml:reference "p" ] ] ;
  rr:predicateObjectMap [ rr:predicate sd:yMax ; rr:objectMap [ rml:reference "m" ] ] .
"""

# Round 1: the exact production failure families — an undeclared prov: prefix
# (store 400) AND a guessed predicate the RML never maps (0 rows).
_BROKEN = json.dumps(
    {
        "name": "high_zt",
        "title": "ZT",
        "query": (
            "PREFIX sd: <https://ex/sd#>\n"
            "SELECT ?c ?t WHERE { ?c a sd:Curve ; sd:composition ?x ; "
            "prov:generatedAtTime ?t . FILTER(CONTAINS(STR(?c), {{q}})) } LIMIT 5"
        ),
        "parameters": [{"name": "q", "type": "string", "required": True}],
    }
)

_FIXED = json.dumps(
    {
        "name": "high_zt",
        "title": "ZT",
        "query": (
            "PREFIX sd: <https://ex/sd#>\n"
            "SELECT ?c ?m WHERE { ?c a sd:Curve ; sd:propertyY ?p ; sd:yMax ?m . "
            "FILTER(CONTAINS(STR(?p), {{q}})) } ORDER BY DESC(?m) LIMIT 10"
        ),
        "parameters": [{"name": "q", "type": "string", "required": True}],
    }
)


class _SeqLLM:
    """Returns scripted responses in order (last one repeats); records prompts."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.users: list[str] = []

    def complete(self, system_prompt: str, user_message: str) -> str:
        self.users.append(user_message)
        return self.responses[min(len(self.users) - 1, len(self.responses) - 1)]


def test_vet_catches_both_live_failure_families() -> None:
    from asterism.rml_validate import extract_rml_vocabulary

    errors, warnings = vet_tool_draft(json.loads(_BROKEN), extract_rml_vocabulary(_RML))
    assert any("prov:" in e for e in errors)  # undeclared prefix -> store 400
    assert any("composition" in w for w in warnings)  # unmapped term -> 0 rows


def test_loop_corrects_broken_draft_in_second_round() -> None:
    llm = _SeqLLM([_BROKEN, _FIXED])
    res = propose_tool_with_correction(llm, intent="rank ZT curves", rml_ttl=_RML)
    assert res.valid and res.error is None and not res.warnings
    assert [r["round"] for r in res.rounds] == [1, 2]
    assert res.rounds[0]["errors"] and not res.rounds[1]["errors"]
    # the refine prompt carried the defects AND the closed-menu oracle
    assert "prov:" in llm.users[1]
    assert "Vocabulary oracle" in llm.users[1]
    assert res.draft["query"].startswith("PREFIX sd:")


def test_oracle_is_injected_into_the_first_draft_too() -> None:
    llm = _SeqLLM([_FIXED])
    res = propose_tool_with_correction(llm, intent="rank", rml_ttl=_RML)
    assert res.valid and len(res.rounds) == 1
    assert "Vocabulary oracle" in llm.users[0]
    assert "<https://ex/sd#yMax>" in llm.users[0]  # the closed menu is explicit


def test_kill_switch_max_rounds_1_single_shot() -> None:
    llm = _SeqLLM([_BROKEN, _FIXED])
    res = propose_tool_with_correction(llm, intent="rank", rml_ttl=_RML, max_rounds=1)
    assert len(llm.users) == 1  # no refine call
    assert res.valid is False and "prov:" in (res.error or "")
    assert len(res.rounds) == 1


def test_llm_failure_after_first_round_keeps_best_draft() -> None:
    class _FailsOnRefine(_SeqLLM):
        def complete(self, system_prompt: str, user_message: str) -> str:
            if self.users:  # any call after the first
                raise RuntimeError("provider blew up")
            return super().complete(system_prompt, user_message)

    llm = _FailsOnRefine([_BROKEN])
    res = propose_tool_with_correction(llm, intent="rank", rml_ttl=_RML)
    assert res.valid is False and res.draft["name"] == "high_zt"  # kept, not lost


def test_llm_failure_on_first_round_propagates() -> None:
    class _AlwaysFails:
        def complete(self, system_prompt: str, user_message: str) -> str:
            raise RuntimeError("no key")

    with pytest.raises(RuntimeError, match="no key"):
        propose_tool_with_correction(_AlwaysFails(), intent="rank", rml_ttl=_RML)


def test_no_rml_means_no_oracle_but_lint_still_gates() -> None:
    llm = _SeqLLM([_BROKEN, _FIXED])
    res = propose_tool_with_correction(llm, intent="rank", rml_ttl="")
    assert "Vocabulary oracle" not in llm.users[0]
    # the undeclared prov: prefix is still caught (round 1) and fixed (round 2)
    assert res.valid and len(res.rounds) == 2


def test_build_vocab_oracle_empty_without_terms() -> None:
    assert build_vocab_oracle({"prefixes": {"sd": "https://ex/sd#"}, "terms": set()}) == ""
