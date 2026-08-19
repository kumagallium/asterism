"""A swept scan axis is not a measured outcome, even though both are doubles.

Live 2026-08-19: a 3001-point XRD scan whose only columns were `2theta` and
`intensity`. Keying a row on 2theta is the right identity -- the instrument sets
that value, it does not measure it -- but the gate called every numeric key a
"measurement" and warned that correcting the value would strand citations. With
no other column in the file, EVERY candidate it could offer carried the same
warning, so the reader had nothing to choose. Only the order in the file tells
an axis from an outcome.
"""
from __future__ import annotations

from pathlib import Path

from asterism_step0.inspect import inspect_source_set
from asterism_step0.skeleton_annotate import _measurement_only


def _inspect(tmp_path: Path, csv: str):
    src = tmp_path / "data.csv"
    src.write_text(csv, encoding="utf-8")
    inspections, _ = inspect_source_set([src])
    return inspections[0]


def _scan(tmp_path: Path) -> str:
    # Both columns fractional: only the ORDER may distinguish them.
    rows = "\n".join(f"{20 + i * 0.02:.6f},{3600.5 + (i * 37) % 900}" for i in range(40))
    return f"2theta,intensity\n{rows}\n"


def test_a_swept_axis_is_recognised_and_an_outcome_is_not(tmp_path: Path) -> None:
    ins = _inspect(tmp_path, _scan(tmp_path))
    by_name = {c.name: c for c in ins.columns}
    assert by_name["2theta"].scan_axis is True
    assert by_name["intensity"].scan_axis is False


def test_a_key_made_of_the_axis_is_not_called_a_measurement(tmp_path: Path) -> None:
    ins = _inspect(tmp_path, _scan(tmp_path))
    assert _measurement_only(ins, ["2theta"]) is False
    # The outcome still is one, and so is a key that mixes the two.
    assert _measurement_only(ins, ["intensity"]) is True
    assert _measurement_only(ins, ["2theta", "intensity"]) is True


def test_a_descending_axis_counts_too(tmp_path: Path) -> None:
    rows = "\n".join(f"{800 - i * 5},{i}" for i in range(40))
    ins = _inspect(tmp_path, f"wavelength,counts\n{rows}\n")
    assert {c.name: c.scan_axis for c in ins.columns}["wavelength"] is True


def test_one_reversal_is_enough_to_disqualify(tmp_path: Path) -> None:
    """Strict on purpose: a column that wanders is an outcome, not an axis."""
    values = [20 + i * 0.02 for i in range(40)]
    values[17], values[18] = values[18], values[17]
    rows = "\n".join(f"{v:.6f},{i}" for i, v in enumerate(values))
    ins = _inspect(tmp_path, f"maybe_axis,n\n{rows}\n")
    assert {c.name: c.scan_axis for c in ins.columns}["maybe_axis"] is False


def test_a_short_file_is_not_called_an_axis(tmp_path: Path) -> None:
    """Three rising rows are a coincidence, not a swept scan."""
    ins = _inspect(tmp_path, "x,y\n1,9\n2,8\n3,7\n")
    assert {c.name: c.scan_axis for c in ins.columns}["x"] is False


def test_a_text_column_is_never_an_axis(tmp_path: Path) -> None:
    rows = "\n".join(f"sample-{i:03d},{i}" for i in range(40))
    ins = _inspect(tmp_path, f"name,n\n{rows}\n")
    assert {c.name: c.scan_axis for c in ins.columns}["name"] is False
