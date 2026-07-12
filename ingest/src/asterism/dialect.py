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
SOURCE_PREAMBLE_PREDICATE: str = ASTERISM_NS + "sourcePreamble"

DIALECT_PREDICATES: tuple[str, ...] = (
    SOURCE_ENCODING_PREDICATE,
    SOURCE_DELIMITER_PREDICATE,
    SOURCE_COLLAPSE_PREDICATE,
    SOURCE_SKIP_ROWS_PREDICATE,
    SOURCE_PREAMBLE_PREDICATE,
)

# The closed set of preamble-handling modes (ADR source-dialect.md, "Header
# metadata"): "drop" keeps today's behavior (the skip_rows preamble is discarded);
# "keyvalue"/"lines" broadcast the parsed preamble metadata onto every body row.
PREAMBLE_MODES: frozenset[str] = frozenset({"drop", "keyvalue", "lines"})

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


# Preamble parsing (ADR source-dialect.md, "Header metadata"): a section heading
# (a run of 3+ leading hyphens) is skipped; a ``key: value`` line splits on its
# FIRST colon only (a second colon stays inside the value — lossless).
_PREAMBLE_SECTION = re.compile(r"^\s*-{3,}")
_PREAMBLE_KV = re.compile(r"^\s*([^:]+?)\s*:\s*(.*)$")


def read_preamble(lines: list[str], mode: str) -> list[tuple[str, str]]:
    """Parse the decoded preamble ``lines`` into ordered ``(name, value)`` pairs.

    Shared verbatim with the design-side twin (``asterism_step0.dialect``). The
    ``skip_rows`` physical lines that ``drop`` would discard are turned into
    metadata columns broadcast onto every body row.

    ``mode == "lines"`` — each non-blank line ``i`` becomes ``("preamble_{i+1}",
    stripped)`` (a bare sample-name line stays intact). ``mode == "keyvalue"`` —
    line by line, in a fixed deterministic priority: a section heading
    (``^\\s*-{3,}``) is skipped; a ``key: value`` line splits on its FIRST colon
    only (``strip``ed key/value, a second colon preserved inside the value, a
    multi-value cell kept whole for Tier-0 to split later); any other non-blank
    line WITHOUT a colon is a CONTINUATION appended (space-joined) to the previous
    key's value (so a colon-free wrapped line rejoins its field), or, with no
    previous key, a ``preamble_{i+1}`` fallback. A duplicate key is suffixed
    ``key_2``/``key_3`` (lossless, never overwritten). An unknown mode yields
    nothing (the caller's ``drop`` path handles the default).

    Determinism has a documented cost: a wrapped line that ITSELF contains a colon
    (e.g. the second physical line of an ICDD ``Comment`` note, ``for Al-filings:
    4.049``) is indistinguishable from a real new field, so it is parsed as its
    own ``key: value`` pair rather than rejoined — the text is never lost but lands
    in an extra, oddly-named column the designer simply does not map. See the ADR /
    report Limitations."""
    if mode == "lines":
        out: list[tuple[str, str]] = []
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped:
                out.append((f"preamble_{i + 1}", stripped))
        return out
    if mode != "keyvalue":
        return []
    pairs: list[list[str]] = []  # [key, value], value mutated by continuation lines
    key_counts: dict[str, int] = {}
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or _PREAMBLE_SECTION.match(line):
            continue
        m = _PREAMBLE_KV.match(line)
        if m:
            key = m.group(1).strip()
            value = m.group(2).strip()
            key_counts[key] = key_counts.get(key, 0) + 1
            if key_counts[key] > 1:
                key = f"{key}_{key_counts[key]}"
            pairs.append([key, value])
        elif pairs:
            pairs[-1][1] = f"{pairs[-1][1]} {stripped}".strip()
        else:
            pairs.append([f"preamble_{i + 1}", stripped])
    return [(k, v) for k, v in pairs]


def resolve_header(body_names: list[str], meta_names: list[str]) -> list[str]:
    """Resolve the broadcast META column names against the body header.

    Shared verbatim with the design-side twin. Each meta name is ``safe_column``ed
    (so a reserved ``subject``/``predicate`` key cannot silence a term map) and, if
    it would collide with a body column or an earlier meta column, suffixed
    ``_2``/``_3`` — the BODY columns are never renamed (their position/name stays
    exactly what ``drop`` produces). Returns the resolved meta names only; the full
    header is ``body_names + resolve_header(body_names, meta_names)``."""
    seen = {safe_column(b) for b in body_names}
    out: list[str] = []
    for name in meta_names:
        base = safe_column(name)
        candidate = base
        n = 2
        while candidate in seen:
            candidate = f"{base}_{n}"
            n += 1
        seen.add(candidate)
        out.append(candidate)
    return out


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
    preamble: str = "drop"  # "drop" | "keyvalue" | "lines" — how to treat the preamble


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

    ``dialect.preamble != "drop"`` (the opt-in header-metadata path) broadcasts
    the parsed preamble onto every body row; the default ``drop`` path below is
    byte-identical to today (the skip_rows preamble is read off and discarded).
    """
    if dialect.preamble != "drop":
        yield from _broadcast_rows(src, dialect)
        return
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


def _body_tokens(fh, dialect: SourceDialect) -> Iterator[list[str]]:
    """Tokenize the body (post-``skip_rows``) rows under ``dialect`` — the same
    single tokenizer rule the ``drop`` path uses inline. Used only by the
    broadcast path so the ``drop`` branch stays byte-identical to today."""
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


def _broadcast_rows(src: Path | str, dialect: SourceDialect) -> Iterator[list[str]]:
    """Broadcast the parsed preamble metadata onto every body row (ADR
    source-dialect.md, "Header metadata").

    Collects the ``skip_rows`` preamble lines, parses them (:func:`read_preamble`)
    into constant metadata columns appended AFTER the body columns (so body
    columns keep their exact ``drop`` positions), then yields ``body_header +
    resolve_header(...)`` as the header and each fitted body row plus the constant
    metadata values. Each body row is fitted to the body header width (short rows
    padded, over-split rows truncated) so the appended metadata always lands in
    the right columns and the output stays rectangular. Reached only when
    ``preamble != "drop"``."""
    with Path(src).open("r", encoding=dialect.encoding, errors="strict", newline="") as fh:
        preamble_lines: list[str] = []
        for _ in range(dialect.skip_rows):
            line = fh.readline()
            if not line:
                break
            preamble_lines.append(line)
        meta = read_preamble(preamble_lines, dialect.preamble)
        meta_names = [name for name, _ in meta]
        meta_values = [value for _, value in meta]
        body = _body_tokens(fh, dialect)
        header = next(body, None)
        if header is None:
            return
        width = len(header)
        yield list(header) + resolve_header(header, meta_names)
        for row in body:
            fitted = list(row[:width]) + [""] * (width - len(row))
            yield fitted + meta_values


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


def strip_preamble_and_header(raw: bytes, dialect: SourceDialect) -> bytes:
    """Drop an append batch's leading ``skip_rows + 1`` PHYSICAL lines — the
    preamble lines plus the single header row — returning the rest of the raw
    bytes unchanged (still in the source's native dialect).

    The runtime side of accumulating an append batch into a dialected source
    (ADR source-dialect.md, "Append"): the persisted copy grows in its NATIVE
    dialect — the FIRST batch keeps its preamble+header, every LATER batch is
    stripped here before its bytes are concatenated — so the accumulated file is
    "preamble once, header once, then every data row" and a snapshot re-ingest
    normalizes it exactly ONCE (no un-pin, no double normalization). Byte-level
    and decode-free: it counts ``\\n`` on the raw bytes (``skip_rows`` counts
    physical lines, the same rule the tokenizer uses), so CP932 / tab /
    whitespace content is never re-encoded and CRLF is preserved. A batch with
    fewer than ``skip_rows + 1`` physical lines (i.e. no data rows) yields
    ``b""``.
    """
    idx = 0
    for _ in range(dialect.skip_rows + 1):
        nl = raw.find(b"\n", idx)
        if nl == -1:
            return b""
        idx = nl + 1
    return raw[idx:]


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
        preamble = next(graph.objects(ls, uri(SOURCE_PREAMBLE_PREDICATE)), None)
        if preamble is not None:
            mode = str(preamble)
            if mode not in PREAMBLE_MODES:
                raise DialectAnnotationError(
                    f"{where}: ast:sourcePreamble must be one of "
                    f"{', '.join(sorted(PREAMBLE_MODES))} (got {mode!r})."
                )
            values["preamble"] = mode
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
