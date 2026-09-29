"""registry に永続化された既存のつながり一覧（契約メモ contract_d_discover_existing.md
R5。担当 api-crosswalk-existing）。

`asterism.crosswalk_discover.discover` の合流先判定
（`match_existing`）は純粋関数でストアも registry も読まない。この module が
その橋渡し役 — `crosswalk_runtime.list_perspectives`/`load_config` で registry
から今ある全 perspective の全 concept を読み、`ExistingConcept` に変換する
（``predicate_label_of`` などの表示名引き手と同じ「渡す」形）。

``POST /api/crosswalk/discover`` と ``autolink.maybe_autolink_handles`` の両方
がこれを使う。
"""

from __future__ import annotations

from pathlib import Path

import yaml
from asterism import crosswalk_runtime
from asterism.crosswalk_discover import ExistingConcept

__all__ = ["load_existing_concepts"]


def load_existing_concepts(registry_root: Path | str) -> list[ExistingConcept]:
    """registry の全 perspective の全 concept を ``ExistingConcept`` の一覧に
    する。読めない config（未生成・壊れている）は飛ばす。

    複合キー（``key_parts`` が非空）の concept は一覧に含めない（R1: 候補は
    常に単一の述語なので複合キーとは絶対に一致しない — 突き合わせの対象外に
    することを、一覧を作る側でやる）。
    """
    out: list[ExistingConcept] = []
    for meta in crosswalk_runtime.list_perspectives(registry_root):
        perspective_id = str(
            meta.get("crosswalk_perspective_id") or crosswalk_runtime.DEFAULT_PERSPECTIVE_ID
        )
        try:
            config = crosswalk_runtime.load_config(registry_root, perspective_id)
        except (ValueError, yaml.YAMLError):
            continue
        if config is None:
            continue
        for concept in config.concepts:
            if concept.key_parts:
                continue
            slots = frozenset(
                (p.dataset_id, p.predicate, p.subject_class) for p in concept.participants
            )
            dataset_ids = frozenset(p.dataset_id for p in concept.participants)
            out.append(
                ExistingConcept(
                    perspective_id=perspective_id,
                    name=concept.name,
                    class_iri=concept.class_iri,
                    link_predicate=concept.link_predicate,
                    slots=slots,
                    dataset_ids=dataset_ids,
                )
            )
    return out
