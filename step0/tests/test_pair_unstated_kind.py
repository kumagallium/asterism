# ruff: noqa: RUF001
"""R1 — 「言えない」のとき、関係の種類の短い名詞 unstated_kind も書かせる（pair_propose）。"""

from __future__ import annotations

import json

import pytest

from asterism_step0.pair_propose import (
    PAIR_SYSTEM_PROMPT,
    _clean_kind,
    pair_json_schema,
    propose_pair,
    validate_pair_answer,
)

# (入力, 期待)。None は捨てる。step0 と ingest で同じ表（見張りは step0 のテスト）
KIND_TABLE = [
    ("条件", "条件"), ("  条件  ", "条件"), ("条　件", "条 件"), ("条件\n比較", "条件 比較"),
    ("ＺＴ値", "ZT値"), ("🔥条件", "🔥条件"), ("a\x1bb", "ab"), ("条\x00件\x07", "条件"),
    ("条​件", "条件"), ("‮条件", "条件"), ("‮تحليل‬", "تحليل"),
    ("あ" * 20, "あ" * 20), ("あ" * 21, None), ("a" * 100, None), ("あ" * 19 + "👨‍👩‍👧", None),
    ("a" * 19 + "🇯🇵", None), ("🔥🔥", None), ("​​", None), ("\x00", None),
    ("---", None), ("123", None), ("①②", None), ("²", None), ("?!", None),
    ("none", None), ("None", None), ("NULL", None), ("n/a", None), ("N/A", None), ("na", None),
    ("unknown", None), ("nothing", None), ("no relation", None), ("  No  Relation ", None),
    ("なし", None), ("無し", None), ("不明", None), ("該当なし", None), ("関係なし", None),
    ("分からない", None), ("わからない", None), ("ＮＯＮＥ", None), ("ｎ／ａ", None),
    ("makes", None), ("Lowers", None), ("has_property", None), ("sub", None),
    ("c1", None), ("C12", None), ("c1, c2", None), ("c1,c2,c3", None), ("a", None), ("B", None),
    ("<b>条件</b>", None), ("{a}", None), ("`code`", None), ("select x", None), ("where", None),
    ("https://x.test/a", None), ("urn:a:b", None), ("javascript:alert", None),
    ("data:text/html,abc", None), ("mailto:a@b.c", None), ("file:///etc/passwd", None),
    ("ftp://x", None), ("//evil.com", None), ("www.evil.com", None), ("WWW.evil.com", None),
    ("[条件](https://x.y)", None), ("[条件](javascript:alert)", None), ("[a](b)", None),
    ("条件]", None), ("a//b", None),
    (5, None), (None, None), (["条件"], None), ({"x": 1}, None), ("", None),
]

NONE_ANS = {"rel": "none", "from": None, "evidence": [], "note": "条件の話"}
PAIR = {
    "a": {"name": "焼結温度", "kind": "property", "role": None},
    "b": {"name": "密度", "kind": "property", "role": None},
    "claims": ["焼結温度が 900 度のとき密度を比べた。"],
}


@pytest.mark.parametrize(("raw", "want"), KIND_TABLE)
def test_the_rule_table(raw: object, want: str | None) -> None:
    assert _clean_kind(raw) == want


def test_step0_and_ingest_give_the_same_answer_on_the_table() -> None:
    pr = pytest.importorskip("asterism.pair_relations")
    for raw, _ in KIND_TABLE:
        assert pr._unstated_kind(raw) == _clean_kind(raw), repr(raw)
    for raw in ("pair", "x" * 20, "x" * 21, "ｃ１"):  # 表の外でも同じ
        assert pr._unstated_kind(raw) == _clean_kind(raw), repr(raw)
    assert pr._LEN_UNSTATED_KIND == 20


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


def test_schema_has_an_optional_field() -> None:
    s = pair_json_schema()
    assert "unstated_kind" not in s["required"]
    assert s["properties"]["unstated_kind"]["maxLength"] == 20


def test_prompt_mentions_the_field_once_for_none_only() -> None:
    assert PAIR_SYSTEM_PROMPT.count("unstated_kind") == 1
    assert "Only for none" in PAIR_SYSTEM_PROMPT
    assert "no effect" not in PAIR_SYSTEM_PROMPT and "never write none" in PAIR_SYSTEM_PROMPT


def test_propose_returns_the_kind() -> None:
    class _LLM:
        def complete(self, system: str, user: str) -> str:
            return json.dumps({**NONE_ANS, "unstated_kind": "条件"}, ensure_ascii=False)

    assert propose_pair(_LLM(), PAIR)["unstated_kind"] == "条件"  # type: ignore[arg-type]
