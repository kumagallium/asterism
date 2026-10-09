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
import yaml

from asterism import substrate
from asterism.crosswalk_runtime import (
    ALIGNMENT_GRAPH,
    DEFAULT_PERSPECTIVE_NAME,
    HUB_GRAPH,
    UNNAMED_PERSPECTIVE_NAME,
    BuildOutcome,
    RuntimeConcept,
    RuntimeCrosswalkConfig,
    RuntimeParticipant,
    assert_alignment,
    build_hub,
    config_to_dict,
    crosswalk_graph_iri,
    crosswalk_registry_id,
    generic_tools_yaml,
    list_alignments,
    list_perspectives,
    load_config,
    parse_config,
    remove_alignment,
    remove_hub,
    save_config,
    write_registry_scaffold,
)
from asterism.query_tools import lint_query_tool, parse_query_tools

XW = "https://kumagallium.github.io/asterism/crosswalk/ontology#"
PRED = "https://kumagallium.github.io/asterism/x/ontology#comp"
PRED2 = "https://kumagallium.github.io/asterism/x/ontology#cryst"


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


def _seed_dataset(
    ds: rdflib.Dataset, dataset_id: str, rows: list[tuple[str, str | rdflib.Literal]]
) -> str:
    """Put ``(entity, raw)`` rows into a dataset's promoted key graph; return its IRI.
    ``raw`` may be a ready ``rdflib.Literal`` (language-tagged / typed)."""
    key = substrate.canonical_graph_iri(dataset_id)
    g = ds.graph(rdflib.URIRef(key))
    for entity, raw in rows:
        lit = raw if isinstance(raw, rdflib.Literal) else rdflib.Literal(raw)
        g.add((rdflib.URIRef(entity), rdflib.URIRef(PRED), lit))
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


def _identity_config(participants: list[tuple[str, str]]) -> RuntimeCrosswalkConfig:
    return RuntimeCrosswalkConfig(
        concepts=(
            RuntimeConcept(
                name="name",
                class_iri=f"{XW}Name",
                link_predicate=f"{XW}hasName",
                normalizer="identity",
                participants=tuple(
                    RuntimeParticipant(dataset_id=dsid, label=label, predicate=PRED)
                    for dsid, label in participants
                ),
            ),
        )
    )


async def _source_values(client: _DatasetClient) -> set[str]:
    return set(
        await _values(
            client,
            f"SELECT ?v WHERE {{ GRAPH <{HUB_GRAPH}> {{ ?l <{XW}sourceValue> ?v }} }}",
        )
    )


async def test_build_hub_reads_a_language_tagged_value_as_the_same_string() -> None:
    """候補さがしは文字列で比べる（"日本"@ja と "日本" は同じ値）。作るときも同じ
    比べ方でなければ、候補が約束した件数と作った件数が食い違う。2 段目の引き直しは
    ストアにある term そのもの（言語タグつき）で問う。同じ主語が ja と en の 2 つの
    値を持っていても、リンクは値 1 つにつき 1 本。"""
    ds = rdflib.Dataset()
    _seed_dataset(ds, "ds-a", [("urn:a1", "日本"), ("urn:a2", "フランス")])
    _seed_dataset(
        ds,
        "ds-b",
        [
            ("urn:b1", rdflib.Literal("日本", lang="ja")),
            ("urn:b1", rdflib.Literal("Japan", lang="en")),
            ("urn:b2", rdflib.Literal("フランス", lang="ja")),
            ("urn:b2", rdflib.Literal("France", lang="en")),
        ],
    )
    client = _DatasetClient(ds)
    cfg = _identity_config([("ds-a", "table"), ("ds-b", "catalog")])

    out = await build_hub(client, cfg, built_at="2026-09-30T00:00:00+00:00")

    assert sorted(out.shared["name"]) == ["フランス", "日本"]
    assert out.links["name"] == {"table": 2, "catalog": 2}
    assert await _source_values(client) == {"日本", "フランス"}


async def test_build_hub_reads_a_typed_value_as_the_same_string() -> None:
    """型つきの値（"42"^^xsd:integer）も、素の "42" と同じ値として一致する。"""
    ds = rdflib.Dataset()
    _seed_dataset(ds, "ds-a", [("urn:a1", "42"), ("urn:a2", "7")])
    _seed_dataset(
        ds,
        "ds-b",
        [
            ("urn:b1", rdflib.Literal("42", datatype=rdflib.XSD.integer)),
            ("urn:b2", rdflib.Literal("8", datatype=rdflib.XSD.integer)),
        ],
    )
    client = _DatasetClient(ds)
    cfg = _identity_config([("ds-a", "table"), ("ds-b", "catalog")])

    out = await build_hub(client, cfg, built_at="2026-09-30T00:00:00+00:00")

    assert out.shared["name"] == ["42"]
    assert out.links["name"] == {"table": 1, "catalog": 1}


async def test_build_hub_counts_the_same_string_under_two_terms_once() -> None:
    """同じ主語が同じ文字列を 2 つの term（言語タグつき・素）で持っていても、
    観測は 1 つ。件数が二重にならない。"""
    ds = rdflib.Dataset()
    _seed_dataset(ds, "ds-a", [("urn:a1", "日本")])
    _seed_dataset(
        ds,
        "ds-b",
        [("urn:b1", rdflib.Literal("日本", lang="ja")), ("urn:b1", rdflib.Literal("日本"))],
    )
    client = _DatasetClient(ds)
    cfg = _identity_config([("ds-a", "table"), ("ds-b", "catalog")])

    out = await build_hub(client, cfg, built_at="2026-09-30T00:00:00+00:00")

    assert out.shared["name"] == ["日本"]
    assert out.links["name"] == {"table": 1, "catalog": 1}


async def test_build_hub_never_joins_on_a_non_literal_value() -> None:
    """候補さがしはリテラルだけを読む。作るときも同じで、IRI の値は文字列が同じでも
    つながない（候補さがしが約束しない一致を、作る側が作らない）。"""
    ds = rdflib.Dataset()
    key_a = substrate.canonical_graph_iri("ds-a")
    ds.graph(rdflib.URIRef(key_a)).add(
        (rdflib.URIRef("urn:a1"), rdflib.URIRef(PRED), rdflib.URIRef("urn:shared"))
    )
    _mark_promoted(ds, key_a)
    _seed_dataset(ds, "ds-b", [("urn:b1", "urn:shared")])
    client = _DatasetClient(ds)
    cfg = _identity_config([("ds-a", "table"), ("ds-b", "catalog")])

    out = await build_hub(client, cfg, built_at="2026-09-30T00:00:00+00:00")

    assert out.shared["name"] == []
    assert out.links["name"] == {}


RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"


async def test_build_hub_concept_labels_overrides_the_hub_classs_rdfs_label() -> None:
    """契約メモ contract_b2_hub_names.md B2-3: ``concept_labels`` は
    :func:`asterism.crosswalk.build_turtle` にそのまま届き、ハブの種類の
    ``rdfs:label`` に反映される。"""
    ds = rdflib.Dataset()
    _seed_dataset(ds, "ds-a", [("urn:a1", "Bi2Te3")])
    _seed_dataset(ds, "ds-b", [("urn:b1", "Bi2Te3")])
    client = _DatasetClient(ds)
    cfg = _composition_config([("ds-a", "starrydata"), ("ds-b", "materials_project")])

    await build_hub(
        client,
        cfg,
        built_at="2026-06-11T00:00:00+00:00",
        concept_labels={"composition": "化学組成"},
    )

    labels = await _values(
        client,
        f"SELECT ?v WHERE {{ GRAPH <{HUB_GRAPH}> {{ <{XW}Composition> <{RDFS_LABEL}> ?v }} }}",
    )
    assert labels == ["化学組成"]


async def test_build_hub_scopes_a_participant_to_its_kind() -> None:
    """Every value catalog stores its value under the same ``rdfs:label``, so a
    participant that names only the predicate joins DOIs and units along with the
    compositions. ``subject_class`` pins the claim to one kind's entities
    (crosswalk-kind-scoped-fields.md); a participant without it keeps the legacy
    any-subject reading."""
    ds = rdflib.Dataset()
    key_a = substrate.canonical_graph_iri("ds-a")
    g = ds.graph(rdflib.URIRef(key_a))
    comp, doi = rdflib.URIRef(f"{PRED}/Composition"), rdflib.URIRef(f"{PRED}/Doi")
    # ds-a: a composition AND a DOI catalog, both labelled with rdfs:label; the DOI
    # value happens to be spelled like a composition in ds-b.
    g.add((rdflib.URIRef("urn:a:comp1"), rdflib.RDF.type, comp))
    g.add((rdflib.URIRef("urn:a:comp1"), rdflib.RDFS.label, rdflib.Literal("Bi2Te3")))
    g.add((rdflib.URIRef("urn:a:doi1"), rdflib.RDF.type, doi))
    g.add((rdflib.URIRef("urn:a:doi1"), rdflib.RDFS.label, rdflib.Literal("PbTe")))
    _mark_promoted(ds, key_a)
    _seed_dataset(ds, "ds-b", [("urn:b1", "Bi2Te3"), ("urn:b2", "PbTe")])
    client = _DatasetClient(ds)

    def cfg(subject_class: str | None) -> RuntimeCrosswalkConfig:
        return RuntimeCrosswalkConfig(
            concepts=(
                RuntimeConcept(
                    name="composition",
                    class_iri=f"{XW}Composition",
                    link_predicate=f"{XW}hasComposition",
                    normalizer="identity",
                    participants=(
                        RuntimeParticipant(
                            dataset_id="ds-a",
                            label="a",
                            predicate=RDFS_LABEL,
                            subject_class=subject_class,
                        ),
                        RuntimeParticipant(dataset_id="ds-b", label="b", predicate=PRED),
                    ),
                ),
            )
        )

    scoped = await build_hub(client, cfg(str(comp)), built_at="2026-09-03T00:00:00+00:00")
    assert scoped.shared["composition"] == ["Bi2Te3"]  # the DOI never entered
    assert scoped.links["composition"] == {"a": 1, "b": 1}
    legacy = await build_hub(client, cfg(None), built_at="2026-09-03T00:00:00+00:00")
    assert legacy.shared["composition"] == ["Bi2Te3", "PbTe"]  # any subject: the DOI too


def test_config_round_trips_subject_class_and_omits_it_when_absent() -> None:
    raw = {
        "concepts": [
            {
                "name": "composition",
                "participants": [
                    {
                        "dataset_id": "ds-a",
                        "predicate": RDFS_LABEL,
                        "subject_class": f"{PRED}/Composition",
                    },
                    {"dataset_id": "ds-b", "predicate": PRED},
                ],
            }
        ]
    }
    cfg = parse_config(raw)
    a, b = cfg.concepts[0].participants
    assert a.subject_class == f"{PRED}/Composition"
    assert b.subject_class is None
    out = config_to_dict(cfg)["concepts"][0]["participants"]
    assert out[0]["subject_class"] == f"{PRED}/Composition"
    assert "subject_class" not in out[1]  # a legacy participant serializes as before


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


def test_config_round_trips_a_normalizer_recipe() -> None:
    data = {
        "min_datasets": 2,
        "concepts": [
            {
                "name": "material",
                "class_iri": "https://x/Material",
                "link_predicate": "https://x/hasMaterial",
                "normalizer": "recipe",
                "normalizer_recipe": ["nfkc", "casefold", "collapse_ws"],
                "participants": [
                    {"dataset_id": "ds-a", "predicate": PRED},
                    {"dataset_id": "ds-b", "predicate": PRED},
                ],
            }
        ],
    }
    cfg = parse_config(data)
    assert cfg.concepts[0].normalizer_recipe == ("nfkc", "casefold", "collapse_ws")
    # round-trips (and a recipe-free concept omits the key entirely)
    assert parse_config(config_to_dict(cfg)) == cfg
    plain = config_to_dict(_composition_config([("ds-a", "starrydata"), ("ds-b", "mp")]))
    assert "normalizer_recipe" not in plain["concepts"][0]


def test_config_rejects_unknown_recipe_primitive() -> None:
    bad = {
        "concepts": [
            {
                "name": "material",
                "normalizer_recipe": ["nfkc", "rm -rf"],
                "participants": [{"dataset_id": "ds-a", "predicate": PRED}],
            }
        ],
    }
    with pytest.raises(ValueError, match="unknown recipe primitive"):
        parse_config(bad)


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
    # An unnamed perspective is named in the reader's words, not "crosswalk hub …".
    assert meta["name"] == DEFAULT_PERSPECTIVE_NAME
    assert "crosswalk" not in meta["name"]
    d = tmp_path / "crosswalk-bridge"
    assert (d / "meta.json").is_file()
    tools = (d / "query_tools.yaml").read_text(encoding="utf-8")
    assert "datasets_for_composition" in tools

    # A human-authored query_tools.yaml is NOT clobbered on a re-scaffold.
    (d / "query_tools.yaml").write_text("tools:\n  - name: my_custom_tool\n", encoding="utf-8")
    write_registry_scaffold(tmp_path, cfg, outcome)
    assert "my_custom_tool" in (d / "query_tools.yaml").read_text(encoding="utf-8")
    assert "datasets_for_composition" not in (d / "query_tools.yaml").read_text(encoding="utf-8")


def _crystal_config() -> RuntimeCrosswalkConfig:
    return RuntimeCrosswalkConfig(
        concepts=(
            RuntimeConcept(
                name="crystal_system",
                class_iri=f"{XW}CrystalSystem",
                link_predicate=f"{XW}hasCrystalSystem",
                normalizer="identity",
                participants=(
                    RuntimeParticipant(dataset_id="ds-a", label="a", predicate=PRED2),
                    RuntimeParticipant(dataset_id="ds-b", label="b", predicate=PRED2),
                ),
            ),
        )
    )


def test_seeded_tool_follows_the_concept_not_composition() -> None:
    # A crosswalk that joins on crystal_system gets a tool that queries
    # xw:CrystalSystem — the old hardcoded composition tool answered 0 rows here.
    text = generic_tools_yaml(_crystal_config())
    assert "datasets_for_crystal_system" in text
    assert "Composition" not in text
    tools = parse_query_tools(yaml.safe_load(text))
    assert [t.name for t in tools] == ["datasets_for_crystal_system"]
    assert [p.name for p in tools[0].params] == ["crystal_system"]
    assert f"<{XW}CrystalSystem>" in tools[0].query
    assert f"<{XW}hasCrystalSystem>" in tools[0].query
    # It is a valid, runnable read-only query (real SPARQL parse via the linter).
    assert lint_query_tool(tools[0]).ok
    # Nothing an implementation-word reader would have to decode.
    assert "crosswalk hub" not in tools[0].title
    assert "normalized" not in tools[0].params[0].description


def test_seeded_tool_keeps_the_composition_tool_name() -> None:
    # `datasets_for_composition` is an Ask/MCP contract — the wording changed, the
    # name did not.
    text = generic_tools_yaml(_composition_config([("ds-a", "a"), ("ds-b", "b")]))
    tools = parse_query_tools(yaml.safe_load(text))
    assert [t.name for t in tools] == ["datasets_for_composition"]


def test_seeded_tools_skip_concepts_with_no_usable_key(tmp_path: Path) -> None:
    # A concept whose name yields no identifier (and one with an unusable IRI) is
    # skipped rather than written out as a broken declaration.
    cfg = RuntimeCrosswalkConfig(
        concepts=(
            RuntimeConcept(
                name="組成",
                class_iri=f"{XW}Composition",
                link_predicate=f"{XW}hasComposition",
                normalizer="identity",
                participants=(RuntimeParticipant(dataset_id="ds-a", label="a", predicate=PRED),),
            ),
        )
    )
    assert generic_tools_yaml(cfg) == ""
    outcome = BuildOutcome(
        built_at="2026-06-11T00:00:00+00:00",
        hub_graph=HUB_GRAPH,
        triple_count=1,
        shared={},
        links={},
        participants_used=[],
        participants_skipped=[],
    )
    write_registry_scaffold(tmp_path, cfg, outcome, perspective_id="jp")
    assert not (tmp_path / "crosswalk-jp" / "query_tools.yaml").exists()


def test_unnamed_named_perspective_gets_a_plain_name(tmp_path: Path) -> None:
    outcome = BuildOutcome(
        built_at="2026-06-11T00:00:00+00:00",
        hub_graph=HUB_GRAPH,
        triple_count=1,
        shared={},
        links={},
        participants_used=[],
        participants_skipped=[],
    )
    meta = write_registry_scaffold(tmp_path, _crystal_config(), outcome, perspective_id="crystal")
    assert meta["name"] == UNNAMED_PERSPECTIVE_NAME
    # A name the user typed still wins.
    meta = write_registry_scaffold(
        tmp_path, _crystal_config(), outcome, perspective_id="crystal2", name="結晶構造でつなぐ"
    )
    assert meta["name"] == "結晶構造でつなぐ"


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
        client,
        a,
        b,
        "equivalentClass",
        at="2026-06-11T00:00:00+00:00",
        from_perspective="composition",
        to_perspective="material",
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


# --- 線の種類・来歴・循環・混在（共有語 ADR §2.1〜§2.2） ----------------------

RDFS_NS = "http://www.w3.org/2000/01/rdf-schema#"
RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
SV_NS = "https://kumagallium.github.io/asterism/vocab/shared#"
STD = "http://purls.helmholtz-metadaten.de/cmso/Material"  # 標準（known_vocabs の名前空間）
QK = "http://qudt.org/vocab/quantitykind/Temperature"
AT = "2026-10-09T00:00:00+00:00"
A_CLS, B_CLS = "https://example.org/a#Rec", "https://example.org/b#Rec"
A_PROP, B_PROP = "https://example.org/a#temp", "https://example.org/b#temp"


def _onto(ds: rdflib.Dataset, dataset_id: str, terms: list[tuple[str, str]]) -> None:
    """Declare ``(iri, "class"|"property")`` in a dataset's ontology graph."""
    g = ds.graph(rdflib.URIRef(substrate.ontology_graph_iri(dataset_id)))
    for iri, kind in terms:
        t = RDFS_NS + "Class" if kind == "class" else RDF_NS + "Property"
        g.add((rdflib.URIRef(iri), rdflib.RDF.type, rdflib.URIRef(t)))


def _shared(ds: rdflib.Dataset, *slugs: str, kind: str = "class") -> list[str]:
    """Mint bare ``sv:`` terms into the shared graph (just the TBox type triple)."""
    g = ds.graph(rdflib.URIRef(substrate.SHARED_VOCAB_GRAPH))
    t = RDFS_NS + "Class" if kind == "class" else RDF_NS + "Property"
    iris = [SV_NS + slug for slug in slugs]
    for iri in iris:
        g.add((rdflib.URIRef(iri), rdflib.RDF.type, rdflib.URIRef(t)))
    return iris


def _world() -> tuple[rdflib.Dataset, _DatasetClient]:
    ds = rdflib.Dataset()
    _onto(ds, "ds-a", [(A_CLS, "class"), (A_PROP, "property")])
    _onto(ds, "ds-b", [(B_CLS, "class"), (B_PROP, "property")])
    return ds, _DatasetClient(ds)


async def _written(client: _DatasetClient) -> int:
    return await _count(client, f"GRAPH <{ALIGNMENT_GRAPH}> {{ ?s ?p ?o }}")


async def test_assert_records_kinds_datasets_and_the_cq() -> None:
    ds, client = _world()
    (sv_cls,) = _shared(ds, "diffraction_point")
    res = await assert_alignment(
        client, A_CLS, sv_cls, "subClassOf", at=AT, cq="回折点はどこにあるか"
    )
    assert (res["source_kind"], res["target_kind"]) == ("dataset", "shared")
    assert (res["source_dataset"], res["target_dataset"]) == ("ds-a", None)
    assert res["cq"] == "回折点はどこにあるか"
    node = res["alignment_iri"]
    rows = (
        await client.sparql_select(
            f"SELECT ?p ?o WHERE {{ GRAPH <{ALIGNMENT_GRAPH}> {{ <{node}> ?p ?o }} }}"
        )
    )["results"]["bindings"]
    rec = {r["p"]["value"].removeprefix(XW): r["o"]["value"] for r in rows}
    assert rec["alignSourceKind"] == "dataset" and rec["alignTargetKind"] == "shared"
    assert rec["alignSourceDataset"] == "ds-a" and rec["answersCq"] == "回折点はどこにあるか"
    assert "alignTargetDataset" not in rec  # dataset 端のときだけ

    # a dataset -> dataset line records BOTH dataset ids; no cq -> no answersCq
    res2 = await assert_alignment(client, A_CLS, B_CLS, "equivalentClass", at=AT)
    assert (res2["source_dataset"], res2["target_dataset"]) == ("ds-a", "ds-b")
    assert res2["cq"] is None
    listed = {a["alignment_iri"]: a for a in await list_alignments(client)}
    assert listed[node]["cq"] == "回折点はどこにあるか"
    assert listed[res2["alignment_iri"]]["cq"] is None
    assert listed[res2["alignment_iri"]]["target_dataset"] == "ds-b"


async def test_unminted_shared_term_is_refused_and_nothing_is_written() -> None:
    ds, client = _world()
    (minted,) = _shared(ds, "minted")
    for src, tgt in [(A_CLS, SV_NS + "ghost"), (SV_NS + "ghost", B_CLS)]:
        with pytest.raises(ValueError, match="unminted shared term"):
            await assert_alignment(client, src, tgt, "equivalentClass", at=AT)
    assert await _written(client) == 0
    # the minted one is fine, and a standard / unknown end never blocks
    await assert_alignment(client, A_CLS, minted, "subClassOf", at=AT)
    await assert_alignment(client, A_CLS, STD, "equivalentClass", at=AT)
    await assert_alignment(client, "https://nowhere.example/x#Y", STD, "equivalentClass", at=AT)


async def test_class_property_mix_is_a_kind_mismatch() -> None:
    ds, client = _world()
    (sv_cls,) = _shared(ds, "a_class")
    (sv_prop,) = _shared(ds, "a_prop", kind="property")
    bad = [
        (A_CLS, B_PROP, "equivalentClass"),  # property end under a class relation
        (A_PROP, B_CLS, "equivalentProperty"),  # class end under a property relation
        (A_PROP, sv_cls, "subPropertyOf"),  # shared class under a property relation
        (A_CLS, sv_prop, "subClassOf"),  # shared property under a class relation
    ]
    for src, tgt, rel in bad:
        with pytest.raises(ValueError, match="kind mismatch"):
            await assert_alignment(client, src, tgt, rel, at=AT)
    assert await _written(client) == 0
    # same-kind lines pass; hasQuantityKind (item -> individual) is the exception
    await assert_alignment(client, A_CLS, sv_cls, "subClassOf", at=AT)
    await assert_alignment(client, A_PROP, sv_prop, "subPropertyOf", at=AT)
    await assert_alignment(client, A_PROP, QK, "hasQuantityKind", at=AT)
    # an end declared nowhere is undecidable -> allowed
    await assert_alignment(client, A_CLS, "https://nowhere.example/x#Y", "subClassOf", at=AT)


async def test_a_term_declared_as_both_is_not_a_mismatch() -> None:
    ds, client = _world()
    _onto(ds, "ds-c", [(A_CLS, "property")])  # A_CLS is also a property somewhere
    await assert_alignment(client, A_CLS, B_CLS, "equivalentClass", at=AT)
    await assert_alignment(client, A_CLS, B_PROP, "equivalentProperty", at=AT)


async def test_cycle_is_refused_through_subsumption_and_equivalence() -> None:
    ds, client = _world()
    top, mid = _shared(ds, "top", "mid")
    await assert_alignment(client, A_CLS, mid, "subClassOf", at=AT)
    await assert_alignment(client, mid, top, "subClassOf", at=AT)
    for src, tgt in [(top, A_CLS), (mid, A_CLS), (top, mid), (A_CLS, A_CLS)]:
        with pytest.raises(ValueError, match="cycle"):
            await assert_alignment(client, src, tgt, "subClassOf", at=AT)
    # ≡ is walked in BOTH directions: B ≡ top (written B -> top) puts B above A_CLS
    await assert_alignment(client, B_CLS, top, "equivalentClass", at=AT)
    with pytest.raises(ValueError, match="cycle"):
        await assert_alignment(client, top, B_CLS, "subClassOf", at=AT)
    # the SAME length-1 re-assertion is idempotent, not a cycle
    await assert_alignment(client, A_CLS, mid, "subClassOf", at=AT)
    # ≡ itself is symmetric: no cycle check, both directions may be written
    await assert_alignment(client, top, B_CLS, "equivalentClass", at=AT)
    # a diamond (two ways up) is not a cycle
    await assert_alignment(client, A_CLS, top, "subClassOf", at=AT)


async def test_cycle_check_for_properties_uses_the_property_path() -> None:
    ds, client = _world()
    (p,) = _shared(ds, "p", kind="property")
    await assert_alignment(client, A_PROP, p, "subPropertyOf", at=AT)
    await assert_alignment(client, B_PROP, A_PROP, "equivalentProperty", at=AT)
    with pytest.raises(ValueError, match="cycle"):
        await assert_alignment(client, p, B_PROP, "subPropertyOf", at=AT)
    # a CLASS path is not followed by a property relation
    (c,) = _shared(ds, "c")
    await assert_alignment(client, A_CLS, c, "subClassOf", at=AT)
    await assert_alignment(client, c, A_CLS, "equivalentClass", at=AT)  # ≡ unchecked


async def test_old_rows_get_kinds_at_read_time_and_are_never_broken() -> None:
    ds, client = _world()
    # a row written before kinds were recorded: no alignSourceKind / answersCq at all
    node = "https://kumagallium.github.io/asterism/crosswalk/resource/alignment/old"
    ds.update(
        f"INSERT DATA {{ GRAPH <{ALIGNMENT_GRAPH}> {{ "
        f"<{node}> a <{XW}Alignment> ; <{XW}alignSource> <{A_CLS}> ; "
        f'<{XW}alignTarget> <{STD}> ; <{XW}alignRelation> "equivalentClass" }} }}'
    )
    (row,) = await list_alignments(client)
    assert (row["source_kind"], row["target_kind"]) == ("dataset", "standard")
    assert row["source_dataset"] == "ds-a" and row["target_dataset"] is None
    assert row["cq"] is None and row["broken"] is False
    # …and even when the end can no longer be classified, an unrecorded row is not broken
    ds.remove_graph(ds.graph(rdflib.URIRef(substrate.ontology_graph_iri("ds-a"))))
    (row,) = await list_alignments(client)
    assert row["source_kind"] == "unknown" and row["broken"] is False


async def test_a_recorded_dataset_or_shared_end_that_vanished_is_broken() -> None:
    ds, client = _world()
    (sv_cls,) = _shared(ds, "gone")
    await assert_alignment(client, A_CLS, sv_cls, "subClassOf", at=AT)
    await assert_alignment(client, B_CLS, STD, "equivalentClass", at=AT)
    await assert_alignment(client, "https://nowhere.example/x#Y", STD, "equivalentClass", at=AT)
    assert [a["broken"] for a in await list_alignments(client)] == [False, False, False]

    # the shared term is un-minted behind the line's back
    ds.remove_graph(ds.graph(rdflib.URIRef(substrate.SHARED_VOCAB_GRAPH)))
    by_source = {a["source"]: a for a in await list_alignments(client)}
    assert by_source[A_CLS]["target_kind"] == "shared_unminted"
    assert by_source[A_CLS]["broken"] is True
    assert by_source[B_CLS]["broken"] is False

    # a dataset's ontology disappears
    ds.remove_graph(ds.graph(rdflib.URIRef(substrate.ontology_graph_iri("ds-b"))))
    by_source = {a["source"]: a for a in await list_alignments(client)}
    assert by_source[B_CLS]["source_kind"] == "unknown" and by_source[B_CLS]["broken"] is True
    # an end that was `unknown` at assertion time stays unbroken
    assert by_source["https://nowhere.example/x#Y"]["broken"] is False


def _perspective_registry(root: Path) -> list[str]:
    cfg = _composition_config([("ds-a", "a"), ("ds-b", "b")])
    outcome = BuildOutcome(
        built_at="2026-06-11T00:00:00+00:00",
        hub_graph=HUB_GRAPH,
        triple_count=1,
        shared={},
        links={},
        participants_used=[],
        participants_skipped=[],
    )
    write_registry_scaffold(root, cfg, outcome)
    save_config(root, cfg)  # 視点の語集合は crosswalk.yaml から読む
    c = cfg.concepts[0]
    return [c.class_iri, c.link_predicate]


async def test_list_alignments_perspective_scope_uses_loaded_terms_only(tmp_path: Path) -> None:
    """With registry_root the filter is "both ends are terms of a loaded perspective",
    whatever the namespace (the screen's alignableIris); the xw: kind test is dropped."""
    _ds, client = _world()
    base = _composition_config([("ds-a", "a")]).concepts[0]
    cfg = RuntimeCrosswalkConfig(
        concepts=(
            RuntimeConcept(
                name=base.name,
                class_iri=A_CLS,  # a dataset-namespace term used as a perspective class
                link_predicate=f"{XW}hasComposition",
                normalizer=base.normalizer,
                participants=base.participants,
            ),
        )
    )
    outcome = BuildOutcome(
        built_at="2026-06-11T00:00:00+00:00",
        hub_graph=HUB_GRAPH,
        triple_count=1,
        shared={},
        links={},
        participants_used=[],
        participants_skipped=[],
    )
    write_registry_scaffold(tmp_path, cfg, outcome)
    save_config(tmp_path, cfg)
    link = f"{XW}hasComposition"
    await assert_alignment(client, A_CLS, link, "equivalentClass", at=AT)
    await assert_alignment(client, A_CLS, B_CLS, "equivalentClass", at=AT)

    async def sel(**kw) -> set[tuple[str, str]]:
        return {(a["source"], a["target"]) for a in await list_alignments(client, **kw)}

    assert await sel(scope="perspective", registry_root=tmp_path) == {(A_CLS, link)}
    # without registry_root the old rule stands: both ends must be xw:
    assert await sel(scope="perspective") == set()


async def test_list_alignments_scope_branches(tmp_path: Path) -> None:
    ds, client = _world()
    (sv_cls,) = _shared(ds, "term")
    loaded_cls, loaded_link = _perspective_registry(tmp_path)
    stray = f"{XW}NotInAnyLoadedPerspective"
    lines = [
        (loaded_cls, loaded_link, "equivalentClass"),  # perspective (loaded) x2
        (loaded_cls, stray, "equivalentClass"),  # xw: but not in a loaded perspective
        (A_CLS, STD, "equivalentClass"),  # dataset -> standard
        (A_CLS, B_CLS, "equivalentClass"),  # dataset <-> dataset
        (A_CLS, sv_cls, "subClassOf"),  # dataset -> shared
        (sv_cls, STD, "equivalentClass"),  # shared -> standard
        ("https://nowhere.example/x#Y", loaded_cls, "subClassOf"),  # unknown -> perspective
    ]
    for src, tgt, rel in lines:
        await assert_alignment(client, src, tgt, rel, at=AT)

    async def sel(**kw) -> set[tuple[str, str]]:
        return {(a["source"], a["target"]) for a in await list_alignments(client, **kw)}

    everything = await sel()
    assert len(everything) == len(lines)  # no scope: all, `unknown` ends included
    assert await sel(scope="perspective") == {(loaded_cls, loaded_link), (loaded_cls, stray)}
    assert await sel(scope="perspective", registry_root=tmp_path) == {(loaded_cls, loaded_link)}
    assert await sel(scope="standard") == {(A_CLS, STD), (sv_cls, STD)}
    assert await sel(scope="shared") == {(A_CLS, sv_cls), (sv_cls, STD)}
    # dataset: either end is a dataset AND the target is not a standard
    assert await sel(scope="dataset") == {(A_CLS, B_CLS), (A_CLS, sv_cls)}
    with pytest.raises(ValueError, match="scope"):
        await list_alignments(client, scope="everything")


def test_list_perspectives_does_not_return_the_shared_vocab_entry(tmp_path: Path) -> None:
    _perspective_registry(tmp_path)
    shared = tmp_path / "vocab-shared"
    shared.mkdir()
    (shared / "meta.json").write_text(
        json.dumps({"id": "vocab-shared", "is_shared_vocab": True, "promoted": True}),
        encoding="utf-8",
    )
    assert [m["id"] for m in list_perspectives(tmp_path)] == ["crosswalk-bridge"]


# --- small SPARQL helpers for assertions -----------------------------------


async def _count(client: _DatasetClient, where: str) -> int:
    rows = (await client.sparql_select(f"SELECT (COUNT(*) AS ?c) WHERE {{ {where} }}"))["results"][
        "bindings"
    ]
    return int(rows[0]["c"]["value"]) if rows else 0


async def _values(client: _DatasetClient, query: str) -> list[str]:
    rows = (await client.sparql_select(query))["results"]["bindings"]
    return [b["v"]["value"] for b in rows]


# --- Compound keys (crosswalk-compound-keys.md, phase 1b) ----------------------


def _seed_phase(ds: rdflib.Dataset, dataset_id: str, rows: list[tuple]) -> None:
    """Seed entities with TWO predicates (composition + crystal). A None crystal seeds
    ONLY composition (a missing part)."""
    key = substrate.canonical_graph_iri(dataset_id)
    g = ds.graph(rdflib.URIRef(key))
    for entity, comp, cryst in rows:
        g.add((rdflib.URIRef(entity), rdflib.URIRef(PRED), rdflib.Literal(comp)))
        if cryst is not None:
            g.add((rdflib.URIRef(entity), rdflib.URIRef(PRED2), rdflib.Literal(cryst)))
    ds.update(
        f"INSERT DATA {{ GRAPH <{substrate.CONTROL_GRAPH_IRI}> {{ "
        f'<{key}> <{substrate.STATUS_PREDICATE}> "promoted" }} }}'
    )


def _phase_config() -> dict:
    return {
        "min_datasets": 2,
        "concepts": [
            {
                "name": "phase",
                "class_iri": f"{XW}Phase",
                "link_predicate": f"{XW}hasPhase",
                "key_parts": [
                    {"name": "composition", "normalizer": "composition"},
                    {"name": "crystal", "normalizer": "identity"},
                ],
                "participants": [
                    {
                        "dataset_id": "ds-a",
                        "label": "sd",
                        "predicates": {"composition": PRED, "crystal": PRED2},
                    },
                    {
                        "dataset_id": "ds-b",
                        "label": "mp",
                        "predicates": {"composition": PRED, "crystal": PRED2},
                    },
                ],
            }
        ],
    }


async def test_build_hub_compound_key_joins_on_the_tuple() -> None:
    ds = rdflib.Dataset()
    # ds-a: (PbTe, rocksalt) + (PbTe, highP) + (SnSe, only composition / missing crystal)
    _seed_phase(
        ds,
        "ds-a",
        [("u:a1", "PbTe", "rocksalt"), ("u:a2", "PbTe", "highP"), ("u:a3", "SnSe", None)],
    )
    _seed_phase(ds, "ds-b", [("u:b1", "Pb Te", "rocksalt"), ("u:b2", "SnSe", "Pnma")])
    client = _DatasetClient(ds)
    out = await build_hub(
        client, parse_config(_phase_config()), built_at="2026-06-14T00:00:00+00:00"
    )

    # Only (PbTe, rocksalt) is shared by BOTH (composition folds the space in "Pb Te").
    # (PbTe, highP) is sd-only; SnSe in ds-a has no crystal part -> excluded (no half-join).
    assert out.shared["phase"] == ["PbTe | rocksalt"]
    assert out.links["phase"] == {"sd": 1, "mp": 1}
    n = await _count(client, f"GRAPH <{HUB_GRAPH}> {{ ?s a <{XW}Phase> }}")
    assert n == 1  # exactly one compound entity minted
    rdfs = "http://www.w3.org/2000/01/rdf-schema#label"
    labels = await _values(
        client, f"SELECT ?v WHERE {{ GRAPH <{HUB_GRAPH}> {{ ?s a <{XW}Phase> ; <{rdfs}> ?v }} }}"
    )
    assert labels == ["PbTe | rocksalt"]  # readable tuple label


async def test_build_hub_compound_key_counts_the_same_strings_under_two_terms_once() -> None:
    """複合キーでも、同じ主語が同じ文字列を 2 つの term（素と言語タグつき）で持つとき、
    タプルは 1 つ（リンクが二重にならない）。単一キーの経路と同じ規則。"""
    ds = rdflib.Dataset()
    _seed_phase(ds, "ds-a", [("u:a1", "PbTe", "rocksalt")])
    _seed_phase(ds, "ds-b", [("u:b1", "PbTe", "rocksalt")])
    ds.graph(rdflib.URIRef(substrate.canonical_graph_iri("ds-b"))).add(
        (rdflib.URIRef("u:b1"), rdflib.URIRef(PRED), rdflib.Literal("PbTe", lang="en"))
    )
    client = _DatasetClient(ds)
    out = await build_hub(
        client, parse_config(_phase_config()), built_at="2026-09-30T00:00:00+00:00"
    )

    assert out.shared["phase"] == ["PbTe | rocksalt"]
    assert out.links["phase"] == {"sd": 1, "mp": 1}


def test_compound_config_round_trips_and_validates() -> None:
    cfg = parse_config(_phase_config())
    c = cfg.concepts[0]
    assert [kp.name for kp in c.key_parts] == ["composition", "crystal"]
    assert c.participants[0].predicates == (PRED, PRED2)
    assert parse_config(config_to_dict(cfg)) == cfg  # round-trips
    # a compound participant missing a part's predicate is rejected
    bad = _phase_config()
    del bad["concepts"][0]["participants"][0]["predicates"]["crystal"]
    with pytest.raises(ValueError, match="needs a predicate for every key part"):
        parse_config(bad)
