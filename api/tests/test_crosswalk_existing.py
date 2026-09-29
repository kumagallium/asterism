"""``crosswalk_existing.load_existing_concepts`` の単体テスト（契約メモ
contract_d2_discover_existing.md D2-1/D2-2）。

複合キーの concept・読めない config の perspective が、R3 の「避ける名前・id」
に正しく入るかを、``ExistingConcept``/``ExistingCrosswalks`` の形で確かめる。
"""

from __future__ import annotations

from pathlib import Path

from asterism import crosswalk_runtime

from asterism_api import crosswalk_existing

CLASS_BOOK = "http://example.org/onto#Book"
LINK_HAS_BOOK = "http://example.org/onto#hasBook"
PRED_TITLE = "http://example.org/onto#title"
PRED_YEAR = "http://example.org/onto#year"


def _outcome() -> crosswalk_runtime.BuildOutcome:
    return crosswalk_runtime.BuildOutcome(
        built_at="2026-01-01T00:00:00Z",
        hub_graph="",
        triple_count=0,
        shared={},
        links={},
        participants_used=[],
        participants_skipped=[],
    )


def _save_and_scaffold(
    root: Path, config: crosswalk_runtime.RuntimeCrosswalkConfig, perspective_id: str, name: str
) -> None:
    crosswalk_runtime.save_config(root, config, perspective_id)
    crosswalk_runtime.write_registry_scaffold(
        root, config, _outcome(), perspective_id=perspective_id, name=name
    )


def test_compound_key_concept_is_included_with_empty_slots(tmp_path: Path) -> None:
    """D2-1: 複合キーの concept も一覧に入る（slots は空 = 突き合わせの対象外の
    ままだが、名前と id は avoid の対象になる）。"""
    root = tmp_path / "registry"
    config = crosswalk_runtime.parse_config(
        {
            "concepts": [
                {
                    "name": "book_edition",
                    "class_iri": CLASS_BOOK,
                    "link_predicate": LINK_HAS_BOOK,
                    "key_parts": [{"name": "title"}, {"name": "year"}],
                    "participants": [
                        {
                            "dataset_id": "ds-a",
                            "label": "ds-a",
                            "predicates": {"title": PRED_TITLE, "year": PRED_YEAR},
                        },
                        {
                            "dataset_id": "ds-b",
                            "label": "ds-b",
                            "predicates": {"title": PRED_TITLE, "year": PRED_YEAR},
                        },
                    ],
                }
            ]
        }
    )
    _save_and_scaffold(root, config, "book-edition", "本の版")

    result = crosswalk_existing.load_existing_concepts(root)

    assert len(result.concepts) == 1
    concept = result.concepts[0]
    assert concept.name == "book_edition"
    assert concept.slots == frozenset()
    assert concept.dataset_ids == frozenset({"ds-a", "ds-b"})
    assert "book-edition" in result.perspective_ids


def test_unreadable_config_still_reserves_the_perspective_id(tmp_path: Path) -> None:
    """D2-2: config が壊れている（読めない）perspective は concept の一覧から
    落ちるが、id は ``perspective_ids`` に残る。"""
    root = tmp_path / "registry"
    perspective_id = "broken"
    reg_dir = root / crosswalk_runtime.crosswalk_registry_id(perspective_id)
    reg_dir.mkdir(parents=True)
    (reg_dir / "meta.json").write_text(
        '{"id": "crosswalk-broken", "is_crosswalk": true, '
        '"crosswalk_perspective_id": "broken", "created_at": "2026-01-01T00:00:00Z"}',
        encoding="utf-8",
    )
    # 壊れた yaml（parse_config が ValueError を投げる形: concepts が無い）。
    (reg_dir / "crosswalk.yaml").write_text("min_datasets: 2\n", encoding="utf-8")

    result = crosswalk_existing.load_existing_concepts(root)

    assert result.concepts == []
    assert "broken" in result.perspective_ids


def test_readable_config_perspective_id_is_reserved_too(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    config = crosswalk_runtime.parse_config(
        {
            "concepts": [
                {
                    "name": "title",
                    "class_iri": CLASS_BOOK,
                    "link_predicate": LINK_HAS_BOOK,
                    "participants": [
                        {"dataset_id": "ds-a", "label": "ds-a", "predicate": PRED_TITLE},
                        {"dataset_id": "ds-b", "label": "ds-b", "predicate": PRED_TITLE},
                    ],
                }
            ]
        }
    )
    _save_and_scaffold(root, config, "book-title", "本のタイトル")

    result = crosswalk_existing.load_existing_concepts(root)

    assert "book-title" in result.perspective_ids
    assert len(result.concepts) == 1
    assert result.concepts[0].slots == frozenset(
        {
            ("ds-a", PRED_TITLE, None),
            ("ds-b", PRED_TITLE, None),
        }
    )
