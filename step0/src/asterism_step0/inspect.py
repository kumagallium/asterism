"""Source inspection for AI-assisted Step 0 (Phase 3).

This module is the deterministic prelude to ``propose_schema``: given one or
more structured-data files, it builds a summary the LLM can ground its schema
proposal on. It deliberately uses only the standard library so the package is
installable without pandas.

Two source kinds are supported, both producing the *same* inspection contract
(:class:`SourceInspection` + :func:`render_markdown`) so everything downstream
(propose / refine / materialize) is source-agnostic:

* **CSV** — columns are header names; cells are strings (the original path).
* **JSON** (#19) — a JSON array of records (or a single nested array). Each
  record is flattened to dot-path leaf fields, mirroring Morph-KGC's
  ``pandas.json_normalize`` semantics (nested objects → ``a.b`` columns, list
  leaves kept as JSON cells). The detected *iterator* (e.g. ``$[*]``) is carried
  on the inspection so ``propose`` can emit an RML ``rml:logicalSource`` with
  ``rml:referenceFormulation ql:JSONPath`` + ``rml:iterator`` and dot-path
  ``rml:reference`` selectors that resolve against Morph-KGC's JSON reader.

Key responsibilities (from ``docs/architecture/ai-assisted-step0-workflow.md``):

1. **Column structure**: inferred type / non-null rate / unique value count /
   3 sample values per column.
2. **JSON detection**: cells whose first non-whitespace char is ``[`` or ``{``
   are tagged as ``json-array`` / ``json-object`` and parsed best-effort.
3. **Foreign key candidates**: when multiple CSVs share a column name with
   overlapping value sets, we flag the pair.
4. **Uniqueness statistics** (★ Phase 1 §6 trap T1): for each ID candidate we
   compute global collision counts across the full CSV and across composite
   keys (``(SID, sample_id)``, ``(SID, figure_id, sample_id)``, …) so the
   AI can pick the smallest globally-unique key.

Output formatting in :func:`render_markdown` matches the layout suggested by
``ai-assisted-step0-prompts.md`` §1, so the same Markdown can be embedded as
the ``step1_inspection`` argument to the Step 3 schema-proposal prompt.

BOM handling (trap T2): every CSV is opened with ``encoding="utf-8-sig"``.

Source dialects (ADR ``source-dialect.md``): every tabular source is sniffed
with :func:`asterism_step0.dialect.detect_dialect` first; a non-default dialect
(CP932, tab/whitespace separation, preamble rows) is applied when reading and
reported both on :class:`SourceInspection` and in the Markdown, so legacy
instrument files can be thrown in as-is. A default dialect keeps the original
read path — and the rendered Markdown — byte-identical.
"""

from __future__ import annotations

import csv
import io
import itertools
import json
import re
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path

from asterism_step0.dialect import (
    LEGACY_SUFFIXES,
    SourceDialect,
    describe_dialect,
    detect_dialect,
    detect_preamble_form,
    is_default,
    iter_rows,
)

# ----------------------------------------------------------------------------
# Type inference primitives
# ----------------------------------------------------------------------------

# Anchored ISO-8601 date and datetime.
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$")
_INTEGER = re.compile(r"^-?\d+$")
_FLOAT = re.compile(r"^-?\d+(\.\d+)?([eE][+-]?\d+)?$")

# Order matters: most specific first. We pick the first type that *all*
# non-empty samples satisfy.
_TYPE_ORDER = ("xsd:integer", "xsd:double", "xsd:date", "xsd:dateTime", "xsd:string")

_JSON_ARRAY_OPEN = "["
_JSON_OBJECT_OPEN = "{"


def _infer_cell_type(value: str) -> str:
    """Return the most specific xsd type a single cell satisfies.

    The "json-*" types are *not* returned here — they are detected separately
    in :func:`_detect_json_kind` because a JSON array column would otherwise
    be inferred as ``xsd:string``.
    """
    v = value.strip()
    if not v:
        return "xsd:string"  # caller filters empties before voting
    if _INTEGER.fullmatch(v):
        return "xsd:integer"
    if _FLOAT.fullmatch(v):
        return "xsd:double"
    if _ISO_DATETIME.fullmatch(v):
        return "xsd:dateTime"
    if _ISO_DATE.fullmatch(v):
        # Also try `date.fromisoformat` to catch invalid month/day.
        try:
            date.fromisoformat(v)
            return "xsd:date"
        except ValueError:
            return "xsd:string"
    return "xsd:string"


def _detect_json_kind(samples: Sequence[str]) -> str | None:
    """If every non-empty sample looks like a JSON array / object, return its kind.

    Returns ``"json-array"`` / ``"json-object"`` or ``None`` if at least one
    non-empty cell does not begin with ``[`` / ``{`` (or parses cleanly).
    """
    nonempty = [s.strip() for s in samples if s.strip()]
    if not nonempty:
        return None
    array_count = 0
    object_count = 0
    for s in nonempty:
        if s[0] == _JSON_ARRAY_OPEN:
            try:
                parsed = json.loads(s)
            except json.JSONDecodeError:
                return None
            if not isinstance(parsed, list):
                return None
            array_count += 1
        elif s[0] == _JSON_OBJECT_OPEN:
            try:
                parsed = json.loads(s)
            except json.JSONDecodeError:
                return None
            if not isinstance(parsed, dict):
                return None
            object_count += 1
        else:
            return None
    if array_count == len(nonempty):
        return "json-array"
    if object_count == len(nonempty):
        return "json-object"
    return None  # mixed; let the LLM decide


def _aggregate_types(types: Iterable[str]) -> str:
    """Return the broadest type that every observed type fits into."""
    seen = set(types)
    if not seen:
        return "xsd:string"
    if seen == {"xsd:integer"}:
        return "xsd:integer"
    if seen <= {"xsd:integer", "xsd:double"}:
        return "xsd:double"
    if seen == {"xsd:date"}:
        return "xsd:date"
    if seen == {"xsd:dateTime"}:
        return "xsd:dateTime"
    if seen <= {"xsd:date", "xsd:dateTime"}:
        return "xsd:dateTime"  # tolerate mixed date/datetime
    return "xsd:string"


def _looks_like_json(value: str) -> bool:
    v = value.lstrip()
    return v.startswith(_JSON_ARRAY_OPEN) or v.startswith(_JSON_OBJECT_OPEN)


def _json_first_keys(samples: Sequence[str], max_keys: int = 12) -> list[str]:
    """For json-object columns, collect a sample of top-level keys."""
    keys: list[str] = []
    for s in samples:
        try:
            parsed = json.loads(s)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            for k in parsed:
                if k not in keys:
                    keys.append(k)
                    if len(keys) >= max_keys:
                        return keys
    return keys


def _json_array_element_kind(samples: Sequence[str]) -> str | None:
    """For json-array columns, infer the element type as 'object' / 'number' / 'string'."""
    object_count = number_count = string_count = 0
    for s in samples:
        try:
            parsed = json.loads(s)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, list) or not parsed:
            continue
        first = parsed[0]
        if isinstance(first, dict):
            object_count += 1
        elif isinstance(first, (int, float)):
            number_count += 1
        elif isinstance(first, str):
            string_count += 1
    if object_count and not (number_count or string_count):
        return "object"
    if number_count and not (object_count or string_count):
        return "number"
    if string_count and not (object_count or number_count):
        return "string"
    return None  # mixed; defer to the LLM


# ----------------------------------------------------------------------------
# Dataclasses
# ----------------------------------------------------------------------------


@dataclass
class ColumnSummary:
    """Per-column summary for a single CSV."""

    name: str
    inferred_type: str  # xsd:* or json-array / json-object
    non_null_count: int
    total_rows: int
    unique_count: int  # 0 if not computed (e.g. unbounded high-card column)
    sample_values: list[str]
    # JSON-only:
    json_keys: list[str] = field(default_factory=list)  # for json-object
    json_element_kind: str | None = None  # for json-array

    @property
    def non_null_rate(self) -> float:
        return 0.0 if self.total_rows == 0 else self.non_null_count / self.total_rows


@dataclass
class UniquenessReport:
    """Result of testing one tuple of columns for globally-unique key candidacy."""

    key: tuple[str, ...]  # e.g. ("SID", "sample_id")
    total_rows_considered: int  # rows where every key column is non-empty
    distinct_tuples: int
    collision_count: int  # total_rows_considered - distinct_tuples
    is_unique: bool  # collision_count == 0 and total_rows_considered > 0


@dataclass
class ForeignKeyCandidate:
    """A pair of (csv_a.column_a, csv_b.column_b) with overlapping values."""

    from_csv: str
    from_column: str
    to_csv: str
    to_column: str
    overlap_count: int
    from_unique_count: int
    overlap_ratio: float  # overlap_count / from_unique_count


@dataclass
class XmlIterator:
    """One structural iterator discovered in an XML/JATS document.

    ``iterator`` is the absolute XPath an ``rml:iterator`` would use (Morph-KGC
    evaluates iterators with full XPath 3.0); ``count`` is how many nodes it
    matches; ``has_id`` is whether those nodes carry an ``@id`` (so a stable IRI
    can be templated from ``{@id}`` — false means the node needs a positional /
    post-pass key). ``note`` carries a short hint (e.g. "no @id → post-pass").
    """

    iterator: str
    element: str
    count: int
    has_id: bool
    note: str = ""


@dataclass
class SourceInspection:
    """Full structured result for one source file (CSV, JSON, or XML/JATS).

    ``source_kind`` distinguishes them; ``iterator`` is the JSONPath record
    iterator for JSON (or the primary XPath iterator for XML; ``None`` for CSV).
    For JSON, ``columns[*].name`` holds a dot-path leaf field usable verbatim as an
    ``rml:reference`` under ``ql:JSONPath``. For XML/JATS the tabular column/uniqueness
    model does not apply: the document structure is reported via ``xml_iterators``.
    """

    path: str  # absolute or relative path as given
    name: str  # basename
    total_rows: int
    columns: list[ColumnSummary]
    uniqueness_reports: list[UniquenessReport]  # candidate keys evaluated for this source
    source_kind: str = "csv"  # "csv" | "json" | "xml"
    iterator: str | None = None  # JSONPath/XPath record iterator (None for CSV)
    xml_iterators: list[XmlIterator] | None = None  # structural iterators for XML/JATS
    root_element: str | None = None  # XML document root tag
    dialect: SourceDialect | None = None  # non-default dialect the source was read with
    dialect_origin: str | None = None  # "detected" | "specified" (None when default)
    # The identify-and-advise preamble form ("keyvalue"/"lines") when a preamble
    # block was detected but is still dropped — advises opt-in header-metadata
    # ingestion (ADR source-dialect.md). None when there is no preamble to advise on.
    preamble_hint: str | None = None

    def column(self, name: str) -> ColumnSummary | None:
        return next((c for c in self.columns if c.name == name), None)


# Back-compat alias: this type was CSV-only before #19 added JSON support.
CSVInspection = SourceInspection


# ----------------------------------------------------------------------------
# Core inspection
# ----------------------------------------------------------------------------


# A "sample" of values we keep per column; we read the full CSV but only retain
# this many distinct example values for the inferred_type / json detection.
_SAMPLE_RING = 200

# Threshold for "ID candidate" — a column is considered an ID candidate if its
# unique-rate is >= this fraction of non-null rows.
ID_UNIQUE_THRESHOLD = 0.95

# Max columns to try in composite keys (combinations grow combinatorially).
_MAX_COMPOSITE_DEPTH = 3


def _stream_rows(path: Path) -> Iterator[dict[str, str]]:
    """Open a CSV with BOM-tolerant encoding and yield each row as a dict."""
    with path.open(encoding="utf-8-sig", newline="") as fh:
        yield from csv.DictReader(fh)


def _dialect_rows(path: Path, dialect: SourceDialect) -> list[dict[str, str]]:
    """Materialise dict rows through a non-default dialect.

    The first dialect-applied row is the header, the rest are data. Shorter
    rows fill with empty cells and extra cells are dropped (the same
    forgiveness ``csv.DictReader`` extends to ragged rows).
    """
    rows_iter = iter_rows(path, dialect)
    header = next(rows_iter, None)
    if not header:
        return []
    return [
        {name: (row[i] if i < len(row) else "") for i, name in enumerate(header)}
        for row in rows_iter
    ]


def _summarize_rows(rows: list[dict[str, str]], columns: Sequence[str]) -> list[ColumnSummary]:
    """Build per-column summaries from already-materialised string rows.

    Shared by the CSV and JSON inspectors so both produce identical
    :class:`ColumnSummary` semantics (type inference, JSON-cell detection,
    bounded distinct/sample counts). Missing keys count as null, so this is
    safe for heterogeneous JSON records.
    """
    seen_values: dict[str, set[str]] = {c: set() for c in columns}
    samples: dict[str, list[str]] = {c: [] for c in columns}
    non_null: dict[str, int] = {c: 0 for c in columns}

    for row in rows:
        for c in columns:
            v = row.get(c, "") or ""
            if v:
                non_null[c] += 1
                if len(seen_values[c]) < _SAMPLE_RING:
                    seen_values[c].add(v)
                if len(samples[c]) < _SAMPLE_RING:
                    samples[c].append(v)

    summaries: list[ColumnSummary] = []
    for c in columns:
        col_samples = samples[c]
        # Try JSON detection first; if it matches, override the xsd type.
        json_kind = _detect_json_kind(col_samples)
        if json_kind is not None:
            inferred = json_kind
            json_keys = _json_first_keys(col_samples) if json_kind == "json-object" else []
            element_kind = (
                _json_array_element_kind(col_samples) if json_kind == "json-array" else None
            )
        else:
            inferred = _aggregate_types(_infer_cell_type(s) for s in col_samples)
            json_keys = []
            element_kind = None

        summaries.append(
            ColumnSummary(
                name=c,
                inferred_type=inferred,
                non_null_count=non_null[c],
                total_rows=len(rows),
                unique_count=len(seen_values[c]),
                sample_values=col_samples[:3],
                json_keys=json_keys,
                json_element_kind=element_kind,
            )
        )

    return summaries


def _build_column_summaries(
    path: Path, dialect: SourceDialect | None = None
) -> tuple[list[ColumnSummary], int, list[dict[str, str]]]:
    """Stream the CSV once; return per-column summaries, row count, and a
    bounded slice of materialised rows for downstream uniqueness checks.
    """
    # We need:
    #  - non_null_count per column
    #  - unique values per column (capped at _SAMPLE_RING)
    #  - sample values for type inference (capped at _SAMPLE_RING)
    #
    # For uniqueness analysis, we ALSO need every row materialised — that means
    # we hold the whole CSV in memory. For starrydata's largest file
    # (curves.csv, 233k rows) this is acceptable; users with multi-million-row
    # CSVs should use a streaming variant (future work).
    rows = _dialect_rows(path, dialect) if dialect is not None else list(_stream_rows(path))
    if not rows:
        return [], 0, []

    # Rename Morph-KGC's reserved columns (subject / predicate) so the proposed
    # rml:reference matches the CSV the substrate feeds Morph-KGC — substrate
    # sanitizes a direct CSV's header the same way (asterism.tabularize.safe_col).
    # _safe_column is identity for every other name, so normal CSVs are unaffected.
    if any(k in _RESERVED_SOURCE_COLUMNS for k in rows[0]):
        rows = [{_safe_column(k): v for k, v in row.items()} for row in rows]
    columns = list(rows[0].keys())
    return _summarize_rows(rows, columns), len(rows), rows


def _check_uniqueness(rows: list[dict[str, str]], key: tuple[str, ...]) -> UniquenessReport:
    """Return how many distinct tuples and collisions ``key`` produces.

    Rows where *any* key column is empty are dropped from the analysis — this
    matches starrydata's behaviour where ID columns are mandatory.
    """
    tuples: Counter[tuple[str, ...]] = Counter()
    dropped = 0
    for row in rows:
        parts = tuple(row.get(c, "").strip() for c in key)
        if any(not p for p in parts):
            dropped += 1
            continue
        tuples[parts] += 1
    total = sum(tuples.values())
    distinct = len(tuples)
    return UniquenessReport(
        key=key,
        total_rows_considered=total,
        distinct_tuples=distinct,
        collision_count=total - distinct,
        is_unique=(total > 0 and distinct == total),
    )


def _id_candidate_columns(
    summaries: list[ColumnSummary], threshold: float = ID_UNIQUE_THRESHOLD
) -> list[str]:
    """Return names of columns that look like ID candidates.

    A column is an ID candidate if its non-null cells are mostly distinct
    (unique_count / non_null_count >= ``threshold``). We *do not* require
    100% uniqueness here because Phase 1 found that ``sample_id`` is reused
    across papers (90%+ unique within a paper but global collisions); the
    caller still tests global uniqueness via composite keys.
    """
    out: list[str] = []
    for s in summaries:
        if s.non_null_count == 0:
            continue
        # We capped unique_count at _SAMPLE_RING during streaming. If both
        # counts hit the cap they're not informative — fall back to "is this
        # column probably an ID by name" heuristic.
        capped = min(s.non_null_count, _SAMPLE_RING) * threshold
        by_unique = s.unique_count >= capped
        by_name = _looks_like_id_by_name(s.name) and s.inferred_type in {
            "xsd:integer",
            "xsd:string",
        }
        if by_unique or by_name:
            out.append(s.name)
    return out


_ID_NAME_HINTS = ("id", "sid", "uuid", "uid", "key", "code")


def _looks_like_id_by_name(column_name: str) -> bool:
    name = column_name.lower()
    return any(
        h == name or name.endswith(f"_{h}") or name.startswith(f"{h}_") or h in name.split("_")
        for h in _ID_NAME_HINTS
    )


def _composite_uniqueness_search(
    rows: list[dict[str, str]],
    candidates: list[str],
    fk_columns: list[str],
    max_depth: int = _MAX_COMPOSITE_DEPTH,
) -> list[UniquenessReport]:
    """For each ID candidate, test it alone and with companion columns.

    Companion pool = ``fk_columns`` union with the other ID candidates. This lets us
    find composites like ``(SID, figure_id, sample_id)`` even when only
    ``SID`` was given as the FK hint, because ``figure_id`` and ``sample_id``
    are both ID candidates of the same CSV.

    We bound the search to ``max_depth`` columns total to avoid combinatorial
    blow-up. FK companion columns (e.g. ``SID`` in starrydata) are tried first
    because they're the most likely to disambiguate.
    """
    reports: list[UniquenessReport] = []
    seen: set[tuple[str, ...]] = set()

    def _record(key: tuple[str, ...]) -> None:
        canonical = tuple(sorted(key))
        if canonical in seen:
            return
        seen.add(canonical)
        reports.append(_check_uniqueness(rows, key))

    # Companion pool: FK hints first, then other ID candidates (deduped).
    companion_pool = list(dict.fromkeys([*fk_columns, *candidates]))

    for cand in candidates:
        _record((cand,))
        others = [c for c in companion_pool if c != cand]
        # 2-column composites
        for other in others:
            _record((other, cand))
        # 3-column composites (only if max_depth >= 3)
        if max_depth >= 3:
            for other_pair in itertools.combinations(others, 2):
                if cand in other_pair:
                    continue
                _record((*other_pair, cand))
    return reports


def _sniff_preamble_form(path: Path, dialect: SourceDialect) -> str | None:
    """Read the ``skip_rows`` preamble lines through the dialect's encoding and
    classify their shape (:func:`detect_preamble_form`) for the inspect advisory.
    Best-effort (``errors="replace"``): a preamble sniff never fails inspection."""
    try:
        with path.open(encoding=dialect.encoding, errors="replace", newline="") as fh:
            lines = [line for line in (fh.readline() for _ in range(dialect.skip_rows)) if line]
    except OSError:
        return None
    return detect_preamble_form(lines, delimiter=dialect.delimiter)


def inspect_csv(
    path: Path | str,
    *,
    fk_hint_columns: Sequence[str] | None = None,
    dialect: SourceDialect | None = None,
) -> CSVInspection:
    """Inspect a single CSV.

    Args:
        path: CSV file path.
        fk_hint_columns: optional list of columns that are foreign-key
            companions to the ID candidates (e.g. ``["SID"]`` for starrydata's
            sample/curve CSVs). When provided, composite-key uniqueness is
            tested. When omitted, only single-column uniqueness is reported.
        dialect: explicit source dialect (CLI/API override). When omitted the
            dialect is auto-detected (``detect_dialect``); a default dialect —
            detected or given — keeps the original read path byte-identical.
    """
    p = Path(path)
    origin = "specified" if dialect is not None else "detected"
    effective: SourceDialect | None = dialect if dialect is not None else detect_dialect(p)
    if effective is not None and is_default(effective):
        effective, origin = None, None
    # A legacy-suffix export is normalized at ingest even with a default dialect
    # (extension-based normalization, ADR), so inspection reads it the same way;
    # `effective` stays None so nothing is reported/pinned for a default read.
    read_dialect = effective
    if read_dialect is None and p.suffix.lower() in LEGACY_SUFFIXES:
        read_dialect = SourceDialect()
    # Identify-and-advise (ADR source-dialect.md): a dropped preamble block is
    # classified (keyvalue/lines) so the inspect Markdown can advise opt-in
    # header-metadata ingestion. Never auto-adopted; a default read (no preamble)
    # leaves the hint None so a clean CSV's Markdown stays byte-identical.
    preamble_hint = (
        _sniff_preamble_form(p, read_dialect)
        if read_dialect is not None
        and read_dialect.skip_rows > 0
        and read_dialect.preamble == "drop"
        else None
    )
    summaries, row_count, rows = _build_column_summaries(p, dialect=read_dialect)
    if not rows:
        return CSVInspection(
            path=str(p),
            name=p.name,
            total_rows=0,
            columns=[],
            uniqueness_reports=[],
            dialect=effective,
            dialect_origin=origin if effective is not None else None,
            preamble_hint=preamble_hint,
        )

    id_candidates = _id_candidate_columns(summaries)
    fk_columns = list(fk_hint_columns) if fk_hint_columns else []
    # If no fk_hint given, fall back to "every other ID candidate" as a
    # potential companion column. Useful for one-CSV cases.
    if not fk_columns:
        fk_columns = [c for c in id_candidates]

    reports = _composite_uniqueness_search(rows, id_candidates, fk_columns)
    return CSVInspection(
        path=str(p),
        name=p.name,
        total_rows=row_count,
        columns=summaries,
        uniqueness_reports=reports,
        dialect=effective,
        dialect_origin=origin if effective is not None else None,
        preamble_hint=preamble_hint,
    )


# ----------------------------------------------------------------------------
# JSON inspection (#19) — flatten records to dot-path leaves (Morph-KGC parity)
# ----------------------------------------------------------------------------


# Morph-KGC reserves the DataFrame columns named ``subject`` / ``predicate`` when it
# builds term maps, so a source column with either name silently yields 0 triples.
# A JSON source is tabularized to CSV at ingest (``asterism.tabularize``), which
# renames these columns; the inspector must show the SAME renamed selectors so the
# proposed ``rml:reference`` matches the tabularized CSV. Keep this in sync with the
# canonical ``RESERVED_COLUMNS`` / ``safe_col`` in ``asterism.tabularize`` (step0 does
# not depend on the ingest package — dedup into a shared module is a follow-up).
_RESERVED_SOURCE_COLUMNS = frozenset({"subject", "predicate"})


def _safe_column(name: str) -> str:
    return f"{name}_" if name in _RESERVED_SOURCE_COLUMNS else name


def _flatten_record(obj: object, prefix: str = "") -> dict[str, str]:
    """Flatten one JSON record to ``{dot_path: string_cell}``.

    Mirrors Morph-KGC's ``pandas.json_normalize`` (sep ``.``): nested objects
    recurse into ``a.b`` keys; **list leaves are kept as a JSON-encoded cell**
    (not exploded) so :func:`_detect_json_kind` tags them ``json-array`` — the
    same shape a CSV "JSON in a cell" column has. Scalars stringify; ``None``
    becomes empty (treated as null, like a blank CSV cell). Reserved column names
    are renamed via :func:`_safe_column` to match the tabularized CSV.
    """
    out: dict[str, str] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict):
                out.update(_flatten_record(v, key))
            elif isinstance(v, list):
                out[_safe_column(key)] = json.dumps(v, ensure_ascii=False)
            elif v is None:
                out[_safe_column(key)] = ""
            elif isinstance(v, bool):
                out[_safe_column(key)] = "true" if v else "false"
            else:
                out[_safe_column(key)] = str(v)
    else:
        # A non-object record (scalar or array). Represent it under a synthetic
        # "value" field so it still has a referenceable selector.
        field_name = _safe_column(prefix or "value")
        if isinstance(obj, list):
            out[field_name] = json.dumps(obj, ensure_ascii=False)
        elif obj is None:
            out[field_name] = ""
        elif isinstance(obj, bool):
            out[field_name] = "true" if obj else "false"
        else:
            out[field_name] = str(obj)
    return out


def _detect_iterator(data: object, record_path: str | None = None) -> tuple[list, str]:
    """Return ``(records, iterator)`` for a parsed JSON document.

    * top-level array → ``$[*]``
    * top-level object with exactly one array-of-objects value → ``$.key[*]``
      (if several, the longest is chosen; pass ``record_path`` to disambiguate)
    * top-level object with no record array → the object itself is one record
      (``$``)
    """
    if record_path is not None:
        records = data.get(record_path) if isinstance(data, dict) else None
        if isinstance(records, list):
            return records, f"$.{record_path}[*]"
        # Fall through to auto-detection if the hint did not resolve.
    if isinstance(data, list):
        return data, "$[*]"
    if isinstance(data, dict):
        array_keys = [
            (k, v)
            for k, v in data.items()
            if isinstance(v, list) and v and all(isinstance(e, dict) for e in v[:10])
        ]
        if array_keys:
            k, v = max(array_keys, key=lambda kv: len(kv[1]))
            return v, f"$.{k}[*]"
        return [data], "$"
    return [], "$[*]"


def _load_json_records(
    path: Path, record_path: str | None = None
) -> tuple[list[dict[str, str]], str]:
    """Load a JSON file and return ``(flattened_rows, iterator)``."""
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    raw_records, iterator = _detect_iterator(data, record_path)
    rows = [_flatten_record(rec) for rec in raw_records]
    return rows, iterator


def _columns_in_order(rows: Sequence[dict[str, str]]) -> list[str]:
    """Ordered union of keys across (possibly heterogeneous) JSON rows."""
    columns: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for k in row:
            if k not in seen:
                seen.add(k)
                columns.append(k)
    return columns


def inspect_json(
    path: Path | str,
    *,
    fk_hint_columns: Sequence[str] | None = None,
    record_path: str | None = None,
) -> SourceInspection:
    """Inspect a single JSON file.

    Produces the same :class:`SourceInspection` contract as :func:`inspect_csv`,
    with ``source_kind="json"`` and the detected ``iterator``. Columns are
    dot-path leaf fields usable verbatim as ``rml:reference`` selectors.
    """
    p = Path(path)
    rows, iterator = _load_json_records(p, record_path)
    if not rows:
        return SourceInspection(
            path=str(p),
            name=p.name,
            total_rows=0,
            columns=[],
            uniqueness_reports=[],
            source_kind="json",
            iterator=iterator,
        )

    columns = _columns_in_order(rows)
    summaries = _summarize_rows(rows, columns)

    id_candidates = _id_candidate_columns(summaries)
    fk_columns = list(fk_hint_columns) if fk_hint_columns else []
    if not fk_columns:
        fk_columns = list(id_candidates)

    reports = _composite_uniqueness_search(rows, id_candidates, fk_columns)
    return SourceInspection(
        path=str(p),
        name=p.name,
        total_rows=len(rows),
        columns=summaries,
        uniqueness_reports=reports,
        source_kind="json",
        iterator=iterator,
    )


def _value_buckets(ins: SourceInspection) -> dict[str, set[str]]:
    """Re-read a source and collect a bounded distinct-value set per column.

    Dispatches on ``source_kind`` so foreign-key detection works uniformly for
    CSV and JSON sources.
    """
    buckets: dict[str, set[str]] = {c.name: set() for c in ins.columns}
    if ins.source_kind == "xml" or not buckets:
        return buckets  # XML carries no columns — nothing to bucket for FK detection
    cap = _SAMPLE_RING * 5
    if ins.source_kind == "json":
        rows, _ = _load_json_records(Path(ins.path))
        for row in rows:
            for col_name in buckets:
                v = (row.get(col_name) or "").strip()
                if v and len(buckets[col_name]) < cap:
                    buckets[col_name].add(v)
        return buckets
    if ins.dialect is not None or Path(ins.path).suffix.lower() in LEGACY_SUFFIXES:
        for row in _dialect_rows(Path(ins.path), ins.dialect or SourceDialect()):
            for col_name in buckets:
                v = (row.get(col_name) or "").strip()
                if v and len(buckets[col_name]) < cap:
                    buckets[col_name].add(v)
        return buckets
    with Path(ins.path).open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            for col_name in buckets:
                v = (row.get(col_name) or "").strip()
                if v and len(buckets[col_name]) < cap:
                    buckets[col_name].add(v)
    return buckets


def _detect_foreign_keys(inspections: Sequence[SourceInspection]) -> list[ForeignKeyCandidate]:
    """Across multiple CSVs, flag column pairs with overlapping value sets.

    We compare *every* column pair where both sides have non-empty values
    and report cases where:
      - column names match (string equality), OR
      - the smaller column's distinct value set is mostly contained in the larger.

    For the second case we use a sampled-set heuristic (we only stored
    _SAMPLE_RING values per column), so the result is a "candidate" — the
    LLM should re-confirm with a SPARQL or pandas verify step.
    """
    candidates: list[ForeignKeyCandidate] = []
    # Pull per-column distinct-value samples by re-reading each source (CSV or
    # JSON). We don't cache them on ColumnSummary to keep that dataclass small.
    cache: dict[str, dict[str, set[str]]] = {ins.path: _value_buckets(ins) for ins in inspections}

    for a, b in itertools.combinations(inspections, 2):
        for col_a in a.columns:
            for col_b in b.columns:
                if col_a.name != col_b.name:
                    continue
                set_a = cache[a.path].get(col_a.name, set())
                set_b = cache[b.path].get(col_b.name, set())
                if not set_a or not set_b:
                    continue
                overlap = set_a & set_b
                if not overlap:
                    continue
                candidates.append(
                    ForeignKeyCandidate(
                        from_csv=a.name,
                        from_column=col_a.name,
                        to_csv=b.name,
                        to_column=col_b.name,
                        overlap_count=len(overlap),
                        from_unique_count=len(set_a),
                        overlap_ratio=len(overlap) / max(len(set_a), 1),
                    )
                )
    return candidates


def inspect_csv_set(
    paths: Sequence[Path | str],
    *,
    fk_hint_columns: Sequence[str] | None = None,
) -> tuple[list[CSVInspection], list[ForeignKeyCandidate]]:
    """Inspect a coordinated set of CSVs and report cross-file foreign keys.

    The same ``fk_hint_columns`` is applied to each CSV — this matches the
    starrydata case where ``SID`` joins papers / samples / curves.
    """
    inspections = [inspect_csv(p, fk_hint_columns=fk_hint_columns) for p in paths]
    fks = _detect_foreign_keys(inspections)
    return inspections, fks


def inspect_json_set(
    paths: Sequence[Path | str],
    *,
    fk_hint_columns: Sequence[str] | None = None,
    record_path: str | None = None,
) -> tuple[list[SourceInspection], list[ForeignKeyCandidate]]:
    """Inspect a coordinated set of JSON files and report cross-file foreign keys."""
    inspections = [
        inspect_json(p, fk_hint_columns=fk_hint_columns, record_path=record_path) for p in paths
    ]
    fks = _detect_foreign_keys(inspections)
    return inspections, fks


_JSON_SUFFIXES = {".json", ".geojson"}
_XML_SUFFIXES = {".xml"}


def _is_json_path(path: Path | str) -> bool:
    return Path(path).suffix.lower() in _JSON_SUFFIXES


def _is_xml_path(path: Path | str) -> bool:
    return Path(path).suffix.lower() in _XML_SUFFIXES


# Structural iterators probed for a JATS-shaped article (document-ontology layer).
# Each entry is (absolute XPath for rml:iterator, relative ElementTree path to count
# from the root). The relative path is what stdlib ElementTree understands; the
# absolute one is what a mapping's rml:iterator declares.
_JATS_PROBES: tuple[tuple[str, str, str], ...] = (
    ("section", "/article/body/sec", "body/sec"),
    ("subsection", "/article/body/sec/sec", "body/sec/sec"),
    ("figure", "/article/body//fig", "body//fig"),
    ("table", "/article/body//table-wrap", "body//table-wrap"),
    ("paragraph", "/article/body//p", "body//p"),
)


def inspect_xml(path: Path | str) -> SourceInspection:
    """Inspect a single XML/JATS document (document-ontology layer).

    Unlike CSV/JSON, a JATS article is a structure tree, not a table of records,
    so this reports the structural *iterators* (``xml_iterators``) — which element
    types exist under ``<body>``, how many, and whether they carry a stable ``@id``
    — rather than columns. That is exactly what the propose step needs to author a
    ``ql:XPath`` mapping: which ``rml:iterator`` to use and whether a node's IRI can
    be keyed by ``{@id}`` (stable) or needs the deterministic post-pass (no ``@id``,
    e.g. ``<p>``). Pure stdlib ElementTree (no lxml dependency).
    """
    import xml.etree.ElementTree as ET

    p = Path(path)
    root = ET.parse(p).getroot()
    iterators: list[XmlIterator] = []
    for element, abs_xpath, rel_path in _JATS_PROBES:
        nodes = root.findall(rel_path)
        if not nodes:
            continue
        has_id = any(n.get("id") for n in nodes)
        note = "" if has_id else "no @id → positional key via the deterministic post-pass"
        iterators.append(
            XmlIterator(
                iterator=abs_xpath, element=element, count=len(nodes), has_id=has_id, note=note
            )
        )
    primary = iterators[0].iterator if iterators else f"/{root.tag}"
    return SourceInspection(
        path=str(p),
        name=p.name,
        total_rows=sum(it.count for it in iterators),
        columns=[],
        uniqueness_reports=[],
        source_kind="xml",
        iterator=primary,
        xml_iterators=iterators,
        root_element=root.tag,
    )


def _inspect_one(
    p: Path | str,
    *,
    fk_hint_columns: Sequence[str] | None,
    record_path: str | None,
    dialect: SourceDialect | None = None,
) -> SourceInspection:
    if _is_json_path(p):
        return inspect_json(p, fk_hint_columns=fk_hint_columns, record_path=record_path)
    if _is_xml_path(p):
        return inspect_xml(p)
    return inspect_csv(p, fk_hint_columns=fk_hint_columns, dialect=dialect)


def inspect_source_set(
    paths: Sequence[Path | str],
    *,
    fk_hint_columns: Sequence[str] | None = None,
    record_path: str | None = None,
    dialects: Mapping[str, SourceDialect] | None = None,
) -> tuple[list[SourceInspection], list[ForeignKeyCandidate]]:
    """Inspect a set of sources, dispatching per file by extension.

    ``.json``/``.geojson`` → JSON, ``.xml`` → XML/JATS, else CSV. Mixed sets are
    supported; foreign-key candidates are reported across the tabular (CSV/JSON)
    sources only (XML carries no columns). This is the source-agnostic entry point
    the API/CLI use; the per-kind ``inspect_csv_set`` / ``inspect_json_set`` remain
    for callers that know the kind up front. ``dialects`` maps a source basename
    to an explicit dialect override (tabular sources only); sources not listed
    are auto-detected.
    """
    dialects = dialects or {}
    inspections = [
        _inspect_one(
            p,
            fk_hint_columns=fk_hint_columns,
            record_path=record_path,
            dialect=dialects.get(Path(p).name),
        )
        for p in paths
    ]
    fks = _detect_foreign_keys(inspections)
    return inspections, fks


# ----------------------------------------------------------------------------
# Markdown renderer (matches ai-assisted-step0-prompts.md §1 output format)
# ----------------------------------------------------------------------------


def _render_xml(buf: io.StringIO, ins: SourceInspection) -> None:
    """Render the ``## XML:`` block for a JATS-shaped document source.

    Reports the structural iterators (not columns): which to use as ``rml:iterator``
    and whether each node carries a stable ``@id``. This is what the propose step
    needs to author a ``ql:XPath`` mapping for the document-ontology layer.
    """
    buf.write(f"## XML: {ins.name}\n\n")
    buf.write(f"- Root element: `{ins.root_element}`\n")
    buf.write(f"- Path: `{ins.path}`\n")
    buf.write(
        "- Reference style: declarative `ql:XPath`. Emit "
        "`rml:referenceFormulation ql:XPath` and an `rml:iterator` from the table "
        "below. References/templates are **iterator-relative element/attribute paths** "
        "(`@id`, `title`, `label`, `.` for text) — Morph-KGC's XML reader does NOT "
        "support `[@a='v']` predicates or parent/ancestor axes, and returns only an "
        "element's `.text` (mixed content is truncated). Build `po:contains` parent→child "
        "via a multi-valued child reference (`{sec/@id}`, `{fig/@id}`). The per-paper IRI "
        "base is a constant (the ingest is per-paper).\n\n"
    )
    buf.write("### Structural iterators\n\n")
    buf.write("| iterator | element | count | stable @id | note |\n")
    buf.write("|---|---|---|---|---|\n")
    for it in ins.xml_iterators or []:
        idmark = "✓" if it.has_id else "✗"
        buf.write(f"| `{it.iterator}` | {it.element} | {it.count:,} | {idmark} | {it.note} |\n")
    buf.write(
        "\nNodes with a stable `@id` (✓) get an `@id`-keyed IRI declaratively; nodes "
        "without (✗, e.g. paragraphs/sentences) are produced by the deterministic "
        "post-pass and recorded as a dated `lit:DocumentParsingActivity` claim "
        "(ADR document-ontology-layer.md).\n\n"
    )


def render_markdown(
    inspections: Sequence[CSVInspection],
    fk_candidates: Sequence[ForeignKeyCandidate] = (),
) -> str:
    """Produce the Markdown body the Step 3 schema-proposal prompt expects."""
    buf = io.StringIO()
    for ins in inspections:
        if ins.source_kind == "xml":
            _render_xml(buf, ins)
            continue
        if ins.source_kind == "json":
            iterator = ins.iterator or "$[*]"
            csv_name = Path(ins.name).stem + ".csv"
            buf.write(f"## JSON: {ins.name}\n\n")
            buf.write(f"- Records: {ins.total_rows:,} (iterator `{iterator}`)\n")
            buf.write(f"- Path: `{ins.path}`\n")
            buf.write(
                f"- Ingest normalizes this JSON to **`{csv_name}`** (tabularized: nested "
                "objects → dot-path columns, arrays → JSON-string cells). Emit "
                f'`rml:source "{csv_name}"` with `rml:referenceFormulation ql:CSV` (NOT '
                "JSONPath / iterator). `rml:reference` / `rr:template` use the dot-path "
                "columns below verbatim (e.g. `structure.spacegroup`); an array column "
                "(type `json-array`) holds the array as a JSON string → explode it with "
                "`fn:json_array` (scalars) or `fn:json_pluck` (objects), not a raw fallback.\n\n"
            )
        else:
            buf.write(f"## CSV: {ins.name}\n\n")
            buf.write(f"- Total rows: {ins.total_rows:,}\n")
            buf.write(f"- Path: `{ins.path}`\n")
            if ins.dialect is not None:
                origin = "specified" if ins.dialect_origin == "specified" else "auto-detected"
                buf.write(f"- Dialect: {describe_dialect(ins.dialect)} ({origin})\n")
            if ins.preamble_hint is not None and ins.dialect is not None:
                skip = ins.dialect.skip_rows
                buf.write(
                    f"- Header metadata: the {skip} preamble line(s) before the header "
                    f"look like **{ins.preamble_hint}** and are currently dropped. To "
                    f"ingest them as columns broadcast onto every row, add "
                    f'`dialects: {{"{ins.name}": {{preamble: {ins.preamble_hint}}}}}` '
                    f"to the mapping spec (or set it in the wizard's read settings).\n"
                )
            buf.write("\n")

        buf.write("### Columns\n\n")
        buf.write("| name | type | non-null rate | distinct values | sample values |\n")
        buf.write("|---|---|---|---|---|\n")
        for col in ins.columns:
            rate = f"{col.non_null_rate:.0%}"
            distinct = f"{col.unique_count}"
            if col.unique_count >= _SAMPLE_RING:
                distinct = f"≥{_SAMPLE_RING}"
            samples = ", ".join(f"`{s[:40]}`" for s in col.sample_values) or "(no values)"
            buf.write(f"| `{col.name}` | {col.inferred_type} | {rate} | {distinct} | {samples} |\n")
        buf.write("\n")

        json_cols = [c for c in ins.columns if c.inferred_type in {"json-array", "json-object"}]
        if json_cols:
            buf.write("### JSON columns\n\n")
            for col in json_cols:
                if col.inferred_type == "json-object":
                    keys = ", ".join(f"`{k}`" for k in col.json_keys) or "(no keys seen)"
                    buf.write(f"- `{col.name}` (object) — keys: {keys}\n")
                else:
                    kind = col.json_element_kind or "mixed"
                    buf.write(f"- `{col.name}` (array of {kind})\n")
            buf.write("\n")

        if ins.uniqueness_reports:
            buf.write("### Uniqueness (★ trap T1 from workflow §6)\n\n")
            buf.write("| key | rows considered | distinct | collisions | unique? |\n")
            buf.write("|---|---|---|---|---|\n")
            for rep in ins.uniqueness_reports:
                key_str = "(" + ", ".join(rep.key) + ")"
                check = "✓" if rep.is_unique else "✗"
                buf.write(
                    f"| {key_str} | {rep.total_rows_considered:,} | {rep.distinct_tuples:,} "
                    f"| {rep.collision_count:,} | {check} |\n"
                )
            buf.write("\n")
        else:
            buf.write(
                "### Uniqueness\n\n(no ID candidate columns detected; "
                "supply `fk_hint_columns` if known)\n\n"
            )

    if fk_candidates:
        buf.write("## Foreign key candidates (across sources)\n\n")
        buf.write(
            "| from CSV | column | to CSV | column | overlap count | from distinct | ratio |\n"
        )
        buf.write("|---|---|---|---|---|---|---|\n")
        for fk in fk_candidates:
            buf.write(
                f"| {fk.from_csv} | `{fk.from_column}` | {fk.to_csv} | `{fk.to_column}` "
                f"| {fk.overlap_count:,} | {fk.from_unique_count:,} | {fk.overlap_ratio:.1%} |\n"
            )
        buf.write("\n")

    return buf.getvalue()


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------


def _build_arg_parser():  # type: ignore[no-untyped-def]
    import argparse

    p = argparse.ArgumentParser(
        prog="asterism-inspect",
        description=(
            "Inspect one or more sources (CSV, JSON, or XML/JATS) and emit the "
            "Markdown body that the Step 3 schema-proposal prompt expects. The "
            "source kind is picked per file by extension (.json / .geojson → JSON, "
            ".xml → XML/JATS, else CSV)."
        ),
    )
    p.add_argument(
        "source", type=Path, nargs="+", help="Source file(s) to inspect (CSV, JSON, or XML)"
    )
    p.add_argument(
        "--fk",
        dest="fk_hint",
        action="append",
        default=[],
        help=(
            "Foreign-key companion column. Repeatable. Example for starrydata: "
            "--fk SID  (joins papers/samples/curves)."
        ),
    )
    p.add_argument(
        "--record-path",
        dest="record_path",
        default=None,
        help=(
            "For JSON sources whose records live under a top-level key, the key "
            "holding the array of records (e.g. --record-path data). Auto-detected "
            "when omitted."
        ),
    )
    p.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write Markdown to this file. Defaults to stdout.",
    )
    p.add_argument(
        "--encoding",
        default=None,
        help="Override the detected source encoding (any Python codec name, e.g. cp932).",
    )
    p.add_argument(
        "--delimiter",
        default=None,
        help=(
            "Override the detected delimiter: a single character or one of "
            "comma / tab / semicolon / pipe / space / whitespace."
        ),
    )
    p.add_argument(
        "--collapse",
        action="store_true",
        help="Treat consecutive delimiters as one (Excel-style).",
    )
    p.add_argument(
        "--skip-rows",
        dest="skip_rows",
        type=int,
        default=None,
        help="Lines before the header row (preamble).",
    )
    p.add_argument(
        "--preamble",
        default=None,
        choices=("drop", "keyvalue", "lines"),
        help=(
            "How to treat the preamble lines: drop (default), keyvalue (Key: value "
            "pairs) or lines (each preamble line) — broadcast as columns onto every row."
        ),
    )
    return p


# Spelled-out delimiter names the CLI accepts (a raw single char also works).
_DELIMITER_OPTIONS = {
    "comma": ",",
    "tab": "\t",
    "\\t": "\t",
    "semicolon": ";",
    "pipe": "|",
    "space": " ",
    "whitespace": "whitespace",
}


def _cli_dialects(args) -> dict[str, SourceDialect] | None:  # type: ignore[no-untyped-def]
    """Per-source dialect overrides from the CLI flags: detect each tabular
    source, then replace only the explicitly-given fields."""
    overrides: dict[str, object] = {}
    if args.encoding is not None:
        overrides["encoding"] = args.encoding
    if args.delimiter is not None:
        delimiter = _DELIMITER_OPTIONS.get(args.delimiter, args.delimiter)
        if delimiter != "whitespace" and len(delimiter) != 1:
            raise SystemExit(
                f"--delimiter must be a single character or one of "
                f"{', '.join(sorted(_DELIMITER_OPTIONS))} (got {args.delimiter!r})."
            )
        overrides["delimiter"] = delimiter
    if args.collapse:
        overrides["collapse"] = True
    if args.skip_rows is not None:
        if args.skip_rows < 0:
            raise SystemExit("--skip-rows must be a non-negative integer.")
        overrides["skip_rows"] = args.skip_rows
    if getattr(args, "preamble", None) is not None:
        overrides["preamble"] = args.preamble
    if not overrides:
        return None
    return {
        Path(src).name: replace(detect_dialect(src), **overrides)  # type: ignore[arg-type]
        for src in args.source
        if not (_is_json_path(src) or _is_xml_path(src))
    }


def _main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    fk_hint = args.fk_hint or None
    inspections, fks = inspect_source_set(
        args.source,
        fk_hint_columns=fk_hint,
        record_path=args.record_path,
        dialects=_cli_dialects(args),
    )
    md = render_markdown(inspections, fks)
    if args.output is None:
        print(md)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(md, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
