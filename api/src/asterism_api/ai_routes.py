"""``register_ai(app, cfg, resolve_llm, write_auth=…)`` — AI の適性検査の api（PR 11q）。

* ``POST /api/ai/aptitude`` — 検査をジョブで走らせる → ``{job_id}``。進みはジョブの events の
  ``{"n": k, "total": 40}``、結果は done の ``result``。LLM はヘッダ（``x-llm-*``・鍵）から作る。
* ``GET /api/ai/aptitude`` — 保存した結果の一覧（``settings.json`` の ``aptitude``）。

鍵は保存しない。結果は ``appdata`` の settings に ``"<provider>::<model>"`` で入れる。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from asterism_api import ai_aptitude, appdata
from asterism_api.chat_routes import _ai_unset
from asterism_api.converse_routes import _llm_coords, _llm_max_tokens

if TYPE_CHECKING:
    from asterism_api.main import Settings

logger = logging.getLogger(__name__)


def register_ai(
    app: FastAPI,
    cfg: Settings,
    resolve_llm: Any,
    *,
    write_auth: Sequence[Any] = (),
) -> None:
    deps = list(write_auth)

    def _save(key: str, entry: dict) -> None:
        """settings.json の ``aptitude`` に 1 件入れる（appdata が無い環境では残さない）。"""
        root = cfg.appdata_root
        if root is None:
            return
        try:
            settings = appdata.read_settings(root)
            table = settings.get("aptitude")
            table = dict(table) if isinstance(table, dict) else {}
            table[key] = entry
            appdata.write_settings(root, {**settings, "aptitude": table})
        except Exception:
            logger.warning("aptitude: could not save the result", exc_info=True)

    @app.post("/api/ai/aptitude", dependencies=deps)
    async def aptitude_run(
        request: Request,
        x_api_key: str | None = Header(default=None),
        x_llm_provider: str | None = Header(default=None),
        x_llm_model: str | None = Header(default=None),
        x_llm_api_base: str | None = Header(default=None),
        x_llm_max_tokens: str | None = Header(default=None),
    ) -> JSONResponse:
        provider, model, api_base, key = _llm_coords(
            x_api_key, x_llm_provider, x_llm_model, x_llm_api_base, cfg.registry_root
        )
        if _ai_unset(key, provider, api_base):
            raise HTTPException(400, "AI is not set up — add a key in Settings")
        llm = resolve_llm(
            provider, model, api_base, key, max_tokens=_llm_max_tokens(x_llm_max_tokens)
        )
        store_key = ai_aptitude.settings_key(provider, model)

        async def work(emit: Any, should_cancel: Any) -> dict:
            loop = asyncio.get_running_loop()

            def progress(n: int, total: int) -> None:  # 作業スレッドから event loop へ
                loop.call_soon_threadsafe(lambda: emit(n=n, total=total))

            result = await asyncio.to_thread(
                lambda: ai_aptitude.run_aptitude(
                    llm, progress=progress, should_cancel=should_cancel
                )
            )
            entry = {**result.as_dict(), "model": model or ""}
            _save(store_key, entry)
            return {"provider": provider, **entry}

        job_id = request.app.state.jobs.start_coro(work)
        return JSONResponse({"job_id": job_id}, status_code=202)

    @app.get("/api/ai/aptitude")
    async def aptitude_list() -> dict:
        root = cfg.appdata_root
        table = appdata.read_settings(root).get("aptitude") if root is not None else None
        return {"aptitude": table if isinstance(table, dict) else {}}
