"""Tests for asterism.documents — the runtime JATS-document structurer.

Covers: the deterministic sentence splitter, generic JATS → doco/nif structuring
(sections + structuralPath + DEO role, paragraphs, sentences + offsets + PROV,
figures + captions), document-identity derivation, idempotency (same bytes → same
graph), and the security posture (untrusted XML: entity-expansion refused).
"""
from __future__ import annotations

import pytest
import rdflib
from defusedxml.common import EntitiesForbidden

from asterism.documents import (
    DOCO,
    LIT,
    NIF,
    JatsDocumentError,
    derive_doc_id,
    sentence_spans,
    structure_jats,
)

_JATS = """<?xml version="1.0"?>
<article>
  <front><article-meta>
    <article-id pub-id-type="pmcid">PMC-TEST</article-id>
    <article-id pub-id-type="doi">10.1234/demo</article-id>
    <title-group><article-title>A <italic>n</italic>-type demo</article-title></title-group>
    <pub-date><year>2020</year><month>3</month><day>4</day></pub-date>
  </article-meta></front>
  <body>
    <sec id="s1"><title>1. Introduction</title>
      <p>Background sentence one. Background sentence two.</p></sec>
    <sec id="s2"><title>2. Results</title>
      <sec id="s2-1"><title>2.1. Sub</title>
        <p>Measured at 300 K under Ar.</p>
        <fig id="f1"><label>Figure 1</label><caption><p>A caption.</p></caption></fig>
      </sec></sec>
  </body>
</article>"""

BASE = "https://kumagallium.github.io/asterism/papers/resource/document/ds/PMC-TEST"


def _g() -> rdflib.Graph:
    return structure_jats(_JATS, paper_iri=BASE)


def test_sentence_spans_basic() -> None:
    text = "First. Second sentence. The value is 1.5 here."
    spans = sentence_spans(text)
    assert [text[a:b] for a, b in spans] == [
        "First.",
        "Second sentence.",
        "The value is 1.5 here.",  # the decimal 1.5 is NOT a boundary
    ]


def test_structure_sections_and_roles() -> None:
    g = _g()
    secs = {str(s) for s in g.subjects(rdflib.RDF.type, rdflib.URIRef(DOCO + "Section"))}
    assert {BASE + "/sec/s1", BASE + "/sec/s2", BASE + "/sec/s2-1"} == secs
    # structuralPath from the heading, and a DEO role on the top section.
    path = rdflib.URIRef(LIT + "structuralPath")
    assert (rdflib.URIRef(BASE + "/sec/s2-1"), path, rdflib.Literal("2-1")) in g
    deo_intro = rdflib.URIRef("http://purl.org/spar/deo/Introduction")
    assert (rdflib.URIRef(BASE + "/sec/s1"), rdflib.RDF.type, deo_intro) in g


def test_structure_sentence_has_offsets_and_prov() -> None:
    g = _g()
    sents = list(g.subjects(rdflib.RDF.type, rdflib.URIRef(DOCO + "Sentence")))
    assert len(sents) == 3  # 2 in Intro + 1 in the subsection
    # the subsection sentence carries verbatim + offsets + provenance to the paper.
    hit = [s for s in sents if "Ar." in str(g.value(s, rdflib.URIRef(NIF + "anchorOf")))]
    assert hit
    s = hit[0]
    assert g.value(s, rdflib.URIRef(NIF + "beginIndex")) is not None
    quoted = rdflib.URIRef("http://www.w3.org/ns/prov#wasQuotedFrom")
    assert g.value(s, quoted) == rdflib.URIRef(BASE)


def test_figure_and_caption() -> None:
    g = _g()
    assert (rdflib.URIRef(BASE + "/fig/f1"), rdflib.RDF.type,
            rdflib.URIRef(DOCO + "Figure")) in g
    cap = rdflib.URIRef(BASE + "/fig/f1/caption")
    assert (cap, rdflib.RDF.type, rdflib.URIRef(DOCO + "Caption")) in g
    assert str(g.value(cap, rdflib.URIRef(NIF + "isString"))) == "A caption."


def test_identity_and_parse_activity() -> None:
    g = _g()
    paper = rdflib.URIRef(BASE)
    assert str(g.value(paper, rdflib.URIRef(LIT + "pmcid"))) == "PMC-TEST"
    ident = rdflib.URIRef("http://purl.org/dc/terms/identifier")
    assert str(g.value(paper, ident)) == "10.1234/demo"
    act = g.value(paper, rdflib.URIRef("http://www.w3.org/ns/prov#wasGeneratedBy"))
    assert (act, rdflib.RDF.type, rdflib.URIRef(LIT + "DocumentParsingActivity")) in g
    # endedAtTime comes from the document's pub-date (deterministic, never now()).
    ended = rdflib.URIRef("http://www.w3.org/ns/prov#endedAtTime")
    assert "2020-03-04" in str(g.value(act, ended))


def test_idempotent_same_bytes_same_graph() -> None:
    a, b = structure_jats(_JATS, paper_iri=BASE), structure_jats(_JATS, paper_iri=BASE)
    assert set(a) == set(b)  # same document → identical graph (no now())


def test_derive_doc_id_prefers_pmcid() -> None:
    assert derive_doc_id(_JATS, fallback="upload.xml") == "PMC-TEST"
    plain = "<article><body><sec id='s'/></body></article>"
    assert derive_doc_id(plain, fallback="My Doc.xml") == "My-Doc.xml"


def test_no_body_rejected() -> None:
    with pytest.raises(JatsDocumentError):
        structure_jats("<article><front/></article>", paper_iri=BASE)


def test_entity_expansion_attack_refused() -> None:
    # "billion laughs" — must be refused, not expanded (defusedxml).
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "AAAA"><!ENTITY b "&a;&a;&a;&a;">]>'
        '<article><body><sec id="s"><p>&b;</p></sec></body></article>'
    )
    with pytest.raises(EntitiesForbidden):
        structure_jats(bomb, paper_iri=BASE)
