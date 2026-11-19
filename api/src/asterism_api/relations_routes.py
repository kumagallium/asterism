"""``GET /api/relations`` — 関係の目録（組み込み）を返す（読み取りのみ・認証なし）。

正は ``asterism.relation_catalog``（design.md §23.13・ADR relation-catalog.md）。
``GET /api/crosswalk/alignments`` と同じく、秘密を含まない閉じた定義だけを返すので認証は付けない。
並びは目録の並び。関係を足す口は無い（R0d で別に設計する）。
"""

from __future__ import annotations

from typing import Any

from asterism import relation_catalog
from fastapi import FastAPI


def register_relations(app: FastAPI) -> None:
    @app.get("/api/relations")
    async def list_relations() -> dict[str, Any]:
        """``{"relations": [...12], "predicates": [...15]}``。"""
        return relation_catalog.catalog_to_json()
