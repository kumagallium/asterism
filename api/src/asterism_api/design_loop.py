"""Automatic propose→validate→refine self-correction loop (TODO ④ propose quality).

Why this exists
---------------
``propose`` emits a whole schema (incl. the §9 declarative RML mapping) in ONE LLM
call. Weak models repeatedly make design mistakes the deterministic validators catch
but the model does not fix on its own: hallucinated columns, wrong FnO parameter IRIs
(``fn:p_field1`` vs ``fn:p_field``), invalid Turtle, non-Tier-0 functions, invented
source filenames. The user used to click "AI に修正を依頼" by hand (the UI's
``composeFixComment`` renders the failures into a refine comment and re-runs). This
module automates that loop server-side.

Design (ADR ``propose-self-correction-loop.md``)
------------------------------------------------
* Round 0 = ``propose_schema`` (once). Rounds 1..N = ``refine_schema`` REUSED as-is —
  no new multi-turn LLM entrypoint (``complete()`` is single-turn by contract), and the
  cacheable SYSTEM_PROMPT stays byte-stable (per-round feedback rides the USER message).
* Each round: ``materialize_schema(write=False)`` to extract the §9 design; if it is
  absent emit "mapping spec missing" and stop collecting (nothing else is checkable).
  NEW proposals carry a §9 **mapping spec** (Mapping IR, ADR mapping-ir-compiler.md):
  validation is front-loaded at the IR level — parse → real files/columns/function-menu
  checks with difflib "Did you mean X?" → deterministic compile — so the feedback is
  always at a granularity a weak model can act on (never a Turtle parse error), and the
  unchanged RML gates run on the compiled output as backstop. LEGACY proposals (raw §9
  RML) keep the original pipeline: ``substitute_run_id`` then ``assert_rml_safe`` FIRST
  (the ONLY layer that flags invalid Turtle — ``validate_rml_design`` silently returns
  [] on unparseable Turtle), then ``validate_rml_design`` against the REAL uploaded
  source dir.
* Feedback = ``composeFixComment``'s server twin PLUS a deterministic **Tier-0 oracle**
  appendix (exact filenames, BOM-safe real columns, every REGISTRY function with its
  exact parameter local-names) — the closed menu that stops a weak model from
  re-hallucinating. It is passed to refine as a SINGLE joined comment (the proven manual
  shape), in the USER message only (cache-safe).
* Stop conditions (priority): converged (zero issues); cancelled (``should_cancel`` /
  ``LLMCancelledError`` → raise, never swallowed into env-bail — the job runner turns
  it into the cancelled state); env-bail (any other LLM exception +
  registry/rdflib import failure → keep best, do NOT iterate); refine truncated
  (``complete`` False → keep the prior complete ``effective_schema_md``, stop); no-progress
  (normalized issue key-set unchanged or seen before → stop); ``max_rounds`` cap
  (default 3; 0 disables the loop = plain propose). Always carry ``effective_schema_md``
  (never ``refined_md``); snapshot the SMALLEST-issue schema as ``best`` and RETURN it
  with ITS remaining issues (not the last round's).

Trust boundary (inviolable): the loop pushes the LLM back INTO the closed Tier-0 set; it
NEVER widens it. Its output still passes the hard 422 ingest gate. Convergence means
"passed the static gates", strictly weaker than "ingests cleanly" — the 422 gate is the
real gate. Known blind spots (documented in the ADR + surfaced honestly in the result):
JSON/XML sources get no column-level feedback (tabular-only validator); un-iri_safe IRIs
from existing columns are caught by no static validator; a cornered weak model can erase
mappings to reach zero issues (surfaced as ``coverage_dropped``, not blocked).
"""
from __future__ import annotations

import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from asterism import substrate
from asterism.rml_validate import read_csv_header
from asterism_step0.llm import LLMCancelledError, LLMTruncatedError
from asterism_step0.mapping_ir import (
    MappingIRParseError,
    catalog_from_registry,
    parse_mapping_ir,
    validate_mapping_ir,
)
from asterism_step0.mapping_ir_schema import mapping_ir_json_schema
from asterism_step0.materialize import materialize_schema
from asterism_step0.propose import propose_schema
from asterism_step0.refine import refine_schema
from asterism_step0.rml_compile import RmlCompileError, compile_mapping_ir
from asterism_step0.spec_repair import (
    SPEC_REPAIR_SYSTEM_PROMPT,
    build_spec_repair_user,
    parse_spec_json,
    replace_mapping_spec_block,
)

_TABULAR_SUFFIXES = frozenset({".csv", ".tsv"})

# The refine comment intro (server twin of the UI's workbench:fix.commentIntro).
_FIX_INTRO = (
    "Fix ONLY the following design issues; keep everything else unchanged. Do not "
    "introduce new columns, functions, or source files — correct the §9 mapping "
    "spec (and any other named artifact) to match the real data and the vetted "
    "Tier-0 functions listed at the end."
)


# ---------------------------------------------------------------------------
# Issue model + classification (pure, LLM-free)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Issue:
    """One machine-detected design problem.

    ``category``/``subject`` form a canonical key used for BOTH de-duplication and
    round-to-round oscillation detection — keying on the raw message would be brittle
    (a difflib "Did you mean X?" suffix or a run-id substitution changes one character
    and defeats set-equality). ``message`` is the full human string fed to the LLM.
    """

    category: str
    subject: str
    message: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.category, self.subject.strip().lower())


_RE_SOURCE_FILE = re.compile(r"source file '([^']+)'")
_RE_COLUMN = re.compile(r"column '([^']+)'")
_RE_FN_EXTRA = re.compile(r"^(\S+) does not accept parameter '([^']+)'")
_RE_FN_MISSING = re.compile(r"^(\S+) is missing required parameter '([^']+)'")
_RE_QUOTED = re.compile(r"'([^']+)'")
# Mapping-IR message shapes (asterism_step0.mapping_ir / rml_compile). Keys are
# predicate/term-based, NOT property-index-based — a model reshuffling rows must
# not defeat the no-progress (oscillation) detection.
_RE_IR_FUNCTION = re.compile(r"function '([^']+)' is not in the vetted")
_RE_IR_ARG_EXTRA = re.compile(r"(\w+) does not take a constant arg '([^']+)'")
_RE_IR_ARG_MISSING = re.compile(r"(\w+) requires the constant arg '([^']+)'")
_RE_IR_UNKNOWN_FIELD = re.compile(r"unknown field '([^']+)'")
_RE_IR_FN_PLUS_TEMPLATE = re.compile(r"\(([^)]+)\): 'function' cannot be combined")
_RE_IR_FN_NEEDS_COLUMN = re.compile(r"\(([^)]+)\)\.function requires 'column'")
_RE_IR_CARDINALITY = re.compile(r"'([^']+)' carries a cardinality marker")
_RE_IR_TYPE_CAST = re.compile(r"'([^']+)' is a type, not a Tier-0 function")

# Message stems that mean the validator ENVIRONMENT is broken (missing rdflib /
# unimportable Tier-0 registry), NOT that the LLM made a mistake. The loop bails on
# these — refining cannot fix an env failure.
_ENV_STEMS = ("rdflib is required", "is not importable", "Tier 0 registry")


def _is_env_message(msg: str) -> bool:
    return any(stem in msg for stem in _ENV_STEMS)


def classify(message: str) -> Issue:
    """Map a validator message string to a canonically-keyed :class:`Issue`.

    Handles every shape ``validate_rml_design`` and ``assert_rml_safe`` emit; an
    unrecognized shape falls back to a whole-message key so it is NEVER silently
    un-keyed (which would defeat dedup + oscillation detection).
    """
    m = message.strip()
    if (mm := _RE_SOURCE_FILE.search(m)):
        return Issue("source", mm.group(1), m)
    if (mm := _RE_COLUMN.search(m)):
        return Issue("column", mm.group(1), m)
    if (mm := _RE_FN_EXTRA.match(m)):
        return Issue("function", f"{mm.group(1)}/+{mm.group(2)}", m)
    if (mm := _RE_FN_MISSING.match(m)):
        return Issue("function", f"{mm.group(1)}/-{mm.group(2)}", m)
    # Mapping-IR shapes (checked before the generic fallbacks).
    if (mm := _RE_IR_FUNCTION.search(m)):
        return Issue("function", mm.group(1), m)
    if (mm := _RE_IR_ARG_EXTRA.search(m)):
        return Issue("function", f"{mm.group(1)}/+{mm.group(2)}", m)
    if (mm := _RE_IR_ARG_MISSING.search(m)):
        return Issue("function", f"{mm.group(1)}/-{mm.group(2)}", m)
    if (mm := _RE_IR_UNKNOWN_FIELD.search(m)):
        return Issue("structural", mm.group(1), m)
    if (mm := _RE_IR_FN_PLUS_TEMPLATE.search(m)):
        return Issue("structural", f"fn+template/{mm.group(1)}", m)
    if (mm := _RE_IR_FN_NEEDS_COLUMN.search(m)):
        return Issue("function", f"{mm.group(1)}/-column", m)
    if (mm := _RE_IR_CARDINALITY.search(m)):
        return Issue("structural", f"cardinality/{mm.group(1)}", m)
    if (mm := _RE_IR_TYPE_CAST.search(m)):
        return Issue("function", f"typecast/{mm.group(1)}", m)
    # assert_rml_safe shapes
    if "outside the closed Tier 0 set" in m:
        return Issue("function-set", _fn_set_subject(m), m)
    if "SQL" in m or "query/table source" in m:
        return Issue("safety", "sql-source", m)
    if "not parseable Turtle" in m or "not valid Turtle" in m:
        return Issue("turtle", "turtle", m)
    if "rml:source" in m:
        q = _RE_QUOTED.search(m)
        return Issue("source", q.group(1) if q else "source", m)
    return Issue("other", m.lower()[:120], m)


def _fn_set_subject(msg: str) -> str:
    """The offending IRIs in a 'functions outside the closed Tier 0 set: …' message."""
    _, _, tail = msg.partition("set:")
    return tail.strip() or "non-tier0"


# ---------------------------------------------------------------------------
# The Tier-0 oracle (deterministic closed menu injected into the refine prompt)
# ---------------------------------------------------------------------------


def build_oracle(source_dir: Path | str, csv_paths: list[Path | str]) -> str:
    """A deterministic 'closed menu' appendix for the refine USER message.

    Enumerates (1) the exact legal source filenames, (2) each tabular file's real
    columns via the SAME BOM-safe header reader the validator uses (so the menu can't
    teach a column name the validator would then reject), and (3) every vetted Tier-0
    function with its column-input count and constant-arg names (the Mapping IR
    surface — FnO parameter IRIs are the compiler's business now). This turns each
    refine round from "you were wrong, try again" (which weak models re-break) into
    "pick only from this menu" — the single strongest lever for weak-model
    convergence. Pure + LLM-free.
    """
    base = Path(source_dir)
    names = sorted({Path(p).name for p in csv_paths})
    lines: list[str] = [
        "── Reference (closed menu — use ONLY these names; do NOT invent or rename) ──",
        "Source files (use the filename EXACTLY as written):",
    ]
    for name in names:
        p = base / name
        cols = read_csv_header(p) if p.suffix.lower() in _TABULAR_SUFFIXES else []
        if cols:
            lines.append(f"  • {name} — columns: {', '.join(cols)}")
        else:
            lines.append(f"  • {name}")
    lines.append(
        "Vetted Tier-0 functions for the §9 mapping spec (bare names in "
        "'function:'/'transform:'; constants by name in 'args:') — use no other "
        "function and no other arg name:"
    )
    for fn in catalog_from_registry():
        n_cols = len(fn.column_params)
        parts = [f"{n_cols} column input" + ("s" if n_cols != 1 else "")]
        if fn.constant_params:
            parts.append("args: " + ", ".join(sorted(fn.constant_params)))
        if fn.multivalued:
            parts.append("multi-valued (one triple per element)")
        lines.append(f"  • {fn.name} — {'; '.join(parts)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Feedback rendering (pure)
# ---------------------------------------------------------------------------


def render_feedback(issues: list[Issue], oracle: str) -> list[str]:
    """Turn machine issues into the ``comments`` list ``refine_schema`` consumes.

    Mirrors the UI's ``composeFixComment``: a bulleted list under a fix intro, PLUS the
    Tier-0 oracle appendix. Returned as a SINGLE joined comment string (matching the
    battle-tested manual shape — a weak model follows one cohesive block better than
    many separately-numbered directives). ``issues`` must be non-empty (the caller only
    calls this when there are issues, so refine never sees an empty comment list).
    """
    bullets = "\n".join(f"- {iss.message}" for iss in issues)
    body = f"{_FIX_INTRO}\n{bullets}"
    if oracle:
        body = f"{body}\n\n{oracle}"
    return [body]


def _dedup(issues: list[Issue]) -> list[Issue]:
    """Collapse issues sharing a canonical key (the same T9 mole can be reported by up
    to three layers). Order-stable: first occurrence wins."""
    seen: set[tuple[str, str]] = set()
    out: list[Issue] = []
    for iss in issues:
        if iss.key in seen:
            continue
        seen.add(iss.key)
        out.append(iss)
    return out


# ---------------------------------------------------------------------------
# Issue collection (deterministic; raises _LoopEnvError on validator env failure)
# ---------------------------------------------------------------------------


class _LoopEnvError(Exception):
    """A non-loopable environment failure (missing rdflib / unimportable registry).

    The loop keeps the last-good schema and stops rather than iterating — refining
    cannot fix a broken validator environment.
    """


def collect_issues(
    ir_yaml: str | None, rml_ttl: str | None, source_dir: Path
) -> list[Issue]:
    """Deterministic, LLM-free machine feedback for ONE candidate design.

    NEW proposals carry a §9 mapping spec (``ir_yaml``, ADR mapping-ir-compiler):
    validation is FRONT-LOADED at the IR level — parse (structural issues in the
    IR's vocabulary), environment checks (real files/columns with did-you-mean,
    the closed function menu), then deterministic compilation, and finally the
    unchanged RML gates on the compiled output as defense in depth. The feedback
    a weak model receives is therefore always at a granularity it can act on
    ("column 'titel' is not in papers.csv. Did you mean: title?"), never a
    Turtle parse error.

    LEGACY proposals (raw ``rml_ttl``, no spec) keep the original pipeline:
    ``assert_rml_safe`` BEFORE ``validate_rml_design`` because it is the only
    layer that flags invalid Turtle (the design validator silently returns []
    on unparseable Turtle — the "convergence hole").

    Raises :class:`_LoopEnvError` when the validators themselves are unavailable.
    """
    if ir_yaml and ir_yaml.strip():
        return _collect_ir_issues(ir_yaml, source_dir)
    if not rml_ttl or not rml_ttl.strip():
        return [
            Issue(
                "structural",
                "mapping-spec",
                "The §9 mapping spec is missing or empty. Emit a complete ```yaml "
                "fenced block under a 'Declarative mapping spec' heading (version/"
                "prefixes/maps).",
            )
        ]
    return _collect_rml_issues(rml_ttl, source_dir)


def _collect_ir_issues(ir_yaml: str, source_dir: Path) -> list[Issue]:
    """The front-loaded IR pipeline: parse → environment validation → compile →
    RML gates (backstop)."""
    try:
        ir = parse_mapping_ir(ir_yaml)
    except MappingIRParseError as exc:
        return _dedup([classify(m) for m in exc.issues])
    except ImportError as exc:  # PyYAML missing — an environment failure
        raise _LoopEnvError(str(exc)) from exc

    try:
        catalog = catalog_from_registry()
    except ImportError as exc:  # Tier-0 registry not importable
        raise _LoopEnvError(str(exc)) from exc
    try:
        files = sorted(p.name for p in Path(source_dir).iterdir() if p.is_file())
    except OSError:
        files = []
    headers = {
        f: (read_csv_header(Path(source_dir) / f) or None)
        for f in files
        if Path(f).suffix.lower() in _TABULAR_SUFFIXES
    }
    messages = validate_mapping_ir(ir, files=files, headers=headers, catalog=catalog)
    if messages:
        return _dedup([classify(m) for m in messages])

    try:
        compiled = compile_mapping_ir(ir, catalog)
    except RmlCompileError as exc:
        # Validation passed but compilation refused — surface verbatim (usually a
        # validator blind spot the LLM can still fix by restructuring the spec).
        return _dedup([classify(m) for m in exc.issues])

    # Defense in depth: the compiled RML still passes the unchanged gates. Any
    # issue here is a compiler bug or a validator blind spot — surfaced honestly,
    # never silently dropped.
    return _collect_rml_issues(compiled, source_dir)


def _collect_rml_issues(rml_ttl: str, source_dir: Path) -> list[Issue]:
    """The original RML pipeline (legacy proposals + backstop for compiled specs)."""
    prepared = substrate.substitute_run_id(rml_ttl)
    issues: list[Issue] = []
    # Safety FIRST — catches non-Tier-0 fn / SQL source / path escape AND invalid Turtle.
    try:
        substrate.assert_rml_safe(prepared, source_dir)
    except substrate.RmlSafetyError as exc:
        msg = str(exc)
        if _is_env_message(msg):
            raise _LoopEnvError(msg) from exc
        issues.append(classify(msg))
        # Unparseable Turtle: the design validator would silently pass — stop here so
        # the loop does not declare "converged" on a syntactically broken mapping.
        if "not parseable Turtle" in msg or "not valid Turtle" in msg:
            return _dedup(issues)
    except Exception as exc:  # rdflib/registry import failure surfaced as a raw error
        raise _LoopEnvError(str(exc)) from exc
    # Design validation — column / param / source-file moles with did-you-mean.
    try:
        substrate.validate_rml_design(prepared, source_dir)
    except substrate.RmlValidationError as exc:
        issues.extend(classify(m) for m in exc.issues)
    except Exception as exc:
        raise _LoopEnvError(str(exc)) from exc
    return _dedup(issues)


def _reference_count(ir_yaml: str | None, rml_ttl: str | None) -> int:
    """A cheap proxy for how much of the source a design covers. Used only to
    surface a soft ``coverage_dropped`` signal (a cornered weak model can delete
    mappings to reach zero issues).

    With a mapping spec the proxy is the number of property rows (more accurate
    than the old ``rml:reference`` count); an unparseable spec counts its
    ``predicate:`` lines instead (still monotone in mapped rows). Legacy raw RML
    keeps the original ``rml:reference`` count.
    """
    if ir_yaml and ir_yaml.strip():
        try:
            ir = parse_mapping_ir(ir_yaml)
        except Exception:
            return len(re.findall(r"^\s*-\s*predicate\s*:", ir_yaml, re.MULTILINE))
        return sum(len(m.properties) for m in ir.maps)
    if not rml_ttl:
        return 0
    return len(re.findall(r"\brml:reference\b", rml_ttl))


# ---------------------------------------------------------------------------
# Result records
# ---------------------------------------------------------------------------


@dataclass
class RoundRecord:
    """One loop round's outcome, for progress display + the result summary."""

    n: int
    issue_count: int
    categories: dict[str, int]
    refine_truncated: bool = False
    env_error: str | None = None


@dataclass
class DesignLoopResult:
    """The loop's outcome. ``proposal_md`` is the BEST (fewest-issue) schema reached;
    ``remaining_issues`` are the messages for THAT schema (not the last round's)."""

    proposal_md: str
    csv_inspection_md: str
    domain_hint: str
    metadata: dict[str, Any]
    rounds: list[RoundRecord]
    converged: bool
    terminal_reason: str
    remaining_issues: list[str]
    initial_issue_count: int
    tabular_only: bool
    coverage_dropped: bool = False


def _cats(issues: list[Issue]) -> dict[str, int]:
    out: dict[str, int] = {}
    for iss in issues:
        out[iss.category] = out.get(iss.category, 0) + 1
    return out


def _emit(on_progress: Callable[[dict[str, Any]], None] | None, **data: Any) -> None:
    if on_progress is not None:
        on_progress(data)


def _surgical_spec_repair(
    llm: Any,
    schema_md: str,
    ir_yaml: str,
    issues: list[Issue],
    oracle: str,
) -> str:
    """One repair round that regenerates ONLY the §9 mapping spec (Phase 2).

    Sets the guided-JSON schema on the client when it supports the attribute
    (OpenAI-compatible; others ignore it and answer from the prompt contract —
    the output is parsed either way and re-gated by the normal round checks).
    Returns the schema_md with the repaired spec spliced in. Raises
    ``ValueError`` (loop-feedable) when the repair output cannot be parsed or
    spliced; LLM errors propagate exactly like refine's.
    """
    try:
        function_names = [f.name for f in catalog_from_registry()]
    except ImportError:
        function_names = None
    user = build_spec_repair_user(ir_yaml, [i.message for i in issues], oracle)
    schema = mapping_ir_json_schema(function_names)
    had_attr = hasattr(llm, "response_schema")
    prior = getattr(llm, "response_schema", None)
    try:
        if had_attr:
            llm.response_schema = schema
        from asterism_step0.llm import as_completion

        raw = as_completion(llm.complete(SPEC_REPAIR_SYSTEM_PROMPT, user)).text
    finally:
        if had_attr:
            llm.response_schema = prior
    new_spec = parse_spec_json(raw)
    return replace_mapping_spec_block(schema_md, new_spec)


# ---------------------------------------------------------------------------
# The orchestrator (synchronous + fully unit-testable with a mock LLM)
# ---------------------------------------------------------------------------


def run_design_loop(
    csv_paths: list[Path | str],
    domain_hint: str,
    source_dir: Path | str,
    *,
    fk_hint_columns: list[str] | None = None,
    record_path: str | None = None,
    llm: Any = None,
    max_rounds: int = 3,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    on_llm_call: Callable[[str], None] | None = None,
    language: str | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> DesignLoopResult:
    """Run the propose→validate→refine self-correction loop.

    ``source_dir`` is the dir holding the uploaded CSVs (the propose job's temp dir),
    which lets the loop run the source-aware ``validate_rml_design``. ``on_progress`` is
    called with a dict per phase (for SSE). ``on_llm_call(feature)`` is called right
    after each LLM call so the caller can record usage (``last_usage`` is overwritten per
    call): feature is ``"propose"`` for round 0 and ``"propose.autocorrect"`` for refines.
    ``language`` is the output language for the schema's human-readable prose; it is
    forwarded to BOTH propose and every refine round (otherwise an autocorrect round
    would silently flip the prose back to English). ``should_cancel`` is the job's
    cooperative cancel poll: checked before round-0 propose and before every refine
    round; when it reports True the loop raises :class:`LLMCancelledError` instead of
    spending another LLM call. Never raises for a bad design; only a round-0 propose
    failure (or the caller's LLM raising) and a cancel (``LLMCancelledError`` — never
    swallowed into ``env_error``) propagate. See the module docstring for the stop
    conditions.
    """
    paths = [Path(p) for p in csv_paths]
    base = Path(source_dir)
    tabular_only = all(p.suffix.lower() in _TABULAR_SUFFIXES for p in paths)

    if should_cancel is not None and should_cancel():
        raise LLMCancelledError("cancelled")
    _emit(on_progress, phase="propose", round=0, message="初期設計を生成中")
    proposal = propose_schema(
        paths,
        domain_hint,
        fk_hint_columns=fk_hint_columns,
        record_path=record_path,
        llm=llm,
        language=language,
    )
    if on_llm_call is not None:
        on_llm_call("propose")
    schema_md = proposal.proposal_md
    oracle = build_oracle(base, paths)

    def _result(
        best_schema: str,
        best_issues: list[Issue],
        rounds: list[RoundRecord],
        *,
        converged: bool,
        reason: str,
        initial: int,
        base_refs: int,
    ) -> DesignLoopResult:
        return DesignLoopResult(
            proposal_md=best_schema,
            csv_inspection_md=proposal.csv_inspection_md,
            domain_hint=domain_hint,
            metadata=dict(proposal.metadata),
            rounds=rounds,
            converged=converged,
            terminal_reason=reason,
            remaining_issues=[iss.message for iss in best_issues],
            initial_issue_count=initial,
            tabular_only=tabular_only,
            coverage_dropped=(
                _reference_count(*_extract_design(best_schema)) < base_refs
                if base_refs
                else False
            ),
        )

    base_refs = _reference_count(*_extract_design(schema_md))

    # Evaluate round 0.
    try:
        ir_yaml, rml_ttl = _extract_design(schema_md)
        issues = collect_issues(ir_yaml, rml_ttl, base)
    except _LoopEnvError as exc:
        return _result(
            schema_md, [], [RoundRecord(0, 0, {}, env_error=str(exc))],
            converged=False, reason="env_error", initial=0, base_refs=base_refs,
        )
    initial = len(issues)
    rounds: list[RoundRecord] = [RoundRecord(0, initial, _cats(issues))]
    best_schema, best_issues = schema_md, issues
    _emit(on_progress, phase="validated", round=0, issue_count=initial,
          categories=_cats(issues), message=f"設計を検証: {initial} 件の問題")

    if not issues:
        return _result(schema_md, [], rounds, converged=True, reason="converged",
                       initial=initial, base_refs=base_refs)
    if max_rounds <= 0:
        return _result(best_schema, best_issues, rounds, converged=False,
                       reason="no_autocorrect", initial=initial, base_refs=base_refs)

    seen_keysets: set[frozenset[tuple[str, str]]] = set()
    prev_issues = issues
    for n in range(1, max_rounds + 1):
        # A pending cancel outranks every stop condition: raise before spending
        # another LLM call (the job runner turns this into the cancelled state).
        if should_cancel is not None and should_cancel():
            raise LLMCancelledError("cancelled")
        keyset = frozenset(i.key for i in prev_issues)
        if keyset in seen_keysets:  # cycle / no-progress (checked before spending a round)
            return _result(best_schema, best_issues, rounds, converged=False,
                           reason="no_progress", initial=initial, base_refs=base_refs)
        seen_keysets.add(keyset)

        # Phase 2 surgical repair (ADR mapping-ir-phase2-guided-repair): with a
        # mapping spec present, ONLY the §9 block is regenerated — guided JSON
        # where the provider supports it — and spliced back deterministically.
        # ~10x fewer output tokens per round, no whole-document truncation
        # risk, and unrelated sections are byte-untouched. The legacy raw-RML
        # path keeps the whole-document refine.
        surgical = bool(ir_yaml and ir_yaml.strip())
        _emit(on_progress, phase="refine", round=n, issue_count=len(prev_issues),
              categories=_cats(prev_issues),
              message=(
                  f"{len(prev_issues)} 件の問題を修正中 (§9 仕様のみ再生成)"
                  if surgical
                  else f"{len(prev_issues)} 件の問題を修正中"
              ))
        try:
            if surgical:
                schema_md = _surgical_spec_repair(
                    llm, schema_md, ir_yaml or "", prev_issues, oracle
                )
            else:
                comments = render_feedback(prev_issues, oracle)  # non-empty by construction
                ref = refine_schema(schema_md, comments, llm=llm, language=language)
        except LLMCancelledError:
            # A user cancel is NOT an env failure — it must reach the job runner
            # (which discards the run), never be swallowed into env_error below.
            raise
        except LLMTruncatedError as exc:
            rounds.append(RoundRecord(n, len(prev_issues), _cats(prev_issues),
                                      refine_truncated=True, env_error=f"truncated: {exc}"))
            return _result(best_schema, best_issues, rounds, converged=False,
                           reason="refine_truncated", initial=initial, base_refs=base_refs)
        except ValueError as exc:
            # Unparseable/unspliceable surgical output — an LLM-quality flake,
            # not an env failure: record the round (schema unchanged) and let
            # the next iteration's seen-keyset check stop as no_progress if it
            # repeats. Guided decoding makes this rare by construction.
            if on_llm_call is not None:
                on_llm_call("propose.autocorrect")
            rounds.append(RoundRecord(n, len(prev_issues), _cats(prev_issues),
                                      env_error=f"spec repair discarded: {exc}"))
            continue
        except Exception as exc:  # provider 429/quota/etc — non-loopable, keep best
            rounds.append(RoundRecord(n, len(prev_issues), _cats(prev_issues), env_error=str(exc)))
            return _result(best_schema, best_issues, rounds, converged=False,
                           reason="env_error", initial=initial, base_refs=base_refs)
        if on_llm_call is not None:
            on_llm_call("propose.autocorrect")

        if not surgical:
            if not ref.complete:  # refine dropped an artifact (truncation) → keep prior
                rounds.append(
                    RoundRecord(n, len(prev_issues), _cats(prev_issues), refine_truncated=True)
                )
                return _result(best_schema, best_issues, rounds, converged=False,
                               reason="refine_truncated", initial=initial, base_refs=base_refs)
            schema_md = ref.effective_schema_md  # == ref.refined_md when complete

        try:
            ir_yaml, rml_ttl = _extract_design(schema_md)
            issues = collect_issues(ir_yaml, rml_ttl, base)
        except _LoopEnvError as exc:
            rounds.append(RoundRecord(n, len(prev_issues), _cats(prev_issues), env_error=str(exc)))
            return _result(best_schema, best_issues, rounds, converged=False,
                           reason="env_error", initial=initial, base_refs=base_refs)
        rounds.append(RoundRecord(n, len(issues), _cats(issues)))
        _emit(on_progress, phase="validated", round=n, issue_count=len(issues),
              categories=_cats(issues), message=f"再検証: {len(issues)} 件の問題")

        if len(issues) < len(best_issues):
            best_schema, best_issues = schema_md, issues
        if not issues:
            return _result(schema_md, [], rounds, converged=True, reason="converged",
                           initial=initial, base_refs=base_refs)
        prev_issues = issues

    return _result(best_schema, best_issues, rounds, converged=False,
                   reason="max_rounds", initial=initial, base_refs=base_refs)


def _extract_design(schema_md: str) -> tuple[str | None, str | None]:
    """Pull the §9 design out of a schema Markdown via the SAME deterministic
    extractor the materialize endpoint uses (no LLM): ``(mapping_ir_yaml,
    rml_ttl)``. New proposals carry the mapping spec (first slot); legacy ones
    carry raw RML (second slot); ``(None, None)`` = a dropped/renamed §9 — a
    structural failure, not a clean design. The loop re-parses/compiles the
    spec itself (``_collect_ir_issues``) so the extraction stays extraction."""
    with tempfile.TemporaryDirectory(prefix="asterism-loop-mat-") as tmp:
        mat = materialize_schema(schema_md, tmp, "design", write=False)
    if mat.mapping_ir_yaml is not None:
        return mat.mapping_ir_yaml, None
    return None, mat.rml_ttl
