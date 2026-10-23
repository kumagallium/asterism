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
  確認済みの手番は ``commit`` の記録から取り消す（線・用語・問い。ほかが使う用語は残す）。
* ``GET  /api/chat/point`` — 点（用語・種類・標準用語・視点・線・未確認の札）の 1 枚。
* ``GET  /api/chat/contents`` — 押した点の中身（ABox）の表（kind・term・dataset・session の 1 つ）。
* 文の手番は ``chat_intent.classify`` で振り分ける: 「<名前>を見せて」は AI を呼ばず閉じた
  テンプレートの表（``reply.table``）・「答えて」の問いは demo-agent の Ask（``reply.answer``）・
  それ以外は図の提案（11b）。

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

from asterism import chat_sessions, crosswalk_runtime, shared_vocab, sky_view, themes
from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from asterism_api import (
    chat_attach,
    chat_contents,
    chat_intent,
    chat_point,
    chat_route,
    chat_tables,
    chat_turns,
    registry,
    source_push,
)
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


class RemoveBody(BaseModel):
    """点から外す — 確認済みの用語（``iri``）か関係（``from``・``relation``・``to``）。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    iri: str | None = Field(default=None, min_length=1, max_length=2000)
    from_: str | None = Field(default=None, alias="from", min_length=1, max_length=2000)
    relation: str | None = Field(default=None, min_length=1, max_length=2000)
    to: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def _one_shape(self) -> RemoveBody:
        line = [self.from_, self.relation, self.to]
        by_iri = self.iri is not None and all(x is None for x in line)
        by_line = self.iri is None and all(x is not None for x in line)
        if not (by_iri or by_line):
            raise ValueError("iri か、from・relation・to の 3 つのどちらかを送ってください")
        return self


class ScopeLine(BaseModel):
    """範囲の 1 行。下書きの鍵・短い関係名、承認済みの行は IRI（サーバが slug に直す）。"""

    model_config = ConfigDict(populate_by_name=True)
    from_: str = Field(alias="from", min_length=1, max_length=500)
    rel: str = Field(min_length=1, max_length=500)
    to: str = Field(min_length=1, max_length=500)


class ScopeBody(BaseModel):
    """チャットの範囲（11o）。``node`` = UI の点の id（``t|<iri>``・``k|<データセット>|<iri>``・
    ``draft|<下書き>|<鍵>``）。``line`` = 範囲を 1 行に絞るとき。"""

    node: str = Field(min_length=1, max_length=1000)
    line: ScopeLine | None = None


class AttachFromSourceBody(BaseModel):
    """📎 の 2 択の片方: 取り込み元（MCP サーバ）のファイルの id。"""

    server: str
    ids: list[str]
    lang: str = "ja"


class TurnBody(BaseModel):
    text: str | None = None
    scope: ScopeBody | None = None
    set_answer: SetAnswerBody | None = None
    ops: list[dict[str, Any]] | None = Field(default=None, max_length=chat_turns.MAX_OPS)
    choice: ChoiceBody | None = None
    remove: RemoveBody | None = None
    # 「答えて」／「知識グラフに足す」チップ: 振り分けを飛ばして Ask／提案へ
    intent: Literal["ask", "propose"] | None = None
    lang: Literal["ja", "en"] = "ja"


class OnlyRelation(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_: str = Field(alias="from", min_length=1, max_length=500)
    rel: str = Field(min_length=1, max_length=500)
    to: str = Field(min_length=1, max_length=500)


class OnlyBody(BaseModel):
    """1 行の承認（11o）— 選んだ行（と両端の未承認の概念）だけを承認する。"""

    relations: list[OnlyRelation] = Field(default_factory=list, max_length=20)
    concepts: list[str] = Field(default_factory=list, max_length=40)


class BindingDecision(BaseModel):
    column: str
    decided: Literal["bind", "skip"]


class ConfirmBody(BaseModel):
    lang: Literal["ja", "en"] = "ja"
    #: 📎 の表の ✓ で、列ごとの「結ぶ／結ばない」
    bindings: list[BindingDecision] | None = Field(default=None, max_length=500)
    #: 11o: 1 行の承認（テーマがあるときだけ）
    only: OnlyBody | None = None


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

    def _stale_running(session: dict) -> list[dict]:
        """``running`` なのに、走っているジョブが無い（再起動で消えた・終わっている）手番。"""
        jobs = app.state.jobs
        out = []
        for t in session.get("turns") or []:
            if t.get("state") != "running":
                continue
            job = jobs.get(t["job_id"]) if t.get("job_id") else None
            if job is None or job.status in ("done", "error", "cancelled"):
                out.append(t)
        return out

    async def _settle(session_id: str) -> dict | None:
        """読むときに、止まったまま残った ``running`` の手番を ``attach_failed`` に落とす。"""
        session = chat_sessions.load_session(root, session_id)
        if session is None or not _stale_running(session):
            return session
        leftovers: list[str] = []
        async with lock:
            session = chat_sessions.load_session(root, session_id)
            if session is None:
                return None
            for t in _stale_running(session):
                lang = (t.get("attach_run") or {}).get("lang") or "ja"
                run = t.get("attach_run") or {}
                if run.get("dataset_id"):
                    leftovers.append(run["dataset_id"])
                    run.pop("dataset_id", None)
                t["reply"] = chat_attach.interrupted_reply(lang)
                t["state"] = "none"
                t["job_id"] = None
            session = chat_sessions.save_session(root, session)
        for dataset_id in leftovers:
            await chat_attach.discard_dataset(app, cfg, dataset_id, token=cfg.api_token)
        return session

    @app.get("/api/chat/sessions", dependencies=deps)
    async def chat_list() -> JSONResponse:
        for head in chat_sessions.list_sessions(root):
            await _settle(head["id"])
        return JSONResponse({"sessions": chat_sessions.list_sessions(root)})

    @app.post("/api/chat/sessions", dependencies=deps)
    async def chat_create(body: CreateBody | None = None) -> JSONResponse:
        session = chat_sessions.new_session((body.title if body else ""), at=_now())
        async with lock:
            saved = chat_sessions.save_session(root, session)
        return JSONResponse(saved, status_code=201)

    @app.get("/api/chat/sessions/{session_id}", dependencies=deps)
    async def chat_get(session_id: str) -> JSONResponse:
        session = await _settle(session_id)
        if session is None:
            raise HTTPException(404, "その話はありません")
        return JSONResponse(session)

    # ---- 点の 1 枚 ---------------------------------------------------------------------------

    @app.get("/api/chat/point", dependencies=deps)
    async def chat_point_card(
        iri: str | None = Query(default=None),
        from_: str | None = Query(default=None, alias="from"),
        relation: str | None = Query(default=None),
        to: str | None = Query(default=None),
        lang: Literal["ja", "en"] = "ja",
    ) -> JSONResponse:
        try:
            card = await chat_point.build_point(
                _client(), root, iri=iri, source=from_, relation=relation, target=to, lang=lang
            )
        except chat_point.PointError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        except Exception as exc:
            logger.warning("chat point failed", exc_info=True)
            raise HTTPException(502, f"point read failed ({type(exc).__name__})") from exc
        return JSONResponse(card)

    # ---- 点の中身（ABox） -----------------------------------------------------------------------

    @app.get("/api/chat/contents", dependencies=deps)
    async def chat_contents_of(
        kind: str | None = Query(default=None),
        term: str | None = Query(default=None),
        dataset: str | None = Query(default=None),
        session: str | None = Query(default=None),
        lang: Literal["ja", "en"] = "ja",
        limit: int = Query(default=chat_tables.MAX_ROWS, ge=1),
    ) -> JSONResponse:
        """押した点の中身の表（読むだけ・AI を呼ばない・ロック不要）。"""
        # 5 通り: kind 単独・term・dataset 単独・session・dataset + kind
        if sum(x is not None for x in (term, session)) + (
            1 if (kind is not None or dataset is not None) else 0
        ) != 1:
            raise HTTPException(
                400, "kind・term・dataset・session のどれか 1 つ（dataset と kind は一緒に可）"
            )
        found = _session_or_404(session) if session is not None else None
        sky: dict = {}
        try:
            if term is not None:
                sky = await sky_view.sky_view(_client(), root)
            card = await chat_contents.build_contents(
                _client(),
                root,
                kind=kind,
                term=term,
                dataset=dataset,
                session=found,
                use_session=session is not None,
                sky=sky,
                lang=lang,
                limit=limit,
            )
        except chat_contents.ContentsError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        except Exception as exc:
            logger.warning("chat contents failed", exc_info=True)
            raise HTTPException(502, f"contents read failed ({type(exc).__name__})") from exc
        return JSONResponse(card)

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
            # 添えた表のうち、公開していない取り込み済みデータも片づける
            for t in session.get("turns") or []:
                dataset_id = (t.get("attachment") or {}).get("dataset_id")
                if dataset_id and t.get("state") != "confirmed":
                    await chat_attach.discard_dataset(
                        app, cfg, dataset_id, token=cfg.api_token
                    )
        return Response(status_code=204)

    # ---- 手番 ---------------------------------------------------------------------------------

    async def _sky_names() -> tuple[dict, dict[str, dict[str, str]]]:
        """公開済みの星図と、種類・用語の表示名 → ``{iri, kind}``（種類が先）。公開済みの種類は
        ``kind: "dataset_kind"``、用語は ``"class"`` / ``"property"``。読めなければ空。"""
        try:
            sky = await sky_view.sky_view(_client(), root)
        except Exception:
            logger.warning("chat intent: sky read failed", exc_info=True)
            return {}, {}
        names: dict[str, dict[str, str]] = {}
        for d in sky.get("datasets") or []:
            for k in d.get("kinds") or []:
                names.setdefault(str(k["label"]), {"iri": k["iri"], "kind": "dataset_kind"})
        for t in sky.get("terms") or []:
            for label in (t.get("label"), t.get("label_en")):
                if label:
                    names.setdefault(
                        str(label), {"iri": t["iri"], "kind": str(t.get("kind") or "class")}
                    )
        return sky, names

    def _demo_url() -> str | None:
        return getattr(app.state, "demo_agent_url", None) or cfg.demo_agent_url

    async def _show_turn(
        session_id: str, text: str, lang: str, sky: dict, iri: str
    ) -> tuple[dict, dict]:
        """「<名前>を見せて」— 読むだけで表を作り（AI なし）、短いロックで手番を足す。"""
        kinds = {k["iri"] for d in sky.get("datasets") or [] for k in d.get("kinds") or []}
        via: str | None = None
        kind_iri: str | None = iri if iri in kinds else None
        if kind_iri is None:
            kind_iri = chat_tables.kind_for_term(sky, iri)
            term = next((t for t in sky.get("terms") or [] if t["iri"] == iri), None)
            via = str((term or {}).get("label") or "") or None
        table = None
        if kind_iri is not None:
            try:
                table = await chat_tables.table_for_kind(_client(), root, kind_iri, lang=lang)
            except Exception:
                logger.warning("chat show: table failed", exc_info=True)
        async with lock:
            session = _session_or_404(session_id)
            turn = chat_turns.run_show_turn(
                session, text, table, via_term=via if kind_iri != iri else None,
                at=_now(), lang=lang,
            )
            saved = chat_sessions.save_session(root, session)
        return turn, saved

    async def _ask_turn(
        session_id: str, text: str, lang: str, llm_headers: dict[str, str]
    ) -> tuple[dict, dict]:
        """「答えて」の問い — 既存の Ask（demo-agent）へ。呼び出しは**ロックの外**。"""
        url = _demo_url()
        if url is None:
            async with lock:
                session = _session_or_404(session_id)
                turn = chat_turns.run_ask_unavailable_turn(session, text, at=_now(), lang=lang)
                return turn, chat_sessions.save_session(root, session)
        async with lock:
            history = chat_turns.ask_history(_session_or_404(session_id))
        outcome = await chat_turns.ask_remote(
            url,
            text,
            history,
            llm_headers,
            transport=getattr(app.state, "ask_transport", None),
        )
        cited: dict[str, dict[str, str]] = {}
        if outcome.get("ok"):
            try:
                cited = await chat_tables.resolve_cited(
                    _client(),
                    root,
                    [
                        str(c.get("iri"))
                        for c in outcome["data"].get("citations") or []
                        if isinstance(c, dict)
                    ],
                )
            except Exception:
                logger.warning("chat ask: citation names failed", exc_info=True)
        async with lock:
            session = _session_or_404(session_id)
            turn = chat_turns.run_ask_turn(
                session, text, outcome, cited=cited, at=_now(), lang=lang
            )
            return turn, chat_sessions.save_session(root, session)

    def _n_datasets() -> int:
        """公開済みのデータセットの数（sky は読まない軽い読み）。"""
        return sum(
            1
            for m in registry.list_datasets(root)
            if m.get("promoted") and not shared_vocab.is_system_entry(m)
        )

    @app.post("/api/chat/sessions/{session_id}/turns", dependencies=deps)
    async def chat_turn(
        session_id: str,
        body: TurnBody,
        request: Request,
        x_api_key: str | None = Header(default=None),
        x_llm_provider: str | None = Header(default=None),
        x_llm_model: str | None = Header(default=None),
        x_llm_api_base: str | None = Header(default=None),
        x_llm_max_tokens: str | None = Header(default=None),
    ) -> JSONResponse:
        has_text = body.text is not None
        # 11o: 範囲（点か行）。範囲つきの文は AI の振り分けを飛ばして提案（直し）へ
        scope: dict | None = None
        if body.scope is not None and has_text:
            ln = body.scope.line
            scope = {
                "node": body.scope.node,
                "line": {"from": ln.from_, "rel": ln.rel, "to": ln.to} if ln else None,
            }
        given = [
            body.text is not None,
            body.ops is not None,
            body.set_answer is not None,
            body.choice is not None,
            body.remove is not None,
        ]
        if sum(given) != 1:
            raise HTTPException(
                422, "text・ops・set_answer・choice・remove のどれか 1 つを送ってください"
            )
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
        if body.remove is not None:
            sky: dict = {}
            try:
                sky = await sky_view.sky_view(_client(), root)
            except Exception:
                logger.warning("chat remove: sky read failed", exc_info=True)
            kinds = {k["iri"] for d in sky.get("datasets") or [] for k in d.get("kinds") or []}
            async with lock:
                session = _session_or_404(session_id)
                try:
                    turn = chat_turns.run_remove_turn(
                        session,
                        {
                            "iri": body.remove.iri,
                            "from": body.remove.from_,
                            "relation": body.remove.relation,
                            "to": body.remove.to,
                        },
                        root=root,
                        existing=existing,
                        dismissed=dismissed,
                        published_kinds=kinds,
                        at=_now(),
                        lang=body.lang,
                    )
                except chat_turns.TurnRefused as exc:
                    raise _refused(exc) from exc
                saved = chat_sessions.save_session(root, session)
                return JSONResponse(
                    {"turn": turn, "session": _head(saved), "draft": _draft_card(saved, body.lang),
                     "reverted": []},
                    status_code=201,
                )  # fmt: skip

        def _done(session: dict, turn: dict, reverted: list[str] | None = None) -> JSONResponse:
            saved = chat_sessions.save_session(root, session)
            card = _draft_card(saved, body.lang)
            return JSONResponse(
                {"turn": turn, "session": _head(saved), "draft": card, "reverted": reverted or []},
                status_code=201,
            )

        # 問いのタブの「答えて」: 振り分けを飛ばして必ず Ask
        if has_text and body.intent == "ask":
            turn, saved = await _ask_turn(
                session_id, text, body.lang, chat_attach.llm_headers_of(request.headers)
            )
            return JSONResponse(
                {"turn": turn, "session": _head(saved), "draft": _draft_card(saved, body.lang),
                 "reverted": []},
                status_code=201,
            )  # fmt: skip

        # 振り分け（決定論）: 「見せて」は表・「答えて」は Ask。AI（11b の道）は呼ばない
        propose_forced = has_text and body.intent == "propose"  # チップ: 1・2 を飛ばす
        if (
            has_text
            and scope is None
            and not propose_forced
            and not chat_turns.is_undo(text)
            and chat_intent.may_need_names(text)
        ):
            sky, names = await _sky_names()
            draft_id = _session_or_404(session_id).get("draft_id")
            intent = chat_intent.classify(
                text,
                names=names,
                draft_open=bool(draft_id and themes.load_draft(root, draft_id) is not None),
            )
            if intent == "show":
                label = chat_intent.show_target(text, names)
                turn, saved = await _show_turn(
                    session_id, text, body.lang, sky, chat_intent.entry_iri(names, label)
                )
                return JSONResponse(
                    {"turn": turn, "session": _head(saved), "draft": _draft_card(saved, body.lang),
                     "reverted": []},
                    status_code=201,
                )  # fmt: skip
            if intent == "ask":
                turn, saved = await _ask_turn(
                    session_id, text, body.lang, chat_attach.llm_headers_of(request.headers)
                )
                return JSONResponse(
                    {"turn": turn, "session": _head(saved), "draft": _draft_card(saved, body.lang),
                     "reverted": []},
                    status_code=201,
                )  # fmt: skip

        provider, model, api_base, key = _llm_coords(
            x_api_key, x_llm_provider, x_llm_model, x_llm_api_base, root
        )
        llm_unset = _ai_unset(key, provider, api_base)
        # 11n: 「〈文〉を外して」「A は B の一種ではない」は AI を呼ばずに remove にする
        if has_text and not chat_turns.is_undo(text):
            async with lock:
                session = _session_or_404(session_id)
                cur = session.get("draft_id")
                cur_draft = themes.load_draft(root, cur) if cur else None
                removal = chat_intent.parse_remove(text, cur_draft)
                if removal:
                    try:
                        turn = chat_turns.run_ops_turn(
                            session, removal, lang=body.lang, root=root, existing=existing,
                            dismissed=dismissed, at=_now(), user_text=text,
                        )  # fmt: skip
                    except chat_turns.TurnRefused as exc:
                        raise _refused(exc) from exc
                    return _done(session, turn)
                if scope is not None and llm_unset:  # 範囲つきの直しは AI が要る
                    turn = chat_turns.run_scope_hint_turn(
                        session, text, at=_now(), lang=body.lang
                    )
                    return _done(session, turn)
                if llm_unset and chat_intent.is_remove_form(text):
                    turn = chat_turns.run_hint_turn(session, text, at=_now(), lang=body.lang)
                    return _done(session, turn)

        # AI の道（文の手番で、AI が設定されていて、「戻して」でないとき）
        llm: Any = None
        if has_text and not chat_turns.is_undo(text) and not llm_unset:
            llm = resolve_llm(
                provider, model, api_base, key, max_tokens=_llm_max_tokens(x_llm_max_tokens)
            )
        # 11m: AI の振り分け（会話／答える／提案）。ロックの外。落ちたら提案（今の挙動）
        has_draft_now = bool(_session_or_404(session_id).get("draft_id"))
        if (
            llm is not None
            and has_text
            and body.intent != "propose"
            and scope is None  # 範囲つきは直しに決まっている
            and not chat_intent.has_graph_words(text)  # 図の言葉は決定論で提案へ
            and not (has_draft_now and chat_intent.has_fix_words(text))  # 指摘・訂正も提案へ
        ):
            async with lock:
                s0 = _session_or_404(session_id)
                hist = chat_route.history_of(s0)
                label = s0.get("title")
                has_draft = bool(s0.get("draft_id"))
            try:
                routed = await asyncio.to_thread(
                    chat_route.route_message,
                    llm,
                    text=text,
                    history=hist,
                    lang=body.lang,
                    theme_label=label,
                    has_draft=has_draft,
                    n_terms=len(existing),
                    n_datasets=await asyncio.to_thread(_n_datasets),
                )
                await asyncio.to_thread(
                    _record_usage, root, "chat.route", provider, llm, model
                )
            except Exception:
                logger.warning("chat route: fell back to propose", exc_info=True)
                routed = None
            if routed is not None and routed.mode == "ask":
                turn, saved = await _ask_turn(
                    session_id, text, body.lang, chat_attach.llm_headers_of(request.headers)
                )
                return JSONResponse(
                    {"turn": turn, "session": _head(saved), "draft": _draft_card(saved, body.lang),
                     "reverted": []},
                    status_code=201,
                )  # fmt: skip
            if routed is not None and routed.mode == "chat":
                async with lock:
                    session = _session_or_404(session_id)
                    turn = chat_turns.run_chat_turn(
                        session, text, routed.reply, at=_now(), lang=body.lang
                    )
                    return _done(session, turn)
        if llm is not None:
            async with lock:  # 1. 準備（読むだけ）
                session = _session_or_404(session_id)
                try:
                    prepared = chat_turns.prepare_text_turn(
                        session, text, root=root, existing=existing, lang=body.lang, scope=scope
                    )
                except chat_turns.TurnRefused as exc:
                    raise _refused(exc) from exc
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
        attach_out: tuple[dict, dict | None, dict | None, dict] | None = None
        llm_headers: dict[str, str] = {}
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
                    attach_out = await _confirm_attach(
                        session, target, lang, decisions, llm_headers
                    )
            if attach_out is None:
                was = {t["id"] for t in session["turns"] if t.get("state") == "proposed"}
                try:
                    if body is not None and body.only is not None:
                        only = {
                            "relations": [
                                {"from": r.from_, "rel": r.rel, "to": r.to}
                                for r in body.only.relations
                            ],
                            "concepts": list(body.only.concepts),
                        }
                        turn, commit = await chat_turns.confirm_only(
                            session, turn_id, only, client=_client(), lang=lang, root=root,
                            existing=await _terms(), fetch_terms=_terms, at=_now(),
                        )  # fmt: skip
                    else:
                        turn, commit = await chat_turns.confirm(
                            session, turn_id, client=_client(), lang=lang, root=root, at=_now()
                        )
                except chat_turns.TurnRefused as exc:
                    raise _refused(exc) from exc
                except Exception as exc:  # 書き込みの失敗（commit_theme は巻き戻し済み）
                    logger.warning("chat confirm failed", exc_info=True)
                    raise HTTPException(502, f"theme write failed: {exc}") from exc
                saved = _save_after_confirm(session, commit, lang)
        if attach_out is not None:
            return await _finish_attach(session_id, turn_id, lang, llm_headers, attach_out)
        confirmed = [
            t["id"] for t in saved["turns"] if t["id"] in was and t.get("state") == "confirmed"
        ]
        return JSONResponse(
            {"turn": turn, "session": _head(saved), "commit": commit, "confirmed": confirmed}
        )

    async def _confirm_attach(
        session: dict, turn: dict, lang: str, decisions: dict[str, str], llm_headers: dict[str, str]
    ) -> tuple[dict, dict | None, dict | None, dict]:
        """📎 の表の ✓ ＝ 公開の機械の手番まで（呼び手が ``lock`` を持っている）。
        問いの提案は AI の往復が長いので、ロックの外で :func:`_finish_attach` が足す。"""
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
        return turn, commit, followup, saved

    async def _finish_attach(
        session_id: str,
        turn_id: str,
        lang: str,
        llm_headers: dict[str, str],
        out: tuple[dict, dict | None, dict | None, dict],
    ) -> JSONResponse:
        """公開できたら、ロックの外で (1) ローカル原本を取り込み元の家へ送り（ADR
        source-switch-mcp-client.md §7。``add`` の呼び手は Asterism でここだけ）、(2) 問いの
        提案を取り、短いロックで手番に書き足す。送り込みの失敗は注記だけで、公開は
        取り消さない。"""
        turn, commit, followup, saved = out
        if commit:
            sent_notes = await source_push.push_originals_home(
                getattr(app.state, "mcp_registry", None),
                cfg.registry_root,
                commit["dataset_id"],
                lang=lang,
            )
            questions = await chat_attach.suggest_questions(
                app, cfg, commit["dataset_id"], lang=lang, llm_headers=llm_headers,
                token=cfg.api_token,
            )
            if questions or sent_notes:
                async with lock:
                    again = chat_sessions.load_session(root, session_id)
                    mine = next(
                        (t for t in (again or {}).get("turns") or [] if t.get("id") == turn_id),
                        None,
                    )
                    if again is not None and mine is not None:
                        if sent_notes:
                            mine["reply"]["notes"] = [
                                *(mine["reply"].get("notes") or []),
                                *sent_notes,
                            ]
                        if questions:
                            chat_attach.add_questions(mine, questions)
                        saved = chat_sessions.save_session(root, again)
                        turn = mine
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

    def _revert_failed(
        session: dict,
        turn: dict,
        lang: str,
        failed: list[dict[str, str]],
        undone: dict | None = None,
    ) -> JSONResponse:
        """外せないものがあった戻す — 手番は戻さず 409（``undone.failed`` に何が外せなかったか）。
        外せたものは外れたまま。もう一度押せる。"""
        body_undone = undone or {"lines": 0, "terms": 0, "questions": 0, "kept": []}
        body_undone = {**body_undone, "failed": failed}
        return JSONResponse(
            {
                "detail": "削除できないものがありました。もう一度試してください"
                if lang != "en"
                else "Some items could not be deleted. Please try again.",
                "turn": turn,
                "session": _head(session),
                "draft": _draft_card(session, lang),
                "undone": body_undone,
                "reverted": [],
            },
            status_code=409,
        )

    async def _revert_confirmed(session: dict, target: dict, lang: str) -> JSONResponse:
        """確認済みの手番の戻す（呼び手が ``lock`` を持っている）。公開したデータは戻せない（409）。
        機械の手番（名前が一致した関係）は、足した関係を外して次から出さない。テーマの手番は
        ``commit`` の記録から取り消す。"""
        suggestions = (target.get("reply") or {}).get("suggestions")
        if target.get("attachment") or target.get("attach_run"):
            raise HTTPException(409, chat_turns._DATA_NO_REVERT["en" if lang == "en" else "ja"])
        if suggestions:
            failed: list[dict[str, str]] = []
            why = chat_turns._REVERT_FAILED["en" if lang == "en" else "ja"]
            for item in suggestions:
                try:
                    await crosswalk_runtime.remove_alignment(
                        _client(), item["subject"], item["term"], item["relation"]
                    )
                except Exception:
                    logger.warning("chat revert: suggestion line not removed", exc_info=True)
                    failed.append(
                        {"what": f"{item.get('subject')} - {item.get('term')}", "reason": why}
                    )
            if not await chat_attach.revert_suggestion(app, target, token=cfg.api_token):
                failed.append({"what": "dismiss", "reason": why})
            if failed:  # 1 つでも外せなければ手番は戻さない（もう一度押せる）
                return _revert_failed(session, target, lang, failed)
            try:
                await shared_vocab.recompute_wired(_client(), root, at=_now())
            except Exception:
                logger.warning("chat revert: recompute_wired failed", exc_info=True)
            saved = chat_sessions.save_session(root, session)
            return JSONResponse(
                {
                    "turn": target,
                    "session": _head(saved),
                    "draft": _draft_card(saved, lang),
                    "undone": {
                        "lines": len(suggestions), "terms": 0, "questions": 0, "kept": [],
                        "failed": [],
                    },
                    "reverted": [target["id"]],
                }
            )
        try:
            turn, undone, reverted = await chat_turns.revert_confirmed(
                session, target["id"], client=_client(), root=root, at=_now(), lang=lang
            )
        except chat_turns.TurnRefused as exc:
            raise _refused(exc) from exc
        except Exception as exc:  # ストアの書き込みの失敗
            logger.warning("chat revert confirmed failed", exc_info=True)
            raise HTTPException(502, f"revert failed: {exc}") from exc
        if undone.get("failed"):
            return _revert_failed(session, turn, lang, undone["failed"], undone)
        saved = chat_sessions.save_session(root, session)
        return JSONResponse(
            {
                "turn": turn,
                "session": _head(saved),
                "draft": _draft_card(saved, lang),
                "undone": undone,
                "reverted": reverted,
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
                        if not await chat_attach.revert_suggestion(
                            app, target, token=cfg.api_token
                        ):
                            why = chat_turns._REVERT_FAILED["en" if lang == "en" else "ja"]
                            return _revert_failed(
                                session, target, lang, [{"what": "dismiss", "reason": why}]
                            )
                    else:  # 📎 の表: 取り込んだ未公開のデータを消して、返事を戻す
                        dataset_id = (target.get("attachment") or {}).get("dataset_id")
                        if dataset_id and not await chat_attach.discard_dataset(
                            app, cfg, dataset_id, token=cfg.api_token
                        ):
                            target["reply"].setdefault("notes", []).append(
                                chat_turns.note("info", chat_attach.text(lang, "leftover"))
                            )
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
            if target is not None and target.get("state") == "confirmed":
                return await _revert_confirmed(session, target, lang)
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
        homes: dict[str, dict] | None = None,
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
                homes=homes,
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
        _session_or_404(session_id)  # 本体を読む前に、話があるか
        form = await request.form()
        uploads = [
            f for f in [*form.getlist("files"), *form.getlist("files[]")] if hasattr(f, "filename")
        ]
        lang = "en" if str(form.get("lang") or "ja").lower().startswith("en") else "ja"
        if not uploads:
            raise HTTPException(422, "表のファイルがありません")
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
        cap = _upload_cap()
        data: list[tuple[str, bytes]] = []
        total = 0
        for name, f in zip(names, uploads, strict=True):
            blob = await f.read()
            total += len(blob)
            if cap > 0 and total > cap:
                raise HTTPException(413, "ファイルが大きすぎます")
            data.append((name, blob))
        return await _begin_attach(session_id, data, lang, llm_headers)

    def _upload_cap() -> int:
        return int(os.environ.get("ASTERISM_MAX_UPLOAD_BYTES", str(1 << 30)))

    async def _begin_attach(
        session_id: str,
        data: list[tuple[str, bytes]],
        lang: str,
        llm_headers: dict[str, str],
        homes: dict[str, dict] | None = None,
    ) -> JSONResponse:
        """📎 の共通の出口: 人の手番（running）を作り、走者を始める。bytes の出どころ
        （ブラウザ／MCP の取り込み元）はここでは区別しない。"""
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
            job_id = _start_attach_job(
                session_id, turn["id"], data, lang, llm_headers, homes=homes
            )
            turn["job_id"] = job_id
            saved = chat_sessions.save_session(root, session)
        return JSONResponse(
            {"turn": turn, "session": _head(saved), "job_id": job_id}, status_code=201
        )

    @app.post("/api/chat/sessions/{session_id}/attach-from-source", dependencies=deps)
    async def chat_attach_from_source(
        session_id: str, body: AttachFromSourceBody, request: Request
    ) -> JSONResponse:
        """📎 の 2 択の片方 — 取り込み元（登録した MCP サーバ）のファイルを添える
        （ADR source-switch-mcp-client.md §5）。api が ``get`` で bytes を取り、ブラウザから
        の 📎 と同じ検査（表の拡張子・AI の有無・容量）を通して同じ走者に流す。
        家（server・id・name）は staging の ``origins`` → データセットの ``source_origins``
        に残る。単一ユーザーでなければ 404・未有効のサーバは 409（``mcp_client_routes``）。"""
        _session_or_404(session_id)
        registry_ = getattr(app.state, "mcp_registry", None)
        if registry_ is None:
            raise HTTPException(404, "MCP servers are only available in single-user mode")
        ids = [i for i in dict.fromkeys(body.ids) if isinstance(i, str) and i.strip()]
        if not ids:
            raise HTTPException(422, "取り込み元のファイルが選ばれていません")
        if len(ids) > 20:
            raise HTTPException(422, "一度に添えられるのは 20 件までです")
        lang = "en" if body.lang.lower().startswith("en") else "ja"
        llm_headers = chat_attach.llm_headers_of(request.headers)
        if not _ai_ready(llm_headers):
            raise HTTPException(503, "AI が設定されていません")
        cap = _upload_cap()
        data: list[tuple[str, bytes]] = []
        homes: dict[str, dict] = {}
        total = 0
        for file_id in ids:
            got = await registry_.get_source_file(body.server, file_id)
            name = os.path.basename(got.name) or "data"
            if not chat_attach.is_table_name(name):
                raise HTTPException(422, f"「{name}」は表ではありません")
            total += len(got.data)
            if cap > 0 and total > cap:
                raise HTTPException(413, "ファイルが大きすぎます")
            # 同じ名前が 2 回来たら後ろに番号（家は別なので別の行として残す）。
            # 番号付きの名前が既にある名前と衝突しないところまで進める
            if name in homes:
                stem, dot, ext = name.rpartition(".")
                n = 2
                while True:
                    cand = f"{stem or ext}-{n}.{ext}" if dot else f"{name}-{n}"
                    if cand not in homes:
                        name = cand
                        break
                    n += 1
            data.append((name, got.data))
            homes[name] = {"server": body.server, "id": got.id, "name": got.name}
        return await _begin_attach(session_id, data, lang, llm_headers, homes)

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
