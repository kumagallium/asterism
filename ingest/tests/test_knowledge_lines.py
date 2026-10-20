"""孤立の定義の改訂 — 確定した知識の関係の本数（利用者裁定 2026-10-20・PR 6a の A4）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import rdflib

from asterism import shared_vocab as sv
from asterism.crosswalk_runtime import (
    assert_alignment,
    remove_alignment,
)
from asterism.query_tools import QueryTool, prune_unwired_tools

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
# A4: 孤立の改訂（知識の本数）
# ---------------------------------------------------------------------------


async def test_knowledge_counts_and_json_beside_wired(seeded: _Client, tmp_path: Path) -> None:
    assert await sv.knowledge_counts(seeded) == {}
    await _lay(seeded, tmp_path)
    cc, lt = T("carrier_concentration"), T("lattice_tc")
    await assert_alignment(seeded, cc, lt, "subPropertyOf", at=AT)
    k = await sv.knowledge_counts(seeded)
    # 熱伝導率: 格子・電子の isPartOf と 点欠陥 lowers = 3（subPropertyOf は数えない）
    assert k[T("thermal_conductivity")] == 3
    assert k[T("lattice_tc")] == 2  # isPartOf（from）+ 粒界散乱 lowers（to）
    assert k[T("carrier_concentration")] == 1  # raises だけ。subPropertyOf は知識の関係でない
    assert sv.load_knowledge(tmp_path) == {}  # まだ書いていない
    await sv.recompute_wired(seeded, tmp_path, at=AT)
    assert sv.load_knowledge(tmp_path) == k
    assert (tmp_path / sv.REGISTRY_ID / "wired.json").is_file()
    raw = json.loads((tmp_path / sv.REGISTRY_ID / "knowledge.json").read_text())
    assert raw["at"] == AT and raw["terms"][T("thermal_conductivity")] == 3
    # 線を外すと本数が減る（数え直し）
    await remove_alignment(seeded, T("point_defect"), T("thermal_conductivity"), "lowers")
    await sv.recompute_wired(seeded, tmp_path)
    assert sv.load_knowledge(tmp_path)[T("thermal_conductivity")] == 2
    assert T("point_defect") not in sv.load_knowledge(tmp_path)  # 線が 0 の語は載らない
    (tmp_path / sv.REGISTRY_ID / "knowledge.json").write_text("{broken")
    assert sv.load_knowledge(tmp_path) == {}


def _tool(name: str, terms: tuple[str, ...]) -> QueryTool:
    return QueryTool(
        name=name,
        title=name,
        description="d",
        params=(),
        query="SELECT 1 WHERE {}",
        for_terms=terms,
    )


def test_prune_keeps_a_tool_whose_term_has_knowledge(tmp_path: Path) -> None:
    a, b, c = T("a"), T("b"), T("c")
    d = tmp_path / sv.REGISTRY_ID
    d.mkdir()
    tools = [_tool("plain", ()), _tool("cq_a", (a,)), _tool("cq_b", (b,)), _tool("cq_c", (b, c))]
    # knowledge.json が無い → 従来どおり（wired だけ）
    (d / "wired.json").write_text(json.dumps({"terms": {a: 1, b: 0, c: 0}}))
    assert [t.name for t in prune_unwired_tools(tools, tmp_path)] == ["plain", "cq_a"]
    # 知識が 1 本以上あれば、データが 0 でも残す
    (d / "knowledge.json").write_text(json.dumps({"terms": {b: 2}}))
    assert [t.name for t in prune_unwired_tools(tools, tmp_path)] == [
        "plain",
        "cq_a",
        "cq_b",
        "cq_c",
    ]
    (d / "knowledge.json").write_text(json.dumps({"terms": {b: 0}}))
    assert [t.name for t in prune_unwired_tools(tools, tmp_path)] == ["plain", "cq_a"]


async def test_mint_without_cqs_needs_a_theme(tmp_path: Path) -> None:
    c = _Client()
    with pytest.raises(sv.TermError):
        await sv.mint_term(
            c, tmp_path, slug="grain", kind="class", label="粒界散乱", cqs=[], at=AT
        )
    out = await sv.mint_term(
        c, tmp_path, slug="grain", kind="class", label="粒界散乱", cqs=[], at=AT, theme_id="tc"
    )
    assert out["slug"] == "grain"
    assert sv.term_iri("grain") in await sv.minted_iris(c)
    # CQ は作らない（要因の概念は問い 0 件）
    assert sv._read_cq_tools(tmp_path) == []
    await sv.remove_term(c, tmp_path, "grain")  # 消せる
