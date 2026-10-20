"""関係の閉集合 9 述語・種別表・循環・来歴・却下の拡張（design.md §15.2・PR 6a の A1〜A3）。

store は実物の ``rdflib.Dataset``（test_shared_vocab と同じ立て方）。
"""

# ruff: noqa: RUF001
from __future__ import annotations

import json
from pathlib import Path

import pytest
import rdflib

from asterism import shared_vocab as sv
from asterism.crosswalk import XW
from asterism.crosswalk_runtime import (
    ALIGN_RELATIONS,
    ALIGNMENT_GRAPH,
    RELATION_SHAPES,
    assert_alignment,
    list_alignments,
)
from asterism.shared_vocab import CQSpec

AT = "2026-10-20T00:00:00+00:00"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
NS_A = "https://example.org/sample-a#"
OBO = "http://purl.obolibrary.org/obo/"


class _Client:
    def __init__(self) -> None:
        self.ds = rdflib.Dataset()

    async def sparql_select(self, query: str) -> dict:
        raw = self.ds.query(query).serialize(format="json")
        return json.loads(raw.decode() if isinstance(raw, bytes) else raw)

    async def sparql_update(self, update: str) -> None:
        self.ds.update(update)


def _declare(client: _Client, iri: str, kind: str, label: str | None = None) -> None:
    t = rdflib.URIRef((RDFS + "Class") if kind == "class" else (RDF + "Property"))
    g = client.ds.graph(rdflib.URIRef(sv.SHARED_VOCAB_GRAPH))
    g.add((rdflib.URIRef(iri), rdflib.RDF.type, t))
    if label:
        g.add((rdflib.URIRef(iri), rdflib.RDFS.label, rdflib.Literal(label, lang="ja")))


T = sv.term_iri  # sv:<slug>


@pytest.fixture
def seeded() -> _Client:
    """熱伝導率（項目）・格子/電子熱伝導率（その一部）・粒界散乱/点欠陥/キャリア濃度。"""
    c = _Client()
    for slug, kind, label in [
        ("thermal_conductivity", "property", "熱伝導率"),
        ("lattice_tc", "property", "格子熱伝導率"),
        ("electronic_tc", "property", "電子熱伝導率"),
        ("grain_boundary_scattering", "class", "粒界散乱"),
        ("point_defect", "class", "点欠陥"),
        ("carrier_concentration", "property", "キャリア濃度"),
    ]:
        _declare(c, T(slug), kind, label)
    return c


async def _lay(client: _Client, tmp_path: Path) -> None:
    kw = {"at": AT, "drafted_by": "ai", "batch": "b1"}
    await assert_alignment(client, T("lattice_tc"), T("thermal_conductivity"), "isPartOf", **kw)
    await assert_alignment(client, T("electronic_tc"), T("thermal_conductivity"), "isPartOf", **kw)
    await assert_alignment(
        client,
        T("grain_boundary_scattering"),
        T("lattice_tc"),
        "lowers",
        reason="粒が細かいほど",
        **kw,
    )
    await assert_alignment(client, T("point_defect"), T("thermal_conductivity"), "lowers", **kw)
    await assert_alignment(client, T("carrier_concentration"), T("electronic_tc"), "raises", **kw)


# ---------------------------------------------------------------------------
# A1: 9 述語・述語ごとの種別表・循環
# ---------------------------------------------------------------------------


def test_nine_relations_with_standard_iris_and_a_shape_row_each() -> None:
    assert len(ALIGN_RELATIONS) == 9
    assert ALIGN_RELATIONS["isPartOf"] == OBO + "BFO_0000050"
    assert ALIGN_RELATIONS["lowers"] == OBO + "RO_0002212"
    assert ALIGN_RELATIONS["raises"] == OBO + "RO_0002213"
    assert ALIGN_RELATIONS["affects"] == OBO + "RO_0002211"
    assert set(RELATION_SHAPES) == set(ALIGN_RELATIONS)
    assert not any(v.startswith(sv.SV) for v in ALIGN_RELATIONS.values())  # sv: に述語は置かない


async def test_shape_table_per_relation(seeded: _Client) -> None:
    prop, cls = T("thermal_conductivity"), T("point_defect")
    prop2, cls2 = T("lattice_tc"), T("grain_boundary_scattering")

    async def put(s: str, t: str, rel: str) -> None:
        await assert_alignment(seeded, s, t, rel, at=AT)

    # lowers / raises / affects: to は項目だけ。from は種類でも項目でもよい
    await put(cls, prop, "lowers")
    await put(prop2, prop, "raises")
    with pytest.raises(ValueError, match="kind mismatch"):
        await put(prop, cls2, "lowers")  # to が種類
    with pytest.raises(ValueError, match="kind mismatch"):
        await put(prop, cls2, "affects")
    # isPartOf: 同じ種別どうし
    await put(prop2, prop, "isPartOf")
    await put(cls2, cls, "isPartOf")
    with pytest.raises(ValueError, match="kind mismatch"):
        await put(prop, cls, "isPartOf")
    # subClassOf は 種類→種類、subPropertyOf は 項目→項目
    with pytest.raises(ValueError, match="kind mismatch"):
        await put(prop, prop2, "subClassOf")
    with pytest.raises(ValueError, match="kind mismatch"):
        await put(cls, cls2, "subPropertyOf")
    # hasQuantityKind: from は項目・to は検査しない（QUDT の個体）
    await put(prop, "http://qudt.org/vocab/quantitykind/ThermalConductivity", "hasQuantityKind")
    with pytest.raises(ValueError, match="kind mismatch"):
        await put(cls, "http://qudt.org/vocab/quantitykind/ThermalConductivity", "hasQuantityKind")


async def test_undeclared_or_both_declared_ends_pass(seeded: _Client) -> None:
    both = T("both")
    _declare(seeded, both, "class")
    _declare(seeded, both, "property")
    await assert_alignment(seeded, T("point_defect"), both, "lowers", at=AT)  # 両方で宣言
    # 宣言の無い端（データセットの語など）は通す
    await assert_alignment(seeded, NS_A + "x", NS_A + "y", "isPartOf", at=AT)


async def test_is_part_of_cycle_refused_but_regulation_cycles_allowed(seeded: _Client) -> None:
    a, b, c = T("lattice_tc"), T("electronic_tc"), T("thermal_conductivity")
    await assert_alignment(seeded, a, b, "isPartOf", at=AT)
    await assert_alignment(seeded, b, c, "isPartOf", at=AT)
    with pytest.raises(ValueError, match="cycle"):
        await assert_alignment(seeded, c, a, "isPartOf", at=AT)  # 一周
    with pytest.raises(ValueError, match="cycle"):
        await assert_alignment(seeded, a, a, "isPartOf", at=AT)
    # 規制系は循環を許す（フィードバックは正当）
    await assert_alignment(seeded, a, b, "lowers", at=AT)
    await assert_alignment(seeded, b, a, "lowers", at=AT)
    await assert_alignment(seeded, a, a, "affects", at=AT)
    # sub / eq のあいだでは isPartOf の輪は検査しない（別の述語）
    await assert_alignment(seeded, c, a, "subPropertyOf", at=AT)


async def test_preview_uses_the_same_cycle_table(seeded: _Client, tmp_path: Path) -> None:
    a, b = T("lattice_tc"), T("electronic_tc")
    await assert_alignment(seeded, a, b, "isPartOf", at=AT)
    p = await sv.preview_alignment(seeded, tmp_path, b, a, "isPartOf")
    assert (p["valid"], p["reason"]) == (False, "cycle")
    p = await sv.preview_alignment(seeded, tmp_path, b, a, "lowers")
    assert p["valid"] is True
    p = await sv.preview_alignment(seeded, tmp_path, T("point_defect"), a, "isPartOf")
    assert (p["valid"], p["reason"]) == (False, "mixed")


async def test_upper_path_is_unchanged_part_of_does_not_wire(seeded: _Client) -> None:
    assert "BFO_0000050" not in sv.UPPER_PATH_CLASS + sv.UPPER_PATH_PROPERTY
    await assert_alignment(seeded, T("lattice_tc"), T("thermal_conductivity"), "isPartOf", at=AT)
    counts = await sv.answering_datasets(seeded, [T("thermal_conductivity")])
    assert counts == {T("thermal_conductivity"): 0}  # 公開データが無ければ 0（isPartOf は数えない）


def test_reserved_slugs() -> None:
    assert {"part_of", "lowers", "raises", "affects", "regulates"} == set(sv.RESERVED_SLUGS)


@pytest.mark.parametrize("slug", sorted(sv.RESERVED_SLUGS))
async def test_mint_refuses_a_reserved_slug(slug: str, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="予約語"):
        await sv.mint_term(
            _Client(),
            tmp_path,
            slug=slug,
            kind="property",
            label="下げる",
            cqs=[CQSpec("何件か", "values")],
            at=AT,
        )


async def test_lines_read_all_nine_relations(seeded: _Client, tmp_path: Path) -> None:
    await _lay(seeded, tmp_path)
    lines = await sv._alignment_lines(seeded)
    assert {r for _, r, _ in lines} == {"isPartOf", "lowers", "raises"}
    assert len(lines) == 5
    for rel in ALIGN_RELATIONS:  # 全部の述語が読み戻せる
        assert sv._REL_BY_IRI[ALIGN_RELATIONS[rel]] == rel


# ---------------------------------------------------------------------------
# A2: 来歴
# ---------------------------------------------------------------------------


async def test_provenance_is_written_listed_and_defaults_to_human(seeded: _Client) -> None:
    long_reason = "粒が細かい\nほど\r\nフォノンが散乱される"
    out = await assert_alignment(
        seeded,
        T("grain_boundary_scattering"),
        T("lattice_tc"),
        "lowers",
        at=AT,
        drafted_by="ai",
        batch="batch-1",
        reason=long_reason,
    )
    assert out["drafted_by"] == "ai" and out["batch"] == "batch-1"
    assert out["reason"] == "粒が細かい ほど フォノンが散乱される"
    await assert_alignment(seeded, T("point_defect"), T("lattice_tc"), "lowers", at=AT)  # 既定
    rows = {r["source"]: r for r in await list_alignments(seeded)}
    g = rows[T("grain_boundary_scattering")]
    assert (g["drafted_by"], g["batch"], g["reason"]) == (
        "ai",
        "batch-1",
        "粒が細かい ほど フォノンが散乱される",
    )
    h = rows[T("point_defect")]
    assert (h["drafted_by"], h["batch"], h["reason"]) == ("human", None, None)
    assert g["relation"] == "lowers"
    # 来歴の述語は xw:（sv: に置かない）
    g_ = seeded.ds.graph(rdflib.URIRef(ALIGNMENT_GRAPH))
    preds = {str(p) for _, p, _ in g_}
    assert {XW + "draftedBy", XW + "batch", XW + "reason"} <= preds
    assert not any(p.startswith(sv.SV) for p in preds)


async def test_provenance_validation(seeded: _Client) -> None:
    s, t = T("point_defect"), T("lattice_tc")
    with pytest.raises(ValueError, match="drafted_by"):
        await assert_alignment(seeded, s, t, "lowers", at=AT, drafted_by="robot")
    with pytest.raises(ValueError, match="reason"):
        await assert_alignment(seeded, s, t, "lowers", at=AT, reason="あ" * 201)
    await assert_alignment(seeded, s, t, "lowers", at=AT, reason="あ" * 200)
    with pytest.raises(ValueError, match="batch"):
        await assert_alignment(seeded, s, t, "lowers", at=AT, batch="a\nb")
    # 再び置くと来歴は置き換わる（冪等）
    await assert_alignment(seeded, s, t, "lowers", at=AT, drafted_by="ai", batch="b2")
    rows = [r for r in await list_alignments(seeded) if r["source"] == s]
    assert len(rows) == 1 and rows[0]["drafted_by"] == "ai" and rows[0]["reason"] is None


# ---------------------------------------------------------------------------
# A3: 却下の拡張
# ---------------------------------------------------------------------------


def test_dismissal_with_relation_and_concepts(tmp_path: Path) -> None:
    a, b = NS_A + "a", NS_A + "b"
    assert sv.dismiss_fit(tmp_path, a, b, at=AT) is True  # 従来の形
    assert sv.load_dismissed(tmp_path) == [{"subject": a, "term": b, "at": AT}]  # relation キー無し
    assert sv.dismiss_fit(tmp_path, a, b, at=AT, relation="lowers") is True  # 別の行
    assert sv.dismiss_fit(tmp_path, a, b, at=AT, relation="lowers") is False  # 冪等
    rows = sv.load_dismissed(tmp_path)
    assert {"subject": a, "term": b, "at": AT, "relation": "lowers"} in rows and len(rows) == 2
    d = sv.load_dismissed(tmp_path)
    assert sv.is_dismissed(d, a, b) and sv.is_dismissed(d, a, b, relation="lowers")
    assert not sv.is_dismissed(d, a, b, relation="raises")
    assert not sv.is_dismissed(d, b, a)
    with pytest.raises(ValueError, match="relation"):
        sv.dismiss_fit(tmp_path, a, b, at=AT, relation="causes")
    # 概念どうしの関係の却下（まだ置かれていない概念の鍵）
    ca, cb = sv.concept_subject("粒界散乱"), sv.concept_subject("格子熱伝導率")
    assert sv.dismiss_fit(tmp_path, ca, cb, at=AT, relation="lowers") is True
    # 概念の却下: term は固定値 "concept"
    assert sv.dismiss_fit(tmp_path, ca, "concept", at=AT) is True
    assert sv.concept_subject(" 粒界　散乱 ").startswith("concept:")
    assert sv.is_dismissed(sv.load_dismissed(tmp_path), ca, sv.CONCEPT_DISMISS_TERM)
    with pytest.raises(ValueError):
        sv.dismiss_fit(tmp_path, ca, "not-concept", at=AT)
    with pytest.raises(ValueError):
        sv.dismiss_fit(tmp_path, "concept:", "concept", at=AT)
    # 戻す
    assert sv.undismiss_fit(tmp_path, a, b, relation="lowers") is True
    assert sv.undismiss_fit(tmp_path, a, b, relation="lowers") is False
    assert sv.is_dismissed(sv.load_dismissed(tmp_path), a, b)  # 従来の行は残る
    assert sv.undismiss_fit(tmp_path, a, b) is True


def test_load_dismissed_reads_old_files(tmp_path: Path) -> None:
    (tmp_path / sv.REGISTRY_ID).mkdir()
    (tmp_path / sv.REGISTRY_ID / "dismissed.json").write_text(
        json.dumps([{"subject": "s", "term": "https://e.org/t", "at": AT}, {"bad": 1}]),
        encoding="utf-8",
    )
    assert sv.load_dismissed(tmp_path) == [{"subject": "s", "term": "https://e.org/t", "at": AT}]
