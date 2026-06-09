"""FastAPI upload + status surface for asterism Phase 2.

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
import contextlib
import json
import logging
import os
import re
import shutil
import tempfile
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from asterism import substrate
from asterism.datasets import load_dataset
from asterism.ontology_projection import (
    STANDARD_PREFIXES,
    extract_prefixes,
    project_model_yaml,
)
from asterism.oxigraph_client import OxigraphClient, OxigraphConfig
from asterism.query_tools import QueryToolError, parse_query_tools
from asterism.starrydata import IngestConfig
from asterism.watcher import (
    DEFAULT_GRAPH_PREFIX,
    DEFAULT_SETTLE_S,
    KINDS,
    WatcherConfig,
    watch,
)
from asterism_step0.inspect import inspect_source_set, render_markdown
from asterism_step0.materialize import materialize_schema
from asterism_step0.propose import AnthropicLLMClient, LLMClient, propose_schema
from asterism_step0.refine import refine_schema
from asterism_step0.validate import SchemaBundle, validate_schema
from fastapi import FastAPI, File, Form, Header, HTTPException, Query, Response, UploadFile
from fastapi import Path as PathParam
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from asterism_api import registry
from asterism_api.jobs import JobManager


class RefineRequest(BaseModel):
    """Body for POST /api/refine: the current schema + review comments."""

    schema_md: str
    comments: list[str]


class MaterializeRequest(BaseModel):
    """Body for POST /api/materialize: the proposal/refine Markdown to split."""

    proposal_md: str
    dataset_name: str = "dataset"
    # When true (default), persist the bundle to the registry so it shows up in
    # the Gallery. Set false for a throwaway validation-only run.
    persist: bool = True


class SparqlRequest(BaseModel):
    """Body for POST /api/sparql: a read-only SPARQL query (escape hatch)."""

    query: str


class QueryToolBody(BaseModel):
    """Body for POST /api/datasets/{id}/tools: one declared, parameterized,
    read-only SPARQL tool (same shape as a datasets/<name>/query_tools.yaml entry).
    Validated server-side via asterism.query_tools.parse_query_tools (read-only +
    safe binding) before it is persisted — saving IS the human-vet gate."""

    name: str
    query: str
    title: str = ""
    description: str = ""
    parameters: list[dict] = []
    result: dict = {}


# Update-form keywords. Oxigraph's /query endpoint is read-only regardless, but
# we reject these up front so the escape hatch can never be mistaken for write
# access and the user gets a clear message.
_SPARQL_UPDATE = re.compile(
    r"\b(insert|delete|load|clear|drop|create|add|move|copy)\b", re.IGNORECASE
)

logger = logging.getLogger(__name__)


async def _project_ontology_graph(
    client: OxigraphClient, dataset_id: str, artifacts: dict[str, str]
) -> int:
    """#20 step5: project the dataset's TBox into its ontology named graph.

    Additive + best-effort: reads the bundle's ``model.yaml`` (rdf-config TBox),
    resolves prefixes from the bundle's own RML / MIE declarations (so ``sd:`` /
    ``sdr:`` map to THIS dataset's IRIs) unioned with standard ones, projects
    RDFS/OWL, and replaces the ontology graph (DROP then load) so a re-promote
    has no stale triples. Returns the triple count (0 = nothing projected). Never
    raises — a projection failure must not block a promote (the TBox graph is
    enrichment; Ask works from the ABox regardless).
    """
    model_yaml = artifacts.get("model.yaml") or ""
    if not model_yaml.strip():
        return 0
    prefixes = STANDARD_PREFIXES | extract_prefixes(
        artifacts.get("mapping.rml.ttl") or "", artifacts.get("mie.yaml") or ""
    )
    graph = project_model_yaml(model_yaml, prefixes)
    if len(graph) == 0:
        return 0
    payload = graph.serialize(format="turtle")
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    ontology_iri = substrate.ontology_graph_iri(dataset_id)
    await substrate.drop_graph(client, ontology_iri)  # replace, not merge
    await client.post_turtle_bytes(payload, graph_iri=ontology_iri)
    return len(graph)


# #20 P2-2b: starrydata's identity (ontology / resource IRIs) is content declared
# in datasets/starrydata/dataset.toml, read via the generic dataset loader — the
# api no longer imports starrydata constants. The descriptor is the source of
# truth (live in prod because the image bundles datasets/); the literals below
# are a defensive fallback for a wheel-only install without the datasets/ tree.
# Env overrides (CSV2RDF_ONTOLOGY_IRI / CSV2RDF_RESOURCE_IRI) still win.
_SD = load_dataset("starrydata")
_DEFAULT_ONTOLOGY = (
    _SD.ontology_iri if _SD else "https://kumagallium.github.io/asterism/starrydata/ontology#"
)
_DEFAULT_RESOURCE = (
    _SD.resource_iri if _SD else "https://kumagallium.github.io/asterism/starrydata/resource/"
)

# Restrict uploaded filenames to a safe subset to avoid directory traversal
# (``..`` segments, absolute paths, NULs). We also reject names without a
# ``.csv`` suffix so the watcher's ``_classify`` actually fires.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,128}\.csv$")
# The step0 / source / ingest paths accept JSON too (#19). Morph-KGC reads CSV
# and JSON (ql:CSV / ql:JSONPath), so both are valid sources; the legacy
# ``/upload/{kind}`` starrydata drop stays CSV-only (it feeds the CSV watcher).
_SAFE_SOURCE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,128}\.(csv|json|geojson)$")


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
        # Where materialized schema bundles are persisted so the Gallery can
        # list what has been built (authoring→catalog half of the lifecycle).
        self.registry_root = Path(
            e.get("CSV2RDF_REGISTRY_ROOT", "/data/sources/registry")
        )
        self.oxigraph_url = e.get("CSV2RDF_OXIGRAPH_URL", "http://oxigraph:7878")
        self.graph_prefix = e.get("CSV2RDF_GRAPH_PREFIX", DEFAULT_GRAPH_PREFIX)
        # Default-graph load keeps GRAPH-less SPARQL (MIE examples) working.
        # Set CSV2RDF_USE_DEFAULT_GRAPH=0 to opt back into per-kind named graphs.
        self.use_default_graph = e.get(
            "CSV2RDF_USE_DEFAULT_GRAPH", "1"
        ).strip().lower() not in ("0", "false", "no")
        self.ontology_iri = e.get("CSV2RDF_ONTOLOGY_IRI", _DEFAULT_ONTOLOGY)
        self.resource_iri = e.get("CSV2RDF_RESOURCE_IRI", _DEFAULT_RESOURCE)
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


def _validate_source_name(name: str) -> str:
    """Validate a step0 / source-attach / ingest upload name (CSV or JSON, #19)."""
    if not _SAFE_SOURCE_NAME.fullmatch(name):
        raise HTTPException(
            400,
            "filename must match [A-Za-z0-9._-]+.(csv|json|geojson) (max 128 chars)",
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


async def _persist_source_uploads(
    registry_root: Path, dataset_id: str, files: list[UploadFile]
) -> tuple[list[str], dict | None]:
    """Persist uploaded sources as the dataset's design-time source (Task E, #19).

    Streams each upload (CSV or JSON) into ``registry_root/<id>/source/``
    (resetting any prior source so it reflects exactly this upload) and records
    the filenames + source kind on the meta. This lets a *design*-stage dataset
    be ingested from the catalog later with no re-attach (reproducibility — the
    citable-facts direction).
    """
    sdir = registry.source_dir(registry_root, dataset_id)
    if sdir is None:
        raise HTTPException(404, f"dataset {dataset_id!r} not found")
    await asyncio.to_thread(shutil.rmtree, sdir, ignore_errors=True)
    saved: list[str] = []
    for upload in files:
        if upload.filename is None:
            raise HTTPException(400, "missing filename")
        name = _validate_source_name(upload.filename)
        await _save_upload(upload, sdir / name)
        saved.append(name)
    meta = registry.mark_source_saved(registry_root, dataset_id, saved)
    return saved, meta


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


async def _pending_drop_sweeper(
    client: OxigraphClient, stop: asyncio.Event, *, interval: float = 10.0
) -> None:
    """Background task (part5): drop superseded / deleted version graphs.

    Re-ingest streams a new version and promote swaps the live pointer, leaving the
    old version superseded; delete enqueues the data graph. This sweeper drops those
    enqueued graphs OFF the request path, so replace / delete never block on a large
    DROP. The first iteration runs immediately (recovering orphans left by a crash
    mid-drop), then every ``interval`` seconds until shutdown.
    """
    while not stop.is_set():
        try:
            dropped = await substrate.sweep_pending_drops(client, limit=20)
            if dropped:
                logger.info("swept %d superseded/deleted graph(s)", len(dropped))
        except Exception:  # never let a sweep error kill the loop
            logger.exception("pending-drop sweep failed (continuing)")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)


# ----------------------------------------------------------------------------
# App builder
# ----------------------------------------------------------------------------


def build_app(
    settings: Settings | None = None,
    *,
    oxigraph_client: OxigraphClient | None = None,
    start_watcher: bool = True,
    llm_factory: Callable[[str | None], LLMClient] | None = None,
) -> FastAPI:
    """Build the FastAPI app.

    ``llm_factory`` maps an optional user-brought API key to an
    :class:`LLMClient` for one propose/refine run. Defaults to constructing an
    :class:`AnthropicLLMClient` with that key. Tests inject a factory returning
    a mock so no real key / network is needed.
    """
    cfg = settings or Settings()
    make_llm = llm_factory or (lambda key: AnthropicLLMClient(api_key=key))
    watcher_cfg = WatcherConfig(
        drop_root=cfg.drop_root,
        rdf_root=cfg.rdf_root,
        error_root=cfg.error_root,
        jobs_log=cfg.jobs_log,
        graph_prefix=cfg.graph_prefix,
        use_default_graph=cfg.use_default_graph,
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
        # #20 FROM-merge: Ask reads a cross-dataset FROM-merge over the canonical
        # graphs, which excludes the raw default graph. Relocate any pre-existing
        # default-graph data (legacy / seed loaded before this change) into
        # canonical/legacy once, so it stays visible. Idempotent + merge-safe
        # (ADD then CLEAR DEFAULT), so running on every startup is harmless.
        try:
            legacy_iri = substrate.canonical_graph_iri(substrate.LEGACY_DATASET_ID)
            moved = await substrate.migrate_default_to_canonical(client, legacy_iri)
            if moved:
                logger.info(
                    "migrated %d default-graph triples into %s (FROM-merge scope)",
                    moved,
                    legacy_iri,
                )
            # Memory-bounded promote: citability is now gated on a control-graph
            # "promoted" flag (no MOVE). Flag the legacy bulk graph (watcher / seed /
            # migration all land there) when it holds data, and backfill the flag for
            # every registry dataset already promoted under the old MOVE scheme so it
            # stays citable after this upgrade. Idempotent — safe on every startup; it
            # never flags a not-yet-promoted draft (registry ``promoted`` is set only
            # after the human gate), and it leaves retracted datasets retracted.
            if moved or await substrate.graph_has_triples(client, legacy_iri):
                await substrate.mark_graph_promoted(client, legacy_iri)
            for meta in registry.list_datasets(cfg.registry_root):
                if meta.get("promoted") and meta.get("status") != "retracted":
                    cg = meta.get("canonical_graph") or substrate.canonical_graph_iri(
                        meta["id"]
                    )
                    # part5: restore the live version pointer too (a dataset promoted
                    # before part5 has no live_graph -> falls back to the key graph).
                    await substrate.mark_graph_promoted(
                        client, cg, live_graph=meta.get("live_graph")
                    )
        except Exception:  # never block startup on the migration / backfill
            logger.exception(
                "default->canonical/legacy migration or promote-flag backfill failed (continuing)"
            )
        stop = asyncio.Event()
        task: asyncio.Task[None] | None = None
        if start_watcher:
            task = asyncio.create_task(
                watch(watcher_cfg, client, stop_event=stop), name="asterism-watcher"
            )
        # part5: a background sweeper drops superseded / deleted version graphs off
        # the request path (an initial sweep also recovers any orphans left by a
        # crash mid-drop). Gated on start_watcher so unit tests opt out cleanly.
        sweeper: asyncio.Task[None] | None = None
        if start_watcher:
            sweeper = asyncio.create_task(
                _pending_drop_sweeper(client, stop), name="asterism-drop-sweeper"
            )
        app.state.client = client
        app.state.watcher_cfg = watcher_cfg
        app.state.watcher_task = task
        app.state.sweeper_task = sweeper
        app.state.jobs = JobManager()
        try:
            yield
        finally:
            stop.set()
            for bg in (task, sweeper):
                if bg is not None:
                    try:
                        await asyncio.wait_for(bg, timeout=2.0)
                    except (TimeoutError, asyncio.CancelledError):
                        bg.cancel()
            if oxigraph_client is None:
                await client.aclose()

    app = FastAPI(
        title="Asterism upload API",
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

    @app.post("/api/inspect")
    async def inspect_csvs(
        files: list[UploadFile] = File(..., description="Source file(s) to inspect (CSV or JSON)"),
        fk: list[str] = Query(
            default=[], description="Foreign-key hint column (repeatable, e.g. SID)"
        ),
    ) -> Response:
        """Phase 4 (M0): run step0's structure inspection and return its Markdown.

        No LLM and no API key — step0's inspect path is dependency-free. The
        uploads are written to a throwaway temp dir, inspected, then discarded;
        nothing is persisted (dataset persistence arrives in M1). CSV and JSON
        sources are dispatched per file by extension (#19).
        """
        if not files:
            raise HTTPException(400, "no files uploaded")
        with tempfile.TemporaryDirectory() as td:
            paths: list[Path] = []
            for upload in files:
                if upload.filename is None:
                    raise HTTPException(400, "missing filename")
                dest = Path(td) / _validate_source_name(upload.filename)
                await _save_upload(upload, dest)
                paths.append(dest)
            inspections, fks = inspect_source_set(paths, fk_hint_columns=fk or None)
            markdown = render_markdown(inspections, fks)
        return Response(content=markdown, media_type="text/markdown")

    @app.post("/api/propose")
    async def propose(
        files: list[UploadFile] = File(
            ..., description="Source file(s) to model (CSV or JSON)"
        ),
        domain: str = Form(
            default="",
            description="Domain hint (Markdown). Optional — improves quality but not required.",
        ),
        fk: list[str] = Query(default=[], description="FK hint column (repeatable)"),
        x_api_key: str | None = Header(
            default=None,
            description="User-brought Anthropic API key (D7: used for this run only, never stored)",
        ),
    ) -> JSONResponse:
        """Phase 4 (M1a): start an async schema-proposal job; return its job_id.

        The proposal call takes minutes, so we return immediately and stream
        lifecycle events from ``GET /api/jobs/{job_id}/stream`` (SSE). The CSVs
        are copied into a temp dir whose lifetime spans the job. The API key
        (header ``X-API-Key``) is used only to build the LLM client for this run
        and is never persisted (D7).
        """
        if not files:
            raise HTTPException(400, "no files uploaded")

        import tempfile as _tempfile

        tmpdir = _tempfile.mkdtemp(prefix="asterism-propose-")
        paths: list[Path] = []
        for upload in files:
            if upload.filename is None:
                raise HTTPException(400, "missing filename")
            dest = Path(tmpdir) / _validate_source_name(upload.filename)
            await _save_upload(upload, dest)
            paths.append(dest)

        llm = make_llm(x_api_key)
        fk_cols = fk or None

        def work() -> dict[str, object]:
            try:
                proposal = propose_schema(
                    list(paths), domain, fk_hint_columns=fk_cols, llm=llm
                )
                return {
                    "proposal_md": proposal.proposal_md,
                    "inspection_md": proposal.csv_inspection_md,
                    "metadata": proposal.metadata,
                }
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)

        jobs: JobManager = app.state.jobs
        job_id = jobs.start(work)
        return JSONResponse({"job_id": job_id}, status_code=202)

    @app.post("/api/refine")
    async def refine(
        body: RefineRequest,
        x_api_key: str | None = Header(default=None),
    ) -> JSONResponse:
        """Phase 4 (M1c): start an async refine job; return its job_id.

        Applies review comments to the current schema Markdown via the LLM and
        streams lifecycle events from ``/api/jobs/{job_id}/stream``. Like
        propose, the API key is used only for this run and never persisted (D7).
        """
        comments = [c for c in (body.comments or []) if c.strip()]
        if not body.schema_md.strip():
            raise HTTPException(400, "schema_md is required")
        if not comments:
            raise HTTPException(400, "at least one non-empty comment is required")

        llm = make_llm(x_api_key)

        def work() -> dict[str, object]:
            result = refine_schema(body.schema_md, comments, llm=llm)
            # Surface the truncation guard: `refined_md` stays the raw output for
            # transparency; `effective_schema_md` is what's safe to materialize
            # next (the previous complete schema when the refine was truncated).
            return {
                "refined_md": result.refined_md,
                "effective_schema_md": result.effective_schema_md,
                "complete": result.complete,
                "missing_artifacts": result.missing_artifacts,
                "warnings": result.warnings,
                "metadata": result.metadata,
            }

        jobs: JobManager = app.state.jobs
        job_id = jobs.start(work)
        return JSONResponse({"job_id": job_id}, status_code=202)

    @app.post("/api/materialize")
    async def materialize(body: MaterializeRequest) -> JSONResponse:
        """Phase 4 (M1d): split a proposal into the 4 artifacts and validate.

        Synchronous (no LLM): extracts diagram / rdf-config model / MIE /
        ingester from the Markdown, then runs the 8-trap validator on the
        extracted bundle. Source CSVs are not attached here, so CSV-dependent
        traps (T1 / T6) report ``skip``; the structural traps (T2-T5 / T7)
        run. Returns the artifact contents (for client-side download) plus the
        trap report. The temp dir is removed before returning.
        """
        if not body.proposal_md.strip():
            raise HTTPException(400, "proposal_md is required")

        def run() -> dict[str, object]:
            tmpdir = tempfile.mkdtemp(prefix="asterism-materialize-")
            try:
                mat = materialize_schema(body.proposal_md, tmpdir, body.dataset_name, write=True)
                paths = {k: Path(v) for k, v in mat.written_paths.items()}
                report = validate_schema(
                    SchemaBundle(
                        diagram_md=paths.get("mermaid") or paths.get("diagram"),
                        mie_yaml=paths.get("mie_yaml") or paths.get("mie"),
                        ingester_py=paths.get("ingester_py") or paths.get("ingester"),
                    )
                )
                artifacts = {
                    "diagram.md": mat.mermaid,
                    "model.yaml": mat.rdf_config_model,
                    "mie.yaml": mat.mie_yaml,
                    "ingester.py": mat.ingester_py,
                    # Phase 5: the declarative RML mapping (may be None/absent on
                    # older proposals — persisted so the human-gated ingest can run it).
                    "mapping.rml.ttl": mat.rml_ttl,
                }
                traps = [
                    {"id": r.trap_id, "name": r.name, "status": r.status, "detail": r.detail}
                    for r in report.results
                ]
                exit_code = report.exit_code()
                result: dict[str, object] = {
                    "artifacts": artifacts,
                    "complete": mat.complete,
                    "warnings": mat.warnings,
                    "traps": traps,
                    "exit_code": exit_code,
                }
                # Persist so the bundle appears in the Gallery (authoring→catalog).
                if body.persist:
                    meta = registry.save_dataset(
                        cfg.registry_root,
                        body.dataset_name,
                        artifacts,
                        complete=mat.complete,
                        warnings=mat.warnings,
                        traps=traps,
                        exit_code=exit_code,
                        created_at=datetime.now(UTC).isoformat(),
                    )
                    result["dataset"] = meta
                return result
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)

        result = await asyncio.to_thread(run)
        return JSONResponse(result)

    @app.get("/api/datasets")
    async def list_datasets() -> dict[str, object]:
        """List materialized datasets (newest first) for the Gallery."""
        items = registry.list_datasets(cfg.registry_root)
        return {"count": len(items), "datasets": items}

    @app.get("/api/datasets/{dataset_id}")
    async def get_dataset(dataset_id: str) -> dict[str, object]:
        """Return one dataset's meta + artifact contents (for detail + download)."""
        data = registry.load_dataset(cfg.registry_root, dataset_id)
        if data is None:
            raise HTTPException(404, f"dataset {dataset_id!r} not found")
        return data

    @app.get("/api/datasets/{dataset_id}/tools")
    async def list_dataset_tools(dataset_id: str) -> dict[str, object]:
        """List a dataset's declared query tools (the "grow verified tools" store).

        Tools live at ``registry/<id>/query_tools.yaml`` and are loaded by the
        same engine the repo example datasets use, so a saved tool becomes a
        verified, deterministic Ask tool for this dataset (no repo PR needed)."""
        if registry.load_dataset(cfg.registry_root, dataset_id) is None:
            raise HTTPException(404, f"dataset {dataset_id!r} not found")
        tools = registry.list_query_tools(cfg.registry_root, dataset_id)
        return {"dataset_id": dataset_id, "tools": tools}

    @app.post("/api/datasets/{dataset_id}/tools")
    async def save_dataset_tool(dataset_id: str, body: QueryToolBody) -> dict[str, object]:
        """Add/replace one query tool on a dataset (upsert by name).

        The submitted tool is validated with ``parse_query_tools`` (read-only
        SELECT/ASK + safe ``{{placeholder}}`` binding) before it is persisted —
        an invalid tool is 400, never saved. Saving IS the human-vet gate: a
        person deliberately submits a tool they have reviewed (same trust model as
        the Tier 0 function library; nothing is generated at runtime)."""
        if registry.load_dataset(cfg.registry_root, dataset_id) is None:
            raise HTTPException(404, f"dataset {dataset_id!r} not found")
        tool = body.model_dump()
        try:
            parsed = parse_query_tools({"tools": [tool]})
        except QueryToolError as exc:
            raise HTTPException(400, f"invalid query tool: {exc}") from exc
        registry.save_query_tool(cfg.registry_root, dataset_id, tool)
        return {
            "dataset_id": dataset_id,
            "saved": parsed[0].name,
            "tools": registry.list_query_tools(cfg.registry_root, dataset_id),
        }

    @app.delete("/api/datasets/{dataset_id}/tools/{tool_name}")
    async def delete_dataset_tool(dataset_id: str, tool_name: str) -> dict[str, object]:
        """Remove one declared query tool from a dataset."""
        if registry.load_dataset(cfg.registry_root, dataset_id) is None:
            raise HTTPException(404, f"dataset {dataset_id!r} not found")
        removed = registry.delete_query_tool(cfg.registry_root, dataset_id, tool_name)
        if not removed:
            raise HTTPException(404, f"tool {tool_name!r} not found")
        return {
            "dataset_id": dataset_id,
            "deleted": tool_name,
            "tools": registry.list_query_tools(cfg.registry_root, dataset_id),
        }

    @app.post("/api/datasets/{dataset_id}/source")
    async def attach_source(
        dataset_id: str,
        files: list[UploadFile] = File(
            ..., description="Design-time source file(s) (CSV or JSON)"
        ),
    ) -> JSONResponse:
        """Persist the sources a dataset was designed from (reproducibility, Task E).

        Saved alongside the registry bundle (``<id>/source/``) so a *design*-stage
        dataset can later be ingested from the catalog with no re-attach. The
        workbench calls this right after materialize (step 3 保存). CSV and JSON
        sources are both accepted (#19). Overwrites any previously attached source.
        """
        if registry.load_dataset(cfg.registry_root, dataset_id) is None:
            raise HTTPException(404, f"dataset {dataset_id!r} not found")
        if not files:
            raise HTTPException(400, "no source files uploaded")
        saved, meta = await _persist_source_uploads(cfg.registry_root, dataset_id, files)
        return JSONResponse(
            {"dataset_id": dataset_id, "source_files": saved, "dataset": meta}
        )

    @app.post("/api/datasets/{dataset_id}/ingest")
    async def ingest_dataset(
        dataset_id: str,
        files: list[UploadFile] = File(
            default=[],
            description="Source file(s) the RML maps (CSV or JSON). Optional — when "
            "omitted, the dataset's persisted design-time source is used (Task E).",
        ),
    ) -> JSONResponse:
        """Phase 5 (#15): human-gated ingest of a dataset's approved RML mapping.

        Runs the dataset's persisted ``mapping.rml.ttl`` through the Morph-KGC
        substrate (NO generated code — only the closed Tier 0 functions) and streams
        the result straight into the dataset's **per-dataset canonical graph**. That
        graph is excluded from the Ask scope until promote flips its control-graph
        flag, so the data is not a citable fact until separately promoted (the flag,
        not graph existence, gates citability — memory-bounded promote needs no
        later MOVE). This is the explicit second gate after ``materialize`` (which
        only saves the RML draft).

        Source CSVs are either uploaded here (and persisted as the dataset's
        design-time source) or — when omitted — taken from that persisted source,
        so a *design*-stage dataset can be ingested straight from the catalog with
        no re-attach (Task E).

        **Scalable / background (ADR scalable-declarative-ingestion.md)**: validation
        is synchronous (4xx below), then the heavy work runs as a background job —
        Morph-KGC writes N-Triples to a file, which is streamed into the draft graph
        in row-chunked POSTs. Returns ``202 {job_id}``; progress + completion stream
        over ``GET /api/jobs/{job_id}/stream`` (SSE). This lets a large dataset
        (millions of triples) load with live progress instead of a blocking request
        that times out.
        """
        data = registry.load_dataset(cfg.registry_root, dataset_id)
        if data is None:
            raise HTTPException(404, f"dataset {dataset_id!r} not found")
        rml_ttl = str(data["artifacts"].get("mapping.rml.ttl", "") or "")
        if not rml_ttl.strip():
            raise HTTPException(
                400, "this dataset has no declarative RML mapping to ingest"
            )

        # Uploaded sources (if any) refresh + persist the design-time source; an
        # ingest with no upload reuses whatever source was persisted. (Synchronous
        # so the file is on disk before the background job reads it.)
        uploaded = [f for f in files if f.filename]
        if uploaded:
            await _persist_source_uploads(cfg.registry_root, dataset_id, uploaded)
        source_paths = registry.list_source_files(cfg.registry_root, dataset_id)
        if not source_paths:
            raise HTTPException(
                400,
                "投入には CSV または JSON のソースファイルが必要です。"
                "設計時に添付するか、ここでアップロードしてください",
            )
        source_dir = source_paths[0].parent
        # part5: stream into a FRESH per-ingest version graph `canonical/{id}/v{n}`
        # — never touching the currently live graph. So a re-ingest needs no
        # un-publish and no DROP on the request path (the old version stays citable
        # until promote swaps the live pointer; it is dropped in the background
        # afterwards). The version graph stays out of the Ask scope until promote
        # points the dataset's liveGraph at it (draft isolation, flag-based).
        dataset_key = substrate.canonical_graph_iri(dataset_id)
        data_seq = registry.next_data_seq(cfg.registry_root, dataset_id)
        staged_iri = substrate.versioned_graph_iri(dataset_id, data_seq)
        client: OxigraphClient = app.state.client

        async def ingest_job(emit: Callable[..., None]) -> dict[str, object]:
            work = Path(tempfile.mkdtemp(prefix="asterism-ingest-"))
            try:
                emit(phase="materialize", message="RDF を生成中")
                # Morph-KGC writes N-Triples to a file (memory-bounded); the
                # subprocess CLI is blocking, so run it off the event loop.
                nt = await asyncio.to_thread(
                    substrate.materialize_to_nt_file, rml_ttl, source_dir, work_dir=work
                )
                total = substrate.count_nt_lines(nt)
                emit(phase="materialized", total=total)
                # The target is a fresh, empty version graph — no clean-slate DROP
                # needed (and the live graph is untouched, so Ask keeps serving the
                # current version throughout the re-stream).
                emit(phase="preparing", message="取り込み先グラフを準備中")
                try:
                    triple_count = await substrate.stream_nt_file_to_oxigraph(
                        nt,
                        client,
                        staged_iri,
                        on_progress=lambda done, tot: emit(
                            phase="upload", done=done, total=tot
                        ),
                    )
                except Exception:
                    # D6: never leave a partial version graph behind on failure (it
                    # was never live, so reclaiming it cannot affect a reader). Use a
                    # chunked delete — a partial can be large, and a single DROP of a
                    # multi-million-triple graph OOMs Oxigraph.
                    await substrate.chunked_drop_graph(client, staged_iri)
                    raise
            finally:
                shutil.rmtree(work, ignore_errors=True)  # the .nt can be GBs

            # Record the staged version graph as the dataset's pending ingest.
            await substrate.set_staged_graph(client, dataset_key, staged_iri)
            meta = registry.mark_ingested(
                cfg.registry_root,
                dataset_id,
                graph_iri=staged_iri,
                triple_count=triple_count,
                ingested_at=datetime.now(UTC).isoformat(),
                data_seq=data_seq,
            )
            return {
                "dataset_id": dataset_id,
                "graph_iri": staged_iri,
                # Staged in a version graph but not yet citable (awaits promote).
                "graph_kind": "staged",
                "triple_count": triple_count,
                "dataset": meta,
            }

        jobs: JobManager = app.state.jobs
        job_id = jobs.start_coro(ingest_job)
        return JSONResponse({"job_id": job_id}, status_code=202)

    @app.get("/api/datasets/{dataset_id}/alignment")
    async def dataset_alignment(dataset_id: str) -> JSONResponse:
        """Preview the Reuse/New alignment of a dataset's staged graph vs canonical.

        What the human reviews *before* promoting (#15 S4): which predicates and
        classes the staged (ingested, not-yet-promoted) graph uses are already in
        the citable canonical scope (Reuse) vs not yet (New). The staged graph is
        the dataset's canonical graph before its promoted flag is set, so it is not
        in the canonical scope it is compared against. Read-only.
        """
        data = registry.load_dataset(cfg.registry_root, dataset_id)
        if data is None:
            raise HTTPException(404, f"dataset {dataset_id!r} not found")
        if not data["meta"].get("ingested"):
            raise HTTPException(400, "dataset has no staged graph (not ingested)")
        client: OxigraphClient = app.state.client
        # part5: align the *staged version graph* (recorded at ingest) against the
        # citable corpus — it is not promoted yet, so it is not part of that scope.
        staged_iri = data["meta"].get("graph_iri") or substrate.canonical_graph_iri(
            dataset_id
        )
        report = await substrate.alignment_report(client, staged_iri)
        return JSONResponse({"dataset_id": dataset_id, "alignment": report})

    @app.post("/api/datasets/{dataset_id}/promote")
    async def promote_dataset(dataset_id: str) -> JSONResponse:
        """Phase 5 (#15 S4): human-gated promotion of a staged version graph to citable.

        Memory-bounded + off-critical-path: the triples were already streamed into a
        version graph at ingest, so promotion just points the dataset's ``liveGraph``
        at it and flips ``promoted`` — O(1) control writes, no MOVE/DROP. A re-promote
        supersedes the prior version, which is dropped in the background (part5). The
        alignment report (Reuse vs New) is recorded on the dataset's meta.
        """
        data = registry.load_dataset(cfg.registry_root, dataset_id)
        if data is None:
            raise HTTPException(404, f"dataset {dataset_id!r} not found")
        if not data["meta"].get("ingested"):
            raise HTTPException(400, "dataset has no staged graph to promote (not ingested)")
        client: OxigraphClient = app.state.client
        dataset_key = substrate.canonical_graph_iri(dataset_id)
        # The staged version graph (recorded at ingest) holds the new data. Aligning
        # it against the citable corpus is valid: it is not promoted yet, so it is not
        # part of the scope it is compared against.
        staged_iri = data["meta"].get("graph_iri") or dataset_key
        alignment = await substrate.alignment_report(client, staged_iri)
        # O(1): point liveGraph at the staged version + flag promoted. Any prior live
        # version is enqueued for a background drop (reclaimed off the request path).
        await substrate.promote_to_canonical(client, dataset_key, staged_iri)
        # Triple count is recorded at ingest (mark_ingested) — read it rather than
        # COUNT the (possibly multi-million-triple) graph, keeping promote O(1).
        triples_promoted = int(data["meta"].get("triple_count") or 0)
        # #20 step5: project the TBox into the ontology graph (additive, best-effort).
        ontology_triples = 0
        try:
            ontology_triples = await _project_ontology_graph(
                client, dataset_id, data.get("artifacts", {})
            )
        except Exception:  # never block a promote on TBox projection
            logger.exception("ontology projection failed for %s (continuing)", dataset_id)
        meta = registry.mark_promoted(
            cfg.registry_root,
            dataset_id,
            triples_promoted=triples_promoted,
            alignment=alignment,
            promoted_at=datetime.now(UTC).isoformat(),
            canonical_graph=dataset_key,
            live_graph=staged_iri,
        )
        return JSONResponse(
            {
                "dataset_id": dataset_id,
                "promoted": True,
                "canonical_graph": dataset_key,
                # part5: the version graph now holding the citable data.
                "live_graph": staged_iri,
                "triples_promoted": triples_promoted,
                # #20 step5: TBox triples projected into the ontology graph.
                "ontology_graph": substrate.ontology_graph_iri(dataset_id),
                "ontology_triples": ontology_triples,
                "alignment": alignment,
                # #20 P3: monotonic dataset version (bumped on each re-promote).
                "version": meta.get("version") if meta else None,
                "dataset": meta,
            }
        )

    @app.post("/api/datasets/{dataset_id}/retract")
    async def retract_dataset(dataset_id: str) -> JSONResponse:
        """#20 P3 step3: withdraw a promoted dataset from the citable corpus.

        Tombstone, not delete: the canonical graph's data + IRIs stay (so existing
        citations keep resolving) but a control-graph marker makes the canonical
        scope exclude it from every Ask read. Reversible via /reinstate.
        """
        data = registry.load_dataset(cfg.registry_root, dataset_id)
        if data is None:
            raise HTTPException(404, f"dataset {dataset_id!r} not found")
        if not data["meta"].get("promoted"):
            raise HTTPException(400, "dataset is not promoted (nothing canonical to retract)")
        canonical_iri = substrate.canonical_graph_iri(dataset_id)
        client: OxigraphClient = app.state.client
        now = datetime.now(UTC).isoformat()
        await substrate.retract_canonical(client, canonical_iri, invalidated_at=now)
        meta = registry.mark_retracted(cfg.registry_root, dataset_id, retracted_at=now)
        return JSONResponse(
            {"dataset_id": dataset_id, "status": "retracted", "dataset": meta}
        )

    @app.post("/api/datasets/{dataset_id}/reinstate")
    async def reinstate_dataset(dataset_id: str) -> JSONResponse:
        """#20 P3 step3: undo a retract — bring the dataset back into the Ask scope."""
        data = registry.load_dataset(cfg.registry_root, dataset_id)
        if data is None:
            raise HTTPException(404, f"dataset {dataset_id!r} not found")
        canonical_iri = substrate.canonical_graph_iri(dataset_id)
        client: OxigraphClient = app.state.client
        await substrate.reinstate_canonical(client, canonical_iri)
        meta = registry.mark_reinstated(
            cfg.registry_root, dataset_id, reinstated_at=datetime.now(UTC).isoformat()
        )
        return JSONResponse(
            {"dataset_id": dataset_id, "status": "active", "dataset": meta}
        )

    @app.delete("/api/datasets/{dataset_id}")
    async def delete_dataset_endpoint(
        dataset_id: str, force: bool = Query(False)
    ) -> JSONResponse:
        """#20 P3 step4: hard-delete a dataset (registry + its graphs).

        A *promoted* dataset has citable canonical data, so deleting it can break
        existing citations — that requires explicit ``?force=true``; the safe
        default for those is ``retract``. A design / staged-only dataset (never
        promoted) is removed freely.

        part5: the dataset's data graphs (live version + any pending staged version)
        are **enqueued for a background drop** and the endpoint returns immediately —
        delete never blocks on a large DROP. A promoted delete also leaves a
        ``deleted`` tombstone in the control graph so dangling citations get a clear
        answer.
        """
        data = registry.load_dataset(cfg.registry_root, dataset_id)
        if data is None:
            raise HTTPException(404, f"dataset {dataset_id!r} not found")
        meta = data["meta"]
        promoted = bool(meta.get("promoted"))
        if promoted and not force:
            raise HTTPException(
                409,
                "promoted dataset has citable canonical data; retract it instead, "
                "or pass ?force=true to hard-delete (breaks existing citations).",
            )
        client: OxigraphClient = app.state.client
        dataset_key = substrate.canonical_graph_iri(dataset_id)
        # Gather the data graphs to reclaim: the live version (or the key graph for a
        # pre-part5 dataset) and any pending staged version.
        to_drop: set[str] = set()
        if promoted:
            to_drop.add(await substrate.live_graph_of(client, dataset_key) or dataset_key)
        staged = meta.get("graph_iri")
        if meta.get("ingested") and staged:
            to_drop.add(staged)
        for g in sorted(to_drop):
            await substrate.mark_pending_drop(client, g)
        if promoted:
            # Replaces the live pointer with a deleted tombstone (one control write).
            await substrate.tombstone_deleted(
                client, dataset_key, deleted_at=datetime.now(UTC).isoformat()
            )
        else:
            # Never citable — just drop its staged pointer (no tombstone needed).
            await substrate.clear_staged_graph(client, dataset_key)
        registry.delete_dataset(cfg.registry_root, dataset_id)
        # The data graphs are enqueued for a background drop; the periodic sweeper
        # reclaims them off the request path (so delete never blocks on a large DROP).
        return JSONResponse(
            {"dataset_id": dataset_id, "deleted": True, "was_promoted": promoted}
        )

    @app.post("/api/sparql")
    async def sparql(body: SparqlRequest) -> JSONResponse:
        """Read-only SPARQL relay to Oxigraph (advanced escape hatch, ADR §5).

        Forwards the query to Oxigraph's read-only ``/query`` endpoint and
        returns the SPARQL-Results JSON. Update-form queries are rejected with a
        clear message (the endpoint is read-only either way).

        #20 FROM-merge: a query that does not declare its own dataset is rewritten
        to read the canonical FROM-merge (every non-retracted canonical graph),
        matching what Ask sees — so plain queries keep working after legacy data
        moves out of the default graph. A power user can still target a specific
        graph (e.g. a draft) by writing an explicit ``FROM`` / ``FROM NAMED``,
        which is respected as-is.
        """
        q = body.query.strip()
        if not q:
            raise HTTPException(400, "query is required")
        # Strip line comments before the read-only check.
        if _SPARQL_UPDATE.search(re.sub(r"#.*", "", q)):
            raise HTTPException(
                400, "読み取り専用です: UPDATE 系 (INSERT/DELETE 等) は実行できません"
            )
        client: OxigraphClient = app.state.client
        try:
            effective = await substrate.canonical_merge_query(client, q)
            return JSONResponse(await client.sparql_select(effective))
        except Exception as exc:  # surface Oxigraph errors to the UI
            raise HTTPException(502, f"SPARQL error: {exc}") from exc

    @app.get("/api/jobs/{job_id}/stream")
    async def job_stream(job_id: str) -> StreamingResponse:
        """Server-Sent Events for one job: replay past events then follow live."""
        jobs: JobManager = app.state.jobs
        return StreamingResponse(
            jobs.stream(job_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",  # disable proxy buffering for SSE
            },
        )

    return app


# ----------------------------------------------------------------------------
# CLI / uvicorn entry point
# ----------------------------------------------------------------------------


_DEFAULT_HOST: Final[str] = "0.0.0.0"
_DEFAULT_PORT: Final[int] = 8080


def _main(argv: list[str] | None = None) -> int:
    import argparse

    import uvicorn

    p = argparse.ArgumentParser(prog="asterism-api")
    p.add_argument("--host", default=_DEFAULT_HOST)
    p.add_argument("--port", type=int, default=_DEFAULT_PORT)
    p.add_argument("--log-level", default="info")
    args = p.parse_args(argv)

    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(message)s")
    uvicorn.run(
        "asterism_api.main:build_app",
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        factory=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
