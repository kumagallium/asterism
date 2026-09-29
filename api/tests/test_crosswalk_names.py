"""``asterism_api.crosswalk_names`` の単体テスト（契約メモ
contract_b_hub_names.md）: R1（つながりの表示名）・R2（concept の表示名）・
R3（ハブの種類の表示名）の全ての枝。

フィールド名・値は架空の題材（貸し出し・書棚まわり）— 分野固有の名詞は使わ
ない（§0）。
"""

from __future__ import annotations

import json
from pathlib import Path

from asterism import crosswalk_runtime

from asterism_api import crosswalk_names, registry

CLASS_IRI = "https://ex/shared#Item"


def _concept(name: str = "shelf_item_name", **kw) -> crosswalk_runtime.RuntimeConcept:
    return crosswalk_runtime.RuntimeConcept(
        name=name,
        class_iri=kw.get("class_iri", CLASS_IRI),
        link_predicate=crosswalk_runtime.DEFAULT_LINK_PREDICATE,
        normalizer=crosswalk_runtime.DEFAULT_NORMALIZER,
        participants=kw.get("participants", ()),
    )


def _participant(
    dataset_id: str = "shelf-a", predicate: str = "https://ex/a#name", **kw
) -> crosswalk_runtime.RuntimeParticipant:
    return crosswalk_runtime.RuntimeParticipant(
        dataset_id=dataset_id,
        label=kw.get("label", dataset_id),
        predicate=predicate,
        predicates=kw.get("predicates", ()),
        subject_class=kw.get("subject_class"),
    )


def _no_labels(_a: str, _b: str, *_c: object) -> None:
    return None


def _field_labels(labels: dict[tuple[str, str], str]):
    def f(dataset_id: str, predicate: str, _subject_class: str | None) -> str | None:
        return labels.get((dataset_id, predicate))

    return f


def _predicate_labels(labels: dict[tuple[str, str], str]):
    def f(dataset_id: str, predicate: str) -> str | None:
        return labels.get((dataset_id, predicate))

    return f


# ---------------------------------------------------------------------------
# R2 — concept_display_name
# ---------------------------------------------------------------------------


def test_r2_uses_the_one_participant_field_label() -> None:
    concept = _concept(participants=(_participant(),))
    field_of = _field_labels({("shelf-a", "https://ex/a#name"): "品名"})
    assert crosswalk_names.concept_display_name(concept, field_of, _no_labels) == "品名"


def test_r2_joins_disagreeing_participant_labels_with_slash() -> None:
    concept = _concept(
        participants=(
            _participant("shelf-a", "https://ex/a#name"),
            _participant("shelf-b", "https://ex/b#title"),
        )
    )
    field_of = _field_labels(
        {("shelf-a", "https://ex/a#name"): "品名", ("shelf-b", "https://ex/b#title"): "書名"}
    )
    assert crosswalk_names.concept_display_name(concept, field_of, _no_labels) == "品名 / 書名"


def test_r2_dedupes_when_participants_agree() -> None:
    concept = _concept(
        participants=(
            _participant("shelf-a", "https://ex/a#name"),
            _participant("shelf-b", "https://ex/b#name"),
        )
    )
    field_of = _field_labels(
        {("shelf-a", "https://ex/a#name"): "品名", ("shelf-b", "https://ex/b#name"): "品名"}
    )
    assert crosswalk_names.concept_display_name(concept, field_of, _no_labels) == "品名"


def test_r2_falls_back_to_predicate_label_when_field_label_is_absent() -> None:
    concept = _concept(participants=(_participant(subject_class="https://ex/a#Book"),))
    predicate_of = _predicate_labels({("shelf-a", "https://ex/a#name"): "名前"})
    assert crosswalk_names.concept_display_name(concept, _no_labels, predicate_of) == "名前"


def test_r2_falls_back_to_humanised_concept_key_when_nothing_resolves() -> None:
    concept = _concept(name="shelf_item_name", participants=(_participant(),))
    got = crosswalk_names.concept_display_name(concept, _no_labels, _no_labels)
    assert got == "shelf item name"


def test_r2_placeholder_key_is_not_humanised() -> None:
    concept = _concept(name="shared_value_1", participants=(_participant(),))
    assert crosswalk_names.concept_display_name(concept, _no_labels, _no_labels) == ""


def test_r2_placeholder_key_bare_form_is_not_humanised() -> None:
    concept = _concept(name="shared_value", participants=(_participant(),))
    assert crosswalk_names.concept_display_name(concept, _no_labels, _no_labels) == ""


# ---------------------------------------------------------------------------
# R1 — perspective_display_name
# ---------------------------------------------------------------------------


def test_r1_a_human_given_name_wins() -> None:
    meta = {"name": "共有たな"}
    assert (
        crosswalk_names.perspective_display_name(meta, None, "shelf-view", _no_labels, _no_labels)
        == "共有たな"
    )


def test_r1_strips_surrounding_whitespace_off_a_human_name() -> None:
    meta = {"name": "  共有たな  "}
    assert (
        crosswalk_names.perspective_display_name(meta, None, "shelf-view", _no_labels, _no_labels)
        == "共有たな"
    )


def test_r1_default_perspective_name_counts_as_a_name() -> None:
    meta = {"name": crosswalk_runtime.DEFAULT_PERSPECTIVE_NAME}
    assert (
        crosswalk_names.perspective_display_name(meta, None, "composition", _no_labels, _no_labels)
        == crosswalk_runtime.DEFAULT_PERSPECTIVE_NAME
    )


def test_r1_empty_name_falls_back_to_concepts() -> None:
    config = crosswalk_runtime.RuntimeCrosswalkConfig(
        concepts=(_concept(name="shelf_item_name", participants=(_participant(),)),)
    )
    meta = {"name": ""}
    assert (
        crosswalk_names.perspective_display_name(meta, config, "shelf-view", _no_labels, _no_labels)
        == "shelf item name"
    )


def test_r1_name_auto_flag_is_treated_as_machine_made() -> None:
    config = crosswalk_runtime.RuntimeCrosswalkConfig(
        concepts=(_concept(name="shelf_item_name", participants=(_participant(),)),)
    )
    meta = {"name": "何かの名前", "name_auto": True}
    assert (
        crosswalk_names.perspective_display_name(meta, config, "shelf-view", _no_labels, _no_labels)
        == "shelf item name"
    )


def test_r1_name_equal_to_a_concept_key_is_machine_made() -> None:
    config = crosswalk_runtime.RuntimeCrosswalkConfig(
        concepts=(_concept(name="shelf_item_name", participants=(_participant(),)),)
    )
    meta = {"name": "shelf_item_name"}
    assert (
        crosswalk_names.perspective_display_name(meta, config, "shelf-view", _no_labels, _no_labels)
        == "shelf item name"
    )


def test_r1_name_equal_to_a_concept_key_from_meta_when_config_is_absent() -> None:
    meta = {"name": "shelf_item_name", "crosswalk_concepts": ["shelf_item_name"]}
    assert (
        crosswalk_names.perspective_display_name(meta, None, "shelf-view", _no_labels, _no_labels)
        == crosswalk_runtime.UNNAMED_PERSPECTIVE_NAME
    )


def test_r1_name_equal_to_the_perspective_id_is_machine_made() -> None:
    config = crosswalk_runtime.RuntimeCrosswalkConfig(
        concepts=(_concept(name="shelf_item_name", participants=(_participant(),)),)
    )
    meta = {"name": "shelf-view"}
    assert (
        crosswalk_names.perspective_display_name(meta, config, "shelf-view", _no_labels, _no_labels)
        == "shelf item name"
    )


def test_r1_name_equal_to_unnamed_constant_is_machine_made() -> None:
    config = crosswalk_runtime.RuntimeCrosswalkConfig(
        concepts=(_concept(name="shelf_item_name", participants=(_participant(),)),)
    )
    meta = {"name": crosswalk_runtime.UNNAMED_PERSPECTIVE_NAME}
    assert (
        crosswalk_names.perspective_display_name(meta, config, "shelf-view", _no_labels, _no_labels)
        == "shelf item name"
    )


def test_r1_old_server_implementation_name_is_machine_made() -> None:
    config = crosswalk_runtime.RuntimeCrosswalkConfig(
        concepts=(_concept(name="shelf_item_name", participants=(_participant(),)),)
    )
    for name in ("crosswalk shelf-view", "crosswalk: shelf-view", "Crosswalk: shelf-view"):
        meta = {"name": name}
        assert (
            crosswalk_names.perspective_display_name(
                meta, config, "shelf-view", _no_labels, _no_labels
            )
            == "shelf item name"
        )


def test_r1_joins_multiple_concept_labels_with_slash_in_config_order() -> None:
    config = crosswalk_runtime.RuntimeCrosswalkConfig(
        concepts=(
            _concept(name="shelf_item_name", participants=(_participant(),)),
            _concept(
                name="shelf_item_maker",
                class_iri="https://ex/shared#Other",
                participants=(_participant("shelf-b", "https://ex/b#maker"),),
            ),
        )
    )
    meta = {"name": ""}
    assert (
        crosswalk_names.perspective_display_name(meta, config, "shelf-view", _no_labels, _no_labels)
        == "shelf item name / shelf item maker"
    )


def test_r1_falls_back_to_unnamed_when_nothing_resolves() -> None:
    meta = {"name": ""}
    assert (
        crosswalk_names.perspective_display_name(meta, None, "shelf-view", _no_labels, _no_labels)
        == crosswalk_runtime.UNNAMED_PERSPECTIVE_NAME
    )


def test_r1_falls_back_to_unnamed_when_every_concept_is_a_placeholder() -> None:
    config = crosswalk_runtime.RuntimeCrosswalkConfig(
        concepts=(_concept(name="shared_value_1", participants=(_participant(),)),)
    )
    meta = {"name": ""}
    assert (
        crosswalk_names.perspective_display_name(meta, config, "shelf-view", _no_labels, _no_labels)
        == crosswalk_runtime.UNNAMED_PERSPECTIVE_NAME
    )


# ---------------------------------------------------------------------------
# R3 — hub_class_display_name
# ---------------------------------------------------------------------------


def test_r3_uses_the_matching_concepts_r2_result() -> None:
    config = crosswalk_runtime.RuntimeCrosswalkConfig(
        concepts=(_concept(name="shelf_item_name", participants=(_participant(),)),)
    )
    field_of = _field_labels({("shelf-a", "https://ex/a#name"): "品名"})
    assert crosswalk_names.hub_class_display_name(CLASS_IRI, config, field_of, _no_labels) == "品名"


def test_r3_falls_back_to_unnamed_when_no_concept_matches_the_class_iri() -> None:
    config = crosswalk_runtime.RuntimeCrosswalkConfig(
        concepts=(_concept(name="shelf_item_name", participants=(_participant(),)),)
    )
    assert (
        crosswalk_names.hub_class_display_name(
            "https://ex/other#Class", config, _no_labels, _no_labels
        )
        == crosswalk_runtime.UNNAMED_PERSPECTIVE_NAME
    )


def test_r3_falls_back_to_unnamed_when_config_is_absent() -> None:
    assert (
        crosswalk_names.hub_class_display_name(CLASS_IRI, None, _no_labels, _no_labels)
        == crosswalk_runtime.UNNAMED_PERSPECTIVE_NAME
    )


def test_r3_falls_back_to_unnamed_when_the_matching_concept_is_a_placeholder() -> None:
    config = crosswalk_runtime.RuntimeCrosswalkConfig(
        concepts=(_concept(name="shared_value_1", participants=(_participant(),)),)
    )
    assert (
        crosswalk_names.hub_class_display_name(CLASS_IRI, config, _no_labels, _no_labels)
        == crosswalk_runtime.UNNAMED_PERSPECTIVE_NAME
    )


# ---------------------------------------------------------------------------
# 作るたびに、機械が付けた名前を表示名で書き直す（refresh_auto_name）
# ---------------------------------------------------------------------------

PERSPECTIVE = "shelf-item-name"


def _write_perspective(
    root: Path,
    *,
    name: str,
    name_auto: bool | None = None,
    auto_linked: bool | None = None,
    extra_concepts: tuple[crosswalk_runtime.RuntimeConcept, ...] = (),
) -> Path:
    """registry に、つながり 1 つ（config と meta）を置く。meta のパスを返す。"""
    config = crosswalk_runtime.RuntimeCrosswalkConfig(
        concepts=(
            _concept(
                participants=(
                    _participant("shelf-a", "https://ex/a#name"),
                    _participant("shelf-b", "https://ex/b#name"),
                )
            ),
            *extra_concepts,
        ),
    )
    crosswalk_runtime.save_config(root, config, PERSPECTIVE)
    registry_id = crosswalk_runtime.crosswalk_registry_id(PERSPECTIVE)
    meta: dict = {
        "id": registry_id,
        "name": name,
        "is_crosswalk": True,
        "crosswalk_perspective_id": PERSPECTIVE,
        "crosswalk_concepts": ["shelf_item_name"],
    }
    if name_auto is not None:
        meta["name_auto"] = name_auto
    if auto_linked is not None:
        meta["auto_linked"] = auto_linked
    meta_path = root / registry_id / "meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return meta_path


_BOTH_NAMED = {
    ("shelf-a", "https://ex/a#name"): "品名",
    ("shelf-b", "https://ex/b#name"): "品名",
}


def test_refresh_rewrites_a_machine_name_with_the_field_label(tmp_path: Path) -> None:
    # 自動でできたつながりで、名前が概念のキーのまま（古いサーバが作ったもの）
    meta_path = _write_perspective(tmp_path, name="shelf_item_name", auto_linked=True)
    got = crosswalk_names.refresh_auto_name(
        tmp_path, PERSPECTIVE, _field_labels(_BOTH_NAMED), _no_labels
    )
    assert got == "品名"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["name"] == "品名"
    assert meta["name_auto"] is True
    # ほかの欄は触らない
    assert meta["crosswalk_concepts"] == ["shelf_item_name"]


def test_refresh_follows_a_later_label_change_while_the_mark_stays(tmp_path: Path) -> None:
    meta_path = _write_perspective(tmp_path, name="品名", name_auto=True)
    renamed = {key: "書棚の品の名前" for key in _BOTH_NAMED}
    got = crosswalk_names.refresh_auto_name(
        tmp_path, PERSPECTIVE, _field_labels(renamed), _no_labels
    )
    assert got == "書棚の品の名前"
    assert json.loads(meta_path.read_text(encoding="utf-8"))["name"] == "書棚の品の名前"


def test_refresh_never_touches_a_human_name(tmp_path: Path) -> None:
    meta_path = _write_perspective(tmp_path, name="書棚どうしの突き合わせ")
    before = meta_path.read_text(encoding="utf-8")
    got = crosswalk_names.refresh_auto_name(
        tmp_path, PERSPECTIVE, _field_labels(_BOTH_NAMED), _no_labels
    )
    assert got is None
    assert meta_path.read_text(encoding="utf-8") == before


def test_refresh_keeps_the_machine_name_when_no_label_resolves(tmp_path: Path) -> None:
    # 表示名が引けないとき、概念のキーを直しただけの語で上書きしても良くならない。
    meta_path = _write_perspective(tmp_path, name="shelf_item_name", auto_linked=True)
    before = meta_path.read_text(encoding="utf-8")
    got = crosswalk_names.refresh_auto_name(tmp_path, PERSPECTIVE, _no_labels, _no_labels)
    assert got is None
    assert meta_path.read_text(encoding="utf-8") == before


def test_refresh_is_a_no_op_for_a_missing_perspective(tmp_path: Path) -> None:
    assert (
        crosswalk_names.refresh_auto_name(
            tmp_path, PERSPECTIVE, _field_labels(_BOTH_NAMED), _no_labels
        )
        is None
    )


def test_a_human_rename_takes_the_machine_mark_off(tmp_path: Path) -> None:
    meta_path = _write_perspective(tmp_path, name="品名", name_auto=True)
    registry_id = crosswalk_runtime.crosswalk_registry_id(PERSPECTIVE)
    registry.rename_dataset(tmp_path, registry_id, "書棚どうしの突き合わせ")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["name"] == "書棚どうしの突き合わせ"
    assert meta["name_auto"] is False
    # 印が外れたので、次に作り直しても人の名前のまま
    got = crosswalk_names.refresh_auto_name(
        tmp_path, PERSPECTIVE, _field_labels(_BOTH_NAMED), _no_labels
    )
    assert got is None
    assert json.loads(meta_path.read_text(encoding="utf-8"))["name"] == "書棚どうしの突き合わせ"


def test_refresh_keeps_a_human_name_that_happens_to_equal_the_concept_key(
    tmp_path: Path,
) -> None:
    """人が、わざと概念のキーと同じ文字列を名前にした（自動でできたつながりでは
    ない）。読むときは表示名を見せるが、書き直しはしない — 人の名前を消さない。"""
    meta_path = _write_perspective(tmp_path, name="shelf_item_name")
    before = meta_path.read_text(encoding="utf-8")
    got = crosswalk_names.refresh_auto_name(
        tmp_path, PERSPECTIVE, _field_labels(_BOTH_NAMED), _no_labels
    )
    assert got is None
    assert meta_path.read_text(encoding="utf-8") == before


def test_refresh_rewrites_the_unnamed_placeholder(tmp_path: Path) -> None:
    meta_path = _write_perspective(tmp_path, name=crosswalk_runtime.UNNAMED_PERSPECTIVE_NAME)
    got = crosswalk_names.refresh_auto_name(
        tmp_path, PERSPECTIVE, _field_labels(_BOTH_NAMED), _no_labels
    )
    assert got == "品名"
    assert json.loads(meta_path.read_text(encoding="utf-8"))["name_auto"] is True


def test_refresh_writes_the_same_name_the_reader_shows(tmp_path: Path) -> None:
    """表示名が引ける concept と引けない concept が混ざるとき、書く名前と、
    読むときの表示名（R1）が同じになる（食い違うと、画面によって同じつながりの
    名前が変わる）。"""
    second = _concept(
        "lending_desk",
        class_iri="https://ex/shared#Desk",
        participants=(_participant("shelf-a", "https://ex/a#desk"),),
    )
    meta_path = _write_perspective(
        tmp_path, name="shelf_item_name", auto_linked=True, extra_concepts=(second,)
    )
    field_of = _field_labels(_BOTH_NAMED)
    got = crosswalk_names.refresh_auto_name(tmp_path, PERSPECTIVE, field_of, _no_labels)
    assert got == "品名 / lending desk"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    config = crosswalk_runtime.load_config(tmp_path, PERSPECTIVE)
    shown = crosswalk_names.perspective_display_name(
        meta, config, PERSPECTIVE, field_of, _no_labels
    )
    assert shown == meta["name"] == "品名 / lending desk"
