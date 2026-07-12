"""Tests for asterism.rml_summary — the human-readable ingest-rules projection."""
from __future__ import annotations

from asterism.rml_summary import summarize_rml

_PREFIXES = """\
@prefix rr:   <http://www.w3.org/ns/r2rml#> .
@prefix rml:  <http://semweb.mmlab.be/ns/rml#> .
@prefix ql:   <http://semweb.mmlab.be/ns/ql#> .
@prefix rmlf: <http://w3id.org/rml/> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
@prefix ex:   <https://example.org/onto#> .
@prefix fn:   <https://kumagallium.github.io/asterism/functions#> .
"""

_BASIC = _PREFIXES + """
<#SampleMap> a rr:TriplesMap ;
  rml:logicalSource [ rml:source "samples.csv" ; rml:referenceFormulation ql:CSV ] ;
  rr:subjectMap [
    rr:template "https://example.org/resource/sample/{sample_id}" ;
    rr:class ex:Sample
  ] ;
  rr:predicateObjectMap [ rr:predicate ex:sampleId ;
    rr:objectMap [ rml:reference "sample_id" ] ] ;
  rr:predicateObjectMap [ rr:predicate ex:mass ;
    rr:objectMap [ rml:reference "mass" ; rr:datatype xsd:double ] ] ;
  rr:predicateObjectMap [ rr:predicate ex:page ;
    rr:objectMap [ rml:reference "url" ; rr:termType rr:IRI ] ] ;
  rr:predicateObjectMap [ rr:predicate ex:origin ;
    rr:objectMap [ rr:constant "lab-A" ] ] ;
  rr:predicateObjectMap [ rr:predicate ex:license ;
    rr:objectMap [ rr:constant <https://example.org/license> ] ] .
"""


def test_basic_map_projection() -> None:
    out = summarize_rml(_BASIC)
    assert out["warnings"] == []
    assert len(out["maps"]) == 1
    m = out["maps"][0]
    assert m["id"] == "SampleMap"
    assert m["source"] == "samples.csv"
    assert m["formulation"] == "CSV"
    assert m["subject"]["kind"] == "template"
    assert m["subject"]["classes"] == ["ex:Sample"]
    assert "{sample_id}" in m["subject"]["template"]

    rows = {r["predicate"]: r for r in m["properties"]}
    assert rows["ex:sampleId"]["kind"] == "reference"
    assert rows["ex:sampleId"]["reference"] == "sample_id"
    assert rows["ex:mass"]["datatype"] == "xsd:double"
    assert rows["ex:page"]["term_type"] == "IRI"
    assert rows["ex:origin"]["kind"] == "constant"
    assert rows["ex:origin"]["constant"] == "lab-A"
    assert rows["ex:origin"]["constant_is_iri"] is False
    assert rows["ex:license"]["constant_is_iri"] is True
    # The full predicate IRI rides along for tooltips/links.
    assert rows["ex:mass"]["predicate_iri"] == "https://example.org/onto#mass"


_FUNCTION = _PREFIXES + """
<#CurveMap> a rr:TriplesMap ;
  rml:logicalSource [ rml:source "curves.csv" ; rml:referenceFormulation ql:CSV ] ;
  rr:subjectMap [ rr:template "https://example.org/resource/curve/{curve_id}" ;
    rr:class ex:Curve ] ;
  rr:predicateObjectMap [ rr:predicate ex:points ;
    rr:objectMap [ rmlf:functionExecution [
        rmlf:function fn:json_pluck ;
        rmlf:input [ rmlf:parameter fn:p_value ;
          rmlf:inputValueMap [ rml:reference "raw_json" ] ] ;
        rmlf:input [ rmlf:parameter fn:p_field ;
          rmlf:inputValueMap [ rr:constant "x" ] ]
      ] ] ] .
"""


def test_function_execution_projection() -> None:
    out = summarize_rml(_FUNCTION)
    assert out["warnings"] == []
    row = out["maps"][0]["properties"][0]
    assert row["kind"] == "function"
    assert row["function"] == "json_pluck"
    assert row["function_iri"].endswith("#json_pluck")
    args = {a["param"]: a for a in row["args"]}
    assert args["p_value"]["kind"] == "reference"
    assert args["p_value"]["reference"] == "raw_json"
    assert args["p_field"]["kind"] == "constant"
    assert args["p_field"]["constant"] == "x"


# The Mapping-IR compiler emits the NEW RML namespace (rml:constant ==
# http://w3id.org/rml/, bound to rmlf: here) for a constant fed to a function's
# inputValueMap and for a plain constant object. Observed live in production
# Starrydata mappings: a function-argument constant read as `unknown` because the
# projector only recognized r2rml#constant. Pin both spellings.
_NEW_NS_CONSTANT = _PREFIXES + """
<#CurveMap> a rr:TriplesMap ;
  rml:logicalSource [ rml:source "curves.csv" ; rml:referenceFormulation ql:CSV ] ;
  rr:subjectMap [ rr:template "https://example.org/resource/curve/{curve_id}" ;
    rr:class ex:Curve ] ;
  rr:predicateObjectMap [ rr:predicate ex:unit ;
    rr:objectMap [ rmlf:functionExecution [
        rmlf:function fn:template ;
        rmlf:input [ rmlf:parameter fn:p_value ;
          rmlf:inputValueMap [ rml:reference "u" ] ] ;
        rmlf:input [ rmlf:parameter fn:p_template ;
          rmlf:inputValueMap [ rmlf:constant "https://example.org/unit/{1}" ] ]
      ] ] ] ;
  rr:predicateObjectMap [ rr:predicate ex:origin ;
    rr:objectMap [ rmlf:constant "lab-A" ] ] .
"""


def test_new_namespace_constant_is_recognized() -> None:
    out = summarize_rml(_NEW_NS_CONSTANT)
    assert out["warnings"] == []  # rml:constant must NOT read as unknown
    rows = {r["predicate"]: r for r in out["maps"][0]["properties"]}
    # Plain object-map constant in the new namespace.
    assert rows["ex:origin"]["kind"] == "constant"
    assert rows["ex:origin"]["constant"] == "lab-A"
    # Constant supplied as a function argument (the exact production shape).
    fn_args = {a["param"]: a for a in rows["ex:unit"]["args"]}
    assert fn_args["p_template"]["kind"] == "constant"
    assert fn_args["p_template"]["constant"] == "https://example.org/unit/{1}"


_JOIN = _PREFIXES + """
<#MeasurementMap> a rr:TriplesMap ;
  rml:logicalSource [ rml:source "meas.csv" ; rml:referenceFormulation ql:CSV ] ;
  rr:subjectMap [ rr:template "https://example.org/resource/meas/{meas_id}" ;
    rr:class ex:Measurement ] ;
  rr:predicateObjectMap [ rr:predicate ex:ofSample ;
    rr:objectMap [ rr:parentTriplesMap <#SampleMap> ;
      rr:joinCondition [ rr:child "sample_id" ; rr:parent "sample_id" ] ] ] .

<#SampleMap> a rr:TriplesMap ;
  rml:logicalSource [ rml:source "samples.csv" ; rml:referenceFormulation ql:CSV ] ;
  rr:subjectMap [ rr:template "https://example.org/resource/sample/{sample_id}" ;
    rr:class ex:Sample ] .
"""


def test_join_projection() -> None:
    out = summarize_rml(_JOIN)
    assert out["warnings"] == []
    by_id = {m["id"]: m for m in out["maps"]}
    row = by_id["MeasurementMap"]["properties"][0]
    assert row["kind"] == "join"
    assert row["parent_map"] == "SampleMap"
    assert row["conditions"] == [{"child": "sample_id", "parent": "sample_id"}]


_ITERATOR = _PREFIXES + """
<#DocMap> a rr:TriplesMap ;
  rml:logicalSource [ rml:source "paper.xml" ;
    rml:referenceFormulation ql:XPath ; rml:iterator "/article/sec" ] ;
  rr:subjectMap [ rr:template "https://example.org/resource/sec/{@id}" ;
    rr:class ex:Section ] ;
  rr:predicateObjectMap [ rr:predicate ex:title ;
    rr:objectMap [ rml:reference "title" ; rr:language "en" ] ] .
"""


def test_iterator_language_and_order() -> None:
    out = summarize_rml(_ITERATOR)
    m = out["maps"][0]
    assert m["iterator"] == "/article/sec"
    assert m["formulation"] == "XPath"
    assert m["properties"][0]["language"] == "en"


def test_maps_follow_source_text_order() -> None:
    # MeasurementMap is authored before SampleMap; the projection keeps that.
    out = summarize_rml(_JOIN)
    assert [m["id"] for m in out["maps"]] == ["MeasurementMap", "SampleMap"]


def test_empty_and_unparseable_inputs_degrade() -> None:
    assert summarize_rml("") == {"maps": [], "prefixes": {}, "warnings": []}
    out = summarize_rml("@prefix rr: <http://www.w3.org/ns/r2rml#> .\n<#x> a rr:TriplesMap")
    assert out["maps"] == []
    assert len(out["warnings"]) == 1
    assert "could not parse" in out["warnings"][0]


def test_unrecognized_object_map_warns_not_drops() -> None:
    ttl = _PREFIXES + """
<#OddMap> a rr:TriplesMap ;
  rml:logicalSource [ rml:source "odd.csv" ; rml:referenceFormulation ql:CSV ] ;
  rr:subjectMap [ rr:template "https://example.org/resource/odd/{id}" ; rr:class ex:Odd ] ;
  rr:predicateObjectMap [ rr:predicate ex:weird ;
    rr:objectMap [ rr:inverseExpression "{id}" ] ] .
"""
    out = summarize_rml(ttl)
    row = out["maps"][0]["properties"][0]
    assert row["kind"] == "unknown"
    assert any("does not recognize" in w for w in out["warnings"])


def test_real_repo_mappings_project_cleanly() -> None:
    # The two in-repo shapes: CSV-source (Materials Project) and XPath-source
    # (papers JATS, with FnO function executions). Both must project with no
    # warnings — these are exactly what the catalog will render.
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "datasets"
    for rel in ("materials_project/json/mp.rml.ttl", "papers/jats/PMC5951533.rml.ttl"):
        out = summarize_rml((root / rel).read_text(encoding="utf-8"))
        assert out["maps"], rel
        assert out["warnings"] == [], rel
        for m in out["maps"]:
            assert m["subject"].get("classes"), f"{rel}: {m['id']} has no classes"
