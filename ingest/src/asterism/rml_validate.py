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
3. An ``rml:source`` names a file the data dir does **not** have (e.g. an AI-invented
   ``<name>_preprocessed.csv`` when the real file is the plain ``<name>.csv``) → the
   source is skipped by the column check (no header to read) and then Morph-KGC's
   pandas reader dies with a ``FileNotFoundError`` deep inside the engine.

This module catches all three classes **up front**, COLLECTS ALL issues (it never
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

from asterism.dialect import (
    DEFAULT_DIALECT,
    LEGACY_SUFFIXES,
    DialectAnnotationError,
    SourceDialect,
    dialect_rows,
    dialects_from_mapping,
    is_default,
    safe_column,
)

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
# rmlf:inputValueMap / constant — the shape the IR compiler emits for a
# transformed template (fn:template with p_template / p_fieldN inputs).
_INPUT_VALUE_MAP_PREDS = (_RMLF + "inputValueMap", _FNML_OLD + "inputValueMap")
_CONSTANT_PREDS = (
    _RMLF + "constant",
    "http://www.w3.org/ns/r2rml#constant",
    _FNML_OLD + "constant",
)
# fn:template's inputs: the pattern constant and its numbered field parameters.
_P_FIELD_RE = re.compile(r"/p_field(\d+)$")
# A {N} slot inside the fn:template pattern constant (`…/sample/{1}`).
_TEMPLATE_SLOT = re.compile(r"\{(\d+)\}")

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
# Legacy instrument suffixes are tabular too (extension-based normalization, ADR
# source-dialect.md), so their columns are checked even with a default dialect.
_TABULAR_SUFFIXES = frozenset({".csv", ".tsv"}) | LEGACY_SUFFIXES


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


def read_csv_header(path: Path | str, dialect: SourceDialect | None = None) -> list[str]:
    """The column names of a delimited file's header row, read BOM-safely.

    Opened ``utf-8-sig`` so a leading UTF-8 BOM is stripped from the first column
    name (matching the inspector / ``strip_bom_sources``), and parsed with
    :mod:`csv` so a quoted delimiter in a header does not split a column. A
    ``.tsv`` is parsed tab-delimited; everything else comma-delimited. Returns an
    empty list for an absent or empty file (the caller treats "no header" as
    "cannot check this source" — it does not invent a missing-column issue).

    With a non-default ``dialect`` (ADR ``source-dialect.md``) the header row is
    read through the SAME rules the substrate normalizes with (encoding /
    skip_rows / delimiter / collapse via :func:`asterism.dialect.dialect_rows`),
    with Morph-KGC's reserved columns renamed (:func:`asterism.dialect.
    safe_column`) — exactly the columns of the normalized copy Morph-KGC reads.
    A legacy-suffix file (``.txt``/``.dat``/``.asc``) reads through the DEFAULT
    dialect rules even when none is pinned (extension-based normalization). A
    file the encoding cannot decode returns ``[]`` ("cannot check" here; the
    ingest boundary raises the loud, structured error).
    """
    p = Path(path)
    if not p.exists():
        return []
    effective = dialect if dialect is not None and not is_default(dialect) else None
    if effective is None and p.suffix.lower() in LEGACY_SUFFIXES:
        effective = DEFAULT_DIALECT
    if effective is not None:
        rows = dialect_rows(p, effective)
        try:
            first = next(rows, None)
        except UnicodeDecodeError:
            return []
        finally:
            rows.close()
        return [safe_column(c) for c in first] if first else []
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


def _check_sources(graph, csv_dir: Path) -> list[str]:
    """Flag every ``rml:source`` whose resolved file is absent on disk.

    Runs on the *prepared* RML, where the substrate has already rewritten sources
    to absolute paths (a real CSV, a tabularized-JSON work-dir copy, a BOM-stripped
    copy, …) — every one of which exists. A source left pointing at a non-existent
    file is therefore exactly an AI mistake: a renamed / invented filename (an
    ``rml:source`` the inspection never listed). The column check skips it silently
    (no header to read), so without this check it surfaces only as a cryptic
    ``FileNotFoundError`` inside Morph-KGC. A "did you mean" (against the real files
    in the data dir) is appended when a close real filename exists, otherwise the
    available files are listed so the AI can pick the right one.
    """
    import rdflib

    issues: list[str] = []
    sub_pred = rdflib.URIRef
    try:
        available = sorted(p.name for p in csv_dir.iterdir() if p.is_file())
    except OSError:
        available = []
    seen: set[str] = set()
    for s_pred in _SOURCE_PREDS:
        for src in graph.objects(None, sub_pred(s_pred)):
            raw = str(src).strip()
            if not raw:
                continue
            path = Path(raw)
            if not path.is_absolute():
                path = csv_dir / raw
            if path.exists():
                continue
            name = path.name
            # A ``.csv`` reference backed by a sibling JSON is legal: ingest
            # tabularizes it on the fly (``substrate.tabularize_json_sources``).
            # Without this alias the design-time check and the compiler demand
            # contradictory names for a JSON source and the repair loop cannot
            # converge (live 2026-09-01).
            if path.suffix.lower() == ".csv" and any(
                path.with_suffix(sfx).exists() for sfx in (".json", ".geojson")
            ):
                continue
            if name in seen:
                continue
            seen.add(name)
            suggestion = difflib.get_close_matches(name, available, n=_SUGGEST_N, cutoff=0.6)
            if suggestion:
                hint = f" Did you mean: {', '.join(suggestion)}?"
            elif available:
                hint = f" Available files: {', '.join(available)}."
            else:
                hint = ""
            issues.append(
                f"source file {name!r} referenced by rml:source does not exist; "
                f"use a source filename exactly as the inspection lists it (do not "
                f"rename or add a suffix).{hint}"
            )
    return issues


def _check_dialects(graph) -> list[str]:
    """Flag ``ast:`` dialect annotation values outside the pinned contract.

    User-authored RML (the raw-RML save path) reaches design validation
    unvetted; an out-of-contract value would otherwise only crash at the ingest
    boundary. The message is :class:`asterism.dialect.DialectAnnotationError`'s
    own (it names the offending source and value).
    """
    try:
        dialects_from_mapping(graph)
    except DialectAnnotationError as exc:
        return [str(exc)]
    return []


def _mapping_dialects(graph) -> dict[str, SourceDialect]:
    """``dialects_from_mapping`` degraded to "cannot check" on a bad annotation
    (``_check_dialects`` reports it; header-based checks just skip)."""
    try:
        return dialects_from_mapping(graph)
    except DialectAnnotationError:
        return {}


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
    # Pinned source dialects (un-prepared RML only: the substrate strips the
    # annotations and rewrites the sources before validation, so prepared RML
    # yields an empty map and the plain header read below).
    dialects = _mapping_dialects(graph)
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
            header = read_csv_header(path, dialects.get(path.name))
            if not header and path.suffix.lower() == ".csv" and not path.exists():
                # A ``.csv`` reference backed by a sibling JSON (ingest tabularizes
                # it on the fly). Derive the tabularized header HERE so phantom
                # column names are caught at DESIGN time with a did-you-mean —
                # skipping let the model invent camelCase columns that only
                # exploded at materialize (live 2026-09-02, periodic-table JSON).
                for sfx in (".json", ".geojson"):
                    json_src = path.with_suffix(sfx)
                    if json_src.exists():
                        import tempfile

                        from asterism.tabularize import tabularize_json_to_csv

                        try:
                            with tempfile.TemporaryDirectory() as td:
                                header = tabularize_json_to_csv(
                                    json_src, Path(td) / path.name
                                )
                        except Exception:
                            header = []  # unreadable JSON: leave to the engine
                        break
            headers[key] = header
        return headers[key] or None

    sub_pred = rdflib.URIRef
    # Pass 1: resolve every tabular TriplesMap's (source, columns, references),
    # so pass 2 can also answer "does this column exist in ANOTHER source?" —
    # the signature of an entity link declared on the wrong side.
    tm_rows: list[tuple[str, set[str], set[str], list[str]]] = []
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
        src_path = Path(source_literal.strip())
        if src_path.suffix.lower() not in _TABULAR_SUFFIXES and src_path.name not in dialects:
            # JSON (JSONPath) / XML (XPath) sources have no flat header to check a
            # reference against; leave them to the engine + safety gate. A dialected
            # source (e.g. a .txt instrument export) IS tabular once normalized.
            continue
        columns = header_for(source_literal)
        if columns is None:
            continue  # unreadable / empty header — cannot check this source
        # Collect every column this TriplesMap references (reachable blank nodes).
        referenced: set[str] = set()
        for node in _reachable_nodes(graph, tm):
            for ref_pred in _REFERENCE_PREDS:
                for ref in graph.objects(node, sub_pred(ref_pred)):
                    referenced.add(str(ref))
            for tpl_pred in _TEMPLATE_PREDS:
                for tpl in graph.objects(node, sub_pred(tpl_pred)):
                    referenced |= _template_columns(str(tpl))
        tm_rows.append((Path(source_literal).name, set(columns), referenced, columns))

    carriers: dict[str, set[str]] = {}
    for src_name, col_set, _refs, _cols in tm_rows:
        for col in col_set:
            carriers.setdefault(col, set()).add(src_name)

    for src_name, col_set, referenced, columns in tm_rows:
        for col in sorted(referenced):
            if col in col_set:
                continue
            suggestion = difflib.get_close_matches(col, columns, n=_SUGGEST_N, cutoff=0.6)
            hint = f" Did you mean: {', '.join(suggestion)}?" if suggestion else ""
            others = sorted(carriers.get(col, set()) - {src_name})
            if others:
                # Observed live: the AI declares Paper -> Sample on the PAPER map
                # using the child's key, which the parent table never carries. The
                # fix is directional knowledge, so say it explicitly.
                hint += (
                    f" NOTE: {col!r} DOES exist in {', '.join(others)} — if this is "
                    "an entity link, declare it on the TriplesMap whose source "
                    f"carries the key (i.e. {others[0]}), using the other entity's "
                    f"subject IRI template as the object; {src_name} does not have "
                    "that key (a parent table never carries its children's keys, "
                    "and SPARQL can traverse the link in both directions anyway)."
                )
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


# fn:lookup's table constant — a seed table name under asterism/tables/.
_P_TABLE_SUFFIX = "/p_table"


def _lookup_fn_iri() -> str | None:
    """The registered IRI of the ``lookup`` Tier 0 function, or None if absent."""
    for fun_iri, meta in _required_param_iris().items():
        if meta["name"] == "lookup":
            return fun_iri
    return None


def _check_lookup_tables(graph) -> list[str]:
    """Flag a ``fn:lookup`` whose ``p_table`` constant names a table we do not ship.

    A table name is a *constant* in the mapping, so a wrong one is never data — it
    is a typo (``"booleans"`` for ``"bool"``) or a packaging gap. Caught here it is
    a design issue with a "did you mean"; caught at runtime it is a
    :class:`~asterism.primitives.LookupTableUnavailableError` that aborts the
    materialization. Both beat the old behaviour, where an unloadable table
    answered ``""`` for every row and the column vanished from a "successful" run.
    """
    import rdflib

    fun_iri = _lookup_fn_iri()
    if fun_iri is None:  # pragma: no cover - lookup is a permanent Tier 0 entry
        return []
    from asterism.primitives import available_tables

    available = available_tables()
    uri = rdflib.URIRef
    issues: list[str] = []
    seen: set[str] = set()

    for fe in _function_executions(graph):
        if not any(
            str(f) == fun_iri for f_pred in _FUNCTION_PREDS for f in graph.objects(fe, uri(f_pred))
        ):
            continue
        for in_pred in _INPUT_PREDS:
            for inp in graph.objects(fe, uri(in_pred)):
                if not any(
                    str(prm).endswith(_P_TABLE_SUFFIX)
                    for p_pred in _PARAMETER_PREDS
                    for prm in graph.objects(inp, uri(p_pred))
                ):
                    continue
                # The constant sits on the inputValueMap (IR-compiler shape); older
                # hand-written RML puts it straight on the input node.
                holders = [inp]
                for ivm_pred in _INPUT_VALUE_MAP_PREDS:
                    holders.extend(graph.objects(inp, uri(ivm_pred)))
                for holder in holders:
                    for c_pred in _CONSTANT_PREDS:
                        for const in graph.objects(holder, uri(c_pred)):
                            name = str(const)
                            if name in available or name in seen:
                                continue
                            seen.add(name)
                            suggestion = difflib.get_close_matches(
                                name, available, n=_SUGGEST_N, cutoff=0.6
                            )
                            if suggestion:
                                hint = f" Did you mean: {', '.join(suggestion)}?"
                            elif available:
                                hint = f" Available tables: {', '.join(available)}."
                            else:  # pragma: no cover - tables ship as package data
                                hint = ""
                            issues.append(
                                f"lookup references seed table {name!r}, which this install "
                                f"does not have; the table name is a constant, so every row "
                                f"would lose its value.{hint}"
                            )
    return issues


# --- graph traversal --------------------------------------------------------


_R2RML_CONSTANT = "http://www.w3.org/ns/r2rml#constant"
_PLACEHOLDER_RE = re.compile(r"\{([^{}]+)\}")


def _check_constant_placeholders(graph) -> list[str]:
    """Flag ``rr:constant`` literals that contain ``{placeholder}`` text.

    A constant is NEVER template-expanded, so a placeholder inside one either
    reaches the store as literal garbage or (Morph-KGC's actual behaviour,
    observed live) gets treated as a template reference and crashes ingest with
    a pandas ``KeyError`` on a column that does not exist — the AI invented
    ``{ingest_run_id}`` for a provenance object. Deterministic, and the message
    is the fix: ``{__run_id__}`` is the ONLY runtime placeholder the engine
    substitutes; column values belong in ``rr:template`` / ``rml:reference``.
    """
    import rdflib

    issues: list[str] = []
    seen: set[str] = set()
    for const in graph.objects(None, rdflib.URIRef(_R2RML_CONSTANT)):
        if not isinstance(const, rdflib.Literal):
            continue
        text = str(const)
        names = sorted({n for n in _PLACEHOLDER_RE.findall(text) if n != "__run_id__"})
        for name in names:
            key = f"{text}::{name}"
            if key in seen:
                continue
            seen.add(key)
            issues.append(
                f"rr:constant \"{text}\" contains the placeholder '{{{name}}}' — a "
                "constant is never template-expanded, so this crashes ingest. If you "
                "meant the engine's ingest run id, the ONLY runtime placeholder is "
                "'{__run_id__}' (substituted automatically). If you meant a column "
                "value, use rr:template (for an IRI) or rml:reference (for a literal) "
                "instead of rr:constant."
            )
    return issues


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
    """Validate prepared RML against the real source files, CSV columns + Tier 0 signatures.

    Collects ALL missing-source, column-reference and function-parameter issues and raises a single
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
    issues = (
        _check_dialects(graph)
        + _check_sources(graph, base)
        + _check_columns(graph, base)
        + _check_function_params(graph)
        + _check_lookup_tables(graph)
        + _check_constant_placeholders(graph)
    )
    if issues:
        raise RmlValidationError(issues)


# ---------------------------------------------------------------------------
# Design advisories (non-blocking, cross-cutting quality checks)
# ---------------------------------------------------------------------------

_R2RML = "http://www.w3.org/ns/r2rml#"

# rr:termType also exists at the new RML namespace (mirrors asterism.rml_summary).
_TERM_TYPE_PREDS = (_R2RML + "termType", _RMLF + "termType")

# The duplicate-column adjudication reads real rows; cap the scan so a 233k-row
# source costs bounded time per validation round (functional dependency observed
# over the first N rows is evidence enough for a non-blocking advisory).
_ADJUDICATION_ROW_CAP = 20000


def _tm_source_name(graph, tm) -> str | None:
    """The file name of a TriplesMap's logical source, or None."""
    import rdflib

    uri = rdflib.URIRef
    for ls_pred in _LOGICAL_SOURCE_PREDS:
        for ls in graph.objects(tm, uri(ls_pred)):
            for s_pred in _SOURCE_PREDS:
                for src in graph.objects(ls, uri(s_pred)):
                    return Path(str(src)).name
    return None


def _tm_label(graph, tm) -> str:
    """A human label for a TriplesMap: its rr:class local name, else its IRI tail."""
    import rdflib

    uri = rdflib.URIRef
    for sm in graph.objects(tm, uri(_R2RML + "subjectMap")):
        for cls in graph.objects(sm, uri(_R2RML + "class")):
            return _local_name(str(cls))
    if isinstance(tm, rdflib.BNode):
        return "(anonymous map)"
    return _local_name(str(tm))


def _tm_node_name(graph, tm) -> str:
    """This TriplesMap's own node name (``<#PeakMap>`` -> ``PeakMap``), or ``""``
    for an anonymous (blank-node) map.

    The HANDLE, as opposed to :func:`_tm_label`'s reading name: the api accepts
    it for a human column-ownership decision, and unlike a class local name it
    is unique inside one mapping. Legacy hand-written RML may carry blank-node
    TriplesMaps; those have no handle at all, so a finding that names one is
    reported without an actionable owner choice rather than with a name that
    points at nothing.
    """
    import rdflib

    if isinstance(tm, rdflib.BNode):
        return ""
    return _local_name(str(tm))


def _input_source_column(graph, node) -> str | None:
    """The source column feeding an input-value map node: a direct
    ``rml:reference``, or — through a nested transform ``functionExecution`` —
    the first reference reachable below it (constants are not columns)."""
    import rdflib

    uri = rdflib.URIRef
    for rp in _REFERENCE_PREDS:
        for r in graph.objects(node, uri(rp)):
            return str(r)
    for fe_pred in _FUNCTION_EXECUTION_PREDS:
        for fe in graph.objects(node, uri(fe_pred)):
            for in_pred in _INPUT_PREDS:
                for inp in graph.objects(fe, uri(in_pred)):
                    for ivm_pred in _INPUT_VALUE_MAP_PREDS:
                        for ivm in graph.objects(inp, uri(ivm_pred)):
                            col = _input_source_column(graph, ivm)
                            if col is not None:
                                return col
    return None


def _effective_template(graph, term_map) -> str | None:
    """The term map's IRI template with every placeholder naming its SOURCE column.

    A plain ``rr:template`` is returned as-is. A term map the IR compiler wrapped
    for a transform — ``fn:template`` with a ``p_template`` pattern constant
    (``…/sample/{1}``) and numbered ``p_fieldN`` inputs whose value maps are the
    (possibly transform-nested) source columns — is folded back to the SAME
    ``…/{column}`` shape by substituting each ``{N}`` slot with its field's
    underlying column. The nested transform is deliberately looked THROUGH: a
    transform changes the value, not which entity the template mints, and the
    connectivity check below must not report two maps as disconnected merely
    because one side's link carries a transform (observed live: six AI-repair
    rounds looping on a mapping whose links were present but transformed, ZEM x
    gpt-oss 2026-07-23). An unresolvable slot yields None — no claim is better
    than a wrong one."""
    import rdflib

    uri = rdflib.URIRef
    for tp in _TEMPLATE_PREDS:
        for t in graph.objects(term_map, uri(tp)):
            return str(t)
    for fe_pred in _FUNCTION_EXECUTION_PREDS:
        for fe in graph.objects(term_map, uri(fe_pred)):
            fun_local: str | None = None
            for f_pred in _FUNCTION_PREDS:
                for f in graph.objects(fe, uri(f_pred)):
                    fun_local = _local_name(str(f))
            if fun_local != "template":
                continue
            pattern: str | None = None
            fields: dict[int, str] = {}
            for in_pred in _INPUT_PREDS:
                for inp in graph.objects(fe, uri(in_pred)):
                    param: str | None = None
                    for p_pred in _PARAMETER_PREDS:
                        for p in graph.objects(inp, uri(p_pred)):
                            param = str(p)
                    if param is None:
                        continue
                    for ivm_pred in _INPUT_VALUE_MAP_PREDS:
                        for ivm in graph.objects(inp, uri(ivm_pred)):
                            if param.endswith("/p_template"):
                                for cp in _CONSTANT_PREDS:
                                    for c in graph.objects(ivm, uri(cp)):
                                        pattern = str(c)
                            else:
                                m = _P_FIELD_RE.search(param)
                                if m:
                                    col = _input_source_column(graph, ivm)
                                    if col is not None:
                                        fields[int(m.group(1))] = col
            if pattern is None or not fields:
                continue
            slots = _TEMPLATE_SLOT.findall(pattern)
            if not slots or any(int(n) not in fields for n in slots):
                continue
            return _TEMPLATE_SLOT.sub(
                lambda m, _f=fields: "{" + _f[int(m.group(1))] + "}", pattern
            )
    return None


def _connectivity_advisories(graph, headers: dict[str, list[str]] | None = None) -> list[str]:
    """Flag a mapping whose entities form DISCONNECTED groups.

    An AI-designed mapping frequently transcribes each source table into its own
    entity but forgets the object properties that JOIN them (observed live: a
    233k-curve dataset whose measurement entity had no edge to its sample entity,
    making "highest ZT per material" structurally unanswerable). The check is
    schema-agnostic graph shape only: two TriplesMaps are connected when one's
    object map joins the other (``rr:parentTriplesMap``) or reuses the other's
    subject IRI template; maps minting the same subject template are the same
    entity. Templates are compared in their EFFECTIVE form
    (:func:`_effective_template`), so a transformed subject or link — compiled to
    ``fn:template`` instead of a plain ``rr:template`` — still matches its plain
    or transformed counterpart. One connected component -> no advisory.
    """
    import rdflib

    uri = rdflib.URIRef
    tms = list(_triples_map_subjects(graph))
    if len(tms) < 2:
        return []
    subj_tpl: dict = {}
    for tm in tms:
        for sm in graph.objects(tm, uri(_R2RML + "subjectMap")):
            tpl = _effective_template(graph, sm)
            if tpl is not None:
                subj_tpl[tm] = tpl

    index = {tm: i for i, tm in enumerate(tms)}
    parent = list(range(len(tms)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for tm in tms:
        for pom in graph.objects(tm, uri(_R2RML + "predicateObjectMap")):
            for om in graph.objects(pom, uri(_R2RML + "objectMap")):
                for ptm in graph.objects(om, uri(_R2RML + "parentTriplesMap")):
                    if ptm in index:
                        union(index[tm], index[ptm])
                otpl = _effective_template(graph, om)
                if otpl is not None:
                    for other, stpl in subj_tpl.items():
                        if other is not tm and stpl == otpl:
                            union(index[tm], index[other])
    by_template: dict[str, list] = {}
    for tm, tpl in subj_tpl.items():
        by_template.setdefault(tpl, []).append(tm)
    for group in by_template.values():
        for other in group[1:]:
            union(index[group[0]], index[other])

    components: dict[int, list] = {}
    for tm in tms:
        components.setdefault(find(index[tm]), []).append(tm)
    if len(components) <= 1:
        return []
    groups = sorted(
        " + ".join(sorted({_tm_label(graph, tm) for tm in members}))
        for members in components.values()
    )
    message = (
        f"the mapping's {len(tms)} entities split into {len(components)} DISCONNECTED "
        "groups: " + "  |  ".join(groups) + ". Entities that share a source key should "
        "be LINKED with an object property (an rr:parentTriplesMap join, or reusing the "
        "linked entity's subject IRI template as the object) — disconnected entities "
        "cannot answer any cross-entity question (e.g. ranking a measured value by the "
        "material it belongs to). Do NOT fix this by deleting references — ADD the "
        "missing link on the correct side."
    )
    # With the real headers we can name the join keys — turning "link them" into
    # a work order. Observed live: without this the corrective loop oscillates
    # (deletes the bad-side link to silence the column error, then trips this
    # advisory, then re-adds the link on the wrong side again).
    if headers:
        comp_sources: list[set[str]] = []
        for members in components.values():
            srcs: set[str] = set()
            for tm in members:
                src = _tm_source_name(graph, tm)
                if src:
                    srcs.add(src)
            comp_sources.append(srcs)
        pairs: list[str] = []
        seen_pairs: set[tuple[str, str]] = set()
        for i in range(len(comp_sources)):
            for j in range(i + 1, len(comp_sources)):
                for a in sorted(comp_sources[i]):
                    for b in sorted(comp_sources[j]):
                        lo, hi = (a, b) if a <= b else (b, a)
                        if (lo, hi) in seen_pairs:
                            continue
                        seen_pairs.add((lo, hi))
                        shared = sorted(set(headers.get(a, ())) & set(headers.get(b, ())))
                        if shared:
                            pairs.append(f"{lo} <-> {hi} share column(s): {', '.join(shared[:8])}")
        if pairs:
            message += (
                " LINK-KEY CANDIDATES (computed from the real source headers): "
                + "; ".join(pairs)
                + ". Declare each link on the CHILD map (the source that CARRIES the "
                "key), with an object rr:template that reuses the parent's subject "
                "IRI template VERBATIM (byte-identical), so the IRIs actually join."
            )
    return [message]


def _source_table(
    path: Path | str,
    dialect: SourceDialect | None = None,
    cap: int = _ADJUDICATION_ROW_CAP,
) -> tuple[list[str], list[list[str]]]:
    """Header + up to ``cap`` data rows of a tabular source.

    Same effective-dialect resolution as :func:`read_csv_header` (pinned dialect
    wins; a legacy-suffix file reads through the default rules) so the rows are
    exactly the ones Morph-KGC will see after normalization. Returns
    ``([], [])`` for an absent/undecodable file — the caller degrades to the
    un-adjudicated advisory, never a wrong claim.
    """
    p = Path(path)
    if not p.exists():
        return [], []
    effective = dialect if dialect is not None and not is_default(dialect) else None
    if effective is None and p.suffix.lower() in LEGACY_SUFFIXES:
        effective = DEFAULT_DIALECT
    rows: list[list[str]] = []
    try:
        if effective is not None:
            it = dialect_rows(p, effective)
            try:
                first = next(it, None)
                if not first:
                    return [], []
                header = [safe_column(c) for c in first]
                for row in it:
                    rows.append(row)
                    if len(rows) >= cap:
                        break
            finally:
                it.close()
            return header, rows
        delimiter = "\t" if p.suffix.lower() == ".tsv" else ","
        with p.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.reader(fh, delimiter=delimiter)
            first = next(reader, None)
            if not first:
                return [], []
            header = [c.strip() for c in first]
            for row in reader:
                rows.append(row)
                if len(rows) >= cap:
                    break
        return header, rows
    except (OSError, UnicodeDecodeError):
        return [], []


def _tm_subject_key_columns(graph, tm) -> frozenset[str] | None:
    """The source columns that mint this map's subject IRI.

    The effective subject template's placeholders (transforms looked through,
    like the connectivity check); a reference-valued subject is keyed by that
    column; a constant subject mints ONE fixed IRI for the whole source →
    ``frozenset()``. ``None`` when the map has no subject map at all (nothing
    to adjudicate against)."""
    import rdflib

    uri = rdflib.URIRef
    for sm in graph.objects(tm, uri(_R2RML + "subjectMap")):
        tpl = _effective_template(graph, sm)
        if tpl is not None:
            return frozenset(_template_columns(tpl))
        for rp in _REFERENCE_PREDS:
            for r in graph.objects(sm, uri(rp)):
                return frozenset({str(r)})
        return frozenset()
    return None


def _fn_source_columns(graph, fe) -> set[str]:
    """Every distinct source column feeding a ``functionExecution``'s inputs.

    Recurses through nested function executions (a transform-of-a-transform),
    so ``f(g(col))`` still counts ``col`` once. A ``rr:constant``/``fn:constant``
    input contributes nothing — it is not a column at all. Used by
    :func:`_tm_transcribed_columns` to tell "this function reshapes ONE column's
    value" (still a transcription of that column) from "this function combines
    SEVERAL columns" (a derived value, not any one column's transcription)."""
    import rdflib

    uri = rdflib.URIRef
    cols: set[str] = set()
    for in_pred in _INPUT_PREDS:
        for inp in graph.objects(fe, uri(in_pred)):
            for ivm_pred in _INPUT_VALUE_MAP_PREDS:
                for ivm in graph.objects(inp, uri(ivm_pred)):
                    found = False
                    for rp in _REFERENCE_PREDS:
                        for r in graph.objects(ivm, uri(rp)):
                            cols.add(str(r))
                            found = True
                    if found:
                        continue
                    for fe_pred in _FUNCTION_EXECUTION_PREDS:
                        for nested_fe in graph.objects(ivm, uri(fe_pred)):
                            cols |= _fn_source_columns(graph, nested_fe)
    return cols


def _tm_transcribed_columns(graph, tm) -> set[str]:
    """Columns this map TRANSCRIBES onto a literal object: a plain fact copied
    from exactly one source cell, however it got there.

    A object map is a transcription of column X when it holds:
    - a direct ``rml:reference`` to X (exactly the shape the IR compiler emits
      for ``column:``), or
    - a function pipeline whose inputs read EXACTLY ONE distinct source column
      (constants don't count as columns; nested transforms are followed) —
      ``number_clean(X)`` is still X's value, just reshaped, so it is X's
      transcription too. A function combining TWO OR MORE columns produces a
      genuinely derived value that belongs to none of its inputs alone, so it
      is excluded (an ``rr:termType rr:IRI`` object map is a link/ID, never a
      transcription, and is excluded outright)."""
    import rdflib

    uri = rdflib.URIRef
    out: set[str] = set()
    for pom in graph.objects(tm, uri(_R2RML + "predicateObjectMap")):
        for om in graph.objects(pom, uri(_R2RML + "objectMap")):
            is_iri = any(
                _local_name(str(t)) == "IRI"
                for tp in _TERM_TYPE_PREDS
                for t in graph.objects(om, uri(tp))
            )
            if is_iri:
                continue
            for rp in _REFERENCE_PREDS:
                for r in graph.objects(om, uri(rp)):
                    out.add(str(r))
            for fe_pred in _FUNCTION_EXECUTION_PREDS:
                for fe in graph.objects(om, uri(fe_pred)):
                    cols = _fn_source_columns(graph, fe)
                    if len(cols) == 1:
                        out |= cols
    return out


_NUMBER = re.compile(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


def _tm_untyped_plain_columns(graph, tm) -> set[str]:
    """Plain-literal columns this map binds with NO ``rr:datatype``.

    Same shape as :func:`_tm_transcribed_columns` (a direct reference, not an
    IRI term), minus the ones already carrying a datatype — those made their
    choice. Unlike that function, a function pipeline is excluded here even
    when it reads a single column: the datatype of a transformed value is the
    FUNCTION's business (its output type need not match its input's), so
    whether the untyped literal is numeric or not is not decidable from the
    source column alone — no claim is better than a wrong one.
    """
    import rdflib

    uri = rdflib.URIRef
    out: set[str] = set()
    for pom in graph.objects(tm, uri(_R2RML + "predicateObjectMap")):
        for om in graph.objects(pom, uri(_R2RML + "objectMap")):
            is_iri = any(
                _local_name(str(t)) == "IRI"
                for tp in _TERM_TYPE_PREDS
                for t in graph.objects(om, uri(tp))
            )
            if is_iri:
                continue
            if any(True for _ in graph.objects(om, uri(_R2RML + "datatype"))):
                continue
            for rp in _REFERENCE_PREDS:
                for r in graph.objects(om, uri(rp)):
                    out.add(str(r))
    return out


def _untyped_numeric_advisories(graph, csv_dir: Path | str | None) -> list[str]:
    """Flag a NUMERIC column bound as a plain literal with no ``rr:datatype``.

    The quietest defect in the pipeline. Every gate passes, the ingest succeeds,
    and then SPARQL compares the values as STRINGS. Observed live (XRD reference
    card, 2026-08-14): "which angle has the highest intensity?" answered 77.47°
    (intensity 9.4) instead of 40.07° (intensity 100.0) — because "9.4" sorts
    above "100.0" lexically. No error, no warning: a confident wrong answer with
    provenance attached.

    The verdict needs the real rows (a column NAME never proves a type), so an
    unreadable source stays silent. Every non-empty cell must be numeric — one
    stray "n/a" and a stamped datatype would be a lie.
    """
    if csv_dir is None:
        return []
    dialects = _mapping_dialects(graph)
    issues: list[str] = []
    for tm in _triples_map_subjects(graph):
        src = _tm_source_name(graph, tm)
        if src is None:
            continue
        columns = _tm_untyped_plain_columns(graph, tm)
        if not columns:
            continue
        header, rows = _source_table(Path(csv_dir) / src, dialects.get(src))
        if not header or not rows:
            continue
        index = {name: i for i, name in enumerate(header)}
        for col in sorted(columns):
            i = index.get(col)
            if i is None:
                continue
            values = [
                v for r in rows if (v := (r[i].strip() if i < len(r) else ""))
            ]
            if not values or not all(_NUMBER.match(v) for v in values):
                continue
            issues.append(
                f"column '{col}' holds numbers but is mapped as an untyped literal — "
                'SPARQL then compares it as TEXT ("9.4" sorts above "100.0"), so '
                "max/min/ORDER BY answer WRONGLY with no error. Add "
                "`datatype: xsd:double` (or xsd:integer) to that property row."
            )
    return sorted(set(issues))


def _duplicate_column_findings(graph, csv_dir: Path | str | None) -> list[dict]:
    """A source column transcribed onto MULTIPLE entities as a plain datatype
    property — one record per duplicated column, MACHINE-READABLE.

    Observed live (ZEM instrument export, weak model, 2026-07-23): the per-map
    stage filled BOTH the per-row measurement map and the constant-subject
    material map with the same 13 instrument columns — every reading stored
    twice, and the single material entity carrying 13 rows' worth of readings
    as multi-values. No structural gate catches it: the columns exist, the rows
    compile, T1-T9 pass. The defect is a DESIGN one: a source cell should
    become a fact on exactly one entity.

    One record per duplicated column (so the corrective loop sees partial fixes
    as progress, and ``classify``'s ``column '…'`` shape keys each issue by
    column). When the real rows are readable, the owner is ADJUDICATED
    deterministically by functional dependency on each map's subject key: among
    the maps whose subjects determine the column's value, the one minting the
    FEWEST subjects owns it (normalization — a constant-per-material dimension
    belongs to the material, not to every measurement). No dependent map, or a
    tie → the record states the defect without a verdict (no claim is better
    than a wrong one).

    Subject-KEY columns are exempt: a map carrying another entity's key column
    is how joins are declared — the connectivity advisory owns that concern.

    Each record carries its own ``text`` — the English advisory a model acts on
    — so the sentence and the structure can never drift apart. The rest is what
    a HUMAN needs to settle it (ADR column-ownership-and-growth G1 leaves the
    tie to a person, and ADR kantan K2 puts that decision in the person's tier):
    which column, which candidate maps, how many entities each mints, and which
    one the rows recommend. ``map`` is the TriplesMap handle the api's column
    decisions accept; ``""`` for an anonymous map, which is why ``actionable``
    exists.
    """
    per_source: dict[str, list[tuple[str, str, frozenset[str], set[str]]]] = {}
    dialects = _mapping_dialects(graph)
    for tm in _triples_map_subjects(graph):
        src = _tm_source_name(graph, tm)
        if src is None:
            continue
        keys = _tm_subject_key_columns(graph, tm)
        if keys is None:
            continue
        per_source.setdefault(src, []).append(
            (
                _tm_label(graph, tm),
                _tm_node_name(graph, tm),
                keys,
                _tm_transcribed_columns(graph, tm),
            )
        )

    findings: list[dict] = []
    for src in sorted(per_source):
        entries = per_source[src]
        if len(entries) < 2:
            continue
        key_cols: set[str] = set().union(*(keys for _, _, keys, _ in entries))
        col_maps: dict[str, list[int]] = {}
        for i, (_, _, _, refs) in enumerate(entries):
            for c in refs:
                if c in key_cols:
                    continue  # join carry — the connectivity advisory's concern
                col_maps.setdefault(c, []).append(i)
        dup_cols = {c: idxs for c, idxs in col_maps.items() if len(idxs) >= 2}
        if not dup_cols:
            continue

        header: list[str] = []
        rows: list[list[str]] = []
        if csv_dir is not None:
            header, rows = _source_table(Path(csv_dir) / src, dialects.get(src))
        col_index = {c: i for i, c in enumerate(header)}

        def cell(row: list[str], idx: int) -> str:
            return row[idx] if idx < len(row) else ""

        # Subjects each map mints over the real rows (1 for a constant subject);
        # None when the rows/columns are unavailable — that map cannot win.
        entity_counts: list[int | None] = []
        for _, _, keys, _ in entries:
            if not rows:
                entity_counts.append(None)
            elif not keys:
                entity_counts.append(1)
            elif any(k not in col_index for k in keys):
                entity_counts.append(None)
            else:
                kidx = [col_index[k] for k in sorted(keys)]
                entity_counts.append(
                    len({tuple(cell(r, i) for i in kidx) for r in rows})
                )

        for c in sorted(dup_cols):
            idxs = dup_cols[c]
            ordered = sorted(idxs, key=lambda i: entries[i][0])
            labels = [entries[i][0] for i in ordered]
            base = (
                f"column '{c}' is bound as a plain datatype property by "
                f"{len(idxs)} maps ({' + '.join(labels)}) — the same source cell "
                "becomes a duplicated fact on several different entities. A "
                "column belongs to exactly ONE entity: keep it on the map whose "
                "subject its value describes and DELETE the duplicate property "
                "row(s) from the other map(s)."
            )
            finding: dict = {
                "source": src,
                "column": c,
                "maps": [
                    {
                        "map": entries[i][1],
                        "label": entries[i][0],
                        "entities": entity_counts[i],
                    }
                    for i in ordered
                ],
                "owner": None,
                # A blank-node map has no handle to send back, so the choice
                # cannot be offered — the sentence still is.
                "actionable": all(entries[i][1] for i in ordered),
            }
            verdict = ""
            ci = col_index.get(c)
            if rows and ci is not None:
                dependent: list[int] = []
                for i in idxs:
                    if entity_counts[i] is None:
                        continue
                    keys = entries[i][2]
                    kidx = [col_index[k] for k in sorted(keys)] if keys else []
                    groups: dict[tuple, str] = {}
                    determined = True
                    for r in rows:
                        v = cell(r, ci).strip()
                        if not v:
                            continue
                        g = tuple(cell(r, i2) for i2 in kidx)
                        prior = groups.setdefault(g, v)
                        if prior != v:
                            determined = False
                            break
                    if determined:
                        dependent.append(i)
                if dependent:
                    best = min(dependent, key=lambda i: entity_counts[i])
                    tie = [
                        i for i in dependent if entity_counts[i] == entity_counts[best]
                    ]
                    if len(tie) == 1:
                        owner, others = entries[best][0], sorted(
                            entries[i][0] for i in idxs if i != best
                        )
                        n_distinct = len(
                            {cell(r, ci).strip() for r in rows if cell(r, ci).strip()}
                        )
                        finding["owner"] = entries[best][1]
                        finding["owner_label"] = owner
                        verdict = (
                            f" Adjudicated from the real rows: over {len(rows)} data "
                            f"rows it holds {n_distinct} distinct non-empty value(s), "
                            f"exactly one per '{owner}' subject "
                            f"({entity_counts[best]} subject(s)) — keep it ONLY on "
                            f"'{owner}' and DELETE it from: {', '.join(others)}."
                        )
            finding["text"] = base + verdict
            findings.append(finding)
    return findings


def _duplicate_column_advisories(graph, csv_dir: Path | str | None) -> list[str]:
    """The English advisory sentences of :func:`_duplicate_column_findings`."""
    return [f["text"] for f in _duplicate_column_findings(graph, csv_dir)]


def duplicate_column_findings(
    rml_ttl: str, csv_dir: Path | str | None = None
) -> list[dict]:
    """:func:`_duplicate_column_findings` from RML Turtle (``[]`` if unparseable).

    The public face of the one duplicate-column implementation: the advisory the
    model reads and the choice a human is offered come from the SAME pass, so a
    verdict the UI shows can never disagree with the sentence beside it.
    """
    import rdflib

    graph = rdflib.Graph()
    try:
        graph.parse(data=rml_ttl, format="turtle")
    except Exception:
        return []
    return _duplicate_column_findings(graph, csv_dir)


def _tm_object_maps(graph, tm):
    """Every object map of a TriplesMap (predicateObjectMap → objectMap)."""
    import rdflib

    uri = rdflib.URIRef
    for pom in graph.objects(tm, uri(_R2RML + "predicateObjectMap")):
        yield from graph.objects(pom, uri(_R2RML + "objectMap"))


def _tm_own_value_columns(graph, tm) -> set[str]:
    """Columns this map turns into a VALUE on its own subject.

    Everything a per-row entity can carry: plain references, typed literals,
    function inputs, and templates *other than* the subject template itself.
    Not counted: a parent-join, a constant, or an object template that only
    re-mints ANOTHER map's key (``xrdr:sample/{No}``) — those are links, and a
    row that carries only links is exactly what this check is looking for.
    """
    import rdflib

    uri = rdflib.URIRef
    keys = _tm_subject_key_columns(graph, tm) or frozenset()
    out: set[str] = set()
    for om in _tm_object_maps(graph, tm):
        for node in _reachable_nodes(graph, om):
            for rp in _REFERENCE_PREDS:
                for r in graph.objects(node, uri(rp)):
                    out.add(str(r))
            for tp in _TEMPLATE_PREDS:
                for tpl in graph.objects(node, uri(tp)):
                    out |= _template_columns(str(tpl))
    # A key column re-used in a link template is the join, not a value.
    return out - keys


def _cell(row: list[str], idx: int) -> str:
    return row[idx].strip() if idx < len(row) else ""


def _determined_by(
    rows: list[list[str]], col_index: dict[str, int], col: str, keys: frozenset[str]
) -> bool:
    """Does ``keys`` functionally determine ``col`` over the real rows (G1)?

    Group by the key; the column is determined iff no group holds two different
    non-empty values. An unreadable key answers ``True`` — no claim against a
    map we cannot test.
    """
    ci = col_index[col]
    kidx = [col_index[k] for k in sorted(keys) if k in col_index]
    if len(kidx) != len(keys):
        return True
    seen: dict[tuple[str, ...], str] = {}
    for r in rows:
        v = _cell(r, ci)
        if not v:
            continue
        g = tuple(_cell(r, i) for i in kidx)
        if seen.setdefault(g, v) != v:
            return False
    return True


def _tm_literal_object_count(graph, tm) -> int:
    """How many of this map's predicate-object pairs actually emit a LITERAL.

    R2RML term-type defaults decide this, not intent. An object map is an IRI
    only when it SAYS so (``rr:termType rr:IRI``) or when it carries a
    ``rr:template`` and says nothing — the template default. Everything else
    that carries a source value is a literal: a direct ``rml:reference``, a
    Tier-0 function pipeline (whose references sit inside the function's own
    triples map, so the check must look through ``fnml:functionValue`` — a
    design whose values all flow through ``trim_collapse`` emits literals and
    must not be accused), an explicit ``rr:datatype`` / ``rr:language``.
    ``rr:constant`` alone is excluded: it carries nothing from the source.
    """
    import rdflib

    uri = rdflib.URIRef
    count = 0
    for pom in graph.objects(tm, uri(_R2RML + "predicateObjectMap")):
        for om in graph.objects(pom, uri(_R2RML + "objectMap")):
            term = {
                _local_name(str(t))
                for tp in _TERM_TYPE_PREDS
                for t in graph.objects(om, uri(tp))
            }
            if "IRI" in term or "BlankNode" in term:
                continue
            on_om_template = any(
                True for tp in _TEMPLATE_PREDS for _ in graph.objects(om, uri(tp))
            )
            if on_om_template and "Literal" not in term:
                continue  # a template with no termType is an IRI (R2RML default)
            typed = any(True for _ in graph.objects(om, uri(_R2RML + "datatype"))) or any(
                True for _ in graph.objects(om, uri(_R2RML + "language"))
            )
            # Reachable so a function pipeline's inputs count as this map's value.
            carries_source = any(
                True
                for node in _reachable_nodes(graph, om)
                for rp in _REFERENCE_PREDS
                for _ in graph.objects(node, uri(rp))
            )
            if carries_source or typed or (on_om_template and "Literal" in term):
                count += 1
    return count


def _no_literal_advisories(graph, csv_dir: Path | str | None) -> list[str]:
    """A design that binds source columns but emits NO literal values.

    The failure this exists for (live 2026-08-18, XRD reference card): every one
    of 25 properties was written as ``object_template:
    .../resource/{Some Column}``, turning every measured value into an opaque
    IRI that nothing else in the graph describes. Volume, intensity, 2theta —
    all present as column references, none retrievable as a value. Every static
    gate passed (the columns exist, the functions are vetted, T1-T10 green, the
    connectivity and empty-shell advisories both silent because an object
    template COUNTS as binding its column), so the self-correction loop reported
    "0 issues" and the human spent four "AI に直してもらう" rounds rebuilding a
    design the machine had certified.

    The check is deliberately about the OUTCOME, not the shape of the mistake:
    a dataset exists to answer questions with values, so a map that reads
    columns and emits no literal cannot answer any, however it got that way.
    That makes it robust to the next failure mode as well — earlier ones
    (missing object form, invented column names) were each caught by a check
    written for their exact shape, and each was replaced by a different shape
    that slipped past.

    Reported per map, plus one whole-design line when NO map emits a value at
    all (the strongest form, and the live one). Advisory, not a hard gate: a
    legitimately link-only map exists (a pure crosswalk), so the human decides —
    but they are told.
    """
    # The claim is about the DATA ("the values in your source are unreachable"),
    # so it needs the data: without a readable source this stays silent, the same
    # posture the empty-shell check takes. Bare-mapping callers (unit fixtures,
    # a design reviewed before its source is attached) therefore see nothing.
    if csv_dir is None:
        return []
    per_map: list[tuple[str, int, int]] = []  # (label, literal count, bound columns)
    for tm in _triples_map_subjects(graph):
        own = _tm_own_value_columns(graph, tm)
        per_map.append((_tm_label(graph, tm), _tm_literal_object_count(graph, tm), len(own)))
    if not per_map:
        return []

    issues: list[str] = []
    silent = [(label, cols) for label, lits, cols in per_map if lits == 0 and cols]
    if silent and all(lits == 0 for _, lits, _ in per_map):
        issues.append(
            "this design emits NO literal values at all: every property writes an IRI "
            "(rr:template / object_template) instead of a value, so the numbers and "
            "text in the source are unreachable — no question about the data can be "
            "answered from the result. A property that records a VALUE must bind its "
            "column directly (`column: <header>`, plus `datatype: xsd:…` for numbers); "
            "reserve `object_template:` for LINKS to another map's subject, whose IRI "
            "template it must reuse verbatim."
        )
        return issues
    for label, cols in silent:
        issues.append(
            f"map '{label}' reads {cols} source column(s) but emits no literal value — "
            f"every property writes an IRI, so nothing from those columns can be read "
            f"back. Bind the value columns directly on '{label}' (`column: <header>`, "
            f"with `datatype: xsd:…` for numbers); keep `object_template:` only for a "
            f"LINK that reuses another map's subject IRI template verbatim."
        )
    return issues


def _empty_shell_advisories(graph, csv_dir: Path | str | None) -> list[str]:
    """A per-row map that mints an entity per row and gives it NO row-level value.

    Observed live (XRD reference card, 2026-08-16): the skeleton gate said
    ``sample_detail`` = "47 rows → 47 XRD-card entities, keyed {No}/{(hkl)}",
    and the human confirmed. The per-map stage then wrote ONE property on it —
    ``dcterms:isPartOf`` back to the sample — and stopped. The four columns
    that make each row a row (2theta, d, I, (hkl)) went nowhere. Every gate
    passed: the classes exist, the join resolves, T1-T10 are green, and the
    "column meanings" review shows a table for ``Material`` and NOTHING for
    ``XRD-card`` — a class with no columns has no rows to review, so its
    absence is invisible in exactly the screen meant to catch it.

    A map whose subject template has ≥1 placeholder is a per-row entity by
    declaration; if it binds no value column of its own, its rows are shells.
    Whether that is a DEFECT is decided from the data, the same way the
    duplicate-column advisory adjudicates ownership (G1): a column that varies
    across the file is a per-row value, and it is misplaced if it is bound
    nowhere (dropped) or only on a map whose key does NOT determine it (parked
    on the header card, where 47 readings collapse onto one entity as
    multi-values). Either finding names the columns and the map to put them
    on — the repair, not the absence (ADR G8's principle). No such column →
    silence: a per-row map whose key IS the whole datum is legitimate.

    Without readable rows the check degrades to the structural reading, fired
    only when a sibling map shares the source (the header+detail shape this
    defect lives in) — minimal single-map fixtures stay quiet.
    """
    dialects = _mapping_dialects(graph)
    per_source: dict[str, list[tuple[str, frozenset[str], set[str]]]] = {}
    for tm in _triples_map_subjects(graph):
        src = _tm_source_name(graph, tm)
        if src is None:
            continue
        keys = _tm_subject_key_columns(graph, tm)
        if keys is None:
            continue
        per_source.setdefault(src, []).append(
            (_tm_label(graph, tm), keys, _tm_own_value_columns(graph, tm))
        )

    issues: list[str] = []
    for src in sorted(per_source):
        entries = per_source[src]
        shells = [(label, keys) for label, keys, own in entries if keys and not own]
        if not shells:
            continue
        header: list[str] = []
        rows: list[list[str]] = []
        if csv_dir is not None:
            header, rows = _source_table(Path(csv_dir) / src, dialects.get(src))
        col_index = {c: i for i, c in enumerate(header)}

        # Where each column lives as a VALUE (own binding), and every bound name.
        value_holders: dict[str, list[tuple[str, frozenset[str]]]] = {}
        bound: set[str] = set()
        for label, keys, own in entries:
            bound |= keys | own
            for c in own:
                value_holders.setdefault(c, []).append((label, keys))

        for label, keys in shells:
            key_txt = ", ".join(sorted(keys))
            dropped: list[str] = []
            parked: list[tuple[str, str]] = []  # (column, map it sits on)
            if header and rows:
                for col in header:
                    if col in keys:
                        continue
                    ci = col_index[col]
                    values = {_cell(r, ci) for r in rows} - {""}
                    if len(values) <= 1:
                        continue  # file-scoped metadata — the header card's, by G1
                    if col not in bound:
                        dropped.append(col)
                        continue
                    for holder, holder_keys in value_holders.get(col, []):
                        if holder != label and not _determined_by(
                            rows, col_index, col, holder_keys
                        ):
                            parked.append((col, holder))
                            break
                if not dropped and not parked:
                    continue  # data shows nothing per-row was lost — a legit anchor
            elif len(entries) < 2:
                continue  # structural fallback only inside a header+detail shape

            base = (
                f"map '{label}' mints one entity per row (subject keyed by {key_txt}) "
                "but binds NO value column of its own — every entity it creates is an "
                "empty shell carrying only links, so the row's own values are recorded "
                "nowhere and cannot be queried. Add the per-row value columns as "
                "`column:` properties on THIS map (they belong to the entity the row IS, "
                "not to its parent)."
            )
            hint = ""
            if dropped:
                shown = ", ".join(dropped[:10]) + (" …" if len(dropped) > 10 else "")
                hint += (
                    f" From the real rows: {len(dropped)} column(s) vary across the file "
                    f"and are bound by no map at all — {shown}. Put them on '{label}'."
                )
            if parked:
                by_holder: dict[str, list[str]] = {}
                for col, holder in parked:
                    by_holder.setdefault(holder, []).append(col)
                for holder, cols in sorted(by_holder.items()):
                    shown = ", ".join(cols[:10]) + (" …" if len(cols) > 10 else "")
                    hint += (
                        f" From the real rows: {len(cols)} column(s) vary per row but sit "
                        f"on '{holder}', whose key does not determine them — there they "
                        f"collapse onto one entity as multi-values: {shown}. MOVE them to "
                        f"'{label}' and DELETE them from '{holder}'."
                    )
            issues.append(base + hint)
    return issues


def _source_headers(graph, csv_dir: Path | str) -> dict[str, list[str]]:
    """Header row of every tabular source file in ``csv_dir``, keyed by file name.

    A source the mapping pins a dialect for (e.g. a ``.txt`` instrument export)
    is read through that dialect — the same columns Morph-KGC will see after
    normalization; plain ``.csv`` / ``.tsv`` files are read as today. Returns
    ``{}`` when the directory is unreadable (advisories then degrade gracefully).
    """
    dialects = _mapping_dialects(graph)
    headers: dict[str, list[str]] = {}
    base = Path(csv_dir)
    try:
        for p in sorted(base.iterdir()):
            if not p.is_file():
                continue
            dialect = dialects.get(p.name)
            if dialect is None and p.suffix.lower() not in _TABULAR_SUFFIXES:
                continue
            cols = read_csv_header(p, dialect)
            if cols:
                headers[p.name] = cols
    except OSError:
        return {}
    return headers


def _unmapped_column_advisories(graph, headers: dict[str, list[str]]) -> list[str]:
    """Columns a tabular source carries that the mapping never references.

    Non-blocking by design (timestamps or bookkeeping columns are often fine to
    drop) — but an unmapped LABEL column silently amputates queryability
    (observed live: ``prop_y`` — the "what does this curve measure" column —
    was left unmapped while ``prop_x`` was mapped, so "which curves measure ZT"
    became unanswerable over 233k curves). The advisory lists the leftovers and
    tells the designer to either map them or record the exclusion in §5.
    """
    per_source: dict[str, set[str]] = {}
    for tm in _triples_map_subjects(graph):
        src = _tm_source_name(graph, tm)
        if src is None or src not in headers:
            continue
        referenced = per_source.setdefault(src, set())
        import rdflib

        uri = rdflib.URIRef
        for node in _reachable_nodes(graph, tm):
            for ref_pred in _REFERENCE_PREDS:
                for ref in graph.objects(node, uri(ref_pred)):
                    referenced.add(str(ref))
            for tpl_pred in _TEMPLATE_PREDS:
                for tpl in graph.objects(node, uri(tpl_pred)):
                    referenced |= _template_columns(str(tpl))
    issues: list[str] = []
    for src in sorted(per_source):
        unmapped = [c for c in headers[src] if c not in per_source[src]]
        if not unmapped:
            continue
        shown = ", ".join(unmapped[:10]) + (" …" if len(unmapped) > 10 else "")
        issues.append(
            f"source {src} has {len(unmapped)} column(s) the mapping never uses: "
            f"{shown}. If a column carries meaning users will ask about — "
            "especially a LABEL column that says what a value IS (a property/"
            "type/category name), an identifier, or a unit — map it: an unmapped "
            "label column makes its rows unqueryable (you cannot ask 'which rows "
            "measure X'). If the exclusion is deliberate, record it in §5 "
            "(design rationale)."
        )
    return issues


def design_advisories(rml_ttl: str, csv_dir: Path | str | None = None) -> list[str]:
    """Non-blocking design-quality advisories for a mapping (schema-agnostic).

    Unlike :func:`validate_rml_design` these are NOT ingest-blocking — a
    disconnected mapping still materializes valid RDF; it just cannot answer the
    questions the user almost certainly wants. Surfaced at materialize (advisory
    list) and fed to the design self-correction loop as fixable issues. Returns
    ``[]`` for unparseable RML (the safety gate owns that rejection).

    ``csv_dir`` (optional): the dataset's real source directory. When given,
    the connectivity advisory also enumerates the concrete JOIN-KEY candidates
    (columns shared between the disconnected groups' sources) and says which
    side must declare the link, and the duplicate-column advisory adjudicates
    which map OWNS a column bound by several maps — the difference between
    "link them" / "decide who owns it" and a work order a weak model can
    execute.
    """
    import rdflib

    graph = rdflib.Graph()
    try:
        graph.parse(data=rml_ttl, format="turtle")
    except Exception:
        return []
    headers = _source_headers(graph, csv_dir) if csv_dir is not None else {}
    return (
        _connectivity_advisories(graph, headers or None)
        + _duplicate_column_advisories(graph, csv_dir)
        + _empty_shell_advisories(graph, csv_dir)
        + _no_literal_advisories(graph, csv_dir)
        + _untyped_numeric_advisories(graph, csv_dir)
    )


def design_review_notes(rml_ttl: str, csv_dir: Path | str | None = None) -> list[str]:
    """Human-judgement review notes (NOT fed to the automatic corrective loop).

    Unlike :func:`design_advisories` (defects that should essentially always be
    fixed, e.g. disconnected entities), these are OBSERVATIONS a human should
    weigh: unmapped source columns are often fine (timestamps, bookkeeping) but
    sometimes amputate queryability (an unmapped label column). Feeding them to
    the self-correction loop would push a weak model to map noise columns until
    no-progress; surfacing them at materialize (where the human decides and can
    include them in a fix request) is the right strength.
    """
    import rdflib

    graph = rdflib.Graph()
    try:
        graph.parse(data=rml_ttl, format="turtle")
    except Exception:
        return []
    headers = _source_headers(graph, csv_dir) if csv_dir is not None else {}
    if not headers:
        return []
    return _unmapped_column_advisories(graph, headers)


# ---------------------------------------------------------------------------
# Vocabulary extraction (the closed set a dataset's RML actually maps)
# ---------------------------------------------------------------------------

# @prefix / PREFIX declarations in the RML Turtle text. We read these from the
# TEXT rather than rdflib's namespace manager because rdflib pre-binds dozens of
# well-known namespaces the mapping never declared — the oracle must list ONLY
# what the author bound.
_TTL_PREFIX = re.compile(r"(?im)^\s*@?prefix\s+([A-Za-z][\w.-]*)?\s*:\s*<([^>]+)>")


def extract_rml_vocabulary(rml_ttl: str) -> dict[str, object]:
    """The closed vocabulary a mapping materializes: prefixes + class/predicate IRIs.

    Deterministic ground truth for anything that must speak the dataset's real
    schema (e.g. an AI-drafted query tool): ``rr:class`` objects and
    ``rr:predicate`` objects ARE the terms that exist in the ingested data —
    a term outside this set matches nothing. Returns
    ``{"prefixes": {label: iri}, "terms": set[str]}``; empty structures when the
    RML is missing/unparseable (callers degrade to "no oracle" gracefully).
    """
    prefixes: dict[str, str] = {
        (m.group(1) or ""): m.group(2) for m in _TTL_PREFIX.finditer(rml_ttl or "")
    }
    terms: set[str] = set()
    if (rml_ttl or "").strip():
        import rdflib

        graph = rdflib.Graph()
        try:
            graph.parse(data=rml_ttl, format="turtle")
        except Exception:
            return {"prefixes": {}, "terms": set()}
        rr = rdflib.Namespace("http://www.w3.org/ns/r2rml#")
        for obj in graph.objects(None, rr["class"]):
            terms.add(str(obj))
        for obj in graph.objects(None, rr.predicate):
            terms.add(str(obj))
    return {"prefixes": prefixes, "terms": terms}
