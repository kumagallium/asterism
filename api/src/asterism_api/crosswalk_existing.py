"""registry に永続化された既存のつながり一覧（契約メモ contract_d_discover_existing.md
R5・contract_d2_discover_existing.md D2-1/D2-2。担当 api-crosswalk-existing）。

`asterism.crosswalk_discover.discover` の合流先判定
（`match_existing`）は純粋関数でストアも registry も読まない。この module が
その橋渡し役 — `crosswalk_runtime.list_perspectives`/`load_config` で registry
から今ある全 perspective の全 concept を読み、`ExistingConcept` に変換する
（``predicate_label_of`` などの表示名引き手と同じ「渡す」形）。

``POST /api/crosswalk/discover`` と ``autolink.maybe_autolink_handles`` の両方
がこれを使う。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from asterism import crosswalk_runtime
from asterism.crosswalk_discover import ExistingConcept

__all__ = ["ExistingCrosswalks", "load_existing_concepts"]


@dataclass(frozen=True)
class ExistingCrosswalks:
    """`load_existing_concepts` の返り値（D2-2）。``concepts`` は突き合わせ
    （R2）に使う一覧、``perspective_ids`` は registry にある全 perspective の
    id（config が読めるかどうかに関わらず）— R3 が「避ける id」に使う。"""

    concepts: list[ExistingConcept]
    perspective_ids: frozenset[str]


def load_existing_concepts(registry_root: Path | str) -> ExistingCrosswalks:
    """registry の全 perspective の全 concept を ``ExistingConcept`` の一覧に
    する。読めない config（未生成・壊れている）の concept は一覧から飛ばす
    が、その perspective の id は ``perspective_ids`` に残す（D2-2: id だけは
    避けるべき）。

    複合キー（``key_parts`` が非空）の concept も一覧に含める（D2-1）。
    突き合わせ（R1: 候補は常に単一の述語なので複合キーとは絶対に一致しない）
    に当たらないよう ``slots`` は空にし、``dataset_ids`` はそのまま渡す —
    R3 の「避ける名前」に、複合キーの concept の名前・perspective の id も
    入るようにするため。
    """
    concepts: list[ExistingConcept] = []
    perspective_ids: set[str] = set()
    for meta in crosswalk_runtime.list_perspectives(registry_root):
        perspective_id = str(
            meta.get("crosswalk_perspective_id") or crosswalk_runtime.DEFAULT_PERSPECTIVE_ID
        )
        perspective_ids.add(perspective_id)
        try:
            config = crosswalk_runtime.load_config(registry_root, perspective_id)
        except (ValueError, yaml.YAMLError):
            continue
        if config is None:
            continue
        for concept in config.concepts:
            dataset_ids = frozenset(p.dataset_id for p in concept.participants)
            if concept.key_parts:
                slots: frozenset[tuple[str, str, str | None]] = frozenset()
            else:
                slots = frozenset(
                    (p.dataset_id, p.predicate, p.subject_class) for p in concept.participants
                )
            concepts.append(
                ExistingConcept(
                    perspective_id=perspective_id,
                    name=concept.name,
                    class_iri=concept.class_iri,
                    link_predicate=concept.link_predicate,
                    slots=slots,
                    dataset_ids=dataset_ids,
                )
            )
    return ExistingCrosswalks(concepts=concepts, perspective_ids=frozenset(perspective_ids))
