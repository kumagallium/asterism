"""Tests for the output-contract layer of asterism.query_tools (object-cards-ui
PR A, ADR ``docs/architecture/object-cards-ui.md``): ``output_kind`` / ``role`` /
``quantity_kind`` / ``unit`` on a declared tool's ``result.item``, the role
rules per output_kind (:func:`validate_roles`), the structural inference of an
unannotated declaration (:func:`infer_output_kind`), the read-time annotation
step (:func:`annotate_output_kind`), and the lint warning for a missing unit.

Fabricated declarations below use two unrelated, deliberately generic domains
(a library catalogue and weather-station readings) — the engine and this
contract are schema-agnostic and must never need to know what a column MEANS.
"""
from __future__ import annotations

import pytest
import yaml

from asterism.datasets import datasets_root
from asterism.query_tools import (
    DECLARABLE_OUTPUT_KINDS,
    OUTPUT_KINDS,
    ROLE_RULES,
    QueryToolError,
    annotate_output_kind,
    infer_output_kind,
    lint_query_tool,
    load_query_tools,
    parse_query_tools,
    synthesize_query_tools_from_trial_queries,
    validate_roles,
)


def _doc(query: str, params: list | None = None, **kw) -> dict:
    return {
        "tools": [
            {
                "name": "t",
                "title": "T",
                "query": query,
                "parameters": params or [],
                **kw,
            }
        ]
    }


# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------


def test_output_kinds_and_declarable_output_kinds() -> None:
    assert OUTPUT_KINDS[-1] == "flow"
    assert "flow" not in DECLARABLE_OUTPUT_KINDS
    assert set(DECLARABLE_OUTPUT_KINDS) == set(OUTPUT_KINDS) - {"flow"}


def test_role_rules_cover_every_declarable_kind() -> None:
    # facts is intentionally unrestricted but still present in the table.
    assert set(ROLE_RULES) >= set(DECLARABLE_OUTPUT_KINDS)


# ---------------------------------------------------------------------------
# backward compatibility (§6.1)
# ---------------------------------------------------------------------------


def test_output_kind_omitted_defaults_to_facts() -> None:
    tools = parse_query_tools(_doc("SELECT ?s WHERE { ?s ?p ?o }"))
    assert tools[0].output_kind == "facts"


def test_item_map_without_new_keys_gains_no_extra_keys() -> None:
    # The pre-existing {"var", "number"} shape must not grow keys just because
    # the contract now exists — every old strict-equality test depends on it.
    doc = _doc(
        "SELECT ?s ?n WHERE { ?s ?p ?n }",
        result={"item": {"book_iri": "s", "loan_count": {"var": "n", "number": True}}},
    )
    tool = parse_query_tools(doc)[0]
    assert tool.item["book_iri"] == {"var": "s", "number": False}
    assert tool.item["loan_count"] == {"var": "n", "number": True}


def test_omitted_output_kind_skips_role_validation() -> None:
    # No output_kind key at all -> stays facts and is NEVER role-checked, even
    # with a role that would violate some other kind's rules.
    doc = _doc(
        "SELECT ?s WHERE { ?s ?p ?o }",
        result={"item": {"book_iri": {"var": "s", "role": "subject"}}},
    )
    tools = parse_query_tools(doc)
    assert tools[0].output_kind == "facts"


def test_item_map_carries_role_quantity_kind_unit_when_declared() -> None:
    doc = _doc(
        "SELECT ?s ?n WHERE { ?s ?p ?n } LIMIT 1",
        output_kind="quantity",
        result={
            "item": {
                "reading": {
                    "var": "n",
                    "number": True,
                    "role": "value",
                    "quantity_kind": "quantitykind:Mass",
                    "unit": "unit:KiloGM",
                }
            }
        },
    )
    tool = parse_query_tools(doc)[0]
    assert tool.item["reading"] == {
        "var": "n",
        "number": True,
        "role": "value",
        "quantity_kind": "quantitykind:Mass",
        "unit": "unit:KiloGM",
    }


# ---------------------------------------------------------------------------
# invalid output_kind / flow / invalid role -> QueryToolError (§1)
# ---------------------------------------------------------------------------


def test_parse_rejects_unknown_output_kind() -> None:
    with pytest.raises(QueryToolError, match="output_kind"):
        parse_query_tools(_doc("SELECT ?s WHERE { ?s ?p ?o }", output_kind="nonsense"))


def test_parse_rejects_flow_output_kind() -> None:
    # flow cannot be declared in Phase 1 — it only comes from prov_graph.
    with pytest.raises(QueryToolError, match="output_kind"):
        parse_query_tools(_doc("SELECT ?s WHERE { ?s ?p ?o }", output_kind="flow"))


def test_parse_rejects_unknown_role() -> None:
    doc = _doc(
        "SELECT ?s WHERE { ?s ?p ?o }",
        result={"item": {"book_iri": {"var": "s", "role": "nope"}}},
    )
    with pytest.raises(QueryToolError, match="role"):
        parse_query_tools(doc)


# ---------------------------------------------------------------------------
# role-rule violations, one case per output_kind (§1's table)
# ---------------------------------------------------------------------------


def test_validate_roles_quantity_requires_value() -> None:
    doc = _doc(
        "SELECT ?s WHERE { ?s ?p ?o } LIMIT 1",
        output_kind="quantity",
        result={"item": {"book_iri": {"var": "s", "role": "subject"}}},
    )
    with pytest.raises(QueryToolError, match="value"):
        parse_query_tools(doc)


def test_validate_roles_series_requires_x_and_y() -> None:
    doc = _doc(
        "SELECT ?day ?readings WHERE { ?day ?p ?readings }",
        output_kind="series",
        result={"item": {"day": {"var": "day", "role": "x"}}},
    )
    with pytest.raises(QueryToolError, match="y"):
        parse_query_tools(doc)


def test_validate_roles_pairs_requires_x_and_y() -> None:
    doc = _doc(
        "SELECT ?a ?b WHERE { ?a ?p ?b }",
        output_kind="pairs",
        result={"item": {"humidity": {"var": "b", "role": "y"}}},
    )
    with pytest.raises(QueryToolError, match="x"):
        parse_query_tools(doc)


def test_validate_roles_ranked_requires_subject_and_value() -> None:
    doc = _doc(
        "SELECT ?n WHERE { ?s ?p ?n } ORDER BY DESC(?n)",
        output_kind="ranked",
        result={"item": {"loan_count": {"var": "n", "number": True, "role": "value"}}},
    )
    with pytest.raises(QueryToolError, match="subject"):
        parse_query_tools(doc)


def test_validate_roles_breakdown_requires_category_and_count() -> None:
    doc = _doc(
        "SELECT ?kind WHERE { ?s a ?kind }",
        output_kind="breakdown",
        result={"item": {"kind": {"var": "kind", "role": "category"}}},
    )
    with pytest.raises(QueryToolError, match="count"):
        parse_query_tools(doc)


def test_validate_roles_duplicate_role_is_error() -> None:
    doc = _doc(
        "SELECT ?a ?b ?n WHERE { ?a ?p ?n . ?b ?p ?n }",
        output_kind="ranked",
        result={
            "item": {
                "book_iri": {"var": "a", "role": "subject"},
                "author_iri": {"var": "b", "role": "subject"},
                "loan_count": {"var": "n", "number": True, "role": "value"},
            }
        },
    )
    with pytest.raises(QueryToolError, match="subject"):
        parse_query_tools(doc)


def test_validate_roles_role_not_allowed_for_kind_is_error() -> None:
    doc = _doc(
        "SELECT ?s ?n WHERE { ?s ?p ?n } LIMIT 1",
        output_kind="quantity",
        result={
            "item": {
                "loan_count": {"var": "n", "number": True, "role": "value"},
                "kind": {"var": "s", "role": "category"},  # not in quantity's table row
            }
        },
    )
    with pytest.raises(QueryToolError, match="category"):
        parse_query_tools(doc)


def test_validate_roles_series_role_may_repeat() -> None:
    # 'series' is the one role allowed to appear on more than one column.
    errs = validate_roles(
        "series",
        {
            "day": {"role": "x"},
            "count_a": {"role": "y"},
            "station_a": {"role": "series"},
            "station_b": {"role": "series"},
        },
    )
    assert errs == []


def test_validate_roles_facts_is_unrestricted() -> None:
    # facts allows any role, any number of times — no violation possible.
    assert validate_roles("facts", {"a": {"role": "value"}, "b": {"role": "value"}}) == []


# ---------------------------------------------------------------------------
# infer_output_kind: the 4 rules, on fabricated declarations
# ---------------------------------------------------------------------------


def test_infer_rule1_breakdown() -> None:
    raw = {
        "query": (
            "SELECT ?genre (COUNT(DISTINCT ?book) AS ?n) WHERE "
            "{ ?book ex:genre ?genre } GROUP BY ?genre"
        ),
        "result": {"item": {"genre": "genre", "loan_count": {"var": "n", "number": True}}},
    }
    kind, roles = infer_output_kind(raw)
    assert kind == "breakdown"
    assert roles == {"genre": "category", "loan_count": "count"}


def test_infer_rule2_quantity() -> None:
    raw = {
        "query": (
            "SELECT ?station_iri ?temp WHERE { ?station_iri ex:temp ?temp } "
            "ORDER BY DESC(?temp) LIMIT 1"
        ),
        "result": {"item": {"station_iri": "station_iri", "temp": {"var": "temp", "number": True}}},
    }
    kind, roles = infer_output_kind(raw)
    assert kind == "quantity"
    assert roles == {"temp": "value", "station_iri": "subject"}


def test_infer_rule2_quantity_without_iri_column_omits_subject() -> None:
    raw = {
        "query": "SELECT ?temp WHERE { ?s ex:temp ?temp } ORDER BY DESC(?temp) LIMIT 1",
        "result": {"item": {"temp": {"var": "temp", "number": True}}},
    }
    kind, roles = infer_output_kind(raw)
    assert kind == "quantity"
    assert roles == {"temp": "value"}


def test_infer_rule3_ranked() -> None:
    raw = {
        "query": (
            "SELECT ?book_iri ?loans WHERE { ?book_iri ex:loans ?loans } "
            "ORDER BY DESC(?loans) LIMIT 20"
        ),
        "result": {"item": {"book_iri": "book_iri", "loans": {"var": "loans", "number": True}}},
    }
    kind, roles = infer_output_kind(raw)
    assert kind == "ranked"
    assert roles == {"book_iri": "subject", "loans": "value"}


def test_infer_rule3_does_not_fire_on_bare_order_by() -> None:
    # ORDER BY ?var (no DESC/ASC) must NOT be read as the ranked signal.
    raw = {
        "query": "SELECT ?book_iri ?year WHERE { ?book_iri ex:year ?year } ORDER BY ?year",
        "result": {"item": {"book_iri": "book_iri", "year": {"var": "year", "number": True}}},
    }
    kind, roles = infer_output_kind(raw)
    assert kind == "facts" and roles == {}


def test_infer_rule4_facts_two_numeric_columns() -> None:
    # 2+ numeric columns is a judgment call -> stays facts, never guessed.
    raw = {
        "query": (
            "SELECT ?station_iri ?temp ?humidity WHERE "
            "{ ?station_iri ex:temp ?temp ; ex:humidity ?humidity }"
        ),
        "result": {
            "item": {
                "station_iri": "station_iri",
                "temp": {"var": "temp", "number": True},
                "humidity": {"var": "humidity", "number": True},
            }
        },
    }
    kind, roles = infer_output_kind(raw)
    assert kind == "facts" and roles == {}


def test_infer_rule4_facts_no_numeric_columns() -> None:
    raw = {
        "query": "SELECT ?book_iri ?title WHERE { ?book_iri ex:title ?title }",
        "result": {"item": {"book_iri": "book_iri", "title": "title"}},
    }
    kind, roles = infer_output_kind(raw)
    assert kind == "facts" and roles == {}


# ---------------------------------------------------------------------------
# infer_output_kind against the actually-shipped datasets — the fixed table
# from the contract memo §2. Deliberately indexed by POSITION in each file
# rather than by tool name, so this file never has to spell out a shipped
# dataset's domain-specific tool names (§0: no domain nouns in new test code).
# ---------------------------------------------------------------------------

_EXPECTED_INFERRED_KIND_BY_POSITION: dict[str, list[str]] = {
    "starrydata": ["ranked", "facts"],
    "materials_project": ["facts", "facts", "facts", "facts"],  # last: 2 numeric columns
    "papers": ["facts", "facts", "facts", "facts"],
}


def test_infer_matches_expected_table_for_shipped_datasets() -> None:
    root = datasets_root()
    for dataset_name, expected_kinds in _EXPECTED_INFERRED_KIND_BY_POSITION.items():
        path = root / dataset_name / "query_tools.yaml"
        raw_doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        tools = raw_doc["tools"]
        assert len(tools) == len(expected_kinds), (
            f"{dataset_name}: shipped tool count changed ({len(tools)}), "
            f"update the expected-kind table alongside it"
        )
        for position, (raw_tool, expected_kind) in enumerate(
            zip(tools, expected_kinds, strict=True)
        ):
            kind, _roles = infer_output_kind(raw_tool)
            assert kind == expected_kind, (
                f"{dataset_name}[{position}] ({raw_tool.get('name')!r}): "
                f"expected {expected_kind}, got {kind}"
            )


def test_infer_matches_expected_for_registry_synthesized_tools() -> None:
    # The registry auto-generates 3 tools (ASK-34) whose RAW shape (pre the
    # explicit output_kind this PR now writes into them) the inference rules
    # must also classify correctly, since infer_output_kind ignores whatever
    # output_kind key a raw dict already carries.
    trial = {
        "available": True,
        "classes": [{"iri": "https://ex/lib#Book", "n": 3}],
        "range": {"predicate_iri": "https://ex/lib#pages", "n": 3, "min": "1", "max": "9"},
        "top": {"predicate_iri": "https://ex/lib#pages", "value": "9", "subject_iri": "https://ex/lib/book/1"},
    }
    tools = synthesize_query_tools_from_trial_queries(trial)
    by_name = {t["name"]: t for t in tools}
    assert infer_output_kind(by_name["counts_by_kind"])[0] == "breakdown"
    assert infer_output_kind(by_name["value_range"])[0] == "facts"
    assert infer_output_kind(by_name["top_value"])[0] == "quantity"


# ---------------------------------------------------------------------------
# annotate_output_kind
# ---------------------------------------------------------------------------


def test_annotate_output_kind_explicit_stays_as_is() -> None:
    raw = {"name": "t", "output_kind": "facts", "result": {"item": {"a": "x"}}}
    out = annotate_output_kind(raw)
    assert out["output_kind"] == "facts"
    assert out["output_kind_inferred"] is False
    assert out["result"] == {"item": {"a": "x"}}


def test_annotate_output_kind_infers_and_splices_roles() -> None:
    raw = {
        "name": "t",
        "query": (
            "SELECT ?genre (COUNT(DISTINCT ?book) AS ?n) WHERE "
            "{ ?book ex:genre ?genre } GROUP BY ?genre"
        ),
        "result": {"item": {"genre": "genre", "loan_count": {"var": "n", "number": True}}},
    }
    out = annotate_output_kind(raw)
    assert out["output_kind"] == "breakdown"
    assert out["output_kind_inferred"] is True
    assert out["result"]["item"]["genre"] == {"var": "genre", "number": False, "role": "category"}
    assert out["result"]["item"]["loan_count"] == {"var": "n", "number": True, "role": "count"}
    # never mutates the caller's dict
    assert raw["result"]["item"]["genre"] == "genre"


def test_annotate_output_kind_facts_leaves_item_untouched() -> None:
    raw = {"name": "t", "query": "SELECT ?s WHERE { ?s ?p ?o }", "result": {"item": {"s": "s"}}}
    out = annotate_output_kind(raw)
    assert out["output_kind"] == "facts"
    assert out["output_kind_inferred"] is True
    assert out["result"]["item"]["s"] == "s"


def test_annotate_output_kind_never_writes_to_disk(tmp_path) -> None:
    # Read-time only: annotate_output_kind takes/returns dicts, no filesystem.
    raw = {"name": "t", "query": "SELECT ?s WHERE { ?s ?p ?o }", "result": {}}
    before = set(tmp_path.iterdir())
    annotate_output_kind(raw)
    assert set(tmp_path.iterdir()) == before


# ---------------------------------------------------------------------------
# synthesize_query_tools_from_trial_queries: explicit output_kind + roles
# ---------------------------------------------------------------------------

_LIB_TRIAL = {
    "available": True,
    "classes": [
        {"iri": "https://ex/lib#Book", "n": 12},
        {"iri": "https://ex/lib#Patron", "n": 4},
    ],
    "range": {
        "predicate_iri": "https://ex/lib#pages",
        "n": 12,
        "min": "50",
        "max": "900",
        "label": "ページ数",
    },
    "top": {
        "predicate_iri": "https://ex/lib#pages",
        "value": "900",
        "subject_iri": "https://ex/lib/book/7",
        "label": "ページ数",
        "unit": "unit:PAGE",
    },
}


def test_synthesize_counts_by_kind_output_kind_and_roles() -> None:
    tools = synthesize_query_tools_from_trial_queries(_LIB_TRIAL)
    by_name = {t["name"]: t for t in tools}
    counts = by_name["counts_by_kind"]
    assert counts["output_kind"] == "breakdown"
    assert counts["result"]["item"]["class_iri"]["role"] == "category"
    assert counts["result"]["item"]["count"]["role"] == "count"


def test_synthesize_value_range_output_kind_facts_no_roles() -> None:
    tools = synthesize_query_tools_from_trial_queries(_LIB_TRIAL)
    by_name = {t["name"]: t for t in tools}
    value_range = by_name["value_range"]
    assert value_range["output_kind"] == "facts"
    for spec in value_range["result"]["item"].values():
        if isinstance(spec, dict):
            assert "role" not in spec


def test_synthesize_top_value_output_kind_and_roles_and_unit_passthrough() -> None:
    tools = synthesize_query_tools_from_trial_queries(_LIB_TRIAL)
    by_name = {t["name"]: t for t in tools}
    top_value = by_name["top_value"]
    assert top_value["output_kind"] == "quantity"
    assert top_value["result"]["item"]["subject_iri"]["role"] == "subject"
    assert top_value["result"]["item"]["value"]["role"] == "value"
    # trial["top"]["unit"] was set -> copied through verbatim.
    assert top_value["result"]["item"]["value"]["unit"] == "unit:PAGE"
    assert "quantity_kind" not in top_value["result"]["item"]["value"]


def test_synthesize_top_value_no_unit_when_trial_has_none() -> None:
    top_sans_unit = {k: v for k, v in _LIB_TRIAL["top"].items() if k != "unit"}
    trial_no_unit = {**_LIB_TRIAL, "top": top_sans_unit}
    tools = synthesize_query_tools_from_trial_queries(trial_no_unit)
    by_name = {t["name"]: t for t in tools}
    assert "unit" not in by_name["top_value"]["result"]["item"]["value"]


def test_synthesized_tools_all_parse_and_role_check_clean() -> None:
    tools = synthesize_query_tools_from_trial_queries(_LIB_TRIAL)
    parsed = parse_query_tools({"tools": tools})
    assert {t.name for t in parsed} == {"counts_by_kind", "value_range", "top_value"}


# ---------------------------------------------------------------------------
# lint: quantity/series/pairs value/x/y missing quantity_kind or unit -> warning
# ---------------------------------------------------------------------------


def test_lint_warns_on_missing_unit_for_quantity_value() -> None:
    doc = _doc(
        "SELECT ?s ?n WHERE { ?s ?p ?n } LIMIT 1",
        output_kind="quantity",
        result={"item": {"reading": {"var": "n", "number": True, "role": "value"}}},
    )
    tool = parse_query_tools(doc)[0]
    lint = lint_query_tool(tool)
    assert lint.ok  # a warning, never an error
    assert any("quantity_kind" in w or "unit" in w for w in lint.warnings)


def test_lint_silent_when_unit_declared() -> None:
    doc = _doc(
        "SELECT ?s ?n WHERE { ?s ?p ?n } LIMIT 1",
        output_kind="quantity",
        result={
            "item": {
                "reading": {"var": "n", "number": True, "role": "value", "unit": "unit:KiloGM"}
            }
        },
    )
    tool = parse_query_tools(doc)[0]
    lint = lint_query_tool(tool)
    assert lint.ok and not lint.warnings


def test_lint_silent_when_quantity_kind_declared() -> None:
    doc = _doc(
        "SELECT ?s ?n WHERE { ?s ?p ?n } LIMIT 1",
        output_kind="quantity",
        result={
            "item": {
                "reading": {
                    "var": "n",
                    "number": True,
                    "role": "value",
                    "quantity_kind": "quantitykind:Mass",
                }
            }
        },
    )
    tool = parse_query_tools(doc)[0]
    lint = lint_query_tool(tool)
    assert lint.ok and not lint.warnings


def test_lint_does_not_warn_for_ranked_or_facts() -> None:
    # ranked/facts are intentionally excluded from the unit-warning rule
    # (§1's deviation from the handoff: unit can be parameter-dependent).
    doc = _doc(
        "SELECT ?s ?n WHERE { ?s ?p ?n } ORDER BY DESC(?n)",
        output_kind="ranked",
        result={
            "item": {
                "book_iri": {"var": "s", "role": "subject"},
                "loans": {"var": "n", "number": True, "role": "value"},
            }
        },
    )
    tool = parse_query_tools(doc)[0]
    lint = lint_query_tool(tool)
    assert lint.ok and not lint.warnings


def test_shipped_ranked_tools_lint_clean_including_new_warning_rule() -> None:
    # Every shipped tool explicitly annotated ``ranked`` must still lint
    # clean now that lint also checks output_kind — matches
    # test_shipped_dataset_content_lints_clean's existing green bar,
    # re-asserted here for this contract specifically.
    for name in ("starrydata", "materials_project"):
        for tool in load_query_tools(name):
            if tool.output_kind == "ranked":
                lint = lint_query_tool(tool)
                assert lint.ok and not lint.warnings, (name, tool.name, lint.errors, lint.warnings)
