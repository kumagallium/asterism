"""契約メモ a・R1/R2: 英字にできない名前から作る識別子の一意化と、種類の
表示名（``SubjectIR.label``）。``_identifier``/``_class_name``（機械名）と
``default_skeleton``（ファイル名から作る種類）の決定論を確かめる。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("yaml")

from asterism_step0.ascii_names import name_tag
from asterism_step0.mapping_ir import parse_mapping_ir
from asterism_step0.staged_propose import _class_name, _identifier, default_skeleton

# 契約メモ a・§1 R1 の表。
_ASCII_UNCHANGED = {
    "Resistivity(Ohm m)": "resistivityOhmM",
    "Resistivity (μΩ·cm)": "resistivityCm",
}


def test_ascii_names_are_untouched() -> None:
    for text, expected in _ASCII_UNCHANGED.items():
        assert _identifier(text) == expected


def test_japanese_names_get_a_tagged_identifier_instead_of_the_fallback_word() -> None:
    assert _identifier("食材の名前") == f"value_{name_tag('食材の名前')}"
    assert _identifier("店の名前") == f"value_{name_tag('店の名前')}"
    # 違う日本語の名前 → 違う識別子（今までは両方 value/value2 に潰れていた）。
    assert _identifier("食材の名前") != _identifier("店の名前")


def test_identifier_keeps_the_ascii_part_when_present_but_words_are_lost() -> None:
    assert _identifier("温度 (K)") == f"k_{name_tag('温度 (K)')}"


def test_class_name_from_a_non_ascii_file_stem() -> None:
    assert _class_name("価格表") == f"Record_{name_tag('価格表')}"


def test_identifier_same_input_always_same_output() -> None:
    assert _identifier("食材の名前") == _identifier("食材の名前")


def test_identifier_custom_fallback_is_used_when_ascii_part_is_empty() -> None:
    assert _identifier("食材の名前", fallback="record") == f"record_{name_tag('食材の名前')}"


def _write(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.write_text("id,name\n1,a\n2,b\n", encoding="utf-8")
    return p


def test_default_skeleton_japanese_filename_gets_a_tagged_map_name_and_class(
    tmp_path: Path,
) -> None:
    from asterism_step0.inspect import inspect_source_set

    path = _write(tmp_path, "価格表.csv")
    inspections, _ = inspect_source_set([path])
    skeleton = default_skeleton(inspections, iri_base="https://x.test", dataset_name="demo")
    (m,) = skeleton["maps"]
    assert m["name"] == f"record_{name_tag('価格表')}"
    assert m["subject"]["classes"] == [f"demo:Record_{name_tag('価格表')}"]
    # ファイル名から作る種類は R1 の符号付き分岐のときだけ表示名を持つ。
    assert m["subject"]["label"] == "価格表"


def test_default_skeleton_ascii_filename_gets_no_label(tmp_path: Path) -> None:
    """英字のファイル名は今までどおり表示名なし（契約メモ a・R2 の表）。"""
    from asterism_step0.inspect import inspect_source_set

    path = _write(tmp_path, "data.csv")
    inspections, _ = inspect_source_set([path])
    skeleton = default_skeleton(inspections, iri_base="https://x.test", dataset_name="demo")
    (m,) = skeleton["maps"]
    assert m["name"] == "data"
    assert "label" not in m["subject"]


def test_default_skeleton_maps_still_compile_into_a_valid_subject_ir(
    tmp_path: Path,
) -> None:
    """種類に表示名が付いても、骨格は今までどおり有効な subject（RML には
    決して入らない）として読める。"""
    from asterism_step0.inspect import inspect_source_set

    path = _write(tmp_path, "価格表.csv")
    inspections, _ = inspect_source_set([path])
    skeleton = default_skeleton(inspections, iri_base="https://x.test", dataset_name="demo")
    (m,) = skeleton["maps"]
    assert m["subject"]["template"]
    assert m["subject"]["classes"]


def test_subject_label_round_trips_through_mapping_ir_parse() -> None:
    """契約メモ a・R2: SubjectIR.label は読み書きで保たれる。"""
    text = """\
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
      label: "試料"
    properties:
      - predicate: ex:name
        column: name
"""
    ir = parse_mapping_ir(text)
    assert ir.maps[0].subject.label == "試料"


def test_subject_without_label_reads_as_before() -> None:
    """label の無い IR は今までどおり読める（不変条件）。"""
    text = """\
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
    ir = parse_mapping_ir(text)
    assert ir.maps[0].subject.label is None
