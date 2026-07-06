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
    bad = MINIMAL.replace(
        "column: name", 'constant: "x"\n        function: date_iso'
    )
    issues = parse_issues(bad)
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
