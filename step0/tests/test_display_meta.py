"""Human-typed column meanings and units (kantan ADR K8 / KZ-B-05).

A column's MEANING and the UNIT it is in are knowledge the person who took the
measurement holds. Until now the only way to record them was a free-text note →
an LLM refine → a rewrite of the whole design → a re-ingest; weak models were
observed ignoring the note, or obeying it and breaking something else. They are
display metadata on the §9 spec, so the correction is deterministic: set the
field, splice the block, re-project. These tests pin that it changes ONLY that,
and that a later AI round cannot take it back (the re-assertion path).
"""
# This module's prose is Japanese: full-width parentheses / slashes are
# intentional, not ASCII look-alikes (same posture as describe.py).
# ruff: noqa: RUF002
from __future__ import annotations

import pytest
import yaml

from asterism_step0.spec_yaml import load_spec_yaml
from asterism_step0.staged_propose import (
    apply_column_decisions,
    apply_column_meanings,
    apply_column_meanings_to_document,
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
    return _doc_from_yaml(body)


def _doc_from_yaml(body: str) -> str:
    """Same shell as :func:`_doc`, but for LITERAL YAML text — used when the
    test needs control over exactly what bytes land in §9 (e.g. an unquoted
    ``No:`` a model actually wrote, which ``yaml.safe_dump`` would auto-quote
    and so could never reproduce)."""
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


# A §9 block written the way a model actually writes it — an unquoted `No`
# column header, exactly the shape the real-user incident (2026-08-25) hit.
# NOT run through yaml.safe_dump (which would auto-quote 'No' and hide the
# bug this pins): this is the literal text a bare `yaml.safe_load` misreads
# as `{False: slug}`.
_NORWAY_SECTION_NINE = (
    "version: 1\n"
    "prefixes:\n  ex: https://ns.invalid/ns#\n"
    "maps:\n"
    "  - name: pattern\n"
    "    source: xrd.txt\n"
    "    subject:\n"
    "      template: ex:pattern/{No}\n"
    "      transform:\n"
    "        No: slug\n"
    "    properties:\n"
    "      - predicate: ex:hasName\n"
    "        constant: placeholder-name\n"
    "        datatype: xsd:string\n"
)


def test_display_meta_survives_a_no_named_column_norway_problem() -> None:
    """Real-user incident (2026-08-25, desktop v0.21.0): editing a column's
    meaning spliced §9 through a bare ``yaml.safe_load``, turning the `No`
    key into the YAML 1.1 boolean False. The re-serialized `false: slug` no
    longer compiled (empty RML -> 409 "no longer compiles" -> the client
    reverted the just-typed meaning). This must not happen — the `No` key must
    survive the round trip, and the edit must still land."""
    doc = _doc_from_yaml(_NORWAY_SECTION_NINE)
    out, changed = apply_display_meta_to_document(
        doc, [{"predicate": "ex:hasName", "label": "パターン名"}]
    )
    assert changed == ["ex:hasName"]
    block = out.split("```yaml\n", 1)[1].split("```", 1)[0]
    assert "false: slug" not in block.lower()  # the exact corruption, absent
    ir = load_spec_yaml(block)
    assert ir["maps"][0]["subject"]["transform"] == {"No": "slug"}
    assert ir["maps"][0]["properties"][0]["label"] == "パターン名"


_TWO_MAPS_SHARED_PREDICATE = {
    "version": 1,
    "prefixes": {"ex": "https://ns.invalid/ns#"},
    "maps": [
        {
            "name": "pattern",
            "source": "xrd.txt",
            "subject": {"template": "ex:pattern/{No}"},
            "properties": [{"predicate": "ex:hasName", "constant": "a"}],
        },
        {
            "name": "peak",
            "source": "xrd.txt",
            "subject": {"template": "ex:peak/{No}/{2theta}"},
            "properties": [{"predicate": "ex:hasName", "constant": "b"}],
        },
    ],
}


def test_map_narrows_an_edit_to_one_of_two_maps_sharing_a_predicate() -> None:
    """Real-user incident, second half: predicate+source alone matched BOTH
    ``pattern`` and ``peak``'s ``ex:hasName`` row (``changed`` came back with
    the same predicate twice). ``map`` is what disambiguates — the client is
    now expected to send it (KantanWizard.tsx commitMeta)."""
    edits = [{"predicate": "ex:hasName", "map": "peak", "label": "ピーク名"}]
    out, changed = apply_display_meta(_TWO_MAPS_SHARED_PREDICATE, edits)
    assert changed == ["ex:hasName"]
    assert "label" not in out["maps"][0]["properties"][0]  # pattern: untouched
    assert out["maps"][1]["properties"][0]["label"] == "ピーク名"  # peak: set


def test_without_map_a_shared_predicate_bleeds_into_every_matching_map() -> None:
    """Documents the bleed itself (same fixture, no ``map``) — the exact shape
    observed live: ``changed == ['ex:hasName', 'ex:hasName']``, one edit
    landing on two unrelated maps."""
    edits = [{"predicate": "ex:hasName", "label": "名前"}]
    out, changed = apply_display_meta(_TWO_MAPS_SHARED_PREDICATE, edits)
    assert changed == ["ex:hasName", "ex:hasName"]
    assert out["maps"][0]["properties"][0]["label"] == "名前"
    assert out["maps"][1]["properties"][0]["label"] == "名前"


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


# ---------------------------------------------------------------------------
# "own": which of two maps keeps a column both of them record (ADR G1 / K2)
# ---------------------------------------------------------------------------

# The live XRD shape (2026-08-28): a file-scoped card AND the per-row peaks,
# both transcribing the preamble's broadcast columns. Eight AI rounds could not
# clear it, because "which entity is this column ABOUT" is not derivable — it is
# the human's call, and this is the deterministic way to record it.
_DUP_IR = {
    "version": 1,
    "prefixes": {"xr": "https://ns.invalid/xrd#", "xrr": "https://ns.invalid/xrd/r/"},
    "maps": [
        {
            "name": "card",
            "source": "xrd.txt",
            "subject": {"template": "xrr:card/{No}", "classes": ["xr:Card"]},
            "properties": [
                {"predicate": "xr:volume", "column": "Volume", "datatype": "xsd:double"},
                {
                    "predicate": "xr:hasPeak",
                    "object_template": "xrr:peak/{No}/{(hkl)}",
                    "object_type": "iri",
                },
            ],
        },
        {
            "name": "peak",
            "source": "xrd.txt",
            "subject": {"template": "xrr:peak/{No}/{(hkl)}", "classes": ["xr:Peak"]},
            "properties": [
                {"predicate": "xr:twoTheta", "column": "2theta", "datatype": "xsd:double"},
                {"predicate": "xr:volume", "column": "Volume", "datatype": "xsd:double"},
            ],
        },
    ],
}

_OWN_VOLUME_ON_CARD = {
    "source": "xrd.txt",
    "column": "Volume",
    "action": "own",
    "map": "card",
    "map_class": "xr:Card",
}


def _columns(ir: dict, map_name: str) -> list[str]:
    m = next(m for m in ir["maps"] if m["name"] == map_name)
    return [str(p.get("column") or "") for p in m["properties"] if p.get("column")]


def test_own_keeps_the_column_on_the_chosen_map_and_drops_the_twin() -> None:
    out, changed = apply_column_decisions(_DUP_IR, [_OWN_VOLUME_ON_CARD])
    assert changed == ["Volume"]
    assert _columns(out, "card") == ["Volume"]
    assert _columns(out, "peak") == ["2theta"]


def test_own_is_idempotent() -> None:
    once, _ = apply_column_decisions(_DUP_IR, [_OWN_VOLUME_ON_CARD])
    twice, changed = apply_column_decisions(once, [_OWN_VOLUME_ON_CARD])
    assert changed == []  # nothing left to drop — the verdict already holds
    assert twice["maps"] == once["maps"]


def test_own_survives_a_map_rename() -> None:
    # A later AI round renamed the card map; the persisted verdict recovers by
    # source + class, exactly like a persisted include does.
    renamed = {
        **_DUP_IR,
        "maps": [{**_DUP_IR["maps"][0], "name": "card_v2"}, _DUP_IR["maps"][1]],
    }
    out, changed = apply_column_decisions(renamed, [_OWN_VOLUME_ON_CARD])
    assert changed == ["Volume"]
    assert _columns(out, "peak") == ["2theta"]


def test_own_never_deletes_the_last_copy_of_a_column() -> None:
    """Fail-safe: a rewrite that moved the column OFF the owner must not turn
    "keep it here" into "delete it everywhere"."""
    moved = {
        **_DUP_IR,
        "maps": [
            {**_DUP_IR["maps"][0], "properties": _DUP_IR["maps"][0]["properties"][1:]},
            _DUP_IR["maps"][1],
        ],
    }
    out, changed = apply_column_decisions(moved, [_OWN_VOLUME_ON_CARD])
    assert changed == []
    assert _columns(out, "peak") == ["2theta", "Volume"]


def test_own_leaves_the_link_to_the_other_entity_alone() -> None:
    """The join is declared with an object_template naming the same column;
    deleting it because it mentions the column would disconnect the design."""
    ir = {
        **_DUP_IR,
        "maps": [
            {
                **_DUP_IR["maps"][0],
                "properties": [
                    {"predicate": "xr:cardNo", "column": "No"},
                    *_DUP_IR["maps"][0]["properties"],
                ],
            },
            {
                **_DUP_IR["maps"][1],
                "properties": [
                    *_DUP_IR["maps"][1]["properties"],
                    {"predicate": "xr:peakCardNo", "column": "No"},
                    {
                        "predicate": "xr:ofCard",
                        "object_template": "xrr:card/{No}",
                        "object_type": "iri",
                    },
                ],
            },
        ],
    }
    out, changed = apply_column_decisions(
        ir, [{**_OWN_VOLUME_ON_CARD, "column": "No"}]
    )
    assert changed == ["No"]
    peak = next(m for m in out["maps"] if m["name"] == "peak")
    # the literal copy is gone, the link stays
    assert [p["predicate"] for p in peak["properties"]] == ["xr:twoTheta", "xr:volume", "xr:ofCard"]


def test_own_reaches_a_reshaped_transcription_but_not_a_derived_value() -> None:
    """The advisory counts a single-input function as that column's value, so
    the fix must reach it — and must NOT touch a value computed from several."""
    ir = {
        **_DUP_IR,
        "maps": [
            _DUP_IR["maps"][0],
            {
                **_DUP_IR["maps"][1],
                "properties": [
                    {
                        "predicate": "xr:volumeClean",
                        "column": "Volume",
                        "function": "number_clean",
                    },
                    {
                        "predicate": "xr:density",
                        "columns": ["Volume", "Mass"],
                        "function": "ratio",
                    },
                ],
            },
        ],
    }
    out, changed = apply_column_decisions(ir, [_OWN_VOLUME_ON_CARD])
    assert changed == ["Volume"]
    peak = next(m for m in out["maps"] if m["name"] == "peak")
    assert [p["predicate"] for p in peak["properties"]] == ["xr:density"]


def test_own_refuses_to_empty_a_map() -> None:
    ir = {
        **_DUP_IR,
        "maps": [
            _DUP_IR["maps"][0],
            {
                **_DUP_IR["maps"][1],
                "properties": [_DUP_IR["maps"][1]["properties"][1]],
            },
        ],
    }
    with pytest.raises(ValueError, match="only property"):
        apply_column_decisions(ir, [_OWN_VOLUME_ON_CARD])


def test_an_unknown_action_is_refused() -> None:
    with pytest.raises(ValueError, match="action must be"):
        apply_column_decisions(_DUP_IR, [{**_OWN_VOLUME_ON_CARD, "action": "delete"}])


# ---------------------------------------------------------------------------
# (source, column) の意味を設計へ写す — ADR meaning-before-identity §6。
# 意味は設計より前に決まるので、保管も (source, column) で行い、設計が組まれた
# あとに述語へ投影する。ここは投影側のテスト。
# ---------------------------------------------------------------------------

_TWO_FILE_IR = {
    "version": 1,
    "prefixes": {"ex": "https://ns.invalid/ns#"},
    "maps": [
        {
            "name": "measurement",
            "source": "data.csv",
            "subject": {"template": "ex:m/{id}", "classes": ["ex:Measurement"]},
            "properties": [
                {"predicate": "ex:hasSeebeck", "column": "S", "label": "AI が書いた意味"},
                {"predicate": "ex:hasTemp", "column": "T"},
                {"predicate": "ex:composed", "columns": ["S", "T"], "function": "concat"},
                {"predicate": "ex:ofSample", "object_template": "ex:sample/{id}"},
            ],
        },
        {
            "name": "other",
            "source": "other.csv",
            "subject": {"template": "ex:o/{id}", "classes": ["ex:Other"]},
            "properties": [{"predicate": "ex:hasS", "column": "S"}],
        },
    ],
}


def test_column_meanings_land_on_the_row_that_reads_that_column() -> None:
    out, changed = apply_column_meanings(
        _TWO_FILE_IR,
        [
            {"source": "data.csv", "column": "T", "label": "試料温度", "unit": "K"},
        ],
    )
    row = out["maps"][0]["properties"][1]
    assert row["label"] == "試料温度" and row["unit"] == "K"
    assert changed == ["data.csv:T"]


def test_column_meanings_win_over_what_the_model_wrote() -> None:
    """意味は列の性質で、生成ラウンドが決め直す側ではない（N6 と同じ姿勢）。"""
    out, _ = apply_column_meanings(
        _TWO_FILE_IR, [{"source": "data.csv", "column": "S", "label": "ゼーベック係数"}]
    )
    assert out["maps"][0]["properties"][0]["label"] == "ゼーベック係数"


def test_column_meanings_do_not_cross_files() -> None:
    """同じ見出しの列が 2 つのファイルにあっても、意味は書いたファイルにだけ載る。"""
    out, changed = apply_column_meanings(
        _TWO_FILE_IR, [{"source": "data.csv", "column": "S", "label": "ゼーベック係数"}]
    )
    assert changed == ["data.csv:S"]
    assert "label" not in out["maps"][1]["properties"][0]


def test_column_meanings_skip_rows_that_do_not_read_one_column() -> None:
    """意味は 1 列のもの。複数列の関数行やリンク行には載せない。"""
    out, changed = apply_column_meanings(
        _TWO_FILE_IR,
        [
            {"source": "data.csv", "column": "S", "label": "ゼーベック係数"},
            {"source": "data.csv", "column": "id", "label": "試料 ID"},
        ],
    )
    assert changed == ["data.csv:S"]
    assert "label" not in out["maps"][0]["properties"][2]  # columns: [S, T]
    assert "label" not in out["maps"][0]["properties"][3]  # object_template


def test_column_meanings_document_splice_is_idempotent() -> None:
    """再設計のたびに走る経路なので、2 度目は無変更でなければならない。"""
    meanings = [{"source": "data.csv", "column": "S", "label": "ゼーベック係数", "unit": "µV/K"}]
    once, changed = apply_column_meanings_to_document(_doc(_TWO_FILE_IR), meanings)
    assert changed == ["data.csv:S"]
    ir = yaml.safe_load(once.split("```yaml\n", 1)[1].split("```", 1)[0])
    assert ir["maps"][0]["properties"][0]["unit"] == "µV/K"
    twice, changed_again = apply_column_meanings_to_document(once, meanings)
    assert changed_again == [] and twice == once


def test_column_meanings_on_a_legacy_design_without_a_spec_says_so() -> None:
    with pytest.raises(ValueError):
        apply_column_meanings_to_document(
            "# Title\n\nno section nine here\n",
            [{"source": "data.csv", "column": "S", "label": "x"}],
        )

def test_column_meanings_absent_field_is_kept_and_an_empty_one_clears() -> None:
    """display-meta と同じ約束。単位を直しただけで意味が消えてはいけない。"""
    with_unit, _ = apply_column_meanings(
        _TWO_FILE_IR,
        [{"source": "data.csv", "column": "S", "label": "ゼーベック係数", "unit": "µV/K"}],
    )
    only_unit, changed = apply_column_meanings(
        with_unit, [{"source": "data.csv", "column": "S", "unit": "V/K"}]
    )
    row = only_unit["maps"][0]["properties"][0]
    assert row["label"] == "ゼーベック係数" and row["unit"] == "V/K"
    assert changed == ["data.csv:S"]
    cleared, _ = apply_column_meanings(
        only_unit, [{"source": "data.csv", "column": "S", "unit": ""}]
    )
    row = cleared["maps"][0]["properties"][0]
    assert row["label"] == "ゼーベック係数" and "unit" not in row


def test_column_meanings_take_the_last_word_field_by_field() -> None:
    """呼び出し側は「いまの状態」と「今回消したもの」を並べて渡す。あとが勝つ。"""
    out, _ = apply_column_meanings(
        _TWO_FILE_IR,
        [
            {"source": "data.csv", "column": "S", "label": "ゼーベック係数", "unit": "µV/K"},
            {"source": "data.csv", "column": "S", "unit": ""},
        ],
    )
    row = out["maps"][0]["properties"][0]
    assert row["label"] == "ゼーベック係数" and "unit" not in row
