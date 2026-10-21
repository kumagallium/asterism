"""``register_chat(app, cfg, resolve_llm, write_auth=…)`` — 話（チャット）の api（PR 11a・§19）。

1 つの話 = 1 つのセッション = 1 つのテーマ。未確認の提案はテーマの下書き
（``themes/<id>/draft.json``）に入り、✓ で ``themes.commit_theme`` が 1 操作でストアに置く
（ストアには確認済みだけが入る）。話の手番の記録は ``asterism.chat_sessions``。

* ``GET  /api/chat/sessions`` — 一覧。
* ``POST /api/chat/sessions`` — 空の話を作る（201）。
* ``GET / PATCH / DELETE /api/chat/sessions/{id}`` — 読む・名前を直す・捨てる（確認済みは残す）。
* ``POST /api/chat/sessions/{id}/turns`` — 手番（文 または 手で足す差分）。文の手番は、AI が
  設定されていれば AI の返事（準備＝ロックの中 → AI＝**ロックの外** → 適用＝ロックの中で読み直し）。
  未設定なら 11a の道（``notes.ai_unset``）。「さっきのを戻して」は AI を呼ばず決定論で戻す。
* ``POST …/turns/{turn}/confirm`` — ✓。``POST …/turns/{turn}/revert`` — 未確認の返事を控えに戻す。

全部 ``write_auth``。下書き・テーマの読み書きは ``app.state.themes_lock`` で 1 つずつ。
組み立ては :mod:`asterism_api.chat_turns`（ここは受け取りと HTTP への写像だけ）。
"""

# ruff: noqa: RUF001
from __future__ import annotations

import asyncio
import logging
import unicodedata
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from asterism import chat_sessions, shared_vocab, themes
from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from asterism_api import chat_turns
from asterism_api.converse_routes import _llm_coords, _llm_max_tokens, _record_usage
from asterism_api.themes_routes import draft_sky_card

if TYPE_CHECKING:
    from asterism.oxigraph_client import OxigraphClient

    from asterism_api.main import Settings

__all__ = ["register_chat"]

logger = logging.getLogger(__name__)

_MAX_TITLE = 80


def _one_short_line(v: str, what: str) -> str:
    if len(v) > _MAX_TITLE or any(
        unicodedata.category(c) == "Cc" or c in "  " for c in v
    ):
        raise ValueError(f"{what}は 1 行 {_MAX_TITLE} 字までで、制御文字を含めません")
    return v


class CreateBody(BaseModel):
    title: str = ""

    @field_validator("title")
    @classmethod
    def _title(cls, v: str) -> str:
        return _one_short_line(v.strip(), "名前")


class RenameBody(BaseModel):
    title: str

    @field_validator("title")
    @classmethod
    def _title(cls, v: str) -> str:
        v = _one_short_line(v.strip(), "名前")
        if not v:
            raise ValueError("名前が空です")
        return v


class SetAnswerBody(BaseModel):
    question: str
    mode: Literal["knowledge", "data"]
    op: str
    target: str = ""
    direction: str = ""
    kind: str = ""
    property: str = ""
    value: str = ""
    match: str = ""
    property2: str = ""
    kind2: str = ""


class TurnBody(BaseModel):
    text: str | None = None
    set_answer: SetAnswerBody | None = None
    ops: list[dict[str, Any]] | None = Field(default=None, max_length=chat_turns.MAX_OPS)
    lang: Literal["ja", "en"] = "ja"


class ConfirmBody(BaseModel):
    lang: Literal["ja", "en"] = "ja"


def _ai_unset(key: str | None, provider: str, api_base: str | None) -> bool:
    """キーが無い（かつ手元の互換サーバでもない）= AI は未設定。copilot はキー無しで使える。"""
    if provider in ("copilot-subscription", "copilot"):
        return False
    return key is None and not (provider != "anthropic" and api_base)


def _head(session: dict) -> dict:
    return {
        "id": session["id"],
        "title": session.get("title") or "",
        "theme_id": session.get("theme_id"),
        "draft_id": session.get("draft_id"),
        "updated_at": session.get("updated_at"),
    }


def register_chat(
    app: FastAPI,
    cfg: Settings,
    resolve_llm: Any,
    *,
    write_auth: Sequence[Any] = (),
) -> None:
    """Register the ``/api/chat/*`` routes on ``app`` (called once from ``build_app``)."""
    # 下書きの読み→書きは themes_routes と同じ鍵で 1 つずつ（register_themes が先に置く）
    lock: asyncio.Lock = getattr(app.state, "themes_lock", None) or asyncio.Lock()
    root = cfg.registry_root
    deps = list(write_auth)

    def _client() -> OxigraphClient:
        return app.state.client

    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def _session_or_404(session_id: str) -> dict:
        session = chat_sessions.load_session(root, session_id)
        if session is None:
            raise HTTPException(404, "その話はありません")
        return session

    async def _terms() -> list[shared_vocab.SharedTerm]:
        try:
            return await shared_vocab._read_terms(_client())
        except Exception as exc:
            raise HTTPException(502, f"shared vocab read failed: {exc}") from exc

    def _draft_card(session: dict, lang: str) -> dict | None:
        """いまの下書きの札（無ければ ``None``）。"""
        draft_id = session.get("draft_id")
        draft = themes.load_draft(root, draft_id) if draft_id else None
        if draft is None:
            return None
        seed = session.get("seed")
        seed = seed if isinstance(seed, dict) and seed.get("draft_id") == draft["id"] else None
        return {**draft_sky_card(draft, lang, seed), "session_id": session["id"]}

    def _save_after_confirm(session: dict, commit: dict | None, lang: str) -> dict:
        """✓ のあとの保存。落ちたら 1 度だけやり直し、それでも落ちたら 500。"""
        for attempt in (1, 2):
            try:
                return chat_sessions.save_session(root, session)
            except Exception:
                logger.warning("chat save after confirm failed (%d)", attempt, exc_info=True)
        tid = commit["theme_id"] if commit else ""
        if lang == "en":
            msg = (
                f"Could not save the chat (theme {tid} is saved)"
                if tid
                else "Could not save the chat"
            )
        else:
            msg = (
                f"話を保存できませんでした（テーマ {tid} は保存されています）"
                if tid
                else "話を保存できませんでした"
            )
        raise HTTPException(500, msg)

    def _refused(exc: chat_turns.TurnRefused) -> HTTPException:
        return HTTPException(exc.status, str(exc))

    # ---- 一覧・作る・読む ---------------------------------------------------------------------

    @app.get("/api/chat/sessions", dependencies=deps)
    async def chat_list() -> JSONResponse:
        return JSONResponse({"sessions": chat_sessions.list_sessions(root)})

    @app.post("/api/chat/sessions", dependencies=deps)
    async def chat_create(body: CreateBody | None = None) -> JSONResponse:
        session = chat_sessions.new_session((body.title if body else ""), at=_now())
        async with lock:
            saved = chat_sessions.save_session(root, session)
        return JSONResponse(saved, status_code=201)

    @app.get("/api/chat/sessions/{session_id}", dependencies=deps)
    async def chat_get(session_id: str) -> JSONResponse:
        return JSONResponse(_session_or_404(session_id))

    # ---- 名前・消す ---------------------------------------------------------------------------

    @app.patch("/api/chat/sessions/{session_id}", dependencies=deps)
    async def chat_rename(session_id: str, body: RenameBody) -> JSONResponse:
        async with lock:
            session = _session_or_404(session_id)
            try:
                if session.get("theme_id"):  # 置いたテーマの名前も同じに
                    data = themes.load_themes_file(root)
                    if data is not None:
                        for t in data["themes"]:
                            if t.get("id") == session["theme_id"]:
                                t["label"] = body.title
                        themes._save_themes(root, data)
                draft_id = session.get("draft_id")
                draft = themes.load_draft(root, draft_id) if draft_id else None
                if draft is not None:
                    themes.save_draft(root, {**draft, "label": body.title, "updated_at": _now()})
            except themes.ThemeError as exc:
                raise HTTPException(exc.status, str(exc)) from exc
            session["title"] = body.title
            saved = chat_sessions.save_session(root, session)
        return JSONResponse(saved)

    @app.delete("/api/chat/sessions/{session_id}", dependencies=deps)
    async def chat_delete(session_id: str) -> Response:
        async with lock:
            session = _session_or_404(session_id)
            if session.get("draft_id"):  # 未確認は捨てる。確認済み（テーマ）は残す
                themes.delete_draft(root, session["draft_id"])
            chat_sessions.delete_session(root, session_id)
        return Response(status_code=204)

    # ---- 手番 ---------------------------------------------------------------------------------

    @app.post("/api/chat/sessions/{session_id}/turns", dependencies=deps)
    async def chat_turn(
        session_id: str,
        body: TurnBody,
        x_api_key: str | None = Header(default=None),
        x_llm_provider: str | None = Header(default=None),
        x_llm_model: str | None = Header(default=None),
        x_llm_api_base: str | None = Header(default=None),
        x_llm_max_tokens: str | None = Header(default=None),
    ) -> JSONResponse:
        has_text = body.text is not None
        given = [body.text is not None, body.ops is not None, body.set_answer is not None]
        if sum(given) != 1:
            raise HTTPException(422, "text・ops・set_answer のどれか 1 つを送ってください")
        text = (body.text or "").strip()
        if has_text and not 1 <= len(text) <= chat_turns.MAX_TEXT:
            raise HTTPException(422, f"文は 1〜{chat_turns.MAX_TEXT} 字です")
        if body.ops is not None and not body.ops:
            raise HTTPException(422, "ops が空です")
        _session_or_404(session_id)  # ストアを読む前に、話があるか
        existing = await _terms()
        dismissed = shared_vocab.load_dismissed(root)

        def _done(session: dict, turn: dict) -> JSONResponse:
            saved = chat_sessions.save_session(root, session)
            card = _draft_card(saved, body.lang)
            return JSONResponse(
                {"turn": turn, "session": _head(saved), "draft": card}, status_code=201
            )

        # AI の道（文の手番で、AI が設定されていて、「戻して」でないとき）
        llm: Any = None
        provider, model, api_base, key = _llm_coords(
            x_api_key, x_llm_provider, x_llm_model, x_llm_api_base, root
        )
        if has_text and not chat_turns.is_undo(text) and not _ai_unset(key, provider, api_base):
            llm = resolve_llm(
                provider, model, api_base, key, max_tokens=_llm_max_tokens(x_llm_max_tokens)
            )
        if llm is not None:
            async with lock:  # 1. 準備（読むだけ）
                session = _session_or_404(session_id)
                prepared = chat_turns.prepare_text_turn(
                    session, text, root=root, existing=existing, lang=body.lang
                )
            result: dict | None = None
            if prepared.mode != "refuse":  # 2. AI（ロックの外）
                result = await chat_turns.ai_text_turn(prepared, llm)
                if result["ok"]:
                    await asyncio.to_thread(
                        _record_usage, root, "chat.turn", provider, llm, model
                    )
            async with lock:  # 3. 適用（話と下書きを読み直す）
                session = _session_or_404(session_id)
                try:
                    turn = chat_turns.apply_text_turn(
                        session,
                        prepared,
                        result,
                        root=root,
                        existing=existing,
                        dismissed=dismissed,
                        at=_now(),
                        lang=body.lang,
                    )
                except chat_turns.TurnRefused as exc:
                    raise _refused(exc) from exc
                return _done(session, turn)
        async with lock:
            session = _session_or_404(session_id)
            at = _now()
            try:
                if has_text and chat_turns.is_undo(text):
                    turn = chat_turns.run_undo_turn(
                        session, text, root=root, at=at, lang=body.lang
                    )
                elif has_text:
                    turn = chat_turns.run_text_turn(
                        session, text, lang=body.lang, root=root, at=at
                    )
                elif body.set_answer is not None:
                    turn = chat_turns.run_set_answer_turn(
                        session,
                        body.set_answer.model_dump(),
                        root=root,
                        at=at,
                        lang=body.lang,
                    )
                else:
                    turn = chat_turns.run_ops_turn(
                        session,
                        body.ops or [],
                        lang=body.lang,
                        root=root,
                        existing=existing,
                        dismissed=dismissed,
                        at=at,
                    )
            except chat_turns.TurnRefused as exc:
                raise _refused(exc) from exc
            return _done(session, turn)

    @app.post("/api/chat/sessions/{session_id}/turns/{turn_id}/confirm", dependencies=deps)
    async def chat_confirm(
        session_id: str, turn_id: str, body: ConfirmBody | None = None
    ) -> JSONResponse:
        lang = body.lang if body else "ja"
        async with lock:
            session = _session_or_404(session_id)
            was = {t["id"] for t in session["turns"] if t.get("state") == "proposed"}
            try:
                turn, commit = await chat_turns.confirm(
                    session, turn_id, client=_client(), lang=lang, root=root, at=_now()
                )
            except chat_turns.TurnRefused as exc:
                raise _refused(exc) from exc
            except Exception as exc:  # 書き込みの失敗（commit_theme は巻き戻し済み）
                logger.warning("chat confirm failed", exc_info=True)
                raise HTTPException(502, f"theme write failed: {exc}") from exc
            saved = _save_after_confirm(session, commit, lang)
        confirmed = [
            t["id"] for t in saved["turns"] if t["id"] in was and t.get("state") == "confirmed"
        ]
        return JSONResponse(
            {"turn": turn, "session": _head(saved), "commit": commit, "confirmed": confirmed}
        )

    @app.post("/api/chat/sessions/{session_id}/turns/{turn_id}/revert", dependencies=deps)
    async def chat_revert(session_id: str, turn_id: str, lang: str = "ja") -> JSONResponse:
        async with lock:
            session = _session_or_404(session_id)
            try:
                turn, undone = chat_turns.revert(
                    session, turn_id, root=root, at=_now(), lang=lang
                )
            except chat_turns.TurnRefused as exc:
                raise _refused(exc) from exc
            saved = chat_sessions.save_session(root, session)
            card = _draft_card(saved, lang)
        return JSONResponse(
            {"turn": turn, "session": _head(saved), "draft": card, "reverted": undone}
        )
