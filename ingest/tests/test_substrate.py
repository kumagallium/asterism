"""Tests for asterism.substrate (declarative-substrate ingestion, #15).

Most tests cover the parts that do not depend on Morph-KGC: the draft graph IRI
scheme, thread-safe rml:source absolutization (CSV and JSON, #19), and loading a
graph into Oxigraph (via a fake client). One test (``test_materialize_to_graph_
json_source``) runs the real Morph-KGC JSON path when the optional ``substrate``
extra is installed (skipped otherwise); the CSV Morph-KGC path is proven by the
``experiments/phase5-morph-kgc-spike`` e2e.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import rdflib

from asterism.substrate import (
    CANONICAL_GRAPH_BASE,
    GRAPH_BASE,
    ONTOLOGY_GRAPH_BASE,
    absolutize_rml_sources,
    alignment_report,
    canonical_graph_iri,
    classify_alignment,
    count_nt_lines,
    draft_graph_iri,
    ingest_graph_to_oxigraph,
    materialize_to_graph,
    materialize_to_nt_file,
    ontology_graph_iri,
    run_substrate_ingest,
    stream_nt_file_to_oxigraph,
    versioned_graph_iri,
)

# ---- draft graph IRI scheme -------------------------------------------------


def test_draft_graph_iri_scheme() -> None:
    assert draft_graph_iri("starrydata-1700000000") == GRAPH_BASE + "draft/starrydata-1700000000"


def test_draft_graph_iri_rejects_unsafe_id() -> None:
    for bad in ("../escape", "a b", "x/y", "", "<inject>"):
        with pytest.raises(ValueError, match="unsafe dataset_id"):
            draft_graph_iri(bad)


# ---- #20 P3 lifecycle graph IRIs (dataset-neutral namespace) ----------------


def test_canonical_graph_iri_scheme() -> None:
    assert canonical_graph_iri("ds1") == CANONICAL_GRAPH_BASE + "ds1"
    # Lifecycle graphs are dataset-neutral, NOT under the starrydata GRAPH_BASE.
    assert "/starrydata/" not in canonical_graph_iri("ds1")


def test_ontology_graph_iri_scheme() -> None:
    assert ontology_graph_iri("ds1") == ONTOLOGY_GRAPH_BASE + "ds1"


def test_lifecycle_graph_iris_reject_unsafe_id() -> None:
    for fn in (canonical_graph_iri, ontology_graph_iri):
        for bad in ("../escape", "a b", "x/y", "", "<inject>"):
            with pytest.raises(ValueError, match="unsafe dataset_id"):
                fn(bad)


def test_canonical_and_draft_graphs_are_distinguishable_by_prefix() -> None:
    # The read-model flip (P3 step 2) relies on filtering canonical graphs by
    # prefix to exclude draft graphs from Ask.
    assert canonical_graph_iri("ds1").startswith(CANONICAL_GRAPH_BASE)
    assert not draft_graph_iri("ds1").startswith(CANONICAL_GRAPH_BASE)


# ---- rml:source absolutization (thread-safe alternative to chdir) -----------


def test_absolutize_rewrites_relative_sources() -> None:
    rml = (
        'rml:logicalSource [ rml:source "papers.csv" ; rml:referenceFormulation ql:CSV ] .\n'
        'rml:logicalSource [ rml:source "samples.csv" ] .\n'
    )
    out = absolutize_rml_sources(rml, "/data/ds1")
    assert 'rml:source "/data/ds1/papers.csv"' in out
    assert 'rml:source "/data/ds1/samples.csv"' in out


def test_absolutize_leaves_absolute_sources_untouched() -> None:
    rml = 'rml:source "/already/abs/papers.csv"'
    assert absolutize_rml_sources(rml, "/data/ds1") == rml


def test_absolutize_only_touches_rml_source() -> None:
    rml = 'rr:template "https://ex/{id}" ; rml:source "c.csv"'
    out = absolutize_rml_sources(rml, "/data/ds1")
    assert 'rr:template "https://ex/{id}"' in out  # template untouched
    assert 'rml:source "/data/ds1/c.csv"' in out


def test_absolutize_rewrites_json_source() -> None:
    """#19: source rewriting is format-agnostic — a JSON rml:source resolves too."""
    rml = (
        'rml:logicalSource [ rml:source "mp.json" ; '
        'rml:referenceFormulation ql:JSONPath ; rml:iterator "$[*]" ] .\n'
    )
    out = absolutize_rml_sources(rml, "/data/ds1")
    assert 'rml:source "/data/ds1/mp.json"' in out


# ---- Oxigraph load (fake client) --------------------------------------------


class _FakeOxi:
    """Records the payload + graph passed to post_turtle_bytes."""

    def __init__(self) -> None:
        self.calls: list[tuple[bytes, str | None]] = []

    async def post_turtle_bytes(self, payload: bytes, graph_iri: str | None = None) -> int:
        self.calls.append((payload, graph_iri))
        return len(payload)


async def test_ingest_graph_to_oxigraph_posts_to_named_graph() -> None:
    g = rdflib.Graph()
    s = rdflib.URIRef("https://ex/curve/1")
    g.add((s, rdflib.URIRef("https://ex/yMax"), rdflib.Literal(1.45)))
    g.add((s, rdflib.URIRef("https://ex/name"), rdflib.Literal("c1")))
    fake = _FakeOxi()

    n = await ingest_graph_to_oxigraph(g, fake, "https://ex/graph/draft/ds1")

    assert n == 2  # triple count returned
    assert len(fake.calls) == 1
    payload, graph_iri = fake.calls[0]
    assert graph_iri == "https://ex/graph/draft/ds1"
    assert isinstance(payload, bytes)
    assert b"yMax" in payload  # the triple made it into the serialized turtle


# ---- Morph-KGC dependency guard ---------------------------------------------


def _morph_kgc_installed() -> bool:
    try:
        import morph_kgc  # noqa: F401
        return True
    except ImportError:
        return False


def test_materialize_to_graph_requires_morph_kgc(tmp_path: Path) -> None:
    if _morph_kgc_installed():
        pytest.skip("morph-kgc installed; cannot exercise the missing-dependency path")
    with pytest.raises(RuntimeError, match="morph-kgc"):
        materialize_to_graph('rml:source "p.csv"', tmp_path)


def test_materialize_to_nt_file_requires_morph_kgc(tmp_path: Path) -> None:
    if _morph_kgc_installed():
        pytest.skip("morph-kgc installed; cannot exercise the missing-dependency path")
    with pytest.raises(RuntimeError, match="morph-kgc"):
        materialize_to_nt_file('rml:source "p.csv"', tmp_path)


def test_materialize_to_graph_json_source(tmp_path: Path) -> None:
    """#19: Morph-KGC reads a JSON source via ql:JSONPath + rml:iterator + dot-path
    references (incl. nested objects). Gated on the optional morph-kgc extra."""
    if not _morph_kgc_installed():
        pytest.skip("morph-kgc not installed; this exercises the real JSON materialize")
    (tmp_path / "mp.json").write_text(
        '[{"mp_id": "mp-1", "formula": "PbTe", "structure": {"spacegroup": "Fm-3m"}},'
        ' {"mp_id": "mp-2", "formula": "SnSe", "structure": {"spacegroup": "Pnma"}}]',
        encoding="utf-8",
    )
    rml = (
        "@prefix rr:  <http://www.w3.org/ns/r2rml#> .\n"
        "@prefix rml: <http://semweb.mmlab.be/ns/rml#> .\n"
        "@prefix ql:  <http://semweb.mmlab.be/ns/ql#> .\n"
        "@prefix ex:  <https://ex/> .\n"
        "<#M> a rr:TriplesMap ;\n"
        '  rml:logicalSource [ rml:source "mp.json" ;\n'
        "                      rml:referenceFormulation ql:JSONPath ;\n"
        '                      rml:iterator "$[*]" ] ;\n'
        '  rr:subjectMap [ rr:template "https://ex/mat/{mp_id}" ] ;\n'
        "  rr:predicateObjectMap [ rr:predicate ex:formula ;\n"
        '      rr:objectMap [ rml:reference "formula" ] ] ;\n'
        "  rr:predicateObjectMap [ rr:predicate ex:spaceGroup ;\n"
        '      rr:objectMap [ rml:reference "structure.spacegroup" ] ] .\n'
    )
    graph = materialize_to_graph(rml, tmp_path)
    triples = {(str(s), str(p), str(o)) for s, p, o in graph}
    assert ("https://ex/mat/mp-1", "https://ex/formula", "PbTe") in triples
    # nested object flattened to a dot-path reference resolves
    assert ("https://ex/mat/mp-1", "https://ex/spaceGroup", "Fm-3m") in triples
    assert ("https://ex/mat/mp-2", "https://ex/spaceGroup", "Pnma") in triples


# ---- streaming N-Triples load (scalable path) -------------------------------


def test_count_nt_lines(tmp_path: Path) -> None:
    f = tmp_path / "a.nt"
    f.write_bytes(b"<s1> <p> <o> .\n<s2> <p> <o> .\n<s3> <p> <o> .\n")
    assert count_nt_lines(f) == 3
    assert count_nt_lines(tmp_path / "missing.nt") == 0  # absent -> 0


async def test_stream_nt_file_chunks_appends_and_reports_progress(tmp_path: Path) -> None:
    # 5 triples, 2 per chunk -> 3 POSTs (2+2+1), all to the draft graph.
    lines = [f"<https://ex/s{i}> <https://ex/p> <https://ex/o> .".encode() for i in range(5)]
    f = tmp_path / "out.nt"
    f.write_bytes(b"\n".join(lines) + b"\n")
    fake = _FakeOxi()
    progress: list[tuple[int, int]] = []

    n = await stream_nt_file_to_oxigraph(
        f, fake, "https://ex/graph/draft/ds1", chunk_lines=2,
        on_progress=lambda done, total: progress.append((done, total)),
    )

    assert n == 5  # total triples loaded
    assert len(fake.calls) == 3  # 2 + 2 + 1
    assert all(g == "https://ex/graph/draft/ds1" for _, g in fake.calls)
    # chunks reassemble to the whole file (append semantics)
    assert b"".join(p for p, _ in fake.calls) == f.read_bytes()
    # progress is monotonic and ends at total
    assert progress == [(2, 5), (4, 5), (5, 5)]


async def test_stream_nt_file_empty_is_zero(tmp_path: Path) -> None:
    f = tmp_path / "empty.nt"
    f.touch()
    fake = _FakeOxi()
    progress: list[tuple[int, int]] = []
    n = await stream_nt_file_to_oxigraph(
        f, fake, "https://ex/graph/draft/ds1",
        on_progress=lambda done, total: progress.append((done, total)),
    )
    assert n == 0
    assert fake.calls == []  # nothing posted
    assert progress == [(0, 0)]


async def test_run_substrate_ingest_validates_id_before_work() -> None:
    # An unsafe id must fail fast (ValueError) before touching Morph-KGC/Oxigraph.
    fake = _FakeOxi()
    with pytest.raises(ValueError, match="unsafe dataset_id"):
        await run_substrate_ingest("rml...", "/data", fake, "../escape")
    assert fake.calls == []  # nothing was posted


# ---- promotion: draft -> canonical (#15 S4) ---------------------------------


def test_classify_alignment_splits_reuse_and_new() -> None:
    draft = {"https://schema.org/name", "https://ex/asterism#customProp"}
    canonical = {"https://schema.org/name", "http://purl.org/dc/terms/identifier"}
    out = classify_alignment(draft, canonical)
    assert out["reuse"] == ["https://schema.org/name"]  # already in canonical
    assert out["new"] == ["https://ex/asterism#customProp"]  # not yet


class _FakeSparql:
    """Fake OxigraphClient: canned predicate/class sets + records updates."""

    def __init__(self, draft_preds, canon_preds, draft_classes, canon_classes, draft_n=0):
        self._sets = {
            ("graph", "p"): draft_preds,
            ("default", "p"): canon_preds,
            ("graph", "c"): draft_classes,
            ("default", "c"): canon_classes,
        }
        self._draft_n = draft_n
        self.updates: list[str] = []

    async def sparql_select(self, query: str) -> dict:
        if "COUNT" in query:
            return {"results": {"bindings": [{"c": {"value": str(self._draft_n)}}]}}
        # The canonical-scope side binds ``?__cg`` (UNION over canonical graphs,
        # incl. a control-graph EXISTS promoted); the staged side names a graph literally.
        scope = "default" if "?__cg" in query else "graph"
        kind = "c" if "?s a ?x" in query else "p"
        vals = self._sets[(scope, kind)]
        return {"results": {"bindings": [{"x": {"type": "uri", "value": v}} for v in vals]}}

    async def sparql_update(self, update: str) -> None:
        self.updates.append(update)


class _RWClient:
    """A SupportsSparql backed by a real rdflib Dataset (SELECT + UPDATE).

    Exercises the surgical control writes + versioned-graph swap + sweep against a
    real SPARQL 1.1 engine, not just recorded query strings.
    """

    def __init__(self, ds: rdflib.Dataset) -> None:
        self.ds = ds

    async def sparql_select(self, query: str) -> dict:
        raw = self.ds.query(query).serialize(format="json")
        return json.loads(raw.decode() if isinstance(raw, bytes) else raw)

    async def sparql_update(self, update: str) -> None:
        self.ds.update(update)


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_alignment_report_classifies_predicates_and_classes() -> None:
    # The canonical side is the FROM-merge over the *promoted* graphs (same scope as
    # Ask); the staged graph is not promoted, so it is never on the side it is
    # compared against.
    from asterism.substrate import (
        CONTROL_GRAPH_IRI,
        STATUS_PREDICATE,
        STATUS_PROMOTED,
        set_staged_graph,
    )

    ds = rdflib.Dataset()
    schema = rdflib.Namespace("https://schema.org/")
    ex = rdflib.Namespace("https://ex#")
    canon_g = rdflib.URIRef(canonical_graph_iri("other"))
    staged_g = rdflib.URIRef(versioned_graph_iri("ds1", 1))
    # canonical (promoted) side: reuses schema:name
    ds.graph(canon_g).add((ex.s, schema.name, rdflib.Literal("x")))
    ds.graph(rdflib.URIRef(CONTROL_GRAPH_IRI)).add(
        (canon_g, rdflib.URIRef(STATUS_PREDICATE), rdflib.Literal(STATUS_PROMOTED))
    )
    # staged side: schema:name (reuse) + ex#new (new) + class ex#Curve (new)
    ds.graph(staged_g).add((ex.a, schema.name, rdflib.Literal("y")))
    ds.graph(staged_g).add((ex.a, ex.new, rdflib.Literal("z")))
    ds.graph(staged_g).add((ex.a, rdflib.RDF.type, ex.Curve))
    client = _RWClient(ds)
    await set_staged_graph(client, canonical_graph_iri("ds1"), str(staged_g))

    rep = await alignment_report(client, str(staged_g))
    assert rep["predicates"]["reuse"] == ["https://schema.org/name"]
    assert "https://ex#new" in rep["predicates"]["new"]
    assert rep["classes"]["new"] == ["https://ex#Curve"]  # canonical has no classes


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_versioned_promote_swaps_pointer_and_orphans_prior() -> None:
    # part5: promote points liveGraph at the staged version (no MOVE/DROP); a
    # re-promote supersedes the prior version, which is enqueued for a background
    # drop and reclaimed by the sweeper — the live version stays untouched.
    from asterism.substrate import (
        canonical_graphs,
        live_graph_of,
        pending_drops,
        promote_to_canonical,
        set_staged_graph,
        sweep_pending_drops,
    )

    ds = rdflib.Dataset()
    ex = rdflib.Namespace("https://ex#")
    key = canonical_graph_iri("ds1")
    v1, v2 = versioned_graph_iri("ds1", 1), versioned_graph_iri("ds1", 2)
    ds.graph(rdflib.URIRef(v1)).add((ex.a, ex.p, rdflib.Literal("v1")))
    ds.graph(rdflib.URIRef(v2)).add((ex.b, ex.p, rdflib.Literal("v2")))
    client = _RWClient(ds)

    # ingest v1 -> stage -> promote
    await set_staged_graph(client, key, v1)
    assert await canonical_graphs(client) == []  # staged, not citable
    orphan = await promote_to_canonical(client, key, v1)
    assert orphan is None
    assert await canonical_graphs(client) == [v1]  # live version is citable
    assert await live_graph_of(client, key) == v1

    # re-ingest v2 -> v1 stays citable during the (separate) re-stream
    await set_staged_graph(client, key, v2)
    assert await canonical_graphs(client) == [v1]  # gap-free: still v1
    # re-promote -> live swaps to v2, v1 superseded + enqueued
    orphan2 = await promote_to_canonical(client, key, v2)
    assert orphan2 == v1
    assert await canonical_graphs(client) == [v2]
    assert await pending_drops(client) == [v1]

    # the sweeper drops only the superseded v1; v2 (live) is untouched
    assert await sweep_pending_drops(client) == [v1]
    assert len(ds.graph(rdflib.URIRef(v1))) == 0
    assert len(ds.graph(rdflib.URIRef(v2))) == 1
    assert await pending_drops(client) == []


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_chunked_drop_graph_empties_in_batches() -> None:
    # Memory-safe reclaim: a large graph is emptied in bounded DELETE…LIMIT batches
    # (a single DROP would materialize the whole graph and OOM Oxigraph). Here a
    # 5-triple graph with chunk=2 takes 3 batches and ends empty; a sibling graph is
    # untouched.
    from asterism.substrate import chunked_drop_graph

    ds = rdflib.Dataset()
    ex = rdflib.Namespace("https://ex#")
    g = rdflib.URIRef(versioned_graph_iri("ds1", 1))
    other = rdflib.URIRef(versioned_graph_iri("ds1", 2))
    for i in range(5):
        ds.graph(g).add((ex[f"s{i}"], ex.p, rdflib.Literal(i)))
    ds.graph(other).add((ex.keep, ex.p, rdflib.Literal("keep")))
    client = _RWClient(ds)

    batches = await chunked_drop_graph(client, str(g), chunk=2)
    assert batches == 3  # 2 + 2 + 1
    assert len(ds.graph(g)) == 0  # emptied
    assert len(ds.graph(other)) == 1  # sibling untouched
    # idempotent: a second call on the empty graph is a no-op (0 batches)
    assert await chunked_drop_graph(client, str(g), chunk=2) == 0


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_repromote_legacy_dataset_orphans_key_graph() -> None:
    # Backward-compat: a dataset promoted BEFORE part5 has its data in the key graph
    # (no liveGraph). Re-ingesting it streams a version graph; re-promote must orphan
    # the legacy key graph so the old version is reclaimed (no leak).
    from asterism.substrate import (
        CONTROL_GRAPH_IRI,
        STATUS_PREDICATE,
        STATUS_PROMOTED,
        canonical_graphs,
        mark_graph_promoted,
        promote_to_canonical,
        set_staged_graph,
        sweep_pending_drops,
    )

    ds = rdflib.Dataset()
    ex = rdflib.Namespace("https://ex#")
    key = canonical_graph_iri("legacyds")
    v1 = versioned_graph_iri("legacyds", 1)
    # legacy state: data in the key graph, promoted, NO liveGraph (pre-part5)
    ds.graph(rdflib.URIRef(key)).add((ex.old, ex.p, rdflib.Literal("legacy")))
    ds.graph(rdflib.URIRef(v1)).add((ex.new, ex.p, rdflib.Literal("v1")))
    ds.graph(rdflib.URIRef(CONTROL_GRAPH_IRI)).add(
        (rdflib.URIRef(key), rdflib.URIRef(STATUS_PREDICATE), rdflib.Literal(STATUS_PROMOTED))
    )
    client = _RWClient(ds)
    assert await canonical_graphs(client) == [key]  # legacy data citable via the key

    # mark_graph_promoted backfill restores the pre-part5 state too (no live_graph)
    await mark_graph_promoted(client, key)
    assert await canonical_graphs(client) == [key]

    # re-ingest v1 -> re-promote: the legacy key graph is orphaned + reclaimed
    await set_staged_graph(client, key, v1)
    orphan = await promote_to_canonical(client, key, v1)
    assert orphan == key
    assert await canonical_graphs(client) == [v1]
    await sweep_pending_drops(client)
    assert len(ds.graph(rdflib.URIRef(key))) == 0  # legacy data reclaimed
    assert len(ds.graph(rdflib.URIRef(v1))) == 1


# ---- retract / reinstate (#20 P3 step3) -------------------------------------


async def test_retract_canonical_writes_tombstone() -> None:
    from asterism.substrate import (
        CONTROL_GRAPH_IRI,
        STATUS_PREDICATE,
        retract_canonical,
    )

    fake = _FakeSparql(set(), set(), set(), set())
    canon = canonical_graph_iri("ds1")
    await retract_canonical(fake, canon, invalidated_at="2026-06-04T00:00:00")
    assert len(fake.updates) == 1
    u = fake.updates[0]
    # clears any prior control triples, then inserts the retracted status marker
    assert "DELETE WHERE" in u and "INSERT DATA" in u
    assert CONTROL_GRAPH_IRI in u and canon in u
    assert STATUS_PREDICATE in u and '"retracted"' in u


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_retract_reinstate_preserve_live_graph() -> None:
    # part5: retract/reinstate flip the status surgically, keeping the liveGraph
    # pointer, so reinstate brings back the SAME version graph.
    from asterism.substrate import (
        canonical_graphs,
        live_graph_of,
        promote_to_canonical,
        reinstate_canonical,
        retract_canonical,
        set_staged_graph,
    )

    ds = rdflib.Dataset()
    ex = rdflib.Namespace("https://ex#")
    key = canonical_graph_iri("ds1")
    v1 = versioned_graph_iri("ds1", 1)
    ds.graph(rdflib.URIRef(v1)).add((ex.a, ex.p, rdflib.Literal("v1")))
    client = _RWClient(ds)
    await set_staged_graph(client, key, v1)
    await promote_to_canonical(client, key, v1)

    await retract_canonical(client, key, invalidated_at="2026-06-08T00:00:00")
    assert await canonical_graphs(client) == []  # withdrawn from the citable scope
    assert await live_graph_of(client, key) == v1  # pointer preserved

    await reinstate_canonical(client, key)
    assert await canonical_graphs(client) == [v1]  # same version back, citable


# ---- delete (#20 P3 step4) --------------------------------------------------


async def test_drop_graph_issues_drop_silent() -> None:
    from asterism.substrate import drop_graph

    fake = _FakeSparql(set(), set(), set(), set())
    canon = canonical_graph_iri("ds1")
    await drop_graph(fake, canon)
    assert fake.updates == [f"DROP SILENT GRAPH <{canon}>"]


async def test_tombstone_deleted_marks_control() -> None:
    from asterism.substrate import CONTROL_GRAPH_IRI, STATUS_PREDICATE, tombstone_deleted

    fake = _FakeSparql(set(), set(), set(), set())
    canon = canonical_graph_iri("ds1")
    await tombstone_deleted(fake, canon, deleted_at="2026-06-04T00:00:00")
    u = fake.updates[0]
    assert "DELETE WHERE" in u and "INSERT DATA" in u
    assert CONTROL_GRAPH_IRI in u and canon in u
    assert STATUS_PREDICATE in u and '"deleted"' in u


# ---- FROM-merge cross-dataset read (#20 P3) ---------------------------------


def test_canonical_from_clauses_builds_from_block() -> None:
    from asterism.substrate import canonical_from_clauses

    assert canonical_from_clauses([]) == ""
    out = canonical_from_clauses(["https://ex/a", "https://ex/b"])
    assert out == "FROM <https://ex/a>\nFROM <https://ex/b>\n"


def test_canonical_from_clauses_named_adds_from_named() -> None:
    # The escape path also emits FROM NAMED so a `GRAPH ?g {}` query resolves over
    # the canonical graphs (and ONLY those — never draft/control/ontology).
    from asterism.substrate import canonical_from_clauses

    assert canonical_from_clauses([], named=True) == ""
    out = canonical_from_clauses(["https://ex/a", "https://ex/b"], named=True)
    assert out == (
        "FROM <https://ex/a>\nFROM <https://ex/b>\n"
        "FROM NAMED <https://ex/a>\nFROM NAMED <https://ex/b>\n"
    )


async def test_canonical_graphs_lists_sorted_promoted_only() -> None:
    from asterism.substrate import CONTROL_GRAPH_IRI, canonical_graphs

    g_a = canonical_graph_iri("a")
    g_b = canonical_graph_iri("b")

    class _Fake:
        async def sparql_select(self, query: str) -> dict:
            # The enumeration reads the control graph's promoted markers (no triple
            # scan); we just return a fixed promoted set to check parsing/shape.
            assert CONTROL_GRAPH_IRI in query and '"promoted"' in query
            return {
                "results": {
                    "bindings": [
                        {"g": {"type": "uri", "value": g_a}},
                        {"g": {"type": "uri", "value": g_b}},
                    ]
                }
            }

    assert await canonical_graphs(_Fake()) == [g_a, g_b]


def _rdflib_client(ds: rdflib.Dataset) -> object:
    """A SupportsSparql adapter that actually executes SPARQL on an rdflib Dataset.

    Used to exercise the real enumeration query (empty-pattern ``GRAPH ?g {}``)
    end-to-end, not just its string shape.
    """

    class _C:
        async def sparql_select(self, query: str) -> dict:
            return json.loads(ds.query(query).serialize(format="json"))

    return _C()


async def test_canonical_graphs_reads_promoted_flags_only() -> None:
    # Real-store enumeration: canonical_graphs reads the control graph's promoted
    # markers (NOT a triple scan), so a graph with data but no promoted flag (a
    # staged/un-promoted ingest) is excluded, and a large draft graph never leaks
    # in regardless of its size. ontology_graphs still enumerates ontology/* by name.
    from asterism.substrate import (
        CONTROL_GRAPH_IRI,
        STATUS_PREDICATE,
        STATUS_PROMOTED,
        canonical_graph_iri,
        canonical_graphs,
        draft_graph_iri,
        ontology_graph_iri,
        ontology_graphs,
    )

    ds = rdflib.Dataset()
    ex = rdflib.Namespace("https://ex/")
    g_a = rdflib.URIRef(canonical_graph_iri("a"))
    g_b = rdflib.URIRef(canonical_graph_iri("b"))
    g_staged = rdflib.URIRef(canonical_graph_iri("staged"))  # data but no flag
    g_onto = rdflib.URIRef(ontology_graph_iri("a"))
    g_draft = rdflib.URIRef(draft_graph_iri("d"))
    ds.graph(g_a).add((ex.s, ex.p, ex.o))
    ds.graph(g_b).add((ex.s2, ex.p, ex.o2))
    ds.graph(g_staged).add((ex.s3, ex.p, ex.o3))
    ds.graph(g_onto).add((ex.c, rdflib.RDFS.label, rdflib.Literal("C")))
    for i in range(500):  # a "large" draft graph that must not leak in
        ds.graph(g_draft).add((ex[f"x{i}"], ex.p, rdflib.Literal(i)))
    # Only g_a and g_b are flagged promoted in the control graph.
    control = ds.graph(rdflib.URIRef(CONTROL_GRAPH_IRI))
    pred = rdflib.URIRef(STATUS_PREDICATE)
    control.add((g_a, pred, rdflib.Literal(STATUS_PROMOTED)))
    control.add((g_b, pred, rdflib.Literal(STATUS_PROMOTED)))

    client = _rdflib_client(ds)
    assert await canonical_graphs(client) == [str(g_a), str(g_b)]  # g_staged excluded
    assert await ontology_graphs(client) == [str(g_onto)]


async def test_canonical_graphs_excludes_retracted_status() -> None:
    # A graph whose control status is retracted (not promoted) is excluded — the
    # promoted requirement subsumes the old retracted filter.
    from asterism.substrate import (
        CONTROL_GRAPH_IRI,
        STATUS_PREDICATE,
        STATUS_PROMOTED,
        STATUS_RETRACTED,
        canonical_graph_iri,
        canonical_graphs,
    )

    ds = rdflib.Dataset()
    ex = rdflib.Namespace("https://ex/")
    g_a = rdflib.URIRef(canonical_graph_iri("a"))
    g_b = rdflib.URIRef(canonical_graph_iri("b"))
    ds.graph(g_a).add((ex.s, ex.p, ex.o))
    ds.graph(g_b).add((ex.s2, ex.p, ex.o2))
    control = ds.graph(rdflib.URIRef(CONTROL_GRAPH_IRI))
    pred = rdflib.URIRef(STATUS_PREDICATE)
    control.add((g_a, pred, rdflib.Literal(STATUS_PROMOTED)))
    control.add((g_b, pred, rdflib.Literal(STATUS_RETRACTED)))

    assert await canonical_graphs(_rdflib_client(ds)) == [str(g_a)]


async def test_migrate_default_to_canonical_merges_then_clears() -> None:
    # Merge-safe + idempotent: ADD (never replaces the target) then CLEAR DEFAULT.
    from asterism.substrate import migrate_default_to_canonical

    fake = _FakeSparql(set(), set(), set(), set(), draft_n=76)
    target = canonical_graph_iri("legacy")
    moved = await migrate_default_to_canonical(fake, target)
    assert moved == 76
    assert fake.updates == [f"ADD DEFAULT TO GRAPH <{target}>", "CLEAR DEFAULT"]


async def test_migrate_default_to_canonical_is_noop_when_default_empty() -> None:
    from asterism.substrate import migrate_default_to_canonical

    fake = _FakeSparql(set(), set(), set(), set(), draft_n=0)
    moved = await migrate_default_to_canonical(fake, canonical_graph_iri("legacy"))
    assert moved == 0
    assert fake.updates == []  # nothing written -> safe to run on every startup


def test_insert_dataset_clause_before_where_keyword() -> None:
    from asterism.substrate import insert_dataset_clause

    out = insert_dataset_clause("SELECT ?s WHERE { ?s ?p ?o }", "FROM <https://ex/a>\n")
    assert out == "SELECT ?s FROM <https://ex/a>\nWHERE { ?s ?p ?o }"


def test_insert_dataset_clause_before_brace_when_no_where_keyword() -> None:
    from asterism.substrate import insert_dataset_clause

    out = insert_dataset_clause("SELECT ?s { ?s ?p ?o }", "FROM <https://ex/a>\n")
    assert out == "SELECT ?s FROM <https://ex/a>\n{ ?s ?p ?o }"


def test_insert_dataset_clause_uses_outer_where_not_subquery() -> None:
    # The first `{` precedes a nested sub-SELECT's WHERE, so we must insert before
    # the brace (the outer group), not before the subquery's WHERE keyword.
    from asterism.substrate import insert_dataset_clause

    q = "SELECT ?s { { SELECT ?s WHERE { ?s ?p ?o } } }"
    out = insert_dataset_clause(q, "FROM <https://ex/a>\n")
    assert out == "SELECT ?s FROM <https://ex/a>\n{ { SELECT ?s WHERE { ?s ?p ?o } } }"


def test_insert_dataset_clause_ignores_from_brace_where_in_literals() -> None:
    # A literal containing '{' / the word WHERE must NOT be mistaken for the
    # group pattern; insertion still lands before the real WHERE.
    from asterism.substrate import insert_dataset_clause

    body = '{ ?s ?p ?o FILTER(CONTAINS(?o, "a { WHERE b")) }'
    out = insert_dataset_clause(f"SELECT ?s WHERE {body}", "FROM <https://ex/a>\n")
    assert out == f"SELECT ?s FROM <https://ex/a>\nWHERE {body}"


async def test_canonical_merge_query_respects_from_only_outside_literals() -> None:
    # "from" inside a string literal is NOT a dataset clause -> still injected.
    from asterism.substrate import canonical_graph_iri, canonical_merge_query

    g = canonical_graph_iri("a")

    class _Fake:
        async def sparql_select(self, query: str) -> dict:
            return {"results": {"bindings": [{"g": {"type": "uri", "value": g}}]}}

    q = 'SELECT ?s WHERE { ?s ?p ?o FILTER(CONTAINS(?o, "from")) }'
    out = await canonical_merge_query(_Fake(), q)
    assert f"FROM <{g}>" in out  # injected despite the "from" literal


async def test_canonical_merge_query_injects_from_and_from_named() -> None:
    from asterism.substrate import canonical_graph_iri, canonical_merge_query

    g = canonical_graph_iri("a")

    class _Fake:
        async def sparql_select(self, query: str) -> dict:
            return {"results": {"bindings": [{"g": {"type": "uri", "value": g}}]}}

    out = await canonical_merge_query(_Fake(), "SELECT ?s WHERE { ?s ?p ?o }")
    assert out == f"SELECT ?s FROM <{g}>\nFROM NAMED <{g}>\nWHERE {{ ?s ?p ?o }}"


async def test_canonical_merge_query_respects_explicit_from() -> None:
    # A query that already declares a dataset is left untouched (caller scoped it).
    from asterism.substrate import canonical_merge_query

    class _Boom:
        async def sparql_select(self, query: str) -> dict:
            raise AssertionError("must not enumerate when query has its own FROM")

    q = "SELECT ?s FROM <https://ex/x> WHERE { ?s ?p ?o }"
    assert await canonical_merge_query(_Boom(), q) == q


async def test_canonical_merge_query_noop_without_canonical_graphs() -> None:
    # No canonical graphs yet -> no FROM injected -> reads the real default graph.
    from asterism.substrate import canonical_merge_query

    class _Empty:
        async def sparql_select(self, query: str) -> dict:
            return {"results": {"bindings": []}}

    q = "SELECT ?s WHERE { ?s ?p ?o }"
    assert await canonical_merge_query(_Empty(), q) == q


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_from_merge_enables_cross_dataset_join() -> None:
    """The point of FROM-merge: a join whose two facts live in DIFFERENT canonical
    graphs resolves once the graphs are merged via FROM (cross-dataset linking)."""
    from asterism.substrate import canonical_from_clauses, canonical_graph_iri

    ds = rdflib.Dataset()
    ex = rdflib.Namespace("https://ex/")
    g_a = rdflib.URIRef(canonical_graph_iri("a"))
    g_b = rdflib.URIRef(canonical_graph_iri("b"))
    ds.graph(g_a).add((ex.sample1, ex.madeOf, ex.bismuth))  # dataset A
    ds.graph(g_b).add((ex.bismuth, rdflib.RDFS.label, rdflib.Literal("Bismuth")))  # dataset B

    body = "WHERE { ?s <https://ex/madeOf> ?e . ?e rdfs:label ?l }"
    prefix = "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n"

    # Without FROM (default graph only), the cross-graph join finds nothing.
    none = list(ds.query(prefix + "SELECT ?l " + body))
    assert none == []

    # With FROM over both canonical graphs, the join across A and B resolves.
    frm = canonical_from_clauses([str(g_a), str(g_b)])
    rows = list(ds.query(prefix + "SELECT ?l " + frm + body))
    assert len(rows) == 1 and str(rows[0][0]) == "Bismuth"


# ---- FnO namespace normalization (#15 ingest robustness) ---------------------


def test_normalize_fno_namespace_rewrites_old_to_new() -> None:
    from asterism.substrate import normalize_fno_namespace
    old = '@prefix rmlf: <http://semweb.mmlab.be/ns/fnml#> .\n<#M> rmlf:function fn:x .'
    out = normalize_fno_namespace(old)
    assert "http://w3id.org/rml/" in out
    assert "semweb.mmlab.be/ns/fnml" not in out


def test_normalize_fno_namespace_noop_for_new() -> None:
    from asterism.substrate import normalize_fno_namespace
    rml = '@prefix rmlf: <http://w3id.org/rml/> .'
    assert normalize_fno_namespace(rml) == rml
