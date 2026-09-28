"""Tests for asterism.prov_graph — the generic PROV-O graph reader (object-cards-ui.md §4).

Backed by a real pyoxigraph.Store, same contract as ``_pyoxi_client`` in
test_query_tools.py: each {graph_iri: ttl} loads into that named graph, and
canonical-base graphs are flagged ``promoted`` so ``canonical_graphs`` finds
them. Fixture data spans two unrelated fictional domains (weather-observation
logs and manuscript analysis) so no test asserts on a single dataset's shape.
"""
from __future__ import annotations

import pytest

from asterism.prov_graph import GRAPH_NODE_KINDS, PROV, prov_graph
from asterism.substrate import (
    CANONICAL_GRAPH_BASE,
    CONTROL_GRAPH_IRI,
    STATUS_PREDICATE,
    STATUS_PROMOTED,
    canonical_graph_iri,
)

pyoxigraph = pytest.importorskip("pyoxigraph")


def _pyoxi_client(graphs: dict[str, str]):
    """pyoxigraph-backed client: each {graph_iri: ttl} loaded to that named
    graph, canonical graphs flagged ``promoted`` in the control graph — same
    contract as ``asterism_ingest``'s ``_pyoxi_client`` in test_query_tools.py,
    backed by the real store engine rather than a mock.
    """
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
                    kind = "uri" if isinstance(term, pyoxigraph.NamedNode) else "literal"
                    row[name] = {"type": kind, "value": term.value}
                bindings.append(row)
            return {"results": {"bindings": bindings}}

    return _C()


# ---------------------------------------------------------------------------
# Fixture data — two unrelated fictional domains, one dataset each.
# ---------------------------------------------------------------------------

WEATHERLOG_DATASET = "weatherlog"
WEATHERLOG_GRAPH = canonical_graph_iri(WEATHERLOG_DATASET) + "/v1"

EX_W = "https://ex/weatherlog#"
DIGEST = EX_W + "digest-2024-01"
ACTIVITY_AGGREGATE = EX_W + "activity-aggregate"
RECORD_OBS_9 = EX_W + "record-obs-9"
AGENT_AGGREGATOR = EX_W + "agent-aggregator"
AGENT_JANE = EX_W + "agent-jane"
ACTIVITY_COLLECT = EX_W + "activity-collect"
NOWHERE = EX_W + "does-not-exist"

_WEATHERLOG_TTL = """
@prefix ex: <https://ex/weatherlog#> .
@prefix prov: <http://www.w3.org/ns/prov#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix schema: <http://schema.org/> .
@prefix dcterms: <http://purl.org/dc/terms/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

ex:digest-2024-01
    a prov:Entity ;
    rdfs:label "Daily Summary (rdfs)" ;
    schema:name "Daily Summary (schema)" ;
    prov:wasGeneratedBy ex:activity-aggregate ;
    prov:wasDerivedFrom ex:record-obs-9 .

ex:activity-aggregate
    a prov:Activity ;
    prov:used ex:record-obs-9 ;
    prov:startedAtTime "2024-01-02T00:00:00Z"^^xsd:dateTime ;
    prov:endedAtTime "2024-01-02T00:05:00Z"^^xsd:dateTime ;
    prov:wasAssociatedWith ex:agent-aggregator ;
    prov:wasInformedBy ex:activity-collect .

ex:record-obs-9
    a prov:Entity ;
    schema:name "Morning Reading" ;
    prov:wasAttributedTo ex:agent-jane .

ex:agent-aggregator
    a prov:SoftwareAgent ;
    schema:name "Aggregator Bot" .

ex:agent-jane
    a prov:Person ;
    rdfs:label "Jane"@en, "ジェーン"@ja .

ex:activity-collect
    a prov:Activity ;
    dcterms:title "Collection Run" .
"""

MANUSCRIPTS_DATASET = "manuscripts"
MANUSCRIPTS_GRAPH = canonical_graph_iri(MANUSCRIPTS_DATASET) + "/v1"

EX_M = "https://ex/manuscripts#"
SENTENCE = EX_M + "sentence-42"
MANUSCRIPT = EX_M + "manuscript-doc7"
ACTIVITY_ANALYZE = EX_M + "activity-analyze"

_MANUSCRIPTS_TTL = """
@prefix ex2: <https://ex/manuscripts#> .
@prefix prov: <http://www.w3.org/ns/prov#> .
@prefix dcterms: <http://purl.org/dc/terms/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

ex2:sentence-42
    prov:wasQuotedFrom ex2:manuscript-doc7 ;
    prov:wasGeneratedBy ex2:activity-analyze .

ex2:manuscript-doc7
    a ex2:Manuscript ;
    dcterms:title "Field Notes" .

ex2:activity-analyze
    a prov:Activity ;
    prov:atTime "2024-02-01T10:00:00Z"^^xsd:dateTime .
"""


def _client():
    return _pyoxi_client(
        {WEATHERLOG_GRAPH: _WEATHERLOG_TTL, MANUSCRIPTS_GRAPH: _MANUSCRIPTS_TTL}
    )


def _edge_set(out: dict) -> set[tuple[str, str, str]]:
    return {(e["from"], e["to"], e["label"]) for e in out["graph"]["edges"]}


def _node_by_id(out: dict, node_id: str) -> dict:
    return next(n for n in out["graph"]["nodes"] if n["id"] == node_id)


# ---------------------------------------------------------------------------
# Constant sanity
# ---------------------------------------------------------------------------


def test_graph_node_kinds_is_the_contract_vocabulary() -> None:
    assert GRAPH_NODE_KINDS == ("entity", "activity", "other")


def test_prov_namespace_constant() -> None:
    assert PROV == "http://www.w3.org/ns/prov#"


# ---------------------------------------------------------------------------
# Direction normalization — all 7 predicates, both fictional domains.
# ---------------------------------------------------------------------------


async def test_six_predicates_normalize_from_weatherlog_domain() -> None:
    out = await prov_graph(_client(), DIGEST)
    assert out["found"] is True
    edges = _edge_set(out)
    assert edges == {
        (ACTIVITY_AGGREGATE, DIGEST, "generated"),
        (RECORD_OBS_9, DIGEST, "derived"),
        (RECORD_OBS_9, ACTIVITY_AGGREGATE, "used"),
        (AGENT_AGGREGATOR, ACTIVITY_AGGREGATE, "associated"),
        (ACTIVITY_COLLECT, ACTIVITY_AGGREGATE, "informed"),
        (AGENT_JANE, RECORD_OBS_9, "attributed"),
    }


async def test_quoted_from_normalizes_in_manuscripts_domain() -> None:
    out = await prov_graph(_client(), SENTENCE)
    assert out["found"] is True
    edges = _edge_set(out)
    assert (MANUSCRIPT, SENTENCE, "quoted") in edges
    assert (ACTIVITY_ANALYZE, SENTENCE, "generated") in edges


# ---------------------------------------------------------------------------
# kind — the 3-way vocabulary, decided from rdf:type only.
# ---------------------------------------------------------------------------


async def test_kind_activity_from_prov_activity_type() -> None:
    out = await prov_graph(_client(), DIGEST)
    assert _node_by_id(out, ACTIVITY_AGGREGATE)["kind"] == "activity"
    assert _node_by_id(out, ACTIVITY_COLLECT)["kind"] == "activity"


async def test_kind_other_from_agent_subtypes() -> None:
    out = await prov_graph(_client(), DIGEST)
    assert _node_by_id(out, AGENT_AGGREGATOR)["kind"] == "other"  # prov:SoftwareAgent
    assert _node_by_id(out, AGENT_JANE)["kind"] == "other"  # prov:Person


async def test_kind_entity_default_including_untyped_and_custom_type() -> None:
    out = await prov_graph(_client(), DIGEST)
    assert _node_by_id(out, DIGEST)["kind"] == "entity"  # prov:Entity
    manuscripts_out = await prov_graph(_client(), SENTENCE)
    assert _node_by_id(manuscripts_out, SENTENCE)["kind"] == "entity"  # no rdf:type at all
    assert _node_by_id(manuscripts_out, MANUSCRIPT)["kind"] == "entity"  # dataset-specific type


async def test_kind_activity_from_local_name_suffix_on_a_non_prov_type() -> None:
    # Not `a prov:Activity` at all — a dataset-specific type whose IRI local
    # name merely ends in "Activity" must still classify as activity (the
    # `_kind_of` local-name fallback, not the `a prov:Activity` branch).
    origin = EX_W + "custom-typed-node"
    ttl = (
        _WEATHERLOG_TTL
        + f'\n<{origin}> a <{EX_W}CollectionActivity> ; rdfs:label "Custom" .\n'
    )
    client = _pyoxi_client({WEATHERLOG_GRAPH: ttl})
    out = await prov_graph(client, origin)
    assert _node_by_id(out, origin)["kind"] == "activity"


# ---------------------------------------------------------------------------
# label priority + K4 (never a raw IRI).
# ---------------------------------------------------------------------------


async def test_label_prefers_rdfs_label_over_schema_name() -> None:
    out = await prov_graph(_client(), DIGEST)
    assert _node_by_id(out, DIGEST)["label"] == "Daily Summary (rdfs)"


async def test_label_falls_back_to_dcterms_title() -> None:
    out = await prov_graph(_client(), DIGEST)
    assert _node_by_id(out, ACTIVITY_COLLECT)["label"] == "Collection Run"


async def test_label_prefers_japanese_over_english_tag() -> None:
    out = await prov_graph(_client(), DIGEST)
    assert _node_by_id(out, AGENT_JANE)["label"] == "ジェーン"


async def test_label_falls_back_to_iri_local_name_never_raw_iri() -> None:
    out = await prov_graph(_client(), SENTENCE)
    node = _node_by_id(out, SENTENCE)
    assert node["label"] == "sentence-42"
    assert node["label"] != SENTENCE  # K4: no raw identifier surfaced as a label


# ---------------------------------------------------------------------------
# dataset_id / snapshot attribution.
# ---------------------------------------------------------------------------


async def test_dataset_id_and_snapshot_from_the_version_graph() -> None:
    out = await prov_graph(_client(), DIGEST)
    props = _node_by_id(out, DIGEST)["props"]
    assert props["dataset_id"] == WEATHERLOG_DATASET
    assert props["snapshot"] == "v1"

    manuscripts_out = await prov_graph(_client(), SENTENCE)
    m_props = _node_by_id(manuscripts_out, MANUSCRIPT)["props"]
    assert m_props["dataset_id"] == MANUSCRIPTS_DATASET
    assert m_props["snapshot"] == "v1"


async def test_activity_time_props_pulled_when_present() -> None:
    out = await prov_graph(_client(), DIGEST)
    props = _node_by_id(out, ACTIVITY_AGGREGATE)["props"]
    assert props["started_at"] == "2024-01-02T00:00:00Z"
    assert props["ended_at"] == "2024-01-02T00:05:00Z"

    manuscripts_out = await prov_graph(_client(), SENTENCE)
    a_props = _node_by_id(manuscripts_out, ACTIVITY_ANALYZE)["props"]
    assert a_props["at_time"] == "2024-02-01T10:00:00Z"


# ---------------------------------------------------------------------------
# materials — deduped (dataset_id, snapshot), sorted.
# ---------------------------------------------------------------------------


async def test_materials_dedup_across_many_nodes_in_one_graph() -> None:
    out = await prov_graph(_client(), DIGEST)
    assert len(out["graph"]["nodes"]) >= 4  # several nodes, same version graph
    assert out["materials"] == [
        {"dataset_id": WEATHERLOG_DATASET, "snapshot": "v1", "graph": WEATHERLOG_GRAPH}
    ]


#: The manuscripts graph, PLUS one extra fact recorded in it about a
#: weatherlog-domain node (``digest-2024-01``) — a realistic cross-dataset
#: citation (a manuscript quoting a weather digest), so a single BFS from
#: ``DIGEST`` necessarily touches two different version graphs.
_MANUSCRIPTS_TTL_WITH_CROSS_LINK = _MANUSCRIPTS_TTL.replace(
    "@prefix ex2:",
    "@prefix ex: <https://ex/weatherlog#> .\n@prefix ex2:",
) + "\nex:digest-2024-01 prov:wasQuotedFrom ex2:manuscript-doc7 .\n"


async def test_materials_sorted_by_dataset_id_then_snapshot() -> None:
    client = _pyoxi_client(
        {WEATHERLOG_GRAPH: _WEATHERLOG_TTL, MANUSCRIPTS_GRAPH: _MANUSCRIPTS_TTL_WITH_CROSS_LINK}
    )
    out = await prov_graph(client, DIGEST)
    assert out["materials"] == [
        {"dataset_id": MANUSCRIPTS_DATASET, "snapshot": "v1", "graph": MANUSCRIPTS_GRAPH},
        {"dataset_id": WEATHERLOG_DATASET, "snapshot": "v1", "graph": WEATHERLOG_GRAPH},
    ]
    # The origin node itself must be attributed to its real home graph
    # (weatherlog, where it has many asserted facts), not to manuscripts —
    # which only quotes it once and happens to sort first alphabetically
    # (canonical/manuscripts/v1 < canonical/weatherlog/v1).
    origin_props = _node_by_id(out, DIGEST)["props"]
    assert origin_props["dataset_id"] == WEATHERLOG_DATASET
    assert origin_props["snapshot"] == "v1"


# ---------------------------------------------------------------------------
# max_depth / max_nodes truncation — deterministic thanks to ORDER BY.
# ---------------------------------------------------------------------------


async def test_max_depth_stops_the_walk_and_flags_truncated() -> None:
    out = await prov_graph(_client(), DIGEST, max_depth=1)
    ids = {n["id"] for n in out["graph"]["nodes"]}
    assert ids == {DIGEST, ACTIVITY_AGGREGATE, RECORD_OBS_9}
    assert out["graph"]["truncated"] is True


async def test_max_nodes_stops_the_walk_and_flags_truncated() -> None:
    out = await prov_graph(_client(), DIGEST, max_nodes=2)
    ids = {n["id"] for n in out["graph"]["nodes"]}
    # ORDER BY ?s ?pred ?o makes this deterministic: "wasDerivedFrom" sorts
    # before "wasGeneratedBy", so record-obs-9 is admitted first.
    assert ids == {DIGEST, RECORD_OBS_9}
    assert out["graph"]["truncated"] is True


async def test_no_truncation_flag_when_the_walk_fits() -> None:
    out = await prov_graph(_client(), DIGEST, max_depth=4, max_nodes=60)
    assert "truncated" not in out["graph"]


# ---------------------------------------------------------------------------
# found / invalid IRI.
# ---------------------------------------------------------------------------


async def test_found_false_for_an_iri_absent_from_every_version_graph() -> None:
    out = await prov_graph(_client(), NOWHERE)
    assert out == {
        "iri": NOWHERE,
        "found": False,
        "graph": {"nodes": [], "edges": []},
        "materials": [],
    }


async def test_found_true_with_no_edges_still_returns_the_origin_node() -> None:
    # agent-aggregator has an inbound `associated` edge in the fixture, so give
    # it its own isolated node with no prov edges at all.
    lonely = EX_W + "lonely-entity"
    ttl = _WEATHERLOG_TTL + '\nex:lonely-entity a prov:Entity ; rdfs:label "Alone" .\n'
    client = _pyoxi_client({WEATHERLOG_GRAPH: ttl})
    out = await prov_graph(client, lonely)
    assert out["found"] is True
    assert [n["id"] for n in out["graph"]["nodes"]] == [lonely]
    assert out["graph"]["edges"] == []
    assert _node_by_id(out, lonely)["label"] == "Alone"


async def test_no_published_version_graph_is_not_found_and_issues_no_graph_query() -> None:
    # Draft isolation: a store with zero promoted version graphs must never let
    # `GRAPH ?g` run unscoped (an empty FROM NAMED means it would range over
    # every named graph, draft included). Load the fixture WITHOUT flagging it
    # promoted — canonical_graphs() then returns [] — and confirm the origin
    # (which really does exist, in a draft-only graph) reads not-found rather
    # than being discovered through the unscoped fallback.
    store = pyoxigraph.Store()
    store.load(
        _WEATHERLOG_TTL.encode("utf-8"),
        mime_type="text/turtle",
        to_graph=pyoxigraph.NamedNode(WEATHERLOG_GRAPH),
    )

    class _DraftOnlyClient:
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
                    kind = "uri" if isinstance(term, pyoxigraph.NamedNode) else "literal"
                    row[name] = {"type": kind, "value": term.value}
                bindings.append(row)
            return {"results": {"bindings": bindings}}

    out = await prov_graph(_DraftOnlyClient(), DIGEST)
    assert out == {
        "iri": DIGEST,
        "found": False,
        "graph": {"nodes": [], "edges": []},
        "materials": [],
    }


async def test_invalid_iri_raises_value_error() -> None:
    client = _client()
    with pytest.raises(ValueError):
        await prov_graph(client, "not-an-iri")
    with pytest.raises(ValueError):
        await prov_graph(client, "<https://ex/weatherlog#digest-2024-01>")
    with pytest.raises(ValueError):
        await prov_graph(client, "")
