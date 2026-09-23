"""Tests for asterism.materials (契約メモ §2: 材料表・「配れる」判定)。

固定テスト（引き継ぎ書 Step 6 の達成条件）: own 混入→False（own_data）／
license None→False（unknown_license）／NC→False（not_redistributable）／
origin unknown→False（unknown_origin）／全部 open＋許可→True／空→False。

``materials_for`` は ``asterism_api.registry.dataset_origin`` /
``asterism.licenses.dataset_license``（どちらも D1-license が並行実装中）を
遅延 import する。その呼び出し口を ``_lookup_origin``/``_lookup_license`` の
monkeypatch で差し替え、この PR だけで完結させる（§0: 分野固有の名詞なし・
LLM／生成コードなし）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import asterism.materials as materials_mod
from asterism.materials import Material, materials_for, shareable, shareable_reasons


def _mat(
    kind: str,
    dataset_id: str = "dataset-a",
    *,
    dataset_label: str | None = None,
    snapshot: str | None = "v1",
    license: str | None = None,
    redistributable: bool | None = None,
    count: int = 1,
) -> Material:
    return Material(
        kind=kind,
        dataset_id=dataset_id,
        dataset_label=dataset_label or dataset_id,
        snapshot=snapshot,
        license=license,
        redistributable=redistributable,
        count=count,
    )


# ---------------------------------------------------------------------------
# shareable / shareable_reasons — 保守側判定（固定テスト）。
# ---------------------------------------------------------------------------


def test_shareable_false_when_own_data_is_mixed_in() -> None:
    materials = [
        _mat("open", "dataset-a", license="CC-BY-4.0", redistributable=True),
        # own のデータセットにも許可されたライセンスが付いていて、own_data
        # だけが理由に立つケース（ライセンス不明との重複を避けて分離して見る）。
        _mat("own", "dataset-b", license="CC-BY-4.0", redistributable=True),
    ]
    assert shareable(materials) is False
    assert shareable_reasons(materials) == ["own_data"]


def test_shareable_false_when_license_is_unknown() -> None:
    materials = [_mat("open", license=None, redistributable=None)]
    assert shareable(materials) is False
    assert shareable_reasons(materials) == ["unknown_license"]


def test_shareable_false_for_non_commercial_license() -> None:
    materials = [_mat("open", license="CC-BY-NC-4.0", redistributable=False)]
    assert shareable(materials) is False
    assert shareable_reasons(materials) == ["not_redistributable"]


def test_shareable_false_when_origin_is_unknown() -> None:
    materials = [_mat("unknown", license="CC-BY-4.0", redistributable=True)]
    assert shareable(materials) is False
    assert shareable_reasons(materials) == ["unknown_origin"]


def test_shareable_true_when_all_open_and_permitted() -> None:
    materials = [
        _mat("open", "dataset-a", license="CC-BY-4.0", redistributable=True),
        _mat("open", "dataset-b", license="CC0-1.0", redistributable=True),
    ]
    assert shareable(materials) is True
    assert shareable_reasons(materials) == []


def test_shareable_false_when_no_materials() -> None:
    assert shareable([]) is False
    assert shareable_reasons([]) == ["no_materials"]


def test_shareable_reasons_order_is_fixed_and_deduplicated() -> None:
    # own + unknown 出どころ + ライセンス不明 + NC が全部同時に混ざっても、
    # 種別ごと 1 回だけ、固定順で返る。
    materials = [
        _mat("own", "dataset-a"),
        _mat("unknown", "dataset-b", license=None),
        _mat("open", "dataset-c", license="CC-BY-NC-4.0", redistributable=False),
    ]
    assert shareable_reasons(materials) == [
        "own_data",
        "unknown_origin",
        "unknown_license",
        "not_redistributable",
    ]


# ---------------------------------------------------------------------------
# materials_for — dataset_id/snapshot/count を Material に仕立てる。
# ---------------------------------------------------------------------------


def test_materials_for_uses_looked_up_origin_and_license(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("asterism.materials._lookup_origin", lambda root, dataset_id: "open")
    monkeypatch.setattr(
        "asterism.materials._lookup_license",
        lambda root, dataset_id: ("CC-BY-4.0", True),
    )
    (tmp_path / "dataset-a").mkdir()
    (tmp_path / "dataset-a" / "meta.json").write_text(
        json.dumps({"id": "dataset-a", "name": "Sample Dataset"}), encoding="utf-8"
    )
    materials = materials_for(tmp_path, [("dataset-a", "v2", 5)])
    assert materials == [
        Material(
            kind="open",
            dataset_id="dataset-a",
            dataset_label="Sample Dataset",
            snapshot="v2",
            license="CC-BY-4.0",
            redistributable=True,
            count=5,
        )
    ]
    assert shareable(materials) is True


def test_materials_for_defaults_to_unknown_when_lookups_are_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # D1-license 未着地（ImportError）のときと同じ既定値 — 保守側。
    monkeypatch.setattr("asterism.materials._lookup_origin", lambda root, dataset_id: None)
    monkeypatch.setattr("asterism.materials._lookup_license", lambda root, dataset_id: None)
    materials = materials_for(tmp_path, [("dataset-b", None, 3)])
    assert materials == [
        Material(
            kind="unknown",
            dataset_id="dataset-b",
            dataset_label="dataset-b",  # meta.json が無い → dataset_id そのまま
            snapshot=None,
            license=None,
            redistributable=None,
            count=3,
        )
    ]
    assert shareable(materials) is False


def test_materials_for_rejects_an_unrecognized_origin_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # dataset_origin の契約は 'own'|'open'|'unknown' のみ。それ以外を返す
    # 実装があっても「不明」に倒す（保守側 — 未知の語彙を信用しない）。
    monkeypatch.setattr("asterism.materials._lookup_origin", lambda root, dataset_id: "public")
    monkeypatch.setattr("asterism.materials._lookup_license", lambda root, dataset_id: None)
    materials = materials_for(tmp_path, [("dataset-c", "v1", 1)])
    assert materials[0].kind == "unknown"


def test_materials_for_empty_contributions_is_empty_list(tmp_path: Path) -> None:
    assert materials_for(tmp_path, []) == []


def test_lookup_origin_degrades_to_none_when_the_backing_call_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # D1-license の実装が例外を投げても materials_for 全体を落とさない
    # （_lookup_origin/_lookup_license 自身が best-effort でにぎりつぶす）。
    def _boom(root: Path, dataset_id: str) -> str:
        raise RuntimeError("boom")

    fake_registry = type("_FakeRegistry", (), {"dataset_origin": staticmethod(_boom)})
    monkeypatch.setitem(sys.modules, "asterism_api.registry", fake_registry)
    assert materials_mod._lookup_origin(tmp_path, "dataset-d") is None


def test_lookup_license_degrades_to_none_when_the_backing_call_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(root: Path, dataset_id: str) -> tuple[str | None, bool | None]:
        raise RuntimeError("boom")

    fake_licenses = type("_FakeLicenses", (), {"dataset_license": staticmethod(_boom)})
    monkeypatch.setitem(sys.modules, "asterism.licenses", fake_licenses)
    assert materials_mod._lookup_license(tmp_path, "dataset-d") is None
