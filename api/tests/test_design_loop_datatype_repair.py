"""Deterministic repair inside the loop: stamp the datatype the machine already
knows, instead of asking a model to type it in.

Live case (2026-08-17, gpt-oss-120b, a 70-line XRD reference card): four numeric
columns were mapped as untyped literals. The advisory that flags this has ALREADY
read every row and proven every cell numeric — the exact edit is known. The loop
still handed it to the model: 2 autocorrect rounds, then ``no_progress``; the user
then clicked "AI に直してもらう" three more times. All four were still untyped.
"""
from __future__ import annotations

from pathlib import Path

from asterism_api.design_loop import _evaluate, _verdict, run_design_loop

# `Z value` is all-integer, `Volume` fractional → the two xsd types must differ.
_CSV = b"SID,Z value,Volume,Name\n1,2,118.845,Al3V\n2,4,120.5,Al2V\n"


def _spec(*, extra: str = "") -> str:
    return (
        "## Schema proposal\n\n### 9. Declarative mapping spec\n\n"
        "```yaml\n"
        "version: 1\n"
        "prefixes:\n"
        '  ex: "https://ns.invalid/ns#"\n'
        '  exr: "https://ns.invalid/r/"\n'
        "maps:\n"
        "  - name: sample\n"
        "    source: data.csv\n"
        "    subject:\n"
        '      template: "exr:sample/{SID}"\n'
        "      classes: [ex:Sample]\n"
        "    properties:\n"
        "      - predicate: ex:name\n"
        "        column: Name\n"
        "      - predicate: ex:zValue\n"
        "        column: Z value\n"
        "      - predicate: ex:volume\n"
        "        column: Volume\n"
        f"{extra}"
        "```\n"
    )


def _src(tmp_path: Path) -> list[Path]:
    (tmp_path / "data.csv").write_bytes(_CSV)
    return [tmp_path / "data.csv"]


class _CountingLLM:
    """Returns the SAME design forever — so anything that converges did so
    without the model's help."""

    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0
        self.model = "mock-model"

    def complete(self, system_prompt: str, user_message: str) -> str:
        self.calls += 1
        return self.response


def test_untyped_numeric_columns_are_stamped_without_an_llm(tmp_path: Path) -> None:
    (tmp_path / "data.csv").write_bytes(_CSV)
    md = _spec()

    _, before = _verdict(md, tmp_path)
    assert sorted(i.subject for i in before) == ["Volume", "Z value"]

    repaired_md, _, after = _evaluate(md, tmp_path)
    assert after == []
    # Integer vs double comes from the DATA, not from the column name.
    assert "datatype: xsd:integer" in repaired_md
    assert "datatype: xsd:double" in repaired_md
    # A non-numeric column is left alone — stamping it would mint invalid literals.
    assert repaired_md.count("datatype:") == 2


def test_the_loop_converges_on_it_with_zero_llm_calls(tmp_path: Path) -> None:
    """The user-visible change: no round is spent, so no "AI に直してもらう"
    click is ever offered for this class of defect."""
    llm = _CountingLLM(_spec())
    result = run_design_loop(_src(tmp_path), "hint", tmp_path, llm=llm, max_rounds=5)
    assert result.converged is True
    assert result.remaining_issues == []
    assert llm.calls == 1  # round 0 (propose) only — no refine round
    assert result.rounds[0].issue_count == 0


def test_a_row_that_already_chose_is_untouched(tmp_path: Path) -> None:
    """An explicit datatype and a function pipeline both mean "the choice is
    made" — the same carve-out the validator makes."""
    (tmp_path / "data.csv").write_bytes(_CSV)
    md = _spec().replace(
        "      - predicate: ex:volume\n        column: Volume\n",
        "      - predicate: ex:volume\n        column: Volume\n"
        "        datatype: xsd:decimal\n",
    )
    repaired_md, _, after = _evaluate(md, tmp_path)
    assert after == []
    assert "datatype: xsd:decimal" in repaired_md  # author's choice survives
    assert "datatype: xsd:integer" in repaired_md  # Z value still stamped


def test_a_structural_error_is_left_untouched_for_the_llm(tmp_path: Path) -> None:
    """The repair never masks a problem it cannot solve. A hallucinated column
    is a structural error: IR validation short-circuits there and no advisory is
    even computed, so there is nothing to stamp and the document goes to the LLM
    byte-unchanged."""
    (tmp_path / "data.csv").write_bytes(_CSV)
    md = _spec().replace("column: Name", "column: Naem")
    repaired_md, _, after = _evaluate(md, tmp_path)
    assert repaired_md == md
    assert [i.subject for i in after] == ["Naem"]
    assert "Did you mean: Name?" in after[0].message


def test_legacy_raw_rml_designs_are_left_to_the_llm(tmp_path: Path) -> None:
    """A pre-spec proposal carries raw §9 RML and no Mapping IR to edit. The
    advisory still fires (so the LLM is told), but the deterministic repair
    declines rather than rewriting Turtle by hand."""
    (tmp_path / "data.csv").write_bytes(_CSV)
    md = (
        "## Schema proposal\n\n### 9. RML (declarative mapping)\n\n"
        "```turtle\n"
        "@prefix rr:  <http://www.w3.org/ns/r2rml#> .\n"
        "@prefix rml: <http://semweb.mmlab.be/ns/rml#> .\n"
        "@prefix ql:  <http://semweb.mmlab.be/ns/ql#> .\n"
        "<#M> a rr:TriplesMap ;\n"
        '  rml:logicalSource [ rml:source "data.csv" ; rml:referenceFormulation ql:CSV ] ;\n'
        '  rr:subjectMap [ rr:template "http://x/{SID}" ] ;\n'
        "  rr:predicateObjectMap [ rr:predicate <http://x/v> ;\n"
        '    rr:objectMap [ rml:reference "Volume" ] ] .\n'
        "```\n"
    )
    repaired_md, ir_yaml, after = _evaluate(md, tmp_path)
    assert ir_yaml is None  # no spec to repair
    assert repaired_md == md
    assert [i.subject for i in after] == ["Volume"]
