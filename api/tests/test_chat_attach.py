"""📎 でデータ — ui-redesign PR 11c（``POST /api/chat/sessions/{id}/attach`` ほか）。

ストアは本物のインメモリ ``rdflib.Dataset``。AI は注入した固定応答（ネットワークなし）で、
**本物の** staging → inspect → column-meanings → assemble → continue → materialize → source →
ingest（Morph-KGC）→ promote が通る。確かめること: 添えた表が 1 枚の返事になる（行数・1 行＝・
ID・列の意味・結ぶ列）／ID の候補が 2 つ以上なら止まって選ぶと続く／✓ ＝ 公開（``upper.json`` に
machine の線・promote・機械の手番・戻す）／``GET /api/sky`` の ``proposed_datasets``（鍵つきだけ）／
AI 未設定は 503／文書は ``unsupported``／走者が LLM を直接呼ばない（system prompt はかんたんの
既存のものだけ）。
"""

# ruff: noqa: RUF001
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import rdflib
from asterism import chat_sessions
from fastapi.testclient import TestClient

from asterism_api import chat_attach
from asterism_api.main import build_app
from tests.test_crosswalk_api import _AUTH, _DatasetClient, _settings

KEY = {"X-API-Key": "test-key"}

#: かんたんの既存の呼び出し（列の意味・設計の直し・問いの提案）の system prompt の目印
_MEANINGS = "what each COLUMN of a data file MEANS"
_REFINE = "same RDF / OWL / SPARQL ontology engineer"
_QUESTIONS = "questions"

PRICES = "食材,価格,店\nトマト,120,A\nきゅうり,80,B\nなす,100,C\n".encode()
TWO_NUMBERS = "id,code,価格\nA1,x1,120\nA2,x2,80\nA3,x3,100\n".encode()


class _Stub:
    """固定応答の AI。列の意味は「<列>の値」（列名そのままの名前は断られるので）。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append(system)
        if _MEANINGS in system:
            source = re.findall(r"^## (?:CSV: )?(\S+)", user, re.M)[-1]
            cols = re.findall(r"^\| `([^`]+)` \|", user, re.M)
            return json.dumps(
                {"columns": [{"source": source, "column": c, "label": f"{c}の値"} for c in cols]},
                ensure_ascii=False,
            )
        if _QUESTIONS in system.lower():
            return json.dumps({"questions": []})
        raise AssertionError(f"unexpected LLM call: {system[:80]}")


def _app(tmp_path: Path, llm: Any = None):
    ds = rdflib.Dataset()
    app = build_app(
        _settings(tmp_path),
        oxigraph_client=_DatasetClient(ds),
        start_watcher=False,
        llm_factory=(lambda key: llm) if llm is not None else None,
    )
    return app, ds


def _new(client: TestClient) -> str:
    r = client.post("/api/chat/sessions", json={})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _mint(client: TestClient, slug: str, label: str, kind: str) -> None:
    cq = (
        {"title": f"{label}は、どのデータセットに何件あるか", "op": "count"}
        if kind == "class"
        else {"title": f"{label}の値は、どのデータセットにいくつあるか", "op": "values"}
    )
    r = client.post(
        "/api/vocab/shared", json={"slug": slug, "kind": kind, "label_ja": label, "cqs": [cq]}
    )
    assert r.status_code == 201, r.text


def _attach(client: TestClient, sid: str, name: str, data: bytes, **kw: Any):
    return client.post(
        f"/api/chat/sessions/{sid}/attach",
        files=[("files", (name, data, "text/csv"))],
        data={"lang": "ja"},
        headers=kw.get("headers", KEY),
    )


def _drain(client: TestClient, job_id: str) -> list[dict]:
    """ジョブの SSE を最後まで読む（終わるまで待つ）。"""
    events: list[dict] = []
    for line in client.get(f"/api/jobs/{job_id}/stream").text.splitlines():
        if line.startswith("data:"):
            events.append(json.loads(line[5:]))
    return events


def _turn(client: TestClient, sid: str, turn_id: str) -> dict:
    session = client.get(f"/api/chat/sessions/{sid}").json()
    return next(t for t in session["turns"] if t["id"] == turn_id)


def _run(client: TestClient, sid: str, name: str, data: bytes) -> dict:
    r = _attach(client, sid, name, data)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["turn"]["state"] == "running" and body["job_id"]
    _drain(client, body["job_id"])
    return _turn(client, sid, body["turn"]["id"])


def test_an_attached_table_becomes_one_reply(tmp_path: Path) -> None:
    llm = _Stub()
    app, _ = _app(tmp_path, llm)
    with TestClient(app, headers=_AUTH) as client:
        _mint(client, "price", "価格の値", "property")  # 列の意味と完全一致する項目
        sid = _new(client)
        r = _attach(client, sid, "価格表.csv", PRICES)
        assert r.status_code == 201, r.text
        first = r.json()
        # 人の手番: 文は空・添付の名前と大きさ・走っている間は running
        assert first["turn"]["user"] == {
            "text": "",
            "attachments": [{"name": "価格表.csv", "size": len(PRICES)}],
        }
        assert first["turn"]["state"] == "running" and first["job_id"]
        events = _drain(client, first["job_id"])
        stages = [e["stage"] for e in events if "stage" in e]
        assert stages[:3] == [1, 2, 3] and 7 in stages  # 段ごとの 1 行が流れる
        assert events[-1]["result"]["state"] == "proposed"

        turn = _turn(client, sid, first["turn"]["id"])
        assert turn["state"] == "proposed" and turn["by"] == "machine"
        att = turn["attachment"]
        assert att["rows"] == 3 and att["name"] == "価格表" and att["dataset_id"]
        assert att["kinds"] and att["kinds"][0]["count"] == 3
        assert att["id_column"]
        text = turn["reply"]["text"]
        assert "表「価格表」を読みました（3 行・1 行＝" in text
        assert "「公開」を押すと公開します" in text
        assert "価格の値（価格）" in text  # 列の意味
        # 結ぶ列: 完全一致した「価格」だけ・既定は結ぶ
        (b,) = turn["reply"]["bindings"]
        assert (b["column"], b["term_label"], b["relation"], b["decided"]) == (
            "価格",
            "価格の値",
            "eq",
            "bind",
        )
        assert b["subject"].startswith("property:")
        assert turn["reply"]["sentences"] == ["列「価格」は「価格の値」と同じもの"]
        # 言ってはいけない言葉
        for word in ("そう", "線", "ことば", "下書き", "置く"):
            assert word not in text and not any(word in s for s in turn["reply"]["sentences"])
        # 走者が AI を直接呼ばない: 呼ばれたのはかんたんの既存の呼び出しだけ
        assert llm.calls and all(_MEANINGS in c for c in llm.calls)


def test_two_id_candidates_stop_and_a_choice_continues(tmp_path: Path, monkeypatch) -> None:
    orig = chat_attach.column_rows

    def with_preamble(res):
        return [
            {**r, "origin": "preamble"} if r["column"] in ("id", "code") else r for r in orig(res)
        ]

    monkeypatch.setattr(chat_attach, "column_rows", with_preamble)
    llm = _Stub()
    app, _ = _app(tmp_path, llm)
    with TestClient(app, headers=_AUTH) as client:
        sid = _new(client)
        turn = _run(client, sid, "番号表.csv", TWO_NUMBERS)
        # 止まる: データセットはまだ無く、選択が 1 つ
        assert turn["state"] == "proposed" and not turn["attachment"]
        (note,) = turn["reply"]["notes"]
        assert note["kind"] == "choice_id"
        assert [o["value"] for o in note["choice"]["options"]] == ["id", "code"]
        assert note["choice"]["default"] == "id" and note["choice"]["label"]
        assert [c for c in llm.calls if _MEANINGS not in c] == []  # 設計はまだ
        # 選ぶ前の ✓ は断る
        r = client.post(f"/api/chat/sessions/{sid}/turns/{turn['id']}/confirm", json={})
        assert r.status_code == 409
        # 選ぶ → 同じ手番が running になり、④ から続く
        r = client.post(
            f"/api/chat/sessions/{sid}/turns",
            json={"choice": {"turn_id": turn["id"], "note_index": 0, "value": "code"}},
            headers=KEY,
        )
        assert r.status_code == 201, r.text
        assert r.json()["turn"]["state"] == "running" and r.json()["job_id"]
        _drain(client, r.json()["job_id"])
        done = _turn(client, sid, turn["id"])
        assert done["state"] == "proposed" and done["attachment"]["rows"] == 3
        assert done["attachment"]["id_column"] == "code"
        # 選んだ値は手番の記録に残る
        session = client.get(f"/api/chat/sessions/{sid}").json()
        assert len(session["turns"]) == 1


def test_choice_is_refused_for_a_value_that_is_not_an_option(tmp_path: Path, monkeypatch) -> None:
    orig = chat_attach.column_rows
    monkeypatch.setattr(
        chat_attach,
        "column_rows",
        lambda res: [
            {**r, "origin": "preamble"} if r["column"] in ("id", "code") else r for r in orig(res)
        ],
    )
    app, _ = _app(tmp_path, _Stub())
    with TestClient(app, headers=_AUTH) as client:
        sid = _new(client)
        turn = _run(client, sid, "番号表.csv", TWO_NUMBERS)
        r = client.post(
            f"/api/chat/sessions/{sid}/turns",
            json={"choice": {"turn_id": turn["id"], "note_index": 0, "value": "価格"}},
            headers=KEY,
        )
        assert r.status_code == 422


def test_id_candidates_follow_the_wizard_rule() -> None:
    def row(column: str, origin: str, examples: list[str]) -> dict:
        return {"source": "f.csv", "column": column, "origin": origin, "examples": examples}

    rows = [
        row("no", "preamble", ["03-065-2664"]),
        row("temp", "preamble", ["300.5"]),  # 測定値は番号にならない
        row("sample", "preamble", ["12"]),  # 小数点も指数も無い数 — 番号になれる
        row("price", "table", ["1", "2"]),  # 表の列は番号の候補ではない
    ]
    assert chat_attach.id_candidates(rows, "f.csv") == ["no", "sample"]
    assert chat_attach.id_candidates(rows, "other.csv") == []
    assert chat_attach.is_measurement(["1", "2.5"]) and chat_attach.is_measurement(["1e3"])
    assert not chat_attach.is_measurement(["1", "2"])
    assert not chat_attach.is_measurement(["a", "1.5"])


def test_confirm_publishes_binds_the_names_and_offers_what_matched(tmp_path: Path) -> None:
    llm = _Stub()
    app, _ = _app(tmp_path, llm)
    with TestClient(app, headers=_AUTH) as client:
        _mint(client, "price", "価格の値", "property")
        _mint(client, "row", "価格表 の 1 行", "class")  # 公開のあと、種類の名前が一致する
        sid = _new(client)
        turn = _run(client, sid, "価格表.csv", PRICES)
        dsid = turn["attachment"]["dataset_id"]
        root = tmp_path / "registry"
        assert not (json.loads((root / dsid / "meta.json").read_text()).get("promoted"))
        r = client.post(
            f"/api/chat/sessions/{sid}/turns/{turn['id']}/confirm",
            json={"bindings": [{"column": "価格", "decided": "bind"}]},
            headers=KEY,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["turn"]["state"] == "confirmed" and body["confirmed"] == [turn["id"]]
        assert body["commit"]["dataset_id"] == dsid and body["commit"]["published_at"]
        assert json.loads((root / dsid / "meta.json").read_text())["promoted"] is True
        # upper.json に machine の線
        upper = json.loads((root / dsid / "upper.json").read_text())
        (line,) = upper["upper"]
        assert line["drafted_by"] == "machine" and line["reason"] == "名前の完全一致"
        assert line["relation"] == "equivalentProperty" and line["subject"].startswith("property:")
        assert line["batch"] == f"{sid}:{turn['id']}"
        # 公開のあと、名前が一致した関係が機械の手番に
        follow = body["followup"]
        assert follow and follow["by"] == "machine" and follow["user"] is None
        assert follow["state"] == "proposed" and follow["reply"]["suggestions"]
        assert follow["reply"]["text"].startswith("名前が一致した関係が")
        assert all("同じもの" in s for s in follow["reply"]["sentences"])
        assert {"subject", "term", "relation"} == set(follow["reply"]["suggestions"][0])
        # 機械の手番の ✓ ＝ 線になる
        r = client.post(f"/api/chat/sessions/{sid}/turns/{follow['id']}/confirm", json={})
        assert r.status_code == 200, r.text
        assert r.json()["turn"]["state"] == "confirmed" and r.json()["commit"]["applied"] >= 1
        assert client.get("/api/sky/suggestions").json()["suggestions"] == []
        # すでに ✓ した手番は、もう ✓ できない
        again = client.post(f"/api/chat/sessions/{sid}/turns/{turn['id']}/confirm", json={})
        assert again.status_code == 409


def test_a_skipped_binding_is_not_written_and_no_followup_means_null(tmp_path: Path) -> None:
    app, _ = _app(tmp_path, _Stub())
    with TestClient(app, headers=_AUTH) as client:
        _mint(client, "price", "価格の値", "property")
        sid = _new(client)
        turn = _run(client, sid, "価格表.csv", PRICES)
        dsid = turn["attachment"]["dataset_id"]
        r = client.post(
            f"/api/chat/sessions/{sid}/turns/{turn['id']}/confirm",
            json={"bindings": [{"column": "価格", "decided": "skip"}]},
            headers=KEY,
        )
        assert r.status_code == 200, r.text
        assert r.json()["followup"] is None
        upper_path = tmp_path / "registry" / dsid / "upper.json"
        written = upper_path.read_text().strip() if upper_path.exists() else ""
        assert not written or not json.loads(written).get("upper")
        # 結ばなかった決定は手番の記録に残る
        assert r.json()["turn"]["reply"]["bindings"][0]["decided"] == "skip"


def test_reverting_a_machine_turn_dismisses_the_suggestion(tmp_path: Path) -> None:
    app, _ = _app(tmp_path, _Stub())
    with TestClient(app, headers=_AUTH) as client:
        _mint(client, "row", "価格表 の 1 行", "class")
        sid = _new(client)
        turn = _run(client, sid, "価格表.csv", PRICES)
        body = client.post(f"/api/chat/sessions/{sid}/turns/{turn['id']}/confirm", json={}).json()
        follow = body["followup"]
        assert follow
        r = client.post(f"/api/chat/sessions/{sid}/turns/{follow['id']}/revert")
        assert r.status_code == 200, r.text
        assert r.json()["turn"]["state"] == "reverted"
        assert client.get("/api/sky/suggestions").json()["suggestions"] == []


def test_sky_lists_the_unpublished_table_for_a_key_only(tmp_path: Path) -> None:
    app, _ = _app(tmp_path, _Stub())
    with TestClient(app, headers=_AUTH) as client:
        sid = _new(client)
        turn = _run(client, sid, "価格表.csv", PRICES)
        dsid = turn["attachment"]["dataset_id"]
        sky = client.get("/api/sky").json()
        (row,) = sky["proposed_datasets"]
        assert row["id"] == dsid and row["label"] == "価格表" and row["session_id"] == sid
        assert row["kinds"] == turn["attachment"]["kinds"]
        assert all(d["id"] != dsid for d in sky["datasets"])  # 公開済みの欄には出ない
        # 鍵が無ければ欄ごと無い
        with TestClient(app) as anon:
            assert "proposed_datasets" not in anon.get("/api/sky").json()
        # 公開すると proposed_datasets から datasets に移る
        client.post(f"/api/chat/sessions/{sid}/turns/{turn['id']}/confirm", json={}, headers=KEY)
        after = client.get("/api/sky").json()
        assert after["proposed_datasets"] == []
        assert any(d["id"] == dsid for d in after["datasets"])


def test_attach_without_an_ai_is_503_and_the_chat_is_untouched(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)  # llm_factory なし
    with TestClient(app, headers=_AUTH) as client:
        sid = _new(client)
        r = client.post(
            f"/api/chat/sessions/{sid}/attach",
            files=[("files", ("価格表.csv", PRICES, "text/csv"))],
        )
        assert r.status_code == 503 and r.json()["detail"] == "AI が設定されていません"
        assert client.get(f"/api/chat/sessions/{sid}").json()["turns"] == []


def test_a_document_is_unsupported_without_running_anything(tmp_path: Path) -> None:
    llm = _Stub()
    app, _ = _app(tmp_path, llm)
    with TestClient(app, headers=_AUTH) as client:
        sid = _new(client)
        r = client.post(
            f"/api/chat/sessions/{sid}/attach",
            files=[("files", ("論文.pdf", b"%PDF-1.4", "application/pdf"))],
            headers=KEY,
        )
        assert r.status_code == 201, r.text
        turn = r.json()["turn"]
        assert turn["state"] == "none" and r.json()["job_id"] is None
        (note,) = turn["reply"]["notes"]
        assert note["kind"] == "unsupported"
        assert note["text"] == "文書はまだ添えられません（表を添えてください）"
        assert turn["user"]["attachments"][0]["name"] == "論文.pdf"
        assert llm.calls == []


def test_attach_needs_the_token_and_a_file(tmp_path: Path) -> None:
    app, _ = _app(tmp_path, _Stub())
    with TestClient(app, headers=_AUTH) as client:
        sid = _new(client)
        assert client.post(f"/api/chat/sessions/{sid}/attach", headers=KEY).status_code == 422
        assert (
            client.post(
                "/api/chat/sessions/cabcdefghij/attach",
                files=[("files", ("a.csv", PRICES, "text/csv"))],
                headers=KEY,
            ).status_code
            == 404
        )
    with TestClient(app) as anon:
        r = anon.post(
            f"/api/chat/sessions/{sid}/attach", files=[("files", ("a.csv", PRICES, "text/csv"))]
        )
        assert r.status_code == 401


def test_a_file_the_reader_cannot_use_ends_as_attach_failed(tmp_path: Path) -> None:
    app, _ = _app(tmp_path, _Stub())
    with TestClient(app, headers=_AUTH) as client:
        sid = _new(client)
        turn = _run(client, sid, "壊れた.xlsx", b"not a workbook")
        assert turn["state"] == "none" and not turn.get("attachment")
        (note,) = turn["reply"]["notes"]
        assert note["kind"] == "attach_failed" and "もう一度" in note["text"]
        assert "job_id" not in turn


def test_the_theme_confirm_and_undo_ignore_table_turns(tmp_path: Path) -> None:
    """未確認の表の返事があっても、テーマの ✓ と「戻して」はテーマの返事だけを数える。"""
    app, _ = _app(tmp_path, _Stub())
    with TestClient(app, headers=_AUTH) as client:
        sid = _new(client)
        turn = _run(client, sid, "価格表.csv", PRICES)
        r = client.post(
            f"/api/chat/sessions/{sid}/turns", json={"text": "さっきのを戻して"}, headers=KEY
        )
        assert r.status_code == 201, r.text
        assert "戻すものがありません" in r.json()["turn"]["reply"]["text"]
        assert _turn(client, sid, turn["id"])["state"] == "proposed"
        session = chat_sessions.load_session(tmp_path / "registry", sid)
        assert session is not None


def test_the_row_kind_is_the_designs_name_and_the_id_says_when_it_is_provisional(
    tmp_path: Path,
) -> None:
    app, _ = _app(tmp_path, _Stub())
    with TestClient(app, headers=_AUTH) as client:
        sid = _new(client)
        turn = _run(client, sid, "価格表.csv", PRICES)
        text = turn["reply"]["text"]
        assert "1 行＝価格表 の 1 行）" in text and "Record" not in text
        att = turn["attachment"]
        assert att["kinds"][0]["label"] == "価格表 の 1 行"
        # 行の種類は件数が最大の 1 つだけ
        assert "、" not in text.split("1 行＝")[1].split("）")[0]
        if "機械の仮置き" in text:
            assert any(
                n["kind"] == "shape" and "機械が仮に" in n["text"] for n in turn["reply"]["notes"]
            )
        else:
            assert not any("機械が仮に" in n["text"] for n in turn["reply"]["notes"])


def test_an_ai_failure_is_told_apart_from_a_reading_failure(tmp_path: Path) -> None:
    class _Down:
        def complete(self, system: str, user: str) -> str:
            raise RuntimeError("AI down")

    app, _ = _app(tmp_path, _Down())
    with TestClient(app, headers=_AUTH) as client:
        sid = _new(client)
        turn = _run(client, sid, "価格表.csv", PRICES)
        assert turn["state"] == "none" and not turn.get("attachment")
        (note,) = turn["reply"]["notes"]
        assert note["kind"] == "ai_failed" and "設定 › AI" in note["text"]
        assert "job_id" not in turn


def test_a_running_turn_without_a_job_is_closed_when_read(tmp_path: Path) -> None:
    app, _ = _app(tmp_path, _Stub())
    with TestClient(app, headers=_AUTH) as client:
        sid = _new(client)
        session = chat_sessions.load_session(tmp_path / "registry", sid)
        from asterism_api import chat_turns

        turn = chat_turns._append_turn(
            session,
            at="2026-01-01T00:00:00+00:00",
            user={"text": "", "attachments": [{"name": "a.csv", "size": 1}]},
            reply=chat_turns._reply(""),
            state="running",
            before=None,
            by="machine",
        )
        turn["job_id"] = "gone-job"
        chat_sessions.save_session(tmp_path / "registry", session)
        got = client.get(f"/api/chat/sessions/{sid}").json()
        t = got["turns"][0]
        assert t["state"] == "none" and t["job_id"] is None
        (note,) = t["reply"]["notes"]
        assert note["kind"] == "attach_failed" and "途中で止まりました" in note["text"]
        saved = chat_sessions.load_session(tmp_path / "registry", sid)
        assert saved["turns"][0]["state"] == "none"  # 保存もされている


def test_reverting_or_deleting_removes_the_unpublished_dataset(tmp_path: Path) -> None:
    app, _ = _app(tmp_path, _Stub())
    with TestClient(app, headers=_AUTH) as client:
        root = tmp_path / "registry"
        sid = _new(client)
        turn = _run(client, sid, "価格表.csv", PRICES)
        dsid = turn["attachment"]["dataset_id"]
        assert (root / dsid / "meta.json").exists()
        r = client.post(f"/api/chat/sessions/{sid}/turns/{turn['id']}/revert")
        assert r.status_code == 200 and r.json()["turn"]["state"] == "reverted"
        assert not (root / dsid / "meta.json").exists()
        # 話ごと消すときも
        sid2 = _new(client)
        turn2 = _run(client, sid2, "価格表.csv", PRICES)
        dsid2 = turn2["attachment"]["dataset_id"]
        assert (root / dsid2 / "meta.json").exists()
        assert client.delete(f"/api/chat/sessions/{sid2}").status_code == 204
        assert not (root / dsid2 / "meta.json").exists()


def test_attach_to_a_missing_session_is_404(tmp_path: Path) -> None:
    app, _ = _app(tmp_path, _Stub())
    with TestClient(app, headers=_AUTH) as client:
        r = _attach(client, "no-such-session", "価格表.csv", PRICES)
        assert r.status_code == 404


def test_provisional_id_is_only_the_machines_placement() -> None:
    f = chat_attach._provisional_ids
    assert f("No", {"cards.csv": "No"}, {}) is True  # 機械の仮置き
    assert f("No", {"cards.csv": "No"}, {"cards.csv": "No"}) is False  # 人が選んだ
    assert f("食材", {}, {}) is False
    text = chat_attach.text("ja", "id_provisional", id="No")
    assert text == "No（機械の仮置き）"
