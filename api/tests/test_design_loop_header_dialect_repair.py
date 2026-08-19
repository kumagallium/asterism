"""A pinned read dialect that starts the table on the wrong line.

The design pins how each source is read. When that pin is off, every later stage
is consistent and wrong: the data row taken for the header becomes the column
names, the rows above it are broadcast as `preamble_1 ... preamble_N`, and the
file's real columns are reported as "not imported". Nothing is invalid, so no
check objects -- the person is shown a table whose column names are numbers.

Live 2026-08-19: a two-column instrument sweep pinned `skip_rows: 12` where
detection said 1. The review screen offered `20.200000` and `3966.666667` as
column names (the file's 13th line) beside twelve `preamble_*` columns.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from asterism_api.design_loop import _evaluate, _looks_like_data_not_headers

_LINES = ["Al3V_bulk", "angle\tcount"] + [
    f"{20 + i * 0.02:.6f}\t{3600 + i}.0" for i in range(30)
]
_TSV = ("\n".join(_LINES) + "\n").encode("utf-8")


def _spec(skip_rows: int, preamble: str = "lines") -> str:
    return (
        "## Schema proposal\n\n### 9. Declarative mapping spec\n\n"
        "```yaml\n"
        "version: 1\n"
        "prefixes:\n"
        '  ex: "https://ns.invalid/ns#"\n'
        '  exr: "https://ns.invalid/r/"\n'
        "dialects:\n"
        "  data.txt:\n"
        '    encoding: "utf-8"\n'
        '    delimiter: "\\t"\n'
        "    collapse: false\n"
        f"    skip_rows: {skip_rows}\n"
        f'    preamble: "{preamble}"\n'
        "maps:\n"
        "  - name: point\n"
        "    source: data.txt\n"
        "    subject:\n"
        '      template: "exr:point/{angle}"\n'
        "      classes: [ex:Point]\n"
        "    properties:\n"
        "      - predicate: ex:angle\n"
        "        column: angle\n"
        "```\n"
    )


def _dialect(schema_md: str) -> dict:
    spec = yaml.safe_load(schema_md.split("```yaml", 1)[1].split("```", 1)[0])
    return spec["dialects"]["data.txt"]


def test_a_header_row_of_numbers_is_recognised() -> None:
    assert _looks_like_data_not_headers(["20.200000", "3966.666667"]) is True
    # One numeric name is a naming choice; a mixed row is not a misread.
    assert _looks_like_data_not_headers(["angle", "2"]) is False
    assert _looks_like_data_not_headers([]) is False


def test_the_table_start_is_corrected_to_what_the_file_shows(tmp_path: Path) -> None:
    (tmp_path / "data.txt").write_bytes(_TSV)
    repaired, ir_yaml, _ = _evaluate(_spec(skip_rows=12), tmp_path)
    fixed = yaml.safe_load(ir_yaml)["dialects"]["data.txt"]
    assert fixed["skip_rows"] == 1
    assert _dialect(repaired)["skip_rows"] == 1


def test_only_the_start_line_is_touched(tmp_path: Path) -> None:
    """The header being data proves where the table starts and nothing else.

    The preamble MODE is a design decision -- this design reads the lines above
    the header as columns -- so replacing the whole dialect would silently drop
    a column the design uses.
    """
    (tmp_path / "data.txt").write_bytes(_TSV)
    _, ir_yaml, _ = _evaluate(_spec(skip_rows=12, preamble="lines"), tmp_path)
    fixed = yaml.safe_load(ir_yaml)["dialects"]["data.txt"]
    assert fixed["preamble"] == "lines"
    assert fixed["encoding"] == "utf-8"
    assert fixed["delimiter"] == "\t"


def test_a_correct_dialect_is_left_alone(tmp_path: Path) -> None:
    (tmp_path / "data.txt").write_bytes(_TSV)
    _, ir_yaml, _ = _evaluate(_spec(skip_rows=1), tmp_path)
    assert yaml.safe_load(ir_yaml)["dialects"]["data.txt"]["skip_rows"] == 1


def test_the_columns_that_do_not_exist_are_now_reported(tmp_path: Path) -> None:
    """The point of repairing the read: the design's real error becomes visible.

    Under the wrong pin the design referenced columns that existed (they were
    data values), so nothing complained. Read correctly, the same design names
    columns the file does not have -- which is what it was doing all along.
    """
    (tmp_path / "data.txt").write_bytes(_TSV)
    md = _spec(skip_rows=12).replace(
        "      - predicate: ex:angle\n        column: angle\n",
        '      - predicate: ex:angle\n        column: "20.200000"\n',
    ).replace('      template: "exr:point/{angle}"', '      template: "exr:point/{20.200000}"')
    _, _, issues = _evaluate(md, tmp_path)
    assert any("20.200000" in i.message and "is not in" in i.message for i in issues)
