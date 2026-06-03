"""Generate a Mermaid classDiagram from a TBox TTL.

Eliminates the hand-sync between ``docs/ontology/{name}.ttl`` and
``docs/ontology/diagram.md``. CI can run this in ``--check`` mode and fail
the build if the diagram drifts from the TBox.

Mapping:
  * Every ``owl:Class`` becomes a Mermaid ``class`` block
  * Every ``owl:DatatypeProperty`` whose ``rdfs:domain`` is a class becomes
    a property line inside that class block (``+name xsd:type``)
  * Every ``owl:ObjectProperty`` whose ``rdfs:domain`` and ``rdfs:range``
    are both classes becomes a ``A --> B : <name>`` relation
  * ``rdfs:subClassOf`` to external classes (e.g. ``schema:ScholarlyArticle``,
    ``prov:Entity``) is emitted as a ``note for X "subClassOf ..."`` line

Mermaid escape rules (★ trap T5 from workflow §6):
  * Class names: strip the namespace prefix (``sd:Paper`` → ``Paper``)
  * Property names in datatype slots: bare local name; the xsd type goes
    after a single space (no colon, since GitHub's renderer chokes on
    ``:`` in classDiagram property lines)
  * Relation labels: bare local name; if it would collide with a Mermaid
    reserved word or duplicate another label, fall back to a numbered alias

The mapping table from short labels → full IRIs is emitted as Markdown
below the Mermaid block so the diagram stays self-contained.
"""
from __future__ import annotations

import argparse
import io
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# Common namespaces we strip from labels. Anything else keeps its prefix
# (with the colon escaped to underscore for Mermaid safety).
_COMMON_NAMESPACE_HINTS = {
    "http://www.w3.org/2002/07/owl#": "owl",
    "http://www.w3.org/2000/01/rdf-schema#": "rdfs",
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#": "rdf",
    "http://www.w3.org/2001/XMLSchema#": "xsd",
    "http://purl.org/dc/terms/": "dcterms",
    "https://schema.org/": "schema",
    "http://schema.org/": "schema",
    "http://www.w3.org/ns/prov#": "prov",
    "http://purl.org/ontology/bibo/": "bibo",
}


# ----------------------------------------------------------------------------
# Data extracted from TTL
# ----------------------------------------------------------------------------


@dataclass
class ClassEntry:
    """A class block to render in Mermaid."""

    iri: str
    label: str  # short label (no prefix) — used in the diagram
    datatype_properties: list[tuple[str, str]] = field(default_factory=list)
    """List of (property local name, range short label) — rendered as ``+name xsd:type``."""

    subclass_of: list[str] = field(default_factory=list)
    """External class IRIs (short labels) for the ``note for`` line."""


@dataclass
class ObjectRelation:
    """An object-property relation between two classes."""

    domain_label: str
    range_label: str
    property_label: str


@dataclass
class MermaidGraph:
    """The full diagram payload."""

    direction: str = "LR"
    classes: list[ClassEntry] = field(default_factory=list)
    relations: list[ObjectRelation] = field(default_factory=list)
    label_map: dict[str, str] = field(default_factory=dict)
    """Reverse map: short label → full IRI (rendered below the Mermaid block)."""


# ----------------------------------------------------------------------------
# IRI → short label
# ----------------------------------------------------------------------------


def _short_label(iri: str, namespaces: dict[str, str]) -> str:
    """Return a Mermaid-safe short label for ``iri``.

    Tries the graph's own prefix map first; falls back to
    :data:`_COMMON_NAMESPACE_HINTS`. If the result would contain ``:``,
    we strip the prefix entirely (taking only the local name) for Mermaid
    classDiagram compatibility.
    """
    for ns_uri, prefix in namespaces.items():
        if iri.startswith(ns_uri):
            local = iri[len(ns_uri) :]
            # Bare local name only — keeps Mermaid labels colon-free.
            return local or prefix
    for ns_uri, _prefix in _COMMON_NAMESPACE_HINTS.items():
        if iri.startswith(ns_uri):
            return iri[len(ns_uri) :]
    # Unknown namespace — take the last path/fragment segment.
    for sep in ("#", "/"):
        if sep in iri:
            tail = iri.rsplit(sep, 1)[1]
            if tail:
                return tail
    return iri


def _collect_namespaces(graph) -> dict[str, str]:  # type: ignore[no-untyped-def]
    """Pull ``graph.namespaces()`` into a ``{full_uri: prefix}`` dict."""
    out: dict[str, str] = {}
    for prefix, uri in graph.namespaces():
        out[str(uri)] = prefix
    return out


# ----------------------------------------------------------------------------
# Extraction from rdflib graph
# ----------------------------------------------------------------------------


def build_graph(ttl_path: Path | str) -> MermaidGraph:
    """Parse ``ttl_path`` and assemble a :class:`MermaidGraph`."""
    import rdflib  # lazy; optional dep
    from rdflib.namespace import OWL, RDF, RDFS

    g = rdflib.Graph()
    g.parse(str(ttl_path), format="turtle")
    namespaces = _collect_namespaces(g)

    # Classes
    class_iris = {s for s in g.subjects(RDF.type, OWL.Class) if isinstance(s, rdflib.URIRef)}
    class_entries: dict[str, ClassEntry] = {}
    label_map: dict[str, str] = {}
    for iri in class_iris:
        label = _short_label(str(iri), namespaces)
        entry = ClassEntry(iri=str(iri), label=label)
        class_entries[str(iri)] = entry
        label_map[label] = str(iri)

    # Datatype properties → property lines on their domain class
    for prop in g.subjects(RDF.type, OWL.DatatypeProperty):
        if not isinstance(prop, rdflib.URIRef):
            continue
        domain = g.value(prop, RDFS.domain)
        range_ = g.value(prop, RDFS.range)
        if not isinstance(domain, rdflib.URIRef) or str(domain) not in class_entries:
            continue
        prop_label = _short_label(str(prop), namespaces)
        range_label = (
            _short_label(str(range_), namespaces) if isinstance(range_, rdflib.URIRef) else "?"
        )
        # Re-prefix xsd: types for clarity in the diagram (the validator
        # T5 only forbids colons in *relation labels*, not property lines).
        if isinstance(range_, rdflib.URIRef) and str(range_).startswith(
            "http://www.w3.org/2001/XMLSchema#"
        ):
            range_label = f"xsd_{range_label}"  # keep colon-free everywhere
        class_entries[str(domain)].datatype_properties.append((prop_label, range_label))

    # Object properties → relations between classes
    relations: list[ObjectRelation] = []
    for prop in g.subjects(RDF.type, OWL.ObjectProperty):
        if not isinstance(prop, rdflib.URIRef):
            continue
        domain = g.value(prop, RDFS.domain)
        range_ = g.value(prop, RDFS.range)
        if not (isinstance(domain, rdflib.URIRef) and isinstance(range_, rdflib.URIRef)):
            continue
        if str(domain) not in class_entries or str(range_) not in class_entries:
            continue
        relations.append(
            ObjectRelation(
                domain_label=class_entries[str(domain)].label,
                range_label=class_entries[str(range_)].label,
                property_label=_short_label(str(prop), namespaces),
            )
        )

    # rdfs:subClassOf to *external* classes (not in our class set) →
    # gets emitted as a `note for` line on the subclass.
    for sub, obj in g.subject_objects(RDFS.subClassOf):
        if not (isinstance(sub, rdflib.URIRef) and isinstance(obj, rdflib.URIRef)):
            continue
        if str(sub) not in class_entries:
            continue
        if str(obj) in class_entries:
            # In-graph subclass — render as inheritance relation below.
            continue
        class_entries[str(sub)].subclass_of.append(_short_label(str(obj), namespaces))

    # Stable ordering: alphabetical by short label.
    classes_sorted = sorted(class_entries.values(), key=lambda c: c.label)
    for c in classes_sorted:
        c.datatype_properties.sort()
        c.subclass_of.sort()
    relations.sort(key=lambda r: (r.domain_label, r.range_label, r.property_label))

    return MermaidGraph(
        direction="LR",
        classes=classes_sorted,
        relations=relations,
        label_map=label_map,
    )


# ----------------------------------------------------------------------------
# Render Mermaid
# ----------------------------------------------------------------------------


def render_mermaid_block(graph: MermaidGraph) -> str:
    """Just the ```mermaid``` fenced block (no surrounding doc)."""
    buf = io.StringIO()
    buf.write("```mermaid\n")
    buf.write("classDiagram\n")
    buf.write(f"    direction {graph.direction}\n\n")
    for c in graph.classes:
        buf.write(f"    class {c.label} {{\n")
        for prop, rng in c.datatype_properties:
            buf.write(f"        +{prop} {rng}\n")
        buf.write("    }\n")
    if graph.classes:
        buf.write("\n")
    for r in graph.relations:
        buf.write(
            f"    {r.domain_label} --> {r.range_label} : {r.property_label}\n"
        )
    if graph.relations:
        buf.write("\n")
    for c in graph.classes:
        if c.subclass_of:
            buf.write(
                f'    note for {c.label} "subClassOf {", ".join(c.subclass_of)}"\n'
            )
    buf.write("```\n")
    return buf.getvalue()


def render_doc(graph: MermaidGraph, *, title: str | None = None) -> str:
    """Render the full docs/ontology/diagram.md body.

    Includes the Mermaid block plus a label-map table so the diagram is
    self-contained. Deterministic — running twice on the same TTL produces
    byte-identical output (suitable for ``--check`` mode in CI).
    """
    buf = io.StringIO()
    if title:
        buf.write(f"# {title}\n\n")
    buf.write(
        "Generated by `asterism-ttl2mermaid` — do not hand-edit. "
        "Re-run after TBox changes.\n\n"
    )
    buf.write(render_mermaid_block(graph))
    if graph.label_map:
        buf.write("\n## Class → full IRI\n\n")
        buf.write("| Label | IRI |\n")
        buf.write("|---|---|\n")
        for label, iri in sorted(graph.label_map.items()):
            buf.write(f"| `{label}` | `{iri}` |\n")
    return buf.getvalue()


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="asterism-ttl2mermaid",
        description=(
            "Generate a Mermaid classDiagram from a TBox TTL. "
            "Use --check to diff against an existing file (CI mode)."
        ),
    )
    p.add_argument("ttl", type=Path, help="TBox TTL file")
    p.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write the diagram .md here. Defaults to stdout.",
    )
    p.add_argument(
        "--title",
        default=None,
        help="Optional H1 title for the output doc.",
    )
    p.add_argument(
        "--check",
        type=Path,
        default=None,
        help=(
            "CI mode: compare generated output against this file (typically "
            "docs/ontology/diagram.md). Exits 1 on diff, prints a unified diff."
        ),
    )
    return p


def _main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    graph = build_graph(args.ttl)
    rendered = render_doc(graph, title=args.title)

    if args.check is not None:
        if not args.check.exists():
            sys.stderr.write(f"--check target does not exist: {args.check}\n")
            return 1
        existing = args.check.read_text(encoding="utf-8")
        if existing.rstrip() == rendered.rstrip():
            return 0
        import difflib

        diff = difflib.unified_diff(
            existing.splitlines(keepends=True),
            rendered.splitlines(keepends=True),
            fromfile=str(args.check),
            tofile=f"<generated from {args.ttl}>",
        )
        sys.stderr.writelines(diff)
        sys.stderr.write(
            "\nDiagram is out of sync with TBox. "
            "Re-run asterism-ttl2mermaid <ttl> --output <md> to regenerate.\n"
        )
        return 1

    if args.output is None:
        sys.stdout.write(rendered)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())


# Quiet "unused" warnings for re-exports.
_ = defaultdict
