"""「取り込み」の裏側: 写す → 関係を選ぶ を 1 本のジョブに・入れた物の一覧・外す（PR 14d-2a）。

* ``POST   /api/knowledge-imports`` — 束を本文で渡して取り込む（スクリプト・試験）。
  ``202 {job_id}``。
* ``POST   /api/mcp-servers/{name}/knowledge-import`` — 登録した MCP サーバから束を取って取り込む
  （単一ユーザーモード限定）。``202 {job_id}``。
* ``GET    /api/knowledge-imports`` — 入れた物の一覧 ``{sources: [...]}``。
* ``DELETE /api/knowledge-imports/{source}`` — 外す（写しを取り下げ、台帳の未確認を消す）。

ジョブの段は ``fetch``（MCP の道のみ）→ ``copy`` → ``pairs`` → ``done``。写す核
（:func:`asterism_api.knowledge_copy.copy_knowledge`）と関係を選ぶ核
（:func:`asterism_api.knowledge_relations.run_relations`）はそのまま呼ぶ。AI を呼ぶのは ``pairs`` の
段だけで、AI が未設定・``relations: false`` のときは AI を呼ばずに台帳を仕上げるだけ。
「答えられる問い」は台帳から機械で数える（:func:`asterism.pair_relations.questions`・AI なし）。

同じ取り込み元で、写す・関係を選ぶ・取り込み・外す は同時に 1 つだけ
（:func:`asterism_api.knowledge_copy.busy_kind`）。出す側（MCP）からは import しない。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from asterism import knowledge_bundle as kb
from asterism import ledger_answer
from asterism import pair_relations as pr
from fastapi import FastAPI, HTTPException, Request

from asterism_api import chat_attach, registry
from asterism_api.chat_routes import _ai_unset
from asterism_api.converse_routes import _llm_coords, _llm_max_tokens, _record_usage
from asterism_api.knowledge_copy import (
    _GONE,
    _SOURCE_NAME,
    KNOWLEDGE_TIMEOUT_S,
    MAX_BUNDLE_BYTES,
    busy_kind,
    copy_is_current,
    copy_knowledge,
    find_copy_dataset,
)
from asterism_api.knowledge_relations import (
    _FEATURE,
    _SERIAL_PROVIDERS,
    CONCURRENCY,
    run_relations,
)
from asterism_api.mcp_client_routes import _call_or_http_error, _reject_if_content_length_exceeds

logger = logging.getLogger(__name__)

__all__ = ["list_imports", "register_knowledge_import_routes", "run_import"]

#: 結果の 1 枚に載せる「答えられる問い」の最大件数。
QUESTIONS_LIMIT = 20
_COUNT_FILES = (
    ("claims", "claims.json"),
    ("topics", "topics.json"),
    ("sources", "sources.json"),
    ("terms", "terms.json"),
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _busy_message(source: str, kind: str) -> str:
    return f"{source} は別の作業 ({kind}) が走っている途中です"


def _hold(app: FastAPI, source: str, kind: str) -> None:
    """この取り込み元を ``kind``（``import``／``remove``）で押さえる。走っている作業があれば 409。
    印は await を挟まずに付ける（2 つ目は必ず 409 になる）。"""
    other = busy_kind(app, source)
    if other is not None:
        raise HTTPException(409, _busy_message(source, other))
    held: dict[str, str] | None = getattr(app.state, "knowledge_busy", None)
    if held is None:
        held = {}
        app.state.knowledge_busy = held
    held[source] = kind


def _release(app: FastAPI, source: str, kind: str) -> None:
    held: dict[str, str] = getattr(app.state, "knowledge_busy", None) or {}
    if held.get(source) == kind:
        del held[source]


def _no_llm() -> Any:  # pragma: no cover - ask=False では呼ばれない
    raise RuntimeError("AI は使いません")


def _ledger_or_empty(root: Path, source: str) -> dict:
    try:
        return pr.load_ledger(root, source)
    except pr.LedgerError:
        return pr.empty_ledger(source)


# ---- ジョブ ----


async def _questions(
    root: Path, source: str, dataset_id: str, lang: str
) -> tuple[list[dict], int]:
    """台帳から答えられる問いを数える（AI なし）。数えられなければ空（取り込みは成功のまま）。"""
    try:
        src = registry.source_dir(root, dataset_id)
        if src is None:
            return [], 0
        copy = await asyncio.to_thread(kb.read_copy, src)
        ledger = pr.load_ledger(root, source)
        # 名前（``lq_…``）つきで返す（押したら AI なしでチャットが答える・14e-2）。数と並びは
        # ``pr.questions`` と同じ（件数の多い順 → 文）
        items = ledger_answer.ledger_questions(ledger, copy, lang=lang)
        return items[:QUESTIONS_LIMIT], len(items)
    except Exception:
        logger.warning("knowledge import: could not count questions", exc_info=True)
        return [], 0


def _copied_card(copied: dict[str, Any]) -> dict[str, Any]:
    counts = copied.get("counts") or {}
    out: dict[str, Any] = {k: int(counts.get(f) or 0) for k, f in _COUNT_FILES}
    out["dropped"] = dict(copied.get("dropped") or {})
    return out


def _cancelled_card(source: str) -> dict[str, Any]:
    """写す前に取り消されたときの結果の 1 枚（何も作っていない）。"""
    return {
        "source": source,
        "title": None,
        "dataset_id": None,
        "created": False,
        "copied": {"claims": 0, "topics": 0, "sources": 0, "terms": 0, "dropped": {}},
        "relations": {
            "shown": 0, "terms": 0, "candidates": 0, "tentative": 0, "by_rel": {}, "dropped": {},
            "failed": 0, "asked": 0, "reused": 0, "skipped": None, "cancelled": True,
            "aborted": False,
        },
        "questions": [],
        "questions_total": 0,
    }  # fmt: skip


async def run_import(
    app: FastAPI,
    root: Path,
    *,
    source: str,
    raw_bundle: Any = None,
    fetch: Callable[[], Awaitable[Any]] | None = None,
    token: str | None,
    lang: str = "ja",
    relations: bool = True,
    make_llm: Callable[[], Any] | None = None,
    on_usage: Callable[[Any], None] | None = None,
    concurrency: int = CONCURRENCY,
    emit: Callable[..., None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """写す → 関係を選ぶ を続けて走らせ、結果の 1 枚を返す。

    ``fetch`` があれば先に呼んで束を得る（MCP の道）。``copy`` の失敗（0 件・壊れた束・検査落ち）は
    そのまま例外（何も変えない＝``copy_knowledge`` の約束）。``pairs`` の失敗・取り消し・打ち切りは
    例外にせず、結果の ``relations`` に ``cancelled``／``aborted``／``skipped`` を入れて終える
    （写しは出来ている）。``make_llm`` が None（AI 未設定）か ``relations=False`` なら
    AI を呼ばない。
    呼び手が ``busy_kind`` の印 ``import`` を付けてから呼ぶ。"""
    emit = emit or (lambda **_: None)
    cancelled_now = should_cancel or (lambda: False)
    if cancelled_now():  # 何も始める前の取り消し: 写しを作らずに終える
        return _cancelled_card(source)
    if fetch is not None:
        emit(stage="fetch", message="取り込み元から受け取っています")
        raw_bundle = await fetch()
        if cancelled_now():  # 受け取りの後・写しの前（写しが始まれば公開まで走り切る）
            return _cancelled_card(source)
    emit(stage="copy", message="写しを作っています")
    copied = await copy_knowledge(
        app, root, source=source, raw_bundle=raw_bundle, token=token, lang=lang, held_by="import"
    )
    dataset_id = copied["dataset_id"]

    skipped: str | None = None if relations else "off"
    if skipped is None and make_llm is None:
        skipped = "ai_unset"
    emit(stage="pairs", message="関係を選んでいます" if skipped is None else "関係を仕上げています")
    run: dict[str, Any] = {}
    error: str | None = None
    try:
        run = await run_relations(
            root,
            source,
            dataset_id,
            make_llm=make_llm or _no_llm,
            on_usage=on_usage,
            lang=lang,
            emit=emit,
            should_cancel=should_cancel,
            concurrency=concurrency,
            ask=skipped is None,
        )
    except Exception as exc:  # 写しは出来ている。関係の段の失敗ではジョブを落とさない
        logger.warning("knowledge import: relations stage failed", exc_info=True)
        error = f"{type(exc).__name__}: {exc}"[:300]
        skipped = skipped or "error"

    summary = run.get("summary")
    if summary is None:
        summary = pr.summarize(_ledger_or_empty(root, source))
    rel_card: dict[str, Any] = {
        "shown": summary["shown"],
        "terms": summary["terms"],
        "candidates": summary["candidates"],
        "tentative": summary["tentative"],
        "by_rel": summary["by_rel"],
        "dropped": summary["dropped"],
        "failed": run.get("failed", 0),
        "asked": run.get("asked", 0),
        "reused": run.get("reused", 0),
        "skipped": skipped,
        "cancelled": bool(run.get("cancelled", False)),
        "aborted": bool(run.get("aborted", False)),
    }
    if run.get("abort_reason"):
        rel_card["abort_reason"] = run["abort_reason"]
    if error:
        rel_card["error"] = error

    emit(stage="done", message="まとめています")
    questions, total = await _questions(root, source, dataset_id, lang)
    meta = registry.load_meta(root, dataset_id) or {}
    ks = meta.get("knowledge_source") or {}
    return {
        "source": source,
        "title": ks.get("title"),
        "dataset_id": dataset_id,
        "created": copied["created"],
        "copied": _copied_card(copied),
        "relations": rel_card,
        "questions": questions,
        "questions_total": total,
    }


# ---- 一覧 ----


def list_imports(app: FastAPI, root: Path) -> list[dict[str, Any]]:
    """入れた物の一覧（``meta.knowledge_source`` を持ち、引用対象から外していないデータセット）。
    取り込み元の名前の順。台帳が無い・読めなければ ``relations`` は 0。"""
    seen: dict[str, dict[str, Any]] = {}
    for meta in registry.list_datasets(root):
        ks = meta.get("knowledge_source")
        if not isinstance(ks, dict) or not isinstance(ks.get("name"), str):
            continue
        if meta.get("status") in _GONE or ks["name"] in seen:
            continue
        seen[ks["name"]] = meta
    out: list[dict[str, Any]] = []
    for source in sorted(seen):
        meta = seen[source]
        ks = meta["knowledge_source"]
        raw = ks.get("counts") or {}
        ledger = _ledger_or_empty(root, source)
        s, c = pr.summarize(ledger), pr.counts_by_state(ledger)
        out.append(
            {
                "source": source,
                "title": ks.get("title"),
                "dataset_id": meta.get("id"),
                "copied_at": ks.get("copied_at"),
                "exported_at": ks.get("exported_at"),
                "counts": {k: int(raw.get(f) or 0) for k, f in _COUNT_FILES},
                "current": copy_is_current(meta),
                "relations": {
                    "shown": s["shown"],
                    "terms": s["terms"],
                    "candidates": s["candidates"],
                    "unconfirmed": c["unconfirmed"],
                    "confirmed": c["confirmed"],
                },
                "running": busy_kind(app, source),
            }
        )
    return out


# ---- 口 ----


def _json_object(raw: bytes, *, required: bool) -> dict[str, Any]:
    if not raw.strip():
        if required:
            raise HTTPException(400, "body は {source, bundle} の JSON オブジェクトです")
        return {}
    try:
        body = json.loads(raw)
    except ValueError as exc:
        raise HTTPException(400, "body must be JSON") from exc
    if not isinstance(body, dict):
        raise HTTPException(400, "body は JSON オブジェクトです")
    return body


def _options(body: dict[str, Any]) -> tuple[str, bool]:
    relations = body.get("relations", True)
    if not isinstance(relations, bool):
        raise HTTPException(400, "relations は true / false です")
    return ("en" if body.get("lang") == "en" else "ja"), relations


def register_knowledge_import_routes(
    app: FastAPI,
    cfg: Any,
    resolve_llm: Any,
    *,
    write_auth: Sequence[Any] = (),
) -> None:
    """取り込みの口 4 つを登録する（``register_knowledge_relations_routes`` の後）。"""
    deps = list(write_auth)
    root: Path = cfg.registry_root

    def _ai_of(request: Request, relations: bool) -> tuple[Callable[[], Any] | None, Any, bool]:
        """「作る AI」を X-LLM-* から組む → ``(make_llm, on_usage, serial)``。``relations: false``・
        AI が未設定（鍵が無い）のときだけ ``make_llm`` が None（AI を呼ばずに仕上げる）。組めない
        ときは例外（``HTTPException``）で、ジョブを始めない。"""
        if not relations:
            return None, None, False
        headers = chat_attach.llm_headers_of(request.headers)
        provider, model, api_base, key = _llm_coords(
            headers.get("x-api-key"),
            headers.get("x-llm-provider"),
            headers.get("x-llm-model"),
            headers.get("x-llm-api-base"),
            root,
        )
        if _ai_unset(key, provider, api_base):
            return None, None, False
        max_tokens = _llm_max_tokens(headers.get("x-llm-max-tokens"))

        def make_llm() -> Any:
            return resolve_llm(provider, model, api_base, key, max_tokens=max_tokens)

        try:
            make_llm()  # 組めるかを先に確かめる（走り出してから落ちない）
        except HTTPException:
            raise
        except Exception as exc:  # ``…/relations`` の口と同じ status・文言
            logger.warning("knowledge import: no AI client", exc_info=True)
            raise HTTPException(409, "AI を組めませんでした") from exc

        def on_usage(llm: Any) -> None:
            _record_usage(root, _FEATURE, provider, llm, model)

        return make_llm, on_usage, provider.strip().lower() in _SERIAL_PROVIDERS

    def _start(
        request: Request,
        *,
        source: str,
        raw_bundle: Any,
        fetch: Callable[[], Awaitable[Any]] | None,
        lang: str,
        relations: bool,
    ) -> dict[str, Any]:
        if not _SOURCE_NAME.fullmatch(source):
            raise HTTPException(400, "source は英数字と _ . - の名前です (64 文字まで)")
        make_llm, on_usage, serial = _ai_of(request, relations)
        _hold(app, source, "import")

        async def work(emit: Callable[..., None], should_cancel: Callable[[], bool]) -> Any:
            try:
                return await run_import(
                    app,
                    root,
                    source=source,
                    raw_bundle=raw_bundle,
                    fetch=fetch,
                    token=cfg.api_token,
                    lang=lang,
                    relations=relations,
                    make_llm=make_llm,
                    on_usage=on_usage,
                    concurrency=1 if serial else CONCURRENCY,
                    emit=emit,
                    should_cancel=should_cancel,
                )
            finally:
                _release(app, source, "import")

        try:
            job_id = app.state.jobs.start_coro(work)
        except Exception:
            _release(app, source, "import")
            raise
        return {"job_id": job_id}

    @app.post("/api/knowledge-imports", status_code=202, dependencies=deps)
    async def knowledge_imports_create(request: Request) -> dict[str, Any]:
        """束を本文で受けて取り込む。本文 ``{source, bundle, lang?, relations?}``。"""
        _reject_if_content_length_exceeds(request, MAX_BUNDLE_BYTES)
        raw = await request.body()
        if len(raw) > MAX_BUNDLE_BYTES:  # Content-Length の無い（chunked の）本文
            raise HTTPException(413, "知識の束が大きすぎます")
        body = _json_object(raw, required=True)
        if not isinstance(body.get("source"), str):
            raise HTTPException(400, "body は {source, bundle} の JSON オブジェクトです")
        lang, relations = _options(body)
        return _start(
            request,
            source=body["source"].strip(),
            raw_bundle=body.get("bundle"),
            fetch=None,
            lang=lang,
            relations=relations,
        )

    @app.post("/api/mcp-servers/{name}/knowledge-import", status_code=202, dependencies=deps)
    async def mcp_servers_knowledge_import(name: str, request: Request) -> dict[str, Any]:
        """登録した MCP サーバから束を取って取り込む（``…/knowledge-copy`` と同じ条件・断り方）。
        呼ぶ道具は設定 ``sourceProvider.knowledge`` の 1 つだけで、引数は渡さない。道具を呼ぶのは
        ジョブの ``fetch`` の段。"""
        reg = getattr(app.state, "mcp_registry", None)
        if reg is None:
            raise HTTPException(404, "MCP servers are only available in single-user mode")
        _reject_if_content_length_exceeds(request, 64 * 1024)
        lang, relations = _options(_json_object(await request.body(), required=False))
        server = reg.enabled_server_or_raise(name)
        tool = reg.provider().knowledge
        if not tool:
            raise HTTPException(
                409, "知識の束を出す道具 (sourceProvider.knowledge) が設定されていません"
            )

        async def fetch() -> Any:
            result = await _call_or_http_error(
                reg.client, server, tool, {}, timeout_s=KNOWLEDGE_TIMEOUT_S
            )
            if len(result.text.encode("utf-8")) > MAX_BUNDLE_BYTES:
                raise HTTPException(413, "知識の束が大きすぎます")
            return result.data

        return {
            "server": name,
            **_start(
                request, source=name, raw_bundle=None, fetch=fetch, lang=lang, relations=relations
            ),
        }

    @app.get("/api/knowledge-imports", dependencies=deps)
    async def knowledge_imports_list() -> dict[str, Any]:
        """入れた物の一覧。"""
        return {"sources": await asyncio.to_thread(list_imports, app, root)}

    @app.delete("/api/knowledge-imports/{source}", dependencies=deps)
    async def knowledge_imports_remove(source: str) -> dict[str, Any]:
        """外す: 台帳の未確認を先に消し（確認済み・「違う」の記録は残す）、そのあと写しのデータ
        セットを取り下げる（公開前のものは削除）。取り下げが落ちたら 5xx で、もう一度 DELETE
        すればやり直せる。走っている間は 409・写しが無ければ 404。"""
        from asterism_api.chat_attach import make_http

        other = busy_kind(app, source)
        if other is not None:
            raise HTTPException(409, _busy_message(source, other))
        dataset_id = find_copy_dataset(root, source)
        if dataset_id is None:
            raise HTTPException(404, f"{source} の写しがありません")
        _hold(app, source, "remove")
        try:
            # 台帳を先に整える（何度やっても同じ結果）。取り下げが落ちても、もう一度 DELETE で
            # やり直せる（写しはまだ見つかる）。「取り下げ済みで台帳が残る」状態を作らない
            try:
                removed = await asyncio.to_thread(pr.remove_ledger, root, source, at=_now())
            except pr.LedgerError as exc:
                raise HTTPException(409, "台帳が読めません") from exc
            except OSError as exc:
                logger.warning("knowledge import: could not update the ledger", exc_info=True)
                raise HTTPException(500, "台帳を更新できませんでした") from exc
            meta = registry.load_meta(root, dataset_id) or {}
            try:
                async with make_http(app, cfg.api_token, {}) as http:
                    if meta.get("promoted"):  # 引用してきた版は消さずに取り下げる
                        res = await http.post(f"/api/datasets/{dataset_id}/retract")
                    else:  # 公開前（途中で止まった写し）は消してよい
                        res = await http.delete(f"/api/datasets/{dataset_id}")
            except Exception as exc:
                logger.warning("knowledge import: could not retract the copy", exc_info=True)
                raise HTTPException(500, "写しを取り下げられませんでした") from exc
            if res.status_code != 200:
                try:
                    detail = res.json().get("detail")
                except Exception:
                    detail = None
                raise HTTPException(res.status_code, detail or res.text[:300])
        finally:
            _release(app, source, "remove")
        return {
            "removed": {
                "dataset_id": dataset_id,
                "relations": removed["relations"],
                "kept_confirmed": removed["kept_confirmed"],
                "kept_rejected": removed["kept_rejected"],
            }
        }
