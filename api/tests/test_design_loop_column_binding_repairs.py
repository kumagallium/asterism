"""Two ways a design can bind a column and still be invisible or unusable.

Live 2026-08-19, a 3001-point instrument sweep:

1. Every property was written as `object_template: "{col}"` with
   `object_type: literal` instead of `column: col`. Those compile to the same
   triples, but every screen and check that asks "which columns does this design
   use" looks for `column:` -- so the "column meanings" review, whose whole job
   is confirming what each column means, listed nothing at all and filed the
   columns under "IDs and fixed values that are added automatically".
2. The numeric columns carried `language: ja`. A number is in no language, and
   RDF allows a datatype OR a language but not both -- so the deterministic
   datatype repair could not apply and the untyped-numeric advisory kept coming
   back with nothing able to clear it.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from asterism_api.design_loop import _evaluate

_CSV = b"k,angle,note\n1,20.0,\xe3\x81\x82\n2,20.5,\xe3\x81\x84\n3,21.0,\xe3\x81\x86\n"


def _spec(prop_block: str) -> str:
    return (
        "## Schema proposal\n\n### 9. Declarative mapping spec\n\n"
        "```yaml\n"
        "version: 1\n"
        "prefixes:\n"
        '  ex: "https://ns.invalid/ns#"\n'
        '  exr: "https://ns.invalid/r/"\n'
        "maps:\n"
        "  - name: m\n"
        "    source: data.csv\n"
        "    subject:\n"
        '      template: "exr:m/{k}"\n'
        "      classes: [ex:M]\n"
        "    properties:\n" + prop_block + "```\n"
    )


def _props(ir_yaml: str) -> list[dict]:
    return yaml.safe_load(ir_yaml)["maps"][0]["properties"]


def test_a_template_that_is_only_a_placeholder_becomes_a_column(tmp_path: Path) -> None:
    (tmp_path / "data.csv").write_bytes(_CSV)
    _, ir_yaml, _ = _evaluate(
        _spec(
            "      - predicate: ex:note\n"
            '        object_template: "{note}"\n'
            '        object_type: "literal"\n'
        ),
        tmp_path,
    )
    prop = _props(ir_yaml)[0]
    assert prop["column"] == "note"
    assert "object_template" not in prop
    assert "object_type" not in prop  # a bare column is a literal by definition


def test_a_template_that_builds_a_value_is_left_alone(tmp_path: Path) -> None:
    """Text around the placeholder means the template makes something new."""
    (tmp_path / "data.csv").write_bytes(_CSV)
    _, ir_yaml, _ = _evaluate(
        _spec(
            "      - predicate: ex:note\n"
            '        object_template: "note-{note}"\n'
            '        object_type: "literal"\n'
        ),
        tmp_path,
    )
    assert _props(ir_yaml)[0]["object_template"] == "note-{note}"


def test_a_template_that_builds_an_iri_is_left_alone(tmp_path: Path) -> None:
    """Without `object_type: literal` the template makes an IRI out of the cell,
    which `column:` cannot express (the compiler refuses a bare column as IRI)."""
    (tmp_path / "data.csv").write_bytes(_CSV)
    _, ir_yaml, _ = _evaluate(
        _spec("      - predicate: ex:link\n        object_template: \"exr:x/{k}\"\n"),
        tmp_path,
    )
    assert _props(ir_yaml)[0]["object_template"] == "exr:x/{k}"


def test_a_language_tag_on_numbers_is_removed_so_the_datatype_can_land(tmp_path: Path) -> None:
    (tmp_path / "data.csv").write_bytes(_CSV)
    _, ir_yaml, issues = _evaluate(
        _spec("      - predicate: ex:angle\n        column: angle\n        language: ja\n"),
        tmp_path,
    )
    prop = _props(ir_yaml)[0]
    assert "language" not in prop
    assert prop["datatype"] == "xsd:double"
    assert not any("untyped literal" in i.message for i in issues)


def test_a_language_tag_on_text_is_kept(tmp_path: Path) -> None:
    """The tag is wrong for numbers, not for words."""
    (tmp_path / "data.csv").write_bytes(_CSV)
    _, ir_yaml, _ = _evaluate(
        _spec("      - predicate: ex:note\n        column: note\n        language: ja\n"),
        tmp_path,
    )
    assert _props(ir_yaml)[0]["language"] == "ja"


def test_both_together_leave_a_typed_column(tmp_path: Path) -> None:
    """The shape the live design was in: bound the long way AND tagged."""
    (tmp_path / "data.csv").write_bytes(_CSV)
    _, ir_yaml, issues = _evaluate(
        _spec(
            "      - predicate: ex:angle\n"
            '        object_template: "{angle}"\n'
            '        object_type: "literal"\n'
            "        language: ja\n"
        ),
        tmp_path,
    )
    prop = _props(ir_yaml)[0]
    assert prop["column"] == "angle"
    assert prop["datatype"] == "xsd:double"
    assert "language" not in prop
    # The advisory that could not be cleared while both were true is gone.
    assert not any("untyped literal" in i.message for i in issues)
