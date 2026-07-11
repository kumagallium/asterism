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
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
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

# Tabular source suffixes the entrance accepts (ADR "entrance widening"):
# the classic pair plus the extensions instrument exports actually use.
TABULAR_SUFFIXES = frozenset({".csv", ".tsv", ".txt", ".dat", ".asc"})

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
    """Tokenize ONE physical line under ``delimiter``.

    Detection counts with the SAME tokenization :func:`iter_rows` reads with,
    so a pinned ``skip_rows`` always lines up with how the rows come back.
    Single-char delimiters are csv-tokenized (quote-aware) so a clean quoted
    CSV — e.g. JSON-array cells full of commas — counts its true fields and
    stays default.
    """
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


def detect_dialect(path: Path | str) -> SourceDialect:
    """Deterministically sniff a tabular file's dialect (ADR source-dialect.md).

    Over a bounded sample (first 1 MiB / first 200 lines):

    1. **Encoding** — UTF-16 BOM check, then the pinned strict attempt list
       ``utf-8-sig`` → ``cp932``, then ``latin-1``.
    2. **Delimiter + header offset** — per candidate, find the trailing run of
       lines with a constant token count ≥ 2; valid when the run is ≥ 5 rows.
       Pick by ``(run_length, columns, candidate priority)``; ``skip_rows`` is
       the run's start index (that row is the header).

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

    best: tuple[tuple[int, int, int], str, int] | None = None
    for rank, delimiter in enumerate(_DELIMITER_CANDIDATES):
        counts = [len(_split_line(line, delimiter, collapse=False)) for line in lines]
        if not counts:
            break
        columns = counts[-1]
        if columns < _MIN_COLUMNS:
            continue
        run = 1
        while run < len(counts) and counts[-run - 1] == columns:
            run += 1
        if run < _MIN_RUN:
            continue
        key = (run, columns, -rank)
        if best is None or key > best[0]:
            best = (key, delimiter, len(counts) - run)
    if best is None:
        return SourceDialect()
    _, delimiter, skip_rows = best
    return SourceDialect(encoding=encoding, delimiter=delimiter, skip_rows=skip_rows)


# ---------------------------------------------------------------------------
# Reading through a dialect
# ---------------------------------------------------------------------------


def iter_rows(path: Path | str, dialect: SourceDialect) -> Iterator[list[str]]:
    """Yield dialect-applied token rows: drop the first ``skip_rows`` physical
    lines, skip blank lines, tokenize each remaining line (the first yielded
    row is the header, then data). CRLF is absorbed by text-mode decoding."""
    with Path(path).open(encoding=dialect.encoding) as fh:
        for i, raw in enumerate(fh):
            if i < dialect.skip_rows:
                continue
            line = raw.rstrip("\r\n")
            if not line.strip():
                continue
            yield _split_line(line, dialect.delimiter, dialect.collapse)


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


def dialect_ir_fields(dialect: SourceDialect) -> dict[str, Any]:
    """The dialect's NON-default fields in the IR ``dialects:`` entry shape."""
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


def _overlay(ir_dict: Mapping[str, Any], detected: Mapping[str, SourceDialect]) -> dict[str, Any]:
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
        entry = dialect_ir_fields(dialect)
        if isinstance(prior, Mapping):
            entry.update(prior)  # explicit IR values win (the human gate)
        merged[name] = entry
    out = dict(ir_dict)
    if merged:
        out["dialects"] = merged
    return out


def apply_detected_dialects(
    ir: Mapping[str, Any] | str, detected: Mapping[str, SourceDialect]
) -> dict[str, Any] | str:
    """Overlay detected dialects onto a Mapping IR (dict or YAML text).

    The deterministic design-pipeline step (the LLM never authors
    ``dialects:``): every non-default detected dialect of a file some map
    declares as its source is written into the ``dialects:`` section, field by
    field, with explicit IR values WINNING over detected ones so the human
    gate can override. Default dialects and files no map reads are skipped.

    A dict input returns a new dict (the input is not mutated). A YAML-text
    input returns YAML text, byte-identical when there is nothing to add — a
    clean design is never re-serialized.
    """
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
        overlaid = _overlay(doc, detected)
        if overlaid.get("dialects") == doc.get("dialects"):
            return ir
        return yaml.safe_dump(overlaid, sort_keys=False, allow_unicode=True)
    return _overlay(ir, detected)
