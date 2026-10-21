"""「問いを提案してもらう」の後処理（ui-redesign PR 10a）— AI が返した問いを機械が絞る。

確かめること: 閉じた形（7 つのデータの形・3 つの知識の形）に落ちて、その形に要る部分が揃うものだけ
残る／名前（種類・項目・対象）が表の列か既にある用語に実在するものだけ残り、表示名に直る／
``filter`` の値は問いの文にある語だけ／既存の問い・同じ答え方・2 本目の「何件」は出ない／最大 5 本。
"""

# ruff: noqa: RUF001
from __future__ import annotations

from asterism.mapping_ir_read import read_mapping_ir
from asterism.question_suggest import MAX_SUGGESTIONS, dataset_names, postprocess_questions
from asterism.shared_vocab import SharedTerm

PRICES_IR = """\
version: 1
prefixes:
  prices: https://asterism.invalid/datasets/prices/ontology#
  pricesr: https://asterism.invalid/datasets/prices/resource/
maps:
- name: record
  source: prices.csv
  subject:
    template: pricesr:record/{no}
    classes: [prices:Record]
    label: 値段の記録
  properties:
  - {predicate: prices:no, column: 'no', label: 行の通し番号}
  - {predicate: prices:price, column: price, datatype: 'xsd:integer', label: 1 つあたりの値段}
  - {predicate: prices:shop, column: shop, label: 店の名前}
  - {predicate: prices:date, column: date, label: 値段を調べた日}
  - {predicate: prices:hasFood, object_template: 'pricesr:food/{food}', label: 食材の名前}
- name: food
  source: prices.csv
  subject:
    template: pricesr:food/{food}
    classes: [prices:Food]
    label: 食材の名前
  properties:
  - {predicate: prices:food, column: food, label: 食材の名前}
"""


def _names():
    return dataset_names(read_mapping_ir(PRICES_IR))


def _q(question: str, mode: str, op: str, **kw: object) -> dict:
    base = {"question": question, "mode": mode, "op": op, "kind": None, "property": None,
            "property2": None, "kind2": None, "value": None, "match": None, "target": None,
            "direction": None, "reason": "x"}  # fmt: skip
    return {**base, **kw}


def _run(questions: list[dict], **kw) -> list[dict]:
    kinds, props = _names()
    return postprocess_questions(
        {"questions": questions}, kinds=kinds, properties=props, **kw
    )


def _term(slug: str, label: str, kind: str, en: str = "") -> SharedTerm:
    return SharedTerm(
        iri=f"https://asterism.invalid/vocab/{slug}", slug=slug, kind=kind, label=label,
        label_en=en or None, comment=None, created_at="2026-10-21T00:00:00+00:00",
    )  # fmt: skip


GOOD = [
    _q("いちばん高い値段の記録は？", "data", "top", kind="値段の記録", property="price"),
    _q("食材ごとに記録は何件ある？", "data", "group", kind="record", property="食材の名前"),
    _q("店の名前に「北」を含む記録は？", "data", "filter", kind="値段の記録",
       property="shop", value="北", match="contains"),
    _q("食材は何種類ある？", "data", "count", kind="食材の名前"),
]  # fmt: skip


def test_the_names_of_the_table_are_headings_and_labels_only() -> None:
    kinds, props = _names()
    assert [k for k, _ in kinds] == ["値段の記録", "食材の名前"]
    shown = [p for p, _ in props]
    assert "1 つあたりの値段" in shown and "店の名前" in shown
    price = dict(props)["1 つあたりの値段"]
    assert "price" in price  # the heading is an alias


def test_good_questions_fall_into_the_closed_shapes_with_the_machine_sentence() -> None:
    out = _run(GOOD)
    assert [(o["op"], o["shape"]) for o in out] == [
        ("top", "値段の記録のうち、1 つあたりの値段がいちばん大きいものを出します（データセットごと）"),  # noqa: E501
        ("group", "値段の記録を 食材の名前 ごとに数える"),
        ("filter", "値段の記録のうち、店の名前に 北 を含むもの"),
        ("count", "食材の名前の件数を数えます（データセットごと）"),
    ]
    # a heading or a map name comes back as the shown name
    assert out[0]["property"] == "1 つあたりの値段" and out[1]["kind"] == "値段の記録"
    assert out[2]["value"] == "北" and out[2]["match"] == "contains"
    assert all(o["mode"] == "data" for o in out)
    assert set(out[0]) == {"question", "mode", "op", "shape", "kind", "property", "property2",
                           "kind2", "value", "match", "target", "direction"}  # fmt: skip


def test_a_name_that_is_not_in_the_table_drops_the_question() -> None:
    out = _run([
        _q("粘度の最大は？", "data", "top", kind="値段の記録", property="粘度"),
        _q("温度ごとの件数は？", "data", "group", kind="値段の記録", property="温度"),
        _q("棚ごとの件数は？", "data", "group", kind="棚", property="店の名前"),
    ])  # fmt: skip
    assert out == []


def test_an_unused_field_is_cleared_not_judged() -> None:
    odd = _q("食材は何種類ある？", "data", "count", kind="食材の名前", property="存在しない")
    out = _run([odd])
    assert len(out) == 1 and out[0]["property"] == ""


def test_an_incomplete_shape_is_dropped() -> None:
    out = _run([
        _q("店ごと？", "data", "group", kind="値段の記録"),  # no property
        _q("推移は？", "data", "trend", kind="値段の記録", property="date"),  # no property2
        _q("結合は？", "data", "join", kind="値段の記録", property="食材の名前"),  # no kind2
        _q("北を含む？", "data", "filter", kind="値段の記録", property="shop"),  # no value
        _q("件数は？", "data", "count"),  # no kind
    ])  # fmt: skip
    assert out == []


def test_trend_and_join_with_both_ends() -> None:
    out = _run([
        _q("日付ごとの値段の並びは？", "data", "trend", kind="値段の記録", property="date",
           property2="price"),
        _q("値段の記録が指す食材は？", "data", "join", kind="値段の記録", property="食材の名前",
           kind2="食材の名前"),
    ])  # fmt: skip
    assert [o["op"] for o in out] == ["trend", "join"]
    assert out[0]["property2"] == "1 つあたりの値段" and out[1]["kind2"] == "食材の名前"


def test_a_filter_value_must_be_written_in_the_question() -> None:
    wrong = _q("店の名前に「北」を含む記録は？", "data", "filter", kind="値段の記録",
               property="shop", value="東京店", match="contains")  # fmt: skip
    assert _run([wrong]) == []
    # full-width / case differences still count as written in the question
    ok = _q("店の名前に ＡＢＣ を含む記録は？", "data", "filter", kind="値段の記録",
            property="shop", value="abc")  # fmt: skip
    got = _run([ok])
    assert len(got) == 1 and got[0]["match"] == "contains" and got[0]["value"] == "abc"


def test_questions_already_asked_and_repeats_are_not_offered() -> None:
    out = _run([*GOOD[:2], GOOD[0]], existing_questions=["食材ごとに記録は何件ある?"])
    assert [o["op"] for o in out] == ["top"]  # the 2nd is already asked, the 3rd repeats the 1st


def test_the_same_way_of_answering_is_offered_once() -> None:
    same = _q("高い値段のものは？", "data", "top", kind="record", property="price")
    out = _run([GOOD[0], same])
    assert len(out) == 1


def test_only_one_how_many_question() -> None:
    out = _run([
        _q("食材は何種類ある？", "data", "count", kind="食材の名前"),
        _q("記録は何件ある？", "data", "count", kind="値段の記録"),
    ])  # fmt: skip
    assert [o["question"] for o in out] == ["食材は何種類ある？"]


def test_at_most_five() -> None:
    props = ["shop", "date", "price", "food", "no", "店の名前"]
    qs = [
        _q(f"{p} ごとの件数は？ ({i})", "data", "group", kind="値段の記録", property=p)
        for i, p in enumerate(props)
    ]
    # the last one is the same shape as the first (shop = 店の名前): five different ones come first
    assert len(_run(qs)) <= MAX_SUGGESTIONS
    many = [
        _q(f"{i} 番目の問い 北", "data", "filter", kind="値段の記録", property="shop", value=str(i))
        for i in range(8)
    ]
    assert len(_run(many)) == MAX_SUGGESTIONS


def test_a_knowledge_question_needs_an_existing_term_and_a_direction() -> None:
    terms = [_term("price", "値段", "property", "price"), _term("food", "食材", "class", "food")]
    out = _run(
        [
            _q("値段を下げるものは？", "knowledge", "factors", target="値段", direction="lowers"),
            _q("値段の一部は？", "knowledge", "parts", target="price"),
            _q("食材の一種は？", "knowledge", "kinds", target="食材"),
            _q("湿度を上げるものは？", "knowledge", "factors", target="湿度", direction="raises"),
            _q("値段に効くものは？", "knowledge", "factors", target="値段"),  # no direction
            _q("値段の何か？", "knowledge", "count", target="値段"),  # not a knowledge op
        ],
        terms=terms,
    )
    assert [(o["op"], o["target"], o["direction"]) for o in out] == [
        ("factors", "値段", "lowers"),
        ("parts", "値段", ""),
        ("kinds", "食材", ""),
    ]
    assert out[0]["shape"] == "値段 を下げるもの" and out[0]["mode"] == "knowledge"
    # without terms there is nothing to know about
    assert _run([_q("値段を下げるものは？", "knowledge", "factors", target="値段",
                    direction="lowers")]) == []  # fmt: skip


def test_the_mode_and_op_must_agree_and_be_closed() -> None:
    out = _run([
        _q("a", "data", "factors", kind="値段の記録"),
        _q("b", "knowledge", "top", property="price"),
        _q("c", "memo", "count", kind="値段の記録"),
        "not a dict",
    ])  # type: ignore[list-item]  # fmt: skip
    assert out == []


def test_existing_terms_also_name_kinds_and_properties() -> None:
    terms = [_term("meal", "料理", "class"), _term("kcal", "熱量", "property")]
    out = _run([_q("料理ごとの熱量の最大は？", "data", "range", property="熱量", kind="料理")],
               terms=terms)  # fmt: skip
    assert len(out) == 1 and out[0]["kind"] == "料理" and out[0]["property"] == "熱量"


def test_english_shapes_use_the_english_names() -> None:
    kinds, props = _names()
    terms = [_term("meal", "料理", "class", "meal")]
    out = postprocess_questions(
        {"questions": [_q("How many meals?", "data", "count", kind="meal")]},
        kinds=kinds, properties=props, terms=terms, lang="en",
    )  # fmt: skip
    assert out[0]["kind"] == "meal" and out[0]["shape"] == "Counts the meal (per dataset)."
