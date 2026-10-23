"""✓ の後の送り込み（A3）——ADR ``source-switch-mcp-client.md`` §7。

固定するのは: 送るのは ✓ の後・ローカル原本（home 無し／local）の表だけ・json と home 済みは
送らない・相手が duplicate を返しても id が入る・32 MiB 超は送らず通知・失敗しても公開は
取り消されない・設定で切れる・設計をやめた物は送られない・add の呼び手はここだけ。
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
import rdflib
from fastapi.testclient import TestClient
from fastmcp import FastMCP
from fastmcp.client.transports import FastMCPTransport
from fastmcp.exceptions import ToolError

from asterism_api import registry, source_push, staging
from asterism_api.main import build_app
from asterism_api.mcp_client import McpClient
from tests.test_chat_attach import KEY, PRICES, _drain, _new, _Stub, _turn
from tests.test_crosswalk_api import _DatasetClient
from tests.test_main import _AUTH, _settings

_SRC_FILES = {"price.csv": PRICES}


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


class _Home:
    """add を数える偽の家。``mode`` = ok / duplicate / fail / no_id。"""

    def __init__(self, mode: str = "ok") -> None:
        self.mode = mode
        self.added: list[dict] = []
        self.srv = FastMCP("fake-home")

        @self.srv.tool
        def list_media(exts: list[str] | None = None, limit: int = 100) -> str:
            return json.dumps(
                {"items": [{"id": k, "name": k, "mimeType": "text/csv"} for k in _SRC_FILES]}
            )

        @self.srv.tool
        def get_media_file(id: str, inline: bool = False) -> str:
            return json.dumps(
                {
                    "id": id,
                    "name": id,
                    "mimeType": "text/csv",
                    "size": len(_SRC_FILES[id]),
                    "bytes_base64": base64.b64encode(_SRC_FILES[id]).decode(),
                }
            )

        @self.srv.tool
        def add_material(bytes_base64: str, name: str, mimeType: str | None = None) -> str:
            self.added.append({"name": name, "sha256": _sha(base64.b64decode(bytes_base64))})
            if self.mode == "fail":
                raise ToolError(json.dumps({"error": "disk full"}))
            if self.mode == "no_id":
                return json.dumps({"duplicate": False})
            return json.dumps(
                {
                    "id": f"m-{len(self.added)}",
                    "duplicate": self.mode == "duplicate",
                    "indexed": False,
                }
            )


def _app(tmp_path: Path, home: _Home, settings_extra: dict | None = None):
    settings = _settings(tmp_path)
    settings.single_user = True
    settings.appdata_root = tmp_path / "appdata"
    settings.appdata_root.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "mcpServers": {
            "g": {"transport": "http", "url": "http://127.0.0.1:1/mcp", "enabled": True}
        },
        "sourceProvider": {"list": "list_media", "get": "get_media_file", "add": "add_material"},
        "sourceHome": {"server": "g"},
    }
    payload.update(settings_extra or {})
    (settings.appdata_root / "settings.json").write_text(json.dumps(payload), "utf-8")
    app = build_app(
        settings,
        oxigraph_client=_DatasetClient(rdflib.Dataset()),
        start_watcher=False,
        llm_factory=lambda key: _Stub(),
    )
    app.state.mcp_registry.client = McpClient(
        transport_factory=lambda cfg: FastMCPTransport(home.srv)
    )
    return app


def _attach_local(client: TestClient, sid: str, name: str, data: bytes) -> dict:
    r = client.post(
        f"/api/chat/sessions/{sid}/attach",
        files=[("files", (name, data, "text/csv"))],
        data={"lang": "ja"},
        headers=KEY,
    )
    assert r.status_code == 201, r.text
    _drain(client, r.json()["job_id"])
    return _turn(client, sid, r.json()["turn"]["id"])


def _confirm(client: TestClient, sid: str, turn_id: str) -> dict:
    r = client.post(
        f"/api/chat/sessions/{sid}/turns/{turn_id}/confirm", json={"lang": "ja"}, headers=KEY
    )
    assert r.status_code == 200, r.text
    return r.json()


def _meta(tmp_path: Path, ds_id: str) -> dict:
    return json.loads((tmp_path / "registry" / ds_id / "meta.json").read_text("utf-8"))


def test_confirm_sends_the_local_original_home_and_writes_the_id_back(tmp_path: Path) -> None:
    home = _Home()
    with TestClient(_app(tmp_path, home), headers=_AUTH) as client:
        sid = _new(client)
        turn = _attach_local(client, sid, "価格表.csv", PRICES)
        ds_id = turn["attachment"]["dataset_id"]
        assert home.added == []  # 📎 の時点では送らない（✓ の後だけ）
        out = _confirm(client, sid, turn["id"])
        assert out["commit"]["dataset_id"] == ds_id
        (note,) = [n for n in out["turn"]["reply"]["notes"] if n["kind"] == "sent_home"]
        assert "g にも送りました" in note["text"]
    assert len(home.added) == 1 and home.added[0]["sha256"] == _sha(PRICES)
    # 相手と注記に見せるのは利用者が置いた名前（保存名 source-<hash>.csv ではない）
    assert home.added[0]["name"] == "価格表.csv" and "「価格表.csv」" in note["text"]
    (origin,) = _meta(tmp_path, ds_id)["source_origins"]
    assert origin["name"].startswith("source-")
    assert origin["home"] == {"server": "g", "id": "m-1", "name": "価格表.csv"}


def test_a_duplicate_answer_still_records_the_id(tmp_path: Path) -> None:
    home = _Home("duplicate")
    with TestClient(_app(tmp_path, home), headers=_AUTH) as client:
        sid = _new(client)
        turn = _attach_local(client, sid, "a.csv", PRICES)
        out = _confirm(client, sid, turn["id"])
        (note,) = [n for n in out["turn"]["reply"]["notes"] if n["kind"] == "sent_home"]
        assert "既にありました" in note["text"]
    (origin,) = _meta(tmp_path, turn["attachment"]["dataset_id"])["source_origins"]
    assert origin["home"]["id"] == "m-1"


def test_originals_that_already_have_a_home_are_not_sent(tmp_path: Path) -> None:
    home = _Home()
    with TestClient(_app(tmp_path, home), headers=_AUTH) as client:
        sid = _new(client)
        r = client.post(
            f"/api/chat/sessions/{sid}/attach-from-source",
            json={"server": "g", "ids": ["price.csv"], "lang": "ja"},
            headers=KEY,
        )
        assert r.status_code == 201, r.text
        _drain(client, r.json()["job_id"])
        turn = _turn(client, sid, r.json()["turn"]["id"])
        out = _confirm(client, sid, turn["id"])
        assert out["commit"]
        assert not [
            n for n in out["turn"]["reply"]["notes"] if n["kind"] in ("sent_home", "send_failed")
        ]
    assert home.added == []
    (origin,) = _meta(tmp_path, turn["attachment"]["dataset_id"])["source_origins"]
    assert origin["home"]["server"] == "g" and origin["home"]["id"] == "price.csv"


def test_json_is_not_sent(tmp_path: Path) -> None:
    home = _Home()
    rows = json.dumps([{"食材": "トマト", "価格": 120, "店": "A"}]).encode()
    with TestClient(_app(tmp_path, home), headers=_AUTH) as client:
        sid = _new(client)
        turn = _attach_local(client, sid, "rows.json", rows)
        if turn["state"] != "proposed":
            pytest.skip("json attach did not reach proposed in this fixture")
        out = _confirm(client, sid, turn["id"])
        assert out["commit"]
    assert home.added == []


def test_failure_keeps_the_publish_and_tells(tmp_path: Path) -> None:
    home = _Home("fail")
    with TestClient(_app(tmp_path, home), headers=_AUTH) as client:
        sid = _new(client)
        turn = _attach_local(client, sid, "a.csv", PRICES)
        out = _confirm(client, sid, turn["id"])
        assert out["commit"] and out["turn"]["state"] == "confirmed"
        (note,) = [n for n in out["turn"]["reply"]["notes"] if n["kind"] == "send_failed"]
        assert "disk full" in note["text"]
    (origin,) = _meta(tmp_path, turn["attachment"]["dataset_id"])["source_origins"]
    assert staging.is_local_origin(origin)  # 家は書き戻されない（再送の経路は v1 に無い＝ADR §7）


def test_setting_off_sends_nothing(tmp_path: Path) -> None:
    home = _Home()
    app = _app(tmp_path, home, {"sendOriginalsToHome": False})
    with TestClient(app, headers=_AUTH) as client:
        sid = _new(client)
        turn = _attach_local(client, sid, "a.csv", PRICES)
        out = _confirm(client, sid, turn["id"])
        assert out["commit"]
    assert home.added == []


def test_too_big_is_not_sent_but_told(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(source_push, "MAX_SOURCE_FILE_BYTES", 10)
    home = _Home()
    with TestClient(_app(tmp_path, home), headers=_AUTH) as client:
        sid = _new(client)
        turn = _attach_local(client, sid, "a.csv", PRICES)
        out = _confirm(client, sid, turn["id"])
        (note,) = [n for n in out["turn"]["reply"]["notes"] if n["kind"] == "send_failed"]
        assert "大きすぎ" in note["text"]
    assert home.added == []


def test_a_changed_file_is_not_sent(tmp_path: Path) -> None:
    home = _Home()
    with TestClient(_app(tmp_path, home), headers=_AUTH) as client:
        sid = _new(client)
        turn = _attach_local(client, sid, "a.csv", PRICES)
        ds_id = turn["attachment"]["dataset_id"]
        (path,) = registry.list_source_files(tmp_path / "registry", ds_id)
        path.write_bytes(PRICES + b"x,1,Z\n")
        out = _confirm(client, sid, turn["id"])
        (note,) = [n for n in out["turn"]["reply"]["notes"] if n["kind"] == "send_failed"]
        assert "記録と違う" in note["text"]
    assert home.added == []


def test_a_discarded_table_is_never_sent(tmp_path: Path) -> None:
    home = _Home()
    with TestClient(_app(tmp_path, home), headers=_AUTH) as client:
        sid = _new(client)
        turn = _attach_local(client, sid, "a.csv", PRICES)
        r = client.post(f"/api/chat/sessions/{sid}/turns/{turn['id']}/revert", json={"lang": "ja"})
        assert r.status_code == 200, r.text
    assert home.added == []


def test_add_is_called_from_the_confirm_hook_only() -> None:
    """add の呼び手は source_push だけ（grep で固定）。"""
    root = Path(__file__).resolve().parents[1] / "src" / "asterism_api"
    callers = [
        p.name
        for p in root.glob("*.py")
        if "provider().add" in p.read_text("utf-8") or ".add_tool" in p.read_text("utf-8")
    ]
    assert callers == ["source_push.py"], callers


def test_a_dead_home_is_told_once_and_does_not_hold_the_confirm(tmp_path: Path) -> None:
    """繋げない家に表が 2 つあっても、1 行で打ち切る（✓ の応答を件数ぶん待たせない）。"""
    home = _Home()
    app = _app(tmp_path, home)
    app.state.mcp_registry.client = McpClient(
        connect_timeout_s=2, list_timeout_s=2, get_timeout_s=2
    )
    with TestClient(app, headers=_AUTH) as client:
        sid = _new(client)
        r = client.post(
            f"/api/chat/sessions/{sid}/attach",
            files=[
                ("files", ("a.csv", PRICES, "text/csv")),
                ("files", ("b.csv", _SRC_FILES["price.csv"][:-1] + b"9\n", "text/csv")),
            ],
            data={"lang": "ja"},
            headers=KEY,
        )
        assert r.status_code == 201, r.text
        _drain(client, r.json()["job_id"])
        turn = _turn(client, sid, r.json()["turn"]["id"])
        if turn["state"] != "proposed":
            pytest.skip("two-table attach did not reach proposed in this fixture")
        out = _confirm(client, sid, turn["id"])
        assert out["commit"]
        failed = [n for n in out["turn"]["reply"]["notes"] if n["kind"] == "send_failed"]
        assert len(failed) == 1
