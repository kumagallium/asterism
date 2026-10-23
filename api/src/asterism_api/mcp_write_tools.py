"""Write tools for the app's own ``/mcp`` endpoint (local / single-user mode).

The standalone MCP server (``asterism_mcp.server.build_server``) stays
read-only: it is the security boundary in front of the hosted store. A tool
that grows a dataset lives only here, on the endpoint the app mounts for its
one local user, and calls the same append core as ``POST /api/datasets/{id}/append``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from asterism.oxigraph_client import OxigraphClient

from asterism_api import registry


def register_write_tools(
    mcp: Any,
    *,
    registry_root: Path,
    get_client: Callable[[], OxigraphClient],
    get_rebuilder: Callable[[], Any] | None = None,
    log_job: Callable[[dict[str, object]], None] | None = None,
    get_app: Callable[[], Any] | None = None,
    token: str | None = None,
) -> None:
    """Register the write tools on a FastMCP ``mcp``.

    ``get_client`` / ``get_rebuilder`` are called at tool-call time, because the
    app's client only exists after its lifespan has started. ``get_app`` / ``token``
    are for ``resync_rows``, which drives the app's own ingest / promote routes over
    ASGI (no new key: ``token`` is the app's existing write token, and in local mode
    ``LoopbackTokenInjector`` adds it for the loopback ASGI client anyway).
    """

    @mcp.tool(
        name="append_rows",
        description=(
            "Append new rows to a PROMOTED dataset's live graph. Generic: any "
            "dataset whose design reads JSON array files. The dataset's design "
            "is not changed; a row identical to one already sent is not "
            "added again (the batch is idempotent); a changed row with the same "
            "id is added alongside — replacing is not supported. The first design is approved by a "
            "person in the app (attach a file in the chat); this tool only grows it. "
            "``file_name`` is the design's source name (e.g. judgments.json); "
            "``rows`` is a list of flat JSON objects, one per record; "
            "``child_files`` maps other source names (e.g. rule_terms.json) to "
            "their rows, appended in the same call. Re-sending the same rows "
            "is a no-op (idempotent_replay). Failures are returned as "
            "{dataset_id, error, status}, never raised."
        ),
    )
    async def _append_rows(
        dataset_id: str,
        file_name: str,
        rows: list[dict[str, Any]],
        child_files: dict[str, list[dict[str, Any]]] | None = None,
    ) -> dict[str, Any]:
        # Imported here: main.py is large and imports this package's siblings.
        from asterism_api.main import AppendError, _append_batch_to_dataset

        def fail(status: int, detail: str) -> dict[str, Any]:
            return {"dataset_id": dataset_id, "error": detail, "status": status}

        if not _valid_id(dataset_id):
            return fail(400, f"invalid dataset id {dataset_id!r}")
        files: list[tuple[str, list[dict[str, Any]]]] = [(file_name, rows)]
        files.extend((child_files or {}).items())
        lowered = [n.lower() for n, _ in files]
        if len(set(lowered)) != len(lowered):
            return fail(400, "file_name and child_files must name different files")
        for name, body in files:
            if not name.lower().endswith(".json"):
                return fail(400, f"file name {name!r} must end with .json")
            if not body:
                return fail(400, f"{name!r} has no rows to append")
        batch = [
            (name, json.dumps(body, ensure_ascii=False, indent=2).encode("utf-8"))
            for name, body in files
        ]
        names = ", ".join(name for name, _ in batch)
        try:
            result = await _append_batch_to_dataset(
                registry_root,
                get_client(),
                dataset_id,
                batch,
                rebuilder=get_rebuilder() if get_rebuilder else None,
            )
        except AppendError as exc:
            if log_job:
                log_job(
                    {
                        "kind": "append",
                        "dataset_id": dataset_id,
                        "file": names,
                        "status": "error",
                        "error": exc.detail,
                        "ended_at": datetime.now(UTC).isoformat(),
                    }
                )
            return fail(exc.status, exc.detail)
        if log_job:
            log_job(
                {
                    "kind": "append",
                    "dataset_id": dataset_id,
                    "file": names,
                    "status": "ok",
                    "triples_in_batch": result["triples_in_batch"],
                    "append_seq": result["append_seq"],
                    "ended_at": datetime.now(UTC).isoformat(),
                }
            )
        return {
            k: result.get(k)
            for k in (
                "dataset_id",
                "live_graph",
                "triples_in_batch",
                "append_seq",
                "idempotent_replay",
                "advisories",
            )
        }

    # 後で要ること（source_origins が main に入ったら）: 全置換のあと、置き換えたファイル名の
    # source_origins の行だけ sha256 を作り直す（home は付けない）。
    @mcp.tool(
        name="resync_rows",
        description=(
            "Replace a PUBLISHED dataset's whole source set and rebuild its citable graph "
            "(re-ingest + promote) — the idempotent full resync. Use when rows "
            "changed or disappeared at the origin; append_rows only adds. "
            "``files`` maps each source name (must end with .json, e.g. "
            "judgments.json) to ALL its rows (a flat JSON object per record; an "
            "empty list means that kind has 0 rows). Files not listed are "
            "dropped from the source. IRIs stay the same because they come from "
            "the row ids; the old version is retired. During the rebuild "
            "append_rows answers 409 until promote completes. The first design is "
            "approved by a person in the app; an unpublished dataset, or all-empty "
            "files, is refused. The rebuild runs to completion even if this call "
            "is cancelled; call again to see the result. Returns "
            "{dataset_id, version, triples, promoted}; failures are returned as "
            "{dataset_id, error, status}, never raised."
        ),
    )
    async def _resync_rows(
        dataset_id: str,
        files: dict[str, list[dict[str, Any]]],
    ) -> dict[str, Any]:
        from asterism_api.chat_attach import make_http

        def fail(status: int, detail: Any) -> dict[str, Any]:
            if not isinstance(detail, str):
                issues = detail.get("issues") if isinstance(detail, dict) else None
                detail = (
                    "; ".join(str(i) for i in issues)
                    if issues
                    else json.dumps(detail, ensure_ascii=False)
                )
            return {"dataset_id": dataset_id, "error": detail, "status": status}

        if not _valid_id(dataset_id):
            return fail(400, f"invalid dataset id {dataset_id!r}")
        if not files:
            return fail(400, "files must name at least one .json source")
        lowered = [n.lower() for n in files]
        if len(set(lowered)) != len(lowered):
            return fail(400, "files must name different files")
        for name in files:
            if not name.lower().endswith(".json"):
                return fail(400, f"file name {name!r} must end with .json")
        if not any(files.values()):
            return fail(400, "every file is empty; refusing to empty a published dataset")
        if get_app is None:
            return fail(500, "resync_rows is not available on this endpoint")
        data = registry.load_dataset(registry_root, dataset_id)
        if data is None:
            return fail(404, f"dataset {dataset_id!r} not found")
        meta = data["meta"]
        if not meta.get("promoted"):
            return fail(
                409,
                "publish the dataset first; the first design is approved by a person "
                "in the app (attach a file in the chat)",
            )
        if meta.get("status") in ("retracted", "deleted"):
            return fail(409, f"dataset is {meta.get('status')}; reinstate it before resyncing")
        upload = [
            (
                "files",
                (
                    name,
                    json.dumps(body, ensure_ascii=False, indent=2).encode("utf-8"),
                    "application/json",
                ),
            )
            for name, body in files.items()
        ]
        app = get_app()

        async def run() -> dict[str, Any]:
            # ingest → 再確認 → promote を 1 つにまとめる（待ち手が消えても公開まで走る）
            async with make_http(app, token, {}) as http:
                res = await http.post(f"/api/datasets/{dataset_id}/ingest", files=upload)
                if res.status_code != 202:
                    return fail(res.status_code, _detail_of(res))
                job = app.state.jobs.get(res.json()["job_id"])
                if job is None or job.task is None:
                    return fail(500, "ingest job not found")
                with contextlib.suppress(Exception):  # job.status / job.error carry the cause
                    await asyncio.shield(job.task)
                if job.status != "done":
                    return fail(500, f"ingest {job.status}: {job.error}")
                triples = (job.result or {}).get("triple_count")
                # ingest が長いあいだに retract されうる
                now = registry.load_dataset(registry_root, dataset_id)
                if now is None or (now["meta"].get("status") in ("retracted", "deleted")):
                    return {
                        "dataset_id": dataset_id,
                        "error": "取り込み中に引用対象から外されたため公開しませんでした",
                        "status": 409,
                        "ingested": True,
                        "promoted": False,
                    }
                res = await http.post(f"/api/datasets/{dataset_id}/promote")
                if res.status_code != 200:
                    return {
                        **fail(res.status_code, _detail_of(res)),
                        "ingested": True,
                        "promoted": False,
                        "hint": (
                            "ingest は終わっている。"
                            f"POST /api/datasets/{dataset_id}/promote を手で呼ぶか、"
                            "resync_rows をもう一度"
                        ),
                    }
                body = res.json()
            return {
                "dataset_id": dataset_id,
                "version": body.get("live_graph"),
                "triples": triples,
                "promoted": True,
            }

        # 待ち手（MCP 呼び出し）がキャンセルされても、取り込みと公開は最後まで走る
        task = asyncio.create_task(run())
        return await asyncio.shield(task)


def _valid_id(dataset_id: str) -> bool:
    """id は URL に埋めるので、registry の id 形（[a-z0-9-]）以外は断る。"""
    return registry.is_valid_dataset_id(dataset_id)


def _detail_of(res: httpx.Response) -> Any:
    try:
        d = res.json().get("detail")
    except Exception:
        return res.text[:300]
    if isinstance(d, dict) and isinstance(d.get("message"), str) and not d.get("issues"):
        return d["message"]
    return d if d is not None else res.text[:300]
