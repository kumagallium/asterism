"""``GET /api/relations`` — 関係の目録（組み込み）を返す（読み取りのみ・認証なし）。

正は ``asterism.relation_catalog``（design.md §23.13・ADR relation-catalog.md）。
``GET /api/crosswalk/alignments`` と同じく、秘密を含まない閉じた定義だけを返すので認証は付けない。
並びは目録の並び。関係を足す口は無い。``?source=store`` でストアの正本（R0d・
``relation_store``）から読んだ同じ形の目録を返す（既定はコードの目録のまま）。
"""

from __future__ import annotations

from typing import Any

from asterism import relation_catalog, relation_store
from fastapi import FastAPI, HTTPException, Query, Request


def register_relations(app: FastAPI) -> None:
    @app.get("/api/relations")
    async def list_relations(
        request: Request, source: str = Query("code", pattern="^(code|store)$")
    ) -> dict[str, Any]:
        """``{"relations": [...12], "predicates": [...15]}``。``source=store`` はストアから読む。"""
        if source == "code":
            return relation_catalog.catalog_to_json()
        client = getattr(request.app.state, "client", None)
        if client is None:
            raise HTTPException(503, "store is not available")
        try:
            return await relation_store.read_catalog(client)
        except Exception as exc:
            raise HTTPException(502, f"relation catalog read failed: {exc}") from exc
