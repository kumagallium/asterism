"""Source dialect (asterism.dialect): the runtime side of ADR ``source-dialect.md``.

These cover ``normalize_source`` on the two audited legacy shapes — a CP932,
CRLF, tab-separated XRD export with a preamble line, and a UTF-8 ICDD card whose
d-I table is whitespace-separated (consecutive spaces = one delimiter) — plus
the annotation round-trip (``dialects_from_mapping`` / ``strip_dialect_
annotations``) and the all-defaults gate. Real instrument files never enter the
repo: every fixture is synthesized (CP932 via ``str.encode``).
"""
from __future__ import annotations

from pathlib import Path

import pytest
import rdflib

from asterism.dialect import (
    DIALECT_PREDICATES,
    DialectAnnotationError,
    SourceDialect,
    dialect_rows,
    dialects_from_mapping,
    is_default,
    normalize_source,
    read_preamble,
    resolve_header,
    strip_dialect_annotations,
    strip_preamble_and_header,
)


def _read_rows(dest: Path) -> list[str]:
    return dest.read_text(encoding="utf-8").splitlines()


# ---- normalize_source --------------------------------------------------------


def test_normalize_cp932_tab_preamble(tmp_path: Path) -> None:
    """The audited XRD export shape: CP932, CRLF, tab-separated, one preamble
    line (sample name) before the header row → a clean 2-column UTF-8 CSV."""
    src = tmp_path / "xrd_measurement.txt"
    lines = ["サンプル名: 試料A", "2θ (deg)\t強度 (cps)", "10.02\t123", "10.04\t130"]
    src.write_bytes("\r\n".join(lines).encode("cp932") + b"\r\n")

    dest = normalize_source(
        src, SourceDialect(encoding="cp932", delimiter="\t", skip_rows=1), tmp_path / "out.csv"
    )

    assert _read_rows(dest) == ["2θ (deg),強度 (cps)", "10.02,123", "10.04,130"]


def test_normalize_whitespace_card(tmp_path: Path) -> None:
    """The audited ICDD card shape: ``Key: value`` preamble, then a
    whitespace-separated d-I table where runs of spaces act as ONE delimiter
    (Excel's "consecutive delimiters as one") → a 4-column CSV."""
    src = tmp_path / "xrd_reference.txt"
    src.write_text(
        "Name: Lead Telluride\n"
        "Formula: PbTe\n"
        "\n"
        "2theta    d       I    (hkl)\n"
        "27.556    3.2340  100  (200)\n"
        "39.408    2.2867   57  (220)\n",
        encoding="utf-8",
    )

    dest = normalize_source(
        src, SourceDialect(delimiter="whitespace", skip_rows=3), tmp_path / "out.csv"
    )

    assert _read_rows(dest) == [
        "2theta,d,I,(hkl)",
        "27.556,3.2340,100,(200)",
        "39.408,2.2867,57,(220)",
    ]


def test_normalize_collapse_single_char_delimiter(tmp_path: Path) -> None:
    # collapse: consecutive (and leading/trailing) delimiters yield no empty tokens.
    src = tmp_path / "d.txt"
    src.write_text("a;;b\n;x;y;\n", encoding="utf-8")

    dest = normalize_source(
        src, SourceDialect(delimiter=";", collapse=True), tmp_path / "out.csv"
    )

    assert _read_rows(dest) == ["a,b", "x,y"]


def test_normalize_drops_blank_lines(tmp_path: Path) -> None:
    src = tmp_path / "d.txt"
    src.write_text("h1\th2\n\n1\t2\n   \n3\t4\n", encoding="utf-8")

    dest = normalize_source(src, SourceDialect(delimiter="\t"), tmp_path / "out.csv")

    assert _read_rows(dest) == ["h1,h2", "1,2", "3,4"]


def test_normalize_strict_decode_is_a_real_error(tmp_path: Path) -> None:
    # ADR: decode is strict — a wrong pinned encoding must raise, never mangle.
    src = tmp_path / "d.txt"
    src.write_bytes(b"a\tb\n\xff\xff\t1\n")
    with pytest.raises(UnicodeDecodeError):
        normalize_source(src, SourceDialect(delimiter="\t"), tmp_path / "out.csv")


def test_dialect_rows_strips_cell_padding(tmp_path: Path) -> None:
    """C16: single-char cells are stripped — legacy exports pad cells for visual
    alignment; the normalized copy and the header checks must agree."""
    src = tmp_path / "padded.txt"
    src.write_text("angle ;  intensity \n 10.0 ; 100 \n", encoding="utf-8")
    assert list(dialect_rows(src, SourceDialect(delimiter=";"))) == [
        ["angle", "intensity"],
        ["10.0", "100"],
    ]


def test_dialect_rows_preserves_quoted_newline_cells(tmp_path: Path) -> None:
    """C3/C4: a quoted cell containing newlines (even a blank line) is ONE cell
    of ONE record — the physical-line filter used to swallow the blank line and
    split the record."""
    src = tmp_path / "notes.txt"
    src.write_text('preamble\nid,note\n1,"a\n\nb"\n2,plain\n', encoding="utf-8")
    assert list(dialect_rows(src, SourceDialect(skip_rows=1))) == [
        ["id", "note"],
        ["1", "a\n\nb"],
        ["2", "plain"],
    ]


def test_normalize_quoted_newline_round_trips(tmp_path: Path) -> None:
    # csv.writer re-quotes the embedded newline, so the normalized copy parses
    # back to the identical records.
    import csv as _csv

    src = tmp_path / "notes.txt"
    src.write_text('id;note\n1;"a\nb"\n', encoding="utf-8")
    dest = normalize_source(src, SourceDialect(delimiter=";"), tmp_path / "out.csv")
    with dest.open(encoding="utf-8", newline="") as fh:
        assert list(_csv.reader(fh)) == [["id", "note"], ["1", "a\nb"]]


def test_normalize_renames_reserved_header_columns(tmp_path: Path) -> None:
    """C6: Morph-KGC reserves ``subject``/``predicate`` DataFrame columns (a source
    column with either name silently yields 0 triples). The normalized copy renames
    them the same way the direct-CSV sanitizer does (asterism.tabularize.safe_col)."""
    src = tmp_path / "d.txt"
    src.write_text("subject\tpredicate\tvalue\na\tb\t1\n", encoding="utf-8")
    dest = normalize_source(src, SourceDialect(delimiter="\t"), tmp_path / "out.csv")
    assert _read_rows(dest) == ["subject_,predicate_,value", "a,b,1"]


# ---- strip_preamble_and_header (append accumulation, plan B) -------------------


def test_strip_preamble_and_header_cp932_tab_preamble() -> None:
    """The audited XRD shape: skip_rows=1 drops the preamble line AND the header
    row (skip_rows+1 physical lines), returning the native CP932/CRLF data bytes
    unchanged — decode-free, so a later batch appends only its new rows."""
    raw = "サンプル名: 試料A\r\n2θ (deg)\t強度 (cps)\r\n10.0\t123\r\n10.2\t456\r\n".encode("cp932")
    dialect = SourceDialect(encoding="cp932", delimiter="\t", skip_rows=1)
    assert strip_preamble_and_header(raw, dialect) == "10.0\t123\r\n10.2\t456\r\n".encode("cp932")


def test_strip_preamble_and_header_whitespace_skip_23() -> None:
    # A large preamble (ICDD card): skip_rows=23 drops 24 physical lines.
    preamble = "".join(f"Key{i}: v{i}\n" for i in range(23))
    raw = (preamble + "2theta d I (hkl)\n27.5 3.2 100 (200)\n").encode("utf-8")
    out = strip_preamble_and_header(raw, SourceDialect(delimiter="whitespace", skip_rows=23))
    assert out == b"27.5 3.2 100 (200)\n"


def test_strip_preamble_and_header_skip_rows_zero_drops_only_header() -> None:
    # A dialected CSV with no preamble (e.g. cp932 comma): skip_rows=0 drops 1 line.
    raw = b"h1,h2\n1,2\n3,4\n"
    assert strip_preamble_and_header(raw, SourceDialect(delimiter=",")) == b"1,2\n3,4\n"


def test_strip_preamble_and_header_header_only_yields_empty() -> None:
    # A batch with no data rows (only preamble+header) contributes nothing.
    raw = b"preamble\nh1\th2\n"
    assert strip_preamble_and_header(raw, SourceDialect(delimiter="\t", skip_rows=1)) == b""


def test_strip_preamble_and_header_fewer_lines_than_offset_yields_empty() -> None:
    raw = b"preamble\n"  # header offset not reached
    assert strip_preamble_and_header(raw, SourceDialect(delimiter="\t", skip_rows=1)) == b""


# ---- the all-defaults gate ----------------------------------------------------


def test_is_default_gate() -> None:
    assert is_default(SourceDialect())
    assert not is_default(SourceDialect(encoding="cp932"))
    assert not is_default(SourceDialect(delimiter="\t"))
    assert not is_default(SourceDialect(collapse=True))
    assert not is_default(SourceDialect(skip_rows=1))


# ---- annotation round-trip -----------------------------------------------------

_ANNOTATED_TTL = """
@prefix rr:  <http://www.w3.org/ns/r2rml#> .
@prefix rml: <http://w3id.org/rml/> .
@prefix ql:  <http://semweb.mmlab.be/ns/ql#> .
@prefix ast: <https://kumagallium.github.io/asterism/vocab#> .
<#M> a rr:TriplesMap ;
  rml:logicalSource [ rml:source "xrd_measurement.txt" ;
                      rml:referenceFormulation ql:CSV ;
                      ast:sourceEncoding "cp932" ;
                      ast:sourceDelimiter "\\t" ;
                      ast:sourceSkipRows 1 ] ;
  rr:subjectMap [ rr:template "https://ex/m/{angle}" ] .
"""


def test_dialects_from_mapping_reads_annotations() -> None:
    g = rdflib.Graph()
    g.parse(data=_ANNOTATED_TTL, format="turtle")
    assert dialects_from_mapping(g) == {
        "xrd_measurement.txt": SourceDialect(encoding="cp932", delimiter="\t", skip_rows=1)
    }


def test_dialects_from_mapping_legacy_namespace_and_collapse() -> None:
    # The legacy mmlab rml:source namespace and the collapse boolean both resolve.
    ttl = (
        "@prefix rml: <http://semweb.mmlab.be/ns/rml#> .\n"
        "@prefix ast: <https://kumagallium.github.io/asterism/vocab#> .\n"
        '<#M> rml:logicalSource [ rml:source "card.txt" ;\n'
        '  ast:sourceDelimiter "whitespace" ;\n'
        "  ast:sourceCollapse true ;\n"
        "  ast:sourceSkipRows 23 ] .\n"
    )
    g = rdflib.Graph()
    g.parse(data=ttl, format="turtle")
    assert dialects_from_mapping(g) == {
        "card.txt": SourceDialect(delimiter="whitespace", collapse=True, skip_rows=23)
    }


def test_unannotated_mapping_yields_no_dialects() -> None:
    ttl = (
        "@prefix rml: <http://w3id.org/rml/> .\n"
        '<#M> rml:logicalSource [ rml:source "papers.csv" ] .\n'
    )
    g = rdflib.Graph()
    g.parse(data=ttl, format="turtle")
    assert dialects_from_mapping(g) == {}


def _graph_with_annotation(fragment: str) -> rdflib.Graph:
    ttl = (
        "@prefix rml: <http://w3id.org/rml/> .\n"
        "@prefix ast: <https://kumagallium.github.io/asterism/vocab#> .\n"
        f'<#M> rml:logicalSource [ rml:source "d.txt" ; {fragment} ] .\n'
    )
    g = rdflib.Graph()
    g.parse(data=ttl, format="turtle")
    return g


def test_dialects_from_mapping_rejects_non_text_codecs() -> None:
    # C10: 'zip'/'base64' resolve via codecs.lookup but are bytes<->bytes codecs;
    # unknown names must be rejected too — never a 500 downstream.
    for bad in ("zip", "base64", "not-a-codec"):
        g = _graph_with_annotation(f'ast:sourceEncoding "{bad}"')
        with pytest.raises(DialectAnnotationError, match="text codec"):
            dialects_from_mapping(g)


def test_dialects_from_mapping_rejects_bad_delimiter() -> None:
    # C5: only a single character or the whitespace sentinel is a delimiter.
    g = _graph_with_annotation('ast:sourceDelimiter "||"')
    with pytest.raises(DialectAnnotationError, match="single character"):
        dialects_from_mapping(g)


def test_dialects_from_mapping_rejects_bad_skip_rows() -> None:
    # C11: a non-integer / negative skip_rows is a structured error, not a crash.
    for bad in ('"abc"', "-1"):
        g = _graph_with_annotation(f"ast:sourceSkipRows {bad}")
        with pytest.raises(DialectAnnotationError, match="non-negative"):
            dialects_from_mapping(g)


def test_dialects_from_mapping_rejects_bad_collapse() -> None:
    g = _graph_with_annotation('ast:sourceCollapse "maybe"')
    with pytest.raises(DialectAnnotationError, match="true or false"):
        dialects_from_mapping(g)


def test_strip_dialect_annotations_round_trip() -> None:
    g = rdflib.Graph()
    g.parse(data=_ANNOTATED_TTL, format="turtle")
    assert dialects_from_mapping(g)  # annotations present before the strip

    strip_dialect_annotations(g)

    assert dialects_from_mapping(g) == {}
    for pred in DIALECT_PREDICATES:
        assert next(g.objects(None, rdflib.URIRef(pred)), None) is None
    # Only the annotations were removed — the mapping itself is intact.
    src = next(g.objects(None, rdflib.URIRef("http://w3id.org/rml/source")))
    assert str(src) == "xrd_measurement.txt"


# ---- Header-metadata broadcast (runtime twin) --------------------------------

_CARD_PREAMBLE = [
    "No: 03-065-2664 ",
    "CSD: N AL1935    (NIST)",
    "Name: Aluminum Vanadium",
    "Cell:  3.7790  3.7790  8.3220  90.000  90.000  90.000",
    "Space Group: I4/mmm(139)",
    "---------------- Experiment",
    "Radiation: CuKa1  lambda: 1.54060",
    "Reference: O.N.Carlson,D.J.Kenney & H.A.Wilhelm Met.47(1955)520.",
    "---------------- Comment",
    "Additional Patterns: See PDF 03-065-5860.",
    "No e.s.d reported on the cell dimension.",
    "2theta range:   21.34 -  147.24",
]


def test_read_preamble_twin_parity_keyvalue() -> None:
    # The runtime twin must produce the SAME (name, value) pairs the design twin does.
    meta = dict(read_preamble(_CARD_PREAMBLE, "keyvalue"))
    assert meta["Radiation"] == "CuKa1  lambda: 1.54060"  # split on first colon only
    assert meta["Cell"] == "3.7790  3.7790  8.3220  90.000  90.000  90.000"  # 1 cell
    assert meta["Additional Patterns"] == (
        "See PDF 03-065-5860. No e.s.d reported on the cell dimension."
    )  # continuation appended
    assert "Experiment" not in meta and "Comment" not in meta  # sections skipped
    assert meta["2theta range"] == "21.34 -  147.24"


def test_read_preamble_twin_parity_lines_and_duplicates() -> None:
    assert read_preamble(["Al3V_bulk"], "lines") == [("preamble_1", "Al3V_bulk")]
    assert read_preamble(["K: 1", "K: 2"], "keyvalue") == [("K", "1"), ("K_2", "2")]


def test_resolve_header_twin_parity() -> None:
    assert resolve_header(["2theta", "d", "I"], ["d", "No"]) == ["d_2", "No"]
    assert resolve_header(["id"], ["subject", "predicate"]) == ["subject_", "predicate_"]


def test_dialect_rows_broadcast_lines(tmp_path: Path) -> None:
    src = tmp_path / "m.txt"
    lines = ["Al3V_bulk", "2theta\tintensity", "20.0\t3600", "20.02\t4233", "20.04\t4100"]
    src.write_bytes("\r\n".join(lines).encode("cp932") + b"\r\n")
    rows = list(
        dialect_rows(
            src, SourceDialect(encoding="cp932", delimiter="\t", skip_rows=1, preamble="lines")
        )
    )
    assert rows[0] == ["2theta", "intensity", "preamble_1"]
    assert rows[1:] == [
        ["20.0", "3600", "Al3V_bulk"],
        ["20.02", "4233", "Al3V_bulk"],
        ["20.04", "4100", "Al3V_bulk"],
    ]


def test_normalize_broadcast_keyvalue_card(tmp_path: Path) -> None:
    """The ICDD card, broadcast: the preamble metadata is appended AFTER the body
    columns and constant across every d-I row → a wide flat CSV morph-kgc reads."""
    src = tmp_path / "card.txt"
    body = [
        "2theta   d      I    (hkl)",
        "27.556   3.2340  100  (200)",
        "39.408   2.2867   57  (220)",
    ]
    src.write_text("\r\n".join([*_CARD_PREAMBLE, *body]) + "\r\n", encoding="utf-8")
    dest = normalize_source(
        src,
        SourceDialect(delimiter="whitespace", skip_rows=12, preamble="keyvalue"),
        tmp_path / "out.csv",
    )
    out = _read_rows(dest)
    header = out[0].split(",")
    assert header[:4] == ["2theta", "d", "I", "(hkl)"]
    assert "No" in header and "Cell" in header and "Space Group" in header
    # A value that contains commas is re-quoted by csv.writer (lossless).
    assert '"O.N.Carlson,D.J.Kenney & H.A.Wilhelm Met.47(1955)520."' in out[1]
    # 2 data rows, both carrying the constant No.
    no_col = header.index("No")
    assert all(row.split(",")[no_col] == "03-065-2664" for row in out[1:])
    assert len(out) == 1 + 2


def test_dialect_rows_drop_is_byte_identical(tmp_path: Path) -> None:
    # Default preamble=drop must be untouched by the broadcast machinery.
    src = tmp_path / "m.txt"
    lines = ["Al3V_bulk", "a\tb", "1\t2", "3\t4"]
    src.write_bytes("\r\n".join(lines).encode("cp932") + b"\r\n")
    base = SourceDialect(encoding="cp932", delimiter="\t", skip_rows=1)
    assert base.preamble == "drop"
    assert list(dialect_rows(src, base)) == [["a", "b"], ["1", "2"], ["3", "4"]]


def test_dialects_from_mapping_reads_preamble() -> None:
    g = rdflib.Graph()
    g.parse(
        data=(
            "@prefix rml: <http://w3id.org/rml/> .\n"
            "@prefix ast: <https://kumagallium.github.io/asterism/vocab#> .\n"
            '[] rml:source "card.txt" ; ast:sourceDelimiter "whitespace" ;\n'
            '   ast:sourceSkipRows 23 ; ast:sourcePreamble "keyvalue" .\n'
        ),
        format="turtle",
    )
    (d,) = dialects_from_mapping(g).values()
    assert d.preamble == "keyvalue" and d.skip_rows == 23 and d.delimiter == "whitespace"


def test_dialects_from_mapping_rejects_bad_preamble() -> None:
    g = rdflib.Graph()
    g.parse(
        data=(
            "@prefix rml: <http://w3id.org/rml/> .\n"
            "@prefix ast: <https://kumagallium.github.io/asterism/vocab#> .\n"
            '[] rml:source "card.txt" ; ast:sourcePreamble "sometimes" .\n'
        ),
        format="turtle",
    )
    with pytest.raises(DialectAnnotationError, match="sourcePreamble"):
        dialects_from_mapping(g)
