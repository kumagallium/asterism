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
import os
import unicodedata
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from asterism import chat_sessions, shared_vocab, themes
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from asterism_api import chat_attach, chat_turns
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


class ChoiceBody(BaseModel):
    """返事の中の小さな選択（ラジオ 1 組）の答え。``notes[note_index].choice`` に記録する。"""

    turn_id: str
    note_index: int = Field(ge=0)
    value: str = Field(min_length=1, max_length=200)


class TurnBody(BaseModel):
    text: str | None = None
    set_answer: SetAnswerBody | None = None
    ops: list[dict[str, Any]] | None = Field(default=None, max_length=chat_turns.MAX_OPS)
    choice: ChoiceBody | None = None
    lang: Literal["ja", "en"] = "ja"


class BindingDecision(BaseModel):
    column: str
    decided: Literal["bind", "skip"]


class ConfirmBody(BaseModel):
    lang: Literal["ja", "en"] = "ja"
    #: 📎 の表の ✓ で、列ごとの「結ぶ／結ばない」
    bindings: list[BindingDecision] | None = Field(default=None, max_length=500)


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
        given = [
            body.text is not None,
            body.ops is not None,
            body.set_answer is not None,
            body.choice is not None,
        ]
        if sum(given) != 1:
            raise HTTPException(422, "text・ops・set_answer・choice のどれか 1 つを送ってください")
        text = (body.text or "").strip()
        if has_text and not 1 <= len(text) <= chat_turns.MAX_TEXT:
            raise HTTPException(422, f"文は 1〜{chat_turns.MAX_TEXT} 字です")
        if body.ops is not None and not body.ops:
            raise HTTPException(422, "ops が空です")
        _session_or_404(session_id)  # ストアを読む前に、話があるか
        if body.choice is not None:
            llm_headers = {
                k: v
                for k, v in {
                    "x-api-key": x_api_key,
                    "x-llm-provider": x_llm_provider,
                    "x-llm-model": x_llm_model,
                    "x-llm-api-base": x_llm_api_base,
                    "x-llm-max-tokens": x_llm_max_tokens,
                }.items()
                if v
            }
            return await _choice_turn(session_id, body, llm_headers)
        existing = await _terms()
        dismissed = shared_vocab.load_dismissed(root)

        def _done(session: dict, turn: dict, reverted: list[str] | None = None) -> JSONResponse:
            saved = chat_sessions.save_session(root, session)
            card = _draft_card(saved, body.lang)
            return JSONResponse(
                {"turn": turn, "session": _head(saved), "draft": card, "reverted": reverted or []},
                status_code=201,
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
            async with lock:  # 3. 適用（話・下書き・用語・外したものを読み直す）
                session = _session_or_404(session_id)
                existing = await _terms()
                dismissed = shared_vocab.load_dismissed(root)
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
            reverted: list[str] = []
            try:
                if has_text and chat_turns.is_undo(text):
                    was = {t["id"] for t in session["turns"] if t.get("state") == "proposed"}
                    turn = chat_turns.run_undo_turn(
                        session, text, root=root, at=at, lang=body.lang
                    )
                    reverted = [
                        t["id"]
                        for t in session["turns"]
                        if t["id"] in was and t.get("state") == "reverted"
                    ]
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
            return _done(session, turn, reverted)

    @app.post("/api/chat/sessions/{session_id}/turns/{turn_id}/confirm", dependencies=deps)
    async def chat_confirm(
        session_id: str,
        turn_id: str,
        body: ConfirmBody | None = None,
        x_api_key: str | None = Header(default=None),
        x_llm_provider: str | None = Header(default=None),
        x_llm_model: str | None = Header(default=None),
        x_llm_api_base: str | None = Header(default=None),
        x_llm_max_tokens: str | None = Header(default=None),
    ) -> JSONResponse:
        lang = body.lang if body else "ja"
        async with lock:
            session = _session_or_404(session_id)
            target = next((t for t in session["turns"] if t.get("id") == turn_id), None)
            if target is not None and target.get("state") == "proposed":
                if (target.get("reply") or {}).get("suggestions"):
                    return await _confirm_suggestion(session, target, lang)
                if target.get("attachment") or target.get("attach_run"):
                    llm_headers = {
                        k: v
                        for k, v in {
                            "x-api-key": x_api_key,
                            "x-llm-provider": x_llm_provider,
                            "x-llm-model": x_llm_model,
                            "x-llm-api-base": x_llm_api_base,
                            "x-llm-max-tokens": x_llm_max_tokens,
                        }.items()
                        if v
                    }
                    listed = (body.bindings if body else None) or []
                    decisions = {d.column: d.decided for d in listed}
                    return await _confirm_attach(session, target, lang, decisions, llm_headers)
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

    async def _confirm_attach(
        session: dict, turn: dict, lang: str, decisions: dict[str, str], llm_headers: dict[str, str]
    ) -> JSONResponse:
        """📎 の表の ✓ ＝ 公開（呼び手が ``lock`` を持っている）。"""
        try:
            turn, commit, followup = await chat_attach.confirm_attach(
                app,
                cfg,
                session,
                turn,
                lang=lang,
                decisions=decisions,
                llm_headers=llm_headers,
                token=cfg.api_token,
                at=_now(),
            )
        except chat_turns.TurnRefused as exc:
            raise _refused(exc) from exc
        saved = _save_after_confirm(session, None, lang)
        return JSONResponse(
            {
                "turn": turn,
                "session": _head(saved),
                "commit": commit,
                "followup": followup,
                "confirmed": [turn["id"]] if commit else [],
            }
        )

    async def _confirm_suggestion(session: dict, turn: dict, lang: str) -> JSONResponse:
        """機械の手番（名前が一致した関係）の ✓ ＝ ``/api/sky/suggestions/apply``。"""
        try:
            commit = await chat_attach.confirm_suggestion(
                app, cfg, turn, lang=lang, token=cfg.api_token
            )
        except chat_turns.TurnRefused as exc:
            raise _refused(exc) from exc
        saved = _save_after_confirm(session, None, lang)
        return JSONResponse(
            {
                "turn": turn,
                "session": _head(saved),
                "commit": commit,
                "confirmed": [turn["id"]] if commit else [],
            }
        )

    @app.post("/api/chat/sessions/{session_id}/turns/{turn_id}/revert", dependencies=deps)
    async def chat_revert(session_id: str, turn_id: str, lang: str = "ja") -> JSONResponse:
        async with lock:
            session = _session_or_404(session_id)
            target = next((t for t in session["turns"] if t.get("id") == turn_id), None)
            if target is not None and target.get("state") == "proposed":
                is_suggestion = bool((target.get("reply") or {}).get("suggestions"))
                if is_suggestion or target.get("attachment") or target.get("attach_run"):
                    if is_suggestion:
                        await chat_attach.revert_suggestion(app, target, token=cfg.api_token)
                    else:  # 📎 の表: 取り込んだ下書きはそのまま、返事だけ戻す
                        target["state"] = "reverted"
                        target["before"] = None
                    saved = chat_sessions.save_session(root, session)
                    return JSONResponse(
                        {
                            "turn": target,
                            "session": _head(saved),
                            "draft": _draft_card(saved, lang),
                            "reverted": [turn_id],
                        }
                    )
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

    # ---- 📎 でデータ（PR 11c） -------------------------------------------------------------

    def _start_attach_job(
        session_id: str,
        turn_id: str,
        files: list[tuple[str, bytes]] | None,
        lang: str,
        llm_headers: dict[str, str],
        resume: dict | None = None,
    ) -> str:
        jobs = app.state.jobs
        return jobs.start_coro(
            lambda emit, _cancel: chat_attach.run_attach(
                app,
                cfg,
                session_id,
                turn_id,
                files,
                lang=lang,
                llm_headers=llm_headers,
                token=cfg.api_token,
                emit=emit,
                resume=resume,
            )
        )

    def _ai_ready(llm_headers: dict[str, str]) -> bool:
        provider, _model, api_base, key = _llm_coords(
            llm_headers.get("x-api-key"),
            llm_headers.get("x-llm-provider"),
            llm_headers.get("x-llm-model"),
            llm_headers.get("x-llm-api-base"),
            root,
        )
        return not _ai_unset(key, provider, api_base)

    @app.post("/api/chat/sessions/{session_id}/attach", dependencies=deps)
    async def chat_attach_files(session_id: str, request: Request) -> JSONResponse:
        """📎 — 表を添える。人の手番（``state: running``）を作り、かんたんの段を通すジョブを始める。
        multipart ``files``（``files[]`` も可）・``lang``・LLM ヘッダ。"""
        form = await request.form()
        uploads = [
            f for f in [*form.getlist("files"), *form.getlist("files[]")] if hasattr(f, "filename")
        ]
        lang = "en" if str(form.get("lang") or "ja").lower().startswith("en") else "ja"
        if not uploads:
            raise HTTPException(422, "表のファイルがありません")
        _session_or_404(session_id)
        llm_headers = chat_attach.llm_headers_of(request.headers)
        names = [os.path.basename(str(f.filename or "")) or "data" for f in uploads]
        if not all(chat_attach.is_table_name(n) for n in names):
            async with lock:
                session = _session_or_404(session_id)
                turn = chat_turns._append_turn(
                    session,
                    at=_now(),
                    user={"text": "", "attachments": [{"name": n, "size": 0} for n in names]},
                    reply=chat_turns._reply(
                        "",
                        notes=[
                            chat_turns.note("unsupported", chat_attach.text(lang, "unsupported"))
                        ],
                    ),
                    state="none",
                    before=None,
                    by="machine",
                )
                saved = chat_sessions.save_session(root, session)
            return JSONResponse(
                {"turn": turn, "session": _head(saved), "job_id": None}, status_code=201
            )
        if not _ai_ready(llm_headers):
            raise HTTPException(503, "AI が設定されていません")
        cap = int(os.environ.get("ASTERISM_MAX_UPLOAD_BYTES", str(1 << 30)))
        data: list[tuple[str, bytes]] = []
        total = 0
        for name, f in zip(names, uploads, strict=True):
            blob = await f.read()
            total += len(blob)
            if cap > 0 and total > cap:
                raise HTTPException(413, "ファイルが大きすぎます")
            data.append((name, blob))
        async with lock:
            session = _session_or_404(session_id)
            turn = chat_turns._append_turn(
                session,
                at=_now(),
                user={"text": "", "attachments": [{"name": n, "size": len(b)} for n, b in data]},
                reply=chat_turns._reply(""),
                state="running",
                before=None,
                by="machine",
            )
            turn["attachment"] = None
            job_id = _start_attach_job(session_id, turn["id"], data, lang, llm_headers)
            turn["job_id"] = job_id
            saved = chat_sessions.save_session(root, session)
        return JSONResponse(
            {"turn": turn, "session": _head(saved), "job_id": job_id}, status_code=201
        )

    async def _choice_turn(
        session_id: str, body: TurnBody, llm_headers: dict[str, str]
    ) -> JSONResponse:
        """小さな選択の答え — 記録して、④ から続きを走らせる（同じ手番を更新する）。"""
        choice = body.choice
        assert choice is not None
        if not _ai_ready(llm_headers):
            raise HTTPException(503, "AI が設定されていません")
        async with lock:
            session = _session_or_404(session_id)
            turn = next((t for t in session["turns"] if t.get("id") == choice.turn_id), None)
            if turn is None:
                raise HTTPException(404, "その手番はありません")
            notes = (turn.get("reply") or {}).get("notes") or []
            if turn.get("state") != "proposed" or choice.note_index >= len(notes):
                raise HTTPException(409, "この選択は受けられません")
            note = notes[choice.note_index]
            picked = note.get("choice")
            if note.get("kind") != "choice_id" or not isinstance(picked, dict):
                raise HTTPException(422, "この選択は受けられません")
            if choice.value not in [o["value"] for o in picked.get("options") or []]:
                raise HTTPException(422, "選べない値です")
            picked["chosen"] = choice.value
            turn["state"] = "running"
            job_id = _start_attach_job(
                session_id,
                turn["id"],
                None,
                body.lang,
                llm_headers,
                {"note_index": choice.note_index, "value": choice.value},
            )
            turn["job_id"] = job_id
            saved = chat_sessions.save_session(root, session)
        return JSONResponse(
            {"turn": turn, "session": _head(saved), "job_id": job_id}, status_code=201
        )
