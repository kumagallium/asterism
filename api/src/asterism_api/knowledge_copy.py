"""取り込み元の「知識の束」を 1 つのデータセットに写す（ADR ``knowledge-copy.md``）。

束（``asterism-knowledge/1``）は、取り込み元（ノートのアプリなど）が持つ知見・トピック・資料と、
語の候補を 1 本の JSON にしたもの。形は Asterism の約束で、取り込み元はそれを実装するだけ
（:mod:`asterism.knowledge_bundle`）。ここは、その束を取り込み元ごとに 1 つのデータセットとして
写す口を 2 つ出す。

* ``POST /api/knowledge-copies`` — 束を本文で受ける（スクリプト・試験。MCP を使わない道）。
* ``POST /api/mcp-servers/{name}/knowledge-copy`` — 登録した MCP サーバの道具
  （設定 ``sourceProvider.knowledge``）を呼んで束を取り、同じ核に渡す。単一ユーザーモード限定。

写しは作り直せる（知見の家は取り込み元）。毎回、束の全量で source を置き換えて取り込み直し、
公開する。設計（Mapping IR）は :func:`asterism.knowledge_bundle.design_document` が束の基底から
機械で組む — AI は呼ばず、人の設計も挟まない。語は写しの行（種類「語」）として持ち、用語
（共有語）にはしない（用語にするのは関係を選ぶ段）。

自分のルート（materialize → ingest → promote）を ASGI で叩く（``resync_rows`` と同じ作り）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from asterism import knowledge_bundle as kb
from fastapi import Depends, FastAPI, HTTPException, Request

from asterism_api import registry
from asterism_api.mcp_client_routes import (
    _call_or_http_error,
    _reject_if_content_length_exceeds,
    _require_write_auth,
)

if TYPE_CHECKING:  # pragma: no cover
    from asterism_api.main import Settings

logger = logging.getLogger(__name__)

__all__ = [
    "KNOWLEDGE_TIMEOUT_S",
    "MAX_BUNDLE_BYTES",
    "CopyError",
    "busy_kind",
    "copy_is_current",
    "copy_knowledge",
    "find_copy_dataset",
    "register_knowledge_copy_routes",
]

#: 束（JSON）の上限。知見 2,000 件で約 1 MB。
MAX_BUNDLE_BYTES = 64 * 1024 * 1024
#: 束を出す道具を待つ秒数（全量を読んで組むので、一覧より長い）。
KNOWLEDGE_TIMEOUT_S = 300.0
#: 取り込み元の名前（MCP のサーバ名と同じ形。meta に入り、同じ名前の写しを探す鍵になる）。
_SOURCE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_GONE = ("retracted", "deleted")


def busy_kind(app: FastAPI, source: str) -> str | None:
    """この取り込み元で今走っている作業の種類（無ければ None）。「同時に 1 つだけ」の印を 1 つの
    見方にまとめる関数で、写す・関係を選ぶ・取り込み・外すの口はどれもこれを見る。

    種類は ``import``・``remove``（``app.state.knowledge_busy``＝取り込みの口が付ける）→ ``copy``
    （``knowledge_copy_running``）→ ``relations``（``knowledge_relations_running``）の順に見る。"""
    held: dict[str, str] = getattr(app.state, "knowledge_busy", None) or {}
    if source in held:
        return held[source]
    if source in (getattr(app.state, "knowledge_copy_running", None) or set()):
        return "copy"
    if source in (getattr(app.state, "knowledge_relations_running", None) or set()):
        return "relations"
    return None


class CopyError(Exception):
    """写せなかった。``status`` は HTTP の status、``detail`` は本文。"""

    def __init__(self, status: int, detail: Any) -> None:
        super().__init__(str(detail))
        self.status = status
        self.detail = detail


def find_copy_dataset(registry_root: Path, source: str) -> str | None:
    """この取り込み元の写しのデータセット（``meta.knowledge_source.name``）。無ければ None。
    引用対象から外した・消したものは数えない（その場合は新しく作る）。"""
    for meta in registry.list_datasets(registry_root):
        ks = meta.get("knowledge_source")
        if not isinstance(ks, dict) or ks.get("name") != source:
            continue
        if meta.get("status") in _GONE:
            continue
        did = meta.get("id")
        if isinstance(did, str) and registry.is_valid_dataset_id(did):
            return did
    return None


def copy_is_current(meta: dict[str, Any] | None) -> bool:
    """写しの source（``registry/<id>/source/``）が、公開中の版と同じ束から来ているか。

    作り直しは source を先に置き換えてから取り込むので、取り込みや公開が途中で落ちると、source は
    新しい束・公開中の版は前の束、になる。その間 ``knowledge_source.syncing`` が立ったままで、
    ``version`` は前の版を指す。source を読む側（:func:`asterism.knowledge_bundle.read_copy` の
    呼び手）は、これが偽のあいだは読まない。次に写しが通れば直る。"""
    ks = (meta or {}).get("knowledge_source")
    if not isinstance(ks, dict) or ks.get("syncing"):
        return False
    return bool(ks.get("version")) and ks.get("version") == (meta or {}).get("live_graph")


def _dataset_name(title: str, lang: str) -> str:
    return f"{title} knowledge" if lang == "en" else f"{title} の知見"


def _detail_of(res: Any) -> Any:
    try:
        d = res.json().get("detail")
    except Exception:
        return res.text[:300]
    return d if d is not None else res.text[:300]


async def copy_knowledge(
    app: FastAPI,
    registry_root: Path,
    *,
    source: str,
    raw_bundle: Any,
    token: str | None,
    lang: str = "ja",
    held_by: str | None = None,
) -> dict[str, Any]:
    """束を検査して、取り込み元 ``source`` の写しのデータセットを作る／作り直す。

    返り ``{dataset_id, created, version, triples, counts, dropped}``。失敗は :class:`CopyError`。
    同じ取り込み元で同時に 2 つは走らせない（2 つ目は 409）。待ち手が消えても公開まで走る。
    ``held_by`` は、呼び手が自分で付けた ``busy_kind`` の印の種類（取り込みのジョブが
    ``"import"`` を付けて呼ぶ）。その印だけは通す。
    """
    # Imported here: chat_attach pulls in most of the app.
    from asterism_api.chat_attach import make_http

    if not isinstance(source, str) or not _SOURCE_NAME.fullmatch(source):
        raise CopyError(400, "source は英数字と _ . - の名前です (64 文字まで)")

    # 同じ取り込み元は 1 つずつ。印は await を挟まずに付ける（2 つ目は必ず 409 になる）
    running: set[str] | None = getattr(app.state, "knowledge_copy_running", None)
    if running is None:
        running = set()
        app.state.knowledge_copy_running = running
    if source in running:
        raise CopyError(409, f"{source} の写しは作っている途中です")
    other = busy_kind(app, source)
    if other is not None and other != held_by:
        raise CopyError(409, f"{source} は別の作業 ({other}) が走っている途中です")
    running.add(source)

    async def run() -> dict[str, Any]:
        try:
            return await _copy(app, registry_root, source, raw_bundle, token, lang, make_http)
        finally:
            running.discard(source)

    # 待ち手（HTTP の要求）が消えても、取り込みと公開は最後まで走る。task はループが弱い参照しか
    # 持たないので、終わるまで app.state に置いておく
    tasks: set[asyncio.Task] | None = getattr(app.state, "knowledge_copy_tasks", None)
    if tasks is None:
        tasks = set()
        app.state.knowledge_copy_tasks = tasks
    task = asyncio.create_task(run())
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return await asyncio.shield(task)


def _prepare(raw_bundle: Any, lang: str) -> tuple[kb.Bundle, kb.Prepared, str, str, list]:
    """束の検査・語の束ねと照合・設計の組み立て（重いので、呼び手はスレッドに出す）。"""
    bundle = kb.parse_bundle(raw_bundle)
    prepared = kb.prepare_files(bundle)
    name = _dataset_name(bundle.title, lang)
    design = kb.design_document(bundle, name=name)
    upload = [
        (
            "files",
            (
                fname,
                json.dumps(rows, ensure_ascii=False, indent=2).encode("utf-8"),
                "application/json",
            ),
        )
        for fname, rows in prepared.files.items()
    ]
    return bundle, prepared, name, design, upload


async def _copy(
    app: FastAPI,
    registry_root: Path,
    source: str,
    raw_bundle: Any,
    token: str | None,
    lang: str,
    make_http: Any,
) -> dict[str, Any]:
    from asterism_api.chat_attach import _materialize_problems

    try:
        # 知見 2,000 × 語 900 で十数秒かかる。イベントループを塞がない
        bundle, prepared, name, design, upload = await asyncio.to_thread(
            _prepare, raw_bundle, lang
        )
    except kb.BundleError as exc:
        raise CopyError(422, f"知識の束が読めません: {exc}") from exc
    except (UnicodeError, RecursionError) as exc:  # 書き出せない文字・深すぎる入れ子
        raise CopyError(422, f"知識の束が読めません: {type(exc).__name__}") from exc
    if not prepared.files.get("claims.json"):
        # 公開済みの写しを空で置き換えない（resync_rows の「全部空は断る」と同じ）
        raise CopyError(422, "知識の束に知見が 1 件もありません")

    def mark(dataset_id: str, changes: dict[str, Any]) -> None:
        """``meta.knowledge_source`` に重ねる（前の ``version``・``copied_at`` は残す）。"""
        now = registry.load_meta(registry_root, dataset_id) or {}
        prev = now.get("knowledge_source")
        merged = {**(prev if isinstance(prev, dict) else {}), **changes}
        merged = {k: v for k, v in merged.items() if v is not None}
        registry.update_meta_atomic(registry_root, dataset_id, {"knowledge_source": merged})

    async with make_http(app, token, {}) as http:
        dataset_id = find_copy_dataset(registry_root, source)
        created = dataset_id is None
        body: dict[str, Any] = {"proposal_md": design, "dataset_name": name, "handles": []}
        if dataset_id:
            body["dataset_id"] = dataset_id
        res = await http.post("/api/materialize", json=body)
        if res.status_code != 200:
            raise CopyError(res.status_code, _detail_of(res))
        saved = res.json()
        problems = _materialize_problems(saved)
        new_id = (saved.get("dataset") or {}).get("id")
        if not isinstance(new_id, str) or not registry.is_valid_dataset_id(new_id):
            raise CopyError(
                500, {"error": "写しの設計を保存できませんでした", "issues": problems}
            )
        dataset_id = new_id
        # 途中で落ちても次の回が同じデータセットを見つけられるように、名前の印を先に付ける。
        # ここから公開までは source と公開中の版がずれうるので syncing を立てる
        mark(dataset_id, {"name": source, "format": kb.FORMAT, "syncing": True})
        if problems:
            # 組み込みの設計が検査に落ちるのは Asterism の不具合。取り込まずに理由を返す
            raise CopyError(
                500, {"error": "写しの設計が検査を通りませんでした", "issues": problems}
            )

        res = await http.post(f"/api/datasets/{dataset_id}/ingest", files=upload)
        if res.status_code != 202:
            raise CopyError(res.status_code, _detail_of(res))
        job = app.state.jobs.get(res.json()["job_id"])
        if job is None or job.task is None:
            raise CopyError(500, "ingest job not found")
        with contextlib.suppress(Exception):  # job.status / job.error が原因を持つ
            await asyncio.shield(job.task)
        if job.status != "done":
            raise CopyError(500, f"ingest {job.status}: {job.error}")
        triples = (job.result or {}).get("triple_count")

        now = registry.load_meta(registry_root, dataset_id)
        if now is None or now.get("status") in _GONE:
            raise CopyError(409, "取り込み中に引用対象から外されたため公開しませんでした")
        res = await http.post(f"/api/datasets/{dataset_id}/promote")
        if res.status_code != 200:
            raise CopyError(res.status_code, _detail_of(res))
        version = res.json().get("live_graph")

        mark(
            dataset_id,
            {
                "title": bundle.title,
                "exported_at": bundle.exported_at,
                "counts": dict(prepared.counts),
                "version": version,
                "copied_at": datetime.now(UTC).isoformat(),
                "syncing": None,
            },
        )
        return {
            "dataset_id": dataset_id,
            "created": created,
            "version": version,
            "triples": triples,
            "counts": dict(prepared.counts),
            "dropped": dict(prepared.dropped),
        }


def register_knowledge_copy_routes(app: FastAPI, cfg: Settings) -> None:
    """写す口を 2 つ登録する。``register_mcp_client_routes`` の後に呼ぶ
    （``app.state.mcp_registry`` を読む）。"""
    write_auth = [Depends(_require_write_auth(cfg))]

    async def _lang_of(request: Request) -> str:
        """本文 ``{lang}``（任意）。本文が無い・JSON でないときは日本語。"""
        raw = await request.body()
        if not raw.strip():
            return "ja"
        try:
            body = json.loads(raw)
        except ValueError as exc:
            raise HTTPException(400, "body must be JSON") from exc
        return "en" if isinstance(body, dict) and body.get("lang") == "en" else "ja"

    async def _copy(source: str, raw_bundle: Any, lang: str) -> dict[str, Any]:
        try:
            return await copy_knowledge(
                app,
                cfg.registry_root,
                source=source,
                raw_bundle=raw_bundle,
                token=cfg.api_token,
                lang=lang,
            )
        except CopyError as exc:
            raise HTTPException(exc.status, exc.detail) from exc

    @app.post("/api/knowledge-copies", dependencies=write_auth)
    async def knowledge_copies_create(request: Request) -> dict[str, Any]:
        """束を本文で受けて写す。本文 ``{source, bundle, lang?}``。"""
        _reject_if_content_length_exceeds(request, MAX_BUNDLE_BYTES)
        raw = await request.body()
        if len(raw) > MAX_BUNDLE_BYTES:  # Content-Length の無い（chunked の）本文
            raise HTTPException(413, "知識の束が大きすぎます")
        try:
            body = json.loads(raw)
        except ValueError as exc:
            raise HTTPException(400, "body must be JSON") from exc
        if not isinstance(body, dict) or not isinstance(body.get("source"), str):
            raise HTTPException(400, "body は {source, bundle} の JSON オブジェクトです")
        lang = "en" if body.get("lang") == "en" else "ja"
        return await _copy(body["source"].strip(), body.get("bundle"), lang)

    @app.post("/api/mcp-servers/{name}/knowledge-copy", dependencies=write_auth)
    async def mcp_servers_knowledge_copy(name: str, request: Request) -> dict[str, Any]:
        """登録した MCP サーバから束を取って写す（子を起こすので POST）。呼ぶ道具は設定
        ``sourceProvider.knowledge`` の 1 つだけで、引数は渡さない。"""
        reg = getattr(app.state, "mcp_registry", None)
        if reg is None:
            raise HTTPException(404, "MCP servers are only available in single-user mode")
        _reject_if_content_length_exceeds(request, 64 * 1024)
        lang = await _lang_of(request)
        server = reg.enabled_server_or_raise(name)
        tool = reg.provider().knowledge
        if not tool:
            raise HTTPException(
                409, "知識の束を出す道具 (sourceProvider.knowledge) が設定されていません"
            )
        result = await _call_or_http_error(
            reg.client, server, tool, {}, timeout_s=KNOWLEDGE_TIMEOUT_S
        )
        if len(result.text.encode("utf-8")) > MAX_BUNDLE_BYTES:
            raise HTTPException(413, "知識の束が大きすぎます")
        return {"server": name, **(await _copy(name, result.data, lang))}
