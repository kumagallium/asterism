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
        for k, p in enumerate(sec.findall("p")):
            ptext = _text(p)
            if not ptext:
                continue
            piri = f"{siri}/para/{k}"
            iri(siri, PO + "contains", piri)
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
        for sub in sec.findall("sec"):
            emit_section(sub, top=False)

    top_secs = body.findall("sec")
    for sec in top_secs:
        if sec.get("id"):
            iri(paper_iri, PO + "contains", f"{paper_iri}/sec/{sec.get('id')}")
    for sec in top_secs:
        emit_section(sec, top=True)

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
