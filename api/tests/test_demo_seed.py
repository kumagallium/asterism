"""``seed_demo_dataset`` — 初回起動の見本自動取り込み（契約メモ
contract_pr_e.md §2）。

``exchange.import_snapshot`` / ``main.py`` の promote ルートが呼ぶ内部関数
（``registry.load_dataset``・``substrate.alignment_report``・
``substrate.promote_to_canonical``・``registry.mark_promoted``）/ appdata
書き込みはすべて monkeypatch する — snapshot.tar は demo-data が並行作成中で、
まだ本物を用意できないため（契約メモの指示どおり）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from asterism_api import local
from asterism_api.main import Settings

_DATASET_ID = "world"
_STAGED_IRI = "https://asterism.invalid/asterism/graph/canonical/world/v1"
_JAPAN_IRI = "https://asterism.invalid/datasets/world/resource/country/japan"
_COUNTRY_CLASS_IRI = "https://asterism.invalid/datasets/world/ontology#Country"
_REGION_PROP_IRI = "https://asterism.invalid/datasets/world/ontology#regionJa"


def _settings(tmp_path: Path, *, single_user: bool = True) -> Settings:
    env = {
        "CSV2RDF_REGISTRY_ROOT": str(tmp_path / "registry"),
        "ASTERISM_APPDATA_ROOT": str(tmp_path / "appdata"),
    }
    if single_user:
        env["ASTERISM_SINGLE_USER"] = "1"
    return Settings(env)


def _fake_snapshot(tmp_path: Path) -> Path:
    """``find_world_snapshot`` が返す先。中身は import_snapshot を
    monkeypatch するので実際の tar 形式である必要はない — 「読めるファイルが
    ある」ことだけが要る。"""
    path = tmp_path / "snapshot.tar"
    path.write_bytes(b"not a real snapshot - import_snapshot is mocked")
    return path


class _Recorder:
    """呼ばれた内部関数の名前を、呼ばれた順に集める。"""

    def __init__(self) -> None:
        self.calls: list[str] = []


def _patch_success_pipeline(
    monkeypatch: pytest.MonkeyPatch, recorder: _Recorder
) -> list[dict[str, Any]]:
    """import_snapshot → promote 内部関数 → appdata 書き込み、すべて成功する形で
    monkeypatch する。"""

    async def fake_import_snapshot(
        cfg: Any, client: Any, payload: bytes, *, max_extracted_bytes: int
    ) -> dict[str, Any]:
        recorder.calls.append("import_snapshot")
        return {
            "dataset_id": _DATASET_ID,
            "name": "世界の国 (Gapminder)",
            "staged_graph": _STAGED_IRI,
            "triples": 756,
            "rebased": False,
            "origin_iri_base": "https://asterism.invalid",
            "status": "ingested",
        }

    def fake_load_dataset(root: Path, dataset_id: str) -> dict[str, Any]:
        recorder.calls.append("load_dataset")
        assert dataset_id == _DATASET_ID
        return {
            "meta": {"triple_count": 756},
            "artifacts": {"mapping.yaml": "TriplesMap: {}"},
        }

    async def fake_alignment_report(client: Any, staged_iri: str) -> dict[str, Any]:
        recorder.calls.append("alignment_report")
        assert staged_iri == _STAGED_IRI
        return {"predicates": {"reuse": [], "new": []}, "classes": {"reuse": [], "new": []}}

    async def fake_promote_to_canonical(client: Any, dataset_key: str, staged_graph: str) -> None:
        recorder.calls.append("promote_to_canonical")
        assert staged_graph == _STAGED_IRI

    def fake_mark_promoted(root: Path, dataset_id: str, **kwargs: Any) -> dict[str, Any]:
        recorder.calls.append("mark_promoted")
        assert dataset_id == _DATASET_ID
        assert kwargs["triples_promoted"] == 756
        assert kwargs["live_graph"] == _STAGED_IRI
        return {"id": dataset_id, "promoted": True}

    async def fake_find_japan(client: Any, graph_iri: str) -> tuple[str, str, str]:
        recorder.calls.append("find_japan")
        assert graph_iri == _STAGED_IRI
        return (_JAPAN_IRI, _COUNTRY_CLASS_IRI, _REGION_PROP_IRI)

    written: list[dict[str, Any]] = []

    def fake_write_thread(
        root: Path, thread_id: str, payload: dict[str, Any], namespace: str
    ) -> None:
        recorder.calls.append(f"write_thread:{payload['kind']}")
        assert namespace == "subjects"
        written.append(payload)

    monkeypatch.setattr(local.exchange, "import_snapshot", fake_import_snapshot)
    monkeypatch.setattr(local.registry, "load_dataset", fake_load_dataset)
    monkeypatch.setattr(local.registry, "list_datasets", lambda root: [])
    monkeypatch.setattr(local.substrate, "alignment_report", fake_alignment_report)
    monkeypatch.setattr(local.substrate, "promote_to_canonical", fake_promote_to_canonical)
    monkeypatch.setattr(local.registry, "mark_promoted", fake_mark_promoted)
    monkeypatch.setattr(local, "_find_demo_japan_subject", fake_find_japan)
    monkeypatch.setattr(local.appdata, "write_thread", fake_write_thread)
    return written


def test_seed_runs_import_then_promote_internals_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    cfg = _settings(tmp_path)
    recorder = _Recorder()
    written = _patch_success_pipeline(monkeypatch, recorder)
    monkeypatch.setattr(local, "find_world_snapshot", lambda: _fake_snapshot(tmp_path))

    asyncio.run(local.seed_demo_dataset(home, cfg, client=object()))

    assert recorder.calls == [
        "import_snapshot",
        "load_dataset",
        "alignment_report",
        "promote_to_canonical",
        "mark_promoted",
        "find_japan",
        "write_thread:individual",
        "write_thread:set",
    ]
    assert (home / "demo-seeded").is_file()
    # 2 件とも書けている: 日本（individual）と地域の一覧（set）。
    kinds = {item["kind"] for item in written}
    assert kinds == {"individual", "set"}
    individual = next(item for item in written if item["kind"] == "individual")
    assert individual["id"] == _JAPAN_IRI
    assert individual["label"] == "日本"
    assert individual["class_label"] == "国"
    assert individual["source"] == "open"
    assert individual["subject_key"] == f"i:{_JAPAN_IRI}"
    # 契約メモ contract_pr_f2.md §2.1: 左レールがデータセットごとに子を束ねる
    # ための紐付け（レジストリに何も無い状態なので dataset_label は id への
    # フォールバック — 実際の見本ファイルがある起動では表示名になる）。
    assert individual["dataset_id"] == _DATASET_ID
    assert individual["dataset_label"] == _DATASET_ID
    the_set = next(item for item in written if item["kind"] == "set")
    assert the_set["label"] == "東アジア・太平洋の国"
    assert the_set["spec"]["class"] == _COUNTRY_CLASS_IRI
    assert the_set["spec"]["where"] == [
        {"property": _REGION_PROP_IRI, "op": "in", "value": ["東アジア・太平洋"]}
    ]
    assert the_set["spec"]["source_scope"] == "open"
    assert the_set["subject_key"] == f"s:{the_set['id']}"
    assert the_set["dataset_id"] == _DATASET_ID
    assert the_set["dataset_label"] == _DATASET_ID


def test_seed_dataset_label_resolves_from_the_registry_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``dataset_id`` は常に ``world`` 固定だが、``dataset_label`` はレジストリ
    の表示名を引く（契約メモ §2.1 の ``resolve_dataset_label`` 再利用）— 実際の
    起動では ``import_snapshot`` が書いた ``meta.json`` がここに乗る。"""
    home = tmp_path / "home"
    home.mkdir()
    cfg = _settings(tmp_path)
    dataset_dir = cfg.registry_root / _DATASET_ID
    dataset_dir.mkdir(parents=True)
    (dataset_dir / "meta.json").write_text(
        '{"id": "world", "name": "世界の国 (Gapminder)"}', encoding="utf-8"
    )
    recorder = _Recorder()
    written = _patch_success_pipeline(monkeypatch, recorder)
    monkeypatch.setattr(local, "find_world_snapshot", lambda: _fake_snapshot(tmp_path))

    asyncio.run(local.seed_demo_dataset(home, cfg, client=object()))

    assert all(item["dataset_label"] == "世界の国 (Gapminder)" for item in written)


def test_second_run_is_a_no_op(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    cfg = _settings(tmp_path)
    recorder = _Recorder()
    _patch_success_pipeline(monkeypatch, recorder)
    monkeypatch.setattr(local, "find_world_snapshot", lambda: _fake_snapshot(tmp_path))

    asyncio.run(local.seed_demo_dataset(home, cfg, client=object()))
    assert recorder.calls  # 1 回目は動いた
    recorder.calls.clear()

    asyncio.run(local.seed_demo_dataset(home, cfg, client=object()))
    assert recorder.calls == []  # 2 回目は何も呼ばれない


def test_skips_when_registry_already_has_a_dataset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    cfg = _settings(tmp_path)
    recorder = _Recorder()
    _patch_success_pipeline(monkeypatch, recorder)
    monkeypatch.setattr(local, "find_world_snapshot", lambda: _fake_snapshot(tmp_path))
    monkeypatch.setattr(local.registry, "list_datasets", lambda root: [{"id": "already-here"}])

    asyncio.run(local.seed_demo_dataset(home, cfg, client=object()))

    assert recorder.calls == []
    assert not (home / "demo-seeded").is_file()


def test_skips_when_env_disables_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    cfg = _settings(tmp_path)
    recorder = _Recorder()
    _patch_success_pipeline(monkeypatch, recorder)
    monkeypatch.setattr(local, "find_world_snapshot", lambda: _fake_snapshot(tmp_path))
    monkeypatch.setenv("ASTERISM_DEMO_DATASET", "0")

    asyncio.run(local.seed_demo_dataset(home, cfg, client=object()))

    assert recorder.calls == []
    assert not (home / "demo-seeded").is_file()


def test_skips_when_not_single_user(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    cfg = _settings(tmp_path, single_user=False)
    recorder = _Recorder()
    _patch_success_pipeline(monkeypatch, recorder)
    monkeypatch.setattr(local, "find_world_snapshot", lambda: _fake_snapshot(tmp_path))

    asyncio.run(local.seed_demo_dataset(home, cfg, client=object()))

    assert recorder.calls == []
    assert not (home / "demo-seeded").is_file()


def test_survives_import_exception_and_does_not_mark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    cfg = _settings(tmp_path)
    recorder = _Recorder()
    _patch_success_pipeline(monkeypatch, recorder)
    monkeypatch.setattr(local, "find_world_snapshot", lambda: _fake_snapshot(tmp_path))

    async def boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
        recorder.calls.append("import_snapshot")
        raise RuntimeError("snapshot import blew up")

    monkeypatch.setattr(local.exchange, "import_snapshot", boom)

    # 起動を落とさない = 例外が外に漏れないこと。
    asyncio.run(local.seed_demo_dataset(home, cfg, client=object()))

    assert recorder.calls == ["import_snapshot"]  # promote 側は一切呼ばれない
    assert not (home / "demo-seeded").is_file()  # 次回起動でやり直せる


def test_skips_when_no_snapshot_is_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    cfg = _settings(tmp_path)
    recorder = _Recorder()
    _patch_success_pipeline(monkeypatch, recorder)
    monkeypatch.setattr(local, "find_world_snapshot", lambda: None)

    asyncio.run(local.seed_demo_dataset(home, cfg, client=object()))

    assert recorder.calls == []
    assert not (home / "demo-seeded").is_file()


def test_missing_japan_skips_starter_subjects_but_still_marks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """日本の schema:name が見つからない場合: マーカーは書くが 2 件は書かない
    （契約メモ §2 の逃げ道 — IRI を直書きできないので、引けなければ諦める）。"""
    home = tmp_path / "home"
    home.mkdir()
    cfg = _settings(tmp_path)
    recorder = _Recorder()
    _patch_success_pipeline(monkeypatch, recorder)
    monkeypatch.setattr(local, "find_world_snapshot", lambda: _fake_snapshot(tmp_path))

    async def not_found(client: Any, graph_iri: str) -> None:
        recorder.calls.append("find_japan")
        return None

    monkeypatch.setattr(local, "_find_demo_japan_subject", not_found)

    asyncio.run(local.seed_demo_dataset(home, cfg, client=object()))

    assert "write_thread:individual" not in recorder.calls
    assert "write_thread:set" not in recorder.calls
    assert (home / "demo-seeded").is_file()  # データセット自体は公開済み


# ---------------------------------------------------------------------------
# 純関数 / SPARQL 解析（monkeypatch なしで直接検証）


def test_demo_subject_items_shape() -> None:
    items = local._demo_subject_items(
        _JAPAN_IRI,
        _COUNTRY_CLASS_IRI,
        _REGION_PROP_IRI,
        dataset_id=_DATASET_ID,
        dataset_label="世界の国 (Gapminder)",
    )
    assert [item["kind"] for item in items] == ["individual", "set"]
    individual, region_set = items
    assert individual["id"] == _JAPAN_IRI
    assert individual["thread_id"] != region_set["thread_id"]  # 別ファイル
    assert region_set["spec"]["limit"] == 20
    assert region_set["spec"]["order_by"] is None
    # 契約メモ contract_pr_f2.md §2.1: レールが子を束ねるための紐付け。
    assert individual["dataset_id"] == _DATASET_ID
    assert individual["dataset_label"] == "世界の国 (Gapminder)"
    assert region_set["dataset_id"] == _DATASET_ID
    assert region_set["dataset_label"] == "世界の国 (Gapminder)"
    # set_id は spec の決定論ハッシュ — 同じ入力なら毎回同じ id になる。
    again = local._demo_subject_items(
        _JAPAN_IRI,
        _COUNTRY_CLASS_IRI,
        _REGION_PROP_IRI,
        dataset_id=_DATASET_ID,
        dataset_label="世界の国 (Gapminder)",
    )
    assert again[1]["id"] == region_set["id"]


def test_find_demo_japan_subject_parses_sparql_bindings() -> None:
    class _FakeClient:
        async def sparql_select(self, query: str) -> dict[str, Any]:
            assert "東アジア・太平洋" in query
            assert "Japan" in query
            return {
                "results": {
                    "bindings": [
                        {
                            "japan": {"type": "uri", "value": _JAPAN_IRI},
                            "class": {"type": "uri", "value": _COUNTRY_CLASS_IRI},
                            "regionProp": {"type": "uri", "value": _REGION_PROP_IRI},
                        }
                    ]
                }
            }

    found = asyncio.run(local._find_demo_japan_subject(_FakeClient(), _STAGED_IRI))
    assert found == (_JAPAN_IRI, _COUNTRY_CLASS_IRI, _REGION_PROP_IRI)


def test_find_demo_japan_subject_returns_none_when_absent() -> None:
    class _EmptyClient:
        async def sparql_select(self, query: str) -> dict[str, Any]:
            return {"results": {"bindings": []}}

    found = asyncio.run(local._find_demo_japan_subject(_EmptyClient(), _STAGED_IRI))
    assert found is None


def _fake_repo_layout(tmp_path: Path) -> tuple[Path, Path]:
    """``local.__file__`` をここに差し替えると、``find_world_snapshot`` の
    「repo 相対」がこの ``datasets/world/snapshot.tar`` を指すようになる
    （``api/src/asterism_api/local.py`` の 4 段と同じ深さを再現する）。
    戻り値は ``(fake_file, repo_relative_snapshot_path)`` — 後者は
    ``find_world_snapshot`` 自身と同じ ``.resolve()`` を通した値なので、
    シンボリックリンクで見た目が変わる tmp_path でも文字列が一致する。"""
    fake_file = tmp_path / "api" / "src" / "asterism_api" / "local.py"
    fake_file.parent.mkdir(parents=True)
    fake_file.write_text("# stand-in for local.py's own path", encoding="utf-8")
    repo_snapshot = fake_file.resolve().parents[3] / "datasets" / "world" / "snapshot.tar"
    return fake_file, repo_snapshot


def test_find_world_snapshot_prefers_repo_checkout_over_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_file, repo_snapshot = _fake_repo_layout(tmp_path)
    repo_snapshot.parent.mkdir(parents=True)
    repo_snapshot.write_bytes(b"repo candidate")
    env_path = tmp_path / "elsewhere" / "snapshot.tar"
    env_path.parent.mkdir(parents=True)
    env_path.write_bytes(b"env candidate")
    monkeypatch.setenv("ASTERISM_DEMO_SNAPSHOT", str(env_path))
    monkeypatch.setattr(local, "__file__", str(fake_file))
    monkeypatch.setattr(local, "_bundled_world_snapshot_candidates", lambda: [])

    assert local.find_world_snapshot() == repo_snapshot


def test_find_world_snapshot_falls_back_to_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_file, _repo_snapshot = _fake_repo_layout(tmp_path)  # snapshot.tar は無い
    env_path = tmp_path / "snapshot.tar"
    env_path.write_bytes(b"env candidate")
    monkeypatch.setenv("ASTERISM_DEMO_SNAPSHOT", str(env_path))
    monkeypatch.setattr(local, "__file__", str(fake_file))
    monkeypatch.setattr(local, "_bundled_world_snapshot_candidates", lambda: [])

    assert local.find_world_snapshot() == env_path


def test_find_world_snapshot_none_when_nothing_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_file, _repo_snapshot = _fake_repo_layout(tmp_path)
    monkeypatch.delenv("ASTERISM_DEMO_SNAPSHOT", raising=False)
    monkeypatch.setattr(local, "__file__", str(fake_file))
    monkeypatch.setattr(local, "_bundled_world_snapshot_candidates", lambda: [])

    assert local.find_world_snapshot() is None
