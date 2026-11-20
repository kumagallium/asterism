# ruff: noqa: RUF001
"""R1 — 「言えない」の件に、関係の種類（unstated_kind）を台帳へ残す。例は作り物。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from asterism import pair_relations as pr
from asterism.knowledge_bundle import KnowledgeCopy

# (入力, 期待)。None は捨てる。step0 と ingest で同じ表（見張りは step0 のテスト）
KIND_TABLE = [
    ("条件", "条件"), ("  条件  ", "条件"), ("条　件", "条 件"), ("条件\n比較", "条件 比較"),
    ("ＺＴ値", "ZT値"), ("🔥条件", "🔥条件"), ("a\x1bb", "ab"), ("条\x00件\x07", "条件"),
    ("条​件", "条件"), ("‮条件", "条件"), ("‮تحليل‬", "تحليل"),
    ("あ" * 20, "あ" * 20), ("あ" * 21, None), ("a" * 100, None), ("あ" * 19 + "👨‍👩‍👧", None),
    ("a" * 19 + "🇯🇵", None), ("🔥🔥", None), ("​​", None), ("\x00", None),
    ("---", None), ("123", None), ("①②", None), ("²", None), ("?!", None),
    ("none", None), ("None", None), ("NULL", None), ("n/a", None), ("N/A", None), ("na", None),
    ("unknown", None), ("nothing", None), ("no relation", None), ("  No  Relation ", None),
    ("なし", None), ("無し", None), ("不明", None), ("該当なし", None), ("関係なし", None),
    ("分からない", None), ("わからない", None), ("ＮＯＮＥ", None), ("ｎ／ａ", None),
    ("makes", None), ("Lowers", None), ("has_property", None), ("sub", None),
    ("c1", None), ("C12", None), ("c1, c2", None), ("c1,c2,c3", None), ("a", None), ("B", None),
    ("<b>条件</b>", None), ("{a}", None), ("`code`", None), ("select x", None), ("where", None),
    ("https://x.test/a", None), ("urn:a:b", None), ("javascript:alert", None),
    ("data:text/html,abc", None), ("mailto:a@b.c", None), ("file:///etc/passwd", None),
    ("ftp://x", None), ("//evil.com", None), ("www.evil.com", None), ("WWW.evil.com", None),
    ("[条件](https://x.y)", None), ("[条件](javascript:alert)", None), ("[a](b)", None),
    ("条件]", None), ("a//b", None),
    (5, None), (None, None), (["条件"], None), ({"x": 1}, None), ("", None),
]

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


@pytest.mark.parametrize(("raw", "want"), KIND_TABLE)
def test_the_rule_table(raw, want):
    assert pr._unstated_kind(raw) == want
    e = pr.check_answer(TASK, {**NONE_KIND, "unstated_kind": raw}, TERMS)
    assert e.get("unstated_kind") == want


def test_最後の砦_手で直した台帳の悪い種類は欄を無いものとして読む(tmp_path: Path):
    led = _ledger(NONE_KIND)
    pr.save_ledger(tmp_path, led, at=AT)
    path = pr.ledger_path(tmp_path, "s")
    for bad in ({"x": 1}, "あ" * 500, "条\x00件\x1b" + "あ" * 30, "none", 5, "https://x.test"):
        data = json.loads(path.read_text("utf-8"))
        data["pairs"][KEY]["unstated_kind"] = bad
        path.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
        e = pr.load_ledger(tmp_path, "s")["pairs"][KEY]
        assert e["state"] == "none" and "unstated_kind" not in e  # 件は今までどおり
    data["pairs"][KEY]["unstated_kind"] = "条\u200b件"  # 取り除けば通る物は整えて残す
    path.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    assert pr.load_ledger(tmp_path, "s")["pairs"][KEY]["unstated_kind"] == "条件"
    data["pairs"][KEY].update(state="relation", unstated_kind="条件")  # none 以外の件には付けない
    path.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    assert "unstated_kind" not in pr.load_ledger(tmp_path, "s")["pairs"][KEY]


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
