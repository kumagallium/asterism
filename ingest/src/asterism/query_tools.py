"""Per-dataset *typed query tools* declared as content (#20 P4).

product_direction "決定論・型付きを主役、LLM は escape" must hold for **any**
dataset, not just starrydata. The 4 hardcoded typed tools (sample_search /
property_ranking / ...) speak ``sd:`` and live in engine code, so a new dataset
gets nothing typed and falls back to the #18 LLM escape. P4 fixes that: a dataset
declares its convenient operations as **content** — a bundle of named,
parameterized, read-only SPARQL queries (the "MIE ``sparql_query_examples``"
shape, ADR ``ontology-canonical-lifecycle.md`` §5(a)) — and the engine turns each
into a typed, safe-to-call operation. The trust model is the same as the Tier 0
function library: a human vets the query template; nothing is generated at
runtime.

This module is the engine (schema-agnostic): it loads the declarations, binds
caller arguments into the template **safely** (type-checked, escaped — never
string-concatenated raw), and runs the result through the canonical FROM-merge
read path (so a typed tool sees exactly what Ask sees, cross-dataset). It does
NOT know any vocabulary; starrydata's tools live in
``datasets/starrydata/query_tools.yaml`` as content.

Safety: parameters are serialized per their declared type (string -> escaped
literal, number/integer -> validated+clamped numeric, iri -> validated <IRI>,
enum -> whitelist), so a value can never inject SPARQL. Templates are validated
read-only (SELECT/ASK, no update forms) at load time.

Quality gate (:func:`lint_query_tool`): the RML path learned that an LLM-drafted
artifact must be verified *before* it is stored, with the same parser the
execution engine uses — otherwise the authoring mistake surfaces as an opaque
runtime failure (observed live: a drafted tool used ``prov:generatedAtTime``
without declaring the prefix; it saved fine and then every Ask call died with an
Oxigraph ``400``). The lint renders the template with placeholder arguments and
parses it with pyoxigraph (same parser as the store), plus deterministic checks
(undeclared prefixes, filter-only variables). Save/propose paths gate on it;
loading is *lenient* (a broken declaration is skipped with a warning, never
taking the whole typed surface down with it).
"""
from __future__ import annotations

import contextlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from asterism.datasets import datasets_root
from asterism.substrate import _scan_view, canonical_merge_query

_log = logging.getLogger(__name__)

# Update-form keywords — a declared template must be a read-only SELECT/ASK.
_UPDATE_FORM = re.compile(
    r"\b(insert|delete|load|clear|drop|create|add|move|copy)\b", re.IGNORECASE
)
_COMMENT = re.compile(r"#.*")

# Mustache-ish template tokens: {{name}} scalar, {{#name}}...{{/name}} section
# (kept iff the param is "active"), {{^name}}...{{/name}} inverse section.
_SECTION = re.compile(r"\{\{([#^])(\w+)\}\}(.*?)\{\{/\2\}\}", re.DOTALL)
_SCALAR = re.compile(r"\{\{(\w+)\}\}")
_PARAM_NAME = re.compile(r"^[A-Za-z_]\w*$")

_VALID_TYPES = frozenset({"string", "number", "integer", "iri", "enum"})


class QueryToolError(Exception):
    """A declared tool is malformed, or a call violates its parameter contract."""


@dataclass(frozen=True)
class ToolParam:
    """One declared parameter of a query tool."""

    name: str
    type: str = "string"
    required: bool = False
    default: Any = None
    description: str = ""
    minimum: float | None = None
    maximum: float | None = None
    enum: tuple[str, ...] | None = None


@dataclass(frozen=True)
class QueryTool:
    """A named, parameterized, read-only SPARQL operation declared as content."""

    name: str
    title: str
    description: str
    params: tuple[ToolParam, ...]
    query: str
    # output_key -> {"var": <sparql var>, "number": bool}
    item: dict[str, dict[str, Any]] = field(default_factory=dict)

    def param(self, name: str) -> ToolParam | None:
        return next((p for p in self.params if p.name == name), None)


# ----------------------------------------------------------------------------
# Loading + validation
# ----------------------------------------------------------------------------


def _parse_param(raw: dict[str, Any]) -> ToolParam:
    name = str(raw.get("name", ""))
    if not _PARAM_NAME.match(name):
        raise QueryToolError(f"invalid parameter name: {name!r}")
    ptype = str(raw.get("type", "string"))
    if ptype not in _VALID_TYPES:
        raise QueryToolError(f"parameter {name!r}: unknown type {ptype!r}")
    enum = raw.get("enum")
    if ptype == "enum" and not enum:
        raise QueryToolError(f"parameter {name!r}: enum type needs a non-empty enum list")
    return ToolParam(
        name=name,
        type=ptype,
        required=bool(raw.get("required", False)),
        default=raw.get("default"),
        description=str(raw.get("description", "")),
        minimum=raw.get("minimum"),
        maximum=raw.get("maximum"),
        enum=tuple(str(v) for v in enum) if enum else None,
    )


def _validate_template(tool_name: str, query: str, params: tuple[ToolParam, ...]) -> None:
    """Fail fast on a malformed declaration (read-only + placeholder sanity)."""
    if not query.strip():
        raise QueryToolError(f"tool {tool_name!r}: empty query")
    if _UPDATE_FORM.search(_COMMENT.sub("", query)):
        raise QueryToolError(
            f"tool {tool_name!r}: query must be a read-only SELECT/ASK "
            "(update-form keyword found)"
        )
    known = {p.name for p in params}
    # Every referenced placeholder/section must name a declared parameter.
    for _sigil, pname, _body in _SECTION.findall(query):
        if pname not in known:
            raise QueryToolError(f"tool {tool_name!r}: section references unknown param {pname!r}")
    # A scalar {{p}} that sits OUTSIDE its own section must be always-present
    # (required or defaulted); otherwise a missing optional value leaves a hole.
    sectionless = _SECTION.sub("", query)
    always = {p.name for p in params if p.required or p.default is not None}
    for pname in _SCALAR.findall(sectionless):
        if pname not in known:
            raise QueryToolError(f"tool {tool_name!r}: references unknown param {pname!r}")
        if pname not in always:
            raise QueryToolError(
                f"tool {tool_name!r}: optional param {pname!r} used outside its "
                "{{#"
                f"{pname}"
                "}} section"
            )


def _parse_item_map(raw: Any) -> dict[str, dict[str, Any]]:
    """Parse the ``result.item`` mapping (output_key -> var / {var, number})."""
    if not raw:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, spec in dict(raw).items():
        if isinstance(spec, str):
            out[str(key)] = {"var": spec, "number": False}
        elif isinstance(spec, dict):
            out[str(key)] = {"var": str(spec["var"]), "number": bool(spec.get("number", False))}
        else:
            raise QueryToolError(f"result.item[{key!r}] must be a var name or a mapping")
    return out


def parse_query_tools(data: Any) -> list[QueryTool]:
    """Parse a ``query_tools.yaml`` document into validated :class:`QueryTool`s."""
    tools_raw = (data or {}).get("tools", []) if isinstance(data, dict) else []
    tools: list[QueryTool] = []
    seen: set[str] = set()
    for raw in tools_raw:
        name = str(raw.get("name", ""))
        if not _PARAM_NAME.match(name):
            raise QueryToolError(f"invalid tool name: {name!r}")
        if name in seen:
            raise QueryToolError(f"duplicate tool name: {name!r}")
        seen.add(name)
        params = tuple(_parse_param(p) for p in raw.get("parameters", []))
        query = str(raw.get("query", ""))
        _validate_template(name, query, params)
        result = raw.get("result") or {}
        tools.append(
            QueryTool(
                name=name,
                title=str(raw.get("title", name)),
                description=str(raw.get("description", "")),
                params=params,
                query=query,
                item=_parse_item_map(result.get("item")),
            )
        )
    return tools


def parse_query_tools_lenient(data: Any) -> tuple[list[QueryTool], list[str]]:
    """Parse per-tool, skipping broken declarations instead of failing the bundle.

    Returns ``(tools, issues)`` where each issue names the skipped tool and why.
    Strictness lives at *save* time (:func:`parse_query_tools` + the lint gate);
    at *load* time one broken declaration must not take down every other typed
    tool of the dataset — availability of the vetted surface wins.
    """
    tools_raw = (data or {}).get("tools", []) if isinstance(data, dict) else []
    tools: list[QueryTool] = []
    issues: list[str] = []
    seen: set[str] = set()
    for raw in tools_raw:
        label = raw.get("name", "?") if isinstance(raw, dict) else "?"
        try:
            tool = parse_query_tools({"tools": [raw]})[0]
            if tool.name in seen:
                raise QueryToolError(f"duplicate tool name: {tool.name!r}")
            seen.add(tool.name)
            tools.append(tool)
        except QueryToolError as exc:
            issues.append(f"tool {label!r}: {exc}")
    return tools, issues


def load_query_tools(name: str, root: Path | str | None = None) -> list[QueryTool]:
    """Load dataset ``name``'s declared query tools, or ``[]`` if none.

    Best-effort on a *missing* file (returns ``[]``, mirroring ``load_dataset``)
    AND on a malformed declaration: broken tools are skipped with a logged
    warning (see :func:`parse_query_tools_lenient`). The authoring bug is
    surfaced where it belongs — at save/propose time by the strict parse + lint
    gate — not by killing the whole dataset's typed surface at load.
    """
    base = Path(root) if root is not None else datasets_root()
    if base is None:
        return []
    path = base / name / "query_tools.yaml"
    if not path.is_file():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        _log.warning("query_tools[%s]: unreadable YAML, skipping bundle: %s", name, exc)
        return []
    tools, issues = parse_query_tools_lenient(data)
    for msg in issues:
        _log.warning("query_tools[%s]: skipped %s", name, msg)
    return tools


def available_datasets(root: Path | str | None = None) -> list[str]:
    """Names of datasets that ship a ``query_tools.yaml`` (sorted), or ``[]``."""
    base = Path(root) if root is not None else datasets_root()
    if base is None or not base.is_dir():
        return []
    return sorted(
        p.name for p in base.iterdir() if (p / "query_tools.yaml").is_file()
    )


def load_all_query_tools(root: Path | str | None = None) -> dict[str, list[QueryTool]]:
    """Map every dataset with declared tools to its :class:`QueryTool`s.

    The MCP surface is the union over all datasets (each reads the same
    cross-dataset canonical scope), so the server registers all of these.
    """
    return {name: load_query_tools(name, root) for name in available_datasets(root)}


# ----------------------------------------------------------------------------
# Safe parameter binding
# ----------------------------------------------------------------------------


def _escape_literal(value: str) -> str:
    """Escape a string for safe embedding in a double-quoted SPARQL literal."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "")


def _serialize(param: ToolParam, value: Any) -> str:
    """Serialize ``value`` to a SPARQL token per ``param.type`` (injection-safe)."""
    if param.type in ("number", "integer"):
        try:
            num: float = int(value) if param.type == "integer" else float(value)
        except (TypeError, ValueError) as exc:
            raise QueryToolError(
                f"param {param.name!r}: expected {param.type}, got {value!r}"
            ) from exc
        if param.minimum is not None and num < param.minimum:
            num = type(num)(param.minimum)
        if param.maximum is not None and num > param.maximum:
            num = type(num)(param.maximum)
        return str(num)
    text = str(value)
    if param.type == "iri":
        iri = text.replace("<", "").replace(">", "")
        if not iri.startswith(("http://", "https://")):
            raise QueryToolError(f"param {param.name!r}: expected an http(s) IRI, got {text!r}")
        return f"<{iri}>"
    if param.type == "enum":
        if param.enum is None or text not in param.enum:
            raise QueryToolError(f"param {param.name!r}: {text!r} not in {param.enum}")
        return f'"{_escape_literal(text)}"'
    return f'"{_escape_literal(text)}"'  # string


def _is_active(param: ToolParam, value: Any) -> bool:
    """Whether an *optional section* for ``param`` is kept (value meaningfully set).

    None is inactive; an empty string is treated as "not provided" too.
    """
    return value is not None and not (param.type == "string" and value == "")


def bind_params(tool: QueryTool, args: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Validate ``args`` against the tool's parameters and serialize each.

    Returns ``{name: {"active": bool, "token": <sparql token> | None}}``. Raises
    :class:`QueryToolError` for an unknown arg, a missing required param, or a
    value that violates its declared type / enum.
    """
    declared = {p.name for p in tool.params}
    for key in args:
        if key not in declared:
            raise QueryToolError(f"tool {tool.name!r}: unknown argument {key!r}")
    bound: dict[str, dict[str, Any]] = {}
    for p in tool.params:
        provided = args.get(p.name)
        if provided is None and p.default is not None:
            provided = p.default
        if provided is None:
            if p.required:
                raise QueryToolError(f"tool {tool.name!r}: missing required argument {p.name!r}")
            bound[p.name] = {"active": False, "token": None}
            continue
        bound[p.name] = {"active": _is_active(p, provided), "token": _serialize(p, provided)}
    return bound


def render_query(tool: QueryTool, args: dict[str, Any]) -> str:
    """Bind ``args`` and render the SPARQL template (safe substitution)."""
    bound = bind_params(tool, args)

    def _section(m: re.Match[str]) -> str:
        sigil, pname, body = m.group(1), m.group(2), m.group(3)
        active = bound.get(pname, {}).get("active", False)
        keep = active if sigil == "#" else not active
        return body if keep else ""

    rendered = _SECTION.sub(_section, tool.query)

    def _scalar(m: re.Match[str]) -> str:
        pname = m.group(1)
        token = bound.get(pname, {}).get("token")
        # A leftover scalar for an inactive param can only occur inside a kept
        # inverse section; emit empty rather than a literal "{{p}}".
        return token if token is not None else ""

    return _SCALAR.sub(_scalar, rendered)


# ----------------------------------------------------------------------------
# Lint — the save-time quality gate (schema-agnostic, engine-parser-true)
# ----------------------------------------------------------------------------

# ``PREFIX foo:`` declarations (SPARQL, case-insensitive; group 1 = prefix label,
# None for the empty prefix). Scanned on a _scan_view so literals/IRIs/comments
# cannot fake one.
_PREFIX_DECL = re.compile(r"(?i)\bPREFIX\s+([A-Za-z][\w.-]*)?\s*:")
# A prefixed-name USE like ``sd:Curve`` / ``xsd:float``. The lookbehind keeps us
# off variables (?x), IRIs (scrubbed anyway), and double-colon artifacts.
_PNAME_USE = re.compile(r"(?<![\w:<?$-])([A-Za-z][\w.-]*):")
# Same, but capturing the local part too (for expanding against a vocabulary).
_PNAME_FULL = re.compile(r"(?<![\w:<?$-])([A-Za-z][\w.-]*):([A-Za-z_][\w.-]*)")
# A full PREFIX declaration with its IRI (read from the RAW text — _scan_view
# blanks IRIs, so the view cannot yield the binding).
_PREFIX_BINDING = re.compile(r"(?i)\bPREFIX\s+([A-Za-z][\w.-]*)?\s*:\s*<([^>\s]+)>")
_IRI_REF = re.compile(r"<(https?://[^>\s]+)>")
_FILTER_KW = re.compile(r"(?i)\bFILTER\b")
_VAR = re.compile(r"[?$](\w+)")

# Namespaces a query may always use without the dataset's RML mapping them
# (rdf:type spelling, datatype casts, schema-level probes).
_STANDARD_NS = (
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "http://www.w3.org/2000/01/rdf-schema#",
    "http://www.w3.org/2001/XMLSchema#",
    "http://www.w3.org/2002/07/owl#",
)


@dataclass(frozen=True)
class QueryToolLint:
    """Outcome of :func:`lint_query_tool`.

    ``errors`` are defects that WILL fail at execution time (broken syntax,
    undeclared prefix) — save paths must reject on them. ``warnings`` are
    almost-certainly-bugs that still parse (e.g. a variable used only inside a
    FILTER, so the filter can never match) — save paths surface them for the
    human vet but do not block.
    """

    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors


def _placeholder_value(p: ToolParam) -> Any:
    """A type-correct dummy value for rendering the template at lint time."""
    if p.default is not None:
        return p.default
    if p.type == "integer":
        with contextlib.suppress(TypeError, ValueError):
            if p.minimum is not None:
                return int(p.minimum)
        return 1
    if p.type == "number":
        with contextlib.suppress(TypeError, ValueError):
            if p.minimum is not None:
                return float(p.minimum)
        return 0.0
    if p.type == "iri":
        return "http://example.invalid/lint"
    if p.type == "enum":
        return p.enum[0] if p.enum else "lint"
    return "lint"


def _render_variants(tool: QueryTool) -> list[str]:
    """Render the template in the shapes execution can produce.

    Two variants cover every section state a caller can reach: all parameters
    provided (every ``{{#p}}`` block kept) and only required parameters provided
    (optional blocks dropped, ``{{^p}}`` inverse blocks kept).
    """
    full = {p.name: _placeholder_value(p) for p in tool.params}
    variants = [render_query(tool, full)]
    minimal = {p.name: _placeholder_value(p) for p in tool.params if p.required}
    if minimal != full:
        variants.append(render_query(tool, minimal))
    return variants


def _undeclared_prefixes(rendered: str) -> list[str]:
    """Prefix labels used as ``label:name`` but never declared with PREFIX."""
    view = _scan_view(rendered)
    declared: set[str] = set()

    def _blank(m: re.Match[str]) -> str:
        declared.add(m.group(1) or "")
        return " " * len(m.group(0))

    scrubbed = _PREFIX_DECL.sub(_blank, view)
    used = {m.group(1) for m in _PNAME_USE.finditer(scrubbed)}
    return sorted(u for u in used if u not in declared)


def _sparql_syntax_error(rendered: str) -> str | None:
    """Parse ``rendered`` with the SAME engine the store runs (pyoxigraph).

    An empty in-memory store makes this a pure parse+plan check (milliseconds),
    with zero dialect gap to the real Oxigraph — the exact property that made
    the observed prefix bug reach production. Returns the parser message, or
    None if the query is well-formed (or pyoxigraph is unavailable, in which
    case the deterministic checks still stand).
    """
    try:
        import pyoxigraph
    except ImportError:  # pragma: no cover - dependency is declared, belt+braces
        return None
    try:
        pyoxigraph.Store().query(rendered)
    # pyoxigraph 0.4+ raises SyntaxError; the 0.3 line (pinned by morph-kgc)
    # raises ValueError for the same parse failures.
    except (SyntaxError, ValueError) as exc:
        return str(exc)
    except Exception:  # pragma: no cover - non-syntax store issues are not lint's
        return None
    return None


def _filter_only_vars(rendered: str) -> list[str]:
    """Variables that appear ONLY inside FILTER(...) — never bound by a pattern.

    Legal SPARQL, but the filter can never match (observed live in an AI-drafted
    tool: ``FILTER(CONTAINS(LCASE(?comments), ...))`` with no ``?comments``
    triple). ``FILTER EXISTS { ... }`` blocks are skipped — they bind patterns.
    """
    view = _scan_view(rendered)
    spans: list[tuple[int, int]] = []
    for m in _FILTER_KW.finditer(view):
        i = view.find("(", m.end())
        if i == -1:
            continue
        between = view[m.end() : i]
        if "{" in between or re.search(r"(?i)\bEXISTS\b", between):
            continue
        depth, j = 0, i
        while j < len(view):
            if view[j] == "(":
                depth += 1
            elif view[j] == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        spans.append((i, j + 1))
    in_filter: set[str] = set()
    for a, b in spans:
        in_filter |= {m.group(1) for m in _VAR.finditer(view[a:b])}
    outside = list(view)
    for a, b in spans:
        outside[a:b] = " " * (b - a)
    out_vars = {m.group(1) for m in _VAR.finditer("".join(outside))}
    return sorted(in_filter - out_vars)


def _vocabulary_issues(rendered: str, vocabulary: dict[str, Any]) -> list[str]:
    """Terms the query uses that the dataset's RML never maps (schema drift).

    ``vocabulary`` is :func:`asterism.rml_validate.extract_rml_vocabulary`'s
    shape (``{"prefixes": {label: iri}, "terms": set[iri]}``). A term outside
    the mapped set matches nothing in the ingested data — the classic 0-row
    tool an LLM drafts by guessing a plausible predicate. Deterministic and
    dataset-agnostic: the closed set comes from the mapping, not from any
    hardcoded schema.
    """
    terms = set(vocabulary.get("terms") or ())
    if not terms:
        return []
    rml_prefixes = {str(k): str(v) for k, v in dict(vocabulary.get("prefixes") or {}).items()}
    issues: list[str] = []
    q_prefixes = {(m.group(1) or ""): m.group(2) for m in _PREFIX_BINDING.finditer(rendered)}
    # A same-label prefix bound to a DIFFERENT IRI than the RML's is the classic
    # everything-matches-nothing drift; say so explicitly.
    for label, iri in q_prefixes.items():
        rml_iri = rml_prefixes.get(label)
        if rml_iri and rml_iri.rstrip("#/") != iri.rstrip("#/"):
            issues.append(
                f"PREFIX {label}: is <{iri}> here but the dataset's RML binds "
                f"{label}: to <{rml_iri}> — patterns using it will match nothing"
            )
    # Collect every term the query references: expanded prefixed names (on the
    # scan view, declarations scrubbed) + literal <IRI> refs (raw text, minus
    # the PREFIX binding IRIs themselves).
    view = _scan_view(rendered)
    scrubbed = _PREFIX_DECL.sub(lambda m: " " * len(m.group(0)), view)
    used: set[str] = set()
    for m in _PNAME_FULL.finditer(scrubbed):
        base = q_prefixes.get(m.group(1))
        if base:
            used.add(base + m.group(2))
    binding_iris = set(q_prefixes.values())
    for m in _IRI_REF.finditer(rendered):
        if m.group(1) not in binding_iris:
            used.add(m.group(1))
    unknown = sorted(
        iri
        for iri in used
        if iri not in terms and not iri.startswith(_STANDARD_NS)
    )
    for iri in unknown[:12]:
        issues.append(
            f"term <{iri}> is not mapped by this dataset's RML — the pattern "
            "using it will match nothing (0 rows)"
        )
    return issues


def lint_query_tool(tool: QueryTool, vocabulary: dict[str, Any] | None = None) -> QueryToolLint:
    """Lint one parsed tool: will its template actually run against the store?

    This is the query_tools counterpart of the RML design checks
    (``asterism.rml_validate``): deterministic, vocabulary-agnostic, and run
    BEFORE the artifact is persisted, so an authoring mistake (human or
    LLM-drafted) surfaces as an actionable message at save time instead of an
    opaque store error at ask time.

    ``vocabulary`` (optional): the dataset's mapped vocabulary from
    :func:`asterism.rml_validate.extract_rml_vocabulary`. When given, terms the
    RML never maps are flagged as warnings (a 0-row tool, the second failure
    family observed live). Still schema-agnostic — the closed set is derived
    from the dataset's own mapping.
    """
    try:
        variants = _render_variants(tool)
    except QueryToolError as exc:
        return QueryToolLint(errors=(f"template cannot be rendered: {exc}",))
    errors: list[str] = []
    warnings: list[str] = []
    for rendered in variants:
        missing = _undeclared_prefixes(rendered)
        if missing:
            for p in missing:
                msg = f"uses prefix '{p}:' without a PREFIX declaration"
                if msg not in errors:
                    errors.append(msg)
        else:
            err = _sparql_syntax_error(rendered)
            if err:
                msg = f"SPARQL syntax error: {err}"
                if msg not in errors:
                    errors.append(msg)
        for v in _filter_only_vars(rendered):
            msg = (
                f"variable ?{v} is used only inside FILTER and never bound by a "
                "graph pattern — that filter can never match"
            )
            if msg not in warnings:
                warnings.append(msg)
        if vocabulary is not None:
            for msg in _vocabulary_issues(rendered, vocabulary):
                if msg not in warnings:
                    warnings.append(msg)
    return QueryToolLint(errors=tuple(errors), warnings=tuple(warnings))


# ----------------------------------------------------------------------------
# Execution (read-only, via the canonical FROM-merge)
# ----------------------------------------------------------------------------


def _cell(row: dict[str, Any], var: str) -> Any:
    node = row.get(var)
    return node.get("value") if node else None


def _shape_row(item_map: dict[str, dict[str, Any]], row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, spec in item_map.items():
        val = _cell(row, spec["var"])
        if spec["number"] and val is not None:
            with contextlib.suppress(TypeError, ValueError):
                val = float(val)
        out[key] = val
    return out


async def run_query_tool(
    client: Any, tool: QueryTool, args: dict[str, Any], *, max_rows: int = 200
) -> dict[str, Any]:
    """Render ``tool`` with ``args`` and run it over the canonical FROM-merge.

    Read-only by construction: the template is validated SELECT/ASK at load and
    the rendered query goes through ``canonical_merge_query`` (the same
    cross-dataset scope Ask uses). Returns
    ``{tool, count, items, truncated, sparql}`` where ``items`` are rows shaped
    per the tool's ``result.item`` mapping (or raw ``{var: value}`` if none).
    """
    max_rows = max(1, min(int(max_rows), 2000))
    sparql = render_query(tool, args)
    try:
        effective = await canonical_merge_query(client, sparql)
    except ValueError as exc:
        # The canonical read scope rejected the template (e.g. a GRAPH pattern
        # with no canonical graphs published yet) — a content/state problem the
        # caller can act on, not an opaque server failure.
        raise QueryToolError(f"tool {tool.name!r}: {exc}") from exc
    try:
        raw = await client.sparql_select(effective)
    except Exception as exc:
        # If the template itself is broken (a pre-gate saved tool, or content
        # edited out-of-band), say WHY instead of leaking a bare store 400 —
        # the Ask agent relays this message to the user. A clean-linting
        # template that still fails is a real store/transport problem: re-raise
        # so callers keep treating it as a 5xx.
        lint = lint_query_tool(tool)
        if lint.errors:
            raise QueryToolError(
                f"tool {tool.name!r}: broken SPARQL template "
                f"({'; '.join(lint.errors)}) — fix the dataset's query_tools.yaml; "
                f"store said: {exc}"
            ) from exc
        raise
    results = raw.get("results", {}) if isinstance(raw, dict) else {}
    bindings = results.get("bindings", []) if isinstance(results, dict) else []
    truncated = len(bindings) > max_rows
    rows = bindings[:max_rows]
    if tool.item:
        items = [_shape_row(tool.item, r) for r in rows]
    else:
        items = [{k: _cell(r, k) for k in r} for r in rows]
    return {
        "tool": tool.name,
        "count": len(items),
        "items": items,
        "truncated": truncated,
        "sparql": effective,
    }
