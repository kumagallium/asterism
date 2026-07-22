"""ir2mermaid: the reviewer-facing diagram compiled from the Mapping IR.

Motivating incident (ZEM dogfood, 2026-07): the LLM's §1 sketch rendered ONE
empty class box named after a measured column (``Temperature``) with all eight
predicates orphaned outside the diagram — the reviewer had nothing to check
the design against. These tests pin the deterministic replacement: boxes carry
their properties (with units), IRI links become edges, and the emitted text
stays inside the T5 lint's safe grammar and Mermaid 11's real parser.
"""

from __future__ import annotations

import pytest

pytest.importorskip("yaml")

from asterism_step0.ir2mermaid import (
    build_graph_from_ir,
    property_table_md,
    render_dataset_doc,
)
from asterism_step0.mapping_ir import parse_mapping_ir
from asterism_step0.ttl2mermaid import render_mermaid_body
from asterism_step0.validate import _lint_classdiagram

ZEM_LIKE = """\
version: 1
prefixes:
  ast: "https://example.org/zem/"
  sd: "https://example.org/zem/vocab/"
maps:
  - name: measurement
    source: analysis.csv
    subject:
      template: "ast:m/{Measurement temp.(C)}"
      classes: [sd:Measurement]
    properties:
      - predicate: sd:temperature
        column: "Measurement temp.(C)"
        datatype: xsd:double
        unit: "°C"
        label: 測定温度
      - predicate: sd:resistivity
        column: "Resistivity(Ohm m)"
        datatype: xsd:double
        unit: "Ohm m"
      - predicate: sd:darkEMF
        column: "Dark EMF(V)"
        datatype: xsd:double
        unit: V
"""

LINKED = """\
version: 1
prefixes:
  ast: "https://example.org/zem/"
  sd: "https://example.org/zem/vocab/"
maps:
  - name: sample
    source: analysis.csv
    subject:
      template: "ast:sample/{Sample name}"
      classes: [sd:Sample]
    properties:
      - predicate: sd:name
        column: "Sample name"
        datatype: xsd:string
  - name: measurement
    source: analysis.csv
    subject:
      template: "ast:sample/{Sample name}/m/{T}"
      classes: [sd:Measurement]
    properties:
      - predicate: sd:sample
        object_template: "ast:sample/{Sample name}"
      - predicate: sd:seeAlso
        object_template: "https://elsewhere.example/{T}"
      - predicate: sd:temperature
        column: T
        datatype: xsd:double
"""


def test_zem_shape_attributes_live_inside_the_box() -> None:
    """The incident shape: every predicate is a member line, in IR order."""
    body = render_mermaid_body(build_graph_from_ir(parse_mapping_ir(ZEM_LIKE)))
    assert "class Measurement {" in body
    i_t = body.index("+temperature xsd_double [°C]")
    i_r = body.index("+resistivity xsd_double [Ohm m]")
    i_d = body.index("+darkEMF xsd_double [V]")
    assert i_t < i_r < i_d  # column order, not alphabetical
    # nothing orphaned outside a box: no relation lines in a single-map design
    assert "-->" not in body


def test_iri_link_between_maps_becomes_an_edge() -> None:
    graph = build_graph_from_ir(parse_mapping_ir(LINKED))
    rels = [(r.domain_label, r.range_label, r.property_label) for r in graph.relations]
    assert rels == [("Measurement", "Sample", "sample")]
    # the external (unmatched-template) IRI renders as a member, not an edge
    measurement = next(c for c in graph.classes if c.label == "Measurement")
    assert ("seeAlso", "IRI") in measurement.datatype_properties
    # the resolved link does NOT additionally appear as a member
    assert all(p != "sample" for p, _ in measurement.datatype_properties)


def test_repeated_class_across_maps_merges_and_dedups() -> None:
    ir = parse_mapping_ir(
        """\
version: 1
prefixes:
  sd: "https://example.org/v/"
  r: "https://example.org/r/"
maps:
  - name: a
    source: a.csv
    subject: {template: "r:x/{id}", classes: [sd:Thing]}
    properties:
      - {predicate: sd:name, column: n1}
      - {predicate: sd:size, column: s, datatype: xsd:int}
  - name: b
    source: b.csv
    subject: {template: "r:y/{id}", classes: [sd:Thing]}
    properties:
      - {predicate: sd:name, column: n2}
      - {predicate: sd:mass, column: m, datatype: xsd:double}
"""
    )
    graph = build_graph_from_ir(ir)
    assert [c.label for c in graph.classes] == ["Thing"]
    names = [p for p, _ in graph.classes[0].datatype_properties]
    assert names == ["name", "size", "mass"]  # merged, deduped, order kept


def test_label_collision_falls_back_to_prefixed_label() -> None:
    ir = parse_mapping_ir(
        """\
version: 1
prefixes:
  a: "https://a.example/v/"
  b: "https://b.example/v/"
  r: "https://example.org/r/"
maps:
  - name: one
    source: a.csv
    subject: {template: "r:a/{id}", classes: [a:Thing]}
    properties: [{predicate: a:p, column: c}]
  - name: two
    source: b.csv
    subject: {template: "r:b/{id}", classes: [b:Thing]}
    properties: [{predicate: b:p, column: c}]
"""
    )
    labels = [c.label for c in build_graph_from_ir(ir).classes]
    assert labels == ["Thing", "b_Thing"]


def test_mermaid_unsafe_unit_omitted_from_diagram_kept_in_table() -> None:
    ir = parse_mapping_ir(
        """\
version: 1
prefixes:
  sd: "https://example.org/v/"
  r: "https://example.org/r/"
maps:
  - name: k
    source: k.csv
    subject: {template: "r:k/{id}", classes: [sd:K]}
    properties:
      - {predicate: sd:kappa, column: k, datatype: xsd:double, unit: "W/(m·K)"}
"""
    )
    body = render_mermaid_body(build_graph_from_ir(ir))
    assert "+kappa xsd_double" in body
    assert "(" not in body.split("class K {")[1].split("}")[0]  # no method-flip
    table = property_table_md(ir)
    assert "W/(m·K)" in table  # verbatim in the provenance table


def test_boxless_map_never_crashes_and_link_falls_back_to_member() -> None:
    ir = parse_mapping_ir(
        """\
version: 1
prefixes:
  sd: "https://example.org/v/"
  r: "https://example.org/r/"
maps:
  - name: bare
    source: a.csv
    subject: {template: "r:bare/{id}"}
    properties: [{predicate: sd:p, column: c}]
  - name: main
    source: a.csv
    subject: {template: "r:main/{id}", classes: [sd:Main]}
    properties:
      - {predicate: sd:toBare, object_template: "r:bare/{id}"}
"""
    )
    graph = build_graph_from_ir(ir)
    assert [c.label for c in graph.classes] == ["Main"]
    assert graph.relations == []  # no box to point at
    assert ("toBare", "IRI") in graph.classes[0].datatype_properties


def test_generated_body_passes_the_t5_lint() -> None:
    """The compiled diagram must live inside validate's safe grammar."""
    for spec in (ZEM_LIKE, LINKED):
        body = render_mermaid_body(build_graph_from_ir(parse_mapping_ir(spec)))
        assert _lint_classdiagram(body) == []


def test_property_table_lists_source_columns_units_and_meaning() -> None:
    table = property_table_md(parse_mapping_ir(ZEM_LIKE))
    assert "| Class | Property | Source | Type | Unit | Meaning |" in table
    assert "column `Measurement temp.(C)`" in table
    assert "°C" in table
    assert "測定温度" in table


def test_dataset_doc_contains_fenced_block_then_table() -> None:
    doc = render_dataset_doc(parse_mapping_ir(ZEM_LIKE), dataset_name="zem")
    assert doc.startswith("# zem ontology — class diagram\n")
    fence = doc.index("```mermaid\n")
    assert doc.index("## Properties") > fence
    # the fenced block closes before the table (UI extracts only the fence)
    assert doc.index("```\n", fence + 1) < doc.index("## Properties")
