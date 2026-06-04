"""Tests for the #20 step5 TBox projector (asterism.ontology_projection)."""
from __future__ import annotations

import rdflib

from asterism.ontology_projection import (
    STANDARD_PREFIXES,
    extract_prefixes,
    project_model_yaml,
)

RDFS = rdflib.Namespace("http://www.w3.org/2000/01/rdf-schema#")
SD = "https://ex.org/onto#"
SDR = "https://ex.org/res/"
_PREFIXES = STANDARD_PREFIXES | {"sd": SD, "sdr": SDR}

# A small rdf-config model.yaml: Paper, Sample (-> Paper), Curve (-> Sample),
# with a predicate (schema:name) shared across two classes.
_MODEL = f"""
- Paper <{SDR}paper/1>:
    - a: sd:Paper
    - schema:name?:
        - title: "A paper"
- Sample <{SDR}sample/1>:
    - a: sd:Sample
    - schema:name?:
        - sname: "s"
    - sd:fromPaper:
        - sample_paper: Paper
- Curve <{SDR}curve/1>:
    - a: sd:Curve
    - sd:ofSample?:
        - curve_sample: Sample
    - sd:propertyY?:
        - property_y: "ZT"
    - weird:unresolved?:
        - x: "y"
"""


def test_extract_prefixes_from_ttl_and_sparql() -> None:
    ttl = "@prefix sd: <https://ex.org/onto#> .\n@prefix sdr: <https://ex.org/res/> ."
    sparql = "PREFIX schema: <https://schema.org/>\nSELECT * WHERE { ?s ?p ?o }"
    px = extract_prefixes(ttl, sparql)
    assert px["sd"] == "https://ex.org/onto#"
    assert px["sdr"] == "https://ex.org/res/"
    assert px["schema"] == "https://schema.org/"


def test_projects_classes_with_labels() -> None:
    g = project_model_yaml(_MODEL, _PREFIXES)
    for name in ("Paper", "Sample", "Curve"):
        cls = rdflib.URIRef(SD + name)
        assert (cls, rdflib.RDF.type, RDFS.Class) in g
        assert (cls, RDFS.label, rdflib.Literal(name)) in g


def test_projects_predicate_with_domain_and_range() -> None:
    g = project_model_yaml(_MODEL, _PREFIXES)
    from_paper = rdflib.URIRef(SD + "fromPaper")
    assert (from_paper, rdflib.RDF.type, rdflib.URIRef(STANDARD_PREFIXES["rdf"] + "Property")) in g
    # used by exactly one class (Sample) -> domain emitted
    assert (from_paper, RDFS.domain, rdflib.URIRef(SD + "Sample")) in g
    # object is a class reference (Paper) -> range emitted
    assert (from_paper, RDFS.range, rdflib.URIRef(SD + "Paper")) in g


def test_multi_domain_predicate_omits_domain() -> None:
    # schema:name is on Paper AND Sample -> ambiguous domain -> omit (no wrong
    # RDFS intersection), but it is still typed as a property with a label.
    g = project_model_yaml(_MODEL, _PREFIXES)
    name = rdflib.URIRef("https://schema.org/name")
    assert (name, rdflib.RDF.type, rdflib.URIRef(STANDARD_PREFIXES["rdf"] + "Property")) in g
    assert (name, RDFS.label, rdflib.Literal("name")) in g
    assert list(g.objects(name, RDFS.domain)) == []  # no domain emitted


def test_literal_object_yields_no_range() -> None:
    g = project_model_yaml(_MODEL, _PREFIXES)
    prop_y = rdflib.URIRef(SD + "propertyY")
    assert (prop_y, rdflib.RDF.type, rdflib.URIRef(STANDARD_PREFIXES["rdf"] + "Property")) in g
    assert list(g.objects(prop_y, RDFS.range)) == []  # "ZT" is a literal, not a class


def test_unresolvable_prefix_is_skipped() -> None:
    # `weird:` is not in the prefix map -> the predicate is silently dropped.
    g = project_model_yaml(_MODEL, _PREFIXES)
    assert not any("unresolved" in str(s) for s in g.subjects())


def test_empty_or_garbage_input_is_empty_graph() -> None:
    assert len(project_model_yaml("", _PREFIXES)) == 0
    assert len(project_model_yaml(": : not yaml : :", _PREFIXES)) == 0
    assert len(project_model_yaml("- just a string", _PREFIXES)) == 0
