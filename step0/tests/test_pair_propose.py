"""PR 14c — 語 2 つの関係を閉じた集合から選ばせる（pair_propose）。AI は偽物。"""

from __future__ import annotations

import json

import pytest

from asterism_step0.pair_propose import (
    MAX_CLAIMS,
    PAIR_RELS,
    SYMMETRIC_RELS,
    PairProposeError,
    build_pair_user,
    pair_json_schema,
    propose_pair,
    validate_pair_answer,
)

PAIR = {
    "a": {"name": "アーク溶解", "kind": "class", "role": "method"},
    "b": {"name": "Mg2Si", "kind": "class", "role": "material"},
    "claims": ["アーク溶解で Mg2Si を作った。", "Mg2Si の熱伝導率を測った。"],
}
GOOD = {"rel": "makes", "from": "a", "evidence": ["c1"], "note": "アーク溶解で作ったと書いてある"}


class _LLM:
    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.responses.pop(0)


def test_a_good_answer_passes() -> None:
    assert validate_pair_answer(GOOD, 2) == GOOD


@pytest.mark.parametrize("frm", ["a", "b", None, "c", 3])
def test_none_is_normalized(frm: object) -> None:
    out = validate_pair_answer({"rel": "none", "from": frm, "evidence": ["c1"], "note": "x"}, 2)
    assert out == {"rel": "none", "from": None, "evidence": [], "note": "x"}


@pytest.mark.parametrize("rel", ["eq", "competes"])
def test_a_relation_without_direction_has_from_a(rel: str) -> None:
    out = validate_pair_answer({**GOOD, "rel": rel, "from": "b"}, 2)
    assert out["from"] == "a"


def test_evidence_is_deduplicated_and_capped_at_three() -> None:
    out = validate_pair_answer({**GOOD, "evidence": ["c1", "c1", "c2", "c3", "c4"]}, 5)
    assert out["evidence"] == ["c1", "c2", "c3"]


def test_out_of_range_numbers_are_dropped_and_all_dropped_is_refused() -> None:
    out = validate_pair_answer({**GOOD, "evidence": ["c9", "c2", "c0", "x1"]}, 2)
    assert out["evidence"] == ["c2"]
    with pytest.raises(PairProposeError):
        validate_pair_answer({**GOOD, "evidence": ["c3"]}, 2)
    with pytest.raises(PairProposeError):
        validate_pair_answer({**GOOD, "evidence": []}, 2)


@pytest.mark.parametrize(
    "bad",
    [
        {**GOOD, "rel": "causes"},
        {**GOOD, "from": "c"},
        {**GOOD, "from": None},
        {**GOOD, "evidence": "c1"},
        "x",
    ],
)
def test_a_wrong_shape_is_refused(bad: object) -> None:
    with pytest.raises(PairProposeError):
        validate_pair_answer(bad, 2)


def test_note_is_cut_and_code_like_note_is_emptied() -> None:
    assert len(validate_pair_answer({**GOOD, "note": "あ" * 500}, 2)["note"]) == 200
    assert validate_pair_answer({**GOOD, "note": "see https://x.example/a"}, 2)["note"] == ""
    assert validate_pair_answer({**GOOD, "note": "SELECT ?s WHERE"}, 2)["note"] == ""


def test_propose_asks_again_after_a_broken_json() -> None:
    llm = _LLM("{not json", json.dumps(GOOD))
    assert propose_pair(llm, PAIR, "ja") == GOOD
    assert len(llm.calls) == 2 and "was refused" in llm.calls[1][1]


def test_propose_asks_again_after_a_refused_answer_and_gives_up_after_three() -> None:
    bad = json.dumps({**GOOD, "evidence": ["c7"]})
    llm = _LLM(bad, bad, bad)
    with pytest.raises(PairProposeError):
        propose_pair(llm, PAIR)
    assert len(llm.calls) == 3 and "c1 and c2" in llm.calls[2][1]


def test_propose_reads_a_fenced_json() -> None:
    llm = _LLM("```json\n" + json.dumps(GOOD) + "\n```")
    assert propose_pair(llm, PAIR) == GOOD and len(llm.calls) == 1


def test_propose_checks_the_input() -> None:
    llm = _LLM()
    with pytest.raises(ValueError):
        propose_pair(llm, {**PAIR, "claims": []})
    with pytest.raises(ValueError):
        propose_pair(llm, {**PAIR, "claims": ["s"] * (MAX_CLAIMS + 1)})
    with pytest.raises(ValueError):
        propose_pair(llm, {**PAIR, "a": {"name": " ", "kind": "class", "role": None}})
    assert llm.calls == []


def test_build_user_with_and_without_a_role() -> None:
    user = build_pair_user({**PAIR, "b": {"name": "熱伝導率", "kind": "property", "role": None}})
    assert "a: アーク溶解 (kind: class, role: method)" in user
    assert "b: 熱伝導率 (kind: property)\n" in user
    assert "Statements:\nc1: アーク溶解で Mg2Si を作った。\nc2: " in user
    assert "Japanese" in user and "English" in build_pair_user(PAIR, "en")


def test_build_user_cuts_a_long_statement_and_folds_spaces() -> None:
    user = build_pair_user({**PAIR, "claims": ["X 線回折  で\n" + "あ" * 900]})
    line = next(x for x in user.splitlines() if x.startswith("c1: "))
    assert "X 線回折 で あ" in line and len(line) == len("c1: ") + 600


def test_schema_pins_rel_from_and_evidence() -> None:
    s = pair_json_schema()
    assert s["additionalProperties"] is False
    assert s["required"] == ["rel", "from", "evidence", "note"]
    assert s["properties"]["rel"]["enum"] == [*PAIR_RELS, "none"]
    assert s["properties"]["from"]["enum"] == ["a", "b", None]
    assert s["properties"]["evidence"]["maxItems"] == 3
    assert s["properties"]["evidence"]["items"]["pattern"] == "^c[0-9]{1,2}$"
    assert s["properties"]["note"]["maxLength"] == 200


def test_pair_rels_match_ingest_relation_kinds() -> None:
    themes = pytest.importorskip("asterism.themes")
    assert tuple(r for r in themes.RELATION_KINDS if r != "quantity") == PAIR_RELS


def test_on_call_runs_once_per_successful_complete() -> None:
    n: list[int] = []
    llm = _LLM("{not json", json.dumps(GOOD))
    propose_pair(llm, PAIR, "ja", on_call=lambda: n.append(1))
    assert len(n) == 2  # 聞き直しの各回も

    class _Down:
        def complete(self, system: str, user: str) -> str:
            raise RuntimeError("down")

    m: list[int] = []
    with pytest.raises(RuntimeError):
        propose_pair(_Down(), PAIR, "ja", on_call=lambda: m.append(1))
    assert m == []  # 例外で落ちた回は呼ばない


def test_symmetric_rels_match_ingest() -> None:
    pr = pytest.importorskip("asterism.pair_relations")
    assert tuple(SYMMETRIC_RELS) == tuple(pr.SYMMETRIC_PAIR_RELATIONS)
