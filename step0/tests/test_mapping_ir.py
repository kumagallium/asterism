"""Unit tests for the Mapping IR parser + validators (ADR mapping-ir-compiler.md).

The parser is strict (unknown fields are errors — an LLM invention must fail
loudly) and collect-all (every issue in one raise, so the self-correction loop
can feed the complete list back in one round).
"""
from __future__ import annotations

import pytest

pytest.importorskip("yaml")

from asterism_step0.mapping_ir import (
    CatalogFunction,
    FunctionCatalog,
    MappingIRParseError,
    parse_mapping_ir,
    referenced_columns,
    validate_mapping_ir,
)

MINIMAL = """
version: 1
prefixes:
  ex: "https://example.org/ns#"
  exr: "https://example.org/r/"
maps:
  - name: thing
    source: data.csv
    subject:
      template: "exr:thing/{id}"
      classes: [ex:Thing]
    properties:
      - predicate: ex:name
        column: name
"""


def parse_issues(text: str) -> list[str]:
    with pytest.raises(MappingIRParseError) as exc:
        parse_mapping_ir(text)
    return exc.value.issues


def test_minimal_parses() -> None:
    ir = parse_mapping_ir(MINIMAL)
    assert ir.version == 1
    assert ir.prefixes["ex"] == "https://example.org/ns#"
    (m,) = ir.maps
    assert m.name == "thing"
    assert m.source == "data.csv"
    assert m.subject.template == "exr:thing/{id}"
    assert m.subject.classes == ("ex:Thing",)
    (p,) = m.properties
    assert p.predicate == "ex:name"
    assert p.column == "name"


def test_label_and_unit_are_display_metadata() -> None:
    """kantan-mode ADR K8: reviewer-facing meaning/unit ride on the property
    row (optional, display only)."""
    ok = MINIMAL.replace(
        "column: name",
        'column: name\n        label: "試料名"\n        unit: "µV/K"',
    )
    ir = parse_mapping_ir(ok)
    (p,) = ir.maps[0].properties
    assert p.label == "試料名"
    assert p.unit == "µV/K"


def test_label_and_unit_must_be_strings() -> None:
    bad = MINIMAL.replace(
        "column: name", "column: name\n        label: [x]\n        unit: 3"
    )
    issues = parse_issues(bad)
    assert any(".label must be a non-empty string" in i for i in issues)
    assert any(".unit must be a non-empty string" in i for i in issues)


def test_unit_is_sanitized_not_fatal() -> None:
    """Weak-model unit runaway (live dogfood 2026-07-23): `°C​Celsius` repeated
    43 times with zero-width spaces. Units are display metadata — sanitize
    (strip invisibles, collapse whitespace) and DROP implausibly long values
    instead of failing the design."""
    # Zero-width characters vanish; surrounding whitespace collapses.
    ok = MINIMAL.replace(
        "column: name",
        'column: name\n        unit: " µV​/K "',
    )
    (p,) = parse_mapping_ir(ok).maps[0].properties
    assert p.unit == "µV/K"

    # A runaway repetition is dropped (None), and the design still parses.
    runaway = "°C​Celsius" * 43
    ok2 = MINIMAL.replace(
        "column: name",
        f'column: name\n        unit: "{runaway}"',
    )
    (p2,) = parse_mapping_ir(ok2).maps[0].properties
    assert p2.unit is None

    # A normal unit passes through untouched.
    ok3 = MINIMAL.replace("column: name", 'column: name\n        unit: "Ohm m"')
    (p3,) = parse_mapping_ir(ok3).maps[0].properties
    assert p3.unit == "Ohm m"


def test_no_object_form_names_the_paste_ready_fix() -> None:
    """Weak-model family (live dogfood 2026-07-23): rows carrying ONLY
    predicate + unit — display metadata mistaken for the value. The bare
    "exactly one object form" message never lands on weak models; the issue
    must name the exact edit (add 'column:' with the header text)."""
    bad = MINIMAL.replace(
        "column: name", 'unit: "Ohm m"'
    )
    issues = parse_issues(bad)
    assert any("exactly one object form" in i for i in issues)
    assert any("add 'column:'" in i and "display metadata" in i for i in issues)


def test_yaml_error_is_one_issue() -> None:
    issues = parse_issues("version: 1\nmaps: [unclosed")
    assert len(issues) == 1
    assert "not valid YAML" in issues[0]


def test_version_required() -> None:
    issues = parse_issues(MINIMAL.replace("version: 1", "version: 2"))
    assert any("version must be 1" in i for i in issues)


def test_unknown_fields_are_errors_with_suggestion() -> None:
    bad = MINIMAL.replace("column: name", "column: name\n        datatyp: xsd:date")
    issues = parse_issues(bad)
    assert any("unknown field 'datatyp'" in i and "datatype" in i for i in issues)


def test_unknown_optional_field_gets_implicitness_hint() -> None:
    bad = MINIMAL.replace("column: name", "column: name\n        optional: true")
    issues = parse_issues(bad)
    assert any("unknown field 'optional'" in i and "drop the field" in i for i in issues)


def test_exactly_one_object_form() -> None:
    bad = MINIMAL.replace(
        "column: name", 'column: name\n        constant: "x"'
    )
    issues = parse_issues(bad)
    assert any("exactly one object form" in i for i in issues)

    none = MINIMAL.replace("        column: name\n", "")
    issues = parse_issues(none)
    assert any("exactly one object form" in i for i in issues)


def test_columns_and_args_require_function() -> None:
    bad = MINIMAL.replace(
        "column: name", "columns: [a, b]"
    )
    issues = parse_issues(bad)
    assert any("requires 'function'" in i for i in issues)


def test_function_requires_column() -> None:
    # function + constant is the combined-form error (targeted guidance)…
    bad = MINIMAL.replace(
        "column: name", 'constant: "x"\n        function: date_iso'
    )
    issues = parse_issues(bad)
    assert any("cannot be combined with object_template" in i for i in issues)
    # …while a function with NO object form at all gets the plain requirement.
    none = MINIMAL.replace("column: name", "function: date_iso")
    issues = parse_issues(none)
    assert any("requires 'column'" in i for i in issues)


def test_fn_prefix_is_stripped() -> None:
    ok = MINIMAL.replace(
        "column: name", "column: name\n        function: fn:date_iso"
    )
    ir = parse_mapping_ir(ok)
    assert ir.maps[0].properties[0].function == "date_iso"


def test_bare_column_iri_is_rejected_with_iri_safe_hint() -> None:
    bad = MINIMAL.replace(
        "column: name", "column: name\n        object_type: iri"
    )
    issues = parse_issues(bad)
    assert any("iri_safe" in i for i in issues)


def test_datatype_language_exclusive() -> None:
    bad = MINIMAL.replace(
        "column: name",
        "column: name\n        datatype: xsd:string\n        language: en",
    )
    issues = parse_issues(bad)
    assert any("cannot have both datatype and language" in i for i in issues)


def test_datatype_on_iri_rejected() -> None:
    bad = MINIMAL.replace(
        "column: name",
        'object_template: "exr:x/{id}"\n        datatype: xsd:string',
    )
    issues = parse_issues(bad)
    assert any("apply to literals" in i for i in issues)


def test_subject_needs_exactly_one_of_template_constant() -> None:
    bad = MINIMAL.replace(
        'template: "exr:thing/{id}"',
        'constant: "exr:one"\n      template: "exr:thing/{id}"',
    )
    issues = parse_issues(bad)
    assert any("exactly one of 'template'" in i for i in issues)


def test_subject_template_without_placeholder() -> None:
    bad = MINIMAL.replace('template: "exr:thing/{id}"', 'template: "exr:thing/fixed"')
    issues = parse_issues(bad)
    assert any("no {column} placeholder" in i for i in issues)


def test_undeclared_prefix_with_suggestion() -> None:
    bad = MINIMAL.replace("predicate: ex:name", "predicate: exx:name")
    issues = parse_issues(bad)
    assert any("prefix 'exx' is not declared" in i and "ex" in i for i in issues)


def test_reserved_prefix_rejected() -> None:
    bad = MINIMAL.replace("predicate: ex:name", "predicate: fn:slug")
    issues = parse_issues(bad)
    assert any("reserved for the compiler" in i for i in issues)

    bad2 = MINIMAL.replace('ex: "https://example.org/ns#"', 'rr: "https://example.org/ns#"')
    issues2 = parse_issues(bad2)
    assert any("reserved" in i for i in issues2)


def test_xsd_is_builtin() -> None:
    ok = MINIMAL.replace(
        "column: name", "column: name\n        datatype: xsd:string"
    )
    ir = parse_mapping_ir(ok)
    assert ir.maps[0].properties[0].datatype == "xsd:string"


def test_xml_source_requires_iterator_and_csv_forbids_it() -> None:
    xml = MINIMAL.replace("source: data.csv", "source: doc.xml").replace(
        'template: "exr:thing/{id}"', 'template: "exr:thing/{@id}"'
    )
    issues = parse_issues(xml)
    assert any("requires 'iterator'" in i for i in issues)

    csv_it = MINIMAL.replace("source: data.csv", 'source: data.csv\n    iterator: "/x"')
    issues = parse_issues(csv_it)
    assert any("XML sources only" in i for i in issues)


def test_source_must_be_bare_filename() -> None:
    issues = parse_issues(MINIMAL.replace("source: data.csv", "source: ../data.csv"))
    assert any("bare filename" in i for i in issues)


def test_duplicate_map_names() -> None:
    dup = MINIMAL + MINIMAL.split("maps:\n", 1)[1]
    issues = parse_issues(dup)
    assert any("more than once" in i for i in issues)


def test_transform_key_must_be_placeholder() -> None:
    bad = MINIMAL.replace(
        'classes: [ex:Thing]',
        "classes: [ex:Thing]\n      transform: { nope: slug }",
    )
    issues = parse_issues(bad)
    assert any("not a placeholder" in i for i in issues)


def test_cardinality_marker_on_predicate_is_flagged() -> None:
    bad = MINIMAL.replace("predicate: ex:name", "predicate: ex:name*")
    issues = parse_issues(bad)
    assert any("cardinality marker" in i and "'ex:name'" in i for i in issues)


def test_function_with_object_template_gets_targeted_guidance() -> None:
    """The live-dogfood invention (function output piped into a template) must
    get the three sanctioned alternatives, not a bare shape error."""
    bad = MINIMAL.replace(
        "column: name",
        'object_template: "exr:author/{id}"\n'
        "        function: json_pluck\n"
        "        args: { field: family }",
    )
    issues = parse_issues(bad)
    assert any("cannot be combined with object_template" in i for i in issues)
    assert any("fallback: true" in i for i in issues)
    # the unhelpful generic message must NOT also fire for the same row
    assert not any(".function requires 'column'" in i for i in issues)


def test_transform_misuse_gets_targeted_message() -> None:
    """Weak-model family (guided-off providers, JSON schema not enforced): the
    object form + its function are nested INSIDE `transform:`
    (`transform: {function: X, args: {…}}`), leaving the row with no object form.
    The parser names the misplaced row fields on top of the generic message."""
    bad = MINIMAL.replace(
        "        column: name\n",
        "        transform: { function: slug, args: { n: 1 } }\n",
    )
    issues = parse_issues(bad)
    # the generic object-form message still fires…
    assert any("exactly one object form" in i and "none" in i for i in issues)
    # …plus targeted guidance naming the moved row fields
    misuse = [i for i in issues if "transform cannot contain the row field(s)" in i]
    assert misuse, issues
    assert "function" in misuse[0] and "args" in misuse[0]
    assert "single-input function" in misuse[0]
    assert "ex:name" in misuse[0]  # keyed on the predicate for classify/oscillation


def test_transform_misuse_not_flagged_for_valid_transform() -> None:
    """A legit transform (readable IRI segment on an object_template) must NOT trip
    the misuse detector — it fires ONLY when the row has no object form."""
    ok = MINIMAL.replace(
        "        column: name\n",
        '        object_template: "exr:author/{name}"\n'
        "        transform: { name: slug }\n",
    )
    ir = parse_mapping_ir(ok)  # parses clean — no issues raised
    (p,) = ir.maps[0].properties
    assert p.object_template == "exr:author/{name}"
    assert p.transform == {"name": "slug"}


def test_fallback_requires_bare_column() -> None:
    bad = MINIMAL.replace(
        "column: name",
        "column: name\n        function: date_iso\n        fallback: true",
    )
    issues = parse_issues(bad)
    assert any("fallback" in i for i in issues)


def test_referenced_columns_skips_run_id() -> None:
    ir = parse_mapping_ir(
        MINIMAL.replace(
            'template: "exr:thing/{id}"', 'template: "exr:thing/{__run_id__}-{id}"'
        )
    )
    assert referenced_columns(ir.maps[0]) == {"id", "name"}


# ---------------------------------------------------------------------------
# Environment-aware validation
# ---------------------------------------------------------------------------


def tiny_catalog() -> FunctionCatalog:
    fn = "https://kumagallium.github.io/asterism/fn/"
    return FunctionCatalog(
        [
            CatalogFunction(
                name="date_iso",
                column_params=(("value", fn + "p_value"),),
                constant_params={},
                required_args=frozenset({"value"}),
            ),
            CatalogFunction(
                name="float_array_count",
                column_params=(("value1", fn + "p_value1"), ("value2", fn + "p_value2")),
                constant_params={},
                required_args=frozenset({"value1", "value2"}),
            ),
            CatalogFunction(
                name="lookup",
                column_params=(("value", fn + "p_value"),),
                constant_params={"table": fn + "p_table"},
                required_args=frozenset({"value", "table"}),
            ),
            CatalogFunction(
                name="slug",
                column_params=(("value", fn + "p_value"),),
                constant_params={},
                required_args=frozenset({"value"}),
            ),
            CatalogFunction(
                name="split",
                column_params=(("value", fn + "p_value"),),
                constant_params={"delimiter": fn + "p_delimiter"},
                required_args=frozenset({"value", "delimiter"}),
                multivalued=True,
            ),
        ]
    )


def validate(text: str, files: list[str], headers: dict) -> list[str]:
    return validate_mapping_ir(
        parse_mapping_ir(text), files=files, headers=headers, catalog=tiny_catalog()
    )


def test_validate_clean() -> None:
    assert validate(MINIMAL, ["data.csv"], {"data.csv": ["id", "name"]}) == []


def test_validate_missing_file_did_you_mean() -> None:
    issues = validate(MINIMAL, ["data_v1.csv"], {})
    assert any("does not exist" in i and "data_v1.csv" in i for i in issues)


def test_validate_missing_column_did_you_mean() -> None:
    issues = validate(MINIMAL, ["data.csv"], {"data.csv": ["id", "Name"]})
    assert any("column 'name'" in i and "Name" in i for i in issues)


def test_validate_unreadable_header_skips_columns() -> None:
    assert validate(MINIMAL, ["data.csv"], {"data.csv": None}) == []


def test_validate_pipe_filter_in_placeholder_steers_to_transform() -> None:
    bad = MINIMAL.replace('template: "exr:thing/{id}"', 'template: "exr:thing/{id|slug}"')
    issues = validate(bad, ["data.csv"], {"data.csv": ["id", "name"]})
    assert any("'|' is not a filter" in i and "transform: { id: slug }" in i for i in issues)


def test_validate_column_from_another_source_gets_move_hint() -> None:
    two = MINIMAL.replace(
        "column: name", "column: name\n      - predicate: ex:other\n        column: extra"
    )
    issues = validate(
        two, ["data.csv", "other.csv"],
        {"data.csv": ["id", "name"], "other.csv": ["extra"]},
    )
    assert any("it exists in other.csv" in i and "move this property" in i for i in issues)


def test_validate_unknown_function_menu() -> None:
    bad = MINIMAL.replace("column: name", "column: name\n        function: dat_iso")
    issues = validate(bad, ["data.csv"], {"data.csv": ["id", "name"]})
    assert any("'dat_iso'" in i and "date_iso" in i for i in issues)


def test_validate_column_count() -> None:
    bad = MINIMAL.replace(
        "column: name", "columns: [name]\n        function: float_array_count"
    )
    issues = validate(bad, ["data.csv"], {"data.csv": ["id", "name"]})
    assert any("takes 2 column input" in i for i in issues)


def test_validate_constant_args() -> None:
    missing = MINIMAL.replace("column: name", "column: name\n        function: lookup")
    issues = validate(missing, ["data.csv"], {"data.csv": ["id", "name"]})
    assert any("requires the constant arg 'table'" in i for i in issues)

    wrong = MINIMAL.replace(
        "column: name",
        "column: name\n        function: lookup\n        args: { tabel: bool }",
    )
    issues = validate(wrong, ["data.csv"], {"data.csv": ["id", "name"]})
    assert any("'tabel'" in i and "table" in i for i in issues)


def test_validate_type_cast_pseudo_function_gets_drop_guidance() -> None:
    """The live invention: function: str on every literal property (a type
    cast). Not a typo of any menu entry, so did-you-mean cannot steer it —
    the message must say 'drop function: / use datatype:' explicitly."""
    bad = MINIMAL.replace("column: name", "column: name\n        function: str")
    issues = validate(bad, ["data.csv"], {"data.csv": ["id", "name"]})
    assert any("is a type, not a Tier-0 function" in i for i in issues)
    assert any("DROP the 'function:' line" in i for i in issues)
    assert any("datatype: xsd:" in i for i in issues)


def test_validate_transform_rules() -> None:
    multival = MINIMAL.replace(
        "classes: [ex:Thing]",
        "classes: [ex:Thing]\n      transform: { id: split }",
    )
    issues = validate(multival, ["data.csv"], {"data.csv": ["id", "name"]})
    assert any("multiple values" in i for i in issues)

    needs_const = MINIMAL.replace(
        "classes: [ex:Thing]",
        "classes: [ex:Thing]\n      transform: { id: lookup }",
    )
    issues = validate(needs_const, ["data.csv"], {"data.csv": ["id", "name"]})
    assert any("single-input" in i for i in issues)

    ok = MINIMAL.replace(
        "classes: [ex:Thing]",
        "classes: [ex:Thing]\n      transform: { id: slug }",
    )
    assert validate(ok, ["data.csv"], {"data.csv": ["id", "name"]}) == []


# ---------------------------------------------------------------------------
# Catalog built from the live registry (single source of truth)
# ---------------------------------------------------------------------------


def test_catalog_from_registry_classifies_params() -> None:
    pytest.importorskip("asterism.functions")
    from asterism_step0.mapping_ir import catalog_from_registry

    catalog = catalog_from_registry()

    lookup = catalog.get("lookup")
    assert lookup is not None
    assert [a for a, _ in lookup.column_params] == ["value"]
    assert set(lookup.constant_params) == {"table"}
    assert "table" in lookup.required_args

    count = catalog.get("float_array_count")
    assert count is not None
    assert [a for a, _ in count.column_params] == ["value1", "value2"]
    assert count.constant_params == {}

    template = catalog.get("template")
    assert template is not None
    # template's config string is the constant; field1..4 are column-bound.
    assert set(template.constant_params) == {"template"}
    assert [a for a, _ in template.column_params] == ["field1", "field2", "field3", "field4"]
    assert template.required_column_count == 0

    split = catalog.get("split")
    assert split is not None and split.multivalued
    assert catalog.get("slug") is not None and not catalog.get("slug").multivalued
    assert catalog.get("no_such_fn") is None


# ---------------------------------------------------------------------------
# dialects section (ADR source-dialect.md)
# ---------------------------------------------------------------------------

TXT_MINIMAL = MINIMAL.replace("source: data.csv", "source: data.txt")


def test_instrument_suffixes_accepted() -> None:
    for suffix in (".txt", ".dat", ".asc", ".tsv"):
        parse_mapping_ir(MINIMAL.replace("data.csv", f"data{suffix}"))


def test_dialects_parse_onto_ir() -> None:
    text = TXT_MINIMAL + (
        "dialects:\n"
        '  "data.txt":\n'
        "    encoding: cp932\n"
        '    delimiter: "\\t"\n'
        "    skip_rows: 1\n"
    )
    ir = parse_mapping_ir(text)
    d = ir.dialects["data.txt"]
    assert (d.encoding, d.delimiter, d.collapse, d.skip_rows) == ("cp932", "\t", False, 1)


def test_dialects_preamble_parses_onto_ir() -> None:
    text = TXT_MINIMAL + 'dialects:\n  "data.txt": { skip_rows: 23, preamble: keyvalue }\n'
    ir = parse_mapping_ir(text)
    assert ir.dialects["data.txt"].preamble == "keyvalue"
    # An absent preamble defaults to drop (byte-identical to today).
    text2 = TXT_MINIMAL + 'dialects:\n  "data.txt": { skip_rows: 1 }\n'
    assert parse_mapping_ir(text2).dialects["data.txt"].preamble == "drop"


def test_dialects_preamble_bad_value_is_error() -> None:
    text = TXT_MINIMAL + 'dialects:\n  "data.txt": { skip_rows: 1, preamble: sometimes }\n'
    issues = parse_issues(text)
    assert any("preamble must be one of" in i for i in issues)


def test_dialects_preamble_without_skip_rows_is_flagged() -> None:
    # Trap 13: a preamble mode with skip_rows=0 has no preamble block to read.
    text = TXT_MINIMAL + 'dialects:\n  "data.txt": { preamble: keyvalue }\n'
    issues = parse_issues(text)
    assert any("skip_rows is 0" in i for i in issues)


def test_dialects_whitespace_sentinel_ok() -> None:
    text = TXT_MINIMAL + 'dialects:\n  "data.txt": { delimiter: whitespace, skip_rows: 23 }\n'
    ir = parse_mapping_ir(text)
    assert ir.dialects["data.txt"].delimiter == "whitespace"
    assert ir.dialects["data.txt"].skip_rows == 23


def test_dialects_absent_is_empty() -> None:
    assert parse_mapping_ir(MINIMAL).dialects == {}


def test_dialects_unknown_field_is_error() -> None:
    text = TXT_MINIMAL + 'dialects:\n  "data.txt": { codepage: cp932 }\n'
    issues = parse_issues(text)
    assert any("unknown field 'codepage'" in i for i in issues)


def test_dialects_bad_codec() -> None:
    text = TXT_MINIMAL + 'dialects:\n  "data.txt": { encoding: not-a-codec }\n'
    issues = parse_issues(text)
    assert any("not a known text codec" in i for i in issues)


def test_dialects_bytes_codec_is_error() -> None:
    # C10 defense in depth: 'zip'/'base64' resolve via codecs.lookup but are
    # bytes<->bytes codecs — the runtime text decode would crash on them.
    for codec in ("zip", "base64"):
        text = TXT_MINIMAL + f'dialects:\n  "data.txt": {{ encoding: {codec} }}\n'
        issues = parse_issues(text)
        assert any("not a known text codec" in i for i in issues), codec


def test_dialects_bad_delimiter_and_skip_rows() -> None:
    text = TXT_MINIMAL + 'dialects:\n  "data.txt": { delimiter: "||", skip_rows: -1 }\n'
    issues = parse_issues(text)
    assert any("single character" in i for i in issues)
    assert any("non-negative" in i for i in issues)


def test_dialects_filename_must_match_declared_source() -> None:
    text = MINIMAL + 'dialects:\n  "data_v1.csv": { encoding: cp932 }\n'
    issues = parse_issues(text)
    assert any("must match a declared source" in i and "data.csv" in i for i in issues)


def test_dialects_apply_to_tabular_sources_only() -> None:
    xml = (
        MINIMAL.replace("source: data.csv", 'source: doc.xml\n    iterator: "/x"').replace(
            'template: "exr:thing/{id}"', 'template: "exr:thing/{@id}"'
        )
        + 'dialects:\n  "doc.xml": { encoding: cp932 }\n'
    )
    issues = parse_issues(xml)
    assert any("tabular sources only" in i for i in issues)
