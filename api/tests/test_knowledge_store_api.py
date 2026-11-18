# ruff: noqa: E501
"""確認済みをストアの正本に置く口（PR 14e-1b・契約書 §3）。中身の検査は ingest 側
（``test_confirmed_store.py``）。ここは口の形・排他・失敗しても review が通ること・行の欄。

api の偽ストア（``_RecOxi``）は状態を持たないので、置く段（``asterism.confirmed_store.sync``）は
差し替えて、呼び方（鍵・排他・保存）を見る。例は作り物。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from asterism import confirmed_store as cs
from asterism import pair_relations as pr
from fastapi.testclient import TestClient

from asterism_api import knowledge_import as ki
from asterism_api import knowledge_store as ks

from .test_ingest import _AUTH
from .test_knowledge_import import _go
from .test_knowledge_relations import _Env, _env
from .test_knowledge_review import _first, _ready, _review, _rows

pytest.importorskip("morph_kgc", reason="the copy runs the real declarative ingest")

_URL = "/api/knowledge-copies/lab/relations"


def _confirm_one(client: TestClient) -> tuple[dict, Any]:
    row = _first(_rows(client), state="relation", rel="makes")
    return row, _review(client, "confirm", [row["key"]])


def test_review_returns_store_and_syncs_in_the_lock_with_the_changed_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env, client = _ready(tmp_path)
    seen: dict[str, Any] = {}

    async def fake_sync(client_, root, ledger, copy, **kw):
        seen["focus"] = list(kw["focus"])
        seen["locked"] = env.app.state.themes_lock.locked()  # 話の確定と同じ排他
        seen["claim_graph"] = kw["claim_graph"]
        led = pr.load_ledger(root, "lab")
        rep = cs.Report(
            placed=[seen["focus"][0]], removed=["x"],
            failed=[{"key": seen["focus"][0], "reason": "cycle"}, {"key": "other", "reason": "error"}],
        )  # fmt: skip
        for k in seen["focus"]:
            led["pairs"][k]["store"] = {"ok": True, "at": "2026-11-18T00:00:00Z", "of": "o"}
        led["stored_terms"] = {"t-aaaaaaaaaaaa": {"slug": "kt_aaaaaaaaaaaa", "minted": True}}
        seen["locked_at_save"] = env.app.state.themes_lock.locked()
        await kw["save"](led)  # 台帳の保存も排他の中（放すのは保存のあと）
        return led, rep

    monkeypatch.setattr(cs, "sync", fake_sync)
    with client:
        row, r = _confirm_one(client)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["changed"] == [row["key"]]
        # failed はその操作で指した件だけ。ほかの件は数（backlog_failed）だけ
        assert body["store"] == {
            "placed": 1, "removed": 1, "failed": [{"key": row["key"], "reason": "cycle"}],
            "backlog_failed": 1, "remaining": 0, "attempted": 0,
        }  # fmt: skip
        assert seen["focus"] == [row["key"]] and seen["locked"] is True
        assert seen["locked_at_save"] is True
        assert seen["claim_graph"]  # 公開中の写しの graph
        # 台帳に写った結果が保存されている（保存は 1 回きりでなくてよい）
        led = pr.load_ledger(env.root, "lab")
        assert led["pairs"][row["key"]]["confirmed"]
        assert led["pairs"][row["key"]]["store"]["ok"] is True
        assert "kt_aaaaaaaaaaaa" in str(led["stored_terms"])
        assert not env.app.state.themes_lock.locked()


def test_a_failing_store_step_does_not_fail_the_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env, client = _ready(tmp_path)

    async def boom(*a, **k):
        raise RuntimeError("store is down")

    monkeypatch.setattr(cs, "sync", boom)
    with client:
        row, r = _confirm_one(client)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["changed"] == [row["key"]]
        assert body["store"]["error"] is True and body["store"]["failed"] == []
        # 台帳の確認は通っている・排他は解けている
        assert _rows(client)[row["key"]]["confirmed"] is True
        assert pr.load_ledger(env.root, "lab")["pairs"][row["key"]]["confirmed"]
        assert not env.app.state.themes_lock.locked()
        assert _review(client, "unconfirm", [row["key"]]).status_code == 200


def test_the_real_sync_against_a_stateless_store_leaves_the_confirmation(tmp_path: Path) -> None:
    """偽ストアは語を返さないので置けない（理由は記号）。それでも review は通り、確認は残る。"""
    _, client = _ready(tmp_path)
    with client:
        row, r = _confirm_one(client)
        assert r.status_code == 200, r.text
        store = r.json()["store"]
        assert set(store) >= {"placed", "removed", "failed"}
        assert all(f["reason"] in cs.REASONS for f in store["failed"])
        assert _rows(client)[row["key"]]["confirmed"] is True


def test_rows_carry_term_iri_and_store_only_when_there_is_one(tmp_path: Path) -> None:
    env, client = _ready(tmp_path)
    with client:
        rows = _rows(client)
        row = _first(rows, state="relation", rel="makes")
        assert "store" not in row and "term_iri" not in row["from"]  # 既存の形のまま
        led = pr.load_ledger(env.root, "lab")
        e = led["pairs"][row["key"]]
        led["stored_terms"] = {
            row["from"]["id"]: {"slug": "kt_aaaaaaaaaaaa", "minted": True},
            row["to"]["id"]: {"slug": "pore", "minted": False},
        }
        e["confirmed"] = {"rel": e["rel"], "from": e["from"], "to": e["to"], "at": "2026-11-18T00:00:00Z",
                          "by": "user", "evidence": []}  # fmt: skip
        e["store"] = {"ok": False, "reason": "role_mismatch", "at": "2026-11-18T00:00:00Z", "of": "x"}
        pr.save_ledger(env.root, led, at="2026-11-18T00:00:00Z")
        got = _rows(client)[row["key"]]
        assert got["from"]["term_iri"].endswith("kt_aaaaaaaaaaaa")
        assert got["to"]["term_iri"].endswith("pore")
        assert got["store"] == {"ok": False, "reason": "role_mismatch"}
        e["store"] = {"ok": True, "at": "2026-11-18T00:00:00Z", "of": "x", "line": "a|b|c"}
        pr.save_ledger(env.root, led, at="2026-11-18T00:00:00Z")
        assert _rows(client)[row["key"]]["store"] == {"ok": True}
        # 確認が外れた件には store を出さない
        e["confirmed"] = None
        pr.save_ledger(env.root, led, at="2026-11-18T00:00:00Z")
        assert "store" not in _rows(client)[row["key"]]


def test_pair_keys_map_origin_row_keys_to_pair_keys() -> None:
    assert ks.pair_keys(["o:t-2|part|t-1", "t-1|t-2", "t-3|t-4"]) == ["t-1|t-2", "t-3|t-4"]


def test_the_import_job_catches_the_store_up_at_the_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str]] = []

    async def fake_catch_up(app, root, source, dataset_id):
        calls.append((source, dataset_id))
        return {"placed": 0, "removed": 0, "failed": []}

    monkeypatch.setattr(ki, "catch_up", fake_catch_up)
    env: _Env = _env(tmp_path)
    with TestClient(env.app, headers=_AUTH) as client:
        out = _go(client)
    assert calls == [("lab", out["dataset_id"])]


def test_catch_up_never_raises_and_reports_an_error(tmp_path: Path) -> None:
    env = _env(tmp_path)
    with TestClient(env.app, headers=_AUTH) as client:
        out = _go(client)
        env.app.state.client = None  # ストアが無い
        import asyncio

        got = asyncio.run(ks.catch_up(env.app, env.root, "lab", out["dataset_id"]))
        assert got["placed"] == 0 and got["failed"] == []
        got = asyncio.run(ks.catch_up(env.app, env.root, "lab", "no-such-dataset"))
        assert got["placed"] == 0


def test_two_sources_never_interleave_inside_the_lock_and_the_save_is_inside(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A が置いてから台帳を保存するまでの間に、別の取り込み元の同期を挟ませない（挟むと、置いた
    ばかりの線を外される）。順番を固定して確かめる: A 開始 → A 保存 → A 終了 → B 開始。"""
    import asyncio

    env = _env(tmp_path)
    events: list[str] = []

    async def fake_sync(client_, root, ledger, copy, **kw):
        name = ledger["source"]
        events.append(f"{name}:start")
        await asyncio.sleep(0.05 if name == "a" else 0)  # A が遅い間に B が来る
        await kw["save"]({**ledger, "marker": name})
        events.append(f"{name}:saved")
        await asyncio.sleep(0)
        events.append(f"{name}:end")
        return {**ledger, "marker": name}, cs.Report()

    with TestClient(env.app, headers=_AUTH) as client:
        out = _go(client)  # 写しを作って、メタ（live_graph）を用意する
        monkeypatch.setattr(cs, "sync", fake_sync)
        copy = ks.kb.KnowledgeCopy()
        ds = out["dataset_id"]
        led_a, led_b = pr.empty_ledger("a"), pr.empty_ledger("b")

        async def both() -> None:
            env.app.state.themes_lock = asyncio.Lock()  # このループの鍵
            await asyncio.gather(
                ks.sync_ledger(env.app, env.root, led_a, copy, ds),
                ks.sync_ledger(env.app, env.root, led_b, copy, ds),
            )

        asyncio.run(both())
    assert events == ["a:start", "a:saved", "a:end", "b:start", "b:saved", "b:end"]
