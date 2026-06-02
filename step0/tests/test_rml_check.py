"""Tests for csv2rdf_step0.rml_check (T9 — RML closed-set guardrail)."""
from __future__ import annotations

from textwrap import dedent

import pytest

from csv2rdf_step0.rml_check import (
    FN_NAMESPACE,
    closed_set_violations,
    referenced_function_iris,
)

pytest.importorskip("rdflib")

# Minimal RML referencing two functions: one allowed, one not.
_RML = dedent(
    """
    @prefix rr:   <http://www.w3.org/ns/r2rml#> .
    @prefix rml:  <http://semweb.mmlab.be/ns/rml#> .
    @prefix ql:   <http://semweb.mmlab.be/ns/ql#> .
    @prefix rmlf: <http://w3id.org/rml/> .
    @prefix fn:   <https://kumagallium.github.io/csv2rdf-mcp/fn/> .
    @prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .

    <#CurveMap> a rr:TriplesMap ;
      rml:logicalSource [ rml:source "curves.csv" ; rml:referenceFormulation ql:CSV ] ;
      rr:subjectMap [ rr:template "https://example.com/r/curve/{id}" ] ;
      rr:predicateObjectMap [ rr:predicate <https://ex/yMax> ; rr:objectMap [
          rmlf:functionExecution [ rmlf:function fn:float_array_max ;
            rmlf:input [ rmlf:parameter fn:p_value ; rmlf:inputValueMap [ rml:reference "y" ] ] ] ;
          rr:datatype xsd:double ] ] ;
      rr:predicateObjectMap [ rr:predicate <https://ex/danger> ; rr:objectMap [
          rmlf:functionExecution [ rmlf:function fn:run_arbitrary_code ;
            rmlf:input [ rmlf:parameter fn:p_value ;
              rmlf:inputValueMap [ rml:reference "y" ] ] ] ] ] .
    """
).lstrip("\n")


def _iri(name: str) -> str:
    return FN_NAMESPACE + name


def test_referenced_function_iris_collects_all() -> None:
    used = referenced_function_iris(_RML)
    assert used == {_iri("float_array_max"), _iri("run_arbitrary_code")}


def test_closed_set_violations_flags_out_of_set() -> None:
    allowed = {_iri("float_array_max"), _iri("float_array_min")}
    violations = closed_set_violations(_RML, allowed)
    # The vetted function passes; the rogue one is flagged.
    assert violations == [_iri("run_arbitrary_code")]


def test_closed_set_violations_empty_when_all_allowed() -> None:
    allowed = {_iri("float_array_max"), _iri("run_arbitrary_code")}
    assert closed_set_violations(_RML, allowed) == []


def test_closed_set_violations_on_mapping_with_no_functions() -> None:
    direct_only = dedent(
        """
        @prefix rr:  <http://www.w3.org/ns/r2rml#> .
        @prefix rml: <http://semweb.mmlab.be/ns/rml#> .
        @prefix ql:  <http://semweb.mmlab.be/ns/ql#> .
        <#M> a rr:TriplesMap ;
          rml:logicalSource [ rml:source "p.csv" ; rml:referenceFormulation ql:CSV ] ;
          rr:subjectMap [ rr:template "https://ex/{id}" ] ;
          rr:predicateObjectMap [ rr:predicate <https://ex/name> ;
            rr:objectMap [ rml:reference "title" ] ] .
        """
    ).lstrip("\n")
    assert referenced_function_iris(direct_only) == set()
    assert closed_set_violations(direct_only, set()) == []
