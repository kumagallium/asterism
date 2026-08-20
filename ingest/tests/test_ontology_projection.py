"""Tests for the #20 step5 TBox projector (asterism.ontology_projection)."""
from __future__ import annotations

import rdflib

from asterism.ontology_projection import (
    STANDARD_PREFIXES,
    extract_prefixes,
    project_mapping_ir,
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


# --- legacy model.yaml, mapping-form (classes:/properties:) ----------------
# Verbatim shape of a real kantan-mode bundle's model.yaml
# (xrd-781e7d77, promoted 2026-08 — audited during the "かんたん" label fix).
# Its own top-level `prefixes:` block is display-only content in this shape
# (not consumed by the projector, same as the rdf-config list form); the real
# `_project_ontology_graph` resolves `xrd:`/`xrdr:` from the bundle's RML/MIE
# `@prefix` declarations instead, which is what `_PREFIXES` (sd:/sdr:) stands
# in for here.

_MODEL_MAPPING_FORM = f"""
prefixes:
  xrd: {SD}
  xrdr: {SDR}
  schema: http://schema.org/
  dcterms: http://purl.org/dc/terms/
  prov: http://www.w3.org/ns/prov#
  qb: http://purl.org/linked-data/cube#
  xsd: http://www.w3.org/2001/XMLSchema#

classes:
  xrd:試料:
    description: "XRD measurement sample"
    key: dcterms:identifier
  xrd:ピーク値:
    description: "A single diffraction peak (2θ, intensity) belonging to a sample"

properties:
  dcterms:identifier:
    domain: xrd:試料
    range: xsd:anyURI
    functional: true
  schema:about:
    domain: xrd:ピーク値
    range: xrd:試料
    type: object
  xrd:2theta:
    domain: xrd:ピーク値
    range: xsd:double
    unit: "°"
    datatype: xsd:double
  xrd:intensity:
    domain: xrd:ピーク値
    range: xsd:double
    unit: "cps"
    datatype: xsd:double
"""


_XRD_PREFIXES = STANDARD_PREFIXES | {"xrd": SD, "xrdr": SDR}


def test_model_yaml_mapping_form_projects_classes_and_properties() -> None:
    g = project_model_yaml(_MODEL_MAPPING_FORM, _XRD_PREFIXES)
    sample = rdflib.URIRef(SD + "試料")
    assert (sample, rdflib.RDF.type, RDFS.Class) in g
    assert (sample, RDFS.label, rdflib.Literal("試料")) in g
    two_theta = rdflib.URIRef(SD + "2theta")
    assert (two_theta, rdflib.RDF.type, rdflib.URIRef(STANDARD_PREFIXES["rdf"] + "Property")) in g
    # no authored label in model.yaml -> local name fallback
    assert (two_theta, RDFS.label, rdflib.Literal("2theta")) in g
    assert (two_theta, RDFS.domain, rdflib.URIRef(SD + "ピーク値")) in g
    assert (two_theta, RDFS.range, rdflib.URIRef("http://www.w3.org/2001/XMLSchema#double")) in g


# --- Mapping IR (mapping.yaml, K8) ------------------------------------------
# Verbatim shape of the same real bundle's mapping.yaml.

_MAPPING_IR = f"""
version: 1
prefixes:
  xrd: {SD}
  xrdr: {SDR}
  schema: http://schema.org/
  dcterms: http://purl.org/dc/terms/
  prov: http://www.w3.org/ns/prov#
  qb: http://purl.org/linked-data/cube#
maps:
- name: sample
  source: xrd-664287b2.txt
  subject:
    template: xrdr:sample/{{preamble_1}}
    classes:
    - xrd:試料
  properties:
  - label: サンプル識別子
    object_template: {SDR}{{preamble_1}}
    object_type: iri
    predicate: dcterms:identifier
    unit: IRI
- name: peak
  source: xrd-664287b2.txt
  subject:
    template: xrdr:peak/{{preamble_1}}/{{2θ (deg)}}
    classes:
    - xrd:ピーク値
  properties:
  - label: サンプルへのリンク
    object_template: xrdr:sample/{{preamble_1}}
    object_type: iri
    predicate: schema:about
  - label: 2θ角度
    object_template: xrd:角度
    predicate: xrd:2theta
    unit: °
  - label: 強度
    object_template: xrd:強度
    predicate: xrd:intensity
    unit: cps
  - column: 2θ (deg)
    predicate: xrd:2theta
    unit: °
    datatype: xsd:double
  - column: 強度 (cps)
    predicate: xrd:intensity
    unit: cps
    datatype: xsd:double
dialects:
  xrd-664287b2.txt:
    encoding: cp932
    delimiter: "\\t"
    collapse: false
    skip_rows: 1
    preamble: lines
"""


def test_mapping_ir_uses_authored_label_not_local_name() -> None:
    g = project_mapping_ir(_MAPPING_IR, STANDARD_PREFIXES)
    two_theta = rdflib.URIRef(SD + "2theta")
    assert (two_theta, RDFS.label, rdflib.Literal("2θ角度")) in g
    # the machine identifier must NOT be what a reader is shown
    assert (two_theta, RDFS.label, rdflib.Literal("2theta")) not in g

    intensity = rdflib.URIRef(SD + "intensity")
    assert (intensity, RDFS.label, rdflib.Literal("強度")) in g


def test_mapping_ir_projects_class_labels() -> None:
    g = project_mapping_ir(_MAPPING_IR, STANDARD_PREFIXES)
    sample_cls = rdflib.URIRef(SD + "試料")
    assert (sample_cls, rdflib.RDF.type, RDFS.Class) in g
    assert (sample_cls, RDFS.label, rdflib.Literal("試料")) in g
    peak_cls = rdflib.URIRef(SD + "ピーク値")
    assert (peak_cls, rdflib.RDF.type, RDFS.Class) in g
    assert (peak_cls, RDFS.label, rdflib.Literal("ピーク値")) in g


def test_mapping_ir_property_without_label_falls_back_to_local_name() -> None:
    ir = f"""
prefixes:
  xrd: {SD}
maps:
- name: thing
  source: x.csv
  subject:
    template: xrd:x/{{a}}
    classes:
    - xrd:Thing
  properties:
  - column: a
    predicate: xrd:noLabelHere
"""
    g = project_mapping_ir(ir, STANDARD_PREFIXES)
    prop = rdflib.URIRef(SD + "noLabelHere")
    assert (prop, RDFS.label, rdflib.Literal("noLabelHere")) in g


def test_mapping_ir_domain_single_map_emitted_multi_map_omitted() -> None:
    g = project_mapping_ir(_MAPPING_IR, STANDARD_PREFIXES)
    # dcterms:identifier only appears in the "sample" map -> domain emitted
    ident = rdflib.URIRef("http://purl.org/dc/terms/identifier")
    assert (ident, RDFS.domain, rdflib.URIRef(SD + "試料")) in g

    ir_shared = f"""
prefixes:
  xrd: {SD}
maps:
- name: a
  source: x.csv
  subject:
    template: xrd:a/{{k}}
    classes:
    - xrd:A
  properties:
  - column: k
    predicate: schema:name
- name: b
  source: x.csv
  subject:
    template: xrd:b/{{k}}
    classes:
    - xrd:B
  properties:
  - column: k
    predicate: schema:name
"""
    g_shared = project_mapping_ir(ir_shared, STANDARD_PREFIXES)
    name = rdflib.URIRef("https://schema.org/name")
    assert list(g_shared.objects(name, RDFS.domain)) == []


def test_mapping_ir_empty_or_garbage_input_is_empty_graph() -> None:
    assert len(project_mapping_ir("", STANDARD_PREFIXES)) == 0
    assert len(project_mapping_ir(": : not yaml : :", STANDARD_PREFIXES)) == 0
    assert len(project_mapping_ir("maps: not-a-list", STANDARD_PREFIXES)) == 0
    assert len(project_mapping_ir("just: a string doc", STANDARD_PREFIXES)) == 0
