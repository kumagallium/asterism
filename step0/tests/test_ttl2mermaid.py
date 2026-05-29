"""Tests for csv2rdf_step0.ttl2mermaid."""
from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from csv2rdf_step0.ttl2mermaid import (
    _main,
    _short_label,
    build_graph,
    render_doc,
    render_mermaid_block,
)


def _write(path: Path, content: str) -> Path:
    path.write_text(dedent(content).lstrip("\n"), encoding="utf-8")
    return path


# ----------------------------------------------------------------------------
# _short_label
# ----------------------------------------------------------------------------


def test_short_label_strips_known_namespace() -> None:
    ns = {"https://schema.org/": "schema"}
    assert _short_label("https://schema.org/Person", ns) == "Person"


def test_short_label_uses_common_namespace_fallback() -> None:
    # Not in caller-supplied namespaces but is a built-in known one.
    assert _short_label("http://www.w3.org/ns/prov#Entity", {}) == "Entity"


def test_short_label_extracts_local_from_unknown_iri() -> None:
    assert _short_label("https://example.com/path/Foo", {}) == "Foo"
    assert _short_label("https://example.com/things#Bar", {}) == "Bar"


# ----------------------------------------------------------------------------
# build_graph — small fixture
# ----------------------------------------------------------------------------


_MINI_TTL = """
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
@prefix schema: <https://schema.org/> .
@prefix prov: <http://www.w3.org/ns/prov#> .
@prefix sd:   <https://example.com/o#> .

sd:Paper a owl:Class ;
    rdfs:subClassOf schema:ScholarlyArticle , prov:Entity .

sd:Sample a owl:Class ;
    rdfs:subClassOf prov:Entity .

sd:fromPaper a owl:ObjectProperty ;
    rdfs:domain sd:Sample ;
    rdfs:range sd:Paper .

sd:title a owl:DatatypeProperty ;
    rdfs:domain sd:Paper ;
    rdfs:range xsd:string .

sd:atTime a owl:DatatypeProperty ;
    rdfs:domain sd:Paper ;
    rdfs:range xsd:dateTime .
"""


def test_build_graph_finds_classes(tmp_path: Path) -> None:
    ttl = _write(tmp_path / "mini.ttl", _MINI_TTL)
    graph = build_graph(ttl)
    labels = {c.label for c in graph.classes}
    assert labels == {"Paper", "Sample"}


def test_build_graph_finds_object_relations(tmp_path: Path) -> None:
    ttl = _write(tmp_path / "mini.ttl", _MINI_TTL)
    graph = build_graph(ttl)
    relations = [(r.domain_label, r.range_label, r.property_label) for r in graph.relations]
    assert ("Sample", "Paper", "fromPaper") in relations


def test_build_graph_extracts_datatype_props_with_xsd_type(tmp_path: Path) -> None:
    ttl = _write(tmp_path / "mini.ttl", _MINI_TTL)
    graph = build_graph(ttl)
    paper = next(c for c in graph.classes if c.label == "Paper")
    props = dict(paper.datatype_properties)
    assert props["title"] == "xsd_string"
    assert props["atTime"] == "xsd_dateTime"


def test_build_graph_records_external_subclass_of(tmp_path: Path) -> None:
    ttl = _write(tmp_path / "mini.ttl", _MINI_TTL)
    graph = build_graph(ttl)
    paper = next(c for c in graph.classes if c.label == "Paper")
    # External subclass_of targets are short-labeled
    assert "ScholarlyArticle" in paper.subclass_of
    assert "Entity" in paper.subclass_of


def test_build_graph_skips_subclass_within_graph(tmp_path: Path) -> None:
    """In-graph subClassOf relations should NOT appear in `subclass_of` notes
    (would be redundant — already structurally represented)."""
    ttl_str = (
        _MINI_TTL
        + "\nsd:SpecificPaper a owl:Class ; rdfs:subClassOf sd:Paper .\n"
    )
    ttl = _write(tmp_path / "tree.ttl", ttl_str)
    graph = build_graph(ttl)
    spec = next(c for c in graph.classes if c.label == "SpecificPaper")
    # sd:Paper is in the graph — should not show up as a `note for` subclass
    assert "Paper" not in spec.subclass_of


def test_build_graph_orders_results_alphabetically(tmp_path: Path) -> None:
    """CI --check mode depends on byte-stable output across runs."""
    ttl = _write(tmp_path / "mini.ttl", _MINI_TTL)
    graph1 = build_graph(ttl)
    graph2 = build_graph(ttl)
    assert [c.label for c in graph1.classes] == [c.label for c in graph2.classes]
    assert graph1.relations == graph2.relations


# ----------------------------------------------------------------------------
# render_mermaid_block — output shape
# ----------------------------------------------------------------------------


def test_render_mermaid_block_has_fenced_codeblock(tmp_path: Path) -> None:
    ttl = _write(tmp_path / "mini.ttl", _MINI_TTL)
    block = render_mermaid_block(build_graph(ttl))
    assert block.startswith("```mermaid\n")
    assert block.rstrip().endswith("```")
    assert "classDiagram" in block
    assert "direction LR" in block


def test_render_mermaid_block_renders_class_and_relation(tmp_path: Path) -> None:
    ttl = _write(tmp_path / "mini.ttl", _MINI_TTL)
    block = render_mermaid_block(build_graph(ttl))
    assert "class Paper {" in block
    assert "+title xsd_string" in block
    assert "Sample --> Paper : fromPaper" in block
    assert 'note for Paper "subClassOf' in block


def test_render_mermaid_block_has_no_colons_in_relations(tmp_path: Path) -> None:
    """★ trap T5: Mermaid relation labels MUST be colon-free."""
    ttl = _write(tmp_path / "mini.ttl", _MINI_TTL)
    block = render_mermaid_block(build_graph(ttl))
    # Find all `A --> B : label` lines and check label
    import re

    arrow_pattern = re.compile(r"-->\s*\w+\s*:\s*(.+)$", re.MULTILINE)
    for label in arrow_pattern.findall(block):
        assert ":" not in label, f"colon in relation label: {label!r}"


# ----------------------------------------------------------------------------
# render_doc — full doc body + determinism
# ----------------------------------------------------------------------------


def test_render_doc_is_deterministic(tmp_path: Path) -> None:
    ttl = _write(tmp_path / "mini.ttl", _MINI_TTL)
    a = render_doc(build_graph(ttl), title="Test")
    b = render_doc(build_graph(ttl), title="Test")
    assert a == b  # byte-identical for CI --check mode


def test_render_doc_includes_label_map(tmp_path: Path) -> None:
    ttl = _write(tmp_path / "mini.ttl", _MINI_TTL)
    doc = render_doc(build_graph(ttl))
    assert "## Class → full IRI" in doc
    assert "| `Paper` |" in doc
    assert "| `Sample` |" in doc


def test_render_doc_includes_generation_warning(tmp_path: Path) -> None:
    """Document must signal it's auto-generated so humans don't hand-edit."""
    ttl = _write(tmp_path / "mini.ttl", _MINI_TTL)
    doc = render_doc(build_graph(ttl))
    assert "Generated by" in doc
    assert "do not hand-edit" in doc.lower()


# ----------------------------------------------------------------------------
# CLI: --check mode
# ----------------------------------------------------------------------------


def test_check_mode_returns_0_when_in_sync(tmp_path: Path) -> None:
    ttl = _write(tmp_path / "mini.ttl", _MINI_TTL)
    md = tmp_path / "diagram.md"
    # Write the canonical output
    md.write_text(render_doc(build_graph(ttl)), encoding="utf-8")
    rc = _main([str(ttl), "--check", str(md)])
    assert rc == 0


def test_check_mode_returns_1_when_drifted(tmp_path: Path) -> None:
    ttl = _write(tmp_path / "mini.ttl", _MINI_TTL)
    md = _write(tmp_path / "diagram.md", "# wrong content\n\nthis is stale\n")
    rc = _main([str(ttl), "--check", str(md)])
    assert rc == 1


def test_check_mode_returns_1_when_target_missing(tmp_path: Path) -> None:
    ttl = _write(tmp_path / "mini.ttl", _MINI_TTL)
    rc = _main([str(ttl), "--check", str(tmp_path / "nonexistent.md")])
    assert rc == 1


def test_cli_writes_to_output_file(tmp_path: Path) -> None:
    ttl = _write(tmp_path / "mini.ttl", _MINI_TTL)
    out = tmp_path / "diagram.md"
    rc = _main([str(ttl), "--output", str(out)])
    assert rc == 0
    body = out.read_text(encoding="utf-8")
    assert "classDiagram" in body


# ----------------------------------------------------------------------------
# Integration sanity: on a Mermaid-trap-prone TTL
# ----------------------------------------------------------------------------


def test_external_property_with_schema_prefix_keeps_label_safe(tmp_path: Path) -> None:
    """A property like sd:author with range schema:Person — the label must
    not have any colon (Mermaid would error). The output uses bare local
    names everywhere."""
    ttl_str = """
    @prefix owl:  <http://www.w3.org/2002/07/owl#> .
    @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
    @prefix schema: <https://schema.org/> .
    @prefix sd:   <https://example.com/o#> .

    sd:Paper a owl:Class .
    schema:Person a owl:Class .

    sd:author a owl:ObjectProperty ;
        rdfs:domain sd:Paper ;
        rdfs:range schema:Person .
    """
    ttl = _write(tmp_path / "ns.ttl", ttl_str)
    block = render_mermaid_block(build_graph(ttl))
    assert "Paper --> Person : author" in block


# ----------------------------------------------------------------------------
# Argparse error path
# ----------------------------------------------------------------------------


def test_cli_errors_on_missing_ttl(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        _main(["--check", str(tmp_path / "nope.md")])
