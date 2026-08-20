"""Project a dataset's TBox into RDFS/OWL triples for the ontology named graph
(#20 §2 / P3 step5, kantan-mode label plumbing — task audit "かんたん" 2026-08).

The design triangle (Mermaid / rdf-config model.yaml / MIE / ingester) keeps the
TBox as **content**; this module *additionally* projects it into a per-dataset
ontology graph (``…/asterism/graph/ontology/{id}``) at promote time, so Ask can
enrich answers with human-readable labels and domain/range. The projection is
**additive and best-effort**: Ask still works from ABox introspection alone
(``schema_summary``) when no ontology graph exists — the TBox graph is enrichment,
never a dependency (ADR ``ontology-canonical-lifecycle.md`` §2).

Two independent inputs, tried in this order by the caller
(``api.main._project_ontology_graph``):

1. :func:`project_mapping_ir` — the reviewed ``mapping.yaml`` (Mapping IR, K8).
   This is the **first-choice source of ``rdfs:label``**: a kantan-mode reviewer
   confirms a property's meaning in their own language (e.g. ``label: 2θ角度``)
   and that word is a promise ("used for search and citation") that must reach
   the published RDF — not just the design-time ``/rules`` display. A property
   without an authored ``label`` falls back to its predicate's local name.
2. :func:`project_model_yaml` — the **legacy** rdf-config ``model.yaml`` TBox,
   kept for bundles that still carry one. Accepts BOTH shapes of ``model.yaml``
   that exist in the wild: rdf-config's example-driven flat list of subjects
   (``- Name <IRI>: …``), and the plain ``classes:``/``properties:`` mapping
   form kantan-mode design docs also use. Since this path never carries a
   human-authored label, ``rdfs:label`` is always the term's local name here.

What we project:
- each class -> ``rdfs:Class`` + ``rdfs:label``;
- each property -> ``rdf:Property`` + ``rdfs:label`` (authored label first,
  local name fallback);
- ``rdfs:domain`` only when a predicate is used by exactly ONE class (multiple
  domains would mean an *intersection* in RDFS — wrong — so we omit it);
- ``rdfs:range`` only when unambiguous (a single declared class reference for
  the rdf-config list form; the declared ``range:`` for the mapping form).

Units are deliberately NOT projected here. The only generalized unit→IRI path
in this codebase (``asterism.qudt.unit_iri``) is a curated synonym table keyed
by convention to a single dataset (starrydata) and meant for per-VALUE triples
via the ``qudt_unit`` Tier-0 function — not a generic "attach a unit to this
TBox property" convention. Inventing one here would be a new convention, out of
scope for a label-plumbing fix (flagged to the caller instead of guessed).

Prefix resolution: ``sd:`` / ``sdr:`` (and any others) come from the bundle's own
``@prefix`` / ``PREFIX`` declarations (RML / MIE) plus the Mapping IR's own
``prefixes:`` block, unioned with a standard well-known map. A term whose prefix
cannot be resolved is skipped (graceful) so a projection never fails a promote.

No generated code runs: this is pure parsing + triple construction.
"""
from __future__ import annotations

import logging
import re
from typing import Any

import rdflib
import yaml

logger = logging.getLogger(__name__)

RDFS = "http://www.w3.org/2000/01/rdf-schema#"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"

# Well-known prefixes we always understand (dataset-specific sd:/sdr: come from
# the bundle's own declarations and are merged on top of this).
STANDARD_PREFIXES: dict[str, str] = {
    "rdf": RDF,
    "rdfs": RDFS,
    "owl": "http://www.w3.org/2002/07/owl#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "schema": "https://schema.org/",
    "dcterms": "http://purl.org/dc/terms/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "prov": "http://www.w3.org/ns/prov#",
    "bibo": "http://purl.org/ontology/bibo/",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "foaf": "http://xmlns.com/foaf/0.1/",
    "qudt": "http://qudt.org/schema/qudt/",
}

# Turtle/SPARQL prefix declarations: `@prefix x: <iri> .` and `PREFIX x: <iri>`.
_TTL_PREFIX = re.compile(r"@prefix\s+([A-Za-z][\w.-]*):\s*<([^>]+)>\s*\.")
_SPARQL_PREFIX = re.compile(r"(?i)\bPREFIX\s+([A-Za-z][\w.-]*):\s*<([^>]+)>")

# A model.yaml predicate key may carry an rdf-config cardinality marker (? * +).
_CARDINALITY = re.compile(r"[?*+]\s*$")
# A prefixed name like `sd:fromPaper` (CURIE).
_CURIE = re.compile(r"^([A-Za-z][\w.-]*):(.+)$")


def extract_prefixes(*texts: str) -> dict[str, str]:
    """Collect ``prefix -> namespace`` from Turtle/SPARQL declarations in ``texts``.

    Used to learn a dataset's own ``sd:`` / ``sdr:`` (and any reused) namespaces
    from its bundle (the RML mapping + MIE shape expressions both declare them).
    """
    out: dict[str, str] = {}
    for text in texts:
        if not text:
            continue
        for pref, ns in _TTL_PREFIX.findall(text):
            out[pref] = ns
        for pref, ns in _SPARQL_PREFIX.findall(text):
            out[pref] = ns
    return out


def _resolve(token: Any, prefixes: dict[str, str]) -> str | None:
    """Resolve a model.yaml class/predicate token to a full IRI, or None.

    Handles ``<full-iri>``, a CURIE (``sd:Curve``) via ``prefixes``, and returns
    None for anything we cannot confidently resolve (skipped — never guessed).
    """
    if not isinstance(token, str):
        return None
    tok = token.strip()
    if tok.startswith("<") and tok.endswith(">"):
        return tok[1:-1]
    m = _CURIE.match(tok)
    if not m:
        return None
    ns = prefixes.get(m.group(1))
    return ns + m.group(2) if ns else None


def _subject_class_token(props: list[Any]) -> str | None:
    for prop in props:
        if isinstance(prop, dict) and "a" in prop:
            return prop["a"]
    return None


def _predicate_entries(props: list[Any]):
    """Yield ``(predicate_token, value_list)`` for each non-``a`` property."""
    for prop in props:
        if not isinstance(prop, dict):
            continue
        for key, val in prop.items():
            if key == "a":
                continue
            yield _CARDINALITY.sub("", str(key)).strip(), val


def _local_name(curie_or_iri: str) -> str:
    """Human-ish label: the part after the last ``:`` / ``/`` / ``#``."""
    for sep in (":", "/", "#"):
        if sep in curie_or_iri:
            curie_or_iri = curie_or_iri.rsplit(sep, 1)[-1]
    return curie_or_iri


def project_model_yaml(model_yaml_text: str, prefixes: dict[str, str]) -> rdflib.Graph:
    """Project a legacy ``model.yaml`` TBox into an RDFS/OWL :class:`rdflib.Graph`.

    Accepts either shape found in the wild: rdf-config's example-driven flat
    list of subjects (``- Name <IRI>: …``), or the plain ``classes:``/
    ``properties:`` mapping form kantan-mode design docs also produce. Neither
    shape carries a human-authored label, so ``rdfs:label`` here is always the
    term's local name — prefer :func:`project_mapping_ir` when a ``mapping.yaml``
    (Mapping IR) is available.

    ``prefixes`` should include the dataset's own ``sd:`` / ``sdr:`` (from its
    bundle) plus standard ones; pass ``STANDARD_PREFIXES | extract_prefixes(...)``.
    Returns an empty graph on unparseable / empty input (best-effort).
    """
    g = rdflib.Graph()
    g.bind("rdfs", rdflib.Namespace(RDFS))
    try:
        data = yaml.safe_load(model_yaml_text)
    except yaml.YAMLError:
        return g
    if isinstance(data, dict) and ("classes" in data or "properties" in data):
        return _project_model_yaml_mapping_form(data, prefixes)
    if not isinstance(data, list):
        return g

    rdfs_Class = rdflib.URIRef(RDFS + "Class")
    rdf_Property = rdflib.URIRef(RDF + "Property")
    rdfs_label = rdflib.URIRef(RDFS + "label")
    rdfs_domain = rdflib.URIRef(RDFS + "domain")
    rdfs_range = rdflib.URIRef(RDFS + "range")
    a = rdflib.RDF.type

    # Pass 1: map each subject's declared ClassName -> class IRI (for range refs).
    class_iri_by_name: dict[str, str] = {}
    subjects: list[tuple[str, str, list[Any]]] = []  # (ClassName, classIRI, props)
    for item in data:
        if not isinstance(item, dict) or len(item) != 1:
            continue
        header, props = next(iter(item.items()))
        if not isinstance(props, list):
            continue
        class_name = str(header).split("<", 1)[0].strip().split()[0] if header else ""
        class_iri = _resolve(_subject_class_token(props), prefixes)
        if not class_name or not class_iri:
            continue
        class_iri_by_name[class_name] = class_iri
        subjects.append((class_name, class_iri, props))

    # Pass 2: emit classes + collect per-predicate domains / ranges.
    pred_domains: dict[str, set[str]] = {}
    pred_ranges: dict[str, set[str]] = {}
    pred_label: dict[str, str] = {}
    for class_name, class_iri, props in subjects:
        cls = rdflib.URIRef(class_iri)
        g.add((cls, a, rdfs_Class))
        g.add((cls, rdfs_label, rdflib.Literal(class_name)))
        for pred_token, val in _predicate_entries(props):
            pred_iri = _resolve(pred_token, prefixes)
            if not pred_iri:
                continue  # unresolvable prefix -> skip gracefully
            pred_domains.setdefault(pred_iri, set()).add(class_iri)
            pred_label.setdefault(pred_iri, _local_name(pred_token))
            # Range: detect a single class reference in the value list.
            for entry in val if isinstance(val, list) else []:
                if not isinstance(entry, dict):
                    continue
                for ref in entry.values():
                    if isinstance(ref, str) and ref in class_iri_by_name:
                        pred_ranges.setdefault(pred_iri, set()).add(
                            class_iri_by_name[ref]
                        )

    # Pass 3: emit predicates (+ domain/range only when unambiguous).
    for pred_iri, domains in pred_domains.items():
        p = rdflib.URIRef(pred_iri)
        g.add((p, a, rdf_Property))
        g.add((p, rdfs_label, rdflib.Literal(pred_label[pred_iri])))
        if len(domains) == 1:
            g.add((p, rdfs_domain, rdflib.URIRef(next(iter(domains)))))
        ranges = pred_ranges.get(pred_iri, set())
        if len(ranges) == 1:
            g.add((p, rdfs_range, rdflib.URIRef(next(iter(ranges)))))

    return g


def _project_model_yaml_mapping_form(
    data: dict[str, Any], prefixes: dict[str, str]
) -> rdflib.Graph:
    """Project the ``classes:``/``properties:`` mapping shape of ``model.yaml``.

    Unlike the rdf-config list form, this shape declares ``domain:``/``range:``
    explicitly per property, so no usage-based ambiguity check is needed — both
    are emitted whenever they resolve. No human-authored label exists in this
    shape either, so ``rdfs:label`` is always the term's local name.
    """
    g = rdflib.Graph()
    g.bind("rdfs", rdflib.Namespace(RDFS))
    rdfs_Class = rdflib.URIRef(RDFS + "Class")
    rdf_Property = rdflib.URIRef(RDF + "Property")
    rdfs_label = rdflib.URIRef(RDFS + "label")
    rdfs_domain = rdflib.URIRef(RDFS + "domain")
    rdfs_range = rdflib.URIRef(RDFS + "range")
    a = rdflib.RDF.type

    classes = data.get("classes")
    if isinstance(classes, dict):
        for cls_token in classes:
            cls_iri = _resolve(cls_token, prefixes)
            if not cls_iri:
                continue
            cls = rdflib.URIRef(cls_iri)
            g.add((cls, a, rdfs_Class))
            g.add((cls, rdfs_label, rdflib.Literal(_local_name(str(cls_token)))))

    properties = data.get("properties")
    if isinstance(properties, dict):
        for pred_token, spec in properties.items():
            pred_iri = _resolve(pred_token, prefixes)
            if not pred_iri:
                continue
            p = rdflib.URIRef(pred_iri)
            g.add((p, a, rdf_Property))
            g.add((p, rdfs_label, rdflib.Literal(_local_name(str(pred_token)))))
            if not isinstance(spec, dict):
                continue
            domain_iri = _resolve(spec.get("domain"), prefixes)
            if domain_iri:
                g.add((p, rdfs_domain, rdflib.URIRef(domain_iri)))
            range_iri = _resolve(spec.get("range"), prefixes)
            if range_iri:
                g.add((p, rdfs_range, rdflib.URIRef(range_iri)))

    return g


def project_mapping_ir(mapping_ir_yaml: str, prefixes: dict[str, str]) -> rdflib.Graph:
    """Project a ``mapping.yaml`` (Mapping IR, K8) into an RDFS/OWL graph.

    This is the **first-choice** TBox source: it carries the reviewer's own
    confirmed ``label:`` per property — the word kantan-mode promised would be
    "used for search and citation" — which the legacy ``model.yaml`` shapes
    never have. A property without an authored ``label`` falls back to its
    predicate's local name (same fallback :func:`project_model_yaml` always
    uses), so every property still gets *some* readable label.

    Parses the IR's own YAML shape directly (``prefixes:`` / ``maps:`` /
    per-map ``subject.classes`` / ``properties[].{predicate,label}``) rather
    than importing ``asterism_step0.mapping_ir`` — this package (``ingest``)
    does not depend on the design-time ``step0`` package, and the projection
    must stay best-effort (never raise) regardless of how strict the IR's own
    validator is. Unknown/extra IR fields are ignored; a row without a
    resolvable ``predicate`` is skipped, never guessed.

    ``prefixes`` should be ``STANDARD_PREFIXES | extract_prefixes(...)`` (the
    bundle's RML/MIE declarations); the IR's own ``prefixes:`` block is merged
    on top (IR-declared prefixes win — they are what the IR's own CURIEs were
    written against).
    """
    g = rdflib.Graph()
    g.bind("rdfs", rdflib.Namespace(RDFS))
    try:
        data = yaml.safe_load(mapping_ir_yaml)
    except yaml.YAMLError:
        return g
    if not isinstance(data, dict):
        return g
    maps = data.get("maps")
    if not isinstance(maps, list):
        return g

    ir_prefixes = data.get("prefixes")
    all_prefixes = dict(prefixes)
    if isinstance(ir_prefixes, dict):
        all_prefixes.update(
            {str(k): str(v) for k, v in ir_prefixes.items() if isinstance(v, str)}
        )

    rdfs_Class = rdflib.URIRef(RDFS + "Class")
    rdf_Property = rdflib.URIRef(RDF + "Property")
    rdfs_label = rdflib.URIRef(RDFS + "label")
    rdfs_domain = rdflib.URIRef(RDFS + "domain")
    a = rdflib.RDF.type

    emitted_classes: set[str] = set()
    pred_domains: dict[str, set[str]] = {}
    pred_authored_label: dict[str, str] = {}
    pred_fallback_label: dict[str, str] = {}

    for tm in maps:
        if not isinstance(tm, dict):
            continue
        subject = tm.get("subject")
        class_tokens = []
        if isinstance(subject, dict):
            raw_classes = subject.get("classes")
            if isinstance(raw_classes, list):
                class_tokens = [c for c in raw_classes if isinstance(c, str)]

        class_iris: list[str] = []
        for cls_token in class_tokens:
            cls_iri = _resolve(cls_token, all_prefixes)
            if not cls_iri:
                continue
            class_iris.append(cls_iri)
            if cls_iri not in emitted_classes:
                emitted_classes.add(cls_iri)
                cls = rdflib.URIRef(cls_iri)
                g.add((cls, a, rdfs_Class))
                g.add((cls, rdfs_label, rdflib.Literal(_local_name(cls_token))))

        props = tm.get("properties")
        if not isinstance(props, list):
            continue
        for prop in props:
            if not isinstance(prop, dict):
                continue
            pred_token = prop.get("predicate")
            if not isinstance(pred_token, str):
                continue
            pred_iri = _resolve(pred_token, all_prefixes)
            if not pred_iri:
                continue
            for cls_iri in class_iris:
                pred_domains.setdefault(pred_iri, set()).add(cls_iri)
            label = prop.get("label")
            if isinstance(label, str) and label.strip():
                pred_authored_label.setdefault(pred_iri, label.strip())
            else:
                pred_fallback_label.setdefault(pred_iri, _local_name(pred_token))

    all_predicates = set(pred_authored_label) | set(pred_fallback_label) | set(pred_domains)
    for pred_iri in all_predicates:
        label = (
            pred_authored_label.get(pred_iri)
            or pred_fallback_label.get(pred_iri)
            or _local_name(pred_iri)
        )
        p = rdflib.URIRef(pred_iri)
        g.add((p, a, rdf_Property))
        g.add((p, rdfs_label, rdflib.Literal(label)))
        domains = pred_domains.get(pred_iri) or set()
        if len(domains) == 1:
            g.add((p, rdfs_domain, rdflib.URIRef(next(iter(domains)))))

    return g
