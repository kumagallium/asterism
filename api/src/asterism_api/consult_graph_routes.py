"""``register_consult_graph(app, cfg, resolve_llm, write_auth=…)`` — 作業場（左に図・右に会話）の
会話の api（ui-redesign PR 7a・design.md §16）。

- ``POST /api/consult/graph`` — 人の一言に、AI が**差分の提案**（閉じた 7 操作）を返す。
  ``home`` は認証が要らない（``/api/design/consult`` と同じ。LLM の鍵の解決も同じ）。``theme`` /
  ``dataset`` は下書き・データセットの中身を AI に渡すので ``write_auth``（``GET
  /api/themes/drafts/{id}`` と同じ）。サーバが画面の図を組む:
  ``theme`` = 下書きの概念・関係・問い（AI の生は無い）、``home`` = 公開済みの用語と、用語どうしの
  線、``dataset`` = データセットの列の**見出しと型**（値は渡さない）と既にある当てはめ。AI は
  SPARQL・コード・IRI を書かず、データの値も見ない。返し ``{reply, ops[], sentences[],
  proposal_id}``。AI 未設定は 503。
- ``POST /api/consult/graph/apply`` — 人の「そう」。差分を決定論の検査（種別・循環・却下済み）に
  通して当てる（``write_auth``）。``theme`` は下書きに当てて保存し、``home`` は alignment graph に
  ``assert_alignment`` / ``remove_alignment`` で直結（来歴 = draftedBy・batch・reason）、
  ``dataset`` は ``upper.json`` に当てはめを足す。再送は「いまの図に対して何も変わらない」ことで
  止める（``duplicate``）— 過去に受けた ``proposal_id`` の記録には頼らない。
- ``POST /api/consult/graph/dismiss`` — 「✕」。却下を ``shared_vocab.dismiss_fit`` の鍵で保存する
  （``write_auth``）。次の提案・適用で出ない。
- ``POST /api/consult/suggest-questions`` — 「問いを提案してもらう」（ui-redesign PR 10a・design.md
  §18・``write_auth``）。``{screen: "dataset", context: {dataset_id}, lang}`` → データセットの
  列の**見出しと型**（値は渡さない）・名前・既にある問いと用語の名前を AI に渡し、閉じた形の問いを
  3〜5 本返させ、機械（``asterism.question_suggest``）が閉じた形・名前の実在・重複で絞った
  ``{suggestions: [{question, mode, op, shape, kind, property, …}]}`` を返す。**何も保存しない**
  （提案の文・却下は残さない）。人が押したときだけ呼ばれる（公開の処理の中では呼ばない）。
  AI 未設定は 503。

AI の散文（``reply``）は保存しない。会話の保存はテーマの下書き（``draft["chat"]`` = 人の発言と
適用した差分だけ）。home・dataset は保存しない。AI が返した ``ops`` は信用せず、適用の前に
同じ閉じた検査（``validate_graph_diff``）を通す。保存は ``asterism.graph_diff`` /
``asterism.themes``。
"""

# ruff: noqa: RUF001
from __future__ import annotations

import asyncio
import inspect
import logging
import re
import tempfile
import unicodedata
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from asterism import (
    crosswalk_runtime,
    graph_diff,
    paper_sections,
    question_suggest,
    shared_vocab,
    sky_view,
    themes,
)
from asterism.grounding import term_display_name
from asterism.mapping_ir_read import MappingIRView, read_mapping_ir
from asterism_step0.graph_propose import (
    GraphDiffError,
    propose_graph_diff,
    validate_graph_diff,
)
from asterism_step0.question_suggest import propose_questions
from asterism_step0.theme_propose import ThemeDraftError
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from asterism_api import chat_turns, registry
from asterism_api.converse_routes import _llm_coords, _llm_max_tokens, _record_usage
from asterism_api.upper_routes import (
    dump_upper,
    load_upper,
    merge_upper,
    resolve_property_ref,
)

if TYPE_CHECKING:  # pragma: no cover - type-checking only
    from asterism.oxigraph_client import OxigraphClient

    from asterism_api.main import Settings

__all__ = ["register_consult_graph"]

logger = logging.getLogger(__name__)

Screen = Literal["theme", "home", "dataset"]

_MAX_OPS = 30
_MAX_MESSAGES = 12
_MAX_CONTENT = 2000
_PROPOSAL_ID_RE = re.compile(r"^[A-Za-z0-9_.:\-]{1,60}\Z")
#: 当てはめ（``upper.json``）に置ける関係 — 項目の列だけ（種類は図で扱う）
_BIND_TO_UPPER = {"eq": "equivalentProperty", "sub": "subPropertyOf"}
#: 種類が用語に「入る」線（種類 ⊂ 用語・種類 ≡ 用語）の述語
_SUBSUMPTION_RELATIONS = frozenset({"subClassOf", "equivalentClass"})
_MAX_COLUMNS = 200
#: 図の穴（``screen: "term"``）で出す提案の上限
_TERM_HOLE_LIMIT = 3
#: AI に見せる種類（星）の行の上限（選んでいる星が先頭・残りは件数の多い順）
_MAX_KIND_ROWS = 150
_MAX_BATCH = 100
#: 同じ意味を向きなしで結ぶ述語（``a eq b`` があれば ``b eq a`` も同じ線）
_SYMMETRIC = frozenset({"equivalentClass", "equivalentProperty"})


class ConsultMessage(BaseModel):
    role: str
    content: str


class GraphContext(BaseModel):
    draft_id: str | None = None
    dataset_id: str | None = None
    #: home のとき、人が星図で選んでいる種類（星）の IRI。AI には「いま選んでいる」と知らせるだけ
    #: （図の文脈の並びと印）。無くてよい・公開済みの種類でなければ無視する。
    focus_kind: str | None = None
    #: ``suggest-questions`` の ``screen: "term"`` で、問いを提案する用語の鍵（slug）。
    term_slug: str | None = None


class ProposeBody(BaseModel):
    screen: Screen
    context: GraphContext = Field(default_factory=GraphContext)
    #: これまでの会話（最後の 1 件が人の今の一言）。新しい 12 件だけ使う。
    messages: list[ConsultMessage]
    lang: str = "ja"


class ApplyBody(BaseModel):
    screen: Screen
    context: GraphContext = Field(default_factory=GraphContext)
    ops: list[dict[str, Any]] = Field(max_length=_MAX_OPS)
    #: 提案の ID（``POST /api/consult/graph`` の返し）。二重適用を防ぐ鍵
    proposal_id: str
    #: 「まとめて確定」の ID（来歴）。無ければサーバが付ける
    batch: str | None = None
    #: ``"ai"`` = AI の提案を受けた・``"human"`` = 人の直接の編集（来歴の draftedBy）
    drafted_by: Literal["ai", "human"] = "ai"
    #: この提案に至った人の発言（前の適用から今まで・テーマの下書きにだけ残る。AI の返事は不要）
    messages: list[ConsultMessage] = Field(default_factory=list, max_length=_MAX_MESSAGES)

    @field_validator("batch")
    @classmethod
    def _batch_is_one_short_line(cls, v: str | None) -> str | None:
        """来歴に残す文字列 — 1 行・100 字まで・制御文字なし。"""
        if v is None:
            return v
        if len(v) > _MAX_BATCH or any(
            unicodedata.category(c) == "Cc" or c in "\u2028\u2029" for c in v
        ):
            raise ValueError(f"batch は 1 行 {_MAX_BATCH} 字までで、制御文字を含めません")
        return v


class SuggestBody(BaseModel):
    #: ``dataset`` = データ（10a）・``paper`` = 文書型のデータセットの要旨と結論（10b）・
    #: ``term`` = 問いの無い用語（図の穴・10c。``context.term_slug``）
    screen: Literal["dataset", "paper", "term"] = "dataset"
    context: GraphContext = Field(default_factory=GraphContext)
    lang: str = "ja"


class DismissBody(BaseModel):
    screen: Screen
    context: GraphContext = Field(default_factory=GraphContext)
    ops: list[dict[str, Any]] = Field(max_length=_MAX_OPS)


def _lang(lang: str | None) -> str:
    return "en" if (lang or "").lower().startswith("en") else "ja"


def _ai_unset(key: str | None, provider: str, api_base: str | None) -> bool:
    # キーが無い（かつ手元の互換サーバでもない）= AI は未設定。
    return key is None and not (provider != "anthropic" and api_base)


def _concept_row(c: dict) -> dict:
    return {k: c.get(k) for k in ("key", "label_ja", "label_en", "kind", "use")}


def _term_row(t: shared_vocab.SharedTerm) -> dict:
    return {
        "key": t.slug,
        "label_ja": t.label,
        "label_en": t.label_en or "",
        "kind": t.kind,
        "use": f"existing:{t.slug}",
    }


def _column_hint(prop: Any) -> str:
    """列の型のヒント（IR が宣言した型と、設計で付けた意味の名前）。データの値ではない。"""
    kind = prop.datatype or prop.object_type or ("reference" if prop.object_template else "text")
    return f"{kind}, {prop.label}" if prop.label else str(kind)


def _ir_columns(view: MappingIRView) -> list[dict]:
    """IR の項目の列 → ``[{name, map, kind_hint}]``（同じ見出しは 1 つ・subject の列は除く）。"""
    out: list[dict] = []
    seen: set[str] = set()
    for tm in view.maps:
        for prop in tm.properties:
            for col in (prop.column, *prop.columns):
                if col and col not in seen:
                    seen.add(col)
                    out.append({"name": col, "map": tm.name, "kind_hint": _column_hint(prop)})
    return out[:_MAX_COLUMNS]


def _ir_link_items(view: MappingIRView) -> list[dict]:
    """列を持たない項目（``object_template`` だけの線 = 別の種類へつなぐ項目）→ ``[{name,
    kind_hint}]``。名前だけで、値は無い（``join`` の「つなぐ項目」の手がかり）。列と同じ名前や
    重なりは 1 つにする。"""
    cols = {c["name"] for c in _ir_columns(view)}
    out: list[dict] = []
    seen: set[str] = set()
    for tm in view.maps:
        for prop in tm.properties:
            if prop.column or prop.columns or prop.object_template is None:
                continue
            name = prop.label or prop.link_label
            if name and name not in cols and name not in seen:
                seen.add(name)
                out.append({"name": name, "kind_hint": "link to another kind"})
    return out


def _property_subject(view: MappingIRView, column: str) -> str | None:
    """列の見出し → 当てはめの subject ``property:<map>/<列>``（公開のとき述語に解決できる形）。
    列を運ぶ項目が IR に無い・述語に解決できなければ ``None``。"""
    for tm in view.maps:
        for prop in tm.properties:
            if prop.predicate and (prop.column == column or column in prop.columns):
                ref = f"property:{tm.name}/{column}"
                return ref if resolve_property_ref(view, ref) else None
    return None


def register_consult_graph(
    app: FastAPI,
    cfg: Settings,
    resolve_llm: Any,
    *,
    write_auth: Sequence[Any] = (),
) -> None:
    """Register the ``/api/consult/graph*`` routes on ``app`` (called once from ``build_app``)."""
    # 下書きの読み→書きは themes_routes と同じ鍵で 1 つずつ（register_themes が先に置く）
    lock: asyncio.Lock = getattr(app.state, "themes_lock", None) or asyncio.Lock()

    def _client() -> OxigraphClient:
        return app.state.client

    def _now() -> str:
        return datetime.now(UTC).isoformat()

    async def _terms() -> list[shared_vocab.SharedTerm]:
        try:
            return await shared_vocab._read_terms(_client())
        except Exception as exc:
            raise HTTPException(502, f"shared vocab read failed: {exc}") from exc

    def _draft_or_404(ctx: GraphContext) -> dict:
        if not ctx.draft_id:
            raise HTTPException(422, "context.draft_id が要ります")
        draft = themes.load_draft(cfg.registry_root, ctx.draft_id)
        if draft is None:
            raise HTTPException(404, "下書きがありません")
        return draft

    def _dataset_ir(ctx: GraphContext) -> tuple[dict, MappingIRView]:
        if not ctx.dataset_id:
            raise HTTPException(422, "context.dataset_id が要ります")
        record = registry.load_dataset(cfg.registry_root, ctx.dataset_id)
        if record is None:
            raise HTTPException(404, "unknown dataset_id")
        try:
            view = read_mapping_ir((record.get("artifacts") or {}).get("mapping.yaml") or "")
        except ValueError as exc:
            raise HTTPException(422, "データの形（mapping.yaml）が読めません") from exc
        return record, view

    async def _require_write(request: Request) -> None:
        """``write_auth`` と同じ検査（``build_app`` が渡す依存をそのまま呼ぶ）。propose は画面で
        分ける（``home`` は不要・``theme`` / ``dataset`` は要る）ので、依存でなく route の中で通す。
        依存が読む header を、引数の名前（``x_asterism_token`` → ``x-asterism-token``）で渡す。"""
        for dep in write_auth:
            fn = getattr(dep, "dependency", None)
            if fn is None:
                continue
            kwargs = {
                name: request.headers.get(name.replace("_", "-"))
                for name in inspect.signature(fn).parameters
            }
            out = fn(**kwargs)
            if inspect.isawaitable(out):
                await out

    # ---- 図の文脈（サーバが組む・名前だけ） -----------------------------------------------

    async def _kinds() -> list[dict]:
        """公開済みの種類（星）— ``sky_view`` の ``datasets[].kinds[]``（IRI・表示名・件数・所属
        データセット名）。値は渡さない。同じ IRI が複数のデータセットにあれば 1 つにまとめる。
        読めなければ ``[]``（用語だけの会話は止めない）。"""
        try:
            from asterism import sky_view as sky_view_mod

            sky = await sky_view_mod.sky_view(_client(), cfg.registry_root)
        except Exception:
            logger.warning("consult graph: published kinds unavailable", exc_info=True)
            return []
        out: dict[str, dict] = {}
        for ds in sky.get("datasets") or []:
            for k in ds.get("kinds") or []:
                iri = str(k.get("iri") or "")
                if not iri:
                    continue
                key = graph_diff.kind_key(iri)
                row = out.get(key)
                if row is None:
                    out[key] = {
                        "key": key,
                        "iri": iri,
                        "label": str(k.get("label") or iri),
                        "count": int(k.get("count") or 0),
                        "datasets": [str(ds.get("label") or ds.get("id") or "")],
                    }
                else:
                    row["count"] += int(k.get("count") or 0)
                    row["datasets"].append(str(ds.get("label") or ds.get("id") or ""))
        return list(out.values())

    def _kind_row(k: dict, *, focus: bool) -> dict:
        """AI に見せる 1 行（key は短い鍵。IRI は渡さない）。``use`` に「どのデータセットの何件の
        種類か」を書く（同名の種類を見分ける材料）。"""
        where = "、".join(d for d in k["datasets"] if d)[:60]
        note = f'a published KIND (a star) of dataset "{where}", {k["count"]} records'
        if focus:
            note += " — the one the person is looking at now"
        return {
            "key": k["key"],
            "label_ja": k["label"],
            "label_en": "",
            "kind": "class",
            "use": note,
        }

    async def _home_graph(
        terms: list[shared_vocab.SharedTerm],
        kinds: list[dict],
        *,
        focus_iri: str | None = None,
    ) -> dict:
        slug_of = {t.iri: t.slug for t in terms}
        key_of = {k["iri"]: k["key"] for k in kinds}
        try:
            lines = await crosswalk_runtime.list_alignments(_client())
        except Exception as exc:
            raise HTTPException(502, f"alignment read failed: {exc}") from exc

        def end(iri: str) -> str | None:
            return slug_of.get(iri) or key_of.get(iri)

        relations = [
            {
                "from": end(ln["source"]),
                "rel": graph_diff.PRED_TO_REL[ln["relation"]],
                "to": end(ln["target"]),
            }
            for ln in lines
            if ln["relation"] in graph_diff.PRED_TO_REL and end(ln["source"]) and end(ln["target"])
        ]
        # 選んでいる星を先頭に、あとは件数の多い順（行数の上限で切れても、選んだ星は残る）
        ordered = sorted(kinds, key=lambda k: (k["iri"] != focus_iri, -k["count"], k["key"]))
        concepts = [
            _kind_row(k, focus=k["iri"] == focus_iri) for k in ordered[:_MAX_KIND_ROWS]
        ] + [_term_row(t) for t in terms]
        return {"concepts": concepts, "relations": relations, "questions": []}

    def _dataset_graph(record: dict, view: MappingIRView, terms: list) -> dict:
        slug_of = {t.iri: t.slug for t in terms}
        columns = {c["name"] for c in _ir_columns(view)}
        bound = []
        for item in load_upper(record.get("artifacts") or {}):
            subject = item["subject"]
            col = subject.split("/", 1)[-1] if subject.startswith("property:") else ""
            if col in columns and item["term"] in slug_of:
                rel = "eq" if item["relation"].startswith("equivalent") else "sub"
                bound.append({"from": col, "rel": rel, "to": slug_of[item["term"]]})
        return {"concepts": [], "relations": bound, "questions": []}

    # ---- propose ---------------------------------------------------------------------------

    @app.post("/api/consult/graph")
    async def consult_graph(
        body: ProposeBody,
        request: Request,
        x_api_key: str | None = Header(default=None),
        x_llm_provider: str | None = Header(default=None),
        x_llm_model: str | None = Header(default=None),
        x_llm_api_base: str | None = Header(default=None),
        x_llm_max_tokens: str | None = Header(default=None),
    ) -> JSONResponse:
        """人の一言 → 差分の提案 ``{reply, ops[], sentences[], proposal_id}``。何も書かない。

        AI に渡すのは図の名前（概念の key・名前・種別・使い方、関係、問い）・既にある用語と
        標準用語の名前・（dataset のとき）列の見出しと型のヒントだけ。データの値は渡さない。
        AI の答えは閉じた差分だけ（SPARQL・IRI・コードの欄は無く、そう見える文字列は弾く・
        1 回だけ言い直させる）。未設定なら 503。``theme`` / ``dataset`` は ``write_auth``
        （401 / 503）。返す操作は画面が受けられるものだけ（home = 線の追加・外し／dataset =
        列の当てはめ eq・sub／theme = 当てはめ以外）。"""
        if body.screen != "home":
            await _require_write(request)
        turns = [
            {"role": m.role, "content": m.content[:_MAX_CONTENT]}
            for m in body.messages
            if m.content.strip()
        ][-_MAX_MESSAGES:]
        if not turns or turns[-1]["role"] != "user":
            raise HTTPException(400, "messages の最後は人の発言にしてください")
        provider, model, api_base, key = _llm_coords(
            x_api_key, x_llm_provider, x_llm_model, x_llm_api_base, cfg.registry_root
        )
        if _ai_unset(key, provider, api_base):
            raise HTTPException(503, "AI が設定されていません")
        lang = _lang(body.lang)

        terms = await _terms()
        columns: list[dict] | None = None
        if body.screen == "theme":
            draft = _draft_or_404(body.context)
            graph, standards = chat_turns.theme_context(draft)
        elif body.screen == "home":
            kinds = await _kinds()
            graph = await _home_graph(terms, kinds, focus_iri=body.context.focus_kind)
            standards = themes.default_standards([])
        else:
            record, view = _dataset_ir(body.context)
            graph = _dataset_graph(record, view, terms)
            columns = [
                {"name": c["name"], "kind_hint": c["kind_hint"]} for c in _ir_columns(view)
            ]
            standards = themes.default_standards([c["name"] for c in columns])
        existing_terms = [
            {"slug": t.slug, "label_ja": t.label, "label_en": t.label_en or "", "kind": t.kind}
            for t in terms
        ]
        llm = resolve_llm(
            provider, model, api_base, key, max_tokens=_llm_max_tokens(x_llm_max_tokens)
        )
        try:
            diff = await asyncio.to_thread(
                propose_graph_diff,
                llm,
                screen=body.screen,
                graph=graph,
                existing_terms=existing_terms,
                standards=standards,
                columns=columns,
                messages=turns[:-1],
                message=turns[-1]["content"],
                lang=lang,
            )
        except GraphDiffError as exc:
            raise HTTPException(502, f"AI の答えが決まった形になりませんでした: {exc}") from exc
        except Exception as exc:  # LLM unreachable / provider error
            raise HTTPException(502, f"AI failed: {exc}") from exc
        await asyncio.to_thread(
            _record_usage, cfg.registry_root, "consult.graph", provider, llm, model
        )
        sentence_graph = graph
        if body.screen == "dataset":  # 文に出す名前は、列の相手の用語の名前
            sentence_graph = {"concepts": [_term_row(t) for t in terms]}
        return JSONResponse(
            {
                "reply": diff["reply"],
                "ops": diff["ops"],
                "sentences": graph_diff.ops_to_sentences(diff["ops"], sentence_graph, lang),
                "proposal_id": graph_diff.proposal_id(diff["ops"]),
            }
        )

    # ---- suggest questions (10a) -------------------------------------------------------------

    def _existing_questions() -> list[str]:
        """既にある問いの文（置いた問い・知識の問い・メモの問い・途中の下書き）。重ねて出さないために
        AI に見せ、後処理でも突き合わせる。保存はしない。"""
        out: list[str] = []
        tools = shared_vocab.read_registry_raw_tools(cfg.registry_root, shared_vocab.REGISTRY_ID)
        out += [str(t.get("title") or "") for t in tools or [] if t.get("for_terms")]
        for th in (themes.load_themes_file(cfg.registry_root) or {}).get("themes", []):
            out += [
                str(m.get("title") or "")
                for m in th.get("memo_questions") or []
                if isinstance(m, dict)
            ]
            out += [
                str(sel.get("title") or "")
                for sel in (th.get("selections") or {}).values()
                if isinstance(sel, dict) and sel.get("mode") == "knowledge"
            ]
        for d in themes.list_drafts(cfg.registry_root):
            draft = themes.load_draft(cfg.registry_root, d["id"]) or {}
            out += [str(q) for q in draft.get("questions") or []]
        return [q for q in dict.fromkeys(" ".join(q.split()) for q in out) if q]

    def _paper_of(ctx: GraphContext) -> tuple[dict, paper_sections.PaperText]:
        """文書型のデータセットの、取り込み済みの文書から要旨と結論の文を取る（決定論・書かない）。
        ``.xml``（JATS・Word は変換済み）はそのまま、``.pdf`` は取り込みのときに控えた変換結果
        （``<name>.pdf.jats``）から読む。データセットか文書が無ければ 404・文書型でなければ 422。"""
        if not ctx.dataset_id:
            raise HTTPException(422, "context.dataset_id が要ります")
        record = registry.load_dataset(cfg.registry_root, ctx.dataset_id)
        if record is None:
            raise HTTPException(404, "unknown dataset_id")
        if str((record.get("meta") or {}).get("source_kind") or "csv") != "xml":
            raise HTTPException(422, "文書型のデータセットではありません")
        for path in registry.list_source_files(cfg.registry_root, ctx.dataset_id):
            suffix = path.suffix.lower()
            if suffix == ".pdf":
                path = path.with_name(path.name + ".jats")
            elif suffix != ".xml":
                continue
            try:
                found = paper_sections.extract_from_jats(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):  # 読めない文書は飛ばす（最後は 404）
                continue
            if found:
                return record, found
        raise HTTPException(
            404, "要旨と結論を読める文書がありません（PDF は取り込み直すと読めるようになります）"
        )

    async def _term_facts(
        ctx: GraphContext, terms: list[shared_vocab.SharedTerm], lang: str
    ) -> str:
        """用語 1 つの事実（名前・種別・標準用語の一致・周りの関係の文・入っている種類の名前）を
        1 行 1 事実の文にする（決定論・書かない・データの値は無い）。用語が無ければ 404・既に問いが
        あれば 409（図の穴だけに出す）。"""
        slug = (ctx.term_slug or "").strip()
        if not slug:
            raise HTTPException(422, "context.term_slug が要ります")
        term = next((t for t in terms if t.slug == slug), None)
        if term is None:
            raise HTTPException(404, "unknown term_slug")
        if sky_view.questions_of_term(cfg.registry_root, term.iri):  # 星図と同じ数え方
            raise HTTPException(409, "この用語には問いがあります")
        kinds = {k["iri"]: k["label"] for k in await _kinds()}
        try:
            lines = await crosswalk_runtime.list_alignments(
                _client(), registry_root=cfg.registry_root
            )
        except Exception as exc:
            raise HTTPException(502, f"alignment read failed: {exc}") from exc
        en = lang == "en"

        def shown(t: shared_vocab.SharedTerm) -> str:
            return (t.label_en or t.label) if en else t.label

        names: dict[str, str] = {t.iri: shown(t) for t in terms}
        std_ns = shared_vocab.known_namespaces()

        def is_standard(iri: str) -> bool:
            return any(iri.startswith(ns) for ns in std_ns)

        def name(iri: str) -> str:
            if names.get(iri):
                return names[iri]
            if kinds.get(iri):
                return kinds[iri]
            return term_display_name(iri) or re.split(r"[/#]", iri.rstrip("/#"))[-1] or iri

        kind_word = ("kind of thing" if en else "種類") if term.kind == "class" else (
            "property" if en else "項目"
        )
        facts = [f"{'Term' if en else '用語'}: {shown(term)}（{kind_word}）"]
        mine = [ln for ln in lines if term.iri in (ln["source"], ln["target"])]
        std = next(
            (
                ln
                for ln in mine
                if ln["source"] == term.iri
                and ln["relation"] in ("equivalentClass", "equivalentProperty", "hasQuantityKind")
                and is_standard(str(ln["target"]))
            ),
            None,
        )
        if std is not None:
            head = "Standard term" if en else "標準用語の一致"
            facts.append(f"{head}: {name(str(std['target']))}（{std['relation']}）")
        sentences = graph_diff.REL_SENTENCE["en" if en else "ja"]
        seen: set[str] = set()
        for ln in mine:
            rel = graph_diff.PRED_TO_REL.get(str(ln.get("relation")))
            if rel is None:
                continue
            text = sentences[rel].format(s=name(str(ln["source"])), o=name(str(ln["target"])))
            if text not in seen and len(seen) < 20:
                seen.add(text)
                facts.append(text)
        filed = list(
            dict.fromkeys(
                kinds[str(ln["source"])]
                for ln in mine
                if ln["target"] == term.iri
                and ln["relation"] in _SUBSUMPTION_RELATIONS
                and str(ln["source"]) in kinds
            )
        )[:20]
        if filed:
            head = "Kinds filed under it" if en else "入っている種類"
            facts.append(f"{head}: " + "、".join(filed))
        return "\n".join(facts)

    @app.post("/api/consult/suggest-questions", dependencies=list(write_auth))
    async def consult_suggest_questions(
        body: SuggestBody,
        x_api_key: str | None = Header(default=None),
        x_llm_provider: str | None = Header(default=None),
        x_llm_model: str | None = Header(default=None),
        x_llm_api_base: str | None = Header(default=None),
        x_llm_max_tokens: str | None = Header(default=None),
    ) -> JSONResponse:
        """「問いを提案してもらう」→ ``{suggestions[{question, mode, op, shape, kind, property,
        property2, kind2, value, match, target, direction}]}``。何も書かない・保存しない。

        AI に渡すのは、データセットの名前・列の**見出しと型のヒント**・何の行か（種類の名前）・既に
        ある問いの文・用語と標準用語の名前だけ（データの値は渡さない）。``screen: "paper"``（10b）は
        ``context.dataset_id`` の文書の**要旨と結論の文**（合計 2,500 字まで）と既存の名前だけで、
        列と種類は無い（本文全体は渡さない）。``screen: "term"``（10c・図の穴）は
        ``context.term_slug`` の**問いの無い用語**の名前・種別・標準用語の一致・周りの関係の文・
        入っている種類の名前だけ（問いのある用語は 409・用語が無ければ 404）。返す問いはその用語に
        ついてのもの（知識の問いは対象がその用語・データの問いは種類か項目がその用語）だけ・
        多くて 3 本。
        AI の答えは閉じた JSON だけ
        （SPARQL・IRI・コードの欄は無く、そう見える文字列は弾く・1 回だけ言い直させる）。機械が
        閉じた形（7 つのデータの形・3 つの知識の形）に落ちるもの・名前が列や用語に実在するもの・
        既存の問いと重ならないものだけに絞る。未設定なら 503。"""
        provider, model, api_base, key = _llm_coords(
            x_api_key, x_llm_provider, x_llm_model, x_llm_api_base, cfg.registry_root
        )
        if _ai_unset(key, provider, api_base):
            raise HTTPException(503, "AI が設定されていません")
        lang = _lang(body.lang)
        paper_text: str | None = None
        columns: list[dict] | None
        screen = body.screen
        terms: list[shared_vocab.SharedTerm] | None = None
        only_term: shared_vocab.SharedTerm | None = None
        if screen == "dataset" and body.context.dataset_id:
            # 星の札（10a）は ``dataset`` で呼ぶ。文書型には表が無いので、論文として読む
            rec = registry.load_dataset(cfg.registry_root, body.context.dataset_id)
            if rec and str((rec.get("meta") or {}).get("source_kind") or "csv") == "xml":
                screen = "paper"
        if screen == "term":
            # 図の穴 = 問いの無い用語 1 つ。列も種類も無い。渡すのは用語の名前・種別・標準用語の
            # 一致・周りの関係の文・入っている種類の名前だけ（データの値は渡さない）
            terms = await _terms()
            record = {"meta": {"name": ""}}
            paper_text = await _term_facts(body.context, terms, lang)
            only_term = next(t for t in terms if t.slug == (body.context.term_slug or "").strip())
            columns, kind_names, prop_names = None, [], []
            standards = [s["label"] for s in themes.default_standards([])]
        elif screen == "paper":
            # 論文 = 列も種類も無い。渡すのは要旨と結論の文と既存の名前だけ（本文全体は渡さない）
            record, paper = await asyncio.to_thread(_paper_of, body.context)
            paper_text = paper.text
            columns, kind_names, prop_names = None, [], []
            standards = [s["label"] for s in themes.default_standards([])]
        else:
            record, view = _dataset_ir(body.context)
            columns = [{"name": c["name"], "kind_hint": c["kind_hint"]} for c in _ir_columns(view)]
            standards = [s["label"] for s in themes.default_standards([c["name"] for c in columns])]
            # 列の無い項目（別の種類へつなぐ線）も名前だけ渡す — join の「つなぐ項目」になる
            columns = [*columns, *_ir_link_items(view)][:_MAX_COLUMNS]
            kind_names, prop_names = question_suggest.dataset_names(view)
        dataset_name = (record.get("meta") or {}).get("name") or body.context.dataset_id or ""
        if terms is None:
            terms = await _terms()
        existing = _existing_questions()
        llm = resolve_llm(
            provider, model, api_base, key, max_tokens=_llm_max_tokens(x_llm_max_tokens)
        )
        try:
            ai = await asyncio.to_thread(
                propose_questions,
                llm,
                screen=screen,
                columns=columns,
                dataset_label=str(dataset_name),
                existing_questions=existing,
                terms=[
                    {"label": (t.label_en or t.label) if lang == "en" else t.label, "kind": t.kind}
                    for t in terms
                ],
                standards=standards,
                kinds=None if screen in ("paper", "term") else [k for k, _ in kind_names],
                text=paper_text,
                lang=lang,
            )
        except ThemeDraftError as exc:
            raise HTTPException(502, f"AI の答えが決まった形になりませんでした: {exc}") from exc
        except Exception as exc:  # LLM unreachable / provider error
            raise HTTPException(502, f"AI failed: {exc}") from exc
        await asyncio.to_thread(
            _record_usage, cfg.registry_root, "consult.suggest_questions", provider, llm, model
        )
        limit = _TERM_HOLE_LIMIT if only_term is not None else question_suggest.MAX_SUGGESTIONS
        return JSONResponse(
            {
                "suggestions": question_suggest.postprocess_questions(
                    ai,
                    kinds=kind_names,
                    properties=prop_names,
                    terms=terms,
                    existing_questions=existing,
                    lang=lang,
                    # 図の穴は、その用語についての問いだけ・多くて 3 本
                    only_term=only_term,
                    limit=limit,
                )
            }
        )

    # ---- apply -----------------------------------------------------------------------------

    def _check_ops(
        ops: list[dict], known_keys: list[str], *, allow_taken_keys: bool = False
    ) -> list[dict]:
        """適用の前に、提案のときと同じ閉じた検査を通す（クライアントの ``ops`` は信用しない）。
        ``allow_taken_keys`` = 既にある key の ``add_concept``（再送）を弾かず、適用側に
        ``duplicate`` と言わせる。"""
        try:
            return validate_graph_diff(
                {"ops": ops}, known_keys=known_keys, allow_taken_keys=allow_taken_keys
            )["ops"]
        except GraphDiffError as exc:
            raise HTTPException(422, f"差分が決まった形ではありません: {exc}") from exc

    def _effective_id(proposal_id: str, ops: list[dict]) -> str:
        """適用の冪等の鍵。``proposal_id`` が ``ops`` の ID と合えばそのまま、合わなければ（人が
        一部だけ受けた）``<proposal_id>:<ops の ID>`` — 同じ部分の二重適用も止める。"""
        if not _PROPOSAL_ID_RE.match(proposal_id):
            raise HTTPException(422, "proposal_id の形が正しくありません")
        own = graph_diff.proposal_id(ops)
        return proposal_id if own == proposal_id else f"{proposal_id}:{own}"

    @app.post("/api/consult/graph/apply", dependencies=list(write_auth))
    async def consult_graph_apply(body: ApplyBody) -> JSONResponse:
        """人の「そう」— 差分を当てる。検査に通らない操作は**落として**理由を返す（残りは当てる）。
        返し ``{applied, dropped[{index, op, reason, ref}], unlinked[], draft?, duplicate?}``
        （home は ``lines[{action, source, target, relation}]`` も — 書いた・外した線の端の IRI）。

        * ``theme`` — 下書きに当てて保存（``draft`` を返す）。適用した差分と人の発言を
          ``draft["chat"]`` に残す。同じ提案は 2 度当てない（``duplicate: true``）。
        * ``home`` — ``add_relation`` / ``remove``（関係）だけ。alignment graph に来歴つきで書く。
          概念の追加は 422。それ以外の操作は ``unsupported`` で落とす。
        * ``dataset`` — ``bind_column`` だけ。``upper.json`` に当てはめを足す（線になるのは
          公開のとき）。"""
        if body.screen == "theme":
            return await _apply_theme(body)
        if body.screen == "home":
            return await _apply_home(body)
        return await _apply_dataset(body)

    def _all_duplicate(dropped: list[dict]) -> bool:
        return bool(dropped) and all(d["reason"] == "duplicate" for d in dropped)

    async def _apply_theme(body: ApplyBody) -> JSONResponse:
        existing = await _terms()
        async with lock:
            draft = _draft_or_404(body.context)
            keys = [str(c.get("key")) for c in draft.get("concepts") or []]
            ops = _check_ops(body.ops, keys, allow_taken_keys=True)
            pid = _effective_id(body.proposal_id, ops)
            dismissed = shared_vocab.load_dismissed(cfg.registry_root)
            new, report = graph_diff.apply_to_draft(
                draft, ops, existing=existing, dismissed=dismissed, by=body.drafted_by
            )
            if report["applied"] == 0:  # いまの下書きに対して何も変わらない — 保存しない
                out = {**report, "draft": draft}
                if _all_duplicate(report["dropped"]):  # 再送（もう当たっている）
                    out["duplicate"] = True
                return JSONResponse(out)
            at = _now()
            gone = {d["index"] for d in report["dropped"]}
            said = [
                {"role": "user", "content": m.content[:_MAX_CONTENT], "at": at}
                for m in body.messages
                if m.role == "user" and m.content.strip()
            ]
            chat = draft.get("chat") or {}
            new["chat"] = {
                "messages": [*(chat.get("messages") or []), *said],
                "proposals": [
                    *(chat.get("proposals") or []),
                    {  # 適用した操作だけを残す（落とした操作は履歴に入れない）
                        "proposal_id": pid,
                        "ops": [o for i, o in enumerate(ops) if i not in gone],
                        "at": at,
                        "by": body.drafted_by,
                    },
                ],
            }
            try:
                saved = themes.save_draft(cfg.registry_root, {**new, "updated_at": at})
            except themes.ThemeError as exc:
                raise HTTPException(exc.status, str(exc)) from exc
        return JSONResponse({**report, "draft": saved})

    async def _apply_home(body: ApplyBody) -> JSONResponse:
        if any(o.get("op") == "add_concept" for o in body.ops):
            raise HTTPException(422, "知りたいことで作ってください（ホームでは概念を足せません）")
        terms = await _terms()
        kinds = {k["key"]: k for k in await _kinds()}
        ops = _check_ops(body.ops, [*(t.slug for t in terms), *kinds])
        plans, dropped = graph_diff.plan_term_relations(
            ops,
            terms=terms,
            dismissed=shared_vocab.load_dismissed(cfg.registry_root),
            kinds=kinds,
        )
        batch = (body.batch or "").strip() or f"chat-{body.proposal_id}"[:_MAX_BATCH]
        at = _now()
        applied = 0
        written: list[dict] = []  # 書いた線の端（画面が星図を取り直して輪を出す）
        client = _client()
        try:
            lines = await crosswalk_runtime.list_alignments(client)
        except Exception as exc:
            raise HTTPException(502, f"alignment read failed: {exc}") from exc
        have = {(ln["source"], ln["relation"], ln["target"]) for ln in lines}

        def drop(plan: dict, reason: str) -> None:
            op = ops[plan["index"]]
            dropped.append(
                {
                    "index": plan["index"],
                    "op": op["op"],
                    "reason": reason,
                    "ref": graph_diff.op_ref(op),
                }
            )

        def found(plan: dict) -> tuple[str, str, str] | None:
            """既にある同じ線（``eq`` の述語は向きを問わない）。無ければ ``None``。"""
            exact = (plan["source"], plan["relation"], plan["target"])
            if exact in have:
                return exact
            back = (plan["target"], plan["relation"], plan["source"])
            return back if plan["relation"] in _SYMMETRIC and back in have else None

        for plan in plans:
            here = found(plan)
            if plan["action"] == "add" and here is not None:
                drop(plan, "duplicate")  # 来歴（人の draftedBy・batch・reason）を上書きしない
                continue
            if plan["action"] == "remove" and here is None:
                drop(plan, "not_found")
                continue
            try:
                if plan["action"] == "add":
                    await crosswalk_runtime.assert_alignment(
                        client,
                        plan["source"],
                        plan["target"],
                        plan["relation"],
                        at=at,
                        drafted_by=body.drafted_by,
                        batch=batch,
                        reason=plan["reason"] or None,
                    )
                    have.add((plan["source"], plan["relation"], plan["target"]))
                else:
                    assert here is not None
                    await crosswalk_runtime.remove_alignment(client, here[0], here[2], here[1])
                    have.discard(here)
                applied += 1
                written.append(
                    {
                        "action": plan["action"],
                        "source": plan["source"],
                        "target": plan["target"],
                        "relation": plan["relation"],
                    }
                )
            except ValueError as exc:  # 閉じた規則で断られた（循環・種別・まだ作っていない用語 …）
                msg = str(exc)
                reason = "invalid"
                if msg == "cycle":
                    reason = "cycle"
                elif msg.startswith("kind mismatch"):
                    reason = "mixed"
                elif msg == "unminted shared term":
                    reason = "unknown_key"
                drop(plan, reason)
            except Exception as exc:
                logger.warning("consult graph: alignment write failed", exc_info=True)
                raise HTTPException(502, f"alignment write failed: {exc}") from exc
        if applied:
            # 語の「答えるデータセット数」・知識の件数を数え直す（線が変わったので）
            try:
                await shared_vocab.recompute_wired(client, cfg.registry_root)
            except Exception:
                logger.warning("consult graph: recompute_wired failed", exc_info=True)
        dropped.sort(key=lambda d: d["index"])
        out: dict[str, Any] = {
            "applied": applied,
            "dropped": dropped,
            "unlinked": [],
            "lines": written,
        }
        if applied == 0 and _all_duplicate(dropped):
            out["duplicate"] = True
        return JSONResponse(out)

    async def _apply_dataset(body: ApplyBody) -> JSONResponse:
        terms = await _terms()
        by_slug = {t.slug: t for t in terms}
        dismissed = shared_vocab.load_dismissed(cfg.registry_root)
        batch = (body.batch or "").strip() or f"chat-{body.proposal_id}"[:_MAX_BATCH]
        dropped: list[dict] = []
        incoming: list[dict] = []
        # 読み→書きは themes と同じ鍵で 1 つずつ（upper.json の取りこぼしを防ぐ）
        async with lock:
            record, view = _dataset_ir(body.context)
            ops = _check_ops(body.ops, list(by_slug))
            existing = load_upper(record.get("artifacts") or {})
            have = {(e["subject"], e["term"], e["relation"]) for e in existing}

            def drop(i: int, why: str) -> None:
                dropped.append(
                    {
                        "index": i,
                        "op": ops[i]["op"],
                        "reason": why,
                        "ref": graph_diff.op_ref(ops[i]),
                    }
                )

            for i, op in enumerate(ops):
                if op["op"] != "bind_column":
                    drop(i, "unsupported")
                    continue
                relation = _BIND_TO_UPPER.get(op["rel"])
                if relation is None:  # quantity: upper.json は項目どうしの線だけを持つ
                    drop(i, "unsupported")
                    continue
                subject = _property_subject(view, op["column"])
                end_row, std = by_slug.get(op["to"]), None
                if end_row is None:
                    std = themes.std_by_curie(op["to"]) if ":" in op["to"] else None
                if subject is None or (end_row is None and std is None):
                    drop(i, "unknown_key")
                    continue
                kind = end_row.kind if end_row is not None else std.kind  # type: ignore[union-attr]
                if kind != "property":  # 列は項目 — 種類・量の種類には結べない
                    drop(i, "mixed")
                    continue
                term = end_row.iri if end_row is not None else std.iri  # type: ignore[union-attr]
                if shared_vocab.is_dismissed(dismissed, subject, term):
                    drop(i, "dismissed")
                    continue
                if (subject, term, relation) in have:
                    drop(i, "duplicate")
                    continue
                have.add((subject, term, relation))
                incoming.append(
                    {
                        "subject": subject,
                        "term": term,
                        "relation": relation,
                        # 来歴: 会話で受けた線は、公開で線の来歴まで届く
                        "drafted_by": body.drafted_by,
                        "batch": batch,
                        "reason": op.get("reason") or "",
                    }
                )
            if incoming:
                merged = merge_upper(existing, [*existing, *incoming])
                registry.write_artifact(
                    cfg.registry_root,
                    str(body.context.dataset_id),
                    "upper.json",
                    dump_upper(merged),
                )
        out: dict[str, Any] = {"applied": len(incoming), "dropped": dropped, "unlinked": []}
        if not incoming and _all_duplicate(dropped):
            out["duplicate"] = True
        return JSONResponse(out)

    # ---- dismiss ---------------------------------------------------------------------------

    @app.post("/api/consult/graph/dismiss", dependencies=list(write_auth))
    async def consult_graph_dismiss(body: DismissBody) -> JSONResponse:
        """「✕」— 提案を外す。各操作を却下の鍵で保存する（冪等）。返し ``{dismissed: n}``（新しく
        足した件数）。
        theme: 概念（``concept_subject``）と関係（両端の ``concept_end_key`` ＋述語）。home: 関係
        （用語の IRI ＋述語）。dataset: 列の当てはめ（``property:<map>/<列>`` → 用語の IRI）。"""
        terms = await _terms()
        at = _now()
        keys: list[dict] = []
        if body.screen == "theme":
            draft = _draft_or_404(body.context)
            ops = _check_ops(
                body.ops, [str(c.get("key")) for c in draft.get("concepts") or []]
            )
            keys = graph_diff.dismissal_keys(draft, ops, existing=terms)
        elif body.screen == "home":
            kinds = {k["key"]: k for k in await _kinds()}
            ops = _check_ops(body.ops, [*(t.slug for t in terms), *kinds])
            plans, _dropped = graph_diff.plan_term_relations(ops, terms=terms, kinds=kinds)
            keys = [
                {"subject": p["source"], "term": p["target"], "relation": p["relation"]}
                for p in plans
                if p["action"] == "add"
            ]
        else:
            _record, view = _dataset_ir(body.context)
            by_slug = {t.slug: t for t in terms}
            ops = _check_ops(body.ops, list(by_slug))
            for op in ops:
                if op["op"] != "bind_column":
                    continue
                subject = _property_subject(view, op["column"])
                end = by_slug[op["to"]].iri if op["to"] in by_slug else None
                if end is None and ":" in op["to"]:
                    std = themes.std_by_curie(op["to"])
                    end = std.iri if std is not None else None
                if subject and end:
                    keys.append({"subject": subject, "term": end})
        # 先に全件を検査する（途中で断られて一部だけ保存された状態を残さない）。同じ規則
        # （``dismiss_fit``）を、使い捨ての置き場に当てて確かめる。
        with tempfile.TemporaryDirectory() as scratch:
            for k in keys:
                try:
                    shared_vocab.dismiss_fit(
                        scratch, k["subject"], k["term"], at=at, relation=k.get("relation")
                    )
                except ValueError as exc:
                    raise HTTPException(422, str(exc)) from exc
        added = 0
        async with lock:
            for k in keys:
                try:
                    if shared_vocab.dismiss_fit(
                        cfg.registry_root,
                        k["subject"],
                        k["term"],
                        at=at,
                        relation=k.get("relation"),
                    ):
                        added += 1
                except ValueError as exc:
                    raise HTTPException(422, str(exc)) from exc
        return JSONResponse({"dismissed": added})

