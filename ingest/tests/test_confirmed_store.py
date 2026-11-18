# ruff: noqa: E501
"""asterism.confirmed_store — 台帳の確認済みの関係をストアの正本に置く（PR 14e-1b）。

実物の ``pyoxigraph.Store`` と registry の実ファイルで動かす。例は作り物。
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path
from typing import Any

import pytest

from asterism import confirmed_store as cs
from asterism import evidence, knowledge, themes
from asterism import knowledge_sources as ksrc
from asterism import pair_relations as pr
from asterism import shared_vocab as sv
from asterism.crosswalk_runtime import ALIGNMENT_GRAPH, assert_alignment
from asterism.knowledge_bundle import KN_NS, KnowledgeCopy

pyoxigraph = pytest.importorskip("pyoxigraph")

AT = "2026-11-18T00:00:00+00:00"
AT2 = "2026-11-18T01:00:00+00:00"
CLAIM = "https://example.org/kn/claim/"
GRAPH = "https://example.org/graph/lab-copy"
RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"


class _Client:
    def __init__(self) -> None:
        self.store = pyoxigraph.Store()

    async def sparql_update(self, update: str) -> None:
        self.store.update(update)

    async def sparql_select(self, query: str) -> dict:
        result = self.store.query(query, use_default_graph_as_union=True)
        if isinstance(result, bool):
            return {"boolean": result}
        names = [v.value for v in result.variables]
        bindings = []
        for solution in result:
            row: dict[str, Any] = {}
            for name in names:
                term = solution[name]
                if term is None:
                    continue
                kind = "uri" if isinstance(term, pyoxigraph.NamedNode) else "literal"
                row[name] = {"type": kind, "value": term.value}
            bindings.append(row)
        return {"results": {"bindings": bindings}}


def _tid(n: int) -> str:
    return f"t-{n:012x}"


def _copy(specs: list[tuple[int, str, str, str | None]], claims: int = 3) -> KnowledgeCopy:
    """``(番号, 名前, class|property, 役割)`` の語と、知見 c1..cN。"""
    return KnowledgeCopy(
        terms=[
            {"id": _tid(n), "name": name, "kind": kind, "role": role, "aliases": []}
            for n, name, kind, role in specs
        ],
        claims=[
            {"id": f"c{i}", "iri": f"{CLAIM}c{i}", "title": f"作り物の知見 {i}"}
            for i in range(1, claims + 1)
        ],
    )


SPEC = [
    (1, "気孔", "class", "concept"),
    (2, "熱の伝わりやすさ", "property", None),
    (3, "粒界", "class", "concept"),
    (4, "焼成", "class", "method"),
    (5, "合金A", "class", "material"),
]


def _ledger(source: str = "lab", rels: list[tuple[int, int, str]] | None = None) -> dict:
    led = pr.empty_ledger(source)
    for a, b, rel in rels or []:
        x, y = sorted((_tid(a), _tid(b)))
        key = f"{x}|{y}"
        led["pairs"][key] = {
            "key": key, "a": x, "b": y, "state": "relation", "rel": rel,
            "from": _tid(a), "to": _tid(b), "evidence": ["c1", "c2"], "by": "ai",
            "reason": "同じ知見に出る", "claims": [], "claims_total": 0, "rejected": [],
            "confirmed": None, "fingerprint": "f",
        }  # fmt: skip
    return led


def _keys(led: dict) -> list[str]:
    return sorted(led["pairs"])


def _confirm(led: dict, keys: list[str], at: str = AT) -> dict:
    new, out = pr.confirm(led, keys, at=at)
    assert out["skipped"] == []
    return new


async def _claims_world(c: _Client, root: Path, *, status: str | None = None) -> None:
    """公開済みの写し（知見の graph）と、registry の meta。"""
    for i in (1, 2):
        c.store.update(
            f"INSERT DATA {{ GRAPH <{GRAPH}> {{ <{CLAIM}c{i}> a <{KN_NS}Claim> ; "
            f'<{RDFS_LABEL}> "作り物の知見 {i}" ; <{KN_NS}sourceUrl> <https://example.org/n/{i}> }} }}'
        )
    meta: dict[str, Any] = {
        "id": "lab-copy", "name": "写し", "promoted": True, "source_kind": "json",
        "live_graph": GRAPH, "knowledge_source": {"name": "lab", "title": "作り物の束"},
    }  # fmt: skip
    if status:
        meta["status"] = status
    (root / "lab-copy").mkdir(parents=True, exist_ok=True)
    (root / "lab-copy" / "meta.json").write_text(json.dumps(meta, ensure_ascii=False))


async def _run(
    c: _Client, root: Path, led: dict, copy: KnowledgeCopy, **kw: Any
) -> tuple[dict, cs.Report]:
    new, rep = await cs.sync(c, root, led, copy, claim_graph=GRAPH, now=AT2, **kw)
    pr.save_ledger(root, new, at=AT2)
    return new, rep


def _snapshot(c: _Client, root: Path) -> dict:
    quads = {(str(q.graph_name), str(q.subject), str(q.predicate), str(q.object)) for q in c.store}
    # 来歴の時刻（``store.at``）は台帳の側。ストアに入るのは確認の時刻
    files = {
        p.name: p.read_text(encoding="utf-8")
        for p in sorted((root / "vocab-shared").glob("*.yaml"))
    }
    return {"quads": quads, "files": files}


def _my_lines(c: _Client, batch: str) -> set[str]:
    rows = c.store.query(
        f"SELECT ?s ?r ?t WHERE {{ GRAPH <{ALIGNMENT_GRAPH}> {{ ?a <{cs.XW}batch> \"{batch}\" ; "
        f"<{cs.XW}alignSource> ?s ; <{cs.XW}alignTarget> ?t ; <{cs.XW}alignRelation> ?r }} }}",
        use_default_graph_as_union=True,
    )
    return {"|".join((x["s"].value, x["r"].value, x["t"].value)) for x in rows}


def _terms_in_store(c: _Client) -> set[str]:
    rows = c.store.query(
        f"SELECT ?s WHERE {{ GRAPH <{sv.SHARED_VOCAB_GRAPH}> {{ ?s a ?t }} }}",
        use_default_graph_as_union=True,
    )
    return {x["s"].value for x in rows}


# ---- 名前・slug --------------------------------------------------------------------------------


def test_slug_is_deterministic_and_not_reserved_or_placeholder() -> None:
    from asterism.graph_diff import _PLACEHOLDER_SLUG

    assert cs.slug_for("t-0123456789ab") == "kt_0123456789ab"
    assert cs.slug_for("t0") == cs.slug_for("t0")
    for i in range(300):
        slug = cs.slug_for(f"t-{i:012x}")
        assert slug not in sv.RESERVED_SLUGS
        assert not _PLACEHOLDER_SLUG.match(slug)
        sv.term_iri(slug)  # slug の形を通る


def test_batch_name_is_cut_to_100_chars_with_a_hash() -> None:
    assert cs.batch_name("lab") == "knowledge:lab"
    long = "x" * 300
    b = cs.batch_name(long)
    assert len(b) <= 100 and b.startswith("knowledge:")
    assert b == cs.batch_name(long) and b != cs.batch_name("x" * 301)


# ---- 置く ---------------------------------------------------------------------------------------


async def test_confirmed_relation_lands_with_terms_line_and_evidence(tmp_path: Path) -> None:
    root, c = tmp_path / "registry", _Client()
    await _claims_world(c, root)
    copy = _copy(SPEC)
    led = _confirm(_ledger(rels=[(1, 2, "lowers")]), _keys(_ledger(rels=[(1, 2, "lowers")])))
    new, rep = await _run(c, root, led, copy)
    assert rep.summary() == {"placed": 1, "removed": 0, "failed": []}
    s1, s2 = cs.slug_for(_tid(1)), cs.slug_for(_tid(2))
    assert {sv.term_iri(s1), sv.term_iri(s2)} <= _terms_in_store(c)
    assert new["stored_terms"] == {
        _tid(1): {"slug": s1, "minted": True}, _tid(2): {"slug": s2, "minted": True},
    }  # fmt: skip
    entry = next(iter(new["pairs"].values()))
    assert entry["store"]["ok"] is True and entry["confirmed"]  # 台帳の確認は残る
    # 役割は写しの語のまま・機械が付けた
    terms = {t.slug: t for t in await sv._read_terms(c)}
    assert (terms[s1].role, terms[s1].role_by) == ("concept", "machine")
    # 来歴: batch・drafted_by（AI の下書きを人が確認）・at＝確認の時刻・根拠
    from asterism.crosswalk_runtime import list_alignments

    (row,) = await list_alignments(c)
    assert row["batch"] == "knowledge:lab" and row["drafted_by"] == "ai"
    assert row["at"].startswith("2026-11-18T00:00:00") and row["reason"] == "同じ知見に出る"
    assert row["evidence"] == [f"{CLAIM}c1", f"{CLAIM}c2"]
    # 答えに出る（kinds ではなく factors: s2 を下げる側）
    ans = await knowledge.knowledge_answer(
        c, {"op": "factors", "target": sv.term_iri(s2), "direction": "lowers"}
    )
    assert ans["count"] == 1


async def test_evidence_missing_from_the_copy_graph_is_dropped_but_the_line_is_placed(
    tmp_path: Path,
) -> None:
    root, c = tmp_path / "registry", _Client()
    await _claims_world(c, root)  # c1・c2 だけ在る
    led = _ledger(rels=[(1, 2, "lowers")])
    next(iter(led["pairs"].values()))["evidence"] = ["c2", "c3"]  # c3 は graph に無い
    led = _confirm(led, _keys(led))
    await _run(c, root, led, _copy(SPEC))
    from asterism.crosswalk_runtime import list_alignments

    (row,) = await list_alignments(c)
    assert row["evidence"] == [f"{CLAIM}c2"]


async def test_same_ledger_twice_gives_the_same_store(tmp_path: Path) -> None:
    root, c = tmp_path / "registry", _Client()
    await _claims_world(c, root)
    led = _ledger(rels=[(1, 2, "lowers"), (1, 3, "part"), (5, 2, "has_property")])
    led = _confirm(led, _keys(led))
    copy = _copy(SPEC)
    first, _ = await _run(c, root, led, copy)
    snap = _snapshot(c, root)
    second, rep = await _run(c, root, first, copy)
    assert rep.summary() == {"placed": 0, "removed": 0, "failed": []}
    assert _snapshot(c, root) == snap
    # 台帳に書き戻さずに、もう一度同じ台帳から流しても同じ（assert は冪等）
    await cs.sync(c, root, led, copy, claim_graph=GRAPH, now=AT2)
    assert _snapshot(c, root) == snap
    assert second["stored_terms"] == first["stored_terms"]


async def test_unconfirm_and_reject_take_the_line_and_the_minted_terms_out(
    tmp_path: Path,
) -> None:
    root, c = tmp_path / "registry", _Client()
    await _claims_world(c, root)
    led = _ledger(rels=[(1, 2, "lowers"), (1, 3, "part")])
    k_low, k_part = "|".join(sorted((_tid(1), _tid(2)))), "|".join(sorted((_tid(1), _tid(3))))
    led = _confirm(led, [k_low, k_part])
    copy = _copy(SPEC)
    led, _ = await _run(c, root, led, copy)
    assert len(_my_lines(c, "knowledge:lab")) == 2
    # 取り消し → 線が 1 本消える・語 2（その線だけが使う）は消え、語 1 は残る
    led, out = pr.unconfirm(led, [k_low])
    led, rep = await _run(c, root, led, copy)
    assert len(rep.removed) == 1 and len(_my_lines(c, "knowledge:lab")) == 1
    assert sv.term_iri(cs.slug_for(_tid(2))) not in _terms_in_store(c)
    assert sv.term_iri(cs.slug_for(_tid(1))) in _terms_in_store(c)
    assert _tid(2) not in led["stored_terms"]
    assert "store" not in led["pairs"][k_low]  # 確認が外れた件の store は消える
    # 「違う」 → 全部外れて、作った語も無くなる
    led, _ = pr.reject(led, [k_part], at=AT2)
    led, _ = await _run(c, root, led, copy)
    assert _my_lines(c, "knowledge:lab") == set()
    assert not [t for t in _terms_in_store(c) if "/kt_" in t]
    assert led["stored_terms"] == {}
    # 戻す（AI の関係は未確認に戻る）→ 確認し直すと、もう一度置かれる
    led, out = pr.restore(led, [k_part])
    assert out["changed"] == [k_part]
    led, rep = await _run(c, root, led, copy)
    assert rep.placed == [] and _my_lines(c, "knowledge:lab") == set()
    led = _confirm(led, [k_part], at=AT2)
    led, rep = await _run(c, root, led, copy)
    assert rep.placed == [k_part] and len(_my_lines(c, "knowledge:lab")) == 1


async def test_an_existing_term_is_used_as_is_and_survives_the_cancel(tmp_path: Path) -> None:
    root, c = tmp_path / "registry", _Client()
    await _claims_world(c, root)
    await sv.mint_term(
        c, root, slug="pore", kind="class", label="気孔", cqs=[], theme_id="x", at=AT,
        aliases=["ポア"], role="concept", role_by="human",
    )  # fmt: skip
    before = _snapshot(c, root)["quads"]
    pore_triples = {q for q in before if q[1] == sv.term_iri("pore")}
    led = _ledger(rels=[(1, 2, "lowers")])
    keys = _keys(led)
    led = _confirm(led, keys)
    copy = _copy(SPEC)
    led, _ = await _run(c, root, led, copy)
    assert led["stored_terms"][_tid(1)] == {"slug": "pore", "minted": False}
    assert sv.term_iri(cs.slug_for(_tid(1))) not in _terms_in_store(c)  # 同じ語を作らない
    after = _snapshot(c, root)["quads"]
    # 既存の語は書き換わらない（別名も役割も足さない・来歴も同じ）
    assert {q for q in after if q[1] == sv.term_iri("pore")} == pore_triples
    led, _ = pr.unconfirm(led, keys)
    led, _ = await _run(c, root, led, copy)
    assert sv.term_iri("pore") in _terms_in_store(c)
    assert {q for q in _snapshot(c, root)["quads"] if q[1] == sv.term_iri("pore")} == pore_triples


async def test_a_line_someone_else_confirmed_is_not_taken_out(tmp_path: Path) -> None:
    root, c = tmp_path / "registry", _Client()
    await _claims_world(c, root)
    copy = _copy(SPEC)
    keys = _keys(_ledger(rels=[(1, 2, "lowers")]))
    a = _confirm(_ledger("lab", [(1, 2, "lowers")]), keys)
    a, _ = await _run(c, root, a, copy)
    b = _confirm(_ledger("memo", [(1, 2, "lowers")]), keys)
    b, rep_b = await _run(c, root, b, copy)  # 同じ線は置き直さない・語は a の物に乗る
    assert rep_b.placed == [] and b["stored_terms"][_tid(1)]["minted"] is False
    a, _ = pr.unconfirm(a, keys)
    a, rep = await _run(c, root, a, copy)
    assert rep.removed == [] and len(_my_lines(c, "knowledge:lab")) == 1
    # a の語は b が使っているので残る。b も外せば、線も語も無くなる
    b, _ = pr.unconfirm(b, keys)
    b, rep = await _run(c, root, b, copy)
    assert len(rep.removed) == 1 and _my_lines(c, "knowledge:lab") == set()
    a, _ = await _run(c, root, a, copy)  # a の台帳はもう何も確認していない
    assert _my_lines(c, "knowledge:lab") == set()
    assert not [t for t in _terms_in_store(c) if "/kt_" in t]


async def test_a_line_a_theme_owns_is_left_alone(tmp_path: Path) -> None:
    root, c = tmp_path / "registry", _Client()
    await _claims_world(c, root)
    for t in (1, 2):
        await sv.mint_term(c, root, slug=f"x{t}", kind="class" if t == 1 else "property",
                           label=("気孔", "熱の伝わりやすさ")[t - 1], cqs=[], theme_id="th", at=AT)  # fmt: skip
    await assert_alignment(c, sv.term_iri("x1"), sv.term_iri("x2"), "lowers", at=AT,
                           drafted_by="human", batch="theme-1")  # fmt: skip
    led = _ledger(rels=[(1, 2, "lowers")])
    keys = _keys(led)
    led = _confirm(led, keys)
    copy = _copy(SPEC)
    led, rep = await _run(c, root, led, copy)
    assert rep.placed == [] and led["pairs"][keys[0]]["store"]["ok"] is True
    led, _ = pr.unconfirm(led, keys)
    led, rep = await _run(c, root, led, copy)
    assert rep.removed == []  # 話の線は外さない
    assert _my_lines(c, "theme-1")


async def test_role_mismatch_is_kept_in_the_ledger_with_a_reason(tmp_path: Path) -> None:
    root, c = tmp_path / "registry", _Client()
    await _claims_world(c, root)
    # 既存の「合金A」は手法の札（makes の相手は材料でなければならない）
    await sv.mint_term(c, root, slug="alloy", kind="class", label="合金A", cqs=[], theme_id="x",
                       at=AT, role="method", role_by="human")  # fmt: skip
    led = _ledger(rels=[(4, 5, "makes")])
    keys = _keys(led)
    led = _confirm(led, keys)
    led, rep = await _run(c, root, led, _copy(SPEC))
    assert rep.failed == [{"key": keys[0], "reason": "role_mismatch"}]
    e = led["pairs"][keys[0]]
    assert e["confirmed"] and e["store"]["ok"] is False and e["store"]["reason"] == "role_mismatch"
    assert _my_lines(c, "knowledge:lab") == set()
    assert sv.term_iri(cs.slug_for(_tid(4))) not in _terms_in_store(c)  # 使わない語は残さない
    # 同じ確認は、確認が変わるまで置き直さない（断りは固定）
    led, rep = await _run(c, root, led, _copy(SPEC))
    assert rep.failed == [] and led["pairs"][keys[0]]["store"]["reason"] == "role_mismatch"


async def test_a_pair_the_copy_lost_is_an_error_but_the_ledger_keeps_it(tmp_path: Path) -> None:
    root, c = tmp_path / "registry", _Client()
    await _claims_world(c, root)
    led = _ledger(rels=[(1, 2, "lowers")])
    keys = _keys(led)
    led = _confirm(led, keys)
    led, rep = await _run(c, root, led, _copy([]))  # 語が写しに無い
    assert rep.failed == [{"key": keys[0], "reason": "error"}]
    # error は次の回にやり直す（写しに戻れば置かれる）
    led, rep = await _run(c, root, led, _copy(SPEC))
    assert rep.placed == keys and led["pairs"][keys[0]]["store"]["ok"] is True


async def test_catch_up_takes_confirmed_entries_without_a_store_in_chunks(tmp_path: Path) -> None:
    root, c = tmp_path / "registry", _Client()
    await _claims_world(c, root)
    led = _ledger(rels=[(1, 2, "lowers"), (1, 3, "part"), (5, 2, "has_property")])
    led = _confirm(led, _keys(led))  # 14e-1 で先に付けた確認（store が無い）
    copy = _copy(SPEC)
    led, rep = await _run(c, root, led, copy, limit=2)
    assert len(rep.placed) == 2 and rep.remaining == 1
    assert sum(1 for e in led["pairs"].values() if "store" in e) == 2
    led, rep = await _run(c, root, led, copy, limit=2)
    assert len(rep.placed) == 1 and rep.remaining == 0
    assert len(_my_lines(c, "knowledge:lab")) == 3


async def test_a_confirmed_line_answers_the_question_and_is_gone_after_the_cancel(
    tmp_path: Path,
) -> None:
    root, c = tmp_path / "registry", _Client()
    await _claims_world(c, root)
    led = _ledger(rels=[(1, 3, "part")])
    keys = _keys(led)
    led = _confirm(led, keys)
    copy = _copy(SPEC)
    led, _ = await _run(c, root, led, copy)
    target = sv.term_iri(cs.slug_for(_tid(3)))
    ans = await knowledge.knowledge_answer(c, {"op": "parts", "target": target})
    assert ans["count"] == 1
    led, _ = pr.unconfirm(led, keys)
    led, _ = await _run(c, root, led, copy)
    # 語ごと消えたので、問いは答えを持たない
    ans = await knowledge.knowledge_answer(c, {"op": "parts", "target": target})
    assert ans["count"] == 0


# ---- 乱数の操作列 ------------------------------------------------------------------------------


async def _trial(seed: int, root: Path) -> None:
    rnd = random.Random(seed)
    c = _Client()
    await _claims_world(c, root)
    specs = [(i, f"語{seed}-{i}", rnd.choice(["class", "class", "property"]), None)
             for i in range(1, rnd.randint(4, 8))]  # fmt: skip
    copy = _copy(specs)
    kind = {_tid(n): k for n, _, k, _ in specs}
    ids = [n for n, *_ in specs]
    led = _ledger()
    ledger_roots = {"lab": led}
    for _ in range(rnd.randint(8, 18)):
        op = rnd.choice(["add", "confirm", "confirm", "unconfirm", "reject", "restore", "again", "remove"])
        pairs = led["pairs"]
        if op == "add":
            a, b = rnd.sample(ids, 2)
            rel = rnd.choice(["sub", "part", "lowers", "raises", "affects", "has_property", "competes"])
            x, y = sorted((_tid(a), _tid(b)))
            key = f"{x}|{y}"
            if key not in pairs:
                tmp = _ledger(rels=[(a, b, rel)])
                pairs[key] = tmp["pairs"][key]
        elif op in ("confirm", "unconfirm", "reject", "restore") and pairs:
            key = rnd.choice(sorted(pairs))
            fns = {
                "confirm": lambda L, k=key: pr.confirm(L, [k], at=AT),
                "unconfirm": lambda L, k=key: pr.unconfirm(L, [k]),
                "reject": lambda L, k=key: pr.reject(L, [k], at=AT2),
                "restore": lambda L, k=key: pr.restore(L, [k]),
            }
            fn = fns[op]
            led, _ = fn(led)
        elif op == "remove":  # 取り込み元を外す（未確認を消す・確認済みは残す）
            led, _ = pr.strip_unconfirmed(led)
        led, _ = await _run(c, root, led, copy)
        # 不変: ストアの自分の batch の線 ＝ 台帳の「確認済みで置けた件」の線
        want = {
            e["store"]["line"]
            for e in led["pairs"].values()
            if e.get("confirmed") and (e.get("store") or {}).get("ok")
        }
        assert _my_lines(c, "knowledge:lab") == want, (seed, op)
        for line in want:
            s, _r, t = line.split("|")
            assert {s, t} <= _terms_in_store(c), (seed, op)
        # 作った語は、線が使っているものだけ
        minted = {sv.term_iri(v["slug"]) for v in led.get("stored_terms", {}).values() if v["minted"]}
        used = {x for ln in want for x in (ln.split("|")[0], ln.split("|")[2])}
        assert {t for t in _terms_in_store(c) if "/kt_" in t} <= used | minted, (seed, op)
        assert minted <= used, (seed, op)
        await _check_questions(c, root, led, copy)
    del kind, ledger_roots


async def test_random_operations_keep_the_store_equal_to_the_ledger(tmp_path: Path) -> None:
    for seed in range(25):
        await _trial(seed, tmp_path / f"r{seed}")


# ---- 根拠の引き方 -------------------------------------------------------------------------------


async def test_evidence_resolves_claims_from_the_copy_even_after_a_retract(tmp_path: Path) -> None:
    root, c = tmp_path / "registry", _Client()
    await _claims_world(c, root, status="retracted")  # 取り下げ済み（graph は残る）
    got = await evidence.resolve_evidence(c, [f"{CLAIM}c1", f"{CLAIM}zz"], registry_root=root)
    assert set(got) == {f"{CLAIM}c1"}
    assert got[f"{CLAIM}c1"] == {
        "iri": f"{CLAIM}c1", "text": "作り物の知見 1", "full": "作り物の知見 1", "section": None,
        "path": None, "dataset_id": None, "dataset_label": "作り物の束",
        "source_url": "https://example.org/n/1",
    }  # fmt: skip


async def test_evidence_does_not_read_a_deleted_copy_or_a_non_knowledge_dataset(
    tmp_path: Path,
) -> None:
    root, c = tmp_path / "registry", _Client()
    await _claims_world(c, root, status="deleted")
    assert await evidence.resolve_evidence(c, [f"{CLAIM}c1"], registry_root=root) == {}
    # 知識の写しの印（knowledge_source）の無い json のデータセットは引かない
    meta = json.loads((root / "lab-copy" / "meta.json").read_text())
    meta.pop("status")
    meta.pop("knowledge_source")
    (root / "lab-copy" / "meta.json").write_text(json.dumps(meta))
    assert await evidence.resolve_evidence(c, [f"{CLAIM}c1"], registry_root=root) == {}


def test_load_ledger_drops_a_malformed_stored_terms(tmp_path: Path) -> None:
    led = pr.empty_ledger("lab")
    led["stored_terms"] = ["x"]
    pr.save_ledger(tmp_path, led, at=AT)
    assert "stored_terms" not in pr.load_ledger(tmp_path, "lab")
    led["stored_terms"] = {_tid(1): {"slug": "kt_x", "minted": True}}
    pr.save_ledger(tmp_path, led, at=AT)
    assert pr.load_ledger(tmp_path, "lab")["stored_terms"] == led["stored_terms"]
    assert re.fullmatch(r"kt_[0-9a-f]{12}", cs.slug_for(_tid(7)))


# ---- 取り込み元の問い（knowledge_sources.json） --------------------------------------------------


async def _check_questions(c: _Client, root: Path, led: dict, copy: KnowledgeCopy) -> None:
    """問いの名前の集合 ＝ ストアに問うて 1 件以上返る（対象の語 × 型 × 向き）の集合。"""
    sels = {n: sel for _t, n, sel in ksrc.selections(root)}
    got: set[str] = set()
    for n, sel in sels.items():
        spec = {"op": sel["op"], "target": sv.term_iri(sel["target_slug"])}
        if sel.get("direction"):
            spec["direction"] = sel["direction"]
        assert (await knowledge.knowledge_answer(c, spec))["count"] >= 1, n
        got.add(n)
    kinds = {t["id"]: t["kind"] for t in copy.terms}
    want: set[str] = set()
    for tid, v in (led.get("stored_terms") or {}).items():
        for op in knowledge.OPS:
            if not themes.knowledge_target_kind_ok(op, kinds.get(tid)):
                continue
            for d in knowledge.DIRECTIONS if op == "factors" else [None]:
                spec = {"op": op, "target": sv.term_iri(v["slug"])}
                if d:
                    spec["direction"] = d
                if (await knowledge.knowledge_answer(c, spec))["count"]:
                    want.add(themes.knowledge_tool_name(v["slug"], op, d))
    assert got == want, (got ^ want)


async def test_questions_follow_the_confirmed_lines_and_answer_with_claim_titles(
    tmp_path: Path,
) -> None:
    root, c = tmp_path / "registry", _Client()
    await _claims_world(c, root)
    led = _ledger(rels=[(1, 3, "part"), (1, 2, "lowers")])
    keys = _keys(led)
    led = _confirm(led, keys)
    copy = _copy(SPEC)
    led, _ = await cs.sync(c, root, led, copy, claim_graph=GRAPH, now=AT2, title="作り物の束")
    pr.save_ledger(root, led, at=AT2)
    got = ksrc.load_sources(root)["sources"]["lab"]
    assert got["title"] == "作り物の束" and len(got["relations"]) == 2
    s1, s3 = cs.slug_for(_tid(1)), cs.slug_for(_tid(3))
    assert themes.knowledge_tool_name(s1, "parts") not in got["selections"]
    assert themes.knowledge_tool_name(s3, "parts") in got["selections"]  # 粒界の一部＝気孔
    await _check_questions(c, root, led, copy)
    # 答えの根拠に、知見の題が引ける
    ans = await knowledge.knowledge_answer(
        c, {"op": "factors", "target": sv.term_iri(cs.slug_for(_tid(2))), "direction": "lowers"}
    )
    cited = await evidence.resolve_evidence(
        c, [e for it in ans["items"] for e in it["evidence"]], registry_root=root
    )
    assert {v["text"] for v in cited.values()} == {"作り物の知見 1", "作り物の知見 2"}
    # 同じ台帳を流し直してもファイルは同じ・取り消せば問いも消える
    text = (root / "vocab-shared" / ksrc.FILE).read_text(encoding="utf-8")
    await cs.sync(c, root, led, copy, claim_graph=GRAPH, now=AT, title="作り物の束")
    assert (root / "vocab-shared" / ksrc.FILE).read_text(encoding="utf-8") == text
    led, _ = pr.unconfirm(led, keys)
    led, _ = await cs.sync(c, root, led, copy, claim_graph=GRAPH, now=AT2, title="作り物の束")
    assert "lab" not in ksrc.load_sources(root)["sources"]
    assert ksrc.selections(root) == []


async def test_a_question_a_theme_already_keeps_is_not_made_again(tmp_path: Path) -> None:
    root, c = tmp_path / "registry", _Client()
    await _claims_world(c, root)
    led = _ledger(rels=[(1, 3, "part")])
    led = _confirm(led, _keys(led))
    name = themes.knowledge_tool_name(cs.slug_for(_tid(3)), "parts")
    (root / "vocab-shared").mkdir(parents=True, exist_ok=True)
    (root / "vocab-shared" / "themes.json").write_text(
        json.dumps({"themes": [{"id": "t1", "selections": {name: {"mode": "knowledge"}}}],
                    "migrated": True}), encoding="utf-8")  # fmt: skip
    await cs.sync(c, root, led, _copy(SPEC), claim_graph=GRAPH, now=AT2)
    assert name not in ksrc.load_sources(root).get("sources", {}).get("lab", {}).get("selections", {})


def test_a_broken_sources_file_is_set_aside_and_read_as_empty(tmp_path: Path) -> None:
    path = tmp_path / "vocab-shared" / ksrc.FILE
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert ksrc.load_sources(tmp_path) == {"version": 1, "sources": {}}
    assert path.with_name(path.name + ".bad").is_file() and not path.exists()


def test_the_sky_counts_an_import_source_question_for_its_term(tmp_path: Path) -> None:
    from asterism import sky_view

    sel = {"mode": "knowledge", "op": "parts", "target_slug": "kt_aaaaaaaaaaaa", "target": "粒界",
           "title": "粒界の一部"}  # fmt: skip
    ksrc.save_sources(tmp_path, {"version": 1, "sources": {"lab": {
        "title": "作り物の束", "updated_at": AT, "minted": [], "uses": [], "relations": [],
        "selections": {"kq_kt_aaaaaaaaaaaa_parts": sel},
    }}})  # fmt: skip
    iri = sv.term_iri("kt_aaaaaaaaaaaa")
    assert sky_view.questions_of_term(tmp_path, iri) == ["kq_kt_aaaaaaaaaaaa_parts"]
    (src,) = [x for x in sky_view._knowledge_question_selections(tmp_path)]
    assert src[0]["label"] == "作り物の束" and src[0]["id"] is None and src[3] == iri
