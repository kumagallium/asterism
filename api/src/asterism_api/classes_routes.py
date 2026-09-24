"""``register_classes(app, cfg)`` — 契約メモ contract_pr_f9.md §2.1（担当 api）。

``GET /api/classes`` — 新設。実装は :func:`asterism.classes_index.classes_index`
という 1 純関数を呼ぶだけ（他の ``register_*`` と同じ薄いルート層の流儀 —
``asterism_api.dataset_summary_routes`` 参照）。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from asterism import classes_index as classes_index_mod
from fastapi import FastAPI

from asterism_api.cards_routes import _run_read

if TYPE_CHECKING:  # pragma: no cover - type-checking only, avoids a runtime cycle
    from asterism.oxigraph_client import OxigraphClient

    from asterism_api.main import Settings

logger = logging.getLogger(__name__)

__all__ = ["register_classes"]


def register_classes(app: FastAPI, cfg: Settings) -> None:
    """Register on ``app``: 呼び出しは他の ``register_*`` と同じく
    ``build_app`` の ``return app`` 直前 1 回だけ。"""

    @app.get("/api/classes")
    async def classes() -> dict[str, Any]:
        return await _run_read(_classes_impl())

    async def _classes_impl() -> dict[str, Any]:
        client: OxigraphClient = app.state.client
        entries = await classes_index_mod.classes_index(client, cfg.registry_root)
        return {"classes": entries}
