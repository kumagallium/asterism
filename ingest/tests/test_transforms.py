"""Unit tests for the core Tier 0 transform helpers (asterism.transforms).

Each helper is ``str -> str`` and returns ``""`` for "no result". Tests cover the
core conversion, representative edge cases, and the empty-string contract.
"""

from __future__ import annotations

from asterism.transforms import (
    datetime_iso,
    doi_norm,
    nfkc_norm,
    number_clean,
    percent_to_ratio,
    range_max,
    range_min,
    strip_footnote,
    trim_collapse,
    unit_of,
    url_canonical,
    value_of,
    year_only,
)

# ---- numbers ----------------------------------------------------------------


def test_number_clean() -> None:
    assert number_clean("$1,234.50") == "1234.50"
    assert number_clean("(1,234)") == "-1234"  # accounting negative
    assert number_clean("1.2e3") == "1.2e3"  # scientific preserved
    assert number_clean("1 234") == "1234"  # space thousands separator
    assert number_clean("  -42 ") == "-42"
    assert number_clean("€2.000") == "2.000"  # currency stripped, precision kept
    # not a number / wrong primitive → ""
    assert number_clean("12%") == ""
    assert number_clean("abc") == ""
    assert number_clean("") == ""


def test_percent_to_ratio() -> None:
    assert percent_to_ratio("12%") == "0.12"
    assert percent_to_ratio("12.5%") == "0.125"
    assert percent_to_ratio("100%") == "1.0"
    assert percent_to_ratio("-5%") == "-0.05"
    # requires an explicit % (no scale guessing)
    assert percent_to_ratio("12") == ""
    assert percent_to_ratio("x%") == ""
    assert percent_to_ratio("") == ""


def test_numeric_range() -> None:
    assert range_min("10\u201320") == "10" and range_max("10\u201320") == "20"  # en dash
    assert range_min("10-20") == "10" and range_max("10-20") == "20"  # hyphen
    assert range_min("1.5 to 2.5") == "1.5" and range_max("1.5 to 2.5") == "2.5"
    assert range_min("-3 \u2014 5") == "-3" and range_max("-3 \u2014 5") == "5"
    # not a range → ""
    assert range_min("5") == "" and range_max("5") == ""
    assert range_min("") == "" and range_max("x") == ""


# ---- date / time ------------------------------------------------------------


def test_datetime_iso_epoch() -> None:
    # 13 digits = milliseconds, 10 = seconds; both emit the same UTC instant
    assert datetime_iso("1609459200000") == "2021-01-01T00:00:00Z"
    assert datetime_iso("1609459200") == "2021-01-01T00:00:00Z"


def test_datetime_iso_strings() -> None:
    assert datetime_iso("2014-03-05") == "2014-03-05T00:00:00"
    assert datetime_iso("2014-03-05T12:30:00Z") == "2014-03-05T12:30:00+00:00"
    assert datetime_iso("03/05/2014") == "2014-03-05T00:00:00"  # US m/d/Y
    assert datetime_iso("Mar 5, 2014") == "2014-03-05T00:00:00"
    assert datetime_iso("nope") == ""
    assert datetime_iso("") == ""


def test_year_only() -> None:
    assert year_only("March 5, 2014") == "2014"
    assert year_only("2014-03-05") == "2014"
    assert year_only("(2014)") == "2014"
    # a long all-digit run (epoch) must not yield its leading 4 digits
    assert year_only("1609459200000") == ""
    assert year_only("98") == ""
    assert year_only("") == ""


# ---- string hygiene ---------------------------------------------------------


def test_nfkc_norm() -> None:
    assert nfkc_norm("\uff21\uff22\uff23") == "ABC"  # full-width latin
    assert nfkc_norm("\uff11\uff12\uff13") == "123"  # full-width digits
    assert nfkc_norm("") == ""


def test_trim_collapse() -> None:
    assert trim_collapse("  a   b  ") == "a b"
    assert trim_collapse("x\t\ny") == "x y"
    assert trim_collapse("") == ""


def test_strip_footnote() -> None:
    assert strip_footnote("Hello[1]") == "Hello"
    assert strip_footnote("World*") == "World"
    assert strip_footnote("value¹") == "value"
    assert strip_footnote("note†‡") == "note"
    # parenthesized numbers are years/counts, not footnotes — left intact
    assert strip_footnote("Movie (2024)") == "Movie (2024)"
    assert strip_footnote("") == ""


# ---- identifiers ------------------------------------------------------------


def test_doi_norm() -> None:
    assert doi_norm("https://doi.org/10.1021/Ar400290F") == "10.1021/ar400290f"
    assert doi_norm("doi:10.1000/xyz123") == "10.1000/xyz123"
    assert doi_norm("10.1038/nature12373.") == "10.1038/nature12373"  # trailing dot
    assert doi_norm("no doi here") == ""
    assert doi_norm("") == ""


def test_url_canonical() -> None:
    assert url_canonical("HTTP://Example.com:80/Path/") == "http://example.com/Path"
    assert url_canonical("https://a.com:443/x") == "https://a.com/x"
    assert url_canonical("https://a.com:8080/x") == "https://a.com:8080/x"  # non-default port kept
    assert url_canonical("https://a.com/x#frag") == "https://a.com/x"  # fragment dropped
    assert url_canonical("not a url") == ""
    assert url_canonical("") == ""


# ---- value + unit -----------------------------------------------------------


def test_value_unit_split() -> None:
    assert value_of("300 K") == "300" and unit_of("300 K") == "K"
    assert value_of("12.5 mm/s") == "12.5" and unit_of("12.5 mm/s") == "mm/s"
    assert value_of("-3.2e-4 V") == "-3.2e-4" and unit_of("-3.2e-4 V") == "V"
    assert value_of("300K") == "300" and unit_of("300K") == "K"  # no space
    # pure number → value only, no unit
    assert value_of("300") == "300" and unit_of("300") == ""
    # no leading number → neither
    assert value_of("K") == "" and unit_of("K") == ""
    assert value_of("") == "" and unit_of("") == ""
