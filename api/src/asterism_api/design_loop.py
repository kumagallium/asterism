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
* Each round: ``materialize_schema(write=False)`` to extract the §9 RML; if it is absent
  emit "§RML missing" and stop collecting (nothing else is checkable). Otherwise
  ``substitute_run_id`` then run ``assert_rml_safe`` FIRST (the ONLY layer that flags
  invalid Turtle — ``validate_rml_design`` silently returns [] on unparseable Turtle),
  then ``validate_rml_design`` against the REAL uploaded source dir (the strongest
  feedback: column / param / source-file moles with difflib "Did you mean X?").
* Feedback = ``composeFixComment``'s server twin PLUS a deterministic **Tier-0 oracle**
  appendix (exact filenames, BOM-safe real columns, every REGISTRY function with its
  exact parameter local-names) — the closed menu that stops a weak model from
  re-hallucinating. It is passed to refine as a SINGLE joined comment (the proven manual
  shape), in the USER message only (cache-safe).
* Stop conditions (priority): converged (zero issues); env-bail (any LLM exception +
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
from asterism.functions import REGISTRY
from asterism.rml_validate import read_csv_header
from asterism_step0.llm import LLMTruncatedError
from asterism_step0.materialize import materialize_schema
from asterism_step0.propose import propose_schema
from asterism_step0.refine import refine_schema

_TABULAR_SUFFIXES = frozenset({".csv", ".tsv"})

# The refine comment intro (server twin of the UI's workbench:fix.commentIntro).
_FIX_INTRO = (
    "Fix ONLY the following design issues; keep everything else unchanged. Do not "
    "introduce new columns, functions, or source files — correct the mapping to match "
    "the real data and the vetted Tier-0 functions listed at the end."
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


def _local_name(iri: str) -> str:
    """Trailing path/fragment segment of an IRI (e.g. …/p_field → p_field)."""
    return iri.rsplit("#", 1)[-1].rsplit("/", 1)[-1] or iri


def build_oracle(source_dir: Path | str, csv_paths: list[Path | str]) -> str:
    """A deterministic 'closed menu' appendix for the refine USER message.

    Enumerates (1) the exact legal source filenames, (2) each tabular file's real
    columns via the SAME BOM-safe header reader the validator uses (so the menu can't
    teach a column name the validator would then reject), and (3) every vetted Tier-0
    function with its exact FnO parameter local-names. This turns each refine round from
    "you were wrong, try again" (which weak models re-break) into "pick only from this
    menu" — the single strongest lever for weak-model convergence. Pure + LLM-free.
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
        "Vetted Tier-0 functions (fn:) with their EXACT parameter names — reference no "
        "other function and no other parameter name:"
    )
    for spec in REGISTRY:
        params = ", ".join(f"fn:{_local_name(iri)}" for iri in spec.params.values())
        lines.append(f"  • fn:{spec.name}({params})")
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


def collect_issues(rml_ttl: str | None, source_dir: Path) -> list[Issue]:
    """Deterministic, LLM-free machine feedback for ONE candidate design.

    Order matters: a missing §RML block short-circuits (nothing else is checkable);
    ``assert_rml_safe`` runs BEFORE ``validate_rml_design`` because it is the only layer
    that flags invalid Turtle (the design validator silently returns [] on unparseable
    Turtle — the "convergence hole"). Raises :class:`_LoopEnvError` when the validators
    themselves are unavailable.
    """
    if not rml_ttl or not rml_ttl.strip():
        return [
            Issue(
                "structural",
                "rml",
                "The §RML declarative-mapping block is missing or empty. Emit a complete "
                "```turtle fenced block under an 'RML' / 'Declarative mapping' heading.",
            )
        ]
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


def _reference_count(rml_ttl: str | None) -> int:
    """A cheap proxy for how much of the source a mapping covers: the number of
    ``rml:reference`` uses. Used only to surface a soft ``coverage_dropped`` signal
    (a cornered weak model can delete mappings to reach zero issues)."""
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
) -> DesignLoopResult:
    """Run the propose→validate→refine self-correction loop.

    ``source_dir`` is the dir holding the uploaded CSVs (the propose job's temp dir),
    which lets the loop run the source-aware ``validate_rml_design``. ``on_progress`` is
    called with a dict per phase (for SSE). ``on_llm_call(feature)`` is called right
    after each LLM call so the caller can record usage (``last_usage`` is overwritten per
    call): feature is ``"propose"`` for round 0 and ``"propose.autocorrect"`` for refines.
    ``language`` is the output language for the schema's human-readable prose; it is
    forwarded to BOTH propose and every refine round (otherwise an autocorrect round
    would silently flip the prose back to English). Never raises for a bad design; only
    a round-0 propose failure (or the caller's LLM raising) propagates. See the module
    docstring for the stop conditions.
    """
    paths = [Path(p) for p in csv_paths]
    base = Path(source_dir)
    tabular_only = all(p.suffix.lower() in _TABULAR_SUFFIXES for p in paths)

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
                _reference_count(_extract_rml(best_schema)) < base_refs if base_refs else False
            ),
        )

    base_refs = _reference_count(_extract_rml(schema_md))

    # Evaluate round 0.
    try:
        issues = collect_issues(_extract_rml(schema_md), base)
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
        keyset = frozenset(i.key for i in prev_issues)
        if keyset in seen_keysets:  # cycle / no-progress (checked before spending a round)
            return _result(best_schema, best_issues, rounds, converged=False,
                           reason="no_progress", initial=initial, base_refs=base_refs)
        seen_keysets.add(keyset)

        comments = render_feedback(prev_issues, oracle)  # non-empty by construction
        _emit(on_progress, phase="refine", round=n, issue_count=len(prev_issues),
              categories=_cats(prev_issues), message=f"{len(prev_issues)} 件の問題を修正中")
        try:
            ref = refine_schema(schema_md, comments, llm=llm, language=language)
        except LLMTruncatedError as exc:
            rounds.append(RoundRecord(n, len(prev_issues), _cats(prev_issues),
                                      refine_truncated=True, env_error=f"truncated: {exc}"))
            return _result(best_schema, best_issues, rounds, converged=False,
                           reason="refine_truncated", initial=initial, base_refs=base_refs)
        except Exception as exc:  # provider 429/quota/etc — non-loopable, keep best
            rounds.append(RoundRecord(n, len(prev_issues), _cats(prev_issues), env_error=str(exc)))
            return _result(best_schema, best_issues, rounds, converged=False,
                           reason="env_error", initial=initial, base_refs=base_refs)
        if on_llm_call is not None:
            on_llm_call("propose.autocorrect")

        if not ref.complete:  # refine dropped an artifact (truncation) → keep prior complete
            rounds.append(
                RoundRecord(n, len(prev_issues), _cats(prev_issues), refine_truncated=True)
            )
            return _result(best_schema, best_issues, rounds, converged=False,
                           reason="refine_truncated", initial=initial, base_refs=base_refs)
        schema_md = ref.effective_schema_md  # == ref.refined_md when complete

        try:
            issues = collect_issues(_extract_rml(schema_md), base)
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


def _extract_rml(schema_md: str) -> str | None:
    """Pull the §RML turtle string out of a schema Markdown via the SAME deterministic
    extractor the materialize endpoint uses (no LLM). None when no RML block is present
    (a dropped/renamed §RML — a structural failure, not a clean design)."""
    with tempfile.TemporaryDirectory(prefix="asterism-loop-mat-") as tmp:
        mat = materialize_schema(schema_md, tmp, "design", write=False)
    return mat.rml_ttl
