"""Tests for the papers example dataset (document-ontology layer) — the third
example dataset and the first of "prose shape": a paper's structured full text as
resolvable, citable IRIs, plus the data↔text fusion.

Covers the MVP gates (handoff §B.4): the declarative RML output is a strict subset
of the committed structure graph (the declarative path is faithful), structure
round-trip (paper↔sec↔para↔sent), citation (quote_with_citation), and fusion
(a curve → its figure + measurement-condition sentence) over a real rdflib
FROM-merge. All content lives under datasets/papers/; no engine code is doc-aware.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import rdflib

from asterism import ontology_projection as op
from asterism.datasets import datasets_root, load_dataset
from asterism.query_tools import load_query_tools, run_query_tool
from asterism.substrate import (
    CANONICAL_GRAPH_BASE,
    CONTROL_GRAPH_IRI,
    STATUS_PREDICATE,
    STATUS_PROMOTED,
    canonical_graph_iri,
    materialize_to_graph,
)

LIT = "https://kumagallium.github.io/asterism/papers/ontology#"
DOCO = "http://purl.org/spar/doco/"
PO = "http://www.essepuntato.it/2008/12/pattern#"
NIF = "http://persistence.uni-leipzig.de/nlp2rdf/ontologies/nif-core#"
PAPER = "https://kumagallium.github.io/asterism/papers/resource/paper/PMC5951533"
FUSION_GRAPH = CANONICAL_GRAPH_BASE + "papersfusion"


def _papers_dir() -> Path:
    return datasets_root() / "papers"


def _seed_ttl() -> str:
    return (_papers_dir() / "seed" / "paper.ttl").read_text(encoding="utf-8")


def _fusion_ttl() -> str:
    return (_papers_dir() / "fusion" / "fusion.ttl").read_text(encoding="utf-8")


def _morph_kgc_installed() -> bool:
    try:
        import morph_kgc  # noqa: F401

        return True
    except ImportError:
        return False


def _tools():
    return {t.name: t for t in load_query_tools("papers")}


def _ds_client(graphs: dict[str, str]):
    """rdflib FROM-merge client (same shape as the materials_project test): each
    {graph_iri: ttl} is loaded into that named graph, and canonical graphs are
    flagged ``promoted`` so the FROM-merge picks them up."""
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
    d = load_dataset("papers")
    assert d is not None
    assert d.ontology_iri == LIT
    assert d.resource_iri == "https://kumagallium.github.io/asterism/papers/resource/"


def test_query_tools_parse() -> None:
    assert {t.name for t in load_query_tools("papers")} == {
        "search_text",
        "quote_with_citation",
        "fetch_passage",
        "measurement_provenance",
    }


# ---------------------------------------------------------------------------
# the declarative RML is faithful: its output is a strict subset of the seed
# (the structural skeleton via ql:XPath; the post-pass adds the rest)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not _morph_kgc_installed(), reason="morph-kgc is the optional substrate extra")
def test_declarative_rml_is_subset_of_seed() -> None:
    jats_dir = _papers_dir() / "jats"
    rml = set(materialize_to_graph((jats_dir / "PMC5951533.rml.ttl").read_text(), jats_dir))
    seed = rdflib.Graph()
    seed.parse(data=_seed_ttl(), format="turtle")
    assert rml, "the declarative RML produced no triples"
    missing = rml - set(seed)
    assert not missing, f"declarative RML triples absent from the seed graph: {list(missing)[:5]}"


# ---------------------------------------------------------------------------
# structure round-trip (gate §B.4): paper -> sec -> para -> sent and back
# ---------------------------------------------------------------------------
def test_structure_round_trip() -> None:
    g = rdflib.Graph()
    g.parse(data=_seed_ttl(), format="turtle")
    # Downward: paper -> section -> paragraph -> sentence, all via po:contains.
    down = list(
        g.query(
            """
            PREFIX po: <http://www.essepuntato.it/2008/12/pattern#>
            PREFIX doco: <http://purl.org/spar/doco/>
            SELECT ?sec ?para ?sent WHERE {
              ?paper po:contains ?sec . ?sec a doco:Section ; po:contains ?para .
              ?para a doco:Paragraph ; po:contains ?sent . ?sent a doco:Sentence .
            } LIMIT 1
            """,
            initBindings={"paper": rdflib.URIRef(PAPER)},
        )
    )
    assert down, "no paper -> sec -> para -> sentence containment path"
    sec, para, sent = (str(x) for x in down[0])
    # Upward from the sentence: its paragraph, its section, the paper it was quoted
    # from, and its NIF reference context — both up-links the gate names.
    up = list(
        g.query(
            """
            PREFIX po: <http://www.essepuntato.it/2008/12/pattern#>
            PREFIX nif: <http://persistence.uni-leipzig.de/nlp2rdf/ontologies/nif-core#>
            PREFIX prov: <http://www.w3.org/ns/prov#>
            SELECT ?para ?sec ?paper ?ctx WHERE {
              ?para po:contains ?sent . ?sec po:contains ?para .
              ?sent prov:wasQuotedFrom ?paper ; nif:referenceContext ?ctx .
            } LIMIT 1
            """,
            initBindings={"sent": rdflib.URIRef(sent)},
        )
    )
    assert up, "cannot walk back up from the sentence"
    u_para, u_sec, u_paper, ctx = (str(x) for x in up[0])
    assert u_para == para and u_sec == sec and u_paper == PAPER
    assert ctx == PAPER + "/fulltext"


# ---------------------------------------------------------------------------
# recall tools over the FROM-merge
# ---------------------------------------------------------------------------
@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_search_text_finds_the_method_sentence() -> None:
    client = _ds_client({canonical_graph_iri("papers"): _seed_ttl()})
    out = await run_query_tool(client, _tools()["search_text"], {"query": "PPMS"})
    assert out["count"] >= 1
    hit = out["items"][0]
    assert "physical properties measurement system" in hit["text"]
    assert hit["structural_path"] == "4"  # Materials and Methods
    assert hit["sentence_iri"].endswith("/sec/sec4-materials-11-00649/para/1/sent/0")
    assert "FROM <" in out["sparql"]


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_quote_with_citation_is_fully_grounded() -> None:
    client = _ds_client({canonical_graph_iri("papers"): _seed_ttl()})
    node = PAPER + "/sec/sec4-materials-11-00649/para/1/sent/0"
    out = await run_query_tool(client, _tools()["quote_with_citation"], {"node": node})
    assert out["count"] == 1
    row = out["items"][0]
    # verbatim + resolvable IRI + structural path + PROV (the parse activity).
    assert "thermal transport option" in row["verbatim"]
    assert row["node_iri"] == node
    assert row["structural_path"] == "4"
    assert row["paper_iri"] == PAPER
    assert row["source_format"] == "jats"
    assert row["parser"] == "asterism-jats/0.1"
    assert row["begin_index"] is not None and row["end_index"] > row["begin_index"]


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_fetch_passage_returns_methods_paragraphs() -> None:
    client = _ds_client({canonical_graph_iri("papers"): _seed_ttl()})
    out = await run_query_tool(
        client, _tools()["fetch_passage"], {"paper": "PMC5951533", "section": "4"}
    )
    assert out["count"] == 2  # Materials and Methods has two paragraphs
    assert any("argon atmosphere" in i["text"] for i in out["items"])
    assert all(i["structural_path"] == "4" for i in out["items"])


# ---------------------------------------------------------------------------
# the headline: data↔text FUSION (gate §B.4) — a curve to its figure + condition
# ---------------------------------------------------------------------------
@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_measurement_provenance_fuses_curve_figure_and_sentence() -> None:
    client = _ds_client(
        {canonical_graph_iri("papers"): _seed_ttl(), FUSION_GRAPH: _fusion_ttl()}
    )
    out = await run_query_tool(client, _tools()["measurement_provenance"], {})
    assert out["count"] == 1
    row = out["items"][0]
    assert row["composition"] == "Ti0.5Zr0.25Hf0.25NiSn"
    assert row["property"] == "ZT"
    assert row["figure_label"] == "Figure 3"
    assert row["figure_iri"].endswith("/fig/materials-11-00649-f003")
    # the condition sentence is the real PPMS/TTO sentence, with a resolvable IRI.
    assert "physical properties measurement system" in row["condition_text"]
    assert row["condition_sentence_iri"].endswith("/sec/sec4-materials-11-00649/para/1/sent/0")
    assert row["paper_iri"] == PAPER
    assert "FROM <" in out["sparql"]


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_measurement_provenance_empty_without_paper() -> None:
    # Cross-modal tool returns nothing if the paper graph is not in scope (the
    # figure label / condition verbatim live there).
    client = _ds_client({FUSION_GRAPH: _fusion_ttl()})
    out = await run_query_tool(client, _tools()["measurement_provenance"], {})
    assert out["count"] == 0


# ---------------------------------------------------------------------------
# TBox projection (#20 step5) — schema_summary enrichment
# ---------------------------------------------------------------------------
def test_model_yaml_projects_classes_and_labels() -> None:
    text = (_papers_dir() / "model.yaml").read_text(encoding="utf-8")
    prefixes = op.STANDARD_PREFIXES | {
        "lit": LIT,
        "doco": DOCO,
        "fabio": "http://purl.org/spar/fabio/",
        "po": PO,
        "nif": NIF,
    }
    g = op.project_model_yaml(text, prefixes)
    rdfs_class = rdflib.URIRef(op.RDFS + "Class")
    classes = {str(s) for s in g.subjects(rdflib.RDF.type, rdfs_class)}
    assert {DOCO + "Section", DOCO + "Sentence", DOCO + "Figure"} <= classes
    # lit:structuralPath is used by exactly one class (Section) -> a domain + label.
    sp = rdflib.URIRef(LIT + "structuralPath")
    assert (sp, rdflib.URIRef(op.RDFS + "domain"), rdflib.URIRef(DOCO + "Section")) in g
    assert (sp, rdflib.URIRef(op.RDFS + "label"), rdflib.Literal("structuralPath")) in g
