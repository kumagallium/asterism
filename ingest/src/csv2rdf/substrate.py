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
only functions it may call are the closed Tier 0 set in :mod:`csv2rdf.functions`
(enforced upstream by step0's T9 closed-set check). ``morph-kgc`` is an optional
dependency — install with ``pip install 'csv2rdf-ingest[substrate]'``.
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Protocol

# Draft graphs live under /graph/draft/<id>, trivially distinguishable from the
# canonical per-kind graphs (.../graph/curves, .../graph/papers, ...).
GRAPH_BASE = "https://kumagallium.github.io/csv2rdf-mcp/starrydata/graph/"
_DATASET_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# rml:source is resolved relative to the CWD by Morph-KGC. Rather than chdir
# (process-global state — unsafe under the api's thread pool), we rewrite
# *relative* sources to absolute paths under the dataset's CSV dir. Thread-safe.
_RML_SOURCE = re.compile(r'(rml:source\s+")([^"]+)(")')

# Default UDF-registration file shipped with the package. Morph-KGC loads it by
# path and registers the closed Tier 0 set via csv2rdf.functions.register.
_DEFAULT_UDFS = Path(__file__).with_name("substrate_udfs.py")


class SupportsTurtlePost(Protocol):
    """The slice of :class:`csv2rdf.oxigraph_client.OxigraphClient` we need."""

    async def post_turtle_bytes(self, payload: bytes, graph_iri: str | None = None) -> int: ...


def draft_graph_iri(dataset_id: str) -> str:
    """Named graph for workbench-ingested (unreviewed) data.

    Raises ``ValueError`` for ids that would not be IRI-safe (the registry
    controls the id format, but we re-check at the trust boundary).
    """
    if not _DATASET_ID.match(dataset_id):
        raise ValueError(f"unsafe dataset_id for graph IRI: {dataset_id!r}")
    return f"{GRAPH_BASE}draft/{dataset_id}"


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
            "install with: pip install 'csv2rdf-ingest[substrate]'"
        ) from exc

    udfs = Path(udfs_path) if udfs_path else _DEFAULT_UDFS
    work = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="c2r-substrate-"))
    work.mkdir(parents=True, exist_ok=True)
    mapping_file = work / "mappings.rml.ttl"
    mapping_file.write_text(absolutize_rml_sources(rml_ttl, csv_dir), encoding="utf-8")

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
