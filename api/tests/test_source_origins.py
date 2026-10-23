"""取り込み元の切り替え（A2）——ADR ``source-switch-mcp-client.md`` §5〜§6。

固定するのは **原本の記録**: staging は raw の bytes から自分で sha256 を計算して
``origins`` に書き（呼び手の申告は信じない）、attach がそれを dataset の
``source_origins`` に写し、再 attach では置き換え、recount→再 attach では残り、
``mark_source_saved`` の再計算でも消えない。被りは名前でなく中身で見つかる。
📎 の ``attach-from-source`` は MCP の取り込み元から bytes を取って同じ走者に流す。
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

import rdflib
from fastapi.testclient import TestClient
from fastmcp import FastMCP
from fastmcp.client.transports import FastMCPTransport

from asterism_api import registry, staging
from asterism_api.main import _sanitize_tabular_name, build_app
from asterism_api.mcp_client import McpClient
from tests.test_chat_attach import KEY, PRICES, _drain, _new, _Stub, _turn
from tests.test_crosswalk_api import _DatasetClient
from tests.test_main import (  # noqa: F401  (healthy_client is a fixture)
    _AUTH,
    _MATERIALIZE_MD_DISCONNECTED,
    _settings,
    healthy_client,
)
from tests.test_xlsx_source import _XLSX_MIME, _xlsx_bytes

# ruff: noqa: F811  — `healthy_client` is a pytest fixture reused by name.

_CSV = b"SID,composition,zt\n1,Bi2Te3,0.9\n"
_CSV2 = b"SID,composition,zt\n2,PbTe,0.7\n"
_SPEC = """version: 1
prefixes:
  ex: "https://e.invalid/ns#"
  exr: "https://e.invalid/r/"
maps:
  - name: sample
    source: a.csv
    subject:
      template: "exr:sample/{SID}"
      classes: [ex:Sample]
    properties:
      - predicate: ex:name
        column: composition
"""


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _client(tmp_path: Path, healthy_client) -> TestClient:
    app = build_app(_settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False)
    return TestClient(app, headers=_AUTH)


def _new_dataset(client: TestClient, name: str = "thermo") -> str:
    return client.post(
        "/api/materialize",
        json={"proposal_md": _MATERIALIZE_MD_DISCONNECTED, "dataset_name": name},
    ).json()["dataset"]["id"]


def _meta(tmp_path: Path, ds_id: str) -> dict:
    return json.loads((tmp_path / "registry" / ds_id / "meta.json").read_text("utf-8"))


def _stage(client: TestClient, files: list[tuple[str, bytes]], homes: dict | None = None) -> dict:
    data = {"source_homes": json.dumps(homes)} if homes is not None else {}
    r = client.post(
        "/api/staging", files=[("files", (n, b, "text/csv")) for n, b in files], data=data
    )
    assert r.status_code == 200, r.text
    return r.json()


# ---- staging の origins ------------------------------------------------------------------


def test_staging_hashes_the_bytes_on_disk_and_records_the_home(
    tmp_path: Path, healthy_client
) -> None:
    home = {"server": "graphium", "id": "f1", "name": "price.csv", "extra": "dropped"}
    with _client(tmp_path, healthy_client) as client:
        body = _stage(client, [("価格表.csv", _CSV), ("b.csv", _CSV2)], {"価格表.csv": home})
    saved = _sanitize_tabular_name("価格表.csv")
    assert body["origins"] == [
        {"name": "b.csv", "sha256": _sha(_CSV2), "home": {"server": "local"}},
        {
            "name": saved,
            "sha256": _sha(_CSV),
            "home": {"server": "graphium", "id": "f1", "name": "price.csv"},
        },
    ]
    assert body["duplicates"] == []
    # meta.json: 既存の sources（表名の配列）はそのまま・origins は別キー
    sdir, _ = staging.load(tmp_path / "registry", body["staging_id"])
    meta = json.loads((sdir / "meta.json").read_text("utf-8"))
    assert meta["sources"] == [saved, "b.csv"] or set(meta["sources"]) == {saved, "b.csv"}
    assert meta["origins"] == body["origins"]


def test_a_plain_drop_records_a_local_origin(tmp_path: Path, healthy_client) -> None:
    with _client(tmp_path, healthy_client) as client:
        body = _stage(client, [("a.csv", _CSV)])
    assert body["origins"] == [{"name": "a.csv", "sha256": _sha(_CSV), "home": {"server": "local"}}]


def test_malformed_source_homes_is_400(tmp_path: Path, healthy_client) -> None:
    with _client(tmp_path, healthy_client) as client:
        for bad in ("not json", "[1]", json.dumps({"a.csv": "graphium"})):
            r = client.post(
                "/api/staging",
                files=[("files", ("a.csv", _CSV, "text/csv"))],
                data={"source_homes": bad},
            )
            assert r.status_code == 400, bad


def test_xlsx_origin_is_the_workbook_and_does_not_mix_with_sheet_provenance(
    tmp_path: Path, healthy_client
) -> None:
    """``create_staging`` のローカル変数 ``origins``（派生 CSV→ブック名）と混ざらない。"""
    book = _xlsx_bytes({"A": [["sid", "name"], [1, "Bi2Te3"]]})
    with _client(tmp_path, healthy_client) as client:
        r = client.post("/api/staging", files=[("files", ("book.xlsx", book, _XLSX_MIME))])
        assert r.status_code == 200, r.text
        body = r.json()
    assert [o["name"] for o in body["origins"]] == ["book.xlsx"]
    assert body["origins"][0]["sha256"] == _sha(book)
    assert all(s.endswith(".csv") for s in body["sources"])  # 設計の表は派生 CSV のまま


# ---- dataset の source_origins ---------------------------------------------------------


def test_attach_copies_origins_and_a_reattach_replaces_them(tmp_path: Path, healthy_client) -> None:
    with _client(tmp_path, healthy_client) as client:
        ds_id = _new_dataset(client)
        sid = _stage(
            client, [("a.csv", _CSV)], {"a.csv": {"server": "g", "id": "f1", "name": "a.csv"}}
        )
        r = client.post(f"/api/datasets/{ds_id}/source", data={"staging_id": sid["staging_id"]})
        assert r.status_code == 200, r.text
        meta = _meta(tmp_path, ds_id)
        assert meta["source_origins"] == [
            {
                "name": "a.csv",
                "sha256": _sha(_CSV),
                "home": {"server": "g", "id": "f1", "name": "a.csv"},
            }
        ]
        # mark_source_saved の再計算（source_files の更新）では消えない
        registry.mark_source_saved(tmp_path / "registry", ds_id, ["a.csv"])
        assert _meta(tmp_path, ds_id)["source_origins"] == meta["source_origins"]

        # 再 attach（別の staging）は置き換える
        sid2 = _stage(client, [("b.csv", _CSV2)])
        r = client.post(f"/api/datasets/{ds_id}/source", data={"staging_id": sid2["staging_id"]})
        assert r.status_code == 200, r.text
        assert _meta(tmp_path, ds_id)["source_origins"] == [
            {"name": "b.csv", "sha256": _sha(_CSV2), "home": {"server": "local"}}
        ]
        # staging を介さない直接の attach は、記録が実物を表さなくなるので空にする
        r = client.post(
            f"/api/datasets/{ds_id}/source", files={"files": ("c.csv", _CSV, "text/csv")}
        )
        assert r.status_code == 200, r.text
        assert _meta(tmp_path, ds_id)["source_origins"] == []


def test_recount_then_reattach_keeps_the_origins(tmp_path: Path, healthy_client) -> None:
    home = {"server": "g", "id": "f1", "name": "a.csv"}
    with _client(tmp_path, healthy_client) as client:
        ds_id = _new_dataset(client)
        sid = _stage(client, [("a.csv", _CSV)], {"a.csv": home})
        client.post(f"/api/datasets/{ds_id}/source", data={"staging_id": sid["staging_id"]})
        # 数えかたを開き直すには設計（Mapping IR）が要る。中身は問わない
        (tmp_path / "registry" / ds_id / "mapping.yaml").write_text(_SPEC, encoding="utf-8")
        r = client.post(f"/api/datasets/{ds_id}/recount")
        assert r.status_code == 200, r.text
        sdir, _ = staging.load(tmp_path / "registry", r.json()["staging_id"])
        smeta = json.loads((sdir / "meta.json").read_text("utf-8"))
        assert smeta["origins"] == [{"name": "a.csv", "sha256": _sha(_CSV), "home": home}]
        r = client.post(
            f"/api/datasets/{ds_id}/source", data={"staging_id": r.json()["staging_id"]}
        )
        assert r.status_code == 200, r.text
    assert _meta(tmp_path, ds_id)["source_origins"] == [
        {"name": "a.csv", "sha256": _sha(_CSV), "home": home}
    ]


def test_duplicates_are_found_by_content_not_by_name(tmp_path: Path, healthy_client) -> None:
    with _client(tmp_path, healthy_client) as client:
        ds_id = _new_dataset(client, "first")
        sid = _stage(client, [("a.csv", _CSV)])
        client.post(f"/api/datasets/{ds_id}/source", data={"staging_id": sid["staging_id"]})
        again = _stage(client, [("renamed.csv", _CSV), ("other.csv", _CSV2)])
    assert again["duplicates"] == [
        {
            "name": "renamed.csv",
            "sha256": _sha(_CSV),
            "dataset_id": ds_id,
            "dataset_name": "first",
            "source_name": "a.csv",
        }
    ]


# ---- 📎 attach-from-source ----------------------------------------------------------------


def _fake_source(files: dict[str, bytes]) -> FastMCP:
    srv = FastMCP("fake-source")

    @srv.tool
    def list_media(exts: list[str] | None = None, limit: int = 100) -> str:
        return json.dumps(
            {
                "items": [
                    {"id": k, "name": k, "mimeType": "text/csv", "size": len(v)}
                    for k, v in files.items()
                ]
            }
        )

    @srv.tool
    def get_media_file(id: str, inline: bool = False) -> str:
        if id not in files:
            from fastmcp.exceptions import ToolError

            raise ToolError(json.dumps({"error": "no such file"}))
        return json.dumps(
            {
                "id": id,
                "name": id,
                "mimeType": "text/csv",
                "size": len(files[id]),
                "bytes_base64": base64.b64encode(files[id]).decode(),
            }
        )

    return srv


def _chat_app(tmp_path: Path, llm: Any, files: dict[str, bytes]):
    settings = _settings(tmp_path)
    settings.single_user = True
    settings.appdata_root = tmp_path / "appdata"
    settings.appdata_root.mkdir(parents=True, exist_ok=True)
    (settings.appdata_root / "settings.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "g": {"transport": "http", "url": "http://127.0.0.1:1/mcp", "enabled": True},
                    "off": {"transport": "http", "url": "http://127.0.0.1:1/mcp", "enabled": False},
                },
                "sourceProvider": {"list": "list_media", "get": "get_media_file"},
            }
        ),
        "utf-8",
    )
    app = build_app(
        settings,
        oxigraph_client=_DatasetClient(rdflib.Dataset()),
        start_watcher=False,
        llm_factory=lambda key: llm,
    )
    srv = _fake_source(files)
    app.state.mcp_registry.client = McpClient(transport_factory=lambda cfg: FastMCPTransport(srv))
    return app


def _from_source(client: TestClient, sid: str, server: str, ids: list[str]):
    return client.post(
        f"/api/chat/sessions/{sid}/attach-from-source",
        json={"server": server, "ids": ids, "lang": "ja"},
        headers=KEY,
    )


def test_attach_from_source_runs_the_same_walker_and_records_the_home(tmp_path: Path) -> None:
    app = _chat_app(tmp_path, _Stub(), {"price.csv": PRICES})
    with TestClient(app, headers=_AUTH) as client:
        sid = _new(client)
        r = _from_source(client, sid, "g", ["price.csv"])
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["turn"]["user"]["attachments"] == [{"name": "price.csv", "size": len(PRICES)}]
        _drain(client, body["job_id"])
        turn = _turn(client, sid, body["turn"]["id"])
        assert turn["state"] == "proposed", turn
        ds_id = turn["attachment"]["dataset_id"]
        assert turn["attach_run"]["homes"] == {
            "price.csv": {"server": "g", "id": "price.csv", "name": "price.csv"}
        }
    meta = _meta(tmp_path, ds_id)
    assert meta["source_origins"] == [
        {
            "name": "price.csv",
            "sha256": _sha(PRICES),
            "home": {"server": "g", "id": "price.csv", "name": "price.csv"},
        }
    ]


def test_attach_from_source_refuses_what_the_browser_path_refuses(tmp_path: Path) -> None:
    app = _chat_app(tmp_path, _Stub(), {"price.csv": PRICES, "note.txt": b"hi"})
    with TestClient(app, headers=_AUTH) as client:
        sid = _new(client)
        assert _from_source(client, sid, "g", []).status_code == 422
        assert _from_source(client, sid, "g", ["note.txt"]).status_code == 422  # 表でない
        assert _from_source(client, sid, "off", ["price.csv"]).status_code == 409  # 未有効
        assert _from_source(client, sid, "nope", ["price.csv"]).status_code == 404
        assert _from_source(client, sid, "g", ["missing.csv"]).status_code == 502
        r = client.post(
            f"/api/chat/sessions/{sid}/attach-from-source",
            json={"server": "g", "ids": ["price.csv"]},
            headers={},  # AI 無し
        )
        assert r.status_code == 503
        # 何も手番が増えていない
        assert client.get(f"/api/chat/sessions/{sid}").json()["turns"] == []


def test_attach_from_source_is_404_outside_single_user(tmp_path: Path) -> None:
    app = build_app(
        _settings(tmp_path),
        oxigraph_client=_DatasetClient(rdflib.Dataset()),
        start_watcher=False,
        llm_factory=lambda key: _Stub(),
    )
    with TestClient(app, headers=_AUTH) as client:
        sid = _new(client)
        assert _from_source(client, sid, "g", ["price.csv"]).status_code == 404


def test_a_second_drop_of_the_same_bytes_is_told_in_the_reply(tmp_path: Path) -> None:
    app = _chat_app(tmp_path, _Stub(), {"price.csv": PRICES, "copy.csv": PRICES})
    with TestClient(app, headers=_AUTH) as client:
        sid = _new(client)
        first = _from_source(client, sid, "g", ["price.csv"]).json()
        _drain(client, first["job_id"])
        t1 = _turn(client, sid, first["turn"]["id"])
        assert not [n for n in t1["reply"]["notes"] if n["kind"] == "duplicate_source"]
        second = _from_source(client, sid, "g", ["copy.csv"]).json()
        _drain(client, second["job_id"])
        t2 = _turn(client, sid, second["turn"]["id"])
        assert t2["state"] == "proposed"  # 設計は続く
        (note,) = [n for n in t2["reply"]["notes"] if n["kind"] == "duplicate_source"]
        assert note["dataset_id"] == t1["attachment"]["dataset_id"]
        assert note["choice"] is None  # 未回答の選択と見なされて「公開」が隠れないように
        assert "既にデータセット" in note["text"] and "copy.csv" in note["text"]


def test_xlsx_origin_survives_the_attach(tmp_path: Path, healthy_client) -> None:
    """レビュー（2026-10-23・高）: ブックの原本は saved に載らないが source/ に同居する。
    突き合わせは saved でなく source/ の実物で。"""
    book = _xlsx_bytes({"A": [["sid", "name"], [1, "Bi2Te3"]]})
    with _client(tmp_path, healthy_client) as client:
        ds_id = _new_dataset(client)
        r = client.post("/api/staging", files=[("files", ("book.xlsx", book, _XLSX_MIME))])
        sid = r.json()["staging_id"]
        r = client.post(f"/api/datasets/{ds_id}/source", data={"staging_id": sid})
        assert r.status_code == 200, r.text
    meta = _meta(tmp_path, ds_id)
    assert meta["source_origins"] == [
        {"name": "book.xlsx", "sha256": _sha(book), "home": {"server": "local"}}
    ]
    assert (tmp_path / "registry" / ds_id / "source" / "book.xlsx").is_file()


def test_attach_from_source_numbering_never_collides(tmp_path: Path) -> None:
    app = _chat_app(tmp_path, _Stub(), {"a": PRICES})
    # 偽サーバの get は name=id を返す。番号付きの名前が既存名とぶつかる並びを用意する
    srv = _fake_source({"x.csv": PRICES, "x-2.csv": PRICES, "y.csv": PRICES})
    app.state.mcp_registry.client = McpClient(transport_factory=lambda cfg: FastMCPTransport(srv))
    with TestClient(app, headers=_AUTH) as client:
        sid = _new(client)
        r = _from_source(client, sid, "g", ["x.csv", "x-2.csv", "y.csv"])
        assert r.status_code == 201, r.text
        names = [a["name"] for a in r.json()["turn"]["user"]["attachments"]]
        assert len(set(names)) == 3
