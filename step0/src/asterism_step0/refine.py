"""Schema refinement (Step 5) for asterism Phase 3.

Given (a) the current schema proposal and (b) human review comments, this
module asks the LLM to **synchronously update all 4 artifacts** (TBox /
Mermaid / MIE / ingester) and return:

  1. **Comment resolution log** — per-comment record of how the LLM
     interpreted it, what it changed, and any side effects (e.g. renaming
     ``Sample`` → ``Specimen`` propagates to the ingester's emitter name).
  2. **Updated schema** — the full Markdown document in the same shape
     :mod:`asterism_step0.propose` emits (so the output can be re-fed to
     refine again, validate, or materialize).

This is Step 5 of the workflow in
``docs/architecture/ai-assisted-step0-workflow.md``. The LLM call uses the
same :class:`LLMClient` Protocol as :mod:`asterism_step0.propose` — pass the
default :class:`asterism_step0.propose.AnthropicLLMClient` for real calls, a
mock for tests.

The system prompt is byte-stable and large (cacheable), so repeated
refinement rounds within a single session hit the prompt cache.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from asterism_step0.language import language_instruction
from asterism_step0.llm import AnthropicLLMClient, LLMClient, as_completion
from asterism_step0.materialize import materialize_schema

# ----------------------------------------------------------------------------
# Incomplete-output guard
# ----------------------------------------------------------------------------
#
# Large schemas can push the LLM past its output budget, so a refine round may
# stop mid-document (e.g. after §2.5) and drop whole artifact blocks — the
# model.yaml / MIE / ingester / RML fences never appear. Feeding that truncated
# Markdown forward as the new "current schema" then silently loses artifacts at
# materialize time. The guard below detects the loss by comparing which artifacts
# materialize can extract from the input vs. from the refined output; when the
# refine dropped an artifact the input had, we keep the previous complete schema
# (see RefinementResult.effective_schema_md) and surface a warning instead.

# Human-readable name per MaterializeResult artifact attribute.
_ARTIFACT_LABELS: dict[str, str] = {
    "mermaid": "Mermaid diagram",
    "rdf_config_model": "rdf-config model.yaml",
    "mie_yaml": "MIE YAML",
    "ingester_py": "ingester Python",
    "rml_ttl": "declarative mapping (§9)",
}


def _artifacts_present(schema_md: str) -> set[str]:
    """Return the set of artifact attrs materialize can extract from ``schema_md``.

    Pure extraction (``write=False``): no files touched. Used to compare the
    input schema against the refined output for the truncation guard.

    The §9 artifact counts as PRESENT when the block exists in either form — a
    mapping spec (``mapping_ir_yaml``, even one with design errors awaiting the
    next fix round) or legacy raw RML (``rml_ttl``). Keying presence on the
    compiled ``rml_ttl`` alone would misread "spec present but not compilable
    yet" as "the refine dropped the block" and wrongly freeze the schema.
    """
    res = materialize_schema(schema_md, ".", "guard", write=False)
    present = {
        attr
        for attr in _ARTIFACT_LABELS
        if attr != "rml_ttl" and getattr(res, attr) is not None
    }
    if res.rml_ttl is not None or res.mapping_ir_yaml is not None:
        present.add("rml_ttl")
    return present

# ----------------------------------------------------------------------------
# System prompt — frozen, cacheable
# ----------------------------------------------------------------------------
#
# Source: docs/architecture/ai-assisted-step0-prompts.md §5.1, lightly
# trimmed. Same constraints as propose.SYSTEM_PROMPT — no interpolation,
# byte-stable, large enough to cache.

SYSTEM_PROMPT = """\
You are the same RDF / OWL / SPARQL ontology engineer who produced the
initial schema proposal in the conversation. The user is sending review
comments. Your job is to process them and return an updated schema with the
**4 artifacts kept in sync** (TBox / Mermaid / MIE / ingester).

## What you receive (user message)

```
# Current schema
<the previous proposal Markdown — same structure as Step 3 output>

# Review comments
1. <comment 1>
2. <comment 2>
...
```

## What you return

A Markdown document with **exactly two top-level sections in this order**:

### 1. Comment resolution log

For each numbered review comment, write:

- **Comment**: quote the original
- **Interpretation**: how you understood it (state your reading explicitly,
  especially when ambiguous)
- **Affected artifacts**: which of {TBox, Mermaid, MIE, ingester} you
  changed
- **Action**: the diff in plain English (e.g. "renamed class Sample →
  Specimen across all 4 artifacts, updated `_emit_sample` helper to
  `_emit_specimen`, kept dcterms:identifier composite key")
- **Side effects**: any non-obvious knock-on changes (e.g. "Phase 1
  anti_patterns mentioning Sample now reference Specimen for consistency")
- **Open questions** (only if applicable): any judgment calls you made that
  the human should confirm before merging

If a comment cannot be addressed (out of scope, contradicts another comment,
needs external input), still log it with **Status: deferred** and explain why.

### 2. Updated schema

Return the **full updated proposal** — same Markdown structure as the Step
3 output (Class hierarchy → IRI scheme → Property design → JSON column
strategy → Design rationale → rdf-config model.yaml → MIE extras →
Ingester sketch → Declarative mapping spec). Do NOT emit a diff or "only
the changed sections" — the output must be reusable as the input to another
refine call or to materialize. When the schema carries a §9 mapping block,
keep its form as-is (a yaml mapping spec stays a yaml mapping spec under the
same heading; never convert it to RML/Turtle or drop it). Preserve each
property's optional `label:` (human-readable meaning, in the reviewer's
language) and `unit:` (human-readable notation like `µV/K`); when a comment
clarifies a column's meaning or unit, set them. These are DISPLAY METADATA for
the review screen only — they never change emitted values and are not a
substitute for unit-conversion functions (`qudt_unit` / `value_of` / `unit_of`).

## Constraints (same 8 traps as Step 3)

After applying the comments, re-verify all 8 traps from the initial
proposal:

- T1 IRI composite keys still use the inspection's uniqueness statistics
- T2 ingester still uses utf-8-sig
- T3 zero blank nodes (no rdflib.BNode() calls)
- T4 MIE keywords / categories still ≥ 5 / ≥ 1
- T5 Mermaid labels still free of colons
- T6 sample_rdf_entries still reference real CSV row IDs (do not invent
  new SIDs to match a renamed class — re-use the real ones from the
  inspection in the original proposal)
- T7 Design rationale: every comment-driven change adds a new Why / Alt /
  Trade-offs entry; existing entries that the comment invalidates are
  marked superseded, not deleted
- T8 ingester / shape_expressions remain mutually consistent

## Renaming rules

If a comment renames an entity, property, or IRI segment:

1. Apply the rename uniformly across all 4 artifacts in one pass
2. Update Phase 1-style anti_patterns / architectural_notes that reference
   the old name
3. If a renamed property is reused from an external ontology (e.g.
   `schema:author`), do NOT rename the external IRI — rename only the
   local alias / variable
4. Preserve all composite IRI key components — renaming Sample → Specimen
   keeps `{SID}-{sample_id}` (the column names in the CSV don't change)

## Conservative-merge bias

When a comment is genuinely ambiguous, take the more conservative
interpretation (smaller blast radius) and surface the ambiguity in
**Open questions** rather than silently picking the more aggressive
reading.

## Tone

No preamble, no "I'll now process your comments" framing. Start the
response with `### 1. Comment resolution log` and the first comment.
"""


# ----------------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------------


@dataclass
class RefinementResult:
    """Result of one :func:`refine_schema` call."""

    current_schema_md: str
    """The schema Markdown that was passed in as input."""

    comments: list[str]
    """The review comments (numbered in the order received)."""

    refined_md: str
    """The LLM's full (possibly truncated) output: resolution log + updated schema."""

    complete: bool = True
    """False when the refine dropped an artifact the input had (likely truncated)."""

    missing_artifacts: list[str] = field(default_factory=list)
    """Human-readable names of artifacts present in the input but lost in ``refined_md``."""

    warnings: list[str] = field(default_factory=list)
    """Guard warnings (e.g. incomplete output). Empty when the refine is clean."""

    metadata: dict[str, Any] = field(default_factory=dict)
    """Optional: model name, token usage, latency, etc."""

    @property
    def effective_schema_md(self) -> str:
        """The schema safe to feed downstream (materialize / another refine).

        When the refine is :attr:`complete`, this is the refined output. When it
        is incomplete (truncated), it falls back to the input schema so the
        caller keeps the previous complete version instead of propagating a
        schema with missing artifact blocks.
        """
        return self.refined_md if self.complete else self.current_schema_md


def refine_schema(
    current_schema_md: str,
    comments: list[str],
    *,
    llm: LLMClient | None = None,
    language: str | None = None,
) -> RefinementResult:
    """Apply ``comments`` to ``current_schema_md`` via the LLM.

    Args:
        current_schema_md: A schema proposal Markdown — typically the
            output of :func:`asterism_step0.propose.propose_schema` (or a
            prior refine call's ``refined_md``, for multi-round iteration).
        comments: A list of human review comments. Order is preserved in
            the user message (numbered 1, 2, 3...) and in the LLM's
            resolution log.
        llm: An :class:`LLMClient`. Defaults to :class:`AnthropicLLMClient`
            (requires ``ANTHROPIC_API_KEY``). Tests pass a mock.
        language: Output language for the resolution log and the schema's
            human-readable prose (e.g. ``"ja"``). Headings / identifiers /
            code stay English (see :mod:`asterism_step0.language`).

    Returns:
        :class:`RefinementResult` with the input schema, the comments, and
        the LLM's full Markdown response.
    """
    if llm is None:
        llm = AnthropicLLMClient()
    if not comments:
        raise ValueError("refine_schema needs at least one review comment")

    numbered = "\n".join(f"{i + 1}. {c.strip()}" for i, c in enumerate(comments))
    user_message = (
        f"# Current schema\n\n{current_schema_md.strip()}\n\n"
        f"# Review comments\n\n{numbered}\n"
    )
    lang_block = language_instruction(language)
    if lang_block:
        user_message += f"\n{lang_block}\n"
    refined = as_completion(llm.complete(SYSTEM_PROMPT, user_message)).text

    # Truncation guard: if the refined output lost an artifact the input had,
    # the round was likely cut off mid-document. Keep the previous complete
    # schema (effective_schema_md) and warn rather than propagating a partial.
    lost = _artifacts_present(current_schema_md) - _artifacts_present(refined)
    missing_labels = sorted(_ARTIFACT_LABELS[a] for a in lost)
    warnings: list[str] = []
    if missing_labels:
        warnings.append(
            "Refine output is incomplete — these artifacts were present in the "
            f"input but missing from the refined schema: {', '.join(missing_labels)}. "
            "The output was likely truncated (large schema or too many comments). "
            "Keeping the previous complete schema; re-run refine with fewer "
            "comments or a smaller schema."
        )

    return RefinementResult(
        current_schema_md=current_schema_md,
        comments=list(comments),
        refined_md=refined,
        complete=not missing_labels,
        missing_artifacts=missing_labels,
        warnings=warnings,
        metadata={"llm_class": type(llm).__name__},
    )


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------


def _build_arg_parser():  # type: ignore[no-untyped-def]
    import argparse

    p = argparse.ArgumentParser(
        prog="asterism-refine",
        description=(
            "Apply review comments to an existing schema proposal via the LLM. "
            "Requires ANTHROPIC_API_KEY."
        ),
    )
    p.add_argument(
        "schema",
        type=Path,
        help="Path to the current proposal Markdown (from asterism-propose).",
    )
    p.add_argument(
        "--comment",
        action="append",
        default=[],
        help=(
            "A review comment. Repeatable. Use --comments-file for longer or "
            "many comments at once."
        ),
    )
    p.add_argument(
        "--comments-file",
        type=Path,
        default=None,
        help=(
            "Read comments from this file (one per line; blank lines and "
            "lines starting with # are ignored). Combined with --comment if both given."
        ),
    )
    p.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write the refined Markdown here. Defaults to stdout.",
    )
    p.add_argument(
        "--model",
        default="claude-opus-4-7",
        help="Anthropic model ID (default: claude-opus-4-7).",
    )
    p.add_argument(
        "--effort",
        default="xhigh",
        choices=["low", "medium", "high", "xhigh", "max"],
        help="output_config.effort (default: xhigh).",
    )
    p.add_argument(
        "--language",
        default=None,
        help=(
            "Output language for the resolution log / schema prose (e.g. 'ja'). "
            "Headings / identifiers / code stay English. Default: English."
        ),
    )
    return p


def _read_comments_file(path: Path) -> list[str]:
    out: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


def _main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    comments: list[str] = list(args.comment)
    if args.comments_file:
        comments.extend(_read_comments_file(args.comments_file))
    if not comments:
        raise SystemExit("error: at least one --comment or --comments-file required")

    schema_md = args.schema.read_text(encoding="utf-8")
    llm = AnthropicLLMClient(model=args.model, effort=args.effort)
    result = refine_schema(schema_md, comments, llm=llm, language=args.language)

    for w in result.warnings:
        sys.stderr.write(f"warning: {w}\n")

    if args.output is None:
        # stdout: print the raw output so the (possibly truncated) resolution
        # log is visible; the guard warning already went to stderr.
        print(result.refined_md)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # When incomplete, write the previous complete schema so the pipeline
        # keeps a usable document, and park the truncated output beside it.
        args.output.write_text(result.effective_schema_md, encoding="utf-8")
        if not result.complete:
            sidecar = args.output.with_suffix(args.output.suffix + ".incomplete.md")
            sidecar.write_text(result.refined_md, encoding="utf-8")
            sys.stderr.write(
                f"warning: kept the previous schema at {args.output}; "
                f"the truncated refine output is at {sidecar} for inspection.\n"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
