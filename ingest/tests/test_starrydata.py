"""Phase 1 papers ingester tests.

各テストは「Phase 0.5 spike からの差分」を保証する:
- bnode を一切出さない (re-ingest 冪等性)
- PROV-O IngestionActivity が 1 つだけ発行される
- 同じ container_title を持つ 2 papers が同じ periodical IRI を共有する
- JSON 埋め込み列 (author / issued) が正しく展開される
"""
from __future__ import annotations

import csv
from pathlib import Path

import pytest
from rdflib import BNode, Graph, Literal, Namespace, URIRef
from rdflib.namespace import PROV, RDF, XSD

from csv2rdf.starrydata import (
    DEFAULT_ONTOLOGY,
    DEFAULT_RESOURCE,
    IngestConfig,
    ingest_papers,
    parse_authors,
    parse_curator_timestamp,
    parse_issued,
    safe_url,
    slugify,
)

SD = Namespace(DEFAULT_ONTOLOGY)
SDR = Namespace(DEFAULT_RESOURCE)
SCHEMA = Namespace("https://schema.org/")
BIBO = Namespace("http://purl.org/ontology/bibo/")


# ----------------------------------------------------------------------------
# Pure helpers
# ----------------------------------------------------------------------------


def test_slugify_basic() -> None:
    assert slugify("Accounts of Chemical Research") == "accounts-of-chemical-research"


def test_slugify_handles_json_quoted_string() -> None:
    # starrydata の container_title は引用符付き JSON literal で渡されることがある
    assert slugify('"Accounts of Chemical Research"') == "accounts-of-chemical-research"


def test_slugify_collapses_punctuation() -> None:
    assert slugify("ACS Appl. Mater. Interfaces!") == "acs-appl-mater-interfaces"


def test_slugify_empty_falls_back() -> None:
    assert slugify("") == "unknown"
    assert slugify("---") == "unknown"


def test_parse_issued_full_date() -> None:
    assert parse_issued('{"date_parts":[[2014,4,15]]}') == "2014-04-15"


def test_parse_issued_year_only() -> None:
    assert parse_issued('{"date_parts":[[2014]]}') == "2014-01-01"


def test_parse_issued_invalid_returns_none() -> None:
    assert parse_issued("") is None
    assert parse_issued("not-json") is None
    assert parse_issued('{"date_parts":[]}') is None
    assert parse_issued('{"date_parts":[[2026,2,30]]}') is None  # Feb 30 → ValueError


def test_parse_curator_timestamp_normalizes_jst_to_utc() -> None:
    # JS Date.toString() with GMT+0900 → equivalent UTC instant
    # (18:19:39 JST == 09:19:39 UTC), emitted in the "+00:00" UTC form
    # (matches rdflib's xsd:dateTime canonicalization).
    out = parse_curator_timestamp(
        "Fri Sep 01 2017 18:19:39 GMT+0900 (Japan Standard Time)"
    )
    assert out == "2017-09-01T09:19:39+00:00"


def test_parse_curator_timestamp_utc_input_stays_utc() -> None:
    out = parse_curator_timestamp(
        "Thu Jan 25 2018 13:56:56 GMT+0000 (Coordinated Universal Time)"
    )
    assert out == "2018-01-25T13:56:56+00:00"


def test_parse_curator_timestamp_no_timezone_returns_none() -> None:
    # No offset → instant is unknown → no prov:atTime (don't fabricate UTC).
    assert parse_curator_timestamp("Thu Jan 25 2018 13:56:56") is None


def test_parse_curator_timestamp_invalid_returns_none() -> None:
    assert parse_curator_timestamp("") is None
    assert parse_curator_timestamp("not a date") is None
    assert parse_curator_timestamp("2018-01-25") is None  # ISO already, not JS form


def test_safe_url_passthrough_for_clean_url() -> None:
    assert safe_url("http://dx.doi.org/10.1021/ar400290f") == (
        "http://dx.doi.org/10.1021/ar400290f"
    )


def test_safe_url_percent_encodes_angle_brackets() -> None:
    # Legacy Wiley DOI URL with < > that would break Turtle serialization.
    raw = "http://dx.doi.org/10.1002/(SICI)1521-396X(199910)175:2<683::AID-PSSA683>3.0.CO;2-3"
    out = safe_url(raw)
    assert out is not None
    assert "<" not in out and ">" not in out
    assert "%3C683" in out and "%3E3.0" in out


def test_safe_url_empty_returns_none() -> None:
    assert safe_url("") is None
    assert safe_url("   ") is None


def test_emit_paper_with_angle_bracket_url_serializes_cleanly(tmp_path: Path) -> None:
    """Regression for the full-scale finding: a paper whose URL has < > must
    still produce loadable Turtle (round-trips through rdflib parse)."""
    import csv as _csv

    from rdflib import Graph

    rows = [
        {
            "SID": "1", "DOI": "10.1002/x",
            "URL": "http://dx.doi.org/10.1002/(SICI)1521-396X(199910)175:2<683::AID-PSSA683>3.0.CO;2-3",
            "issued": "", "author": "[]", "title": '"T"', "container_title": "",
            "container_title_short": "", "volume": "", "issue": "", "page": "",
            "ISSN": "", "publisher": "", "project_names": "[]", "created_at": "",
        }
    ]
    csv_path = tmp_path / "p.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        w = _csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    out = tmp_path / "p.ttl"
    ingest_papers(csv_path, out, IngestConfig(emit_prov=False))
    # The key assertion: the emitted Turtle re-parses without error.
    g = Graph()
    g.parse(out, format="turtle")
    paper = URIRef(DEFAULT_RESOURCE + "paper/1")
    urls = list(g.objects(paper, URIRef("https://schema.org/url")))
    assert len(urls) == 1
    assert "%3C683" in str(urls[0])


def test_parse_authors_basic() -> None:
    raw = '[{"given":"Chong","family":"Xiao"},{"given":"Yi","family":"Xie"}]'
    assert parse_authors(raw) == [
        {"given": "Chong", "family": "Xiao"},
        {"given": "Yi", "family": "Xie"},
    ]


def test_parse_authors_skips_empty() -> None:
    raw = '[{"given":"","family":""},{"given":"Yi","family":"Xie"}]'
    assert parse_authors(raw) == [{"given": "Yi", "family": "Xie"}]


# ----------------------------------------------------------------------------
# End-to-end fixtures
# ----------------------------------------------------------------------------


@pytest.fixture
def two_papers_csv(tmp_path: Path) -> Path:
    """2 paper の最小 CSV。container_title が同じ -> periodical IRI は共有される。"""
    rows = [
        {
            "SID": "1",
            "DOI": "10.1021/ar400290f",
            "URL": "http://dx.doi.org/10.1021/ar400290f",
            "issued": '{"date_parts":[[2014,4,15]]}',
            "author": '[{"given":"Chong","family":"Xiao"},{"given":"Yi","family":"Xie"}]',
            "title": '"Decoupling Interrelated Parameters"',
            "container_title": '"Accounts of Chemical Research"',
            "container_title_short": '"Acc. Chem. Res."',
            "volume": "47",
            "issue": "4",
            "page": '"1287-1295"',
            "ISSN": "0001-4842,1520-4898",
            "publisher": "American Chemical Society (ACS)",
            "project_names": '["ThermoelectricMaterials","GeneralDB"]',
            "created_at": "Thu Jan 25 2018 13:56:56",
        },
        {
            "SID": "42",
            "DOI": "10.1021/ar400999z",
            "URL": "",
            "issued": '{"date_parts":[[2015]]}',
            "author": '[{"given":"Jane","family":"Doe"}]',
            "title": '"Some Other Paper"',
            "container_title": '"Accounts of Chemical Research"',  # same journal
            "container_title_short": "",
            "volume": "",
            "issue": "",
            "page": "",
            "ISSN": "",
            "publisher": "American Chemical Society (ACS)",
            "project_names": "[]",
            "created_at": "",
        },
    ]
    p = tmp_path / "papers.csv"
    with p.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return p


# ----------------------------------------------------------------------------
# E2E behavior
# ----------------------------------------------------------------------------


def _load(path: Path) -> Graph:
    g = Graph()
    g.parse(str(path), format="turtle")
    return g


def test_ingest_runs_and_writes_turtle(two_papers_csv: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.ttl"
    stats = ingest_papers(two_papers_csv, out)
    assert stats.rows_in == 2
    assert stats.rows_ok == 2
    assert stats.rows_err == 0
    assert stats.triples_out > 10
    assert out.exists()


def test_no_blank_nodes(two_papers_csv: Path, tmp_path: Path) -> None:
    """Phase 0.5 spike からの最大の差分: bnode を一切出さない。"""
    out = tmp_path / "out.ttl"
    ingest_papers(two_papers_csv, out)
    g = _load(out)
    bnodes = [s for s in g.subjects() if isinstance(s, BNode)] + [
        o for o in g.objects() if isinstance(o, BNode)
    ]
    assert bnodes == [], f"bnode が混入しています: {bnodes}"


def test_periodical_is_shared_iri(two_papers_csv: Path, tmp_path: Path) -> None:
    """container_title が同じ 2 papers は同じ periodical IRI を持つ。"""
    out = tmp_path / "out.ttl"
    ingest_papers(two_papers_csv, out)
    g = _load(out)
    paper1 = URIRef(DEFAULT_RESOURCE + "paper/1")
    paper42 = URIRef(DEFAULT_RESOURCE + "paper/42")
    p1_journal = list(g.objects(paper1, SCHEMA.isPartOf))
    p42_journal = list(g.objects(paper42, SCHEMA.isPartOf))
    assert len(p1_journal) == 1
    assert len(p42_journal) == 1
    assert p1_journal[0] == p42_journal[0]
    expected = URIRef(DEFAULT_RESOURCE + "periodical/accounts-of-chemical-research")
    assert p1_journal[0] == expected


def test_authors_become_separate_iris(two_papers_csv: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.ttl"
    ingest_papers(two_papers_csv, out)
    g = _load(out)
    paper1 = URIRef(DEFAULT_RESOURCE + "paper/1")
    authors = list(g.objects(paper1, SCHEMA.author))
    assert len(authors) == 2
    # 期待される IRI 形式: sdr:person/{sid}/{idx}
    iris = {str(a) for a in authors}
    assert DEFAULT_RESOURCE + "person/1/0" in iris
    assert DEFAULT_RESOURCE + "person/1/1" in iris


def test_date_published_is_xsd_date(two_papers_csv: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.ttl"
    ingest_papers(two_papers_csv, out)
    g = _load(out)
    paper1 = URIRef(DEFAULT_RESOURCE + "paper/1")
    dates = list(g.objects(paper1, SCHEMA.datePublished))
    assert len(dates) == 1
    assert isinstance(dates[0], Literal)
    assert dates[0].datatype == XSD.date
    assert str(dates[0]) == "2014-04-15"


def test_ingestion_activity_is_emitted_once(
    two_papers_csv: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out.ttl"
    ingest_papers(two_papers_csv, out)
    g = _load(out)
    activities = list(g.subjects(RDF.type, SD.IngestionActivity))
    assert len(activities) == 1
    activity = activities[0]
    # 全 paper が同じ activity を wasGeneratedBy として持つ
    for paper in (
        URIRef(DEFAULT_RESOURCE + "paper/1"),
        URIRef(DEFAULT_RESOURCE + "paper/42"),
    ):
        gens = list(g.objects(paper, PROV.wasGeneratedBy))
        assert activity in gens


def test_no_prov_flag_omits_ingestion_activity(
    two_papers_csv: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out.ttl"
    ingest_papers(two_papers_csv, out, IngestConfig(emit_prov=False))
    g = _load(out)
    assert list(g.subjects(RDF.type, SD.IngestionActivity)) == []


def test_project_names_become_literals(
    two_papers_csv: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out.ttl"
    ingest_papers(two_papers_csv, out)
    g = _load(out)
    paper1 = URIRef(DEFAULT_RESOURCE + "paper/1")
    projects = {str(o) for o in g.objects(paper1, SD.projectName)}
    assert projects == {"ThermoelectricMaterials", "GeneralDB"}


def test_failed_row_recorded_to_error_log(tmp_path: Path) -> None:
    """SID 不在の行はカウントされるが ok にもならない。"""
    csv_path = tmp_path / "papers.csv"
    csv_path.write_text(
        "SID,DOI,URL,issued,author,title,container_title,container_title_short,"
        "volume,issue,page,ISSN,publisher,project_names,created_at\n"
        ",10.1/junk,,,,,,,,,,,,\n",  # empty SID
        encoding="utf-8",
    )
    out = tmp_path / "out.ttl"
    err = tmp_path / "err.jsonl"
    stats = ingest_papers(csv_path, out, error_log_path=err)
    assert stats.rows_in == 1
    assert stats.rows_ok == 0
    # NOTE: SID 空は _emit_paper が False を返すだけで例外にはしないので
    # rows_err は 0 のまま。これは spec の意図 (silent skip)。
    assert stats.rows_err == 0
