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


# ---------------------------------------------------------------------------
# つなぐ行の link_label（つなぐ先の種類の表示名）— api の _ir_display_entries と同じ規則
# ---------------------------------------------------------------------------

# 公開済みの設計と同じ形: 機械が足したつなぐ行（dcterms:isPartOf）に label が無い。
_LINKED_MAPPING_YAML = """
version: 1
prefixes:
  ex: https://ex/lending/ontology#
  exr: https://ex/lending/resource/
  dcterms: http://purl.org/dc/terms/
maps:
- name: card
  source: lending.txt
  subject:
    template: exr:card/{card_no}
    classes:
    - ex:Record_aaaaaa
    label: 貸出カード
  properties:
  - predicate: ex:cardNo
    column: card_no
    label: カードの番号
- name: record
  source: lending.txt
  subject:
    template: exr:record/{card_no}/{item}
    classes:
    - ex:Record_bbbbbb
    label: 貸出した本
  properties:
  - predicate: ex:item
    column: item
    label: 本の名前
  - predicate: dcterms:isPartOf
    object_template: exr:card/{card_no}
  - predicate: ex:named
    object_template: exr:card/{card_no}
    label: 行に書いた名前
  - predicate: ex:self
    object_template: https://ex/lending/resource/record/{card_no}/{item}
  - predicate: ex:asText
    object_template: exr:card/{card_no}
    object_type: literal
  - predicate: ex:toBranch
    constant: https://ex/lending/resource/branch
    object_type: iri
  - predicate: ex:branchText
    constant: exr:branch
- name: branch
  source: lending.txt
  subject:
    constant: exr:branch
    classes:
    - ex:Branch
    label: 貸出した館
  properties: []
- name: unnamed
  source: lending.txt
  subject:
    template: exr:shelf/{shelf}
    classes:
    - ex:Shelf
  properties:
  - predicate: ex:onShelf
    object_template: exr:shelf/{shelf}
"""


def _props(map_name: str) -> dict[str, object]:
    ir = read_mapping_ir(_LINKED_MAPPING_YAML)
    tm = next(m for m in ir.maps if m.name == map_name)
    return {p.predicate: p for p in tm.properties}


def test_link_row_without_label_reads_the_target_kind_label() -> None:
    props = _props("record")
    assert props["dcterms:isPartOf"].label is None
    # CURIE のテンプレート同士・完全な IRI とも展開してから比べる
    assert props["dcterms:isPartOf"].link_label == "貸出カード"


def test_link_label_is_kept_beside_an_authored_label() -> None:
    # 行に書いた label はそのまま（どちらを使うかは読む側が決める）
    named = _props("record")["ex:named"]
    assert named.label == "行に書いた名前"
    assert named.link_label == "貸出カード"


def test_row_pointing_at_its_own_map_gets_no_link_label() -> None:
    assert _props("record")["ex:self"].link_label is None


def test_literal_template_row_gets_no_link_label() -> None:
    assert _props("record")["ex:asText"].link_label is None


def test_iri_constant_row_matches_a_constant_subject() -> None:
    props = _props("record")
    assert props["ex:toBranch"].constant == "https://ex/lending/resource/branch"
    assert props["ex:toBranch"].link_label == "貸出した館"
    # object_type が iri でない定数は文字の値 — つなぐ行ではない
    assert props["ex:branchText"].link_label is None


def test_target_without_a_kind_label_leaves_link_label_empty() -> None:
    assert _props("unnamed")["ex:onShelf"].link_label is None


def test_subject_constant_and_label_are_read() -> None:
    ir = read_mapping_ir(_LINKED_MAPPING_YAML)
    branch = next(m for m in ir.maps if m.name == "branch")
    assert branch.subject_constant == "exr:branch"
    assert branch.subject_label == "貸出した館"
    assert branch.subject_template is None


_MORE_LINKS_YAML = """
version: 1
prefixes:
  ex: https://ex/lending/ontology#
  exr: https://ex/lending/resource/
maps:
- name: first
  subject:
    template: exr:card/{card_no}
    classes: [ex:A]
    label: 先の名前
  properties:
  - predicate: ex:toCard
    object_template: exr:card/{card_no}
- name: second
  subject:
    template: https://ex/lending/resource/card/{card_no}
    classes: [ex:B]
    label: 後の名前
  properties:
  - predicate: ex:fullIri
    object_template: https://ex/lending/resource/card/{card_no}
- name: blank
  subject:
    template: exr:blank/{id}
    classes: [ex:C]
    label: "   "
  properties:
  - predicate: ex:toBlank
    object_template: exr:blank/{id}
- name: other
  subject:
    template: exr:other/{id}
    classes: [ex:D]
  properties:
  - predicate: ex:toBlankFromOther
    object_template: exr:blank/{id}
"""


def _more(map_name: str, predicate: str):
    ir = read_mapping_ir(_MORE_LINKS_YAML)
    tm = next(m for m in ir.maps if m.name == map_name)
    return next(p for p in tm.properties if p.predicate == predicate)


def test_full_iri_template_matches_a_curie_subject_after_expansion() -> None:
    # 完全な IRI の行が、CURIE で書いた別の map（first）に当たる
    assert _more("second", "ex:fullIri").link_label == "先の名前"


def test_own_map_is_skipped_and_the_next_candidate_wins() -> None:
    # first 自身は除外され、同じ主語を持つ second の名前になる
    assert _more("first", "ex:toCard").link_label == "後の名前"


def test_blank_subject_label_is_not_a_candidate() -> None:
    assert _more("other", "ex:toBlankFromOther").link_label is None
