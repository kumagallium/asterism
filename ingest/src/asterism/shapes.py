"""Data shape checks — does the INGESTED graph match what the design declared?

Why this module exists
----------------------
Asterism's existing validation stops at the design boundary:

* :mod:`asterism_step0.validate` (T1-T10) checks the design documents against
  *each other*;
* :mod:`asterism.rml_validate` checks the design against the *source files* —
  the columns exist, the Tier 0 signatures line up, the entities are linked.

Neither looks at the graph that came out the other end. A mapping can pass both
gates, materialize without a single exception, and still produce a graph that
does not say what the design said it would:

* a declared predicate that materialized **zero** times (a transform that
  returned empty for every row, a column that is present but always blank);
* a link whose object IRI **does not exist** in the graph (parent and child
  templates drifted apart, or the key was normalized on one side only) —
  the connectivity advisory checks that a link is *declared*, not that it
  *lands*;
* a link that lands on the **wrong class**;
* a literal that does not carry the **declared datatype**.

All four break Ask silently: the query returns *nothing*, and the reader blames
the question rather than the data.

How it works
------------
:func:`compile_shapes` derives node shapes deterministically from the RML that
actually produced the data (no LLM, no heuristics beyond RML's own structure).
:func:`shape_check_queries` turns them into read-only SPARQL, and
:func:`run_shape_checks` executes those against the dataset's named graph
through an injected ``SELECT``/``ASK`` runner — the store already has the data
indexed, so this scales to graphs no in-process SHACL engine could load.
:func:`shapes_to_shacl` emits the same shapes as standard SHACL for anyone who
wants to run them elsewhere.

The findings are ADVISORY: they never block an ingest or a promote (ADR
``data-shape-checks.md`` §D4). Their messages carry fixed marker phrases so the
UI can classify them into plain language without parsing prose.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

from asterism import rml_validate as _rv

_R2RML = "http://www.w3.org/ns/r2rml#"
_RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
_XSD_STRING = "http://www.w3.org/2001/XMLSchema#string"
_SH = "http://www.w3.org/ns/shacl#"

_DATATYPE_PREDS = (_R2RML + "datatype", "http://w3id.org/rml/datatype")
_TERM_TYPE_PREDS = (_R2RML + "termType", "http://w3id.org/rml/termType")
_PREDICATE_PREDS = (_R2RML + "predicate", "http://w3id.org/rml/predicate")
_PREDICATE_MAP_PREDS = (_R2RML + "predicateMap", "http://w3id.org/rml/predicateMap")
_OBJECT_MAP_PREDS = (_R2RML + "objectMap", "http://w3id.org/rml/objectMap")
_POM_PREDS = (_R2RML + "predicateObjectMap", "http://w3id.org/rml/predicateObjectMap")

#: How many offending examples a check reports. Small on purpose: naming a few
#: broken IRIs is what makes a finding actionable; a count is not (ADR §D2).
_EXAMPLE_LIMIT = 5

# Marker phrases the UI classifies on (ui/src/advisoryPlain.ts). Deterministic —
# never model output — so the match is stable.
MARKER_MISSING = "declared but MISSING in the ingested data"
MARKER_DANGLING = "DANGLING reference"
MARKER_WRONG_CLASS = "WRONG class"
MARKER_DATATYPE = "datatype MISMATCH"


@dataclass(frozen=True)
class PropertyShape:
    """One predicate as the design declares it, on one class."""

    predicate: str
    kind: str  # "iri" | "literal"
    datatype: str | None = None
    #: Classes the object is expected to have, when the design says so (a join
    #: to another TriplesMap, or an object template equal to another map's
    #: subject template). Empty when the design does not pin the target down.
    target_classes: tuple[str, ...] = ()


@dataclass(frozen=True)
class NodeShape:
    """One class, with the predicates the design declares on it."""

    class_iri: str
    label: str
    properties: tuple[PropertyShape, ...] = ()


@dataclass(frozen=True)
class ShapeFinding:
    """One way the ingested data departs from the design."""

    kind: str  # "predicate-missing" | "dangling-reference" | "class-mismatch" | "datatype-mismatch"
    class_iri: str
    predicate: str
    message: str
    examples: tuple[str, ...] = ()
    truncated: bool = False


# ----------------------------------------------------------------------------
# Compile: RML -> node shapes
# ----------------------------------------------------------------------------


@dataclass
class _MapInfo:
    classes: tuple[str, ...] = ()
    subject_template: str | None = None
    properties: list[PropertyShape] = field(default_factory=list)


def _first(graph, subject, preds: Sequence[str]):
    import rdflib

    for pred in preds:
        for obj in graph.objects(subject, rdflib.URIRef(pred)):
            return obj
    return None


def _all(graph, subject, preds: Sequence[str]) -> list:
    import rdflib

    seen: list = []
    for pred in preds:
        for obj in graph.objects(subject, rdflib.URIRef(pred)):
            if obj not in seen:
                seen.append(obj)
    return seen


def compile_shapes(rml_ttl: str) -> tuple[NodeShape, ...]:
    """Derive node shapes from a declarative RML mapping. Deterministic.

    One :class:`NodeShape` per class the mapping mints. Maps that declare no
    ``rr:class`` contribute no shape of their own (there is nothing to key a
    check on), but they still participate as *link targets*: a join to such a
    map yields a property with no ``target_classes``, so S3 (wrong class) stays
    silent for it while S2 (dangling) still applies.

    Never raises on a malformed mapping — an unparseable RML yields ``()``.
    """
    import rdflib

    graph = rdflib.Graph()
    try:
        graph.parse(data=rml_ttl, format="turtle")
    except Exception:
        return ()

    maps: dict = {}
    for tm in _rv._triples_map_subjects(graph):
        info = _MapInfo()
        for sm in _all(graph, tm, (_R2RML + "subjectMap",)):
            info.classes = tuple(
                sorted({str(c) for c in _all(graph, sm, (_R2RML + "class",))})
            )
            info.subject_template = _rv._effective_template(graph, sm)
        maps[tm] = info

    # subject template -> classes, so an object template that reuses another
    # map's subject template resolves to that map's classes (the same link
    # detection the connectivity advisory uses, ADR §D1).
    by_template: dict[str, set[str]] = {}
    for info in maps.values():
        if info.subject_template:
            by_template.setdefault(info.subject_template, set()).update(info.classes)

    for tm, info in maps.items():
        for pom in _all(graph, tm, _POM_PREDS):
            predicates = [str(p) for p in _all(graph, pom, _PREDICATE_PREDS)]
            for pm in _all(graph, pom, _PREDICATE_MAP_PREDS):
                const = _first(graph, pm, _rv._CONSTANT_PREDS)
                if const is not None:
                    predicates.append(str(const))
            if not predicates:
                continue
            for om in _all(graph, pom, _OBJECT_MAP_PREDS):
                shape_kind, datatype, targets = _object_shape(graph, om, maps, by_template)
                for pred in predicates:
                    info.properties.append(
                        PropertyShape(
                            predicate=pred,
                            kind=shape_kind,
                            datatype=datatype,
                            target_classes=targets,
                        )
                    )

    # Several maps may mint the same class (one class fed by two sources) —
    # union their properties into one shape.
    shapes: dict[str, dict] = {}
    for info in maps.values():
        for cls in info.classes:
            entry = shapes.setdefault(cls, {"label": _rv._local_name(cls), "props": {}})
            for prop in info.properties:
                # De-duplicate on (predicate, kind, datatype); union the targets
                # so a predicate reachable to two classes accepts both.
                key = (prop.predicate, prop.kind, prop.datatype)
                existing = entry["props"].get(key)
                if existing is None:
                    entry["props"][key] = prop
                elif prop.target_classes and existing.target_classes:
                    entry["props"][key] = PropertyShape(
                        predicate=prop.predicate,
                        kind=prop.kind,
                        datatype=prop.datatype,
                        target_classes=tuple(
                            sorted(set(existing.target_classes) | set(prop.target_classes))
                        ),
                    )
                elif not prop.target_classes:
                    # One binding leaves the target open -> the union is open.
                    entry["props"][key] = prop

    out = []
    for cls in sorted(shapes):
        entry = shapes[cls]
        props = sorted(
            entry["props"].values(), key=lambda p: (p.predicate, p.kind, p.datatype or "")
        )
        out.append(
            NodeShape(class_iri=cls, label=entry["label"], properties=tuple(props))
        )
    return tuple(out)


def _object_shape(graph, om, maps: dict, by_template: dict[str, set[str]]):
    """``(kind, datatype, target_classes)`` for one object map."""
    term_type = _first(graph, om, _TERM_TYPE_PREDS)
    datatype = _first(graph, om, _DATATYPE_PREDS)

    parent = _first(graph, om, (_R2RML + "parentTriplesMap",))
    if parent is not None:
        info = maps.get(parent)
        return "iri", None, tuple(info.classes) if info else ()

    template = _rv._effective_template(graph, om)
    if template is not None and template in by_template:
        return "iri", None, tuple(sorted(by_template[template]))

    if datatype is not None:
        return "literal", str(datatype), ()
    if term_type is not None:
        local = _rv._local_name(str(term_type))
        if local == "IRI":
            return "iri", None, ()
        return "literal", None, ()
    if template is not None:
        # A template with no termType defaults to an IRI in R2RML/RML.
        return "iri", None, ()
    return "literal", None, ()


# ----------------------------------------------------------------------------
# Check: shapes -> SPARQL -> findings
# ----------------------------------------------------------------------------


@dataclass(frozen=True)
class ShapeQuery:
    """One check, ready to run: the SPARQL plus what a hit would mean."""

    kind: str
    class_iri: str
    predicate: str
    query: str
    form: str  # "ask" | "select"
    datatype: str | None = None
    target_classes: tuple[str, ...] = ()


def _iri(value: str) -> str:
    return "<" + value.replace(">", "%3E") + ">"


def class_presence_query(graph_iri: str, class_iri: str) -> str:
    """Does the graph hold any instance of this class? (S1's precondition.)"""
    return f"ASK {{ GRAPH {_iri(graph_iri)} {{ ?s <{_RDF_TYPE}> {_iri(class_iri)} }} }}"


def shape_check_queries(
    shapes: Iterable[NodeShape], graph_iri: str
) -> tuple[ShapeQuery, ...]:
    """Every check for ``shapes``, as read-only SPARQL scoped to one graph."""
    out: list[ShapeQuery] = []
    for shape in shapes:
        cls, gi = _iri(shape.class_iri), _iri(graph_iri)
        for prop in shape.properties:
            pred = _iri(prop.predicate)
            base = f"GRAPH {gi} {{ ?s <{_RDF_TYPE}> {cls} ; {pred} ?o ."
            # S1 — the design declares it; did it ever materialize?
            out.append(
                ShapeQuery(
                    kind="predicate-missing",
                    class_iri=shape.class_iri,
                    predicate=prop.predicate,
                    form="ask",
                    query=f"ASK {{ {base} }} }}",
                )
            )
            if prop.kind == "iri":
                # S2 — the object IRI is never a subject anywhere in the graph.
                out.append(
                    ShapeQuery(
                        kind="dangling-reference",
                        class_iri=shape.class_iri,
                        predicate=prop.predicate,
                        form="select",
                        query=(
                            f"SELECT DISTINCT ?o WHERE {{ {base} FILTER(isIRI(?o)) "
                            f"FILTER NOT EXISTS {{ ?o ?anyp ?anyo }} }} }} "
                            f"LIMIT {_EXAMPLE_LIMIT + 1}"
                        ),
                    )
                )
                if prop.target_classes:
                    allowed = ", ".join(_iri(c) for c in prop.target_classes)
                    # S3 — it is typed, but not as any class the design expects.
                    out.append(
                        ShapeQuery(
                            kind="class-mismatch",
                            class_iri=shape.class_iri,
                            predicate=prop.predicate,
                            target_classes=prop.target_classes,
                            form="select",
                            query=(
                                f"SELECT DISTINCT ?o WHERE {{ {base} FILTER(isIRI(?o)) "
                                f"?o <{_RDF_TYPE}> ?t . "
                                f"FILTER NOT EXISTS {{ ?o <{_RDF_TYPE}> ?ok . "
                                f"FILTER(?ok IN ({allowed})) }} }} }} "
                                f"LIMIT {_EXAMPLE_LIMIT + 1}"
                            ),
                        )
                    )
            elif prop.datatype and prop.datatype != _XSD_STRING:
                # S4 — the declared datatype is not what the literals carry.
                # xsd:string is excluded: a plain literal and an xsd:string
                # literal are the same term in RDF 1.1, and stores differ in
                # which form they report.
                out.append(
                    ShapeQuery(
                        kind="datatype-mismatch",
                        class_iri=shape.class_iri,
                        predicate=prop.predicate,
                        datatype=prop.datatype,
                        form="select",
                        query=(
                            f"SELECT DISTINCT ?o WHERE {{ {base} FILTER(isLiteral(?o) "
                            f"&& datatype(?o) != {_iri(prop.datatype)}) }} }} "
                            f"LIMIT {_EXAMPLE_LIMIT + 1}"
                        ),
                    )
                )
    return tuple(out)


def _finding_message(check: ShapeQuery, examples: Sequence[str], truncated: bool) -> str:
    """The advisory text. English + fixed marker phrase (ADR §D5) — the UI
    translates it into one plain sentence; the raw line stays available and is
    what the one-click AI fix receives."""
    cls = _rv._local_name(check.class_iri)
    pred = _rv._local_name(check.predicate)
    shown = ", ".join(examples[:_EXAMPLE_LIMIT])
    more = " (and more)" if truncated else ""
    if check.kind == "predicate-missing":
        return (
            f"{cls}.{pred} is {MARKER_MISSING}: the mapping declares this predicate "
            f"and instances of {cls} exist, but not one carries it. Either the "
            "source column is empty for every row, or a transform returned nothing "
            f"— check the {pred} row in the §9 mapping spec against the real data "
            "before relying on it in a query."
        )
    if check.kind == "dangling-reference":
        return (
            f"{cls}.{pred} is a {MARKER_DANGLING}: it points at IRIs that appear "
            f"nowhere else in this dataset's graph — e.g. {shown}{more}. The link "
            "was materialized but its target was not, so no query can follow it. "
            "Usually the two sides mint the key differently (one side normalizes / "
            "pads / prefixes it and the other does not) — make both sides build the "
            "IRI from the same transformed value."
        )
    if check.kind == "class-mismatch":
        expected = ", ".join(_rv._local_name(c) for c in check.target_classes)
        return (
            f"{cls}.{pred} links to the {MARKER_WRONG_CLASS}: the design expects "
            f"{expected}, but these objects are typed as something else — e.g. "
            f"{shown}{more}. Either the link points at the wrong entity, or the "
            "expected class is wrong in the design."
        )
    return (
        f"{cls}.{pred} has a {MARKER_DATATYPE}: the mapping declares "
        f"{_rv._local_name(check.datatype or '')} but the ingested literals carry "
        f"a different type — e.g. {shown}{more}. A numeric comparison or a unit "
        "conversion on this predicate will silently return nothing."
    )


def evaluate_shape_result(
    check: ShapeQuery, rows: Sequence[str], present: bool = True
) -> ShapeFinding | None:
    """Turn one query's result into a finding, or None when the data is fine.

    ``rows`` — the object IRIs/literals a ``select`` check returned (empty for
    an ``ask`` check). ``present`` — for ``predicate-missing``, whether the class
    has any instance at all (a class with zero instances says nothing about the
    predicate, so the check is skipped rather than reported).
    """
    if check.kind == "predicate-missing":
        if not present or rows:
            return None
        return ShapeFinding(
            kind=check.kind,
            class_iri=check.class_iri,
            predicate=check.predicate,
            message=_finding_message(check, (), False),
        )
    if not rows:
        return None
    truncated = len(rows) > _EXAMPLE_LIMIT
    examples = tuple(rows[:_EXAMPLE_LIMIT])
    return ShapeFinding(
        kind=check.kind,
        class_iri=check.class_iri,
        predicate=check.predicate,
        message=_finding_message(check, examples, truncated),
        examples=examples,
        truncated=truncated,
    )


def _binding_values(result: object) -> list[str]:
    """Object values out of a SPARQL JSON results document (tolerant of shape)."""
    if not isinstance(result, dict):
        return []
    results = result.get("results")
    if not isinstance(results, dict):
        return []
    bindings = results.get("bindings")
    if not isinstance(bindings, list):
        return []
    out: list[str] = []
    for row in bindings:
        if not isinstance(row, dict):
            continue
        for cell in row.values():
            if isinstance(cell, dict) and "value" in cell:
                out.append(str(cell["value"]))
                break
    return out


def _ask_value(result: object) -> bool:
    if isinstance(result, dict) and isinstance(result.get("boolean"), bool):
        return bool(result["boolean"])
    # Some runners answer an ASK as a one-row SELECT — treat any row as true.
    return bool(_binding_values(result))


async def run_shape_checks(
    shapes: Iterable[NodeShape],
    graph_iri: str,
    run_query: Callable[[str], object],
    *,
    max_queries: int = 400,
) -> list[ShapeFinding]:
    """Run every check against ``graph_iri`` and return the findings.

    ``run_query`` is an awaitable taking one SPARQL string and returning the
    store's JSON results document (``OxigraphClient.sparql_select`` fits).
    Best-effort throughout: a query that errors is skipped, never raised —
    these findings are advice, and advice must not break the page that shows it.

    ``max_queries`` bounds the work on a very wide design (checks are cheap and
    LIMIT-bounded, but a 100-class schema should not fire thousands of them).
    """
    shapes = list(shapes)
    checks = shape_check_queries(shapes, graph_iri)
    findings: list[ShapeFinding] = []
    presence: dict[str, bool] = {}
    fired = 0
    for check in checks:
        if fired >= max_queries:
            break
        try:
            if check.kind == "predicate-missing":
                if check.class_iri not in presence:
                    fired += 1
                    presence[check.class_iri] = _ask_value(
                        await run_query(class_presence_query(graph_iri, check.class_iri))
                    )
                if not presence[check.class_iri]:
                    continue
                fired += 1
                has_any = _ask_value(await run_query(check.query))
                finding = evaluate_shape_result(
                    check, ["x"] if has_any else [], present=True
                )
            else:
                fired += 1
                rows = _binding_values(await run_query(check.query))
                finding = evaluate_shape_result(check, rows)
        except Exception:
            continue
        if finding is not None:
            findings.append(finding)
    return findings


# ----------------------------------------------------------------------------
# Export: shapes -> standard SHACL
# ----------------------------------------------------------------------------


def shapes_to_shacl(shapes: Iterable[NodeShape], *, base: str = "urn:asterism:shape:") -> str:
    """The same shapes as a standard SHACL Turtle document (ADR §D3).

    Not used as the checking engine — it exists so the dataset's constraints can
    leave Asterism: pySHACL, TopBraid, or anyone else's semantic layer can run
    them unchanged. Deterministic output (sorted), so it diffs cleanly.
    """
    lines = [
        "@prefix sh: <http://www.w3.org/ns/shacl#> .",
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
        "",
        "# Generated deterministically from this dataset's RML mapping by",
        "# asterism.shapes.shapes_to_shacl — do not hand-edit; regenerate.",
        "",
    ]
    for shape in shapes:
        node = f"<{base}{shape.label}>"
        lines.append(f"{node} a sh:NodeShape ;")
        lines.append(f"    sh:targetClass {_iri(shape.class_iri)} ;")
        for i, prop in enumerate(shape.properties):
            parts = [f"        sh:path {_iri(prop.predicate)}"]
            if prop.kind == "iri":
                parts.append("        sh:nodeKind sh:IRI")
                for cls in prop.target_classes:
                    parts.append(f"        sh:class {_iri(cls)}")
            else:
                parts.append("        sh:nodeKind sh:Literal")
                if prop.datatype:
                    parts.append(f"        sh:datatype {_iri(prop.datatype)}")
            block = " ;\n".join(parts)
            terminator = " ;" if i < len(shape.properties) - 1 else " ."
            lines.append(f"    sh:property [\n{block}\n    ]{terminator}")
        if not shape.properties:
            lines[-1] = lines[-1].rstrip(" ;") + " ."
        lines.append("")
    return "\n".join(lines)
