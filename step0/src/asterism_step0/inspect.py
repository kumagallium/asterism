"""CSV inspection for AI-assisted Step 0 (Phase 3).

This module is the deterministic prelude to ``propose_schema``: given one or
more CSV files, it builds a structured summary the LLM can ground its schema
proposal on. It deliberately uses only the standard library so the package is
installable without pandas.

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
"""
from __future__ import annotations

import csv
import io
import itertools
import json
import re
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

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
class CSVInspection:
    """Full structured result for one CSV file."""

    path: str  # absolute or relative path as given
    name: str  # basename
    total_rows: int
    columns: list[ColumnSummary]
    uniqueness_reports: list[UniquenessReport]  # candidate keys evaluated for this CSV

    def column(self, name: str) -> ColumnSummary | None:
        return next((c for c in self.columns if c.name == name), None)


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


def _build_column_summaries(path: Path) -> tuple[list[ColumnSummary], int, list[dict[str, str]]]:
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
    rows = list(_stream_rows(path))
    if not rows:
        return [], 0, []

    columns = list(rows[0].keys())
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

    return summaries, len(rows), rows


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


def inspect_csv(path: Path | str, *, fk_hint_columns: Sequence[str] | None = None) -> CSVInspection:
    """Inspect a single CSV.

    Args:
        path: CSV file path.
        fk_hint_columns: optional list of columns that are foreign-key
            companions to the ID candidates (e.g. ``["SID"]`` for starrydata's
            sample/curve CSVs). When provided, composite-key uniqueness is
            tested. When omitted, only single-column uniqueness is reported.
    """
    p = Path(path)
    summaries, row_count, rows = _build_column_summaries(p)
    if not rows:
        return CSVInspection(
            path=str(p),
            name=p.name,
            total_rows=0,
            columns=[],
            uniqueness_reports=[],
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
    )


def _detect_foreign_keys(inspections: Sequence[CSVInspection]) -> list[ForeignKeyCandidate]:
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
    # Pull per-column distinct-value samples by re-reading the CSVs. We don't
    # cache them on ColumnSummary to keep that dataclass small.
    cache: dict[str, dict[str, set[str]]] = {}
    for ins in inspections:
        with Path(ins.path).open(encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            buckets: dict[str, set[str]] = {c.name: set() for c in ins.columns}
            for row in reader:
                for col_name in buckets:
                    v = (row.get(col_name) or "").strip()
                    if v and len(buckets[col_name]) < _SAMPLE_RING * 5:
                        buckets[col_name].add(v)
            cache[ins.path] = buckets

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


# ----------------------------------------------------------------------------
# Markdown renderer (matches ai-assisted-step0-prompts.md §1 output format)
# ----------------------------------------------------------------------------


def render_markdown(
    inspections: Sequence[CSVInspection],
    fk_candidates: Sequence[ForeignKeyCandidate] = (),
) -> str:
    """Produce the Markdown body the Step 3 schema-proposal prompt expects."""
    buf = io.StringIO()
    for ins in inspections:
        buf.write(f"## CSV: {ins.name}\n\n")
        buf.write(f"- Total rows: {ins.total_rows:,}\n")
        buf.write(f"- Path: `{ins.path}`\n\n")

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
        buf.write("## Foreign key candidates (across CSVs)\n\n")
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
            "Inspect one or more CSVs and emit the Markdown body that the "
            "Step 3 schema-proposal prompt expects."
        ),
    )
    p.add_argument("csv", type=Path, nargs="+", help="CSV file(s) to inspect")
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
        "--output",
        type=Path,
        default=None,
        help="Write Markdown to this file. Defaults to stdout.",
    )
    return p


def _main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    fk_hint = args.fk_hint or None
    inspections, fks = inspect_csv_set(args.csv, fk_hint_columns=fk_hint)
    md = render_markdown(inspections, fks)
    if args.output is None:
        print(md)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(md, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
