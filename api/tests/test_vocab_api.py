"""共有の言葉（上位構造）の api（ADR upper-structure-shared-terms.md §2・契約 handoff §5/§6）。

ストアは本物のインメモリ ``rdflib.Dataset``（``test_crosswalk_api`` と同じ差し替え口）なので、
鋳造・線・「答えるデータセット数」の集計 SPARQL・上位表が端点を通して本当に動く。
"""

# ruff: noqa: RUF001
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import rdflib
import yaml
from asterism import shared_vocab, substrate
from fastapi.testclient import TestClient

from asterism_api import autolink, registry
from asterism_api.main import build_app
from tests.test_autolink import _make_dataset, _never_discover
from tests.test_crosswalk_api import (
    _AUTH,
    PRED,
    _config_body,
    _DatasetClient,
    _discover,
    _seed_promoted,
    _settings,
)

XW = "https://kumagallium.github.io/asterism/crosswalk/ontology#"
SV = shared_vocab.SV
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"

CQ_COUNT = {"title": "回折点は、どのデータセットに何件あるか", "op": "count"}
CQ_VALUES = {"title": "強度は、どのデータセットにいくつあるか", "op": "values"}


def _app(tmp_path: Path, ds: rdflib.Dataset | None = None):
    ds = ds if ds is not None else rdflib.Dataset()
    app = build_app(_settings(tmp_path), oxigraph_client=_DatasetClient(ds), start_watcher=False)
    return app, ds


def _mint(client, slug="diffraction_point", kind="class", cqs=None, **extra):
    body = {
        "slug": slug,
        "kind": kind,
        "label_ja": "回折点" if kind == "class" else "強度",
        "cqs": [CQ_COUNT] if cqs is None else cqs,
        **extra,
    }
    return client.post("/api/vocab/shared", json=body)


def _align(client, source, target, relation="subClassOf", **extra):
    return client.post(
        "/api/crosswalk/align",
        json={"source": source, "target": target, "relation": relation, **extra},
    )


# ---------------------------------------------------------------------------
# 語: 作る・一覧・外す
# ---------------------------------------------------------------------------


def test_mint_returns_201_then_the_list_shows_it_unwired(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        assert client.get("/api/vocab/shared").json() == {"terms": []}

        r = _mint(
            client,
            label_en="Diffraction point",
            comment="回折で見える点",
            declined_standard={"iri": "http://example.org/std#Spot", "reason": "意味がずれる"},
        )
        assert r.status_code == 201, r.text
        term = r.json()["term"]
        assert term["iri"] == SV + "diffraction_point"
        assert term["declined_standard"] == "http://example.org/std#Spot"

        (listed,) = client.get("/api/vocab/shared").json()["terms"]
        assert listed["slug"] == "diffraction_point"
        assert listed["kind"] == "class"
        assert listed["wired"] is False and listed["answering_datasets"] == 0
        assert [c["tool_name"] for c in listed["cqs"]] == ["cq_diffraction_point_count"]

    reg = tmp_path / "registry" / "vocab-shared"
    meta = json.loads((reg / "meta.json").read_text(encoding="utf-8"))
    assert meta["is_shared_vocab"] is True and meta["promoted"] is True
    assert "is_crosswalk" not in meta
    tools = yaml.safe_load((reg / "query_tools.yaml").read_text(encoding="utf-8"))["tools"]
    assert tools[0]["for_terms"] == [SV + "diffraction_point"]


def test_mint_without_a_question_is_422_and_writes_nothing(tmp_path: Path) -> None:
    app, ds = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        r = _mint(client, cqs=[])
        assert r.status_code == 422, r.text
        assert client.get("/api/vocab/shared").json() == {"terms": []}
    assert not (tmp_path / "registry" / "vocab-shared").exists()
    assert len(ds.graph(rdflib.URIRef(substrate.SHARED_VOCAB_GRAPH))) == 0


@pytest.mark.parametrize(
    "extra",
    [
        {"slug": "Bad-Slug"},  # 形が違う
        {"slug": "1abc"},
        {"kind": "thing"},  # 閉じた選択の外
        {"cqs": [{"title": "項目の問い", "op": "values"}]},  # 種類(class)に項目の op
        {"cqs": [{"title": "", "op": "count"}]},  # 題が空
    ],
)
def test_mint_bad_input_is_422(tmp_path: Path, extra: dict) -> None:
    app, _ = _app(tmp_path)
    body = {"slug": "ok_term", "kind": "class", "label_ja": "語", "cqs": [CQ_COUNT], **extra}
    with TestClient(app, headers=_AUTH) as client:
        r = client.post("/api/vocab/shared", json=body)
        assert r.status_code == 422, r.text
        assert client.get("/api/vocab/shared").json() == {"terms": []}


def test_mint_same_slug_is_409(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        assert _mint(client).status_code == 201
        again = _mint(client)
        assert again.status_code == 409, again.text
        assert len(client.get("/api/vocab/shared").json()["terms"]) == 1


def test_vocab_writes_need_the_write_token_but_reads_do_not(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app) as anon:  # トークン無し
        assert anon.get("/api/vocab/shared").status_code == 200
        assert anon.get("/api/vocab/upper").status_code == 200
        assert anon.post("/api/vocab/shared", json={}).status_code == 401
        assert anon.delete("/api/vocab/shared/x").status_code == 401
        assert anon.post("/api/vocab/shared/x/cq", json={}).status_code == 401


def test_delete_is_409_while_a_line_remains_then_204_with_its_questions(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    src = "https://example.org/xrd#Record"
    with TestClient(app, headers=_AUTH) as client:
        _mint(client)
        assert _align(client, src, SV + "diffraction_point").status_code == 200

        refused = client.delete("/api/vocab/shared/diffraction_point")
        assert refused.status_code == 409, refused.text
        assert len(client.get("/api/vocab/shared").json()["terms"]) == 1

        assert _align(client, src, SV + "diffraction_point", remove=True).status_code == 200
        done = client.delete("/api/vocab/shared/diffraction_point")
        assert done.status_code == 204 and done.content == b""
        assert client.get("/api/vocab/shared").json() == {"terms": []}

        assert client.delete("/api/vocab/shared/diffraction_point").status_code == 404
        assert client.delete("/api/vocab/shared/Bad-Slug").status_code == 422

    tools = yaml.safe_load(
        (tmp_path / "registry" / "vocab-shared" / "query_tools.yaml").read_text(encoding="utf-8")
    )
    assert tools["tools"] == []


# ---------------------------------------------------------------------------
# 問いを足す
# ---------------------------------------------------------------------------


def test_add_cq_appends_with_a_numbered_name_and_refuses_bad_ones(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        _mint(client)
        first = client.post("/api/vocab/shared/diffraction_point/cq", json=CQ_COUNT)
        assert first.status_code == 201, first.text
        assert first.json()["cq"]["tool_name"] == "cq_diffraction_point_count_2"
        second = client.post(
            "/api/vocab/shared/diffraction_point/cq", json={**CQ_COUNT, "title": "もう一つ"}
        )
        assert second.json()["cq"]["tool_name"] == "cq_diffraction_point_count_3"

        (term,) = client.get("/api/vocab/shared").json()["terms"]
        assert len(term["cqs"]) == 3

        # 種類の語に項目の op は 422・未鋳造は 404・「写す」は元の問いが無ければ 404
        # （写す本体は test_upper_questions.py）
        bad = client.post("/api/vocab/shared/diffraction_point/cq", json=CQ_VALUES)
        assert bad.status_code == 422, bad.text
        assert client.post("/api/vocab/shared/nothing/cq", json=CQ_COUNT).status_code == 404
        copy = client.post(
            "/api/vocab/shared/diffraction_point/cq",
            json={**CQ_COUNT, "from_dataset": "ds-a", "question_id": "q_x"},
        )
        assert copy.status_code == 404, copy.text
        assert len(client.get("/api/vocab/shared").json()["terms"][0]["cqs"]) == 3


def test_add_cq_stops_on_an_unreadable_query_tools_file(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        _mint(client)
        path = tmp_path / "registry" / "vocab-shared" / "query_tools.yaml"
        path.write_text("tools: [unclosed", encoding="utf-8")
        r = client.post("/api/vocab/shared/diffraction_point/cq", json=CQ_COUNT)
        assert r.status_code in (409, 502), r.text
        assert path.read_text(encoding="utf-8") == "tools: [unclosed"


# ---------------------------------------------------------------------------
# 線: cq・kind・エラーの写像・wired の数え直し
# ---------------------------------------------------------------------------


def test_align_records_the_cq_and_rows_carry_kinds(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    src = "https://example.org/xrd#Record"
    with TestClient(app, headers=_AUTH) as client:
        _mint(client)
        r = _align(client, src, SV + "diffraction_point", cq="回折点は何件あるか")
        assert r.status_code == 200, r.text
        assert r.json()["cq"] == "回折点は何件あるか"
        assert r.json()["target_kind"] == "shared"

        (row,) = client.get("/api/crosswalk/alignments").json()["alignments"]
        assert row["cq"] == "回折点は何件あるか"
        assert (row["source_kind"], row["target_kind"]) == ("unknown", "shared")
        assert row["broken"] is False
        assert {"source_dataset", "target_dataset"} <= row.keys()


def test_align_maps_cycle_to_409_and_unminted_and_kind_mismatch_to_422(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        _mint(client, slug="parent_term")
        _mint(client, slug="child_term")
        _mint(client, slug="intensity", kind="property", cqs=[CQ_VALUES])
        parent, child = SV + "parent_term", SV + "child_term"

        assert _align(client, child, parent).status_code == 200
        cycle = _align(client, parent, child)
        assert cycle.status_code == 409, cycle.text
        assert _align(client, parent, parent).status_code == 409

        ghost = _align(client, "https://example.org/x#A", SV + "never_minted")
        assert ghost.status_code == 422, ghost.text

        mixed = _align(client, child, SV + "intensity")
        assert mixed.status_code == 422, mixed.text

        # 既存の挙動: 何も知らない絶対 IRI は通す・関係の閉集合の外は 400
        assert _align(client, "https://a.example/x#A", "https://b.example/y#B").status_code == 200
        assert _align(client, "https://a.example/x#A", parent, "sameAs").status_code == 400

        lines = client.get("/api/crosswalk/alignments").json()["alignments"]
        assert len(lines) == 2  # 拒まれた線は 1 本も書かれていない


def test_wired_is_recounted_after_align_and_unalign(tmp_path: Path) -> None:
    ds = rdflib.Dataset()
    _seed_promoted(ds, tmp_path / "registry", "ds-a", [("urn:a1", "x"), ("urn:a2", "y")])
    app, _ = _app(tmp_path, ds)
    with TestClient(app, headers=_AUTH) as client:
        _mint(client, slug="intensity", kind="property", cqs=[CQ_VALUES])

        def term() -> dict:
            return client.get("/api/vocab/shared").json()["terms"][0]

        assert term()["answering_datasets"] == 0 and term()["wired"] is False

        r = _align(client, PRED, SV + "intensity", "subPropertyOf")
        assert r.status_code == 200, r.text
        assert "wired_stale" not in r.json()
        assert term()["answering_datasets"] == 1 and term()["wired"] is True

        rm = _align(client, PRED, SV + "intensity", "subPropertyOf", remove=True)
        assert rm.status_code == 200 and "wired_stale" not in rm.json()
        assert term()["answering_datasets"] == 0 and term()["wired"] is False

    wired = json.loads(
        (tmp_path / "registry" / "vocab-shared" / "wired.json").read_text(encoding="utf-8")
    )
    assert wired["terms"][SV + "intensity"] == 0


def _wired(tmp_path: Path) -> dict:
    path = tmp_path / "registry" / "vocab-shared" / "wired.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_wired_is_stale_when_the_recount_fails_but_the_line_is_still_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ds = rdflib.Dataset()
    _seed_promoted(ds, tmp_path / "registry", "ds-a", [("urn:a1", "x")])
    app, _ = _app(tmp_path, ds)
    with TestClient(app, headers=_AUTH) as client:
        _mint(client, slug="intensity", kind="property", cqs=[CQ_VALUES])

        async def boom(*_a, **_k):
            raise RuntimeError("store down")

        monkeypatch.setattr(shared_vocab, "recompute_wired", boom)
        r = _align(client, PRED, SV + "intensity", "subPropertyOf")
        assert r.status_code == 200, r.text
        assert r.json()["wired_stale"] is True
        rows = client.get("/api/crosswalk/alignments").json()["alignments"]
        # 数え直しが落ちても線そのものは書けている
        assert [(a["source"], a["target"]) for a in rows] == [(PRED, SV + "intensity")]

        rm = _align(client, PRED, SV + "intensity", "subPropertyOf", remove=True)
        assert rm.status_code == 200 and rm.json()["wired_stale"] is True


def test_wired_is_recounted_when_a_dataset_using_a_term_directly_is_published_and_retracted(
    tmp_path: Path,
) -> None:
    """線を 1 本も引かず、sv:X を rdf:type に直接使うデータセットを公開 → 1。
    引用対象から外す → 0、戻す → 1、削除 → 0。"""
    root = tmp_path / "registry"
    root.mkdir()
    dsid = _make_dataset(root, "direct-use", has_handle=False, promoted=False)
    registry.mark_ingested(
        root,
        dsid,
        graph_iri=substrate.versioned_graph_iri(dsid, 1),
        triple_count=1,
        ingested_at="2026-01-01T00:00:00Z",
        data_seq=1,
    )
    ds = rdflib.Dataset()
    ds.graph(rdflib.URIRef(substrate.versioned_graph_iri(dsid, 1))).add(
        (rdflib.URIRef("urn:e1"), rdflib.RDF.type, rdflib.URIRef(SV + "diffraction_point"))
    )
    app, _ = _app(tmp_path, ds)
    with TestClient(app, headers=_AUTH) as client:
        assert _mint(client).status_code == 201
        assert _wired(tmp_path)["terms"][SV + "diffraction_point"] == 0

        r = client.post(f"/api/datasets/{dsid}/promote")
        assert r.status_code == 200, r.text
        assert _wired(tmp_path)["terms"][SV + "diffraction_point"] == 1

        assert client.post(f"/api/datasets/{dsid}/retract").status_code == 200
        assert _wired(tmp_path)["terms"][SV + "diffraction_point"] == 0

        assert client.post(f"/api/datasets/{dsid}/reinstate").status_code == 200
        assert _wired(tmp_path)["terms"][SV + "diffraction_point"] == 1

        assert client.delete(f"/api/datasets/{dsid}", params={"force": "true"}).status_code == 200
        assert _wired(tmp_path)["terms"][SV + "diffraction_point"] == 0


def test_normalize_label_is_the_same_rule_as_step0_meaning_key() -> None:
    from asterism.shared_vocab import normalize_label
    from asterism_step0.skeleton_annotate import _meaning_key

    samples = [
        "Composition",
        "composition",
        "  Thermal   Conductivity ",
        "ＴＨＥＲＭＡＬ　Ｃｏｎｄｕｃｔｉｖｉｔｙ",  # 全角英字・全角空白
        "組成 ",
        "　組成　",
        "回折\t点\n",
        "ﾊﾝｶｸ ｶﾅ",  # 半角カナ
        "Ｂｉ２Ｔｅ３",
        "",
        "   ",
        "ß Straße",  # casefold の差が出る例
    ]
    for text in samples:
        assert normalize_label(text) == _meaning_key(text), text
    assert normalize_label("ＣＯＭＰＯＳＩＴＩＯＮ") == normalize_label(" composition ")


def test_align_without_any_shared_term_creates_no_vocab_shared_directory(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        assert _align(client, "https://a.example/x#A", "https://b.example/y#B").status_code == 200
    assert not (tmp_path / "registry" / "vocab-shared").exists()


# ---------------------------------------------------------------------------
# scope
# ---------------------------------------------------------------------------


def test_scope_perspective_equals_the_client_side_filter_of_the_screen(tmp_path: Path) -> None:
    """CrosswalkView は「両端が読み込めた視点の語（concept の class_iri / link_predicate）」だけを
    視点の対応として出す。端点の scope=perspective が同じ集合を返す。"""
    ds = rdflib.Dataset()
    _seed_promoted(ds, tmp_path / "registry", "ds-a", [("urn:a1", "Bi₂Te₃")])
    _seed_promoted(ds, tmp_path / "registry", "ds-b", [("urn:b1", "Bi2Te3")])
    app, _ = _app(tmp_path, ds)
    with TestClient(app, headers=_AUTH) as client:
        assert client.post("/api/crosswalk/build", json=_config_body(["ds-a", "ds-b"])).is_success
        concept = client.get("/api/crosswalk").json()["config"]["concepts"][0]
        alignable = {concept["class_iri"], concept["link_predicate"]}
        assert len(alignable) == 2

        cls, prop = concept["class_iri"], concept["link_predicate"]
        stray_a, stray_b = XW + "StrayA", XW + "StrayB"  # xw: だが読み込める視点の語ではない
        assert _align(client, cls, XW + "Material").status_code == 200  # 片端だけ視点の語
        assert _align(client, stray_a, stray_b).status_code == 200
        r = client.post(
            "/api/crosswalk/align",
            json={"source": prop, "target": cls, "relation": "equivalentClass"},
        )
        assert r.status_code == 200, r.text
        assert _align(client, cls, "http://qudt.org/vocab/quantitykind/Mass").status_code == 200

        everything = client.get("/api/crosswalk/alignments").json()["alignments"]
        expected = {
            (a["source"], a["target"])
            for a in everything
            if a["source"] in alignable and a["target"] in alignable
        }
        got = client.get("/api/crosswalk/alignments?scope=perspective").json()["alignments"]
        assert expected == {(a["source"], a["target"]) for a in got}
        assert expected == {(prop, cls)}

        std = client.get("/api/crosswalk/alignments?scope=standard").json()["alignments"]
        assert [a["target"] for a in std] == ["http://qudt.org/vocab/quantitykind/Mass"]

        assert client.get("/api/crosswalk/alignments?scope=nonsense").status_code == 422
        all_rows = client.get("/api/crosswalk/alignments").json()
        assert len(all_rows["alignments"]) == len(everything)
        assert "subClassOf" in all_rows["relations"]


def test_scope_shared_returns_only_lines_touching_a_shared_term(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        _mint(client)
        _align(client, "https://a.example/x#A", SV + "diffraction_point")
        _align(client, "https://a.example/x#A", "https://b.example/y#B")
        shared = client.get("/api/crosswalk/alignments?scope=shared").json()["alignments"]
        assert [a["target"] for a in shared] == [SV + "diffraction_point"]


# ---------------------------------------------------------------------------
# 上位の対応表・当てはめ候補
# ---------------------------------------------------------------------------


def test_upper_returns_the_topmost_shared_term_per_iri(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    a, b = "https://example.org/xrd-a#Record", "https://example.org/xrd-b#Record"
    with TestClient(app, headers=_AUTH) as client:
        _mint(client, slug="peak", cqs=[CQ_COUNT])
        _mint(client, slug="diffraction_point", cqs=[CQ_COUNT])
        _align(client, SV + "diffraction_point", SV + "peak")  # peak が上
        _align(client, a, SV + "diffraction_point")
        _align(client, b, SV + "diffraction_point")

        body = client.get("/api/vocab/upper").json()
        assert body["classes"][a] == SV + "peak"
        assert body["classes"][b] == SV + "peak"
        assert body["classes"][SV + "peak"] == SV + "peak"
        assert body["properties"] == {}
        assert body["at"]


def test_fit_exact_matches_only_and_generic_labels_yield_nothing(tmp_path: Path) -> None:
    ds = rdflib.Dataset()
    onto = ds.graph(rdflib.URIRef(substrate.ontology_graph_iri("xrd-a")))
    onto.add(
        (rdflib.URIRef("https://example.org/xrd-a#intensity"), rdflib.RDF.type, rdflib.RDF.Property)
    )
    onto.add(
        (
            rdflib.URIRef("https://example.org/xrd-a#intensity"),
            rdflib.RDFS.label,
            rdflib.Literal("回折強度", lang="ja"),
        )
    )
    app, _ = _app(tmp_path, ds)
    with TestClient(app, headers=_AUTH) as client:
        _mint(client, label_en="Diffraction point")
        got = client.get("/api/vocab/fit", params={"label": "回折点", "column": "dp"}).json()
        assert [(c["kind"], c["term"], c["matched_by"]) for c in got["candidates"]] == [
            ("shared", SV + "diffraction_point", "label")
        ]
        # 列名の英語でも当たる（NFKC・大文字小文字・空白を畳む）
        by_col = client.get("/api/vocab/fit", params={"column": "  DIFFRACTION   point "}).json()
        assert by_col["candidates"][0]["matched_by"] == "column"

        other = client.get("/api/vocab/fit", params={"label": "回折強度"}).json()["candidates"]
        assert [(c["kind"], c["term"]) for c in other] == [
            ("dataset", "https://example.org/xrd-a#intensity")
        ]
        # 曖昧一致は出さない・一般的すぎる名前は何も出さない
        assert client.get("/api/vocab/fit", params={"label": "回折"}).json()["candidates"] == []
        for generic in ("name", "ID", "名前", "値"):
            assert client.get("/api/vocab/fit", params={"label": generic}).json() == {
                "candidates": []
            }
        assert client.get("/api/vocab/fit").status_code == 400


def test_fit_candidates_say_class_or_property(tmp_path: Path) -> None:
    """③（項目）と⑤（種類）が候補を取り違えないよう、各候補に term_kind が付く。"""
    ds = rdflib.Dataset()
    onto = ds.graph(rdflib.URIRef(substrate.ontology_graph_iri("xrd-a")))
    for local, rdf_type, label in (
        ("Peak", rdflib.RDFS.Class, "ピーク"),
        ("intensity", rdflib.RDF.Property, "ピーク"),
    ):
        iri = rdflib.URIRef(f"https://example.org/xrd-a#{local}")
        onto.add((iri, rdflib.RDF.type, rdf_type))
        onto.add((iri, rdflib.RDFS.label, rdflib.Literal(label, lang="ja")))
    app, _ = _app(tmp_path, ds)
    with TestClient(app, headers=_AUTH) as client:
        _mint(client, label_en="Diffraction point")  # 回折点（class）
        _mint(client, slug="peak_height", kind="property", label_ja="ピーク高さ", cqs=[CQ_VALUES])
        shared = client.get("/api/vocab/fit", params={"label": "回折点"}).json()["candidates"]
        assert [(c["kind"], c["term_kind"]) for c in shared] == [("shared", "class")]
        prop = client.get("/api/vocab/fit", params={"label": "ピーク高さ"}).json()["candidates"]
        assert [(c["kind"], c["term_kind"]) for c in prop] == [("shared", "property")]
        # 他データの項目は ontology graph の型で区別される（同じ label でも別々に出る）
        other = client.get("/api/vocab/fit", params={"label": "ピーク"}).json()["candidates"]
        assert {(c["term"].rsplit("#", 1)[-1], c["term_kind"]) for c in other} == {
            ("Peak", "class"),
            ("intensity", "property"),
        }
        # 標準の語は ground_terms の kind
        from asterism import grounding

        want = {
            c.iri: c.kind
            for c in grounding.ground_terms("thermal conductivity", limit=8)
            if c.score == 100
        }
        assert want
        std = client.get("/api/vocab/fit", params={"column": "thermal conductivity"}).json()
        assert {c["term"]: c["term_kind"] for c in std["candidates"]} == want


def test_fit_puts_an_exact_standard_term_first_and_drops_fuzzy_ones(tmp_path: Path) -> None:
    from asterism import grounding

    exact = [
        c.iri for c in grounding.ground_terms("thermal conductivity", limit=8) if c.score == 100
    ]
    assert exact
    app, _ = _app(tmp_path)
    with TestClient(app, headers=_AUTH) as client:
        _mint(client, slug="thermal_conductivity", kind="property", cqs=[CQ_VALUES])
        # 共有の語の label も同じ列名に当たる → 標準が先・共有が後
        client.post(
            "/api/vocab/shared",
            json={
                "slug": "heat_conduction",
                "kind": "property",
                "label_ja": "熱伝導",
                "label_en": "thermal conductivity",
                "cqs": [CQ_VALUES],
            },
        )
        got = client.get("/api/vocab/fit", params={"column": "thermal conductivity"}).json()
        kinds = [c["kind"] for c in got["candidates"]]
        assert kinds == ["standard"] * len(exact) + ["shared"]
        assert [c["term"] for c in got["candidates"][: len(exact)]] == exact
        # 曖昧(tokens_subset 級)だけの列名では標準を出さない
        fuzzy = client.get("/api/vocab/fit", params={"column": "temperature"}).json()
        assert [c for c in fuzzy["candidates"] if c["kind"] == "standard"] == []
        # 語順違いの exact_tokens(90) も出さない — 標準は完全一致(100)だけ
        reordered = client.get("/api/vocab/fit", params={"column": "conductivity thermal"}).json()
        assert [c for c in reordered["candidates"] if c["kind"] == "standard"] == []


# ---------------------------------------------------------------------------
# vocab-shared はデータセットではない
# ---------------------------------------------------------------------------


def test_vocab_shared_is_not_listed_as_a_dataset_and_never_marked_canonical(
    tmp_path: Path,
) -> None:
    ds = rdflib.Dataset()
    _seed_promoted(ds, tmp_path / "registry", "ds-a", [("urn:a1", "x")])
    app, _ = _app(tmp_path, ds)
    with TestClient(app, headers=_AUTH) as client:
        _mint(client)
        listed = client.get("/api/datasets").json()
        assert [d["id"] for d in listed["datasets"]] == ["ds-a"]
        assert listed["count"] == 1
    # 起動時の「公開済み」バックフィルが共有の言葉に印を付けない（再起動しても）
    app2, _ = _app(tmp_path, ds)
    with TestClient(app2, headers=_AUTH):
        pass
    flagged = ds.query(
        f"ASK {{ GRAPH <{substrate.CONTROL_GRAPH_IRI}> {{ "
        f"<{substrate.canonical_graph_iri('vocab-shared')}> ?p ?o }} }}"
    )
    assert flagged.askAnswer is False


def test_discover_skips_vocab_shared_with_its_own_reason(tmp_path: Path) -> None:
    ds = rdflib.Dataset()
    _seed_promoted(ds, tmp_path / "registry", "ds-a", [("urn:a1", "Bi₂Te₃")])
    _seed_promoted(ds, tmp_path / "registry", "ds-b", [("urn:b1", "Bi2Te3")])
    app, _ = _app(tmp_path, ds)
    with TestClient(app, headers=_AUTH) as client:
        _mint(client)
        result = _discover(client)
    scanned = result["scanned"]
    assert {"dataset_id": "vocab-shared", "reason": "shared_vocab"} in scanned["datasets_skipped"]
    assert "vocab-shared" not in {d["dataset_id"] for d in scanned["datasets"]}


@pytest.mark.asyncio
async def test_autolink_does_not_treat_vocab_shared_as_a_partner(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    root.mkdir()
    own = _make_dataset(root, "book-loans", has_handle=True)
    # ☑ 列を持つ「相棒に見える」項目でも、共有の言葉なら候補にしない
    fake = _make_dataset(root, "vocab-shared", has_handle=True)
    registry.update_meta_atomic(root, fake, {"is_shared_vocab": True})

    report = await autolink.maybe_autolink_handles(None, root, own, discover=_never_discover)

    assert report["linked"] == []
    assert report["skipped"] == [{"reason": "no_partner", "dataset_id": own}]


def test_is_system_entry_is_reexported_from_registry() -> None:
    assert registry.is_system_entry is shared_vocab.is_system_entry
    assert registry.is_system_entry({"is_shared_vocab": True})
    assert registry.is_system_entry({"is_crosswalk": True})
    assert not registry.is_system_entry({"id": "ds-a"})


# ---------------------------------------------------------------------------
# registry._write_tools: atomic・読めない yaml では上書きしない
# ---------------------------------------------------------------------------

TOOL = {
    "name": "t1",
    "title": "t",
    "description": "d",
    "parameters": [],
    "query": "SELECT ?s WHERE { ?s ?p ?o } LIMIT 1",
    "result": {"item": {"s": "s"}},
}


def _dataset_dir(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "registry"
    meta = registry.save_dataset(
        root,
        "tools-ds",
        {},
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
        created_at="2026-01-01T00:00:00Z",
    )
    return root, meta["id"]


def test_save_query_tool_refuses_to_overwrite_an_unreadable_yaml(tmp_path: Path) -> None:
    root, dsid = _dataset_dir(tmp_path)
    path = registry.query_tools_path(root, dsid)
    assert path is not None
    for broken in ("tools: [unclosed", "- just\n- a list\n", "tools: {a: 1}\n", "tools: [1, 2]\n"):
        path.write_text(broken, encoding="utf-8")
        with pytest.raises(registry.QueryToolsUnreadable):
            registry.save_query_tool(root, dsid, TOOL)
        with pytest.raises(registry.QueryToolsUnreadable):
            registry.delete_query_tool(root, dsid, "t1")
        assert path.read_text(encoding="utf-8") == broken  # 1 バイトも変えない
    assert [p.name for p in path.parent.iterdir() if p.name.endswith(".tmp")] == []


def test_save_query_tool_is_atomic_when_the_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, dsid = _dataset_dir(tmp_path)
    registry.save_query_tool(root, dsid, TOOL)
    path = registry.query_tools_path(root, dsid)
    assert path is not None
    before = path.read_text(encoding="utf-8")

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        registry.save_query_tool(root, dsid, {**TOOL, "name": "t2"})
    monkeypatch.undo()
    assert path.read_text(encoding="utf-8") == before  # 書きかけが残らない
    assert [p.name for p in path.parent.iterdir() if p.name.endswith(".tmp")] == []


def test_a_tools_key_with_no_value_is_an_empty_list_not_unreadable(tmp_path: Path) -> None:
    root, dsid = _dataset_dir(tmp_path)
    path = registry.query_tools_path(root, dsid)
    assert path is not None
    for empty in ("tools:\n", "tools: null\n", "{}\n", ""):
        path.write_text(empty, encoding="utf-8")
        assert registry.delete_query_tool(root, dsid, "t1") is False
        assert [t["name"] for t in registry.save_query_tool(root, dsid, TOOL)] == ["t1"]
        path.unlink()


def test_save_and_delete_still_work_on_a_readable_or_missing_yaml(tmp_path: Path) -> None:
    root, dsid = _dataset_dir(tmp_path)
    assert [t["name"] for t in registry.save_query_tool(root, dsid, TOOL)] == ["t1"]
    registry.save_query_tool(root, dsid, {**TOOL, "name": "t2"})
    registry.save_query_tool(root, dsid, {**TOOL, "title": "renamed"})  # 同名は置き換え
    names = [t["name"] for t in registry.list_query_tools(root, dsid)]
    assert names == ["t2", "t1"]
    assert registry.delete_query_tool(root, dsid, "t2") is True
    assert registry.delete_query_tool(root, dsid, "nope") is False


def test_tools_routes_map_an_unreadable_yaml_to_409(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    root, dsid = _dataset_dir(tmp_path)
    path = registry.query_tools_path(root, dsid)
    assert path is not None
    path.write_text("tools: [unclosed", encoding="utf-8")
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(f"/api/datasets/{dsid}/tools", json=TOOL)
        assert r.status_code == 409, r.text
        assert client.delete(f"/api/datasets/{dsid}/tools/t1").status_code == 409
    assert path.read_text(encoding="utf-8") == "tools: [unclosed"
