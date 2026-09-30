"""``backfill_store_projections`` — 公開済みなのにストアに名前の graph／説明の
graph が無いデータセットを起動時に補う。

投影・graph 一覧は monkeypatch する（ストアには繋がない）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from asterism import substrate

from asterism_api import local
from asterism_api import main as main_mod
from asterism_api.main import Settings


def _write(root: Path, dataset_id: str, meta: dict[str, Any]) -> None:
    d = root / dataset_id
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"id": dataset_id, **meta}), encoding="utf-8")


def _setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Settings, list[tuple[str, str]]]:
    root = tmp_path / "registry"
    _write(root, "has-names", {"promoted": True})
    _write(root, "no-names", {"promoted": True})
    _write(root, "draft", {"promoted": False})
    _write(
        root,
        "hub",
        {"promoted": True, "canonical_graph": substrate.CANONICAL_GRAPH_BASE + "crosswalk"},
    )
    calls: list[tuple[str, str]] = []

    async def onto(client: Any, dataset_id: str, artifacts: dict[str, str]) -> int:
        calls.append(("onto", dataset_id))
        return 1

    async def meta(client: Any, dataset_id: str, artifacts: dict[str, str]) -> int:
        calls.append(("meta", dataset_id))
        return 1

    async def onto_graphs(client: Any) -> list[str]:
        return [substrate.ontology_graph_iri("has-names")]

    async def meta_graphs(client: Any) -> list[str]:
        return [substrate.meta_graph_iri("has-names")]

    monkeypatch.setattr(main_mod, "_project_ontology_graph", onto)
    monkeypatch.setattr(main_mod, "_project_meta_graph", meta)
    monkeypatch.setattr(substrate, "ontology_graphs", onto_graphs)
    monkeypatch.setattr(substrate, "meta_graphs", meta_graphs)
    monkeypatch.setattr(
        local.registry,
        "load_dataset",
        lambda r, i: {"meta": {}, "artifacts": {"mapping.yaml": "x"}},
    )
    return Settings({"CSV2RDF_REGISTRY_ROOT": str(root)}), calls


def test_only_the_missing_published_dataset_is_projected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, calls = _setup(tmp_path, monkeypatch)
    asyncio.run(local.backfill_store_projections(cfg, object()))
    assert calls == [("onto", "no-names"), ("meta", "no-names")]


def test_only_meta_projected_when_only_meta_graph_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, calls = _setup(tmp_path, monkeypatch)

    async def onto_graphs(client: Any) -> list[str]:
        return [substrate.ontology_graph_iri(i) for i in ("has-names", "no-names")]

    monkeypatch.setattr(substrate, "ontology_graphs", onto_graphs)
    asyncio.run(local.backfill_store_projections(cfg, object()))
    assert calls == [("meta", "no-names")]


def test_one_failure_does_not_stop_the_next(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, calls = _setup(tmp_path, monkeypatch)
    _write(tmp_path / "registry", "zzz-later", {"promoted": True})

    async def onto(client: Any, dataset_id: str, artifacts: dict[str, str]) -> int:
        calls.append(("onto", dataset_id))
        if dataset_id == "no-names":
            raise RuntimeError("boom")
        return 1

    monkeypatch.setattr(main_mod, "_project_ontology_graph", onto)
    asyncio.run(local.backfill_store_projections(cfg, object()))
    assert ("onto", "zzz-later") in calls
    assert ("meta", "zzz-later") in calls


def test_listing_failure_does_not_raise(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, calls = _setup(tmp_path, monkeypatch)

    async def broken(client: Any) -> list[str]:
        raise RuntimeError("store down")

    monkeypatch.setattr(substrate, "ontology_graphs", broken)
    asyncio.run(local.backfill_store_projections(cfg, object()))
    assert calls == []


def _history(root: Path, dataset_id: str, stamp: str) -> None:
    (root / dataset_id / "history" / stamp).mkdir(parents=True)


def test_redesigned_after_publishing_is_not_projected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """公開より後に設計を保存し直した（いまの成果物は未公開の下書き）データセットは
    補わない。公開より前の控えしか無いデータセットは補う。"""
    cfg, calls = _setup(tmp_path, monkeypatch)
    root = cfg.registry_root
    _write(root, "redesigned", {"promoted": True, "promoted_at": "2026-09-01T00:00:00+00:00"})
    _history(root, "redesigned", "20260902T000000Z")
    _write(root, "older-history", {"promoted": True, "promoted_at": "2026-09-01T00:00:00+00:00"})
    _history(root, "older-history", "20260831T235959Z")
    asyncio.run(local.backfill_store_projections(cfg, object()))
    projected = {dataset_id for _, dataset_id in calls}
    assert "redesigned" not in projected
    assert "older-history" in projected


@pytest.mark.parametrize(
    ("meta", "stamps", "expected"),
    [
        ({"promoted_at": "2026-09-01T00:00:00+00:00"}, [], False),
        ({"promoted_at": "2026-09-01T00:00:00+00:00"}, ["20260831T000000Z"], False),
        ({"promoted_at": "2026-09-01T00:00:00+00:00"}, ["20260901T000000Z"], True),
        ({"promoted_at": "2026-09-01T00:00:00.500000+00:00"}, ["20260901T000000Z-2"], True),
        ({}, ["20260831T000000Z"], True),
        ({"promoted_at": "2026-09-01T00:00:00+00:00"}, ["not-a-stamp"], True),
    ],
)
def test_redesigned_after_promote_reads_history_stamps(
    tmp_path: Path, meta: dict[str, Any], stamps: list[str], expected: bool
) -> None:
    d = tmp_path / "ds"
    d.mkdir()
    for stamp in stamps:
        (d / "history" / stamp).mkdir(parents=True)
    assert local._redesigned_after_promote(d, meta) is expected
