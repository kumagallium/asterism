# ruff: noqa: RUF001
"""知識で答える型の拡張（14a-2）— 作る・測る・変わる・競合する・物性を問いで引く。

例はすべて作り物（Mg2Si・Mg2Sn・Mg2Ge・アーク溶解・X 線回折・熱伝導率・ゼーベック係数）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import rdflib

from asterism import knowledge, sky_view, themes
from asterism import shared_vocab as sv
from asterism.crosswalk_runtime import assert_alignment
from asterism.themes import ThemeError

AT = "2026-11-18T00:00:00+00:00"
T = sv.term_iri
EVID = "https://example.org/paper/s1"


class _Client:
    def __init__(self) -> None:
        self.ds = rdflib.Dataset()

    async def sparql_select(self, query: str) -> dict:
        raw = self.ds.query(query).serialize(format="json")
        return json.loads(raw.decode() if isinstance(raw, bytes) else raw)

    async def sparql_update(self, update: str) -> None:
        self.ds.update(update)


async def _mint(c: _Client, root: Path, slug: str, kind: str, label: str) -> None:
    await sv.mint_term(c, root, slug=slug, kind=kind, label=label, cqs=[], theme_id="exp", at=AT)


async def _line(c: _Client, frm: str, rel: str, to: str, **kw: object) -> None:
    await assert_alignment(
        c, T(frm), T(to), rel, at=AT, drafted_by="ai", batch="b1", **kw  # type: ignore[arg-type]
    )


@pytest.fixture
async def world(tmp_path: Path) -> _Client:
    c = _Client()
    for slug, kind, label in [
        ("mg2si", "class", "Mg2Si"),
        ("mg2sn", "class", "Mg2Sn"),
        ("mg2ge", "class", "Mg2Ge"),
        ("mg2si_pellet", "class", "Mg2Si 焼結体"),
        ("arc_melting", "class", "アーク溶解"),
        ("xrd", "class", "X 線回折"),
        ("thermal_conductivity", "property", "熱伝導率"),
        ("seebeck", "property", "ゼーベック係数"),
        ("band_gap", "property", "バンドギャップ"),
    ]:
        await _mint(c, tmp_path, slug, kind, label)
    await _line(c, "arc_melting", "makes", "mg2si", reason="溶かして作る", evidence=[EVID])
    await _line(c, "arc_melting", "makes", "mg2sn")
    await _line(c, "xrd", "measuresMaterial", "mg2si")
    await _line(c, "xrd", "measuresProperty", "thermal_conductivity")
    await _line(c, "mg2si", "becomes", "mg2si_pellet")
    # 競合は向きなし: ストアには IRI 順に置かれる。置く向きは両方試す
    await _line(c, "mg2si", "competesWith", "mg2sn")
    await _line(c, "mg2ge", "competesWith", "mg2si")
    await _line(c, "mg2si", "hasProperty", "thermal_conductivity")
    await _line(c, "mg2sn", "hasProperty", "thermal_conductivity")
    await _line(c, "mg2sn", "hasProperty", "seebeck")
    await _line(c, "mg2ge", "hasProperty", "thermal_conductivity")
    await _line(c, "mg2ge", "hasProperty", "seebeck")
    await _line(c, "mg2ge", "hasProperty", "band_gap")  # 持つのは 1 件だけ
    return c


async def _ask(c: _Client, op: str, slug: str) -> list[dict]:
    out = await knowledge.knowledge_answer(c, {"op": op, "target": T(slug)})
    assert out["count"] == len(out["items"]) and out["op"] == op and out["direction"] is None
    return out["items"]


def _pairs(items: list[dict]) -> list[tuple[str, str]]:
    return [(r["label"], r["relation"]) for r in items]


def test_ops_are_the_same_in_themes_and_knowledge() -> None:
    assert knowledge.OPS == themes.KNOWLEDGE_OPS
    assert knowledge.OPS[:3] == ("factors", "parts", "kinds")
    assert len(knowledge.OPS) == 13
    assert set(knowledge.LINE_OPS) | {"missing"} == set(knowledge.OPS[3:])


async def test_makers_and_products(world: _Client) -> None:
    rows = await _ask(world, "makers", "mg2si")
    assert _pairs(rows) == [("アーク溶解", "makes")]
    r = rows[0]
    assert r["iri"] == T("arc_melting") and r["via"] == T("mg2si") and r["via_label"] == "Mg2Si"
    assert r["reason"] == "溶かして作る" and r["drafted_by"] == "ai" and r["at"] == AT
    assert r["batch"] == "b1" and r["evidence"] == [EVID]
    prods = await _ask(world, "products", "arc_melting")
    assert [(p["label"], p["via_label"]) for p in prods] == [
        ("Mg2Si", "アーク溶解"),
        ("Mg2Sn", "アーク溶解"),
    ]
    assert prods[0]["reason"] == "溶かして作る"


async def test_measurers_and_measured_use_both_relations(world: _Client) -> None:
    assert _pairs(await _ask(world, "measurers", "mg2si")) == [("X 線回折", "measuresMaterial")]
    assert _pairs(await _ask(world, "measurers", "thermal_conductivity")) == [
        ("X 線回折", "measuresProperty")
    ]
    assert sorted(_pairs(await _ask(world, "measured", "xrd"))) == [
        ("Mg2Si", "measuresMaterial"),
        ("熱伝導率", "measuresProperty"),
    ]


async def test_sources_and_outcomes(world: _Client) -> None:
    assert _pairs(await _ask(world, "outcomes", "mg2si")) == [("Mg2Si 焼結体", "becomes")]
    assert _pairs(await _ask(world, "sources", "mg2si_pellet")) == [("Mg2Si", "becomes")]
    assert await _ask(world, "sources", "mg2si") == []


async def test_competitors_return_the_other_end_whichever_way_placed(world: _Client) -> None:
    rows = await _ask(world, "competitors", "mg2si")
    assert [(r["label"], r["via_label"]) for r in rows] == [("Mg2Ge", "Mg2Si"), ("Mg2Sn", "Mg2Si")]
    assert all(r["iri"] != T("mg2si") and r["via"] == T("mg2si") for r in rows)
    assert _pairs(await _ask(world, "competitors", "mg2sn")) == [("Mg2Si", "competesWith")]
    assert _pairs(await _ask(world, "competitors", "mg2ge")) == [("Mg2Si", "competesWith")]


async def test_properties_and_bearers(world: _Client) -> None:
    props = await _ask(world, "properties", "mg2sn")
    assert [r["label"] for r in props] == ["ゼーベック係数", "熱伝導率"]
    assert all(r["relation"] == "hasProperty" and r["via"] == T("mg2sn") for r in props)
    bearers = await _ask(world, "bearers", "thermal_conductivity")
    assert [r["label"] for r in bearers] == ["Mg2Ge", "Mg2Si", "Mg2Sn"]
    assert all(r["via_label"] == "熱伝導率" for r in bearers)


async def test_missing(world: _Client) -> None:
    rows = await _ask(world, "missing", "mg2si")
    # ゼーベック係数: ほか 2 件が持つ → 出る。バンドギャップ: 1 件だけ → 出ない。
    # 熱伝導率: Mg2Si 自身が持つ → 出ない
    assert [r["label"] for r in rows] == ["ゼーベック係数"]
    r = rows[0]
    assert r["iri"] == T("seebeck") and r["relation"] == "hasProperty"
    assert r["via"] == T("mg2si") and r["via_label"] == "Mg2Si"
    assert r["reason"] is None and r["drafted_by"] is None and r["at"] is None
    assert r["batch"] is None and r["evidence"] == []
    assert r["others_count"] == 2
    assert [o["label"] for o in r["others"]] == ["Mg2Ge", "Mg2Sn"]
    assert r["others"][0]["iri"] == T("mg2ge")


async def test_missing_do_not_count_the_target_itself(world: _Client) -> None:
    # Mg2Sn にとって、ゼーベック係数は自分が持つ・熱伝導率も持つ。バンドギャップは他 1 件だけ
    assert await _ask(world, "missing", "mg2sn") == []
    # 3 つ目の語が 2 件目の持ち主になると、バンドギャップも出る（Mg2Ge・Mg2Sn が持つ）
    await _line(world, "mg2sn", "hasProperty", "band_gap")
    rows = await _ask(world, "missing", "mg2si")
    assert [r["label"] for r in rows] == ["ゼーベック係数", "バンドギャップ"]
    assert all(r["others_count"] == 2 for r in rows)


async def test_missing_cap_others_and_sort_by_count(tmp_path: Path) -> None:
    c = _Client()
    await _mint(c, tmp_path, "x", "class", "対象")
    await _mint(c, tmp_path, "p1", "property", "あ項目")
    await _mint(c, tmp_path, "p2", "property", "い項目")
    for i in range(7):
        await _mint(c, tmp_path, f"m{i}", "class", f"材料{i}")
        await _line(c, f"m{i}", "hasProperty", "p2")
        if i < 2:
            await _line(c, f"m{i}", "hasProperty", "p1")
    rows = await _ask(c, "missing", "x")
    assert [r["label"] for r in rows] == ["い項目", "あ項目"]  # 件数の多い順
    assert rows[0]["others_count"] == 7 and len(rows[0]["others"]) == 5
    assert [o["label"] for o in rows[0]["others"]] == [f"材料{i}" for i in range(5)]


async def test_draft_lines_are_not_read(tmp_path: Path) -> None:
    c = _Client()  # 確定した線は無関係な 1 本だけ
    await _mint(c, tmp_path, "a", "class", "あ")
    await _mint(c, tmp_path, "b", "class", "い")
    await _line(c, "a", "isPartOf", "b")
    for op in knowledge.OPS[3:]:
        out = await knowledge.knowledge_answer(c, {"op": op, "target": T("mg2si")})
        assert out["items"] == []


async def test_existing_three_ops_are_unchanged(tmp_path: Path) -> None:
    c = _Client()
    for slug, kind, label in [
        ("tc", "property", "熱伝導率"),
        ("lattice", "property", "格子熱伝導率"),
        ("defect", "class", "点欠陥"),
        ("fine", "property", "微細化"),
    ]:
        await _mint(c, tmp_path, slug, kind, label)
    await _line(c, "lattice", "isPartOf", "tc")
    await _line(c, "defect", "lowers", "lattice")
    await _line(c, "fine", "subPropertyOf", "tc")
    spec = {"op": "factors", "target": T("tc"), "direction": "lowers"}
    f = await knowledge.knowledge_answer(c, spec)
    assert [(r["label"], r["via_label"]) for r in f["items"]] == [("点欠陥", "格子熱伝導率")]
    assert f["direction"] == "lowers"
    assert _pairs(await _ask(c, "parts", "tc")) == [("格子熱伝導率", "isPartOf")]
    assert _pairs(await _ask(c, "kinds", "tc")) == [("微細化", "subPropertyOf")]


# --- 検査・文・名前 ------------------------------------------------------------------------


def test_target_kind_check_only_for_the_new_ops() -> None:
    ok = themes.knowledge_target_kind_ok
    assert ok("makers", "class") and not ok("makers", "property")
    assert ok("bearers", "property") and not ok("bearers", "class")
    assert ok("measurers", "class") and ok("measurers", "property")
    assert not ok("missing", "property") and not ok("properties", "property")
    for op in ("factors", "parts", "kinds"):
        assert ok(op, "class") and ok(op, "property")


def test_knowledge_tool_name_has_no_direction_for_new_ops() -> None:
    assert themes.knowledge_tool_name("mg2si", "makers") == "kq_mg2si_makers"
    assert themes.knowledge_tool_name("mg2si", "missing", None) == (
        "kq_mg2si_missing"
    )
    assert themes.knowledge_tool_name("tc", "factors", "lowers") == "kq_tc_factors_lowers"


@pytest.mark.parametrize(
    ("op", "ja", "en"),
    [
        ("makers", "Mg2Si を作る手法", "Methods that make Mg2Si"),
        ("products", "Mg2Si で作るもの", "What Mg2Si makes"),
        ("measurers", "Mg2Si を測る手法", "Methods that measure Mg2Si"),
        ("measured", "Mg2Si で測るもの", "What Mg2Si measures"),
        ("sources", "Mg2Si に変わるもの", "What turns into Mg2Si"),
        ("outcomes", "Mg2Si が変わる先", "What Mg2Si turns into"),
        ("competitors", "Mg2Si と競合するもの", "What competes with Mg2Si"),
        ("properties", "Mg2Si の物性", "Properties of Mg2Si"),
        ("bearers", "Mg2Si を持つ材料", "Materials that have Mg2Si"),
        (
            "missing",
            "Mg2Si でまだ無い物性",
            "Properties not yet recorded for Mg2Si",
        ),
    ],
)
def test_describe_selection_sentences(op: str, ja: str, en: str) -> None:
    sel = {"op": op, "target": "Mg2Si"}
    assert sv.describe_selection(sel, "ja") == ja
    assert sv.describe_selection(sel, "en") == en
    assert sv.describe_selection({"op": op}, "ja") == ""


def _c(key: str, ja: str, kind: str, slug: str, role: str | None = None) -> dict:
    d = {"key": key, "label_ja": ja, "label_en": slug, "aliases": [], "kind": kind,
         "use": "new", "slug": slug, "by": "human"}  # fmt: skip
    if role:
        d["role"] = role
    return d


def _body(op: str, target: str, q: str = "Mg2Si を作る手法は？") -> dict:
    return {
        "label": "熱電材料の実験", "lang": "ja", "ai_drafted": False, "questions": [q],
        "concepts": [
            _c("a", "アーク溶解", "class", "arc_melting", "method"),
            _c("m", "Mg2Si", "class", "mg2si", "material"),
            _c("p", "熱伝導率", "property", "thermal_conductivity"),
        ],
        "relations": [
            {"from": "a", "rel": "makes", "to": "m", "reason": ""},
            {"from": "m", "rel": "has_property", "to": "p", "reason": ""},
        ],
        "answers": [{"question": q, "mode": "knowledge", "op": op, "target": target}],
    }  # fmt: skip


async def test_commit_theme_places_a_new_type_question(tmp_path: Path) -> None:
    c = _Client()
    out = await themes.commit_theme(c, tmp_path, _body("makers", "m"), at=AT)
    assert out["lines"] == 2
    saved = json.dumps(out, ensure_ascii=False)
    for f in tmp_path.rglob("themes.json"):
        saved += f.read_text("utf-8")
    assert "kq_mg2si_makers" in saved
    rows = await _ask(c, "makers", "mg2si")
    assert [r["label"] for r in rows] == ["アーク溶解"]
    # 「知識 N」の数は走者の count（sky_view の数え方のまま）
    n = await sky_view._knowledge_count(c, {"op": "makers"}, T("mg2si"), {})
    assert n == 1
    assert await sky_view._knowledge_count(c, {"op": "missing"}, T("mg2si"), {}) == 0


async def test_commit_theme_refuses_a_wrong_target_kind(tmp_path: Path) -> None:
    with pytest.raises(ThemeError, match=r"対象は種類にしてください（「〜を作る手法」は種類を"):
        await themes.commit_theme(_Client(), tmp_path, _body("makers", "p"), at=AT)
    with pytest.raises(ThemeError, match=r"対象は項目にしてください（「〜を持つ材料」は項目を"):
        await themes.commit_theme(_Client(), tmp_path / "y", _body("bearers", "m"), at=AT)
    # どちらでもの型は両方通る
    await themes.commit_theme(_Client(), tmp_path / "z", _body("measurers", "p"), at=AT)


def test_item_sentence_follows_the_line_direction_and_skips_the_old_types() -> None:
    row = {"label": "X 線回折", "relation": "measuresProperty", "via_label": "格子定数"}
    assert knowledge.item_sentence("measurers", row, "ja") == "X 線回折 で 格子定数 を測る"
    swapped = {**row, "label": "格子定数", "via_label": "X 線回折"}
    assert knowledge.item_sentence("measured", swapped, "ja") == "X 線回折 で 格子定数 を測る"
    mg = {"label": "Mg", "relation": "becomes", "via_label": "Mg2Si"}
    assert knowledge.item_sentence("sources", mg, "en") == "Mg turns into Mg2Si"
    comp = {"label": "Mg2Sn", "relation": "competesWith", "via_label": "Mg2Si"}
    assert knowledge.item_sentence("competitors", comp, "ja") == "Mg2Si と Mg2Sn は競合する"
    low = {"label": "a", "relation": "lowers", "via_label": "b"}
    assert knowledge.item_sentence("factors", low) is None
    assert knowledge.item_sentence("parts", {**low, "relation": "isPartOf"}) is None


async def test_missing_names_the_target_even_when_it_has_no_property_line(
    world: _Client, tmp_path: Path
) -> None:
    await _mint(world, tmp_path, "mg2x", "class", "Mg2X")  # hasProperty を 1 本も持たない
    rows = await _ask(world, "missing", "mg2x")
    assert rows and all(r["via_label"] == "Mg2X" for r in rows)  # IRI の末尾でなく表示名


def test_no_op_ends_with_another_op_as_an_underscore_suffix() -> None:
    """``kq_<slug>_<op>`` が、別の（slug, op）の組と同じ名前にならないための不変条件。"""
    for a in knowledge.OPS:
        for b in knowledge.OPS:
            assert a == b or not a.endswith("_" + b), (a, b)


def test_postprocess_draft_picks_the_target_of_the_kind_the_new_op_needs() -> None:
    def concept(key: str, kind: str) -> dict:
        return {"key": key, "label_ja": "Mg2Si", "label_en": "Mg2Si", "aliases": [],
                "kind": kind, "use": "new", "reason": ""}  # fmt: skip

    def answer(op: str) -> dict:
        return {"question": "Q", "mode": "knowledge", "op": op, "target": "Mg2Si",
                "direction": None, "kind": None, "property": None, "wanted_data": "",
                "reason": ""}  # fmt: skip

    for op, want in (("bearers", "property"), ("makers", "class"), ("parts", "class")):
        ai = {"theme_label": "t", "concepts": [concept("c1", "class"), concept("c2", "property")],
              "relations": [], "answers": [answer(op)], "suggested_questions": []}  # fmt: skip
        out = themes.postprocess_draft(ai, questions=["Q"], existing=[])
        (a,) = out["answers"]
        kinds = {c["key"]: c["kind"] for c in out["concepts"]}
        assert a["memo"] is None and kinds[a["target"]] == want, op
