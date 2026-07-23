"""Deterministic Mermaid classDiagram from a Mapping IR (§9 mapping spec).

The dataset diagram used to be whatever the LLM sketched in §1 — observed
live (ZEM dogfood) as an EMPTY class box with every predicate orphaned
outside the diagram, leaving the reviewer nothing to check the design
against. But the Mapping IR already states, per TriplesMap, the subject
classes, every predicate with its column / datatype / unit, and the IRI
links between maps — exactly what a reviewer needs to see INSIDE the boxes.
So when a parseable §9 spec exists the diagram is COMPILED from it, the same
way the RML is (deterministic, LLM-free); the LLM's §1 sketch remains only
as the fallback for designs without a spec.

Mapping:
  * every ``subject.classes`` entry becomes a Mermaid ``class`` block
  * every literal property row becomes ``+<predicate> <xsd_type> [unit]``
    inside its subject's class block(s); IR order is preserved — reviewers
    compare against their column order, not the alphabet
  * every IRI-object row whose ``object_template`` equals another map's
    ``subject.template`` becomes ``Domain --> Range : <predicate>``
  * any other IRI-object row renders as a ``+<predicate> IRI`` property
    line (the link target lives outside this design)

Mermaid escape rules follow ttl2mermaid (trap T5): colon-free member lines
(``+name xsd_double``, never ``+name: t``), sanitized identifiers, bare
local names. Units render as a bracketed suffix (``[µV/K]``) and are
dropped from the diagram when they contain characters Mermaid mis-parses
in member lines (parens/braces/colons) — the property table below the
diagram always carries the verbatim unit.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from asterism_step0.mapping_ir import MappingIR, PropertyIR, TriplesMapIR
from asterism_step0.ttl2mermaid import (
    ClassEntry,
    MermaidGraph,
    ObjectRelation,
    render_mermaid_body,
)

# Characters that flip a Mermaid classDiagram member line into a *method*
# (parens) or break brace balance / the colon rule (T5). A unit containing
# any of these is omitted from the diagram line — never mangled.
_UNSAFE_MEMBER_CHARS = re.compile(r"[(){}:\[\]]")

_XSD_NS = "http://www.w3.org/2001/XMLSchema#"


def _local(term: str) -> str:
    """CURIE/IRI → local name (``sd:Temperature`` → ``Temperature``)."""
    tail = re.split(r"[:#/]", term.strip())[-1]
    return tail or term.strip()


def _safe_ident(name: str) -> str:
    """A Mermaid-safe bare identifier (letters/digits/underscore, letter start)."""
    out = re.sub(r"[^A-Za-z0-9_]", "_", name.strip())
    if not out or not re.match(r"[A-Za-z_]", out[0]):
        out = "_" + out
    return out


def _expand(term: str, prefixes: dict[str, str] | object) -> str:
    """CURIE → full IRI via the IR's prefix map; full IRIs pass through."""
    if ":" in term:
        prefix, local = term.split(":", 1)
        if not local.startswith("//"):
            try:
                base = prefixes[prefix]  # type: ignore[index]
            except (KeyError, TypeError):
                return term
            return f"{base}{local}"
    return term


def _datatype_label(datatype: str | None) -> str:
    """IR datatype → colon-free diagram token (``xsd:double`` → ``xsd_double``)."""
    if not datatype:
        return ""
    if datatype.startswith(_XSD_NS):
        return f"xsd_{datatype[len(_XSD_NS) :]}"
    return _safe_ident(datatype.replace(":", "_"))


def _unit_suffix(unit: str | None) -> str:
    """Display unit → ``[µV/K]`` suffix, or empty when Mermaid-unsafe."""
    if not unit:
        return ""
    u = unit.strip()
    if not u or _UNSAFE_MEMBER_CHARS.search(u):
        return ""
    return f"[{u}]"


def _is_iri_object(p: PropertyIR) -> bool:
    """Mirror of the compiler's term-type contextual defaults.

    ``object_template`` defaults to IRI (unless ``object_type: literal``);
    column/columns/constant default to literal (unless ``object_type: iri``).
    """
    if p.object_template is not None:
        return p.object_type != "literal"
    return p.object_type == "iri"


def build_graph_from_ir(ir: MappingIR) -> MermaidGraph:
    """Assemble a :class:`MermaidGraph` from a parsed Mapping IR.

    Deterministic and total: never raises on a parseable IR. Maps without
    ``subject.classes`` contribute no class box (nothing to hang members on),
    and properties pointing at them fall back to ``+pred IRI`` lines.
    """
    prefixes = dict(ir.prefixes)

    # ---- class boxes (IR order, deduped; label collisions → prefixed label)
    entries: dict[str, ClassEntry] = {}  # expanded class IRI → entry
    label_map: dict[str, str] = {}
    taken: set[str] = set()

    def _class_entry(class_term: str) -> ClassEntry:
        iri = _expand(class_term, prefixes)
        entry = entries.get(iri)
        if entry is not None:
            return entry
        label = _safe_ident(_local(class_term))
        if label in taken:
            label = _safe_ident(class_term.replace(":", "_"))
        while label in taken:  # still colliding — numbered alias
            label = f"{label}_2" if not label[-1].isdigit() else label + "_"
        taken.add(label)
        entry = ClassEntry(iri=iri, label=label)
        entries[iri] = entry
        label_map[label] = iri
        return entry

    map_classes: list[tuple[TriplesMapIR, list[ClassEntry]]] = []
    for m in ir.maps:
        map_classes.append((m, [_class_entry(c) for c in m.subject.classes]))

    # ---- subject-term index for IRI-link resolution (template AND constant)
    subject_index: dict[str, ClassEntry] = {}
    for m, owners in map_classes:
        if not owners:
            continue  # boxless map — cannot be a relation endpoint
        for key in (m.subject.template, m.subject.constant):
            if key is not None:
                subject_index.setdefault(key, owners[0])

    # ---- property rows → members / relations (IR order, deduped)
    relations: list[ObjectRelation] = []
    seen_rel: set[tuple[str, str, str]] = set()
    for m, owners in map_classes:
        if not owners:
            continue
        for p in m.properties:
            pred = _safe_ident(_local(p.predicate))
            if _is_iri_object(p):
                target = (
                    subject_index.get(p.object_template) if p.object_template is not None else None
                )
                if target is not None:
                    for owner in owners:
                        key = (owner.label, target.label, pred)
                        if key not in seen_rel:
                            seen_rel.add(key)
                            relations.append(
                                ObjectRelation(
                                    domain_label=owner.label,
                                    range_label=target.label,
                                    property_label=pred,
                                )
                            )
                    continue
                member: tuple[str, str] = (pred, "IRI")
            else:
                rng = " ".join(x for x in (_datatype_label(p.datatype), _unit_suffix(p.unit)) if x)
                member = (pred, rng)
            for owner in owners:
                if member not in owner.datatype_properties:
                    owner.datatype_properties.append(member)

    return MermaidGraph(
        direction="LR",
        classes=[e for e in entries.values()],
        relations=relations,
        label_map=label_map,
    )


# ----------------------------------------------------------------------------
# Property ↔ column table (the "where did my column go" companion)
# ----------------------------------------------------------------------------


def _md_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def property_table_md(ir: MappingIR) -> str:
    """A Markdown table mapping every property row back to its source columns.

    This is the reviewer's completeness companion to the diagram: the diagram
    shows the STRUCTURE (boxes/edges), this table shows the PROVENANCE —
    predicate ↔ column(s) ↔ unit ↔ meaning (the IR's display metadata),
    verbatim and in IR order. Written below the Mermaid block in diagram.md;
    consumers that only extract the fenced block are unaffected.
    """
    lines = [
        "## Properties",
        "",
        "| Class | Property | Source | Type | Unit | Meaning |",
        "|---|---|---|---|---|---|",
    ]
    rows = 0
    for m in ir.maps:
        classes = ", ".join(_local(c) for c in m.subject.classes) or "—"
        for p in m.properties:
            if p.column:
                source = f"column `{p.column}`"
            elif p.columns:
                source = ", ".join(f"`{c}`" for c in p.columns)
            elif p.object_template is not None:
                source = f"template `{p.object_template}`"
            elif p.constant is not None:
                source = f"constant `{p.constant}`"
            else:
                source = "—"
            if p.function:
                source += f" via `{p.function}`"
            type_label = "IRI" if _is_iri_object(p) else (p.datatype or "")
            lines.append(
                "| "
                + " | ".join(
                    _md_cell(x)
                    for x in (
                        classes,
                        _local(p.predicate),
                        source,
                        type_label,
                        p.unit or "",
                        p.label or "",
                    )
                )
                + " |"
            )
            rows += 1
    if rows == 0:
        return ""
    lines.append("")
    return "\n".join(lines)


def render_diagram_doc(
    *, dataset_name: str, mermaid_body: str, property_table: str | None = None
) -> str:
    """THE diagram.md format — title + fenced Mermaid + optional property table.

    The single definition of the artifact's shape, shared by every producer:
    the materialize write path (CLI + api), the api's persisted registry
    artifact, and :func:`render_dataset_doc` (the regeneration CLI). Keeping
    one function is what makes a CLI-regenerated diagram.md and an
    api-materialized one byte-identical for the same design.

    ``mermaid_body`` is the bare diagram source (no fence). Consumers that
    extract only the fenced block (UI ``extractMermaid``, api
    ``registry.mermaid_of``) see exactly the same Mermaid as before the table
    existed — the table is provenance for humans reading the file.
    """
    doc = f"# {dataset_name} ontology — class diagram\n\n```mermaid\n{mermaid_body}\n```\n"
    if property_table:
        doc += "\n" + property_table
    return doc


def render_dataset_doc(ir: MappingIR, *, dataset_name: str) -> str:
    """The full diagram.md body for a dataset: Mermaid block + property table.

    Byte-deterministic for a given IR (CLI regeneration of an existing
    registry entry produces a stable file).
    """
    graph = build_graph_from_ir(ir)
    return render_diagram_doc(
        dataset_name=dataset_name,
        mermaid_body=render_mermaid_body(graph).rstrip("\n"),
        property_table=property_table_md(ir) or None,
    )


# ----------------------------------------------------------------------------
# CLI — regenerate diagram.md for an existing registry entry
# ----------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="asterism-ir2mermaid",
        description=(
            "Compile a Mapping IR (mapping spec YAML, e.g. a registry's "
            "<name>-mapping.yaml) into a deterministic Mermaid class-diagram "
            "doc. Use to regenerate diagram.md for datasets designed before "
            "diagrams were compiled from the IR."
        ),
    )
    p.add_argument("mapping", type=Path, help="Mapping IR YAML file")
    p.add_argument("--name", required=True, help="Dataset name (doc title)")
    p.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write the diagram .md here. Defaults to stdout.",
    )
    return p


def _main(argv: list[str] | None = None) -> int:
    from asterism_step0.mapping_ir import MappingIRParseError, parse_mapping_ir

    args = _build_arg_parser().parse_args(argv)
    try:
        ir = parse_mapping_ir(args.mapping.read_text(encoding="utf-8"))
    except MappingIRParseError as exc:
        for issue in exc.issues:
            sys.stderr.write(f"{issue}\n")
        return 1
    rendered = render_dataset_doc(ir, dataset_name=args.name)
    if args.output is None:
        sys.stdout.write(rendered)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = [
    "build_graph_from_ir",
    "property_table_md",
    "render_dataset_doc",
    "render_diagram_doc",
    "render_mermaid_body",
]
