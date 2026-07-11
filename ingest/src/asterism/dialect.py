"""Source dialect: runtime side of the legacy-instrument-file contract.

Real instrument exports are rarely clean UTF-8 comma CSV (a CP932 tab-separated
XRD export with a preamble line; an ICDD card whose d-I table is
whitespace-separated). The design side detects the dialect once and pins it as
``ast:`` annotations on ``rml:logicalSource``; this module reads those
annotations at ingest time and normalizes the source to UTF-8 comma CSV so
Morph-KGC sees exactly what it sees today. Detection never happens here —
normalization is a pure function of ``(bytes, dialect)``.

Deliberately implemented twice (``asterism_step0.dialect`` is the design-side
twin): the two sides communicate via the RML artifact, not Python imports —
same boundary as the IR compiler. Same field names, same semantics, both
stdlib-only at module load. Contract: ``docs/architecture/source-dialect.md``.
"""
from __future__ import annotations

import csv
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

# The asterism vocab namespace (same IRI as ``asterism.substrate.ASTERISM_NS``;
# duplicated so this module stays import-light and dependency-free).
ASTERISM_NS: str = "https://kumagallium.github.io/asterism/vocab#"

# The four dialect annotation predicates, carried on the ``rml:logicalSource``
# node next to ``rml:source``. Data-only — they describe how to *read* the
# source file and carry no execution semantics.
SOURCE_ENCODING_PREDICATE: str = ASTERISM_NS + "sourceEncoding"
SOURCE_DELIMITER_PREDICATE: str = ASTERISM_NS + "sourceDelimiter"
SOURCE_COLLAPSE_PREDICATE: str = ASTERISM_NS + "sourceCollapse"
SOURCE_SKIP_ROWS_PREDICATE: str = ASTERISM_NS + "sourceSkipRows"

DIALECT_PREDICATES: tuple[str, ...] = (
    SOURCE_ENCODING_PREDICATE,
    SOURCE_DELIMITER_PREDICATE,
    SOURCE_COLLAPSE_PREDICATE,
    SOURCE_SKIP_ROWS_PREDICATE,
)

# ``delimiter`` sentinel: split on runs of spaces/tabs (Excel's "treat
# consecutive delimiters as one") — covers fixed-width-ish instrument tables.
WHITESPACE: str = "whitespace"
_WHITESPACE_RUN = re.compile(r"[ \t]+")

# Legacy instrument-export suffixes (ADR "extension-based normalization"):
# Morph-KGC cannot resolve their source type at all, so the substrate
# normalizes them to a work-dir CSV even when no dialect is annotated.
LEGACY_SUFFIXES: frozenset[str] = frozenset({".txt", ".dat", ".asc"})

# rml:source lives at either the new RML namespace or the legacy mmlab one
# (mirrors asterism.rml_safety._SOURCE_PREDICATES).
_SOURCE_PREDICATES: tuple[str, ...] = (
    "http://w3id.org/rml/source",
    "http://semweb.mmlab.be/ns/rml#source",
)

# Morph-KGC reserves these DataFrame column names when building term maps — a
# source column named either silently yields 0 triples. Mirror of the canonical
# ``asterism.tabularize.RESERVED_COLUMNS`` / ``safe_col`` (duplicated so this
# module stays import-light; keep in sync).
_RESERVED_COLUMNS: frozenset[str] = frozenset({"subject", "predicate"})


def safe_column(name: str) -> str:
    """Rename a Morph-KGC-reserved column (``subject``/``predicate`` → suffixed
    ``_``); identity for every other name."""
    return f"{name}_" if name in _RESERVED_COLUMNS else name


class DialectAnnotationError(ValueError):
    """An ``ast:`` dialect annotation value is outside the pinned contract.

    User-authored RML reaches ``dialects_from_mapping`` unvetted (the raw-RML
    save path), so every annotation value is boundary-checked here; callers map
    this to a structured 422 (``RmlValidationError``), never a 500.
    """


@dataclass(frozen=True)
class SourceDialect:
    """How to read one legacy source file (pinned at design time).

    All-defaults means "today's behavior": a default dialect is never annotated,
    never normalized, and byte-identical current behavior applies.
    """

    encoding: str = "utf-8-sig"  # any Python codec name; "cp932" for Shift-JIS
    delimiter: str = ","  # single char, or the sentinel "whitespace"
    collapse: bool = False  # treat consecutive delimiters as one
    skip_rows: int = 0  # lines before the header row (preamble)


DEFAULT_DIALECT = SourceDialect()


def is_default(dialect: SourceDialect) -> bool:
    """True when every field is the default — the gate for all downstream
    emission/normalization (a default dialect must change nothing anywhere)."""
    return dialect == DEFAULT_DIALECT


def dialect_rows(src: Path | str, dialect: SourceDialect) -> Iterator[list[str]]:
    """Read ``src`` through ``dialect``, yielding rows of tokens (header row first).

    The single tokenizer rule, shared verbatim with the design-side twin
    (``asterism_step0.dialect.iter_rows``): decode with ``dialect.encoding``
    **strict** (a decode error is a real error, not something to paper over);
    ``skip_rows`` counts PHYSICAL lines; after that a single-char delimiter
    reads csv LOGICAL records straight off the file handle (quoted newlines
    stay inside their cell), every cell is stripped, and blank records (all
    cells empty) are dropped — ``collapse`` additionally drops empty cells.
    ``whitespace`` splits non-blank physical lines on runs of spaces/tabs
    (collapse implied). Lazy (streamed), so reading just the header row does
    not slurp a large export.
    """
    with Path(src).open("r", encoding=dialect.encoding, errors="strict", newline="") as fh:
        for _ in range(dialect.skip_rows):
            if not fh.readline():
                return
        if dialect.delimiter == WHITESPACE:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                yield [tok for tok in _WHITESPACE_RUN.split(line) if tok]
        else:
            for row in csv.reader(fh, delimiter=dialect.delimiter):
                tokens = [tok.strip() for tok in row]
                if not any(tokens):
                    continue
                yield [tok for tok in tokens if tok] if dialect.collapse else tokens


def normalize_source(src: Path | str, dialect: SourceDialect, dest: Path | str) -> Path:
    """Normalize ``src`` (read through ``dialect``) to a UTF-8 comma CSV at ``dest``.

    Pure function of ``(bytes, dialect)``; returns ``dest``. The output is what
    Morph-KGC's plain CSV reader expects, so the engine itself stays untouched.
    The header row gets Morph-KGC's reserved columns renamed
    (:func:`safe_column`) — the normalized copy bypasses the direct-CSV
    sanitizer, so the rename must happen here. Memory-bounded: rows stream from
    :func:`dialect_rows` into :func:`csv.writer`.
    """
    dest_path = Path(dest)
    rows = dialect_rows(src, dialect)
    with dest_path.open("w", encoding="utf-8", newline="") as out:
        writer = csv.writer(out)
        header = next(rows, None)
        if header is not None:
            writer.writerow([safe_column(c) for c in header])
            writer.writerows(rows)
    return dest_path


def _text_codec_exists(name: str) -> bool:
    # TEXT codecs only: codecs.lookup also resolves bytes<->bytes codecs
    # ('zip', 'base64', …) which would crash the text decode. The probe must be
    # non-empty (b"".decode skips the codec lookup entirely).
    try:
        b"\x00\x00\x00\x00".decode(name, errors="ignore")
    except (LookupError, TypeError):
        return False
    return True


def dialects_from_mapping(graph) -> dict[str, SourceDialect]:
    """Read the ``ast:`` dialect annotations from a parsed RML mapping.

    Returns ``{source basename: SourceDialect}`` for every logical source that
    carries at least one annotation (absent fields keep their defaults, matching
    the emit-only-non-default contract). ``graph`` is an ``rdflib.Graph``.

    Every annotation value is boundary-checked (the raw-RML save path reaches
    here unvetted); a value outside the pinned contract raises
    :class:`DialectAnnotationError` with the offending source in the message.
    """
    import rdflib

    uri = rdflib.URIRef
    annotated: set = set()
    for pred in DIALECT_PREDICATES:
        annotated |= set(graph.subjects(uri(pred), None))
    out: dict[str, SourceDialect] = {}
    for ls in annotated:
        names = [
            Path(str(src)).name
            for src_pred in _SOURCE_PREDICATES
            for src in graph.objects(ls, uri(src_pred))
        ]
        where = f"logical source of {names[0]!r}" if names else "a logical source"
        values: dict[str, object] = {}
        enc = next(graph.objects(ls, uri(SOURCE_ENCODING_PREDICATE)), None)
        if enc is not None:
            encoding = str(enc)
            if not _text_codec_exists(encoding):
                raise DialectAnnotationError(
                    f"{where}: ast:sourceEncoding {encoding!r} is not a known text codec."
                )
            values["encoding"] = encoding
        delim = next(graph.objects(ls, uri(SOURCE_DELIMITER_PREDICATE)), None)
        if delim is not None:
            delimiter = str(delim)
            if delimiter != WHITESPACE and len(delimiter) != 1:
                raise DialectAnnotationError(
                    f"{where}: ast:sourceDelimiter must be a single character or "
                    f"'{WHITESPACE}' (got {delimiter!r})."
                )
            values["delimiter"] = delimiter
        collapse = next(graph.objects(ls, uri(SOURCE_COLLAPSE_PREDICATE)), None)
        if collapse is not None:
            flag = str(collapse).lower()
            if flag not in ("true", "false", "0", "1"):
                raise DialectAnnotationError(
                    f"{where}: ast:sourceCollapse must be true or false "
                    f"(got {str(collapse)!r})."
                )
            values["collapse"] = flag in ("true", "1")
        skip = next(graph.objects(ls, uri(SOURCE_SKIP_ROWS_PREDICATE)), None)
        if skip is not None:
            try:
                skip_rows = int(str(skip))
            except ValueError:
                skip_rows = -1
            if skip_rows < 0:
                raise DialectAnnotationError(
                    f"{where}: ast:sourceSkipRows must be a non-negative integer "
                    f"(got {str(skip)!r})."
                )
            values["skip_rows"] = skip_rows
        for name in names:
            out[name] = SourceDialect(**values)  # type: ignore[arg-type]
    return out


def strip_dialect_annotations(graph) -> None:
    """Remove every ``ast:`` dialect annotation triple from ``graph`` in place.

    The substrate calls this after normalizing the annotated sources, so the
    mapping handed to Morph-KGC carries exactly what it carries today.
    """
    import rdflib

    for pred in DIALECT_PREDICATES:
        graph.remove((None, rdflib.URIRef(pred), None))
