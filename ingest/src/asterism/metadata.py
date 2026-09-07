"""Deterministic triples for a dataset's *description* (ADR
``dataset-description-in-the-store.md``).

A dataset's §7 (``mie.yaml``) — title, description, keywords, example SPARQL
queries, anti-patterns, sample RDF — used to be a single YAML file with no
relation to the triples the dataset actually holds. This module makes the
triples the source of truth: :func:`build_metadata_graph` turns a parsed
``mie.yaml`` into an RDF graph (standard DCAT/VoID/SHACL vocabulary where one
exists, a small closed ``ast:`` vocabulary otherwise), and
:func:`project_mie_document` / :func:`project_mie_yaml` turn that graph back
into the same shape of document.

The one governing rule (ADR §0, observed from real ``mie.yaml`` files, which
are LLM/human-authored and therefore inconsistent in shape): known keys become
standard-vocabulary triples, everything else — unknown top-level keys, known
keys with an unexpected type, unmodeled fields inside a known list's items —
is round-tripped *verbatim* as a YAML literal (``ast:yaml`` / ``ast:extra``).
Nothing is ever silently dropped except the one normalization the ADR names:
a key whose value is ``None``, ``""``, ``[]`` or ``{}`` carries no information
and is never written (:func:`normalize_document` applies the same rule so
tests can compare "before" and "after" for equality).

No LLM calls anywhere in this module (ADR §5, §11): building and projecting
are both pure, deterministic functions of their input.
"""
from __future__ import annotations

import logging
import urllib.parse
from collections.abc import Iterable, Mapping
from typing import Protocol

import rdflib
import yaml
from rdflib import RDF, RDFS, Literal, URIRef
from rdflib.namespace import Namespace

from asterism import substrate
from asterism.shapes import NodeShape, compile_shapes, shapes_to_shacl

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------------
# Vocabulary (ADR §2)
# ----------------------------------------------------------------------------

DCAT = Namespace("http://www.w3.org/ns/dcat#")
DCTERMS = Namespace("http://purl.org/dc/terms/")
VOID = Namespace("http://rdfs.org/ns/void#")
SH = Namespace("http://www.w3.org/ns/shacl#")
SKOS = Namespace("http://www.w3.org/2004/02/skos/core#")
XSD = rdflib.XSD
AST = Namespace(substrate.ASTERISM_NS)

# English label for each ``ast:`` predicate this module can emit. A dataset's
# description graph carries an ``rdf:Property``/``rdfs:label`` pair for ONLY the
# predicates it actually used (spec §2) — a reader who lands on the graph with
# no other context can still tell what an unfamiliar ``ast:`` term means.
_AST_LABELS: dict[str, str] = {
    "namedGraph": "named graph",
    "hasShape": "has shape",
    "shapeExpressions": "shape expressions (ShEx)",
    "hasQueryExample": "has query example",
    "queryKey": "query key",
    "antiPattern": "anti-pattern",
    "hasAntiPattern": "has anti-pattern",
    "mitigation": "mitigation",
    "impact": "impact",
    "architecturalNote": "architectural note",
    "hasSampleEntry": "has sample entry",
    "turtle": "turtle",
    "hasExtraSection": "has extra section",
    "sectionName": "section name",
    "yaml": "YAML",
    "extra": "extra",
    "index": "index",
}

# The six top-level keys this module models (ADR §3 / spec §3) are the
# branches of the if/elif chain in build_metadata_graph; anything else — an
# unknown key, or one of these with an unexpected type/shape — falls to its
# `else` and becomes an "extra section" (spec §3.6). project_mie_document's
# fixed output order (spec §4) is likewise just the sequence its own `if
# section: doc[...] = ...` statements run in. Neither is driven by a shared
# constant (removed here after it was found unused - grep before reintroducing
# one: if it doesn't actually drive the dispatch/order, it is just a second
# place to forget to update).


class SupportsMetadataStore(Protocol):
    """The slice of :class:`asterism.oxigraph_client.OxigraphClient` this module
    needs: SPARQL Update (for the DROP in :func:`write_metadata_graph`), SPARQL
    CONSTRUCT (for :func:`fetch_metadata_graph`), and a Graph Store POST."""

    async def sparql_update(self, update: str) -> None: ...
    async def sparql_construct(self, query: str) -> str: ...
    async def post_turtle_bytes(self, payload: bytes, graph_iri: str | None = None) -> int: ...


# ----------------------------------------------------------------------------
# Shared helpers
# ----------------------------------------------------------------------------


def _bind(graph: rdflib.Graph) -> None:
    """Bind the fixed prefix set (ADR §2) so every serialization uses them."""
    graph.bind("dcat", DCAT)
    graph.bind("dcterms", DCTERMS)
    graph.bind("void", VOID)
    graph.bind("sh", SH)
    graph.bind("skos", SKOS)
    graph.bind("rdfs", RDFS)
    graph.bind("rdf", RDF)
    graph.bind("xsd", XSD)
    graph.bind("ast", AST)


def _is_droppable(value: object) -> bool:
    """Spec §3.7: a key whose value is ``None``/``""``/``[]``/``{}`` carries no
    information and is never turned into a triple (nor kept in a "residual" dict
    that gets round-tripped verbatim)."""
    if value is None:
        return True
    if isinstance(value, str) and value == "":
        return True
    if isinstance(value, list) and len(value) == 0:
        return True
    return bool(isinstance(value, dict) and len(value) == 0)


# The exact character set rdflib's own Turtle serializer rejects a URIRef for
# (rdflib.term._is_valid_uri). A ``schema_info.endpoint``/``graphs[]`` value
# that slips one of these past a naive ``"://" in v`` check (e.g. a raw space
# from an LLM/human typo) builds fine but raises on the *first* later
# ``graph.serialize()`` call — taking down the whole graph, not just the
# offending triple. Mirroring rdflib's own check here lets us catch it before
# ``URIRef()`` and demote the value to the residual instead.
_INVALID_URI_CHARS = '<>" {}|\\^`'


def _is_valid_iri(value: str) -> bool:
    """Would ``URIRef(value)`` survive Turtle serialization?"""
    return "://" in value and not any(c in value for c in _INVALID_URI_CHARS)


def _dump_extra_yaml(value: object) -> str:
    """``ast:yaml`` / ``ast:extra`` literal content (spec §2's exact recipe)."""
    return yaml.safe_dump(value, sort_keys=False, allow_unicode=True)


def _int_literal(n: int) -> Literal:
    return Literal(n, datatype=XSD.integer)


# ----------------------------------------------------------------------------
# Builder (spec §3)
# ----------------------------------------------------------------------------


def build_metadata_graph(
    document: Mapping[str, object] | None,
    dataset_id: str,
    *,
    rml_ttl: str | None = None,
) -> rdflib.Graph:
    """Deterministically compile a parsed ``mie.yaml`` document into an RDF graph.

    ``document`` is typically the result of :func:`parse_mie_yaml` (or ``None``
    for a dataset with no §7 yet — the subject is still typed ``dcat:Dataset``).
    ``rml_ttl``, when given, adds the RML-derived SHACL node shapes (spec §3.8);
    a shape-compile failure is logged and skipped rather than raised — metadata
    is best-effort (ADR §4), it must never fail an ingest/promote on its own.
    """
    graph = rdflib.Graph()
    _bind(graph)
    d = URIRef(substrate.dataset_iri(dataset_id))
    used: set[str] = set()

    graph.add((d, RDF.type, DCAT.Dataset))
    graph.add((d, RDF.type, VOID.Dataset))

    doc = document if isinstance(document, Mapping) else {}
    extra_index = 1
    for key, value in doc.items():
        if _is_droppable(value):
            continue
        if key == "schema_info":
            if isinstance(value, dict):
                residual = _build_schema_info(graph, d, value, used)
                if residual:
                    _write_extra_section(graph, d, "schema_info", residual, 0, used)
            else:
                _write_extra_section(graph, d, key, value, extra_index, used)
                extra_index += 1
        elif key == "sparql_query_examples":
            if isinstance(value, list):
                _build_query_examples(graph, d, value, used)
            else:
                _write_extra_section(graph, d, key, value, extra_index, used)
                extra_index += 1
        elif key == "anti_patterns":
            if isinstance(value, (str, list)):
                _build_anti_patterns(graph, d, value, used)
            else:
                _write_extra_section(graph, d, key, value, extra_index, used)
                extra_index += 1
        elif key in ("architectural_notes", "shape_expressions"):
            if isinstance(value, str):
                is_notes = key == "architectural_notes"
                pred = AST.architecturalNote if is_notes else AST.shapeExpressions
                local = "architecturalNote" if is_notes else "shapeExpressions"
                graph.add((d, pred, Literal(value)))
                used.add(local)
            else:
                _write_extra_section(graph, d, key, value, extra_index, used)
                extra_index += 1
        elif key == "sample_rdf_entries":
            if isinstance(value, list):
                _build_sample_entries(graph, d, value, used)
            else:
                _write_extra_section(graph, d, key, value, extra_index, used)
                extra_index += 1
        else:
            _write_extra_section(graph, d, key, value, extra_index, used)
            extra_index += 1

    if rml_ttl:
        _add_shapes(graph, d, rml_ttl, used)

    _add_tbox(graph, used)
    return graph


def _write_extra_section(
    graph: rdflib.Graph, d: URIRef, key: str, value: object, index: int, used: set[str]
) -> None:
    """Spec §3.6: an unknown/mistyped top-level key, round-tripped verbatim."""
    s = URIRef(f"{d}/section/{urllib.parse.quote(key, safe='')}")
    graph.add((d, AST.hasExtraSection, s))
    graph.add((s, AST.sectionName, Literal(key)))
    graph.add((s, AST.yaml, Literal(_dump_extra_yaml(value))))
    graph.add((s, AST["index"], _int_literal(index)))
    used.update({"hasExtraSection", "sectionName", "yaml", "index"})


def _build_schema_info(
    graph: rdflib.Graph, d: URIRef, value: dict, used: set[str]
) -> dict[str, object]:
    """Spec §3.1. Returns the residual dict (keys that did not become triples)."""
    residual: dict[str, object] = {}
    for key, v in value.items():
        if _is_droppable(v):
            continue
        if key == "title" and isinstance(v, str):
            graph.add((d, DCTERMS.title, Literal(v)))
        elif key == "description" and isinstance(v, str):
            graph.add((d, DCTERMS.description, Literal(v)))
        elif key == "keywords" and isinstance(v, list) and all(isinstance(x, str) for x in v):
            for kw in v:
                if _is_droppable(kw):  # e.g. a stray "" element - never a triple (spec §3.7)
                    continue
                graph.add((d, DCAT.keyword, Literal(kw)))
        elif key == "categories" and isinstance(v, list) and all(isinstance(x, str) for x in v):
            for cat in v:
                if _is_droppable(cat):
                    # An empty category would otherwise mint THEME_IRI_BASE
                    # itself (quote("", safe="") == ""), colliding across datasets.
                    continue
                t = URIRef(substrate.THEME_IRI_BASE + urllib.parse.quote(cat, safe=""))
                graph.add((d, DCAT.theme, t))
                graph.add((t, RDF.type, SKOS.Concept))
                graph.add((t, SKOS.prefLabel, Literal(cat)))
        elif key == "base_uri" and isinstance(v, str):
            graph.add((d, VOID.uriSpace, Literal(v)))
        elif key == "endpoint" and isinstance(v, str) and _is_valid_iri(v):
            graph.add((d, VOID.sparqlEndpoint, URIRef(v)))
        elif (
            key == "graphs"
            and isinstance(v, list)
            and all(isinstance(x, str) and _is_valid_iri(x) for x in v)
        ):
            for g in v:
                graph.add((d, AST.namedGraph, URIRef(g)))
            used.add("namedGraph")
        else:
            # Includes: endpoint/graphs values that look like a URI but are not
            # syntactically valid (e.g. a raw space) - round-tripped verbatim
            # via the schema_info residual instead of ever reaching URIRef().
            residual[key] = v
    return residual


def _build_query_examples(graph: rdflib.Graph, d: URIRef, items: list, used: set[str]) -> None:
    """Spec §3.2."""
    used.update({"hasQueryExample", "index"})
    for i, item in enumerate(items, start=1):
        q = URIRef(f"{d}/query/{i}")
        graph.add((d, AST.hasQueryExample, q))
        graph.add((q, RDF.type, SH.SPARQLExecutable))
        graph.add((q, RDF.type, SH.SPARQLSelectExecutable))
        graph.add((q, AST["index"], _int_literal(i)))
        if isinstance(item, dict):
            residual: dict[str, object] = {}
            query_val = item.get("query")
            query_is_str = isinstance(query_val, str) and not _is_droppable(query_val)
            for key, v in item.items():
                if _is_droppable(v):
                    continue
                if key == "title" and isinstance(v, str):
                    graph.add((q, DCTERMS.title, Literal(v)))
                elif key == "description" and isinstance(v, str):
                    graph.add((q, RDFS.comment, Literal(v)))
                elif key == "query" and isinstance(v, str):
                    graph.add((q, SH.select, Literal(v)))
                elif key == "sparql" and isinstance(v, str):
                    if query_is_str:
                        residual["sparql"] = v
                    else:
                        graph.add((q, SH.select, Literal(v)))
                        graph.add((q, AST.queryKey, Literal("sparql")))
                        used.add("queryKey")
                else:
                    residual[key] = v
            if residual:
                graph.add((q, AST.extra, Literal(_dump_extra_yaml(residual))))
                used.add("extra")
        else:
            graph.add((q, AST.extra, Literal(_dump_extra_yaml(item))))
            used.add("extra")


def _build_anti_patterns(graph: rdflib.Graph, d: URIRef, value: object, used: set[str]) -> None:
    """Spec §3.3."""
    if isinstance(value, str):
        graph.add((d, AST.antiPattern, Literal(value)))
        used.add("antiPattern")
        return
    assert isinstance(value, list)
    used.update({"hasAntiPattern", "index"})
    for i, item in enumerate(value, start=1):
        a = URIRef(f"{d}/anti-pattern/{i}")
        graph.add((d, AST.hasAntiPattern, a))
        graph.add((a, AST["index"], _int_literal(i)))
        if isinstance(item, dict):
            residual: dict[str, object] = {}
            for key, v in item.items():
                if _is_droppable(v):
                    continue
                if key == "name" and isinstance(v, str):
                    graph.add((a, DCTERMS.title, Literal(v)))
                elif key == "description" and isinstance(v, str):
                    graph.add((a, DCTERMS.description, Literal(v)))
                elif key == "mitigation" and isinstance(v, str):
                    graph.add((a, AST.mitigation, Literal(v)))
                    used.add("mitigation")
                elif key == "impact" and isinstance(v, str):
                    graph.add((a, AST.impact, Literal(v)))
                    used.add("impact")
                else:
                    residual[key] = v
            if residual:
                graph.add((a, AST.extra, Literal(_dump_extra_yaml(residual))))
                used.add("extra")
        else:
            graph.add((a, AST.extra, Literal(_dump_extra_yaml(item))))
            used.add("extra")


def _build_sample_entries(graph: rdflib.Graph, d: URIRef, items: list, used: set[str]) -> None:
    """Spec §3.5."""
    used.update({"hasSampleEntry", "index"})
    for i, item in enumerate(items, start=1):
        e = URIRef(f"{d}/sample/{i}")
        graph.add((d, AST.hasSampleEntry, e))
        graph.add((e, AST["index"], _int_literal(i)))
        if isinstance(item, dict):
            residual: dict[str, object] = {}
            for key, v in item.items():
                if _is_droppable(v):
                    continue
                if key == "id" and isinstance(v, str):
                    graph.add((e, DCTERMS.identifier, Literal(v)))
                elif key == "rdf" and isinstance(v, str):
                    graph.add((e, AST.turtle, Literal(v)))
                    used.add("turtle")
                elif key == "title" and isinstance(v, str):
                    graph.add((e, DCTERMS.title, Literal(v)))
                elif key == "description" and isinstance(v, str):
                    graph.add((e, RDFS.comment, Literal(v)))
                else:
                    residual[key] = v
            if residual:
                graph.add((e, AST.extra, Literal(_dump_extra_yaml(residual))))
                used.add("extra")
        else:
            graph.add((e, AST.extra, Literal(_dump_extra_yaml(item))))
            used.add("extra")


def _add_shapes(graph: rdflib.Graph, d: URIRef, rml_ttl: str, used: set[str]) -> None:
    """Spec §3.8: RML-derived SHACL node shapes, linked from the dataset subject.

    Best-effort: a bad/unrelated RML document must not break the description
    (metadata is a side channel, never on the critical path — ADR §4).
    """
    try:
        shapes: Iterable[NodeShape] = compile_shapes(rml_ttl)
        shacl_ttl = shapes_to_shacl(shapes, base=f"{d}/shape/")
        shacl_graph = rdflib.Graph()
        shacl_graph.parse(data=shacl_ttl, format="turtle")
        # Canary: rdflib's Turtle *parser* only warns on a term it cannot
        # serialize later (e.g. a raw space inside an RML rr:class/predicate
        # IRI - a plausible LLM/human typo), it does not raise. Force the
        # same check `metadata_turtle()` will hit down the line, HERE, while
        # we can still catch it and fall back to "no shapes" instead of
        # poisoning the whole description graph once these triples are merged.
        shacl_graph.serialize(format="turtle")
    except Exception:
        logger.warning(
            "build_metadata_graph: shape compile failed, continuing without shapes",
            exc_info=True,
        )
        return
    node_shapes = list(shacl_graph.subjects(RDF.type, SH.NodeShape))
    for s in node_shapes:
        graph.add((d, AST.hasShape, s))
    if node_shapes:
        used.add("hasShape")
    for triple in shacl_graph:
        graph.add(triple)


def _add_tbox(graph: rdflib.Graph, used: set[str]) -> None:
    """Spec §2: each graph carries ``rdf:Property``/``rdfs:label`` for only the
    ``ast:`` predicates it actually used."""
    for local in sorted(used):
        label = _AST_LABELS.get(local)
        if label is None:  # pragma: no cover - defensive; every caller uses a known local name
            continue
        pred = AST[local]
        graph.add((pred, RDF.type, RDF.Property))
        graph.add((pred, RDFS.label, Literal(label, lang="en")))


# ----------------------------------------------------------------------------
# Projection (spec §4) — the builder's inverse
# ----------------------------------------------------------------------------


def _lit_str(value: object) -> str | None:
    if value is None:
        return None
    s = str(value)
    return s if s != "" else None


def _extra_sections(graph: rdflib.Graph, d: URIRef) -> list[tuple[int, str, object]]:
    out: list[tuple[int, str, object]] = []
    for s in graph.objects(d, AST.hasExtraSection):
        name = _lit_str(graph.value(s, AST.sectionName)) or ""
        idx_lit = graph.value(s, AST["index"])
        idx = int(idx_lit) if idx_lit is not None else 0
        yaml_lit = graph.value(s, AST.yaml)
        value = yaml.safe_load(str(yaml_lit)) if yaml_lit is not None else None
        out.append((idx, name, value))
    return out


def _project_extra_merge(graph: rdflib.Graph, subject: URIRef, known: dict[str, object]) -> object:
    """Spec §4: known-predicate fields plus the ``ast:extra`` residual, merged.
    ``known`` and the residual are disjoint by construction (the builder only
    ever puts a key in ``ast:extra`` when it did NOT become a known predicate),
    so ``update`` direction does not matter."""
    extra_lit = graph.value(subject, AST.extra)
    if extra_lit is None:
        return known
    extra_val = yaml.safe_load(str(extra_lit))
    if isinstance(extra_val, dict):
        merged = dict(known)
        merged.update(extra_val)
        return merged
    # A non-dict list element has no known predicates by construction (the
    # builder only writes ast:extra for a whole non-dict item), so `known` is {}.
    return extra_val


def _project_list_section(
    graph: rdflib.Graph, subject: URIRef, link_pred: URIRef, projector
) -> list:
    items: list[tuple[int, object]] = []
    for obj in graph.objects(subject, link_pred):
        idx_lit = graph.value(obj, AST["index"])
        idx = int(idx_lit) if idx_lit is not None else 0
        items.append((idx, projector(graph, obj)))
    items.sort(key=lambda t: t[0])
    return [v for _, v in items]


def _project_query_example(graph: rdflib.Graph, q: URIRef) -> object:
    known: dict[str, object] = {}
    title = _lit_str(graph.value(q, DCTERMS.title))
    if title is not None:
        known["title"] = title
    desc = _lit_str(graph.value(q, RDFS.comment))
    if desc is not None:
        known["description"] = desc
    select = _lit_str(graph.value(q, SH.select))
    if select is not None:
        query_key = _lit_str(graph.value(q, AST.queryKey))
        key_name = "sparql" if query_key == "sparql" else "query"
        known[key_name] = select
    return _project_extra_merge(graph, q, known)


def _project_anti_pattern_item(graph: rdflib.Graph, a: URIRef) -> object:
    known: dict[str, object] = {}
    name = _lit_str(graph.value(a, DCTERMS.title))
    if name is not None:
        known["name"] = name
    desc = _lit_str(graph.value(a, DCTERMS.description))
    if desc is not None:
        known["description"] = desc
    mitigation = _lit_str(graph.value(a, AST.mitigation))
    if mitigation is not None:
        known["mitigation"] = mitigation
    impact = _lit_str(graph.value(a, AST.impact))
    if impact is not None:
        known["impact"] = impact
    return _project_extra_merge(graph, a, known)


def _project_sample_entry(graph: rdflib.Graph, e: URIRef) -> object:
    known: dict[str, object] = {}
    ident = _lit_str(graph.value(e, DCTERMS.identifier))
    if ident is not None:
        known["id"] = ident
    title = _lit_str(graph.value(e, DCTERMS.title))
    if title is not None:
        known["title"] = title
    desc = _lit_str(graph.value(e, RDFS.comment))
    if desc is not None:
        known["description"] = desc
    rdf_lit = _lit_str(graph.value(e, AST.turtle))
    if rdf_lit is not None:
        known["rdf"] = rdf_lit
    return _project_extra_merge(graph, e, known)


def _project_schema_info(
    graph: rdflib.Graph, d: URIRef, schema_residual: object
) -> dict[str, object]:
    info: dict[str, object] = {}
    title = _lit_str(graph.value(d, DCTERMS.title))
    if title is not None:
        info["title"] = title
    desc = _lit_str(graph.value(d, DCTERMS.description))
    if desc is not None:
        info["description"] = desc
    base_uri = _lit_str(graph.value(d, VOID.uriSpace))
    if base_uri is not None:
        info["base_uri"] = base_uri
    endpoint = _lit_str(graph.value(d, VOID.sparqlEndpoint))
    if endpoint is not None:
        info["endpoint"] = endpoint
    graphs = sorted({str(g) for g in graph.objects(d, AST.namedGraph)})
    if graphs:
        info["graphs"] = graphs
    keywords = sorted({str(k) for k in graph.objects(d, DCAT.keyword)})
    if keywords:
        info["keywords"] = keywords
    categories: set[str] = set()
    for t in graph.objects(d, DCAT.theme):
        label = _lit_str(graph.value(t, SKOS.prefLabel))
        if label is not None:
            categories.add(label)
        else:
            tail = str(t).rsplit("/", 1)[-1]
            categories.add(urllib.parse.unquote(tail))
    if categories:
        info["categories"] = sorted(categories)
    if isinstance(schema_residual, dict):
        for key, value in schema_residual.items():
            if key not in info:
                info[key] = value
    return info


def project_mie_document(graph: rdflib.Graph, dataset_id: str) -> dict[str, object]:
    """Inverse of :func:`build_metadata_graph`: reconstruct a ``mie.yaml``-shaped
    document from the description graph.

    Output key order is fixed (spec §4): ``schema_info``, ``shape_expressions``,
    ``sample_rdf_entries``, ``sparql_query_examples``, ``anti_patterns``,
    ``architectural_notes``, then unknown sections in ``ast:index`` order.
    """
    d = URIRef(substrate.dataset_iri(dataset_id))
    sections = _extra_sections(graph, d)
    schema_residual: object = None
    unknown: list[tuple[int, str, object]] = []
    for idx, name, value in sections:
        if idx == 0 and name == "schema_info":
            schema_residual = value
        else:
            unknown.append((idx, name, value))
    unknown.sort(key=lambda t: t[0])

    doc: dict[str, object] = {}

    schema_info = _project_schema_info(graph, d, schema_residual)
    if schema_info:
        doc["schema_info"] = schema_info

    shape_expr = _lit_str(graph.value(d, AST.shapeExpressions))
    if shape_expr is not None:
        doc["shape_expressions"] = shape_expr

    samples = _project_list_section(graph, d, AST.hasSampleEntry, _project_sample_entry)
    if samples:
        doc["sample_rdf_entries"] = samples

    queries = _project_list_section(graph, d, AST.hasQueryExample, _project_query_example)
    if queries:
        doc["sparql_query_examples"] = queries

    anti_literal = _lit_str(graph.value(d, AST.antiPattern))
    if anti_literal is not None:
        doc["anti_patterns"] = anti_literal
    else:
        anti_items = _project_list_section(graph, d, AST.hasAntiPattern, _project_anti_pattern_item)
        if anti_items:
            doc["anti_patterns"] = anti_items

    note = _lit_str(graph.value(d, AST.architecturalNote))
    if note is not None:
        doc["architectural_notes"] = note

    for _idx, name, value in unknown:
        doc[name] = value

    return doc


# ----------------------------------------------------------------------------
# YAML (de)serialization (spec §1)
# ----------------------------------------------------------------------------


class _BlockStyleDumper(yaml.SafeDumper):
    """``yaml.SafeDumper`` with multi-line strings forced to ``|`` block style,
    so a projected ``mie.yaml`` reads the same way a hand-authored one does."""


def _represent_str(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_BlockStyleDumper.add_representer(str, _represent_str)


def dump_mie_yaml(document: Mapping[str, object]) -> str:
    """The one place a ``mie.yaml`` document becomes YAML text (spec §1)."""
    text = yaml.dump(
        dict(document),
        Dumper=_BlockStyleDumper,
        sort_keys=False,
        allow_unicode=True,
        width=10_000,
    )
    return text if text.endswith("\n") else text + "\n"


def project_mie_yaml(graph: rdflib.Graph, dataset_id: str) -> str:
    """``dump_mie_yaml(project_mie_document(graph, dataset_id))`` (spec §4)."""
    return dump_mie_yaml(project_mie_document(graph, dataset_id))


def parse_mie_yaml(text: str) -> dict[str, object]:
    """Load a ``mie.yaml`` document, rejecting anything that is not a mapping."""
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"mie.yaml must parse to a mapping, got {type(data).__name__}")
    return data


# The only three fields the builder actually writes as one bare triple per
# element with no per-item subject (spec §4/§5.1: schema_info.keywords ->
# dcat:keyword, schema_info.categories -> dcat:theme, schema_info.graphs ->
# ast:namedGraph). A rdflib.Graph is a *set* of triples, so for exactly these
# three, round-tripping is only lossless up to set (not multiset) equality.
# Every other all-string list (sparql_query_examples / anti_patterns /
# sample_rdf_entries bare-string items, and any unknown section) gets a
# distinct per-item subject and a strict ast:index, so normalize_document
# must NOT erase its order or duplicates - doing so would hide a build/project
# bug (a dropped repeat, a reordering) behind a passing round-trip test.
_SET_LIKE_LIST_PATHS = frozenset(
    {
        ("schema_info", "keywords"),
        ("schema_info", "categories"),
        ("schema_info", "graphs"),
    }
)


def normalize_document(document: object, path: tuple[str, ...] = ()) -> object:
    """The equivalence relation used to compare a ``mie.yaml`` document before
    and after a build→project round trip (spec §3.7 / §5).

    Recursively drops any dict key whose (normalized) value is ``None``/``""``/
    ``[]``/``{}`` — the same rule :func:`build_metadata_graph` applies when
    deciding what becomes a triple — and, ONLY for the three set-shaped paths
    named in :data:`_SET_LIKE_LIST_PATHS`, sorts+dedupes an all-string list
    (see that constant's docstring for why those three, and only those three,
    are order/duplicate-insensitive). ``path`` is the sequence of dict keys
    used to reach the value being normalized; list elements keep their parent
    list's path since a list index is not a document key.
    """
    if isinstance(document, Mapping):
        result: dict[object, object] = {}
        for key, value in document.items():
            nv = normalize_document(value, (*path, key) if isinstance(key, str) else path)
            if _is_droppable(nv):
                continue
            result[key] = nv
        return result
    if isinstance(document, list):
        items = [normalize_document(v, path) for v in document]
        items = [v for v in items if not _is_droppable(v)]
        if path in _SET_LIKE_LIST_PATHS and items and all(isinstance(v, str) for v in items):
            return sorted(set(items))
        return items
    return document


# ----------------------------------------------------------------------------
# Turtle (de)serialization + store I/O (spec §1)
# ----------------------------------------------------------------------------


def metadata_turtle(graph: rdflib.Graph) -> str:
    """Serialize a description graph as Turtle (prefixes bound by the builder)."""
    return graph.serialize(format="turtle")


def graph_from_turtle(text: str) -> rdflib.Graph:
    """Parse Turtle text (e.g. from :func:`fetch_metadata_graph`) back into a graph."""
    graph = rdflib.Graph()
    _bind(graph)
    if text.strip():
        graph.parse(data=text, format="turtle")
    return graph


async def fetch_metadata_graph(client: SupportsMetadataStore, dataset_id: str) -> rdflib.Graph:
    """Read a dataset's description graph out of the store (empty graph if none)."""
    iri = substrate.meta_graph_iri(dataset_id)
    query = f"CONSTRUCT {{ ?s ?p ?o }} WHERE {{ GRAPH <{iri}> {{ ?s ?p ?o }} }}"
    turtle = await client.sparql_construct(query)
    return graph_from_turtle(turtle)


class MetadataGraphWriteError(RuntimeError):
    """The POST half of :func:`write_metadata_graph` failed *after* its DROP
    already succeeded: ``graph_iri`` is now empty in the store (neither the
    old nor the new description survives), not merely unreachable/unchanged.
    Distinct from a plain exception out of the DROP itself (store down,
    non-2xx, ...) — there the graph is untouched and a retry is just a retry,
    not a repair of a now-empty graph. Callers (the migrate CLI) use this to
    tell an operator which case they are in."""

    def __init__(self, dataset_id: str, graph_iri: str, cause: BaseException) -> None:
        super().__init__(
            f"{dataset_id}: POST failed after DROP <{graph_iri}> already succeeded "
            f"— that graph is now empty in the store ({cause})"
        )
        self.dataset_id = dataset_id
        self.graph_iri = graph_iri


async def write_metadata_graph(
    client: SupportsMetadataStore, dataset_id: str, graph: rdflib.Graph
) -> int:
    """Replace a dataset's description graph in the store (DROP then load).

    Replace, not append (ADR §4: "追記ではなく置き換え") — a description is the
    current say-so, not a history. Returns the number of triples written.

    DROP and POST are two separate store requests, not one transaction: if the
    POST fails after the DROP already went through, ``graph_iri`` is left
    empty in the store rather than holding the old or the new description.
    That case is re-raised as :class:`MetadataGraphWriteError` so a caller can
    say so, rather than as whatever bare exception ``post_turtle_bytes`` threw
    (indistinguishable, on its own, from "the DROP never happened").
    """
    iri = substrate.meta_graph_iri(dataset_id)
    await substrate.drop_graph(client, iri)
    payload = metadata_turtle(graph).encode("utf-8")
    try:
        await client.post_turtle_bytes(payload, graph_iri=iri)
    except Exception as exc:
        raise MetadataGraphWriteError(dataset_id, iri, exc) from exc
    return len(graph)
