"""Materialize a schema proposal Markdown into individual artifact files.

This is the deterministic "Step 6" of the workflow: :mod:`csv2rdf_step0.propose`
and :mod:`csv2rdf_step0.refine` emit a single Markdown document with fenced
code blocks for each artifact. ``materialize_schema`` parses that document and
splits the blocks into files on disk:

* The ``mermaid`` block (under the "Class hierarchy" section) →
  ``{out}/diagram.md``
* The ``yaml`` block under the "rdf-config model.yaml" section →
  ``{out}/{name}-model.yaml`` (the *input* to rdf-config, which then
  generates the ShEx ``shape_expressions``)
* The ``yaml`` block under the "MIE" section →
  ``{out}/{name}-mie.yaml`` (``schema_info`` + ``sample_rdf_entries`` +
  ``sparql_query_examples`` + ``anti_patterns`` + ``architectural_notes`` —
  the shape_expressions are filled in afterward by running rdf-config)
* The ``python`` block under the "Ingester" section →
  ``{out}/{name}.py``

No LLM call — pure text extraction, so it's fully testable and CI-safe.

The section matching is keyword-based (case-insensitive) rather than exact,
so it tolerates the LLM varying the header wording slightly. When a target
block is missing, materialize records a warning rather than failing — the
caller decides whether a partial materialization is acceptable.

The final step — running rdf-config on ``{name}-model.yaml`` to generate
``shape_expressions`` and merging into the MIE — is intentionally left to a
separate invocation (it needs the Ruby toolchain). See
``docs/architecture/linkml-vs-rdf-config.md`` §3.1.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ----------------------------------------------------------------------------
# Fenced code block extraction
# ----------------------------------------------------------------------------

# Capture: the most recent header line preceding each fenced block, plus the
# block's language tag and body. We walk the doc once, tracking the current
# header, and collect (header, lang, body) for every fenced block.

_HEADER = re.compile(r"^#{1,6}\s+(.*)$")
_FENCE_OPEN = re.compile(r"^```([a-zA-Z0-9_+-]*)\s*$")
_FENCE_CLOSE = re.compile(r"^```\s*$")


@dataclass
class CodeBlock:
    """One fenced code block with the header context it appeared under."""

    header: str  # nearest preceding header text (may be "" if none)
    language: str  # the ``` fence language tag (may be "")
    body: str


def extract_code_blocks(markdown: str) -> list[CodeBlock]:
    """Walk ``markdown`` and return every fenced code block with its header context."""
    blocks: list[CodeBlock] = []
    current_header = ""
    lines = markdown.splitlines()
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        header_match = _HEADER.match(line)
        if header_match:
            current_header = header_match.group(1).strip()
            i += 1
            continue
        fence_match = _FENCE_OPEN.match(line)
        if fence_match:
            language = fence_match.group(1)
            body_lines: list[str] = []
            i += 1
            while i < n and not _FENCE_CLOSE.match(lines[i]):
                body_lines.append(lines[i])
                i += 1
            # i now points at the closing fence (or EOF)
            blocks.append(
                CodeBlock(
                    header=current_header,
                    language=language,
                    body="\n".join(body_lines),
                )
            )
            i += 1  # skip the closing fence
            continue
        i += 1
    return blocks


# ----------------------------------------------------------------------------
# Classification of blocks → artifacts
# ----------------------------------------------------------------------------


def _header_matches(header: str, keywords: tuple[str, ...]) -> bool:
    h = header.lower()
    return any(kw in h for kw in keywords)


# Header keyword sets per artifact (case-insensitive substring match).
_MERMAID_HEADERS = ("class hierarchy", "mermaid", "diagram")
_MODEL_HEADERS = ("rdf-config", "model.yaml")
_MIE_HEADERS = ("mie",)
_INGESTER_HEADERS = ("ingester", "ingest")


@dataclass
class MaterializeResult:
    """Result of materializing a proposal Markdown."""

    mermaid: str | None = None
    rdf_config_model: str | None = None
    mie_yaml: str | None = None
    ingester_py: str | None = None
    written_paths: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """True iff all 4 artifacts were extracted."""
        return all(
            x is not None
            for x in (self.mermaid, self.rdf_config_model, self.mie_yaml, self.ingester_py)
        )


def _pick_block(
    blocks: list[CodeBlock],
    *,
    header_keywords: tuple[str, ...],
    language_prefs: tuple[str, ...],
    allow_lang_only: bool = True,
) -> str | None:
    """Pick the best block for an artifact.

    Preference order:
      1. A block whose header matches AND whose language is in language_prefs
      2. A block whose header matches (any language)
      3. (only if ``allow_lang_only``) A block whose language is in
         language_prefs (any header) — only when exactly one such block exists

    ``allow_lang_only`` is disabled for the rdf-config model so a lone MIE
    yaml block (no rdf-config header) is not mis-claimed as the model.
    """
    header_and_lang = [
        b
        for b in blocks
        if _header_matches(b.header, header_keywords) and b.language in language_prefs
    ]
    if header_and_lang:
        return header_and_lang[0].body

    header_only = [b for b in blocks if _header_matches(b.header, header_keywords)]
    if header_only:
        return header_only[0].body

    if allow_lang_only:
        lang_only = [b for b in blocks if b.language in language_prefs]
        if len(lang_only) == 1:
            return lang_only[0].body

    return None


def materialize_schema(
    proposal_md: str,
    output_dir: Path | str,
    dataset_name: str,
    *,
    write: bool = True,
) -> MaterializeResult:
    """Split ``proposal_md`` into artifact files under ``output_dir``.

    Args:
        proposal_md: A propose/refine Markdown document.
        output_dir: Destination directory (created on demand).
        dataset_name: Used in output filenames (``{name}-model.yaml`` etc.).
        write: If False, only extract (no files written) — useful for tests
            and dry-runs.

    Returns:
        :class:`MaterializeResult` with the extracted strings, written paths,
        and any warnings about missing blocks.
    """
    blocks = extract_code_blocks(proposal_md)
    result = MaterializeResult()

    # ----- classify -----
    result.mermaid = _pick_block(
        blocks, header_keywords=_MERMAID_HEADERS, language_prefs=("mermaid",)
    )

    # For YAML, there are TWO blocks (rdf-config model + MIE). Disambiguate
    # by header. rdf-config model first (its header is more specific).
    result.rdf_config_model = _pick_block(
        blocks,
        header_keywords=_MODEL_HEADERS,
        language_prefs=("yaml", "yml"),
        allow_lang_only=False,  # don't grab a lone MIE block as the model
    )
    # For MIE, exclude the block we already chose as the model.
    mie_candidates = [
        b
        for b in blocks
        if b.language in ("yaml", "yml") and b.body != result.rdf_config_model
    ]
    result.mie_yaml = _pick_block(
        mie_candidates, header_keywords=_MIE_HEADERS, language_prefs=("yaml", "yml")
    )

    result.ingester_py = _pick_block(
        blocks, header_keywords=_INGESTER_HEADERS, language_prefs=("python", "py")
    )

    # ----- warnings -----
    if result.mermaid is None:
        result.warnings.append("No Mermaid block found (Class hierarchy section).")
    if result.rdf_config_model is None:
        result.warnings.append("No rdf-config model.yaml block found.")
    if result.mie_yaml is None:
        result.warnings.append("No MIE YAML block found.")
    if result.ingester_py is None:
        result.warnings.append("No ingester Python block found.")

    # ----- write -----
    if write:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        if result.mermaid is not None:
            p = out / "diagram.md"
            p.write_text(
                f"# {dataset_name} ontology — class diagram\n\n"
                f"```mermaid\n{result.mermaid}\n```\n",
                encoding="utf-8",
            )
            result.written_paths["mermaid"] = str(p)
        if result.rdf_config_model is not None:
            p = out / f"{dataset_name}-model.yaml"
            p.write_text(result.rdf_config_model + "\n", encoding="utf-8")
            result.written_paths["rdf_config_model"] = str(p)
        if result.mie_yaml is not None:
            p = out / f"{dataset_name}-mie.yaml"
            p.write_text(result.mie_yaml + "\n", encoding="utf-8")
            result.written_paths["mie_yaml"] = str(p)
        if result.ingester_py is not None:
            p = out / f"{dataset_name}.py"
            p.write_text(result.ingester_py + "\n", encoding="utf-8")
            result.written_paths["ingester_py"] = str(p)

    return result


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="csv2rdf-materialize",
        description=(
            "Split a propose/refine schema Markdown into individual artifact "
            "files (diagram.md / {name}-model.yaml / {name}-mie.yaml / {name}.py)."
        ),
    )
    p.add_argument("proposal", type=Path, help="Proposal Markdown (from csv2rdf-propose/refine)")
    p.add_argument("--name", required=True, help="Dataset name (used in output filenames)")
    p.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory to write the artifact files into.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Extract and report without writing files.",
    )
    return p


def _main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    proposal_md = args.proposal.read_text(encoding="utf-8")
    result = materialize_schema(
        proposal_md,
        args.output_dir,
        args.name,
        write=not args.dry_run,
    )

    if args.dry_run:
        sys.stdout.write("Extracted (dry-run, no files written):\n")
        for name, present in (
            ("mermaid", result.mermaid is not None),
            ("rdf_config_model", result.rdf_config_model is not None),
            ("mie_yaml", result.mie_yaml is not None),
            ("ingester_py", result.ingester_py is not None),
        ):
            sys.stdout.write(f"  {'✓' if present else '✗'} {name}\n")
    else:
        sys.stdout.write("Wrote:\n")
        for kind, path in result.written_paths.items():
            sys.stdout.write(f"  {kind}: {path}\n")

    for w in result.warnings:
        sys.stderr.write(f"warning: {w}\n")

    if result.warnings:
        sys.stderr.write(
            "\nReminder: run rdf-config on {name}-model.yaml to generate the "
            "MIE shape_expressions, then merge into {name}-mie.yaml.\n"
        )

    # Exit 0 even with warnings (partial materialization is allowed); exit 1
    # only if NOTHING was extracted (likely a malformed proposal).
    return 0 if result.written_paths or args.dry_run else 1


if __name__ == "__main__":
    raise SystemExit(_main())
