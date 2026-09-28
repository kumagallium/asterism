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

from asterism import crosswalk_runtime as crosswalk_runtime_mod
from asterism import dataset_summary as dataset_summary_mod
from asterism import subjects as subjects_mod
from asterism.crosswalk import XW as _XW_NS
from asterism.prov_graph import PROV as _PROV_NS
from asterism.substrate import SupportsSparql, canonical_from_clauses

__all__ = ["classes_index"]

_META_FILE = "meta.json"
_RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
#: HUB graph は ABox（メンバー実体）と TBox 宣言（``<class> a owl:Class``。
#: :func:`asterism.crosswalk.build_turtle` 参照）を同じ graph に書くので、
#: ``?s a ?class`` はハブの種類そのものだけでなく ``owl:Class`` 自身も
#: 主語として拾ってしまう — 通常データセットの ontology は別 graph なので
#: 起きない、HUB 固有の混線を人が見る前に機械が除く（PROV 除外と同じ考え方）。
_XW_LINK_CLASS = _XW_NS + "CrosswalkLink"
_OWL_CLASS_IRI = "http://www.w3.org/2002/07/owl#Class"


def _promoted_datasets(registry_root: Path) -> list[tuple[str, dict[str, Any]]]:
    """``registry_root`` 直下の、``meta.json`` の ``promoted`` が truthy な
    データセットの ``(id, meta)``（ディレクトリ名順 — 最終的な並びは呼び出し側が
    件数→名前で並べ替えるので、ここでの順は決定論でさえあればよい）。

    ``asterism.class_schema``'s ``_promoted_metas`` と同じ判定
    （``meta.get("promoted")``）だが、あちらは private かつこのモジュールが
    要る「id と meta の一覧」とは形が違うので duplicate する（このコードベース
    の既存の流儀 — :mod:`asterism.subjects` の docstring 参照）。
    """
    if not registry_root.is_dir():
        return []
    out: list[tuple[str, dict[str, Any]]] = []
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
            out.append((dataset_id, meta))
    return out


def _hub_perspective_id(meta: dict[str, Any]) -> str | None:
    """``meta.crosswalk_perspective_id``（HUB の registry scaffold が書く欄。
    契約メモ §1.2） — 文字列で空でなければそれ、それ以外は ``None``。"""
    pid = meta.get("crosswalk_perspective_id")
    return pid.strip() if isinstance(pid, str) and pid.strip() else None


async def _hub_class_label(client: SupportsSparql, hub_graph: str, class_iri: str) -> str | None:
    """``class_iri`` の HUB graph 内の ``rdfs:label``（無ければ ``None``）。

    HUB のクラスは :func:`asterism.crosswalk.build_turtle` が HUB graph 自身に
    ``rdfs:label`` を書く（各データセットの ``ontology/`` named graph とは別の
    場所なので、既存の :func:`asterism.class_schema.class_label` の探索順では
    見つからない — 契約メモ §1.2 が「class の rdfs:label があればそれ」と直接
    HUB graph を指す理由）。
    """
    ref = subjects_mod.safe_iri(class_iri)
    if ref is None:
        return None
    from_clause = canonical_from_clauses([hub_graph])
    query = f"SELECT ?label\n{from_clause}WHERE {{ <{ref}> <{_RDFS_LABEL}> ?label }} LIMIT 1"
    raw = await client.sparql_select(query)
    results = raw.get("results", {}) if isinstance(raw, dict) else {}
    rows = results.get("bindings", []) if isinstance(results, dict) else []
    if not rows:
        return None
    node = rows[0].get("label")
    return node.get("value") if isinstance(node, dict) else None


async def _hub_classes(
    client: SupportsSparql,
    registry_root: Path,
    dataset_id: str,
    dataset_label: str,
    perspective_id: str,
) -> list[dict[str, Any]]:
    """HUB データセット 1 件分の行（契約メモ §1.2）: ハブの graph を直接
    ``?s a ?class`` で数える — 通常の :func:`dataset_summary_mod.dataset_summary`
    は ``dataset_id_of_canonical_graph(g) == id`` で絞るため、registry id
    （``crosswalk-bridge``/``crosswalk-<pid>``）と graph id
    （``crosswalk``/``crosswalk/<pid>``）が食い違う HUB では 0 件になってしまう。
    """
    try:
        hub_graph = crosswalk_runtime_mod.crosswalk_graph_iri(perspective_id)
    except ValueError:
        return []
    graphs = [hub_graph]
    entries: list[dict[str, Any]] = []
    for class_iri in await dataset_summary_mod._classes_in_dataset(client, graphs):
        if class_iri == _OWL_CLASS_IRI:
            continue
        # ハブの graph には per-link の来歴（xw:CrosswalkLink）と build の
        # prov:Activity も載るが、人が「種類」として辿るものではない
        # （実機 2026-09-25: 3 つとも同じ名前で並んだ）。
        if class_iri == _XW_LINK_CLASS or class_iri.startswith(_PROV_NS):
            continue
        entry = await dataset_summary_mod._class_entry(client, registry_root, graphs, class_iri)
        label = await _hub_class_label(client, hub_graph, class_iri) or dataset_label
        entries.append(
            {
                **entry,
                "label": label,
                "dataset_id": dataset_id,
                "dataset_label": dataset_label,
                "is_demo": False,
                "is_hub": True,
                "hub_perspective_id": perspective_id,
            }
        )
    return entries


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
    for dataset_id, meta in _promoted_datasets(root):
        perspective_id = _hub_perspective_id(meta)
        if perspective_id is not None:
            dataset_label = meta.get("name") or dataset_id
            entries.extend(
                await _hub_classes(client, root, dataset_id, dataset_label, perspective_id)
            )
            continue
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
