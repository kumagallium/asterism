"""Guard the propose-prompt ⇄ Tier 0 REGISTRY contract (T9 fail-closed by design).

The declarative substrate is only safe because an AI-authored RML mapping may
reference a **closed, once-vetted set of functions** — and trap T9
(``asterism_step0.rml_check``) rejects any mapping that names a function outside
``asterism.functions.REGISTRY``.

But T9 can only ever *pass* if the AI is told the truth about which functions
exist. The propose ``SYSTEM_PROMPT`` (``asterism_step0.propose``) advertises the
§9 Tier-0 MENU — one ``- `name` (…)`` bullet per function, bare names, the
Mapping IR contract (ADR mapping-ir-compiler.md) — and the AI chooses exactly
from it. If the menu lists a function that is **not** in REGISTRY, the AI will
emit it, compilation/T9 will flag it, and refine can never fix it (the prompt
keeps leading the AI back to the same un-registered name). Conversely, a
registered function the menu never mentions is dead — the AI can't reach it.

So the product invariant is: **the set of functions the prompt advertises ==
the set of functions REGISTRY registers.** This test locks that invariant in the
one environment where it can actually be checked against the live registry (the
ingest package, where ``asterism`` imports). It parses the prompt out of the
sibling ``step0`` source on disk and skips gracefully when that source is not
present (e.g. a standalone install of the ingest package).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from asterism.functions import REGISTRY

# The propose prompt lives in the sibling ``step0`` package. In the monorepo /
# CI checkout it is reachable from the ingest tree; in a standalone install of
# just the ingest package it is absent and the guard is skipped (not failed).
_PROPOSE_PY = (
    Path(__file__).resolve().parents[2]
    / "step0"
    / "src"
    / "asterism_step0"
    / "propose.py"
)

# Backticked names on a §9 menu bullet line ("- `name` (…" possibly with
# "`a` / `b`" pairs). Only the part before the first "(" is parsed so prose
# examples inside the description never register as advertised functions.
_MENU_NAME = re.compile(r"`([a-z0-9_]+)`")

# The functions a real user's generated RML used (from the bug report). Every one
# must be REGISTERED or the AI's faithful output fails T9 with no path to fix.
_USER_RML_FUNCTIONS = {
    "datetime_iso",
    "doi_norm",
    "float_array_max",
    "float_array_min",
    "float_array_count",
    "qudt_quantity",
    "qudt_unit",
    "trim_collapse",
    "url_canonical",
    "json_array",
    "json_pluck",
    "split",
    "lookup",
}


def _advertised_function_names() -> set[str]:
    """The function names on the propose SYSTEM_PROMPT's §9 Tier-0 menu."""
    if not _PROPOSE_PY.exists():  # standalone ingest install — nothing to compare
        pytest.skip(f"step0 propose prompt not present at {_PROPOSE_PY}")
    src = _PROPOSE_PY.read_text(encoding="utf-8")
    marker = 'SYSTEM_PROMPT = """'
    start = src.index(marker)
    end = src.index('"""', start + len(marker))
    body = src[start:end]
    _, _, menu = body.partition("Vetted **Tier 0** functions")
    menu, _, _ = menu.partition("## Self-check")
    assert menu, "the §9 Tier-0 menu section is missing from the prompt"
    names: set[str] = set()
    for line in menu.splitlines():
        if not line.startswith("- `"):
            continue
        names.update(_MENU_NAME.findall(line.split("(", 1)[0]))
    return names


def _registered_names() -> set[str]:
    return {spec.name for spec in REGISTRY}


def test_prompt_advertised_set_equals_registry() -> None:
    """prompt's advertised fn:* set == REGISTRY set (both directions, no slack).

    This is THE invariant: every advertised function is registered (so the AI's
    faithful output passes T9), and every registered function is advertised (so
    the AI can actually reach it). A mismatch in either direction is the bug.
    """
    advertised = _advertised_function_names()
    registered = _registered_names()
    vaporware = advertised - registered  # advertised but un-registered ⇒ T9 fails
    unreachable = registered - advertised  # registered but never advertised ⇒ dead
    assert not vaporware, (
        "propose prompt advertises functions absent from REGISTRY "
        f"(AI will emit them and T9 will ALWAYS fail): {sorted(vaporware)}"
    )
    assert not unreachable, (
        "REGISTRY has functions the propose prompt never advertises "
        f"(AI can never reach them): {sorted(unreachable)}"
    )
    assert advertised == registered


def test_user_rml_functions_are_registered() -> None:
    """The functions a real user's RML used must all be in the closed set.

    Regression for the bug where the prompt led the AI to use these but the
    runtime REGISTRY did not contain them, so T9 (closed-set, Tier 0 only)
    always failed and refine could not recover.
    """
    registered = _registered_names()
    missing = sorted(_USER_RML_FUNCTIONS - registered)
    assert not missing, f"user-RML functions not registered (T9 would fail): {missing}"


def test_user_rml_functions_are_advertised() -> None:
    """...and the prompt actually tells the AI those functions exist."""
    advertised = _advertised_function_names()
    missing = sorted(_USER_RML_FUNCTIONS - advertised)
    assert not missing, f"user-RML functions not advertised in prompt: {missing}"
