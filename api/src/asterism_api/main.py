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
from asterism.oxigraph_client import OxigraphClient, OxigraphConfig
from asterism.starrydata import IngestConfig
from asterism.watcher import (
    DEFAULT_GRAPH_PREFIX,
    DEFAULT_SETTLE_S,
    KINDS,
    WatcherConfig,
    watch,
)
from asterism_step0.inspect import inspect_csv_set, render_markdown
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


# Update-form keywords. Oxigraph's /query endpoint is read-only regardless, but
# we reject these up front so the escape hatch can never be mistaken for write
# access and the user gets a clear message.
_SPARQL_UPDATE = re.compile(
    r"\b(insert|delete|load|clear|drop|create|add|move|copy)\b", re.IGNORECASE
)

logger = logging.getLogger(__name__)

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
        stop = asyncio.Event()
        task: asyncio.Task[None] | None = None
        if start_watcher:
            task = asyncio.create_task(
                watch(watcher_cfg, client, stop_event=stop), name="asterism-watcher"
            )
        app.state.client = client
        app.state.watcher_cfg = watcher_cfg
        app.state.watcher_task = task
        app.state.jobs = JobManager()
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
        files: list[UploadFile] = File(..., description="CSV file(s) to inspect"),
        fk: list[str] = Query(
            default=[], description="Foreign-key hint column (repeatable, e.g. SID)"
        ),
    ) -> Response:
        """Phase 4 (M0): run step0's structure inspection and return its Markdown.

        No LLM and no API key — step0's inspect path is dependency-free. The
        uploads are written to a throwaway temp dir, inspected, then discarded;
        nothing is persisted (dataset persistence arrives in M1).
        """
        if not files:
            raise HTTPException(400, "no files uploaded")
        with tempfile.TemporaryDirectory() as td:
            paths: list[Path] = []
            for upload in files:
                if upload.filename is None:
                    raise HTTPException(400, "missing filename")
                dest = Path(td) / _validate_name(upload.filename)
                await _save_upload(upload, dest)
                paths.append(dest)
            inspections, fks = inspect_csv_set(paths, fk_hint_columns=fk or None)
            markdown = render_markdown(inspections, fks)
        return Response(content=markdown, media_type="text/markdown")

    @app.post("/api/propose")
    async def propose(
        files: list[UploadFile] = File(..., description="CSV file(s) to model"),
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
            dest = Path(tmpdir) / _validate_name(upload.filename)
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

    @app.post("/api/datasets/{dataset_id}/ingest")
    async def ingest_dataset(
        dataset_id: str,
        files: list[UploadFile] = File(..., description="Source CSV(s) the RML maps"),
    ) -> JSONResponse:
        """Phase 5 (#15): human-gated ingest of a dataset's approved RML mapping.

        Runs the dataset's persisted ``mapping.rml.ttl`` through the Morph-KGC
        substrate on the uploaded CSVs (NO generated code — only the closed Tier 0
        functions) and loads the result into an **isolated draft named graph**.
        Ask cites the canonical graph by default, so draft data is not a citable
        fact until separately promoted. This is the explicit second gate after
        ``materialize`` (which only saves the RML draft).
        """
        data = registry.load_dataset(cfg.registry_root, dataset_id)
        if data is None:
            raise HTTPException(404, f"dataset {dataset_id!r} not found")
        rml_ttl = str(data["artifacts"].get("mapping.rml.ttl", "") or "")
        if not rml_ttl.strip():
            raise HTTPException(
                400, "this dataset has no declarative RML mapping to ingest"
            )
        if not files:
            raise HTTPException(400, "no CSV files uploaded")

        tmpdir = Path(tempfile.mkdtemp(prefix="asterism-ingest-"))
        try:
            for upload in files:
                if upload.filename is None:
                    raise HTTPException(400, "missing filename")
                await _save_upload(upload, tmpdir / _validate_name(upload.filename))

            graph_iri = substrate.draft_graph_iri(dataset_id)
            client: OxigraphClient = app.state.client
            try:
                # Morph-KGC is CPU-bound and blocking — run it off the event loop.
                graph = await asyncio.to_thread(
                    substrate.materialize_to_graph, rml_ttl, tmpdir
                )
            except RuntimeError as exc:  # morph-kgc not installed (optional extra)
                raise HTTPException(501, str(exc)) from exc
            except Exception as exc:  # Morph-KGC could not run this mapping
                # User-data error (malformed/unsupported RML, column mismatch, …)
                # — surface it as 422 with the cause, not an opaque 500.
                raise HTTPException(
                    422,
                    "宣言マッピングを substrate で実行できませんでした: "
                    f"{type(exc).__name__}: {exc}",
                ) from exc
            triple_count = await substrate.ingest_graph_to_oxigraph(
                graph, client, graph_iri
            )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

        meta = registry.mark_ingested(
            cfg.registry_root,
            dataset_id,
            graph_iri=graph_iri,
            triple_count=triple_count,
            ingested_at=datetime.now(UTC).isoformat(),
        )
        return JSONResponse(
            {
                "dataset_id": dataset_id,
                "graph_iri": graph_iri,
                "graph_kind": "draft",
                "triple_count": triple_count,
                "dataset": meta,
            }
        )

    @app.get("/api/datasets/{dataset_id}/alignment")
    async def dataset_alignment(dataset_id: str) -> JSONResponse:
        """Preview the Reuse/New alignment of a dataset's draft graph vs canonical.

        What the human reviews *before* promoting (#15 S4): which predicates and
        classes the draft uses are already in the canonical (default) graph
        (Reuse) vs not yet (New). Read-only.
        """
        data = registry.load_dataset(cfg.registry_root, dataset_id)
        if data is None:
            raise HTTPException(404, f"dataset {dataset_id!r} not found")
        if not data["meta"].get("ingested"):
            raise HTTPException(400, "dataset has no draft graph (not ingested)")
        client: OxigraphClient = app.state.client
        graph_iri = substrate.draft_graph_iri(dataset_id)
        report = await substrate.alignment_report(client, graph_iri)
        return JSONResponse({"dataset_id": dataset_id, "alignment": report})

    @app.post("/api/datasets/{dataset_id}/promote")
    async def promote_dataset(dataset_id: str) -> JSONResponse:
        """Phase 5 (#15 S4): human-gated promotion of a draft graph to canonical.

        MOVEs the draft named graph into the canonical (default) graph so Ask can
        cite it. The alignment report (Reuse vs New) is recorded on the dataset's
        meta. The draft graph is consumed by the move.
        """
        data = registry.load_dataset(cfg.registry_root, dataset_id)
        if data is None:
            raise HTTPException(404, f"dataset {dataset_id!r} not found")
        if not data["meta"].get("ingested"):
            raise HTTPException(400, "dataset has no draft graph to promote (not ingested)")
        client: OxigraphClient = app.state.client
        graph_iri = substrate.draft_graph_iri(dataset_id)
        # #20 P3: promote into the dataset's own canonical named graph (not the
        # shared default graph), so retract / re-promote / delete are clean graph-
        # scoped ops. Ask reads it via the canonical scope (default + canonical/*).
        canonical_iri = substrate.canonical_graph_iri(dataset_id)
        alignment = await substrate.alignment_report(client, graph_iri)
        triples_promoted = await substrate.promote_draft_to_canonical(
            client, graph_iri, canonical_iri
        )
        meta = registry.mark_promoted(
            cfg.registry_root,
            dataset_id,
            triples_promoted=triples_promoted,
            alignment=alignment,
            promoted_at=datetime.now(UTC).isoformat(),
            canonical_graph=canonical_iri,
        )
        return JSONResponse(
            {
                "dataset_id": dataset_id,
                "promoted": True,
                "canonical_graph": canonical_iri,
                "triples_promoted": triples_promoted,
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

    @app.post("/api/sparql")
    async def sparql(body: SparqlRequest) -> JSONResponse:
        """Read-only SPARQL relay to Oxigraph (advanced escape hatch, ADR §5).

        Forwards the query to Oxigraph's read-only ``/query`` endpoint and
        returns the SPARQL-Results JSON. Update-form queries are rejected with a
        clear message (the endpoint is read-only either way).
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
            return JSONResponse(await client.sparql_select(q))
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
