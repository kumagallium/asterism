"""Unit tests for the parameterized Tier 0 primitive engines (asterism.primitives).

Covers the three primitives plus their safety properties: lookup miss / unsafe
table name, regex ReDoS guard / re2-only behavior, and template safe interpolation
(no format-string injection). All entry points are ``str -> str`` and return ``""``
for "no result", matching the rest of the Tier 0 library.
"""

from __future__ import annotations

import importlib

import pytest

from asterism import primitives
from asterism.primitives import (
    _MAX_REGEX_INPUT,
    LookupTableUnavailableError,
    RegexEngineUnavailableError,
    array_at,
    json_get,
    json_pluck,
    load_table,
    lookup,
    regex_extract,
    split,
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
    """A key the table does not contain is a fact ABOUT THE ROW, so it stays ""."""
    assert lookup("maybe", "bool") == ""
    assert lookup("atlantis", "country_iso3166") == ""
    # an empty cell has nothing to look up, whatever the table says
    assert lookup("", "bool") == ""


def test_lookup_unknown_table_raises() -> None:
    """A table this install does not have must be LOUD, never an empty string.

    The table name is a constant in the mapping, so a wrong one means every row
    loses its value. Degrading to "" made the whole column vanish from a
    materialized graph that still reported success — the silent drop the substrate
    exists to prevent. The message names the tables that DO exist so the mapping
    can be corrected without reading the source.
    """
    with pytest.raises(LookupTableUnavailableError, match="no_such_table"):
        lookup("Yes", "no_such_table")
    with pytest.raises(LookupTableUnavailableError, match="bool"):
        load_table("no_such_table")


def test_lookup_empty_table_name_raises() -> None:
    """An empty table constant is a broken mapping, not a row with no value."""
    with pytest.raises(LookupTableUnavailableError):
        lookup("Yes", "")


def test_lookup_rejects_unsafe_table_name() -> None:
    """A table name is a bare identifier; traversal / absolute paths never resolve.

    Rejected before the filesystem is touched (fail closed) AND loudly (fail
    visibly) — the two are not in tension.
    """
    for bad in ("../etc/passwd", "a/b", "..", "bool.yaml", "BOOL", "a b", "/abs"):
        with pytest.raises(LookupTableUnavailableError, match="unsafe"):
            load_table(bad)
        with pytest.raises(LookupTableUnavailableError, match="unsafe"):
            lookup("Yes", bad)


def test_lookup_malformed_table_raises(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """A table file that is not a YAML mapping is a packaging/content defect.

    Same reasoning as a missing file: an empty table answers "" for every row.
    """
    (tmp_path / "broken.yaml").write_text("- just\n- a\n- list\n", encoding="utf-8")
    monkeypatch.setattr(primitives, "_TABLES_DIR", tmp_path)
    primitives.load_table.cache_clear()
    try:
        with pytest.raises(LookupTableUnavailableError, match="not a mapping"):
            primitives.load_table("broken")
    finally:
        primitives.load_table.cache_clear()


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


def test_regex_extract_raises_when_engine_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing engine must be LOUD, never an empty string.

    "" is a claim about the row ("no match"); a missing google-re2 is a claim about
    the environment ("no row can be answered"). Returning "" for the second made
    every value of the column disappear from the materialized graph with no error —
    the silent-drop failure the substrate exists to prevent. Simulated by stubbing
    the cached module lookup, so the test runs whether or not re2 is installed.
    """
    monkeypatch.setattr(primitives, "_re2_module", lambda: None)
    with pytest.raises(RegexEngineUnavailableError, match="google-re2"):
        regex_extract("sample-300", r"(?P<v>[0-9]+)")


def test_regex_extract_engine_missing_still_short_circuits_empty_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No engine needed to know an empty cell / empty pattern has nothing to extract,
    so those stay "" rather than raising a spurious environment error."""
    monkeypatch.setattr(primitives, "_re2_module", lambda: None)
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


# ---- array_at ---------------------------------------------------------------


def test_array_at() -> None:
    assert array_at("[10, 20, 30]", "0") == "10"
    assert array_at("[10, 20, 30]", "1") == "20"
    assert array_at("[10, 20, 30]", "-1") == "30"  # negative index from the end
    assert array_at('["a", "b"]', "1") == "b"
    # out of range / non-integer / non-array / null element / empty → ""
    assert array_at("[10, 20]", "5") == ""
    assert array_at("[10, 20]", "x") == ""
    assert array_at('{"a": 1}', "0") == ""
    assert array_at("[null]", "0") == ""
    assert array_at("", "0") == ""
    assert array_at("[1]", "") == ""


# ---- split (multi-value → list, Morph-KGC explodes) -------------------------


def test_split_returns_list() -> None:
    # a list result is what Morph-KGC explodes into one triple per element
    assert split(",ci,us,", ",") == ["ci", "us"]  # wrapper commas / blanks dropped
    assert split("a; b ;c", ";") == ["a", "b", "c"]  # tokens trimmed
    assert split("single", ",") == ["single"]  # one token → one-element list
    # nothing to emit → None (Morph-KGC drops the row pre-explode; [] would NaN-crash)
    assert split("", ",") is None
    assert split("a,b", "") is None
    assert split(",,,", ",") is None  # all-blank → None


# ---- json_pluck (sub-field of each object in a JSON-string array → list) -----


def test_json_pluck() -> None:
    arr = '[{"given": "A", "family": "Adams"}, {"given": "B", "family": "Brown"}]'
    assert json_pluck(arr, "family") == ["Adams", "Brown"]
    assert json_pluck(arr, "given") == ["A", "B"]
    # objects missing the field (or with null / non-scalar value) are skipped
    assert json_pluck('[{"family": "X"}, {"given": "Y"}]', "family") == ["X"]
    assert json_pluck('[{"f": null}, {"f": "ok"}, {"f": [1]}]', "f") == ["ok"]
    # non-array / non-JSON / empty / empty-field / no matches → None (dropped pre-explode)
    assert json_pluck('{"family": "X"}', "family") is None
    assert json_pluck("not json", "family") is None
    assert json_pluck("", "family") is None
    assert json_pluck('[{"f": "x"}]', "") is None
    assert json_pluck('[{"given": "Y"}]', "family") is None  # no object has the field


# ---- json_get (dotted-path scalar out of a single JSON object) --------------


def test_json_get_basic() -> None:
    assert json_get('{"lattice": {"a": 3.33}}', "lattice.a") == "3.33"


def test_json_get_python_literal_repr() -> None:
    assert json_get("{'lattice': {'a': 3.33}}", "lattice.a") == "3.33"


def test_json_get_list_index() -> None:
    assert json_get('{"m": [[1,2],[3,4]]}', "m.1.0") == "3"
    assert json_get('{"m": [[1,2],[3,4]]}', "m.-1.0") == "3"  # negative index


def test_json_get_missing_or_mismatched() -> None:
    assert json_get('{"a": 1}', "b") == ""  # missing key
    assert json_get('{"a": [1, 2]}', "a.5") == ""  # out of range
    assert json_get('{"a": {"b": 1}}', "a.b.c") == ""  # descend into a scalar
    assert json_get('{"a": "x"}', "a.b") == ""  # type mismatch (str, not dict/list)


def test_json_get_scalar_only() -> None:
    assert json_get('{"a": {"b": 1}}', "a") == ""  # final value is a dict
    assert json_get('{"a": [1, 2]}', "a") == ""  # final value is a list
    assert json_get('{"a": null}', "a") == ""  # final value is None


def test_json_get_empty_input() -> None:
    assert json_get("", "a") == ""
    assert json_get('{"a": 1}', "") == ""


def test_json_get_no_attribute_access() -> None:
    """A path segment naming a Python attribute is just an absent dict key."""
    assert json_get('{"a": 1}', "__class__") == ""
    assert json_get('{"a": 1}', "a.__class__") == ""


def test_json_get_real_data_shape() -> None:
    text = (
        "{'@module': 'pymatgen.core.structure', "
        "'lattice': {'a': 3.33, 'volume': 37.0, 'matrix': [[1,0,0],[0,1,0],[0,0,1]]}, "
        "'sites': [{'label': 'In'}]}"
    )
    assert json_get(text, "lattice.a") == "3.33"
    assert json_get(text, "lattice.volume") == "37.0"
    assert json_get(text, "lattice.matrix.0.0") == "1"


def test_json_get_index_segment_is_a_plain_integer() -> None:
    """``int()`` also accepts " 1", "+1" and Python's "1_0" digit grouping —
    surprising readings of what is meant to be a plain index in a constant.
    Only an optional "-" followed by digits indexes into a list."""
    data = '{"m": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]}'
    assert json_get(data, "m.10") == "10"
    assert json_get(data, "m.-1") == "10"
    assert json_get(data, "m.1_0") == ""
    assert json_get(data, "m. 1") == ""
    assert json_get(data, "m.+1") == ""
