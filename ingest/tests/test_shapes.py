"""Data shape checks (asterism.shapes) — ADR docs/architecture/data-shape-checks.md.

Two halves: compiling shapes out of RML (deterministic, no store needed) and
turning query results into findings. The SPARQL itself is exercised end-to-end
against a real in-memory store in ``test_shapes_store.py`` when pyoxigraph is
available; here we keep the runner injected so the logic is testable anywhere.
"""
from __future__ import annotations

import pytest

from asterism.shapes import (
    MARKER_DANGLING,
    MARKER_DATATYPE,
    MARKER_MISSING,
    MARKER_WRONG_CLASS,
    NodeShape,
    PropertyShape,
    compile_shapes,
    run_shape_checks,
    shape_check_queries,
    shapes_to_shacl,
)

pytest.importorskip("rdflib")

_PREFIXES = (
    "@prefix rr: <http://www.w3.org/ns/r2rml#> .\n"
    "@prefix rml: <http://semweb.mmlab.be/ns/rml#> .\n"
    "@prefix ql: <http://semweb.mmlab.be/ns/ql#> .\n"
    "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .\n"
    "@prefix ex: <https://example.org/v/> .\n"
)

_EX = "https://example.org/v/"

# Sample --hasMeasurement--> Measurement, plus a typed literal and a plain one.
_RML = _PREFIXES + """
<#Sample> a rr:TriplesMap ;
  rml:logicalSource [ rml:source "samples.csv" ; rml:referenceFormulation ql:CSV ] ;
  rr:subjectMap [ rr:template "https://example.org/r/sample/{id}" ; rr:class ex:Sample ] ;
  rr:predicateObjectMap [
    rr:predicate ex:label ;
    rr:objectMap [ rml:reference "name" ]
  ] ;
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


def _shape(shapes, local: str) -> NodeShape:
    for shape in shapes:
        if shape.class_iri == _EX + local:
            return shape
    raise AssertionError(f"no shape for {local}: {[s.class_iri for s in shapes]}")


def _prop(shape: NodeShape, local: str) -> PropertyShape:
    for prop in shape.properties:
        if prop.predicate == _EX + local:
            return prop
    raise AssertionError(f"no property {local} on {shape.class_iri}")


class TestCompileShapes:
    def test_one_shape_per_class(self):
        shapes = compile_shapes(_RML)
        assert {s.class_iri for s in shapes} == {_EX + "Sample", _EX + "Measurement"}

    def test_literal_datatype_is_carried(self):
        mass = _prop(_shape(compile_shapes(_RML), "Sample"), "mass")
        assert mass.kind == "literal"
        assert mass.datatype == "http://www.w3.org/2001/XMLSchema#double"

    def test_plain_reference_is_a_literal_without_datatype(self):
        label = _prop(_shape(compile_shapes(_RML), "Sample"), "label")
        assert (label.kind, label.datatype) == ("literal", None)

    def test_link_resolves_target_class_via_subject_template(self):
        """The object template equals Measurement's subject template, so the
        design pins the link's target — that is what makes S3 possible."""
        link = _prop(_shape(compile_shapes(_RML), "Sample"), "hasMeasurement")
        assert link.kind == "iri"
        assert link.target_classes == (_EX + "Measurement",)

    def test_parent_triples_map_join_resolves_target_class(self):
        rml = _PREFIXES + """
        <#A> a rr:TriplesMap ;
          rml:logicalSource [ rml:source "a.csv" ; rml:referenceFormulation ql:CSV ] ;
          rr:subjectMap [ rr:template "https://example.org/r/a/{id}" ; rr:class ex:A ] ;
          rr:predicateObjectMap [
            rr:predicate ex:linksTo ;
            rr:objectMap [ rr:parentTriplesMap <#B> ]
          ] .
        <#B> a rr:TriplesMap ;
          rml:logicalSource [ rml:source "b.csv" ; rml:referenceFormulation ql:CSV ] ;
          rr:subjectMap [ rr:template "https://example.org/r/b/{id}" ; rr:class ex:B ] .
        """
        link = _prop(_shape(compile_shapes(rml), "A"), "linksTo")
        assert (link.kind, link.target_classes) == ("iri", (_EX + "B",))

    def test_untyped_target_map_leaves_target_open(self):
        """A link into a map that declares no rr:class must NOT claim an expected
        class — S3 would then flag every object as wrong."""
        rml = _PREFIXES + """
        <#A> a rr:TriplesMap ;
          rml:logicalSource [ rml:source "a.csv" ; rml:referenceFormulation ql:CSV ] ;
          rr:subjectMap [ rr:template "https://example.org/r/a/{id}" ; rr:class ex:A ] ;
          rr:predicateObjectMap [
            rr:predicate ex:linksTo ;
            rr:objectMap [ rr:parentTriplesMap <#B> ]
          ] .
        <#B> a rr:TriplesMap ;
          rml:logicalSource [ rml:source "b.csv" ; rml:referenceFormulation ql:CSV ] ;
          rr:subjectMap [ rr:template "https://example.org/r/b/{id}" ] .
        """
        link = _prop(_shape(compile_shapes(rml), "A"), "linksTo")
        assert link.target_classes == ()

    def test_two_maps_minting_the_same_class_are_unioned(self):
        rml = _PREFIXES + """
        <#A1> a rr:TriplesMap ;
          rml:logicalSource [ rml:source "a1.csv" ; rml:referenceFormulation ql:CSV ] ;
          rr:subjectMap [ rr:template "https://example.org/r/a/{id}" ; rr:class ex:A ] ;
          rr:predicateObjectMap [ rr:predicate ex:p1 ; rr:objectMap [ rml:reference "x" ] ] .
        <#A2> a rr:TriplesMap ;
          rml:logicalSource [ rml:source "a2.csv" ; rml:referenceFormulation ql:CSV ] ;
          rr:subjectMap [ rr:template "https://example.org/r/a/{id}" ; rr:class ex:A ] ;
          rr:predicateObjectMap [ rr:predicate ex:p2 ; rr:objectMap [ rml:reference "y" ] ] .
        """
        shape = _shape(compile_shapes(rml), "A")
        assert {p.predicate for p in shape.properties} == {_EX + "p1", _EX + "p2"}

    def test_malformed_rml_yields_no_shapes_rather_than_raising(self):
        assert compile_shapes("this is not turtle {{{") == ()

    def test_output_is_deterministic(self):
        assert compile_shapes(_RML) == compile_shapes(_RML)


class TestQueries:
    def test_every_property_gets_a_presence_check(self):
        checks = shape_check_queries(compile_shapes(_RML), "urn:g")
        missing = {(c.class_iri, c.predicate) for c in checks if c.kind == "predicate-missing"}
        assert (_EX + "Sample", _EX + "mass") in missing
        assert (_EX + "Measurement", _EX + "value") in missing

    def test_iri_property_gets_dangling_and_class_checks(self):
        checks = shape_check_queries(compile_shapes(_RML), "urn:g")
        kinds = {c.kind for c in checks if c.predicate == _EX + "hasMeasurement"}
        assert kinds == {"predicate-missing", "dangling-reference", "class-mismatch"}

    def test_literal_without_datatype_gets_no_datatype_check(self):
        checks = shape_check_queries(compile_shapes(_RML), "urn:g")
        kinds = {c.kind for c in checks if c.predicate == _EX + "label"}
        assert kinds == {"predicate-missing"}

    def test_xsd_string_is_not_datatype_checked(self):
        """Plain and xsd:string literals are the same term in RDF 1.1 — checking
        it would fire on every store that reports the plain form."""
        rml = _PREFIXES + """
        <#A> a rr:TriplesMap ;
          rml:logicalSource [ rml:source "a.csv" ; rml:referenceFormulation ql:CSV ] ;
          rr:subjectMap [ rr:template "https://example.org/r/a/{id}" ; rr:class ex:A ] ;
          rr:predicateObjectMap [
            rr:predicate ex:s ;
            rr:objectMap [ rml:reference "x" ; rr:datatype xsd:string ]
          ] .
        """
        checks = shape_check_queries(compile_shapes(rml), "urn:g")
        assert {c.kind for c in checks} == {"predicate-missing"}

    def test_queries_are_scoped_to_the_graph(self):
        for check in shape_check_queries(compile_shapes(_RML), "urn:g"):
            assert "GRAPH <urn:g>" in check.query

    def test_queries_are_read_only(self):
        for check in shape_check_queries(compile_shapes(_RML), "urn:g"):
            upper = check.query.upper()
            for verb in ("INSERT", "DELETE", "DROP", "CLEAR", "LOAD"):
                assert verb not in upper


def _select(values):
    return {"results": {"bindings": [{"o": {"value": v}} for v in values]}}


def _ask(value: bool):
    return {"boolean": value}


class TestRunChecks:
    @pytest.mark.asyncio
    async def test_clean_data_yields_no_findings(self):
        async def run(query: str):
            return _ask(True) if query.startswith("ASK") else _select([])

        findings = await run_shape_checks(compile_shapes(_RML), "urn:g", run)
        assert findings == []

    @pytest.mark.asyncio
    async def test_declared_predicate_that_never_materialized_is_reported(self):
        async def run(query: str):
            if query.startswith("ASK"):
                # The class exists; the mass predicate does not.
                return _ask("mass" not in query)
            return _select([])

        findings = await run_shape_checks(compile_shapes(_RML), "urn:g", run)
        kinds = {(f.kind, f.predicate) for f in findings}
        assert ("predicate-missing", _EX + "mass") in kinds
        message = next(f.message for f in findings if f.predicate == _EX + "mass")
        assert MARKER_MISSING in message

    @pytest.mark.asyncio
    async def test_a_class_with_no_instances_is_not_reported(self):
        """Nothing to say about predicates on a class that was never minted —
        the empty class is its own (separate) problem."""

        async def run(query: str):
            return _ask(False) if query.startswith("ASK") else _select([])

        findings = await run_shape_checks(compile_shapes(_RML), "urn:g", run)
        assert findings == []

    @pytest.mark.asyncio
    async def test_dangling_link_names_the_offending_iris(self):
        broken = ["https://example.org/r/measurement/9", "https://example.org/r/measurement/10"]

        async def run(query: str):
            if query.startswith("ASK"):
                return _ask(True)
            if "NOT EXISTS { ?o ?anyp ?anyo }" in query:
                return _select(broken)
            return _select([])

        findings = await run_shape_checks(compile_shapes(_RML), "urn:g", run)
        dangling = [f for f in findings if f.kind == "dangling-reference"]
        assert len(dangling) == 1
        assert dangling[0].examples == tuple(broken)
        assert MARKER_DANGLING in dangling[0].message
        assert broken[0] in dangling[0].message

    @pytest.mark.asyncio
    async def test_examples_are_capped_and_marked_truncated(self):
        many = [f"https://example.org/r/measurement/{i}" for i in range(6)]

        async def run(query: str):
            if query.startswith("ASK"):
                return _ask(True)
            if "NOT EXISTS" in query and "?anyp" in query:
                return _select(many)
            return _select([])

        findings = await run_shape_checks(compile_shapes(_RML), "urn:g", run)
        dangling = next(f for f in findings if f.kind == "dangling-reference")
        assert len(dangling.examples) == 5
        assert dangling.truncated is True
        assert "(and more)" in dangling.message

    @pytest.mark.asyncio
    async def test_wrong_class_link_is_reported(self):
        async def run(query: str):
            if query.startswith("ASK"):
                return _ask(True)
            if "?o <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> ?t" in query:
                return _select(["https://example.org/r/sample/3"])
            return _select([])

        findings = await run_shape_checks(compile_shapes(_RML), "urn:g", run)
        wrong = [f for f in findings if f.kind == "class-mismatch"]
        assert len(wrong) == 1
        assert MARKER_WRONG_CLASS in wrong[0].message
        assert "Measurement" in wrong[0].message  # the expected class is named

    @pytest.mark.asyncio
    async def test_datatype_mismatch_is_reported(self):
        async def run(query: str):
            if query.startswith("ASK"):
                return _ask(True)
            if "datatype(?o)" in query:
                return _select(["n/a"])
            return _select([])

        findings = await run_shape_checks(compile_shapes(_RML), "urn:g", run)
        mismatch = [f for f in findings if f.kind == "datatype-mismatch"]
        assert {f.predicate for f in mismatch} == {_EX + "mass", _EX + "value"}
        assert MARKER_DATATYPE in mismatch[0].message

    @pytest.mark.asyncio
    async def test_a_failing_query_never_raises(self):
        async def run(query: str):
            raise RuntimeError("store is down")

        assert await run_shape_checks(compile_shapes(_RML), "urn:g", run) == []

    @pytest.mark.asyncio
    async def test_query_budget_is_respected(self):
        fired = 0

        async def run(query: str):
            nonlocal fired
            fired += 1
            return _ask(True) if query.startswith("ASK") else _select([])

        await run_shape_checks(compile_shapes(_RML), "urn:g", run, max_queries=3)
        assert fired <= 3


class TestShaclExport:
    def test_emits_target_class_and_paths(self):
        ttl = shapes_to_shacl(compile_shapes(_RML))
        assert f"sh:targetClass <{_EX}Sample>" in ttl
        assert f"sh:path <{_EX}mass>" in ttl
        assert "sh:datatype <http://www.w3.org/2001/XMLSchema#double>" in ttl
        assert f"sh:class <{_EX}Measurement>" in ttl

    def test_parses_as_turtle(self):
        rdflib = pytest.importorskip("rdflib")
        graph = rdflib.Graph()
        graph.parse(data=shapes_to_shacl(compile_shapes(_RML)), format="turtle")
        node_shapes = set(
            graph.subjects(
                rdflib.RDF.type, rdflib.URIRef("http://www.w3.org/ns/shacl#NodeShape")
            )
        )
        assert len(node_shapes) == 2

    def test_class_without_properties_still_parses(self):
        rdflib = pytest.importorskip("rdflib")
        shapes = (NodeShape(class_iri=_EX + "Bare", label="Bare"),)
        rdflib.Graph().parse(data=shapes_to_shacl(shapes), format="turtle")

    def test_is_deterministic(self):
        shapes = compile_shapes(_RML)
        assert shapes_to_shacl(shapes) == shapes_to_shacl(shapes)
