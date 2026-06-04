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
"""
from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from asterism.datasets import datasets_root
from asterism.substrate import canonical_merge_query

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


def load_query_tools(name: str, root: Path | str | None = None) -> list[QueryTool]:
    """Load dataset ``name``'s declared query tools, or ``[]`` if none.

    Best-effort on a *missing* file (returns ``[]``, mirroring ``load_dataset``),
    but a *malformed* declaration raises :class:`QueryToolError` — a broken
    content file is an authoring bug we want surfaced, not silently dropped.
    """
    base = Path(root) if root is not None else datasets_root()
    if base is None:
        return []
    path = base / name / "query_tools.yaml"
    if not path.is_file():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return parse_query_tools(data)


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
    effective = await canonical_merge_query(client, sparql)
    raw = await client.sparql_select(effective)
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
