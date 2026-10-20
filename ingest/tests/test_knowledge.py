"""asterism.knowledge — 知識の走者（design.md §15.2・PR 6a の A5）。

公開済みのデータセットが 1 件も無い状態で、確定した関係の線だけから答えが返ることを固める。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import rdflib

from asterism import knowledge, substrate
from asterism import shared_vocab as sv
from asterism.crosswalk_runtime import (
    ALIGN_RELATIONS,
    ALIGNMENT_GRAPH,
    assert_alignment,
    remove_alignment,
)

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
# A5: 知識の走者
# ---------------------------------------------------------------------------


async def test_factors_parts_kinds_on_a_store_with_no_published_dataset(
    seeded: _Client, tmp_path: Path
) -> None:
    await _lay(seeded, tmp_path)
    tc = T("thermal_conductivity")
    low = await knowledge.factors(seeded, tc, "lowers")
    assert {r["label"] for r in low} == {"粒界散乱", "点欠陥"}
    by_label = {r["label"]: r for r in low}
    # 点欠陥は直接・粒界散乱は 格子熱伝導率（一部）経由の 1 段の閉包
    assert by_label["点欠陥"]["via"] == tc and by_label["粒界散乱"]["via"] == T("lattice_tc")
    assert by_label["粒界散乱"]["via_label"] == "格子熱伝導率"
    assert by_label["粒界散乱"]["reason"] == "粒が細かいほど"
    assert by_label["粒界散乱"]["drafted_by"] == "ai" and by_label["粒界散乱"]["batch"] == "b1"
    assert by_label["粒界散乱"]["at"] == AT
    assert by_label["点欠陥"]["reason"] is None
    assert low[0]["label"] == "点欠陥"  # 直接の線が先
    assert {r["relation"] for r in low} == {"lowers"}

    aff = await knowledge.factors(seeded, tc, "affects")  # 3 述語すべて
    assert {r["label"] for r in aff} == {"粒界散乱", "点欠陥", "キャリア濃度"}
    assert len(aff) == 3
    assert {r["relation"] for r in aff} == {"lowers", "raises"}
    rai = await knowledge.factors(seeded, tc, "raises")
    assert [r["label"] for r in rai] == ["キャリア濃度"]
    assert rai[0]["via"] == T("electronic_tc")

    got_parts = await knowledge.parts(seeded, tc)
    assert {r["label"] for r in got_parts} == {"格子熱伝導率", "電子熱伝導率"}
    assert await knowledge.kinds(seeded, tc) == []
    await assert_alignment(seeded, T("lattice_tc"), tc, "subPropertyOf", at=AT)
    assert [r["label"] for r in await knowledge.kinds(seeded, tc)] == ["格子熱伝導率"]
    # 閉包は 1 段だけ: 粒界散乱の下にさらに足しても 熱伝導率 の下げるものには出ない
    await assert_alignment(
        seeded, T("point_defect"), T("grain_boundary_scattering"), "isPartOf", at=AT
    )  # 種類 → 種類（from/to とも class）
    low2 = await knowledge.factors(seeded, tc, "lowers")
    assert {r["label"] for r in low2} == {"粒界散乱", "点欠陥"}
    # 外した線は答えから消える
    await remove_alignment(seeded, T("point_defect"), tc, "lowers")
    assert {r["label"] for r in await knowledge.factors(seeded, tc, "lowers")} == {"粒界散乱"}


async def test_label_falls_back_to_local_name_and_prefers_japanese(seeded: _Client) -> None:
    tc = T("thermal_conductivity")
    other = NS_A + "unlabeled_factor"  # shared graph にラベルが無い
    g = seeded.ds.graph(rdflib.URIRef(sv.SHARED_VOCAB_GRAPH))
    g.add((rdflib.URIRef(tc), rdflib.RDFS.label, rdflib.Literal("Thermal conductivity", lang="en")))
    await assert_alignment(seeded, other, tc, "lowers", at=AT)
    rows = await knowledge.factors(seeded, tc, "lowers")
    assert [r["label"] for r in rows] == ["unlabeled_factor"]
    assert rows[0]["via_label"] == "熱伝導率"  # ja が en に勝つ


async def test_knowledge_answer_validates_and_shapes(seeded: _Client, tmp_path: Path) -> None:
    await _lay(seeded, tmp_path)
    tc = T("thermal_conductivity")
    out = await knowledge.knowledge_answer(
        seeded, {"op": "factors", "target": tc, "direction": "lowers"}
    )
    assert out["count"] == 2 == len(out["items"])
    assert (out["op"], out["target"], out["direction"]) == ("factors", tc, "lowers")
    out = await knowledge.knowledge_answer(seeded, {"op": "parts", "target": tc, "direction": "x"})
    assert out["count"] == 2 and out["direction"] is None
    assert (await knowledge.knowledge_answer(seeded, {"op": "kinds", "target": tc}))["count"] == 0
    for bad in (
        {"op": "count", "target": tc},
        {"op": "factors", "target": tc},  # direction 無し
        {"op": "factors", "target": tc, "direction": "changes"},
        {"op": "parts", "target": "not an iri"},
        {"op": "parts", "target": tc + "> } DROP ALL #"},
        {"op": "parts"},
    ):
        with pytest.raises(ValueError):
            await knowledge.knowledge_answer(seeded, bad)


async def test_empty_alignment_graph_answers_zero(tmp_path: Path) -> None:
    c = _Client()
    # 無関係の 1 行（rdflib は存在しない graph を FROM NAMED で引くと URL を取りに行く）
    c.ds.graph(rdflib.URIRef(ALIGNMENT_GRAPH)).add(
        (rdflib.URIRef("urn:x"), rdflib.URIRef("urn:p"), rdflib.URIRef("urn:y"))
    )
    c.ds.graph(rdflib.URIRef(sv.SHARED_VOCAB_GRAPH)).add(
        (rdflib.URIRef("urn:x"), rdflib.URIRef("urn:p"), rdflib.URIRef("urn:y"))
    )
    out = await knowledge.knowledge_answer(
        c, {"op": "factors", "target": T("thermal_conductivity"), "direction": "affects"}
    )
    assert out["items"] == [] and out["count"] == 0


async def test_runner_never_reads_a_data_graph(seeded: _Client, tmp_path: Path) -> None:
    await _lay(seeded, tmp_path)
    secret = seeded.ds.graph(rdflib.URIRef(substrate.canonical_graph_iri("ds1")))
    tc = rdflib.URIRef(T("thermal_conductivity"))
    lowers = rdflib.URIRef(ALIGN_RELATIONS["lowers"])
    secret.add((rdflib.URIRef(T("point_defect")), lowers, tc))
    secret.add((rdflib.URIRef("urn:x"), lowers, tc))
    rows = await knowledge.factors(seeded, T("thermal_conductivity"), "lowers")
    assert "urn:x" not in {r["iri"] for r in rows}
