"""台帳の確認済みの関係を、ストアの正本に置く口（PR 14e-1b・``pair-relations.md`` §7）。

中身は :mod:`asterism.confirmed_store`（ingest・決定論）。ここは、同じ取り込み元の作業と
排他になった状態（呼び手が ``busy_kind`` の印を持っている）で、話の確定と同じ排他
（``app.state.themes_lock``）を取って、ストアを台帳に合わせ、結果を台帳に写して保存する。

置く段の失敗は、呼び手（review・取り込みジョブ）を失敗にしない: 例外は握りつぶして
``{placed: 0, removed: 0, failed: [], error: True}`` を返す（台帳の確認は残る）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from asterism import confirmed_store as cs
from asterism import knowledge_bundle as kb
from asterism import pair_relations as pr
from fastapi import FastAPI

from asterism_api import registry

logger = logging.getLogger(__name__)

__all__ = ["catch_up", "pair_keys", "sync_ledger"]

#: 追い付きで回す最大の回数（1 回 50 件）。
_MAX_ROUNDS = 40
_EMPTY = {"placed": 0, "removed": 0, "failed": []}


def pair_keys(keys: Iterable[str]) -> list[str]:
    """review の鍵（組の鍵・取り込み元の行の鍵 ``o:<from>|<rel>|<to>``）→ 組の鍵。"""
    out: list[str] = []
    for k in keys:
        if k.startswith("o:"):
            parts = k[2:].split("|")
            if len(parts) == 3:
                out.append("|".join(sorted((parts[0], parts[2]))))
                continue
        out.append(k)
    return list(dict.fromkeys(out))


def _lock(app: FastAPI) -> asyncio.Lock:
    lock = getattr(app.state, "themes_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        app.state.themes_lock = lock
    return lock


async def sync_ledger(
    app: FastAPI,
    root: Path,
    ledger: dict,
    copy: kb.KnowledgeCopy,
    dataset_id: str,
    *,
    focus: Iterable[str] = (),
    save: bool = True,
) -> tuple[dict, dict]:
    """1 回分（最大 50 件）を置いて、台帳に結果を写して保存する。
    返りは ``(台帳, 口の返りの store)``。ストアが無い・何か落ちたときは、台帳をそのまま・
    ``store`` は ``error: True``。"""
    client = getattr(app.state, "client", None)
    if client is None:
        return ledger, dict(_EMPTY)
    meta = registry.load_meta(root, dataset_id) or {}
    graph = meta.get("live_graph") or None
    try:
        async with _lock(app):
            new, rep = await cs.sync(
                client, root, ledger, copy, claim_graph=graph, focus=list(focus),
                title=(meta.get("knowledge_source") or {}).get("title"),
            )
        if save and (new != ledger):
            await asyncio.to_thread(
                pr.save_ledger, root, new, at=datetime.now(UTC).isoformat()
            )
        out = rep.summary()
        out["remaining"] = rep.remaining
        return new, out
    except Exception:
        logger.warning("knowledge store: could not place confirmed relations", exc_info=True)
        return ledger, {**_EMPTY, "failed": [], "error": True}


async def catch_up(app: FastAPI, root: Path, source: str, dataset_id: str) -> dict:
    """台帳の確認済みのうち、ストアに置いていない件（14e-1 で先に付けた確認）を、置き終わるまで
    （1 回 50 件を最大 :data:`_MAX_ROUNDS` 回）追い付かせる。呼び手が取り込み元を押さえている前提。
    落ちても例外にしない（返りの ``error``）。"""
    total = {"placed": 0, "removed": 0, "failed": []}
    try:
        src = registry.source_dir(root, dataset_id)
        if src is None:
            return dict(_EMPTY)
        copy = await asyncio.to_thread(kb.read_copy, src)
        ledger = await asyncio.to_thread(pr.load_ledger, root, source)
    except Exception:
        logger.warning("knowledge store: could not read the ledger to catch up", exc_info=True)
        return {**_EMPTY, "failed": [], "error": True}
    for _ in range(_MAX_ROUNDS):
        ledger, out = await sync_ledger(app, root, ledger, copy, dataset_id)
        total["placed"] += out["placed"]
        total["removed"] += out["removed"]
        total["failed"] = [*total["failed"], *out["failed"]]
        if out.get("error"):
            return {**total, "error": True}
        if not out.get("remaining") or not out["placed"]:
            break
    return total
