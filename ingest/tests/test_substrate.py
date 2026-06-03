"""Tests for asterism.substrate (declarative-substrate ingestion, #15).

The Morph-KGC step needs the optional ``substrate`` extra + real CSVs, so it is
not exercised here; these tests cover the parts that do not depend on it: the
draft graph IRI scheme, thread-safe rml:source absolutization, and loading a
graph into Oxigraph (via a fake client). The Morph-KGC path is proven by the
``experiments/phase5-morph-kgc-spike`` e2e.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import rdflib

from asterism.substrate import (
    GRAPH_BASE,
    absolutize_rml_sources,
    draft_graph_iri,
    ingest_graph_to_oxigraph,
    materialize_to_graph,
    run_substrate_ingest,
)

# ---- draft graph IRI scheme -------------------------------------------------


def test_draft_graph_iri_scheme() -> None:
    assert draft_graph_iri("starrydata-1700000000") == GRAPH_BASE + "draft/starrydata-1700000000"


def test_draft_graph_iri_rejects_unsafe_id() -> None:
    for bad in ("../escape", "a b", "x/y", "", "<inject>"):
        with pytest.raises(ValueError, match="unsafe dataset_id"):
            draft_graph_iri(bad)


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


async def test_run_substrate_ingest_validates_id_before_work() -> None:
    # An unsafe id must fail fast (ValueError) before touching Morph-KGC/Oxigraph.
    fake = _FakeOxi()
    with pytest.raises(ValueError, match="unsafe dataset_id"):
        await run_substrate_ingest("rml...", "/data", fake, "../escape")
    assert fake.calls == []  # nothing was posted
