"""Phase 1 samples + curves ingester tests."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from rdflib import BNode, Graph, Literal, Namespace, URIRef
from rdflib.namespace import DCTERMS, PROV, RDF, XSD

from csv2rdf.starrydata import (
    DEFAULT_ONTOLOGY,
    DEFAULT_RESOURCE,
    IngestConfig,
    ingest_curves,
    ingest_samples,
    parse_float_array,
    parse_sample_info,
)

SD = Namespace(DEFAULT_ONTOLOGY)
SDR = Namespace(DEFAULT_RESOURCE)
SCHEMA = Namespace("https://schema.org/")
QK = Namespace("http://qudt.org/vocab/quantitykind/")
QUNIT = Namespace("http://qudt.org/vocab/unit/")


# ----------------------------------------------------------------------------
# Pure helpers
# ----------------------------------------------------------------------------


def test_parse_sample_info_strips_empty_entries() -> None:
    raw = json.dumps(
        {
            "MaterialFamily": {"category": "Bi2Te3", "comment": "", "extracted": ""},
            "GrainSize": {"category": "", "comment": "", "extracted": ""},
            "Form": {"category": "Film", "comment": "thin film"},
        }
    )
    out = parse_sample_info(raw)
    assert "MaterialFamily" in out
    assert "Form" in out
    assert "GrainSize" not in out, "全フィールド空のエントリは除外する"
    assert out["MaterialFamily"]["category"] == "Bi2Te3"
    assert out["Form"]["comment"] == "thin film"


def test_parse_sample_info_invalid_returns_empty() -> None:
    assert parse_sample_info("") == {}
    assert parse_sample_info("not-json") == {}
    assert parse_sample_info("[]") == {}


def test_parse_float_array_basic() -> None:
    assert parse_float_array("[1.5, 2.0, 3.25]") == [1.5, 2.0, 3.25]


def test_parse_float_array_handles_garbage() -> None:
    # 数値でないもの, None, NaN を取り除く
    assert parse_float_array('[1, "x", null, 2]') == [1.0, 2.0]
    assert parse_float_array("[]") == []
    assert parse_float_array("bad") == []
    assert parse_float_array("") == []


# ----------------------------------------------------------------------------
# samples ingester
# ----------------------------------------------------------------------------


@pytest.fixture
def samples_csv(tmp_path: Path) -> Path:
    rows = [
        {
            "sample_name": '"PH1000"',
            "sample_id": "16786",
            "composition": '"PEDOT:PSS with DMSO"',
            "composition_details": '"Bi2Te3 ball-milled into PEDOT:PSS"',
            "SID": "2",  # → paper/2
            "DOI": "10.1021/am100654p",
            "created_at": "Wed May 29 2019",
            "updated_at": "Wed May 29 2019",
            "sample_info": json.dumps(
                {
                    "MaterialFamily": {"category": "Bi2Te3", "comment": ""},
                    "GrainSize": {"category": "", "comment": "", "extracted": ""},
                    "Form": {"category": "Film", "comment": "thin film"},
                }
            ),
        },
        {
            "sample_name": '"Cu2.025Cd0.975SnSe4"',
            "sample_id": "6027",
            "composition": '"Pb1Te1.01Na0.02"',
            "composition_details": "",
            "SID": "1",
            "DOI": "10.1021/ar400290f",
            "created_at": "",
            "updated_at": "",
            "sample_info": "{}",
        },
    ]
    p = tmp_path / "samples.csv"
    with p.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return p


def _load(path: Path) -> Graph:
    g = Graph()
    g.parse(str(path), format="turtle")
    return g


def test_ingest_samples_emits_basic_triples(samples_csv: Path, tmp_path: Path) -> None:
    out = tmp_path / "samples.ttl"
    stats = ingest_samples(samples_csv, out, IngestConfig(emit_prov=False))
    assert stats.rows_in == 2
    assert stats.rows_ok == 2
    assert stats.rows_err == 0
    g = _load(out)
    # 複合 IRI: sample/{paper_sid}-{sample_id}
    s = URIRef(DEFAULT_RESOURCE + "sample/2-16786")
    assert (s, RDF.type, SD.Sample) in g
    assert (s, RDF.type, PROV.Entity) in g
    assert (s, DCTERMS.identifier, Literal("2-16786")) in g
    # raw sample_id も保持
    assert (s, SD.rawSampleId, Literal("16786")) in g
    assert (s, SCHEMA.name, Literal("PH1000")) in g
    assert (s, SD.compositionString, Literal("PEDOT:PSS with DMSO")) in g
    # fromPaper link
    assert (s, SD.fromPaper, URIRef(DEFAULT_RESOURCE + "paper/2")) in g


def test_samples_have_no_bnodes(samples_csv: Path, tmp_path: Path) -> None:
    out = tmp_path / "samples.ttl"
    ingest_samples(samples_csv, out, IngestConfig(emit_prov=False))
    g = _load(out)
    bnodes = [s for s in g.subjects() if isinstance(s, BNode)] + [
        o for o in g.objects() if isinstance(o, BNode)
    ]
    assert bnodes == []


def test_samples_descriptor_iris(samples_csv: Path, tmp_path: Path) -> None:
    """sample_info の descriptor は sdr:descriptor/{paper_sid}-{sample_id}/{idx} の IRI を持つ"""
    out = tmp_path / "samples.ttl"
    ingest_samples(samples_csv, out, IngestConfig(emit_prov=False))
    g = _load(out)
    s = URIRef(DEFAULT_RESOURCE + "sample/2-16786")
    descriptors = list(g.objects(s, SD.hasDescriptor))
    assert len(descriptors) == 2  # MaterialFamily, Form (GrainSize は除外)
    assert all(str(d).startswith(DEFAULT_RESOURCE + "descriptor/2-16786/") for d in descriptors)
    # descriptor 内容の検証
    names = {str(o) for d in descriptors for o in g.objects(d, SD.descriptorName)}
    assert names == {"MaterialFamily", "Form"}


# ----------------------------------------------------------------------------
# curves ingester
# ----------------------------------------------------------------------------


@pytest.fixture
def curves_csv(tmp_path: Path) -> Path:
    rows = [
        {
            "SID": "6",
            "DOI": "10.1021/am405410e",
            "composition": '"Pb1.00025Zn0.02Te1.02I0.0005"',
            "sample_id": "113",
            "figure_id": "79",
            "figure_name": '"6(b)"',
            "prop_x": '"Temperature"',
            "prop_y": '"Seebeck coefficient"',
            "unit_x": '"K"',
            "unit_y": '"V*K^(-1)"',
            "x": "[300, 400, 500, 600, 650]",
            "y": "[-0.0001, -0.0002, -0.0003, -0.0004, -0.00035]",
            "created_at": "",
            "updated_at": "",
            "project_names": '["ThermoelectricMaterials"]',
            "comments": '"figure (b)"',
        },
        {
            # Empty x/y to verify graceful handling
            "SID": "7",
            "DOI": "",
            "composition": '"Bi2Te3"',
            "sample_id": "114",
            "figure_id": "80",
            "figure_name": '"7"',
            "prop_x": '"Temperature"',
            "prop_y": '"Resistivity"',
            "unit_x": '"K"',
            "unit_y": '"ohm*cm"',
            "x": "",
            "y": "",
            "created_at": "",
            "updated_at": "",
            "project_names": "[]",
            "comments": "",
        },
    ]
    p = tmp_path / "curves.csv"
    with p.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return p


def test_ingest_curves_emits_basic_triples(curves_csv: Path, tmp_path: Path) -> None:
    out = tmp_path / "curves.ttl"
    stats = ingest_curves(curves_csv, out, IngestConfig(emit_prov=False))
    assert stats.rows_in == 2
    assert stats.rows_ok == 2
    g = _load(out)
    c79 = URIRef(DEFAULT_RESOURCE + "curve/6-79-113")  # SID=6 / figure=79 / sample=113
    assert (c79, RDF.type, SD.Curve) in g
    assert (c79, RDF.type, PROV.Entity) in g
    assert (c79, SD.figureName, Literal("6(b)")) in g
    # ofSample link は paper-sample 複合 IRI を指す
    assert (c79, SD.ofSample, URIRef(DEFAULT_RESOURCE + "sample/6-113")) in g
    # raw figure_id も保持
    assert (c79, SD.rawFigureId, Literal("79")) in g
    # complex identifier
    assert (c79, DCTERMS.identifier, Literal("6-79-113")) in g
    assert (c79, SD.propertyX, Literal("Temperature")) in g
    assert (c79, SD.propertyY, Literal("Seebeck coefficient")) in g
    assert (c79, SD.unitXString, Literal("K")) in g
    assert (c79, SD.unitYString, Literal("V*K^(-1)")) in g


def test_curves_emit_qudt_iris_when_mapped(curves_csv: Path, tmp_path: Path) -> None:
    """Phase 2 #2: mapped properties/units gain additive QUDT IRI triples,
    while the original string predicates are preserved (backward compatible)."""
    out = tmp_path / "curves.ttl"
    ingest_curves(curves_csv, out, IngestConfig(emit_prov=False))
    g = _load(out)
    c79 = URIRef(DEFAULT_RESOURCE + "curve/6-79-113")

    # QUDT quantity-kind IRIs (additive)
    assert (c79, SD.propertyYQuantity, QK.SeebeckCoefficient) in g
    assert (c79, SD.propertyXQuantity, QK.Temperature) in g
    # QUDT unit IRIs (additive)
    assert (c79, SD.unitY, QUNIT["V-PER-K"]) in g
    assert (c79, SD.unitX, QUNIT.K) in g
    # original string predicates still present
    assert (c79, SD.propertyY, Literal("Seebeck coefficient")) in g
    assert (c79, SD.unitYString, Literal("V*K^(-1)")) in g


def test_curves_partial_qudt_mapping(curves_csv: Path, tmp_path: Path) -> None:
    """Second fixture row: 'Resistivity' maps but 'ohm*cm' does not (only ohm*m
    is in the curated map) — so quantity IRI is emitted, unit IRI is not."""
    out = tmp_path / "curves.ttl"
    ingest_curves(curves_csv, out, IngestConfig(emit_prov=False))
    g = _load(out)
    c80 = URIRef(DEFAULT_RESOURCE + "curve/7-80-114")
    assert (c80, SD.propertyYQuantity, QK.Resistivity) in g
    # ohm*cm is intentionally unmapped -> no unitY IRI at all for this curve
    assert list(g.objects(c80, SD.unitY)) == []
    # but the string form is retained
    assert (c80, SD.unitYString, Literal("ohm*cm")) in g


def test_curves_aggregates(curves_csv: Path, tmp_path: Path) -> None:
    """設計プラン §4 方針 C: x/y 集約値 (Min/Max/PointCount) を出す"""
    out = tmp_path / "curves.ttl"
    ingest_curves(curves_csv, out, IngestConfig(emit_prov=False))
    g = _load(out)
    c79 = URIRef(DEFAULT_RESOURCE + "curve/6-79-113")  # SID=6 / figure=79 / sample=113
    x_min = list(g.objects(c79, SD.xMin))
    x_max = list(g.objects(c79, SD.xMax))
    y_min = list(g.objects(c79, SD.yMin))
    y_max = list(g.objects(c79, SD.yMax))
    point_count = list(g.objects(c79, SD.pointCount))
    assert len(x_min) == 1 and float(x_min[0]) == 300.0
    assert len(x_max) == 1 and float(x_max[0]) == 650.0
    # Y min/max: with negatives, min = -0.0004, max = -0.0001
    assert len(y_min) == 1 and float(y_min[0]) == pytest.approx(-0.0004)
    assert len(y_max) == 1 and float(y_max[0]) == pytest.approx(-0.0001)
    assert len(point_count) == 1 and int(point_count[0]) == 5
    # xsd:double / xsd:integer のデータ型確認
    assert x_min[0].datatype == XSD.double
    assert point_count[0].datatype == XSD.integer


def test_curves_keep_json_literal(curves_csv: Path, tmp_path: Path) -> None:
    """生 JSON 配列は xsd:string literal として保持する (方針 C)"""
    out = tmp_path / "curves.ttl"
    ingest_curves(curves_csv, out, IngestConfig(emit_prov=False))
    g = _load(out)
    c79 = URIRef(DEFAULT_RESOURCE + "curve/6-79-113")  # SID=6 / figure=79 / sample=113
    x_vals = list(g.objects(c79, SD.xValuesJSON))
    y_vals = list(g.objects(c79, SD.yValuesJSON))
    assert len(x_vals) == 1
    assert "300" in str(x_vals[0]) and "650" in str(x_vals[0])
    assert len(y_vals) == 1
    assert "-0.0004" in str(y_vals[0])


def test_curves_empty_xy_no_aggregates(curves_csv: Path, tmp_path: Path) -> None:
    """x/y が空の curve は集約値を出さない (Curve 自体は生成)"""
    out = tmp_path / "curves.ttl"
    ingest_curves(curves_csv, out, IngestConfig(emit_prov=False))
    g = _load(out)
    c80 = URIRef(DEFAULT_RESOURCE + "curve/7-80-114")
    assert (c80, RDF.type, SD.Curve) in g
    assert list(g.objects(c80, SD.xMin)) == []
    assert list(g.objects(c80, SD.pointCount)) == []


def test_curves_have_no_bnodes(curves_csv: Path, tmp_path: Path) -> None:
    out = tmp_path / "curves.ttl"
    ingest_curves(curves_csv, out, IngestConfig(emit_prov=False))
    g = _load(out)
    bnodes = [s for s in g.subjects() if isinstance(s, BNode)] + [
        o for o in g.objects() if isinstance(o, BNode)
    ]
    assert bnodes == []


# ----------------------------------------------------------------------------
# Regression: composite IRI for sample / curve
# ----------------------------------------------------------------------------


def test_curve_iri_disambiguates_same_figure_different_samples(tmp_path: Path) -> None:
    """同じ figure_id でも sample_id が違えば別の curve IRI になる。

    starrydata では 1 つの figure に複数 sample の曲線が重ねて描かれることがあり、
    その場合 CSV 上は figure_id 一致 + sample_id 別 + y 配列別 で複数行になる。
    旧来 sdr:curve/{figure_id} だと 6 行が 1 IRI に collapse して y 値が上書き
    されていた (実観測バグ)。
    """
    rows = [
        {**_base_curve_row(),
         "SID": "61", "figure_id": "20485", "sample_id": "25267",
         "composition": '"Bi2Te3"', "y": "[-0.00010,-0.00011]"},
        {**_base_curve_row(),
         "SID": "61", "figure_id": "20485", "sample_id": "25269",
         "composition": '"Ag0.04Bi2Te3"', "y": "[-0.00012,-0.00013]"},
        {**_base_curve_row(),
         "SID": "61", "figure_id": "20485", "sample_id": "25271",
         "composition": '"Ag0.07Bi2Te3"', "y": "[-0.00014,-0.00015]"},
    ]
    p = tmp_path / "curves.csv"
    with p.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    out = tmp_path / "curves.ttl"
    stats = ingest_curves(p, out, IngestConfig(emit_prov=False))
    assert stats.rows_ok == 3
    g = _load(out)
    # 3 つの curve IRI が独立して存在する
    curves = list(g.subjects(RDF.type, SD.Curve))
    assert len(curves) == 3, f"figure_id collapse の回帰: {len(curves)} curves found"
    iris = sorted(str(c) for c in curves)
    assert iris == [
        DEFAULT_RESOURCE + "curve/61-20485-25267",
        DEFAULT_RESOURCE + "curve/61-20485-25269",
        DEFAULT_RESOURCE + "curve/61-20485-25271",
    ]
    # ofSample がそれぞれ違う sample を指す
    for curve_iri, sample_id in zip(iris, ["25267", "25269", "25271"], strict=True):
        ofsample = list(g.objects(URIRef(curve_iri), SD.ofSample))
        assert ofsample == [URIRef(DEFAULT_RESOURCE + f"sample/61-{sample_id}")]


def test_sample_iri_disambiguates_same_sample_id_different_papers(
    tmp_path: Path,
) -> None:
    """sample_id 単独はグローバルユニークではない (実測 9,661 重複)。
    {paper_sid}-{sample_id} の複合キーで別 sample になることを assert。"""
    rows = [
        {"sample_name": '"sample-A"', "sample_id": "6027", "composition": '"Bi2Te3"',
         "composition_details": "", "SID": "1", "DOI": "", "created_at": "",
         "updated_at": "", "sample_info": "{}"},
        # 別 paper、同じ sample_id 6027
        {"sample_name": '"sample-B"', "sample_id": "6027", "composition": '"PbTe"',
         "composition_details": "", "SID": "2", "DOI": "", "created_at": "",
         "updated_at": "", "sample_info": "{}"},
    ]
    p = tmp_path / "samples.csv"
    with p.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    out = tmp_path / "samples.ttl"
    ingest_samples(p, out, IngestConfig(emit_prov=False))
    g = _load(out)
    samples = sorted(str(s) for s in g.subjects(RDF.type, SD.Sample))
    assert samples == [
        DEFAULT_RESOURCE + "sample/1-6027",
        DEFAULT_RESOURCE + "sample/2-6027",
    ]
    # それぞれの composition が混ざらない
    s1 = URIRef(DEFAULT_RESOURCE + "sample/1-6027")
    s2 = URIRef(DEFAULT_RESOURCE + "sample/2-6027")
    assert list(g.objects(s1, SD.compositionString)) == [Literal("Bi2Te3")]
    assert list(g.objects(s2, SD.compositionString)) == [Literal("PbTe")]


def _base_curve_row() -> dict[str, str]:
    """fixtures で使い回す最小 curve 行のデフォルト値。"""
    return {
        "SID": "1", "DOI": "", "composition": "", "sample_id": "1",
        "figure_id": "1", "figure_name": "", "prop_x": '"Temperature"',
        "prop_y": '"Seebeck coefficient"', "unit_x": '"K"', "unit_y": '"V*K^(-1)"',
        "x": "[300,400]", "y": "[-0.0001,-0.0002]",
        "created_at": "", "updated_at": "", "project_names": "[]", "comments": "",
    }
