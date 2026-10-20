"""asterism.graph_diff — 作業場の会話の差分を、テーマの下書きに当てる（ui-redesign PR 7a）。

ストアも AI も使わない（純粋な関数）。下書きは ``themes.postprocess_draft`` が作る v2 の形
（熱伝導率の例）をそのまま使い、差分は ``asterism_step0.graph_propose`` が検査したあとの形の dict。
"""

# ruff: noqa: RUF001
from __future__ import annotations

import copy

from asterism import graph_diff as gd
from asterism import shared_vocab as sv
from asterism import themes

AT = "2026-10-20T00:00:00+00:00"
Q = "熱伝導率を下げる要素は？"


def _concept(key, ja, en, kind, use="new", reason=""):
    return {"key": key, "label_ja": ja, "label_en": en, "aliases": [], "kind": kind,
            "use": use, "reason": reason}  # fmt: skip


def _draft(*, wrong: bool = True) -> dict:
    """熱伝導率の例。``wrong`` = 散乱因子が（格子でなく）熱伝導率そのものを下げる線になっている。"""
    ai = {
        "theme_label": "熱電材料",
        "concepts": [
            _concept("c1", "熱伝導率", "thermal conductivity", "property"),
            _concept("c2", "格子熱伝導率", "lattice thermal conductivity", "property"),
            _concept("c3", "電子熱伝導率", "electronic thermal conductivity", "property"),
            _concept("c4", "粒界散乱", "grain boundary scattering", "class"),
            _concept("c5", "点欠陥散乱", "point defect scattering", "class"),
            _concept("c6", "ナノ析出物", "nano precipitate", "class"),
        ],
        "relations": [
            {"from": "c2", "rel": "part", "to": "c1", "reason": ""},
            {"from": "c3", "rel": "part", "to": "c1", "reason": ""},
            *[
                {"from": k, "rel": "lowers", "to": "c1" if wrong else "c2", "reason": "旧"}
                for k in ("c4", "c5", "c6")
            ],
        ],
        "answers": [
            {"question": Q, "mode": "knowledge", "op": "factors", "target": "c1",
             "direction": "lowers", "kind": None, "property": None, "wanted_data": "",
             "reason": ""},
        ],
        "suggested_questions": [],
    }  # fmt: skip
    out = themes.postprocess_draft(ai, questions=[Q], existing=[])
    return {
        "version": 2, "id": "heat", "label": "熱電材料", "step": 2, "questions": [Q],
        "concepts": out["concepts"], "relations": out["relations"], "answers": out["answers"],
        "suggested_questions": [], "standards": out["standards"], "updated_at": AT,
    }  # fmt: skip


def _apply(draft, ops, **kw):
    return gd.apply_to_draft(draft, ops, existing=kw.pop("existing", []), **kw)


def _rel(draft, frm, rel):
    return [r["to"] for r in draft["relations"] if r["from"] == frm and r["rel"] == rel]


def _retarget(frm, to, new_to, rel="lowers", reason="フォノンの散乱"):
    return {"op": "retarget", "from": frm, "rel": rel, "to": to, "new_to": new_to, "reason": reason}


def test_three_retargets_move_the_scattering_factors_to_the_lattice_part() -> None:
    draft = _draft()
    ops = [_retarget(k, "c1", "c2") for k in ("c4", "c5", "c6")]
    out, report = _apply(draft, ops)
    assert [_rel(out, k, "lowers") for k in ("c4", "c5", "c6")] == [["c2"], ["c2"], ["c2"]]
    assert report == {"applied": 3, "dropped": [], "unlinked": []}
    # the lines stay in place and keep one row each; the reason is the new one
    assert len(out["relations"]) == len(draft["relations"])
    assert {r["reason"] for r in out["relations"] if r["rel"] == "lowers"} == {"フォノンの散乱"}
    assert all(r["by"] == "ai" for r in out["relations"] if r["rel"] == "lowers")
    # the input is not touched
    assert _rel(draft, "c4", "lowers") == ["c1"]


def test_a_retarget_of_a_line_that_is_not_there_is_dropped() -> None:
    # the old line is gone and the new one is not there either → not_found
    out, report = _apply(_draft(wrong=False), [_retarget("c4", "c3", "c1")])
    assert report["applied"] == 0
    assert [(d["op"], d["reason"], d["index"]) for d in report["dropped"]] == [
        ("retarget", "not_found", 0)
    ]
    assert _rel(out, "c4", "lowers") == ["c2"]


def test_a_retarget_that_is_already_applied_is_a_duplicate() -> None:
    # replay: the old line moved already and the new one is there → a no-op, not "not found"
    out, report = _apply(_draft(wrong=False), [_retarget("c4", "c1", "c2")])
    assert report["applied"] == 0
    assert [d["reason"] for d in report["dropped"]] == ["duplicate"]
    assert _rel(out, "c4", "lowers") == ["c2"]


def test_a_retarget_that_breaks_a_rule_is_dropped_and_the_old_line_stays() -> None:
    # lowers needs a property at the far end: c4 → c5 (a class) is refused
    out, report = _apply(_draft(), [_retarget("c4", "c1", "c5")])
    assert report["dropped"][0]["reason"] == "target_not_property"
    assert _rel(out, "c4", "lowers") == ["c1"]


def test_a_cycle_in_part_of_is_dropped_with_its_reason() -> None:
    draft = _draft()
    op = {"op": "add_relation", "from": "c1", "rel": "part", "to": "c2", "reason": "逆"}
    out, report = _apply(draft, [op])
    assert report["applied"] == 0
    assert [(d["reason"], d["ref"]) for d in report["dropped"]] == [("cycle", "c1 -part-> c2")]
    assert out["relations"] == draft["relations"]
    # a longer loop is caught as well (c2 ⊂ c1 and c1 ⊂ c3 would make c3 above c2 above c1 ...)
    two = [
        {"op": "add_relation", "from": "c1", "rel": "part", "to": "c3", "reason": ""},
        {"op": "add_relation", "from": "c3", "rel": "part", "to": "c2", "reason": ""},
    ]
    _out, rep = _apply(draft, two)
    assert rep["applied"] == 1 and rep["dropped"][0]["reason"] == "cycle"


def test_a_feedback_loop_of_lowers_is_legitimate() -> None:
    ops = [
        {"op": "add_relation", "from": "c2", "rel": "lowers", "to": "c3", "reason": ""},
        {"op": "add_relation", "from": "c3", "rel": "lowers", "to": "c2", "reason": ""},
    ]
    out, report = _apply(_draft(), ops)
    assert report["applied"] == 2 and not report["dropped"]
    assert _rel(out, "c2", "lowers") == ["c3"] and _rel(out, "c3", "lowers") == ["c2"]


def test_kinds_must_fit_each_predicate() -> None:
    draft = _draft()
    cases = [
        ({"from": "c4", "rel": "part", "to": "c2"}, "mixed"),  # class part of property
        ({"from": "c4", "rel": "sub", "to": "c1"}, "mixed"),
        ({"from": "c2", "rel": "lowers", "to": "c4"}, "target_not_property"),
        ({"from": "c2", "rel": "eq", "to": "c2"}, "same"),
        ({"from": "c2", "rel": "quantity", "to": "c1"}, "quantity_target"),
        ({"from": "c2", "rel": "part", "to": "c1"}, "duplicate"),
        ({"from": "zz", "rel": "part", "to": "c1"}, "unknown_key"),
        ({"from": "c2", "rel": "part", "to": "nope:Thing"}, "unknown_key"),  # not a standard
    ]
    for rel, why in cases:
        _out, report = _apply(draft, [{"op": "add_relation", "reason": "", **rel}])
        assert [d["reason"] for d in report["dropped"]] == [why], rel


def test_a_standard_term_can_be_the_far_end() -> None:
    op = {"op": "add_relation", "from": "c1", "rel": "quantity",
          "to": "quantitykind:ThermalConductivity", "reason": "量の種類"}  # fmt: skip
    out, report = _apply(_draft(), [op])
    assert report["applied"] == 1
    assert _rel(out, "c1", "quantity") == ["quantitykind:ThermalConductivity"]
    assert out["standards"]["quantitykind:ThermalConductivity"]["kind"] == "quantity"


def test_a_dismissed_relation_is_dropped() -> None:
    draft = _draft(wrong=False)
    c = {r["key"]: r for r in draft["concepts"]}
    dismissed = [{"subject": themes.concept_end_key(c["c4"]),
                  "term": themes.concept_end_key(c["c3"]),
                  "relation": "lowers", "at": AT}]  # fmt: skip
    op = {"op": "add_relation", "from": "c4", "rel": "lowers", "to": "c3", "reason": ""}
    out, report = _apply(draft, [op], dismissed=dismissed)
    assert [d["reason"] for d in report["dropped"]] == ["dismissed"]
    assert out["relations"] == draft["relations"]
    # another predicate for the same pair is not dismissed
    _o, rep2 = _apply(draft, [{**op, "rel": "raises"}], dismissed=dismissed)
    assert rep2["applied"] == 1


def test_removing_a_concept_takes_its_lines_and_turns_its_questions_into_memos() -> None:
    draft = _draft()
    out, report = _apply(draft, [{"op": "remove", "target": "concept", "key": "c1", "reason": ""}])
    assert report["applied"] == 1
    assert "c1" not in {c["key"] for c in out["concepts"]}
    assert all("c1" not in (r["from"], r["to"]) for r in out["relations"])
    assert [r["from"] for r in out["relations"]] == []  # every line touched c1
    (ans,) = out["answers"]
    assert (ans["mode"], ans["memo"], ans["target"]) == ("memo", "ai", "")
    assert ans["question"] == Q


def test_removing_a_line_leaves_the_concepts() -> None:
    op = {"op": "remove", "target": "relation", "from": "c4", "rel": "lowers", "to": "c1",
          "reason": ""}  # fmt: skip
    out, report = _apply(_draft(), [op])
    assert report["applied"] == 1 and len(out["concepts"]) == 6
    assert _rel(out, "c4", "lowers") == []
    _o, rep = _apply(_draft(), [op, op])  # the second one finds nothing
    assert [d["reason"] for d in rep["dropped"]] == ["not_found"]


def test_add_question_goes_to_answers_and_to_the_question_list() -> None:
    op = {"op": "add_question", "question": "粒界散乱が下げるものは？", "mode": "knowledge",
          "op_kind": "factors", "target": "c2", "direction": "lowers", "kind": None,
          "property": None, "wanted_data": "粒径と熱伝導率の表", "reason": "会話から"}  # fmt: skip
    out, report = _apply(_draft(), [op])
    assert report["applied"] == 1
    assert out["questions"] == [Q, "粒界散乱が下げるものは？"]
    new = out["answers"][-1]
    assert (new["mode"], new["op"], new["target"], new["direction"], new["memo"]) == (
        "knowledge", "factors", "c2", "lowers", None,
    )  # fmt: skip
    assert new["wanted_data"] == "粒径と熱伝導率の表" and new["by"] == "ai"
    # asking the same question again is a duplicate
    _o, rep = _apply(out, [op])
    assert [d["reason"] for d in rep["dropped"]] == ["duplicate"]


def test_a_question_the_machine_cannot_answer_stays_as_a_memo_question() -> None:
    op = {"op": "add_question", "question": "容量が最大のセルは？", "mode": "data",
          "op_kind": "top", "target": None, "direction": None, "kind": "c4",
          "property": "c4", "wanted_data": "", "reason": ""}  # fmt: skip
    out, report = _apply(_draft(), [op])  # property = c4 is a class → no way to answer
    assert report["applied"] == 1
    last = out["answers"][-1]
    assert (last["mode"], last["memo"]) == ("memo", "ai")
    ok = {**op, "question": "容量の範囲は？", "op_kind": "range", "kind": None, "property": "c2"}
    out2, _ = _apply(out, [ok])
    assert (out2["answers"][-1]["mode"], out2["answers"][-1]["property"]) == ("data", "c2")


def test_add_concept_matches_exact_names_and_the_match_beats_the_models_use() -> None:
    op = {"op": "add_concept", "key": "n1", "label_ja": "温度", "label_en": "Temperature",
          "aliases": [], "kind": "property", "use": "new", "reason": "条件"}  # fmt: skip
    out, report = _apply(_draft(), [op])
    assert report["applied"] == 1
    row = out["concepts"][-1]
    assert row["key"] == "n1" and row["by"] == "ai" and row["reason"] == "条件"
    assert row["use"] == "standard:quantitykind:Temperature"
    assert out["standards"]["quantitykind:Temperature"]["kind"] == "quantity"
    existing = [
        sv.SharedTerm(sv.SV + "annealing", "annealing", "class", "焼きなまし", None, None, AT)
    ]
    op2 = {**op, "key": "n2", "label_ja": "焼きなまし", "label_en": "", "kind": "class"}
    out2, _ = _apply(_draft(), [op2], existing=existing)
    assert (out2["concepts"][-1]["use"], out2["concepts"][-1]["slug"]) == (
        "existing:annealing", "annealing",
    )  # fmt: skip


def test_a_two_character_alias_does_not_reach_a_standard_term() -> None:
    op = {"op": "add_concept", "key": "n1", "label_ja": "キャリア濃度",
          "label_en": "carrier concentration", "aliases": ["n"], "kind": "property",
          "use": "new", "reason": ""}  # fmt: skip
    out, _ = _apply(_draft(), [op])
    assert out["concepts"][-1]["use"] == "new"


def test_a_concept_with_a_name_already_on_the_map_is_folded_into_it() -> None:
    ops = [
        {"op": "add_concept", "key": "n1", "label_ja": "格子熱伝導率", "label_en": "",
         "aliases": [], "kind": "property", "use": "new", "reason": ""},
        {"op": "add_relation", "from": "c4", "rel": "lowers", "to": "n1", "reason": "x"},
    ]  # fmt: skip
    draft = _draft(wrong=False)
    out, report = _apply(draft, ops)
    assert [d["reason"] for d in report["dropped"]] == ["duplicate", "duplicate"]
    assert len(out["concepts"]) == len(draft["concepts"])
    # a same-named concept of the OTHER kind is a different row
    op = {**ops[0], "kind": "class"}
    out2, rep2 = _apply(draft, [op])
    assert rep2["applied"] == 1 and len(out2["concepts"]) == len(draft["concepts"]) + 1


def test_a_new_concept_can_be_the_far_end_of_a_later_line_and_a_taken_key_is_renumbered() -> None:
    ops = [
        {"op": "add_concept", "key": "c1", "label_ja": "結晶粒径", "label_en": "grain size",
         "aliases": [], "kind": "property", "use": "new", "reason": ""},
        {"op": "add_relation", "from": "c1", "rel": "affects", "to": "c2",
         "reason": "粒径で変わる"},
    ]  # fmt: skip
    out, report = _apply(_draft(wrong=False), [*ops])
    assert report["applied"] == 2
    new = out["concepts"][-1]
    assert new["key"] != "c1" and new["label_ja"] == "結晶粒径"
    assert _rel(out, new["key"], "affects") == ["c2"]


def test_a_dismissed_concept_is_dropped_and_so_are_its_lines() -> None:
    dismissed = [{"subject": sv.concept_subject("点欠陥散乱"), "term": sv.CONCEPT_DISMISS_TERM,
                  "at": AT}]  # fmt: skip
    draft = _draft(wrong=False)
    draft["concepts"] = [c for c in draft["concepts"] if c["key"] != "c5"]
    draft["relations"] = [r for r in draft["relations"] if r["from"] != "c5"]
    ops = [
        {"op": "add_concept", "key": "n1", "label_ja": "点欠陥散乱", "label_en": "", "aliases": [],
         "kind": "class", "use": "new", "reason": ""},
        {"op": "add_relation", "from": "n1", "rel": "lowers", "to": "c2", "reason": ""},
    ]  # fmt: skip
    out, report = _apply(draft, ops, dismissed=dismissed)
    assert [d["reason"] for d in report["dropped"]] == ["dismissed", "dismissed"]
    assert len(out["concepts"]) == len(draft["concepts"])


def test_rename_changes_the_name_but_not_a_concept_tied_to_a_known_term() -> None:
    draft = _draft()
    op = {"op": "rename", "key": "c6", "label_ja": "析出ナノ粒子", "label_en": "nanoprecipitate",
          "aliases": ["NP"], "reason": "言い直し"}  # fmt: skip
    out, report = _apply(draft, [op])
    row = next(c for c in out["concepts"] if c["key"] == "c6")
    assert report["applied"] == 1
    assert (row["label_ja"], row["label_en"], row["aliases"]) == (
        "析出ナノ粒子", "nanoprecipitate", ["NP"],
    )  # fmt: skip
    # c1 is tied to a quantity kind in the draft below → it keeps its name
    tied = copy.deepcopy(draft)
    tied["concepts"][0].update(use="standard:quantitykind:ThermalConductivity",
                               match={"kind": "quantity", "ref": "quantitykind:ThermalConductivity",
                                      "label": "x", "matched_by": "label"})  # fmt: skip
    _o, rep = _apply(tied, [{**op, "key": "c1"}])
    assert [d["reason"] for d in rep["dropped"]] == ["not_renamable"]
    # a name another concept of the same kind already has
    _o2, rep2 = _apply(draft, [{**op, "label_ja": "格子熱伝導率", "key": "c3"}])
    assert [d["reason"] for d in rep2["dropped"]] == ["duplicate"]


def test_bind_column_is_for_datasets_only() -> None:
    op = {"op": "bind_column", "column": "kappa", "to": "c1", "rel": "eq", "reason": ""}
    out, report = _apply(_draft(), [op])
    assert [d["reason"] for d in report["dropped"]] == ["dataset_only"]
    assert out["relations"] == _draft()["relations"]


def test_unlinked_marks_concepts_that_do_not_reach_a_question_target() -> None:
    draft = _draft(wrong=False)
    add = {"op": "add_concept", "key": "n1", "label_ja": "格子欠陥", "label_en": "", "aliases": [],
           "kind": "class", "use": "new", "reason": ""}  # fmt: skip
    out, report = _apply(draft, [add])
    new_key = out["concepts"][-1]["key"]
    assert report["unlinked"] == [new_key]
    line = {"op": "add_relation", "from": new_key, "rel": "lowers", "to": "c2", "reason": ""}
    _o, rep = _apply(out, [line])
    assert rep["unlinked"] == []
    # with no question target yet, nothing is called unlinked
    bare = {**draft, "answers": []}
    assert _apply(bare, [add])[1]["unlinked"] == []


def test_a_humans_edit_is_marked_by_human() -> None:
    op = {"op": "add_relation", "from": "c3", "rel": "lowers", "to": "c2", "reason": "手で足した"}
    out, _ = _apply(_draft(), [op], by="human")
    assert out["relations"][-1]["by"] == "human"


def test_the_apply_is_in_order_and_one_bad_op_does_not_stop_the_rest() -> None:
    ops = [
        {"op": "add_relation", "from": "c1", "rel": "part", "to": "c2", "reason": ""},  # cycle
        _retarget("c4", "c1", "c2"),
    ]
    out, report = _apply(_draft(), ops)
    assert (report["applied"], [d["index"] for d in report["dropped"]]) == (1, [0])
    assert _rel(out, "c4", "lowers") == ["c2"]


def test_proposal_id_is_stable_and_twelve_characters() -> None:
    ops = [_retarget("c4", "c1", "c2")]
    pid = gd.proposal_id(ops)
    assert len(pid) == 12 and pid == gd.proposal_id(copy.deepcopy(ops))
    assert pid != gd.proposal_id([_retarget("c5", "c1", "c2")])


def test_sentences_use_the_screens_wording_in_both_languages() -> None:
    graph = {"concepts": [
        {"key": "c1", "label_ja": "熱伝導率", "label_en": "thermal conductivity"},
        {"key": "c2", "label_ja": "格子熱伝導率", "label_en": "lattice thermal conductivity"},
        {"key": "c4", "label_ja": "粒界散乱", "label_en": "grain boundary scattering"},
    ]}  # fmt: skip
    ops = [
        _retarget("c4", "c1", "c2"),
        {"op": "add_concept", "key": "n1", "label_ja": "点欠陥散乱", "label_en": "point defect",
         "aliases": [], "kind": "class", "use": "new", "reason": ""},
        {"op": "add_relation", "from": "n1", "rel": "lowers", "to": "c2", "reason": ""},
        {"op": "add_relation", "from": "c2", "rel": "part", "to": "c1", "reason": ""},
        {"op": "remove", "target": "relation", "from": "c4", "rel": "lowers", "to": "c1",
         "reason": ""},
        {"op": "remove", "target": "concept", "key": "c4", "reason": ""},
        {"op": "rename", "key": "c4", "label_ja": "粒界での散乱", "label_en": "", "aliases": [],
         "reason": ""},
        {"op": "add_question", "question": "何が下げる？", "mode": "knowledge",
         "op_kind": "factors", "target": "c1", "direction": "lowers", "kind": None,
         "property": None, "wanted_data": "",
         "reason": ""},
        {"op": "add_relation", "from": "c1", "rel": "quantity",
         "to": "quantitykind:ThermalConductivity", "reason": ""},
    ]  # fmt: skip
    ja = gd.ops_to_sentences(ops, graph, "ja")
    assert ja[0] == "粒界散乱 は 格子熱伝導率 を下げる に付け替え"
    assert ja[1] == "「点欠陥散乱」を種類として足す"
    assert ja[2] == "点欠陥散乱 は 格子熱伝導率 を下げる"
    assert ja[3] == "格子熱伝導率 は 熱伝導率 の一部"
    assert ja[4] == "粒界散乱 は 熱伝導率 を下げる を外す"
    assert ja[5] == "「粒界散乱」を外す"
    assert ja[6] == "「粒界散乱」の名前を「粒界での散乱」に"
    assert ja[7] == "問いを足す: 何が下げる？"
    assert ja[8].startswith("熱伝導率 の量は ") and "標準" in ja[8]
    en = gd.ops_to_sentences(ops[:4], graph, "en")
    assert en[0] == "Move to: grain boundary scattering lowers lattice thermal conductivity"
    assert en[3] == "lattice thermal conductivity is a part of thermal conductivity"
    cols = gd.ops_to_sentences(
        [{"op": "bind_column", "column": "kappa", "to": "c1", "rel": "eq", "reason": ""}], graph
    )
    assert cols == ["列「kappa」 と 熱伝導率 は同じもの"]


def test_dismissal_keys_name_concepts_and_both_ends_of_a_line() -> None:
    draft = _draft(wrong=False)
    c = {r["key"]: r for r in draft["concepts"]}
    ops = [
        {"op": "add_concept", "key": "n1", "label_ja": "格子欠陥", "label_en": "", "aliases": [],
         "kind": "class", "use": "new", "reason": ""},
        {"op": "add_relation", "from": "n1", "rel": "lowers", "to": "c2", "reason": ""},
        _retarget("c4", "c2", "c3"),
        {"op": "remove", "target": "concept", "key": "c5", "reason": ""},
    ]
    keys = gd.dismissal_keys(draft, ops, existing=[])
    assert keys == [
        {"subject": sv.concept_subject("格子欠陥"), "term": sv.CONCEPT_DISMISS_TERM},
        {"subject": sv.concept_subject("格子欠陥"), "term": themes.concept_end_key(c["c2"]),
         "relation": "lowers"},
        {"subject": themes.concept_end_key(c["c4"]), "term": themes.concept_end_key(c["c3"]),
         "relation": "lowers"},
    ]  # fmt: skip
    # what a ✕ saves is exactly what the apply step then refuses
    _out, rep = _apply(draft, ops[:2], dismissed=[{**k, "at": AT} for k in keys])
    assert [d["reason"] for d in rep["dropped"]] == ["dismissed", "dismissed"]


def test_dismissal_keys_resolve_use_like_apply_for_a_concept_on_an_existing_term() -> None:
    draft = _draft(wrong=False)
    terms = [sv.SharedTerm(sv.SV + "lt", "lt", "property", "フォノンの熱", None, None, AT)]
    ops = [
        {"op": "add_concept", "key": "n1", "label_ja": "格子の熱", "label_en": "", "aliases": [],
         "kind": "property", "use": "existing:lt", "reason": ""},
        {"op": "add_relation", "from": "n1", "rel": "part", "to": "c1", "reason": ""},
    ]  # fmt: skip
    keys = gd.dismissal_keys(draft, ops, existing=terms)
    assert keys[-1]["subject"] == sv.SV + "lt"  # the term's IRI, not concept:<name>
    _out, rep = gd.apply_to_draft(
        draft, ops, existing=terms, dismissed=[{**k, "at": AT} for k in keys[1:]]
    )
    assert [d["reason"] for d in rep["dropped"]] == ["dismissed"]  # the ✕ is what apply refuses


def test_an_eq_line_is_the_same_line_in_either_direction() -> None:
    eq = {"op": "add_relation", "from": "c2", "rel": "eq", "to": "c3", "reason": ""}
    out, rep = _apply(_draft(), [eq])
    assert rep["applied"] == 1
    back = {**eq, "from": "c3", "to": "c2"}
    _out, rep2 = _apply(out, [back])
    assert [d["reason"] for d in rep2["dropped"]] == ["duplicate"]
    # part is directional: the reverse is a different (here cyclic) line, not a duplicate
    rev = {"op": "add_relation", "from": "c1", "rel": "part", "to": "c2", "reason": ""}
    _o, rep3 = _apply(_draft(), [rev])
    assert [d["reason"] for d in rep3["dropped"]] == ["cycle"]


def test_end_iri_reads_a_term_slug_or_a_standard_curie() -> None:
    t = sv.SharedTerm(sv.SV + "capacity", "capacity", "property", "容量", None, None, AT)
    assert gd.end_iri("capacity", {"capacity": t}) == sv.SV + "capacity"
    assert gd.end_iri("quantitykind:Temperature", {}).endswith("/quantitykind/Temperature")
    assert gd.end_iri("nope", {}) is None and gd.end_iri("x:y", {}) is None


def test_a_draft_keeps_its_conversation_across_saves_of_the_rows(tmp_path) -> None:
    draft = themes.create_draft(tmp_path, "熱電", [Q], at=AT)
    chat = {
        "messages": [
            {"role": "user", "content": "散乱因子は格子熱伝導率に紐づくはず", "at": AT},
            {"role": "assistant", "content": "AI の散文は残さない", "at": AT},
        ],
        "proposals": [
            {"proposal_id": "abc123def456", "ops": [_retarget("c4", "c1", "c2")], "at": AT}
        ],
    }  # fmt: skip
    saved = themes.save_draft(tmp_path, {**draft, "chat": chat})
    # the model's prose is not kept
    assert [m["role"] for m in saved["chat"]["messages"]] == ["user"]
    assert saved["chat"]["proposals"][0] == {
        "proposal_id": "abc123def456", "ops": [_retarget("c4", "c1", "c2")], "at": AT, "by": "ai",
    }  # fmt: skip
    # an ordinary save of the rows (no "chat" key) carries it over
    again = themes.save_draft(tmp_path, {k: v for k, v in saved.items() if k != "chat"})
    assert again["chat"] == saved["chat"]
    assert themes.load_draft(tmp_path, draft["id"])["chat"] == saved["chat"]
    # passing "chat" replaces it; a draft with no conversation has no "chat" key
    assert "chat" not in themes.save_draft(tmp_path, {**saved, "chat": {}})


def _terms() -> list[sv.SharedTerm]:
    def t(slug, ja, kind):
        return sv.SharedTerm(sv.SV + slug, slug, kind, ja, None, None, AT)

    return [
        t("thermal_conductivity", "熱伝導率", "property"),
        t("lattice_tc", "格子熱伝導率", "property"),
        t("grain_boundary", "粒界散乱", "class"),
    ]


def test_home_plans_only_lines_between_published_terms() -> None:
    ops = [
        {"op": "add_relation", "from": "grain_boundary", "rel": "lowers", "to": "lattice_tc",
         "reason": "r1"},
        {"op": "add_relation", "from": "lattice_tc", "rel": "part", "to": "thermal_conductivity",
         "reason": "r2"},
        {"op": "add_relation", "from": "thermal_conductivity", "rel": "quantity",
         "to": "quantitykind:ThermalConductivity", "reason": "r3"},
        {"op": "remove", "target": "relation", "from": "grain_boundary", "rel": "lowers",
         "to": "thermal_conductivity", "reason": "r4"},
    ]  # fmt: skip
    plans, dropped = gd.plan_term_relations(ops, terms=_terms())
    assert dropped == []
    assert [(p["index"], p["action"], p["relation"]) for p in plans] == [
        (0, "add", "lowers"), (1, "add", "isPartOf"), (2, "add", "hasQuantityKind"),
        (3, "remove", "lowers"),
    ]  # fmt: skip
    assert (plans[0]["source"], plans[0]["target"]) == (
        sv.SV + "grain_boundary", sv.SV + "lattice_tc",
    )  # fmt: skip
    assert plans[2]["target"].endswith("/quantitykind/ThermalConductivity")
    assert plans[0]["reason"] == "r1"


def test_home_drops_what_the_tables_or_the_dismissals_refuse_and_what_is_not_a_line() -> None:
    ops = [
        {"op": "add_relation", "from": "lattice_tc", "rel": "lowers", "to": "grain_boundary",
         "reason": ""},  # lowers needs a property at the far end
        {"op": "add_relation", "from": "grain_boundary", "rel": "part", "to": "lattice_tc",
         "reason": ""},  # a class is not part of a property
        {"op": "add_relation", "from": "nope", "rel": "eq", "to": "lattice_tc", "reason": ""},
        {"op": "add_relation", "from": "lattice_tc", "rel": "eq", "to": "thermal_conductivity",
         "reason": ""},  # dismissed below
        {"op": "rename", "key": "lattice_tc", "label_ja": "x", "label_en": "", "aliases": [],
         "reason": ""},
    ]  # fmt: skip
    dismissed = [{"subject": sv.SV + "lattice_tc", "term": sv.SV + "thermal_conductivity",
                  "relation": "equivalentProperty", "at": AT}]  # fmt: skip
    plans, dropped = gd.plan_term_relations(ops, terms=_terms(), dismissed=dismissed)
    assert plans == []
    assert [(d["index"], d["reason"]) for d in dropped] == [
        (0, "target_not_property"), (1, "mixed"), (2, "unknown_key"), (3, "dismissed"),
        (4, "unsupported"),
    ]  # fmt: skip


def test_home_plans_a_published_kind_as_the_end_of_a_line_by_its_iri() -> None:
    """7c A1: 公開済みの種類（星）は ``kind:<…>`` の鍵で線の元（先）に使える。線の端・却下の鍵は
    種類の IRI そのもの（``concept:…`` の仮の名前ではない）。"""
    star = "https://ex/onto#Ingredient"
    key = gd.kind_key(star)
    assert key == gd.kind_key(star) and key.startswith("kind:") and "/" not in key
    kinds = {key: {"iri": star, "label": "食材の名前"}}
    ops = [
        {"op": "add_relation", "from": key, "rel": "eq", "to": "grain_boundary", "reason": "r1"},
        {"op": "add_relation", "from": key, "rel": "sub", "to": "grain_boundary", "reason": ""},
        {"op": "add_relation", "from": key, "rel": "eq", "to": "lattice_tc", "reason": ""},
        {"op": "add_relation", "from": key, "rel": "lowers", "to": "lattice_tc", "reason": ""},
        {"op": "remove", "target": "relation", "from": key, "rel": "eq", "to": "grain_boundary",
         "reason": ""},
    ]  # fmt: skip
    dismissed = [{"subject": star, "term": sv.SV + "grain_boundary",
                  "relation": "subClassOf", "at": AT}]  # fmt: skip
    plans, dropped = gd.plan_term_relations(ops, terms=_terms(), dismissed=dismissed, kinds=kinds)
    assert [(p["index"], p["action"], p["relation"]) for p in plans] == [
        (0, "add", "equivalentClass"), (3, "add", "lowers"), (4, "remove", "equivalentClass"),
    ]  # fmt: skip  (a kind may lower an item: the table says "from: a class or a property")
    assert (plans[0]["source"], plans[0]["target"]) == (star, sv.SV + "grain_boundary")
    assert plans[0]["reason"] == "r1"
    assert [(d["index"], d["reason"]) for d in dropped] == [
        (1, "dismissed"),  # 種類の IRI で書いた却下が効く
        (2, "mixed"),  # 同じものと言えるのは同じ種別どうしだけ（種類 ≡ 項目は不可）
    ]
    # kinds を渡さなければ、種類の鍵は図に無い鍵（従来どおり）
    _plans, none = gd.plan_term_relations(ops[:1], terms=_terms())
    assert [d["reason"] for d in none] == ["unknown_key"]
