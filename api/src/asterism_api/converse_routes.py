"""``register_converse(app, cfg, resolve_llm)`` — 契約メモ contract_pr_f12.md
§1-3 の ``POST /api/cards/converse`` と、appdata namespace ``pagechat`` の
3 ルート（担当 api・§2）。

``cards_routes.py``/``appdata_cards_routes.py`` と同じ規律で、このモジュールが
own の ``@app.get``/``@app.post``/``@app.put``/``@app.delete`` を own で登録し、
``main.py`` には呼び出し 1 行だけを残す。

``resolve_llm`` は ``main.py`` の ``build_app`` が閉じ込めている ``_resolve_llm``
（``design_consult`` と同じ解決経路 — ヘッダ → 設定済みの提供元）をそのまま
関数として受け取る。``main.py`` の中の関数を **import** することはできない
（``cards_routes.py`` の docstring と同じ理由: それは ``build_app`` 内の
closure）が、こちらは呼び出し時に**値として渡してもらう**だけなので、
``asterism_api.main`` を一切 import しなくて済む——``_llm_coords``/
``_llm_max_tokens``/LLM 使用量の記録は、それぞれ数行の純粋なロジックなので、
ここに同じ規則で複製する（コメントで出典を明示）。
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
from typing import TYPE_CHECKING, Any

from asterism import class_schema as class_schema_mod
from asterism import subject_tools
from asterism import subjects as subjects_mod
from asterism.measure_spec import MeasureSpecError
from asterism_step0.llm import as_completion
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

from asterism_api import appdata, server_keys
from asterism_api import usage as usage_ledger
from asterism_api.converse_prompt import (
    build_system_prompt,
    extract_proposal,
    render_retry_message,
    render_user_prompt,
    validate_proposal,
    with_cannot_build_note,
)

if TYPE_CHECKING:  # pragma: no cover - type-checking only, avoids a runtime cycle
    from asterism.oxigraph_client import OxigraphClient
    from asterism_step0.llm import LLMClient

    from asterism_api.main import Settings

logger = logging.getLogger(__name__)

#: appdata.read_threads/write_thread/delete_thread の namespace（契約メモ
#: §1-1: 保存は ``createThreadStore`` の流儀で namespace ``pagechat``）。
PAGECHAT_NAMESPACE = "pagechat"

#: messages + page の合計上限（契約メモ §1-3「合計 64KB」）。超えたら 413 —
#: appdata 側の ``MAX_THREAD_BYTES``（スレッド 1 件の保存上限）とは別の、
#: この呼び出し 1 回ぶんの入力サイズの上限。
_MAX_CONVERSE_BYTES = 64 * 1024

#: 検証に落ちたとき AI に直させる回数（契約メモ §1-3「1 回だけ」）。
_MAX_ATTEMPTS = 2


class ConverseMessage(BaseModel):
    role: str
    content: str


class ConverseBody(BaseModel):
    """Body for POST /api/cards/converse（契約メモ §1-3）。"""

    subject: dict[str, Any]
    messages: list[ConverseMessage] = []
    draft: dict[str, Any] | None = None
    page: dict[str, Any] | None = None
    lang: str = "ja"


def _require_write_auth(cfg: Settings):
    """``cards_routes._require_write_auth``/``appdata_cards_routes._require_write_auth``
    の verbatim コピー（``main.py`` の closure を直接 import できない事情は
    そちらのモジュール docstring と同じ）。"""

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
    """``main.py``/``cards_routes.py``/``appdata_cards_routes.py`` の appdata
    事前チェックと同じ早期 413。"""
    raw = request.headers.get("content-length")
    if raw is None:
        return
    try:
        declared = int(raw)
    except ValueError:
        return
    if declared > limit:
        raise HTTPException(413, f"body is {declared} bytes, over the {limit} limit")


# ----------------------------------------------------------------------------
# LLM 座標の解決（``main.py`` の ``_llm_coords``/``_llm_max_tokens`` の複製 —
# モジュール docstring 参照。ロジックは一字一句同じ意図: ヘッダにキーが無ければ
# 運用者の共有キー（``server_keys``）にフォールバックし、それも無ければ
# ``None`` のまま ``resolve_llm`` に渡して provider 側の認証失敗を 502 として
# 素通しする — design_consult と同じ「キー無しは呼び出し時に自然に失敗する」
# 設計）。
# ----------------------------------------------------------------------------


def _llm_coords(
    x_api_key: str | None,
    x_llm_provider: str | None,
    x_llm_model: str | None,
    x_llm_api_base: str | None,
    registry_root: Any,
) -> tuple[str, str | None, str | None, str | None]:
    provider = (x_llm_provider or "anthropic").strip().lower() or "anthropic"
    if x_api_key:
        return provider, (x_llm_model or None), (x_llm_api_base or None), x_api_key
    key, pinned_base = server_keys.resolve(provider, registry_root)
    api_base = pinned_base or (x_llm_api_base or None)
    return provider, (x_llm_model or None), api_base, key


def _llm_max_tokens(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    try:
        parsed = int(value.strip())
    except ValueError:
        parsed = 0
    if parsed < 1:
        raise HTTPException(400, "X-LLM-Max-Tokens must be a positive integer")
    return parsed


def _record_usage(
    registry_root: Any, feature: str, provider: str, llm: Any, model_hint: str | None
) -> None:
    """``main.py``'s ``_record_llm_usage`` の複製（best-effort・usage の無い
    モック/クライアントは無視 — テストが usage ファイルを書かずに済む）。"""
    usage = getattr(llm, "last_usage", None)
    if usage is None or getattr(usage, "total_tokens", 0) <= 0:
        return
    model_id = getattr(llm, "model", None) or model_hint or provider
    try:
        usage_ledger.record_usage(
            registry_root,
            feature,
            provider,
            str(model_id),
            input_tokens=getattr(usage, "input_tokens", 0),
            output_tokens=getattr(usage, "output_tokens", 0),
            cache_read_tokens=getattr(usage, "cache_read_tokens", 0),
            cache_write_tokens=getattr(usage, "cache_write_tokens", 0),
        )
    except OSError:
        logger.exception("failed to append LLM usage event (continuing)")


# ----------------------------------------------------------------------------
# subject の解決（個体／絞り込み一覧／種類のページ）
# ----------------------------------------------------------------------------


async def _resolve_subject(
    client: OxigraphClient, registry_root: Any, raw_subject: Any
) -> dict[str, Any]:
    """契約メモ §1-3 の 3 つの subject 形を、``validate_proposal`` が読める
    共通の形に正規化する:

    - ``own_class``: このページ自身の種類（絞り込み一覧は ``spec.class``・
      種類のページは ``class_iri``）——個体のページには「自分自身の種類」を
      対象にした測定の作りかたが存在しない（``NewCardForm.tsx`` の
      ``targetClassIri`` も常に ``linking_kinds`` の候補からしか決まらない）
      ので ``None``。
    - ``own_where``: ``own_class`` を選んだときにそのまま使う where
      （絞り込み一覧はいまの条件・種類のページは空＝全件）。個体のページは
      ``None``（＝own_class 側の分岐を使わせない）。
    - ``linking_kinds``: この 1 件を指す種類の一覧（個体のページだけ）。
    - ``individual_iri``: 個体のページの IRI（それ以外は ``None``）。

    ``{"kind": "class", "class_iri": ...}`` は ``asterism.subjects`` にまだ
    無い形（契約メモ §1-3 で新設された、この会話専用の第 3 形）なので、ここで
    直接 ``safe_iri`` を通す（既存の ``class_type_clause`` と同じ「文字安全性
    のみ・スキーム要件は課さない」判定を再利用する）。"""
    if not isinstance(raw_subject, dict):
        raise HTTPException(400, "subject must be an object")
    kind = raw_subject.get("kind")
    if kind == "class":
        class_iri = subjects_mod.safe_iri(raw_subject.get("class_iri"))
        if class_iri is None:
            raise HTTPException(400, "subject.class_iri must be a well-formed IRI")
        return {
            "kind": "class",
            "own_class": class_iri,
            "own_where": [],
            "linking_kinds": [],
            "individual_iri": None,
        }
    try:
        normalized = subjects_mod.validate_subject_key(raw_subject)
    except (subjects_mod.SubjectKeyError, subjects_mod.SetSpecError) as exc:
        raise HTTPException(400, str(exc)) from exc
    if normalized["kind"] == "set":
        spec = normalized["spec"]
        return {
            "kind": "set",
            "own_class": spec["class"],
            "own_where": spec["where"],
            "linking_kinds": [],
            "individual_iri": None,
        }
    iri = normalized["iri"]
    kinds = await subject_tools.linking_kinds(client, iri, registry_root=registry_root)
    return {
        "kind": "individual",
        "own_class": None,
        "own_where": None,
        "linking_kinds": kinds,
        "individual_iri": iri,
    }


async def _class_schema_or_none(
    client: OxigraphClient, registry_root: Any, class_iri: str
) -> dict[str, Any] | None:
    """``cards_routes._class_schema_or_none`` と同じ best-effort ロード（この
    会話は系統プロンプトを組むための下読みなので、単一の class の schema 取得
    失敗が会話全体を落とすことはしない）。"""
    try:
        schema = await class_schema_mod.class_schema(client, registry_root, class_iri)
    except Exception:  # best-effort: プロンプトの材料集めであって検証ではない
        logger.debug("converse: class_schema lookup failed for %s", class_iri, exc_info=True)
        return None
    return schema if isinstance(schema, dict) else None


async def _class_properties_map(
    client: OxigraphClient, registry_root: Any, resolved_subject: dict[str, Any]
) -> dict[str, list[dict[str, Any]]]:
    """観点を作れる候補の種類ぶんの ``class_schema(...)['properties']``
    （``{class_iri: [property, ...]}``）——``own_class`` と、個体のページなら
    ``linking_kinds`` に出てくる種類すべて。schema が引けなかった種類は候補
    から静かに落とす（``validate_proposal`` はこの dict に無い class を
    「妥当性表の外」として弾くので、それだけで安全側に倒れる）。"""
    candidate_classes: set[str] = set()
    own_class = resolved_subject.get("own_class")
    if isinstance(own_class, str):
        candidate_classes.add(own_class)
    for k in resolved_subject.get("linking_kinds") or []:
        cls = k.get("class_iri") if isinstance(k, dict) else None
        if isinstance(cls, str):
            candidate_classes.add(cls)
    out: dict[str, list[dict[str, Any]]] = {}
    for class_iri in sorted(candidate_classes):
        schema = await _class_schema_or_none(client, registry_root, class_iri)
        if schema is None:
            continue
        properties = schema.get("properties")
        if isinstance(properties, list):
            out[class_iri] = properties
    return out


def _existing_titles(page: dict[str, Any] | None) -> list[str]:
    if not isinstance(page, dict):
        return []
    cards = page.get("cards")
    if not isinstance(cards, list):
        return []
    return [c.get("title") for c in cards if isinstance(c, dict) and c.get("title")]


def register_converse(app: FastAPI, cfg: Settings, resolve_llm: Any) -> None:
    """契約メモ contract_pr_f12.md §1-3・§2(api) のルートを登録する。

    ``main.py`` が ``build_app`` の中で 1 回だけ呼ぶ（``cards_routes.register_cards``
    と同じ規律）。``resolve_llm`` は ``design_consult`` が使うのと同じ
    ``_resolve_llm`` closure（``(provider, model, api_base, api_key,
    max_tokens=None) -> LLMClient``）。
    """

    write_auth = [Depends(_require_write_auth(cfg))]

    # ------------------------------------------------------------------
    # POST /api/cards/converse
    # ------------------------------------------------------------------

    @app.post("/api/cards/converse")
    async def cards_converse(
        body: ConverseBody,
        x_api_key: str | None = Header(default=None),
        x_llm_provider: str | None = Header(default=None),
        x_llm_model: str | None = Header(default=None),
        x_llm_api_base: str | None = Header(default=None),
        x_llm_max_tokens: str | None = Header(default=None),
    ) -> dict[str, Any]:
        client: OxigraphClient = app.state.client

        messages = [m.model_dump() for m in body.messages if m.content.strip()]
        if not messages:
            raise HTTPException(400, "messages must contain at least one non-empty turn")
        page = body.page if isinstance(body.page, dict) else None
        # 契約メモ §1-3: messages + page の合計 64KB 上限。
        payload_size = len(
            json.dumps({"messages": messages, "page": page}, ensure_ascii=False).encode("utf-8")
        )
        if payload_size > _MAX_CONVERSE_BYTES:
            raise HTTPException(
                413,
                f"messages + page is {payload_size} bytes, over the {_MAX_CONVERSE_BYTES} limit",
            )
        lang = body.lang if body.lang in ("ja", "en") else "ja"

        resolved_subject = await _resolve_subject(client, cfg.registry_root, body.subject)
        class_properties = await _class_properties_map(client, cfg.registry_root, resolved_subject)
        draft = body.draft if isinstance(body.draft, dict) else None
        system_prompt = build_system_prompt(
            lang,
            class_properties,
            resolved_subject["linking_kinds"],
            _existing_titles(page),
            draft,
        )

        provider, model, api_base, key = _llm_coords(
            x_api_key, x_llm_provider, x_llm_model, x_llm_api_base, cfg.registry_root
        )
        llm: LLMClient = resolve_llm(
            provider, model, api_base, key, max_tokens=_llm_max_tokens(x_llm_max_tokens)
        )

        def call(turns: list[dict[str, str]]) -> str:
            user_message = render_user_prompt(turns, page)
            return as_completion(llm.complete(system_prompt, user_message)).text

        try:
            reply = await asyncio.to_thread(call, messages)
        except Exception as exc:  # LLM unreachable / provider error -> 502
            raise HTTPException(502, f"AI failed: {exc}") from exc
        await asyncio.to_thread(
            _record_usage, cfg.registry_root, "cards.converse", provider, llm, model
        )

        text, raw_proposal = extract_proposal(reply)
        if raw_proposal is None:
            return {"reply": text, "proposal": None}
        try:
            validated = validate_proposal(
                raw_proposal, class_properties, resolved_subject, lang=lang
            )
            return {"reply": text, "proposal": validated}
        except MeasureSpecError as exc:
            reason = str(exc)

        # 契約メモ §1-3: 検証に落ちたら AI に 1 回だけ直させる。
        retry_turns = [
            *messages,
            {"role": "assistant", "content": reply},
            {"role": "user", "content": render_retry_message(reason, lang)},
        ]
        try:
            reply2 = await asyncio.to_thread(call, retry_turns)
        except Exception as exc2:  # LLM unreachable / provider error -> 502
            raise HTTPException(502, f"AI failed: {exc2}") from exc2
        await asyncio.to_thread(
            _record_usage, cfg.registry_root, "cards.converse", provider, llm, model
        )

        text2, raw_proposal2 = extract_proposal(reply2)
        if raw_proposal2 is not None:
            try:
                validated2 = validate_proposal(
                    raw_proposal2, class_properties, resolved_subject, lang=lang
                )
                return {"reply": text2, "proposal": validated2}
            except MeasureSpecError:
                pass
        return {"reply": with_cannot_build_note(text2, lang), "proposal": None}

    # ------------------------------------------------------------------
    # appdata pagechat threads（consult threads と同じ書き方・§1-1）
    # ------------------------------------------------------------------

    def _appdata_root_or_404():
        if not cfg.single_user or cfg.appdata_root is None:
            raise HTTPException(404, "appdata is only available in single-user mode")
        return cfg.appdata_root

    @app.get("/api/appdata/pagechat/threads")
    async def appdata_list_pagechat_threads() -> dict[str, Any]:
        root = _appdata_root_or_404()
        return {"threads": appdata.read_threads(root, namespace=PAGECHAT_NAMESPACE)}

    @app.put("/api/appdata/pagechat/threads/{thread_id}", dependencies=write_auth)
    async def appdata_put_pagechat_thread(thread_id: str, request: Request) -> dict[str, Any]:
        root = _appdata_root_or_404()
        _reject_if_content_length_exceeds(request, appdata.MAX_THREAD_BYTES)
        try:
            payload = await request.json()
        except ValueError as exc:
            raise HTTPException(400, "body must be JSON") from exc
        if not isinstance(payload, dict):
            raise HTTPException(400, "thread body must be a JSON object")
        try:
            appdata.write_thread(root, thread_id, payload, namespace=PAGECHAT_NAMESPACE)
        except appdata.InvalidThreadId as exc:
            raise HTTPException(400, str(exc)) from exc
        except (appdata.ThreadTooLarge, appdata.TooManyThreads) as exc:
            raise HTTPException(413, str(exc)) from exc
        return {"saved": True}

    @app.delete("/api/appdata/pagechat/threads/{thread_id}", dependencies=write_auth)
    async def appdata_delete_pagechat_thread(thread_id: str) -> dict[str, Any]:
        root = _appdata_root_or_404()
        try:
            deleted = appdata.delete_thread(root, thread_id, namespace=PAGECHAT_NAMESPACE)
        except appdata.InvalidThreadId as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"deleted": deleted}
