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
    DATASET_ID,
    SNAPSHOT,
    _cfg,
    _DatasetClient,
    _meta,
    _Projections,
    _read_meta,
    _real,
    _refresh,
    _write_env,
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
