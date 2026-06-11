"""Tool implementations exposed by the asterism self-built server.

Each tool here is a regular async function that takes simple inputs and
returns a JSON-serializable dict. The :mod:`asterism_mcp.server` module
wraps these with FastMCP's ``@mcp.tool`` decorator and adds the network
plumbing.

Keeping the tool body separate from the FastMCP wiring lets us unit-test
it in-process against a mocked Oxigraph (see ``mcp/tests/test_tools.py``),
without spinning up a transport.

Currently exposed:

- :func:`template_curve_fetch` — given a curve IRI, returns the raw x/y
  arrays plus contextual properties (units, sample, figure name, etc.).
  Phase 1 stores curves as ``sd:xValuesJSON``/``sd:yValuesJSON`` literals
  containing JSON arrays plus the aggregate ``xMin/xMax/yMin/yMax/pointCount``
  values; this tool parses them back into typed lists so AI clients can do
  point-level reasoning (e.g. "what's the Seebeck value at 300 K?") without
  re-implementing the JSON parsing themselves.
"""

from __future__ import annotations

import json
import re
from typing import Any, Final

from asterism.datasets import load_dataset
from asterism.oxigraph_client import OxigraphClient
from asterism.substrate import (
    ONTOLOGY_GRAPH_BASE,
    canonical_from_clauses,
    canonical_graphs,
    canonical_merge_query,
    ontology_graphs,
)

_RDFS_LABEL: Final[str] = "http://www.w3.org/2000/01/rdf-schema#label"

# ----------------------------------------------------------------------------
# Predicate -> output-key mapping for template_curve_fetch
# ----------------------------------------------------------------------------

# #20 P2-2b: the typed curve tool speaks starrydata's `sd:` vocabulary, whose IRI
# is content declared in datasets/starrydata/dataset.toml and read via the
# generic dataset loader — no import of starrydata constants. The descriptor is
# the source of truth (live in prod via the bundled datasets/ tree); the literal
# is a defensive fallback for a wheel-only install. Per-dataset generalization of
# the typed tools themselves is P4.
_SD = load_dataset("starrydata")
SD: Final[str] = (
    _SD.ontology_iri if _SD else "https://kumagallium.github.io/asterism/starrydata/ontology#"
)
SCHEMA: Final[str] = "https://schema.org/"
DCTERMS: Final[str] = "http://purl.org/dc/terms/"

# Predicates we extract as scalars (single value per curve). Anything not in
# this map is ignored in the response (prov:wasGeneratedBy etc. are
# observable via SPARQL DESCRIBE if needed, but not part of the AI-facing
# curve summary).
_SCALAR_MAP: Final[dict[str, str]] = {
    f"{SD}propertyX": "property_x",
    f"{SD}propertyY": "property_y",
    f"{SD}unitXString": "unit_x",
    f"{SD}unitYString": "unit_y",
    f"{SD}figureName": "figure_name",
    f"{SD}comments": "comments",
    f"{SD}rawFigureId": "raw_figure_id",
    f"{SD}ofSample": "of_sample",
    f"{SD}xMin": "x_min",
    f"{SD}xMax": "x_max",
    f"{SD}yMin": "y_min",
    f"{SD}yMax": "y_max",
    f"{SD}pointCount": "point_count",
    f"{DCTERMS}identifier": "identifier",
}

# Numeric scalars: cast Oxigraph's string-formatted literal to int/float.
_NUMERIC_INT: Final[frozenset[str]] = frozenset({"point_count"})
_NUMERIC_FLOAT: Final[frozenset[str]] = frozenset({"x_min", "x_max", "y_min", "y_max"})


class CurveNotFoundError(Exception):
    """Raised when the requested curve IRI has no triples in the store."""


# ----------------------------------------------------------------------------
# Internal helpers
# ----------------------------------------------------------------------------


def _parse_value(key: str, raw: str) -> Any:
    if key in _NUMERIC_INT:
        return int(raw)
    if key in _NUMERIC_FLOAT:
        return float(raw)
    return raw


# ----------------------------------------------------------------------------
# Canonical read scope = cross-dataset FROM-merge (#20 P3)
# ----------------------------------------------------------------------------
#
# Every Ask read spans the **canonical scope**: every non-retracted per-dataset
# canonical named graph (.../asterism/graph/canonical/{id}), merged into the
# query's default graph via ``FROM`` clauses. Merging (rather than the earlier
# GRAPH-union) is what lets a single join span TWO datasets — the whole point of
# "link various data through a shared ontology and query across it". Draft /
# control / ontology graphs are never in the FROM list, so unreviewed drafts
# never leak into Ask. Legacy / seed data lives in ``canonical/legacy`` (writers
# relocated + a one-time startup migration), so it is covered too.
#
# When no canonical graph exists yet the FROM block is empty and the query reads
# the real default graph — the safe pre-migration behaviour.


async def _from_merge(client: OxigraphClient) -> str:
    """The ``FROM <canonical/*>`` block scoping a read to the cross-dataset corpus.

    One round-trip enumerates the canonical graphs (excluding retracted); the
    cost is accepted per read (cache later if it shows up). Empty string when no
    canonical graphs exist (reads the real default graph).
    """
    return canonical_from_clauses(await canonical_graphs(client))


def _scoped_select(projection: str, body: str, from_block: str, tail: str = "") -> str:
    """Assemble ``<projection>\\n<FROM block>WHERE { <body> }<tail>``.

    ``projection`` carries any PREFIX decls + the SELECT clause; ``from_block`` is
    the canonical FROM-merge (possibly empty); ``tail`` carries GROUP BY / ORDER
    BY / LIMIT. The body is a plain GRAPH-less pattern read over the merged
    canonical default graph.
    """
    return f"{projection}\n{from_block}WHERE {{ {body} }}{tail}"


def _build_query(curve_iri: str, from_block: str) -> str:
    # Read the curve from the canonical FROM-merge; draft graphs are not in the
    # merge so an unreviewed curve never surfaces.
    safe = curve_iri.replace("<", "").replace(">", "")
    return _scoped_select("SELECT ?p ?o", f"<{safe}> ?p ?o", from_block)


# ----------------------------------------------------------------------------
# Public tool implementation
# ----------------------------------------------------------------------------


async def template_curve_fetch(
    curve_iri: str,
    client: OxigraphClient,
    *,
    max_points: int | None = None,
) -> dict[str, Any]:
    """Return the raw x/y arrays + summary metadata for one curve.

    Args:
        curve_iri: Full IRI of the curve, e.g.
            ``https://kumagallium.github.io/asterism/starrydata/resource/curve/1-1-1``.
        client: :class:`OxigraphClient` used to issue the SPARQL query.
        max_points: If provided, the returned ``x``/``y`` are truncated to
            this many leading points. ``None`` (default) returns the full
            arrays. Truncation is useful when an AI client only needs the
            curve shape preview and the raw arrays are large.

    Returns:
        A dict with keys:

        - ``iri``: echo of the input curve IRI
        - ``property_x``, ``property_y``, ``unit_x``, ``unit_y``: strings
        - ``figure_name``, ``comments``, ``raw_figure_id``, ``identifier``
        - ``of_sample``: sample IRI string (resolvable in the same store)
        - ``x_min``, ``x_max``, ``y_min``, ``y_max``: float
        - ``point_count``: int (total points before truncation)
        - ``x``, ``y``: list[float] (possibly truncated)
        - ``truncated``: bool — True iff max_points cut the arrays
        - ``found``: True (the false case raises instead)

    Raises:
        CurveNotFoundError: If no triples are returned for ``curve_iri``.
    """
    if not curve_iri or not curve_iri.startswith(("http://", "https://")):
        raise ValueError(f"curve_iri must be a full http(s) IRI, got {curve_iri!r}")

    query = _build_query(curve_iri, await _from_merge(client))
    raw = await client.sparql_select(query)

    # Oxigraph returns bindings as a list of dicts mapping varname -> value dict.
    bindings = raw.get("results", {}).get("bindings", [])
    if not bindings:
        raise CurveNotFoundError(curve_iri)

    out: dict[str, Any] = {
        "iri": curve_iri,
        "found": True,
        "truncated": False,
    }
    x_json: str | None = None
    y_json: str | None = None

    for row in bindings:
        p = row.get("p", {}).get("value")
        o = row.get("o", {}).get("value")
        if p is None or o is None:
            continue
        if p == f"{SD}xValuesJSON":
            x_json = o
            continue
        if p == f"{SD}yValuesJSON":
            y_json = o
            continue
        if p in _SCALAR_MAP:
            key = _SCALAR_MAP[p]
            try:
                out[key] = _parse_value(key, o)
            except (TypeError, ValueError):
                # Numeric cast failed — keep raw string rather than dropping.
                out[key] = o

    out["x"] = _decode_array(x_json)
    out["y"] = _decode_array(y_json)

    if max_points is not None and max_points >= 0:
        if len(out["x"]) > max_points or len(out["y"]) > max_points:
            out["truncated"] = True
        out["x"] = out["x"][:max_points]
        out["y"] = out["y"][:max_points]

    return out


def _decode_array(raw: str | None) -> list[float]:
    """Parse a JSON-literal array into list[float], silently dropping garbage.

    Mirrors :func:`asterism.starrydata.parse_float_array` so the round-trip
    (CSV -> Turtle literal -> Oxigraph -> back to Python list) is
    consistent. We re-implement here rather than re-export to keep
    mcp/tools.py honest about its inputs (Oxigraph string literals, not raw
    CSV cells).
    """
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[float] = []
    for v in data:
        try:
            if v is None:
                continue
            fv = float(v)
            if fv != fv:  # NaN
                continue
            out.append(fv)
        except (TypeError, ValueError):
            continue
    return out


# ============================================================================
# Phase 4 / ARiSE demo: typed query tools (Claude-free SPARQL wrappers)
# ============================================================================
# These follow the same contract as template_curve_fetch: plain async funcs
# taking simple inputs + an OxigraphClient, returning JSON-serializable dicts.
# They wrap the MIE sparql_query_examples as *typed* MCP tools so AI agents
# call a named, parameterized operation instead of writing raw SPARQL — see
# docs/architecture/ontology-mapping-boundary-and-provenance.md §5. They stay
# in core (no LLM / no Anthropic), so the runtime query path is Claude-free.

PROV: Final[str] = "http://www.w3.org/ns/prov#"

_PREFIXES: Final[str] = (
    f"PREFIX sd: <{SD}>\n"
    f"PREFIX schema: <{SCHEMA}>\n"
    f"PREFIX dcterms: <{DCTERMS}>\n"
    f"PREFIX prov: <{PROV}>\n"
)


def _sparql_escape_literal(value: str) -> str:
    """Escape a user string for safe embedding in a double-quoted SPARQL literal."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "")


def _cell(row: dict[str, Any], var: str) -> Any:
    """Return the ``.value`` of a SPARQL binding cell, or None if unbound."""
    node = row.get(var)
    if not node:
        return None
    return node.get("value")


def _bindings(raw: dict[str, Any]) -> list[dict[str, Any]]:
    results = raw.get("results", {}) if isinstance(raw, dict) else {}
    return results.get("bindings", []) if isinstance(results, dict) else []


def _to_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


async def sample_search(
    client: OxigraphClient,
    *,
    composition: str | None = None,
    property_y: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Find samples by composition substring and/or a measured property.

    Args:
        composition: case-insensitive substring matched against
            ``sd:compositionString`` (e.g. ``"Bi2Te3"``). None = no filter.
        property_y: if given, restrict to samples that have at least one Curve
            whose ``sd:propertyY`` equals this label (e.g. ``"ZT"``).
        limit: max samples returned (clamped to 1..200).

    Returns ``{query, count, results}`` where each result is
    ``{sample_iri, composition, name, paper_iri, title}``.
    """
    limit = max(1, min(int(limit), 200))
    where: list[str] = ["?sample a sd:Sample ."]
    if composition:
        esc = _sparql_escape_literal(composition.lower())
        where.append("?sample sd:compositionString ?comp .")
        where.append(f'FILTER(CONTAINS(LCASE(STR(?comp)), "{esc}"))')
    else:
        where.append("OPTIONAL { ?sample sd:compositionString ?comp }")
    if property_y:
        esc_py = _sparql_escape_literal(property_y)
        where.append(f'?c sd:ofSample ?sample ; sd:propertyY "{esc_py}" .')
    where.append("OPTIONAL { ?sample schema:name ?name }")
    where.append(
        "OPTIONAL { ?sample sd:fromPaper ?paper . OPTIONAL { ?paper schema:name ?title } }"
    )
    query = _scoped_select(
        _PREFIXES + "SELECT DISTINCT ?sample ?comp ?name ?paper ?title",
        "\n  ".join(where),
        await _from_merge(client),
        tail=" LIMIT " + str(limit),
    )
    raw = await client.sparql_select(query)
    results = [
        {
            "sample_iri": _cell(r, "sample"),
            "composition": _cell(r, "comp"),
            "name": _cell(r, "name"),
            "paper_iri": _cell(r, "paper"),
            "title": _cell(r, "title"),
        }
        for r in _bindings(raw)
    ]
    return {
        "query": {
            "composition": composition,
            "property_y": property_y,
            "limit": limit,
        },
        "count": len(results),
        "results": results,
    }


async def property_ranking(
    client: OxigraphClient,
    *,
    property_y: str,
    top_n: int = 10,
    max_plausible: float | None = None,
) -> dict[str, Any]:
    """Rank curves by per-curve peak value (``sd:yMax``) for a property label.

    Generalizes the MIE "Highest ZT" example. ``max_plausible`` excludes
    digitization/labeling outliers (e.g. ZT > 3.5); the number of excluded
    curves is returned so the agent can stay honest about data quality instead
    of fabricating a record value.

    Args:
        property_y: the ``sd:propertyY`` label, e.g. ``"ZT"`` or
            ``"Seebeck coefficient"``.
        top_n: number of top curves to return (clamped to 1..100).
        max_plausible: if set, only curves with ``yMax <= max_plausible`` rank;
            curves above it are counted into ``excluded_implausible``.

    Returns ``{property_y, top_n, max_plausible, excluded_implausible, results}``
    where each result is ``{curve_iri, value, sample_iri, composition,
    paper_iri, title}``.
    """
    if not property_y:
        raise ValueError("property_y is required")
    top_n = max(1, min(int(top_n), 100))
    esc_py = _sparql_escape_literal(property_y)
    plausible_filter = (
        f"  FILTER(?ymax <= {float(max_plausible)})\n" if max_plausible is not None else ""
    )
    rank_body = (
        f'  ?curve a sd:Curve ; sd:propertyY "{esc_py}" ;'
        + " sd:yMax ?ymax ; sd:ofSample ?s .\n"
        + "  ?s sd:fromPaper ?p .\n"
        + "  OPTIONAL { ?s sd:compositionString ?comp }\n"
        + "  OPTIONAL { ?p schema:name ?title }\n"
        + plausible_filter
    )
    from_block = await _from_merge(client)
    query = _scoped_select(
        _PREFIXES + "SELECT ?curve ?ymax ?s ?comp ?p ?title",
        rank_body,
        from_block,
        tail=" ORDER BY DESC(?ymax) LIMIT " + str(top_n),
    )
    raw = await client.sparql_select(query)
    results = [
        {
            "curve_iri": _cell(r, "curve"),
            "value": _to_float(_cell(r, "ymax")),
            "sample_iri": _cell(r, "s"),
            "composition": _cell(r, "comp"),
            "paper_iri": _cell(r, "p"),
            "title": _cell(r, "title"),
        }
        for r in _bindings(raw)
    ]
    excluded = 0
    if max_plausible is not None:
        count_body = (
            f'  ?curve a sd:Curve ; sd:propertyY "{esc_py}" ; sd:yMax ?ymax .\n'
            + f"  FILTER(?ymax > {float(max_plausible)})\n"
        )
        count_q = _scoped_select(
            _PREFIXES + "SELECT (COUNT(?curve) AS ?n)", count_body, from_block
        )
        cb = _bindings(await client.sparql_select(count_q))
        if cb:
            excluded = int(float(_cell(cb[0], "n") or 0))
    return {
        "property_y": property_y,
        "top_n": top_n,
        "max_plausible": max_plausible,
        "excluded_implausible": excluded,
        "results": results,
    }


# Map sd: activity classes to a human-friendly chain step label.
_PROV_STEP_LABEL: Final[dict[str, str]] = {
    f"{SD}IngestionActivity": "ingestion",
    f"{SD}DigitizationActivity": "digitization",
}


async def provenance_of(
    iri: str,
    client: OxigraphClient,
) -> dict[str, Any]:
    """Return the PROV chain behind a starrydata entity (curve-centric).

    Walks Curve -> Sample (``sd:ofSample``) -> Paper (``sd:fromPaper``) and the
    activities the entity ``prov:wasGeneratedBy`` (Digitization + Ingestion), so
    the demo UI can render "where did this number come from?". Sample / Paper
    IRIs degrade gracefully to the resolvable portion.

    Returns ``{iri, found, chain}`` where chain is an ordered list of
    ``{step, iri, label, detail}`` (curve -> sample -> paper -> digitization ->
    ingestion).
    """
    if not iri or not iri.startswith(("http://", "https://")):
        raise ValueError(f"iri must be a full http(s) IRI, got {iri!r}")
    safe = iri.replace("<", "").replace(">", "")
    # The body is an all-OPTIONAL walk off ?e. A required anchor (?e has at least
    # one triple) makes a non-existent IRI yield zero rows -> found=False; without
    # it the BIND alone would always emit one row (#20 P3).
    prov_body = (
        f"  BIND(<{safe}> AS ?e)\n"
        + "  ?e ?__anchor_p ?__anchor_o .\n"
        + f'  OPTIONAL {{ ?e a ?etype . FILTER(STRSTARTS(STR(?etype), "{SD}")) }}\n'
        + "  OPTIONAL { ?e sd:figureName ?fig }\n"
        + "  OPTIONAL { ?e sd:propertyY ?py }\n"
        + "  OPTIONAL { ?e sd:yMax ?ymax }\n"
        + "  OPTIONAL { ?e sd:compositionString ?ecomp }\n"
        + "  OPTIONAL { ?e schema:name ?ename }\n"
        + "  OPTIONAL {\n"
        + "    ?e sd:ofSample ?sample .\n"
        + "    OPTIONAL { ?sample sd:compositionString ?scomp }\n"
        + "    OPTIONAL { ?sample schema:name ?sname }\n"
        + "    OPTIONAL { ?sample sd:fromPaper ?paper .\n"
        + "      OPTIONAL { ?paper schema:name ?ptitle }\n"
        + "      OPTIONAL { ?paper dcterms:identifier ?pid } }\n"
        + "  }\n"
        + "  OPTIONAL { ?e sd:fromPaper ?paper .\n"
        + "    OPTIONAL { ?paper schema:name ?ptitle }\n"
        + "    OPTIONAL { ?paper dcterms:identifier ?pid } }\n"
        + "  OPTIONAL {\n"
        + "    ?e prov:wasGeneratedBy ?act .\n"
        + f'    OPTIONAL {{ ?act a ?atype . FILTER(STRSTARTS(STR(?atype), "{SD}")) }}\n'
        + "    OPTIONAL { ?act prov:atTime ?atime }\n"
        + "  }\n"
    )
    query = _scoped_select(
        _PREFIXES
        + "SELECT ?etype ?fig ?py ?ymax ?ecomp ?ename ?sample ?scomp ?sname "
        + "?paper ?ptitle ?pid ?act ?atype ?atime",
        prov_body,
        await _from_merge(client),
    )
    rows = _bindings(await client.sparql_select(query))
    if not rows:
        return {"iri": iri, "found": False, "chain": []}

    first = rows[0]
    etype = _cell(first, "etype") or ""
    chain: list[dict[str, Any]] = []

    # Curve step (the entity itself, when it is a Curve).
    if etype == f"{SD}Curve" or _cell(first, "fig") or _cell(first, "py"):
        detail_bits = []
        if _cell(first, "py"):
            detail_bits.append(str(_cell(first, "py")))
        if _cell(first, "ymax"):
            detail_bits.append(f"yMax={_cell(first, 'ymax')}")
        chain.append(
            {
                "step": "curve",
                "iri": iri,
                "label": _cell(first, "fig") or "curve",
                "detail": "; ".join(detail_bits),
            }
        )

    # Sample step (via ofSample, or the entity itself if it is a Sample).
    is_sample = etype == f"{SD}Sample"
    sample_iri = _cell(first, "sample") or (iri if is_sample else None)
    sample_comp = _cell(first, "scomp") or (_cell(first, "ecomp") if is_sample else None)
    sample_name = _cell(first, "sname") or (_cell(first, "ename") if is_sample else None)
    if sample_iri:
        chain.append(
            {
                "step": "sample",
                "iri": sample_iri,
                "label": sample_comp or sample_name or "sample",
                "detail": f"composition={sample_comp}" if sample_comp else "",
            }
        )

    # Paper step.
    paper_iri = _cell(first, "paper") or (iri if etype == f"{SD}Paper" else None)
    if paper_iri:
        chain.append(
            {
                "step": "paper",
                "iri": paper_iri,
                "label": _cell(first, "ptitle") or "paper",
                "detail": f"id={_cell(first, 'pid')}" if _cell(first, "pid") else "",
            }
        )

    # Activity steps, deduped across rows, digitization before ingestion.
    seen: set[str] = set()
    acts: list[dict[str, Any]] = []
    for r in rows:
        act = _cell(r, "act")
        if not act or act in seen:
            continue
        seen.add(act)
        atype = _cell(r, "atype") or ""
        acts.append(
            {
                "step": _PROV_STEP_LABEL.get(atype, "activity"),
                "iri": act,
                "label": atype.rsplit("#", 1)[-1] if atype else "Activity",
                "detail": f"atTime={_cell(r, 'atime')}" if _cell(r, "atime") else "",
            }
        )
    order = {"digitization": 0, "ingestion": 1, "activity": 2}
    acts.sort(key=lambda a: order.get(a["step"], 3))
    chain.extend(acts)

    return {"iri": iri, "found": True, "chain": chain}


# ============================================================================
# #18 generic Ask layer — schema-independent foundation (LLM-free)
# ============================================================================
# Everything above is starrydata-shaped (sd:Curve, sd:propertyY, ...). When a
# user designs their OWN schema in step0 and promotes data, those typed tools
# cannot see it. The two tools below are deliberately schema-AGNOSTIC: they
# introspect whatever vocabulary actually lives in the store and run arbitrary
# read-only SELECT/ASK, so an agent can answer questions over a graph it was
# never specialized for. Per docs/architecture/.../§5 these stay in core, fully
# deterministic and Claude-free; the LLM NL->SPARQL escape is layered ON TOP of
# them in a later increment (the schema_summary output is exactly the context
# that escape will ground on).


class SparqlNotReadOnlyError(Exception):
    """Raised when ``sparql_query`` is handed an update-form query."""


# Update-form keywords. Oxigraph's /query endpoint is read-only regardless, but
# we reject these up front (mirroring api ``POST /api/sparql``) so a generic
# tool can never be mistaken for write access and the caller gets a clear error.
_SPARQL_UPDATE: Final = re.compile(
    r"\b(insert|delete|load|clear|drop|create|add|move|copy)\b", re.IGNORECASE
)


def _strip_comments(query: str) -> str:
    """Drop ``#`` line comments so the read-only check can't be smuggled past."""
    return re.sub(r"#.*", "", query)


def _graph_pattern(graph: str | None, body: str) -> str:
    """The WHERE-inner pattern for ``body``.

    ``graph=<iri>`` inspects exactly that named graph (e.g. a draft graph), so the
    body is GRAPH-wrapped. ``graph=None`` reads the **canonical scope**, where the
    cross-dataset FROM-merge is added at the query level (see ``_from_merge``) and
    the body stays a plain GRAPH-less pattern.
    """
    if not graph:
        return body
    safe = graph.replace("<", "").replace(">", "")
    return f"GRAPH <{safe}> {{ {body} }}"


async def schema_summary(
    client: OxigraphClient,
    *,
    graph: str | None = None,
    max_classes: int = 50,
    max_predicates: int = 100,
    predicates_per_class: int = 25,
) -> dict[str, Any]:
    """Introspect the vocabulary actually present in the store (schema-agnostic).

    Surfaces the classes, predicates, and per-class predicate "shapes" of
    whatever data is loaded — no starrydata assumptions — so an agent (or a
    later NL->SPARQL step) can write correct queries against a user-designed
    schema it has never seen. Counts come straight from the triples, so they
    double as a coarse data-quality signal (e.g. a predicate used 3 times vs
    30 000).

    Args:
        graph: named graph IRI to inspect, or None for the default (canonical)
            graph. Promotion MOVEs drafts into the default graph, so None is
            the right target for asking about canonical data.
        max_classes: cap on returned classes (clamped 1..500), by instance count.
        max_predicates: cap on returned predicates (clamped 1..500), by usage.
        predicates_per_class: cap on predicates listed per class (clamped 1..200).

    Returns ``{graph, classes, predicates, class_shapes}`` where:

    - ``classes``: ``[{iri, count}]`` — ``?s a ?cls`` instance counts, desc.
    - ``predicates``: ``[{iri, count}]`` — all predicate usages, desc.
    - ``class_shapes``: ``[{class, predicates: [{iri, count}]}]`` — for each
      class, the predicates used on its instances (the implicit shape).
    """
    max_classes = max(1, min(int(max_classes), 500))
    max_predicates = max(1, min(int(max_predicates), 500))
    predicates_per_class = max(1, min(int(predicates_per_class), 200))

    # Security (M2): an explicit graph must be a PROMOTED canonical or ontology
    # graph. This tool is always-on (it runs even in the typed-only topology-B
    # profile), so without this an arbitrary `graph=<draft IRI>` would leak the
    # vocabulary / usage counts of unreviewed data. graph=None (canonical scope)
    # is always allowed.
    if graph is not None:
        allowed = set(await canonical_graphs(client)) | set(await ontology_graphs(client))
        if graph not in allowed:
            raise ValueError(
                "graph must be a promoted canonical or ontology graph; "
                "draft / control graphs are not exposed via schema_summary"
            )

    # graph=None reads the cross-dataset canonical FROM-merge; an explicit graph
    # reads that one named graph directly (no FROM).
    from_block = "" if graph else await _from_merge(client)
    classes_q = _scoped_select(
        "SELECT ?cls (COUNT(DISTINCT ?s) AS ?n)",
        _graph_pattern(graph, "?s a ?cls ."),
        from_block,
        tail=" GROUP BY ?cls ORDER BY DESC(?n) LIMIT " + str(max_classes),
    )
    preds_q = _scoped_select(
        "SELECT ?p (COUNT(*) AS ?n)",
        _graph_pattern(graph, "?s ?p ?o ."),
        from_block,
        tail=" GROUP BY ?p ORDER BY DESC(?n) LIMIT " + str(max_predicates),
    )

    classes = [
        {"iri": _cell(r, "cls"), "count": int(float(_cell(r, "n") or 0))}
        for r in _bindings(await client.sparql_select(classes_q))
        if _cell(r, "cls")
    ]
    predicates = [
        {"iri": _cell(r, "p"), "count": int(float(_cell(r, "n") or 0))}
        for r in _bindings(await client.sparql_select(preds_q))
        if _cell(r, "p")
    ]

    # Per-class shapes: one bounded query per class keeps each result small and
    # avoids a single giant GROUP BY that the store may truncate unpredictably.
    class_shapes: list[dict[str, Any]] = []
    for cls in classes:
        cls_iri = str(cls["iri"]).replace("<", "").replace(">", "")
        shape_q = _scoped_select(
            "SELECT ?p (COUNT(*) AS ?n)",
            _graph_pattern(graph, f"?s a <{cls_iri}> ; ?p ?o ."),
            from_block,
            tail=" GROUP BY ?p ORDER BY DESC(?n) LIMIT " + str(predicates_per_class),
        )
        shape = [
            {"iri": _cell(r, "p"), "count": int(float(_cell(r, "n") or 0))}
            for r in _bindings(await client.sparql_select(shape_q))
            if _cell(r, "p")
        ]
        class_shapes.append({"class": cls["iri"], "predicates": shape})

    # #20 step5: enrich ABox-derived vocabulary with TBox labels from the
    # projected ontology graph(s). Only for the canonical scope (graph=None); an
    # explicit graph is inspected as-is. Invariant: no ontology graph -> no labels,
    # so schema_summary still works purely from ABox introspection (ADR §2).
    if graph is None:
        labels = await _ontology_labels(client)
        if labels:
            _attach_labels(classes, labels)
            _attach_labels(predicates, labels)
            for shape in class_shapes:
                _attach_label_value(shape, "class", labels)
                _attach_labels(shape["predicates"], labels)

    return {
        "graph": graph,
        "classes": classes,
        "predicates": predicates,
        "class_shapes": class_shapes,
    }


async def _ontology_labels(client: OxigraphClient) -> dict[str, str]:
    """``term IRI -> rdfs:label`` from the projected ontology graph(s) (#20 step5).

    Reads named graphs under the ontology prefix directly (these are deliberately
    NOT in the canonical scope — TBox is separate from citable ABox). Empty dict
    when no ontology graph exists, so callers degrade to label-free output.
    """
    q = (
        "SELECT ?t ?l WHERE { GRAPH ?g { ?t <" + _RDFS_LABEL + "> ?l } "
        f'FILTER(STRSTARTS(STR(?g), "{ONTOLOGY_GRAPH_BASE}")) }}'
    )
    out: dict[str, str] = {}
    for r in _bindings(await client.sparql_select(q)):
        term, label = _cell(r, "t"), _cell(r, "l")
        if term and label and term not in out:
            out[term] = label
    return out


def _attach_labels(items: list[dict[str, Any]], labels: dict[str, str]) -> None:
    for item in items:
        _attach_label_value(item, "iri", labels)


def _attach_label_value(item: dict[str, Any], key: str, labels: dict[str, str]) -> None:
    label = labels.get(str(item.get(key)))
    if label:
        item["label"] = label


def _flatten_cell(node: dict[str, Any] | None) -> dict[str, Any] | None:
    """Flatten a SPARQL-Results binding cell to ``{value, type, datatype?, lang?}``."""
    if not node:
        return None
    out: dict[str, Any] = {"value": node.get("value"), "type": node.get("type")}
    if "datatype" in node:
        out["datatype"] = node["datatype"]
    if "xml:lang" in node:
        out["lang"] = node["xml:lang"]
    return out


async def sparql_query(
    query: str,
    client: OxigraphClient,
    *,
    max_rows: int = 200,
) -> dict[str, Any]:
    """Run an arbitrary read-only SPARQL SELECT/ASK and return flat rows.

    The schema-agnostic escape hatch: once :func:`schema_summary` has revealed
    the vocabulary, this executes any SELECT/ASK against it. Update-form queries
    (INSERT/DELETE/MOVE/...) are rejected — this is read-only by contract, same
    as api ``POST /api/sparql``.

    Cross-dataset scope: the query is rewritten to read the canonical FROM-merge
    (``FROM`` + ``FROM NAMED`` over every non-retracted canonical graph) so plain
    patterns join ACROSS datasets through shared vocabulary, unless it already
    declares its own ``FROM`` (then it is left as-is). When the rewrite changes
    the query, the executed form is returned in ``effective_query`` so the caller
    can disclose exactly what ran.

    Args:
        query: a SPARQL 1.1 SELECT or ASK query string.
        max_rows: cap on returned rows (clamped 1..2000). ``truncated`` reports
            whether the cap cut results so the caller never mistakes a capped
            answer for the whole set.

    Returns ``{columns, rows, count, truncated}`` for SELECT (each row maps a
    var name to ``{value, type, datatype?, lang?}`` or None), or
    ``{boolean, columns: [], rows: [], count: 0, truncated: false}`` for ASK.
    Adds ``effective_query`` when the cross-dataset rewrite changed the query.

    Raises:
        ValueError: empty query.
        SparqlNotReadOnlyError: the query contains an update-form keyword.
    """
    q = (query or "").strip()
    if not q:
        raise ValueError("query is required")
    if _SPARQL_UPDATE.search(_strip_comments(q)):
        raise SparqlNotReadOnlyError(
            "read-only: update-form queries (INSERT/DELETE/...) are not allowed"
        )
    max_rows = max(1, min(int(max_rows), 2000))

    effective = await canonical_merge_query(client, q)
    extra = {"effective_query": effective} if effective != q else {}
    raw = await client.sparql_select(effective)

    # ASK returns {"head": {}, "boolean": true/false} — no bindings.
    if isinstance(raw, dict) and "boolean" in raw:
        return {
            "boolean": bool(raw["boolean"]),
            "columns": [],
            "rows": [],
            "count": 0,
            "truncated": False,
            **extra,
        }

    head = raw.get("head", {}) if isinstance(raw, dict) else {}
    columns = head.get("vars", []) if isinstance(head, dict) else []
    bindings = _bindings(raw)
    truncated = len(bindings) > max_rows
    rows = [
        {var: _flatten_cell(r.get(var)) for var in columns}
        for r in bindings[:max_rows]
    ]
    return {
        "columns": columns,
        "rows": rows,
        "count": len(rows),
        "truncated": truncated,
        **extra,
    }
