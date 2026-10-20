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

from asterism import crosswalk_runtime, graph_diff, shared_vocab, themes
from asterism.mapping_ir_read import MappingIRView, read_mapping_ir
from asterism_step0.graph_propose import (
    GraphDiffError,
    propose_graph_diff,
    validate_graph_diff,
)
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from asterism_api import registry
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
#: ホームの用語どうしの線に載せる述語 → 図の関係名（``themes.relation_predicate`` の逆）
_PRED_TO_REL = {
    "subClassOf": "sub",
    "subPropertyOf": "sub",
    "equivalentClass": "eq",
    "equivalentProperty": "eq",
    "hasQuantityKind": "quantity",
    "isPartOf": "part",
    "lowers": "lowers",
    "raises": "raises",
    "affects": "affects",
}
#: 当てはめ（``upper.json``）に置ける関係 — 項目の列だけ（種類は図で扱う）
_BIND_TO_UPPER = {"eq": "equivalentProperty", "sub": "subPropertyOf"}
_MAX_COLUMNS = 200
_MAX_BATCH = 100
#: 同じ意味を向きなしで結ぶ述語（``a eq b`` があれば ``b eq a`` も同じ線）
_SYMMETRIC = frozenset({"equivalentClass", "equivalentProperty"})


class ConsultMessage(BaseModel):
    role: str
    content: str


class GraphContext(BaseModel):
    draft_id: str | None = None
    dataset_id: str | None = None


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

    async def _home_graph(terms: list[shared_vocab.SharedTerm]) -> dict:
        slug_of = {t.iri: t.slug for t in terms}
        try:
            lines = await crosswalk_runtime.list_alignments(_client())
        except Exception as exc:
            raise HTTPException(502, f"alignment read failed: {exc}") from exc
        relations = [
            {
                "from": slug_of[ln["source"]],
                "rel": _PRED_TO_REL[ln["relation"]],
                "to": slug_of[ln["target"]],
            }
            for ln in lines
            if ln["relation"] in _PRED_TO_REL
            and ln["source"] in slug_of
            and ln["target"] in slug_of
        ]
        return {"concepts": [_term_row(t) for t in terms], "relations": relations, "questions": []}

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
            graph = {
                "concepts": [_concept_row(c) for c in draft.get("concepts") or []],
                "relations": [
                    {k: r.get(k) for k in ("from", "rel", "to")}
                    for r in draft.get("relations") or []
                ],
                "questions": [
                    {k: a.get(k) for k in ("question", "mode", "op", "direction", "target")}
                    for a in draft.get("answers") or []
                ]
                or list(draft.get("questions") or []),
            }
            standards = themes.default_standards(list(draft.get("questions") or []))
        elif body.screen == "home":
            graph = await _home_graph(terms)
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
        返し ``{applied, dropped[{index, op, reason, ref}], unlinked[], draft?, duplicate?}``。

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
        ops = _check_ops(body.ops, [t.slug for t in terms])
        plans, dropped = graph_diff.plan_term_relations(
            ops, terms=terms, dismissed=shared_vocab.load_dismissed(cfg.registry_root)
        )
        batch = (body.batch or "").strip() or f"chat-{body.proposal_id}"[:_MAX_BATCH]
        at = _now()
        applied = 0
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
        out: dict[str, Any] = {"applied": applied, "dropped": dropped, "unlinked": []}
        if applied == 0 and _all_duplicate(dropped):
            out["duplicate"] = True
        return JSONResponse(out)

    async def _apply_dataset(body: ApplyBody) -> JSONResponse:
        terms = await _terms()
        by_slug = {t.slug: t for t in terms}
        dismissed = shared_vocab.load_dismissed(cfg.registry_root)
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
                incoming.append({"subject": subject, "term": term, "relation": relation})
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
            ops = _check_ops(body.ops, [t.slug for t in terms])
            plans, _dropped = graph_diff.plan_term_relations(ops, terms=terms)
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

