"""台帳の関係を人が確認する口（PR 14e-1・契約書 §1.2）。

* ``POST /api/knowledge-copies/{source}/relations/review`` — 確認・確認を取り消す・「違う」・戻す
  （1 回に 1〜50 件の鍵。「全部」の指定は無い）。
* ``POST /api/knowledge-copies/{source}/relations/assign`` — 候補に、人が関係を付ける。

台帳の書き換えは :mod:`asterism.pair_relations` の純関数（決定論）。ここは、同じ取り込み元の
作業（写す・関係を選ぶ・取り込み・外す）と排他にして（``busy_kind`` の印 ``review`` を短く握る）、
台帳を読む → 書き換える → ``finalize`` して保存する。AI は呼ばない。MCP には出さない。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from asterism import knowledge_bundle as kb
from asterism import pair_relations as pr
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from asterism_api import registry
from asterism_api.knowledge_copy import copy_is_current, find_copy_dataset
from asterism_api.knowledge_import import _hold, _release

logger = logging.getLogger(__name__)

__all__ = ["MAX_KEYS", "register_knowledge_review_routes"]

#: 1 回に送れる鍵の数（「全部」の指定は無い）。
MAX_KEYS = 50
_KIND = "review"

#: 人が関係を付けるのを断った理由（人が読める文）。
_REASONS: dict[str, dict[str, str]] = {
    "ja": {
        "unknown": "この組は台帳にありません",
        "already_related": "この組にはもう関係が付いています。先に「違う」にしてください",
        "relation": "この関係は選べません",
        "direction": "主語を選んでください",
        "term": "語が写しにありません",
        "same": "同じ語どうしは結べません",
        "mixed": "この関係は、語の種類が合いません",
        "target_not_property": "この関係の相手は項目 (物性) でなければなりません",
        "role_mismatch": "この関係は、語の役割が合いません",
        "cycle": "「一種」「一部」の輪になるので付けられません",
        "rejected": "同じ関係を「違う」としています。先に戻してください",
    },
    "en": {
        "unknown": "This pair is not in the ledger",
        "already_related": "This pair already has a relation. Mark it as wrong first",
        "relation": "This relation cannot be chosen",
        "direction": "Choose the subject",
        "term": "A term is missing from the copy",
        "same": "A term cannot be related to itself",
        "mixed": "The kinds of the two terms do not fit this relation",
        "target_not_property": "The target of this relation must be a property",
        "role_mismatch": "The roles of the two terms do not fit this relation",
        "cycle": "This would make a loop of 'kind of' or 'part of'",
        "rejected": "You marked this same relation as wrong. Restore it first",
    },
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["confirm", "unconfirm", "reject", "restore"]
    keys: list[str] = Field(min_length=1, max_length=MAX_KEYS)


class AssignBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1)
    rel: str
    subject: Literal["a", "b"]
    lang: Literal["ja", "en"] = "ja"


def register_knowledge_review_routes(
    app: FastAPI, cfg: Any, *, write_auth: Sequence[Any] = ()
) -> None:
    """確認と関係付けの口を登録する（``register_knowledge_relations_routes`` の後）。"""
    deps = list(write_auth)
    root: Path = cfg.registry_root

    def _current(source: str) -> tuple[str, Path]:
        """写しのデータセット id と source の置き場。無ければ 404・作り直しの途中なら 409。"""
        dataset_id = find_copy_dataset(root, source)
        if dataset_id is None:
            raise HTTPException(404, f"{source} の写しがありません")
        meta = registry.load_meta(root, dataset_id) or {}
        if not copy_is_current(meta):
            raise HTTPException(409, f"{source} の写しは作り直している途中です")
        src = registry.source_dir(root, dataset_id)
        if src is None:
            raise HTTPException(404, f"{source} の写しがありません")
        return dataset_id, src

    async def _apply(
        source: str, op: Callable[[dict, kb.KnowledgeCopy], tuple[dict, dict]]
    ) -> tuple[dict, dict]:
        """握る → 台帳と写しを読む → ``op`` → ``finalize`` → 保存。返りは ``(結果, 台帳)``。"""
        dataset_id, src = _current(source)
        _hold(app, source, _KIND)  # 走っている作業があれば 409
        try:
            try:
                copy = await asyncio.to_thread(kb.read_copy, src)
            except kb.BundleError as exc:
                raise HTTPException(409, f"写しが読めません: {exc}") from exc
            try:
                ledger = await asyncio.to_thread(pr.load_ledger, root, source)
            except pr.LedgerError as exc:
                raise HTTPException(409, "台帳が読めません") from exc
            new, result = op(ledger, copy)
            if result["changed"]:
                final = pr.finalize(new, copy, dataset_id=ledger.get("dataset_id") or dataset_id)
                try:
                    await asyncio.to_thread(pr.save_ledger, root, final, at=_now())
                except OSError as exc:
                    logger.warning("knowledge review: could not save the ledger", exc_info=True)
                    raise HTTPException(500, "台帳を更新できませんでした") from exc
                new = final
            return result, new
        finally:
            _release(app, source, _KIND)

    @app.post("/api/knowledge-copies/{source}/relations/review", dependencies=deps)
    async def knowledge_relations_review(source: str, body: ReviewBody) -> dict[str, Any]:
        """確認・確認を取り消す・「違う」・戻す。本文 ``{action, keys}``（1〜50 件）。
        返りは ``{changed, skipped, summary}``（鍵が合わない・状態が合わない件は ``skipped``）。"""
        keys = list(dict.fromkeys(body.keys))
        at = _now()

        def op(ledger: dict, _copy: kb.KnowledgeCopy) -> tuple[dict, dict]:
            if body.action == "confirm":
                return pr.confirm(ledger, keys, at=at)
            if body.action == "unconfirm":
                return pr.unconfirm(ledger, keys)
            if body.action == "reject":
                return pr.reject(ledger, keys, at=at)
            return pr.restore(ledger, keys)

        result, ledger = await _apply(source, op)
        return {**result, "summary": pr.summarize(ledger)}

    @app.post("/api/knowledge-copies/{source}/relations/assign", dependencies=deps)
    async def knowledge_relations_assign(source: str, body: AssignBody) -> Any:
        """候補に人が関係を付ける。本文 ``{key, rel, subject, lang?}``。検査に落ちたら 422 と理由
        （``{detail: 人が読める文, reason: 記号}``）。組が台帳に無ければ 404。"""
        at = _now()

        def op(ledger: dict, copy: kb.KnowledgeCopy) -> tuple[dict, dict]:
            return pr.assign(ledger, copy.terms, body.key, body.rel, body.subject, at=at)

        result, ledger = await _apply(source, op)
        if result["skipped"]:
            reason = result["skipped"][0]["reason"]
            if reason == "unknown":
                raise HTTPException(404, _REASONS[body.lang]["unknown"])
            msgs = _REASONS[body.lang]
            return JSONResponse(
                status_code=422, content={"detail": msgs.get(reason, reason), "reason": reason}
            )
        return {**result, "summary": pr.summarize(ledger)}
