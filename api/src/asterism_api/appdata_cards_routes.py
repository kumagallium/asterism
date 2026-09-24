"""``register_appdata_cards(app, cfg)`` — appdata の namespace ``cards`` 用の
3 ルート（契約メモ contract_pr_f4.md §1-5・§2 api 行）。

``cards_routes.py`` の ``/api/appdata/subjects`` 3 本と同じ書き方・同じ
``write_auth``（§0.1: このモジュールも own の ``@app.get``/``@app.put``/
``@app.delete`` を own で登録し、``main.py`` には呼び出し 1 行だけを残す。
``main.py``'s ``require_write_auth`` はビルド中の closure で直接
import できないので、``cards_routes._require_write_auth``/
``export_routes._require_write_auth`` と同じ verbatim コピーを ここにも
持つ — 理由は ``cards_routes.py`` のモジュール docstring 参照）。

保存する 1 件は O19 の ``CardSpec``
``{ card_id, subject_key, tool, params, title, output_kind, created_at }``
——「足したグラフ」1 枚（新規追加。既定カードは appdata に書かない）。
``card_id`` は params の決定論ハッシュ（**ui-form 側が計算**する値 —
``measureCardFields.ts`` の ``cardId()``。``"card-" + sha256(canonical
params) の先頭 N 桁の16進``。この api は形（``asterism_api.appdata.
valid_thread_id`` が uuid4 と並んで受け付けるようにした ``card-`` +
小文字16進 8〜64 桁の形）だけを検証する）。

``/api/appdata/subjects`` と同じく単一ユーザー（``cfg.single_user`` かつ
``cfg.appdata_root`` あり）のときだけ有効 — それ以外は 404（hosted は
localStorage フォールバックへ）。1 件 1 MiB・最大 1000 件も
``asterism_api.appdata`` の既存の上限をそのまま使う。
"""

from __future__ import annotations

import hmac
import logging
from typing import TYPE_CHECKING, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request

from asterism_api import appdata

if TYPE_CHECKING:  # pragma: no cover - type-checking only, avoids a runtime cycle
    from asterism_api.main import Settings

logger = logging.getLogger(__name__)

#: appdata.read_threads/write_thread/delete_thread の namespace。
#: ``export_routes.py`` が「その主語のカード」を絞り込むときも同じ名前を使う
#: （契約メモ §1-6: 束への凍結）。
CARDS_NAMESPACE = "cards"


def _require_write_auth(cfg: Settings):
    """``cards_routes._require_write_auth`` の verbatim コピー（同じ理由で
    ``main.py`` の closure を直接 import できないため — 詳細はそちらの
    モジュール docstring 参照）。"""

    def _dep(
        authorization: str | None = Header(default=None),
        x_asterism_token: str | None = Header(default=None),
    ) -> None:
        token = cfg.api_token
        if not token:
            logger.warning(
                "write route refused: ASTERISM_API_TOKEN is unset "
                "(fail-closed against anonymous writes)"
            )
            raise HTTPException(
                503,
                "利用許可コード (管理者が設定する API token) が未設定のため、この操作はできません",
            )
        presented: str | None = None
        if authorization and authorization.startswith("Bearer "):
            presented = authorization[len("Bearer ") :].strip()
        elif x_asterism_token:
            presented = x_asterism_token.strip()
        if not (presented and hmac.compare_digest(presented, token)):
            raise HTTPException(401, "利用許可コードが違います")

    return _dep


def _reject_if_content_length_exceeds(request: Request, limit: int) -> None:
    """``cards_routes``/``export_routes``/``main.py``の appdata 事前チェックと
    同じ: 宣言された ``Content-Length`` が上限を超えていたら、本文を読む前に
    早期に断る。"""
    raw = request.headers.get("content-length")
    if raw is None:
        return
    try:
        declared = int(raw)
    except ValueError:
        return
    if declared > limit:
        raise HTTPException(413, f"body is {declared} bytes, over the {limit} limit")


def register_appdata_cards(app: FastAPI, cfg: Settings) -> None:
    """契約メモ contract_pr_f4.md §1-5 の 3 ルートを登録する。

    ``main.py`` が ``build_app`` の中で 1 回だけ呼ぶ（§0.1・
    ``cards_routes.register_cards`` と同じ規律 — 呼び出しのほかは
    ``main.py`` を一切変更しない）。
    """

    write_auth = [Depends(_require_write_auth(cfg))]

    def _appdata_root_or_404():
        if not cfg.single_user or cfg.appdata_root is None:
            raise HTTPException(404, "appdata is only available in single-user mode")
        return cfg.appdata_root

    @app.get("/api/appdata/cards")
    async def appdata_list_cards() -> dict[str, Any]:
        root = _appdata_root_or_404()
        return {"cards": appdata.read_threads(root, namespace=CARDS_NAMESPACE)}

    @app.put("/api/appdata/cards/{card_id}", dependencies=write_auth)
    async def appdata_put_card(card_id: str, request: Request) -> dict[str, Any]:
        root = _appdata_root_or_404()
        if not appdata.valid_thread_id(card_id):
            raise HTTPException(400, f"invalid card id {card_id!r}")
        _reject_if_content_length_exceeds(request, appdata.MAX_THREAD_BYTES)
        try:
            payload = await request.json()
        except ValueError as exc:
            raise HTTPException(400, "body must be JSON") from exc
        if not isinstance(payload, dict):
            raise HTTPException(400, "card body must be a JSON object")
        try:
            appdata.write_thread(root, card_id, payload, namespace=CARDS_NAMESPACE)
        except appdata.InvalidThreadId as exc:
            raise HTTPException(400, str(exc)) from exc
        except (appdata.ThreadTooLarge, appdata.TooManyThreads) as exc:
            raise HTTPException(413, str(exc)) from exc
        return {"saved": True}

    @app.delete("/api/appdata/cards/{card_id}", dependencies=write_auth)
    async def appdata_delete_card(card_id: str) -> dict[str, Any]:
        root = _appdata_root_or_404()
        if not appdata.valid_thread_id(card_id):
            raise HTTPException(400, f"invalid card id {card_id!r}")
        try:
            deleted = appdata.delete_thread(root, card_id, namespace=CARDS_NAMESPACE)
        except appdata.InvalidThreadId as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"deleted": deleted}
