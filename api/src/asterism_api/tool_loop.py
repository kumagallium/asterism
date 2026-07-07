"""Self-correcting AI draft of ONE query tool (the design loop's little sibling).

``POST /api/datasets/{id}/tools/propose`` used to be a single LLM shot whose
draft was only *flagged* invalid — the human then had to bounce it back by
hand, and (before the lint gate) a broken draft could even be saved and take
the Ask surface down at runtime (observed live: an undeclared ``prov:`` prefix,
a guessed ``sd:``-namespaced predicate the RML never maps). This module closes
the same loop the RML design path closed in ``design_loop.py`` (#239):

    propose -> deterministic vet -> targeted feedback + closed-menu oracle ->
    refine -> re-vet ... (max_rounds, keep the best draft)

The vet is deterministic and dataset-agnostic:

* ``parse_query_tools``   — declaration contract (read-only, placeholder sanity)
* ``lint_query_tool``     — rendered-template parse with the store's own parser
                            (pyoxigraph), undeclared prefixes, filter-only vars
* vocabulary check        — every term must be in the closed set the dataset's
                            RML actually maps (``extract_rml_vocabulary``); a
                            term outside it matches nothing (the 0-row tool)

The oracle is the vocabulary check's constructive twin: the exact PREFIX block
and the full closed menu of mapped class/predicate IRIs, injected into BOTH the
first draft and every refine round — the same anti-hallucination lever as the
design loop's Tier-0 oracle (its strongest, per the live probes).

Trust model unchanged: the loop only *drafts*; a human still reviews and saves
via ``POST .../tools`` (which re-runs the same strict gate). No LLM output is
executed here — validation is pure parsing/linting.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from asterism.query_tools import QueryToolError, lint_query_tool, parse_query_tools
from asterism.rml_validate import extract_rml_vocabulary
from asterism_step0.tool_propose import propose_query_tool, refine_query_tool

#: Max mapped terms enumerated in the oracle before truncation (a mapping with
#: hundreds of predicates would blow the prompt for marginal gain).
_ORACLE_TERM_CAP = 200


def build_vocab_oracle(vocabulary: dict[str, Any]) -> str:
    """Render the closed-menu oracle block from an extracted RML vocabulary.

    Empty string when the vocabulary is empty (no RML — nothing to enumerate),
    so callers can inject it unconditionally.
    """
    terms = sorted(str(t) for t in (vocabulary.get("terms") or ()))
    prefixes = {str(k): str(v) for k, v in dict(vocabulary.get("prefixes") or {}).items()}
    if not terms:
        return ""
    lines = [
        "Vocabulary oracle (extracted DETERMINISTICALLY from this dataset's RML "
        "mapping — this is the complete, closed set of terms that exist in the "
        "ingested data):",
        "PREFIX declarations (copy the ones you use verbatim, and declare EVERY "
        "prefix your query mentions):",
    ]
    lines += [f"  PREFIX {label}: <{iri}>" for label, iri in sorted(prefixes.items()) if label]
    lines.append(
        "Mapped classes and predicates (use ONLY these — any other term matches "
        "nothing and the tool returns 0 rows):"
    )
    lines += [f"  <{t}>" for t in terms[:_ORACLE_TERM_CAP]]
    if len(terms) > _ORACLE_TERM_CAP:
        lines.append(f"  ... ({len(terms) - _ORACLE_TERM_CAP} more omitted)")
    return "\n".join(lines)


def vet_tool_draft(draft: dict, vocabulary: dict[str, Any] | None) -> tuple[list[str], list[str]]:
    """Deterministically vet one draft. Returns ``(errors, warnings)``.

    Errors = will not save / will not run (contract violation, broken SPARQL,
    undeclared prefix). Warnings = saves but is almost certainly wrong
    (filter-only variable, term outside the RML-mapped vocabulary).
    """
    try:
        parsed = parse_query_tools({"tools": [draft]})
    except QueryToolError as exc:
        return [str(exc)], []
    lint = lint_query_tool(parsed[0], vocabulary=vocabulary)
    return list(lint.errors), list(lint.warnings)


@dataclass
class ToolLoopResult:
    """Outcome of :func:`propose_tool_with_correction` (best draft, always set)."""

    draft: dict
    valid: bool
    error: str | None
    warnings: list[str] = field(default_factory=list)
    #: One record per LLM round: {"round": n, "errors": [...], "warnings": [...]}
    rounds: list[dict] = field(default_factory=list)


def propose_tool_with_correction(
    llm: Any,
    *,
    intent: str,
    model_yaml: str = "",
    mie_yaml: str = "",
    rml_ttl: str = "",
    language: str | None = None,
    max_rounds: int = 3,
) -> ToolLoopResult:
    """Draft a query tool and self-correct against deterministic validation.

    Runs up to ``max_rounds`` LLM calls (1 propose + up to ``max_rounds - 1``
    refines); each round's defects — plus the closed-menu oracle — feed the
    next. Stops early when a round is defect-free. Always returns the BEST
    draft seen (fewest errors, then fewest warnings): an LLM failure mid-loop
    keeps the best-so-far instead of raising (env-bail, like the design loop),
    but a failure on the FIRST round propagates — there is nothing to fall
    back on.

    ``max_rounds=1`` is the no-self-correction escape hatch (vet still runs).
    """
    vocabulary = extract_rml_vocabulary(rml_ttl) if (rml_ttl or "").strip() else None
    oracle = build_vocab_oracle(vocabulary) if vocabulary else ""

    best: tuple[tuple[int, int], dict, list[str], list[str]] | None = None
    rounds: list[dict] = []
    draft: dict | None = None
    issues: list[str] = []
    for round_no in range(1, max(1, int(max_rounds)) + 1):
        try:
            if draft is None:
                draft = propose_query_tool(
                    llm,
                    intent=intent,
                    model_yaml=model_yaml,
                    mie_yaml=mie_yaml,
                    rml_ttl=rml_ttl,
                    oracle=oracle,
                    language=language,
                )
            else:
                draft = refine_query_tool(
                    llm, draft=draft, issues=issues, oracle=oracle, language=language
                )
        except Exception:
            if best is None:
                raise  # first round: no draft to keep
            break  # later rounds: keep the best draft rather than losing it
        errors, warnings = vet_tool_draft(draft, vocabulary)
        rounds.append({"round": round_no, "errors": errors, "warnings": warnings})
        score = (len(errors), len(warnings))
        if best is None or score < best[0]:
            best = (score, draft, errors, warnings)
        if not errors and not warnings:
            break
        issues = errors + warnings

    assert best is not None  # round 1 either raised or set it
    _score, final_draft, errors, warnings = best
    return ToolLoopResult(
        draft=final_draft,
        valid=not errors,
        error="; ".join(errors) or None,
        warnings=warnings,
        rounds=rounds,
    )
