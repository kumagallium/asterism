"""``register_handles(app, cfg)`` — S4 の ☑「他のデータとつながる手がかり」を
既存データセットに後付けする穴（契約メモ contract_pr_f15.md §1.1・§1.2。担当
api-handles）。

Every route here is wired the way the umbrella contract's §0.1 requires: this
module owns its own ``@app.get``/``@app.put`` decorators, never touches
``main.py``, and reads ``cfg.registry_root`` at request time exactly like the
routes ``main.py`` already defines. The **integration** step — calling
``register_handles(app, cfg)`` inside ``build_app`` — is left to the
integrator (api-autolink), per contract.

Deviation from the contract's literal wording (same reason every sibling
``*_routes.py`` module documents): ``main.py``'s write-auth gate
(``require_write_auth``) is a closure defined *inside* ``build_app``, not a
module-level name — it cannot be imported, and importing anything from
``main.py`` here would create a circular import (``main.py`` imports
``register_handles`` from this module). :func:`_require_write_auth` below is
the same verbatim copy every other ``*_routes.py`` module already carries
(``cards_routes._require_write_auth`` / ``place_routes._require_write_auth`` /
…): same fail-closed 503-when-unset / 401-when-wrong-token behaviour, same
messages, same constant-time comparison.
"""

from __future__ import annotations

import hmac
import json
import logging
from typing import TYPE_CHECKING

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from asterism_api import registry
from asterism_api.handles import load_handles

if TYPE_CHECKING:  # pragma: no cover - type-checking only, avoids a runtime cycle
    from asterism_api.main import Settings

logger = logging.getLogger(__name__)


class HandleItem(BaseModel):
    """1 件の ☑（``handles.json`` の ``handles[]`` と同じ形）。"""

    source: str
    column: str


class HandlesBody(BaseModel):
    handles: list[HandleItem]


def _require_write_auth(cfg: Settings):
    """``cards_routes._require_write_auth`` の verbatim コピー（同じ理由でここ
    にも複製 — モジュール docstring参照）。"""

    def _check(
        authorization: str | None = Header(default=None),
        x_asterism_token: str | None = Header(default=None),
    ) -> None:
        token = cfg.api_token
        if not token:
            logger.warning(
                "write route refused: ASTERISM_API_TOKEN is unset "
                "(fail-closed against anonymous writes / raw SPARQL)"
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

    return _check


def register_handles(app: FastAPI, cfg: Settings) -> None:
    """``GET``/``PUT /api/datasets/{dataset_id}/handles`` を ``app`` に登録する。

    ``build_app`` の ``return app`` 直前で 1 回だけ呼ばれる想定（main.py 側は
    api-autolink が配線する — このモジュール自身は import も呼び出しもしない）。
    """
    write_auth = [Depends(_require_write_auth(cfg))]

    @app.get("/api/datasets/{dataset_id}/handles")
    async def get_handles(dataset_id: str) -> dict:
        record = registry.load_dataset(cfg.registry_root, dataset_id)
        if record is None:
            raise HTTPException(404, "unknown dataset_id")
        return {"handles": load_handles(record["artifacts"])}

    @app.put("/api/datasets/{dataset_id}/handles", dependencies=write_auth)
    async def put_handles(dataset_id: str, body: HandlesBody) -> dict:
        record = registry.load_dataset(cfg.registry_root, dataset_id)
        if record is None:
            raise HTTPException(404, "unknown dataset_id")
        handles = [{"source": h.source, "column": h.column} for h in body.handles]
        payload = {"version": 1, "handles": handles}
        dest = cfg.registry_root / dataset_id / "handles.json"
        dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"handles": handles}
