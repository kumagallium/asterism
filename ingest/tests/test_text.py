"""Tests for asterism.text (generic helpers extracted from starrydata, #20 P2).

Behavioural coverage of the helpers lives with their original call sites; here we
(1) lock the domain-neutral behaviour directly and (2) assert the backward-compat
re-export from asterism.starrydata is the *same object* so old import paths keep
working.
"""

from __future__ import annotations

from asterism import starrydata, text


def test_slugify_normalizes_and_strips_quotes() -> None:
    assert text.slugify('"Nature Materials"') == "nature-materials"
    assert text.slugify("  Bi2Te3 / SnSe  ") == "bi2te3-snse"
    assert text.slugify("!!!") == "unknown"


def test_parse_issued_csl_date_parts() -> None:
    assert text.parse_issued('{"date_parts": [[2014, 4, 17]]}') == "2014-04-17"
    assert text.parse_issued('{"date_parts": [[2014]]}') == "2014-01-01"
    assert text.parse_issued("not json") is None
    assert text.parse_issued("") is None


def test_parse_float_array_drops_garbage() -> None:
    assert text.parse_float_array("[1, 2.5, 3]") == [1.0, 2.5, 3.0]
    assert text.parse_float_array('[1, null, "x", 2]') == [1.0, 2.0]
    assert text.parse_float_array("broken") == []


def test_safe_url_encodes_illegal_and_rejects_schemeless() -> None:
    assert text.safe_url("https://ex.org/a b") == "https://ex.org/a%20b"
    assert text.safe_url("http://dx.doi.org/10.1002/(x)<y>") == (
        "http://dx.doi.org/10.1002/(x)%3Cy%3E"
    )
    assert text.safe_url("unknown") is None  # scheme-less placeholder
    assert text.safe_url("") is None


def test_starrydata_reexports_same_objects() -> None:
    # Backward-compat: `from asterism.starrydata import slugify` must still work
    # and resolve to the moved implementation (identity, not a copy).
    for name in ("slugify", "parse_issued", "parse_float_array", "strip_quoted", "safe_url"):
        assert getattr(starrydata, name) is getattr(text, name), name
