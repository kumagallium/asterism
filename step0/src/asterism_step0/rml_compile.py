"""Deterministic Mapping-IR → RML Turtle compiler.

ALL RML/FnO syntax knowledge lives here — moved out of the propose prompt's
HARD RULES per ADR ``docs/architecture/mapping-ir-compiler.md``:

* the ``rmlf:`` (``http://w3id.org/rml/``) function-execution namespace — the
  legacy ``fnml#`` guess (#86) becomes structurally impossible;
* FnO parameter IRIs (``fn:p_value`` / ``p_value1`` / ``p_table`` / …) — bound
  from the injected catalog, never written by the LLM;
* constants via ``rmlf:constant`` (the legacy ``rml:`` namespace has none);
* CURIE expansion inside ``rr:template`` strings (RML engines do NOT expand
  prefixes inside template literals — a top weak-model trap);
* termType/datatype/language placement, ``ql:CSV`` vs ``ql:XPath`` sources;
* readable-segment transforms as a nested ``fn:template`` + nested single-input
  function execution (probed against real Morph-KGC: nesting works, and plain
  ``rr:template`` placeholders are R2RML-percent-encoded by the engine, so raw
  data columns in templates are load-safe without wrapping).

Fail-closed: an unknown function, an unresolvable CURIE or an out-of-spec IR
raises :class:`RmlCompileError` — the compiler narrows the closed set, never
widens it. Its output still passes the full downstream gates
(``assert_rml_safe`` / ``validate_rml_design`` / T9 / the hard 422 ingest gate)
as defense in depth.

Pure: ``compile_mapping_ir(ir, catalog) -> str`` has no I/O and is fully
deterministic (stable ordering, no timestamps) so golden tests can pin it.
"""
from __future__ import annotations

import re

from asterism_step0.mapping_ir import (
    BUILTIN_PREFIXES,
    FunctionCatalog,
    MappingIR,
    PropertyIR,
    SubjectIR,
    TriplesMapIR,
    catalog_from_registry,
)

__all__ = ["RmlCompileError", "compile_mapping_ir", "default_catalog"]

# The compiler-owned prefix block. rmlf: MUST be http://w3id.org/rml/ — Morph-KGC
# does not support the legacy http://semweb.mmlab.be/ns/fnml# namespace.
_RESERVED_PREFIX_LINES = (
    ("rr", "http://www.w3.org/ns/r2rml#"),
    ("rml", "http://semweb.mmlab.be/ns/rml#"),
    ("ql", "http://semweb.mmlab.be/ns/ql#"),
    ("rmlf", "http://w3id.org/rml/"),
    ("fn", "https://kumagallium.github.io/asterism/fn/"),
)

_CURIE = re.compile(r"^([A-Za-z][\w.-]*):(\S+)$")
_PLACEHOLDER = re.compile(r"(?<!\\)\{([^{}]+)\}")
# Characters that may not appear raw inside an IRIREF (<...>) in Turtle. Data
# values are engine-encoded at runtime; this guards compile-time constants only.
_IRI_ILLEGAL = re.compile(r'[\x00-\x20<>"{}|^`\\]')

_XML_SUFFIXES = frozenset({".xml"})


class RmlCompileError(Exception):
    """The IR cannot be compiled. ``issues`` carries every problem (collected,
    not short-circuited) in the IR's vocabulary, ready for loop feedback."""

    def __init__(self, issues: list[str]):
        self.issues = list(issues)
        super().__init__("Mapping spec cannot be compiled:\n- " + "\n- ".join(self.issues))


def default_catalog() -> FunctionCatalog:
    """The vetted Tier-0 catalog from ``asterism.functions.REGISTRY``.

    Raises ``ImportError`` when the ingest package is not importable — callers
    surface that as an environment failure (same contract as the T9 checker).
    """
    return catalog_from_registry()


# ---------------------------------------------------------------------------
# Small emission helpers
# ---------------------------------------------------------------------------


def _turtle_string(value: str) -> str:
    """A Turtle double-quoted string literal for ``value`` (escaped)."""
    out = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{out}"'


class _Compiler:
    def __init__(self, ir: MappingIR, catalog: FunctionCatalog):
        self.ir = ir
        self.catalog = catalog
        self.issues: list[str] = []
        self.namespaces: dict[str, str] = dict(BUILTIN_PREFIXES)
        self.namespaces.update(ir.prefixes)

    # -- terms ---------------------------------------------------------------

    def _fail(self, msg: str) -> None:
        self.issues.append(msg)

    def expand(self, term: str, where: str) -> str:
        """A CURIE-or-IRI → full IRI string."""
        if term.startswith(("http://", "https://")):
            return term
        m = _CURIE.match(term)
        if m and m.group(1) in self.namespaces:
            return self.namespaces[m.group(1)] + m.group(2)
        self._fail(f"{where}: cannot resolve {term!r} to an IRI.")
        return term

    def iri_ref(self, term: str, where: str) -> str:
        """A CURIE-or-IRI in a Turtle IRI position: keep a resolvable CURIE
        verbatim (its prefix is declared in the output), else ``<full-iri>``."""
        m = _CURIE.match(term)
        if m and m.group(1) in self.namespaces:
            return term
        full = self.expand(term, where)
        if _IRI_ILLEGAL.search(full):
            self._fail(f"{where}: {term!r} contains characters illegal in an IRI.")
        return f"<{full}>"

    def expand_template(self, template: str, where: str) -> str:
        """Expand a template's CURIE head to a full IRI; placeholders unchanged.

        RML engines treat ``rr:template`` as an opaque string — a ``sdr:...``
        head would NOT be expanded at runtime, so the compiler must do it.
        """
        head, brace, rest = template.partition("{")
        m = _CURIE.match(head)
        if m and m.group(1) in self.namespaces:
            head = self.namespaces[m.group(1)] + m.group(2)
        elif not head.startswith(("http://", "https://")):
            self._fail(
                f"{where}: template {template!r} must start with a declared prefix "
                f"or an absolute http(s) IRI."
            )
        return head + brace + rest

    # -- function executions ---------------------------------------------------

    def function_execution(
        self,
        fn_name: str,
        column_inputs: list[str],
        args: dict[str, str],
        where: str,
        indent: str,
        nested_inputs: dict[str, str] | None = None,
    ) -> str:
        """An ``rmlf:functionExecution [...]`` node (no trailing termination).

        ``column_inputs`` bind the catalog's column params in declaration order;
        ``args`` bind constant params by python arg name; ``nested_inputs`` maps
        a column-param arg name to an already-rendered nested node (used by the
        transform emission — nesting is supported by Morph-KGC, probed).
        """
        fn = self.catalog.get(fn_name)
        if fn is None:
            self._fail(
                f"{where}: function {fn_name!r} is not in the vetted Tier-0 set."
            )
            return "[ ]"
        nested_inputs = nested_inputs or {}
        inputs: list[str] = []
        col_params = list(fn.column_params)
        if len(column_inputs) > len(col_params):
            self._fail(
                f"{where}: {fn.name} takes at most {len(col_params)} column input(s); "
                f"got {len(column_inputs)}."
            )
            column_inputs = column_inputs[: len(col_params)]
        for (arg, param_iri), col in zip(col_params, column_inputs, strict=False):
            if arg in nested_inputs:
                value_map = nested_inputs[arg]
            else:
                value_map = f"[ rml:reference {_turtle_string(col)} ]"
            inputs.append(
                f"rmlf:input [ rmlf:parameter <{param_iri}> ;\n"
                f"{indent}                 rmlf:inputValueMap {value_map} ]"
            )
        # Required column params left unbound → issue (validators catch this too,
        # but the compiler must not silently emit a broken execution).
        for arg, _ in col_params[len(column_inputs):]:
            if arg in fn.required_args:
                self._fail(f"{where}: {fn.name} is missing a column input for {arg!r}.")
        for arg in sorted(args):
            param_iri = fn.constant_params.get(arg)
            if param_iri is None:
                self._fail(f"{where}: {fn.name} does not take a constant arg {arg!r}.")
                continue
            inputs.append(
                f"rmlf:input [ rmlf:parameter <{param_iri}> ;\n"
                f"{indent}                 rmlf:inputValueMap [ rmlf:constant "
                f"{_turtle_string(args[arg])} ] ]"
            )
        for arg in sorted(a for a in fn.constant_params if a in fn.required_args):
            if arg not in args:
                self._fail(f"{where}: {fn.name} requires the constant arg {arg!r}.")
        joined = f" ;\n{indent}    ".join(inputs)
        return (
            f"[\n{indent}    rmlf:function fn:{fn.name} ;\n{indent}    {joined} ]"
        )

    def transformed_template(
        self, template: str, transform: dict[str, str], where: str, indent: str
    ) -> str:
        """A template with per-placeholder transforms → a nested ``fn:template``
        execution: the constant template gets positional ``{1}``..``{4}`` tokens
        and each field is either a plain column reference or a nested
        single-input function execution (e.g. ``fn:slug``).
        """
        expanded = self.expand_template(template, where)
        slots = _PLACEHOLDER.findall(expanded)
        if len(slots) > 4:
            self._fail(
                f"{where}: a transformed template supports at most 4 placeholders "
                f"(fn:template has 4 field slots); got {len(slots)}."
            )
            slots = slots[:4]

        def positional(m: re.Match[str]) -> str:
            if m.group(1) not in slots:  # beyond the 4-slot cap — already an issue
                return m.group(0)
            return "{" + str(slots.index(m.group(1)) + 1) + "}"

        const_template = _PLACEHOLDER.sub(positional, expanded)
        nested: dict[str, str] = {}
        columns: list[str] = []
        for i, col in enumerate(slots):
            arg = f"field{i + 1}"
            columns.append(col)
            if col in transform:
                inner = self.function_execution(
                    transform[col], [col], {}, f"{where} transform of {{{col}}}",
                    indent + "        ",
                )
                nested[arg] = f"[ rmlf:functionExecution {inner} ]"
        return self.function_execution(
            "template",
            columns,
            {"template": const_template},
            where,
            indent,
            nested_inputs=nested,
        )

    # -- maps ------------------------------------------------------------------

    def subject_map(self, m: TriplesMapIR, s: SubjectIR, where: str) -> str:
        parts: list[str] = []
        if s.constant is not None:
            full = self.expand(s.constant, f"{where}.subject.constant")
            if _IRI_ILLEGAL.search(full):
                self._fail(f"{where}.subject.constant is not a valid IRI.")
            parts.append(f"rr:constant <{full}>")
        elif s.template is not None:
            if s.transform:
                fe = self.transformed_template(
                    s.template, dict(s.transform), f"{where}.subject", "  "
                )
                parts.append(f"rmlf:functionExecution {fe}")
                parts.append("rr:termType rr:IRI")
            else:
                expanded = self.expand_template(s.template, f"{where}.subject.template")
                parts.append(f"rr:template {_turtle_string(expanded)}")
        else:
            self._fail(f"{where}.subject needs 'template' or 'constant'.")
        if s.classes:
            classes = ", ".join(
                self.iri_ref(c, f"{where}.subject.classes") for c in s.classes
            )
            parts.append(f"rr:class {classes}")
        joined = " ;\n    ".join(parts)
        return f"  rr:subjectMap [\n    {joined} ]"

    def object_map(self, p: PropertyIR, where: str) -> str:
        annotations: list[str] = []
        if p.datatype:
            annotations.append(f"rr:datatype {self.iri_ref(p.datatype, where)}")
        if p.language:
            annotations.append(f"rr:language {_turtle_string(p.language)}")

        if p.constant is not None:
            if p.object_type == "iri":
                full = self.expand(p.constant, f"{where}.constant")
                if _IRI_ILLEGAL.search(full):
                    self._fail(f"{where}.constant is not a valid IRI.")
                return f"[ rr:constant <{full}> ]"
            const = _turtle_string(p.constant)
            if p.datatype:
                return f"[ rr:constant {const}^^{self.iri_ref(p.datatype, where)} ]"
            if p.language:
                return f"[ rr:constant {const}@{p.language} ]"
            return f"[ rr:constant {const} ]"

        if p.object_template is not None:
            if p.object_type == "literal":
                body = [f"rr:template {_turtle_string(p.object_template)}"]
                body.append("rr:termType rr:Literal")
                body.extend(annotations)
                return "[ " + " ;\n      ".join(body) + " ]"
            if p.transform:
                fe = self.transformed_template(
                    p.object_template, dict(p.transform), where, "      "
                )
                return f"[ rmlf:functionExecution {fe} ;\n      rr:termType rr:IRI ]"
            expanded = self.expand_template(p.object_template, f"{where}.object_template")
            return (
                f"[ rr:template {_turtle_string(expanded)} ;\n      rr:termType rr:IRI ]"
            )

        if p.function is not None:
            cols = [p.column] if p.column else list(p.columns)
            fe = self.function_execution(
                p.function, [c for c in cols if c], dict(p.args), where, "      "
            )
            body = [f"rmlf:functionExecution {fe}"]
            if p.object_type == "iri":
                body.append("rr:termType rr:IRI")
            body.extend(annotations)
            return "[ " + " ;\n      ".join(body) + " ]"

        if p.column is not None:
            if p.object_type == "iri":
                # Unencoded reference IRIs break the store on load (probed);
                # the parser rejects this, the compiler refuses as backstop.
                self._fail(
                    f"{where}: a bare column cannot be an IRI; use function: "
                    f"iri_safe or an object_template."
                )
            body = [f"rml:reference {_turtle_string(p.column)}"]
            body.extend(annotations)
            return "[ " + " ;\n      ".join(body) + " ]"

        self._fail(f"{where} has no object (column / columns / object_template / constant).")
        return "[ ]"

    def triples_map(self, m: TriplesMapIR) -> str:
        where = f"map '{m.name}'"
        node = f"<#{_map_node_name(m.name)}>"
        lines: list[str] = [f"{node} a rr:TriplesMap ;"]
        source = _turtle_string(m.source)
        suffix = "." + m.source.rsplit(".", 1)[-1].lower() if "." in m.source else ""
        if suffix in _XML_SUFFIXES:
            if not m.iterator:
                self._fail(f"{where}: an XML source requires an iterator.")
            lines.append(
                "  rml:logicalSource [ rml:source "
                f"{source} ; rml:referenceFormulation ql:XPath ;\n"
                f"                      rml:iterator {_turtle_string(m.iterator or '')} ] ;"
            )
        else:
            lines.append(
                f"  rml:logicalSource [ rml:source {source} ; "
                "rml:referenceFormulation ql:CSV ] ;"
            )
        lines.append(self.subject_map(m, m.subject, where) + " ;")
        for p in m.properties:
            p_where = f"{where} property {p.predicate}"
            pred = self.iri_ref(p.predicate, p_where)
            obj = self.object_map(p, p_where)
            if p.fallback and p.column:
                lines.append(f"  # fallback: {p.column} not expanded")
            lines.append(
                f"  rr:predicateObjectMap [ rr:predicate {pred} ;\n"
                f"    rr:objectMap {obj} ] ;"
            )
        # terminate the last property (turn the trailing ' ;' into ' .')
        lines[-1] = lines[-1][:-2] + " ."
        return "\n".join(lines)

    def compile(self) -> str:
        header = [
            "# Compiled by the Asterism mapping-spec compiler — do not edit by hand;",
            "# edit the mapping spec (YAML) and re-materialize instead.",
        ]
        prefix_lines = [
            f"@prefix {name}: <{iri}> ." for name, iri in _RESERVED_PREFIX_LINES
        ]
        prefix_lines.append(f"@prefix xsd: <{BUILTIN_PREFIXES['xsd']}> .")
        for name in sorted(self.ir.prefixes):
            prefix_lines.append(f"@prefix {name}: <{self.ir.prefixes[name]}> .")
        maps = [self.triples_map(m) for m in self.ir.maps]
        if not self.ir.maps:
            self._fail("The mapping spec has no maps.")
        if self.issues:
            raise RmlCompileError(self.issues)
        parts = ["\n".join(header), "\n".join(prefix_lines), "\n\n".join(maps)]
        return "\n\n".join(parts) + "\n"


def _map_node_name(name: str) -> str:
    """A deterministic TriplesMap node name: ``paper`` → ``PaperMap``,
    ``crystal_structure`` → ``CrystalStructureMap``. Already-suffixed names
    (``PaperMap``) are kept as-is."""
    parts = [p for p in re.split(r"[-_\s]+", name) if p]
    camel = "".join(p[:1].upper() + p[1:] for p in parts)
    return camel if camel.endswith("Map") else camel + "Map"


def compile_mapping_ir(ir: MappingIR, catalog: FunctionCatalog | None = None) -> str:
    """Compile a structurally-valid :class:`MappingIR` to RML Turtle.

    Raises :class:`RmlCompileError` (all issues collected) on anything outside
    the spec — never emits a function/namespace/shape it does not know.
    ``catalog`` defaults to the live Tier-0 registry (:func:`default_catalog`).
    """
    if catalog is None:
        catalog = default_catalog()
    return _Compiler(ir, catalog).compile()
