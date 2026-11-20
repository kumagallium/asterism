"""R1 — 「言えない」の件に、関係の種類（unstated_kind）を台帳へ残す。例は作り物。"""

from __future__ import annotations

import json
from pathlib import Path

from asterism import pair_relations as pr
from asterism.knowledge_bundle import KnowledgeCopy

AT = "2026-11-20T00:00:00+00:00"


def _t(tid, name, kind="class", role=None):
    return {"id": tid, "name": name, "kind": kind, "role": role, "iri": f"https://k.test/t/{tid}"}


def _c(cid, title):
    return {"id": cid, "iri": f"https://k.test/c/{cid}", "title": title, "source_url": None,
            "review_state": "candidate", "epistemic_status": None, "modal_qualifier": None}


COPY = KnowledgeCopy(
    terms=[_t("si", "Mg2Si", role="material"), _t("kappa", "熱伝導率", kind="property")],
    claims=[_c("c1", "Mg2Si の熱伝導率を条件ごとに比べた")],
    term_claims=[{"term_id": t, "claim_id": "c1", "by": "ai"} for t in ("si", "kappa")],
)
TERMS = {t["id"]: t for t in COPY.terms}
TASK = pr.build_tasks(COPY)[0]
KEY = TASK["key"]


def _ledger(answer):
    led = pr.empty_ledger("s")
    pr.merge_entry(led, pr.check_answer(TASK, answer, TERMS), model="m", at=AT)
    return led


NONE_KIND = {"rel": "none", "from": None, "evidence": [], "note": "n", "unstated_kind": "条件"}


def test_none_の件に種類が残る_無ければ欄なし():
    assert pr.check_answer(TASK, NONE_KIND, TERMS)["unstated_kind"] == "条件"
    plain = pr.check_answer(TASK, {**NONE_KIND, "unstated_kind": ""}, TERMS)
    assert plain["state"] == "none" and "unstated_kind" not in plain
    assert "unstated_kind" not in pr.check_answer(TASK, {"rel": "none"}, TERMS)


def test_関係の件と落ちた件には付かない():
    ans = {"rel": "has_property", "from": "a", "evidence": ["c1"], "unstated_kind": "条件"}
    for a in (ans, {**ans, "rel": "x"}):
        assert "unstated_kind" not in pr.check_answer(TASK, a, TERMS)


def test_形の悪い種類は捨てる():
    for bad in (5, ["条件"], "---", "42", "http://x.test/a", "urn:a:b"):
        e = pr.check_answer(TASK, {**NONE_KIND, "unstated_kind": bad}, TERMS)
        assert "unstated_kind" not in e
    long = pr.check_answer(TASK, {**NONE_KIND, "unstated_kind": "あ" * 50}, TERMS)
    assert long["unstated_kind"] == "あ" * 20


def test_指紋は指示の文面を含まない_入力と知見だけ():
    assert TASK["fingerprint"] == pr._fingerprint(TASK["input"], TASK["claim_ids"])
    assert set(TASK["input"]) == {"a", "b", "claims"}
    # 既存の台帳の指紋（R1 より前に作ったもの）と同じ値のまま（固定）
    inp = {"a": {"name": "Mg2Si", "kind": "class", "role": "material"},
           "b": {"name": "熱伝導率", "kind": "property", "role": None}, "claims": ["x"]}
    raw = json.dumps({"input": inp, "claim_ids": ["c1"]}, sort_keys=True, ensure_ascii=False)
    import hashlib

    assert pr._fingerprint(inp, ["c1"]) == hashlib.sha1(raw.encode()).hexdigest()[:16]


def test_finalize_と_保存_読み込み_と_同じ組の再取り込みで消えない(tmp_path: Path):
    led = _ledger(NONE_KIND)
    fin = pr.finalize(led, COPY, dataset_id="d")
    assert fin["pairs"][KEY]["unstated_kind"] == "条件"
    pr.save_ledger(tmp_path, fin, at=AT)
    again = pr.load_ledger(tmp_path, "s")
    assert again["pairs"][KEY]["unstated_kind"] == "条件"
    todo, base = pr.plan_run(again, pr.build_tasks(COPY))
    assert todo == []  # 組が同じなら聞き直さない
    assert pr.finalize(base, COPY, dataset_id="d")["pairs"][KEY]["unstated_kind"] == "条件"


def test_指紋が変わって聞き直すと新しい答えで置き換わる():
    led = _ledger(NONE_KIND)
    pr.merge_entry(led, pr.check_answer(TASK, {"rel": "none"}, TERMS), model="m", at=AT)
    assert "unstated_kind" not in led["pairs"][KEY]


def test_既存の台帳の件_欄なし_はそのまま読める():
    led = _ledger({"rel": "none"})
    fin = pr.finalize(led, COPY, dataset_id="d")
    assert fin["pairs"][KEY]["state"] == "none" and "unstated_kind" not in fin["pairs"][KEY]


def test_確認_違う_人が付けた関係の件には付かない():
    out, res = pr.assign(_ledger(NONE_KIND), COPY.terms, KEY, "has_property", "b", at=AT)
    assert res["changed"] == [KEY]
    e = out["pairs"][KEY]
    assert e["state"] == "relation" and "unstated_kind" not in e
    back, _ = pr.unconfirm(out, [KEY])  # 人の関係は候補に戻る。種類は復活させない
    assert back["pairs"][KEY]["state"] == "none" and "unstated_kind" not in back["pairs"][KEY]
    rej, _ = pr.reject(out, [KEY], at=AT)
    assert "unstated_kind" not in rej["pairs"][KEY]


def test_外すと_言えない_の件ごと消える_今のまま():
    stripped, _ = pr.strip_unconfirmed(_ledger(NONE_KIND))
    assert KEY not in stripped["pairs"]
