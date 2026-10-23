"""``register_themes(app, cfg, resolve_llm, write_auth=…)`` — 「知りたいことから作る」の api
（ui-redesign PR 4・PR 6a v2・design.md §15）。

- ``GET /api/themes`` — テーマごとの問い（``mode`` = ``data`` | ``knowledge``・答える
  データセット数・実行文・``wanted_data``）・要る用語・メモの問い・``knowledge_total``・続きの下書き・どのテーマにも
  入っていない問い。最初に呼ばれたとき、``themes.json`` が無ければ既存の共有の問いを 1 つの
  テーマ「はじめの問い」にまとめる（冪等）。
- ``POST /api/themes/draft`` — 問いから AI が**知識の図**（概念・関係・答え方・欲しいデータ）の
  下書きを作る。AI は閉じた JSON だけを返し（SPARQL・IRI・コードを書かせない・データの値は
  渡さない）、機械が標準用語・既にある用語との完全一致・述語ごとの種別・循環・却下済みで
  検査する。AI の生の出力は返さない・保存しない。AI が未設定なら 503（画面は手入力へ）。
- ``POST /api/themes/expand`` — 「ここを広げる」: 下書きの 1 つの概念のまわりを AI に足させ、
  足し分を下書きに合成して保存する（write）。
- ``POST /api/themes/match`` — 手で書いた概念の行を、既にある用語・標準用語と完全一致で照合
  （決定論・読み取り専用・AI を呼ばない）。
- ``POST /api/themes/describe`` — 閉じた選択（数え方 × 種類 × 項目、知識の問い）の実行文（決定論）。
- ``GET`` / ``POST`` / ``PUT`` / ``DELETE /api/themes/drafts…`` — 途中の下書き（閉じても続きから）。
- ``GET /api/themes/drafts/sky`` — 未確定の下書きを星図の札の形で返す（write・データ 0 件でも返す・
  ``GET /api/sky`` は公開済みだけのまま）。
- ``POST /api/themes/{id}/commit`` — 下書きの確定。概念・関係（来歴つき）・問い・テーマを**1 操作**
  で置く（先に全部検査・失敗したら何も書かない）。
- ``GET /api/themes/{id}/answers`` — 問いごとの「知識 N・データ N」（知識は決定論の走者で答える）。
- ``POST /api/themes/{id}/questions`` — 作った問いをテーマに足す（かんたん ⑥）。
- ``DELETE /api/themes/{id}`` — テーマ単位の取り消し: テーマと、ほかが使っていない概念・関係を消す。
- ``GET /api/themes/{id}/template.csv.zip`` — 問いの対象の語から作る CSV の型紙（見出し行だけ）。

保存は ``asterism.themes``（ingest）。ここは受け取りと HTTP への写像、AI 呼び出しの座標の解決
だけを持つ。書き込みの認証は呼ぶ側（``build_app``）が渡す ``write_auth``。
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import zipfile
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from asterism import chat_sessions, knowledge, shared_vocab, themes
from asterism_step0.theme_propose import (
    ThemeDraftError,
    propose_theme_draft,
    propose_theme_expand,
)
from fastapi import FastAPI, Header, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from asterism_api import chat_seed, registry
from asterism_api.converse_routes import _llm_coords, _llm_max_tokens, _record_usage

if TYPE_CHECKING:  # pragma: no cover - type-checking only
    from asterism.oxigraph_client import OxigraphClient

    from asterism_api.main import Settings

__all__ = ["draft_sky_card", "draft_sky_rows", "register_themes"]

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
    #: テーマ名（空なら AI が付ける・AI が付けなければ最初の問いの先頭 20 字）
    label: str = ""
    #: サーバが自分で埋めてもよい（既にある用語・標準用語の名前）。送れば上書きする。
    known_terms: list[KnownTerm] | None = None
    known_standards: list[KnownStandard] | None = None
    lang: str = "ja"


class ExpandBody(BaseModel):
    draft_id: str
    focus_key: str
    known_terms: list[KnownTerm] | None = None
    known_standards: list[KnownStandard] | None = None
    lang: str = "ja"


class DraftCreateBody(BaseModel):
    #: 空でよい（最初の問いの先頭 20 字が名前になる）
    label: str = ""
    questions: list[str] = Field(default_factory=list)
    #: 既にあるテーマの id（「問いを足す」: 確定すると、そのテーマに問いが加わる）
    extend: str | None = None


class DraftSaveBody(BaseModel):
    """作業中の下書き（v2）。形はウィザードのもの（枠だけ検査する）。AI の生の出力の欄は無い
    （送られても読まない）。"""

    label: str = ""
    step: int
    questions: list[str] = Field(default_factory=list)
    extend: str | None = None
    concepts: list[dict[str, Any]] = Field(default_factory=list)
    relations: list[dict[str, Any]] = Field(default_factory=list)
    answers: list[dict[str, Any]] = Field(default_factory=list)
    suggested_questions: list[str] = Field(default_factory=list)
    standards: dict[str, Any] = Field(default_factory=dict)
    #: true なら保存済みの会話を空にする（「もう一度読ませる」: 消えた行についての会話を戻さない）。
    reset_chat: bool = False
    #: まだ「提案（薄い）」のままの行 `{concepts: [key], relations: [[from, rel, to]]}`。
    #: 送らなければ保存済みのものをそのまま残す（空の `{}` 形も「全部本物」という答え）。
    pending: dict[str, Any] | None = None


class DescribeBody(BaseModel):
    selections: list[dict[str, Any]]
    lang: str = "ja"


class CommitBody(BaseModel):
    """下書きの最終形（v2）。``concepts`` / ``relations`` / ``answers`` はウィザードの行
    （key で結ぶ）"""

    id: str | None = None
    #: 既にあるテーマの id。あれば新しいテーマは作らず、そのテーマに問いを足す。
    extend: str | None = None
    #: 空でよい（最初の問いの先頭 20 字）
    label: str = ""
    lang: str = "ja"
    ai_drafted: bool = False
    #: 「まとめて確定」の ID（来歴に残る）。無ければサーバが付ける。
    batch: str | None = None
    questions: list[str] = Field(default_factory=list)
    concepts: list[dict[str, Any]] = Field(default_factory=list)
    relations: list[dict[str, Any]] = Field(default_factory=list)
    answers: list[dict[str, Any]] = Field(default_factory=list)


class MatchTerm(BaseModel):
    key: str | None = None
    label_ja: str = ""
    label_en: str = ""
    aliases: list[str] = Field(default_factory=list)
    kind: str = "class"


class MatchBody(BaseModel):
    terms: list[MatchTerm] = Field(default_factory=list, max_length=200)
    #: 同じ下書きで既に使っている ID（仮置きの ``t<n>`` を重ねないため・任意）
    taken: list[str] = Field(default_factory=list, max_length=400)


class AddQuestionsBody(BaseModel):
    tool_names: list[str]
    #: tool_name → ``{op, kind, property, value, match, property2, kind2}``
    #: （実行文を組むための表示名・任意）
    selections: dict[str, dict[str, Any]] = Field(default_factory=dict)


def _lang(lang: str | None) -> str:
    return "en" if (lang or "").lower().startswith("en") else "ja"


_WIDE_NAME = re.compile(r"^cq_.+_(filter|group|trend|join)(?:_\d+)?\Z")
_FILTER_WORD = re.compile(r'(?:LCASE\(|STR\(\?v\) = )"((?:[^"\\]|\\.)*)"')


def _wide_selection(tool: dict, term_by_iri: dict[str, dict]) -> dict:
    """値で絞る・〜ごと・推移・結合 1 段の問いが、テーマに実行文を記録していないとき
    （別のテーマの問いを足した等）に、宣言ツールそのものから選択を組み直す。名前の末尾が数え方、
    ``for_terms`` は [種類, 項目, (2 つ目の項目 | 2 つ目の種類)] の順（``default_cq_tools``）。"""
    m = _WIDE_NAME.match(str(tool.get("name")))
    if m is None:
        return {}
    iris = [str(i) for i in tool.get("for_terms") or []]
    if len(iris) < 2 or iris[0] not in term_by_iri or iris[1] not in term_by_iri:
        return {}

    def label_at(n: int) -> str | None:  # 位置は for_terms の順（語が無くてもずらさない）
        return term_by_iri[iris[n]]["label"] if len(iris) > n and iris[n] in term_by_iri else None

    op = m.group(1)
    sel: dict[str, str] = {"op": op, "kind": label_at(0) or "", "property": label_at(1) or ""}
    third = label_at(2)
    if op == "trend" and third:
        sel["property2"] = third
    if op == "join" and third:
        sel["kind2"] = third
    if op == "filter":
        w = _FILTER_WORD.search(str(tool.get("query") or ""))
        if w is not None:
            sel["value"] = w.group(1).replace('\\"', '"').replace("\\\\", "\\")
        sel["match"] = "exact" if "STR(?v) = " in str(tool.get("query") or "") else "contains"
    return sel


def _selection_of(theme: dict, tool: dict, term_by_iri: dict[str, dict]) -> dict:
    """The selection a question was made with: the one the theme recorded, else the one its
    template implies (a kind's CQ counts; an item's CQ counts the values; the four wider
    shapes are read back off the tool)."""
    name = str(tool.get("name"))
    recorded = (theme.get("selections") or {}).get(name)
    if isinstance(recorded, dict) and recorded.get("op"):
        return recorded
    wide = _wide_selection(tool, term_by_iri)
    if wide:
        return wide
    for iri in tool.get("for_terms") or []:
        term = term_by_iri.get(str(iri))
        if term is not None:
            return (
                {"op": "count", "kind": term["label"]}
                if term["kind"] == "class"
                else {"op": "values", "property": term["label"]}
            )
    return {}


def draft_sky_card(draft: dict, lang: str = "ja", seed: dict | None = None) -> dict:
    """1 つの下書きを**星図の札の形**にする ``{draft_id, theme_label, concepts[{key, label, kind,
    use}], relations[{from, to, rel}], questions[], removals}``（``drafts_sky`` と話の API が共有
    する）。``removals`` = 確認済みのテーマを種にした下書きで、種にあって下書きに無いもの
    ``{"terms": [IRI], "lines": [{from, to, relation}]}``（種が無ければ空）。"""
    lg = _lang(lang)
    seed_keys, seed_rels = chat_seed.seed_marks(seed, draft)  # 承認済みの種（from_seed）
    return {
        "draft_id": draft["id"],
        "theme_label": draft["label"],
        "concepts": [
            {
                "key": c.get("key"),
                "label": (c.get("label_en") or c.get("label_ja") or "")
                if lg == "en"
                else (c.get("label_ja") or ""),
                "kind": c.get("kind"),
                "use": c.get("use") or "new",
                "from_seed": str(c.get("key")) in seed_keys,
            }
            for c in draft.get("concepts") or []
        ],
        "relations": [
            {
                "from": r.get("from"),
                "to": r.get("to"),
                "rel": r.get("rel"),
                "from_seed": (str(r.get("from")), str(r.get("rel")), str(r.get("to")))
                in seed_rels,
            }
            for r in draft.get("relations") or []
        ],
        "questions": list(draft.get("questions") or []),
        "removals": chat_seed.sky_removals(seed, draft),
    }


def draft_sky_rows(root: Any, lang: str = "ja") -> list[dict]:
    """未確定の下書き全部の札（概念が 1 つも無い下書きは載せない）。``GET /api/themes/drafts/sky``
    と ``GET /api/sky`` の ``proposed`` が共有する。"""
    out: list[dict] = []
    seeds = chat_sessions.draft_seed_map(root)
    for item in themes.list_drafts(root):
        draft = themes.load_draft(root, item["id"])
        if draft is None or not draft.get("concepts"):
            continue
        out.append(draft_sky_card(draft, lang, seeds.get(draft["id"])))
    return out


def register_themes(
    app: FastAPI,
    cfg: Settings,
    resolve_llm: Any,
    *,
    write_auth: Sequence[Any] = (),
) -> None:
    """Register the ``/api/themes/*`` routes on ``app`` (called once from ``build_app``)."""
    lock = asyncio.Lock()  # 置く・足す・外すは 1 つずつ（themes.json は 1 ファイル）
    app.state.themes_lock = lock  # 作業場の会話（consult_graph_routes）が同じ鍵で下書きを書く

    def _client() -> OxigraphClient:
        return app.state.client

    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def _fail(exc: themes.ThemeError) -> HTTPException:
        return HTTPException(exc.status, str(exc))

    # ---- 一覧 -----------------------------------------------------------------

    async def _knowledge_run(sel: dict) -> dict:
        """記録された知識の問い（``selections[kq_…]``）を、決定論の走者で答える。読めなければ
        0 件（一覧を壊さない）。"""
        try:
            spec = {
                "op": sel.get("op"),
                "target": shared_vocab.term_iri(str(sel.get("target_slug"))),
                "direction": sel.get("direction"),
            }
            return await knowledge.knowledge_answer(_client(), spec)
        except Exception:
            logger.warning("knowledge question failed", exc_info=True)
            return {"items": [], "count": 0, "op": sel.get("op"), "target": None,
                    "direction": sel.get("direction")}  # fmt: skip

    def _is_knowledge(theme: dict, name: str) -> bool:
        sel = (theme.get("selections") or {}).get(name)
        return isinstance(sel, dict) and sel.get("mode") == "knowledge"

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
        term_by_slug = {t["slug"]: t for t in term_by_iri.values()}
        wired = shared_vocab.load_wired(cfg.registry_root)

        def question(theme: dict, tool: dict) -> dict:
            for_terms = [str(x) for x in (tool.get("for_terms") or [])]
            sel = _selection_of(theme, tool, term_by_iri)
            name = str(tool.get("name"))
            return {
                "tool_name": name,
                "title": str(tool.get("title", tool.get("name"))),
                "mode": "data",
                "answering_datasets": shared_vocab.answering_of(wired, for_terms),
                "shape": shared_vocab.describe_selection(sel, lg),
                "terms": [term_by_iri[i]["slug"] for i in for_terms if i in term_by_iri],
                "wanted_data": str((theme.get("wanted") or {}).get(name) or ""),
            }

        async def knowledge_question(theme: dict, name: str) -> dict:
            sel = theme["selections"][name]
            target = term_by_slug.get(str(sel.get("target_slug")))
            shown = target["label"] if target else str(sel.get("target") or "")
            run = await _knowledge_run(sel)
            return {
                "tool_name": name,
                "title": str(sel.get("title") or name),
                "mode": "knowledge",
                # the data side of a knowledge question = datasets that answer its target
                "answering_datasets": wired.get(shared_vocab.term_iri(sel["target_slug"]), 0)
                if target
                else 0,
                "knowledge_count": run["count"],
                "shape": shared_vocab.describe_selection(
                    {"op": sel.get("op"), "target": shown, "direction": sel.get("direction")}, lg
                ),
                "terms": [target["slug"]] if target else [],
                "wanted_data": str((theme.get("wanted") or {}).get(name) or ""),
            }

        total = sum(
            1
            for m in registry.list_datasets(cfg.registry_root)
            if m.get("promoted") and not shared_vocab.is_system_entry(m)
        )
        out: list[dict] = []
        assigned: set[str] = set()
        for theme in data["themes"]:
            qs: list[dict] = []
            for n in theme.get("questions") or []:
                if _is_knowledge(theme, n):
                    qs.append(await knowledge_question(theme, n))
                elif n in tool_by_name:
                    qs.append(question(theme, tool_by_name[n]))
            support = [n for n in theme.get("support_questions") or [] if n in tool_by_name]
            assigned.update(q["tool_name"] for q in qs)
            assigned.update(support)
            need: dict[str, dict] = {}
            for n in [*(theme.get("questions") or []), *support]:
                for iri in (tool_by_name.get(n) or {}).get("for_terms") or []:
                    if str(iri) in term_by_iri:
                        t = term_by_iri[str(iri)]
                        need[t["slug"]] = t
            for q in qs:
                for slug in q["terms"] if q["mode"] == "knowledge" else []:
                    if slug in term_by_slug:
                        need[slug] = term_by_slug[slug]
            out.append(
                {
                    "id": theme["id"],
                    "label": theme.get("label", theme["id"]),
                    "created_at": theme.get("created_at", ""),
                    "questions": qs,
                    # 答えられる問い = データ ≥ 1 または 知識 ≥ 1。知識の件数は knowledge_total
                    "answerable": sum(
                        1
                        for q in qs
                        if q["answering_datasets"] > 0 or q.get("knowledge_count", 0) > 0
                    ),
                    "knowledge_total": sum(q.get("knowledge_count", 0) for q in qs),
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

    def _ai_unset(key: str | None, provider: str, api_base: str | None) -> bool:
        # キーが無い（かつ手元の互換サーバでもない）= AI は未設定。画面は手入力に切り替える。
        # copilot-subscription はキー無しで使える（単一ユーザー限定の拒否は _resolve_llm が 400）。
        if provider in ("copilot-subscription", "copilot"):
            return False
        return key is None and not (provider != "anthropic" and api_base)

    async def _known_names(
        questions: list[str],
        known_terms: list[KnownTerm] | None,
        known_standards: list[KnownStandard] | None,
    ) -> tuple[list, list[dict], list[dict]]:
        """AI に渡す名前（既にある用語・標準用語）。送られていればそれを使う。→ ``(existing,
        known_terms, known_standards)``（``existing`` は後処理の照合に使う ``SharedTerm``）。"""
        try:
            existing = await shared_vocab._read_terms(_client())
        except Exception as exc:
            raise HTTPException(502, f"shared vocab read failed: {exc}") from exc
        terms = (
            [t.model_dump() for t in known_terms]
            if known_terms is not None
            else [
                {"slug": e.slug, "label_ja": e.label, "label_en": e.label_en or "", "kind": e.kind}
                for e in existing
            ]
        )
        standards = (
            [s.model_dump() for s in known_standards]
            if known_standards is not None
            else themes.default_standards(questions)
        )
        return existing, terms, standards

    @app.post("/api/themes/draft", dependencies=list(write_auth))
    async def themes_draft(
        body: DraftBody,
        x_api_key: str | None = Header(default=None),
        x_llm_provider: str | None = Header(default=None),
        x_llm_model: str | None = Header(default=None),
        x_llm_api_base: str | None = Header(default=None),
        x_llm_max_tokens: str | None = Header(default=None),
    ) -> JSONResponse:
        """問いの文から、知識の図（概念・関係・答え方・欲しいデータ）の下書きを作る。未設定なら
        503。

        AI に渡すのは問いの文と、既にある用語・標準用語の**名前**だけ（データの値は渡さない）。
        AI の答えは閉じた JSON だけ（schema に SPARQL・IRI・コードの欄は無く、そう見える文字列は
        弾く）。機械が、標準用語・既にある用語との完全一致で ``use`` を上書きし、述語ごとの種別・
        循環・今の数え方に無いもの（メモの問いへ）・却下済みの概念と関係を見分ける。返すのは
        後処理済みの行だけ（AI の生の出力は返さない）。``theme_label`` は ``label``（あれば）→ AI →
        最初の問いの先頭 20 字の順。"""
        try:
            questions = themes.clean_questions(body.questions)
        except themes.ThemeError as exc:
            raise _fail(exc) from exc
        if not questions:
            raise HTTPException(422, "問いが 1 件も無いため、AI に読ませられません")
        provider, model, api_base, key = _llm_coords(
            x_api_key, x_llm_provider, x_llm_model, x_llm_api_base, cfg.registry_root
        )
        if _ai_unset(key, provider, api_base):
            raise HTTPException(503, "AI が設定されていません。手で書くこともできます")
        existing, known_terms, known_standards = await _known_names(
            questions, body.known_terms, body.known_standards
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
                label=body.label,
            )
        except ThemeDraftError as exc:
            raise HTTPException(502, f"AI の答えが決まった形になりませんでした: {exc}") from exc
        except Exception as exc:  # LLM unreachable / provider error
            raise HTTPException(502, f"AI failed: {exc}") from exc
        await asyncio.to_thread(
            _record_usage, cfg.registry_root, "themes.draft", provider, llm, model
        )
        out = themes.postprocess_draft(
            ai,
            questions=questions,
            existing=existing,
            dismissed=shared_vocab.load_dismissed(cfg.registry_root),
        )
        given = " ".join(body.label.split())
        out["theme_label"] = (given or out["theme_label"] or themes.default_label(questions))[:80]
        return JSONResponse(out)

    @app.post("/api/themes/expand", dependencies=list(write_auth))
    async def themes_expand(
        body: ExpandBody,
        x_api_key: str | None = Header(default=None),
        x_llm_provider: str | None = Header(default=None),
        x_llm_model: str | None = Header(default=None),
        x_llm_api_base: str | None = Header(default=None),
        x_llm_max_tokens: str | None = Header(default=None),
    ) -> JSONResponse:
        """「ここを広げる」— 下書きの概念 ``focus_key`` のまわりを AI に足させ、足し分（概念 ≤ 10・
        関係 ≤ 20）を下書きに合成して**保存**し、返す。AI に渡すのは図にある概念の名前と key・問い・
        用語の名前だけ。足し分も同じ後処理（完全一致・種別・循環・却下済み）を通る。未設定なら 503。
        返し: 保存した下書き（``GET /api/themes/drafts/{id}`` と同じ形）＋ ``added``（足した概念と
        関係の行）。"""
        draft = themes.load_draft(cfg.registry_root, body.draft_id)
        if draft is None:
            raise HTTPException(404, "下書きがありません")
        on_map = [c for c in draft.get("concepts") or [] if isinstance(c, dict)]
        if not any(c.get("key") == body.focus_key for c in on_map):
            raise HTTPException(422, "広げる概念が下書きにありません")
        provider, model, api_base, key = _llm_coords(
            x_api_key, x_llm_provider, x_llm_model, x_llm_api_base, cfg.registry_root
        )
        if _ai_unset(key, provider, api_base):
            raise HTTPException(503, "AI が設定されていません。手で足すこともできます")
        questions = list(draft.get("questions") or [])
        existing, known_terms, known_standards = await _known_names(
            questions, body.known_terms, body.known_standards
        )
        llm = resolve_llm(
            provider, model, api_base, key, max_tokens=_llm_max_tokens(x_llm_max_tokens)
        )
        try:
            ai = await asyncio.to_thread(
                propose_theme_expand,
                llm,
                focus_key=body.focus_key,
                concepts=[
                    {k: c.get(k) for k in ("key", "label_ja", "label_en", "kind")} for c in on_map
                ],
                questions=questions,
                existing=known_terms,
                standards=known_standards,
                language=_lang(body.lang),
            )
        except ThemeDraftError as exc:
            raise HTTPException(502, f"AI の答えが決まった形になりませんでした: {exc}") from exc
        except Exception as exc:
            raise HTTPException(502, f"AI failed: {exc}") from exc
        await asyncio.to_thread(
            _record_usage, cfg.registry_root, "themes.expand", provider, llm, model
        )
        try:
            async with lock:
                # AI を待つあいだに人が下書きを直したかもしれない — 最新を読み直して合成する
                fresh = themes.load_draft(cfg.registry_root, body.draft_id)
                if fresh is None:
                    raise HTTPException(404, "下書きがありません")
                added = themes.postprocess_draft(
                    ai,
                    questions=[],
                    existing=existing,
                    dismissed=shared_vocab.load_dismissed(cfg.registry_root),
                    base=fresh,
                )
                merged = themes.merge_expansion(fresh, added)
                saved = themes.save_draft(
                    cfg.registry_root, {**merged, "updated_at": _now()}
                )
        except themes.ThemeError as exc:
            raise _fail(exc) from exc
        return JSONResponse(
            {
                **saved,
                "added": {
                    "concepts": added["concepts"],
                    "relations": added["relations"],
                    "dropped": added["dropped"],
                },
            }
        )

    @app.post("/api/themes/describe")
    async def themes_describe(body: DescribeBody) -> JSONResponse:
        """閉じた選択の実行文（機械が組む・決定論）。足りなければ空文字。"""
        lg = _lang(body.lang)
        return JSONResponse(
            {"sentences": [shared_vocab.describe_selection(s, lg) for s in body.selections[:200]]}
        )

    @app.post("/api/themes/match")
    async def themes_match(body: MatchBody) -> JSONResponse:
        """手で書いた用語の行を、既にある用語・標準用語と**完全一致**で照合する（決定論）。

        読み取り専用・トークン不要・AI は呼ばない。AI の下書きの後処理と同じ照合
        （ja / en / 別名・部分一致は当てない）。``use`` は機械の勧め。"""
        try:
            existing = await shared_vocab._read_terms(_client())
        except Exception as exc:
            raise HTTPException(502, f"shared vocab read failed: {exc}") from exc
        out = await asyncio.to_thread(
            themes.match_rows,
            [t.model_dump() for t in body.terms],
            existing=existing,
            taken=body.taken,
        )
        return JSONResponse({"terms": out})

    # ---- 下書き（途中で閉じても続きから） --------------------------------------------

    @app.get("/api/themes/drafts/sky", dependencies=list(write_auth))
    async def drafts_sky(lang: str = Query(default="ja")) -> JSONResponse:
        """未確定の下書きを**星図の札の形**で返す（枠だけの札＋「下書き」の印を UI が描く）。

        認証つき（write token）の別口 — ``GET /api/sky`` は公開済みの事実だけのまま、下書きは
        確かめるまで Ask / MCP にも星図にも出さない。データ 0 件でも返す（ストアを読まない・
        ``themes/<id>/draft.json`` だけ）。概念が 1 つも無い下書き（まだ①の途中）は札が無いので
        載せない。"""
        out = draft_sky_rows(cfg.registry_root, lang)
        return JSONResponse({"drafts": out})

    @app.get("/api/themes/drafts/{draft_id}", dependencies=list(write_auth))
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
            fields = body.model_dump()
            if body.pending is None:  # 送られなかった — 保存済みの薄い状態を残す
                del fields["pending"]
            async with lock:
                saved = themes.save_draft(
                    cfg.registry_root, {**fields, "id": draft_id, "updated_at": _now()}
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

    @app.get("/api/themes/{theme_id}/answers")
    async def theme_answers(theme_id: str, lang: str = Query(default="ja")) -> JSONResponse:
        """問いごとの答えの数 — 「知識 N・データ N」。

        各問い ``{tool_name, title, mode, knowledge, data, wanted_data}``（読み取り専用）:

        * ``mode`` = ``knowledge``（確定した関係の線から決定論で答える）| ``data``（宣言ツール）;
        * ``knowledge`` = ``{count, items[]}``（知識の問いだけ・データの問いは ``null``）。
          ``items`` は ``asterism.knowledge`` の行 ``{iri, label, relation, via, via_label, reason,
          drafted_by, at, batch}``（誰が・いつ・何をまとめて・なぜ）。公開済みデータセットが
          0 件でも答える;
        * ``data`` = ``{answering, total_datasets}`` — データの問いは答えるデータセット数
          （``wired.json``）、知識の問いはその対象の語に答えるデータセット数。"""
        lg = _lang(lang)
        data = themes.ensure_migrated(cfg.registry_root, at=_now())
        theme = next((t for t in data["themes"] if t["id"] == theme_id), None)
        if theme is None:
            raise HTTPException(404, "テーマがありません")
        try:
            terms = await shared_vocab._read_terms(_client())
        except Exception as exc:
            raise HTTPException(502, f"shared vocab read failed: {exc}") from exc
        by_slug = {t.slug: t for t in terms}
        tools = shared_vocab.read_registry_raw_tools(cfg.registry_root, shared_vocab.REGISTRY_ID)
        tool_by_name = {str(t.get("name")): t for t in (tools or [])}
        wired = shared_vocab.load_wired(cfg.registry_root)
        total = sum(
            1
            for m in registry.list_datasets(cfg.registry_root)
            if m.get("promoted") and not shared_vocab.is_system_entry(m)
        )
        by_iri = {t.iri: t for t in terms}
        wanted = theme.get("wanted") or {}
        out: list[dict] = []
        for name in theme.get("questions") or []:
            if _is_knowledge(theme, name):
                sel = theme["selections"][name]
                target = by_slug.get(str(sel.get("target_slug")))
                run = await _knowledge_run(sel)
                items = run["items"]
                if lg == "en":  # 英語名があれば英語名で（無ければ日本語のまま）
                    for it in items:
                        for field, iri_key in (("label", "iri"), ("via_label", "via")):
                            en = by_iri.get(it[iri_key])
                            if en is not None and en.label_en:
                                it[field] = en.label_en
                shown = (
                    (target.label_en or target.label) if lg == "en" and target else
                    (target.label if target else str(sel.get("target") or ""))
                )
                out.append(
                    {
                        "tool_name": name,
                        "title": str(sel.get("title") or name),
                        "mode": "knowledge",
                        "shape": shared_vocab.describe_selection(
                            {
                                "op": sel.get("op"),
                                "target": shown,
                                "direction": sel.get("direction"),
                            },
                            lg,
                        ),
                        "knowledge": {"count": run["count"], "items": items},
                        "data": {
                            "answering": wired.get(target.iri, 0) if target else 0,
                            "total_datasets": total,
                        },
                        "wanted_data": str(wanted.get(name) or ""),
                    }
                )
            elif name in tool_by_name:
                tool = tool_by_name[name]
                for_terms = [str(x) for x in (tool.get("for_terms") or [])]
                term_by_iri = {
                    t.iri: {"label": (t.label_en or t.label) if lg == "en" else t.label,
                            "kind": t.kind}
                    for t in terms
                }  # fmt: skip
                out.append(
                    {
                        "tool_name": name,
                        "title": str(tool.get("title", name)),
                        "mode": "data",
                        "shape": shared_vocab.describe_selection(
                            _selection_of(theme, tool, term_by_iri), lg
                        ),
                        "knowledge": None,
                        "data": {
                            "answering": shared_vocab.answering_of(wired, for_terms),
                            "total_datasets": total,
                        },
                        "wanted_data": str(wanted.get(name) or ""),
                    }
                )
        return JSONResponse({"theme_id": theme_id, "answers": out})

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
    async def theme_delete(theme_id: str) -> JSONResponse:
        """テーマ単位の取り消し。テーマと、**ほかのテーマの問いにもデータの線にも使われていない**
        概念・関係を消す。返し: ``{removed_concepts, kept_concepts[{slug, label, reason}],
        removed_relations, kept_relations}``（``reason`` = ``other_theme`` / ``other_question``
        / ``has_lines``）。"""
        try:
            async with lock:
                result = await themes.remove_theme_cascade(_client(), cfg.registry_root, theme_id)
        except themes.ThemeError as exc:
            raise _fail(exc) from exc
        except Exception as exc:
            logger.warning("theme removal failed", exc_info=True)
            raise HTTPException(502, f"theme removal failed: {exc}") from exc
        return JSONResponse(result)

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
