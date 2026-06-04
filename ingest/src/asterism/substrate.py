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
import tempfile
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
# Promotion: draft -> canonical (#15 S4)
# ----------------------------------------------------------------------------
#
# "Canonical" is the *default graph* — where Ask's GRAPH-less queries (and the
# MCP tools' GRAPH ?g UNION default) read citable facts. Promotion MOVEs a draft
# named graph into the default graph after a human reviews the alignment report,
# so unreviewed vocabulary never silently becomes a citable fact (D2).


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


# Excludes retracted canonical graphs: a tombstone in the control graph
# (``<canonical/{id}> asterism:status "retracted"``) drops that graph from every
# Ask read (#20 P3 step3). The IRIs stay resolvable for citations; they just
# leave the citable corpus until reinstated.
def _retracted_exclusion_for_var(graph_var: str) -> str:
    """``FILTER NOT EXISTS`` that drops ``graph_var`` if it is retracted."""
    return (
        f"FILTER NOT EXISTS {{ GRAPH <{CONTROL_GRAPH_IRI}> {{ "
        f'{graph_var} <{STATUS_PREDICATE}> "{STATUS_RETRACTED}" }} }}'
    )


_RETRACTED_EXCLUSION: str = _retracted_exclusion_for_var("?__cg")


def canonical_scope_where(body: str) -> str:
    """A WHERE group reading the **canonical scope** (#20 P3). Single source of truth.

    ``{ body } UNION { GRAPH ?__cg { body } FILTER(canonical prefix) FILTER NOT
    EXISTS retracted }`` — the default graph (legacy / seed / pre-migration data)
    plus every per-dataset canonical named graph that is NOT retracted; draft /
    control / ontology graphs are excluded by the prefix filter. ``asterism_mcp``
    imports this so Ask reads and alignment use the exact same scope.
    """
    return (
        "{ " + body + " } UNION { GRAPH ?__cg { " + body + " } "
        f'FILTER(STRSTARTS(STR(?__cg), "{CANONICAL_GRAPH_BASE}")) '
        f"{_RETRACTED_EXCLUSION} }}"
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


async def promote_draft_to_canonical(
    client: SupportsSparql, draft_iri: str, canonical_iri: str
) -> int:
    """MOVE the draft graph into the dataset's **canonical named graph** (#20 P3).

    Returns the triples moved. ``MOVE`` replaces the destination, so a re-promote
    cleanly swaps in the new version (stale triples from a prior version do not
    linger — that is why canonical is per-dataset, ADR §3.1). After this the draft
    named graph no longer exists; Ask reads ``canonical_iri`` via the canonical
    scope. An empty/absent draft makes this a no-op.
    """
    count = 0
    data = await client.sparql_select(
        f"SELECT (COUNT(*) AS ?c) WHERE {{ GRAPH <{draft_iri}> {{ ?s ?p ?o }} }}"
    )
    bindings = data.get("results", {}).get("bindings", []) if isinstance(data, dict) else []
    if bindings:
        count = int(bindings[0]["c"]["value"])
    await client.sparql_update(f"MOVE GRAPH <{draft_iri}> TO GRAPH <{canonical_iri}>")
    return count


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
    """Remove the retract tombstone for ``canonical_iri`` (back into the Ask scope)."""
    await client.sparql_update(
        f"DELETE WHERE {{ GRAPH <{CONTROL_GRAPH_IRI}> {{ <{canonical_iri}> ?p ?o }} }}"
    )


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
# (migrate_default_to_canonical) for the merge to cover it. Retracted graphs are
# simply omitted from the FROM list. These helpers are introduced unused; the
# read-path switch + migration land in the follow-up PR.

# Reserved canonical graph that legacy default-graph data migrates into, so the
# FROM-merge covers it. "legacy" is IRI-safe and unlikely to collide with a real
# dataset id (which are slug-uuid).
LEGACY_DATASET_ID: str = "legacy"


async def canonical_graphs(client: SupportsSparql) -> list[str]:
    """List the canonical named graphs to read, sorted, EXCLUDING retracted ones.

    Used to build the FROM-merge dataset for cross-dataset queries. Deterministic
    order (sorted) keeps generated queries stable/cacheable.
    """
    q = (
        "SELECT DISTINCT ?g WHERE { "
        "GRAPH ?g { ?s ?p ?o } "
        f'FILTER(STRSTARTS(STR(?g), "{CANONICAL_GRAPH_BASE}")) '
        f"{_retracted_exclusion_for_var('?g')} "
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


def canonical_from_clauses(graphs: list[str]) -> str:
    """Build the ``FROM <g>`` block that merges ``graphs`` into the query dataset.

    Empty list -> empty string (the query then reads the real default graph, which
    is the safe pre-migration behaviour).
    """
    return "".join(f"FROM <{g}>\n" for g in graphs)


async def migrate_default_to_canonical(
    client: SupportsSparql, target_iri: str
) -> None:
    """One-time: MOVE the real default graph's triples into a canonical named graph.

    Required so the FROM-merge read (which excludes the real default graph) still
    covers legacy / seed / pre-P3 data. ``MOVE`` empties the default graph.
    """
    await client.sparql_update(f"MOVE DEFAULT TO GRAPH <{target_iri}>")
