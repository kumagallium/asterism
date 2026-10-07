"""Tests for ``asterism.subject_tools.subject_neighbors`` (object-cards-ui.md O67).

1 件の隣を 1 段だけ返す読み手。実物の ``pyoxigraph.Store`` で動かす（他の ingest の
テストと同じ流儀）。題材は架空の分野（気象観測所と観測記録）— 分野語を実装に書かない。
"""

from __future__ import annotations

import pytest

from asterism import subject_tools
from asterism.subject_tools import _label_lookup, subject_facts, subject_neighbors
from asterism.subjects import normalize_set_spec
from asterism.substrate import (
    CANONICAL_GRAPH_BASE,
    CONTROL_GRAPH_IRI,
    ONTOLOGY_GRAPH_BASE,
    STATUS_PREDICATE,
    STATUS_PROMOTED,
    canonical_graph_iri,
    canonical_graphs,
)

pyoxigraph = pytest.importorskip("pyoxigraph")


def _pyoxi_client(graphs: dict[str, str]):
    """{graph_iri: ttl}。CANONICAL_GRAPH_BASE 配下は promoted 印を付ける。
    ``client.queries`` に投げた SPARQL を残す（「SPARQL を 1 本も投げない」の検査用）。"""
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
        def __init__(self) -> None:
            self.queries: list[str] = []

        async def sparql_select(self, query: str) -> dict:
            self.queries.append(query)
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
                        row[name] = cell
                bindings.append(row)
            return {"results": {"bindings": bindings}}

    return _C()


WX = "https://ex/wx#"
R = "https://ex/wx/resource/"
PROV = "http://www.w3.org/ns/prov#"
XW = "https://kumagallium.github.io/asterism/crosswalk/ontology#"
DATASET = "weather-stations"
GRAPH = canonical_graph_iri(DATASET) + "/v1"
HUB_GRAPH = canonical_graph_iri("crosswalk")
ONTO_GRAPH = ONTOLOGY_GRAPH_BASE + DATASET

STATION = R + "station-1"
REGION = R + "region-1"
HUB = R + "shared-1"

PREFIXES = (
    f"@prefix wx: <{WX}> .\n@prefix r: <{R}> .\n@prefix prov: <{PROV}> .\n"
    f"@prefix xw: <{XW}> .\n@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
)


def _data(*, observations: int = 8) -> str:
    lines = [
        PREFIXES,
        'r:station-1 a wx:Station ; rdfs:label "Harbor Station" ; wx:locatedIn r:region-1 ;'
        ' wx:operator r:agency-1 ; wx:sharedWith r:shared-1 ; wx:note "free text" .',
        'r:region-1 a wx:Region ; rdfs:label "North Coast" .',
        "r:agency-1 a wx:Agency .",
        # 入る線: 8 件の束（代表の種類の検査: obs-1 は PROV の型も、obs-2 は業務の型を 2 つ持つ）
        "r:alert-1 a wx:Alert ; wx:about r:station-1 .",
        "r:link-1 a xw:CrosswalkLink ; wx:about r:station-1 .",
        "r:untyped-1 wx:ref r:station-1 .",
        "r:station-1 wx:ref r:station-1 .",
    ]
    for i in range(1, observations + 1):
        extra = ""
        if i == 1:
            extra = " , prov:Entity"
        if i == 2:
            extra = " , wx:Reading"
        lines.append(f"r:obs-{i} a wx:Observation{extra} ; wx:observedAt r:station-1 .")
    return "\n".join(lines)


ONTOLOGY = (
    PREFIXES + 'wx:locatedIn rdfs:label "located in" .\nwx:observedAt rdfs:label "seen at" .\n'
)


@pytest.fixture(autouse=True)
def _ontology_graphs_listed(monkeypatch: pytest.MonkeyPatch) -> None:
    """実機と同じく、オントロジーの graph を引き先に入れる（テスト用の pyoxigraph は
    ``GRAPH ?g {}`` で graph を列挙しないため、そのままではストアの rdfs:label の段が
    試されない）。"""

    async def _onto(_client: object) -> list[str]:
        return [ONTO_GRAPH]

    async def _readable(client: object) -> set[str]:
        return set(await canonical_graphs(client)) | {ONTO_GRAPH}  # type: ignore[arg-type]

    monkeypatch.setattr("asterism.subject_tools.ontology_graphs", _onto)
    monkeypatch.setattr("asterism.subject_tools.readable_graph_iris", _readable)

HUB_TTL = PREFIXES + "r:shared-1 a xw:Composition .\n"


def _client(**kw):
    return _pyoxi_client({GRAPH: _data(**kw), ONTO_GRAPH: ONTOLOGY, HUB_GRAPH: HUB_TTL})


def _group(out: dict, direction: str, predicate: str, class_iri: str | None):
    found = [
        g
        for g in out["groups"]
        if g["direction"] == direction
        and g["predicate_iri"] == predicate
        and g["class_iri"] == class_iri
    ]
    assert len(found) == 1, (direction, predicate, class_iri, [g["key"] for g in out["groups"]])
    return found[0]


async def test_outgoing_lines_are_one_per_neighbour_and_skip_type_literals_and_self() -> None:
    out = await subject_neighbors(_client(), STATION)
    outs = [g for g in out["groups"] if g["direction"] == "out"]
    assert {g["predicate_iri"] for g in outs} == {
        WX + "locatedIn",
        WX + "operator",
        WX + "sharedWith",
    }
    region = _group(out, "out", WX + "locatedIn", WX + "Region")
    assert region["count"] == 1
    assert region["items"] == [
        {"iri": REGION, "label": "North Coast", "class_iri": WX + "Region", "is_hub": False}
    ]
    assert region["set_spec"] is None
    assert region["key"] == f"out|{WX}locatedIn|{WX}Region"


async def test_incoming_bundle_gives_exact_count_and_set_spec() -> None:
    out = await subject_neighbors(_client(), STATION)
    bundle = _group(out, "in", WX + "observedAt", WX + "Observation")
    # obs-1（PROV の型も持つ）・obs-2（型を 2 つ持つ）も 1 回ずつ → 8
    assert bundle["count"] == 8
    assert bundle["items"] == []
    assert bundle["set_spec"] == {
        "class": WX + "Observation",
        "where": [{"property": WX + "observedAt", "iri": STATION}],
        "order_by": None,
        "limit": 20,
        "source_scope": "all",
    }
    # そのまま normalize_set_spec を通る
    assert normalize_set_spec(bundle["set_spec"]) == bundle["set_spec"]
    # 他の群にこれらの主語が別の種類で紛れ込まない
    assert not [g for g in out["groups"] if g["class_iri"] in (WX + "Reading", PROV + "Entity")]


async def test_incoming_inline_group_lists_items_and_excludes_crosswalk_links() -> None:
    out = await subject_neighbors(_client(), STATION)
    alert = _group(out, "in", WX + "about", WX + "Alert")
    assert alert["count"] == 1
    assert [i["iri"] for i in alert["items"]] == [R + "alert-1"]
    assert alert["set_spec"] is not None or alert["items"]
    # CrosswalkLink の主語は群にも数にも出ない
    about = [g for g in out["groups"] if g["predicate_iri"] == WX + "about"]
    assert [g["class_iri"] for g in about] == [WX + "Alert"]
    assert all(i["iri"] != R + "link-1" for g in out["groups"] for i in g["items"])


async def test_untyped_neighbour_has_null_kind_and_self_reference_is_ignored() -> None:
    out = await subject_neighbors(_client(), STATION)
    ref = _group(out, "in", WX + "ref", None)
    assert ref["count"] == 1
    assert [i["iri"] for i in ref["items"]] == [R + "untyped-1"]
    assert ref["class_label"] is None
    assert ref["key"] == f"in|{WX}ref|"


async def test_representative_kind_prefers_non_prov_then_lexical_first() -> None:
    out = await subject_neighbors(_client(observations=2), STATION)
    # obs-1 は {PROV Entity, wx:Observation}、obs-2 は {Observation, Reading}
    g = _group(out, "in", WX + "observedAt", WX + "Observation")
    assert g["count"] == 2
    assert {i["iri"] for i in g["items"]} == {R + "obs-1", R + "obs-2"}
    assert all(i["class_iri"] == WX + "Observation" for i in g["items"])


async def test_prov_only_subject_keeps_the_prov_kind_and_bundle_has_no_set_spec() -> None:
    prov_lines = [PREFIXES, "r:station-1 a wx:Station ."]
    for i in range(8):
        prov_lines.append(f"r:act-{i} a prov:Activity ; wx:used r:station-1 .")
    client = _pyoxi_client({GRAPH: "\n".join(prov_lines)})
    out = await subject_neighbors(client, STATION)
    g = _group(out, "in", WX + "used", PROV + "Activity")
    assert g["count"] == 8
    assert g["set_spec"] is None
    assert [i["iri"] for i in g["items"]] == [R + f"act-{i}" for i in range(8)]


async def test_outgoing_bundle_gets_sample_and_no_set_spec() -> None:
    lines = [PREFIXES, "r:station-1 a wx:Station ."]
    for i in range(1, 16):
        lines.append(f"r:station-1 wx:covers r:cell-{i:02d} . r:cell-{i:02d} a wx:Cell .")
    out = await subject_neighbors(_pyoxi_client({GRAPH: "\n".join(lines)}), STATION)
    g = _group(out, "out", WX + "covers", WX + "Cell")
    assert g["count"] == 15
    assert g["set_spec"] is None
    assert [i["iri"] for i in g["items"]] == [R + f"cell-{i:02d}" for i in range(1, 13)]


async def test_hub_flag_on_center_and_neighbours() -> None:
    out = await subject_neighbors(_client(), STATION)
    assert out["center"]["is_hub"] is False
    shared = _group(out, "out", WX + "sharedWith", XW + "Composition")
    assert shared["items"][0]["iri"] == HUB
    assert shared["items"][0]["is_hub"] is True
    hub_center = await subject_neighbors(_client(), HUB)
    assert hub_center["center"]["is_hub"] is True


async def test_build_activity_and_link_in_the_hub_graph_are_not_hubs() -> None:
    """ハブの graph には build の prov:Activity と per-link の xw:CrosswalkLink も型つきで
    載る。それらはハブ実体ではない（resolve の is_hub＝_hub_entity_ask と同じ条件）。
    実機 2026-10-07: ハブの隣の「取り込みの記録」がハブの色で描かれた。"""
    hub_ttl = (
        HUB_TTL
        + f"<{HUB}> <{PROV}wasGeneratedBy> <{R}hub-build> .\n"
        + f"<{R}hub-build> a <{PROV}Activity> .\n"
        + f"<{R}link-1> a <{XW}CrosswalkLink> ; <{XW}linkObject> <{HUB}> .\n"
    )
    client = _pyoxi_client({GRAPH: _data(), ONTO_GRAPH: ONTOLOGY, HUB_GRAPH: hub_ttl})
    out = await subject_neighbors(client, HUB)
    assert out["center"]["is_hub"] is True
    flags = {i["iri"]: i["is_hub"] for g in out["groups"] for i in g["items"]}
    assert flags.get(R + "hub-build") is False
    assert R + "link-1" not in flags  # per-link の来歴は隣にもしない


async def test_center_carries_names_and_kind() -> None:
    out = await subject_neighbors(_client(), STATION)
    assert out["found"] is True
    assert out["center"]["iri"] == STATION
    assert out["center"]["label"] == "Harbor Station"
    assert out["center"]["class_iri"] == WX + "Station"
    assert out["center"]["class_label"]


async def test_draft_graphs_are_not_read() -> None:
    draft = PREFIXES + "r:ghost-1 a wx:Observation ; wx:observedAt r:station-1 .\n"
    client = _pyoxi_client({GRAPH: _data(), "https://ex/draft/graph": draft})
    out = await subject_neighbors(client, STATION)
    bundle = _group(out, "in", WX + "observedAt", WX + "Observation")
    assert bundle["count"] == 8


async def test_no_published_graph_sends_no_sparql_beyond_the_listing() -> None:
    client = _pyoxi_client({"https://ex/draft/graph": PREFIXES + "r:a wx:p r:b ."})
    out = await subject_neighbors(client, "https://ex/wx/resource/a")
    assert out == {
        "iri": "https://ex/wx/resource/a",
        "found": False,
        "center": None,
        "groups": [],
        "truncated": False,
    }
    # 読み取り範囲の列挙（control graph）だけ。データへの問い合わせは 0 本
    assert all("CONTROL" in q.upper() or CONTROL_GRAPH_IRI in q for q in client.queries)


async def test_literal_only_subject_is_found_with_no_groups() -> None:
    ttl = PREFIXES + 'r:lonely wx:note "only text" .\n'
    out = await subject_neighbors(_pyoxi_client({GRAPH: ttl}), R + "lonely")
    assert out["found"] is True
    assert out["groups"] == []


async def test_unknown_iri_is_not_found() -> None:
    out = await subject_neighbors(_client(), R + "does-not-exist")
    assert out["found"] is False
    assert out["groups"] == []


async def test_max_groups_truncates() -> None:
    out = await subject_neighbors(_client(), STATION, max_groups=2)
    assert out["truncated"] is True
    assert len(out["groups"]) == 2
    full = await subject_neighbors(_client(), STATION)
    assert full["truncated"] is False
    assert [g["key"] for g in out["groups"]] == [g["key"] for g in full["groups"][:2]]


async def test_order_is_out_before_in_then_by_names_and_deterministic() -> None:
    a = await subject_neighbors(_client(), STATION)
    b = await subject_neighbors(_client(), STATION)
    assert a == b
    directions = [g["direction"] for g in a["groups"]]
    assert directions == sorted(directions, key=lambda d: 0 if d == "out" else 1)
    outs = [g for g in a["groups"] if g["direction"] == "out"]
    assert [g["predicate_label"] for g in outs] == sorted(g["predicate_label"] for g in outs)
    ins = [g for g in a["groups"] if g["direction"] == "in"]
    assert [g["predicate_label"] for g in ins] == sorted(g["predicate_label"] for g in ins)


async def test_neighbour_names_equal_the_page_heading() -> None:
    """図の箱の名前 = 開いた 1 件ページの見出し（api の ``_entity_label`` と同じ手順）。"""
    client = _client()
    out = await subject_neighbors(client, STATION)
    iris = {STATION, REGION, HUB, R + "alert-1", R + "untyped-1", R + "agency-1"}
    canon = set(await canonical_graphs(client))
    graphs = sorted(canon | set(await subject_tools.ontology_graphs(client)))
    expected = await _label_lookup(client, graphs, iris)
    shown = {out["center"]["iri"]: out["center"]["label"]}
    for g in out["groups"]:
        for i in g["items"]:
            shown[i["iri"]] = i["label"]
    assert set(iris) <= set(shown)
    for iri in iris:
        assert shown[iri] == expected[iri], iri


async def test_outgoing_line_names_equal_the_facts_card_names() -> None:
    client = _client()
    out = await subject_neighbors(client, STATION)
    facts = await subject_facts(client, STATION)
    card = {row["property_iri"]: row["property"] for row in facts["items"]}
    outs = [g for g in out["groups"] if g["direction"] == "out"]
    assert outs
    for g in outs:
        assert g["predicate_label"] == card[g["predicate_iri"]], g["predicate_iri"]
    # ストアに名前のある線はその名前
    assert _group(out, "out", WX + "locatedIn", WX + "Region")["predicate_label"] == "located in"


async def test_incoming_line_name_uses_the_subjects_kind() -> None:
    out = await subject_neighbors(_client(), STATION)
    assert _group(out, "in", WX + "observedAt", WX + "Observation")["predicate_label"] == "seen at"
    # 名前の無い線は読みくだし（生の識別子にしない）
    assert _group(out, "in", WX + "about", WX + "Alert")["predicate_label"] == "about"
