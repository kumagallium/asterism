"""Declarative-substrate ingestion: run an approved RML mapping through Morph-KGC
and load the result into an isolated *draft* named graph in Oxigraph (#15 gate).

The human gate is two-step (see
``docs/architecture/phase5-workbench-materialize-gate.md``):

1. ``materialize`` saves the RML *draft* to the registry — reviewable, NOT run.
2. On explicit human approval, this module runs Morph-KGC on the dataset's
   persisted CSVs + RML and loads the RDF into a **draft named graph**.

Draft data is deliberately isolated. Ask cites the *canonical* graph by default,
so unreviewed workbench output never silently becomes a citable fact. Promoting a
draft graph to canonical (including ontology alignment / merge) is a separate,
later gate.

No generated code runs: Morph-KGC interprets the declarative mapping, and the
only functions it may call are the closed Tier 0 set in :mod:`asterism.functions`.
That invariant — plus the rule that a mapping may read only confined CSV/JSON
files (no DuckDB ``rml:query``, no absolute / traversing / URL sources) — is
enforced **fail-closed at runtime** by :func:`asterism.rml_safety.assert_rml_safe`,
called immediately before Morph-KGC is invoked (the offline step0 trap T9 is a
CI convenience, not the trust boundary). ``morph-kgc`` is an optional dependency
— install with ``pip install 'asterism-ingest[substrate]'``.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

# Re-exported so callers can `substrate.assert_rml_safe` / catch `substrate.RmlSafetyError`.
from asterism.rml_safety import RmlSafetyError as RmlSafetyError
from asterism.rml_safety import assert_rml_safe as assert_rml_safe

# Re-exported so callers can `substrate.validate_rml_design` / catch
# `substrate.RmlValidationError` (mapped to a 422 with a structured `issues` list).
from asterism.rml_validate import RmlValidationError as RmlValidationError
from asterism.rml_validate import design_advisories as design_advisories
from asterism.rml_validate import validate_rml_design as validate_rml_design
from asterism.tabularize import sanitize_csv_columns, tabularize_json_to_csv

# Draft graphs live under /graph/draft/<id>, trivially distinguishable from the
# canonical per-kind graphs (.../graph/curves, .../graph/papers, ...).
GRAPH_BASE = "https://kumagallium.github.io/asterism/starrydata/graph/"
_DATASET_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# rml:source is resolved relative to the CWD by Morph-KGC. Rather than chdir
# (process-global state — unsafe under the api's thread pool), we rewrite
# *relative* sources to absolute paths under the dataset's CSV dir. Thread-safe.
_RML_SOURCE = re.compile(r'(rml:source\s+")([^"]+)(")')

# Morph-KGC's FnO function-execution vocab (functionExecution / function / input
# / parameter / inputValueMap) lives at the *new* RML namespace below. AI-authored
# RML sometimes declares its function prefix against the *old* FnO namespace URI,
# which Morph-KGC silently ignores (the function objectMaps then yield no object →
# a cryptic ``KeyError: 'object'`` at materialize). We normalize the URI so RML
# written against either namespace works.
_FNO_OLD_NS = "http://semweb.mmlab.be/ns/fnml#"
_FNO_NEW_NS = "http://w3id.org/rml/"

# Default UDF-registration file shipped with the package. Morph-KGC loads it by
# path and registers the closed Tier 0 set via asterism.functions.register.
_DEFAULT_UDFS = Path(__file__).with_name("substrate_udfs.py")


class SupportsTurtlePost(Protocol):
    """The slice of :class:`asterism.oxigraph_client.OxigraphClient` we need."""

    async def post_turtle_bytes(self, payload: bytes, graph_iri: str | None = None) -> int: ...


def draft_graph_iri(dataset_id: str) -> str:
    """Named graph for workbench-ingested (unreviewed) data.

    Raises ``ValueError`` for ids that would not be IRI-safe (the registry
    controls the id format, but we re-check at the trust boundary).
    """
    if not _DATASET_ID.match(dataset_id):
        raise ValueError(f"unsafe dataset_id for graph IRI: {dataset_id!r}")
    return f"{GRAPH_BASE}draft/{dataset_id}"


# ----------------------------------------------------------------------------
# #20 P3: lifecycle graph layout (introduced ahead of the read-model flip)
# ----------------------------------------------------------------------------
#
# canonical / ontology / control graphs span *all* datasets, so they live under a
# dataset-neutral graph namespace — distinct from the starrydata-flavored
# ``GRAPH_BASE`` used by the legacy per-kind + draft graphs. Graph IRIs are
# storage containers, NOT data identity (entity IRIs stay immutable), so this
# neutral scheme is safe to introduce. These helpers are defined now so the IRI
# scheme is fixed; the promote target change + read-path flip land in P3 step 2.
#
# Per ADR §3.1: per-dataset canonical graph makes retract / re-promote / delete
# clean graph-scoped operations; control graph holds tombstones (retract /
# delete markers); ontology graph holds the projected TBox (§2).
LIFECYCLE_GRAPH_BASE: str = "https://kumagallium.github.io/asterism/graph/"
CANONICAL_GRAPH_BASE: str = LIFECYCLE_GRAPH_BASE + "canonical/"
ONTOLOGY_GRAPH_BASE: str = LIFECYCLE_GRAPH_BASE + "ontology/"
CONTROL_GRAPH_IRI: str = LIFECYCLE_GRAPH_BASE + "control"

# Control vocabulary (asterism: namespace) for the lifecycle status of a dataset.
ASTERISM_NS: str = "https://kumagallium.github.io/asterism/vocab#"
STATUS_PREDICATE: str = ASTERISM_NS + "status"
INVALIDATED_PREDICATE: str = "http://www.w3.org/ns/prov#invalidatedAtTime"
STATUS_ACTIVE: str = "active"
# A canonical graph is *citable* iff the control graph flags it "promoted"
# (memory-bounded promote — the flag, not graph existence, gates the Ask scope, so
# a freshly-ingested-but-unreviewed graph is excluded without a MOVE). retracted /
# deleted are the two ways a once-promoted graph leaves the scope.
STATUS_PROMOTED: str = "promoted"
STATUS_RETRACTED: str = "retracted"
STATUS_DELETED: str = "deleted"

# part5 (versioned data graphs): a dataset's *key* graph IRI ``canonical/{id}`` is
# the control subject; its citable data lives in a per-ingest *version* graph
# ``canonical/{id}/v{n}`` that the control graph points at via ``liveGraph``. This
# takes the large DROP off the critical path for replace / delete: a re-ingest
# streams a NEW version alongside the live one, promote swaps the pointer (O(1)),
# and the superseded version is dropped in the background (``pendingDrop`` queue).
# Datasets promoted before part5 (data in the key graph, no ``liveGraph``) keep
# working — ``canonical_graphs`` falls back to the key graph when no live pointer.
LIVE_GRAPH_PREDICATE: str = ASTERISM_NS + "liveGraph"
STAGED_GRAPH_PREDICATE: str = ASTERISM_NS + "stagedGraph"
PENDING_DROP_PREDICATE: str = ASTERISM_NS + "pendingDrop"


def canonical_graph_iri(dataset_id: str) -> str:
    """Per-dataset canonical *key* graph IRI (the control subject; also the data
    graph for datasets promoted before part5's versioned graphs)."""
    if not _DATASET_ID.match(dataset_id):
        raise ValueError(f"unsafe dataset_id for graph IRI: {dataset_id!r}")
    return f"{CANONICAL_GRAPH_BASE}{dataset_id}"


def versioned_graph_iri(dataset_id: str, version: int) -> str:
    """Per-ingest versioned data graph IRI ``…/canonical/{id}/v{n}`` (part5).

    Each ingest streams into a fresh version graph; promote points the dataset's
    ``liveGraph`` at it. ``version`` is a monotonic per-dataset sequence (it never
    reuses a number even after old versions are dropped, so graphs never collide).
    """
    if not _DATASET_ID.match(dataset_id):
        raise ValueError(f"unsafe dataset_id for graph IRI: {dataset_id!r}")
    if not isinstance(version, int) or version < 1:
        raise ValueError(f"version must be a positive int, got {version!r}")
    return f"{CANONICAL_GRAPH_BASE}{dataset_id}/v{version}"


def ontology_graph_iri(dataset_id: str) -> str:
    """Per-dataset ontology (projected TBox) named graph IRI (§2)."""
    if not _DATASET_ID.match(dataset_id):
        raise ValueError(f"unsafe dataset_id for graph IRI: {dataset_id!r}")
    return f"{ONTOLOGY_GRAPH_BASE}{dataset_id}"


def absolutize_rml_sources(rml_ttl: str, csv_dir: Path | str) -> str:
    """Rewrite relative ``rml:source "name"`` to absolute paths under ``csv_dir``.

    Already-absolute sources are left untouched. This lets Morph-KGC resolve the
    CSVs without a process-global ``chdir``.
    """
    base = Path(csv_dir)

    def repl(m: re.Match[str]) -> str:
        src = m.group(2)
        if Path(src).is_absolute():
            return m.group(0)
        return f"{m.group(1)}{base / src}{m.group(3)}"

    return _RML_SOURCE.sub(repl, rml_ttl)


def normalize_fno_namespace(rml_ttl: str) -> str:
    """Rewrite the old FnO namespace URI to the Morph-KGC-supported RML one.

    No-op for RML already written against ``http://w3id.org/rml/``.
    """
    return rml_ttl.replace(_FNO_OLD_NS, _FNO_NEW_NS)


# A JSON source is tabularized to a CSV of JSON-string cells at the ingest boundary
# so the closed Tier 0 exploders (json_pluck / json_array / split) can read its
# nested arrays — Morph-KGC's native JSON reader cannot explode a nested array and
# link it to its parent (it drops object arrays and truncates scalar arrays to the
# first element). Decision: docs/architecture/native-json-denormalization.md.
#
# The signal is purely declarative: a mapping that wants the tabularized form
# references ``<name>.csv``; if that CSV is absent but a sibling ``<name>.json`` /
# ``<name>.geojson`` is present, we derive it here. A legacy mapping that reads JSON
# natively references ``<name>.json`` with ``ql:JSONPath`` and is left untouched (no
# ``.csv`` is requested), so this is backward-compatible.
_JSON_SOURCE_SUFFIXES = (".json", ".geojson")


def tabularize_json_sources(rml_ttl: str, csv_dir: Path | str, work_dir: Path | str) -> str:
    """Derive a tabularized CSV for any ``.csv`` source the mapping references that
    is absent on disk but backed by a sibling JSON file, rewriting that source to the
    derived CSV's absolute path. Sources with a real CSV present, absolute sources,
    and non-CSV sources are returned unchanged (resolved later by
    :func:`absolutize_rml_sources`).
    """
    base = Path(csv_dir)
    work = Path(work_dir)

    def repl(m: re.Match[str]) -> str:
        name = Path(m.group(2))
        if name.is_absolute() or name.suffix.lower() != ".csv" or (base / name.name).exists():
            return m.group(0)
        for suffix in _JSON_SOURCE_SUFFIXES:
            json_src = base / (name.stem + suffix)
            if json_src.exists():
                dest = work / name.name
                tabularize_json_to_csv(json_src, dest)
                return f"{m.group(1)}{dest}{m.group(3)}"
        return m.group(0)

    return _RML_SOURCE.sub(repl, rml_ttl)


def sanitize_csv_sources(rml_ttl: str, csv_dir: Path | str, work_dir: Path | str) -> str:
    """Rewrite a *direct* ``.csv`` source whose header carries a reserved column
    (``subject`` / ``predicate``) to a work-dir copy with the header sanitized,
    matching the renamed selectors the inspector/propose emit. Sources with no
    reserved column, absolute sources, and absent sources are left unchanged
    (resolved later by :func:`absolutize_rml_sources`). Runs after
    :func:`tabularize_json_sources`, so JSON-backed sources (already absolute and
    safe_col-applied) are skipped here.
    """
    base = Path(csv_dir)
    work = Path(work_dir)

    def repl(m: re.Match[str]) -> str:
        name = Path(m.group(2))
        if name.is_absolute() or name.suffix.lower() != ".csv":
            return m.group(0)
        src = base / name.name
        if not src.exists():
            return m.group(0)
        dest = work / name.name
        if sanitize_csv_columns(src, dest):
            return f"{m.group(1)}{dest}{m.group(3)}"
        return m.group(0)

    return _RML_SOURCE.sub(repl, rml_ttl)


# A UTF-8 BOM (EF BB BF) at the head of a CSV makes pandas — Morph-KGC's CSV
# reader — read the *first* header as ``﻿SID`` instead of ``SID``. The RML
# (authored from the BOM-stripped inspect, which opens CSVs ``utf-8-sig``) names
# the clean column, so Morph-KGC can't find it ("columns expected but not found:
# ['SID']"). We strip a leading BOM into a work-dir copy at the ingest boundary so
# Morph-KGC reads the clean header. Only the 3 BOM bytes are checked/stripped.
_UTF8_BOM: bytes = b"\xef\xbb\xbf"


def strip_bom_sources(rml_ttl: str, csv_dir: Path | str, work_dir: Path | str) -> str:
    """Rewrite a *direct* ``.csv`` source whose file starts with a UTF-8 BOM to a
    work-dir copy with the BOM stripped, so Morph-KGC's pandas reader sees the clean
    first-column name the RML references. Sources with no BOM, absolute sources, and
    absent sources are left unchanged (resolved later by
    :func:`absolutize_rml_sources`). Runs after :func:`sanitize_csv_sources`, so a
    source already rewritten to an absolute work-dir copy is skipped here (the copy
    was written ``utf-8`` with no BOM); only the cheap 3-byte head is read to decide,
    and the strip is a streamed byte copy (memory-bounded for large CSVs).
    """
    base = Path(csv_dir)
    work = Path(work_dir)

    def repl(m: re.Match[str]) -> str:
        name = Path(m.group(2))
        if name.is_absolute() or name.suffix.lower() != ".csv":
            return m.group(0)
        src = base / name.name
        if not src.exists():
            return m.group(0)
        with src.open("rb") as fh:
            if fh.read(len(_UTF8_BOM)) != _UTF8_BOM:
                return m.group(0)  # no BOM -> read the original in place
            dest = work / name.name
            with dest.open("wb") as out:
                for chunk in iter(lambda: fh.read(1 << 16), b""):
                    out.write(chunk)
        return f"{m.group(1)}{dest}{m.group(3)}"

    return _RML_SOURCE.sub(repl, rml_ttl)


# AI-authored RML mints the per-run ingestion-activity IRI with a template like
# ``rr:template ".../activity/ingest/{__run_id__}"``. ``__run_id__`` is NOT a CSV
# column — it's a runtime value the hardcoded ingester supplies programmatically
# (``asterism.starrydata`` uses ``run-<UTC timestamp>``). Morph-KGC, seeing the
# template reference, adds ``__run_id__`` to pandas' usecols → "columns expected
# but not found: ['__run_id__']". We substitute the placeholder with one generated
# run-id before Morph-KGC runs, so every row shares one constant ingestion-activity
# IRI (one activity per ingest run — semantically correct).
_RUN_ID_PLACEHOLDER: str = "{__run_id__}"

# A template whose ONLY reference was ``{__run_id__}`` becomes a constant after
# substitution — but Morph-KGC rejects a reference-free ``rr:template`` ("invalid
# template"). So such a template is rewritten to ``rr:constant <IRI>`` (an IRI node,
# which yields a URIRef object — the activity IRI is an entity, not a literal). A
# template that ALSO references a real column (e.g. ``.../{SID}/ingest/{__run_id__}``)
# keeps a reference after substitution, so it stays a valid ``rr:template``.
# Matches ``rr:template "<...{__run_id__}...>"`` (quoted literal value).
_RR_TEMPLATE_WITH_RUN_ID: re.Pattern[str] = re.compile(
    r'rr:template\s+"([^"]*\{__run_id__\}[^"]*)"'
)
_TEMPLATE_REF: re.Pattern[str] = re.compile(r"(?<!\\)\{")


def generate_run_id() -> str:
    """A run-id literal for one ingest run: ``run-<UTC timestamp>`` (matches the
    hardcoded ingester's format in :mod:`asterism.starrydata`)."""
    return "run-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def batch_fingerprint(files: Iterable[tuple[str, bytes]]) -> str:
    """A deterministic content fingerprint (hex sha256) for an append batch.

    Order-independent over the batch's ``(filename, bytes)`` files (a batch is a
    *set* of source files), and unambiguous (each field length-delimited so
    ``("a", b"bc")`` and ``("ab", b"c")`` never collide). Identical batch content
    → identical fingerprint, so a retried batch (ADR incremental-ingest §3 / A3)
    is recognised as the same batch regardless of when or how many times it is
    re-delivered — the seam that makes both provenance (a content-derived run-id,
    :func:`run_id_for_batch`) and source accumulation idempotent.
    """
    h = hashlib.sha256()
    for name, content in sorted(files):
        h.update(str(len(name)).encode("ascii"))
        h.update(b":")
        h.update(name.encode("utf-8"))
        h.update(str(len(content)).encode("ascii"))
        h.update(b":")
        h.update(content)
    return h.hexdigest()


def run_id_for_batch(fingerprint: str) -> str:
    """A *content-derived* append run-id ``run-<hex16>`` from a batch fingerprint.

    Unlike :func:`generate_run_id` (a fresh timestamp per call), this is a pure
    function of the batch content, so a retried batch re-mints the **same**
    ingestion-activity IRI via ``{__run_id__}``. Set-semantics then dedupe it on
    the POST-merge instead of leaving an orphan activity/provenance subtree per
    attempt — the append path's determinism requirement (ADR incremental-ingest
    §3, A8: the data graph stays a pure, idempotent materialization). Keeps the
    ``run-`` shape the hardcoded ingester and template substitution expect; hex is
    IRI-safe. 16 hex chars (64 bits) is ample to keep distinct batches distinct.
    """
    return "run-" + fingerprint[:16]


def substitute_run_id(rml_ttl: str, run_id: str | None = None) -> str:
    """Substitute the runtime-only ``{__run_id__}`` template placeholder.

    ``__run_id__`` is a runtime value (no such CSV column), so left in place it
    crashes Morph-KGC's usecols check. We replace it with one generated run-id, so
    every row shares the same ingestion-activity IRI — the intended semantics (one
    activity per ingest run). A ``rr:template`` whose only reference was the
    placeholder is reference-free after substitution (which Morph-KGC rejects), so it
    is rewritten to ``rr:constant <IRI>``; a template that still references a real
    column stays a ``rr:template``. No-op for RML without the placeholder. ``run_id``
    defaults to a fresh :func:`generate_run_id`; pass an existing one to reuse a
    run-id already threaded through the call. Only the exact ``{__run_id__}`` token is
    touched — never another ``{column}`` reference.
    """
    if _RUN_ID_PLACEHOLDER not in rml_ttl:
        return rml_ttl
    rid = run_id or generate_run_id()

    def repl(m: re.Match[str]) -> str:
        resolved = m.group(1).replace(_RUN_ID_PLACEHOLDER, rid)
        if _TEMPLATE_REF.search(resolved):
            # Still has a real {column} reference → a valid template.
            return f'rr:template "{resolved}"'
        # Reference-free → emit an IRI constant (Morph-KGC rejects empty templates,
        # and an IRI node keeps the activity a URIRef, not a literal).
        return f"rr:constant <{resolved}>"

    return _RR_TEMPLATE_WITH_RUN_ID.sub(repl, rml_ttl)


def rml_source_names(rml_ttl: str) -> set[str]:
    """Basenames of every ``rml:source "..."`` declared in the mapping.

    The incremental append path materializes a *batch* directory holding only the new
    rows; this lets the caller check that a batch file's name matches a source the RML
    actually reads, so a mis-named batch fails loudly instead of silently materializing
    0 triples. Basenames, so a relative or absolute ``rml:source`` both match an
    uploaded batch file name.
    """
    return {Path(m.group(2)).name for m in _RML_SOURCE.finditer(rml_ttl)}


def materialize_to_graph(
    rml_ttl: str,
    csv_dir: Path | str,
    *,
    udfs_path: Path | str | None = None,
    work_dir: Path | str | None = None,
):  # -> rdflib.Graph (annotation omitted to avoid importing rdflib at module load)
    """Run Morph-KGC on ``rml_ttl`` (sources resolved under ``csv_dir``).

    Returns the produced ``rdflib.Graph``. Raises ``RuntimeError`` if ``morph-kgc``
    is not installed (it is the optional ``substrate`` extra),
    :class:`RmlSafetyError` if the mapping references non-Tier-0 code or an
    out-of-bounds source (validated fail-closed before Morph-KGC is invoked), or
    :class:`RmlValidationError` if a referenced column is absent from its CSV or a
    function execution has a wrong/missing parameter (a structured ``issues`` list,
    so the failure is a clear message rather than a cryptic Morph-KGC crash).
    """
    try:
        import morph_kgc
    except ImportError as exc:  # optional dependency
        raise RuntimeError(
            "morph-kgc is required for substrate ingestion; "
            "install with: pip install 'asterism-ingest[substrate]'"
        ) from exc

    # Trust boundary: validate the mapping before it can reach Morph-KGC.
    assert_rml_safe(rml_ttl, csv_dir)
    udfs = Path(udfs_path) if udfs_path else _DEFAULT_UDFS
    work = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="c2r-substrate-"))
    work.mkdir(parents=True, exist_ok=True)
    mapping_file = work / "mappings.rml.ttl"
    run_id_subst = substitute_run_id(rml_ttl)
    tabularized = tabularize_json_sources(run_id_subst, csv_dir, work)
    sanitized = sanitize_csv_sources(tabularized, csv_dir, work)
    bom_stripped = strip_bom_sources(sanitized, csv_dir, work)
    prepared = normalize_fno_namespace(absolutize_rml_sources(bom_stripped, csv_dir))
    # Design validation: catch a column-reference typo or a wrong/missing function
    # parameter against the REAL CSVs + Tier 0 signatures, with a clear `issues`
    # list, instead of letting it surface as a cryptic Morph-KGC crash. Runs on the
    # prepared RML (sources already absolute, {__run_id__} already substituted) so
    # the columns checked are exactly what Morph-KGC will read.
    validate_rml_design(prepared, csv_dir)
    mapping_file.write_text(prepared, encoding="utf-8")

    config = (
        "[CONFIGURATION]\n"
        f"udfs: {udfs}\n"
        "[DataSource1]\n"
        f"mappings: {mapping_file}\n"
    )
    return morph_kgc.materialize(config)


async def ingest_graph_to_oxigraph(
    graph,  # rdflib.Graph
    client: SupportsTurtlePost,
    graph_iri: str,
) -> int:
    """Serialize ``graph`` to Turtle and load it into ``graph_iri``.

    Returns the number of triples loaded. Oxigraph's set semantics make a
    re-ingest of the same graph idempotent.
    """
    payload = graph.serialize(format="turtle")
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    await client.post_turtle_bytes(payload, graph_iri=graph_iri)
    return len(graph)


# ----------------------------------------------------------------------------
# Scalable streaming path (ADR scalable-declarative-ingestion.md)
# ----------------------------------------------------------------------------
# The functions above build the whole rdflib Graph in memory and POST it in one
# request — fine for design-sized subsets, but a large dataset (millions of
# triples) exhausts memory / times out the single POST. The streaming pair below
# scales: Morph-KGC writes N-Triples to a *file* (its memory-bounded CLI path),
# then we load that file into Oxigraph in row-chunked POSTs with progress.

# Default rows per /store POST. N-Triples lines are independent (absolute IRIs),
# so any contiguous slice is a valid payload; this bounds per-request size.
DEFAULT_CHUNK_LINES = 50_000


def materialize_to_nt_file(
    rml_ttl: str,
    csv_dir: Path | str,
    *,
    udfs_path: Path | str | None = None,
    work_dir: Path | str | None = None,
    run_id: str | None = None,
) -> Path:
    """Run Morph-KGC and write the triples to an N-Triples file; return its path.

    Unlike :func:`materialize_to_graph` (whole graph in memory), this invokes
    Morph-KGC's file-output CLI (``python -m morph_kgc <config>`` with
    ``output_file``) so triples stream to disk — the memory-bounded path Morph-KGC
    itself recommends for large data. ``number_of_processes: 1`` avoids
    multiprocessing (portable + safe UDF import). Raises ``RuntimeError`` if
    ``morph-kgc`` is absent or materialization fails (caller maps a failure to a
    user-facing 4xx, as with :func:`materialize_to_graph`). Raises
    :class:`RmlSafetyError` if the mapping references non-Tier-0 code or an
    out-of-bounds source (validated fail-closed before Morph-KGC is invoked), or
    :class:`RmlValidationError` if a referenced column is absent from its CSV or a
    function execution has a wrong/missing parameter (a structured ``issues`` list).
    """
    try:
        import morph_kgc  # noqa: F401  (presence check; the CLI does the work)
    except ImportError as exc:  # optional dependency
        raise RuntimeError(
            "morph-kgc is required for substrate ingestion; "
            "install with: pip install 'asterism-ingest[substrate]'"
        ) from exc

    # Trust boundary: validate the mapping before it can reach Morph-KGC.
    assert_rml_safe(rml_ttl, csv_dir)
    work = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="c2r-substrate-"))
    work.mkdir(parents=True, exist_ok=True)
    udfs = Path(udfs_path) if udfs_path else _DEFAULT_UDFS
    mapping_file = work / "mappings.rml.ttl"
    # ``run_id`` pins the ``{__run_id__}`` ingestion-activity IRI. The append path
    # passes a content-derived id (:func:`run_id_for_batch`) so a retried batch
    # re-mints the SAME activity (idempotent); None keeps the per-call fresh
    # timestamp used by snapshot ingest (each version graph is its own activity).
    run_id_subst = substitute_run_id(rml_ttl, run_id)
    tabularized = tabularize_json_sources(run_id_subst, csv_dir, work)
    sanitized = sanitize_csv_sources(tabularized, csv_dir, work)
    bom_stripped = strip_bom_sources(sanitized, csv_dir, work)
    prepared = normalize_fno_namespace(absolutize_rml_sources(bom_stripped, csv_dir))
    # Design validation before Morph-KGC (see materialize_to_graph): turn a missing
    # column / wrong function parameter into a structured RmlValidationError instead
    # of an opaque engine exit.
    validate_rml_design(prepared, csv_dir)
    mapping_file.write_text(prepared, encoding="utf-8")
    out = work / "out.nt"
    config_file = work / "config.ini"
    config_file.write_text(
        "[CONFIGURATION]\n"
        f"udfs: {udfs}\n"
        f"output_file: {out}\n"
        "number_of_processes: 1\n"
        "[DataSource1]\n"
        f"mappings: {mapping_file}\n",
        encoding="utf-8",
    )
    # The vetted Morph-KGC CLI on a declarative config (no generated code).
    proc = subprocess.run(
        [sys.executable, "-m", "morph_kgc", str(config_file)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-5:])
        raise RuntimeError(
            f"Morph-KGC materialization failed (exit {proc.returncode}): {tail}"
        )
    # A 0-triple result may leave no file; the contract is "path to an .nt file".
    out.touch(exist_ok=True)
    return out


def count_nt_lines(nt_path: Path | str) -> int:
    """Triple count of an N-Triples file (one triple per line; 0 if absent)."""
    path = Path(nt_path)
    if not path.exists():
        return 0
    with path.open("rb") as fh:
        return sum(1 for _ in fh)


async def stream_nt_file_to_oxigraph(
    nt_path: Path | str,
    client: SupportsTurtlePost,
    graph_iri: str,
    *,
    chunk_lines: int = DEFAULT_CHUNK_LINES,
    on_progress: Callable[[int, int], None] | None = None,
) -> int:
    """Load an N-Triples file into ``graph_iri`` in row-chunked ``/store`` POSTs.

    Bounded memory (one chunk in flight). Each chunk is valid Turtle (N-Triples is
    a Turtle subset), posted via ``post_turtle_bytes`` whose Graph Store POST
    *appends*, so the chunks accumulate in the same graph. ``on_progress(done,
    total)`` is called after each chunk (for SSE progress). Returns triples loaded.
    """
    path = Path(nt_path)
    total = count_nt_lines(path)
    if total == 0:
        if on_progress is not None:
            on_progress(0, 0)
        return 0
    done = 0
    buf: list[bytes] = []

    async def flush() -> None:
        nonlocal done, buf
        if not buf:
            return
        await client.post_turtle_bytes(b"".join(buf), graph_iri=graph_iri)
        done += len(buf)
        buf = []
        if on_progress is not None:
            on_progress(done, total)

    with path.open("rb") as fh:
        for line in fh:
            buf.append(line)
            if len(buf) >= chunk_lines:
                await flush()
        await flush()
    return done


async def run_substrate_ingest(
    rml_ttl: str,
    csv_dir: Path | str,
    client: SupportsTurtlePost,
    dataset_id: str,
    *,
    udfs_path: Path | str | None = None,
) -> dict[str, object]:
    """End-to-end: Morph-KGC materialize → load into the draft graph for ``dataset_id``.

    Returns ``{"graph_iri": ..., "triple_count": ...}``. The Morph-KGC step runs
    synchronously; callers in async contexts should wrap it in ``to_thread`` for
    the materialize portion if it becomes heavy.
    """
    graph_iri = draft_graph_iri(dataset_id)  # validate id before doing work
    graph = materialize_to_graph(rml_ttl, csv_dir, udfs_path=udfs_path)
    triple_count = await ingest_graph_to_oxigraph(graph, client, graph_iri)
    return {"graph_iri": graph_iri, "triple_count": triple_count}


# ----------------------------------------------------------------------------
# Incremental append (ADR incremental-ingest.md) — grow a live feed in place
# ----------------------------------------------------------------------------
#
# Snapshot ingest re-materializes the whole source set into a fresh version graph
# (O(total)). For a device that emits CSV continuously, that is quadratic. The
# append path materializes ONLY the new batch (O(new rows)) and POST-merges it into
# the dataset's *already-live* canonical graph, so re-emitted rows dedupe by their
# deterministic IRIs (set semantics) and existing triples/IRIs are untouched. The
# trust model is unchanged: same Morph-KGC + Tier 0 substrate (no generated code),
# and the append is a Graph Store POST (the ingest write path), never a SPARQL
# UPDATE, so the read-only escape stays read-only.


async def run_append_ingest(
    rml_ttl: str,
    csv_dir: Path | str,
    client: SupportsTurtlePost,
    graph_iri: str,
    *,
    udfs_path: Path | str | None = None,
    work_dir: Path | str | None = None,
    chunk_lines: int = DEFAULT_CHUNK_LINES,
    on_progress: Callable[[int, int], None] | None = None,
    run_id: str | None = None,
) -> dict[str, object]:
    """Append one batch's materialized triples into an existing graph (incremental).

    ``csv_dir`` holds *only* the new batch's source (the new rows), so materialize is
    O(new). The N-Triples are POST-merged into ``graph_iri`` — the dataset's live
    canonical graph the caller resolved, NOT a draft graph: append grows a citable
    feed in place because the human gate was given when the dataset was first promoted
    (ADR §A6). This is the shared seam for ``POST /api/datasets/{id}/append`` and the
    per-dataset watcher. The blocking Morph-KGC subprocess runs off the event loop.
    Returns ``{"graph_iri": ..., "triples_in_batch": ...}``.

    Append is idempotent and safe to retry *when the batch's IRIs are deterministic*:
    a partially-streamed (or fully re-delivered) batch leaves only triples that
    re-appear and dedupe on retry, so — unlike the version-graph ingest — no rollback
    DROP is needed on failure. The one non-deterministic seam is the ``{__run_id__}``
    ingestion-activity IRI: left to a fresh timestamp it would mint a NEW activity per
    attempt, orphaning the prior attempt's provenance subtree in the live graph. So
    the caller passes ``run_id`` derived from the batch content
    (:func:`run_id_for_batch`), making the activity IRI itself deterministic → the
    retried activity dedupes too. ``run_id=None`` keeps the per-call fresh timestamp
    (snapshot ingest, where each version graph is its own activity).
    """
    work = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="c2r-append-"))
    nt = await asyncio.to_thread(
        materialize_to_nt_file,
        rml_ttl,
        csv_dir,
        udfs_path=udfs_path,
        work_dir=work,
        run_id=run_id,
    )
    triples = await stream_nt_file_to_oxigraph(
        nt, client, graph_iri, chunk_lines=chunk_lines, on_progress=on_progress
    )
    return {"graph_iri": graph_iri, "triples_in_batch": triples}


# ----------------------------------------------------------------------------
# Promotion: stage -> canonical (#15 S4) — memory-bounded, flag-based (no MOVE)
# ----------------------------------------------------------------------------
#
# Ingest streams a dataset's triples STRAIGHT into its per-dataset canonical graph
# (``…/graph/canonical/{id}``); promotion then flips a single "promoted" flag in
# the control graph. Citability is gated on that flag, NOT on graph existence, so
# an ingested-but-unreviewed graph sits in ``canonical/{id}`` yet stays out of the
# Ask scope until a human promotes it (the draft-isolation invariant, D2).
#
# This replaces the old ``MOVE GRAPH draft TO canonical``: Oxigraph materializes a
# whole graph in memory to MOVE it (~1.5 GB per 1 M triples — OOM on large data),
# whereas a flag write is O(1) and memory-bounded. See
# ``docs/architecture/scalable-declarative-ingestion.md`` §promote.


class SupportsSparql(Protocol):
    """The slice of OxigraphClient needed for alignment + promotion."""

    async def sparql_select(self, query: str) -> dict: ...
    async def sparql_update(self, update: str) -> None: ...


def classify_alignment(draft: set[str], canonical: set[str]) -> dict[str, list[str]]:
    """Split draft terms into Reuse (already in canonical) vs New (not).

    Pure — the alignment heuristic, testable without a store. ``Align``/``Extend``
    (synonym mapping / TBox extension) need ontology knowledge and are left to the
    human reviewer; Reuse-vs-New is the concrete, mechanical signal we can give.
    """
    return {
        "reuse": sorted(draft & canonical),
        "new": sorted(draft - canonical),
    }


async def _distinct_iris(
    client: SupportsSparql, where: str, *, from_block: str = ""
) -> set[str]:
    """DISTINCT IRI values of ``?x`` from ``SELECT ?x {from_block}WHERE {{ where }}``.

    ``from_block`` injects ``FROM`` clauses so the canonical side reads the
    cross-dataset merge (same scope as Ask), not a single graph.
    """
    data = await client.sparql_select(
        f"SELECT DISTINCT ?x {from_block}WHERE {{ {where} }}"
    )
    results = data.get("results", {}) if isinstance(data, dict) else {}
    out: set[str] = set()
    for b in results.get("bindings", []):
        v = b.get("x", {})
        if v.get("type") == "uri":
            out.add(v["value"])
    return out


async def alignment_report(client: SupportsSparql, staged_iri: str) -> dict[str, object]:
    """Compare a staged graph's predicates + classes against the citable corpus.

    Returns ``{"predicates": {reuse, new}, "classes": {reuse, new}}`` — what the
    human reviews before promoting (the Reuse/Align/Extend/New decision; here we
    surface Reuse vs New mechanically). The canonical side is the FROM-merge over
    every citable graph (:func:`canonical_graphs` — the same scope Ask reads), so
    Reuse reflects the whole corpus the staged data is being merged into. The staged
    graph is not promoted yet, so it is never part of the side it is compared against.
    """
    staged = f"<{staged_iri}>"
    from_block = canonical_from_clauses(await canonical_graphs(client))
    staged_preds = await _distinct_iris(client, f"GRAPH {staged} {{ ?s ?x ?o }}")
    canon_preds = await _distinct_iris(client, "?s ?x ?o", from_block=from_block)
    staged_classes = await _distinct_iris(client, f"GRAPH {staged} {{ ?s a ?x }}")
    canon_classes = await _distinct_iris(client, "?s a ?x", from_block=from_block)
    return {
        "predicates": classify_alignment(staged_preds, canon_preds),
        "classes": classify_alignment(staged_classes, canon_classes),
    }


# --- surgical control-graph writers (touch ONE predicate, preserve the rest) ---
# part5 keeps several control triples per dataset key (status / liveGraph /
# stagedGraph), so a write must replace a single predicate, never DELETE-all.


async def _set_control_literal(
    client: SupportsSparql, subject: str, predicate: str, value: str
) -> None:
    await client.sparql_update(
        f"DELETE WHERE {{ GRAPH <{CONTROL_GRAPH_IRI}> {{ <{subject}> <{predicate}> ?o }} }} ;"
        f"INSERT DATA {{ GRAPH <{CONTROL_GRAPH_IRI}> {{ "
        f'<{subject}> <{predicate}> "{value}" }} }}'
    )


async def _set_control_iri(
    client: SupportsSparql, subject: str, predicate: str, obj_iri: str
) -> None:
    await client.sparql_update(
        f"DELETE WHERE {{ GRAPH <{CONTROL_GRAPH_IRI}> {{ <{subject}> <{predicate}> ?o }} }} ;"
        f"INSERT DATA {{ GRAPH <{CONTROL_GRAPH_IRI}> {{ "
        f"<{subject}> <{predicate}> <{obj_iri}> }} }}"
    )


async def _clear_control_predicate(
    client: SupportsSparql, subject: str, predicate: str
) -> None:
    await client.sparql_update(
        f"DELETE WHERE {{ GRAPH <{CONTROL_GRAPH_IRI}> {{ <{subject}> <{predicate}> ?o }} }}"
    )


async def _control_iri_object(
    client: SupportsSparql, subject: str, predicate: str
) -> str | None:
    """Read the single IRI object of ``<subject> <predicate>`` from the control graph."""
    data = await client.sparql_select(
        f"SELECT ?o WHERE {{ GRAPH <{CONTROL_GRAPH_IRI}> {{ "
        f"<{subject}> <{predicate}> ?o }} }} LIMIT 1"
    )
    results = data.get("results", {}) if isinstance(data, dict) else {}
    for b in results.get("bindings", []):
        v = b.get("o", {})
        if v.get("type") == "uri":
            return v["value"]
    return None


async def live_graph_of(client: SupportsSparql, dataset_key: str) -> str | None:
    """The dataset's current live (citable) data graph, or None if unset.

    None means the data lives in the key graph itself (a dataset promoted before
    part5's versioned graphs) — callers fall back to ``dataset_key``.
    """
    return await _control_iri_object(client, dataset_key, LIVE_GRAPH_PREDICATE)


async def set_staged_graph(
    client: SupportsSparql, dataset_key: str, staged_iri: str
) -> str | None:
    """Record the just-ingested (not-yet-citable) version graph for ``dataset_key``.

    Returns the *superseded* staged version graph — now enqueued for a background
    drop — or ``None`` if there was none. A re-ingest before promotion overwrites
    this pointer; a set staged pointer always names an *un-promoted* version (promote
    clears it), so no ``liveGraph`` ever pointed at the old one and, left un-reclaimed,
    it would leak unbounded — the re-ingest-after-a-misread-disconnect case that makes
    version graphs pile up. Symmetric with :func:`promote_to_canonical`, which already
    enqueues a superseded *live* graph. Dropping the old staged graph cannot affect a
    reader: it was never citable. No-op enqueue when the pointer is unchanged (the same
    version re-recorded), so it never drops the graph it is currently pointing at.
    """
    prior = await _control_iri_object(client, dataset_key, STAGED_GRAPH_PREDICATE)
    await _set_control_iri(client, dataset_key, STAGED_GRAPH_PREDICATE, staged_iri)
    if prior and prior != staged_iri:
        await mark_pending_drop(client, prior)
        return prior
    return None


async def clear_staged_graph(client: SupportsSparql, dataset_key: str) -> None:
    """Drop the staged-version pointer (e.g. deleting a never-promoted dataset).
    Leaves no tombstone — the dataset was never citable."""
    await _clear_control_predicate(client, dataset_key, STAGED_GRAPH_PREDICATE)


async def mark_graph_promoted(
    client: SupportsSparql, dataset_key: str, *, live_graph: str | None = None
) -> None:
    """Flag ``dataset_key`` ``promoted`` in the control graph; set its live graph.

    Surgical (preserves other control triples). Used by the startup backfill to
    restore citability after upgrade — ``live_graph`` points it at the version graph
    holding the data (omit for a pre-part5 dataset whose data is in the key graph).
    """
    await _set_control_literal(client, dataset_key, STATUS_PREDICATE, STATUS_PROMOTED)
    await _clear_control_predicate(client, dataset_key, INVALIDATED_PREDICATE)
    if live_graph:
        await _set_control_iri(client, dataset_key, LIVE_GRAPH_PREDICATE, live_graph)


async def promote_to_canonical(
    client: SupportsSparql, dataset_key: str, staged_graph: str
) -> str | None:
    """Promote: make ``dataset_key`` citable and point its live graph at ``staged_graph``.

    Memory-bounded (only control-graph writes — no MOVE, no DROP): the triples were
    streamed straight into ``staged_graph`` at ingest. Sets ``status promoted`` +
    ``liveGraph = staged_graph`` and clears the staged pointer. If a *prior* live
    graph is being superseded, it is enqueued for a background drop (off the request
    path) and returned; otherwise None.
    """
    prior = await live_graph_of(client, dataset_key)
    # No live pointer: a dataset promoted before part5 holds its data in the key graph
    # itself. Orphan it (only if it actually has data) so a re-promote does not leak
    # the old version. A fresh part5 dataset's key graph is empty -> stays None.
    if (
        prior is None
        and dataset_key != staged_graph
        and await graph_has_triples(client, dataset_key)
    ):
        prior = dataset_key
    await _set_control_literal(client, dataset_key, STATUS_PREDICATE, STATUS_PROMOTED)
    await _clear_control_predicate(client, dataset_key, INVALIDATED_PREDICATE)
    await _set_control_iri(client, dataset_key, LIVE_GRAPH_PREDICATE, staged_graph)
    await _clear_control_predicate(client, dataset_key, STAGED_GRAPH_PREDICATE)
    if prior and prior != staged_graph:
        await mark_pending_drop(client, prior)
        return prior
    return None


# --- background drop queue (part5): superseded / deleted graphs are dropped off
# the request path, so replace / delete never block on a large DROP --------------


async def mark_pending_drop(client: SupportsSparql, graph_iri: str) -> None:
    """Enqueue ``graph_iri`` for a background drop (a superseded version or a deleted
    dataset's data graph). Idempotent; the sweeper drops it and clears the marker."""
    await client.sparql_update(
        f"DELETE WHERE {{ GRAPH <{CONTROL_GRAPH_IRI}> {{ "
        f"<{graph_iri}> <{PENDING_DROP_PREDICATE}> ?o }} }} ;"
        f"INSERT DATA {{ GRAPH <{CONTROL_GRAPH_IRI}> {{ "
        f'<{graph_iri}> <{PENDING_DROP_PREDICATE}> "1" }} }}'
    )


async def pending_drops(client: SupportsSparql, *, limit: int = 50) -> list[str]:
    """Graphs currently enqueued for a background drop (oldest IRI order)."""
    data = await client.sparql_select(
        f"SELECT ?g WHERE {{ GRAPH <{CONTROL_GRAPH_IRI}> {{ "
        f"?g <{PENDING_DROP_PREDICATE}> ?o }} }} ORDER BY ?g LIMIT {int(limit)}"
    )
    results = data.get("results", {}) if isinstance(data, dict) else {}
    out: list[str] = []
    for b in results.get("bindings", []):
        v = b.get("g", {})
        if v.get("type") == "uri":
            out.append(v["value"])
    return out


async def sweep_pending_drops(client: SupportsSparql, *, limit: int = 50) -> list[str]:
    """Reclaim the enqueued graphs (memory-safe chunked delete) and clear their
    markers; return the IRIs reclaimed. Each is a superseded version or a deleted
    dataset's data graph — never a live/citable graph — so this is safe to run
    concurrently with reads. Runs off the request path (a background sweeper),
    keeping the large reclaim out of the critical path (part5).

    Uses :func:`chunked_drop_graph` rather than ``DROP GRAPH``: a single DROP of a
    multi-million-triple graph materializes the whole graph and OOMs even an 8 GB
    Oxigraph (measured), so the reclaim itself must be batched.
    """
    drops = await pending_drops(client, limit=limit)
    done: list[str] = []
    for g in drops:
        await chunked_drop_graph(client, g)
        await _clear_control_predicate(client, g, PENDING_DROP_PREDICATE)
        done.append(g)
    return done


# --- orphan reclamation (part5): version graphs no pointer references ------------
#
# A versioned data graph ``…/canonical/{id}/v{n}`` is created by an ingest and becomes
# referenced by ``stagedGraph`` (at ingest) then ``liveGraph`` (at promote). A version
# that NEITHER pointer names is an orphan: a re-ingest before promotion whose old staged
# pointer was overwritten, a crash partial left before its staged pointer was written,
# or a deleted dataset's leftover version. All are safe to reclaim — a never-referenced
# version was never live/citable. ``set_staged_graph`` / ``promote_to_canonical`` enqueue
# the version they supersede at the source; the reconciliation below is the startup
# safety net that also catches an orphan from a crash in the gap *before* a pointer was
# written, and any orphan that predates this machinery (a store that leaked before it).

# A versioned data graph's IRI ends in ``/v<digits>`` — distinguishes it from the
# per-dataset key graph ``canonical/{id}`` and the ``canonical/legacy`` graph.
_VERSION_SUFFIX_RE: str = "/v[0-9]+$"


async def all_version_graphs(
    client: SupportsSparql, *, dataset_id: str | None = None
) -> list[str]:
    """Enumerate versioned data graphs ``…/canonical/{id}/v{n}`` in the store, sorted.

    Scoped to one ``dataset_id`` when given (delete reclaims every version of the
    dataset it removes); otherwise every dataset's versions (startup reconciliation).
    Enumerates via the graph-name index (empty group ``GRAPH ?g {}`` + a name filter),
    NOT a triple scan, so it stays O(#graphs) even against a multi-million-triple store
    (the property that made the old name-scan slow — see :func:`canonical_graphs`). The
    ``/v<digits>`` filter matches only version graphs, so the per-dataset key graph
    (``canonical/{id}``) and the ``legacy`` graph are never returned.
    """
    if dataset_id is not None:
        if not _DATASET_ID.match(dataset_id):
            raise ValueError(f"unsafe dataset_id for graph IRI: {dataset_id!r}")
        prefix = f"{CANONICAL_GRAPH_BASE}{dataset_id}/v"
    else:
        prefix = CANONICAL_GRAPH_BASE
    q = (
        "SELECT DISTINCT ?g WHERE { "
        "GRAPH ?g {} "
        f'FILTER(STRSTARTS(STR(?g), "{prefix}") && REGEX(STR(?g), "{_VERSION_SUFFIX_RE}")) '
        "} ORDER BY ?g"
    )
    data = await client.sparql_select(q)
    results = data.get("results", {}) if isinstance(data, dict) else {}
    out: list[str] = []
    for b in results.get("bindings", []):
        v = b.get("g", {})
        if v.get("type") == "uri":
            out.append(v["value"])
    return out


async def referenced_version_graphs(client: SupportsSparql) -> set[str]:
    """Every version graph a ``liveGraph`` or ``stagedGraph`` pointer currently names.

    These must never be reclaimed: ``liveGraph`` is the citable data, ``stagedGraph``
    is an ingested version awaiting a promote gate. Read from the control graph
    (O(#datasets), no triple scan). Both pointers survive a restart (the control graph
    is durable), so a legitimately-staged-awaiting-promote version is never mistaken for
    an orphan after a restart.
    """
    q = (
        "SELECT DISTINCT ?g WHERE { "
        f"GRAPH <{CONTROL_GRAPH_IRI}> {{ "
        "?c ?p ?g . "
        f"FILTER(?p = <{LIVE_GRAPH_PREDICATE}> || ?p = <{STAGED_GRAPH_PREDICATE}>) "
        "} }"
    )
    data = await client.sparql_select(q)
    results = data.get("results", {}) if isinstance(data, dict) else {}
    out: set[str] = set()
    for b in results.get("bindings", []):
        v = b.get("g", {})
        if v.get("type") == "uri":
            out.add(v["value"])
    return out


async def reconcile_orphan_versions(client: SupportsSparql) -> list[str]:
    """Enqueue for a background drop every version graph no pointer references.

    The startup safety net for the part5 versioned-graph scheme: a re-ingest before
    promotion, a crash before the staged pointer was written, or a deleted dataset can
    leave a ``…/canonical/{id}/v{n}`` graph that neither ``liveGraph`` nor ``stagedGraph``
    names. Such a version was never citable, so reclaiming it cannot affect a reader.
    Returns the orphan IRIs enqueued (the sweeper drops them off the request path).

    **Run at startup only.** It infers "referenced by no pointer" ⇒ "orphan", which holds
    only when no ingest is in flight — an ingest streams into its version graph *before*
    writing the staged pointer, so a concurrent reconciliation could mistake an in-flight
    target for an orphan and drop it mid-stream. At startup (before the server accepts
    requests) there is no in-flight ingest, so the inference is sound. Idempotent: an
    already-queued graph is re-marked harmlessly.
    """
    versions = set(await all_version_graphs(client))
    referenced = await referenced_version_graphs(client)
    orphans = sorted(versions - referenced)
    for g in orphans:
        await mark_pending_drop(client, g)
    return orphans


# ----------------------------------------------------------------------------
# Retract / reinstate (#20 P3 step3) — tombstone, never physical delete
# ----------------------------------------------------------------------------
#
# Retract marks a canonical graph as withdrawn in the control graph; the
# canonical scope (canonical_scope_where) then excludes it from every Ask read.
# The data + IRIs stay in place so existing citations keep resolving (ADR §3 確定
# ②: physical delete is avoided for citation stability). Reinstate removes the
# marker. Both are scoped, validated control-graph writes — NOT a generic UPDATE
# passthrough (the public /api/sparql stays read-only).

_XSD_DATETIME: str = "http://www.w3.org/2001/XMLSchema#dateTime"


async def retract_canonical(
    client: SupportsSparql, dataset_key: str, *, invalidated_at: str
) -> None:
    """Tombstone ``dataset_key`` as retracted (excluded from Ask, data + IRIs kept).

    Surgical: only the status + invalidatedAt change; the ``liveGraph`` pointer is
    preserved so reinstate brings back the same data.
    """
    await client.sparql_update(
        f"DELETE WHERE {{ GRAPH <{CONTROL_GRAPH_IRI}> {{ "
        f"<{dataset_key}> <{STATUS_PREDICATE}> ?s }} }} ;"
        f"DELETE WHERE {{ GRAPH <{CONTROL_GRAPH_IRI}> {{ "
        f"<{dataset_key}> <{INVALIDATED_PREDICATE}> ?t }} }} ;"
        f"INSERT DATA {{ GRAPH <{CONTROL_GRAPH_IRI}> {{ "
        f'<{dataset_key}> <{STATUS_PREDICATE}> "{STATUS_RETRACTED}" ; '
        f'<{INVALIDATED_PREDICATE}> "{invalidated_at}"^^<{_XSD_DATETIME}> }} }}'
    )


async def reinstate_canonical(client: SupportsSparql, dataset_key: str) -> None:
    """Undo a retract: flip the status back to ``promoted`` (citable again), keeping
    the same ``liveGraph``. Citability requires the promoted flag, so reinstate must
    set it (not merely clear the retracted marker); the live pointer is untouched.
    """
    await _set_control_literal(client, dataset_key, STATUS_PREDICATE, STATUS_PROMOTED)
    await _clear_control_predicate(client, dataset_key, INVALIDATED_PREDICATE)


# ----------------------------------------------------------------------------
# Delete (#20 P3 step4) — hard removal; canonical delete is gated upstream
# ----------------------------------------------------------------------------


async def drop_graph(client: SupportsSparql, graph_iri: str) -> None:
    """Physically remove a named graph (``DROP SILENT GRAPH``). SILENT = no error
    if it does not exist (idempotent).

    ``DROP GRAPH`` materializes the whole graph in memory on Oxigraph, so it is only
    safe for *small* graphs (an ontology TBox, an empty key graph). For a possibly
    large data graph use :func:`chunked_drop_graph`, which is memory-bounded.
    """
    await client.sparql_update(f"DROP SILENT GRAPH <{graph_iri}>")


# Rows deleted per chunked-delete batch. Bounds per-request memory: a single
# ``DROP GRAPH`` of millions of triples materializes the whole graph and OOMs even
# an 8 GB Oxigraph (measured), so reclaiming a large graph must be batched.
DEFAULT_DELETE_CHUNK = 100_000


async def chunked_drop_graph(
    client: SupportsSparql, graph_iri: str, *, chunk: int = DEFAULT_DELETE_CHUNK
) -> int:
    """Empty a named graph in bounded ``DELETE … LIMIT N`` batches; return batch count.

    Memory-safe alternative to ``DROP GRAPH`` for large graphs: each batch
    materializes at most ``chunk`` bindings (a sub-SELECT ``LIMIT`` inside the
    ``WHERE``), so Oxigraph never holds the whole graph in memory. Loops until the
    graph is empty (``ASK`` between batches — O(1), stops at the first triple). Used
    by the background sweeper to reclaim superseded / deleted versions without OOM.
    """
    batches = 0
    while await graph_has_triples(client, graph_iri):
        await client.sparql_update(
            f"DELETE {{ GRAPH <{graph_iri}> {{ ?s ?p ?o }} }} WHERE {{ "
            f"SELECT ?s ?p ?o WHERE {{ GRAPH <{graph_iri}> {{ ?s ?p ?o }} }} "
            f"LIMIT {int(chunk)} }}"
        )
        batches += 1
    return batches


async def tombstone_deleted(
    client: SupportsSparql, canonical_iri: str, *, deleted_at: str
) -> None:
    """Leave a ``deleted`` marker in the control graph after a canonical DROP.

    The triples are gone, but the marker lets a dangling citation get a clear
    "this was deleted" answer instead of silence (ADR §3.1). Replaces any prior
    control triples (e.g. a retract marker) for the graph.
    """
    await client.sparql_update(
        f"DELETE WHERE {{ GRAPH <{CONTROL_GRAPH_IRI}> {{ <{canonical_iri}> ?p ?o }} }} ;"
        f"INSERT DATA {{ GRAPH <{CONTROL_GRAPH_IRI}> {{ "
        f'<{canonical_iri}> <{STATUS_PREDICATE}> "{STATUS_DELETED}" ; '
        f'<{INVALIDATED_PREDICATE}> "{deleted_at}"^^<{_XSD_DATETIME}> }} }}'
    )


# ----------------------------------------------------------------------------
# Cross-dataset read: FROM-merge over canonical graphs (#20 P3 step "1+2")
# ----------------------------------------------------------------------------
#
# The GRAPH-union scope (canonical_scope_where) reads every canonical graph, but a
# single join whose two triples live in DIFFERENT canonical graphs will NOT match
# (``GRAPH ?g { A . B }`` binds one ?g for the whole group). To link data ACROSS
# datasets through a shared ontology, we instead MERGE the canonical graphs into
# one query dataset via ``FROM`` clauses: ``SELECT ... FROM <c1> FROM <c2> WHERE
# {GRAPH-less}`` — verified on Oxigraph (FROM merges named graphs into the query's
# default graph, so plain patterns join across them). FROM replaces the real
# default graph, so legacy/seed data must first be migrated into a canonical graph
# (migrate_default_to_canonical, whose caller flags the target promoted) for the
# merge to cover it. Only promoted graphs are listed, so unpromoted / retracted /
# deleted graphs are simply absent from the FROM list.

# Reserved canonical graph that legacy default-graph data migrates into, so the
# FROM-merge covers it. "legacy" is IRI-safe and unlikely to collide with a real
# dataset id (which are slug-uuid).
LEGACY_DATASET_ID: str = "legacy"


async def canonical_graphs(client: SupportsSparql) -> list[str]:
    """List the **citable** canonical named graphs, sorted (#20 P3).

    A canonical graph is citable iff the control graph flags it ``promoted``, so
    this enumerates the control graph's promoted markers — NOT the graph-name index.
    Ingested-but-unpromoted graphs (no flag) and retracted / deleted graphs (a
    different status) are absent, so draft data is never cited (the draft-isolation
    invariant, now flag-based instead of MOVE-based).

    Used to build the FROM-merge dataset for cross-dataset queries. Deterministic
    order (sorted) keeps generated queries stable/cacheable.

    part5: each promoted dataset key resolves to its ``liveGraph`` (the current
    version graph holding the data); a dataset promoted before part5 has no live
    pointer, so it falls back to the key graph itself (``COALESCE``). Superseded
    versions are not pointed at by any live key, so they are never returned.

    Perf: reads the control graph's ``promoted`` triples — one per promoted dataset
    — so this is O(#datasets) with no triple scan, even with a multi-million-triple
    graph in the store (the property that made the old name-index scan slow).
    """
    q = (
        "SELECT DISTINCT ?g WHERE { "
        f"GRAPH <{CONTROL_GRAPH_IRI}> {{ "
        f'?c <{STATUS_PREDICATE}> "{STATUS_PROMOTED}" . '
        f"OPTIONAL {{ ?c <{LIVE_GRAPH_PREDICATE}> ?lg }} "
        "BIND(COALESCE(?lg, ?c) AS ?g) "
        "} } ORDER BY ?g"
    )
    data = await client.sparql_select(q)
    results = data.get("results", {}) if isinstance(data, dict) else {}
    out: list[str] = []
    for b in results.get("bindings", []):
        v = b.get("g", {})
        if v.get("type") == "uri":
            out.append(v["value"])
    return out


async def ontology_graphs(client: SupportsSparql) -> list[str]:
    """List the per-dataset ontology (projected TBox) named graphs, sorted (#20 §2).

    These hold the RDFS/OWL projection of each dataset's TBox
    (``…/asterism/graph/ontology/{id}``). ``schema_summary`` reads them to enrich
    Ask with labels / domain / range, but Ask works without them (ABox baseline).

    Perf: enumerates with the empty group ``GRAPH ?g {}`` (graph-name index, not a
    triple scan) for the same reason as :func:`canonical_graphs`.
    """
    q = (
        "SELECT DISTINCT ?g WHERE { "
        "GRAPH ?g {} "
        f'FILTER(STRSTARTS(STR(?g), "{ONTOLOGY_GRAPH_BASE}")) '
        "} ORDER BY ?g"
    )
    data = await client.sparql_select(q)
    results = data.get("results", {}) if isinstance(data, dict) else {}
    out: list[str] = []
    for b in results.get("bindings", []):
        v = b.get("g", {})
        if v.get("type") == "uri":
            out.append(v["value"])
    return out


def canonical_from_clauses(graphs: list[str], *, named: bool = False) -> str:
    """Build the dataset clause that merges ``graphs`` into the query dataset.

    ``FROM <g>`` for each graph merges them into the query's default graph so
    GRAPH-less patterns join across datasets. With ``named=True`` we *also* emit a
    ``FROM NAMED <g>`` for each, so a query that uses ``GRAPH ?g { ... }`` still
    resolves — but only over the canonical graphs (draft / control / ontology are
    never listed, so explicit-GRAPH escape queries cannot reach unreviewed data).

    Empty list -> empty string (the query then reads the real default graph, which
    is the safe pre-migration behaviour).
    """
    out = "".join(f"FROM <{g}>\n" for g in graphs)
    if named:
        out += "".join(f"FROM NAMED <{g}>\n" for g in graphs)
    return out


# Reject queries that already declare their own RDF dataset: a ``FROM`` means the
# caller scoped the read deliberately, so we respect it and inject nothing.
_HAS_DATASET_CLAUSE: re.Pattern[str] = re.compile(r"\bfrom\b", re.IGNORECASE)
_WHERE_KEYWORD: re.Pattern[str] = re.compile(r"\bWHERE\b", re.IGNORECASE)
# SERVICE (federated query) on the read escape is an SSRF / exfiltration vector —
# a read-only SELECT can stream the graph to an external endpoint or reach internal
# hosts. The escape is local-only, so SERVICE is rejected outright.
_SERVICE: re.Pattern[str] = re.compile(r"\bservice\b", re.IGNORECASE)
# A GRAPH keyword in a query with NO injected FROM NAMED would range over EVERY
# named graph (incl. unreviewed drafts); disabled until a canonical graph exists.
_GRAPH_KEYWORD: re.Pattern[str] = re.compile(r"\bgraph\b", re.IGNORECASE)
# FROM / FROM NAMED head; the IRI itself is read from the original (un-blanked) query.
_FROM_HEAD: re.Pattern[str] = re.compile(r"\bfrom\b", re.IGNORECASE)
_FROM_IRI: re.Pattern[str] = re.compile(r"\s*(?:named\b\s*)?<([^>\s]+)>", re.IGNORECASE)


async def readable_graph_iris(client: SupportsSparql) -> set[str]:
    """The only graphs a raw-SPARQL caller may scope to: the promoted canonical
    graphs plus the projected ontology graphs. Draft / control / legacy graphs are
    never listed, so a hand-written ``FROM NAMED <draft>`` cannot reach them."""
    return set(await canonical_graphs(client)) | set(await ontology_graphs(client))


def _dataset_clause_iris(query: str) -> list[str] | None:
    """IRIs named by the query's own ``FROM`` / ``FROM NAMED`` clauses.

    Returns ``None`` when a clause cannot be parsed as an explicit ``<IRI>`` (e.g. a
    prefixed name) — the caller treats that as "reject", since an unresolvable
    dataset clause cannot be checked against the allowlist. Locates real ``from``
    keywords via :func:`_scan_view` (so a ``from`` inside a literal is ignored),
    then reads the IRI from the original query at the same offset.
    """
    view = _scan_view(query)
    iris: list[str] = []
    for m in _FROM_HEAD.finditer(view):
        im = _FROM_IRI.match(query, m.end())
        if im is None:
            return None
        iris.append(im.group(1))
    return iris


def _scan_view(query: str) -> str:
    """A same-length copy of ``query`` with comments, string literals, and IRIs
    blanked to spaces, so keyword/brace scans never trip on a ``FROM`` / ``WHERE``
    / ``{`` that lives inside a literal or ``<...>`` IRI. Character positions are
    preserved 1:1 so a match index maps straight back to the original query.
    """
    out = list(query)
    i, n = 0, len(query)
    while i < n:
        ch = query[i]
        if ch == "#":  # line comment to end of line
            while i < n and query[i] != "\n":
                out[i] = " "
                i += 1
        elif ch == "<":  # IRI ref: <...> (no '>' or whitespace inside)
            j = i + 1
            while j < n and query[j] not in ">\n":
                j += 1
            if j < n and query[j] == ">":
                for k in range(i, j + 1):
                    out[k] = " "
                i = j + 1
            else:  # not a closed IRI (e.g. a '<' operator) — leave it
                i += 1
        elif ch in "\"'":  # string literal: "...", '...', """...""", '''...'''
            triple = query[i : i + 3] in ('"""', "'''")
            quote = ch * 3 if triple else ch
            j = i + len(quote)
            while j < n:
                if query[j] == "\\":  # escaped char
                    j += 2
                    continue
                if query[j : j + len(quote)] == quote:
                    j += len(quote)
                    break
                j += 1
            for k in range(i, min(j, n)):
                out[k] = " "
            i = j
        else:
            i += 1
    return "".join(out)


def insert_dataset_clause(query: str, clause: str) -> str:
    """Insert a dataset clause (``FROM``/``FROM NAMED`` block) before the WHERE.

    Per the SPARQL grammar the ``DatasetClause*`` sits after the SELECT/ASK
    projection (and any PREFIX decls) and before the ``WhereClause`` — which is
    the ``WHERE`` keyword if present, otherwise the opening ``{`` of the group
    graph pattern. We insert at whichever of those comes first. Returns the query
    unchanged if it has no group pattern (nothing to scope). The scan ignores
    text inside comments / literals / IRIs (see :func:`_scan_view`).
    """
    view = _scan_view(query)
    m_where = _WHERE_KEYWORD.search(view)
    brace = view.find("{")
    if m_where is not None and (brace == -1 or m_where.start() < brace):
        idx = m_where.start()
    elif brace != -1:
        idx = brace
    else:
        return query
    return f"{query[:idx]}{clause}{query[idx:]}"


async def canonical_merge_query(client: SupportsSparql, query: str) -> str:
    """Rewrite a read-only SELECT/ASK to read the cross-dataset canonical scope.

    Injects ``FROM <c>`` + ``FROM NAMED <c>`` over every non-retracted canonical
    graph so GRAPH-less patterns join ACROSS datasets through shared vocabulary,
    and an explicit ``GRAPH ?g`` stays scoped to canonical graphs (never reaches
    draft / control / ontology data).

    Security (the read escape must NEVER let the caller choose an unreviewed graph):

    * ``SERVICE`` (federation) is rejected outright — SSRF / exfiltration vector.
    * When the caller supplies its OWN ``FROM`` / ``FROM NAMED``, it is NOT trusted
      verbatim: every referenced graph must be in the canonical + ontology
      allowlist, else the query is rejected. This closes the ``FROM NAMED <draft>``
      bypass.
    * When no canonical graph exists yet, GRAPH-less reads hit the real default
      graph (safe pre-migration behaviour), but any ``GRAPH`` pattern is rejected —
      with no injected ``FROM NAMED`` it would otherwise range over every named
      graph, drafts included.

    Raises :class:`ValueError` on a rejected query (callers map it to a 4xx).
    Returns the (possibly unchanged) query; callers disclose it as the effective
    query actually executed, so the FROM-merge is visible and reproducible.
    """
    view = _scan_view(query)
    if _SERVICE.search(view):
        raise ValueError(
            "SERVICE (federated query) is not allowed on the read-only SPARQL escape"
        )
    if _HAS_DATASET_CLAUSE.search(view):
        # The caller scoped the read with its own FROM/FROM NAMED — validate it
        # against the allowlist rather than honouring it blindly.
        iris = _dataset_clause_iris(query)
        if iris is None:
            raise ValueError(
                "unsupported FROM clause on the read escape — use explicit <IRI> graphs"
            )
        allowed = await readable_graph_iris(client)
        outside = sorted(g for g in iris if g not in allowed)
        if outside:
            raise ValueError(
                "FROM/FROM NAMED may only reference promoted canonical or ontology "
                f"graphs; rejected: {', '.join(outside)}"
            )
        return query
    clause = canonical_from_clauses(await canonical_graphs(client), named=True)
    if not clause:
        if _GRAPH_KEYWORD.search(view):
            raise ValueError(
                "no canonical graphs are published yet; GRAPH patterns are disabled "
                "on the read escape until a dataset is promoted"
            )
        return query
    return insert_dataset_clause(query, clause)


async def migrate_default_to_canonical(
    client: SupportsSparql, target_iri: str
) -> int:
    """Move the real default graph's triples into a canonical named graph.

    Required so the FROM-merge read (which excludes the real default graph) still
    covers legacy / seed / pre-P3 data parked in the default graph. Implemented as
    ``ADD DEFAULT TO GRAPH`` (merge — set semantics, never replaces the target)
    followed by ``CLEAR DEFAULT``, so it is **idempotent and safe to run on every
    startup**: a second run finds the default graph empty and is a no-op, and a
    target that already holds data is merged into, not clobbered (unlike ``MOVE``).

    The caller flags ``target_iri`` ``promoted`` (``mark_graph_promoted``) so the
    migrated data is citable — citability is now flag-gated, and the target is not
    a workbench-promoted dataset that would otherwise carry its own flag.

    Returns the number of triples that were in the default graph before the move
    (0 = nothing to migrate).
    """
    count = 0
    data = await client.sparql_select("SELECT (COUNT(*) AS ?c) WHERE { ?s ?p ?o }")
    bindings = data.get("results", {}).get("bindings", []) if isinstance(data, dict) else []
    if bindings:
        try:
            count = int(bindings[0].get("c", {}).get("value", 0))
        except (TypeError, ValueError):
            count = 0
    if count:
        await client.sparql_update(f"ADD DEFAULT TO GRAPH <{target_iri}>")
        await client.sparql_update("CLEAR DEFAULT")
    return count


async def graph_has_triples(client: SupportsSparql, graph_iri: str) -> bool:
    """True iff ``graph_iri`` holds at least one triple (a cheap ASK — stops at the
    first match, so it does not scan a large graph). Used at startup to flag the
    legacy bulk graph ``promoted`` only when it actually holds data."""
    data = await client.sparql_select(f"ASK {{ GRAPH <{graph_iri}> {{ ?s ?p ?o }} }}")
    return bool(data.get("boolean")) if isinstance(data, dict) else False
