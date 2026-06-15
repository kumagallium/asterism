"""Generic, deterministic JATS-document → ``doco``/``nif`` structurer (runtime path).

The document-ontology layer (ADR ``docs/architecture/document-ontology-layer.md``)
ingests a structured document (JATS) to resolvable, citable IRIs down to the
sentence. The MVP produced the example ``papers`` dataset with an OFFLINE content
tool (``datasets/papers/seed/build_paper_graph.py``). This module is the **runtime**
counterpart: a single, fixed, vetted, deterministic structurer the API ingest path
runs on an *uploaded* document — so a user can upload a JATS file through the
catalog and get a sentence-level citable graph, with no schema design step.

Trust model (CLAUDE.md「生成コードを実行しない」): like the Tier 0 function library
and Morph-KGC, this executes **no code from the document** — it is a closed,
once-vetted parser. It only reads XML structure. Uploaded XML is untrusted, so it
is parsed with :mod:`defusedxml` (entity-expansion / external-entity attacks
refused; the harmless JATS DOCTYPE declaration is allowed).

Determinism / idempotency: same document in → same graph out (the only "timestamp",
the parse activity's ``prov:endedAtTime``, is the document's own publication date
when present, never ``now()``; the activity IRI carries a content hash so a
re-ingest of identical bytes yields identical IRIs). Sentence segmentation is a
**dated claim** under a ``lit:DocumentParsingActivity`` — the structure (= this *is*
§4) is high-confidence; sentence boundaries are the parser's claim.
"""
from __future__ import annotations

import hashlib
import re

from asterism.transforms import structural_slug, trim_collapse

# --- namespaces (reused SPAR/NIF/PROV + the doc layer's own lit:) ----------------
FABIO = "http://purl.org/spar/fabio/"
DOCO = "http://purl.org/spar/doco/"
DEO = "http://purl.org/spar/deo/"
PO = "http://www.essepuntato.it/2008/12/pattern#"
NIF = "http://persistence.uni-leipzig.de/nlp2rdf/ontologies/nif-core#"
DCTERMS = "http://purl.org/dc/terms/"
PROV = "http://www.w3.org/ns/prov#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
XSD = "http://www.w3.org/2001/XMLSchema#"
LIT = "https://kumagallium.github.io/asterism/papers/ontology#"

PARSER_ID = "asterism-doc/0.1"

# Map a top-level section heading to its DEO rhetorical role (deterministic keyword
# match; an unmatched heading simply gets no role — best-effort enrichment).
_DEO_ROLES: tuple[tuple[str, str], ...] = (
    ("introduction", "Introduction"),
    ("method", "Methods"),
    ("material", "Materials"),
    ("result", "Results"),
    ("discussion", "Discussion"),
    ("conclusion", "Conclusion"),
)

# Abbreviations whose trailing "." is NOT a sentence boundary (scientific prose).
_ABBREV = {
    "fig", "figs", "eq", "eqs", "ref", "refs", "no", "vs", "etc", "al", "e.g",
    "i.e", "cf", "approx", "ca", "wt", "vol", "mol", "min", "max", "resp", "calc",
    "exp", "temp", "ed", "eds", "pp", "vols", "nos", "dr", "prof",
}


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """Split into [start, end) sentence spans over ``text`` (exact substrings).

    Conservative, deterministic rules tuned for scientific / legal prose: a boundary
    is ``. ! ?`` followed by whitespace then an uppercase/open-quote/paren (or EOS),
    UNLESS the char before it is a digit (decimal / "99.95%."), or the preceding
    token is a known abbreviation or a one-letter initial. Imperfect by design —
    recorded as a ``lit:DocumentParsingActivity`` claim, not a fact. (This is the
    single source of truth for the splitter, shared with the offline papers content
    tool.)
    """
    spans: list[tuple[int, int]] = []
    start, i, n = 0, 0, len(text)
    while i < n:
        if text[i] in ".!?":
            j = i + 1
            while j < n and text[j] == " ":
                j += 1
            nxt = text[j] if j < n else ""
            prev = text[i - 1] if i > 0 else ""
            ends = nxt == "" or nxt.isupper() or nxt in "([“‘\"'"  # noqa: RUF001 (typographic quotes can open a sentence)
            if j > i + 1 and ends and not prev.isdigit():
                m = re.search(r"(\S+)$", text[start : i + 1])
                word = m.group(1).rstrip(".!?").lower() if m else ""
                if word not in _ABBREV and not (len(word) == 1 and word.isalpha()):
                    spans.append((start, i + 1))
                    start = j
                    i = j
                    continue
        i += 1
    if start < n and text[start:].strip():
        spans.append((start, n))
    out: list[tuple[int, int]] = []
    for s, e in spans:
        while s < e and text[s] == " ":
            s += 1
        while e > s and text[e - 1] == " ":
            e -= 1
        if e > s:
            out.append((s, e))
    return out


def _safe_parse(xml_text: str):
    """Parse untrusted XML with defusedxml — refuse entity-expansion / external entities."""
    from defusedxml.ElementTree import fromstring

    # forbid_dtd=False keeps the harmless JATS <!DOCTYPE> declaration legal, while
    # forbid_entities / forbid_external block the actual attacks (billion laughs,
    # SSRF / local file read via an external entity).
    return fromstring(xml_text, forbid_dtd=False, forbid_entities=True, forbid_external=True)


def _text(el) -> str:
    """Faithful, whitespace-collapsed text of an element incl. all mixed content."""
    return trim_collapse("".join(el.itertext()))


def _block_paragraphs(container) -> list[str]:
    """Prose blocks directly in ``container``, in document order.

    Captures ``<p>`` *and* list items (``<list>/<list-item>``) that are direct
    content of the section/body — meeting notes, minutes and contracts carry most
    of their text as bullet lists, which earlier versions dropped. Does NOT descend
    into nested ``<sec>`` (those are emitted as their own sections), so no text is
    double-counted. Each top-level ``<list-item>`` becomes one block (its own
    nested sub-bullets fold into that block's text).
    """
    out: list[str] = []
    for child in container:
        if child.tag == "p":
            t = _text(child)
            if t:
                out.append(t)
        elif child.tag == "list":
            for item in child.findall("list-item"):
                t = _text(item)
                if t:
                    out.append(t)
    return out


def _article_id(meta, kind: str) -> str:
    if meta is None:
        return ""
    for aid in meta.findall("article-id"):
        if aid.get("pub-id-type") == kind:
            return (aid.text or "").strip()
    return ""


class JatsDocumentError(ValueError):
    """The uploaded XML is not an ingestible JATS document (no <body>)."""


def structure_jats(
    xml_text: str,
    *,
    paper_iri: str,
    parser_id: str = PARSER_ID,
    conversion: dict[str, str] | None = None,
) -> object:
    """Structure a JATS document into a ``doco``/``nif`` :class:`rdflib.Graph`.

    ``paper_iri`` is the resolvable IRI for the document node; all section / figure /
    paragraph / sentence IRIs hang off it. Returns the full citable graph (paper +
    section tree with ``lit:structuralPath`` + DEO roles, paragraphs, sentences with
    NIF offsets + PROV, figures + captions, the NIF context, and the dated
    ``lit:DocumentParsingActivity``). Raises :class:`JatsDocumentError` if there is
    no ``<body>``.

    ``conversion`` (optional) records that the JATS was produced from another format
    by an external converter (e.g. ``{"converter": "pandoc/3.1", "sourceFormat":
    "docx"}``): a ``lit:DocumentConversionActivity`` is emitted and the parse activity
    ``prov:wasInformedBy`` it — the "disclosed, version-pinned conversion" claim
    (vs RAG hiding it). One confidence rung below a native JATS source.
    """
    import rdflib

    root = _safe_parse(xml_text)
    body = root.find("body") if root.tag == "article" else root.find(".//body")
    if body is None:
        raise JatsDocumentError("no <body> element — not an ingestible JATS document")

    meta = root.find(".//article-meta")
    run = hashlib.sha256(xml_text.encode("utf-8")).hexdigest()[:12]
    parse_iri = f"{paper_iri}/activity/parse/run-{run}"
    context_iri = f"{paper_iri}/fulltext"
    g = rdflib.Graph()
    U, L = rdflib.URIRef, rdflib.Literal
    RDF = rdflib.RDF

    def lit(s: str, p: str, v: str, dt: str | None = None) -> None:
        if v == "":
            return
        g.add((U(s), U(p), L(v, datatype=U(dt) if dt else None)))

    def iri(s: str, p: str, o: str) -> None:
        g.add((U(s), U(p), U(o)))

    # paper node + identity
    iri(paper_iri, RDF.type, FABIO + "ResearchPaper")
    iri(paper_iri, RDF.type, PROV + "Entity")
    iri(paper_iri, PROV + "wasGeneratedBy", parse_iri)
    pmcid, doi = _article_id(meta, "pmcid"), _article_id(meta, "doi")
    lit(paper_iri, LIT + "pmcid", pmcid)
    lit(paper_iri, DCTERMS + "identifier", doi)
    title_el = meta.find(".//article-title") if meta is not None else None
    if title_el is not None:
        lit(paper_iri, DCTERMS + "title", _text(title_el))

    # conversion activity — if the JATS came from another format via an external
    # converter, disclose it as a version-pinned claim (the parse is informed by it).
    src_format = "jats"
    if conversion:
        conv_iri = f"{paper_iri}/activity/convert/run-{run}"
        iri(conv_iri, RDF.type, LIT + "DocumentConversionActivity")
        iri(conv_iri, RDF.type, PROV + "Activity")
        lit(conv_iri, LIT + "converter", conversion.get("converter", ""))
        lit(conv_iri, LIT + "sourceFormat", conversion.get("sourceFormat", ""))
        iri(parse_iri, PROV + "wasInformedBy", conv_iri)
        src_format = f"{conversion.get('sourceFormat', '?')}-via-jats"

    # parse activity — a dated claim; endedAtTime from the doc's pub-date if present
    iri(parse_iri, RDF.type, LIT + "DocumentParsingActivity")
    iri(parse_iri, RDF.type, PROV + "Activity")
    iri(parse_iri, PROV + "used", paper_iri)
    lit(parse_iri, LIT + "sourceFormat", src_format)
    lit(parse_iri, LIT + "parser", parser_id)
    pub = meta.find(".//pub-date") if meta is not None else None
    if pub is not None:
        y = (pub.findtext("year") or "").strip()
        mo = (pub.findtext("month") or "1").strip()
        d = (pub.findtext("day") or "1").strip()
        if y.isdigit():
            stamp = f"{int(y):04d}-{int(mo):02d}-{int(d):02d}T00:00:00Z"
            lit(parse_iri, PROV + "endedAtTime", stamp, XSD + "dateTime")

    context_parts: list[str] = []

    def emit_figure(fig) -> None:
        fid = fig.get("id")
        if not fid:
            return
        firi = f"{paper_iri}/fig/{fid}"
        iri(firi, RDF.type, DOCO + "Figure")
        label = fig.find("label")
        if label is not None:
            lit(firi, RDFS + "label", _text(label))
        cap = fig.find("caption")
        if cap is not None:
            ctext = _text(cap)
            if ctext:
                ciri = f"{firi}/caption"
                iri(firi, PO + "contains", ciri)
                iri(ciri, RDF.type, DOCO + "Caption")
                lit(ciri, NIF + "isString", ctext)

    def emit_paragraphs(parent_iri: str, texts: list[str]) -> None:
        for k, ptext in enumerate(texts):
            piri = f"{parent_iri}/para/{k}"
            iri(parent_iri, PO + "contains", piri)
            iri(piri, RDF.type, DOCO + "Paragraph")
            lit(piri, NIF + "isString", ptext)
            para_start = sum(len(x) for x in context_parts)
            context_parts.append(ptext + "\n")
            for j, (a, b) in enumerate(sentence_spans(ptext)):
                seniri = f"{piri}/sent/{j}"
                iri(piri, PO + "contains", seniri)
                iri(seniri, RDF.type, DOCO + "Sentence")
                lit(seniri, NIF + "anchorOf", ptext[a:b])
                iri(seniri, NIF + "referenceContext", context_iri)
                lit(seniri, NIF + "beginIndex", str(para_start + a), XSD + "nonNegativeInteger")
                lit(seniri, NIF + "endIndex", str(para_start + b), XSD + "nonNegativeInteger")
                iri(seniri, PROV + "wasQuotedFrom", paper_iri)
                iri(seniri, PROV + "wasGeneratedBy", parse_iri)

    def emit_section(sec, *, top: bool) -> None:
        sid = sec.get("id")
        if not sid:
            return
        siri = f"{paper_iri}/sec/{sid}"
        iri(siri, RDF.type, DOCO + "Section")
        tnode = sec.find("title")
        title = _text(tnode) if tnode is not None else ""
        lit(siri, DCTERMS + "title", title)
        lit(siri, LIT + "structuralPath", structural_slug(title))
        if top:
            low = title.lower()
            for kw, role in _DEO_ROLES:
                if kw in low:
                    iri(siri, RDF.type, DEO + role)
        for sub in sec.findall("sec"):
            if sub.get("id"):
                iri(siri, PO + "contains", f"{paper_iri}/sec/{sub.get('id')}")
        for fig in sec.findall("fig"):
            if fig.get("id"):
                iri(siri, PO + "contains", f"{paper_iri}/fig/{fig.get('id')}")
                emit_figure(fig)
        emit_paragraphs(siri, _block_paragraphs(sec))
        for sub in sec.findall("sec"):
            emit_section(sub, top=False)

    top_secs = body.findall("sec")
    for sec in top_secs:
        if sec.get("id"):
            iri(paper_iri, PO + "contains", f"{paper_iri}/sec/{sec.get('id')}")
    for sec in top_secs:
        emit_section(sec, top=True)

    # Heading-less documents (notes, minutes, contracts often start flat) keep their
    # prose as <p>/<list> directly under <body>. Capture it under one body-level
    # section so it stays paragraph/sentence-addressable and searchable.
    body_texts = _block_paragraphs(body)
    if body_texts:
        biri = f"{paper_iri}/sec/_body"
        iri(paper_iri, PO + "contains", biri)
        iri(biri, RDF.type, DOCO + "Section")
        lit(biri, DCTERMS + "title", "")
        lit(biri, LIT + "structuralPath", "")
        emit_paragraphs(biri, body_texts)

    iri(context_iri, RDF.type, NIF + "Context")
    lit(context_iri, NIF + "isString", "".join(context_parts))
    return g


def document_to_nt_file(
    xml_text: str, *, paper_iri: str, work_dir: str, conversion: dict[str, str] | None = None
) -> object:
    """Structure ``xml_text`` and write the graph as N-Triples under ``work_dir``.

    Parallel to :func:`asterism.substrate.materialize_to_nt_file` (the RML path): a
    file the API ingest streams into the staged graph in row-chunked POSTs
    (memory-bounded). ``conversion`` is forwarded to :func:`structure_jats` so a
    converted (e.g. Word) source records its ``lit:DocumentConversionActivity``.
    Returns the ``pathlib.Path`` to the ``.nt`` file.
    """
    from pathlib import Path

    g = structure_jats(xml_text, paper_iri=paper_iri, conversion=conversion)
    path = Path(work_dir) / "document.nt"
    g.serialize(destination=str(path), format="nt", encoding="utf-8")
    return path


# Cap on an uploaded Word file we will hand to pandoc (defence-in-depth alongside
# the API's own upload byte cap): a .docx is a zip, so this is generous for prose.
_MAX_DOCX_BYTES = 64 * 1024 * 1024


class ConversionError(RuntimeError):
    """A Word→JATS conversion could not be performed (pandoc missing, timeout, or
    a malformed / oversized document). The API surfaces this as a clear 4xx."""


def pandoc_version() -> str | None:
    """``"pandoc/<version>"`` if the pandoc binary is available, else ``None``."""
    import shutil
    import subprocess

    if shutil.which("pandoc") is None:
        return None
    try:
        out = subprocess.run(
            ["pandoc", "--version"], capture_output=True, text=True, timeout=10, check=True
        )
    except (OSError, subprocess.SubprocessError):
        return None
    first = (out.stdout.splitlines() or [""])[0].strip()  # "pandoc 3.1.11.1"
    return first.replace(" ", "/", 1) if first else "pandoc"


def convert_docx_to_jats(docx_bytes: bytes, *, timeout: float = 30.0) -> tuple[str, str]:
    """Convert a Word ``.docx`` to JATS XML with **pandoc**, returning ``(jats, converter)``.

    pandoc is an OPTIONAL external tool: a ``.docx`` is already structured XML
    (OOXML), so pandoc recovers headings/paragraphs faithfully and emits JATS
    (``<sec id><title><p>``) — the exact shape :func:`structure_jats` ingests. This
    runs pandoc as a hardened subprocess (no shell, fixed argv, timeout, bounded
    input) and reads ``stdin``/``stdout`` (no temp files, no host paths). The
    resulting JATS is still parsed by the defusedxml structurer downstream.

    Raises :class:`ConversionError` if pandoc is unavailable, the input is oversized,
    or the conversion fails/times out.
    """
    import subprocess

    version = pandoc_version()
    if version is None:
        raise ConversionError(
            "Word (.docx) ingestion requires the 'pandoc' tool, which is not installed. "
            "Convert the document to JATS XML first, or install pandoc."
        )
    if len(docx_bytes) > _MAX_DOCX_BYTES:
        raise ConversionError("Word document is too large to convert")
    try:
        # -s (standalone) wraps the content in <article><body> — the shape the
        # structurer ingests (a bare `-t jats` emits a sectionless fragment).
        proc = subprocess.run(
            ["pandoc", "-f", "docx", "-t", "jats", "-s", "-o", "-"],
            input=docx_bytes,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ConversionError("Word→JATS conversion timed out") from exc
    except OSError as exc:
        raise ConversionError(f"could not run pandoc: {exc}") from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace")[:300].strip()
        raise ConversionError(f"pandoc could not convert the document: {detail}")
    jats = proc.stdout.decode("utf-8", "replace")
    if "<body" not in jats:
        raise ConversionError("the converted document has no body content")
    return jats, version


# --- PDF (Docling) conversion ----------------------------------------------------
# Born-digital PDF needs layout-aware ML (Docling) to recover its structure. That ML
# runs in a SEPARATE sidecar service (ADR pdf-docling-conversion.md), keeping the api
# image ML-free. The sidecar returns the RAW DoclingDocument dict; the deterministic,
# vetted dict→JATS adapter below runs HERE (in-repo, unit-tested, no torch). So a PDF
# lands in the SAME JATS shape structure_jats already ingests — figures/sections get the
# ``id``s a PDF lacks, synthesised deterministically.

# Cap on a PDF handed to the sidecar (defence-in-depth alongside the sidecar's own cap
# and the API upload cap). A PDF is far denser than prose.
_MAX_PDF_BYTES = 64 * 1024 * 1024

# Strip a leading "Figure 3." / "Table 1:" into a JATS <label> for cleaner citations.
_FIG_LABEL_RE = re.compile(r"^(Fig(?:ure)?\.?\s*\d+|Table\s*\d+|Scheme\s*\d+)[.:]?\s*", re.I)


def _docling_ref(node: object) -> str | None:
    """A DoclingDocument cross-reference string (``"#/texts/0"``) from a ref dict."""
    if isinstance(node, dict):
        r = node.get("$ref") or node.get("cref")
        return r if isinstance(r, str) else None
    return None


def docling_dict_to_jats(doc: dict) -> str:
    """Convert a Docling ``DoclingDocument.export_to_dict()`` into JATS XML (deterministic).

    The vetted, in-repo half of the PDF path: the sidecar runs the ML and returns this
    raw dict; this pure, stdlib, deterministic function turns it into the JATS shape
    :func:`structure_jats` already ingests (``<article><body><sec id><title><p>…``),
    synthesising the section / figure ``id``s a PDF lacks. No torch, no network — it is
    unit-tested with a committed fixture.

    Reading order comes from ``body.children`` (followed recursively); section nesting
    from ``section_header`` levels (markdown-style); lists from list ``groups``; figures
    from ``pictures`` that carry a caption. Table grids are deferred — a table's caption
    is kept as prose so it stays searchable / citable (ADR pdf-docling-conversion.md §7).
    """
    import xml.etree.ElementTree as ET

    index: dict[str, dict] = {}
    for key in ("texts", "groups", "pictures", "tables"):
        for it in doc.get(key) or []:
            ref = it.get("self_ref")
            if isinstance(ref, str):
                index[ref] = it

    # Caption text items are emitted with their figure/table — skip them as body prose.
    caption_refs: set[str] = set()
    for key in ("pictures", "tables"):
        for it in doc.get(key) or []:
            for cap in it.get("captions") or []:
                r = _docling_ref(cap)
                if r:
                    caption_refs.add(r)

    def item_text(it: dict) -> str:
        return trim_collapse(it.get("text") or it.get("orig") or "")

    def caption_text(it: dict) -> str:
        out = []
        for cap in it.get("captions") or []:
            r = _docling_ref(cap)
            c = index.get(r) if r else None
            if c is not None:
                t = item_text(c)
                if t:
                    out.append(t)
        return trim_collapse(" ".join(out))

    # Flatten to a linear reading-order token stream; sections are rebuilt from levels.
    seq: list[tuple] = []
    seen: set[str] = set()

    def flatten(children: object) -> None:
        for ch in children or []:
            ref = _docling_ref(ch)
            if not ref or ref in seen:
                continue
            seen.add(ref)
            it = index.get(ref)
            if it is None:
                continue
            kind = ref.split("/")[1] if "/" in ref else ""
            if kind == "groups":
                if "list" in (it.get("label") or "").lower():
                    items = []
                    for li in it.get("children") or []:
                        lr = _docling_ref(li)
                        lit = index.get(lr) if lr else None
                        if lit is not None:
                            seen.add(lr)
                            t = item_text(lit)
                            if t:
                                items.append(t)
                    if items:
                        seq.append(("list", items))
                else:
                    flatten(it.get("children"))  # inline / other group: descend
            elif kind == "pictures":
                cap = caption_text(it)
                if cap:
                    seq.append(("figure", cap))
            elif kind == "tables":
                cap = caption_text(it)
                if cap:
                    seq.append(("table", cap))
            elif kind == "texts":
                if ref in caption_refs:
                    continue
                label = (it.get("label") or "").lower()
                text = item_text(it)
                if label == "title":
                    if text:
                        seq.append(("title", text))
                elif label == "section_header":
                    if text:
                        lvl = it.get("level")
                        seq.append(("header", lvl if isinstance(lvl, int) and lvl > 0 else 1, text))
                elif label in ("page_header", "page_footer", "footnote"):
                    pass  # furniture — not body prose
                elif label == "list_item":
                    if text:
                        seq.append(("list", [text]))
                elif text:  # text / paragraph / code / formula / ...
                    seq.append(("para", text))
                flatten(it.get("children"))  # hierarchical layouts: descend in order

    flatten((doc.get("body") or {}).get("children"))

    # Build the JATS tree (ElementTree handles XML escaping).
    article = ET.Element("article")
    ameta = ET.SubElement(ET.SubElement(article, "front"), "article-meta")
    body_el = ET.SubElement(article, "body")

    title_text = next((t[1] for t in seq if t[0] == "title"), "") or trim_collapse(
        doc.get("name") or ""
    )
    if title_text:
        ET.SubElement(ET.SubElement(ameta, "title-group"), "article-title").text = title_text

    counters = {"sec": 0, "fig": 0}
    stack: list[tuple[int, ET.Element]] = []

    def container() -> ET.Element:
        return stack[-1][1] if stack else body_el

    def paragraph(parent: ET.Element, text: str) -> None:
        ET.SubElement(parent, "p").text = text

    for tok in seq:
        head = tok[0]
        if head == "title":
            continue
        if head == "header":
            _, level, text = tok
            while stack and stack[-1][0] >= level:
                stack.pop()
            parent = stack[-1][1] if stack else body_el
            counters["sec"] += 1
            sec = ET.SubElement(parent, "sec", {"id": f"sec-{counters['sec']}"})
            ET.SubElement(sec, "title").text = text
            stack.append((level, sec))
        elif head == "para":
            paragraph(container(), tok[1])
        elif head == "list":
            lst = ET.SubElement(container(), "list")
            for item in tok[1]:
                paragraph(ET.SubElement(lst, "list-item"), item)
        elif head == "figure":
            counters["fig"] += 1
            fig = ET.SubElement(container(), "fig", {"id": f"fig-{counters['fig']}"})
            cap = tok[1]
            m = _FIG_LABEL_RE.match(cap)
            if m:
                ET.SubElement(fig, "label").text = m.group(1)
                cap = cap[m.end() :].strip()
            paragraph(ET.SubElement(fig, "caption"), cap or tok[1])
        elif head == "table":
            # v1: keep the table caption as prose so it stays searchable / citable.
            paragraph(container(), tok[1])

    return ET.tostring(article, encoding="unicode")


def convert_pdf_to_jats(
    pdf_bytes: bytes, *, sidecar_url: str | None, timeout: float = 600.0
) -> tuple[str, str]:
    """Convert a born-digital PDF to JATS via the Docling sidecar, returning ``(jats, converter)``.

    The sidecar (``ASTERISM_DOCLING_URL``) runs the ML and returns the raw
    ``DoclingDocument`` dict; :func:`docling_dict_to_jats` (deterministic, in-repo) turns
    it into JATS here in the trusted runtime. Raises :class:`ConversionError` if the
    sidecar is not configured / unreachable, the input is oversized, or conversion fails —
    the API surfaces it as a clear 4xx (graceful degrade, like absent pandoc).
    """
    if not sidecar_url:
        raise ConversionError(
            "PDF ingestion requires the Docling sidecar, which is not configured. "
            "Set ASTERISM_DOCLING_URL to its URL, or convert the PDF to JATS/Word first."
        )
    if len(pdf_bytes) > _MAX_PDF_BYTES:
        raise ConversionError("PDF is too large to convert")
    import httpx

    url = sidecar_url.rstrip("/") + "/convert"
    try:
        resp = httpx.post(
            url, content=pdf_bytes, headers={"content-type": "application/pdf"}, timeout=timeout
        )
    except httpx.HTTPError as exc:
        raise ConversionError(
            f"could not reach the Docling sidecar at {sidecar_url}: {exc}"
        ) from exc
    if resp.status_code != 200:
        detail = resp.text[:300].strip()
        raise ConversionError(
            f"the Docling sidecar could not convert the PDF ({resp.status_code}): {detail}"
        )
    try:
        payload = resp.json()
    except ValueError as exc:
        raise ConversionError("the Docling sidecar returned a non-JSON response") from exc
    jats = docling_dict_to_jats(payload.get("docling_doc") or {})
    converter = payload.get("converter") or "docling"
    if "<body" not in jats:
        raise ConversionError("the converted PDF has no body content")
    return jats, converter


def derive_doc_id(xml_text: str, *, fallback: str) -> str:
    """A stable, IRI-safe document id from the JATS (pmcid > doi-slug > fallback slug)."""
    try:
        root = _safe_parse(xml_text)
    except Exception:
        return structural_slug(fallback) or "doc"
    meta = root.find(".//article-meta")
    pmcid = _article_id(meta, "pmcid")
    if pmcid:
        return pmcid
    doi = _article_id(meta, "doi")
    if doi:
        return doi.replace("/", "_")
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", fallback).strip("-")
    return slug or "doc"
