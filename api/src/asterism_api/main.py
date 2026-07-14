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
import csv
import difflib
import hashlib
import hmac
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
from typing import TYPE_CHECKING, Final

import yaml
from asterism import crosswalk, crosswalk_runtime, documents, grounding, substrate
from asterism.datasets import datasets_root, load_dataset
from asterism.exposure import raw_sparql_enabled
from asterism.ontology_projection import (
    STANDARD_PREFIXES,
    extract_prefixes,
    project_model_yaml,
)
from asterism.oxigraph_client import OxigraphClient, OxigraphConfig
from asterism.query_tools import (
    QueryToolError,
    lint_query_tool,
    parse_query_tools,
    run_query_tool,
)
from asterism.rml_summary import summarize_rml
from asterism.starrydata import IngestConfig
from asterism.watcher import (
    DEFAULT_GRAPH_PREFIX,
    DEFAULT_SETTLE_S,
    KINDS,
    WatcherConfig,
    watch,
    watch_tree,
)
from asterism_step0.crosswalk_propose import propose_crosswalk_mapping
from asterism_step0.inspect import inspect_source_set, render_markdown
from asterism_step0.instance_iri import DEFAULT_IRI_BASE, normalize_iri_base
from asterism_step0.llm import list_available_models
from asterism_step0.llm import make_llm as build_llm_client
from asterism_step0.materialize import (
    _MODEL_HEADERS,
    _pick_block,
    extract_code_blocks,
    materialize_schema,
)
from asterism_step0.propose import LLMClient
from asterism_step0.refine import refine_schema
from asterism_step0.skeleton_annotate import annotate_skeleton
from asterism_step0.staged_propose import propose_skeleton
from asterism_step0.validate import SchemaBundle, validate_schema
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Response,
    UploadFile,
)
from fastapi import Path as PathParam
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from asterism_api import describe as describe_mod
from asterism_api import design_loop, registry, server_keys
from asterism_api import usage as usage_ledger
from asterism_api.jobs import JobManager
from asterism_api.tool_loop import ToolLoopResult, propose_tool_with_correction

if TYPE_CHECKING:
    from asterism.dialect import SourceDialect


class RefineRequest(BaseModel):
    """Body for POST /api/refine: the current schema + review comments."""

    schema_md: str
    comments: list[str]
    # Output language for the resolution log / schema prose (e.g. "ja").
    # Absent/empty → English (legacy behaviour). Headings / identifiers stay
    # English regardless — materialize extracts artifacts by English headings.
    language: str | None = None


class MaterializeRequest(BaseModel):
    """Body for POST /api/materialize: the proposal/refine Markdown to split."""

    proposal_md: str
    dataset_name: str = "dataset"
    # When true (default), persist the bundle to the registry so it shows up in
    # the Gallery. Set false for a throwaway validation-only run.
    persist: bool = True
    # Redesign target: when set, re-materialize OVERWRITES this existing dataset's
    # artifacts/design IN PLACE (same id — graphs / IRIs / lifecycle / source
    # preserved) instead of minting a new dataset. The user re-applies data via the
    # existing re-ingest controls. Ignored when persist is false.
    dataset_id: str | None = None


class SparqlRequest(BaseModel):
    """Body for POST /api/sparql: a read-only SPARQL query (escape hatch)."""

    query: str


class RenameRequest(BaseModel):
    """Body for POST /api/datasets/{id}/rename: the new DISPLAY name (id is immutable)."""

    name: str


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


class ToolProposeBody(BaseModel):
    """Body for POST /api/datasets/{id}/tools/propose: a natural-language intent
    the AI drafts a query tool for (P2). The draft is returned for human review,
    never auto-saved. ``language`` (i18next code, e.g. ``"ja"``) switches the
    draft's human-readable prose (title/description) — name/SPARQL/IRIs stay
    English; absent = English (legacy). ``autocorrect=False`` is the kill-switch
    for the self-correction loop (single LLM shot; the deterministic vet still
    runs)."""

    intent: str
    language: str | None = None
    autocorrect: bool = True


class ToolRunBody(BaseModel):
    """Body for POST /api/datasets/{id}/tools/{name}/run: the typed arguments to
    bind into a saved (human-vetted) query tool. The deterministic, read-only,
    key-free execution path — no LLM, the same typed surface MCP exposes."""

    args: dict = {}


class CrosswalkBuildBody(BaseModel):
    """Body for POST /api/crosswalk[/{perspective_id}]/build (crosswalk-hub.md ①,
    multi-perspective ADR). When a ``config`` is given (the authoring flow: which
    datasets + which concept-bearing predicate participate) it is validated, persisted,
    and built; omit it to rebuild from the persisted config. ``name`` is a human label
    for a new perspective. The mapping is a human-vetted claim — building it IS that
    gate (the same way saving a query tool is)."""

    config: dict | None = None
    name: str = ""


class CrosswalkProposeBody(BaseModel):
    """Body for POST /api/crosswalk/propose: the datasets to crosswalk + the shared
    concept. The LLM suggests each dataset's concept-bearing predicate (返り値は下書き
    — never built); the human confirms/edits in the authoring UI (the vet gate).
    ``language`` (i18next code) switches the human-readable ``why`` reasons —
    predicate IRIs stay verbatim; absent = English (legacy)."""

    dataset_ids: list[str] = []
    concept: str = "composition"
    language: str | None = None


class CrosswalkAlignBody(BaseModel):
    """Body for POST /api/crosswalk/align (multi-perspective ADR §Phase 2): assert (or,
    with ``remove``, withdraw) a schema relationship between two perspective terms.
    ``relation`` is from the closed set (owl:equivalentClass / rdfs:subClassOf /
    owl:equivalentProperty / rdfs:subPropertyOf). A human-vetted, reversible, citable
    claim — additive, never auto-reasoned."""

    source: str
    target: str
    relation: str = "equivalentClass"
    from_perspective: str = ""
    to_perspective: str = ""
    remove: bool = False


class NormalizerPreviewBody(BaseModel):
    """Body for POST /api/crosswalk/normalizer/preview: try a declarative normalizer
    recipe (ordered closed primitive ids) on sample values, so the human can see the
    join key before authoring it (crosswalk-normalizer-recipes.md). Pure compute — no
    store access; the closed primitive set is the safety gate."""

    recipe: list[str] = []
    samples: list[str] = []


class GroundSchemaBody(BaseModel):
    """Body for POST /api/ground/schema: attach external-standard candidates to a PROPOSED
    schema (external-standard-alignment.md §8). Give the propose markdown (its rdf-config
    model.yaml block is extracted) or the model.yaml directly. Read-only + deterministic —
    candidates come only from the closed catalog, never from the LLM."""

    proposal_md: str = ""
    model_yaml: str = ""


class UsageEventBody(BaseModel):
    """Body for POST /api/usage: one LLM-usage event (token counts only — no cost).

    The api's own endpoints record usage in-process; this route is the receiver for
    the demo-agent's agentic Ask, which runs in a separate process and POSTs its
    accumulated tokens here so all spend lands in one ledger (write-gated)."""

    feature: str = "ask"
    provider: str = ""
    model_id: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


class ModelsAvailableBody(BaseModel):
    """Body for POST /api/models/available (model picker #②).

    User-brought credentials used only to list the models they can use; never
    persisted server-side (D7). ``api_base`` is for openai-compatible providers."""

    provider: str = "anthropic"
    api_key: str | None = None
    api_base: str | None = None


class ServerKeyBody(BaseModel):
    """Body for POST /api/llm/server-keys — set/clear the shared server-side key.

    A blank ``api_key`` clears the provider's key. ``api_base`` is required for
    openai-compatible (the endpoint the shared key is pinned to)."""

    provider: str = "anthropic"
    api_key: str = ""
    api_base: str | None = None


def _validate_llm_api_base(api_base: str) -> None:
    """Fail-closed SSRF guard for a user-supplied OpenAI-compatible base URL.

    Rejects non-http(s) schemes and hosts resolving to a private / loopback /
    link-local / reserved address, so an operator-hosted API cannot be turned
    into an internal port scanner or a cloud-metadata exfil vector. Local LLM
    servers (Ollama, vLLM on localhost) are a legitimate use, so private targets
    are allowed when ``ASTERISM_ALLOW_PRIVATE_LLM_BASE=1`` (set on local dev,
    leave unset in shared deployments — fail-closed by default)."""
    import ipaddress
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(api_base)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(400, "api_base must be an http(s) URL")
    host = parsed.hostname
    if not host:
        raise HTTPException(400, "api_base has no host")
    if os.environ.get("ASTERISM_ALLOW_PRIVATE_LLM_BASE") == "1":
        return
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise HTTPException(400, f"api_base host does not resolve: {host}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise HTTPException(
                400,
                f"api_base resolves to a non-public address ({ip}); set "
                "ASTERISM_ALLOW_PRIVATE_LLM_BASE=1 to allow a local LLM server",
            )


def _llm_coords(
    x_api_key: str | None,
    x_llm_provider: str | None,
    x_llm_model: str | None,
    x_llm_api_base: str | None,
    registry_root: Path | str | None = None,
) -> tuple[str, str | None, str | None, str | None]:
    """Resolve the per-request LLM coordinates from headers.

    Absent ``X-LLM-Provider`` → ``"anthropic"`` so requests that send only the
    legacy ``X-API-Key`` keep the exact Anthropic-default behavior. When the
    request carries no key, fall back to the operator's server-side key (UI/file
    store first, then ``ASTERISM_LLM_KEY_<PROVIDER>``) — unset by default, so a
    browser-brought key still wins and is still required unless the operator opts
    in. For an openai-compatible shared key the stored ``api_base`` is PINNED
    (overrides the request's base) so the shared key is never sent to a
    user-controlled endpoint (see :mod:`asterism_api.server_keys`)."""
    provider = (x_llm_provider or "anthropic").strip().lower() or "anthropic"
    if x_api_key:
        return provider, (x_llm_model or None), (x_llm_api_base or None), x_api_key
    key, pinned_base = server_keys.resolve(provider, registry_root)
    api_base = pinned_base or (x_llm_api_base or None)
    return provider, (x_llm_model or None), api_base, key


def _llm_max_tokens(value: str | None) -> int | None:
    """Parse the optional ``X-LLM-Max-Tokens`` header into an output-token cap.

    Absent / blank → None (the provider default). A weak OpenAI-compatible model
    (qwen3-class via vLLM) can reject step0's generous default cap outright, so
    the UI lets the user pin a smaller one per model. Anything that is not a
    positive integer is a client error (400), not a silent fallback."""
    if value is None or not value.strip():
        return None
    try:
        parsed = int(value.strip())
    except ValueError:
        parsed = 0
    if parsed < 1:
        raise HTTPException(400, "X-LLM-Max-Tokens must be a positive integer")
    return parsed


def _arm_llm_callbacks(
    llm: object,
    *,
    should_cancel: Callable[[], bool] | None = None,
    on_generation: Callable[[int, int], None] | None = None,
    on_note: Callable[[str], None] | None = None,
) -> None:
    """Attach a job's cooperative hooks to a real LLM client (best-effort).

    The step0 clients declare mutable ``should_cancel`` / ``on_generation`` /
    ``on_note`` attributes; a bare test mock (whose whole surface is
    ``complete``) declares none. Set only what the client already has, so mocks
    — and any legacy client — are left untouched."""
    for name, value in (
        ("should_cancel", should_cancel),
        ("on_generation", on_generation),
        ("on_note", on_note),
    ):
        if value is not None and hasattr(llm, name):
            setattr(llm, name, value)


def _record_llm_usage(
    registry_root: Path, feature: str, provider: str, llm: object, model_hint: str | None
) -> None:
    """Append the client's ``last_usage`` to the ledger (best-effort, skips zeros).

    Mocks (and any client that returns bare text) have no ``last_usage`` → no-op,
    so tests never write a usage file."""
    usage = getattr(llm, "last_usage", None)
    if usage is None or getattr(usage, "total_tokens", 0) <= 0:
        return
    model_id = getattr(llm, "model", None) or model_hint or provider
    try:
        usage_ledger.record_usage(
            registry_root,
            feature,
            provider,
            str(model_id),
            input_tokens=getattr(usage, "input_tokens", 0),
            output_tokens=getattr(usage, "output_tokens", 0),
            cache_read_tokens=getattr(usage, "cache_read_tokens", 0),
            cache_write_tokens=getattr(usage, "cache_write_tokens", 0),
        )
    except OSError:
        logger.exception("failed to append LLM usage event (continuing)")


# Update-form keywords. Oxigraph's /query endpoint is read-only regardless, but
# we reject these up front so the escape hatch can never be mistaken for write
# access and the user gets a clear message.
_SPARQL_UPDATE = re.compile(
    r"\b(insert|delete|load|clear|drop|create|add|move|copy)\b", re.IGNORECASE
)

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
# Crosswalk hub auto-rebuild (crosswalk-hub.md productize ②)
# ----------------------------------------------------------------------------
# The hub is a derived projection over the canonical scope, so it goes stale when a
# participating dataset is promoted / appended. promote rebuilds inline (the user
# just gated a citable change — they expect it reflected); append self-heals via a
# DEBOUNCED background rebuild, so a burst of device-feed batches coalesces into one
# rebuild instead of running the O(new-rows) append into an O(all-shared) hub rebuild
# per batch. Both are idempotent (drop + replace) and best-effort (never block).


async def _rebuild_crosswalk_now(
    client: OxigraphClient,
    registry_root: Path,
    perspective_id: str = crosswalk_runtime.DEFAULT_PERSPECTIVE_ID,
) -> dict | None:
    """Rebuild ONE perspective from its persisted config + refresh its registry meta.
    No-op (returns None) when that perspective has no config yet."""
    config = crosswalk_runtime.load_config(registry_root, perspective_id)
    if config is None:
        return None
    outcome = await crosswalk_runtime.build_hub(
        client, config, built_at=datetime.now(UTC).isoformat(), perspective_id=perspective_id
    )
    crosswalk_runtime.write_registry_scaffold(
        registry_root, config, outcome, perspective_id=perspective_id
    )
    return {
        "perspective_id": perspective_id,
        "built_at": outcome.built_at,
        "triple_count": outcome.triple_count,
        "shared": outcome.shared,
        "participants_used": outcome.participants_used,
        "participants_skipped": outcome.participants_skipped,
    }


def _perspective_ids_for_dataset(registry_root: Path, dataset_id: str) -> list[str]:
    """Perspective ids whose config includes ``dataset_id`` — i.e. the perspectives a
    promote/append of that dataset makes stale (multi-perspective, ADR §Phase 1). The
    default (composition) perspective is always considered (it may carry a config before
    its scaffold meta exists)."""
    ids = {crosswalk_runtime.DEFAULT_PERSPECTIVE_ID}
    for meta in crosswalk_runtime.list_perspectives(registry_root):
        ids.add(meta.get("crosswalk_perspective_id") or crosswalk_runtime.DEFAULT_PERSPECTIVE_ID)
    out: list[str] = []
    for pid in sorted(ids):
        try:
            cfg = crosswalk_runtime.load_config(registry_root, pid)
        except Exception:
            cfg = None
        if cfg is not None and dataset_id in cfg.dataset_ids():
            out.append(pid)
    return out


async def _maybe_rebuild_crosswalk(
    client: OxigraphClient, registry_root: Path, dataset_id: str
) -> None:
    """Inline best-effort rebuild after a promote of EVERY perspective the dataset
    participates in. Never raises — a hub-rebuild failure must not fail the promote."""
    try:
        for pid in _perspective_ids_for_dataset(registry_root, dataset_id):
            await _rebuild_crosswalk_now(client, registry_root, pid)
    except Exception:  # never block a promote on the derived-hub rebuild
        logger.exception("crosswalk auto-rebuild after promote failed (continuing)")


def _crosswalk_participates(registry_root: Path, dataset_id: str) -> bool:
    """True iff ``dataset_id`` participates in ANY crosswalk perspective (so an append
    to it makes a hub stale). Best-effort: a malformed registry reads as 'no'."""
    try:
        return bool(_perspective_ids_for_dataset(registry_root, dataset_id))
    except Exception:
        return False


class CrosswalkRebuilder:
    """Debounced background rebuilder: ``schedule(dataset_id)`` (re)arms a short timer
    that coalesces a burst of appends into ONE rebuild, then rebuilds **every
    perspective** the accumulated datasets participate in. Runs off the request path so
    an append returns immediately and the hubs self-heal shortly after."""

    def __init__(
        self, client: OxigraphClient, registry_root: Path, *, delay_s: float = 5.0
    ) -> None:
        self._client = client
        self._root = registry_root
        self._delay = delay_s
        self._task: asyncio.Task[None] | None = None
        self._pending: set[str] = set()  # dataset_ids whose perspectives are stale

    def schedule(self, dataset_id: str | None = None) -> None:
        if dataset_id:
            self._pending.add(dataset_id)
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = asyncio.create_task(self._run(), name="asterism-crosswalk-rebuild")

    async def _run(self) -> None:
        try:
            await asyncio.sleep(self._delay)  # debounce window
        except asyncio.CancelledError:
            return  # superseded by a newer schedule() — let it run instead
        datasets = set(self._pending)
        self._pending.clear()
        try:
            pids: set[str] = set()
            for dsid in datasets:
                pids.update(_perspective_ids_for_dataset(self._root, dsid))
            for pid in sorted(pids):
                await _rebuild_crosswalk_now(self._client, self._root, pid)
        except Exception:
            logger.exception("debounced crosswalk rebuild failed")

    async def aclose(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task


async def _literal_predicates(client: OxigraphClient, graph_iri: str) -> list[dict]:
    """Literal-valued predicates of a dataset's live graph, with a sample value and a
    usage count (most-used first). The crosswalk AI-assist offers these as candidates
    for the concept-bearing predicate; ``isLiteral`` drops ``rdf:type`` / object links
    (a composition is a literal), and the sample lets the model judge by VALUES."""
    q = (
        f"SELECT ?p (SAMPLE(?v) AS ?ex) (COUNT(*) AS ?n) WHERE {{ GRAPH <{graph_iri}> {{ "
        f"?e ?p ?v FILTER(isLiteral(?v)) }} }} GROUP BY ?p ORDER BY DESC(?n) LIMIT 40"
    )
    data = await client.sparql_select(q)
    results = data.get("results", {}) if isinstance(data, dict) else {}
    out: list[dict] = []
    for b in results.get("bindings", []):
        p = b.get("p", {})
        if p.get("type") == "uri":
            out.append({"iri": p["value"], "sample": b.get("ex", {}).get("value", "")})
    return out


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
# The step0 / source / ingest paths accept JSON (#19), XML/JATS, and Word .docx
# (document-ontology layer). CSV/JSON/XML are ingested directly; a .docx is
# CONVERTED to JATS (pandoc, optional) at source-attach and the resulting .xml
# becomes the persisted source. Legacy instrument exports (.tsv/.txt/.dat/.asc)
# are tabular sources too (ADR source-dialect.md — dialect handling lives in
# step0/ingest; the api only widens the entrance). The legacy ``/upload/{kind}``
# starrydata drop stays CSV-only (it feeds the CSV watcher).
_SAFE_SOURCE_NAME = re.compile(
    r"^[A-Za-z0-9._-]{1,128}\.(csv|tsv|txt|dat|asc|json|geojson|xml|docx|pdf)$"
)

# Resolvable IRI base for documents ingested through the API (the document-ontology
# layer). A document dataset's nodes hang off ``…/document/<dataset_id>/<doc_id>``;
# the doc layer's own vocabulary lives in the same ``papers/ontology#`` (lit:) space.
_DOCUMENT_RESOURCE_BASE = "https://kumagallium.github.io/asterism/papers/resource/document"

# The reusable document recall tools auto-attached to an uploaded document dataset
# so it is queryable + citable from the catalog with no per-document authoring. They
# are dataset-agnostic (they run over the canonical FROM-merge), so the same vetted
# content the papers example declares works for any promoted document graph.
_DOCUMENT_TOOL_NAMES = ("search_text", "quote_with_citation", "fetch_passage")
# A document upload may be native JATS (.xml), Word (.docx, converted by pandoc at
# persist time), or born-digital PDF (.pdf, converted by the Docling sidecar at ingest
# time — see ADR pdf-docling-conversion.md). All three land in the same doco/nif graph.
_DOCUMENT_SOURCE_SUFFIXES = (".xml", ".docx", ".pdf")


def _document_tool_specs() -> list[dict]:
    """Raw query-tool dicts (the document recall set) read from the papers example's
    vetted ``query_tools.yaml`` as content — nothing is generated at runtime."""
    root = datasets_root()
    if root is None:
        return []
    path = root / "papers" / "query_tools.yaml"
    if not path.is_file():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    tools = data.get("tools", []) if isinstance(data, dict) else []
    return [t for t in tools if isinstance(t, dict) and t.get("name") in _DOCUMENT_TOOL_NAMES]


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
        # Docling PDF→structure sidecar (ADR pdf-docling-conversion.md). The ONE place
        # the document layer runs ML, isolated out of this image. Unset → PDF ingest
        # fails with a clear 4xx (like absent pandoc); Word/JATS/CSV/JSON are unaffected.
        self.docling_url = (e.get("ASTERISM_DOCLING_URL") or "").strip().rstrip("/") or None
        # Exposure profile (ADR store-mcp-split): when False, the read-only
        # SPARQL relay (POST /api/sparql) is withheld so a sensitive deployment
        # exposes only the typed tools / vetted endpoints. Default open.
        self.expose_raw_sparql = raw_sparql_enabled(e)
        # Operator-set shared secret gating the write / design / raw-SPARQL routes.
        # Unset → those routes fail closed (503) so a sensitive store is never
        # mutated or root-read anonymously. Read-only catalog / health routes stay
        # open. Set it (and front the service with an authenticating proxy) before
        # exposing the api beyond loopback.
        self.api_token = (e.get("ASTERISM_API_TOKEN") or "").strip() or None
        self.graph_prefix = e.get("CSV2RDF_GRAPH_PREFIX", DEFAULT_GRAPH_PREFIX)
        # Default-graph load keeps GRAPH-less SPARQL (MIE examples) working.
        # Set CSV2RDF_USE_DEFAULT_GRAPH=0 to opt back into per-kind named graphs.
        self.use_default_graph = e.get(
            "CSV2RDF_USE_DEFAULT_GRAPH", "1"
        ).strip().lower() not in ("0", "false", "no")
        self.ontology_iri = e.get("CSV2RDF_ONTOLOGY_IRI", _DEFAULT_ONTOLOGY)
        self.resource_iri = e.get("CSV2RDF_RESOURCE_IRI", _DEFAULT_RESOURCE)
        # Instance-owned IRI base for NEWLY designed datasets (ADR
        # instance-iri-base.md): AI-designed namespaces are minted under
        # ``<iri_base>/datasets/<slug>/…`` so a local install's data never lands
        # in the upstream author's namespace nor on example.org. Unset → the
        # RFC 2606 ``https://asterism.invalid`` fallback (self-describingly
        # unpublished); set it to a namespace the operator controls to mint
        # citable identifiers. Bundled datasets keep their dataset.toml IRIs.
        self.iri_base = normalize_iri_base(e.get("ASTERISM_IRI_BASE"))
        self.settle_s = float(e.get("CSV2RDF_SETTLE_S", DEFAULT_SETTLE_S))
        # Per-dataset append inbox (ADR incremental-ingest.md §6): a CSV/JSON dropped
        # at ``<append_drop_root>/<dataset_id>/<file>`` is appended to that dataset's
        # live feed by the append watcher. A transient inbox — a consumed file is
        # deleted (the durable record is the live graph + accumulated source). Default
        # a sibling of the legacy drop root. Disable the watcher with
        # ASTERISM_APPEND_WATCHER=0.
        self.append_drop_root = Path(
            e.get("ASTERISM_APPEND_DROP_ROOT", str(self.drop_root.parent / "append"))
        )
        self.append_watcher = e.get(
            "ASTERISM_APPEND_WATCHER", "1"
        ).strip().lower() not in ("0", "false", "no")
        # Propose self-correction loop (ADR propose-self-correction-loop.md, TODO ④):
        # how many refine rounds propose may run to auto-fix a design against the real
        # source + Tier-0 signatures before returning. 0 disables the loop (plain
        # propose). Per-request ``?autocorrect=N`` overrides this default.
        try:
            self.autocorrect_rounds = max(0, int(e.get("ASTERISM_AUTOCORRECT_ROUNDS", "3")))
        except ValueError:
            self.autocorrect_rounds = 3
        # Wall-clock cap on ONE background job (propose / refine / ingest). A stuck
        # LLM call otherwise runs forever with no signal (the 400-minute propose).
        # "0" disables the cap (None → JobManager runs jobs unbounded, as before).
        try:
            timeout_s = float(e.get("ASTERISM_JOB_TIMEOUT_SECONDS", "3600"))
        except ValueError:
            timeout_s = 3600.0
        self.job_timeout_seconds: float | None = timeout_s if timeout_s > 0 else None


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


def _sanitize_tabular_name(filename: str) -> str:
    """Map an arbitrary uploaded TABULAR filename to a safe ``[A-Za-z0-9._-]+.<ext>``.

    Unlike a document, a tabular source IS referenced by the RML mapping
    (``rml:source`` must equal the persisted filename), so rejecting a human
    filename would strand real instrument exports (「xrd_測定結果.txt」, ADR
    source-dialect.md). Instead the name is slugified with the document sanitizer's
    rules, deterministically — every entrance (inspect / propose / source-attach /
    ingest / append) maps the same original name to the same canonical name, which
    is returned to the client so the design references it. An already-safe name
    passes through unchanged; a disallowed extension is still a 400 (the extension,
    not the name, is the safety property).

    One deliberate divergence from :func:`_sanitize_document_name`: EVERY lossy
    slug gets the short hash of the original name, not only a degenerate one —
    two distinct sources whose stems differ only in the dropped characters
    (``xrd_測定結果`` / ``xrd_参考文献`` both slug to ``xrd``) would otherwise
    collide on one canonical name and silently merge in the RML."""
    name = Path(filename).name
    if _SAFE_SOURCE_NAME.fullmatch(name):
        return name
    ext = Path(name).suffix.lower()
    if not _SAFE_SOURCE_NAME.fullmatch(f"source{ext}"):
        raise HTTPException(
            400,
            "filename must end in .(csv|tsv|txt|dat|asc|json|geojson|xml|docx|pdf) "
            "(max 128 chars)",
        )
    stem = Path(name).stem
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-._")
    slug = re.sub(r"-{2,}", "-", slug)
    h = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
    slug = f"{slug[:111]}-{h}" if slug else f"source-{h}"
    return f"{slug}{ext}"


def _sanitize_document_name(filename: str) -> str:
    """Map an arbitrary uploaded DOCUMENT filename to a safe ``[A-Za-z0-9._-]+.<ext>``.

    A document (xml / docx / pdf) is NOT referenced by an RML mapping — unlike a CSV /
    JSON source, whose filename must match ``rml:source`` — so a human filename with
    spaces, parentheses, non-ASCII or ``+`` is *slugified* rather than rejected: the
    friend uploads ``会議メモ (6月).pdf`` / ``10+3390__x.pdf`` and it just works. The result
    is a single safe path component (no separators, never ``.``/``..``), capped under the
    128-char source-name limit, and the extension is one already checked by the caller.

    Identity is filename-based (the existing append model: same name ⇒ same document).
    When too little of the stem survives slugging (e.g. an all-non-ASCII name), a short
    hash of the ORIGINAL filename is appended so distinct uploads stay distinct and the
    same upload stays idempotent (deterministic — no ``now()``/random)."""
    ext = Path(filename).suffix.lower()
    stem = Path(filename).stem
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-._")
    slug = re.sub(r"-{2,}", "-", slug)
    if len(slug) < 3 or set(slug) <= {".", "-", "_"}:
        h = hashlib.sha256(filename.encode("utf-8")).hexdigest()[:8]
        base = re.sub(r"[^A-Za-z0-9]+", "", slug)
        slug = f"{base}-{h}" if base else f"document-{h}"
    return f"{slug[:120]}{ext}"


# Hard cap on a single uploaded file (bytes). Bounds disk-fill / OOM on the write
# surface (which is fail-closed without ASTERISM_API_TOKEN, but defence in depth).
# Override with ASTERISM_MAX_UPLOAD_BYTES; 0 disables the cap.
_MAX_UPLOAD_BYTES: Final[int] = int(
    os.environ.get("ASTERISM_MAX_UPLOAD_BYTES", str(1 << 30))  # 1 GiB
)


async def _save_upload(
    file: UploadFile,
    dest: Path,
    chunk_size: int = 1 << 20,
    max_bytes: int | None = None,
) -> int:
    """Stream ``file`` to ``dest`` atomically via a sibling ``.tmp`` file.

    Aborts with ``413`` (deleting the partial) the moment the stream exceeds the
    byte cap — Content-Length is never trusted, the cap is enforced on the bytes
    actually read. ``max_bytes=None`` resolves to the module default
    (``_MAX_UPLOAD_BYTES``) at call time; ``0`` disables the cap.
    """
    cap = _MAX_UPLOAD_BYTES if max_bytes is None else max_bytes
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
            total += len(chunk)
            if cap and total > cap:
                raise HTTPException(
                    413, f"upload exceeds the {cap // (1 << 20) or 1} MiB limit"
                )
            await asyncio.to_thread(fh.write, chunk)
    except BaseException:
        # Clean the partial so a rejected/aborted upload cannot fill the volume.
        await asyncio.to_thread(fh.close)
        await asyncio.to_thread(tmp.unlink, True)  # missing_ok
        raise
    await asyncio.to_thread(fh.close)
    # os.replace is atomic on POSIX; the watcher sees a single rename event
    # rather than partial writes.
    await asyncio.to_thread(os.replace, tmp, dest)
    return total


async def _read_upload_bounded(upload: UploadFile, cap: int) -> bytes:
    """Read an upload fully into memory, aborting with 413 past ``cap`` bytes."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(1 << 20)
        if not chunk:
            break
        total += len(chunk)
        if total > cap:
            raise HTTPException(413, f"document exceeds the {cap // (1 << 20)} MiB limit")
        chunks.append(chunk)
    return b"".join(chunks)


async def _persist_converted_docx(
    upload: UploadFile, sdir: Path, name: str
) -> tuple[str, dict]:
    """Convert a Word ``.docx`` upload to JATS (pandoc) and persist it as the source.

    Returns ``(jats_filename, conversion_record)``. The converted ``.jats.xml`` is the
    persisted SOURCE (what gets ingested); the original ``.docx`` is kept alongside
    for re-conversion / provenance (it is not a listed source — ``.docx`` is not a
    source suffix). pandoc absence / failure surfaces as a clear 4xx.
    """
    data = await _read_upload_bounded(upload, documents._MAX_DOCX_BYTES)
    try:
        jats, converter = await asyncio.to_thread(documents.convert_docx_to_jats, data)
    except documents.ConversionError as exc:
        raise HTTPException(422, str(exc)) from exc
    await asyncio.to_thread(sdir.mkdir, parents=True, exist_ok=True)
    jats_name = f"{Path(name).stem}.jats.xml"
    await asyncio.to_thread((sdir / jats_name).write_text, jats, "utf-8")
    await asyncio.to_thread((sdir / name).write_bytes, data)  # keep the original .docx
    conversion = {"converter": converter, "sourceFormat": "docx", "original": name}
    # Drop a per-document conversion sidecar (NOT a .json — that is a source suffix)
    # so multi-document ingest and document append can disclose THIS doc's conversion
    # provenance faithfully; the meta hint only holds the most-recent conversion.
    await asyncio.to_thread(
        (sdir / f"{jats_name}.conversion").write_text,
        json.dumps(conversion, ensure_ascii=False),
        "utf-8",
    )
    return jats_name, conversion


def _doc_conversion_for(xml_path: Path) -> dict | None:
    """Read the per-document conversion sidecar next to ``xml_path`` (or None)."""
    side = xml_path.parent / f"{xml_path.name}.conversion"
    if side.is_file():
        try:
            return json.loads(side.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    return None


async def _persist_source_uploads(
    registry_root: Path, dataset_id: str, files: list[UploadFile]
) -> tuple[list[str], dict | None]:
    """Persist uploaded sources as the dataset's design-time source (Task E, #19).

    Streams each upload into ``registry_root/<id>/source/`` (resetting any prior
    source so it reflects exactly this upload) and records the filenames + source
    kind on the meta. A Word ``.docx`` is CONVERTED to JATS (pandoc) and the
    resulting ``.xml`` becomes the persisted source (the conversion is recorded so
    the document ingest can disclose it). This lets a *design*-stage dataset be
    ingested from the catalog later with no re-attach (reproducibility).
    """
    sdir = registry.source_dir(registry_root, dataset_id)
    if sdir is None:
        raise HTTPException(404, f"dataset {dataset_id!r} not found")
    await asyncio.to_thread(shutil.rmtree, sdir, ignore_errors=True)
    saved: list[str] = []
    conversion: dict | None = None
    for upload in files:
        if upload.filename is None:
            raise HTTPException(400, "missing filename")
        # Documents (xml/docx/pdf) accept ANY filename (slugified — not RML-referenced);
        # tabular names slug DETERMINISTICALLY so they still match the mapping's
        # rml:source (which the design wrote from the same canonical name).
        if Path(upload.filename).suffix.lower() in _DOCUMENT_SOURCE_SUFFIXES:
            name = _sanitize_document_name(upload.filename)
        else:
            name = _sanitize_tabular_name(upload.filename)
        if name.lower().endswith(".docx"):
            jats_name, conversion = await _persist_converted_docx(upload, sdir, name)
            saved.append(jats_name)
        else:
            await _save_upload(upload, sdir / name)
            saved.append(name)
    meta = registry.mark_source_saved(registry_root, dataset_id, saved, conversion=conversion)
    return saved, meta


# Suffixes whose non-dialected (default-dialect) batches accumulate by GROWING the
# persisted file (byte concat + repeated-header drop), rather than overwriting it. All
# read under the default clean comma-CSV rules, so the existing CSV append logic (header
# compare → drop repeated header → concat) is correct for every one of them. Legacy
# instrument exports (.txt/.dat/.asc) and .tsv reach this path via the widened entrance
# (#273); before that only ``.csv`` was appendable, so a clean .txt/.tsv/.dat/.asc second
# batch overwrote the whole persisted source and lost every earlier append (data loss).
_APPENDABLE_TABULAR = {".csv", ".tsv", ".txt", ".dat", ".asc"}


def _accumulate_source_batch(
    sdir: Path, name: str, content: bytes, dialect: SourceDialect | None = None
) -> None:
    """Accumulate an append batch into the dataset's persisted source set (ADR A7).

    So a later snapshot re-ingest reproduces the whole feed from the source set, the
    canonical source file must GROW. For a batch whose name matches an existing tabular
    source read under the default rules (``_APPENDABLE_TABULAR`` — .csv/.tsv/.txt/.dat/
    .asc), we append the batch's data rows — dropping a repeated header line and inserting
    a newline first if the existing file lacks a trailing one. Otherwise (a new name, or a
    JSON batch) we write the file as-is. JSON array-merge compaction is a future step — a
    JSON batch is recorded as its own file.

    A ``dialect`` (ADR source-dialect.md, "Append", plan B) switches to native
    accumulation: the file grows in its OWN dialect (CP932 / tab / preamble
    intact), so the persisted RML normalizes it exactly once at snapshot
    re-ingest. The first batch (no file yet) is written as-is (its single
    preamble+header stays); a later batch has its repeated
    ``skip_rows + 1`` preamble/header physical lines sliced off
    (:func:`asterism.dialect.strip_preamble_and_header`) before its native data
    bytes are concatenated — no header-byte compare, the pinned offset is
    authoritative and decode-free.
    """
    sdir.mkdir(parents=True, exist_ok=True)
    dest = sdir / name
    if dialect is not None and dest.is_file():
        from asterism.dialect import strip_preamble_and_header

        payload = strip_preamble_and_header(content, dialect)
        needs_nl = False
        with dest.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            if size > 0:
                fh.seek(size - 1)
                needs_nl = fh.read(1) not in (b"\n", b"\r")
        with dest.open("ab") as fh:
            if needs_nl and payload:
                fh.write(b"\n")
            fh.write(payload)
        return
    if dialect is None and dest.suffix.lower() in _APPENDABLE_TABULAR and dest.is_file():
        with dest.open("rb") as fh:
            existing_header = fh.readline()
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            needs_nl = False
            if size > 0:
                fh.seek(size - 1)
                needs_nl = fh.read(1) not in (b"\n", b"\r")
        lines = content.splitlines(keepends=True)
        if lines and lines[0].rstrip(b"\r\n") == existing_header.rstrip(b"\r\n"):
            lines = lines[1:]  # drop the repeated header row
        with dest.open("ab") as fh:
            if needs_nl:
                fh.write(b"\n")
            fh.write(b"".join(lines))
    else:
        dest.write_bytes(content)


# Per-dataset marker dir (under source/) recording which batch fingerprints have been
# folded into the persisted source set. A subdirectory (not a *.csv/*.json file), so
# ``registry.list_source_files`` — which enumerates only source-suffixed files,
# non-recursively — never picks it up, and a design-time source reset (rmtree of
# source/) clears it along with the accumulated rows it tracks.
_APPLIED_BATCHES_DIR = ".applied_batches"


def _accumulate_batch_sources(
    sdir: Path,
    batch: list[tuple[str, bytes]],
    batch_id: str,
    dialects: dict[str, SourceDialect] | None = None,
) -> None:
    """Fold a batch's rows into the persisted source set at most once (A7, idempotent).

    Guards :func:`_accumulate_source_batch` with an atomic per-batch marker so a batch
    already folded is not appended again. The succeeded-then-retry case is
    short-circuited earlier (by the append log); this covers the narrower case where a
    *failed* attempt got as far as accumulating the source before erroring — without
    the guard, the retry would append the same rows a second time and a later snapshot
    re-ingest would re-materialize duplicates. The marker is created BEFORE the append
    (``O_EXCL``), favouring "no duplicate rows" — the reported harm — over the
    vanishingly small window where a crash between the marker and the single append
    write leaves those rows only in the live graph (recoverable by a snapshot
    re-baseline, which reads the accumulated source).
    """
    sdir.mkdir(parents=True, exist_ok=True)
    applied = sdir / _APPLIED_BATCHES_DIR
    applied.mkdir(parents=True, exist_ok=True)
    marker = applied / batch_id
    try:
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return  # already folded into the source set — idempotent
    os.close(fd)
    dialects = dialects or {}
    for name, content in batch:
        _accumulate_source_batch(sdir, name, content, dialects.get(name))


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
# Source-dialect design wiring (ADR source-dialect.md — the wizard "read
# settings" surface: expose detected dialects on /api/inspect, accept human
# overrides on the propose routes)
# ----------------------------------------------------------------------------


def _dialects_header_json(inspections: list) -> str:
    """Compact JSON for the ``X-Asterism-Dialects`` response header of /api/inspect.

    ``{source_name: {encoding, delimiter, collapse, skip_rows, origin}}`` for every
    source read with a NON-default dialect (the inspector auto-detects). Default
    sources are omitted — the client renders one row per ``X-Asterism-Source-Names``
    entry and prefills the rest with defaults, so a clean-CSV set yields ``{}`` (zero
    friction). The delimiter is the canonical token (``,`` ``\\t`` ``;`` ``|`` or
    ``whitespace``); ``json.dumps`` escapes the tab so the header stays ASCII/latin-1
    safe. Additive: the Markdown body and ``X-Asterism-Source-Names`` are unchanged.
    """
    out: dict[str, dict[str, object]] = {}
    for ins in inspections:
        dialect = getattr(ins, "dialect", None)
        if dialect is None:  # default dialect → nothing to surface (byte-identical)
            continue
        out[ins.name] = {
            "encoding": dialect.encoding,
            "delimiter": dialect.delimiter,
            "collapse": dialect.collapse,
            "skip_rows": dialect.skip_rows,
            "preamble": dialect.preamble,
            "origin": getattr(ins, "dialect_origin", None) or "detected",
        }
    return json.dumps(out, separators=(",", ":"))


def _parse_dialect_overrides(raw: str) -> dict:
    """Parse + boundary-check the wizard's dialect overrides (a JSON form field).

    ``{source_name: {encoding?, delimiter?, collapse?, skip_rows?}}``. Empty / blank
    → ``{}`` (no override; effective == detected, byte-identical to today). Reuses the
    IR's dialect linter (``mapping_ir._parse_dialects``) with NO declared maps, so only
    the field-level rules run — the same contract the compiled RML annotations enforce:
    a text codec (not a bytes↔bytes codec), a single-char delimiter or ``whitespace``,
    a boolean ``collapse``, a non-negative ``skip_rows``, no unknown keys. An invalid
    value is a readable 422 (never a 500 / a silently bad §9 annotation). Returns
    ``{source_name: SourceDialect}`` (step0 dialects, ready to merge over the detected
    ones in the design loop).
    """
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, f"dialects is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(422, "dialects must be a JSON object {source: {fields}}")
    from asterism_step0.mapping_ir import _parse_dialects

    issues: list[str] = []
    dialects = _parse_dialects(parsed, [], issues)  # no maps → field-only lint
    if issues:
        raise HTTPException(422, "; ".join(issues))
    return dialects


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
# Incremental append core (ADR incremental-ingest.md) — shared by the /append
# endpoint and the per-dataset append watcher
# ----------------------------------------------------------------------------


# Local names of the source-dialect annotations the IR compiler pins on
# rml:logicalSource (ADR source-dialect.md; namespace = substrate.ASTERISM_NS).
# Emitted ONLY for a non-default dialect, so their mere presence marks the source.
_DIALECT_NOTE = re.compile(r"source(Encoding|Delimiter|Collapse|SkipRows)\b")


class _DialectReadError(Exception):
    """The mapping carries dialect annotations but they could not be read.

    Fail-closed signal: a dialected batch cannot be accumulated without its
    pinned header offset, so the caller refuses the append (snapshot re-ingest
    still works). Practically unreachable for a PROMOTED dataset (its annotations
    were already vetted at ingest), but the append path stays defensive.
    """


def _dialected_sources(rml_ttl: str) -> dict[str, SourceDialect]:
    """``{source basename: SourceDialect}`` for every ``rml:logicalSource`` that
    carries pinned dialect annotations (``ast:sourceEncoding`` / …).

    Fast path: a mapping with no annotation local-names anywhere has no dialected
    source (only non-default dialects are ever emitted) — the pre-dialect fleet
    short-circuits here without touching rdflib or the (parallel-shipped)
    ``asterism.dialect`` module. When annotations ARE present but unreadable
    (module absent / unparseable Turtle / out-of-contract value) the read fails
    closed with :class:`_DialectReadError` — the append is refused rather than
    accumulate a dialected batch with an unknown offset.
    """
    if not _DIALECT_NOTE.search(rml_ttl):
        return {}
    try:
        import rdflib
        from asterism.dialect import dialects_from_mapping

        graph = rdflib.Graph()
        graph.parse(data=substrate.substitute_run_id(rml_ttl), format="turtle")
        return {
            Path(name).name: dialect
            for name, dialect in dialects_from_mapping(graph).items()
        }
    except Exception as exc:
        logger.exception("could not read source-dialect annotations (failing closed)")
        raise _DialectReadError(str(exc)) from exc


def _dialect_standin_bytes(raw: bytes, dialect: SourceDialect) -> bytes:
    """A header-only stand-in for a dialected source the batch does not cover:
    the persisted file's preamble+header (its first ``skip_rows + 1`` physical
    lines) in the NATIVE dialect, which :func:`normalize_dialect_sources` reads
    to a header with 0 data rows (so this source contributes 0 new triples). The
    complement of :func:`asterism.dialect.strip_preamble_and_header` — the bytes
    that function drops."""
    from asterism.dialect import strip_preamble_and_header

    data = strip_preamble_and_header(raw, dialect)
    return raw[: len(raw) - len(data)]


class AppendError(Exception):
    """An append precondition / materialization failure carrying an HTTP status.

    The endpoint maps it to an ``HTTPException``; the watcher logs it and moves the
    offending drop file aside. Keeping the orchestration in one place means both
    entry points enforce the same gate (promoted-only, rml:source match, …).
    """

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


async def _append_batch_to_dataset(
    registry_root: Path,
    client: OxigraphClient,
    dataset_id: str,
    batch: list[tuple[str, bytes]],
    *,
    rebuilder: CrosswalkRebuilder | None = None,
) -> dict[str, object]:
    """Append one batch (``[(filename, bytes), …]``) to a dataset's live feed.

    The shared core behind ``POST /api/datasets/{id}/append`` and the append watcher.
    Validates the preconditions (raising :class:`AppendError`), materializes ONLY the
    batch in an isolated dir (so Morph-KGC reads just the new rows), POST-merges it
    into the dataset's live canonical graph, accumulates the batch into the persisted
    source set (A7), and records :func:`registry.mark_appended`. Returns the response
    payload. Trust model unchanged: Morph-KGC + Tier 0 only; a Graph Store POST, never
    a SPARQL UPDATE.

    crosswalk-hub.md ②: if the dataset is a crosswalk participant, the derived hub is
    now stale; ``rebuilder`` (when provided) schedules a DEBOUNCED self-heal so a burst
    of device-feed batches coalesces into one rebuild (the append stays O(new)).
    """
    data = registry.load_dataset(registry_root, dataset_id)
    if data is None:
        raise AppendError(404, f"dataset {dataset_id!r} not found")
    meta = data["meta"]
    rml_ttl = str(data["artifacts"].get("mapping.rml.ttl", "") or "")
    if not rml_ttl.strip():
        raise AppendError(400, "this dataset has no declarative RML mapping to append with")
    if not meta.get("promoted"):
        raise AppendError(
            409,
            "append needs a live canonical graph; ingest then promote the dataset "
            "first (append grows an already-citable feed in place)",
        )
    if meta.get("status") in ("retracted", "deleted"):
        raise AppendError(
            409, f"dataset is {meta.get('status')}; reinstate it before appending"
        )
    if not batch:
        raise AppendError(400, "append requires at least one batch source file")

    sources = substrate.rml_source_names(rml_ttl)
    # ADR source-dialect.md ("Append", plan B): a dialected source appends by
    # growing its persisted copy in its NATIVE dialect (accumulation strips the
    # repeated preamble+header per batch), so the RML normalizes it exactly once
    # at snapshot re-ingest — no un-pin, no double normalization. The pinned
    # dialect per source drives both accumulation and the multi-source stand-in.
    try:
        dialected = _dialected_sources(rml_ttl)
    except _DialectReadError as exc:
        raise AppendError(
            422,
            "this dataset's source dialect annotations could not be read, so a "
            "batch cannot be safely accumulated — use a snapshot re-ingest "
            "(再取り込み) instead",
        ) from exc
    canonical: list[tuple[str, bytes]] = []
    for name, content in batch:
        # Canonicalize the SAME way the design entrances do, so a batch dropped
        # under the instrument's original (non-ASCII) filename matches the
        # rml:source the design pinned.
        try:
            cname = _sanitize_tabular_name(name)
        except HTTPException as exc:
            raise AppendError(400, str(exc.detail)) from exc
        if sources and cname not in sources:
            raise AppendError(
                400,
                f"batch file {cname!r} does not match any rml:source in the mapping "
                f"(expected one of {sorted(sources)})",
            )
        canonical.append((cname, content))
    batch = canonical

    dataset_key = substrate.canonical_graph_iri(dataset_id)
    # The live (citable) graph to grow: the version graph liveGraph points at, or the
    # key graph for a dataset promoted before part5's versioned graphs.
    live_graph = await substrate.live_graph_of(client, dataset_key) or dataset_key
    sdir = registry.source_dir(registry_root, dataset_id)

    # Idempotency (ADR incremental-ingest §3 / A3): identify the batch by its content
    # fingerprint. A re-delivered batch — a retry after the server applied it but the
    # client timed out reading the 200 — is recognised here and short-circuited, so it
    # is NOT re-accumulated into the persisted source (a later snapshot re-ingest would
    # otherwise re-materialize duplicate rows) and its counters/seq are not bumped
    # again. The graph itself is already dedupe-safe via deterministic IRIs (including
    # the provenance activity, pinned below to a content-derived run-id).
    batch_id = substrate.batch_fingerprint(batch)
    prior = registry.find_append_by_batch_id(registry_root, dataset_id, batch_id)
    if prior is not None:
        return {
            "dataset_id": dataset_id,
            "live_graph": live_graph,
            "triples_in_batch": int(prior.get("triples_in_batch", 0)),
            "append_seq": int(prior.get("seq", 0)),
            "crosswalk_stale": False,
            "dataset": meta,
            "idempotent_replay": True,
        }

    work = Path(tempfile.mkdtemp(prefix="asterism-append-"))
    try:
        provided = {n for n, _ in batch}
        for name, content in batch:
            (work / name).write_bytes(content)
        # For a multi-source RML, give any source the batch does NOT cover a
        # header-only stand-in (0 new rows) so Morph-KGC can still materialize the
        # batch without re-reading the full prior source. Best-effort: a persisted
        # tabular source; otherwise Morph-KGC fails loudly (422 below). A DIALECTED
        # stand-in keeps its native preamble+header (its first skip_rows+1 physical
        # lines) so normalize_dialect_sources reads it to a header-only CSV; a clean
        # CSV stand-in is just its header row.
        for src in sources - provided:
            persisted = sdir / src if sdir else None
            if persisted is None or not persisted.is_file():
                continue
            src_dialect = dialected.get(src)
            if src_dialect is not None:
                (work / src).write_bytes(
                    _dialect_standin_bytes(persisted.read_bytes(), src_dialect)
                )
            elif persisted.suffix.lower() == ".csv":
                with persisted.open("rb") as fh:
                    (work / src).write_bytes(fh.readline())
        try:
            # Pin the {__run_id__} provenance activity to a content-derived run-id so a
            # retried batch re-mints the SAME activity IRI (dedupe) instead of orphaning
            # the prior attempt's activity/provenance subtree in the live graph.
            result = await substrate.run_append_ingest(
                rml_ttl,
                work,
                client,
                live_graph,
                run_id=substrate.run_id_for_batch(batch_id),
            )
        except substrate.RmlValidationError as exc:  # malformed design vs real data
            raise AppendError(422, "; ".join(exc.issues)) from exc
        except RuntimeError as exc:  # morph-kgc missing / materialization failed
            raise AppendError(422, str(exc)) from exc
    finally:
        shutil.rmtree(work, ignore_errors=True)

    # Accumulate the batch into the persisted source set (additive, A7) — once. The
    # succeeded-then-retry case is already short-circuited above; this guards the
    # narrower case where a FAILED attempt got as far as accumulating the source before
    # erroring, so a retry does not append the same rows twice.
    if sdir is not None:
        await asyncio.to_thread(_accumulate_batch_sources, sdir, batch, batch_id, dialected)
    all_files = [p.name for p in registry.list_source_files(registry_root, dataset_id)]

    triples_in_batch = int(result["triples_in_batch"])
    append_seq = registry.next_append_seq(registry_root, dataset_id)
    new_meta = registry.mark_appended(
        registry_root,
        dataset_id,
        batch_files=[n for n, _ in batch],
        source_files=all_files,
        triples_in_batch=triples_in_batch,
        appended_at=datetime.now(UTC).isoformat(),
        append_seq=append_seq,
        batch_id=batch_id,
    )
    # crosswalk-hub.md ②: the hub is a derived projection over the canonical scope;
    # this append may have introduced new shared values. Mark stale ONLY if the
    # dataset participates, and schedule a debounced rebuild (self-healing).
    crosswalk_stale = _crosswalk_participates(registry_root, dataset_id)
    if crosswalk_stale and rebuilder is not None:
        rebuilder.schedule(dataset_id)
    return {
        "dataset_id": dataset_id,
        "live_graph": live_graph,
        "triples_in_batch": triples_in_batch,
        "append_seq": append_seq,
        "crosswalk_stale": crosswalk_stale,
        "dataset": new_meta,
        "idempotent_replay": False,
    }


async def _append_document_to_dataset(
    registry_root: Path,
    client: OxigraphClient,
    dataset_id: str,
    upload: UploadFile,
    *,
    docling_url: str | None = None,
) -> dict[str, object]:
    """Append ONE document to an existing, promoted document dataset's live graph.

    The document analogue of :func:`_append_batch_to_dataset`. A document dataset has
    no RML — it uses the closed, deterministic structurer — so this structures just the
    new doc and POST-merges its triples into the dataset's live canonical graph. Each
    document is namespaced by its filename (its ``paper_iri``), so documents accumulate
    without collision and re-appending a file dedupes by deterministic IRIs (set
    semantics). This lets a "定例ミーティング"-style dataset grow document by document,
    with ``search_text`` / ``quote_with_citation`` spanning every document added. Trust
    model unchanged: no generated code (Tier 0 structurer), a Graph Store POST not a
    SPARQL UPDATE.
    """
    data = registry.load_dataset(registry_root, dataset_id)
    if data is None:
        raise AppendError(404, f"dataset {dataset_id!r} not found")
    meta = data["meta"]
    if str((meta or {}).get("source_kind") or "csv") != "xml":
        raise AppendError(
            400, "this dataset is not a document dataset (create one via POST /api/documents)"
        )
    if not meta.get("promoted"):
        raise AppendError(
            409,
            "append needs a live canonical graph; ingest then promote the first "
            "document before adding more (append grows an already-citable feed)",
        )
    if meta.get("status") in ("retracted", "deleted"):
        raise AppendError(
            409, f"dataset is {meta.get('status')}; reinstate it before appending"
        )
    if upload.filename is None:
        raise AppendError(400, "missing filename")
    if Path(upload.filename).suffix.lower() not in _DOCUMENT_SOURCE_SUFFIXES:
        raise AppendError(400, "a document must be a JATS .xml, a Word .docx, or a .pdf file")
    # Accept any human filename — a document is not RML-referenced (slugify, don't reject).
    name = _sanitize_document_name(upload.filename)

    sdir = registry.source_dir(registry_root, dataset_id)
    if sdir is None:
        raise AppendError(404, f"dataset {dataset_id!r} not found")
    # Persist the new document into the source set ADDITIVELY (no reset — unlike the
    # design-time _persist_source_uploads) and drop the per-doc conversion sidecar so
    # provenance survives a later snapshot re-ingest (A7). Word converts via pandoc; a
    # PDF persists RAW and converts via the Docling sidecar (the JATS is held in memory,
    # the raw .pdf is the recorded source so a re-ingest re-runs the pinned converter).
    conversion: dict | None = None
    if name.lower().endswith(".docx"):
        xml_name, conversion = await _persist_converted_docx(upload, sdir, name)
        xml_text = await asyncio.to_thread((sdir / xml_name).read_text, "utf-8")
    elif name.lower().endswith(".pdf"):
        data = await _read_upload_bounded(upload, documents._MAX_PDF_BYTES)
        await asyncio.to_thread(sdir.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread((sdir / name).write_bytes, data)  # raw .pdf = the source
        try:
            xml_text, converter = await asyncio.to_thread(
                documents.convert_pdf_to_jats, data, sidecar_url=docling_url
            )
        except documents.ConversionError as exc:
            raise AppendError(422, str(exc)) from exc
        conversion = {"converter": converter, "sourceFormat": "pdf", "original": name}
        await asyncio.to_thread(
            (sdir / f"{name}.conversion").write_text,
            json.dumps(conversion, ensure_ascii=False),
            "utf-8",
        )
        xml_name = name
    else:
        await _save_upload(upload, sdir / name)
        xml_name = name
        xml_text = await asyncio.to_thread((sdir / name).read_text, "utf-8")
    doc_id = documents.derive_doc_id(xml_text, fallback=Path(xml_name).stem)
    paper_iri = f"{_DOCUMENT_RESOURCE_BASE}/{dataset_id}/{doc_id}"

    dataset_key = substrate.canonical_graph_iri(dataset_id)
    live_graph = await substrate.live_graph_of(client, dataset_key) or dataset_key

    work = Path(tempfile.mkdtemp(prefix="asterism-doc-append-"))
    try:
        nt = await asyncio.to_thread(
            documents.document_to_nt_file,
            xml_text,
            paper_iri=paper_iri,
            work_dir=str(work),
            conversion=conversion,
        )
        triples = await substrate.stream_nt_file_to_oxigraph(nt, client, live_graph)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    all_files = [p.name for p in registry.list_source_files(registry_root, dataset_id)]
    append_seq = registry.next_append_seq(registry_root, dataset_id)
    # The document's content fingerprint as the append idempotency key (parallel to the
    # CSV/RML path). A document dataset is already graph-idempotent (paper_iri is
    # content-derived) and source-idempotent (each doc persists by filename overwrite,
    # not row accumulation), so this records the key without a short-circuit — no
    # duplicate rows can accrue, unlike the growing CSV source.
    batch_id = substrate.batch_fingerprint([(xml_name, xml_text.encode("utf-8"))])
    new_meta = registry.mark_appended(
        registry_root,
        dataset_id,
        batch_files=[xml_name],
        source_files=all_files,
        triples_in_batch=triples,
        appended_at=datetime.now(UTC).isoformat(),
        append_seq=append_seq,
        batch_id=batch_id,
    )
    return {
        "dataset_id": dataset_id,
        "live_graph": live_graph,
        "paper_iri": paper_iri,
        "triples_in_batch": triples,
        "append_seq": append_seq,
        "dataset": new_meta,
    }


def _log_job(cfg: Settings, record: dict[str, object]) -> None:
    """Append one ingest/append outcome as a JSON line to the jobs log (best-effort).

    The activity ledger behind ``GET /jobs`` (アクティビティ). Writers: the legacy
    kind watcher, the append watcher, the manual append routes, and the Workbench
    ingest job (``kind:"ingest"``) — so the activity view reflects every write
    path, not only the unattended ones."""
    try:
        cfg.jobs_log.parent.mkdir(parents=True, exist_ok=True)
        with cfg.jobs_log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        logger.exception("failed to write job log (continuing)")


def _quarantine_drop(root: Path, dataset_id: str, path: Path) -> None:
    """Move a failed drop file into ``<root>/<dataset_id>/.error/`` (a hidden dir the
    watcher skips), so it is not reprocessed and is kept for inspection."""
    try:
        err_dir = root / dataset_id / ".error"
        err_dir.mkdir(parents=True, exist_ok=True)
        os.replace(path, err_dir / path.name)
    except OSError:
        logger.exception("failed to quarantine drop file %s (continuing)", path)


async def _append_watch_loop(
    cfg: Settings,
    client: OxigraphClient,
    stop: asyncio.Event,
    *,
    events_source=None,
    crosswalk_rebuilder: CrosswalkRebuilder | None = None,
) -> None:
    """Per-dataset append watcher (ADR incremental-ingest.md §6).

    A settled CSV/JSON dropped at ``<append_drop_root>/<dataset_id>/<file>`` is
    appended to that dataset's live feed. The inbox is transient: a successfully
    appended file is **deleted** (the durable record is the live graph + the
    accumulated source set, A7); a failed file is quarantined under ``.error/``. Each
    outcome is logged to the jobs log. ``events_source`` drives the loop in tests.
    """
    # Resolve to match the canonical paths watch_tree dispatches (macOS reports
    # ``/private/var/…`` for a ``/var/…`` symlinked root), so ``relative_to`` below
    # extracts the ``<dataset_id>`` component correctly.
    root = cfg.append_drop_root.resolve()

    async def on_ready(path: Path) -> None:
        try:
            rel = path.relative_to(root)
        except ValueError:
            return
        if len(rel.parts) < 2:  # need <dataset_id>/<file>
            return
        dataset_id = rel.parts[0]
        name = path.name
        try:
            content = await asyncio.to_thread(path.read_bytes)
            result = await _append_batch_to_dataset(
                cfg.registry_root,
                client,
                dataset_id,
                [(name, content)],
                rebuilder=crosswalk_rebuilder,
            )
            await asyncio.to_thread(path.unlink)  # consume the transient drop file
            _log_job(
                cfg,
                {
                    "kind": "append",
                    "dataset_id": dataset_id,
                    "file": name,
                    "status": "ok",
                    "triples_in_batch": result["triples_in_batch"],
                    "append_seq": result["append_seq"],
                    "ended_at": datetime.now(UTC).isoformat(),
                },
            )
            logger.info(
                "append-watcher: %s/%s -> +%s triples (seq %s)",
                dataset_id,
                name,
                result["triples_in_batch"],
                result["append_seq"],
            )
        except AppendError as exc:
            _quarantine_drop(root, dataset_id, path)
            _log_job(
                cfg,
                {
                    "kind": "append",
                    "dataset_id": dataset_id,
                    "file": name,
                    "status": "error",
                    "error": exc.detail,
                    "ended_at": datetime.now(UTC).isoformat(),
                },
            )
            logger.warning("append-watcher: %s/%s failed: %s", dataset_id, name, exc.detail)
        except Exception as exc:  # never let one bad file kill the loop
            _quarantine_drop(root, dataset_id, path)
            _log_job(
                cfg,
                {
                    "kind": "append",
                    "dataset_id": dataset_id,
                    "file": name,
                    "status": "error",
                    "error": repr(exc),
                    "ended_at": datetime.now(UTC).isoformat(),
                },
            )
            logger.exception("append-watcher: %s/%s crashed (continuing)", dataset_id, name)

    await watch_tree(
        root, on_ready, settle_s=cfg.settle_s, stop_event=stop, events_source=events_source
    )


def _validate_design_at_materialize(
    registry_root: Path, dataset_id: str | None, rml_ttl: str | None
) -> list[str]:
    """Advisory design validation for the materialize response (NEVER raises).

    Runs the SAME ``validate_rml_design`` the ingest gate runs — column references +
    Tier 0 function parameters against the dataset's REAL persisted source CSVs — but
    catches :class:`RmlValidationError` and returns its ``issues`` list instead of
    raising, so a bad column / wrong function parameter surfaces at materialize (where
    the one-click "ask AI to fix" lives) without failing the save.

    Also appends the source-independent ``design_advisories`` (e.g. disconnected
    entities — a mapping whose measurement never links to its material cannot
    answer cross-entity questions), which need no persisted CSVs.

    Returns ``[]`` when the design is clean OR when nothing can be checked: no RML, no
    ``dataset_id`` (a brand-new design has no persisted source yet — the workbench
    attaches it AFTER materialize). The hard ingest gate
    still catches a bad design once a source is attached; this is purely advisory and
    best-effort (any unexpected error degrades to "no advice", never a 500).
    """
    if not (rml_ttl or "").strip() or not dataset_id:
        return []
    prepared = substrate.substitute_run_id(rml_ttl)
    try:
        source_paths = registry.list_source_files(registry_root, dataset_id)
    except Exception:
        source_paths = []
    source_dir = source_paths[0].parent if source_paths else None
    try:
        # With the real sources the connectivity advisory also names the
        # join-key candidates (work order, not just a diagnosis). Review notes
        # (unmapped columns) are human-judgement items: shown here so the person
        # can weigh them / include them in a fix request, but NOT fed to the
        # automatic corrective loop (which would over-fix noise columns).
        advisories = substrate.design_advisories(
            prepared, source_dir
        ) + substrate.design_review_notes(prepared, source_dir)
    except Exception:  # advisory only
        logger.exception("design advisories at materialize failed (continuing)")
        advisories = []
    try:
        if not source_paths:
            return advisories
        # Validate the run-id-substituted form so the runtime-only {__run_id__}
        # placeholder is never flagged (matches the ingest gate exactly).
        substrate.validate_rml_design(prepared, source_dir)
        return advisories
    except substrate.RmlValidationError as exc:
        return list(exc.issues) + advisories
    except Exception:  # advisory only — a check failure must never break materialize
        logger.exception("advisory design validation at materialize failed (continuing)")
        return advisories


# ----------------------------------------------------------------------------
# App builder
# ----------------------------------------------------------------------------


def build_app(
    settings: Settings | None = None,
    *,
    oxigraph_client: OxigraphClient | None = None,
    start_watcher: bool = True,
    llm_factory: Callable[[str | None], LLMClient] | None = None,
    llm_resolver: Callable[..., LLMClient] | None = None,
) -> FastAPI:
    """Build the FastAPI app.

    LLM client construction goes through ``_resolve_llm(provider, model, api_base,
    key, max_tokens=None)``, built once here:

    * ``llm_resolver`` — provider-aware injection (new tests use this to exercise
      multi-provider routing without a network). Must accept the keyword
      ``max_tokens`` (the parsed ``X-LLM-Max-Tokens`` header, None when absent).
    * ``llm_factory`` — legacy 1-arg (key → client) injection; kept so existing
      tests that inject a mock keep working. Only the key is honored; provider /
      model / api_base / max_tokens are ignored (those tests never send provider
      headers).
    * default — :func:`asterism_step0.llm.make_llm`, which returns the Anthropic
      default when no provider is selected (byte-for-byte the historical path) and
      an OpenAI-compatible client otherwise (Sakura AI Engine via a custom base_url,
      etc.).
    """
    cfg = settings or Settings()
    if llm_resolver is not None:
        _resolve_llm = llm_resolver
    elif llm_factory is not None:

        def _resolve_llm(
            provider: str,
            model: str | None,
            api_base: str | None,
            api_key: str | None,
            max_tokens: int | None = None,
        ) -> LLMClient:
            return llm_factory(api_key)
    else:

        def _resolve_llm(
            provider: str,
            model: str | None,
            api_base: str | None,
            api_key: str | None,
            max_tokens: int | None = None,
        ) -> LLMClient:
            return build_llm_client(
                provider, model=model, api_base=api_base, api_key=api_key,
                max_tokens=max_tokens,
            )
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
            # part5 storage reclaim: enqueue any orphaned version graph — a
            # `canonical/{id}/v{n}` that neither a liveGraph nor a stagedGraph pointer
            # names. A re-ingest that overwrote its staged pointer, a crash partial left
            # before the staged pointer was written, a deleted dataset's leftover
            # version, or an orphan that predates this reclamation all end up here. It is
            # sound only because startup has no ingest in flight (an in-flight target has
            # no pointer yet); it runs before the server accepts requests. Idempotent;
            # the sweeper drops the enqueued graphs off the request path.
            orphans = await substrate.reconcile_orphan_versions(client)
            if orphans:
                logger.info(
                    "enqueued %d orphaned version graph(s) for reclaim", len(orphans)
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
        # crosswalk-hub.md ②: a debounced rebuilder self-heals the hub after appends
        # to a participating dataset (gated on start_watcher so unit tests opt out).
        crosswalk_rebuilder = (
            CrosswalkRebuilder(client, cfg.registry_root) if start_watcher else None
        )
        # Per-dataset append watcher (ADR incremental-ingest.md §6): a CSV/JSON dropped
        # at <append_drop_root>/<id>/ grows that dataset's live feed. Gated on
        # start_watcher (unit tests opt out) and ASTERISM_APPEND_WATCHER. It shares the
        # crosswalk rebuilder so unattended appends self-heal the hub too.
        append_watcher: asyncio.Task[None] | None = None
        if start_watcher and cfg.append_watcher:
            cfg.append_drop_root.mkdir(parents=True, exist_ok=True)
            append_watcher = asyncio.create_task(
                _append_watch_loop(
                    cfg, client, stop, crosswalk_rebuilder=crosswalk_rebuilder
                ),
                name="asterism-append-watcher",
            )
        app.state.client = client
        app.state.watcher_cfg = watcher_cfg
        app.state.watcher_task = task
        app.state.sweeper_task = sweeper
        app.state.append_watcher_task = append_watcher
        app.state.crosswalk_rebuilder = crosswalk_rebuilder
        app.state.jobs = JobManager(job_timeout_seconds=cfg.job_timeout_seconds)
        try:
            yield
        finally:
            stop.set()
            if crosswalk_rebuilder is not None:
                await crosswalk_rebuilder.aclose()
            for bg in (task, sweeper, append_watcher):
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

    def require_write_auth(
        authorization: str | None = Header(default=None),
        x_asterism_token: str | None = Header(default=None),
    ) -> None:
        """Fail-closed gate for the write / design / raw-SPARQL routes.

        When ``ASTERISM_API_TOKEN`` is unset these routes are *disabled* (503) —
        the opposite of an anonymously-open default — so a sensitive store is
        never mutated or root-read without a credential. When it is set, the
        caller must present it as ``Authorization: Bearer <token>`` or
        ``X-Asterism-Token: <token>`` (constant-time compared). Read-only
        catalog / health / job-stream routes stay open.
        """
        token = cfg.api_token
        if not token:
            raise HTTPException(
                503,
                "この操作は ASTERISM_API_TOKEN を設定するまで無効です "
                "(機微ストアへの匿名の書き込み・生 SPARQL を防ぐ fail-closed)",
            )
        presented: str | None = None
        if authorization and authorization.startswith("Bearer "):
            presented = authorization[len("Bearer ") :].strip()
        elif x_asterism_token:
            presented = x_asterism_token.strip()
        if not presented or not hmac.compare_digest(presented, token):
            raise HTTPException(401, "API トークンがありません/一致しません")

    # The set of routes that mutate the store/registry or expose raw SPARQL.
    _write_auth = [Depends(require_write_auth)]

    @app.get("/health")
    async def health() -> JSONResponse:
        client: OxigraphClient = app.state.client
        ok = await client.ping()
        return JSONResponse(
            {"status": "ok" if ok else "degraded", "oxigraph": ok},
            status_code=200 if ok else 503,
        )

    @app.get("/describe")
    async def describe_iri(
        iri: str,
        format: str | None = None,
        accept: str | None = Header(default=None),
    ) -> Response:
        """Dereference one IRI against the PUBLISHED (canonical + ontology)
        scope — ADR instance-iri-base.md phase 2. Content-negotiated: Turtle
        for machines (Accept: text/turtle / ?format=ttl), HTML for browsers.
        Tokenless by design: a bounded read of already-published data (same
        exposure class as the typed tools, narrower than the raw-SPARQL
        escape) — see the module docstring of :mod:`asterism_api.describe`."""
        iri = iri.strip()
        if not describe_mod.valid_iri(iri):
            raise HTTPException(400, "iri must be an absolute http(s) IRI")
        wants_turtle = format in ("ttl", "turtle", "nt") or (
            format is None
            and accept is not None
            and ("text/turtle" in accept or "application/n-triples" in accept)
        )
        client: OxigraphClient = app.state.client
        try:
            if wants_turtle:
                graphs = sorted(
                    set(await substrate.canonical_graphs(client))
                    | set(await substrate.ontology_graphs(client))
                )
                if not graphs:
                    raise HTTPException(404, "no published data on this instance")
                q_out, q_in = describe_mod.turtle_queries(iri, graphs)
                turtle = await client.sparql_construct(q_out)
                inbound = await client.sparql_construct(q_in)
                if inbound.strip():
                    turtle = f"{turtle}\n{inbound}"
                return Response(turtle, media_type="text/turtle")
            data = await describe_mod.fetch_description(client, iri)
        except HTTPException:
            raise
        except Exception as exc:
            # Same posture as /api/sparql: never echo upstream details.
            logger.exception("describe error")
            raise HTTPException(502, "upstream SPARQL error") from exc
        if data is None:
            graphs = await substrate.canonical_graphs(client)
            return HTMLResponse(
                describe_mod.render_not_found(iri, len(graphs)), status_code=404
            )
        return HTMLResponse(describe_mod.render_html(iri, data))

    @app.get("/api/instance")
    async def instance_info() -> dict[str, object]:
        """Public identity of this install (ADR instance-iri-base.md): where new
        designs mint their namespaces. Not a secret — the base is embedded in
        every minted IRI — so it is readable without the write token; the UI
        settings surface shows it and flags the unconfigured default."""
        return {
            "iri_base": cfg.iri_base,
            "iri_base_configured": cfg.iri_base != DEFAULT_IRI_BASE,
        }

    @app.post("/upload/{kind}", dependencies=_write_auth)
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
        sources are dispatched per file by extension (#19). Filenames are
        canonicalized (:func:`_sanitize_tabular_name`) and echoed back in the
        ``X-Asterism-Source-Names`` header so the client knows the names the
        design's ``rml:source`` must use. The ``X-Asterism-Dialects`` header carries
        the structured detected dialect of every NON-default source (encoding /
        delimiter / collapse / skip_rows / origin) for the wizard "read settings"
        panel — a clean-CSV set yields ``{}`` and the panel stays hidden (ADR
        source-dialect.md). A file the inspector cannot decode or parse is a 422 (a
        readable message, not a traceback) — dialect detection normally prevents this.
        """
        if not files:
            raise HTTPException(400, "no files uploaded")
        with tempfile.TemporaryDirectory() as td:
            paths: list[Path] = []
            for upload in files:
                if upload.filename is None:
                    raise HTTPException(400, "missing filename")
                dest = Path(td) / _sanitize_tabular_name(upload.filename)
                await _save_upload(upload, dest)
                paths.append(dest)
            try:
                inspections, fks = inspect_source_set(paths, fk_hint_columns=fk or None)
            except UnicodeDecodeError as exc:
                raise HTTPException(
                    422,
                    f"ソースをテキストとして読み取れませんでした ({exc.encoding} として"
                    f"デコード不能)。エンコーディングが異なる可能性があります: {exc}",
                ) from exc
            except csv.Error as exc:
                raise HTTPException(
                    422, f"ソースを表として解析できませんでした: {exc}"
                ) from exc
            markdown = render_markdown(inspections, fks)
        return Response(
            content=markdown,
            media_type="text/markdown",
            headers={
                "X-Asterism-Source-Names": ",".join(p.name for p in paths),
                # Structured detected dialects for the wizard "read settings" panel
                # (ADR source-dialect.md); non-default sources only, clean set → {}.
                "X-Asterism-Dialects": _dialects_header_json(inspections),
            },
        )

    @app.post("/api/models/available")
    def models_available(body: ModelsAvailableBody) -> JSONResponse:
        """List the models a user-brought key can use (model picker #②).

        ``anthropic`` → Anthropic ``models.list()``; openai-compatible →
        ``client.models.list()``. The key is used only for this call and never
        stored (D7). Falls back to the operator's server-side key when the body
        omits one (so the picker works without typing a key on an instance that
        opted in). Sync ``def`` so FastAPI runs the blocking SDK call in a
        threadpool. No write-auth — same trust model as propose (the caller's own
        key). ``api_base`` is SSRF-guarded for openai-compatible providers.
        """
        provider = (body.provider or "anthropic").strip().lower()
        api_key = body.api_key
        api_base = body.api_base
        if not api_key:
            # Fall back to the operator's shared key; for openai-compatible use its
            # PINNED base (not the request's) so the shared key is never sent out.
            api_key, pinned = server_keys.resolve(provider, cfg.registry_root)
            if pinned:
                api_base = pinned
        if api_base and provider not in ("", "anthropic", "claude"):
            _validate_llm_api_base(api_base)
        try:
            models = list_available_models(provider, api_key=api_key, api_base=api_base)
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:  # network / auth / provider-side failure
            raise HTTPException(502, f"could not list models: {exc}") from exc
        return JSONResponse({"models": models})

    @app.get("/api/llm/server-keys")
    def llm_server_keys() -> JSONResponse:
        """Which providers have an operator-configured server-side key.

        Booleans only — never the key. Lets the UI let a user proceed (and fetch
        models / Ask / propose) without typing a key when the server already has
        one for that provider. Read-open (reveals no secret); all-false unless a
        key was set via env or ``POST /api/llm/server-keys`` (opt-in)."""
        return JSONResponse(
            {"providers": server_keys.configured_providers(cfg.registry_root)}
        )

    @app.post("/api/llm/server-keys", dependencies=_write_auth)
    def set_llm_server_key(body: ServerKeyBody) -> JSONResponse:
        """Set (blank key = clear) the shared server-side key for a provider.

        Write-gated (login + ``ASTERISM_API_TOKEN``) — same trust as the other
        write routes (on the deployed box the SPA sends the token for any
        logged-in user). Persisted server-side and never returned. For
        openai-compatible the ``api_base`` is required + SSRF-guarded and gets
        pinned to the key. Returns the updated booleans (never the key)."""
        provider = (body.provider or "").strip().lower()
        if provider not in server_keys.PROVIDERS:
            raise HTTPException(400, f"unknown provider: {body.provider!r}")
        key = (body.api_key or "").strip()
        base = (body.api_base or "").strip() or None
        if key and provider == "openai-compatible":
            if not base:
                raise HTTPException(
                    400, "openai-compatible の共有キーには endpoint (api_base) が必要です"
                )
            _validate_llm_api_base(base)
        try:
            server_keys.set_server_key(cfg.registry_root, provider, key, base)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return JSONResponse(
            {"providers": server_keys.configured_providers(cfg.registry_root)}
        )

    @app.post("/api/propose")
    async def propose(
        files: list[UploadFile] = File(
            ..., description="Source file(s) to model (CSV or JSON)"
        ),
        domain: str = Form(
            default="",
            description="Domain hint (Markdown). Optional — improves quality but not required.",
        ),
        language: str = Form(
            default="",
            description=(
                "Output language for the proposal's human-readable prose (e.g. 'ja'). "
                "Empty → English. Headings / identifiers / code stay English."
            ),
        ),
        dialects: str = Form(
            default="",
            description=(
                "Optional JSON of per-source read-dialect overrides (ADR source-"
                "dialect.md) — {source: {encoding?, delimiter?, collapse?, skip_rows?}}. "
                "Empty → auto-detected dialects only (byte-identical to today)."
            ),
        ),
        fk: list[str] = Query(default=[], description="FK hint column (repeatable)"),
        autocorrect: int | None = Query(
            default=None,
            description=(
                "Self-correction rounds (TODO ④): propose auto-fixes the design against "
                "the real source + Tier-0 signatures for up to N refine rounds. Absent → "
                "server default (ASTERISM_AUTOCORRECT_ROUNDS); 0 = plain propose."
            ),
        ),
        x_api_key: str | None = Header(
            default=None,
            description="User-brought API key (D7: used for this run only, never stored)",
        ),
        x_llm_provider: str | None = Header(
            default=None,
            description="LLM provider (anthropic | openai | openai-compatible); absent → anthropic",
        ),
        x_llm_model: str | None = Header(default=None, description="Model id override"),
        x_llm_api_base: str | None = Header(
            default=None, description="Custom base URL (OpenAI-compatible, e.g. Sakura AI Engine)"
        ),
        x_llm_max_tokens: str | None = Header(
            default=None,
            description="Output-token cap override (positive integer); absent → provider default",
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
        # Parse (and 400/422 on) the cap header + dialect overrides BEFORE the upload
        # dir exists so a bad value cannot leak a temp dir.
        max_tokens = _llm_max_tokens(x_llm_max_tokens)
        dialect_overrides = _parse_dialect_overrides(dialects)

        import tempfile as _tempfile

        tmpdir = _tempfile.mkdtemp(prefix="asterism-propose-")
        paths: list[Path] = []
        for upload in files:
            if upload.filename is None:
                raise HTTPException(400, "missing filename")
            dest = Path(tmpdir) / _sanitize_tabular_name(upload.filename)
            await _save_upload(upload, dest)
            paths.append(dest)

        provider, model, api_base, key = _llm_coords(
            x_api_key, x_llm_provider, x_llm_model, x_llm_api_base, cfg.registry_root
        )
        llm = _resolve_llm(provider, model, api_base, key, max_tokens=max_tokens)
        fk_cols = fk or None
        rounds = cfg.autocorrect_rounds if autocorrect is None else max(0, autocorrect)

        async def propose_job(
            emit: Callable[..., None], should_cancel: Callable[[], bool]
        ) -> dict[str, object]:
            # The self-correction loop (TODO ④) is a blocking, synchronous orchestrator;
            # run it in ONE worker thread and bridge its per-round progress back onto the
            # event loop via call_soon_threadsafe (emit is ONLY safe from the loop, never
            # from the worker thread — jobs.py). Usage is recorded per LLM call inside the
            # thread (best-effort file append, safe off-loop). The temp source dir the
            # loop validates against is cleaned up here, AFTER the loop returns.
            loop = asyncio.get_running_loop()

            def on_progress(data: dict[str, object]) -> None:
                loop.call_soon_threadsafe(lambda: emit(**data))

            def on_llm_call(feature: str) -> None:
                _record_llm_usage(cfg.registry_root, feature, provider, llm, model)

            # In-call progress from the LLM client itself (multi-generation
            # continuations / auto-downgrade notes), bridged thread→loop like
            # on_progress. Message style matches design_loop's Japanese frames.
            def on_generation(current: int, total: int) -> None:
                loop.call_soon_threadsafe(
                    lambda: emit(phase="llm", message=f"モデル生成中 (パート {current}/{total})")
                )

            def on_note(note: str) -> None:
                loop.call_soon_threadsafe(lambda: emit(phase="llm", message=note))

            _arm_llm_callbacks(
                llm,
                should_cancel=should_cancel,
                on_generation=on_generation,
                on_note=on_note,
            )

            try:
                result = await asyncio.to_thread(
                    design_loop.run_design_loop,
                    list(paths),
                    domain,
                    Path(tmpdir),
                    fk_hint_columns=fk_cols,
                    llm=llm,
                    max_rounds=rounds,
                    on_progress=on_progress,
                    on_llm_call=on_llm_call,
                    language=language or None,
                    should_cancel=should_cancel,
                    dialect_overrides=dialect_overrides,
                    iri_base=cfg.iri_base,
                )
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)
            return {
                "proposal_md": result.proposal_md,
                "inspection_md": result.csv_inspection_md,
                "metadata": result.metadata,
                # The canonicalized upload names (== the rml:source names the design
                # references — non-ASCII instrument filenames are slugged on save).
                "source_files": [p.name for p in paths],
                # Additive self-correction summary (TODO ④). Absent fields keep the
                # response backward-compatible with clients that only read proposal_md.
                "autocorrect": {
                    "enabled": rounds > 0,
                    "converged": result.converged,
                    "terminal_reason": result.terminal_reason,
                    "initial_issue_count": result.initial_issue_count,
                    "final_issue_count": len(result.remaining_issues),
                    "rounds": [
                        {"n": r.n, "issue_count": r.issue_count, "categories": r.categories}
                        for r in result.rounds
                    ],
                    "remaining_issues": result.remaining_issues,
                    "tabular_only": result.tabular_only,
                    "coverage_dropped": result.coverage_dropped,
                },
            }

        jobs: JobManager = app.state.jobs
        job_id = jobs.start_coro(propose_job)
        return JSONResponse({"job_id": job_id}, status_code=202)

    @app.post("/api/propose/skeleton")
    async def propose_skeleton_endpoint(
        files: list[UploadFile] = File(
            ..., description="Source file(s) to model (CSV or JSON)"
        ),
        domain: str = Form(default="", description="Domain hint (Markdown). Optional."),
        language: str = Form(
            default="",
            description="Output language for prose (e.g. 'ja'); headings/identifiers stay English.",
        ),
        dialects: str = Form(
            default="",
            description="Per-source read-dialect overrides as JSON (ADR source-dialect.md).",
        ),
        fk: list[str] = Query(default=[], description="FK hint column (repeatable)"),
        x_api_key: str | None = Header(default=None),
        x_llm_provider: str | None = Header(default=None),
        x_llm_model: str | None = Header(default=None),
        x_llm_api_base: str | None = Header(default=None),
        x_llm_max_tokens: str | None = Header(default=None),
    ) -> JSONResponse:
        """Phase 2b (job 1 of 2): generate the mapping SKELETON for human review —
        which source becomes which class, keyed by which column(s) — WITHOUT any
        property or prose. Returns a job_id immediately; the SSE done payload carries
        ``{skeleton, inspection_md, metadata}``. The human confirms/edits the
        skeleton, then re-attaches the source and POSTs it to /api/propose/continue.
        The API key is used only for this run and never persisted (D7)."""
        if not files:
            raise HTTPException(400, "no files uploaded")
        max_tokens = _llm_max_tokens(x_llm_max_tokens)
        dialect_overrides = _parse_dialect_overrides(dialects)

        import tempfile as _tempfile

        tmpdir = _tempfile.mkdtemp(prefix="asterism-skeleton-")
        paths: list[Path] = []
        for upload in files:
            if upload.filename is None:
                raise HTTPException(400, "missing filename")
            dest = Path(tmpdir) / _sanitize_tabular_name(upload.filename)
            await _save_upload(upload, dest)
            paths.append(dest)

        provider, model, api_base, key = _llm_coords(
            x_api_key, x_llm_provider, x_llm_model, x_llm_api_base, cfg.registry_root
        )
        llm = _resolve_llm(provider, model, api_base, key, max_tokens=max_tokens)
        fk_cols = fk or None

        async def skeleton_job(
            emit: Callable[..., None], should_cancel: Callable[[], bool]
        ) -> dict[str, object]:
            loop = asyncio.get_running_loop()

            def on_generation(current: int, total: int) -> None:
                loop.call_soon_threadsafe(
                    lambda: emit(phase="llm", message=f"モデル生成中 (パート {current}/{total})")
                )

            def on_note(note: str) -> None:
                loop.call_soon_threadsafe(lambda: emit(phase="llm", message=note))

            _arm_llm_callbacks(
                llm, should_cancel=should_cancel, on_generation=on_generation, on_note=on_note
            )
            emit(phase="skeleton", message="骨格を生成中")
            try:
                result = await asyncio.to_thread(
                    propose_skeleton,
                    list(paths),
                    domain,
                    llm=llm,
                    language=language or None,
                    fk_hint_columns=fk_cols,
                    dialects=dialect_overrides,
                    iri_base=cfg.iri_base,
                )
                _record_llm_usage(cfg.registry_root, "propose", provider, llm, model)
                # Deterministic evidence for the human gate (LLM-free): key
                # uniqueness / collisions / real ID previews / fix candidates,
                # computed against the SAME dialect-read sources. Best-effort —
                # a failure here must never cost the (paid) skeleton itself.
                try:
                    annotations = await asyncio.to_thread(
                        annotate_skeleton,
                        result.skeleton,
                        list(paths),
                        dialects=dialect_overrides,
                    )
                except Exception as exc:  # pragma: no cover — defensive
                    logger.warning("skeleton annotation failed: %s", exc)
                    annotations = None
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)
            return {
                "skeleton": result.skeleton,
                "inspection_md": result.csv_inspection_md,
                "metadata": result.metadata,
                "source_files": [p.name for p in paths],
                "annotations": annotations,
            }

        jobs: JobManager = app.state.jobs
        job_id = jobs.start_coro(skeleton_job)
        return JSONResponse({"job_id": job_id}, status_code=202)

    @app.post("/api/propose/skeleton/validate")
    async def validate_skeleton_endpoint(
        files: list[UploadFile] = File(
            ..., description="The same source file(s) the skeleton was generated from"
        ),
        skeleton: str = Form(..., description="The (possibly edited) skeleton as a JSON object"),
        dialects: str = Form(
            default="",
            description="Per-source read-dialect overrides as JSON (ADR source-dialect.md).",
        ),
    ) -> dict[str, object]:
        """Deterministic gate evidence for an EDITED skeleton — no LLM, no job.

        The skeleton gate calls this after a human edits a subject key or class,
        so a typo'd column or a key that collapses rows is caught in
        milliseconds, not after the (minutes-long, paid) continue run. Same
        computation the initial skeleton response ships in ``annotations``;
        stateless like /api/propose/continue (the source rides the request)."""
        if not files:
            raise HTTPException(400, "no files uploaded")
        try:
            skeleton_obj = json.loads(skeleton)
        except json.JSONDecodeError as exc:
            raise HTTPException(400, f"skeleton is not valid JSON: {exc}") from exc
        if not isinstance(skeleton_obj, dict):
            raise HTTPException(400, "skeleton must be a JSON object")
        dialect_overrides = _parse_dialect_overrides(dialects)

        tmpdir = tempfile.mkdtemp(prefix="asterism-skelcheck-")
        try:
            paths: list[Path] = []
            for upload in files:
                if upload.filename is None:
                    raise HTTPException(400, "missing filename")
                dest = Path(tmpdir) / _sanitize_tabular_name(upload.filename)
                await _save_upload(upload, dest)
                paths.append(dest)
            annotations = await asyncio.to_thread(
                annotate_skeleton, skeleton_obj, paths, dialects=dialect_overrides
            )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        return {"annotations": annotations}

    @app.post("/api/propose/continue")
    async def propose_continue_endpoint(
        files: list[UploadFile] = File(
            ..., description="Source file(s) — re-attach the same source the skeleton used"
        ),
        skeleton: str = Form(..., description="The confirmed skeleton IR as a JSON object"),
        domain: str = Form(default="", description="Domain hint (Markdown). Optional."),
        language: str = Form(default=""),
        dialects: str = Form(
            default="",
            description="Per-source read-dialect overrides as JSON (ADR source-dialect.md).",
        ),
        fk: list[str] = Query(default=[], description="FK hint column (repeatable)"),
        autocorrect: int | None = Query(
            default=None,
            description="Self-correction rounds; absent → server default; 0 = no autocorrect.",
        ),
        x_api_key: str | None = Header(default=None),
        x_llm_provider: str | None = Header(default=None),
        x_llm_model: str | None = Header(default=None),
        x_llm_api_base: str | None = Header(default=None),
        x_llm_max_tokens: str | None = Header(default=None),
    ) -> JSONResponse:
        """Phase 2b (job 2 of 2): from the confirmed skeleton + the re-attached source,
        generate each map's property table, the §1-8 document, splice §9 in
        deterministically, then run the SAME self-correction loop. The done payload is
        identical to /api/propose — materialize and everything downstream is unchanged."""
        if not files:
            raise HTTPException(400, "no files uploaded")
        try:
            skeleton_obj = json.loads(skeleton)
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, f"skeleton is not valid JSON: {exc}") from exc
        if not isinstance(skeleton_obj, dict):
            raise HTTPException(400, "skeleton must be a JSON object")
        max_tokens = _llm_max_tokens(x_llm_max_tokens)
        dialect_overrides = _parse_dialect_overrides(dialects)

        import tempfile as _tempfile

        tmpdir = _tempfile.mkdtemp(prefix="asterism-continue-")
        paths: list[Path] = []
        for upload in files:
            if upload.filename is None:
                raise HTTPException(400, "missing filename")
            dest = Path(tmpdir) / _sanitize_tabular_name(upload.filename)
            await _save_upload(upload, dest)
            paths.append(dest)

        provider, model, api_base, key = _llm_coords(
            x_api_key, x_llm_provider, x_llm_model, x_llm_api_base, cfg.registry_root
        )
        llm = _resolve_llm(provider, model, api_base, key, max_tokens=max_tokens)
        fk_cols = fk or None
        rounds = cfg.autocorrect_rounds if autocorrect is None else max(0, autocorrect)

        async def continue_job(
            emit: Callable[..., None], should_cancel: Callable[[], bool]
        ) -> dict[str, object]:
            loop = asyncio.get_running_loop()

            def on_progress(data: dict[str, object]) -> None:
                loop.call_soon_threadsafe(lambda: emit(**data))

            def on_llm_call(feature: str) -> None:
                _record_llm_usage(cfg.registry_root, feature, provider, llm, model)

            def on_generation(current: int, total: int) -> None:
                loop.call_soon_threadsafe(
                    lambda: emit(phase="llm", message=f"モデル生成中 (パート {current}/{total})")
                )

            def on_note(note: str) -> None:
                loop.call_soon_threadsafe(lambda: emit(phase="llm", message=note))

            _arm_llm_callbacks(
                llm, should_cancel=should_cancel, on_generation=on_generation, on_note=on_note
            )

            try:
                result = await asyncio.to_thread(
                    design_loop.run_design_loop,
                    list(paths),
                    domain,
                    Path(tmpdir),
                    fk_hint_columns=fk_cols,
                    llm=llm,
                    max_rounds=rounds,
                    on_progress=on_progress,
                    on_llm_call=on_llm_call,
                    language=language or None,
                    should_cancel=should_cancel,
                    skeleton=skeleton_obj,
                    dialect_overrides=dialect_overrides,
                    iri_base=cfg.iri_base,
                )
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)
            return {
                "proposal_md": result.proposal_md,
                "inspection_md": result.csv_inspection_md,
                "metadata": result.metadata,
                "source_files": [p.name for p in paths],
                "autocorrect": {
                    "enabled": rounds > 0,
                    "converged": result.converged,
                    "terminal_reason": result.terminal_reason,
                    "initial_issue_count": result.initial_issue_count,
                    "final_issue_count": len(result.remaining_issues),
                    "rounds": [
                        {"n": r.n, "issue_count": r.issue_count, "categories": r.categories}
                        for r in result.rounds
                    ],
                    "remaining_issues": result.remaining_issues,
                    "tabular_only": result.tabular_only,
                    "coverage_dropped": result.coverage_dropped,
                },
            }

        jobs = app.state.jobs
        job_id = jobs.start_coro(continue_job)
        return JSONResponse({"job_id": job_id}, status_code=202)

    @app.post("/api/refine")
    async def refine(
        body: RefineRequest,
        x_api_key: str | None = Header(default=None),
        x_llm_provider: str | None = Header(default=None),
        x_llm_model: str | None = Header(default=None),
        x_llm_api_base: str | None = Header(default=None),
        x_llm_max_tokens: str | None = Header(
            default=None,
            description="Output-token cap override (positive integer); absent → provider default",
        ),
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

        provider, model, api_base, key = _llm_coords(
            x_api_key, x_llm_provider, x_llm_model, x_llm_api_base, cfg.registry_root
        )
        llm = _resolve_llm(
            provider, model, api_base, key, max_tokens=_llm_max_tokens(x_llm_max_tokens)
        )

        def work(should_cancel: Callable[[], bool]) -> dict[str, object]:
            # Cooperative cancel only (jobs.start has no emit to bridge progress
            # through): the client checks it before each generation.
            _arm_llm_callbacks(llm, should_cancel=should_cancel)
            result = refine_schema(body.schema_md, comments, llm=llm, language=body.language)
            _record_llm_usage(cfg.registry_root, "refine", provider, llm, model)
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

    @app.get("/api/usage")
    async def usage_get(
        since: str | None = Query(default=None, description="ISO-8601 lower bound on ts"),
        until: str | None = Query(default=None, description="ISO-8601 upper bound on ts"),
    ) -> JSONResponse:
        """The LLM-usage ledger: raw events + monthly rollups (token counts only).

        Read-only — the UI joins these with its user-editable per-model rate table
        to compute cost at display time, so cost lives in the browser, not here."""
        events = await asyncio.to_thread(
            usage_ledger.read_usage, cfg.registry_root, since=since, until=until
        )
        monthly = usage_ledger.summarize_monthly(events)
        return JSONResponse({"events": events, "monthly": monthly})

    @app.post("/api/usage", dependencies=_write_auth)
    async def usage_post(body: UsageEventBody) -> JSONResponse:
        """Append one usage event (write-gated). The receiver for the demo-agent's
        agentic Ask, which runs out-of-process and POSTs its accumulated tokens so
        all LLM spend lands in one ledger."""
        event = await asyncio.to_thread(
            usage_ledger.record_usage,
            cfg.registry_root,
            body.feature,
            body.provider,
            body.model_id,
            input_tokens=body.input_tokens,
            output_tokens=body.output_tokens,
            cache_read_tokens=body.cache_read_tokens,
            cache_write_tokens=body.cache_write_tokens,
        )
        return JSONResponse({"recorded": event})

    @app.post("/api/materialize", dependencies=_write_auth)
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
                # Re-pin source dialects on a redesign (`dataset_id` set): a refine
                # round / hand edit can drop the §9 `dialects:` section, and the
                # compiled RML would silently lose its annotations. The dataset's
                # persisted source dir lets materialize_schema re-detect and
                # overlay deterministically (explicit spec values still win).
                src_dir = (
                    registry.source_dir(cfg.registry_root, body.dataset_id)
                    if body.dataset_id
                    else None
                )
                mat = materialize_schema(
                    body.proposal_md,
                    tmpdir,
                    body.dataset_name,
                    write=True,
                    source_dir=src_dir if src_dir is not None and src_dir.is_dir() else None,
                )
                paths = {k: Path(v) for k, v in mat.written_paths.items()}
                report = validate_schema(
                    SchemaBundle(
                        diagram_md=paths.get("mermaid") or paths.get("diagram"),
                        mie_yaml=paths.get("mie_yaml") or paths.get("mie"),
                        ingester_py=paths.get("ingester_py") or paths.get("ingester"),
                        # Pass the RML so trap T9 (closed-set) actually runs and
                        # surfaces a non-Tier-0 function to the reviewer at design
                        # time. The hard gate is at ingest (substrate.assert_rml_safe);
                        # this makes the violation visible before persistence.
                        rml_ttl=paths.get("rml_ttl"),
                        # Pass the §9 mapping spec so T4's fix recipe can derive
                        # keyword candidates from the design's own map/class/column
                        # names — present even when the spec failed to compile to
                        # RML (the very case where the one-click AI fix runs).
                        mapping_ir_yaml=paths.get("mapping_ir"),
                    )
                )
                artifacts = {
                    "diagram.md": mat.mermaid,
                    "model.yaml": mat.rdf_config_model,
                    "mie.yaml": mat.mie_yaml,
                    "ingester.py": mat.ingester_py,
                    # Phase 5: the declarative RML mapping — compiled from the §9
                    # mapping spec on new proposals, or the raw legacy block on
                    # older ones (may be None — persisted so the human-gated
                    # ingest can run it).
                    "mapping.rml.ttl": mat.rml_ttl,
                    # The reviewed §9 mapping spec itself (ADR mapping-ir-compiler):
                    # persisted for re-edit/re-compile; absent on legacy proposals.
                    "mapping.yaml": mat.mapping_ir_yaml,
                }
                traps = [
                    {
                        "id": r.trap_id,
                        "name": r.name,
                        "status": r.status,
                        "detail": r.detail,
                        # Deterministic repair recipe (where + what + paste-ready
                        # example) — the UI's one-click AI fix forwards it so weak
                        # models stop looping on symptom-only descriptions.
                        "fix": r.fix,
                    }
                    for r in report.results
                ]
                exit_code = report.exit_code()
                # Advisory design validation AT MATERIALIZE: run the SAME check the
                # ingest gate runs (validate_rml_design — column references + Tier 0
                # function parameters against the REAL source CSVs), so a typo'd
                # column or a wrong/missing function parameter surfaces here, at the
                # review/save step where the one-click "ask AI to fix" lives, not only
                # later at ingest. It is advisory: materialize still saves the design;
                # the issues are returned so the user fixes them before ingest. The
                # hard 422 ingest gate (below in /ingest) is unchanged.
                #
                # The check needs the dataset's persisted source CSVs. A brand-new
                # design has none yet (the workbench attaches source AFTER materialize),
                # so validation runs only when source is available — a redesign /
                # re-materialize in place (`dataset_id` set), whose registry already
                # holds the source from the prior round. With no readable source the
                # field is simply absent (no false issues); the ingest gate still
                # catches it once a source is attached.
                validation_issues = _validate_design_at_materialize(
                    cfg.registry_root,
                    body.dataset_id,
                    artifacts.get("mapping.rml.ttl"),
                )
                # Mapping-spec parse/compile problems are the same class of
                # advisory, readable design issue — surface them first (when the
                # spec does not compile there IS no RML for the check above).
                validation_issues = [*mat.mapping_ir_issues, *validation_issues]
                result: dict[str, object] = {
                    "artifacts": artifacts,
                    "complete": mat.complete,
                    "warnings": mat.warnings,
                    "traps": traps,
                    "exit_code": exit_code,
                    # Advisory list (one readable message per design issue), empty when
                    # the design is clean OR no source was available to check against.
                    "validation_issues": validation_issues,
                }
                # Persist so the bundle appears in the Gallery (authoring→catalog).
                if body.persist:
                    if body.dataset_id:
                        # Redesign: re-materialize the SAME dataset in place (keep its
                        # id / graphs / lifecycle / source). Re-design changes only the
                        # mapping; the user re-applies data via the re-ingest controls.
                        meta = registry.update_dataset_artifacts(
                            cfg.registry_root,
                            body.dataset_id,
                            artifacts,
                            complete=mat.complete,
                            warnings=mat.warnings,
                            traps=traps,
                            exit_code=exit_code,
                            proposal_md=body.proposal_md,
                        )
                        if meta is None:
                            raise HTTPException(404, f"dataset {body.dataset_id!r} not found")
                    else:
                        meta = registry.save_dataset(
                            cfg.registry_root,
                            body.dataset_name,
                            artifacts,
                            complete=mat.complete,
                            warnings=mat.warnings,
                            traps=traps,
                            exit_code=exit_code,
                            created_at=datetime.now(UTC).isoformat(),
                            proposal_md=body.proposal_md,
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

    @app.get("/api/datasets/{dataset_id}/validate-design")
    async def validate_dataset_design(dataset_id: str) -> dict[str, object]:
        """Advisory design validation against the dataset's persisted source (read-only).

        Same check as the materialize response (``validate_rml_design`` — missing
        source files, column references, Tier 0 function parameters against the REAL
        persisted CSVs), but callable AFTER the source is attached. This closes the
        brand-new-design gap: at materialize a fresh design has no persisted source
        yet (the workbench attaches it right after), so the inline advisory returns
        nothing; the workbench calls this once the attach lands to surface the same
        issues before ingest — where the one-click "ask AI to fix" lives. Never
        raises on a bad design (returns its ``issues``); 404 only when the dataset is
        absent. ``validation_issues`` is ``[]`` when the design is clean OR nothing
        could be checked (no RML, no readable source)."""
        data = registry.load_dataset(cfg.registry_root, dataset_id)
        if data is None:
            raise HTTPException(404, f"dataset {dataset_id!r} not found")
        rml_ttl = (data.get("artifacts") or {}).get("mapping.rml.ttl")
        issues = await asyncio.to_thread(
            _validate_design_at_materialize, cfg.registry_root, dataset_id, rml_ttl
        )
        return {"dataset_id": dataset_id, "validation_issues": issues}

    @app.get("/api/datasets/{dataset_id}/proposal")
    async def get_dataset_proposal(dataset_id: str) -> dict[str, object]:
        """Return a dataset's stored design (propose/refine Markdown) for re-design.

        The "見直す" (redesign) flow reopens this in the workbench so the user can
        refine/edit it and re-materialize the SAME dataset. Read-only. 404 when the
        dataset is absent; ``proposal_md`` is empty (and ``has_proposal`` false) for
        datasets materialized before the design was persisted — the UI then steers
        the user to recreate rather than re-open."""
        data = registry.load_dataset(cfg.registry_root, dataset_id)
        if data is None:
            raise HTTPException(404, f"dataset {dataset_id!r} not found")
        proposal_md = registry.load_proposal(cfg.registry_root, dataset_id) or ""
        return {
            "dataset_id": dataset_id,
            "dataset_name": data["meta"].get("name", dataset_id),
            "proposal_md": proposal_md,
            "has_proposal": bool(proposal_md.strip()),
        }

    @app.get("/api/datasets/{dataset_id}/rules")
    async def get_dataset_rules(dataset_id: str) -> dict[str, object]:
        """A human-readable projection of the dataset's ingest rules (read-only).

        Deterministic and LLM-free: parses the persisted ``mapping.rml.ttl``
        (:func:`asterism.rml_summary.summarize_rml`) into "this column → this
        property (via this function)" rows the catalog renders, and enriches
        term IRIs with the ``model.yaml`` labels (the same rdf-config projection
        promote uses). This closes the transparency gap: the rules that produce
        the citable facts are themselves reviewable, not a black box. Datasets
        without RML return ``maps: []`` (the UI keeps its empty state); an
        unparseable mapping returns a ``warnings`` entry rather than a 500.
        """
        data = registry.load_dataset(cfg.registry_root, dataset_id)
        if data is None:
            raise HTTPException(404, f"dataset {dataset_id!r} not found")
        artifacts = data.get("artifacts") or {}
        rml_ttl = str(artifacts.get("mapping.rml.ttl") or "")
        model_yaml = str(artifacts.get("model.yaml") or "")
        mie_yaml = str(artifacts.get("mie.yaml") or "")

        def run() -> dict[str, object]:
            import rdflib

            summary = summarize_rml(rml_ttl)
            labels: dict[str, str] = {}
            if model_yaml.strip():
                prefixes = STANDARD_PREFIXES | extract_prefixes(rml_ttl, mie_yaml)
                projected = project_model_yaml(model_yaml, prefixes)
                for subj, obj in projected.subject_objects(rdflib.RDFS.label):
                    labels[str(subj)] = str(obj)
            return {"dataset_id": dataset_id, **summary, "labels": labels}

        return await asyncio.to_thread(run)

    @app.get("/api/datasets/{dataset_id}/history")
    async def get_dataset_history(dataset_id: str) -> dict[str, object]:
        """Redesign snapshots (newest first) — metadata only, contents by id below."""
        data = registry.load_dataset(cfg.registry_root, dataset_id)
        if data is None:
            raise HTTPException(404, f"dataset {dataset_id!r} not found")
        snapshots = registry.list_dataset_history(cfg.registry_root, dataset_id)
        return {"dataset_id": dataset_id, "count": len(snapshots), "snapshots": snapshots}

    @app.get("/api/datasets/{dataset_id}/history/{snapshot_id}")
    async def get_dataset_history_snapshot(
        dataset_id: str, snapshot_id: str
    ) -> dict[str, object]:
        """One redesign snapshot's stored artifacts + unified diffs vs the CURRENT set.

        The diff answers the reviewer's actual question — "what did this redesign
        change?" — without shipping a diff engine to the browser. Direction is
        snapshot → current (the snapshot is the ``---`` side). Unchanged files are
        omitted from ``diffs``; files that exist on only one side diff against
        empty.
        """
        data = registry.load_dataset(cfg.registry_root, dataset_id)
        if data is None:
            raise HTTPException(404, f"dataset {dataset_id!r} not found")
        snapshot = registry.load_dataset_history(cfg.registry_root, dataset_id, snapshot_id)
        if snapshot is None:
            raise HTTPException(404, f"snapshot {snapshot_id!r} not found")

        current: dict[str, str] = {
            name: str(text or "") for name, text in (data.get("artifacts") or {}).items()
        }
        proposal_md = registry.load_proposal(cfg.registry_root, dataset_id)
        if proposal_md is not None:
            current["proposal.md"] = proposal_md

        def run() -> dict[str, str]:
            diffs: dict[str, str] = {}
            old_files: dict[str, str] = snapshot["artifacts"]
            for name in sorted(set(old_files) | set(current)):
                old = old_files.get(name, "")
                new = current.get(name, "")
                if old == new:
                    continue
                diff = "\n".join(
                    difflib.unified_diff(
                        old.splitlines(),
                        new.splitlines(),
                        fromfile=f"{name} ({snapshot_id})",
                        tofile=f"{name} (current)",
                        lineterm="",
                    )
                )
                if diff:
                    diffs[name] = diff
            return diffs

        diffs = await asyncio.to_thread(run)
        return {"dataset_id": dataset_id, "snapshot": snapshot, "diffs": diffs}

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

    @app.post("/api/datasets/{dataset_id}/tools", dependencies=_write_auth)
    async def save_dataset_tool(dataset_id: str, body: QueryToolBody) -> dict[str, object]:
        """Add/replace one query tool on a dataset (upsert by name).

        The submitted tool is validated with ``parse_query_tools`` (read-only
        SELECT/ASK + safe ``{{placeholder}}`` binding) AND ``lint_query_tool``
        (rendered-template parse with the store's own parser, undeclared-prefix
        and filter-only-variable checks) before it is persisted — a tool that
        would fail at execution time is 400 with the actionable reason, never
        saved. Saving IS the human-vet gate: a person deliberately submits a
        tool they have reviewed (same trust model as the Tier 0 function
        library; nothing is generated at runtime). Lint *warnings* do not block
        the save; they are returned for the reviewer.

        Dry run (best-effort, advisory): a tool that parses and lints clean can
        still be a 0-row tool — a pattern stricter than the data (observed
        live: required triples not every row carries, or a version-lag between
        the template and the live graph). When every required parameter has a
        default, the saved template is executed once with default arguments
        against the canonical scope; 0 rows adds a warning and the row count is
        returned as ``dry_run``. Never blocks the save (store down / no
        canonical data yet → ``dry_run: null``)."""
        if registry.load_dataset(cfg.registry_root, dataset_id) is None:
            raise HTTPException(404, f"dataset {dataset_id!r} not found")
        tool = body.model_dump()
        try:
            parsed = parse_query_tools({"tools": [tool]})
        except QueryToolError as exc:
            raise HTTPException(400, f"invalid query tool: {exc}") from exc
        lint = lint_query_tool(parsed[0])
        if lint.errors:
            raise HTTPException(400, "invalid query tool: " + "; ".join(lint.errors))
        registry.save_query_tool(cfg.registry_root, dataset_id, tool)
        warnings = list(lint.warnings)
        dry_run: dict[str, object] | None = None
        client = getattr(app.state, "client", None)
        runnable = all(not p.required or p.default is not None for p in parsed[0].params)
        if client is not None and runnable:
            try:
                out = await run_query_tool(client, parsed[0], {}, max_rows=1)
                dry_run = {"rows": out["count"], "truncated": out["truncated"]}
                if out["count"] == 0:
                    warnings.append(
                        "dry run with default arguments returned 0 rows against the "
                        "current canonical data — the pattern may be stricter than the "
                        "data (required triples not every row carries; consider "
                        "OPTIONAL), a term may not match, or the dataset is not "
                        "promoted/re-ingested yet. Saved anyway; verify on the live "
                        "graph."
                    )
            except Exception:  # advisory only — a dry-run failure never blocks a save
                dry_run = None
        return {
            "dataset_id": dataset_id,
            "saved": parsed[0].name,
            "warnings": warnings,
            "dry_run": dry_run,
            "tools": registry.list_query_tools(cfg.registry_root, dataset_id),
        }

    @app.delete("/api/datasets/{dataset_id}/tools/{tool_name}", dependencies=_write_auth)
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

    @app.post("/api/datasets/{dataset_id}/tools/propose", dependencies=_write_auth)
    async def propose_dataset_tool(
        dataset_id: str,
        body: ToolProposeBody,
        x_api_key: str | None = Header(default=None),
        x_llm_provider: str | None = Header(default=None),
        x_llm_model: str | None = Header(default=None),
        x_llm_api_base: str | None = Header(default=None),
        x_llm_max_tokens: str | None = Header(
            default=None,
            description="Output-token cap override (positive integer); absent → provider default",
        ),
    ) -> dict[str, object]:
        """P2: AI-draft ONE query tool from a natural-language intent.

        The LLM (user-brought key, never stored) drafts a parameterized read-only
        SPARQL tool grounded in this dataset's vocabulary, then SELF-CORRECTS
        against deterministic validation (parse + lint with the store's parser +
        the RML-mapped closed vocabulary; ``asterism_api.tool_loop``) — up to 3
        rounds, targeted defects + a closed-menu oracle fed back each time.
        ``body.autocorrect=false`` limits it to a single shot (vet still runs).
        The best draft is RETURNED FOR HUMAN REVIEW with its ``valid`` flag,
        remaining ``warnings`` and per-round ``rounds`` record — it is NOT
        saved; the person reviews/edits it and saves via ``POST .../tools``
        (the human-vet gate). The API key is required (LLM call)."""
        data = registry.load_dataset(cfg.registry_root, dataset_id)
        if data is None:
            raise HTTPException(404, f"dataset {dataset_id!r} not found")
        if not body.intent.strip():
            raise HTTPException(400, "intent is required")
        arts = data["artifacts"]
        provider, model, api_base, key = _llm_coords(
            x_api_key, x_llm_provider, x_llm_model, x_llm_api_base, cfg.registry_root
        )
        if not key:
            raise HTTPException(
                400,
                "AI draft needs an API key — set one in Settings, or have the "
                "operator configure a server-side key (ASTERISM_LLM_KEY_<PROVIDER>)",
            )
        llm = _resolve_llm(
            provider, model, api_base, key, max_tokens=_llm_max_tokens(x_llm_max_tokens)
        )

        def run() -> ToolLoopResult:
            return propose_tool_with_correction(
                llm,
                intent=body.intent,
                model_yaml=arts.get("model.yaml", "") or "",
                mie_yaml=arts.get("mie.yaml", "") or "",
                # The RML is the ground truth for the dataset's real namespaces +
                # predicate/class IRIs — the vocabulary oracle and the closed-set
                # vet both derive from it.
                rml_ttl=arts.get("mapping.rml.ttl", "") or "",
                language=body.language,
                max_rounds=3 if body.autocorrect else 1,
            )

        try:
            res = await asyncio.to_thread(run)
        except Exception as exc:  # LLM failure with no draft -> 502 with the reason
            raise HTTPException(502, f"AI draft failed: {exc}") from exc
        await asyncio.to_thread(
            _record_llm_usage, cfg.registry_root, "tool.propose", provider, llm, model
        )
        warnings = list(res.warnings)
        dry_run: dict[str, object] | None = None
        client = getattr(app.state, "client", None)
        if res.valid and client is not None:
            # Same advisory dry run as the save endpoint: tell the reviewer NOW
            # if the (clean) draft returns 0 rows with default arguments.
            try:
                draft_tool = parse_query_tools({"tools": [res.draft]})[0]
                if all(not p.required or p.default is not None for p in draft_tool.params):
                    out = await run_query_tool(client, draft_tool, {}, max_rows=1)
                    dry_run = {"rows": out["count"], "truncated": out["truncated"]}
                    if out["count"] == 0:
                        warnings.append(
                            "dry run with default arguments returned 0 rows against "
                            "the current canonical data — the pattern may be stricter "
                            "than the data, a term may not match, or the dataset is "
                            "not promoted/re-ingested yet."
                        )
            except Exception:
                dry_run = None
        return {
            "dataset_id": dataset_id,
            "draft": res.draft,
            "valid": res.valid,
            "error": res.error,
            "warnings": warnings,
            "dry_run": dry_run,
            "rounds": res.rounds,
        }

    @app.post("/api/datasets/{dataset_id}/tools/{tool_name}/run")
    async def run_dataset_tool(
        dataset_id: str, tool_name: str, body: ToolRunBody
    ) -> dict[str, object]:
        """Run ONE saved query tool deterministically — typed, read-only, key-free.

        This is the verified-tool *execution* path (product_direction: 決定論・型付
        きを主役, no LLM). The template was vetted by a human at save time; here we
        only bind the caller's typed arguments safely (type-checked + escaped, never
        string-concatenated) and run the result over the canonical FROM-merge — the
        same deterministic path the MCP surface exposes. Needs no API key. Allowed
        even in a typed-only exposure profile: it is NOT the raw-SPARQL escape, it is
        the typed path that profile is meant to keep (so it stays unauthenticated —
        no graph mutation, no arbitrary SPARQL). Returns
        ``{tool, count, items, truncated, sparql}``."""
        if registry.load_dataset(cfg.registry_root, dataset_id) is None:
            raise HTTPException(404, f"dataset {dataset_id!r} not found")
        match = next(
            (t for t in registry.list_query_tools(cfg.registry_root, dataset_id)
             if t.get("name") == tool_name),
            None,
        )
        if match is None:
            raise HTTPException(404, f"tool {tool_name!r} not found")
        try:
            tool = parse_query_tools({"tools": [match]})[0]
        except QueryToolError as exc:  # a saved tool should already be valid
            raise HTTPException(400, f"invalid query tool: {exc}") from exc
        client: OxigraphClient = app.state.client
        try:
            return await run_query_tool(client, tool, dict(body.args or {}))
        except QueryToolError as exc:
            # A caller-actionable problem: bad/missing/typed-wrong argument, or
            # a broken saved template (run_query_tool translates the store's
            # parse failure into the lint detail). The message already names
            # the tool and the cause.
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:  # a real store/transport failure stays a 5xx
            raise HTTPException(502, f"tool run failed: {exc}") from exc

    @app.post("/api/datasets/{dataset_id}/source", dependencies=_write_auth)
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

    @app.post("/api/documents", dependencies=_write_auth)
    async def create_document_dataset(
        name: str = Form("document"),
        files: list[UploadFile] = File(
            ..., description="One or more JATS .xml, Word .docx, or .pdf documents"
        ),
    ) -> JSONResponse:
        """Create a DOCUMENT dataset from one or MORE uploaded JATS/Word/PDF files.

        Unlike CSV/JSON — which go through the LLM design → materialize flow — a
        structured document needs no schema. This creates the registry record,
        persists the source(s) (a ``.docx`` is converted to JATS by pandoc; a ``.pdf`` is
        persisted RAW and converted by the Docling sidecar at *ingest* — the slow ML step
        lives in the async ingest job; both set ``source_kind=xml``), and auto-attaches
        the reusable document recall tools (``search_text`` / ``quote_with_citation`` /
        ``fetch_passage``). Multiple documents land in ONE dataset (the accumulating
        "定例ミーティング" model — ingest structures every source). Ingest + promote remain
        explicit human gates.
        """
        uploads = [f for f in files if f.filename]
        if not uploads:
            raise HTTPException(400, "no document uploaded")
        # A document accepts ANY filename (sanitized in _persist_source_uploads); only
        # the extension must be a document kind. CSV/JSON keep strict name validation.
        for f in uploads:
            if Path(f.filename).suffix.lower() not in _DOCUMENT_SOURCE_SUFFIXES:
                raise HTTPException(
                    400, "a document must be a JATS .xml, a Word .docx, or a .pdf file"
                )
        meta = registry.save_dataset(
            cfg.registry_root,
            name or "document",
            {"diagram.md": "classDiagram\n  class Document"},
            complete=True,
            warnings=[],
            traps=[],
            exit_code=0,
            created_at=datetime.now(UTC).isoformat(),
        )
        dataset_id = meta["id"]
        # Roll back the just-created (still empty) record unless creation FULLY
        # succeeds — not only on HTTPException. A client disconnect mid-upload
        # (starlette ClientDisconnect) or an OSError (disk full, permission) raises a
        # non-HTTPException that the old `except HTTPException` let escape, leaving a
        # source-less orphan dataset for a re-upload to duplicate. A `finally` guard
        # also covers cancellation; the delete is best-effort so it never masks the
        # original error.
        created_ok = False
        try:
            saved, meta = await _persist_source_uploads(cfg.registry_root, dataset_id, uploads)
            for tool in _document_tool_specs():
                registry.save_query_tool(cfg.registry_root, dataset_id, tool)
            created_ok = True
            return JSONResponse(
                {"dataset_id": dataset_id, "source_files": saved, "dataset": meta},
                status_code=201,
            )
        finally:
            if not created_ok:
                with contextlib.suppress(Exception):
                    registry.delete_dataset(cfg.registry_root, dataset_id)

    @app.post("/api/datasets/{dataset_id}/documents", dependencies=_write_auth)
    async def append_document(
        dataset_id: str,
        file: UploadFile = File(..., description="A JATS .xml, .docx, or .pdf document to add"),
    ) -> JSONResponse:
        """Add another document to an existing, promoted document dataset (incremental).

        The document analogue of ``POST /api/datasets/{id}/append``: structure just
        this document and POST-merge it into the live graph, so a dataset grows
        document by document (e.g. a running "定例ミーティング" of meeting minutes) and
        ``search_text`` / ``quote_with_citation`` span every document added. Synchronous —
        a JATS/Word document structures in milliseconds; a ``.pdf`` blocks for the Docling
        sidecar conversion (one document; full async append is a follow-up).
        """
        doc_name = str(file.filename or "document")
        try:
            result = await _append_document_to_dataset(
                cfg.registry_root, app.state.client, dataset_id, file, docling_url=cfg.docling_url
            )
        except AppendError as exc:
            _log_job(
                cfg,
                {
                    "kind": "append",
                    "dataset_id": dataset_id,
                    "file": doc_name,
                    "status": "error",
                    "error": exc.detail,
                    "ended_at": datetime.now(UTC).isoformat(),
                },
            )
            raise HTTPException(exc.status, exc.detail) from exc
        _log_job(
            cfg,
            {
                "kind": "append",
                "dataset_id": dataset_id,
                "file": doc_name,
                "status": "ok",
                "triples_in_batch": result["triples_in_batch"],
                "append_seq": result["append_seq"],
                "ended_at": datetime.now(UTC).isoformat(),
            },
        )
        return JSONResponse(result, status_code=200)

    @app.post("/api/datasets/{dataset_id}/ingest", dependencies=_write_auth)
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
        # A document (JATS/XML) dataset takes the DOCUMENT path: a closed, vetted
        # deterministic structurer (asterism.documents) — NO RML, NO Morph-KGC, NO
        # generated code (CLAUDE.md「生成コードを実行しない」). A CSV/JSON dataset
        # takes the declarative RML path.
        is_document = str((data.get("meta") or {}).get("source_kind") or "csv") == "xml"

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
                "投入には CSV / JSON / XML のソースファイルが必要です。"
                "設計時に添付するか、ここでアップロードしてください",
            )
        source_dir = source_paths[0].parent

        rml_ttl = ""
        # Each item: (xml_text, paper_iri, conversion|None). A document dataset can hold
        # MORE THAN ONE document (a "定例ミーティング" of accumulated minutes); ingest
        # structures every .xml source so a snapshot re-ingest reproduces the whole feed
        # from the source set (A7), staying consistent with incremental document append.
        docs_to_structure: list[tuple[str, str, dict | None]] = []
        pdfs_to_convert: list[Path] = []
        if is_document:
            xml_paths = [p for p in source_paths if p.suffix.lower() == ".xml"]
            pdfs_to_convert = [p for p in source_paths if p.suffix.lower() == ".pdf"]
            if not xml_paths and not pdfs_to_convert:
                raise HTTPException(400, "document ingest needs a .xml (JATS) or .pdf source")
            # A .pdf needs the Docling sidecar; fail fast with a clear 422 (before the
            # job) when it is not configured — same graceful degrade as absent pandoc.
            if pdfs_to_convert and not cfg.docling_url:
                raise HTTPException(
                    422,
                    "PDF ingestion requires the Docling sidecar, which is not configured. "
                    "Set ASTERISM_DOCLING_URL to its URL, or convert the PDF to JATS/Word first.",
                )
            meta_conv = (data.get("meta") or {}).get("conversion") or None
            for p in xml_paths:
                txt = p.read_text(encoding="utf-8")
                doc_id = documents.derive_doc_id(txt, fallback=p.stem)
                piri = f"{_DOCUMENT_RESOURCE_BASE}/{dataset_id}/{doc_id}"
                # Per-doc conversion from the sidecar; fall back to the meta hint only
                # when there is a single document (preserves the original behaviour).
                conv = _doc_conversion_for(p) or (meta_conv if len(xml_paths) == 1 else None)
                docs_to_structure.append((txt, piri, conv))
        else:
            rml_ttl = str(data["artifacts"].get("mapping.rml.ttl", "") or "")
            if not rml_ttl.strip():
                raise HTTPException(
                    400, "this dataset has no declarative RML mapping to ingest"
                )
            # Trust boundary (CLAUDE.md「生成コードを実行しない」): refuse a mapping that
            # would execute non-Tier-0 code or read outside this dataset's source dir.
            # Fail-closed and synchronous, so a malicious RML is rejected with a clear
            # 422 before any background job runs (the substrate re-checks before
            # Morph-KGC as defense in depth).
            try:
                substrate.assert_rml_safe(rml_ttl, source_dir)
            except substrate.RmlSafetyError as exc:
                raise HTTPException(422, f"unsafe RML mapping: {exc}") from exc
            # Design validation (also synchronous, before any job): catch a column
            # reference to a non-existent column or a wrong/missing Tier 0 function
            # parameter, returning a structured 422 whose `issues` list the UI renders
            # as a readable bullet list — instead of letting it surface as an opaque
            # Morph-KGC crash deep inside the background job. Validate the run-id-
            # substituted form so the runtime-only `{__run_id__}` placeholder (not a
            # CSV column) is never flagged. The substrate re-validates the prepared RML
            # before Morph-KGC as defense in depth.
            try:
                substrate.validate_rml_design(
                    substrate.substitute_run_id(rml_ttl), source_dir
                )
            except substrate.RmlValidationError as exc:
                raise HTTPException(
                    422,
                    detail={
                        "error": "RML design validation failed",
                        "issues": exc.issues,
                    },
                ) from exc
        # part5: stream into a FRESH per-ingest version graph `canonical/{id}/v{n}`
        # — never touching the currently live graph. So a re-ingest needs no
        # un-publish and no DROP on the request path (the old version stays citable
        # until promote swaps the live pointer; it is dropped in the background
        # afterwards). The version graph stays out of the Ask scope until promote
        # points the dataset's liveGraph at it (draft isolation, flag-based).
        #
        # `reserve_data_seq` (not `next_data_seq`) *persists* the number now, so every
        # attempt — even one whose predecessor was killed / cancelled before its
        # cleanup ran — gets a fresh, empty version graph. A retry therefore never
        # streams into a partial left by a prior attempt (no stale-row / duplicate-
        # activity merge); the abandoned version is reclaimed by the failure-path drop
        # below or by startup reconciliation.
        dataset_key = substrate.canonical_graph_iri(dataset_id)
        data_seq = registry.reserve_data_seq(cfg.registry_root, dataset_id)
        staged_iri = substrate.versioned_graph_iri(dataset_id, data_seq)
        client: OxigraphClient = app.state.client

        dataset_name = str((data.get("meta") or {}).get("name") or dataset_id)
        source_names = ", ".join(p.name for p in source_paths)

        # Cooperative cancel (the start_coro contract): the job polls
        # ``should_cancel`` at its phase boundaries — per document, per PDF,
        # inside the Morph-KGC subprocess poll loop, and at every upload chunk.
        # A pending cancel (user POST /api/jobs/{id}/cancel, or the job timeout,
        # whose expiry sets the same event) raises IngestCancelledError — a plain
        # Exception, so a cancel mid-upload reuses the failure path's
        # chunked_drop_graph and no partial version graph outlives the job.
        async def ingest_job(
            emit: Callable[..., None], should_cancel: Callable[[], bool]
        ) -> dict[str, object]:
            started_at = datetime.now(UTC).isoformat()

            def check_cancel() -> None:
                if should_cancel():
                    raise substrate.IngestCancelledError("ingest cancelled")

            def log_outcome(status: str, **extra: object) -> None:
                # One activity-ledger line (GET /jobs) per outcome, so Workbench
                # ingests show up alongside watcher/append activity (audit ⑦).
                _log_job(
                    cfg,
                    {
                        "kind": "ingest",
                        "dataset_id": dataset_id,
                        "dataset_name": dataset_name,
                        "file": source_names,
                        "status": status,
                        "started_at": started_at,
                        "ended_at": datetime.now(UTC).isoformat(),
                        **extra,
                    },
                )

            async def run_pipeline() -> dict[str, object]:
                work = Path(tempfile.mkdtemp(prefix="asterism-ingest-"))
                try:
                    # ``dataset_id`` rides on the first frame so a resumed
                    # subscriber can verify its saved job id still refers to THIS
                    # dataset (job ids restart at job-1 when the api restarts).
                    emit(phase="materialize", message="RDF を生成中", dataset_id=dataset_id)
                    check_cancel()
                    nt_paths: list[Path] = []
                    if is_document:
                        # Document path: the vetted deterministic structurer writes each
                        # document's doco/nif graph as N-Triples (no morph-kgc). Blocking →
                        # off-loop. One sub-dir per doc so the .nt files do not collide.
                        for i, (txt, piri, conv) in enumerate(docs_to_structure):
                            check_cancel()
                            sub = work / f"doc_{i}"
                            sub.mkdir()
                            nt_paths.append(
                                await asyncio.to_thread(
                                    documents.document_to_nt_file,
                                    txt,
                                    paper_iri=piri,
                                    work_dir=str(sub),
                                    conversion=conv,
                                )
                            )
                        # PDF sources: the slow ML conversion (Docling sidecar) lives HERE,
                        # inside the async job, so the request returned 202 immediately and the
                        # UI follows SSE progress (ADR pdf-docling-conversion.md). Each PDF is
                        # converted to JATS, structured identically, and its conversion is
                        # disclosed (lit:DocumentConversionActivity) + recorded for A7 re-ingest.
                        # One Docling HTTP call is not interruptible; the cancel
                        # boundary is per PDF (same as propose's in-flight call).
                        for j, pdf_path in enumerate(pdfs_to_convert):
                            check_cancel()
                            emit(phase="converting", message=f"PDF を変換中 ({pdf_path.name})")
                            pdf_bytes = await asyncio.to_thread(pdf_path.read_bytes)
                            jats, converter = await asyncio.to_thread(
                                documents.convert_pdf_to_jats,
                                pdf_bytes,
                                sidecar_url=cfg.docling_url,
                            )
                            conv = {
                                "converter": converter,
                                "sourceFormat": "pdf",
                                "original": pdf_path.name,
                            }
                            await asyncio.to_thread(
                                (pdf_path.parent / f"{pdf_path.name}.conversion").write_text,
                                json.dumps(conv, ensure_ascii=False),
                                "utf-8",
                            )
                            doc_id = documents.derive_doc_id(jats, fallback=pdf_path.stem)
                            piri = f"{_DOCUMENT_RESOURCE_BASE}/{dataset_id}/{doc_id}"
                            sub = work / f"pdf_{j}"
                            sub.mkdir()
                            nt_paths.append(
                                await asyncio.to_thread(
                                    documents.document_to_nt_file,
                                    jats,
                                    paper_iri=piri,
                                    work_dir=str(sub),
                                    conversion=conv,
                                )
                            )
                    else:
                        # Morph-KGC writes N-Triples to a file (memory-bounded); the
                        # subprocess CLI is blocking, so run it off the event loop.
                        # ``should_cancel`` reaches the subprocess poll loop, so a
                        # cancel interrupts even a minutes-long materialization.
                        nt_paths.append(
                            await asyncio.to_thread(
                                substrate.materialize_to_nt_file,
                                rml_ttl,
                                source_dir,
                                work_dir=work,
                                should_cancel=should_cancel,
                            )
                        )
                    total = sum(substrate.count_nt_lines(p) for p in nt_paths)
                    emit(phase="materialized", total=total)
                    # The target is a fresh, empty version graph — no clean-slate DROP
                    # needed (and the live graph is untouched, so Ask keeps serving the
                    # current version throughout the re-stream).
                    emit(phase="preparing", message="取り込み先グラフを準備中")
                    try:
                        triple_count = 0
                        for nt in nt_paths:
                            base = triple_count
                            triple_count += await substrate.stream_nt_file_to_oxigraph(
                                nt,
                                client,
                                staged_iri,
                                on_progress=lambda done, tot, base=base: emit(
                                    phase="upload", done=base + done, total=total
                                ),
                                should_cancel=should_cancel,
                            )
                        # Final gate: a cancel that lands after the last chunk must
                        # still drop the (complete but unwanted) version graph and
                        # skip the staged-pointer + mark_ingested commit below.
                        check_cancel()
                    except (Exception, asyncio.CancelledError):
                        # D6: never leave a partial version graph behind on failure (it
                        # was never live, so reclaiming it cannot affect a reader). Use a
                        # chunked delete — a partial can be large, and a single DROP of a
                        # multi-million-triple graph OOMs Oxigraph. CancelledError (the
                        # job-timeout path injects it at an await point) is included so
                        # a timed-out upload is reclaimed NOW, not at the next restart.
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

            try:
                result = await run_pipeline()
            except substrate.IngestCancelledError:
                log_outcome("cancelled")
                raise
            except asyncio.CancelledError:
                # The job timeout (or a shutdown) — record it, then re-raise
                # (asyncio requires cancellation to propagate).
                log_outcome("error", error="job cancelled (timeout or shutdown)")
                raise
            except Exception as exc:
                log_outcome("error", error=str(exc) or type(exc).__name__)
                raise
            log_outcome(
                "ok",
                triples=result["triple_count"],
                graph_iri=staged_iri,
                data_seq=data_seq,
            )
            return result

        jobs: JobManager = app.state.jobs
        job_id = jobs.start_coro(ingest_job)
        return JSONResponse({"job_id": job_id}, status_code=202)

    @app.post("/api/datasets/{dataset_id}/append", dependencies=_write_auth)
    async def append_dataset(
        dataset_id: str,
        files: list[UploadFile] = File(
            ...,
            description="New batch source file(s) (CSV or JSON) to append to the live "
            "feed. Each name must match an rml:source in the mapping.",
        ),
    ) -> JSONResponse:
        """Incremental append (ADR incremental-ingest.md): grow a promoted dataset's
        live canonical graph with a new batch — the device-feed path.

        Materializes ONLY this batch (O(new rows)) and POST-merges it into the
        dataset's already-live canonical graph, so the new triples are immediately
        citable while existing triples/IRIs are untouched (re-emitted rows dedupe by
        their deterministic IRIs). No new version graph, no pointer swap, no DROP —
        unlike snapshot ``ingest`` which re-materializes the whole source set.

        Preconditions (4xx): the dataset exists, has an RML mapping, is *promoted* (a
        live graph to grow) and active (not retracted/deleted). A batch file is
        required (append always carries the new rows) and each must match an
        ``rml:source`` name, else it would silently materialize 0 triples. The batch is
        also accumulated into the dataset's source set so a later snapshot re-ingest
        reproduces the whole feed (A7).

        Trust model unchanged: same Morph-KGC + Tier 0 substrate (no generated code);
        the append is a Graph Store POST (the ingest write path), not a SPARQL UPDATE,
        so ``/api/sparql`` stays read-only. Append is idempotent — safe to retry. The
        same logic runs unattended via the per-dataset append watcher (§6).
        """
        uploaded = [f for f in files if f.filename]
        if not uploaded:
            raise HTTPException(400, "append requires at least one batch source file")
        batch = [(str(f.filename), await f.read()) for f in uploaded]
        batch_names = ", ".join(name for name, _ in batch)
        # Same activity-ledger record the append watcher writes — a manual append
        # from the catalog is the same operation, so it must not be invisible in
        # the activity view while the unattended path is recorded.
        try:
            result = await _append_batch_to_dataset(
                cfg.registry_root,
                app.state.client,
                dataset_id,
                batch,
                rebuilder=getattr(app.state, "crosswalk_rebuilder", None),
            )
        except AppendError as exc:
            _log_job(
                cfg,
                {
                    "kind": "append",
                    "dataset_id": dataset_id,
                    "file": batch_names,
                    "status": "error",
                    "error": exc.detail,
                    "ended_at": datetime.now(UTC).isoformat(),
                },
            )
            raise HTTPException(exc.status, exc.detail) from exc
        _log_job(
            cfg,
            {
                "kind": "append",
                "dataset_id": dataset_id,
                "file": batch_names,
                "status": "ok",
                "triples_in_batch": result["triples_in_batch"],
                "append_seq": result["append_seq"],
                "ended_at": datetime.now(UTC).isoformat(),
            },
        )
        return JSONResponse(result)

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

    @app.post("/api/datasets/{dataset_id}/promote", dependencies=_write_auth)
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
        # crosswalk-hub.md ②: if this dataset participates in the crosswalk, rebuild
        # the hub now (inline best-effort) so its newly-citable values are joined.
        await _maybe_rebuild_crosswalk(client, cfg.registry_root, dataset_id)
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

    @app.post("/api/datasets/{dataset_id}/retract", dependencies=_write_auth)
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

    @app.post("/api/datasets/{dataset_id}/reinstate", dependencies=_write_auth)
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

    @app.post("/api/datasets/{dataset_id}/rename", dependencies=_write_auth)
    async def rename_dataset_endpoint(dataset_id: str, body: RenameRequest) -> JSONResponse:
        """Change a dataset's DISPLAY name. The ``id`` is the IRI seed (data identity) and
        is immutable, so this updates only the human label — graphs, IRIs and existing
        citations are untouched."""
        if registry.load_dataset(cfg.registry_root, dataset_id) is None:
            raise HTTPException(404, f"dataset {dataset_id!r} not found")
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "name must not be empty")
        if len(name) > 200:
            raise HTTPException(400, "name is too long (max 200 chars)")
        meta = registry.rename_dataset(cfg.registry_root, dataset_id, name)
        return JSONResponse({"dataset_id": dataset_id, "dataset": meta})

    @app.delete("/api/datasets/{dataset_id}", dependencies=_write_auth)
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
        # part5: also reclaim EVERY version graph of this dataset — a re-ingest before
        # promotion leaves superseded `…/v{n}` versions that neither the live nor the
        # staged pointer names, so the two adds above would miss them. Enumerated by the
        # graph-name index (cheap), so delete stays complete without waiting for a
        # restart's reconciliation pass.
        to_drop.update(await substrate.all_version_graphs(client, dataset_id=dataset_id))
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

    # ----------------------------------------------------------------------
    # Crosswalk hub (crosswalk-hub.md productize ①④) — author / build / view
    # ----------------------------------------------------------------------

    def _validated_perspective_id(perspective_id: str) -> str:
        try:
            crosswalk_runtime.crosswalk_graph_iri(perspective_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return perspective_id

    def _crosswalk_view(perspective_id: str) -> dict:
        config = crosswalk_runtime.load_config(cfg.registry_root, perspective_id)
        data = registry.load_dataset(
            cfg.registry_root, crosswalk_runtime.crosswalk_registry_id(perspective_id)
        )
        return {
            "perspective_id": perspective_id,
            "exists": config is not None,
            "config": crosswalk_runtime.config_to_dict(config) if config else None,
            "dataset": data["meta"] if data else None,
        }

    async def _do_crosswalk_build(
        perspective_id: str, body: CrosswalkBuildBody
    ) -> JSONResponse:
        """Build (or rebuild) ONE perspective. ``config`` in the body (the authoring
        flow) is validated + persisted, then built; omit it to rebuild from the
        persisted config. Each perspective is its own graph; the FROM-merge unions
        them. Building the human-declared mapping IS the vet gate."""
        client: OxigraphClient = app.state.client
        if body.config is not None:
            try:
                config = crosswalk_runtime.parse_config(body.config)
            except ValueError as exc:
                raise HTTPException(400, f"invalid crosswalk config: {exc}") from exc
            crosswalk_runtime.save_config(cfg.registry_root, config, perspective_id)
        else:
            config = crosswalk_runtime.load_config(cfg.registry_root, perspective_id)
            if config is None:
                raise HTTPException(
                    400,
                    "no crosswalk config yet — POST a config (datasets + the "
                    "concept-bearing predicate of each) to create this perspective",
                )
        try:
            outcome = await crosswalk_runtime.build_hub(
                client,
                config,
                built_at=datetime.now(UTC).isoformat(),
                perspective_id=perspective_id,
            )
        except Exception as exc:  # surface a build error to the UI
            raise HTTPException(502, f"crosswalk build failed: {exc}") from exc
        meta = crosswalk_runtime.write_registry_scaffold(
            cfg.registry_root, config, outcome, perspective_id=perspective_id, name=body.name or ""
        )
        return JSONResponse(
            {
                "perspective_id": perspective_id,
                "dataset_id": meta["id"],
                "hub_graph": outcome.hub_graph,
                "built_at": outcome.built_at,
                "triple_count": outcome.triple_count,
                "shared": outcome.shared,
                "shared_total": outcome.shared_total,
                "links": outcome.links,
                "participants_used": outcome.participants_used,
                "participants_skipped": outcome.participants_skipped,
                "dataset": meta,
            }
        )

    @app.get("/api/crosswalks")
    async def crosswalks_list() -> JSONResponse:
        """List every crosswalk PERSPECTIVE (id, name, stats, config) — the upper
        ontology is plural (multi-perspective ADR)."""
        out = []
        for meta in crosswalk_runtime.list_perspectives(cfg.registry_root):
            pid = (
                meta.get("crosswalk_perspective_id")
                or crosswalk_runtime.DEFAULT_PERSPECTIVE_ID
            )
            config = crosswalk_runtime.load_config(cfg.registry_root, pid)
            out.append(
                {
                    "perspective_id": pid,
                    "config": crosswalk_runtime.config_to_dict(config) if config else None,
                    "dataset": meta,
                }
            )
        return JSONResponse({"perspectives": out})

    @app.get("/api/crosswalk")
    async def crosswalk_get() -> JSONResponse:
        """The default (composition) perspective's config + stats (back-compat).
        ``exists:false`` when it has not been built yet."""
        return JSONResponse(_crosswalk_view(crosswalk_runtime.DEFAULT_PERSPECTIVE_ID))

    @app.post("/api/crosswalk/build", dependencies=_write_auth)
    async def crosswalk_build(body: CrosswalkBuildBody) -> JSONResponse:
        """Build (or rebuild) the default (composition) perspective (back-compat)."""
        return await _do_crosswalk_build(crosswalk_runtime.DEFAULT_PERSPECTIVE_ID, body)

    @app.post("/api/crosswalk/propose", dependencies=_write_auth)
    async def crosswalk_propose(
        body: CrosswalkProposeBody,
        x_api_key: str | None = Header(default=None),
        x_llm_provider: str | None = Header(default=None),
        x_llm_model: str | None = Header(default=None),
        x_llm_api_base: str | None = Header(default=None),
        x_llm_max_tokens: str | None = Header(
            default=None,
            description="Output-token cap override (positive integer); absent → provider default",
        ),
    ) -> dict[str, object]:
        """AI-assist (手動選択の補助): suggest each dataset's concept-bearing predicate.

        Samples each selected dataset's literal-valued predicates from the store and
        asks the LLM (user-brought key, never stored) which one carries the concept.
        Returns a DRAFT (per-dataset predicate + why) for the human to confirm/edit in
        the authoring UI — nothing is built here (the human review is the vet gate)."""
        if not body.dataset_ids:
            raise HTTPException(400, "dataset_ids is required")
        provider, model, api_base, api_key_val = _llm_coords(
            x_api_key, x_llm_provider, x_llm_model, x_llm_api_base, cfg.registry_root
        )
        if not api_key_val:
            raise HTTPException(
                400,
                "AI suggestion needs an API key — set one in Settings, or have the "
                "operator configure a server-side key (ASTERISM_LLM_KEY_<PROVIDER>)",
            )
        llm = _resolve_llm(
            provider, model, api_base, api_key_val,
            max_tokens=_llm_max_tokens(x_llm_max_tokens),
        )
        client: OxigraphClient = app.state.client
        datasets: list[dict] = []
        skipped: list[dict] = []
        for dsid in body.dataset_ids:
            data = registry.load_dataset(cfg.registry_root, dsid)
            if data is None:
                skipped.append({"dataset_id": dsid, "reason": "not found"})
                continue
            meta = data["meta"]
            if not meta.get("promoted"):
                skipped.append({"dataset_id": dsid, "reason": "not promoted (no live data)"})
                continue
            key = substrate.canonical_graph_iri(dsid)
            live = await substrate.live_graph_of(client, key) or key
            datasets.append(
                {
                    "dataset_id": dsid,
                    "label": meta.get("name") or dsid,
                    "predicates": await _literal_predicates(client, live),
                }
            )
        if not datasets:
            raise HTTPException(400, "none of dataset_ids is a promoted, sampleable dataset")

        def run() -> list[dict]:
            return propose_crosswalk_mapping(
                llm, concept=body.concept, datasets=datasets, language=body.language
            )

        try:
            participants = await asyncio.to_thread(run)
        except Exception as exc:  # LLM/parse failure -> 502 with the reason
            raise HTTPException(502, f"AI suggestion failed: {exc}") from exc
        await asyncio.to_thread(
            _record_llm_usage, cfg.registry_root, "crosswalk.propose", provider, llm, model
        )
        return {
            "concept": body.concept,
            "participants": participants,
            "candidates": datasets,
            "skipped": skipped,
        }

    @app.get("/api/crosswalk/alignments")
    async def crosswalk_alignments() -> JSONResponse:
        """The asserted schema alignments BETWEEN perspectives (Phase 2) + the closed
        set of relations a human may assert. Read-only."""
        client: OxigraphClient = app.state.client
        return JSONResponse(
            {
                "alignments": await crosswalk_runtime.list_alignments(client),
                "relations": sorted(crosswalk_runtime.ALIGN_RELATIONS),
            }
        )

    @app.get("/api/vocabularies")
    async def grounding_vocabularies() -> JSONResponse:
        """The curated KNOWN external vocabularies (CMSO / QUDT / schema.org / PROV …)
        Asterism recognizes + can ground to (external-standard-alignment.md §8). The SoT
        for both recognition and grounding. Read-only."""
        return JSONResponse({"vocabularies": [v.to_dict() for v in grounding.vocabularies()]})

    @app.get("/api/ground")
    async def grounding_search(
        q: str = Query(description="class / predicate name or label to ground"),
        kind: str | None = Query(default=None, description='"class" | "property"'),
        domain: str | None = Query(default=None, description='e.g. "materials"'),
        limit: int = Query(default=8, ge=1, le=50),
    ) -> JSONResponse:
        """Candidate REAL external-standard terms for ``q``, best first — so a human (or
        AI-assisted propose) can REUSE / ALIGN to a standard instead of re-minting a
        private term. Closed-set + deterministic: every candidate is a real IRI from the
        curated catalog (never fabricated); the human still confirms the pick. Read-only."""
        try:
            cands = grounding.ground_terms(q, kind=kind, domain=domain, limit=limit)
        except ValueError as exc:  # bad kind
            raise HTTPException(400, str(exc)) from exc
        return JSONResponse({"query": q, "candidates": [c.to_dict() for c in cands]})

    @app.post("/api/ground/schema")
    async def grounding_for_schema(body: GroundSchemaBody) -> JSONResponse:
        """External-standard candidates for the MINTED class/predicate of a PROPOSED schema
        (the rdf-config model.yaml) — so AI-assisted design surfaces "your data could lean
        on cmso:/qudt:/…" (external-standard-alignment.md §8). Pass the propose markdown
        (its model.yaml block is extracted) or model.yaml directly. Deterministic +
        closed-set: candidates come only from the curated catalog, never from the LLM.
        Reused (known-namespace) terms are skipped. Read-only."""
        model_yaml = body.model_yaml
        if not model_yaml and body.proposal_md:
            model_yaml = (
                _pick_block(
                    extract_code_blocks(body.proposal_md),
                    header_keywords=_MODEL_HEADERS,
                    language_prefs=("yaml", "yml"),
                    allow_lang_only=False,
                )
                or ""
            )
        terms = grounding.ground_model_yaml(model_yaml) if model_yaml.strip() else []
        return JSONResponse({"terms": [t.to_dict() for t in terms]})

    @app.post("/api/crosswalk/align", dependencies=_write_auth)
    async def crosswalk_align(body: CrosswalkAlignBody) -> JSONResponse:
        """Assert (or, with ``remove``, withdraw) a schema relationship between two
        perspective terms — "視点をつなぐ". Additive, reversible, human-gated; stored in a
        promoted alignment graph the FROM-merge unions (a citable, declared fact)."""
        client: OxigraphClient = app.state.client
        try:
            if body.remove:
                await crosswalk_runtime.remove_alignment(
                    client, body.source, body.target, body.relation
                )
                return JSONResponse(
                    {
                        "removed": True,
                        "source": body.source,
                        "target": body.target,
                        "relation": body.relation,
                    }
                )
            res = await crosswalk_runtime.assert_alignment(
                client,
                body.source,
                body.target,
                body.relation,
                at=datetime.now(UTC).isoformat(),
                from_perspective=body.from_perspective,
                to_perspective=body.to_perspective,
            )
            return JSONResponse(res)
        except ValueError as exc:  # bad relation / non-IRI term
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:  # surface a store error
            raise HTTPException(502, f"alignment failed: {exc}") from exc

    @app.get("/api/crosswalk/normalizer/primitives")
    async def normalizer_primitives() -> JSONResponse:
        """The CLOSED set of recipe primitive ids a human may compose into a normalizer
        (crosswalk-normalizer-recipes.md). Read-only; the UI supplies the labels."""
        return JSONResponse({"primitives": sorted(crosswalk.RECIPE_PRIMITIVES)})

    @app.post("/api/crosswalk/normalizer/preview")
    async def normalizer_preview(body: NormalizerPreviewBody) -> JSONResponse:
        """Apply a declarative recipe to sample values (the join keys it would produce),
        so a human can vet a normalizer before authoring it. Pure compute, no store."""
        try:
            results = [
                {"input": s, "output": crosswalk.apply_recipe(body.recipe, s)}
                for s in body.samples
            ]
        except ValueError as exc:  # unknown primitive (closed-set gate)
            raise HTTPException(400, str(exc)) from exc
        return JSONResponse({"recipe": body.recipe, "results": results})

    # Parameterized perspective routes are declared AFTER the literal ones
    # (/crosswalk/build, /crosswalk/propose, /crosswalk/align[ments], /crosswalk/
    # normalizer/*) so those never bind ``perspective_id``.
    @app.get("/api/crosswalk/{perspective_id}")
    async def crosswalk_get_one(perspective_id: str) -> JSONResponse:
        """One perspective's config + stats (multi-perspective ADR)."""
        return JSONResponse(_crosswalk_view(_validated_perspective_id(perspective_id)))

    @app.post("/api/crosswalk/{perspective_id}/build", dependencies=_write_auth)
    async def crosswalk_build_one(
        perspective_id: str, body: CrosswalkBuildBody
    ) -> JSONResponse:
        """Build (or rebuild) a NAMED perspective — author a new lens or refresh one.
        Each perspective is its own crosswalk graph; the FROM-merge unions them."""
        return await _do_crosswalk_build(_validated_perspective_id(perspective_id), body)

    @app.post("/api/sparql", dependencies=_write_auth)
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
        if not cfg.expose_raw_sparql:
            # Exposure profile = typed-only: the raw SPARQL escape is withheld.
            raise HTTPException(
                403,
                "この配備では生 SPARQL は無効です (型付きツールのみ公開). "
                "ASTERISM_EXPOSE_RAW_SPARQL=1 で有効化できます",
            )
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
        except ValueError as exc:
            # A rejected query (SERVICE federation, FROM outside the canonical
            # allowlist, GRAPH before any promote). The message is operator-safe.
            raise HTTPException(400, str(exc)) from exc
        try:
            return JSONResponse(await client.sparql_select(effective))
        except Exception as exc:
            # Do NOT echo the raw exception: it embeds the internal Oxigraph URL /
            # connection details (info disclosure). Log server-side, return generic.
            logger.exception("sparql relay error")
            raise HTTPException(502, "upstream SPARQL error") from exc

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

    @app.post("/api/jobs/{job_id}/cancel")
    async def job_cancel(job_id: str) -> JSONResponse:
        """Cancel one background job (idempotent — cancelling a finished job is OK).

        Unauthenticated by the same reasoning as the stream route above: job ids
        are per-process handles whose stream (the full result) is already open,
        so cancel adds no new exposure. The SSE stream ends with a ``cancelled``
        event; the job's cooperative ``should_cancel`` stops the LLM work at its
        next checkpoint and its late result is discarded."""
        jobs: JobManager = app.state.jobs
        if not jobs.cancel(job_id):
            raise HTTPException(404, "unknown job_id")
        return JSONResponse({"status": "cancelled"})

    return app


# ----------------------------------------------------------------------------
# CLI / uvicorn entry point
# ----------------------------------------------------------------------------


# Bind loopback by default: a bare `asterism-api` run is reachable only from the
# host unless the operator explicitly opts into a wider bind with --host. The
# container image passes --host 0.0.0.0 (Docker forwards a loopback-bound host
# port to it), so containerized deployments are unaffected.
_DEFAULT_HOST: Final[str] = "127.0.0.1"
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
    # Private-by-default at-rest: every durable artifact this process (and its
    # in-process watcher) creates — registry source CSVs, meta.json, materialized
    # bundles, *.ttl, jobs.jsonl — is made 0600 / dirs 0700, so a shared host or a
    # bind-mounted data volume does not expose unpublished research data.
    os.umask(0o077)
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
