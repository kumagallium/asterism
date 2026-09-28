"""Tests for ``register_appdata_cards`` (契約メモ contract_pr_f4.md §1-5).

Same shape as ``test_cards_api.py``'s ``/api/appdata/subjects`` block and
``test_appdata.py``'s Ask-thread limit tests: ``build_app`` then this
parallel-段 module's OWN ``register_appdata_cards(app, settings)`` — main.py
does not need to be running for these (its 1-line wiring is exercised
elsewhere, by any test that boots the full app)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from asterism_api import appdata
from asterism_api.appdata_cards_routes import CARDS_NAMESPACE, register_appdata_cards
from asterism_api.main import build_app
from tests.test_main import _AUTH, _settings, healthy_client  # noqa: F401

# ruff: noqa: F811  — `healthy_client` is a pytest fixture reused by name.

CARD_ID = "card-0123456789abcdef"
CARD_PAYLOAD = {
    "card_id": CARD_ID,
    "subject_key": "i:https://ex/library/resource/checkout-1",
    "tool": "set_measure",
    "params": {"class": "https://ex/library#CheckoutRecord", "where": [], "shape": "quantity"},
    "title": "件数",
    "output_kind": "quantity",
    "created_at": "2026-09-24T00:00:00Z",
}


def _single_user_settings(tmp_path: Path) -> object:
    s = _settings(tmp_path)
    s.single_user = True
    s.appdata_root = tmp_path / "appdata"
    return s


def _client(tmp_path: Path, healthy_client, *, single_user: bool = True) -> TestClient:
    settings = _single_user_settings(tmp_path) if single_user else _settings(tmp_path)
    app = build_app(settings, oxigraph_client=healthy_client, start_watcher=False)
    register_appdata_cards(app, settings)
    return TestClient(app, headers=_AUTH)


def test_appdata_cards_404_without_single_user(tmp_path: Path, healthy_client) -> None:
    with _client(tmp_path, healthy_client, single_user=False) as client:
        assert client.get("/api/appdata/cards").status_code == 404
        assert client.put(f"/api/appdata/cards/{CARD_ID}", json=CARD_PAYLOAD).status_code == 404
        assert client.delete(f"/api/appdata/cards/{CARD_ID}").status_code == 404


def test_appdata_cards_round_trip(tmp_path: Path, healthy_client) -> None:
    with _client(tmp_path, healthy_client) as client:
        r = client.put(f"/api/appdata/cards/{CARD_ID}", json=CARD_PAYLOAD)
        assert r.status_code == 200, r.text
        assert r.json() == {"saved": True}

        r = client.get("/api/appdata/cards")
        assert r.status_code == 200
        assert r.json()["cards"] == [CARD_PAYLOAD]

        r = client.delete(f"/api/appdata/cards/{CARD_ID}")
        assert r.status_code == 200
        assert r.json()["deleted"] is True

        assert client.get("/api/appdata/cards").json()["cards"] == []


def test_appdata_cards_delete_missing_returns_false(tmp_path: Path, healthy_client) -> None:
    with _client(tmp_path, healthy_client) as client:
        r = client.delete(f"/api/appdata/cards/{CARD_ID}")
        assert r.status_code == 200
        assert r.json()["deleted"] is False


def test_appdata_cards_put_requires_write_auth(tmp_path: Path, healthy_client) -> None:
    settings = _single_user_settings(tmp_path)
    app = build_app(settings, oxigraph_client=healthy_client, start_watcher=False)
    register_appdata_cards(app, settings)
    with TestClient(app) as client:  # NO auth headers
        r = client.put(f"/api/appdata/cards/{CARD_ID}", json=CARD_PAYLOAD)
        assert r.status_code == 401


def test_appdata_cards_delete_requires_write_auth(tmp_path: Path, healthy_client) -> None:
    settings = _single_user_settings(tmp_path)
    app = build_app(settings, oxigraph_client=healthy_client, start_watcher=False)
    register_appdata_cards(app, settings)
    with TestClient(app) as client:  # NO auth headers
        r = client.delete(f"/api/appdata/cards/{CARD_ID}")
        assert r.status_code == 401


@pytest.mark.parametrize("bad_id", ["not-a-hash", "card-AB12", "card-abcd", "0123456789abcdef"])
def test_appdata_cards_invalid_id_is_400(tmp_path: Path, healthy_client, bad_id: str) -> None:
    with _client(tmp_path, healthy_client) as client:
        r = client.put(f"/api/appdata/cards/{bad_id}", json=CARD_PAYLOAD)
        assert r.status_code == 400, r.text
        r = client.delete(f"/api/appdata/cards/{bad_id}")
        assert r.status_code == 400, r.text


def test_appdata_cards_put_body_must_be_object(tmp_path: Path, healthy_client) -> None:
    with _client(tmp_path, healthy_client) as client:
        r = client.put(f"/api/appdata/cards/{CARD_ID}", json=[1, 2, 3])
        assert r.status_code == 400, r.text


def test_appdata_cards_over_content_length_is_413_before_reading_body(
    tmp_path: Path, healthy_client
) -> None:
    with _client(tmp_path, healthy_client) as client:
        r = client.put(
            f"/api/appdata/cards/{CARD_ID}",
            content=b"{}",
            headers={"content-length": str(appdata.MAX_THREAD_BYTES + 1)},
        )
        assert r.status_code == 413


def test_appdata_cards_over_size_limit_is_413(tmp_path: Path, healthy_client) -> None:
    with _client(tmp_path, healthy_client) as client:
        huge = {**CARD_PAYLOAD, "blob": "x" * (2 * 1024 * 1024)}
        r = client.put(f"/api/appdata/cards/{CARD_ID}", json=huge)
        assert r.status_code == 413


def test_appdata_cards_count_limit(tmp_path: Path, healthy_client) -> None:
    """``MAX_THREADS`` 件で頭打ち。1000 回 HTTP を叩く代わりに、直接
    ``appdata.write_thread`` で ``cards/`` を埋めてから 1 回だけ PUT する
    （同じ限界を安く固定する）。"""
    settings = _single_user_settings(tmp_path)
    root = settings.appdata_root
    for i in range(appdata.MAX_THREADS):
        cid = f"card-{i:016x}"
        appdata.write_thread(root, cid, {**CARD_PAYLOAD, "card_id": cid}, namespace=CARDS_NAMESPACE)
    app = build_app(settings, oxigraph_client=healthy_client, start_watcher=False)
    register_appdata_cards(app, settings)
    with TestClient(app, headers=_AUTH) as client:
        overflow_id = f"card-{appdata.MAX_THREADS:016x}"
        r = client.put(
            f"/api/appdata/cards/{overflow_id}",
            json={**CARD_PAYLOAD, "card_id": overflow_id},
        )
        assert r.status_code == 413, r.text
