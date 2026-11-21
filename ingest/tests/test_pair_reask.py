"""R4 — 足した関係で「言えない」組だけを判定し直す: 選ぶ規則・force・reasked。"""

from __future__ import annotations

import copy
import dataclasses

from asterism import pair_relations as pr
from asterism import relation_catalog as rc
from asterism import relation_store as rs
from asterism.knowledge_bundle import KnowledgeCopy
from asterism.term_match import merge_key
from asterism.unstated_groups import unstated_groups


def _e(a, b, kind=None, state="none", **kw):
    e = {"key": f"{a}|{b}", "a": a, "b": b, "state": state, "fingerprint": "f1", "note": "理由"}
    if kind is not None:
        e["unstated_kind"] = kind
    e.update(kw)
    return e


def _ledger(*entries):
    return {"source": "s", "pairs": {e["key"]: e for e in entries}}


KEYS = {merge_key("併用")}


def test_選ぶ規則_鍵の一致と触らない組():
    led = _ledger(
        _e("t0", "t1", "併用"),
        _e("t0", "t2", "併用 "),  # 空白ゆれ
        _e("t0", "t3", "条件"),  # 別の種類
        _e("t0", "t4"),  # unstated_kind なし（R1 より前）
        _e("t0", "t5", "併用", state="relation"),
        _e("t0", "t6", "併用", confirmed={"at": "x"}),
        _e("t0", "t7", "併用", rejected=[{"rel": "x"}]),
        _e("t0", "t8", "併用", by="user"),
        _e("t0", "t9", "併用", orphan=True),
    )
    assert pr.reask_targets(led, relation_name="u_1", keys=KEYS) == ["t0|t1", "t0|t2"]


def test_reasked_に同じ関係があれば選ばない_別の関係なら選ぶ():
    led = _ledger(_e("t0", "t1", "併用", reasked=["u_1"]), _e("t0", "t2", "併用", reasked=["u_2"]))
    assert pr.reask_targets(led, relation_name="u_1", keys=KEYS) == ["t0|t2"]
    assert pr.reask_targets(led, relation_name="u_2", keys=KEYS) == ["t0|t1"]


def test_決定論_入力の並びに依らない():
    a = _ledger(_e("t0", "t2", "併用"), _e("t0", "t1", "併用"))
    b = _ledger(_e("t0", "t1", "併用"), _e("t0", "t2", "併用"))
    assert pr.reask_targets(a, relation_name="u", keys=KEYS) == pr.reask_targets(
        b, relation_name="u", keys=KEYS
    )


def test_reask_keys_は_unstated_key_と表示名():
    rel = dataclasses.replace(
        rc.relation("sub"), unstated_key="併用", label={"ja": "同時に使う", "en": "Used Together"}
    )
    assert pr.reask_keys(rel) == {"併用", merge_key("同時に使う"), merge_key("Used Together")}
    bare = dataclasses.replace(rc.relation("sub"), unstated_key=None, label={})
    assert pr.reask_keys(bare) == frozenset()


def _task(key, fp="f1"):
    a, b = key.split("|")
    return {"key": key, "a": a, "b": b, "fingerprint": fp, "claim_ids": []}


def test_plan_run_force_が空なら今と同じ():
    led = _ledger(_e("t0", "t1", "併用"), _e("t0", "t2", "併用", state="relation"))
    tasks = [_task("t0|t1"), _task("t0|t2"), _task("t0|t3")]
    assert pr.plan_run(led, tasks) == pr.plan_run(led, tasks, force=frozenset())
    todo, _ = pr.plan_run(led, tasks)
    assert [t["key"] for t in todo] == ["t0|t3"]


def test_plan_run_force_は指紋が同じでも聞く_confirmed_は聞かない():
    led = _ledger(_e("t0", "t1", "併用"), _e("t0", "t2", "併用", confirmed={"at": "x"}))
    tasks = [_task("t0|t1"), _task("t0|t2")]
    todo, base = pr.plan_run(led, tasks, force={"t0|t1", "t0|t2"})
    assert [t["key"] for t in todo] == ["t0|t1"]
    assert base["pairs"]["t0|t1"]["state"] == "none"  # 答えが来るまで今の件を残す


def _entry(key, state="none", **kw):
    return {"key": key, "a": "t0", "b": "t1", "state": state, "fingerprint": "f1", "rel": None,
            "evidence": [], **kw}


def test_merge_entry_reasked_none_でも_relation_でも残り_引き継ぐ():
    led = _ledger(_e("t0", "t1", "併用"))
    pr.merge_entry(led, _entry("t0|t1"), model="m", at="t", reasked_as="u_1")
    assert led["pairs"]["t0|t1"]["reasked"] == ["u_1"]
    pr.merge_entry(led, _entry("t0|t1", "relation", rel="sub"), model="m", at="t", reasked_as="u_2")
    assert led["pairs"]["t0|t1"]["reasked"] == ["u_1", "u_2"]
    # 通常の取り直しでも既存の記録は消えない・同じ名前は重ねない
    pr.merge_entry(led, _entry("t0|t1"), model="m", at="t")
    pr.merge_entry(led, _entry("t0|t1"), model="m", at="t", reasked_as="u_1")
    assert led["pairs"]["t0|t1"]["reasked"] == ["u_1", "u_2"]


def test_merge_entry_失敗なら_reasked_を足さない():
    led = _ledger(_e("t0", "t1", "併用"))
    pr.merge_entry(led, pr.failed_entry(_task("t0|t1"), "x"), model="m", at="t", reasked_as="u_1")
    assert "reasked" not in led["pairs"]["t0|t1"]


def test_reasked_を使わない呼び出しは欄を作らない():
    led = {"pairs": {}}
    pr.merge_entry(led, _entry("t0|t1"), model="m", at="t")
    assert "reasked" not in led["pairs"]["t0|t1"]


def test_finalize_と_strip_unconfirmed_は_reasked_を落とさない():
    copy_ = KnowledgeCopy(terms=[], claims=[], term_claims=[])
    led = _ledger(_e("t0", "t1", "併用", reasked=["u_1"]),
                  _e("t0", "t2", "併用", reasked=["u_1"], confirmed={"at": "x"}))
    out = pr.finalize(led, copy_, dataset_id=None)
    assert out["pairs"]["t0|t1"]["reasked"] == ["u_1"]
    kept, _ = pr.strip_unconfirmed(copy.deepcopy(out))
    assert kept["pairs"]["t0|t2"]["reasked"] == ["u_1"]


def test_load_ledger_壊れた_reasked_は欄ごと外す(tmp_path):
    led = _ledger(
        _e("t0", "t1", "併用", reasked=["u_1"]),
        _e("t0", "t2", "併用", reasked="u_1"),
        _e("t0", "t3", "併用", reasked=["u_1", 3]),
        _e("t0", "t4", "併用", reasked=[""]),
    )
    led["version"] = pr.LEDGER_VERSION
    pr.save_ledger(tmp_path, led, at="t")
    got = pr.load_ledger(tmp_path, "s")["pairs"]
    assert got["t0|t1"]["reasked"] == ["u_1"]
    assert all("reasked" not in got[k] for k in ("t0|t2", "t0|t3", "t0|t4"))


def test_unstated_groups_reaskable():
    def _t(tid):
        return {"id": tid, "name": tid, "kind": "class", "role": None, "iri": f"https://k/{tid}"}

    cp = KnowledgeCopy(terms=[_t(f"t{i}") for i in range(6)], claims=[], term_claims=[])
    led = _ledger(
        _e("t0", "t1", "併用"), _e("t0", "t2", "併用", reasked=["u_1"]), _e("t0", "t3", "条件")
    )
    rel, pred = rs._build(
        "u_1", label={"ja": "同時"}, sentence=rs.default_sentence({"ja": "同時"}, symmetric=True),
        definition={"ja": "説明"}, symmetric=True, to_property_only=False, exact_match=None,
        unstated_key=merge_key("併用"), added_at="2026-11-20T00:00:00Z",
    )
    old = rc.human_relations(), rc.human_predicates()
    try:
        rc.set_human([rel], [pred])
        groups = {g["label"]: g for g in unstated_groups([("s", led, cp)])}
        assert groups["併用"]["reaskable"] == {"u_1": 1}
        assert "reaskable" not in groups["条件"]
        rc.set_human([dataclasses.replace(rel, retired=True)], [pred])
        groups = {g["label"]: g for g in unstated_groups([("s", led, cp)])}
        assert "reaskable" not in groups["併用"]
    finally:
        rc.set_human(*old)
    groups = {g["label"]: g for g in unstated_groups([("s", led, cp)])}
    assert all("reaskable" not in g for g in groups.values())
