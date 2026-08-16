"""The bundle trap validator (T1-T10) inside the self-correction loop.

Why these exist: ``/api/materialize`` runs the trap validator and the kantan
wizard STOPS on a blocking failure with a one-click "AI に直してもらう" button
whose only effect is a refine round. The loop used to converge on a different
validator, so every trap failure surfaced after "converged" and the human's
click was the missing round (live 2026-08-16: ~5 clicks on a 70-line XRD file).

These run the REAL validator over REAL materialized bundles with a scripted
mock LLM — a trap check silently returning nothing would make the whole change
a no-op, so each test pins an ACTUAL trap firing, not just the plumbing.
"""
from __future__ import annotations

from pathlib import Path

from asterism_api.design_loop import _evaluate, run_design_loop

_HEADER = b"SID,composition\n1,Bi2Te3\n"

# A legacy raw-RML §9 that references a REAL column — zero IR/RML issues, so the
# only thing that can fail is a trap.
_RML = (
    "## Schema proposal\n\n### 9. RML (declarative mapping)\n\n"
    "```turtle\n"
    "@prefix rr:  <http://www.w3.org/ns/r2rml#> .\n"
    "@prefix rml: <http://semweb.mmlab.be/ns/rml#> .\n"
    "@prefix ql:  <http://semweb.mmlab.be/ns/ql#> .\n"
    "<#M> a rr:TriplesMap ;\n"
    '  rml:logicalSource [ rml:source "data.csv" ; rml:referenceFormulation ql:CSV ] ;\n'
    '  rr:subjectMap [ rr:template "http://x/{SID}" ] ;\n'
    "  rr:predicateObjectMap [ rr:predicate <http://x/c> ;\n"
    '    rr:objectMap [ rml:reference "composition" ] ] .\n'
    "```\n"
)


def _mie(keywords: str, categories: str) -> str:
    return (
        "\n### 7. MIE YAML extras\n\n```yaml\n"
        "schema_info:\n"
        "  title: XRD reference pattern\n"
        f"  keywords: [{keywords}]\n"
        f"  categories: [{categories}]\n"
        "```\n"
    )


# T4 needs ≥ 5 keywords and ≥ 1 category — the classic weak-model miss.
_MD_TRAP_FAIL = _RML + _mie("xrd", "")
_MD_TRAP_OK = _RML + _mie("xrd, diffraction, aluminum, vanadium, tetragonal", "materials")


class _ScriptedLLM:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []
        self.model = "mock-model"

    def complete(self, system_prompt: str, user_message: str) -> str:
        self.calls.append((system_prompt, user_message))
        if len(self._responses) > 1:
            return self._responses.pop(0)
        return self._responses[0]


def _write_csv(tmp_path: Path) -> list[Path]:
    (tmp_path / "data.csv").write_bytes(_HEADER)
    return [tmp_path / "data.csv"]


def _run(tmp_path: Path, llm, *, max_rounds: int = 3):
    features: list[str] = []
    result = run_design_loop(
        _write_csv(tmp_path), "domain hint", tmp_path,
        llm=llm, max_rounds=max_rounds, on_llm_call=features.append,
    )
    return result, features


# ---- the trap actually reaches the loop --------------------------------------


def test_evaluate_reports_a_real_trap_failure(tmp_path: Path) -> None:
    """The ground truth this whole change rests on: a design the IR/RML gates
    accept can still fail a trap, and ``_evaluate`` returns it as an issue."""
    (tmp_path / "data.csv").write_bytes(_HEADER)
    _, issues = _evaluate(_MD_TRAP_FAIL, tmp_path)
    assert [(i.category, i.subject) for i in issues] == [("trap", "T4")]
    # The message carries the symptom AND the deterministic repair recipe — a
    # symptom-only line loops weak models forever (the 2026-07-14 T4 incident).
    assert "need ≥ 5" in issues[0].message
    assert "schema_info" in issues[0].message
    assert "↳" in issues[0].message


def test_evaluate_is_quiet_on_a_clean_bundle(tmp_path: Path) -> None:
    (tmp_path / "data.csv").write_bytes(_HEADER)
    _, issues = _evaluate(_MD_TRAP_OK, tmp_path)
    assert issues == []


# ---- the loop closes it without a human --------------------------------------


def test_trap_failure_is_fixed_by_the_loop_not_the_user(tmp_path: Path) -> None:
    """The behaviour change: round 0 fails T4, the loop refines, round 1 is
    clean. Before this, round 0 was reported "converged" and the user clicked
    "AI に直してもらう" to run exactly this round by hand."""
    llm = _ScriptedLLM([_MD_TRAP_FAIL, _MD_TRAP_OK])
    result, features = _run(tmp_path, llm)
    assert result.converged is True
    assert result.terminal_reason == "converged"
    assert result.initial_issue_count == 1
    assert result.remaining_issues == []
    assert features == ["propose", "propose.autocorrect"]
    # The refine got the trap's own repair recipe, not just the symptom.
    _, refine_user = llm.calls[1]
    assert "T4 MIE keywords / categories" in refine_user
    assert "schema_info" in refine_user


def test_trap_forces_the_whole_document_refine(tmp_path: Path) -> None:
    """Surgical repair regenerates ONLY §9 — it cannot reach the §7 MIE where
    T4 lives. A spec-carrying design with a failing trap must therefore take
    the whole-document refine path, or the round would change nothing and the
    loop would stop one round later with the trap still open."""
    from asterism_step0.spec_repair import SPEC_REPAIR_SYSTEM_PROMPT

    spec = (
        "## Schema proposal\n\n### 9. Declarative mapping spec\n\n"
        "```yaml\n"
        "version: 1\n"
        "prefixes:\n"
        '  ex: "https://ns.invalid/ns#"\n'
        '  exr: "https://ns.invalid/r/"\n'
        "maps:\n"
        "  - name: thing\n"
        "    source: data.csv\n"
        "    subject:\n"
        '      template: "exr:thing/{SID}"\n'
        "      classes: [ex:Thing]\n"
        "    properties:\n"
        "      - predicate: ex:comp\n"
        "        column: composition\n"
        "        function: trim_collapse\n"
        "```\n"
    )
    llm = _ScriptedLLM([spec + _mie("xrd", ""), spec + _mie(
        "xrd, diffraction, aluminum, vanadium, tetragonal", "materials"
    )])
    result, _ = _run(tmp_path, llm)
    assert result.converged is True
    repair_system, _ = llm.calls[1]
    assert repair_system != SPEC_REPAIR_SYSTEM_PROMPT


def test_unfixable_trap_stops_bounded_instead_of_looping(tmp_path: Path) -> None:
    """The loop must not spin on a model that keeps re-emitting the same trap:
    the no-progress detector keys on the trap ID (not the recipe text, which
    embeds derived candidate terms), so an identical failure stops the loop."""
    llm = _ScriptedLLM([_MD_TRAP_FAIL, _MD_TRAP_FAIL, _MD_TRAP_FAIL])
    result, features = _run(tmp_path, llm)
    assert result.converged is False
    assert result.terminal_reason == "no_progress"
    assert any("T4" in m for m in result.remaining_issues)
    assert features == ["propose", "propose.autocorrect"]  # did NOT spin to max
