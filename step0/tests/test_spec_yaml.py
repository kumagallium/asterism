"""The mapping-spec YAML loader: YAML 1.2 booleans, so a column named ``No``
survives (live 2026-08-18: ``transform: {No: No}`` arrived as ``{False: False}``
and the validator's ``transform['False']`` message was undecodable to the model
— three "AI に直してもらう" clicks changed nothing)."""
from __future__ import annotations

import pytest

from asterism_step0.mapping_ir import MappingIRParseError, parse_mapping_ir
from asterism_step0.spec_yaml import describe_bare_scalar, dump_spec_yaml, load_spec_yaml

yaml = pytest.importorskip("yaml")


def test_yaml_11_booleans_stay_strings_but_true_false_do_not() -> None:
    doc = "a: No\nb: yes\nc: Off\nd: on\ne: n\nf: Y\nt: true\nF: FALSE\ni: 23\nx: 1.5\nz: ~\n"
    got = load_spec_yaml(doc)
    assert got["a"] == "No" and got["b"] == "yes" and got["c"] == "Off" and got["d"] == "on"
    assert got["e"] == "n" and got["f"] == "Y"
    assert got["t"] is True and got["F"] is False  # the real booleans the IR uses
    assert got["i"] == 23 and got["x"] == 1.5 and got["z"] is None
    # and PyYAML's own SafeLoader is untouched (other callers keep 1.1 semantics)
    assert yaml.safe_load("a: No")["a"] is False


def test_bare_no_column_parses_as_the_column_named_no() -> None:
    """The exact live shape: unquoted ``No`` in a template placeholder, a
    property column and a transform key. All three must read as the string."""
    spec = (
        "version: 1\n"
        "prefixes:\n  ex: https://ns.invalid/ns#\n  exr: https://ns.invalid/r/\n"
        "maps:\n"
        "  - name: card\n    source: xrd.txt\n"
        "    subject:\n      template: exr:card/{No}\n      classes: [ex:Card]\n"
        "    properties:\n"
        "      - predicate: ex:number\n        column: No\n"
    )
    ir = parse_mapping_ir(spec)
    assert ir.maps[0].properties[0].column == "No"


def test_a_number_looking_header_gets_the_quoting_hint() -> None:
    """A header YAML still types (``column: 2023`` → int) is explained WITH the
    fix, not just "(got 2023)"."""
    spec = (
        "version: 1\nprefixes:\n  ex: https://ns.invalid/ns#\n"
        "maps:\n  - name: m\n    source: d.csv\n"
        "    subject:\n      template: ex:m/{SID}\n      classes: [ex:T]\n"
        "    properties:\n      - predicate: ex:year\n        column: 2023\n"
    )
    with pytest.raises(MappingIRParseError) as exc:
        parse_mapping_ir(spec)
    msg = " ".join(exc.value.issues)
    assert "got 2023" in msg
    assert "read the bare scalar as a number" in msg
    assert "'2023'" in msg  # the paste-ready quoted form


def test_describe_bare_scalar_is_silent_for_strings_and_none() -> None:
    assert describe_bare_scalar("x") == ""
    assert describe_bare_scalar(None) == ""
    assert "boolean" in describe_bare_scalar(True)


def test_boolean_typed_fields_still_accept_the_yaml_11_spellings() -> None:
    """The flip side of the 1.2 loader: a column named ``No`` must stay a
    string, but ``collapse: no`` is unambiguous and was silently accepted
    before. Coercing it here keeps the loader change from costing an LLM round
    over punctuation."""
    from asterism_step0.spec_yaml import coerce_bool

    assert coerce_bool(True) is True and coerce_bool(False) is False
    for word in ("no", "No", "NO", "off", "Off", "n", "false", "FALSE"):
        assert coerce_bool(word) is False, word
    for word in ("yes", "Yes", "on", "y", "true", "True"):
        assert coerce_bool(word) is True, word
    # Not boolean at all → None, so the caller still reports its own issue.
    assert coerce_bool("maybe") is None
    assert coerce_bool(1) is None
    assert coerce_bool(None) is None


def test_dump_spec_yaml_quotes_yaml_11_bool_lookalikes_key_and_value() -> None:
    """The dump side of the Norway problem (2026-08-25 real-user incident):
    dump -> load must round-trip a ``No``/``Yes``/``On``/``Off`` column name
    unharmed, whether it is a mapping KEY (``transform: {No: slug}``) or a
    plain VALUE (a human-typed label that happens to read as ``No``) — and it
    must survive even a bare ``yaml.safe_load`` elsewhere in the stack, not
    just this file's own :func:`load_spec_yaml`."""
    data = {
        "transform": {"No": "slug", "Yes": "slug"},
        "label_no": "No",
        "label_on": "On",
        "label_off": "off",
        "label_y": "y",
        "plain": "a normal value",
    }
    out = dump_spec_yaml(data, sort_keys=False, allow_unicode=True)
    assert load_spec_yaml(out) == data
    assert yaml.safe_load(out) == data  # even the UNPATCHED 1.1 loader survives


def test_collapse_written_as_no_is_not_a_new_design_issue() -> None:
    """End-to-end: the whole point is that this spec parses clean."""
    spec = (
        "version: 1\nprefixes:\n  ex: https://ns.invalid/ns#\n"
        "maps:\n  - name: m\n    source: d.txt\n"
        "    subject:\n      template: ex:m/{No}\n      classes: [ex:T]\n"
        "    properties:\n      - predicate: ex:n\n        column: No\n"
        "        fallback: yes\n"
        "dialects:\n  d.txt:\n    encoding: utf-8\n    delimiter: ','\n"
        "    collapse: no\n    skip_rows: 0\n"
    )
    ir = parse_mapping_ir(spec)
    assert ir.maps[0].properties[0].column == "No"
    assert ir.maps[0].properties[0].fallback is True
    assert ir.dialects["d.txt"].collapse is False
