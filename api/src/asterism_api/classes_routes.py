"""``register_classes(app, cfg)`` — 契約メモ contract_pr_f9.md §2.1（担当 api）。

``GET /api/classes`` — 新設。実装は :func:`asterism.classes_index.classes_index`
という 1 純関数を呼ぶだけ（他の ``register_*`` と同じ薄いルート層の流儀 —
``asterism_api.dataset_summary_routes`` 参照）。

契約メモ contract_b_hub_names.md §2: ``is_hub`` の行の ``label``（R3）と
``dataset_label``（R1）は、``classes_index`` が返す ingest 生の値
（ハブ graph の ``rdfs:label`` そのもの — 実装の語 ``(crosswalk)`` が付いたま
まのことがある）ではなく、読むたびに項目の表示名から組み立てた値へこの層で
上書きする（:mod:`asterism_api.crosswalk_names`）。``classes_index`` 自体
（ingest 側）は変えない。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from asterism import classes_index as classes_index_mod
from asterism import crosswalk_runtime
from fastapi import FastAPI

from asterism_api import crosswalk_names
from asterism_api.cards_routes import LabelResolvers, _no_op_label_resolvers, _run_read

if TYPE_CHECKING:  # pragma: no cover - type-checking only, avoids a runtime cycle
    from asterism.oxigraph_client import OxigraphClient

    from asterism_api.main import Settings

logger = logging.getLogger(__name__)

__all__ = ["register_classes"]


def _apply_hub_display_names(
    registry_root: Any, entries: list[dict[str, Any]], label_resolvers: LabelResolvers
) -> None:
    """``entries`` を in place で直す: ``is_hub`` の行だけ、その perspective の
    meta/config を 1 度だけ読んで R1（``dataset_label``）と R3（``label``）を
    上書きする（同じ perspective の複数行で読み直さない）。"""
    predicate_label_of, field_label_of = label_resolvers(registry_root)
    cache: dict[str, tuple[dict[str, Any], crosswalk_runtime.RuntimeCrosswalkConfig | None]] = {}
    for entry in entries:
        if not entry.get("is_hub"):
            continue
        perspective_id = str(entry.get("hub_perspective_id") or "")
        if not perspective_id:
            continue
        cached = cache.get(perspective_id)
        if cached is None:
            meta = crosswalk_names.load_perspective_meta(registry_root, perspective_id)
            config = crosswalk_runtime.load_config(registry_root, perspective_id)
            cached = (meta, config)
            cache[perspective_id] = cached
        meta, config = cached
        entry["dataset_label"] = crosswalk_names.perspective_display_name(
            meta, config, perspective_id, field_label_of, predicate_label_of
        )
        entry["label"] = crosswalk_names.hub_class_display_name(
            str(entry.get("class_iri") or ""), config, field_label_of, predicate_label_of
        )


def register_classes(
    app: FastAPI, cfg: Settings, *, label_resolvers: LabelResolvers | None = None
) -> None:
    """Register on ``app``: 呼び出しは他の ``register_*`` と同じく
    ``build_app`` の ``return app`` 直前 1 回だけ。

    ``label_resolvers``: :func:`asterism_api.cards_routes.register_cards` と
    同じ ``asterism_api.main._crosswalk_label_resolvers``（省略時は
    :func:`asterism_api.cards_routes._no_op_label_resolvers`）。
    """
    resolve_labels = label_resolvers or _no_op_label_resolvers

    @app.get("/api/classes")
    async def classes() -> dict[str, Any]:
        return await _run_read(_classes_impl())

    async def _classes_impl() -> dict[str, Any]:
        client: OxigraphClient = app.state.client
        entries = await classes_index_mod.classes_index(client, cfg.registry_root)
        _apply_hub_display_names(cfg.registry_root, entries, resolve_labels)
        return {"classes": entries}
