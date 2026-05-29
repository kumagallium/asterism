"""Tests for csv2rdf_step0.materialize."""
from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from csv2rdf_step0.materialize import (
    extract_code_blocks,
    materialize_schema,
)

# A representative propose/refine output: 4 fenced blocks under headers
# matching the propose SYSTEM_PROMPT structure.
_PROPOSAL = dedent(
    """
    # Schema proposal

    ### 1. Class hierarchy (Mermaid classDiagram)

    ```mermaid
    classDiagram
        Paper --> Sample : has
    ```

    ### 2. IRI scheme

    Some prose about IRIs (no code block).

    ### 6. rdf-config model.yaml

    ```yaml
    - Paper <https://example.com/r/paper/1>:
        - a: sd:Paper
    ```

    ### 7. MIE YAML extras

    ```yaml
    schema_info:
      title: Example
      keywords: [a, b, c, d, e]
    ```

    ### 8. Ingester sketch

    ```python
    def ingest_papers(path):
        with open(path, encoding="utf-8-sig") as f:
            ...
    ```
    """
).lstrip("\n")


# ----------------------------------------------------------------------------
# extract_code_blocks
# ----------------------------------------------------------------------------


def test_extract_code_blocks_counts_all() -> None:
    blocks = extract_code_blocks(_PROPOSAL)
    assert len(blocks) == 4
    langs = [b.language for b in blocks]
    assert langs == ["mermaid", "yaml", "yaml", "python"]


def test_extract_code_blocks_tracks_header_context() -> None:
    blocks = extract_code_blocks(_PROPOSAL)
    by_lang = {b.language: b for b in blocks}
    assert "Class hierarchy" in by_lang["mermaid"].header
    assert "Ingester" in by_lang["python"].header
    # Two yaml blocks — distinguish by header
    yaml_blocks = [b for b in blocks if b.language == "yaml"]
    headers = [b.header for b in yaml_blocks]
    assert any("rdf-config" in h for h in headers)
    assert any("MIE" in h for h in headers)


def test_extract_code_blocks_handles_empty_doc() -> None:
    assert extract_code_blocks("") == []
    assert extract_code_blocks("# Just a header\n\nsome prose") == []


def test_extract_code_blocks_preserves_body_lines() -> None:
    blocks = extract_code_blocks(_PROPOSAL)
    mermaid = next(b for b in blocks if b.language == "mermaid")
    assert "classDiagram" in mermaid.body
    assert "Paper --> Sample : has" in mermaid.body


# ----------------------------------------------------------------------------
# materialize_schema — classification
# ----------------------------------------------------------------------------


def test_materialize_extracts_all_four(tmp_path: Path) -> None:
    result = materialize_schema(_PROPOSAL, tmp_path, "example")
    assert result.complete
    assert "classDiagram" in result.mermaid  # type: ignore[operator]
    assert "a: sd:Paper" in result.rdf_config_model  # type: ignore[operator]
    assert "schema_info" in result.mie_yaml  # type: ignore[operator]
    assert "utf-8-sig" in result.ingester_py  # type: ignore[operator]
    assert not result.warnings


def test_materialize_disambiguates_two_yaml_blocks(tmp_path: Path) -> None:
    """The rdf-config model and MIE are both yaml — must not be swapped."""
    result = materialize_schema(_PROPOSAL, tmp_path, "example")
    # model.yaml is the rdf-config one (starts with a list `- Paper`)
    assert result.rdf_config_model.strip().startswith("- Paper")  # type: ignore[union-attr]
    # MIE is the schema_info one
    assert result.mie_yaml.strip().startswith("schema_info")  # type: ignore[union-attr]
    assert result.rdf_config_model != result.mie_yaml


def test_materialize_writes_files(tmp_path: Path) -> None:
    materialize_schema(_PROPOSAL, tmp_path, "mydata")
    assert (tmp_path / "diagram.md").exists()
    assert (tmp_path / "mydata-model.yaml").exists()
    assert (tmp_path / "mydata-mie.yaml").exists()
    assert (tmp_path / "mydata.py").exists()
    # diagram.md should wrap the mermaid in a fence again
    diagram = (tmp_path / "diagram.md").read_text(encoding="utf-8")
    assert "```mermaid" in diagram
    assert "classDiagram" in diagram


def test_materialize_dry_run_writes_nothing(tmp_path: Path) -> None:
    result = materialize_schema(_PROPOSAL, tmp_path, "mydata", write=False)
    assert result.complete  # still extracted
    assert not result.written_paths
    assert not list(tmp_path.iterdir())  # nothing written


def test_materialize_warns_on_missing_block(tmp_path: Path) -> None:
    partial = dedent(
        """
        ### 1. Class hierarchy

        ```mermaid
        classDiagram
            A --> B : x
        ```
        """
    ).lstrip("\n")
    result = materialize_schema(partial, tmp_path, "partial")
    assert result.mermaid is not None
    assert result.rdf_config_model is None
    assert result.ingester_py is None
    assert not result.complete
    assert any("rdf-config" in w for w in result.warnings)
    assert any("ingester" in w.lower() for w in result.warnings)
    # The mermaid file should still be written
    assert (tmp_path / "diagram.md").exists()


def test_materialize_handles_single_yaml_via_header(tmp_path: Path) -> None:
    """When there's only one yaml block, header still routes it correctly."""
    only_mie = dedent(
        """
        ### 7. MIE YAML extras

        ```yaml
        schema_info:
          title: Solo
        ```
        """
    ).lstrip("\n")
    result = materialize_schema(only_mie, tmp_path, "solo", write=False)
    assert result.mie_yaml is not None
    assert "schema_info" in result.mie_yaml
    # No rdf-config header → model stays None
    assert result.rdf_config_model is None


def test_materialize_tolerates_varied_header_wording(tmp_path: Path) -> None:
    """Header keyword matching is fuzzy — 'Ingester skeleton' still matches."""
    doc = dedent(
        """
        ## Ingester skeleton (Python)

        ```python
        def go(): ...
        ```
        """
    ).lstrip("\n")
    result = materialize_schema(doc, tmp_path, "x", write=False)
    assert result.ingester_py is not None
    assert "def go()" in result.ingester_py


# ----------------------------------------------------------------------------
# Round-trip determinism
# ----------------------------------------------------------------------------


def test_materialize_is_deterministic(tmp_path: Path) -> None:
    a = materialize_schema(_PROPOSAL, tmp_path / "a", "x")
    b = materialize_schema(_PROPOSAL, tmp_path / "b", "x")
    assert a.mermaid == b.mermaid
    assert a.rdf_config_model == b.rdf_config_model
    assert a.mie_yaml == b.mie_yaml
    assert a.ingester_py == b.ingester_py
