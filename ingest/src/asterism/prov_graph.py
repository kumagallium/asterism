"""Generic PROV-O provenance graph reader (object-cards-ui.md §4).

``mcp.tools.provenance_of`` is a starrydata-shaped linear chain: it walks
hardcoded ``sd:`` predicates (Curve -> Sample -> Paper -> activities), so a
dataset with its own vocabulary gets nothing. This module answers the same
"where did this fact come from?" question generically: it follows only the
seven PROV-O predicates that ANY RML mapping might emit (never a dataset's own
vocabulary) and returns a directed graph an agnostic renderer can draw, instead
of a chain a specific UI already knows how to walk. ``provenance_of`` is left
untouched — this is a parallel, general-purpose read path, not a replacement.

Reads only ever touch the citable scope (promoted canonical / version graphs,
via :func:`asterism.substrate.canonical_graphs`), never draft data — the same
draft-isolation invariant every other read path holds.
"""
from __future__ import annotations

import re
from typing import Any

from asterism.substrate import (
    SupportsSparql,
    canonical_from_clauses,
    canonical_graphs,
    dataset_id_of_canonical_graph,
)

#: PROV-O namespace (never a dataset's own vocabulary — that is the point).
PROV: str = "http://www.w3.org/ns/prov#"

_RDFS_LABEL: str = "http://www.w3.org/2000/01/rdf-schema#label"
_SCHEMA_NAME_HTTP: str = "http://schema.org/name"
_SCHEMA_NAME_HTTPS: str = "https://schema.org/name"
_DCTERMS_TITLE: str = "http://purl.org/dc/terms/title"

#: A graph node's coarse shape, decided from ``rdf:type`` alone (no vocabulary
#: of any single dataset — see :func:`_kind_of`).
GRAPH_NODE_KINDS: tuple[str, ...] = ("entity", "activity", "other")

#: The seven PROV-O predicates this module follows, and the edge label each
#: becomes. Every one of them reads "backward" in PROV (``X wasGeneratedBy Y``
#: means Y happened first), so the edge is always ``object -> subject`` —
#: renderers get data/time-flow arrows without needing to know PROV itself.
_EDGE_LABELS: dict[str, str] = {
    PROV + "wasGeneratedBy": "generated",
    PROV + "used": "used",
    PROV + "wasDerivedFrom": "derived",
    PROV + "wasAttributedTo": "attributed",
    PROV + "wasAssociatedWith": "associated",
    PROV + "wasInformedBy": "informed",
    PROV + "wasQuotedFrom": "quoted",
}

_AGENT_TYPES: frozenset[str] = frozenset(
    {
        PROV + "Agent",
        PROV + "Person",
        PROV + "Organization",
        PROV + "SoftwareAgent",
    }
)

#: A version graph's trailing ``/v{n}`` segment (part5: ``canonical/{id}/v{n}``).
_VERSION_SUFFIX = re.compile(r"/v(\d+)$")


def _safe_iri(iri: str) -> str:
    """Strip ``<``/``>`` before embedding ``iri`` in a query (defence in depth —
    every IRI here already came from either a validated caller input or the
    store's own output, but a query template must never trust that blindly)."""
    return iri.replace("<", "").replace(">", "")


def _validate_iri(iri: str) -> None:
    if not iri or not iri.startswith(("http://", "https://")) or "<" in iri or ">" in iri:
        raise ValueError(f"iri must be a full http(s) IRI, got {iri!r}")


def _local_name(iri: str) -> str:
    """The part after the last ``#`` or ``/`` — never the raw IRI itself (K4:
    a citable label must read as a name, not an opaque identifier)."""
    return iri.rsplit("#", 1)[-1].rsplit("/", 1)[-1] or iri


def _snapshot_of(graph_iri: str) -> str | None:
    m = _VERSION_SUFFIX.search(graph_iri)
    return f"v{m.group(1)}" if m else None


async def _run_select(client: SupportsSparql, query: str) -> list[dict[str, dict[str, Any]]]:
    raw = await client.sparql_select(query)
    results = raw.get("results", {}) if isinstance(raw, dict) else {}
    return results.get("bindings", []) if isinstance(results, dict) else []


def _cell(row: dict[str, dict[str, Any]], var: str) -> str | None:
    node = row.get(var)
    return node.get("value") if node else None


def _exists_query(from_clause: str, iri: str) -> str:
    """Per-graph count of triples where ``iri`` is the SUBJECT, used to both
    detect existence and to attribute the origin node to whichever version
    graph actually has the most facts recorded about it — not whichever graph
    happens to sort first alphabetically (a node quoted once from a foreign
    dataset must not steal attribution from its home graph, which may hold many
    more triples about it). Ties broken by graph IRI (deterministic).

    Deliberately scoped to the subject role only, matching how a version graph
    "owns" a node (asserts facts about it) rather than merely mentioning it as
    an object. Callers that get no rows here still fall back to an object-role
    existence check (see :func:`_exists_as_object_query`) — a node cited only
    as an object anywhere still exists and should read ``found: true``.
    """
    safe = _safe_iri(iri)
    return (
        "SELECT ?g (COUNT(*) AS ?cnt)\n"
        f"{from_clause}"
        "WHERE {\n"
        f"  BIND(<{safe}> AS ?n)\n"
        "  GRAPH ?g {\n"
        "    ?n ?__prov_p ?__prov_o .\n"
        "  }\n"
        "}\n"
        "GROUP BY ?g\n"
        "ORDER BY DESC(?cnt) ?g\n"
    )


def _exists_as_object_query(from_clause: str, iri: str) -> str:
    """Fallback existence check for a node that is never a subject anywhere
    (e.g. cited only as the object of someone else's triple). Graph choice here
    has no "ownership" signal to go on, so it is deterministic (IRI order)
    rather than meaningful attribution."""
    safe = _safe_iri(iri)
    return (
        "SELECT ?g\n"
        f"{from_clause}"
        "WHERE {\n"
        f"  BIND(<{safe}> AS ?n)\n"
        "  GRAPH ?g {\n"
        "    ?__prov_s ?__prov_p ?n .\n"
        "  }\n"
        "}\n"
        "ORDER BY ?g\n"
        "LIMIT 1\n"
    )


def _upstream_step_query(from_clause: str, frontier: list[str]) -> str:
    """One step of the *upstream* walk (away from origin, toward how it came to
    be): every PROV-O triple where a ``frontier`` node is the SUBJECT — never
    the object. Querying a frontier node's object role too (as an earlier
    version of this walk did) climbs straight into "what else did this
    activity produce" — the sibling wall (ADR object-cards-ui.md O29) this
    module must not cross. Following only the subject role keeps the walk to
    the origin's own genealogy.

    ``ORDER BY`` makes row order store-independent: when ``max_nodes`` cuts a
    step short mid-way, which node survives must be deterministic, not an
    artifact of the store's internal iteration order.
    """
    values = " ".join(f"<{_safe_iri(n)}>" for n in frontier)
    preds = " ".join(f"<{p}>" for p in _EDGE_LABELS)
    return (
        "SELECT ?g ?n ?pred ?o\n"
        f"{from_clause}"
        "WHERE {\n"
        f"  VALUES ?pred {{ {preds} }}\n"
        f"  VALUES ?n {{ {values} }}\n"
        "  GRAPH ?g {\n"
        "    ?n ?pred ?o .\n"
        "  }\n"
        "}\n"
        "ORDER BY ?n ?pred ?o\n"
    )


#: The three PROV-O predicates whose OBJECT position points straight at a
#: node the walk allows exactly one downstream hop from: the origin itself
#: (O29). ``wasGeneratedBy`` is deliberately absent — an entity generated by
#: the same activity as the origin is a sibling, not a descendant, and O29
#: forbids reaching it from either direction.
_DOWNSTREAM_PREDS: tuple[str, ...] = (
    PROV + "wasDerivedFrom",
    PROV + "used",
    PROV + "wasQuotedFrom",
)


def _downstream_step_query(from_clause: str, origin: str) -> str:
    """The single downstream hop this module allows: things that cite
    ``origin`` directly via ``wasDerivedFrom``/``used``/``wasQuotedFrom``
    (``origin`` as OBJECT). Callers must not feed the nodes this returns back
    in as a new frontier — O29 allows this hop from the origin only, never
    onward from what it finds.
    """
    safe = _safe_iri(origin)
    preds = " ".join(f"<{p}>" for p in _DOWNSTREAM_PREDS)
    return (
        "SELECT ?g ?x ?pred\n"
        f"{from_clause}"
        "WHERE {\n"
        f"  VALUES ?pred {{ {preds} }}\n"
        "  GRAPH ?g {\n"
        f"    ?x ?pred <{safe}> .\n"
        "  }\n"
        "}\n"
        "ORDER BY ?pred ?x\n"
    )


def _info_query(from_clause: str, nodes: list[str]) -> str:
    """Type / label-candidate / time literals for every discovered node, in one
    round trip. Each UNION branch keeps its own ``?prop`` tag so label priority
    (rdfs:label > schema:name > dcterms:title) stays a Python decision, not a
    SPARQL one — the query only pulls candidates, it never picks among them."""
    values = " ".join(f"<{_safe_iri(n)}>" for n in nodes)
    return (
        "SELECT ?n ?prop ?val ?lang\n"
        f"{from_clause}"
        "WHERE {\n"
        f"  VALUES ?n {{ {values} }}\n"
        "  {\n"
        "    ?n a ?val .\n"
        '    BIND("type" AS ?prop)\n'
        "  } UNION {\n"
        f"    ?n <{_RDFS_LABEL}> ?val .\n"
        '    BIND("rdfs_label" AS ?prop)\n'
        "    BIND(LANG(?val) AS ?lang)\n"
        "  } UNION {\n"
        f"    ?n <{_SCHEMA_NAME_HTTP}> ?val .\n"
        '    BIND("schema_name" AS ?prop)\n'
        "    BIND(LANG(?val) AS ?lang)\n"
        "  } UNION {\n"
        f"    ?n <{_SCHEMA_NAME_HTTPS}> ?val .\n"
        '    BIND("schema_name" AS ?prop)\n'
        "    BIND(LANG(?val) AS ?lang)\n"
        "  } UNION {\n"
        f"    ?n <{_DCTERMS_TITLE}> ?val .\n"
        '    BIND("dcterms_title" AS ?prop)\n'
        "    BIND(LANG(?val) AS ?lang)\n"
        "  } UNION {\n"
        f"    ?n <{PROV}startedAtTime> ?val .\n"
        '    BIND("started_at" AS ?prop)\n'
        "  } UNION {\n"
        f"    ?n <{PROV}endedAtTime> ?val .\n"
        '    BIND("ended_at" AS ?prop)\n'
        "  } UNION {\n"
        f"    ?n <{PROV}atTime> ?val .\n"
        '    BIND("at_time" AS ?prop)\n'
        "  }\n"
        "}\n"
        "ORDER BY ?n ?prop ?val ?lang\n"
    )


def _type_label_query(from_clause: str, type_iris: list[str]) -> str:
    """``rdfs:label`` candidates for a set of ``rdf:type`` IRIs themselves
    (not the instances) — same version-graph scope as :func:`_info_query`, a
    separate round trip because the values being labelled are different
    (class IRIs, not instance IRIs)."""
    values = " ".join(f"<{_safe_iri(t)}>" for t in type_iris)
    return (
        "SELECT ?t ?val ?lang\n"
        f"{from_clause}"
        "WHERE {\n"
        f"  VALUES ?t {{ {values} }}\n"
        f"  ?t <{_RDFS_LABEL}> ?val .\n"
        "  BIND(LANG(?val) AS ?lang)\n"
        "}\n"
        "ORDER BY ?t ?val ?lang\n"
    )


def _group_type_labels(rows: list[dict[str, dict[str, Any]]]) -> dict[str, str]:
    """Group :func:`_type_label_query` rows into ``{type_iri: label}``, using
    the same ja > en > lexicographic tie-break as :func:`_pick_label`."""
    candidates: dict[str, list[tuple[str, str]]] = {}
    for row in rows:
        t = _cell(row, "t")
        val = _cell(row, "val")
        if t is None or val is None:
            continue
        lang = _cell(row, "lang") or ""
        candidates.setdefault(t, []).append((val, lang))
    return {t: label for t, vals in candidates.items() if (label := _pick_label(vals))}


def _type_label_of(type_iri: str, type_labels: dict[str, str]) -> str:
    """A type's display label (K4: never the raw IRI) — the version graph's
    own ``rdfs:label`` for that class if any, else the IRI's local name."""
    return type_labels.get(type_iri) or _local_name(type_iri)


def _kind_of(type_iris: list[str]) -> str:
    """Coarse node shape from ``rdf:type`` alone — never a dataset vocabulary."""
    for t in type_iris:
        if t == PROV + "Activity" or _local_name(t).endswith("Activity"):
            return "activity"
    for t in type_iris:
        if t in _AGENT_TYPES:
            return "other"
    return "entity"


def _pick_label(candidates: list[tuple[str, str]]) -> str | None:
    """Among same-priority label candidates (value, lang tag), prefer ``ja``,
    then ``en``, then the lexicographically first — deterministic when a store
    holds several language-tagged labels for the same predicate."""
    if not candidates:
        return None
    ordered = sorted(candidates)
    by_lang: dict[str, str] = {}
    for val, lang in ordered:
        by_lang.setdefault(lang, val)
    for lang in ("ja", "en"):
        if lang in by_lang:
            return by_lang[lang]
    return ordered[0][0]


def _group_info(
    rows: list[dict[str, dict[str, Any]]],
) -> dict[str, dict[str, list[tuple[str, str]]]]:
    grouped: dict[str, dict[str, list[tuple[str, str]]]] = {}
    for row in rows:
        n = _cell(row, "n")
        prop = _cell(row, "prop")
        val = _cell(row, "val")
        if n is None or prop is None or val is None:
            continue
        lang = _cell(row, "lang") or ""
        grouped.setdefault(n, {}).setdefault(prop, []).append((val, lang))
    return grouped


def _node_entry(
    node_iri: str,
    info: dict[str, list[tuple[str, str]]],
    graph_iri: str | None,
    type_labels: dict[str, str],
) -> dict[str, Any]:
    types = sorted({v for v, _ in info.get("type", [])})
    rep_type = types[0] if types else None
    label = (
        _pick_label(info.get("rdfs_label", []))
        or _pick_label(info.get("schema_name", []))
        or _pick_label(info.get("dcterms_title", []))
        or (_local_name(rep_type) if rep_type else None)
        or _local_name(node_iri)
    )
    props: dict[str, str] = {}
    if rep_type:
        props["type"] = rep_type
        # 人向けの見出し（K4: 生の IRI を画面に出さない） — object-cards-ui.md §4。
        props["type_label"] = _type_label_of(rep_type, type_labels)
    if graph_iri:
        dataset_id = dataset_id_of_canonical_graph(graph_iri)
        if dataset_id:
            props["dataset_id"] = dataset_id
        snapshot = _snapshot_of(graph_iri)
        if snapshot:
            props["snapshot"] = snapshot
    for key in ("started_at", "ended_at", "at_time"):
        vals = info.get(key, [])
        if vals:
            props[key] = sorted(v for v, _ in vals)[0]
    return {"id": node_iri, "label": label, "kind": _kind_of(types), "props": props}


async def prov_graph(
    client: SupportsSparql, iri: str, *, max_depth: int = 4, max_nodes: int = 60
) -> dict[str, Any]:
    """Follow only the 7 PROV-O predicates out from ``iri`` and return a
    renderer-ready directed graph: ``{iri, found, graph, materials}``.

    Unlike ``mcp.tools.provenance_of`` (a starrydata-shaped linear chain), this
    walks no dataset vocabulary at all — it is the generic counterpart any
    dataset's data gets for free, at the cost of returning a graph instead of an
    ordered chain (the caller lays it out).

    The walk follows only ``iri``'s own lineage (ADR object-cards-ui.md O29),
    not everything the store can reach from it:

    - **Upstream** (away from origin, toward how it came to be): repeated,
      up to ``max_depth`` hops — ``wasGeneratedBy``/``wasDerivedFrom``/
      ``wasAttributedTo``/``wasQuotedFrom`` from an entity, ``used``/
      ``wasAssociatedWith``/``wasInformedBy`` from an activity.
    - **Downstream** (things that cite ``iri``): exactly **one hop** from
      ``iri`` itself via ``wasDerivedFrom``/``used``/``wasQuotedFrom``, never
      expanded further.

    Deliberately NOT walked: an activity's *other* outputs (every other
    entity it also generated) and anything past the one downstream hop. A
    real-world activity commonly generates hundreds of sibling entities in one
    batch; treating that edge as bidirectional turns "where did this fact come
    from" into a wall of unrelated siblings before the origin's own ancestry
    is even reached. BFS is bounded by ``max_depth`` (upstream round trips)
    and ``max_nodes`` (total node count); either limit cutting the walk short
    sets ``graph.truncated``.

    Reads are scoped to citable version graphs only
    (:func:`asterism.substrate.canonical_graphs`), via ``FROM NAMED`` + ``GRAPH
    ?g`` (a plain ``FROM`` merge loses which graph a triple came from, and
    ``dataset_id``/``snapshot`` attribution needs that).

    When no version graph is published yet (``canonical_graphs`` is empty), no
    SPARQL is issued at all: an empty ``FROM NAMED`` clause makes ``GRAPH ?g``
    range over every named graph in the store — draft / control / ontology
    included — which would break draft isolation. The only correct answer with
    nothing published is the same "not found" a real IRI lookup would get.
    """
    _validate_iri(iri)
    graphs = await canonical_graphs(client)
    if not graphs:
        return {"iri": iri, "found": False, "graph": {"nodes": [], "edges": []}, "materials": []}
    named_from = canonical_from_clauses(graphs, named=True)
    plain_from = canonical_from_clauses(graphs, named=False)

    exists_rows = await _run_select(client, _exists_query(named_from, iri))
    if exists_rows:
        origin_graph = _cell(exists_rows[0], "g")
    else:
        fallback_rows = await _run_select(client, _exists_as_object_query(named_from, iri))
        if not fallback_rows:
            return {
                "iri": iri,
                "found": False,
                "graph": {"nodes": [], "edges": []},
                "materials": [],
            }
        origin_graph = _cell(fallback_rows[0], "g")

    node_graph: dict[str, str | None] = {iri: origin_graph}
    edges: list[dict[str, str]] = []
    edge_seen: set[tuple[str, str, str]] = set()
    truncated = False

    # Upstream: the origin's own lineage, one subject-role step at a time.
    frontier = [iri]
    depth = 0
    while frontier:
        if depth >= max_depth:
            truncated = True
            break
        if len(node_graph) >= max_nodes:
            truncated = True
            break
        depth += 1
        rows = await _run_select(client, _upstream_step_query(named_from, frontier))
        next_frontier: list[str] = []
        for row in rows:
            n, pred, o, g = (_cell(row, k) for k in ("n", "pred", "o", "g"))
            if n is None or o is None or pred is None:
                continue
            label = _EDGE_LABELS.get(pred)
            if label is None:
                continue
            if o not in node_graph and len(node_graph) + 1 > max_nodes:
                truncated = True
                continue
            edge_key = (o, n, label)
            if edge_key not in edge_seen:
                edge_seen.add(edge_key)
                edges.append({"from": o, "to": n, "label": label})
            if o not in node_graph:
                node_graph[o] = g
                next_frontier.append(o)
            elif node_graph[o] is None and g:
                node_graph[o] = g
        frontier = next_frontier

    # Downstream: exactly one hop from the origin itself — O29 forbids
    # expanding onward from what this finds (that would re-open the sibling
    # wall from the other side).
    if len(node_graph) >= max_nodes:
        truncated = True
    else:
        rows = await _run_select(client, _downstream_step_query(named_from, iri))
        for row in rows:
            x, pred, g = (_cell(row, k) for k in ("x", "pred", "g"))
            if x is None or pred is None:
                continue
            label = _EDGE_LABELS.get(pred)
            if label is None:
                continue
            if x not in node_graph:
                if len(node_graph) >= max_nodes:
                    truncated = True
                    continue
                node_graph[x] = g
            elif node_graph[x] is None and g:
                node_graph[x] = g
            edge_key = (iri, x, label)
            if edge_key not in edge_seen:
                edge_seen.add(edge_key)
                edges.append({"from": iri, "to": x, "label": label})

    info_rows = await _run_select(client, _info_query(plain_from, list(node_graph)))
    info = _group_info(info_rows)

    all_types = sorted({v for node_info in info.values() for v, _ in node_info.get("type", [])})
    type_labels: dict[str, str] = {}
    if all_types:
        type_label_rows = await _run_select(client, _type_label_query(plain_from, all_types))
        type_labels = _group_type_labels(type_label_rows)

    nodes = [
        _node_entry(node_iri, info.get(node_iri, {}), graph_iri, type_labels)
        for node_iri, graph_iri in node_graph.items()
    ]
    nodes.sort(key=lambda n: n["id"])
    edges.sort(key=lambda e: (e["from"], e["to"], e["label"]))

    materials_by_graph: dict[str, tuple[str, str | None]] = {}
    for g in node_graph.values():
        if not g or g in materials_by_graph:
            continue
        dataset_id = dataset_id_of_canonical_graph(g)
        if not dataset_id:
            continue
        materials_by_graph[g] = (dataset_id, _snapshot_of(g))
    materials = [
        {"dataset_id": dataset_id, "snapshot": snapshot, "graph": g}
        for g, (dataset_id, snapshot) in materials_by_graph.items()
    ]
    materials.sort(key=lambda m: (m["dataset_id"], m["snapshot"] or ""))

    graph_out: dict[str, Any] = {"nodes": nodes, "edges": edges}
    if truncated:
        graph_out["truncated"] = True
    return {"iri": iri, "found": True, "graph": graph_out, "materials": materials}
