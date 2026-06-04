"""Project a dataset's TBox (rdf-config ``model.yaml``) into RDFS/OWL triples
for the ontology named graph (#20 §2 / P3 step5).

The design triangle (Mermaid / rdf-config model.yaml / MIE / ingester) keeps the
TBox as **content**; this module *additionally* projects it into a per-dataset
ontology graph (``…/asterism/graph/ontology/{id}``) at promote time, so Ask can
enrich answers with human-readable labels and domain/range. The projection is
**additive and best-effort**: Ask still works from ABox introspection alone
(``schema_summary``) when no ontology graph exists — the TBox graph is enrichment,
never a dependency (ADR ``ontology-canonical-lifecycle.md`` §2).

What we project from ``model.yaml`` (rdf-config's example-driven, flat list of
subjects):
- each subject's class (its ``a:`` token) -> ``rdfs:Class`` + ``rdfs:label``;
- each subject predicate -> ``rdf:Property`` + ``rdfs:label``;
- ``rdfs:domain`` only when a predicate is used by exactly ONE class (multiple
  domains would mean an *intersection* in RDFS — wrong — so we omit it);
- ``rdfs:range`` only when a predicate's object is consistently a single declared
  class (a class reference, not a literal).

Prefix resolution: ``sd:`` / ``sdr:`` (and any others) come from the bundle's own
``@prefix`` / ``PREFIX`` declarations (RML / MIE), unioned with a standard
well-known map. A term whose prefix cannot be resolved is skipped (graceful) so a
projection never fails a promote.

No generated code runs: this is pure parsing + triple construction.
"""
from __future__ import annotations

import re
from typing import Any

import rdflib
import yaml

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
    """Project an rdf-config ``model.yaml`` into an RDFS/OWL :class:`rdflib.Graph`.

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
