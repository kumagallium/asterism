"""``register_sample_routes(app, cfg)`` — 見本の「新しい見本に置き換える」と「控えから戻す」
（ADR kantan K62・画面の知らせ）。

* ``POST /api/datasets/{id}/sample/refresh`` — 利用者が変えたので新しくしていない単位を、
  控えを取ってから新しい見本に置き換える。
* ``POST /api/datasets/{id}/sample/restore`` — その控えから戻す。

どちらも利用者の変更を捨てうる操作なので、次を全部課す:

* 単一ユーザーのときだけ（それ以外は 404）。書き込み認証（``cards_routes`` と同じ流儀）。
* 本文は JSON 必須（``Content-Type: application/json`` をハンドラの中で確かめる）で、
  さらに ``X-Asterism-Intent`` の見出しを必須にする — ローカル版は 127.0.0.1 に届く要求
  すべてに書き込み権限が付くので、ほかのページからの単純な POST（本文なし・text/plain）を
  通さないため。
* 画面が見た ``seq``・``revision`` が今と違えば 409 ``stale``。
* データセットごとの非同期ロックを全区間で持つ（取れなければ 409 ``busy``）。
* 失敗は ``detail.error`` の固定コードで返す。画面はメッセージを出さず、コードから
  固定の文にする。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from fastapi import Depends, FastAPI, HTTPException, Request

from asterism_api import demo_sample
from asterism_api.cards_routes import _require_write_auth

if TYPE_CHECKING:  # pragma: no cover - type-checking only, avoids a runtime cycle
    from asterism.oxigraph_client import OxigraphClient

    from asterism_api.main import Settings

logger = logging.getLogger(__name__)

__all__ = ["INTENT_HEADER", "INTENT_REFRESH", "INTENT_RESTORE", "register_sample_routes"]

INTENT_HEADER = "X-Asterism-Intent"
INTENT_REFRESH = "sample-refresh"
INTENT_RESTORE = "sample-restore"
_MAX_BODY = 16 * 1024


def _fail(status: int, code: str) -> HTTPException:
    return HTTPException(status, detail={"error": code})


async def _json_body(request: Request, intent: str) -> dict[str, Any]:
    """Content-Type・見出し・本文の形を確かめる（どれかが欠ければ拒む）。"""
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != "application/json":
        raise _fail(415, "json_required")
    if request.headers.get(INTENT_HEADER) != intent:
        raise _fail(403, "intent_required")
    raw = await request.body()
    if len(raw) > _MAX_BODY:
        raise _fail(413, "too_large")
    try:
        body = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise _fail(400, "bad_request") from exc
    if not isinstance(body, dict):
        raise _fail(400, "bad_request")
    return body


def register_sample_routes(app: FastAPI, cfg: Settings) -> None:
    """Register on ``app``: 呼び出しは他の ``register_*`` と同じく ``build_app`` の
    ``return app`` 直前 1 回だけ。"""

    locks: dict[str, asyncio.Lock] = {}
    app.state.sample_locks = locks

    def _single_user_only() -> None:
        if not cfg.single_user:
            raise _fail(404, "not_available")

    gate = [Depends(_single_user_only), Depends(_require_write_auth(cfg))]

    def _snapshot():
        from asterism_api import local  # 遅延 import（local は main を遅延 import する）

        return local.find_world_snapshot()

    async def _locked(dataset_id: str, work):
        lock = locks.setdefault(dataset_id, asyncio.Lock())
        if lock.locked():
            raise _fail(409, "busy")
        async with lock:
            try:
                return await work()
            except demo_sample.SampleOpError as exc:
                raise _fail(exc.status, exc.code) from exc

    @app.post("/api/datasets/{dataset_id}/sample/refresh", dependencies=gate)
    async def sample_refresh(dataset_id: str, request: Request) -> dict[str, Any]:
        body = await _json_body(request, INTENT_REFRESH)
        seq, revision, units = body.get("seq"), body.get("revision"), body.get("units")
        if (
            not isinstance(seq, int)
            or isinstance(seq, bool)
            or not isinstance(revision, str)
            or not isinstance(units, list)
            or not all(isinstance(u, str) for u in units)
        ):
            raise _fail(400, "bad_request")
        client: OxigraphClient = app.state.client

        async def work() -> dict[str, Any]:
            outcome = await demo_sample.manual_override(
                cfg, client, _snapshot(), dataset_id, seq=seq, revision=revision, units=units
            )
            return {"ok": True, **outcome}

        return await _locked(dataset_id, work)

    @app.post("/api/datasets/{dataset_id}/sample/restore", dependencies=gate)
    async def sample_restore(dataset_id: str, request: Request) -> dict[str, Any]:
        body = await _json_body(request, INTENT_RESTORE)
        at = body.get("at")
        if not isinstance(at, str) or not at:
            raise _fail(400, "bad_request")
        client: OxigraphClient = app.state.client

        async def work() -> dict[str, Any]:
            outcome = await demo_sample.manual_restore(cfg, client, _snapshot(), dataset_id, at=at)
            return {"ok": True, **outcome}

        return await _locked(dataset_id, work)
