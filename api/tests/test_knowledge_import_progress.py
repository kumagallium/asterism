"""pairs の進みが数（done / total）で取れる（PR 14d-2b・契約書 §1）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from .test_ingest import _AUTH
from .test_knowledge_import import _events, _start
from .test_knowledge_relations import _env

pytest.importorskip("morph_kgc", reason="the copy runs the real declarative ingest")


def test_pairs_frames_carry_done_and_total(tmp_path: Path) -> None:
    env = _env(tmp_path)
    with TestClient(env.app, headers=_AUTH) as client:
        r = _start(client)
        assert r.status_code == 202, r.text
        events = _events(client, r.json()["job_id"])
    frames = [e for e in events if e.get("stage") == "pairs" and "total" in e]
    assert frames and frames[0]["done"] == 0
    total = frames[0]["total"]
    assert total > 0 and all(f["total"] == total for f in frames)
    assert frames[-1]["done"] == total
    assert [f["done"] for f in frames] == sorted(f["done"] for f in frames)
