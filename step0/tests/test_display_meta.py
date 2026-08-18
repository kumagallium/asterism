"""Human-typed column meanings and units (kantan ADR K8 / KZ-B-05).

A column's MEANING and the UNIT it is in are knowledge the person who took the
measurement holds. Until now the only way to record them was a free-text note →
an LLM refine → a rewrite of the whole design → a re-ingest; weak models were
observed ignoring the note, or obeying it and breaking something else. They are
display metadata on the §9 spec, so the correction is deterministic: set the
field, splice the block, re-project. These tests pin that it changes ONLY that,
and that a later AI round cannot take it back (the re-assertion path).
"""
from __future__ import annotations

import pytest
import yaml

from asterism_step0.staged_propose import apply_display_meta, apply_display_meta_to_document

_IR = {
    "version": 1,
    "prefixes": {"ex": "https://ns.invalid/ns#"},
    "maps": [
        {
            "name": "measurement",
            "source": "data.csv",
            "subject": {"template": "ex:m/{id}", "classes": ["ex:Measurement"]},
            "properties": [
                {"predicate": "ex:hasSeebeck", "column": "S", "datatype": "xsd:double"},
                {"predicate": "ex:hasTemp", "column": "T", "unit": "degC"},
            ],
        }
    ],
}


def _doc(ir: dict) -> str:
    body = yaml.safe_dump(ir, sort_keys=False, allow_unicode=True)
    return (
        "# Title\n\nprose\n\n### 9. Declarative mapping spec\n\n"
        f"```yaml\n{body}```\n\n### tail\nkeep me\n"
    )


def test_sets_label_and_unit_on_the_named_row() -> None:
    out, changed = apply_display_meta(
        _IR, [{"predicate": "ex:hasSeebeck", "label": "ゼーベック係数", "unit": "µV/K"}]
    )
    row = out["maps"][0]["properties"][0]
    assert row["label"] == "ゼーベック係数"
    assert row["unit"] == "µV/K"
    assert changed == ["S"]
    # Nothing else moved: the value binding and its datatype are untouched.
    assert row["column"] == "S" and row["datatype"] == "xsd:double"
    assert out["maps"][0]["properties"][1] == _IR["maps"][0]["properties"][1]


def test_expanded_iri_matches_the_curie_the_design_wrote() -> None:
    """The client shows expanded IRIs; the design stores CURIEs, and K13 may have
    re-derived the prefix since. Matching on the term's last segment keeps the
    edit landing on the right row either way."""
    out, changed = apply_display_meta(
        _IR, [{"predicate": "https://ns.invalid/ns#hasTemp", "label": "測定温度"}]
    )
    assert changed == ["T"]
    assert out["maps"][0]["properties"][1]["label"] == "測定温度"


def test_column_narrows_when_one_predicate_is_bound_twice() -> None:
    ir = {
        **_IR,
        "maps": [
            {
                **_IR["maps"][0],
                "properties": [
                    {"predicate": "ex:hasValue", "column": "A"},
                    {"predicate": "ex:hasValue", "column": "B"},
                ],
            }
        ],
    }
    edits = [{"predicate": "ex:hasValue", "column": "B", "unit": "K"}]
    out, changed = apply_display_meta(ir, edits)
    assert changed == ["B"]
    assert "unit" not in out["maps"][0]["properties"][0]
    assert out["maps"][0]["properties"][1]["unit"] == "K"


def test_empty_string_clears_a_wrong_unit_and_none_leaves_it_alone() -> None:
    cleared, changed = apply_display_meta(_IR, [{"predicate": "ex:hasTemp", "unit": ""}])
    assert changed == ["T"]
    assert "unit" not in cleared["maps"][0]["properties"][1]
    # `None` is "the client did not touch this field" — not "clear it".
    same, nothing = apply_display_meta(_IR, [{"predicate": "ex:hasTemp", "unit": None}])
    assert nothing == []
    assert same["maps"][0]["properties"][1]["unit"] == "degC"


def test_no_matching_row_changes_nothing() -> None:
    out, changed = apply_display_meta(_IR, [{"predicate": "ex:notThere", "label": "x"}])
    assert changed == []
    assert out == dict(_IR)


def test_document_splice_touches_only_section_nine() -> None:
    doc = _doc(_IR)
    out, changed = apply_display_meta_to_document(
        doc, [{"predicate": "ex:hasSeebeck", "label": "ゼーベック係数", "unit": "µV/K"}]
    )
    assert changed == ["S"]
    assert out.startswith("# Title\n\nprose")
    assert out.rstrip().endswith("keep me")
    ir = yaml.safe_load(out.split("```yaml\n", 1)[1].split("```", 1)[0])
    assert ir["maps"][0]["properties"][0]["label"] == "ゼーベック係数"


def test_document_splice_is_idempotent() -> None:
    """The same edits re-applied after an AI round must be a no-op, not a churn:
    this is the function the refine path calls to re-assert what a human said."""
    edits = [{"predicate": "ex:hasSeebeck", "label": "ゼーベック係数"}]
    once, _ = apply_display_meta_to_document(_doc(_IR), edits)
    twice, changed = apply_display_meta_to_document(once, edits)
    assert changed == []
    assert twice == once


def test_legacy_design_without_a_mapping_spec_says_so() -> None:
    with pytest.raises(ValueError, match="no mapping spec"):
        apply_display_meta_to_document("# Title\n\nno section nine here\n", [{"predicate": "x"}])


def test_unreadable_section_nine_is_a_valueerror_not_a_crash() -> None:
    """The refine tail re-asserts human meanings on WHATEVER the round produced,
    and an unparseable §9 is a routine weak-model outcome. It has to arrive as
    the same ValueError a legacy design does — the caller suppresses that and
    lets the normal validation report the broken spec, instead of the whole
    refine job dying on a YAML error nobody can act on."""
    broken = (
        "# Title\n\n### 9. Declarative mapping spec\n\n"
        "```yaml\nmaps:\n  - name: m\n   source: [unclosed\n```\n"
    )
    with pytest.raises(ValueError):
        apply_display_meta_to_document(broken, [{"predicate": "ex:hasSeebeck", "label": "x"}])
