"""データで答える 4 つの形（値で絞る・〜ごと・推移・結合 1 段）のテンプレート
（design.md §3・``shared_vocab.default_cq_tools``）。

ストアは本物の ``rdflib.Dataset``（既定グラフ = 全グラフの和 = FROM-merge と同じ見え方）。
データセットの種類・項目は ``sv:`` の語へ**線**（subClassOf / subPropertyOf）で結ばれ、
その線が答えを作る。ハブのグラフは数えない。
"""

# ruff: noqa: RUF001
from __future__ import annotations

import json

import pytest
import rdflib
import yaml

from asterism import shared_vocab as sv
from asterism import substrate, themes
from asterism.crosswalk_runtime import ALIGNMENT_GRAPH
from asterism.query_tools import lint_query_tool, parse_query_tools
from asterism.shared_vocab import CQSpec, SharedTerm, TermError

AT = "2026-10-21T00:00:00+00:00"
RDF_TYPE = rdflib.RDF.type
NS = "https://example.org/starry#"
NS2 = "https://example.org/other#"


def _term(slug: str, label: str, kind: str = "class") -> SharedTerm:
    return SharedTerm(sv.SV + slug, slug, kind, label, None, None, AT)


def _u(s: str) -> rdflib.URIRef:
    return rdflib.URIRef(s)


def _store() -> rdflib.Dataset:
    """試料 3 件・測定曲線 3 件・論文 2 件（starry）＋ 別のデータセット（other）に試料 1 件。"""
    ds = rdflib.Dataset(default_union=True)
    g = ds.graph(_u(substrate.versioned_graph_iri("starry", 1)))
    for s, comp in (("s1", "SnSe"), ("s2", "SnSe0.9S0.1"), ("s3", "PbTe")):
        g.add((_u(f"urn:{s}"), RDF_TYPE, _u(NS + "Sample")))
        g.add((_u(f"urn:{s}"), _u(NS + "composition"), rdflib.Literal(comp)))
    for c, s, t, z in (("c1", "s1", 300, -10), ("c2", "s1", 400, -20), ("c3", "s2", 300, -5)):
        g.add((_u(f"urn:{c}"), RDF_TYPE, _u(NS + "Curve")))
        g.add((_u(f"urn:{c}"), _u(NS + "temperature"), rdflib.Literal(t)))
        g.add((_u(f"urn:{c}"), _u(NS + "seebeck"), rdflib.Literal(z)))
        g.add((_u(f"urn:{c}"), _u(NS + "ofSample"), _u(f"urn:{s}")))
    for p, ss in (("p1", ("s1", "s2")), ("p2", ("s3",))):
        g.add((_u(f"urn:{p}"), RDF_TYPE, _u(NS + "Paper")))
        for s in ss:
            g.add((_u(f"urn:{p}"), _u(NS + "reports"), _u(f"urn:{s}")))
    other = ds.graph(_u(substrate.versioned_graph_iri("other", 1)))
    other.add((_u("urn:o1"), RDF_TYPE, _u(NS2 + "Specimen")))
    other.add((_u("urn:o1"), _u(NS2 + "formula"), rdflib.Literal("snse")))
    # ハブ（交差の台帳）には同じ形があっても、データセットとして数えない
    hub = ds.graph(_u(substrate.canonical_graph_iri("crosswalk")))
    hub.add((_u("urn:h1"), RDF_TYPE, _u(NS + "Sample")))
    hub.add((_u("urn:h1"), _u(NS + "composition"), rdflib.Literal("SnSe")))
    # 線: データセットの語 → 共有の語（上位パス）
    a = ds.graph(_u(ALIGNMENT_GRAPH))
    for ds_iri, slug, rel in (
        (NS + "Sample", "sample", rdflib.RDFS.subClassOf),
        (NS2 + "Specimen", "sample", rdflib.RDFS.subClassOf),
        (NS + "Curve", "curve", rdflib.RDFS.subClassOf),
        (NS + "Paper", "paper", rdflib.RDFS.subClassOf),
        (NS + "composition", "composition", rdflib.RDFS.subPropertyOf),
        (NS2 + "formula", "composition", rdflib.RDFS.subPropertyOf),
        (NS + "temperature", "temperature", rdflib.RDFS.subPropertyOf),
        (NS + "seebeck", "seebeck", rdflib.RDFS.subPropertyOf),
        (NS + "reports", "reports", rdflib.RDFS.subPropertyOf),
    ):
        a.add((_u(ds_iri), rel, _u(sv.SV + slug)))
    return ds


def _run(ds: rdflib.Dataset, tool: dict) -> list[dict[str, str]]:
    rows = json.loads(ds.query(tool["query"]).serialize(format="json"))["results"]["bindings"]
    # (rdflib は空の集計に空の 1 行を返す癖がある — 本物の Oxigraph は 0 行)
    return [{k: v["value"] for k, v in r.items()} for r in rows if r]


def _graph(ds_id: str) -> str:
    return substrate.versioned_graph_iri(ds_id, 1)


def _linted(tool: dict) -> None:
    (qt,) = parse_query_tools({"tools": [tool]})
    lint = lint_query_tool(qt)
    assert lint.ok, lint.errors


SAMPLE = _term("sample", "試料")
COMP = sv.SV + "composition"


def test_filter_contains_counts_per_dataset_and_skips_hubs() -> None:
    ds = _store()
    spec = CQSpec("組成に SnSe を含む試料は？", "filter", property=COMP, value="snse")
    (tool,) = sv.default_cq_tools(SAMPLE, [spec])
    _linted(tool)
    assert tool["name"] == "cq_sample_filter"
    assert tool["for_terms"] == [SAMPLE.iri, COMP]
    assert sv.UPPER_PATH_CLASS in tool["query"] and sv.UPPER_PATH_PROPERTY in tool["query"]
    rows = _run(ds, tool)
    # 大文字小文字を畳む部分一致: starry は s1・s2、other は o1。ハブの h1 は数えない
    assert {r["dataset_graph"]: int(r["entities"]) for r in rows} == {
        _graph("starry"): 2,
        _graph("other"): 1,
    }


def test_filter_exact_matches_the_whole_value_only() -> None:
    ds = _store()
    spec = CQSpec("q", "filter", property=COMP, value="SnSe", match="exact")
    (tool,) = sv.default_cq_tools(SAMPLE, [spec])
    _linted(tool)
    assert "STR(?v) = \"SnSe\"" in tool["query"] and "CONTAINS" not in tool["query"]
    rows = _run(ds, tool)
    assert {r["dataset_graph"]: int(r["entities"]) for r in rows} == {_graph("starry"): 1}


def test_filter_value_is_escaped_and_never_breaks_the_template() -> None:
    nasty = 'x" ) } UNION { ?s ?p ?o } #\\'
    (tool,) = sv.default_cq_tools(SAMPLE, [CQSpec("q", "filter", property=COMP, value=nasty)])
    _linted(tool)  # 構文として成り立つ = 値は文字列の中に閉じている
    assert _run(_store(), tool) == []


def test_group_counts_per_dataset_and_value() -> None:
    ds = _store()
    (tool,) = sv.default_cq_tools(SAMPLE, [CQSpec("組成ごとの試料数", "group", property=COMP)])
    _linted(tool)
    assert "GROUP BY ?dataset_graph ?v" in tool["query"] and "LIMIT 200" in tool["query"]
    rows = _run(ds, tool)
    got = {(r["dataset_graph"], r["v"]): int(r["entities"]) for r in rows}
    assert got == {
        (_graph("starry"), "SnSe"): 1,
        (_graph("starry"), "SnSe0.9S0.1"): 1,
        (_graph("starry"), "PbTe"): 1,
        (_graph("other"), "snse"): 1,
    }


def test_trend_returns_x_y_pairs_ordered_by_x() -> None:
    ds = _store()
    curve = _term("curve", "測定曲線")
    t, z = sv.SV + "temperature", sv.SV + "seebeck"
    (tool,) = sv.default_cq_tools(
        curve, [CQSpec("温度と Seebeck 係数は？", "trend", property=t, property2=z)]
    )
    _linted(tool)
    assert tool["for_terms"] == [curve.iri, t, z]
    assert "ORDER BY ?dataset_graph ?x" in tool["query"] and "LIMIT 2000" in tool["query"]
    rows = _run(ds, tool)
    assert [(float(r["x"]), float(r["y"])) for r in rows] == [(300, -10), (300, -5), (400, -20)]
    assert {r["dataset_graph"] for r in rows} == {_graph("starry")}


def test_join_counts_pairs_a_to_b() -> None:
    ds = _store()
    paper = _term("paper", "論文")
    rep, samp = sv.SV + "reports", SAMPLE.iri
    (tool,) = sv.default_cq_tools(
        paper, [CQSpec("論文が報告する試料は？", "join", property=rep, kind2=samp)]
    )
    _linted(tool)
    assert tool["for_terms"] == [paper.iri, rep, samp]
    rows = _run(ds, tool)
    assert {r["dataset_graph"]: int(r["pairs"]) for r in rows} == {_graph("starry"): 3}


def test_join_does_not_double_count_when_the_end_has_two_types() -> None:
    ds = _store()
    ds.graph(_u(_graph("starry"))).add((_u("urn:s1"), RDF_TYPE, _u(NS2 + "Specimen")))
    paper = _term("paper", "論文")
    (tool,) = sv.default_cq_tools(
        paper,
        [CQSpec("q", "join", property=sv.SV + "reports", kind2=SAMPLE.iri)],
    )
    assert {r["dataset_graph"]: int(r["pairs"]) for r in _run(ds, tool)} == {_graph("starry"): 3}


def test_names_repeat_per_op_and_old_ops_still_work_beside_the_new() -> None:
    specs = [
        CQSpec("a", "count"),
        CQSpec("b", "filter", property=COMP, value="Sn"),
        CQSpec("c", "filter", property=COMP, value="Pb"),
        CQSpec("d", "group", property=COMP),
    ]
    tools = sv.default_cq_tools(SAMPLE, specs)
    assert [t["name"] for t in tools] == [
        "cq_sample_count",
        "cq_sample_filter",
        "cq_sample_filter_2",
        "cq_sample_group",
    ]
    for t in tools:
        _linted(t)
    assert tools[0]["for_terms"] == [SAMPLE.iri]


@pytest.mark.parametrize(
    "spec",
    [
        CQSpec("q", "filter", property=COMP),  # 値が無い
        CQSpec("q", "filter", property=COMP, value="x", match="regex"),
        CQSpec("q", "filter", property=COMP, value="x" * 200),
        CQSpec("q", "filter", value="x"),  # 項目が無い
        CQSpec("q", "group"),
        CQSpec("q", "trend", property=COMP),  # y が無い
        CQSpec("q", "join", property=COMP),  # つなぐ先が無い
        CQSpec("q", "group", property="not an iri"),
        CQSpec("q", "join", property=COMP, kind2="javascript:alert(1)"),
    ],
)
def test_incomplete_or_unsafe_choices_are_refused(spec: CQSpec) -> None:
    with pytest.raises(TermError):
        sv.default_cq_tools(SAMPLE, [spec])


def test_a_property_term_takes_only_values() -> None:
    prop = _term("composition", "組成", kind="property")
    with pytest.raises(TermError):
        sv.default_cq_tools(prop, [CQSpec("q", "group", property=COMP)])


def test_describe_selection_phrases_the_four_shapes() -> None:
    d = sv.describe_selection
    assert d({"op": "filter", "kind": "試料", "property": "組成", "value": "SnSe"}) == (
        "試料のうち、組成に SnSe を含むもの"
    )
    assert d(
        {"op": "filter", "kind": "試料", "property": "組成", "value": "SnSe", "match": "exact"}
    ) == ("試料のうち、組成が SnSe のもの")
    assert d({"op": "group", "kind": "試料", "property": "組成"}) == "試料を 組成 ごとに数える"
    assert d(
        {"op": "trend", "kind": "測定曲線", "property": "温度", "property2": "Seebeck 係数"}
    ) == ("測定曲線の 温度 と Seebeck 係数 の並び")
    assert d({"op": "join", "kind": "論文", "property": "報告する", "kind2": "試料"}) == (
        "論文 が 報告する 試料"
    )
    assert d({"op": "join", "kind": "論文", "property": "報告する"}, "en") == ""
    assert d({"op": "trend", "kind": "測定曲線", "property": "温度"}) == ""  # y が無い
    assert d({"op": "filter", "kind": "試料", "property": "組成"}) == ""  # 値が無い
    assert d({"op": "group", "kind": "Sample", "property": "composition"}, "en") == (
        "Count Sample by composition"
    )


# ---------------------------------------------------------------------------
# テーマ: 下書き（answers）→ 確定（commit）で 4 つの形が宣言ツールになる
# ---------------------------------------------------------------------------


def _concept(key: str, ja: str, en: str, kind: str, slug: str) -> dict:
    return {
        "key": key, "label_ja": ja, "label_en": en, "aliases": [], "kind": kind,
        "use": "new", "slug": slug,
    }  # fmt: skip


def _body() -> dict:
    qs = [
        "組成に SnSe を含む試料は？",
        "試料は組成ごとに何件？",
        "温度と Seebeck 係数の並びは？",
        "論文が報告する試料は？",
    ]
    return {
        "label": "熱電",
        "lang": "ja",
        "ai_drafted": False,
        "questions": qs,
        "concepts": [
            _concept("t1", "試料", "sample", "class", "sample"),
            _concept("t2", "組成", "composition", "property", "composition"),
            _concept("t3", "測定曲線", "curve", "class", "curve"),
            _concept("t4", "温度", "temperature", "property", "temperature"),
            _concept("t5", "Seebeck 係数", "seebeck", "property", "seebeck"),
            _concept("t6", "論文", "paper", "class", "paper"),
            _concept("t7", "報告する", "reports", "property", "reports"),
        ],
        "relations": [],
        "answers": [
            {"question": qs[0], "mode": "data", "op": "filter", "kind": "t1", "property": "t2",
             "value": "SnSe", "match": "contains"},
            {"question": qs[1], "mode": "data", "op": "group", "kind": "t1", "property": "t2"},
            {"question": qs[2], "mode": "data", "op": "trend", "kind": "t3", "property": "t4",
             "property2": "t5"},
            {"question": qs[3], "mode": "data", "op": "join", "kind": "t6", "property": "t7",
             "kind2": "t1"},
        ],
    }  # fmt: skip


class _Client:
    def __init__(self) -> None:
        self.ds = rdflib.Dataset()

    async def sparql_select(self, query: str) -> dict:
        raw = self.ds.query(query).serialize(format="json")
        return json.loads(raw.decode() if isinstance(raw, bytes) else raw)

    async def sparql_update(self, update: str) -> None:
        self.ds.update(update)


async def test_commit_turns_the_four_shapes_into_declared_tools(tmp_path) -> None:
    out = await themes.commit_theme(_Client(), tmp_path, _body(), at=AT)
    assert out["memo"] == 0
    assert out["questions"] == [
        "cq_sample_filter",
        "cq_sample_group",
        "cq_curve_trend",
        "cq_paper_join",
    ]
    sel = out["theme"]["selections"]
    assert sel["cq_sample_filter"] == {
        "op": "filter", "kind": "試料", "property": "組成", "value": "SnSe", "match": "contains",
        "kind_slug": "sample", "property_slug": "composition",
    }  # fmt: skip
    assert sel["cq_curve_trend"]["property2"] == "Seebeck 係数"
    assert sel["cq_paper_join"]["kind2"] == "試料"
    tools = yaml.safe_load((tmp_path / "vocab-shared" / "query_tools.yaml").read_text())["tools"]
    by = {t["name"]: t for t in tools}
    assert by["cq_sample_filter"]["for_terms"] == [sv.SV + "sample", sv.SV + "composition"]
    assert by["cq_curve_trend"]["for_terms"] == [
        sv.SV + "curve", sv.SV + "temperature", sv.SV + "seebeck"
    ]
    assert by["cq_paper_join"]["for_terms"] == [
        sv.SV + "paper", sv.SV + "reports", sv.SV + "sample"
    ]  # fmt: skip
    for t in tools:
        _linted(t)
    # 実行文は選んだ名前だけから決まる
    assert sv.describe_selection(sel["cq_paper_join"]) == "論文 が 報告する 試料"


async def test_an_incomplete_choice_falls_to_a_memo_not_a_tool(tmp_path) -> None:
    body = _body()
    body["answers"][0] = {**body["answers"][0], "value": ""}  # 値が無い filter
    out = await themes.commit_theme(_Client(), tmp_path, body, at=AT)
    assert out["memo"] == 1
    assert out["questions"] == ["cq_sample_group", "cq_curve_trend", "cq_paper_join"]
    # y が無い trend だけを残すと、その曲線・温度は問いに結ばれない概念として拒まれる
    body = _body()
    body["answers"][2] = {**body["answers"][2], "property2": None}
    with pytest.raises(themes.ThemeError):
        await themes.commit_theme(_Client(), tmp_path / "x", body, at=AT)


def test_postprocess_checks_the_new_fields_and_drops_what_does_not_fit() -> None:
    ai = {
        "theme_label": "熱電",
        "concepts": [
            {"key": "c1", "label_ja": "試料", "label_en": "sample", "aliases": [],
             "kind": "class", "use": "new", "reason": ""},
            {"key": "c2", "label_ja": "組成", "label_en": "composition", "aliases": [],
             "kind": "property", "use": "new", "reason": ""},
            {"key": "c3", "label_ja": "論文", "label_en": "paper", "aliases": [],
             "kind": "class", "use": "new", "reason": ""},
        ],
        "relations": [],
        "answers": [
            {"question": "q1", "mode": "data", "op": "filter", "target": None, "direction": None,
             "kind": "c1", "property": "c2", "value": " SnSe  ", "match": "exact",
             "property2": None, "kind2": None, "wanted_data": "", "reason": ""},
            # kind2 に項目を選んだ join は、検査に合わずメモへ
            {"question": "q2", "mode": "data", "op": "join", "target": None, "direction": None,
             "kind": "c3", "property": "c2", "value": None, "match": None,
             "property2": None, "kind2": "c2", "wanted_data": "", "reason": ""},
        ],
        "suggested_questions": [],
    }  # fmt: skip
    out = themes.postprocess_draft(ai, questions=["q1", "q2"], existing=[])
    a = {x["question"]: x for x in out["answers"]}
    assert (a["q1"]["mode"], a["q1"]["value"], a["q1"]["match"]) == ("data", "SnSe", "exact")
    assert (a["q2"]["mode"], a["q2"]["memo"], a["q2"]["kind2"]) == ("memo", "ai", "")
