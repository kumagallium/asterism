# ruff: noqa: E501
"""``pair_relations.questions`` の数が、同じ関係を確定して置いたストアに ``knowledge_answer`` で
問うた件数と全型・全語で一致するか（種を固定した乱数の台帳）。例は作り物。"""

from __future__ import annotations

import json
import random
from pathlib import Path

import rdflib

from asterism import knowledge, themes
from asterism import pair_relations as pr
from asterism import shared_vocab as sv
from asterism.crosswalk_runtime import assert_alignment
from asterism.knowledge_bundle import KnowledgeCopy

_AT = "2026-11-18T00:00:00+00:00"
_RELS = ["sub", "part", "lowers", "raises", "affects", "makes", "measures", "becomes",
         "competes", "has_property"]


class _Store:
    def __init__(self) -> None:
        self.ds = rdflib.Dataset()

    async def sparql_select(self, q: str) -> dict:
        raw = self.ds.query(q).serialize(format="json")
        return json.loads(raw.decode() if isinstance(raw, bytes) else raw)

    async def sparql_update(self, u: str) -> None:
        self.ds.update(u)


async def _trial(seed: int, root: Path) -> list[tuple]:
    rnd = random.Random(seed)
    terms = [
        {"id": f"t{i}", "name": f"名{i}", "kind": rnd.choice(["class", "class", "property"]), "role": None}
        for i in range(rnd.randint(4, 9))
    ]
    store = _Store()
    for t in terms:
        await sv.mint_term(store, root, slug=t["id"], kind=t["kind"], label=t["name"], cqs=[],
                           theme_id="x", at=_AT)
    kind = {t["id"]: t["kind"] for t in terms}
    pairs: dict[str, dict] = {}
    for _ in range(rnd.randint(3, 16)):
        a, b = rnd.sample([t["id"] for t in terms], 2)
        rel = rnd.choice(_RELS)
        key = "|".join(sorted((a, b)))
        if key in pairs:
            continue
        try:
            pred = themes.relation_predicate(rel, kind[a], kind[b])
            await assert_alignment(store, sv.term_iri(a), sv.term_iri(b), pred, at=_AT,
                                   drafted_by="ai", batch="b")
        except Exception:  # 検査に通らない組み合わせは置かない
            continue
        x, y = sorted((a, b))
        pairs[key] = {"key": key, "a": x, "b": y, "state": "relation", "rel": rel,
                      "from": a, "to": b, "evidence": ["c"]}
    if not pairs:  # 線が 1 本も無いストアには、rdflib が無い graph を取りに行く
        return []
    ledger = pr.empty_ledger("lab")
    ledger["pairs"] = pairs
    items, _ = pr.questions(ledger, KnowledgeCopy(terms=terms), lang="ja", limit=10000)
    got = {(i["op"], i["term"]["id"], i.get("direction")): i["count"] for i in items}
    want: dict[tuple, int] = {}
    for t in terms:
        for op in knowledge.OPS:
            if not themes.knowledge_target_kind_ok(op, t["kind"]):
                continue
            for d in (knowledge.DIRECTIONS if op == "factors" else [None]):
                spec = {"op": op, "target": sv.term_iri(t["id"])}
                if d:
                    spec["direction"] = d
                out = await knowledge.knowledge_answer(store, spec)
                if out["count"]:
                    want[(op, t["id"], d)] = out["count"]
    return [(seed, k, got.get(k), want.get(k)) for k in set(got) | set(want) if got.get(k) != want.get(k)]


async def test_questions_agree_with_the_runner_on_a_seeded_random_ledger(tmp_path: Path) -> None:
    diffs: list[tuple] = []
    for seed in range(40):
        diffs += await _trial(seed, tmp_path)
    assert diffs == []
