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


# ---------------------------------------------------------------------------
# sample_notice（見本のページの「新しくなった・新しくしていない」の知らせ）

_RAW_WORDS = ("mapping.yaml", "model.yaml", "diagram.md", "query_tools", "life_expectancy")


def _stamp(**over):
    stamp = {
        "seq": 3,
        "revision": "08c3557051b4b61b",
        "applied_at": "2026-09-30T00:00:00+00:00",
        "units": {},
        "held": [],
    }
    stamp.update(over)
    return stamp


def test_sample_notice_is_none_unless_the_sample() -> None:
    from asterism.dataset_summary import sample_notice

    assert sample_notice({"sample": _stamp()}, is_demo=False) is None


def test_sample_notice_without_a_stamp_is_empty_but_present() -> None:
    from asterism.dataset_summary import sample_notice

    notice = sample_notice({}, is_demo=True)
    assert notice == {
        "updated": None,
        "held": [],
        "overridable": [],
        "restorable": None,
        "seq": None,
        "revision": None,
    }


def test_sample_notice_carries_what_the_screen_needs_and_no_raw_identifiers() -> None:
    from asterism.dataset_summary import sample_notice

    meta = {
        "sample": _stamp(
            last_update={
                "seq": 3,
                "at": "2026-09-30T01:00:00+00:00",
                "units": ["design", "data"],
                "note": {"ja": "図の箱を日本語にした", "en": "Named the boxes"},
            },
            held=[
                {"unit": "tools", "reason": "edited", "titles": ["国の平均寿命の推移"]},
                {"unit": "tools", "reason": "edited"},
                {"unit": "design", "reason": "decisions", "detail": "column-decisions.json"},
            ],
            backups=[
                {"at": "2026-09-30T02:00:00+00:00", "units": ["name"], "dir": "sample-backup/x/"},
                {"at": "2026-09-29T02:00:00+00:00", "units": ["design"], "dir": "sample-backup/y/"},
            ],
        )
    }
    notice = sample_notice(meta, is_demo=True)
    assert notice is not None
    assert notice["seq"] == 3
    assert notice["revision"] == "08c3557051b4b61b"
    assert notice["updated"] == {
        "at": "2026-09-30T01:00:00+00:00",
        "note": {"ja": "図の箱を日本語にした", "en": "Named the boxes"},
        "units": ["design", "data"],
    }
    assert notice["held"] == [
        {"unit": "tools", "reason": "edited", "count": 2, "titles": ["国の平均寿命の推移"]},
        {"unit": "design", "reason": "decisions", "count": 1},
    ]
    assert notice["overridable"] == ["tools", "design"]
    # 控えは新しいものだけ。dir は画面に要らない
    assert notice["restorable"] == {"at": "2026-09-30T02:00:00+00:00", "units": ["name"]}
    text = json.dumps(notice, ensure_ascii=False)
    for word in (*_RAW_WORDS, "column-decisions", "sample-backup"):
        assert word not in text


@pytest.mark.parametrize(
    "held",
    [
        [{"unit": "design", "reason": "appended"}],
        [{"unit": "design", "reason": "ids_move"}],
        [{"unit": "design", "reason": "edited"}, {"unit": "design", "reason": "ids_unknown"}],
        # データの群は、同じ理由が design・tools にもあっても置き換えない
        [
            {"unit": "design", "reason": "edited"},
            {"unit": "data", "reason": "edited"},
            {"unit": "tools", "reason": "edited"},
        ],
    ],
)
def test_sample_notice_overridable_excludes_what_would_lose_the_users_data(held) -> None:
    from asterism.dataset_summary import sample_notice

    notice = sample_notice({"sample": _stamp(held=held)}, is_demo=True)
    assert notice is not None
    assert notice["overridable"] == []


def test_sample_notice_overridable_is_per_unit() -> None:
    from asterism.dataset_summary import sample_notice

    held = [
        {"unit": "design", "reason": "edited"},
        {"unit": "name", "reason": "edited"},
        {"unit": "description", "reason": "ids_move"},
    ]
    notice = sample_notice({"sample": _stamp(held=held)}, is_demo=True)
    assert notice is not None
    assert notice["overridable"] == ["design", "name"]


def test_sample_notice_ignores_malformed_stamp_parts() -> None:
    from asterism.dataset_summary import sample_notice

    notice = sample_notice(
        {"sample": {"seq": True, "held": ["x", {"unit": 1}], "backups": [1], "last_update": "x"}},
        is_demo=True,
    )
    assert notice is not None
    assert notice["held"] == []
    assert notice["restorable"] is None
    assert notice["updated"] is None
    assert notice["seq"] is None


async def test_dataset_summary_carries_the_notice_only_for_the_sample(tmp_path: Path) -> None:
    from asterism.dataset_summary import DEMO_DATASET_ID

    _write_registry(tmp_path)
    plain = await dataset_summary(_pyoxi_client({}), tmp_path, DATASET_ID)
    assert plain is not None
    assert plain["is_demo"] is False
    assert plain["sample_notice"] is None

    dest = tmp_path / DEMO_DATASET_ID
    dest.mkdir()
    meta = {"id": DEMO_DATASET_ID, "name": "見本", "origin": "open", "sample": _stamp()}
    (dest / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    demo = await dataset_summary(_pyoxi_client({}), tmp_path, DEMO_DATASET_ID)
    assert demo is not None
    assert demo["sample_notice"]["seq"] == 3
