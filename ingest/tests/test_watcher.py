"""Watcher loop tests — drive it with synthetic events + httpx.MockTransport.

We bypass the real ``watchfiles.awatch`` by passing an ``events_source``
async iterator built from canned change sets. This keeps the test
deterministic and free of inotify/FSEvents quirks.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from watchfiles import Change

from csv2rdf.oxigraph_client import OxigraphClient, OxigraphConfig
from csv2rdf.watcher import (
    DEFAULT_GRAPH_PREFIX,
    Job,
    WatcherConfig,
    _classify,
    process_csv,
    watch,
)

PAPER_CSV = (
    "SID,DOI,URL,issued,author,title,container_title,container_title_short,"
    "volume,issue,page,ISSN,publisher,project_names,created_at\n"
    '1,10.1/x,http://x,"{""date_parts"":[[2020]]}",'
    '"[{""given"":""A"",""family"":""B""}]",'
    '"""Title""","""Journal""","""J""",1,1,"""10-20""",0000-0000,Pub,'
    '"[""P""]",Mon Jan 1 2024 00:00:00\n'
)


def _make_config(tmp: Path) -> WatcherConfig:
    return WatcherConfig(
        drop_root=tmp / "csv",
        rdf_root=tmp / "rdf",
        error_root=tmp / "errors",
        jobs_log=tmp / "jobs.jsonl",
        settle_s=0.0,  # no debounce so tests don't sleep
    )


def _make_client(captured: dict[str, object]) -> OxigraphClient:
    def handler(request: httpx.Request) -> httpx.Response:
        captured["graph_param"] = request.url.params.get("graph")
        captured["query"] = request.url.query.decode()
        captured["body_len"] = len(bytes(request.content))
        return httpx.Response(204)

    inner = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    )
    return OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner)


# ----------------------------------------------------------------------------
# Pure helpers
# ----------------------------------------------------------------------------


def test_classify_papers(tmp_path: Path) -> None:
    drop = tmp_path / "csv"
    (drop / "papers").mkdir(parents=True)
    p = drop / "papers" / "foo.csv"
    assert _classify(p, drop) == "papers"


def test_classify_rejects_unknown_kind(tmp_path: Path) -> None:
    drop = tmp_path / "csv"
    (drop / "junk").mkdir(parents=True)
    p = drop / "junk" / "foo.csv"
    assert _classify(p, drop) is None


def test_classify_rejects_non_csv(tmp_path: Path) -> None:
    drop = tmp_path / "csv"
    (drop / "papers").mkdir(parents=True)
    p = drop / "papers" / "foo.txt"
    assert _classify(p, drop) is None


def test_classify_rejects_hidden(tmp_path: Path) -> None:
    drop = tmp_path / "csv"
    (drop / "papers").mkdir(parents=True)
    p = drop / "papers" / ".foo.csv.tmp.csv"
    assert _classify(p, drop) is None


# ----------------------------------------------------------------------------
# process_csv: end-to-end without watchfiles
# ----------------------------------------------------------------------------


async def test_process_csv_writes_turtle_and_uploads(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    cfg.ensure_dirs()
    csv_path = cfg.drop_root / "papers" / "tiny.csv"
    csv_path.write_text(PAPER_CSV, encoding="utf-8")

    captured: dict[str, object] = {}
    async with _make_client(captured) as client:
        job = await process_csv("papers", csv_path, cfg, client)

    assert job.status == "ok"
    assert job.rows_in == 1
    assert job.rows_ok == 1
    assert job.rows_err == 0
    assert job.triples_out > 0
    assert job.bytes_uploaded > 0
    assert job.error is None

    ttl = cfg.rdf_root / "papers" / "tiny.ttl"
    assert ttl.exists()
    text = ttl.read_text(encoding="utf-8")
    assert "sd:Paper" in text or "Paper" in text
    # Default config posts into the default graph (no ``graph`` param).
    assert captured["graph_param"] is None
    assert "default" in str(captured["query"])


async def test_process_csv_named_graph_opt_in(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    cfg.use_default_graph = False  # legacy per-kind named graph
    cfg.ensure_dirs()
    csv_path = cfg.drop_root / "papers" / "tiny.csv"
    csv_path.write_text(PAPER_CSV, encoding="utf-8")

    captured: dict[str, object] = {}
    async with _make_client(captured) as client:
        job = await process_csv("papers", csv_path, cfg, client)

    assert job.status == "ok"
    assert captured["graph_param"] == f"{DEFAULT_GRAPH_PREFIX}papers"


async def test_process_csv_marks_error_on_upload_failure(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    cfg.ensure_dirs()
    csv_path = cfg.drop_root / "papers" / "tiny.csv"
    csv_path.write_text(PAPER_CSV, encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    inner = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    )
    async with OxigraphClient(
        OxigraphConfig(base_url="http://test", retries=1, backoff_s=0.0),
        client=inner,
    ) as client:
        job = await process_csv("papers", csv_path, cfg, client)

    assert job.status == "error"
    assert job.bytes_uploaded == 0
    assert job.error is not None
    assert "upload failed" in job.error


async def test_process_csv_rejects_unknown_kind(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    cfg.ensure_dirs()
    csv_path = cfg.drop_root / "papers" / "x.csv"
    csv_path.write_text(PAPER_CSV, encoding="utf-8")
    captured: dict[str, object] = {}
    async with _make_client(captured) as client:
        with pytest.raises(ValueError, match="unknown kind"):
            await process_csv("junk", csv_path, cfg, client)


# ----------------------------------------------------------------------------
# Watch loop: drive with synthetic events_source
# ----------------------------------------------------------------------------


async def _one_shot_events(
    pairs: list[tuple[Change, str]],
) -> AsyncIterator[set[tuple[Change, str]]]:
    yield set(pairs)
    # Block forever so the watcher loop must be terminated via stop_event.
    await asyncio.sleep(3600)


async def test_watch_processes_added_csv(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    cfg.ensure_dirs()
    csv_path = cfg.drop_root / "papers" / "tiny.csv"
    csv_path.write_text(PAPER_CSV, encoding="utf-8")

    captured: dict[str, object] = {}
    seen_jobs: list[Job] = []

    async def listener(job: Job) -> None:
        seen_jobs.append(job)

    stop = asyncio.Event()
    async with _make_client(captured) as client:
        task = asyncio.create_task(
            watch(
                cfg,
                client,
                on_job=listener,
                stop_event=stop,
                events_source=_one_shot_events([(Change.added, str(csv_path))]),
            )
        )
        # Wait until the job is observed, then stop.
        for _ in range(100):
            if seen_jobs:
                break
            await asyncio.sleep(0.02)
        stop.set()
        # Allow the loop one more iteration to honour stop.
        await asyncio.sleep(0.05)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert len(seen_jobs) == 1
    assert seen_jobs[0].status == "ok"
    assert seen_jobs[0].kind == "papers"

    # jobs.jsonl was appended
    assert cfg.jobs_log.exists()
    record = json.loads(cfg.jobs_log.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert record["status"] == "ok"
    assert record["rows_in"] == 1
