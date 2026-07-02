"""Tests for asterism_step0.language — the Output-language user-message block.

The directive must (1) stay OUT of the byte-stable system prompts (it rides
the user message), (2) switch only the human-readable prose — headings /
identifiers stay English because materialize extracts artifacts by matching
English heading keywords.
"""

from __future__ import annotations

from asterism_step0.language import language_instruction


def test_unset_language_yields_no_directive() -> None:
    assert language_instruction(None) == ""
    assert language_instruction("") == ""
    assert language_instruction("   ") == ""


def test_known_code_gets_human_name() -> None:
    block = language_instruction("ja")
    assert block.startswith("# Output language")
    assert "Japanese (日本語)" in block
    # The materialize contract: headings / identifiers stay English.
    assert "section headings" in block
    assert "English" in block


def test_code_lookup_is_case_insensitive() -> None:
    assert "Japanese (日本語)" in language_instruction("JA")


def test_unknown_code_passes_through_verbatim() -> None:
    # Fail open: a future UI language must not be rejected here.
    assert "pt-BR" in language_instruction("pt-BR")
