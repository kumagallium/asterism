"""Materialize a schema proposal Markdown into individual artifact files.

This is the deterministic "Step 6" of the workflow: :mod:`asterism_step0.propose`
and :mod:`asterism_step0.refine` emit a single Markdown document with fenced
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
* (optional) The ``yaml`` **mapping spec** block under the "Declarative
  mapping spec" section → ``{out}/{name}-mapping.yaml`` (the reviewed/refined
  IR artifact) **plus** its deterministic compilation →
  ``{out}/{name}-mapping.rml.ttl``. The LLM writes the small Mapping IR; the
  compiler (:mod:`asterism_step0.rml_compile`) owns all RML/FnO syntax. See
  ``docs/architecture/mapping-ir-compiler.md``.
* (legacy) The ``turtle`` block under the "RML" / "declarative mapping"
  section → ``{out}/{name}-mapping.rml.ttl`` — older proposals carried raw RML
  directly. Extraction is kept so existing designs re-materialize unchanged;
  when a mapping-spec block is present it WINS (a stale turtle block must not
  mask IR errors). Both blocks are *additive*: absence is not a warning and
  does not affect :attr:`MaterializeResult.complete` (the 4 core artifacts).

No LLM call for extraction — pure text splitting. Compiling the mapping spec
is equally deterministic but needs the vetted Tier-0 catalog
(``asterism.functions``); when that package is absent the spec is still
extracted and a warning explains why no RML was produced.

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
import contextlib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from asterism_step0.units import enrich_units

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
_RML_HEADERS = ("rml", "declarative mapping", "宣言マッピング")
# The Mapping IR block shares the §9 headers (a yaml block under the mapping
# section); "mapping spec" is the canonical §9 heading of the IR contract.
_MAPPING_IR_HEADERS = ("mapping spec", *_RML_HEADERS)


@dataclass
class MaterializeResult:
    """Result of materializing a proposal Markdown."""

    mermaid: str | None = None
    rdf_config_model: str | None = None
    mie_yaml: str | None = None
    ingester_py: str | None = None
    rml_ttl: str | None = None  # compiled from the mapping spec, or legacy raw RML
    mapping_ir_yaml: str | None = None  # the extracted Mapping IR block (additive)
    diagram_from_ir: bool = False
    """True when :attr:`mermaid` was compiled deterministically from the
    mapping spec (IR) rather than taken from the LLM's §1 sketch."""
    diagram_property_table: str | None = None
    """Markdown property ↔ column table appended below the Mermaid block in
    diagram.md (only when the diagram was compiled from the IR)."""
    mapping_ir_issues: list[str] = field(default_factory=list)
    """Parse/compile problems of the mapping spec, in the IR's own vocabulary
    (the design loop feeds them back to the LLM). Empty when there is no
    mapping-spec block or it compiled cleanly."""
    written_paths: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """True iff all 4 *core* artifacts were extracted.

        The optional RML mapping (:attr:`rml_ttl`) is deliberately excluded:
        it is additive and absent from older proposals, so it must not gate
        completeness.
        """
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


def _compile_mapping_spec(result: MaterializeResult) -> tuple[str | None, object | None]:
    """Compile the extracted mapping spec to RML; record problems on ``result``.

    Returns ``(rml_ttl, parsed_ir)``. The parsed IR is surfaced even when the
    RML compile fails (unknown function, etc.) — the deterministic diagram
    only needs the parse to succeed. Parse/compile problems land in
    :attr:`MaterializeResult.mapping_ir_issues` (the design loop's feedback
    source). A missing compiler dependency (the vetted Tier-0 catalog /
    PyYAML) is an environment warning, not a design issue — the spec is still
    extracted and persisted.
    """
    from asterism_step0.mapping_ir import MappingIRParseError, parse_mapping_ir
    from asterism_step0.rml_compile import RmlCompileError, compile_mapping_ir

    assert result.mapping_ir_yaml is not None
    ir = None
    try:
        ir = parse_mapping_ir(result.mapping_ir_yaml)
        return compile_mapping_ir(ir), ir
    except (MappingIRParseError, RmlCompileError) as exc:
        result.mapping_ir_issues = list(exc.issues)
        result.warnings.append(
            "The mapping spec could not be compiled to RML "
            f"({len(exc.issues)} issue(s)); see mapping_ir_issues."
        )
    except ImportError as exc:
        result.warnings.append(f"Mapping-spec compiler unavailable: {exc}")
    return None, ir


def _diagram_from_ir(result: MaterializeResult, ir: object) -> None:
    """Replace the LLM's §1 diagram sketch with one compiled from the IR.

    Same philosophy as the RML compile: when a parseable spec exists, the
    artifact reviewers stare at is derived deterministically from the design's
    single source of truth, not hand-drawn by the model (observed live: empty
    class boxes with all predicates orphaned outside the diagram). The §1
    sketch stays as-is for spec-less / unparseable proposals. Best-effort by
    design — a diagram bug must never block materialize."""
    try:
        from asterism_step0.ir2mermaid import build_graph_from_ir, property_table_md
        from asterism_step0.ttl2mermaid import render_mermaid_body

        graph = build_graph_from_ir(ir)  # type: ignore[arg-type]
        if not graph.classes:
            return  # nothing to draw (no subject classes) — keep the sketch
        result.mermaid = render_mermaid_body(graph).rstrip("\n")
        result.diagram_from_ir = True
        result.diagram_property_table = property_table_md(ir) or None  # type: ignore[arg-type]
    except Exception:
        return


def apply_source_dialects(mapping_ir_yaml: str, source_dir: Path | str) -> str:
    """Overlay auto-detected source dialects onto a mapping-spec YAML.

    Deterministic design-pipeline step (ADR ``source-dialect.md``): every
    tabular source the spec declares that exists under ``source_dir`` is
    sniffed with ``detect_dialect`` and pinned into the spec's ``dialects:``
    section — explicit spec values win, so a human-gated override survives.
    Returns the input text unchanged when there is nothing to add (default
    dialects emit nothing anywhere), so a clean spec is never re-serialized.
    Callers with the source files at hand (the api design loop, the
    materialize CLI via ``--source-dir``) run this before compiling.
    """
    import yaml

    from asterism_step0.dialect import (
        TABULAR_SUFFIXES,
        apply_detected_dialects,
        detect_dialect,
        is_default,
    )

    try:
        doc = yaml.safe_load(mapping_ir_yaml)
    except yaml.YAMLError:
        return mapping_ir_yaml  # a broken spec flows on; the compiler reports it
    if not isinstance(doc, dict) or not isinstance(doc.get("maps"), list):
        return mapping_ir_yaml
    base = Path(source_dir)
    detected = {}
    for m in doc["maps"]:
        if not isinstance(m, dict):
            continue
        source = m.get("source")
        if not isinstance(source, str) or source in detected:
            continue
        if Path(source).suffix.lower() not in TABULAR_SUFFIXES:
            continue
        path = base / source
        if not path.is_file():
            continue
        try:
            dialect = detect_dialect(path)
        except OSError:
            continue
        if not is_default(dialect):
            detected[source] = dialect
    if not detected:
        return mapping_ir_yaml
    overlaid = apply_detected_dialects(mapping_ir_yaml, detected)
    assert isinstance(overlaid, str)
    return overlaid


def materialize_schema(
    proposal_md: str,
    output_dir: Path | str,
    dataset_name: str,
    *,
    write: bool = True,
    source_dir: Path | str | None = None,
) -> MaterializeResult:
    """Split ``proposal_md`` into artifact files under ``output_dir``.

    Args:
        proposal_md: A propose/refine Markdown document.
        output_dir: Destination directory (created on demand).
        dataset_name: Used in output filenames (``{name}-model.yaml`` etc.).
        write: If False, only extract (no files written) — useful for tests
            and dry-runs.
        source_dir: Directory holding the declared source files. When given,
            detected non-default dialects are pinned into the mapping spec
            (:func:`apply_source_dialects`) before it is compiled/persisted.

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
    # The Mapping IR block (§9, yaml). Picked BEFORE the MIE block so the MIE
    # lang-only fallback can never claim a mapping spec (e.g. on a truncated
    # proposal missing its MIE section), and only ever by header
    # (allow_lang_only=False) so a lone model/MIE block is never mistaken for
    # a mapping spec.
    # Candidates are restricted to yaml-tagged blocks up front: a LEGACY §9 block
    # (turtle under the same headers) must never be picked as a mapping spec by
    # the header-only fallback.
    ir_candidates = [
        b for b in blocks if b.language in ("yaml", "yml") and b.body != result.rdf_config_model
    ]
    result.mapping_ir_yaml = _pick_block(
        ir_candidates,
        header_keywords=_MAPPING_IR_HEADERS,
        language_prefs=("yaml", "yml"),
        allow_lang_only=False,
    )

    # For MIE, exclude the blocks already claimed as the model / mapping spec.
    mie_candidates = [
        b
        for b in blocks
        if b.language in ("yaml", "yml")
        and b.body != result.rdf_config_model
        and b.body != result.mapping_ir_yaml
    ]
    result.mie_yaml = _pick_block(
        mie_candidates, header_keywords=_MIE_HEADERS, language_prefs=("yaml", "yml")
    )

    result.ingester_py = _pick_block(
        blocks, header_keywords=_INGESTER_HEADERS, language_prefs=("python", "py")
    )

    # §9 precedence: with a mapping spec present, the spec IS the design — the
    # legacy turtle extraction is not even attempted. Any stray ```turtle fence
    # (an MIE sample-RDF snippet, a leftover legacy block after a redesign) is
    # inert by construction and deliberately NOT worth a warning: the UI treats
    # `warnings` as "this design cannot be ingested" (materializeUsable) and
    # feeds them verbatim to the one-click AI fix, so an informational note
    # here would wrongly block the save and send the model chasing a non-issue.
    # Warnings stay reserved for genuinely blocking states (missing core
    # artifact / spec that does not compile / compiler unavailable).
    if result.mapping_ir_yaml is not None:
        # Fill each property's display `unit` from a bracketed column name
        # (e.g. "Resistivity(Ohm m)" → unit: "Ohm m") — deterministic, no model
        # call. Units are display metadata only (never compiled into RML), so this
        # cannot change the RML; it only enriches the persisted spec the rules
        # view reads. PyYAML absence is a no-op (the compile step warns).
        with contextlib.suppress(ImportError):
            result.mapping_ir_yaml = enrich_units(result.mapping_ir_yaml)
        if source_dir is not None:
            # Pin detected dialects before compiling so the persisted spec and
            # the RML annotations agree. PyYAML absence falls through to the
            # compile step, which reports the environment warning.
            with contextlib.suppress(ImportError):
                result.mapping_ir_yaml = apply_source_dialects(result.mapping_ir_yaml, source_dir)
        result.rml_ttl, mapping_ir = _compile_mapping_spec(result)
        if mapping_ir is not None:
            # The reviewer-facing diagram compiles from the same parsed spec —
            # boxes WITH their properties/units, edges from IRI links — instead
            # of trusting the LLM's §1 sketch (observed live: empty boxes).
            _diagram_from_ir(result, mapping_ir)
    else:
        # Legacy raw-RML artifact. Turtle is unambiguous in a proposal (only
        # the RML block uses it), so a lone turtle block routes by language.
        result.rml_ttl = _pick_block(
            blocks, header_keywords=_RML_HEADERS, language_prefs=("turtle", "ttl")
        )

    # ----- warnings -----
    # Note: rml_ttl is intentionally NOT warned-on when absent — it is additive.
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
            doc = (
                f"# {dataset_name} ontology — class diagram\n\n```mermaid\n{result.mermaid}\n```\n"
            )
            if result.diagram_property_table:
                # Provenance companion (predicate ↔ column ↔ unit ↔ meaning).
                # Consumers that extract only the fenced block are unaffected.
                doc += "\n" + result.diagram_property_table
            p.write_text(doc, encoding="utf-8")
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
        if result.mapping_ir_yaml is not None:
            p = out / f"{dataset_name}-mapping.yaml"
            p.write_text(result.mapping_ir_yaml + "\n", encoding="utf-8")
            result.written_paths["mapping_ir"] = str(p)
        if result.rml_ttl is not None:
            p = out / f"{dataset_name}-mapping.rml.ttl"
            p.write_text(result.rml_ttl + "\n", encoding="utf-8")
            result.written_paths["rml_ttl"] = str(p)

    return result


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="asterism-materialize",
        description=(
            "Split a propose/refine schema Markdown into individual artifact "
            "files (diagram.md / {name}-model.yaml / {name}-mie.yaml / {name}.py)."
        ),
    )
    p.add_argument("proposal", type=Path, help="Proposal Markdown (from asterism-propose/refine)")
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
    p.add_argument(
        "--source-dir",
        type=Path,
        default=None,
        help=(
            "Directory holding the declared source files. When given, tabular "
            "sources are sniffed and non-default dialects are pinned into the "
            "mapping spec before compiling (ADR source-dialect.md)."
        ),
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
        source_dir=args.source_dir,
    )

    if args.dry_run:
        sys.stdout.write("Extracted (dry-run, no files written):\n")
        for name, present in (
            ("mermaid", result.mermaid is not None),
            ("rdf_config_model", result.rdf_config_model is not None),
            ("mie_yaml", result.mie_yaml is not None),
            ("ingester_py", result.ingester_py is not None),
            ("mapping_ir (optional)", result.mapping_ir_yaml is not None),
            ("rml_ttl (optional)", result.rml_ttl is not None),
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
