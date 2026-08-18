"""On-disk Ask threads + settings for single-user mode (ADR
app-data-on-disk.md).

Everything under ``/api/appdata/*`` except ``GET .../info`` must 404 unless
``cfg.single_user`` is on (the shared/hosted api never sets
``ASTERISM_SINGLE_USER``) — that is the load-bearing property this file
checks first, before exercising the single-user round trip.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from asterism_api import appdata
from asterism_api.main import build_app
from tests.test_main import _AUTH, _settings, healthy_client  # noqa: F401

# ruff: noqa: F811  — `healthy_client` is a pytest fixture reused by name.


def _single_user_settings(tmp_path: Path) -> object:
    s = _settings(tmp_path)
    s.single_user = True
    s.appdata_root = tmp_path / "appdata"
    return s


def _client(tmp_path: Path, healthy_client, *, single_user: bool = True) -> TestClient:
    settings = _single_user_settings(tmp_path) if single_user else _settings(tmp_path)
    app = build_app(settings, oxigraph_client=healthy_client, start_watcher=False)
    return TestClient(app, headers=_AUTH)


def test_shared_api_only_exposes_info_and_it_says_no(tmp_path: Path, healthy_client) -> None:
    """Without ASTERISM_SINGLE_USER the shared/hosted api's behaviour is
    untouched: info answers 200 with single_user: false, and every other
    appdata route 404s."""
    with _client(tmp_path, healthy_client, single_user=False) as client:
        r = client.get("/api/appdata/info")
        assert r.status_code == 200
        assert r.json() == {"single_user": False, "home": None}

        assert client.get("/api/appdata/ask/threads").status_code == 404
        assert client.get("/api/appdata/settings").status_code == 404
        tid = "0" * 8 + "-0000-4000-8000-000000000000"
        assert client.put(f"/api/appdata/ask/threads/{tid}", json={}).status_code == 404
        assert client.delete(f"/api/appdata/ask/threads/{tid}").status_code == 404
        assert client.put("/api/appdata/settings", json={}).status_code == 404


def test_single_user_info_reports_the_home(tmp_path: Path, healthy_client) -> None:
    with _client(tmp_path, healthy_client) as client:
        r = client.get("/api/appdata/info")
        assert r.status_code == 200
        assert r.json() == {"single_user": True, "home": str(tmp_path)}


def test_thread_round_trips_then_deletes(tmp_path: Path, healthy_client) -> None:
    with _client(tmp_path, healthy_client) as client:
        tid = "11111111-1111-4111-8111-111111111111"
        payload = {"id": tid, "title": "hello", "messages": [{"role": "user", "content": "hi"}]}
        r = client.put(f"/api/appdata/ask/threads/{tid}", json=payload)
        assert r.status_code == 200, r.text

        r = client.get("/api/appdata/ask/threads")
        assert r.status_code == 200
        assert r.json()["threads"] == [payload]

        r = client.delete(f"/api/appdata/ask/threads/{tid}")
        assert r.status_code == 200
        assert r.json() == {"deleted": True}
        assert client.get("/api/appdata/ask/threads").json()["threads"] == []

        # Deleting again is idempotent.
        assert client.delete(f"/api/appdata/ask/threads/{tid}").json() == {"deleted": False}


def test_one_corrupt_thread_file_does_not_take_down_the_rest(
    tmp_path: Path, healthy_client
) -> None:
    with _client(tmp_path, healthy_client) as client:
        tid = "22222222-2222-4222-8222-222222222222"
        client.put(f"/api/appdata/ask/threads/{tid}", json={"id": tid})

        ask_dir = appdata.appdata_root(tmp_path) / "ask"
        (ask_dir / "not-a-uuid-but-json.json").write_text("{not valid json", "utf-8")

        r = client.get("/api/appdata/ask/threads")
        assert r.status_code == 200
        threads = r.json()["threads"]
        assert threads == [{"id": tid}]


def test_invalid_thread_id_is_rejected(tmp_path: Path, healthy_client) -> None:
    with _client(tmp_path, healthy_client) as client:
        assert client.put("/api/appdata/ask/threads/not-a-uuid", json={}).status_code == 400
        assert (
            client.put("/api/appdata/ask/threads/..%2F..%2Fetc", json={}).status_code
            in (400, 404)
        )
        assert client.delete("/api/appdata/ask/threads/not-a-uuid").status_code == 400


def test_settings_round_trip_strips_credential_looking_keys(
    tmp_path: Path, healthy_client
) -> None:
    with _client(tmp_path, healthy_client) as client:
        payload = {"theme": "dark", "apiKey": "sk-super-secret", "provider_token": "abc"}
        r = client.put("/api/appdata/settings", json=payload)
        assert r.status_code == 200, r.text

        r = client.get("/api/appdata/settings")
        assert r.status_code == 200
        settings = r.json()["settings"]
        assert settings == {"theme": "dark"}
        assert "apiKey" not in settings
        assert "provider_token" not in settings

        # Never written to disk in the first place.
        on_disk = json.loads((appdata.appdata_root(tmp_path) / "settings.json").read_text())
        assert "apiKey" not in on_disk
        assert "provider_token" not in on_disk


def test_settings_strips_nested_credentials_but_keeps_lookalikes(
    tmp_path: Path, healthy_client
) -> None:
    """Secrets buried inside the actual settings shape (models.models[].apiKey)
    must be stripped too — not just top-level keys — while non-credential
    fields whose names merely contain "key" as a substring survive."""
    with _client(tmp_path, healthy_client) as client:
        payload = {
            "models": {
                "models": [{"id": "m1", "apiKey": "sk-x", "apiBase": "https://x"}],
                "activeModelId": "m1",
            },
            "sortKey": "name",
            "hotkey": "cmd+k",
        }
        r = client.put("/api/appdata/settings", json=payload)
        assert r.status_code == 200, r.text

        settings = client.get("/api/appdata/settings").json()["settings"]
        assert settings["models"]["models"] == [{"id": "m1", "apiBase": "https://x"}]
        assert settings["models"]["activeModelId"] == "m1"
        assert settings["sortKey"] == "name"
        assert settings["hotkey"] == "cmd+k"

        on_disk = json.loads((appdata.appdata_root(tmp_path) / "settings.json").read_text())
        assert "apiKey" not in json.dumps(on_disk)


def test_thread_count_limit_ignores_non_uuid_json_files(tmp_path: Path, healthy_client) -> None:
    """Stray non-uuid4 .json files in ask/ (hand-edited leftovers, a sync
    tool's "(conflicted copy)" file, ...) must not count toward the
    thread-limit check."""
    with _client(tmp_path, healthy_client) as client:
        ask_dir = appdata.appdata_root(tmp_path) / "ask"
        ask_dir.mkdir(parents=True)
        for i in range(5):
            (ask_dir / f"not-a-uuid-{i}.json").write_text("{}", "utf-8")
        (ask_dir / "thread (conflicted copy).json").write_text("{}", "utf-8")

        tid = "55555555-5555-4555-8555-555555555555"
        r = client.put(f"/api/appdata/ask/threads/{tid}", json={"id": tid})
        assert r.status_code == 200, r.text


def test_thread_put_over_content_length_is_413_before_reading_body(
    tmp_path: Path, healthy_client
) -> None:
    with _client(tmp_path, healthy_client) as client:
        tid = "66666666-6666-4666-8666-666666666666"
        r = client.put(
            f"/api/appdata/ask/threads/{tid}",
            content=b"{}",
            headers={"content-length": str(appdata.MAX_THREAD_BYTES + 1)},
        )
        assert r.status_code == 413


def test_settings_put_over_content_length_is_413_before_reading_body(
    tmp_path: Path, healthy_client
) -> None:
    with _client(tmp_path, healthy_client) as client:
        r = client.put(
            "/api/appdata/settings",
            content=b"{}",
            headers={"content-length": str(appdata.MAX_SETTINGS_BYTES + 1)},
        )
        assert r.status_code == 413


def test_thread_over_size_limit_is_413(tmp_path: Path, healthy_client) -> None:
    with _client(tmp_path, healthy_client) as client:
        tid = "33333333-3333-4333-8333-333333333333"
        huge = {"id": tid, "blob": "x" * (2 * 1024 * 1024)}
        r = client.put(f"/api/appdata/ask/threads/{tid}", json=huge)
        assert r.status_code == 413


def test_settings_over_size_limit_is_413(tmp_path: Path, healthy_client) -> None:
    with _client(tmp_path, healthy_client) as client:
        huge = {"blob": "x" * (512 * 1024)}
        r = client.put("/api/appdata/settings", json=huge)
        assert r.status_code == 413


def test_appdata_writes_are_write_gated(tmp_path: Path, healthy_client) -> None:
    settings = _single_user_settings(tmp_path)
    app = build_app(settings, oxigraph_client=healthy_client, start_watcher=False)
    with TestClient(app) as client:  # deliberately no token
        tid = "44444444-4444-4444-8444-444444444444"
        assert client.put(f"/api/appdata/ask/threads/{tid}", json={}).status_code == 401
        assert client.delete(f"/api/appdata/ask/threads/{tid}").status_code == 401
        assert client.put("/api/appdata/settings", json={}).status_code == 401
        # Reads stay open.
        assert client.get("/api/appdata/ask/threads").status_code == 200
        assert client.get("/api/appdata/settings").status_code == 200
