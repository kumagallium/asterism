"""0 件の回（空の配列・無いファイル）の取り込み — :mod:`asterism.empty_sources`.

種類ごとの JSON を 1 ファイル 1 map にした設計で、ある種類がその回 0 件でも
他の種類は普段どおり取り込める。決定: ``docs/architecture/native-json-denormalization.md`` §10。

* 中身が ``[]`` → 印なしで 0 件（その source を読む TriplesMap を外す）。
* ファイルが無い → 既定は従来どおり止める。``ast:sourceOptional true``
  （IR の ``optional: true``）があるときだけ 0 件。

後半は実 Morph-KGC（substrate extra）で通す。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from asterism.empty_sources import (
    SOURCE_OPTIONAL_PREDICATE,
    prune_empty_sources,
    zero_row_reason,
)
from asterism.rml_validate import RmlValidationError, validate_rml_design
from asterism.tabularize import is_empty_json_array, tabularize_json_to_csv


def _morph_kgc_installed() -> bool:
    try:
        import morph_kgc  # noqa: F401

        return True
    except ImportError:
        return False


needs_morph = pytest.mark.skipif(
    not _morph_kgc_installed(),
    reason="morph-kgc not installed; this exercises the real materialize",
)

EX = "https://example.org/ns#"
RES = "https://example.org/r/"
KINDS = ("judgments", "rules", "observations", "interpretations")


def _map(kind: str, *, optional: bool = False) -> str:
    """種類 1 つ分の TriplesMap（IR コンパイラが出す形）。"""
    cls = kind[:-1].capitalize()
    mark = " ;\n                      ast:sourceOptional true" if optional else ""
    return f"""
<#{cls}Map> a rr:TriplesMap ;
  rml:logicalSource [ rml:source "{kind}.csv" ; rml:referenceFormulation ql:CSV{mark} ] ;
  rr:subjectMap [ rr:template "{RES}{kind}/{{id}}" ; rr:class ex:{cls} ] ;
  rr:predicateObjectMap [ rr:predicate ex:text ;
    rr:objectMap [ rml:reference "text" ] ] .
"""


def _rml(*maps: str) -> str:
    return (
        "@prefix rr: <http://www.w3.org/ns/r2rml#> .\n"
        "@prefix rml: <http://semweb.mmlab.be/ns/rml#> .\n"
        "@prefix ql: <http://semweb.mmlab.be/ns/ql#> .\n"
        "@prefix ast: <https://kumagallium.github.io/asterism/vocab#> .\n"
        f"@prefix ex: <{EX}> .\n" + "".join(maps)
    )


def _export(tmp_path: Path, **records: list[dict] | None) -> Path:
    """種類ごとの JSON を書く。値が ``None`` の種類はファイルを作らない。"""
    for kind, rows in records.items():
        if rows is not None:
            (tmp_path / f"{kind}.json").write_text(json.dumps(rows), encoding="utf-8")
    return tmp_path


def _rows(kind: str, n: int = 2) -> list[dict]:
    return [{"id": f"{kind[0]}{i}", "text": f"{kind} {i}"} for i in range(1, n + 1)]


# ---- 空の配列の判定 ----------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (b"[]", True),
        (b"  [ \n\t ]\r\n", True),
        (b"\xef\xbb\xbf[]", True),
        (b'[{"id": "a"}]', False),
        (b"[1]", False),
        (b'{"data": []}', False),  # 包まれた空配列は 1 件の文書と区別できない（§10.5）
        (b"{}", False),
        (b"", False),
        (b"[] x", False),
    ],
)
def test_is_empty_json_array(tmp_path: Path, body: bytes, expected: bool) -> None:
    src = tmp_path / "x.json"
    src.write_bytes(body)
    assert is_empty_json_array(src) is expected


def test_is_empty_json_array_across_read_chunks(tmp_path: Path) -> None:
    """読み込みの区切り（64KiB）をまたぐ空白でも判定は変わらない。"""
    src = tmp_path / "x.json"
    src.write_bytes(b"[" + b" " * 200_000 + b"]\n")
    assert is_empty_json_array(src) is True
    src.write_bytes(b"[" + b" " * 200_000 + b"{}]")
    assert is_empty_json_array(src) is False


def test_tabularize_refuses_zero_records_and_writes_nothing(tmp_path: Path) -> None:
    src = tmp_path / "x.json"
    src.write_text("[]", encoding="utf-8")
    dest = tmp_path / "x.csv"
    with pytest.raises(ValueError, match="no records"):
        tabularize_json_to_csv(src, dest)
    assert not dest.exists()


def test_zero_row_reason(tmp_path: Path) -> None:
    (tmp_path / "empty.json").write_text("[]", encoding="utf-8")
    (tmp_path / "full.json").write_text('[{"id": "a"}]', encoding="utf-8")
    (tmp_path / "plain.csv").write_text("id\n", encoding="utf-8")
    assert zero_row_reason("empty.csv", tmp_path, optional=False) == "empty"
    assert zero_row_reason("empty.json", tmp_path, optional=False) == "empty"
    assert zero_row_reason("full.csv", tmp_path, optional=False) is None
    assert zero_row_reason("plain.csv", tmp_path, optional=True) is None
    assert zero_row_reason("absent.csv", tmp_path, optional=False) is None
    assert zero_row_reason("absent.csv", tmp_path, optional=True) == "missing"


# ---- 取り込みから外す（マッピングの編集） --------------------------------------


def test_prune_is_byte_identical_when_nothing_is_empty(tmp_path: Path) -> None:
    _export(tmp_path, judgments=_rows("judgments"), rules=_rows("rules"))
    rml = _rml(_map("judgments"), _map("rules"))
    assert prune_empty_sources(rml, tmp_path) == rml


def _sources_in(rml: str) -> set[str]:
    """マッピングに残る ``rml:source`` の値（孤児になったノードの分も数える）。"""
    import rdflib

    graph = rdflib.Graph().parse(data=rml, format="turtle")
    return {
        str(o)
        for pred in ("http://semweb.mmlab.be/ns/rml#source", "http://w3id.org/rml/source")
        for o in graph.objects(None, rdflib.URIRef(pred))
    }


def test_prune_drops_the_map_of_an_empty_array(tmp_path: Path) -> None:
    _export(tmp_path, judgments=_rows("judgments"), interpretations=[])
    out = prune_empty_sources(_rml(_map("judgments"), _map("interpretations")), tmp_path)
    assert out is not None
    assert _sources_in(out) == {"judgments.csv"}


def _named_source_map(kind: str, *, optional: bool = False) -> str:
    """logicalSource を名前つきノードで書いた手書き RML の形。"""
    cls = kind[:-1].capitalize()
    mark = " ;\n  ast:sourceOptional true" if optional else ""
    return f"""
<#{cls}Source> a rml:LogicalSource ;
  rml:source "{kind}.csv" ; rml:referenceFormulation ql:CSV{mark} .
<#{cls}Map> a rr:TriplesMap ;
  rml:logicalSource <#{cls}Source> ;
  rr:subjectMap [ rr:template "{RES}{kind}/{{id}}" ; rr:class ex:{cls} ] .
"""


def test_prune_removes_a_named_logical_source_with_its_map(tmp_path: Path) -> None:
    """名前つきの logicalSource も外す（残ると後段がその rml:source を拾って止まる）。"""
    _export(tmp_path, judgments=_rows("judgments"), rules=[])
    rml = _rml(
        _named_source_map("judgments"),
        _named_source_map("rules"),
        _named_source_map("interpretations", optional=True),
    )
    out = prune_empty_sources(rml, tmp_path)
    assert out is not None
    assert _sources_in(out) == {"judgments.csv"}


def test_prune_keeps_a_named_logical_source_another_map_still_reads(tmp_path: Path) -> None:
    _export(tmp_path, judgments=_rows("judgments"), rules=[])
    shared = f"""
<#SharedSource> a rml:LogicalSource ;
  rml:source "judgments.csv" ; rml:referenceFormulation ql:CSV .
<#AMap> a rr:TriplesMap ; rml:logicalSource <#SharedSource> ;
  rr:subjectMap [ rr:template "{RES}a/{{id}}" ; rr:class ex:A ] .
<#BMap> a rr:TriplesMap ; rml:logicalSource <#SharedSource> ;
  rr:subjectMap [ rr:template "{RES}b/{{id}}" ; rr:class ex:B ] .
"""
    out = prune_empty_sources(_rml(shared, _map("rules")), tmp_path)
    assert out is not None
    assert _sources_in(out) == {"judgments.csv"}


def test_prune_handles_the_w3id_rml_namespace(tmp_path: Path) -> None:
    _export(tmp_path, judgments=_rows("judgments"), interpretations=[])
    rml = _rml(_map("judgments"), _map("interpretations")).replace(
        "<http://semweb.mmlab.be/ns/rml#>", "<http://w3id.org/rml/>"
    )
    out = prune_empty_sources(rml, tmp_path)
    assert out is not None
    assert _sources_in(out) == {"judgments.csv"}


def test_prune_drops_an_absent_optional_source_and_strips_the_mark(tmp_path: Path) -> None:
    _export(tmp_path, judgments=_rows("judgments"), rules=_rows("rules"))
    rml = _rml(
        _map("judgments"),
        _map("rules", optional=True),
        _map("interpretations", optional=True),
    )
    out = prune_empty_sources(rml, tmp_path)
    assert out is not None
    assert "interpretations.csv" not in out
    assert "rules.csv" in out  # 印があってもファイルがあれば普段どおり読む
    assert "sourceOptional" not in out


def test_prune_keeps_an_absent_unmarked_source_for_the_design_check(tmp_path: Path) -> None:
    _export(tmp_path, judgments=_rows("judgments"))
    rml = _rml(_map("judgments"), _map("interpretations"))
    assert prune_empty_sources(rml, tmp_path) == rml


def test_prune_returns_none_when_no_map_is_left(tmp_path: Path) -> None:
    _export(tmp_path, judgments=[])
    rml = _rml(_map("judgments"), _map("rules", optional=True))
    assert prune_empty_sources(rml, tmp_path) is None


def test_prune_removes_a_join_to_a_dropped_parent(tmp_path: Path) -> None:
    """``rr:parentTriplesMap`` で外した map を参照する object map も外す
    （0 行の親との join は 0 トリプル — 結果は同じ）。"""
    _export(
        tmp_path,
        judgments=[{"id": "j1", "text": "a", "rule": "r1"}],
        rules=[],
    )
    join = f"""
<#JudgmentMap> a rr:TriplesMap ;
  rml:logicalSource [ rml:source "judgments.csv" ; rml:referenceFormulation ql:CSV ] ;
  rr:subjectMap [ rr:template "{RES}judgments/{{id}}" ] ;
  rr:predicateObjectMap [ rr:predicate ex:text ; rr:objectMap [ rml:reference "text" ] ] ;
  rr:predicateObjectMap [ rr:predicate ex:rule ;
    rr:objectMap [ rr:parentTriplesMap <#RuleMap> ;
      rr:joinCondition [ rr:child "rule" ; rr:parent "id" ] ] ] .
"""
    out = prune_empty_sources(_rml(join, _map("rules")), tmp_path)
    assert out is not None
    assert "parentTriplesMap" not in out
    assert "joinCondition" not in out
    assert "rules.csv" not in out
    assert f"{EX}text" in out or "ex:text" in out


def test_prune_drops_a_map_left_emitting_nothing(tmp_path: Path) -> None:
    """唯一の出力が外した親への join で class も無い map は、何も生まないので一緒に外す。"""
    _export(
        tmp_path,
        judgments=_rows("judgments"),
        links=[{"id": "l1", "rule": "r1"}],
        rules=[],
    )
    link = f"""
<#LinkMap> a rr:TriplesMap ;
  rml:logicalSource [ rml:source "links.csv" ; rml:referenceFormulation ql:CSV ] ;
  rr:subjectMap [ rr:template "{RES}links/{{id}}" ] ;
  rr:predicateObjectMap [ rr:predicate ex:rule ;
    rr:objectMap [ rr:parentTriplesMap <#RuleMap> ;
      rr:joinCondition [ rr:child "rule" ; rr:parent "id" ] ] ] .
"""
    out = prune_empty_sources(_rml(_map("judgments"), link, _map("rules")), tmp_path)
    assert out is not None
    assert _sources_in(out) == {"judgments.csv"}


# ---- 設計検証（取り込み前の 422） --------------------------------------------


def test_design_check_lets_an_absent_optional_source_through(tmp_path: Path) -> None:
    _export(tmp_path, judgments=_rows("judgments"))
    validate_rml_design(_rml(_map("judgments"), _map("interpretations", optional=True)), tmp_path)


def test_design_check_still_stops_an_absent_unmarked_source(tmp_path: Path) -> None:
    _export(tmp_path, judgments=_rows("judgments"))
    with pytest.raises(RmlValidationError) as exc:
        validate_rml_design(_rml(_map("judgments"), _map("interpretations")), tmp_path)
    assert any("interpretations.csv" in i for i in exc.value.issues)


def test_design_check_ignores_a_non_boolean_mark(tmp_path: Path) -> None:
    """``true`` 以外の値は印として扱わない（無いファイルは従来どおり止まる）。"""
    _export(tmp_path, judgments=_rows("judgments"))
    rml = _rml(_map("judgments"), _map("interpretations", optional=True)).replace(
        "ast:sourceOptional true", 'ast:sourceOptional "yes"'
    )
    assert SOURCE_OPTIONAL_PREDICATE.endswith("sourceOptional")
    with pytest.raises(RmlValidationError):
        validate_rml_design(rml, tmp_path)


def test_design_check_passes_an_empty_array_source(tmp_path: Path) -> None:
    _export(tmp_path, judgments=_rows("judgments"), interpretations=[])
    validate_rml_design(_rml(_map("judgments"), _map("interpretations")), tmp_path)


# ---- 実 Morph-KGC -------------------------------------------------------------


def _graph_triples(rml: str, csv_dir: Path) -> set[tuple[str, str, str]]:
    from asterism.substrate import materialize_to_graph

    graph = materialize_to_graph(rml, csv_dir, work_dir=csv_dir / "_work_graph")
    return {(str(s), str(p), str(o)) for s, p, o in graph}


def _nt_lines(rml: str, csv_dir: Path) -> list[str]:
    from asterism.substrate import materialize_to_nt_file

    out = materialize_to_nt_file(rml, csv_dir, work_dir=csv_dir / "_work_nt")
    return [ln for ln in out.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _subjects_of(triples: set[tuple[str, str, str]], kind: str) -> set[str]:
    return {s for s, _p, _o in triples if s.startswith(f"{RES}{kind}/")}


@needs_morph
def test_e2e_empty_array_source_does_not_stop_the_other_kinds(tmp_path: Path) -> None:
    """(a) 中身が ``[]`` の種類があっても、他の種類は取り込める（0 件の種類は 0 トリプル）。"""
    _export(
        tmp_path,
        judgments=_rows("judgments"),
        rules=_rows("rules", 1),
        observations=_rows("observations", 3),
        interpretations=[],
    )
    rml = _rml(*(_map(k) for k in KINDS))
    triples = _graph_triples(rml, tmp_path)
    assert _subjects_of(triples, "judgments") == {f"{RES}judgments/j1", f"{RES}judgments/j2"}
    assert len(_subjects_of(triples, "rules")) == 1
    assert len(_subjects_of(triples, "observations")) == 3
    assert _subjects_of(triples, "interpretations") == set()
    assert len(triples) == (2 + 1 + 3) * 2  # 1 行 = rdf:type + ex:text
    # 流し込みの経路（N-Triples ファイル）も同じ結果
    assert len(_nt_lines(rml, tmp_path)) == len(triples)
    # 列の無い空の表は作らない
    assert not list(tmp_path.glob("_work_*/interpretations.csv"))


@needs_morph
def test_e2e_absent_optional_source_does_not_stop_the_other_kinds(tmp_path: Path) -> None:
    """(b) 印（``ast:sourceOptional true``）のある map のファイルが無くても、他は取り込める。"""
    _export(
        tmp_path,
        judgments=_rows("judgments"),
        rules=_rows("rules", 1),
        observations=_rows("observations", 3),
        interpretations=None,
    )
    rml = _rml(*(_map(k, optional=(k == "interpretations")) for k in KINDS))
    triples = _graph_triples(rml, tmp_path)
    assert len(_subjects_of(triples, "judgments")) == 2
    assert len(_subjects_of(triples, "rules")) == 1
    assert len(_subjects_of(triples, "observations")) == 3
    assert _subjects_of(triples, "interpretations") == set()
    assert len(_nt_lines(rml, tmp_path)) == len(triples)


@needs_morph
def test_e2e_optional_source_present_is_read_as_usual(tmp_path: Path) -> None:
    """印はファイルが無いときだけ効く。あれば普段どおり読む。"""
    _export(tmp_path, judgments=_rows("judgments"), interpretations=_rows("interpretations", 1))
    rml = _rml(_map("judgments"), _map("interpretations", optional=True))
    triples = _graph_triples(rml, tmp_path)
    assert _subjects_of(triples, "interpretations") == {f"{RES}interpretations/i1"}


@needs_morph
def test_e2e_absent_unmarked_source_still_stops(tmp_path: Path) -> None:
    """(c) 印が無ければ従来どおり止まる（名前の誤り・書き出し漏れを黙って 0 件にしない）。"""
    from asterism.substrate import materialize_to_graph, materialize_to_nt_file

    _export(
        tmp_path,
        judgments=_rows("judgments"),
        rules=_rows("rules", 1),
        observations=_rows("observations", 3),
        interpretations=None,
    )
    rml = _rml(*(_map(k) for k in KINDS))
    for materialize in (materialize_to_graph, materialize_to_nt_file):
        with pytest.raises(RmlValidationError) as exc:
            materialize(rml, tmp_path)
        assert any("interpretations.csv" in i for i in exc.value.issues)


@needs_morph
def test_e2e_every_source_empty_yields_zero_triples(tmp_path: Path) -> None:
    """全部の種類が 0 件なら Morph-KGC を呼ばずに 0 トリプル。"""
    _export(tmp_path, judgments=[], rules=None)
    rml = _rml(_map("judgments"), _map("rules", optional=True))
    assert _graph_triples(rml, tmp_path) == set()
    assert _nt_lines(rml, tmp_path) == []


@needs_morph
def test_e2e_join_to_an_empty_parent_keeps_the_child(tmp_path: Path) -> None:
    """外した親への join があっても、子の map の他のトリプルは取り込める。"""
    _export(tmp_path, judgments=[{"id": "j1", "text": "a", "rule": "r1"}], rules=[])
    join = f"""
<#JudgmentMap> a rr:TriplesMap ;
  rml:logicalSource [ rml:source "judgments.csv" ; rml:referenceFormulation ql:CSV ] ;
  rr:subjectMap [ rr:template "{RES}judgments/{{id}}" ] ;
  rr:predicateObjectMap [ rr:predicate ex:text ; rr:objectMap [ rml:reference "text" ] ] ;
  rr:predicateObjectMap [ rr:predicate ex:rule ;
    rr:objectMap [ rr:parentTriplesMap <#RuleMap> ;
      rr:joinCondition [ rr:child "rule" ; rr:parent "id" ] ] ] .
"""
    triples = _graph_triples(_rml(join, _map("rules")), tmp_path)
    assert triples == {(f"{RES}judgments/j1", f"{EX}text", "a")}


@needs_morph
def test_e2e_named_logical_sources(tmp_path: Path) -> None:
    """手書き RML の名前つき logicalSource でも、``[]`` と印のある欠落を外して他を取り込む。"""
    _export(tmp_path, judgments=_rows("judgments"), rules=[])
    rml = _rml(
        _named_source_map("judgments"),
        _named_source_map("rules"),
        _named_source_map("interpretations", optional=True),
    )
    triples = _graph_triples(rml, tmp_path)
    assert _subjects_of(triples, "judgments") == {f"{RES}judgments/j1", f"{RES}judgments/j2"}
    assert len(triples) == 2
    assert len(_nt_lines(rml, tmp_path)) == 2


@needs_morph
def test_e2e_map_left_emitting_nothing_does_not_crash(tmp_path: Path) -> None:
    _export(
        tmp_path,
        judgments=_rows("judgments"),
        links=[{"id": "l1", "rule": "r1"}],
        rules=[],
    )
    link = f"""
<#LinkMap> a rr:TriplesMap ;
  rml:logicalSource [ rml:source "links.csv" ; rml:referenceFormulation ql:CSV ] ;
  rr:subjectMap [ rr:template "{RES}links/{{id}}" ] ;
  rr:predicateObjectMap [ rr:predicate ex:rule ;
    rr:objectMap [ rr:parentTriplesMap <#RuleMap> ;
      rr:joinCondition [ rr:child "rule" ; rr:parent "id" ] ] ] .
"""
    triples = _graph_triples(_rml(_map("judgments"), link, _map("rules")), tmp_path)
    assert len(_subjects_of(triples, "judgments")) == 2
    assert _subjects_of(triples, "links") == set()
