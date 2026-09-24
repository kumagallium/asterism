"""``classes_index(client, registry_root)`` — 契約メモ contract_pr_f9.md §2.1
（担当 api）。

``GET /api/classes`` の薄い api ルート（``asterism_api.classes_routes``）が、
この 1 純関数を呼ぶだけになるようにする — 「追加画面」の左（Asterism にある
種類すべて）が、promoted な**データセットすべて**の
:func:`asterism.dataset_summary.dataset_summary` の ``classes`` を束ねた
1 か所の出力から組み立つ。

来歴 (PROV) のクラスは :func:`asterism.dataset_summary.dataset_summary` 自身が
既に除いている（契約メモ §2.1: 「来歴のクラスは既に除かれている」）ので、
ここで二重に判定しない。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from asterism import dataset_summary as dataset_summary_mod
from asterism import subjects as subjects_mod
from asterism.substrate import SupportsSparql

__all__ = ["classes_index"]

_META_FILE = "meta.json"


def _promoted_dataset_ids(registry_root: Path) -> list[str]:
    """``registry_root`` 直下の、``meta.json`` の ``promoted`` が truthy な
    データセット id（ディレクトリ名順 — 最終的な並びは呼び出し側が件数→
    名前で並べ替えるので、ここでの順は決定論でさえあればよい）。

    ``asterism.class_schema``'s ``_promoted_metas`` と同じ判定
    （``meta.get("promoted")``）だが、あちらは private かつこのモジュールが
    要る「id だけの一覧」とは形が違うので duplicate する（このコードベース
    の既存の流儀 — :mod:`asterism.subjects` の docstring 参照）。
    """
    if not registry_root.is_dir():
        return []
    ids: list[str] = []
    for child in sorted(registry_root.iterdir()):
        if not child.is_dir():
            continue
        dataset_id = subjects_mod.valid_dataset_id(child.name)
        if dataset_id is None:
            continue
        meta_path = child / _META_FILE
        if not meta_path.is_file():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(meta, dict) and meta.get("promoted"):
            ids.append(dataset_id)
    return ids


async def classes_index(
    client: SupportsSparql, registry_root: Path | str | None
) -> list[dict[str, Any]]:
    """契約メモ §2.1 の ``{"classes": [...]}`` の中身（並び替え済み）。

    promoted なデータセットが 0 件、または ``registry_root`` が無ければ
    空リスト。並びは件数の多い順→名前順（ラベルが同じ/無いときは
    ``class_iri`` で決定論のタイブレーク）。
    """
    if registry_root is None:
        return []
    root = Path(registry_root)
    entries: list[dict[str, Any]] = []
    for dataset_id in _promoted_dataset_ids(root):
        summary = await dataset_summary_mod.dataset_summary(client, root, dataset_id)
        if summary is None:
            continue
        for c in summary["classes"]:
            entries.append(
                {
                    "class_iri": c["class_iri"],
                    "label": c["label"],
                    "count": c["count"],
                    "dataset_id": summary["dataset_id"],
                    "dataset_label": summary["label"],
                    "is_demo": summary["is_demo"],
                    "properties": c["properties"],
                    "with_label": c["with_label"],
                    "with_unit": c["with_unit"],
                }
            )
    entries.sort(key=lambda e: (-e["count"], e["label"] or "", e["class_iri"]))
    return entries
