"""置いたファイル名の記録 — 保存名（slug 済み）と、利用者が置いた名前の対応表。

サーバは日本語だけのファイル名（価格表.csv）を保存名（source-<hash>.csv）に直す。
設計・IRI・source_files は保存名のままで、置いた名前は staging の ``names`` と
データセットの ``source_names`` にだけ残る。画面（ID の引っ越しの知らせ・
取り込みタブ・アクティビティ）はそれを引く。
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from asterism_api import staging
from asterism_api.main import _sanitize_tabular_name, build_app
from tests.test_main import (  # noqa: F401  (healthy_client is a fixture)
    _AUTH,
    _MATERIALIZE_MD_DISCONNECTED,
    _settings,
    healthy_client,
)
from tests.test_xlsx_source import _XLSX_MIME, _xlsx_bytes

# ruff: noqa: F811  — `healthy_client` is a pytest fixture reused by name.

_CSV = b"SID,composition,zt\n1,Bi2Te3,0.9\n"
_PLACED = "価格表.csv"
_SAVED = _sanitize_tabular_name(_PLACED)


def _client(tmp_path: Path, healthy_client) -> TestClient:
    app = build_app(_settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False)
    return TestClient(app, headers=_AUTH)


def _new_dataset(client: TestClient) -> str:
    return client.post(
        "/api/materialize",
        json={"proposal_md": _MATERIALIZE_MD_DISCONNECTED, "dataset_name": "thermo"},
    ).json()["dataset"]["id"]


def _meta(tmp_path: Path, ds_id: str) -> dict:
    return json.loads((tmp_path / "registry" / ds_id / "meta.json").read_text("utf-8"))


def test_staging_records_the_placed_name_only_when_it_differs(
    tmp_path: Path, healthy_client
) -> None:
    assert _SAVED != _PLACED
    with _client(tmp_path, healthy_client) as client:
        sid = client.post("/api/staging", files={"files": (_PLACED, _CSV, "text/csv")}).json()[
            "staging_id"
        ]
        sdir = staging.dir_for(tmp_path / "registry", sid)
        meta = json.loads((sdir / "meta.json").read_text("utf-8"))
        assert meta["names"] == {_SAVED: _PLACED}
        sid2 = client.post(
            "/api/staging", files={"files": ("prices.csv", _CSV, "text/csv")}
        ).json()["staging_id"]
        meta2 = json.loads(
            (staging.dir_for(tmp_path / "registry", sid2) / "meta.json").read_text("utf-8")
        )
        assert "names" not in meta2


def test_attach_from_staging_keeps_the_placed_name(tmp_path: Path, healthy_client) -> None:
    with _client(tmp_path, healthy_client) as client:
        sid = client.post("/api/staging", files={"files": (_PLACED, _CSV, "text/csv")}).json()[
            "staging_id"
        ]
        ds_id = _new_dataset(client)
        r = client.post(f"/api/datasets/{ds_id}/source", data={"staging_id": sid})
        assert r.status_code == 200, r.text
    meta = _meta(tmp_path, ds_id)
    assert meta["source_files"] == [_SAVED]
    assert meta["source_names"] == {_SAVED: _PLACED}


def test_direct_attach_keeps_the_placed_name(tmp_path: Path, healthy_client) -> None:
    with _client(tmp_path, healthy_client) as client:
        ds_id = _new_dataset(client)
        r = client.post(
            f"/api/datasets/{ds_id}/source", files={"files": (_PLACED, _CSV, "text/csv")}
        )
        assert r.status_code == 200, r.text
    assert _meta(tmp_path, ds_id)["source_names"] == {_SAVED: _PLACED}


def test_reattach_under_the_saved_name_does_not_forget_the_placed_name(
    tmp_path: Path, healthy_client
) -> None:
    with _client(tmp_path, healthy_client) as client:
        ds_id = _new_dataset(client)
        client.post(f"/api/datasets/{ds_id}/source", files={"files": (_PLACED, _CSV, "text/csv")})
        r = client.post(
            f"/api/datasets/{ds_id}/source", files={"files": (_SAVED, _CSV, "text/csv")}
        )
        assert r.status_code == 200, r.text
    assert _meta(tmp_path, ds_id)["source_names"] == {_SAVED: _PLACED}


def test_replacing_with_another_file_drops_the_old_entry(tmp_path: Path, healthy_client) -> None:
    with _client(tmp_path, healthy_client) as client:
        ds_id = _new_dataset(client)
        client.post(f"/api/datasets/{ds_id}/source", files={"files": (_PLACED, _CSV, "text/csv")})
        r = client.post(
            f"/api/datasets/{ds_id}/source", files={"files": ("prices.csv", _CSV, "text/csv")}
        )
        assert r.status_code == 200, r.text
    meta = _meta(tmp_path, ds_id)
    assert meta["source_files"] == ["prices.csv"]
    assert not meta.get("source_names")


def test_xlsx_attach_maps_each_derived_csv_to_the_workbook(
    tmp_path: Path, healthy_client
) -> None:
    data = _xlsx_bytes({"A": [["SID"], [1]], "B": [["SID"], [2]]})
    with _client(tmp_path, healthy_client) as client:
        ds_id = _new_dataset(client)
        r = client.post(
            f"/api/datasets/{ds_id}/source",
            files={"files": ("book.xlsx", data, _XLSX_MIME)},
        )
        assert r.status_code == 200, r.text
    meta = _meta(tmp_path, ds_id)
    csvs = [f for f in meta["source_files"] if f.endswith(".csv")]
    assert len(csvs) == 2
    assert {k: v for k, v in meta["source_names"].items() if k in csvs} == {
        c: "book.xlsx" for c in csvs
    }


def test_a_redesign_does_not_forget_what_the_workbook_was_called(
    tmp_path: Path, healthy_client
) -> None:
    """見直し（数え直し）は保存済みのファイルを staging に写し、もう一度 attach する。
    写しには派生 CSV と一緒に Excel の原本（保存名）も入っていて、attach はそれを
    変換し直す。原本は保存名で届くので、その名前で「置いた名前」を上書きしてはいけない
    —— 上書きすると、次の知らせが「source-xxxx.xlsx」と言い出す。"""
    from tests.test_id_move import _SPEC

    data = _xlsx_bytes({"A": [["sid", "name"], [1, "Bi2Te3"]]})
    with _client(tmp_path, healthy_client) as client:
        sid = client.post(
            "/api/staging", files={"files": ("価格表.xlsx", data, _XLSX_MIME)}
        ).json()["staging_id"]
        ds_id = _new_dataset(client)
        attached = client.post(f"/api/datasets/{ds_id}/source", data={"staging_id": sid})
        assert attached.status_code == 200, attached.text
        first = _meta(tmp_path, ds_id)["source_names"]
        assert first and set(first.values()) == {"価格表.xlsx"}

        # 数え直しは Mapping IR のある設計にだけ開く。
        (tmp_path / "registry" / ds_id / "mapping.yaml").write_text(_SPEC, encoding="utf-8")
        recount = client.post(f"/api/datasets/{ds_id}/recount")
        assert recount.status_code == 200, recount.text
        again = client.post(
            f"/api/datasets/{ds_id}/source", data={"staging_id": recount.json()["staging_id"]}
        )
        assert again.status_code == 200, again.text
    assert _meta(tmp_path, ds_id)["source_names"] == first


def test_the_workbook_itself_answers_to_its_placed_name(tmp_path: Path, healthy_client) -> None:
    """Excel の原本は source/ に残り、あとで一覧に載る（形を直したあとの読み直し・
    追記のあと）。そのとき原本だけが保存名で出ないよう、原本の名前も覚えておく。"""
    from asterism_api import registry

    data = _xlsx_bytes({"A": [["sid", "name"], [1, "Bi2Te3"]]})
    with _client(tmp_path, healthy_client) as client:
        ds_id = _new_dataset(client)
        r = client.post(
            f"/api/datasets/{ds_id}/source",
            files={"files": ("価格表.xlsx", data, _XLSX_MIME)},
        )
        assert r.status_code == 200, r.text
    root = tmp_path / "registry"
    listed = [p.name for p in registry.list_source_files(root, ds_id)]
    book = next(n for n in listed if n.endswith(".xlsx"))
    assert book != "価格表.xlsx"  # 原本も保存名で置いてある
    # 一覧を読み直して書き直す（attach の中の reshape・追記がやること）。
    meta = registry.mark_source_saved(root, ds_id, listed)
    assert set(meta["source_files"]) == set(listed)
    assert meta["source_names"] == dict.fromkeys(listed, "価格表.xlsx")


def test_redesigning_an_older_workbook_dataset_records_nothing(
    tmp_path: Path, healthy_client
) -> None:
    """この仕組みより前に作ったデータセットは、置いた名前を持たない。見直しで
    保存済みの組（派生 CSV と原本）を入れ直しても、保存名を「置いた名前」として
    書き込まない —— 分からないものは、分からないままにする。"""
    from tests.test_id_move import _SPEC

    data = _xlsx_bytes({"A": [["sid", "name"], [1, "Bi2Te3"]]})
    with _client(tmp_path, healthy_client) as client:
        ds_id = _new_dataset(client)
        r = client.post(
            f"/api/datasets/{ds_id}/source",
            files={"files": ("価格表.xlsx", data, _XLSX_MIME)},
        )
        assert r.status_code == 200, r.text
        # 前の版で作られた状態にする: 対応表が無い。
        meta_path = tmp_path / "registry" / ds_id / "meta.json"
        older = json.loads(meta_path.read_text("utf-8"))
        older.pop("source_names")
        meta_path.write_text(json.dumps(older, ensure_ascii=False), "utf-8")

        (tmp_path / "registry" / ds_id / "mapping.yaml").write_text(_SPEC, encoding="utf-8")
        recount = client.post(f"/api/datasets/{ds_id}/recount")
        assert recount.status_code == 200, recount.text
        again = client.post(
            f"/api/datasets/{ds_id}/source", data={"staging_id": recount.json()["staging_id"]}
        )
        assert again.status_code == 200, again.text
    assert "source_names" not in _meta(tmp_path, ds_id)


def test_staging_names_a_workbooks_tables_after_the_workbook(
    tmp_path: Path, healthy_client
) -> None:
    """staging を読む側（置く経路など）が見るのは派生した表だけ。表の名前からも
    ブックの置いた名前が引けるように、両方を記録する。"""
    data = _xlsx_bytes({"A": [["sid"], [1]], "B": [["sid"], [2]]})
    with _client(tmp_path, healthy_client) as client:
        body = client.post(
            "/api/staging", files={"files": ("価格表.xlsx", data, _XLSX_MIME)}
        ).json()
    sdir = staging.dir_for(tmp_path / "registry", body["staging_id"])
    names = json.loads((sdir / "meta.json").read_text("utf-8"))["names"]
    assert len(body["sources"]) == 2
    assert {names[csv] for csv in body["sources"]} == {"価格表.xlsx"}
    (raw,) = [p.name for p in (sdir / "raw").iterdir()]
    assert names[raw] == "価格表.xlsx"


def test_id_move_adds_source_label_without_touching_the_record(
    tmp_path: Path, healthy_client
) -> None:
    record = {
        "changes_ids": True,
        "fully_movable": False,
        "blocked": [
            {"name": "record", "source": _SAVED, "reason": "missing_columns",
             "missing_columns": ["番号"]}
        ],
        "moved": [{"name": "shop", "source": _SAVED}],
    }
    root = tmp_path / "registry"
    (root / "dataset-x").mkdir(parents=True)
    meta = {"id": "dataset-x", "name": "ZEM", "id_move": record, "source_names": {_SAVED: _PLACED}}
    (root / "dataset-x" / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), "utf-8")
    with _client(tmp_path, healthy_client) as client:
        body = client.get("/api/datasets/dataset-x/id-move").json()
        assert body["blocked"][0]["source_label"] == _PLACED
        assert body["moved"][0]["source_label"] == _PLACED
        # 名前の対応が無ければ付けない
        meta.pop("source_names")
        (root / "dataset-x" / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), "utf-8")
        body = client.get("/api/datasets/dataset-x/id-move").json()
        assert "source_label" not in body["blocked"][0]
        assert "source_label" not in body["moved"][0]
    assert _meta(tmp_path, "dataset-x")["id_move"] == record


def test_jobs_adds_file_label_for_a_placed_name(tmp_path: Path, healthy_client) -> None:
    root = tmp_path / "registry"
    (root / "dataset-x").mkdir(parents=True)
    book = {"source-a5e26419.csv": "在庫.xlsx", "source-a5e26419.xlsx": "在庫.xlsx"}
    (root / "dataset-x" / "meta.json").write_text(
        json.dumps(
            {"id": "dataset-x", "source_names": {_SAVED: _PLACED, **book}}, ensure_ascii=False
        ),
        "utf-8",
    )
    s = _settings(tmp_path)
    s.jobs_log.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"kind": "ingest", "dataset_id": "dataset-x", "file": _SAVED},
        {"kind": "ingest", "dataset_id": "dataset-x", "file": f"other.csv, {_SAVED}"},
        {"kind": "ingest", "dataset_id": "dataset-x", "file": "other.csv"},
        {"kind": "ingest", "dataset_id": "dataset-x", "file": ", ".join(book)},
    ]
    s.jobs_log.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    app = build_app(s, oxigraph_client=healthy_client, start_watcher=False)
    with TestClient(app, headers=_AUTH) as client:
        jobs = client.get("/jobs").json()["jobs"]
    assert jobs[0]["file_label"] == _PLACED
    assert jobs[1]["file_label"] == f"other.csv, {_PLACED}"
    assert "file_label" not in jobs[2]
    assert jobs[3]["file_label"] == "在庫.xlsx"  # ブックとその表は同じ名前 — 1 回だけ
    assert jobs[0]["file"] == _SAVED  # ログ自体は書き換えない
