"""``register_themes(app, cfg, resolve_llm, write_auth=…)`` — 「知りたいことから作る」の api
（ui-redesign PR 4・design.md §3）。

- ``GET /api/themes`` — テーマごとの問い（答えるデータセット数・実行文）・要る用語・メモの問い・
  続きの下書き・どのテーマにも入っていない問い。最初に呼ばれたとき、``themes.json`` が無ければ
  既存の共有の問いを 1 つのテーマ「はじめの問い」にまとめる（冪等）。
- ``POST /api/themes/draft`` — 問いから AI が用語・関係・数え方の下書きを作る。AI は閉じた JSON
  だけを返し（SPARQL・IRI・コードを書かせない・データの値は渡さない）、機械が標準用語・既にある用語
  との完全一致で上書きして検査する。AI が未設定なら 503（画面は手入力へ）。
- ``POST /api/themes/describe`` — 閉じた選択（数え方 × 種類 × 項目）の実行文（決定論）。
- ``GET`` / ``POST`` / ``PUT`` / ``DELETE /api/themes/drafts…`` — 途中の下書き（閉じても続きから）。
- ``POST /api/themes/{id}/commit`` — 下書きの確定。用語・関係・問い・テーマを**1 操作**で置く
  （先に全部検査・失敗したら何も書かない）。
- ``POST /api/themes/{id}/questions`` — 作った問いをテーマに足す（かんたん ⑥）。
- ``DELETE /api/themes/{id}`` — テーマを外す（用語・線・問いは残る）。
- ``GET /api/themes/{id}/template.csv.zip`` — テーマが要る種類ごとの CSV の型紙（見出し行だけ）。

保存は ``asterism.themes``（ingest）。ここは受け取りと HTTP への写像、AI 呼び出しの座標の解決
だけを持つ。書き込みの認証は呼ぶ側（``build_app``）が渡す ``write_auth``。
"""

from __future__ import annotations

import asyncio
import io
import logging
import zipfile
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from asterism import shared_vocab, themes
from asterism_step0.theme_propose import ThemeDraftError, propose_theme_draft
from fastapi import FastAPI, Header, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from asterism_api import registry
from asterism_api.converse_routes import _llm_coords, _llm_max_tokens, _record_usage

if TYPE_CHECKING:  # pragma: no cover - type-checking only
    from asterism.oxigraph_client import OxigraphClient

    from asterism_api.main import Settings

__all__ = ["register_themes"]

logger = logging.getLogger(__name__)


class KnownTerm(BaseModel):
    slug: str
    label_ja: str = ""
    label_en: str = ""
    kind: str = "class"


class KnownStandard(BaseModel):
    curie: str
    label: str = ""
    kind: str = ""


class DraftBody(BaseModel):
    questions: list[str]
    #: サーバが自分で埋めてもよい（既にある用語・標準用語の名前）。送れば上書きする。
    known_terms: list[KnownTerm] | None = None
    known_standards: list[KnownStandard] | None = None
    lang: str = "ja"


class DraftCreateBody(BaseModel):
    label: str
    questions: list[str] = Field(default_factory=list)
    #: 既にあるテーマの id（「問いを足す」: 確定すると、そのテーマに問いが加わる）
    extend: str | None = None


class DraftSaveBody(BaseModel):
    """作業中の下書き。形はウィザードのもの（枠だけ検査する）。"""

    label: str
    step: int
    questions: list[str] = Field(default_factory=list)
    ai: dict[str, Any] | None = None
    extend: str | None = None
    terms: list[dict[str, Any]] = Field(default_factory=list)
    relations: list[dict[str, Any]] = Field(default_factory=list)
    selections: list[dict[str, Any]] = Field(default_factory=list)


class DescribeBody(BaseModel):
    selections: list[dict[str, Any]]
    lang: str = "ja"


class CommitBody(BaseModel):
    """下書きの最終形。``terms`` / ``relations`` / ``selections`` はウィザードの行（key で結ぶ）"""

    id: str | None = None
    #: 既にあるテーマの id。あれば新しいテーマは作らず、そのテーマに問いを足す。
    extend: str | None = None
    label: str
    lang: str = "ja"
    ai_drafted: bool = False
    questions: list[str] = Field(default_factory=list)
    terms: list[dict[str, Any]] = Field(default_factory=list)
    relations: list[dict[str, Any]] = Field(default_factory=list)
    selections: list[dict[str, Any]] = Field(default_factory=list)


class AddQuestionsBody(BaseModel):
    tool_names: list[str]
    #: tool_name → ``{op, kind, property, value}``（実行文を組むための表示名・任意）
    selections: dict[str, dict[str, Any]] = Field(default_factory=dict)


def _lang(lang: str | None) -> str:
    return "en" if (lang or "").lower().startswith("en") else "ja"


def _selection_of(theme: dict, tool: dict, term_by_iri: dict[str, dict]) -> dict:
    """The selection a question was made with: the one the theme recorded, else the one its
    template implies (a kind's CQ counts; an item's CQ counts the values)."""
    name = str(tool.get("name"))
    recorded = (theme.get("selections") or {}).get(name)
    if isinstance(recorded, dict) and recorded.get("op"):
        return recorded
    for iri in tool.get("for_terms") or []:
        term = term_by_iri.get(str(iri))
        if term is not None:
            return (
                {"op": "count", "kind": term["label"]}
                if term["kind"] == "class"
                else {"op": "values", "property": term["label"]}
            )
    return {}


def register_themes(
    app: FastAPI,
    cfg: Settings,
    resolve_llm: Any,
    *,
    write_auth: Sequence[Any] = (),
) -> None:
    """Register the ``/api/themes/*`` routes on ``app`` (called once from ``build_app``)."""
    lock = asyncio.Lock()  # 置く・足す・外すは 1 つずつ（themes.json は 1 ファイル）

    def _client() -> OxigraphClient:
        return app.state.client

    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def _fail(exc: themes.ThemeError) -> HTTPException:
        return HTTPException(exc.status, str(exc))

    # ---- 一覧 -----------------------------------------------------------------

    @app.get("/api/themes")
    async def themes_list(lang: str = Query(default="ja")) -> JSONResponse:
        lg = _lang(lang)
        async with lock:
            data = themes.ensure_migrated(cfg.registry_root, at=_now())
        try:
            rows = await shared_vocab.list_terms(_client(), cfg.registry_root)
        except Exception as exc:
            logger.warning("themes list failed", exc_info=True)
            raise HTTPException(502, f"shared vocab read failed: {exc}") from exc
        tools = shared_vocab.read_registry_raw_tools(cfg.registry_root, shared_vocab.REGISTRY_ID)
        tool_by_name = {str(t.get("name")): t for t in (tools or [])}
        term_by_iri = {
            r["iri"]: {
                "slug": r["slug"],
                "label": (r.get("label_en") or r["label"]) if lg == "en" else r["label"],
                "kind": r["kind"],
                "answering_datasets": r["answering_datasets"],
                "wired": r["wired"],
            }
            for r in rows
        }
        wired = shared_vocab.load_wired(cfg.registry_root)

        def question(theme: dict, tool: dict) -> dict:
            for_terms = [str(x) for x in (tool.get("for_terms") or [])]
            sel = _selection_of(theme, tool, term_by_iri)
            return {
                "tool_name": str(tool.get("name")),
                "title": str(tool.get("title", tool.get("name"))),
                "answering_datasets": max((wired.get(x, 0) for x in for_terms), default=0),
                "shape": shared_vocab.describe_selection(sel, lg),
                "terms": [term_by_iri[i]["slug"] for i in for_terms if i in term_by_iri],
            }

        total = sum(
            1
            for m in registry.list_datasets(cfg.registry_root)
            if m.get("promoted") and not shared_vocab.is_system_entry(m)
        )
        out: list[dict] = []
        assigned: set[str] = set()
        for theme in data["themes"]:
            qs = [
                question(theme, tool_by_name[n])
                for n in theme.get("questions") or []
                if n in tool_by_name
            ]
            support = [n for n in theme.get("support_questions") or [] if n in tool_by_name]
            assigned.update(q["tool_name"] for q in qs)
            assigned.update(support)
            need: dict[str, dict] = {}
            for n in [*(theme.get("questions") or []), *support]:
                for iri in (tool_by_name.get(n) or {}).get("for_terms") or []:
                    if str(iri) in term_by_iri:
                        t = term_by_iri[str(iri)]
                        need[t["slug"]] = t
            out.append(
                {
                    "id": theme["id"],
                    "label": theme.get("label", theme["id"]),
                    "created_at": theme.get("created_at", ""),
                    "questions": qs,
                    "answerable": sum(1 for q in qs if q["answering_datasets"] > 0),
                    "terms": sorted(need.values(), key=lambda t: t["slug"]),
                    "memo_questions": theme.get("memo_questions") or [],
                }
            )
        unassigned = [
            question({}, t)
            for n, t in sorted(tool_by_name.items())
            if t.get("for_terms") and n not in assigned
        ]
        return JSONResponse(
            {
                "total_datasets": total,
                "themes": out,
                "drafts": themes.list_drafts(cfg.registry_root),
                "unassigned_questions": unassigned,
            }
        )

    # ---- AI の下書き --------------------------------------------------------------

    @app.post("/api/themes/draft", dependencies=list(write_auth))
    async def themes_draft(
        body: DraftBody,
        x_api_key: str | None = Header(default=None),
        x_llm_provider: str | None = Header(default=None),
        x_llm_model: str | None = Header(default=None),
        x_llm_api_base: str | None = Header(default=None),
        x_llm_max_tokens: str | None = Header(default=None),
    ) -> JSONResponse:
        """問いの文から、用語・関係・数え方の下書きを作る。未設定なら 503。

        AI に渡すのは問いの文と、既にある用語・標準用語の**名前**だけ（データの値は渡さない）。
        AI の答えは閉じた JSON だけ（schema に SPARQL・IRI・コードの欄は無く、そう見える文字列は
        弾く）。機械が、標準用語・既にある用語との完全一致で ``use`` を上書きし、種類と項目の混在・
        循環・今の数え方に無いもの（メモの問いへ）を見分ける。"""
        try:
            questions = themes.clean_questions(body.questions)
        except themes.ThemeError as exc:
            raise _fail(exc) from exc
        if not questions:
            raise HTTPException(422, "問いが 1 件も無いため、AI に読ませられません")
        provider, model, api_base, key = _llm_coords(
            x_api_key, x_llm_provider, x_llm_model, x_llm_api_base, cfg.registry_root
        )
        # キーが無い（かつ手元の互換サーバでもない）= AI は未設定。画面は手入力に切り替える。
        if key is None and not (provider != "anthropic" and api_base):
            raise HTTPException(503, "AI が設定されていません。手で書くこともできます")
        try:
            existing = await shared_vocab._read_terms(_client())
        except Exception as exc:
            raise HTTPException(502, f"shared vocab read failed: {exc}") from exc
        known_terms = (
            [t.model_dump() for t in body.known_terms]
            if body.known_terms is not None
            else [
                {"slug": e.slug, "label_ja": e.label, "label_en": e.label_en or "", "kind": e.kind}
                for e in existing
            ]
        )
        known_standards = (
            [s.model_dump() for s in body.known_standards]
            if body.known_standards is not None
            else themes.default_standards(questions)
        )
        llm = resolve_llm(
            provider, model, api_base, key, max_tokens=_llm_max_tokens(x_llm_max_tokens)
        )
        try:
            ai = await asyncio.to_thread(
                propose_theme_draft,
                llm,
                questions=questions,
                known_terms=known_terms,
                known_standards=known_standards,
                language=_lang(body.lang),
            )
        except ThemeDraftError as exc:
            raise HTTPException(502, f"AI の答えが決まった形になりませんでした: {exc}") from exc
        except Exception as exc:  # LLM unreachable / provider error
            raise HTTPException(502, f"AI failed: {exc}") from exc
        await asyncio.to_thread(
            _record_usage, cfg.registry_root, "themes.draft", provider, llm, model
        )
        out = themes.postprocess_draft(ai, questions=questions, existing=existing)
        return JSONResponse({"ai": ai, **out})

    @app.post("/api/themes/describe")
    async def themes_describe(body: DescribeBody) -> JSONResponse:
        """閉じた選択の実行文（機械が組む・決定論）。足りなければ空文字。"""
        lg = _lang(body.lang)
        return JSONResponse(
            {"sentences": [shared_vocab.describe_selection(s, lg) for s in body.selections[:200]]}
        )

    # ---- 下書き（途中で閉じても続きから） --------------------------------------------

    @app.get("/api/themes/drafts/{draft_id}")
    async def draft_get(draft_id: str) -> JSONResponse:
        draft = themes.load_draft(cfg.registry_root, draft_id)
        if draft is None:
            raise HTTPException(404, "下書きがありません")
        return JSONResponse(draft)

    @app.post("/api/themes/drafts", dependencies=list(write_auth))
    async def draft_create(body: DraftCreateBody) -> JSONResponse:
        try:
            async with lock:
                draft = themes.create_draft(
                    cfg.registry_root, body.label, body.questions, at=_now(), extend=body.extend
                )
        except themes.ThemeError as exc:
            raise _fail(exc) from exc
        return JSONResponse(draft, status_code=201)

    @app.put("/api/themes/drafts/{draft_id}", dependencies=list(write_auth))
    async def draft_save(draft_id: str, body: DraftSaveBody) -> JSONResponse:
        if themes.load_draft(cfg.registry_root, draft_id) is None:
            raise HTTPException(404, "下書きがありません")
        try:
            async with lock:
                saved = themes.save_draft(
                    cfg.registry_root, {**body.model_dump(), "id": draft_id, "updated_at": _now()}
                )
        except themes.ThemeError as exc:
            raise _fail(exc) from exc
        return JSONResponse(saved)

    @app.delete("/api/themes/drafts/{draft_id}", dependencies=list(write_auth))
    async def draft_delete(draft_id: str) -> Response:
        async with lock:
            themes.delete_draft(cfg.registry_root, draft_id)
        return Response(status_code=204)

    # ---- 確定・足す・外す ------------------------------------------------------------

    @app.post("/api/themes/{theme_id}/commit", dependencies=list(write_auth))
    async def theme_commit(theme_id: str, body: CommitBody) -> JSONResponse:
        """下書きの最終形を 1 操作で置く。先に全部検査し（失敗は 422／409・何も書かない）、
        書いている途中で失敗したら書いたものを取り消す（鋳造だけ残さない）。"""
        payload = body.model_dump()
        payload["id"] = theme_id
        try:
            async with lock:
                out = await themes.commit_theme(
                    _client(), cfg.registry_root, payload, at=_now()
                )
        except themes.ThemeError as exc:
            raise _fail(exc) from exc
        except Exception as exc:
            logger.warning("theme commit failed", exc_info=True)
            raise HTTPException(502, f"theme write failed: {exc}") from exc
        return JSONResponse(out, status_code=201)

    @app.post("/api/themes/{theme_id}/questions", dependencies=list(write_auth))
    async def theme_add_questions(theme_id: str, body: AddQuestionsBody) -> JSONResponse:
        try:
            async with lock:
                theme = themes.add_questions_to_theme(
                    cfg.registry_root, theme_id, body.tool_names, body.selections
                )
        except themes.ThemeError as exc:
            raise _fail(exc) from exc
        return JSONResponse({"theme": theme})

    @app.delete("/api/themes/{theme_id}", dependencies=list(write_auth))
    async def theme_delete(theme_id: str) -> Response:
        """テーマを外す。用語・線・問いは**残る**（用語はテーマの持ち物ではない）。"""
        try:
            async with lock:
                themes.remove_theme(cfg.registry_root, theme_id)
        except themes.ThemeError as exc:
            raise _fail(exc) from exc
        return Response(status_code=204)

    # ---- 型紙 -----------------------------------------------------------------------

    @app.get("/api/themes/{theme_id}/template.csv.zip")
    async def theme_template(theme_id: str) -> Response:
        data = themes.ensure_migrated(cfg.registry_root, at=_now())
        theme = next((t for t in data["themes"] if t["id"] == theme_id), None)
        if theme is None:
            raise HTTPException(404, "テーマがありません")
        try:
            rows = await shared_vocab.list_terms(_client(), cfg.registry_root)
        except Exception as exc:
            raise HTTPException(502, f"shared vocab read failed: {exc}") from exc
        tools = shared_vocab.read_registry_raw_tools(cfg.registry_root, shared_vocab.REGISTRY_ID)
        files = themes.template_files(theme, rows, tools or [])
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name, header in files.items():
                z.writestr(name, _csv_line(header))
        return Response(
            buf.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{theme_id}-template.zip"'},
        )


def _csv_line(cells: list[str]) -> str:
    def q(c: str) -> str:
        # 表計算が式として評価しないように、= + - @ で始まるセルは ' を付ける
        if c[:1] in ("=", "+", "-", "@"):
            c = "'" + c
        return '"' + c.replace('"', '""') + '"' if any(ch in c for ch in ',"\n') else c

    return ",".join(q(c) for c in cells) + "\r\n"
