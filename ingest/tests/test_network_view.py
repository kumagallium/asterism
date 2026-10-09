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


async def test_value_set_spec_keeps_untrimmed_store_value() -> None:
    lines = [PREFIXES]
    for i in range(6):
        lines.append(f'r:st-{i} a wx:Station ; wx:zone " A " .')
    out = await network_view(_pyoxi_client({GRAPH_A: "\n".join(lines)}))
    (value,) = _nodes(out, "value")
    assert value["label"].endswith(": A")
    assert value["set_spec"]["where"] == [{"property": WX + "zone", "op": "eq", "value": " A "}]


async def test_truncated_when_fixed_nodes_alone_exceed_max_nodes() -> None:
    out = await network_view(_pyoxi_client({GRAPH_A: _stations(25, zones=1)}), max_nodes=1)
    assert out["truncated"] is True


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


# --- 上位構造との接続（ADR global-network-view.md・upper-structure-shared-terms.md） ---


async def test_upper_map_merges_values_of_different_predicates_and_groups_colors() -> None:
    """上位構造の対応表を渡すと、別の述語でも同じ上位の項目なら同じ値の点に合流し、
    件の色の鍵（group_iri）は最上位の種類になる。表に無い IRI はそのまま。"""
    a = "\n".join(
        [PREFIXES] + [f'r:a-{i} a wx:Station ; wx:zone "z{i % 2}" .' for i in range(6)]
    )
    b = "\n".join(
        [PREFIXES] + [f'r:b-{i} a wx:Buoy ; wx:area "z{i % 2}" .' for i in range(6)]
    )
    client = _pyoxi_client({GRAPH_A: a, GRAPH_B: b, ONTO_GRAPH: ONTOLOGY})
    upper = {
        "classes": {WX + "Station": WX + "Site", WX + "Buoy": WX + "Site"},
        "properties": {WX + "zone": WX + "region", WX + "area": WX + "region"},
    }
    merged = await network_view(client, upper=upper)
    values = _nodes(merged, "value")
    assert len(values) == 2  # z0・z1 が 2 つの述語をまたいで 1 つずつ
    groups = {n["group_iri"] for n in merged["nodes"] if n["kind"] in ("entity", "bundle")}
    assert groups == {WX + "Site"}
    assert [k["class_iri"] for k in merged["kinds"]] == [WX + "Site"]

    plain = await network_view(client)
    assert len(_nodes(plain, "value")) == 4  # 表が無ければ述語ごとに別の点
    assert {n["group_iri"] for n in plain["nodes"] if n["kind"] in ("entity", "bundle")} == {
        WX + "Station",
        WX + "Buoy",
    }


async def _count_listed(client, spec: dict) -> int:
    """set_spec の where で一覧が引けるか（subjects の where の組み立てで SPARQL を打つ）。"""
    from asterism.subject_tools import _clause_pattern, _ref

    spec = normalize_set_spec(spec)
    lines = [f"?s a {_ref(spec['class'])} ."]
    lines += [_clause_pattern(c, index=i) for i, c in enumerate(spec["where"])]
    graphs = await canonical_graphs(client)
    from asterism.substrate import canonical_from_clauses

    q = (
        "PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>\n"
        f"SELECT (COUNT(DISTINCT ?s) AS ?n) {canonical_from_clauses(graphs)}"
        "WHERE { " + " ".join(lines) + " }"
    )
    rows = (await client.sparql_select(q))["results"]["bindings"]
    return int(rows[0]["n"]["value"])


def _upper_fixture():
    a = "\n".join([PREFIXES] + [f'r:a-{i} a wx:Station ; wx:zone "z{i % 2}" .' for i in range(6)])
    b = "\n".join([PREFIXES] + [f'r:b-{i} a wx:Buoy ; wx:area "z{i % 2}" .' for i in range(6)])
    upper = {
        "classes": {WX + "Station": WX + "Site", WX + "Buoy": WX + "Site"},
        "properties": {WX + "zone": WX + "region", WX + "area": WX + "region"},
    }
    return a, b, upper


async def test_upper_value_set_spec_uses_the_original_predicate_and_lists_items() -> None:
    a, b, upper = _upper_fixture()
    client = _pyoxi_client({GRAPH_A: a, GRAPH_B: b, ONTO_GRAPH: ONTOLOGY})
    out = await network_view(client, upper=upper)
    values = _nodes(out, "value")
    assert len(values) == 2  # 点は上位の述語で 1 つ
    assert all(n["id"].startswith(f"value:{WX}region\n") for n in values)
    for v in values:
        where = v["set_spec"]["where"]
        assert where[0]["property"] in {WX + "zone", WX + "area"}
        assert where[0]["property"] != WX + "region"
        assert await _count_listed(client, v["set_spec"]) >= 1
    # 同数なら種類 IRI の辞書順（Buoy < Station）→ その種類が使う述語
    assert values[0]["set_spec"]["class"] == WX + "Buoy"
    assert values[0]["set_spec"]["where"][0]["property"] == WX + "area"


async def test_upper_kinds_get_the_ontology_label_of_the_top_class() -> None:
    a, b, upper = _upper_fixture()
    onto = ONTOLOGY + 'wx:Site a rdfs:Class ; rdfs:label "Observation site" .\n'
    client = _pyoxi_client({GRAPH_A: a, GRAPH_B: b, ONTO_GRAPH: onto})
    out = await network_view(client, upper=upper)
    (kind,) = out["kinds"]
    assert kind["class_iri"] == WX + "Site"
    assert kind["class_label"] == "Observation site"


async def test_truncated_type_rows_do_not_make_untyped_iris_into_nodes() -> None:
    lines = [PREFIXES, "r:st-1 a wx:Station ."]
    for i in range(6):
        lines.append(f"r:obs-{i} a wx:Observation ; wx:observedAt r:st-1 .")
    lines.append("r:obs-0 wx:ref r:zz-link , r:zz-run .")
    lines.append("r:zz-link a xw:CrosswalkLink .")
    lines.append("r:zz-run a prov:Activity .")
    client = _pyoxi_client({GRAPH_A: "\n".join(lines)})
    full = await network_view(client, include_prov=True)
    assert R + "zz-run" in {n["id"] for n in full["nodes"]}  # 切れていなければ従来どおり
    assert full["truncated"] is False
    cut = await network_view(client, include_prov=True, max_rows=7)
    ids = {n["id"] for n in cut["nodes"]}
    assert cut["truncated"] is True
    assert R + "zz-link" not in ids and R + "zz-run" not in ids


async def test_things_without_a_kind_are_never_bundled() -> None:
    lines = [PREFIXES, "r:st-1 a wx:Station ."]
    lines += [f"r:k-{i} wx:observedAt r:st-1 ." for i in range(25)]
    out = await network_view(_pyoxi_client({GRAPH_A: "\n".join(lines)}))
    assert _nodes(out, "bundle") == []
    assert len([n for n in out["nodes"] if n["id"].startswith(R + "k-")]) == 25


async def test_prov_activity_and_crosswalk_link_in_a_hub_graph_are_not_hubs() -> None:
    data = _observations(3) + "\nr:st-1 wx:sharedWith r:act-1 , r:link-1 .\n"
    hub = PREFIXES + "r:act-1 a prov:Activity .\nr:link-1 a xw:CrosswalkLink .\n"
    out = await network_view(_pyoxi_client({GRAPH_A: data, HUB_GRAPH: hub}), include_prov=True)
    assert _nodes(out, "hub") == []
    assert R + "link-1" not in {n["id"] for n in out["nodes"]}


async def test_ontology_graph_label_is_used_for_the_name_of_a_thing() -> None:
    onto = ONTOLOGY + 'r:st-2 rdfs:label "Ontology Pier" .\n'
    out = await network_view(_pyoxi_client({GRAPH_A: _observations(3, other=1), ONTO_GRAPH: onto}))
    node = next(n for n in out["nodes"] if n["id"] == R + "st-2")
    assert node["label"] == "Ontology Pier"


@pytest.mark.parametrize(
    "predicate",
    [
        "http://www.w3.org/2000/01/rdf-schema#label",
        "http://www.w3.org/2000/01/rdf-schema#comment",
        "http://schema.org/name",
        "http://schema.org/description",
        "http://purl.org/dc/terms/title",
        "http://purl.org/dc/terms/description",
        "http://www.w3.org/2004/02/skos/core#prefLabel",
        "http://www.w3.org/2004/02/skos/core#altLabel",
    ],
)
async def test_each_name_predicate_never_becomes_a_value_node(predicate: str) -> None:
    lines = [PREFIXES] + [f'r:st-{i} a wx:Station ; <{predicate}> "same" .' for i in range(9)]
    out = await network_view(_pyoxi_client({GRAPH_A: "\n".join(lines)}))
    assert _nodes(out, "value") == []


async def test_insertion_order_does_not_change_the_result() -> None:
    lines = _observations(25, other=3).splitlines() + _stations(12, zones=2).splitlines()
    fwd = "\n".join(lines)
    body = [ln for ln in lines if not ln.startswith("@prefix")]
    rev = PREFIXES + "\n".join(reversed(body))
    a = await network_view(_pyoxi_client({GRAPH_A: fwd, ONTO_GRAPH: ONTOLOGY}))
    b = await network_view(_pyoxi_client({ONTO_GRAPH: ONTOLOGY, GRAPH_A: rev}))
    assert a == b


async def test_stats_published_graphs_counts_graphs_read() -> None:
    none = await network_view(_pyoxi_client({DRAFT_GRAPH: _stations(30, zones=3)}))
    assert none["stats"]["published_graphs"] == 0
    one = await network_view(_pyoxi_client({GRAPH_A: _observations(3)}))
    assert one["stats"]["published_graphs"] == 1
    two = await network_view(
        _pyoxi_client({GRAPH_A: _observations(3), GRAPH_B: _stations(6, zones=2)})
    )
    assert two["stats"]["published_graphs"] == 2


async def test_cut_type_rows_do_not_let_the_subject_at_the_cut_slip_in() -> None:
    # 型の行が (st-1, Station)・(zz, A) で切れ、zz の CrosswalkLink の型は読めていない
    ttl = PREFIXES + (
        "r:st-1 a wx:Station .\n"
        "r:zz a wx:A, xw:CrosswalkLink ; wx:about r:st-1 .\n"
        "r:zz2 a prov:Activity, wx:Z .\n"
        "r:st-1 wx:ref r:zz .\n"
    )
    out = await network_view(_pyoxi_client({GRAPH_A: ttl}), max_rows=2)
    assert out["truncated"] is True
    assert f"{R}zz" not in {n["id"] for n in out["nodes"]}


async def test_value_set_spec_uses_a_string_the_chosen_kind_really_has() -> None:
    # 駅（6 件）が多いので一覧は駅で開く。値の文字列は駅が持つもの（"z0" と "z0  "）から選び、
    # 浮標だけが持つ "z0 "（5 件で全体では最多）を渡さない（渡すと駅の一覧が 0 件になる）。
    lines = [PREFIXES]
    lines += [f'r:st-{i} a wx:Station ; wx:zone "z0" .' for i in range(3)]
    lines += [f'r:st-{i} a wx:Station ; wx:zone "z0  " .' for i in range(3, 6)]
    lines += [f'r:by-{i} a wx:Buoy ; wx:zone "z0 " .' for i in range(5)]
    client = _pyoxi_client({GRAPH_A: "\n".join(lines)})
    out = await network_view(client)
    (value,) = _nodes(out, "value")
    spec = value["set_spec"]
    assert spec["class"] == f"{WX}Station"
    assert spec["where"][0]["value"] in {"z0", "z0  "}
    assert await _count_listed(client, spec) >= 1


async def test_output_is_sorted_so_it_does_not_depend_on_set_order() -> None:
    # 同じプロセスの中では集合の並びがたまたま揃うので、並べ替えそのものを確かめる
    ttl = _observations(25, other=3) + "\n" + _stations(12, zones=2).replace(PREFIXES, "")
    out = await network_view(_pyoxi_client({GRAPH_A: ttl, ONTO_GRAPH: ONTOLOGY}))
    things = [n["id"] for n in out["nodes"] if n["kind"] in ("entity", "hub")]
    values = [n["id"] for n in out["nodes"] if n["kind"] == "value"]
    bundles = [n["id"] for n in out["nodes"] if n["kind"] == "bundle"]
    assert len(things) > 5 and values and bundles
    assert things == sorted(things)
    assert values == sorted(values)
    assert bundles == sorted(bundles)
    pairs = [(e["source"], e["target"]) for e in out["edges"]]
    assert pairs == sorted(pairs)


# --- データセット名（同じ名前の種類を見分ける） -----------------------------------


def _write_meta(root, did: str, name: str) -> None:
    (root / did).mkdir(parents=True)
    (root / did / "meta.json").write_text(f'{{"name": "{name}"}}', encoding="utf-8")


async def test_datasets_lists_used_datasets_sorted_with_resolved_labels(tmp_path) -> None:
    from asterism.subjects import resolve_dataset_label

    _write_meta(tmp_path, "weather-a", "気象A")  # weather-b は meta が無く id に落ちる
    client = _pyoxi_client(
        {GRAPH_B: _stations(3, zones=1, prefix="b"), GRAPH_A: _stations(3, zones=1, prefix="a")}
    )
    out = await network_view(client, registry_root=tmp_path)
    assert out["datasets"] == [
        {"id": "weather-a", "label": "気象A"},
        {"id": "weather-b", "label": "weather-b"},
    ]
    assert out["datasets"][0]["label"] == resolve_dataset_label(tmp_path, "weather-a")


async def test_kinds_dataset_ids_for_same_named_kind_in_two_datasets() -> None:
    client = _pyoxi_client(
        {GRAPH_A: _stations(3, zones=1, prefix="a"), GRAPH_B: _stations(3, zones=1, prefix="b")}
    )
    out = await network_view(client)
    (kind,) = out["kinds"]
    assert kind["dataset_ids"] == ["weather-a", "weather-b"]
    only_a = await network_view(_pyoxi_client({GRAPH_A: _stations(3, zones=1)}))
    assert only_a["kinds"][0]["dataset_ids"] == ["weather-a"]


async def test_bundle_dataset_id_is_set_when_same_and_null_when_split() -> None:
    same = await network_view(_pyoxi_client({GRAPH_A: _observations(20)}))
    (b1,) = _nodes(same, "bundle")
    assert b1["dataset_id"] == "weather-a"
    assert same["kinds"][0]["dataset_ids"] == ["weather-a"]
    half = "\n".join(
        [PREFIXES]
        + [f"r:obs-b{i} a wx:Observation ; wx:observedAt r:st-1 ." for i in range(10)]
    )
    split = await network_view(
        _pyoxi_client({GRAPH_A: _observations(10), GRAPH_B: half})
    )
    (b2,) = _nodes(split, "bundle")
    assert b2["count"] == 20 and b2["dataset_id"] is None
    obs = next(k for k in split["kinds"] if k["class_iri"] == WX + "Observation")
    assert obs["dataset_ids"] == ["weather-a", "weather-b"]


async def test_no_published_graph_gives_empty_datasets() -> None:
    out = await network_view(_pyoxi_client({DRAFT_GRAPH: _stations(3, zones=1)}))
    assert out["datasets"] == []


async def test_upper_kinds_dataset_ids_are_per_group() -> None:
    a, b, upper = _upper_fixture()
    client = _pyoxi_client({GRAPH_A: a, GRAPH_B: b, ONTO_GRAPH: ONTOLOGY})
    out = await network_view(client, upper=upper)
    (kind,) = out["kinds"]
    assert kind["class_iri"] == WX + "Site"
    assert kind["dataset_ids"] == ["weather-a", "weather-b"]
    assert [d["id"] for d in out["datasets"]] == ["weather-a", "weather-b"]
    # 束になる件数でも group 単位
    a2 = "\n".join([PREFIXES] + [f"r:a-{i} a wx:Station ; wx:zone \"z\" ." for i in range(25)])
    b2 = "\n".join([PREFIXES] + [f"r:b-{i} a wx:Buoy ; wx:area \"z\" ." for i in range(25)])
    big = await network_view(
        _pyoxi_client({GRAPH_A: a2, GRAPH_B: b2, ONTO_GRAPH: ONTOLOGY}), upper=upper
    )
    assert _nodes(big, "bundle")
    (bk,) = big["kinds"]
    assert bk["class_iri"] == WX + "Site"
    assert bk["dataset_ids"] == ["weather-a", "weather-b"]


async def test_value_nodes_have_no_dataset_id() -> None:
    out = await network_view(
        _pyoxi_client(
            {
                GRAPH_A: _stations(6, zones=2, prefix="a"),
                GRAPH_B: _stations(6, zones=2, prefix="b"),
            }
        )
    )
    assert _nodes(out, "value")
    assert all(n["dataset_id"] is None for n in _nodes(out, "value"))


async def test_hub_has_no_dataset_id_even_if_typed_in_a_dataset_graph_too() -> None:
    ttl = _observations(3) + "\nr:st-1 wx:sharedWith r:shared-1 .\nr:shared-1 a wx:Thing .\n"
    hub = PREFIXES + "r:shared-1 a xw:Composition .\n"
    out = await network_view(_pyoxi_client({GRAPH_A: ttl, HUB_GRAPH: hub}))
    (h,) = _nodes(out, "hub")
    assert h["dataset_id"] is None
