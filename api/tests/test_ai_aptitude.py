"""AI の適性検査（PR 11q）— 推奨／条件つき／非推奨・取り消し・保存の形・鍵が残らない・読むだけ。"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

import pytest
import rdflib
from asterism_step0.theme_propose import THEME_DRAFT_SYSTEM_PROMPT
from fastapi.testclient import TestClient

from asterism_api import ai_aptitude, chat_intent
from asterism_api.main import build_app
from tests.test_crosswalk_api import _AUTH, _DatasetClient, _settings
from tests.test_themes_api import AI_JSON, KEY


class _Fake:
    """台本に応じて正しい JSON／崩れた JSON／別の振り分けを返す偽 LLM。

    ``mode``: good = 全部正しい / limited = 形・振り分けは正しく対象外の変更が混ざる /
    poor = 提案は崩れ、振り分けは半分外れる。"""

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.calls = 0
        self.diffs = 0

    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        if system.startswith("# Asterism chat router"):
            return self._route(user)
        if system == THEME_DRAFT_SYSTEM_PROMPT:
            return "うまく答えられません" if self.mode == "poor" else json.dumps(AI_JSON)
        return self._diff(user)

    def _route(self, user: str) -> str:
        text = user.split("# Message\n", 1)[1]
        expected = dict(ai_aptitude.ROUTING)[text]
        idx = [t for t, _ in ai_aptitude.ROUTING].index(text)
        if self.mode == "poor" and idx % 2 == 0:
            expected = "ask" if expected == "chat" else "chat"
        reply = "どういたしまして。" if expected == "chat" else ""
        return json.dumps({"mode": expected, "reply": reply}, ensure_ascii=False)

    def _diff(self, user: str) -> str:
        self.diffs += 1
        if self.mode == "poor":
            return "[]"
        rename = {"op": "rename", "label_en": "", "aliases": [], "reason": ""}
        if "Only this one relation:" in user:
            head = user.split("Only this one relation: ", 1)[1].split("\n", 1)[0]
            f = head.split(" ", 1)[0]
            rel = head.split("-[", 1)[1].split("]->", 1)[0]
            t = head.split("]-> ", 1)[1].split(" ", 1)[0]
            inside = {"op": "remove", "target": "relation", "from": f, "rel": rel, "to": t,
                      "reason": ""}  # fmt: skip
        else:
            key = user.split("Only this item: ", 1)[1].split(" ", 1)[0]
            inside = {**rename, "key": key, "label_ja": "名前"}
        ops = [inside]
        if self.mode == "limited" and self.diffs <= 3:  # 5 問のうち 3 問に対象外の変更
            outside = "c2" if "c2" not in user.split("Only this", 1)[1].split("\n\n")[0] else "c5"
            ops.append({**rename, "key": outside, "label_ja": "対象外"})
        return json.dumps({"reply": "直します。", "ops": ops}, ensure_ascii=False)


#: 見本の種類名（ask の「？」の文がこれを含むと決定論の ask に当たる）
_NAMES = {
    n: {"iri": f"https://example.org/{i}", "kind": "class"}
    for i, n in enumerate(["値段の記録", "食材の名前", "食材", "値段", "店", "賞味期限"])
}


def test_script_sizes_and_no_deterministic_words() -> None:
    assert len(ai_aptitude.SHAPE_CREATE) == 5 and len(ai_aptitude.SHAPE_DIFF) == 5
    assert len(ai_aptitude.ROUTING) == 30 and ai_aptitude.TOTAL == 40
    kinds = [e for _, e in ai_aptitude.ROUTING]
    assert [kinds.count(k) for k in ("chat", "ask", "propose")] == [10, 10, 10]
    draft = {"concepts": ai_aptitude.GRAPH["concepts"], "relations": ai_aptitude.GRAPH["relations"]}
    for text, _ in ai_aptitude.ROUTING:  # 決定論に当たる文は AI の振り分けを測れない
        assert chat_intent.classify(text, names=_NAMES, draft_open=False) == "graph", text
        assert chat_intent.classify(text, names=_NAMES, draft_open=True) == "graph", text
        assert not chat_intent.is_undo(text), text
        assert not chat_intent.has_graph_words(text), text
        assert not chat_intent.has_fix_words(text), text
        assert chat_intent.parse_remove(text, None) is None, text
        assert chat_intent.parse_remove(text, draft) is None, text


def test_judge_boundaries() -> None:
    j = ai_aptitude.judge
    assert j(0.9, 0.9, 0.8) == "good"
    assert j(0.89, 0.9, 0.8) == "limited" and j(0.9, 0.9, 0.79) == "limited"
    assert j(1.0, 0.7, 1.0) == "limited" and j(1.0, 0.69, 1.0) == "poor"


@pytest.mark.parametrize(
    ("mode", "verdict"), [("good", "good"), ("limited", "limited"), ("poor", "poor")]
)
def test_run_three_verdicts(mode: str, verdict: str) -> None:
    llm = _Fake(mode)
    seen: list[tuple[int, int]] = []
    r = ai_aptitude.run_aptitude(llm, progress=lambda n, t: seen.append((n, t)))
    assert r.verdict == verdict
    assert seen[0] == (1, 40) and seen[-1] == (40, 40) and llm.calls == 40  # 聞き直し無し
    if mode == "good":
        assert (r.shape, r.routing, r.scope, r.failed) == (1.0, 1.0, 1.0, [])
    if mode == "limited":
        assert (r.shape, r.routing, r.scope) == (1.0, 1.0, 0.4)
        assert [f["index"] for f in r.failed if f["kind"] == "scope"] == [0, 1, 2]
    if mode == "poor":
        assert r.shape == 0 and r.routing == 0.5
        assert {f["kind"] for f in r.failed} == {"shape", "scope", "routing"}


def test_cancel_stops_the_run() -> None:
    llm = _Fake("good")
    flag = {"n": 0}

    def cancel() -> bool:
        flag["n"] += 1
        return flag["n"] > 3

    with pytest.raises(ai_aptitude.AptitudeCancelled):
        ai_aptitude.run_aptitude(llm, should_cancel=cancel)
    assert llm.calls == 3


def _api(tmp_path: Path, llm: _Fake):
    cfg = _settings(tmp_path)
    cfg.appdata_root = tmp_path / "appdata"
    cfg.appdata_root.mkdir()
    cfg.single_user = True
    ds = rdflib.Dataset()
    app = build_app(
        cfg, oxigraph_client=_DatasetClient(ds), start_watcher=False,
        llm_factory=lambda key: llm,
    )  # fmt: skip
    return app, ds, cfg


def _events(body: str) -> list[dict]:
    out: list[dict] = []
    name = ""
    for ln in body.splitlines():  # "event: x" の次の "data: {…}" に名前を足す
        if ln.startswith("event: "):
            name = ln[7:]
        elif ln.startswith("data: "):
            out.append({"event": name, **json.loads(ln[6:])})
    return out


def _run(c: TestClient, **headers: str) -> list[dict]:
    r = c.post("/api/ai/aptitude", headers=headers)
    assert r.status_code == 202
    return _events(c.get(f"/api/jobs/{r.json()['job_id']}/stream").text)


def _done(events: list[dict]) -> dict:
    done = [e for e in events if e.get("event") == "done"]
    assert len(done) == 1, events[-2:]
    return done[0]["result"]


def test_api_runs_saves_without_key_and_leaves_store(tmp_path: Path) -> None:
    app, ds, cfg = _api(tmp_path, _Fake("limited"))
    with TestClient(app, headers={**_AUTH, **KEY}) as c:
        events = _run(c, **{"X-LLM-Provider": "openai", "X-LLM-Model": "m1"})
        progress = [e for e in events if e.get("event") == "running" and "n" in e]
        assert progress[0]["n"] == 1 and progress[-1]["n"] == 40 and progress[-1]["total"] == 40
        result = _done(events)
        got = c.get("/api/ai/aptitude").json()["aptitude"]
    entry = got["openai::m1"]
    assert entry["verdict"] == "limited" and entry["scope"] == 0.4
    assert set(entry) == {"verdict", "shape", "routing", "scope", "failed", "at", "model"}
    assert result == {"provider": "openai", **entry}  # GET と done は同じ
    raw = (cfg.appdata_root / "settings.json").read_text("utf-8")
    assert "test-key" not in raw
    assert len(ds) == 0  # 読むだけ


def test_models_coexist_and_rerun_overwrites(tmp_path: Path) -> None:
    llm = _Fake("good")
    app, _, _ = _api(tmp_path, llm)
    with TestClient(app, headers={**_AUTH, **KEY}) as c:
        _run(c, **{"X-LLM-Provider": "openai", "X-LLM-Model": "a"})
        llm.mode, llm.diffs = "poor", 0
        _run(c, **{"X-LLM-Provider": "openai", "X-LLM-Model": "b"})
        got = c.get("/api/ai/aptitude").json()["aptitude"]
        assert got["openai::a"]["verdict"] == "good" and got["openai::b"]["verdict"] == "poor"
        _run(c, **{"X-LLM-Provider": "openai", "X-LLM-Model": "a"})  # poor のまま再実行
        again = c.get("/api/ai/aptitude").json()["aptitude"]
    assert again["openai::a"]["verdict"] == "poor" and again["openai::b"]["verdict"] == "poor"


class _Boom:
    def complete(self, system: str, user: str) -> str:
        raise RuntimeError("provider down")


def test_llm_exception_is_a_job_error_and_not_saved(tmp_path: Path) -> None:
    app, _, cfg = _api(tmp_path, _Boom())  # type: ignore[arg-type]
    with TestClient(app, headers={**_AUTH, **KEY}) as c:
        events = _run(c)
        assert any(e.get("event") == "error" for e in events)
        assert not any(e.get("event") == "done" for e in events)
        assert c.get("/api/ai/aptitude").json() == {"aptitude": {}}
    assert not (cfg.appdata_root / "settings.json").exists()


class _Gated(_Fake):
    """3 回目の呼び出しで止まる（取り消しを試すため）。"""

    def __init__(self) -> None:
        super().__init__("good")
        self.reached = threading.Event()
        self.gate = threading.Event()

    def complete(self, system: str, user: str) -> str:
        if self.calls == 2:
            self.reached.set()
            self.gate.wait(5)
        return super().complete(system, user)


def test_cancel_via_api_saves_nothing(tmp_path: Path) -> None:
    llm = _Gated()
    app, _, cfg = _api(tmp_path, llm)  # type: ignore[arg-type]
    with TestClient(app, headers={**_AUTH, **KEY}) as c:
        job = c.post("/api/ai/aptitude").json()["job_id"]
        assert llm.reached.wait(5)
        assert c.post(f"/api/jobs/{job}/cancel").status_code == 200
        llm.gate.set()
        body = c.get(f"/api/jobs/{job}/stream").text
        assert "cancelled" in body and '"done"' not in body
        deadline = time.time() + 5
        while time.time() < deadline and llm.calls < 4:
            time.sleep(0.05)
        time.sleep(0.3)
        assert llm.calls <= 4  # 次の問の手前で止まる
        assert c.get("/api/ai/aptitude").json() == {"aptitude": {}}
    assert not (cfg.appdata_root / "settings.json").exists()


def test_get_is_empty_without_appdata(tmp_path: Path) -> None:
    ds = rdflib.Dataset()
    app = build_app(
        _settings(tmp_path), oxigraph_client=_DatasetClient(ds), start_watcher=False,
        llm_factory=lambda key: _Fake("good"),
    )  # fmt: skip
    with TestClient(app, headers={**_AUTH, **KEY}) as c:
        assert c.get("/api/ai/aptitude").json() == {"aptitude": {}}
        assert _done(_run(c))["verdict"] == "good"  # 保存はできなくても結果は返る
        assert c.get("/api/ai/aptitude").json() == {"aptitude": {}}


def test_post_needs_write_auth(tmp_path: Path) -> None:
    app, _, _ = _api(tmp_path, _Fake("good"))
    with TestClient(app, headers=KEY) as c:  # トークン無し
        assert c.post("/api/ai/aptitude").status_code in (401, 503)


class _Junk(_Fake):
    """振り分けは JSON でない・提案は崩れる・修正は対象外。"""

    def complete(self, system: str, user: str) -> str:
        if system.startswith("# Asterism chat router"):
            return "no json"
        if system == THEME_DRAFT_SYSTEM_PROMPT:
            return "broken"
        self.mode = "limited"
        self.diffs = 0  # 常に対象外の変更が混ざる
        return super().complete(system, user)


def test_failed_reasons_have_the_agreed_shapes() -> None:
    r = ai_aptitude.run_aptitude(_Junk("good"))
    reasons = {(f["kind"], f["reason"]) for f in r.failed}
    assert ("shape", "create") in reasons and ("routing", "unreadable") in reasons
    assert ("scope", "out_of_scope") in reasons
    r2 = ai_aptitude.run_aptitude(_Fake("poor"))
    assert {f["reason"] for f in r2.failed if f["kind"] == "shape"} == {"create", "diff"}
    assert {f["reason"] for f in r2.failed if f["kind"] == "scope"} == {"unreadable"}
    wrong = [f["reason"] for f in r2.failed if f["kind"] == "routing"]
    assert wrong and all(re.fullmatch(r"(chat|ask|propose)->(chat|ask|propose)", w) for w in wrong)
    assert all(isinstance(f["index"], int) and f["index"] >= 0 for f in r2.failed)


def test_cancel_after_the_last_question() -> None:
    n = {"i": 0}

    def cancel() -> bool:
        n["i"] += 1
        return n["i"] > ai_aptitude.TOTAL  # 40 問すべての手前では False・最後のあとで True

    with pytest.raises(ai_aptitude.AptitudeCancelled):
        ai_aptitude.run_aptitude(_Fake("good"), should_cancel=cancel)


def test_api_refuses_without_a_key(tmp_path: Path) -> None:
    app, _, _ = _api(tmp_path, _Fake("good"))
    with TestClient(app, headers=_AUTH) as c:
        assert c.post("/api/ai/aptitude").status_code == 400


def test_shape_note_points_to_the_check() -> None:
    from asterism_api import chat_turns

    assert "適性を調べる" in chat_turns._shape_note("形が崩れました", "", "ja")
    assert "Check aptitude" in chat_turns._shape_note("Bad shape", "", "en")


def test_put_settings_keeps_aptitude_and_ignores_the_put_one(tmp_path: Path) -> None:
    app, _, cfg = _api(tmp_path, _Fake("good"))
    mine = {"p::m": {"verdict": "good"}}
    (cfg.appdata_root / "settings.json").write_text(json.dumps({"aptitude": mine}), "utf-8")
    with TestClient(app, headers={**_AUTH, **KEY}) as c:
        assert c.put("/api/appdata/settings", json={"theme": "x"}).status_code == 200
        assert c.get("/api/appdata/settings").json()["settings"] == {"theme": "x", "aptitude": mine}
        fake = {"aptitude": {"p::m": {"verdict": "poor"}}, "theme": "y"}
        c.put("/api/appdata/settings", json=fake)
        assert c.get("/api/appdata/settings").json()["settings"]["aptitude"] == mine
