"""Tests for asterism.mapping_ir_read (class_schema §2 の最小限の読み手)。

架空 2 分野（貸出記録・観測ログ）の ``mapping.yaml`` 文字列を使う（§0: 分野固有の
語彙を書かない）。
"""
from __future__ import annotations

import pytest

from asterism.mapping_ir_read import (
    BUILTIN_PREFIXES,
    MappingIRReadError,
    expand,
    read_mapping_ir,
)

_LENDING_MAPPING_YAML = """
version: 1
prefixes:
  ex: "https://ex/lending#"
  exr: "https://ex/lending/resource/"
  dcterms: "http://purl.org/dc/terms/"
  xsd: "http://www.w3.org/2001/XMLSchema#"
maps:
  - name: checkout
    source: checkouts.csv
    subject:
      template: "exr:checkout/{id}"
      classes: [ex:CheckoutRecord]
    properties:
      - predicate: dcterms:identifier
        column: id
        label: "記録番号"
      - predicate: ex:borrower
        column: borrower
      - predicate: ex:branch
        object_template: "exr:branch/{branch_code}"
        object_type: iri
      - predicate: ex:overdueDays
        column: overdue_days
        datatype: xsd:integer
        unit: "days"
"""

_OBSERVATION_MAPPING_YAML = """
version: 1
prefixes:
  ex: "https://ex/observe#"
  exr: "https://ex/observe/resource/"
maps:
  - name: reading
    source: readings.csv
    subject:
      template: "exr:reading/{code}"
      classes: [ex:Reading]
    properties:
      - predicate: ex:station
        columns: [lat, lon]
"""


def test_reads_prefixes_and_single_map() -> None:
    view = read_mapping_ir(_LENDING_MAPPING_YAML)
    assert view.prefixes["ex"] == "https://ex/lending#"
    assert len(view.maps) == 1
    tm = view.maps[0]
    assert tm.name == "checkout"
    assert tm.source == "checkouts.csv"
    assert tm.subject_template == "exr:checkout/{id}"
    assert tm.subject_classes == ("ex:CheckoutRecord",)


def test_subject_columns_extracted_from_template_placeholders() -> None:
    view = read_mapping_ir(_LENDING_MAPPING_YAML)
    assert view.maps[0].subject_columns == ("id",)


def test_properties_fields_are_read() -> None:
    view = read_mapping_ir(_LENDING_MAPPING_YAML)
    by_predicate = {p.predicate: p for p in view.maps[0].properties}
    assert by_predicate["dcterms:identifier"].column == "id"
    assert by_predicate["dcterms:identifier"].label == "記録番号"
    assert by_predicate["ex:branch"].object_template == "exr:branch/{branch_code}"
    assert by_predicate["ex:branch"].object_type == "iri"
    assert by_predicate["ex:overdueDays"].datatype == "xsd:integer"
    assert by_predicate["ex:overdueDays"].unit == "days"


def test_multivalued_columns_field_is_read() -> None:
    view = read_mapping_ir(_OBSERVATION_MAPPING_YAML)
    prop = view.maps[0].properties[0]
    assert prop.columns == ("lat", "lon")
    assert prop.column is None


def test_expand_curie_via_prefixes() -> None:
    view = read_mapping_ir(_LENDING_MAPPING_YAML)
    prefixes = dict(BUILTIN_PREFIXES) | dict(view.prefixes)
    assert expand(prefixes, "ex:CheckoutRecord") == "https://ex/lending#CheckoutRecord"
    # Already-expanded / unknown-prefix terms pass through unchanged.
    assert expand(prefixes, "https://ex/lending#CheckoutRecord") == (
        "https://ex/lending#CheckoutRecord"
    )
    assert expand(prefixes, "nope:Thing") == "nope:Thing"


def test_builtin_prefixes_available_without_declaration() -> None:
    assert BUILTIN_PREFIXES["xsd"] == "http://www.w3.org/2001/XMLSchema#"


@pytest.mark.parametrize(
    "bad_text",
    [
        "not: valid: yaml: [",  # broken YAML
        "- just\n- a\n- list\n",  # top-level not a mapping
        "version: 1\nmaps: not-a-list\n",  # maps not a list
        "version: 1\nmaps:\n  - source: x.csv\n",  # map missing name
        "version: 1\nmaps:\n  - name: x\n    source: x.csv\n",  # map missing subject
    ],
)
def test_malformed_input_raises_mapping_ir_read_error(bad_text: str) -> None:
    with pytest.raises(MappingIRReadError):
        read_mapping_ir(bad_text)


def test_mapping_ir_read_error_is_a_value_error() -> None:
    assert issubclass(MappingIRReadError, ValueError)
