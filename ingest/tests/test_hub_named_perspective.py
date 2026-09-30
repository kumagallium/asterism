"""名前つきの perspective のハブ graph（``…/canonical/crosswalk/<名前>``）でも、
ハブの判定・メンバーの解決・出どころが引けること。

背景: ``dataset_id_of_canonical_graph`` は ``crosswalk/<名前>`` に ``None`` を返す
（スラッシュを含む値は dataset id ではない — それを id として扱って
``schema_summary`` が落ちた）。ハブの判定がその関数の返り値に乗っていると、
名前つきの perspective（☑ から自動で作られるものは全てこれ）のハブが 1 つも
ハブとして認識されなくなる。ハブの判定は graph の名前を直接読む。

架空データ: 2 つの台帳（ledger-a/b）と、名前つきの perspective のハブ graph。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from asterism import crosswalk_runtime
from asterism.subject_tools import (
    crosswalk_registry_id_of_hub_graph,
    hub_of_subject,
    subject_hub_members,
    subject_sources,
)
from asterism.substrate import (
    CANONICAL_GRAPH_BASE,
    CONTROL_GRAPH_IRI,
    STATUS_PREDICATE,
    STATUS_PROMOTED,
    canonical_graph_iri,
    dataset_id_of_canonical_graph,
    hub_perspective_name,
    is_hub_graph,
)

pyoxigraph = pytest.importorskip("pyoxigraph")

PERSPECTIVE = "shared-things"
NAMED_HUB_GRAPH = crosswalk_runtime.crosswalk_graph_iri(PERSPECTIVE)
LEGACY_HUB_GRAPH = crosswalk_runtime.crosswalk_graph_iri()

EX_A = "https://ex/ledger-a#"
EX_B = "https://ex/ledger-b#"
EX_HUB = "https://ex/shared#"
DATASET_A = "ledger-a"
DATASET_B = "ledger-b"
GRAPH_A = canonical_graph_iri(DATASET_A) + "/v1"
GRAPH_B = canonical_graph_iri(DATASET_B) + "/v1"
MEMBER_A = "https://ex/ledger-a/resource/entry-1"
MEMBER_B = "https://ex/ledger-b/resource/entry-9"
HUB_IRI = "https://ex/shared/resource/thing-1"

_TTL_A = f"<{MEMBER_A}> a <{EX_A}Entry> .\n"
_TTL_B = f"<{MEMBER_B}> a <{EX_B}Entry> .\n"
_TTL_HUB = f"""
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
<{HUB_IRI}> a <{EX_HUB}Thing> ; rdfs:label "同じもの" .
<{MEMBER_A}> <{EX_HUB}hasThing> <{HUB_IRI}> .
<{MEMBER_B}> <{EX_HUB}hasThing> <{HUB_IRI}> .
"""


def _client(graphs: dict[str, str]):
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
            if isinstance(result, bool):
                return {"head": {}, "boolean": result}
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


def _named_hub_client():
    return _client({GRAPH_A: _TTL_A, GRAPH_B: _TTL_B, NAMED_HUB_GRAPH: _TTL_HUB})


def _write_registry(root: Path) -> None:
    rows = (
        (DATASET_A, {"id": DATASET_A, "name": "台帳A", "promoted": True, "version": 1}),
        (DATASET_B, {"id": DATASET_B, "name": "台帳B", "promoted": True, "version": 1}),
        (
            crosswalk_runtime.crosswalk_registry_id(PERSPECTIVE),
            {
                "id": crosswalk_runtime.crosswalk_registry_id(PERSPECTIVE),
                "name": "同じものの束",
                "promoted": True,
                "crosswalk_perspective_id": PERSPECTIVE,
            },
        ),
    )
    for dataset_id, meta in rows:
        dest = root / dataset_id
        dest.mkdir(parents=True)
        (dest / "meta.json").write_text(json.dumps(meta), encoding="utf-8")


# ---------------------------------------------------------------------------
# 判定（純関数）
# ---------------------------------------------------------------------------


def test_named_perspective_hub_graph_is_a_hub_but_not_a_dataset() -> None:
    assert dataset_id_of_canonical_graph(NAMED_HUB_GRAPH) is None
    assert is_hub_graph(NAMED_HUB_GRAPH) is True
    assert hub_perspective_name(NAMED_HUB_GRAPH) == PERSPECTIVE


def test_legacy_hub_graph_has_an_empty_name() -> None:
    assert is_hub_graph(LEGACY_HUB_GRAPH) is True
    assert hub_perspective_name(LEGACY_HUB_GRAPH) == ""


def test_alignment_graph_is_not_a_hub() -> None:
    """perspective どうしの対応づけを置く graph は、昇格済みで名前も
    ``crosswalk/…`` だが、共有実体を持たない — ハブではない。"""
    assert is_hub_graph(crosswalk_runtime.ALIGNMENT_GRAPH) is False
    assert hub_perspective_name(crosswalk_runtime.ALIGNMENT_GRAPH) is None


def test_ordinary_graphs_are_not_hubs() -> None:
    for iri in (
        GRAPH_A,
        canonical_graph_iri("crosswalk-notes"),
        CANONICAL_GRAPH_BASE + "crosswalk/Bad Name",
        "https://ex/elsewhere/crosswalk",
    ):
        assert is_hub_graph(iri) is False, iri


def test_registry_id_of_a_named_hub_graph() -> None:
    assert crosswalk_registry_id_of_hub_graph(NAMED_HUB_GRAPH) == (
        crosswalk_runtime.crosswalk_registry_id(PERSPECTIVE)
    )
    assert crosswalk_registry_id_of_hub_graph(LEGACY_HUB_GRAPH) == (
        crosswalk_runtime.crosswalk_registry_id()
    )
    assert crosswalk_registry_id_of_hub_graph(GRAPH_A) is None


# ---------------------------------------------------------------------------
# 実ストア
# ---------------------------------------------------------------------------


async def test_hub_of_subject_recognises_a_hub_in_a_named_perspective() -> None:
    out = await hub_of_subject(_named_hub_client(), HUB_IRI)
    assert out == {"hub_iri": HUB_IRI, "graph": NAMED_HUB_GRAPH, "perspective_id": PERSPECTIVE}


async def test_hub_of_subject_finds_the_hub_from_a_member_in_a_named_perspective() -> None:
    out = await hub_of_subject(_named_hub_client(), MEMBER_A)
    assert out is not None
    assert out["hub_iri"] == HUB_IRI
    assert out["perspective_id"] == PERSPECTIVE
    assert out["via_parent"] is None


async def test_subject_hub_members_in_a_named_perspective(tmp_path: Path) -> None:
    _write_registry(tmp_path)
    out = await subject_hub_members(_named_hub_client(), HUB_IRI, registry_root=tmp_path)
    by_iri = {i["subject_iri"]: i for i in out["items"]}
    assert set(by_iri) == {MEMBER_A, MEMBER_B}
    assert by_iri[MEMBER_A]["dataset_label"] == "台帳A"
    assert by_iri[MEMBER_B]["dataset_label"] == "台帳B"


async def test_subject_sources_names_the_perspective_not_the_graph(tmp_path: Path) -> None:
    """ハブ自身の三つ組の出どころは perspective の名前で出る（graph の名前や
    registry の id をそのまま見せない）。メンバーの出どころも合わせて出る。"""
    _write_registry(tmp_path)
    out = await subject_sources(_named_hub_client(), HUB_IRI, registry_root=tmp_path)
    categories = [str(i["category"]) for i in out["items"]]
    assert any("同じものの束" in c for c in categories), categories
    assert not any("crosswalk" in c for c in categories), categories
