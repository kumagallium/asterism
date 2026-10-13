"""Zero-row sources: a mapping's source that has no rows to read this run.

A per-kind export (one JSON file per kind of record) routinely has a kind with
nothing in it. That kind should yield 0 triples and every other kind should
ingest as usual — but a zero-row source used to stop the whole ingest. Decision
of record: ``docs/architecture/native-json-denormalization.md`` §10.

* A JSON source whose top level is ``[]`` has 0 rows. No mark is needed: the
  file is there and says so.
* An ABSENT source file stays a design error (a misspelt or forgotten file must
  not silently become 0 rows) — unless its logical source carries
  ``ast:sourceOptional true``, compiled from ``optional: true`` on the Mapping IR
  map. Then absence means 0 rows.

A zero-row source's TriplesMaps are removed from the mapping before anything
reads the source, so no column-less table is ever written and Morph-KGC never
sniffs an empty file. A join (``rr:parentTriplesMap``) against a removed map is
removed with it — a join with a 0-row parent yields nothing either way. This is
a fixed edit of the declarative mapping (the same trust class as stripping the
``ast:`` dialect annotations); no code is generated or run.
"""
from __future__ import annotations

import logging
from pathlib import Path

from asterism.tabularize import is_empty_json_array

logger = logging.getLogger(__name__)

# The asterism vocab namespace the ``ast:`` logical-source annotations live in
# (same IRI as ``asterism.dialect.ASTERISM_NS`` and the IR compiler's).
ASTERISM_NS: str = "https://kumagallium.github.io/asterism/vocab#"

# On ``rml:logicalSource``: "this source file may be absent; absent = 0 rows".
# Data-only, like the dialect annotations; stripped before Morph-KGC runs.
SOURCE_OPTIONAL_PREDICATE: str = ASTERISM_NS + "sourceOptional"

_R2RML = "http://www.w3.org/ns/r2rml#"
_RML_NEW = "http://w3id.org/rml/"
_RML_OLD = "http://semweb.mmlab.be/ns/rml#"
_LOGICAL_SOURCE_PREDS = (_RML_NEW + "logicalSource", _RML_OLD + "logicalSource")
_SOURCE_PREDS = (_RML_NEW + "source", _RML_OLD + "source")
_PARENT_TRIPLES_MAP_PREDS = (_R2RML + "parentTriplesMap", _RML_NEW + "parentTriplesMap")
_PREDICATE_OBJECT_MAP_PREDS = (_R2RML + "predicateObjectMap", _RML_NEW + "predicateObjectMap")
_OBJECT_MAP_PREDS = (_R2RML + "objectMap", _RML_NEW + "objectMap")
_OBJECT_PREDS = (_R2RML + "object", _RML_NEW + "object")
_SUBJECT_MAP_PREDS = (_R2RML + "subjectMap", _RML_NEW + "subjectMap")
_CLASS_PREDS = (_R2RML + "class", _RML_NEW + "class")

# Same sibling order the substrate tabularizes with (``tabularize_json_sources``).
_JSON_SUFFIXES = (".json", ".geojson")


def is_optional_logical_source(graph, logical_source) -> bool:
    """Whether ``logical_source`` carries ``ast:sourceOptional true``.

    Only a boolean ``true`` counts. Any other value leaves the source required,
    so an absent file is still reported (loudly) by the design check.
    """
    import rdflib

    for value in graph.objects(logical_source, rdflib.URIRef(SOURCE_OPTIONAL_PREDICATE)):
        if isinstance(value, rdflib.Literal) and value.toPython() is True:
            return True
    return False


def zero_row_reason(source: str, csv_dir: Path | str, *, optional: bool) -> str | None:
    """Why ``source`` (an ``rml:source`` value) has no rows, or ``None``.

    ``"empty"`` — the JSON behind it is ``[]`` (the source itself, or the JSON a
    ``<stem>.csv`` reference is tabularized from). ``"missing"`` — no file (nor a
    JSON sibling) is there and the source is marked optional. An absolute path is
    left alone (sources are confined and relative before the substrate prepares
    them).
    """
    raw = source.strip()
    if not raw or Path(raw).is_absolute():
        return None
    path = Path(csv_dir) / raw
    if path.exists():
        if path.suffix.lower() in _JSON_SUFFIXES and is_empty_json_array(path):
            return "empty"
        return None
    if path.suffix.lower() == ".csv":
        for suffix in _JSON_SUFFIXES:
            sibling = path.with_suffix(suffix)
            if sibling.exists():
                return "empty" if is_empty_json_array(sibling) else None
    return "missing" if optional else None


def _triples_maps(graph):
    """Every subject with a logical source (a TriplesMap), de-duplicated, in a
    stable order."""
    import rdflib

    seen: list = []
    for pred in _LOGICAL_SOURCE_PREDS:
        for tm in graph.subjects(rdflib.URIRef(pred), None):
            if tm not in seen:
                seen.append(tm)
    return seen


def _remove_subtree(graph, root) -> None:
    """Remove ``root``'s triples and every blank node only it reached."""
    import rdflib

    stack = [root]
    while stack:
        node = stack.pop()
        children = [o for o in graph.objects(node, None) if isinstance(o, rdflib.BNode)]
        graph.remove((node, None, None))
        for child in children:
            if next(graph.subjects(None, child), None) is None:
                stack.append(child)


def _remove_joins_to(graph, parent) -> list:
    """Remove every object map that joins ``parent`` (``rr:parentTriplesMap``),
    and the predicate-object map left with no object at all. Returns the
    TriplesMaps that lost a predicate-object map this way."""
    import rdflib

    uri = rdflib.URIRef
    lost: list = []
    for ptm_pred in _PARENT_TRIPLES_MAP_PREDS:
        for om in list(graph.subjects(uri(ptm_pred), parent)):
            for om_pred in _OBJECT_MAP_PREDS:
                for pom in list(graph.subjects(uri(om_pred), om)):
                    graph.remove((pom, uri(om_pred), om))
                    has_object = any(
                        next(graph.objects(pom, uri(p)), None) is not None
                        for p in _OBJECT_MAP_PREDS + _OBJECT_PREDS
                    )
                    if not has_object:
                        for pom_pred in _PREDICATE_OBJECT_MAP_PREDS:
                            for owner in list(graph.subjects(uri(pom_pred), pom)):
                                graph.remove((owner, uri(pom_pred), pom))
                                lost.append(owner)
                        _remove_subtree(graph, pom)
            _remove_subtree(graph, om)
    return lost


def _emits_nothing(graph, tm) -> bool:
    """A TriplesMap with no predicate-object map and no class emits no triple
    (and Morph-KGC fails on it rather than yielding 0)."""
    import rdflib

    uri = rdflib.URIRef
    if any(next(graph.objects(tm, uri(p)), None) is not None for p in _PREDICATE_OBJECT_MAP_PREDS):
        return False
    return not any(
        next(graph.objects(sm, uri(c)), None) is not None
        for sm_pred in _SUBJECT_MAP_PREDS
        for sm in graph.objects(tm, uri(sm_pred))
        for c in _CLASS_PREDS
    )


def _drop_triples_maps(graph, tms: list) -> None:
    """Remove ``tms`` with their joins and logical sources — a NAMED logical source
    too, once nothing else reads it (left behind, its ``rml:source`` would still be
    picked up downstream). A map whose only output was a join to a removed map is
    removed in turn: it now emits nothing."""
    import rdflib

    uri = rdflib.URIRef
    dropped: list = []
    queue = list(tms)
    while queue:
        tm = queue.pop(0)
        if tm in dropped:
            continue
        dropped.append(tm)
        lost = _remove_joins_to(graph, tm)
        sources = [ls for p in _LOGICAL_SOURCE_PREDS for ls in graph.objects(tm, uri(p))]
        _remove_subtree(graph, tm)
        for ls in sources:
            if next(graph.subjects(None, ls), None) is None:
                _remove_subtree(graph, ls)
        for owner in lost:
            if owner not in dropped and owner in _triples_maps(graph) and _emits_nothing(
                graph, owner
            ):
                logger.info("%s emits nothing without its join to %s; skipping it too", owner, tm)
                queue.append(owner)


def prune_empty_sources(rml_ttl: str, csv_dir: Path | str) -> str | None:
    """Remove the TriplesMaps whose source has no rows this run.

    Returns the mapping without them (and without any ``ast:sourceOptional``
    annotation), or ``None`` when no TriplesMap is left — there is nothing to
    materialize, and the caller returns 0 triples without invoking Morph-KGC. A
    mapping with no optional mark and no ``[]`` source is returned
    **byte-identical**. Unparseable RML is returned unchanged
    (:func:`asterism.rml_safety.assert_rml_safe` owns that rejection).
    """
    import rdflib

    graph = rdflib.Graph()
    try:
        graph.parse(data=rml_ttl, format="turtle")
    except Exception:
        return rml_ttl
    uri = rdflib.URIRef
    marked = next(graph.subjects(uri(SOURCE_OPTIONAL_PREDICATE), None), None) is not None
    pruned: list = []
    for tm in _triples_maps(graph):
        for ls_pred in _LOGICAL_SOURCE_PREDS:
            for ls in graph.objects(tm, uri(ls_pred)):
                optional = is_optional_logical_source(graph, ls)
                for src_pred in _SOURCE_PREDS:
                    for src in graph.objects(ls, uri(src_pred)):
                        reason = zero_row_reason(str(src), csv_dir, optional=optional)
                        if reason is not None and tm not in pruned:
                            pruned.append(tm)
                            logger.info(
                                "source %r has no rows this run (%s); skipping %s",
                                str(src),
                                reason,
                                tm,
                            )
    if not pruned and not marked:
        return rml_ttl
    _drop_triples_maps(graph, pruned)
    graph.remove((None, uri(SOURCE_OPTIONAL_PREDICATE), None))
    if not _triples_maps(graph):
        return None
    return graph.serialize(format="turtle")
