"""Tests for asterism.class_schema (object-cards-ui.md §2 / 契約メモ §2).

Backed by a real ``pyoxigraph.Store`` — same contract as ``_pyoxi_client`` in
test_query_tools.py / test_prov_graph.py, extended to also carry a literal's
``datatype`` in each SPARQL-JSON binding (real Oxigraph does this; the other
files' mocks didn't need it, this module's live-store fallback does). Fixture
data spans two unrelated fictional domains (a lending-library catalogue and a
"ghost sighting" log with no registry design at all) so no test asserts on a
single dataset's shape or a domain-specific noun.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from asterism.class_schema import class_schema
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
    ``CANONICAL_GRAPH_BASE`` is flagged ``promoted`` in the control graph, same
    as every other ingest test's store fixture. Bindings additionally carry a
    literal's ``datatype`` (and language, when set) — the real Oxigraph HTTP
    endpoint does this; ``asterism.class_schema``'s ontology-fallback path
    reads it to tell a link from a quantity from a live sample value.
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
ITEM_CLASS = EX_LIB + "Item"
IDENTIFIER_PRED = "http://purl.org/dc/terms/identifier"
TITLE_PRED = EX_LIB + "title"
OWNER_PRED = EX_LIB + "owner"
WEIGHT_PRED = EX_LIB + "weight"
CATEGORY_PRED = EX_LIB + "category"
SECTION_PRED = EX_LIB + "section"

LIBRARY_DATASET = "library-aaaaaaaa"
LIBRARY_V2_DATASET = "library-bbbbbbbb"  # re-declares ex:Item, newer promoted_at

_LIBRARY_MAPPING_YAML = """
version: 1
prefixes:
  ex: "https://ex/library#"
  exr: "https://ex/library/resource/"
  dcterms: "http://purl.org/dc/terms/"
  xsd: "http://www.w3.org/2001/XMLSchema#"
maps:
  - name: item
    source: items.csv
    subject:
      template: "exr:item/{code}"
      classes: [ex:Item]
    properties:
      - predicate: dcterms:identifier
        column: code
        label: "登録番号"
      - predicate: ex:title
        column: title
      - predicate: ex:owner
        object_template: "exr:person/{owner_id}"
      - predicate: ex:weight
        column: weight_kg
        datatype: xsd:double
        unit: "kg"
      - predicate: ex:category
        column: category
      - predicate: ex:section
        column: section_code
"""

_LIBRARY_MODEL_YAML = """
classes:
  ex:Item: {}
  ex:Section: {}
properties:
  ex:section:
    domain: ex:Item
    range: ex:Section
"""

_LIBRARY_DISPLAY_META = {
    "edits": [
        {
            "predicate": IDENTIFIER_PRED,
            "column": "code",
            "label": "コード",
        }
    ]
}

_LIBRARY_TOOLS_YAML = """
tools:
  - name: heaviest_item
    title: "最重量"
    output_kind: quantity
    query: >-
      SELECT ?w WHERE { ?s a <https://ex/library#Item> ;
      <https://ex/library#weight> ?w } LIMIT 1
    result:
      item:
        weight: {var: w, number: true, role: value}
"""

_LIBRARY_V2_MAPPING_YAML = """
version: 1
prefixes:
  ex: "https://ex/library#"
  exr: "https://ex/library/resource/"
maps:
  - name: item_v2
    source: items_v2.csv
    subject:
      template: "exr:item/{code}"
      classes: [ex:Item]
    properties:
      - predicate: ex:reshelvedCount
        column: reshelved
        datatype: xsd:integer
"""


def _distinct_titles_ttl(n: int, subject_prefix: str, predicate: str) -> str:
    lines = []
    for i in range(n):
        subj = f"<https://ex/library/resource/item/{i}>"
        lines.append(f'{subj} a <{ITEM_CLASS}> ; <{predicate}> "{subject_prefix}-{i}" .')
    return "\n".join(lines)


_LIBRARY_TTL = (
    "\n".join(
        [
            f'<https://ex/library/resource/item/code-1> a <{ITEM_CLASS}> ;'
            f' <{TITLE_PRED}> "Alpha" .',
            f'<https://ex/library/resource/item/code-2> a <{ITEM_CLASS}> ;'
            f' <{TITLE_PRED}> "Beta" .',
            f'<https://ex/library/resource/item/code-3> a <{ITEM_CLASS}> ;'
            f' <{TITLE_PRED}> "Gamma" .',
        ]
    )
    + "\n"
    + _distinct_titles_ttl(51, "cat", CATEGORY_PRED)
)

EX_GHOST = "https://ex/ghost#"
GHOST_CLASS = EX_GHOST + "Ghost"
HAUNTED_BY_PRED = EX_GHOST + "hauntedBy"
SIGHTING_COUNT_PRED = EX_GHOST + "sightingCount"
GHOST_DATASET = "ghostset-cccccccc"  # registry entry with NO mapping.yaml at all

_GHOST_ONTOLOGY_TTL = f"""
@prefix ex: <{EX_GHOST}> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .

ex:Ghost a rdfs:Class ; rdfs:label "亡霊" .
ex:hauntedBy a rdf:Property ; rdfs:domain ex:Ghost ; rdfs:label "取り憑かれた" .
ex:sightingCount a rdf:Property ; rdfs:domain ex:Ghost .
"""

_GHOST_CANONICAL_TTL = f"""
@prefix ex: <{EX_GHOST}> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

<https://ex/ghost/resource/ghost-1> a <{GHOST_CLASS}> ;
    ex:hauntedBy <https://ex/ghost/resource/house-1> ;
    ex:sightingCount "3"^^xsd:integer .
"""

_GHOST_TOOLS_YAML = """
tools:
  - name: recent_sightings
    title: "最近の目撃"
    query: "SELECT ?s WHERE { ?s a <https://ex/ghost#Ghost> }"
"""


def _write_dataset(
    registry_root: Path,
    dataset_id: str,
    *,
    promoted_at: str,
    version: int = 1,
    mapping_yaml: str | None = None,
    model_yaml: str | None = None,
    display_meta: dict | None = None,
    tools_yaml: str | None = None,
) -> None:
    dest = registry_root / dataset_id
    dest.mkdir(parents=True)
    meta = {
        "id": dataset_id,
        "promoted": True,
        "promoted_at": promoted_at,
        "version": version,
    }
    (dest / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    if mapping_yaml is not None:
        (dest / "mapping.yaml").write_text(mapping_yaml, encoding="utf-8")
    if model_yaml is not None:
        (dest / "model.yaml").write_text(model_yaml, encoding="utf-8")
    if display_meta is not None:
        (dest / "display-meta.json").write_text(json.dumps(display_meta), encoding="utf-8")
    if tools_yaml is not None:
        (dest / "query_tools.yaml").write_text(tools_yaml, encoding="utf-8")


@pytest.fixture()
def registry_root(tmp_path: Path) -> Path:
    root = tmp_path / "registry"
    root.mkdir()
    _write_dataset(
        root,
        LIBRARY_DATASET,
        promoted_at="2026-01-01T00:00:00Z",
        version=2,
        mapping_yaml=_LIBRARY_MAPPING_YAML,
        model_yaml=_LIBRARY_MODEL_YAML,
        display_meta=_LIBRARY_DISPLAY_META,
        tools_yaml=_LIBRARY_TOOLS_YAML,
    )
    # The "ghost" dataset carries only a query_tools.yaml — no mapping.yaml, so
    # it can never be matched by _find_owning_dataset; it exists to prove the
    # ontology-fallback branch still finds a dataset's tools by id.
    dest = root / GHOST_DATASET
    dest.mkdir()
    (dest / "query_tools.yaml").write_text(_GHOST_TOOLS_YAML, encoding="utf-8")
    return root


@pytest.fixture()
def client():
    return _pyoxi_client(
        {
            canonical_graph_iri(LIBRARY_DATASET): _LIBRARY_TTL,
            ONTOLOGY_GRAPH_BASE + GHOST_DATASET: _GHOST_ONTOLOGY_TTL,
            canonical_graph_iri(GHOST_DATASET): _GHOST_CANONICAL_TTL,
        }
    )


# ---------------------------------------------------------------------------
# ① mapping.yaml から見つかる主経路
# ---------------------------------------------------------------------------


async def test_unknown_class_iri_returns_none(client, registry_root: Path) -> None:
    out = await class_schema(client, registry_root, "https://ex/nowhere#Nothing")
    assert out is None


async def test_unsafe_class_iri_returns_none_without_querying(registry_root: Path) -> None:
    class _Boom:
        async def sparql_select(self, query: str) -> dict:  # pragma: no cover
            raise AssertionError("must not query the store for an unsafe class_iri")

    assert await class_schema(_Boom(), registry_root, "not-an-iri") is None
    assert await class_schema(_Boom(), registry_root, "ftp://ex/Item") is None


async def test_class_schema_picks_up_dataset_and_label(client, registry_root: Path) -> None:
    out = await class_schema(client, registry_root, ITEM_CLASS)
    assert out is not None
    assert out["class_iri"] == ITEM_CLASS
    assert out["dataset_id"] == LIBRARY_DATASET
    assert out["snapshot"] == "v2"
    # Mapping IR class-token local name (no display-meta class-level edit).
    assert out["label"] == "Item"


async def test_identifier_kind_from_subject_template_column(client, registry_root: Path) -> None:
    out = await class_schema(client, registry_root, ITEM_CLASS)
    props = {p["iri"]: p for p in out["properties"]}
    ident = props[IDENTIFIER_PRED]
    assert ident["kind"] == "identifier"
    assert ident["column"] == "code"
    # display-meta.json's label wins over the Mapping IR's authored label.
    assert ident["label"] == "コード"


async def test_link_kind_from_object_template(client, registry_root: Path) -> None:
    out = await class_schema(client, registry_root, ITEM_CLASS)
    props = {p["iri"]: p for p in out["properties"]}
    owner = props[OWNER_PRED]
    assert owner["kind"] == "link"
    assert owner["column"] is None


async def test_link_kind_from_model_yaml_range(client, registry_root: Path) -> None:
    out = await class_schema(client, registry_root, ITEM_CLASS)
    props = {p["iri"]: p for p in out["properties"]}
    section = props[SECTION_PRED]
    # ``ex:section`` is a bare column reference in the Mapping IR (no
    # object_template) — only model.yaml's declared range makes it a link.
    assert section["kind"] == "link"


async def test_quantity_kind_from_numeric_datatype(client, registry_root: Path) -> None:
    out = await class_schema(client, registry_root, ITEM_CLASS)
    props = {p["iri"]: p for p in out["properties"]}
    weight = props[WEIGHT_PRED]
    assert weight["kind"] == "quantity"
    assert weight["unit"] == "kg"
    assert weight["distinct_count"] is None


async def test_category_kind_at_or_under_the_50_threshold(client, registry_root: Path) -> None:
    out = await class_schema(client, registry_root, ITEM_CLASS)
    props = {p["iri"]: p for p in out["properties"]}
    title = props[TITLE_PRED]
    assert title["kind"] == "category"
    assert title["distinct_count"] == 3


async def test_text_kind_over_the_50_threshold(client, registry_root: Path) -> None:
    out = await class_schema(client, registry_root, ITEM_CLASS)
    props = {p["iri"]: p for p in out["properties"]}
    category = props[CATEGORY_PRED]
    assert category["kind"] == "text"
    assert category["distinct_count"] == 51


async def test_tools_are_annotated_with_output_kind(client, registry_root: Path) -> None:
    out = await class_schema(client, registry_root, ITEM_CLASS)
    tools = {t["name"]: t for t in out["tools"]}
    assert tools["heaviest_item"]["output_kind"] == "quantity"
    assert tools["heaviest_item"]["output_kind_inferred"] is False


async def test_multiple_datasets_same_class_newest_promoted_at_wins(
    client, registry_root: Path
) -> None:
    # A second, independently-promoted dataset re-declares ex:Item with a
    # disjoint property set and a NEWER promoted_at than the fixture's
    # LIBRARY_DATASET — it alone must win, not a merge of both.
    _write_dataset(
        registry_root,
        LIBRARY_V2_DATASET,
        promoted_at="2026-06-01T00:00:00Z",
        version=1,
        mapping_yaml=_LIBRARY_V2_MAPPING_YAML,
    )
    out = await class_schema(client, registry_root, ITEM_CLASS)
    assert out["dataset_id"] == LIBRARY_V2_DATASET
    iris = {p["iri"] for p in out["properties"]}
    assert iris == {EX_LIB + "reshelvedCount"}


async def test_class_schema_is_deterministic_across_repeated_calls(
    client, registry_root: Path
) -> None:
    first = await class_schema(client, registry_root, ITEM_CLASS)
    second = await class_schema(client, registry_root, ITEM_CLASS)
    assert first == second


# ---------------------------------------------------------------------------
# ④ どの registry データセットもこのクラスを宣言していないとき: ontology
#    named graph だけを読むフォールバック。
# ---------------------------------------------------------------------------


async def test_ontology_fallback_when_no_dataset_declares_the_class(
    client, registry_root: Path
) -> None:
    out = await class_schema(client, registry_root, GHOST_CLASS)
    assert out is not None
    assert out["dataset_id"] == GHOST_DATASET
    assert out["snapshot"] is None
    assert out["label"] == "亡霊"

    props = {p["iri"]: p for p in out["properties"]}
    assert props[HAUNTED_BY_PRED]["kind"] == "link"
    assert props[HAUNTED_BY_PRED]["label"] == "取り憑かれた"
    assert props[SIGHTING_COUNT_PRED]["kind"] == "quantity"
    # No authored label on this predicate — falls back to a humanized (but
    # not titlecased) local name.
    assert props[SIGHTING_COUNT_PRED]["label"] == "sighting Count"
    # No Mapping IR / model.yaml in this branch: column/datatype stay null.
    assert props[SIGHTING_COUNT_PRED]["column"] is None
    assert props[SIGHTING_COUNT_PRED]["datatype"] is None


async def test_ontology_fallback_still_loads_the_dataset_tools(
    client, registry_root: Path
) -> None:
    out = await class_schema(client, registry_root, GHOST_CLASS)
    names = {t["name"] for t in out["tools"]}
    assert names == {"recent_sightings"}


async def test_ontology_fallback_without_registry_root(client) -> None:
    out = await class_schema(client, None, GHOST_CLASS)
    assert out is not None
    assert out["dataset_id"] == GHOST_DATASET
    assert out["tools"] == []  # no registry_root to load them from


# ---------------------------------------------------------------------------
# class_iri IRI safety — a caller-supplied class_iri that breaks the SPARQL
# IRIREF grammar must yield None (→ 404 at the api boundary), never a raw
# store-syntax error (checker finding: the old check only denied `<`/`>`/
# `"`/space, letting `{`/`}` through to a query built with a raw f-string).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_iri",
    ['https://ex/x" ?bad ?y ?z . }', "http://x/y}"],
)
async def test_class_schema_returns_none_for_unsafe_class_iri(
    client, registry_root: Path, bad_iri: str
) -> None:
    assert await class_schema(client, registry_root, bad_iri) is None


# ---------------------------------------------------------------------------
# クラスのラベル（契約メモ §3）: registry の model.yaml
# （classes.<curie>.label）→ ontology named graph の rdfs:label → ローカル名の
# 人間化。3 個目の架空分野（青空市の出店記録）— 既存フィクスチャとは独立。
# ---------------------------------------------------------------------------

from asterism.class_schema import class_label  # noqa: E402 (test-local grouping)

EX_MARKET = "https://ex/market#"
STALL_CLASS = EX_MARKET + "Stall"
UNKNOWN_MARKET_CLASS = EX_MARKET + "Unknown"
MARKET_DATASET = "market-fair"

_MARKET_MAPPING_YAML = """
version: 1
prefixes:
  market: "https://ex/market#"
  marketr: "https://ex/market/resource/"
maps:
  - name: stall
    source: stalls.csv
    subject:
      template: "marketr:stall/{code}"
      classes: [market:Stall]
    properties: []
"""

_MARKET_MODEL_YAML = """
classes:
  market:Stall:
    label: "屋台"
"""

# 意図的に model.yaml と違うラベル — model.yaml が勝つことを検証する。
_MARKET_ONTOLOGY_TTL = f"""
@prefix market: <{EX_MARKET}> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

market:Stall a rdfs:Class ; rdfs:label "露店" .
"""


@pytest.fixture()
def market_registry_root(tmp_path: Path) -> Path:
    root = tmp_path / "registry"
    root.mkdir()
    _write_dataset(
        root,
        MARKET_DATASET,
        promoted_at="2026-01-01T00:00:00Z",
        mapping_yaml=_MARKET_MAPPING_YAML,
        model_yaml=_MARKET_MODEL_YAML,
    )
    return root


@pytest.fixture()
def market_client():
    return _pyoxi_client({ONTOLOGY_GRAPH_BASE + MARKET_DATASET: _MARKET_ONTOLOGY_TTL})


async def test_class_label_prefers_model_yaml_over_ontology(
    market_client, market_registry_root: Path
) -> None:
    assert await class_label(market_client, market_registry_root, STALL_CLASS) == "屋台"


async def test_class_label_falls_back_to_ontology_when_no_model_yaml_label(
    market_client, market_registry_root: Path
) -> None:
    # ex:Ghost の rdfs:label は model.yaml が無いクラスにも効く（既存フィクスチャ
    # を流用 — test_ontology_fallback_when_no_dataset_declares_the_class と同じ
    # 経路を class_label() 単体で確認する）。
    onto_only_client = _pyoxi_client(
        {ONTOLOGY_GRAPH_BASE + GHOST_DATASET: _GHOST_ONTOLOGY_TTL}
    )
    assert await class_label(onto_only_client, market_registry_root, GHOST_CLASS) == "亡霊"


async def test_class_label_humanizes_local_name_when_neither_source_has_it(
    market_client, market_registry_root: Path
) -> None:
    assert await class_label(market_client, market_registry_root, UNKNOWN_MARKET_CLASS) == "Unknown"


async def test_class_schema_label_field_uses_the_shared_class_label(
    market_client, market_registry_root: Path
) -> None:
    out = await class_schema(market_client, market_registry_root, STALL_CLASS)
    assert out is not None
    assert out["label"] == "屋台"


# ---------------------------------------------------------------------------
# kind はストアの実データで決める（契約メモ §5(a)）＋ unit placeholder 正規化
# （§5(b)）＋ properties は store ∪ IR の和集合・並びは IR 順→残り IRI 辞書順
# （§5(c)）。4 個目の架空分野（配送便の小包記録）。
# ---------------------------------------------------------------------------

EX_DEPOT2 = "https://ex/depot2#"
PARCEL_CLASS = EX_DEPOT2 + "Parcel"
IDENTIFIER_PRED2 = "http://purl.org/dc/terms/identifier"
CONTACT_PRED = EX_DEPOT2 + "contact"
WEIGHT_PRED2 = EX_DEPOT2 + "weight"
SCORE_PRED = EX_DEPOT2 + "score"  # store-only: not in the Mapping IR at all
EXTRA_PRED = EX_DEPOT2 + "extra"  # store-only: not in the Mapping IR at all

DEPOT2_DATASET = "depot2-shipping"

_DEPOT2_MAPPING_YAML = """
version: 1
prefixes:
  depot: "https://ex/depot2#"
  depotr: "https://ex/depot2/resource/"
  dcterms: "http://purl.org/dc/terms/"
  xsd: "http://www.w3.org/2001/XMLSchema#"
maps:
  - name: parcel
    source: parcels.csv
    subject:
      template: "depotr:parcel/{code}"
      classes: [depot:Parcel]
    properties:
      - predicate: dcterms:identifier
        column: code
      - predicate: depot:contact
        column: contact
      - predicate: depot:weight
        column: weight_kg
        datatype: xsd:double
        unit: "N/A"
"""

# depot:contact is a majority-IRI predicate in the *store* even though the
# Mapping IR row above is a bare column (no object_template) — the store must
# win. depot:score/depot:extra are not in the Mapping IR at all (union case).
_DEPOT2_TTL = f"""
@prefix depot: <{EX_DEPOT2}> .
@prefix dcterms: <http://purl.org/dc/terms/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

<https://ex/depot2/resource/parcel/code-1> a <{PARCEL_CLASS}> ;
    dcterms:identifier "code-1" ;
    depot:contact <https://ex/depot2/resource/agent-1> ;
    depot:weight "1.5"^^xsd:double ;
    depot:score "10"^^xsd:integer ;
    depot:extra "x" .

<https://ex/depot2/resource/parcel/code-2> a <{PARCEL_CLASS}> ;
    dcterms:identifier "code-2" ;
    depot:contact <https://ex/depot2/resource/agent-2> ;
    depot:weight "2.0"^^xsd:double ;
    depot:score "20"^^xsd:integer ;
    depot:extra "y" .

<https://ex/depot2/resource/parcel/code-3> a <{PARCEL_CLASS}> ;
    dcterms:identifier "code-3" ;
    depot:contact "unknown" ;
    depot:weight "3.0"^^xsd:double ;
    depot:score "30"^^xsd:integer ;
    depot:extra "x" .
"""

_DEPOT2_ONTOLOGY_TTL = f"""
@prefix depot: <{EX_DEPOT2}> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

depot:score rdfs:label "得点" .
"""


@pytest.fixture()
def depot2_registry_root(tmp_path: Path) -> Path:
    root = tmp_path / "registry"
    root.mkdir()
    _write_dataset(
        root,
        DEPOT2_DATASET,
        promoted_at="2026-01-01T00:00:00Z",
        mapping_yaml=_DEPOT2_MAPPING_YAML,
    )
    return root


@pytest.fixture()
def depot2_client():
    return _pyoxi_client(
        {
            canonical_graph_iri(DEPOT2_DATASET): _DEPOT2_TTL,
            ONTOLOGY_GRAPH_BASE + DEPOT2_DATASET: _DEPOT2_ONTOLOGY_TTL,
        }
    )


async def test_kind_identifier_wins_regardless_of_store_data(
    depot2_client, depot2_registry_root: Path
) -> None:
    out = await class_schema(depot2_client, depot2_registry_root, PARCEL_CLASS)
    props = {p["iri"]: p for p in out["properties"]}
    assert props[IDENTIFIER_PRED2]["kind"] == "identifier"


async def test_kind_link_from_store_majority_overrides_ir_bare_column(
    depot2_client, depot2_registry_root: Path
) -> None:
    out = await class_schema(depot2_client, depot2_registry_root, PARCEL_CLASS)
    props = {p["iri"]: p for p in out["properties"]}
    assert props[CONTACT_PRED]["kind"] == "link"


async def test_unit_placeholder_normalizes_to_null(
    depot2_client, depot2_registry_root: Path
) -> None:
    out = await class_schema(depot2_client, depot2_registry_root, PARCEL_CLASS)
    props = {p["iri"]: p for p in out["properties"]}
    weight = props[WEIGHT_PRED2]
    assert weight["kind"] == "quantity"
    assert weight["unit"] is None


async def test_store_only_predicate_is_added_with_kind_from_stats_and_ontology_label(
    depot2_client, depot2_registry_root: Path
) -> None:
    out = await class_schema(depot2_client, depot2_registry_root, PARCEL_CLASS)
    props = {p["iri"]: p for p in out["properties"]}
    assert SCORE_PRED in props  # not in the Mapping IR — union with the store
    assert props[SCORE_PRED]["kind"] == "quantity"
    assert props[SCORE_PRED]["label"] == "得点"
    assert props[SCORE_PRED]["column"] is None


async def test_store_only_predicate_without_ontology_label_is_humanized(
    depot2_client, depot2_registry_root: Path
) -> None:
    out = await class_schema(depot2_client, depot2_registry_root, PARCEL_CLASS)
    props = {p["iri"]: p for p in out["properties"]}
    assert props[EXTRA_PRED]["kind"] == "category"
    assert props[EXTRA_PRED]["label"] == "extra"


async def test_properties_order_is_ir_order_then_extras_sorted_by_iri(
    depot2_client, depot2_registry_root: Path
) -> None:
    out = await class_schema(depot2_client, depot2_registry_root, PARCEL_CLASS)
    order = [p["iri"] for p in out["properties"]]
    ir_prefix = [IDENTIFIER_PRED2, CONTACT_PRED, WEIGHT_PRED2]
    assert order[: len(ir_prefix)] == ir_prefix
    assert order[len(ir_prefix) :] == sorted([SCORE_PRED, EXTRA_PRED])


async def test_class_schema_with_store_data_is_deterministic(
    depot2_client, depot2_registry_root: Path
) -> None:
    first = await class_schema(depot2_client, depot2_registry_root, PARCEL_CLASS)
    second = await class_schema(depot2_client, depot2_registry_root, PARCEL_CLASS)
    assert first == second


# ---------------------------------------------------------------------------
# SPARQL インジェクション — predicate（mapping.yaml 由来）・?p/?g（store 由来）
# を無検証に ``f"<{...}>"`` へ埋めると IRIREF が早期に閉じ任意の節を注入できた
# （checker が実際に再現した欠陥）。5 個目の架空分野（古書の貸出記録）。
# ---------------------------------------------------------------------------

EX_ARCHIVE = "https://ex/archive#"
FOLIO_CLASS = EX_ARCHIVE + "Folio"
SAFE_PRED = EX_ARCHIVE + "borrower"
_INJECTED_PRED = (
    "http://evil.example/p> } SELECT * WHERE { ?s ?p ?o . } #"
)  # closes the IRIREF early with `>` then splices a second clause via `}` `{`

ARCHIVE_DATASET = "archive-folios"

_ARCHIVE_MAPPING_YAML = f"""
version: 1
prefixes:
  archive: "https://ex/archive#"
  archiver: "https://ex/archive/resource/"
maps:
  - name: folio
    source: folios.csv
    subject:
      template: "archiver:folio/{{code}}"
      classes: [archive:Folio]
    properties:
      - predicate: archive:borrower
        column: borrower
      - predicate: "{_INJECTED_PRED}"
        column: suspicious
"""

_ARCHIVE_TTL = f"""
@prefix archive: <{EX_ARCHIVE}> .

<https://ex/archive/resource/folio/code-1> a <{FOLIO_CLASS}> ;
    archive:borrower "someone" .
"""


@pytest.fixture()
def archive_registry_root(tmp_path: Path) -> Path:
    root = tmp_path / "registry"
    root.mkdir()
    _write_dataset(
        root,
        ARCHIVE_DATASET,
        promoted_at="2026-01-01T00:00:00Z",
        mapping_yaml=_ARCHIVE_MAPPING_YAML,
    )
    return root


class _CapturingClient:
    """Wraps a real ``SupportsSparql`` and records every query text sent to
    it, so a test can assert an unsafe value was never spliced in literally."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.queries: list[str] = []

    async def sparql_select(self, query: str) -> dict:
        self.queries.append(query)
        return await self._inner.sparql_select(query)


async def test_injected_mapping_predicate_is_dropped_not_embedded(
    archive_registry_root: Path,
) -> None:
    real_client = _pyoxi_client({canonical_graph_iri(ARCHIVE_DATASET): _ARCHIVE_TTL})
    client = _CapturingClient(real_client)

    out = await class_schema(client, archive_registry_root, FOLIO_CLASS)

    assert out is not None
    # The row with the unsafe predicate is dropped entirely — never raises,
    # never shows up in the returned properties.
    ious = {p["iri"] for p in out["properties"]}
    assert _INJECTED_PRED not in ious
    assert SAFE_PRED in ious
    # No query this module built ever contains the raw injected text —
    # _build_property must return before it is ever embedded.
    for q in client.queries:
        assert "evil.example" not in q
        assert "SELECT * WHERE { ?s ?p ?o" not in q


class _MaliciousStoreClient:
    """A fake ``SupportsSparql`` whose *store-returned* ``?g``/``?p`` bindings
    are themselves SPARQL-injection payloads — simulating a store that (via a
    bug, or a bulk-load path that bypasses Turtle's IRIREF grammar) holds a
    value :func:`asterism.subjects.safe_http_iri` would reject. Every query
    after the first must never contain the payload literally; the module must
    treat the class as absent rather than splice it in."""

    _MALICIOUS_GRAPH = ONTOLOGY_GRAPH_BASE + 'evil"> } SELECT * WHERE { ?s ?p ?o . } #'

    def __init__(self) -> None:
        self.queries: list[str] = []

    async def sparql_select(self, query: str) -> dict:
        self.queries.append(query)
        if "?g ?label WHERE" in query:
            return {
                "results": {
                    "bindings": [{"g": {"type": "uri", "value": self._MALICIOUS_GRAPH}}]
                }
            }
        return {"results": {"bindings": []}}


async def test_malicious_store_graph_iri_is_never_embedded() -> None:
    client = _MaliciousStoreClient()

    out = await class_schema(client, None, FOLIO_CLASS)

    # graph_ref fails safe_http_iri → the module treats the class as not
    # found (safe default) instead of embedding the payload in a second query.
    assert out is None
    for q in client.queries:
        assert "evil" not in q


class _MaliciousPredicateClient:
    """Same idea, but the payload comes back as a property's ``?p`` (the
    per-predicate loop in ``_schema_from_ontology_graph``, not the graph
    selector)."""

    _GRAPH = ONTOLOGY_GRAPH_BASE + ARCHIVE_DATASET
    _MALICIOUS_PRED = "http://evil.example/p> } SELECT * WHERE { ?s ?p ?o . } #"

    def __init__(self) -> None:
        self.queries: list[str] = []

    async def sparql_select(self, query: str) -> dict:
        self.queries.append(query)
        if "?g ?label WHERE" in query:
            return {"results": {"bindings": [{"g": {"type": "uri", "value": self._GRAPH}}]}}
        if "?p ?label WHERE" in query:
            return {
                "results": {
                    "bindings": [
                        {"p": {"type": "uri", "value": self._MALICIOUS_PRED}},
                        {"p": {"type": "uri", "value": SAFE_PRED}},
                    ]
                }
            }
        return {"results": {"bindings": []}}


async def test_malicious_store_predicate_iri_is_dropped_not_embedded() -> None:
    client = _MaliciousPredicateClient()

    out = await class_schema(client, None, FOLIO_CLASS)

    assert out is not None
    ious = {p["iri"] for p in out["properties"]}
    assert client._MALICIOUS_PRED not in ious
    assert SAFE_PRED in ious
    for q in client.queries:
        assert "evil.example" not in q
        assert "SELECT * WHERE { ?s ?p ?o" not in q
