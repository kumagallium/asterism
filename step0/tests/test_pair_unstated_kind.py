"""R1 — 「言えない」のとき、関係の種類の短い名詞 unstated_kind も書かせる（pair_propose）。"""

from __future__ import annotations

import json

import pytest

from asterism_step0.pair_propose import (
    PAIR_SYSTEM_PROMPT,
    pair_json_schema,
    propose_pair,
    validate_pair_answer,
)

NONE_ANS = {"rel": "none", "from": None, "evidence": [], "note": "条件の話"}
PAIR = {
    "a": {"name": "焼結温度", "kind": "property", "role": None},
    "b": {"name": "密度", "kind": "property", "role": None},
    "claims": ["焼結温度が 900 度のとき密度を比べた。"],
}


def test_none_keeps_a_short_kind() -> None:
    out = validate_pair_answer({**NONE_ANS, "unstated_kind": "  条件  "}, 1)
    assert out == {**NONE_ANS, "unstated_kind": "条件"}


def test_none_without_the_field_reads_as_before() -> None:
    assert validate_pair_answer(NONE_ANS, 1) == NONE_ANS  # 古いモデル: 欄が無い
    assert "unstated_kind" not in validate_pair_answer({**NONE_ANS, "unstated_kind": ""}, 1)
    assert "unstated_kind" not in validate_pair_answer({**NONE_ANS, "unstated_kind": None}, 1)


def test_a_relation_in_the_set_drops_the_kind() -> None:
    ans = {"rel": "makes", "from": "a", "evidence": ["c1"], "note": "", "unstated_kind": "条件"}
    assert "unstated_kind" not in validate_pair_answer(ans, 1)


@pytest.mark.parametrize(
    "bad",
    ["https://example.org/x", "urn:x:y", "{a}", "`code`", "SELECT x", "---", "123", "?!", 5, []],
)
def test_code_iri_symbols_digits_are_dropped(bad: object) -> None:
    assert "unstated_kind" not in validate_pair_answer({**NONE_ANS, "unstated_kind": bad}, 1)


def test_a_long_kind_is_cut_at_the_limit() -> None:
    out = validate_pair_answer({**NONE_ANS, "unstated_kind": "あ" * 50}, 1)
    assert out["unstated_kind"] == "あ" * 20


def test_folds_inner_spaces() -> None:
    out = validate_pair_answer({**NONE_ANS, "unstated_kind": "no\n  effect"}, 1)
    assert out["unstated_kind"] == "no effect"


def test_schema_has_an_optional_field() -> None:
    s = pair_json_schema()
    assert "unstated_kind" not in s["required"]
    assert s["properties"]["unstated_kind"]["maxLength"] == 20


def test_prompt_mentions_the_field_once_for_none_only() -> None:
    assert PAIR_SYSTEM_PROMPT.count("unstated_kind") == 1
    assert "Only for none" in PAIR_SYSTEM_PROMPT


def test_propose_returns_the_kind() -> None:
    class _LLM:
        def complete(self, system: str, user: str) -> str:
            return json.dumps({**NONE_ANS, "unstated_kind": "条件"}, ensure_ascii=False)

    assert propose_pair(_LLM(), PAIR)["unstated_kind"] == "条件"  # type: ignore[arg-type]
