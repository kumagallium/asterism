"""ハブの graph が 2 つ以上あっても、対象のハブが後ろの graph にあるとき
「ハブではない」と誤判定されないこと（契約メモ §1）。

背景: ``subject_member_facts`` / ``subject_sources`` / ``subject_hub_members``
は、ハブの graph を「最初の 1 つ」だけ見て、その graph に対してだけハブ実体の
有無を問い合わせていた。☑ を 2 種類の列に付けると perspective ごとにハブの
graph ができ、2 つ以上になる — 主語のハブ実体が 2 つ目以降の graph にあると
「ハブではない」という誤判定になる。

架空データ: 台帳 2 つ（``ledger-a``/``ledger-b``、各 1 メンバー）と、名前つき
perspective のハブ graph を 2 つ（``aa-things`` が graph の IRI で前、
``bb-things`` が後ろ）。検査対象のハブ実体は後ろの graph（``bb-things``）に、
別のハブ実体は前の graph（``aa-things``）に置く。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from asterism import crosswalk_runtime
from asterism.subject_tools import (
    SubjectToolError,
    subject_hub_members,
    subject_member_facts,
    subject_sources,
)
from asterism.substrate import (
    CANONICAL_GRAPH_BASE,
    CONTROL_GRAPH_IRI,
    STATUS_PREDICATE,
    STATUS_PROMOTED,
    canonical_graph_iri,
)

pyoxigraph = pytest.importorskip("pyoxigraph")

# graph の IRI の辞書順で "aa-things" が前・"bb-things" が後ろ。
FRONT_PERSPECTIVE = "aa-things"
BACK_PERSPECTIVE = "bb-things"
FRONT_HUB_GRAPH = crosswalk_runtime.crosswalk_graph_iri(FRONT_PERSPECTIVE)
BACK_HUB_GRAPH = crosswalk_runtime.crosswalk_graph_iri(BACK_PERSPECTIVE)
assert FRONT_HUB_GRAPH < BACK_HUB_GRAPH

EX_A = "https://ex/ledger-a#"
EX_B = "https://ex/ledger-b#"
EX_C = "https://ex/ledger-c#"
EX_HUB = "https://ex/shared#"
EX_OTHER = "https://ex/other-shared#"
DATASET_A = "ledger-a"
DATASET_B = "ledger-b"
DATASET_C = "ledger-c"
GRAPH_A = canonical_graph_iri(DATASET_A) + "/v1"
GRAPH_B = canonical_graph_iri(DATASET_B) + "/v1"
GRAPH_C = canonical_graph_iri(DATASET_C) + "/v1"
MEMBER_A = "https://ex/ledger-a/resource/entry-1"
MEMBER_B = "https://ex/ledger-b/resource/entry-9"
MEMBER_C = "https://ex/ledger-c/resource/entry-5"
# 検査対象のハブ実体（後ろの graph）。
HUB_IRI = "https://ex/shared/resource/thing-1"
# 前の graph に置く、別のハブ実体（検査対象とは無関係）。
OTHER_HUB_IRI = "https://ex/other-shared/resource/thing-9"

_TTL_A = f"<{MEMBER_A}> a <{EX_A}Entry> .\n"
_TTL_B = f"<{MEMBER_B}> a <{EX_B}Entry> .\n"
_TTL_C = f"<{MEMBER_C}> a <{EX_C}Entry> .\n"
_TTL_FRONT_HUB = f"""
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
<{OTHER_HUB_IRI}> a <{EX_OTHER}Thing> ; rdfs:label "別のもの" .
<{MEMBER_C}> <{EX_OTHER}hasThing> <{OTHER_HUB_IRI}> .
"""
_TTL_BACK_HUB = f"""
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


def _multi_hub_client():
    return _client(
        {
            GRAPH_A: _TTL_A,
            GRAPH_B: _TTL_B,
            GRAPH_C: _TTL_C,
            FRONT_HUB_GRAPH: _TTL_FRONT_HUB,
            BACK_HUB_GRAPH: _TTL_BACK_HUB,
        }
    )


def _no_hub_client():
    return _client({GRAPH_A: _TTL_A, GRAPH_B: _TTL_B})


def _write_registry(root: Path) -> None:
    rows = (
        (DATASET_A, {"id": DATASET_A, "name": "台帳A", "promoted": True, "version": 1}),
        (DATASET_B, {"id": DATASET_B, "name": "台帳B", "promoted": True, "version": 1}),
        (DATASET_C, {"id": DATASET_C, "name": "台帳C", "promoted": True, "version": 1}),
    )
    for dataset_id, meta in rows:
        dest = root / dataset_id
        dest.mkdir(parents=True)
        (dest / "meta.json").write_text(json.dumps(meta), encoding="utf-8")


# ---------------------------------------------------------------------------
# ハブの graph が 2 つ — 対象は後ろの graph
# ---------------------------------------------------------------------------


async def test_subject_hub_members_finds_hub_in_the_later_graph(tmp_path: Path) -> None:
    _write_registry(tmp_path)
    out = await subject_hub_members(_multi_hub_client(), HUB_IRI, registry_root=tmp_path)
    by_iri = {i["subject_iri"]: i for i in out["items"]}
    assert set(by_iri) == {MEMBER_A, MEMBER_B}
    assert by_iri[MEMBER_A]["dataset_label"] == "台帳A"
    assert by_iri[MEMBER_B]["dataset_label"] == "台帳B"


async def test_subject_member_facts_resolves_hub_in_the_later_graph(tmp_path: Path) -> None:
    _write_registry(tmp_path)
    out = await subject_member_facts(_multi_hub_client(), HUB_IRI, MEMBER_A, registry_root=tmp_path)
    assert out["tool"] == "subject_facts"


async def test_subject_member_facts_rejects_a_member_of_a_different_hub(tmp_path: Path) -> None:
    _write_registry(tmp_path)
    with pytest.raises(SubjectToolError):
        await subject_member_facts(_multi_hub_client(), HUB_IRI, MEMBER_C, registry_root=tmp_path)


async def test_subject_sources_names_member_datasets_for_the_later_hub(tmp_path: Path) -> None:
    _write_registry(tmp_path)
    out = await subject_sources(_multi_hub_client(), HUB_IRI, registry_root=tmp_path)
    categories = [str(i["category"]) for i in out["items"]]
    assert any("台帳A" in c for c in categories), categories
    assert any("台帳B" in c for c in categories), categories
    # 別のハブ（前の graph）のメンバーの出どころは混ざらない
    assert not any("台帳C" in c for c in categories), categories


# ---------------------------------------------------------------------------
# 前の graph のハブでも同じく引ける（順番に依らない）
# ---------------------------------------------------------------------------


async def test_subject_hub_members_still_finds_the_earlier_hub(tmp_path: Path) -> None:
    _write_registry(tmp_path)
    out = await subject_hub_members(_multi_hub_client(), OTHER_HUB_IRI, registry_root=tmp_path)
    by_iri = {i["subject_iri"]: i for i in out["items"]}
    assert set(by_iri) == {MEMBER_C}


async def test_subject_member_facts_still_resolves_the_earlier_hub(tmp_path: Path) -> None:
    _write_registry(tmp_path)
    out = await subject_member_facts(
        _multi_hub_client(), OTHER_HUB_IRI, MEMBER_C, registry_root=tmp_path
    )
    assert out["tool"] == "subject_facts"


async def test_subject_sources_names_member_datasets_for_the_earlier_hub(tmp_path: Path) -> None:
    _write_registry(tmp_path)
    out = await subject_sources(_multi_hub_client(), OTHER_HUB_IRI, registry_root=tmp_path)
    categories = [str(i["category"]) for i in out["items"]]
    assert any("台帳C" in c for c in categories), categories
    # 別のハブ（後ろの graph）のメンバーの出どころは混ざらない
    assert not any("台帳A" in c for c in categories), categories
    assert not any("台帳B" in c for c in categories), categories


# ---------------------------------------------------------------------------
# ハブの graph が 1 つも無いストア — 今と同じ振る舞い
# ---------------------------------------------------------------------------


async def test_no_hub_graph_subject_hub_members_is_empty(tmp_path: Path) -> None:
    out = await subject_hub_members(_no_hub_client(), MEMBER_A, registry_root=tmp_path)
    assert out["count"] == 0
    assert out["items"] == []


async def test_no_hub_graph_subject_member_facts_rejects(tmp_path: Path) -> None:
    with pytest.raises(SubjectToolError):
        await subject_member_facts(_no_hub_client(), MEMBER_A, MEMBER_B, registry_root=tmp_path)


async def test_no_hub_graph_subject_sources_does_not_error(tmp_path: Path) -> None:
    out = await subject_sources(_no_hub_client(), MEMBER_A, registry_root=tmp_path)
    assert out["tool"] == "subject_sources"
