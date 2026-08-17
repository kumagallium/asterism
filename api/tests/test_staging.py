"""Design-time source staging (ADR source-staging.md).

The source gets a server-side home the moment it is dropped: POST /api/staging
once, then every design call takes the id instead of re-uploading, and S5's
attach copies from staging into the dataset's source/. Nothing here needs an
LLM — the staged-vs-uploaded shape is exercised through the deterministic
entrances (inspect / skeleton validate / attach).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from fastapi.testclient import TestClient

from asterism_api import staging
from asterism_api.main import build_app
from tests.test_main import (  # noqa: F401  (healthy_client is a fixture)
    _AUTH,
    _MATERIALIZE_MD_DISCONNECTED,
    _settings,
    healthy_client,
)

# ruff: noqa: F811  — `healthy_client` is a pytest fixture reused by name.

_CSV = b"No,Name,2theta,(hkl)\n03-065-2664,Aluminum Vanadium,21.34,(0;0;2)\n"


def _client(tmp_path: Path, healthy_client) -> TestClient:
    app = build_app(_settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False)
    return TestClient(app, headers=_AUTH)


def test_stage_then_read_then_forget(tmp_path: Path, healthy_client) -> None:
    """One upload → a record with raw + canonical files; GET sees it; DELETE forgets it."""
    with _client(tmp_path, healthy_client) as client:
        r = client.post("/api/staging", files={"files": ("xrd card.txt", _CSV, "text/plain")})
        assert r.status_code == 200, r.text
        body = r.json()
        sid = body["staging_id"]
        assert staging.valid_id(sid)
        # Canonical (slugged, hash-suffixed because it changed) name — the one
        # rml:source will use, and the SAME one attach will produce later
        # (the sanitizer is idempotent, so raw/ and root agree).
        (name,) = body["sources"]
        assert name.startswith("xrd-card") and name.endswith(".txt")
        sdir = staging.dir_for(tmp_path / "registry", sid)
        assert (sdir / "raw" / name).read_bytes() == _CSV
        assert (sdir / name).read_bytes() == _CSV
        assert json.loads((sdir / "meta.json").read_text())["sources"] == [name]

        assert client.get(f"/api/staging/{sid}").json()["sources"] == [name]
        assert client.delete(f"/api/staging/{sid}").json() == {"deleted": True}
        assert client.get(f"/api/staging/{sid}").status_code == 404
        assert client.delete(f"/api/staging/{sid}").json() == {"deleted": False}


def test_staging_id_is_a_capability_never_a_path(tmp_path: Path, healthy_client) -> None:
    """The id is the ONLY path component a client controls: anything but a uuid4
    is refused before it can touch the filesystem; an unknown uuid is a 404."""
    with _client(tmp_path, healthy_client) as client:
        assert client.get("/api/staging/..%2F..%2Fetc").status_code == 404
        assert client.get("/api/staging/not-a-uuid").status_code == 404
        assert client.get(f"/api/staging/{staging.new_id()}").status_code == 404
        # And a design call given a dead id says so instead of guessing.
        r = client.post("/api/inspect", data={"staging_id": staging.new_id()})
        assert r.status_code == 404


def test_design_calls_read_from_staging_and_leave_it_alone(
    tmp_path: Path, healthy_client
) -> None:
    """inspect / skeleton-validate take the id instead of files — the same
    answer as an upload, and the record survives the call (it outlives it)."""
    with _client(tmp_path, healthy_client) as client:
        sid = client.post(
            "/api/staging", files={"files": ("card.csv", _CSV, "text/csv")}
        ).json()["staging_id"]

        uploaded = client.post("/api/inspect", files={"files": ("card.csv", _CSV, "text/csv")})
        staged = client.post("/api/inspect", data={"staging_id": sid})
        assert staged.status_code == 200, staged.text
        assert staged.headers["X-Asterism-Source-Names"] == "card.csv"
        # Identical inspection, save for the path line (temp dir vs staging dir).
        def strip(md: str) -> str:
            return "\n".join(ln for ln in md.splitlines() if not ln.startswith("- Path:"))

        assert strip(staged.text) == strip(uploaded.text)

        skeleton = {
            "version": 1,
            "prefixes": {"xo": "https://x/#", "xr": "https://x/r/"},
            "maps": [
                {"name": "card", "source": "card.csv",
                 "subject": {"template": "xr:card/{No}", "classes": ["xo:Card"]}}
            ],
        }
        r = client.post(
            "/api/propose/skeleton/validate",
            data={"staging_id": sid, "skeleton": json.dumps(skeleton)},
        )
        assert r.status_code == 200, r.text
        assert r.json()["annotations"]["maps"]["card"]["checkable"] is True
        # Still there: the record is the design's source until attach consumes it.
        assert client.get(f"/api/staging/{sid}").status_code == 200


def test_attach_consumes_staging_into_the_dataset_source(
    tmp_path: Path, healthy_client
) -> None:
    """S5: the staged raw upload becomes the dataset's persisted source through
    the SAME converter a fresh upload takes; the staging record is then gone."""
    with _client(tmp_path, healthy_client) as client:
        payload = b"SID,composition,zt\n1,Bi2Te3,0.9\n"
        sid = client.post(
            "/api/staging", files={"files": ("data.csv", payload, "text/csv")}
        ).json()["staging_id"]
        ds_id = client.post(
            "/api/materialize",
            json={"proposal_md": _MATERIALIZE_MD_DISCONNECTED, "dataset_name": "thermo"},
        ).json()["dataset"]["id"]
        r = client.post(f"/api/datasets/{ds_id}/source", data={"staging_id": sid})
        assert r.status_code == 200, r.text
        assert r.json()["source_files"] == ["data.csv"]
        assert (tmp_path / "registry" / ds_id / "source" / "data.csv").is_file()
        assert client.get(f"/api/staging/{sid}").status_code == 404  # consumed


def test_stale_records_are_swept_on_the_next_create(tmp_path: Path, healthy_client) -> None:
    with _client(tmp_path, healthy_client) as client:
        first = client.post("/api/staging", files={"files": ("a.csv", _CSV, "text/csv")})
        old = first.json()["staging_id"]
        old_dir = staging.dir_for(tmp_path / "registry", old)
        ancient = time.time() - staging.TTL.total_seconds() - 60
        os.utime(old_dir, (ancient, ancient))
        client.post("/api/staging", files={"files": ("b.csv", _CSV, "text/csv")})
        assert not old_dir.exists()


def test_staging_is_write_gated(tmp_path: Path, healthy_client) -> None:
    """It writes to the registry, so it sits behind the same gate as every
    other write — a bare instance answers 401/503 and the client falls back
    to keeping its own copy of the files (the legacy path)."""
    app = build_app(_settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False)
    with TestClient(app) as client:  # deliberately no token
        r = client.post("/api/staging", files={"files": ("a.csv", _CSV, "text/csv")})
        assert r.status_code == 401
