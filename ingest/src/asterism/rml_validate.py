"""Design validation for declarative RML, run *before* Morph-KGC materializes it.

Why this module exists
----------------------
:mod:`asterism.rml_safety` is the *trust* boundary — it rejects RML that would
execute non-Tier-0 code or read out-of-bounds files. But a mapping can be
perfectly *safe* and still be *malformed* against the actual data + Tier 0
signatures, in ways that surface only as a cryptic Morph-KGC crash:

1. A column reference (``rml:reference "X"`` or a ``{X}`` template placeholder)
   names a column the CSV does **not** have → pandas dies with
   ``ValueError: Usecols do not match columns, columns expected but not found:
   ['X']``.
2. An FnO function execution supplies the wrong parameter IRI (e.g. ``fn:p_field1``
   for ``json_pluck`` whose registered param is ``fn:p_field``) → the required
   positional argument is unbound and the Tier 0 callable dies with
   ``TypeError: json_pluck() missing 1 required positional argument: 'field'``.

This module catches both classes **up front**, COLLECTS ALL issues (it never
stops at the first), and raises a structured :class:`RmlValidationError` whose
``issues`` list carries one human-readable, actionable message per problem. The
api maps the error to a ``422`` that carries the ``issues`` list so the UI can
render a readable bulleted list instead of a raw engine traceback.

It runs on the *prepared* RML — after ``substitute_run_id`` /
``tabularize_json_sources`` / ``sanitize_csv_sources`` / ``strip_bom_sources`` /
``absolutize_rml_sources`` — so the ``{__run_id__}`` placeholder is already gone
(never flagged as a missing column) and ``rml:source`` paths already point at the
real CSVs on disk. It only *parses* RML + reads CSV headers, so it needs neither
Morph-KGC nor any heavy dependency.
"""
from __future__ import annotations

import csv
import difflib
import inspect
import re
from pathlib import Path

# FnO vocab (the *new* RML-FNML namespace Morph-KGC uses; the substrate normalizes
# the legacy URI to this before validation, but we accept both for robustness).
_RMLF = "http://w3id.org/rml/"
_FNML_OLD = "http://semweb.mmlab.be/ns/fnml#"
# rml:reference lives at either the new RML namespace or the legacy mmlab one.
_REFERENCE_PREDS = (
    "http://w3id.org/rml/reference",
    "http://semweb.mmlab.be/ns/rml#reference",
)
# rr:template / rml:template carry {column} placeholders.
_TEMPLATE_PREDS = (
    "http://www.w3.org/ns/r2rml#template",
    "http://w3id.org/rml/template",
    "http://semweb.mmlab.be/ns/rml#template",
)
_SOURCE_PREDS = (
    "http://w3id.org/rml/source",
    "http://semweb.mmlab.be/ns/rml#source",
)
_LOGICAL_SOURCE_PREDS = (
    "http://w3id.org/rml/logicalSource",
    "http://semweb.mmlab.be/ns/rml#logicalSource",
)
# rmlf:functionExecution / rmlf:function / rmlf:input / rmlf:parameter (+ legacy).
_FUNCTION_EXECUTION_PREDS = (_RMLF + "functionExecution", _FNML_OLD + "functionExecution")
_FUNCTION_PREDS = (_RMLF + "function", _FNML_OLD + "function")
_INPUT_PREDS = (_RMLF + "input", _FNML_OLD + "input")
_PARAMETER_PREDS = (_RMLF + "parameter", _FNML_OLD + "parameter")

# A {column} reference inside a template. An escaped \{ is a literal brace, not a
# placeholder (matches the substrate's own _TEMPLATE_REF guard).
_TEMPLATE_PLACEHOLDER = re.compile(r"(?<!\\)\{([^{}]+)\}")

# How many "did you mean" suggestions to surface per missing column.
_SUGGEST_N = 3

# The column check is meaningful only for delimited tabular sources, where a
# reference / {placeholder} is a CSV column name we can check against the header.
# A JSON source (rml:reference / iterator is a JSONPath field) or an XML source (an
# XPath) has no flat header to validate against, so its references are skipped — we
# never invent a missing-column issue for a field we cannot see in a header row.
_TABULAR_SUFFIXES = frozenset({".csv", ".tsv"})


class RmlValidationError(Exception):
    """An RML mapping is malformed against the real CSVs or Tier 0 signatures.

    ``issues`` is a list of human-readable, actionable messages (one per problem;
    all problems are collected, never short-circuited at the first). The api maps
    this to a ``422`` whose body carries ``issues`` so the UI can render them.
    """

    def __init__(self, issues: list[str]):
        self.issues = list(issues)
        super().__init__("RML design validation failed:\n- " + "\n- ".join(self.issues))


# ---------------------------------------------------------------------------
# CSV header reading (BOM-safe)
# ---------------------------------------------------------------------------


def read_csv_header(path: Path | str) -> list[str]:
    """The column names of a delimited file's header row, read BOM-safely.

    Opened ``utf-8-sig`` so a leading UTF-8 BOM is stripped from the first column
    name (matching the inspector / ``strip_bom_sources``), and parsed with
    :mod:`csv` so a quoted delimiter in a header does not split a column. A
    ``.tsv`` is parsed tab-delimited; everything else comma-delimited. Returns an
    empty list for an absent or empty file (the caller treats "no header" as
    "cannot check this source" — it does not invent a missing-column issue).
    """
    p = Path(path)
    if not p.exists():
        return []
    delimiter = "\t" if p.suffix.lower() == ".tsv" else ","
    with p.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh, delimiter=delimiter)
        for row in reader:
            return [c.strip() for c in row]
    return []


# ---------------------------------------------------------------------------
# RML parsing helpers
# ---------------------------------------------------------------------------


def _template_columns(template: str) -> set[str]:
    """Column names referenced by ``{column}`` placeholders in a template string."""
    return {m.group(1) for m in _TEMPLATE_PLACEHOLDER.finditer(template)}


def _required_param_iris() -> dict[str, dict[str, object]]:
    """Map every Tier 0 function IRI → its FnO parameter metadata.

    Returns ``{fun_id: {"name": str, "accepted": set[param_iri],
    "required": set[param_iri]}}``. ``accepted`` is every parameter IRI the
    function is registered with; ``required`` is the subset whose Python argument
    has no default (the callable's required positional args — exactly what
    Morph-KGC must bind or the call raises ``TypeError``). Derived live from
    ``asterism.functions.REGISTRY`` so it is a single source of truth.
    """
    from asterism.functions import REGISTRY

    out: dict[str, dict[str, object]] = {}
    for spec in REGISTRY:
        sig = inspect.signature(spec.func)
        required_args = {
            name
            for name, p in sig.parameters.items()
            if p.default is inspect.Parameter.empty
            and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        }
        accepted = set(spec.params.values())
        required = {iri for arg, iri in spec.params.items() if arg in required_args}
        out[spec.fun_id] = {"name": spec.name, "accepted": accepted, "required": required}
    return out


def _local_name(iri: str) -> str:
    """The trailing path/fragment segment of an IRI (for readable messages)."""
    tail = iri.rsplit("#", 1)[-1]
    return tail.rsplit("/", 1)[-1] or iri


# ---------------------------------------------------------------------------
# The checks (each collects ALL its issues)
# ---------------------------------------------------------------------------


def _check_columns(graph, csv_dir: Path) -> list[str]:
    """Flag every ``rml:reference`` / ``{template}`` column absent from its source CSV.

    Each TriplesMap is checked against the header of its own logical source, so a
    column that exists in *another* source does not mask a typo in this one. A
    "did you mean" suggestion (via :func:`difflib.get_close_matches`) is appended
    when a close real column exists. A source with no readable header is skipped
    (cannot check), never reported as missing.
    """
    import rdflib

    issues: list[str] = []
    # header cache per resolved CSV path (a source is read once even if shared).
    headers: dict[str, list[str]] = {}

    def header_for(src_literal: str) -> list[str] | None:
        raw = src_literal.strip()
        if not raw:
            return None
        # The prepared RML has absolute paths (absolutize_rml_sources); a relative
        # one (e.g. a unit test passing un-prepared RML) resolves under csv_dir.
        path = Path(raw)
        if not path.is_absolute():
            path = csv_dir / raw
        key = str(path)
        if key not in headers:
            headers[key] = read_csv_header(path)
        return headers[key] or None

    sub_pred = rdflib.URIRef
    for tm in _triples_map_subjects(graph):
        # Resolve this TriplesMap's logical source -> rml:source literal.
        source_literal: str | None = None
        for ls_pred in _LOGICAL_SOURCE_PREDS:
            for ls in graph.objects(tm, sub_pred(ls_pred)):
                for s_pred in _SOURCE_PREDS:
                    for src in graph.objects(ls, sub_pred(s_pred)):
                        source_literal = str(src)
        if source_literal is None:
            # No logical source on this map (e.g. a referencing-object map). Its
            # references are validated against the parent map; skip here.
            continue
        if Path(source_literal.strip()).suffix.lower() not in _TABULAR_SUFFIXES:
            # JSON (JSONPath) / XML (XPath) sources have no flat header to check a
            # reference against; leave them to the engine + safety gate.
            continue
        columns = header_for(source_literal)
        if columns is None:
            continue  # unreadable / empty header — cannot check this source
        col_set = set(columns)
        # Collect every column this TriplesMap references (reachable blank nodes).
        referenced: set[str] = set()
        for node in _reachable_nodes(graph, tm):
            for ref_pred in _REFERENCE_PREDS:
                for ref in graph.objects(node, sub_pred(ref_pred)):
                    referenced.add(str(ref))
            for tpl_pred in _TEMPLATE_PREDS:
                for tpl in graph.objects(node, sub_pred(tpl_pred)):
                    referenced |= _template_columns(str(tpl))
        src_name = Path(source_literal).name
        for col in sorted(referenced):
            if col in col_set:
                continue
            suggestion = difflib.get_close_matches(col, columns, n=_SUGGEST_N, cutoff=0.6)
            hint = f" Did you mean: {', '.join(suggestion)}?" if suggestion else ""
            issues.append(
                f"column {col!r} referenced by the mapping is not in {src_name} "
                f"(columns: {', '.join(columns)}).{hint}"
            )
    return issues


def _check_function_params(graph) -> list[str]:
    """Flag FnO executions that supply an unaccepted param or omit a required one.

    For each ``rmlf:functionExecution``: resolve its ``rmlf:function`` IRI to a
    registered Tier 0 spec, gather the supplied ``rmlf:parameter`` IRIs, then flag
    (a) any supplied parameter the function does not accept and (b) any required
    parameter the execution did not supply. A function IRI outside the Tier 0 set
    is left to :func:`asterism.rml_safety.assert_rml_safe`, not duplicated here.
    """
    import rdflib

    issues: list[str] = []
    specs = _required_param_iris()
    sub_pred = rdflib.URIRef

    for fe in _function_executions(graph):
        fun_iri: str | None = None
        for f_pred in _FUNCTION_PREDS:
            for f in graph.objects(fe, sub_pred(f_pred)):
                fun_iri = str(f)
        if fun_iri is None or fun_iri not in specs:
            continue  # unnamed, or non-Tier-0 (rml_safety handles the latter)
        meta = specs[fun_iri]
        fn_name = str(meta["name"])
        accepted: set[str] = meta["accepted"]  # type: ignore[assignment]
        required: set[str] = meta["required"]  # type: ignore[assignment]
        supplied: set[str] = set()
        for in_pred in _INPUT_PREDS:
            for inp in graph.objects(fe, sub_pred(in_pred)):
                for p_pred in _PARAMETER_PREDS:
                    for p in graph.objects(inp, sub_pred(p_pred)):
                        supplied.add(str(p))
        for extra in sorted(supplied - accepted):
            accepts = ", ".join(sorted(_local_name(a) for a in accepted)) or "(none)"
            issues.append(
                f"{fn_name} does not accept parameter {_local_name(extra)!r}; "
                f"it accepts: {accepts}."
            )
        for missing in sorted(required - supplied):
            issues.append(
                f"{fn_name} is missing required parameter {_local_name(missing)!r}."
            )
    return issues


# --- graph traversal --------------------------------------------------------


def _triples_map_subjects(graph):
    """Every subject that has a logical source (a TriplesMap), de-duplicated."""
    import rdflib

    seen = set()
    for ls_pred in _LOGICAL_SOURCE_PREDS:
        for s in graph.subjects(rdflib.URIRef(ls_pred), None):
            if s not in seen:
                seen.add(s)
                yield s


def _function_executions(graph):
    """Every ``rmlf:functionExecution`` object node, de-duplicated."""
    import rdflib

    seen = set()
    for fe_pred in _FUNCTION_EXECUTION_PREDS:
        for o in graph.objects(None, rdflib.URIRef(fe_pred)):
            if o not in seen:
                seen.add(o)
                yield o


def _reachable_nodes(graph, root):
    """All nodes reachable from ``root`` by forward edges (BFS over object nodes).

    A TriplesMap's column references live in nested blank-node maps (subjectMap,
    predicateObjectMap → objectMap → inputValueMap …); collecting every reachable
    node lets us gather them without hard-coding the path shape. Bounded by the
    visited set, so cycles terminate.
    """
    seen = {root}
    frontier = [root]
    while frontier:
        node = frontier.pop()
        yield node
        for o in graph.objects(node, None):
            if o not in seen:
                seen.add(o)
                frontier.append(o)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def validate_rml_design(rml_ttl: str, csv_dir: Path | str) -> None:
    """Validate prepared RML against the real CSV columns + Tier 0 signatures.

    Collects ALL column-reference and function-parameter issues and raises a single
    :class:`RmlValidationError` carrying every one. Returns ``None`` when the design
    is valid. A Turtle parse error is left to :func:`asterism.rml_safety.assert_rml_safe`
    (which runs first and already fails closed on unparseable RML); if the RML is
    unparseable here we simply return without inventing design issues, since the
    safety gate has the authoritative parse-error message.
    """
    import rdflib

    graph = rdflib.Graph()
    try:
        graph.parse(data=rml_ttl, format="turtle")
    except Exception:
        return  # rml_safety owns the parse-error rejection

    base = Path(csv_dir)
    issues = _check_columns(graph, base) + _check_function_params(graph)
    if issues:
        raise RmlValidationError(issues)
