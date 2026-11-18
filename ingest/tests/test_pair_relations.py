# ruff: noqa: RUF001, E501
"""``asterism.pair_relations``（語の組の関係の検査と台帳）のテスト。例はすべて作り物。"""

from __future__ import annotations

import copy as _copy
import json
import os
from pathlib import Path

import pytest

from asterism import pair_relations as pr
from asterism.knowledge_bundle import KnowledgeCopy, read_copy
from asterism.themes import RELATION_KINDS


def _t(tid, name, kind="class", role=None):
    return {"id": tid, "iri": f"https://k.test/term/{tid}", "name": name, "kind": kind, "role": role,
            "aliases": [], "origin_iri": None, "claim_count": 0}


def _c(cid, title, state="candidate", **kw):
    row = {"id": cid, "iri": f"https://k.test/claim/{cid}", "source_url": None, "title": title,
           "review_state": state, "epistemic_status": None, "modal_qualifier": None, "level": None,
           "source_iri": [], "generated_at": None, "exported_at": None}
    row.update(kw)
    return row


def _tc(*pairs):
    return [{"term_id": t, "claim_id": c, "by": "ai"} for c, ts in pairs for t in ts]


def _copy_a() -> KnowledgeCopy:
    terms = [
        _t("alloy1", "Mg2Si", role="material"),
        _t("alloy2", "Mg2Sn", role="material"),
        _t("arc", "アーク溶解", role="method"),
        _t("xrd", "X 線回折", role="method"),
        _t("kappa", "熱伝導率", kind="property"),
        _t("seebeck", "ゼーベック係数", kind="property"),
    ]
    claims = [
        _c("c01", "Mg2Si はアーク溶解で作る", "verified"),
        _c("c02", "Mg2Si の熱伝導率を X 線回折と合わせて調べた"),
        _c("c03", "Mg2Si の熱伝導率は低いかもしれない", epistemic_status="speculation"),
        _c("c04", "Mg2Sn はアーク溶解で作る"),
        _c("c05", "  "),
    ]
    tcs = _tc(
        ("c01", ["alloy1", "arc"]),
        ("c02", ["alloy1", "kappa", "xrd"]),
        ("c03", ["alloy1", "kappa"]),
        ("c04", ["alloy2", "arc"]),
        ("c05", ["alloy1", "seebeck"]),
    )
    return KnowledgeCopy(terms=terms, claims=claims, term_claims=tcs)


TERMS = {t["id"]: t for t in _copy_a().terms}


def _task(copy=None, key="alloy1|kappa"):
    return next(t for t in pr.build_tasks(copy or _copy_a()) if t["key"] == key)


# ---- 定数 ----------------------------------------------------------------------------------------


def test_PAIR_RELATIONS_は_quantity_を除いた下書きの関係():
    assert tuple(r for r in RELATION_KINDS if r != "quantity") == pr.PAIR_RELATIONS
    assert len(pr.PAIR_RELATIONS) == 11


# ---- build_tasks ---------------------------------------------------------------------------------


def test_build_tasks_形と並びと題の空の知見():
    tasks = pr.build_tasks(_copy_a())
    keys = [t["key"] for t in tasks]
    # 知見の多い組が先・同数は key の順。c05（題が空）だけの組は作らない
    assert keys[0] == "alloy1|kappa"
    assert "alloy1|seebeck" not in keys
    t = _task()
    assert t["claim_ids"] == ["c02", "c03"] and t["claims_total"] == 2
    assert t["input"]["a"] == {"name": "Mg2Si", "kind": "class", "role": "material"}
    assert t["input"]["claims"] == ["Mg2Si の熱伝導率を X 線回折と合わせて調べた", "Mg2Si の熱伝導率は低いかもしれない"]
    assert len(t["fingerprint"]) == 16
    assert pr.build_tasks(_copy_a()) == tasks


def test_build_tasks_確認済みが先と_max_claims():
    c = _copy_a()
    c.claims += [_c(f"d{i:02d}", f"文 {i}") for i in range(10)] + [_c("z99", "確認済みの文", "verified")]
    c.term_claims += _tc(*[(x["id"], ["alloy1", "arc"]) for x in c.claims[-11:]])
    t = _task(c, "alloy1|arc")
    assert len(t["claim_ids"]) == 8 and t["claims_total"] == 12
    assert t["claim_ids"][:2] == ["c01", "z99"]  # verified が id より先
    assert t["claim_ids"][2:] == sorted(t["claim_ids"][2:])
    assert len(pr.build_tasks(c, max_claims=3)[0]["claim_ids"]) == 3


def test_build_tasks_語が写しに無い組は作らない():
    c = _copy_a()
    c.term_claims += _tc(("c01", ["ghost"]))
    assert all("ghost" not in t["key"] for t in pr.build_tasks(c))


def test_fingerprint_は入力で決まる():
    base = _task()["fingerprint"]
    c = _copy_a()
    assert _task(c)["fingerprint"] == base
    c.claims[1]["title"] += "（改）"
    assert _task(c)["fingerprint"] != base
    c2 = _copy_a()
    c2.claims[2]["review_state"] = "verified"  # 選ぶ順が変われば変わる
    assert _task(c2)["fingerprint"] != base
    c3 = _copy_a()
    c3.claims[1]["source_url"] = "https://x.test"  # 入力に無い欄では変わらない
    assert _task(c3)["fingerprint"] == base


def test_写しを_read_copy_で読んでも組が作れる(tmp_path: Path):
    c = _copy_a()
    for name in ("claims", "terms", "term_claims", "term_relations", "topics", "sources"):
        (tmp_path / f"{name}.json").write_text(json.dumps(getattr(c, name)), encoding="utf-8")
    assert pr.build_tasks(read_copy(tmp_path)) == pr.build_tasks(c)


# ---- relation_problem ----------------------------------------------------------------------------


def test_relation_problem_全分岐():
    mat, mat2 = _t("m1", "Mg2Si", role="material"), _t("m2", "Mg2Sn", role="material")
    meth, prop = _t("x", "X 線回折", role="method"), _t("k", "熱伝導率", "property")
    assert pr.relation_problem("sub", mat, mat) == "same"
    assert pr.relation_problem("sub", mat, mat2) is None
    assert pr.relation_problem("sub", mat, prop) == "mixed"
    assert pr.relation_problem("eq", prop, mat) == "mixed"
    assert pr.relation_problem("part", mat, prop) == "mixed"
    assert pr.relation_problem("lowers", mat, prop) is None
    assert pr.relation_problem("raises", mat, mat2) == "target_not_property"
    assert pr.relation_problem("affects", prop, mat) == "target_not_property"
    assert pr.relation_problem("makes", meth, mat) is None
    assert pr.relation_problem("makes", mat, meth) == "role_mismatch"
    assert pr.relation_problem("makes", prop, mat) == "mixed"
    assert pr.relation_problem("has_property", mat, prop) is None
    assert pr.relation_problem("measures", meth, prop) is None
    assert pr.relation_problem("nonsense", mat, mat2) == "relation"


# ---- check_answer --------------------------------------------------------------------------------


def _ans(rel, frm="a", ev=("c1",), note="理由"):
    return {"rel": rel, "from": frm, "evidence": list(ev), "note": note}


def test_check_answer_通る():
    t = _task(key="alloy1|arc")
    e = pr.check_answer(t, _ans("makes", "b"), TERMS)
    assert (e["state"], e["rel"], e["from"], e["to"]) == ("relation", "makes", "arc", "alloy1")
    assert e["evidence"] == ["c01"] and e["claims"] == t["claim_ids"] and e["reason"] is None
    assert e["fingerprint"] == t["fingerprint"] and e["key"] == "alloy1|arc"


def test_check_answer_none_と集合外():
    t = _task()
    n = pr.check_answer(t, _ans("none", None, ()), TERMS)
    assert n["state"] == "none" and n["rel"] is None and n["evidence"] == []
    d = pr.check_answer(t, _ans("friend_of"), TERMS)
    assert (d["state"], d["reason"]) == ("dropped", "relation")
    d = pr.check_answer(t, {}, TERMS)
    assert d["reason"] == "relation"


def test_check_answer_根拠の外と空と向き():
    t = _task()
    for ev in (("c3",), ("c0",), ("x",), ()):
        d = pr.check_answer(t, _ans("affects", "a", ev), TERMS)
        assert (d["state"], d["reason"]) == ("dropped", "evidence")
    d = pr.check_answer(t, _ans("affects", None), TERMS)
    assert d["reason"] == "direction"
    e = pr.check_answer(t, _ans("affects", "a", ("c2", "c2", "c1")), TERMS)
    assert e["evidence"] == ["c03", "c02"]


def test_check_answer_検査に当たれば_dropped():
    t = _task()
    # a=Mg2Si（class）→ b=熱伝導率（property）の sub は種別違い
    assert pr.check_answer(t, _ans("sub"), TERMS)["reason"] == "mixed"
    # property → class の lowers は相手が項目でない
    assert pr.check_answer(t, _ans("lowers", "b"), TERMS)["reason"] == "target_not_property"
    assert pr.check_answer(t, _ans("lowers"), TERMS)["state"] == "relation"
    assert pr.check_answer(t, _ans("lowers"), {})["reason"] == "term"


def test_check_answer_rejected_と向きなしの並べ替え():
    c = _copy_a()
    c.term_claims += _tc(("c01", ["alloy2"]))
    t = _task(c, "alloy1|alloy2")
    e = pr.check_answer(t, _ans("eq", "b"), TERMS)
    assert (e["from"], e["to"]) == ("alloy1", "alloy2")  # 向きなしは id の順
    rej = [{"rel": "eq", "from": "alloy2", "to": "alloy1", "at": "t"}]
    d = pr.check_answer(t, _ans("eq", "a"), TERMS, rejected=rej)
    assert (d["state"], d["reason"]) == ("dropped", "rejected")
    # 向きのある関係は向きを問う
    rej2 = [{"rel": "sub", "from": "alloy2", "to": "alloy1", "at": "t"}]
    assert pr.check_answer(t, _ans("sub", "a"), TERMS, rejected=rej2)["state"] == "relation"
    assert pr.check_answer(t, _ans("sub", "b"), TERMS, rejected=rej2)["reason"] == "rejected"


def test_failed_entry():
    t = _task()
    f = pr.failed_entry(t, "timeout")
    assert (f["state"], f["reason"], f["key"]) == ("failed", "timeout", t["key"])


# ---- drop_cycles ---------------------------------------------------------------------------------


def _rel(key, rel, frm, to):
    return {"key": key, "state": "relation", "rel": rel, "from": frm, "to": to, "evidence": []}


def test_drop_cycles_後のものを落とす_sub_と_part_は別():
    es = [_rel("a|1", "sub", "a", "b"), _rel("a|2", "sub", "b", "c"), _rel("a|3", "sub", "c", "a"),
          _rel("a|d", "part", "d", "a"), _rel("a|e", "part", "a", "d")]
    orig = _copy.deepcopy(es)
    out = pr.drop_cycles(es, [])
    assert es == orig
    st = {e["key"]: (e["state"], e.get("reason")) for e in out}
    assert st["a|3"] == ("dropped", "cycle") and st["a|1"][0] == "relation"
    assert st["a|d"][0] == "relation" and st["a|e"] == ("dropped", "cycle")  # part の輪は part 同士で
    assert [e["key"] for e in out] == [e["key"] for e in es]


def test_drop_cycles_origin_の辺は数えない_AI_の関係を消さない():
    origin = [{"from": "b", "rel": "sub", "to": "a", "origin_iri": None, "by": "origin"}]
    out = pr.drop_cycles([_rel("a|b", "sub", "a", "b")], origin)
    assert out[0]["state"] == "relation"
    assert len(origin) == 1


# ---- annotate ------------------------------------------------------------------------------------


def test_annotate_文脈は半数以上_並びは件数順():
    c = _copy_a()
    e = {"key": "alloy1|kappa", "state": "relation", "rel": "lowers", "from": "alloy1",
         "to": "kappa", "evidence": ["c02", "c03"]}
    a = pr.annotate(e, c)
    # xrd は c02 だけ（1/2 → ceil(2/2)=1 で入る）
    assert a["context"] == ["xrd"] and "context" not in e
    c.term_claims += _tc(("c02", ["arc"]), ("c03", ["arc"]))
    assert pr.annotate(e, c)["context"] == ["arc", "xrd"]  # 2 件 → 1 件
    e3 = dict(e, evidence=["c01", "c02", "c03"], from_="x")
    e3.update({"from": "alloy1", "to": "kappa"})
    assert pr.annotate(e3, c)["context"] == ["arc"]  # n=3 → 2 件以上
    c.term_claims += _tc(*[("c01", [f"t{i}"]) for i in range(5)] + [("c02", [f"t{i}"]) for i in range(5)])
    big = pr.annotate(e3, c)["context"]
    assert len(big) == pr.CONTEXT_MAX and big[0] == "arc"


def test_annotate_tentative_は全部が見込みのときだけ():
    c = _copy_a()
    c.claims[2]["modal_qualifier"] = "rarely"
    e = {"key": "k", "state": "relation", "rel": "lowers", "from": "alloy1", "to": "kappa",
         "evidence": ["c03"]}
    assert pr.annotate(e, c)["tentative"] is True
    both = pr.annotate(dict(e, evidence=["c02", "c03"]), c)
    assert both["tentative"] is False
    assert both["review"] == {"verified": 0, "candidate": 2}
    assert pr.annotate(dict(e, evidence=["c01"]), c)["review"] == {"verified": 1, "candidate": 0}
    c.claims[2]["epistemic_status"] = None
    c.claims[2]["modal_qualifier"] = "possibly"
    assert pr.annotate(e, c)["tentative"] is True
    c.claims[2]["modal_qualifier"] = "likely"
    assert pr.annotate(e, c)["tentative"] is False


def test_annotate_relation_以外はそのまま():
    e = {"key": "k", "state": "none"}
    assert pr.annotate(e, _copy_a()) == e


# ---- origin_relations ----------------------------------------------------------------------------


def test_origin_relations():
    c = _copy_a()
    c.term_relations = [
        {"id": "r1", "from_term": "alloy1", "rel": "has_property", "to_term": "kappa", "origin_iri": "https://k.test/o1"},
        {"id": "r2", "from_term": "kappa", "rel": "has_property", "to_term": "alloy1", "origin_iri": None},
        {"id": "r3", "from_term": "alloy2", "rel": "eq", "to_term": "alloy1", "origin_iri": None},
        {"id": "r4", "from_term": "alloy1", "rel": "sub", "to_term": "alloy1", "origin_iri": None},
        {"id": "r5", "from_term": "alloy1", "rel": "whatever", "to_term": "kappa", "origin_iri": None},
        {"id": "r6", "from_term": "alloy1", "rel": "sub", "to_term": "ghost", "origin_iri": None},
    ]
    rows, dropped = pr.origin_relations(c)
    assert rows == [
        {"from": "alloy1", "rel": "eq", "to": "alloy2", "origin_iri": None, "by": "origin"},
        {"from": "alloy1", "rel": "has_property", "to": "kappa", "origin_iri": "https://k.test/o1", "by": "origin"},
    ]
    assert dropped == {"mixed": 1, "relation": 1, "same": 1, "term": 1}


# ---- 台帳 ----------------------------------------------------------------------------------------


def test_ledger_path_は_knowledge_の中に収まる(tmp_path: Path):
    base = (tmp_path / "vocab-shared" / "knowledge").resolve()
    assert pr.ledger_path(tmp_path, "my-source_1") == base / "my-source_1.json"
    for src in ("../x", "", "a/b", "x" * 65, "ノート", "..", "a\x00b", "/etc/passwd"):
        p = pr.ledger_path(tmp_path, src)
        assert p.resolve().parent == base and p.name.startswith("s-") and p.suffix == ".json"
    assert pr.ledger_path(tmp_path, "../x") != pr.ledger_path(tmp_path, "../y")
    assert pr.ledger_path(tmp_path, "x" * 64).name == "x" * 64 + ".json"


def test_台帳の往復と壊れたもの(tmp_path: Path):
    led = pr.load_ledger(tmp_path, "src")
    assert led == {"version": 1, "source": "src", "dataset_id": None, "updated_at": None, "pairs": {}, "origin": []}
    led["pairs"]["a|b"] = {"key": "a|b", "note": "日本語"}
    pr.save_ledger(tmp_path, led, at="2026-01-01T00:00:00Z")
    p = pr.ledger_path(tmp_path, "src")
    assert "日本語" in p.read_text(encoding="utf-8")
    assert not list(p.parent.glob("*.tmp"))
    back = pr.load_ledger(tmp_path, "src")
    assert back["pairs"] == led["pairs"] and back["updated_at"] == "2026-01-01T00:00:00Z"
    p.write_text("{ broken", encoding="utf-8")
    assert pr.load_ledger(tmp_path, "src")["pairs"] == {}
    p.write_text(json.dumps({"version": 99, "pairs": {"x": {}}}), encoding="utf-8")
    assert pr.load_ledger(tmp_path, "src")["pairs"] == {}


# ---- plan_run / merge_entry ----------------------------------------------------------------------


def _ledger_for(tasks, **extra):
    led = pr.load_ledger(Path("/nonexistent-root-xyz"), "s")
    for t in tasks:
        e = pr.check_answer(t, _ans("none", None, ()), TERMS)
        pr.merge_entry(led, e, model="m", at="t0")
    for k, v in extra.items():
        led["pairs"][k].update(v)
    return led


def test_plan_run_同じ指紋は聞かない_変われば聞く():
    tasks = pr.build_tasks(_copy_a())
    led = _ledger_for(tasks)
    todo, base = pr.plan_run(led, tasks)
    assert todo == [] and set(base["pairs"]) == {t["key"] for t in tasks}
    tasks2 = [dict(t) for t in tasks]
    tasks2[0]["fingerprint"] = "0" * 16
    todo, base = pr.plan_run(led, tasks2)
    assert [t["key"] for t in todo] == [tasks[0]["key"]]
    assert base["pairs"][tasks[0]["key"]] == led["pairs"][tasks[0]["key"]]  # 今の件は残す
    todo, _ = pr.plan_run(pr.load_ledger(Path("/nonexistent-root-xyz"), "s"), tasks)
    assert len(todo) == len(tasks)


def test_plan_run_failed_は聞き直す_confirmed_は聞かない():
    tasks = pr.build_tasks(_copy_a())
    k0, k1 = tasks[0]["key"], tasks[1]["key"]
    led = _ledger_for(tasks, **{k0: {"state": "failed"}, k1: {"confirmed": {"at": "t", "by": "u"}}})
    tasks2 = [dict(t) for t in tasks]
    tasks2[1]["fingerprint"] = "f" * 16
    todo, _ = pr.plan_run(led, tasks2)
    assert [t["key"] for t in todo] == [k0]


def test_plan_run_消えた組は外す_confirmed_は_orphan():
    tasks = pr.build_tasks(_copy_a())
    led = _ledger_for(tasks, **{tasks[1]["key"]: {"confirmed": {"at": "t", "by": "u"}}})
    _, base = pr.plan_run(led, tasks[2:])
    assert set(base["pairs"]) == {t["key"] for t in tasks[2:]} | {tasks[1]["key"]}
    assert base["pairs"][tasks[1]["key"]]["orphan"] is True
    assert "orphan" not in led["pairs"][tasks[1]["key"]]  # 入力は変えない


def test_merge_entry_rejected_を引き継ぐ_confirmed_は置き換えない():
    t = _task()
    led = _ledger_for([t])
    rej = [{"rel": "sub", "from": "kappa", "to": "alloy1", "at": "t"}]
    led["pairs"][t["key"]]["rejected"] = rej
    e = pr.check_answer(t, _ans("lowers"), TERMS)
    pr.merge_entry(led, e, model="m2", at="t1")
    got = led["pairs"][t["key"]]
    assert (got["by"], got["model"], got["proposed_at"], got["state"]) == ("ai", "m2", "t1", "relation")
    assert got["rejected"] == rej
    led["pairs"][t["key"]]["confirmed"] = {"at": "t", "by": "u"}
    pr.merge_entry(led, pr.failed_entry(t, "x"), model="m3", at="t2")
    assert led["pairs"][t["key"]]["state"] == "relation"


# ---- finalize / summarize ------------------------------------------------------------------------


def test_finalize_冪等で_summarize_の数が合う():
    c = _copy_a()
    c.terms.append(_t("alloy3", "Mg2Ge", role="material"))
    c.term_claims += _tc(("c01", ["alloy3"]), ("c04", ["alloy3", "alloy1"]))
    c.term_relations = [{"id": "r", "from_term": "alloy2", "rel": "sub", "to_term": "alloy1", "origin_iri": None}]
    tasks = pr.build_tasks(c)
    led = pr.load_ledger(Path("/nonexistent-root-xyz"), "s")
    answers = {
        "alloy1|arc": _ans("makes", "b"),
        "alloy1|kappa": _ans("lowers"),
        "alloy1|alloy2": _ans("sub", "a"),  # origin の alloy2 sub alloy1 と輪になる
        "alloy1|alloy3": _ans("none", None, ()),
        "alloy2|arc": _ans("makes", "a"),  # 役割違い → dropped
    }
    for t in tasks:
        a = answers.get(t["key"])
        e = pr.check_answer(t, a, c.terms) if a else pr.failed_entry(t, "x")
        pr.merge_entry(led, e, model="m", at="t")
    led["pairs"]["alloy1|kappa"]["evidence"] = ["c03"]
    c.claims[2]["modal_qualifier"] = "possibly"
    once = pr.finalize(led, c, dataset_id="ds1")
    assert pr.finalize(once, c, dataset_id="ds1") == once
    assert pr.finalize(pr.finalize(once, c, dataset_id="ds1"), c, dataset_id="ds1") == once
    assert "tentative" not in led["pairs"]["alloy1|kappa"]  # 入力は変えない
    s = pr.summarize(once)
    n = len(tasks)
    assert s["pairs"] == n
    assert s["relations"] + s["none"] + sum(s["dropped"].values()) + s["failed"] == n
    # origin の alloy2 sub alloy1 と AI の alloy1 sub alloy2 は逆向き → AI が残る
    assert once["pairs"]["alloy1|alloy2"]["state"] == "relation"
    assert s["dropped"] == {"role_mismatch": 1}
    # origin の alloy2 sub alloy1 は AI の関係と逆向きで輪になる → disputes（候補）
    assert s["origin"] == 1 and once["origin"][0]["ai"] == "disputes"
    assert s["origin_check"] == {"agrees": 0, "disputes": 1, "unseen": 0}
    assert s["by_rel"] == {"lowers": 1, "makes": 1, "sub": 1}
    assert s["tentative"] == 1 and once["pairs"]["alloy1|kappa"]["tentative"] is True
    # 既定で見せるのは relation の 3 件（disputes の origin は数えない）→ alloy1・alloy2・arc・kappa
    assert s["shown"] == 3 and s["terms"] == 4 and once["dataset_id"] == "ds1"
    assert s["candidates"] == s["none"] + 1


# ---- origin_check ----------------------------------------------------------------------------------


def _row(frm, rel, to):
    return {"from": frm, "rel": rel, "to": to, "origin_iri": None, "by": "origin"}


def test_origin_check_三つの印():
    pairs = {
        "a|b": _rel("a|b", "lowers", "a", "b"),
        "a|c": {"key": "a|c", "state": "none", "rel": None},
        "a|d": _rel("a|d", "raises", "a", "d"),
        "a|e": {"key": "a|e", "state": "dropped", "reason": "mixed", "rel": "sub", "from": "a", "to": "e"},
        "a|f": {"key": "a|f", "state": "failed"},
        "a|g": _rel("a|g", "eq", "a", "g"),
    }
    chk = lambda *r: pr.origin_check(_row(*r), pairs)  # noqa: E731
    assert chk("a", "lowers", "b") == "agrees"
    assert chk("b", "lowers", "a") == "disputes"  # 向き違い
    assert chk("a", "lowers", "c") == "disputes"  # none
    assert chk("a", "sub", "d") == "disputes"  # 別の関係
    assert chk("a", "sub", "e") == "disputes"  # dropped
    assert chk("a", "sub", "f") == "unseen"  # failed
    assert chk("a", "sub", "z") == "unseen"  # 組が無い
    assert chk("g", "eq", "a") == "agrees"  # 向きの無い関係は向きを問わない


def test_finalize_origin_に_ai_の印が付き冪等_summarize_は_ai_無しでも落ちない():
    c = _copy_a()
    c.term_relations = [
        {"id": "r1", "from_term": "alloy1", "rel": "has_property", "to_term": "kappa", "origin_iri": "u1"},
        {"id": "r2", "from_term": "arc", "rel": "makes", "to_term": "alloy1", "origin_iri": "u2"},
        {"id": "r3", "from_term": "alloy1", "rel": "sub", "to_term": "alloy2", "origin_iri": "u3"},
    ]
    led = pr.load_ledger(Path("/nonexistent-root-xyz"), "s")
    tasks = {t["key"]: t for t in pr.build_tasks(c)}
    for key, ans in {"alloy1|kappa": _ans("has_property"), "alloy1|arc": _ans("none", None, ())}.items():
        pr.merge_entry(led, pr.check_answer(tasks[key], ans, c.terms), model="m", at="t")
    raw = _copy.deepcopy(led)
    once = pr.finalize(led, c, dataset_id="d")
    marks = {(o["from"], o["rel"], o["to"]): o["ai"] for o in once["origin"]}
    assert marks == {
        ("alloy1", "has_property", "kappa"): "agrees",
        ("arc", "makes", "alloy1"): "disputes",
        ("alloy1", "sub", "alloy2"): "unseen",
    }
    assert pr.finalize(once, c, dataset_id="d") == once
    s = pr.summarize(once)
    assert s["origin"] == 3 and s["origin_check"] == {"agrees": 1, "disputes": 1, "unseen": 1}
    assert s["relations"] == 1 and s["shown"] == 2  # AI の関係 1 ＋ unseen 1
    assert s["terms"] == 3  # alloy1・kappa・alloy2（alloy1 は重なる）
    assert s["candidates"] == s["none"] + 1 == 2
    # finalize 前の台帳（origin に ai が無い）でも落ちず、unseen として数える
    raw["origin"] = [_row("alloy1", "has_property", "kappa")]
    assert pr.summarize(raw)["origin_check"] == {"agrees": 0, "disputes": 0, "unseen": 1}


# ---- 輪・orphan・台帳・失敗・入力の守り（レビューの直し）-----------------------------------------


def _sub(key, frm, to, **kw):
    return {**_rel(key, "sub", frm, to), "evidence": ["c01"], **kw}


def test_drop_cycles_confirmed_は落とさない():
    es = [_sub("a|b", "a", "b"), _sub("b|a", "b", "a", confirmed={"at": "t", "by": "u"})]
    out = {e["key"]: e for e in pr.drop_cycles(es, [])}
    # key の順なら a|b が先に通るが、確認済みの b|a を先に辺として入れるので a|b が落ちる
    assert out["b|a"]["state"] == "relation" and out["a|b"]["reason"] == "cycle"


def _origin_copy(rels):
    c = _copy_a()
    c.terms += [_t("x", "X"), _t("y", "Y"), _t("z", "Z")]
    c.term_relations = [
        {"id": f"r{i}", "from_term": f, "rel": "sub", "to_term": t, "origin_iri": None}
        for i, (f, t) in enumerate(rels)
    ]
    return c


def _led(*entries):
    led = pr.empty_ledger("s")
    for e in entries:
        led["pairs"][e["key"]] = dict(e)
    return led


def test_finalize_origin_逆向きなら_AI_が残り_origin_が_disputes():
    c = _origin_copy([("y", "x")])
    led = _led({**_sub("x|y", "x", "y"), "evidence": ["c01"]})
    once = pr.finalize(led, c, dataset_id="d")
    assert once["pairs"]["x|y"]["state"] == "relation"
    assert [o["ai"] for o in once["origin"]] == ["disputes"]
    assert pr.finalize(once, c, dataset_id="d") == once


def test_finalize_origin_だけの輪は後の行が_disputes():
    c = _origin_copy([("x", "y"), ("y", "z"), ("z", "x")])
    once = pr.finalize(pr.empty_ledger("s"), c, dataset_id="d")
    marks = {(o["from"], o["to"]): o["ai"] for o in once["origin"]}
    assert marks == {("x", "y"): "unseen", ("y", "z"): "unseen", ("z", "x"): "disputes"}
    assert pr.finalize(once, c, dataset_id="d") == once


def test_finalize_confirmed_は輪で落ちない():
    c = _origin_copy([])
    led = _led(
        _sub("x|y", "x", "y"),
        _sub("y|z", "y", "z"),
        _sub("x|z", "z", "x", confirmed={"at": "t", "by": "u"}),
    )
    once = pr.finalize(led, c, dataset_id="d")
    assert once["pairs"]["x|z"]["state"] == "relation"
    assert once["pairs"]["y|z"]["reason"] == "cycle" and once["pairs"]["x|y"]["state"] == "relation"
    assert pr.finalize(once, c, dataset_id="d") == once


def test_plan_run_rejected_を持つ消えた組は_orphan_で残り_戻ると外れる():
    tasks = pr.build_tasks(_copy_a())
    key = tasks[0]["key"]
    led = _ledger_for(tasks, **{key: {"rejected": [{"rel": "sub", "from": "a", "to": "b", "at": "t"}]}})
    _todo, base = pr.plan_run(led, tasks[1:])
    assert base["pairs"][key]["orphan"] is True and base["pairs"][key]["rejected"]
    _todo, back = pr.plan_run(base, tasks)
    assert "orphan" not in back["pairs"][key] and back["pairs"][key]["rejected"]
    plain = _ledger_for(tasks)
    assert key not in pr.plan_run(plain, tasks[1:])[1]["pairs"]


def test_ledger_path_大文字小文字を潰さない(tmp_path):
    a, b = pr.ledger_path(tmp_path, "Graphium"), pr.ledger_path(tmp_path, "graphium")
    assert a != b and b.name == "graphium.json" and a.name.startswith("s-")
    assert pr.ledger_path(tmp_path, "g" * 64).name == "g" * 64 + ".json"
    assert pr.ledger_path(tmp_path, "g" * 65).name.startswith("s-")
    led = pr.empty_ledger("Graphium")
    led["pairs"]["k"] = {"key": "k"}
    pr.save_ledger(tmp_path, led, at="t")
    assert pr.load_ledger(tmp_path, "graphium")["pairs"] == {}
    assert "k" in pr.load_ledger(tmp_path, "Graphium")["pairs"]


def test_load_ledger_壊れたファイルは_bad_に退避して空を返す(tmp_path):
    p = pr.ledger_path(tmp_path, "src")
    p.parent.mkdir(parents=True)
    p.write_text('{"version":1,"pairs":{"k":')
    assert pr.load_ledger(tmp_path, "src")["pairs"] == {}
    assert not p.exists() and p.with_name("src.json.bad").read_text() == '{"version":1,"pairs":{"k":'
    p.write_text(json.dumps({"version": 2, "pairs": {}}))
    assert pr.load_ledger(tmp_path, "src")["pairs"] == {}
    assert p.with_name("src.json.bad.1").read_text().startswith('{"version": 2')  # 上書きしない
    assert pr.load_ledger(tmp_path, "nothing")["pairs"] == {}  # 無いだけなら退避しない
    assert not pr.ledger_path(tmp_path, "nothing").with_name("nothing.json.bad").exists()


def test_load_ledger_辞書でない件は読み飛ばす(tmp_path):
    p = pr.ledger_path(tmp_path, "src")
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"version": 1, "pairs": {"k": "str", "ok": {"key": "ok"}}, "origin": []}))
    led = pr.load_ledger(tmp_path, "src")
    assert set(led["pairs"]) == {"ok"}
    pr.plan_run(led, [])  # 落ちない


def test_merge_entry_failed_は既存の件を消さない():
    task = _task()
    led = pr.empty_ledger("s")
    pr.merge_entry(led, pr.check_answer(task, _ans("lowers"), TERMS), model="m", at="t0")
    before = _copy.deepcopy(led)
    pr.merge_entry(led, pr.failed_entry(task, "down"), model="m", at="t1")
    assert led == before
    led["pairs"][task["key"]]["fingerprint"] = "0" * 16  # 写しが変わった
    todo, _ = pr.plan_run(led, [task])
    assert len(todo) == 1  # 指紋が古いままなので次の回にまた聞く
    # 前が failed なら置き換わる・前が無ければ failed を置く
    fresh = pr.empty_ledger("s")
    pr.merge_entry(fresh, pr.failed_entry(task, "down"), model="m", at="t")
    pr.merge_entry(fresh, pr.failed_entry(task, "again"), model="m", at="t2")
    assert fresh["pairs"][task["key"]]["reason"] == "again"


def test_check_answer_形の合わない答えは例外にせず_dropped():
    task = _task()
    for bad in (None, "lowers", [1], 5):
        e = pr.check_answer(task, bad, TERMS)
        assert e["state"] == "dropped" and e["reason"] == "relation"
    for ev in (5, [1], [None], {"c1": 1}, "c1"):
        e = pr.check_answer(task, {"rel": "lowers", "from": "a", "evidence": ev}, TERMS)
        assert e["state"] == "dropped" and e["reason"] == "evidence", ev
    e = pr.check_answer(task, {"rel": "lowers", "from": ["a"], "evidence": ["c1"]}, TERMS)
    assert e["state"] == "dropped"


# ---- orphan・根拠が消えた件・台帳の守り・origin の輪（再レビューの直し）-------------------------


def test_orphan_は輪の辺にならず_数にも入らず_変えない():
    c = _origin_copy([])
    orphan = _sub("x|y", "x", "y", orphan=True, confirmed={"at": "t", "by": "u"})
    led = _led(orphan, _sub("y|x", "y", "x"))
    once = pr.finalize(led, c, dataset_id="d")
    assert once["pairs"]["x|y"] == orphan  # 注釈も付かない・変わらない
    assert once["pairs"]["y|x"]["state"] == "relation"  # orphan は辺に数えない（輪にならない）
    s = pr.summarize(once)
    assert s["pairs"] == 1 and s["orphans"] == 1 and s["relations"] == 1
    assert s["by_rel"] == {"sub": 1} and s["shown"] == 1 and s["terms"] == 2
    assert pr.finalize(once, c, dataset_id="d") == once


def test_orphan_は_origin_の突き合わせでは組が無いのと同じ():
    row = {"from": "x", "rel": "sub", "to": "y"}
    pairs = {"x|y": _sub("x|y", "x", "y", orphan=True)}
    assert pr.origin_check(row, pairs) == "unseen"


def test_根拠が全部消えた_relation_は落ち_一部だけなら残った根拠だけ():
    c = _origin_copy([])
    led = _led(
        _sub("x|y", "x", "y", evidence=["gone1", "gone2"]),
        _sub("x|z", "x", "z", evidence=["c01", "gone"]),
        _sub("y|z", "y", "z", evidence=["gone"], confirmed={"at": "t", "by": "u"}),
    )
    once = pr.finalize(led, c, dataset_id="d")
    assert (once["pairs"]["x|y"]["state"], once["pairs"]["x|y"]["reason"]) == ("dropped", "evidence")
    assert once["pairs"]["x|z"]["state"] == "relation" and once["pairs"]["x|z"]["evidence"] == ["c01"]
    assert once["pairs"]["y|z"]["state"] == "relation"  # confirmed は変えない
    assert pr.finalize(once, c, dataset_id="d") == once


def test_load_ledger_UTF8_でない_権限なし_key_なし(tmp_path):
    p = pr.ledger_path(tmp_path, "src")
    p.parent.mkdir(parents=True)
    p.write_bytes(b"\xff\xfe\x00{")
    assert pr.load_ledger(tmp_path, "src")["pairs"] == {}
    assert not p.exists() and p.with_name("src.json.bad").exists()
    p.write_text(json.dumps({"version": 1, "pairs": {"a": {"x": 1}, "b": {"key": "b"}}, "origin": []}))
    assert set(pr.load_ledger(tmp_path, "src")["pairs"]) == {"b"}
    p.chmod(0)
    try:
        if os.access(p, os.R_OK):  # root などで権限が効かない環境では確かめない
            return
        with pytest.raises(pr.LedgerError):
            pr.load_ledger(tmp_path, "src")
        assert p.exists()  # 空として読んで上書きしない
    finally:
        p.chmod(0o644)


def test_finalize_agrees_の行は輪の検査に通さない():
    c = _origin_copy([("x", "y")])
    led = _led(_sub("x|y", "x", "y"), _sub("y|z", "y", "z"), _sub("x|z", "z", "x"))
    once = pr.finalize(led, c, dataset_id="d")
    # 輪の 1 本（y|z）が落ちる。x|y は残り、origin の x sub y は agrees のまま
    assert once["pairs"]["x|y"]["state"] == "relation"
    assert [o["ai"] for o in once["origin"]] == ["agrees"]
    assert pr.finalize(once, c, dataset_id="d") == once


def test_finalize_前に_cycle_で落とした件は輪の原因が消えると戻る():
    c = _origin_copy([])
    led = _led(_sub("x|y", "x", "y"), _sub("y|z", "y", "z"), _sub("x|z", "z", "x"))
    once = pr.finalize(led, c, dataset_id="d")
    assert once["pairs"]["y|z"]["reason"] == "cycle"  # key の順で後の y|z が落ちる
    del once["pairs"]["x|z"]  # 輪の原因の件が消えた
    again = pr.finalize(once, c, dataset_id="d")
    assert again["pairs"]["y|z"]["state"] == "relation" and again["pairs"]["y|z"]["reason"] is None
