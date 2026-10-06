"""Tests for asterism.kind_counts — 種類ごとの件数をデータセット単位で返す読み手。

実物の pyoxigraph.Store で動かす（``test_prov_graph`` の ``_pyoxi_client`` と同じ約束:
canonical 基底の graph は control graph に promoted 印を付ける）。題材は架空の 2 分野
（気象観測・図書館の貸出）。両方が同じ種類 IRI を使う場合を含む。
"""
from __future__ import annotations

import pytest

from asterism.kind_counts import kind_counts
from asterism.substrate import (
    CANONICAL_GRAPH_BASE,
    CONTROL_GRAPH_IRI,
    STATUS_PREDICATE,
    STATUS_PROMOTED,
    canonical_graph_iri,
)

pyoxigraph = pytest.importorskip("pyoxigraph")

SHARED = "https://ex/shared#Record"
ONLY_W = "https://ex/weather#Station"
ONLY_L = "https://ex/library#Loan"

G_WEATHER = canonical_graph_iri("weatherlog") + "/v1"
G_LIBRARY = canonical_graph_iri("library-loans") + "/v2"
G_HUB = canonical_graph_iri("crosswalk")
G_DRAFT = "https://kumagallium.github.io/asterism/draft/unpublished"

PFX = "@prefix ex: <https://ex/> .\n"


def _pyoxi_client(graphs: dict[str, str]):
    """実物の pyoxigraph.Store を包む（test_prov_graph の同名ヘルパと同じ約束。
    ingest/tests は兄弟を import できないので写している）。"""
    store = pyoxigraph.Store()
    for giri, ttl in graphs.items():
        store.load(
            ttl.encode("utf-8"), mime_type="text/turtle", to_graph=pyoxigraph.NamedNode(giri)
        )
        if giri.startswith(CANONICAL_GRAPH_BASE):
            store.add(
                pyoxigraph.Quad(
                    pyoxigraph.NamedNode(giri),
                    pyoxigraph.NamedNode(STATUS_PREDICATE),
                    pyoxigraph.Literal(STATUS_PROMOTED),
                    pyoxigraph.NamedNode(CONTROL_GRAPH_IRI),
                )
            )

    class _C:
        async def sparql_select(self, query: str) -> dict:
            result = store.query(query)
            names = [v.value for v in result.variables]
            bindings = []
            for solution in result:
                row = {}
                for name in names:
                    term = solution[name]
                    if term is None:
                        continue
                    kind = "uri" if isinstance(term, pyoxigraph.NamedNode) else "literal"
                    row[name] = {"type": kind, "value": term.value}
                bindings.append(row)
            return {"results": {"bindings": bindings}}

    return _C()


def _ttl(rows: dict[str, list[str]]) -> str:
    """{種類 IRI: [主語のローカル名…]} → turtle。"""
    out = []
    for cls, subjects in rows.items():
        for s in subjects:
            out.append(f"<https://ex/s/{s}> a <{cls}> .")
    return "\n".join(out)


class _Counting:
    """SPARQL の本数を数える包み。"""

    def __init__(self, inner):
        self.inner = inner
        self.calls = 0

    async def sparql_select(self, query: str) -> dict:
        self.calls += 1
        return await self.inner.sparql_select(query)


@pytest.mark.asyncio
async def test_same_kind_iri_is_counted_per_dataset() -> None:
    client = _pyoxi_client(
        {
            G_WEATHER: _ttl({SHARED: ["a", "b", "c"], ONLY_W: ["st1"]}),
            G_LIBRARY: _ttl({SHARED: ["x"], ONLY_L: ["l1", "l2"]}),
        }
    )
    out = await kind_counts(client)
    assert out["truncated"] is False
    by_ds = {g["dataset_id"]: g for g in out["graphs"]}
    assert by_ds["weatherlog"]["kinds"] == [
        {"class_iri": SHARED, "count": 3},
        {"class_iri": ONLY_W, "count": 1},
    ]
    assert by_ds["library-loans"]["kinds"] == [
        {"class_iri": ONLY_L, "count": 2},
        {"class_iri": SHARED, "count": 1},
    ]
    assert all(g["hub"] is False for g in out["graphs"])
    # graph IRI 順
    assert [g["graph"] for g in out["graphs"]] == sorted(g["graph"] for g in out["graphs"])


@pytest.mark.asyncio
async def test_hub_graph_flagged_and_has_no_dataset_id() -> None:
    client = _pyoxi_client({G_HUB: _ttl({SHARED: ["h"]}), G_WEATHER: _ttl({ONLY_W: ["a"]})})
    out = await kind_counts(client)
    hub = next(g for g in out["graphs"] if g["graph"] == G_HUB)
    assert hub["hub"] is True
    assert hub["dataset_id"] is None


@pytest.mark.asyncio
async def test_distinct_subjects_counted_once_and_ties_sorted_by_iri() -> None:
    ttl = (
        f"<https://ex/s/a> a <{SHARED}>, <{ONLY_W}> .\n"
        f"<https://ex/s/b> a <{SHARED}> .\n"
        f"<https://ex/s/a> a <{SHARED}> .\n"
    )
    out = await kind_counts(_pyoxi_client({G_WEATHER: ttl}))
    assert out["graphs"][0]["kinds"] == [
        {"class_iri": SHARED, "count": 2},
        {"class_iri": ONLY_W, "count": 1},
    ]


@pytest.mark.asyncio
async def test_draft_graph_is_not_counted() -> None:
    client = _pyoxi_client(
        {G_WEATHER: _ttl({ONLY_W: ["a"]}), G_DRAFT: _ttl({SHARED: ["d1", "d2", "d3"]})}
    )
    out = await kind_counts(client)
    assert [g["dataset_id"] for g in out["graphs"]] == ["weatherlog"]
    assert all(k["class_iri"] != SHARED for g in out["graphs"] for k in g["kinds"])


@pytest.mark.asyncio
async def test_no_published_graph_sends_no_sparql_beyond_listing() -> None:
    client = _Counting(_pyoxi_client({G_DRAFT: _ttl({SHARED: ["d"]})}))
    out = await kind_counts(client)
    assert out == {"graphs": [], "truncated": False}
    # canonical_graphs の列挙 1 本だけ。空の FROM NAMED で draft まで読む本は投げない。
    assert client.calls == 1


@pytest.mark.asyncio
async def test_truncated_when_rows_exceed_max_rows() -> None:
    classes = {f"https://ex/k#C{i}": [f"s{i}"] for i in range(5)}
    out = await kind_counts(_pyoxi_client({G_WEATHER: _ttl(classes)}), max_rows=3)
    assert out["truncated"] is True
    assert sum(len(g["kinds"]) for g in out["graphs"]) == 3
    full = await kind_counts(_pyoxi_client({G_WEATHER: _ttl(classes)}), max_rows=5)
    assert full["truncated"] is False
    assert sum(len(g["kinds"]) for g in full["graphs"]) == 5


@pytest.mark.asyncio
async def test_literal_class_is_ignored_only_iri_kinds() -> None:
    ttl = f'<https://ex/s/a> a <{ONLY_W}> .\n<https://ex/s/a> <https://ex/p> "x" .'
    out = await kind_counts(_pyoxi_client({G_WEATHER: ttl}))
    assert out["graphs"][0]["kinds"] == [{"class_iri": ONLY_W, "count": 1}]
