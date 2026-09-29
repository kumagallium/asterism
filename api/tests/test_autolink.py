"""公開時の自動リンク（契約メモ contract_pr_f15.md §1.4）の単体テスト。

``discover`` はテスト用の差し替え口（本物の ``crosswalk_discover.discover`` は
ingest 側で別途テストされる）。「新規作成」「既存に追加」「idempotent」は
``build`` を差し替えず、本物の内部手順（``crosswalk_runtime`` 経由で
perspective の config/meta を実際に読み書きする）を通す — ただし SPARQL の
読み手は :class:`_DatasetClient`（空の ``rdflib.Dataset``）なので、参加者が
実際に "used" になるかどうかは検証しない（それは crosswalk_runtime 側の対象）。
ここで検証するのは自動リンクの判断（作る／足す／足さない／idempotent／
例外を握る）だけ。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import rdflib
import yaml
from asterism import crosswalk_runtime, substrate

from asterism_api import autolink, registry

PRED_CODE = "http://example.org/onto#code"
PRED_TITLE = "http://example.org/onto#title"
CLASS_BOOK = "http://example.org/onto#Book"


class _DatasetClient:
    """SPARQL 読み手として動く最小限のスタブ（rdflib.Dataset 上）。"""

    def __init__(self) -> None:
        self.ds = rdflib.Dataset()

    async def sparql_select(self, query: str) -> dict:
        raw = self.ds.query(query).serialize(format="json")
        return json.loads(raw.decode() if isinstance(raw, bytes) else raw)

    async def sparql_update(self, update: str) -> None:
        self.ds.update(update)

    async def post_turtle_bytes(self, payload: bytes, graph_iri: str | None = None) -> int:
        g = self.ds.graph(rdflib.URIRef(graph_iri)) if graph_iri else self.ds.default_graph
        g.parse(data=payload.decode("utf-8"), format="turtle")
        return len(payload)

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None


def _mapping_yaml(*, source: str, column: str, predicate: str) -> str:
    """1 source・1 handle 列だけを使う最小の Mapping IR（``handles.handle_slots``
    が読めればよい・分野固有の語彙は書かない）。"""
    local = predicate.rsplit("#", 1)[-1]
    return yaml.safe_dump(
        {
            "prefixes": {"ex": "http://example.org/onto#"},
            "maps": [
                {
                    "source": source,
                    "subject": {"classes": ["ex:Book"]},
                    "properties": [{"column": column, "predicate": f"ex:{local}"}],
                }
            ],
        }
    )


def _make_dataset(
    root: Path,
    name: str,
    *,
    has_handle: bool,
    predicate: str = PRED_CODE,
    source: str = "csv1",
    column: str = "code",
    promoted: bool = True,
) -> str:
    artifacts = {"mapping.yaml": _mapping_yaml(source=source, column=column, predicate=predicate)}
    if has_handle:
        artifacts["handles.json"] = json.dumps(
            {"version": 1, "handles": [{"source": source, "column": column}]}
        )
    meta = registry.save_dataset(
        root,
        name,
        artifacts,
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
        created_at="2026-01-01T00:00:00Z",
    )
    dataset_id = meta["id"]
    if promoted:
        registry.mark_promoted(
            root,
            dataset_id,
            triples_promoted=0,
            alignment={},
            promoted_at="2026-01-01T00:00:00Z",
        )
    return dataset_id


def _candidate(
    perspective_id: str, name: str, participants: list[dict], *, score: float = 1.0
) -> dict:
    return {
        "perspective_id": perspective_id,
        "name": name,
        "concept": name,
        "score": score,
        "build_config": {
            "min_datasets": 2,
            "concepts": [
                {
                    "name": name,
                    "class_iri": CLASS_BOOK,
                    "link_predicate": "http://example.org/onto#hasBook",
                    "normalizer": "identity",
                    "participants": participants,
                }
            ],
        },
    }


def _participant(dataset_id: str, predicate: str = PRED_CODE) -> dict:
    return {"dataset_id": dataset_id, "label": dataset_id, "predicate": predicate}


async def _never_discover(*args, **kwargs) -> dict:
    raise AssertionError("discover must not be called")


@pytest.mark.asyncio
async def test_no_handles_skips_without_calling_discover(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    root.mkdir()
    dsid = _make_dataset(root, "book-loans", has_handle=False)

    report = await autolink.maybe_autolink_handles(None, root, dsid, discover=_never_discover)

    assert report["linked"] == []
    assert report["skipped"] == [{"reason": "no_handles", "dataset_id": dsid}]


@pytest.mark.asyncio
async def test_no_partner_skips_without_calling_discover(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    root.mkdir()
    dsid = _make_dataset(root, "book-loans", has_handle=True)

    report = await autolink.maybe_autolink_handles(None, root, dsid, discover=_never_discover)

    assert report["linked"] == []
    assert report["skipped"] == [{"reason": "no_partner", "dataset_id": dsid}]


@pytest.mark.asyncio
async def test_no_candidates(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    root.mkdir()
    a = _make_dataset(root, "book-loans", has_handle=True)
    b = _make_dataset(root, "book-reviews", has_handle=True)

    seen_only_slots: dict = {}

    async def discover_stub(client, datasets, *, limits=None, only_slots=None):
        seen_only_slots.update(only_slots or {})
        assert {d.dataset_id for d in datasets} == {a, b}
        return {"candidates": []}

    report = await autolink.maybe_autolink_handles(None, root, a, discover=discover_stub)

    assert report["linked"] == []
    assert report["skipped"] == [{"reason": "no_candidates", "dataset_id": a}]
    assert set(seen_only_slots) == {a, b}
    assert seen_only_slots[a] == {(CLASS_BOOK, PRED_CODE)}


@pytest.mark.asyncio
async def test_new_perspective_created(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    root.mkdir()
    a = _make_dataset(root, "book-loans", has_handle=True)
    b = _make_dataset(root, "book-reviews", has_handle=True)
    perspective_id = "shared-code"

    async def discover_stub(client, datasets, *, limits=None, only_slots=None):
        return {
            "candidates": [
                _candidate(
                    perspective_id,
                    "shared code",
                    [_participant(a), _participant(b)],
                )
            ]
        }

    client = _DatasetClient()
    report = await autolink.maybe_autolink_handles(client, root, a, discover=discover_stub)

    assert report["skipped"] == []
    assert len(report["linked"]) == 1
    linked = report["linked"][0]
    assert linked["perspective_id"] == perspective_id
    assert linked["created"] is True
    assert {p["dataset_id"] for p in linked["participants_added"]} == {a, b}

    config = crosswalk_runtime.load_config(root, perspective_id)
    assert config is not None
    assert {p.dataset_id for p in config.concepts[0].participants} == {a, b}

    meta_path = root / crosswalk_runtime.crosswalk_registry_id(perspective_id) / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["auto_linked"] is True
    assert meta["auto_linked_from"] == [a]
    # 契約メモ contract_b_hub_names.md §2: 新規作成は機械付けの名前 —
    # R1（表示名の解決）がこの meta.name を人が付けた名前として扱わない。
    assert meta["name_auto"] is True


@pytest.mark.asyncio
async def test_existing_perspective_gets_missing_participant_only(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    root.mkdir()
    a = _make_dataset(root, "book-loans", has_handle=True)
    b = _make_dataset(root, "book-reviews", has_handle=True)
    perspective_id = "shared-code"
    name = "shared code"

    # A human-built perspective that already carries A (not auto_linked yet).
    existing_config = crosswalk_runtime.parse_config(
        {
            "concepts": [
                {
                    "name": name,
                    "class_iri": CLASS_BOOK,
                    "link_predicate": "http://example.org/onto#hasBook",
                    "normalizer": "identity",
                    "participants": [{"dataset_id": a, "label": a, "predicate": PRED_CODE}],
                }
            ]
        }
    )
    crosswalk_runtime.save_config(root, existing_config, perspective_id)
    outcome = crosswalk_runtime.BuildOutcome(
        built_at="2026-01-01T00:00:00Z",
        hub_graph="",
        triple_count=0,
        shared={},
        links={},
        participants_used=[],
        participants_skipped=[],
    )
    crosswalk_runtime.write_registry_scaffold(
        root, existing_config, outcome, perspective_id=perspective_id, name=name
    )

    async def discover_stub(client, datasets, *, limits=None, only_slots=None):
        return {
            "candidates": [_candidate(perspective_id, name, [_participant(a), _participant(b)])]
        }

    client = _DatasetClient()
    report = await autolink.maybe_autolink_handles(client, root, a, discover=discover_stub)

    assert len(report["linked"]) == 1
    linked = report["linked"][0]
    assert linked["created"] is False
    assert linked["participants_added"] == [{"dataset_id": b, "predicate": PRED_CODE}]

    config = crosswalk_runtime.load_config(root, perspective_id)
    assert {p.dataset_id for p in config.concepts[0].participants} == {a, b}

    # 契約メモ contract_b_hub_names.md §2: 既存 perspective への参加者追加は
    # 「新規作成」ではないので name_auto を立てない — 人が後で付けたかもし
    # れない名前を消さない。
    meta_path = root / crosswalk_runtime.crosswalk_registry_id(perspective_id) / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert "name_auto" not in meta


@pytest.mark.asyncio
async def test_same_dataset_second_slot_not_added(tmp_path: Path) -> None:
    """候補の participants に自分自身が 2 つの述語で重複していても、1 データ
    セット 1 スロット／concept で 1 つしか足さない。"""
    root = tmp_path / "registry"
    root.mkdir()
    a = _make_dataset(root, "book-loans", has_handle=True)
    b = _make_dataset(root, "book-reviews", has_handle=True)
    perspective_id = "shared-code"

    async def discover_stub(client, datasets, *, limits=None, only_slots=None):
        return {
            "candidates": [
                _candidate(
                    perspective_id,
                    "shared code",
                    [
                        _participant(a, PRED_CODE),
                        _participant(a, PRED_TITLE),  # same dataset, a 2nd slot
                        _participant(b),
                    ],
                )
            ]
        }

    client = _DatasetClient()
    report = await autolink.maybe_autolink_handles(client, root, a, discover=discover_stub)

    linked = report["linked"][0]
    assert sorted(p["dataset_id"] for p in linked["participants_added"]) == [a, b]

    config = crosswalk_runtime.load_config(root, perspective_id)
    assert len(config.concepts[0].participants) == 2


@pytest.mark.asyncio
async def test_second_promote_is_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    root.mkdir()
    a = _make_dataset(root, "book-loans", has_handle=True)
    b = _make_dataset(root, "book-reviews", has_handle=True)
    perspective_id = "shared-code"

    async def discover_stub(client, datasets, *, limits=None, only_slots=None):
        return {
            "candidates": [
                _candidate(perspective_id, "shared code", [_participant(a), _participant(b)])
            ]
        }

    client = _DatasetClient()
    first = await autolink.maybe_autolink_handles(client, root, a, discover=discover_stub)
    assert len(first["linked"]) == 1

    second = await autolink.maybe_autolink_handles(client, root, a, discover=discover_stub)
    assert second["linked"] == []
    assert second["skipped"] == []

    config = crosswalk_runtime.load_config(root, perspective_id)
    assert len(config.concepts[0].participants) == 2


@pytest.mark.asyncio
async def test_exception_is_swallowed_and_reported_as_error(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    root.mkdir()
    a = _make_dataset(root, "book-loans", has_handle=True)
    _make_dataset(root, "book-reviews", has_handle=True)

    async def discover_boom(*args, **kwargs) -> dict:
        raise RuntimeError("boom")

    report = await autolink.maybe_autolink_handles(None, root, a, discover=discover_boom)

    assert report["linked"] == []
    assert report["skipped"] == [{"reason": "error", "dataset_id": a}]


# ---------------------------------------------------------------------------
# 契約メモ contract_b2_hub_names.md B2-3: 自動で作る／育てるハブも R2 の
# 表示名を rdfs:label に書く。
# ---------------------------------------------------------------------------


def _fake_label_resolvers(word: str):
    """``main.py`` の ``_crosswalk_label_resolvers`` と同じ形の偽物 —
    ``predicate_label_of`` だけが ``word`` を返す（field_label_of/
    class_label_of は常に None、契約メモ B2-3 の R2 の 2 段目に相当）。"""

    def make(registry_root):
        def field_label_of(dataset_id, predicate, subject_class):
            return None

        def predicate_label_of(dataset_id, predicate):
            return word

        def class_label_of(dataset_id, class_iri):
            return None

        return predicate_label_of, field_label_of, class_label_of

    return make


def test_concept_labels_for_config_resolves_each_concepts_r2_label() -> None:
    config = crosswalk_runtime.parse_config(
        {
            "concepts": [
                {
                    "name": "shared_thing_name",
                    "class_iri": CLASS_BOOK,
                    "participants": [{"dataset_id": "ds-a", "predicate": PRED_CODE}],
                }
            ]
        }
    )
    labels = autolink._concept_labels_for_config(
        Path("/nonexistent"), config, _fake_label_resolvers("貸し出しコード")
    )
    assert labels == {"shared_thing_name": "貸し出しコード"}


def test_concept_labels_for_config_is_none_when_label_resolvers_is_none() -> None:
    config = crosswalk_runtime.parse_config(
        {
            "concepts": [
                {
                    "name": "shared_thing_name",
                    "class_iri": CLASS_BOOK,
                    "participants": [{"dataset_id": "ds-a", "predicate": PRED_CODE}],
                }
            ]
        }
    )
    assert autolink._concept_labels_for_config(Path("/nonexistent"), config, None) is None


@pytest.mark.asyncio
async def test_new_perspective_created_with_label_resolvers_writes_the_resolved_class_label(
    tmp_path: Path,
) -> None:
    """``label_resolvers`` を渡すと、実際に使われた concept のハブの種類の
    ``rdfs:label`` が R2 の結果になる（渡さない既存の呼び出し方は他のテスト
    で確認済み・出力は変わらない）。"""
    root = tmp_path / "registry"
    root.mkdir()
    a = _make_dataset(root, "book-loans", has_handle=True)
    b = _make_dataset(root, "book-reviews", has_handle=True)
    perspective_id = "shared-code"

    async def discover_stub(client, datasets, *, limits=None, only_slots=None):
        return {
            "candidates": [
                _candidate(perspective_id, "shared code", [_participant(a), _participant(b)])
            ]
        }

    client = _DatasetClient()
    # 両方のデータセットを実際に promoted な canonical graph にし、同じ値の
    # 三つ組を置く — concept が実際に "used" になり、ハブの種類の三つ組
    # （owl:Class + rdfs:label）が書かれる。
    for dsid in (a, b):
        key = substrate.canonical_graph_iri(dsid)
        g = client.ds.graph(rdflib.URIRef(key))
        g.add(
            (
                rdflib.URIRef(f"urn:{dsid}:item"),
                rdflib.URIRef(PRED_CODE),
                rdflib.Literal("C1"),
            )
        )
        client.ds.update(
            f"INSERT DATA {{ GRAPH <{substrate.CONTROL_GRAPH_IRI}> {{ "
            f'<{key}> <{substrate.STATUS_PREDICATE}> "promoted" }} }}'
        )

    report = await autolink.maybe_autolink_handles(
        client,
        root,
        a,
        discover=discover_stub,
        label_resolvers=_fake_label_resolvers("貸し出しコード"),
    )
    assert report["linked"], report["skipped"]

    hub_graph = crosswalk_runtime.crosswalk_graph_iri(perspective_id)
    labels = list(
        client.ds.graph(rdflib.URIRef(hub_graph)).objects(
            rdflib.URIRef(CLASS_BOOK), rdflib.RDFS.label
        )
    )
    assert [str(v) for v in labels] == ["貸し出しコード"]


@pytest.mark.asyncio
async def test_fake_build_is_never_given_label_resolvers(tmp_path: Path) -> None:
    """``build=`` に偽物を渡す既存のテストの流儀（固定シグネチャ）が壊れない
    こと — ``label_resolvers`` を渡しても偽物の呼び出しには現れない。"""
    root = tmp_path / "registry"
    root.mkdir()
    a = _make_dataset(root, "book-loans", has_handle=True)
    b = _make_dataset(root, "book-reviews", has_handle=True)
    perspective_id = "shared-code"

    async def discover_stub(client, datasets, *, limits=None, only_slots=None):
        return {
            "candidates": [
                _candidate(perspective_id, "shared code", [_participant(a), _participant(b)])
            ]
        }

    async def fake_build(client, registry_root, perspective_id, candidate) -> dict:
        return {"created": True, "participants_added": [{"dataset_id": a, "predicate": PRED_CODE}]}

    client = _DatasetClient()
    report = await autolink.maybe_autolink_handles(
        client,
        root,
        a,
        discover=discover_stub,
        build=fake_build,
        label_resolvers=_fake_label_resolvers("貸し出しコード"),
    )
    assert len(report["linked"]) == 1
