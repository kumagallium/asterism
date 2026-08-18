"""A design that reads columns but emits no VALUE.

Live 2026-08-18 (XRD reference card): all 25 properties were written as
``object_template: .../resource/{Some Column}``, so every measured value became
an opaque IRI nothing describes. Columns exist, functions vetted, T1-T10 green,
connectivity and empty-shell advisories silent (an object template counts as
binding its column) — the self-correction loop reported "0 issues" and the human
spent four "AI に直してもらう" rounds rebuilding a design the machine certified.

These pin the OUTCOME, not the shape: a dataset answers questions with values.
"""
from __future__ import annotations

import pytest

from asterism.rml_validate import design_advisories

_CSV = b"SID,Volume\n1,118.845\n2,120.5\n"

# The untyped-numeric advisory also contains the word "literal"; match only the
# sentence THIS check emits.
_MARK = "emits no literal value"


def _hits(advisories):
    return [a for a in advisories if _MARK in a.lower()]


@pytest.fixture()
def src(tmp_path):
    """The advisory is a claim about the data, so it only speaks with the data
    in hand — every case here supplies the real source."""
    (tmp_path / "d.csv").write_bytes(_CSV)
    return tmp_path

_HEAD = (
    "@prefix rr:  <http://www.w3.org/ns/r2rml#> .\n"
    "@prefix rml: <http://semweb.mmlab.be/ns/rml#> .\n"
    "@prefix ql:  <http://semweb.mmlab.be/ns/ql#> .\n"
    "@prefix ex:  <https://ns.invalid/ns#> .\n"
)


def _map(name: str, body: str) -> str:
    return (
        f"<#{name}> a rr:TriplesMap ;\n"
        '  rml:logicalSource [ rml:source "d.csv" ; rml:referenceFormulation ql:CSV ] ;\n'
        f'  rr:subjectMap [ rr:template "https://ex/{name}/{{SID}}" ; rr:class ex:{name} ] ;\n'
        f"{body} .\n"
    )


_LITERAL = (
    "  rr:predicateObjectMap [ rr:predicate ex:volume ;\n"
    '    rr:objectMap [ rml:reference "Volume" ] ]'
)
_IRI = (
    "  rr:predicateObjectMap [ rr:predicate ex:volume ;\n"
    '    rr:objectMap [ rr:template "https://ex/resource/{Volume}" ] ]'
)


def test_a_design_of_only_iris_is_reported(src) -> None:
    """The live shape: every property an IRI template over a value column."""
    out = design_advisories(_HEAD + _map("Material", _IRI), src)
    assert any("emits NO literal values at all" in a for a in out)
    hit = next(a for a in out if "emits NO literal" in a)
    # names the repair, not just the absence
    assert "column: <header>" in hit and "object_template:" in hit


def test_a_design_that_emits_values_is_silent(src) -> None:
    assert not _hits(design_advisories(_HEAD + _map("Material", _LITERAL), src))


def test_one_silent_map_among_several_is_named(src) -> None:
    """A whole-design line only when NOTHING emits a value; otherwise the map."""
    ttl = _HEAD + _map("Material", _LITERAL) + _map("Peak", _IRI)
    out = _hits(design_advisories(ttl, src))
    assert len(out) == 1
    assert "map 'Peak'" in out[0]
    assert "emits NO literal values at all" not in out[0]


def test_a_typed_template_literal_counts_as_a_value(src) -> None:
    """``rr:termType rr:Literal`` on a template IS a value — not a false hit."""
    body = (
        "  rr:predicateObjectMap [ rr:predicate ex:label ;\n"
        '    rr:objectMap [ rr:template "{Volume} A" ; rr:termType rr:Literal ] ]'
    )
    assert not _hits(design_advisories(_HEAD + _map("M", body), src))


def test_a_link_only_map_with_no_value_columns_stays_quiet(src) -> None:
    """A pure link map binds no value column of its own — the empty-shell check
    owns that case; this one must not double-report it."""
    body = (
        "  rr:predicateObjectMap [ rr:predicate ex:partOf ;\n"
        '    rr:objectMap [ rr:template "https://ex/Material/{SID}" ] ]'
    )
    assert not _hits(design_advisories(_HEAD + _map("Peak", body), src))


def test_without_the_source_it_says_nothing(tmp_path) -> None:
    """No data, no claim — a bare mapping (unit fixture, or a design reviewed
    before its source is attached) must not be accused of losing values."""
    assert not _hits(design_advisories(_HEAD + _map("Material", _IRI)))
