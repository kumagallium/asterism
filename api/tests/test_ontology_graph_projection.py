"""Tests for ``_project_ontology_graph`` (kantan-mode label plumbing fix,
task audit 2026-08): the promoted ontology graph must carry the reviewer's
authored ``mapping.yaml`` label, not just the legacy ``model.yaml`` local name.

Uses a MockTransport Oxigraph client (same pattern as ``test_ingest.py``) so no
real Oxigraph server is needed; the /store POST body is captured and parsed
back into an rdflib graph for assertions.
"""
from __future__ import annotations

import logging

import httpx
import pytest
import rdflib
from asterism.oxigraph_client import OxigraphClient, OxigraphConfig

from asterism_api.main import _project_ontology_graph

RDFS = rdflib.Namespace("http://www.w3.org/2000/01/rdf-schema#")
XRD = "https://asterism.invalid/datasets/xrd-experiment/ontology#"
XRDR = "https://asterism.invalid/datasets/xrd-experiment/resource/"

# Verbatim shape of the real bundle audited (xrd-781e7d77, promoted 2026-08):
# `mapping.yaml` carries the reviewer's authored `label:`, `model.yaml` does not.

_MAPPING_RML_TTL = f"""\
@prefix rr: <http://www.w3.org/ns/r2rml#> .
@prefix xrd: <{XRD}> .
@prefix xrdr: <{XRDR}> .
"""

_MAPPING_IR_YAML = f"""
version: 1
prefixes:
  xrd: {XRD}
  xrdr: {XRDR}
  schema: http://schema.org/
  dcterms: http://purl.org/dc/terms/
maps:
- name: sample
  source: xrd-664287b2.txt
  subject:
    template: xrdr:sample/{{preamble_1}}
    classes:
    - xrd:試料
  properties:
  - label: サンプル識別子
    object_template: {XRDR}{{preamble_1}}
    object_type: iri
    predicate: dcterms:identifier
    unit: IRI
- name: peak
  source: xrd-664287b2.txt
  subject:
    template: xrdr:peak/{{preamble_1}}/{{2θ (deg)}}
    classes:
    - xrd:ピーク値
  properties:
  - label: サンプルへのリンク
    object_template: xrdr:sample/{{preamble_1}}
    object_type: iri
    predicate: schema:about
  - label: 2θ角度
    object_template: xrd:角度
    predicate: xrd:2theta
    unit: °
  - column: 2θ (deg)
    predicate: xrd:2theta
    unit: °
    datatype: xsd:double
"""

_MODEL_YAML_MAPPING_FORM = f"""
prefixes:
  xrd: {XRD}
  xrdr: {XRDR}

classes:
  xrd:試料:
    description: "XRD measurement sample"
  xrd:ピーク値:
    description: "A single diffraction peak"

properties:
  dcterms:identifier:
    domain: xrd:試料
    range: xsd:anyURI
  xrd:2theta:
    domain: xrd:ピーク値
    range: xsd:double
    unit: "°"
"""


class _RecordingOxi:
    """OxigraphClient backed by a MockTransport that records /store POST bodies
    and /update (DROP GRAPH) calls, mirroring test_ingest.py's helper."""

    def __init__(self) -> None:
        self.store_bodies: list[bytes] = []
        self.store_graphs: list[str | None] = []
        self.updates: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/store":
                self.store_graphs.append(request.url.params.get("graph"))
                self.store_bodies.append(request.content)
                return httpx.Response(204)
            if request.url.path == "/update":
                self.updates.append(request.content.decode())
                return httpx.Response(204)
            return httpx.Response(200, text="{}", headers={"content-type": "application/json"})

        inner = httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://test"
        )
        self.client = OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner)


def _uploaded_graph(rec: _RecordingOxi) -> rdflib.Graph:
    assert rec.store_bodies, "no /store POST was made"
    g = rdflib.Graph()
    g.parse(data=rec.store_bodies[-1].decode("utf-8"), format="turtle")
    return g


@pytest.mark.asyncio
async def test_mapping_ir_label_reaches_published_graph() -> None:
    """mapping.yaml present -> rdfs:label is the reviewer's authored word, not
    the machine identifier — the exact defect audited (K19/K20 lineage)."""
    rec = _RecordingOxi()
    n = await _project_ontology_graph(
        rec.client,
        "xrd-781e7d77",
        {"mapping.yaml": _MAPPING_IR_YAML, "mapping.rml.ttl": _MAPPING_RML_TTL},
    )
    assert n > 0
    g = _uploaded_graph(rec)
    two_theta = rdflib.URIRef(XRD + "2theta")
    assert (two_theta, RDFS.label, rdflib.Literal("2θ角度")) in g
    assert (two_theta, RDFS.label, rdflib.Literal("2theta")) not in g
    assert (rdflib.URIRef(XRD + "試料"), RDFS.label, rdflib.Literal("試料")) in g


@pytest.mark.asyncio
async def test_mapping_ir_wins_over_model_yaml_when_both_present() -> None:
    rec = _RecordingOxi()
    await _project_ontology_graph(
        rec.client,
        "xrd-781e7d77",
        {
            "mapping.yaml": _MAPPING_IR_YAML,
            "mapping.rml.ttl": _MAPPING_RML_TTL,
            "model.yaml": _MODEL_YAML_MAPPING_FORM,
        },
    )
    g = _uploaded_graph(rec)
    two_theta = rdflib.URIRef(XRD + "2theta")
    # the authored label wins, not model.yaml's local-name fallback
    assert (two_theta, RDFS.label, rdflib.Literal("2θ角度")) in g


@pytest.mark.asyncio
async def test_falls_back_to_legacy_model_yaml_when_no_mapping_ir() -> None:
    rec = _RecordingOxi()
    n = await _project_ontology_graph(
        rec.client,
        "xrd-781e7d77",
        {"model.yaml": _MODEL_YAML_MAPPING_FORM, "mapping.rml.ttl": _MAPPING_RML_TTL},
    )
    assert n > 0
    g = _uploaded_graph(rec)
    two_theta = rdflib.URIRef(XRD + "2theta")
    # no authored label available -> local name fallback, but SOME label exists
    assert (two_theta, RDFS.label, rdflib.Literal("2theta")) in g


@pytest.mark.asyncio
async def test_neither_artifact_present_returns_zero_without_error() -> None:
    rec = _RecordingOxi()
    n = await _project_ontology_graph(rec.client, "xrd-781e7d77", {})
    assert n == 0
    assert not rec.store_bodies


@pytest.mark.asyncio
async def test_present_but_unprojectable_mapping_ir_warns_and_does_not_raise(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """mapping.yaml present but structurally empty (e.g. an IR with no `maps:`)
    must not silently look identical to "no mapping.yaml at all" — task item 4."""
    rec = _RecordingOxi()
    with caplog.at_level(logging.WARNING, logger="asterism_api.main"):
        n = await _project_ontology_graph(
            rec.client, "xrd-781e7d77", {"mapping.yaml": "version: 1\nprefixes: {}\n"}
        )
    assert n == 0
    assert any("mapping.yaml" in rec_.message for rec_ in caplog.records)
