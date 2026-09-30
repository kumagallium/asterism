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


def _label_skeleton() -> dict:
    return {
        "version": 1,
        "prefixes": {"ex": "https://example.org/ns#", "exr": "https://example.org/r/"},
        "maps": [
            {
                "name": "a",
                "source": "a.csv",
                "subject": {
                    "template": "exr:a/{id}",
                    "classes": ["ex:Record_389a00"],
                    "label": "食材の名前",
                },
            }
        ],
    }


def test_json_schemas_accept_subject_label() -> None:
    """骨格・IR のガイド付き生成スキーマが subject.label を通す。"""
    import jsonschema

    from asterism_step0.mapping_ir_schema import mapping_ir_json_schema, skeleton_json_schema

    sk = _label_skeleton()
    jsonschema.validate(sk, skeleton_json_schema())
    full = {
        **sk,
        "maps": [{**sk["maps"][0], "properties": [{"predicate": "ex:name", "column": "name"}]}],
    }
    jsonschema.validate(full, mapping_ir_json_schema())


def test_reassert_and_carry_keep_subject_label() -> None:
    from asterism_step0.staged_propose import human_pinned_edits, reassert_human_edits

    baseline = _label_skeleton()
    baseline["maps"][0]["subject"] = {
        "template": "exr:a/{id}",
        "classes": ["ex:A"],
        "label": "A",
    }
    current = _label_skeleton()
    pinned = human_pinned_edits(baseline, current)
    assert pinned["a"]["label"] == "食材の名前"
    answer = {
        "maps": [
            {
                "name": "a",
                "source": "a.csv",
                "subject": {"template": "exr:a/{id}", "classes": ["ex:A"]},
            }
        ]
    }
    out, restored = reassert_human_edits(answer, current, pinned)
    assert out["maps"][0]["subject"]["label"] == "食材の名前"
    assert out["maps"][0]["subject"]["classes"] == ["ex:Record_389a00"]
    assert restored[0]["label"] == "食材の名前"


def test_carry_subject_labels_fills_only_missing() -> None:
    from asterism_step0.spec_repair import carry_subject_labels

    old = _label_skeleton()
    new = {"maps": [{"name": "a", "subject": {"classes": ["ex:X"]}}, {"name": "b", "subject": {}}]}
    out = carry_subject_labels(new, old)
    assert out["maps"][0]["subject"]["label"] == "食材の名前"
    assert "label" not in out["maps"][1]["subject"]
    own = {"maps": [{"name": "a", "subject": {"label": "自分の名前"}}]}
    assert carry_subject_labels(own, old)["maps"][0]["subject"]["label"] == "自分の名前"


# ---------------------------------------------------------------------------
# 機械が足す「つなぐ項目」の表示名 — つなぐ先の種類の表示名を付ける
# （マップ名は、英字にできない列から作ると符号つきになる）
# ---------------------------------------------------------------------------


def test_the_decided_link_carries_the_kinds_display_name(monkeypatch) -> None:
    import asterism_step0.staged_propose as sp
    from asterism_step0.skeleton_annotate import _ascii_map_name

    def fake_generate(*_args, **_kwargs):
        return {
            "properties": [{"predicate": "xr:n", "column": "No", "label": "番号"}],
            "prefixes": {},
        }

    monkeypatch.setattr(sp, "generate_map_properties", fake_generate)
    owner = _ascii_map_name("食材の名前", set(), "value")
    out = sp._generate_map_properties_gated(
        "rec",
        {"name": "rec", "source": "c.csv", "subject": {"template": "xr:rec/{No}"}},
        "ctx",
        "menu",
        llm=object(),
        function_names=None,
        language=None,
        index=0,
        total=1,
        emit=lambda **_k: None,
        record=lambda: None,
        owned_elsewhere={"食材の名前": owner},
        owner_subjects={owner: "xr:" + owner + "/{食材の名前}"},
        owner_labels={owner: "食材の名前"},
        ontology_prefix="xr",
    )
    links = [p for p in out["properties"] if p.get("object_template")]
    assert len(links) == 1
    assert links[0]["label"] == "食材の名前"
    assert owner not in links[0]["label"]


def test_the_decided_link_falls_back_to_the_map_name_without_a_display_name(monkeypatch) -> None:
    import asterism_step0.staged_propose as sp

    def fake_generate(*_args, **_kwargs):
        return {
            "properties": [{"predicate": "xr:n", "column": "No", "label": "番号"}],
            "prefixes": {},
        }

    monkeypatch.setattr(sp, "generate_map_properties", fake_generate)
    out = sp._generate_map_properties_gated(
        "rec",
        {"name": "rec", "source": "c.csv", "subject": {"template": "xr:rec/{No}"}},
        "ctx",
        "menu",
        llm=object(),
        function_names=None,
        language=None,
        index=0,
        total=1,
        emit=lambda **_k: None,
        record=lambda: None,
        owned_elsewhere={"shelf": "shelf"},
        owner_subjects={"shelf": "xr:shelf/{shelf}"},
        ontology_prefix="xr",
    )
    links = [p for p in out["properties"] if p.get("object_template")]
    assert len(links) == 1
    assert links[0]["label"] == "shelf"


def test_the_same_source_link_carries_the_kinds_display_name() -> None:
    from asterism_step0.staged_propose import ensure_same_source_links

    ir = {
        "version": 1,
        "prefixes": {"xr": "http://x/ontology#"},
        "maps": [
            {
                "name": "rec",
                "source": "c.csv",
                "subject": {"template": "xr:rec/{No}", "classes": ["xr:Rec"]},
                "properties": [{"predicate": "xr:n", "column": "食材の名前", "label": "食材"}],
            },
            {
                "name": "value_389a00",
                "source": "c.csv",
                "subject": {
                    "template": "xr:value_389a00/{食材の名前}",
                    "classes": ["xr:Value_389a00"],
                    "label": "食材の名前",
                },
                "properties": [],
            },
        ],
    }
    out, added = ensure_same_source_links(ir, ontology_prefix="xr")
    assert added
    links = [p for p in out["maps"][0]["properties"] if p.get("object_template")]
    assert len(links) == 1
    assert links[0]["label"] == "食材の名前"


def test_the_same_source_link_has_no_label_without_a_display_name() -> None:
    from asterism_step0.staged_propose import ensure_same_source_links

    ir = {
        "version": 1,
        "prefixes": {"xr": "http://x/ontology#"},
        "maps": [
            {
                "name": "rec",
                "source": "c.csv",
                "subject": {"template": "xr:rec/{No}", "classes": ["xr:Rec"]},
                "properties": [{"predicate": "xr:n", "column": "shelf"}],
            },
            {
                "name": "shelf",
                "source": "c.csv",
                "subject": {"template": "xr:shelf/{shelf}", "classes": ["xr:Shelf"]},
                "properties": [],
            },
        ],
    }
    out, added = ensure_same_source_links(ir, ontology_prefix="xr")
    assert added
    links = [p for p in out["maps"][0]["properties"] if p.get("object_template")]
    assert len(links) == 1
    assert "label" not in links[0]


def _nested_id_ir(parent_label: str | None) -> dict:
    """子の ID が親の鍵を丸ごと含む（ID の入れ子）2 つの種類。"""
    parent_subject: dict = {"template": "xr:parent/{p}", "classes": ["xr:Parent"]}
    if parent_label is not None:
        parent_subject["label"] = parent_label
    return {
        "version": 1,
        "prefixes": {"xr": "http://x/ontology#"},
        "maps": [
            {
                "name": "child",
                "source": "c.csv",
                "subject": {"template": "xr:child/{p}/{c}", "classes": ["xr:Child"]},
                "properties": [{"predicate": "xr:n", "column": "n"}],
            },
            {
                "name": "parent",
                "source": "c.csv",
                "subject": parent_subject,
                "properties": [],
            },
        ],
    }


def test_the_nested_id_link_carries_the_parents_display_name() -> None:
    from asterism_step0.staged_propose import ensure_same_source_links

    out, added = ensure_same_source_links(_nested_id_ir("親の名前"), ontology_prefix="xr")
    assert added
    links = [
        p for p in out["maps"][0]["properties"] if p.get("predicate") == "dcterms:isPartOf"
    ]
    assert len(links) == 1
    assert links[0]["label"] == "親の名前"


def test_the_nested_id_link_has_no_label_without_a_display_name() -> None:
    from asterism_step0.staged_propose import ensure_same_source_links

    out, added = ensure_same_source_links(_nested_id_ir(None), ontology_prefix="xr")
    assert added
    links = [
        p for p in out["maps"][0]["properties"] if p.get("predicate") == "dcterms:isPartOf"
    ]
    assert len(links) == 1
    assert "label" not in links[0]
