"""Output-language directive for the propose / refine user messages.

Why a user-message block and not the system prompt: both SYSTEM_PROMPTs are
frozen + byte-stable for prompt caching, so any per-call variation must ride
the user message. Why prose-only: ``materialize`` locates artifacts by
matching ENGLISH heading keywords, and every downstream consumer (rdf-config,
Morph-KGC, the ingest gate) reads identifiers verbatim — so headings,
identifiers, and code stay English; only the human-readable prose switches.
"""

from __future__ import annotations

__all__ = ["language_instruction"]

# Human-readable names for the language codes the UI sends (i18next codes).
# An unknown code is passed through verbatim — the LLM resolves BCP-47-ish
# codes fine, and failing open beats rejecting a future UI language.
_LANGUAGE_NAMES = {
    "ja": "Japanese (日本語)",
    "en": "English",
}


def language_instruction(language: str | None) -> str:
    """The ``# Output language`` user-message block, or ``""`` when unset.

    Empty / ``None`` keeps the legacy behaviour (no directive → English prose).
    """
    code = (language or "").strip()
    if not code:
        return ""
    name = _LANGUAGE_NAMES.get(code.lower(), code)
    return (
        "# Output language\n\n"
        f"Write ALL human-readable prose in {name}: section body text, design\n"
        "rationale (Decision / Why / Alternatives / Trade-offs), justifications,\n"
        "explanations in table cells, and comments inside code blocks.\n"
        "Keep in English EXACTLY as the required output structure specifies:\n"
        "Markdown section headings (downstream tooling extracts artifacts by\n"
        "matching their English keywords), class / property / variable names,\n"
        "YAML keys, IRIs and prefixes, Mermaid node names, and all code syntax.\n"
        "Multilingual keyword requirements (e.g. MIE keywords in English AND\n"
        "domain-relevant languages) are unchanged."
    )
