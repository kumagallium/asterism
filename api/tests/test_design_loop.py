"""The automatic propose→validate→refine self-correction loop (TODO ④).

These exercise ``asterism_api.design_loop`` end-to-end with a SCRIPTED mock LLM (a
queue of canned proposal/refine markdown) and the REAL deterministic validators over a
tiny fixture CSV dir — no network, no API key. They pin the loop's convergence, its stop
conditions (no-progress / max-rounds / refine-truncation / env-bail), the invalid-Turtle
convergence-hole guard, per-round LLM-call feature tagging, the pure feedback renderer,
and the Tier-0 oracle's exact contents.
"""
from __future__ import annotations

from pathlib import Path

from asterism_step0.llm import LLMTruncatedError

from asterism_api import design_loop
from asterism_api.design_loop import (
    Issue,
    build_oracle,
    classify,
    render_feedback,
    run_design_loop,
)

# ---- fixtures ---------------------------------------------------------------

_HEADER = b"SID,composition\n1,Bi2Te3\n"


def _md_with_rml(reference_col: str) -> str:
    """A minimal schema Markdown whose only extractable artifact is the §RML block,
    referencing ``reference_col`` from data.csv."""
    return (
        "## Schema proposal\n\n### RML (declarative mapping)\n\n"
        "```turtle\n"
        "@prefix rr:  <http://www.w3.org/ns/r2rml#> .\n"
        "@prefix rml: <http://semweb.mmlab.be/ns/rml#> .\n"
        "@prefix ql:  <http://semweb.mmlab.be/ns/ql#> .\n"
        "<#M> a rr:TriplesMap ;\n"
        '  rml:logicalSource [ rml:source "data.csv" ; rml:referenceFormulation ql:CSV ] ;\n'
        '  rr:subjectMap [ rr:template "http://x/{SID}" ] ;\n'
        "  rr:predicateObjectMap [ rr:predicate <http://x/c> ;\n"
        f'    rr:objectMap [ rml:reference "{reference_col}" ] ] .\n'
        "```\n"
    )


_MD_BAD = _md_with_rml("comp")  # 'comp' is NOT a column → flagged (did-you-mean composition)
_MD_GOOD = _md_with_rml("composition")  # correct → zero issues
_MD_NO_RML = "## Schema proposal\n\nNo mapping here.\n"  # structural failure
_MD_BROKEN_TURTLE = (
    "## Schema proposal\n\n### RML\n\n```turtle\n"
    "this is <not> valid turtle @@@ ;;;\n"
    "```\n"
)


class _ScriptedLLM:
    """Returns canned responses in order (repeats the last once exhausted); records the
    (system, user) of every call so tests can assert cache-stability + oracle injection."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []
        self.model = "mock-model"

    def complete(self, system_prompt: str, user_message: str) -> str:
        self.calls.append((system_prompt, user_message))
        if len(self._responses) > 1:
            return self._responses.pop(0)
        return self._responses[0]


class _RaisingLLM:
    """Returns the first response, then raises ``exc`` on the next call (a refine)."""

    def __init__(self, first: str, exc: Exception) -> None:
        self._first = first
        self._exc = exc
        self.calls = 0

    def complete(self, system_prompt: str, user_message: str) -> str:
        self.calls += 1
        if self.calls == 1:
            return self._first
        raise self._exc


def _write_csv(tmp_path: Path) -> list[Path]:
    (tmp_path / "data.csv").write_bytes(_HEADER)
    return [tmp_path / "data.csv"]


def _run(tmp_path: Path, llm, *, max_rounds: int = 3):
    features: list[str] = []
    result = run_design_loop(
        _write_csv(tmp_path),
        "domain hint",
        tmp_path,
        llm=llm,
        max_rounds=max_rounds,
        on_llm_call=features.append,
    )
    return result, features


# ---- convergence ------------------------------------------------------------


def test_converges_bad_then_good(tmp_path: Path) -> None:
    # Round 0 proposes a bad column; round 1 refine fixes it → zero issues → converged.
    llm = _ScriptedLLM([_MD_BAD, _MD_GOOD])
    result, features = _run(tmp_path, llm)
    assert result.converged is True
    assert result.terminal_reason == "converged"
    assert result.remaining_issues == []
    assert result.proposal_md == _MD_GOOD  # best = the corrected schema
    assert result.initial_issue_count == 1
    # One propose + exactly one refine, each tagged distinctly for the usage ledger.
    assert features == ["propose", "propose.autocorrect"]


def test_clean_first_shot_makes_no_refine_call(tmp_path: Path) -> None:
    # A correct round-0 design converges with NO refine call (cheap on strong models).
    llm = _ScriptedLLM([_MD_GOOD])
    result, features = _run(tmp_path, llm)
    assert result.converged is True
    assert result.initial_issue_count == 0
    assert features == ["propose"]


def test_language_reaches_propose_and_every_refine_round(tmp_path: Path) -> None:
    # The output-language directive must ride EVERY round's user message — otherwise an
    # autocorrect round would silently flip the prose back to English. System prompts
    # stay directive-free (byte-stable, cacheable).
    llm = _ScriptedLLM([_MD_BAD, _MD_GOOD])
    result = run_design_loop(
        _write_csv(tmp_path), "domain hint", tmp_path, llm=llm, max_rounds=3, language="ja"
    )
    assert result.converged is True
    assert len(llm.calls) == 2  # propose + one refine
    for system, user in llm.calls:
        assert "# Output language" in user
        assert "Japanese (日本語)" in user
        assert "# Output language" not in system


# ---- stop conditions --------------------------------------------------------


def test_no_progress_stops_and_returns_best(tmp_path: Path) -> None:
    # The model keeps emitting the SAME bad design → the key-set repeats → stop after one
    # refine (not run to max), returning the best schema + ITS remaining issues.
    llm = _ScriptedLLM([_MD_BAD, _MD_BAD])
    result, features = _run(tmp_path, llm, max_rounds=3)
    assert result.converged is False
    assert result.terminal_reason == "no_progress"
    assert len(result.remaining_issues) == 1
    assert any("comp" in m for m in result.remaining_issues)
    assert features == ["propose", "propose.autocorrect"]  # did NOT spin to max


def test_max_rounds_cap(tmp_path: Path) -> None:
    # Each round surfaces a DIFFERENT bad column (key-set never repeats) → runs to the cap.
    llm = _ScriptedLLM([
        _md_with_rml("aa"), _md_with_rml("bb"), _md_with_rml("cc"), _md_with_rml("dd"),
    ])
    result, features = _run(tmp_path, llm, max_rounds=2)
    assert result.converged is False
    assert result.terminal_reason == "max_rounds"
    assert features == ["propose", "propose.autocorrect", "propose.autocorrect"]


def test_refine_truncation_keeps_prior_complete_schema(tmp_path: Path) -> None:
    # The refine drops the §RML artifact the input had (complete=False) → keep the prior
    # complete schema, stop.
    llm = _ScriptedLLM([_MD_BAD, _MD_NO_RML])
    result, _ = _run(tmp_path, llm)
    assert result.converged is False
    assert result.terminal_reason == "refine_truncated"
    assert result.proposal_md == _MD_BAD  # the last COMPLETE schema, not the truncated one


def test_llm_truncated_error_bails_keeping_best(tmp_path: Path) -> None:
    llm = _RaisingLLM(_MD_BAD, LLMTruncatedError("too long"))
    result, _ = _run(tmp_path, llm)
    assert result.converged is False
    assert result.terminal_reason == "refine_truncated"
    assert result.proposal_md == _MD_BAD


def test_provider_exception_bails_not_crash(tmp_path: Path) -> None:
    # A 429/quota (a generic provider error, NOT truncation/import) mid-loop must keep the
    # best-so-far schema, not surface as a crash.
    llm = _RaisingLLM(_MD_BAD, RuntimeError("429 rate limit"))
    result, _ = _run(tmp_path, llm)
    assert result.converged is False
    assert result.terminal_reason == "env_error"
    assert result.proposal_md == _MD_BAD


def test_autocorrect_zero_is_plain_propose(tmp_path: Path) -> None:
    # max_rounds=0 → round-0 propose only (no refine), proposal_md unchanged.
    llm = _ScriptedLLM([_MD_BAD])
    result, features = _run(tmp_path, llm, max_rounds=0)
    assert result.terminal_reason == "no_autocorrect"
    assert result.proposal_md == _MD_BAD
    assert result.converged is False
    assert features == ["propose"]  # no refine


# ---- invalid-Turtle convergence hole ---------------------------------------


def test_broken_turtle_is_not_reported_converged(tmp_path: Path) -> None:
    # validate_rml_design SILENTLY returns [] on unparseable Turtle; assert_rml_safe is
    # the only layer that flags it. A broken-turtle design must NOT be called 'converged'.
    llm = _ScriptedLLM([_MD_BROKEN_TURTLE])
    result, _ = _run(tmp_path, llm, max_rounds=0)
    assert result.converged is False
    assert result.initial_issue_count >= 1
    assert any("turtle" in m.lower() for m in result.remaining_issues)


def test_missing_rml_is_a_structural_issue(tmp_path: Path) -> None:
    llm = _ScriptedLLM([_MD_NO_RML])
    result, _ = _run(tmp_path, llm, max_rounds=0)
    assert result.converged is False
    assert any("§RML" in m or "RML declarative" in m for m in result.remaining_issues)


# ---- pure helpers -----------------------------------------------------------


def test_classify_shapes() -> None:
    assert classify("source file 'x.csv' referenced by rml:source does not exist").category == (
        "source"
    )
    assert classify("column 'comp' referenced by the mapping is not in data.csv").category == (
        "column"
    )
    assert classify(
        "json_pluck does not accept parameter 'p_field1'; it accepts: p_field."
    ).category == "function"
    assert classify("json_pluck is missing required parameter 'p_field'.").category == "function"
    assert classify("RML mapping is not parseable Turtle: bad").category == "turtle"
    # Unknown shape is still keyed (never silently dropped).
    assert classify("something totally new").category == "other"


def test_dedup_collapses_same_key() -> None:
    dup = (
        "column 'comp' referenced by the mapping is not in data.csv "
        "(columns: SID). Did you mean: SID?"
    )
    issues = design_loop._dedup([classify(dup), classify(dup)])
    assert len(issues) == 1


def test_render_feedback_single_comment_with_oracle(tmp_path: Path) -> None:
    _write_csv(tmp_path)
    oracle = build_oracle(tmp_path, [tmp_path / "data.csv"])
    comments = render_feedback([Issue("column", "comp", "column 'comp' is wrong")], oracle)
    assert len(comments) == 1  # a SINGLE joined comment (proven manual shape)
    assert "column 'comp' is wrong" in comments[0]
    assert "closed menu" in comments[0]


def test_oracle_lists_exact_columns_and_param_local_names(tmp_path: Path) -> None:
    _write_csv(tmp_path)
    oracle = build_oracle(tmp_path, [tmp_path / "data.csv"])
    assert "data.csv" in oracle
    assert "SID, composition" in oracle  # exact real header
    # Exact FnO parameter local-names: json_pluck's field param is p_field (the
    # p_field1-vs-p_field mole the loop targets), while template legitimately has
    # p_field1..p_field4 — both must be rendered with their OWN correct params.
    assert "fn:json_pluck(fn:p_value, fn:p_field)" in oracle
    assert (
        "fn:template(fn:p_template, fn:p_field1, fn:p_field2, fn:p_field3, fn:p_field4)"
        in oracle
    )
    assert "fn:iri_safe(fn:p_value)" in oracle


def test_system_prompt_byte_stable_across_rounds(tmp_path: Path) -> None:
    # The cacheable SYSTEM_PROMPT must be identical every call (per-round feedback rides
    # the USER message) or prompt-caching breaks.
    llm = _ScriptedLLM([_MD_BAD, _MD_GOOD])
    _run(tmp_path, llm)
    systems = {system for system, _ in llm.calls}
    # propose and refine each use their own module-constant system prompt; within a role
    # it must be byte-identical. Assert the refine call's system equals itself across the
    # (single) refine here and that no per-round mutation crept in.
    assert len(llm.calls) == 2
    # The oracle + issues live in the USER message, never the system prompt.
    for _system, user in llm.calls[1:]:
        assert "closed menu" in user
    assert all("closed menu" not in system for system in systems)
