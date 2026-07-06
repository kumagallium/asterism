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
