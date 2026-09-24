"""``register_dataset_summary(app, cfg)`` — 契約メモ contract_pr_f2.md §3.1。
担当 api。

``GET /api/datasets/{dataset_id}/summary`` — 新設。実装は
:mod:`asterism.dataset_summary` の 1 純関数を呼ぶだけ。

``GET /api/datasets`` の一覧（既存フィールドに ``origin``/``stage``/``is_demo``
を足す・契約メモ §3.4）は ``main.py`` の ``list_datasets`` 自身が
:func:`asterism.dataset_summary.list_entry_extras` を呼んで直接持つ（統合時の
所見 #2: 起動時にルートを外して差し替えるハックはやめた）。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from asterism import dataset_summary as dataset_summary_mod
from fastapi import FastAPI, HTTPException

from asterism_api.cards_routes import _run_read

if TYPE_CHECKING:  # pragma: no cover - type-checking only, avoids a runtime cycle
    from asterism.oxigraph_client import OxigraphClient

    from asterism_api.main import Settings

logger = logging.getLogger(__name__)

__all__ = ["register_dataset_summary"]


def register_dataset_summary(app: FastAPI, cfg: Settings) -> None:
    """Register on ``app``: 呼び出しは他の ``register_*`` と同じく
    ``build_app`` の ``return app`` 直前 1 回だけ。"""

    @app.get("/api/datasets/{dataset_id}/summary")
    async def dataset_summary(dataset_id: str) -> dict[str, Any]:
        return await _run_read(_dataset_summary_impl(dataset_id))

    async def _dataset_summary_impl(dataset_id: str) -> dict[str, Any]:
        client: OxigraphClient = app.state.client
        result = await dataset_summary_mod.dataset_summary(client, cfg.registry_root, dataset_id)
        if result is None:
            raise HTTPException(404, f"dataset {dataset_id!r} not found")
        return result
