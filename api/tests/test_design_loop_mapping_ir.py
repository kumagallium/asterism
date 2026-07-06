"""design_loop x Mapping IR: the front-loaded validation path (ADR
mapping-ir-compiler.md).

New proposals carry a §9 yaml mapping spec instead of raw RML. These tests pin
the loop behavior on that path with a scripted mock LLM and the REAL
parser/validator/compiler + fixture CSVs: IR-vocabulary feedback (did-you-mean),
convergence on a fixed spec, structurally-impossible error classes (a Turtle
parse error can no longer occur), the compiled-RML backstop, and the coverage
proxy on IR property rows.
"""
from __future__ import annotations

from pathlib import Path

from asterism_api import design_loop
from asterism_api.design_loop import collect_issues, run_design_loop

_HEADER = b"SID,composition\n1,Bi2Te3\n"


def _md_with_spec(reference_col: str, function: str = "trim_collapse") -> str:
    """A minimal schema Markdown whose only extractable artifact is the §9
    mapping spec, referencing ``reference_col`` of data.csv via ``function``."""
    return (
        "## Schema proposal\n\n### 9. Declarative mapping spec\n\n"
        "```yaml\n"
        "version: 1\n"
        "prefixes:\n"
        '  ex: "https://example.org/ns#"\n'
        '  exr: "https://example.org/r/"\n'
        "maps:\n"
        "  - name: thing\n"
        "    source: data.csv\n"
        "    subject:\n"
        '      template: "exr:thing/{SID}"\n'
        "      classes: [ex:Thing]\n"
        "    properties:\n"
        "      - predicate: ex:comp\n"
        f"        column: {reference_col}\n"
        f"        function: {function}\n"
        "```\n"
    )


_SPEC_BAD_COLUMN = _md_with_spec("comp")  # 'comp' is NOT a column → did-you-mean
_SPEC_BAD_FUNCTION = _md_with_spec("composition", function="rml_transform")
_SPEC_GOOD = _md_with_spec("composition")


def _write_csv(tmp_path: Path) -> list[Path]:
    (tmp_path / "data.csv").write_bytes(_HEADER)
    return [tmp_path / "data.csv"]


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


def _run(tmp_path: Path, llm, *, max_rounds: int = 3):
    return run_design_loop(
        _write_csv(tmp_path), "domain hint", tmp_path, llm=llm, max_rounds=max_rounds
    )


# ---- loop behavior on the IR path --------------------------------------------


def test_ir_bad_column_feedback_is_actionable_and_converges(tmp_path: Path) -> None:
    llm = _ScriptedLLM([_SPEC_BAD_COLUMN, _SPEC_GOOD])
    result = _run(tmp_path, llm)
    assert result.converged is True
    assert result.proposal_md == _SPEC_GOOD
    assert result.initial_issue_count == 1
    # The refine round received IR-vocabulary feedback with a did-you-mean —
    # the granularity a weak model can actually act on.
    _, refine_user = llm.calls[1]
    assert "column 'comp'" in refine_user
    assert "composition" in refine_user
    # …and the new-style oracle menu (bare names + args, no FnO parameter IRIs).
    assert "trim_collapse — 1 column input" in refine_user
    assert "fn:p_" not in refine_user


def test_ir_unknown_function_is_a_menu_issue(tmp_path: Path) -> None:
    llm = _ScriptedLLM([_SPEC_BAD_FUNCTION])
    result = _run(tmp_path, llm, max_rounds=0)
    assert result.converged is False
    assert any("rml_transform" in m and "Tier-0" in m for m in result.remaining_issues)


def test_ir_good_spec_converges_round_zero_through_full_gates(tmp_path: Path) -> None:
    """A clean spec converges at round 0 — which proves the whole chain ran:
    IR validation, deterministic compilation AND the unchanged RML gates
    (assert_rml_safe + validate_rml_design) on the compiled output."""
    llm = _ScriptedLLM([_SPEC_GOOD])
    result = _run(tmp_path, llm)
    assert result.converged is True
    assert result.terminal_reason == "converged"
    assert len(llm.calls) == 1


def test_ir_spec_wins_over_stale_turtle_and_cannot_be_a_turtle_error(tmp_path: Path) -> None:
    """With a mapping spec present, the 'invalid Turtle' error class is
    structurally impossible — even when a stale broken turtle block rides along."""
    stale = _SPEC_GOOD + "\n### RML (legacy)\n\n```turtle\nthis is @@@ not turtle\n```\n"
    llm = _ScriptedLLM([stale])
    result = _run(tmp_path, llm)
    assert result.converged is True


# ---- pure helpers -------------------------------------------------------------


def test_collect_issues_ir_parse_errors_are_ir_vocabulary(tmp_path: Path) -> None:
    _write_csv(tmp_path)
    issues = collect_issues("version: 1\nmaps: []\n", None, tmp_path)
    assert issues
    assert all("Turtle" not in i.message for i in issues)


def test_classify_ir_shapes_key_on_predicate_not_index() -> None:
    """Row indices shift when a model reshuffles properties; the canonical keys
    must survive that so oscillation (no-progress) detection still fires."""
    from asterism_api.design_loop import classify

    a = classify(
        "map 'paper'.properties[4] (schema:author): 'function' cannot be combined "
        "with object_template/constant — a function's output IS the object."
    )
    b = classify(
        "map 'paper'.properties[7] (schema:author): 'function' cannot be combined "
        "with object_template/constant — a function's output IS the object."
    )
    assert a.key == b.key and a.category == "structural"

    c = classify("map 'x'.properties[1] (sd:p).function requires 'column' (or 'columns').")
    d = classify("map 'x'.properties[3] (sd:p).function requires 'column' (or 'columns').")
    assert c.key == d.key and c.category == "function"

    e = classify(
        "map 'x'.properties[0].predicate: 'sd:project*' carries a cardinality marker — …"
    )
    assert e.category == "structural" and "sd:project*" in e.subject


def test_classify_type_cast_keys_on_function_name() -> None:
    from asterism_api.design_loop import classify

    a = classify(
        "map 'papers' property schema:name: 'str' is a type, not a Tier-0 function — …"
    )
    b = classify(
        "map 'papers' property dcterms:identifier: 'str' is a type, not a Tier-0 function — …"
    )
    assert a.category == "function" and a.subject == "typecast/str"
    assert a.key == b.key  # 23 identical inventions dedup to ONE feedback line


def test_reference_count_counts_ir_property_rows() -> None:
    ir_yaml = (
        "version: 1\n"
        'prefixes: { ex: "https://example.org/ns#", exr: "https://example.org/r/" }\n'
        "maps:\n"
        "  - name: a\n"
        "    source: d.csv\n"
        '    subject: { template: "exr:a/{id}", classes: [ex:A] }\n'
        "    properties:\n"
        "      - { predicate: ex:x, column: x }\n"
        "      - { predicate: ex:y, column: y }\n"
    )
    assert design_loop._reference_count(ir_yaml, None) == 2
    # Unparseable spec: fall back to counting predicate lines (never zero-out —
    # the coverage_dropped signal must not fire spuriously on a broken round).
    assert (
        design_loop._reference_count(
            "version: 7\nmaps:\n  - properties:\n      - predicate: ex:x\n", None
        )
        == 1
    )


def test_coverage_dropped_when_ir_rows_shrink(tmp_path: Path) -> None:
    """A cornered model erasing property rows to reach zero issues is surfaced."""
    two_rows = _SPEC_GOOD.replace(
        "      - predicate: ex:comp\n",
        "      - predicate: ex:sid\n        column: SID\n      - predicate: ex:comp\n",
    )
    # round 0: two rows, bad column → round 1 "fixes" by deleting down to one row
    bad_two = two_rows.replace("column: composition", "column: comp")
    llm = _ScriptedLLM([bad_two, _SPEC_GOOD])
    result = _run(tmp_path, llm)
    assert result.converged is True
    assert result.coverage_dropped is True
