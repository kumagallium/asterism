"""Tests for asterism.subject_tools (object-cards-ui.md §3 / 契約メモ §3).

Backed by a real ``pyoxigraph.Store`` — same contract as ``_pyoxi_client`` in
test_query_tools.py / test_prov_graph.py / test_class_schema.py. Fixture data
spans two unrelated fictional domains (a lending-library checkout log and an
unrelated field-log) with a registry (meta.json + query_tools.yaml) for one
of them, so no test asserts on a single domain's shape or a domain-specific
noun (§0).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from asterism.query_tools import load_query_tools
from asterism.subject_tools import (
    SubjectKindMismatchError,
    SubjectToolError,
    UnknownSubjectToolError,
    card_id_of,
    default_cards_for_set,
    default_cards_for_subject,
    hub_of_subject,
    materials_for_subject,
    pick_class_iri,
    run_subject_tool,
    set_breakdown,
    set_count,
    set_members,
    subject_facts,
    subject_flow,
    subject_hub_members,
    subject_sources,
    subject_types,
)
from asterism.subjects import SetSpecError, normalize_set_spec, set_id_of, validate_subject_key
from asterism.substrate import (
    CANONICAL_GRAPH_BASE,
    CONTROL_GRAPH_IRI,
    ONTOLOGY_GRAPH_BASE,
    STATUS_PREDICATE,
    STATUS_PROMOTED,
    canonical_graph_iri,
)

pyoxigraph = pytest.importorskip("pyoxigraph")


def _pyoxi_client(graphs: dict[str, str]):
    """{graph_iri: ttl} loaded into that named graph; any graph under
    CANONICAL_GRAPH_BASE is flagged promoted (same contract as every other
    ingest test's store fixture)."""
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


# ---------------------------------------------------------------------------
# Fixture data — two unrelated fictional domains.
# ---------------------------------------------------------------------------

EX_LIB = "https://ex/library#"
CHECKOUT_CLASS = EX_LIB + "CheckoutRecord"
BORROWER_PRED = EX_LIB + "borrower"
BRANCH_PRED = EX_LIB + "branch"
OVERDUE_PRED = EX_LIB + "overdueDays"

LIB_DATASET = "library-checkouts"
LIB_GRAPH = canonical_graph_iri(LIB_DATASET) + "/v1"

CHECKOUT_1 = "https://ex/library/resource/checkout-1"
CHECKOUT_2 = "https://ex/library/resource/checkout-2"
CHECKOUT_3 = "https://ex/library/resource/checkout-3"
BORROWER_A = "https://ex/library/resource/person-a"
ACTIVITY_1 = "https://ex/library/resource/activity-1"
NOWHERE = "https://ex/library/resource/does-not-exist"

_LIB_TTL = f"""
@prefix ex: <{EX_LIB}> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix prov: <http://www.w3.org/ns/prov#> .

<{CHECKOUT_1}> a <{CHECKOUT_CLASS}> ;
    rdfs:label "Checkout One" ;
    ex:borrower <{BORROWER_A}> ;
    ex:branch "north" ;
    ex:overdueDays "3"^^xsd:integer ;
    prov:wasGeneratedBy <{ACTIVITY_1}> .

<{ACTIVITY_1}> a prov:Activity .

<{CHECKOUT_2}> a <{CHECKOUT_CLASS}> ;
    rdfs:label "Checkout Two" ;
    ex:borrower <{BORROWER_A}> ;
    ex:branch "south" ;
    ex:overdueDays "7"^^xsd:integer .

<{CHECKOUT_3}> a <{CHECKOUT_CLASS}> ;
    rdfs:label "Checkout Three" ;
    ex:borrower <{BORROWER_A}> ;
    ex:branch "north" ;
    ex:overdueDays "1"^^xsd:integer .

<{BORROWER_A}> rdfs:label "Borrower A" .
"""

EX_FIELD = "https://ex/field#"
READING_CLASS = EX_FIELD + "Reading"
DEVICE_PRED = EX_FIELD + "device"
READING_PRED = EX_FIELD + "reading"

FIELD_DATASET = "field-log"
FIELD_GRAPH = canonical_graph_iri(FIELD_DATASET) + "/v1"

READING_1 = "https://ex/field/resource/reading-1"
DEVICE_A = "https://ex/field/resource/device-a"

_FIELD_TTL = f"""
@prefix ex: <{EX_FIELD}> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

<{READING_1}> a <{READING_CLASS}> ;
    rdfs:label "Reading One" ;
    ex:device <{DEVICE_A}> ;
    ex:reading "42.5"^^xsd:double .
"""

_LIB_TOOLS_YAML = """
tools:
  - name: overdue_days
    title: "延滞日数"
    output_kind: quantity
    parameters:
      - name: checkout
        type: iri
        required: true
    query: |
      SELECT ?v WHERE { BIND({{checkout}} AS ?s) ?s <https://ex/library#overdueDays> ?v } LIMIT 1
    result:
      item:
        value: {var: v, number: true, role: value}

  - name: needs_two_iris
    title: "二つの IRI を要る道具 (既定カードには出ない)"
    output_kind: facts
    parameters:
      - name: a
        type: iri
        required: true
      - name: b
        type: iri
        required: true
    query: |
      SELECT ?x WHERE { BIND({{a}} AS ?s1) BIND({{b}} AS ?s2) ?s1 <https://ex/library#borrower> ?x }

  - name: plain_facts_tool
    title: "facts のまま (既定カードには出ない)"
    parameters:
      - name: checkout
        type: iri
        required: true
    query: |
      SELECT ?p ?o WHERE { BIND({{checkout}} AS ?s) ?s ?p ?o }
"""


def _client() -> object:
    return _pyoxi_client({LIB_GRAPH: _LIB_TTL, FIELD_GRAPH: _FIELD_TTL})


def _write_registry(root: Path) -> None:
    dest = root / LIB_DATASET
    dest.mkdir(parents=True)
    meta = {"id": LIB_DATASET, "name": "貸出記録", "promoted": True, "promoted_at": "2024-01-01"}
    (dest / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    (dest / "query_tools.yaml").write_text(_LIB_TOOLS_YAML, encoding="utf-8")


# ---------------------------------------------------------------------------
# subject_facts — determinism, labels, K4.
# ---------------------------------------------------------------------------


async def test_subject_facts_ordering_is_deterministic() -> None:
    out = await subject_facts(_client(), CHECKOUT_1)
    assert out["output_kind"] == "facts"
    props = [item["property_iri"] for item in out["items"]]
    assert props == sorted(props)  # ordered by predicate IRI


async def test_subject_facts_same_input_same_order_across_calls() -> None:
    first = await subject_facts(_client(), CHECKOUT_1)
    second = await subject_facts(_client(), CHECKOUT_1)
    assert first["items"] == second["items"]


async def test_subject_facts_resolves_iri_object_label_never_raw_iri() -> None:
    out = await subject_facts(_client(), CHECKOUT_1)
    borrower_row = next(i for i in out["items"] if i["property_iri"] == BORROWER_PRED)
    assert borrower_row["value"] == "Borrower A"  # rdfs:label, not the raw IRI (K4)
    assert borrower_row["value_iri"] == BORROWER_A


async def test_subject_facts_dedupes_same_triple_across_two_canonical_graphs() -> None:
    # 実機所見の再現: 「置く」で棚とつながった 1 件は同じ (property, value)
    # の三つ組を 2 つの版グラフに持つ（同じ実物を 2 つのデータセットが記録し
    # ている）。facts はそれを 1 行に畳む — 出どころは subject_sources の仕事
    # なので facts の行に graph は持たせない。
    own_dataset = "own-shelf"
    own_graph = canonical_graph_iri(own_dataset) + "/v1"
    own_ttl = f"""
    @prefix ex: <{EX_LIB}> .
    @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
    @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

    <{CHECKOUT_1}> a <{CHECKOUT_CLASS}> ;
        rdfs:label "Checkout One" ;
        ex:borrower <{BORROWER_A}> ;
        ex:branch "north" ;
        ex:overdueDays "3"^^xsd:integer .

    <{BORROWER_A}> rdfs:label "Borrower A" .
    """
    client = _pyoxi_client({LIB_GRAPH: _LIB_TTL, own_graph: own_ttl, FIELD_GRAPH: _FIELD_TTL})
    out = await subject_facts(client, CHECKOUT_1)
    borrower_rows = [i for i in out["items"] if i["property_iri"] == BORROWER_PRED]
    branch_rows = [i for i in out["items"] if i["property_iri"] == BRANCH_PRED]
    assert len(borrower_rows) == 1
    assert len(branch_rows) == 1
    assert out["count"] == len(out["items"])
    assert all("graph" not in item for item in out["items"])


async def test_subject_facts_shape_carries_item_and_materials() -> None:
    out = await subject_facts(_client(), CHECKOUT_1)
    assert set(out["item"]) == {"property_iri", "property", "value", "value_iri"}
    # registry_root=None (既定) なので出どころ・ライセンスは引けず「不明」側に
    # 倒れる（保守側）— materials.py の遅延 import が失敗したときと同じ既定値。
    assert out["materials"] == [
        {
            "kind": "unknown",
            "dataset_id": LIB_DATASET,
            "dataset_label": LIB_DATASET,
            "snapshot": "v1",
            "license": None,
            "redistributable": None,
            "count": out["materials"][0]["count"],
        }
    ]
    assert out["shareable"] is False
    assert out["shareable_reasons"] == ["unknown_origin", "unknown_license"]


# ---------------------------------------------------------------------------
# subject_sources — per-dataset aggregation + registry label fallback.
# ---------------------------------------------------------------------------


async def test_subject_sources_counts_one_dataset_and_uses_registry_name(
    tmp_path: Path,
) -> None:
    _write_registry(tmp_path)
    out = await subject_sources(_client(), CHECKOUT_1, registry_root=tmp_path)
    assert out["output_kind"] == "breakdown"
    assert len(out["items"]) == 1
    assert out["items"][0]["category"] == "貸出記録 (v1)"
    assert out["items"][0]["count"] > 0


async def test_subject_sources_falls_back_to_dataset_id_without_registry() -> None:
    out = await subject_sources(_client(), CHECKOUT_1, registry_root=None)
    assert out["items"][0]["category"] == f"{LIB_DATASET} (v1)"


# ---------------------------------------------------------------------------
# subject_flow — found only when there is an actual edge.
# ---------------------------------------------------------------------------


async def test_subject_flow_found_true_when_edges_exist() -> None:
    out = await subject_flow(_client(), CHECKOUT_1)
    assert out["found"] is True
    assert out["count"] >= 1
    assert out["output_kind"] == "flow"


async def test_subject_flow_shape_carries_material_form_materials(tmp_path: Path) -> None:
    """flow の materials は prov_graph 生の ``{dataset_id, snapshot, graph}``
    ではなく、他の built-in と同じ Material 形（§3.3）でなければならない —
    さもないと ui の材料タブ（dataset_label/kind/license/redistributable/
    count 前提）が flow カードだけ壊れる。"""
    _write_registry(tmp_path)
    out = await subject_flow(_client(), CHECKOUT_1, registry_root=tmp_path)
    assert out["found"] is True
    assert out["materials"]
    mat = out["materials"][0]
    assert set(mat) == {
        "kind",
        "dataset_id",
        "dataset_label",
        "snapshot",
        "license",
        "redistributable",
        "count",
    }
    assert mat["dataset_id"] == LIB_DATASET
    assert mat["dataset_label"] == "貸出記録"
    assert isinstance(mat["count"], int) and mat["count"] > 0
    assert isinstance(out["shareable"], bool)
    assert isinstance(out["shareable_reasons"], list)


async def test_subject_flow_found_false_with_zero_edges() -> None:
    out = await subject_flow(_client(), CHECKOUT_2)
    assert out["found"] is False
    assert out["count"] == 0
    assert out["items"] == []


async def test_subject_flow_found_false_for_absent_iri() -> None:
    out = await subject_flow(_client(), NOWHERE)
    assert out["found"] is False


# ---------------------------------------------------------------------------
# declared-tool iri binding, via run_subject_tool dispatch.
# ---------------------------------------------------------------------------


async def test_run_subject_tool_binds_declared_tool_to_the_subject(tmp_path: Path) -> None:
    _write_registry(tmp_path)
    subject = validate_subject_key({"kind": "individual", "iri": CHECKOUT_1})
    out = await run_subject_tool(_client(), tmp_path, subject, f"{LIB_DATASET}/overdue_days", {})
    assert out["output_kind"] == "quantity"
    assert out["items"] == [{"value": 3.0}]
    assert out["materials"][0]["dataset_id"] == LIB_DATASET


async def test_run_subject_tool_resolves_bare_declared_tool_name_via_subject_class(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """後方互換: 接頭 (dataset_id/) の付かない宣言ツール名でも、その主語自身の
    クラスから解決できれば通る（§ dispatch back-compat）。"""
    _write_registry(tmp_path)
    _patch_class_schema_from_fixture_tools(monkeypatch, tmp_path)
    subject = validate_subject_key({"kind": "individual", "iri": CHECKOUT_1})
    out = await run_subject_tool(_client(), tmp_path, subject, "overdue_days", {})
    assert out["output_kind"] == "quantity"
    assert out["items"] == [{"value": 3.0}]


async def test_run_subject_tool_unknown_declared_tool_is_unknown_tool_error(
    tmp_path: Path,
) -> None:
    _write_registry(tmp_path)
    subject = validate_subject_key({"kind": "individual", "iri": CHECKOUT_1})
    with pytest.raises(UnknownSubjectToolError):
        await run_subject_tool(_client(), tmp_path, subject, f"{LIB_DATASET}/nope", {})


async def test_run_subject_tool_unknown_builtin_is_unknown_tool_error() -> None:
    subject = validate_subject_key({"kind": "individual", "iri": CHECKOUT_1})
    with pytest.raises(UnknownSubjectToolError):
        await run_subject_tool(_client(), None, subject, "not_a_real_tool", {})


async def test_run_subject_tool_set_tool_on_individual_is_kind_mismatch() -> None:
    subject = validate_subject_key({"kind": "individual", "iri": CHECKOUT_1})
    with pytest.raises(SubjectKindMismatchError):
        await run_subject_tool(_client(), None, subject, "set_members", {})


async def test_run_subject_tool_individual_tool_on_set_is_kind_mismatch() -> None:
    subject = validate_subject_key({"kind": "set", "spec": {"class": CHECKOUT_CLASS, "where": []}})
    with pytest.raises(SubjectKindMismatchError):
        await run_subject_tool(_client(), None, subject, "subject_facts", {})


# ---------------------------------------------------------------------------
# set_members — where ops, order_by, own scope, `at` rejected.
# ---------------------------------------------------------------------------


async def test_set_members_gt_filters_numerically() -> None:
    spec = {
        "class": CHECKOUT_CLASS,
        "where": [{"property": OVERDUE_PRED, "op": "gt", "value": 2}],
    }
    out = await set_members(_client(), spec)
    subjects = {i["subject_iri"] for i in out["items"]}
    assert subjects == {CHECKOUT_1, CHECKOUT_2}  # overdueDays 3 and 7, not 1


async def test_set_members_lt_filters_numerically() -> None:
    spec = {
        "class": CHECKOUT_CLASS,
        "where": [{"property": OVERDUE_PRED, "op": "lt", "value": 2}],
    }
    out = await set_members(_client(), spec)
    assert {i["subject_iri"] for i in out["items"]} == {CHECKOUT_3}


async def test_set_members_eq_string_matches_category_value() -> None:
    spec = {
        "class": CHECKOUT_CLASS,
        "where": [{"property": BRANCH_PRED, "op": "eq", "value": "north"}],
    }
    out = await set_members(_client(), spec)
    assert {i["subject_iri"] for i in out["items"]} == {CHECKOUT_1, CHECKOUT_3}


async def test_set_members_between_filters_numeric_range() -> None:
    spec = {
        "class": CHECKOUT_CLASS,
        "where": [{"property": OVERDUE_PRED, "op": "between", "value": [2, 5]}],
    }
    out = await set_members(_client(), spec)
    assert {i["subject_iri"] for i in out["items"]} == {CHECKOUT_1}


async def test_set_members_in_numeric_list() -> None:
    spec = {
        "class": CHECKOUT_CLASS,
        "where": [{"property": OVERDUE_PRED, "op": "in", "value": [1, 7]}],
    }
    out = await set_members(_client(), spec)
    assert {i["subject_iri"] for i in out["items"]} == {CHECKOUT_2, CHECKOUT_3}


async def test_set_members_in_string_list() -> None:
    spec = {
        "class": CHECKOUT_CLASS,
        "where": [{"property": BRANCH_PRED, "op": "in", "value": ["south"]}],
    }
    out = await set_members(_client(), spec)
    assert {i["subject_iri"] for i in out["items"]} == {CHECKOUT_2}


async def test_set_members_order_by_desc_ranks_numerically() -> None:
    spec = {
        "class": CHECKOUT_CLASS,
        "where": [],
        "order_by": {"property": OVERDUE_PRED, "dir": "desc"},
    }
    out = await set_members(_client(), spec)
    values = [i["value"] for i in out["items"]]
    assert values == sorted(values, reverse=True)
    assert out["items"][0]["subject_iri"] == CHECKOUT_2  # overdueDays 7, largest


async def test_set_members_order_by_asc_ranks_numerically() -> None:
    spec = {
        "class": CHECKOUT_CLASS,
        "where": [],
        "order_by": {"property": OVERDUE_PRED, "dir": "asc"},
    }
    out = await set_members(_client(), spec)
    assert out["items"][0]["subject_iri"] == CHECKOUT_3  # overdueDays 1, smallest


async def test_set_members_where_at_clause_is_400_shaped_error() -> None:
    spec = {
        "class": CHECKOUT_CLASS,
        "where": [
            {
                "property": OVERDUE_PRED,
                "op": "gt",
                "value": 1,
                "at": {"property": "x", "value": 1},
            }
        ],
    }
    with pytest.raises(SetSpecError):
        await set_members(_client(), spec)


async def test_set_members_order_by_at_clause_is_400_shaped_error() -> None:
    spec = {
        "class": CHECKOUT_CLASS,
        "where": [],
        "order_by": {"property": OVERDUE_PRED, "at": {"property": "x", "value": 1}},
    }
    with pytest.raises(SetSpecError):
        await set_members(_client(), spec)


async def test_set_members_own_scope_falls_back_to_all_when_nothing_is_own(
    tmp_path: Path,
) -> None:
    _write_registry(tmp_path)  # no origin field set anywhere yet (PR D territory)
    spec = {"class": CHECKOUT_CLASS, "where": [], "source_scope": "own"}
    out = await set_members(_client(), spec, registry_root=tmp_path)
    assert {i["subject_iri"] for i in out["items"]} == {CHECKOUT_1, CHECKOUT_2, CHECKOUT_3}


async def test_set_members_own_scope_filters_to_origin_own_datasets(tmp_path: Path) -> None:
    dest = tmp_path / LIB_DATASET
    dest.mkdir(parents=True)
    meta = {"id": LIB_DATASET, "promoted": True, "origin": "own"}
    (dest / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    other = tmp_path / FIELD_DATASET
    other.mkdir(parents=True)
    (other / "meta.json").write_text(
        json.dumps({"id": FIELD_DATASET, "promoted": True}), encoding="utf-8"
    )
    spec = {"class": CHECKOUT_CLASS, "where": [], "source_scope": "own"}
    out = await set_members(_client(), spec, registry_root=tmp_path)
    # library-checkouts IS the origin:own dataset, so its members still show.
    assert {i["subject_iri"] for i in out["items"]} == {CHECKOUT_1, CHECKOUT_2, CHECKOUT_3}


# ---------------------------------------------------------------------------
# set_breakdown / set_count.
# ---------------------------------------------------------------------------


async def test_set_breakdown_counts_by_category_value() -> None:
    spec = {"class": CHECKOUT_CLASS, "where": []}
    out = await set_breakdown(_client(), spec, BRANCH_PRED)
    by_category = {i["category"]: i["count"] for i in out["items"]}
    assert by_category == {"north": 2, "south": 1}
    assert out["output_kind"] == "breakdown"


async def test_set_breakdown_groups_overflow_into_other() -> None:
    spec = {"class": CHECKOUT_CLASS, "where": []}
    out = await set_breakdown(_client(), spec, BRANCH_PRED, top_n=1)
    categories = {i["category"] for i in out["items"]}
    assert "other" in categories
    total = sum(i["count"] for i in out["items"])
    assert total == 3


async def test_set_breakdown_labels_iri_valued_category_values() -> None:
    """category の値が IRI（ここでは link 相当の property）のときは §2 の共通
    ラベル関数でラベルに直し、生の IRI は ``category_iri`` に添える（K4）。"""
    spec = {"class": CHECKOUT_CLASS, "where": []}
    out = await set_breakdown(_client(), spec, BORROWER_PRED)
    assert out["items"] == [{"category": "Borrower A", "count": 3, "category_iri": BORROWER_A}]


async def test_set_count_counts_matching_subjects() -> None:
    spec = {
        "class": CHECKOUT_CLASS,
        "where": [{"property": BRANCH_PRED, "op": "eq", "value": "north"}],
    }
    out = await set_count(_client(), spec)
    assert out["output_kind"] == "quantity"
    assert out["items"] == [{"value": 2}]


async def test_set_count_zero_for_a_class_with_no_data() -> None:
    spec = {"class": "https://ex/library#NoSuchClass", "where": []}
    out = await set_count(_client(), spec)
    assert out["items"] == [{"value": 0}]


# ---------------------------------------------------------------------------
# default_cards_for_subject — ordering, flow-only-when-edges, iri-arity filter.
# ---------------------------------------------------------------------------


async def test_default_cards_for_subject_always_has_facts_then_sources(
    tmp_path: Path,
) -> None:
    _write_registry(tmp_path)
    cards = await default_cards_for_subject(_client(), tmp_path, CHECKOUT_2)
    tools = [c["tool"] for c in cards]
    assert tools[0] == "subject_facts"
    assert tools[1] == "subject_sources"


async def test_default_cards_for_subject_includes_flow_only_with_edges(
    tmp_path: Path,
) -> None:
    _write_registry(tmp_path)
    with_flow = await default_cards_for_subject(_client(), tmp_path, CHECKOUT_1)
    without_flow = await default_cards_for_subject(_client(), tmp_path, CHECKOUT_2)
    assert "subject_flow" in [c["tool"] for c in with_flow]
    assert "subject_flow" not in [c["tool"] for c in without_flow]


def _patch_class_schema_from_fixture_tools(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point ``_load_class_schema`` at a fake whose ``tools`` are exactly this
    fixture's ``query_tools.yaml`` (real class_schema's OWN dataset-matching
    logic — needing a mapping.yaml — is c1-schema's to test, not ours; this
    isolates default_cards_for_subject's grouping/filtering/ordering)."""
    import asterism.subject_tools as subject_tools_mod

    tools = load_query_tools(LIB_DATASET, root=tmp_path)
    raw_tools = [
        {
            "name": t.name,
            "title": t.title,
            "output_kind": t.output_kind,
            "parameters": [{"name": p.name, "type": p.type} for p in t.params],
        }
        for t in tools
    ]

    async def _fake_class_schema(client, registry_root, class_iri):
        return {"class_iri": class_iri, "dataset_id": LIB_DATASET, "tools": raw_tools}

    monkeypatch.setattr(subject_tools_mod, "_load_class_schema", lambda: _fake_class_schema)


async def test_default_cards_for_subject_excludes_multi_iri_and_facts_declared_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_registry(tmp_path)
    _patch_class_schema_from_fixture_tools(monkeypatch, tmp_path)
    cards = await default_cards_for_subject(_client(), tmp_path, CHECKOUT_2)
    tools = [c["tool"] for c in cards]
    assert "needs_two_iris" not in tools  # 2 iri params -> never bindable to 1 subject
    assert f"{LIB_DATASET}/needs_two_iris" not in tools
    assert "plain_facts_tool" not in tools  # facts output_kind is excluded by rule
    assert f"{LIB_DATASET}/plain_facts_tool" not in tools
    # default-cards returns a name cards/run can be called with as-is: the
    # dataset-prefixed form (§ dispatch contract), not the bare tool name.
    assert f"{LIB_DATASET}/overdue_days" in tools  # exactly 1 iri param, quantity kind
    assert "overdue_days" not in tools


async def test_default_cards_for_subject_card_ids_are_deterministic(tmp_path: Path) -> None:
    _write_registry(tmp_path)
    first = await default_cards_for_subject(_client(), tmp_path, CHECKOUT_1)
    second = await default_cards_for_subject(_client(), tmp_path, CHECKOUT_1)
    assert [c["card_id"] for c in first] == [c["card_id"] for c in second]


async def test_default_cards_for_subject_builtin_titles_are_i18n_keys(tmp_path: Path) -> None:
    cards = await default_cards_for_subject(_client(), tmp_path, CHECKOUT_2)
    facts_card = next(c for c in cards if c["tool"] == "subject_facts")
    assert facts_card["title"] == "cards:builtin.subject_facts"


async def test_default_cards_for_subject_class_schema_seam_can_be_monkeypatched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lazy ``_load_class_schema`` indirection is the seam tests (and a
    caller without the parallel-authored ``asterism.class_schema`` module)
    use instead of a real class_schema lookup."""
    import asterism.subject_tools as subject_tools_mod

    async def _fake_class_schema(client, registry_root, class_iri):
        assert class_iri == CHECKOUT_CLASS
        return {
            "class_iri": class_iri,
            "dataset_id": LIB_DATASET,
            "tools": [
                {
                    "name": "fake_tool",
                    "title": "偽の道具",
                    "output_kind": "breakdown",
                    "parameters": [{"name": "x", "type": "iri"}],
                }
            ],
        }

    monkeypatch.setattr(subject_tools_mod, "_load_class_schema", lambda: _fake_class_schema)
    cards = await default_cards_for_subject(_client(), tmp_path, CHECKOUT_2)
    assert f"{LIB_DATASET}/fake_tool" in [c["tool"] for c in cards]


# ---------------------------------------------------------------------------
# default_cards_for_set — pure function, no store access.
# ---------------------------------------------------------------------------


def test_default_cards_for_set_always_members_then_count() -> None:
    spec = {"class": CHECKOUT_CLASS, "where": []}
    cards = default_cards_for_set(spec, properties=None)
    assert [c["tool"] for c in cards] == ["set_members", "set_count"]


def test_default_cards_for_set_picks_smallest_distinct_category_not_the_first() -> None:
    """distinct_count が件数と同じような「1 件ごとに違う名前」の category
    (near-unique) より、いちばんまとまる (distinct_count 最小) 方を選ぶ —
    先に出てくる方ではない（実機所見: 最初の category を選ぶと全部 1 件の
    棒になった）。"""
    spec = {"class": CHECKOUT_CLASS, "where": []}
    properties = [
        {"iri": OVERDUE_PRED, "kind": "quantity"},
        {"iri": BORROWER_PRED, "kind": "category", "distinct_count": 3},  # near-unique
        {"iri": BRANCH_PRED, "kind": "category", "distinct_count": 2},  # groups better
    ]
    cards = default_cards_for_set(spec, properties=properties)
    assert [c["tool"] for c in cards] == ["set_members", "set_breakdown", "set_count"]
    assert cards[1]["params"] == {"property": BRANCH_PRED}


def test_default_cards_for_set_ties_go_to_earlier_ir_order() -> None:
    spec = {"class": CHECKOUT_CLASS, "where": []}
    properties = [
        {"iri": BRANCH_PRED, "kind": "category", "distinct_count": 2},
        {"iri": BORROWER_PRED, "kind": "category", "distinct_count": 2},
    ]
    cards = default_cards_for_set(spec, properties=properties)
    breakdown = next(c for c in cards if c["tool"] == "set_breakdown")
    assert breakdown["params"] == {"property": BRANCH_PRED}


def test_default_cards_for_set_no_breakdown_when_no_category_qualifies() -> None:
    """distinct_count が無い（未計算）・1 以下しかない candidate しか無ければ
    内訳カードそのものを出さない（今までは常に「最初の category」を出していた
    ので、これは挙動変更）。"""
    spec = {"class": CHECKOUT_CLASS, "where": []}
    properties = [
        {"iri": OVERDUE_PRED, "kind": "quantity"},
        {"iri": BRANCH_PRED, "kind": "category", "distinct_count": None},
        {"iri": BORROWER_PRED, "kind": "category", "distinct_count": 1},
    ]
    cards = default_cards_for_set(spec, properties=properties)
    assert [c["tool"] for c in cards] == ["set_members", "set_count"]


def test_default_cards_for_set_is_deterministic() -> None:
    spec = {"class": CHECKOUT_CLASS, "where": []}
    properties = [{"iri": BRANCH_PRED, "kind": "category", "distinct_count": 2}]
    first = default_cards_for_set(spec, properties)
    second = default_cards_for_set(spec, properties)
    assert first == second


# ---------------------------------------------------------------------------
# card_id_of / set_id_of — determinism sanity.
# ---------------------------------------------------------------------------


def test_card_id_of_is_deterministic_and_order_sensitive_to_params_only() -> None:
    a = card_id_of("i:" + CHECKOUT_1, "subject_facts", {"iri": CHECKOUT_1})
    b = card_id_of("i:" + CHECKOUT_1, "subject_facts", {"iri": CHECKOUT_1})
    assert a == b
    c = card_id_of("i:" + CHECKOUT_2, "subject_facts", {"iri": CHECKOUT_2})
    assert a != c


def test_set_id_of_is_deterministic_for_normalized_spec() -> None:
    spec = normalize_set_spec({"class": CHECKOUT_CLASS, "where": []})
    assert set_id_of(spec) == set_id_of(spec)
    assert set_id_of(spec).startswith("set-")


# ---------------------------------------------------------------------------
# pick_class_iri / subject_types.
# ---------------------------------------------------------------------------


async def test_subject_types_returns_sorted_rdf_types() -> None:
    types = await subject_types(_client(), CHECKOUT_1)
    assert types == [CHECKOUT_CLASS]


async def test_pick_class_iri_empty_list_is_none() -> None:
    assert await pick_class_iri(_client(), []) is None


async def test_pick_class_iri_falls_back_to_first_when_none_is_an_ontology_class() -> None:
    # No ontology graph published at all -> the first type wins by construction.
    result = await pick_class_iri(_client(), [CHECKOUT_CLASS, READING_CLASS])
    assert result == CHECKOUT_CLASS


# ---------------------------------------------------------------------------
# materials_for_subject — §3.3. registry の meta.json だけでは出どころ／
# ライセンスは引けない（asterism.licenses / asterism_api.registry.dataset_origin
# は D1-license の持ち場 — ここでは「不明」側に倒れることだけを固定する）。
# ---------------------------------------------------------------------------


async def test_materials_for_subject_shape(tmp_path: Path) -> None:
    _write_registry(tmp_path)
    materials = await materials_for_subject(_client(), CHECKOUT_1, registry_root=tmp_path)
    assert len(materials) == 1
    entry = materials[0]
    assert entry.dataset_id == LIB_DATASET
    assert entry.dataset_label == "貸出記録"
    assert entry.snapshot == "v1"
    assert entry.license is None
    assert entry.redistributable is None
    assert entry.kind == "unknown"
    assert entry.count > 0


async def test_materials_for_subject_empty_for_absent_iri(tmp_path: Path) -> None:
    materials = await materials_for_subject(_client(), NOWHERE, registry_root=tmp_path)
    assert materials == []


# ---------------------------------------------------------------------------
# SPARQL value escaping — no string-concatenated raw injection (§0 grep point).
# ---------------------------------------------------------------------------


async def test_set_members_eq_value_with_quote_is_escaped_not_injected() -> None:
    spec = {
        "class": CHECKOUT_CLASS,
        "where": [{"property": BRANCH_PRED, "op": "eq", "value": 'north" } } #'}],
    }
    out = await set_members(_client(), spec)  # must not raise / must not match anything
    assert out["items"] == []


async def test_load_query_tools_sees_the_fixture_dataset(tmp_path: Path) -> None:
    _write_registry(tmp_path)
    tools = load_query_tools(LIB_DATASET, root=tmp_path)
    assert {t.name for t in tools} == {"overdue_days", "needs_two_iris", "plain_facts_tool"}


@pytest.mark.parametrize(
    "bad_iri",
    ['https://ex/x" ?bad ?y ?z . }', "http://x/y}"],
)
async def test_subject_facts_rejects_iri_with_unsafe_sparql_chars(bad_iri: str) -> None:
    # Defense in depth: even if a caller reaches subject_facts directly with
    # an IRI that skipped subjects.validate_subject_key, `_ref`/`_safe_iri`
    # must refuse to embed it rather than let the store see a broken query.
    with pytest.raises(SubjectToolError):
        await subject_facts(_client(), bad_iri)


@pytest.mark.parametrize(
    "bad_iri",
    ['https://ex/x" ?bad ?y ?z . }', "http://x/y}"],
)
async def test_set_members_where_property_with_unsafe_chars_is_set_spec_error(
    bad_iri: str,
) -> None:
    spec = {"class": CHECKOUT_CLASS, "where": [{"property": bad_iri, "op": "eq", "value": "a"}]}
    with pytest.raises(SetSpecError):
        await set_members(_client(), spec)


@pytest.mark.parametrize(
    "bad_iri",
    ['https://ex/x" ?bad ?y ?z . }', "http://x/y}"],
)
async def test_set_breakdown_property_param_with_unsafe_chars_is_set_spec_error(
    bad_iri: str,
) -> None:
    spec = {"class": CHECKOUT_CLASS, "where": []}
    with pytest.raises(SetSpecError):
        await set_breakdown(_client(), spec, bad_iri)


async def test_query_tool_error_surfaces_as_subject_tool_error(tmp_path: Path) -> None:
    # run_subject_tool wraps a QueryToolError (unknown argument) into a
    # SubjectToolError — one exception vocabulary at the cards/run boundary.
    _write_registry(tmp_path)
    subject = validate_subject_key({"kind": "individual", "iri": CHECKOUT_1})
    with pytest.raises(SubjectToolError):
        await run_subject_tool(
            _client(),
            tmp_path,
            subject,
            f"{LIB_DATASET}/needs_two_iris",
            {"not_a_declared_param": "x"},
        )


# ---------------------------------------------------------------------------
# subject_facts の項目ラベル優先順位（契約メモ §6）: ontology の rdfs:label →
# class_schema（Mapping IR / display-meta の label）→ ローカル名の人間化。
# 共有フィクスチャとは独立の、3 個目の架空分野（配送の荷物記録）。
# ---------------------------------------------------------------------------

EX_DEPOT = "https://ex/depot#"
PARCEL_CLASS = EX_DEPOT + "Parcel"
WEIGHT_PRED = EX_DEPOT + "weightKg"
NOTE_PRED = EX_DEPOT + "note"
CARRIER_PRED = EX_DEPOT + "carrier"

DEPOT_DATASET = "depot-log"
DEPOT_GRAPH = canonical_graph_iri(DEPOT_DATASET) + "/v1"
DEPOT_ONTOLOGY_GRAPH = ONTOLOGY_GRAPH_BASE + DEPOT_DATASET

PARCEL_1 = "https://ex/depot/resource/parcel-1"

_DEPOT_TTL = f"""
@prefix depot: <{EX_DEPOT}> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

<{PARCEL_1}> a <{PARCEL_CLASS}> ;
    depot:weightKg "12.5"^^xsd:double ;
    depot:note "handle with care" ;
    depot:carrier "Acme" .
"""

# ontology projection: ONLY weightKg carries an rdfs:label — note/carrier have
# none, so they must fall through to the next tiers.
_DEPOT_ONTOLOGY_TTL = f"""
@prefix depot: <{EX_DEPOT}> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

depot:weightKg rdfs:label "重さ" .
"""

_DEPOT_MAPPING_YAML = """
version: 1
prefixes:
  depot: "https://ex/depot#"
  depotr: "https://ex/depot/resource/"
maps:
  - name: parcel
    source: parcels.csv
    subject:
      template: "depotr:parcel/{code}"
      classes: [depot:Parcel]
    properties:
      - predicate: depot:note
        column: note
        label: "メモ"
"""


def _depot_client() -> object:
    return _pyoxi_client({DEPOT_GRAPH: _DEPOT_TTL, DEPOT_ONTOLOGY_GRAPH: _DEPOT_ONTOLOGY_TTL})


def _write_depot_registry(root: Path) -> None:
    dest = root / DEPOT_DATASET
    dest.mkdir(parents=True)
    meta = {"id": DEPOT_DATASET, "promoted": True, "promoted_at": "2024-01-01"}
    (dest / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    (dest / "mapping.yaml").write_text(_DEPOT_MAPPING_YAML, encoding="utf-8")


async def test_subject_facts_property_label_from_ontology_graph(tmp_path: Path) -> None:
    _write_depot_registry(tmp_path)
    out = await subject_facts(_depot_client(), PARCEL_1, registry_root=tmp_path)
    row = next(i for i in out["items"] if i["property_iri"] == WEIGHT_PRED)
    assert row["property"] == "重さ"


async def test_subject_facts_property_label_falls_back_to_class_schema(tmp_path: Path) -> None:
    _write_depot_registry(tmp_path)
    out = await subject_facts(_depot_client(), PARCEL_1, registry_root=tmp_path)
    row = next(i for i in out["items"] if i["property_iri"] == NOTE_PRED)
    # No ontology rdfs:label for depot:note — falls to the Mapping IR's own
    # authored label, read via asterism.class_schema.
    assert row["property"] == "メモ"


async def test_subject_facts_property_label_humanizes_as_last_resort(tmp_path: Path) -> None:
    _write_depot_registry(tmp_path)
    out = await subject_facts(_depot_client(), PARCEL_1, registry_root=tmp_path)
    row = next(i for i in out["items"] if i["property_iri"] == CARRIER_PRED)
    # No ontology label, not in the Mapping IR at all — humanized local name,
    # never the raw predicate IRI (K4).
    assert row["property"] == "carrier"


async def test_subject_facts_property_label_without_registry_root_still_humanizes() -> None:
    # registry_root=None (default) must not crash — class_schema lookup is
    # best-effort and simply contributes nothing.
    out = await subject_facts(_depot_client(), PARCEL_1)
    row = next(i for i in out["items"] if i["property_iri"] == NOTE_PRED)
    assert row["property"] == "note"


# ---------------------------------------------------------------------------
# set_members のラベル（契約メモ §2 の実機所見）: rdfs:label が無く
# https://schema.org/name だけの主語でも、ラベルが IRI 末尾に落ちない。
# ---------------------------------------------------------------------------

EX_YARD = "https://ex/yard#"
CRATE_CLASS = EX_YARD + "Crate"
CRATE_1 = "https://ex/yard/resource/crate-1"

_YARD_TTL = f"""
@prefix yard: <{EX_YARD}> .

<{CRATE_1}> a <{CRATE_CLASS}> ;
    <https://schema.org/name> "Crate One" .
"""

YARD_DATASET = "yard-stock"
YARD_GRAPH = canonical_graph_iri(YARD_DATASET) + "/v1"


async def test_set_members_label_uses_schema_name_when_no_rdfs_label() -> None:
    client = _pyoxi_client({YARD_GRAPH: _YARD_TTL})
    spec = {"class": CRATE_CLASS, "where": []}
    out = await set_members(client, spec)
    item = next(i for i in out["items"] if i["subject_iri"] == CRATE_1)
    assert item["label"] == "Crate One"


@pytest.mark.asyncio
async def test_subject_flow_type_label_uses_class_label(monkeypatch: pytest.MonkeyPatch) -> None:
    """The flow card names a node's kind the way the rail does (registry label),
    not by the class IRI's local name (K4: one name per thing)."""
    import asterism.class_schema as cs

    async def fake_label(client: object, root: object, class_iri: str) -> str:
        return "貸出" if "Checkout" in class_iri else class_iri.rsplit("#", 1)[-1]

    monkeypatch.setattr(cs, "class_label", fake_label)
    out = await subject_flow(_client(), CHECKOUT_1)
    labels = {
        n["props"].get("type"): n["props"].get("type_label")
        for n in out["graph"]["nodes"]
        if n.get("props", {}).get("type")
    }
    assert any(v == "貸出" for v in labels.values()), labels
    assert all("://" not in (v or "") for v in labels.values())


def test_local_name_decodes_percent_encoding() -> None:
    """A subject minted from a key value keeps the value URL-encoded in its
    IRI; the heading fallback must show the value, not ``%28…%29`` (K4)."""
    from asterism.subject_tools import _local_name

    assert _local_name("https://ex/onto/resource/record/%280%2C0%2C10%29") == "(0,0,10)"
    assert _local_name("https://ex/onto#hasCount") == "hasCount"
    assert _local_name("https://ex/onto/resource/plain") == "plain"


# ----------------------------------------------------------------------------
# linking_kinds — 近傍（上に 1 段・下に 2 段・4 形）（契約メモ
# contract_pr_f14.md §1.1・ADR O59）。架空データ:
#   item-1 → parent-1 ← sibling-{1,2,3}（3 件・sibling） ; sibling-1 ← detail-
#   {1,2}（2 件・sibling_child） ; child-a → item-1（direct） ; grandchild-1 →
#   child-a（child_child）; 別の親 (unrelated-parent) を持つ無関係な記録も
#   置いて混ざらないことを見る。
# ----------------------------------------------------------------------------

from asterism.subject_tools import linking_kinds  # noqa: E402

EX_NBH = "https://ex/neighborhood#"
NBH_RECORD_CLASS = EX_NBH + "Record"
NBH_GROUP_CLASS = EX_NBH + "Group"
NBH_PART_OF = EX_NBH + "partOf"
NBH_REFERS_TO = EX_NBH + "refersTo"

NBH_DATASET = "neighborhood-log"
NBH_GRAPH = canonical_graph_iri(NBH_DATASET) + "/v1"

NBH_ITEM = "https://ex/neighborhood/resource/item-1"
NBH_PARENT = "https://ex/neighborhood/resource/parent-1"
NBH_SIBLINGS = [f"https://ex/neighborhood/resource/sibling-{n}" for n in (1, 2, 3)]
NBH_DETAILS = [f"https://ex/neighborhood/resource/detail-{n}" for n in (1, 2)]
NBH_CHILD_A = "https://ex/neighborhood/resource/child-a"
NBH_GRANDCHILD = "https://ex/neighborhood/resource/grandchild-1"
NBH_UNRELATED_PARENT = "https://ex/neighborhood/resource/unrelated-parent"
NBH_UNRELATED_SIBLING = "https://ex/neighborhood/resource/unrelated-sibling"
NBH_UNRELATED_DIRECT = "https://ex/neighborhood/resource/unrelated-direct"

_NBH_TTL = f"""
@prefix ex: <{EX_NBH}> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .

<{NBH_ITEM}> a <{NBH_RECORD_CLASS}> ; ex:partOf <{NBH_PARENT}> .
<{NBH_PARENT}> a <{NBH_GROUP_CLASS}> .

<{NBH_SIBLINGS[0]}> a <{NBH_RECORD_CLASS}> ; ex:partOf <{NBH_PARENT}> .
<{NBH_SIBLINGS[1]}> a <{NBH_RECORD_CLASS}> ; ex:partOf <{NBH_PARENT}> .
<{NBH_SIBLINGS[2]}> a <{NBH_RECORD_CLASS}> ; ex:partOf <{NBH_PARENT}> .

<{NBH_DETAILS[0]}> a <{NBH_RECORD_CLASS}> ; ex:refersTo <{NBH_SIBLINGS[0]}> .
<{NBH_DETAILS[1]}> a <{NBH_RECORD_CLASS}> ; ex:refersTo <{NBH_SIBLINGS[0]}> .

<{NBH_CHILD_A}> a <{NBH_RECORD_CLASS}> ; ex:refersTo <{NBH_ITEM}> .
<{NBH_GRANDCHILD}> a <{NBH_RECORD_CLASS}> ; ex:refersTo <{NBH_CHILD_A}> .

<{NBH_UNRELATED_PARENT}> a <{NBH_GROUP_CLASS}> .
<{NBH_UNRELATED_SIBLING}> a <{NBH_RECORD_CLASS}> ; ex:partOf <{NBH_UNRELATED_PARENT}> .
<{NBH_UNRELATED_DIRECT}> a <{NBH_RECORD_CLASS}> ; ex:refersTo <{NBH_UNRELATED_PARENT}> .
"""


def _nbh_client() -> object:
    return _pyoxi_client({NBH_GRAPH: _NBH_TTL})


async def test_linking_kinds_direct_counts_records_pointing_directly_at_the_item() -> None:
    out = await linking_kinds(_nbh_client(), NBH_ITEM)
    direct = [r for r in out if r["path_kind"] == "direct"]
    assert len(direct) == 1
    row = direct[0]
    assert row["class_iri"] == NBH_RECORD_CLASS
    assert row["property"] == NBH_REFERS_TO
    assert row["count"] == 1  # only child-a; unrelated-direct points elsewhere
    assert row["hops"] == 1
    assert row["where"] == [{"property": NBH_REFERS_TO, "iri": NBH_ITEM}]


async def test_linking_kinds_child_child_counts_a_grandchild_of_the_item() -> None:
    out = await linking_kinds(_nbh_client(), NBH_ITEM)
    child_child = [r for r in out if r["path_kind"] == "child_child"]
    assert len(child_child) == 1
    row = child_child[0]
    assert row["count"] == 1  # grandchild-1, via child-a
    assert row["hops"] == 2
    assert row["via"]["property"] == NBH_REFERS_TO
    assert row["where"] == [
        {"property": NBH_REFERS_TO, "via": {"property": NBH_REFERS_TO, "iri": NBH_ITEM}}
    ]


async def test_linking_kinds_sibling_counts_records_sharing_the_same_parent() -> None:
    out = await linking_kinds(_nbh_client(), NBH_ITEM)
    sibling = [r for r in out if r["path_kind"] == "sibling"]
    assert len(sibling) == 1
    row = sibling[0]
    assert row["count"] == 3  # sibling-{1,2,3}; item-1 itself excluded
    assert row["hops"] == 2
    assert row["anchor_iri"] == NBH_PARENT
    assert row["anchor_property"] == NBH_PART_OF
    assert row["where"] == [{"property": NBH_PART_OF, "iri": NBH_PARENT}]


async def test_linking_kinds_sibling_child_counts_records_pointing_at_a_sibling() -> None:
    out = await linking_kinds(_nbh_client(), NBH_ITEM)
    sibling_child = [r for r in out if r["path_kind"] == "sibling_child"]
    assert len(sibling_child) == 1
    row = sibling_child[0]
    assert row["count"] == 2  # detail-{1,2}, both pointing at sibling-1
    assert row["hops"] == 3
    assert row["anchor_iri"] == NBH_PARENT
    assert row["where"] == [
        {"property": NBH_REFERS_TO, "via": {"property": NBH_PART_OF, "iri": NBH_PARENT}}
    ]


async def test_linking_kinds_unrelated_parent_does_not_mix_in() -> None:
    """A sibling sharing an unrelated parent (not item-1's) never appears —
    and a direct record pointing elsewhere doesn't count toward item-1."""
    out = await linking_kinds(_nbh_client(), NBH_ITEM)
    for row in out:
        assert row["anchor_iri"] != NBH_UNRELATED_PARENT
    direct = next(r for r in out if r["path_kind"] == "direct")
    assert direct["count"] == 1  # not 2 (would be 2 if unrelated-direct leaked in)


async def test_linking_kinds_sorted_by_hops_then_count_descending() -> None:
    out = await linking_kinds(_nbh_client(), NBH_ITEM)
    hops = [r["hops"] for r in out]
    assert hops == sorted(hops)
    # within hops == 2: sibling (count 3) sorts before child_child (count 1).
    same_hops = [r for r in out if r["hops"] == 2]
    assert [r["path_kind"] for r in same_hops] == ["sibling", "child_child"]


async def test_linking_kinds_direct_existing_keys_are_unchanged() -> None:
    """The pre-existing (ADR O46) fields on the direct row are untouched —
    only new fields are added (契約メモ §0: 既存の返り値のフィールドは削らな
    い)."""
    out = await linking_kinds(_nbh_client(), NBH_ITEM)
    row = next(r for r in out if r["path_kind"] == "direct")
    assert row["class_iri"] == NBH_RECORD_CLASS
    assert isinstance(row["class_label"], str)
    assert row["property"] == NBH_REFERS_TO
    assert isinstance(row["property_label"], str)
    assert row["count"] == 1


async def test_linking_kinds_empty_for_a_record_nothing_points_at() -> None:
    out = await linking_kinds(_nbh_client(), "https://ex/neighborhood/resource/does-not-exist")
    assert out == []


async def test_linking_kinds_respects_the_parent_member_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A parent whose member count exceeds the cap is not a neighborhood
    (契約メモ §1.1) — lower the cap to below parent-1's 3 siblings and see
    the sibling/sibling_child forms disappear."""
    import asterism.subject_tools as st

    monkeypatch.setattr(st, "_NEIGHBORHOOD_MAX_PARENT_MEMBERS", 2)
    out = await linking_kinds(_nbh_client(), NBH_ITEM)
    assert not any(r["path_kind"] in ("sibling", "sibling_child") for r in out)
    # direct/child_child are unaffected by the parent cap.
    assert any(r["path_kind"] == "direct" for r in out)
    assert any(r["path_kind"] == "child_child" for r in out)


# ----------------------------------------------------------------------------
# hub_of_subject / linking_kinds(hub) / subject_hub_members / default_cards_
# for_subject(hub) — 共有ハブ（xw: の共有実体）を「同じものの 1 つのページ」
# として扱う（契約メモ contract_pr_f16.md §1.1/§1.3・ADR O60）。架空データ:
# 2 データセット（hub-catalogue-a/b）＋ハブ graph。member-1 はハブへ直接
# （1 段）・record-1 は member-1 を親に持つ（member-1 経由の 2 段）。
# member-2/record-2 も同じ形（データセットが違う２件目のメンバー）。
# ----------------------------------------------------------------------------

EX_HUB_A = "https://ex/hub-a#"
HUB_A_MEMBER_CLASS = EX_HUB_A + "Member"
HUB_A_RECORD_CLASS = EX_HUB_A + "Record"
HUB_A_REFERS_TO = EX_HUB_A + "refersTo"

HUB_DATASET_A = "hub-catalogue-a"
HUB_A_GRAPH = canonical_graph_iri(HUB_DATASET_A) + "/v1"
HUB_MEMBER_1 = "https://ex/hub-a/resource/member-1"
HUB_RECORD_1 = "https://ex/hub-a/resource/record-1"

_HUB_A_TTL = f"""
@prefix ex: <{EX_HUB_A}> .

<{HUB_MEMBER_1}> a <{HUB_A_MEMBER_CLASS}> .
<{HUB_RECORD_1}> a <{HUB_A_RECORD_CLASS}> ; <{HUB_A_REFERS_TO}> <{HUB_MEMBER_1}> .
"""

EX_HUB_B = "https://ex/hub-b#"
HUB_B_MEMBER_CLASS = EX_HUB_B + "Member"
HUB_B_RECORD_CLASS = EX_HUB_B + "Record"
HUB_B_REFERS_TO = EX_HUB_B + "refersTo"

HUB_DATASET_B = "hub-catalogue-b"
HUB_B_GRAPH = canonical_graph_iri(HUB_DATASET_B) + "/v1"
HUB_MEMBER_2 = "https://ex/hub-b/resource/member-2"
HUB_RECORD_2 = "https://ex/hub-b/resource/record-2"

_HUB_B_TTL = f"""
@prefix ex: <{EX_HUB_B}> .

<{HUB_MEMBER_2}> a <{HUB_B_MEMBER_CLASS}> .
<{HUB_RECORD_2}> a <{HUB_B_RECORD_CLASS}> ; <{HUB_B_REFERS_TO}> <{HUB_MEMBER_2}> .
"""

EX_HUB = "https://ex/hub#"
HUB_CLASS = EX_HUB + "Shared"
HUB_LINK_PREDICATE = EX_HUB + "hasShared"
HUB_IRI = "https://ex/hub/resource/shared-1"
HUB_GRAPH = canonical_graph_iri("crosswalk")  # legacy (composition) perspective
_HUB_GRAPH_TTL = f"""
@prefix ex: <{EX_HUB}> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

<{HUB_IRI}> a <{HUB_CLASS}> ; rdfs:label "共有された1件" .
<{HUB_MEMBER_1}> <{HUB_LINK_PREDICATE}> <{HUB_IRI}> .
<{HUB_MEMBER_2}> <{HUB_LINK_PREDICATE}> <{HUB_IRI}> .
"""


def _hub_client() -> object:
    return _pyoxi_client(
        {HUB_A_GRAPH: _HUB_A_TTL, HUB_B_GRAPH: _HUB_B_TTL, HUB_GRAPH: _HUB_GRAPH_TTL}
    )


def _write_hub_registry(root: Path) -> None:
    for dataset_id, name in ((HUB_DATASET_A, "台帳A"), (HUB_DATASET_B, "台帳B")):
        dest = root / dataset_id
        dest.mkdir(parents=True)
        meta = {"id": dataset_id, "name": name, "promoted": True, "version": 1}
        (dest / "meta.json").write_text(json.dumps(meta), encoding="utf-8")


async def test_hub_of_subject_true_for_the_hub_itself() -> None:
    out = await hub_of_subject(_hub_client(), HUB_IRI)
    assert out == {"hub_iri": HUB_IRI, "graph": HUB_GRAPH, "perspective_id": "composition"}


async def test_hub_of_subject_one_hop_for_a_direct_member() -> None:
    out = await hub_of_subject(_hub_client(), HUB_MEMBER_1)
    assert out is not None
    assert out["hub_iri"] == HUB_IRI
    assert out["hub_label"] == "共有された1件"
    assert out["perspective_id"] == "composition"
    assert out["via_parent"] is None


async def test_hub_of_subject_two_hops_via_parent() -> None:
    """record-1 → member-1（親）→（hub graph 内）hub — メンバー自身が親として
    記録から指されている 2 段の形（契約メモ §1.1: 「メンバー → 親 ← 記録」）。"""
    out = await hub_of_subject(_hub_client(), HUB_RECORD_1)
    assert out is not None
    assert out["hub_iri"] == HUB_IRI
    assert out["via_parent"] == HUB_MEMBER_1

    out2 = await hub_of_subject(_hub_client(), HUB_RECORD_2)
    assert out2 is not None
    assert out2["hub_iri"] == HUB_IRI
    assert out2["via_parent"] == HUB_MEMBER_2


async def test_hub_of_subject_none_when_no_path_to_a_hub() -> None:
    """ハブでない主語では何も変わらない（契約メモ §3 の検証観点）— ハブ graph
    が全く無いストアでも通常主語には安全に ``None``。"""
    assert await hub_of_subject(_nbh_client(), NBH_ITEM) is None
    assert await hub_of_subject(_hub_client(), "https://ex/hub-a/resource/does-not-exist") is None


async def test_linking_kinds_hub_unions_each_members_neighborhood() -> None:
    """契約メモ §1.3: ハブ主語の候補はメンバーごとの ``linking_kinds`` の和 —
    どちらのデータセットの記録の種類も出て、``where`` はメンバー起点の絶対 IRI
    のまま、``hops`` はメンバー自身の近傍より 1 つ多い。"""
    out = await linking_kinds(_hub_client(), HUB_IRI)

    row_a = next(r for r in out if r["class_iri"] == HUB_A_RECORD_CLASS)
    assert row_a["hops"] == 2  # member-1 の direct (hops=1) + 1
    assert row_a["where"] == [{"property": HUB_A_REFERS_TO, "iri": HUB_MEMBER_1}]
    assert row_a["via_member"]["iri"] == HUB_MEMBER_1
    assert row_a["via_member"]["dataset_id"] == HUB_DATASET_A

    row_b = next(r for r in out if r["class_iri"] == HUB_B_RECORD_CLASS)
    assert row_b["hops"] == 2
    assert row_b["where"] == [{"property": HUB_B_REFERS_TO, "iri": HUB_MEMBER_2}]
    assert row_b["via_member"]["iri"] == HUB_MEMBER_2
    assert row_b["via_member"]["dataset_id"] == HUB_DATASET_B


async def test_linking_kinds_hub_respects_the_member_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """メンバーは最大 8・IRI 辞書順（契約メモ §1.3） — cap を 1 に下げると
    辞書順で先の member-1 しか union されない。"""
    import asterism.subject_tools as st

    monkeypatch.setattr(st, "_HUB_MEMBER_LIMIT", 1)
    out = await linking_kinds(_hub_client(), HUB_IRI)
    assert not any(r["class_iri"] == HUB_B_RECORD_CLASS for r in out)
    assert any(r["class_iri"] == HUB_A_RECORD_CLASS for r in out)


async def test_linking_kinds_non_hub_subject_is_unaffected_by_hub_branch() -> None:
    """record-1 はハブでない（record-1 → member-1 だけで、record-1 を指す記録
    は無い）— ハブ graph がストアにあっても F14 までの挙動から何も変わらず、
    候補は空のまま。"""
    out = await linking_kinds(_hub_client(), HUB_RECORD_1)
    assert out == []


async def test_linking_kinds_member_page_reaches_the_other_member_via_the_hub() -> None:
    """メンバー自身のページでは、ハブが「親」になる（member-1 → hub ← member-2）
    ので、別データセットの同じもの（member-2）が兄弟として、member-2 を指す
    記録が兄弟を指す記録として出る — 別データセットの同じ対象を 1 ページで
    扱う、というユーザーの狙いそのもの。per-link の来歴（xw:CrosswalkLink）は
    記録の種類に混ざらない。"""
    out = await linking_kinds(_hub_client(), HUB_MEMBER_1)
    sibling = next(r for r in out if r["class_iri"] == HUB_B_MEMBER_CLASS)
    assert sibling["path_kind"] == "sibling"
    assert sibling["anchor_iri"] == HUB_IRI
    assert sibling["where"] == [{"property": sibling["property"], "iri": HUB_IRI}]
    sibling_child = next(r for r in out if r["class_iri"] == HUB_B_RECORD_CLASS)
    assert sibling_child["path_kind"] == "sibling_child"
    assert sibling_child["where"][0]["via"]["iri"] == HUB_IRI
    assert all("via_member" not in r for r in out)
    assert not any("CrosswalkLink" in r["class_iri"] for r in out)
    direct = next(r for r in out if r["path_kind"] == "direct")
    assert direct["class_iri"] == HUB_A_RECORD_CLASS
    assert direct["count"] == 1


async def test_subject_hub_members_lists_each_member_with_its_dataset(tmp_path: Path) -> None:
    _write_hub_registry(tmp_path)
    out = await subject_hub_members(_hub_client(), HUB_IRI, registry_root=tmp_path)
    assert out["output_kind"] == "facts"
    assert out["count"] == 2
    by_iri = {i["subject_iri"]: i for i in out["items"]}
    assert set(by_iri) == {HUB_MEMBER_1, HUB_MEMBER_2}
    assert by_iri[HUB_MEMBER_1]["dataset_label"] == "台帳A"
    assert by_iri[HUB_MEMBER_2]["dataset_label"] == "台帳B"
    assert by_iri[HUB_MEMBER_1]["class_label"] is not None


async def test_subject_hub_members_empty_for_a_non_hub_subject() -> None:
    out = await subject_hub_members(_hub_client(), HUB_RECORD_1)
    assert out["count"] == 0
    assert out["items"] == []


async def test_default_cards_for_subject_leads_with_hub_members_for_a_hub(
    tmp_path: Path,
) -> None:
    _write_hub_registry(tmp_path)
    cards = await default_cards_for_subject(_hub_client(), tmp_path, HUB_IRI)
    assert cards[0]["tool"] == "subject_hub_members"
    assert cards[1]["tool"] == "subject_facts"


async def test_default_cards_for_subject_unaffected_for_a_non_hub_subject(
    tmp_path: Path,
) -> None:
    """ハブでない主語では既定カードの並びも変わらない。"""
    _write_hub_registry(tmp_path)
    cards = await default_cards_for_subject(_hub_client(), tmp_path, HUB_RECORD_1)
    assert cards[0]["tool"] == "subject_facts"
    assert "subject_hub_members" not in [c["tool"] for c in cards]
