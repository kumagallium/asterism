"""Tests for asterism_step0.refine."""
from __future__ import annotations

from pathlib import Path

import pytest

from asterism_step0.propose import LLMClient
from asterism_step0.refine import (
    SYSTEM_PROMPT,
    RefinementResult,
    _read_comments_file,
    refine_schema,
)

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
