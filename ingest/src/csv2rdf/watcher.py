"""Phase 2 watcher: drop CSV -> auto reindex.

Watches ``<drop_root>/{papers,samples,curves}/`` and, on every settled CSV
write, runs the matching ingester, writes Turtle to ``<rdf_root>/{kind}/...``,
and POSTs that Turtle into the default graph on Oxigraph.

Design notes
~~~~~~~~~~~~

- **One process, one queue**. Watcher events are translated into ``Job``
  records and pushed onto an ``asyncio.Queue``. A single consumer task drains
  the queue serially. Serializing keeps memory and Oxigraph writers
  predictable; the bottleneck (rdflib serialization) is CPU-bound and
  parallelism would not help with a single Python process anyway.
- **Debounce**. ``watchfiles.awatch`` already batches bursts, but partial
  writes (a long ``cp`` of a 100 MB CSV) can still appear as "modified".
  We add a small settle delay per file (``DEFAULT_SETTLE_S``) — if a fresh
  change for the same path arrives during the wait, the timer resets.
- **Atomicity contract**. The upload API writes to ``<file>.tmp`` and then
  ``os.replace`` to the final name. ``watchfiles`` reports the rename as a
  single "added" event so the file is always complete when we read it.
  Manual ``cp`` from a shell may still race; the settle delay covers that.
- **Idempotency**. Re-ingesting the same CSV is a no-op for primary entity
  triples (Oxigraph dedupes by set semantics on IRI-keyed triples) and adds
  exactly one new ``sd:IngestionActivity`` node. See
  ``docs/architecture/phase05-decisions.md`` §2.2.
- **Default graph by default**. We POST every kind into Oxigraph's default
  graph so GRAPH-less SPARQL — the MIE example queries and the Phase 1 smoke
  tests — sees the data. ``WatcherConfig.use_default_graph=False`` opts back
  into per-kind named graphs (graph IRIs derived from ``graph_prefix``); that
  legacy mode then requires GRAPH-wrapped queries.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from watchfiles import Change, awatch

from csv2rdf.oxigraph_client import OxigraphClient
from csv2rdf.starrydata import (
    DEFAULT_ONTOLOGY,
    DEFAULT_RESOURCE,
    IngestConfig,
    IngestStats,
    ingest_curves,
    ingest_papers,
    ingest_samples,
)

logger = logging.getLogger(__name__)

# Kinds we accept. The drop directory layout is
# ``<drop_root>/<kind>/<filename>.csv`` for each kind below.
KINDS: Final[tuple[str, ...]] = ("papers", "samples", "curves")

_INGESTERS = {
    "papers": ingest_papers,
    "samples": ingest_samples,
    "curves": ingest_curves,
}

DEFAULT_SETTLE_S: Final[float] = 0.3
DEFAULT_GRAPH_PREFIX: Final[str] = (
    "https://kumagallium.github.io/csv2rdf-mcp/starrydata/graph/"
)


# ----------------------------------------------------------------------------
# Records
# ----------------------------------------------------------------------------


@dataclass
class WatcherConfig:
    drop_root: Path
    rdf_root: Path
    error_root: Path
    jobs_log: Path
    graph_prefix: str = DEFAULT_GRAPH_PREFIX
    settle_s: float = DEFAULT_SETTLE_S
    ingest_config: IngestConfig = field(default_factory=IngestConfig)
    # Post into Oxigraph's default graph so GRAPH-less SPARQL (MIE examples,
    # Phase 1 smoke) sees the data. Set False to keep per-kind named graphs.
    use_default_graph: bool = True

    def ensure_dirs(self) -> None:
        for kind in KINDS:
            (self.drop_root / kind).mkdir(parents=True, exist_ok=True)
            (self.rdf_root / kind).mkdir(parents=True, exist_ok=True)
            (self.error_root / kind).mkdir(parents=True, exist_ok=True)
        self.jobs_log.parent.mkdir(parents=True, exist_ok=True)

    def target_graph(self, kind: str) -> str | None:
        """Graph IRI to POST ``kind`` into, or ``None`` for the default graph."""
        if self.use_default_graph:
            return None
        return f"{self.graph_prefix}{kind}"

    def graph_iri(self, kind: str) -> str:
        """Named graph IRI for ``kind`` (used by ``target_graph`` and tests)."""
        return f"{self.graph_prefix}{kind}"


@dataclass
class Job:
    """Outcome of one ingest pass.

    Persisted as one JSON line in ``jobs.jsonl``. ``status`` is one of
    ``ok`` (ingester returned 0 rows in error and Oxigraph POST succeeded),
    ``partial`` (some rows failed but the Turtle was uploaded), or
    ``error`` (ingestion or upload raised).
    """

    kind: str
    csv_path: str
    ttl_path: str | None
    rows_in: int
    rows_ok: int
    rows_err: int
    triples_out: int
    bytes_uploaded: int
    status: str
    error: str | None
    started_at: str
    ended_at: str

    @classmethod
    def from_stats(
        cls,
        *,
        kind: str,
        csv_path: Path,
        ttl_path: Path | None,
        stats: IngestStats,
        bytes_uploaded: int,
        status: str,
        error: str | None,
        started_at: datetime,
        ended_at: datetime,
    ) -> Job:
        return cls(
            kind=kind,
            csv_path=str(csv_path),
            ttl_path=str(ttl_path) if ttl_path else None,
            rows_in=stats.rows_in,
            rows_ok=stats.rows_ok,
            rows_err=stats.rows_err,
            triples_out=stats.triples_out,
            bytes_uploaded=bytes_uploaded,
            status=status,
            error=error,
            started_at=started_at.isoformat(),
            ended_at=ended_at.isoformat(),
        )


JobListener = Callable[[Job], Awaitable[None]]


# ----------------------------------------------------------------------------
# Single-CSV ingest+upload
# ----------------------------------------------------------------------------


async def process_csv(
    kind: str,
    csv_path: Path,
    config: WatcherConfig,
    client: OxigraphClient,
) -> Job:
    """Run ingester for ``csv_path`` and upload the resulting Turtle."""
    if kind not in _INGESTERS:
        raise ValueError(f"unknown kind {kind!r}")

    started = datetime.now(UTC)
    ttl_path = config.rdf_root / kind / (csv_path.stem + ".ttl")
    err_path = config.error_root / kind / (csv_path.stem + ".jsonl")
    ingester = _INGESTERS[kind]

    # The ingester is sync (rdflib). Push it to a thread so we don't stall the
    # event loop while processing a 100 MB CSV.
    try:
        stats = await asyncio.to_thread(
            ingester,
            csv_path,
            ttl_path,
            config.ingest_config,
            err_path,
        )
    except Exception as exc:
        ended = datetime.now(UTC)
        return Job.from_stats(
            kind=kind,
            csv_path=csv_path,
            ttl_path=None,
            stats=IngestStats(started_at=started, ended_at=ended),
            bytes_uploaded=0,
            status="error",
            error=f"ingest failed: {exc!r}",
            started_at=started,
            ended_at=ended,
        )

    try:
        bytes_uploaded = await client.post_turtle(ttl_path, config.target_graph(kind))
    except Exception as exc:
        ended = datetime.now(UTC)
        return Job.from_stats(
            kind=kind,
            csv_path=csv_path,
            ttl_path=ttl_path,
            stats=stats,
            bytes_uploaded=0,
            status="error",
            error=f"upload failed: {exc!r}",
            started_at=started,
            ended_at=ended,
        )

    ended = datetime.now(UTC)
    status = "partial" if stats.rows_err else "ok"
    return Job.from_stats(
        kind=kind,
        csv_path=csv_path,
        ttl_path=ttl_path,
        stats=stats,
        bytes_uploaded=bytes_uploaded,
        status=status,
        error=None,
        started_at=started,
        ended_at=ended,
    )


# ----------------------------------------------------------------------------
# Watch loop
# ----------------------------------------------------------------------------


def _classify(path: Path, drop_root: Path) -> str | None:
    """Return the kind (papers/samples/curves) inferred from the path, or None."""
    if path.suffix.lower() != ".csv":
        return None
    if path.name.startswith("."):  # hidden files (.tmp, .DS_Store, etc.)
        return None
    try:
        rel = path.relative_to(drop_root)
    except ValueError:
        return None
    if len(rel.parts) < 2:
        return None
    kind = rel.parts[0]
    if kind not in KINDS:
        return None
    return kind


async def _settle(path: Path, settle_s: float) -> bool:
    """Wait until ``path`` stops changing for ``settle_s`` seconds.

    Returns False if the file disappears while we wait.
    """
    deadline_size = -1
    deadline_mtime = 0.0
    while True:
        try:
            stat = path.stat()
        except FileNotFoundError:
            return False
        if stat.st_size == deadline_size and stat.st_mtime == deadline_mtime:
            return True
        deadline_size = stat.st_size
        deadline_mtime = stat.st_mtime
        await asyncio.sleep(settle_s)


async def _append_job(job: Job, log_path: Path) -> None:
    line = json.dumps(asdict(job), ensure_ascii=False) + "\n"
    await asyncio.to_thread(_append_line_sync, log_path, line)


def _append_line_sync(log_path: Path, line: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(line)


async def watch(
    config: WatcherConfig,
    client: OxigraphClient,
    *,
    on_job: JobListener | None = None,
    stop_event: asyncio.Event | None = None,
    events_source: AsyncIterator[set[tuple[Change, str]]] | None = None,
) -> None:
    """Run the watch loop forever (or until ``stop_event`` is set).

    ``events_source`` is for tests: pass a pre-built async iterator yielding
    watchfiles-style change sets to drive the loop deterministically.
    Production calls leave it None and ``watchfiles.awatch`` is used.
    """
    config.ensure_dirs()
    stop = stop_event or asyncio.Event()

    if events_source is None:
        # ``watchfiles.awatch`` already debounces: it waits for the change
        # stream to go quiet before yielding a batch, so by the time we see
        # a path it's typically settled. The per-file ``_settle`` call below
        # is the belt-and-suspenders check for slow ``cp`` of large CSVs.
        debounce_ms = max(50, int(config.settle_s * 1000))
        watch_dirs = [str(config.drop_root / k) for k in KINDS]
        events_source = awatch(
            *watch_dirs, stop_event=stop, debounce=debounce_ms
        )

    async for batch in events_source:
        # Dedupe within a single batch: one upload typically yields multiple
        # events (added .tmp / removed .tmp / added .csv) for the same path.
        ready: dict[Path, str] = {}
        for change_type, raw_path in batch:
            if change_type == Change.deleted:
                continue
            path = Path(raw_path)
            kind = _classify(path, config.drop_root)
            if kind is None:
                continue
            ready[path] = kind

        for path, kind in ready.items():
            if not await _settle(path, max(0.05, config.settle_s)):
                logger.info("watcher: file disappeared before settle: %s", path)
                continue
            logger.info("watcher: processing %s (kind=%s)", path, kind)
            job = await process_csv(kind, path, config, client)
            await _append_job(job, config.jobs_log)
            if on_job is not None:
                await on_job(job)
            logger.info(
                "watcher: %s status=%s rows=%s/%s triples=%s",
                path.name,
                job.status,
                job.rows_ok,
                job.rows_in,
                job.triples_out,
            )

        if stop.is_set():
            return


# ----------------------------------------------------------------------------
# CLI entry point (for ad-hoc runs outside docker-compose)
# ----------------------------------------------------------------------------


def _main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        prog="csv2rdf-watcher",
        description="Watch a drop directory and ingest CSVs into Oxigraph.",
    )
    p.add_argument("--drop-root", type=Path, default=Path("data/sources/csv"))
    p.add_argument("--rdf-root", type=Path, default=Path("data/sources/rdf/starrydata"))
    p.add_argument(
        "--error-root", type=Path, default=Path("data/sources/errors/starrydata")
    )
    p.add_argument("--jobs-log", type=Path, default=Path("data/sources/jobs.jsonl"))
    p.add_argument("--oxigraph-url", default="http://localhost:7878")
    p.add_argument("--graph-prefix", default=DEFAULT_GRAPH_PREFIX)
    p.add_argument(
        "--named-graphs",
        action="store_true",
        help="POST each kind into a per-kind named graph instead of the "
        "default graph (legacy mode; requires GRAPH-wrapped SPARQL).",
    )
    p.add_argument("--ontology", default=DEFAULT_ONTOLOGY)
    p.add_argument("--resource", default=DEFAULT_RESOURCE)
    p.add_argument("--settle-s", type=float, default=DEFAULT_SETTLE_S)
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    cfg = WatcherConfig(
        drop_root=args.drop_root,
        rdf_root=args.rdf_root,
        error_root=args.error_root,
        jobs_log=args.jobs_log,
        graph_prefix=args.graph_prefix,
        use_default_graph=not args.named_graphs,
        settle_s=args.settle_s,
        ingest_config=IngestConfig(
            ontology_iri=args.ontology,
            resource_iri=args.resource,
        ),
    )

    from csv2rdf.oxigraph_client import OxigraphConfig

    async def runner() -> None:
        async with OxigraphClient(OxigraphConfig(base_url=args.oxigraph_url)) as client:
            await watch(cfg, client)

    asyncio.run(runner())
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
