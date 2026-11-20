"""PR R3b — run_relations が、目録の「人が足した関係」を 1 回読んで全部の組に渡す。"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest
from asterism import relation_catalog as rc
from asterism import relation_store as rs

from asterism_api import knowledge_relations as kr

from .test_knowledge_relations import _prepared

pytest.importorskip("morph_kgc", reason="the copy runs the real declarative ingest")


@pytest.fixture(autouse=True)
def _reset_human():
    rc.set_human((), ())
    yield
    rc.set_human((), ())


def _human(name: str, label: str, *, symmetric: bool = False) -> tuple[Any, Any]:
    return rs._build(
        name, label={"ja": label}, sentence=rs.default_sentence({"ja": label}, symmetric=symmetric),
        definition={"ja": f"{label}の説明"}, symmetric=symmetric, to_property_only=False,
        exact_match=None, unstated_key=None, added_at="2026-11-20T00:00:00Z",
    )


class _Rec:
    model = "stub"

    def __init__(self) -> None:
        self.systems: list[str] = []

    def complete(self, system: str, user: str) -> str:
        self.systems.append(system)
        return json.dumps({"rel": "none", "from": None, "evidence": [], "note": ""})


def test_added_relations_reach_every_pair_once_read(tmp_path: Path, monkeypatch) -> None:
    env, dataset_id = _prepared(tmp_path)
    r1, p1 = _human("u_0000aaaa", "支える")
    r2, p2 = _human("u_0000bbbb", "やめた")
    rc.set_human([r1, dataclasses.replace(r2, retired=True)], [p1, p2])
    reads = {"n": 0}
    real = kr._added_relations

    def counting():
        reads["n"] += 1
        out = real()
        rc.set_human((), ())  # 走りの途中で消えても、始めに読んだ物を使う
        return out

    monkeypatch.setattr(kr, "_added_relations", counting)
    ai = _Rec()
    out = asyncio.run(
        kr.run_relations(env.root, "lab", dataset_id, make_llm=lambda: ai, concurrency=2)
    )
    assert out["asked"] > 1 and reads["n"] == 1
    assert all("x1 = 支える: 支えるの説明" in s for s in ai.systems)
    assert all("やめた" not in s and "x2" not in s and "u_0000" not in s for s in ai.systems)


def test_zero_added_calls_propose_pair_without_extra(tmp_path: Path, monkeypatch) -> None:
    env, dataset_id = _prepared(tmp_path)
    seen: list[dict] = []

    def fake(llm, pair, lang="ja", **kw):
        seen.append(kw)
        return {"rel": "none", "from": None, "evidence": [], "note": ""}

    monkeypatch.setattr(kr, "propose_pair", fake)
    out = asyncio.run(kr.run_relations(env.root, "lab", dataset_id, make_llm=lambda: _Rec()))
    assert out["asked"] > 0 and all("extra" not in kw for kw in seen)
    for kw in seen:
        assert set(kw) == {"on_call"}


class _AliasAI:
    """足した関係があれば、Mg2Si か Mg2Sn を含む組を別名 x1 で答える。それ以外は none。"""

    model = "stub-model"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        if "x1 = " in system and ("Mg2Si" in user or "Mg2Sn" in user):
            note = "x1 と書いてある"
            return json.dumps({"rel": "x1", "from": "a", "evidence": ["c1"], "note": note})
        return json.dumps({"rel": "none", "from": None, "evidence": [], "note": ""})


def test_added_relation_end_to_end(tmp_path: Path) -> None:
    from asterism import pair_relations as pr
    from fastapi.testclient import TestClient

    from .test_ingest import _AUTH
    from .test_knowledge_relations import _Env

    ai = _AliasAI()
    env = _Env(tmp_path, ai)
    rel, pred = _human("u_0000cccc", "同じ表", symmetric=True)
    with TestClient(env.app, headers=_AUTH) as client:
        env.copy(client)
        rc.set_human([rel], [pred])  # 起動の読み込みの後に重ねる
        out = env.go(client)
        assert out["failed"] == 0 and out["asked"] > 0
        ledger = pr.load_ledger(env.root, "lab")
        hits = [e for e in ledger["pairs"].values() if e.get("rel") == "u_0000cccc"]
        assert hits and all(h["state"] == "relation" for h in hits)
        rows = client.get("/api/knowledge-copies/lab/relations", headers={"X-API-Key": "test-key"})
        rows = rows.json()["relations"]
        row = next(r for r in rows if r.get("rel") == "u_0000cccc")
        assert "同じ表" in row["sentence"]
        assert "x1" not in (hits[0].get("note") or "") and "同じ表" in (hits[0].get("note") or "")
        # 2 回目: 聞き直しは 0
        before = ai.calls
        again = env.go(client)
        assert again["asked"] == 0 and ai.calls == before
