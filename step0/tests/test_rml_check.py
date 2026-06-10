"""Tests for asterism_step0.rml_check (T9 — RML closed-set guardrail)."""
from __future__ import annotations

from textwrap import dedent

import pytest

from asterism_step0.rml_check import (
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
    @prefix fn:   <https://kumagallium.github.io/asterism/fn/> .
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


# A mapping using a parameterized primitive (lookup) with a *constant* table
# argument. The constant ("country_iso3166") is a string literal, not a function
# IRI — so the closed-set check sees only fn:lookup, and the primitive passes T9
# exactly like the column-only functions do.
_RML_PRIMITIVE = dedent(
    """
    @prefix rr:   <http://www.w3.org/ns/r2rml#> .
    @prefix rml:  <http://semweb.mmlab.be/ns/rml#> .
    @prefix ql:   <http://semweb.mmlab.be/ns/ql#> .
    @prefix rmlf: <http://w3id.org/rml/> .
    @prefix fn:   <https://kumagallium.github.io/asterism/fn/> .

    <#SampleMap> a rr:TriplesMap ;
      rml:logicalSource [ rml:source "samples.csv" ; rml:referenceFormulation ql:CSV ] ;
      rr:subjectMap [ rr:template "https://example.com/r/s/{id}" ] ;
      rr:predicateObjectMap [ rr:predicate <https://ex/country> ; rr:objectMap [
          rmlf:functionExecution [ rmlf:function fn:lookup ;
            rmlf:input [ rmlf:parameter fn:p_value ;
              rmlf:inputValueMap [ rml:reference "country" ] ] ;
            rmlf:input [ rmlf:parameter fn:p_table ;
              rmlf:inputValueMap [ rmlf:constant "country_iso3166" ] ] ] ] ] .
    """
).lstrip("\n")


def test_primitive_constant_arg_is_not_seen_as_a_function() -> None:
    # only the function IRI is collected; the constant table name is a literal
    assert referenced_function_iris(_RML_PRIMITIVE) == {_iri("lookup")}


def test_primitive_passes_closed_set_when_allowed() -> None:
    allowed = {_iri("lookup"), _iri("date_iso")}
    assert closed_set_violations(_RML_PRIMITIVE, allowed) == []
    # ...and is flagged when not in the vetted set
    assert closed_set_violations(_RML_PRIMITIVE, {_iri("date_iso")}) == [_iri("lookup")]
