"""Tests for asterism.dataset_summary (契約メモ contract_pr_f2.md §3.1、
統合時の所見 #1: classes から来歴 (PROV) のクラスを除く)。

Backed by a real ``pyoxigraph.Store`` — same fixture shape as
``test_class_schema.py`` の ``_pyoxi_client``。Fixture data is a fictional
lending-library catalogue (§0: no materials-science domain vocabulary).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from asterism.dataset_summary import dataset_summary
from asterism.substrate import (
    CANONICAL_GRAPH_BASE,
    CONTROL_GRAPH_IRI,
    STATUS_PREDICATE,
    STATUS_PROMOTED,
    canonical_graph_iri,
    ontology_graph_iri,
)

pyoxigraph = pytest.importorskip("pyoxigraph")

EX = "https://ex/library#"
BOOK_CLASS = EX + "Book"
INGESTION_CLASS = EX + "IngestionActivity"
PROV = "http://www.w3.org/ns/prov#"
PROV_ACTIVITY = PROV + "Activity"

DATASET_ID = "library-catalogue-aaaa"
GRAPH = canonical_graph_iri(DATASET_ID) + "/v1"

# データの種類 (Book) 2 件・PROV そのもの (prov:Activity) の実例 1 件・
# データセットの ontology が prov:Activity のサブクラスとして定義した
# 「取り込み活動」の実例 1 件 — どちらも「この中のもの」ではないので除く。
_TTL = f"""
@prefix ex: <{EX}> .
@prefix prov: <{PROV}> .

<https://ex/library/book/1> a ex:Book .
<https://ex/library/book/2> a ex:Book .
<https://ex/library/activity/1> a prov:Activity .
<https://ex/library/activity/2> a ex:IngestionActivity, prov:Activity .
"""

_ONTOLOGY_TTL = f"""
@prefix ex: <{EX}> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix prov: <{PROV}> .

ex:IngestionActivity rdfs:subClassOf prov:Activity .
"""


def _pyoxi_client(graphs: dict[str, str]):
    """``test_class_schema.py``'s ``_pyoxi_client`` と同じ形。"""
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
                    if isinstance(term, pyoxigraph.NamedNode):
                        row[name] = {"type": "uri", "value": term.value}
                    elif isinstance(term, pyoxigraph.Literal):
                        cell = {"type": "literal", "value": term.value}
                        if term.language:
                            cell["xml:lang"] = term.language
                        elif term.datatype:
                            cell["datatype"] = term.datatype.value
                        row[name] = cell
                bindings.append(row)
            return {"results": {"bindings": bindings}}

    return _C()


def _write_registry(registry_root: Path) -> None:
    dest = registry_root / DATASET_ID
    dest.mkdir(parents=True)
    meta = {
        "id": DATASET_ID,
        "name": "貸し出し目録",
        "origin": "own",
        "promoted": True,
        "promoted_at": "2024-01-01",
        "version": 1,
    }
    (dest / "meta.json").write_text(json.dumps(meta), encoding="utf-8")


async def test_prov_activity_and_its_dataset_subclass_are_excluded_from_classes(
    tmp_path: Path,
) -> None:
    _write_registry(tmp_path)
    client = _pyoxi_client(
        {
            GRAPH: _TTL,
            ontology_graph_iri(DATASET_ID): _ONTOLOGY_TTL,
        }
    )

    result = await dataset_summary(client, tmp_path, DATASET_ID)

    assert result is not None
    class_iris = {c["class_iri"] for c in result["classes"]}
    assert class_iris == {BOOK_CLASS}
    assert PROV_ACTIVITY not in class_iris
    assert INGESTION_CLASS not in class_iris
    book = next(c for c in result["classes"] if c["class_iri"] == BOOK_CLASS)
    assert book["count"] == 2
