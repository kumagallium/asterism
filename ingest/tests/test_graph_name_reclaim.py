"""掃除した版の graph が、名前の索引（``GRAPH ?g {}``）に名前を残さないこと。

公開を新しい版に切り替えると古い版は pendingDrop に積まれ、掃除
（``sweep_pending_drops`` → ``chunked_drop_graph``）が中身を消す。本物の Oxigraph は、
三つ組を全部消しても（``CLEAR`` でも）graph の名前を索引に残し、名前を消すのは ``DROP``
だけ。名前が残ると、起動のたびに ``reconcile_orphan_versions`` がその空の graph を孤児として
積み直し、掃除がまた走っていた（入れ替えるほど毎回の掃除が増える）。

同じ本文を 2 つのストアで走らせる:

* ``rdflib`` — 常に走る（CI）。rdflib の Dataset も本物と同じく名前を残す。
* ``oxigraph`` — 本物の Oxigraph。``ASTERISM_OXIGRAPH_BIN``（なければ PATH の
  ``oxigraph``）があるときだけ走る。一時フォルダの store で起動し、途中で再起動もする。
  例::

      ASTERISM_OXIGRAPH_BIN=/Applications/Asterism.app/Contents/Resources/backend/oxigraph \\
          uv run pytest tests/test_graph_name_reclaim.py

``test_emptied_graph_keeps_its_name_until_dropped`` は代役（rdflib）が本物と同じ答えを
返すことを固定する。これが崩れると、rdflib だけで走る残りのテストは何も確かめなくなる。
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import rdflib

from asterism.oxigraph_client import OxigraphClient, OxigraphConfig
from asterism.substrate import (
    CONTROL_GRAPH_IRI,
    LIVE_GRAPH_PREDICATE,
    all_version_graphs,
    canonical_graph_iri,
    chunked_drop_graph,
    pending_drops,
    promote_to_canonical,
    reconcile_orphan_versions,
    set_staged_graph,
    sweep_pending_drops,
    versioned_graph_iri,
)

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _oxigraph_binary() -> str | None:
    override = (os.environ.get("ASTERISM_OXIGRAPH_BIN") or "").strip()
    if override:
        path = Path(override).expanduser()
        return str(path) if path.is_file() else shutil.which(override)
    return shutil.which("oxigraph")


class _RdflibClient:
    """rdflib の Dataset の上の SELECT/UPDATE（書き込みの回数を数える）。"""

    def __init__(self) -> None:
        self.ds = rdflib.Dataset()
        self.updates = 0

    async def sparql_select(self, query: str) -> dict:
        raw = self.ds.query(query).serialize(format="json")
        return json.loads(raw.decode() if isinstance(raw, bytes) else raw)

    async def sparql_update(self, update: str) -> None:
        self.updates += 1
        self.ds.update(update)


class _CountingOxigraphClient(OxigraphClient):
    def __init__(self, config: OxigraphConfig) -> None:
        super().__init__(config)
        self.updates = 0

    async def sparql_update(self, update: str) -> None:
        self.updates += 1
        await super().sparql_update(update)


class _Store:
    """テストが触るストア。``restart`` は本物なら Oxigraph を止めて同じ store で起動し直す。"""

    def __init__(self, kind: str, tmp_path: Path) -> None:
        self.kind = kind
        self._location = tmp_path / "oxigraph-store"
        self._proc: subprocess.Popen[bytes] | None = None
        self._port = 0
        self.client: _RdflibClient | _CountingOxigraphClient
        if kind == "rdflib":
            self.client = _RdflibClient()
        else:
            self._start()
            self.client = self._new_client()

    def _start(self) -> None:
        binary = _oxigraph_binary()
        assert binary is not None
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self._port = int(sock.getsockname()[1])
        self._proc = subprocess.Popen(
            [
                binary,
                "serve",
                "--location",
                str(self._location),
                "--bind",
                f"127.0.0.1:{self._port}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                pytest.fail(f"oxigraph exited early (code {self._proc.returncode})")
            try:
                httpx.get(f"http://127.0.0.1:{self._port}/", timeout=0.5)
                return
            except httpx.HTTPError:
                time.sleep(0.1)
        self._stop()  # 立ち上がらなかったプロセスを残さない
        pytest.fail("oxigraph did not become ready")

    def _new_client(self) -> _CountingOxigraphClient:
        return _CountingOxigraphClient(OxigraphConfig(base_url=f"http://127.0.0.1:{self._port}"))

    def _stop(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=5)
        self._proc = None

    async def restart(self) -> None:
        if self.kind == "rdflib":
            return
        assert isinstance(self.client, OxigraphClient)
        await self.client.aclose()
        self._stop()
        self._start()
        self.client = self._new_client()

    async def close(self) -> None:
        if isinstance(self.client, OxigraphClient):
            await self.client.aclose()
        self._stop()


@pytest.fixture(
    params=[
        "rdflib",
        pytest.param(
            "oxigraph",
            marks=pytest.mark.skipif(
                _oxigraph_binary() is None,
                reason="no oxigraph binary (ASTERISM_OXIGRAPH_BIN / PATH)",
            ),
        ),
    ]
)
async def store(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[_Store]:
    s = _Store(request.param, tmp_path)
    try:
        yield s
    finally:
        await s.close()


async def _put(store: _Store, graph: str, *values: str) -> None:
    rows = " ".join(f'<https://ex#s> <https://ex#p> "{v}" .' for v in values)
    await store.client.sparql_update(f"INSERT DATA {{ GRAPH <{graph}> {{ {rows} }} }}")


async def _names(store: _Store) -> list[str]:
    """名前の索引にある graph の名前（control は除く）。"""
    data = await store.client.sparql_select("SELECT ?g WHERE { GRAPH ?g {} } ORDER BY ?g")
    return [
        b["g"]["value"]
        for b in data["results"]["bindings"]
        if "g" in b and b["g"]["value"] != CONTROL_GRAPH_IRI
    ]


async def test_emptied_graph_keeps_its_name_until_dropped(store: _Store) -> None:
    # 前提（代役が本物と同じであること）: 三つ組を全部消しても CLEAR しても名前は残り、
    # DROP だけが名前を消す。再起動しても残った名前は消えない。
    deleted, cleared, dropped = (versioned_graph_iri("probe", n) for n in (1, 2, 3))
    for g in (deleted, cleared, dropped):
        await _put(store, g, "a", "b")
    await store.client.sparql_update(f"DELETE WHERE {{ GRAPH <{deleted}> {{ ?s ?p ?o }} }}")
    await store.client.sparql_update(f"CLEAR GRAPH <{cleared}>")
    await store.client.sparql_update(f"DROP GRAPH <{dropped}>")
    await store.restart()
    assert await _names(store) == [deleted, cleared]


async def test_chunked_drop_graph_drops_the_name(store: _Store) -> None:
    # 中身のある graph は束で消したあと名前も消える。中身が空で名前だけ残った graph
    # （直す前に掃除した版）は、束は 0 のまま名前が消える。隣の graph は触らない。
    full, leftover, keep = (versioned_graph_iri("ds1", n) for n in (1, 2, 3))
    await _put(store, full, "a", "b", "c")
    await _put(store, leftover, "x")
    await _put(store, keep, "k")
    await store.client.sparql_update(f"DELETE WHERE {{ GRAPH <{leftover}> {{ ?s ?p ?o }} }}")
    assert await _names(store) == [full, leftover, keep]

    assert await chunked_drop_graph(store.client, full, chunk=2) == 2
    assert await chunked_drop_graph(store.client, leftover) == 0
    await store.restart()
    assert await _names(store) == [keep]
    # 名前の無い graph にもう一度打っても失敗しない（掃除の途中で落ちたあとのやり直し）
    assert await chunked_drop_graph(store.client, full) == 0


async def test_swept_version_is_not_requeued_on_the_next_startup(store: _Store) -> None:
    # 報告された穴そのもの: 公開を v2 に切り替え → v1 は pendingDrop → 掃除。そのあと
    # 起動し直しても、v1 は孤児として積み直されず、掃除も書き込みも起きない。
    key = canonical_graph_iri("ds1")
    v1, v2 = versioned_graph_iri("ds1", 1), versioned_graph_iri("ds1", 2)
    await _put(store, v1, "v1")
    await _put(store, v2, "v2")
    await set_staged_graph(store.client, key, v1)
    await promote_to_canonical(store.client, key, v1)
    await set_staged_graph(store.client, key, v2)
    assert await promote_to_canonical(store.client, key, v2) == v1
    assert await sweep_pending_drops(store.client) == [v1]

    for _ in range(2):  # 2 回目・3 回目の起動
        await store.restart()
        store.client.updates = 0
        assert await all_version_graphs(store.client) == [v2]
        assert await reconcile_orphan_versions(store.client) == []
        assert await sweep_pending_drops(store.client) == []
        assert await pending_drops(store.client) == []
        assert store.client.updates == 0


async def test_leftover_empty_name_is_reclaimed_once(store: _Store) -> None:
    # 直す前に掃除した版は、空の名前だけが残っている。次の起動で 1 回だけ孤児として
    # 積まれ、掃除が名前ごと消し、その次の起動からは何も書かない。
    key = canonical_graph_iri("ds1")
    v1, v2 = versioned_graph_iri("ds1", 1), versioned_graph_iri("ds1", 2)
    await _put(store, v1, "old")
    await store.client.sparql_update(f"DELETE WHERE {{ GRAPH <{v1}> {{ ?s ?p ?o }} }}")
    await _put(store, v2, "v2")
    await store.client.sparql_update(
        f"INSERT DATA {{ GRAPH <{CONTROL_GRAPH_IRI}> {{ "
        f"<{key}> <{LIVE_GRAPH_PREDICATE}> <{v2}> }} }}"
    )

    assert await reconcile_orphan_versions(store.client) == [v1]
    assert await sweep_pending_drops(store.client) == [v1]

    await store.restart()
    store.client.updates = 0
    assert await all_version_graphs(store.client) == [v2]
    assert await reconcile_orphan_versions(store.client) == []
    assert await sweep_pending_drops(store.client) == []
    assert store.client.updates == 0
