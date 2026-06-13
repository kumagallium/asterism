"""De-risk spike: a Word (.docx) document → the same citable document graph.

The easy-win sibling of the PDF spike (experiments/pdf-docling-spike/). Where PDF
needs a heavy layout-aware ML converter, **Word is already structured XML inside
the .docx (OOXML)**, so a deterministic, dependency-light converter (pandoc)
recovers the structure faithfully — and, crucially, pandoc emits **JATS** with
`<sec id><title><p>` nesting, the EXACT shape the document layer's existing JATS
path already ingests. So Word lands in the unchanged pipeline.

This matters for the real-world ask (legal contracts are usually Word): the
committed sample is a contract (Articles / Sections / numbered clauses), and the
spike resolves a specific clause — "Article 2.2, the 30-day cure period" — to a
citable IRI with a structural path (§2-2) and conversion provenance.

Trust model (same as PDF): the conversion (pandoc docx→JATS) is an offline,
provenance-recorded step (`lit:DocumentConversionActivity`, converter+version+date).
pandoc is deterministic (no ML), so Word sits HIGH on the confidence ladder —
essentially JATS-grade. The committed `sample_agreement.jats.xml` lets the spike
run with no pandoc dependency; `sample_agreement.docx` is the original source.

    PYTHONPATH=ingest/src ingest/.venv/bin/python experiments/word-pandoc-spike/run_spike.py
"""
from __future__ import annotations

import importlib.util as ilu
import xml.etree.ElementTree as ET
from pathlib import Path

from asterism.transforms import structural_slug, trim_collapse

_bpg = Path(__file__).resolve().parents[2] / "datasets" / "papers" / "seed" / "build_paper_graph.py"
_spec = ilu.spec_from_file_location("_bpg", _bpg)
_mod = ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
sentence_spans = _mod.sentence_spans  # reuse the JATS post-pass splitter (single source of truth)

HERE = Path(__file__).resolve().parent
DOC = "sample-agreement"
RES = "https://kumagallium.github.io/asterism/papers/resource/"
PAPER = f"{RES}document/{DOC}"
DOCO = "http://purl.org/spar/doco/"
PO = "http://www.essepuntato.it/2008/12/pattern#"
NIF = "http://persistence.uni-leipzig.de/nlp2rdf/ontologies/nif-core#"
DCTERMS = "http://purl.org/dc/terms/"
PROV = "http://www.w3.org/ns/prov#"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
LIT = "https://kumagallium.github.io/asterism/papers/ontology#"
CONVERTER = "pandoc/3.1.11.1 (docx -> jats)"
RUN = "2026-06-12"


def jats_to_doco(xml_text: str) -> str:
    """pandoc-JATS (article/body/sec@id/title/p) -> doco/nif Turtle. Deterministic.

    Sections keep pandoc's stable readable @id (e.g. 'term-and-termination', '2.1');
    the human-readable structural number is fn:structural_slug(title) — the SAME
    addressing scheme the JATS path uses. Paragraphs are positional; sentences via
    the shared deterministic splitter.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        root = ET.fromstring("<article><body>" + xml_text + "</body></article>")
    body = root.find(".//body")
    if body is None:
        body = root
    conv = f"{RES}activity/convert/{DOC}/run-{RUN}"
    parse = f"{RES}activity/parse/{DOC}/run-{RUN}"
    out: list[str] = []

    def lit(s: str, p: str, v: str, dt: str | None = None) -> None:
        v = v.replace("\\", "\\\\").replace('"', '\\"')
        out.append(f'<{s}> <{p}> "{v}"' + (f"^^<{dt}>" if dt else "") + " .")

    def iri(s: str, p: str, o: str) -> None:
        out.append(f"<{s}> <{p}> <{o}> .")

    iri(PAPER, RDF + "type", "http://purl.org/spar/fabio/Document")
    iri(PAPER, RDF + "type", PROV + "Entity")
    iri(PAPER, PROV + "wasGeneratedBy", parse)
    iri(conv, RDF + "type", LIT + "DocumentConversionActivity")
    iri(conv, RDF + "type", PROV + "Activity")
    lit(conv, LIT + "sourceFormat", "docx")
    lit(conv, LIT + "converter", CONVERTER)
    lit(conv, PROV + "endedAtTime", f"{RUN}T00:00:00Z", "http://www.w3.org/2001/XMLSchema#dateTime")
    iri(parse, RDF + "type", LIT + "DocumentParsingActivity")
    iri(parse, RDF + "type", PROV + "Activity")
    iri(parse, PROV + "wasInformedBy", conv)
    lit(parse, LIT + "parser", "asterism-doc/0.1")

    def emit_section(sec: ET.Element, parent: str) -> None:
        sid = sec.get("id")
        if not sid:
            return
        siri = f"{PAPER}/sec/{sid}"
        iri(parent, PO + "contains", siri)
        iri(siri, RDF + "type", DOCO + "Section")
        tnode = sec.find("title")
        title = trim_collapse("".join(tnode.itertext())) if tnode is not None else ""
        lit(siri, DCTERMS + "title", title)
        path = structural_slug(title)
        if path:
            lit(siri, LIT + "structuralPath", path)
        for k, p in enumerate(sec.findall("p")):
            ptext = trim_collapse("".join(p.itertext()))
            if not ptext:
                continue
            piri = f"{siri}/para/{k}"
            iri(siri, PO + "contains", piri)
            iri(piri, RDF + "type", DOCO + "Paragraph")
            lit(piri, NIF + "isString", ptext)
            for j, (a, b) in enumerate(sentence_spans(ptext)):
                sent = f"{piri}/sent/{j}"
                iri(piri, PO + "contains", sent)
                iri(sent, RDF + "type", DOCO + "Sentence")
                lit(sent, NIF + "anchorOf", ptext[a:b])
                iri(sent, PROV + "wasQuotedFrom", PAPER)
                iri(sent, PROV + "wasGeneratedBy", parse)
        for sub in sec.findall("sec"):
            emit_section(sub, siri)

    for sec in body.findall("sec"):
        emit_section(sec, PAPER)
    pre = (
        f"@prefix doco: <{DOCO}> .\n@prefix po: <{PO}> .\n@prefix nif: <{NIF}> .\n"
        f"@prefix dcterms: <{DCTERMS}> .\n@prefix prov: <{PROV}> .\n@prefix lit: <{LIT}> .\n\n"
    )
    return pre + "\n".join(out) + "\n"


def main() -> int:
    ttl = jats_to_doco((HERE / "sample_agreement.jats.xml").read_text(encoding="utf-8"))
    import rdflib

    g = rdflib.Graph()
    g.parse(data=ttl, format="turtle")
    n_sec = len(list(g.subjects(rdflib.RDF.type, rdflib.URIRef(DOCO + "Section"))))
    n_sent = len(list(g.subjects(rdflib.RDF.type, rdflib.URIRef(DOCO + "Sentence"))))
    print(f"Word (.docx) -> pandoc JATS -> doco/nif graph: {len(g)} triples "
          f"({n_sec} sections, {n_sent} sentences)")

    # cite a specific clause: Article 2.2 — the 30-day cure period.
    rows = list(g.query(
        """
        PREFIX doco: <http://purl.org/spar/doco/>
        PREFIX po: <http://www.essepuntato.it/2008/12/pattern#>
        PREFIX nif: <http://persistence.uni-leipzig.de/nlp2rdf/ontologies/nif-core#>
        PREFIX dcterms: <http://purl.org/dc/terms/>
        PREFIX lit: <https://kumagallium.github.io/asterism/papers/ontology#>
        SELECT ?sent ?text ?path ?title WHERE {
          ?sent a doco:Sentence ; nif:anchorOf ?text .
          ?para po:contains ?sent . ?sec po:contains ?para ; dcterms:title ?title .
          OPTIONAL { ?sec lit:structuralPath ?path }
          FILTER(CONTAINS(?text, "thirty (30) days"))
        }
        """
    ))
    assert rows, "the termination clause was not recovered from the .docx"
    sent, text, path, title = (str(x) for x in rows[0])
    print("\nCITATION recovered from the Word document:")
    print(f"  §{path} / {title}")
    print(f"  “{text}”")
    print(f"  IRI: {sent}")
    print("\nOK: a Word .docx, via a deterministic provenance-recorded conversion (pandoc),"
          " lands in the same citable document graph — addressable to the clause.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
