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
only functions it may call are the closed Tier 0 set in :mod:`asterism.functions`
(enforced upstream by step0's T9 closed-set check). ``morph-kgc`` is an optional
dependency — install with ``pip install 'asterism-ingest[substrate]'``.
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

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


def canonical_graph_iri(dataset_id: str) -> str:
    """Per-dataset canonical (citable) named graph IRI."""
    if not _DATASET_ID.match(dataset_id):
        raise ValueError(f"unsafe dataset_id for graph IRI: {dataset_id!r}")
    return f"{CANONICAL_GRAPH_BASE}{dataset_id}"


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


def materialize_to_graph(
    rml_ttl: str,
    csv_dir: Path | str,
    *,
    udfs_path: Path | str | None = None,
    work_dir: Path | str | None = None,
):  # -> rdflib.Graph (annotation omitted to avoid importing rdflib at module load)
    """Run Morph-KGC on ``rml_ttl`` (sources resolved under ``csv_dir``).

    Returns the produced ``rdflib.Graph``. Raises ``RuntimeError`` if ``morph-kgc``
    is not installed (it is the optional ``substrate`` extra).
    """
    try:
        import morph_kgc
    except ImportError as exc:  # optional dependency
        raise RuntimeError(
            "morph-kgc is required for substrate ingestion; "
            "install with: pip install 'asterism-ingest[substrate]'"
        ) from exc

    udfs = Path(udfs_path) if udfs_path else _DEFAULT_UDFS
    work = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="c2r-substrate-"))
    work.mkdir(parents=True, exist_ok=True)
    mapping_file = work / "mappings.rml.ttl"
    prepared = normalize_fno_namespace(absolutize_rml_sources(rml_ttl, csv_dir))
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
) -> Path:
    """Run Morph-KGC and write the triples to an N-Triples file; return its path.

    Unlike :func:`materialize_to_graph` (whole graph in memory), this invokes
    Morph-KGC's file-output CLI (``python -m morph_kgc <config>`` with
    ``output_file``) so triples stream to disk — the memory-bounded path Morph-KGC
    itself recommends for large data. ``number_of_processes: 1`` avoids
    multiprocessing (portable + safe UDF import). Raises ``RuntimeError`` if
    ``morph-kgc`` is absent or materialization fails (caller maps a failure to a
    user-facing 4xx, as with :func:`materialize_to_graph`).
    """
    try:
        import morph_kgc  # noqa: F401  (presence check; the CLI does the work)
    except ImportError as exc:  # optional dependency
        raise RuntimeError(
            "morph-kgc is required for substrate ingestion; "
            "install with: pip install 'asterism-ingest[substrate]'"
        ) from exc

    work = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="c2r-substrate-"))
    work.mkdir(parents=True, exist_ok=True)
    udfs = Path(udfs_path) if udfs_path else _DEFAULT_UDFS
    mapping_file = work / "mappings.rml.ttl"
    mapping_file.write_text(
        normalize_fno_namespace(absolutize_rml_sources(rml_ttl, csv_dir)),
        encoding="utf-8",
    )
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


def _promoted_requirement_for_var(graph_var: str) -> str:
    """``FILTER EXISTS`` that keeps ``graph_var`` only if it is flagged promoted."""
    return (
        f"FILTER EXISTS {{ GRAPH <{CONTROL_GRAPH_IRI}> {{ "
        f'{graph_var} <{STATUS_PREDICATE}> "{STATUS_PROMOTED}" }} }}'
    )


def canonical_scope_where(body: str) -> str:
    """A WHERE group reading the **canonical scope** (#20 P3). Single source of truth.

    ``{ body } UNION { GRAPH ?__cg { body } FILTER EXISTS promoted }`` — the
    default graph (legacy / seed / pre-migration data) plus every per-dataset
    canonical named graph that the control graph flags ``promoted``. Ingested-but-
    unpromoted graphs (no flag) and retracted / deleted graphs (a different status)
    are excluded — that single requirement subsumes the old retracted filter.
    ``asterism_mcp`` imports this so Ask reads and alignment use the exact same scope.
    """
    return (
        "{ " + body + " } UNION { GRAPH ?__cg { " + body + " } "
        f"{_promoted_requirement_for_var('?__cg')} }}"
    )


# Backwards-compatible internal alias.
_canonical_where = canonical_scope_where


async def _distinct_iris(client: SupportsSparql, where: str) -> set[str]:
    data = await client.sparql_select(f"SELECT DISTINCT ?x WHERE {{ {where} }}")
    results = data.get("results", {}) if isinstance(data, dict) else {}
    out: set[str] = set()
    for b in results.get("bindings", []):
        v = b.get("x", {})
        if v.get("type") == "uri":
            out.add(v["value"])
    return out


async def alignment_report(client: SupportsSparql, draft_iri: str) -> dict[str, object]:
    """Compare a draft graph's predicates + classes against the canonical scope.

    Returns ``{"predicates": {reuse, new}, "classes": {reuse, new}}`` — what the
    human reviews before promoting (the Reuse/Align/Extend/New decision; here we
    surface Reuse vs New mechanically). The canonical side spans the default graph
    plus every per-dataset canonical named graph (#20 P3), so Reuse reflects the
    whole citable corpus the draft is being merged into.
    """
    g = f"<{draft_iri}>"
    draft_preds = await _distinct_iris(client, f"GRAPH {g} {{ ?s ?x ?o }}")
    canon_preds = await _distinct_iris(client, _canonical_where("?s ?x ?o"))
    draft_classes = await _distinct_iris(client, f"GRAPH {g} {{ ?s a ?x }}")
    canon_classes = await _distinct_iris(client, _canonical_where("?s a ?x"))
    return {
        "predicates": classify_alignment(draft_preds, canon_preds),
        "classes": classify_alignment(draft_classes, canon_classes),
    }


async def mark_graph_promoted(client: SupportsSparql, canonical_iri: str) -> None:
    """Flag ``canonical_iri`` as ``promoted`` (citable) in the control graph. O(1).

    Replaces any prior control status for the graph (so it also serves as
    reinstate-from-retracted and re-promote). One small control-graph write — it
    never touches the data graph, so it is memory-bounded regardless of how many
    triples ``canonical_iri`` holds (the whole point: no MOVE).
    """
    await client.sparql_update(
        f"DELETE WHERE {{ GRAPH <{CONTROL_GRAPH_IRI}> {{ <{canonical_iri}> ?p ?o }} }} ;"
        f"INSERT DATA {{ GRAPH <{CONTROL_GRAPH_IRI}> {{ "
        f'<{canonical_iri}> <{STATUS_PREDICATE}> "{STATUS_PROMOTED}" }} }}'
    )


async def clear_status(client: SupportsSparql, canonical_iri: str) -> None:
    """Remove ``canonical_iri`` from the citable scope by clearing its control
    status. Used on re-ingest to un-publish before re-streaming (gap-free swap);
    NOT a tombstone (leaves no retracted / deleted marker)."""
    await client.sparql_update(
        f"DELETE WHERE {{ GRAPH <{CONTROL_GRAPH_IRI}> {{ <{canonical_iri}> ?p ?o }} }}"
    )


async def promote_to_canonical(client: SupportsSparql, canonical_iri: str) -> None:
    """Promote a dataset's canonical graph: flip its control flag to ``promoted``.

    Memory-bounded (O(1) flag write) — the triples were streamed straight into
    ``canonical_iri`` at ingest, so promotion moves/copies nothing (the OOM-prone
    ``MOVE GRAPH`` is gone). Ask then reads ``canonical_iri`` via the canonical
    scope. The triple count is read from the registry (recorded at ingest), so this
    does not scan the graph either.
    """
    await mark_graph_promoted(client, canonical_iri)


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
    client: SupportsSparql, canonical_iri: str, *, invalidated_at: str
) -> None:
    """Tombstone ``canonical_iri`` as retracted (excluded from Ask, IRIs kept)."""
    await client.sparql_update(
        f"DELETE WHERE {{ GRAPH <{CONTROL_GRAPH_IRI}> {{ <{canonical_iri}> ?p ?o }} }} ;"
        f"INSERT DATA {{ GRAPH <{CONTROL_GRAPH_IRI}> {{ "
        f'<{canonical_iri}> <{STATUS_PREDICATE}> "{STATUS_RETRACTED}" ; '
        f'<{INVALIDATED_PREDICATE}> "{invalidated_at}"^^<{_XSD_DATETIME}> }} }}'
    )


async def reinstate_canonical(client: SupportsSparql, canonical_iri: str) -> None:
    """Undo a retract: flip the control status back to ``promoted`` (citable again).

    (Citability now requires the ``promoted`` flag, so reinstate must set it — not
    merely clear the retracted marker.)
    """
    await mark_graph_promoted(client, canonical_iri)


# ----------------------------------------------------------------------------
# Delete (#20 P3 step4) — hard removal; canonical delete is gated upstream
# ----------------------------------------------------------------------------


async def drop_graph(client: SupportsSparql, graph_iri: str) -> None:
    """Physically remove a named graph (``DROP SILENT GRAPH``). SILENT = no error
    if it does not exist (idempotent)."""
    await client.sparql_update(f"DROP SILENT GRAPH <{graph_iri}>")


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

    Perf: reads the control graph's ``promoted`` triples — one per promoted dataset
    — so this is O(#datasets) with no triple scan, even with a multi-million-triple
    graph in the store (the property that made the old name-index scan slow).
    """
    q = (
        "SELECT DISTINCT ?g WHERE { "
        f'GRAPH <{CONTROL_GRAPH_IRI}> {{ ?g <{STATUS_PREDICATE}> "{STATUS_PROMOTED}" }} '
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
    draft / control / ontology data). No-ops when the query already declares its
    own ``FROM`` (caller scoped it) or when no canonical graphs exist yet (the
    query then reads the real default graph — safe pre-migration behaviour).

    Returns the (possibly unchanged) query; callers disclose this as the effective
    query actually executed, so the FROM-merge is visible and reproducible.
    """
    if _HAS_DATASET_CLAUSE.search(_scan_view(query)):
        return query
    clause = canonical_from_clauses(await canonical_graphs(client), named=True)
    if not clause:
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
