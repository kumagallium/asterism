"""Built-in (non-declared) deterministic subject/set tools (object-cards-ui.md
§3). These are the tools every class gets "for free", regardless of what a
dataset's ``query_tools.yaml`` declares: facts about one thing, where its
facts came from, its provenance flow, and the generic-over-a-class-schema
tools that back the "絞り込み" (set) page.

Every function here returns the SAME shape ``run_query_tool`` returns
(``{tool, count, items, truncated, sparql}``) plus ``output_kind`` / ``item``
(the PR A ``ItemSpec`` map — see ``ui/src/cards/viewSpec.ts``) / ``materials``
/ ``shareable`` / ``shareable_reasons`` (§3.3 — filled in for real by
:mod:`asterism.materials`, contract §2).

No LLM, no generated code, no domain vocabulary. Every literal a caller
supplies is escaped via :func:`asterism.query_tools._escape_literal`; every
IRI is validated before it is embedded. Reads are scoped to the citable
canonical (+ ontology, for labels) graphs only — the same scope every other
read path in this codebase uses (:mod:`asterism.substrate`).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

from asterism.materials import Material
from asterism.materials import materials_for as _materials_for
from asterism.materials import shareable as _shareable
from asterism.materials import shareable_reasons as _shareable_reasons
from asterism.measure_spec import MeasureSpecError, output_kind_for, validate_measure
from asterism.prov_graph import PROV as _PROV_NS
from asterism.prov_graph import prov_graph as _prov_graph
from asterism.query_tools import (
    QueryTool,
    QueryToolError,
    ToolParam,
    _escape_literal,
    load_query_tools,
    run_query_tool,
)
from asterism.subjects import (
    SetSpecError,
    label_union_clause,
    normalize_set_spec,
    pick_label,
    safe_http_iri,
    safe_iri,
    set_id_of,
)
from asterism.substrate import (
    SupportsSparql,
    canonical_from_clauses,
    canonical_graphs,
    dataset_id_of_canonical_graph,
    readable_graph_iris,
)

logger = logging.getLogger(__name__)

__all__ = [
    "INDIVIDUAL_BUILTIN_TOOLS",
    "SET_BUILTIN_TOOLS",
    "SubjectKindMismatchError",
    "SubjectToolError",
    "UnknownSubjectToolError",
    "card_id_of",
    "default_cards_for_set",
    "default_cards_for_subject",
    "iri_param_of",
    "linking_kinds",
    "materials_for_set",
    "materials_for_subject",
    "pick_class_iri",
    "run_iri_bound_tool",
    "run_subject_tool",
    "set_breakdown",
    "set_count",
    "set_measure",
    "set_members",
    "subject_facts",
    "subject_flow",
    "subject_sources",
    "subject_types",
]


class SubjectToolError(Exception):
    """A ``cards/run``-style call is malformed (api boundary → 400)."""


class UnknownSubjectToolError(SubjectToolError):
    """No such built-in or declared tool for this subject (api boundary → 404)."""


class SubjectKindMismatchError(SubjectToolError):
    """A set-only tool called on an individual subject, or vice versa (→ 400)."""


#: Built-in tool names that take ``{kind: "individual", iri}`` (§3.1).
INDIVIDUAL_BUILTIN_TOOLS: tuple[str, ...] = ("subject_facts", "subject_sources", "subject_flow")
#: Built-in tool names that take ``{kind: "set", spec}`` (§3.2) — EXCEPT
#: ``set_measure`` (PR F4 §1-4), which reads its ``class``/``where`` from
#: ``params`` instead and so runs for either subject kind (see
#: :func:`run_subject_tool`'s docstring). Still listed here: it is the
#: seam ``default_cards_for_set``/"which tool names never bind an iri"
#: callers use, not a promise every entry needs ``subject.spec``.
SET_BUILTIN_TOOLS: tuple[str, ...] = ("set_members", "set_breakdown", "set_count", "set_measure")

#: Declared-tool ``output_kind``s default cards for a subject include, and the
#: order groups appear in (§3.4 — deliberately excludes ``facts``/``flow``:
#: ``subject_facts`` already covers "facts about this thing").
_DEFAULT_CARD_KIND_ORDER: tuple[str, ...] = ("series", "pairs", "ranked", "breakdown", "quantity")

_RDFS_CLASS = "http://www.w3.org/2000/01/rdf-schema#Class"
_RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"

_XSD_PREFIX = "PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>\n"


# ----------------------------------------------------------------------------
# Small shared helpers (IRI safety, row shaping, labels)
# ----------------------------------------------------------------------------


def _safe_iri(iri: str) -> str:
    """The single embedding gate every ``_ref`` call goes through — defence
    in depth via :func:`asterism.subjects.safe_iri` (the SAME character-class
    check ``asterism.class_schema`` and the api boundary use; no duplicate
    regex here). Scheme validation (http(s)) happens once, at the api
    boundary (``asterism.subjects.safe_http_iri`` / ``_require_iri``), not
    repeated here — a store-returned object IRI need not be http(s) to be
    safe to embed. Raises :class:`SubjectToolError` (→ 400) rather than
    silently degrading, so a value that somehow reached here with an unsafe
    character never gets embedded."""
    checked = safe_iri(iri)
    if checked is None:
        raise SubjectToolError(f"unsafe IRI cannot be embedded in SPARQL: {iri!r}")
    return checked


def _ref(iri: str) -> str:
    return f"<{_safe_iri(iri)}>"


def _rows(raw: dict[str, Any]) -> list[dict[str, dict[str, Any]]]:
    results = raw.get("results", {}) if isinstance(raw, dict) else {}
    return list(results.get("bindings", []) if isinstance(results, dict) else [])


def _cell(row: dict[str, dict[str, Any]], var: str) -> str | None:
    node = row.get(var)
    return node.get("value") if node else None


def _local_name(iri: str) -> str:
    """The part after the last ``#``/``/`` — never the raw IRI (K4)."""
    return iri.rsplit("#", 1)[-1].rsplit("/", 1)[-1] or iri


# Same small humanizer ``asterism.class_schema``/``asterism.shape_match`` each
# keep a hand-synced copy of (§0: each担当ファイル別・共有 module を増やさない
# established convention in this PR) — the last-resort "readable, not a raw
# identifier" form of a local name (K4), used only when neither the ontology
# projection nor the class schema names a property.
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _humanize(local: str) -> str:
    if not local:
        return ""
    stripped = re.sub(r"^(has|is)(?=[A-Z])", "", local)
    spaced = _CAMEL_BOUNDARY.sub(" ", stripped.replace("_", " ").replace("-", " "))
    return " ".join(spaced.split())


def _fallback_label(iri: str) -> str:
    local = _local_name(iri)
    return _humanize(local) or local


def _as_number(value: str | None) -> float | str | None:
    """Best-effort numeric coercion (mirrors ``query_tools._shape_row``): a
    value that parses as a float becomes one, otherwise it is left as the
    original string. ``None`` stays ``None``."""
    if value is None:
        return None
    with contextlib.suppress(TypeError, ValueError):
        return float(value)
    return value


async def _label_lookup(
    client: SupportsSparql,
    graphs: list[str],
    iris: set[str],
    *,
    fallback: Any = None,
) -> dict[str, str]:
    """Human display name for each of ``iris``, read from the given (already
    citable) ``graphs`` in one round trip. Delegates the priority order and
    tie-breaking to :func:`asterism.subjects.label_union_clause` /
    :func:`asterism.subjects.pick_label` — the ONE shared rule (§2 of the
    contract memo: this used to be a shorter, independently-maintained
    predicate list that missed ``dcterms:title``/``skos:prefLabel``/
    ``foaf:name`` entirely). ``fallback`` (default :func:`_local_name`) is
    called for an iri no candidate matched (§6: ``subject_facts`` passes a
    fallback that tries the class schema's label before humanizing)."""
    fallback = fallback or _local_name
    if not iris or not graphs:
        return {i: fallback(i) for i in iris}
    from_clause = canonical_from_clauses(graphs)
    values = " ".join(_ref(i) for i in sorted(iris))
    optional = label_union_clause("?t", label_var="?l", predicate_var="?__lp", rank_var="?__rank")
    query = (
        f"SELECT ?t ?l ?__rank (LANG(?l) AS ?__lang)\n{from_clause}"
        f"WHERE {{ VALUES ?t {{ {values} }} {optional} }}"
    )
    candidates: dict[str, list[tuple[str | None, int | None, str | None]]] = {i: [] for i in iris}
    for row in _rows(await client.sparql_select(query)):
        t = _cell(row, "t")
        if t is None or t not in candidates:
            continue
        rank_raw = _cell(row, "__rank")
        rank = int(rank_raw) if rank_raw is not None else None
        candidates[t].append((_cell(row, "l"), rank, _cell(row, "__lang")))
    return {i: pick_label(candidates[i]) or fallback(i) for i in iris}


def _dataset_metas(registry_root: Path | str | None) -> list[dict[str, Any]]:
    """Every registry dataset's ``meta.json``, read directly (ingest layer —
    never imports the api's ``registry.py``). Best-effort: an unreadable
    ``meta.json`` is skipped, never raises."""
    if registry_root is None:
        return []
    root = Path(registry_root)
    if not root.is_dir():
        return []
    metas: list[dict[str, Any]] = []
    for child in sorted(root.iterdir()):
        meta_path = child / "meta.json"
        if not meta_path.is_file():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(meta, dict):
            metas.append(meta)
    return metas


def dataset_labels(registry_root: Path | str | None) -> dict[str, str]:
    """``{dataset_id: display name}`` from the registry (meta.name, falling
    back to the id itself — subject_sources' "無ければ dataset_id").

    Public (契約メモ contract_pr_f2.md §5): this bulk, meta.json-only lookup
    (every dataset at once) is deliberately NOT the same function as
    :func:`asterism.subjects.resolve_dataset_label` (one dataset, also reads
    ``metadata.ttl``'s ``dcterms:title`` first) — this module already reads
    every dataset's ``meta.json`` for :func:`_dataset_metas`'s other callers,
    and ``asterism.subjects`` cannot import this module back (this module
    already imports ``asterism.subjects`` — a cycle), so the two stay
    separate, each duplicating the small "meta.json name" read (§0 of this
    codebase's established shape, see ``asterism.materials._dataset_label``).
    """
    return {
        str(m.get("id")): str(m.get("name") or m.get("id"))
        for m in _dataset_metas(registry_root)
        if m.get("id")
    }


def _own_dataset_ids(registry_root: Path | str | None) -> set[str]:
    """Dataset ids whose ``meta.origin == "own"`` (PR D territory — the field
    does not exist yet, so this is always empty until PR D lands, and
    :func:`_scoped_graphs` treats an empty set as "no filtering")."""
    return {
        str(m.get("id"))
        for m in _dataset_metas(registry_root)
        if m.get("id") and m.get("origin") == "own"
    }


async def _scoped_graphs(
    client: SupportsSparql, registry_root: Path | str | None, source_scope: str
) -> list[str]:
    """The canonical graphs a set query should read, per ``source_scope``.

    ``"own"`` narrows to datasets whose registry meta declares
    ``origin: "own"`` — falling back to every canonical graph when NO
    dataset declares one yet (§3.2: "own の判定は PR D まで meta.origin ==
    'own'、無ければ全部"). ``"all"``/``"open"`` are not yet distinguishable
    without that same field (PR D), so both read every canonical graph.
    """
    graphs = await canonical_graphs(client)
    if source_scope != "own":
        return graphs
    own_ids = _own_dataset_ids(registry_root)
    if not own_ids:
        return graphs
    return [g for g in graphs if dataset_id_of_canonical_graph(g) in own_ids]


def _finalize(
    result: dict[str, Any],
    *,
    output_kind: str,
    item: dict[str, dict[str, Any]],
    materials: list[Material],
) -> dict[str, Any]:
    """Splice the object-cards-ui §3 contract fields onto a base result.

    ``shareable``/``shareable_reasons`` are :mod:`asterism.materials`'
    real (保守側) verdict over ``materials`` — never a placeholder ``None``
    (契約メモ §2)."""
    mats = list(materials)
    return {
        **result,
        "output_kind": output_kind,
        "item": item,
        "materials": [m.to_dict() for m in mats],
        "shareable": _shareable(mats),
        "shareable_reasons": _shareable_reasons(mats),
    }


# ----------------------------------------------------------------------------
# Class resolution (shared by /api/subjects/resolve and default_cards_for_subject)
# ----------------------------------------------------------------------------


async def subject_types(client: SupportsSparql, iri: str) -> list[str]:
    """``rdf:type`` values of ``iri`` in the citable canonical scope, sorted."""
    graphs = await canonical_graphs(client)
    if not graphs:
        return []
    from_clause = canonical_from_clauses(graphs)
    query = (
        f"SELECT DISTINCT ?t\n{from_clause}WHERE {{ {_ref(iri)} {_ref(_RDF_TYPE)} ?t }} ORDER BY ?t"
    )
    return [t for row in _rows(await client.sparql_select(query)) if (t := _cell(row, "t"))]


async def pick_class_iri(client: SupportsSparql, type_iris: list[str]) -> str | None:
    """The "1件の種類" among ``type_iris`` (§3.4 resolve rule): the first one
    that is a known ontology class (``a rdfs:Class`` in a projected ontology
    graph), else the first ``type_iris`` entry, else ``None``."""
    if not type_iris:
        return None
    graphs = await canonical_graphs(client)
    if not graphs:
        return type_iris[0]
    named = canonical_from_clauses(graphs, named=True)
    values = " ".join(_ref(t) for t in type_iris)
    rdf_type = _ref(_RDF_TYPE)
    rdfs_class = _ref(_RDFS_CLASS)
    query = (
        f"SELECT DISTINCT ?t\n{named}"
        f"WHERE {{ VALUES ?t {{ {values} }} "
        f"GRAPH ?g {{ ?t {rdf_type} {rdfs_class} }} }}"
    )
    known = {c for row in _rows(await client.sparql_select(query)) if (c := _cell(row, "t"))}
    for t in type_iris:
        if t in known:
            return t
    return type_iris[0]


# ----------------------------------------------------------------------------
# §3.1 — one-subject built-ins
# ----------------------------------------------------------------------------


async def subject_facts(
    client: SupportsSparql,
    iri: str,
    *,
    max_rows: int = 200,
    registry_root: Path | str | None = None,
) -> dict[str, Any]:
    """Every distinct ``<iri> ?p ?o`` fact, in the citable canonical scope
    (§3.1).

    ``SELECT DISTINCT`` — the same ``(p, o)`` triple commonly lives in more
    than one canonical (version) graph (the same real-world thing recorded by
    two datasets, e.g. after "置く" links a row to an already-shelved subject
    with the same IRI); merging graphs via ``FROM`` ranges over quads, not a
    deduplicated triple set, so without ``DISTINCT`` that fact would print
    once per graph it lives in (実機所見). Where the fact came from is
    ``subject_sources``' job, not this row's — a fact row never carries a
    ``graph``.

    Ordered by the predicate's IRI, then by the value — deterministic
    regardless of store iteration order. ``?p``'s label comes from the
    ontology projection's ``rdfs:label`` → the class schema's own label
    (Mapping IR / display-meta, via :mod:`asterism.class_schema`) → a
    humanized local name (§6 — never the raw identifier alone, K4). An IRI
    value's label uses the same priority rule
    (:func:`asterism.subjects.pick_label`), falling back to a humanized
    local name; ``value_iri`` always carries the raw IRI for "出典を見る".
    Bounded to ``max_rows`` (200 default).
    """
    max_rows = max(1, min(int(max_rows), 2000))
    graphs = await canonical_graphs(client)
    item = {
        "property_iri": {"var": "property_iri", "number": False},
        "property": {"var": "property", "number": False, "role": "label"},
        "value": {"var": "value", "number": False, "role": "value"},
        "value_iri": {"var": "value_iri", "number": False},
    }
    if not graphs:
        base = {
            "tool": "subject_facts",
            "count": 0,
            "items": [],
            "truncated": False,
            "sparql": None,
        }
        return _finalize(base, output_kind="facts", item=item, materials=[])
    from_clause = canonical_from_clauses(graphs)
    query = (
        f"SELECT DISTINCT ?p ?o\n{from_clause}"
        f"WHERE {{ {_ref(iri)} ?p ?o }} ORDER BY ?p ?o LIMIT {max_rows + 1}"
    )
    rows = _rows(await client.sparql_select(query))
    truncated = len(rows) > max_rows
    rows = rows[:max_rows]

    label_targets: set[str] = {_cell(r, "p") for r in rows if _cell(r, "p")}  # type: ignore[misc]
    iri_objects = {
        _cell(r, "o") for r in rows if r.get("o", {}).get("type") == "uri" and _cell(r, "o")
    }

    # A property's ``rdfs:label`` lives in the projected ontology graph, not
    # the canonical data graph (§6) — the label lookup for property IRIs must
    # read both scopes, or a resource whose only outbound facts are literals
    # (no per-subject rdfs:label) never resolves its properties' names.
    property_scope = sorted(await readable_graph_iris(client))
    schema_labels: dict[str, str] = {}
    types = await subject_types(client, iri)
    class_iri = await pick_class_iri(client, types)
    if class_iri is not None:
        schema_fn = _load_class_schema()
        if schema_fn is not None:
            try:
                schema = await schema_fn(client, registry_root, class_iri)
            except Exception:  # best-effort: class_schema failing must not break facts
                logger.debug("subject_facts: class_schema lookup failed", exc_info=True)
                schema = None
            if isinstance(schema, dict):
                schema_labels = {
                    p["iri"]: p["label"]
                    for p in schema.get("properties") or []
                    if isinstance(p, dict) and p.get("iri") and p.get("label")
                }

    def _property_fallback(p: str) -> str:
        return schema_labels.get(p) or _fallback_label(p)

    property_labels = await _label_lookup(
        client, property_scope, label_targets, fallback=_property_fallback
    )
    value_labels = await _label_lookup(client, graphs, iri_objects, fallback=_fallback_label)
    labels = {**value_labels, **property_labels}

    items: list[dict[str, Any]] = []
    for row in rows:
        p = _cell(row, "p") or ""
        o_node = row.get("o", {})
        o_is_iri = o_node.get("type") == "uri"
        o_value = o_node.get("value")
        items.append(
            {
                "property_iri": p,
                "property": labels.get(p, _local_name(p)),
                "value": labels.get(o_value, o_value) if o_is_iri and o_value else o_value,
                "value_iri": o_value if o_is_iri else None,
            }
        )
    base = {
        "tool": "subject_facts",
        "count": len(items),
        "items": items,
        "truncated": truncated,
        "sparql": query,
    }
    materials = await materials_for_subject(client, iri, registry_root=registry_root)
    return _finalize(base, output_kind="facts", item=item, materials=materials)


def _graph_counts_query(iri: str, graphs: list[str]) -> str:
    """The bare (FROM-NAMED-annotated) SPARQL text behind
    :func:`_graph_counts_for_subject_with_query` — split out so a caller that
    already has ``graphs`` (agent_bundle 束の再現用) never needs to duplicate
    this string by hand."""
    named = canonical_from_clauses(graphs, named=True)
    ref = _ref(iri)
    return (
        f"SELECT ?g (COUNT(*) AS ?cnt)\n{named}"
        "WHERE { GRAPH ?g { "
        f"{{ {ref} ?__sp ?__so }} UNION {{ ?__os ?__op {ref} }} "
        "} } GROUP BY ?g ORDER BY ?g"
    )


async def _graph_counts_for_subject_with_query(
    client: SupportsSparql, iri: str
) -> tuple[list[tuple[str, int]], str | None]:
    """``([(graph_iri, triple_count)], sparql)`` where ``iri`` is subject or
    object (canonical scope), sorted by graph IRI — the shared aggregation
    behind both :func:`subject_sources` and :func:`materials_for_subject`
    (§3.3: "1件向けは subject_sources と同じ集計から"). ``sparql`` is
    ``None`` when there are no canonical graphs to read (nothing ran)."""
    graphs = await canonical_graphs(client)
    if not graphs:
        return [], None
    query = _graph_counts_query(iri, graphs)
    out: list[tuple[str, int]] = []
    for row in _rows(await client.sparql_select(query)):
        g = _cell(row, "g")
        cnt = _cell(row, "cnt")
        if g is not None and cnt is not None:
            with contextlib.suppress(ValueError):
                out.append((g, int(float(cnt))))
    return out, query


async def _graph_counts_for_subject(client: SupportsSparql, iri: str) -> list[tuple[str, int]]:
    """``[(graph_iri, triple_count)]`` — thin wrapper over
    :func:`_graph_counts_for_subject_with_query` for callers (e.g.
    :func:`materials_for_subject`) that only need the counts."""
    counts, _query = await _graph_counts_for_subject_with_query(client, iri)
    return counts


async def subject_sources(
    client: SupportsSparql, iri: str, *, registry_root: Path | str | None = None
) -> dict[str, Any]:
    """Which dataset(s) recorded facts about ``iri``, and how many (§3.1)."""
    counts, sparql = await _graph_counts_for_subject_with_query(client, iri)
    labels = dataset_labels(registry_root)
    # dataset_id -> (label, snapshot, count)
    per_dataset: dict[str, tuple[str, str | None, int]] = {}
    for g, cnt in counts:
        dataset_id = dataset_id_of_canonical_graph(g)
        if dataset_id is None:
            continue
        tail = g.rsplit("/", 1)[-1]
        snapshot = tail if tail.startswith("v") and tail[1:].isdigit() else None
        label = labels.get(dataset_id, dataset_id)
        prior_label, prior_snapshot, prior_count = per_dataset.get(dataset_id, (label, snapshot, 0))
        per_dataset[dataset_id] = (prior_label, prior_snapshot or snapshot, prior_count + cnt)

    items = []
    for dataset_id in sorted(per_dataset):
        label, snapshot, cnt = per_dataset[dataset_id]
        category = f"{label} ({snapshot})" if snapshot else label
        items.append({"category": category, "count": cnt})
    item_spec = {
        "category": {"var": "category", "number": False, "role": "category"},
        "count": {"var": "count", "number": True, "role": "count"},
    }
    base = {
        "tool": "subject_sources",
        "count": len(items),
        "items": items,
        "truncated": False,
        "sparql": sparql,
    }
    materials = await materials_for_subject(
        client, iri, registry_root=registry_root, _counts=counts
    )
    return _finalize(base, output_kind="breakdown", item=item_spec, materials=materials)


async def _label_node_types(
    client: SupportsSparql, registry_root: Path | str | None, nodes: list[Any]
) -> None:
    """Give every flow node whose ``props.type`` is a class IRI the same
    display name the rest of the UI uses for that class (registry
    ``model.yaml`` label → ontology ``rdfs:label`` → local name), via
    :func:`asterism.class_schema.class_label`. ``prov_graph`` already fills
    ``type_label`` from the version graph alone; this only *upgrades* it so
    a node reads 「国」 where the rail and the set title say 「国」 (K4: one
    name per thing). Best-effort: a failure leaves ``prov_graph``'s value."""
    try:
        from asterism.class_schema import class_label
    except ImportError:  # authored in parallel — keep prov_graph's fallback
        return
    root = Path(registry_root) if registry_root is not None else None
    cache: dict[str, str] = {}
    for node in nodes:
        props = node.get("props") if isinstance(node, dict) else None
        type_iri = (props or {}).get("type")
        if not isinstance(type_iri, str) or not type_iri:
            continue
        if type_iri not in cache:
            try:
                cache[type_iri] = await class_label(client, root, type_iri)
            except Exception:  # best-effort: a label must never break the flow card
                continue
        props["type_label"] = cache[type_iri]


async def subject_flow(
    client: SupportsSparql, iri: str, *, registry_root: Path | str | None = None
) -> dict[str, Any]:
    """The generic PROV-O flow around ``iri``, wrapped in the §3 contract shape.

    A node that IS found but has zero PROV edges reads ``found: false`` here
    (the flow card has nothing to draw, so the UI must not render it) even
    though ``prov_graph`` itself would say ``found: true`` for that node.

    ``prov_graph``'s own ``materials`` list is shaped for the flow diagram
    (``{dataset_id, snapshot, graph}`` — one row per graph the drawn nodes
    came from, no license/redistributable/count). §3 requires every tool's
    ``materials`` to be the real :mod:`asterism.materials` verdict, so this
    counts each drawn node's ``props.dataset_id``/``props.snapshot`` into a
    contribution and runs it through the same :func:`_materials_for` (and
    ``_finalize``) every other built-in uses.
    """
    result = await _prov_graph(client, iri)
    graph = result.get("graph") or {"nodes": [], "edges": []}
    edges = graph.get("edges") or []
    nodes = graph.get("nodes") or []
    await _label_node_types(client, registry_root, nodes)
    per_dataset: dict[str, tuple[str | None, int]] = {}
    for node in nodes:
        props = node.get("props") if isinstance(node, dict) else None
        dataset_id = (props or {}).get("dataset_id")
        if not dataset_id:
            continue
        snapshot = (props or {}).get("snapshot")
        prior_snapshot, prior_count = per_dataset.get(dataset_id, (snapshot, 0))
        per_dataset[dataset_id] = (prior_snapshot or snapshot, prior_count + 1)
    contributions = [
        (dataset_id, snapshot, cnt) for dataset_id, (snapshot, cnt) in sorted(per_dataset.items())
    ]
    materials = _materials_for(registry_root, contributions)
    base = {
        "tool": "subject_flow",
        "count": len(edges),
        "items": [],
        "truncated": bool(graph.get("truncated", False)),
        "sparql": None,
        "graph": graph,
        "found": bool(result.get("found")) and len(edges) > 0,
    }
    return _finalize(base, output_kind="flow", item={}, materials=materials)


async def _resolve_bare_declared_tool(
    client: SupportsSparql,
    registry_root: Path | str | None,
    iri: str,
    tool_name: str,
) -> QueryTool | None:
    """Back-compat resolution for a bare (no ``dataset_id/`` prefix) declared
    tool name against ``iri``'s own class (dispatch §3.4 back-compat): the
    canonical name ``POST /api/cards/run`` accepts for a declared tool is
    ``f"{dataset_id}/{name}"`` (what ``default_cards_for_subject`` now
    returns), but a caller that only has the bare ``name`` (an older
    default-cards response, a hand-typed tool name) must still resolve —
    via the subject's own class schema, exactly the same class/dataset this
    subject's default cards would use. Returns ``None`` (→ 404 upstream) when
    no class, no schema, no matching tool, or the match does not have
    exactly one ``iri`` parameter (cannot bind a subject)."""
    types = await subject_types(client, iri)
    class_iri = await pick_class_iri(client, types)
    if class_iri is None:
        return None
    schema_fn = _load_class_schema()
    if schema_fn is None:
        return None
    try:
        schema = await schema_fn(client, registry_root, class_iri)
    except Exception:  # best-effort: class_schema is owned by a parallel PR
        logger.debug("run_subject_tool: class_schema lookup failed", exc_info=True)
        return None
    if not isinstance(schema, dict):
        return None
    dataset_id = schema.get("dataset_id")
    if not dataset_id:
        return None
    declared = {t.name: t for t in load_query_tools(str(dataset_id), root=registry_root)}
    qt = declared.get(tool_name)
    if qt is None or iri_param_of(qt) is None:
        return None
    return qt


def iri_param_of(tool: QueryTool) -> ToolParam | None:
    """The tool's single ``iri``-typed parameter, or ``None`` when it has
    zero or more than one (§3.1: only a tool with exactly one iri param can
    be bound to a subject)."""
    iri_params = [p for p in tool.params if p.type == "iri"]
    return iri_params[0] if len(iri_params) == 1 else None


async def run_iri_bound_tool(
    client: SupportsSparql,
    tool: QueryTool,
    iri: str,
    extra_params: dict[str, Any] | None = None,
    *,
    registry_root: Path | str | None = None,
    max_rows: int = 200,
) -> dict[str, Any]:
    """Bind ``iri`` into ``tool``'s one iri parameter and run it (§3.1 row 4).

    Raises :class:`SubjectToolError` when ``tool`` does not have exactly one
    ``iri``-typed parameter, or :class:`asterism.query_tools.QueryToolError`
    for any other binding failure (unknown extra param, wrong type, ...).
    """
    param = iri_param_of(tool)
    if param is None:
        raise SubjectToolError(
            f"tool {tool.name!r} does not take exactly one iri parameter (cannot bind a subject)"
        )
    args = dict(extra_params or {})
    args[param.name] = iri
    result = await run_query_tool(client, tool, args, max_rows=max_rows)
    materials = await materials_for_subject(client, iri, registry_root=registry_root)
    return _finalize(
        result, output_kind=tool.output_kind, item=dict(tool.item), materials=materials
    )


# ----------------------------------------------------------------------------
# §3.3 — materials (real kind/license/redistributable via asterism.materials)
# ----------------------------------------------------------------------------


async def materials_for_subject(
    client: SupportsSparql,
    iri: str,
    *,
    registry_root: Path | str | None = None,
    _counts: list[tuple[str, int]] | None = None,
) -> list[Material]:
    """Per-dataset contribution counts for ``iri`` (§3.3), from the same
    per-graph aggregation :func:`subject_sources` uses, turned into
    :class:`asterism.materials.Material` (kind/license/redistributable —
    契約メモ §2). ``_counts`` lets :func:`subject_sources` pass its
    already-fetched rows through instead of re-querying (private — not part
    of the public contract)."""
    counts = _counts if _counts is not None else await _graph_counts_for_subject(client, iri)
    per_dataset: dict[str, tuple[str | None, int]] = {}
    for g, cnt in counts:
        dataset_id = dataset_id_of_canonical_graph(g)
        if dataset_id is None:
            continue
        tail = g.rsplit("/", 1)[-1]
        snapshot = tail if tail.startswith("v") and tail[1:].isdigit() else None
        prior_snapshot, prior_count = per_dataset.get(dataset_id, (snapshot, 0))
        per_dataset[dataset_id] = (prior_snapshot or snapshot, prior_count + cnt)
    contributions = [
        (dataset_id, snapshot, cnt) for dataset_id, (snapshot, cnt) in sorted(per_dataset.items())
    ]
    return _materials_for(registry_root, contributions)


async def materials_for_set(
    client: SupportsSparql, spec: dict[str, Any], *, registry_root: Path | str | None = None
) -> list[Material]:
    """Per-dataset contribution counts for a set's matching subjects (§3.3):
    ``COUNT(DISTINCT ?s)`` of matching subjects, grouped by graph, turned into
    :class:`asterism.materials.Material` (契約メモ §2)."""
    spec = normalize_set_spec(spec)
    graphs = await _scoped_graphs(client, registry_root, spec["source_scope"])
    if not graphs:
        return []
    named = canonical_from_clauses(graphs, named=True)
    lines = [f"?s a {_ref(spec['class'])} ."]
    for i, clause in enumerate(spec["where"]):
        lines.append(_clause_pattern(clause, index=i))
    query = (
        _XSD_PREFIX
        + f"SELECT ?g (COUNT(DISTINCT ?s) AS ?cnt)\n{named}"
        + "WHERE { GRAPH ?g { "
        + " ".join(lines)
        + " } } GROUP BY ?g ORDER BY ?g"
    )
    per_dataset: dict[str, tuple[str | None, int]] = {}
    for row in _rows(await client.sparql_select(query)):
        g = _cell(row, "g")
        cnt = _cell(row, "cnt")
        if g is None or cnt is None:
            continue
        dataset_id = dataset_id_of_canonical_graph(g)
        if dataset_id is None:
            continue
        tail = g.rsplit("/", 1)[-1]
        snapshot = tail if tail.startswith("v") and tail[1:].isdigit() else None
        with contextlib.suppress(ValueError):
            prior_snapshot, prior_count = per_dataset.get(dataset_id, (snapshot, 0))
            per_dataset[dataset_id] = (prior_snapshot or snapshot, prior_count + int(float(cnt)))
    contributions = [
        (dataset_id, snapshot, cnt) for dataset_id, (snapshot, cnt) in sorted(per_dataset.items())
    ]
    return _materials_for(registry_root, contributions)


# ----------------------------------------------------------------------------
# §1-3 (PR F4 / ADR O46) — 「この 1 件を指す種類」を実データの形から求める。
# ----------------------------------------------------------------------------


async def linking_kinds(
    client: SupportsSparql, iri: str, *, registry_root: Path | str | None = None
) -> list[dict[str, Any]]:
    """Which ``(class, property)`` pairs point AT ``iri`` in the citable
    canonical scope (契約メモ §1-3・ADR O46): 「この IRI を目的語に持つ実例の
    種類と述語」— 1 件のページに絞り込みの条件が無いとき、「この 1 件に関する
    記録」を集める set の ``class``/``where`` を機械が探すための材料（宣言
    不要、実データの形からだけ求める — O16 の汎用性方針。データセットごとに
    語彙が違っても新しいデータセットにそのまま効く）。

    来歴のクラス（PROV 名前空間 — 実例は典型的に ``prov:Activity``/
    ``prov:Agent``）は除く: その種類を選ばせても人には意味が無い（来歴は
    :func:`subject_flow` の役目）。行は
    ``{class_iri, class_label, property, property_label, count}`` —
    ``count`` はその ``(class, property)`` の組で ``iri`` を指す実例
    （``DISTINCT ?rec``）の数。候補が無ければ空リスト（「数字 1 つ」「表」で
    しか測定を作れない、O46 の最後の段落）。ソートは ``class_iri``・
    ``property`` の辞書順 — store の反復順に依存しない。"""
    graphs = await canonical_graphs(client)
    if not graphs:
        return []
    from_clause = canonical_from_clauses(graphs)
    query = (
        f"SELECT ?cls ?p (COUNT(DISTINCT ?rec) AS ?cnt)\n{from_clause}"
        f"WHERE {{ ?rec ?p {_ref(iri)} ; {_ref(_RDF_TYPE)} ?cls . "
        f'FILTER(!STRSTARTS(STR(?cls), "{_PROV_NS}")) }} '
        "GROUP BY ?cls ?p ORDER BY ?cls ?p"
    )
    pairs: list[tuple[str, str, int]] = []
    for row in _rows(await client.sparql_select(query)):
        cls = _cell(row, "cls")
        p = _cell(row, "p")
        cnt = _cell(row, "cnt")
        if cls is None or p is None or cnt is None:
            continue
        with contextlib.suppress(ValueError):
            pairs.append((cls, p, int(float(cnt))))
    if not pairs:
        return []

    class_iris = {cls for cls, _p, _cnt in pairs}
    property_iris = {p for _cls, p, _cnt in pairs}
    class_labels = await _class_labels(client, registry_root, class_iris)
    property_scope = sorted(await readable_graph_iris(client))
    property_labels = await _label_lookup(
        client, property_scope, property_iris, fallback=_fallback_label
    )
    return [
        {
            "class_iri": cls,
            "class_label": class_labels.get(cls, _fallback_label(cls)),
            "property": p,
            "property_label": property_labels.get(p, _fallback_label(p)),
            "count": cnt,
        }
        for cls, p, cnt in pairs
    ]


async def _class_labels(
    client: SupportsSparql, registry_root: Path | str | None, class_iris: set[str]
) -> dict[str, str]:
    """§3 の class_label 優先順位（registry の model.yaml → ontology
    rdfs:label → humanize）で ``class_iris`` を引く — :func:`_label_node_types`
    と同じ遅延 import・best-effort（``class_schema`` は並行担当ファイルなので
    無いことがある）。"""
    try:
        from asterism.class_schema import class_label
    except ImportError:
        return {c: _fallback_label(c) for c in class_iris}
    root = Path(registry_root) if registry_root is not None else None
    out: dict[str, str] = {}
    for c in class_iris:
        try:
            out[c] = await class_label(client, root, c)
        except Exception:  # best-effort: class_schema is owned by a parallel PR
            logger.debug("linking_kinds: class_label lookup failed", exc_info=True)
            out[c] = _fallback_label(c)
    return out


# ----------------------------------------------------------------------------
# §3.2 — set (絞り込み) built-ins
# ----------------------------------------------------------------------------


def _numeric_literal(value: Any, *, index: int) -> str:
    try:
        return repr(float(value))
    except (TypeError, ValueError) as exc:
        raise SetSpecError(f"where[{index}].value must be numeric for this op") from exc


def _clause_pattern(clause: dict[str, Any], *, index: int) -> str:
    """One normalized where clause -> SPARQL pattern lines (triple + FILTER).

    A link clause (``{property, iri}`` — PR F4 §1-3, ``asterism.subjects``'s
    ``_normalize_clause``) has no ``op``/``value`` at all: it is a plain
    existence triple ``?s <property> <iri>``, embedded via the same ``_ref``
    (→ ``safe_iri``) gate every other IRI in this module goes through.

    Numeric ops cast the bound value to ``xsd:double`` (mirrors
    ``query_tools``'s own ``value_range``/``top_value`` synthesis) so a
    literal without an explicit numeric datatype still compares correctly.
    Every string value is escaped via ``_escape_literal`` — never
    concatenated raw (§0)."""
    if "iri" in clause:
        return f"?s {_ref(clause['property'])} {_ref(clause['iri'])} ."
    var = f"?wv{index}"
    pattern = f"?s {_ref(clause['property'])} {var} ."
    op = clause["op"]
    value = clause["value"]
    numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
    if op == "between":
        low, high = value
        numvar = f"?wn{index}"
        return (
            f"{pattern} BIND(xsd:double(str({var})) AS {numvar}) "
            f"FILTER(BOUND({numvar}) && {numvar} >= {_numeric_literal(low, index=index)} "
            f"&& {numvar} <= {_numeric_literal(high, index=index)})"
        )
    if op == "in":
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
            numvar = f"?wn{index}"
            nums = ", ".join(_numeric_literal(v, index=index) for v in value)
            return (
                f"{pattern} BIND(xsd:double(str({var})) AS {numvar}) "
                f"FILTER(BOUND({numvar}) && {numvar} IN ({nums}))"
            )
        lits = ", ".join(f'"{_escape_literal(str(v))}"' for v in value)
        return f"{pattern} FILTER(STR({var}) IN ({lits}))"
    if numeric:
        numvar = f"?wn{index}"
        cmp_op = {"gt": ">", "lt": "<", "eq": "="}[op]
        return (
            f"{pattern} BIND(xsd:double(str({var})) AS {numvar}) "
            f"FILTER(BOUND({numvar}) && {numvar} {cmp_op} {_numeric_literal(value, index=index)})"
        )
    if op == "eq":
        return f'{pattern} FILTER(STR({var}) = "{_escape_literal(str(value))}")'
    raise SetSpecError(f"where[{index}].op {op!r} needs a numeric value")


async def _resolve_order_unit(
    client: SupportsSparql, registry_root: Path | str | None, class_iri: str, property_iri: str
) -> tuple[str | None, bool]:
    """``(unit, is_quantity)`` for ``property_iri`` on ``class_iri``, via the
    (parallel-authored) class schema — best-effort: any failure (module not
    present yet, store issue, property absent from the schema) degrades to
    ``(None, False)`` rather than failing the whole set_members call."""
    schema_fn = _load_class_schema()
    if schema_fn is None:
        return None, False
    try:
        schema = await schema_fn(client, registry_root, class_iri)
    except Exception:  # best-effort: class_schema is owned by a parallel PR
        logger.debug("set_members: class_schema lookup failed", exc_info=True)
        return None, False
    if not isinstance(schema, dict):
        return None, False
    for prop in schema.get("properties") or []:
        if isinstance(prop, dict) and prop.get("iri") == property_iri:
            return prop.get("unit"), prop.get("kind") == "quantity"
    return None, False


def _load_class_schema():
    """Lazy indirection to ``asterism.class_schema.class_schema`` (c1-schema's
    module, authored in parallel — may not exist yet in this worktree).
    A separate function (not inlined) so tests can monkeypatch this exact
    seam without needing the real module to exist."""
    try:
        from asterism.class_schema import class_schema
    except ImportError:
        return None
    return class_schema


async def set_members(
    client: SupportsSparql,
    spec: dict[str, Any],
    *,
    registry_root: Path | str | None = None,
) -> dict[str, Any]:
    """The set's matching subjects, ranked by ``order_by`` when given (§3.2)."""
    spec = normalize_set_spec(spec)
    graphs = await _scoped_graphs(client, registry_root, spec["source_scope"])
    order_by = spec["order_by"]
    limit = spec["limit"]

    unit: str | None = None
    is_quantity = False
    if order_by is not None:
        unit, is_quantity = await _resolve_order_unit(
            client, registry_root, spec["class"], order_by["property"]
        )

    if not graphs:
        item = {
            "subject_iri": {"var": "subject_iri", "number": False, "role": "subject"},
            "label": {"var": "label", "number": False, "role": "label"},
        }
        base = {"tool": "set_members", "count": 0, "items": [], "truncated": False, "sparql": None}
        return _finalize(
            base, output_kind="ranked" if is_quantity else "facts", item=item, materials=[]
        )

    named = canonical_from_clauses(graphs, named=True)
    lines = [f"?s a {_ref(spec['class'])} ."]
    for i, clause in enumerate(spec["where"]):
        lines.append(_clause_pattern(clause, index=i))
    select_vars = "?s"
    if order_by is not None:
        lines.append(f"OPTIONAL {{ ?s {_ref(order_by['property'])} ?value }}")
        select_vars += " ?value"

    query = _XSD_PREFIX + f"SELECT {select_vars}\n{named}" + "WHERE { " + " ".join(lines) + " }"
    if order_by is not None:
        direction = "DESC" if order_by["dir"] == "desc" else "ASC"
        query += f" ORDER BY {direction}(?value) ?s"
    else:
        query += " ORDER BY ?s"
    query += f" LIMIT {limit}"

    rows = _rows(await client.sparql_select(query))
    # ラベルは §2 の共通関数（label_union_clause/pick_label）で一括取得する — 行ごとに
    # 別々の OPTIONAL/COALESCE を組むと、rdfs:label しか無い主語と schema:name しか
    # 無い主語のどちらかがラベル無し（IRI 末尾）になる食い違いが起きる（実機所見）。
    subjects_in_page = {s for row in rows if (s := _cell(row, "s"))}
    labels = await _label_lookup(client, graphs, subjects_in_page, fallback=_fallback_label)
    items: list[dict[str, Any]] = []
    for row in rows:
        s = _cell(row, "s") or ""
        entry: dict[str, Any] = {
            "subject_iri": s,
            "label": labels.get(s, _fallback_label(s)),
        }
        if order_by is not None:
            entry["value"] = _as_number(_cell(row, "value"))
        items.append(entry)

    item: dict[str, dict[str, Any]] = {
        "subject_iri": {"var": "subject_iri", "number": False, "role": "subject"},
        "label": {"var": "label", "number": False, "role": "label"},
    }
    if order_by is not None:
        value_spec: dict[str, Any] = {"var": "value", "number": True, "role": "value"}
        if unit:
            value_spec["unit"] = unit
        item["value"] = value_spec

    base = {
        "tool": "set_members",
        "count": len(items),
        "items": items,
        "truncated": False,
        "sparql": query,
    }
    materials = await materials_for_set(client, spec, registry_root=registry_root)
    output_kind = "ranked" if (order_by is not None and is_quantity) else "facts"
    return _finalize(base, output_kind=output_kind, item=item, materials=materials)


async def set_breakdown(
    client: SupportsSparql,
    spec: dict[str, Any],
    property_iri: str,
    *,
    registry_root: Path | str | None = None,
    top_n: int = 12,
) -> dict[str, Any]:
    """Value counts of ``property_iri`` over the set's matching subjects,
    top ``top_n`` + an ``"other"`` bucket for the rest (§3.2)."""
    spec = normalize_set_spec(spec)
    property_iri = _require_property_iri(property_iri)
    graphs = await _scoped_graphs(client, registry_root, spec["source_scope"])
    item = {
        "category": {"var": "category", "number": False, "role": "category"},
        "count": {"var": "count", "number": True, "role": "count"},
        "category_iri": {"var": "category_iri", "number": False},
    }
    if not graphs:
        base = {
            "tool": "set_breakdown",
            "count": 0,
            "items": [],
            "truncated": False,
            "sparql": None,
        }
        return _finalize(base, output_kind="breakdown", item=item, materials=[])

    named = canonical_from_clauses(graphs, named=True)
    lines = [f"?s a {_ref(spec['class'])} .", f"?s {_ref(property_iri)} ?cat ."]
    for i, clause in enumerate(spec["where"]):
        lines.append(_clause_pattern(clause, index=i))
    query = (
        _XSD_PREFIX
        + f"SELECT ?cat (COUNT(DISTINCT ?s) AS ?cnt)\n{named}"
        + "WHERE { "
        + " ".join(lines)
        + " } GROUP BY ?cat ORDER BY DESC(?cnt) ?cat"
    )
    rows = _rows(await client.sparql_select(query))
    parsed: list[tuple[str, bool, int]] = []
    for row in rows:
        cat_node = row.get("cat") or {}
        cat = cat_node.get("value")
        cnt = _cell(row, "cnt")
        if cat is None or cnt is None:
            continue
        with contextlib.suppress(ValueError):
            parsed.append((cat, cat_node.get("type") == "uri", int(float(cnt))))

    top = parsed[:top_n]
    rest = parsed[top_n:]
    # 値が IRI（kind=link 相当の category）のときは §2 と同じ共通ラベル関数
    # (label_union_clause/pick_label 経由の _label_lookup) でラベルに直す。
    # 元の IRI は `category_iri` に添え、K4（生の識別子を見せない）を保つ。
    iri_values = {cat for cat, is_iri, _cnt in top if is_iri}
    labels = (
        await _label_lookup(client, graphs, iri_values, fallback=_fallback_label)
        if iri_values
        else {}
    )
    items: list[dict[str, Any]] = []
    for cat, is_iri, cnt in top:
        entry: dict[str, Any] = {
            "category": labels.get(cat, _fallback_label(cat)) if is_iri else cat,
            "count": cnt,
        }
        if is_iri:
            entry["category_iri"] = cat
        items.append(entry)
    if rest:
        items.append({"category": "other", "count": sum(cnt for _cat, _is_iri, cnt in rest)})

    base = {
        "tool": "set_breakdown",
        "count": len(items),
        "items": items,
        "truncated": False,
        "sparql": query,
    }
    materials = await materials_for_set(client, spec, registry_root=registry_root)
    return _finalize(base, output_kind="breakdown", item=item, materials=materials)


def _require_property_iri(value: str) -> str:
    """Boundary validation for ``set_breakdown``'s ``params.property`` — this
    one, unlike ``where[].property``/``order_by.property``, is NOT covered by
    :func:`asterism.subjects.normalize_set_spec` (it arrives as a separate
    ``POST /api/cards/run`` body field), so it must independently pass the
    same http(s)+character-safety check via
    :func:`asterism.subjects.safe_http_iri` (§0: no duplicated validation
    logic — one regex, in ``asterism.subjects``)."""
    text = safe_http_iri(value)
    if text is None:
        raise SetSpecError(f"property is required and must be an http(s) IRI, got {value!r}")
    return text


async def set_count(
    client: SupportsSparql, spec: dict[str, Any], *, registry_root: Path | str | None = None
) -> dict[str, Any]:
    """How many subjects match the set's ``where`` clauses (§3.2) — backs the
    絞り込みページ's "N 件" heading."""
    spec = normalize_set_spec(spec)
    graphs = await _scoped_graphs(client, registry_root, spec["source_scope"])
    item = {"value": {"var": "value", "number": True, "role": "value"}}
    if not graphs:
        base = {
            "tool": "set_count",
            "count": 1,
            "items": [{"value": 0}],
            "truncated": False,
            "sparql": None,
        }
        return _finalize(base, output_kind="quantity", item=item, materials=[])

    named = canonical_from_clauses(graphs, named=True)
    lines = [f"?s a {_ref(spec['class'])} ."]
    for i, clause in enumerate(spec["where"]):
        lines.append(_clause_pattern(clause, index=i))
    query = (
        _XSD_PREFIX
        + f"SELECT (COUNT(DISTINCT ?s) AS ?value)\n{named}"
        + "WHERE { "
        + " ".join(lines)
        + " }"
    )
    rows = _rows(await client.sparql_select(query))
    value = 0
    if rows:
        with contextlib.suppress(TypeError, ValueError):
            value = int(float(_cell(rows[0], "value") or 0))
    base = {
        "tool": "set_count",
        "count": 1,
        "items": [{"value": value}],
        "truncated": False,
        "sparql": query,
    }
    materials = await materials_for_set(client, spec, registry_root=registry_root)
    return _finalize(base, output_kind="quantity", item=item, materials=materials)


# ----------------------------------------------------------------------------
# §1-2/§1-4 (PR F4 / ADR O43-O45) — set_measure: 「見せ方 → 項目」の 1 builtin
# ----------------------------------------------------------------------------

#: SPARQL の集計関数名（測定 spec の ``agg`` 語彙 → SPARQL、契約メモ §3 言葉の
#: 平均/最大/最小/合計/件数の順と揃えてある）。
_AGG_SPARQL: dict[str, str] = {
    "avg": "AVG",
    "max": "MAX",
    "min": "MIN",
    "sum": "SUM",
    "count": "COUNT",
}


async def _schema_properties_for(
    client: SupportsSparql, registry_root: Path | str | None, class_iri: str
) -> list[dict[str, Any]] | None:
    """``class_schema(...)['properties']`` for ``class_iri``, or ``None`` when
    the (parallel-authored) ``class_schema`` module is absent or the lookup
    fails — best-effort, same seam as :func:`_resolve_order_unit`."""
    schema_fn = _load_class_schema()
    if schema_fn is None:
        return None
    try:
        schema = await schema_fn(client, registry_root, class_iri)
    except Exception:  # best-effort: class_schema is owned by a parallel PR
        logger.debug("set_measure: class_schema lookup failed", exc_info=True)
        return None
    if not isinstance(schema, dict):
        return None
    return list(schema.get("properties") or [])


def _property_meta(schema_properties: list[dict[str, Any]] | None, iri: str) -> dict[str, Any]:
    """The one ``class_schema`` property row for ``iri`` (``label``/``kind``/
    ``unit``), or ``{}`` when ``schema_properties`` is ``None``/lacks it —
    :func:`validate_measure` already rejected any ``iri`` that is not on the
    schema when a schema was available, so this only ever comes up empty in
    the best-effort "no schema at all" path."""
    for prop in schema_properties or []:
        if isinstance(prop, dict) and prop.get("iri") == iri:
            return prop
    return {}


async def set_measure(
    client: SupportsSparql,
    spec: dict[str, Any],
    *,
    params: dict[str, Any],
    registry_root: Path | str | None = None,
) -> dict[str, Any]:
    """The one generic builtin behind every 「＋ グラフを足す」card (契約メモ
    §1-2/§1-4、ADR O43-O45): 見せ方（``params["shape"]``）ごとに違う SPARQL を
    組むが、どれも既存の ``_scoped_graphs``/``canonical_from_clauses``/
    ``_clause_pattern``/``_numeric_literal``/``_label_lookup`` の組み合わせで
    しか組まない（O45 — 見せ方が増えるたびに新しいエスケープ経路を増やさな
    い）。

    ``params`` は :func:`asterism.measure_spec.validate_measure` の入力その
    もの — §1-2 の妥当性表の外（例: category 列の平均）は
    :class:`SubjectToolError`（→ api 400）。UI をバイパスした呼び出しに対する
    「最後の砦」（ADR O44 — UI 側の同じ表は「迷わせない先回り」という別の役
    目）。数値化できない値は既存の
    ``BIND(xsd:double(str(...))) FILTER(BOUND(...))`` の流儀（``query_tools``
    の ``top_value``/``value_range`` と同じ、§0）で捨てる。"""
    spec = normalize_set_spec(spec)
    schema_properties = await _schema_properties_for(client, registry_root, spec["class"])
    try:
        measure = validate_measure(params, schema_properties)
    except MeasureSpecError as exc:
        raise SubjectToolError(str(exc)) from exc
    shape = measure["shape"]
    # ``output_kind_for`` は shape の語彙が :data:`asterism.query_tools.OUTPUT_KINDS`
    # と 1 対 1 であることの唯一の確認経路 — 各 ``_measure_*`` ヘルパーは
    # ``shape`` 文字列をそのまま ``_finalize`` の ``output_kind`` に渡すので
    # ここで（有効な shape であることは既に ``validate_measure`` が保証済み
    # だが）呼んでおくことで、その等価性が測定 spec 側でも一度は検証される。
    output_kind_for(shape)

    if shape == "breakdown":
        return await set_breakdown(client, spec, measure["category"], registry_root=registry_root)

    graphs = await _scoped_graphs(client, registry_root, spec["source_scope"])
    if shape == "series":
        return await _measure_series(
            client, spec, measure, graphs, schema_properties, registry_root=registry_root
        )
    if shape == "pairs":
        return await _measure_pairs(
            client, spec, measure, graphs, schema_properties, registry_root=registry_root
        )
    if shape == "ranked":
        return await _measure_ranked(
            client, spec, measure, graphs, schema_properties, registry_root=registry_root
        )
    if shape == "quantity":
        return await _measure_quantity(
            client, spec, measure, graphs, schema_properties, registry_root=registry_root
        )
    return await _measure_facts(
        client, spec, measure, graphs, schema_properties, registry_root=registry_root
    )


async def _measure_series(
    client: SupportsSparql,
    spec: dict[str, Any],
    measure: dict[str, Any],
    graphs: list[str],
    schema_properties: list[dict[str, Any]] | None,
    *,
    registry_root: Path | str | None,
) -> dict[str, Any]:
    """「推移」— ``x`` で ``GROUP BY`` した ``y`` の平均、``x`` の昇順（同じ
    ``x`` が複数行のときも 1 点に畳む。1 行しか無ければ平均はその値そのも
    の）。``output_kind`` の役割どおり ``x``/``y`` とも quantity（series の
    ``x`` に単位を求めないのは query_tools の lint 規則の話であって、ここでは
    schema に unit があれば普通に転記する）。"""
    x_iri, y_iri = measure["x"], measure["y"]
    item: dict[str, dict[str, Any]] = {
        "x": {"var": "x", "number": True, "role": "x"},
        "y": {"var": "y", "number": True, "role": "y"},
    }
    x_meta = _property_meta(schema_properties, x_iri)
    y_meta = _property_meta(schema_properties, y_iri)
    if unit := x_meta.get("unit"):
        item["x"]["unit"] = unit
    if unit := y_meta.get("unit"):
        item["y"]["unit"] = unit
    if label := x_meta.get("label"):
        item["x"]["label"] = label
    if label := y_meta.get("label"):
        item["y"]["label"] = label
    if not graphs:
        base = {"tool": "set_measure", "count": 0, "items": [], "truncated": False, "sparql": None}
        return _finalize(base, output_kind="series", item=item, materials=[])

    named = canonical_from_clauses(graphs, named=True)
    lines = [
        f"?s a {_ref(spec['class'])} .",
        f"?s {_ref(x_iri)} ?xr . BIND(xsd:double(str(?xr)) AS ?xn) FILTER(BOUND(?xn))",
        f"?s {_ref(y_iri)} ?yr . BIND(xsd:double(str(?yr)) AS ?yn) FILTER(BOUND(?yn))",
    ]
    for i, clause in enumerate(spec["where"]):
        lines.append(_clause_pattern(clause, index=i))
    limit = spec["limit"]
    query = (
        _XSD_PREFIX
        + f"SELECT ?xn (AVG(?yn) AS ?y)\n{named}"
        + "WHERE { "
        + " ".join(lines)
        + " }"
        + f" GROUP BY ?xn ORDER BY ?xn LIMIT {limit + 1}"
    )
    rows = _rows(await client.sparql_select(query))
    truncated = len(rows) > limit
    rows = rows[:limit]
    items = [{"x": _as_number(_cell(row, "xn")), "y": _as_number(_cell(row, "y"))} for row in rows]
    base = {
        "tool": "set_measure",
        "count": len(items),
        "items": items,
        "truncated": truncated,
        "sparql": query,
    }
    materials = await materials_for_set(client, spec, registry_root=registry_root)
    return _finalize(base, output_kind="series", item=item, materials=materials)


async def _measure_pairs(
    client: SupportsSparql,
    spec: dict[str, Any],
    measure: dict[str, Any],
    graphs: list[str],
    schema_properties: list[dict[str, Any]] | None,
    *,
    registry_root: Path | str | None,
) -> dict[str, Any]:
    """「散らばり」— ``(x, y)`` の ``DISTINCT`` 組（§1-2: 集約しない生の散布
    図の点）。"""
    x_iri, y_iri = measure["x"], measure["y"]
    item: dict[str, dict[str, Any]] = {
        "x": {"var": "x", "number": True, "role": "x"},
        "y": {"var": "y", "number": True, "role": "y"},
    }
    x_meta = _property_meta(schema_properties, x_iri)
    y_meta = _property_meta(schema_properties, y_iri)
    if unit := x_meta.get("unit"):
        item["x"]["unit"] = unit
    if unit := y_meta.get("unit"):
        item["y"]["unit"] = unit
    if label := x_meta.get("label"):
        item["x"]["label"] = label
    if label := y_meta.get("label"):
        item["y"]["label"] = label
    if not graphs:
        base = {"tool": "set_measure", "count": 0, "items": [], "truncated": False, "sparql": None}
        return _finalize(base, output_kind="pairs", item=item, materials=[])

    named = canonical_from_clauses(graphs, named=True)
    lines = [
        f"?s a {_ref(spec['class'])} .",
        f"?s {_ref(x_iri)} ?xr . BIND(xsd:double(str(?xr)) AS ?x) FILTER(BOUND(?x))",
        f"?s {_ref(y_iri)} ?yr . BIND(xsd:double(str(?yr)) AS ?y) FILTER(BOUND(?y))",
    ]
    for i, clause in enumerate(spec["where"]):
        lines.append(_clause_pattern(clause, index=i))
    limit = spec["limit"]
    query = (
        _XSD_PREFIX
        + f"SELECT DISTINCT ?x ?y\n{named}"
        + "WHERE { "
        + " ".join(lines)
        + " }"
        + f" ORDER BY ?x ?y LIMIT {limit + 1}"
    )
    rows = _rows(await client.sparql_select(query))
    truncated = len(rows) > limit
    rows = rows[:limit]
    items = [{"x": _as_number(_cell(row, "x")), "y": _as_number(_cell(row, "y"))} for row in rows]
    base = {
        "tool": "set_measure",
        "count": len(items),
        "items": items,
        "truncated": truncated,
        "sparql": query,
    }
    materials = await materials_for_set(client, spec, registry_root=registry_root)
    return _finalize(base, output_kind="pairs", item=item, materials=materials)


async def _measure_ranked(
    client: SupportsSparql,
    spec: dict[str, Any],
    measure: dict[str, Any],
    graphs: list[str],
    schema_properties: list[dict[str, Any]] | None,
    *,
    registry_root: Path | str | None,
) -> dict[str, Any]:
    """「比べる」— 1 件ごとの label と value を ``ORDER BY`` ``value``
    （既定 desc）``LIMIT``（``set_members``の``order_by``と同じ形だが、
    ``item`` が quantity であることは既に :func:`validate_measure` が保証
    済みなので ``output_kind`` は常に ``"ranked"``）。"""
    item_iri, order = measure["item"], measure["order"]
    item: dict[str, dict[str, Any]] = {
        "subject_iri": {"var": "subject_iri", "number": False, "role": "subject"},
        "label": {"var": "label", "number": False, "role": "label"},
        "value": {"var": "value", "number": True, "role": "value"},
    }
    value_meta = _property_meta(schema_properties, item_iri)
    if unit := value_meta.get("unit"):
        item["value"]["unit"] = unit
    if label := value_meta.get("label"):
        item["value"]["label"] = label
    limit = spec["limit"]
    if not graphs:
        base = {"tool": "set_measure", "count": 0, "items": [], "truncated": False, "sparql": None}
        return _finalize(base, output_kind="ranked", item=item, materials=[])

    named = canonical_from_clauses(graphs, named=True)
    lines = [
        f"?s a {_ref(spec['class'])} .",
        f"?s {_ref(item_iri)} ?vr . BIND(xsd:double(str(?vr)) AS ?value) FILTER(BOUND(?value))",
    ]
    for i, clause in enumerate(spec["where"]):
        lines.append(_clause_pattern(clause, index=i))
    direction = "DESC" if order == "desc" else "ASC"
    query = (
        _XSD_PREFIX
        + f"SELECT ?s ?value\n{named}"
        + "WHERE { "
        + " ".join(lines)
        + " }"
        + f" ORDER BY {direction}(?value) ?s LIMIT {limit + 1}"
    )
    rows = _rows(await client.sparql_select(query))
    truncated = len(rows) > limit
    rows = rows[:limit]
    subjects = {s for row in rows if (s := _cell(row, "s"))}
    labels = await _label_lookup(client, graphs, subjects, fallback=_fallback_label)
    items = []
    for row in rows:
        s = _cell(row, "s") or ""
        items.append(
            {
                "subject_iri": s,
                "label": labels.get(s, _fallback_label(s)),
                "value": _as_number(_cell(row, "value")),
            }
        )
    base = {
        "tool": "set_measure",
        "count": len(items),
        "items": items,
        "truncated": truncated,
        "sparql": query,
    }
    materials = await materials_for_set(client, spec, registry_root=registry_root)
    return _finalize(base, output_kind="ranked", item=item, materials=materials)


async def _measure_quantity(
    client: SupportsSparql,
    spec: dict[str, Any],
    measure: dict[str, Any],
    graphs: list[str],
    schema_properties: list[dict[str, Any]] | None,
    *,
    registry_root: Path | str | None,
) -> dict[str, Any]:
    """「数字 1 つ」— ``AVG``/``MAX``/``MIN``/``SUM``/``COUNT`` のどれか 1 つを
    ``item`` に適用した単一の値（``set_count``と同じ形の 1 行結果）。"""
    item_iri, agg = measure["item"], measure["agg"]
    item: dict[str, dict[str, Any]] = {"value": {"var": "value", "number": True, "role": "value"}}
    value_meta = _property_meta(schema_properties, item_iri)
    unit = value_meta.get("unit")
    if unit and agg != "count":  # 件数に単位は付かない
        item["value"]["unit"] = unit
    if agg != "count" and (label := value_meta.get("label")):
        item["value"]["label"] = label
    if not graphs:
        base = {
            "tool": "set_measure",
            "count": 1,
            "items": [{"value": 0}],
            "truncated": False,
            "sparql": None,
        }
        return _finalize(base, output_kind="quantity", item=item, materials=[])

    named = canonical_from_clauses(graphs, named=True)
    lines = [
        f"?s a {_ref(spec['class'])} .",
        f"?s {_ref(item_iri)} ?vr . BIND(xsd:double(str(?vr)) AS ?vn) FILTER(BOUND(?vn))",
    ]
    for i, clause in enumerate(spec["where"]):
        lines.append(_clause_pattern(clause, index=i))
    agg_fn = _AGG_SPARQL[agg]
    query = (
        _XSD_PREFIX
        + f"SELECT ({agg_fn}(?vn) AS ?value)\n{named}"
        + "WHERE { "
        + " ".join(lines)
        + " }"
    )
    rows = _rows(await client.sparql_select(query))
    value: float | str = 0
    if rows:
        raw = _cell(rows[0], "value")
        if raw is not None:
            value = _as_number(raw)
    base = {
        "tool": "set_measure",
        "count": 1,
        "items": [{"value": value if value is not None else 0}],
        "truncated": False,
        "sparql": query,
    }
    materials = await materials_for_set(client, spec, registry_root=registry_root)
    return _finalize(base, output_kind="quantity", item=item, materials=materials)


async def _measure_facts(
    client: SupportsSparql,
    spec: dict[str, Any],
    measure: dict[str, Any],
    graphs: list[str],
    schema_properties: list[dict[str, Any]] | None,
    *,
    registry_root: Path | str | None,
) -> dict[str, Any]:
    """「表」— ``items`` の列を並べた表（1 件 1 行、値が無い列は ``OPTIONAL``
    で欠けたセルになる）。``item`` の ``role``/``label``/``unit`` は
    class_schema からそのまま転記する（quantity 列は ``role: "value"``、
    category 列は ``role: "category"`` — §1-4 の「item の role/label/unit を
    class_schema から転記」）。"""
    item_iris = measure["items"]
    item: dict[str, dict[str, Any]] = {}
    for idx, iri in enumerate(item_iris):
        meta = _property_meta(schema_properties, iri)
        key = f"value{idx}"
        is_quantity = meta.get("kind") == "quantity"
        entry: dict[str, Any] = {
            "var": key,
            "number": is_quantity,
            "role": "value" if is_quantity else "category",
        }
        if label := meta.get("label"):
            entry["label"] = label
        if is_quantity and (unit := meta.get("unit")):
            entry["unit"] = unit
        item[key] = entry
    if not graphs:
        base = {"tool": "set_measure", "count": 0, "items": [], "truncated": False, "sparql": None}
        return _finalize(base, output_kind="facts", item=item, materials=[])

    named = canonical_from_clauses(graphs, named=True)
    lines = [f"?s a {_ref(spec['class'])} ."]
    for i, clause in enumerate(spec["where"]):
        lines.append(_clause_pattern(clause, index=i))
    select_vars = ["?s"]
    for idx, iri in enumerate(item_iris):
        var = f"?v{idx}"
        lines.append(f"OPTIONAL {{ ?s {_ref(iri)} {var} }}")
        select_vars.append(var)
    limit = spec["limit"]
    query = (
        _XSD_PREFIX
        + f"SELECT {' '.join(select_vars)}\n{named}"
        + "WHERE { "
        + " ".join(lines)
        + " }"
        + f" ORDER BY ?s LIMIT {limit + 1}"
    )
    rows = _rows(await client.sparql_select(query))
    truncated = len(rows) > limit
    rows = rows[:limit]
    items = []
    for row in rows:
        entry = {f"value{idx}": _as_number(_cell(row, f"v{idx}")) for idx in range(len(item_iris))}
        items.append(entry)
    base = {
        "tool": "set_measure",
        "count": len(items),
        "items": items,
        "truncated": truncated,
        "sparql": query,
    }
    materials = await materials_for_set(client, spec, registry_root=registry_root)
    return _finalize(base, output_kind="facts", item=item, materials=materials)


# ----------------------------------------------------------------------------
# §3.4 (default-cards halves) — pure/near-pure card-list builders
# ----------------------------------------------------------------------------


def card_id_of(subject_key: str, tool: str, params: dict[str, Any]) -> str:
    """``"card-" + sha256(subject_key ␟ tool ␟ canonical(params))[:12]`` (§3.4)
    — deterministic for the same (subject, tool, params) triple."""
    canonical_params = json.dumps(
        params or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str
    )
    basis = f"{subject_key}\x1f{tool}\x1f{canonical_params}"
    digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()
    return f"card-{digest[:12]}"


def _declared_tool_cards(
    tools: list[dict[str, Any]], iri: str, *, dataset_id: str | None
) -> list[dict[str, Any]]:
    """Declared-tool default cards for one subject: only tools with exactly
    one ``iri`` parameter, grouped/ordered by :data:`_DEFAULT_CARD_KIND_ORDER`
    (declaration order within a group) — §3.4's rule.

    ``tool`` is the name ``POST /api/cards/run`` accepts as-is:
    ``f"{dataset_id}/{name}"`` when ``dataset_id`` is known (the normal case
    — a declared tool always belongs to exactly one dataset, and
    ``run_subject_tool`` only accepts a bare declared-tool name via its
    back-compat resolution path), else the bare ``name`` (``dataset_id``
    unavailable — e.g. a monkeypatched schema in a caller that has no
    dataset to attribute the tool to)."""
    by_kind: dict[str, list[dict[str, Any]]] = {k: [] for k in _DEFAULT_CARD_KIND_ORDER}
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        kind = tool.get("output_kind", "facts")
        if kind not in by_kind:
            continue
        params = [p for p in (tool.get("parameters") or []) if isinstance(p, dict)]
        iri_params = [p for p in params if p.get("type") == "iri"]
        if len(iri_params) != 1:
            continue
        name = str(tool.get("name") or "")
        if not name:
            continue
        full_name = f"{dataset_id}/{name}" if dataset_id else name
        by_kind[kind].append(
            {
                "tool": full_name,
                "title": str(tool.get("title") or name),
                "params": {str(iri_params[0].get("name")): iri},
                "output_kind": kind,
            }
        )
    out: list[dict[str, Any]] = []
    for kind in _DEFAULT_CARD_KIND_ORDER:
        out.extend(by_kind[kind])
    return out


async def default_cards_for_subject(
    client: SupportsSparql, registry_root: Path | str | None, iri: str
) -> list[dict[str, Any]]:
    """The default card list for one subject's page (§3.4 / O7): always
    ``subject_facts`` then ``subject_sources``; ``subject_flow`` only when
    the provenance graph actually has an edge; then this subject's class's
    declared iri-bound tools, grouped by output_kind.

    ``title`` for the two/three built-ins is the i18n key
    ``"cards:builtin.<tool>"`` (the ui translates it); a declared tool's
    title is the tool's own authored title.
    """
    subject_key = f"i:{iri}"
    cards: list[dict[str, Any]] = [
        {
            "card_id": card_id_of(subject_key, "subject_facts", {"iri": iri}),
            "title": "cards:builtin.subject_facts",
            "tool": "subject_facts",
            "params": {"iri": iri},
            "output_kind": "facts",
        },
        {
            "card_id": card_id_of(subject_key, "subject_sources", {"iri": iri}),
            "title": "cards:builtin.subject_sources",
            "tool": "subject_sources",
            "params": {"iri": iri},
            "output_kind": "breakdown",
        },
    ]

    flow = await subject_flow(client, iri, registry_root=registry_root)
    if flow.get("found"):
        cards.append(
            {
                "card_id": card_id_of(subject_key, "subject_flow", {"iri": iri}),
                "title": "cards:builtin.subject_flow",
                "tool": "subject_flow",
                "params": {"iri": iri},
                "output_kind": "flow",
            }
        )

    types = await subject_types(client, iri)
    class_iri = await pick_class_iri(client, types)
    if class_iri is not None:
        schema_fn = _load_class_schema()
        if schema_fn is not None:
            try:
                schema = await schema_fn(client, registry_root, class_iri)
            except Exception:  # best-effort: class_schema is a parallel PR
                logger.debug("default_cards_for_subject: class_schema lookup failed", exc_info=True)
                schema = None
            if isinstance(schema, dict):
                dataset_id = schema.get("dataset_id")
                declared_tools = _declared_tool_cards(
                    list(schema.get("tools") or []),
                    iri,
                    dataset_id=str(dataset_id) if dataset_id else None,
                )
                for card in declared_tools:
                    card["card_id"] = card_id_of(subject_key, card["tool"], card["params"])
                    cards.append(card)
    return cards


def default_cards_for_set(
    spec: dict[str, Any], properties: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    """The default card list for a set (絞り込み) page (§3.4): ``set_members``
    always; ``set_breakdown`` on the ``category``-kind property whose
    ``distinct_count`` is smallest (>= 2) — the one that groups the set into
    the fewest, most meaningful bars, not whichever property happens to come
    first (a near-unique property, e.g. one distinct value per subject,
    produces a breakdown where every bar has count 1 and tells the user
    nothing). Ties go to the property earlier in the class schema's own
    order (IR order — deterministic, no further tie-break needed since a
    strict "<" comparison only replaces the running best). A property whose
    ``distinct_count`` is ``None``/1 (not a real category grouping, or
    ``class_schema`` never computed it) is not a candidate; when no property
    qualifies, no breakdown card is produced at all. ``set_count`` always.
    Pure — ``properties`` is the caller's already-fetched
    ``class_schema(...)['properties']`` (or ``None`` when unavailable), so
    this function needs no store access."""
    spec = normalize_set_spec(spec)
    subject_key = f"s:{set_id_of(spec)}"
    cards: list[dict[str, Any]] = [
        {
            "card_id": card_id_of(subject_key, "set_members", {}),
            "title": "cards:builtin.set_members",
            "tool": "set_members",
            "params": {},
            "output_kind": "ranked" if spec["order_by"] is not None else "facts",
        }
    ]
    category_iri: str | None = None
    best_distinct: int | None = None
    for prop in properties or []:
        if not isinstance(prop, dict) or prop.get("kind") != "category":
            continue
        distinct_count = prop.get("distinct_count")
        if not isinstance(distinct_count, int) or isinstance(distinct_count, bool):
            continue
        if distinct_count < 2:
            continue
        if best_distinct is None or distinct_count < best_distinct:
            best_distinct = distinct_count
            category_iri = prop.get("iri")
    if category_iri:
        params = {"property": category_iri}
        cards.append(
            {
                "card_id": card_id_of(subject_key, "set_breakdown", params),
                "title": "cards:builtin.set_breakdown",
                "tool": "set_breakdown",
                "params": params,
                "output_kind": "breakdown",
            }
        )
    cards.append(
        {
            "card_id": card_id_of(subject_key, "set_count", {}),
            "title": "cards:builtin.set_count",
            "tool": "set_count",
            "params": {},
            "output_kind": "quantity",
        }
    )
    return cards


# ----------------------------------------------------------------------------
# POST /api/cards/run dispatch (§3.4) — one seam both the route and tests use.
# ----------------------------------------------------------------------------


async def run_subject_tool(
    client: SupportsSparql,
    registry_root: Path | str | None,
    subject: dict[str, Any],
    tool: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Dispatch one ``POST /api/cards/run`` call (§3.4).

    ``subject`` must already be the *normalized* form
    (:func:`asterism.subjects.validate_subject_key`'s return value) — this
    function does not re-validate it. Raises :class:`UnknownSubjectToolError`
    (→ 404) for an unrecognized tool name, :class:`SubjectKindMismatchError`
    (→ 400) when the tool needs the other subject kind, and
    :class:`asterism.subjects.SetSpecError` (→ 400) for a set tool given an
    unsupported ``at`` clause deeper in the spec (already caught by
    ``validate_subject_key`` in the normal case, but ``set_breakdown``'s
    ``property`` param is validated here too).

    ``set_measure`` (契約メモ contract_pr_f4.md §1-4) is dispatched BEFORE the
    individual/set kind gates below, unlike every other
    :data:`SET_BUILTIN_TOOLS` entry: its ``class``/``where`` travel inside
    ``params`` itself, not ``subject["spec"]`` — a card added from a 1 件の
    ページ also sends ``subject={"kind": "individual", ...}`` (the picked
    「この 1 件を指す種類」becomes ``params.where``'s link clause, built
    client-side from :func:`linking_kinds`), so gating ``set_measure`` on
    ``kind == "set"`` would reject every measure card added from an
    individual subject's page (実機所見: the ui always populates
    ``params.class``/``params.where`` itself, regardless of the page's own
    subject kind — see ``ui/src/cards/NewCardForm.tsx``'s
    ``defaultWhere``/``handleSubmit``)."""
    params = dict(params or {})
    if tool == "set_measure":
        try:
            measure_spec = normalize_set_spec(params)
        except SetSpecError as exc:
            raise SubjectToolError(str(exc)) from exc
        return await set_measure(client, measure_spec, params=params, registry_root=registry_root)

    kind = subject.get("kind")

    if kind == "individual":
        iri = subject["iri"]
        if tool == "subject_facts":
            return await subject_facts(client, iri, registry_root=registry_root)
        if tool == "subject_sources":
            return await subject_sources(client, iri, registry_root=registry_root)
        if tool == "subject_flow":
            return await subject_flow(client, iri, registry_root=registry_root)
        if tool in SET_BUILTIN_TOOLS:
            raise SubjectKindMismatchError(f"tool {tool!r} needs a set subject, got an individual")
        if "/" in tool:
            dataset_id, tool_name = tool.split("/", 1)
            declared = {t.name: t for t in load_query_tools(dataset_id, root=registry_root)}
            qt = declared.get(tool_name)
            if qt is None:
                raise UnknownSubjectToolError(f"unknown tool {tool!r}")
        else:
            # 後方互換: 接頭なしの宣言ツール名も、この主語自身のクラスから解決
            # できれば通す（§ dispatch back-compat — 上の _resolve_bare_declared_tool）。
            qt = await _resolve_bare_declared_tool(client, registry_root, iri, tool)
            if qt is None:
                raise UnknownSubjectToolError(f"unknown tool {tool!r}")
        try:
            return await run_iri_bound_tool(client, qt, iri, params, registry_root=registry_root)
        except QueryToolError as exc:
            raise SubjectToolError(str(exc)) from exc

    if kind == "set":
        spec = subject["spec"]
        if tool == "set_members":
            return await set_members(client, spec, registry_root=registry_root)
        if tool == "set_breakdown":
            property_iri = params.get("property")
            if not property_iri:
                raise SubjectToolError("set_breakdown requires params.property")
            return await set_breakdown(client, spec, property_iri, registry_root=registry_root)
        if tool == "set_count":
            return await set_count(client, spec, registry_root=registry_root)
        # ``set_measure`` is intercepted above (before this kind gate) — it
        # never reaches here.
        if tool in INDIVIDUAL_BUILTIN_TOOLS or "/" in tool:
            raise SubjectKindMismatchError(f"tool {tool!r} needs an individual subject, got a set")
        raise UnknownSubjectToolError(f"unknown tool {tool!r}")

    raise SubjectToolError(f"subject.kind must be 'individual' or 'set', got {kind!r}")
