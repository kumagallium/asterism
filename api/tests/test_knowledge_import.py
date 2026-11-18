# ruff: noqa: E501
"""取り込みのジョブ・結果の 1 枚・一覧・外す・同時に 1 つだけ（PR 14d-2a・契約書 §1・§4）。

作り物の束（Mg2Si・Mg2Sn・アーク溶解・X 線回折・熱伝導率）と、スタブの AI
（``test_knowledge_relations`` と同じ）で動かす。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from asterism import pair_relations as pr
from fastapi.testclient import TestClient
from fastmcp import FastMCP
from fastmcp.client.transports import FastMCPTransport

from asterism_api import knowledge_relations as kr
from asterism_api import registry
from asterism_api.knowledge_copy import CopyError, find_copy_dataset
from asterism_api.knowledge_import import run_import
from asterism_api.main import build_app
from asterism_api.mcp_client import McpClient

from .test_ingest import _AUTH, _settings
from .test_knowledge_copy import _SERVERS
from .test_knowledge_relations import _KEY, _Env, _env, _terms_bundle
from .test_mcp_client import _single_user_settings, _write_settings
from .test_mcp_resync_rows import _RecOxi

pytest.importorskip("morph_kgc", reason="the copy runs the real declarative ingest")


def _start(client: TestClient, body: dict | None = None, headers: dict | None = _KEY):
    payload = {"source": "lab", "bundle": _terms_bundle(), **(body or {})}
    return client.post("/api/knowledge-imports", json=payload, headers=headers)


def _events(client: TestClient, job_id: str) -> list[dict]:
    out = []
    for line in client.get(f"/api/jobs/{job_id}/stream").text.splitlines():
        if line.startswith("data:"):
            out.append(json.loads(line[5:]))
    return out


def _result(client: TestClient, job_id: str) -> dict:
    for ev in _events(client, job_id):
        if "result" in ev:
            return ev["result"]
    raise AssertionError("job did not finish with a result")


def _go(client: TestClient, body: dict | None = None, headers: dict | None = _KEY) -> dict:
    r = _start(client, body, headers)
    assert r.status_code == 202, r.text
    return _result(client, r.json()["job_id"])


def _wait(cond, seconds: float = 15.0) -> None:
    end = time.time() + seconds
    while not cond() and time.time() < end:
        time.sleep(0.02)
    assert cond()


def test_the_job_copies_then_picks_relations_and_returns_one_card(tmp_path: Path) -> None:
    env = _env(tmp_path)
    with TestClient(env.app, headers=_AUTH) as client:
        r = _start(client)
        assert r.status_code == 202, r.text
        events = _events(client, r.json()["job_id"])
    stages = [e["stage"] for e in events if "stage" in e]
    # 段は copy → pairs（n/N）→ done の順（MCP でないので fetch は無い）
    assert stages[0] == "copy" and stages[-1] == "done" and "fetch" not in stages
    assert stages.index("pairs") > stages.index("copy")
    assert any(e.get("message") == "0/6" for e in events)
    out = next(e["result"] for e in events if "result" in e)
    assert out["source"] == "lab" and out["title"] == "Lab" and out["created"] is True
    assert out["dataset_id"] == find_copy_dataset(env.root, "lab")
    assert out["copied"]["claims"] == 4 and out["copied"]["terms"] == 5
    assert out["copied"]["dropped"] == {}
    rel = out["relations"]
    assert rel["skipped"] is None and rel["cancelled"] is False and rel["aborted"] is False
    assert rel["asked"] == env.ai.calls > 0 and rel["failed"] == 0 and rel["reused"] == 0
    assert rel["shown"] >= 4 and rel["terms"] >= 4 and rel["by_rel"]["makes"] == 1
    assert out["questions_total"] >= len(out["questions"]) > 0
    assert len(out["questions"]) <= 20
    first = out["questions"][0]
    assert set(first) >= {"op", "term", "text", "count"} and set(first["term"]) == {"id", "name"}
    counts = [q["count"] for q in out["questions"]]
    assert counts == sorted(counts, reverse=True)
    # アーク溶解で作るもの（Mg2Si・Mg2Sn）と、熱伝導率を変えるもの
    texts = {q["text"] for q in out["questions"]}
    assert "アーク溶解 で作るもの" in texts and "熱伝導率 を変えるもの" in texts
    assert registry.load_meta(env.root, out["dataset_id"])["knowledge_source"]["title"] == "Lab"


def test_no_ai_or_relations_false_never_calls_the_ai_but_keeps_origin_relations(
    tmp_path: Path,
) -> None:
    env = _env(tmp_path)
    with TestClient(env.app, headers=_AUTH) as client:
        unset = _go(client, headers={})  # X-LLM / X-API-Key 無し = AI 未設定
        off = _go(client, {"relations": False})
    assert unset["relations"]["skipped"] == "ai_unset" and off["relations"]["skipped"] == "off"
    assert env.ai.calls == 0 and env.resolved == 0
    for out in (unset, off):
        rel = out["relations"]
        assert rel["asked"] == 0 and rel["failed"] == 0
        assert rel["shown"] == 1  # 取り込み元が知っている関係（Mg2Si の熱伝導率）だけ
    assert unset["created"] is True and off["created"] is False
    ledger = pr.load_ledger(env.root, "lab")
    assert ledger["pairs"] == {} and [o["rel"] for o in ledger["origin"]] == ["has_property"]


def test_a_second_import_asks_nothing_and_reuses_the_dataset(tmp_path: Path) -> None:
    env = _env(tmp_path)
    with TestClient(env.app, headers=_AUTH) as client:
        first = _go(client)
        calls = env.ai.calls
        second = _go(client)
        # AI を使わない取り込みでも、前の回の結果は残る
        third = _go(client, {"relations": False})
    assert second["created"] is False and second["dataset_id"] == first["dataset_id"]
    assert second["relations"]["asked"] == 0 and env.ai.calls == calls
    assert second["relations"]["reused"] == first["relations"]["asked"]
    assert second["relations"]["shown"] == first["relations"]["shown"]
    assert third["relations"]["shown"] == first["relations"]["shown"]


def test_a_failed_copy_fails_the_job_and_changes_nothing(tmp_path: Path) -> None:
    env = _env(tmp_path)
    bundle = _terms_bundle()
    bundle["files"]["claims.json"] = []
    with TestClient(env.app, headers=_AUTH) as client:
        r = _start(client, {"bundle": bundle})
        assert r.status_code == 202
        events = _events(client, r.json()["job_id"])
        listed = client.get("/api/knowledge-imports").json()
        busy = getattr(env.app.state, "knowledge_busy", {})
    assert [e for e in events if "result" in e] == []
    err = next(e for e in events if e.get("message") and "知見が 1 件もありません" in e["message"])
    assert err
    assert registry.list_datasets(env.root) == [] and listed == {"sources": []}
    assert not pr.ledger_path(env.root, "lab").exists() and "lab" not in busy
    assert env.ai.calls == 0 and env.app.state.knowledge_copy_running == set()


def test_bad_bodies_are_refused_before_a_job_starts(tmp_path: Path) -> None:
    env = _env(tmp_path)
    with TestClient(env.app, headers=_AUTH) as client:
        assert _start(client, {"source": "bad name!"}).status_code == 400
        assert _start(client, {"relations": "yes"}).status_code == 400
        assert client.post("/api/knowledge-imports", content=b"x").status_code == 400
        assert client.post("/api/knowledge-imports", json={"bundle": {}}).status_code == 400
    with TestClient(env.app) as anon:
        assert anon.post("/api/knowledge-imports", json={}).status_code == 401
        assert anon.get("/api/knowledge-imports").status_code == 401
        assert anon.delete("/api/knowledge-imports/lab").status_code == 401
    assert registry.list_datasets(env.root) == []


def test_an_early_run_of_failures_aborts_but_the_job_still_succeeds(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(kr, "ABORT_AFTER_FAILURES", 1)
    env = _env(tmp_path, boom={"Mg2Si", "Mg2Sn", "アーク溶解", "X線回折", "熱伝導率"})
    with TestClient(env.app, headers=_AUTH) as client:
        out = _go(client)
    rel = out["relations"]
    assert rel["aborted"] is True and rel["abort_reason"] == "RuntimeError" and rel["skipped"] is None
    assert out["dataset_id"] and find_copy_dataset(env.root, "lab") == out["dataset_id"]


def test_a_failing_relations_stage_does_not_fail_the_job(tmp_path: Path, monkeypatch) -> None:
    async def boom(*a: Any, **kw: Any):
        raise RuntimeError("台帳が壊れた")

    monkeypatch.setattr("asterism_api.knowledge_import.run_relations", boom)
    env = _env(tmp_path)
    with TestClient(env.app, headers=_AUTH) as client:
        out = _go(client)
    assert out["relations"]["skipped"] == "error" and "RuntimeError" in out["relations"]["error"]
    assert out["dataset_id"] and out["copied"]["claims"] == 4


@pytest.mark.asyncio
async def test_a_cancel_in_the_pairs_stage_ends_with_cancelled_and_keeps_the_copy(
    tmp_path: Path,
) -> None:
    env = _env(tmp_path)
    token = _AUTH["X-Asterism-Token"]
    stages: list[str] = []
    async with env.app.router.lifespan_context(env.app):
        out = await run_import(
            env.app, env.root, source="lab", raw_bundle=_terms_bundle(), token=token,
            make_llm=lambda: env.ai, emit=lambda **kw: stages.append(kw.get("stage", "")),
            should_cancel=lambda: "pairs" in stages,  # 写しが終わって pairs に入ってから取り消す
        )
    assert out["relations"]["cancelled"] is True and out["relations"]["asked"] == 0
    assert out["dataset_id"] and find_copy_dataset(env.root, "lab") == out["dataset_id"]
    assert env.ai.calls == 0 and "copy" in stages and stages[-1] == "done"


@pytest.mark.asyncio
async def test_run_import_raises_for_a_bad_bundle(tmp_path: Path) -> None:
    env = _env(tmp_path)
    async with env.app.router.lifespan_context(env.app):
        with pytest.raises(CopyError) as exc:
            await run_import(
                env.app, env.root, source="lab", raw_bundle={"format": "nope"},
                token=_AUTH["X-Asterism-Token"],
            )
    assert exc.value.status == 422 and registry.list_datasets(env.root) == []


def test_the_list_shows_counts_unconfirmed_and_running(tmp_path: Path) -> None:
    env = _env(tmp_path)
    with TestClient(env.app, headers=_AUTH) as client:
        assert client.get("/api/knowledge-imports").json() == {"sources": []}
        out = _go(client)
        other = client.post("/api/knowledge-copies", json={"source": "alpha", "bundle": _terms_bundle()})
        assert other.status_code == 200
        ledger = pr.load_ledger(env.root, "lab")
        key = next(k for k, e in ledger["pairs"].items() if e["state"] == "relation")
        ledger["pairs"][key]["confirmed"] = {"by": "user", "at": "t"}
        pr.save_ledger(env.root, ledger, at="t")
        listed = client.get("/api/knowledge-imports").json()["sources"]
    assert [s["source"] for s in listed] == ["alpha", "lab"]
    alpha, lab = listed
    assert alpha["relations"] == {"shown": 0, "terms": 0, "candidates": 0, "unconfirmed": 0, "confirmed": 0}
    assert alpha["running"] is None and alpha["current"] is True
    assert lab["dataset_id"] == out["dataset_id"] and lab["title"] == "Lab"
    assert lab["counts"] == {"claims": 4, "topics": 5, "sources": 0, "terms": 5}
    assert lab["copied_at"] and lab["exported_at"] and lab["current"] is True and lab["running"] is None
    rel = lab["relations"]
    assert rel["shown"] == out["relations"]["shown"] and rel["confirmed"] == 1
    assert rel["unconfirmed"] == rel["shown"] - 1


def test_remove_retracts_the_copy_and_keeps_only_confirmed(tmp_path: Path) -> None:
    env = _env(tmp_path)
    with TestClient(env.app, headers=_AUTH) as client:
        assert client.delete("/api/knowledge-imports/lab").status_code == 404
        out = _go(client)
        shown = out["relations"]["shown"]
        # 確認済みが無い: 台帳のファイルごと消える
        gone = client.delete("/api/knowledge-imports/lab")
        assert gone.status_code == 200, gone.text
        assert gone.json() == {
            "removed": {"dataset_id": out["dataset_id"], "relations": shown, "kept_confirmed": 0,
                        "kept_rejected": 0}
        }
        assert registry.load_meta(env.root, out["dataset_id"])["status"] == "retracted"
        assert not pr.ledger_path(env.root, "lab").exists()
        assert client.get("/api/knowledge-imports").json() == {"sources": []}
        assert client.delete("/api/knowledge-imports/lab").status_code == 404
        # 入れ直すと新しい写しになる。確認済みの 1 件は外しても残る
        again = _go(client)
        assert again["created"] is True and again["dataset_id"] != out["dataset_id"]
        ledger = pr.load_ledger(env.root, "lab")
        key = next(k for k, e in ledger["pairs"].items() if e["state"] == "relation")
        ledger["pairs"][key]["confirmed"] = {"by": "user", "at": "t"}
        pr.save_ledger(env.root, ledger, at="t")
        kept = client.delete("/api/knowledge-imports/lab")
    assert kept.status_code == 200
    removed = kept.json()["removed"]
    assert removed["kept_confirmed"] == 1 and removed["relations"] == again["relations"]["shown"] - 1
    after = pr.load_ledger(env.root, "lab")
    assert list(after["pairs"]) == [key] and after["origin"] == []
    assert registry.load_meta(env.root, again["dataset_id"])["status"] == "retracted"


def test_only_one_job_per_source_across_the_four_routes(tmp_path: Path) -> None:
    gate = threading.Event()
    env = _env(tmp_path, gate=gate)
    with TestClient(env.app, headers=_AUTH) as client:
        copied = env.copy(client)
        first = _start(client)
        assert first.status_code == 202
        _wait(lambda: env.ai.calls >= 1)  # 関係を選んでいる途中
        busy = {
            "copy": client.post("/api/knowledge-copies", json={"source": "lab", "bundle": _terms_bundle()}),
            "relations": env.run(client),
            "import": _start(client),
            "remove": client.delete("/api/knowledge-imports/lab"),
        }
        listed = client.get("/api/knowledge-imports").json()["sources"]
        other = client.post("/api/knowledge-copies", json={"source": "other", "bundle": _terms_bundle()})
        gate.set()
        out = _result(client, first.json()["job_id"])
        later = _start(client)  # 終わったらまた入れられる（印が残らない）
        assert later.status_code == 202
        _result(client, later.json()["job_id"])
    assert {k: r.status_code for k, r in busy.items()} == {k: 409 for k in busy}
    assert "import" in busy["copy"].text and "import" in busy["relations"].text
    assert listed[0]["running"] == "import" and other.status_code == 200
    assert out["dataset_id"] == copied["dataset_id"] and out["created"] is False
    assert getattr(env.app.state, "knowledge_busy", {}) == {}


def test_a_running_relations_job_blocks_copy_import_and_remove(tmp_path: Path) -> None:
    gate = threading.Event()
    env = _env(tmp_path, gate=gate)
    with TestClient(env.app, headers=_AUTH) as client:
        env.copy(client)
        r = env.run(client)
        assert r.status_code == 202
        _wait(lambda: env.ai.calls >= 1)
        statuses = [
            client.post("/api/knowledge-copies", json={"source": "lab", "bundle": _terms_bundle()}).status_code,
            _start(client).status_code,
            client.delete("/api/knowledge-imports/lab").status_code,
            env.run(client).status_code,
        ]
        running = client.get("/api/knowledge-imports").json()["sources"][0]["running"]
        gate.set()
        env.finish(client, r.json()["job_id"])
        done = client.delete("/api/knowledge-imports/lab").status_code
    assert statuses == [409, 409, 409, 409] and running == "relations" and done == 200


# ---- MCP の道 ---------------------------------------------------------------------------------


def _mcp(tmp_path: Path, provider: dict) -> tuple[TestClient, dict, _Env]:
    oxi = _RecOxi()
    settings = _single_user_settings(tmp_path)
    _write_settings(tmp_path, {"mcpServers": _SERVERS, "sourceProvider": provider})
    app = build_app(settings, oxigraph_client=oxi.client, start_watcher=False)
    seen: dict = {}
    srv = FastMCP("fake-knowledge")

    @srv.tool
    def export_knowledge() -> str:
        seen["calls"] = seen.get("calls", 0) + 1
        return json.dumps(_terms_bundle(), ensure_ascii=False)

    app.state.mcp_registry.client = McpClient(transport_factory=lambda cfg: FastMCPTransport(srv))
    env = _Env.__new__(_Env)
    env.root = tmp_path / "registry"
    return TestClient(app, headers=_AUTH), seen, env


def test_mcp_route_fetches_then_imports(tmp_path: Path) -> None:
    client, seen, env = _mcp(tmp_path, {"knowledge": "export_knowledge"})
    with client:
        r = client.post("/api/mcp-servers/fake/knowledge-import", json={"lang": "en"})
        assert r.status_code == 202, r.text
        assert r.json()["server"] == "fake"
        events = _events(client, r.json()["job_id"])
        listed = client.get("/api/knowledge-imports").json()["sources"]
    stages = [e["stage"] for e in events if "stage" in e]
    assert stages[0] == "fetch" and stages.index("copy") > 0 and stages[-1] == "done"
    out = next(e["result"] for e in events if "result" in e)
    assert seen == {"calls": 1} and out["source"] == "fake" and out["created"] is True
    assert out["relations"]["skipped"] == "ai_unset" and out["relations"]["shown"] == 1
    assert {q["text"] for q in out["questions"]} == {"Properties of Mg2Si", "Materials that have 熱伝導率"}
    assert listed[0]["source"] == "fake" and find_copy_dataset(env.root, "fake")


def test_mcp_route_trust_boundary(tmp_path: Path) -> None:
    client, seen, env = _mcp(tmp_path, {"list": "list_media"})
    with client:
        # 束を出す道具が設定されていない・無効なサーバ・知らないサーバ・本文が壊れている
        assert client.post("/api/mcp-servers/fake/knowledge-import").status_code == 409
        assert client.post("/api/mcp-servers/off/knowledge-import").status_code == 409
        assert client.post("/api/mcp-servers/nope/knowledge-import").status_code == 404
        assert client.post("/api/mcp-servers/fake/knowledge-import", content=b"x").status_code == 400
    assert seen == {} and registry.list_datasets(env.root) == []


def test_mcp_route_needs_the_single_user_mode_and_the_token(tmp_path: Path) -> None:
    env = _env(tmp_path)  # 単一ユーザーモードでない
    with TestClient(env.app, headers=_AUTH) as shared:
        assert shared.post("/api/mcp-servers/fake/knowledge-import").status_code == 404
    client, _seen, _e = _mcp(tmp_path / "x", {"knowledge": "export_knowledge"})
    with client:
        anon = TestClient(client.app)
        assert anon.post("/api/mcp-servers/fake/knowledge-import").status_code == 401


# ---- レビューの直し -------------------------------------------------------------------------


def _import_with_one_relation(client: TestClient, env: _Env):
    out = _go(client)
    ledger = pr.load_ledger(env.root, "lab")
    key = next(k for k, e in ledger["pairs"].items() if e["state"] == "relation")
    return out, ledger, key


def test_remove_trims_the_ledger_first_so_a_failed_retract_can_be_retried(
    tmp_path: Path, monkeypatch
) -> None:
    env = _env(tmp_path)
    with TestClient(env.app, headers=_AUTH, raise_server_exceptions=False) as client:
        out, ledger, key = _import_with_one_relation(client, env)
        ledger["pairs"][key]["confirmed"] = {"by": "user", "at": "t"}
        pr.save_ledger(env.root, ledger, at="t")
        real = registry.mark_retracted

        def boom(*a: Any, **kw: Any):
            raise OSError("disk")

        monkeypatch.setattr(registry, "mark_retracted", boom)
        failed = client.delete("/api/knowledge-imports/lab")
        assert failed.status_code >= 500
        # 台帳は整った状態・写しはまだ取り下げられていない（見つかる）
        assert list(pr.load_ledger(env.root, "lab")["pairs"]) == [key]
        assert find_copy_dataset(env.root, "lab") == out["dataset_id"]
        assert registry.load_meta(env.root, out["dataset_id"]).get("status") != "retracted"
        assert getattr(env.app.state, "knowledge_busy", {}) == {}
        monkeypatch.setattr(registry, "mark_retracted", real)
        again = client.delete("/api/knowledge-imports/lab")
    assert again.status_code == 200, again.text
    assert again.json()["removed"]["kept_confirmed"] == 1
    assert registry.load_meta(env.root, out["dataset_id"])["status"] == "retracted"
    assert list(pr.load_ledger(env.root, "lab")["pairs"]) == [key]


def test_remove_does_not_retract_when_the_ledger_cannot_be_saved(tmp_path: Path, monkeypatch) -> None:
    env = _env(tmp_path)
    with TestClient(env.app, headers=_AUTH) as client:
        out = _go(client)

        def boom(*a: Any, **kw: Any):
            raise OSError("disk full")

        monkeypatch.setattr(pr, "remove_ledger", boom)
        r = client.delete("/api/knowledge-imports/lab")
        assert r.status_code == 500
        assert registry.load_meta(env.root, out["dataset_id"]).get("status") != "retracted"
        assert find_copy_dataset(env.root, "lab") == out["dataset_id"]
        assert getattr(env.app.state, "knowledge_busy", {}) == {}


def test_a_rejected_triple_survives_remove_and_is_not_offered_again(tmp_path: Path) -> None:
    env = _env(tmp_path)
    with TestClient(env.app, headers=_AUTH) as client:
        _out, ledger, key = _import_with_one_relation(client, env)
        e = ledger["pairs"][key]
        e["rejected"] = [{"rel": e["rel"], "from": e["from"], "to": e["to"], "at": "t"}]
        pr.save_ledger(env.root, ledger, at="t")
        removed = client.delete("/api/knowledge-imports/lab").json()["removed"]
        assert removed["kept_rejected"] == 1 and removed["kept_confirmed"] == 0
        kept = pr.load_ledger(env.root, "lab")["pairs"]
        assert list(kept) == [key] and kept[key]["orphan"] is True and "rel" not in kept[key]
        again = _go(client)
        assert again["created"] is True
    after = pr.load_ledger(env.root, "lab")["pairs"][key]
    assert after["state"] == "dropped" and after["reason"] == "rejected"
    assert after["rejected"] and not after.get("orphan")


def test_an_ai_that_cannot_be_built_stops_before_the_job(tmp_path: Path) -> None:
    env = _env(tmp_path)
    copilot = {"X-LLM-Provider": "copilot-subscription"}  # 共有サーバでは使えない
    with TestClient(env.app, headers=_AUTH) as client:
        refused = _start(client, headers=copilot)
        assert registry.list_datasets(env.root) == []  # 取り込みを始めていない
        env.copy(client)
        same = client.post("/api/knowledge-copies/lab/relations", json={}, headers=copilot)
        relations_off = _start(client, {"relations": False}, headers=copilot)
        assert relations_off.status_code == 202
        _result(client, relations_off.json()["job_id"])
    # 既存の …/relations の口と同じ status・文言
    assert refused.status_code == same.status_code == 400
    assert refused.json() == same.json()
    assert env.ai.calls == 0


def test_a_generic_failure_to_build_the_ai_is_a_409_not_a_silent_skip(tmp_path: Path) -> None:
    def resolver(*a: Any, **kw: Any):
        raise RuntimeError("no client")

    from asterism_api.main import build_app as _build

    oxi = _RecOxi()
    app = _build(_settings(tmp_path), oxigraph_client=oxi.client, start_watcher=False,
                 llm_resolver=resolver)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post("/api/knowledge-imports", json={"source": "lab", "bundle": _terms_bundle()},
                        headers=_KEY)
        assert registry.list_datasets(tmp_path / "registry") == []
        off = client.post("/api/knowledge-imports",
                          json={"source": "lab", "bundle": _terms_bundle(), "relations": False},
                          headers=_KEY)
    assert r.status_code == 409 and "AI" in r.text
    assert off.status_code == 202


@pytest.mark.asyncio
async def test_a_cancel_before_any_stage_makes_no_copy(tmp_path: Path) -> None:
    env = _env(tmp_path)
    fetched: list[int] = []

    async def fetch() -> Any:
        fetched.append(1)
        return _terms_bundle()

    async with env.app.router.lifespan_context(env.app):
        before = await run_import(
            env.app, env.root, source="lab", fetch=fetch, token=_AUTH["X-Asterism-Token"],
            make_llm=lambda: env.ai, should_cancel=lambda: True,
        )
        # fetch の後に取り消されたら、写す前に終える
        flips = iter([False, True])
        after = await run_import(
            env.app, env.root, source="lab", fetch=fetch, token=_AUTH["X-Asterism-Token"],
            make_llm=lambda: env.ai, should_cancel=lambda: next(flips),
        )
    assert before["relations"]["cancelled"] is True and before["dataset_id"] is None
    assert after["relations"]["cancelled"] is True and after["dataset_id"] is None
    assert fetched == [1]
    assert registry.list_datasets(env.root) == [] and env.ai.calls == 0


def test_mcp_route_accepts_an_empty_or_missing_body(tmp_path: Path) -> None:
    client, _seen, _env2 = _mcp(tmp_path, {"knowledge": "export_knowledge"})
    with client:
        a = client.post("/api/mcp-servers/fake/knowledge-import")
        _result(client, a.json()["job_id"])
        b = client.post("/api/mcp-servers/fake/knowledge-import", content=b"")
        _result(client, b.json()["job_id"])
        c = client.post("/api/mcp-servers/fake/knowledge-import", json=[1])
    assert a.status_code == b.status_code == 202 and c.status_code == 400


def test_a_rejected_only_entry_does_not_disturb_an_import_without_the_ai(tmp_path: Path) -> None:
    env = _env(tmp_path)
    with TestClient(env.app, headers=_AUTH) as client:
        _out, ledger, key = _import_with_one_relation(client, env)
        e = ledger["pairs"][key]
        e["rejected"] = [{"rel": e["rel"], "from": e["from"], "to": e["to"], "at": "t"}]
        pr.save_ledger(env.root, ledger, at="t")
        client.delete("/api/knowledge-imports/lab")
        calls = env.ai.calls
        quiet = _go(client, {"relations": False})  # AI なしで入れ直す
        read = client.get("/api/knowledge-copies/lab/relations", params={"state": "none"}).json()
        listed = client.get("/api/knowledge-imports").json()["sources"][0]
        assert env.ai.calls == calls
        # 数・一覧は取り込み元の関係 1 本だけ。縮めた件は数えず・出さない
        assert quiet["relations"]["shown"] == 1 and read["summary"]["pairs"] == 0
        assert read["summary"]["failed"] == 0 and read["summary"]["orphans"] == 0
        assert read["relations"] == [] and listed["relations"]["shown"] == 1
        assert listed["relations"]["unconfirmed"] == 1
        # 次に聞く回で正しい件になり、rejected の 3 つ組は出ない
        full = _go(client)
    after = pr.load_ledger(env.root, "lab")["pairs"][key]
    assert full["relations"]["asked"] > 0
    assert after["state"] == "dropped" and after["reason"] == "rejected" and after["rejected"]
