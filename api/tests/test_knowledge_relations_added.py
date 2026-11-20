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
