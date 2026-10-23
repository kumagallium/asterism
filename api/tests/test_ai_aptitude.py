"""AI の適性検査（PR 11q）— 推奨／条件つき／非推奨・取り消し・保存の形・鍵が残らない・読むだけ。"""

from __future__ import annotations

import json
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


def test_script_sizes_and_no_deterministic_words() -> None:
    assert len(ai_aptitude.SHAPE_CREATE) == 5 and len(ai_aptitude.SHAPE_DIFF) == 5
    assert len(ai_aptitude.ROUTING) == 30 and ai_aptitude.TOTAL == 40
    kinds = [e for _, e in ai_aptitude.ROUTING]
    assert [kinds.count(k) for k in ("chat", "ask", "propose")] == [10, 10, 10]
    for text, _ in ai_aptitude.ROUTING:  # 決定論に当たる文は AI の振り分けを測れない
        assert not chat_intent.has_graph_words(text), text
        assert not chat_intent.has_fix_words(text), text


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
    ds = rdflib.Dataset()
    app = build_app(
        cfg, oxigraph_client=_DatasetClient(ds), start_watcher=False,
        llm_factory=lambda key: llm,
    )  # fmt: skip
    return app, ds, cfg


def test_api_runs_saves_without_key_and_leaves_store(tmp_path: Path) -> None:
    app, ds, cfg = _api(tmp_path, _Fake("limited"))
    with TestClient(app, headers={**_AUTH, **KEY}) as c:
        r = c.post("/api/ai/aptitude", headers={"X-LLM-Provider": "openai", "X-LLM-Model": "m1"})
        assert r.status_code == 202
        body = c.get(f"/api/jobs/{r.json()['job_id']}/stream").text
        assert '"n": 40' in body.replace('"n":40', '"n": 40') and '"total": 40' in body.replace(
            '"total":40', '"total": 40'
        )
        assert "event: done" in body or '"event": "done"' in body or "done" in body
        got = c.get("/api/ai/aptitude").json()["aptitude"]
    entry = got["openai::m1"]
    assert entry["verdict"] == "limited" and entry["scope"] == 0.4
    assert set(entry) == {"verdict", "shape", "routing", "scope", "failed", "at", "model"}
    raw = (cfg.appdata_root / "settings.json").read_text("utf-8")
    assert "test-key" not in raw
    assert len(ds) == 0  # 読むだけ


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
