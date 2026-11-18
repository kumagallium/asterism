# ruff: noqa: RUF001
"""``asterism.term_match``（語の照合）のテスト。例はすべて作り物。"""

from __future__ import annotations

import time

import pytest

from asterism.term_match import (
    NAME_MAX,
    fold,
    match_terms,
    mentions,
    merge_key,
    name_spans,
    name_variants,
    prep_text,
    term_key,
    usable_name,
)


def test_fold_と_prep_text():
    assert fold("Bread  Dough") == "Bread Dough"
    assert prep_text("ＢＲＥＡＤ-Flour") == "bread flour"
    assert prep_text("thermo-\nelectric") == "thermoelectric"


def test_term_key_は単複と全角半角とハイフンを揃える():
    assert term_key("Loaves") == term_key("loaves")
    assert term_key("Bread Doughs") == term_key("bread dough")
    assert term_key("ＰｂＴｅ") == term_key("PbTe")
    assert term_key("sour-dough") == term_key("sour dough")
    assert term_key("series") == "series"  # 単複同形は触らない


def test_mentions_英語の単複():
    t = prep_text("The doughs were rested for an hour.")
    assert mentions(t, "dough")
    assert mentions(t, "Doughs")
    assert not mentions(t, "doug")  # 語の途中には当たらない


def test_mentions_全角半角とハイフン():
    t = prep_text("Ｂｉ２Ｔｅ３ is a layered compound; sour-dough starter.")
    assert mentions(t, "Bi2Te3")
    assert mentions(t, "sour dough")
    assert mentions(t, "sourdough starter") is False


def test_mentions_短いラテン名は使わない():
    t = prep_text("The ZT value and the pH were measured.")
    assert not mentions(t, "ZT")
    assert not mentions(t, "pH")
    assert not mentions(t, "")


def test_mentions_日本語は部分一致():
    t = prep_text("格子熱伝導率が下がった。")
    assert mentions(t, "熱伝導率")
    assert mentions(t, "格子熱伝導率")
    assert not mentions(t, "電気伝導率")
    assert not usable_name("率")  # 1 文字の日本語は照合に使わない


def test_mentions_英数字の無い名前は空白を無視する():
    t = prep_text("温 度 が 上がる")
    assert mentions(t, "温度")
    spans = name_spans(t, "温度")
    assert spans == [(0, 3)]
    assert t[spans[0][0] : spans[0][1]] == "温 度"


@pytest.mark.parametrize(
    ("text", "name"),
    [
        ("the doughs rest", "dough"),
        ("bread-making", "bread making"),
        ("格子熱伝導率", "熱伝導率"),
        ("温 度", "温度"),
        ("温 度", "湿度"),
        ("PbTe and Bi2Te3", "Bi2Te3"),
        ("PbTe", "Bi2Te3"),
        ("an analysis of crises", "analyses"),
        ("zt", "ZT"),
        ("", "dough"),
    ],
)
def test_name_spans_と_mentions_の真偽が一致(text: str, name: str):
    t = prep_text(text)
    assert mentions(t, name) == bool(name_spans(t, name))


def test_name_spans_は添字を返す():
    t = prep_text("Rye bread and wheat bread")
    assert name_spans(t, "bread") == [(4, 9), (20, 25)]
    assert name_spans(t, "ZT") == []


def test_name_variants_括弧の外と中():
    assert name_variants("Bi2Te3") == ["Bi2Te3"]
    assert name_variants("熱伝導率 (kappa)") == ["熱伝導率 (kappa)", "熱伝導率", "kappa"]
    # 括弧の中が 3 文字に満たない修飾（κ）は別名にしない
    assert name_variants("熱伝導率（κ）") == ["熱伝導率（κ）", "熱伝導率"]
    # 外が空・同じ形は重複として落とす
    assert name_variants("(ABC)") == ["(ABC)", "ABC"]
    assert name_variants("Dough (doughs)") == ["Dough (doughs)", "Dough"]


def test_merge_key():
    assert merge_key("格子 熱伝導率") == merge_key("格子熱伝導率")
    assert merge_key("Bread (Loaf)") == merge_key("bread loaf")
    assert merge_key("熱電・材料") == "熱電材料"
    assert merge_key("[x]（y）") == "xy"
    assert merge_key("Doughs") == merge_key("dough")
    assert merge_key("()") == ""


def test_usable_name():
    assert usable_name("熱伝導率")
    assert usable_name("熱電")
    assert not usable_name("熱")
    assert usable_name("PbTe")
    assert not usable_name("ZT")
    assert not usable_name("Z T")  # 空白は数えない
    assert usable_name("αβ")  # U+0250 以上を含む（ギリシャ文字）は 2 文字から
    assert not usable_name("  ")


def test_match_terms_長い当たりが勝つ():
    names = {"t1": ["熱伝導率"], "t2": ["格子熱伝導率"]}
    texts = {
        "a": "格子熱伝導率が下がった",  # 熱伝導率は格子熱伝導率の中だけ → t2 だけ
        "b": "格子熱伝導率と熱伝導率を測った",  # 別の場所にもある → 両方
        "c": "熱伝導率だけ",
        "d": "何も無い",
    }
    assert match_terms(names, texts) == {"a": ["t2"], "b": ["t1", "t2"], "c": ["t1"]}


def test_match_terms_同じ位置同じ長さは両方():
    names = {"t2": ["bread"], "t1": ["Breads"], "t3": ["flour"]}
    out = match_terms(names, {"a": "Fresh bread and flour"})
    assert out == {"a": ["t1", "t2", "t3"]}


def test_match_terms_重なる当たりは長い方():
    # 「熱伝導」と「伝導率」は重なるが、長い「熱伝導率」が先に採られる
    names = {"a": ["熱伝導"], "b": ["伝導率"], "c": ["熱伝導率"]}
    assert match_terms(names, {"x": "熱伝導率"}) == {"x": ["c"]}


def test_match_terms_使えない名前と空の名前():
    names = {"t1": ["ZT", ""], "t2": []}
    assert match_terms(names, {"a": "ZT zt"}) == {}


def test_match_terms_変形の名前():
    names = {"t": [*name_variants("熱伝導率 (kappa)")]}
    assert match_terms(names, {"a": "kappa を測る", "b": "別の話"}) == {"a": ["t"]}


def test_match_terms_大きさ():
    names = {f"t{i:03d}": [f"term{i:03d}x", f"語{i:03d}の名"] for i in range(500)}
    texts = {
        f"c{j}": f"文{j} に term{j % 500:03d}x と 語{(j * 7) % 500:03d}の名が出る"
        for j in range(2000)
    }
    start = time.perf_counter()
    out = match_terms(names, texts)
    assert time.perf_counter() - start < 30
    assert out["c0"] == ["t000"]
    assert len(out) == 2000


def test_短い修飾や数字だけの括弧は別名にしない():
    # 「Si(100)」の 100 や Si、「(n型)」の n型 は、どの文の「100」「n型」にも当たってしまう
    assert name_variants("Si(100)") == ["Si(100)"]
    assert name_variants("熱電材料 (n型)") == ["熱電材料 (n型)", "熱電材料"]
    dft = name_variants("密度汎関数理論 (DFT)")
    assert dft == ["密度汎関数理論 (DFT)", "密度汎関数理論", "DFT"]
    hits = match_terms(
        {"t1": name_variants("Si(100)"), "t2": name_variants("熱電材料 (n型)")},
        {"c1": "heated to 100 K", "c2": "n型の試料を測った", "c3": "熱電材料の試料"},
    )
    assert hits == {"c3": ["t2"]}


def test_文字の無い名前と長すぎる名前は使わない():
    assert not usable_name("100")
    assert not usable_name("...")
    assert not usable_name("——")
    assert usable_name("X線回折")
    assert usable_name("a" * NAME_MAX) and not usable_name("a" * (NAME_MAX + 1))
    hits = match_terms({"t1": ["...", "2024"]}, {"c1": "2024 年の結果は ... だった"})
    assert hits == {}


def test_日本語だけの名前が多くても速い():
    """英数字の無い名前は、空白を無視して比べる分岐を通る。文ごとに空白なしの文を作り直さない。"""
    names = {f"t{i}": [f"生地の状態{i}番"] for i in range(600)}
    texts = {f"c{i}": f"発酵のあと 生地の状態{i % 600}番 を確かめた " * 3 for i in range(2000)}
    t0 = time.perf_counter()
    hits = match_terms(names, texts)
    assert time.perf_counter() - t0 < 8
    assert hits["c7"] == ["t7"]
