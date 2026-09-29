"""``asterism_api.crosswalk_names`` の単体テスト（契約メモ
contract_b_hub_names.md）: R1（つながりの表示名）・R2（concept の表示名）・
R3（ハブの種類の表示名）の全ての枝。

フィールド名・値は架空の題材（貸し出し・書棚まわり）— 分野固有の名詞は使わ
ない（§0）。
"""

from __future__ import annotations

from asterism import crosswalk_runtime

from asterism_api import crosswalk_names

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
