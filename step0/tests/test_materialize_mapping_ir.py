"""materialize x Mapping IR: extraction, compilation, precedence, artifacts.

The §9 contract is now a yaml mapping-spec block (ADR mapping-ir-compiler.md);
materialize extracts it, compiles it deterministically, and writes BOTH the
reviewed IR (``{name}-mapping.yaml``) and the compiled RML
(``{name}-mapping.rml.ttl``) so everything downstream of materialize
(registry / ingest / 422 gate / T9) is unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("yaml")
pytest.importorskip("asterism.functions")

from asterism_step0.materialize import materialize_schema

IR_BLOCK = """\
version: 1
prefixes:
  ex: "https://example.org/ns#"
  exr: "https://example.org/r/"
maps:
  - name: thing
    source: data.csv
    subject:
      template: "exr:thing/{id}"
      classes: [ex:Thing]
    properties:
      - predicate: ex:name
        column: name
"""

PROPOSAL_WITH_IR = f"""\
# Schema proposal

### 1. Class hierarchy

```mermaid
classDiagram
  class Thing
```

### 6. rdf-config model.yaml

```yaml
- Thing <https://example.org/r/thing/1>:
    - a: ex:Thing
```

### 7. MIE YAML extras

```yaml
schema_info:
  title: things
```

### 8. Ingester sketch

```python
def ingest(): ...
```

### 9. Declarative mapping spec

```yaml
{IR_BLOCK}```
"""


def test_ir_extracted_compiled_and_written(tmp_path: Path) -> None:
    result = materialize_schema(PROPOSAL_WITH_IR, tmp_path, "demo", write=True)
    assert result.mapping_ir_yaml is not None
    assert "exr:thing/{id}" in result.mapping_ir_yaml
    assert result.mapping_ir_issues == []
    # compiled RML — full IRI expanded into the template, rmlf namespace correct
    assert result.rml_ttl is not None
    assert 'rr:template "https://example.org/r/thing/{id}"' in result.rml_ttl
    assert "@prefix rmlf: <http://w3id.org/rml/> ." in result.rml_ttl
    # both artifacts on disk
    assert (tmp_path / "demo-mapping.yaml").exists()
    assert (tmp_path / "demo-mapping.rml.ttl").exists()
    assert result.written_paths["mapping_ir"].endswith("demo-mapping.yaml")
    assert result.written_paths["rml_ttl"].endswith("demo-mapping.rml.ttl")
    # the MIE/model blocks were not confused with the mapping spec
    assert result.mie_yaml is not None and "schema_info" in result.mie_yaml
    assert result.rdf_config_model is not None and "a: ex:Thing" in result.rdf_config_model
    assert result.complete


def test_ir_parse_issues_are_collected_not_fatal(tmp_path: Path) -> None:
    broken = PROPOSAL_WITH_IR.replace("column: name", "colunm: name")
    result = materialize_schema(broken, tmp_path, "demo", write=True)
    assert result.mapping_ir_yaml is not None
    assert result.rml_ttl is None
    assert any("colunm" in i for i in result.mapping_ir_issues)
    assert any("could not be compiled" in w for w in result.warnings)
    # the spec itself is still persisted for the fix round
    assert (tmp_path / "demo-mapping.yaml").exists()
    assert not (tmp_path / "demo-mapping.rml.ttl").exists()


def test_ir_wins_over_stale_turtle_block_without_warning(tmp_path: Path) -> None:
    """A leftover/stray turtle block next to a mapping spec is inert — and NOT
    a warning: the UI treats any warning as "cannot be ingested"
    (materializeUsable requires warnings == []) and feeds warnings verbatim to
    the one-click AI fix, so an informational note here wrongly blocked a
    perfectly ingestable design (live production report, 2026-07-06)."""
    with_turtle = PROPOSAL_WITH_IR + (
        "\n### 9b. RML declarative mapping (legacy)\n\n"
        "```turtle\n@prefix rr: <http://www.w3.org/ns/r2rml#> .\n```\n"
    )
    result = materialize_schema(with_turtle, tmp_path, "demo", write=False)
    assert result.mapping_ir_yaml is not None
    assert result.rml_ttl is not None
    assert "r2rml" in result.rml_ttl  # compiled output, not the stale block…
    assert "rr:TriplesMap" in result.rml_ttl  # …which had no TriplesMap at all
    assert result.warnings == []
    assert result.complete


def test_stray_sample_turtle_fence_does_not_warn_or_block(tmp_path: Path) -> None:
    """The live false positive: a model rendering §7 sample_rdf_entries as a
    ```turtle fence. The old lone-turtle language fallback claimed it as a
    legacy §9 and the both-blocks warning blocked the save."""
    with_sample = PROPOSAL_WITH_IR.replace(
        "### 8. Ingester sketch",
        "### 7b. Sample RDF entries\n\n"
        "```turtle\n<https://example.org/r/thing/1> a <https://example.org/ns#Thing> .\n```\n\n"
        "### 8. Ingester sketch",
    )
    result = materialize_schema(with_sample, tmp_path, "demo", write=False)
    assert result.mapping_ir_yaml is not None
    assert result.rml_ttl is not None and "rr:TriplesMap" in result.rml_ttl
    assert result.warnings == []
    assert result.complete


def test_legacy_turtle_only_proposal_unchanged(tmp_path: Path) -> None:
    legacy = PROPOSAL_WITH_IR.replace(
        "### 9. Declarative mapping spec\n\n```yaml\n" + IR_BLOCK + "```\n",
        "### 9. RML declarative mapping\n\n"
        "```turtle\n<#M> a <http://www.w3.org/ns/r2rml#TriplesMap> .\n```\n",
    )
    result = materialize_schema(legacy, tmp_path, "demo", write=False)
    assert result.mapping_ir_yaml is None
    assert result.rml_ttl is not None and "TriplesMap" in result.rml_ttl
    assert result.mapping_ir_issues == []


def test_mie_absent_does_not_steal_mapping_spec(tmp_path: Path) -> None:
    """A truncated proposal (no MIE section) must still classify the mapping
    spec as the mapping spec — the MIE lang-only fallback must not claim it."""
    truncated = PROPOSAL_WITH_IR.replace(
        "### 7. MIE YAML extras\n\n```yaml\nschema_info:\n  title: things\n```\n\n", ""
    )
    result = materialize_schema(truncated, tmp_path, "demo", write=False)
    assert result.mapping_ir_yaml is not None
    assert "maps:" in result.mapping_ir_yaml
    assert result.mie_yaml is None
    assert result.rml_ttl is not None


# ---------------------------------------------------------------------------
# Source dialects (ADR source-dialect.md): --source-dir pinning
# ---------------------------------------------------------------------------


def test_source_dir_pins_detected_dialects(tmp_path: Path) -> None:
    src = tmp_path / "sources"
    src.mkdir()
    lines = ["id,名前"] + [f"{i},材料{i}" for i in range(6)]
    (src / "data.csv").write_bytes("\n".join(lines).encode("cp932") + b"\n")

    result = materialize_schema(PROPOSAL_WITH_IR, tmp_path / "out", "demo", source_dir=src)
    assert result.mapping_ir_issues == []
    assert result.mapping_ir_yaml is not None and "dialects:" in result.mapping_ir_yaml
    assert "cp932" in result.mapping_ir_yaml
    assert result.rml_ttl is not None
    assert 'ast:sourceEncoding "cp932"' in result.rml_ttl
    assert "@prefix ast: <https://kumagallium.github.io/asterism/vocab#> ." in result.rml_ttl
    # the persisted spec artifact carries the pinned dialect too
    written = Path(result.written_paths["mapping_ir"]).read_text(encoding="utf-8")
    assert "cp932" in written


def test_source_dir_with_clean_source_changes_nothing(tmp_path: Path) -> None:
    src = tmp_path / "sources"
    src.mkdir()
    lines = ["id,name"] + [f"{i},row{i}" for i in range(6)]
    (src / "data.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")

    plain = materialize_schema(PROPOSAL_WITH_IR, tmp_path / "o1", "demo")
    dialected = materialize_schema(PROPOSAL_WITH_IR, tmp_path / "o2", "demo", source_dir=src)
    assert dialected.mapping_ir_yaml == plain.mapping_ir_yaml  # byte-identical
    assert dialected.rml_ttl == plain.rml_ttl


def test_apply_source_dialects_missing_file_is_noop(tmp_path: Path) -> None:
    from asterism_step0.materialize import apply_source_dialects

    assert apply_source_dialects(IR_BLOCK, tmp_path) == IR_BLOCK


# ---------------------------------------------------------------------------
# Display-unit auto-completion (task #10): a bracketed column name → property
# `unit`, filled deterministically at materialize time (units never reach RML).
# ---------------------------------------------------------------------------

_IR_BRACKETED = """\
version: 1
prefixes:
  ex: "https://example.org/ns#"
  exr: "https://example.org/r/"
maps:
  - name: measurement
    source: data.csv
    subject:
      template: "exr:m/{id}"
      classes: [ex:Measurement]
    properties:
      - predicate: ex:resistivity
        column: "Resistivity(Ohm m)"
      - predicate: ex:name
        column: name
"""


def test_bracketed_column_unit_is_auto_completed(tmp_path: Path) -> None:
    import yaml

    proposal = PROPOSAL_WITH_IR.replace(IR_BLOCK, _IR_BRACKETED)
    result = materialize_schema(proposal, tmp_path, "demo", write=True)
    assert result.mapping_ir_issues == []
    assert result.mapping_ir_yaml is not None
    props = {
        p["predicate"]: p for p in yaml.safe_load(result.mapping_ir_yaml)["maps"][0]["properties"]
    }
    # the display unit is derived from the bracketed column name, no model call
    assert props["ex:resistivity"]["unit"] == "Ohm m"
    # a plain column name yields no unit (no over-completion)
    assert "unit" not in props["ex:name"]
    # the persisted spec carries it; the spec still compiles to RML
    written = (tmp_path / "demo-mapping.yaml").read_text(encoding="utf-8")
    assert "Ohm m" in written
    assert result.rml_ttl is not None and "rr:TriplesMap" in result.rml_ttl


def test_clean_columns_get_no_spurious_unit(tmp_path: Path) -> None:
    # The stock IR (columns without brackets) is untouched — enrichment is a
    # no-op, so no `unit:` line is invented.
    result = materialize_schema(PROPOSAL_WITH_IR, tmp_path, "demo", write=False)
    assert result.mapping_ir_yaml is not None
    assert "unit:" not in result.mapping_ir_yaml


# ----------------------------------------------------------------------------
# Deterministic diagram (ir2mermaid) wiring
# ----------------------------------------------------------------------------


def test_diagram_compiled_from_ir_replaces_the_sketch(tmp_path: Path) -> None:
    """With a parseable spec, diagram.md is compiled — not the LLM's §1.

    The §1 sketch in the fixture is an EMPTY ``class Thing`` (exactly the
    observed ZEM failure shape); the compiled diagram must carry the
    property INSIDE the box, plus the provenance table below the fence.
    """
    result = materialize_schema(PROPOSAL_WITH_IR, tmp_path, "demo", write=True)
    assert result.diagram_from_ir is True
    assert result.mermaid is not None
    assert "class Thing {" in result.mermaid
    assert "+name" in result.mermaid
    written = (tmp_path / "diagram.md").read_text(encoding="utf-8")
    assert "+name" in written
    assert "## Properties" in written
    assert "column `name`" in written
    # the fenced block still comes first and closes before the table
    assert written.index("```mermaid") < written.index("## Properties")


def test_diagram_md_is_the_document_every_producer_shares(tmp_path: Path) -> None:
    """``diagram_md`` = the artifact; ``mermaid`` = only its fenced payload.

    Callers that persist the artifact themselves (the api's registry) read
    ``diagram_md``; the file, the result and the regeneration CLI must all be
    the same bytes, or the two paths drift again (they did: the api stored the
    bare Mermaid, so ZEM's registry diagram.md had no property table while its
    CLI-regenerated twin did).
    """
    from asterism_step0.ir2mermaid import render_dataset_doc
    from asterism_step0.mapping_ir import parse_mapping_ir

    result = materialize_schema(PROPOSAL_WITH_IR, tmp_path, "demo", write=True)
    assert result.diagram_md is not None
    assert result.diagram_md.startswith("# demo ontology — class diagram\n")
    assert "## Properties" in result.diagram_md
    assert result.diagram_md == (tmp_path / "diagram.md").read_text(encoding="utf-8")
    assert result.mapping_ir_yaml is not None
    assert result.diagram_md == render_dataset_doc(
        parse_mapping_ir(result.mapping_ir_yaml), dataset_name="demo"
    )


def test_diagram_md_exists_without_a_spec(tmp_path: Path) -> None:
    """A spec-less design still yields a document (title + fenced §1 sketch),
    just without the provenance table — nothing to derive it from."""
    specless = PROPOSAL_WITH_IR.split("### 9.")[0]
    result = materialize_schema(specless, tmp_path, "demo", write=False)
    assert result.diagram_md is not None
    assert result.diagram_md.startswith("# demo ontology — class diagram\n")
    assert "```mermaid\nclassDiagram" in result.diagram_md
    assert "## Properties" not in result.diagram_md


def test_unparseable_spec_keeps_the_sketch(tmp_path: Path) -> None:
    broken = PROPOSAL_WITH_IR.replace("version: 1", "version: [broken")
    result = materialize_schema(broken, tmp_path, "demo", write=False)
    assert result.mapping_ir_issues != []
    assert result.diagram_from_ir is False
    assert result.mermaid is not None and "classDiagram" in result.mermaid
    assert "+name" not in result.mermaid  # untouched §1 sketch


def test_specless_proposal_keeps_the_sketch(tmp_path: Path) -> None:
    specless = PROPOSAL_WITH_IR.split("### 9.")[0]
    result = materialize_schema(specless, tmp_path, "demo", write=False)
    assert result.mapping_ir_yaml is None
    assert result.diagram_from_ir is False
    assert result.mermaid is not None and "+name" not in result.mermaid


def test_compile_failure_still_yields_the_ir_diagram(tmp_path: Path) -> None:
    """An unknown Tier-0 function kills the RML compile but not the parse —
    the reviewer still gets the real diagram (the design's structure is
    exactly what they need to see to fix the design)."""
    bad_fn = IR_BLOCK.replace(
        "- predicate: ex:name\n        column: name",
        "- predicate: ex:name\n        column: name\n        function: no_such_fn",
    )
    assert bad_fn != IR_BLOCK
    proposal = PROPOSAL_WITH_IR.replace(IR_BLOCK, bad_fn)
    result = materialize_schema(proposal, tmp_path, "demo", write=False)
    assert result.rml_ttl is None
    assert result.mapping_ir_issues != []
    assert result.diagram_from_ir is True
    assert result.mermaid is not None and "+name" in result.mermaid
