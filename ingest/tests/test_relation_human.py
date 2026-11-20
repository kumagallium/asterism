# ruff: noqa: E501, RUF001
"""人が足す関係（R3a）— ストアに書く・読む・重ねる・使われているかの数え方・B〜D の通し。

実物の ``pyoxigraph.Store`` と registry の実ファイルで動かす。足した関係は作り物。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from test_confirmed_store import (
    SPEC,
    _claims_world,
    _Client,
    _copy,
    _tid,
)

from asterism import crosswalk_runtime as cr
from asterism import graph_diff, themes
from asterism import pair_relations as pr
from asterism import relation_catalog as rc
from asterism import relation_store as rs
from asterism import shared_vocab as sv

pytest.importorskip("pyoxigraph")

AT = "2026-11-20T00:00:00Z"
AT2 = "2026-11-20T01:00:00Z"


@pytest.fixture(autouse=True)
def _reset_human():
    """足した関係は module の入れ物に入る。テストの外へ漏らさない。"""
    rc.set_human((), ())
    yield
    rc.set_human((), ())


def _spec(label: str = "支える", **kw):
    base = {"label": label if isinstance(label, dict) else {"ja": label}, "definition": {"ja": "A が B を力学的に支える"}, "symmetric": False, "to_property_only": False}
    base.update(kw)
    return base


async def _add(c, **kw):
    rel = await rs.add_relation(c, _spec(**kw), at=AT)
    await rs.refresh_human(c)
    return rel


# ---- 名前の形・衝突 ---------------------------------------------------------


def test_組み込みの名前は_u_で始まらない():
    assert not any(r.name.startswith(rs.HUMAN_PREFIX) for r in rc.RELATIONS)
    assert not any(p.name.startswith("u") and p.name[1:].isalnum() and re.fullmatch(r"u[0-9a-f]{8}", p.name) for p in rc.PREDICATES)


async def test_足す_名前と述語は機械が作る_往復で同じ物が読める():
    c = _Client()
    await rs.seed_builtin(c, at=AT)
    rel = await _add(c, label="支える", to_property_only=True, exact_match="https://example.org/ns#supports")
    assert re.fullmatch(r"u_[0-9a-f]{8}", rel.name)
    pred_name = "u" + rel.name[2:]
    assert [p.name for p in rel.predicates] == [pred_name]
    assert rel.predicates[0].iri == rc.REL_NS + pred_name
    assert (rel.builtin, rel.added_by, rel.added_at, rel.retired) == (False, "human", AT, False)
    assert (rel.experiment, rel.paper, rel.question_op, rel.reserve_slug, rel.same_kind, rel.acyclic) == (False, False, None, False, False, False)
    rels, preds = await rs.read_human(c)
    assert rels == [rel] and rc.human_relations() == (rel,)
    (pred,) = preds
    assert pred.knowledge and pred.own is not None and pred.own.definition == "A が B を力学的に支える"
    assert pred.shapes == (rc._EITHER, rc._PROPERTY) and not pred.symmetric
    # 重ねた目録: 組み込みの後ろに足した順
    assert rc.relation_kinds()[-1] == rel.name and rc.relation_kinds()[:12] == tuple(r.name for r in rc.RELATIONS)
    assert rc.pred_to_rel()[pred_name] == rel.name and rc.align_relations()[pred_name] == pred.iri
    assert rc.rel_sentence()["ja"][rel.name] == "{s} → {o}（支える）"
    # JSON: 人の項目にだけ足した欄が出る・組み込みの目録 JSON は変わらない
    js = {r["name"]: r for r in rc.overlay_catalog_to_json()["relations"]}
    assert js[rel.name]["exact_match"] == "https://example.org/ns#supports" and js[rel.name]["added_by"] == "human"
    assert "retired" not in js["sub"] and rc.catalog_to_json() == {"relations": [rc.relation_to_json(r) for r in rc.RELATIONS], "predicates": [rc.predicate_to_json(p) for p in rc.PREDICATES]}
    # ストアの目録（read_catalog）にも出る
    cat = await rs.read_catalog(c)
    assert [r["name"] for r in cat["relations"]][-1] == rel.name


async def test_同じ名前は_409_組み込みも足した関係も_表記ゆれ込み():
    c = _Client()
    await rs.seed_builtin(c, at=AT)
    with pytest.raises(rs.RelationExists, match="既にある関係（競合する）"):
        await rs.add_relation(c, _spec("競合 する"), at=AT)
    with pytest.raises(rs.RelationExists):
        await rs.add_relation(c, _spec({"ja": "x", "en": "Competes With"}), at=AT)
    await _add(c, label="支える")
    with pytest.raises(rs.RelationExists):
        await rs.add_relation(c, _spec("支える"), at=AT)
    assert len((await rs.read_human(c))[0]) == 1


@pytest.mark.parametrize(
    ("spec", "needle"),
    [
        ({"label": {"ja": ""}}, "label"),
        ({"label": {"ja": "あ" * 41}}, "40"),
        ({"label": {"ja": "a\u0007b"}}, "使えない"),
        ({"label": {"ja": "a​b"}}, "使えない"),
        ({"label": {"ja": "<b>x</b>"}}, "使えない"),
        ({"label": {"ja": "{x}"}}, "使えない"),
        ({"label": {"ja": "https://x.test/a"}}, "URL"),
        ({"label": {"ja": "foo"}, "definition": {}}, "definition"),
        ({"definition": {"ja": "あ" * 201}}, "200"),
        ({"definition": {"ja": "see http://evil.test"}}, "URL"),
        ({"definition": {"fr": "x"}}, "言語"),
        ({"exact_match": "supports"}, "exact_match"),
        ({"exact_match": "ftp://x.test/a"}, "exact_match"),
        ({"exact_match": rc.REL_NS + "supports"}, "exact_match"),
        ({"exact_match": "https://x.test/a b"}, "exact_match"),
        ({"sentence": {"ja": "{s} が出る"}}, "{s} と {o}"),
    ],
)
async def test_入力の検査(spec, needle):
    c = _Client()
    with pytest.raises(rs.RelationInputError, match=re.escape(needle)):
        await rs.add_relation(c, _spec(**spec), at=AT)
    assert len(c.store) == 0  # 何も書かれていない


async def test_NFKC_で整える_文は書かれたものが優先():
    c = _Client()
    rel = await rs.add_relation(
        c, _spec(label="ｓｕｐｐｏｒｔ　ｓ", sentence={"ja": "{s} は {o} を支える"}), at=AT
    )
    assert rel.label == {"ja": "support s"}
    assert rel.sentence["ja"] == "{s} は {o} を支える" and rel.sentence["en"] == "{s} → {o} (support s)"


# ---- 組み込みに触れない・再種まき・1 回の更新 --------------------------------


async def test_組み込みの項目には触れない_再種まきでも足した関係は消えない():
    c = _Client()
    await rs.seed_builtin(c, at=AT)
    before = await rs.read_catalog(c, builtin_only=True)
    rel = await _add(c)
    assert await rs.read_catalog(c, builtin_only=True) == before == rc.catalog_to_json()
    for call in (
        rs.update_relation(c, "sub", label={"ja": "x"}),
        rs.retire_relation(c, "sub", at=AT),
        rs.delete_relation(c, "competes"),
    ):
        with pytest.raises(rs.RelationForbidden):
            await call
    with pytest.raises(rs.RelationNotFound):
        await rs.retire_relation(c, "u_00000000", at=AT)
    # 組み込みが変わった（種が違う）ので再種まきが走っても、足した関係は残る
    c.store.update(f"DELETE WHERE {{ GRAPH <{rs.CATALOG_GRAPH}> {{ <{rs.relation_iri('sub')}> ?p ?o }} }}")
    assert await rs.seed_builtin(c, at=AT2) is True
    assert (await rs.read_human(c))[0] == [rel]
    assert await rs.read_catalog(c, builtin_only=True) == rc.catalog_to_json()


async def test_書く口は_1_回の更新():
    c = _Client()
    await rs.seed_builtin(c, at=AT)
    counted = _Counting(c)
    rel = await rs.add_relation(counted, _spec(), at=AT)
    assert counted.updates == 1
    await rs.update_relation(counted, rel.name, definition={"ja": "別の説明"})
    assert counted.updates == 2
    await rs.retire_relation(counted, rel.name, at=AT2)
    assert counted.updates == 3
    await rs.retire_relation(counted, rel.name, at=AT2)  # 何度呼んでも同じ（書かない）
    assert counted.updates == 3
    await rs.delete_relation(counted, rel.name)
    assert counted.updates == 4
    assert await rs.read_human(c) == ([], [])


class _Counting:
    def __init__(self, inner):
        self.inner, self.updates = inner, 0

    async def sparql_update(self, q):
        self.updates += 1
        await self.inner.sparql_update(q)

    async def sparql_select(self, q):
        return await self.inner.sparql_select(q)


# ---- 直す・やめる -----------------------------------------------------------


async def test_直せるのは表示名と文と説明だけ_名前と述語と性質は変わらない():
    c = _Client()
    await rs.seed_builtin(c, at=AT)
    rel = await _add(c, label="支える", symmetric=True, to_property_only=True)
    new = await rs.update_relation(c, rel.name, label={"ja": "支持する", "en": "supports"}, definition={"en": "A supports B"})
    assert new.name == rel.name and new.predicates == rel.predicates
    assert (new.symmetric_pair, new.to_property_only, new.added_at) == (True, True, AT)
    assert new.label == {"ja": "支持する", "en": "supports"} and new.definition == {"en": "A supports B"}
    # 既定の文のままだった言語は新しい表示名で作り直る
    assert new.sentence == {"ja": "{s} ↔ {o}（支持する）", "en": "{s} ↔ {o} (supports)"}
    (back,), (pred,) = await rs.read_human(c)
    assert back == new and pred.own.label == new.label and pred.own.definition == "A supports B"
    # exact_match は渡せば変わり None で消える
    assert (await rs.update_relation(c, rel.name, exact_match="https://example.org/x")).exact_match == "https://example.org/x"
    assert (await rs.update_relation(c, rel.name, exact_match=None)).exact_match is None
    # 並びは変わらない
    other = await _add(c, label="受ける")
    assert [r.name for r in rc.human_relations()] == [rel.name, other.name]
    await rs.update_relation(c, rel.name, label={"ja": "支持する 2"})
    await rs.refresh_human(c)
    assert [r.name for r in rc.human_relations()] == [rel.name, other.name]
    with pytest.raises(rs.RelationExists):  # 別の関係の名前にはできない
        await rs.update_relation(c, rel.name, label={"ja": "受ける"})


async def test_retired_は選べる集合から外れ_読める集合には残る():
    c = _Client()
    rel = await _add(c)
    pred = rel.predicates[0].name
    assert rel.name in rc.assignable_relation_kinds() and pred in rc.assignable_predicates()
    out = await rs.retire_relation(c, rel.name, at=AT2)
    await rs.refresh_human(c)
    assert (out.retired, out.retired_at) == (True, AT2)
    assert rel.name not in rc.assignable_relation_kinds() and pred not in rc.assignable_predicates()
    assert rel.name in rc.relation_kinds() and rc.pred_to_rel()[pred] == rel.name  # 読み戻しに残る
    assert rel.name in rc.rel_sentence()["ja"] and pred in rc.align_relations()
    assert rc.is_retired(rel.name) and not rc.is_retired("sub")
    again = await rs.retire_relation(c, rel.name, at="2027-01-01T00:00:00+00:00")
    assert again.retired_at == AT2  # 何度呼んでも同じ
    # 新しくは付けられない（assign）
    led = pr.empty_ledger("lab")
    led["pairs"]["k|l"] = {"key": "k|l", "a": "k", "b": "l", "state": "none", "rel": None, "from": None, "to": None, "evidence": [], "claims": ["c1"], "claims_total": 1, "by": "ai", "confirmed": None, "rejected": [], "fingerprint": "f"}
    terms = {"k": {"id": "k", "name": "K", "kind": "class"}, "l": {"id": "l", "name": "L", "kind": "property"}}
    _, res = pr.assign(led, terms, "k|l", rel.name, "a", at=AT)
    assert res["skipped"] == [{"key": "k|l", "reason": "retired"}]


# ---- 使われているかの数え方・削除 -------------------------------------------


async def test_使われているかの_3_種と削除の可否(tmp_path: Path):
    c = _Client()
    await rs.seed_builtin(c, at=AT)
    root = tmp_path / "registry"
    (root / "vocab-shared" / "knowledge").mkdir(parents=True)
    rel = await _add(c)
    pred = rel.predicates[0]
    assert await rs.usage(c, rel.name, registry_root=root) == {"lines": 0, "ledger": 0, "drafts": 0}
    # (a) 確定の線
    c.store.update(
        f"INSERT DATA {{ GRAPH <{cr.ALIGNMENT_GRAPH}> {{ <https://x.test/a> <{pred.iri}> <https://x.test/b> }} }}"
    )
    assert (await rs.usage(c, rel.name, registry_root=root))["lines"] == 1
    with pytest.raises(rs.RelationInUse) as ei:
        await rs.delete_relation(c, rel.name, registry_root=root)
    assert ei.value.usage == {"lines": 1, "ledger": 0, "drafts": 0}
    c.store.update(f"DELETE WHERE {{ GRAPH <{cr.ALIGNMENT_GRAPH}> {{ ?s <{pred.iri}> ?o }} }}")
    # (b) 台帳
    (root / "vocab-shared" / "knowledge" / "lab.json").write_text(
        json.dumps({"pairs": {"a|b": {"rel": rel.name}, "c|d": {"rel": "sub"}}, "origin": [{"rel": rel.name}]})
    )
    assert (await rs.usage(c, rel.name, registry_root=root))["ledger"] == 2
    (root / "vocab-shared" / "knowledge" / "lab.json").unlink()
    # (c) 話の下書き・却下の記録
    (root / "vocab-shared" / "themes.json").write_text(json.dumps({"themes": [{"relations": [{"rel": rel.name}]}]}))
    (root / "vocab-shared" / "dismissed.json").write_text(json.dumps([{"subject": "s", "term": "t", "relation": pred.name}]))
    assert (await rs.usage(c, rel.name, registry_root=root))["drafts"] == 2
    with pytest.raises(rs.RelationInUse):
        await rs.delete_relation(c, rel.name, registry_root=root)
    for f in ("themes.json", "dismissed.json"):
        (root / "vocab-shared" / f).unlink()
    # 全部 0 → 消せる。目録の項目だけが消える
    await rs.delete_relation(c, rel.name, registry_root=root)
    assert await rs.read_human(c) == ([], [])
    assert await rs.read_catalog(c, builtin_only=True) == rc.catalog_to_json()
    assert not [q for q in c.store if rel.name in str(q.subject) or pred.name in str(q.subject)]


# ---- 0 件のとき組み込みだけの値と同じ ---------------------------------------


def test_足した関係が_0_件なら導いた値は組み込みだけのときと同じ():
    R, P = rc.RELATIONS, rc.PREDICATES
    assert rc.human_relations() == () and rc.overlay_catalog_to_json() == rc.catalog_to_json()
    assert rc.relation_kinds() == tuple(r.name for r in R) == rc.assignable_relation_kinds()
    assert rc.align_relations() == {p.name: p.iri for p in P}
    assert rc.pred_to_rel() == {p.name: r.name for r in R for p in r.predicates}
    assert rc.knowledge_relations() == tuple(p.name for p in P if p.knowledge)
    assert rc.symmetric_predicates() == frozenset(p.name for p in P if p.symmetric)
    assert rc.symmetric_pair_relations() == tuple(r.name for r in R if r.symmetric_pair)
    assert rc.to_property_only_relations() == tuple(r.name for r in R if r.to_property_only)
    assert rc.relation_shapes() == {p.name: p.shapes for p in P}
    assert list(rc.relation_shapes(order=tuple(p.name for p in P))) == [p.name for p in P]
    assert rc.own_relations().keys() == {"competesWith"} and rc.role_rules().keys() == {p.name for p in P if p.roles}
    assert rc.rel_sentence()["ja"] == {r.name: r.sentence["ja"] for r in R}
    assert dict(cr.ALIGN_RELATIONS) == rc.align_relations() and dict(graph_diff.PRED_TO_REL) == rc.pred_to_rel()
    assert set(cr.RELATION_SHAPES) == set(cr.ALIGN_RELATIONS) and themes.relation_predicate("sub", "class") == "subClassOf"


async def test_束ねた定数にも足した関係が届く():
    from asterism import knowledge

    c = _Client()
    align = cr.ALIGN_RELATIONS  # from … import で束ねる読み手と同じ実体
    assert type(align) is dict and len(align) == 15
    rel = await _add(c)
    pred = rel.predicates[0]
    assert align[pred.name] == pred.iri and len(align) == 16 and list(align)[-1] == pred.name
    assert knowledge.ALIGN_RELATIONS is align
    assert pred.name in cr.RELATION_SHAPES and pred.name in dict(graph_diff.PRED_TO_REL)
    rc.set_human((), ())
    assert len(align) == 15 and pred.name not in cr.RELATION_SHAPES and pred.name not in graph_diff.PRED_TO_REL


# ---- B・C・D の通し ----------------------------------------------------------


def _pair(a: str, b: str) -> dict:
    x, y = sorted((a, b))
    return {"key": f"{x}|{y}", "a": x, "b": y, "state": "none", "rel": None, "from": None, "to": None, "evidence": [], "claims": ["c1", "c2"], "claims_total": 2, "by": "ai", "confirmed": None, "rejected": [], "fingerprint": "f"}


async def test_足した関係で_付ける_確定する_読み戻す(tmp_path: Path):
    c, root = _Client(), tmp_path / "registry"
    await rs.seed_builtin(c, at=AT)
    await _claims_world(c, root)
    rel = await _add(c, label="支える", to_property_only=True)
    pred = rel.predicates[0].name
    copy = _copy(SPEC)  # 1 気孔(class)・2 熱の伝わりやすさ(property)・3 粒界(class)
    led = pr.empty_ledger("lab")
    for a, b in ((1, 2), (1, 3)):
        p = _pair(_tid(a), _tid(b))
        led["pairs"][p["key"]] = p
    # B: 相手が項目なら付く・class なら断る（項目に限る関係）
    k12, k13 = (_pair(_tid(1), _tid(2))["key"], _pair(_tid(1), _tid(3))["key"])
    _, res = pr.assign(led, copy.terms, k13, rel.name, "a", at=AT)
    assert res["skipped"] == [{"key": k13, "reason": "target_not_property"}]
    led, res = pr.assign(led, copy.terms, k12, rel.name, "a", at=AT)
    assert res["changed"] == [k12]
    e = led["pairs"][k12]
    assert (e["state"], e["rel"], e["from"], e["to"]) == ("relation", rel.name, _tid(1), _tid(2))
    assert pr.relation_problem(rel.name, {"id": "x", "kind": "class"}, {"id": "y", "kind": "class"}) == "target_not_property"
    assert themes.relation_predicate(rel.name, "class", "property") == pred
    # C: 確定してストアに線が置ける
    from asterism import confirmed_store as cs

    _new, rep = await cs.sync(c, root, led, copy, claim_graph="https://example.org/graph/lab-copy", now=AT2)
    assert rep.summary()["placed"] == 1 and rep.summary()["failed"] == []
    lines = await sv._alignment_lines(c)  # D: 読み戻しで関係の名前（述語）に戻る
    assert [ln[1] for ln in lines] == [pred]
    assert graph_diff.PRED_TO_REL[pred] == rel.name and graph_diff.REL_SENTENCE["ja"][rel.name].startswith("{s}")
    counts = await sv.knowledge_counts(c)
    assert len(counts) == 2 and set(counts.values()) == {1}
    assert (await rs.usage(c, rel.name, registry_root=root))["lines"] == 1  # 使われている→消せない
    with pytest.raises(rs.RelationInUse):
        await rs.delete_relation(c, rel.name, registry_root=root)


async def test_足した関係の端の検査は_cr_assert_alignment_でも効く():
    c = _Client()
    rel = await _add(c, label="支える", to_property_only=True)
    pred = rel.predicates[0].name
    a, b, k = sv.term_iri("aa"), sv.term_iri("bb"), sv.term_iri("kk")
    g = f"INSERT DATA {{ GRAPH <{sv.SHARED_VOCAB_GRAPH}> {{ <{a}> a <http://www.w3.org/2000/01/rdf-schema#Class> . <{b}> a <http://www.w3.org/2000/01/rdf-schema#Class> . <{k}> a <http://www.w3.org/1999/02/22-rdf-syntax-ns#Property> }} }}"
    c.store.update(g)
    with pytest.raises(ValueError, match="kind mismatch"):
        await cr.assert_alignment(c, a, b, pred, at=AT)  # 相手が種類
    out = await cr.assert_alignment(c, a, k, pred, at=AT)
    assert out["relation"] == pred
    with pytest.raises(ValueError, match="relation must be one of"):
        await cr.assert_alignment(c, a, k, "u00000000", at=AT)


async def test_向きの無い関係は逆向きを同じとして扱う():
    c = _Client()
    rel = await _add(c, label="仲間", symmetric=True)
    pred = rel.predicates[0].name
    assert themes.draft_rel_key("b", rel.name, "a") == ("a", rel.name, "b")
    assert cr.canonical_ends(pred, "https://x.test/b", "https://x.test/a") == ("https://x.test/a", "https://x.test/b")
    assert pr._order(rel.name, "t2", "t1") == ("t1", "t2")
    row = {"from": "t2", "rel": rel.name, "to": "t1"}
    pairs = {"t1|t2": {"state": "relation", "rel": rel.name, "from": "t1", "to": "t2"}}
    assert pr.origin_check(row, pairs) == "agrees"
    assert pred in rc.symmetric_predicates() and rc.cycle_rels(pred) is None


async def test_起動時に読めなくても足した関係_0_件として動く():
    class _Broken:
        async def sparql_select(self, q):
            raise RuntimeError("down")

    rel_ok = _Client()
    await _add(rel_ok)
    assert len(rc.human_relations()) == 1
    assert await rs.refresh_human(_Broken()) == 0 and rc.human_relations() == ()


def test_unstated_groups_の_in_catalog_は足した関係も見る():
    from asterism import unstated_groups as ug

    rel = rc.Relation(
        name="u_00000001", label={"ja": "支える"}, sentence={"ja": "{s}", "en": "{s}"},
        predicates=(rc.PredicateRule("u00000001", rc.REL_NS + "u00000001"),), builtin=False, retired=True,
    )
    rc.set_human([rel], [])
    keys = ug._catalog_keys()
    from asterism.term_match import merge_key

    assert keys[merge_key("支える")] == "u_00000001" and keys[merge_key("u_00000001")] == "u_00000001"
    assert keys[merge_key("競合する")] == "competes"  # 組み込みが先に勝つ
