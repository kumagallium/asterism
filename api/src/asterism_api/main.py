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
from collections.abc import AsyncIterator, Callable, Collection, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal

import httpx
import yaml
from asterism import (
    crosswalk,
    crosswalk_discover,
    crosswalk_runtime,
    documents,
    grounding,
    shapes,
    substrate,
)
from asterism.datasets import datasets_root, load_dataset
from asterism.exposure import raw_sparql_enabled
from asterism.ontology_projection import (
    STANDARD_PREFIXES,
    extract_prefixes,
    project_mapping_ir,
    project_model_yaml,
)
from asterism.oxigraph_client import OxigraphClient, OxigraphConfig
from asterism.query_tools import (
    QueryToolError,
    lint_query_tool,
    parse_query_tools,
    run_query_tool,
    synthesize_query_tools_from_trial_queries,
    write_registry_query_tools,
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
from asterism_step0.llm import (
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_OPENAI_MODEL,
    as_completion,
    list_available_models,
)
from asterism_step0.llm import make_llm as build_llm_client
from asterism_step0.materialize import (
    _MODEL_HEADERS,
    _pick_block,
    extract_code_blocks,
    materialize_schema,
)
from asterism_step0.propose import LLMClient
from asterism_step0.refine import refine_schema
from asterism_step0.skeleton_annotate import annotate_skeleton, apply_key_safety_fix
from asterism_step0.staged_propose import (
    COLUMN_DECISION_ACTIONS,
    apply_column_decisions_to_document,
    apply_column_meanings_to_document,
    apply_display_meta_to_document,
    propose_column_meanings,
    propose_skeleton,
    remove_stale_column_includes_from_document,
)
from asterism_step0.validate import SchemaBundle, validate_schema
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi import Path as PathParam
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from asterism_api import (
    appdata,
    design_loop,
    exchange,
    registry,
    server_keys,
    staging,
    togomcp_sync,
)
from asterism_api import describe as describe_mod
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
    # Where the design's source lives, when there is one. Two jobs, both of them
    # backstops for the MANUAL round:
    #   * the persisted source is what lets the refined document go through the
    #     SAME validation + deterministic self-correction round 0 runs (a weak
    #     model asked for one wording change can drop a `datatype:` and turn
    #     numbers into strings);
    #   * and it is where the closed-menu oracle (exact filenames, real columns,
    #     Tier-0 menu) comes from — without it the human's "AI に直してもらう"
    #     rounds ran on the symptom text alone (live 2026-08-18: the model
    #     invented 17 camel-cased column names over five clicks).
    # The dataset's own persisted source wins; the staged copy (ADR
    # source-staging.md) covers a design not attached yet. Both optional: absent
    # → refine behaves exactly as before, one LLM call, no menu and no backstop.
    dataset_id: str | None = None
    staging_id: str | None = None


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
    # Design-time source that is NOT yet attached (ADR source-staging.md): the
    # wizard stages uploads once and designs against them; attach happens only
    # after materialize succeeds. Until then the dataset has no source dir, so
    # every source-grounded check here (dialect re-pin, column existence with
    # did-you-mean, numeric typing, join-key candidates) used to be silently
    # skipped on a brand-new design — the manual "AI に直してもらう" rounds ran
    # blind and a design with 17 invented column names was reported "clean"
    # (live 2026-08-18). With the staging id the same checks run against the
    # staged copy. The dataset's own persisted source still wins when present.
    staging_id: str | None = None


class SparqlRequest(BaseModel):
    """Body for POST /api/sparql: a read-only SPARQL query (escape hatch)."""

    query: str


class StagingSourcesBody(BaseModel):
    """Body for POST /api/staging/{id}/sources: the tables to actually use.

    One Excel workbook becomes one table per sheet (K6), and the human is the
    only one who knows which sheets hold data and which hold a chart. Names are
    the canonical (slugged) source names the record already holds; anything else
    is ignored, so a stale client can never widen the selection to a file the
    record does not have.
    """

    sources: list[str] = []


class DisplayMetaEdit(BaseModel):
    """One human correction to a column's MEANING or UNIT (kantan S6, K8).

    Display metadata only: it changes what the reviewer reads, never a value,
    never a triple. ``predicate`` identifies the row (expanded IRI or the CURIE
    the design wrote); ``map`` / ``column`` narrow it when one predicate is bound
    by more than one map. An omitted field is left alone; an empty string clears
    it (the human saying "this was wrong and I have nothing better").
    """

    predicate: str
    map: str | None = None
    source: str | None = None
    column: str | None = None
    label: str | None = None
    unit: str | None = None


class DisplayMetaBody(BaseModel):
    """Body for POST /api/datasets/{id}/display-meta: a batch of S6 corrections."""

    edits: list[DisplayMetaEdit] = []


class ColumnDecision(BaseModel):
    """A human decision about one source column.

    ``include`` / ``exclude`` answer "is this column mapped at all" (a column the
    AI proposal left out). ``own`` answers a different question — "several kinds
    record this column; which one KEEPS it" — the ownership tie the rows could
    not break (ADR column-ownership-and-growth G1). Both live in one durable
    store because both are statements about the same physical source column, and
    the latest one wins.
    """

    source: str
    column: str
    action: Literal["include", "exclude", "own"]
    map: str | None = None
    map_class: str | None = None
    label: str | None = None
    unit: str | None = None
    datatype: str | None = None


class ColumnMeaning(BaseModel):
    """What ONE source column MEANS — the input layer of a dataset's display metadata.

    ADR ``meaning-before-identity.md`` §6. Keyed by ``(source, column)``: that is
    the only identity a column has before a design exists, and the meaning of a
    column is decided by the data, not by the design later built on it. The
    predicate-keyed ``display-meta.json`` store stays as the PROJECTION of this
    onto a design that has been built (so the detail tier and post-redesign
    reconciliation keep working unchanged).
    """

    source: str
    column: str
    label: str | None = None
    unit: str | None = None


class ColumnMeaningsBody(BaseModel):
    """Body for POST /api/datasets/{id}/column-meanings."""

    meanings: list[ColumnMeaning] = []


class ColumnDecisionsBody(BaseModel):
    """Body for POST /api/datasets/{id}/column-decisions."""

    decisions: list[ColumnDecision] = []
    # The wizard settles a duplicated column at S5 — BEFORE the attach step has
    # persisted the source. The staged copy is the design-time source there
    # (ADR source-staging.md), exactly as it is for /api/materialize.
    staging_id: str | None = None


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


class ConsultFocusColumn(BaseModel):
    """The column the user's cursor is on right now (D4), if any."""

    name: str = ""
    samples: list[str] = []


class ConsultPendingColumn(BaseModel):
    """One row of S6's "まだ取り込んでいない項目" (droppedColumns) table."""

    name: str = ""
    samples: list[str] = []


class ConsultColumn(BaseModel):
    """One row of S6's "項目の意味" table — a column with a decided meaning/unit
    (blank when not yet filled in). ``samples`` (2026-08-25 extension): real
    values, so a "意味が未入力の項目" question can be answered from the actual
    data, not just the bare column name."""

    name: str = ""
    meaning: str | None = None
    unit: str | None = None
    samples: list[str] = []


class ConsultKind(BaseModel):
    """One row of S4's「データの数えかた」ゲート — a map's key columns and its
    current "1 件が表すもの" (class/kind) name, verbatim from the same data
    SkeletonGate renders."""

    map: str = ""
    source: str = ""
    key_columns: list[str] = []
    kind_name: str | None = None


class ConsultContext(BaseModel):
    """What the drawer is standing in front of (D4) — attached automatically by
    the UI, never typed by the user. Every field optional: the drawer also
    works from a screen with no design context (D2's ``general`` thread).

    ``pending_columns``/``columns`` (2026-08-25 extension) are S6's two column
    tables, verbatim from the same data the screen renders — a real-LLM
    dogfood found the model asking "which columns?" when the person had just
    asked about the ones visibly listed on screen. Input-guarded (not just
    trusted client state) since this rides every consult call: at most 40
    columns, 3 samples each, and every string capped so a pathological client
    payload can't blow up the prompt."""

    step: str | None = None
    dataset: str | None = None
    skeleton_summary: str | None = None
    focus_column: ConsultFocusColumn | None = None
    pending_columns: list[ConsultPendingColumn] = []
    columns: list[ConsultColumn] = []
    # 2026-08-25 D10 extension (B): S4 gate's per-map key columns + kind name.
    kinds: list[ConsultKind] = []


class ConsultMessage(BaseModel):
    role: str
    content: str


class ConsultBody(BaseModel):
    """Body for POST /api/design/consult: a stateless one-shot chat turn (D3).
    ``messages`` is the transcript the CLIENT holds (this endpoint has no
    memory of its own); ``context`` is what the drawer is standing in front of
    (D4)."""

    messages: list[ConsultMessage] = []
    context: ConsultContext | None = None


class CrosswalkDiscoverBody(BaseModel):
    """Body for POST /api/crosswalk/discover: find the crosswalks that COULD exist by
    comparing the promoted datasets' actual values (kantan-mode ADR). No LLM and no API
    key — the join keys come from the closed normalizer set, so this is deterministic
    and repeatable. Every field bounds the scan; the response echoes the bounds and
    discloses whatever they cut off. ``dataset_ids`` empty = scan everything promoted."""

    dataset_ids: list[str] = []
    min_datasets: int = Field(2, ge=2, le=8)
    max_datasets: int = Field(12, ge=2, le=50)
    max_predicates_per_dataset: int = Field(12, ge=1, le=64)
    max_values_per_predicate: int = Field(2000, ge=50, le=20000)
    min_shared_keys: int = Field(2, ge=1, le=1000)
    max_candidates: int = Field(12, ge=1, le=50)


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


# camelCase → words, for the last-resort reading of a term IRI.
_RE_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
# Full-width parens spelled as escapes: a Japanese column heading uses them,
# and the literal characters read as typos next to their ASCII twins.
_RE_TRAILING_PARENS = re.compile(r"[\uff08(][^\uff08()\uff09]*[)\uff09]\s*$")


def _label_from_column(column: str | None) -> str | None:
    """A source column heading, minus the unit its parentheses carried.

    ``Seebeck coeff.(V/K)`` → ``Seebeck coeff.``. The unit is already shown
    separately, and repeating it inside the name reads as a mistake.
    """
    if not column or not column.strip():
        return None
    from asterism_step0.units import extract_unit_from_label

    text = column
    if extract_unit_from_label(column):
        text = _RE_TRAILING_PARENS.sub("", text)
    text = text.strip().strip("_-").strip()
    return text or None


def _unit_echoes_its_term(unit: str, column: str | None, predicate: str | None) -> bool:
    """True when an authored ``unit:`` is just the column (or predicate) name again.

    A weak model that is handed K8's optional ``label:``/``unit:`` pair reliably
    fills BOTH with the same thing, so a text column ends up carrying
    ``unit: Name`` — and the review screen then asks a person to confirm a unit
    for a chemical name. The label side already drops this echo before display;
    the unit side did not, which is the half a person notices ("単位に単位じゃ
    ないものが入っている", 2026-08-19 review).

    Deliberately narrow: only an echo is dropped, never an unrecognised unit. A
    genuine unit is not its own column heading, so nothing real is lost — and a
    unit a person types is kept whatever it says (they know what it means).
    """
    text = (unit or "").strip()
    if not text:
        return False

    def key(value: str | None) -> str:
        return re.sub(r"[\s_\-.]+", "", (value or "")).casefold()

    target = key(text)
    if not target:
        return False
    if target == key(_label_from_column(column) or column):
        return True
    local = (predicate or "").rsplit("#", 1)[-1].rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    return target == key(local)


def _humanize_term_iri(iri: str) -> str | None:
    """Last resort: read a term IRI's local name as words, or None.

    ``…#hasSeebeckCoefficient`` → ``Seebeck Coefficient``. Returns None when the
    reading is identical to the local name (``…#Sample`` → ``Sample``): a label
    that repeats what the caller already shows is noise, and the endpoints
    promise not to manufacture one out of nothing. Display only — it never
    touches stored data, and every earlier source (authored label, model.yaml
    projection, source column) wins over it.
    """
    tail = iri.rsplit("#", 1)[-1].rsplit("/", 1)[-1]
    if not tail:
        return None
    stripped = re.sub(r"^(has|is)(?=[A-Z])", "", tail)
    spaced = _RE_CAMEL_BOUNDARY.sub(" ", stripped.replace("_", " ").replace("-", " "))
    words = " ".join(spaced.split())
    return words if words and words != tail else None


def _ir_predicate_display(mapping_ir_yaml: str) -> dict[str, dict[str, str]]:
    """The Mapping IR's reviewer-facing display metadata per expanded predicate IRI.

    Returns ``{predicate_iri: {"label": …?, "unit": …?}}`` from the reviewed
    ``mapping.yaml`` (the design SSOT — kantan-mode ADR K8). A missing authored
    unit falls back to the bracketed-column-name extraction the materialize
    chokepoint persists (task #10), so an IR saved without it still shows the
    unit. Raises on an unparsable IR — callers pick their own degradation
    (a warning on /rules, silence on /trial-queries).
    """
    from asterism_step0.mapping_ir import BUILTIN_PREFIXES, parse_mapping_ir
    from asterism_step0.units import extract_unit_from_label

    ir = parse_mapping_ir(mapping_ir_yaml)
    prefixes = dict(BUILTIN_PREFIXES) | dict(ir.prefixes)

    def expand(term: str) -> str:
        prefix, sep, rest = term.partition(":")
        if sep and prefix in prefixes:
            return prefixes[prefix] + rest
        return term

    meta: dict[str, dict[str, str]] = {}
    for tm in ir.maps:
        for prop in tm.properties:
            extra: dict[str, str] = {}
            if prop.label:
                extra["label"] = prop.label
            elif prop.column:
                # Deterministic third choice, below the authored label and the
                # model.yaml projection: the source column heading the user
                # actually typed. A weak model that skipped K8's `label:` would
                # otherwise put `hasSeebeckCoefficient` in a question the user is
                # asked to read — a word from their own file always beats one.
                derived_label = _label_from_column(prop.column)
                if derived_label:
                    extra["column_label"] = derived_label
            if prop.unit and not _unit_echoes_its_term(prop.unit, prop.column, prop.predicate):
                extra["unit"] = prop.unit
            elif (
                prop.column
                and not prop.columns
                and prop.object_template is None
                and prop.constant is None
            ):
                derived = extract_unit_from_label(prop.column)
                if derived:
                    extra["unit"] = derived
            if extra:
                meta.setdefault(expand(prop.predicate), {}).update(extra)
    return meta


def _model_yaml_labels(model_yaml: str, rml_ttl: str, mie_yaml: str) -> dict[str, str]:
    """``rdfs:label`` per term IRI from the dataset's model.yaml projection.

    The same deterministic rdf-config projection promote uses — prefixes resolve
    against THIS dataset's RML/MIE declarations unioned with the standard set.
    Synchronous (rdflib) — call via ``asyncio.to_thread`` on request paths.
    """
    import rdflib

    labels: dict[str, str] = {}
    if model_yaml.strip():
        prefixes = STANDARD_PREFIXES | extract_prefixes(rml_ttl, mie_yaml)
        projected = project_model_yaml(model_yaml, prefixes)
        for subj, obj in projected.subject_objects(rdflib.RDFS.label):
            labels[str(subj)] = str(obj)
    return labels


def _merge_ir_display_metadata(
    mapping_ir_yaml: str, summary: dict
) -> dict[str, dict[str, str]]:
    """Attach the Mapping IR's reviewer-facing ``label``/``unit`` to rule rows.

    Matching is by expanded predicate IRI so the compiled RML stays the single
    structural projection (see :func:`_ir_predicate_display`). Best-effort: an
    unparsable IR adds a warning instead of failing the read-only endpoint.
    Returns the metadata it merged, so the caller can also use its fallbacks.
    """
    meta: dict[str, dict[str, str]] = {}
    try:
        meta = _ir_predicate_display(mapping_ir_yaml)
        if not meta:
            return meta
        for entry in summary.get("maps") or []:
            if not isinstance(entry, dict):
                continue
            for row in entry.get("properties") or []:
                extra = meta.get(str(row.get("predicate_iri") or ""))
                if extra:
                    # ``column_label`` is a FALLBACK, resolved in
                    # :func:`_fill_missing_labels` after model.yaml has had its
                    # turn — it must not enter the row as the label here.
                    for key in ("label", "unit"):
                        if key in extra:
                            row.setdefault(key, extra[key])
    except Exception:
        warnings = summary.setdefault("warnings", [])
        if isinstance(warnings, list):
            warnings.append(
                "mapping.yaml (Mapping IR) could not be parsed; "
                "label/unit enrichment was skipped."
            )
        return {}
    return meta


def _fill_missing_labels(
    summary: dict, labels: dict[str, str], ir_meta: dict[str, dict[str, str]]
) -> None:
    """Give every rule row a readable name, deterministically.

    The order is fixed and never involves a model: ① the IR's authored ``label``
    (K8), ② the ``model.yaml`` ``rdfs:label`` projection, ③ the source column
    heading the row reads, ④ the term IRI's local name read as words. Before
    this, a design whose weak model skipped ① and ② left the reader looking at
    ``hasSeebeckCoefficient`` — while the server was holding the very column
    heading that person typed. Display only; the stored data is untouched.
    """
    for entry in summary.get("maps") or []:
        if not isinstance(entry, dict):
            continue
        for row in entry.get("properties") or []:
            if not isinstance(row, dict) or row.get("label"):
                continue
            iri = str(row.get("predicate_iri") or "")
            if not iri or iri in labels:
                # ② is already answered: the response carries the model.yaml
                # projection in its own ``labels`` map, so repeating it on the
                # row would only give the reader two copies to reconcile.
                continue
            # ③ the source column heading the IR bound (the row already shows the
            # raw reference in its own cell, so only the IR's cleaned form is
            # used here) ④ the term IRI read as words, and only when that
            # actually reads better than the local name.
            fallback = (ir_meta.get(iri) or {}).get("column_label") or _humanize_term_iri(iri)
            if fallback:
                row["label"] = fallback


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


def _default_llm_models() -> dict[str, str | None]:
    """Non-secret per-provider default model ids, advertised to the browser.

    A fresh browser has an empty model registry, so without this it could not use
    an operator-configured shared key without first being asked to pick a
    provider and type a model id — the entry-gate the two-tier UX removes. These
    are plain identifiers (no secret), and an operator can pin one per provider
    with ``ASTERISM_LLM_MODEL_<PROVIDER>``. There is no built-in default for
    ``openai-compatible`` (model ids are endpoint-specific), so it stays ``None``
    unless the operator names one."""
    builtin: dict[str, str | None] = {
        "anthropic": DEFAULT_ANTHROPIC_MODEL,
        "openai": DEFAULT_OPENAI_MODEL,
        "openai-compatible": None,
    }
    resolved: dict[str, str | None] = {}
    for provider, fallback in builtin.items():
        env_name = f"ASTERISM_LLM_MODEL_{provider.replace('-', '_').upper()}"
        resolved[provider] = os.environ.get(env_name, "").strip() or fallback
    return resolved


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


# ---------------------------------------------------------------------------
# design-consult-chat.md: POST /api/design/consult
# ---------------------------------------------------------------------------

CONSULT_MAX_MESSAGES = 20
CONSULT_MAX_CONTENT_CHARS = 8000

# design-consult-chat.md D8: the manual (repo-root manual/ja/*.md) is the
# single source of truth for both the human-facing help text and the AI's
# knowledge of real navigation — a screen rename only needs the manual
# edited, never the prompt. A hardcoded catalog would rot the moment the UI
# changes; concatenating the manual instead means api/tests/test_design_consult.py
# can machine-check the manual's own UI-name claims against the i18n locales.
CONSULT_MANUAL_HEADING = "## マニュアル(実在する画面の操作)"

# design-consult-chat.md D9: the fenced-code-block language tag a suggestion
# block is wrapped in — the UI (ui/src/consult/ConsultDrawer.tsx) looks for
# this exact tag to find/hide the block and parse its JSON. Kept as one
# constant so the prompt text and any future reference to the tag agree.
CONSULT_SUGGESTIONS_FENCE = "asterism-suggestions"


def _find_consult_manual_dir() -> Path | None:
    """Resolve `manual/ja/`: ``ASTERISM_MANUAL_DIR`` env var if set (must exist),
    else search upward from this file's directory for a `manual/ja` dir (finds
    the repo-root `manual/ja/` in every deployment layout this codebase runs
    from — hosted image, asterism-local, dev checkout). None if neither is
    found — callers degrade silently rather than erroring: a missing manual
    should never take the consult endpoint down, it just loses the catalog
    (the "don't invent names" guardrail in the prompt still holds on its own)."""
    override = os.environ.get("ASTERISM_MANUAL_DIR", "").strip()
    if override:
        candidate = Path(override)
        return candidate if candidate.is_dir() else None
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "manual" / "ja"
        if candidate.is_dir():
            return candidate
    return None


# The manual grew from 2 files to a full user manual (2026-08-25, ADR
# design-consult-chat.md §3.8): injecting every chapter would blow the system
# prompt past ~30k chars per consult call. Only this core set — the 6-step
# walkthrough plus the screen-by-screen navigation reference — is injected;
# screens.md keeps a 1-2 line pointer for every feature, so the AI still knows
# where everything lives. The staleness test (test_manual_ui_names_exist_in_
# ui_locales) still checks EVERY manual/ja/*.md, injected or not.
_CONSULT_MANUAL_CORE = ("getting-started.md", "screens.md")


def _load_consult_manual() -> str:
    """Concatenate the core `manual/ja/` files (`_CONSULT_MANUAL_CORE`, sorted,
    deterministic order) into the block the system prompt injects. Read once at
    import time (see the module-level ``CONSULT_MANUAL_TEXT`` below) — not per
    request, so a consult call never does file I/O beyond the one LLM call.
    "" when the manual dir is missing or holds none of the core files."""
    manual_dir = _find_consult_manual_dir()
    if manual_dir is None:
        return ""
    parts: list[str] = []
    for path in sorted(manual_dir.glob("*.md")):
        if path.name not in _CONSULT_MANUAL_CORE:
            continue
        try:
            parts.append(path.read_text(encoding="utf-8").strip())
        except OSError:
            # The module logger (`logger = logging.getLogger(__name__)`)
            # isn't defined yet at this point in module load order (this
            # runs at import time, before that assignment) — a fresh
            # getLogger() call for the same name returns the identical
            # logger instance either way.
            logging.getLogger(__name__).warning(
                "consult manual: could not read %s (skipping)", path
            )
    return "\n\n".join(parts)


# Read once at process start (module import), matching every other
# module-level constant here — a request handler never re-reads the manual.
CONSULT_MANUAL_TEXT = _load_consult_manual()


def _build_consult_system_prompt(manual_text: str) -> str:
    """Assemble CONSULT_SYSTEM_PROMPT: role + the 6-step overview (kept here,
    not in the manual, since it doubles as this prompt's own outline) + the
    manual's real-navigation text (D8, absent when no manual dir was found)
    + the guardrails (D5 判断は代行しない)."""
    manual_block = (
        f"\n\n{CONSULT_MANUAL_HEADING}\n\n{manual_text}\n" if manual_text else ""
    )
    return f"""あなたは Asterism の設計相談役です。研究者がデータを Asterism に取り込む
とき、隣に座って質問に答える専門家として振る舞ってください。

Asterism の「かんたんモード」は次の6ステップで進みます。ユーザーがどのステップにいるかは
「## いま見ている画面」に書かれています。それぞれ一言で言うと:

1. 入れる - 元データ(CSV・Excel・装置ファイルなど)をアップロードする
2. AI が読む - AI がファイルを読み、種類(サンプル・測定条件・測定値など)を推定する
3. データの数えかた - 1行が何を表すか(例: 1サンプルにつき1行、1測定点につき1行)を決める
4. 項目の意味 - 各列が何を意味するか(例: 「Ic」は臨界電流、単位はA)を確認・修正する
5. ためす - 実際にデータを取り込んでみて、想定どおりの結果になるか試す
6. 公開する - 問題がなければ、他の人が引用できる形で公開する
{manual_block}
守るべきこと:
- 取り込む/取り込まないの裁定、列の意味や単位の最終判断は常にユーザーが行います。あなたは
  説明と参考情報を提示するだけで、判断を代行したり、フォームへの記入を指示したりしません。
  下で説明する提案ブロックも同じです——ブロックは候補の提示であり、採用と確定は必ず
  ユーザーが(表に反映されたあとで)行います。
- 列の意味・単位について具体的な候補を提示するときは、通常の説明文に加えて、応答の
  末尾に次の形式のコードブロックを 1 つだけ添えてください:
  ```{CONSULT_SUGGESTIONS_FENCE}
  {{"suggestions": [{{"column": "CSD", "meaning": "NIST 結晶構造データベースの収載コード",
  "unit": ""}}], "kinds": [{{"map": "peak", "name": "ピーク"}}]}}
  ```
  `suggestions` の `column` の値は「## いま見ている画面」に書かれている列名を一字一句
  そのまま使ってください(言い換え・意訳・翻訳しない)。「意味が未入力の項目」として挙げ
  られている列についても、同じ形式で候補を出せます。確信が持てない列は含めないでください。
  単位が無い/分からないときは `unit` を空文字にするか省略してください。
  `kinds` は「データの種類」(1 件が表すもの/種類名)について尋ねられたときだけ使います。
  `map` は「## いま見ている画面」の「データの種類」に書かれているマップ名を一字一句その
  まま、`name` はその内容を表す日本語の短い種類名(例: ピーク、試料)です。ID の作り方
  (どの列で数えるか)や、取り込む/取り込まないの裁定はここでは提案しません——種類名だけ
  です。`suggestions`・`kinds` はどちらも省略可能で、何も具体的に提案していない応答には
  このブロック自体を付けないでください。
- 操作の案内は、上の「{CONSULT_MANUAL_HEADING.removeprefix("## ")}」と「## いま見ている画面」
  に書かれている名前だけを使ってください。そこに無いボタン・メニュー・画面名を発明しては
  いけません。該当する導線が無い/分からないとき、あるいはマニュアルの記載が見当たらない
  ときは、存在しない名前をでっち上げず「いまの画面に見えているボタンの名前を教えてください」
  のように聞き返してください。
- 「## いま見ている画面」に文脈(ステップ・データセット名・骨格の要約・注目している列など)
  が添付されていれば、それに即して具体的かつ簡潔に答えてください。
- 一般的な使い方の質問(文脈がない、または一般的な内容)には、上記6ステップの説明とマニュアルを
  踏まえて答えてください。
- ドメイン略語(例: XRD カードの Quality・RIR(I/Ic)・Subfile)を聞かれたら、一般的な意味を
  説明したうえで、実データの値(添付されていれば)と整合するか一緒に考えてください。
- 知らないこと・確信が持てないことは、知らないとはっきり言ってください。憶測を断定調で語らない
  でください。
- 回答は簡潔に。長い前置きや繰り返しは避け、要点から述べてください。
- マニュアルは日本語の実際の画面表示に合わせています。英語で答えるときも、ボタン名・
  メニュー名は日本語の表記のまま書き、そのあとに簡単な英訳を添えてください
  (例: 「データを追加」(Add data))。
"""


# D5 (判断は代行しない): the model explains and points at reference material;
# the person still clicks 含める/除外する. Written in Japanese — the audience is
# a domain expert consulting mid-design, not a developer reading source.
CONSULT_SYSTEM_PROMPT = _build_consult_system_prompt(CONSULT_MANUAL_TEXT)


# S6 column-table context (2026-08-25 extension): bounds so a pathological
# client payload can't blow up the prompt — these are enforced here (not just
# trusted client-side truncation), same posture as CONSULT_MAX_CONTENT_CHARS.
_CONSULT_MAX_COLUMNS = 40
_CONSULT_MAX_SAMPLES_PER_COLUMN = 3
_CONSULT_MAX_FIELD_CHARS = 80
_CONSULT_COLUMNS_CHAR_BUDGET = 2000


def _clip(s: str, limit: int = _CONSULT_MAX_FIELD_CHARS) -> str:
    s = s.strip()
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def _render_name_and_samples(name: str, samples: list[str]) -> str:
    """One "name (例: a、b、c)" entry — the shape both droppedColumns
    ("まだ取り込んでいない項目") and meaning-blank confirmed columns ("意味が
    未入力の項目") render with, so they read identically to the person
    (they're the same kind of gap: a column with no meaning attached yet)."""
    clipped_samples = [
        _clip(s) for s in samples[:_CONSULT_MAX_SAMPLES_PER_COLUMN] if s and s.strip()
    ]
    entry = _clip(name)
    if clipped_samples:
        entry += f" (例: {'、'.join(clipped_samples)})"
    return entry


def _render_pending_columns(columns: list[ConsultPendingColumn]) -> str:
    """"まだ取り込んでいない項目" — S6's droppedColumns table, verbatim."""
    entries = [
        _render_name_and_samples(c.name, c.samples)
        for c in columns[:_CONSULT_MAX_COLUMNS]
        if c.name
    ]
    if not entries:
        return ""
    return f"まだ取り込んでいない項目 ({len(entries)} 件): " + ", ".join(entries)


def _render_confirmed_columns(columns: list[ConsultColumn]) -> str:
    """"意味が確定している項目" — S6's meaning table, only the rows that
    already have a meaning (a blank one is not "確定")."""
    entries = []
    for c in columns[:_CONSULT_MAX_COLUMNS]:
        if not c.name or not (c.meaning and c.meaning.strip()):
            continue
        entry = f"{_clip(c.name)} = {_clip(c.meaning)}"
        if c.unit and c.unit.strip():
            entry += f" [{_clip(c.unit, 20)}]"
        entries.append(entry)
    if not entries:
        return ""
    return "意味が確定している項目: " + ", ".join(entries)


def _render_missing_meaning_columns(columns: list[ConsultColumn]) -> str:
    """"意味が未入力の項目" — the SAME S6 meaning table as
    `_render_confirmed_columns`, but the complementary rows: already-mapped
    columns whose meaning cell is still blank. Without this line the model
    only ever saw columns that already had a meaning, so "propose meanings
    for the blank ones" had nothing to answer from (real-LLM dogfood
    2026-08-25: the model asked the person to type out the column names)."""
    entries = [
        _render_name_and_samples(c.name, c.samples)
        for c in columns[:_CONSULT_MAX_COLUMNS]
        if c.name and not (c.meaning and c.meaning.strip())
    ]
    if not entries:
        return ""
    return f"意味が未入力の項目 ({len(entries)} 件): " + ", ".join(entries)


def _render_kinds(kinds: list[ConsultKind]) -> str:
    """"データの種類" — S4 gate's per-map key columns + kind name, verbatim
    from the same data SkeletonGate renders (D10 extension B)."""
    entries = []
    for k in kinds[:_CONSULT_MAX_COLUMNS]:
        if not k.map:
            continue
        id_desc = "+".join(_clip(c, 30) for c in k.key_columns) or "なし"
        kind_desc = _clip(k.kind_name, 30) if k.kind_name and k.kind_name.strip() else "未入力"
        entries.append(f"{_clip(k.map, 30)} (ID: {id_desc}, 種類名: {kind_desc})")
    if not entries:
        return ""
    return "データの種類: " + ", ".join(entries)


def _render_consult_columns(ctx: ConsultContext) -> list[str]:
    """Render every S4/S6 column-or-kind line under one shared character
    budget (a long design must not fill the whole prompt) — over-budget lines
    are clipped with "(ほか N 列)" disclosed, never silently dropped."""
    pending = _render_pending_columns(ctx.pending_columns)
    confirmed = _render_confirmed_columns(ctx.columns)
    missing = _render_missing_meaning_columns(ctx.columns)
    kinds = _render_kinds(ctx.kinds)
    lines = [f"- {line}" for line in (pending, confirmed, missing, kinds) if line]
    total = sum(len(line) for line in lines)
    if total <= _CONSULT_COLUMNS_CHAR_BUDGET or not lines:
        return lines
    # Over budget: clip each line's rendered text to its share of the budget
    # (proportional to its own length) and say how much was cut, rather than
    # silently truncating mid-entry.
    budget = _CONSULT_COLUMNS_CHAR_BUDGET
    clipped: list[str] = []
    for line in lines:
        share = max(200, int(budget * (len(line) / total)))
        if len(line) <= share:
            clipped.append(line)
            continue
        cut = line[:share].rstrip()
        # Back off to the last complete entry (", " boundary) so we never cut
        # a column name/example in half.
        boundary = cut.rfind(", ")
        if boundary > 0:
            cut = cut[:boundary]
        remaining = line.count(", ") + 1 - (cut.count(", ") + 1)
        clipped.append(f"{cut} (ほか {remaining} 列)" if remaining > 0 else cut)
    return clipped


def _render_consult_context(ctx: ConsultContext | None) -> str:
    """Render the auto-attached design context (D4) as a Markdown block the
    system prompt tells the model to answer "in front of". Absent/empty
    fields are omitted — a general-thread question carries no block at all."""
    if ctx is None:
        return ""
    lines: list[str] = []
    if ctx.step:
        lines.append(f"- ステップ: {ctx.step}")
    if ctx.dataset:
        lines.append(f"- データセット名: {ctx.dataset}")
    if ctx.skeleton_summary:
        lines.append(f"- 骨格の要約: {ctx.skeleton_summary}")
    if ctx.focus_column and ctx.focus_column.name:
        samples = "、".join(s for s in ctx.focus_column.samples if s)
        col_line = f"- 注目している列: {ctx.focus_column.name}"
        if samples:
            col_line += f"(実データ例: {samples})"
        lines.append(col_line)
    lines.extend(_render_consult_columns(ctx))
    if not lines:
        return ""
    return "## いま見ている画面\n" + "\n".join(lines)


def _render_consult_prompt(messages: list[ConsultMessage], context: ConsultContext | None) -> str:
    """Render the transcript + context block into the single user_message the
    LLMClient protocol's ``complete()`` takes (system_prompt, user_message)."""
    parts: list[str] = []
    ctx_block = _render_consult_context(context)
    if ctx_block:
        parts.append(ctx_block)
    turns = []
    for m in messages:
        speaker = "ユーザー" if m.role == "user" else "アシスタント"
        turns.append(f"{speaker}: {m.content}")
    parts.append("\n\n".join(turns))
    return "\n\n".join(parts)


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


def _coded_error(status: int, code: str, message: str) -> HTTPException:
    """An HTTP error a client can act on WITHOUT reading English prose.

    The detail is ``{"code", "message"}``: the stable ``code`` is what logic keys
    off (the UI maps it to one plain sentence in one place — the same contract
    ``jobs._ERROR_CODES`` gives job failures), while ``message`` stays a short
    English technical summary for the folded technical view. The provider's /
    library's raw exception text never reaches the screen: it goes to the log,
    where it is useful, instead of into a stop card, where it is not.
    """
    return HTTPException(status, detail={"code": code, "message": message})


def _error_text(detail: object) -> str:
    """The human-readable half of a detail that may be coded or a bare string."""
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("error") or detail)
    return str(detail)


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


def _discover_targets(
    registry_root: Path, dataset_ids: list[str], max_datasets: int
) -> tuple[list[crosswalk_discover.DiscoverDataset], list[dict], bool]:
    """Which datasets discovery may scan, plus the ones it will not and why.

    Only PROMOTED, non-crosswalk datasets qualify: a draft is not citable, and a hub is
    a bridge rather than something to bridge. The registry read lives here so
    ``asterism.crosswalk_discover`` stays free of any api-layer dependency (the same
    split ``crosswalk_runtime`` keeps). Every exclusion is returned with a reason —
    "we found nothing" and "we did not look" must never be indistinguishable.
    """
    wanted = set(dataset_ids)
    targets: list[crosswalk_discover.DiscoverDataset] = []
    skipped: list[dict] = []
    for meta in registry.list_datasets(registry_root):
        dsid = str(meta.get("id") or "")
        if not dsid:
            continue
        if wanted and dsid not in wanted:
            skipped.append({"dataset_id": dsid, "reason": "not_requested"})
        elif meta.get("is_crosswalk"):
            skipped.append({"dataset_id": dsid, "reason": "crosswalk"})
        elif not meta.get("promoted"):
            skipped.append({"dataset_id": dsid, "reason": "not_promoted"})
        elif len(targets) >= max_datasets:
            skipped.append({"dataset_id": dsid, "reason": "over_max_datasets"})
        else:
            name = str(meta.get("name") or dsid)
            targets.append(
                crosswalk_discover.DiscoverDataset(
                    dataset_id=dsid, label=_crosswalk_label(name, dsid), name=name
                )
            )
    truncated = any(s["reason"] == "over_max_datasets" for s in skipped)
    return targets, skipped, truncated


def _crosswalk_label(name: str, dataset_id: str) -> str:
    """A crosswalk participant label from a dataset name (mirrors the authoring UI's
    ``labelFor``): ascii slug, falling back to the id when the name has no ascii."""
    slug = re.sub(r"^_+|_+$", "", re.sub(r"[^a-z0-9]+", "_", name.lower()))
    return slug or dataset_id


def _crosswalk_predicate_labels(registry_root: Path, dataset_id: str) -> dict[str, str]:
    """``{predicate_iri: label}`` for one dataset's design (XW-01/XW-04/XW-06).

    The SAME 2-step chain as the design SSOT: an authored §9 ``label`` (Mapping
    IR), then the ``model.yaml`` projection's ``rdfs:label`` — nothing else (no
    column-heading / term-IRI fallback here, unlike S7's fuller ``label_of``):
    a crosswalk candidate's heading and try-it question must say a word the
    design actually chose, or say nothing rather than guess one. Best-effort —
    an unreadable dataset/IR/model.yaml simply contributes no labels.
    """
    data = registry.load_dataset(registry_root, dataset_id)
    artifacts = (data or {}).get("artifacts") or {}
    labels: dict[str, str] = {}
    with contextlib.suppress(Exception):
        labels.update(
            _model_yaml_labels(
                str(artifacts.get("model.yaml") or ""),
                str(artifacts.get("mapping.rml.ttl") or ""),
                str(artifacts.get("mie.yaml") or ""),
            )
        )
    try:
        ir_meta = _ir_predicate_display(str(artifacts.get("mapping.yaml") or ""))
    except Exception:
        ir_meta = {}
    for iri, meta in ir_meta.items():
        lbl = meta.get("label")
        if lbl:  # the authored label wins over the model.yaml projection
            labels[iri] = lbl
    return labels


def _crosswalk_predicate_label_resolver(
    registry_root: Path,
) -> Callable[[str, str], str | None]:
    """A cached ``(dataset_id, predicate_iri) -> label|None`` closure for
    :func:`asterism.crosswalk_discover.discover`'s ``predicate_label_of`` hook —
    one registry read per dataset touched, however many predicates/candidates
    reference it (a discovery run reads every participant's predicates)."""
    cache: dict[str, dict[str, str]] = {}

    def resolve(dataset_id: str, predicate_iri: str) -> str | None:
        labels = cache.get(dataset_id)
        if labels is None:
            labels = _crosswalk_predicate_labels(registry_root, dataset_id)
            cache[dataset_id] = labels
        return labels.get(predicate_iri)

    return resolve


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


# The draft-stats correspondence card (kantan-mode ADR K12) counts data rows of
# these persisted tabular sources only. Instrument text (.txt/.dat/.asc) has
# preambles / logical records a bare line count would misread, and JSON/XML have
# no "rows" — those are skipped rather than guessed. An .xlsx upload was already
# converted to derived .csv files at attach, so it IS covered via those.
_ROW_COUNT_SUFFIXES = (".csv", ".tsv")


def _count_source_rows(root: Path, dataset_id: str) -> dict[str, int]:
    """Header-excluded data-row counts of a dataset's persisted tabular source.

    Best-effort by contract: only ``.csv`` / ``.tsv`` are counted (see
    ``_ROW_COUNT_SUFFIXES``); a file that cannot be read is silently omitted —
    the correspondence card is enrichment, never a gate. Quoted embedded
    newlines are handled by the csv reader (a raw line count would overcount).
    """
    out: dict[str, int] = {}
    for path in registry.list_source_files(root, dataset_id):
        suffix = path.suffix.lower()
        if suffix not in _ROW_COUNT_SUFFIXES:
            continue
        delimiter = "\t" if suffix == ".tsv" else ","
        try:
            with path.open(newline="", encoding="utf-8-sig", errors="replace") as fh:
                reader = csv.reader(fh, delimiter=delimiter)
                rows = sum(1 for row in reader if any(cell.strip() for cell in row))
        except OSError:
            continue
        out[path.name] = max(0, rows - 1)  # minus the header row
    return out


def _curie_of(iri: str, prefixes: Mapping[str, str]) -> str | None:
    """Best-effort CURIE for ``iri`` under ``prefixes`` (longest namespace wins).

    Display-only enrichment for the draft-stats classes; ``None`` when no
    declared namespace prefixes the IRI or the remainder is not one clean
    segment (the UI then falls back to the label / local name).
    """
    best: tuple[str, str] | None = None
    for prefix, ns in prefixes.items():
        if ns and iri.startswith(ns) and (best is None or len(ns) > len(best[1])):
            best = (prefix, ns)
    if best is None:
        return None
    local = iri[len(best[1]) :]
    if not local or "/" in local or "#" in local:
        return None
    return f"{best[0]}:{local}"


async def _project_ontology_graph(
    client: OxigraphClient, dataset_id: str, artifacts: dict[str, str]
) -> int:
    """#20 step5: project the dataset's TBox into its ontology named graph.

    Additive + best-effort: tries the bundle's ``mapping.yaml`` (Mapping IR,
    K8) FIRST — it is the only source carrying a reviewer's human-authored
    ``label:`` (kantan-mode's "used for search and citation" promise), so it
    must win whenever present. Only when the IR is absent or projects nothing
    does this fall back to the legacy ``model.yaml`` TBox (rdf-config list or
    the plain ``classes:``/``properties:`` mapping shape — both accepted by
    :func:`project_model_yaml`), which never carries an authored label.
    Prefixes resolve from the bundle's own RML / MIE declarations (so ``sd:`` /
    ``sdr:`` map to THIS dataset's IRIs) unioned with standard ones, then
    replaces the ontology graph (DROP then load) so a re-promote has no stale
    triples. Returns the triple count (0 = nothing projected — legitimate when
    the bundle carries neither artifact). Never raises — a projection failure
    must not block a promote (the TBox graph is enrichment; Ask works from the
    ABox regardless). Logs a warning (not silence) when an artifact WAS present
    but still produced zero triples — that is an unparsed/unexpected shape, not
    the legitimate "nothing to project" case.
    """
    mapping_ir_yaml = artifacts.get("mapping.yaml") or ""
    model_yaml = artifacts.get("model.yaml") or ""
    prefixes = STANDARD_PREFIXES | extract_prefixes(
        artifacts.get("mapping.rml.ttl") or "", artifacts.get("mie.yaml") or ""
    )

    graph = None
    if mapping_ir_yaml.strip():
        graph = project_mapping_ir(mapping_ir_yaml, prefixes)
        if len(graph) == 0:
            logger.warning(
                "dataset %s: mapping.yaml (Mapping IR) present but projected "
                "0 ontology triples (unparsed shape?); falling back to model.yaml",
                dataset_id,
            )
            graph = None

    if graph is None and model_yaml.strip():
        graph = project_model_yaml(model_yaml, prefixes)
        if len(graph) == 0:
            logger.warning(
                "dataset %s: model.yaml present but projected 0 ontology "
                "triples (unrecognized shape?)",
                dataset_id,
            )
            graph = None

    if graph is None or len(graph) == 0:
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

# The desktop release feed (Tauri updater manifest). ONE endpoint, the same the
# native updater installs from — this backend only *reads* it to answer "is a
# newer version out?"; installing stays with the shell (ADR
# local-first-distribution.md: the SPA is never wired to Tauri IPC).
# Served from GitHub Pages (`docs/updater/latest.json` on main), NOT from the
# release. `releases/latest/download/...` starts pointing at a new tag the moment
# tagpr publishes it, but the desktop build needs ~15 minutes to attach the file
# — so every update check in that window 404'd and surfaced here as a 502
# (observed live on v0.14.0). The Pages copy is written only after a build
# succeeds, so it is never missing and never half-published.
DEFAULT_UPDATER_FEED: Final = "https://kumagallium.github.io/asterism/updater/latest.json"


def _write_credential_ok(
    cfg: Settings, authorization: str | None, x_asterism_token: str | None
) -> bool:
    """Does this request carry the write token? ``Authorization: Bearer <t>`` or
    ``X-Asterism-Token: <t>``, compared in constant time. False when no token is
    configured server-side (the gate is closed for everyone)."""
    token = cfg.api_token
    if not token:
        return False
    presented: str | None = None
    if authorization and authorization.startswith("Bearer "):
        presented = authorization[len("Bearer ") :].strip()
    elif x_asterism_token:
        presented = x_asterism_token.strip()
    return bool(presented) and hmac.compare_digest(presented, token)


def _version_tuple(version: str) -> tuple[int, int, int]:
    """``0.13.2`` → ``(0, 13, 2)`` for ordering. A pre-release/build suffix
    (``0.14.0-rc1``) contributes its numeric head only — enough to order
    releases, and a malformed feed can never raise here."""
    parts = re.split(r"[.+-]", version.strip().lstrip("v"))[:3]
    nums = [int(m.group()) if (m := re.match(r"\d+", part)) else 0 for part in parts]
    nums += [0] * (3 - len(nums))
    return (nums[0], nums[1], nums[2])

# Restrict uploaded filenames to a safe subset to avoid directory traversal
# (``..`` segments, absolute paths, NULs). We also reject names without a
# ``.csv`` suffix so the watcher's ``_classify`` actually fires.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,128}\.csv$")
# The step0 / source / ingest paths accept JSON (#19), XML/JATS, and Word .docx
# (document-ontology layer). CSV/JSON/XML are ingested directly; a .docx is
# CONVERTED to JATS (pandoc, optional) at source-attach and the resulting .xml
# becomes the persisted source. Legacy instrument exports (.tsv/.txt/.dat/.asc)
# are tabular sources too (ADR source-dialect.md — dialect handling lives in
# step0/ingest; the api only widens the entrance). An Excel ``.xlsx`` (kantan-
# mode K6) is CONVERTED to CSV(s) at every tabular entrance — like .docx, the
# original never becomes an ``rml:source``. The legacy ``/upload/{kind}``
# starrydata drop stays CSV-only (it feeds the CSV watcher).
_SAFE_SOURCE_NAME = re.compile(
    r"^[A-Za-z0-9._-]{1,128}\.(csv|tsv|txt|dat|asc|json|geojson|xml|docx|pdf|xlsx)$"
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
        # Desktop shell identity. The Tauri shell passes its own bundle version
        # when it spawns this backend, so "which Asterism am I running?" has one
        # answer the SPA can read (the window is a remote http://127.0.0.1 origin
        # and deliberately has no Tauri IPC — see local-first-distribution.md).
        # Unset → a server/web install: the About surface hides the desktop-only
        # update check instead of inventing a version.
        self.app_version = (e.get("ASTERISM_APP_VERSION") or "").strip() or None
        # Release feed the desktop update check reads — the same single endpoint
        # the native updater installs from (tauri.conf.json plugins.updater).
        self.updater_feed = (
            e.get("ASTERISM_UPDATER_FEED") or DEFAULT_UPDATER_FEED
        ).strip()
        # togomcp auto-publish (ADR togomcp-auto-publish.md): promote projects the
        # dataset's MIE into this togomcp TOGOMCP_DIR (mie/<id>.yaml + an
        # endpoints.csv row) so promoted datasets appear in the DBCLS togomcp
        # catalog. Unset → disabled. The file layout is the ONLY coupling —
        # togomcp itself is never imported.
        togomcp_raw = (e.get("ASTERISM_TOGOMCP_DIR") or "").strip()
        self.togomcp_dir = Path(togomcp_raw) if togomcp_raw else None
        # The SPARQL endpoint URL as togomcp (same compose network) reaches it,
        # and the endpoint_name grouping key in endpoints.csv.
        self.togomcp_endpoint_url = e.get(
            "ASTERISM_TOGOMCP_ENDPOINT_URL", "http://oxigraph:7878/query"
        )
        self.togomcp_endpoint_name = e.get("ASTERISM_TOGOMCP_ENDPOINT_NAME", "oxigraph")
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
        #
        # 5, not 3, since 2026-08-16: the loop now also has to clear the bundle
        # traps (T1-T10) that used to be left to the human's repeated "AI に直し
        # てもらう" click. The observed live count on a small XRD file was ~5
        # clicks; giving the machine fewer rounds than the human was making
        # would just hand the tail back to the human.
        try:
            self.autocorrect_rounds = max(0, int(e.get("ASTERISM_AUTOCORRECT_ROUNDS", "5")))
        except ValueError:
            self.autocorrect_rounds = 5
        # Wall-clock cap on ONE background job (propose / refine / ingest). A stuck
        # LLM call otherwise runs forever with no signal (the 400-minute propose).
        # "0" disables the cap (None → JobManager runs jobs unbounded, as before).
        try:
            timeout_s = float(e.get("ASTERISM_JOB_TIMEOUT_SECONDS", "3600"))
        except ValueError:
            timeout_s = 3600.0
        self.job_timeout_seconds: float | None = timeout_s if timeout_s > 0 else None
        # Single-user mode (ADR app-data-on-disk.md): asterism-local sets
        # both of these so Ask threads / app settings get a server-side home on
        # disk instead of the browser's localStorage. Unset (the shared/hosted
        # api) → the /api/appdata/* routes stay 404 except GET .../info.
        self.single_user = (e.get("ASTERISM_SINGLE_USER") or "").strip().lower() in (
            "1", "true", "yes",
        )
        appdata_raw = (e.get("ASTERISM_APPDATA_ROOT") or "").strip()
        self.appdata_root = Path(appdata_raw) if appdata_raw else None


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
            "filename must end in .(csv|tsv|txt|dat|asc|json|geojson|xml|docx|pdf|xlsx) "
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


# Cap on an .xlsx upload: the whole workbook is read into memory for conversion
# (unlike CSV, which streams to disk), so it gets the document-style bound rather
# than the 1 GiB streaming cap. Same figure as the .docx / .pdf document caps.
_MAX_XLSX_BYTES: Final[int] = 64 * 1024 * 1024


def _expand_xlsx_bytes(name: str, data: bytes) -> list[tuple[str, bytes]]:
    """``[(csv_name, csv_bytes), …]`` — :func:`_expand_xlsx_sheets` without titles."""
    return [(n, body) for n, _title, body in _expand_xlsx_sheets(name, data)]


def _expand_xlsx_sheets(name: str, data: bytes) -> list[tuple[str, str, bytes]]:
    """Convert a canonicalized ``.xlsx`` upload to its derived CSV set (K6).

    ``[(csv_name, worksheet_title, csv_bytes), …]`` — the title is what the person
    called the sheet in Excel, which the derived filename may have hashed away
    (K6's "which sheet do you mean?" has to ask in their words, not in slugs).

    One sheet = one CSV (``asterism.tabularize.xlsx_to_csvs`` — openpyxl,
    deterministic). Everything downstream — inspect, the design loop,
    ``rml:source`` — only ever sees the derived ``.csv`` names; ``.xlsx`` is NOT
    in the rml_safety source allow-list. An unreadable workbook (corrupt zip,
    every sheet empty) is a coded 422, not a traceback: openpyxl's own wording
    ("File is not a zip file", "Max value is 14") means nothing to the person
    who just dropped a spreadsheet, so it is logged and the client renders one
    plain sentence off the code.
    """
    from asterism.tabularize import xlsx_to_csv_sheets

    try:
        return xlsx_to_csv_sheets(data, stem=Path(name).stem)
    except ValueError as exc:
        logger.warning("xlsx → csv conversion failed for %r: %s", name, exc)
        raise _coded_error(
            422, "xlsx.convert_failed", "the Excel workbook could not be converted to CSV"
        ) from exc
    except Exception as exc:
        logger.warning("xlsx could not be read: %r: %s", name, exc, exc_info=True)
        raise _coded_error(
            422, "xlsx.unreadable", "the Excel workbook could not be read"
        ) from exc


async def _persist_converted_xlsx(
    upload: UploadFile, sdir: Path, name: str, keep: Collection[str] | None = None
) -> tuple[list[str], dict]:
    """Convert an Excel ``.xlsx`` upload to CSV(s) and persist them as the source.

    Returns ``([csv_filename, …], conversion_record)``. The derived ``.csv`` files
    are the persisted SOURCE (what the RML maps); the original ``.xlsx`` is kept
    alongside for re-conversion / provenance (listed, but classified as a csv-kind
    source). Mirrors :func:`_persist_converted_docx`, incl. the per-file
    ``<name>.conversion`` sidecar (NOT a source suffix).
    """
    import openpyxl

    data = await _read_upload_bounded(upload, _MAX_XLSX_BYTES)
    derived = await asyncio.to_thread(_expand_xlsx_bytes, name, data)
    # ``keep`` = the sheets the human chose at S2 (K6). A workbook routinely
    # carries a chart sheet and a notes sheet; persisting those as sources makes
    # the design answer for columns nobody meant to publish. None = keep all
    # (every caller that never asks the question).
    if keep is not None:
        chosen = [(n, b) for n, b in derived if n in keep]
        if chosen:  # an empty selection is a client bug — never drop the source
            derived = chosen
    await asyncio.to_thread(sdir.mkdir, parents=True, exist_ok=True)
    conversion = {"tool": "openpyxl", "version": openpyxl.__version__, "from": name}
    for csv_name, csv_bytes in derived:
        await asyncio.to_thread((sdir / csv_name).write_bytes, csv_bytes)
        await asyncio.to_thread(
            (sdir / f"{csv_name}.conversion").write_text,
            json.dumps(conversion, ensure_ascii=False),
            "utf-8",
        )
    await asyncio.to_thread((sdir / name).write_bytes, data)  # keep the original .xlsx
    return [csv_name for csv_name, _ in derived], conversion


async def _save_tabular_uploads(
    files: list[UploadFile], dest_dir: Path, sheets_out: dict[str, dict[str, str]] | None = None
) -> list[Path]:
    """Canonicalize + save tabular uploads for a design entrance (inspect / propose
    / skeleton / validate / continue) — the ONE convert+sanitize seam.

    Every filename goes through :func:`_sanitize_tabular_name`; a ``.xlsx`` is
    expanded to its derived CSV set here, BEFORE the path list is built, so the
    returned paths (and thus ``X-Asterism-Source-Names`` / the design's
    ``rml:source``) are always the canonical CSV names.

    ``sheets_out``, when given, collects ``{csv_name: {"from": xlsx, "sheet":
    title}}`` for every workbook that expanded into MORE THAN ONE table — the
    only case K6 asks "which sheet do you mean?" about.
    """
    paths: list[Path] = []
    for upload in files:
        if upload.filename is None:
            raise HTTPException(400, "missing filename")
        name = _sanitize_tabular_name(upload.filename)
        if name.lower().endswith(".xlsx"):
            data = await _read_upload_bounded(upload, _MAX_XLSX_BYTES)
            derived = await asyncio.to_thread(_expand_xlsx_sheets, name, data)
            for csv_name, title, csv_bytes in derived:
                dest = dest_dir / csv_name
                await asyncio.to_thread(dest.write_bytes, csv_bytes)
                paths.append(dest)
                if sheets_out is not None and len(derived) > 1:
                    sheets_out[csv_name] = {"from": name, "sheet": title}
        else:
            dest = dest_dir / name
            await _save_upload(upload, dest)
            paths.append(dest)
    return paths


def _uploads_from_dir(directory: Path) -> list[UploadFile]:
    """Wrap files on disk as UploadFiles, so a staged source flows through the
    SAME converters (`_persist_source_uploads`, `_save_tabular_uploads`) as a
    fresh upload — one conversion path, not two."""
    out: list[UploadFile] = []
    for p in sorted(x for x in directory.iterdir() if x.is_file()):
        out.append(UploadFile(file=p.open("rb"), filename=p.name))
    return out


async def _design_sources(
    registry_root: Path,
    files: list[UploadFile],
    staging_id: str | None,
    *,
    prefix: str,
    sheets_out: dict[str, dict[str, str]] | None = None,
) -> tuple[Path, list[Path], bool]:
    """Where a design call reads its sources from: ``(work_dir, paths, owned)``.

    ``staging_id`` (ADR source-staging.md) → the staged record's own directory,
    ``owned=False`` (the caller must NOT delete it; the record outlives the
    call). Otherwise the legacy shape: uploads canonicalised into a fresh temp
    dir, ``owned=True`` (delete when done). A missing/expired staging id is a
    404 the client answers by re-uploading its own copy.

    ``sheets_out`` collects ``{derived csv: {"from", "sheet"}}`` for multi-sheet
    workbooks (K6) — read off the staged record when there is one, computed from
    the uploads otherwise.
    """
    if staging_id:
        try:
            sdir, paths = staging.load(registry_root, staging_id)
        except staging.StagingNotFound as exc:
            raise HTTPException(404, f"staging {staging_id!r} not found (expired?)") from exc
        if sheets_out is not None:
            known = _staging_meta(sdir).get("sheets")
            if isinstance(known, dict):
                sheets_out.update(known)
        return sdir, paths, False
    if not files:
        raise HTTPException(400, "no files uploaded")
    tmpdir = Path(tempfile.mkdtemp(prefix=prefix))
    return tmpdir, await _save_tabular_uploads(files, tmpdir, sheets_out), True


async def _persist_source_uploads(
    registry_root: Path,
    dataset_id: str,
    files: list[UploadFile],
    keep: Collection[str] | None = None,
) -> tuple[list[str], dict | None]:
    """Persist uploaded sources as the dataset's design-time source (Task E, #19).

    Streams each upload into ``registry_root/<id>/source/`` (resetting any prior
    source so it reflects exactly this upload) and records the filenames + source
    kind on the meta. A Word ``.docx`` is CONVERTED to JATS (pandoc) and the
    resulting ``.xml`` becomes the persisted source (the conversion is recorded so
    the document ingest can disclose it); an Excel ``.xlsx`` is likewise CONVERTED
    to CSV(s) (openpyxl, K6) with the original kept alongside. This lets a
    *design*-stage dataset be ingested from the catalog later with no re-attach
    (reproducibility).
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
        elif name.lower().endswith(".xlsx"):
            csv_names, conversion = await _persist_converted_xlsx(upload, sdir, name, keep)
            saved.extend(csv_names)
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
        # Identify-and-advise (ADR source-dialect.md): the preamble's detected
        # SHAPE ("keyvalue" / "keyvalue_cells" / "lines") so the client's
        # "keep the metadata" answer can pin the right parsing mode instead of
        # hardcoding one. Additive — absent when there is no preamble.
        hint = getattr(ins, "preamble_hint", None)
        if hint is not None:
            out[ins.name]["preamble_hint"] = hint
    return json.dumps(out, separators=(",", ":"))


# How many example values per column travel to the client, and how long each
# one may be. The wizard shows at most three per column; a 200-character cell
# (a JSON blob, a pasted note) is not an example anyone reads, and the whole set
# rides a response HEADER, which has to stay small.
_SAMPLE_VALUES_PER_COLUMN = 3
_SAMPLE_VALUE_MAX_CHARS = 60
# Hard ceiling on the serialized samples header. Past it the header is dropped
# entirely (the client falls back to its own client-side preview) rather than
# truncated into something half-true.
_SAMPLES_HEADER_BUDGET = 6000


# Persisted source files that HAVE columns to sample. The source directory also
# keeps the original ``.xlsx`` a design was converted from and, for a document
# dataset, the ``.pdf`` / ``.xml`` — none of which the tabular inspector reads.
_SAMPLEABLE_SUFFIXES = (".csv", ".tsv", ".txt", ".dat", ".asc", ".json", ".geojson")


def _column_samples(inspections: list) -> dict[str, dict[str, list[str]]]:
    """``{source_name: {column: [up to 3 real values]}}`` from an inspection set.

    The values are the inspector's own ``sample_values`` — the first non-empty
    cells of that column, in file order. Deliberately NOT assembled into rows:
    each column's examples are taken independently, so presenting them as a table
    of rows would show a record that does not exist in anyone's file.
    """
    out: dict[str, dict[str, list[str]]] = {}
    for ins in inspections:
        cols: dict[str, list[str]] = {}
        for col in getattr(ins, "columns", []) or []:
            values = [
                v if len(v) <= _SAMPLE_VALUE_MAX_CHARS else v[:_SAMPLE_VALUE_MAX_CHARS] + "…"
                for v in (getattr(col, "sample_values", []) or [])[:_SAMPLE_VALUES_PER_COLUMN]
            ]
            cols[col.name] = values
        if cols:
            out[ins.name] = cols
    return out


def _read_source_preamble_origins(path: Path, dialect: SourceDialect, *, log_tag: str) -> list:
    """Read ``path``'s first ``dialect.skip_rows`` lines and return their
    :class:`~asterism_step0.dialect.PreambleOrigin` provenance (:func:`read_preamble_origins`).

    The single place both :func:`_preamble_column_origins` (the post-attach
    "which column came from where" answer) and :func:`_preamble_header_json`
    (the pre-attach /api/inspect answer) read a file's preamble lines and hand
    them to ``read_preamble_origins`` — so the file-open, decode-error, and
    "no preamble to read" handling exist in exactly one place. Best-effort: an
    unreadable file logs (tagged ``log_tag`` so the two callers' log lines stay
    distinguishable) and answers ``[]`` rather than raising.
    """
    if dialect.preamble == "drop" or dialect.skip_rows <= 0:
        return []
    from asterism_step0.dialect import read_preamble_origins

    try:
        with path.open(encoding=dialect.encoding, newline="") as fh:
            preamble_lines: list[str] = []
            for _ in range(dialect.skip_rows):
                line = fh.readline()
                if not line:
                    break
                preamble_lines.append(line)
    except OSError:
        logger.warning("%s: %s could not be read", log_tag, path.name)
        return []
    return read_preamble_origins(preamble_lines, dialect.preamble, delimiter=dialect.delimiter)


def _preamble_column_origins(
    path: Path, dialect: SourceDialect, resolved_columns: list[str]
) -> dict[str, dict[str, object]]:
    """Where each BROADCAST preamble column of ``path`` came from.

    A column produced by ``preamble: lines``/``keyvalue``/``keyvalue_cells`` was
    never written by the person who made the file — the meaning screen must not
    present an ``asterism``-invented name (``preamble_1``) as if it were the
    file's own column name. ``resolved_columns`` is the FULL, already-resolved
    column name list for this one source, in the order :func:`iter_rows`
    produces it (body columns first, then the broadcast meta columns) — the same
    list the caller already has from the inspection, so the meta tail can be
    sliced off and run back through :func:`resolve_header` to reproduce the
    EXACT collision-suffixed names (``_2``/``_3``) the broadcast used, rather
    than guessing at them.

    Best-effort by contract (mirrors the endpoint): an unreadable file, or a
    column count that does not line up with what :func:`read_preamble_origins`
    reports, answers ``{}`` rather than mislabeling a column or raising.
    """
    origins = _read_source_preamble_origins(path, dialect, log_tag="source origins")
    if not origins:
        return {}
    from asterism_step0.dialect import resolve_header

    n_body = len(resolved_columns) - len(origins)
    if n_body < 0:
        # The inspection's column count does not match what read_preamble_origins
        # would broadcast — resolve_header could not reproduce the right names,
        # so refuse rather than risk pointing an origin at the wrong column.
        logger.warning(
            "source origins: %s column count does not match the preamble broadcast", path.name
        )
        return {}
    body_names = resolved_columns[:n_body]
    resolved_meta = resolve_header(body_names, [o.name for o in origins])
    return {
        name: {"source": path.name, "line": o.line, "text": o.text, "named": o.named}
        for name, o in zip(resolved_meta, origins, strict=True)
    }


def _samples_header_json(inspections: list) -> str:
    """Compact JSON for the ``X-Asterism-Samples`` response header of /api/inspect.

    Why it exists: ``.xlsx`` and ``.json`` cannot be parsed in the browser, so the
    "read check" screen showed a bare filename card for the persona's MAIN file
    format — nothing to confirm, and the column examples on the meaning screen
    stayed empty for the whole run (KZ-A-08). The server has already read those
    files; this hands back what it saw. Over budget → ``{}`` (the client keeps its
    own preview): a partial answer here would silently hide columns.
    """
    payload = json.dumps(_column_samples(inspections), separators=(",", ":"))
    return payload if len(payload) <= _SAMPLES_HEADER_BUDGET else "{}"


def _preamble_header_json(inspections: list, paths: list[Path]) -> str:
    """Compact JSON for the ``X-Asterism-Preamble`` response header of /api/inspect.

    The "read check" screen (S2) is where a preamble line first becomes visible
    to a human — and the only moment before ``preamble_1`` gets baked into the
    design, the IRI, and the published item name. Answering "what would each
    dropped-preamble line become if I choose to record it" here means the
    screen can offer that choice instead of the machine-invented name silently
    winning.

    ``{source name: [{name, line, text, named}, …]}`` — only for sources where
    the inspector detected a still-dropped preamble (``ins.preamble_hint`` is
    set: ADR source-dialect.md's identify-and-advise). The mode used is that
    SAME hint — the shape the "record it" answer would actually pin — not the
    dialect the file was just read with (which is still ``preamble: drop``).
    ``named`` mirrors :func:`_preamble_column_origins`: True only when the file
    itself wrote that name (a ``key:``/``key=`` label), False for an invented
    ``preamble_N``. Reuses :func:`_read_source_preamble_origins` — the same
    file-read + :func:`~asterism_step0.dialect.read_preamble_origins` call the
    post-attach ``origins`` answer uses — so the rule is written once.

    Best-effort per source (an unreadable file just has no key, matching
    ``_preamble_column_origins``). Over budget → ``{}`` (mirrors
    ``X-Asterism-Samples``): the wizard falls back to no per-line naming rather
    than the response failing to send.
    """
    import dataclasses

    paths_by_name = {p.name: p for p in paths}
    out: dict[str, list[dict[str, object]]] = {}
    for ins in inspections:
        hint = getattr(ins, "preamble_hint", None)
        dialect = getattr(ins, "dialect", None)
        if hint is None or dialect is None:
            continue
        path = paths_by_name.get(ins.name)
        if path is None:
            continue
        hinted_dialect = dataclasses.replace(dialect, preamble=hint)
        try:
            origins = _read_source_preamble_origins(
                path, hinted_dialect, log_tag="inspect preamble"
            )
        except Exception:
            logger.warning("inspect preamble: %s could not be attributed", path.name)
            continue
        if not origins:
            continue
        out[ins.name] = [
            {"name": o.name, "line": o.line, "text": o.text, "named": o.named} for o in origins
        ]
    payload = json.dumps(out, separators=(",", ":"))
    return payload if len(payload) <= _SAMPLES_HEADER_BUDGET else "{}"


def _staging_meta(sdir: Path) -> dict:
    """The staging record's ``meta.json`` (``{}`` when unreadable)."""
    try:
        data = json.loads((sdir / "meta.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_staging_meta(sdir: Path, meta: dict) -> None:
    (sdir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _parse_column_meanings(raw: str) -> list[dict[str, str]]:
    """Parse the wizard's settled column meanings (a JSON form field on a design job).

    ``[{source, column, label?, unit?}]`` — the input layer of ADR
    meaning-before-identity: what each column MEANS, decided before this design
    existed and therefore keyed by the file and the header rather than by a
    predicate. Empty / blank → ``[]`` (byte-identical to a job that carries none).
    An entry with no source or no column cannot be filed against anything, so it
    is a readable 422 rather than a meaning that silently lands nowhere.
    """
    raw = (raw or "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, f"column_meanings is not valid JSON: {exc}") from exc
    if not isinstance(parsed, list):
        raise HTTPException(422, "column_meanings must be a JSON array")
    out: list[dict[str, str]] = []
    for entry in parsed:
        if not isinstance(entry, dict):
            raise HTTPException(422, "each column meaning must be a JSON object")
        source = str(entry.get("source") or "").strip()
        column = str(entry.get("column") or "").strip()
        if not source or not column:
            raise HTTPException(422, "each column meaning needs a source and a column")
        row = {"source": source, "column": column}
        for field_name in ("label", "unit"):
            text = str(entry.get(field_name) or "").strip()
            if text:
                row[field_name] = text
        out.append(row)
    return out


def _parse_design_column_decisions(raw: str) -> list[dict[str, str]]:
    """Parse the wizard's pre-design column decisions (a JSON form field).

    ``[{source, column, action}]``. Only ``exclude`` is accepted here: before a
    design exists there is no map to attach an ``include`` or an ``own`` to, and
    the default is that every column is taken in — so the only thing a person
    can say at that point is which columns they do NOT want. The post-design
    vocabulary (all three actions) stays on
    ``POST /api/datasets/{id}/column-decisions``.
    """
    raw = (raw or "").strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, f"column_decisions is not valid JSON: {exc}") from exc
    if not isinstance(parsed, list):
        raise HTTPException(422, "column_decisions must be a JSON array")
    out: list[dict[str, str]] = []
    for entry in parsed:
        if not isinstance(entry, dict):
            raise HTTPException(422, "each column decision must be a JSON object")
        source = str(entry.get("source") or "").strip()
        column = str(entry.get("column") or "").strip()
        action = str(entry.get("action") or "").strip()
        if not source or not column:
            raise HTTPException(422, "each column decision needs a source and a column")
        if action != "exclude":
            raise HTTPException(
                422, "before a design exists the only column decision is 'exclude'"
            )
        out.append({"source": source, "column": column, "action": action})
    return out


def _parse_dialect_overrides(raw: str) -> dict[str, dict[str, Any]]:
    """Parse + boundary-check the wizard's dialect overrides (a JSON form field).

    ``{source_name: {encoding?, delimiter?, collapse?, skip_rows?}}``. Empty / blank
    → ``{}`` (no override; effective == detected, byte-identical to today). Reuses the
    IR's dialect linter (``mapping_ir._parse_dialects``) with NO declared maps, so only
    the field-level rules run — the same contract the compiled RML annotations enforce:
    a text codec (not a bytes↔bytes codec), a single-char delimiter or ``whitespace``,
    a boolean ``collapse``, a non-negative ``skip_rows``, no unknown keys. An invalid
    value is a readable 422 (never a 500 / a silently bad §9 annotation).

    Returns ``{source_name: {field: lint-checked value}}`` carrying ONLY the fields the
    person actually wrote — never a whole ``SourceDialect``. A ``SourceDialect`` cannot
    say "not specified": every absent field silently reads back as its class default,
    and the caller merges the override OVER detection, so an override that corrected
    only the delimiter used to reset a detected ``cp932`` to ``utf-8-sig`` and the
    design pinned a reading the file cannot be read with (live 2026-08-26: an XRD
    export whose only non-ASCII bytes are the two header cells 2θ / 強度 — detection
    got it right at every stage and the pin threw it away). Field-level merging is
    :func:`design_loop.merge_dialect_overrides`.
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
    from asterism_step0.mapping_ir import _DIALECT_KEYS, _parse_dialects

    issues: list[str] = []
    dialects = _parse_dialects(parsed, [], issues)  # no maps → field-only lint
    if issues:
        raise HTTPException(422, "; ".join(issues))
    # Keep the linted VALUE (coerced / normalized by the linter) but only for the keys
    # the request actually carried — the rest stays "not specified" for the merge.
    out: dict[str, dict[str, Any]] = {}
    for name, fields in parsed.items():
        linted = dialects.get(str(name))
        if linted is None or not isinstance(fields, dict):
            continue
        out[str(name)] = {k: getattr(linted, k) for k in _DIALECT_KEYS if k in fields}
    return out


def _effective_dialects(
    paths: list[Path], overrides: Mapping[str, Mapping[str, Any]] | None
) -> dict[str, Any]:
    """The read rules in force for these uploads: detection with the human's
    corrections laid over it field by field — the same map
    :func:`design_loop.run_design_loop` builds, so the skeleton stage reads every
    source exactly the way the generation stage will. Blocking I/O (it reads each
    file): call it off the event loop."""
    return design_loop.merge_dialect_overrides(
        design_loop._detect_source_dialects(list(paths)), overrides
    )


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


def _batch_header_columns(content: bytes, dialect: SourceDialect | None) -> set[str]:
    """The header row's column names for a batch's bytes, read the same way
    materialization will (GAL-A-40's content check must see what Morph-KGC sees).

    Falls back to a bare comma / ``utf-8-sig`` read when the source has no pinned
    dialect (today's plain-CSV append path — :data:`asterism.dialect.DEFAULT_DIALECT`
    is exactly that). Any read failure (bad encoding, empty file, …) returns an
    empty set, which the caller treats as "every expected column is missing" — the
    fail-closed direction for a rename decision.
    """
    from asterism.dialect import DEFAULT_DIALECT, dialect_rows

    work = Path(tempfile.mkdtemp(prefix="asterism-append-header-"))
    try:
        tmp = work / "batch"
        tmp.write_bytes(content)
        try:
            row = next(dialect_rows(tmp, dialect or DEFAULT_DIALECT))
        except (StopIteration, UnicodeDecodeError, OSError, ValueError):
            return set()
        return {c for c in row if c}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _expected_columns_for_single_source(
    mapping_ir_yaml: str, source_name: str
) -> set[str] | None:
    """The Mapping IR's referenced columns for the dataset's single ``rml:source``.

    GAL-A-40: before a mismatched batch filename is machine-renamed to the pinned
    source name, this proves the rename is safe by CONTENT — the design's
    referenced columns must all be present in the batch's header — rather than
    trusting the (arbitrary, instrument-chosen) filename alone. Returns ``None``
    when the IR cannot be read; the caller then refuses the rename (fail-closed).
    """
    try:
        from asterism_step0.mapping_ir import parse_mapping_ir, referenced_columns
    except Exception:
        return None
    try:
        ir = parse_mapping_ir(mapping_ir_yaml)
    except Exception:
        return None
    matched = [tm for tm in ir.maps if Path(tm.source).name == source_name]
    cols: set[str] = set()
    for tm in matched or list(ir.maps):
        cols.update(referenced_columns(tm))
    return cols


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
    # GAL-A-40: the design-source rename below changes what materialization sees,
    # but the "取り込んだファイル" history must keep showing the file the instrument
    # actually produced — so provenance names are tracked in parallel, one per
    # batch entry, and used for ``mark_appended`` instead of the (possibly renamed)
    # ``canonical`` names.
    provenance_names: list[str] = []
    for name, content in batch:
        # Canonicalize the SAME way the design entrances do, so a batch dropped
        # under the instrument's original (non-ASCII) filename matches the
        # rml:source the design pinned.
        try:
            cname = _sanitize_tabular_name(name)
        except HTTPException as exc:
            raise AppendError(400, _error_text(exc.detail)) from exc
        if cname.lower().endswith(".xlsx"):
            # K6: an .xlsx batch appends as its derived CSV — but only a
            # SINGLE-sheet workbook, whose derived name (<stem>.csv) matches the
            # design-time conversion. A multi-sheet batch is ambiguous (which
            # sheet grows which source?), so it is refused explicitly.
            try:
                derived = await asyncio.to_thread(_expand_xlsx_bytes, cname, content)
            except HTTPException as exc:
                raise AppendError(422, _error_text(exc.detail)) from exc
            if len(derived) > 1:
                raise AppendError(
                    400,
                    "この Excel ファイルには複数のシートがあります。追記するファイルは"
                    "シートを 1 つにするか、CSV に変換してから追加してください",
                )
            cname, content = derived[0]
        provenance_names.append(cname)  # pre-rename: what the instrument actually named it
        if sources and cname not in sources:
            # GAL-A-40: an instrument names each export differently (dates,
            # run numbers, …) — S9 promises "the same device's files, as-is",
            # so a single-source design machine-resolves the name instead of
            # rejecting it. The safety check moves from the NAME to the
            # CONTENT: the batch's header must carry every column the design
            # reads before it is accepted under the pinned source name. A
            # multi-source design stays name-matched (ambiguous which source a
            # lone file continues) — unchanged from today.
            if len(sources) == 1:
                target = next(iter(sources))
                expected_cols = _expected_columns_for_single_source(
                    str(data["artifacts"].get("mapping.yaml") or ""), target
                )
                if expected_cols is not None:
                    header_cols = await asyncio.to_thread(
                        _batch_header_columns, content, dialected.get(target)
                    )
                    missing = sorted(expected_cols - header_cols)
                    if not missing:
                        cname = target
                    else:
                        raise AppendError(
                            400,
                            "このファイルは最初のファイルと列が違います"
                            f"(見つからない列: {'、'.join(missing)})",
                        )
            if cname not in sources:
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
        batch_files=provenance_names,
        source_files=all_files,
        triples_in_batch=triples_in_batch,
        appended_at=datetime.now(UTC).isoformat(),
        append_seq=append_seq,
        batch_id=batch_id,
    )
    # Re-check the shapes on the grown graph (ADR data-shape-checks.md): a feed
    # that starts clean can drift — a batch whose key is formatted differently
    # lands as dangling links, and the previous round's findings would otherwise
    # keep saying "all clear". Recomputed (not merged) so a fixed batch clears
    # stale advice. Advisory and best-effort: never fails an append.
    await _record_shape_findings(registry_root, client, dataset_id, live_graph, rml_ttl)
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


def _design_source_files(registry_root: Path, dataset_id: str | None) -> list[Path]:
    """The dataset's persisted design-time sources, or [] (never raises).

    Anything that wants to re-check a design against the DATA needs these; a
    missing / unreadable source must degrade to "cannot check", never to a 500.
    """
    if not dataset_id:
        return []
    try:
        return registry.list_source_files(registry_root, dataset_id)
    except Exception:
        logger.exception("listing design sources for %s failed (continuing)", dataset_id)
        return []


# Where a dataset remembers the meanings/units a HUMAN typed (kantan S6).
# Beside the bundle rather than inside the design: the design is regenerated by
# every AI round, and the whole point of this file is to outlive those rounds
# (ADR data-facts-invariant N6 — a fact a person asserted is not the model's to
# forget).
_DISPLAY_META_FILE = "display-meta.json"
_COLUMN_DECISIONS_FILE = "column-decisions.json"
_COLUMN_MEANINGS_FILE = "column-meanings.json"


def _display_meta_path(registry_root: Path, dataset_id: str) -> Path | None:
    """``<root>/<id>/display-meta.json``, or None for an unknown/unsafe id."""
    sdir = registry.source_dir(registry_root, dataset_id)
    return None if sdir is None else sdir.parent / _DISPLAY_META_FILE


def _source_column_names(
    paths: list[Path], rml_ttl: str | None
) -> dict[str, set[str]]:
    """Read exact current headers for decision reconciliation.

    A source absent from the result was not inspectable and must not cause a
    persisted decision to be discarded.
    """
    try:
        dialects = _dialected_sources(str(rml_ttl or ""))
    except _DialectReadError:
        dialects = {}
    columns: dict[str, set[str]] = {}
    for path in paths:
        if path.suffix.lower() not in _SAMPLEABLE_SUFFIXES:
            continue
        try:
            inspections, _ = inspect_source_set([path], dialects=dialects or None)
        except Exception:
            logger.warning("decision reconciliation: %s could not be read", path.name)
            continue
        for inspection in inspections:
            columns[inspection.name] = {
                column.name for column in getattr(inspection, "columns", []) or []
            }
    return columns


def _load_display_meta(registry_root: Path, dataset_id: str | None) -> list[dict]:
    """The human's meaning/unit corrections for this dataset (``[]``, never raises)."""
    if not dataset_id:
        return []
    path = _display_meta_path(registry_root, dataset_id)
    if path is None or not path.is_file():
        return []
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        logger.warning("display-meta for %s is unreadable (ignoring)", dataset_id)
        return []
    edits = data.get("edits") if isinstance(data, dict) else None
    return [e for e in edits or [] if isinstance(e, dict) and e.get("predicate")]


def _remember_display_meta(registry_root: Path, dataset_id: str, edits: list[dict]) -> None:
    """Record (upsert) the human's corrections. Best-effort — losing the memo
    must not fail the edit the user already sees applied."""
    path = _display_meta_path(registry_root, dataset_id)
    if path is None:
        return
    kept = {_display_meta_key(e): e for e in _load_display_meta(registry_root, dataset_id)}
    for edit in edits:
        key = _display_meta_key(edit)
        previous = kept.get(key, {})
        if key[2]:
            # Upgrade a pre-source memo to the now source-scoped identity so it
            # cannot keep matching the same predicate+column in every file.
            legacy_key = (key[0], key[1], "", key[3])
            previous = {**kept.pop(legacy_key, {}), **previous}
        kept[key] = {
            **previous,
            **{field: value for field, value in edit.items() if value is not None},
        }
    try:
        path.write_text(
            json.dumps({"edits": list(kept.values())}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        logger.exception("could not persist display-meta for %s (continuing)", dataset_id)


def _display_meta_key(edit: Mapping[str, object]) -> tuple[str, str, str, str]:
    return (
        str(edit.get("predicate") or ""),
        str(edit.get("map") or ""),
        str(edit.get("source") or ""),
        str(edit.get("column") or ""),
    )


def _column_decisions_path(registry_root: Path, dataset_id: str) -> Path | None:
    """``<root>/<id>/column-decisions.json``, or None for an unknown dataset."""
    sdir = registry.source_dir(registry_root, dataset_id)
    return None if sdir is None else sdir.parent / _COLUMN_DECISIONS_FILE


def _load_column_decisions(registry_root: Path, dataset_id: str | None) -> list[dict]:
    """The durable human include/exclude/own decisions for a dataset."""
    if not dataset_id:
        return []
    path = _column_decisions_path(registry_root, dataset_id)
    if path is None or not path.is_file():
        return []
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        logger.warning("column decisions for %s are unreadable (ignoring)", dataset_id)
        return []
    decisions = data.get("decisions") if isinstance(data, dict) else None
    return [
        d
        for d in decisions or []
        if isinstance(d, dict)
        and d.get("source")
        and d.get("column")
        and d.get("action") in COLUMN_DECISION_ACTIONS
    ]


def _column_decision_key(decision: Mapping[str, object]) -> tuple[str, str]:
    return str(decision.get("source") or ""), str(decision.get("column") or "")


def _merge_column_decisions(
    existing: list[dict], incoming: list[dict]
) -> list[dict]:
    """Upsert by physical source column; the latest human statement wins."""
    merged = {_column_decision_key(d): d for d in existing}
    for decision in incoming:
        merged[_column_decision_key(decision)] = decision
    return list(merged.values())


def _remember_column_decisions(
    registry_root: Path, dataset_id: str, decisions: list[dict]
) -> None:
    """Persist the complete upserted decision set after a successful edit."""
    path = _column_decisions_path(registry_root, dataset_id)
    if path is None:
        raise ValueError(f"dataset {dataset_id!r} not found")
    path.write_text(
        json.dumps({"decisions": decisions}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _column_meanings_path(registry_root: Path, dataset_id: str) -> Path | None:
    """``<root>/<id>/column-meanings.json``, or None for an unknown dataset."""
    sdir = registry.source_dir(registry_root, dataset_id)
    return None if sdir is None else sdir.parent / _COLUMN_MEANINGS_FILE


def _load_column_meanings(registry_root: Path, dataset_id: str | None) -> list[dict]:
    """The settled ``(source, column)`` meanings for a dataset (``[]``, never raises)."""
    if not dataset_id:
        return []
    path = _column_meanings_path(registry_root, dataset_id)
    if path is None or not path.is_file():
        return []
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        logger.warning("column meanings for %s are unreadable (ignoring)", dataset_id)
        return []
    meanings = data.get("meanings") if isinstance(data, dict) else None
    return [
        m
        for m in meanings or []
        if isinstance(m, dict) and m.get("source") and m.get("column")
    ]


def _column_meaning_key(meaning: Mapping[str, object]) -> tuple[str, str]:
    return str(meaning.get("source") or ""), str(meaning.get("column") or "")


def _merge_column_meanings(existing: list[dict], incoming: list[dict]) -> list[dict]:
    """Upsert by physical source column; the latest statement about a field wins.

    A field the caller did not send is left as it was (a screen that only edits
    the unit must not erase the meaning); an EMPTY string clears it — the person
    saying "this was wrong and I have nothing better", the same convention
    ``apply_display_meta`` uses.
    """
    merged = {_column_meaning_key(m): dict(m) for m in existing}
    for meaning in incoming:
        key = _column_meaning_key(meaning)
        row = dict(merged.get(key) or {})
        row.update({"source": key[0], "column": key[1]})
        for field_name in ("label", "unit"):
            if field_name not in meaning:
                continue
            value = meaning.get(field_name)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                row[field_name] = text
            else:
                row.pop(field_name, None)
        merged[key] = row
    return [m for m in merged.values() if m.get("label") or m.get("unit")]


def _remember_column_meanings(
    registry_root: Path, dataset_id: str, meanings: list[dict]
) -> None:
    """Persist the complete upserted meaning set after a successful edit."""
    path = _column_meanings_path(registry_root, dataset_id)
    if path is None:
        raise ValueError(f"dataset {dataset_id!r} not found")
    path.write_text(
        json.dumps({"meanings": meanings}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


_UNMAPPED_ADVISORY = re.compile(
    r"^source (?P<source>\S+) has (?P<count>\d+) column\(s\) the mapping never uses: "
    r"(?P<columns>.+?)\. If a column carries meaning"
)


def _columns_are_confirmed_excluded(
    shown: str, count: int, candidates: set[str]
) -> bool:
    """Parse a comma-joined advisory against exact headers, including commas."""

    def match(offset: int, remaining: int, used: frozenset[str]) -> bool:
        if remaining == 0:
            return offset == len(shown)
        for column in candidates - used:
            token = column if remaining == 1 else f"{column}, "
            if shown.startswith(token, offset) and match(
                offset + len(token), remaining - 1, used | {column}
            ):
                return True
        return False

    return match(0, count, frozenset())


def _without_confirmed_exclusion_advisories(
    advisories: list[str], decisions: list[dict]
) -> list[str]:
    """Drop only fully identified, fully excluded unmapped-column notices.

    The source validator deliberately reports at most ten column names.  Do not
    hide a notice with an ellipsis, a count mismatch, or an unfamiliar shape:
    any of those might contain a newly unreviewed column.
    """
    excluded = {
        (str(d.get("source")), str(d.get("column")))
        for d in decisions
        if d.get("action") == "exclude"
    }
    kept: list[str] = []
    for advisory in advisories:
        match = _UNMAPPED_ADVISORY.match(advisory)
        if match is None:
            kept.append(advisory)
            continue
        shown = match.group("columns")
        source = match.group("source")
        count = int(match.group("count"))
        candidates = {
            column for candidate_source, column in excluded if candidate_source == source
        }
        if (
            "…" in shown
            or not _columns_are_confirmed_excluded(shown, count, candidates)
        ):
            kept.append(advisory)
    return kept


def _artifacts_from_document(
    document_md: str, dataset_name: str, source_dir: Path | None
) -> tuple[dict[str, str | None], list[str]]:
    """Re-project the stored artifacts from a design document (no LLM, no writes).

    The same deterministic split ``/api/materialize`` performs; used by the
    display-meta edit, where the §9 change is display-only and the compiled RML
    is expected to come back byte-identical.
    """
    mat = materialize_schema(
        document_md,
        ".",
        dataset_name,
        write=False,
        source_dir=source_dir if source_dir is not None and source_dir.is_dir() else None,
    )
    artifacts: dict[str, str | None] = {
        "diagram.md": mat.diagram_md,
        "model.yaml": mat.rdf_config_model,
        "mie.yaml": mat.mie_yaml,
        "ingester.py": mat.ingester_py,
        "mapping.rml.ttl": mat.rml_ttl,
        "mapping.yaml": mat.mapping_ir_yaml,
    }
    return artifacts, list(mat.warnings)


def _refine_oracle(registry_root: Path, dataset_id: str | None, staging_id: str | None) -> str:
    """The closed-menu appendix for a MANUAL refine round, or "" when no source
    is known. Best-effort: any failure means an ungrounded round (today's
    behaviour), never a failed request. Dialects are re-detected from the files
    the same way the loop does, so the column list matches what ingest reads."""
    try:
        paths: list[Path] = []
        if dataset_id:
            with contextlib.suppress(Exception):
                paths = registry.list_source_files(registry_root, dataset_id)
        if not paths and staging_id:
            with contextlib.suppress(staging.StagingNotFound, ValueError):
                _sdir, paths = staging.load(registry_root, staging_id)
        if not paths:
            return ""
        base = paths[0].parent
        return design_loop.build_oracle(
            base, paths, dialects=design_loop._detect_source_dialects(paths)
        )
    except Exception:
        return ""


def _design_checks_at_materialize(
    registry_root: Path,
    dataset_id: str | None,
    rml_ttl: str | None,
    *,
    source_dir: Path | None = None,
    column_decisions: list[dict] | None = None,
) -> tuple[list[str], list[str], list[dict]]:
    """Design checks for the materialize response (NEVER raises).

    Returns ``(issues, advisories, duplicate_columns)`` — the first two are lists
    with DIFFERENT strengths, kept apart because the caller must treat them
    differently:

    ``issues`` — the design is wrong and will not do what it says: a column the
    source does not have, a Tier-0 function called with the wrong parameters.
    Same :func:`validate_rml_design` the ingest gate runs, but its
    :class:`RmlValidationError` is caught and returned so the problem surfaces at
    materialize (where the one-click "ask AI to fix" lives) without failing the
    save. NEEDS the dataset's persisted source CSVs, so it is empty until a
    source is attached.

    ``advisories`` — the design is valid but weak: entities that never link to
    each other (a measurement that cannot be traced to its material answers no
    cross-entity question), columns left unmapped. These need NO source — they
    are read from the mapping alone — which is exactly why they must not sit
    behind the source/``dataset_id`` guard. They did, until 2026-07-24: the
    wizard mints its dataset IN the first materialize call, so that call had no
    ``dataset_id``, the whole function returned early, and a real dogfood
    dataset (ZEM) reached publication with its two entities disconnected and
    the user never saw a word about it. When a source IS available the
    connectivity advisory additionally names the join-key candidates, turning a
    diagnosis into a work order.

    ``duplicate_columns`` — the machine-readable half of ONE of those advisories.
    "Which of these two kinds should keep this column" is a design judgement the
    rows could not settle, so it is a person's to make (ADR
    column-ownership-and-growth G1, ADR kantan K2) — and a person needs the
    candidates, not an English paragraph. Same pass as the sentence
    (:func:`asterism.rml_validate.duplicate_column_findings`), so the choice on
    screen can never disagree with the advisory beside it.

    ``source_dir`` is the design-time source when the dataset has none attached
    yet (the wizard's staged copy, ADR source-staging.md); the dataset's own
    persisted source wins when it exists.

    Every list is best-effort: any unexpected error degrades to "no advice",
    never a 500.
    """
    if not (rml_ttl or "").strip():
        return [], [], []
    prepared = substrate.substitute_run_id(rml_ttl)
    source_paths: list[Path] = []
    if dataset_id:
        try:
            source_paths = registry.list_source_files(registry_root, dataset_id)
        except Exception:
            source_paths = []
    if source_paths:
        source_dir = source_paths[0].parent
    duplicate_columns: list[dict] = []
    try:
        # Review notes (unmapped columns) are human-judgement items: shown so the
        # person can weigh them / include them in a fix request, but NOT fed to
        # the automatic corrective loop (which would over-fix noise columns).
        advisories = substrate.design_advisories(
            prepared, source_dir
        ) + substrate.design_review_notes(prepared, source_dir)
        duplicate_columns = substrate.duplicate_column_findings(prepared, source_dir)
    except Exception:  # advisory only
        logger.exception("design advisories at materialize failed (continuing)")
        advisories = []
    decisions = (
        column_decisions
        if column_decisions is not None
        else _load_column_decisions(registry_root, dataset_id)
    )
    advisories = _without_confirmed_exclusion_advisories(advisories, decisions)
    if source_dir is None:
        return [], advisories, duplicate_columns
    try:
        # Validate the run-id-substituted form so the runtime-only {__run_id__}
        # placeholder is never flagged (matches the ingest gate exactly).
        substrate.validate_rml_design(prepared, source_dir)
        return [], advisories, duplicate_columns
    except substrate.RmlValidationError as exc:
        return list(exc.issues), advisories, duplicate_columns
    except Exception:  # a check failure must never break materialize
        logger.exception("advisory design validation at materialize failed (continuing)")
        return [], advisories, duplicate_columns


async def _record_shape_findings(
    registry_root: Path,
    client: OxigraphClient,
    dataset_id: str,
    graph_iri: str,
    rml_ttl: str,
) -> list[str]:
    """Check the INGESTED graph against the shapes its own RML declares, and
    persist the findings on the dataset (ADR ``data-shape-checks.md``).

    Runs once per ingest — not per page view — because the answer only changes
    when the data does, and a browse of the catalog must stay instant. The
    findings ride the existing ``advisories`` channel, so no new UI surface
    exists to keep in sync.

    Best-effort in every direction: no RML (a document dataset), an unparseable
    mapping, a store hiccup — all degrade to "no findings". A shape check must
    never be the reason an otherwise-successful ingest reports failure.
    """
    if not (rml_ttl or "").strip():
        return []
    try:
        compiled = await asyncio.to_thread(shapes.compile_shapes, rml_ttl)
        if not compiled:
            return []
        findings = await shapes.run_shape_checks(
            compiled, graph_iri, client.sparql_select
        )
        messages = [f.message for f in findings]
        registry.record_shape_findings(registry_root, dataset_id, messages)
        return messages
    except Exception:
        logger.exception("shape checks after ingest failed (continuing)")
        return []


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
            # The detail reaches a human verbatim (the catalog shows the raw
            # server message), so it says what the person can do about it. The
            # operational reason — env var name, fail-closed posture — is a log
            # line for whoever runs the box, not a sentence for the scientist
            # who just pressed 公開する.
            logger.warning(
                "write route refused: ASTERISM_API_TOKEN is unset "
                "(fail-closed against anonymous writes / raw SPARQL)"
            )
            raise HTTPException(
                503,
                "利用許可コード (管理者が設定する API token) が未設定のため、"
                "この操作はできません",
            )
        if not _write_credential_ok(cfg, authorization, x_asterism_token):
            raise HTTPException(401, "利用許可コードが違います")

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

    def _graph_display(graphs: list[str], lang: str) -> dict[str, dict[str, str]]:
        """Resolve source graph IRIs to something a reader recognises.

        ``…/graph/canonical/<dataset_id>[/v<n>]`` → the dataset's own name (so
        the 出どころ column never shows ``dataset-9422ba7c``), and
        ``…/graph/ontology/<id>`` → 「共通の言葉」. Unresolvable graphs are simply
        omitted; the renderer then drops the column rather than showing an
        internal id. Resolution lives here so ``describe.py`` stays free of any
        registry dependency.
        """
        names = {
            str(m.get("id")): str(m.get("name") or "")
            for m in registry.list_datasets(cfg.registry_root)
        }
        info: dict[str, dict[str, str]] = {}
        for graph in graphs:
            base = re.sub(r"/v\d+$", "", graph)
            if base.startswith(substrate.CANONICAL_GRAPH_BASE):
                dataset_id = base[len(substrate.CANONICAL_GRAPH_BASE) :]
                name = names.get(dataset_id)
                if name:
                    info[graph] = {"name": name, "dataset_id": dataset_id}
            elif base.startswith(substrate.ONTOLOGY_GRAPH_BASE):
                info[graph] = {"name": describe_mod.shared_terms_label(lang)}
        return info

    @app.get("/describe")
    async def describe_iri(
        iri: str,
        format: str | None = None,
        lang: str | None = None,
        accept: str | None = Header(default=None),
        accept_language: str | None = Header(default=None),
    ) -> Response:
        """Dereference one IRI against the PUBLISHED (canonical + ontology)
        scope — ADR instance-iri-base.md phase 2. Content-negotiated: Turtle
        for machines (Accept: text/turtle / ?format=ttl), HTML for browsers.
        Tokenless by design: a bounded read of already-published data (same
        exposure class as the typed tools, narrower than the raw-SPARQL
        escape) — see the module docstring of :mod:`asterism_api.describe`.

        For a browser every failure answers in HTML too: this is where someone
        who was handed a citation lands, and a raw JSON ``detail`` is a dead end
        for them (ADR kantan-mode-two-tier-ux.md K11)."""
        iri = iri.strip()
        page_lang = describe_mod.pick_language(lang, accept_language)
        wants_turtle = format in ("ttl", "turtle", "nt") or (
            format is None
            and accept is not None
            and ("text/turtle" in accept or "application/n-triples" in accept)
        )
        if not describe_mod.valid_iri(iri):
            if wants_turtle:
                raise HTTPException(400, "iri must be an absolute http(s) IRI")
            return HTMLResponse(
                describe_mod.render_bad_request(iri, lang=page_lang), status_code=400
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
            if wants_turtle:
                raise HTTPException(502, "upstream SPARQL error") from exc
            return HTMLResponse(
                describe_mod.render_upstream_error(lang=page_lang), status_code=502
            )
        if data is None:
            graphs = await substrate.canonical_graphs(client)
            return HTMLResponse(
                describe_mod.render_not_found(iri, len(graphs), lang=page_lang),
                status_code=404,
            )
        return HTMLResponse(
            describe_mod.render_html(
                iri,
                data,
                lang=page_lang,
                graph_info=_graph_display(list(data["graphs"]), page_lang),
                iri_base=cfg.iri_base,
            )
        )

    @app.get("/api/instance")
    async def instance_info(
        authorization: str | None = Header(default=None),
        x_asterism_token: str | None = Header(default=None),
    ) -> dict[str, object]:
        """Public identity of this install (ADR instance-iri-base.md): where new
        designs mint their namespaces. Not a secret — the base is embedded in
        every minted IRI — so it is readable without the write token; the UI
        settings surface shows it and flags the unconfigured default."""
        # Where this caller stands with the write gate, so the settings UI can
        # ask for a token ONLY when pasting one would actually change something:
        #   closed         — no server-side token: writes are off for everyone
        #   authorized     — this request already carries it (desktop injects it
        #                    on loopback; production caddy injects it after the
        #                    session gate — in both, a pasted token is discarded)
        #   token_required — protected, and this caller has no valid token yet
        if cfg.api_token is None:
            write_gate = "closed"
        elif _write_credential_ok(cfg, authorization, x_asterism_token):
            write_gate = "authorized"
        else:
            write_gate = "token_required"
        return {
            "iri_base": cfg.iri_base,
            "iri_base_configured": cfg.iri_base != DEFAULT_IRI_BASE,
            # Which build is running, when the desktop shell started this backend
            # (null on a server/web install — see Settings.app_version).
            "app_version": cfg.app_version,
            "desktop": cfg.app_version is not None,
            "write_gate": write_gate,
        }

    @app.get("/api/desktop/update-check")
    async def desktop_update_check() -> dict[str, object]:
        """Desktop only: is a newer release out? Reads the same updater manifest
        the shell installs from and compares versions — it never downloads or
        installs anything (that stays with the native updater: auto at startup
        and the "アップデートを確認…" menu item). 404 on a server/web install,
        where there is no bundle to update."""
        if cfg.app_version is None:
            raise HTTPException(404, "not a desktop install")
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as http:
                res = await http.get(cfg.updater_feed)
                res.raise_for_status()
                feed = res.json()
        except (httpx.HTTPError, ValueError) as exc:
            # Offline / rate-limited / malformed feed: the caller shows "check
            # failed", never a wrong "you are up to date".
            raise HTTPException(502, "update feed unreachable") from exc
        latest = str(feed.get("version") or "").strip().lstrip("v")
        if not latest:
            raise HTTPException(502, "update feed has no version")
        return {
            "current": cfg.app_version,
            "latest": latest,
            "update_available": _version_tuple(latest) > _version_tuple(cfg.app_version),
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

    @app.post("/api/staging", dependencies=_write_auth)
    async def create_staging(
        files: list[UploadFile] = File(..., description="Source file(s) to stage for design"),
    ) -> dict[str, object]:
        """Give the design-time source a server-side home the moment it is
        dropped (ADR source-staging.md). Returns ``staging_id`` + the canonical
        source names; every later design call takes the id instead of the files,
        and S5's attach copies from here. Write-gated like every other write to
        the registry; a bare instance with the gate closed answers 503 and the
        client keeps its own copy (the legacy path is unchanged)."""
        if not files:
            raise HTTPException(400, "no files uploaded")
        staging.sweep(cfg.registry_root)
        sid = staging.new_id()
        sdir = staging.dir_for(cfg.registry_root, sid, create=True)
        try:
            # raw/ = as received (attach converts it like a fresh upload);
            # root = the canonical design files (xlsx expanded, names slugged).
            for upload in files:
                if upload.filename is None:
                    raise HTTPException(400, "missing filename")
                await _save_upload(upload, sdir / "raw" / _sanitize_tabular_name(upload.filename))
            replay = _uploads_from_dir(sdir / "raw")
            sheets: dict[str, dict[str, str]] = {}
            try:
                paths = await _save_tabular_uploads(replay, sdir, sheets)
            finally:
                for u in replay:
                    await u.close()
            meta = staging.write_meta(sdir, [p.name for p in paths])
            if sheets:
                # Which derived table came from which worksheet — the record has
                # to remember it, because everything downstream sees only the
                # slugged CSV names (K6). Kept on the record, not recomputed:
                # re-opening the workbook to answer would read it a second time.
                meta["sheets"] = sheets
                _write_staging_meta(sdir, meta)
        except Exception:
            shutil.rmtree(sdir, ignore_errors=True)
            raise
        return {
            "staging_id": sid,
            "sources": meta["sources"],
            "sheets": meta.get("sheets") or {},
            "expires_at": staging.expires_at(sdir),
        }

    @app.get("/api/staging/{staging_id}")
    async def get_staging(staging_id: str) -> dict[str, object]:
        """Is this record still live, and what does it hold? The client asks on
        reload before trusting a remembered id."""
        try:
            sdir, paths = staging.load(cfg.registry_root, staging_id)
        except staging.StagingNotFound as exc:
            raise HTTPException(404, "staging not found") from exc
        return {
            "staging_id": staging_id,
            "sources": [p.name for p in paths],
            "sheets": _staging_meta(sdir).get("sheets") or {},
            "expires_at": staging.expires_at(sdir),
        }

    @app.post("/api/staging/{staging_id}/sources", dependencies=_write_auth)
    async def select_staging_sources(
        staging_id: str, body: StagingSourcesBody
    ) -> dict[str, object]:
        """Narrow a staged record to the tables the human actually meant (K6).

        One Excel workbook becomes one table per sheet, and a real workbook
        routinely carries a chart sheet, a notes sheet and a legend — all of which
        were handed to the AI as data. This is the answer to "which sheet do you
        want to use?": the chosen names become the record's sources, so every
        later design call (inspect / skeleton / continue) reads only those, and
        the attach persists only those. Nothing is deleted — an unchosen table
        stays on disk and can be chosen again until the record expires.
        """
        try:
            sdir, _paths = staging.load(cfg.registry_root, staging_id)
        except staging.StagingNotFound as exc:
            raise HTTPException(404, "staging not found") from exc
        meta = _staging_meta(sdir)
        # The full set is what the record was created with; "sources" may already
        # be a narrowed selection, so a re-pick must widen back from disk.
        known = {p.name for p in sdir.iterdir() if p.is_file() and p.name != "meta.json"}
        chosen = [n for n in dict.fromkeys(body.sources) if n in known]
        if not chosen:
            raise HTTPException(
                422, "none of the requested sources belong to this staged record"
            )
        meta["sources"] = chosen
        _write_staging_meta(sdir, meta)
        return {"staging_id": staging_id, "sources": chosen}

    @app.delete("/api/staging/{staging_id}", dependencies=_write_auth)
    async def delete_staging(staging_id: str) -> dict[str, object]:
        """Forget a record (a fresh start). Idempotent."""
        return {"deleted": staging.delete(cfg.registry_root, staging_id)}

    # ------------------------------------------------------------------
    # Single-user on-disk appdata (ADR app-data-on-disk.md): Ask
    # chat threads + app settings, for asterism-local only. Everything but
    # GET /api/appdata/info 404s in the shared/hosted api (cfg.single_user
    # is False there, and appdata_root is None), so this is a strict
    # addition — the info probe is what lets the SPA branch on it.

    def _reject_if_content_length_exceeds(request: Request, limit: int) -> None:
        """Cheap pre-check before reading the body into memory: when the
        client sends ``Content-Length`` and it already exceeds the limit,
        413 immediately instead of buffering megabytes just to throw them
        away. Missing/unparsable header falls through — the caller still
        enforces the limit on the serialized body afterwards."""
        raw = request.headers.get("content-length")
        if raw is None:
            return
        try:
            declared = int(raw)
        except ValueError:
            return
        if declared > limit:
            raise HTTPException(413, f"body is {declared} bytes, over the {limit} limit")

    @app.get("/api/appdata/info")
    async def appdata_info() -> dict[str, object]:
        """Always 200 — the UI's only signal for "do I have a disk home?".
        ``home`` is the appdata root's parent (the data home), not the
        appdata dir itself, to match what a human recognizes from the app."""
        if not cfg.single_user or cfg.appdata_root is None:
            return {"single_user": False, "home": None}
        return {"single_user": True, "home": str(cfg.appdata_root.parent)}

    def _appdata_root_or_404() -> Path:
        if not cfg.single_user or cfg.appdata_root is None:
            raise HTTPException(404, "appdata is only available in single-user mode")
        return cfg.appdata_root

    @app.get("/api/appdata/ask/threads")
    async def appdata_list_threads() -> dict[str, object]:
        root = _appdata_root_or_404()
        return {"threads": appdata.read_threads(root)}

    @app.put("/api/appdata/ask/threads/{thread_id}", dependencies=_write_auth)
    async def appdata_put_thread(thread_id: str, request: Request) -> dict[str, object]:
        root = _appdata_root_or_404()
        _reject_if_content_length_exceeds(request, appdata.MAX_THREAD_BYTES)
        try:
            payload = await request.json()
        except ValueError as exc:
            raise HTTPException(400, "body must be JSON") from exc
        if not isinstance(payload, dict):
            raise HTTPException(400, "thread body must be a JSON object")
        try:
            appdata.write_thread(root, thread_id, payload)
        except appdata.InvalidThreadId as exc:
            raise HTTPException(400, str(exc)) from exc
        except (appdata.ThreadTooLarge, appdata.TooManyThreads) as exc:
            raise HTTPException(413, str(exc)) from exc
        return {"saved": True}

    @app.delete("/api/appdata/ask/threads/{thread_id}", dependencies=_write_auth)
    async def appdata_delete_thread(thread_id: str) -> dict[str, object]:
        root = _appdata_root_or_404()
        try:
            deleted = appdata.delete_thread(root, thread_id)
        except appdata.InvalidThreadId as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"deleted": deleted}

    # design-consult-chat.md D2: the same mechanism as the Ask threads above,
    # under the `consult` namespace, so a design-consult conversation persists
    # to disk in single-user mode exactly like an Ask thread does.
    @app.get("/api/appdata/consult/threads")
    async def appdata_list_consult_threads() -> dict[str, object]:
        root = _appdata_root_or_404()
        return {"threads": appdata.read_threads(root, namespace=appdata.CONSULT_DIRNAME)}

    @app.put("/api/appdata/consult/threads/{thread_id}", dependencies=_write_auth)
    async def appdata_put_consult_thread(thread_id: str, request: Request) -> dict[str, object]:
        root = _appdata_root_or_404()
        _reject_if_content_length_exceeds(request, appdata.MAX_THREAD_BYTES)
        try:
            payload = await request.json()
        except ValueError as exc:
            raise HTTPException(400, "body must be JSON") from exc
        if not isinstance(payload, dict):
            raise HTTPException(400, "thread body must be a JSON object")
        try:
            appdata.write_thread(root, thread_id, payload, namespace=appdata.CONSULT_DIRNAME)
        except appdata.InvalidThreadId as exc:
            raise HTTPException(400, str(exc)) from exc
        except (appdata.ThreadTooLarge, appdata.TooManyThreads) as exc:
            raise HTTPException(413, str(exc)) from exc
        return {"saved": True}

    @app.delete("/api/appdata/consult/threads/{thread_id}", dependencies=_write_auth)
    async def appdata_delete_consult_thread(thread_id: str) -> dict[str, object]:
        root = _appdata_root_or_404()
        try:
            deleted = appdata.delete_thread(root, thread_id, namespace=appdata.CONSULT_DIRNAME)
        except appdata.InvalidThreadId as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"deleted": deleted}

    @app.get("/api/appdata/settings")
    async def appdata_get_settings() -> dict[str, object]:
        root = _appdata_root_or_404()
        return {"settings": appdata.read_settings(root)}

    @app.put("/api/appdata/settings", dependencies=_write_auth)
    async def appdata_put_settings(request: Request) -> dict[str, object]:
        root = _appdata_root_or_404()
        _reject_if_content_length_exceeds(request, appdata.MAX_SETTINGS_BYTES)
        try:
            payload = await request.json()
        except ValueError as exc:
            raise HTTPException(400, "body must be JSON") from exc
        if not isinstance(payload, dict):
            raise HTTPException(400, "settings body must be a JSON object")
        try:
            appdata.write_settings(root, payload)
        except appdata.SettingsTooLarge as exc:
            raise HTTPException(413, str(exc)) from exc
        return {"saved": True}

    @app.post("/api/inspect")
    async def inspect_csvs(
        files: list[UploadFile] = File(
            default=[], description="Source file(s) to inspect (CSV or JSON)"),
        staging_id: str = Form(
            default="",
            description="A staged source (POST /api/staging) to read INSTEAD of files.",
        ),
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
        sheets: dict[str, dict[str, str]] = {}
        work, paths, owned = await _design_sources(
            cfg.registry_root,
            files,
            staging_id or None,
            prefix="asterism-inspect-",
            sheets_out=sheets,
        )
        try:
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
            # {source: [{name, line, text, named}]} for a source with a
            # still-dropped preamble the wizard's "record it" answer would
            # broadcast — only what a per-line rename screen (S2) needs, empty
            # for a clean source and absent entirely when no source has one.
            # Computed here (inside the try, before the temp dir is removed
            # below) because it re-reads each source's own preamble lines.
            preamble_header = _preamble_header_json(inspections, paths)
        finally:
            if owned:
                shutil.rmtree(work, ignore_errors=True)
        headers = {
            "X-Asterism-Source-Names": ",".join(p.name for p in paths),
            # Structured detected dialects for the wizard "read settings" panel
            # (ADR source-dialect.md); non-default sources only, clean set → {}.
            "X-Asterism-Dialects": _dialects_header_json(inspections),
            # Real example values per column, so the "read check" screen has
            # something to check for the formats the browser cannot parse
            # (.xlsx / .json) — KZ-A-08.
            "X-Asterism-Samples": _samples_header_json(inspections),
            "X-Asterism-Preamble": preamble_header,
        }
        if sheets:
            # {derived csv: {from, sheet}} — only for workbooks that produced more
            # than one table, i.e. exactly when K6 says to ask which one to use.
            # Same budget as the samples header, and for the same reason: a
            # workbook with dozens of long (escaped, so 6x) sheet titles must
            # degrade to "no sheet chooser", never to a response the server
            # cannot send at all.
            encoded = json.dumps(sheets, separators=(",", ":"))
            if len(encoded) <= _SAMPLES_HEADER_BUDGET:
                headers["X-Asterism-Sheets"] = encoded
        return Response(content=markdown, media_type="text/markdown", headers=headers)

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
            # Fall back to the operator's shared key. For openai-compatible the
            # shared key is PINNED to one endpoint, so it must never be sent to a
            # different api_base. When the request explicitly names another
            # endpoint (e.g. a local LM Studio / Ollama at localhost), keep the
            # user's endpoint and DON'T borrow the shared key — otherwise we would
            # silently query the pinned provider (Sakura etc.) and return the
            # wrong model list. When the request omits api_base, adopt the pinned
            # one so the picker still works key-lessly against the shared endpoint.
            resolved_key, pinned = server_keys.resolve(provider, cfg.registry_root)
            request_base = (api_base or "").strip()
            if pinned and request_base and request_base.rstrip("/") != pinned.rstrip("/"):
                pass  # user named a different endpoint → use it as-is, no shared key
            else:
                api_key = resolved_key
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
        key was set via env or ``POST /api/llm/server-keys`` (opt-in).

        ``default_models`` rides along (non-secret): with it a browser whose model
        registry is empty can use a shared key straight away, instead of being
        asked to pick a provider and type a model id before anything works."""
        return JSONResponse(
            {
                "providers": server_keys.configured_providers(cfg.registry_root),
                "default_models": _default_llm_models(),
            }
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
            default=[], description="Source file(s) to model (CSV or JSON)"
        ),
        staging_id: str = Form(
            default="",
            description="A staged source (POST /api/staging) to read INSTEAD of files.",
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
        # Parse (and 400/422 on) the cap header + dialect overrides BEFORE the upload
        # dir exists so a bad value cannot leak a temp dir.
        max_tokens = _llm_max_tokens(x_llm_max_tokens)
        dialect_overrides = _parse_dialect_overrides(dialects)

        work, paths, owned = await _design_sources(
            cfg.registry_root, files, staging_id or None, prefix="asterism-propose-"
        )
        tmpdir = str(work)

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
                if owned:
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

    @app.post("/api/design/column-meanings")
    async def design_column_meanings(
        files: list[UploadFile] = File(
            default=[], description="Source file(s) to read (CSV or JSON)"
        ),
        staging_id: str = Form(
            default="",
            description="A staged source (POST /api/staging) to read INSTEAD of files.",
        ),
        domain: str = Form(default="", description="Domain hint (Markdown). Optional."),
        language: str = Form(
            default="", description="Output language for the meanings (e.g. 'ja')."
        ),
        dialects: str = Form(
            default="",
            description="Per-source read-dialect overrides as JSON (ADR source-dialect.md).",
        ),
        x_api_key: str | None = Header(default=None),
        x_llm_provider: str | None = Header(default=None),
        x_llm_model: str | None = Header(default=None),
        x_llm_api_base: str | None = Header(default=None),
        x_llm_max_tokens: str | None = Header(default=None),
    ) -> JSONResponse:
        """Stage 0 of a staged design: what does each COLUMN mean?

        ADR meaning-before-identity. Runs BEFORE the skeleton and needs none of
        it: the meaning and the unit of a column are decided by the data, and
        they are the same whatever design is later built on them. Returns a
        job_id immediately; the SSE done payload carries
        ``{meanings: [{source, column, label?, unit?}]}``. The person corrects
        those, and they ride /api/propose/continue as ``column_meanings`` (and
        are stored per dataset once one exists).

        No dataset is read or written, so no write-auth gate — the same posture
        as /api/propose and /api/design/consult. The API key is used only for
        this run and never persisted (D7).
        """
        max_tokens = _llm_max_tokens(x_llm_max_tokens)
        dialect_overrides = _parse_dialect_overrides(dialects)

        work, paths, owned = await _design_sources(
            cfg.registry_root, files, staging_id or None, prefix="asterism-meanings-"
        )
        tmpdir = str(work)

        provider, model, api_base, key = _llm_coords(
            x_api_key, x_llm_provider, x_llm_model, x_llm_api_base, cfg.registry_root
        )
        llm = _resolve_llm(provider, model, api_base, key, max_tokens=max_tokens)

        async def meanings_job(
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
            effective = await asyncio.to_thread(
                _effective_dialects, list(paths), dialect_overrides
            )
            emit(phase="meanings", message="項目の意味を読み取り中")
            try:
                result = await asyncio.to_thread(
                    propose_column_meanings,
                    list(paths),
                    domain,
                    llm=llm,
                    language=language or None,
                    dialects=effective,
                )
                _record_llm_usage(cfg.registry_root, "propose.meanings", provider, llm, model)
            finally:
                if owned:
                    shutil.rmtree(tmpdir, ignore_errors=True)
            return {
                "meanings": result.meanings,
                "rejected": result.rejected,
                "source_files": [p.name for p in paths],
            }

        jobs: JobManager = app.state.jobs
        job_id = jobs.start_coro(meanings_job)
        return JSONResponse({"job_id": job_id}, status_code=202)

    @app.post("/api/propose/skeleton")
    async def propose_skeleton_endpoint(
        files: list[UploadFile] = File(
            default=[], description="Source file(s) to model (CSV or JSON)"
        ),
        staging_id: str = Form(
            default="",
            description="A staged source (POST /api/staging) to read INSTEAD of files.",
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
        max_tokens = _llm_max_tokens(x_llm_max_tokens)
        dialect_overrides = _parse_dialect_overrides(dialects)

        work, paths, owned = await _design_sources(
            cfg.registry_root, files, staging_id or None, prefix="asterism-skeleton-"
        )
        tmpdir = str(work)

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
            # Detection + the human's corrections, field by field. The overrides alone
            # are NOT the read rules: they carry only what the person edited, so
            # passing them raw made this stage read a cp932 source as utf-8-sig.
            effective = await asyncio.to_thread(
                _effective_dialects, list(paths), dialect_overrides
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
                    dialects=effective,
                    iri_base=cfg.iri_base,
                )
                _record_llm_usage(cfg.registry_root, "propose", provider, llm, model)
                # Deterministic evidence for the human gate (LLM-free): key
                # uniqueness / collisions / real ID previews / fix candidates,
                # computed against the SAME dialect-read sources. Best-effort —
                # a failure here must never cost the (paid) skeleton itself.
                skeleton = result.skeleton
                try:
                    annotations = await asyncio.to_thread(
                        annotate_skeleton,
                        skeleton,
                        list(paths),
                        dialects=effective,
                        iri_base=cfg.iri_base,
                    )
                    # Before the human ever sees a "measurement-only key"
                    # caution: the same evidence that would raise it already
                    # proves a safe alternative when one exists — swap it in
                    # deterministically and re-annotate so uniqueness / ID
                    # previews / candidates all reflect the NEW key. Never
                    # applied to a human-edited skeleton (that only happens on
                    # /api/propose/skeleton/validate, which never calls this).
                    skeleton, key_fixes = await asyncio.to_thread(
                        apply_key_safety_fix, skeleton, annotations
                    )
                    if key_fixes:
                        annotations = await asyncio.to_thread(
                            annotate_skeleton,
                            skeleton,
                            list(paths),
                            dialects=effective,
                            iri_base=cfg.iri_base,
                        )
                        for name, record in key_fixes.items():
                            map_ann = annotations.get("maps", {}).get(name)
                            if isinstance(map_ann, dict):
                                map_ann["applied_key_fix"] = record
                except Exception as exc:  # pragma: no cover — defensive
                    logger.warning("skeleton annotation failed: %s", exc)
                    # A key swap with no evidence to show for it is worse than
                    # no swap: if re-annotation (pass 2) failed, `skeleton` may
                    # already hold the SWAPPED key while `annotations` (and the
                    # `applied_key_fix` record inside it) is about to be
                    # dropped below. Never ship an unexplained key change —
                    # fall back to the AI's original skeleton.
                    skeleton = result.skeleton
                    annotations = None
            finally:
                if owned:
                    shutil.rmtree(tmpdir, ignore_errors=True)
            return {
                "skeleton": skeleton,
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
            default=[], description="The same source file(s) the skeleton was generated from"
        ),
        staging_id: str = Form(
            default="",
            description="A staged source (POST /api/staging) to read INSTEAD of files.",
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
        try:
            skeleton_obj = json.loads(skeleton)
        except json.JSONDecodeError as exc:
            raise HTTPException(400, f"skeleton is not valid JSON: {exc}") from exc
        if not isinstance(skeleton_obj, dict):
            raise HTTPException(400, "skeleton must be a JSON object")
        dialect_overrides = _parse_dialect_overrides(dialects)

        work, paths, owned = await _design_sources(
            cfg.registry_root, files, staging_id or None, prefix="asterism-skelcheck-"
        )
        try:
            effective = await asyncio.to_thread(
                _effective_dialects, list(paths), dialect_overrides
            )
            annotations = await asyncio.to_thread(
                annotate_skeleton,
                skeleton_obj,
                paths,
                dialects=effective,
                iri_base=cfg.iri_base,
            )
        finally:
            if owned:
                shutil.rmtree(work, ignore_errors=True)
        return {"annotations": annotations}

    @app.post("/api/propose/continue")
    async def propose_continue_endpoint(
        files: list[UploadFile] = File(
            default=[], description="Source file(s) — re-attach the same source the skeleton used"
        ),
        staging_id: str = Form(
            default="",
            description="A staged source (POST /api/staging) to read INSTEAD of files.",
        ),
        skeleton: str = Form(..., description="The confirmed skeleton IR as a JSON object"),
        domain: str = Form(default="", description="Domain hint (Markdown). Optional."),
        language: str = Form(default=""),
        dialects: str = Form(
            default="",
            description="Per-source read-dialect overrides as JSON (ADR source-dialect.md).",
        ),
        column_meanings: str = Form(
            default="",
            description=(
                "Settled (source, column) meanings as JSON "
                "[{source, column, label?, unit?}] (ADR meaning-before-identity). "
                "Projected onto §9 deterministically after every round."
            ),
        ),
        column_decisions: str = Form(
            default="",
            description=(
                "Columns the reader decided not to take in, as JSON "
                "[{source, column, action: 'exclude'}]. Re-asserted every round."
            ),
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
        try:
            skeleton_obj = json.loads(skeleton)
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, f"skeleton is not valid JSON: {exc}") from exc
        if not isinstance(skeleton_obj, dict):
            raise HTTPException(400, "skeleton must be a JSON object")
        max_tokens = _llm_max_tokens(x_llm_max_tokens)
        dialect_overrides = _parse_dialect_overrides(dialects)
        settled_meanings = _parse_column_meanings(column_meanings)
        settled_decisions = _parse_design_column_decisions(column_decisions)

        work, paths, owned = await _design_sources(
            cfg.registry_root, files, staging_id or None, prefix="asterism-continue-"
        )
        tmpdir = str(work)

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
                    column_meanings=settled_meanings,
                    column_decisions=settled_decisions,
                )
            finally:
                if owned:
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

        With ``dataset_id`` / ``staging_id`` set, the round is GROUNDED: the
        closed-menu appendix the automatic loop appends to every refine (exact
        filenames, the real columns, the Tier-0 menu) rides the last comment, so
        the model cannot invent column names it never saw.

        With ``dataset_id`` set (and a source attached to it) the refined
        document then goes through the SAME bounded self-correction round 0
        uses — validate, deterministic repair, at most two §9-only rounds — and
        the response carries an ``autocorrect`` block of the same shape. Without
        either the behaviour is byte-identical to before: one LLM call, no menu
        and no backstop.
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

        oracle = _refine_oracle(cfg.registry_root, body.dataset_id, body.staging_id)
        if oracle:
            # Same shape the loop uses: the menu rides the LAST comment's tail
            # (one cohesive block — a weak model follows that better than a
            # separately numbered directive).
            comments = [*comments[:-1], f"{comments[-1]}\n\n{oracle}"]

        def work(should_cancel: Callable[[], bool]) -> dict[str, object]:
            # Cooperative cancel only (jobs.start has no emit to bridge progress
            # through): the client checks it before each generation.
            _arm_llm_callbacks(llm, should_cancel=should_cancel)
            result = refine_schema(body.schema_md, comments, llm=llm, language=body.language)
            _record_llm_usage(cfg.registry_root, "refine", provider, llm, model)
            effective_md = result.effective_schema_md
            autocorrect: dict[str, object] | None = None
            # Only a COMPLETE refine is worth repairing: a truncated one already
            # fell back to the previous schema, which round 0 had validated.
            if body.dataset_id and result.complete:
                sources = _design_source_files(cfg.registry_root, body.dataset_id)
                if sources:
                    effective_md, autocorrect = design_loop.repair_after_refine(
                        effective_md,
                        list(sources),
                        sources[0].parent,
                        llm=llm,
                        on_llm_call=lambda feature: _record_llm_usage(
                            cfg.registry_root, feature, provider, llm, model
                        ),
                        should_cancel=should_cancel,
                    )
            # A meaning or a unit the HUMAN typed at S6 is not the model's to
            # forget: re-assert it on whatever document this round produced,
            # deterministically and last (ADR data-facts-invariant N6). Without
            # this, "fix the wording of that one column" quietly reverted every
            # correction the person had already made.
            # Mapping decisions are durable across AI rewrites: restore omitted
            # includes and remove any newly introduced use of excluded columns.
            human_decisions = _load_column_decisions(cfg.registry_root, body.dataset_id)
            if human_decisions:
                effective_md, _restored = apply_column_decisions_to_document(
                    effective_md, human_decisions
                )
            # Apply the latest meaning/unit after restoring an included row. Its
            # original column decision carries the first label the person entered;
            # a later display-meta edit is the newer human statement and must win.
            human_meta = _load_display_meta(cfg.registry_root, body.dataset_id)
            if human_meta:
                # ValueError = a legacy design with no §9 to re-assert onto.
                with contextlib.suppress(ValueError):
                    effective_md, _restored = apply_display_meta_to_document(
                        effective_md, human_meta
                    )
            # …and the input layer LAST: `(source, column)` is the truth source
            # for what a column means (ADR meaning-before-identity §6), so it
            # wins over the predicate-keyed projection of the same statement.
            settled_meanings = _load_column_meanings(cfg.registry_root, body.dataset_id)
            if settled_meanings:
                with contextlib.suppress(ValueError):
                    effective_md, _restored = apply_column_meanings_to_document(
                        effective_md, settled_meanings
                    )
            # Surface the truncation guard: `refined_md` stays the raw output for
            # transparency; `effective_schema_md` is what's safe to materialize
            # next (the previous complete schema when the refine was truncated,
            # or the self-corrected document when the backstop ran).
            return {
                "refined_md": result.refined_md,
                "effective_schema_md": effective_md,
                "complete": result.complete,
                "missing_artifacts": result.missing_artifacts,
                "warnings": result.warnings,
                "metadata": result.metadata,
                "autocorrect": autocorrect,
            }

        jobs: JobManager = app.state.jobs
        job_id = jobs.start(work)
        return JSONResponse({"job_id": job_id}, status_code=202)

    @app.post("/api/design/consult")
    async def design_consult(
        body: ConsultBody,
        x_api_key: str | None = Header(default=None),
        x_llm_provider: str | None = Header(default=None),
        x_llm_model: str | None = Header(default=None),
        x_llm_api_base: str | None = Header(default=None),
        x_llm_max_tokens: str | None = Header(
            default=None,
            description="Output-token cap override (positive integer); absent → provider default",
        ),
    ) -> dict[str, str]:
        """Design-consult chat (ADR design-consult-chat.md): a stateless, tool-free,
        non-streaming LLM turn for "what does this column mean" / "how do I use this
        screen" questions asked mid-design. Like ``/api/propose`` and ``/api/refine``,
        this is a plain generation call — no dataset is read or written, so it carries
        no write-auth gate. The API key is used only for this call and never
        persisted (D7, same as every other propose-family route).
        """
        messages = [m for m in body.messages if m.content.strip()]
        if not messages:
            raise HTTPException(400, "messages must contain at least one non-empty turn")
        for m in messages:
            if len(m.content) > CONSULT_MAX_CONTENT_CHARS:
                raise HTTPException(
                    400, f"message content exceeds {CONSULT_MAX_CONTENT_CHARS} characters"
                )
        # Only the most recent CONSULT_MAX_MESSAGES turns ride the call — older
        # history is dropped (oldest first), not rejected: a long-running
        # relationship with the drawer should degrade gracefully, not error.
        if len(messages) > CONSULT_MAX_MESSAGES:
            messages = messages[-CONSULT_MAX_MESSAGES:]

        provider, model, api_base, key = _llm_coords(
            x_api_key, x_llm_provider, x_llm_model, x_llm_api_base, cfg.registry_root
        )
        llm = _resolve_llm(
            provider, model, api_base, key, max_tokens=_llm_max_tokens(x_llm_max_tokens)
        )
        user_message = _render_consult_prompt(messages, body.context)

        def run() -> str:
            return as_completion(llm.complete(CONSULT_SYSTEM_PROMPT, user_message)).text

        try:
            reply = await asyncio.to_thread(run)
        except Exception as exc:  # LLM unreachable / provider error -> 502
            raise HTTPException(502, f"AI consult failed: {exc}") from exc
        await asyncio.to_thread(
            _record_llm_usage, cfg.registry_root, "design.consult", provider, llm, model
        )
        return {"reply": reply}

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
                if src_dir is None or not src_dir.is_dir():
                    # Not attached yet — the staged copy is the design-time source.
                    # An expired/unknown staging id is not an error HERE (the save
                    # must go through); the chain's attach step 404s and re-uploads.
                    src_dir = None
                    if body.staging_id:
                        with contextlib.suppress(staging.StagingNotFound, ValueError):
                            src_dir, _paths = staging.load(cfg.registry_root, body.staging_id)
                # Deterministic repair + data-fact re-assertion BEFORE the split.
                # Every path reaches materialize — the loop's own chain and the
                # wizard's manual "AI に直してもらう" / S6 "AI に反映して作り直す"
                # click — but only the loop re-asserted what the rows proved and
                # ran the machine-known repairs. So each manual round could
                # silently un-type a numeric column, and a dropped
                # `datatype: xsd:double` makes SPARQL compare numbers lexically:
                # a range question then answers WRONGLY instead of failing, the
                # one failure mode the citable-facts invariant cannot tolerate.
                # It also came back as an advisory, which invited the next click
                # (live 2026-08-18). Doing it here covers both paths with one
                # call — deterministic, idempotent, zero LLM calls, and a no-op
                # without a readable source (a brand-new design the loop covered).
                proposal_md = body.proposal_md
                if src_dir is not None and src_dir.is_dir():
                    # (already inside asyncio.to_thread(run) — call directly)
                    proposal_md = design_loop.repair_design(proposal_md, src_dir)
                human_decisions = _load_column_decisions(cfg.registry_root, body.dataset_id)
                if human_decisions:
                    proposal_md, _restored = apply_column_decisions_to_document(
                        proposal_md, human_decisions
                    )
                human_meta = _load_display_meta(cfg.registry_root, body.dataset_id)
                if human_meta:
                    proposal_md, _restored = apply_display_meta_to_document(
                        proposal_md, human_meta
                    )
                settled_meanings = _load_column_meanings(cfg.registry_root, body.dataset_id)
                if settled_meanings:
                    proposal_md, _restored = apply_column_meanings_to_document(
                        proposal_md, settled_meanings
                    )
                mat = materialize_schema(
                    proposal_md,
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
                    # The whole diagram DOCUMENT (title + fenced Mermaid + the
                    # property ↔ column table), byte-identical to what the CLI
                    # writes / regenerates — storing `mat.mermaid` here dropped the
                    # provenance table on the api path (observed live on ZEM). The
                    # fence is what every reader already extracts (UI
                    # `extractMermaid`, `registry.mermaid_of`), so the diagram
                    # renders unchanged.
                    "diagram.md": mat.diagram_md,
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
                (
                    validation_issues,
                    design_advisories,
                    duplicate_columns,
                ) = _design_checks_at_materialize(
                    cfg.registry_root,
                    body.dataset_id,
                    artifacts.get("mapping.rml.ttl"),
                    source_dir=src_dir if src_dir is not None and src_dir.is_dir() else None,
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
                    # Design DEFECTS (one readable message each) — empty when the
                    # design is clean OR no source was available to check against.
                    "validation_issues": validation_issues,
                    # Design WEAKNESSES (disconnected entities, unmapped columns).
                    # Separate field, not merged into the line above, because the
                    # two carry different force: a defect must be fixed, a weakness
                    # is a judgement call the human is offered ("fix it" vs
                    # "continue anyway"). Needs no source, so it is populated even
                    # on a brand-new design.
                    "advisories": design_advisories,
                    # The one weakness whose resolution is a CHOICE, handed over
                    # in the shape a chooser needs: the column, both candidate
                    # kinds with how many entities each mints, and the owner the
                    # rows recommend. The English sentence for the same finding
                    # is in `advisories` (ADR kantan K42).
                    "duplicate_columns": duplicate_columns,
                }
                if proposal_md != body.proposal_md:
                    # The deterministic repair edited the design (datatypes
                    # re-asserted, machine-known fixes applied — no LLM).
                    # Hand it back so the client's in-memory copy matches what
                    # was saved — otherwise the next refine would start from the
                    # stale document and re-introduce the same drop.
                    result["proposal_md"] = proposal_md
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
                            proposal_md=proposal_md,
                            advisories=design_advisories,
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
                            proposal_md=proposal_md,
                            advisories=design_advisories,
                        )
                    result["dataset"] = meta
                return result
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)

        try:
            result = await asyncio.to_thread(run)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
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

    @app.get(
        "/api/datasets/{dataset_id}/snapshot",
        dependencies=_write_auth,
        response_class=Response,
    )
    async def export_snapshot(dataset_id: str) -> Response:
        """Snapshot export (ADR local-first-distribution.md §5, exchange 軸).

        Token-gated like /api/sparql: the archive carries the full registry
        dir including accumulated source data, which is a sensitive read.
        """
        payload, filename = await exchange.build_snapshot(
            cfg, app.state.client, dataset_id
        )
        return Response(
            content=payload,
            media_type="application/gzip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.post("/api/datasets/import", dependencies=_write_auth)
    async def import_snapshot(file: UploadFile) -> dict[str, object]:
        """Snapshot import — lands as ingested (unpublished); publish via the
        existing promote gate, which runs alignment/ontology/crosswalk/togomcp."""
        payload = await _read_upload_bounded(file, _MAX_UPLOAD_BYTES)
        return await exchange.import_snapshot(
            cfg, app.state.client, payload, max_extracted_bytes=_MAX_UPLOAD_BYTES
        )

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
        could be checked (no RML, no readable source). ``advisories`` (weaknesses —
        disconnected entities, unmapped columns) comes back alongside it, and with
        the source now attached it carries the join-key candidates."""
        data = registry.load_dataset(cfg.registry_root, dataset_id)
        if data is None:
            raise HTTPException(404, f"dataset {dataset_id!r} not found")
        rml_ttl = (data.get("artifacts") or {}).get("mapping.rml.ttl")
        # The chooser's structured half is materialize's channel (that is where
        # the wizard builds its card); this endpoint keeps its two lists.
        issues, advisories, _dups = await asyncio.to_thread(
            _design_checks_at_materialize, cfg.registry_root, dataset_id, rml_ttl
        )
        # The data shape findings recorded at ingest (ADR data-shape-checks.md)
        # ride the SAME advisories list — they are the same kind of thing to the
        # reader ("something about this dataset is worth knowing"), and merging
        # them here means no new UI surface, no second fetch, no second empty
        # state. Design advisories come first: they are about the design the user
        # can still edit, while a shape finding describes data already ingested.
        shape_findings = [
            str(f) for f in ((data.get("meta") or {}).get("shape_findings") or [])
        ]
        return {
            "dataset_id": dataset_id,
            "validation_issues": issues,
            "advisories": advisories + shape_findings,
        }

    @app.get("/api/datasets/{dataset_id}/shapes.ttl")
    async def get_dataset_shapes(dataset_id: str) -> Response:
        """This dataset's constraints as standard SHACL (ADR data-shape-checks.md §D3).

        Compiled deterministically from the dataset's own RML — the same shapes
        Asterism checks with SPARQL, in the form the rest of the world runs
        (pySHACL, TopBraid, any SHACL-aware semantic layer). Read-only, and
        offered for export rather than as the checking engine: an in-process
        SHACL run cannot hold a graph this size, but a shapes FILE travels.

        Empty (a valid, shape-less Turtle document) for a dataset with no RML —
        a document dataset, or a design whose §9 spec never compiled.
        """
        data = registry.load_dataset(cfg.registry_root, dataset_id)
        if data is None:
            raise HTTPException(404, f"dataset {dataset_id!r} not found")
        rml_ttl = str((data.get("artifacts") or {}).get("mapping.rml.ttl") or "")
        compiled = await asyncio.to_thread(shapes.compile_shapes, rml_ttl)
        return Response(
            content=shapes.shapes_to_shacl(compiled),
            media_type="text/turtle",
            headers={
                "Content-Disposition": f'attachment; filename="{dataset_id}-shapes.ttl"'
            },
        )

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
        mapping_ir_yaml = str(artifacts.get("mapping.yaml") or "")

        def run() -> dict[str, object]:
            summary = summarize_rml(rml_ttl)
            labels = _model_yaml_labels(model_yaml, rml_ttl, mie_yaml)
            ir_meta: dict[str, dict[str, str]] = {}
            if mapping_ir_yaml.strip():
                ir_meta = _merge_ir_display_metadata(mapping_ir_yaml, summary)
            # Deterministic last resorts (source column, then the term IRI read
            # as words) so a design that skipped K8's labels still reads.
            _fill_missing_labels(summary, labels, ir_meta)
            return {"dataset_id": dataset_id, **summary, "labels": labels}

        return await asyncio.to_thread(run)

    @app.get("/api/datasets/{dataset_id}/source-samples")
    async def get_dataset_source_samples(dataset_id: str) -> dict[str, object]:
        """Real example values per column, read from the dataset's OWN persisted source.

        The kantan tier's column-meaning screen (S6) asks "is this what this column
        means?" and answers it with three real values from the file. Those used to
        come from a preview the BROWSER parsed at drop time — so re-opening the
        design from the catalog, resuming after a reload, or dropping an ``.xlsx``
        (which no browser can parse) left the evidence column blank on the one
        screen that exists to check the design against the data (KZ-B-25).

        Deterministic and LLM-free: the same inspector the design entrance uses,
        run over ``<id>/source/`` with the dialects the mapping pinned, so an
        instrument export with a preamble is read the way the design reads it. An
        ``.xlsx`` was already converted to CSV at attach, so it is covered.
        Best-effort by contract — a missing/unreadable source answers ``{}``
        rather than failing the screen.

        ``origins`` answers a narrower question the meaning screen must not
        blur: for a column BROADCAST from a dropped preamble (``dialects:
        …preamble: lines/keyvalue/keyvalue_cells``), was that column's NAME
        written by the person who made the file, or invented by asterism
        (``preamble_1``)? ``{resolved column name: {source, line, text,
        named}}`` — only preamble-origin columns appear; a column that came
        from the file's own header is not in ``origins`` at all, because its
        name needs no provenance note.
        """
        data = registry.load_dataset(cfg.registry_root, dataset_id)
        if data is None:
            raise HTTPException(404, f"dataset {dataset_id!r} not found")
        rml_ttl = str((data.get("artifacts") or {}).get("mapping.rml.ttl") or "")
        paths = _design_source_files(cfg.registry_root, dataset_id)

        def run() -> dict[str, object]:
            if not paths:
                return {"dataset_id": dataset_id, "sources": {}, "columns": {}, "origins": {}}
            try:
                dialects = _dialected_sources(rml_ttl)
            except _DialectReadError:
                dialects = {}
            per_source: dict[str, dict[str, list[str]]] = {}
            # Broadcast preamble columns only (K? — "the item name your file
            # didn't write"): {resolved column name: {source, line, text, named}}.
            # First file wins on a name clash, matching ``flat`` below.
            origins: dict[str, dict[str, object]] = {}
            for path in paths:
                # One file at a time, and only files that HAVE columns: the source
                # directory also holds the original .xlsx a design was converted
                # from (kept for provenance) and, for a document dataset, a PDF.
                # Inspecting those as tables raises — and one raise used to take
                # the whole answer with it, which is exactly the .xlsx case this
                # endpoint exists for.
                if path.suffix.lower() not in _SAMPLEABLE_SUFFIXES:
                    continue
                try:
                    inspections, _fks = inspect_source_set([path], dialects=dialects or None)
                except Exception:
                    logger.warning("source samples: %s could not be read", path.name)
                    continue
                per_source.update(_column_samples(inspections))
                dialect = dialects.get(path.name)
                if dialect is not None:
                    for ins in inspections:
                        cols = [c.name for c in getattr(ins, "columns", []) or []]
                        try:
                            found = _preamble_column_origins(path, dialect, cols)
                        except Exception:
                            logger.warning(
                                "source origins: %s could not be attributed", path.name
                            )
                            continue
                        for name, info in found.items():
                            origins.setdefault(name, info)
            # Flat view for the common single-source design; first file wins on a
            # name clash, matching what the client-side preview did.
            flat: dict[str, list[str]] = {}
            for cols in per_source.values():
                for name, values in cols.items():
                    if values and name not in flat:
                        flat[name] = values
            return {
                "dataset_id": dataset_id,
                "sources": per_source,
                "columns": flat,
                "origins": origins,
            }

        return await asyncio.to_thread(run)

    @app.post("/api/datasets/{dataset_id}/display-meta", dependencies=_write_auth)
    async def set_dataset_display_meta(
        dataset_id: str, body: DisplayMetaBody
    ) -> dict[str, object]:
        """Correct a column's MEANING / UNIT in place — deterministic, no LLM (K8).

        The meaning of a column and the unit it is in are the two things only the
        person who took the measurements knows, and until now the only way to fix
        them was a free-text note → a full LLM refine → a rewrite of the whole
        design → a re-ingest. That is minutes of waiting to fix "K", and weak
        models were observed ignoring the note or breaking something else while
        obeying it (KZ-B-05 / WEAK-MODEL-20).

        ``label`` and ``unit`` are DISPLAY metadata on the mapping spec: no triple
        and no value changes, so the correction is a splice of the §9 block plus a
        re-projection of the artifacts (:func:`materialize_schema`) — the same
        deterministic path the unit auto-fill uses. The edits are also recorded
        beside the dataset as HUMAN-owned, so a later AI round cannot silently
        revert them (re-asserted on /api/refine).

        Returns the rows it actually changed. 409 when the dataset has no stored
        design to splice (nothing to be the single source of truth).
        """
        data = registry.load_dataset(cfg.registry_root, dataset_id)
        if data is None:
            raise HTTPException(404, f"dataset {dataset_id!r} not found")
        edits = [e for e in body.edits if e.predicate.strip()]
        if not edits:
            raise HTTPException(422, "at least one edit is required")
        proposal_md = registry.load_proposal(cfg.registry_root, dataset_id) or ""
        if not proposal_md.strip():
            raise HTTPException(
                409, f"dataset {dataset_id!r} has no stored design to edit"
            )
        source_dir = registry.source_dir(cfg.registry_root, dataset_id)
        # /rules exposes the compiled TriplesMap id (e.g. ``PatternMap``), while
        # §9 stores the authored map name (``pattern``) that `_display_meta_matches`
        # compares against. Same boundary the column-decisions endpoint already
        # normalizes (see its own ``canonical_maps`` comment) — without this, a
        # client-sent ``map`` built from /rules either never matches (silent no-op)
        # or, when omitted, matches every map sharing the predicate (a single edit
        # bleeding into an unrelated map's row — real-user incident 2026-08-25).
        # Best-effort: an unparseable/legacy IR just means edits go through
        # unnormalized (today's behavior), never a hard failure on this path.
        canonical_maps: dict[str, str] = {}
        mapping_ir_yaml = str((data.get("artifacts") or {}).get("mapping.yaml") or "")
        if mapping_ir_yaml.strip():
            try:
                from asterism_step0.mapping_ir import parse_mapping_ir
                from asterism_step0.rml_compile import _map_node_name

                for m in parse_mapping_ir(mapping_ir_yaml).maps:
                    canonical_maps[m.name] = m.name
                    canonical_maps[_map_node_name(m.name)] = m.name
            except Exception:
                canonical_maps = {}

        def run() -> dict[str, object]:
            spec = [e.model_dump() for e in edits]
            for edit in spec:
                requested_map = str(edit.get("map") or "")
                if requested_map and requested_map in canonical_maps:
                    edit["map"] = canonical_maps[requested_map]
            try:
                new_md, changed = apply_display_meta_to_document(proposal_md, spec)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            if not changed:
                return {"dataset_id": dataset_id, "changed": [], "stored": False}
            projected, warnings = _artifacts_from_document(new_md, dataset_id, source_dir)
            artifacts = {k: v or "" for k, v in projected.items()}
            # A meaning is display metadata: the compiled mapping must come back
            # unchanged. If it did not, something else is wrong with the document
            # and overwriting a working mapping over a label edit would be the
            # worst possible trade — refuse and leave the dataset as it was.
            stored_rml = str((data.get("artifacts") or {}).get("mapping.rml.ttl") or "")
            if stored_rml.strip() and not artifacts["mapping.rml.ttl"].strip():
                raise HTTPException(
                    409,
                    "the stored design no longer compiles, so the meaning could not "
                    "be saved without losing the mapping",
                )
            meta = registry.update_dataset_artifacts(
                cfg.registry_root,
                dataset_id,
                artifacts,
                complete=bool(data["meta"].get("complete")),
                warnings=warnings,
                traps=list(data["meta"].get("traps") or []),
                exit_code=int(data["meta"].get("exit_code") or 0),
                proposal_md=new_md,
                advisories=list(data["meta"].get("advisories") or []),
            )
            _remember_display_meta(cfg.registry_root, dataset_id, spec)
            return {"dataset_id": dataset_id, "changed": changed, "stored": meta is not None}

        return await asyncio.to_thread(run)

    @app.get("/api/datasets/{dataset_id}/column-decisions")
    async def get_dataset_column_decisions(dataset_id: str) -> dict[str, object]:
        """Return the human's durable include/exclude calls for source columns."""
        if registry.load_dataset(cfg.registry_root, dataset_id) is None:
            raise HTTPException(404, f"dataset {dataset_id!r} not found")
        return {
            "dataset_id": dataset_id,
            "decisions": _load_column_decisions(cfg.registry_root, dataset_id),
        }

    @app.post("/api/datasets/{dataset_id}/column-decisions", dependencies=_write_auth)
    async def set_dataset_column_decisions(
        dataset_id: str, body: ColumnDecisionsBody
    ) -> dict[str, object]:
        """Apply human source-column decisions without an AI or generated code.

        An include adds one raw-passthrough direct property to §9, compiles it
        through the normal materialize gate, and saves the reprojected artifacts
        in place. An exclude leaves an already-unused column alone, and removes a
        later rewrite's attempt to map it. An ``own`` keeps a column two kinds
        both record on the ONE the human picked, and deletes it from the others —
        the ownership tie the rows could not break, which is why eight AI rounds
        never cleared it (ADR kantan K42).
        """
        data = registry.load_dataset(cfg.registry_root, dataset_id)
        if data is None:
            raise HTTPException(404, f"dataset {dataset_id!r} not found")
        incoming = [decision.model_dump(exclude_none=True) for decision in body.decisions]
        if not incoming:
            raise HTTPException(422, "at least one column decision is required")
        for decision in incoming:
            if not str(decision.get("source") or "").strip():
                raise HTTPException(422, "a column decision requires a source")
            if not str(decision.get("column") or "").strip():
                raise HTTPException(422, "a column decision requires a column")
            if decision["action"] in {"include", "own"} and not str(
                decision.get("map") or ""
            ).strip():
                raise HTTPException(422, "a column decision requires a map")
            if decision["action"] == "include" and not str(decision.get("label") or "").strip():
                raise HTTPException(422, "an include decision requires a label")
        proposal_md = registry.load_proposal(cfg.registry_root, dataset_id) or ""
        if not proposal_md.strip():
            raise HTTPException(409, f"dataset {dataset_id!r} has no stored design to edit")
        source_dir = registry.source_dir(cfg.registry_root, dataset_id)
        staged_paths: list[Path] = []
        if source_dir is None or not source_dir.is_dir():
            # The wizard settles a duplicated column at S5, one step BEFORE the
            # chain persists the source — the staged copy is the design-time
            # source there (ADR source-staging.md), same as for /api/materialize.
            source_dir = None
            if body.staging_id:
                with contextlib.suppress(staging.StagingNotFound, ValueError):
                    source_dir, staged_paths = staging.load(
                        cfg.registry_root, body.staging_id
                    )
        if source_dir is None or not source_dir.is_dir():
            raise HTTPException(409, f"dataset {dataset_id!r} has no persisted source to inspect")
        mapping_ir_yaml = str((data.get("artifacts") or {}).get("mapping.yaml") or "")
        try:
            from asterism_step0.mapping_ir import MappingIRParseError, parse_mapping_ir
            from asterism_step0.rml_compile import _map_node_name

            mapping_ir = parse_mapping_ir(mapping_ir_yaml)
        except MappingIRParseError as exc:
            raise HTTPException(409, "the stored mapping spec could not be read") from exc
        existing_decisions = _load_column_decisions(cfg.registry_root, dataset_id)
        existing_by_key = {
            _column_decision_key(decision): decision for decision in existing_decisions
        }
        # /rules exposes the compiled TriplesMap id (``ReadingMap``), while §9
        # stores the authored map name (``reading``). Accept either at this API
        # boundary and persist only the §9 name used by the deterministic patch.
        canonical_maps = {mapping.name: mapping.name for mapping in mapping_ir.maps}
        canonical_maps.update(
            {_map_node_name(mapping.name): mapping.name for mapping in mapping_ir.maps}
        )
        mappings_by_name = {mapping.name: mapping for mapping in mapping_ir.maps}
        prefixes = dict(mapping_ir.prefixes)

        def expanded_class(term: str) -> str:
            head, separator, tail = term.partition(":")
            namespace = prefixes.get(head) if separator else None
            return f"{namespace}{tail}" if namespace is not None else term

        for decision in incoming:
            if decision["action"] == "exclude":
                # Exclusion belongs to the physical source column, not an RDF
                # entity. It remains valid even when no map reads the source or a
                # later structural rewrite renames every map.
                decision.pop("map", None)
                decision.pop("map_class", None)
                decision.pop("datatype", None)
                continue
            if decision["action"] == "own":
                # No label to restore and no fallback row to re-point: an owner
                # verdict is only "this map keeps it", so the plain canonical
                # lookup is the whole resolution. The class is stamped for the
                # same reason an include stamps it — a later AI round may rename
                # the map, and the verdict has to survive that.
                requested_map = str(decision["map"])
                canonical_map = canonical_maps.get(requested_map)
                if canonical_map is None:
                    raise HTTPException(
                        422, f"column decision names unknown map {requested_map!r}"
                    )
                selected_map = mappings_by_name[canonical_map]
                if selected_map.source != str(decision["source"]):
                    raise HTTPException(
                        422,
                        f"column decision source {decision['source']!r} does not match "
                        f"map {canonical_map!r}",
                    )
                decision["map"] = canonical_map
                if selected_map.subject.classes:
                    decision["map_class"] = expanded_class(selected_map.subject.classes[0])
                else:
                    decision.pop("map_class", None)
                decision.pop("label", None)
                decision.pop("unit", None)
                decision.pop("datatype", None)
                continue
            requested_map = str(decision["map"])
            previous = existing_by_key.get(_column_decision_key(decision))
            previous_class = (
                str(previous.get("map_class") or "").strip()
                if previous and previous.get("action") == "include"
                else ""
            )
            previous_map = str(previous.get("map") or "") if previous else ""
            source_maps = [
                mapping
                for mapping in mapping_ir.maps
                if mapping.source == str(decision["source"])
            ]
            class_maps = []
            if previous_class and requested_map == previous_map:
                wanted_class = expanded_class(previous_class)
                class_maps = [
                    mapping
                    for mapping in source_maps
                    if wanted_class
                    in {expanded_class(value) for value in mapping.subject.classes}
                ]
            restoring_owner = bool(previous_class and requested_map == previous_map)
            if restoring_owner and len(class_maps) == 1:
                canonical_map = class_maps[0].name
            elif restoring_owner and len(source_maps) == 1:
                canonical_map = source_maps[0].name
            elif restoring_owner:
                canonical_map = None
            else:
                canonical_map = canonical_maps.get(requested_map)
            if canonical_map is None:
                raise HTTPException(422, f"column decision names unknown map {requested_map!r}")
            decision["map"] = canonical_map
            selected_map = mappings_by_name[canonical_map]
            if selected_map.source != str(decision["source"]):
                raise HTTPException(
                    422,
                    f"column decision source {decision['source']!r} does not match "
                    f"map {canonical_map!r}",
                )
            if selected_map.subject.classes:
                decision["map_class"] = expanded_class(selected_map.subject.classes[0])
            else:
                decision.pop("map_class", None)
            # Datatypes are established from the persisted source below, never
            # accepted as a browser assertion.
            decision.pop("datatype", None)

        def run() -> dict[str, object]:
            merged_decisions = _merge_column_decisions(existing_decisions, incoming)
            persisted = registry.list_source_files(cfg.registry_root, dataset_id)
            source_paths = {path.name: path for path in (persisted or staged_paths)}
            incoming_sources = {str(d["source"]) for d in incoming}
            missing_sources = sorted(incoming_sources - set(source_paths))
            if missing_sources:
                raise HTTPException(
                    422, f"dataset source does not contain {missing_sources[0]!r}"
                )
            current_decisions = [
                decision
                for decision in merged_decisions
                if str(decision["source"]) in source_paths
            ]
            requested_sources = {str(d["source"]) for d in current_decisions}
            try:
                inspections, _ = inspect_source_set(
                    [source_paths[name] for name in sorted(requested_sources)],
                    dialects=mapping_ir.dialects or None,
                )
            except (OSError, UnicodeError, ValueError) as exc:
                raise HTTPException(422, f"could not inspect dataset source: {exc}") from exc
            # ColumnSummary.inferred_type is intentionally sample-based and is
            # unsafe for emitting datatypes. Reuse the full-column scan that the
            # normal design repair path uses; every other value remains a string.
            strict_types = design_loop._numeric_types_by_source(source_dir, mapping_ir)
            source_columns = {
                inspection.name: {
                    column.name: strict_types.get(inspection.name, {}).get(
                        column.name, "xsd:string"
                    )
                    for column in inspection.columns
                }
                for inspection in inspections
            }
            incoming_keys = {_column_decision_key(decision) for decision in incoming}
            for decision in current_decisions:
                source = str(decision["source"])
                column = str(decision["column"])
                if (
                    _column_decision_key(decision) in incoming_keys
                    and column not in source_columns.get(source, {})
                ):
                    raise HTTPException(422, f"source {source!r} has no column {column!r}")
            decisions = [
                decision
                for decision in current_decisions
                if str(decision["column"])
                in source_columns.get(str(decision["source"]), {})
            ]
            for decision in decisions:
                if decision.get("action") != "include":
                    decision.pop("datatype", None)
                    continue
                source_types = source_columns.get(str(decision["source"]), {})
                datatype = source_types.get(str(decision["column"]))
                if datatype:
                    decision["datatype"] = datatype
            try:
                new_md, changed = apply_column_decisions_to_document(
                    proposal_md, decisions, source_columns=source_columns
                )
                human_meta = _load_display_meta(cfg.registry_root, dataset_id)
                if human_meta:
                    new_md, _restored = apply_display_meta_to_document(new_md, human_meta)
                settled_meanings = _load_column_meanings(cfg.registry_root, dataset_id)
                if settled_meanings:
                    new_md, _restored = apply_column_meanings_to_document(
                        new_md, settled_meanings
                    )
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            requires_reingest = False
            if changed:
                mat = materialize_schema(
                    new_md, ".", dataset_id, write=False, source_dir=source_dir
                )
                if mat.mapping_ir_issues:
                    raise HTTPException(422, {"mapping_ir_issues": mat.mapping_ir_issues})
                if not (mat.rml_ttl or "").strip():
                    raise HTTPException(409, "the edited mapping spec could not generate RML")
                try:
                    substrate.validate_rml_design(
                        substrate.substitute_run_id(mat.rml_ttl), source_dir
                    )
                except substrate.RmlValidationError as exc:
                    raise HTTPException(422, {"validation_issues": list(exc.issues)}) from exc
                artifacts = {
                    "diagram.md": mat.diagram_md or "",
                    "model.yaml": mat.rdf_config_model or "",
                    "mie.yaml": mat.mie_yaml or "",
                    "ingester.py": mat.ingester_py or "",
                    "mapping.rml.ttl": mat.rml_ttl or "",
                    "mapping.yaml": mat.mapping_ir_yaml or "",
                }
                _issues, advisories, _dups = _design_checks_at_materialize(
                    cfg.registry_root,
                    dataset_id,
                    artifacts["mapping.rml.ttl"],
                    source_dir=source_dir,
                    column_decisions=decisions,
                )
                meta = registry.update_dataset_artifacts(
                    cfg.registry_root,
                    dataset_id,
                    artifacts,
                    complete=mat.complete,
                    warnings=mat.warnings,
                    traps=list(data["meta"].get("traps") or []),
                    exit_code=int(data["meta"].get("exit_code") or 0),
                    proposal_md=new_md,
                    advisories=advisories,
                )
                if meta is None:
                    raise HTTPException(404, f"dataset {dataset_id!r} not found")
                requires_reingest = (
                    artifacts["mapping.rml.ttl"]
                    != str((data.get("artifacts") or {}).get("mapping.rml.ttl") or "")
                )
            else:
                # A decision that changes no mapping artifact can still resolve a
                # human-review advisory. Re-save the unchanged bundle so the
                # dataset's persisted meta stops repeating only that confirmed
                # notice (the filter is fail-closed for any unlisted column).
                artifacts = {
                    key: str(value or "")
                    for key, value in (data.get("artifacts") or {}).items()
                }
                _issues, advisories, _dups = _design_checks_at_materialize(
                    cfg.registry_root,
                    dataset_id,
                    artifacts.get("mapping.rml.ttl", ""),
                    source_dir=source_dir,
                    column_decisions=decisions,
                )
                meta = registry.update_dataset_artifacts(
                    cfg.registry_root,
                    dataset_id,
                    artifacts,
                    complete=bool(data["meta"].get("complete")),
                    warnings=list(data["meta"].get("warnings") or []),
                    traps=list(data["meta"].get("traps") or []),
                    exit_code=int(data["meta"].get("exit_code") or 0),
                    proposal_md=proposal_md,
                    advisories=advisories,
                )
                if meta is None:
                    raise HTTPException(404, f"dataset {dataset_id!r} not found")
            _remember_column_decisions(cfg.registry_root, dataset_id, decisions)
            return {
                "dataset_id": dataset_id,
                "changed": changed,
                "proposal_md": new_md,
                "requires_reingest": requires_reingest,
            }

        return await asyncio.to_thread(run)

    @app.get("/api/datasets/{dataset_id}/column-meanings")
    async def get_dataset_column_meanings(dataset_id: str) -> dict[str, object]:
        """Return the settled ``(source, column)`` meanings for this dataset."""
        if registry.load_dataset(cfg.registry_root, dataset_id) is None:
            raise HTTPException(404, f"dataset {dataset_id!r} not found")
        return {
            "dataset_id": dataset_id,
            "meanings": _load_column_meanings(cfg.registry_root, dataset_id),
        }

    @app.post("/api/datasets/{dataset_id}/column-meanings", dependencies=_write_auth)
    async def set_dataset_column_meanings(
        dataset_id: str, body: ColumnMeaningsBody
    ) -> dict[str, object]:
        """Record what source columns MEAN, and project that onto the design.

        ADR ``meaning-before-identity.md``. The store is keyed by
        ``(source, column)`` because that is what a meaning is ABOUT — it holds
        whether or not a design maps the column today, and it survives a redesign
        that re-mints every predicate. The projection onto §9 is the same
        deterministic splice the predicate-keyed display-meta edit does: display
        metadata only (K8), no triple and no value changes.

        A meaning for a column the current design does not map is stored and
        reported as unmapped rather than refused: the person answered a question
        about their data, and the answer is not wrong just because the design has
        not caught up.
        """
        data = registry.load_dataset(cfg.registry_root, dataset_id)
        if data is None:
            raise HTTPException(404, f"dataset {dataset_id!r} not found")
        # exclude_unset: a field the client did not send is "leave it alone",
        # while an empty string is "clear it" (_merge_column_meanings).
        incoming = [
            {
                **meaning.model_dump(exclude_unset=True),
                "source": meaning.source,
                "column": meaning.column,
            }
            for meaning in body.meanings
        ]
        for meaning in incoming:
            if not str(meaning.get("source") or "").strip():
                raise HTTPException(422, "a column meaning requires a source")
            if not str(meaning.get("column") or "").strip():
                raise HTTPException(422, "a column meaning requires a column")
        if not incoming:
            raise HTTPException(422, "at least one column meaning is required")
        proposal_md = registry.load_proposal(cfg.registry_root, dataset_id) or ""
        if not proposal_md.strip():
            raise HTTPException(
                409, f"dataset {dataset_id!r} has no stored design to edit"
            )
        source_dir = registry.source_dir(cfg.registry_root, dataset_id)

        def run() -> dict[str, object]:
            meanings = _merge_column_meanings(
                _load_column_meanings(cfg.registry_root, dataset_id), incoming
            )
            # The store holds the merged STATE, where a cleared field is simply
            # gone. Downstream an absent field means "leave it alone", so a
            # deliberate clear rides along as the empty string it was sent as.
            cleared = [
                meaning
                for meaning in incoming
                if any(
                    field in meaning and not str(meaning[field] or "").strip()
                    for field in ("label", "unit")
                )
            ]
            try:
                new_md, changed = apply_column_meanings_to_document(
                    proposal_md, [*meanings, *cleared]
                )
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            if changed:
                projected, warnings = _artifacts_from_document(new_md, dataset_id, source_dir)
                artifacts = {k: v or "" for k, v in projected.items()}
                # A meaning is display metadata: the compiled mapping must come
                # back unchanged. If it did not, something else is wrong with the
                # document, and overwriting a working mapping over a label edit
                # would be the worst possible trade.
                stored_rml = str((data.get("artifacts") or {}).get("mapping.rml.ttl") or "")
                if stored_rml.strip() and not artifacts["mapping.rml.ttl"].strip():
                    raise HTTPException(
                        409,
                        "the stored design no longer compiles, so the meaning could not "
                        "be saved without losing the mapping",
                    )
                registry.update_dataset_artifacts(
                    cfg.registry_root,
                    dataset_id,
                    artifacts,
                    complete=bool(data["meta"].get("complete")),
                    warnings=warnings,
                    traps=list(data["meta"].get("traps") or []),
                    exit_code=int(data["meta"].get("exit_code") or 0),
                    proposal_md=new_md,
                    advisories=list(data["meta"].get("advisories") or []),
                )
            _remember_column_meanings(cfg.registry_root, dataset_id, meanings)
            return {"dataset_id": dataset_id, "changed": changed, "stored": True}

        return await asyncio.to_thread(run)

    @app.get("/api/datasets/{dataset_id}/draft-stats")
    async def dataset_draft_stats(dataset_id: str) -> dict[str, object]:
        """Per-class entity counts of the dataset's draft graph + source data rows.

        Backs the kantan tier's correspondence card (ADR kantan-mode-two-tier-ux.md
        K12): "source file N rows → M entities per kind (still an unpublished
        draft)". Counts DISTINCT subjects per class in the staged version graph
        recorded at ingest — the same "current draft" resolution ``/alignment``
        and ``/promote`` use (``meta.graph_iri``, falling back to the dataset key
        graph for pre-part5 records) — and pairs them with the per-file data-row
        counts of the persisted tabular source. The row↔entity correspondence is
        how a domain expert spots a collapsed key with their own eyes; no triple
        counts here by design (K12).

        Read-only and forgiving: a dataset that was never ingested, or an
        unreachable Oxigraph, returns 200 with ``classes: []`` (the UI hides the
        card) — only an unknown dataset 404s.
        """
        data = registry.load_dataset(cfg.registry_root, dataset_id)
        if data is None:
            raise HTTPException(404, f"dataset {dataset_id!r} not found")
        meta = data.get("meta") or {}
        artifacts = data.get("artifacts") or {}

        classes: list[dict[str, object]] = []
        # Before promote the data sits in the staged draft (``graph_iri``);
        # promote clears ``ingested``/``graph_iri`` and records the SAME version
        # graph as ``live_graph`` (an O(1) pointer flip — nothing moves). Reading
        # only ``ingested`` therefore made the correspondence card vanish exactly
        # when it matters most: 見直す on an already-published dataset, where the
        # question is "does the published version still say what I meant?".
        # /trial-queries already resolves it this way; this is the same rule.
        if meta.get("ingested") or meta.get("promoted"):
            staged_iri = (
                meta.get("graph_iri")  # the staged draft (pre-promote)
                or meta.get("live_graph")  # the live version graph (post-promote)
                or substrate.canonical_graph_iri(dataset_id)  # pre-part5 records
            )
            q = (
                f"SELECT ?class (COUNT(DISTINCT ?s) AS ?n) WHERE {{ "
                f"GRAPH <{staged_iri}> {{ ?s a ?class }} }} "
                f"GROUP BY ?class ORDER BY DESC(?n)"
            )
            client: OxigraphClient = app.state.client
            res: dict | None
            try:
                res = await client.sparql_select(q)
            except Exception:  # store unreachable → empty card, never a 500
                res = None
            bindings = []
            if isinstance(res, dict):
                results = res.get("results")
                if isinstance(results, dict):
                    bindings = results.get("bindings", [])
            # CURIEs resolve against THIS dataset's declared prefixes (same
            # extraction the ontology projection uses) — display enrichment only.
            prefixes = STANDARD_PREFIXES | extract_prefixes(
                str(artifacts.get("mapping.rml.ttl") or ""),
                str(artifacts.get("mie.yaml") or ""),
            )
            for b in bindings:
                cls = b.get("class") or {}
                n_raw = (b.get("n") or {}).get("value")
                if cls.get("type") != "uri" or n_raw is None:
                    continue
                try:
                    n = int(n_raw)
                except (TypeError, ValueError):
                    continue
                entry: dict[str, object] = {"iri": cls["value"], "n": n}
                curie = _curie_of(str(cls["value"]), prefixes)
                if curie:
                    entry["curie"] = curie
                classes.append(entry)

        source_rows = await asyncio.to_thread(
            _count_source_rows, cfg.registry_root, dataset_id
        )
        # `counted` separates "nothing has been taken in yet" from "the count
        # failed": without it the UI said the latter for both, which reads as an
        # error on a screen where nothing is wrong (2026-08-19 review).
        return {
            "dataset_id": dataset_id,
            "classes": classes,
            "source_rows": source_rows,
            "counted": bool(meta.get("ingested") or meta.get("promoted")),
        }

    @app.get("/api/datasets/{dataset_id}/trial-queries")
    async def dataset_trial_queries(dataset_id: str) -> dict[str, object]:
        """Deterministic "try it" queries over the dataset's staged draft graph.

        Backs the kantan tier's S7 ためす screen (ADR kantan-mode-two-tier-ux.md
        K9): right after the draft ingest the wizard auto-runs a fixed set of
        read-only aggregates — per-kind entity counts, the busiest numeric
        field's range, and the entity holding its maximum (its subject IRI is
        the citation) — so the user experiences "citable facts" on their own
        data before publishing. LLM-free by design: the question sentences are
        assembled client-side from the returned labels; this endpoint reports
        numbers, IRIs and the exact SPARQL it ran (the UI folds it as 技術情報).
        Labels/units come from the Mapping IR (K8) + the model.yaml projection —
        never re-derived by an AI. Works before AND after promote: the staged
        version graph a promote points ``liveGraph`` at is the same graph.

        Read-only and forgiving like /draft-stats: a never-ingested dataset or
        an unreachable store returns 200 with ``available: false`` (the UI
        offers a retry; S7 must stay passable — the human gates are S4/S6/S8).
        When no numeric field exists, ``samples`` carries real entity IRIs of
        the biggest kind instead (the ADR's fallback) and ``range``/``top``
        are null. Only an unknown dataset 404s.
        """
        data = registry.load_dataset(cfg.registry_root, dataset_id)
        if data is None:
            raise HTTPException(404, f"dataset {dataset_id!r} not found")
        meta = data.get("meta") or {}
        artifacts = data.get("artifacts") or {}
        out: dict[str, object] = {
            "dataset_id": dataset_id,
            "available": False,
            "classes": [],
            "count_sparql": None,
            "entities": None,
            "range": None,
            "top": None,
            "samples": None,
        }
        # Before promote the data sits in the staged draft (``graph_iri``);
        # promote clears ``ingested``/``graph_iri`` and records the SAME version
        # graph as ``live_graph`` (O(1) pointer flip, nothing moves) — so the S9
        # done screen can re-fetch its question chips after a reload too.
        if not (meta.get("ingested") or meta.get("promoted")):
            return out

        # Display enrichment — the same two sources /rules merges: the IR's
        # reviewed label/unit per predicate + the model.yaml rdfs:labels
        # (classes AND predicates). Both deterministic; both optional.
        def display_meta() -> tuple[dict[str, str], dict[str, dict[str, str]]]:
            labels = _model_yaml_labels(
                str(artifacts.get("model.yaml") or ""),
                str(artifacts.get("mapping.rml.ttl") or ""),
                str(artifacts.get("mie.yaml") or ""),
            )
            try:
                ir_meta = _ir_predicate_display(str(artifacts.get("mapping.yaml") or ""))
            except Exception:
                ir_meta = {}  # enrichment only — a broken IR must not block S7
            return labels, ir_meta

        labels, ir_meta = await asyncio.to_thread(display_meta)

        def label_of(iri: str) -> str | None:
            # ① authored label (K8) ② model.yaml projection ③ the source column
            # heading ④ the term IRI read as words. The last two are new: the
            # trial questions on S7 are read aloud by the person who made the
            # file, and "hasSeebeckCoefficient の範囲は" is not their language
            # when the server is holding the column heading they typed.
            return (
                (ir_meta.get(iri) or {}).get("label")
                or labels.get(iri)
                or (ir_meta.get(iri) or {}).get("column_label")
                or _humanize_term_iri(iri)
                or None
            )

        def unit_of(iri: str) -> str | None:
            return (ir_meta.get(iri) or {}).get("unit") or None

        def decorate(entry: dict[str, object], iri: str) -> dict[str, object]:
            got = label_of(iri)
            if got:
                entry["label"] = got
            unit = unit_of(iri)
            if unit:
                entry["unit"] = unit
            return entry

        staged_iri = (
            meta.get("graph_iri")  # the staged draft (pre-promote)
            or meta.get("live_graph")  # the live version graph (post-promote)
            or substrate.canonical_graph_iri(dataset_id)  # pre-part5 records
        )
        client: OxigraphClient = app.state.client

        async def select(q: str) -> list[dict] | None:
            """Bindings, or None when the store call failed (degrade, never 500)."""
            try:
                res = await client.sparql_select(q)
            except Exception:
                return None
            if isinstance(res, dict):
                results = res.get("results")
                if isinstance(results, dict):
                    bindings = results.get("bindings", [])
                    if isinstance(bindings, list):
                        return bindings
            return []

        # 1) Per-kind counts — the same aggregation the S6 correspondence card
        #    shows, restated here as the first answered question.
        count_q = (
            f"SELECT ?class (COUNT(DISTINCT ?s) AS ?n) WHERE {{ "
            f"GRAPH <{staged_iri}> {{ ?s a ?class }} }} "
            f"GROUP BY ?class ORDER BY DESC(?n) ?class"
        )
        rows = await select(count_q)
        if rows is None:
            return out  # store down → available: false, the UI offers retry
        classes: list[dict[str, object]] = []
        for b in rows:
            cls = b.get("class") or {}
            n_raw = (b.get("n") or {}).get("value")
            if cls.get("type") != "uri" or n_raw is None:
                continue
            try:
                n = int(n_raw)
            except (TypeError, ValueError):
                continue
            entry: dict[str, object] = {"iri": cls["value"], "n": n}
            got = label_of(str(cls["value"]))
            if got:
                entry["label"] = got
            classes.append(entry)
        out["available"] = True
        out["classes"] = classes
        out["count_sparql"] = count_q

        # 1b) No typed classes at all — a legal shape (real weak-model designs
        #     often declare no rr:class): the first question falls back to the
        #     plain entity count so the screen never opens empty-handed.
        if not classes:
            ent_q = (
                f"SELECT (COUNT(DISTINCT ?s) AS ?n) WHERE {{ "
                f"GRAPH <{staged_iri}> {{ ?s ?p ?o }} }}"
            )
            ent_rows = await select(ent_q) or []
            ent_raw = (ent_rows[0].get("n") or {}).get("value") if ent_rows else None
            try:
                ent_n = int(ent_raw) if ent_raw is not None else 0
            except (TypeError, ValueError):
                ent_n = 0
            if ent_n > 0:
                out["entities"] = {"n": ent_n, "sparql": ent_q}

        # 2) Numeric fields by usage. The busiest one with an actual spread
        #    (min < max) becomes the range question; the next such field (or the
        #    same when it is the only one) the top-value question. Deterministic
        #    tie-break by predicate IRI. Numbers are recognised by a cast
        #    attempt (xsd:double(str(?v)) — an un-castable literal leaves ?num
        #    unbound), NOT by isNumeric(): real ingests routinely carry numbers
        #    as plain string literals, which isNumeric() would hide entirely.
        num_q = (
            f"PREFIX xsd: <http://www.w3.org/2001/XMLSchema#> "
            f"SELECT ?p (COUNT(?num) AS ?n) (MIN(?num) AS ?min) (MAX(?num) AS ?max) WHERE {{ "
            f"GRAPH <{staged_iri}> {{ ?s ?p ?v FILTER(isLiteral(?v)) "
            f"BIND(xsd:double(str(?v)) AS ?num) FILTER(BOUND(?num)) }} }} "
            f"GROUP BY ?p ORDER BY DESC(?n) ?p LIMIT 12"
        )
        num_rows = await select(num_q) or []
        spread: list[dict[str, object]] = []
        for b in num_rows:
            p = b.get("p") or {}
            vmin = (b.get("min") or {}).get("value")
            vmax = (b.get("max") or {}).get("value")
            if p.get("type") != "uri" or vmin is None or vmax is None:
                continue
            try:
                has_spread = float(vmin) < float(vmax)
                n = int((b.get("n") or {}).get("value") or 0)
            except (TypeError, ValueError):
                continue
            if has_spread:
                spread.append({"iri": p["value"], "n": n, "min": vmin, "max": vmax})
        if spread:
            pick = spread[0]
            out["range"] = decorate(
                {
                    "predicate_iri": pick["iri"],
                    "n": pick["n"],
                    "min": pick["min"],
                    "max": pick["max"],
                    "sparql": num_q,
                },
                str(pick["iri"]),
            )

        # 3) The entity holding the maximum of the top-question field, with its
        #    other literal values as context ("1.42 — 試料: BiTe-04, 温度: 500")
        #    and its IRI as the citation the whole screen exists to show.
        top_pick = spread[1] if len(spread) > 1 else (spread[0] if spread else None)
        if top_pick:
            top_iri = str(top_pick["iri"])
            top_q = (
                f"PREFIX xsd: <http://www.w3.org/2001/XMLSchema#> "
                f"SELECT ?s ?v WHERE {{ GRAPH <{staged_iri}> {{ "
                f"?s <{top_iri}> ?v FILTER(isLiteral(?v)) "
                f"BIND(xsd:double(str(?v)) AS ?num) FILTER(BOUND(?num)) }} }} "
                f"ORDER BY DESC(?num) ?s LIMIT 1"
            )
            top_rows = await select(top_q) or []
            subj = (top_rows[0].get("s") or {}) if top_rows else {}
            top_v = (top_rows[0].get("v") or {}).get("value") if top_rows else None
            if subj.get("type") == "uri" and top_v is not None:
                subject_iri = str(subj["value"])
                detail_q = (
                    f"SELECT ?p ?v WHERE {{ GRAPH <{staged_iri}> {{ "
                    f"<{subject_iri}> ?p ?v FILTER(isLiteral(?v)) }} }} ORDER BY ?p LIMIT 12"
                )
                details: list[dict[str, object]] = []
                for db in await select(detail_q) or []:
                    dp = db.get("p") or {}
                    dv = (db.get("v") or {}).get("value")
                    if dp.get("type") != "uri" or dv is None:
                        continue
                    if dp["value"] == top_iri:
                        continue  # the answer value itself is not context
                    details.append(
                        decorate(
                            {"predicate_iri": dp["value"], "value": dv},
                            str(dp["value"]),
                        )
                    )
                out["top"] = decorate(
                    {
                        "predicate_iri": top_iri,
                        "value": top_v,
                        "subject_iri": subject_iri,
                        "subject_details": details,
                        "sparql": top_q,
                    },
                    top_iri,
                )

        # 4) ADR fallback for shapes with no numeric field at all: real entity
        #    IRIs — of the biggest kind when classes exist, of any subject
        #    otherwise — so the "every fact has a permanent ID" promise is
        #    still demonstrated with the user's own data.
        if out["range"] is None and out["top"] is None:
            biggest = classes[0] if classes else None
            if biggest:
                sample_q = (
                    f"SELECT ?s WHERE {{ GRAPH <{staged_iri}> {{ "
                    f"?s a <{biggest['iri']}> }} }} ORDER BY ?s LIMIT 3"
                )
            else:
                sample_q = (
                    f"SELECT DISTINCT ?s WHERE {{ GRAPH <{staged_iri}> {{ "
                    f"?s ?p ?o FILTER(isIRI(?s)) }} }} ORDER BY ?s LIMIT 3"
                )
            iris = [
                str((b.get("s") or {}).get("value"))
                for b in await select(sample_q) or []
                if (b.get("s") or {}).get("type") == "uri"
            ]
            if iris:
                samples: dict[str, object] = {
                    "class_iri": biggest["iri"] if biggest else None,
                    "iris": iris,
                    "sparql": sample_q,
                }
                if biggest and biggest.get("label"):
                    samples["label"] = biggest["label"]
                out["samples"] = samples
        return out

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
            default=[], description="Design-time source file(s) (CSV or JSON)"
        ),
        staging_id: str = Form(
            default="",
            description="A staged source (POST /api/staging) to attach INSTEAD of files.",
        ),
    ) -> JSONResponse:
        """Persist the sources a dataset was designed from (reproducibility, Task E).

        Saved alongside the registry bundle (``<id>/source/``) so a *design*-stage
        dataset can later be ingested from the catalog with no re-attach. The
        workbench calls this right after materialize (step 3 保存). CSV and JSON
        sources are both accepted (#19). Overwrites any previously attached source.

        ``staging_id`` (ADR source-staging.md): attach what was staged at drop
        time — its RAW uploads replay through the very same converter as a fresh
        upload (an ``.xlsx`` keeps its original alongside, exactly as before),
        and the staging record is consumed.

        The response also carries ``validation_issues`` / ``advisories``
        RECOMPUTED against the source that was just attached. The kantan flow
        runs materialize → attach → ingest, so the first materialize mints the
        dataset and therefore has no source: "column X of your file is not used
        by this design" — the one thing that tells a user a weak model dropped
        17 of their 20 columns — could not be computed even once, and the value
        stored on the dataset at that moment was the source-less (empty) one.
        Answering it here means the caller learns it at the first moment it is
        knowable, with no extra round-trip and no LLM call.
        """
        existing = registry.load_dataset(cfg.registry_root, dataset_id)
        if existing is None:
            raise HTTPException(404, f"dataset {dataset_id!r} not found")
        if staging_id:
            try:
                sdir, _paths = staging.load(cfg.registry_root, staging_id)
            except staging.StagingNotFound as exc:
                raise HTTPException(404, f"staging {staging_id!r} not found (expired?)") from exc
            raw = staging.raw_paths(sdir)
            if not raw:
                raise HTTPException(404, f"staging {staging_id!r} holds no sources")
            uploads = _uploads_from_dir(sdir / "raw")
            # The raw workbook is re-converted here, so the sheet choice made at
            # S2 has to be re-applied — otherwise the design maps three sheets
            # and the dataset persists all seven (K6 / KZ-A-09).
            keep = {str(n) for n in (_staging_meta(sdir).get("sources") or [])} or None
            try:
                saved, meta = await _persist_source_uploads(
                    cfg.registry_root, dataset_id, uploads, keep
                )
            finally:
                for u in uploads:
                    await u.close()
            # Consumed: the dataset's source/ is now the durable home.
            staging.delete(cfg.registry_root, staging_id)
        else:
            if not files:
                raise HTTPException(400, "no source files uploaded")
            saved, meta = await _persist_source_uploads(cfg.registry_root, dataset_id, files)
        current = registry.load_dataset(cfg.registry_root, dataset_id) or existing
        rml_ttl = str((current.get("artifacts") or {}).get("mapping.rml.ttl") or "")
        decisions = _load_column_decisions(cfg.registry_root, dataset_id)
        if decisions:
            paths = registry.list_source_files(cfg.registry_root, dataset_id)
            source_names = {path.name for path in paths}
            source_columns = await asyncio.to_thread(_source_column_names, paths, rml_ttl)
            kept_decisions = [
                decision
                for decision in decisions
                if str(decision["source"]) in source_names
                and (
                    str(decision["source"]) not in source_columns
                    or str(decision["column"])
                    in source_columns[str(decision["source"])]
                )
            ]
            stale_decisions = [
                decision for decision in decisions if decision not in kept_decisions
            ]
            stale_includes = [
                decision
                for decision in stale_decisions
                if decision.get("action") == "include"
            ]
            if stale_includes:
                proposal_md = registry.load_proposal(cfg.registry_root, dataset_id) or ""
                try:
                    new_md, changed = remove_stale_column_includes_from_document(
                        proposal_md, stale_includes
                    )
                except ValueError as exc:
                    raise HTTPException(422, str(exc)) from exc
                if changed:
                    artifacts_raw, warnings = await asyncio.to_thread(
                        _artifacts_from_document,
                        new_md,
                        dataset_id,
                        registry.source_dir(cfg.registry_root, dataset_id),
                    )
                    artifacts = {key: value or "" for key, value in artifacts_raw.items()}
                    updated = registry.update_dataset_artifacts(
                        cfg.registry_root,
                        dataset_id,
                        artifacts,
                        complete=bool(current["meta"].get("complete")),
                        warnings=warnings,
                        traps=list(current["meta"].get("traps") or []),
                        exit_code=int(current["meta"].get("exit_code") or 0),
                        proposal_md=new_md,
                        advisories=list(current["meta"].get("advisories") or []),
                    )
                    if updated is None:
                        raise HTTPException(404, f"dataset {dataset_id!r} not found")
                    meta = updated
                    rml_ttl = artifacts["mapping.rml.ttl"]
            if stale_decisions:
                _remember_column_decisions(
                    cfg.registry_root, dataset_id, kept_decisions
                )
        try:
            issues, advisories, _dups = await asyncio.to_thread(
                _design_checks_at_materialize, cfg.registry_root, dataset_id, rml_ttl
            )
        except Exception:  # advice must never fail an otherwise-successful attach
            logger.exception("design checks after source attach failed (continuing)")
            issues, advisories = [], []
        return JSONResponse(
            {
                "dataset_id": dataset_id,
                "source_files": saved,
                "dataset": meta,
                "validation_issues": issues,
                "advisories": advisories,
            }
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
                "取り込むには元のファイル (CSV / JSON / XML) が必要です。"
                "設計時に置いたファイルをもう一度追加してください",
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
                # An empty RML usually means the §9 mapping spec did not compile
                # at materialize time (the design was still saved, with the
                # compile problems in its warnings). Return the SAME structured
                # 422 shape as design validation below, so every ingest surface
                # renders a readable bullet list with a fix path instead of the
                # opaque "no declarative RML mapping" string (the ZEM x gpt-oss
                # live dead-end, 2026-07-23) — the wizard additionally stops
                # this state at the design step now, making this the fallback
                # gate for older registries / direct API calls.
                meta_warnings = [
                    str(w) for w in ((data.get("meta") or {}).get("warnings") or [])
                ]
                raise HTTPException(
                    422,
                    detail={
                        "error": "no declarative RML mapping",
                        "issues": [
                            "this dataset has no compiled RML mapping to ingest — "
                            "the mapping spec (§9) did not compile to RML; re-run "
                            "the design (見直す) so the self-correction loop can "
                            "repair the spec",
                            *meta_warnings,
                        ],
                    },
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
                # Does the graph we just built say what the design said it would?
                # (ADR data-shape-checks.md) The existing gates stop at the design
                # boundary — columns exist, functions type-check — so a predicate
                # that materialized ZERO times, or a link whose target was never
                # minted, reaches the catalog looking healthy and answers every
                # question with silence. Advisory: the findings are persisted and
                # surfaced next to the design advisories; nothing is blocked. A
                # document dataset has no RML and is skipped inside the helper.
                if not is_document:
                    await _record_shape_findings(
                        cfg.registry_root, client, dataset_id, staged_iri, rml_ttl
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
            raise HTTPException(400, "まだ公開前の下書きに取り込まれていません。")
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
            # kantan S8 can reach this (publish pressed before the draft ingest
            # finished). The plain sentence and the "「確かめる」に戻る" button live
            # in the UI's error dictionary keyed by this code; the English
            # message stays for the folded technical view.
            raise _coded_error(
                400,
                "dataset.not_ingested",
                "dataset has no staged graph to promote (not ingested)",
            )
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
        # ADR ask-quality-and-generality.md: register this dataset's deterministic
        # trial queries as typed query tools, so Ask answers "how many / what range
        # / which is largest" from the verified path instead of asking a weak model
        # to compose SPARQL. Best-effort: a failure here must not undo the promote.
        try:
            trial = await dataset_trial_queries(dataset_id)
            tools = synthesize_query_tools_from_trial_queries(trial)
            if tools:
                await asyncio.to_thread(
                    write_registry_query_tools,
                    cfg.registry_root,
                    dataset_id,
                    tools,
                )
        except Exception:  # never block a promote on tool synthesis
            logger.exception("query tool synthesis failed for %s (continuing)", dataset_id)
        # ADR togomcp-auto-publish.md: project the vetted MIE into the togomcp
        # catalog (best-effort, opt-in via ASTERISM_TOGOMCP_DIR). Only promoted
        # data is ever published; the projection pins the CURRENT live graph.
        togomcp: dict[str, object] | None = None
        if cfg.togomcp_dir is not None:
            togomcp = await asyncio.to_thread(
                togomcp_sync.publish_dataset,
                cfg.togomcp_dir,
                dataset_id,
                str((data.get("artifacts") or {}).get("mie.yaml") or ""),
                staged_iri,
                endpoint_url=cfg.togomcp_endpoint_url,
                endpoint_name=cfg.togomcp_endpoint_name,
            )
        payload: dict[str, object] = {
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
        if togomcp is not None:
            payload["togomcp"] = togomcp
        return JSONResponse(payload)

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
            # S8 promises "間違いに気づいたら、いつでも引用対象から外せます" — the
            # sentence that promise fails with is read by the same person, so it
            # says what happened in their words, not the store's.
            raise HTTPException(
                400, "このデータはまだ公開されていないため、引用対象から外す操作はできません。"
            )
        canonical_iri = substrate.canonical_graph_iri(dataset_id)
        client: OxigraphClient = app.state.client
        now = datetime.now(UTC).isoformat()
        await substrate.retract_canonical(client, canonical_iri, invalidated_at=now)
        meta = registry.mark_retracted(cfg.registry_root, dataset_id, retracted_at=now)
        # A retracted dataset leaves the Ask scope — unlist it from the togomcp
        # catalog too (best-effort; reversed by /reinstate).
        if cfg.togomcp_dir is not None:
            await asyncio.to_thread(
                togomcp_sync.unpublish_dataset, cfg.togomcp_dir, dataset_id
            )
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
        # Reverse the retract-time unlisting: republish the MIE against the live
        # graph that just came back into scope (best-effort).
        if cfg.togomcp_dir is not None:
            live = await substrate.live_graph_of(client, canonical_iri) or canonical_iri
            await asyncio.to_thread(
                togomcp_sync.publish_dataset,
                cfg.togomcp_dir,
                dataset_id,
                str((data.get("artifacts") or {}).get("mie.yaml") or ""),
                live,
                endpoint_url=cfg.togomcp_endpoint_url,
                endpoint_name=cfg.togomcp_endpoint_name,
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
        # A deleted dataset must not linger in the togomcp catalog (best-effort).
        if cfg.togomcp_dir is not None:
            await asyncio.to_thread(
                togomcp_sync.unpublish_dataset, cfg.togomcp_dir, dataset_id
            )
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

    def _enrich_crosswalk_config_dict(config_dict: dict | None) -> dict | None:
        """Read-time DISPLAY enrichment for a crosswalk config response
        (XW-01/XW-04/XW-06): each participant gets the dataset's CURRENT name
        and a ``predicate_label``, each concept gets a ``concept_label`` —
        resolved fresh on every read via :func:`_crosswalk_predicate_label_resolver`.
        The persisted config (ids + predicate IRIs) is never touched, only this
        response dict — a rename or a redesign is reflected without a migration.
        """
        if config_dict is None:
            return None
        names = {
            str(m.get("id")): str(m.get("name") or m.get("id"))
            for m in registry.list_datasets(cfg.registry_root)
        }
        label_of = _crosswalk_predicate_label_resolver(cfg.registry_root)
        for concept in config_dict.get("concepts") or []:
            resolved: list[str] = []
            for p in concept.get("participants") or []:
                ds_id = str(p.get("dataset_id") or "")
                if ds_id in names:
                    p["name"] = names[ds_id]
                preds = (
                    [p["predicate"]]
                    if p.get("predicate")
                    else list((p.get("predicates") or {}).values())
                )
                label = next(
                    (got for pred in preds if (got := label_of(ds_id, pred))), None
                )
                if label:
                    p["predicate_label"] = label
                    if label not in resolved:
                        resolved.append(label)
            concept["concept_label"] = resolved[0] if len(resolved) == 1 else " / ".join(resolved)
        return config_dict

    def _crosswalk_view(perspective_id: str) -> dict:
        config = crosswalk_runtime.load_config(cfg.registry_root, perspective_id)
        data = registry.load_dataset(
            cfg.registry_root, crosswalk_runtime.crosswalk_registry_id(perspective_id)
        )
        return {
            "perspective_id": perspective_id,
            "exists": config is not None,
            "config": _enrich_crosswalk_config_dict(
                crosswalk_runtime.config_to_dict(config) if config else None
            ),
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
                    "config": _enrich_crosswalk_config_dict(
                        crosswalk_runtime.config_to_dict(config) if config else None
                    ),
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

    @app.post("/api/crosswalk/discover", dependencies=_write_auth)
    async def crosswalk_discover_route(body: CrosswalkDiscoverBody) -> JSONResponse:
        """Find the crosswalks that COULD exist, from the data itself.

        Compares the promoted datasets' actual values under the closed normalizer set
        and returns ranked candidates — which datasets connect, on what, how many
        values match, and the real spellings as evidence. **No LLM and no API key**
        (kantan-mode ADR K5: the entrance must not be a key prompt), and no writes:
        every candidate carries a ``build_config`` the human can build as-is, so the
        only decision left is *which one* (K13).

        Runs as a JOB: the query count grows with datasets x predicates, so a large
        deployment would otherwise hang a request with no progress and no way out.
        Progress frames stream over ``/api/jobs/{id}/stream``; cancel is cooperative.
        """
        limits = crosswalk_discover.DiscoverLimits(
            max_datasets=body.max_datasets,
            max_predicates_per_dataset=body.max_predicates_per_dataset,
            max_values_per_predicate=body.max_values_per_predicate,
            min_datasets=body.min_datasets,
            min_shared_keys=body.min_shared_keys,
            max_candidates=body.max_candidates,
        )
        targets, skipped, truncated = _discover_targets(
            cfg.registry_root, body.dataset_ids, body.max_datasets
        )
        client: OxigraphClient = app.state.client
        existing_perspectives = {
            meta.get("crosswalk_perspective_id") or crosswalk_runtime.DEFAULT_PERSPECTIVE_ID
            for meta in crosswalk_runtime.list_perspectives(cfg.registry_root)
        }

        async def discover_job(emit, should_cancel):
            result = await crosswalk_discover.discover(
                client,
                targets,
                limits=limits,
                datasets_truncated=truncated,
                skipped_datasets=skipped,
                progress=lambda phase, payload: emit(phase=phase, **payload),
                should_cancel=should_cancel,
                predicate_label_of=_crosswalk_predicate_label_resolver(cfg.registry_root),
            )
            # Building a candidate whose id already exists REPLACES that crosswalk —
            # the UI has to be able to warn before that happens.
            for cand in result["candidates"]:
                cand["perspective_exists"] = cand["perspective_id"] in existing_perspectives
            return result

        job_manager: JobManager = app.state.jobs
        return JSONResponse({"job_id": job_manager.start_coro(discover_job)}, status_code=202)

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

    @app.get("/api/units/resolve")
    async def units_resolve(
        q: str = Query(description="unit string as a person typed it, e.g. 'W/(m*K)'"),
        limit: int = Query(default=6, ge=1, le=20),
    ) -> JSONResponse:
        """Does this unit string land on a REAL standard unit? (units.py)

        A unit is not one attribute among many: "300" alone is not a citable fact, and
        no RDF datatype carries the unit. Until now a person could type any spelling in
        the かんたん column table and nothing said whether it reached a standard — the
        QUDT triple just quietly did not appear. This answers it: ``resolved`` (with the
        real IRI), ``ambiguous`` (the string means several units — a person picks), or
        ``unknown`` plus near-miss suggestions. Closed set, deterministic, read-only.
        """
        res = grounding.resolve_unit(q, limit=limit)
        return JSONResponse({**res.to_dict(), "catalog": grounding.catalog_meta()})

    @app.get("/api/quantitykinds/resolve")
    async def quantity_kinds_resolve(
        q: str = Query(default="", description="the column's name or label"),
        unit: str = Query(default="", description="QUDT unit local name, e.g. 'V-PER-K'"),
        limit: int = Query(default=8, ge=1, le=50),
    ) -> JSONResponse:
        """What quantity is this column measuring? (quantity_kinds.py)

        The other half of `/api/units/resolve`: that answers "in what", this answers
        "of what". A dataset whose units reach the standard but whose PROPERTIES do not
        is half-connected — the quantity is what other people search on ("who else
        measured thermal conductivity?").

        Pass the unit already resolved for the column: it ranks name matches higher and,
        on its own, offers the quantities that unit can express — which is how a column
        called `S` still reaches Seebeck coefficient. Closed set (every candidate is a
        real QUDT IRI, never fabricated), deterministic, read-only; a human confirms.
        """
        if not q.strip() and not unit.strip():
            raise HTTPException(400, "q or unit is required")
        cands = grounding.resolve_quantity_kind(q, unit=unit or None, limit=limit)
        return JSONResponse(
            {
                "query": q,
                "unit": unit,
                "candidates": [c.to_dict() for c in cands],
                "catalog": grounding.quantity_kind_catalog_meta(),
            }
        )

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
