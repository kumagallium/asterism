# ruff: noqa: E501
"""台帳の関係を人が確認する口（PR 14e-1・契約書 §1.2）。例は作り物（test_knowledge_relations と同じ束）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from asterism import pair_relations as pr
from fastapi.testclient import TestClient

from .test_ingest import _AUTH
from .test_knowledge_relations import _Env, _env

pytest.importorskip("morph_kgc", reason="the copy runs the real declarative ingest")

_URL = "/api/knowledge-copies/lab/relations"


def _ready(tmp_path: Path) -> tuple[_Env, TestClient]:
    env = _env(tmp_path)
    client = TestClient(env.app, headers=_AUTH)
    client.__enter__()
    env.copy(client)
    env.go(client)
    return env, client


def _rows(client: TestClient, state: str = "all") -> dict[str, dict]:
    body = client.get(_URL, params={"state": state}).json()
    return {r["key"]: r for r in body["relations"]}


def _review(client: TestClient, action: str, keys: list[str]) -> Any:
    return client.post(f"{_URL}/review", json={"action": action, "keys": keys})


def _first(rows: dict[str, dict], **want: Any) -> dict:
    return next(r for r in rows.values() if all(r.get(k) == v for k, v in want.items()))


def test_confirm_unconfirm_の往復と_行の新しい欄(tmp_path: Path) -> None:
    env, client = _ready(tmp_path)
    with client:
        row = _first(_rows(client), state="relation", rel="makes")
        assert row["confirmed"] is None and row["by"] == "ai" and row["rejected"] is False
        r = _review(client, "confirm", [row["key"], "zz|yy"])
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["changed"] == [row["key"]]
        assert body["skipped"] == [{"key": "zz|yy", "reason": "unknown"}]
        assert body["summary"]["relations"] >= 4
        assert _rows(client)[row["key"]]["confirmed"] is True
        led = pr.load_ledger(env.root, "lab")
        assert led["pairs"][row["key"]]["confirmed"]["by"] == "user"
        # 何度かけても同じ
        assert _review(client, "confirm", [row["key"]]).json()["changed"] == []
        back = _review(client, "unconfirm", [row["key"]])
        assert back.json()["changed"] == [row["key"]]
        assert _rows(client)[row["key"]]["confirmed"] is None


def test_reject_と_restore_違うにした関係は状態_all_と_dropped_に出る(tmp_path: Path) -> None:
    env, client = _ready(tmp_path)
    with client:
        row = _first(_rows(client), state="relation", rel="makes")
        assert _review(client, "reject", [row["key"]]).json()["changed"] == [row["key"]]
        assert row["key"] not in _rows(client, "relation")
        gone = _rows(client)[row["key"]]
        assert gone["rejected"] is True and gone["state"] == "dropped" and gone["reason"] == "rejected"
        assert row["key"] in _rows(client, "dropped")
        # 読み直し（ジョブをもう一度）でも出ない
        env.go(client)
        assert row["key"] not in _rows(client, "relation")
        back = _review(client, "restore", [row["key"]]).json()
        assert back["changed"] == [row["key"]]
        assert _rows(client, "relation")[row["key"]]["rejected"] is False


def test_取り込み元の行を確認する_違うにする(tmp_path: Path) -> None:
    env, client = _ready(tmp_path)
    with client:
        # 題から来た「Mg2Si の 熱伝導率」は AI と食い違い候補になっている。違う関係の行で試す
        body = client.get(_URL, params={"state": "all"}).json()
        o = body["origin"]
        assert o and all("key" in x for x in o)
        # AI が読んでいない行を作る: 台帳の origin を unseen に書き換えて保存する
        led = pr.load_ledger(env.root, "lab")
        key = o[0]["key"]
        del led["pairs"][key]
        for row in led["origin"]:
            row["ai"] = "unseen"
        pr.save_ledger(env.root, led, at="2026-11-18T00:00:00+00:00")
        shown = client.get(_URL).json()["origin"]
        assert [x["key"] for x in shown] == [key]
        ok = _review(client, "confirm", [key])
        assert ok.json()["changed"] == [key]
        rel = _rows(client, "relation")[key]
        assert rel["by"] == "origin" and rel["confirmed"] is True and rel["evidence"] == []
        assert client.get(_URL).json()["origin"] == []
        # 確認を取り消すと取り込み元の行に戻り、違うにすると消える
        _review(client, "unconfirm", [key])
        assert [x["key"] for x in client.get(_URL).json()["origin"]] == [key]
        _review(client, "reject", [key])
        assert client.get(_URL).json()["origin"] == []
        rejected = client.get(_URL, params={"state": "dropped"}).json()["origin"]
        assert [x["key"] for x in rejected] == [key] and rejected[0]["rejected"] is True
        _review(client, "restore", [key])
        assert [x["key"] for x in client.get(_URL).json()["origin"]] == [key]


def test_assign_候補に関係を付ける_落ちたら_422_と理由(tmp_path: Path) -> None:
    env, client = _ready(tmp_path)
    with client:
        none = _first(_rows(client), state="none")
        wrong = client.post(f"{_URL}/assign", json={"key": none["key"], "rel": "no-such", "subject": "a"})
        assert wrong.status_code == 422 and wrong.json()["reason"] == "relation"
        en = client.post(
            f"{_URL}/assign",
            json={"key": none["key"], "rel": "no-such", "subject": "a", "lang": "en"},
        )
        assert en.json()["detail"] != wrong.json()["detail"]
        missing = client.post(f"{_URL}/assign", json={"key": "no|such", "rel": "eq", "subject": "a"})
        assert missing.status_code == 404
        # 一致する関係が見つかる組に付ける（作り物の束: Mg2Si と Mg2Sn は同じもの）
        led = pr.load_ledger(env.root, "lab")
        pair = next(
            k for k, e in led["pairs"].items() if e["state"] == "relation" and e["rel"] == "eq"
        )
        _review(client, "reject", [pair])
        ok = client.post(f"{_URL}/assign", json={"key": pair, "rel": "eq", "subject": "a"})
        assert ok.status_code == 422 and ok.json()["reason"] == "rejected"
        _review(client, "restore", [pair])
        already = client.post(f"{_URL}/assign", json={"key": pair, "rel": "eq", "subject": "a"})
        assert already.status_code == 422 and already.json()["reason"] == "already_related"


def test_assign_成功すると確認済みの関係になる(tmp_path: Path) -> None:
    env, client = _ready(tmp_path)
    with client:
        led = pr.load_ledger(env.root, "lab")
        key = next(k for k, e in led["pairs"].items() if e["state"] == "relation" and e["rel"] == "eq")
        entry = led["pairs"][key]
        _review(client, "reject", [key])
        _review(client, "unconfirm", [key])
        # 「違う」とした 3 つ組は付けられない。別の関係は組の種別が合えば付けられる
        ok = client.post(f"{_URL}/assign", json={"key": key, "rel": "competes", "subject": "a"})
        assert ok.status_code == 200, ok.text
        assert ok.json()["changed"] == [key]
        row = _rows(client, "relation")[key]
        assert row["rel"] == "competes" and row["by"] == "user" and row["confirmed"] is True
        assert {row["from"]["id"], row["to"]["id"]} == {entry["a"], entry["b"]}


@pytest.mark.parametrize(
    "body",
    [
        {"action": "confirm", "keys": []},
        {"action": "confirm", "keys": [f"a{i}|b" for i in range(51)]},
        {"action": "all", "keys": ["x|y"]},
        {"action": "confirm"},
        {"action": "confirm", "keys": ["x|y"], "all": True},
    ],
)
def test_件数の上限と形(tmp_path: Path, body: dict) -> None:
    _, client = _ready(tmp_path)
    with client:
        assert client.post(f"{_URL}/review", json=body).status_code == 422


def test_50件までは通る(tmp_path: Path) -> None:
    _, client = _ready(tmp_path)
    with client:
        r = _review(client, "confirm", [f"a{i}|b" for i in range(50)])
        assert r.status_code == 200 and len(r.json()["skipped"]) == 50


def test_写しが無ければ404_認証が無ければ401(tmp_path: Path) -> None:
    env = _env(tmp_path)
    with TestClient(env.app, headers=_AUTH) as client:
        assert _review(client, "confirm", ["a|b"]).status_code == 404
        assert client.post(f"{_URL}/assign", json={"key": "a|b", "rel": "eq", "subject": "a"}).status_code == 404
    with TestClient(env.app) as anon:
        assert anon.post(f"{_URL}/review", json={"action": "confirm", "keys": ["a|b"]}).status_code == 401
        assert anon.post(f"{_URL}/assign", json={"key": "a|b", "rel": "eq", "subject": "a"}).status_code == 401


@pytest.mark.parametrize("kind", ["import", "remove", "copy", "relations"])
def test_同じ取り込み元の作業が走っている間は409(tmp_path: Path, kind: str) -> None:
    env, client = _ready(tmp_path)
    with client:
        row = _first(_rows(client), state="relation")
        state = env.app.state
        if kind in ("import", "remove"):
            state.knowledge_busy = {"lab": kind}
        elif kind == "copy":
            state.knowledge_copy_running = {"lab"}
        else:
            state.knowledge_relations_running = {"lab"}
        r = _review(client, "confirm", [row["key"]])
        a = client.post(f"{_URL}/assign", json={"key": row["key"], "rel": "eq", "subject": "a"})
        assert r.status_code == 409 and a.status_code == 409
        assert pr.load_ledger(env.root, "lab")["pairs"][row["key"]]["confirmed"] is None
        state.knowledge_busy = {}
        state.knowledge_copy_running = set()
        state.knowledge_relations_running = set()
        assert _review(client, "confirm", [row["key"]]).status_code == 200
        # 握りは必ず外れる（続けて関係選びも走らせられる）
        assert getattr(state, "knowledge_busy", {}) == {}


def test_台帳が読めなければ409(tmp_path: Path) -> None:
    env, client = _ready(tmp_path)
    with client:
        row = _first(_rows(client), state="relation")
        path = pr.ledger_path(env.root, "lab")
        path.chmod(0)
        try:
            r = _review(client, "confirm", [row["key"]])
        finally:
            path.chmod(0o600)
        assert r.status_code == 409


def test_状態_all_は全部の状態を返す(tmp_path: Path) -> None:
    _, client = _ready(tmp_path)
    with client:
        every = _rows(client)
        states = {r["state"] for r in every.values()}
        assert {"relation", "none"} <= states
        assert client.get(_URL, params={"state": "maybe"}).status_code == 422
