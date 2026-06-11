"""Crosswalk hub runtime (ADR crosswalk-hub.md productize ②): read the live store,
build the hub, write it back.

These run the REAL SPARQL the runtime issues (canonical-scope resolution, the two
bounded passes, the drop+post+flag write) against an in-memory ``rdflib.Dataset``,
so graph resolution, shared bounding, per-link provenance, and the promoted flag are
exercised end-to-end without a triplestore.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import rdflib

from asterism import substrate
from asterism.crosswalk_runtime import (
    ALIGNMENT_GRAPH,
    HUB_GRAPH,
    BuildOutcome,
    RuntimeConcept,
    RuntimeCrosswalkConfig,
    RuntimeParticipant,
    assert_alignment,
    build_hub,
    config_to_dict,
    crosswalk_graph_iri,
    crosswalk_registry_id,
    list_alignments,
    list_perspectives,
    load_config,
    parse_config,
    remove_alignment,
    remove_hub,
    save_config,
    write_registry_scaffold,
)

XW = "https://kumagallium.github.io/asterism/crosswalk/ontology#"
PRED = "https://kumagallium.github.io/asterism/x/ontology#comp"


class _DatasetClient:
    """OxigraphClient stand-in backed by a real rdflib Dataset: SELECT, UPDATE, and a
    Graph-Store ``post_turtle_bytes`` that parses Turtle into a named graph."""

    def __init__(self, ds: rdflib.Dataset) -> None:
        self.ds = ds
        self.posted: list[tuple[str, bytes]] = []

    async def sparql_select(self, query: str) -> dict:
        raw = self.ds.query(query).serialize(format="json")
        return json.loads(raw.decode() if isinstance(raw, bytes) else raw)

    async def sparql_update(self, update: str) -> None:
        self.ds.update(update)

    async def post_turtle_bytes(self, payload: bytes, graph_iri: str | None = None) -> int:
        g = self.ds.graph(rdflib.URIRef(graph_iri)) if graph_iri else self.ds.default_context
        g.parse(data=payload.decode("utf-8"), format="turtle")
        self.posted.append((graph_iri or "", payload))
        return len(payload)


def _mark_promoted(ds: rdflib.Dataset, key_graph: str) -> None:
    ds.update(
        f"INSERT DATA {{ GRAPH <{substrate.CONTROL_GRAPH_IRI}> {{ "
        f'<{key_graph}> <{substrate.STATUS_PREDICATE}> "promoted" }} }}'
    )


def _seed_dataset(ds: rdflib.Dataset, dataset_id: str, rows: list[tuple[str, str]]) -> str:
    """Put ``(entity, raw)`` rows into a dataset's promoted key graph; return its IRI."""
    key = substrate.canonical_graph_iri(dataset_id)
    g = ds.graph(rdflib.URIRef(key))
    for entity, raw in rows:
        g.add((rdflib.URIRef(entity), rdflib.URIRef(PRED), rdflib.Literal(raw)))
        g.add((rdflib.URIRef(entity), rdflib.RDF.type, rdflib.URIRef(f"{PRED}/Thing")))
    _mark_promoted(ds, key)
    return key


def _composition_config(participants: list[tuple[str, str]]) -> RuntimeCrosswalkConfig:
    return RuntimeCrosswalkConfig(
        concepts=(
            RuntimeConcept(
                name="composition",
                class_iri=f"{XW}Composition",
                link_predicate=f"{XW}hasComposition",
                normalizer="composition",
                participants=tuple(
                    RuntimeParticipant(dataset_id=dsid, label=label, predicate=PRED)
                    for dsid, label in participants
                ),
            ),
        )
    )


async def test_build_hub_joins_shared_across_promoted_graphs() -> None:
    ds = rdflib.Dataset()
    _seed_dataset(ds, "ds-a", [("urn:a1", "Bi₂Te₃"), ("urn:a2", "PbTe")])  # subscripts
    _seed_dataset(ds, "ds-b", [("urn:b1", "Bi2Te3")])  # ascii variant
    client = _DatasetClient(ds)
    cfg = _composition_config([("ds-a", "starrydata"), ("ds-b", "materials_project")])

    out = await build_hub(client, cfg, built_at="2026-06-11T00:00:00+00:00")

    # Bi2Te3 is shared across BOTH (even across the subscript variant); PbTe isn't.
    assert out.shared["composition"] == ["Bi2Te3"]
    assert out.links["composition"] == {"starrydata": 1, "materials_project": 1}
    assert {p["dataset_id"] for p in out.participants_used} == {"ds-a", "ds-b"}
    assert out.participants_skipped == []
    assert out.triple_count > 0

    # The hub graph was written and flagged promoted (so the FROM-merge unions it).
    assert any(g == HUB_GRAPH for g, _ in client.posted)
    promoted = set(await substrate.canonical_graphs(client))
    assert HUB_GRAPH in promoted
    # The join + per-link provenance landed in the store with the ORIGINAL raws.
    n = await _count(client, f"GRAPH <{HUB_GRAPH}> {{ ?s a <{XW}Composition> }}")
    assert n == 1
    links = await _count(client, f"GRAPH <{HUB_GRAPH}> {{ ?s a <{XW}CrosswalkLink> }}")
    assert links == 2
    raws = await _values(
        client,
        f"SELECT ?v WHERE {{ GRAPH <{HUB_GRAPH}> {{ ?l <{XW}sourceValue> ?v }} }}",
    )
    assert set(raws) == {"Bi₂Te₃", "Bi2Te3"}


async def test_build_hub_skips_unpromoted_participant() -> None:
    ds = rdflib.Dataset()
    _seed_dataset(ds, "ds-a", [("urn:a1", "Bi2Te3")])
    # ds-b's data exists but is NOT promoted (no control flag) -> excluded.
    keyb = substrate.canonical_graph_iri("ds-b")
    gb = ds.graph(rdflib.URIRef(keyb))
    gb.add((rdflib.URIRef("urn:b1"), rdflib.URIRef(PRED), rdflib.Literal("Bi2Te3")))
    client = _DatasetClient(ds)
    cfg = _composition_config([("ds-a", "a"), ("ds-b", "b")])

    out = await build_hub(client, cfg, built_at="2026-06-11T00:00:00+00:00")

    assert {p["dataset_id"] for p in out.participants_skipped} == {"ds-b"}
    assert out.shared.get("composition", []) == []  # only one promoted -> nothing shared


async def test_remove_hub_drops_graph_and_flag() -> None:
    ds = rdflib.Dataset()
    _seed_dataset(ds, "ds-a", [("urn:a1", "Bi2Te3")])
    _seed_dataset(ds, "ds-b", [("urn:b1", "Bi2Te3")])
    client = _DatasetClient(ds)
    cfg = _composition_config([("ds-a", "a"), ("ds-b", "b")])
    await build_hub(client, cfg, built_at="2026-06-11T00:00:00+00:00")
    assert HUB_GRAPH in set(await substrate.canonical_graphs(client))

    await remove_hub(client)
    assert HUB_GRAPH not in set(await substrate.canonical_graphs(client))
    assert await _count(client, f"GRAPH <{HUB_GRAPH}> {{ ?s ?p ?o }}") == 0


def test_config_round_trips_through_dict_and_yaml(tmp_path: Path) -> None:
    cfg = _composition_config([("ds-a", "starrydata"), ("ds-b", "materials_project")])
    assert parse_config(config_to_dict(cfg)) == cfg
    # single-concept shorthand (no top-level "concepts") is accepted
    shorthand = {
        "name": "composition",
        "participants": [{"dataset_id": "ds-a", "predicate": PRED}],
        "min_datasets": 2,
    }
    parsed = parse_config(shorthand)
    assert parsed.concepts[0].participants[0].dataset_id == "ds-a"
    assert parsed.concepts[0].participants[0].label == "ds-a"  # defaults to id
    # filesystem round-trip
    save_config(tmp_path, cfg)
    assert load_config(tmp_path) == cfg
    assert load_config(tmp_path / "nonexistent") is None


def test_write_registry_scaffold_seeds_then_preserves(tmp_path: Path) -> None:
    cfg = _composition_config([("ds-a", "starrydata"), ("ds-b", "materials_project")])
    outcome = BuildOutcome(
        built_at="2026-06-11T00:00:00+00:00",
        hub_graph=HUB_GRAPH,
        triple_count=42,
        shared={"composition": ["Bi2Te3", "PbTe"]},
        links={"composition": {"starrydata": 3, "materials_project": 2}},
        participants_used=[{"dataset_id": "ds-a", "label": "starrydata"}],
        participants_skipped=[],
    )
    meta = write_registry_scaffold(tmp_path, cfg, outcome)
    assert meta["is_crosswalk"] is True
    assert meta["crosswalk_participants"] == ["materials_project", "starrydata"]
    assert meta["crosswalk_shared_compositions"] == 2
    assert meta["triple_count"] == 42
    assert meta["promoted"] is True
    d = tmp_path / "crosswalk-bridge"
    assert (d / "meta.json").is_file()
    tools = (d / "query_tools.yaml").read_text(encoding="utf-8")
    assert "datasets_for_composition" in tools

    # A human-authored query_tools.yaml is NOT clobbered on a re-scaffold.
    (d / "query_tools.yaml").write_text("tools:\n  - name: my_custom_tool\n", encoding="utf-8")
    write_registry_scaffold(tmp_path, cfg, outcome)
    assert "my_custom_tool" in (d / "query_tools.yaml").read_text(encoding="utf-8")
    assert "datasets_for_composition" not in (d / "query_tools.yaml").read_text(encoding="utf-8")


def test_perspective_id_scheme() -> None:
    # default = the legacy composition perspective (back-compat ids/graph)
    assert crosswalk_graph_iri() == HUB_GRAPH
    assert crosswalk_registry_id() == "crosswalk-bridge"
    # a named perspective gets its own graph + registry id
    assert crosswalk_graph_iri("crystal").endswith("/graph/canonical/crosswalk/crystal")
    assert crosswalk_registry_id("crystal") == "crosswalk-crystal"
    assert crosswalk_graph_iri("crystal") != HUB_GRAPH


async def test_named_perspective_writes_its_own_graph(tmp_path: Path) -> None:
    ds = rdflib.Dataset()
    _seed_dataset(ds, "ds-a", [("urn:a1", "Bi2Te3")])
    _seed_dataset(ds, "ds-b", [("urn:b1", "Bi2Te3")])
    client = _DatasetClient(ds)
    cfg = _composition_config([("ds-a", "starrydata"), ("ds-b", "materials_project")])

    out = await build_hub(
        client, cfg, built_at="2026-06-11T00:00:00+00:00", perspective_id="crystal"
    )

    g = crosswalk_graph_iri("crystal")
    assert out.hub_graph == g
    assert any(posted == g for posted, _ in client.posted)
    assert HUB_GRAPH not in [posted for posted, _ in client.posted]  # default untouched
    # the named perspective is promoted -> the FROM-merge unions it
    assert g in set(await substrate.canonical_graphs(client))


def test_list_perspectives_finds_each_by_flag(tmp_path: Path) -> None:
    cfg = _composition_config([("ds-a", "a"), ("ds-b", "b")])
    outcome = BuildOutcome(
        built_at="2026-06-11T00:00:00+00:00",
        hub_graph=HUB_GRAPH,
        triple_count=1,
        shared={"composition": ["Bi2Te3"]},
        links={},
        participants_used=[],
        participants_skipped=[],
    )
    write_registry_scaffold(tmp_path, cfg, outcome)  # default (composition)
    write_registry_scaffold(tmp_path, cfg, outcome, perspective_id="crystal", name="結晶構造")

    persp = list_perspectives(tmp_path)
    ids = {m["crosswalk_perspective_id"] for m in persp}
    assert ids == {"composition", "crystal"}
    crystal = next(m for m in persp if m["crosswalk_perspective_id"] == "crystal")
    assert crystal["id"] == "crosswalk-crystal"
    assert crystal["name"] == "結晶構造"
    assert crystal["canonical_graph"] == crosswalk_graph_iri("crystal")


async def test_schema_alignment_assert_list_remove() -> None:
    ds = rdflib.Dataset()
    client = _DatasetClient(ds)
    a = f"{XW}Composition"
    b = f"{XW}Material"
    # assert an equivalentClass between two perspectives' concept classes
    res = await assert_alignment(
        client, a, b, "equivalentClass",
        at="2026-06-11T00:00:00+00:00", from_perspective="composition", to_perspective="material",
    )
    assert res["relation"] == "equivalentClass"
    # the semantic owl triple landed in the alignment graph
    eq = "http://www.w3.org/2002/07/owl#equivalentClass"
    triple = f"GRAPH <{ALIGNMENT_GRAPH}> {{ <{a}> <{eq}> <{b}> }}"
    assert await _count(client, triple) == 1
    # the alignment graph is promoted -> the FROM-merge unions it (citable)
    assert ALIGNMENT_GRAPH in set(await substrate.canonical_graphs(client))

    listed = await list_alignments(client)
    assert len(listed) == 1
    assert listed[0]["source"] == a and listed[0]["target"] == b
    assert listed[0]["from_perspective"] == "composition"

    # re-assert is idempotent (still one management node)
    await assert_alignment(client, a, b, "equivalentClass", at="2026-06-11T01:00:00+00:00")
    assert len(await list_alignments(client)) == 1

    # remove withdraws both the triple and the provenance node
    await remove_alignment(client, a, b, "equivalentClass")
    assert await list_alignments(client) == []
    assert await _count(client, triple) == 0


def test_alignment_rejects_bad_relation_and_iri() -> None:
    import asyncio

    client = _DatasetClient(rdflib.Dataset())
    with pytest.raises(ValueError):  # relation not in the closed set
        asyncio.run(assert_alignment(client, f"{XW}A", f"{XW}B", "sameAs", at="t"))
    with pytest.raises(ValueError):  # not an absolute IRI
        asyncio.run(assert_alignment(client, "not-an-iri", f"{XW}B", "equivalentClass", at="t"))


# --- small SPARQL helpers for assertions -----------------------------------


async def _count(client: _DatasetClient, where: str) -> int:
    rows = (
        await client.sparql_select(f"SELECT (COUNT(*) AS ?c) WHERE {{ {where} }}")
    )["results"]["bindings"]
    return int(rows[0]["c"]["value"]) if rows else 0


async def _values(client: _DatasetClient, query: str) -> list[str]:
    rows = (await client.sparql_select(query))["results"]["bindings"]
    return [b["v"]["value"] for b in rows]
