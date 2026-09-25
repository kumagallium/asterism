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
from urllib.parse import unquote

from asterism import crosswalk_runtime as crosswalk_runtime_mod
from asterism.crosswalk import XW as _XW_NS
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
    is_hub_graph,
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
    "hub_of_subject",
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
    "subject_hub_members",
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
INDIVIDUAL_BUILTIN_TOOLS: tuple[str, ...] = (
    "subject_facts",
    "subject_sources",
    "subject_flow",
    "subject_hub_members",
)
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
    """The part after the last ``#``/``/`` — never the raw IRI (K4).

    Percent-encoding is undone (``%280%2C0%2C10%29`` → ``(0,0,10)``): a
    subject minted from a key value carries that value URL-encoded in its
    IRI, and a subject without ``rdfs:label`` falls back to this local name
    for its heading (実機所見: 自分で入れたデータの見出しが ``%28…`` のまま
    並んだ). The decoded value is still not the raw IRI, but it is at least
    the value a person typed.
    """
    local = iri.rsplit("#", 1)[-1].rsplit("/", 1)[-1] or iri
    try:
        return unquote(local) or local
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return local


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
        if dataset_id is not None and is_hub_graph(g):
            # ハブ graph の id（crosswalk/<pid>）は registry の id
            # （crosswalk-<pid>）と食い違うので、perspective の名前を引けるよう
            # registry の id に読み替える（実機 2026-09-25: 出どころに graph id）。
            dataset_id = crosswalk_registry_id_of_hub_graph(g) or dataset_id
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
# §1.1/§1.2 (PR F14 / ADR O59) — 届く範囲を近傍（上に 1 段・下に 2 段）へ。
# O60 (PR F16 / 契約メモ §1.1/§1.3) — 共有ハブ（xw: の共有実体）を「同じもの
# の 1 つのページ」として扱う: 主語がハブそのものか、ハブへの道
# （:func:`hub_of_subject`）を持つか、あるいは何も変わらないか。
# ----------------------------------------------------------------------------


def _perspective_id_of_hub_graph(hub_graph: str) -> str | None:
    """``hub_graph``（``…/canonical/crosswalk`` か ``…/canonical/crosswalk/<id>``）
    から perspective id を戻す — :func:`asterism.crosswalk_runtime.crosswalk_graph_iri`
    の逆写像（レガシーの無名 perspective は ``DEFAULT_PERSPECTIVE_ID``）。"""
    dataset_id = dataset_id_of_canonical_graph(hub_graph)
    if dataset_id is None:
        return None
    if dataset_id == "crosswalk":
        return crosswalk_runtime_mod.DEFAULT_PERSPECTIVE_ID
    prefix = "crosswalk/"
    if dataset_id.startswith(prefix):
        rest = dataset_id[len(prefix) :]
        return rest or None
    return None


async def hub_of_subject(client: SupportsSparql, iri: str) -> dict[str, Any] | None:
    """契約メモ §1.1: 主語からハブへの道。

    - 主語がハブそのもの（ハブ graph に ``?s a ?c`` で載っている）なら
      ``{"hub_iri": iri, "graph": g, "perspective_id": pid}``。
    - そうでなければ、ハブ graph の中で主語が直接ハブを指す 1 段
      （``GRAPH <hub graph> { <s> ?p ?hub }``）、または主語の親（canonical
      graph 群で引く）がハブを指す 2 段
      （``<s> ?q ?parent . GRAPH <hub graph> { ?parent ?p ?hub }``）を探し、
      見つかれば ``{"hub_iri", "hub_label", "perspective_id", "via_parent"}``
      （1 段なら ``via_parent`` は ``None``）。
    - どちらも見つからなければ ``None``。

    ハブ graph は canonical graph 群のうち :func:`asterism.substrate.is_hub_graph`
    のもの（複数 perspective があれば辞書順に試す・決定論）。
    """
    graphs = await canonical_graphs(client)
    hub_graphs = sorted(g for g in graphs if is_hub_graph(g))
    if not hub_graphs:
        return None
    ref = _ref(iri)

    for hub_graph in hub_graphs:
        raw = await client.sparql_select(_hub_entity_ask(hub_graph, iri))
        if isinstance(raw, dict) and raw.get("boolean"):
            return {
                "hub_iri": iri,
                "graph": hub_graph,
                "perspective_id": _perspective_id_of_hub_graph(hub_graph),
            }

    for hub_graph in hub_graphs:
        rows = _rows(
            await client.sparql_select(
                f"SELECT ?hub\nWHERE {{ GRAPH {_ref(hub_graph)} {{ {ref} ?p ?hub }} "
                # 同じ主語が 2 つ以上のハブを指し得る（1 perspective に複数 concept）
                # ので、ORDER BY で決定論に 1 件を選ぶ（checker の指摘）。
                "FILTER(isIRI(?hub)) } ORDER BY ?hub LIMIT 1"
            )
        )
        hub = _cell(rows[0], "hub") if rows else None
        if hub:
            label = (await _label_lookup(client, [hub_graph], {hub}, fallback=_fallback_label)).get(
                hub, _fallback_label(hub)
            )
            return {
                "hub_iri": hub,
                "hub_label": label,
                "perspective_id": _perspective_id_of_hub_graph(hub_graph),
                "via_parent": None,
            }

    if not graphs:
        return None
    from_clause = canonical_from_clauses(graphs)
    for hub_graph in hub_graphs:
        # ``GRAPH <hub_graph> { ... }`` needs the hub graph declared ``FROM
        # NAMED`` too (SPARQL: a query with a ``FROM`` clause but no matching
        # ``FROM NAMED`` has an EMPTY named-graph set, so ``GRAPH <iri>``
        # matches nothing — 実機所見). Only the hub graph itself needs it; the
        # ``<s> ?q ?parent`` half stays a plain default-graph (FROM-merge) read.
        rows = _rows(
            await client.sparql_select(
                f"SELECT ?parent ?hub\n{from_clause}FROM NAMED {_ref(hub_graph)}\n"
                f"WHERE {{ {ref} ?q ?parent . FILTER(isIRI(?parent)) "
                f"GRAPH {_ref(hub_graph)} {{ ?parent ?p ?hub }} FILTER(isIRI(?hub)) }} "
                "ORDER BY ?parent ?hub LIMIT 1"
            )
        )
        parent = _cell(rows[0], "parent") if rows else None
        hub = _cell(rows[0], "hub") if rows else None
        if parent and hub:
            label = (await _label_lookup(client, [hub_graph], {hub}, fallback=_fallback_label)).get(
                hub, _fallback_label(hub)
            )
            return {
                "hub_iri": hub,
                "hub_label": label,
                "perspective_id": _perspective_id_of_hub_graph(hub_graph),
                "via_parent": parent,
            }
    return None


#: ハブのメンバー（ハブを指す実体）の上限（契約メモ §1.3・最大 8・IRI 辞書順）。
_HUB_MEMBER_LIMIT = 8
#: ハブ主語の候補行の上限（契約メモ §1.3 — 通常主語の :data:`_NEIGHBORHOOD_LIMIT`
#: 24 より広い。メンバーごとの近傍の和なので候補が増えやすい）。
_HUB_LINKING_LIMIT = 40


async def _hub_members(client: SupportsSparql, hub_graph: str, hub_iri: str) -> list[str]:
    """このハブを指す実体（``GRAPH <hub graph> { ?m ?p <hub> }``）— 最大
    :data:`_HUB_MEMBER_LIMIT`・IRI 辞書順（契約メモ §1.3）。"""
    ref = _ref(hub_iri)
    rows = _rows(
        await client.sparql_select(
            f"SELECT DISTINCT ?m\nWHERE {{ GRAPH {_ref(hub_graph)} {{ ?m ?p {ref} "
            # per-link の来歴（xw:CrosswalkLink）もハブを指すが、メンバーではない
            f"FILTER NOT EXISTS {{ ?m {_ref(_RDF_TYPE)} ?mt "
            f'FILTER(STRSTARTS(STR(?mt), "{_XW_NS}")) }} '
            "} FILTER(isIRI(?m)) } ORDER BY ?m"
        )
    )
    members = [m for row in rows if (m := _cell(row, "m"))]
    return members[:_HUB_MEMBER_LIMIT]


async def _member_dataset(
    client: SupportsSparql, dataset_labels_by_id: dict[str, str], member: str
) -> tuple[str | None, str | None]:
    """メンバー 1 件の ``(dataset_id, dataset_label)`` — メンバーが事実を持つ
    graph のうち、ハブ graph 自身ではない最初のもの（``_graph_counts_for_subject``
    が graph IRI の辞書順で返すので決定論）。見つからなければ
    ``(None, None)``。"""
    for g, _cnt in await _graph_counts_for_subject(client, member):
        dataset_id = dataset_id_of_canonical_graph(g)
        if dataset_id is not None and not is_hub_graph(g):
            return dataset_id, dataset_labels_by_id.get(dataset_id, dataset_id)
    return None, None


#: 親の共有先が大きすぎるときは近傍として数えない（契約メモ §1.1）。
_NEIGHBORHOOD_MAX_PARENT_MEMBERS = 5000
#: linking_kinds が返す行の上限（契約メモ §1.1）。
_NEIGHBORHOOD_LIMIT = 24


def _hub_entity_ask(hub_graph: str, iri: str) -> str:
    """``iri`` がハブ**実体**（concept の class で型付けされた主語）か。ハブの
    graph には per-link の来歴（xw:CrosswalkLink）と build の prov:Activity も
    載るので、それらは実体ではない（実機 2026-09-25: リンクの節が「ハブ」と
    判定され、メンバー 0・候補 0 のページになった）。"""
    return (
        f"ASK {{ GRAPH {_ref(hub_graph)} {{ {_ref(iri)} {_ref(_RDF_TYPE)} ?c "
        f'FILTER(!STRSTARTS(STR(?c), "{_PROV_NS}") '
        f'&& STR(?c) != "{_XW_NS}CrosswalkLink") }} }}'
    )


def crosswalk_registry_id_of_hub_graph(graph_iri: str) -> str | None:
    """ハブ graph の IRI → その perspective の registry id（``crosswalk-bridge``／
    ``crosswalk-<pid>``）。ハブ graph でなければ None。"""
    if not is_hub_graph(graph_iri):
        return None
    from asterism import crosswalk_runtime as _xw_rt

    pid = _perspective_id_of_hub_graph(graph_iri)
    try:
        return _xw_rt.crosswalk_registry_id(pid or _xw_rt.DEFAULT_PERSPECTIVE_ID)
    except ValueError:
        return None


def _not_prov_class(var: str) -> str:
    return f'FILTER(!STRSTARTS(STR({var}), "{_PROV_NS}"))'


def _not_link_class(var: str) -> str:
    """候補の「記録の種類」から来歴（PROV）と、クロスウォークの節（``xw:``
    ＝ハブ実体・per-link の ``CrosswalkLink``）を除く。ハブは**親**としては
    正しい（sibling の anchor になる）ので、親の種類には :func:`_not_prov_class`
    だけを掛ける。実機 2026-09-25: ハブができると「Crosswalk Link（この 1 件を
    指す記録）」が候補に混ざった。"""
    return f'FILTER(!STRSTARTS(STR({var}), "{_PROV_NS}") && !STRSTARTS(STR({var}), "{_XW_NS}"))'


async def linking_kinds(
    client: SupportsSparql, iri: str, *, registry_root: Path | str | None = None
) -> list[dict[str, Any]]:
    """4 つの形で「この 1 件」の近傍（届く範囲）を求める（契約メモ §1.1・ADR
    O46/O59）: 「この 1 件から上に 1 段（この 1 件が指す先＝親）、そこから下に
    2 段（親を指す記録＝兄弟、兄弟を指す記録）」。

    - ``direct``（従来）: ``?rec <p> <iri>`` — この 1 件を直接指す記録。
    - ``child_child``: ``?a <p2> <iri> . ?rec <p3> ?a`` — この 1 件を指す何か
      を、さらに指す記録。
    - ``sibling``: ``<iri> <p1> ?parent . ?rec <p2> ?parent`` — 同じ親を持つ
      記録（兄弟）。
    - ``sibling_child``: ``<iri> <p1> ?parent . ?sib <p2> ?parent . ?rec <p3>
      ?sib`` — 兄弟を指す記録。

    宣言不要、実データの形からだけ求める（O16 の汎用性方針）。来歴のクラス
    （PROV 名前空間）・リテラルの親・メンバー数が
    :data:`_NEIGHBORHOOD_MAX_PARENT_MEMBERS` を超える親は除く。行は 1 件の
    ページに「絞り込みの where」を機械が探すための材料 —
    :func:`asterism.subjects.normalize_set_spec` がそのまま受け取れる
    ``where``（1 段の link 形／2 段の via 形）を完成形で返す。候補が無ければ
    空リスト。ソートは ``hops`` 昇順→``count`` 降順→``class_iri``・
    ``property`` の辞書順（決定論）。上限 :data:`_NEIGHBORHOOD_LIMIT` 件。"""
    graphs = await canonical_graphs(client)
    if not graphs:
        return []
    from_clause = canonical_from_clauses(graphs)

    hub_graphs = sorted(g for g in graphs if is_hub_graph(g))
    for hub_graph in hub_graphs:
        raw = await client.sparql_select(_hub_entity_ask(hub_graph, iri))
        if isinstance(raw, dict) and raw.get("boolean"):
            return await _hub_linking_kinds(
                client, iri, hub_graph, from_clause, registry_root=registry_root
            )

    # ハブの graph も近傍に含める: 主語がハブを指していれば、ハブは「親」で、
    # 同じハブを指す別データセットの実体は「兄弟」＝同じもの（ユーザーの狙い:
    # 別データセットの同じ対象を 1 ページで扱う）。per-link の来歴
    # （xw:CrosswalkLink）は :func:`_not_link_class` が記録の種類から除く。

    direct_pairs = await _direct_linking_pairs(client, iri, from_clause)
    child_child_rows = await _child_child_linking_rows(client, iri, from_clause)
    parent_candidates = await _neighborhood_parents(client, iri, from_clause)
    sibling_rows = await _sibling_linking_rows(client, iri, from_clause, parent_candidates)
    sibling_child_rows = await _sibling_child_linking_rows(
        client, iri, from_clause, parent_candidates
    )

    raw_rows: list[dict[str, Any]] = []
    for cls, p, cnt in direct_pairs:
        raw_rows.append(
            {
                "class_iri": cls,
                "property": p,
                "count": cnt,
                "hops": 1,
                "path_kind": "direct",
                "anchor_iri": iri,
                "anchor_class_iri": None,
                "anchor_property": None,
                "via_property": None,
                "via_class_iri": None,
                "where": [{"property": p, "iri": iri}],
            }
        )
    for cls, p3, p2, acls, cnt in child_child_rows:
        raw_rows.append(
            {
                "class_iri": cls,
                "property": p3,
                "count": cnt,
                "hops": 2,
                "path_kind": "child_child",
                "anchor_iri": iri,
                "anchor_class_iri": None,
                "anchor_property": None,
                "via_property": p2,
                "via_class_iri": acls,
                "where": [{"property": p3, "via": {"property": p2, "iri": iri}}],
            }
        )
    for cls, p2, p1, parent, pcls, cnt in sibling_rows:
        raw_rows.append(
            {
                "class_iri": cls,
                "property": p2,
                "count": cnt,
                "hops": 2,
                "path_kind": "sibling",
                "anchor_iri": parent,
                "anchor_class_iri": pcls,
                "anchor_property": p1,
                "via_property": None,
                "via_class_iri": None,
                "where": [{"property": p2, "iri": parent}],
            }
        )
    for cls, p3, p2, p1, parent, pcls, sibcls, cnt in sibling_child_rows:
        raw_rows.append(
            {
                "class_iri": cls,
                "property": p3,
                "count": cnt,
                "hops": 3,
                "path_kind": "sibling_child",
                "anchor_iri": parent,
                "anchor_class_iri": pcls,
                "anchor_property": p1,
                "via_property": p2,
                "via_class_iri": sibcls,
                "where": [{"property": p3, "via": {"property": p2, "iri": parent}}],
            }
        )
    if not raw_rows:
        return []
    return await _finalize_linking_rows(client, registry_root, raw_rows, limit=_NEIGHBORHOOD_LIMIT)


async def _finalize_linking_rows(
    client: SupportsSparql,
    registry_root: Path | str | None,
    raw_rows: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """label 解決・重複排除（``(class_iri, where)`` が同じ行は 1 つ）・並べ替え
    （``hops`` 昇順→``count`` 降順→``class_iri``・``property`` の辞書順）・上限
    ``limit`` 件への切り詰め — :func:`linking_kinds` の生の行の共通後処理
    （契約メモ §1.3: ハブの合成行にも同じ規則を適用するため分離した）。"""
    if not raw_rows:
        return []
    class_iris = {r["class_iri"] for r in raw_rows}
    class_iris |= {r["anchor_class_iri"] for r in raw_rows if r["anchor_class_iri"]}
    class_iris |= {r["via_class_iri"] for r in raw_rows if r["via_class_iri"]}
    property_iris = {r["property"] for r in raw_rows}
    property_iris |= {r["anchor_property"] for r in raw_rows if r["anchor_property"]}
    property_iris |= {r["via_property"] for r in raw_rows if r["via_property"]}
    entity_iris = {r["anchor_iri"] for r in raw_rows}

    class_labels = await _class_labels(client, registry_root, class_iris)
    property_scope = sorted(await readable_graph_iris(client))
    property_labels = await _label_lookup(
        client, property_scope, property_iris, fallback=_fallback_label
    )
    entity_labels = await _label_lookup(
        client, property_scope, entity_iris, fallback=_fallback_label
    )

    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for r in raw_rows:
        dedupe_key = json.dumps({"class_iri": r["class_iri"], "where": r["where"]}, sort_keys=True)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        via = None
        if r["via_property"] is not None:
            via = {
                "property": r["via_property"],
                "property_label": property_labels.get(
                    r["via_property"], _fallback_label(r["via_property"])
                ),
                "class_label": class_labels.get(
                    r["via_class_iri"], _fallback_label(r["via_class_iri"])
                ),
            }
        rows.append(
            {
                "class_iri": r["class_iri"],
                "class_label": class_labels.get(r["class_iri"], _fallback_label(r["class_iri"])),
                "property": r["property"],
                "property_label": property_labels.get(
                    r["property"], _fallback_label(r["property"])
                ),
                "count": r["count"],
                "hops": r["hops"],
                "path_kind": r["path_kind"],
                "anchor_iri": r["anchor_iri"],
                "anchor_label": entity_labels.get(
                    r["anchor_iri"], _fallback_label(r["anchor_iri"])
                ),
                "anchor_class_label": (
                    class_labels.get(r["anchor_class_iri"], _fallback_label(r["anchor_class_iri"]))
                    if r["anchor_class_iri"]
                    else None
                ),
                "anchor_property": r["anchor_property"],
                "anchor_property_label": (
                    property_labels.get(r["anchor_property"], _fallback_label(r["anchor_property"]))
                    if r["anchor_property"]
                    else None
                ),
                "via": via,
                "where": r["where"],
            }
        )
    rows.sort(key=lambda r: (r["hops"], -r["count"], r["class_iri"], r["property"]))
    return rows[:limit]


async def _hub_linking_kinds(
    client: SupportsSparql,
    hub_iri: str,
    hub_graph: str,
    from_clause: str,
    *,
    registry_root: Path | str | None,
) -> list[dict[str, Any]]:
    """契約メモ §1.3: ハブ主語の候補＝メンバーごとの :func:`linking_kinds` の和
    ＋ハブ自身の direct 行。

    メンバー（``GRAPH <hub graph> { ?m ?p <hub> }``・最大
    :data:`_HUB_MEMBER_LIMIT`・IRI 辞書順）ごとに、そのメンバー自身の
    ``linking_kinds`` をそのまま呼ぶ（``where`` はメンバー起点の絶対 IRI なので
    そのまま使える）— 各行に ``via_member`` を足し、``hops`` を 1 つ足す。ハブ
    自身の direct 行（``?rec ?p <hub>``。メンバー自身がこれに当たる）は通常の
    :func:`_direct_linking_pairs` から作る（メンバーの canonical graph の
    データが hub graph 自身に載っている記録の形と同じ）。重複排除・並べ替えは
    :func:`_finalize_linking_rows` と同じ規則、上限は :data:`_HUB_LINKING_LIMIT`。
    """
    direct_pairs = await _direct_linking_pairs(client, hub_iri, from_clause)
    hub_raw_rows: list[dict[str, Any]] = []
    for cls, p, cnt in direct_pairs:
        hub_raw_rows.append(
            {
                "class_iri": cls,
                "property": p,
                "count": cnt,
                "hops": 1,
                "path_kind": "direct",
                "anchor_iri": hub_iri,
                "anchor_class_iri": None,
                "anchor_property": None,
                "via_property": None,
                "via_class_iri": None,
                "where": [{"property": p, "iri": hub_iri}],
            }
        )
    finalized: list[dict[str, Any]] = list(
        await _finalize_linking_rows(client, registry_root, hub_raw_rows, limit=_HUB_LINKING_LIMIT)
    )

    members = await _hub_members(client, hub_graph, hub_iri)
    if members:
        member_labels = await _label_lookup(
            client,
            [hub_graph, *await canonical_graphs(client)],
            set(members),
            fallback=_fallback_label,
        )
        dataset_labels_by_id = dataset_labels(registry_root)
        # ハブを指す実体の種類は、別データセットどうしで同じ名前になりがち
        # （実機 2026-09-25: 「ChemicalFormula」が 2 行）。direct 行にその種類が
        # 属するデータセットの名前を添えて、人が見分けられるようにする。
        class_dataset_label: dict[str, str] = {}
        for member in members:
            member_rows = await linking_kinds(client, member, registry_root=registry_root)
            member_dataset_id, member_dataset_label = await _member_dataset(
                client, dataset_labels_by_id, member
            )
            if member_dataset_label:
                for cls in await subject_types(client, member):
                    class_dataset_label.setdefault(cls, member_dataset_label)
            for row in member_rows:
                # メンバーの近傍のうち「ハブを親にした行」（別のメンバー＝兄弟と、
                # 兄弟を指す記録）は、ハブ自身の direct／child_child 行と同じもの
                # なので、ここでは足さない（二重・member cap の意味が崩れる）。
                if row.get("anchor_iri") == hub_iri:
                    continue
                finalized.append(
                    {
                        **row,
                        "hops": row["hops"] + 1,
                        "via_member": {
                            "iri": member,
                            "label": member_labels.get(member, _fallback_label(member)),
                            "dataset_id": member_dataset_id,
                            "dataset_label": member_dataset_label,
                        },
                    }
                )

    if members:
        for row in finalized:
            if row.get("path_kind") == "direct" and "via_member" not in row:
                label = class_dataset_label.get(str(row.get("class_iri")))
                if label:
                    row["class_dataset_label"] = label

    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for row in finalized:
        dedupe_key = json.dumps(
            {"class_iri": row["class_iri"], "where": row["where"]}, sort_keys=True
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        deduped.append(row)
    deduped.sort(key=lambda r: (r["hops"], -r["count"], r["class_iri"], r["property"]))
    return deduped[:_HUB_LINKING_LIMIT]


async def _direct_linking_pairs(
    client: SupportsSparql, iri: str, from_clause: str
) -> list[tuple[str, str, int]]:
    """``direct``（従来のクエリ・キー不変）: ``?rec <p> <iri>``。"""
    query = (
        f"SELECT ?cls ?p (COUNT(DISTINCT ?rec) AS ?cnt)\n{from_clause}"
        f"WHERE {{ ?rec ?p {_ref(iri)} ; {_ref(_RDF_TYPE)} ?cls . "
        f"{_not_link_class('?cls')} }} "
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
    return pairs


async def _child_child_linking_rows(
    client: SupportsSparql, iri: str, from_clause: str
) -> list[tuple[str, str, str, str, int]]:
    """``child_child``: ``?a <p2> <iri> . ?rec <p3> ?a`` — この 1 件を指す
    何か（``?a``）を、さらに指す記録。"""
    ref_iri = _ref(iri)
    rdf_type = _ref(_RDF_TYPE)
    query = (
        f"SELECT ?cls ?p3 ?p2 ?acls (COUNT(DISTINCT ?rec) AS ?cnt)\n{from_clause}"
        f"WHERE {{ ?a ?p2 {ref_iri} ; {rdf_type} ?acls . ?rec ?p3 ?a ; {rdf_type} ?cls . "
        f"FILTER(?p2 != {rdf_type}) FILTER(?p3 != {rdf_type}) "
        f"{_not_link_class('?cls')} {_not_link_class('?acls')} }} "
        "GROUP BY ?cls ?p3 ?p2 ?acls ORDER BY ?cls ?p3 ?p2 ?acls"
    )
    rows: list[tuple[str, str, str, str, int]] = []
    for row in _rows(await client.sparql_select(query)):
        cls, p3, p2, acls, cnt = (
            _cell(row, "cls"),
            _cell(row, "p3"),
            _cell(row, "p2"),
            _cell(row, "acls"),
            _cell(row, "cnt"),
        )
        if None in (cls, p3, p2, acls, cnt):
            continue
        with contextlib.suppress(ValueError):
            rows.append((cls, p3, p2, acls, int(float(cnt))))
    return rows


async def _neighborhood_parents(
    client: SupportsSparql, iri: str, from_clause: str
) -> list[tuple[str, str, str]]:
    """``(p1, parent, pcls)`` candidates for ``<iri> <p1> ?parent`` — typed
    IRI parents only, PROV classes excluded, and parents whose total member
    count exceeds :data:`_NEIGHBORHOOD_MAX_PARENT_MEMBERS` dropped (too big a
    shared target is not a neighborhood, 契約メモ §1.1)."""
    ref_iri = _ref(iri)
    rdf_type = _ref(_RDF_TYPE)
    query = (
        f"SELECT DISTINCT ?p1 ?parent ?pcls\n{from_clause}"
        f"WHERE {{ {ref_iri} ?p1 ?parent . FILTER(isIRI(?parent)) FILTER(?p1 != {rdf_type}) "
        f"?parent {rdf_type} ?pcls . {_not_prov_class('?pcls')} }} "
        "ORDER BY ?parent ?p1 ?pcls"
    )
    candidates: list[tuple[str, str, str]] = []
    for row in _rows(await client.sparql_select(query)):
        p1, parent, pcls = _cell(row, "p1"), _cell(row, "parent"), _cell(row, "pcls")
        if None in (p1, parent, pcls):
            continue
        candidates.append((p1, parent, pcls))
    if not candidates:
        return []
    parents = sorted({parent for _p1, parent, _pcls in candidates})
    members_query = (
        f"SELECT ?parent (COUNT(DISTINCT ?x) AS ?members)\n{from_clause}"
        f"WHERE {{ VALUES ?parent {{ {' '.join(_ref(p) for p in parents)} }} "
        "?x ?anyp ?parent . } GROUP BY ?parent"
    )
    member_counts: dict[str, int] = {}
    for row in _rows(await client.sparql_select(members_query)):
        parent, members = _cell(row, "parent"), _cell(row, "members")
        if parent is None or members is None:
            continue
        with contextlib.suppress(ValueError):
            member_counts[parent] = int(float(members))
    return [
        (p1, parent, pcls)
        for p1, parent, pcls in candidates
        if member_counts.get(parent, 0) <= _NEIGHBORHOOD_MAX_PARENT_MEMBERS
    ]


async def _sibling_linking_rows(
    client: SupportsSparql, iri: str, from_clause: str, parents: list[tuple[str, str, str]]
) -> list[tuple[str, str, str, str, str, int]]:
    """``sibling``: ``<iri> <p1> ?parent . ?rec <p2> ?parent`` (``?rec !=
    <iri>``) — records sharing the same parent as ``iri``."""
    if not parents:
        return []
    ref_iri = _ref(iri)
    rdf_type = _ref(_RDF_TYPE)
    values = " ".join(f"({_ref(p1)} {_ref(parent)})" for p1, parent, _pcls in parents)
    query = (
        f"SELECT ?cls ?p2 ?p1 ?parent ?pcls (COUNT(DISTINCT ?rec) AS ?cnt)\n{from_clause}"
        f"WHERE {{ VALUES (?p1 ?parent) {{ {values} }} {ref_iri} ?p1 ?parent . "
        f"?parent {rdf_type} ?pcls . ?rec ?p2 ?parent ; {rdf_type} ?cls . "
        f"FILTER(?rec != {ref_iri}) FILTER(?p2 != {rdf_type}) "
        f"{_not_link_class('?cls')} {_not_prov_class('?pcls')} }} "
        "GROUP BY ?cls ?p2 ?p1 ?parent ?pcls ORDER BY ?parent ?cls ?p2 ?p1"
    )
    rows: list[tuple[str, str, str, str, str, int]] = []
    for row in _rows(await client.sparql_select(query)):
        cls, p2, p1, parent, pcls, cnt = (
            _cell(row, "cls"),
            _cell(row, "p2"),
            _cell(row, "p1"),
            _cell(row, "parent"),
            _cell(row, "pcls"),
            _cell(row, "cnt"),
        )
        if None in (cls, p2, p1, parent, pcls, cnt):
            continue
        with contextlib.suppress(ValueError):
            rows.append((cls, p2, p1, parent, pcls, int(float(cnt))))
    return rows


async def _sibling_child_linking_rows(
    client: SupportsSparql, iri: str, from_clause: str, parents: list[tuple[str, str, str]]
) -> list[tuple[str, str, str, str, str, str, str, int]]:
    """``sibling_child``: ``<iri> <p1> ?parent . ?sib <p2> ?parent . ?rec <p3>
    ?sib`` (``?sib != <iri>``・``?rec != <iri>``) — records pointing at a
    sibling of ``iri``."""
    if not parents:
        return []
    ref_iri = _ref(iri)
    rdf_type = _ref(_RDF_TYPE)
    values = " ".join(f"({_ref(p1)} {_ref(parent)})" for p1, parent, _pcls in parents)
    query = (
        f"SELECT ?cls ?p3 ?p2 ?p1 ?parent ?pcls ?sibcls\n"
        f"  (COUNT(DISTINCT ?rec) AS ?cnt)\n{from_clause}"
        f"WHERE {{ VALUES (?p1 ?parent) {{ {values} }} {ref_iri} ?p1 ?parent . "
        f"?parent {rdf_type} ?pcls . ?sib ?p2 ?parent ; {rdf_type} ?sibcls . "
        f"FILTER(?sib != {ref_iri}) FILTER(?p2 != {rdf_type}) "
        f"?rec ?p3 ?sib ; {rdf_type} ?cls . "
        f"FILTER(?rec != {ref_iri}) FILTER(?p3 != {rdf_type}) "
        f"{_not_link_class('?cls')} {_not_prov_class('?pcls')} {_not_link_class('?sibcls')} }} "
        "GROUP BY ?cls ?p3 ?p2 ?p1 ?parent ?pcls ?sibcls ORDER BY ?parent ?cls ?p3 ?p2 ?p1"
    )
    rows: list[tuple[str, str, str, str, str, str, str, int]] = []
    for row in _rows(await client.sparql_select(query)):
        cls, p3, p2, p1, parent, pcls, sibcls, cnt = (
            _cell(row, "cls"),
            _cell(row, "p3"),
            _cell(row, "p2"),
            _cell(row, "p1"),
            _cell(row, "parent"),
            _cell(row, "pcls"),
            _cell(row, "sibcls"),
            _cell(row, "cnt"),
        )
        if None in (cls, p3, p2, p1, parent, pcls, sibcls, cnt):
            continue
        with contextlib.suppress(ValueError):
            rows.append((cls, p3, p2, p1, parent, pcls, sibcls, int(float(cnt))))
    return rows


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

    A 2 段 link clause (``{property, via: {property, iri}}`` — PR F14 §1-2)
    is the same existence check one hop further out: ``?s <property> ?wl{i}
    . ?wl{i} <via.property> <via.iri> .`` — the intermediate variable is
    named with ``index`` so two where clauses never collide.

    Numeric ops cast the bound value to ``xsd:double`` (mirrors
    ``query_tools``'s own ``value_range``/``top_value`` synthesis) so a
    literal without an explicit numeric datatype still compares correctly.
    Every string value is escaped via ``_escape_literal`` — never
    concatenated raw (§0)."""
    if "via" in clause:
        via = clause["via"]
        wlvar = f"?wl{index}"
        return (
            f"?s {_ref(clause['property'])} {wlvar} . "
            f"{wlvar} {_ref(via['property'])} {_ref(via['iri'])} ."
        )
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


async def subject_hub_members(
    client: SupportsSparql, iri: str, *, registry_root: Path | str | None = None
) -> dict[str, Any]:
    """ハブ 1 件の「同じものとして束ねたもの」（契約メモ §1.3）: メンバー
    （ハブを指す実体・最大 :data:`_HUB_MEMBER_LIMIT`・IRI 辞書順）ごとに、
    データセット・種類・名前の 1 行。``subject_iri`` を持たせて UI がそのまま
    そのメンバーのページへリンクできるようにする。``iri`` がハブでなければ
    （通常主語なら）0 件。"""
    item = {
        "dataset_label": {"var": "dataset_label", "number": False, "role": "category"},
        "class_label": {"var": "class_label", "number": False, "role": "category"},
        "subject_iri": {"var": "subject_iri", "number": False, "role": "subject"},
        "label": {"var": "label", "number": False, "role": "label"},
    }
    empty_base = {
        "tool": "subject_hub_members",
        "count": 0,
        "items": [],
        "truncated": False,
        "sparql": None,
    }

    graphs = await canonical_graphs(client)
    hub_graph = next((g for g in graphs if is_hub_graph(g)), None)
    if hub_graph is None:
        return _finalize(empty_base, output_kind="facts", item=item, materials=[])

    raw = await client.sparql_select(_hub_entity_ask(hub_graph, iri))
    if not (isinstance(raw, dict) and raw.get("boolean")):
        return _finalize(empty_base, output_kind="facts", item=item, materials=[])

    members = await _hub_members(client, hub_graph, iri)
    if not members:
        return _finalize(empty_base, output_kind="facts", item=item, materials=[])

    labels = await _label_lookup(
        client, [hub_graph, *graphs], set(members), fallback=_fallback_label
    )
    dataset_labels_by_id = dataset_labels(registry_root)
    items: list[dict[str, Any]] = []
    for member in members:
        types = await subject_types(client, member)
        class_iri = await pick_class_iri(client, types)
        class_label: str | None = None
        if class_iri is not None:
            class_labels_map = await _class_labels(client, registry_root, {class_iri})
            class_label = class_labels_map.get(class_iri, _fallback_label(class_iri))
        _member_dataset_id, member_dataset_label = await _member_dataset(
            client, dataset_labels_by_id, member
        )
        items.append(
            {
                "dataset_label": member_dataset_label,
                "class_label": class_label,
                "subject_iri": member,
                "label": labels.get(member, _fallback_label(member)),
            }
        )
    base = {
        "tool": "subject_hub_members",
        "count": len(items),
        "items": items,
        "truncated": False,
        "sparql": None,
    }
    return _finalize(base, output_kind="facts", item=item, materials=[])


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
    cards: list[dict[str, Any]] = []
    hub = await hub_of_subject(client, iri)
    if hub is not None and "graph" in hub:
        # 主語がハブそのもの（契約メモ §1.3）— 「同じものとして束ねたもの」を
        # 既定カードの先頭に。
        cards.append(
            {
                "card_id": card_id_of(subject_key, "subject_hub_members", {"iri": iri}),
                "title": "cards:builtin.subject_hub_members",
                "tool": "subject_hub_members",
                "params": {"iri": iri},
                "output_kind": "facts",
            }
        )
    cards.extend(
        [
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
    )

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
        if tool == "subject_hub_members":
            return await subject_hub_members(client, iri, registry_root=registry_root)
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
