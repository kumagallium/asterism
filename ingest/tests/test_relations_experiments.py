"""実験の記録の関係 5 文（作る・測る・変わる・競合する・物性）と語の役割の札（14a-1・design §23）。

store は実物の ``rdflib.Dataset``（``test_relations_nine`` と同じ立て方）。語・材料は作り物
（Mg2Si・アーク溶解・X 線回折・熱伝導率）。
"""

# ruff: noqa: RUF001
from __future__ import annotations

import json
from pathlib import Path

import pytest
import rdflib

from asterism import graph_diff as gd
from asterism import shared_vocab as sv
from asterism import themes
from asterism.crosswalk import XW
from asterism.crosswalk_runtime import (
    ALIGN_RELATIONS,
    ALIGNMENT_GRAPH,
    KNOWLEDGE_RELATIONS,
    NO_SELF_RELATIONS,
    REL,
    RELATION_SHAPES,
    ROLE_RULES,
    ROLES,
    SYMMETRIC_RELATIONS,
    add_evidence,
    assert_alignment,
    canonical_ends,
    check_roles,
    list_alignments,
    remove_alignment,
)
from asterism.themes import ThemeError

AT = "2026-11-17T00:00:00+00:00"
OBO = "http://purl.obolibrary.org/obo/"
SOSA = "http://www.w3.org/ns/sosa/"
T = sv.term_iri
EVID = "https://example.org/paper/s1"
EVID2 = "https://example.org/paper/s2"

NEW_SIX = (
    "makes",
    "measuresProperty",
    "measuresMaterial",
    "becomes",
    "competesWith",
    "hasProperty",
)


class _Client:
    def __init__(self) -> None:
        self.ds = rdflib.Dataset()

    async def sparql_select(self, query: str) -> dict:
        raw = self.ds.query(query).serialize(format="json")
        return json.loads(raw.decode() if isinstance(raw, bytes) else raw)

    async def sparql_update(self, update: str) -> None:
        self.ds.update(update)

    def triples(self, graph: str) -> set[tuple[str, str, str]]:
        g = self.ds.graph(rdflib.URIRef(graph))
        return {(str(s), str(p), str(o)) for s, p, o in g}


async def _mint(
    c: _Client, root: Path, slug: str, kind: str, label: str, role: str | None = None
) -> None:
    await sv.mint_term(
        c, root, slug=slug, kind=kind, label=label, cqs=[], theme_id="exp", at=AT, role=role
    )


@pytest.fixture
async def world(tmp_path: Path) -> tuple[_Client, Path]:
    """材料 2（Mg2Si・Mg2Si 焼結体）・手法 2（アーク溶解・X 線回折）・概念 1・札なし 1・項目 1。"""
    c = _Client()
    for slug, kind, label, role in [
        ("mg2si", "class", "Mg2Si", "material"),
        ("mg2si_pellet", "class", "Mg2Si 焼結体", "material"),
        ("arc_melting", "class", "アーク溶解", "method"),
        ("xrd", "class", "X 線回折", "method"),
        ("thermoelectric", "class", "熱電変換", "concept"),
        ("plain", "class", "札なしの種類", None),
        ("thermal_conductivity", "property", "熱伝導率", None),
    ]:
        await _mint(c, tmp_path, slug, kind, label, role)
    return c, tmp_path


# ---------------------------------------------------------------------------
# 15 の関係・述語・種別表
# ---------------------------------------------------------------------------


def test_fifteen_relations_with_standard_iris_and_one_own_predicate() -> None:
    assert len(ALIGN_RELATIONS) == 15
    assert ALIGN_RELATIONS["makes"] == OBO + "RO_0002234"
    assert ALIGN_RELATIONS["measuresProperty"] == OBO + "RO_0009006"
    assert ALIGN_RELATIONS["measuresMaterial"] == SOSA + "hasFeatureOfInterest"
    assert ALIGN_RELATIONS["becomes"] == OBO + "RO_0001001"
    assert ALIGN_RELATIONS["hasProperty"] == OBO + "RO_0000053"
    assert ALIGN_RELATIONS["competesWith"] == REL + "competesWith"
    assert REL == "https://kumagallium.github.io/asterism/vocab/relation#"
    own = [k for k, v in ALIGN_RELATIONS.items() if v.startswith(REL)]
    assert own == ["competesWith"]  # 自前は 1 つだけ
    assert not any(v.startswith(sv.SV) for v in ALIGN_RELATIONS.values())
    assert set(RELATION_SHAPES) == set(ALIGN_RELATIONS)
    assert set(NEW_SIX) <= set(KNOWLEDGE_RELATIONS) and len(KNOWLEDGE_RELATIONS) == 10
    assert {"competesWith"} == SYMMETRIC_RELATIONS
    assert set(ROLE_RULES) == set(NEW_SIX) == set(NO_SELF_RELATIONS)


def test_shape_table_of_the_new_six() -> None:
    cls, prop = frozenset({"class"}), frozenset({"property"})
    assert RELATION_SHAPES["makes"] == (cls, cls)
    assert RELATION_SHAPES["measuresProperty"] == (cls, prop)
    assert RELATION_SHAPES["measuresMaterial"] == (cls, cls)
    assert RELATION_SHAPES["becomes"] == (cls, cls)
    assert RELATION_SHAPES["competesWith"] == (cls, cls)
    assert RELATION_SHAPES["hasProperty"] == (cls, prop)


def test_canonical_ends_orders_only_the_symmetric_relation() -> None:
    assert canonical_ends("competesWith", T("b"), T("a")) == (T("a"), T("b"))
    assert canonical_ends("competesWith", T("a"), T("b")) == (T("a"), T("b"))
    assert canonical_ends("becomes", T("b"), T("a")) == (T("b"), T("a"))
    assert canonical_ends("lowers", T("b"), T("a")) == (T("b"), T("a"))


def test_check_roles_is_per_end_and_ignores_missing_labels() -> None:
    check_roles("makes", "method", "material")
    check_roles("makes", None, None)
    check_roles("measuresProperty", "concept", "material")  # to は「—」
    check_roles("lowers", "material", "other")  # 既存の関係は検査しない
    with pytest.raises(ValueError, match=r"role mismatch: makes cannot join a material \(from\)"):
        check_roles("makes", "material", "material")
    with pytest.raises(ValueError, match=r"role mismatch: becomes cannot join a method \(to\)"):
        check_roles("becomes", "material", "method")
    assert ROLES == ("material", "method", "concept", "other")


# ---------------------------------------------------------------------------
# assert_alignment
# ---------------------------------------------------------------------------


async def test_the_six_can_be_placed(world: tuple[_Client, Path]) -> None:
    c, _ = world
    kw = {"at": AT}
    r = await assert_alignment(c, T("arc_melting"), T("mg2si"), "makes", **kw)
    assert r["relation_iri"] == OBO + "RO_0002234"
    await assert_alignment(c, T("xrd"), T("thermal_conductivity"), "measuresProperty", **kw)
    await assert_alignment(c, T("xrd"), T("mg2si"), "measuresMaterial", **kw)
    await assert_alignment(c, T("mg2si"), T("mg2si_pellet"), "becomes", **kw)
    await assert_alignment(c, T("mg2si"), T("mg2si_pellet"), "competesWith", **kw)
    await assert_alignment(c, T("mg2si"), T("thermal_conductivity"), "hasProperty", **kw)
    got = {(a["relation"]) for a in await list_alignments(c)}
    assert got == set(NEW_SIX)


async def test_kind_mismatch_comes_from_the_table(world: tuple[_Client, Path]) -> None:
    c, _ = world
    kw = {"at": AT}
    with pytest.raises(ValueError, match="kind mismatch"):
        await assert_alignment(c, T("arc_melting"), T("thermal_conductivity"), "makes", **kw)
    with pytest.raises(ValueError, match="kind mismatch"):
        await assert_alignment(c, T("xrd"), T("mg2si"), "measuresProperty", **kw)
    with pytest.raises(ValueError, match="kind mismatch"):
        await assert_alignment(c, T("mg2si"), T("mg2si_pellet"), "hasProperty", **kw)
    with pytest.raises(ValueError, match="kind mismatch"):
        await assert_alignment(c, T("thermal_conductivity"), T("mg2si"), "becomes", **kw)
    assert c.triples(ALIGNMENT_GRAPH) == set()


async def test_role_mismatch_is_refused_per_end(world: tuple[_Client, Path]) -> None:
    c, _ = world
    kw = {"at": AT}
    cases = [
        (T("mg2si"), T("mg2si_pellet"), "makes"),  # from が材料
        (T("arc_melting"), T("xrd"), "makes"),  # to が手法
        (T("mg2si"), T("thermal_conductivity"), "measuresProperty"),  # from が材料
        (T("xrd"), T("arc_melting"), "measuresMaterial"),  # to が手法
        (T("arc_melting"), T("mg2si"), "becomes"),
        (T("mg2si"), T("xrd"), "becomes"),
        (T("thermoelectric"), T("mg2si"), "competesWith"),
        (T("arc_melting"), T("thermal_conductivity"), "hasProperty"),
    ]
    for s, t, rel in cases:
        with pytest.raises(ValueError, match=f"role mismatch: {rel} cannot join"):
            await assert_alignment(c, s, t, rel, **kw)
    assert c.triples(ALIGNMENT_GRAPH) == set()


async def test_an_end_without_a_label_passes(world: tuple[_Client, Path]) -> None:
    c, _ = world
    await assert_alignment(c, T("plain"), T("mg2si"), "makes", at=AT)  # from に札なし
    await assert_alignment(c, T("arc_melting"), T("plain"), "makes", at=AT)  # to に札なし
    await assert_alignment(c, T("plain"), T("mg2si"), "becomes", at=AT)


async def test_self_relation_is_refused(world: tuple[_Client, Path]) -> None:
    c, _ = world
    for rel in ("makes", "measuresMaterial", "becomes", "competesWith"):
        with pytest.raises(ValueError, match="self relation"):
            await assert_alignment(c, T("mg2si"), T("mg2si"), rel, at=AT)
    with pytest.raises(ValueError, match="self relation"):
        await assert_alignment(
            c, T("mg2si"), T("mg2si"), "hasProperty", at=AT
        )


async def test_competes_with_is_one_line_whichever_way_it_is_placed(
    world: tuple[_Client, Path],
) -> None:
    c, _ = world
    a, b = T("mg2si"), T("mg2si_pellet")
    r1 = await assert_alignment(c, a, b, "competesWith", at=AT, reason="どちらも候補")
    r2 = await assert_alignment(c, b, a, "competesWith", at=AT)
    lo, hi = sorted([a, b])
    assert (r1["source"], r1["target"]) == (lo, hi) == (r2["source"], r2["target"])
    assert r1["alignment_iri"] == r2["alignment_iri"]
    g = c.triples(ALIGNMENT_GRAPH)
    assert {x for x in g if x[1] == ALIGN_RELATIONS["competesWith"]} == {
        (lo, ALIGN_RELATIONS["competesWith"], hi)
    }
    nodes = {s for (s, p, o) in g if p == str(rdflib.RDF.type) and o == XW + "Alignment"}
    assert len(nodes) == 1
    assert len(await list_alignments(c)) == 1


async def test_competes_with_can_be_withdrawn_from_either_side(
    world: tuple[_Client, Path],
) -> None:
    c, _ = world
    a, b = T("mg2si"), T("mg2si_pellet")
    for first, second in ((a, b), (b, a)):
        await assert_alignment(c, first, second, "competesWith", at=AT)
        await remove_alignment(c, second, first, "competesWith")
        assert c.triples(ALIGNMENT_GRAPH) == set()


async def test_competes_with_evidence_is_one_node_from_either_side(
    world: tuple[_Client, Path],
) -> None:
    c, _ = world
    a, b = T("mg2si"), T("mg2si_pellet")
    await assert_alignment(c, a, b, "competesWith", at=AT, evidence=[EVID])
    assert await add_evidence(c, b, a, "competesWith", [EVID, EVID2]) == 1  # EVID は既にある
    rows = await list_alignments(c)
    assert len(rows) == 1 and rows[0]["evidence"] == [EVID, EVID2]


async def test_becomes_may_go_both_ways(world: tuple[_Client, Path]) -> None:
    c, _ = world
    a, b = T("mg2si"), T("mg2si_pellet")
    await assert_alignment(c, a, b, "becomes", at=AT)
    await assert_alignment(c, b, a, "becomes", at=AT)  # 循環を許す
    assert len(await list_alignments(c)) == 2
    with pytest.raises(ValueError, match="self relation"):
        await assert_alignment(c, a, a, "becomes", at=AT)


async def test_the_old_nine_do_not_look_at_roles(world: tuple[_Client, Path]) -> None:
    c, _ = world
    # 材料 → 項目の「下げる」も、手法 → 項目の「上げる」も置ける
    await assert_alignment(c, T("mg2si"), T("thermal_conductivity"), "lowers", at=AT)
    await assert_alignment(c, T("arc_melting"), T("thermal_conductivity"), "raises", at=AT)
    await assert_alignment(c, T("mg2si"), T("mg2si_pellet"), "isPartOf", at=AT)
    await assert_alignment(c, T("xrd"), T("mg2si"), "subClassOf", at=AT)


# ---------------------------------------------------------------------------
# 役割の札（mint_term・set_term_role・list_terms）
# ---------------------------------------------------------------------------


async def test_mint_term_keeps_the_role_and_who_put_it(world: tuple[_Client, Path]) -> None:
    c, root = world
    t = next(x for x in await sv.list_terms(c, root) if x["slug"] == "mg2si")
    assert t["role"] == "material" and t["role_by"] == "human"
    g = c.triples(sv.SHARED_VOCAB_GRAPH)
    assert (T("mg2si"), XW + "role", "material") in g
    assert (T("mg2si"), XW + "roleBy", "human") in g
    assert any(s == T("mg2si") and p == XW + "roleAt" for s, p, _o in g)
    plain = next(x for x in await sv.list_terms(c, root) if x["slug"] == "plain")
    assert plain["role"] is None and plain["role_by"] is None
    await sv.mint_term(
        c, root, slug="sintering", kind="class", label="焼結", cqs=[], theme_id="exp", at=AT,
        role="method", role_by="machine",
    )  # fmt: skip
    got = next(x for x in await sv._read_terms(c) if x.slug == "sintering")
    assert (got.role, got.role_by) == ("method", "machine")


async def test_mint_term_refuses_a_bad_role(tmp_path: Path) -> None:
    c = _Client()
    with pytest.raises(ValueError):
        await _mint(c, tmp_path, "x", "class", "甲", "weird")
    with pytest.raises(ValueError):
        await _mint(c, tmp_path, "y", "property", "乙", "material")  # 項目には付けない
    with pytest.raises(ValueError):
        await sv.mint_term(
            c, tmp_path, slug="z", kind="class", label="丙", cqs=[], theme_id="t", at=AT,
            role="material", role_by="robot",
        )  # fmt: skip
    assert c.triples(sv.SHARED_VOCAB_GRAPH) == set()


async def test_set_term_role_replaces_and_removes(world: tuple[_Client, Path]) -> None:
    c, root = world
    out = await sv.set_term_role(c, "plain", "method", at=AT, by="machine")
    assert out["role"] == "method" and out["role_by"] == "machine"
    out = await sv.set_term_role(c, "plain", "other", at=AT)
    assert out["role"] == "other" and out["role_by"] == "human"
    g = c.triples(sv.SHARED_VOCAB_GRAPH)
    assert len([x for x in g if x[0] == T("plain") and x[1] == XW + "role"]) == 1
    out = await sv.set_term_role(c, "plain", None, at=AT)
    assert out["role"] is None and out["role_by"] is None
    g = c.triples(sv.SHARED_VOCAB_GRAPH)
    assert not [x for x in g if x[0] == T("plain") and x[1].startswith(XW + "role")]
    with pytest.raises(sv.TermNotFound):
        await sv.set_term_role(c, "nothing", "material", at=AT)
    with pytest.raises(ValueError):
        await sv.set_term_role(c, "thermal_conductivity", "material", at=AT)  # 項目
    with pytest.raises(ValueError):
        await sv.set_term_role(c, "plain", "weird", at=AT)
    # 札を変えても、既にある線は調べ直さない
    await assert_alignment(c, T("arc_melting"), T("mg2si"), "makes", at=AT)
    await sv.set_term_role(c, "mg2si", "method", at=AT)
    assert len(await list_alignments(c)) == 1
    # 札のない語を消すとき、札も消える
    await sv.remove_term(c, root, "xrd")
    assert not [x for x in c.triples(sv.SHARED_VOCAB_GRAPH) if x[0] == T("xrd")]


async def test_a_changed_role_is_what_the_check_reads(world: tuple[_Client, Path]) -> None:
    c, _ = world
    await sv.set_term_role(c, "mg2si_pellet", "method", at=AT)
    with pytest.raises(ValueError, match="role mismatch"):
        await assert_alignment(c, T("arc_melting"), T("mg2si_pellet"), "makes", at=AT)
    await sv.set_term_role(c, "mg2si_pellet", None, at=AT)
    await assert_alignment(c, T("arc_melting"), T("mg2si_pellet"), "makes", at=AT)


def test_reserved_slugs_cover_the_new_relation_names() -> None:
    for slug in ("makes", "measures", "becomes", "competes", "competes_with", "has_property"):
        assert slug in sv.RESERVED_SLUGS


async def test_knowledge_counts_see_a_term_that_only_has_the_new_lines(
    world: tuple[_Client, Path],
) -> None:
    c, _ = world
    await assert_alignment(c, T("arc_melting"), T("mg2si"), "makes", at=AT)
    await assert_alignment(c, T("mg2si_pellet"), T("thermal_conductivity"), "hasProperty", at=AT)
    k = await sv.knowledge_counts(c)
    assert k[T("arc_melting")] == 1 and k[T("mg2si")] == 1
    assert k[T("mg2si_pellet")] == 1 and k[T("thermal_conductivity")] == 1
    assert T("xrd") not in k


# ---------------------------------------------------------------------------
# preview_alignment・却下
# ---------------------------------------------------------------------------


async def test_preview_alignment_says_role_and_same(world: tuple[_Client, Path]) -> None:
    c, root = world
    out = await sv.preview_alignment(c, root, T("mg2si"), T("mg2si_pellet"), "makes")
    assert out["valid"] is False and out["reason"] == "role"
    out = await sv.preview_alignment(c, root, T("arc_melting"), T("mg2si"), "makes")
    assert out["valid"] is True
    out = await sv.preview_alignment(c, root, T("mg2si"), T("mg2si"), "competesWith")
    assert out["reason"] == "same"
    out = await sv.preview_alignment(c, root, T("arc_melting"), T("thermal_conductivity"), "makes")
    assert out["reason"] == "mixed"
    # 向きの無い関係は、並べ替えてから見る
    await assert_alignment(c, T("mg2si"), T("mg2si_pellet"), "competesWith", at=AT)
    out = await sv.preview_alignment(
        c, root, T("mg2si_pellet"), T("mg2si"), "competesWith", remove=True
    )
    assert out["valid"] is True


def test_a_dismissal_of_a_symmetric_relation_ignores_the_order(tmp_path: Path) -> None:
    a, b = T("mg2si"), T("mg2si_pellet")
    assert sv.dismiss_fit(tmp_path, a, b, at=AT, relation="competesWith") is True
    assert sv.dismiss_fit(tmp_path, b, a, at=AT, relation="competesWith") is False  # 同じ却下
    rows = sv.load_dismissed(tmp_path)
    assert sv.is_dismissed(rows, b, a, relation="competesWith")
    assert not sv.is_dismissed(rows, b, a, relation="becomes")  # 向きのある関係は別
    assert sv.dismiss_fit(tmp_path, b, a, at=AT, relation="becomes") is True
    assert sv.undismiss_fit(tmp_path, b, a, relation="competesWith") is True
    assert sv.load_dismissed(tmp_path) == [
        {"subject": b, "term": a, "at": AT, "relation": "becomes"}
    ]


# ---------------------------------------------------------------------------
# themes: 述語・純関数・下書きの検査
# ---------------------------------------------------------------------------


def test_relation_predicate_for_the_new_five() -> None:
    p = themes.relation_predicate
    assert p("makes", "class", "class") == "makes"
    assert p("becomes", "class", "class") == "becomes"
    assert p("competes", "class", "class") == "competesWith"
    assert p("has_property", "class", "property") == "hasProperty"
    assert p("measures", "class", "property") == "measuresProperty"
    assert p("measures", "class", "class") == "measuresMaterial"
    assert p("measures", "class", "quantity") == "measuresMaterial"
    with pytest.raises(ThemeError):
        p("measures", "class")  # 相手の種別が無ければ決められない
    # 既存の 7 つは to_kind が無くても今までどおり
    assert p("sub", "class") == "subClassOf" and p("lowers", "class") == "lowers"
    assert p("part", "property") == "isPartOf" and p("quantity", "property") == "hasQuantityKind"
    assert set(themes.RELATION_KINDS) >= {"makes", "measures", "becomes", "competes"}
    assert len(themes.RELATION_KINDS) == 12


def test_experiment_relation_problem_is_a_pure_table() -> None:
    f = themes.experiment_relation_problem
    assert f("makes", "class", "class", "method", "material") is None
    assert f("makes", "class", "class", None, None) is None
    assert f("makes", "class", "property", None, None) == "mixed"
    assert f("makes", "class", "class", "material", "material") == "role_mismatch"
    assert f("measures", "class", "property", "method", None) is None
    assert f("measures", "class", "property", "material", None) == "role_mismatch"
    assert f("measures", "class", "class", "method", "method") == "role_mismatch"
    assert f("measures", "class", "quantity", None, None) == "mixed"
    assert f("measures", "property", "class", None, None) == "mixed"
    assert f("becomes", "class", "class", "material", "material") is None
    assert f("competes", "class", "class", "material", "concept") == "role_mismatch"
    assert f("has_property", "class", "property", "material", None) is None
    assert f("has_property", "class", "class", None, None) == "mixed"
    assert f("has_property", "class", "property", "method", None) == "role_mismatch"


def _t(key: str, ja: str, en: str, kind: str, use: str = "new") -> dict:
    return {
        "key": key, "label_ja": ja, "label_en": en, "aliases": [], "kind": kind, "use": use,
        "reason": "",
    }  # fmt: skip


def _ai(**over: object) -> dict:
    base: dict = {
        "theme_label": "", "concepts": [], "relations": [], "answers": [],
        "suggested_questions": [],
    }  # fmt: skip
    base.update(over)
    return base


def _r(frm: str, rel: str, to: str) -> dict:
    return {"from": frm, "rel": rel, "to": to, "reason": ""}


def _term(slug: str, kind: str, label: str, role: str | None = None) -> sv.SharedTerm:
    return sv.SharedTerm(T(slug), slug, kind, label, None, None, AT, role=role, role_by="human")


def test_postprocess_draft_marks_mixed_and_role_mismatch() -> None:
    existing = [
        _term("arc_melting", "class", "アーク溶解", "method"),
        _term("mg2si", "class", "Mg2Si", "material"),
    ]
    out = themes.postprocess_draft(
        _ai(
            concepts=[
                _t("a", "アーク溶解", "arc melting", "class"),
                _t("m", "Mg2Si", "Mg2Si", "class"),
                _t("n", "Mg2Si 焼結体", "Mg2Si pellet", "class"),
                _t("p", "熱伝導率", "thermal conductivity", "property"),
            ],
            relations=[
                _r("a", "makes", "m"),  # 通る
                _r("m", "makes", "n"),  # from が材料
                _r("a", "makes", "p"),  # 種別
                _r("a", "measures", "p"),  # 項目 → measuresProperty
                _r("a", "measures", "m"),  # 材料 → measuresMaterial
                _r("m", "has_property", "p"),
                _r("a", "has_property", "p"),  # from が手法
                _r("m", "becomes", "n"),  # n は札なし → 通る
                _r("m", "competes", "n"),
                _r("n", "competes", "m"),  # 向きを問わず 1 本 → 落ちる
                _r("a", "becomes", "a"),
            ],
        ),
        questions=[],
        existing=existing,
    )
    got = [(r["from"], r["rel"], r["to"], r["problem"]) for r in out["relations"]]
    assert got == [
        ("a", "makes", "m", None),
        ("m", "makes", "n", "role_mismatch"),
        ("a", "makes", "p", "mixed"),
        ("a", "measures", "p", None),
        ("a", "measures", "m", None),
        ("m", "has_property", "p", None),
        ("a", "has_property", "p", "role_mismatch"),
        ("m", "becomes", "n", None),
        ("m", "competes", "n", None),
        ("a", "becomes", "a", "same"),
    ]


def test_postprocess_draft_dismissal_of_competes_ignores_the_order() -> None:
    dismissed = [{"subject": T("mg2si"), "term": T("mg2si_pellet"), "at": AT,
                  "relation": "competesWith"}]  # fmt: skip
    existing = [_term("mg2si", "class", "Mg2Si", "material"),
                _term("mg2si_pellet", "class", "Mg2Si 焼結体", "material")]  # fmt: skip
    out = themes.postprocess_draft(
        _ai(
            concepts=[
                _t("a", "Mg2Si", "Mg2Si", "class"),
                _t("b", "Mg2Si 焼結体", "x", "class"),
            ],
            relations=[_r("b", "competes", "a")],
        ),
        questions=[],
        existing=existing,
        dismissed=dismissed,
    )
    assert out["relations"] == []
    assert out["dropped"]["relations"] == [{"from": "b", "rel": "competes", "to": "a"}]


def _draft_rows(**roles: str | None) -> dict:
    def row(key: str, ja: str, kind: str, slug: str, role: str | None = None, use: str = "new"):
        d = {"key": key, "label_ja": ja, "label_en": "", "aliases": [], "kind": kind, "use": use,
             "slug": slug, "match": None, "by": "human"}  # fmt: skip
        if role:
            d["role"] = role
        return d

    return {
        "concepts": [
            row("a", "アーク溶解", "class", "arc_melting", roles.get("a")),
            row("m", "Mg2Si", "class", "mg2si", roles.get("m")),
            row("p", "熱伝導率", "property", "thermal_conductivity"),
        ],
        "relations": [],
        "answers": [],
        "questions": [],
        "standards": {},
    }


def _op(rel: str, frm: str, to: str) -> dict:
    return {"op": "add_relation", "from": frm, "rel": rel, "to": to, "reason": ""}


def test_apply_to_draft_drops_a_role_mismatch_and_keeps_the_rest() -> None:
    draft = _draft_rows(a="method", m="material")
    new, report = gd.apply_to_draft(
        draft,
        [_op("makes", "a", "m"), _op("makes", "m", "a"), _op("measures", "a", "p"),
         _op("competes", "m", "m")],
        existing=[],
    )  # fmt: skip
    assert [r["rel"] for r in new["relations"]] == ["makes", "measures"]
    assert [(d["reason"], d["ref"]) for d in report["dropped"]] == [
        ("role_mismatch", "m -makes-> a"),
        ("same", "m -competes-> m"),
    ]
    # competes は向きを問わず 1 本
    draft2 = _draft_rows(m="material")
    draft2["concepts"].append(
        {"key": "n", "label_ja": "Mg2Si 焼結体", "label_en": "", "aliases": [], "kind": "class",
         "use": "new", "slug": "pellet", "match": None, "by": "human", "role": "material"}
    )  # fmt: skip
    new2, report2 = gd.apply_to_draft(
        draft2, [_op("competes", "m", "n"), _op("competes", "n", "m")], existing=[]
    )
    assert len(new2["relations"]) == 1
    assert [d["reason"] for d in report2["dropped"]] == ["duplicate"]


def test_apply_to_draft_reads_the_stores_role_for_an_existing_term() -> None:
    draft = _draft_rows(a="material")  # 行の role は無視される（use が existing のとき）
    draft["concepts"][0]["use"] = "existing:arc_melting"
    existing = [_term("arc_melting", "class", "アーク溶解", "method")]
    _, report = gd.apply_to_draft(draft, [_op("makes", "a", "m")], existing=existing)
    assert report["dropped"] == []


def test_apply_to_draft_add_concept_takes_a_valid_role_only() -> None:
    def add(kind: str, role: object) -> dict:
        op = {"op": "add_concept", "key": "z", "label_ja": "新しい語", "label_en": "new term",
              "kind": kind, "role": role}  # fmt: skip
        new, _ = gd.apply_to_draft(_draft_rows(), [op], existing=[])
        return next(r for r in new["concepts"] if r["key"] == "z")

    assert add("class", "method")["role"] == "method"
    assert "role" not in add("class", "weird")
    assert "role" not in add("property", "material")


def test_sentences_and_pred_to_rel_for_the_new_relations() -> None:
    assert gd.PRED_TO_REL["measuresProperty"] == "measures"
    assert gd.PRED_TO_REL["measuresMaterial"] == "measures"
    assert gd.PRED_TO_REL["competesWith"] == "competes"
    assert gd.PRED_TO_REL["hasProperty"] == "has_property"
    graph = {"concepts": [{"key": "a", "label_ja": "アーク溶解", "label_en": "arc melting"},
                          {"key": "m", "label_ja": "Mg2Si", "label_en": "Mg2Si"}]}  # fmt: skip
    ops = [_op("makes", "a", "m"), _op("measures", "a", "m"), _op("becomes", "m", "a"),
           _op("competes", "m", "a"), _op("has_property", "m", "a")]  # fmt: skip
    assert gd.ops_to_sentences(ops, graph, "ja") == [
        "アーク溶解 で Mg2Si を作る",
        "アーク溶解 で Mg2Si を測る",
        "Mg2Si が アーク溶解 に変わる",
        "Mg2Si と アーク溶解 は競合する",
        "Mg2Si の アーク溶解",
    ]
    assert gd.ops_to_sentences(ops[:4], graph, "en") == [
        "arc melting makes Mg2Si",
        "arc melting measures Mg2Si",
        "Mg2Si turns into arc melting",
        "Mg2Si competes with arc melting",
    ]


def test_plan_term_relations_checks_roles_and_splits_measures() -> None:
    terms = [
        _term("arc_melting", "class", "アーク溶解", "method"),
        _term("mg2si", "class", "Mg2Si", "material"),
        _term("thermal_conductivity", "property", "熱伝導率"),
    ]
    ops = [
        _op("makes", "arc_melting", "mg2si"),
        _op("makes", "mg2si", "arc_melting"),
        _op("measures", "arc_melting", "thermal_conductivity"),
        _op("measures", "arc_melting", "mg2si"),
    ]
    plans, dropped = gd.plan_term_relations(ops, terms=terms)
    assert [p["relation"] for p in plans] == ["makes", "measuresProperty", "measuresMaterial"]
    assert [(d["index"], d["reason"]) for d in dropped] == [(1, "role_mismatch")]


def test_dismissal_keys_use_the_split_measures_predicate() -> None:
    draft = _draft_rows()
    keys = gd.dismissal_keys(draft, [_op("measures", "a", "p"), _op("measures", "a", "m")],
                             existing=[])  # fmt: skip
    assert [k["relation"] for k in keys] == ["measuresProperty", "measuresMaterial"]


# ---------------------------------------------------------------------------
# commit_theme
# ---------------------------------------------------------------------------


def _c(key: str, ja: str, kind: str, slug: str, role: str | None = None, **kw: object) -> dict:
    d = {"key": key, "label_ja": ja, "label_en": slug.replace("_", " "), "aliases": [],
         "kind": kind, "use": "new", "slug": slug, "by": "human", **kw}  # fmt: skip
    if role is not None:
        d["role"] = role
    return d


def _body(
    concepts: list[dict], relations: list[dict], target: str = "m", **over: object
) -> dict:
    """承認に要る「答え方の決まった問い」を 1 つ添える（対象は Mg2Si の行 ``m``・知識で答える）。"""
    q = "Mg2Si は何でできている？"
    body: dict = {
        "label": "熱電材料の実験", "lang": "ja", "ai_drafted": False, "questions": [q],
        "concepts": concepts, "relations": relations,
        "answers": [{"question": q, "mode": "knowledge", "op": "parts", "target": target}],
    }  # fmt: skip
    body.update(over)
    return body


def _exp_concepts() -> list[dict]:
    return [
        _c("a", "アーク溶解", "class", "arc_melting", "method"),
        _c("x", "X 線回折", "class", "xrd", "method"),
        _c("m", "Mg2Si", "class", "mg2si", "material"),
        _c("n", "Mg2Si 焼結体", "class", "pellet", "material"),
        _c("p", "熱伝導率", "property", "thermal_conductivity"),
    ]


async def test_commit_theme_places_the_five_sentences(tmp_path: Path) -> None:
    c = _Client()
    out = await themes.commit_theme(
        c,
        tmp_path,
        _body(
            _exp_concepts(),
            [
                _r("a", "makes", "m"),
                _r("x", "measures", "p"),  # 相手が項目
                _r("x", "measures", "m"),  # 相手が種類
                _r("m", "becomes", "n"),
                _r("m", "competes", "n"),
                _r("m", "has_property", "p"),
            ],
        ),
        at=AT,
    )
    assert out["lines"] == 6
    rels = {a["relation"] for a in await list_alignments(c)}
    assert rels == set(NEW_SIX)
    terms = {t.slug: t for t in await sv._read_terms(c)}
    assert terms["arc_melting"].role == "method" and terms["mg2si"].role == "material"
    assert terms["thermal_conductivity"].role is None


async def test_commit_theme_competes_both_ways_is_one_line(tmp_path: Path) -> None:
    c = _Client()
    out = await themes.commit_theme(
        c,
        tmp_path,
        _body(
            [_c("m", "Mg2Si", "class", "mg2si", "material"),
             _c("n", "Mg2Si 焼結体", "class", "pellet", "material")],
            [_r("m", "competes", "n"), _r("n", "competes", "m")],
        ),
        at=AT,
    )
    assert out["lines"] == 1
    assert len(await list_alignments(c)) == 1
    # 同じ並べ替え後の線は、もう一度確定しても足されない（既にある線）
    again = _body([_c("m2", "Mg2Si", "class", "mg2si", use="existing:mg2si"),
                   _c("n2", "Mg2Si 焼結体", "class", "pellet", use="existing:pellet")],
                  [_r("n2", "competes", "m2")], target="m2", label="続き")  # fmt: skip
    await themes.commit_theme(c, tmp_path, again, at=AT)
    assert len(await list_alignments(c)) == 1


async def test_commit_theme_refuses_a_role_mismatch_and_writes_nothing(tmp_path: Path) -> None:
    c = _Client()
    msg = r"役割の札が合わない関係があります（「Mg2Si」は材料です）"
    with pytest.raises(ThemeError, match=msg):
        await themes.commit_theme(
            c, tmp_path, _body(_exp_concepts(), [_r("m", "makes", "n")]), at=AT
        )
    assert c.triples(sv.SHARED_VOCAB_GRAPH) == set() and c.triples(ALIGNMENT_GRAPH) == set()
    with pytest.raises(ThemeError, match=r"「アーク溶解」は手法です"):
        await themes.commit_theme(
            c, tmp_path, _body(_exp_concepts(), [_r("m", "becomes", "a")]), at=AT
        )
    assert c.triples(ALIGNMENT_GRAPH) == set()


async def test_commit_theme_refuses_a_kind_mismatch_with_its_own_sentence(tmp_path: Path) -> None:
    c = _Client()
    with pytest.raises(ThemeError, match="この関係の両端は、種類と項目の組が合いません"):
        await themes.commit_theme(
            c, tmp_path, _body(_exp_concepts(), [_r("a", "makes", "p")]), at=AT
        )
    with pytest.raises(ThemeError, match="この関係の両端は、種類と項目の組が合いません"):
        await themes.commit_theme(
            c, tmp_path, _body(_exp_concepts(), [_r("m", "has_property", "n")]), at=AT
        )
    with pytest.raises(ThemeError, match="自分自身"):
        await themes.commit_theme(
            c, tmp_path, _body(_exp_concepts(), [_r("m", "competes", "m")]), at=AT
        )


async def test_commit_theme_reads_the_stores_role_for_an_existing_term(tmp_path: Path) -> None:
    c = _Client()
    first = [_r("a", "makes", "m"), _r("x", "measures", "m"), _r("m", "becomes", "n"),
             _r("m", "has_property", "p")]  # fmt: skip
    await themes.commit_theme(c, tmp_path, _body(_exp_concepts(), first), at=AT)
    # 行に role: material と書いても、既にある語（アーク溶解 = 手法）はストアの札が勝つ
    body = _body(
        [
            _c("a", "アーク溶解", "class", "arc_melting", "material", use="existing:arc_melting"),
            _c("n", "Mg2Si 焼結体", "class", "pellet", use="existing:pellet"),
        ],
        [_r("a", "makes", "n")],
        target="a",
        label="続き",
    )
    await themes.commit_theme(c, tmp_path, body, at=AT)
    assert sorted(a["relation"] for a in await list_alignments(c)).count("makes") == 2
    terms = {t.slug: t for t in await sv._read_terms(c)}
    assert terms["arc_melting"].role == "method"  # 書き換わらない
    # 逆に、行が手法と言っても、既にある材料の語はストアの札で断られる
    bad = _body(
        [
            _c("m", "Mg2Si", "class", "mg2si", "method", use="existing:mg2si"),
            _c("n", "Mg2Si 焼結体", "class", "pellet", use="existing:pellet"),
        ],
        [_r("m", "makes", "n")],
        target="m",
        label="続き 2",
    )
    with pytest.raises(ThemeError, match="材料です"):
        await themes.commit_theme(c, tmp_path, bad, at=AT)


async def test_commit_theme_checks_the_role_value_and_where_it_goes(tmp_path: Path) -> None:
    c = _Client()
    with pytest.raises(ThemeError, match="role"):
        await themes.commit_theme(
            c, tmp_path, _body([_c("m", "Mg2Si", "class", "mg2si", "weird")], []), at=AT
        )
    with pytest.raises(ThemeError, match="項目"):
        await themes.commit_theme(
            c, tmp_path, _body([_c("p", "熱伝導率", "property", "tc", "material")], []), at=AT
        )
    assert c.triples(sv.SHARED_VOCAB_GRAPH) == set()


# ---------------------------------------------------------------------------
# 知識の線（/api/sky の edges の元）
# ---------------------------------------------------------------------------


async def test_the_edge_relations_of_the_sky_include_the_new_ones() -> None:
    from asterism import sky_view

    assert set(NEW_SIX) <= set(sky_view._EDGE_RELATIONS)


# ---- 向きの無い関係の突き合わせ（下書きの鍵・逆向きの remove／retarget） -------------------------


def test_draft_rel_key_orders_only_the_symmetric_relation() -> None:
    assert themes.SYMMETRIC_DRAFT_RELATIONS == ("competes",)
    assert themes.draft_rel_key("zzz", "competes", "aaa") == ("aaa", "competes", "zzz")
    assert themes.draft_rel_key("aaa", "competes", "zzz") == ("aaa", "competes", "zzz")
    # それ以外はそのまま（eq も向きを変えない）
    assert themes.draft_rel_key("zzz", "makes", "aaa") == ("zzz", "makes", "aaa")
    assert themes.draft_rel_key("zzz", "eq", "aaa") == ("zzz", "eq", "aaa")


def _three_materials() -> dict:
    draft = _draft_rows(m="material")
    for key in ("n", "k"):
        draft["concepts"].append(
            {"key": key, "label_ja": f"Mg2{key}", "label_en": "", "aliases": [], "kind": "class",
             "use": "new", "slug": f"mg2{key}", "match": None, "by": "human",
             "role": "material"}
        )  # fmt: skip
    draft["relations"] = [
        {"from": "n", "rel": "competes", "to": "m", "reason": "", "problem": None, "by": "human"}
    ]
    return draft


def test_remove_finds_a_competes_row_placed_the_other_way() -> None:
    op = {"op": "remove", "target": "relation", "from": "m", "rel": "competes", "to": "n",
          "reason": ""}  # fmt: skip
    new, report = gd.apply_to_draft(_three_materials(), [op], existing=[])
    assert new["relations"] == [] and report["dropped"] == []


def test_retarget_finds_a_competes_row_placed_the_other_way_and_keeps_the_op_direction() -> None:
    op = {"op": "retarget", "from": "m", "rel": "competes", "to": "n", "new_to": "k",
          "reason": ""}  # fmt: skip
    new, report = gd.apply_to_draft(_three_materials(), [op], existing=[])
    assert report["dropped"] == []
    assert [(r["from"], r["rel"], r["to"]) for r in new["relations"]] == [("m", "competes", "k")]
