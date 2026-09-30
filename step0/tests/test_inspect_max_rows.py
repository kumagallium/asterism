"""Tests for ``inspect_csv``'s optional ``max_rows`` cap (ADR source-reshape.md R22).

A machine-generated derived table (reshape's explode/pivot/flatten output) is
column-uniform, so a head sample decides its type as well as a full scan
does. These tests cover:
  - the type/profile is decided from the first ``max_rows`` rows only
  - ``total_rows`` is still the EXACT row count, counted by a separate pass
  - ``render_markdown`` notes the cap only when it actually reduced the
    profiled row count
  - omitting ``max_rows`` is unchanged from before the argument existed
  - ``inspect_csv_set`` / ``inspect_source_set`` apply a per-source cap via
    ``max_rows_by_name``
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from asterism_step0.inspect import inspect_csv, inspect_csv_set, inspect_source_set, render_markdown


def _write_csv(path: Path, content: str) -> Path:
    path.write_text(dedent(content).lstrip("\n"), encoding="utf-8")
    return path


def _write_generated_csv(path: Path, n_rows: int, tail_value: str = "9") -> Path:
    """A column-uniform table whose LAST row's ``value`` would fail integer
    inference if read — the head sample must never see it when capped."""
    lines = ["id,value"]
    for i in range(n_rows - 1):
        lines.append(f"{i},{i}")
    lines.append(f"{n_rows - 1},{tail_value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_max_rows_caps_the_column_profile(tmp_path: Path) -> None:
    # The final row's value is a string ("not-a-number") — outside a 10-row
    # cap, so the capped inspection must still see the column as an integer.
    csv_path = _write_generated_csv(tmp_path / "derived.csv", 100, tail_value="not-a-number")
    capped = inspect_csv(csv_path, max_rows=10)
    by_name = {c.name: c for c in capped.columns}
    assert by_name["value"].inferred_type == "xsd:integer"

    uncapped = inspect_csv(csv_path)
    by_name_uncapped = {c.name: c for c in uncapped.columns}
    assert by_name_uncapped["value"].inferred_type == "xsd:string"


def test_max_rows_reports_exact_row_count(tmp_path: Path) -> None:
    csv_path = _write_generated_csv(tmp_path / "derived.csv", 1000)
    ins = inspect_csv(csv_path, max_rows=25)
    assert ins.total_rows == 1000  # exact, via the separate counting pass
    assert ins.sampled_rows == 25  # only the head slice was profiled


def test_max_rows_larger_than_file_is_a_no_op(tmp_path: Path) -> None:
    csv_path = _write_generated_csv(tmp_path / "small.csv", 5)
    ins = inspect_csv(csv_path, max_rows=1000)
    assert ins.total_rows == 5
    assert ins.sampled_rows == 5


def test_max_rows_none_is_unchanged(tmp_path: Path) -> None:
    csv_path = _write_generated_csv(tmp_path / "derived.csv", 50)
    ins = inspect_csv(csv_path)
    assert ins.total_rows == 50
    assert ins.sampled_rows == 50


def test_max_rows_handles_embedded_newlines_in_row_count(tmp_path: Path) -> None:
    """The exact-count pass uses csv.reader, not a line count, so a quoted
    cell with an embedded newline is still counted as ONE row."""
    csv_path = tmp_path / "quoted.csv"
    csv_path.write_text(
        'id,note\n1,"line one\nline two"\n2,plain\n3,plain\n4,plain\n',
        encoding="utf-8",
    )
    ins = inspect_csv(csv_path, max_rows=2)
    assert ins.total_rows == 4
    assert ins.sampled_rows == 2


def test_render_markdown_notes_the_cap_when_it_reduced_profiling(tmp_path: Path) -> None:
    csv_path = _write_generated_csv(tmp_path / "derived.csv", 1000)
    capped = inspect_csv(csv_path, max_rows=100)
    md = render_markdown([capped])
    assert "first 100 rows" in md
    assert "1,000" in md  # exact row count still rendered


def test_render_markdown_omits_the_note_without_a_cap(tmp_path: Path) -> None:
    csv_path = _write_csv(tmp_path / "basic.csv", "id,value\n1,10\n2,20\n")
    ins = inspect_csv(csv_path)
    md = render_markdown([ins])
    assert "Column profile" not in md


def test_inspect_csv_set_applies_max_rows_by_name(tmp_path: Path) -> None:
    derived = _write_generated_csv(tmp_path / "derived__long.csv", 500, tail_value="oops")
    raw = _write_csv(tmp_path / "raw.csv", "id,note\n1,a\n2,b\n")
    inspections, _fks = inspect_csv_set(
        [raw, derived], max_rows_by_name={"derived__long.csv": 10}
    )
    by_name = {ins.name: ins for ins in inspections}
    # Capped source: sees only the first 10 rows, "oops" never surfaces.
    assert by_name["derived__long.csv"].sampled_rows == 10
    assert by_name["derived__long.csv"].total_rows == 500
    # Uncapped source: unaffected.
    assert by_name["raw.csv"].sampled_rows == by_name["raw.csv"].total_rows == 2


def test_inspect_source_set_applies_max_rows_by_name(tmp_path: Path) -> None:
    derived = _write_generated_csv(tmp_path / "derived__long.csv", 300)
    inspections, _fks = inspect_source_set([derived], max_rows_by_name={"derived__long.csv": 20})
    assert inspections[0].sampled_rows == 20
    assert inspections[0].total_rows == 300
