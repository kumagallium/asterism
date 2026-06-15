"""Tests for the PDF (Docling) path's deterministic, in-repo half.

The Docling sidecar runs the ML (PDF → DoclingDocument) and returns a raw dict; the
vetted ``docling_dict_to_jats`` adapter (here, no torch) turns it into the SAME JATS
shape :func:`structure_jats` already ingests. These tests drive a COMMITTED real Docling
dict fixture (PMC5951533 / 10.3390/ma11040649 — the same paper the ``papers`` dataset
holds as native JATS), so the adapter is verified against true converter output without
any torch / network dependency.

The headline gate (mirrors ``docs/reports/pdf-conversion-feasibility.md``): the PDF path
recovers the SAME measurement-condition sentence — down to the sentence — that the JATS
path cites. Two independent sources reaching one citable fact.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import rdflib

from asterism.documents import (
    DOCO,
    NIF,
    ConversionError,
    convert_pdf_to_jats,
    docling_dict_to_jats,
    structure_jats,
)

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
# A real DoclingDocument.export_to_dict() of the born-digital PMC5951533 PDF, trimmed to
# the fields the adapter reads (verified to yield byte-identical JATS to the full dict).
_DOCLING_DICT = json.loads((_FIXTURES / "docling_ma11040649.json").read_text(encoding="utf-8"))
_PAPER = "https://kumagallium.github.io/asterism/papers/resource/document/pdf-test/ma11040649"
_PPMS = "physical properties measurement system"


def _count(g: rdflib.Graph, cls: str) -> int:
    return len(list(g.subjects(rdflib.RDF.type, rdflib.URIRef(DOCO + cls))))


def test_adapter_emits_well_formed_jats() -> None:
    jats = docling_dict_to_jats(_DOCLING_DICT)
    assert "<article" in jats and "<body" in jats
    # 14 numbered headings recovered from the PDF (matches the feasibility report).
    assert jats.count("<sec ") == 14
    assert jats.count("<fig ") >= 10  # pictures that carry a caption
    # Every section and figure has the synthesised id structure_jats requires.
    assert 'id="sec-1"' in jats and 'id="fig-1"' in jats


def test_pdf_path_structures_to_sentences() -> None:
    g = structure_jats(docling_dict_to_jats(_DOCLING_DICT), paper_iri=_PAPER)
    assert _count(g, "Section") >= 14
    assert _count(g, "Paragraph") > 50
    assert _count(g, "Sentence") > 200
    assert _count(g, "Figure") >= 10


def test_pdf_path_recovers_the_same_citable_sentence() -> None:
    """The headline: the PDF-derived graph carries the PPMS measurement sentence, with a
    resolvable sentence IRI + PROV — the same citable fact the JATS path holds."""
    g = structure_jats(
        docling_dict_to_jats(_DOCLING_DICT),
        paper_iri=_PAPER,
        conversion={"converter": "docling/2.x", "sourceFormat": "pdf"},
    )
    anchor = rdflib.URIRef(NIF + "anchorOf")
    hits = [
        (s, str(o)) for s, _, o in g.triples((None, anchor, None)) if _PPMS in str(o)
    ]
    assert len(hits) == 1, "the PPMS measurement sentence was not recovered from the PDF"
    sent_iri, text = hits[0]
    assert text.startswith("The transport properties at low temperatures")
    # resolvable sentence node, quoted from the paper (a citation, not a blob)
    assert (sent_iri, rdflib.URIRef("http://www.w3.org/ns/prov#wasQuotedFrom"),
            rdflib.URIRef(_PAPER)) in g


def test_adapter_is_deterministic() -> None:
    assert docling_dict_to_jats(_DOCLING_DICT) == docling_dict_to_jats(_DOCLING_DICT)


def test_adapter_skips_page_furniture() -> None:
    """Docling labels running heads/feet as page_header/page_footer — they are NOT body
    prose and must not become paragraphs (the fixture has 32 page_header items)."""
    jats = docling_dict_to_jats(_DOCLING_DICT)
    # The journal running head appears on many pages; it must not be a citable paragraph.
    assert "<p>Materials 2018" not in jats


def test_convert_pdf_to_jats_without_sidecar_degrades() -> None:
    """No ASTERISM_DOCLING_URL → a clear ConversionError (graceful degrade, like absent
    pandoc). No network is touched."""
    with pytest.raises(ConversionError, match="Docling sidecar"):
        convert_pdf_to_jats(b"%PDF-1.7 ...", sidecar_url=None)


def test_convert_pdf_to_jats_rejects_oversized() -> None:
    from asterism.documents import _MAX_PDF_BYTES

    with pytest.raises(ConversionError, match="too large"):
        convert_pdf_to_jats(b"%PDF-" + b"0" * (_MAX_PDF_BYTES + 1), sidecar_url="http://x")
