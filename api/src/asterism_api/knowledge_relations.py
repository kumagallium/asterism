"""写した知識の「語の組」ごとに、AI が関係を 1 つ選ぶジョブと、台帳を読む口（PR 14c・契約書 §3）。

* ``POST /api/knowledge-copies/{source}/relations`` — ジョブを始める（``202 {job_id}``）。
* ``GET  /api/knowledge-copies/{source}/relations`` — 台帳を写しの名前・欄で引いて返す。

検査と台帳の更新は :mod:`asterism.pair_relations`（決定論）、AI への 1 回の問いは
:func:`asterism_step0.pair_propose.propose_pair`。ここは、それらを同時 4 本で回し、
台帳の更新と保存を
イベントループ側で直列に行う（スレッドから台帳を触らない）。出す側（MCP）からは import しない。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

from asterism import knowledge_bundle as kb
from asterism import ledger_answer, relation_catalog
from asterism import pair_relations as pr
from asterism import unstated_groups as ug
from asterism.graph_diff import REL_SENTENCE
from asterism.shared_vocab import term_iri
from asterism_step0.pair_propose import propose_pair
from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from asterism_api import chat_attach, registry
from asterism_api.chat_routes import _ai_unset
from asterism_api.converse_routes import _llm_coords, _llm_max_tokens, _record_usage
from asterism_api.knowledge_copy import busy_kind, copy_is_current, find_copy_dataset

logger = logging.getLogger(__name__)

__all__ = [
    "ABORT_AFTER_FAILURES",
    "CONCURRENCY",
    "SAVE_EVERY",
    "build_rows",
    "register_knowledge_relations_routes",
    "run_relations",
]

#: 同時に AI へ問う数。
CONCURRENCY = 4
#: 台帳をこの件数ごとに保存する（止めても済んだ分が残る）。
SAVE_EVERY = 10
#: 最初のこの件数が全部失敗（成功 0）なら、残りを聞かずに止める（鍵が切れているとき）。
ABORT_AFTER_FAILURES = 8
#: 同時に 1 本しか安全に回せないプロバイダ（呼び出しごとに手元の CLI を起こす）。
_SERIAL_PROVIDERS = ("copilot", "copilot-subscription")
_FEATURE = "knowledge.relations"


def _now() -> str:
    return datetime.now(UTC).isoformat()


class RelationsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lang: Literal["ja", "en"] = "ja"
    #: 聞く組を先頭からこの件数に絞る（残りは次に走らせたとき聞く）。
    limit: int | None = Field(default=None, ge=1)


# ---- ジョブ ----


def _added_relations() -> list[dict[str, Any]]:
    """目録の「人が足した関係」（やめた物を除く・足した順）を、AI に渡す形にする（R3b）。
    読めなければ 0 件として動く（足した関係が無いときと同じ）。"""
    try:
        return [
            {
                "name": r.name,
                "label": dict(r.label),
                "definition": dict(r.definition),
                "symmetric": bool(r.symmetric_pair),
                "to_property_only": bool(r.to_property_only),
            }
            for r in relation_catalog.human_relations()
            if not r.retired
        ]
    except Exception:
        logger.warning("relations: added relations unreadable", exc_info=True)
        return []


async def run_relations(
    registry_root: Path,
    source: str,
    dataset_id: str,
    *,
    make_llm: Callable[[], Any],
    on_usage: Callable[[Any], None] | None = None,
    lang: str = "ja",
    limit: int | None = None,
    emit: Callable[..., None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    concurrency: int = CONCURRENCY,
    ask: bool = True,
    force: frozenset[str] | set[str] = frozenset(),
    reasked_as: str | None = None,
) -> dict[str, Any]:
    """語の組ごとに関係を聞いて台帳に残す。
    返り ``{summary, asked, reused, failed, kept_stale, cancelled, aborted, abort_reason}``。
    ``failed`` は台帳に ``failed`` として入った数。``kept_stale`` は失敗して前の件を残した組の数。

    ``make_llm()`` は呼び出し 1 本ごとの AI クライアントを返す（クライアントは使い回さない:
    guided decoding のスキーマをクライアントの属性に置くので、並行すると互いに上書きする）。
    ``on_usage`` は AI が答えを返すたび（聞き直しの各回も・失敗した呼び出しは除く）に、その回の
    使用量の写し（``last_usage`` など）を持つ object で、イベントループ側から呼ばれる。

    ``ask=False`` は AI を呼ばない仕上げ（聞く組を 0 件にする。``make_llm`` も呼ばない）: 台帳を
    ``finalize``・保存・``summarize`` するだけで、取り込み元の関係と前の回の結果は残る
    （14d-2a・AI 未設定と ``relations: false``）。台帳の扱いは普段と同じ。

    ``force``（R4）の組は指紋が同じでも聞き直す（``confirmed`` は聞かない）。``reasked_as`` は
    判定し直した足した関係の名前で、``force`` の組の件に ``reasked`` として残る。``force`` が空なら
    今までと同じ。返りに ``relation``・``none``（``force`` の組のうち答えが関係／「言えない」に
    なった数）を足す。

    止まり方: 取り消し・時間切れ・途中の例外でも、済んだ答えは台帳に入れ、``finalize`` して保存して
    から抜ける。最初の :data:`ABORT_AFTER_FAILURES` 件が全部失敗なら（鍵が切れているなど）残りは
    聞かずに止める（飛行中の呼び出しが終わるのを待ち、終わった中に成功があれば続ける）。"""
    emit = emit or (lambda **_: None)
    cancelled_now = should_cancel or (lambda: False)
    loop = asyncio.get_running_loop()

    src = registry.source_dir(registry_root, dataset_id)
    if src is None:
        raise RuntimeError("写しのデータセットが見つかりません")
    copy = await asyncio.to_thread(kb.read_copy, src)

    tasks = pr.build_tasks(copy)
    ledger = pr.load_ledger(registry_root, source)
    todo_all, base = pr.plan_run(ledger, tasks, force=force)
    reused = len(tasks) - len(todo_all)
    todo = (todo_all[:limit] if limit else todo_all) if ask else []
    total = len(todo)

    # 呼び出し 1 本に AI クライアント 1 つ（同時に最大 ``concurrency`` 本）
    pool = (
        [make_llm() for _ in range(max(1, min(concurrency, total) if total else 1))]
        if ask
        else []
    )
    model = next((m for m in (getattr(c, "model", None) for c in pool) if isinstance(m, str)), None)
    sem = asyncio.Semaphore(len(pool))
    terms = {str(t["id"]): t for t in copy.terms if "id" in t}
    # R3b: 人が足した関係（やめた物を除く）を走りの始めに 1 回だけ読み、全部の組に同じ物を渡す。
    # 0 件なら AI への呼び出しは今までと同じ（引数を足さない）
    extra = _added_relations()
    extra_kw: dict[str, Any] = {"extra": extra} if extra else {}

    def usage_hook(llm: Any) -> Callable[[], None] | None:
        """``complete`` が成功して返るたびにワーカースレッドから呼ばれる。その回の使用量を
        スレッドの中で写し取り（次の回が上書きする前に）、記録はイベントループ側に渡す。"""
        if on_usage is None:
            return None

        def hook() -> None:
            snap = SimpleNamespace(
                last_usage=getattr(llm, "last_usage", None),
                last_model=getattr(llm, "last_model", None),
                model=getattr(llm, "model", None),
            )
            with contextlib.suppress(RuntimeError):  # ループが閉じていれば記録しない
                loop.call_soon_threadsafe(_record, snap)

        return hook

    def _record(snap: Any) -> None:
        try:
            on_usage(snap)  # type: ignore[misc]
        except Exception:  # 使用量の記録が足りなくても仕事は止めない
            logger.warning("relations: usage record failed", exc_info=True)

    st: dict[str, Any] = {
        "done": 0, "failed": 0, "relation": 0, "none": 0, "ok": 0, "kept_stale": 0,
        "since_save": 0, "inflight": 0,
        "last_exc": None, "halt": False, "aborted": False, "wake": asyncio.Event(),
    }  # fmt: skip

    def save() -> None:
        pr.save_ledger(registry_root, base, at=_now())
        st["since_save"] = 0

    def settle() -> None:
        """止めている間（halt）に、飛行中の呼び出しが全部終わったら判定する。終わった中に成功が
        1 件でもあれば再開し、1 件も無ければ打ち切る。"""
        if st["halt"] and st["inflight"] == 0:
            st["halt"] = False
            st["aborted"] = st["ok"] == 0
            old, st["wake"] = st["wake"], asyncio.Event()
            old.set()

    async def ask(task: dict) -> None:
        """1 組を聞いて、答えをその場（イベントループ上）で台帳に入れる。"""
        async with sem:
            while st["halt"]:  # 失敗が続いて止めている間は、新しい呼び出しを始めない
                await st["wake"].wait()
            if cancelled_now() or st["aborted"]:
                return
            llm = pool.pop()
            st["inflight"] += 1
            try:
                answer = await asyncio.to_thread(
                    propose_pair, llm, task["input"], lang, on_call=usage_hook(llm), **extra_kw
                )
                reason = None
            except Exception as exc:  # PairProposeError・LLM の例外はその組だけ failed
                answer, reason = None, f"{type(exc).__name__}: {exc}"[:300]
                st["last_exc"] = type(exc).__name__
            finally:
                pool.append(llm)
            # 答えは枠（sem）を持ったまま台帳に入れる（次の呼び出しが始まる前に中止の判定が効く）
            prev = (base.get("pairs") or {}).get(task["key"]) or {}
            if answer is not None:
                entry = pr.check_answer(task, answer, terms, rejected=prev.get("rejected") or [])
                st["ok"] += 1
            else:
                entry = pr.failed_entry(task, reason or "")
                if prev and prev.get("state") != "failed":
                    st["kept_stale"] += 1  # 前の件を残す（merge_entry）。台帳の failed には入らない
                else:
                    st["failed"] += 1
            forced = task["key"] in force
            pr.merge_entry(
                base, entry, model=model, at=_now(), reasked_as=reasked_as if forced else None
            )
            if forced and entry.get("state") in ("relation", "none"):
                st[str(entry["state"])] += 1
            st["done"] += 1
            st["since_save"] += 1
            st["inflight"] -= 1
            emit(stage="pairs", message=f"{st['done']}/{total}", done=st["done"], total=total)
            if not st["halt"] and st["ok"] == 0 and st["done"] >= ABORT_AFTER_FAILURES:
                st["halt"] = True  # 成功 0 のまま失敗がたまった → 飛行中の分を待ってから判定する
            settle()
            if st["since_save"] >= SAVE_EVERY:
                save()

    emit(stage="pairs", message=f"0/{total}", done=0, total=total)
    running = [asyncio.create_task(ask(t)) for t in todo]
    completed = False
    try:
        await asyncio.gather(*running)
        completed = True
    finally:
        # 時間切れ・取り消し・例外でも、済んだ分を残す（ここでは await しない）
        for t in running:
            t.cancel()
        try:
            pr.save_ledger(
                registry_root, pr.finalize(base, copy, dataset_id=dataset_id), at=_now()
            )
        except Exception:
            if completed:
                raise
            # 止まり方の元の例外（取り消し・時間切れ）を、保存の失敗で置き換えない
            logger.warning("relations: final save failed", exc_info=True)
    final = pr.load_ledger(registry_root, source)
    return {
        "summary": pr.summarize(final),
        "asked": st["done"],
        "reused": reused,
        "failed": st["failed"],
        "kept_stale": st["kept_stale"],
        "relation": st["relation"],
        "none": st["none"],
        "cancelled": bool(cancelled_now()),
        "aborted": bool(st["aborted"]),
        "abort_reason": st["last_exc"] if st["aborted"] else None,
    }


# ---- 読む口の行 ----


def _term_card(t: dict, stored: dict | None = None) -> dict:
    """語の札。``term_iri`` は、確認済みの関係を置いたときに決めたストアの用語の IRI
    （台帳の ``stored_terms``。**無いときは欄ごと出さない**＝既存の札の形を変えない。14e-1b）。"""
    slug = ((stored or {}).get(str(t["id"])) or {}).get("slug")
    iri = None
    if isinstance(slug, str):
        try:
            iri = term_iri(slug)
        except ValueError:
            iri = None
    card = {
        "id": str(t["id"]),
        "name": t.get("name"),
        "kind": t.get("kind"),
        "role": t.get("role"),
    }
    if iri is not None:
        card["term_iri"] = iri
    return card


def _store_of(e: dict) -> dict | None:
    """件の ``store``（確認済みを正本に置いた結果）を ``{ok, reason?}`` に。無ければ ``None``。"""
    st = e.get("store")
    if not (e.get("confirmed") and isinstance(st, dict) and "ok" in st):
        return None
    out: dict[str, Any] = {"ok": bool(st["ok"])}
    if not st["ok"] and st.get("reason"):
        out["reason"] = st["reason"]
    return out


def build_rows(
    ledger: dict, copy: kb.KnowledgeCopy, *, state: str, lang: str
) -> tuple[list[dict], list[dict]]:
    """台帳を写しの名前・欄で引いて ``(relations, origin)`` にする。

    両端の語が写しに無い行は落とす。根拠の知見・文脈の語が写しに無ければ、その分だけ落とす。

    ``origin``（取り込み元の関係）は AI の判定との印 ``ai`` を持ち、``state`` ごとに出し分ける:
    ``relation`` は ``unseen`` だけ（``agrees`` は同じ関係が ``relations`` 側に根拠つきで
    出るので、その行に ``origin_iri`` を足す）・``none`` は ``disputes`` だけ（候補）・
    ``dropped`` は人が「違う」とした行（``rejected``）だけ。``state == "all"`` は全部の状態の件と、
    ``agrees`` 以外の取り込み元の行（14e-1）。

    行の ``by`` は ``ai``／``origin``／``user``・``confirmed`` は今の確認の有無（``True``・
    無ければ ``None``。既存の読み手と形を変えない）・``rejected`` は今「違う」で見せていない件か
    （bool）。取り込み元の行（``origin``）にも組の鍵 ``key`` を付ける
    （確認・「違う」の対象を指す）。
    組の鍵 ``pair`` は全部の行に付く。取り込み元の行の ``key`` は 3 つ組ごと
    （``o:<from>|<rel>|<to>``・:func:`asterism.pair_relations.origin_key`）。"""
    terms = {str(t["id"]): t for t in copy.terms if "id" in t}
    claims = {str(c["id"]): c for c in copy.claims if "id" in c}
    sentences = REL_SENTENCE.get(lang) or REL_SENTENCE["ja"]
    stored = ledger.get("stored_terms") if isinstance(ledger.get("stored_terms"), dict) else {}

    def sentence(rel: Any, frm: dict, to: dict) -> str | None:
        tmpl = sentences.get(rel) if rel else None
        return tmpl.format(s=frm["name"], o=to["name"]) if tmpl else None

    pairs = ledger.get("pairs") or {}
    marked = [
        (o, o.get("ai") or pr.origin_check(o, pairs))
        for o in ledger.get("origin") or []
    ]
    # AI と同じ関係の取り込み元の題（agrees）→ その組の relations 側の行へ
    agreed = {
        "|".join(sorted((str(o["from"]), str(o["to"])))): o.get("origin_iri")
        for o, mark in marked
        if mark == "agrees"
    }

    rows: list[dict] = []
    for key in sorted(pairs):
        e = ledger["pairs"][key]
        # orphan・state の無い件は記録として残すだけ（出さない）
        if e.get("orphan") or not e.get("state") or (state != "all" and e.get("state") != state):
            continue
        # 関係が無い件（none・failed・集合の外）は、組の 2 語を両端に出す
        tf = terms.get(str(e.get("from") or e.get("a")))
        tt = terms.get(str(e.get("to") or e.get("b")))
        if tf is None or tt is None:
            continue
        frm, to = _term_card(tf, stored), _term_card(tt, stored)
        evidence = []
        for cid in e.get("evidence") or []:
            c = claims.get(str(cid))
            if c is None:
                continue
            evidence.append(
                {
                    "id": str(cid),
                    "iri": c.get("iri"),
                    "title": c.get("title"),
                    "source_url": c.get("source_url"),
                    "review_state": c.get("review_state"),
                    "epistemic_status": c.get("epistemic_status"),
                    "modal_qualifier": c.get("modal_qualifier"),
                }
            )
        context = [
            {"id": str(t), "name": terms[str(t)].get("name")}
            for t in e.get("context") or []
            if str(t) in terms
        ]
        row = {
            "key": key,
            "pair": key,
            "state": e.get("state"),
            "rel": e.get("rel"),
            "from": frm,
            "to": to,
            "sentence": sentence(e.get("rel"), frm, to),
            "evidence": evidence,
            "context": context,
            "tentative": bool(e.get("tentative")),
            "note": e.get("note"),
            "reason": e.get("reason"),
            "by": e.get("by"),
            "confirmed": True if e.get("confirmed") else None,
            "rejected": e.get("state") == "dropped" and e.get("reason") == "rejected",
        }
        if e.get("state") == "none" and e.get("unstated_kind"):  # R1: 無いときは欄ごと出さない
            row["unstated_kind"] = e["unstated_kind"]
        if (store := _store_of(e)) is not None:  # 無いときは欄ごと出さない（既存の行の形のまま）
            row["store"] = store
        if e.get("state") == "relation" and key in agreed:
            row["origin_iri"] = agreed[key]
        rows.append(row)

    show = {
        "relation": ("unseen",),
        "none": ("disputes",),
        "dropped": ("rejected",),
        "all": ("unseen", "disputes", "rejected"),
    }.get(state, ())
    origin: list[dict] = []
    for o, mark in marked:
        if mark not in show:
            continue
        tf, tt = terms.get(str(o.get("from"))), terms.get(str(o.get("to")))
        if tf is None or tt is None:
            continue
        frm, to = _term_card(tf, stored), _term_card(tt, stored)
        origin.append(
            {
                "key": pr.origin_key(o),
                "pair": "|".join(sorted((frm["id"], to["id"]))),
                "rel": o.get("rel"),
                "from": frm,
                "to": to,
                "sentence": sentence(o.get("rel"), frm, to),
                "origin_iri": o.get("origin_iri"),
                "ai": mark,
                "by": "origin",
                "rejected": mark == "rejected",
            }
        )
    return rows, origin


# ---- 口 ----


def register_knowledge_relations_routes(
    app: FastAPI,
    cfg: Any,
    resolve_llm: Any,
    *,
    write_auth: Sequence[Any] = (),
) -> None:
    """関係を選ぶジョブの口と読む口を登録する（``register_knowledge_copy_routes`` の後）。"""
    deps = list(write_auth)
    root: Path = cfg.registry_root

    def _running() -> set[str]:
        running: set[str] | None = getattr(app.state, "knowledge_relations_running", None)
        if running is None:
            running = set()
            app.state.knowledge_relations_running = running
        return running

    def _ledger_or_409(source: str) -> dict:
        try:
            return pr.load_ledger(root, source)
        except pr.LedgerError as exc:
            raise HTTPException(409, "台帳が読めません") from exc

    def _current_copy(source: str) -> tuple[str, dict]:
        """写しのデータセット id と meta。無ければ 404・作り直しの途中なら 409。"""
        dataset_id = find_copy_dataset(root, source)
        if dataset_id is None:
            raise HTTPException(404, f"{source} の写しがありません")
        meta = registry.load_meta(root, dataset_id) or {}
        copying = getattr(app.state, "knowledge_copy_running", None) or set()
        if source in copying or not copy_is_current(meta):
            raise HTTPException(409, f"{source} の写しは作り直している途中です")
        return dataset_id, meta

    def _llm_setup(request: Request) -> tuple[Callable[[], Any], Callable[[Any], None], bool]:
        """「作る AI」をブラウザの X-LLM-* から組む（論文の提案と同じ）。
        返りは ``(make_llm, on_usage, serial)``。設定が無い・組めなければ 409。"""
        headers = chat_attach.llm_headers_of(request.headers)
        provider, model, api_base, key = _llm_coords(
            headers.get("x-api-key"),
            headers.get("x-llm-provider"),
            headers.get("x-llm-model"),
            headers.get("x-llm-api-base"),
            root,
        )
        if _ai_unset(key, provider, api_base):
            raise HTTPException(409, "AI が設定されていません")
        max_tokens = _llm_max_tokens(headers.get("x-llm-max-tokens"))

        def make_llm() -> Any:
            return resolve_llm(provider, model, api_base, key, max_tokens=max_tokens)

        try:
            make_llm()  # 組めるかを先に確かめる（走り出してから落ちない）
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("relations: no AI client", exc_info=True)
            raise HTTPException(409, "AI を組めませんでした") from exc

        def on_usage(llm: Any) -> None:
            _record_usage(root, _FEATURE, provider, llm, model)

        serial = provider.strip().lower() in _SERIAL_PROVIDERS
        return make_llm, on_usage, serial

    @app.post("/api/knowledge-copies/{source}/relations", status_code=202, dependencies=deps)
    async def knowledge_relations_start(
        source: str, request: Request, body: RelationsBody | None = None
    ) -> dict[str, Any]:
        """語の組ごとの関係選びを始める。本文 ``{lang?, limit?}``。"""
        body = body or RelationsBody()
        dataset_id, _meta = _current_copy(source)
        _ledger_or_409(source)
        running = _running()
        if source in running:
            raise HTTPException(409, f"{source} の関係選びは走っている途中です")
        other = busy_kind(app, source)  # 取り込み・外すも同じ取り込み元では 1 つずつ
        if other is not None:
            raise HTTPException(409, f"{source} は別の作業 ({other}) が走っている途中です")

        make_llm, on_usage, serial = _llm_setup(request)

        running.add(source)  # await を挟まずに付ける（2 つ目は必ず 409）

        async def work(emit: Callable[..., None], should_cancel: Callable[[], bool]) -> Any:
            try:
                return await run_relations(
                    root,
                    source,
                    dataset_id,
                    make_llm=make_llm,
                    on_usage=on_usage,
                    lang=body.lang,
                    limit=body.limit,
                    emit=emit,
                    should_cancel=should_cancel,
                    concurrency=1 if serial else CONCURRENCY,
                )
            finally:
                running.discard(source)

        try:
            job_id = app.state.jobs.start_coro(work)
        except Exception:
            running.discard(source)
            raise
        return {"job_id": job_id}

    @app.get("/api/knowledge-copies/{source}/relations", dependencies=deps)
    async def knowledge_relations_read(
        source: str,
        state: str = Query("relation"),
        lang: str = Query("ja"),
    ) -> dict[str, Any]:
        """台帳を、写しの名前・欄で引いて返す。"""
        if state != "all" and state not in pr.STATES:
            raise HTTPException(422, f"state は {', '.join((*pr.STATES, 'all'))} のどれかです")
        lang = "en" if lang == "en" else "ja"
        dataset_id, _meta = _current_copy(source)
        src = registry.source_dir(root, dataset_id)
        if src is None:
            raise HTTPException(404, f"{source} の写しがありません")
        try:
            copy = await asyncio.to_thread(kb.read_copy, src)
        except kb.BundleError as exc:
            raise HTTPException(409, f"写しが読めません: {exc}") from exc
        ledger = _ledger_or_409(source)
        rows, origin = build_rows(ledger, copy, state=state, lang=lang)
        return {"summary": pr.summarize(ledger), "relations": rows, "origin": origin}

    @app.get("/api/relations/unstated", dependencies=deps)
    async def relations_unstated(
        lang: str = Query("ja"), source: str | None = Query(None)
    ) -> dict[str, Any]:
        """全部の取り込み元の台帳の「言えなかった関係の種類」を束ねて返す（R2）。読むだけ
        （台帳は書き換えない・走っている作業があっても読める）。取り下げ・削除した写しの取り込み元は
        飛ばして ``skipped.missing_copy`` に、壊れた台帳は ``skipped.broken`` に数える。"""
        lang = "en" if lang == "en" else "ja"

        def work() -> dict[str, Any]:
            st: dict[str, int] = {}
            if source is None:
                found = ledger_answer.find_ledgers(root, stats=st)
            else:
                one = ledger_answer.load_source(root, source)
                found = [(source, one[0], one[1])] if one is not None else []
                st = {"broken": 0, "skipped": 0}
                lp = pr.ledger_path(root, source)
                if not found and lp.exists():
                    # 台帳が読めない＝壊れた・読めて写しが無い＝取り下げ・削除
                    led = ledger_answer._read_ledger(lp)
                    key = "broken" if led is None or led.get("source") != source else "skipped"
                    st[key] = 1
            groups = ug.unstated_groups(found, lang=lang)
            return {
                "groups": groups,
                "total_pairs": sum(g["count"] for g in groups),
                "sources": [name for name, _l, _c in found],
                "skipped": {"broken": st.get("broken", 0), "missing_copy": st.get("skipped", 0)},
            }

        return await asyncio.to_thread(work)

    # ---- 判定し直し（R4）----

    def _reasking() -> set[str]:
        """判定し直しが走っている関係の名前（同じ関係の 2 本目を 409 にする）。"""
        names: set[str] | None = getattr(app.state, "relations_reasking", None)
        if names is None:
            names = set()
            app.state.relations_reasking = names
        return names

    def _added_or_error(name: str) -> relation_catalog.Relation:
        """足した関係を返す。無い・組み込みは 404・やめた関係は 409。"""
        try:
            rel = relation_catalog.relation(name)
        except KeyError:
            raise HTTPException(404, f"relation not found: {name}") from None
        if rel.builtin:
            raise HTTPException(404, f"relation not found: {name}")
        if rel.retired:
            raise HTTPException(409, f"{name} は使うのをやめた関係です")
        return rel

    def _reask_plan(name: str, rel: relation_catalog.Relation) -> dict[str, list[str]]:
        """取り込み元 → 判定し直す組の鍵（写しに両端の語がある組だけ・台帳は書き換えない）。"""
        keys = set(pr.reask_keys(rel))
        out: dict[str, list[str]] = {}
        if not keys:
            return out
        for source, ledger, copy in ledger_answer.find_ledgers(root):
            terms = {str(t["id"]) for t in copy.terms if isinstance(t, dict) and "id" in t}
            pairs = ledger.get("pairs") or {}
            found = [
                k
                for k in pr.reask_targets(ledger, relation_name=name, keys=keys)
                if str(pairs[k].get("a")) in terms and str(pairs[k].get("b")) in terms
            ]
            if found:
                out[source] = found
        return out

    @app.get("/api/relations/{name}/reask", dependencies=deps)
    async def relation_reask_count(name: str) -> dict[str, Any]:
        """足した関係で判定し直せる「言えない」組の数（読むだけ）。"""
        rel = _added_or_error(name)
        plan = await asyncio.to_thread(_reask_plan, name, rel)
        return {
            "name": name,
            "keys": sorted(k for ks in plan.values() for k in ks),
            "sources": {s: len(ks) for s, ks in sorted(plan.items())},
            "total": sum(len(ks) for ks in plan.values()),
        }

    @app.post("/api/relations/{name}/reask", status_code=202, dependencies=deps)
    async def relation_reask_start(
        name: str, request: Request, body: RelationsBody | None = None
    ) -> dict[str, Any]:
        """足した関係で、同じ種類の「言えない」組だけを AI に判定し直す（取り込み元ごとに順に）。"""
        body = body or RelationsBody()
        rel = _added_or_error(name)
        reasking = _reasking()
        if name in reasking:
            raise HTTPException(409, f"{name} の判定し直しは走っている途中です")
        plan = await asyncio.to_thread(_reask_plan, name, rel)
        if not plan:
            raise HTTPException(409, "判定し直す組がありません")
        running = _running()
        # 飛ばした理由は符号: relations_running・busy・no_copy・rebuilding（始めに）／
        # cancelled・aborted・error（走りの途中）。画面が文にする
        skipped: dict[str, str] = {}
        todo: list[tuple[str, str, list[str]]] = []
        for source, keys in plan.items():
            if source in running:
                skipped[source] = "relations_running"
                continue
            if busy_kind(app, source) is not None:
                skipped[source] = "busy"  # 取り込み・写す・外す
                continue
            dataset_id = find_copy_dataset(root, source)
            if dataset_id is None:
                skipped[source] = "no_copy"
                continue
            meta = registry.load_meta(root, dataset_id) or {}
            if not copy_is_current(meta):
                skipped[source] = "rebuilding"
                continue
            todo.append((source, dataset_id, keys))
        if not todo:
            detail = {"detail": "判定し直せる取り込み元がありません", "skipped": skipped}
            raise HTTPException(409, detail)
        make_llm, on_usage, serial = _llm_setup(request)

        # await を挟まずに付ける（2 本目・同じ取り込み元の別の作業は必ず 409）
        reasking.add(name)
        running.update(s for s, _d, _k in todo)

        async def work(emit: Callable[..., None], should_cancel: Callable[[], bool]) -> Any:
            results: dict[str, dict[str, Any]] = {}
            failed_sources: dict[str, str] = dict(skipped)
            try:
                for i, (source, dataset_id, keys) in enumerate(todo, start=1):
                    if should_cancel():
                        failed_sources[source] = "cancelled"
                        running.discard(source)
                        continue

                    def emit_one(_s: str = source, _i: int = i, **kw: Any) -> None:
                        emit(source=_s, source_index=_i, source_total=len(todo), **kw)

                    try:
                        run = await run_relations(
                            root,
                            source,
                            dataset_id,
                            make_llm=make_llm,
                            on_usage=on_usage,
                            lang=body.lang,
                            limit=body.limit,
                            emit=emit_one,
                            should_cancel=should_cancel,
                            concurrency=1 if serial else CONCURRENCY,
                            force=frozenset(keys),
                            reasked_as=name,
                        )
                        results[source] = {
                            "asked": run["asked"],
                            "relation": run["relation"],
                            "none": run["none"],
                            "failed": run["failed"],
                            "cancelled": run["cancelled"],
                            "aborted": run["aborted"],
                        }
                        if run["aborted"] or run["cancelled"]:
                            for rest, _d, _k in todo[i:]:
                                why = "cancelled" if run["cancelled"] else "aborted"
                                failed_sources[rest] = why
                                running.discard(rest)
                            running.discard(source)
                            break
                    except Exception:
                        logger.warning("relations: reask failed for %s", source, exc_info=True)
                        failed_sources[source] = "error"
                    finally:
                        running.discard(source)
            finally:
                for source, _d, _k in todo:
                    running.discard(source)
                reasking.discard(name)
            total = {
                key: sum(r[key] for r in results.values())
                for key in ("asked", "relation", "none", "failed")
            }
            return {"name": name, "sources": results, "skipped": failed_sources, "total": total}

        try:
            job_id = app.state.jobs.start_coro(work)
        except Exception:
            reasking.discard(name)
            for source, _d, _k in todo:
                running.discard(source)
            raise
        return {
            "job_id": job_id,
            "sources": [s for s, _d, _k in todo],
            "skipped": skipped,
        }
