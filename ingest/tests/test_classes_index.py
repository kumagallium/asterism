"""Tests for ``asterism.classes_index.classes_index`` (契約メモ
contract_pr_f9.md §2.1、担当 api).

Backed by a real ``pyoxigraph.Store`` — same fixture shape as
``test_dataset_summary.py``. Fixture data spans two unrelated fictional
domains (§0: no materials-science domain vocabulary).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from asterism.classes_index import classes_index
from asterism.substrate import (
    CANONICAL_GRAPH_BASE,
    CONTROL_GRAPH_IRI,
    STATUS_PREDICATE,
    STATUS_PROMOTED,
    canonical_graph_iri,
)

pyoxigraph = pytest.importorskip("pyoxigraph")

PROV = "http://www.w3.org/ns/prov#"
PROV_ACTIVITY = PROV + "Activity"

EX_LIB = "https://ex/library#"
BOOK_CLASS = EX_LIB + "Book"
LIB_DATASET = "library-catalogue-aaaa"
LIB_GRAPH = canonical_graph_iri(LIB_DATASET) + "/v1"
_LIB_TTL = f"""
@prefix ex: <{EX_LIB}> .
@prefix prov: <{PROV}> .

<https://ex/library/book/1> a ex:Book .
<https://ex/library/book/2> a ex:Book .
<https://ex/library/book/3> a ex:Book .
<https://ex/library/activity/1> a prov:Activity .
"""

EX_SEED = "https://ex/seed#"
SEED_CLASS = EX_SEED + "Seed"
SEED_DATASET = "seed-catalogue-bbbb"
SEED_GRAPH = canonical_graph_iri(SEED_DATASET) + "/v1"
_SEED_TTL = f"""
@prefix ex: <{EX_SEED}> .

<https://ex/seed/1> a ex:Seed .
<https://ex/seed/2> a ex:Seed .
"""

DRAFT_DATASET = "draft-only-cccc"


def _pyoxi_client(graphs: dict[str, str]):
    """``test_dataset_summary.py``'s ``_pyoxi_client`` と同じ形。"""
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


def _write_dataset(
    registry_root: Path, dataset_id: str, name: str, *, promoted: bool = True
) -> None:
    dest = registry_root / dataset_id
    dest.mkdir(parents=True)
    meta = {"id": dataset_id, "name": name, "promoted": promoted, "version": 1}
    (dest / "meta.json").write_text(json.dumps(meta), encoding="utf-8")


async def test_classes_index_unions_classes_from_every_promoted_dataset(
    tmp_path: Path,
) -> None:
    _write_dataset(tmp_path, LIB_DATASET, "貸し出し目録")
    _write_dataset(tmp_path, SEED_DATASET, "種苗カタログ")
    client = _pyoxi_client({LIB_GRAPH: _LIB_TTL, SEED_GRAPH: _SEED_TTL})

    entries = await classes_index(client, tmp_path)

    by_iri = {e["class_iri"]: e for e in entries}
    assert set(by_iri) == {BOOK_CLASS, SEED_CLASS}
    assert PROV_ACTIVITY not in by_iri
    assert by_iri[BOOK_CLASS]["count"] == 3
    assert by_iri[BOOK_CLASS]["dataset_id"] == LIB_DATASET
    assert by_iri[SEED_CLASS]["count"] == 2
    assert by_iri[SEED_CLASS]["dataset_id"] == SEED_DATASET


async def test_classes_index_orders_by_count_desc_then_name(tmp_path: Path) -> None:
    _write_dataset(tmp_path, LIB_DATASET, "貸し出し目録")
    _write_dataset(tmp_path, SEED_DATASET, "種苗カタログ")
    client = _pyoxi_client({LIB_GRAPH: _LIB_TTL, SEED_GRAPH: _SEED_TTL})

    entries = await classes_index(client, tmp_path)

    # Book (3 件) が Seed (2 件) より先 — 件数の多い順。
    assert [e["class_iri"] for e in entries] == [BOOK_CLASS, SEED_CLASS]


async def test_classes_index_ties_on_count_break_by_name(tmp_path: Path) -> None:
    # Book/Seed とも 2 件ずつになるフィクスチャ — 件数が同じときは名前順
    # (ローカル名の人間化 "Book" < "Seed")。
    tied_lib_ttl = f"""
@prefix ex: <{EX_LIB}> .

<https://ex/library/book/1> a ex:Book .
<https://ex/library/book/2> a ex:Book .
"""
    _write_dataset(tmp_path, LIB_DATASET, "貸し出し目録")
    _write_dataset(tmp_path, SEED_DATASET, "種苗カタログ")
    client = _pyoxi_client({LIB_GRAPH: tied_lib_ttl, SEED_GRAPH: _SEED_TTL})

    entries = await classes_index(client, tmp_path)

    assert [e["class_iri"] for e in entries] == [BOOK_CLASS, SEED_CLASS]


async def test_classes_index_excludes_non_promoted_datasets(tmp_path: Path) -> None:
    _write_dataset(tmp_path, LIB_DATASET, "貸し出し目録")
    _write_dataset(tmp_path, DRAFT_DATASET, "下書き中", promoted=False)
    client = _pyoxi_client({LIB_GRAPH: _LIB_TTL})

    entries = await classes_index(client, tmp_path)

    assert {e["dataset_id"] for e in entries} == {LIB_DATASET}


async def test_classes_index_is_empty_when_there_are_no_datasets(tmp_path: Path) -> None:
    client = _pyoxi_client({})
    assert await classes_index(client, tmp_path) == []


async def test_classes_index_none_registry_root_is_empty(tmp_path: Path) -> None:
    client = _pyoxi_client({})
    assert await classes_index(client, None) == []
