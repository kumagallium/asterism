"""De-risk spike: an UNSTRUCTURED PDF → the same citable document-ontology graph.

The document-ontology layer (ADR docs/architecture/document-ontology-layer.md)
ingests JATS XML — a *structured* source. The real-world need is PDF/Word, which
are unstructured. This spike proves the missing link: a layout-aware converter
(here Docling, IBM) recovers the structure from the PDF, and that structure lands
in the SAME deterministic post-pass + the SAME typed citation tools — so a
PDF-derived paper is queryable down to the sentence with a resolvable citation,
exactly like the JATS one.

Trust model (the honest design): the ML converter is an OFFLINE, provenance-
recorded step — it is NOT in the runtime ingest path. Its output (this committed
markdown, derived from the CC-BY PDF) is the structured source; the conversion is
stamped as a ``lit:DocumentConversionActivity`` (converter + version + date), one
rung of confidence below JATS. Re-running the deterministic post-pass over the
committed converter output is byte-stable; only the converter step needs pinned
model versions. The committed ``ma11040649.docling.md`` lets this spike run with
NO torch/Docling dependency.

Ground truth: the SAME paper (PMC5951533 / 10.3390/ma11040649) is also ingested
from real JATS in datasets/papers/. This spike asserts the PDF path recovers the
SAME measurement-condition sentence (the PPMS/TTO sentence in §4) the JATS path
cited — i.e. the two paths agree on the citable fact.

    PYTHONPATH=ingest/src ingest/.venv/bin/python experiments/pdf-docling-spike/run_spike.py
"""
from __future__ import annotations

import re
from pathlib import Path

from asterism.transforms import structural_slug, trim_collapse

# Reuse the deterministic sentence splitter the JATS post-pass uses (single source
# of truth) — the PDF path differs only in where the *structure* comes from.
import importlib.util as _ilu  # noqa: E402

_bpg = Path(__file__).resolve().parents[1].parent / "datasets" / "papers" / "seed" / "build_paper_graph.py"
_spec = _ilu.spec_from_file_location("_bpg", _bpg)
_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
sentence_spans = _mod.sentence_spans

HERE = Path(__file__).resolve().parent
DOI = "10.3390/ma11040649"
RES = "https://kumagallium.github.io/asterism/papers/resource/"
PAPER = f"{RES}paper/via-pdf/{DOI.replace('/', '_')}"
DOCO = "http://purl.org/spar/doco/"
PO = "http://www.essepuntato.it/2008/12/pattern#"
NIF = "http://persistence.uni-leipzig.de/nlp2rdf/ontologies/nif-core#"
DCTERMS = "http://purl.org/dc/terms/"
PROV = "http://www.w3.org/ns/prov#"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
LIT = "https://kumagallium.github.io/asterism/papers/ontology#"
CONVERTER = "docling/2.101.0 (docling-ibm-models/3.13.3)"
CONVERT_RUN = "2026-06-12"


def parse_markdown(md: str) -> list[tuple[str, list[str]]]:
    """Docling markdown -> [(heading, [paragraph, ...])]. Deterministic, stdlib."""
    sections: list[tuple[str, list[str]]] = []
    cur: tuple[str, list[str]] | None = None
    buf: list[str] = []

    def flush() -> None:
        if cur is not None and buf:
            text = trim_collapse(re.sub(r"_([^_]+)_", r"\1", " ".join(buf)))
            if text:
                cur[1].append(text)
        buf.clear()

    for line in md.splitlines():
        if re.match(r"^#{1,4}\s", line):
            flush()
            cur = (re.sub(r"^#{1,4}\s", "", line).strip(), [])
            sections.append(cur)
        elif not line.strip() or line.startswith(("<!--", "|", "![")):
            flush()
        elif cur is not None:
            buf.append(line.strip())
    flush()
    return sections


def build_turtle(sections: list[tuple[str, list[str]]]) -> str:
    pre = (
        f"@prefix doco: <{DOCO}> .\n@prefix po: <{PO}> .\n@prefix nif: <{NIF}> .\n"
        f"@prefix dcterms: <{DCTERMS}> .\n@prefix prov: <{PROV}> .\n@prefix lit: <{LIT}> .\n"
        f"@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .\n@prefix fabio: <http://purl.org/spar/fabio/> .\n\n"
    )
    conv = f"{RES}activity/convert/{DOI.replace('/', '_')}/run-{CONVERT_RUN}"
    parse = f"{RES}activity/parse/via-pdf/{DOI.replace('/', '_')}/run-{CONVERT_RUN}"
    out: list[str] = []

    def lit(s: str, p: str, v: str, dt: str | None = None) -> None:
        v = v.replace("\\", "\\\\").replace('"', '\\"')
        out.append(f'<{s}> <{p}> "{v}"' + (f"^^<{dt}>" if dt else "") + " .")

    def iri(s: str, p: str, o: str) -> None:
        out.append(f"<{s}> <{p}> <{o}> .")

    iri(PAPER, RDF + "type", "http://purl.org/spar/fabio/ResearchPaper")
    iri(PAPER, RDF + "type", PROV + "Entity")
    lit(PAPER, DCTERMS + "identifier", DOI)
    iri(PAPER, PROV + "wasGeneratedBy", parse)
    # the conversion claim (ML converter — a dated, version-pinned provenance step)
    iri(conv, RDF + "type", LIT + "DocumentConversionActivity")
    iri(conv, RDF + "type", PROV + "Activity")
    lit(conv, LIT + "sourceFormat", "pdf")
    lit(conv, LIT + "converter", CONVERTER)
    lit(conv, PROV + "endedAtTime", f"{CONVERT_RUN}T00:00:00Z", "http://www.w3.org/2001/XMLSchema#dateTime")
    # the parse activity is informed by the conversion (provenance chain)
    iri(parse, RDF + "type", LIT + "DocumentParsingActivity")
    iri(parse, RDF + "type", PROV + "Activity")
    iri(parse, PROV + "wasInformedBy", conv)
    lit(parse, LIT + "sourceFormat", "pdf-docling-markdown")
    lit(parse, LIT + "parser", "asterism-doc/0.1")
    lit(parse, PROV + "endedAtTime", f"{CONVERT_RUN}T00:00:00Z", "http://www.w3.org/2001/XMLSchema#dateTime")

    for si, (heading, paras) in enumerate(sections):
        path = structural_slug(heading)
        # PDF has no @id; key the section IRI by its structural number, else position.
        key = path or f"p{si}"
        sec = f"{PAPER}/sec/{key}"
        iri(PAPER, PO + "contains", sec)
        iri(sec, RDF + "type", DOCO + "Section")
        lit(sec, DCTERMS + "title", heading)
        if path:
            lit(sec, LIT + "structuralPath", path)
        for k, ptext in enumerate(paras):
            para = f"{sec}/para/{k}"
            iri(sec, PO + "contains", para)
            iri(para, RDF + "type", DOCO + "Paragraph")
            lit(para, NIF + "isString", ptext)
            for j, (a, b) in enumerate(sentence_spans(ptext)):
                sent = f"{para}/sent/{j}"
                iri(para, PO + "contains", sent)
                iri(sent, RDF + "type", DOCO + "Sentence")
                lit(sent, NIF + "anchorOf", ptext[a:b])
                iri(sent, PROV + "wasQuotedFrom", PAPER)
                iri(sent, PROV + "wasGeneratedBy", parse)
    return pre + "\n".join(out) + "\n"


def main() -> int:
    sections = parse_markdown((HERE / "ma11040649.docling.md").read_text(encoding="utf-8"))
    ttl = build_turtle(sections)
    import rdflib

    g = rdflib.Graph()
    g.parse(data=ttl, format="turtle")
    n_sec = len(list(g.subjects(rdflib.RDF.type, rdflib.URIRef(DOCO + "Section"))))
    n_para = len(list(g.subjects(rdflib.RDF.type, rdflib.URIRef(DOCO + "Paragraph"))))
    n_sent = len(list(g.subjects(rdflib.RDF.type, rdflib.URIRef(DOCO + "Sentence"))))
    print(f"PDF -> Docling -> doco/nif graph: {len(g)} triples "
          f"({n_sec} sections, {n_para} paragraphs, {n_sent} sentences)")

    # the headline: find the SAME measurement-condition sentence the JATS path cited,
    # via the SAME kind of full-text-down-to-the-sentence query.
    rows = list(g.query(
        """
        PREFIX doco: <http://purl.org/spar/doco/>
        PREFIX po: <http://www.essepuntato.it/2008/12/pattern#>
        PREFIX nif: <http://persistence.uni-leipzig.de/nlp2rdf/ontologies/nif-core#>
        PREFIX dcterms: <http://purl.org/dc/terms/>
        PREFIX prov: <http://www.w3.org/ns/prov#>
        PREFIX lit: <https://kumagallium.github.io/asterism/papers/ontology#>
        SELECT ?sent ?text ?path ?title ?conv WHERE {
          ?sent a doco:Sentence ; nif:anchorOf ?text ; prov:wasGeneratedBy ?parse .
          ?para po:contains ?sent . ?sec po:contains ?para ; dcterms:title ?title .
          OPTIONAL { ?sec lit:structuralPath ?path }
          ?parse prov:wasInformedBy ?conv .
          FILTER(CONTAINS(?text, "physical properties measurement system"))
        }
        """
    ))
    assert rows, "PPMS sentence not recovered from the PDF path"
    sent, text, path, title, conv = (str(x) for x in rows[0])
    print("\nCITATION recovered from the PDF (no JATS):")
    print(f"  §{path} / {title}")
    print(f"  “{text}”")
    print(f"  IRI: {sent}")
    print(f"  via conversion: {conv.rsplit('/', 2)[-2]} ({CONVERTER})")

    # cross-check: the JATS path cited the SAME verbatim (the two sources agree).
    jats = rdflib.Graph()
    jats.parse(HERE.parents[1] / "datasets" / "papers" / "seed" / "paper.ttl", format="turtle")
    jats_ppms = [str(o) for s, p, o in jats.triples((None, rdflib.URIRef(NIF + "anchorOf"), None))
                 if "physical properties measurement system" in str(o)]
    agree = bool(jats_ppms) and jats_ppms[0].strip() == text.strip()
    print(f"\nPDF-derived sentence == JATS-derived sentence (same citable fact): "
          f"{'✓ IDENTICAL' if agree else '≈ (see both)'}")
    if not agree and jats_ppms:
        print(f"  JATS: “{jats_ppms[0]}”")
    print("\nOK: an unstructured PDF, via a provenance-recorded conversion, lands in the "
          "same citable document graph as JATS — down to the sentence.")
    return 0 if agree else 1


if __name__ == "__main__":
    raise SystemExit(main())
