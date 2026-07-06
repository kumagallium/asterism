"""Tests for asterism_step0.refine."""
from __future__ import annotations

from pathlib import Path

import pytest

from asterism_step0.propose import LLMClient
from asterism_step0.refine import (
    SYSTEM_PROMPT,
    RefinementResult,
    _main,
    _read_comments_file,
    refine_schema,
)

# ----------------------------------------------------------------------------
# Schema fixtures for the truncation guard
# ----------------------------------------------------------------------------

# A minimal but materialize-complete schema: all 4 core artifacts + the optional
# RML block, each under a header materialize recognizes.
_COMPLETE_SCHEMA = """\
## Class hierarchy

```mermaid
classDiagram
  class Sample
```

## rdf-config model.yaml

```yaml
Sample:
  - id: xsd:string
```

## MIE extras

```yaml
schema_info:
  name: test
```

## Ingester sketch

```python
def ingest() -> None:
    pass
```

## RML declarative mapping

```turtle
@prefix rml: <http://w3id.org/rml/> .
```
"""

# A truncated refine output: the document was cut off after the MIE block, so the
# ingester Python and RML turtle fences never appear.
_TRUNCATED_OUTPUT = """\
### 1. Comment resolution log

- Comment: rename Sample
- Action: renamed

### 2. Updated schema

## Class hierarchy

```mermaid
classDiagram
  class Specimen
```

## rdf-config model.yaml

```yaml
Specimen:
  - id: xsd:string
```

## MIE extras

```yaml
schema_info:
  name: test
```
"""

# ----------------------------------------------------------------------------
# Mock LLM (same shape as test_propose's _RecordingLLM)
# ----------------------------------------------------------------------------


class _RecordingLLM:
    def __init__(self, canned: str = "### 1. Comment resolution log\n...") -> None:
        self.canned = canned
        self.system_prompts: list[str] = []
        self.user_messages: list[str] = []

    def complete(self, system_prompt: str, user_message: str) -> str:
        self.system_prompts.append(system_prompt)
        self.user_messages.append(user_message)
        return self.canned


# ----------------------------------------------------------------------------
# refine_schema end-to-end (with mock LLM)
# ----------------------------------------------------------------------------


def test_refine_schema_passes_current_and_numbered_comments() -> None:
    mock = _RecordingLLM()
    result = refine_schema(
        "# Current\n... schema body ...",
        ["Rename Sample to Specimen", "Add QUDT units"],
        llm=mock,
    )
    assert isinstance(result, RefinementResult)
    assert len(mock.user_messages) == 1
    msg = mock.user_messages[0]
    # current schema embedded
    assert "# Current" in msg
    assert "schema body" in msg
    # comments numbered in order
    assert "1. Rename Sample to Specimen" in msg
    assert "2. Add QUDT units" in msg
    # section headers
    assert "# Current schema" in msg
    assert "# Review comments" in msg


def test_refine_schema_returns_canned_response() -> None:
    mock = _RecordingLLM(canned="REFINED OUTPUT")
    result = refine_schema("schema", ["c"], llm=mock)
    assert result.refined_md == "REFINED OUTPUT"


def test_refine_schema_records_metadata() -> None:
    mock = _RecordingLLM()
    result = refine_schema("schema", ["c"], llm=mock)
    assert result.metadata["llm_class"] == "_RecordingLLM"
    assert result.comments == ["c"]


def test_refine_schema_rejects_empty_comments() -> None:
    with pytest.raises(ValueError, match="at least one"):
        refine_schema("schema", [], llm=_RecordingLLM())


def test_refine_schema_strips_comment_whitespace() -> None:
    mock = _RecordingLLM()
    refine_schema("schema", ["  trim me  ", "\n\tand me\n"], llm=mock)
    msg = mock.user_messages[0]
    assert "1. trim me" in msg
    assert "2. and me" in msg


def test_refine_schema_language_rides_user_message_only() -> None:
    """language= appends the Output-language block to the USER message; the
    cacheable system prompt stays byte-stable (prompt-caching contract)."""
    mock = _RecordingLLM()
    refine_schema("schema", ["c"], llm=mock, language="ja")
    assert "# Output language" in mock.user_messages[0]
    assert "Japanese (日本語)" in mock.user_messages[0]
    assert mock.system_prompts[0] == SYSTEM_PROMPT


def test_refine_schema_no_language_keeps_legacy_message() -> None:
    mock = _RecordingLLM()
    refine_schema("schema", ["c"], llm=mock)
    assert "# Output language" not in mock.user_messages[0]


# ----------------------------------------------------------------------------
# Truncation guard (incomplete refine output)
# ----------------------------------------------------------------------------


def test_refine_complete_output_passes_guard() -> None:
    """A refine that keeps all artifacts is complete; effective == refined."""
    mock = _RecordingLLM(canned=_COMPLETE_SCHEMA)
    result = refine_schema(_COMPLETE_SCHEMA, ["c"], llm=mock)
    assert result.complete is True
    assert result.missing_artifacts == []
    assert result.warnings == []
    assert result.effective_schema_md == result.refined_md


def test_refine_truncated_output_is_flagged_and_keeps_previous() -> None:
    """A truncated refine (lost ingester + RML) keeps the previous complete schema."""
    mock = _RecordingLLM(canned=_TRUNCATED_OUTPUT)
    result = refine_schema(_COMPLETE_SCHEMA, ["rename Sample"], llm=mock)
    assert result.complete is False
    # Both dropped artifacts are reported by their human-readable names.
    assert "ingester Python" in result.missing_artifacts
    assert "declarative mapping (§9)" in result.missing_artifacts
    assert len(result.warnings) == 1 and "incomplete" in result.warnings[0]
    # The raw (truncated) output is preserved for inspection...
    assert result.refined_md == _TRUNCATED_OUTPUT
    # ...but the schema safe to feed downstream is the previous complete one.
    assert result.effective_schema_md == _COMPLETE_SCHEMA


def test_refine_no_blocks_is_not_a_false_positive() -> None:
    """Prose-only input + prose-only output must not be flagged as truncated."""
    mock = _RecordingLLM(canned="### 1. log\nno code here")
    result = refine_schema("# Current\njust prose", ["c"], llm=mock)
    assert result.complete is True
    assert result.missing_artifacts == []


def test_refine_guard_is_relative_to_input() -> None:
    """An artifact absent from the input too is not counted as 'lost'."""
    # Input without an ingester block; refined also omits it but adds the rest.
    input_no_ingester = _COMPLETE_SCHEMA.replace(
        "## Ingester sketch\n\n```python\ndef ingest() -> None:\n    pass\n```\n\n", ""
    )
    mock = _RecordingLLM(canned=input_no_ingester)
    result = refine_schema(input_no_ingester, ["c"], llm=mock)
    assert result.complete is True
    assert "ingester Python" not in result.missing_artifacts


# ----------------------------------------------------------------------------
# CLI guard behavior
# ----------------------------------------------------------------------------


def test_cli_incomplete_writes_previous_and_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """asterism-refine keeps the previous schema at --output and parks the raw."""
    import asterism_step0.refine as refine_mod

    class _FakeAnthropic:
        def __init__(self, *a: object, **k: object) -> None:
            pass

        def complete(self, system_prompt: str, user_message: str) -> str:
            return _TRUNCATED_OUTPUT

    monkeypatch.setattr(refine_mod, "AnthropicLLMClient", _FakeAnthropic)

    schema = tmp_path / "schema.md"
    schema.write_text(_COMPLETE_SCHEMA, encoding="utf-8")
    out = tmp_path / "refined.md"

    rc = _main([str(schema), "--comment", "rename Sample", "--output", str(out)])

    assert rc == 0
    # Output holds the previous complete schema (not the truncated one).
    assert out.read_text(encoding="utf-8") == _COMPLETE_SCHEMA
    # The truncated output is parked beside it for inspection.
    sidecar = tmp_path / "refined.md.incomplete.md"
    assert sidecar.exists()
    assert sidecar.read_text(encoding="utf-8") == _TRUNCATED_OUTPUT


# ----------------------------------------------------------------------------
# System prompt invariants (caching)
# ----------------------------------------------------------------------------


def test_system_prompt_byte_stable_across_calls() -> None:
    """Same caching invariant as propose: SYSTEM_PROMPT must not change between calls."""
    mock = _RecordingLLM()
    refine_schema("schema A", ["c1"], llm=mock)
    refine_schema("schema B", ["c2"], llm=mock)
    assert mock.system_prompts[0] == mock.system_prompts[1]
    # User messages SHOULD differ (variables live there)
    assert mock.user_messages[0] != mock.user_messages[1]


def test_system_prompt_keeps_8_traps_referenced() -> None:
    """Refinement must re-verify the same 8 traps as the initial proposal."""
    for trap in ("T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8"):
        assert trap in SYSTEM_PROMPT, f"trap {trap} missing from refine system prompt"


def test_system_prompt_mandates_full_output_not_diff() -> None:
    """Refined output must be a complete schema, not a diff — required for re-feeding."""
    assert "full" in SYSTEM_PROMPT.lower()
    assert "diff" in SYSTEM_PROMPT.lower()


def test_system_prompt_demands_two_top_level_sections() -> None:
    """Output structure must include resolution log + updated schema."""
    assert "Comment resolution log" in SYSTEM_PROMPT
    assert "Updated schema" in SYSTEM_PROMPT


def test_system_prompt_addresses_renaming_propagation() -> None:
    """Renames must apply across all 4 artifacts in one pass — a known gotcha."""
    lower = SYSTEM_PROMPT.lower()
    assert "rename" in lower or "renaming" in lower


# ----------------------------------------------------------------------------
# Comments file parsing
# ----------------------------------------------------------------------------


def test_read_comments_file_skips_blank_and_comment_lines(tmp_path: Path) -> None:
    p = tmp_path / "comments.txt"
    p.write_text(
        "\n".join(
            [
                "# This is a comment header",
                "",
                "First comment",
                "  ",
                "# Another header",
                "Second comment",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    assert _read_comments_file(p) == ["First comment", "Second comment"]


def test_read_comments_file_strips_whitespace(tmp_path: Path) -> None:
    p = tmp_path / "c.txt"
    p.write_text("  with surrounding space  \n\ttabbed\n", encoding="utf-8")
    assert _read_comments_file(p) == ["with surrounding space", "tabbed"]


# ----------------------------------------------------------------------------
# Protocol satisfaction
# ----------------------------------------------------------------------------


def test_recording_llm_satisfies_llm_protocol() -> None:
    """Sanity: the mock used here implements LLMClient."""
    assert isinstance(_RecordingLLM(), LLMClient)
