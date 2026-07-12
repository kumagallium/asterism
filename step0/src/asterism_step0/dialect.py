"""Source dialect — deterministic sniffing of legacy instrument-file dialects.

Design-time half of the source-dialect model (ADR
``docs/architecture/source-dialect.md``): real instrument exports are rarely
clean UTF-8 comma CSV — a CP932 tab export with a sample-name preamble line, an
ICDD reference card whose d-I table is whitespace-separated. ``detect_dialect``
sniffs the dialect ONCE at design time with a fixed, ordered, auditable
procedure (a pinned strict-decode attempt list, never a statistical detector);
the result is pinned into the Mapping IR ``dialects:`` section
(:func:`apply_detected_dialects`), compiled to ``ast:`` annotations on the RML
logical source, and normalized deterministically at ingest. The runtime twin
(``asterism.dialect``) implements the same field names and semantics; the two
sides communicate via the RML artifact, not Python imports (same boundary as
the IR compiler).

:func:`is_default` gates ALL downstream emission: a default dialect emits
nothing anywhere, keeping current behavior byte-identical.

Stdlib only (like the rest of the inspection prelude); PyYAML is lazy-imported
only for the YAML-text form of :func:`apply_detected_dialects` (same pattern as
``mapping_ir``).
"""

from __future__ import annotations

import codecs
import csv
import io
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "LEGACY_SUFFIXES",
    "TABULAR_SUFFIXES",
    "WHITESPACE",
    "SourceDialect",
    "apply_detected_dialects",
    "describe_dialect",
    "detect_dialect",
    "dialect_ir_fields",
    "is_default",
    "iter_rows",
]

# The sentinel delimiter: split on runs of spaces/tabs (Excel's "treat
# consecutive delimiters as one") — covers fixed-width-ish instrument tables.
WHITESPACE = "whitespace"

# The instrument-export extensions beyond the classic pair. Morph-KGC cannot
# resolve their source type at all, so ingest normalizes them EVEN with a
# default dialect (extension-based normalization, ADR) — and inspection reads
# them through the same default read rules so both sides see the same rows.
LEGACY_SUFFIXES = frozenset({".txt", ".dat", ".asc"})

# Tabular source suffixes the entrance accepts (ADR "entrance widening"):
# the classic pair plus the extensions instrument exports actually use.
TABULAR_SUFFIXES = frozenset({".csv", ".tsv"}) | LEGACY_SUFFIXES

# Detection sample bounds: enough to see any realistic preamble + table run.
_SAMPLE_BYTES = 1 << 20
_SAMPLE_LINES = 200

# The pinned strict-decode attempt list (ADR: deterministic and auditable —
# never chardet). latin-1 is the terminal fallback; it always succeeds.
_ENCODING_ATTEMPTS = ("utf-8-sig", "cp932")

# Delimiter candidates in priority order: comma > tab > semicolon > pipe >
# whitespace (ties on run length and column count break toward the earlier one).
_DELIMITER_CANDIDATES = (",", "\t", ";", "|", WHITESPACE)
_MIN_RUN = 5
_MIN_COLUMNS = 2

_WS_RUN = re.compile(r"[ \t]+")


@dataclass(frozen=True)
class SourceDialect:
    """How to read one tabular source file (the pinned contract, ADR
    source-dialect.md). The header row is the first row AFTER ``skip_rows``."""

    encoding: str = "utf-8-sig"  # any Python codec name; "cp932" for Shift-JIS
    delimiter: str = ","  # single char, or the sentinel "whitespace"
    collapse: bool = False  # treat consecutive delimiters as one
    skip_rows: int = 0  # lines before the header row (preamble)


_DEFAULT_DIALECT = SourceDialect()


def is_default(dialect: SourceDialect) -> bool:
    """True when every field is the default — the gate for ALL downstream
    emission (IR section / RML annotations / ingest normalization)."""
    return dialect == _DEFAULT_DIALECT


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def _detect_encoding(sample: bytes) -> str:
    """First full strict decode of the sample wins (UTF-16 by BOM only)."""
    if sample.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"
    for encoding in _ENCODING_ATTEMPTS:
        try:
            sample.decode(encoding)
        except UnicodeDecodeError:
            continue
        return encoding
    return "latin-1"


def _split_line(line: str, delimiter: str, collapse: bool) -> list[str]:
    """Tokenize ONE physical line under ``delimiter`` (detection-side counting;
    reading goes through :func:`iter_rows`). Single-char delimiters are
    csv-tokenized (quote-aware) so a quoted delimiter never splits a cell."""
    if delimiter == WHITESPACE:
        stripped = line.strip()
        return [t for t in _WS_RUN.split(stripped) if t] if stripped else []
    if not line:
        return []
    try:
        row = next(csv.reader([line], delimiter=delimiter), [])
    except csv.Error:
        row = line.split(delimiter)
    if collapse:
        return [t for t in row if t != ""]
    return row


def _logical_records(text: str, delimiter: str) -> list[tuple[int, int]]:
    """``(start physical line index, token count)`` per non-blank LOGICAL record.

    Single-char candidates count csv logical records, not physical lines: a
    quoted cell containing newlines is ONE record whose start line is what
    ``skip_rows`` (a physical count) must point at, and blank records (empty
    lines, all-empty rows) never enter the count column — so neither an
    interior blank line nor a multi-line cell can shift the run.
    """
    records: list[tuple[int, int]] = []
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    prev = 0
    try:
        for row in reader:
            start = prev
            prev = reader.line_num
            if not row or all(not t.strip() for t in row):
                continue
            records.append((start, len(row)))
    except csv.Error:
        # Unbalanced quoting etc. — fall back to a naive physical-line split.
        return [
            (i, len(line.split(delimiter)))
            for i, line in enumerate(text.splitlines())
            if line.strip()
        ]
    return records


def _trailing_run(records: list[tuple[int, int]]) -> tuple[int, int, int] | None:
    """``(run_length, columns, start line)`` of the trailing constant-count run,
    or None when the run is too short (< 5 records) or too narrow (< 2 cols)."""
    if not records:
        return None
    columns = records[-1][1]
    if columns < _MIN_COLUMNS:
        return None
    run = 1
    while run < len(records) and records[-run - 1][1] == columns:
        run += 1
    if run < _MIN_RUN:
        return None
    return run, columns, records[-run][0]


def detect_dialect(path: Path | str) -> SourceDialect:
    """Deterministically sniff a tabular file's dialect (ADR source-dialect.md).

    Over a bounded sample (first 1 MiB / first 200 lines):

    1. **Encoding** — UTF-16 BOM check, then the pinned strict attempt list
       ``utf-8-sig`` → ``cp932``, then ``latin-1``.
    2. **Well-formed short circuit** — when the comma (default) read yields
       logical records with a constant column count ≥ 2 from the FIRST record
       on, the file already reads correctly under the default rules: only the
       encoding can pin, no other candidate is consulted.
    3. **Single-char candidates** (``, \\t ; |``) — per candidate, the trailing
       run of LOGICAL records (:func:`_logical_records`) with a constant token
       count ≥ 2; valid when the run is ≥ 5 records. Pick by
       ``(run_length, columns, candidate priority)``.
    4. **Whitespace subordination** — the whitespace candidate counts non-blank
       physical lines (no quote concept), and only ever wins:
       (a) over a valid single-char candidate when its run is strictly longer
       AND its column count differs (a tab table splits into the SAME columns
       under ``[ \\t]+``, so an equal count is a structural artifact of the
       single-char table, not evidence); (b) with no single-char candidate,
       when its run starts at the first record (a preamble-free whitespace
       table) or the comma read is not constant-width from the start (so a
       clean constant-width CSV — even 1-column — is never hijacked).
    5. ``skip_rows`` = the adopted run's first record's physical line index.

    No valid candidate ⇒ the default dialect (current behavior, nothing
    emitted anywhere).
    """
    with Path(path).open("rb") as fh:
        sample = fh.read(_SAMPLE_BYTES)
    truncated = len(sample) == _SAMPLE_BYTES
    probe = sample
    if truncated:
        # The cut may have split a multibyte char; probe whole lines only.
        cut = probe.rfind(b"\n")
        if cut != -1:
            probe = probe[: cut + 1]
    encoding = _detect_encoding(probe)

    lines = sample.decode(encoding, errors="replace").splitlines()
    if truncated and len(lines) > 1:
        lines = lines[:-1]
    lines = lines[:_SAMPLE_LINES]
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return SourceDialect()
    text = "\n".join(lines)

    comma_records = _logical_records(text, ",")
    comma_constant = bool(comma_records) and len({c for _, c in comma_records}) == 1
    if comma_constant and comma_records[0][1] >= _MIN_COLUMNS:
        # Already a well-formed CSV under the default read rules.
        return SourceDialect(encoding=encoding)

    best: tuple[tuple[int, int, int], str, int] | None = None
    for rank, delimiter in enumerate(_DELIMITER_CANDIDATES):
        if delimiter == WHITESPACE:
            continue
        records = comma_records if delimiter == "," else _logical_records(text, delimiter)
        found = _trailing_run(records)
        if found is None:
            continue
        run, columns, start = found
        key = (run, columns, -rank)
        if best is None or key > best[0]:
            best = (key, delimiter, start)

    ws_records = [
        (i, len(_split_line(line, WHITESPACE, collapse=False)))
        for i, line in enumerate(lines)
        if line.strip()
    ]
    ws_found = _trailing_run(ws_records)

    if best is not None:
        (run, columns, _), delimiter, start = best
        if ws_found is not None:
            ws_run, ws_columns, ws_start = ws_found
            if ws_run > run and ws_columns != columns:
                return SourceDialect(
                    encoding=encoding, delimiter=WHITESPACE, skip_rows=ws_start
                )
        return SourceDialect(encoding=encoding, delimiter=delimiter, skip_rows=start)
    if ws_found is not None:
        ws_run, _ws_columns, ws_start = ws_found
        preamble_free = ws_start == ws_records[0][0]
        if preamble_free or not comma_constant:
            return SourceDialect(encoding=encoding, delimiter=WHITESPACE, skip_rows=ws_start)
    return SourceDialect()


# ---------------------------------------------------------------------------
# Reading through a dialect
# ---------------------------------------------------------------------------


def iter_rows(path: Path | str, dialect: SourceDialect) -> Iterator[list[str]]:
    """Yield dialect-applied token rows (the first yielded row is the header).

    One tokenizer rule, shared verbatim with the runtime twin
    (``asterism.dialect.dialect_rows``): ``skip_rows`` counts PHYSICAL lines;
    after that a single-char delimiter reads csv LOGICAL records straight off
    the file handle (quoted newlines stay inside their cell), every cell is
    stripped, and blank records (all cells empty) are dropped. ``whitespace``
    splits non-blank physical lines on ``[ \\t]+`` (collapse implied).
    """
    with Path(path).open(encoding=dialect.encoding, newline="") as fh:
        for _ in range(dialect.skip_rows):
            if not fh.readline():
                return
        if dialect.delimiter == WHITESPACE:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                yield [t for t in _WS_RUN.split(line) if t]
        else:
            for row in csv.reader(fh, delimiter=dialect.delimiter):
                tokens = [t.strip() for t in row]
                if not any(tokens):
                    continue
                yield [t for t in tokens if t] if dialect.collapse else tokens


_DELIMITER_LABELS = {
    ",": "comma",
    "\t": "tab",
    ";": "semicolon",
    "|": "pipe",
    " ": "space",
    WHITESPACE: WHITESPACE,
}


def describe_dialect(dialect: SourceDialect) -> str:
    """A short human-readable summary of the non-default fields (for the
    inspection Markdown), e.g. ``encoding=cp932, delimiter=tab, skip_rows=1``."""
    parts: list[str] = []
    if dialect.encoding != "utf-8-sig":
        parts.append(f"encoding={dialect.encoding}")
    if dialect.delimiter != ",":
        label = _DELIMITER_LABELS.get(dialect.delimiter, repr(dialect.delimiter))
        parts.append(f"delimiter={label}")
    if dialect.collapse:
        parts.append("collapse=true")
    if dialect.skip_rows:
        parts.append(f"skip_rows={dialect.skip_rows}")
    return ", ".join(parts) or "default"


# ---------------------------------------------------------------------------
# Overlay onto the Mapping IR
# ---------------------------------------------------------------------------


def dialect_ir_fields(dialect: SourceDialect, *, full: bool = False) -> dict[str, Any]:
    """The dialect's fields in the IR ``dialects:`` entry shape.

    ``full=False`` (default) emits only NON-default fields — the detection path, kept
    minimal so a clean design never churns and the RML/normalize contract stays
    byte-identical. ``full=True`` emits ALL four fields (defaults included): a
    human-override entry is the source's complete intended dialect, so an explicit
    default (e.g. ``skip_rows`` corrected 1→0) must be authoritative and survive the
    materialize re-pin. Without the explicit default, "set to default" and "unset" are
    indistinguishable and re-detection silently refills the field (FIX2). The RML
    compiler still emits only non-default annotations, so the extra IR fields never
    change the compiled artifact."""
    if full:
        return {
            "encoding": dialect.encoding,
            "delimiter": dialect.delimiter,
            "collapse": dialect.collapse,
            "skip_rows": dialect.skip_rows,
        }
    out: dict[str, Any] = {}
    if dialect.encoding != "utf-8-sig":
        out["encoding"] = dialect.encoding
    if dialect.delimiter != ",":
        out["delimiter"] = dialect.delimiter
    if dialect.collapse:
        out["collapse"] = True
    if dialect.skip_rows:
        out["skip_rows"] = dialect.skip_rows
    return out


def _declared_sources(ir_dict: Mapping[str, Any]) -> set[str]:
    maps = ir_dict.get("maps")
    if not isinstance(maps, Sequence) or isinstance(maps, str):
        return set()
    return {
        m["source"]
        for m in maps
        if isinstance(m, Mapping) and isinstance(m.get("source"), str)
    }


def _overlay(
    ir_dict: Mapping[str, Any],
    detected: Mapping[str, SourceDialect],
    full_fields: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    sources = _declared_sources(ir_dict)
    existing = ir_dict.get("dialects")
    merged: dict[str, Any] = {}
    if isinstance(existing, Mapping):
        merged = {str(k): (dict(v) if isinstance(v, Mapping) else v) for k, v in existing.items()}
    for name, dialect in detected.items():
        if name not in sources or is_default(dialect):
            continue
        prior = merged.get(name)
        if prior is not None and not isinstance(prior, Mapping):
            continue  # malformed explicit entry — leave it for lint to flag
        # A human-override source (name in full_fields) emits ALL four fields so an
        # explicit default is authoritative and survives a later re-pin (FIX2);
        # detection-only sources stay minimal (byte-equivalence).
        entry = dialect_ir_fields(dialect, full=name in full_fields)
        if isinstance(prior, Mapping):
            entry.update(prior)  # explicit IR values win (the human gate)
        merged[name] = entry
    out = dict(ir_dict)
    if merged:
        out["dialects"] = merged
    return out


def apply_detected_dialects(
    ir: Mapping[str, Any] | str,
    detected: Mapping[str, SourceDialect],
    full_fields: frozenset[str] | set[str] | None = None,
) -> dict[str, Any] | str:
    """Overlay detected dialects onto a Mapping IR (dict or YAML text).

    The deterministic design-pipeline step (the LLM never authors
    ``dialects:``): every non-default detected dialect of a file some map
    declares as its source is written into the ``dialects:`` section, field by
    field, with explicit IR values WINNING over detected ones so the human
    gate can override. Default dialects and files no map reads are skipped.

    ``full_fields`` names the sources whose entry must carry ALL four fields
    (defaults included) rather than only the non-default ones — the human-override
    set (ADR source-dialect.md, the wizard's "read settings"). An override entry is
    the source's complete intended dialect, so an explicit default (``skip_rows``
    corrected 1→0) is pinned verbatim and survives the materialize re-pin (FIX2).
    The default (empty set) is byte-identical to the pre-FIX behavior: detection-only
    sources keep their minimal entries.

    A dict input returns a new dict (the input is not mutated). A YAML-text
    input returns YAML text, byte-identical when there is nothing to add — a
    clean design is never re-serialized.
    """
    ff = frozenset(full_fields or ())
    if isinstance(ir, str):
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - environment failure
            raise ImportError(
                "PyYAML is required to overlay dialects onto a mapping-spec text."
            ) from exc

        try:
            doc = yaml.safe_load(ir)
        except yaml.YAMLError:
            return ir  # a spec too broken to parse flows on; lint reports it
        if not isinstance(doc, Mapping):
            return ir
        overlaid = _overlay(doc, detected, ff)
        if overlaid.get("dialects") == doc.get("dialects"):
            return ir
        return yaml.safe_dump(overlaid, sort_keys=False, allow_unicode=True)
    return _overlay(ir, detected, ff)
