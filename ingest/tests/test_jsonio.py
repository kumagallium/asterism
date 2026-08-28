"""Unit tests for asterism._jsonio.loads_relaxed — the sole ast.literal_eval entry
point in the Tier 0 library. Covers the happy path (JSON, Python literal repr),
the two DoS bounds (size / nesting depth), that the depth scan cannot be
evaded by quoting tricks, and that no input can reach code execution.
"""

from __future__ import annotations

from asterism._jsonio import (
    _MAX_DEPTH,
    _MAX_INPUT_BYTES,
    _max_nesting_depth,
    loads_relaxed,
)


def test_valid_json_passthrough() -> None:
    assert loads_relaxed('{"a": 1}') == {"a": 1}
    assert loads_relaxed("[1, 2, 3]") == [1, 2, 3]
    assert loads_relaxed('"hello"') == "hello"


def test_python_dict_repr() -> None:
    # single-quoted keys/strings, as produced by pandas DataFrame.to_csv() for a
    # dict-valued column
    assert loads_relaxed("{'lattice': {'a': 3.33}}") == {"lattice": {"a": 3.33}}


def test_nested_python_repr_real_data_shape() -> None:
    text = (
        "{'@module': 'pymatgen.core.structure', "
        "'lattice': {'a': 3.33, 'matrix': [[1,0,0],[0,1,0],[0,0,1]]}, "
        "'sites': [{'label': 'In'}]}"
    )
    data = loads_relaxed(text)
    assert data["@module"] == "pymatgen.core.structure"
    assert data["lattice"]["a"] == 3.33
    assert data["lattice"]["matrix"] == [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    assert data["sites"] == [{"label": "In"}]


def test_oversized_input_rejected() -> None:
    huge = "[" + "1," * (_MAX_INPUT_BYTES) + "1]"
    assert loads_relaxed(huge) is None


def test_over_deep_nesting_rejected_without_raising() -> None:
    deep = "[" * 100 + "]" * 100
    assert loads_relaxed(deep) is None


def test_depth_scan_counts_brackets_inside_strings_too() -> None:
    """The scan deliberately counts every bracket, quoted or not.

    A quote-aware scan would have to re-implement Python's string lexer; getting
    that wrong lets an attacker hide real nesting behind a mis-parsed quote (see
    :func:`test_depth_cap_survives_quote_confusion`). Over-counting is the safe
    direction: balanced punctuation inside a string still parses, because it
    does not accumulate depth.
    """
    balanced = "{'cif': '(x, y, z) (a, b, c)'}"
    assert loads_relaxed(balanced) == {"cif": "(x, y, z) (a, b, c)"}
    # ...but a long run of *unmatched* opening brackets in a string is refused.
    assert loads_relaxed("{'cif': '" + "(" * 100 + "'}") is None


def test_depth_cap_survives_quote_confusion() -> None:
    """Real nesting must not hide behind a quote the scanner mis-reads.

    A triple-quoted chunk holding a lone apostrophe desynchronizes any naive
    "toggle on every quote" tracker, leaving it stuck "inside a string" for the
    rest of the input. A scan that trusted that state reports depth 1 here and
    hands 150 levels of real nesting to ``ast.literal_eval``.
    """
    n = 150
    attack = "{'cif': " + "'" * 3 + "it's fine" + "'" * 3 + ", 'm': " + "[" * n + "]" * n + "}"
    assert _max_nesting_depth(attack) > _MAX_DEPTH
    assert loads_relaxed(attack) is None


def test_depth_cap_boundary() -> None:
    """Exactly at the cap parses; one level past it is refused."""
    assert loads_relaxed("[" * _MAX_DEPTH + "]" * _MAX_DEPTH) is not None
    assert loads_relaxed("[" * (_MAX_DEPTH + 1) + "]" * (_MAX_DEPTH + 1)) is None


def test_code_execution_attempts_return_none() -> None:
    assert loads_relaxed("__import__('os').system('echo pwned')") is None
    assert loads_relaxed("[].__class__") is None
    assert loads_relaxed("open('/etc/passwd').read()") is None


def test_malformed_input_returns_none() -> None:
    assert loads_relaxed("{'a': ") is None
    assert loads_relaxed("not json or python") is None


def test_blank_input_returns_none() -> None:
    assert loads_relaxed("") is None
    assert loads_relaxed("   ") is None
    assert loads_relaxed(None) is None  # type: ignore[arg-type]
