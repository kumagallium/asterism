# ruff: noqa: RUF001
"""``asterism.knowledge_bundle``（知識の束 → 写しのファイル）のテスト。例はすべて作り物。"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml

from asterism.knowledge_bundle import (
    FORMAT,
    BundleError,
    KnowledgeCopy,
    design_document,
    link_terms,
    merge_relations,
    merge_terms,
    parse_bundle,
    prepare_files,
    read_copy,
    term_pairs,
)
from asterism.mapping_ir_read import read_mapping_ir

CLAIM = "https://knowledge.test/claim/"
SOURCE = "https://knowledge.test/source/"
TERM = "https://knowledge.test/term/"
STAMP = "2026-01-01T00:00:00.000Z"


def _claim(cid: str, title: str, **kw):
    row = {
        "id": cid,
        "iri": CLAIM + cid,
        "source_url": f"https://app.test/#claim/{cid}",
        "title": title,
        "review_state": "candidate",
        "epistemic_status": None,
        "modal_qualifier": None,
        "level": None,
        "source_iri": [SOURCE + "s1"],
        "generated_at": None,
        "exported_at": STAMP,
    }
    row.update(kw)
    return row


def _cand(cid: str, name: str, kind="class", role=None, aliases=None):
    return {
        "id": cid,
        "name": name,
        "kind": kind,
        "role": role,
        "aliases": aliases or [],
        "origin_id": cid.split(".")[0],
        "origin_iri": CLAIM + cid.split(".")[0],
        "source_url": "https://app.test/#topic",
        "exported_at": STAMP,
    }


def _raw() -> dict:
    return {
        "format": FORMAT,
        "title": "テスト帳",
        "exported_at": STAMP,
        "bases": {"claim": CLAIM, "source": SOURCE, "term": TERM},
        "files": {
            "claims.json": [
                _claim("c1", "生地を休ませるとパンが膨らむ"),
                _claim("c2", "格子熱伝導率が Bi2Te3 で下がる"),
                _claim("c3", "熱伝導率は PbTe より低い"),
            ],
            "topics.json": [
                {
                    "id": "p1",
                    "iri": CLAIM + "p1",
                    "source_url": "https://app.test/#topic/p1",
                    "title": "パン生地",
                    "topic_type": "material",
                    "material": "パン生地",
                    "property": None,
                    "claim_iri": [CLAIM + "c1"],
                    "source_iri": [],
                    "generated_at": None,
                    "exported_at": STAMP,
                }
            ],
            "sources.json": [
                {"id": "s1", "iri": SOURCE + "s1", "name": "実験メモ", "kind": "note",
                 "exported_at": STAMP},
            ],
            "term_candidates.json": [
                _cand("p1", "パン生地", role="material"),
                _cand("p2.m", "Bi2Te3", role="material"),
                _cand("p2.p", "格子熱伝導率", kind="property"),
                _cand("p3", "熱伝導率"),
            ],
            "term_claims.json": [
                {"term_id": "p1", "claim_id": "c1", "claim_iri": CLAIM + "c1"},
            ],
            "term_relations.json": [
                {"from_term": "p2.m", "rel": "has_property", "to_term": "p2.p",
                 "origin_id": "p2", "origin_iri": CLAIM + "p2"},
            ],
        },
        "counts": {},
    }


# ---- parse_bundle -------------------------------------------------------------------------------


def test_parse_bundle_正しい束():
    b = parse_bundle(_raw())
    assert b.title == "テスト帳"
    assert b.bases == {"claim": CLAIM, "source": SOURCE, "term": TERM}
    assert set(b.files) == {
        "claims.json", "topics.json", "sources.json",
        "term_candidates.json", "term_claims.json", "term_relations.json",
    }  # fmt: skip
    assert len(b.files["claims.json"]) == 3
    assert b.dropped == {}


def test_parse_bundle_無いファイルは空_知らないファイルは無視():
    raw = _raw()
    del raw["files"]["sources.json"]
    raw["files"]["other.json"] = [1, 2]
    b = parse_bundle(raw)
    assert b.files["sources.json"] == []
    assert "other.json" not in b.files


def test_parse_bundle_題が空なら既定():
    raw = _raw()
    raw["title"] = "  "
    assert parse_bundle(raw).title == "knowledge"
    raw["title"] = 3
    assert parse_bundle(raw).title == "knowledge"


@pytest.mark.parametrize("fmt", [None, "asterism-knowledge/2", "other"])
def test_parse_bundle_format_が違う(fmt):
    raw = _raw()
    raw["format"] = fmt
    with pytest.raises(BundleError):
        parse_bundle(raw)


def test_parse_bundle_形の悪い例():
    for bad in (None, [], "x"):
        with pytest.raises(BundleError):
            parse_bundle(bad)
    raw = _raw()
    raw["files"] = []
    with pytest.raises(BundleError):
        parse_bundle(raw)
    raw = _raw()
    raw["files"]["claims.json"] = {"a": 1}
    with pytest.raises(BundleError):
        parse_bundle(raw)
    raw = _raw()
    del raw["bases"]
    with pytest.raises(BundleError):
        parse_bundle(raw)


@pytest.mark.parametrize(
    "bad",
    [
        "ftp://knowledge.test/claim/",  # http(s) でない
        "https://knowledge.test/claim",  # 末尾が / か # でない
        "https://knowledge.test/cla im/",  # 空白
        'https://knowledge.test/"x"/',  # 禁止文字
        "https://example.org/claim/",  # 仮のホスト
        "https://notes.example.com/claim/",  # 仮のホストのサブドメイン
        "http://localhost:3000/claim/",
        "",
        None,
    ],
)
def test_parse_bundle_基底の悪い例(bad):
    raw = _raw()
    raw["bases"]["claim"] = bad
    with pytest.raises(BundleError, match="claim"):
        parse_bundle(raw)


def test_parse_bundle_基底は必須でどの基底か分かる():
    raw = _raw()
    del raw["bases"]["term"]
    with pytest.raises(BundleError, match="term"):
        parse_bundle(raw)
    raw = _raw()
    raw["bases"]["source"] = "https://data.example.net/s/"
    with pytest.raises(BundleError, match="source"):
        parse_bundle(raw)


def test_parse_bundle_ハッシュで終わる基底は通る():
    raw = _raw()
    raw["bases"]["term"] = "https://knowledge.test/vocab#"
    assert parse_bundle(raw).bases["term"].endswith("#")


def test_parse_bundle_悪い_id_は落として数える():
    raw = _raw()
    raw["files"]["claims.json"] += [
        _claim("bad id", "x"),
        _claim("a/b", "x"),
        _claim("", "x"),
        {"title": "id が無い"},
        "文字列の行",
        _claim("c9", "   "),  # 題が空
        _claim("c8", None),
    ]
    raw["files"]["term_claims.json"].append({"term_id": "p1"})
    raw["files"]["term_relations.json"].append(
        {"from_term": "p1", "rel": "bad rel", "to_term": "p3"}
    )
    b = parse_bundle(raw)
    assert [r["id"] for r in b.files["claims.json"]] == ["c1", "c2", "c3"]
    assert b.dropped["claims.json:bad_row"] == 7
    assert b.dropped["term_claims.json:bad_row"] == 1
    assert b.dropped["term_relations.json:bad_row"] == 1


def test_parse_bundle_重複は2行目以降を落とす():
    raw = _raw()
    raw["files"]["claims.json"].append(_claim("c1", "別の題"))
    raw["files"]["term_claims.json"].append({"term_id": "p1", "claim_id": "c1"})
    b = parse_bundle(raw)
    assert len(b.files["claims.json"]) == 3
    assert b.files["claims.json"][0]["title"] == "生地を休ませるとパンが膨らむ"
    assert b.dropped["claims.json:duplicate"] == 1
    assert b.dropped["term_claims.json:duplicate"] == 1


# ---- merge_terms --------------------------------------------------------------------------------


def test_merge_terms_表記ゆれが1語になる():
    cands = [
        _cand("a", "Bread Dough", role="material"),
        _cand("b", "bread  doughs", role="material"),
        _cand("c", "bread-dough"),
        _cand("d", "Rye"),
    ]
    terms, term_of = merge_terms(cands, term_base=TERM)
    assert len(terms) == 2
    assert term_of["a"] == term_of["b"] == term_of["c"] != term_of["d"]
    t = next(t for t in terms if t["id"] == term_of["a"])
    assert t["iri"] == TERM + t["id"]
    assert t["id"].startswith("t-") and len(t["id"]) == 14
    # 別名は名前以外の表記（空白違いは 1 つに）
    assert t["name"] not in t["aliases"]
    assert len(t["aliases"]) == 2


def test_merge_terms_名前と別名():
    cands = [
        _cand("a", "Dough", aliases=["生地", "Dough"]),
        _cand("b", "dough"),
        _cand("c", "dough"),
        _cand("d", "Doughs"),
    ]
    terms, _ = merge_terms(cands, term_base=TERM)
    (t,) = terms
    assert t["name"] == "dough"  # いちばん多い表記
    # normalize_label が同じ "Dough" は名前と同じ扱いで別名に入らない
    assert t["aliases"] == ["Doughs", "生地"]
    # 同数なら短い方
    terms, _ = merge_terms([_cand("a", "Bread Dough"), _cand("b", "bread dough")], term_base=TERM)
    assert terms[0]["name"] == "Bread Dough"  # 同じ長さなら文字の順（大文字が先）


def test_merge_terms_種別と役割():
    # property が 1 つでもあれば property・役割は None
    terms, _ = merge_terms(
        [_cand("a", "熱伝導率", kind="property"), _cand("b", "熱伝導率", role="concept")],
        term_base=TERM,
    )
    assert (terms[0]["kind"], terms[0]["role"]) == ("property", None)
    # 役割は多い順
    terms, _ = merge_terms(
        [_cand("a", "x線", role="method"), _cand("b", "X線", role="material"),
         _cand("c", "X 線", role="material")],
        term_base=TERM,
    )  # fmt: skip
    assert (terms[0]["kind"], terms[0]["role"]) == ("class", "material")
    # 同数なら material → method → concept → other
    terms, _ = merge_terms(
        [_cand("a", "測定", role="other"), _cand("b", "測 定", role="concept")], term_base=TERM
    )
    assert terms[0]["role"] == "concept"
    terms, _ = merge_terms(
        [_cand("a", "測定", role="method"), _cand("b", "測 定", role="material")], term_base=TERM
    )
    assert terms[0]["role"] == "material"
    # 札が全部 None
    terms, _ = merge_terms([_cand("a", "測定"), _cand("b", "測 定")], term_base=TERM)
    assert terms[0]["role"] is None


def test_merge_terms_id_は鍵で決まり順に依らない():
    cands = [_cand("a", "Bread Dough"), _cand("b", "bread dough"), _cand("c", "熱伝導率 (κ)")]
    t1, m1 = merge_terms(cands, term_base=TERM)
    t2, m2 = merge_terms(list(reversed(cands)), term_base=TERM)
    assert t1 == t2
    assert m1 == m2
    # 括弧の有無・全角半角は同じ鍵
    t3, _ = merge_terms([_cand("z", "熱伝導率（κ）")], term_base=TERM)
    assert t3[0]["id"] == next(t["id"] for t in t1 if "熱" in t["name"])
    # 基底が違っても id は同じ
    t4, _ = merge_terms(cands, term_base="https://other.test/t/")
    assert [t["id"] for t in t4] == [t["id"] for t in t1]


def test_merge_terms_鍵が空の名前は落とす_起点は重複なし():
    cands = [
        _cand("a", "(---)"),
        _cand("b", "・"),
        _cand("c", "Bread"),
        {**_cand("d", "bread"), "origin_iri": CLAIM + "c"},
        {**_cand("e", "Bread"), "origin_iri": CLAIM + "c"},
    ]
    terms, term_of = merge_terms(cands, term_base=TERM)
    assert len(terms) == 1
    assert set(term_of) == {"c", "d", "e"}
    assert terms[0]["origin_iri"] == [CLAIM + "c"]


# ---- link_terms ---------------------------------------------------------------------------------


def test_link_terms_origin_と_text():
    claims = [_claim("c1", "生地を休ませる"), _claim("c2", "パン生地の話"), _claim("c3", "無関係")]
    terms, term_of = merge_terms([_cand("p1", "パン生地"), _cand("p2", "生地")], term_base=TERM)
    by_name = {t["name"]: t["id"] for t in terms}
    rows, counted = link_terms(
        terms, claims, [{"term_id": "p1", "claim_id": "c3"}], term_of
    )
    got = {(r["term_id"], r["claim_id"]): r["by"] for r in rows}
    assert got[(by_name["パン生地"], "c3")] == "origin"  # 題に無くても取り込み元の対応は残る
    assert got[(by_name["パン生地"], "c2")] == "text"
    assert got[(by_name["生地"], "c1")] == "text"
    assert (by_name["パン生地"], "c1") not in got
    # 「パン生地」の中の「生地」は当たらない（長い当たりが勝つ）
    assert (by_name["生地"], "c2") not in got
    counts = {t["name"]: t["claim_count"] for t in counted}
    assert counts == {"パン生地": 2, "生地": 1}
    assert rows == sorted(rows, key=lambda r: (r["term_id"], r["claim_id"]))


def test_link_terms_origin_が勝つ():
    claims = [_claim("c1", "パン生地を休ませる")]
    terms, term_of = merge_terms([_cand("p1", "パン生地")], term_base=TERM)
    rows, counted = link_terms(terms, claims, [{"term_id": "p1", "claim_id": "c1"}], term_of)
    assert [(r["by"]) for r in rows] == ["origin"]
    assert counted[0]["claim_count"] == 1


def test_link_terms_知らない語や知見の対応は捨てる_別名と括弧の変形で当てる():
    claims = [_claim("c1", "kappa を測った"), _claim("c2", "Loaf を焼く")]
    terms, term_of = merge_terms(
        [_cand("p1", "熱伝導率 (kappa)"), _cand("p2", "パン", aliases=["Loaf"])], term_base=TERM
    )
    rows, _ = link_terms(
        terms,
        claims,
        [{"term_id": "nope", "claim_id": "c1"}, {"term_id": "p1", "claim_id": "zzz"}],
        term_of,
    )
    by_name = {t["name"]: t["id"] for t in terms}
    assert {(r["term_id"], r["claim_id"]) for r in rows} == {
        (by_name["熱伝導率 (kappa)"], "c1"),
        (by_name["パン"], "c2"),
    }
    assert all(r["by"] == "text" for r in rows)


# ---- 関係 ---------------------------------------------------------------------------------------


def test_merge_relations_落とす3つとまとめる():
    _, term_of = merge_terms(
        [_cand("a", "Bread"), _cand("b", "Loaf"), _cand("c", "Bread Loaves"), _cand("d", "bread")],
        term_base=TERM,
    )
    # a と d は同じ語（鍵が同じ）
    rels = [
        {"from_term": "a", "rel": "has_property", "to_term": "b", "origin_iri": CLAIM + "x"},
        {"from_term": "d", "rel": "has_property", "to_term": "b", "origin_iri": CLAIM + "y"},
        {"from_term": "a", "rel": "has_property", "to_term": "b", "origin_iri": CLAIM + "x"},
        {"from_term": "a", "rel": "no_such_rel", "to_term": "b"},  # 集合に無い
        {"from_term": "a", "rel": "has_property", "to_term": "zzz"},  # 端が束に無い
        {"from_term": "a", "rel": "has_property", "to_term": "d"},  # 同じ語
    ]
    rows, dropped = merge_relations(rels, term_of)
    assert dropped == {
        "term_relations.json:bad_rel": 1,
        "term_relations.json:unknown_term": 1,
        "term_relations.json:same_term": 1,
    }
    assert len(rows) == 1
    r = rows[0]
    assert r["id"] == f"{r['from_term']}.has_property.{r['to_term']}"
    assert r["origin_iri"] == [CLAIM + "x", CLAIM + "y"]


# ---- prepare_files ------------------------------------------------------------------------------


def test_prepare_files_6つのファイルと全キー():
    p = prepare_files(parse_bundle(_raw()))
    assert set(p.files) == {
        "claims.json", "topics.json", "sources.json",
        "terms.json", "term_claims.json", "term_relations.json",
    }  # fmt: skip
    assert "term_candidates.json" not in p.files
    assert p.counts == {k: len(v) for k, v in p.files.items()}
    assert p.counts["claims.json"] == 3
    assert p.counts["terms.json"] == 4
    assert p.counts["term_relations.json"] == 1
    for name, rows in p.files.items():
        assert rows, name
        assert all(list(r) == list(rows[0]) for r in rows), name
    assert list(p.files["terms.json"][0]) == [
        "id", "iri", "name", "kind", "role", "aliases", "origin_iri", "claim_count",
    ]  # fmt: skip
    assert list(p.files["term_claims.json"][0]) == ["term_id", "claim_id", "by"]
    assert list(p.files["term_relations.json"][0]) == [
        "id", "from_term", "rel", "to_term", "origin_iri",
    ]  # fmt: skip


def test_prepare_files_欠けた列は_null_配列は空():
    raw = _raw()
    raw["files"]["claims.json"] = [
        {"id": "c1", "title": "題だけ"},
        _claim("c0", "全部ある", level="x", source_iri=[SOURCE + "s1"]),
    ]
    raw["files"]["topics.json"] = [{"id": "p1", "title": "t", "extra": 1}]
    p = prepare_files(parse_bundle(raw))
    c0, c1 = p.files["claims.json"]  # id の順
    assert (c0["id"], c1["id"]) == ("c0", "c1")
    assert list(c0) == list(c1)
    assert c1["review_state"] is None and c1["source_iri"] == []
    assert "extra" not in p.files["topics.json"][0]
    assert p.files["topics.json"][0]["claim_iri"] == []


def test_prepare_files_順は安定して入力順に依らない():
    raw = _raw()
    a = prepare_files(parse_bundle(raw))
    raw2 = copy.deepcopy(raw)
    for rows in raw2["files"].values():
        rows.reverse()
    b = prepare_files(parse_bundle(raw2))
    assert a.files == b.files


def test_prepare_files_落とした数を持つ():
    raw = _raw()
    raw["files"]["claims.json"].append(_claim("bad id", "x"))
    raw["files"]["term_claims.json"].append({"term_id": "p1", "claim_id": "none"})
    raw["files"]["term_candidates.json"].append(_cand("z", "(--)"))
    raw["files"]["term_relations.json"].append(
        {"from_term": "p1", "rel": "nonsense", "to_term": "p3"}
    )
    p = prepare_files(parse_bundle(raw))
    assert p.dropped["claims.json:bad_row"] == 1
    assert p.dropped["term_claims.json:unknown_ref"] == 1
    assert p.dropped["term_candidates.json:empty_key"] == 1
    assert p.dropped["term_relations.json:bad_rel"] == 1


def test_prepare_files_照合で知見に結ばれる():
    p = prepare_files(parse_bundle(_raw()))
    terms = {t["name"]: t for t in p.files["terms.json"]}
    by = {(r["term_id"], r["claim_id"]): r["by"] for r in p.files["term_claims.json"]}
    assert by[(terms["パン生地"]["id"], "c1")] == "origin"
    assert by[(terms["格子熱伝導率"]["id"], "c2")] == "text"
    assert by[(terms["熱伝導率"]["id"], "c3")] == "text"
    # c2 の「熱伝導率」は「格子熱伝導率」の中だけなので数えない
    assert (terms["熱伝導率"]["id"], "c2") not in by
    assert by[(terms["Bi2Te3"]["id"], "c2")] == "text"
    assert terms["熱伝導率"]["claim_count"] == 1


# ---- design_document ----------------------------------------------------------------------------


def _ir(doc: str) -> dict:
    head = "### 9. Declarative mapping spec"
    body = doc.split(head, 1)[1]
    return yaml.safe_load(body.split("```yaml\n", 1)[1].split("```", 1)[0])


def test_design_document_文書の形とIR():
    b = parse_bundle(_raw())
    doc = design_document(b, name="テスト帳 の知見")
    for h in ("## Schema proposal", "### Class diagram", "```mermaid", "### MIE", "### 9. "):
        assert h in doc
    mie = yaml.safe_load(doc.split("### MIE", 1)[1].split("```yaml\n", 1)[1].split("```", 1)[0])
    assert mie["schema_info"]["title"] == "テスト帳 の知見"
    ir = _ir(doc)
    assert ir["version"] == 1
    assert [m["name"] for m in ir["maps"]] == [
        "claim", "topic", "source", "term", "term_claim", "term_relation",
    ]  # fmt: skip
    assert all(m["optional"] is True for m in ir["maps"])
    assert ir["prefixes"]["kn"] == "https://kumagallium.github.io/asterism/vocab/knowledge#"
    tpl = {m["name"]: m["subject"]["template"] for m in ir["maps"]}
    assert tpl == {
        "claim": CLAIM + "{id}",
        "topic": CLAIM + "{id}",
        "source": SOURCE + "{id}",
        "term": TERM + "{id}",
        "term_claim": CLAIM + "{claim_id}",
        "term_relation": TERM + "{id}",
    }
    sources = {m["name"]: m["source"] for m in ir["maps"]}
    assert sources["term_claim"] == "term_claims.csv"
    mentions_prop = next(m for m in ir["maps"] if m["name"] == "term_claim")["properties"][0]
    assert mentions_prop["predicate"] == "kn:mentions"
    assert mentions_prop["object_template"] == TERM + "{term_id}"
    assert mentions_prop["object_type"] == "iri"


def test_design_document_は_Mapping_IR_として読める():
    doc = design_document(parse_bundle(_raw()), name="x")
    view = read_mapping_ir(yaml.safe_dump(_ir(doc)))
    assert len(view.maps) == 6


def test_design_document_名前に記号があっても_YAML_が壊れない():
    doc = design_document(parse_bundle(_raw()), name='a: "b" #c\n{d}')
    mie = yaml.safe_load(doc.split("### MIE", 1)[1].split("```yaml\n", 1)[1].split("```", 1)[0])
    assert mie["schema_info"]["title"] == 'a: "b" #c\n{d}'


def test_design_document_基底が差し込まれる():
    raw = _raw()
    raw["bases"] = {
        "claim": "https://one.test/c#",
        "source": "https://two.test/s/",
        "term": "https://three.test/t/",
    }
    doc = design_document(parse_bundle(raw), name="n")
    ir = _ir(doc)
    templates = {m["subject"]["template"] for m in ir["maps"]}
    assert "https://one.test/c#{id}" in templates
    assert "https://two.test/s/{id}" in templates
    assert "https://three.test/t/{id}" in templates


# ---- read_copy / term_pairs ---------------------------------------------------------------------


def test_read_copy_と_term_pairs(tmp_path: Path):
    p = prepare_files(parse_bundle(_raw()))
    for name, rows in p.files.items():
        (tmp_path / name).write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    c = read_copy(tmp_path)
    assert isinstance(c, KnowledgeCopy)
    assert len(c.claims) == 3 and len(c.terms) == 4
    assert c.term_relations[0]["rel"] == "has_property"
    pairs = term_pairs(c)
    assert all(a < b for a, b in pairs)
    terms = {t["name"]: t["id"] for t in c.terms}
    key = tuple(sorted((terms["格子熱伝導率"], terms["Bi2Te3"])))
    assert pairs[key] == ["c2"]


def test_read_copy_無いファイルは空_壊れたら_BundleError(tmp_path: Path):
    c = read_copy(tmp_path)
    assert c == KnowledgeCopy()
    (tmp_path / "claims.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(BundleError):
        read_copy(tmp_path)
    (tmp_path / "claims.json").write_text('{"a": 1}', encoding="utf-8")
    with pytest.raises(BundleError):
        read_copy(tmp_path)


def test_term_pairs_知見ごとの全部の組():
    c = KnowledgeCopy(
        term_claims=[
            {"term_id": "c", "claim_id": "k2"},
            {"term_id": "a", "claim_id": "k2"},
            {"term_id": "b", "claim_id": "k2"},
            {"term_id": "a", "claim_id": "k1"},
            {"term_id": "b", "claim_id": "k1"},
            {"term_id": "a", "claim_id": "k3"},
        ]
    )
    assert term_pairs(c) == {("a", "b"): ["k1", "k2"], ("a", "c"): ["k2"], ("b", "c"): ["k2"]}


# ---- 信頼できない束（外のアプリが作る JSON）への備え ---------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "https://knowledge.test/\x00/",  # NUL
        "https://knowledge.test/\x07/",  # 制御文字
        "https://knowledge.test/%zz/",  # 壊れたパーセント符号化
        "https://knowledge.test/100%/",
        "https://user:pw@knowledge.test/claim/",  # 利用者名とパスワード
        "https://example.org./claim/",  # 末尾にドットを付けた仮のホスト
    ],
)
def test_parse_bundle_基底の細工を拒む(bad):
    raw = _raw()
    raw["bases"]["claim"] = bad
    with pytest.raises(BundleError, match="claim"):
        parse_bundle(raw)


def test_parse_bundle_基底は3つとも別の値():
    raw = _raw()
    raw["bases"]["term"] = raw["bases"]["source"]
    with pytest.raises(BundleError, match="別々"):
        parse_bundle(raw)


def test_parse_bundle_ドットだけの_id_と長すぎる_id_は落とす():
    raw = _raw()
    raw["files"]["claims.json"] += [
        _claim(".", "点だけの id"),
        _claim("..", "点 2 つの id"),
        _claim("a" * 201, "長すぎる id"),
    ]
    b = parse_bundle(raw)
    assert [c["id"] for c in b.files["claims.json"]] == ["c1", "c2", "c3"]
    assert b.dropped["claims.json:bad_row"] == 3


def test_parse_bundle_知見と同じ_id_のトピックは落とす():
    raw = _raw()
    raw["files"]["topics.json"].append({**raw["files"]["topics.json"][0], "id": "c1"})
    b = parse_bundle(raw)
    assert [t["id"] for t in b.files["topics.json"]] == ["p1"]
    assert b.dropped["topics.json:id_collision"] == 1


def test_parse_bundle_文字の値から制御文字とサロゲートを除く():
    raw = _raw()
    raw["files"]["claims.json"][0]["title"] = "生地を\x00休ませる\ud800とパンが膨らむ"
    raw["title"] = "テスト\n帳" + "x" * 200
    b = parse_bundle(raw)
    title = b.files["claims.json"][0]["title"]
    assert title == "生地を 休ませるとパンが膨らむ"
    json.dumps(b.files, ensure_ascii=False).encode("utf-8")  # 書き出せる
    assert b.title.startswith("テスト 帳") and len(b.title) == 60


def test_parse_bundle_長すぎる語の候補は落とす():
    raw = _raw()
    raw["files"]["term_candidates.json"].append(_cand("p9", "長" * 201))
    b = parse_bundle(raw)
    assert "p9" not in [c["id"] for c in b.files["term_candidates.json"]]
    assert b.dropped["term_candidates.json:bad_row"] == 1


def test_prepare_files_IRI_の列は形の合うものだけ残し_iri_は作り直す():
    """IRI でない値をそのまま置くと、ストアがその版を丸ごと受け付けない。1 行の細工で写しが
    作れなくならないよう、悪い値だけ落として数える。"""
    raw = _raw()
    c = raw["files"]["claims.json"][0]
    c["iri"] = "https://evil.test/other"  # 束の iri は信じない
    c["source_iri"] = [SOURCE + "s1", "not an iri", "javascript:alert(1)", SOURCE + "s1", 7]
    c["source_url"] = "ftp://x/y z"
    t = raw["files"]["topics.json"][0]
    t["claim_iri"] = ["<" + CLAIM + "c1>", CLAIM + "c1"]
    raw["files"]["term_candidates.json"][0]["origin_iri"] = "nope"
    raw["files"]["term_relations.json"][0]["origin_iri"] = "also nope"
    p = prepare_files(parse_bundle(raw))
    claim = p.files["claims.json"][0]
    assert claim["iri"] == CLAIM + "c1"
    assert claim["source_iri"] == [SOURCE + "s1"]
    assert claim["source_url"] is None
    assert p.files["topics.json"][0]["claim_iri"] == [CLAIM + "c1"]
    assert p.dropped["claims.json:bad_iri"] == 3  # 文字でない値（7）は数えず、ただ捨てる
    assert p.dropped["topics.json:bad_iri"] == 1
    bread = next(x for x in p.files["terms.json"] if x["name"] == "パン生地")
    assert bread["origin_iri"] == []
    assert p.files["term_relations.json"][0]["origin_iri"] == []
