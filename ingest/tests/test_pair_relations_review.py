# ruff: noqa: E501
"""``pair_relations`` の確認・違う・人が付ける（14e-1）のテスト。例は作り物。"""

from __future__ import annotations

import copy as _copy

from asterism import pair_relations as pr
from asterism.knowledge_bundle import KnowledgeCopy

AT = "2026-11-18T00:00:00+00:00"


def _t(tid, name, kind="class", role=None):
    return {"id": tid, "name": name, "kind": kind, "role": role, "iri": f"https://k.test/t/{tid}"}


def _c(cid, title):
    return {"id": cid, "iri": f"https://k.test/c/{cid}", "title": title, "source_url": None,
            "review_state": "candidate", "epistemic_status": None, "modal_qualifier": None}


COPY = KnowledgeCopy(
    terms=[
        _t("si", "Mg2Si", role="material"),
        _t("sn", "Mg2Sn", role="material"),
        _t("arc", "アーク溶解", role="method"),
        _t("kappa", "熱伝導率", kind="property"),
        _t("zt", "ZT", kind="property"),
    ],
    claims=[_c("c1", "Mg2Si はアーク溶解で作る"), _c("c2", "Mg2Si の熱伝導率"), _c("c3", "Mg2Sn の熱伝導率")],
    term_claims=[
        {"term_id": t, "claim_id": c, "by": "ai"}
        for c, ts in (("c1", ["si", "arc"]), ("c2", ["si", "kappa"]), ("c3", ["sn", "kappa"]))
        for t in ts
    ],
    term_relations=[
        {"from_term": "si", "rel": "has_property", "to_term": "kappa", "origin_iri": "https://k.test/o"}
    ],
)
TERMS = {t["id"]: t for t in COPY.terms}


def _entry(frm, rel, to, **kw):
    a, b = sorted((frm, to))
    e = {"key": f"{a}|{b}", "a": a, "b": b, "fingerprint": f"fp-{a}-{b}", "claims": ["c1"],
         "claims_total": 1, "state": "relation", "rel": rel, "from": frm, "to": to,
         "evidence": ["c1"], "note": None, "reason": None, "by": "ai", "confirmed": None,
         "rejected": []}
    e.update(kw)
    return e


def _none(a, b):
    a, b = sorted((a, b))
    return {"key": f"{a}|{b}", "a": a, "b": b, "fingerprint": "fp", "claims": ["c1", "c2"],
            "claims_total": 2, "state": "none", "rel": None, "from": None, "to": None,
            "evidence": [], "note": None, "reason": None, "by": "ai", "confirmed": None,
            "rejected": []}


def _led(pairs, origin=()):
    led = pr.empty_ledger("lab")
    led["pairs"] = {p["key"]: p for p in pairs}
    led["origin"] = list(origin)
    return led


def _origin(frm, rel, to, ai="unseen"):
    return {"from": frm, "rel": rel, "to": to, "origin_iri": "https://k.test/o", "by": "origin", "ai": ai}


def _shown(led):
    return {(r["from"], r["rel"], r["to"]) for r in pr.shown_relations(led)}


# ---- confirm ----


def test_confirm_AIの関係に確認が付き_冪等で_入力は変わらない():
    led = _led([_entry("arc", "makes", "si")])
    before = _copy.deepcopy(led)
    out, res = pr.confirm(led, ["arc|si", "nope|x"], at=AT)
    assert led == before
    assert res == {"changed": ["arc|si"], "skipped": [{"key": "nope|x", "reason": "unknown"}]}
    assert out["pairs"]["arc|si"]["confirmed"] == {
        "rel": "makes", "from": "arc", "to": "si", "at": AT, "by": "user", "evidence": ["c1"]}
    again, res2 = pr.confirm(out, ["arc|si"], at="later")
    assert again == out and res2 == {"changed": [], "skipped": [{"key": "arc|si", "reason": "already_confirmed"}]}
    assert pr.counts_by_state(out) == {"unconfirmed": 0, "confirmed": 1}


def test_confirm_見せていない件は_skipped():
    led = _led([_none("si", "kappa"), _entry("arc", "makes", "si", state="dropped", reason="mixed", orphan=True)])
    _, res = pr.confirm(led, ["kappa|si", "arc|si"], at=AT)
    assert res["changed"] == [] and [s["reason"] for s in res["skipped"]] == ["unknown", "unknown"]


def test_confirm_取り込み元の行は件になり_AIの関係と同じ1本として数える():
    led = _led([], origin=[_origin("si", "has_property", "kappa")])
    out, res = pr.confirm(led, ["o:si|has_property|kappa"], at=AT)
    assert res["changed"] == ["o:si|has_property|kappa"]
    e = out["pairs"]["kappa|si"]
    assert e["by"] == "origin" and e["evidence"] == [] and e["confirmed"]["by"] == "user"
    fin = pr.finalize(out, COPY, dataset_id="d")
    assert fin["origin"][0]["ai"] == "agrees"
    assert _shown(fin) == {("si", "has_property", "kappa")}
    assert pr.counts_by_state(fin) == {"unconfirmed": 0, "confirmed": 1}
    assert pr.finalize(fin, COPY, dataset_id="d") == fin


def test_confirmは_AIが読んでいる取り込み元の行には使えない():
    led = _led([_none("si", "kappa")], origin=[_origin("si", "has_property", "kappa", ai="disputes")])
    _, res = pr.confirm(led, ["kappa|si"], at=AT)
    assert res["changed"] == []


def test_確認済みは_読み直しで上書きされない():
    entry = _entry("arc", "makes", "si")
    led = _led([entry])
    led, _ = pr.confirm(led, [entry["key"]], at=AT)
    task = {"key": "arc|si", "a": "arc", "b": "si", "claim_ids": ["c1"], "claims_total": 1, "fingerprint": "changed"}
    todo, base = pr.plan_run(led, [task])
    assert todo == []
    pr.merge_entry(base, pr.check_answer(task, {"rel": "none", "from": None, "evidence": []}, TERMS), model="m", at=AT)
    assert base["pairs"]["arc|si"]["rel"] == "makes" and base["pairs"]["arc|si"]["confirmed"]
    fin = pr.finalize(base, COPY, dataset_id="d")
    assert fin["pairs"]["arc|si"]["state"] == "relation" and fin["pairs"]["arc|si"]["confirmed"]


def test_取り込み元から確認した件は_組が写しに無くても_orphanにならず見え続ける():
    led = _led([], origin=[_origin("si", "has_property", "kappa")])
    led, _ = pr.confirm(led, ["o:si|has_property|kappa"], at=AT)
    todo, base = pr.plan_run(led, [])  # 組が 1 つも無い
    assert todo == [] and "orphan" not in base["pairs"]["kappa|si"]
    fin = pr.finalize(base, COPY, dataset_id="d")
    assert _shown(fin) == {("si", "has_property", "kappa")}


def test_確認済みの輪は落ちない():
    a = _entry("si", "sub", "sn", key="si|sn")
    b = _entry("sn", "sub", "si", key="si|sn2", a="si", b="sn")
    led = _led([a, b])
    led, _ = pr.confirm(led, ["si|sn2"], at=AT)
    fin = pr.finalize(led, COPY, dataset_id="d")
    assert fin["pairs"]["si|sn2"]["state"] == "relation"


# ---- unconfirm ----


def test_unconfirm_確認を消して_未確認に戻る():
    led, _ = pr.confirm(_led([_entry("arc", "makes", "si")]), ["arc|si"], at=AT)
    out, res = pr.unconfirm(led, ["arc|si", "arc|si", "x|y"])
    assert res == {"changed": ["arc|si"], "skipped": [{"key": "x|y", "reason": "unknown"}]}
    assert out["pairs"]["arc|si"]["confirmed"] is None and out["pairs"]["arc|si"]["state"] == "relation"
    assert pr.unconfirm(out, ["arc|si"])[1]["skipped"] == [{"key": "arc|si", "reason": "not_confirmed"}]


def test_unconfirm_取り込み元から確認した件は取り込み元の行に戻る():
    led, _ = pr.confirm(_led([], origin=[_origin("si", "has_property", "kappa")]), ["o:si|has_property|kappa"], at=AT)
    out, _ = pr.unconfirm(led, ["o:si|has_property|kappa"])
    assert "kappa|si" not in out["pairs"]
    assert _shown(pr.finalize(out, COPY, dataset_id="d")) == {("si", "has_property", "kappa")}


def test_unconfirm_根拠の無い人が付けた件は候補に戻る():
    led = _led([_none("arc", "si") | {"claims": []}])
    led, _ = pr.assign(led, TERMS, "arc|si", "makes", "a", at=AT)
    out, res = pr.unconfirm(led, ["arc|si"])
    assert res["changed"] == ["arc|si"] and out["pairs"]["arc|si"]["state"] == "none"
    assert pr.finalize(out, COPY, dataset_id="d")["pairs"]["arc|si"]["state"] == "none"


# ---- reject / restore ----


def test_reject_関係を見せず_次の聞き直しでも出ない():
    led = _led([_entry("arc", "makes", "si")])
    out, res = pr.reject(led, ["arc|si", "zz|yy"], at=AT)
    assert res["changed"] == ["arc|si"] and res["skipped"] == [{"key": "zz|yy", "reason": "unknown"}]
    e = out["pairs"]["arc|si"]
    assert (e["state"], e["reason"], e["confirmed"]) == ("dropped", "rejected", None)
    assert e["rejected"] == [{"rel": "makes", "from": "arc", "to": "si", "at": AT}]
    assert _shown(out) == set()
    assert pr.reject(out, ["arc|si"], at=AT)[1]["skipped"] == [{"key": "arc|si", "reason": "already_rejected"}]
    # 聞き直して同じ答えが来ても出ない
    task = {"key": "arc|si", "a": "arc", "b": "si", "claim_ids": ["c1"], "claims_total": 1, "fingerprint": "new"}
    todo, base = pr.plan_run(out, [task])
    assert [t["key"] for t in todo] == ["arc|si"]
    ent = pr.check_answer(task, {"rel": "makes", "from": "a", "evidence": ["c1"]}, TERMS, rejected=base["pairs"]["arc|si"]["rejected"])
    pr.merge_entry(base, ent, model="m", at=AT)
    assert base["pairs"]["arc|si"]["state"] == "dropped" and base["pairs"]["arc|si"]["reason"] == "rejected"


def test_reject_確認済みも外れる():
    led, _ = pr.confirm(_led([_entry("arc", "makes", "si")]), ["arc|si"], at=AT)
    out, _ = pr.reject(led, ["arc|si"], at=AT)
    assert out["pairs"]["arc|si"]["confirmed"] is None
    assert pr.counts_by_state(out)["confirmed"] == 0


def test_reject_取り込み元の行は_読み直しの後も出ない():
    led = _led([], origin=[_origin("si", "has_property", "kappa")])
    out, res = pr.reject(led, ["o:si|has_property|kappa"], at=AT)
    assert res["changed"] == ["o:si|has_property|kappa"]
    assert out["pairs"]["kappa|si"]["orphan"] is True
    _, base = pr.plan_run(out, [])
    fin = pr.finalize(base, COPY, dataset_id="d")
    assert fin["origin"][0]["ai"] == "rejected" and _shown(fin) == set()
    s = pr.summarize(fin)
    assert s["shown"] == 0 and s["candidates"] == 0 and s["origin_check"] == {"agrees": 0, "disputes": 0, "unseen": 0}
    assert pr.finalize(fin, COPY, dataset_id="d") == fin
    # 外すときも「違う」の記録は残る
    _kept, info = pr.strip_unconfirmed(fin)
    assert info["kept_rejected"] == 1


def test_restore_関係を未確認で戻す():
    led, _ = pr.reject(_led([_entry("arc", "makes", "si")]), ["arc|si"], at=AT)
    out, res = pr.restore(led, ["arc|si", "none|x"])
    assert res == {"changed": ["arc|si"], "skipped": [{"key": "none|x", "reason": "unknown"}]}
    e = out["pairs"]["arc|si"]
    assert (e["state"], e["reason"], e["rejected"], e["confirmed"]) == ("relation", None, [], None)
    assert _shown(out) == {("arc", "makes", "si")}
    assert pr.restore(out, ["arc|si"])[1]["skipped"] == [{"key": "arc|si", "reason": "not_rejected"}]


def test_restore_根拠の無い確認済みは_確認も戻る():
    led = _led([], origin=[_origin("si", "has_property", "kappa")])
    led, _ = pr.confirm(led, ["o:si|has_property|kappa"], at=AT)
    led, _ = pr.reject(led, ["o:si|has_property|kappa"], at=AT)
    out, _ = pr.restore(led, ["o:si|has_property|kappa"])
    assert out["pairs"]["kappa|si"]["confirmed"]["rel"] == "has_property"
    assert _shown(pr.finalize(out, COPY, dataset_id="d")) == {("si", "has_property", "kappa")}


def test_restore_取り込み元の行は_記録だけ消えて件ごと外れ_行が戻る():
    led, _ = pr.reject(_led([], origin=[_origin("si", "has_property", "kappa")]), ["o:si|has_property|kappa"], at=AT)
    out, _ = pr.restore(led, ["o:si|has_property|kappa"])
    assert "kappa|si" not in out["pairs"]
    assert _shown(pr.finalize(out, COPY, dataset_id="d")) == {("si", "has_property", "kappa")}


def test_restore_orphanで関係が残っていない件は_件ごと消えて次に聞き直される():
    stub = {"key": "arc|si", "a": "arc", "b": "si", "orphan": True,
            "rejected": [{"rel": "makes", "from": "arc", "to": "si", "at": AT}]}
    out, res = pr.restore(_led([stub]), ["arc|si"])
    assert res["changed"] == ["arc|si"] and out["pairs"] == {}
    task = {"key": "arc|si", "a": "arc", "b": "si", "claim_ids": ["c1"], "claims_total": 1, "fingerprint": "f"}
    assert [t["key"] for t in pr.plan_run(out, [task])[0]] == ["arc|si"]


# ---- assign ----


def test_assign_候補に関係を付け_確認済みで置く_聞き直しで消えない():
    led = _led([_none("si", "kappa")])
    out, res = pr.assign(led, COPY.terms, "kappa|si", "affects", "b", at=AT)
    assert res == {"changed": ["kappa|si"], "skipped": []}
    e = out["pairs"]["kappa|si"]
    assert (e["state"], e["rel"], e["from"], e["to"], e["by"]) == ("relation", "affects", "si", "kappa", "user")
    assert e["confirmed"]["by"] == "user" and e["evidence"] == ["c1", "c2"] and "model" not in e
    assert _shown(out) == {("si", "affects", "kappa")}
    assert pr.assign(out, TERMS, "kappa|si", "affects", "b", at=AT)[1]["skipped"] == [{"key": "kappa|si", "reason": "already_related"}]
    task = {"key": "kappa|si", "a": "kappa", "b": "si", "claim_ids": ["c1"], "claims_total": 2, "fingerprint": "new"}
    todo, base = pr.plan_run(out, [task])
    assert todo == []
    pr.merge_entry(base, pr.check_answer(task, {"rel": "none", "from": None, "evidence": []}, TERMS), model="m", at="t")
    fin = pr.finalize(base, COPY, dataset_id="d")
    assert fin["pairs"]["kappa|si"]["rel"] == "affects" and fin["pairs"]["kappa|si"]["confirmed"]


def test_assign_落ちた組にも付けられ_向きの無い関係は並べ直される():
    drop = _none("si", "sn") | {"state": "dropped", "reason": "mixed"}
    out, res = pr.assign(_led([drop]), TERMS, "si|sn", "eq", "b", at=AT)
    assert res["changed"] == ["si|sn"]
    e = out["pairs"]["si|sn"]
    assert (e["from"], e["to"]) == ("si", "sn") and e["reason"] is None


def test_assign_機械の検査に落ちたものは付けない():
    led = _led([_none("si", "kappa"), _none("arc", "si"), _none("si", "sn"), _none("kappa", "zt")])
    cases = [
        ("kappa|si", "makes", "a", "mixed"),  # 項目が作る側にならない
        ("kappa|si", "sub", "a", "mixed"),
        ("kappa|si", "affects", "a", "target_not_property"),  # 材料が主語・相手が項目の向きの逆（kappa → si）
        ("arc|si", "nonsense", "a", "relation"),
        ("arc|si", "makes", "c", "direction"),
        ("si|sn", "eq", "a", None),
        ("no|such", "makes", "a", "unknown"),
    ]
    for key, rel, subj, why in cases:
        out, res = pr.assign(led, TERMS, key, rel, subj, at=AT)
        if why is None:
            assert res["changed"] == [key]
        else:
            assert res["changed"] == [] and res["skipped"] == [{"key": key, "reason": why}], (key, rel, res)
            assert out == led


def test_assign_一種の輪になるものと_違うとした3つ組は付けない():
    led = _led([_entry("si", "sub", "sn"), _entry("sn", "sub", "arc"), _none("arc", "si")])
    _, res = pr.assign(led, TERMS, "arc|si", "sub", "a", at=AT)  # arc sub si は si→sn→arc と輪
    assert res["skipped"] == [{"key": "arc|si", "reason": "cycle"}]
    rejected = _none("arc", "si") | {"rejected": [{"rel": "makes", "from": "arc", "to": "si", "at": AT}]}
    _, res = pr.assign(_led([rejected]), TERMS, "arc|si", "makes", "a", at=AT)
    assert res["skipped"] == [{"key": "arc|si", "reason": "rejected"}]
    _, res = pr.assign(_led([rejected]), TERMS, "arc|si", "makes", "b", at=AT)  # 逆向きは別の 3 つ組
    assert res["skipped"] and res["skipped"][0]["reason"] != "rejected"


# ---- 行ごとの鍵（o:）・origin 由来の「違う」・輪・人の関係（反証で出た穴の回帰） ----

_TWO = KnowledgeCopy(
    terms=COPY.terms, claims=COPY.claims, term_claims=COPY.term_claims,
    term_relations=[
        {"from_term": "si", "rel": "has_property", "to_term": "kappa", "origin_iri": "o1"},
        {"from_term": "si", "rel": "lowers", "to_term": "kappa", "origin_iri": "o2"},
    ],
)
_HP = "o:si|has_property|kappa"
_LW = "o:si|lowers|kappa"


def _fin(led, cp=_TWO):
    return pr.finalize(led, cp, dataset_id="d")


def _marks(led):
    return {pr.origin_key(o): o["ai"] for o in led["origin"]}


def test_origin_key_は向きの無い関係で_id_の順():
    assert pr.origin_key({"from": "sn", "rel": "eq", "to": "si"}) == "o:si|eq|sn"
    assert pr.origin_key({"from": "sn", "rel": "sub", "to": "si"}) == "o:sn|sub|si"


def test_同じ2語の別の関係は_行ごとの鍵で別々に効く():
    led = _fin(pr.empty_ledger("lab"))
    assert _marks(led) == {_HP: "unseen", _LW: "unseen"}
    out, res = pr.confirm(led, [_HP], at=AT)
    assert res["changed"] == [_HP]
    out = _fin(out)
    assert _marks(out) == {_HP: "agrees", _LW: "disputes"}  # 組には確認した 1 本が付く
    assert _shown(out) == {("si", "has_property", "kappa")}
    # もう 1 本は、組に別の関係があるので確認できない
    _, res = pr.confirm(out, [_LW], at=AT)
    assert res == {"changed": [], "skipped": [{"key": _LW, "reason": "pair_has_relation"}]}
    # 2 回目は同じ結果
    again, res = pr.confirm(out, [_HP], at=AT)
    assert again == out and res["skipped"] == [{"key": _HP, "reason": "already_confirmed"}]


def test_行の鍵の_reject_はその3つ組だけで_2回送っても同じ():
    led = _fin(pr.empty_ledger("lab"))
    out, res = pr.reject(led, [_LW], at=AT)
    assert res["changed"] == [_LW]
    out = _fin(out)
    assert _marks(out) == {_HP: "unseen", _LW: "rejected"}
    assert _shown(out) == {("si", "has_property", "kappa")}
    again, res = pr.reject(out, [_LW], at=AT)
    assert again == out and res["skipped"] == [{"key": _LW, "reason": "already_rejected"}]
    # 読み直し・外す → 入れ直しでも出ない
    _, base = pr.plan_run(out, [])
    assert _marks(_fin(base))[_LW] == "rejected"
    kept, _info = pr.strip_unconfirmed(out)
    assert _marks(_fin(kept))[_LW] == "rejected"
    back, res = pr.restore(out, [_LW])
    assert res["changed"] == [_LW] and _marks(_fin(back))[_LW] == "unseen"
    assert pr.restore(back, [_LW])[1]["skipped"] == [{"key": _LW, "reason": "not_rejected"}]


def test_組の鍵は取り込み元の行を指さない():
    led = _fin(pr.empty_ledger("lab"))
    for fn in (
        lambda: pr.confirm(led, ["kappa|si"], at=AT),
        lambda: pr.reject(led, ["kappa|si"], at=AT),
        lambda: pr.unconfirm(led, ["kappa|si"]),
        lambda: pr.restore(led, ["kappa|si"]),
    ):
        out, res = fn()
        assert res["changed"] == [] and out == led


def test_AIと同じ関係の取り込み元の行は_行の鍵でも組の件を確認できる():
    led = _led([_entry("si", "has_property", "kappa")])
    led = _fin(led)
    assert _marks(led)[_HP] == "agrees"
    out, res = pr.confirm(led, [_HP], at=AT)
    assert res["changed"] == [_HP] and out["pairs"]["kappa|si"]["confirmed"]["rel"] == "has_property"


def test_取り込み元から確認した件を_違うにすると_記録に_origin_が付き_どこでも出ない():
    led = _fin(pr.empty_ledger("lab"))
    led, _ = pr.confirm(led, [_HP], at=AT)
    led = _fin(led)
    out, res = pr.reject(led, ["kappa|si"], at=AT)
    assert res["changed"] == ["kappa|si"]
    assert out["pairs"]["kappa|si"]["rejected"][0]["origin"] is True
    out = _fin(out)
    assert _marks(out)[_HP] == "rejected" and ("si", "has_property", "kappa") not in _shown(out)
    _, base = pr.plan_run(out, [])
    assert _marks(_fin(base))[_HP] == "rejected"
    kept, _info = pr.strip_unconfirmed(out)
    assert _marks(_fin(kept))[_HP] == "rejected"
    assert ("si", "has_property", "kappa") not in _shown(_fin(kept))
    # 戻すと確認ごと戻る
    back, res = pr.restore(out, [_HP])
    assert res["changed"] == [_HP]
    back = _fin(back)
    assert back["pairs"]["kappa|si"]["confirmed"] and ("si", "has_property", "kappa") in _shown(back)


def test_restore_確認を戻すとき_輪になるなら戻さない():
    led = _led([], origin=[_origin("si", "sub", "sn")])
    cp = KnowledgeCopy(
        terms=COPY.terms, claims=COPY.claims, term_claims=COPY.term_claims,
        term_relations=[{"from_term": "si", "rel": "sub", "to_term": "sn", "origin_iri": "o"}],
    )
    led = _fin(led, cp)
    key = "o:si|sub|sn"
    led, _ = pr.confirm(led, [key], at=AT)
    led, _ = pr.reject(_fin(led, cp), ["si|sn"], at=AT)
    led = _fin(led, cp)
    # その間に、AI が sn → si の「一種」を置いた（si → sn を戻すと輪になる）
    led["pairs"]["m|n"] = _entry("sn", "sub", "si") | {"key": "m|n", "a": "m", "b": "n"}
    out, res = pr.restore(led, [key])
    assert res == {"changed": [], "skipped": [{"key": key, "reason": "cycle"}]}
    assert out == led


def test_unconfirm_人が付けた関係は根拠があっても候補に戻る():
    led = _led([_none("arc", "si")])
    led, _ = pr.assign(led, TERMS, "arc|si", "makes", "a", at=AT)
    assert led["pairs"]["arc|si"]["evidence"]
    out, res = pr.unconfirm(led, ["arc|si"])
    assert res["changed"] == ["arc|si"]
    assert out["pairs"]["arc|si"]["state"] == "none" and out["pairs"]["arc|si"]["by"] is None
    assert ("arc", "makes", "si") not in _shown(pr.finalize(out, COPY, dataset_id="d"))


def test_restore_人が付けた関係は_確認ごと戻り_未確認のまま残らない():
    led = _led([_none("arc", "si")])
    led, _ = pr.assign(led, TERMS, "arc|si", "makes", "a", at=AT)
    led, _ = pr.reject(led, ["arc|si"], at=AT)
    out, _ = pr.restore(led, ["arc|si"])
    assert out["pairs"]["arc|si"]["confirmed"] and out["pairs"]["arc|si"]["by"] == "user"


def test_summarize_origin_は_違うとした行を数えず_origin_check_と合う():
    led = _fin(pr.empty_ledger("lab"))
    led, _ = pr.reject(led, [_LW], at=AT)
    s = pr.summarize(_fin(led))
    assert s["origin"] == sum(s["origin_check"].values()) == 1


def test_乱数列の操作でも_違うは出ず_確認は消えず_同じ操作は2回で同じ():
    import random

    ids = ["si", "sn", "arc", "kappa", "zt"]
    for seed in range(60):
        rng = random.Random(seed)
        rels = [
            {"from_term": a, "rel": rng.choice(["sub", "has_property", "makes", "eq", "affects"]),
             "to_term": b, "origin_iri": "o"}
            for a, b in (rng.sample(ids, 2) for _ in range(rng.randint(1, 4)))
        ]
        cp = KnowledgeCopy(
            terms=COPY.terms, claims=COPY.claims, term_claims=COPY.term_claims, term_relations=rels
        )
        led = _fin(pr.empty_ledger("lab"), cp)
        # AI の関係を数本置く
        for a, b in (rng.sample(ids, 2) for _ in range(rng.randint(0, 3))):
            rel = rng.choice(["sub", "makes", "eq", "affects"])
            e = _entry(a, rel, b)
            led["pairs"][e["key"]] = e
        led = _fin(led, cp)
        for _ in range(rng.randint(3, 12)):
            keys = list(led["pairs"]) + [pr.origin_key(o) for o in led["origin"]]
            ks = rng.sample(keys, min(len(keys), rng.randint(1, 2))) if keys else ["x|y"]
            op = rng.choice(["confirm", "unconfirm", "reject", "restore", "strip", "plan"])
            confirmed = {k for k, p in led["pairs"].items() if p.get("confirmed")}
            if op == "strip":
                new, _ = pr.strip_unconfirmed(led)
            elif op == "plan":
                _, new = pr.plan_run(led, [])
            else:
                fn = {"confirm": lambda ledger, k=ks: pr.confirm(ledger, k, at=AT),
                      "unconfirm": lambda ledger, k=ks: pr.unconfirm(ledger, k),
                      "reject": lambda ledger, k=ks: pr.reject(ledger, k, at=AT),
                      "restore": lambda ledger, k=ks: pr.restore(ledger, k)}[op]
                new, _ = fn(led)
                new = _fin(new, cp)
                twice, _ = fn(new)
                assert _fin(twice, cp) == new, (seed, op, ks)  # 同じ操作 2 回で同じ
            new = _fin(new, cp)
            if op in ("strip", "plan"):
                for k in confirmed:  # 確認済みは消えない
                    assert new["pairs"][k]["confirmed"], (seed, op, k)
            origin_rej = {
                (r["from"], r["rel"], r["to"])
                for p in new["pairs"].values() for r in p.get("rejected") or [] if r.get("origin")
            }
            for r in pr.shown_relations(new):
                trip = (r["from"], r["rel"], r["to"])
                trip = (*pr._order(r["rel"], trip[0], trip[2])[:1], trip[1], pr._order(r["rel"], trip[0], trip[2])[1])
                assert trip not in {(*pr._order(t[1], t[0], t[2])[:1], t[1], pr._order(t[1], t[0], t[2])[1]) for t in origin_rej}, (seed, op, ks, trip)
            led = new
