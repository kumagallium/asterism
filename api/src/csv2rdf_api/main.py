"""FastAPI upload + status surface for csv2rdf-mcp Phase 2.

Endpoints
~~~~~~~~~

``POST /upload/{kind}`` (kind in {papers, samples, curves})
    Accepts a multipart ``file=`` part, writes it atomically into
    ``<drop_root>/<kind>/<filename>``, and returns the saved path. The
    background watcher picks the file up and triggers an ingest pass.

``GET /jobs?limit=N``
    Tail of ``jobs.jsonl``. Default 50 most recent.

``GET /health``
    Liveness + Oxigraph reachability.

The watcher runs inside this process as a background asyncio task wired up
via the FastAPI ``lifespan`` callback. We deliberately keep both surfaces in
the same process so they share an OxigraphClient pool and a single jsonl
log writer.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Final

from csv2rdf.oxigraph_client import OxigraphClient, OxigraphConfig
from csv2rdf.starrydata import DEFAULT_ONTOLOGY, DEFAULT_RESOURCE, IngestConfig
from csv2rdf.watcher import (
    DEFAULT_GRAPH_PREFIX,
    DEFAULT_SETTLE_S,
    KINDS,
    WatcherConfig,
    watch,
)
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi import Path as PathParam
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# Restrict uploaded filenames to a safe subset to avoid directory traversal
# (``..`` segments, absolute paths, NULs). We also reject names without a
# ``.csv`` suffix so the watcher's ``_classify`` actually fires.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,128}\.csv$")


# ----------------------------------------------------------------------------
# Settings (env-driven)
# ----------------------------------------------------------------------------


class Settings:
    """Resolve from environment with sensible compose defaults."""

    def __init__(self, env: dict[str, str] | None = None) -> None:
        e = env if env is not None else os.environ
        self.drop_root = Path(e.get("CSV2RDF_DROP_ROOT", "/data/sources/csv"))
        self.rdf_root = Path(
            e.get("CSV2RDF_RDF_ROOT", "/data/sources/rdf/starrydata")
        )
        self.error_root = Path(
            e.get("CSV2RDF_ERROR_ROOT", "/data/sources/errors/starrydata")
        )
        self.jobs_log = Path(e.get("CSV2RDF_JOBS_LOG", "/data/sources/jobs.jsonl"))
        self.oxigraph_url = e.get("CSV2RDF_OXIGRAPH_URL", "http://oxigraph:7878")
        self.graph_prefix = e.get("CSV2RDF_GRAPH_PREFIX", DEFAULT_GRAPH_PREFIX)
        self.ontology_iri = e.get("CSV2RDF_ONTOLOGY_IRI", DEFAULT_ONTOLOGY)
        self.resource_iri = e.get("CSV2RDF_RESOURCE_IRI", DEFAULT_RESOURCE)
        self.settle_s = float(e.get("CSV2RDF_SETTLE_S", DEFAULT_SETTLE_S))


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def _validate_kind(kind: str) -> str:
    if kind not in KINDS:
        raise HTTPException(400, f"kind must be one of {KINDS}, got {kind!r}")
    return kind


def _validate_name(name: str) -> str:
    if not _SAFE_NAME.fullmatch(name):
        raise HTTPException(
            400,
            "filename must match [A-Za-z0-9._-]+.csv (max 128 chars)",
        )
    return name


async def _save_upload(file: UploadFile, dest: Path, chunk_size: int = 1 << 20) -> int:
    """Stream ``file`` to ``dest`` atomically via a sibling ``.tmp`` file."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    total = 0
    # We do the actual writes on a thread because UploadFile.read() is async
    # but file.write is sync.
    fh = await asyncio.to_thread(tmp.open, "wb")
    try:
        while True:
            chunk = await file.read(chunk_size)
            if not chunk:
                break
            await asyncio.to_thread(fh.write, chunk)
            total += len(chunk)
    finally:
        await asyncio.to_thread(fh.close)
    # os.replace is atomic on POSIX; the watcher sees a single rename event
    # rather than partial writes.
    await asyncio.to_thread(os.replace, tmp, dest)
    return total


def _tail_jsonl(path: Path, limit: int) -> list[dict[str, object]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        lines = fh.readlines()
    out: list[dict[str, object]] = []
    for raw in lines[-limit:]:
        try:
            out.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return out


# ----------------------------------------------------------------------------
# App builder
# ----------------------------------------------------------------------------


def build_app(
    settings: Settings | None = None,
    *,
    oxigraph_client: OxigraphClient | None = None,
    start_watcher: bool = True,
) -> FastAPI:
    cfg = settings or Settings()
    watcher_cfg = WatcherConfig(
        drop_root=cfg.drop_root,
        rdf_root=cfg.rdf_root,
        error_root=cfg.error_root,
        jobs_log=cfg.jobs_log,
        graph_prefix=cfg.graph_prefix,
        settle_s=cfg.settle_s,
        ingest_config=IngestConfig(
            ontology_iri=cfg.ontology_iri,
            resource_iri=cfg.resource_iri,
        ),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        watcher_cfg.ensure_dirs()
        client = oxigraph_client or OxigraphClient(
            OxigraphConfig(base_url=cfg.oxigraph_url)
        )
        stop = asyncio.Event()
        task: asyncio.Task[None] | None = None
        if start_watcher:
            task = asyncio.create_task(
                watch(watcher_cfg, client, stop_event=stop), name="csv2rdf-watcher"
            )
        app.state.client = client
        app.state.watcher_cfg = watcher_cfg
        app.state.watcher_task = task
        try:
            yield
        finally:
            stop.set()
            if task is not None:
                try:
                    await asyncio.wait_for(task, timeout=2.0)
                except (TimeoutError, asyncio.CancelledError):
                    task.cancel()
            if oxigraph_client is None:
                await client.aclose()

    app = FastAPI(
        title="csv2rdf-mcp upload API",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get("/health")
    async def health() -> JSONResponse:
        client: OxigraphClient = app.state.client
        ok = await client.ping()
        return JSONResponse(
            {"status": "ok" if ok else "degraded", "oxigraph": ok},
            status_code=200 if ok else 503,
        )

    @app.post("/upload/{kind}")
    async def upload(
        file: UploadFile,
        kind: str = PathParam(..., description="papers | samples | curves"),
    ) -> dict[str, object]:
        _validate_kind(kind)
        if file.filename is None:
            raise HTTPException(400, "missing filename")
        name = _validate_name(file.filename)
        dest = cfg.drop_root / kind / name
        size = await _save_upload(file, dest)
        return {
            "kind": kind,
            "saved_to": str(dest),
            "bytes": size,
            "queued": True,
        }

    @app.get("/jobs")
    async def jobs(limit: int = 50) -> dict[str, object]:
        if not 1 <= limit <= 500:
            raise HTTPException(400, "limit must be in [1, 500]")
        entries = _tail_jsonl(cfg.jobs_log, limit)
        return {"count": len(entries), "jobs": entries}

    return app


# ----------------------------------------------------------------------------
# CLI / uvicorn entry point
# ----------------------------------------------------------------------------


_DEFAULT_HOST: Final[str] = "0.0.0.0"
_DEFAULT_PORT: Final[int] = 8080


def _main(argv: list[str] | None = None) -> int:
    import argparse

    import uvicorn

    p = argparse.ArgumentParser(prog="csv2rdf-api")
    p.add_argument("--host", default=_DEFAULT_HOST)
    p.add_argument("--port", type=int, default=_DEFAULT_PORT)
    p.add_argument("--log-level", default="info")
    args = p.parse_args(argv)

    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(message)s")
    uvicorn.run(
        "csv2rdf_api.main:build_app",
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        factory=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
