"""Mapping IR — the small declarative spec the LLM writes instead of raw RML.

Why this module exists (ADR ``docs/architecture/mapping-ir-compiler.md``)
--------------------------------------------------------------------------
Production dogfooding showed weak models cannot reproduce RML/FnO *syntax*
(invented predicates, template function calls, unparseable Turtle) while their
*semantic* judgment (column→predicate, which Tier-0 function) is mostly sound.
So propose §9 emits this IR — a table of "source → subject → property rows"
where every value is either a choice from a closed menu (function names, column
names, file names) or a small template string — and the deterministic compiler
(:mod:`asterism_step0.rml_compile`) owns ALL RML/FnO syntax.

This module is pure and dependency-light:

* :func:`parse_mapping_ir` — strict YAML → :class:`MappingIR`. Unknown fields
  are errors (an LLM invention must fail loudly, not silently drop). Collects
  ALL problems and raises one :class:`MappingIRParseError` carrying every issue
  (the self-correction loop feeds them back verbatim).
* :func:`validate_mapping_ir` — environment-aware checks (source files exist,
  columns exist with did-you-mean, functions/args match the vetted catalog).
  The environment (file list, headers, catalog) is INJECTED so the function
  stays pure; callers (design_loop / api) supply the real directory contents.

PyYAML is lazy-imported (same pattern as ``validate.py``): step0 stays
installable dependency-free, and every real caller (api, dev, CI) has it.
"""
from __future__ import annotations

import difflib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "CatalogFunction",
    "FunctionCatalog",
    "MappingIR",
    "MappingIRParseError",
    "PropertyIR",
    "SubjectIR",
    "TriplesMapIR",
    "parse_mapping_ir",
    "referenced_columns",
    "validate_mapping_ir",
]

# The placeholder shape shared with the substrate/validator: ``{column}`` where an
# escaped ``\{`` is a literal brace (matches ``asterism.substrate._TEMPLATE_REF``).
_PLACEHOLDER = re.compile(r"(?<!\\)\{([^{}]+)\}")

# The run-id placeholder is substituted by the substrate (``substitute_run_id``)
# before any header exists — it is never a CSV column, so never "missing".
_RUN_ID_PLACEHOLDER = "__run_id__"

_PREFIX_NAME = re.compile(r"^[A-Za-z][\w.-]*$")
_MAP_NAME = re.compile(r"^[A-Za-z][\w-]*$")
_CURIE = re.compile(r"^([A-Za-z][\w.-]*):(\S+)$")
_ABSOLUTE_IRI = re.compile(r"^https?://\S+$")
_LANGUAGE_TAG = re.compile(r"^[A-Za-z]{1,8}(-[A-Za-z0-9]{1,8})*$")

# Prefixes the compiler owns (RML machinery + the Tier-0 function namespace).
# The IR must not redefine them; IR terms must not live in them (functions are
# referenced by bare name, never as fn: CURIEs).
RESERVED_PREFIXES = ("rr", "rml", "ql", "rmlf", "fn")

# Builtin prefixes usable without declaration (the compiler always declares them).
BUILTIN_PREFIXES: dict[str, str] = {
    "xsd": "http://www.w3.org/2001/XMLSchema#",
}

_TABULAR_SUFFIXES = frozenset({".csv", ".tsv"})
_XML_SUFFIXES = frozenset({".xml"})

_SUGGEST_N = 3


# ---------------------------------------------------------------------------
# IR dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SubjectIR:
    """A TriplesMap's subject: exactly one of ``template`` / ``constant``."""

    template: str | None = None
    constant: str | None = None
    classes: tuple[str, ...] = ()
    transform: Mapping[str, str] = field(default_factory=dict)
    """Optional per-placeholder Tier-0 transform (e.g. ``{container_title: slug}``)
    for readable/stable IRI segments. Untransformed placeholders rely on the
    engine's R2RML-conformant percent-encoding (probed; see the ADR)."""


@dataclass(frozen=True)
class PropertyIR:
    """One predicate-object row. Exactly one object form:

    * ``column`` — direct reference (optionally through ``function``)
    * ``columns`` — multi-input function reference (requires ``function``)
    * ``object_template`` — IRI link or literal composition
    * ``constant`` — fixed value
    """

    predicate: str
    column: str | None = None
    columns: tuple[str, ...] = ()
    function: str | None = None
    args: Mapping[str, str] = field(default_factory=dict)
    object_template: str | None = None
    constant: str | None = None
    object_type: str | None = None  # "iri" | "literal" | None (contextual default)
    datatype: str | None = None
    language: str | None = None
    transform: Mapping[str, str] = field(default_factory=dict)
    fallback: bool = False


@dataclass(frozen=True)
class TriplesMapIR:
    name: str
    source: str
    subject: SubjectIR
    properties: tuple[PropertyIR, ...]
    iterator: str | None = None  # XML only


@dataclass(frozen=True)
class MappingIR:
    prefixes: Mapping[str, str]
    maps: tuple[TriplesMapIR, ...]
    version: int = 1


class MappingIRParseError(Exception):
    """The IR text is structurally invalid. ``issues`` lists every problem found
    (all collected, never short-circuited) in the IR's own vocabulary — these
    messages are fed back to the LLM verbatim by the self-correction loop."""

    def __init__(self, issues: list[str]):
        self.issues = list(issues)
        super().__init__("Mapping spec is invalid:\n- " + "\n- ".join(self.issues))


# ---------------------------------------------------------------------------
# Catalog (injected view of the vetted Tier-0 registry)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CatalogFunction:
    """One vetted Tier-0 function as the IR sees it.

    ``column_params`` are the parameters bound from source columns (IR
    ``column``/``columns``), in declaration order; ``constant_params`` are the
    parameters bound from IR ``args`` (compiled to ``rmlf:constant``). The split
    comes from ``asterism.functions.CONSTANT_PARAM_IRIS`` — the single source of
    truth next to the registry itself.
    """

    name: str
    column_params: tuple[tuple[str, str], ...]  # (python arg name, param IRI)
    constant_params: Mapping[str, str]  # python arg name -> param IRI
    required_args: frozenset[str]  # python arg names without defaults
    multivalued: bool = False

    @property
    def required_column_count(self) -> int:
        return sum(1 for name, _ in self.column_params if name in self.required_args)


class FunctionCatalog:
    """Name-indexed view over the vetted function set. Closed: lookups of
    unknown names return ``None`` — the caller turns that into an issue."""

    def __init__(self, functions: Sequence[CatalogFunction]):
        self._by_name = {f.name: f for f in functions}

    def get(self, name: str) -> CatalogFunction | None:
        return self._by_name.get(name)

    def names(self) -> list[str]:
        return sorted(self._by_name)

    def __iter__(self):
        return iter(self._by_name.values())


def catalog_from_registry() -> FunctionCatalog:
    """Build the catalog from ``asterism.functions.REGISTRY`` (single source of
    truth). Raises ``ImportError`` when the ingest package is absent — callers
    treat that as an environment failure, not a design failure."""
    import inspect as _inspect

    from asterism.functions import CONSTANT_PARAM_IRIS, MULTIVALUED_FUNCTIONS, REGISTRY

    functions: list[CatalogFunction] = []
    for spec in REGISTRY:
        sig = _inspect.signature(spec.func)
        required = frozenset(
            name
            for name, p in sig.parameters.items()
            if p.default is _inspect.Parameter.empty
            and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        )
        column_params = tuple(
            (arg, iri) for arg, iri in spec.params.items() if iri not in CONSTANT_PARAM_IRIS
        )
        constant_params = {
            arg: iri for arg, iri in spec.params.items() if iri in CONSTANT_PARAM_IRIS
        }
        functions.append(
            CatalogFunction(
                name=spec.name,
                column_params=column_params,
                constant_params=constant_params,
                required_args=required,
                multivalued=spec.name in MULTIVALUED_FUNCTIONS,
            )
        )
    return FunctionCatalog(functions)


# ---------------------------------------------------------------------------
# Parsing (strict, collect-all)
# ---------------------------------------------------------------------------


def _suggest(wrong: str, options: Sequence[str]) -> str:
    close = difflib.get_close_matches(wrong, options, n=_SUGGEST_N, cutoff=0.6)
    return f" Did you mean: {', '.join(close)}?" if close else ""


def _placeholders(template: str) -> list[str]:
    return [m.group(1) for m in _PLACEHOLDER.finditer(template)]


def referenced_columns(m: TriplesMapIR) -> set[str]:
    """Every source column a map references (direct, multi-input, template
    placeholders, transform keys). ``{__run_id__}`` is excluded — it is the
    substrate's substitution slot, not a column."""
    cols: set[str] = set()
    if m.subject.template:
        cols.update(_placeholders(m.subject.template))
    for p in m.properties:
        if p.column:
            cols.add(p.column)
        cols.update(p.columns)
        if p.object_template:
            cols.update(_placeholders(p.object_template))
    cols.discard(_RUN_ID_PLACEHOLDER)
    return cols


def _expect_str(value: Any, where: str, issues: list[str]) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    issues.append(f"{where} must be a non-empty string (got {value!r}).")
    return None


def _check_unknown_keys(
    obj: Mapping[str, Any], allowed: Sequence[str], where: str, issues: list[str]
) -> None:
    for key in obj:
        if key not in allowed:
            issues.append(
                f"{where} has an unknown field {key!r}; allowed fields: "
                f"{', '.join(allowed)}.{_suggest(str(key), list(allowed))}"
            )


def _parse_transform(
    raw: Any, template: str | None, where: str, issues: list[str]
) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        issues.append(f"{where}.transform must be a mapping of {{placeholder: function}}.")
        return {}
    out: dict[str, str] = {}
    slots = set(_placeholders(template)) if template else set()
    for col, fn in raw.items():
        col_s = str(col)
        fn_s = _expect_str(fn, f"{where}.transform[{col_s!r}]", issues)
        if fn_s is None:
            continue
        if template is not None and col_s not in slots:
            issues.append(
                f"{where}.transform names {col_s!r}, which is not a placeholder of "
                f"its template {template!r}."
            )
            continue
        out[col_s] = fn_s
    return out


_SUBJECT_KEYS = ("template", "constant", "classes", "transform")
_PROPERTY_KEYS = (
    "predicate",
    "column",
    "columns",
    "function",
    "args",
    "object_template",
    "constant",
    "object_type",
    "datatype",
    "language",
    "transform",
    "fallback",
)
_MAP_KEYS = ("name", "source", "iterator", "subject", "properties")
_TOP_KEYS = ("version", "prefixes", "maps")


def _parse_subject(raw: Any, where: str, issues: list[str]) -> SubjectIR:
    if not isinstance(raw, Mapping):
        issues.append(f"{where}.subject must be a mapping with 'template' or 'constant'.")
        return SubjectIR()
    _check_unknown_keys(raw, _SUBJECT_KEYS, f"{where}.subject", issues)
    template = raw.get("template")
    constant = raw.get("constant")
    if (template is None) == (constant is None):
        issues.append(
            f"{where}.subject must have exactly one of 'template' (CSV/most cases) "
            f"or 'constant' (XML per-document injection)."
        )
    if template is not None:
        template = _expect_str(template, f"{where}.subject.template", issues)
        if template is not None and not _placeholders(template):
            issues.append(
                f"{where}.subject.template has no {{column}} placeholder; a fixed "
                f"IRI should use 'constant' instead."
            )
    if constant is not None:
        constant = _expect_str(constant, f"{where}.subject.constant", issues)
    classes_raw = raw.get("classes", [])
    classes: list[str] = []
    if classes_raw is None:
        classes_raw = []
    if not isinstance(classes_raw, Sequence) or isinstance(classes_raw, str):
        issues.append(f"{where}.subject.classes must be a list of class CURIEs/IRIs.")
    else:
        for i, c in enumerate(classes_raw):
            c_s = _expect_str(c, f"{where}.subject.classes[{i}]", issues)
            if c_s is not None:
                classes.append(c_s)
    transform = _parse_transform(
        raw.get("transform"), template if isinstance(template, str) else None,
        f"{where}.subject", issues,
    )
    return SubjectIR(
        template=template if isinstance(template, str) else None,
        constant=constant if isinstance(constant, str) else None,
        classes=tuple(classes),
        transform=transform,
    )


def _parse_property(raw: Any, where: str, issues: list[str]) -> PropertyIR | None:
    if not isinstance(raw, Mapping):
        issues.append(f"{where} must be a mapping with at least 'predicate'.")
        return None
    _check_unknown_keys(raw, _PROPERTY_KEYS, where, issues)
    predicate = _expect_str(raw.get("predicate"), f"{where}.predicate", issues)
    if predicate is None:
        return None
    where = f"{where} ({predicate})"

    column = raw.get("column")
    if column is not None:
        column = _expect_str(column, f"{where}.column", issues)
    columns_raw = raw.get("columns")
    columns: list[str] = []
    if columns_raw is not None:
        if not isinstance(columns_raw, Sequence) or isinstance(columns_raw, str):
            issues.append(f"{where}.columns must be a list of column names.")
        else:
            for i, c in enumerate(columns_raw):
                c_s = _expect_str(c, f"{where}.columns[{i}]", issues)
                if c_s is not None:
                    columns.append(c_s)
    object_template = raw.get("object_template")
    if object_template is not None:
        object_template = _expect_str(object_template, f"{where}.object_template", issues)
    constant = raw.get("constant")
    if constant is not None and not isinstance(constant, str):
        constant = str(constant)  # YAML may parse numbers/booleans; keep the surface form

    object_forms = [
        name
        for name, present in (
            ("column", column is not None),
            ("columns", bool(columns)),
            ("object_template", object_template is not None),
            ("constant", constant is not None),
        )
        if present
    ]
    if len(object_forms) != 1:
        issues.append(
            f"{where} must use exactly one object form of column / columns / "
            f"object_template / constant (got: {', '.join(object_forms) or 'none'})."
        )

    function = raw.get("function")
    if function is not None:
        function = _expect_str(function, f"{where}.function", issues)
        if function is not None and function.startswith("fn:"):
            # The menu speaks bare names; strip an over-eager prefix instead of failing.
            function = function[len("fn:"):]
    args_raw = raw.get("args")
    args: dict[str, str] = {}
    if args_raw is not None:
        if not isinstance(args_raw, Mapping):
            issues.append(f"{where}.args must be a mapping of {{arg_name: constant value}}.")
        else:
            for k, v in args_raw.items():
                args[str(k)] = str(v)

    if function is None:
        if columns:
            issues.append(f"{where}.columns requires 'function' (a multi-input Tier-0 function).")
        if args:
            issues.append(f"{where}.args requires 'function'.")
    elif column is None and not columns:
        issues.append(f"{where}.function requires 'column' (or 'columns' for multi-input).")

    object_type = raw.get("object_type")
    if object_type is not None:
        object_type = _expect_str(object_type, f"{where}.object_type", issues)
        if object_type is not None and object_type not in ("iri", "literal"):
            issues.append(f"{where}.object_type must be 'iri' or 'literal' (got {object_type!r}).")
            object_type = None
    if object_type == "iri" and column is not None and function is None:
        issues.append(
            f"{where}: a bare column cannot be emitted as an IRI (raw values are not "
            f"IRI-encoded and break the store on load). For a URL column use "
            f"function: iri_safe with object_type: iri; for an entity link use "
            f"object_template."
        )

    datatype = raw.get("datatype")
    if datatype is not None:
        datatype = _expect_str(datatype, f"{where}.datatype", issues)
    language = raw.get("language")
    if language is not None:
        language = _expect_str(language, f"{where}.language", issues)
        if language is not None and not _LANGUAGE_TAG.match(language):
            issues.append(f"{where}.language must be a BCP47 tag like 'en' or 'ja'.")
    if datatype is not None and language is not None:
        issues.append(f"{where} cannot have both datatype and language (RDF literals allow one).")
    is_iri_result = object_type == "iri" or (
        object_template is not None and object_type != "literal"
    )
    if is_iri_result and (datatype is not None or language is not None):
        issues.append(f"{where}: datatype/language apply to literals, not IRIs.")

    transform = _parse_transform(
        raw.get("transform"), object_template if isinstance(object_template, str) else None,
        where, issues,
    )
    if transform and object_template is None:
        issues.append(f"{where}.transform applies to object_template placeholders only.")

    fallback = raw.get("fallback", False)
    if not isinstance(fallback, bool):
        issues.append(f"{where}.fallback must be true or false.")
        fallback = False
    if fallback and (function is not None or column is None):
        issues.append(
            f"{where}.fallback marks a raw-string passthrough: use it with a bare "
            f"'column' and no function."
        )

    return PropertyIR(
        predicate=predicate,
        column=column if isinstance(column, str) else None,
        columns=tuple(columns),
        function=function if isinstance(function, str) else None,
        args=args,
        object_template=object_template if isinstance(object_template, str) else None,
        constant=constant if isinstance(constant, str) else None,
        object_type=object_type if isinstance(object_type, str) else None,
        datatype=datatype if isinstance(datatype, str) else None,
        language=language if isinstance(language, str) else None,
        transform=transform,
        fallback=fallback,
    )


def _parse_map(raw: Any, index: int, issues: list[str]) -> TriplesMapIR | None:
    where = f"maps[{index}]"
    if not isinstance(raw, Mapping):
        issues.append(f"{where} must be a mapping (name / source / subject / properties).")
        return None
    name = raw.get("name")
    if isinstance(name, str) and name.strip():
        where = f"map '{name.strip()}'"
    _check_unknown_keys(raw, _MAP_KEYS, where, issues)
    name = _expect_str(raw.get("name"), f"{where}.name", issues)
    if name is not None and not _MAP_NAME.match(name):
        issues.append(
            f"{where}.name {name!r} must be an identifier (letters, digits, '_', '-')."
        )
    source = _expect_str(raw.get("source"), f"{where}.source", issues)
    if source is not None and ("/" in source or "\\" in source):
        issues.append(
            f"{where}.source {source!r} must be a bare filename exactly as the "
            f"inspection lists it (no directories)."
        )
    iterator = raw.get("iterator")
    if iterator is not None:
        iterator = _expect_str(iterator, f"{where}.iterator", issues)
    if source is not None:
        suffix = "." + source.rsplit(".", 1)[-1].lower() if "." in source else ""
        if suffix in _XML_SUFFIXES and iterator is None:
            issues.append(f"{where}: an XML source requires 'iterator' (an XPath).")
        if suffix in _TABULAR_SUFFIXES and iterator is not None:
            issues.append(f"{where}: 'iterator' applies to XML sources only; remove it.")
        if suffix not in _TABULAR_SUFFIXES | _XML_SUFFIXES:
            issues.append(
                f"{where}.source {source!r} must be .csv, .tsv or .xml (a JSON source "
                f"is read via its tabularized .csv name — use the name the "
                f"inspection lists)."
            )
    subject = _parse_subject(raw.get("subject"), where, issues)
    props_raw = raw.get("properties")
    properties: list[PropertyIR] = []
    if not isinstance(props_raw, Sequence) or isinstance(props_raw, str) or not props_raw:
        issues.append(f"{where}.properties must be a non-empty list of property rows.")
    else:
        for i, p_raw in enumerate(props_raw):
            p = _parse_property(p_raw, f"{where}.properties[{i}]", issues)
            if p is not None:
                properties.append(p)
    if name is None or source is None:
        return None
    return TriplesMapIR(
        name=name,
        source=source,
        subject=subject,
        properties=tuple(properties),
        iterator=iterator if isinstance(iterator, str) else None,
    )


def _check_curies(ir: MappingIR, issues: list[str]) -> None:
    """Every CURIE-position term must resolve: a declared/builtin prefix, or an
    absolute IRI. Reserved prefixes are the compiler's, not the IR's."""

    known = set(ir.prefixes) | set(BUILTIN_PREFIXES)

    def check(term: str, where: str) -> None:
        if _ABSOLUTE_IRI.match(term):
            return
        m = _CURIE.match(term)
        if not m:
            issues.append(
                f"{where}: {term!r} is neither a CURIE (prefix:local) nor an "
                f"absolute http(s) IRI."
            )
            return
        prefix = m.group(1)
        if prefix in RESERVED_PREFIXES:
            issues.append(
                f"{where}: the {prefix}: namespace is reserved for the compiler; "
                f"Tier-0 functions are referenced by bare name in 'function'."
            )
        elif prefix not in known:
            issues.append(
                f"{where}: prefix {prefix!r} is not declared in 'prefixes'."
                f"{_suggest(prefix, sorted(known))}"
            )

    def check_template(template: str, where: str) -> None:
        # Only the prefix part (before the first placeholder) matters; a
        # template is a string, so we check its CURIE head if it has one.
        head = template.split("{", 1)[0]
        if head.startswith(("http://", "https://")):
            return
        m = _CURIE.match(head)
        if m:
            check(f"{m.group(1)}:x", where)
        else:
            issues.append(
                f"{where}: template {template!r} must start with a declared prefix "
                f"(e.g. sdr:...) or an absolute http(s) IRI."
            )

    for m_ir in ir.maps:
        where = f"map '{m_ir.name}'"
        if m_ir.subject.template:
            check_template(m_ir.subject.template, f"{where}.subject.template")
        if m_ir.subject.constant:
            check(m_ir.subject.constant, f"{where}.subject.constant")
        for c in m_ir.subject.classes:
            check(c, f"{where}.subject.classes")
        for p in m_ir.properties:
            p_where = f"{where} property {p.predicate}"
            check(p.predicate, p_where)
            if p.datatype:
                check(p.datatype, f"{p_where}.datatype")
            if p.object_template and p.object_type != "literal":
                check_template(p.object_template, f"{p_where}.object_template")
            if p.constant is not None and p.object_type == "iri":
                check(p.constant, f"{p_where}.constant")


def parse_mapping_ir(text: str) -> MappingIR:
    """Parse + structurally validate an IR YAML document.

    Raises :class:`MappingIRParseError` with EVERY problem found. YAML itself is
    parsed with ``yaml.safe_load`` (lazy import — PyYAML ships with every real
    deployment; see module docstring).
    """
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - environment failure
        raise ImportError(
            "PyYAML is required to parse a mapping spec (install asterism-step0[validate])."
        ) from exc

    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise MappingIRParseError(
            [f"The mapping spec is not valid YAML: {exc}"]
        ) from exc
    if not isinstance(doc, Mapping):
        raise MappingIRParseError(
            ["The mapping spec must be a YAML mapping with 'version', 'prefixes' and 'maps'."]
        )

    issues: list[str] = []
    _check_unknown_keys(doc, _TOP_KEYS, "mapping spec", issues)

    version = doc.get("version")
    if version != 1:
        issues.append(f"version must be 1 (got {version!r}).")

    prefixes_raw = doc.get("prefixes") or {}
    prefixes: dict[str, str] = {}
    if not isinstance(prefixes_raw, Mapping):
        issues.append("prefixes must be a mapping of {prefix: namespace IRI}.")
    else:
        for name, iri in prefixes_raw.items():
            name_s = str(name)
            if not _PREFIX_NAME.match(name_s):
                issues.append(f"prefix name {name_s!r} is not a valid prefix.")
                continue
            if name_s in RESERVED_PREFIXES:
                issues.append(
                    f"prefix {name_s!r} is reserved for the compiler; remove it."
                )
                continue
            if name_s in BUILTIN_PREFIXES and str(iri) != BUILTIN_PREFIXES[name_s]:
                issues.append(
                    f"prefix {name_s!r} is builtin as <{BUILTIN_PREFIXES[name_s]}>; "
                    f"it cannot be redefined."
                )
                continue
            iri_s = str(iri)
            if not _ABSOLUTE_IRI.match(iri_s):
                issues.append(f"prefix {name_s!r} must map to an absolute http(s) IRI.")
                continue
            prefixes[name_s] = iri_s

    maps_raw = doc.get("maps")
    maps: list[TriplesMapIR] = []
    if not isinstance(maps_raw, Sequence) or isinstance(maps_raw, str) or not maps_raw:
        issues.append("maps must be a non-empty list of triples maps.")
    else:
        for i, m_raw in enumerate(maps_raw):
            m = _parse_map(m_raw, i, issues)
            if m is not None:
                maps.append(m)
        names = [m.name for m in maps]
        for dup in sorted({n for n in names if names.count(n) > 1}):
            issues.append(f"map name {dup!r} is used more than once; names must be unique.")

    ir = MappingIR(prefixes=prefixes, maps=tuple(maps), version=1)
    _check_curies(ir, issues)

    if issues:
        raise MappingIRParseError(issues)
    return ir


# ---------------------------------------------------------------------------
# Environment-aware validation (files / columns / function catalog)
# ---------------------------------------------------------------------------


def validate_mapping_ir(
    ir: MappingIR,
    *,
    files: Sequence[str],
    headers: Mapping[str, Sequence[str] | None],
    catalog: FunctionCatalog,
) -> list[str]:
    """Check a structurally-valid IR against the real environment.

    ``files`` are the available source filenames; ``headers`` maps a tabular
    filename to its real column list (``None``/missing = unreadable → columns
    are not checked for that source, mirroring ``validate_rml_design``).
    Collects ALL issues; every message is closed-menu actionable (did-you-mean).
    Pure: the caller reads the directory/headers (see ``design_loop``).
    """
    issues: list[str] = []
    file_set = set(files)

    for m in ir.maps:
        where = f"map '{m.name}'"
        if m.source not in file_set:
            hint = _suggest(m.source, sorted(file_set))
            if not hint and file_set:
                hint = f" Available files: {', '.join(sorted(file_set))}."
            issues.append(
                f"{where}: source file {m.source!r} does not exist; use a filename "
                f"exactly as the inspection lists it.{hint}"
            )

        suffix = "." + m.source.rsplit(".", 1)[-1].lower() if "." in m.source else ""
        columns = headers.get(m.source) if suffix in _TABULAR_SUFFIXES else None
        if columns:
            col_set = set(columns)
            for col in sorted(referenced_columns(m)):
                if col not in col_set:
                    issues.append(
                        f"{where}: column {col!r} is not in {m.source} (columns: "
                        f"{', '.join(columns)}).{_suggest(col, list(columns))}"
                    )

        for slot, fn_name in m.subject.transform.items():
            issues.extend(_check_transform(fn_name, slot, f"{where}.subject", catalog))
        for p in m.properties:
            p_where = f"{where} property {p.predicate}"
            if p.function is not None:
                issues.extend(_check_function(p, p_where, catalog))
            for slot, fn_name in p.transform.items():
                issues.extend(_check_transform(fn_name, slot, p_where, catalog))

    return issues


def _check_function(p: PropertyIR, where: str, catalog: FunctionCatalog) -> list[str]:
    issues: list[str] = []
    fn = catalog.get(p.function or "")
    if fn is None:
        issues.append(
            f"{where}: function {p.function!r} is not in the vetted Tier-0 set; "
            f"choose one of: {', '.join(catalog.names())}."
            f"{_suggest(p.function or '', catalog.names())}"
        )
        return issues

    provided_cols = [p.column] if p.column else list(p.columns)
    n_min = fn.required_column_count
    n_max = len(fn.column_params)
    if not (n_min <= len(provided_cols) <= n_max):
        expect = str(n_min) if n_min == n_max else f"{n_min}..{n_max}"
        plural = "s" if n_max != 1 else ""
        issues.append(
            f"{where}: {fn.name} takes {expect} column input{plural} "
            f"({', '.join(a for a, _ in fn.column_params)}); got {len(provided_cols)}."
        )

    allowed_args = set(fn.constant_params)
    required_const = {a for a in fn.constant_params if a in fn.required_args}
    for extra in sorted(set(p.args) - allowed_args):
        menu = ", ".join(sorted(allowed_args)) or "(none)"
        issues.append(
            f"{where}: {fn.name} does not take a constant arg {extra!r}; it takes: "
            f"{menu}.{_suggest(extra, sorted(allowed_args))}"
        )
    for missing in sorted(required_const - set(p.args)):
        issues.append(f"{where}: {fn.name} requires the constant arg {missing!r}.")
    return issues


def _check_transform(
    fn_name: str, slot: str, where: str, catalog: FunctionCatalog
) -> list[str]:
    fn = catalog.get(fn_name)
    if fn is None:
        return [
            f"{where}: transform function {fn_name!r} (for {{{slot}}}) is not in the "
            f"vetted Tier-0 set.{_suggest(fn_name, catalog.names())}"
        ]
    if fn.multivalued:
        return [
            f"{where}: {fn.name} returns multiple values and cannot build a single "
            f"IRI segment for {{{slot}}}."
        ]
    required_const = {a for a in fn.constant_params if a in fn.required_args}
    if len(fn.column_params) != 1 or required_const:
        return [
            f"{where}: transform for {{{slot}}} must be a single-input function "
            f"(like slug); {fn.name} is not."
        ]
    return []
