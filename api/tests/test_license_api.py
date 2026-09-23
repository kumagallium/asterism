"""Tests for ライセンスの持ち場 (契約メモ contract_pr_d.md §1。担当 D1-license):

* ``registry.dataset_origin`` — own/open/unknown の読み手。
* ``PUT /api/datasets/{dataset_id}/license`` — 保存すると ``mie.yaml`` の
  ``schema_info.license`` と（投影の）``metadata.ttl`` の ``dcterms:license``
  が一緒に書き変わり、公開済みならストアのメタグラフも書き直る。

``register_license`` は main.py にまだ配線されていない（§0.1: 配線は統合段の
仕事）ので、``test_class_schema_api.py``/``test_place_api.py`` と同じ流儀で
``build_app(settings, ...)`` の後に自分で ``register_license(app, settings)``
を呼んでから ``TestClient`` を作る。

架空 2 分野（道具台帳・気象観測ログ）を使い、分野固有の名詞（材料科学の語）は
書かない。
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
from asterism.oxigraph_client import OxigraphClient, OxigraphConfig
from fastapi.testclient import TestClient

from asterism_api import license_routes, registry
from asterism_api.main import build_app
from tests.test_main import _AUTH, _settings, healthy_client  # noqa: F401 (fixture)

# ruff: noqa: F811  — `healthy_client` is a pytest fixture reused by name.

_TOOLS_MIE = (
    "schema_info:\n"
    "  title: 道具台帳\n"
    "  description: 貸出可能な道具の一覧\n"
)


def _save(tmp: Path, dataset_name: str = "道具台帳", mie: str = _TOOLS_MIE) -> dict:
    artifacts = {
        "diagram.md": "classDiagram\n  class Item",
        "model.yaml": "- Item:",
        "mie.yaml": mie,
    }
    return registry.save_dataset(
        tmp / "registry",
        dataset_name,
        artifacts,
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
        created_at="2026-09-23T00:00:00+00:00",
    )


def _build(tmp_path: Path, client: OxigraphClient):
    settings = _settings(tmp_path)
    app = build_app(settings, oxigraph_client=client, start_watcher=False)
    license_routes.register_license(app, settings)
    return app, settings


def _mock_client(handler) -> OxigraphClient:
    inner = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
    return OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner)


# ---------------------------------------------------------------------------
# registry.dataset_origin
# ---------------------------------------------------------------------------


def test_dataset_origin_defaults_to_unknown(tmp_path: Path) -> None:
    meta = _save(tmp_path)
    root = tmp_path / "registry"
    assert registry.dataset_origin(root, meta["id"]) == "unknown"


def test_dataset_origin_own(tmp_path: Path) -> None:
    meta = _save(tmp_path)
    root = tmp_path / "registry"
    registry._update_meta(root, meta["id"], {"origin": "own"})
    assert registry.dataset_origin(root, meta["id"]) == "own"


def test_dataset_origin_open(tmp_path: Path) -> None:
    meta = _save(tmp_path)
    root = tmp_path / "registry"
    registry._update_meta(root, meta["id"], {"origin": "open"})
    assert registry.dataset_origin(root, meta["id"]) == "open"


def test_dataset_origin_unrecognized_value_is_unknown(tmp_path: Path) -> None:
    """保守側 (O13): own/open 以外の値はどれも unknown 扱い。"""
    meta = _save(tmp_path)
    root = tmp_path / "registry"
    registry._update_meta(root, meta["id"], {"origin": "borrowed"})
    assert registry.dataset_origin(root, meta["id"]) == "unknown"


def test_dataset_origin_absent_dataset_is_unknown(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    assert registry.dataset_origin(root, "does-not-exist") == "unknown"


def test_dataset_origin_rejects_unsafe_id(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    assert registry.dataset_origin(root, "../escape") == "unknown"


# ---------------------------------------------------------------------------
# PUT /api/datasets/{dataset_id}/license
# ---------------------------------------------------------------------------


def test_put_license_writes_mie_yaml_and_metadata_ttl(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    meta = _save(tmp_path)
    dataset_id = meta["id"]
    app, settings = _build(tmp_path, healthy_client)
    with TestClient(app, headers=_AUTH) as client:
        r = client.put(f"/api/datasets/{dataset_id}/license", json={"license": "CC-BY-4.0"})
        assert r.status_code == 200, r.text
        assert r.json() == {
            "dataset_id": dataset_id,
            "license": "CC-BY-4.0",
            "redistributable": True,
        }

    root = settings.registry_root
    mie_text = (root / dataset_id / "mie.yaml").read_text(encoding="utf-8")
    assert "license: CC-BY-4.0" in mie_text
    ttl_text = (root / dataset_id / "metadata.ttl").read_text(encoding="utf-8")
    assert "CC-BY-4.0" in ttl_text
    assert "dcterms:license" in ttl_text
    # A plain SPDX id is not a valid IRI (ADR §3) - it must be a Literal, not
    # written as <CC-BY-4.0>.
    assert "<CC-BY-4.0>" not in ttl_text


def test_put_license_unknown_value_is_kept_with_unknown_redistributable(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    meta = _save(tmp_path, dataset_name="気象観測ログ")
    dataset_id = meta["id"]
    app, _settings_obj = _build(tmp_path, healthy_client)
    with TestClient(app, headers=_AUTH) as client:
        r = client.put(
            f"/api/datasets/{dataset_id}/license", json={"license": "Some-Weird-License-1.0"}
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["license"] == "Some-Weird-License-1.0"
        assert body["redistributable"] is None


def test_put_license_null_clears_it(tmp_path: Path, healthy_client: OxigraphClient) -> None:
    meta = _save(tmp_path)
    dataset_id = meta["id"]
    app, settings = _build(tmp_path, healthy_client)
    with TestClient(app, headers=_AUTH) as client:
        r = client.put(f"/api/datasets/{dataset_id}/license", json={"license": "MIT"})
        assert r.status_code == 200, r.text

        r = client.put(f"/api/datasets/{dataset_id}/license", json={"license": None})
        assert r.status_code == 200, r.text
        assert r.json() == {"dataset_id": dataset_id, "license": None, "redistributable": None}

    mie_text = (settings.registry_root / dataset_id / "mie.yaml").read_text(encoding="utf-8")
    assert "license" not in mie_text
    # The rest of schema_info survives the clear.
    assert "道具台帳" in mie_text


def test_put_license_unknown_dataset_is_404(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    app, _settings_obj = _build(tmp_path, healthy_client)
    with TestClient(app, headers=_AUTH) as client:
        r = client.put("/api/datasets/does-not-exist/license", json={"license": "MIT"})
        assert r.status_code == 404


def test_put_license_requires_write_auth(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    meta = _save(tmp_path)
    app, _settings_obj = _build(tmp_path, healthy_client)
    with TestClient(app) as client:  # ヘッダなし
        r = client.put(f"/api/datasets/{meta['id']}/license", json={"license": "MIT"})
        assert r.status_code == 401


def test_put_license_rejects_when_token_unset(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    meta = _save(tmp_path)
    settings = _settings(tmp_path)
    settings.api_token = None
    app = build_app(settings, oxigraph_client=healthy_client, start_watcher=False)
    license_routes.register_license(app, settings)
    with TestClient(app, headers=_AUTH) as client:
        r = client.put(f"/api/datasets/{meta['id']}/license", json={"license": "MIT"})
        assert r.status_code == 503


def test_put_license_on_promoted_dataset_rewrites_meta_graph(tmp_path: Path) -> None:
    """公開済み (promoted) なら、既存 ``_project_meta_graph`` を呼んで
    ストアのメタグラフも書き直す（ADR dataset-description-in-the-store.md §4:
    説明の正本はストア）。"""
    calls: list[tuple[str, bytes]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/query":
            return httpx.Response(
                200,
                text=json.dumps({"head": {}, "boolean": True}),
                headers={"content-type": "application/sparql-results+json"},
            )
        calls.append((request.url.path, request.content))
        return httpx.Response(204)

    client_ = _mock_client(handler)
    meta = _save(tmp_path)
    dataset_id = meta["id"]
    app, settings = _build(tmp_path, client_)
    registry.mark_promoted(
        settings.registry_root,
        dataset_id,
        triples_promoted=1,
        alignment={},
        promoted_at="2026-09-23T00:00:00+00:00",
    )

    with TestClient(app, headers=_AUTH) as tc:
        r = tc.put(f"/api/datasets/{dataset_id}/license", json={"license": "CC-BY-4.0"})
        assert r.status_code == 200, r.text

    store_writes = [content for path, content in calls if path in ("/store", "/update")]
    assert any(b"CC-BY-4.0" in content for content in store_writes)


def test_put_license_on_unpromoted_dataset_does_not_touch_store(tmp_path: Path) -> None:
    """未公開 (not promoted) のときは既存 ``_project_meta_graph`` を呼ばない —
    ストア書き込みの中身にライセンス値が現れないことで確認する（build_app の
    ``lifespan`` は起動のたびに control グラフの移行/backfill を無条件で走らせる
    ので、``/update``/``/store`` 呼び出しの有無そのものはこの route 専用ではない）。
    """
    calls: list[tuple[str, bytes]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/query":
            return httpx.Response(
                200,
                text=json.dumps({"head": {}, "boolean": True}),
                headers={"content-type": "application/sparql-results+json"},
            )
        calls.append((request.url.path, request.content))
        return httpx.Response(204)

    client_ = _mock_client(handler)
    meta = _save(tmp_path)
    dataset_id = meta["id"]
    app, _settings_obj = _build(tmp_path, client_)

    with TestClient(app, headers=_AUTH) as tc:
        r = tc.put(f"/api/datasets/{dataset_id}/license", json={"license": "CC-BY-4.0"})
        assert r.status_code == 200, r.text

    assert not any(b"CC-BY-4.0" in content for _path, content in calls)
