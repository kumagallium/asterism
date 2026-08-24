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

from asterism_step0.staged_propose import (
    apply_column_decisions,
    apply_display_meta,
    apply_display_meta_to_document,
    remove_stale_column_includes_from_document,
)

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


def test_source_narrows_the_same_predicate_and_column_in_two_files() -> None:
    ir = {
        **_IR,
        "maps": [
            {**_IR["maps"][0], "name": "a", "source": "a.csv"},
            {**_IR["maps"][0], "name": "b", "source": "b.csv"},
        ],
    }
    out, changed = apply_display_meta(
        ir,
        [
            {
                "predicate": "ex:hasSeebeck",
                "source": "a.csv",
                "column": "S",
                "label": "only A",
            }
        ],
    )
    assert changed == ["S"]
    assert out["maps"][0]["properties"][0]["label"] == "only A"
    assert "label" not in out["maps"][1]["properties"][0]


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


def test_human_include_adds_a_typed_raw_passthrough_property() -> None:
    out, changed = apply_column_decisions(
        _IR,
        [
            {
                "source": "data.csv",
                "map": "measurement",
                "column": "pressure",
                "action": "include",
                "label": "圧力",
                "unit": "Pa",
            }
        ],
        source_columns={"data.csv": {"pressure": "xsd:double"}},
    )
    row = out["maps"][0]["properties"][-1]
    assert changed == ["pressure"]
    assert row == {
        "predicate": "ex:hasPressure",
        "column": "pressure",
        "fallback": True,
        "label": "圧力",
        "datatype": "xsd:double",
        "unit": "Pa",
    }


def test_human_include_is_idempotent_and_updates_its_display_metadata() -> None:
    decision = {
        "source": "data.csv",
        "map": "measurement",
        "column": "pressure",
        "action": "include",
        "label": "Pressure",
    }
    once, _ = apply_column_decisions(
        _IR, [decision], source_columns={"data.csv": {"pressure": "xsd:double"}}
    )
    twice, changed = apply_column_decisions(
        once,
        [{**decision, "label": "圧力", "unit": "Pa"}],
        source_columns={"data.csv": {"pressure": "xsd:double"}},
    )
    assert changed == ["pressure"]
    rows = [p for p in twice["maps"][0]["properties"] if p.get("column") == "pressure"]
    assert len(rows) == 1
    assert rows[0]["label"] == "圧力" and rows[0]["unit"] == "Pa"


def test_human_include_survives_a_map_rename_and_restores_its_datatype() -> None:
    renamed = {
        **_IR,
        "maps": [{**_IR["maps"][0], "name": "measurement_v2"}],
    }
    out, changed = apply_column_decisions(
        renamed,
        [
            {
                "source": "data.csv",
                "map": "measurement",
                "map_class": "ex:Measurement",
                "column": "pressure",
                "action": "include",
                "label": "Pressure",
                "datatype": "xsd:double",
            }
        ],
    )
    row = out["maps"][0]["properties"][-1]
    assert changed == ["pressure"]
    assert row["column"] == "pressure" and row["datatype"] == "xsd:double"


def test_human_include_without_a_class_falls_back_to_the_sole_map_on_its_source() -> None:
    """``_decision_map`` tries, in order: ① map_class ② exact name ③ a source
    with exactly one map. A decision with no ``map_class`` that also lost its
    exact name (a rename) must still resolve — through ③ alone, since ① never
    even runs without a class to match."""
    renamed = {
        **_IR,
        "maps": [{**_IR["maps"][0], "name": "measurement_v2"}],
    }
    out, changed = apply_column_decisions(
        renamed,
        [
            {
                "source": "data.csv",
                "map": "measurement",
                "column": "pressure",
                "action": "include",
                "label": "Pressure",
            }
        ],
        source_columns={"data.csv": {"pressure": "xsd:double"}},
    )
    assert changed == ["pressure"]
    row = out["maps"][0]["properties"][-1]
    assert row["column"] == "pressure" and row["datatype"] == "xsd:double"


def test_human_include_uses_its_class_when_multiple_maps_share_a_source() -> None:
    ir = {
        **_IR,
        "maps": [
            {
                **_IR["maps"][0],
                "name": "sample_v2",
                "subject": {"template": "ex:s/{id}", "classes": ["ex:Sample"]},
            },
            {
                **_IR["maps"][0],
                "name": "measurement_v2",
            },
        ],
    }
    out, changed = apply_column_decisions(
        ir,
        [
            {
                "source": "data.csv",
                "map": "measurement",
                "map_class": "https://ns.invalid/ns#Measurement",
                "column": "pressure",
                "action": "include",
                "label": "Pressure",
            }
        ],
        source_columns={"data.csv": {"pressure": "xsd:double"}},
    )
    assert changed == ["pressure"]
    assert not any(p.get("column") == "pressure" for p in out["maps"][0]["properties"])
    assert out["maps"][1]["properties"][-1]["column"] == "pressure"


def test_human_include_prefers_its_class_when_an_old_map_name_is_reused() -> None:
    ir = {
        **_IR,
        "maps": [
            {
                **_IR["maps"][0],
                "name": "measurement",
                "subject": {"template": "ex:s/{id}", "classes": ["ex:Sample"]},
                "properties": [
                    *_IR["maps"][0]["properties"],
                    {
                        "predicate": "ex:hasPressureOld",
                        "column": "pressure",
                        "fallback": True,
                    },
                ],
            },
            {
                **_IR["maps"][0],
                "name": "measurement_v2",
            },
        ],
    }
    out, changed = apply_column_decisions(
        ir,
        [
            {
                "source": "data.csv",
                "map": "measurement",
                "map_class": "ex:Measurement",
                "column": "pressure",
                "action": "include",
                "label": "Pressure",
            }
        ],
        source_columns={"data.csv": {"pressure": "xsd:double"}},
    )
    assert changed == ["pressure"]
    assert not any(p.get("column") == "pressure" for p in out["maps"][0]["properties"])
    assert out["maps"][1]["properties"][-1]["column"] == "pressure"


def test_stale_include_cleanup_removes_only_the_human_fallback_row() -> None:
    included, _ = apply_column_decisions(
        _IR,
        [
            {
                "source": "data.csv",
                "map": "measurement",
                "column": "old_note",
                "action": "include",
                "label": "Old note",
            }
        ],
        source_columns={"data.csv": {"old_note": "xsd:string"}},
    )
    out, changed = remove_stale_column_includes_from_document(
        _doc(included),
        [
            {
                "source": "data.csv",
                "map": "measurement",
                "column": "old_note",
                "action": "include",
                "label": "Old note",
            }
        ],
    )
    ir = yaml.safe_load(out.split("```yaml\n", 1)[1].split("```", 1)[0])
    assert changed == ["old_note"]
    assert all(p.get("column") != "old_note" for p in ir["maps"][0]["properties"])
    assert any(p.get("column") == "S" for p in ir["maps"][0]["properties"])


def test_human_include_uses_a_hash_suffix_for_a_predicate_collision() -> None:
    ir = {
        **_IR,
        "maps": [
            {
                **_IR["maps"][0],
                "properties": [{"predicate": "ex:hasAlpha", "column": "other"}],
            }
        ],
    }
    out, _ = apply_column_decisions(
        ir,
        [
            {
                "source": "data.csv",
                "map": "measurement",
                "column": "alpha",
                "action": "include",
                "label": "Alpha",
            }
        ],
        source_columns={"data.csv": {"alpha": "xsd:string"}},
    )
    assert out["maps"][0]["properties"][-1]["predicate"].startswith("ex:hasAlpha_")


def test_human_include_avoids_a_predicate_used_by_another_map() -> None:
    ir = {
        **_IR,
        "maps": [
            _IR["maps"][0],
            {
                "name": "sample",
                "source": "sample.csv",
                "subject": {"template": "ex:s/{id}", "classes": ["ex:Sample"]},
                "properties": [{"predicate": "ex:hasPressure", "column": "other"}],
            },
        ],
    }
    out, _ = apply_column_decisions(
        ir,
        [
            {
                "source": "data.csv",
                "map": "measurement",
                "column": "pressure",
                "action": "include",
                "label": "Pressure",
            }
        ],
        source_columns={"data.csv": {"pressure": "xsd:double"}},
    )
    assert out["maps"][0]["properties"][-1]["predicate"].startswith("ex:hasPressure_")


def test_human_include_detects_a_collision_through_an_alias_prefix() -> None:
    ir = {
        **_IR,
        "prefixes": {
            **_IR["prefixes"],
            "alias": _IR["prefixes"]["ex"],
        },
        "maps": [
            {
                **_IR["maps"][0],
                "properties": [{"predicate": "alias:hasPressure", "column": "other"}],
            }
        ],
    }
    out, _ = apply_column_decisions(
        ir,
        [
            {
                "source": "data.csv",
                "map": "measurement",
                "column": "pressure",
                "action": "include",
                "label": "Pressure",
            }
        ],
        source_columns={"data.csv": {"pressure": "xsd:double"}},
    )
    assert out["maps"][0]["properties"][-1]["predicate"].startswith("ex:hasPressure_")


def test_display_meta_follows_a_human_include_when_its_predicate_gets_a_suffix() -> None:
    ir = {
        **_IR,
        "maps": [
            {
                **_IR["maps"][0],
                "properties": [
                    *_IR["maps"][0]["properties"],
                    {"predicate": "ex:hasPressure", "column": "other"},
                ],
            }
        ],
    }
    restored, _ = apply_column_decisions(
        ir,
        [
            {
                "source": "data.csv",
                "map": "measurement",
                "column": "pressure",
                "action": "include",
                "label": "Old meaning",
                "unit": "m",
            }
        ],
        source_columns={"data.csv": {"pressure": "xsd:double"}},
    )
    out, changed = apply_display_meta(
        restored,
        [
            {
                "predicate": "ex:hasPressure",
                "source": "data.csv",
                "column": "pressure",
                "label": "New meaning",
                "unit": "cm",
            }
        ],
    )
    row = next(p for p in out["maps"][0]["properties"] if p.get("column") == "pressure")
    assert changed == ["pressure"]
    assert row["predicate"].startswith("ex:hasPressure_")
    assert row["label"] == "New meaning" and row["unit"] == "cm"


def test_human_exclude_needs_no_owning_map_and_leaves_the_ir_unchanged() -> None:
    out, changed = apply_column_decisions(
        _IR, [{"source": "notes.csv", "column": "unused", "action": "exclude"}],
        source_columns={"notes.csv": {"unused": "xsd:string"}},
    )
    assert changed == []
    assert out == _IR


def test_human_exclude_removes_a_property_added_by_a_later_ai_rewrite() -> None:
    rewritten = {
        **_IR,
        "maps": [
            {
                **_IR["maps"][0],
                "properties": [
                    *_IR["maps"][0]["properties"],
                    {"predicate": "ex:hasSecret", "column": "secret"},
                ],
            }
        ],
    }
    out, changed = apply_column_decisions(
        rewritten,
        [{"source": "data.csv", "column": "secret", "action": "exclude"}],
        source_columns={"data.csv": {"secret": "xsd:string"}},
    )
    assert changed == ["secret"]
    assert all(p.get("column") != "secret" for p in out["maps"][0]["properties"])


def test_human_include_never_matches_a_same_local_class_in_another_namespace() -> None:
    ir = {
        **_IR,
        "prefixes": {
            **_IR["prefixes"],
            "other": "https://other.invalid/ontology#",
        },
        "maps": [
            {
                **_IR["maps"][0],
                "name": "other_thing",
                "subject": {"template": "ex:o/{id}", "classes": ["other:Measurement"]},
            },
            {
                **_IR["maps"][0],
                "name": "measurement_v2",
            },
        ],
    }
    out, changed = apply_column_decisions(
        ir,
        [
            {
                "source": "data.csv",
                "map": "measurement",
                "map_class": "https://ns.invalid/ns#Measurement",
                "column": "pressure",
                "action": "include",
                "label": "Pressure",
            }
        ],
        source_columns={"data.csv": {"pressure": "xsd:double"}},
    )
    assert changed == ["pressure"]
    assert not any(p.get("column") == "pressure" for p in out["maps"][0]["properties"])
    assert out["maps"][1]["properties"][-1]["column"] == "pressure"


@pytest.mark.parametrize(
    "decision",
    [
        {"source": "other.csv", "map": "measurement", "column": "S", "action": "exclude"},
    ],
)
def test_human_decision_rejects_an_invalid_map_or_source(decision: dict) -> None:
    with pytest.raises(ValueError):
        apply_column_decisions(_IR, [decision], source_columns={"data.csv": {"S": "xsd:double"}})


def test_human_decision_rejects_an_ambiguous_renamed_map() -> None:
    ir = {
        **_IR,
        "maps": [
            _IR["maps"][0],
            {**_IR["maps"][0], "name": "other"},
        ],
    }
    with pytest.raises(ValueError, match="owner is ambiguous"):
        apply_column_decisions(
            ir,
            [
                {
                    "source": "data.csv",
                    "map": "old",
                    "column": "S",
                    "action": "include",
                    "label": "Signal",
                }
            ],
            source_columns={"data.csv": {"S": "xsd:double"}},
        )
