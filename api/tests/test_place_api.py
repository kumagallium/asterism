"""Tests for the 「データを置く」api (object-cards-ui.md §4 / 契約メモ §4。担当 C1-place)。

``register_place`` は main.py にまだ配線されていない（§0.1: 配線は統合段の仕事）
ので、ここでは契約メモの通り ``build_app(settings, ...)`` の後に自分で
``register_place(app, settings)`` を呼んでから ``TestClient(app)`` を作る。

フィクスチャは架空の「庭の植物台帳」ドメイン（ingest/tests/test_shape_match.py の
図書館・動物ドメインとは別の 3 つ目の架空分野）— 材料科学の語彙は一切出てこない。

commit の材料化 (materialize) → 取り込み (ingest) → 昇格 (promote) は既存の経路を
順に呼ぶだけという契約なので、その 3 つの内部関数を monkeypatch して呼び順と
``meta.origin`` だけを固定する（実 ingest の中身は api/tests/test_ingest.py の
既存 e2e に任せる — 契約メモ §4.4）。
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from asterism.oxigraph_client import OxigraphClient, OxigraphConfig
from fastapi.testclient import TestClient

from asterism_api import place_routes, registry
from asterism_api.main import build_app
from tests.test_main import _AUTH, _settings, healthy_client  # noqa: F401 (fixture)

# ruff: noqa: F811  — `healthy_client` is a pytest fixture reused by name.

_EX_GARDEN = "https://ex/garden#"
_PLANT_CLASS = _EX_GARDEN + "Plant"
_CODE_PRED = _EX_GARDEN + "code"
_PLANT_RESOURCE = "https://ex/garden/resource/plant/"

_GARDEN_MAPPING_YAML = f"""
version: 1
prefixes:
  ex: "{_EX_GARDEN}"
  exr: "https://ex/garden/resource/"
  xsd: "http://www.w3.org/2001/XMLSchema#"
maps:
  - name: plant
    source: plants.csv
    subject:
      template: "exr:plant/{{code}}"
      classes: [ex:Plant]
    properties:
      - predicate: ex:code
        column: code
        label: "整理番号"
      - predicate: ex:name
        column: name
      - predicate: ex:height
        column: height_cm
        datatype: xsd:double
        unit: "cm"
"""

_PLANTS_CSV = b"code,name,height_cm\nP-01,Rose,32.5\nP-02,Tulip,18.0\n"


def _write_garden_dataset(registry_root: Path, dataset_id: str = "garden-aaaaaaaa") -> None:
    dest = registry_root / dataset_id
    dest.mkdir(parents=True)
    (dest / "meta.json").write_text(
        json.dumps({"id": dataset_id, "promoted": True}), encoding="utf-8"
    )
    (dest / "mapping.yaml").write_text(_GARDEN_MAPPING_YAML, encoding="utf-8")


def _build(tmp_path: Path, client: OxigraphClient):
    settings = _settings(tmp_path)
    _write_garden_dataset(settings.registry_root)
    app = build_app(settings, oxigraph_client=client, start_watcher=False)
    place_routes.register_place(app, settings)
    return app, settings


def _stage(client: TestClient, csv: bytes = _PLANTS_CSV, name: str = "plants.csv") -> str:
    r = client.post("/api/staging", files={"files": (name, csv, "text/csv")})
    assert r.status_code == 200, r.text
    return r.json()["staging_id"]


# ---------------------------------------------------------------------------
# POST /api/place/inspect
# ---------------------------------------------------------------------------


def test_place_inspect_matches_known_shape(tmp_path: Path, healthy_client: OxigraphClient) -> None:
    app, _cfg = _build(tmp_path, healthy_client)
    with TestClient(app, headers=_AUTH) as client:
        sid = _stage(client)
        r = client.post("/api/place/inspect", json={"staging_id": sid})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["files"] == [
            {"name": "plants.csv", "columns": ["code", "name", "height_cm"], "rows": 2}
        ]
        match = body["match"]
        assert match["type_id"] == _PLANT_CLASS
        assert set(match["matched_columns"]) == {"code", "name", "height_cm"}
        assert match["unmatched_columns"] == []
        assert match["confidence"] == 1.0
        assert match["dialect"]  # 何かしらの文字列（既定でも "default"）
        assert body["signature_dataset_id"] == "garden-aaaaaaaa"
        assert isinstance(body["signature_label"], str) and body["signature_label"]


def test_place_inspect_no_match_returns_null_type_id(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    app, _cfg = _build(tmp_path, healthy_client)
    with TestClient(app, headers=_AUTH) as client:
        sid = _stage(client, csv=b"foo,bar\n1,2\n", name="unrelated.csv")
        r = client.post("/api/place/inspect", json={"staging_id": sid})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["match"]["type_id"] is None
        assert body["signature_label"] is None
        assert body["signature_dataset_id"] is None


def test_place_inspect_requires_staging_or_dataset_id(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    app, _cfg = _build(tmp_path, healthy_client)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post("/api/place/inspect", json={})
        assert r.status_code == 400


def test_place_inspect_unknown_staging_id_is_404(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    app, _cfg = _build(tmp_path, healthy_client)
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/place/inspect", json={"staging_id": "11111111-1111-4111-8111-111111111111"}
        )
        assert r.status_code == 404


def test_place_inspect_requires_write_auth(tmp_path: Path, healthy_client: OxigraphClient) -> None:
    app, _cfg = _build(tmp_path, healthy_client)
    with TestClient(app) as client:  # ヘッダなし
        r = client.post("/api/place/inspect", json={"dataset_id": "garden-aaaaaaaa"})
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/place/subjects — 3 状態（linked / ambiguous / own_only）
# ---------------------------------------------------------------------------


def _uri(value: str) -> dict[str, str]:
    return {"type": "uri", "value": value}


def _lit(value: str) -> dict[str, str]:
    return {"type": "literal", "value": value}


def _bindings(rows: list[dict[str, dict[str, str]]]) -> str:
    return json.dumps({"head": {}, "results": {"bindings": rows}})


def _subjects_store_client() -> OxigraphClient:
    """P-01 は棚に 1 件（linked）。P-02 は存在しない（own_only）— match_subjects の
    key_predicate 経路が投げる SPARQL の形に沿って応答を作り分ける（test_prov_graph_api.py
    と同じ流儀）。"""
    rose_iri = _PLANT_RESOURCE + "rose-1"

    def handler(request: httpx.Request) -> httpx.Response:
        body = (request.content or b"").decode("utf-8")
        headers = {"content-type": "application/sparql-results+json"}
        if f"<{_CODE_PRED}>" in body and "?s ?v" in body:
            # match_subjects の value-scan クエリ: ?s <code> ?v
            rows = [{"s": _uri(rose_iri), "v": _lit("P-01")}]
            return httpx.Response(200, text=_bindings(rows), headers=headers)
        if "GROUP BY" in body:
            # _subject_triple_counts
            rows = [{"s": _uri(rose_iri), "n": _lit("2")}]
            return httpx.Response(200, text=_bindings(rows), headers=headers)
        return httpx.Response(200, text=_bindings([]), headers=headers)

    inner = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
    return OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner)


def test_place_subjects_three_states(tmp_path: Path) -> None:
    app, _cfg = _build(tmp_path, _subjects_store_client())
    with TestClient(app, headers=_AUTH) as client:
        sid = _stage(client)
        r = client.post(
            "/api/place/subjects", json={"staging_id": sid, "type_id": _PLANT_CLASS}
        )
        assert r.status_code == 200, r.text
        items = {it["value"]: it for it in r.json()["items"]}
        assert items["P-01"]["match"] == "linked"
        assert items["P-01"]["iri"] == _PLANT_RESOURCE + "rose-1"
        assert items["P-01"]["rows"] == 1
        assert items["P-02"]["match"] == "own_only"
        assert "iri" not in items["P-02"]
        assert items["P-02"]["rows"] == 1


def test_place_subjects_unknown_type_id_is_404(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    app, _cfg = _build(tmp_path, healthy_client)
    with TestClient(app, headers=_AUTH) as client:
        sid = _stage(client)
        r = client.post(
            "/api/place/subjects",
            json={"staging_id": sid, "type_id": "https://ex/garden#NoSuchClass"},
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/place/commit — materialize → ingest → promote の呼び順・origin=own
# ---------------------------------------------------------------------------


def test_place_commit_calls_materialize_then_ingest_then_promote_in_order(
    tmp_path: Path, healthy_client: OxigraphClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, cfg = _build(tmp_path, healthy_client)
    call_order: list[str] = []
    committed_id: dict[str, str] = {}

    def fake_materialize_own(
        settings, *, dataset_name, signature, source_path, source_name, columns
    ):
        call_order.append("materialize")
        assert dataset_name == "私の庭"
        assert signature.type_id == _PLANT_CLASS
        assert source_name == "plants.csv"
        # 既存 materialize と同じ形の meta を返す ── 以降の commit の処理
        # (source_dir の解決・mark_source_saved) が本物のデータセットとして進める。
        meta = registry.save_dataset(
            settings.registry_root,
            dataset_name,
            {},
            complete=True,
            warnings=[],
            traps=[],
            exit_code=0,
            created_at="2024-01-01T00:00:00Z",
            proposal_md="",
        )
        committed_id["id"] = str(meta["id"])
        return meta

    async def fake_ingest_own(fastapi_app, dataset_id):
        call_order.append("ingest")
        assert dataset_id == committed_id["id"]
        # 実データセットとして存在していることを確かめる（materialize が先に走った証拠）。
        assert registry.load_dataset(cfg.registry_root, dataset_id) is not None
        return {"job_id": "job-fake-1"}

    async def fake_promote_own(fastapi_app, dataset_id):
        call_order.append("promote")
        assert dataset_id == committed_id["id"]
        # ingest が先に呼ばれていることを呼び順で確認する。
        assert call_order == ["materialize", "ingest", "promote"]
        return {"promoted": True}

    monkeypatch.setattr(place_routes, "_materialize_own", fake_materialize_own)
    monkeypatch.setattr(place_routes, "_ingest_own", fake_ingest_own)
    monkeypatch.setattr(place_routes, "_promote_own", fake_promote_own)

    with TestClient(app, headers=_AUTH) as client:
        sid = _stage(client)
        r = client.post(
            "/api/place/commit",
            json={
                "staging_id": sid,
                "type_id": _PLANT_CLASS,
                "choices": {},
                "name": "私の庭",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()

    assert call_order == ["materialize", "ingest", "promote"]
    assert body["dataset_id"] == committed_id["id"]
    assert body["job_id"] == "job-fake-1"

    # ④ meta.origin = 'own' が書かれている。
    meta = registry.load_dataset(cfg.registry_root, committed_id["id"])["meta"]
    assert meta["origin"] == "own"

    # subjects: own_only (棚に何も無い) と set は §4.3 の形の通り。
    subjects = {s["label"]: s for s in body["subjects"]}
    assert subjects["P-01"]["source"] == "own"
    assert subjects["P-01"]["kind"] == "individual"
    assert subjects["P-01"]["subject_key"].startswith("i:")
    assert body["set"]["spec"]["class"] == _PLANT_CLASS
    assert body["set"]["spec"]["source_scope"] == "own"
    assert body["set"]["set_id"].startswith("set-")


def test_place_commit_choices_override_match_state(
    tmp_path: Path, healthy_client: OxigraphClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """行ごとの ``choices`` (人間が候補を選んだ／own_only にした) が最終結果を上書きする。"""
    app, _cfg = _build(tmp_path, healthy_client)

    def fake_materialize_own(
        settings, *, dataset_name, signature, source_path, source_name, columns
    ):
        return registry.save_dataset(
            settings.registry_root,
            dataset_name,
            {},
            complete=True,
            warnings=[],
            traps=[],
            exit_code=0,
            created_at="2024-01-01T00:00:00Z",
            proposal_md="",
        )

    async def fake_ingest_own(fastapi_app, dataset_id):
        return {"job_id": "job-fake-2"}

    async def fake_promote_own(fastapi_app, dataset_id):
        return {}

    monkeypatch.setattr(place_routes, "_materialize_own", fake_materialize_own)
    monkeypatch.setattr(place_routes, "_ingest_own", fake_ingest_own)
    monkeypatch.setattr(place_routes, "_promote_own", fake_promote_own)

    with TestClient(app, headers=_AUTH) as client:
        sid = _stage(client)
        chosen_iri = _PLANT_RESOURCE + "chosen-rose"
        r = client.post(
            "/api/place/commit",
            json={
                "staging_id": sid,
                "type_id": _PLANT_CLASS,
                "choices": {"P-01": chosen_iri},
                "name": "選び直した庭",
            },
        )
        assert r.status_code == 200, r.text
        subjects = {s["label"]: s for s in r.json()["subjects"]}
        assert subjects["P-01"]["match"] == "linked"
        assert subjects["P-01"]["id"] == chosen_iri


def test_place_commit_relocates_signature_map_when_template_is_curie(
    tmp_path: Path, healthy_client: OxigraphClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """signature dataset の mapping.yaml が ``subject.template`` を CURIE
    （``exr:plant/{code}``）で書いている場合でも、commit の ① materialize が
    その map を mapping.yaml の中で再特定できる（実の ``_materialize_own`` を
    monkeypatch せずに走らせる — CURIE と展開済み IRI をそのまま比べると
    再特定に失敗し ``signature map could not be re-located`` の 500 になる）。"""
    app, cfg = _build(tmp_path, healthy_client)

    async def fake_ingest_own(fastapi_app, dataset_id):
        return {"job_id": "job-fake-curie"}

    async def fake_promote_own(fastapi_app, dataset_id):
        return {}

    monkeypatch.setattr(place_routes, "_ingest_own", fake_ingest_own)
    monkeypatch.setattr(place_routes, "_promote_own", fake_promote_own)

    with TestClient(app, headers=_AUTH) as client:
        sid = _stage(client)
        r = client.post(
            "/api/place/commit",
            json={
                "staging_id": sid,
                "type_id": _PLANT_CLASS,
                "choices": {},
                "name": "CURIE テンプレートの庭",
            },
        )
        assert r.status_code == 200, r.text
        dataset_id = r.json()["dataset_id"]

    saved = registry.load_dataset(cfg.registry_root, dataset_id)
    mapping_text = saved["artifacts"]["mapping.yaml"]
    assert "plant" in mapping_text  # 元 map (name: plant) がコピーされている
    assert "plants.csv" in mapping_text


def test_place_commit_partial_columns_prunes_the_copied_mapping(
    tmp_path: Path, healthy_client: OxigraphClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """置いたファイルが signature の列の部分集合 (``height_cm`` 無し) でも commit
    が成功する — コピーされた RML はファイルにある列だけを参照し (O5)、
    ``pruned_columns`` に落とした列が返る（ingest/promote は monkeypatch で
    省略 — RML の刈り込みそのものは実の ``_materialize_own`` を走らせて見る）。"""
    app, cfg = _build(tmp_path, healthy_client)

    async def fake_ingest_own(fastapi_app, dataset_id):
        return {"job_id": "job-fake-partial"}

    async def fake_promote_own(fastapi_app, dataset_id):
        return {}

    monkeypatch.setattr(place_routes, "_ingest_own", fake_ingest_own)
    monkeypatch.setattr(place_routes, "_promote_own", fake_promote_own)

    with TestClient(app, headers=_AUTH) as client:
        sid = _stage(client, csv=b"code,name\nP-01,Rose\nP-02,Tulip\n", name="plants.csv")
        r = client.post(
            "/api/place/commit",
            json={
                "staging_id": sid,
                "type_id": _PLANT_CLASS,
                "choices": {},
                "name": "部分集合の庭",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["pruned_columns"] == ["height_cm"]
        dataset_id = body["dataset_id"]

    saved = registry.load_dataset(cfg.registry_root, dataset_id)
    mapping_text = saved["artifacts"]["mapping.yaml"]
    assert "height_cm" not in mapping_text
    assert "ex:height" not in mapping_text
    rml_ttl = saved["artifacts"]["mapping.rml.ttl"]
    assert "height_cm" not in rml_ttl


def test_place_commit_missing_id_column_is_400(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    """ID の列 (``code``) が無い表は「後で定義を直す」に落とせない — 400。"""
    app, _cfg = _build(tmp_path, healthy_client)
    with TestClient(app, headers=_AUTH) as client:
        sid = _stage(client, csv=b"name,height_cm\nRose,32.5\n", name="plants.csv")
        r = client.post(
            "/api/place/commit",
            json={
                "staging_id": sid,
                "type_id": _PLANT_CLASS,
                "choices": {},
                "name": "ID の無い庭",
            },
        )
        assert r.status_code == 400
        assert "code" in r.text


def test_place_commit_does_not_leave_a_dataset_when_ingest_fails(
    tmp_path: Path, healthy_client: OxigraphClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ingest が失敗したら、materialize が作りかけたデータセットを残さない。"""
    app, cfg = _build(tmp_path, healthy_client)

    async def failing_ingest_own(fastapi_app, dataset_id):
        raise place_routes.HTTPException(502, "ingest failed: boom")

    monkeypatch.setattr(place_routes, "_ingest_own", failing_ingest_own)

    with TestClient(app, headers=_AUTH) as client:
        sid = _stage(client)
        before = {d["id"] for d in registry.list_datasets(cfg.registry_root)}
        r = client.post(
            "/api/place/commit",
            json={
                "staging_id": sid,
                "type_id": _PLANT_CLASS,
                "choices": {},
                "name": "ingest が失敗する庭",
            },
        )
        assert r.status_code == 502
        after = {d["id"] for d in registry.list_datasets(cfg.registry_root)}
        assert after == before  # 新しいデータセットが残っていない


def test_place_commit_reraises_original_error_even_when_cleanup_delete_fails(
    tmp_path: Path, healthy_client: OxigraphClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ingest が失敗し、その後始末の ``registry.delete_dataset`` 自体も失敗しても、
    呼び出し元には（delete 失敗の 500 ではなく）元の ingest 失敗がそのまま返る
    （checker finding: 素の ``except Exception: registry.delete_dataset(...); raise``
    は delete が例外を投げると元の例外を握りつぶしていた）。"""
    app, _cfg = _build(tmp_path, healthy_client)

    async def failing_ingest_own(fastapi_app, dataset_id):
        raise place_routes.HTTPException(502, "ingest failed: boom")

    def failing_delete_dataset(registry_root, dataset_id):
        raise RuntimeError("cleanup delete also failed")

    monkeypatch.setattr(place_routes, "_ingest_own", failing_ingest_own)
    monkeypatch.setattr(registry, "delete_dataset", failing_delete_dataset)

    with TestClient(app, headers=_AUTH) as client:
        sid = _stage(client)
        r = client.post(
            "/api/place/commit",
            json={
                "staging_id": sid,
                "type_id": _PLANT_CLASS,
                "choices": {},
                "name": "delete も失敗する庭",
            },
        )
        # 502 のまま（delete 失敗の別の 500 に置き換わっていない）。
        assert r.status_code == 502
        assert "ingest failed" in r.text


def test_place_commit_requires_write_auth(tmp_path: Path, healthy_client: OxigraphClient) -> None:
    app, _cfg = _build(tmp_path, healthy_client)
    with TestClient(app) as client:  # ヘッダなし
        r = client.post(
            "/api/place/commit",
            json={"staging_id": "x", "type_id": _PLANT_CLASS, "choices": {}, "name": "n"},
        )
        assert r.status_code == 401
