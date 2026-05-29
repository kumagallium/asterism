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
_NUMERIC_FLOAT: Final[frozenset[str]] = frozenset(
    {"x_min", "x_max", "y_min", "y_max"}
)


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
