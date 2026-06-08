"""Tests for the materials_project example dataset (#19) — the second,
non-starrydata dataset that proves the engine generalizes.

Covers: identity descriptor, declared typed tools (parse + run over a real
rdflib FROM-merge), the cross-dataset structure-property join to starrydata, and
the TBox ontology projection. All content lives under datasets/materials_project/;
no engine code is materials-aware.
"""
from __future__ import annotations

import json

import pytest
import rdflib

from asterism import ontology_projection as op
from asterism.datasets import datasets_root, load_dataset
from asterism.query_tools import load_query_tools, render_query, run_query_tool
from asterism.substrate import (
    CANONICAL_GRAPH_BASE,
    CONTROL_GRAPH_IRI,
    STATUS_PREDICATE,
    STATUS_PROMOTED,
    canonical_graph_iri,
)

SD = "https://kumagallium.github.io/asterism/starrydata/ontology#"
MP = "https://kumagallium.github.io/asterism/materials_project/ontology#"


def _mp_seed_ttl() -> str:
    """The committed Materials Project seed ABox (real MP facts)."""
    return (datasets_root() / "materials_project" / "seed" / "mp.ttl").read_text(encoding="utf-8")


def _mp_tools():
    return {t.name: t for t in load_query_tools("materials_project")}


def _ds_client(graphs: dict[str, str]):
    """rdflib client: each {graph_iri: ttl} loaded into that named graph.

    Canonical graphs are flagged ``promoted`` in the control graph (as a real
    ingest+promote would since #136), so the FROM-merge — which enumerates only
    promoted canonical graphs — picks them up.
    """
    ds = rdflib.ConjunctiveGraph()
    control = ds.get_context(rdflib.URIRef(CONTROL_GRAPH_IRI))
    pred = rdflib.URIRef(STATUS_PREDICATE)
    for giri, ttl in graphs.items():
        ds.get_context(rdflib.URIRef(giri)).parse(data=ttl, format="turtle")
        if giri.startswith(CANONICAL_GRAPH_BASE):
            control.add((rdflib.URIRef(giri), pred, rdflib.Literal(STATUS_PROMOTED)))

    class _C:
        async def sparql_select(self, query: str) -> dict:
            raw = ds.query(query).serialize(format="json")
            return json.loads(raw.decode() if isinstance(raw, bytes) else raw)

    return _C()


# ---------------------------------------------------------------------------
# identity + declared tools
# ---------------------------------------------------------------------------


def test_descriptor_declares_own_namespaces() -> None:
    d = load_dataset("materials_project")
    assert d is not None
    assert d.ontology_iri == MP
    assert d.resource_iri == "https://kumagallium.github.io/asterism/materials_project/resource/"


def test_query_tools_parse() -> None:
    # Loading validates every template is read-only and placeholders are sane.
    names = {t.name for t in load_query_tools("materials_project")}
    assert names == {
        "structure_by_composition",
        "materials_by_space_group",
        "materials_by_crystal_system",
        "thermoelectric_structure",
    }


def test_crystal_system_enum_is_whitelisted() -> None:
    from asterism.query_tools import QueryToolError, bind_params

    tool = _mp_tools()["materials_by_crystal_system"]
    assert bind_params(tool, {"crystal_system": "Cubic"})["crystal_system"]["token"] == '"Cubic"'
    with pytest.raises(QueryToolError, match="not in"):
        bind_params(tool, {"crystal_system": "Hexagonall"})  # typo rejected


def test_thermoelectric_structure_optional_outlier_guard() -> None:
    tool = _mp_tools()["thermoelectric_structure"]
    assert "FILTER" in render_query(tool, {"max_plausible": 3.5})
    assert "FILTER" not in render_query(tool, {})  # optional section dropped


# ---------------------------------------------------------------------------
# integration: typed tools over a real rdflib FROM-merge
# ---------------------------------------------------------------------------


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_structure_by_composition_runs() -> None:
    client = _ds_client({canonical_graph_iri("materials_project"): _mp_seed_ttl()})
    out = await run_query_tool(
        client, _mp_tools()["structure_by_composition"], {"composition": "Bi2Te3"}
    )
    assert out["count"] == 1
    row = out["items"][0]
    assert row["mp_id"] == "mp-34202"
    assert row["space_group"] == "R-3m"
    assert row["space_group_number"] == 166.0
    assert row["crystal_system"] == "Trigonal"
    assert "FROM <" in out["sparql"]


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_materials_by_crystal_system_runs() -> None:
    client = _ds_client({canonical_graph_iri("materials_project"): _mp_seed_ttl()})
    out = await run_query_tool(
        client, _mp_tools()["materials_by_crystal_system"], {"crystal_system": "Trigonal"}
    )
    assert {i["formula"] for i in out["items"]} == {"Bi2Te3"}


# ---------------------------------------------------------------------------
# the #19 headline: cross-dataset structure-property join (typed, FROM-merge)
# ---------------------------------------------------------------------------

# starrydata-side ABox: SnSe (good ZT) + a digitization outlier, in its own graph.
_SD_TTL = f"""
@prefix sd: <{SD}> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
<https://ex/sample/snse> a sd:Sample ; sd:compositionString "SnSe" .
<https://ex/sample/bad>  a sd:Sample ; sd:compositionString "Bi2Te3" .
<https://ex/curve/snse>  a sd:Curve ; sd:propertyY "ZT" ; sd:yMax "0.822"^^xsd:double ;
    sd:ofSample <https://ex/sample/snse> .
<https://ex/curve/bad>   a sd:Curve ; sd:propertyY "ZT" ; sd:yMax "9000.0"^^xsd:double ;
    sd:ofSample <https://ex/sample/bad> .
"""


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_thermoelectric_structure_joins_starrydata_and_mp() -> None:
    # ZT lives in the starrydata canonical graph; crystal structure in the
    # materials_project canonical graph; the FROM-merge joins them on composition.
    client = _ds_client(
        {
            canonical_graph_iri("starrydata"): _SD_TTL,
            canonical_graph_iri("materials_project"): _mp_seed_ttl(),
        }
    )
    out = await run_query_tool(
        client,
        _mp_tools()["thermoelectric_structure"],
        {"property_y": "ZT", "max_plausible": 3.5, "top_n": 10},
    )
    # SnSe joins to its Pnma structure; the 9000 outlier is excluded by the guard.
    assert out["count"] == 1
    row = out["items"][0]
    assert row["composition"] == "SnSe"
    assert row["value"] == 0.822
    assert row["space_group"] == "Pnma"
    assert row["crystal_system"] == "Orthorhombic"
    assert row["mp_id"] == "mp-691"
    assert "FROM <" in out["sparql"]  # ran through the canonical FROM-merge


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_thermoelectric_structure_empty_without_mp() -> None:
    # Cross-dataset tool returns nothing if the MP dataset is not in scope.
    client = _ds_client({canonical_graph_iri("starrydata"): _SD_TTL})
    out = await run_query_tool(client, _mp_tools()["thermoelectric_structure"], {})
    assert out["count"] == 0


# ---------------------------------------------------------------------------
# TBox projection (#20 step5)
# ---------------------------------------------------------------------------


def test_model_yaml_projects_classes_and_domain_range() -> None:
    text = (datasets_root() / "materials_project" / "model.yaml").read_text(encoding="utf-8")
    prefixes = op.STANDARD_PREFIXES | {"mp": MP}
    g = op.project_model_yaml(text, prefixes)

    rdfs_class = rdflib.URIRef(op.RDFS + "Class")
    classes = {str(s) for s in g.subjects(rdflib.RDF.type, rdfs_class)}
    assert {MP + "Material", MP + "CrystalStructure"} <= classes

    # mp:hasCrystalStructure: single domain (Material) + single range (CrystalStructure).
    has_struct = rdflib.URIRef(MP + "hasCrystalStructure")
    assert (has_struct, rdflib.URIRef(op.RDFS + "domain"), rdflib.URIRef(MP + "Material")) in g
    assert (
        has_struct,
        rdflib.URIRef(op.RDFS + "range"),
        rdflib.URIRef(MP + "CrystalStructure"),
    ) in g
    # a label is attached for schema_summary enrichment.
    label = rdflib.URIRef(op.RDFS + "label")
    assert (has_struct, label, rdflib.Literal("hasCrystalStructure")) in g
