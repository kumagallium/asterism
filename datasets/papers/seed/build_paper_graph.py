"""Content tool: real JATS full-text XML → the committed document-structure graph.

This is the deterministic post-pass of the document-ontology layer (ADR
``docs/architecture/document-ontology-layer.md``). It is the twin of
``datasets/materials_project/seed/build_seed.py``: a human-run, offline content
tool that turns a vetted structured source into a committed ABox
(``seed/paper.ttl``). It is **never executed on the runtime ingest path** — the
substrate only ever runs the declarative ``jats/PMC5951533.rml.ttl`` through
Morph-KGC. The same trust model as the seeded ``mp.ttl``.

Why a post-pass exists at all (and why it is the *honest* design):
  * The declarative RML (``jats/PMC5951533.rml.ttl``) covers what JATS carries
    natively with stable identity — the paper, every ``<sec>``/nested ``<sec>``
    (all have ``@id``), every ``<fig>`` (``@id`` + label), and the ``po:contains``
    tree. This tool reproduces that **same skeleton** (identical IRIs / types /
    containment / ``lit:structuralPath`` — via the SAME ``asterism.transforms``
    function the RML's ``fn:structural_slug`` binds) so the RML output is a strict
    subset of this graph.
  * On top, it adds what RML cannot express on real JATS: ``doco:Paragraph``
    (positional — real ``<p>`` have no ``@id``), ``doco:Sentence`` (JATS has no
    sentence element), faithful ``nif:isString`` verbatim (Morph-KGC's stdlib
    ElementTree reader truncates mixed content like ``<sub>``/``<italic>``), and
    ``nif:`` character offsets (stateful). Sentence segmentation is recorded as a
    **dated, low-confidence claim** under a ``lit:DocumentParsingActivity`` — the
    structure (= this *is* §4) is high-confidence; the sentence boundaries are a
    parser's claim. That auditable split is exactly what separates this from an
    LLM black box.

Deterministic + idempotent: same JATS in → byte-identical ``paper.ttl`` out
(the only timestamp, ``prov:endedAtTime``, is the fixed ``PARSE_RUN`` constant,
never ``now()`` — so a re-ingest yields the same graph: MVP gate §B.4).

Usage (needs the asterism package on the path for the single-sourced slug logic):

    PYTHONPATH=ingest/src python datasets/papers/seed/build_paper_graph.py \
        datasets/papers/jats/PMC5951533.xml datasets/papers/seed/paper.ttl
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from asterism.documents import sentence_spans  # single source of truth for the splitter
from asterism.transforms import structural_slug, trim_collapse

# --- identity (matches jats/PMC5951533.rml.ttl and dataset.toml — IRIs are data
# identity; keep these byte-stable) ----------------------------------------------
PMCID = "PMC5951533"
DOI = "10.3390/ma11040649"
RESOURCE = "https://kumagallium.github.io/asterism/papers/resource/"
PAPER = f"{RESOURCE}paper/{PMCID}"
TITLE = "On the Phase Separation in n-Type Thermoelectric Half-Heusler Materials"

# The parse run is a FIXED constant (not now()) so the graph is reproducible and a
# re-ingest is byte-identical (idempotency gate). Bump it only on a real re-parse.
PARSE_RUN = "2026-06-11"
PARSE_ACTIVITY = f"{RESOURCE}activity/parse/{PMCID}/run-{PARSE_RUN}"
PARSER_ID = "asterism-jats/0.1"
SOURCE_FORMAT = "jats"
CONTEXT = f"{PAPER}/fulltext"

# --- namespaces ------------------------------------------------------------------
FABIO = "http://purl.org/spar/fabio/"
DOCO = "http://purl.org/spar/doco/"
DEO = "http://purl.org/spar/deo/"
PO = "http://www.essepuntato.it/2008/12/pattern#"
NIF = "http://persistence.uni-leipzig.de/nlp2rdf/ontologies/nif-core#"
DCTERMS = "http://purl.org/dc/terms/"
PROV = "http://www.w3.org/ns/prov#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
XSD = "http://www.w3.org/2001/XMLSchema#"
LIT = "https://kumagallium.github.io/asterism/papers/ontology#"

_PREFIXES = {
    "fabio": FABIO, "doco": DOCO, "deo": DEO, "po": PO, "nif": NIF,
    "dcterms": DCTERMS, "prov": PROV, "rdfs": RDFS, "rdf": RDF, "xsd": XSD,
    "lit": LIT,
}

# Map a top-level section heading to its DEO rhetorical role (deterministic keyword
# match on the heading; an unmatched heading simply gets no role — best-effort).
_DEO_ROLES = [
    ("introduction", "Introduction"),
    ("method", "Methods"),
    ("material", "Materials"),
    ("result", "Results"),
    ("discussion", "Discussion"),
    ("conclusion", "Conclusion"),
]

# ---------------------------------------------------------------------------
# text helper (sentence segmentation lives in asterism.documents — shared)
# ---------------------------------------------------------------------------
def _text(el: ET.Element) -> str:
    """Faithful, whitespace-collapsed text of an element incl. all mixed content."""
    return trim_collapse("".join(el.itertext()))


# ---------------------------------------------------------------------------
# turtle emission (stable, sorted — byte-deterministic diffs)
# ---------------------------------------------------------------------------
def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "")


class Graph:
    def __init__(self) -> None:
        self._t: set[tuple[str, str, str]] = set()

    def add(self, s: str, p: str, o: str) -> None:
        self._t.add((s, p, o))

    def iri(self, s: str, p: str, o: str) -> None:
        self.add(s, p, f"<{o}>")

    def lit(self, s: str, p: str, value: str, *, dt: str | None = None) -> None:
        if value == "":
            return
        obj = f'"{_esc(value)}"'
        if dt:
            obj += f"^^<{dt}>"
        self.add(s, p, obj)

    def serialize(self) -> str:
        head = "".join(f"@prefix {k}: <{v}> .\n" for k, v in _PREFIXES.items())
        body = "\n".join(f"<{s}> <{p}> {o} ." for s, p, o in sorted(self._t))
        return head + "\n" + body + "\n"


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------
def build(jats_path: Path) -> str:
    root = ET.parse(jats_path).getroot()
    body = root.find("body")
    if body is None:
        raise SystemExit("no <body> in JATS")
    g = Graph()

    # paper (mirrors PaperMap)
    g.iri(PAPER, RDF + "type", FABIO + "ResearchPaper")
    g.iri(PAPER, RDF + "type", PROV + "Entity")
    g.lit(PAPER, DCTERMS + "identifier", DOI)
    g.lit(PAPER, LIT + "pmcid", PMCID)
    g.lit(PAPER, DCTERMS + "title", TITLE)
    g.iri(PAPER, PROV + "wasGeneratedBy", PARSE_ACTIVITY)

    # parse activity (the dated claim envelope)
    g.iri(PARSE_ACTIVITY, RDF + "type", LIT + "DocumentParsingActivity")
    g.iri(PARSE_ACTIVITY, RDF + "type", PROV + "Activity")
    g.iri(PARSE_ACTIVITY, PROV + "used", PAPER)
    g.lit(PARSE_ACTIVITY, LIT + "sourceFormat", SOURCE_FORMAT)
    g.lit(PARSE_ACTIVITY, LIT + "parser", PARSER_ID)
    g.lit(PARSE_ACTIVITY, PROV + "endedAtTime", f"{PARSE_RUN}T00:00:00Z", dt=XSD + "dateTime")

    # fulltext context node (NIF) — built up as paragraphs are emitted
    context_parts: list[str] = []

    def sec_iri(sec_id: str) -> str:
        return f"{PAPER}/sec/{sec_id}"

    def fig_iri(fig_id: str) -> str:
        return f"{PAPER}/fig/{fig_id}"

    # paper -> top sections
    for sec in body.findall("sec"):
        sid = sec.get("id")
        if sid:
            g.iri(PAPER, PO + "contains", sec_iri(sid))

    def emit_section(sec: ET.Element, *, top: bool) -> None:
        sid = sec.get("id")
        if not sid:
            return
        siri = sec_iri(sid)
        g.iri(siri, RDF + "type", DOCO + "Section")
        title_el = sec.find("title")
        title = _text(title_el) if title_el is not None else ""
        g.lit(siri, DCTERMS + "title", title)
        g.lit(siri, LIT + "structuralPath", structural_slug(title))  # SAME fn as the RML
        if top:
            low = title.lower()
            for kw, role in _DEO_ROLES:
                if kw in low:
                    g.iri(siri, RDF + "type", DEO + role)

        # contained subsections
        for sub in sec.findall("sec"):
            sub_id = sub.get("id")
            if sub_id:
                g.iri(siri, PO + "contains", sec_iri(sub_id))
        # contained direct-child figures
        for fig in sec.findall("fig"):
            fid = fig.get("id")
            if fid:
                g.iri(siri, PO + "contains", fig_iri(fid))
                emit_figure(fig)
        # contained paragraphs (positional — real <p> have no @id)
        for k, p in enumerate(sec.findall("p")):
            ptext = _text(p)
            if not ptext:
                continue
            piri = f"{siri}/para/{k}"
            g.iri(siri, PO + "contains", piri)
            g.iri(piri, RDF + "type", DOCO + "Paragraph")
            g.lit(piri, NIF + "isString", ptext)
            para_start = sum(len(x) for x in context_parts)
            context_parts.append(ptext + "\n")
            # sentences
            for j, (a, b) in enumerate(sentence_spans(ptext)):
                stext = ptext[a:b]
                seniri = f"{piri}/sent/{j}"
                g.iri(piri, PO + "contains", seniri)
                g.iri(seniri, RDF + "type", DOCO + "Sentence")
                g.lit(seniri, NIF + "anchorOf", stext)
                g.iri(seniri, NIF + "referenceContext", CONTEXT)
                g.lit(seniri, NIF + "beginIndex", str(para_start + a), dt=XSD + "nonNegativeInteger")
                g.lit(seniri, NIF + "endIndex", str(para_start + b), dt=XSD + "nonNegativeInteger")
                g.iri(seniri, PROV + "wasQuotedFrom", PAPER)
                g.iri(seniri, PROV + "wasGeneratedBy", PARSE_ACTIVITY)
        for sub in sec.findall("sec"):
            emit_section(sub, top=False)

    def emit_figure(fig: ET.Element) -> None:
        fid = fig.get("id")
        if not fid:
            return
        firi = fig_iri(fid)
        g.iri(firi, RDF + "type", DOCO + "Figure")
        label_el = fig.find("label")
        if label_el is not None:
            g.lit(firi, RDFS + "label", _text(label_el))  # "Figure 3" — clean
        cap = fig.find("caption")
        if cap is not None:
            captext = _text(cap)
            if captext:
                ciri = f"{firi}/caption"
                g.iri(firi, PO + "contains", ciri)
                g.iri(ciri, RDF + "type", DOCO + "Caption")
                g.lit(ciri, NIF + "isString", captext)

    for sec in body.findall("sec"):
        emit_section(sec, top=True)

    # finalize the context node
    g.iri(CONTEXT, RDF + "type", NIF + "Context")
    g.lit(CONTEXT, NIF + "isString", "".join(context_parts))
    g.iri(CONTEXT, NIF + "predLang", "http://lexvo.org/id/iso639-3/eng")

    return g.serialize()


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: build_paper_graph.py <jats.xml> <out.ttl>", file=sys.stderr)
        return 2
    ttl = build(Path(argv[1]))
    Path(argv[2]).write_text(ttl, encoding="utf-8")
    n = ttl.count(" .\n")
    print(f"wrote {argv[2]} ({n} triples)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
