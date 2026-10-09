"""Tests for ``asterism.network_view.network_view`` (contract_network_view.md §2)。

実物の ``pyoxigraph.Store`` で動かす。題材は架空の分野（気象観測所と観測記録）。
"""

from __future__ import annotations

import pytest

from asterism import subject_tools
from asterism.network_view import network_view
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
GRAPH_A = canonical_graph_iri("weather-a") + "/v1"
GRAPH_B = canonical_graph_iri("weather-b") + "/v1"
HUB_GRAPH = canonical_graph_iri("crosswalk")
ONTO_GRAPH = ONTOLOGY_GRAPH_BASE + "weather-a"
DRAFT_GRAPH = "https://ex/draft/weather-draft"

PREFIXES = (
    f"@prefix wx: <{WX}> .\n@prefix r: <{R}> .\n@prefix prov: <{PROV}> .\n"
    f"@prefix xw: <{XW}> .\n@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
)


@pytest.fixture(autouse=True)
def _ontology_graphs_listed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _onto(_client: object) -> list[str]:
        return [ONTO_GRAPH]

    async def _readable(client: object) -> set[str]:
        return set(await canonical_graphs(client)) | {ONTO_GRAPH}  # type: ignore[arg-type]

    monkeypatch.setattr("asterism.network_view.ontology_graphs", _onto)
    monkeypatch.setattr("asterism.subject_tools.ontology_graphs", _onto)
    monkeypatch.setattr("asterism.subject_tools.readable_graph_iris", _readable)


ONTOLOGY = PREFIXES + 'wx:zone rdfs:label "zone" .\nwx:observedAt rdfs:label "seen at" .\n'


def _stations(n: int, *, zones: int, prefix: str = "st", predicate: str = "wx:zone") -> str:
    """n 件の観測所。値 ``zones`` 種類を順に割り当てる（使われた数 n）。"""
    lines = [PREFIXES]
    for i in range(n):
        lines.append(f'r:{prefix}-{i} a wx:Station ; {predicate} "z{i % zones}" .')
    return "\n".join(lines)


def _nodes(out: dict, kind: str) -> list[dict]:
    return [n for n in out["nodes"] if n["kind"] == kind]


# --- 値の点の規則 ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("n", "zones", "expected"),
    [
        (150, 50, 50),  # 種類 50（境界）・比 3.0（境界）→ 点になる
        (153, 51, 0),  # 種類 51 → ならない
        (5, 2, 0),  # 比 2.5 → ならない
        (6, 2, 2),  # 比 3.0 → なる
    ],
)
async def test_value_node_rule_boundaries(n: int, zones: int, expected: int) -> None:
    out = await network_view(_pyoxi_client({GRAPH_A: _stations(n, zones=zones)}))
    assert len(_nodes(out, "value")) == expected


async def test_name_like_predicates_never_become_value_nodes() -> None:
    ttl = _stations(30, zones=3, predicate="rdfs:label") + "\n" + _stations(
        30, zones=3, prefix="zz", predicate="wx:zone"
    )
    out = await network_view(_pyoxi_client({GRAPH_A: ttl}))
    values = _nodes(out, "value")
    assert len(values) == 3
    assert all(n["id"].startswith(f"value:{WX}zone\n") for n in values)


async def test_same_predicate_and_value_in_two_datasets_is_one_node() -> None:
    client = _pyoxi_client(
        {
            GRAPH_A: _stations(12, zones=2, prefix="a"),
            GRAPH_B: _stations(12, zones=2, prefix="b"),
        }
    )
    out = await network_view(client)
    assert len(_nodes(out, "value")) == 2
    z0 = next(n for n in out["nodes"] if n["id"] == f"value:{WX}zone\nz0")
    assert z0["degree"] == 12  # 12 件が個別に点のまま（束は 20 件から）


async def test_different_predicates_with_the_same_text_are_different_nodes() -> None:
    ttl = _stations(12, zones=2) + "\n" + "\n".join(
        f'r:st-{i} wx:band "z{i % 2}" .' for i in range(12)
    )
    out = await network_view(_pyoxi_client({GRAPH_A: ttl}))
    ids = {n["id"] for n in _nodes(out, "value")}
    assert ids == {
        f"value:{WX}zone\nz0",
        f"value:{WX}zone\nz1",
        f"value:{WX}band\nz0",
        f"value:{WX}band\nz1",
    }


async def test_value_label_is_predicate_name_and_value_and_text_is_trimmed() -> None:
    ttl = PREFIXES + "\n".join(f'r:st-{i} a wx:Station ; wx:zone " north " .' for i in range(6))
    out = await network_view(_pyoxi_client({GRAPH_A: ttl, ONTO_GRAPH: ONTOLOGY}))
    (node,) = _nodes(out, "value")
    assert node["id"] == f"value:{WX}zone\nnorth"
    assert node["label"] == "zone: north"


# --- PROV ----------------------------------------------------------------------


def _prov_data() -> str:
    lines = [PREFIXES, "r:run-1 a prov:Activity ."]
    for i in range(3):
        nxt = (i + 1) % 3
        lines.append(
            f"r:st-{i} a wx:Station ; wx:near r:st-{nxt} ; prov:wasGeneratedBy r:run-1 ."
        )
    return "\n".join(lines)


async def test_prov_lines_and_prov_only_things_are_dropped_by_default() -> None:
    out = await network_view(_pyoxi_client({GRAPH_A: _prov_data()}))
    ids = {n["id"] for n in out["nodes"]}
    assert R + "run-1" not in ids
    assert len(out["nodes"]) == 3
    assert len(out["edges"]) == 3


async def test_include_prov_adds_prov_lines_and_prov_only_things() -> None:
    out = await network_view(_pyoxi_client({GRAPH_A: _prov_data()}), include_prov=True)
    ids = {n["id"] for n in out["nodes"]}
    assert R + "run-1" in ids
    assert len(out["edges"]) == 6


# --- 束 --------------------------------------------------------------------------


def _observations(n: int, *, other: int = 0) -> str:
    lines = [PREFIXES, 'r:st-1 a wx:Station ; rdfs:label "Harbor" .', "r:st-2 a wx:Station ."]
    for i in range(n):
        target = "st-2" if i < other else "st-1"
        lines.append(f"r:obs-{i} a wx:Observation ; wx:observedAt r:{target} .")
    return "\n".join(lines)


async def test_bundle_boundary_is_20_things() -> None:
    out19 = await network_view(_pyoxi_client({GRAPH_A: _observations(19)}))
    assert _nodes(out19, "bundle") == []
    out20 = await network_view(_pyoxi_client({GRAPH_A: _observations(20)}))
    (bundle,) = _nodes(out20, "bundle")
    assert bundle["count"] == 20
    assert bundle["class_iri"] == WX + "Observation"
    assert bundle["degree"] == 1
    assert out20["stats"]["entities"] == 21
    assert out20["stats"]["nodes"] == 2
    assert out20["stats"]["bundles"] == 1
    assert out20["edges"][0]["source"] == bundle["id"]
    assert out20["edges"][0]["target"] == R + "st-1"


async def test_things_with_different_partners_are_not_bundled_together() -> None:
    # 30 件のうち 11 件は別の観測所 → どちらの組も 20 件に満たない
    out = await network_view(_pyoxi_client({GRAPH_A: _observations(30, other=11)}))
    assert _nodes(out, "bundle") == []
    # 25 件のうち 3 件が別 → 22 件の束と、束にならない 3 件
    out = await network_view(_pyoxi_client({GRAPH_A: _observations(25, other=3)}))
    (bundle,) = _nodes(out, "bundle")
    assert bundle["count"] == 22
    assert len([n for n in out["nodes"] if n["kind"] == "entity" and "obs-" in n["id"]]) == 3


async def test_bundle_set_spec_when_single_partner_and_normalizes() -> None:
    out = await network_view(_pyoxi_client({GRAPH_A: _observations(20)}))
    (bundle,) = _nodes(out, "bundle")
    assert bundle["set_spec"] == {
        "class": WX + "Observation",
        "where": [{"property": WX + "observedAt", "iri": R + "st-1"}],
        "order_by": None,
        "limit": 20,
        "source_scope": "all",
    }
    assert normalize_set_spec(bundle["set_spec"]) == bundle["set_spec"]


async def test_bundle_set_spec_is_null_when_partner_not_single() -> None:
    lines = [PREFIXES, "r:st-1 a wx:Station .", "r:st-2 a wx:Station ."]
    for i in range(20):
        lines.append(f"r:obs-{i} a wx:Observation ; wx:observedAt r:st-1 , r:st-2 .")
    out = await network_view(_pyoxi_client({GRAPH_A: "\n".join(lines)}))
    (bundle,) = _nodes(out, "bundle")
    assert bundle["set_spec"] is None


async def test_value_set_spec_uses_most_common_kind_and_normalizes() -> None:
    lines = [PREFIXES]
    for i in range(6):
        lines.append(f'r:st-{i} a wx:Station ; wx:zone "north" .')
    for i in range(3):
        lines.append(f'r:bu-{i} a wx:Buoy ; wx:zone "north" .')
    out = await network_view(_pyoxi_client({GRAPH_A: "\n".join(lines)}))
    (value,) = _nodes(out, "value")
    assert value["set_spec"] == {
        "class": WX + "Station",
        "where": [{"property": WX + "zone", "op": "eq", "value": "north"}],
        "order_by": None,
        "limit": 20,
        "source_scope": "all",
    }
    assert normalize_set_spec(value["set_spec"]) == value["set_spec"]


# --- 点にしないもの・ハブ・範囲 ---------------------------------------------------


async def test_crosswalk_link_is_not_a_node() -> None:
    ttl = _observations(3) + "\nr:link-1 a xw:CrosswalkLink ; wx:about r:st-1 .\n"
    out = await network_view(_pyoxi_client({GRAPH_A: ttl}))
    assert R + "link-1" not in {n["id"] for n in out["nodes"]}
    assert all(e["source"] != R + "link-1" for e in out["edges"])


async def test_hub_entity_is_kind_hub() -> None:
    ttl = _observations(3) + "\nr:st-1 wx:sharedWith r:shared-1 .\n"
    hub = PREFIXES + "r:shared-1 a xw:Composition .\n"
    out = await network_view(_pyoxi_client({GRAPH_A: ttl, HUB_GRAPH: hub}))
    hubs = _nodes(out, "hub")
    assert [h["id"] for h in hubs] == [R + "shared-1"]
    assert hubs[0]["class_iri"] == XW + "Composition"
    assert hubs[0]["dataset_id"] is None
    st = next(n for n in out["nodes"] if n["id"] == R + "st-1")
    assert st["kind"] == "entity"
    assert st["dataset_id"] == "weather-a"


async def test_draft_graph_is_not_read() -> None:
    client = _pyoxi_client({GRAPH_A: _observations(3), DRAFT_GRAPH: _stations(30, zones=3)})
    out = await network_view(client)
    ids = {n["id"] for n in out["nodes"]}
    assert not any(i.startswith(R + "st-") and i not in (R + "st-1", R + "st-2") for i in ids)
    assert _nodes(out, "value") == []


async def test_no_published_graph_sends_no_sparql_beyond_listing() -> None:
    client = _pyoxi_client({DRAFT_GRAPH: _stations(30, zones=3)})
    out = await network_view(client)
    assert out["nodes"] == [] and out["edges"] == [] and out["kinds"] == []
    assert out["truncated"] is False
    assert len(client.queries) == 1
    assert CONTROL_GRAPH_IRI in client.queries[0]


async def test_zero_degree_things_are_not_nodes() -> None:
    ttl = _observations(3) + "\nr:lonely-1 a wx:Station .\n"
    out = await network_view(_pyoxi_client({GRAPH_A: ttl}))
    assert R + "lonely-1" not in {n["id"] for n in out["nodes"]}


# --- 上限・決定論・名前・種類 -----------------------------------------------------


async def test_max_nodes_keeps_values_and_most_connected_things() -> None:
    lines = [PREFIXES]
    for i in range(10):
        lines.append(f'r:st-{i} a wx:Station ; wx:zone "z{i % 2}" .')
    lines.append("r:st-0 wx:near r:st-1 , r:st-2 , r:st-3 .")
    out = await network_view(_pyoxi_client({GRAPH_A: "\n".join(lines)}), max_nodes=6)
    assert out["truncated"] is True
    assert len(out["nodes"]) <= 6
    assert len(_nodes(out, "value")) == 2
    ids = {n["id"] for n in out["nodes"]}
    assert R + "st-0" in ids  # 次数が最大の件は残る
    assert all(e["source"] in ids and e["target"] in ids for e in out["edges"])


async def test_max_rows_marks_truncated() -> None:
    out = await network_view(_pyoxi_client({GRAPH_A: _observations(30)}), max_rows=10)
    assert out["truncated"] is True
    ok = await network_view(_pyoxi_client({GRAPH_A: _observations(30)}))
    assert ok["truncated"] is False


async def test_same_input_gives_same_output() -> None:
    ttl = _observations(25, other=3) + "\n" + _stations(12, zones=2)
    a = await network_view(_pyoxi_client({GRAPH_A: ttl, ONTO_GRAPH: ONTOLOGY}))
    b = await network_view(_pyoxi_client({GRAPH_A: ttl, ONTO_GRAPH: ONTOLOGY}))
    assert a == b


async def test_entity_label_equals_the_heading_and_kinds_count_bundle_members() -> None:
    client = _pyoxi_client({GRAPH_A: _observations(20)})
    out = await network_view(client)
    st = next(n for n in out["nodes"] if n["id"] == R + "st-1")
    heading = (
        await subject_tools._label_lookup(
            client, await canonical_graphs(client), {R + "st-1"}
        )
    )[R + "st-1"]
    assert st["label"] == heading == "Harbor"
    kinds = {k["class_iri"]: k["count"] for k in out["kinds"]}
    assert kinds == {WX + "Observation": 20, WX + "Station": 1}
    assert out["kinds"][0]["class_iri"] == WX + "Observation"  # 件数の多い順
    assert out["stats"]["edges"] == len(out["edges"])
