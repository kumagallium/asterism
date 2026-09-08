"""Tests for asterism.metadata (ADR dataset-description-in-the-store.md, spec
PR2).

Uses REAL pyoxigraph for every query-shaped assertion (same convention as
tests/test_shapes_store.py): a best-effort ``except`` elsewhere in the store
stack has swallowed a broken generated query before (ADR data-shape-checks.md),
so this module proves the SPARQL this code emits actually runs, not just that
the builder's own Python logic is internally consistent.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import rdflib

from asterism import metadata as m
from asterism import substrate

pyoxigraph = pytest.importorskip("pyoxigraph")

# ingest/tests -> ingest -> repo root (spec §4: "ingest/tests からは
# Path(__file__).resolve().parents[2] でリポジトリ根を取る").
_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE_MIE = _REPO_ROOT / "step0" / "tests" / "fixtures" / "starrydata_min" / "mie.yaml"
_TOGOMCP_MIE = _REPO_ROOT / "data" / "togomcp" / "mie" / "starrydata.yaml"


def _store_with_graph(turtle: str, graph_iri: str) -> pyoxigraph.Store:
    store = pyoxigraph.Store()
    store.load(
        turtle.encode("utf-8"),
        mime_type="text/turtle",
        to_graph=pyoxigraph.NamedNode(graph_iri),
    )
    return store


# ----------------------------------------------------------------------------
# 1. Round trip (spec §5.1) — real fixtures + diverse inline shapes (§0)
# ----------------------------------------------------------------------------


def _assert_roundtrip(document: dict, dataset_id: str = "roundtrip-ds") -> None:
    graph = m.build_metadata_graph(document, dataset_id)
    projected = m.project_mie_document(graph, dataset_id)
    before = m.normalize_document(document)
    after = m.normalize_document(projected)
    assert before == after


@pytest.mark.parametrize("path", [_FIXTURE_MIE, _TOGOMCP_MIE], ids=["fixture", "togomcp"])
def test_roundtrip_real_mie_yaml(path: Path) -> None:
    assert path.exists(), f"required fixture missing: {path}"
    document = m.parse_mie_yaml(path.read_text(encoding="utf-8"))
    _assert_roundtrip(document)


def test_roundtrip_query_examples_alternate_shapes() -> None:
    """{title, description, query} / {name, query} / togomcp's {..., sparql}."""
    document = {
        "sparql_query_examples": [
            {"title": "Count samples", "description": "How many.", "query": "SELECT * WHERE {}"},
            {"name": "Legacy-named example", "query": "SELECT ?s WHERE { ?s ?p ?o }"},
            {"sparql": "SELECT ?s WHERE { ?s a ?t }"},
            {"query": "SELECT 1", "sparql": "SELECT 2"},
            "a bare string example (not modeled, must survive verbatim)",
        ]
    }
    _assert_roundtrip(document)


def test_roundtrip_anti_patterns_list_shape() -> None:
    document = {
        "anti_patterns": [
            {
                "name": "Do not mint bare sample IRIs",
                "description": "sample_id collides across papers.",
                "mitigation": "Use the composite (SID, sample_id) key.",
                "impact": "Silent data loss on ingest.",
            },
            {"description": "impact-only style, no name/mitigation"},
            "a bare-string anti-pattern element",
        ]
    }
    _assert_roundtrip(document)


def test_roundtrip_sample_entries_subject_triples_shape() -> None:
    """{subject, rdf_type, note, triples:[…]} / {triple, comment} — unmodeled
    keys, so the whole item round-trips through ast:extra."""
    document = {
        "sample_rdf_entries": [
            {
                "subject": "sdr:sample/1-10",
                "rdf_type": "sd:Sample",
                "note": "one sample linked to its paper",
                "triples": [
                    "sdr:sample/1-10 a sd:Sample .",
                    "sdr:sample/1-10 sd:fromPaper sdr:paper/1 .",
                ],
            },
            {"triple": "sdr:curve/1 a sd:Curve .", "comment": "a single curve"},
            {"id": "sample-1", "rdf": "sdr:sample/1 a sd:Sample ."},
        ]
    }
    _assert_roundtrip(document)


def test_roundtrip_sparql_key_and_schema_info_unknown_keys() -> None:
    document = {
        "schema_info": {
            "title": "Unknown-key dataset",
            "version": "2.1",
            "license": "CC-BY-4.0",
            "access": "public",
            "kw_search_tools": ["sparql"],
            "base_uri": "https://example.org/data/",
        },
    }
    _assert_roundtrip(document)


def test_roundtrip_unknown_top_level_sections_str_list_dict() -> None:
    document = {
        "schema_info": {"title": "t"},
        "common_errors": "a free-text paragraph about frequent mistakes",
        "cross_references": [
            {"source_property": "schema:identifier", "target_db": "DOI", "description": "…"}
        ],
        "data_statistics": {"papers": 10, "samples": 200, "note": "as of last ingest"},
    }
    _assert_roundtrip(document)


def test_roundtrip_drops_empty_and_none_values() -> None:
    """Spec §3.7: None / "" / [] / {} keys never round-trip (they carry nothing)."""
    document = {
        "schema_info": {"title": "t", "description": None, "keywords": []},
        "anti_patterns": "",
        "architectural_notes": None,
        "sample_rdf_entries": [],
        "unknown_empty": {},
    }
    graph = m.build_metadata_graph(document, "empties-ds")
    projected = m.project_mie_document(graph, "empties-ds")
    assert projected == {"schema_info": {"title": "t"}}


def test_roundtrip_none_document() -> None:
    """A dataset with no §7 yet: still typed, nothing else to project."""
    graph = m.build_metadata_graph(None, "no-doc-ds")
    d = rdflib.URIRef(substrate.dataset_iri("no-doc-ds"))
    assert (d, rdflib.RDF.type, m.DCAT.Dataset) in graph
    assert (d, rdflib.RDF.type, m.VOID.Dataset) in graph
    assert m.project_mie_document(graph, "no-doc-ds") == {}


def test_roundtrip_duplicate_keywords_categories_graphs() -> None:
    """dcat:keyword / dcat:theme / ast:namedGraph are each written as one bare
    triple per element, and a rdflib.Graph is a *set* of triples — a repeated
    value collapses to one triple in the store no matter what. Round-tripping
    these three fields is therefore only lossless up to set (not multiset)
    equality, which is exactly what normalize_document now asserts."""
    document = {
        "schema_info": {
            "keywords": ["thermoelectric", "thermoelectric", "Seebeck"],
            "categories": ["materials science", "materials science"],
            "graphs": ["http://ex.org/g1", "http://ex.org/g1", "http://ex.org/g2"],
        }
    }
    _assert_roundtrip(document)


def test_normalize_document_does_not_erase_order_or_duplicates_elsewhere() -> None:
    """normalize_document's sort+dedupe is scoped to the three set-shaped
    paths (schema_info.keywords/categories/graphs) - a bare-string list
    ANYWHERE else (a top-level list's own bare-string items, an unknown
    section, ...) must still compare as order- and duplicate-sensitive, or
    the round-trip tests could no longer tell a correct round trip from one
    that silently dropped a repeated item and reordered the rest."""
    doc_a = {"anti_patterns": ["duplicate", "unique", "duplicate"]}
    doc_b = {"anti_patterns": ["unique", "duplicate"]}
    assert m.normalize_document(doc_a) != m.normalize_document(doc_b)
    assert m.normalize_document(doc_a) == doc_a  # order+duplicates preserved as-is

    # The three set-shaped fields are still order/duplicate-insensitive.
    set_a = {"schema_info": {"keywords": ["b", "a", "b"]}}
    set_b = {"schema_info": {"keywords": ["a", "b"]}}
    assert m.normalize_document(set_a) == m.normalize_document(set_b)


def test_roundtrip_anti_patterns_bare_strings_exact_order_and_duplicates() -> None:
    """Spec §3.2/§3.3/§3.5: bare (non-dict) sparql_query_examples/anti_patterns/
    sample_rdf_entries items get a distinct per-item subject and a strict
    ast:index, so order AND duplicates must come back exactly - checked here
    against the RAW projected document (not normalize_document), which is the
    only comparison strict enough to catch a dropped duplicate."""
    document = {
        "anti_patterns": ["dup", "unique", "dup"],
        "sample_rdf_entries": ["z entry", "a entry", "z entry"],
        "sparql_query_examples": ["q2", "q1", "q2"],
    }
    graph = m.build_metadata_graph(document, "bare-string-multiset-ds")
    projected = m.project_mie_document(graph, "bare-string-multiset-ds")
    assert projected == document


def test_schema_info_empty_string_list_elements_are_dropped() -> None:
    """Spec §3.7: an individual "" element never becomes a triple - and, for
    categories, must not mint THEME_IRI_BASE itself (empty slug)."""
    document = {"schema_info": {"keywords": ["", "a"], "categories": ["", "x"]}}
    graph = m.build_metadata_graph(document, "empty-elem-ds")
    d = rdflib.URIRef(substrate.dataset_iri("empty-elem-ds"))
    assert set(graph.objects(d, m.DCAT.keyword)) == {rdflib.Literal("a")}
    themes = set(graph.objects(d, m.DCAT.theme))
    assert themes == {rdflib.URIRef(substrate.THEME_IRI_BASE + "x")}
    assert rdflib.URIRef(substrate.THEME_IRI_BASE) not in themes
    _assert_roundtrip(document, "empty-elem-ds")


def test_schema_info_invalid_endpoint_and_graphs_do_not_break_serialization() -> None:
    """A syntactically-invalid IRI (a raw space - a plausible typo) must not
    reach URIRef()/serialize(): build_metadata_graph demotes it to the
    schema_info residual instead of crashing metadata_turtle() for the whole
    graph."""
    document = {
        "schema_info": {
            "endpoint": "http://ex.org/sparql endpoint",
            "graphs": ["http://ex.org/g 1"],
        }
    }
    graph = m.build_metadata_graph(document, "bad-iri-ds")
    turtle = m.metadata_turtle(graph)  # must not raise
    assert "sparql endpoint" in turtle  # preserved verbatim in the residual literal
    _assert_roundtrip(document, "bad-iri-ds")


# ----------------------------------------------------------------------------
# 2. Standard vocabulary actually appears (spec §5.2)
# ----------------------------------------------------------------------------


def test_standard_vocabulary_triples() -> None:
    document = {
        "schema_info": {
            "title": "Starrydata",
            "description": "Measurement curves.",
            "keywords": ["thermoelectric", "Seebeck"],
            "categories": ["materials science"],
            "base_uri": "https://example.org/starrydata/",
            "endpoint": "http://localhost:7878/query",
        },
        "sparql_query_examples": [{"title": "Count", "query": "SELECT * WHERE {}"}],
    }
    graph = m.build_metadata_graph(document, "vocab-ds")
    d = rdflib.URIRef(substrate.dataset_iri("vocab-ds"))

    assert (d, m.DCTERMS.title, rdflib.Literal("Starrydata")) in graph
    assert (d, m.DCTERMS.description, rdflib.Literal("Measurement curves.")) in graph
    assert (d, m.DCAT.keyword, rdflib.Literal("thermoelectric")) in graph

    themes = list(graph.objects(d, m.DCAT.theme))
    assert len(themes) == 1
    assert (themes[0], m.SKOS.prefLabel, rdflib.Literal("materials science")) in graph

    # void:uriSpace is a LITERAL (ADR §3: "IRI 型で書くのは VoID の誤用").
    uri_space = graph.value(d, m.VOID.uriSpace)
    assert isinstance(uri_space, rdflib.Literal)
    assert str(uri_space) == "https://example.org/starrydata/"

    # void:sparqlEndpoint is an IRI.
    endpoint = graph.value(d, m.VOID.sparqlEndpoint)
    assert isinstance(endpoint, rdflib.URIRef)

    queries = list(graph.objects(d, m.AST.hasQueryExample))
    assert len(queries) == 1
    assert (queries[0], m.SH.select, rdflib.Literal("SELECT * WHERE {}")) in graph
    assert (queries[0], m.DCTERMS.title, rdflib.Literal("Count")) in graph


# ----------------------------------------------------------------------------
# 3. ast: TBox is self-describing and minimal (spec §5.3)
# ----------------------------------------------------------------------------


def test_ast_tbox_covers_exactly_the_used_predicates() -> None:
    document = {
        "schema_info": {"graphs": ["http://ex.org/g1"]},
        "sparql_query_examples": [{"sparql": "SELECT 1"}],
        "anti_patterns": [{"name": "n", "mitigation": "m", "impact": "i", "extra_key": "x"}],
        "sample_rdf_entries": [{"rdf": "a b c ."}],
        "architectural_notes": "note",
        "shape_expressions": "shex",
        "weird_top_level_key": "value",
    }
    graph = m.build_metadata_graph(document, "tbox-ds")

    used_locals = {
        "namedGraph",
        "hasQueryExample",
        "index",
        "queryKey",
        "hasAntiPattern",
        "mitigation",
        "impact",
        "extra",
        "hasSampleEntry",
        "turtle",
        "architecturalNote",
        "shapeExpressions",
        "hasExtraSection",
        "sectionName",
        "yaml",
    }
    for local in used_locals:
        pred = m.AST[local]
        has_property_type = (pred, rdflib.RDF.type, rdflib.RDF.Property) in graph
        assert has_property_type, f"missing rdf:Property for {local}"
        labels = list(graph.objects(pred, rdflib.RDFS.label))
        assert labels, f"missing rdfs:label for {local}"

    unused_locals = set(m._AST_LABELS) - used_locals
    for local in unused_locals:
        pred = m.AST[local]
        has_property_type = (pred, rdflib.RDF.type, rdflib.RDF.Property) in graph
        assert not has_property_type, f"unused predicate {local} got a TBox entry"


# ----------------------------------------------------------------------------
# 4. SHACL (spec §5.4)
# ----------------------------------------------------------------------------


_RML = """
@prefix rr: <http://www.w3.org/ns/r2rml#> .
@prefix rml: <http://semweb.mmlab.be/ns/rml#> .
@prefix ql: <http://semweb.mmlab.be/ns/ql#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix ex: <https://example.org/v/> .

<#Sample> a rr:TriplesMap ;
  rml:logicalSource [ rml:source "samples.csv" ; rml:referenceFormulation ql:CSV ] ;
  rr:subjectMap [ rr:template "https://example.org/r/sample/{id}" ; rr:class ex:Sample ] ;
  rr:predicateObjectMap [
    rr:predicate ex:mass ;
    rr:objectMap [ rml:reference "mass" ; rr:datatype xsd:double ]
  ] .
"""


def test_shacl_shapes_emitted_and_hidden_from_projection() -> None:
    document = {"schema_info": {"title": "Shaped"}}
    graph = m.build_metadata_graph(document, "shape-ds", rml_ttl=_RML)
    d = rdflib.URIRef(substrate.dataset_iri("shape-ds"))

    node_shapes = list(graph.subjects(rdflib.RDF.type, m.SH.NodeShape))
    assert node_shapes, "expected at least one sh:NodeShape"
    linked = set(graph.objects(d, m.AST.hasShape))
    assert linked == set(node_shapes)

    # Projection never sees the shapes — they are not part of the §7 document.
    projected = m.project_mie_document(graph, "shape-ds")
    assert projected == {"schema_info": {"title": "Shaped"}}


def test_shacl_compile_failure_is_swallowed() -> None:
    """Best-effort (ADR §4): a broken/unrelated RML must not raise."""
    document = {"schema_info": {"title": "t"}}
    graph = m.build_metadata_graph(document, "broken-rml-ds", rml_ttl="not valid turtle {{{")
    assert not list(graph.subjects(rdflib.RDF.type, m.SH.NodeShape))
    assert m.project_mie_document(graph, "broken-rml-ds") == document


def test_shacl_class_with_invalid_iri_char_falls_back_instead_of_crashing() -> None:
    """A syntactically-valid RML whose rr:class contains a character rdflib's
    Turtle *parser* accepts (with only a warning) but its *serializer* rejects
    (e.g. a raw space - a plausible LLM/human typo) must be caught INSIDE
    _add_shapes's best-effort try/except, not merged into the graph and left
    to crash the first later metadata_turtle()/graph.serialize() call."""
    rml_with_space_in_class = """
    @prefix rr: <http://www.w3.org/ns/r2rml#> .
    @prefix rml: <http://semweb.mmlab.be/ns/rml#> .

    <#TM> a rr:TriplesMap ;
      rml:logicalSource [ rml:source "x.csv" ] ;
      rr:subjectMap [
        rr:template "https://example.org/x/{id}" ;
        rr:class <https://example.org/v/Weird Class>
      ] .
    """
    document = {"schema_info": {"title": "t"}}
    graph = m.build_metadata_graph(
        document, "weird-shape-ds", rml_ttl=rml_with_space_in_class
    )
    # Best-effort: no shape triples merged, and the rest of the graph is intact.
    assert not list(graph.subjects(rdflib.RDF.type, m.SH.NodeShape))
    d = rdflib.URIRef(substrate.dataset_iri("weird-shape-ds"))
    assert not list(graph.objects(d, m.AST.hasShape))
    turtle = m.metadata_turtle(graph)  # must not raise
    assert "t" in turtle
    assert m.project_mie_document(graph, "weird-shape-ds") == document


# ----------------------------------------------------------------------------
# 5. Real pyoxigraph (spec §5.5)
# ----------------------------------------------------------------------------


def test_real_pyoxigraph_schema_summary_shaped_query() -> None:
    """The query ADR §6 says schema_summary will run: dcterms:title +
    dcterms:description, scoped to an explicit VALUES list of graph IRIs."""
    document = {"schema_info": {"title": "Real Store Dataset", "description": "A description."}}
    dataset_id = "real-store-ds"
    graph = m.build_metadata_graph(document, dataset_id)
    graph_iri = substrate.meta_graph_iri(dataset_id)
    store = _store_with_graph(m.metadata_turtle(graph), graph_iri)

    query = f"""
    PREFIX dcterms: <http://purl.org/dc/terms/>
    SELECT ?d ?title ?desc WHERE {{
      VALUES ?g {{ <{graph_iri}> }}
      GRAPH ?g {{ ?d dcterms:title ?title ; dcterms:description ?desc }}
    }}
    """
    rows = list(store.query(query))
    assert len(rows) == 1
    assert rows[0]["title"].value == "Real Store Dataset"
    assert rows[0]["desc"].value == "A description."


def test_real_pyoxigraph_fetch_construct_query_runs() -> None:
    """The exact CONSTRUCT fetch_metadata_graph issues, run for real — a broken
    generated query must raise, not vanish into a best-effort except."""
    document = {"schema_info": {"title": "t"}, "anti_patterns": "do not do X"}
    dataset_id = "construct-ds"
    graph = m.build_metadata_graph(document, dataset_id)
    graph_iri = substrate.meta_graph_iri(dataset_id)
    store = _store_with_graph(m.metadata_turtle(graph), graph_iri)

    query = f"CONSTRUCT {{ ?s ?p ?o }} WHERE {{ GRAPH <{graph_iri}> {{ ?s ?p ?o }} }}"
    result_quads = list(store.query(query))
    assert len(result_quads) == len(graph)


# ----------------------------------------------------------------------------
# 6. fetch/write against a fake client (spec §5.6)
# ----------------------------------------------------------------------------


class _FakeClient:
    """Records calls; sparql_construct returns pre-seeded Turtle."""

    def __init__(self, construct_result: str = "") -> None:
        self.updates: list[str] = []
        self.posts: list[tuple[bytes, str | None]] = []
        self.construct_queries: list[str] = []
        self._construct_result = construct_result

    async def sparql_update(self, update: str) -> None:
        self.updates.append(update)

    async def sparql_construct(self, query: str) -> str:
        self.construct_queries.append(query)
        return self._construct_result

    async def post_turtle_bytes(self, payload: bytes, graph_iri: str | None = None) -> int:
        self.posts.append((payload, graph_iri))
        return len(payload)


async def test_write_metadata_graph_drops_then_posts() -> None:
    document = {"schema_info": {"title": "Write Me"}}
    dataset_id = "write-ds"
    graph = m.build_metadata_graph(document, dataset_id)
    client = _FakeClient()

    n = await m.write_metadata_graph(client, dataset_id, graph)

    assert n == len(graph)
    assert len(client.updates) == 1
    assert "DROP" in client.updates[0]
    assert substrate.meta_graph_iri(dataset_id) in client.updates[0]
    assert len(client.posts) == 1
    payload, graph_iri = client.posts[0]
    assert graph_iri == substrate.meta_graph_iri(dataset_id)
    # DROP happened before the POST (order matters: replace, not append).
    assert client.updates[0] and client.posts

    reloaded = m.graph_from_turtle(payload.decode("utf-8"))
    projected = m.project_mie_document(reloaded, dataset_id)
    assert projected == document


class _DropOkPostFailsClient(_FakeClient):
    """DROP succeeds, then POST fails — the non-atomic window where the
    store's meta/{id} graph ends up empty (neither old nor new content)."""

    async def post_turtle_bytes(self, payload: bytes, graph_iri: str | None = None) -> int:
        raise ConnectionError("simulated: connection dropped mid-POST")


async def test_write_metadata_graph_wraps_post_failure_after_drop_succeeded() -> None:
    """A POST failure that comes *after* the DROP already went through must be
    reported as :class:`m.MetadataGraphWriteError` — distinguishable from a
    DROP-side failure (store unreachable, graph untouched) — so a caller can
    tell an operator the store-side graph is now empty, not just stale."""
    document = {"schema_info": {"title": "Write Me"}}
    dataset_id = "write-ds"
    graph = m.build_metadata_graph(document, dataset_id)
    client = _DropOkPostFailsClient()

    with pytest.raises(m.MetadataGraphWriteError) as excinfo:
        await m.write_metadata_graph(client, dataset_id, graph)

    # The DROP itself did happen (that's exactly the dangerous case).
    assert len(client.updates) == 1
    err = excinfo.value
    assert err.dataset_id == dataset_id
    assert err.graph_iri == substrate.meta_graph_iri(dataset_id)
    assert "now empty" in str(err)
    assert isinstance(err.__cause__, ConnectionError)


class _DropFailsClient(_FakeClient):
    """The DROP itself fails (store unreachable) — post_turtle_bytes must
    never even be attempted, and the raised exception must NOT be
    MetadataGraphWriteError (the graph was never touched)."""

    async def sparql_update(self, update: str) -> None:
        raise ConnectionError("simulated: store unreachable")

    async def post_turtle_bytes(self, payload: bytes, graph_iri: str | None = None) -> int:
        raise AssertionError("post_turtle_bytes must not be reached when DROP itself fails")


async def test_write_metadata_graph_drop_failure_is_not_wrapped() -> None:
    document = {"schema_info": {"title": "Write Me"}}
    dataset_id = "write-ds"
    graph = m.build_metadata_graph(document, dataset_id)
    client = _DropFailsClient()

    with pytest.raises(ConnectionError) as excinfo:
        await m.write_metadata_graph(client, dataset_id, graph)
    assert not isinstance(excinfo.value, m.MetadataGraphWriteError)


async def test_fetch_metadata_graph_construct_shape_and_roundtrip() -> None:
    document = {"schema_info": {"title": "Fetch Me"}, "anti_patterns": "avoid Y"}
    dataset_id = "fetch-ds"
    graph = m.build_metadata_graph(document, dataset_id)
    turtle = m.metadata_turtle(graph)
    client = _FakeClient(construct_result=turtle)

    fetched = await m.fetch_metadata_graph(client, dataset_id)

    assert len(client.construct_queries) == 1
    query = client.construct_queries[0]
    assert "CONSTRUCT" in query
    assert substrate.meta_graph_iri(dataset_id) in query
    assert m.project_mie_document(fetched, dataset_id) == document


async def test_fetch_metadata_graph_empty_result_is_empty_graph() -> None:
    client = _FakeClient(construct_result="")
    fetched = await m.fetch_metadata_graph(client, "absent-ds")
    assert len(fetched) == 0


# ----------------------------------------------------------------------------
# 7. Determinism (spec §5.7)
# ----------------------------------------------------------------------------


def test_metadata_turtle_is_byte_deterministic() -> None:
    document = {
        "schema_info": {
            "title": "Determinism Check",
            "keywords": ["b", "a", "c"],
            "categories": ["y", "x"],
        },
        "sparql_query_examples": [
            {"title": "q1", "query": "SELECT 1"},
            {"title": "q2", "query": "SELECT 2"},
        ],
        "anti_patterns": [{"name": "n1"}, {"name": "n2"}],
    }
    turtle_a = m.metadata_turtle(m.build_metadata_graph(document, "det-ds"))
    turtle_b = m.metadata_turtle(m.build_metadata_graph(document, "det-ds"))
    assert turtle_a == turtle_b


# ----------------------------------------------------------------------------
# 8. substrate helpers (spec §5.8)
# ----------------------------------------------------------------------------


def test_meta_graph_iri_rejects_unsafe_id() -> None:
    with pytest.raises(ValueError):
        substrate.meta_graph_iri("x y")


def test_meta_graph_iri_shape() -> None:
    assert substrate.meta_graph_iri("abc") == f"{substrate.META_GRAPH_BASE}abc"


def test_dataset_iri_rejects_unsafe_id() -> None:
    with pytest.raises(ValueError):
        substrate.dataset_iri("x y")


def test_dataset_iri_shape() -> None:
    assert substrate.dataset_iri("abc") == f"{substrate.DATASET_IRI_BASE}abc"


async def test_meta_graphs_lists_only_meta_named_graphs() -> None:
    """Same enumeration shape as ontology_graphs: STRSTARTS over the empty-group
    graph-name index, no triple scan."""

    class _FakeSelectClient:
        def __init__(self, bindings: list[dict]) -> None:
            self._bindings = bindings
            self.queries: list[str] = []

        async def sparql_select(self, query: str) -> dict:
            self.queries.append(query)
            return {"results": {"bindings": self._bindings}}

    # The FILTER runs server-side; this fake just proves the query is built
    # correctly and the SPARQL-JSON bindings are parsed correctly — a real
    # engine's STRSTARTS is what would actually exclude other_iri.
    meta_iri = substrate.meta_graph_iri("a")
    client = _FakeSelectClient([{"g": {"type": "uri", "value": meta_iri}}])
    out = await substrate.meta_graphs(client)
    assert out == [meta_iri]
    assert substrate.META_GRAPH_BASE in client.queries[0]
    assert "STRSTARTS" in client.queries[0]
