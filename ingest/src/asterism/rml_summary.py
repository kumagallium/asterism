"""Human-readable projection of an RML mapping (the ingest-rules viewer).

Why this module exists
----------------------
The product promise is *citable facts*: every answer discloses the IRIs and the
SPARQL that produced it. The one link still missing from that chain is the
TRANSFORMATION — the RML mapping that turned source cells into those facts is an
AI-generated artifact, and "the rules are declarative so a human can vet them"
only pays off if a human can actually read them. Raw Turtle is not that reading
surface for a researcher.

:func:`summarize_rml` parses a mapping (rdflib, no Morph-KGC) and projects it
into a plain data structure the catalog UI renders as "this column → this
property (via this function)". It is deterministic and LLM-free — the same
closed-world discipline as the typed query tools — and it works for EVERY
persisted dataset regardless of how its RML was authored (Mapping-IR compiled,
legacy raw-Turtle §9, or hand-written), because it reads the mapping that
actually runs.

Anything the projector does not recognize is surfaced in ``warnings`` rather
than silently dropped — a reviewer must never mistake a partial rendering for
the whole mapping.
"""
from __future__ import annotations

from pathlib import Path

from . import rml_validate as _rv

_R2RML = "http://www.w3.org/ns/r2rml#"

# rml:iterator / rml:referenceFormulation live at either RML namespace, like the
# source/reference predicates in :mod:`asterism.rml_validate`.
_ITERATOR_PREDS = (
    "http://w3id.org/rml/iterator",
    "http://semweb.mmlab.be/ns/rml#iterator",
)
_FORMULATION_PREDS = (
    "http://w3id.org/rml/referenceFormulation",
    "http://semweb.mmlab.be/ns/rml#referenceFormulation",
)
# rmlf:inputValueMap (new ns) / fnml:inputValueMap (legacy) — the value node a
# function argument reads from.
_INPUT_VALUE_MAP_PREDS = (
    "http://w3id.org/rml/inputValueMap",
    "http://semweb.mmlab.be/ns/fnml#inputValueMap",
)
# rr:datatype / rr:termType / rr:language also exist at the new RML namespace.
_DATATYPE_PREDS = (_R2RML + "datatype", "http://w3id.org/rml/datatype")
_TERM_TYPE_PREDS = (_R2RML + "termType", "http://w3id.org/rml/termType")
_LANGUAGE_PREDS = (_R2RML + "language", "http://w3id.org/rml/language")
# rr:constant / rml:constant — the constant term map (a fixed literal or IRI).
# The Mapping-IR compiler emits the NEW RML namespace (rml:constant) for the
# constant fed to a function's inputValueMap, so both must be recognized or a
# function-argument constant reads as an unrecognized (unknown) term.
_CONSTANT_PREDS = (_R2RML + "constant", "http://w3id.org/rml/constant")


def _first(graph, subject, preds: tuple[str, ...]):
    """The first object of ``subject`` across candidate predicate IRIs, or None."""
    import rdflib

    for pred in preds:
        for obj in graph.objects(subject, rdflib.URIRef(pred)):
            return obj
    return None


def _all(graph, subject, preds: tuple[str, ...]) -> list:
    """Every object of ``subject`` across candidate predicate IRIs (de-duplicated)."""
    import rdflib

    seen: list = []
    for pred in preds:
        for obj in graph.objects(subject, rdflib.URIRef(pred)):
            if obj not in seen:
                seen.append(obj)
    return seen


def _compress(iri: str, prefixes: dict[str, str]) -> str:
    """``prefix:local`` under the longest declared namespace, else the full IRI."""
    best: tuple[int, str] | None = None
    for pref, ns in prefixes.items():
        matches = ns and iri.startswith(ns) and len(iri) > len(ns)
        if matches and (best is None or len(ns) > best[0]):
            best = (len(ns), f"{pref}:{iri[len(ns):]}" if pref else iri[len(ns):])
    return best[1] if best else iri


def _term_map_value(
    graph, node, prefixes: dict[str, str], warnings: list[str], names: dict
) -> dict:
    """Describe one term map node as ``{"kind": ..., ...}``.

    Recognized kinds, in resolution order: ``function`` (FnO execution),
    ``join`` (rr:parentTriplesMap), ``reference`` (a source column/path),
    ``template`` (an IRI/literal template with ``{ref}`` placeholders),
    ``constant``. An unrecognized node degrades to ``{"kind": "unknown"}``
    AND appends a warning — partial renderings must be visible.
    """
    import rdflib

    out: dict = {}
    datatype = _first(graph, node, _DATATYPE_PREDS)
    if datatype is not None:
        out["datatype"] = _compress(str(datatype), prefixes)
    language = _first(graph, node, _LANGUAGE_PREDS)
    if language is not None:
        out["language"] = str(language)
    term_type = _first(graph, node, _TERM_TYPE_PREDS)
    if term_type is not None:
        out["term_type"] = _rv._local_name(str(term_type))

    fe = _first(graph, node, _rv._FUNCTION_EXECUTION_PREDS)
    if fe is not None:
        fun = _first(graph, fe, _rv._FUNCTION_PREDS)
        args: list[dict] = []
        for inp in _all(graph, fe, _rv._INPUT_PREDS):
            param = _first(graph, inp, _rv._PARAMETER_PREDS)
            arg: dict = {"param": _rv._local_name(str(param)) if param is not None else "?"}
            value_node = _first(graph, inp, _INPUT_VALUE_MAP_PREDS)
            if value_node is not None:
                arg.update(_term_map_value(graph, value_node, prefixes, warnings, names))
            args.append(arg)
        args.sort(key=lambda a: str(a.get("param", "")))
        out.update(
            {
                "kind": "function",
                "function": _rv._local_name(str(fun)) if fun is not None else "?",
                "function_iri": str(fun) if fun is not None else "",
                "args": args,
            }
        )
        return out

    parent = _first(graph, node, (_R2RML + "parentTriplesMap",))
    if parent is not None:
        conditions: list[dict] = []
        for cond in _all(graph, node, (_R2RML + "joinCondition",)):
            child = _first(graph, cond, (_R2RML + "child",))
            parent_col = _first(graph, cond, (_R2RML + "parent",))
            conditions.append(
                {
                    "child": str(child) if child is not None else "?",
                    "parent": str(parent_col) if parent_col is not None else "?",
                }
            )
        conditions.sort(key=lambda c: (c["child"], c["parent"]))
        out.update(
            {
                "kind": "join",
                "parent_map": names.get(parent, _rv._local_name(str(parent))),
                "conditions": conditions,
            }
        )
        return out

    ref = _first(graph, node, _rv._REFERENCE_PREDS)
    if ref is not None:
        out.update({"kind": "reference", "reference": str(ref)})
        return out

    template = _first(graph, node, _rv._TEMPLATE_PREDS)
    if template is not None:
        out.update({"kind": "template", "template": str(template)})
        return out

    constant = _first(graph, node, _CONSTANT_PREDS)
    if constant is not None:
        is_iri = isinstance(constant, rdflib.URIRef)
        out.update(
            {
                "kind": "constant",
                "constant": _compress(str(constant), prefixes) if is_iri else str(constant),
                "constant_is_iri": is_iri,
            }
        )
        return out

    out["kind"] = "unknown"
    warnings.append(
        "an object/term map uses a form this viewer does not recognize; "
        "check the raw mapping.rml.ttl for the full definition."
    )
    return out


def _map_display_names(graph, tms: list, prefixes: dict[str, str]) -> dict:
    """A stable human name per TriplesMap: IRI local name, else its class, else map-N."""
    import rdflib

    names: dict = {}
    for i, tm in enumerate(tms, start=1):
        if isinstance(tm, rdflib.URIRef):
            names[tm] = _rv._local_name(str(tm))
            continue
        label = _rv._tm_label(graph, tm)
        names[tm] = label if label != "(anonymous map)" else f"map-{i}"
    return names


def summarize_rml(rml_ttl: str) -> dict:
    """Project an RML mapping into a renderable ``{"maps": [...], ...}`` structure.

    Returns ``{"maps": [], "prefixes": {}, "warnings": [...]}`` for empty or
    unparseable input — the caller renders the warning instead of a blank panel.
    Every IRI is compressed to ``prefix:local`` under the mapping's OWN declared
    prefixes (never rdflib's pre-bound guesses), with the full IRI kept alongside
    where the UI needs a tooltip/link.
    """
    import rdflib

    text = rml_ttl or ""
    prefixes: dict[str, str] = {
        (m.group(1) or ""): m.group(2) for m in _rv._TTL_PREFIX.finditer(text)
    }
    if not text.strip():
        return {"maps": [], "prefixes": prefixes, "warnings": []}

    graph = rdflib.Graph()
    try:
        graph.parse(data=text, format="turtle")
    except Exception as exc:  # the parse-error message IS the diagnostic
        return {
            "maps": [],
            "prefixes": prefixes,
            "warnings": [f"could not parse the RML mapping as Turtle: {exc}"],
        }

    warnings: list[str] = []
    tms = list(_rv._triples_map_subjects(graph))
    names = _map_display_names(graph, tms, prefixes)

    def text_order(tm) -> tuple[int, str]:
        # Approximate author order by where the map's name appears in the source
        # text (rdflib does not preserve statement order). BNode maps sort last.
        name = names[tm]
        pos = text.find(name) if isinstance(tm, rdflib.URIRef) else -1
        return (pos if pos >= 0 else len(text), name)

    maps: list[dict] = []
    for tm in sorted(tms, key=text_order):
        entry: dict = {"id": names[tm]}

        ls = _first(graph, tm, _rv._LOGICAL_SOURCE_PREDS)
        if ls is not None:
            src = _first(graph, ls, _rv._SOURCE_PREDS)
            if src is not None:
                # The registry stores design-time RML (relative sources); a
                # prepared/absolutized path degrades to its file name.
                entry["source"] = Path(str(src)).name
            iterator = _first(graph, ls, _ITERATOR_PREDS)
            if iterator is not None:
                entry["iterator"] = str(iterator)
            formulation = _first(graph, ls, _FORMULATION_PREDS)
            if formulation is not None:
                entry["formulation"] = _rv._local_name(str(formulation))

        subject: dict = {}
        sm = _first(graph, tm, (_R2RML + "subjectMap",))
        if sm is not None:
            classes = _all(graph, sm, (_R2RML + "class",))
            subject = _term_map_value(graph, sm, prefixes, warnings, names)
            subject["classes"] = sorted(
                {_compress(str(c), prefixes) for c in classes}
            )
            subject["class_iris"] = sorted({str(c) for c in classes})
            if subject.get("kind") == "unknown":
                subject.pop("kind")  # a class-only subject map is legal, not a warning
                warnings.pop()
        entry["subject"] = subject

        rows: list[dict] = []
        for pom in _all(graph, tm, (_R2RML + "predicateObjectMap",)):
            predicates = [str(p) for p in _all(graph, pom, (_R2RML + "predicate",))]
            for pm in _all(graph, pom, (_R2RML + "predicateMap",)):
                const = _first(graph, pm, _CONSTANT_PREDS)
                if const is not None:
                    predicates.append(str(const))
            values: list[dict] = []
            for om in _all(graph, pom, (_R2RML + "objectMap",)):
                values.append(_term_map_value(graph, om, prefixes, warnings, names))
            for obj in _all(graph, pom, (_R2RML + "object",)):
                values.append(
                    {
                        "kind": "constant",
                        "constant": _compress(str(obj), prefixes)
                        if isinstance(obj, rdflib.URIRef)
                        else str(obj),
                        "constant_is_iri": isinstance(obj, rdflib.URIRef),
                    }
                )
            if not predicates:
                warnings.append(
                    f"a predicate-object map in {entry['id']} has no readable "
                    "predicate; check the raw mapping.rml.ttl."
                )
                continue
            for pred in predicates:
                for value in values:
                    rows.append(
                        {
                            "predicate": _compress(pred, prefixes),
                            "predicate_iri": pred,
                            **value,
                        }
                    )

        # rdflib iteration order is not stable across runs — sort for determinism.
        rows.sort(
            key=lambda r: (
                r["predicate"],
                r.get("kind", ""),
                str(r.get("reference", "")),
                str(r.get("template", "")),
                str(r.get("constant", "")),
                str(r.get("function", "")),
                str(r.get("parent_map", "")),
            )
        )
        entry["properties"] = rows
        maps.append(entry)

    return {"maps": maps, "prefixes": prefixes, "warnings": warnings}
