"""Unit tests for the parameterized Tier 0 primitive engines (asterism.primitives).

Covers the three primitives plus their safety properties: lookup miss / unsafe
table name, regex ReDoS guard / re2-only behavior, and template safe interpolation
(no format-string injection). All entry points are ``str -> str`` and return ``""``
for "no result", matching the rest of the Tier 0 library.
"""

from __future__ import annotations

import importlib

import pytest

from asterism.primitives import (
    _MAX_REGEX_INPUT,
    load_table,
    lookup,
    regex_extract,
    template,
)


def _re2_installed() -> bool:
    try:
        importlib.import_module("re2")
        return True
    except ImportError:
        return False


# ---- lookup -----------------------------------------------------------------


def test_lookup_seed_tables_hit() -> None:
    # bool / unit_alias are case-insensitive on the key; values keep their case.
    assert lookup("Yes", "bool") == "true"
    assert lookup("NO", "bool") == "false"
    assert lookup("Kelvin", "unit_alias") == "K"
    assert lookup("United States", "country_iso3166") == "US"
    assert lookup("usa", "country_iso3166") == "US"


def test_lookup_miss_returns_empty() -> None:
    assert lookup("maybe", "bool") == ""
    assert lookup("atlantis", "country_iso3166") == ""
    # empty value / empty table name → ""
    assert lookup("", "bool") == ""
    assert lookup("Yes", "") == ""


def test_lookup_unknown_table_returns_empty() -> None:
    assert lookup("Yes", "no_such_table") == ""


def test_lookup_rejects_unsafe_table_name() -> None:
    """A table name is a bare identifier; traversal / absolute paths never resolve."""
    for bad in ("../etc/passwd", "a/b", "..", "bool.yaml", "BOOL", "a b", "/abs"):
        assert load_table(bad) == {}
        assert lookup("Yes", bad) == ""


def test_lookup_is_case_insensitive_on_key() -> None:
    assert lookup("  united KINGDOM ", "country_iso3166") == "GB"


# ---- regex_extract ----------------------------------------------------------


@pytest.mark.skipif(not _re2_installed(), reason="google-re2 not installed")
def test_regex_extract_group_one() -> None:
    assert regex_extract("temp 300 K", r"(\d+)") == "300"
    # whole match when the pattern has no capture group
    assert regex_extract("ab 42 cd", r"\d+") == "42"


@pytest.mark.skipif(not _re2_installed(), reason="google-re2 not installed")
def test_regex_extract_named_group_v_preferred() -> None:
    # a named group `v` is the explicit extraction target, preferred over group 1
    assert regex_extract("temp 300 K", r"(?P<v>\d+)\s*(K|degC)") == "300"


@pytest.mark.skipif(not _re2_installed(), reason="google-re2 not installed")
def test_regex_extract_no_match_returns_empty() -> None:
    assert regex_extract("no digits here", r"\d+") == ""


@pytest.mark.skipif(not _re2_installed(), reason="google-re2 not installed")
def test_regex_extract_bad_pattern_returns_empty() -> None:
    # backreferences are an re-only construct that re2 rejects → "" (not a raise)
    assert regex_extract("aa", r"(a)\1") == ""


def test_regex_extract_empty_inputs_return_empty() -> None:
    # contract holds even without re2: empty value / pattern short-circuit to ""
    assert regex_extract("", r"\d+") == ""
    assert regex_extract("abc", "") == ""


@pytest.mark.skipif(not _re2_installed(), reason="google-re2 not installed")
def test_regex_extract_input_length_capped() -> None:
    pattern = r"(\d+)"
    over = "x" * (_MAX_REGEX_INPUT + 1)
    assert regex_extract(over, pattern) == ""
    # at the cap it still runs
    at_cap = "9" + "x" * (_MAX_REGEX_INPUT - 1)
    assert regex_extract(at_cap, pattern) == "9"


@pytest.mark.skipif(not _re2_installed(), reason="google-re2 not installed")
def test_regex_extract_redos_pattern_does_not_hang() -> None:
    """A classic catastrophic-backtracking pattern stays linear-time under re2.

    With the stdlib ``re`` engine ``(a+)+$`` against ``"a"*N + "!"`` blows up
    exponentially; re2 cannot backtrack, so this returns quickly. We assert both
    the result (no match → "") and that it completes well under a wall-clock
    budget that a backtracking engine would blow.
    """
    import time

    evil = "a" * 50 + "!"
    start = time.monotonic()
    assert regex_extract(evil, r"(a+)+$") == ""
    assert time.monotonic() - start < 1.0


# ---- template ---------------------------------------------------------------


def test_template_interpolates_positional_fields() -> None:
    assert template("{1}-{2}", "a", "b") == "a-b"
    assert template("{1}/{2}/{3}/{4}", "w", "x", "y", "z") == "w/x/y/z"


def test_template_missing_field_substitutes_empty() -> None:
    # field2 unset → "" in its slot; the rest of the template is preserved
    assert template("{1}-{2}", "a") == "a-"
    assert template("[{1}]", "") == "[]"


def test_template_empty_template_returns_empty() -> None:
    assert template("", "a", "b") == ""


def test_template_is_injection_safe() -> None:
    """No str.format / eval: only literal {1}..{4} tokens are substituted."""
    # attribute access via a format-string is inert (does not match {1})
    assert template("{1.__class__}", "x") == "{1.__class__}"
    # out-of-range / non-numeric tokens are left literal
    assert template("{0}-{5}-{x}", "a", "b") == "{0}-{5}-{x}"
    # a field value that itself looks like a token is NOT re-interpreted
    assert template("{1}{2}", "{2}", "B") == "{2}B"
