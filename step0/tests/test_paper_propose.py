"""12b 追補 — AI の仕事を「名前 → 関係 → 名前＋根拠の文の番号」に絞った形（paper_propose）。

AI は偽物（ネットワークなし）。確かめること: 1 本ずつ落とす（形の悪い 1 本だけ）・``rel`` は
5 つだけ・``evidence`` は番号 1〜3 個で必須・鍵や種類の欄が来ても無視・20 本まで・JSON が読めない
ときだけ聞き直す・スキーマは全欄必須で guided decoding にかける。
"""

from __future__ import annotations

import json

import pytest

from asterism_step0.paper_propose import (
    PAPER_RELS,
    PaperProposeError,
    paper_relations_json_schema,
    propose_paper_relations,
    validate_paper_relations,
)

GOOD = {"from": "synthesis procedure", "from_ja": "合成手順", "from_en": None, "rel": "part",
        "to": "MatPROV dataset", "to_ja": None, "to_en": "MatPROV dataset",
        "evidence": ["s12"]}  # fmt: skip
SENTENCES = [{"id": "s12", "text": "The synthesis procedure is part of the MatPROV dataset."}]


class _LLM:
    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.responses.pop(0)


def test_a_good_relation_is_kept_and_extra_fields_are_ignored() -> None:
    noisy = {**GOOD, "key": "n1", "kind": "class", "question": "?", "reason": "x", "use": "new"}
    out = validate_paper_relations({"relations": [noisy], "ops": [1]})
    assert out == {"relations": [{**GOOD}], "shape_dropped": 0, "over_limit": 0}


def test_each_relation_is_judged_alone_and_the_bad_ones_are_counted() -> None:
    bad = [
        {**GOOD, "rel": "causes"},  # rel は 5 つだけ
        {**GOOD, "evidence": []},  # 必須
        {k: v for k, v in GOOD.items() if k != "evidence"},
        {**GOOD, "evidence": ["s1", "s2", "s3", "s4"]},  # 3 個まで
        {**GOOD, "evidence": ["The synthesis procedure is part of it."]},  # 引用文は不可
        {**GOOD, "evidence": ["https://x/sent/1"]},
        {**GOOD, "from": ""},
        {**GOOD, "to": "http://example.org/x"},  # IRI に見える名前
        {**GOOD, "to": "Synthesis Procedure"},  # 自分自身
        "not an object",
    ]
    out = validate_paper_relations({"relations": [bad[0], GOOD, *bad[1:]]})
    assert out["relations"] == [GOOD] and out["shape_dropped"] == len(bad)
    assert set(PAPER_RELS) == {"part", "sub", "lowers", "raises", "affects"}


def test_at_most_twenty_relations() -> None:
    items = [{**GOOD, "to": f"dataset {i}"} for i in range(25)]
    out = validate_paper_relations({"relations": items})
    assert len(out["relations"]) == 20 and out["over_limit"] == 5 and out["shape_dropped"] == 0


def test_a_bad_japanese_name_only_loses_the_japanese_name() -> None:
    out = validate_paper_relations({"relations": [{**GOOD, "from_ja": 3, "to_ja": "x" * 200}]})
    assert out["relations"][0]["from_ja"] is None and out["relations"][0]["to_ja"] is None


@pytest.mark.parametrize("raw", [[], "x", {"relations": "no"}, {"ops": []}])
def test_only_an_unreadable_shape_is_refused(raw: object) -> None:
    with pytest.raises(PaperProposeError):
        validate_paper_relations(raw)


def test_schema_requires_every_field_and_pins_rel_and_evidence() -> None:
    item = paper_relations_json_schema()["properties"]["relations"]["items"]
    assert item["required"] == [
        "from", "from_ja", "from_en", "rel", "to", "to_ja", "to_en", "evidence",
    ]
    assert item["additionalProperties"] is False
    assert item["properties"]["rel"]["enum"] == list(PAPER_RELS)
    assert item["properties"]["evidence"]["items"]["pattern"] == "^s[0-9]{1,4}$"
    assert all("null" in item["properties"][k]["type"]
               for k in ("from_ja", "from_en", "to_ja", "to_en"))  # fmt: skip


def test_propose_sends_sentences_and_existing_names_and_keeps_good_relations() -> None:
    llm = _LLM(json.dumps({"relations": [GOOD, {**GOOD, "rel": "x"}]}))
    out = propose_paper_relations(
        llm, SENTENCES, [{"label_ja": "熱伝導率", "label_en": "thermal conductivity"}], "ja"
    )
    system, user = llm.calls[0]
    assert "s12 | The synthesis procedure is part of" in user
    assert "熱伝導率 | thermal conductivity" in user
    assert "keys, kinds, questions" in system
    assert out == {"relations": [GOOD], "shape_dropped": 1, "over_limit": 0}
    assert "from_en" in system and "never translate" in system
    assert "日本語で" not in user and "Japanese" not in user.split("# Existing terms")[0]
    assert len(llm.calls) == 1  # 1 本の外れでは聞き直さない


def test_propose_asks_again_only_when_the_json_cannot_be_read() -> None:
    llm = _LLM(
        "not json at all", json.dumps({"relations": "oops"}), json.dumps({"relations": [GOOD]})
    )
    out = propose_paper_relations(llm, SENTENCES, [], "ja")
    assert len(llm.calls) == 3 and out["relations"] == [GOOD]
    assert "was refused" in llm.calls[1][1] and "relations" in llm.calls[2][1]
    with pytest.raises(PaperProposeError):
        propose_paper_relations(_LLM("x", "y", "z"), SENTENCES, [], "ja")
