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
    detect_preamble_form,
    dialect_ir_fields,
    is_default,
    iter_rows,
    read_preamble,
    resolve_header,
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
    (consecutive spaces act as one delimiter). The (hkl) cells carry commas —
    the real card's shape — so a comma candidate exists (the d-I rows split
    into a constant 3 comma-tokens) and whitespace must win on run length."""
    preamble = [
        "Name: Silicon",
        "Formula: Si",
        "Reference: Smith et al. (1998) J. Appl. Cryst.",
        "Wavelength: 1.5406",
    ]
    table = ["2theta      d      I    (hkl)"]
    table += [
        f"{28.4 + i:.3f}   {3.135 - i * 0.1:.3f}   {100 - i * 10}   (1,1,{i})"
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
# detect_dialect false positives (adversarial-review regressions)
# ----------------------------------------------------------------------------


def test_detect_interior_blank_line_stays_default(tmp_path: Path) -> None:
    """C1/S1: ONE blank line inside a clean comma CSV must not shift skip_rows
    (the rows above the blank line would silently vanish at ingest)."""
    lines = ["id,name,value"] + [f"{i},row{i},{i}" for i in range(3)]
    lines += [""] + [f"{i},row{i},{i}" for i in range(3, 9)]
    p = tmp_path / "gap.csv"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert is_default(detect_dialect(p))


def test_detect_quoted_newline_cells_stay_default(tmp_path: Path) -> None:
    """C1/S2: quoted cells containing newlines are ONE logical record — physical
    line counting must not misalign the run and invent a skip_rows."""
    lines = ["id,note"] + [f'{i},"line one\nline two {i}"' for i in range(8)]
    p = tmp_path / "notes.csv"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert is_default(detect_dialect(p))


def test_detect_single_column_two_word_values_stays_default(tmp_path: Path) -> None:
    """C14/S3: a clean 1-column CSV whose values happen to be two words must not
    be hijacked by the whitespace candidate (skip_rows=1 would drop the header
    and split every value)."""
    lines = ["comment"] + [f"word{i} word{i + 1}" for i in range(8)]
    p = tmp_path / "comments.csv"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert is_default(detect_dialect(p))


def test_detect_preamble_ws_width_coincidence_prefers_tab(tmp_path: Path) -> None:
    """C7: a preamble line whose whitespace token count coincidentally matches
    the table's column count extends the whitespace run past the real header —
    same column count means the whitespace split is a structural artifact of the
    tab table, so tab (with the correct skip_rows) must win."""
    lines = ["sample: A", "angle\tintensity"] + [f"{10 + i}.0\t{100 + i}" for i in range(6)]
    p = tmp_path / "xrd.txt"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    d = detect_dialect(p)
    assert d.delimiter == "\t"
    assert d.skip_rows == 1


def test_detect_comma_cells_with_spaces_stay_default(tmp_path: Path) -> None:
    """C12: a clean comma CSV whose every cell contains spaces must not let the
    whitespace candidate win on column count."""
    lines = ["full name,home city"] + [f"First{i} Last{i},City {i}" for i in range(8)]
    p = tmp_path / "people.csv"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert is_default(detect_dialect(p))


def test_detect_pure_whitespace_table_no_preamble(tmp_path: Path) -> None:
    """A preamble-free whitespace table (no single-char candidate) still detects
    — the subordination rules must not kill the legitimate case."""
    lines = ["a b c"] + [f"{i} {i + 1} {i + 2}" for i in range(6)]
    p = tmp_path / "table.txt"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    d = detect_dialect(p)
    assert d.delimiter == "whitespace"
    assert d.skip_rows == 0


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
    assert rows[1] == ["28.400", "3.135", "100", "(1,1,0)"]


def test_iter_rows_skips_blank_lines(tmp_path: Path) -> None:
    p = tmp_path / "gaps.txt"
    p.write_text("preamble\na\tb\n\n1\t2\n", encoding="utf-8")
    dialect = SourceDialect(delimiter="\t", skip_rows=1)
    assert list(iter_rows(p, dialect)) == [["a", "b"], ["1", "2"]]


def test_iter_rows_strips_cell_padding(tmp_path: Path) -> None:
    """C16: single-char cells are stripped — legacy exports pad cells for visual
    alignment, and the normalized copy / header checks must agree on the name."""
    p = tmp_path / "padded.txt"
    p.write_text("angle ;  intensity \n 10.0 ; 100 \n", encoding="utf-8")
    assert list(iter_rows(p, SourceDialect(delimiter=";"))) == [
        ["angle", "intensity"],
        ["10.0", "100"],
    ]


def test_iter_rows_preserves_quoted_newline_cells(tmp_path: Path) -> None:
    """C3: a quoted cell containing a newline (even a blank line) is ONE cell of
    ONE record — the physical-line reader used to split it and drop the blank."""
    p = tmp_path / "notes.txt"
    p.write_text('preamble\nid,note\n1,"a\n\nb"\n2,plain\n', encoding="utf-8")
    assert list(iter_rows(p, SourceDialect(skip_rows=1))) == [
        ["id", "note"],
        ["1", "a\n\nb"],
        ["2", "plain"],
    ]


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


def test_dialect_ir_fields_full_emits_all_four_fields() -> None:
    # FIX2: an override-derived source emits ALL fields (defaults included) so an
    # explicit-default (e.g. skip_rows corrected 1→0, or preamble reset to drop) is
    # authoritative and survives the materialize re-pin. A default field is otherwise
    # omitted (indistinguishable from "unset"), so re-detection would silently refill it.
    assert dialect_ir_fields(SourceDialect(encoding="cp932", delimiter="\t"), full=True) == {
        "encoding": "cp932",
        "delimiter": "\t",
        "collapse": False,
        "skip_rows": 0,
        "preamble": "drop",
    }
    # full=False is the byte-identical default (detection-only sources stay minimal).
    assert dialect_ir_fields(SourceDialect(encoding="cp932", delimiter="\t")) == {
        "encoding": "cp932",
        "delimiter": "\t",
    }


def test_apply_detected_dialects_full_fields_survives_repin() -> None:
    """FIX2 end-to-end at the pure-function seam: an override source keeps cp932/tab but
    corrects skip_rows 1→0. With ``full_fields`` the §9 entry pins skip_rows:0 explicitly,
    so a later re-detection (the materialize re-pin) whose ``entry.update(prior)`` merges
    the re-detected skip_rows=1 UNDER the explicit prior keeps the human's 0."""
    override = {
        "xrd_measurement.txt": SourceDialect(encoding="cp932", delimiter="\t", skip_rows=0)
    }
    pinned = apply_detected_dialects(_IR_DICT, override, full_fields={"xrd_measurement.txt"})
    assert pinned["dialects"]["xrd_measurement.txt"] == {
        "encoding": "cp932",
        "delimiter": "\t",
        "collapse": False,
        "skip_rows": 0,
        "preamble": "drop",
    }
    # Re-pin: source_dir re-detection yields skip_rows=1 (detection-only, minimal fields);
    # the explicit prior must win so the header offset does not silently revert to 1.
    redetected = {
        "xrd_measurement.txt": SourceDialect(encoding="cp932", delimiter="\t", skip_rows=1)
    }
    repinned = apply_detected_dialects(pinned, redetected)
    assert repinned["dialects"]["xrd_measurement.txt"]["skip_rows"] == 0


def test_apply_detected_dialects_full_fields_only_named_sources() -> None:
    # A detection-only source NOT in full_fields stays minimal (byte-equivalence for the
    # non-overridden sources of the same design); a default override is still gated out.
    detected = {
        "xrd_measurement.txt": SourceDialect(encoding="cp932", delimiter="\t", skip_rows=1),
        "xrd_reference.txt": SourceDialect(delimiter="whitespace", skip_rows=2),
    }
    out = apply_detected_dialects(_IR_DICT, detected, full_fields={"xrd_measurement.txt"})
    assert out["dialects"]["xrd_measurement.txt"] == {
        "encoding": "cp932",
        "delimiter": "\t",
        "collapse": False,
        "skip_rows": 1,
        "preamble": "drop",
    }
    assert out["dialects"]["xrd_reference.txt"] == {"delimiter": "whitespace", "skip_rows": 2}


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


# ----------------------------------------------------------------------------
# Header-metadata broadcast (ADR source-dialect.md, "Header metadata")
# ----------------------------------------------------------------------------

# The 23-line ICDD reference-card preamble (the audited real shape): key:value
# pairs, section headings (--- runs), a double-colon value, a 6-value cell, a
# wrapped (continuation) comment, and a value that itself contains a hyphen.
_CARD_PREAMBLE = [
    "No: 03-065-2664 ",
    "CSD: N AL1935    (NIST)",
    "Name: Aluminum Vanadium",
    "Chemical Formula: Al3 V",
    "Formula: Al3 V",
    "Z value: 2",
    "Space Group: I4/mmm(139)",
    "Cell:  3.7790  3.7790  8.3220  90.000  90.000  90.000",
    "Volume:  118.845",
    "Crystal System: Tetragonal",
    "Quality: I",
    "RIR(I/Ic):    5.05",
    "Subfile: Inorganic, Alloy&Metal, NIST Pattern",
    "---------------- Experiment",
    "Radiation: CuKa1  lambda: 1.54060",
    "Reference: O.N.Carlson,D.J.Kenney & H.A.Wilhelm Trans. Am. Soc. Met.47(1955)520.",
    "---------------- Physical",
    "Dcalc:  3.686",
    "---------------- Comment",
    "Additional Patterns: See PDF 03-065-5860. Minor Warning: ",
    "No e.s.d reported/abstracted on the cell dimension.",
    "---------------- d-I list (2theta are calculated with wavelength=1.54056)",
    "2theta range:   21.34 -  147.24",
]


def test_read_preamble_lines_measurement() -> None:
    # A bare sample-name preamble line becomes one metadata column.
    assert read_preamble(["Al3V_bulk"], "lines") == [("preamble_1", "Al3V_bulk")]
    # Blank physical lines are skipped, but the 1-based index tracks the original row.
    assert read_preamble(["", "Al3V_bulk", ""], "lines") == [("preamble_2", "Al3V_bulk")]


def test_read_preamble_keyvalue_card_edge_cases() -> None:
    meta = dict(read_preamble(_CARD_PREAMBLE, "keyvalue"))
    # Section headings (--- runs) are skipped, keys are top-level flattened.
    assert "Experiment" not in meta and "Physical" not in meta and "Comment" not in meta
    # Split on the FIRST colon only — the second colon stays inside the value.
    assert meta["Radiation"] == "CuKa1  lambda: 1.54060"
    # A multi-value cell is kept whole (Tier-0 splits it later, not the dialect).
    assert meta["Cell"] == "3.7790  3.7790  8.3220  90.000  90.000  90.000"
    # A value with commas / hyphen is preserved verbatim.
    assert meta["Reference"].startswith("O.N.Carlson,D.J.Kenney")
    assert meta["2theta range"] == "21.34 -  147.24"
    # A continuation line (no colon) is appended to the previous key's value.
    assert meta["Additional Patterns"] == (
        "See PDF 03-065-5860. Minor Warning: No e.s.d reported/abstracted on the cell dimension."
    )
    # No duplicate keys in this card, so ordering/count is lossless.
    assert len(read_preamble(_CARD_PREAMBLE, "keyvalue")) == 18


def test_read_preamble_duplicate_keys_suffixed() -> None:
    # A repeated key is suffixed key_2/key_3 (lossless, never overwritten).
    out = read_preamble(["Peak: 10", "Peak: 20", "Peak: 30"], "keyvalue")
    assert out == [("Peak", "10"), ("Peak_2", "20"), ("Peak_3", "30")]


def test_read_preamble_continuation_without_prior_key() -> None:
    # A leading continuation line with no prior key falls back to preamble_{i+1}.
    assert read_preamble(["orphan text", "K: v"], "keyvalue") == [
        ("preamble_1", "orphan text"),
        ("K", "v"),
    ]


def test_read_preamble_colon_free_continuation_rejoins() -> None:
    # A colon-FREE wrapped line rejoins the previous key's value losslessly
    # (the ICDD ``Additional Patterns`` case).
    out = read_preamble(["Additional Patterns: See PDF", "03-065-5860 for more"], "keyvalue")
    assert out == [("Additional Patterns", "See PDF 03-065-5860 for more")]


def test_read_preamble_colon_bearing_continuation_splits_documented() -> None:
    # DOCUMENTED deterministic limitation (adversarial review 2026-07-12): a
    # wrapped free-text note whose CONTINUATION line itself contains a colon —
    # the real ``Al.txt`` card's multi-line ``Comment`` block — cannot be told
    # from a real new field, so it splits into its own column instead of
    # rejoining. The text is never lost; it lands in an oddly-named column the
    # designer does not map. This pins that behavior so the claim stays honest.
    out = read_preamble(
        [
            "ANX: N. ... Original Remarks: Cell",
            "for Al-filings: 4.04920(5). ... Unit",
            "Cell Data Source: Single Crystal.",
        ],
        "keyvalue",
    )
    assert out == [
        ("ANX", "N. ... Original Remarks: Cell"),
        ("for Al-filings", "4.04920(5). ... Unit"),
        ("Cell Data Source", "Single Crystal."),
    ]


def test_read_preamble_drop_and_unknown_mode_yield_nothing() -> None:
    assert read_preamble(["K: v"], "drop") == []
    assert read_preamble(["K: v"], "bogus") == []


def test_resolve_header_collisions_and_reserved_columns() -> None:
    # A meta name colliding with a body column is suffixed on the META side only.
    assert resolve_header(["2theta", "d", "I"], ["d", "No"]) == ["d_2", "No"]
    # A reserved column name (subject/predicate) is renamed via safe_column.
    assert resolve_header(["id"], ["subject", "predicate"]) == ["subject_", "predicate_"]
    # Two meta names that collide with each other get progressive suffixes.
    assert resolve_header(["a"], ["m", "m"]) == ["m", "m_2"]


def test_iter_rows_broadcast_lines() -> None:
    # The measurement shape: one preamble line broadcast onto every body row.
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        p = _write_cp932_xrd(Path(d) / "m.txt", data_rows=3)
        dialect = SourceDialect(encoding="cp932", delimiter="\t", skip_rows=1, preamble="lines")
        rows = list(iter_rows(p, dialect))
    assert rows[0] == ["2θ (deg)", "強度 (cps)", "preamble_1"]
    # The preamble value (the sample name) is constant across every data row.
    assert all(r[-1] == "サンプル名: 試料A" for r in rows[1:])
    assert len(rows) == 1 + 3


def test_iter_rows_broadcast_keyvalue_dedupes_and_links() -> None:
    p = _write_icdd_card_full(Path(_tmpdir()) / "card.txt", data_rows=5)
    dialect = SourceDialect(
        encoding="utf-8-sig", delimiter="whitespace", skip_rows=23, preamble="keyvalue"
    )
    rows = list(iter_rows(p, dialect))
    header = rows[0]
    # Body columns keep their exact positions; the meta columns follow.
    assert header[:4] == ["2theta", "d", "I", "(hkl)"]
    assert "No" in header and "Cell" in header and "Space Group" in header
    # 5 data rows, each carrying the constant card metadata.
    assert len(rows) == 1 + 5
    no_idx = header.index("No")
    assert {r[no_idx] for r in rows[1:]} == {"03-065-2664"}


def test_iter_rows_drop_is_byte_identical_to_today(tmp_path: Path) -> None:
    # The drop path (default) must be untouched by the broadcast machinery.
    p = _write_cp932_xrd(tmp_path / "m.txt", data_rows=4)
    base = SourceDialect(encoding="cp932", delimiter="\t", skip_rows=1)
    assert base.preamble == "drop"
    assert list(iter_rows(p, base)) == [
        ["2θ (deg)", "強度 (cps)"],
        *[[f"{10.0 + i * 0.02:.2f}", f"{100 + i}"] for i in range(4)],
    ]


def test_detect_preamble_form_classifies_shape() -> None:
    assert detect_preamble_form(["Al3V_bulk"]) == "lines"
    assert detect_preamble_form(_CARD_PREAMBLE) == "keyvalue"
    assert detect_preamble_form([]) is None
    assert detect_preamble_form(["", "   "]) is None


def test_describe_and_ir_fields_carry_preamble() -> None:
    d = SourceDialect(delimiter="whitespace", skip_rows=23, preamble="keyvalue")
    assert "preamble=keyvalue" in describe_dialect(d)
    assert dialect_ir_fields(d) == {
        "delimiter": "whitespace",
        "skip_rows": 23,
        "preamble": "keyvalue",
    }
    # Default preamble is never emitted (byte-equivalence for a clean dialect).
    assert "preamble" not in dialect_ir_fields(SourceDialect(delimiter="\t"))


def _tmpdir() -> str:
    import tempfile

    d = tempfile.mkdtemp()
    return d


def _write_icdd_card_full(path: Path, data_rows: int = 5) -> Path:
    """The full 23-line ICDD card preamble + a whitespace d-I table (real shape)."""
    header = "   2theta     d        I      (hkl)"
    table = [
        f"  {21.34 + i:.2f}   {4.161 - i * 0.1:.3f}   {5.0 + i:.1f}  (0,0,{i})"
        for i in range(data_rows)
    ]
    path.write_text("\r\n".join([*_CARD_PREAMBLE, header, *table]) + "\r\n", encoding="utf-8")
    return path
