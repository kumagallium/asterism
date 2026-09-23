"""``register_class_schema(app, cfg)`` — 1 件の種類のスキーマを返す api（契約メモ
contract_pr_c.md §2。担当 C1-schema）。

``GET /api/classes/schema?class_iri=...`` は ``asterism.class_schema.class_schema``
（決定論・LLM ゼロ）をそのまま HTTP に載せるだけの薄いルート。§0.1 の型どおり、
このモジュール自身が ``@app.get`` を持ち、``main.py`` は一切書き換えない — 配線
（``build_app`` の ``return app`` 直前で ``register_class_schema(app, cfg)`` を呼ぶ
こと）は統合段の担当が行う。

読み取り専用で認証は要らない（``GET /api/prov/graph`` と同じ扱い）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from asterism.class_schema import class_schema
from fastapi import FastAPI, HTTPException, Query

from asterism_api.cards_routes import _run_read

if TYPE_CHECKING:  # pragma: no cover - type-checking only, avoids a runtime cycle
    from asterism.oxigraph_client import OxigraphClient

    from asterism_api.main import Settings


def register_class_schema(app: FastAPI, cfg: Settings) -> None:
    """Register ``GET /api/classes/schema`` on ``app``.

    Called once by the integrator, inside ``build_app``, right before
    ``return app`` (§0.1) — never imported/called anywhere else.
    """

    @app.get("/api/classes/schema")
    async def classes_schema(
        class_iri: str = Query(description="the class IRI to describe"),
    ) -> dict[str, Any]:
        return await _run_read(_classes_schema_impl(class_iri))

    async def _classes_schema_impl(class_iri: str) -> dict[str, Any]:
        client: OxigraphClient = app.state.client
        schema = await class_schema(client, cfg.registry_root, class_iri)
        if schema is None:
            raise HTTPException(404, f"unknown class_iri: {class_iri}")
        return schema
