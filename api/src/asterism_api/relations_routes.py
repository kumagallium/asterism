"""``/api/relations`` — 関係の目録（組み込み＋人が足した関係）。

正は ``asterism.relation_catalog``（design.md §23.13・ADR relation-catalog.md）。

* ``GET /api/relations`` — 読み取り・認証なし（秘密を含まない閉じた定義だけ）。既定は組み込み＋
  足した関係（重ねた目録）。足した関係が 0 件なら組み込みだけの目録と同じ。``?source=store`` は
  ストアの正本（R0d・``relation_store``）から読んだ同じ形の目録。``?usage=1`` で足した関係の
  使われ方（``{lines, ledger, drafts}``）を付ける。
* ``POST /api/relations``・``PATCH /api/relations/{name}``・
  ``POST /api/relations/{name}/retire``・``DELETE /api/relations/{name}`` —
  人が関係を足す・直す・使うのをやめる・消す（R3a）。
  ほかの書き込みの口と同じ ``write_auth``。組み込みの関係は 403。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from asterism import relation_catalog, relation_store
from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)


class _LangText(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ja: str | None = None
    en: str | None = None

    def as_dict(self) -> dict[str, str]:
        return {k: v for k, v in (("ja", self.ja), ("en", self.en)) if v is not None}


class RelationAddBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: _LangText
    definition: _LangText
    symmetric: bool = False
    to_property_only: bool = False
    sentence: _LangText | None = None
    exact_match: str | None = None
    unstated_key: str | None = None


class RelationPatchBody(BaseModel):
    """直せるのは表示名・文・説明・``exact_match`` だけ（名前・述語・向き・終端は変えない）。"""

    model_config = ConfigDict(extra="forbid")

    label: _LangText | None = None
    sentence: _LangText | None = None
    definition: _LangText | None = None
    exact_match: str | None = None


def register_relations(
    app: FastAPI, cfg: Any = None, *, write_auth: Sequence[Any] = ()
) -> None:
    def _client(request: Request) -> Any:
        client = getattr(request.app.state, "client", None)
        if client is None:
            raise HTTPException(503, "store is not available")
        return client

    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def _root() -> Any:
        return getattr(cfg, "registry_root", None)

    def _fail(exc: Exception) -> HTTPException:
        if isinstance(exc, relation_store.RelationInputError):
            return HTTPException(400, str(exc))
        if isinstance(exc, relation_store.RelationExists):
            return HTTPException(409, str(exc))
        if isinstance(exc, relation_store.RelationForbidden):
            return HTTPException(403, str(exc))
        if isinstance(exc, relation_store.RelationNotFound):
            return HTTPException(404, f"relation not found: {exc.args[0]}")
        logger.warning("relation write failed", exc_info=exc)
        return HTTPException(502, f"relation catalog write failed: {exc}")

    @app.get("/api/relations")
    async def list_relations(
        request: Request,
        source: str = Query("code", pattern="^(code|store)$"),
        usage: bool = Query(False),
    ) -> dict[str, Any]:
        """``{"relations": [...], "predicates": [...]}``。``source=store`` はストアから読む。"""
        if source == "code":
            out = relation_catalog.overlay_catalog_to_json()
        else:
            client = _client(request)
            try:
                out = await relation_store.read_catalog(client)
            except Exception as exc:
                raise HTTPException(502, f"relation catalog read failed: {exc}") from exc
        if usage:
            client = _client(request)
            for r in out["relations"]:
                if r.get("builtin"):
                    continue
                try:
                    r["usage"] = await relation_store.usage(
                        client, r["name"], registry_root=_root()
                    )
                except Exception as exc:
                    raise HTTPException(502, f"relation usage failed: {exc}") from exc
        return out

    @app.post("/api/relations", status_code=201, dependencies=list(write_auth))
    async def add_relation(body: RelationAddBody, request: Request) -> dict[str, Any]:
        client = _client(request)
        spec = {
            "label": body.label.as_dict(),
            "definition": body.definition.as_dict(),
            "symmetric": body.symmetric,
            "to_property_only": body.to_property_only,
            "sentence": body.sentence.as_dict() if body.sentence else None,
            "exact_match": body.exact_match,
            "unstated_key": body.unstated_key,
        }
        try:
            rel = await relation_store.add_relation(client, spec, at=_now())
            await relation_store.refresh_human(client)
        except Exception as exc:
            raise _fail(exc) from exc
        return {"relation": relation_catalog.relation_to_json(rel)}

    @app.patch("/api/relations/{name}", dependencies=list(write_auth))
    async def patch_relation(
        name: str, body: RelationPatchBody, request: Request
    ) -> dict[str, Any]:
        client = _client(request)
        fields = body.model_fields_set
        kwargs: dict[str, Any] = {}
        for key in ("label", "sentence", "definition"):
            value = getattr(body, key)
            if key in fields and value is not None:
                kwargs[key] = value.as_dict()
        if "exact_match" in fields:
            kwargs["exact_match"] = body.exact_match
        try:
            rel = await relation_store.update_relation(client, name, **kwargs)
            await relation_store.refresh_human(client)
        except Exception as exc:
            raise _fail(exc) from exc
        return {"relation": relation_catalog.relation_to_json(rel)}

    @app.post("/api/relations/{name}/retire", dependencies=list(write_auth))
    async def retire_relation(name: str, request: Request) -> dict[str, Any]:
        client = _client(request)
        try:
            rel = await relation_store.retire_relation(client, name, at=_now())
            await relation_store.refresh_human(client)
        except Exception as exc:
            raise _fail(exc) from exc
        return {"relation": relation_catalog.relation_to_json(rel)}

    @app.delete("/api/relations/{name}", dependencies=list(write_auth))
    async def delete_relation(name: str, request: Request) -> dict[str, Any]:
        client = _client(request)
        try:
            await relation_store.delete_relation(client, name, registry_root=_root())
            await relation_store.refresh_human(client)
        except relation_store.RelationInUse as exc:
            # 使われている: 画面は「使うのをやめる」を勧める
            raise HTTPException(
                409, {"detail": "relation is in use", "usage": exc.usage}
            ) from exc
        except Exception as exc:
            raise _fail(exc) from exc
        return {"deleted": name}
