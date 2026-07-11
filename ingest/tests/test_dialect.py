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
    SourceDialect,
    dialects_from_mapping,
    is_default,
    normalize_source,
    strip_dialect_annotations,
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
