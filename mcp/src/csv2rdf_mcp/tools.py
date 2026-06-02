"""Tool implementations exposed by the csv2rdf-mcp self-built server.

Each tool here is a regular async function that takes simple inputs and
returns a JSON-serializable dict. The :mod:`csv2rdf_mcp.server` module
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
from typing import Any, Final

from csv2rdf.oxigraph_client import OxigraphClient
from csv2rdf.starrydata import DEFAULT_ONTOLOGY

# ----------------------------------------------------------------------------
# Predicate -> output-key mapping for template_curve_fetch
# ----------------------------------------------------------------------------

SD: Final[str] = DEFAULT_ONTOLOGY
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


def _build_query(curve_iri: str) -> str:
    # We bind ``GRAPH ?g`` so the query works whether triples live in the
    # default graph (Phase 0.5 / smoke tests) or a named graph
    # (Phase 2 upload-api which targets ``sd:graph/curves``).
    safe = curve_iri.replace("<", "").replace(">", "")
    return (
        "SELECT ?p ?o WHERE {\n"
        "  { GRAPH ?g { <" + safe + "> ?p ?o } }\n"
        "  UNION\n"
        "  { <" + safe + "> ?p ?o }\n"
        "}"
    )


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
            ``https://kumagallium.github.io/csv2rdf-mcp/starrydata/resource/curve/1-1-1``.
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

    query = _build_query(curve_iri)
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

    Mirrors :func:`csv2rdf.starrydata.parse_float_array` so the round-trip
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
    query = (
        _PREFIXES
        + "SELECT DISTINCT ?sample ?comp ?name ?paper ?title WHERE {\n  "
        + "\n  ".join(where)
        + "\n} LIMIT "
        + str(limit)
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
    query = (
        _PREFIXES
        + "SELECT ?curve ?ymax ?s ?comp ?p ?title WHERE {\n"
        + f'  ?curve a sd:Curve ; sd:propertyY "{esc_py}" ;'
        + " sd:yMax ?ymax ; sd:ofSample ?s .\n"
        + "  ?s sd:fromPaper ?p .\n"
        + "  OPTIONAL { ?s sd:compositionString ?comp }\n"
        + "  OPTIONAL { ?p schema:name ?title }\n"
        + plausible_filter
        + "} ORDER BY DESC(?ymax) LIMIT "
        + str(top_n)
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
        count_q = (
            _PREFIXES
            + "SELECT (COUNT(?curve) AS ?n) WHERE {\n"
            + f'  ?curve a sd:Curve ; sd:propertyY "{esc_py}" ; sd:yMax ?ymax .\n'
            + f"  FILTER(?ymax > {float(max_plausible)})\n"
            + "}"
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
    query = (
        _PREFIXES
        + "SELECT ?etype ?fig ?py ?ymax ?ecomp ?ename ?sample ?scomp ?sname "
        + "?paper ?ptitle ?pid ?act ?atype ?atime WHERE {\n"
        + f"  BIND(<{safe}> AS ?e)\n"
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
        + "}"
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
