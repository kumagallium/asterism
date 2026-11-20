"""語の組ごとに関係を選ぶジョブと読む口（PR 14c・契約書 §3・§6 の api の行）。

作り物の束（Mg2Si・Mg2Sn・アーク溶解・X 線回折・熱伝導率）を
``POST /api/knowledge-copies`` で写してから、
スタブの AI（入力の語の名前から答えを決め、呼ばれた回数を数える）で走らせる。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from asterism import pair_relations as pr
from fastapi.testclient import TestClient

from asterism_api import knowledge_relations as kr
from asterism_api import registry
from asterism_api.main import build_app

from .test_ingest import _AUTH, _settings
from .test_knowledge_copy import _AT, _CLAIM, _candidate, _claim
from .test_mcp_resync_rows import _RecOxi

pytest.importorskip("morph_kgc", reason="the copy runs the real declarative ingest")

_KEY = {"X-API-Key": "test-key"}
_NAME = re.compile(r"^([ab]): (.+?) \(", re.M)


class _StubAI:
    """入力の語の名前から答えを決める。何回呼ばれたかを数える（スレッドから呼ばれる）。"""

    model = "stub-model"

    def __init__(
        self, *, boom: set[str] | None = None, gate: threading.Event | None = None
    ) -> None:
        self.calls = 0
        self.names: list[tuple[str, str]] = []
        self.boom = boom or set()
        self.gate = gate
        self._lock = threading.Lock()

    def complete(self, system: str, user: str) -> str:
        found = dict(_NAME.findall(user))
        a, b = found["a"], found["b"]
        with self._lock:
            self.calls += 1
            self.names.append((a, b))
        if self.gate is not None:
            self.gate.wait(10)
        if {a, b} & self.boom:
            raise RuntimeError("stub AI is down")
        pair = {a, b}
        if "熱伝導率" in pair:  # 物性は、もう片方の語が変える
            return json.dumps({"rel": "affects", "from": "a" if b == "熱伝導率" else "b",
                               "evidence": ["c1"], "note": "熱伝導率に効く"})
        if pair == {"Mg2Si", "Mg2Sn"}:
            return json.dumps({"rel": "eq", "from": "a", "evidence": ["c1"], "note": "同じ"})
        if "アーク溶解" in pair and (pair - {"アーク溶解"}) <= {"Mg2Si", "Mg2Sn"}:
            return json.dumps({"rel": "makes", "from": "a" if a == "アーク溶解" else "b",
                               "evidence": ["c1"], "note": "作る"})
        return json.dumps({"rel": "none", "from": None, "evidence": [], "note": "一緒に出るだけ"})


def _terms_bundle(
    extra_claims: list[dict] | None = None, relations: list[tuple[str, str, str]] | None = None
) -> dict:
    names = [
        ("tp-si", "Mg2Si", "class", "material"),
        ("tp-sn", "Mg2Sn", "class", "material"),
        ("tp-arc", "アーク溶解", "class", "method"),
        ("tp-xrd", "X線回折", "class", "method"),
        ("tp-kappa", "熱伝導率", "property", None),
    ]
    claims = [
        _claim("c1", "Mg2Si は熱伝導率が低い", "verified"),
        _claim("c2", "Mg2Si をアーク溶解で作った"),
        _claim("c3", "Mg2Si と Mg2Sn の相を X線回折 で確かめた"),
        _claim("c4", "Mg2Sn の熱伝導率を測った"),
        *(extra_claims or []),
    ]
    return {
        "format": "asterism-knowledge/1",
        "title": "Lab",
        "exported_at": _AT,
        "bases": {"claim": _CLAIM, "source": "https://notes.test/source/",
                  "term": "https://notes.test/term/"},
        "files": {
            "claims.json": claims,
            "topics.json": [
                {
                    "id": tid,
                    "iri": _CLAIM + tid,
                    "source_url": f"https://notes.test/app/#knowledge/topic/{tid}",
                    "title": name,
                    "topic_type": "material" if role == "material" else "method",
                    "material": None,
                    "property": None,
                    "claim_iri": [],
                    "source_iri": [],
                    "generated_at": _AT,
                    "exported_at": _AT,
                }
                for tid, name, _k, role in names
            ],
            "sources.json": [],
            "term_candidates.json": [_candidate(tid, n, k, r, tid) for tid, n, k, r in names],
            "term_claims.json": [],
            "term_relations.json": [
                {"from_term": f, "rel": r, "to_term": t,
                 "origin_id": f, "origin_iri": _CLAIM + f}
                for f, r, t in (relations or [("tp-si", "has_property", "tp-kappa")])
            ],
        },
    }


class _Env:
    def __init__(self, tmp: Path, ai: _StubAI, *, single_user: bool = False) -> None:
        self.tmp = tmp
        self.ai = ai
        self.oxi = _RecOxi()
        self.resolved = 0

        def resolver(provider, model, api_base, api_key, max_tokens=None):
            self.resolved += 1
            return ai

        settings = _settings(tmp)
        settings.single_user = single_user
        self.app = build_app(
            settings, oxigraph_client=self.oxi.client, start_watcher=False,
            llm_resolver=resolver,
        )
        self.root = tmp / "registry"

    def copy(self, client: TestClient, bundle: dict | None = None) -> dict:
        r = client.post("/api/knowledge-copies",
                        json={"source": "lab", "bundle": bundle or _terms_bundle()})
        assert r.status_code == 200, r.text
        return r.json()

    def run(self, client: TestClient, body: dict | None = None, headers: dict | None = _KEY):
        return client.post("/api/knowledge-copies/lab/relations", json=body or {},
                           headers=headers)

    def finish(self, client: TestClient, job_id: str) -> dict:
        """ジョブの終わりまで待って、``done`` の結果を返す。"""
        for line in client.get(f"/api/jobs/{job_id}/stream").text.splitlines():
            if line.startswith("data:"):
                data = json.loads(line[5:])
                if "result" in data:
                    return data["result"]
        raise AssertionError("job did not finish with a result")

    def go(self, client: TestClient, body: dict | None = None) -> dict:
        r = self.run(client, body)
        assert r.status_code == 202, r.text
        return self.finish(client, r.json()["job_id"])


def _env(tmp_path: Path, **kw: Any) -> _Env:
    return _Env(tmp_path, _StubAI(**kw))


def test_the_job_writes_the_ledger(tmp_path: Path) -> None:
    env = _env(tmp_path)
    with TestClient(env.app, headers=_AUTH) as client:
        copied = env.copy(client)
        assert copied["counts"]["term_claims.json"] >= 5
        out = env.go(client)
    ledger = pr.load_ledger(env.root, "lab")
    assert ledger["dataset_id"] == copied["dataset_id"]
    assert out["asked"] == len(ledger["pairs"]) == env.ai.calls and out["failed"] == 0
    assert out["reused"] == 0 and out["cancelled"] is False
    summary = out["summary"]
    assert summary["pairs"] == len(ledger["pairs"])
    assert summary["relations"] >= 4 and summary["none"] >= 1
    assert summary["by_rel"].get("affects") == 2 and summary["by_rel"].get("makes") == 1
    assert summary["origin"] == 1
    assert pr.ledger_path(env.root, "lab").is_file()
    for e in ledger["pairs"].values():
        assert e["model"] == "stub-model" and e["by"] == "ai"


def test_a_second_run_asks_nothing(tmp_path: Path) -> None:
    env = _env(tmp_path)
    with TestClient(env.app, headers=_AUTH) as client:
        env.copy(client)
        first = env.go(client)
        calls = env.ai.calls
        second = env.go(client)
    assert second["asked"] == 0 and second["reused"] == first["asked"]
    assert env.ai.calls == calls
    assert second["summary"] == first["summary"]


def test_a_new_claim_asks_only_that_pair(tmp_path: Path) -> None:
    env = _env(tmp_path)
    with TestClient(env.app, headers=_AUTH) as client:
        env.copy(client)
        env.go(client)
        before = env.ai.calls
        # Mg2Si と 熱伝導率 の組に知見を 1 件足して、写し直す
        env.copy(client, _terms_bundle([_claim("c5", "Mg2Si の熱伝導率は温度で変わる")]))
        out = env.go(client)
    assert out["asked"] == 1 and env.ai.calls == before + 1
    assert set(env.ai.names[-1]) == {"Mg2Si", "熱伝導率"}


def test_limit_asks_the_first_n_and_the_rest_next_time(tmp_path: Path) -> None:
    env = _env(tmp_path)
    with TestClient(env.app, headers=_AUTH) as client:
        env.copy(client)
        first = env.go(client, {"limit": 2})
        assert first["asked"] == 2 and env.ai.calls == 2
        assert first["summary"]["pairs"] == 2
        rest = env.go(client)
    assert rest["asked"] > 0 and rest["reused"] == 2
    assert pr.summarize(pr.load_ledger(env.root, "lab"))["pairs"] == 2 + rest["asked"]


@pytest.mark.parametrize("limit", [0, -1, "x", 1.5])
def test_a_bad_limit_is_refused(tmp_path: Path, limit: Any) -> None:
    env = _env(tmp_path)
    with TestClient(env.app, headers=_AUTH) as client:
        env.copy(client)
        r = env.run(client, {"limit": limit})
    assert r.status_code == 422 and env.ai.calls == 0


def test_no_ai_is_a_409_and_no_copy_is_a_404(tmp_path: Path) -> None:
    env = _env(tmp_path)
    with TestClient(env.app, headers=_AUTH) as client:
        assert env.run(client).status_code == 404
        assert client.get("/api/knowledge-copies/lab/relations").status_code == 404
        env.copy(client)
        r = env.run(client, headers={})
        assert r.status_code == 409
    assert env.ai.calls == 0 and not pr.ledger_path(env.root, "lab").exists()


def test_routes_need_the_write_token(tmp_path: Path) -> None:
    env = _env(tmp_path)
    with TestClient(env.app) as client:
        assert client.post("/api/knowledge-copies/lab/relations", json={}).status_code == 401
        assert client.get("/api/knowledge-copies/lab/relations").status_code == 401


def test_the_read_route_gives_rows_sentences_and_evidence(tmp_path: Path) -> None:
    env = _env(tmp_path)
    with TestClient(env.app, headers=_AUTH) as client:
        env.copy(client)
        empty = client.get("/api/knowledge-copies/lab/relations")
        assert empty.status_code == 200
        assert empty.json()["relations"] == [] and empty.json()["summary"]["pairs"] == 0
        env.go(client)
        ja = client.get("/api/knowledge-copies/lab/relations").json()
        en = client.get("/api/knowledge-copies/lab/relations", params={"lang": "en"}).json()
        none = client.get("/api/knowledge-copies/lab/relations", params={"state": "none"}).json()
        bad = client.get("/api/knowledge-copies/lab/relations", params={"state": "maybe"})
    assert bad.status_code == 422
    rows = {r["key"]: r for r in ja["relations"]}
    assert all(r["state"] == "relation" for r in rows.values())
    makes = next(r for r in ja["relations"] if r["rel"] == "makes" and r["to"]["name"] == "Mg2Si")
    assert makes["from"] == {"id": makes["from"]["id"], "name": "アーク溶解",
                             "kind": "class", "role": "method"}
    assert makes["sentence"] == "アーク溶解 で Mg2Si を作る"
    en_makes = next(r for r in en["relations"] if r["key"] == makes["key"])
    assert en_makes["sentence"] != makes["sentence"] and "Mg2Si" in en_makes["sentence"]
    ev = makes["evidence"][0]
    assert ev["title"] == "Mg2Si をアーク溶解で作った"
    assert ev["iri"] == _CLAIM + "c2" and ev["review_state"] == "candidate"
    assert set(ev) == {"id", "iri", "title", "source_url", "review_state",
                       "epistemic_status", "modal_qualifier"}
    assert makes["tentative"] is False and makes["by"] == "ai" and makes["confirmed"] is None
    # 題から来た「Mg2Si の 熱伝導率」は AI（affects）と食い違う → 既定では出ず、候補（none）に出る
    assert ja["origin"] == [] and all("origin_iri" not in r for r in ja["relations"])
    assert [o["origin_iri"] for o in none["origin"]] == [[_CLAIM + "tp-si"]]
    assert none["origin"][0]["sentence"] == "Mg2Si の 熱伝導率"
    assert none["origin"][0]["ai"] == "disputes" and "evidence" not in none["origin"][0]
    assert none["relations"] and all(r["rel"] is None for r in none["relations"])


def test_rows_drop_what_the_copy_no_longer_has(tmp_path: Path) -> None:
    env = _env(tmp_path)
    with TestClient(env.app, headers=_AUTH) as client:
        env.copy(client)
        env.go(client)
        before = client.get("/api/knowledge-copies/lab/relations").json()
        # 知見 c2 の写しを消した束で写し直す（台帳は触らない）
        smaller = _terms_bundle()
        smaller["files"]["claims.json"] = [c for c in smaller["files"]["claims.json"]
                                          if c["id"] != "c2"]
        env.copy(client, smaller)
        after = client.get("/api/knowledge-copies/lab/relations").json()
    keys_before = {r["key"] for r in before["relations"]}
    keys_after = {r["key"] for r in after["relations"]}
    # 両端の語は残る（アーク溶解の語が他の知見に出なくても、語の行は束ねた束の語から作られる）
    assert keys_after <= keys_before
    for r in after["relations"]:
        assert all(e["id"] != "c2" for e in r["evidence"])


def test_a_copy_being_rebuilt_is_a_409(tmp_path: Path) -> None:
    env = _env(tmp_path)
    with TestClient(env.app, headers=_AUTH) as client:
        copied = env.copy(client)
        registry.update_meta_atomic(env.root, copied["dataset_id"],
                                    {"knowledge_source": {"name": "lab", "syncing": True}})
        post = env.run(client)
        get = client.get("/api/knowledge-copies/lab/relations")
    assert post.status_code == 409 and get.status_code == 409 and env.ai.calls == 0


def test_cancel_keeps_what_was_done(tmp_path: Path) -> None:
    gate = threading.Event()
    env = _env(tmp_path, gate=gate)
    with TestClient(env.app, headers=_AUTH) as client:
        env.copy(client)
        r = env.run(client)
        assert r.status_code == 202
        job_id = r.json()["job_id"]
        end = time.time() + 15
        while env.ai.calls < 4 and time.time() < end:
            time.sleep(0.02)
        assert env.ai.calls == 4  # 同時に 4 本まで
        assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 200
        gate.set()
        task = env.app.state.jobs.get(job_id).task
        end = time.time() + 15
        while not task.done() and time.time() < end:
            time.sleep(0.02)
        assert task.done()
        ledger = pr.load_ledger(env.root, "lab")
        # 走っていた 4 本は受けて保存し、新しい呼び出しは始めない
        assert env.ai.calls == 4 and len(ledger["pairs"]) == 4
        assert all(e["state"] != "failed" for e in ledger["pairs"].values())
        # 次に走らせると、残りだけ聞く
        again = env.go(client)
    assert again["reused"] == 4 and again["asked"] > 0


def test_a_failing_pair_does_not_stop_the_run(tmp_path: Path) -> None:
    env = _env(tmp_path, boom={"X線回折"})
    with TestClient(env.app, headers=_AUTH) as client:
        env.copy(client)
        out = env.go(client)
        failed = client.get("/api/knowledge-copies/lab/relations",
                            params={"state": "failed"}).json()
        # AI が戻ったら、失敗した組だけ聞き直す
        env.ai.boom = set()
        again = env.go(client)
    assert out["failed"] == 2 and out["asked"] > 2 and out["summary"]["failed"] == 2
    assert len(failed["relations"]) == 2
    assert again["asked"] == 2 and again["failed"] == 0 and again["summary"]["failed"] == 0


def test_every_pair_failing_still_finishes(tmp_path: Path) -> None:
    env = _env(tmp_path, boom={"Mg2Si", "Mg2Sn", "アーク溶解", "X線回折", "熱伝導率"})
    with TestClient(env.app, headers=_AUTH) as client:
        env.copy(client)
        out = env.go(client)
    assert out["failed"] == out["asked"] > 0 and out["summary"]["relations"] == 0
    assert out["summary"]["failed"] == out["asked"]


def test_a_second_run_on_the_same_source_is_a_409(tmp_path: Path) -> None:
    gate = threading.Event()
    env = _env(tmp_path, gate=gate)
    with TestClient(env.app, headers=_AUTH) as client:
        env.copy(client)
        first = env.run(client)
        assert first.status_code == 202
        second = env.run(client)
        gate.set()
        env.finish(client, first.json()["job_id"])
        third = env.run(client)  # 終わったら、また走らせられる
        assert third.status_code == 202
        env.finish(client, third.json()["job_id"])
    assert second.status_code == 409


def test_origin_relations_are_checked_against_the_ai(tmp_path: Path) -> None:
    env = _env(tmp_path)
    bundle = _terms_bundle(relations=[
        ("tp-si", "eq", "tp-sn"),  # (a) AI も eq を選ぶ
        ("tp-xrd", "measures", "tp-si"),  # (b) AI は none
        ("tp-arc", "affects", "tp-kappa"),  # (c) 2 語が同じ知見に出ない
    ])
    with TestClient(env.app, headers=_AUTH) as client:
        env.copy(client, bundle)
        out = env.go(client)
        url = "/api/knowledge-copies/lab/relations"
        rel = client.get(url).json()
        none = client.get(url, params={"state": "none"}).json()
        dropped = client.get(url, params={"state": "dropped"}).json()
        failed = client.get(url, params={"state": "failed"}).json()
    # (a) relations 側の行に origin_iri が付き、origin には出ない
    eq = next(r for r in rel["relations"] if r["rel"] == "eq")
    assert eq["origin_iri"] == [_CLAIM + "tp-si"]
    assert all(r["rel"] == "eq" or "origin_iri" not in r for r in rel["relations"])
    # (c) 既定の origin に出るのは unseen だけ
    assert [(o["rel"], o["ai"]) for o in rel["origin"]] == [("affects", "unseen")]
    assert rel["origin"][0]["from"]["name"] == "アーク溶解"
    # (b) none の origin に disputes が出る
    assert [(o["rel"], o["ai"]) for o in none["origin"]] == [("measures", "disputes")]
    assert dropped["origin"] == [] and failed["origin"] == []
    s = out["summary"]
    assert s["origin"] == 3 and s["origin_check"] == {"agrees": 1, "disputes": 1, "unseen": 1}
    assert s["shown"] == s["relations"] + 1 and s["candidates"] == s["none"] + 1
    assert rel["summary"] == s


class _SlowXrdAI(_StubAI):
    """X 線回折を含む組だけ ``hold`` が開くまで返さない（ほかの組はすぐ返る）。"""

    def __init__(self, hold: threading.Event) -> None:
        super().__init__()
        self.hold = hold

    def complete(self, system: str, user: str) -> str:
        if "X線回折" in dict(_NAME.findall(user)).values():
            with self._lock:
                self.calls += 1
            self.hold.wait(10)
            return json.dumps({"rel": "none", "from": None, "evidence": [], "note": ""})
        return super().complete(system, user)


def test_a_forced_stop_keeps_the_finished_answers(tmp_path: Path) -> None:
    hold = threading.Event()
    ai = _SlowXrdAI(hold)
    env = _Env(tmp_path, ai)
    with TestClient(env.app, headers=_AUTH) as client:
        copied = env.copy(client, _terms_bundle(relations=[("tp-xrd", "measures", "tp-si")]))
        r = env.run(client)
        assert r.status_code == 202
        job_id = r.json()["job_id"]
        end = time.time() + 15
        while ai.calls < 6 and time.time() < end:  # X 線回折の 2 組は止まったまま・4 組は済む
            time.sleep(0.02)
        assert ai.calls == 6
        time.sleep(0.2)
        task = env.app.state.jobs.get(job_id).task
        task.get_loop().call_soon_threadsafe(task.cancel)  # 時間切れ相当の力ずくの停止
        end = time.time() + 15
        while not task.done() and time.time() < end:
            time.sleep(0.02)
        assert task.done()
        hold.set()
        ledger = pr.load_ledger(env.root, "lab")
        assert len(ledger["pairs"]) == 4
        assert ledger["dataset_id"] == copied["dataset_id"] and len(ledger["origin"]) == 1
        assert all(e["state"] != "failed" for e in ledger["pairs"].values())
        # 二重起動の印は外れ、続きを走らせられる
        assert not env.app.state.knowledge_relations_running
        again = env.go(client)
    assert again["reused"] == 4 and again["asked"] == 2


class _UsageAI:
    """奇数回目は壊れた JSON・偶数回目で正しい答え（聞き直し）。``last_usage`` は回ごとに増える。"""

    model = "usage-model"

    def __init__(self, *, boom: bool = False) -> None:
        self.n = 0
        self.last_usage = 0
        self.boom = boom

    def complete(self, system: str, user: str) -> str:
        self.n += 1
        if self.boom:
            raise RuntimeError("down")
        self.last_usage = self.n
        if self.n % 2:
            return "{broken"
        return json.dumps({"rel": "none", "from": None, "evidence": [], "note": ""})


def _prepared(tmp_path: Path) -> tuple[_Env, str]:
    env = _env(tmp_path)
    with TestClient(env.app, headers=_AUTH) as client:
        dataset_id = env.copy(client)["dataset_id"]
    return env, dataset_id


def test_usage_is_recorded_per_successful_call(tmp_path: Path) -> None:
    env, dataset_id = _prepared(tmp_path)
    ai = _UsageAI()
    seen: list[int] = []
    out = asyncio.run(kr.run_relations(
        env.root, "lab", dataset_id, make_llm=lambda: ai,
        on_usage=lambda snap: seen.append(snap.last_usage), concurrency=1,
    ))
    # どの組も 2 回聞く（1 回目は壊れた JSON）→ 2 回とも記録される・前の回を二重に数えない
    assert out["asked"] > 0 and out["failed"] == 0
    assert seen == list(range(1, 2 * out["asked"] + 1))


def test_usage_is_not_recorded_for_failed_calls(tmp_path: Path) -> None:
    env, dataset_id = _prepared(tmp_path)
    seen: list[Any] = []
    out = asyncio.run(kr.run_relations(
        env.root, "lab", dataset_id, make_llm=lambda: _UsageAI(boom=True),
        on_usage=seen.append, concurrency=1,
    ))
    assert out["failed"] == out["asked"] > 0 and seen == []


def test_an_early_run_of_failures_stops_asking(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(kr, "ABORT_AFTER_FAILURES", 1)
    env = _env(tmp_path, boom={"Mg2Si", "Mg2Sn", "アーク溶解", "X線回折", "熱伝導率"})
    with TestClient(env.app, headers=_AUTH) as client:
        env.copy(client)
        out = env.go(client)
    assert out["aborted"] is True and out["abort_reason"] == "RuntimeError"
    assert out["asked"] == env.ai.calls == 4  # 走っていた 4 本は受ける・残りは聞かない
    assert out["asked"] < 6
    ledger = pr.load_ledger(env.root, "lab")
    assert len(ledger["pairs"]) == out["asked"] and ledger["dataset_id"]


def test_a_success_keeps_failures_from_aborting(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(kr, "ABORT_AFTER_FAILURES", 3)
    env = _env(tmp_path, boom={"X線回折"})  # 2 組だけ失敗・ほかは成功
    with TestClient(env.app, headers=_AUTH) as client:
        env.copy(client)
        out = env.go(client)
    assert out["aborted"] is False and out["failed"] == 2 and out["asked"] == env.ai.calls


def test_orphans_are_not_returned_or_counted(tmp_path: Path) -> None:
    env = _env(tmp_path)
    with TestClient(env.app, headers=_AUTH) as client:
        env.copy(client)
        env.go(client)
        url = "/api/knowledge-copies/lab/relations"
        before = client.get(url).json()
        key = before["relations"][0]["key"]
        ledger = pr.load_ledger(env.root, "lab")
        ledger["pairs"][key]["orphan"] = True
        pr.save_ledger(env.root, ledger, at="t")
        after = client.get(url).json()
        none = client.get(url, params={"state": "none"}).json()
    assert key not in {r["key"] for r in after["relations"]}
    assert len(after["relations"]) == len(before["relations"]) - 1
    assert after["summary"]["orphans"] == 1
    assert after["summary"]["pairs"] == before["summary"]["pairs"] - 1
    assert key not in {r["key"] for r in none["relations"]}


def test_an_unreadable_ledger_is_a_409_not_an_empty_one(tmp_path: Path) -> None:
    env = _env(tmp_path)
    with TestClient(env.app, headers=_AUTH) as client:
        env.copy(client)
        env.go(client)
        path = pr.ledger_path(env.root, "lab")
        path.chmod(0)
        try:
            if os.access(path, os.R_OK):  # 権限が効かない環境では確かめない
                return
            post = env.run(client)
            get = client.get("/api/knowledge-copies/lab/relations")
        finally:
            path.chmod(0o644)
        assert post.status_code == 409 and get.status_code == 409
        assert "台帳" in get.json()["detail"] and path.exists()


def test_failures_while_a_call_is_in_flight_do_not_abort(tmp_path: Path, monkeypatch) -> None:
    """1 本目だけ遅く成功し、ほかは即失敗。飛行中の成功を待ってから判定するので、打ち切らない。"""
    monkeypatch.setattr(kr, "ABORT_AFTER_FAILURES", 3)

    class FirstSlow(_StubAI):
        def complete(self, system: str, user: str) -> str:
            with self._lock:
                self.calls += 1
                first = self.calls == 1
            if first:
                time.sleep(0.4)
                return json.dumps({"rel": "none", "from": None, "evidence": [], "note": ""})
            raise RuntimeError("429")

    env = _Env(tmp_path, FirstSlow())
    with TestClient(env.app, headers=_AUTH) as client:
        env.copy(client)
        out = env.go(client)
    assert out["aborted"] is False and out["asked"] == env.ai.calls == 6
    assert out["failed"] == 5 and out["kept_stale"] == 0 and out["summary"]["none"] == 1


class _ConcAI(_StubAI):
    """同時に走っている呼び出しの最大数を数える。"""

    def __init__(self) -> None:
        super().__init__()
        self.now = 0
        self.max = 0

    def complete(self, system: str, user: str) -> str:
        with self._lock:
            self.now += 1
            self.max = max(self.max, self.now)
        try:
            time.sleep(0.08)
            return super().complete(system, user)
        finally:
            with self._lock:
                self.now -= 1


@pytest.mark.parametrize(
    ("provider", "expect_one"),
    [("copilot", True), ("copilot-subscription", True), ("openai", False)],
)
def test_copilot_runs_one_call_at_a_time(tmp_path: Path, provider: str, expect_one: bool) -> None:
    env = _Env(tmp_path, _ConcAI(), single_user=True)
    headers = {"X-LLM-Provider": provider, "X-LLM-API-Base": "http://llm.test/v1"}
    with TestClient(env.app, headers=_AUTH) as client:
        env.copy(client)
        r = env.run(client, headers=headers)
        assert r.status_code == 202, r.text
        env.finish(client, r.json()["job_id"])
    assert env.ai.calls == 6
    if expect_one:
        assert env.ai.max == 1
    else:
        assert 1 < env.ai.max <= 4


def test_a_failed_final_save_does_not_replace_the_original_exception(
    tmp_path: Path, monkeypatch
) -> None:
    env, dataset_id = _prepared(tmp_path)

    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("final save failed")

    monkeypatch.setattr(kr.pr, "finalize", boom)
    # 正常に終わる道では、保存の失敗はそのまま上がる
    with pytest.raises(RuntimeError, match="final save failed"):
        asyncio.run(kr.run_relations(env.root, "lab", dataset_id, make_llm=lambda: _StubAI()))

    # 取り消される道では、元の CancelledError が残る（保存の失敗で置き換えない）
    async def cancelled() -> None:
        hold = threading.Event()
        task = asyncio.ensure_future(kr.run_relations(
            env.root, "lab", dataset_id, make_llm=lambda: _SlowXrdAI(hold)
        ))
        await asyncio.sleep(0.5)
        task.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            hold.set()

    asyncio.run(cancelled())


def test_a_failed_call_that_keeps_the_old_entry_counts_as_kept_stale(tmp_path: Path) -> None:
    env = _env(tmp_path)
    with TestClient(env.app, headers=_AUTH) as client:
        env.copy(client)
        env.go(client)
        env.copy(client, _terms_bundle([_claim("c5", "Mg2Si の熱伝導率は温度で変わる")]))
        env.ai.boom = {"熱伝導率"}  # AI が落ちている間
        out = env.go(client)
        rel = client.get("/api/knowledge-copies/lab/relations").json()
    assert out["asked"] == 1 and out["failed"] == 0 and out["kept_stale"] == 1
    assert out["summary"]["failed"] == 0
    assert any(r["rel"] == "affects" and r["to"]["name"] == "熱伝導率" for r in rel["relations"])


class _KindAI(_StubAI):
    """「言えない」のとき、関係の種類（unstated_kind）も返す代役（R1）。"""

    def complete(self, system: str, user: str) -> str:
        obj = json.loads(super().complete(system, user))
        if obj["rel"] == "none":
            obj["unstated_kind"] = "併用"
        return json.dumps(obj)


def test_a_none_row_carries_the_unstated_kind(tmp_path: Path) -> None:
    env = _Env(tmp_path, _KindAI())
    with TestClient(env.app, headers=_AUTH) as client:
        env.copy(client)
        env.go(client)
        none = client.get("/api/knowledge-copies/lab/relations", params={"state": "none"}).json()
        rel = client.get("/api/knowledge-copies/lab/relations").json()
    assert none["relations"] and all(r["unstated_kind"] == "併用" for r in none["relations"])
    assert all("unstated_kind" not in r for r in rel["relations"])  # 関係の行には付かない
    ledger = pr.load_ledger(env.root, "lab")
    assert any(e.get("unstated_kind") == "併用" for e in ledger["pairs"].values())


def test_a_none_row_without_a_kind_has_no_field(tmp_path: Path) -> None:
    env = _env(tmp_path)  # 種類を返さない（古いモデル）
    with TestClient(env.app, headers=_AUTH) as client:
        env.copy(client)
        env.go(client)
        none = client.get("/api/knowledge-copies/lab/relations", params={"state": "none"}).json()
    assert none["relations"] and all("unstated_kind" not in r for r in none["relations"])
