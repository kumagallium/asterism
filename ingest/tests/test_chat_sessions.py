"""話の保存（chat_sessions）— 作る→一覧→読む→消す・壊れた JSON・下書きから話・原子的な書き込み。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from asterism import chat_sessions as cs
from asterism import shared_vocab as sv


def test_id_shape() -> None:
    for _ in range(20):
        assert cs.ID_RE.match(cs.new_session_id())


def test_create_list_load_delete(tmp_path: Path) -> None:
    assert cs.list_sessions(tmp_path) == []
    a = cs.save_session(tmp_path, cs.new_session("熱電材料"))
    b = cs.new_session("")
    b["turns"] = [
        {"id": "t1", "state": "proposed"},
        {"id": "t2", "state": "none"},
        {"id": "t3", "state": "proposed"},
    ]
    b = cs.save_session(tmp_path, b)
    rows = cs.list_sessions(tmp_path)
    assert [r["id"] for r in rows] == [b["id"], a["id"]]  # 新しい順
    assert rows[0]["turns"] == 3 and rows[0]["proposed"] == 2
    assert rows[1]["title"] == "熱電材料" and rows[1]["proposed"] == 0
    got = cs.load_session(tmp_path, a["id"])
    assert got is not None and got["title"] == "熱電材料" and got["version"] == 1
    assert cs.delete_session(tmp_path, a["id"]) is True
    assert cs.delete_session(tmp_path, a["id"]) is False
    assert cs.load_session(tmp_path, a["id"]) is None
    assert [r["id"] for r in cs.list_sessions(tmp_path)] == [b["id"]]


def test_save_advances_updated_at(tmp_path: Path) -> None:
    s = cs.new_session("x", at="2000-01-01T00:00:00+00:00")
    saved = cs.save_session(tmp_path, s)
    assert saved["updated_at"] > s["updated_at"]
    assert saved["created_at"] == s["created_at"]


def test_broken_and_foreign_version_are_none(tmp_path: Path) -> None:
    s = cs.save_session(tmp_path, cs.new_session("ok"))
    d = sv._dir(tmp_path) / "chat"
    (d / "cbroken0000.json").write_text("{not json", encoding="utf-8")
    (d / "cold0000000.json").write_text(
        '{"version": 0, "id": "cold0000000", "turns": []}', encoding="utf-8"
    )
    (d / "cwrongid000.json").write_text(
        '{"version": 1, "id": "cother00000", "turns": []}', encoding="utf-8"
    )
    for name in ("cbroken0000", "cold0000000", "cwrongid000", "nope", "../x"):
        assert cs.load_session(tmp_path, name) is None
    assert [r["id"] for r in cs.list_sessions(tmp_path)] == [s["id"]]


def test_session_of_draft_and_turn_ids(tmp_path: Path) -> None:
    s = cs.new_session("x")
    s["draft_id"] = "draft-a"
    s["turns"] = [{"id": "t1"}, {"id": "t4"}]
    cs.save_session(tmp_path, s)
    cs.save_session(tmp_path, cs.new_session("y"))
    assert cs.session_of_draft(tmp_path, "draft-a") == s["id"]
    assert cs.session_of_draft(tmp_path, "draft-zzz") is None
    assert cs.new_turn_id(s) == "t5"
    assert cs.new_turn_id({"turns": []}) == "t1"


def test_write_is_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = cs.save_session(tmp_path, cs.new_session("前の名前"))

    def boom(src: object, dst: object) -> None:
        raise OSError("disk gone")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        cs.save_session(tmp_path, {**s, "title": "新しい名前"})
    monkeypatch.undo()
    assert cs.load_session(tmp_path, s["id"])["title"] == "前の名前"


def test_draft_session_map_reads_once_and_id_re_rejects_trailing_newline(tmp_path: Path) -> None:
    a = cs.new_session("")
    a["draft_id"] = "d-one"
    a = cs.save_session(tmp_path, a)
    b = cs.save_session(tmp_path, cs.new_session(""))
    assert cs.draft_session_map(tmp_path) == {"d-one": a["id"]}
    assert cs.session_of_draft(tmp_path, "d-one") == a["id"]
    assert cs.session_of_draft(tmp_path, "nope") is None
    assert b["id"] not in cs.draft_session_map(tmp_path).values()
    assert not cs.ID_RE.match(a["id"] + "\n")
    assert cs.load_session(tmp_path, a["id"] + "\n") is None


def test_turn_of_batch_and_turn_of_minted(tmp_path: Path) -> None:
    a = cs.new_session("熱電材料")
    a["turns"] = [
        {"id": "t1", "state": "confirmed", "commit": {"minted": ["lattice"], "theme_id": "th1",
                                                      "at": "2026-10-22T00:00:00+00:00"}},
        {"id": "t2", "state": "reverted", "commit": {"minted": ["gone"], "theme_id": "th1"}},
    ]  # fmt: skip
    a = cs.save_session(tmp_path, a)
    assert cs.turn_of_batch(tmp_path, f"{a['id']}:t1") == {"session_id": a["id"], "turn_id": "t1"}
    for bad in (None, "", "no-colon", f"{a['id']}:t9", "cnosuchone0:t1", "../x:t1"):
        assert cs.turn_of_batch(tmp_path, bad) is None
    assert cs.turn_of_minted(tmp_path, "lattice") == {
        "session_id": a["id"],
        "turn_id": "t1",
        "theme_id": "th1",
        "at": "2026-10-22T00:00:00+00:00",
    }
    assert cs.turn_of_minted(tmp_path, "gone") is None  # 戻した手番は引かない
    assert cs.turn_of_minted(tmp_path, "unknown") is None
    assert {"ask_unavailable", "ask_failed"} <= cs.NOTE_KINDS
