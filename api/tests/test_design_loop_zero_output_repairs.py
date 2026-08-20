"""Two defects that let a design pass every check and then produce NOTHING.

Live 2026-08-19, a 3001-row XRD scan (columns: 2theta, intensity):

1. The model wrote ``object_template: "xrdr:sample/sample1"`` -- a template with
   no placeholder. Morph-KGC refuses it outright ("Invalid template ... No pairs
   of unescaped curly braces were found") and the import stops. Seven AI rounds
   rewrote the same reasonable-looking line.
2. It wrote ``subject.transform: {2theta: iri_safe}``. ``iri_safe`` sanitizes a
   whole URL and returns "" for anything without a scheme -- so every subject was
   empty, every row was dropped, and the run "succeeded" with zero triples. No
   check fired, because nothing about the design is invalid.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from asterism_step0.materialize import materialize_schema

from asterism_api.design_loop import _evaluate

_CSV = b"2theta,intensity\n20.000000,3600.0\n20.020000,4233.3\n20.040000,4100.0\n"


def _spec(*, subject_transform: str = "", object_template: str = "") -> str:
    return (
        "## Schema proposal\n\n### 9. Declarative mapping spec\n\n"
        "```yaml\n"
        "version: 1\n"
        "prefixes:\n"
        '  ex: "https://ns.invalid/ns#"\n'
        '  exr: "https://ns.invalid/r/"\n'
        "maps:\n"
        "  - name: measurement\n"
        "    source: data.csv\n"
        "    subject:\n"
        '      template: "exr:measurement/{2theta}"\n'
        "      classes: [ex:Measurement]\n" + subject_transform + "    properties:\n"
        "      - predicate: ex:twoTheta\n"
        "        column: 2theta\n"
        "      - predicate: ex:intensity\n"
        "        column: intensity\n" + object_template + "```\n"
    )


def test_a_transform_that_empties_every_row_is_removed(tmp_path: Path) -> None:
    (tmp_path / "data.csv").write_bytes(_CSV)
    md = _spec(subject_transform='      transform: { "2theta": iri_safe }\n')
    repaired, ir_yaml, _ = _evaluate(md, tmp_path)

    spec = yaml.safe_load(ir_yaml)
    assert "transform" not in spec["maps"][0]["subject"]
    # The IDs the design describes are untouched: R2RML engines percent-encode
    # template placeholders themselves, so the transform was never needed.
    assert spec["maps"][0]["subject"]["template"] == "exr:measurement/{2theta}"
    assert "iri_safe" not in repaired.split("```yaml", 1)[1].split("```", 1)[0]


def test_a_transform_that_works_on_the_data_is_kept(tmp_path: Path) -> None:
    """Only a function that empties EVERY value is removed -- one that does its
    job is a judgment about the data and stays."""
    (tmp_path / "data.csv").write_bytes(_CSV)
    md = _spec(subject_transform='      transform: { "2theta": number_clean }\n')
    _, ir_yaml, _ = _evaluate(md, tmp_path)
    assert yaml.safe_load(ir_yaml)["maps"][0]["subject"]["transform"] == {
        "2theta": "number_clean"
    }


def test_a_template_with_no_placeholder_compiles_to_a_constant(tmp_path: Path) -> None:
    """rr:template with no {…} is what Morph-KGC rejects; the value is a constant."""
    (tmp_path / "data.csv").write_bytes(_CSV)
    md = _spec(
        object_template="      - predicate: ex:ofSample\n"
        '        object_template: "exr:sample/sample1"\n'
    )
    res = materialize_schema(md, str(tmp_path / "out"), "design", write=False)
    ttl = res.rml_ttl
    assert "https://ns.invalid/r/sample/sample1" in ttl
    # It arrives as a constant, never as a placeholder-less template.
    assert 'rr:template "https://ns.invalid/r/sample/sample1"' not in ttl
    assert "rr:constant <https://ns.invalid/r/sample/sample1>" in ttl


def test_a_real_template_still_compiles_as_a_template(tmp_path: Path) -> None:
    md = _spec(
        object_template="      - predicate: ex:ofSample\n"
        '        object_template: "exr:sample/{2theta}"\n'
    )
    ttl = materialize_schema(md, str(tmp_path / "out2"), "design", write=False).rml_ttl
    assert "rr:template" in ttl
    assert "https://ns.invalid/r/sample/{2theta}" in ttl
