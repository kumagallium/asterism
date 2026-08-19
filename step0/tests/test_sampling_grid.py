"""An evenly spaced monotonic column is a grid the rows were sampled on.

The rule is about the DATA, not about any kind of file: every value is a number,
each is strictly past the one before it, and every step is the same size. A
value on such a grid was SET by whoever took the data, so it is a sound identity
-- unlike a measured outcome, which gets corrected and re-mints the ID.

Monotonic alone was tried first and rejected: a file sorted by a measured column
is monotonic too, and treating that as a grid would silently drop a warning that
is still true for it. Even spacing is the part sorting does not reproduce.

Live case that motivated it (2026-08-19): a 3001-row, two-column instrument
sweep. With every column numeric, every key the gate could offer was
"measurement-only", so the warning sat on the correct answer and there was
nothing to choose.
"""
from __future__ import annotations

import random
from pathlib import Path

from asterism_step0.inspect import inspect_source_set
from asterism_step0.skeleton_annotate import _measurement_only


def _inspect(tmp_path: Path, csv: str, name: str = "data.csv"):
    src = tmp_path / name
    src.write_text(csv, encoding="utf-8")
    inspections, _ = inspect_source_set([src])
    return inspections[0]


def _grids(inspection) -> dict[str, bool]:
    return {c.name: c.sampling_grid for c in inspection.columns}


def _swept(n: int = 40) -> str:
    """A swept setting with a measured value beside it."""
    rows = "\n".join(f"{20 + i * 0.02:.6f},{3600.5 + (i * 37) % 900}" for i in range(n))
    return f"position,reading\n{rows}\n"


def test_an_evenly_spaced_column_is_a_grid_and_its_neighbour_is_not(tmp_path: Path) -> None:
    grids = _grids(_inspect(tmp_path, _swept()))
    assert grids["position"] is True
    assert grids["reading"] is False


def test_a_file_sorted_by_a_measured_column_is_not_a_grid(tmp_path: Path) -> None:
    """The false positive the rule exists to avoid: sorted, but irregular gaps."""
    rng = random.Random(20260819)
    values = sorted(round(rng.uniform(0.0, 100.0), 3) for _ in range(40))
    rows = "\n".join(f"S{i:03d},{v}" for i, v in enumerate(values))
    ins = _inspect(tmp_path, f"sample,yield\n{rows}\n")
    assert _grids(ins)["yield"] is False
    # …so the warning it would have suppressed is still made.
    assert _measurement_only(ins, ["yield"]) is True


def test_a_key_on_the_grid_is_not_called_a_measurement(tmp_path: Path) -> None:
    ins = _inspect(tmp_path, _swept())
    assert _measurement_only(ins, ["position"]) is False
    # The measured neighbour still is one, and so is a key mixing the two.
    assert _measurement_only(ins, ["reading"]) is True
    assert _measurement_only(ins, ["position", "reading"]) is True


def test_a_descending_grid_counts_too(tmp_path: Path) -> None:
    rows = "\n".join(f"{800 - i * 5}.0,{i}" for i in range(40))
    assert _grids(_inspect(tmp_path, f"setting,n\n{rows}\n"))["setting"] is True


def test_one_irregular_step_disqualifies_the_column(tmp_path: Path) -> None:
    values = [20 + i * 0.02 for i in range(40)]
    values[20] += 0.5  # still ascending, no longer evenly spaced
    rows = "\n".join(f"{v:.6f},{i}" for i, v in enumerate(values))
    assert _grids(_inspect(tmp_path, f"maybe,n\n{rows}\n"))["maybe"] is False


def test_a_reversal_disqualifies_the_column(tmp_path: Path) -> None:
    values = [20 + i * 0.02 for i in range(40)]
    values[17], values[18] = values[18], values[17]
    rows = "\n".join(f"{v:.6f},{i}" for i, v in enumerate(values))
    assert _grids(_inspect(tmp_path, f"maybe,n\n{rows}\n"))["maybe"] is False


def test_a_short_file_is_not_a_grid(tmp_path: Path) -> None:
    """Three rising rows are a coincidence, not a sampling grid."""
    assert _grids(_inspect(tmp_path, "x,y\n1.0,9\n2.0,8\n3.0,7\n"))["x"] is False


def test_a_text_column_is_never_a_grid(tmp_path: Path) -> None:
    rows = "\n".join(f"sample-{i:03d},{i}" for i in range(40))
    assert _grids(_inspect(tmp_path, f"name,n\n{rows}\n"))["name"] is False


def test_written_decimals_still_count_as_evenly_spaced(tmp_path: Path) -> None:
    """The step survives the rounding a written decimal carries."""
    rows = "\n".join(f"{i * 0.1:.1f},{i}" for i in range(40))
    assert _grids(_inspect(tmp_path, f"t,n\n{rows}\n"))["t"] is True
