"""Deterministic repair inside the loop: put a row's own values on the per-row
map that has none, instead of telling a person to do it.

Live case (2026-08-19, an XRD reference card): the empty-shell advisory told the
reader that the per-row kind held none of the row's values and that adding the
row-varying columns to it would fix that -- but no screen in the wizard assigns
columns to a kind, so the sentence was unactionable. Ten AI rounds did not do it
either. The machine has read every row and knows exactly which columns belong
where, so it makes the edit itself.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from asterism_api.design_loop import _evaluate, _verdict

# One sample (03-065-2664), three diffraction rows. `2theta` / `d` / `I` vary per
# row; `Name` is the same on every row (file-scoped — the sample's, not the peak's).
_CSV = (
    b"No,Name,2theta,d,I\n"
    b"03-065-2664,Aluminum Vanadium,21.34,4.161,5.0\n"
    b"03-065-2664,Aluminum Vanadium,25.87,3.441,11.5\n"
    b"03-065-2664,Aluminum Vanadium,33.51,2.672,2.7\n"
)


def _spec() -> str:
    """The shape a weak model produces: a per-row `peak` map carrying only a link
    back to the sample, with the peak's own measurements parked on `sample`."""
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
        '      template: "exr:sample/{No}"\n'
        "      classes: [ex:Sample]\n"
        "    properties:\n"
        "      - predicate: ex:name\n"
        "        column: Name\n"
        "      - predicate: ex:twoTheta\n"
        "        column: 2theta\n"
        "      - predicate: ex:dSpacing\n"
        "        column: d\n"
        "      - predicate: ex:intensity\n"
        "        column: I\n"
        "  - name: peak\n"
        "    source: data.csv\n"
        "    subject:\n"
        '      template: "exr:peak/{No}/{2theta}"\n'
        "      classes: [ex:Peak]\n"
        "    properties:\n"
        "      - predicate: ex:ofSample\n"
        '        object_template: "exr:sample/{No}"\n'
        "```\n"
    )


def _maps(schema_md: str) -> dict[str, dict]:
    block = schema_md.split("```yaml", 1)[1].split("```", 1)[0]
    spec = yaml.safe_load(block)
    return {m["name"]: m for m in spec["maps"]}


def _columns(entry: dict) -> set[str]:
    return {p["column"] for p in entry.get("properties") or [] if isinstance(p.get("column"), str)}


def test_the_rows_own_values_are_moved_onto_the_row_map(tmp_path: Path) -> None:
    (tmp_path / "data.csv").write_bytes(_CSV)
    schema_md = _spec()
    # The advisory fires on the design as written.
    _, before = _verdict(schema_md, tmp_path)
    assert any("mints one entity per row" in i.message for i in before)

    repaired, _, after = _evaluate(schema_md, tmp_path)
    maps = _maps(repaired)
    # The three per-row measurements moved to `peak`…
    assert {"2theta", "d", "I"} <= _columns(maps["peak"])
    # …and left `sample`, where they collapsed into multi-values.
    assert not ({"2theta", "d", "I"} & _columns(maps["sample"]))
    # The file-scoped column stays with the sample (G1: it is not the row's).
    assert "Name" in _columns(maps["sample"])
    # The link the model did write is untouched.
    assert any(p.get("predicate") == "ex:ofSample" for p in maps["peak"]["properties"])
    # And the advisory the repair answers is gone, with no LLM involved.
    assert not any("mints one entity per row" in i.message for i in after)


def test_a_predicate_keeps_everything_it_carried(tmp_path: Path) -> None:
    """A move, not a rewrite: the property arrives with its own predicate and
    datatype, so nothing about the design is invented."""
    (tmp_path / "data.csv").write_bytes(_CSV)
    schema_md = _spec().replace(
        "      - predicate: ex:twoTheta\n        column: 2theta\n",
        "      - predicate: ex:twoTheta\n        column: 2theta\n"
        "        datatype: xsd:double\n        unit: deg\n",
    )
    repaired, _, _ = _evaluate(schema_md, tmp_path)
    moved = next(
        p
        for p in _maps(repaired)["peak"]["properties"]
        if p.get("column") == "2theta"
    )
    assert moved["predicate"] == "ex:twoTheta"
    assert moved["datatype"] == "xsd:double"
    assert moved["unit"] == "deg"


def test_a_design_with_nothing_parked_is_left_alone(tmp_path: Path) -> None:
    """No advisory, no edit — the repair never touches a design it was not called for."""
    (tmp_path / "data.csv").write_bytes(_CSV)
    good = _spec().replace(
        "      - predicate: ex:twoTheta\n        column: 2theta\n"
        "      - predicate: ex:dSpacing\n        column: d\n"
        "      - predicate: ex:intensity\n        column: I\n",
        "",
    ).replace(
        "      - predicate: ex:ofSample\n"
        '        object_template: "exr:sample/{No}"\n',
        "      - predicate: ex:ofSample\n"
        '        object_template: "exr:sample/{No}"\n'
        "      - predicate: ex:twoTheta\n        column: 2theta\n"
        "      - predicate: ex:dSpacing\n        column: d\n"
        "      - predicate: ex:intensity\n        column: I\n",
    )
    repaired, _, _ = _evaluate(good, tmp_path)
    maps = _maps(repaired)
    assert {"2theta", "d", "I"} <= _columns(maps["peak"])
    assert _columns(maps["sample"]) == {"Name"}
