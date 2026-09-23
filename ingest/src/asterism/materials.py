"""材料表（Material）と「配れるか」の判定（object-cards-ui.md O19 / 契約メモ §2）。

1 件・絞り込みの各カードが読んだ材料（＝出典・証拠のデータセット）を列挙し、
それを他人に配ってよいか（``shareable``）を**保守側**（O13）で判定する:
どれか 1 件でも自分のデータ（``own``）を含む・出どころが分からない
（``unknown``）・ライセンスが分からない・再配布不可なら、配れない。材料が
1 件も無いときも配れない（判定できることが無い＝保守側）。

``dataset_license``（``asterism.licenses``、D1-license が並行実装）と
``dataset_origin``（``asterism_api.registry``、同じく D1-license が並行実装）
は、このモジュールの作成時点でまだ存在しないことがあるため、モジュール読み込み
時ではなく呼び出し時に遅延 import する。どちらも見つからない／失敗したときは
「不明」側（``kind='unknown'``・``license=None``・``redistributable=None``）に
倒れる — 保守側の判定はこの既定値だけで正しく False になる。``_lookup_origin``
/ ``_lookup_license`` を分けているのは、テストがこの 2 つの呼び出し口だけを
monkeypatch できるようにするため（``subject_tools._load_class_schema`` と同じ
流儀）。

分野固有の名詞・LLM 呼び出し・生成コード実行は無い（§0）。
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["Material", "materials_for", "shareable", "shareable_reasons"]

#: ``shareable_reasons`` が返しうる値（固定語彙・順序も固定 — 契約メモ §2）。
_OWN_DATA = "own_data"
_UNKNOWN_ORIGIN = "unknown_origin"
_UNKNOWN_LICENSE = "unknown_license"
_NOT_REDISTRIBUTABLE = "not_redistributable"
_NO_MATERIALS = "no_materials"

_KNOWN_ORIGIN_KINDS = frozenset({"own", "open", "unknown"})


@dataclass(frozen=True)
class Material:
    """1 データセットが 1 件の結果に寄与した分（object-cards-ui.md O19）。"""

    kind: str  # 'own' | 'open' | 'unknown'
    dataset_id: str
    dataset_label: str  # meta.name（無ければ dataset_id）
    snapshot: str | None  # 'v3' など
    license: str | None  # 正規化後（asterism.licenses.dataset_license の返り値そのまま）
    redistributable: bool | None
    count: int  # この結果に寄与した三つ組か行の数

    def to_dict(self) -> dict[str, Any]:
        """API レスポンスに載せる JSON 形（キー順は Material のフィールド順）。"""
        return asdict(self)


def _lookup_origin(registry_root: Path | str | None, dataset_id: str) -> str | None:
    """``asterism_api.registry.dataset_origin`` への遅延 import。D1-license 未着地
    のときや失敗時は ``None``（=不明）を返す — 決して例外を外に出さない。"""
    try:
        from asterism_api.registry import dataset_origin  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        return dataset_origin(registry_root, dataset_id)
    except Exception:  # best-effort: D1-license 側の実装詳細に依存しない
        logger.debug("materials: dataset_origin lookup failed for %r", dataset_id, exc_info=True)
        return None


def _lookup_license(
    registry_root: Path | str | None, dataset_id: str
) -> tuple[str | None, bool | None] | None:
    """``asterism.licenses.dataset_license`` への遅延 import。未着地／失敗時は
    ``None``（=不明。呼び出し側で ``(None, None)`` として扱う）。"""
    try:
        from asterism.licenses import dataset_license
    except ImportError:
        return None
    try:
        return dataset_license(registry_root, dataset_id)
    except Exception:  # best-effort: D1-license 側の実装詳細に依存しない
        logger.debug("materials: dataset_license lookup failed for %r", dataset_id, exc_info=True)
        return None


def _dataset_label(registry_root: Path | str | None, dataset_id: str) -> str:
    """registry の ``meta.json`` の ``name``（無ければ ``dataset_id`` そのもの）。
    ``subject_tools._dataset_labels`` と同じ規則だが、循環 import を避けるため
    ここで直接 ``meta.json`` を読む（§0: 担当ファイル別・共有 module を増やさ
    ない、既存の流儀を踏襲）。"""
    if registry_root is None:
        return dataset_id
    meta_path = Path(registry_root) / dataset_id / "meta.json"
    if not meta_path.is_file():
        return dataset_id
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dataset_id
    if isinstance(meta, dict):
        name = meta.get("name")
        if isinstance(name, str) and name:
            return name
    return dataset_id


def materials_for(
    registry_root: Path | str | None, contributions: list[tuple[str, str | None, int]]
) -> list[Material]:
    """``contributions``（``[(dataset_id, snapshot, count), …]`` — 1 件／絞り込み
    の結果に寄与したデータセットの集計）を、出どころ・ライセンスを埋めた
    :class:`Material` の一覧にする（入力の順序どおり）。"""
    out: list[Material] = []
    for dataset_id, snapshot, count in contributions:
        origin = _lookup_origin(registry_root, dataset_id)
        kind = origin if origin in _KNOWN_ORIGIN_KINDS else "unknown"
        looked_up_license = _lookup_license(registry_root, dataset_id)
        license_norm, redistributable = (
            looked_up_license if looked_up_license is not None else (None, None)
        )
        out.append(
            Material(
                kind=kind,
                dataset_id=dataset_id,
                dataset_label=_dataset_label(registry_root, dataset_id),
                snapshot=snapshot,
                license=license_norm,
                redistributable=redistributable,
                count=count,
            )
        )
    return out


def shareable(materials: list[Material]) -> bool:
    """配れるか（O13: 保守側）。材料が 1 件も無い、またはどれか 1 件でも
    ``own``／再配布可と確認できていない（``open`` かつ ``redistributable is True``
    でない）なら False。"""
    return len(materials) > 0 and all(
        m.kind == "open" and m.redistributable is True for m in materials
    )


def shareable_reasons(materials: list[Material]) -> list[str]:
    """``shareable`` が False の理由（固定語彙・固定順 — 契約メモ §2）。True の
    ときは空リスト。理由の重複は 1 回にまとめる（材料が何件あっても種別ごと
    1 回）。"""
    if not materials:
        return [_NO_MATERIALS]
    reasons: list[str] = []
    if any(m.kind == "own" for m in materials):
        reasons.append(_OWN_DATA)
    if any(m.kind == "unknown" for m in materials):
        reasons.append(_UNKNOWN_ORIGIN)
    # ライセンスが無い、または既知の再配布可否が付けられていない（正規化
    # できなかった綴りなど）はどちらも「ライセンスが分からない」に畳む —
    # not_redistributable は既知の NC/ND 系など、明確に False と分かる場合だけ。
    if any(m.license is None or m.redistributable is None for m in materials):
        reasons.append(_UNKNOWN_LICENSE)
    if any(m.redistributable is False for m in materials):
        reasons.append(_NOT_REDISTRIBUTABLE)
    return reasons
