"""Tabularize a native-JSON source into a flat table whose nested arrays become
JSON-string cells — the exact shape Asterism's vetted Tier 0 exploders
(``json_pluck`` / ``json_array`` / ``split``) already consume.

Morph-KGC, reading a native JSON source via JSONPath, cannot explode a nested
array and link each element to its parent record: a list-of-objects leaf is
silently dropped and a list-of-scalars leaf collapses to its first element. But
the SAME shapes, when they arrive as a JSON *string* in a tabular cell, explode
cleanly through the Tier 0 functions (the starrydata author / project_names
shape). So this module normalizes a JSON source to that string-cell tabular form
at the ingestion boundary, leaving Morph-KGC reading plain CSV and the closed
Tier 0 set unchanged. Decision of record:
``docs/architecture/native-json-denormalization.md``.

The flattening mirrors ``pandas.json_normalize`` — the semantics Morph-KGC's JSON
reader and :mod:`asterism_step0.inspect` already use — so the dot-path columns
here match the selectors the inspector surfaces: nested objects recurse into
``a.b`` columns, list leaves stay as a JSON-encoded cell, scalars stringify,
booleans lower-case, ``null`` becomes an empty cell.
"""
from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from pathlib import Path

# Morph-KGC builds its term-map intermediate DataFrame with reserved columns named
# ``subject`` and ``predicate`` (case-sensitive; ``object`` / ``graph`` are NOT
# reserved). A *source* column with either name collides: a function input
# ``rml:reference "subject"`` then resolves to the generated subject IRI instead of
# the cell, silently yielding zero triples. We rename such columns at the boundary,
# so any tabular source carrying a ``subject`` / ``predicate`` field stays safe.
#
# This is the CANONICAL definition. :mod:`asterism_step0.inspect` keeps a deliberate
# lightweight mirror (``_RESERVED_SOURCE_COLUMNS`` / ``_safe_column`` /
# ``_flatten_record``) so the step0 design tool stays installable without the ingest
# runtime (rdflib/httpx/watchfiles). The mirror is pinned to this module by a
# skip-guarded equivalence test (``step0/tests/test_inspect_tabularize_sync.py``) —
# update both sides together if this changes.
RESERVED_COLUMNS = frozenset({"subject", "predicate"})


def safe_col(name: str) -> str:
    """Rename a column that would collide with Morph-KGC's reserved term columns."""
    return f"{name}_" if name in RESERVED_COLUMNS else name


def _stringify_leaf(value: object) -> str:
    """Render a JSON leaf as a string cell (arrays stay as a JSON string)."""
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    if value is None:
        return ""
    if isinstance(value, bool):  # checked before the str fallback: bool ⊂ int
        return "true" if value else "false"
    return str(value)


def flatten_record(record: object, prefix: str = "") -> dict[str, str]:
    """Flatten one JSON record to ``{column: string_cell}``.

    Nested objects recurse into dot-path columns; list leaves stay as a
    JSON-encoded cell (so a Tier 0 exploder can later read them); scalars
    stringify. Reserved column names are renamed via :func:`safe_col`. A
    non-object record is placed under a synthetic ``value`` column so it stays
    referenceable.
    """
    out: dict[str, str] = {}
    if isinstance(record, dict):
        for key, value in record.items():
            col = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, dict):
                out.update(flatten_record(value, col))
            else:
                out[safe_col(col)] = _stringify_leaf(value)
    else:
        out[safe_col(prefix or "value")] = _stringify_leaf(record)
    return out


def tabularize_records(records: Iterable[object]) -> list[dict[str, str]]:
    """Flatten each JSON record to a string-cell row (see :func:`flatten_record`)."""
    return [flatten_record(r) for r in records]


def column_order(rows: Iterable[dict[str, str]]) -> list[str]:
    """Stable union of columns across rows, in first-seen order."""
    cols: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for col in row:
            if col not in seen:
                seen.add(col)
                cols.append(col)
    return cols


def write_rows_csv(rows: list[dict[str, str]], dest: Path | str) -> list[str]:
    """Write flattened rows to ``dest`` as CSV; return the column order written.

    A key absent from a sparse row is written as an empty cell (RFC 4180 quoting
    via :mod:`csv`), so a Tier 0 exploder sees an empty cell (→ ``None`` → row
    drop) rather than a malformed line.
    """
    cols = column_order(rows)
    with Path(dest).open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in cols})
    return cols


def _load_records(doc: object, record_path: str | None = None) -> list[object]:
    """Pick the record list from a parsed JSON document.

    * top-level array → its elements
    * top-level object + ``record_path`` naming an array value → that array
    * top-level object **wrapping** a record array (e.g. ``{"docs": [...]}``,
      ``{"data": [...]}`` — the common API-response shape) → that array,
      auto-detected as the longest array-of-objects value (mirrors the inspector's
      ``_detect_iterator`` so the columns line up)
    * otherwise → the document as a single record
    """
    if isinstance(doc, list):
        return doc
    if isinstance(doc, dict):
        if record_path is not None:
            value = doc.get(record_path)
            if isinstance(value, list):
                return value
        record_arrays = [
            v
            for v in doc.values()
            if isinstance(v, list) and v and all(isinstance(e, dict) for e in v[:10])
        ]
        if record_arrays:
            return max(record_arrays, key=len)
    return [doc]


def sanitize_csv_columns(source: Path | str, dest: Path | str) -> bool:
    """Guard a *direct* CSV source against the reserved-column collision.

    A CSV with a header literally named ``subject`` / ``predicate`` hits the same
    Morph-KGC reservation as a tabularized JSON column: the function input resolves
    to the generated term IRI, silently yielding zero triples. JSON sidesteps this
    via :func:`flatten_record`'s :func:`safe_col`, but a CSV is read as-is. So if
    (and only if) the header carries a reserved name, write ``dest`` with the header
    renamed via :func:`safe_col` and return ``True``; otherwise leave ``dest``
    untouched and return ``False`` (the caller reads the original). Rows stream
    row-by-row, so this stays memory-bounded for large CSVs.
    """
    src = Path(source)
    with src.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if header is None or not any(h in RESERVED_COLUMNS for h in header):
            return False
        with Path(dest).open("w", encoding="utf-8", newline="") as out:
            writer = csv.writer(out)
            writer.writerow([safe_col(h) for h in header])
            writer.writerows(reader)  # generator → bounded memory
    return True


def tabularize_json_to_csv(
    source: Path | str,
    dest: Path | str,
    *,
    record_path: str | None = None,
) -> list[str]:
    """Read a JSON ``source`` and write the tabularized CSV to ``dest``.

    Returns the column order written. ``record_path`` selects the record array
    inside a top-level object (mirrors the inspector's iterator detection); by
    default a top-level array is the record set and any other document is treated
    as one record.
    """
    doc = json.loads(Path(source).read_text(encoding="utf-8"))
    rows = tabularize_records(_load_records(doc, record_path))
    return write_rows_csv(rows, dest)
