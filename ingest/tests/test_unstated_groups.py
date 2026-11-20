# ruff: noqa: RUF001
"""R2 — 「言えなかった関係の種類」を束ねる。例は作り物。"""

from __future__ import annotations

import copy as _copy

from asterism import relation_catalog
from asterism.knowledge_bundle import KnowledgeCopy
from asterism.unstated_groups import unstated_groups


def _t(tid, name):
    return {"id": tid, "name": name, "kind": "class", "role": None, "iri": f"https://k.test/t/{tid}"}


COPY = KnowledgeCopy(terms=[_t(f"t{i}", f"語{i}") for i in range(8)], claims=[], term_claims=[])


def _e(a, b, kind=None, state="none", note="理由", **kw):
    e = {"key": f"{a}|{b}", "a": a, "b": b, "state": state, "note": note}
    if kind is not None:
        e["unstated_kind"] = kind
    e.update(kw)
    return e


def _ledger(source, *entries):
    return {"source": source, "pairs": {e["key"]: e for e in entries}}


def _run(*ledgers, **kw):
    return unstated_groups([(led["source"], led, COPY) for led in ledgers], **kw)


def test_表記ゆれが_1_つに束なり_label_は最多の書き方():
    led = _ledger(
        "s",
        _e("t0", "t1", "Condition"), _e("t0", "t2", "conditions"), _e("t0", "t3", "conditions"),
        _e("t1", "t2", "比較"),
    )
    groups = _run(led)
    assert [g["label"] for g in groups] == ["conditions", "比較"]
    g = groups[0]
    assert g["count"] == 3 and g["sources"] == {"s": 3}
    assert g["variants"] == [
        {"label": "conditions", "count": 2}, {"label": "Condition", "count": 1},
    ]
    assert groups[1]["variants"] == [{"label": "比較", "count": 1}]


def test_同数の書き方は文字の順():
    groups = _run(_ledger("s", _e("t0", "t1", "Cond"), _e("t0", "t2", "cond")))
    assert groups[0]["label"] == "Cond" and groups[0]["count"] == 2


def test_空白_括弧_中黒を無視して束ねる():
    led = _ledger("s", _e("t0", "t1", "併用 条件"), _e("t0", "t2", "併用条件"),
                  _e("t0", "t3", "併用（条件）"))
    assert len(_run(led)) == 1


def test_確認_違う_人が付けた_欄なし_取り残しは入らない():
    led = _ledger(
        "s",
        _e("t0", "t1", "条件", state="relation"),
        _e("t0", "t2", "条件", state="rejected"),
        _e("t0", "t3"),
        _e("t0", "t4", "条件", orphan=True),
        _e("t0", "t5", "none"),  # 規則に通らない
        _e("t0", "t6", "条件"),
    )
    groups = _run(led)
    assert len(groups) == 1 and groups[0]["count"] == 1


def test_鍵が空になる物と写しに語の無い組は捨てる():
    led = _ledger("s", _e("t0", "t1", "（）"), _e("t0", "zz", "条件"))
    assert _run(led) == []


def test_取り込み元をまたぐ_sources_と_examples_の上限と並び():
    a = _ledger("b-src", *[_e("t0", f"t{i}", "条件") for i in (1, 2)])
    b = _ledger("a-src", *[_e("t3", f"t{i}", "条件") for i in (4, 5, 6)])
    g = _run(a, b, examples=3)[0]
    assert g["count"] == 5 and g["sources"] == {"a-src": 3, "b-src": 2}
    assert [(x["source"], x["pair"]) for x in g["examples"]] == [
        ("a-src", "t3|t4"), ("a-src", "t3|t5"), ("a-src", "t3|t6"),
    ]
    ex = g["examples"][0]
    assert ex["a"] == {"id": "t3", "name": "語3"} and ex["b"]["name"] == "語4"
    assert ex["note"] == "理由"
    assert len(_run(a, b)[0]["examples"]) == 5
    assert _run(a, b, examples=0)[0]["examples"] == []


def test_並びは_count_の多い順_そして_label():
    led = _ledger("s", _e("t0", "t1", "比較"), _e("t0", "t2", "条件"), _e("t0", "t3", "目的"),
                  _e("t0", "t4", "目的"))
    assert [g["label"] for g in _run(led)] == ["目的", "条件", "比較"]


def test_目録にある関係と同じ鍵には_in_catalog():
    rel = relation_catalog.RELATIONS[0]
    led = _ledger("s", _e("t0", "t1", rel.label["ja"]), _e("t0", "t2", rel.label["en"].upper()),
                  _e("t0", "t3", "条件"))
    groups = {g["label"]: g for g in _run(led)}
    assert all(g.get("in_catalog") == rel.name for k, g in groups.items() if k != "条件")
    assert "in_catalog" not in groups["条件"]


def test_決定論で台帳を書き換えない():
    led = _ledger("s", _e("t0", "t1", "条件"), _e("t0", "t2", "Condition"))
    before = _copy.deepcopy(led)
    assert _run(led) == _run(led) and led == before
    assert _run(led, lang="en") == _run(led, lang="ja")
