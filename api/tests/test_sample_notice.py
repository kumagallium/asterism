"""見本の入れ直し PR3 = 画面の知らせと、控えつきの手動の置き換え（ADR kantan K62）。

契約メモ contract_sample_refresh_pr3_notice.md のテスト節に対応する。

* 印の新しい欄（``last_update``・``backups``）と、保留に生の識別子が入らないこと
* 手動の置き換え（``manual_override``）: 控え・置き換え・履歴を作らない・5 件まで
* 控えから戻す（``manual_restore``）
* 各 409（stale・not_overridable・取り込み途中・予約の残り・取り下げ・見本でない）
* HTTP ルート: 本文・見出しの要求・busy・単一ユーザーでなければ 404
* アクティビティ（jobs）の記録は、中身が変わったときだけ
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest
import rdflib
from asterism import dataset_summary
from fastapi.testclient import TestClient

from asterism_api import demo_sample, local
from asterism_api import main as main_mod
from asterism_api.sample_routes import INTENT_HEADER, INTENT_REFRESH, INTENT_RESTORE
from tests.test_demo_sample import (
    _HOLD_CASES,
    DATASET_ID,
    SNAPSHOT,
    _cfg,
    _DatasetClient,
    _files_of,
    _meta,
    _nquads,
    _old_store,
    _Projections,
    _read_meta,
    _real,
    _refresh,
    _refresh_release,
    _release_file,
    _write_env,
    _write_swap_env,
)
from tests.test_main import _AUTH, _settings


@pytest.fixture(autouse=True)
def _no_demo_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ASTERISM_DEMO_DATASET", raising=False)


def _client() -> _DatasetClient:
    return _DatasetClient(rdflib.Dataset())


def _jobs(tmp_path: Path) -> list[dict[str, Any]]:
    path = tmp_path / "jobs.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


def _edited_design_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Any]:
    """C の版の環境で、利用者が設計（mapping.yaml）を直した。1 回の起動で印が書かれる。"""
    _Projections(monkeypatch)
    dest = _write_env(tmp_path, a_design=False)
    (dest / "mapping.yaml").write_bytes((dest / "mapping.yaml").read_bytes() + b"\n# mine\n")
    cfg = _cfg(tmp_path)
    _refresh(cfg)
    return dest, cfg


def _override(cfg: Any, dest: Path, units: list[str], **over: Any) -> dict[str, Any]:
    stamp = _read_meta(dest)["sample"]
    args: dict[str, Any] = {"seq": stamp["seq"], "revision": stamp["revision"], "units": units}
    args.update(over)
    return asyncio.run(demo_sample.manual_override(cfg, _client(), SNAPSHOT, DATASET_ID, **args))


def _code(exc: pytest.ExceptionInfo[demo_sample.SampleOpError]) -> tuple[str, int]:
    return exc.value.code, exc.value.status


# ---------------------------------------------------------------------------
# 印: 保留に生の識別子が入らない・last_update


def test_held_carries_no_raw_identifiers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dest, _ = _edited_design_env(tmp_path, monkeypatch)
    stamp = _read_meta(dest)["sample"]
    assert stamp["held"] == [{"unit": "design", "reason": "edited"}]
    text = json.dumps(stamp)
    for raw in ("mapping.yaml", "model.yaml", "diagram.md", "query_tools", "life_expectancy"):
        assert raw not in text


def test_last_update_is_written_only_when_something_was_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Projections(monkeypatch)
    # A の環境（設計が古い）→ 入れ替える → last_update
    dest = _write_env(tmp_path / "a", a_design=True)
    _refresh(_cfg(tmp_path / "a"))
    last = _read_meta(dest)["sample"]["last_update"]
    assert last["seq"] == _real().seq
    assert last["units"] == ["design"]
    # 印の無い環境の前の版は A（最初の見本）とみなし、B・C の note を新しい順に並べる
    ledger = _real().ledger
    assert last["note"]["ja"] == demo_sample.NOTE_JOIN_JA.join(
        [ledger[2]["note"]["ja"], ledger[1]["note"]["ja"]]
    )
    assert last["note"]["en"] == "; ".join([ledger[2]["note"]["en"], ledger[1]["note"]["en"]])
    assert last["at"]
    # 印の無い C の環境（ファイルが既に同じ）→ 派生だけ・last_update は書かない
    dest_c = _write_env(tmp_path / "c", a_design=False)
    _refresh(_cfg(tmp_path / "c"))
    assert "last_update" not in _read_meta(dest_c)["sample"]


def test_last_update_is_carried_over_by_a_quiet_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Projections(monkeypatch)
    dest = _write_env(tmp_path, a_design=True)
    cfg = _cfg(tmp_path)
    _refresh(cfg)
    first = _read_meta(dest)["sample"]["last_update"]
    _refresh(cfg)
    assert _read_meta(dest)["sample"]["last_update"] == first


def test_make_last_update_keeps_the_newest_three_notes() -> None:
    ledger = [{"seq": i, "note": {"ja": f"日本語{i}", "en": f"en{i}"}} for i in range(1, 7)]
    fake = dataclasses.replace(_real(), ledger=ledger, latest=ledger[-1])
    assert fake.seq == 6
    out = demo_sample.make_last_update(fake, 1, ["design"], "T")
    assert out["seq"] == 6
    join = demo_sample.NOTE_JOIN_JA
    assert out["note"] == {
        "ja": join.join(["日本語6", "日本語5", "日本語4"]),
        "en": "en6; en5; en4",
    }
    # 範囲が空（同じ版のうち保留していた単位を置き換えた）→ 最新の note 1 件
    assert demo_sample.make_last_update(fake, 6, ["design"], "T")["note"]["ja"] == "日本語6"
    # 前の印より後が 2 件だけなら、その 2 件
    assert demo_sample.make_last_update(fake, 4, [], "T")["note"]["ja"] == join.join(
        ["日本語6", "日本語5"]
    )


# ---------------------------------------------------------------------------
# アクティビティ（jobs）


def test_startup_records_one_row_and_no_more_until_something_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Projections(monkeypatch)
    dest = _write_env(tmp_path, a_design=True)
    cfg = _cfg(tmp_path)
    _refresh(cfg)
    rows = _jobs(tmp_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "sample_refresh"
    assert row["status"] == "ok"
    assert row["dataset_id"] == DATASET_ID
    assert row["sample"] == {"action": "startup", "units": ["design"], "held": []}
    assert row["ended_at"]
    _refresh(cfg)
    _refresh(cfg)
    assert len(_jobs(tmp_path)) == 1  # 2 回目・3 回目の起動では増えない
    assert dest.exists()


def test_startup_records_a_held_environment_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest, cfg = _edited_design_env(tmp_path, monkeypatch)
    rows = _jobs(tmp_path)
    assert len(rows) == 1
    assert rows[0]["status"] == "partial"
    assert rows[0]["sample"]["held"] == [{"unit": "design", "reason": "edited"}]
    assert "mapping" not in json.dumps(rows[0])
    _refresh(cfg)
    assert len(_jobs(tmp_path)) == 1  # 保留のまま続く起動では増えない
    assert dest.exists()


def test_startup_of_an_unchanged_c_environment_records_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Projections(monkeypatch)
    _write_env(tmp_path, a_design=False)
    _refresh(_cfg(tmp_path))
    assert _jobs(tmp_path) == []  # 何も入れ替えず・保留も無い


# ---------------------------------------------------------------------------
# 手動の置き換え


def test_override_takes_a_backup_and_replaces_only_the_held_unit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest, cfg = _edited_design_env(tmp_path, monkeypatch)
    mine = (dest / "mapping.yaml").read_bytes()
    before = _read_meta(dest)
    out = _override(cfg, dest, ["design"])
    assert out["units"] == ["design"]
    assert out["held"] == []

    b = _real()
    for member in demo_sample.UNIT_TABLE["design"]["members"]:
        assert (dest / member[len("registry/") :]).read_bytes() == b.members[member]
    assert not (dest / "history").exists()  # 履歴は作らない（作ると起動時の埋め直しが止まる）
    after = _read_meta(dest)
    stamp = after["sample"]
    assert stamp["held"] == []
    assert "design" in stamp["units"]
    assert stamp["last_update"]["units"] == ["design"]
    assert len(stamp["backups"]) == 1
    backup = stamp["backups"][0]
    assert backup["units"] == ["design"]
    assert backup["dir"].startswith("sample-backup/") and backup["dir"].endswith("/")
    assert (dest / backup["dir"] / "files" / "mapping.yaml").read_bytes() == mine
    for key in ("name", "imported", "version", "published_subjects", "live_graph", "status"):
        assert after[key] == before[key], key
    # 次の起動は何も書かない
    raw = (dest / "meta.json").read_bytes()
    _refresh(cfg)
    assert (dest / "meta.json").read_bytes() == raw


def test_override_records_one_activity_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dest, cfg = _edited_design_env(tmp_path, monkeypatch)
    _override(cfg, dest, ["design"])
    rows = _jobs(tmp_path)
    assert [r["sample"]["action"] for r in rows] == ["startup", "override"]
    assert rows[1]["status"] == "ok"
    assert rows[1]["sample"]["units"] == ["design"]


def test_override_replaces_an_edited_name_and_an_edited_description(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Projections(monkeypatch)
    dest = _write_env(tmp_path, a_design=False, meta_over={"name": "わたしの名前"})
    (dest / "mie.yaml").write_bytes((dest / "mie.yaml").read_bytes() + b"\n# mine\n")
    cfg = _cfg(tmp_path)
    _refresh(cfg)
    reasons = {(h["unit"], h["reason"]) for h in _read_meta(dest)["sample"]["held"]}
    assert reasons == {("name", "edited"), ("description", "edited")}
    _override(cfg, dest, ["name", "description"])
    after = _read_meta(dest)
    assert after["name"] == _real().name
    assert (dest / "mie.yaml").read_bytes() == _real().members["registry/mie.yaml"]
    assert after["sample"]["held"] == []
    assert sorted(after["sample"]["backups"][0]["units"]) == ["description", "name"]


def test_override_of_an_edited_tool_keeps_the_users_own_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Projections(monkeypatch)
    dest = _write_env(tmp_path, a_design=False)
    tools = demo_sample.parse_tools((dest / "query_tools.yaml").read_text("utf-8"))
    fixed = dict(tools[0])
    fixed["description"] = "利用者が直した説明"
    mine = {**tools[1], "name": "my_own_tool", "title": "わたしの道具"}
    (dest / "query_tools.yaml").write_bytes(demo_sample.dump_tools([fixed, *tools[1:], mine]))
    cfg = _cfg(tmp_path)
    _refresh(cfg)
    held = _read_meta(dest)["sample"]["held"]
    assert held == [{"unit": "tools", "reason": "edited", "titles": [tools[0]["title"]]}]
    _override(cfg, dest, ["tools"])
    written = {
        t["name"]: t
        for t in demo_sample.parse_tools((dest / "query_tools.yaml").read_text("utf-8"))
    }
    assert written[tools[0]["name"]] == tools[0]  # 同梱の定義に戻った
    assert "my_own_tool" in written  # 利用者が足したものは残る


def test_backups_keep_five_and_older_directories_are_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest, cfg = _edited_design_env(tmp_path, monkeypatch)
    dirs: list[str] = []
    for i in range(7):
        _override(cfg, dest, ["design"])
        dirs.append(_read_meta(dest)["sample"]["backups"][0]["dir"])
        (dest / "mapping.yaml").write_bytes(f"# mine {i}\n".encode())
        # 速い道（最新で保留なし）は編集を見ないので、印の units を落として判定し直させる
        meta = _read_meta(dest)
        meta["sample"]["units"] = {}
        (dest / "meta.json").write_text(json.dumps(meta), "utf-8")
        _refresh(cfg)  # 印に「触った」を書き直す
    backups = _read_meta(dest)["sample"]["backups"]
    assert len(backups) == 5
    assert [b["dir"] for b in backups] == list(reversed(dirs[2:]))
    present = sorted(p.name for p in (dest / "sample-backup").iterdir())
    assert present == sorted(d.split("/")[1] for d in dirs[2:])


def test_backups_are_not_exported_in_a_snapshot(tmp_path: Path) -> None:
    """書き出し（exchange.build_snapshot）は dataset ディレクトリを丸ごと歩くので、この環境の
    利用者の控えを配らないよう除く。"""
    import io
    import tarfile

    from asterism_api import exchange
    from tests import test_exchange as ex

    ds = rdflib.Dataset()
    ex._seed_promoted(tmp_path, ds, "zem", base=ex._INVALID_BASE)
    backup = tmp_path / "registry" / "zem" / "sample-backup" / "20260930T000000000000Z"
    backup.mkdir(parents=True)
    (backup / "backup.json").write_text("{}", "utf-8")
    payload, _name = asyncio.run(
        exchange.build_snapshot(ex._settings(tmp_path), ex._DatasetClient(ds), "zem")
    )
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tar:
        names = tar.getnames()
    assert "registry/model.yaml" in names
    assert not [n for n in names if "sample-backup" in n]


# ---------------------------------------------------------------------------
# 控えから戻す


def test_restore_puts_the_users_files_back_and_the_hold_returns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest, cfg = _edited_design_env(tmp_path, monkeypatch)
    mine = (dest / "mapping.yaml").read_bytes()
    _override(cfg, dest, ["design"])
    at = _read_meta(dest)["sample"]["backups"][0]["at"]
    directory = _read_meta(dest)["sample"]["backups"][0]["dir"]

    out = asyncio.run(demo_sample.manual_restore(cfg, _client(), SNAPSHOT, DATASET_ID, at=at))
    assert out["units"] == ["design"]
    assert (dest / "mapping.yaml").read_bytes() == mine
    stamp = _read_meta(dest)["sample"]
    assert "design" not in stamp["units"]  # 次の起動でまた「触った」と判定される
    assert stamp["held"] == [{"unit": "design", "reason": "edited"}]  # すぐ保留に戻って見える
    assert "backups" not in stamp
    assert "last_update" not in stamp
    assert not (dest / directory).exists()
    assert not (dest / "history").exists()
    # 次の起動でも保留のまま・ファイルはそのまま
    _refresh(cfg)
    assert (dest / "mapping.yaml").read_bytes() == mine
    assert [r["sample"]["action"] for r in _jobs(tmp_path)] == ["startup", "override", "restore"]
    assert _jobs(tmp_path)[-1]["status"] == "ok"


def test_restore_of_an_unknown_backup_is_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest, cfg = _edited_design_env(tmp_path, monkeypatch)
    _override(cfg, dest, ["design"])
    with pytest.raises(demo_sample.SampleOpError) as exc:
        asyncio.run(demo_sample.manual_restore(cfg, _client(), SNAPSHOT, DATASET_ID, at="x"))
    assert _code(exc) == ("stale", 409)


def test_a_failed_restore_projection_keeps_the_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest, cfg = _edited_design_env(tmp_path, monkeypatch)
    _override(cfg, dest, ["design"])
    at = _read_meta(dest)["sample"]["backups"][0]["at"]
    _Projections(monkeypatch, onto=RuntimeError("boom"))
    with pytest.raises(demo_sample.SampleOpError) as exc:
        asyncio.run(demo_sample.manual_restore(cfg, _client(), SNAPSHOT, DATASET_ID, at=at))
    assert _code(exc) == ("failed", 500)
    assert _read_meta(dest)["sample"]["backups"][0]["at"] == at  # もう一度押せる
    assert _jobs(tmp_path)[-1]["status"] == "error"


def test_a_failed_restore_after_a_later_edit_keeps_the_edit_reachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """置き換えのあとに直したうえで、戻す途中の投影が失敗しても、直した内容の控えは印に載る
    （画面から辿れる）。押し直しは、戻した内容をもう一度控えず、直した内容の控えを残し、
    その控えから直しが返る。"""
    dest, cfg = _edited_design_env(tmp_path, monkeypatch)
    mine = (dest / "mapping.yaml").read_bytes()
    _override(cfg, dest, ["design"])
    later = (dest / "mapping.yaml").read_bytes() + b"# LATER EDIT AFTER OVERRIDE\n"
    (dest / "mapping.yaml").write_bytes(later)
    at = _read_meta(dest)["sample"]["backups"][0]["at"]

    _Projections(monkeypatch, onto=RuntimeError("boom"))
    with pytest.raises(demo_sample.SampleOpError) as exc:
        asyncio.run(demo_sample.manual_restore(cfg, _client(), SNAPSHOT, DATASET_ID, at=at))
    assert _code(exc) == ("failed", 500)
    listed = _read_meta(dest)["sample"]["backups"]
    assert listed[0]["at"] == at  # もう一度押せる
    assert len(listed) == 2
    # ディスクの控えはすべて印に載っている（孤児が無い）
    on_disk = {f"sample-backup/{p.name}/" for p in (dest / "sample-backup").iterdir()}
    assert on_disk == {b["dir"] for b in listed}
    assert (dest / listed[1]["dir"] / "files" / "mapping.yaml").read_bytes() == later

    _Projections(monkeypatch)
    asyncio.run(demo_sample.manual_restore(cfg, _client(), SNAPSHOT, DATASET_ID, at=at))
    assert (dest / "mapping.yaml").read_bytes() == mine
    listed = _read_meta(dest)["sample"]["backups"]
    assert len(listed) == 1  # 戻した内容を、もう一度控えない
    assert {p.name for p in (dest / "sample-backup").iterdir()} == {listed[0]["dir"].split("/")[1]}
    _restore(cfg, dest)  # 直した内容の控えから戻せる
    assert (dest / "mapping.yaml").read_bytes() == later
    assert "backups" not in _read_meta(dest)["sample"]


def _restore(cfg: Any, dest: Path) -> dict[str, Any]:
    at = _read_meta(dest)["sample"]["backups"][0]["at"]
    return asyncio.run(demo_sample.manual_restore(cfg, _client(), SNAPSHOT, DATASET_ID, at=at))


def _newest_backup(dest: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    entry = _read_meta(dest)["sample"]["backups"][0]
    manifest = json.loads((dest / entry["dir"] / "backup.json").read_text("utf-8"))
    return entry, manifest


def test_restore_keeps_what_the_user_changed_after_the_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """置き換えたあとに直した設計を、控えから戻して失わない: 戻す前のいまの内容を新しい控えに
    取り、その控えから戻せば直しが返る。直していなければ新しい控えは作らない。"""
    dest, cfg = _edited_design_env(tmp_path, monkeypatch)
    mine = (dest / "mapping.yaml").read_bytes()
    _override(cfg, dest, ["design"])
    later = (dest / "mapping.yaml").read_bytes() + b"# LATER EDIT AFTER OVERRIDE\n"
    (dest / "mapping.yaml").write_bytes(later)
    old_dir = _read_meta(dest)["sample"]["backups"][0]["dir"]

    _restore(cfg, dest)
    assert (dest / "mapping.yaml").read_bytes() == mine
    entry, manifest = _newest_backup(dest)
    assert entry["dir"] != old_dir and not (dest / old_dir).exists()
    assert len(_read_meta(dest)["sample"]["backups"]) == 1
    assert (dest / entry["dir"] / "files" / "mapping.yaml").read_bytes() == later
    assert manifest["units"] == ["design"]
    assert not (dest / "history").exists()

    # その控えから戻せば、置き換えのあとの直しが返る（往復できる）
    _restore(cfg, dest)
    assert (dest / "mapping.yaml").read_bytes() == later
    assert "backups" not in _read_meta(dest)["sample"]


def test_restore_of_untouched_replacement_makes_no_new_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest, cfg = _edited_design_env(tmp_path, monkeypatch)
    _override(cfg, dest, ["design"])
    _restore(cfg, dest)
    assert "backups" not in _read_meta(dest)["sample"]
    assert not any((dest / "sample-backup").iterdir())


def test_restore_of_the_tools_keeps_a_tool_added_after_the_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Projections(monkeypatch)
    dest = _write_env(tmp_path, a_design=False)
    tools = demo_sample.parse_tools((dest / "query_tools.yaml").read_text("utf-8"))
    fixed = {**tools[0], "description": "利用者が直した説明"}
    mine = demo_sample.dump_tools([fixed, *tools[1:]])
    (dest / "query_tools.yaml").write_bytes(mine)
    cfg = _cfg(tmp_path)
    _refresh(cfg)
    _override(cfg, dest, ["tools"])
    added = {**tools[1], "name": "added_after_override", "title": "あとから足した道具"}
    current = demo_sample.parse_tools((dest / "query_tools.yaml").read_text("utf-8"))
    later = demo_sample.dump_tools([*current, added])
    (dest / "query_tools.yaml").write_bytes(later)

    _restore(cfg, dest)
    assert (dest / "query_tools.yaml").read_bytes() == mine
    assert _read_meta(dest)["sample"]["held"] == [
        {"unit": "tools", "reason": "edited", "titles": [tools[0]["title"]]}
    ]
    entry, _ = _newest_backup(dest)
    assert (dest / entry["dir"] / "files" / "query_tools.yaml").read_bytes() == later
    _restore(cfg, dest)
    names = [
        t["name"] for t in demo_sample.parse_tools((dest / "query_tools.yaml").read_text("utf-8"))
    ]
    assert "added_after_override" in names


def test_restore_of_a_name_puts_the_users_name_back_and_keeps_a_later_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Projections(monkeypatch)
    dest = _write_env(tmp_path, a_design=False, meta_over={"name": "わたしの名前"})
    cfg = _cfg(tmp_path)
    _refresh(cfg)
    assert _read_meta(dest)["sample"]["held"] == [{"unit": "name", "reason": "edited"}]
    _override(cfg, dest, ["name"])
    assert _read_meta(dest)["name"] == _real().name
    entry, manifest = _newest_backup(dest)
    assert entry["units"] == ["name"]
    assert manifest["fields"] == {"name": "わたしの名前"}  # 名前の控え
    assert manifest["files"] == []

    # 何も直していなければ、名前が戻り、保留に戻り、新しい控えは作らない
    _restore(cfg, dest)
    meta = _read_meta(dest)
    assert meta["name"] == "わたしの名前"
    assert meta["sample"]["held"] == [{"unit": "name", "reason": "edited"}]
    assert "backups" not in meta["sample"]

    # 置き換えたあとに名前を付け直したなら、その名前は新しい控えに残る
    _override(cfg, dest, ["name"])
    registry_meta = _read_meta(dest)
    registry_meta["name"] = "あとで付けた名前"
    (dest / "meta.json").write_text(json.dumps(registry_meta, ensure_ascii=False), "utf-8")
    _restore(cfg, dest)
    assert _read_meta(dest)["name"] == "わたしの名前"
    _entry, manifest = _newest_backup(dest)
    assert manifest["fields"] == {"name": "あとで付けた名前"}
    _restore(cfg, dest)
    assert _read_meta(dest)["name"] == "あとで付けた名前"


def test_restore_of_a_description_reprojects_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = _Projections(monkeypatch)
    dest = _write_env(tmp_path, a_design=False)
    (dest / "mie.yaml").write_bytes((dest / "mie.yaml").read_bytes() + b"\n# mine\n")
    mine = (dest / "mie.yaml").read_bytes()
    cfg = _cfg(tmp_path)
    _refresh(cfg)
    _override(cfg, dest, ["description"])
    calls = len(proj.meta_calls)
    _restore(cfg, dest)
    assert (dest / "mie.yaml").read_bytes() == mine
    assert len(proj.meta_calls) > calls  # 戻した説明を投影し直した
    assert _read_meta(dest)["sample"]["held"] == [{"unit": "description", "reason": "edited"}]


def test_restore_refuses_a_backup_that_points_outside_or_lists_another_unit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest, cfg = _edited_design_env(tmp_path, monkeypatch)
    _override(cfg, dest, ["design"])
    replaced = (dest / "mapping.yaml").read_bytes()
    # 印の dir が外を指している（meta.json は利用者が書き換えられる）→ 消さず・戻さず、印から外す
    (dest / "outside").mkdir()
    (dest / "outside" / "keep.txt").write_text("keep", "utf-8")
    for bad in ("../outside", "sample-backup/../outside", "/etc", "sample-backup"):
        meta = _read_meta(dest)
        meta["sample"]["backups"] = [{"at": "T", "units": ["design"], "dir": bad}]
        (dest / "meta.json").write_text(json.dumps(meta), "utf-8")
        with pytest.raises(demo_sample.SampleOpError) as exc:
            asyncio.run(demo_sample.manual_restore(cfg, _client(), SNAPSHOT, DATASET_ID, at="T"))
        assert _code(exc) == ("stale", 409), bad
        assert "backups" not in _read_meta(dest)["sample"]
    assert (dest / "outside" / "keep.txt").read_text("utf-8") == "keep"
    assert (dest / "mapping.yaml").read_bytes() == replaced

    # 控えの一覧が別の単位のファイルを挙げている → 戻さない（失敗・控えは残る）
    _restore_target = dest / "sample-backup" / "20260930T000000000000Z"
    (_restore_target / "files").mkdir(parents=True)
    (_restore_target / "files" / "mapping.yaml").write_bytes(b"evil")
    (_restore_target / "backup.json").write_text(
        json.dumps({"units": ["tools"], "files": ["mapping.yaml"], "fields": {}}), "utf-8"
    )
    meta = _read_meta(dest)
    meta["sample"]["backups"] = [
        {"at": "T2", "units": ["tools"], "dir": "sample-backup/20260930T000000000000Z/"}
    ]
    (dest / "meta.json").write_text(json.dumps(meta), "utf-8")
    with pytest.raises(demo_sample.SampleOpError) as exc:
        asyncio.run(demo_sample.manual_restore(cfg, _client(), SNAPSHOT, DATASET_ID, at="T2"))
    assert _code(exc) == ("failed", 500)
    assert (dest / "mapping.yaml").read_bytes() == replaced
    assert _read_meta(dest)["sample"]["backups"][0]["at"] == "T2"


@pytest.mark.parametrize("how", ["dotdot", "symlink"])
def test_restore_never_reads_or_removes_a_real_backup_outside_the_backup_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    """外側に有効な backup.json と files がある場所を、印の dir が指しても（``..`` でも、
    控えの下のシンボリックリンクでも）読まず・消さず・戻さない。"""
    dest, cfg = _edited_design_env(tmp_path, monkeypatch)
    _override(cfg, dest, ["design"])
    replaced = (dest / "mapping.yaml").read_bytes()
    outside = dest.parent / "outside"  # dataset ディレクトリの外（``../outside``）
    (outside / "files").mkdir(parents=True)
    (outside / "files" / "mapping.yaml").write_bytes(b"evil")
    (outside / "backup.json").write_text(
        json.dumps({"units": ["design"], "files": ["mapping.yaml"], "fields": {}}), "utf-8"
    )
    if how == "dotdot":
        bad = "../outside"
    else:
        (dest / "sample-backup" / "link").symlink_to(outside, target_is_directory=True)
        bad = "sample-backup/link/"
    meta = _read_meta(dest)
    meta["sample"]["backups"] = [{"at": "T", "units": ["design"], "dir": bad}]
    (dest / "meta.json").write_text(json.dumps(meta), "utf-8")
    with pytest.raises(demo_sample.SampleOpError) as exc:
        asyncio.run(demo_sample.manual_restore(cfg, _client(), SNAPSHOT, DATASET_ID, at="T"))
    assert _code(exc) == ("stale", 409)
    assert (dest / "mapping.yaml").read_bytes() == replaced
    assert (outside / "files" / "mapping.yaml").read_bytes() == b"evil"
    assert (outside / "backup.json").is_file()
    assert "backups" not in _read_meta(dest)["sample"]


def test_restore_of_a_backup_whose_directory_is_gone_drops_it_from_the_stamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    dest, cfg = _edited_design_env(tmp_path, monkeypatch)
    _override(cfg, dest, ["design"])
    entry = _read_meta(dest)["sample"]["backups"][0]
    shutil.rmtree(dest / entry["dir"])
    for _ in range(2):  # 押すたびに失敗し続けない（1 回目で印から外れ、2 回目は控えが無い）
        with pytest.raises(demo_sample.SampleOpError) as exc:
            asyncio.run(
                demo_sample.manual_restore(cfg, _client(), SNAPSHOT, DATASET_ID, at=entry["at"])
            )
        assert _code(exc) == ("stale", 409)
    assert "backups" not in _read_meta(dest)["sample"]


def test_restore_still_succeeds_when_rewriting_the_held_marks_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ファイルを戻して控えを外したあとの、保留の書き直しが落ちても、戻しは済んでいる。
    失敗として返さない（もう一度押せる控えは無い）。"""
    dest, cfg = _edited_design_env(tmp_path, monkeypatch)
    mine = (dest / "mapping.yaml").read_bytes()
    _override(cfg, dest, ["design"])

    async def boom(*_a: Any, **_kw: Any) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(demo_sample, "_refresh", boom)
    out = _restore(cfg, dest)
    assert out["units"] == ["design"]
    assert (dest / "mapping.yaml").read_bytes() == mine
    assert _jobs(tmp_path)[-1]["sample"]["action"] == "restore"
    assert _jobs(tmp_path)[-1]["status"] == "ok"


# ---------------------------------------------------------------------------
# 409 の各理由


def test_override_refuses_a_stale_screen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dest, cfg = _edited_design_env(tmp_path, monkeypatch)
    for over in ({"seq": 1}, {"revision": "0000000000000000"}):
        with pytest.raises(demo_sample.SampleOpError) as exc:
            _override(cfg, dest, ["design"], **over)
        assert _code(exc) == ("stale", 409)
    assert "backups" not in _read_meta(dest)["sample"]


def test_override_refuses_units_that_are_not_overridable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest, cfg = _edited_design_env(tmp_path, monkeypatch)
    for units in ([], ["name"], ["data"], ["design", "tools"]):
        with pytest.raises(demo_sample.SampleOpError) as exc:
            _override(cfg, dest, units)
        assert _code(exc) == ("not_overridable", 409), units


def test_override_does_not_replace_ids_moves_or_appended_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Projections(monkeypatch)
    dest = _write_env(
        tmp_path,
        a_design=False,
        meta_over={"published_subjects": [{"map": "other"}]},
    )
    cfg = _cfg(tmp_path)
    _refresh(cfg)
    held = _read_meta(dest)["sample"]["held"]
    assert {h["reason"] for h in held} == {"ids_move"}
    with pytest.raises(demo_sample.SampleOpError) as exc:
        _override(cfg, dest, ["design"])
    assert _code(exc) == ("not_overridable", 409)


_GROUP_HOLDS = [
    "feed",
    "append_seq",
    "applied_batches",
    "reshape",
    "source_edited",
    "design_edited",
    "live_graph_differs",
    "control_differs",
    "ids_move",
    "ids_unknown",
]


@pytest.mark.parametrize("name", _GROUP_HOLDS)
def test_override_never_replaces_the_data_group(tmp_path: Path, name: str) -> None:
    """データが変わる版で、追記・取り込み直し・決めた内容・引用の住所・決着していない状態・
    編集のどれかで群が保留のとき、design・data・tools のどの置き換えも 409 で断り、
    ファイル・meta・ストア・控えは何も変わらない（利用者のデータや引用の住所を守る）。"""
    ds, client = _old_store()
    dest = _write_swap_env(tmp_path, stamp=True)
    alter = {c[0]: c[1] for c in _HOLD_CASES}[name]
    alter(dest, ds)
    _refresh_release(tmp_path, client)
    stamp = _read_meta(dest)["sample"]
    assert {h["unit"] for h in stamp["held"]} >= {"design", "data", "tools"}
    files, meta_raw, nquads = _files_of(dest), (dest / "meta.json").read_bytes(), _nquads(ds)
    for units in (["design"], ["data"], ["tools"], ["design", "data", "tools"]):
        with pytest.raises(demo_sample.SampleOpError) as exc:
            asyncio.run(
                demo_sample.manual_override(
                    _cfg(tmp_path),
                    client,
                    _release_file(tmp_path),
                    DATASET_ID,
                    seq=stamp["seq"],
                    revision=stamp["revision"],
                    units=units,
                )
            )
        assert _code(exc) == ("not_overridable", 409), (name, units)
    assert _files_of(dest) == files
    assert (dest / "meta.json").read_bytes() == meta_raw
    assert _nquads(ds) == nquads
    assert not (dest / "sample-backup").exists()


def test_override_of_a_decisions_only_hold_replaces_nothing_and_says_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """決めた内容だけが保留の理由のとき、設計のファイルは同梱と同じで置き換わるものが無い。
    控えは作らず、決めた内容のファイルは残り、アクティビティに「置き換えました」を書かない。"""
    _Projections(monkeypatch)
    dest = _write_env(tmp_path, a_design=False)
    (dest / "column-decisions.json").write_text("{}", "utf-8")
    cfg = _cfg(tmp_path)
    _refresh(cfg)
    assert _read_meta(dest)["sample"]["held"] == [{"unit": "design", "reason": "decisions"}]
    out = _override(cfg, dest, ["design"])
    assert out["units"] == []
    assert (dest / "column-decisions.json").exists()
    assert not (dest / "sample-backup").exists()
    assert "backups" not in _read_meta(dest)["sample"]
    assert _read_meta(dest)["sample"]["held"] == []  # 置き換えたあとは保留が残らない
    assert [r["sample"]["action"] for r in _jobs(tmp_path)] == ["startup"]


def test_override_of_an_edited_design_with_decisions_replaces_it_and_clears_both_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """設計の編集と決めた内容が両方の保留のとき、置き換えで設計が入れ替わり、保留は両方消える
    （置き換えを押しても何も起きず、ボタンが残り続ける状態にならない）。決めた内容のファイルは残る。"""
    dest, cfg = _edited_design_env(tmp_path, monkeypatch)
    (dest / "column-decisions.json").write_text("{}", "utf-8")
    _refresh(cfg)
    held = _read_meta(dest)["sample"]["held"]
    assert {(h["unit"], h["reason"]) for h in held} == {
        ("design", "edited"),
        ("design", "decisions"),
    }
    out = _override(cfg, dest, ["design"])
    assert out["units"] == ["design"] and out["held"] == []
    assert b"# mine" not in (dest / "mapping.yaml").read_bytes()
    assert (dest / "column-decisions.json").exists()
    stamp = _read_meta(dest)["sample"]
    assert stamp["held"] == []
    assert "design" in stamp["units"]


@pytest.mark.parametrize(
    ("meta_over", "code"),
    [
        ({"ingested": True}, "ingest_in_progress"),
        ({"data_seq": 5}, "ingest_reserved"),
        ({"status": "retracted"}, "retracted"),
        ({"promoted": False}, "not_sample"),
        ({"origin": "user"}, "not_sample"),
    ],
)
def test_override_refuses_a_dataset_in_the_wrong_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, meta_over: dict, code: str
) -> None:
    dest, cfg = _edited_design_env(tmp_path, monkeypatch)
    stamp = _read_meta(dest)["sample"]
    meta = _read_meta(dest)
    meta.update(meta_over)
    (dest / "meta.json").write_text(json.dumps(meta), "utf-8")
    with pytest.raises(demo_sample.SampleOpError) as exc:
        asyncio.run(
            demo_sample.manual_override(
                cfg,
                _client(),
                SNAPSHOT,
                DATASET_ID,
                seq=stamp["seq"],
                revision=stamp["revision"],
                units=["design"],
            )
        )
    assert _code(exc) == (code, 409)
    assert (dest / "mapping.yaml").read_bytes().endswith(b"# mine\n")  # 何も変わらない


def test_override_refuses_a_staged_ingest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from asterism import substrate

    dest, cfg = _edited_design_env(tmp_path, monkeypatch)
    ds = rdflib.Dataset()
    client = _DatasetClient(ds)
    asyncio.run(
        substrate.set_staged_graph(
            client,
            substrate.canonical_graph_iri(DATASET_ID),
            substrate.versioned_graph_iri(DATASET_ID, 2),
        )
    )
    stamp = _read_meta(dest)["sample"]
    with pytest.raises(demo_sample.SampleOpError) as exc:
        asyncio.run(
            demo_sample.manual_override(
                cfg,
                client,
                SNAPSHOT,
                DATASET_ID,
                seq=stamp["seq"],
                revision=stamp["revision"],
                units=["design"],
            )
        )
    assert _code(exc) == ("ingest_in_progress", 409)


def test_override_refuses_a_dataset_that_is_not_the_sample(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest, cfg = _edited_design_env(tmp_path, monkeypatch)
    other = dest.parent / "mine"
    other.mkdir()
    (other / "meta.json").write_text(json.dumps(_meta(_real(), id="mine")), "utf-8")
    with pytest.raises(demo_sample.SampleOpError) as exc:
        asyncio.run(
            demo_sample.manual_override(
                cfg, _client(), SNAPSHOT, "mine", seq=3, revision="x", units=["design"]
            )
        )
    assert _code(exc) == ("not_sample", 409)
    with pytest.raises(demo_sample.SampleOpError) as exc:
        asyncio.run(
            demo_sample.manual_override(
                cfg, _client(), SNAPSHOT, "missing", seq=3, revision="x", units=["design"]
            )
        )
    assert _code(exc) == ("not_found", 404)


def test_override_needs_a_single_user(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _edited_design_env(tmp_path, monkeypatch)
    with pytest.raises(demo_sample.SampleOpError) as exc:
        asyncio.run(
            demo_sample.manual_override(
                _cfg(tmp_path, single_user=False),
                _client(),
                SNAPSHOT,
                DATASET_ID,
                seq=3,
                revision="x",
                units=["design"],
            )
        )
    assert _code(exc) == ("not_available", 404)


# ---------------------------------------------------------------------------
# HTTP ルート


@pytest.fixture
def _snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local, "find_world_snapshot", lambda: SNAPSHOT)


def _app(tmp_path: Path, *, single_user: bool = True) -> TestClient:
    cfg = _settings(tmp_path)
    cfg.single_user = single_user
    app = main_mod.build_app(cfg, oxigraph_client=_client(), start_watcher=False)
    return TestClient(app)


def _post(client: TestClient, path: str, body: Any, **over: Any):
    headers = {**_AUTH, "Content-Type": "application/json", INTENT_HEADER: INTENT_REFRESH}
    headers.update(over.pop("headers", {}))
    payload = body if isinstance(body, (bytes, str)) else json.dumps(body)
    return client.post(path, content=payload, headers=headers, **over)


def _route_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict[str, Any]]:
    _Projections(monkeypatch)
    dest = _write_env(tmp_path, a_design=False)
    (dest / "mapping.yaml").write_bytes((dest / "mapping.yaml").read_bytes() + b"\n# mine\n")
    _refresh(_cfg(tmp_path))
    stamp = _read_meta(dest)["sample"]
    return dest, {"seq": stamp["seq"], "revision": stamp["revision"], "units": ["design"]}


REFRESH = f"/api/datasets/{DATASET_ID}/sample/refresh"
RESTORE = f"/api/datasets/{DATASET_ID}/sample/restore"


def test_route_refresh_then_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _snapshot: None
) -> None:
    dest, body = _route_env(tmp_path, monkeypatch)
    mine = (dest / "mapping.yaml").read_bytes()
    with _app(tmp_path) as client:
        res = _post(client, REFRESH, body)
        assert res.status_code == 200, res.text
        assert res.json()["units"] == ["design"]
        assert (dest / "mapping.yaml").read_bytes() == _real().members["registry/mapping.yaml"]
        at = _read_meta(dest)["sample"]["backups"][0]["at"]

        res = _post(client, RESTORE, {"at": at}, headers={INTENT_HEADER: INTENT_RESTORE})
        assert res.status_code == 200, res.text
        assert (dest / "mapping.yaml").read_bytes() == mine


def test_route_needs_json_the_intent_header_and_a_valid_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _snapshot: None
) -> None:
    dest, body = _route_env(tmp_path, monkeypatch)
    mine = (dest / "mapping.yaml").read_bytes()
    with _app(tmp_path) as client:
        # 本文なし・text/plain の単純な POST（ほかのページから送れる形）
        res = client.post(REFRESH, content=b"", headers={**_AUTH, "Content-Type": "text/plain"})
        assert (res.status_code, res.json()["detail"]["error"]) == (415, "json_required")
        res = client.post(REFRESH, content=json.dumps(body), headers=_AUTH)
        assert res.status_code == 415
        res = client.post(
            REFRESH,
            content=json.dumps(body),
            headers={**_AUTH, "Content-Type": "text/plain", INTENT_HEADER: INTENT_REFRESH},
        )
        assert res.status_code == 415
        # 見出しが無い・違う（restore の見出しでは refresh を通さない）
        res = _post(client, REFRESH, body, headers={INTENT_HEADER: ""})
        assert (res.status_code, res.json()["detail"]["error"]) == (403, "intent_required")
        res = _post(client, REFRESH, body, headers={INTENT_HEADER: INTENT_RESTORE})
        assert res.status_code == 403
        # 本文が壊れている・形が違う
        for bad in (
            "{",
            "[]",
            json.dumps({"seq": "1", "revision": "x", "units": []}),
            json.dumps({"seq": 3, "revision": "x", "units": "design"}),
        ):
            res = _post(client, REFRESH, bad)
            assert (res.status_code, res.json()["detail"]["error"]) == (400, "bad_request"), bad
        # 書き込み認証
        res = client.post(
            REFRESH,
            content=json.dumps(body),
            headers={"Content-Type": "application/json", INTENT_HEADER: INTENT_REFRESH},
        )
        assert res.status_code in (401, 503)
    assert (dest / "mapping.yaml").read_bytes() == mine  # どれも書いていない
    assert not (dest / "sample-backup").exists()


def test_restore_route_needs_json_the_intent_header_a_valid_body_and_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _snapshot: None
) -> None:
    dest, body = _route_env(tmp_path, monkeypatch)
    with _app(tmp_path) as client:
        assert _post(client, REFRESH, body).status_code == 200
        at = _read_meta(dest)["sample"]["backups"][0]["at"]
        replaced = (dest / "mapping.yaml").read_bytes()
        payload = json.dumps({"at": at})
        res = client.post(RESTORE, content=payload, headers={**_AUTH, "Content-Type": "text/plain"})
        assert (res.status_code, res.json()["detail"]["error"]) == (415, "json_required")
        res = client.post(RESTORE, content=payload, headers=_AUTH)
        assert res.status_code == 415
        # 見出しが無い・refresh の見出し
        res = _post(client, RESTORE, {"at": at}, headers={INTENT_HEADER: ""})
        assert (res.status_code, res.json()["detail"]["error"]) == (403, "intent_required")
        res = _post(client, RESTORE, {"at": at}, headers={INTENT_HEADER: INTENT_REFRESH})
        assert res.status_code == 403
        # 本文が壊れている・形が違う
        for bad in ("{", "[]", json.dumps({"at": 1}), json.dumps({"at": ""}), json.dumps({})):
            res = _post(client, RESTORE, bad, headers={INTENT_HEADER: INTENT_RESTORE})
            assert (res.status_code, res.json()["detail"]["error"]) == (400, "bad_request"), bad
        # 書き込み認証
        res = client.post(
            RESTORE,
            content=payload,
            headers={"Content-Type": "application/json", INTENT_HEADER: INTENT_RESTORE},
        )
        assert res.status_code in (401, 503)
        # 知らない控え
        res = _post(client, RESTORE, {"at": "x"}, headers={INTENT_HEADER: INTENT_RESTORE})
        assert (res.status_code, res.json()["detail"]) == (409, {"error": "stale"})
    assert (dest / "mapping.yaml").read_bytes() == replaced  # どれも戻していない
    assert _read_meta(dest)["sample"]["backups"][0]["at"] == at


def test_restore_route_is_busy_while_another_request_holds_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _snapshot: None
) -> None:
    dest, body = _route_env(tmp_path, monkeypatch)
    with _app(tmp_path) as client:
        assert _post(client, REFRESH, body).status_code == 200
        at = _read_meta(dest)["sample"]["backups"][0]["at"]
        lock = asyncio.Lock()
        asyncio.run(lock.acquire())
        client.app.state.sample_locks[DATASET_ID] = lock
        res = _post(client, RESTORE, {"at": at}, headers={INTENT_HEADER: INTENT_RESTORE})
        assert (res.status_code, res.json()["detail"]) == (409, {"error": "busy"})


def test_route_holds_the_lock_for_the_whole_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _snapshot: None
) -> None:
    """1 つ目が途中（await の最中）にいる間に来た 2 つ目は 409 busy。終われば次は通る。
    ロックを手で先に取るのでなく、実際に走っている操作の途中で確かめる。"""
    import threading

    _dest, body = _route_env(tmp_path, monkeypatch)
    started, release = threading.Event(), threading.Event()
    calls: list[int] = []

    async def slow(*_a: Any, **_kw: Any) -> dict[str, Any]:
        calls.append(1)
        started.set()
        while len(calls) == 1 and not release.is_set():  # 2 つ目が入ってきても待たせない
            await asyncio.sleep(0.01)
        return {"units": [], "held": []}

    monkeypatch.setattr(demo_sample, "manual_override", slow)
    results: list[Any] = []
    with _app(tmp_path) as client:
        first = threading.Thread(target=lambda: results.append(_post(client, REFRESH, body)))
        first.start()
        try:
            assert started.wait(10)
            res = _post(client, REFRESH, body)
            assert (res.status_code, res.json()["detail"]) == (409, {"error": "busy"})
            assert calls == [1]  # 2 つ目は中身まで入っていない
        finally:
            release.set()
            first.join(10)
        assert results[0].status_code == 200
        assert _post(client, REFRESH, body).status_code == 200  # 終われば次は通る


def test_route_reports_the_fixed_code_for_a_stale_screen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _snapshot: None
) -> None:
    _dest, body = _route_env(tmp_path, monkeypatch)
    with _app(tmp_path) as client:
        res = _post(client, REFRESH, {**body, "seq": 1})
        assert res.status_code == 409
        assert res.json()["detail"] == {"error": "stale"}


def test_route_is_404_unless_single_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _snapshot: None
) -> None:
    _dest, body = _route_env(tmp_path, monkeypatch)
    with _app(tmp_path, single_user=False) as client:
        assert _post(client, REFRESH, body).status_code == 404
        res = _post(client, RESTORE, {"at": "x"}, headers={INTENT_HEADER: INTENT_RESTORE})
        assert res.status_code == 404


def test_route_is_busy_while_another_request_holds_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _snapshot: None
) -> None:
    dest, body = _route_env(tmp_path, monkeypatch)
    with _app(tmp_path) as client:
        lock = asyncio.Lock()
        asyncio.run(lock.acquire())
        client.app.state.sample_locks[DATASET_ID] = lock
        res = _post(client, REFRESH, body)
        assert (res.status_code, res.json()["detail"]) == (409, {"error": "busy"})
    assert not (dest / "sample-backup").exists()


# ---------------------------------------------------------------------------
# summary の sample_notice（ingest 側の純関数）と、ingest の理由コードの一致


def test_overridable_reasons_match_the_held_codes() -> None:
    assert set(dataset_summary.OVERRIDABLE_REASONS) == {
        demo_sample.HELD_EDITED,
        demo_sample.HELD_DECISIONS,
    }


def test_notice_of_a_real_stamp_round_trips_through_the_summary_function(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest, cfg = _edited_design_env(tmp_path, monkeypatch)
    notice = dataset_summary.sample_notice(_read_meta(dest), is_demo=True)
    assert notice is not None
    assert notice["held"] == [{"unit": "design", "reason": "edited", "count": 1}]
    assert notice["overridable"] == ["design"]
    assert notice["restorable"] is None
    _override(cfg, dest, ["design"])
    notice = dataset_summary.sample_notice(_read_meta(dest), is_demo=True)
    assert notice is not None
    assert notice["held"] == []
    assert notice["updated"]["units"] == ["design"]
    assert notice["restorable"]["units"] == ["design"]
