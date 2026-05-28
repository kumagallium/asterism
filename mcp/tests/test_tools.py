"""Tests for csv2rdf_mcp.tools — drive the SPARQL client with httpx.MockTransport.

We test the tool body directly (no FastMCP transport involved) because the
SPARQL parsing logic is the interesting part. ``test_server.py`` covers the
wiring into FastMCP separately.
"""
from __future__ import annotations

import json

import httpx
import pytest
from csv2rdf.oxigraph_client import OxigraphClient, OxigraphConfig
from csv2rdf.starrydata import DEFAULT_ONTOLOGY, DEFAULT_RESOURCE

from csv2rdf_mcp.tools import CurveNotFoundError, _decode_array, template_curve_fetch

SD = DEFAULT_ONTOLOGY
SDR = DEFAULT_RESOURCE
CURVE_IRI = f"{SDR}curve/1-1-1"
SAMPLE_IRI = f"{SDR}sample/1-1"


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def _sparql_response(bindings: list[dict[str, dict[str, str]]]) -> httpx.Response:
    body = {
        "head": {"vars": ["p", "o"]},
        "results": {"bindings": bindings},
    }
    return httpx.Response(
        200,
        text=json.dumps(body),
        headers={"content-type": "application/sparql-results+json"},
    )


def _binding(p: str, o_value: str, datatype: str | None = None) -> dict:
    o: dict[str, str] = {"type": "literal", "value": o_value}
    if datatype is not None:
        o["datatype"] = datatype
    return {"p": {"type": "uri", "value": p}, "o": o}


def _uri_binding(p: str, iri: str) -> dict:
    return {
        "p": {"type": "uri", "value": p},
        "o": {"type": "uri", "value": iri},
    }


def _make_client(handler) -> OxigraphClient:
    inner = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    )
    return OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner)


# ----------------------------------------------------------------------------
# _decode_array
# ----------------------------------------------------------------------------


def test_decode_array_basic() -> None:
    assert _decode_array("[1, 2.5, 3]") == [1.0, 2.5, 3.0]


def test_decode_array_drops_nan_and_garbage() -> None:
    # NaN, None, non-numeric strings all dropped silently.
    assert _decode_array('[1, null, "x", 2]') == [1.0, 2.0]


def test_decode_array_empty_inputs() -> None:
    assert _decode_array(None) == []
    assert _decode_array("") == []
    assert _decode_array("not-json") == []
    assert _decode_array('{"not": "a list"}') == []


# ----------------------------------------------------------------------------
# template_curve_fetch — happy path
# ----------------------------------------------------------------------------


async def test_template_curve_fetch_returns_full_record() -> None:
    bindings = [
        _binding(f"{SD}propertyX", "Temperature"),
        _binding(f"{SD}propertyY", "Seebeck coefficient"),
        _binding(f"{SD}unitXString", "K"),
        _binding(f"{SD}unitYString", "V/K"),
        _binding(f"{SD}figureName", "Fig. 3a"),
        _binding(f"{SD}xValuesJSON", "[300, 350, 400]"),
        _binding(f"{SD}yValuesJSON", "[0.0001, 0.00025, 0.00035]"),
        _binding(
            f"{SD}xMin",
            "300",
            datatype="http://www.w3.org/2001/XMLSchema#double",
        ),
        _binding(
            f"{SD}xMax",
            "400",
            datatype="http://www.w3.org/2001/XMLSchema#double",
        ),
        _binding(
            f"{SD}yMin",
            "0.0001",
            datatype="http://www.w3.org/2001/XMLSchema#double",
        ),
        _binding(
            f"{SD}yMax",
            "0.00035",
            datatype="http://www.w3.org/2001/XMLSchema#double",
        ),
        _binding(
            f"{SD}pointCount",
            "3",
            datatype="http://www.w3.org/2001/XMLSchema#integer",
        ),
        _uri_binding(f"{SD}ofSample", SAMPLE_IRI),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/query"
        # Sanity check: query embeds the curve IRI
        body = request.content.decode()
        assert CURVE_IRI in body
        return _sparql_response(bindings)

    async with _make_client(handler) as client:
        result = await template_curve_fetch(CURVE_IRI, client)

    assert result["iri"] == CURVE_IRI
    assert result["found"] is True
    assert result["truncated"] is False
    assert result["property_x"] == "Temperature"
    assert result["property_y"] == "Seebeck coefficient"
    assert result["unit_x"] == "K"
    assert result["unit_y"] == "V/K"
    assert result["figure_name"] == "Fig. 3a"
    assert result["of_sample"] == SAMPLE_IRI
    assert result["x"] == [300.0, 350.0, 400.0]
    assert result["y"] == [0.0001, 0.00025, 0.00035]
    assert result["x_min"] == 300.0
    assert result["x_max"] == 400.0
    assert result["point_count"] == 3


async def test_template_curve_fetch_truncates_at_max_points() -> None:
    bindings = [
        _binding(f"{SD}xValuesJSON", "[300, 350, 400, 450, 500]"),
        _binding(f"{SD}yValuesJSON", "[1, 2, 3, 4, 5]"),
        _binding(
            f"{SD}pointCount",
            "5",
            datatype="http://www.w3.org/2001/XMLSchema#integer",
        ),
    ]

    async with _make_client(lambda r: _sparql_response(bindings)) as client:
        result = await template_curve_fetch(CURVE_IRI, client, max_points=2)

    assert result["x"] == [300.0, 350.0]
    assert result["y"] == [1.0, 2.0]
    assert result["truncated"] is True
    # point_count remains the original (untruncated) total
    assert result["point_count"] == 5


async def test_template_curve_fetch_max_points_zero_returns_empty_arrays() -> None:
    bindings = [
        _binding(f"{SD}xValuesJSON", "[1, 2]"),
        _binding(f"{SD}yValuesJSON", "[3, 4]"),
    ]
    async with _make_client(lambda r: _sparql_response(bindings)) as client:
        result = await template_curve_fetch(CURVE_IRI, client, max_points=0)
    assert result["x"] == []
    assert result["y"] == []
    assert result["truncated"] is True


# ----------------------------------------------------------------------------
# Error cases
# ----------------------------------------------------------------------------


async def test_template_curve_fetch_not_found_raises() -> None:
    async with _make_client(lambda r: _sparql_response([])) as client:
        with pytest.raises(CurveNotFoundError):
            await template_curve_fetch(CURVE_IRI, client)


async def test_template_curve_fetch_rejects_non_http_iri() -> None:
    async with _make_client(lambda r: _sparql_response([])) as client:
        with pytest.raises(ValueError, match="full http"):
            await template_curve_fetch("not-an-iri", client)
        with pytest.raises(ValueError, match="full http"):
            await template_curve_fetch("", client)


async def test_template_curve_fetch_handles_malformed_arrays_gracefully() -> None:
    # If the literal isn't valid JSON, we degrade to empty list (matches the
    # ingester's tolerance, see csv2rdf.starrydata.parse_float_array).
    bindings = [
        _binding(f"{SD}xValuesJSON", "not-json"),
        _binding(f"{SD}yValuesJSON", "[1, 2]"),
    ]
    async with _make_client(lambda r: _sparql_response(bindings)) as client:
        result = await template_curve_fetch(CURVE_IRI, client)
    assert result["x"] == []
    assert result["y"] == [1.0, 2.0]
