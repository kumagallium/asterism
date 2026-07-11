"""Tests for asterism_step0.dialect (ADR source-dialect.md, design side).

Fixtures are synthesized from the two audited real-file shapes — a CP932 CRLF
tab-separated XRD export with a sample-name preamble line, and a UTF-8 ICDD
reference card whose d-I table is whitespace-separated — plus clean CSVs that
MUST stay default (the is_default gate keeps current behavior byte-identical).
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from asterism_step0.dialect import (
    SourceDialect,
    apply_detected_dialects,
    describe_dialect,
    detect_dialect,
    dialect_ir_fields,
    is_default,
    iter_rows,
)

# ----------------------------------------------------------------------------
# Fixture builders (real data is NOT committed; CP932 bytes are synthesized)
# ----------------------------------------------------------------------------


def _write_cp932_xrd(path: Path, data_rows: int = 6) -> Path:
    """CP932, CRLF, tab-separated, one preamble line before the header."""
    lines = ["サンプル名: 試料A", "2θ (deg)\t強度 (cps)"]
    lines += [f"{10.0 + i * 0.02:.2f}\t{100 + i}" for i in range(data_rows)]
    path.write_bytes("\r\n".join(lines).encode("cp932") + b"\r\n")
    return path


def _write_icdd_card(path: Path, data_rows: int = 6) -> Path:
    """UTF-8 ICDD card: Key: value preamble, then a whitespace-separated table
    (consecutive spaces act as one delimiter)."""
    preamble = [
        "Name: Silicon",
        "Formula: Si",
        "Reference: Smith et al. (1998) J. Appl. Cryst.",
        "Wavelength: 1.5406",
    ]
    table = ["2theta      d      I    (hkl)"]
    table += [
        f"{28.4 + i:.3f}   {3.135 - i * 0.1:.3f}   {100 - i * 10}   (11{i})"
        for i in range(data_rows)
    ]
    path.write_text("\n".join(preamble + table) + "\n", encoding="utf-8")
    return path


def _write_clean_csv(path: Path, rows: int = 8) -> Path:
    lines = ["id,name,value"] + [f"{i},row{i},{i * 1.5}" for i in range(rows)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ----------------------------------------------------------------------------
# detect_dialect
# ----------------------------------------------------------------------------


def test_detect_cp932_tab_with_preamble(tmp_path: Path) -> None:
    p = _write_cp932_xrd(tmp_path / "xrd_measurement.txt")
    d = detect_dialect(p)
    assert d == SourceDialect(encoding="cp932", delimiter="\t", collapse=False, skip_rows=1)
    assert not is_default(d)


def test_detect_icdd_whitespace_card(tmp_path: Path) -> None:
    p = _write_icdd_card(tmp_path / "xrd_reference.txt")
    d = detect_dialect(p)
    assert d.encoding == "utf-8-sig"  # clean UTF-8 decodes on the first attempt
    assert d.delimiter == "whitespace"
    assert d.skip_rows == 4  # the header row follows the 4 preamble lines
    assert not is_default(d)


def test_detect_clean_csv_is_default(tmp_path: Path) -> None:
    p = _write_clean_csv(tmp_path / "clean.csv")
    assert is_default(detect_dialect(p))


def test_detect_quoted_json_cells_stay_default(tmp_path: Path) -> None:
    """Comma counting is quote-aware: JSON-array cells full of commas must not
    push the header offset (the starrydata curves.csv shape)."""
    lines = ["SID,x_json,y_json"]
    lines += [f'{i},"[300, 400]","[{i}, {i + 1}]"' for i in range(8)]
    p = tmp_path / "curves.csv"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert is_default(detect_dialect(p))


def test_detect_cp932_comma_csv_pins_encoding_only(tmp_path: Path) -> None:
    lines = ["id,物質名"] + [f"{i},材料{i}" for i in range(6)]
    p = tmp_path / "materials.csv"
    p.write_bytes("\n".join(lines).encode("cp932") + b"\n")
    d = detect_dialect(p)
    assert d == SourceDialect(encoding="cp932")


def test_detect_short_table_falls_back_to_default(tmp_path: Path) -> None:
    """A run under 5 rows is not a valid candidate — default (current behavior)."""
    p = _write_cp932_xrd(tmp_path / "short.txt", data_rows=2)
    assert is_default(detect_dialect(p))


def test_detect_utf16_bom(tmp_path: Path) -> None:
    lines = ["a\tb"] + [f"{i}\t{i}" for i in range(6)]
    p = tmp_path / "wide.txt"
    p.write_bytes("\n".join(lines).encode("utf-16"))  # writes the BOM
    d = detect_dialect(p)
    assert d.encoding == "utf-16"
    assert d.delimiter == "\t"
    assert d.skip_rows == 0


def test_detect_priority_prefers_comma_over_whitespace(tmp_path: Path) -> None:
    """When two candidates tie on run length and column count, the earlier
    candidate wins (comma > tab > semicolon > pipe > whitespace)."""
    lines = ["a,b c,d"] + [f"{i},x {i},y" for i in range(6)]
    p = tmp_path / "tie.csv"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    d = detect_dialect(p)
    # comma: constant 3 columns over all 7 lines; whitespace: constant 2 — the
    # higher column count wins even before priority is consulted, and priority
    # would break a true tie the same way.
    assert d.delimiter == ","
    assert d.skip_rows == 0


def test_detect_empty_file_is_default(tmp_path: Path) -> None:
    p = tmp_path / "empty.csv"
    p.write_text("", encoding="utf-8")
    assert is_default(detect_dialect(p))


# ----------------------------------------------------------------------------
# iter_rows
# ----------------------------------------------------------------------------


def test_iter_rows_cp932_tab(tmp_path: Path) -> None:
    p = _write_cp932_xrd(tmp_path / "xrd.txt", data_rows=4)
    rows = list(iter_rows(p, detect_dialect(p)))
    assert rows[0] == ["2θ (deg)", "強度 (cps)"]
    assert rows[1] == ["10.00", "100"]
    assert len(rows) == 5  # header + 4 data rows; the preamble is gone


def test_iter_rows_whitespace_collapses_runs(tmp_path: Path) -> None:
    p = _write_icdd_card(tmp_path / "card.txt", data_rows=4)
    rows = list(iter_rows(p, detect_dialect(p)))
    assert rows[0] == ["2theta", "d", "I", "(hkl)"]
    assert rows[1] == ["28.400", "3.135", "100", "(110)"]


def test_iter_rows_skips_blank_lines(tmp_path: Path) -> None:
    p = tmp_path / "gaps.txt"
    p.write_text("preamble\na\tb\n\n1\t2\n", encoding="utf-8")
    dialect = SourceDialect(delimiter="\t", skip_rows=1)
    assert list(iter_rows(p, dialect)) == [["a", "b"], ["1", "2"]]


def test_iter_rows_single_char_collapse_drops_empty_tokens(tmp_path: Path) -> None:
    p = tmp_path / "multi.txt"
    p.write_text("a;;b\n1;;2\n", encoding="utf-8")
    assert list(iter_rows(p, SourceDialect(delimiter=";", collapse=True))) == [
        ["a", "b"],
        ["1", "2"],
    ]
    assert list(iter_rows(p, SourceDialect(delimiter=";"))) == [
        ["a", "", "b"],
        ["1", "", "2"],
    ]


# ----------------------------------------------------------------------------
# describe / IR fields / overlay
# ----------------------------------------------------------------------------


def test_describe_dialect_lists_non_default_fields_only() -> None:
    d = SourceDialect(encoding="cp932", delimiter="\t", skip_rows=1)
    assert describe_dialect(d) == "encoding=cp932, delimiter=tab, skip_rows=1"
    assert describe_dialect(SourceDialect()) == "default"
    assert "whitespace" in describe_dialect(SourceDialect(delimiter="whitespace"))


def test_dialect_ir_fields_non_default_only() -> None:
    assert dialect_ir_fields(SourceDialect()) == {}
    assert dialect_ir_fields(SourceDialect(encoding="cp932", skip_rows=2)) == {
        "encoding": "cp932",
        "skip_rows": 2,
    }


_IR_DICT = {
    "version": 1,
    "prefixes": {"ex": "https://example.org/ns#"},
    "maps": [
        {"name": "point", "source": "xrd_measurement.txt"},
        {"name": "ref", "source": "xrd_reference.txt"},
    ],
}


def test_apply_detected_dialects_overlays_non_default() -> None:
    detected = {
        "xrd_measurement.txt": SourceDialect(encoding="cp932", delimiter="\t", skip_rows=1),
        "xrd_reference.txt": SourceDialect(),  # default — must not be pinned
        "unrelated.csv": SourceDialect(encoding="cp932"),  # no map reads it
    }
    out = apply_detected_dialects(_IR_DICT, detected)
    assert out["dialects"] == {
        "xrd_measurement.txt": {"encoding": "cp932", "delimiter": "\t", "skip_rows": 1}
    }
    assert "dialects" not in _IR_DICT  # input not mutated


def test_apply_detected_dialects_explicit_values_win() -> None:
    ir = dict(_IR_DICT)
    ir["dialects"] = {"xrd_measurement.txt": {"skip_rows": 3}}
    detected = {
        "xrd_measurement.txt": SourceDialect(encoding="cp932", delimiter="\t", skip_rows=1)
    }
    out = apply_detected_dialects(ir, detected)
    # detected fills encoding/delimiter; the human-gated skip_rows survives.
    assert out["dialects"]["xrd_measurement.txt"] == {
        "encoding": "cp932",
        "delimiter": "\t",
        "skip_rows": 3,
    }


def test_apply_detected_dialects_yaml_text_roundtrip() -> None:
    import pytest

    pytest.importorskip("yaml")
    ir_yaml = dedent(
        """
        version: 1
        prefixes:
          ex: "https://example.org/ns#"
        maps:
          - name: point
            source: xrd_measurement.txt
            subject:
              template: "ex:point/{2θ (deg)}"
            properties:
              - predicate: ex:angle
                column: "2θ (deg)"
        """
    )
    detected = {"xrd_measurement.txt": SourceDialect(encoding="cp932", delimiter="\t")}
    out = apply_detected_dialects(ir_yaml, detected)
    assert isinstance(out, str)
    assert "dialects:" in out
    assert "cp932" in out
    # nothing to add ⇒ byte-identical input text (a clean spec never churns)
    assert apply_detected_dialects(ir_yaml, {}) == ir_yaml
    assert (
        apply_detected_dialects(ir_yaml, {"xrd_measurement.txt": SourceDialect()}) == ir_yaml
    )
