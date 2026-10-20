"""asterism.suggested_lines — 公開のあとの「結べそうな線」（ui-redesign PR 7c・契約 B1）。

実物の ``pyoxigraph.Store``（ontology graph を置く）。計算は名前の完全一致だけ（AI なし）。
2 文字以下の別名は使わない・置くのは冪等・読むときにいまの線と却下で絞る。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from asterism import shared_vocab as sv
from asterism import substrate, suggested_lines

pyoxigraph = pytest.importorskip("pyoxigraph")

AT = "2026-10-20T00:00:00+00:00"
WX = "https://ex/wx#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"


class _Client:
    def __init__(self) -> None:
        self.store = pyoxigraph.Store()

    async def sparql_select(self, query: str) -> dict:
        result = self.store.query(query, use_default_graph_as_union=True)
        names = [v.value for v in result.variables]
        bindings = []
        for solution in result:
            row = {}
            for name in names:
                term = solution[name]
                if term is None:
                    continue
                if isinstance(term, pyoxigraph.NamedNode):
                    row[name] = {"type": "uri", "value": term.value}
                elif isinstance(term, pyoxigraph.Literal):
                    cell = {"type": "literal", "value": term.value}
                    if term.language:
                        cell["xml:lang"] = term.language
                    row[name] = cell
            bindings.append(row)
        return {"results": {"bindings": bindings}}


def _t(slug: str, ja: str, kind: str, *aliases: str, en: str | None = None) -> sv.SharedTerm:
    return sv.SharedTerm(sv.SV + slug, slug, kind, ja, en, None, AT, aliases=tuple(aliases))


def _ontology(client: _Client, dataset_id: str, ttl: str) -> None:
    pfx = f"@prefix wx: <{WX}> .\n@prefix rdfs: <{RDFS}> .\n@prefix rdf: <{RDF}> .\n"
    client.store.load(
        (pfx + ttl).encode(),
        mime_type="text/turtle",
        to_graph=pyoxigraph.NamedNode(substrate.ontology_graph_iri(dataset_id)),
    )


def test_exact_names_only_and_a_short_alias_is_not_a_reason() -> None:
    client = _Client()
    _ontology(
        client,
        "ds1",
        "wx:Station a rdfs:Class ; rdfs:label 'ステーション'@ja , 'Station'@en .\n"
        "wx:Site a rdfs:Class ; rdfs:label 'ス'@ja .\n"
        "wx:Pad a rdfs:Class ; rdfs:label 'パッド'@ja .\n"
        "wx:temp a rdf:Property ; rdfs:label '気温'@ja .\n"
        "wx:name a rdf:Property ; rdfs:label '名前'@ja .\n",  # generic — never a candidate
    )
    terms = [
        _t("station", "ステーション", "class", en="Station"),
        _t("short", "別の語", "class", "ス"),  # a 1-character alias must not catch wx:Site
        _t("long_alias", "別の語2", "class", "パッド"),  # a 3-character alias counts
        _t("air_temp", "気温", "property"),
        _t("temp_class", "気温", "class"),  # same name, wrong kind for the item
        _t("name_term", "名前", "property"),
    ]
    rows = asyncio.run(
        suggested_lines.compute_for_dataset(client, "ds1", version="v1", at=AT, terms=terms)
    )
    got = {(r["subject"], r["term"], r["relation"]) for r in rows}
    assert got == {
        (WX + "Station", sv.SV + "station", "equivalentClass"),  # ja と en の両方で当たっても 1 件
        (WX + "Pad", sv.SV + "long_alias", "equivalentClass"),
        (WX + "temp", sv.SV + "air_temp", "equivalentProperty"),
    }
    assert len(rows) == 3
    assert {r["dataset_id"] for r in rows} == {"ds1"} and {r["version"] for r in rows} == {"v1"}
    # no terms → nothing; a shared / standard subject is not a dataset's own kind
    none = suggested_lines.compute_for_dataset(client, "ds1", version="v", at=AT, terms=[])
    assert asyncio.run(none) == []


def test_recording_is_idempotent_per_dataset_version_subject_term(tmp_path: Path) -> None:
    row = {"subject": WX + "A", "term": sv.SV + "a", "relation": "equivalentClass",
           "dataset_id": "ds1", "version": "v1", "at": AT}  # fmt: skip
    assert suggested_lines.record_for_dataset(tmp_path, [row, row]) == 1
    assert suggested_lines.record_for_dataset(tmp_path, [row]) == 0
    assert suggested_lines.record_for_dataset(tmp_path, [{**row, "version": "v2"}]) == 1
    assert len(suggested_lines.load_suggestions(tmp_path)) == 2
    same = {k: row[k] for k in ("subject", "term", "relation")}
    assert suggested_lines.remove_suggestions(tmp_path, [same]) == 2  # 同じ線の全版
    assert suggested_lines.load_suggestions(tmp_path) == []
    # a broken file reads as empty
    (tmp_path / sv.REGISTRY_ID).mkdir(exist_ok=True)
    (tmp_path / sv.REGISTRY_ID / suggested_lines.FILE).write_text("{not json", encoding="utf-8")
    assert suggested_lines.load_suggestions(tmp_path) == []


def test_pending_drops_what_the_store_and_the_dismissals_no_longer_want() -> None:
    def row(s, t, rel="equivalentClass", ds="ds1", v="v1", at=AT):
        return {"subject": WX + s, "term": sv.SV + t, "relation": rel, "dataset_id": ds,
                "version": v, "at": at}  # fmt: skip

    rows = [
        row("A", "a"), row("A", "a", v="v2", at="2026-10-21T00:00:00+00:00"),  # same → newest
        row("B", "b"),  # a line already exists (either way round for ≡)
        row("C", "c"),  # dismissed (no relation)
        row("D", "d"),  # dismissed with the relation
        row("E", "gone"),  # the term is no longer there
        row("F", "f", ds="ds-retracted"),  # the dataset is no longer published
        row("G", "g", rel="subClassOf"),  # a one-way line only counts the same way round
    ]
    terms = [_t(s, s, "class") for s in ("a", "b", "c", "d", "f", "g")]
    lines = [
        {"source": sv.SV + "b", "relation": "equivalentClass", "target": WX + "B"},
        {"source": sv.SV + "g", "relation": "subClassOf", "target": WX + "G"},
    ]
    dismissed = [
        {"subject": WX + "C", "term": sv.SV + "c", "at": AT},
        {"subject": WX + "D", "term": sv.SV + "d", "relation": "equivalentClass", "at": AT},
    ]
    got = suggested_lines.pending(
        rows, lines=lines, dismissed=dismissed, terms=terms, live_datasets={"ds1"}
    )
    assert [(r["subject"], r["version"]) for r in got] == [
        (WX + "A", "v2"), (WX + "G", "v1"),
    ]  # fmt: skip
