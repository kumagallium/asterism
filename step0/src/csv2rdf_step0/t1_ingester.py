"""Recover composite-key structure from an ingester's IRI builders (T1 safety net).

Phase 3 dogfood Round 3 exposed T1's blind spot: the uniqueness check only read
the MIE's ``{}`` templates. A *refined* ingester that correctly mints
``sdr:paper/{sid}-{slug(doi)}`` therefore produced a T1 **warn** ("no composite
templates in MIE"), because the real composite key lived in Python, not in the
MIE. This module parses the ingester with :mod:`ast` and recovers, **per RDF
entity, the actual CSV columns that compose its IRI** — so full-CSV uniqueness
validation runs against what the ingester *does*, not just what the MIE
*documents*. If ``propose`` picks the wrong key on a subset, a full-CSV
``csv2rdf-validate`` now catches it from the ingester even when the MIE looks
clean.

Two ingester styles are supported (both seen in real dogfood output):

1. **Builder-function style** (LLM / proposal output)::

       def sample_iri(sid, sample_id):
           return SDR[f"sample/{sid}-{sample_id}"]
       ...
       sample_iri(row["SID"], row["sample_id"])

   IRI placeholders are *function parameters*; their CSV columns come from call
   sites (``row["SID"]`` → ``SID``), with a case-insensitive header-name
   fallback (``sid`` → ``SID``) when no call site is informative.

2. **Inline style** (Phase 1 hand-written ``starrydata.py``)::

       paper_sid = row.get("SID", "").strip()
       sample_id = row.get("sample_id", "").strip()
       sample_key = f"{paper_sid}-{sample_id}"
       sample = sdr[f"sample/{sample_key}"]

   IRI placeholders are *local variables*; we trace assignments across as many
   hops as needed back to ``row["COL"]`` / ``row.get("COL")``.

The extractor is deliberately conservative: a placeholder that cannot be traced
to a CSV column (a loop index ``i``, a ``run_id``, ``csv_path.name``) is
recorded in :attr:`IngesterKey.unresolved` rather than guessed. The validator
only runs uniqueness on :attr:`IngesterKey.fully_resolved` keys, so secondary
resources (``descriptor/{sample_key}/{i}``) never produce a false failure.
"""
from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

# A resolution path is a set of (enclosing-function, identifier) pairs already
# visited — the cycle guard that lets param resolution recurse across scopes.
_Seen = frozenset[tuple[str | None, str]]

# ----------------------------------------------------------------------------
# Public result type
# ----------------------------------------------------------------------------


@dataclass
class IngesterKey:
    """One IRI-minting site recovered from the ingester.

    ``columns`` are resolved CSV column names in IRI order; ``unresolved`` holds
    placeholder identifiers that could not be mapped to a column.
    """

    entity: str  # the IRI prefix, e.g. "sample" / "curve" / "paper"
    columns: tuple[str, ...]  # resolved CSV columns, IRI order, de-duplicated
    placeholders: tuple[str, ...]  # original identifiers from the f-string chain
    unresolved: tuple[str, ...] = ()  # placeholders not traceable to a column
    func: str | None = None  # enclosing function name (for evidence)

    @property
    def fully_resolved(self) -> bool:
        """True when every placeholder maps to a CSV column (safe to validate)."""
        return bool(self.columns) and not self.unresolved


# ----------------------------------------------------------------------------
# Resolution accumulator
# ----------------------------------------------------------------------------


@dataclass
class _Res:
    """Resolution result: ordered CSV columns plus untraceable identifiers."""

    columns: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)

    @staticmethod
    def column(name: str) -> _Res:
        return _Res(columns=[name])

    @staticmethod
    def unknown(name: str) -> _Res:
        return _Res(unresolved=[name])

    def merge(self, other: _Res) -> _Res:
        for c in other.columns:
            if c not in self.columns:
                self.columns.append(c)
        for u in other.unresolved:
            if u not in self.unresolved:
                self.unresolved.append(u)
        return self


# ----------------------------------------------------------------------------
# AST helpers
# ----------------------------------------------------------------------------

# An IRI literal's leading constant looks like "entity/...". The slash is the
# key signal that separates this from an ordinary dict lookup.
_ENTITY_PREFIX = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)/")

# String methods / wrappers that don't change which column a value came from.
_PASSTHROUGH_METHODS = frozenset(
    {"strip", "lstrip", "rstrip", "lower", "upper", "casefold", "title", "zfill", "replace"}
)
_PASSTHROUGH_FUNCS = frozenset({"str", "int", "float", "slug", "_slug"})


def _slice_node(node: ast.Subscript) -> ast.expr:
    """Return the subscript's slice expression (Python 3.9+ stores it directly)."""
    return node.slice


def _as_iri_fstring(node: ast.AST) -> tuple[str, ast.JoinedStr] | None:
    """If ``node`` is ``<ns>[f"entity/..."]`` return ``(entity, fstring)``.

    Requires the subscript base to be a bare name (a namespace like ``sdr`` /
    ``SDR``) and the f-string to start with an ``entity/`` literal. The trailing
    slash keeps ordinary ``d[f"{k}"]`` dict access from matching.
    """
    if not isinstance(node, ast.Subscript) or not isinstance(node.value, ast.Name):
        return None
    sl = _slice_node(node)
    if not isinstance(sl, ast.JoinedStr) or not sl.values:
        return None
    first = sl.values[0]
    if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
        return None
    m = _ENTITY_PREFIX.match(first.value)
    if not m:
        return None
    return m.group(1), sl


def _csv_column_of(node: ast.AST) -> str | None:
    """Return the CSV column ``node`` reads, unwrapping ``.strip()`` etc.

    Recognises ``row["COL"]``, ``row.get("COL", ...)`` and any chain of
    no-op string transforms / ``str()`` / ``slug()`` wrapping them.
    """
    # row["COL"]
    if isinstance(node, ast.Subscript):
        key = _slice_node(node)
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            return key.value
        return None
    if isinstance(node, ast.Call):
        func = node.func
        # row.get("COL", ...)  /  x.strip()  /  x.replace(...)
        if isinstance(func, ast.Attribute):
            if func.attr == "get" and node.args:
                arg0 = node.args[0]
                if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                    return arg0.value
                return None
            if func.attr in _PASSTHROUGH_METHODS:
                return _csv_column_of(func.value)
            return None
        # str(x) / slug(x) wrappers
        if isinstance(func, ast.Name) and func.id in _PASSTHROUGH_FUNCS and node.args:
            return _csv_column_of(node.args[0])
    return None


def _value_names(node: ast.AST) -> list[str]:
    """Collect value-bearing identifiers in a placeholder expression.

    Skips the *callee* of calls (so ``slug(doi)`` yields ``doi``, not ``slug``)
    and method names (so ``x.strip()`` yields ``x``). Order-preserving, deduped.
    """
    names: list[str] = []

    class _V(ast.NodeVisitor):
        def visit_Call(self, n: ast.Call) -> None:
            for a in n.args:
                self.visit(a)
            for kw in n.keywords:
                self.visit(kw.value)

        def visit_Attribute(self, n: ast.Attribute) -> None:
            self.visit(n.value)  # row.get -> row, not "get"

        def visit_Name(self, n: ast.Name) -> None:
            if n.id not in names:
                names.append(n.id)

    _V().visit(node)
    return names


def _iter_local_nodes(fn: ast.AST) -> Iterable[ast.AST]:
    """Yield every node inside ``fn`` without descending into nested scopes."""
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue  # a new scope — its locals are not ours
        stack.extend(ast.iter_child_nodes(node))


# ----------------------------------------------------------------------------
# Per-scope symbol table
# ----------------------------------------------------------------------------


@dataclass
class _Scope:
    func_name: str | None  # None / "" for module scope
    params: list[str] = field(default_factory=list)
    assignments: dict[str, ast.AST] = field(default_factory=dict)  # name -> RHS
    iri_sites: list[tuple[str, ast.JoinedStr]] = field(default_factory=list)
    calls: list[ast.Call] = field(default_factory=list)


def _param_names(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    a = fn.args
    names = [arg.arg for arg in (*a.posonlyargs, *a.args, *a.kwonlyargs)]
    if a.vararg:
        names.append(a.vararg.arg)
    if a.kwarg:
        names.append(a.kwarg.arg)
    return names


def _build_scope(fn: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> _Scope:
    scope = _Scope(func_name=name, params=_param_names(fn))
    for node in _iter_local_nodes(fn):
        if isinstance(node, ast.Assign):
            # last write wins; only simple single-Name targets are traceable
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    scope.assignments[tgt.id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.value is not None:
                scope.assignments[node.target.id] = node.value
        elif isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
            scope.assignments[node.target.id] = node.value
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            scope.calls.append(node)
        iri = _as_iri_fstring(node)
        if iri is not None:
            scope.iri_sites.append(iri)
    return scope


# ----------------------------------------------------------------------------
# Module context + resolver
# ----------------------------------------------------------------------------


class _ModuleCtx:
    def __init__(self, tree: ast.AST, columns: tuple[str, ...] | None) -> None:
        self.columns = columns
        self.scopes: dict[str, _Scope] = {}
        self.funcdefs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
        # func_name -> [(call, caller_scope)]
        self.callsites: dict[str, list[tuple[ast.Call, _Scope]]] = {}

        # Module scope (top-level assignments / IRI sites).
        module_scope = _Scope(func_name="")
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        module_scope.assignments[tgt.id] = node.value
        self.scopes[""] = module_scope

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.funcdefs[node.name] = node
                self.scopes[node.name] = _build_scope(node, node.name)

        # Register call sites with their enclosing scope.
        for scope in self.scopes.values():
            for call in scope.calls:
                assert isinstance(call.func, ast.Name)
                self.callsites.setdefault(call.func.id, []).append((call, scope))

    # -- resolution -----------------------------------------------------------

    def resolve_name(
        self, name: str, scope: _Scope, seen: _Seen
    ) -> _Res:
        key = (scope.func_name, name)
        if key in seen:
            return _Res()  # cycle guard — contribute nothing
        seen = seen | {key}
        if name in scope.assignments:
            return self.resolve_expr(scope.assignments[name], scope, seen)
        if name in scope.params:
            return self.resolve_param(scope.func_name, name, seen)
        return _Res.unknown(name)

    def resolve_expr(self, node: ast.AST, scope: _Scope, seen: _Seen) -> _Res:
        col = _csv_column_of(node)
        if col is not None:
            return _Res.column(col)
        if isinstance(node, ast.JoinedStr):
            res = _Res()
            for part in node.values:
                if isinstance(part, ast.FormattedValue):
                    res.merge(self.resolve_value(part.value, scope, seen))
            return res
        if isinstance(node, ast.Name):
            return self.resolve_name(node.id, scope, seen)
        return self.resolve_value(node, scope, seen)

    def resolve_value(self, node: ast.AST, scope: _Scope, seen: _Seen) -> _Res:
        """Resolve one placeholder expression to its CSV column(s)."""
        col = _csv_column_of(node)
        if col is not None:
            return _Res.column(col)
        names = _value_names(node)
        if not names:
            return _Res()  # constant-only placeholder (e.g. an index literal)
        res = _Res()
        for n in names:
            res.merge(self.resolve_name(n, scope, seen))
        return res

    def resolve_param(self, func_name: str | None, param: str, seen: _Seen) -> _Res:
        scope = self.scopes.get(func_name or "")
        params = scope.params if scope else []
        idx = params.index(param) if param in params else None

        res = _Res()
        found = False
        for call, caller_scope in self.callsites.get(func_name or "", []):
            arg: ast.AST | None = None
            if idx is not None and idx < len(call.args):
                cand = call.args[idx]
                if not isinstance(cand, ast.Starred):
                    arg = cand
            for kw in call.keywords:
                if kw.arg == param:
                    arg = kw.value
            if arg is None:
                continue
            sub = self.resolve_expr(arg, caller_scope, seen)
            if sub.columns:
                res.merge(sub)
                found = True
        if found and res.columns:
            return _Res(columns=res.columns)  # call site is authoritative

        # Fallback: case-insensitive header-name match (sid -> SID).
        if self.columns:
            for c in self.columns:
                if c.lower() == param.lower():
                    return _Res.column(c)
        return _Res.unknown(param)


# ----------------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------------


def extract_ingester_keys(
    source: str, available_columns: Iterable[str] | None = None
) -> list[IngesterKey]:
    """Parse ingester ``source`` and return the composite key behind each IRI.

    ``available_columns`` (the union of the source CSV headers) powers the
    case-insensitive param→column fallback; omit it to rely purely on call-site
    evidence. Returns one :class:`IngesterKey` per distinct ``(entity, columns)``
    minting site, in first-seen order. Returns ``[]`` if ``source`` does not
    parse (a syntactically broken ingester is the validator's problem, not ours).
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    cols = tuple(available_columns) if available_columns is not None else None
    ctx = _ModuleCtx(tree, cols)

    keys: list[IngesterKey] = []
    seen_sigs: set[tuple[str, tuple[str, ...], tuple[str, ...]]] = set()
    for scope in ctx.scopes.values():
        for entity, fstring in scope.iri_sites:
            placeholders: list[str] = []
            res = _Res()
            for part in fstring.values:
                if not isinstance(part, ast.FormattedValue):
                    continue
                direct = _csv_column_of(part.value)
                if direct is not None:
                    placeholders.append(direct)
                    res.merge(_Res.column(direct))
                    continue
                for ident in _value_names(part.value):
                    placeholders.append(ident)
                res.merge(ctx.resolve_value(part.value, scope, frozenset()))

            sig = (entity, tuple(res.columns), tuple(res.unresolved))
            if sig in seen_sigs:
                continue
            seen_sigs.add(sig)
            keys.append(
                IngesterKey(
                    entity=entity,
                    columns=tuple(res.columns),
                    placeholders=tuple(dict.fromkeys(placeholders)),
                    unresolved=tuple(res.unresolved),
                    func=scope.func_name or None,
                )
            )
    return keys
