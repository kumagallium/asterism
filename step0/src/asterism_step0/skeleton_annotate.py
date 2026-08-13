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
from .instance_iri import dataset_namespace_info, placeholder_prefix_issue

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
# The entity card: how many stable property values it shows, and how many
# fighting values per conflicting column (real accident evidence, not a dump).
_CARD_VALUE_COLUMNS = 8
_CONFLICT_VALUES = 3
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


def _measurement_only(inspection: SourceInspection, key: Sequence[str]) -> bool:
    """True when EVERY key column holds measurement values (double/float/decimal).

    Such a key can be unique in today's rows only by accident — two later runs
    measuring the same value collide — so it is never a semantically safe
    identity, even when the current file happens to pass the uniqueness check.
    """
    types = {c.name: c.inferred_type for c in inspection.columns}
    return bool(key) and all(types.get(col) in _MEASUREMENT_TYPES for col in key)


def _class_numeric_key_caution(
    inspection: SourceInspection, key: Sequence[str], classes: Sequence[str]
) -> list[dict[str, str]]:
    """Class names that look like a measured KEY column's name.

    Real dogfood (ZEM): the AI promoted the first numeric column into the row
    identity — key ``{Measurement temp.(C)}`` AND class ``Temperature`` — so
    the design claimed "a temperature has a resistivity". The properties-in-
    the-box diagram makes that visible AFTER the design lands; this flags it
    AT the gate. Only computed when the key is measurement-only (the K7
    caution state — exactly when a value column was promoted to identity, name
    included), which keeps a legitimate row class like ``Measurement`` over a
    mixed key out of scope. Match: the class local name vs each numeric key
    column's word tokens, exact or prefix either way with ≥ 4 shared chars
    (``temp`` ≈ ``temperature``). Deterministic, domain-free."""
    types = {c.name: c.inferred_type for c in inspection.columns}
    out: list[dict[str, str]] = []
    for cls in classes:
        local = re.sub(r"[^a-z0-9]", "", re.split(r"[:#/]", str(cls))[-1].lower())
        if len(local) < 4:
            continue
        for col in key:
            if types.get(col) not in _MEASUREMENT_TYPES:
                continue
            for token in re.split(r"[^a-z0-9]+", col.lower()):
                if len(token) < 4:
                    continue
                if token == local or token.startswith(local) or local.startswith(token):
                    out.append({"class": str(cls), "column": col, "token": token})
                    break
            else:
                continue
            break  # one hit per class is enough for the caution
    return out


def _key_candidates(
    inspection: SourceInspection, current_key: tuple[str, ...]
) -> list[dict[str, Any]]:
    """Unique key combinations the inspector already proved, smallest first.

    A candidate whose EVERY column is a measurement type (double/float/decimal)
    is only accidentally unique — it ranks last and carries
    ``measurement_only: true`` so the UI can caveat it.
    """
    current = tuple(sorted(current_key))
    seen: set[tuple[str, ...]] = set()
    ranked = sorted(
        (r for r in inspection.uniqueness_reports if r.is_unique),
        key=lambda r: (
            _measurement_only(inspection, r.key),
            len(r.key),
            -r.total_rows_considered,
        ),
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
                "measurement_only": _measurement_only(inspection, report.key),
            }
        )
        if len(out) >= _KEY_CANDIDATES:
            break
    return out


def _collapse_kind(report: Any) -> str:
    """Classify what the key does to the rows — the judgment the gate needs.

    ``unique``    every row keeps its own ID (a row-level entity — normal).
    ``singleton`` ALL rows merge into one ID (a file-scoped entity: the
                  reference card, the run header — merging is the POINT, so
                  this must never be presented as the collision accident).
    ``partial``   some rows merge, some don't — the overwrite accident.
    """
    if report.is_unique:
        return "unique"
    if report.distinct_tuples == 1:
        return "singleton"
    return "partial"


def _entity_preview(
    rows: list[dict[str, str]],
    key: tuple[str, ...],
    template: str,
    prefixes: Mapping[str, str],
    inspection: SourceInspection,
    first_data_line: int,
    collapse_kind: str,
    distinct_ids: int,
) -> dict[str, Any] | None:
    """One representative entity rendered as the CARD the mapping would build.

    The ID previews show URL syntax; this shows the consequence — which rows
    merge onto one subject and what values it ends up carrying. Group choice:
    for a partial collapse the LARGEST colliding group (the accident must be
    visible), otherwise the first group in row order.

    Column verdicts within the representative group:
    - one distinct non-empty value → a property the card carries;
    - several values on a ``partial`` map → a conflict (overwrite fight),
      shown with the real values and file line numbers;
    - several values on a ``singleton`` map → NOT a conflict: the column
      varies per row, so it belongs to a row-level map — named, not shown.
    """
    groups: dict[tuple[str, ...], list[int]] = defaultdict(list)
    for i, row in enumerate(rows):
        values = tuple((row.get(col) or "").strip() for col in key)
        if key and any(v == "" for v in values):
            continue
        groups[values].append(i)
    if not groups:
        return None
    if collapse_kind == "partial":
        rep = max(groups.values(), key=lambda idxs: (len(idxs), -idxs[0]))
    else:
        rep = next(iter(groups.values()))

    props: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    varying: list[str] = []
    for col in (c.name for c in inspection.columns):
        distinct: list[tuple[str, int]] = []
        seen_values: set[str] = set()
        for i in rep:
            v = (rows[i].get(col) or "").strip()
            if v and v not in seen_values:
                seen_values.add(v)
                distinct.append((v, i + first_data_line))
        if not distinct:
            continue
        if len(distinct) == 1:
            props.append({"column": col, "value": distinct[0][0]})
        elif collapse_kind == "partial":
            conflicts.append(
                {
                    "column": col,
                    "conflict": True,
                    "values": [
                        {"value": v, "line": line} for v, line in distinct[:_CONFLICT_VALUES]
                    ],
                    "more_values": max(0, len(distinct) - _CONFLICT_VALUES),
                }
            )
        else:
            varying.append(col)

    # Key columns first (they explain the ID); conflicts always survive the
    # column cap (the accident is the point); stable values fill what's left.
    key_set = set(key)
    key_props = [p for p in props if p["column"] in key_set]
    rest = [p for p in props if p["column"] not in key_set]
    room = max(0, _CARD_VALUE_COLUMNS - len(key_props) - len(conflicts))
    return {
        "id": _render_template(template, rows[rep[0]], prefixes),
        "row_count": len(rep),
        "entity_count": distinct_ids,
        "properties": key_props + conflicts + rest[:room],
        "varying_columns": varying,
        "omitted_columns": max(0, len(rest) - room),
    }


def _scoped_candidates(
    parent_cols: Sequence[str],
    current_key: Sequence[str],
    existing: Sequence[Mapping[str, Any]],
    rows_considered: Any,
    inspection: SourceInspection | None,
) -> list[dict[str, Any]]:
    """Parent-scoped rewrites of the current key and the proven candidates.

    A superset of a unique key stays unique (rows where a parent cell is empty
    simply drop out of consideration), so no re-check is needed. Ranking
    prefers fewer measurement-valued columns, then fewer columns — so
    ``{No}/{(hkl)}`` beats ``{No}/{2theta}`` beats three-column combos.
    """
    types: dict[str, str | None] = (
        {c.name: c.inferred_type for c in inspection.columns} if inspection else {}
    )
    bases: list[Sequence[str]] = [current_key, *(c.get("columns") or [] for c in existing)]
    scored: list[tuple[tuple[bool, int, int], dict[str, Any]]] = []
    seen: set[tuple[str, ...]] = set()
    for base in bases:
        combo = [*parent_cols, *(c for c in base if c not in parent_cols)]
        canonical = tuple(sorted(combo))
        if not combo or canonical in seen:
            continue
        seen.add(canonical)
        n_meas = sum(1 for c in combo if types.get(c) in _MEASUREMENT_TYPES)
        candidate = {
            "columns": combo,
            "rows_considered": rows_considered,
            "measurement_only": n_meas == len(combo),
            "scoped": True,
        }
        scored.append(((candidate["measurement_only"], n_meas, len(combo)), candidate))
    scored.sort(key=lambda item: item[0])
    return [c for _, c in scored[:_KEY_CANDIDATES]]


def _inject_scope_risks(
    annotations: dict[str, Any],
    map_sources: Mapping[str, str],
    inspections: Mapping[str, tuple[Path, SourceInspection]],
) -> None:
    """Cross-map pass: append-safety of row-level IDs (the citation question).

    When exactly one map of a source collapses to a SINGLETON (the file-scoped
    metadata entity — one reference card, one run header), its key columns are
    that file's namespace. A row-level map on the same source whose key does
    not include them is unique only within THIS file: appending the next file
    can mint the same ID for a different parent's row. Flag it as a reference
    risk and prepend the parent key to every proven candidate. Two singletons
    would make the parent ambiguous — stay silent rather than guess.
    """
    by_source: dict[str, list[str]] = defaultdict(list)
    for name, src in map_sources.items():
        by_source[src].append(name)
    for src, names in by_source.items():
        singletons = [
            n
            for n in names
            if annotations[n].get("collapse_kind") == "singleton"
            and annotations[n].get("key_columns")
        ]
        if len(singletons) != 1:
            continue
        parent = singletons[0]
        parent_cols = list(annotations[parent]["key_columns"])
        parent_classes = [c["curie"] for c in annotations[parent].get("expanded_classes") or []]
        entry = inspections.get(src)
        inspection = entry[1] if entry else None
        for name in names:
            if name == parent:
                continue
            ann = annotations[name]
            if not ann.get("checkable") or ann.get("collapse_kind") != "unique":
                continue
            key_cols = list(ann.get("key_columns") or [])
            if not key_cols or set(parent_cols) <= set(key_cols):
                continue
            ann.setdefault("reference_risks", []).append(
                {
                    "kind": "scope-missing",
                    "parent_map": parent,
                    "parent_columns": parent_cols,
                    "parent_classes": parent_classes,
                }
            )
            ann["key_candidates"] = _scoped_candidates(
                parent_cols,
                key_cols,
                ann.get("key_candidates") or [],
                ann.get("rows_considered"),
                inspection,
            )


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
        "expanded_classes": [{"curie": c, "iri": _expand_curie(c, prefixes)} for c in classes],
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
    # K7 (kantan-mode ADR): a key built ONLY from measurement-valued columns can
    # pass the uniqueness check by accident (real dogfood: 13 rows of
    # 3.636740E+1-style values happened to be distinct). The green band alone
    # would let a semantically wrong ID through — flag it so the gate shows an
    # amber caution AND, unlike the plain-unique case, still offers the proven
    # safer key candidates as one-click fixes.
    caution = bool(report.is_unique) and _measurement_only(inspection, key)
    # ZEM-shape naming trap: a measurement-only key whose CLASS is named after
    # one of those numeric columns (the row identity mislabeled as one of its
    # measurements). Computed for any measurement-only key, unique or not.
    class_caution = (
        _class_numeric_key_caution(inspection, key, classes)
        if _measurement_only(inspection, key)
        else []
    )
    kind = _collapse_kind(report)
    ann.update(
        {
            "checkable": True,
            "class_numeric_key_caution": class_caution,
            "rows_considered": report.total_rows_considered,
            "total_rows": len(rows),
            "distinct_ids": report.distinct_tuples,
            "colliding_rows": report.total_rows_considered - report.distinct_tuples,
            "is_unique": report.is_unique,
            "collapse_kind": kind,
            "key_measurement_caution": caution,
            # Citation-consequence risks (machine-readable; copy lives in the
            # UI): a measured-value ID stops pointing at its peak the moment
            # the value is corrected. The cross-map ``scope-missing`` risk is
            # appended by _inject_scope_risks after all maps are annotated.
            "reference_risks": (
                [{"kind": "measurement-id", "columns": list(key)}] if caution else []
            ),
            "collision_examples": []
            if report.is_unique
            else _collision_examples(rows, key, first_data_line),
            "id_previews": [
                _render_template(str(template), row, prefixes) for row in rows[:_PREVIEW_ROWS]
            ],
            "entity_preview": _entity_preview(
                rows,
                key,
                str(template),
                prefixes,
                inspection,
                first_data_line,
                kind,
                report.distinct_tuples,
            ),
            "key_candidates": []
            if (report.is_unique and not caution)
            else _key_candidates(inspection, key),
        }
    )
    return ann


def annotate_skeleton(
    skeleton: Mapping[str, Any],
    paths: Sequence[Path | str],
    *,
    dialects: Mapping[str, Any] | None = None,
    record_path: str | None = None,
    iri_base: str | None = None,
) -> dict[str, Any]:
    """Deterministic per-map evidence for the skeleton gate.

    Returns ``{"maps": {map_name: annotation}}``. Re-inspects the sources with
    the SAME dialect overrides the skeleton run used, so column names match what
    the AI saw. Designed to be best-effort at the call site: raise nothing the
    caller can avoid by construction; per-map problems degrade to
    ``checkable: false`` with a machine-readable ``reason``.
    """
    resolved = [Path(p) for p in paths]
    inspections, _fks = inspect_source_set(resolved, record_path=record_path, dialects=dialects)
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
    map_sources: dict[str, str] = {}
    for map_entry in skeleton.get("maps") or []:
        if not isinstance(map_entry, Mapping):
            continue
        name = str(map_entry.get("name") or f"map-{len(annotations) + 1}")
        source = str(map_entry.get("source") or "")
        path, inspection = by_name.get(Path(source).name, (None, None))
        map_sources[name] = Path(source).name
        annotations[name] = _annotate_map(map_entry, prefixes, path, inspection)
    # Second pass — needs every map's collapse verdict: the singleton map's
    # key is the file's namespace, and row-level keys missing it are unique
    # only until the next file is appended (see _inject_scope_risks).
    _inject_scope_risks(annotations, map_sources, by_name)
    # Skeleton-level (not per-map): namespaces minted on a placeholder domain
    # (ADR instance-iri-base.md). The design loop would catch this after the
    # (paid, minutes-long) continue run — the gate shows it in milliseconds,
    # and an edit of the prefix clears it via /skeleton/validate like any key fix.
    placeholder = [
        {"prefix": name, "iri": iri}
        for name, iri in prefixes.items()
        if placeholder_prefix_issue(name, iri)
    ]
    # Which prefixes are THIS dataset's minted pair (vs reused vocabularies),
    # under which base, operator-configured or not — the gate renders "dataset
    # name" as the one editable naming judgment from this (kantan ADR K13).
    return {
        "maps": annotations,
        "placeholder_prefixes": placeholder,
        "dataset_namespace": dataset_namespace_info(prefixes, iri_base),
    }
