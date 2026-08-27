"""ID の引っ越し計画（ADR id-move-after-publish.md）。

住所が動くかどうかの判定と、動くときの転送台帳 RML を、旧設計と新設計だけから
決定論で出す。ここで守りたい性質は 3 つ:

* IRI 文字列をこの module が自分で組み立てない（綴りはコンパイラ + エンジンの
  一枚看板）。テストは「同じ template が同じ形で RML に載る」ことだけを見る。
* 「引っ越せない」が黙って消えない —— 前の住所が開けなくなる事実は結果に残る。
* prefix 名が設計をやり直して変わっても、記録した住所は動かない。
"""
# This module's prose is Japanese: full-width parentheses / slashes are
# intentional, not ASCII look-alikes (same posture as describe.py).
# ruff: noqa: RUF002, RUF003
from __future__ import annotations

import pytest

pytest.importorskip("yaml")

from asterism_step0.id_move import (
    DCTERMS_IS_REPLACED_BY,
    PublishedSubject,
    compile_id_move_rml,
    expand_template,
    plan_id_move,
    published_subjects,
)
from asterism_step0.mapping_ir import parse_mapping_ir

BASE = """
version: 1
prefixes:
  ex: "https://example.org/ns#"
  exr: "https://example.org/r/"
maps:
  - name: sample
    source: samples.csv
    subject:
      template: "exr:sample/{sid}"
      classes: [ex:Sample]
    properties:
      - predicate: ex:name
        column: name
  - name: paper
    source: papers.csv
    subject:
      template: "exr:paper/{sid}"
      classes: [ex:Paper]
    properties:
      - predicate: ex:title
        column: title
"""

# 「データの数えかた」をやり直した版: 試料のキーが 1 列 → 2 列（1 行 = 1 試料の
# 1 回の測定、という数えかたに直したときに実際に起きる形）。
RECOUNTED = BASE.replace(
    'template: "exr:sample/{sid}"', 'template: "exr:sample/{sid}-{run}"'
)


def test_published_subjects_records_absolute_iris() -> None:
    """記録は CURIE ではなく展開済みの絶対 IRI。prefix 名は設計のやり直しで
    変わる（K13 は slug から導出する）のに、住所は変わってはいけない。"""
    subs = {s.name: s for s in published_subjects(parse_mapping_ir(BASE))}
    assert subs["sample"].template == "https://example.org/r/sample/{sid}"
    assert subs["sample"].source == "samples.csv"


def test_renamed_prefix_alone_is_not_a_move() -> None:
    """prefix 名だけ変えても IRI は同じ → 引っ越しは起きない。"""
    renamed = BASE.replace("exr:", "res:")  # 宣言も参照も一括で改名
    plan = plan_id_move(
        published_subjects(parse_mapping_ir(BASE)), parse_mapping_ir(renamed)
    )
    assert plan.changes_ids is False
    assert sorted(plan.unchanged) == ["paper", "sample"]


def test_unchanged_design_moves_nothing() -> None:
    ir = parse_mapping_ir(BASE)
    plan = plan_id_move(published_subjects(ir), ir)
    assert plan.changes_ids is False
    assert plan.fully_movable is True
    assert compile_id_move_rml(plan, ir) is None


def test_key_column_added_is_a_move() -> None:
    """キー列が増えると住所が動く。動かない map は unchanged に残る。"""
    plan = plan_id_move(
        published_subjects(parse_mapping_ir(BASE)), parse_mapping_ir(RECOUNTED)
    )
    assert plan.changes_ids is True
    assert plan.fully_movable is True
    assert [m.name for m in plan.moved] == ["sample"]
    assert plan.moved[0].old_template == "https://example.org/r/sample/{sid}"
    assert plan.moved[0].new_template == "https://example.org/r/sample/{sid}-{run}"
    assert plan.unchanged == ("paper",)


def test_label_only_change_is_not_a_move() -> None:
    """意味（ラベル）を直しても住所は動かない —— 公開後にずっと開いていた道。"""
    labelled = BASE.replace("column: name", 'column: name\n        label: "試料名"')
    plan = plan_id_move(
        published_subjects(parse_mapping_ir(BASE)), parse_mapping_ir(labelled)
    )
    assert plan.changes_ids is False


def test_renamed_map_still_pairs() -> None:
    """map 名が変わっただけ（同じ元ファイルに 1 つずつ）なら対応づける。"""
    renamed = RECOUNTED.replace("- name: sample", "- name: specimen")
    plan = plan_id_move(
        published_subjects(parse_mapping_ir(BASE)), parse_mapping_ir(renamed)
    )
    assert [(m.old_name, m.name) for m in plan.moved] == [("sample", "specimen")]


def test_dropped_map_is_blocked_not_silent() -> None:
    """種類ごと設計から消えたら、行き先が無い。黙って落とさず結果に残す。"""
    without_sample = """
version: 1
prefixes:
  ex: "https://example.org/ns#"
  exr: "https://example.org/r/"
maps:
  - name: paper
    source: papers.csv
    subject:
      template: "exr:paper/{sid}-{run}"
      classes: [ex:Paper]
    properties:
      - predicate: ex:title
        column: title
"""
    plan = plan_id_move(
        published_subjects(parse_mapping_ir(BASE)), parse_mapping_ir(without_sample)
    )
    assert plan.fully_movable is False
    blocked = {b.name: b for b in plan.blocked}
    assert blocked["sample"].reason == "no_matching_map"


def test_missing_column_blocks_the_move() -> None:
    """旧 ID を綴る列がいまの元ファイルに無ければ、前の住所は計算できない。"""
    plan = plan_id_move(
        published_subjects(parse_mapping_ir(BASE)),
        parse_mapping_ir(RECOUNTED),
        available_columns={"samples.csv": {"run", "name"}, "papers.csv": {"sid", "title"}},
    )
    assert plan.fully_movable is False
    assert plan.blocked[0].reason == "missing_columns"
    assert plan.blocked[0].missing_columns == ("sid",)


def test_present_columns_allow_the_move() -> None:
    plan = plan_id_move(
        published_subjects(parse_mapping_ir(BASE)),
        parse_mapping_ir(RECOUNTED),
        available_columns={
            "samples.csv": {"sid", "run", "name"},
            "papers.csv": {"sid", "title"},
        },
    )
    assert plan.fully_movable is True
    assert [m.name for m in plan.moved] == ["sample"]


def test_compiled_rml_links_old_to_new_and_asserts_nothing_else() -> None:
    """台帳は転送 1 本だけ。旧 IRI に種類を主張させると、記録が *データ* に化ける。"""
    new_ir = parse_mapping_ir(RECOUNTED)
    plan = plan_id_move(published_subjects(parse_mapping_ir(BASE)), new_ir)
    rml = compile_id_move_rml(plan, new_ir)
    assert rml is not None
    assert f"<{DCTERMS_IS_REPLACED_BY}>" in rml
    assert '"https://example.org/r/sample/{sid}"' in rml
    assert '"https://example.org/r/sample/{sid}-{run}"' in rml
    assert "rr:class" not in rml
    # 動かない map は台帳に載らない（載せると「変わっていない」が嘘になる）。
    assert "paper" not in rml.lower()
    assert rml.count(DCTERMS_IS_REPLACED_BY) == 1


def test_subject_transform_is_carried_on_both_sides() -> None:
    """読みやすい IRI 断片（Tier-0 変換つき）も、旧・新の両方でそのまま効く。"""
    old = """
version: 1
prefixes:
  exr: "https://example.org/r/"
maps:
  - name: sample
    source: samples.csv
    subject:
      template: "exr:sample/{name}"
      transform:
        name: slug
    properties:
      - predicate: https://example.org/ns#n
        column: n
"""
    new = old.replace('template: "exr:sample/{name}"', 'template: "exr:sample/{name}-{run}"')
    new = new.replace("        name: slug", "        name: slug\n        run: slug")
    new_ir = parse_mapping_ir(new)
    plan = plan_id_move(published_subjects(parse_mapping_ir(old)), new_ir)
    assert [m.name for m in plan.moved] == ["sample"]
    assert plan.moved[0].old_transform == {"name": "slug"}
    rml = compile_id_move_rml(plan, new_ir)
    assert rml is not None
    assert "slug" in rml


def test_transform_change_alone_is_a_move() -> None:
    """template の字面が同じでも、掛かる変換が変われば綴りは変わる。"""
    old = """
version: 1
prefixes:
  exr: "https://example.org/r/"
maps:
  - name: sample
    source: samples.csv
    subject:
      template: "exr:sample/{name}"
    properties:
      - predicate: https://example.org/ns#n
        column: n
"""
    new = old.replace(
        '      template: "exr:sample/{name}"',
        '      template: "exr:sample/{name}"\n      transform:\n        name: slug',
    )
    plan = plan_id_move(published_subjects(parse_mapping_ir(old)), parse_mapping_ir(new))
    assert [m.name for m in plan.moved] == ["sample"]


def test_constant_subject_is_not_recorded() -> None:
    """行に紐づかない固定エンティティは引っ越しの対象外（繋ぐ行が無い）。"""
    ir = parse_mapping_ir(
        """
version: 1
prefixes:
  exr: "https://example.org/r/"
maps:
  - name: dataset
    source: samples.csv
    subject:
      constant: "exr:dataset"
    properties:
      - predicate: https://example.org/ns#n
        column: n
"""
    )
    assert published_subjects(ir) == []


def test_expand_template_leaves_unknown_prefix_alone() -> None:
    """展開できないものを勝手に直さない —— 不整合はコンパイラが自分の語彙で言う。"""
    assert expand_template("nope:x/{id}", {}) == "nope:x/{id}"
    assert (
        expand_template("https://example.org/x/{id}", {"ex": "https://e/"})
        == "https://example.org/x/{id}"
    )


def test_published_subject_json_roundtrip_and_junk() -> None:
    s = PublishedSubject(
        name="sample",
        source="samples.csv",
        template="https://example.org/r/sample/{sid}",
        transform={"sid": "slug"},
    )
    assert PublishedSubject.from_json(s.to_json()) == s
    # 読めない記録は捨てるだけ（公開を止めない）。
    assert PublishedSubject.from_json({"name": "x"}) is None
    assert PublishedSubject.from_json("nonsense") is None
    assert PublishedSubject.from_json({"name": "x", "source": "s", "template": ""}) is None
