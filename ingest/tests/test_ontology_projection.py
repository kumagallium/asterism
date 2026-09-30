"""Tests for the #20 step5 TBox projector (asterism.ontology_projection)."""

from __future__ import annotations

import rdflib

from asterism.ontology_projection import (
    STANDARD_PREFIXES,
    extract_prefixes,
    model_yaml_class_labels,
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


def test_mapping_ir_conflicting_labels_for_one_predicate_fall_back_to_local_name() -> None:
    """One term, one label. Every value catalog carries its value as
    ``rdfs:label`` (step0 ``ensure_value_catalog_labels``), so several kinds
    author different words for the SAME predicate — and the first map's word
    is not the term's (2026-09-02: Ask called every kind's ID 「DOI」).
    Agreement still yields the word; disagreement the local-name fallback."""
    ir = f"""
prefixes:
  xrd: {SD}
maps:
- name: doi
  source: x.csv
  subject:
    template: xrd:doi/{{doi}}
    classes: [xrd:Doi]
  properties:
  - column: doi
    predicate: xrd:name
    label: DOI
  - column: doi
    predicate: xrd:key
    label: 鍵
- name: unit
  source: x.csv
  subject:
    template: xrd:unit/{{unit}}
    classes: [xrd:Unit]
  properties:
  - column: unit
    predicate: xrd:name
    label: 縦軸単位
  - column: unit
    predicate: xrd:key
    label: 鍵
"""
    g = project_mapping_ir(ir, STANDARD_PREFIXES)
    name = rdflib.URIRef(SD + "name")
    assert set(g.objects(name, RDFS.label)) == {rdflib.Literal("name")}
    key = rdflib.URIRef(SD + "key")
    assert set(g.objects(key, RDFS.label)) == {rdflib.Literal("鍵")}


def test_mapping_ir_subject_label_wins_over_the_curie_local_name() -> None:
    """契約メモ a・R3: 種類の rdfs:label は subject.label を先に見る。"""
    ir = f"""
prefixes:
  xrd: {SD}
maps:
- name: thing
  source: x.csv
  subject:
    template: xrd:x/{{a}}
    classes: [xrd:Thing]
    label: "試料"
  properties:
  - column: a
    predicate: xrd:name
"""
    g = project_mapping_ir(ir, STANDARD_PREFIXES)
    cls = rdflib.URIRef(SD + "Thing")
    assert (cls, RDFS.label, rdflib.Literal("試料")) in g
    assert (cls, RDFS.label, rdflib.Literal("Thing")) not in g


def test_mapping_ir_subject_without_label_falls_back_to_the_local_name() -> None:
    """label の無い IR は今までどおり読める（不変条件）。"""
    ir = f"""
prefixes:
  xrd: {SD}
maps:
- name: thing
  source: x.csv
  subject:
    template: xrd:x/{{a}}
    classes: [xrd:Thing]
  properties:
  - column: a
    predicate: xrd:name
"""
    g = project_mapping_ir(ir, STANDARD_PREFIXES)
    cls = rdflib.URIRef(SD + "Thing")
    assert (cls, RDFS.label, rdflib.Literal("Thing")) in g


def test_mapping_ir_agreeing_subject_labels_across_maps_yield_the_word() -> None:
    """同じ種類を持つマップの表示名が一致すれば、それを使う。"""
    ir = f"""
prefixes:
  xrd: {SD}
maps:
- name: a
  source: a.csv
  subject:
    template: xrd:a/{{id}}
    classes: [xrd:Shared]
    label: "試料"
  properties:
  - column: id
    predicate: xrd:name
- name: b
  source: b.csv
  subject:
    template: xrd:b/{{id}}
    classes: [xrd:Shared]
    label: "試料"
  properties:
  - column: id
    predicate: xrd:key
"""
    g = project_mapping_ir(ir, STANDARD_PREFIXES)
    cls = rdflib.URIRef(SD + "Shared")
    assert set(g.objects(cls, RDFS.label)) == {rdflib.Literal("試料")}


def test_mapping_ir_conflicting_subject_labels_first_one_wins() -> None:
    """食い違えば最初の label（api・図と同じ規則。符号つきの名前を人に見せない）。"""
    ir = f"""
prefixes:
  xrd: {SD}
maps:
- name: a
  source: a.csv
  subject:
    template: xrd:a/{{id}}
    classes: [xrd:Shared]
    label: "試料"
  properties:
  - column: id
    predicate: xrd:name
- name: b
  source: b.csv
  subject:
    template: xrd:b/{{id}}
    classes: [xrd:Shared]
    label: "サンプル"
  properties:
  - column: id
    predicate: xrd:key
"""
    g = project_mapping_ir(ir, STANDARD_PREFIXES)
    cls = rdflib.URIRef(SD + "Shared")
    assert set(g.objects(cls, RDFS.label)) == {rdflib.Literal("試料")}


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


_MODEL_CLASS_LABELS = """
classes:
  sd:Sample:
    label: " 試料 "
  sd:Curve:
    description: "no label here"
properties:
  sd:ofSample:
    domain: sd:Curve
    range: sd:Sample
"""


def test_mapping_form_class_label_from_model_yaml_else_local_name() -> None:
    g = project_model_yaml(_MODEL_CLASS_LABELS, _PREFIXES)
    assert (rdflib.URIRef(SD + "Sample"), RDFS.label, rdflib.Literal("試料")) in g
    assert (rdflib.URIRef(SD + "Curve"), RDFS.label, rdflib.Literal("Curve")) in g
    # 項目はローカル名のまま
    assert (rdflib.URIRef(SD + "ofSample"), RDFS.label, rdflib.Literal("ofSample")) in g


_IR_FOR_CLASS_LABELS = f"""
version: 1
prefixes:
  ir: {SD}ir#
maps:
- name: a
  subject:
    template: "x/{{id}}"
    classes: [sd:Sample]
    label: "IR の名前"
  properties:
  - predicate: sd:p
    column: c
- name: b
  subject:
    template: "y/{{id}}"
    classes: [sd:Curve, ir:Other]
  properties:
  - predicate: sd:q
    column: c
"""


def test_project_mapping_ir_class_labels_order_ir_then_model_then_local() -> None:
    labels = {
        "sd:Sample": "model の名前",  # IR の subject.label が勝つ
        "<" + SD + "Curve>": "曲線",  # IR に無い -> model の名前（IRI 形も可）
        "nope:Ghost": "無視される",  # 解決できないキーは捨てる
    }
    g = project_mapping_ir(_IR_FOR_CLASS_LABELS, _PREFIXES, class_labels=labels)
    assert (rdflib.URIRef(SD + "Sample"), RDFS.label, rdflib.Literal("IR の名前")) in g
    assert (rdflib.URIRef(SD + "Curve"), RDFS.label, rdflib.Literal("曲線")) in g
    # どちらにも無い種類はローカル名
    assert (rdflib.URIRef(SD + "ir#Other"), RDFS.label, rdflib.Literal("Other")) in g
    assert not any("Ghost" in str(s) for s in g.subjects())


def test_model_yaml_class_labels_empty_for_list_form_and_broken_yaml() -> None:
    assert model_yaml_class_labels(_MODEL) == {}
    assert model_yaml_class_labels("classes: [unclosed") == {}
    assert model_yaml_class_labels("classes: [a, b]") == {}
    assert model_yaml_class_labels("") == {}
    assert model_yaml_class_labels(_MODEL_CLASS_LABELS) == {"sd:Sample": "試料"}
