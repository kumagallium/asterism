"""PR R3b — 人が足した関係を AI に渡す（別名 x1… で答える・検査の後で名前に戻す）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from asterism_step0.pair_propose import (
    MAX_EXTRA,
    NONE,
    PAIR_RELS,
    PAIR_SYSTEM_PROMPT,
    PairProposeError,
    assign_aliases,
    build_pair_system,
    pair_json_schema,
    propose_pair,
    validate_pair_answer,
)

PAIR = {
    "a": {"name": "梁", "kind": "class", "role": None},
    "b": {"name": "柱", "kind": "class", "role": None},
    "claims": ["梁が柱を支える。", "柱は梁と接する。"],
}


def _item(n: int, **kw) -> dict:
    base = {
        "name": f"u_{n:08x}",
        "label": {"ja": f"関係{n}", "en": f"rel{n}"},
        "definition": {"ja": f"説明{n}\n二行目", "en": f"desc{n}"},
        "symmetric": False,
        "to_property_only": False,
    }
    base.update(kw)
    return base


class _LLM:
    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.responses.pop(0)


def test_zero_added_is_byte_identical() -> None:
    assert build_pair_system(None) == PAIR_SYSTEM_PROMPT
    assert build_pair_system({}, "en") == PAIR_SYSTEM_PROMPT
    assert pair_json_schema() == pair_json_schema({})
    assert pair_json_schema()["properties"]["rel"]["enum"] == [*PAIR_RELS, NONE]
    llm = _LLM(json.dumps({"rel": "none", "from": None, "evidence": [], "note": ""}))
    propose_pair(llm, PAIR, "ja", extra=[])
    assert llm.calls[0][0] == PAIR_SYSTEM_PROMPT


def test_aliases_in_order_and_no_collision() -> None:
    al = assign_aliases([_item(1), _item(2), _item(3)])
    assert list(al) == ["x1", "x2", "x3"]
    assert al["x2"]["name"] == "u_00000002"
    assert not ({*al} & {*PAIR_RELS, NONE})


def test_section_text_and_language_choice() -> None:
    al = assign_aliases(
        [
            _item(1, symmetric=True, to_property_only=True),
            _item(2, label={"en": "only-en"}, definition={"en": "d"}),
        ]
    )
    ja = build_pair_system(al, "ja")
    assert ja.startswith(PAIR_SYSTEM_PROMPT)
    assert 'Relations added by the user (answer with the short code, e.g. "x1"):' in ja
    assert "x1 = 関係1: 説明1 二行目 [no direction] [to is a property]" in ja
    assert "x2 = only-en: d" in ja  # ja が無ければ en
    assert "u_0000" not in ja  # 名前は見せない
    en = build_pair_system(al, "en")
    assert "x1 = rel1: desc1 二行目" not in en and "x1 = rel1: desc1 [no direction]" in en


def test_long_definition_is_not_cut() -> None:
    d = "あ" * 200
    sec = build_pair_system(assign_aliases([_item(1, definition={"ja": d})]), "ja")
    assert d in sec


def test_schema_enum_has_aliases() -> None:
    al = assign_aliases([_item(1), _item(2)])
    assert pair_json_schema(al)["properties"]["rel"]["enum"] == [*PAIR_RELS, "x1", "x2", NONE]


def test_alias_answer_is_restored_to_name() -> None:
    al = assign_aliases([_item(1)])
    out = validate_pair_answer({"rel": "x1", "from": "b", "evidence": ["c1"], "note": "n"}, 2, al)
    assert out["rel"] == "u_00000001" and out["from"] == "b"


def test_symmetric_alias_forces_from_a() -> None:
    al = assign_aliases([_item(1, symmetric=True)])
    out = validate_pair_answer({"rel": "x1", "from": "b", "evidence": ["c1"], "note": ""}, 2, al)
    assert out["from"] == "a"


def test_direct_name_and_unknown_alias_are_refused() -> None:
    al = assign_aliases([_item(1)])
    for bad in ("u_00000001", "x2", "x1 "):
        with pytest.raises(PairProposeError):
            validate_pair_answer({"rel": bad, "from": "a", "evidence": ["c1"], "note": ""}, 2, al)
    # 足した関係が無いときは別名も断る（今まで通り）
    with pytest.raises(PairProposeError):
        validate_pair_answer({"rel": "x1", "from": "a", "evidence": ["c1"], "note": ""}, 2)


def test_cap_is_40() -> None:
    al = assign_aliases([_item(i) for i in range(MAX_EXTRA + 5)])
    assert len(al) == 40 and "x40" in al and "x41" not in al
    assert al["x40"]["name"] == f"u_{39:08x}"


def test_propose_retries_when_name_written() -> None:
    bad = json.dumps({"rel": "u_00000001", "from": "a", "evidence": ["c1"], "note": ""})
    good = json.dumps({"rel": "x1", "from": "a", "evidence": ["c1"], "note": ""})
    llm = _LLM(bad, good)
    out = propose_pair(llm, PAIR, "ja", extra=[_item(1)])
    assert out["rel"] == "u_00000001" and len(llm.calls) == 2
    assert "x1 = 関係1" in llm.calls[0][0]


def test_step0_does_not_import_ingest() -> None:
    """step0 のこの機能は ingest を import しない（項目は api が辞書で渡す）。"""
    src = (Path(__file__).parents[1] / "src" / "asterism_step0" / "pair_propose.py").read_text()
    assert "import asterism." not in src and "from asterism " not in src


def test_alias_in_note_becomes_label_and_in_kind_is_dropped() -> None:
    al = assign_aliases([_item(1), _item(2)])
    ans = {"rel": "x1", "from": "a", "evidence": ["c1"], "note": "x1を使い、x2でなくx3ではない x10"}
    out = validate_pair_answer(ans, 2, al, "ja")
    assert out["note"] == "関係1を使い、関係2でなくx3ではない x10"
    assert validate_pair_answer(ans, 2, al, "en")["note"].startswith("rel1を使い")
    none = {"rel": "none", "from": None, "evidence": [], "note": "x2 とは違う"}
    assert validate_pair_answer({**none, "unstated_kind": "x2"}, 1, al)["note"] == "関係2 とは違う"
    assert "unstated_kind" not in validate_pair_answer({**none, "unstated_kind": "x2"}, 1, al)
    assert validate_pair_answer({**none, "unstated_kind": "条件"}, 1, al)["unstated_kind"] == "条件"
    # 足した関係が無いときは触らない
    assert validate_pair_answer({**none, "note": "x1"}, 1)["note"] == "x1"


def test_never_write_codes_sentence_only_with_added() -> None:
    sentence = "Never write the short codes in note or unstated_kind"
    assert sentence not in PAIR_SYSTEM_PROMPT
    assert sentence in build_pair_system(assign_aliases([_item(1)]), "ja")


def test_brackets_in_definition_are_replaced_and_empty_has_no_colon() -> None:
    al = assign_aliases(
        [_item(1, definition={"ja": "A [no direction] B"}), _item(2, definition={})]
    )
    sec = build_pair_system(al, "ja")
    assert "x1 = 関係1: A (no direction) B" in sec
    assert "x1 = 関係1: A (no direction) B [" not in sec
    assert "\nx2 = 関係2\n" in sec
    assert al["x1"]["definition"]["ja"] == "A [no direction] B"  # 保存は変えない
