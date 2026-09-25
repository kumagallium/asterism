"""公開時の自動リンク（契約メモ contract_pr_f15.md §1.4。担当 api-autolink）。

S4 で ☑ した列（``handles.json``）は、公開（promote）の瞬間に、他の promoted
データセットの ☑ 列と値が重なるかどうかを機械が調べ、重なっていれば「つなが
り」ハブ（クロスウォーク・パースペクティブ）へ自動で参加させる。人の裁定は
☑（両側が opt-in）で済んでいるので、それ以外の判断（どの値が同じ意味か）だ
けを決定論の突き合わせ（``asterism.crosswalk_discover.discover``）に任せる。

``discover``/``build`` はテスト用の差し替え口。既定は本物の
``crosswalk_discover.discover`` と、内部の ``_default_build``（perspective の
新規作成／既存への参加者追加を ``crosswalk_runtime`` の手順で行う）。

例外は握りつぶす（``_maybe_rebuild_crosswalk`` と同じ流儀）— 自動リンクの失敗
が公開そのものを止めてはいけない。
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from asterism import crosswalk_discover, crosswalk_runtime

from asterism_api import handles as handles_mod
from asterism_api import registry

logger = logging.getLogger(__name__)

__all__ = ["maybe_autolink_handles"]


class DiscoverFn(Protocol):
    async def __call__(self, client: Any, datasets: Any, **kwargs: Any) -> dict: ...


class BuildFn(Protocol):
    async def __call__(
        self, client: Any, registry_root: Any, perspective_id: str, candidate: dict
    ) -> dict: ...


def _label_for(dataset_id: str, name: str) -> str:
    """自動リンクの参加者ラベルは **dataset_id そのもの**（契約 F15 §1.4）。
    手動の「つながり」画面はデータセット名の slug を使うが、名前は重複し得る
    （既定の名前のまま公開したデータセットが 2 つあると同じ slug になり、
    crosswalk_runtime は label をキーに観測を持つので片方が消える）。id は
    一意なので衝突しない。``name`` は将来の表示用に受け取るだけ。"""
    del name
    return dataset_id


def _slots_of(dataset: dict) -> list[dict]:
    """1 データセットの ``artifacts`` から ☑ 列の述語スロットを引く。壊れて
    いる／☑ が無ければ空。"""
    artifacts = dataset.get("artifacts") or {}
    own_handles = handles_mod.load_handles(artifacts)
    if not own_handles:
        return []
    return handles_mod.handle_slots(str(artifacts.get("mapping.yaml") or ""), own_handles)


def _dedupe_participants(raw_participants: list[dict]) -> list[dict]:
    """同じ ``dataset_id`` が 2 回出てきたら最初の 1 つだけ残す（1 データセッ
    ト 1 スロット／concept — 候補の participants 自体に重複がある場合の保険）。"""
    seen: set[str] = set()
    out: list[dict] = []
    for part in raw_participants:
        dsid = str(part.get("dataset_id") or "")
        if not dsid or dsid in seen:
            continue
        seen.add(dsid)
        out.append(part)
    return out


def _amend_concept(
    config: crosswalk_runtime.RuntimeCrosswalkConfig, cand_concept: dict
) -> tuple[crosswalk_runtime.RuntimeCrosswalkConfig, list[dict]]:
    """既存 perspective の config に、候補の participants のうちその concept に
    まだ参加していないデータセットだけ足す。concept 自体が無ければ丸ごと足す。

    戻り値は (新しい config, 実際に足した [{"dataset_id","predicate"}, ...])。
    """
    name = str(cand_concept.get("name") or "")
    cand_participants = _dedupe_participants(cand_concept.get("participants") or [])
    new_concepts: list[crosswalk_runtime.RuntimeConcept] = []
    found = False
    added: list[dict] = []
    for concept in config.concepts:
        if concept.name != name:
            new_concepts.append(concept)
            continue
        found = True
        existing_ids = {p.dataset_id for p in concept.participants}
        participants = list(concept.participants)
        for part in cand_participants:
            dsid = str(part.get("dataset_id") or "")
            predicate = str(part.get("predicate") or "")
            if not dsid or not predicate or dsid in existing_ids:
                continue
            participants.append(
                crosswalk_runtime.RuntimeParticipant(
                    dataset_id=dsid,
                    label=str(part.get("label") or dsid),
                    predicate=predicate,
                    subject_class=str(part.get("subject_class") or "").strip() or None,
                )
            )
            existing_ids.add(dsid)
            added.append({"dataset_id": dsid, "predicate": predicate})
        new_concepts.append(replace(concept, participants=tuple(participants)))
    if not found:
        concept_dict = dict(cand_concept)
        concept_dict["participants"] = cand_participants
        try:
            parsed = crosswalk_runtime.parse_config({"concepts": [concept_dict]})
        except ValueError:
            return config, []
        new_concepts.append(parsed.concepts[0])
        added = [
            {"dataset_id": p.dataset_id, "predicate": p.predicate}
            for p in parsed.concepts[0].participants
        ]
    return replace(config, concepts=tuple(new_concepts)), added


async def _default_build(
    client: Any, registry_root: Any, perspective_id: str, candidate: dict
) -> dict:
    """候補 1 件を build する既定の実装: perspective が無ければ
    ``candidate["build_config"]`` からそのまま作る。あれば既存 concept に参加
    者を足す（``_do_crosswalk_build`` と同じ内部手順: parse_config → save_config
    → build_hub → write_registry_scaffold）。"""
    build_config = candidate.get("build_config") or {}
    cand_concepts = build_config.get("concepts") or []
    if not cand_concepts:
        return {"created": False, "participants_added": []}

    existing = crosswalk_runtime.load_config(registry_root, perspective_id)
    if existing is None:
        concept_dict = dict(cand_concepts[0])
        concept_dict["participants"] = _dedupe_participants(concept_dict.get("participants") or [])
        try:
            config = crosswalk_runtime.parse_config(
                {"min_datasets": build_config.get("min_datasets", 2), "concepts": [concept_dict]}
            )
        except ValueError:
            return {"created": False, "participants_added": []}
        created = True
        added = [
            {"dataset_id": p.dataset_id, "predicate": p.predicate}
            for concept in config.concepts
            for p in concept.participants
        ]
    else:
        config, added = _amend_concept(existing, cand_concepts[0])
        created = False
        if not added:
            return {"created": False, "participants_added": []}

    crosswalk_runtime.save_config(registry_root, config, perspective_id)
    outcome = await crosswalk_runtime.build_hub(
        client,
        config,
        built_at=datetime.now(UTC).isoformat(),
        perspective_id=perspective_id,
    )
    crosswalk_runtime.write_registry_scaffold(
        registry_root,
        config,
        outcome,
        perspective_id=perspective_id,
        name=str(candidate.get("name") or ""),
    )
    return {"created": created, "participants_added": added}


def _mark_auto_linked(registry_root: Any, perspective_id: str, from_dataset_id: str) -> None:
    """perspective の ``meta.json`` に ``auto_linked``/``auto_linked_from`` を書
    き戻す（``registry.mark_promoted`` と同じ読み書きの流儀）。無ければ何もし
    ない（``write_registry_scaffold`` は必ずこれを作るので通常は無いことはな
    い — ここは best-effort の保険）。"""
    meta_path = (
        Path(registry_root) / crosswalk_runtime.crosswalk_registry_id(perspective_id) / "meta.json"
    )
    if not meta_path.is_file():
        return
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    meta["auto_linked"] = True
    existing_from = [d for d in (meta.get("auto_linked_from") or []) if isinstance(d, str)]
    if from_dataset_id not in existing_from:
        existing_from.append(from_dataset_id)
    meta["auto_linked_from"] = existing_from
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


async def maybe_autolink_handles(
    client: Any,
    registry_root: Any,
    dataset_id: str,
    *,
    discover: DiscoverFn | None = None,
    build: BuildFn | None = None,
) -> dict:
    """公開されたばかりの ``dataset_id`` の ☑ 列を、他の promoted データセット
    の ☑ 列と突き合わせて自動でハブへ参加させる。

    戻り値（報告用）:
    ``{"linked": [{"perspective_id","name","created","participants_added"}],
       "skipped": [{"reason": "no_handles"|"no_partner"|"no_candidates"|"error", ...}]}``

    例外は決して外に投げない（``logger.exception`` して ``skipped`` に
    ``"error"`` を積んで戻す）。
    """
    discover_fn: DiscoverFn = discover or crosswalk_discover.discover  # type: ignore[assignment]
    build_fn: BuildFn = build or _default_build  # type: ignore[assignment]
    report: dict[str, list[dict]] = {"linked": [], "skipped": []}
    try:
        own = registry.load_dataset(registry_root, dataset_id)
        if own is None:
            report["skipped"].append({"reason": "no_handles", "dataset_id": dataset_id})
            return report
        own_slots = _slots_of(own)
        if not own_slots:
            report["skipped"].append({"reason": "no_handles", "dataset_id": dataset_id})
            return report

        only_slots: dict[str, set[tuple[str | None, str]]] = {
            dataset_id: {(s["class_iri"], s["predicate"]) for s in own_slots}
        }
        this_name = str(own.get("meta", {}).get("name") or dataset_id)
        targets = [
            crosswalk_discover.DiscoverDataset(
                dataset_id=dataset_id, label=_label_for(dataset_id, this_name), name=this_name
            )
        ]
        for meta in registry.list_datasets(registry_root):
            other_id = str(meta.get("id") or "")
            if not other_id or other_id == dataset_id:
                continue
            if not meta.get("promoted") or meta.get("is_crosswalk"):
                continue
            other = registry.load_dataset(registry_root, other_id)
            if other is None:
                continue
            other_slots = _slots_of(other)
            if not other_slots:
                continue
            only_slots[other_id] = {(s["class_iri"], s["predicate"]) for s in other_slots}
            other_name = str(meta.get("name") or other_id)
            targets.append(
                crosswalk_discover.DiscoverDataset(
                    dataset_id=other_id,
                    label=_label_for(other_id, other_name),
                    name=other_name,
                )
            )
        if len(targets) < 2:
            report["skipped"].append({"reason": "no_partner", "dataset_id": dataset_id})
            return report

        limits = crosswalk_discover.DiscoverLimits(min_datasets=2, min_shared_keys=1)
        result = await discover_fn(client, targets, limits=limits, only_slots=only_slots)
        candidates = sorted(
            result.get("candidates") or [],
            key=lambda c: (-float(c.get("score") or 0.0), str(c.get("perspective_id") or "")),
        )
        if not candidates:
            report["skipped"].append({"reason": "no_candidates", "dataset_id": dataset_id})
            return report

        for candidate in candidates:
            perspective_id = str(candidate.get("perspective_id") or "")
            if not perspective_id:
                continue
            outcome = await build_fn(client, registry_root, perspective_id, candidate)
            created = bool(outcome.get("created"))
            participants_added = list(outcome.get("participants_added") or [])
            if not created and not participants_added:
                continue  # already fully joined — idempotent re-promote, nothing to report
            _mark_auto_linked(registry_root, perspective_id, dataset_id)
            report["linked"].append(
                {
                    "perspective_id": perspective_id,
                    "name": str(candidate.get("name") or perspective_id),
                    "created": created,
                    "participants_added": participants_added,
                }
            )
    except Exception:  # never block a promote on auto-linking (同 _maybe_rebuild_crosswalk)
        logger.exception("autolink after promote of %s failed (continuing)", dataset_id)
        report["skipped"].append({"reason": "error", "dataset_id": dataset_id})
    return report
