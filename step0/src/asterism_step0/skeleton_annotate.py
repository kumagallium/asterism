"""Deterministic evidence for the skeleton human gate (Phase 2b follow-up).

Why this module exists
----------------------
The skeleton gate asks a domain expert — NOT an ontology engineer — to confirm
the single costliest design decision: the subject key. "Is
``sdr:point/{2θ (deg)}`` a good key?" is unanswerable from the template string
alone; it is trivially answerable from the DATA ("312 of 1,024 rows would
collapse onto an ID another row already uses"). The inspector already computes
exactly this for the AI (uniqueness statistics ride the prompt); this module
computes it FOR THE HUMAN, per skeleton map, so the gate shows evidence instead
of asking for faith.

Everything here is deterministic and LLM-free: re-read the (dialect-applied)
source, test the AI's chosen key columns for global uniqueness, show real
example IDs (prefix-expanded), name concrete colliding rows, and offer the
inspector's own unique key combinations as one-click fix candidates. The same
function serves the initial skeleton response and the re-validate endpoint the
gate calls after a human edit.

Scope: tabular sources (CSV/TSV and dialect-read instrument text). JSON and
XML/document maps get an honest ``checkable: false`` note instead of a guess —
never a silent pass.
"""
from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .dialect import SourceDialect
from .inspect import (
    SourceInspection,
    _check_uniqueness,
    _dialect_rows,
    _stream_rows,
    inspect_source_set,
)

__all__ = ["annotate_skeleton"]

# A {column} placeholder inside a subject template (same shape the IR compiler
# expands; an escaped \{ is a literal brace, not a placeholder).
_PLACEHOLDER = re.compile(r"(?<!\\)\{([^{}]+)\}")
# A CURIE head: `sdr:point/…` → prefix `sdr`. A full IRI (http/https) has no
# prefix to resolve.
_CURIE_HEAD = re.compile(r"^([A-Za-z][\w.-]*):")

# How many example IDs / collision groups to surface per map — enough to make
# the failure concrete, few enough to stay a glance.
_PREVIEW_ROWS = 3
_COLLISION_EXAMPLES = 2
_KEY_CANDIDATES = 3
# Measurement-valued columns make semantically wrong IDs even when accidentally
# unique — candidates made ONLY of these types rank last, never first.
_MEASUREMENT_TYPES = {"xsd:double", "xsd:float", "xsd:decimal"}


def _expand_curie(value: str, prefixes: Mapping[str, str]) -> str:
    """Expand a leading `prefix:` through the skeleton's declared prefixes."""
    m = _CURIE_HEAD.match(value)
    if not m or value.startswith(("http://", "https://")):
        return value
    ns = prefixes.get(m.group(1))
    return ns + value[m.end() :] if ns else value


def _undeclared_prefixes(
    template: str | None, classes: Sequence[str], prefixes: Mapping[str, str]
) -> list[str]:
    """CURIE prefixes used by the subject that ``prefixes`` never declares."""
    heads: set[str] = set()
    for value in [template or "", *classes]:
        if value.startswith(("http://", "https://")):
            continue
        m = _CURIE_HEAD.match(value)
        if m:
            heads.add(m.group(1))
    # xsd is builtin for the IR compiler; never report it.
    return sorted(h for h in heads if h != "xsd" and h not in prefixes)


def _read_rows(path: Path, dialect: SourceDialect | None) -> list[dict[str, str]]:
    """Materialise dict rows the same way the inspector read this source."""
    if dialect is not None:
        return _dialect_rows(path, dialect)
    return list(_stream_rows(path))


def _render_template(template: str, row: Mapping[str, str], prefixes: Mapping[str, str]) -> str:
    """One row's ID exactly as the mapping would mint it (prefix-expanded).

    Values are substituted verbatim — no escaping — because that IS what the
    RML template does; showing a space or unit inside the resulting IRI is a
    feature (the human should see it), not a rendering bug.
    """
    expanded = _expand_curie(template, prefixes)
    return _PLACEHOLDER.sub(lambda m: row.get(m.group(1), ""), expanded)


def _collision_examples(
    rows: list[dict[str, str]], key: tuple[str, ...], first_data_line: int
) -> list[dict[str, Any]]:
    """The largest duplicate-key groups, with 1-based file line numbers.

    ``first_data_line`` counts the preamble (dialect ``skip_rows``) and the
    header, so the numbers match the file as opened in an editor/spreadsheet.
    Rows with an empty key cell are skipped (mirrors ``_check_uniqueness``,
    which only considers rows where every key column is non-empty).
    """
    groups: dict[tuple[str, ...], list[int]] = defaultdict(list)
    for i, row in enumerate(rows):
        values = tuple((row.get(col) or "").strip() for col in key)
        if key and any(v == "" for v in values):
            continue
        groups[values].append(i + first_data_line)
    dupes = sorted(
        ((vals, lines) for vals, lines in groups.items() if len(lines) > 1),
        key=lambda item: (-len(item[1]), item[1][0]),
    )
    out: list[dict[str, Any]] = []
    for values, lines in dupes[:_COLLISION_EXAMPLES]:
        out.append(
            {
                "key_values": dict(zip(key, values, strict=True)) if key else {},
                "row_count": len(lines),
                "line_numbers": lines[:4],
            }
        )
    return out


def _key_candidates(
    inspection: SourceInspection, current_key: tuple[str, ...]
) -> list[dict[str, Any]]:
    """Unique key combinations the inspector already proved, smallest first.

    A candidate whose EVERY column is a measurement type (double/float/decimal)
    is only accidentally unique — it ranks last and carries
    ``measurement_only: true`` so the UI can caveat it.
    """
    types = {c.name: c.inferred_type for c in inspection.columns}

    def measurement_only(key: tuple[str, ...]) -> bool:
        return bool(key) and all(types.get(col) in _MEASUREMENT_TYPES for col in key)

    current = tuple(sorted(current_key))
    seen: set[tuple[str, ...]] = set()
    ranked = sorted(
        (r for r in inspection.uniqueness_reports if r.is_unique),
        key=lambda r: (measurement_only(r.key), len(r.key), -r.total_rows_considered),
    )
    out: list[dict[str, Any]] = []
    for report in ranked:
        canonical = tuple(sorted(report.key))
        if canonical == current or canonical in seen:
            continue
        seen.add(canonical)
        out.append(
            {
                "columns": list(report.key),
                "rows_considered": report.total_rows_considered,
                "measurement_only": measurement_only(report.key),
            }
        )
        if len(out) >= _KEY_CANDIDATES:
            break
    return out


def _annotate_map(
    map_entry: Mapping[str, Any],
    prefixes: Mapping[str, str],
    source_path: Path | None,
    inspection: SourceInspection | None,
) -> dict[str, Any]:
    """Evidence for ONE skeleton map. Never raises: unreadable → checkable:false."""
    subject = map_entry.get("subject") or {}
    template = subject.get("template") if isinstance(subject, Mapping) else None
    constant = subject.get("constant") if isinstance(subject, Mapping) else None
    classes = list(subject.get("classes") or []) if isinstance(subject, Mapping) else []

    ann: dict[str, Any] = {
        "checkable": False,
        "undeclared_prefixes": _undeclared_prefixes(template, classes, prefixes),
        "expanded_classes": [
            {"curie": c, "iri": _expand_curie(c, prefixes)} for c in classes
        ],
    }

    if constant is not None and template is None:
        # Document-style map: one subject per source document — uniqueness over
        # rows does not apply. Say so instead of silently passing.
        ann["reason"] = "constant"
        ann["expanded_template"] = _expand_curie(str(constant), prefixes)
        return ann

    if not template:
        ann["reason"] = "no-template"
        return ann

    ann["expanded_template"] = _expand_curie(str(template), prefixes)
    key = tuple(_PLACEHOLDER.findall(str(template)))
    ann["key_columns"] = list(key)

    if inspection is None or source_path is None:
        ann["reason"] = "source-not-found"
        return ann
    if inspection.source_kind != "csv":
        # JSON dot-path rows / XML iterators need their own reader to check
        # honestly; report "not checked" rather than pretend. (Follow-up.)
        ann["reason"] = f"unsupported-source-kind:{inspection.source_kind}"
        return ann

    columns = {c.name for c in inspection.columns}
    missing = [c for c in key if c not in columns]
    ann["missing_columns"] = missing
    if missing:
        ann["reason"] = "missing-columns"
        return ann

    try:
        rows = _read_rows(source_path, inspection.dialect)
    except OSError as exc:
        ann["reason"] = f"read-error:{exc}"
        return ann

    report = _check_uniqueness(rows, key)
    # Line numbers as the human sees the FILE: preamble lines (dialect
    # skip_rows) + the header line + 1-based counting.
    skip = inspection.dialect.skip_rows if inspection.dialect is not None else 0
    first_data_line = skip + 2
    ann.update(
        {
            "checkable": True,
            "rows_considered": report.total_rows_considered,
            "total_rows": len(rows),
            "distinct_ids": report.distinct_tuples,
            "colliding_rows": report.total_rows_considered - report.distinct_tuples,
            "is_unique": report.is_unique,
            "collision_examples": []
            if report.is_unique
            else _collision_examples(rows, key, first_data_line),
            "id_previews": [
                _render_template(str(template), row, prefixes)
                for row in rows[:_PREVIEW_ROWS]
            ],
            "key_candidates": [] if report.is_unique else _key_candidates(inspection, key),
        }
    )
    return ann


def annotate_skeleton(
    skeleton: Mapping[str, Any],
    paths: Sequence[Path | str],
    *,
    dialects: Mapping[str, Any] | None = None,
    record_path: str | None = None,
) -> dict[str, Any]:
    """Deterministic per-map evidence for the skeleton gate.

    Returns ``{"maps": {map_name: annotation}}``. Re-inspects the sources with
    the SAME dialect overrides the skeleton run used, so column names match what
    the AI saw. Designed to be best-effort at the call site: raise nothing the
    caller can avoid by construction; per-map problems degrade to
    ``checkable: false`` with a machine-readable ``reason``.
    """
    resolved = [Path(p) for p in paths]
    inspections, _fks = inspect_source_set(
        resolved, record_path=record_path, dialects=dialects
    )
    by_name: dict[str, tuple[Path, SourceInspection]] = {}
    for path, ins in zip(resolved, inspections, strict=True):
        by_name[path.name] = (path, ins)

    prefixes_raw = skeleton.get("prefixes")
    prefixes: dict[str, str] = (
        {str(k): str(v) for k, v in prefixes_raw.items()}
        if isinstance(prefixes_raw, Mapping)
        else {}
    )

    annotations: dict[str, Any] = {}
    for map_entry in skeleton.get("maps") or []:
        if not isinstance(map_entry, Mapping):
            continue
        name = str(map_entry.get("name") or f"map-{len(annotations) + 1}")
        source = str(map_entry.get("source") or "")
        path, inspection = by_name.get(Path(source).name, (None, None))
        annotations[name] = _annotate_map(map_entry, prefixes, path, inspection)
    return {"maps": annotations}
