# ruff: noqa: E501
"""``pair_relations`` の 14d-2a の部分（見せる関係・答えられる問い・外す）のテスト。例は作り物。"""

from __future__ import annotations

import json
from pathlib import Path

from asterism import pair_relations as pr
from asterism.knowledge_bundle import KnowledgeCopy


def _t(tid, name, kind="class", role=None):
    return {"id": tid, "name": name, "kind": kind, "role": role}


def _rel(key, frm, rel, to, **kw):
    a, b = sorted((frm, to))
    e = {"key": f"{a}|{b}", "a": a, "b": b, "state": "relation", "rel": rel, "from": frm, "to": to,
         "evidence": ["c1"]}
    e.update(kw)
    return e


def _ledger(pairs, origin=()):
    led = pr.empty_ledger("lab")
    led["pairs"] = {p["key"]: p for p in pairs}
    led["origin"] = list(origin)
    return led


_COPY = KnowledgeCopy(terms=[
    _t("si", "Mg2Si", role="material"),
    _t("sn", "Mg2Sn", role="material"),
    _t("ge", "Mg2Ge", role="material"),
    _t("arc", "アーク溶解", role="method"),
    _t("xrd", "X線回折", role="method"),
    _t("kappa", "熱伝導率", kind="property"),
    _t("seebeck", "ゼーベック係数", kind="property"),
    _t("dope", "ドーピング", role="method"),
])


def _by(items, op, tid):
    return next((i for i in items if i["op"] == op and i["term"]["id"] == tid), None)


def test_shown_relations_are_ai_relations_plus_unseen_origin() -> None:
    led = _ledger(
        [
            _rel("k1", "arc", "makes", "si"),
            {"key": "kappa|si", "a": "kappa", "b": "si", "state": "none"},
            _rel("k3", "dope", "makes", "sn", orphan=True),
        ],
        origin=[
            {"from": "si", "rel": "has_property", "to": "kappa", "by": "origin", "ai": "unseen"},
            {"from": "sn", "rel": "has_property", "to": "kappa", "by": "origin", "ai": "agrees"},
            {"from": "ge", "rel": "has_property", "to": "kappa", "by": "origin", "ai": "disputes"},
            {"from": "ge", "rel": "has_property", "to": "seebeck", "by": "origin"},
        ],
    )
    rows = pr.shown_relations(led)
    assert [(r["by"], r["from"], r["rel"], r["to"]) for r in rows] == [
        ("ai", "arc", "makes", "si"),
        ("origin", "si", "has_property", "kappa"),
        ("origin", "ge", "has_property", "seebeck"),
    ]
    assert rows[0]["key"] == "arc|si" and "key" not in rows[1]
    s = pr.summarize(led)
    assert s["shown"] == 3 and s["terms"] == 5


def test_questions_count_each_type_by_confirmed_names() -> None:
    led = _ledger([
        _rel("1", "arc", "makes", "si"),
        _rel("2", "arc", "makes", "sn"),
        _rel("3", "xrd", "measures", "si"),
        _rel("4", "xrd", "measures", "kappa"),
        _rel("5", "dope", "becomes", "ge"),
        _rel("6", "sn", "sub", "si"),
        _rel("7", "ge", "part", "si"),
        _rel("8", "dope", "lowers", "kappa"),
        _rel("9", "arc", "raises", "seebeck"),
        _rel("10", "dope", "affects", "seebeck"),
        _rel("11", "si", "has_property", "kappa"),
    ])
    items, total = pr.questions(led, _COPY)
    assert total == len(items)
    assert _by(items, "makers", "si")["count"] == 1
    assert _by(items, "products", "arc")["count"] == 2
    # measures は両方の確定側の名前に当たる: 材料（si）にも項目（kappa）にも測る手法
    assert _by(items, "measurers", "si")["count"] == 1
    assert _by(items, "measurers", "kappa")["count"] == 1
    assert _by(items, "measured", "xrd")["count"] == 2
    assert _by(items, "sources", "ge")["count"] == 1 and _by(items, "outcomes", "dope")["count"] == 1
    assert _by(items, "kinds", "si")["count"] == 1 and _by(items, "parts", "si")["count"] == 1
    assert _by(items, "properties", "si")["count"] == 1
    assert _by(items, "bearers", "kappa")["count"] == 1
    # factors: affects の問いは 3 つの関係全部・lowers の問いは lowers と affects でない
    f = {(i["term"]["id"], i["direction"]): i["count"] for i in items if i["op"] == "factors"}
    assert f[("kappa", "lowers")] == 1 and f[("kappa", "affects")] == 1
    assert f[("seebeck", "raises")] == 1 and f[("seebeck", "affects")] == 2
    assert ("seebeck", "lowers") not in f


def test_symmetric_relation_counts_both_ends() -> None:
    led = _ledger([_rel("1", "si", "competes", "sn")])
    items, _ = pr.questions(led, _COPY)
    assert _by(items, "competitors", "si")["count"] == 1
    assert _by(items, "competitors", "sn")["count"] == 1


def test_a_term_of_the_wrong_kind_gets_no_question_of_that_type() -> None:
    # makes の to が項目（makers は種類だけ）・has_property の to が種類（bearers は項目だけ）
    led = _ledger([_rel("1", "arc", "makes", "kappa"), _rel("2", "si", "has_property", "sn")])
    items, _ = pr.questions(led, _COPY)
    assert _by(items, "makers", "kappa") is None
    assert _by(items, "bearers", "sn") is None
    assert _by(items, "products", "arc") is not None
    assert _by(items, "properties", "si") is not None


def test_missing_counts_properties_others_have_and_x_lacks() -> None:
    led = _ledger([
        _rel("1", "si", "has_property", "kappa"),
        _rel("2", "sn", "has_property", "kappa"),
        _rel("3", "ge", "has_property", "seebeck"),
        _rel("4", "arc", "makes", "ge"),
        _rel("5", "xrd", "measures", "ge"),
    ])
    items, _ = pr.questions(led, _COPY)
    # kappa は si・sn が持つ（2 語）。ge は持たない → ge で「まだ無い物性」は 1
    assert _by(items, "missing", "ge")["count"] == 1
    # si・sn は kappa を持つ。ほかに 2 語以上が持つ項目は無い
    assert _by(items, "missing", "si") is None and _by(items, "missing", "sn") is None
    # 関係に出ない語・項目の語は対象にしない
    assert _by(items, "missing", "dope") is None and _by(items, "missing", "kappa") is None


def test_order_limit_and_languages() -> None:
    led = _ledger([
        _rel("1", "arc", "makes", "si"),
        _rel("2", "dope", "makes", "si"),
        _rel("3", "xrd", "makes", "sn"),
        _rel("4", "arc", "makes", "ge"),
    ])
    items, total = pr.questions(led, _COPY, lang="ja", limit=2)
    assert total > 2 and len(items) == 2
    assert items[0]["count"] >= items[1]["count"]
    assert items[0]["text"] == "Mg2Si を作る手法" and items[0]["count"] == 2
    full, _ = pr.questions(led, _COPY, lang="ja", limit=50)
    keys = [(-i["count"], i["text"]) for i in full]
    assert keys == sorted(keys)
    en, _ = pr.questions(led, _COPY, lang="en")
    assert _by(en, "makers", "si")["text"] == "Methods that make Mg2Si"
    assert pr.questions(led, _COPY) == pr.questions(led, _COPY)


def test_questions_of_an_empty_ledger() -> None:
    assert pr.questions(pr.empty_ledger("lab"), _COPY) == ([], 0)


def test_counts_by_state_and_strip_unconfirmed() -> None:
    led = _ledger(
        [
            _rel("1", "arc", "makes", "si", confirmed={"by": "user", "at": "t"}),
            _rel("2", "arc", "makes", "sn"),
            {"key": "kappa|si", "a": "kappa", "b": "si", "state": "none"},
            {"key": "ge|si", "a": "ge", "b": "si", "state": "dropped", "reason": "same"},
        ],
        origin=[{"from": "si", "rel": "has_property", "to": "kappa", "by": "origin", "ai": "unseen"}],
    )
    led["dataset_id"] = "ds-1"
    assert pr.counts_by_state(led) == {"unconfirmed": 2, "confirmed": 1}
    kept, removed = pr.strip_unconfirmed(led)
    assert removed == {"relations": 2, "kept_confirmed": 1, "kept_rejected": 0}
    assert list(kept["pairs"]) == ["arc|si"] and kept["origin"] == []
    assert len(led["pairs"]) == 4 and led["origin"]  # 入力は変えない


def test_remove_ledger_keeps_only_confirmed_and_deletes_an_empty_file(tmp_path: Path) -> None:
    led = _ledger([_rel("1", "arc", "makes", "si", confirmed={"by": "user"}), _rel("2", "arc", "makes", "sn")])
    pr.save_ledger(tmp_path, led, at="t0")
    assert pr.remove_ledger(tmp_path, "lab", at="t1") == {"relations": 1, "kept_confirmed": 1, "kept_rejected": 0}
    after = pr.load_ledger(tmp_path, "lab")
    assert list(after["pairs"]) == ["arc|si"] and after["updated_at"] == "t1"
    assert after["origin"] == []

    led2 = _ledger([_rel("2", "arc", "makes", "sn")])
    pr.save_ledger(tmp_path, led2, at="t0")
    assert pr.remove_ledger(tmp_path, "lab", at="t2") == {"relations": 1, "kept_confirmed": 0, "kept_rejected": 0}
    assert not pr.ledger_path(tmp_path, "lab").exists()
    # 無い台帳は 0（落ちない）
    assert pr.remove_ledger(tmp_path, "lab", at="t3") == {"relations": 0, "kept_confirmed": 0, "kept_rejected": 0}
    json.dumps(led2)  # 台帳は JSON のまま


def test_factors_also_count_the_parts_of_x_one_level() -> None:
    led = _ledger([
        _rel("1", "ge", "part", "si"),  # ge は si の一部
        _rel("2", "dope", "lowers", "ge"),
        _rel("3", "arc", "raises", "si"),
    ])
    items, _ = pr.questions(led, _COPY)
    f = {(i["term"]["id"], i["direction"]): i["count"] for i in items if i["op"] == "factors"}
    assert f[("ge", "lowers")] == 1
    assert f[("si", "lowers")] == 1  # 一部 ge を下げるものも数える
    assert f[("si", "raises")] == 1 and f[("si", "affects")] == 2


def test_strip_keeps_rejected_as_orphan_and_is_idempotent() -> None:
    led = _ledger([
        _rel("1", "arc", "makes", "si", confirmed={"by": "user"}),
        _rel("2", "arc", "makes", "sn"),
        _rel("3", "xrd", "measures", "si", rejected=[{"rel": "measures", "from": "xrd", "to": "si", "at": "t"}]),
    ])
    kept, removed = pr.strip_unconfirmed(led)
    assert removed == {"relations": 2, "kept_confirmed": 1, "kept_rejected": 1}
    rej = kept["pairs"]["si|xrd"]
    assert rej == {"key": "si|xrd", "a": "si", "b": "xrd", "orphan": True,
                   "rejected": led["pairs"]["si|xrd"]["rejected"]}
    again, removed2 = pr.strip_unconfirmed(kept)
    assert again["pairs"] == kept["pairs"]
    assert removed2["kept_confirmed"] == 1 and removed2["kept_rejected"] == 1
    # 入れ直しても、rejected の 3 つ組は出ない
    task = {"key": "si|xrd", "a": "si", "b": "xrd", "claim_ids": ["c1"], "claims_total": 1,
            "fingerprint": "f"}
    terms = {t["id"]: t for t in _COPY.terms}
    answer = {"rel": "measures", "from": "a", "evidence": ["c1"]}
    entry = pr.check_answer(task, {**answer, "from": "b"}, terms, rejected=rej["rejected"])
    assert entry["state"] == "dropped" and entry["reason"] == "rejected"


def test_remove_ledger_keeps_a_rejected_only_ledger(tmp_path: Path) -> None:
    led = _ledger([_rel("1", "xrd", "measures", "si", rejected=[{"rel": "measures", "from": "xrd", "to": "si"}])])
    pr.save_ledger(tmp_path, led, at="t0")
    assert pr.remove_ledger(tmp_path, "lab", at="t1") == {"relations": 1, "kept_confirmed": 0, "kept_rejected": 1}
    assert pr.ledger_path(tmp_path, "lab").exists()


def test_a_stateless_rejected_only_entry_is_counted_nowhere() -> None:
    stub = {"key": "si|xrd", "a": "si", "b": "xrd", "rejected": [{"rel": "measures", "from": "xrd", "to": "si"}]}
    led = _ledger([_rel("1", "arc", "makes", "si"), stub],
                  origin=[{"from": "xrd", "rel": "measures", "to": "si", "by": "origin"}])
    led["origin"][0]["ai"] = pr.origin_check(led["origin"][0], led["pairs"])
    s = pr.summarize(led)
    assert s["pairs"] == 1 and s["orphans"] == 0 and s["relations"] == 1 and s["failed"] == 0
    assert s["none"] == 0 and s["dropped"] == {} and s["candidates"] == 0
    # 取り込み元の同じ組の関係は「まだ読んでいない」（disputes にしない）
    assert led["origin"][0]["ai"] == "unseen" and s["shown"] == 2
    assert [r["by"] for r in pr.shown_relations(led)] == ["ai", "origin"]
    assert pr.counts_by_state(led) == {"unconfirmed": 2, "confirmed": 0}
    # plan_run は今までどおり聞く対象にする
    task = {"key": "si|xrd", "a": "si", "b": "xrd", "claim_ids": ["c1"], "claims_total": 1, "fingerprint": "f"}
    todo, _ = pr.plan_run(led, [task])
    assert [t["key"] for t in todo] == ["si|xrd"]
