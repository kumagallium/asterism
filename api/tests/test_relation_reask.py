"""PR R4 — 足した関係で、同じ種類の「言えない」組だけを判定し直す口（GET/POST reask）。"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from pathlib import Path

import pytest
from asterism import pair_relations as pr
from asterism import relation_catalog as rc
from asterism import relation_store as rs
from asterism.term_match import merge_key
from fastapi.testclient import TestClient

from asterism_api import knowledge_relations as kr

from .test_ingest import _AUTH
from .test_knowledge_relations import _Env, _terms_bundle

pytest.importorskip("morph_kgc", reason="the copy runs the real declarative ingest")

NAME = "u_0000dddd"
_KEY = {"X-API-Key": "test-key"}


@pytest.fixture(autouse=True)
def _reset_human():
    rc.set_human((), ())
    yield
    rc.set_human((), ())


def _human(name: str = NAME, label: str = "併用", *, key: str | None = "併用"):
    return rs._build(
        name, label={"ja": label}, sentence=rs.default_sentence({"ja": label}, symmetric=True),
        definition={"ja": f"{label}の説明"}, symmetric=True, to_property_only=False,
        exact_match=None, unstated_key=merge_key(key) if key else None,
        added_at="2026-11-20T00:00:00Z",
    )


class _KindAI:
    """1 回目: 全部「言えない」（Mg2Si・Mg2Sn を含む組は種類「併用」・ほかは「条件」）。
    足した関係が系に渡っていれば、Mg を含む組は別名 x1 で答える。"""

    model = "stub-model"

    def __init__(self) -> None:
        self.calls = 0
        self.systems: list[str] = []

    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        self.systems.append(system)
        mg = "Mg2Si" in user or "Mg2Sn" in user
        if "x1 = " in system and mg:
            return json.dumps(
                {"rel": "x1", "from": "a", "evidence": ["c1"], "note": "x1 と書いてある"}
            )
        return json.dumps({
            "rel": "none", "from": None, "evidence": [], "note": "なし",
            "unstated_kind": "併用" if mg else "条件",
        })


def _setup(tmp_path: Path):
    ai = _KindAI()
    env = _Env(tmp_path, ai)
    return env, ai


def _first_run(env: _Env, client: TestClient) -> dict:
    env.copy(client)
    return env.go(client)


def _add(rel_pred=None) -> None:
    rel, pred = rel_pred or _human()
    rc.set_human([rel], [pred])


def test_count_start_flow_and_second_count_zero(tmp_path: Path) -> None:
    env, ai = _setup(tmp_path)
    with TestClient(env.app, headers=_AUTH) as client:
        _first_run(env, client)
        before = pr.load_ledger(env.root, "lab")
        none_kind = [k for k, e in before["pairs"].items()
                     if e["state"] == "none" and e.get("unstated_kind") == "併用"]
        assert none_kind
        _add()
        got = client.get(f"/api/relations/{NAME}/reask", headers=_KEY)
        assert got.status_code == 200
        body = got.json()
        assert body["name"] == NAME and body["total"] == len(none_kind)
        assert body["sources"] == {"lab": len(none_kind)} and body["keys"] == sorted(none_kind)
        calls = ai.calls
        r = client.post(f"/api/relations/{NAME}/reask", json={}, headers=_KEY)
        assert r.status_code == 202, r.text
        start = r.json()
        assert start["sources"] == ["lab"] and start["skipped"] == {}
        out = env.finish(client, start["job_id"])
        n = len(none_kind)
        assert out["total"] == {"asked": n, "relation": n, "none": 0, "unusable": 0, "failed": 0}
        assert out["sources"]["lab"]["asked"] == len(none_kind)
        assert ai.calls == calls + len(none_kind)  # 聞いたのは「言えない」の同じ種類だけ
        ledger = pr.load_ledger(env.root, "lab")
        for k in none_kind:
            e = ledger["pairs"][k]
            assert e["state"] == "relation" and e["rel"] == NAME and e["reasked"] == [NAME]
        again = client.get(f"/api/relations/{NAME}/reask", headers=_KEY).json()
        assert again["total"] == 0 and again["keys"] == []
        # 取り込み直し（通常の関係選び）でも聞かない・記録は残る
        assert env.go(client)["asked"] == 0


def test_none_answer_also_marks_reasked(tmp_path: Path) -> None:
    env, ai = _setup(tmp_path)
    with TestClient(env.app, headers=_AUTH) as client:
        _first_run(env, client)
        _add()
        ai.complete = lambda s, u: json.dumps(  # type: ignore[method-assign]
            {"rel": "none", "from": None, "evidence": [], "note": "なお", "unstated_kind": "併用"}
        )
        r = client.post(f"/api/relations/{NAME}/reask", json={}, headers=_KEY)
        out = env.finish(client, r.json()["job_id"])
        assert out["total"]["none"] == out["total"]["asked"] > 0 and out["total"]["relation"] == 0
        assert client.get(f"/api/relations/{NAME}/reask", headers=_KEY).json()["total"] == 0


def test_errors_404_409_and_auth(tmp_path: Path) -> None:
    env, _ai = _setup(tmp_path)
    with TestClient(env.app, headers=_AUTH) as client:
        _first_run(env, client)
        got = client.get("/api/relations/affects/reask", headers=_KEY)  # 組み込み
        assert got.status_code == 404
        assert client.get("/api/relations/u_ffffffff/reask", headers=_KEY).status_code == 404
        rel, pred = _human()
        rc.set_human([dataclasses.replace(rel, retired=True)], [pred])
        assert client.get(f"/api/relations/{NAME}/reask", headers=_KEY).status_code == 409
        retired = client.post(f"/api/relations/{NAME}/reask", json={}, headers=_KEY)
        assert retired.status_code == 409
        _add()
        no_ai = client.post(f"/api/relations/{NAME}/reask", json={})  # X-API-Key なし
        assert no_ai.status_code == 409 and "AI" in no_ai.text
    with TestClient(env.app) as anon:  # 書き込みの認証なし
        assert anon.post(f"/api/relations/{NAME}/reask", json={}).status_code in (401, 403, 503)


def test_nothing_to_reask_is_409(tmp_path: Path) -> None:
    env, _ai = _setup(tmp_path)
    with TestClient(env.app, headers=_AUTH) as client:
        _first_run(env, client)
        # 表示名「併用」でも結ばれる（unstated_key が違っても）→ 表示名も変えて 0 件にする
        _add(_human(label="全然別の名前", key="関係ない種類"))
        assert client.get(f"/api/relations/{NAME}/reask", headers=_KEY).json()["total"] == 0
        r = client.post(f"/api/relations/{NAME}/reask", json={}, headers=_KEY)
        assert r.status_code == 409


def test_label_also_links_when_no_unstated_key(tmp_path: Path) -> None:
    env, _ai = _setup(tmp_path)
    with TestClient(env.app, headers=_AUTH) as client:
        _first_run(env, client)
        _add(_human(key=None))  # 表示名「併用」だけで結ぶ
        assert client.get(f"/api/relations/{NAME}/reask", headers=_KEY).json()["total"] > 0


def test_busy_source_is_skipped_and_all_skipped_is_409(tmp_path: Path) -> None:
    env, _ai = _setup(tmp_path)
    with TestClient(env.app, headers=_AUTH) as client:
        _first_run(env, client)
        _add()
        client.app.state.knowledge_relations_running = {"lab"}  # 関係選びが走っている
        r = client.post(f"/api/relations/{NAME}/reask", json={}, headers=_KEY)
        assert r.status_code == 409 and "lab" in r.json()["detail"]["skipped"]
        client.app.state.knowledge_relations_running = set()
        client.app.state.knowledge_busy = {"lab": "import"}
        r = client.post(f"/api/relations/{NAME}/reask", json={}, headers=_KEY)
        assert r.status_code == 409
        client.app.state.knowledge_busy = {}


def test_second_start_for_same_relation_is_409_and_running_is_marked(tmp_path: Path) -> None:
    env, _ai = _setup(tmp_path)
    with TestClient(env.app, headers=_AUTH) as client:
        _first_run(env, client)
        _add()
        client.app.state.relations_reasking = {NAME}
        r = client.post(f"/api/relations/{NAME}/reask", json={}, headers=_KEY)
        assert r.status_code == 409 and "走っている" in r.json()["detail"]
        client.app.state.relations_reasking = set()
        ok = client.post(f"/api/relations/{NAME}/reask", json={}, headers=_KEY)
        assert ok.status_code == 202
        env.finish(client, ok.json()["job_id"])
        assert not client.app.state.relations_reasking
        assert not client.app.state.knowledge_relations_running


def test_force_empty_keeps_run_relations_behavior(tmp_path: Path) -> None:
    env, _ai = _setup(tmp_path)
    with TestClient(env.app, headers=_AUTH) as client:
        env.copy(client)
        env.go(client)
        again = env.go(client)
        assert again["asked"] == 0 and again["relation"] == 0 and again["none"] == 0


def test_run_relations_force_asks_same_fingerprint_pair(tmp_path: Path) -> None:
    env, ai = _setup(tmp_path)
    with TestClient(env.app, headers=_AUTH) as client:
        dataset_id = env.copy(client)["dataset_id"]
        env.go(client)
    ledger = pr.load_ledger(env.root, "lab")
    key = sorted(ledger["pairs"])[0]
    before = ai.calls
    _add()
    out = asyncio.run(kr.run_relations(
        env.root, "lab", dataset_id, make_llm=lambda: ai, force={key}
    ))
    assert out["asked"] == 1 and ai.calls == before + 1
    assert pr.load_ledger(env.root, "lab")["pairs"][key]["reasked"] == [NAME]



def _second_source(env: _Env, client: TestClient, ai: _KindAI) -> None:
    r = client.post("/api/knowledge-copies", json={"source": "lab2", "bundle": _terms_bundle()})
    assert r.status_code == 200, r.text
    r = client.post("/api/knowledge-copies/lab2/relations", json={}, headers=_KEY)
    env.finish(client, r.json()["job_id"])


def test_force_asks_only_forced_pairs(tmp_path: Path) -> None:
    """failed・新しい組・指紋が変わった組が混ざる台帳でも、force の組だけを聞く。"""
    env, ai = _setup(tmp_path)
    with TestClient(env.app, headers=_AUTH) as client:
        dataset_id = env.copy(client)["dataset_id"]
        env.go(client)
    led = pr.load_ledger(env.root, "lab")
    keys = sorted(led["pairs"])
    forced, failed, stale, gone = keys[0], keys[1], keys[2], keys[3]
    led["pairs"][failed]["state"] = "failed"
    led["pairs"][stale]["fingerprint"] = "古い"
    del led["pairs"][gone]
    pr.save_ledger(env.root, led, at="t")
    before = ai.calls
    out = asyncio.run(kr.run_relations(
        env.root, "lab", dataset_id, make_llm=lambda: ai, force={forced}
    ))
    assert out["asked"] == 1 and ai.calls == before + 1
    got = pr.load_ledger(env.root, "lab")["pairs"]
    assert got[failed]["state"] == "failed" and got[stale]["fingerprint"] == "古い"
    assert gone not in got


def test_counts_failed_and_unusable_and_sum(tmp_path: Path) -> None:
    env, _ai = _setup(tmp_path)
    with TestClient(env.app, headers=_AUTH) as client:
        dataset_id = env.copy(client)["dataset_id"]
        env.go(client)
    ledger = pr.load_ledger(env.root, "lab")
    keys = sorted(ledger["pairs"])[:4]
    before = {k: dict(ledger["pairs"][k]) for k in keys}
    rel, pred = _human()
    rc.set_human([rel], [pred])

    class _Mixed:
        model = "m"

        def __init__(self) -> None:
            self.n = 0

        def complete(self, system: str, user: str) -> str:
            self.n += 1
            if "Mg2Si" in user or "Mg2Sn" in user:
                raise RuntimeError("boom")  # AI の失敗
            return json.dumps({"rel": "none", "from": None, "evidence": [], "note": "x"})

    out = asyncio.run(kr.run_relations(
        env.root, "lab", dataset_id, make_llm=_Mixed, force=set(keys)
    ))
    assert out["asked"] == len(keys)
    assert out["relation"] + out["none"] + out["unusable"] + out["failed"] == out["asked"]
    assert out["failed"] >= 1 and out["kept_stale"] == 0
    after = pr.load_ledger(env.root, "lab")["pairs"]
    for k in keys:
        if after[k].get("reasked") is None:  # 失敗した組は前の件のまま
            assert after[k]["state"] == before[k]["state"]


def test_unusable_when_relation_retired_midrun_and_stops_next_source(tmp_path: Path) -> None:
    env, ai = _setup(tmp_path)
    with TestClient(env.app, headers=_AUTH) as client:
        _first_run(env, client)
        _second_source(env, client, ai)
        rel, pred = _human()
        rc.set_human([rel], [pred])
        real = ai.complete

        def retire_then_answer(system: str, user: str) -> str:
            if "x1 = " in system:
                rc.set_human([dataclasses.replace(rel, retired=True)], [pred])
                return json.dumps({"rel": "x1", "from": "a", "evidence": ["c1"], "note": "n"})
            return real(system, user)

        ai.complete = retire_then_answer  # type: ignore[method-assign]
        r = client.post(f"/api/relations/{NAME}/reask", json={}, headers=_KEY)
        assert r.status_code == 202, r.text
        out = env.finish(client, r.json()["job_id"])
        assert out["stopped"] == "retired"
        assert out["total"]["unusable"] > 0 and out["total"]["relation"] == 0
        t = out["total"]
        assert t["asked"] == t["relation"] + t["none"] + t["unusable"] + t["failed"]
        assert set(out["sources"]) == {"lab"} and out["skipped"]["lab2"] == "retired"
        # 使えない答えは前の件を残し、reasked も付けない
        pairs = pr.load_ledger(env.root, "lab")["pairs"].values()
        assert all("reasked" not in e for e in pairs)
        assert any(e.get("unstated_kind") == "併用" for e in pairs)


def test_plain_run_marks_reasked_with_all_added_names(tmp_path: Path) -> None:
    env, _ai = _setup(tmp_path)
    rel, pred = _human()
    with TestClient(env.app, headers=_AUTH) as client:
        env.copy(client)
        rc.set_human([rel], [pred])
        env.go(client)
        pairs = pr.load_ledger(env.root, "lab")["pairs"]
        done = [e for e in pairs.values() if e["state"] != "failed"]
        assert done and all(e.get("reasked") == [NAME] for e in done)
        # ボタンで二重に聞かない
        assert client.get(f"/api/relations/{NAME}/reask", headers=_KEY).json()["total"] == 0


def test_reasking_is_set_before_counting_and_cleared_on_409(tmp_path: Path, monkeypatch) -> None:
    env, _ai = _setup(tmp_path)
    with TestClient(env.app, headers=_AUTH) as client:
        _first_run(env, client)
        _add()
        seen: list[bool] = []
        real = kr.ledger_answer.find_ledgers

        def spy(root, **kw):
            seen.append(NAME in client.app.state.relations_reasking)
            return real(root, **kw)

        monkeypatch.setattr(kr.ledger_answer, "find_ledgers", spy)
        client.post(f"/api/relations/{NAME}/reask", json={})  # AI なしで 409
        assert seen == [True]
        assert NAME not in client.app.state.relations_reasking


def test_count_excludes_blocked_sources_and_reports_them(tmp_path: Path) -> None:
    env, _ai = _setup(tmp_path)
    with TestClient(env.app, headers=_AUTH) as client:
        _first_run(env, client)
        _add()
        full = client.get(f"/api/relations/{NAME}/reask", headers=_KEY).json()
        client.app.state.knowledge_relations_running = {"lab"}
        got = client.get(f"/api/relations/{NAME}/reask", headers=_KEY).json()
        assert got["total"] == 0 and got["sources"] == {} and got["keys"] == []
        assert got["blocked"] == {"lab": {"count": full["total"], "reason": "relations_running"}}
        client.app.state.knowledge_relations_running = set()
        client.app.state.knowledge_busy = {"lab": "import"}
        busy = client.get(f"/api/relations/{NAME}/reask", headers=_KEY).json()
        assert busy["blocked"]["lab"]["reason"] == "busy"
        client.app.state.knowledge_busy = {}
