# ruff: noqa: E501
"""台帳の走者（``ledger_answer``）。例は作り物（実在の研究データは入れない）。"""

from __future__ import annotations

import json
import random
from pathlib import Path

from asterism import ledger_answer as la
from asterism import pair_relations as pr
from asterism.knowledge_bundle import KnowledgeCopy

_AT = "2026-11-18T00:00:00+00:00"
_RELS = ["sub", "part", "lowers", "raises", "affects", "makes", "measures", "becomes",
         "competes", "has_property", "eq"]


def _terms(*rows: tuple[str, str, str]) -> list[dict]:
    return [{"id": i, "name": n, "kind": k, "role": None} for i, n, k in rows]


def _pair(a: str, rel: str, b: str, **kw) -> dict:
    key = "|".join(sorted((a, b)))
    x, y = sorted((a, b))
    e = {"key": key, "a": x, "b": y, "state": "relation", "rel": rel, "from": a, "to": b,
         "evidence": [], "claims": [], "claims_total": 0}
    e.update(kw)
    return e


def _ledger(pairs: list[dict], origin: list[dict] | None = None, source: str = "lab") -> dict:
    ld = pr.empty_ledger(source)
    ld["pairs"] = {p["key"]: p for p in pairs}
    ld["origin"] = origin or []
    return ld


def _find(ld: dict, copy: KnowledgeCopy, op: str, tid: str, direction: str | None = None) -> dict:
    for q in la.ledger_questions(ld, copy):
        if q["op"] == op and q["term"]["id"] == tid and q.get("direction") == direction:
            return q
    raise AssertionError((op, tid, direction))


# ---- 件数が questions と一致 -----------------------------------------------------------------


def _random_trial(seed: int) -> tuple[dict, KnowledgeCopy]:
    rnd = random.Random(seed)
    terms = [
        {"id": f"t{i}", "name": f"名{i}", "kind": rnd.choice(["class", "class", "property"]), "role": None}
        for i in range(rnd.randint(4, 9))
    ]
    ids = [t["id"] for t in terms]
    pairs: dict[str, dict] = {}
    for _ in range(rnd.randint(3, 20)):
        a, b = rnd.sample(ids, 2)
        key = "|".join(sorted((a, b)))
        if key in pairs:
            continue
        rel = rnd.choice(_RELS)
        pairs[key] = _pair(a, rel, b, confirmed={"at": _AT} if rnd.random() < 0.3 else None)
        if not pairs[key]["confirmed"]:
            del pairs[key]["confirmed"]
        if rnd.random() < 0.15:
            pairs[key]["state"] = rnd.choice(["none", "dropped"])
    origin = []
    for _ in range(rnd.randint(0, 8)):
        a, b = rnd.sample(ids, 2)
        rel = rnd.choice(_RELS)
        if rel in pr.SYMMETRIC_PAIR_RELATIONS:
            a, b = sorted((a, b))
        origin.append({"from": a, "rel": rel, "to": b, "by": "origin",
                       **({"ai": rnd.choice(["agrees", "disputes", "rejected"])} if rnd.random() < 0.3 else {})})
    return _ledger(list(pairs.values()), origin), KnowledgeCopy(terms=terms)


def test_counts_equal_questions_and_answer_items_for_every_op_and_term() -> None:
    n_checked = 0
    for seed in range(120):
        ld, copy = _random_trial(seed)
        want, _ = pr.questions(ld, copy, lang="ja", limit=100000)
        got = la.ledger_questions(ld, copy, lang="ja")
        key = lambda i: (i["op"], i["term"]["id"], i.get("direction"))  # noqa: E731
        assert {key(i): i["count"] for i in got} == {key(i): i["count"] for i in want}, seed
        assert [i["text"] for i in got] == [i["text"] for i in want], seed
        names = [q["name"] for q in got]
        assert len(names) == len(set(names)), seed
        for q in got:
            a = la.answer(ld, copy, q, lang="ja")
            assert a["found"] and len(a["items"]) == q["count"], (seed, q)
            assert all(it["sentence"] and it["uses"] for it in a["items"]), (seed, q)
            n_checked += 1
    assert n_checked > 200


# ---- 型ごと ---------------------------------------------------------------------------------


def test_kinds_parts_and_factors_with_one_level_of_parts() -> None:
    copy = KnowledgeCopy(
        terms=_terms(("a", "粒界", "class"), ("b", "結晶", "class"), ("p", "硬さ", "property"),
                     ("q", "粒", "class")),
        claims=[{"id": "c1", "iri": "https://x/c1", "title": "粒界の知見", "source_url": "https://s/1"}],
    )
    ld = _ledger([
        _pair("a", "sub", "b", evidence=["c1"], claims_total=4),
        _pair("q", "part", "b"),
        _pair("a", "lowers", "p", evidence=["c1"], claims_total=2, confirmed={"at": _AT}),
        _pair("b", "part", "p"),  # b は p の一部 → p を下げるもの ∪ b を下げるもの
    ])
    k = la.answer(ld, copy, _find(ld, copy, "kinds", "b"))
    assert [i["label"] for i in k["items"]] == ["粒界"]
    assert k["items"][0]["sentence"] == "粒界 は 結晶 の一種"
    assert k["items"][0]["evidence"] == [{"id": "c1", "iri": "https://x/c1", "title": "粒界の知見", "source_url": "https://s/1"}]
    assert k["items"][0]["evidence_total"] == 4
    assert k["items"][0]["confirmed"] is False
    f = la.answer(ld, copy, _find(ld, copy, "factors", "p", "lowers"))
    assert [i["sentence"] for i in f["items"]] == ["粒界 は 硬さ を下げる"]
    assert f["items"][0]["confirmed"] is True
    # 「変える」は下げるも数える
    assert la.answer(ld, copy, _find(ld, copy, "factors", "p", "affects"))["count"] == 1


def test_factors_through_a_part_lists_both_relations_in_uses() -> None:
    copy = KnowledgeCopy(terms=_terms(("a", "添加物", "class"), ("x", "強度", "property"), ("pp", "引張強度", "property")))
    ld = _ledger([_pair("a", "lowers", "pp"), _pair("pp", "part", "x", confirmed={"at": _AT})])
    a = la.answer(ld, copy, _find(ld, copy, "factors", "x", "lowers"))
    assert a["count"] == 1
    item = a["items"][0]
    assert item["via_label"] == "引張強度" and item["label"] == "添加物"
    assert sorted(u["key"] for u in item["uses"]) == ["a|pp", "pp|x"]
    assert item["confirmed"] is False  # 1 本でも未確認なら未確認


def test_symmetric_relation_counts_from_either_end_and_origin_rows_use_origin_keys() -> None:
    copy = KnowledgeCopy(terms=_terms(("a", "方法甲", "class"), ("b", "方法乙", "class")))
    ld = _ledger([], origin=[{"from": "a", "rel": "competes", "to": "b", "by": "origin"}])
    qa, qb = _find(ld, copy, "competitors", "a"), _find(ld, copy, "competitors", "b")
    aa, ab = la.answer(ld, copy, qa), la.answer(ld, copy, qb)
    assert [i["label"] for i in aa["items"]] == ["方法乙"] and [i["label"] for i in ab["items"]] == ["方法甲"]
    assert aa["items"][0]["uses"] == [{"source": "lab", "key": "o:a|competes|b", "confirmed": False}]
    assert aa["items"][0]["evidence"] == [] and aa["items"][0]["evidence_total"] == 0


def test_line_ops_makers_measurers_and_sentences_follow_the_direction() -> None:
    copy = KnowledgeCopy(terms=_terms(("m", "焼結", "class"), ("s", "試料", "class"), ("x", "XRD", "class")))
    ld = _ledger([_pair("m", "makes", "s"), _pair("x", "measures", "s", confirmed={"at": _AT})])
    mk = la.answer(ld, copy, _find(ld, copy, "makers", "s"))
    assert mk["items"][0]["sentence"] == "焼結 で 試料 を作る"
    pr_ = la.answer(ld, copy, _find(ld, copy, "products", "m"))
    assert pr_["items"][0]["label"] == "試料" and pr_["items"][0]["sentence"] == "焼結 で 試料 を作る"
    me = la.answer(ld, copy, _find(ld, copy, "measurers", "s"))
    assert me["items"][0]["confirmed"] is True


def test_missing_lists_every_owner_relation_in_uses() -> None:
    copy = KnowledgeCopy(terms=_terms(
        ("x", "試料甲", "class"), ("o1", "試料乙", "class"), ("o2", "試料丙", "class"),
        ("p", "密度", "property"), ("e", "端", "class")))
    ld = _ledger([
        _pair("o1", "has_property", "p", confirmed={"at": _AT}),
        _pair("o2", "has_property", "p"),
        _pair("x", "sub", "e"),  # x は見せる関係に出る種類の語
    ])
    q = _find(ld, copy, "missing", "x")
    a = la.answer(ld, copy, q)
    assert a["count"] == q["count"] == 1
    item = a["items"][0]
    assert item["label"] == "密度" and "ほかの 2 件" in item["sentence"]
    assert sorted(u["key"] for u in item["uses"]) == ["o1|p", "o2|p"]
    assert item["confirmed"] is False
    assert item["evidence"] == [] and item["tentative"] is False


def test_context_tentative_and_evidence_cap() -> None:
    claims = [{"id": f"c{i}", "iri": f"https://x/c{i}", "title": f"知見{i}", "source_url": None} for i in range(5)]
    copy = KnowledgeCopy(terms=_terms(("a", "甲", "class"), ("b", "乙", "class"), ("z", "条件", "class")), claims=claims)
    ld = _ledger([_pair("a", "sub", "b", evidence=[f"c{i}" for i in range(5)], claims_total=9,
                        context=["z", "ghost"], tentative=True)])
    it = la.answer(ld, copy, _find(ld, copy, "kinds", "b"))["items"][0]
    assert [e["title"] for e in it["evidence"]] == ["知見0", "知見1", "知見2"]
    assert it["evidence_total"] == 9 and it["context"] == ["条件"] and it["tentative"] is True


def test_rejected_rows_and_dropped_pairs_are_not_used() -> None:
    copy = KnowledgeCopy(terms=_terms(("a", "甲", "class"), ("b", "乙", "class"), ("c", "丙", "class")))
    ld = _ledger(
        [_pair("a", "sub", "b", state="dropped")],
        origin=[{"from": "c", "rel": "sub", "to": "b", "by": "origin", "ai": "rejected"}],
    )
    assert la.ledger_questions(ld, copy) == []


def test_uses_keys_are_the_keys_the_confirm_gate_accepts() -> None:
    copy = KnowledgeCopy(terms=_terms(("a", "甲", "class"), ("b", "乙", "class"), ("c", "丙", "class")))
    ld = _ledger([_pair("a", "sub", "b")], origin=[{"from": "c", "rel": "sub", "to": "b", "by": "origin"}])
    a = la.answer(ld, copy, _find(ld, copy, "kinds", "b"))
    keys = [u["key"] for it in a["items"] for u in it["uses"]]
    assert sorted(keys) == ["a|b", "o:c|sub|b"]
    new, out = pr.confirm(ld, keys, at=_AT)
    assert sorted(out["changed"]) == sorted(keys) and out["skipped"] == []
    after = la.answer(new, copy, _find(new, copy, "kinds", "b"))
    assert all(it["confirmed"] for it in after["items"])
    assert la.summarize_use([after]) == {"unconfirmed": [], "unconfirmed_count": 0, "confirmed_count": 2}


def test_summarize_use_and_reply_block() -> None:
    copy = KnowledgeCopy(terms=_terms(("a", "甲", "class"), ("b", "乙", "class"), ("c", "丙", "class")))
    ld = _ledger([_pair("a", "sub", "b"), _pair("c", "sub", "b", confirmed={"at": _AT})])
    ans = la.answer(ld, copy, _find(ld, copy, "kinds", "b"))
    s = la.summarize_use([ans, ans])  # 同じ鍵は 1 度だけ
    assert s == {"unconfirmed": [{"source": "lab", "key": "a|b"}], "unconfirmed_count": 1, "confirmed_count": 1}
    blk = la.reply_block([ans, la.answer(ld, copy, {"op": "kinds", "term": {"id": "zz"}, "text": "t", "name": "lq_x"})])
    assert blk is not None and len(blk["questions"]) == 1 and blk["unconfirmed_count"] == 1
    assert set(blk["items"][0]) == {"label", "sentence", "confirmed", "tentative", "context", "evidence_total", "evidence", "uses"}
    assert la.reply_block([]) is None
    assert la.reply_block(list([la.answer(ld, copy, {"op": "kinds", "term": {"id": "zz"}, "text": "t", "name": "n"})])) is None
    cut = la.reply_block([ans], limit=1)
    assert cut is not None and len(cut["items"]) == 1 and cut["items_total"] == 2


def test_names_are_unique_and_safe_for_odd_term_ids() -> None:
    copy = KnowledgeCopy(terms=_terms(("a.b", "甲", "class"), ("a_b", "乙", "class"), ("ab", "丙", "class"), ("z", "終", "class")))
    ld = _ledger([_pair("z", "sub", "a.b"), _pair("z", "sub", "a_b"), _pair("z", "sub", "ab")])
    names = [q["name"] for q in la.ledger_questions(ld, copy)]
    assert len(names) == len(set(names)) == 3 and all(n.startswith("lq_kinds_") for n in names)
    assert all(n.replace("_", "").isalnum() for n in names)
    assert la.find_question(ld, copy, "lq_kinds_nothing") is None
    assert la.find_question(ld, copy, names[0]) is not None


# ---- registry から読む ----------------------------------------------------------------------


def _install(root: Path, source: str, dataset_id: str, *, status: str = "active", terms: list[dict] | None = None) -> dict:
    d = root / dataset_id
    (d / "source").mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"id": dataset_id, "status": status}), encoding="utf-8")
    (d / "source" / "terms.json").write_text(json.dumps(terms or _terms(("a", "甲", "class"), ("b", "乙", "class")), ensure_ascii=False), encoding="utf-8")
    ld = _ledger([_pair("a", "sub", "b")], source=source)
    ld["dataset_id"] = dataset_id
    pr.save_ledger(root, ld, at=_AT)
    return ld


def test_find_ledgers_skips_retracted_missing_and_broken(tmp_path: Path) -> None:
    _install(tmp_path, "good", "ds-good")
    _install(tmp_path, "gone", "ds-gone", status="retracted")
    orphan = _ledger([_pair("a", "sub", "b")], source="nocopy")
    orphan["dataset_id"] = "ds-none"
    pr.save_ledger(tmp_path, orphan, at=_AT)
    broken = pr.ledger_path(tmp_path, "broken")
    broken.write_text("{not json", encoding="utf-8")
    stats: dict[str, int] = {}
    got = la.find_ledgers(tmp_path, stats=stats)
    assert [s for s, _, _ in got] == ["good"]
    assert stats == {"broken": 1, "skipped": 2}
    assert broken.exists()  # 読むだけ・退避しない
    assert la.find_ledgers(None) == [] and la.find_ledgers(tmp_path / "none") == []
    one = la.load_source(tmp_path, "good")
    assert one is not None and one[0]["source"] == "good"
    assert la.load_source(tmp_path, "gone") is None and la.load_source(tmp_path, "broken") is None
