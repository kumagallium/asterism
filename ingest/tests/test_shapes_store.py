"""The shape checks against a REAL SPARQL engine (pyoxigraph, in-memory).

test_shapes.py injects a fake runner, so it proves the logic but not the SPARQL.
A syntax error in a generated query would be swallowed by the best-effort
`except` in `run_shape_checks` and silently disable the whole feature in
production — exactly the failure class this file exists to catch. Same engine
family the store runs (ADR data-shape-checks.md §D2).
"""
from __future__ import annotations

import pytest

from asterism.shapes import compile_shapes, run_shape_checks

pyoxigraph = pytest.importorskip("pyoxigraph")
pytest.importorskip("rdflib")

_GRAPH = "urn:test:graph"
_EX = "https://example.org/v/"
_R = "https://example.org/r/"

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
  ] ;
  rr:predicateObjectMap [
    rr:predicate ex:hasMeasurement ;
    rr:objectMap [ rr:template "https://example.org/r/measurement/{id}" ]
  ] .

<#Measurement> a rr:TriplesMap ;
  rml:logicalSource [ rml:source "measurements.csv" ; rml:referenceFormulation ql:CSV ] ;
  rr:subjectMap [ rr:template "https://example.org/r/measurement/{id}" ; rr:class ex:Measurement ] ;
  rr:predicateObjectMap [
    rr:predicate ex:value ;
    rr:objectMap [ rml:reference "v" ; rr:datatype xsd:double ]
  ] .
"""

_HEALTHY = f"""
<{_R}sample/1> a <{_EX}Sample> ;
  <{_EX}mass> "1.5"^^<http://www.w3.org/2001/XMLSchema#double> ;
  <{_EX}hasMeasurement> <{_R}measurement/1> .
<{_R}measurement/1> a <{_EX}Measurement> ;
  <{_EX}value> "300.0"^^<http://www.w3.org/2001/XMLSchema#double> .
"""


def _store(turtle: str):
    store = pyoxigraph.Store()
    store.load(
        turtle.encode("utf-8"),
        mime_type="text/turtle",
        to_graph=pyoxigraph.NamedNode(_GRAPH),
    )
    return store


def _runner(store):
    """Adapt pyoxigraph results to the SPARQL JSON shape run_shape_checks reads."""

    async def run(query: str):
        result = store.query(query)
        if isinstance(result, bool):
            return {"boolean": result}
        bindings = []
        for solution in result:
            row = {}
            for name in ("o",):
                term = solution[name]
                if term is not None:
                    row[name] = {"value": term.value}
            bindings.append(row)
        return {"results": {"bindings": bindings}}

    return run


async def _findings(turtle: str):
    return await run_shape_checks(compile_shapes(_RML), _GRAPH, _runner(_store(turtle)))


@pytest.mark.asyncio
async def test_every_generated_query_is_valid_sparql():
    """The point of this file: a malformed query would be swallowed as 'no
    finding'. Run them all against a real engine and let it raise."""
    from asterism.shapes import class_presence_query, shape_check_queries

    store = _store(_HEALTHY)
    checks = shape_check_queries(compile_shapes(_RML), _GRAPH)
    assert checks, "the fixture should produce checks"
    for check in checks:
        store.query(check.query)  # raises on a syntax error
    store.query(class_presence_query(_GRAPH, _EX + "Sample"))


@pytest.mark.asyncio
async def test_healthy_graph_has_no_findings():
    assert await _findings(_HEALTHY) == []


@pytest.mark.asyncio
async def test_dangling_link_is_found():
    """The measurement the sample points at was never materialized."""
    turtle = f"""
    <{_R}sample/1> a <{_EX}Sample> ;
      <{_EX}mass> "1.5"^^<http://www.w3.org/2001/XMLSchema#double> ;
      <{_EX}hasMeasurement> <{_R}measurement/99> .
    <{_R}measurement/1> a <{_EX}Measurement> ;
      <{_EX}value> "300.0"^^<http://www.w3.org/2001/XMLSchema#double> .
    """
    findings = await _findings(turtle)
    dangling = [f for f in findings if f.kind == "dangling-reference"]
    assert len(dangling) == 1
    assert dangling[0].examples == (f"{_R}measurement/99",)


@pytest.mark.asyncio
async def test_predicate_that_never_materialized_is_found():
    turtle = f"""
    <{_R}sample/1> a <{_EX}Sample> ;
      <{_EX}hasMeasurement> <{_R}measurement/1> .
    <{_R}measurement/1> a <{_EX}Measurement> ;
      <{_EX}value> "300.0"^^<http://www.w3.org/2001/XMLSchema#double> .
    """
    findings = await _findings(turtle)
    assert [(f.kind, f.predicate) for f in findings] == [
        ("predicate-missing", _EX + "mass")
    ]


@pytest.mark.asyncio
async def test_link_to_the_wrong_class_is_found():
    turtle = f"""
    <{_R}sample/1> a <{_EX}Sample> ;
      <{_EX}mass> "1.5"^^<http://www.w3.org/2001/XMLSchema#double> ;
      <{_EX}hasMeasurement> <{_R}measurement/1> .
    <{_R}measurement/1> a <{_EX}Sample> ;
      <{_EX}value> "300.0"^^<http://www.w3.org/2001/XMLSchema#double> .
    """
    findings = await _findings(turtle)
    kinds = {f.kind for f in findings}
    assert "class-mismatch" in kinds
    # Not also reported as dangling: the object exists, it is just typed wrong.
    assert "dangling-reference" not in kinds


@pytest.mark.asyncio
async def test_datatype_mismatch_is_found():
    turtle = f"""
    <{_R}sample/1> a <{_EX}Sample> ;
      <{_EX}mass> "heavy" ;
      <{_EX}hasMeasurement> <{_R}measurement/1> .
    <{_R}measurement/1> a <{_EX}Measurement> ;
      <{_EX}value> "300.0"^^<http://www.w3.org/2001/XMLSchema#double> .
    """
    findings = await _findings(turtle)
    assert [(f.kind, f.predicate) for f in findings] == [
        ("datatype-mismatch", _EX + "mass")
    ]


@pytest.mark.asyncio
async def test_checks_are_scoped_to_the_dataset_graph():
    """A link whose target lives in ANOTHER graph is still dangling for this
    dataset — but data in another graph must not silence a real finding here."""
    store = pyoxigraph.Store()
    store.load(
        _HEALTHY.encode("utf-8"),
        mime_type="text/turtle",
        to_graph=pyoxigraph.NamedNode("urn:test:other"),
    )
    findings = await run_shape_checks(compile_shapes(_RML), _GRAPH, _runner(store))
    # Nothing at all in the dataset's own graph -> no class instances -> silence.
    assert findings == []
