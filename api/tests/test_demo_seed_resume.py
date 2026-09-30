"""前回の種まきが、見本を取り込んだ後・公開する前で止まった環境（``seed_demo_dataset``）。

同梱の本物の見本（``datasets/world/snapshot.tar``）を、本物の取り込み
（``exchange.import_snapshot``）と公開の内部関数に通す。ストアは rdflib の Dataset
（``test_exchange.py`` と同じ形の代役）。1 回目の起動は公開だけを落として
「取り込み済み・未公開・印なし」を作り、2 回目の起動で何が起きるかを見る。
"""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
import rdflib
from asterism import substrate

from asterism_api import appdata, local, registry
from asterism_api.main import Settings

_SNAPSHOT = Path(__file__).resolve().parents[2] / "datasets" / "world" / "snapshot.tar"
_DATASET_ID = "world"
_STAGED_IRI = substrate.versioned_graph_iri(_DATASET_ID, 1)


class _StoreClient:
    """OxigraphClient の代役（rdflib の Dataset の上で、種まきが使う面だけ）。"""

    def __init__(self) -> None:
        self.ds = rdflib.Dataset()

    async def sparql_select(self, query: str) -> dict:
        raw = self.ds.query(query).serialize(format="json")
        return json.loads(raw.decode() if isinstance(raw, bytes) else raw)

    async def sparql_update(self, update: str) -> None:
        self.ds.update(update)

    async def sparql_construct(self, query: str) -> str:
        raw = self.ds.query(query).serialize(format="turtle")
        if raw is None:
            return ""
        return raw.decode() if isinstance(raw, bytes) else raw

    async def post_turtle_bytes(self, payload: bytes, graph_iri: str | None = None) -> int:
        g = self.ds.graph(rdflib.URIRef(graph_iri)) if graph_iri else self.ds.default_graph
        g.parse(data=payload.decode("utf-8"), format="turtle")
        return len(payload)

    async def graph_triple_count(self, graph_iri: str) -> int:
        return len(self.ds.graph(rdflib.URIRef(graph_iri)))


class _PromoteGate:
    """``substrate.promote_to_canonical`` の前に置く関門。``fail`` の間は落とす。"""

    def __init__(self) -> None:
        self.fail = False
        self.calls = 0
        self._real = substrate.promote_to_canonical

    async def __call__(self, client: _StoreClient, dataset_key: str, staged: str) -> str | None:
        self.calls += 1
        if self.fail:
            raise RuntimeError("injected promote failure")
        return await self._real(client, dataset_key, staged)


class _Env:
    def __init__(self, tmp_path: Path, gate: _PromoteGate, iri_base: str | None) -> None:
        self.home = tmp_path / "home"
        self.home.mkdir()
        env = {
            "CSV2RDF_REGISTRY_ROOT": str(tmp_path / "registry"),
            "ASTERISM_APPDATA_ROOT": str(tmp_path / "appdata"),
            "ASTERISM_SINGLE_USER": "1",
        }
        if iri_base is not None:
            env["ASTERISM_IRI_BASE"] = iri_base
        self.cfg = Settings(env)
        self.store = _StoreClient()
        self.gate = gate

    @property
    def dataset_dir(self) -> Path:
        return self.cfg.registry_root / _DATASET_ID

    def meta(self) -> dict:
        return json.loads((self.dataset_dir / "meta.json").read_text(encoding="utf-8"))

    def write_meta(self, **changes: object) -> None:
        meta = self.meta()
        meta.update(changes)
        (self.dataset_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    def start(self) -> None:
        """起動 1 回ぶんの種まき。"""
        asyncio.run(local.seed_demo_dataset(self.home, self.cfg, self.store))

    def live_graph(self) -> str | None:
        key = substrate.canonical_graph_iri(_DATASET_ID)
        return asyncio.run(substrate.live_graph_of(self.store, key))

    def starter_subjects(self) -> list[dict]:
        return appdata.read_threads(self.cfg.appdata_root, namespace="subjects")


def _make_stuck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, iri_base: str | None = None
) -> _Env:
    """1 回目の起動で、取り込みは済み・公開で落ちた環境。"""
    gate = _PromoteGate()
    monkeypatch.setattr(local.substrate, "promote_to_canonical", gate)
    monkeypatch.setattr(local, "find_world_snapshot", lambda: _SNAPSHOT)
    env = _Env(tmp_path, gate, iri_base)

    gate.fail = True
    env.start()
    gate.fail = False

    meta = env.meta()
    assert meta["ingested"] is True
    assert meta["promoted"] is False
    assert not (env.home / "demo-seeded").exists()
    assert env.live_graph() is None
    gate.calls = 0
    return env


@pytest.fixture
def stuck(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Env:
    return _make_stuck(tmp_path, monkeypatch)


def test_next_start_publishes_the_sample_the_previous_seed_left_unpublished(
    stuck: _Env,
) -> None:
    stuck.start()

    assert stuck.gate.calls == 1
    meta = stuck.meta()
    assert meta["promoted"] is True
    assert meta["live_graph"] == _STAGED_IRI
    assert stuck.live_graph() == _STAGED_IRI
    assert (stuck.home / "demo-seeded").is_file()
    # 公開の後ろの段（スターター主語）まで届いている。
    assert {item["kind"] for item in stuck.starter_subjects()} == {"individual", "set"}

    # その次の起動では、もう何もしない。
    stuck.start()
    assert stuck.gate.calls == 1


def test_next_start_publishes_a_sample_whose_iris_the_import_rewrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """手元の IRI の土台が見本と違い、取り込みがファイルの IRI を書き換えた環境でも。"""
    env = _make_stuck(tmp_path, monkeypatch, iri_base="https://lab.example.jp")
    assert env.meta()["imported"]["rebased"] is True

    env.start()

    assert env.gate.calls == 1
    assert env.meta()["promoted"] is True
    assert (env.home / "demo-seeded").is_file()


def _edit_description(env: _Env) -> None:
    path = env.dataset_dir / "mie.yaml"
    path.write_bytes(path.read_bytes() + b"\n# edited\n")


def _add_decision_file(env: _Env) -> None:
    (env.dataset_dir / "handles.json").write_text("[]", encoding="utf-8")


def _save_design_again(env: _Env) -> None:
    data = registry.load_dataset(env.cfg.registry_root, _DATASET_ID)
    assert data is not None
    artifacts = dict(data["artifacts"])
    artifacts["diagram.md"] = artifacts["diagram.md"] + "\n"
    registry.update_dataset_artifacts(
        env.cfg.registry_root,
        _DATASET_ID,
        artifacts,
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
    )


def _rename(env: _Env) -> None:
    registry.rename_dataset(env.cfg.registry_root, _DATASET_ID, "my sample")


def _fail_to_ingest_again(env: _Env) -> None:
    registry.reserve_data_seq(env.cfg.registry_root, _DATASET_ID)


def _ingest_again(env: _Env) -> None:
    root = env.cfg.registry_root
    seq = registry.reserve_data_seq(root, _DATASET_ID)
    graph = substrate.versioned_graph_iri(_DATASET_ID, seq)
    for triple in env.store.ds.graph(rdflib.URIRef(_STAGED_IRI)):
        env.store.ds.graph(rdflib.URIRef(graph)).add(triple)
    registry.mark_ingested(
        root,
        _DATASET_ID,
        graph_iri=graph,
        triple_count=len(env.store.ds.graph(rdflib.URIRef(graph))),
        ingested_at="2026-09-30T00:00:00+00:00",
        data_seq=seq,
    )


def _publish_by_hand(env: _Env) -> None:
    key = substrate.canonical_graph_iri(_DATASET_ID)
    asyncio.run(substrate.promote_to_canonical(env.store, key, _STAGED_IRI))
    registry.mark_promoted(
        env.cfg.registry_root,
        _DATASET_ID,
        triples_promoted=1,
        alignment={},
        promoted_at="2026-09-30T00:00:00+00:00",
        canonical_graph=key,
        live_graph=_STAGED_IRI,
        published_subjects=[],
    )
    env.gate.calls = 0


def _mark_published(env: _Env) -> None:
    env.write_meta(promoted=True)


def _retract(env: _Env) -> None:
    env.write_meta(status="retracted")


def _other_data(env: _Env) -> None:
    imported = dict(env.meta()["imported"])
    imported["canonical_sha256"] = "0" * 64
    env.write_meta(imported=imported)


def _not_imported(env: _Env) -> None:
    env.write_meta(origin="own")


def _lose_a_triple_from_the_imported_graph(env: _Env) -> None:
    graph = env.store.ds.graph(rdflib.URIRef(_STAGED_IRI))
    graph.remove(next(iter(graph)))


@pytest.mark.parametrize(
    "touch",
    [
        _edit_description,
        _add_decision_file,
        _save_design_again,
        _rename,
        _fail_to_ingest_again,
        _ingest_again,
        _publish_by_hand,
        _mark_published,
        _retract,
        _other_data,
        _not_imported,
        _lose_a_triple_from_the_imported_graph,
    ],
)
def test_next_start_leaves_a_touched_sample_alone(
    stuck: _Env, touch: Callable[[_Env], None]
) -> None:
    before = stuck.meta()
    touch(stuck)
    after_touch = stuck.meta()

    stuck.start()

    assert stuck.gate.calls == 0
    assert stuck.meta() == after_touch
    assert not (stuck.home / "demo-seeded").exists()
    assert stuck.starter_subjects() == []
    if not before.get("promoted") and not after_touch.get("promoted"):
        assert stuck.live_graph() is None


def test_next_start_does_not_bring_back_a_sample_the_user_deleted(stuck: _Env) -> None:
    """利用者が止まった見本を消し、自分のデータを入れていたら、見本を入れ直さない。"""
    shutil.rmtree(stuck.dataset_dir)
    mine = stuck.cfg.registry_root / "mine"
    mine.mkdir()
    (mine / "meta.json").write_text(json.dumps({"id": "mine", "name": "mine"}), encoding="utf-8")

    stuck.start()

    assert stuck.gate.calls == 0
    assert not stuck.dataset_dir.exists()
    assert not (stuck.home / "demo-seeded").exists()
