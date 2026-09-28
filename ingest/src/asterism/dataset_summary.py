"""1 データセットの要約（契約メモ contract_pr_f2.md §3.1）。

``GET /api/datasets/{dataset_id}/summary`` の薄い api ルート
（``asterism_api.dataset_summary_routes``）が、この 1 純関数 :func:`dataset_summary`
を呼ぶだけになるようにする — 「データセットのページ」の帯（データの意味を定義
する）と「この中のもの」の種類ごと件数が、どちらもここ 1 か所の出力から組み立つ。

数え方は既存の流儀の再利用のみ（新しい SPARQL の書き方を増やさない）:

* 種類ごとの件数は :func:`asterism.subject_tools.set_count` と同じ
  「DISTINCT ``?s`` を、そのデータセットの版グラフに限定して数える」形。
* 1 種類のプロパティ（項目・名前あり・単位あり）は
  :func:`asterism.class_schema.class_schema` の ``properties`` をそのまま数える。
* ライセンスは :func:`asterism.licenses.dataset_license`（正本は
  ``metadata.ttl`` の ``dcterms:license``）。
* データセットの表示名は :func:`asterism.subjects.resolve_dataset_label`
  （``metadata.ttl`` の ``dcterms:title`` → ``meta.json`` の ``name`` → id）。

promoted（公開済み）でないデータセットは、版グラフがまだ引用可能スコープに
無い（:func:`asterism.substrate.canonical_graphs` の定義そのもの）ので、
``classes`` は常に空リスト — 「データの意味を定義する」帯の
「▲ まだ取り込んでいません」表示は、この空リストではなく ``stage`` フィールド
で判定する（K39: 止めるのではなく帰結を言う — 空リストだけでは「0 種類」なの
か「未取り込み」なのか呼び出し側が区別できない）。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import yaml

from asterism import class_schema as class_schema_mod
from asterism import licenses as licenses_mod
from asterism import metadata as metadata_mod
from asterism import subjects as subjects_mod
from asterism.substrate import (
    SupportsSparql,
    canonical_from_clauses,
    canonical_graphs,
    dataset_id_of_canonical_graph,
    ontology_graph_iri,
)

logger = logging.getLogger(__name__)

__all__ = ["DEMO_DATASET_ID", "dataset_summary", "list_entry_extras"]

#: PROV-O namespace（統合時の所見 #1: 来歴のクラス — ``prov:Activity`` そのもの
#: や、データセットの ontology が定義した「その」サブクラス — は人が「この中の
#: もの」として扱うデータの種類ではないので ``classes`` から除く）。
_PROV_NS = "http://www.w3.org/ns/prov#"
_PROV_TYPES: tuple[str, str] = (f"{_PROV_NS}Activity", f"{_PROV_NS}Agent")

#: 見本データセットの id（``datasets/world/`` そのものの中身 — 分野固有名詞では
#: なく見本データの識別子そのものなので契約メモ §0 のとおり書いてよい。
#: ``api/src/asterism_api/local.py``（PR E）が唯一の見本データの取り込み経路
#: で、そこも同じ id を仮定している——``find_world_snapshot`` が探すのは常に
#: ``datasets/world/snapshot.tar``）。公開（api 層の一覧ルート・``local.py``
#: の seed が同じ判定を再利用できるように）。
DEMO_DATASET_ID = "world"


def _read_meta(registry_root: Path | str | None, dataset_id: str) -> dict[str, Any] | None:
    """``registry_root/dataset_id/meta.json``、無ければ ``None``（=存在しない
    データセット。api 層はこれを 404 にする）。"""
    if registry_root is None:
        return None
    meta_path = Path(registry_root) / dataset_id / "meta.json"
    if not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return meta if isinstance(meta, dict) else None


def _origin_of(meta: dict[str, Any]) -> str:
    """``meta.origin`` の読み手（``asterism_api.registry.dataset_origin`` と同じ
    3 値ルール — その関数は api 層なのでここでは持てず、同じ小さな判定を
    duplicate する。§0 の既存の流儀（``asterism.materials._lookup_origin`` も
    同様）。"""
    origin = meta.get("origin")
    return origin if origin in ("own", "open") else "unknown"


def _stage_of(meta: dict[str, Any]) -> str:
    """``ui/src/galleryApi.ts``'s ``datasetStage`` と同じ判定（``meta.promoted``
    → ``meta.ingested`` → それ以外は ``"design"``）— 契約メモ §3.1 の
    「既存の stage 語をそのまま」。"""
    if meta.get("promoted"):
        return "promoted"
    if meta.get("ingested"):
        return "ingested"
    return "design"


def _snapshot_of(meta: dict[str, Any]) -> str | None:
    """``asterism.class_schema``'s ``_schema_from_registry`` と同じ規則:
    ``meta.version``（re-promote のたびに増える単調カウンタ）を ``"v{n}"`` に。
    0/欠落は「まだ公開されていない」= ``None``。"""
    version = meta.get("version")
    return f"v{version}" if isinstance(version, int) and version > 0 else None


def _source_note(registry_root: Path, dataset_id: str) -> str | None:
    """``mie.yaml``'s ``schema_info.source``（既知キーではないので
    :mod:`asterism.metadata` の投影往復では ``schema_info`` の残余として
    素通しされる — 契約メモ §3.1 の「出どころの短い説明」に充てられる唯一の
    任意フィールド）。無ければ ``None``（同梱データセットの ``mie.yaml`` は
    今のところこのキーを持たない — 契約メモの JSON 例は将来この欄が埋まった
    ときの見え方の例示と読む）。"""
    mie_path = registry_root / dataset_id / "mie.yaml"
    if not mie_path.is_file():
        return None
    text = mie_path.read_text(encoding="utf-8")
    if not text.strip():
        return None
    try:
        doc = metadata_mod.parse_mie_yaml(text)
    except (yaml.YAMLError, ValueError):
        return None
    schema_info = doc.get("schema_info")
    if isinstance(schema_info, dict):
        value = schema_info.get("source")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def list_entry_extras(meta: dict[str, Any]) -> dict[str, Any]:
    """``GET /api/datasets``（一覧）の 1 要素に足す 3 フィールド
    （契約メモ §3.4） — ``origin``/``stage``/``is_demo``。

    api 層の一覧ルート（``asterism_api.main.list_datasets``）は
    ``registry.list_datasets`` が返す生の meta dict をそのまま持っているので、
    呼び出し側は ``{**item, **list_entry_extras(item)}`` で足すだけでよい
    （既存フィールドは削らない）。
    """
    dataset_id = meta.get("id")
    return {
        "origin": _origin_of(meta),
        "stage": _stage_of(meta),
        "is_demo": dataset_id == DEMO_DATASET_ID,
    }


def _ref(iri: str) -> str | None:
    safe = subjects_mod.safe_iri(iri)
    return f"<{safe}>" if safe else None


async def _run_select(client: SupportsSparql, query: str) -> list[dict[str, dict[str, Any]]]:
    raw = await client.sparql_select(query)
    results = raw.get("results", {}) if isinstance(raw, dict) else {}
    return list(results.get("bindings", []) if isinstance(results, dict) else [])


def _cell(row: dict[str, dict[str, Any]], var: str) -> str | None:
    node = row.get(var)
    return node.get("value") if node else None


async def _classes_in_dataset(client: SupportsSparql, graphs: list[str]) -> list[str]:
    """``rdf:type`` の値（このデータセットの版グラフに限定・辞書順）——
    :func:`asterism.subject_tools.pick_class_iri` と同じ ``?s a ?class`` 集計
    の「1 データセット全体」版。"""
    if not graphs:
        return []
    from_clause = canonical_from_clauses(graphs)
    query = f"SELECT DISTINCT ?class\n{from_clause}WHERE {{ ?s a ?class }} ORDER BY ?class"
    rows = await _run_select(client, query)
    return [c for row in rows if (c := _cell(row, "class"))]


async def _prov_excluded_classes(
    client: SupportsSparql, graphs: list[str], ontology_graph: str
) -> set[str]:
    """来歴（PROV）由来のクラス IRI の集合 — 決定論 2 規則:

    (a) そのクラスの実例が版グラフ内で ``prov:Activity``/``prov:Agent`` としても
        型付けされている（``?s a ?class ; a ?provType`` の 1 本の SELECT）。
    (b) データセットの ontology（projected TBox）グラフで
        ``rdfs:subClassOf prov:Activity|prov:Agent`` と宣言されている。

    クラス IRI 自体が ``prov:`` 名前空間かどうかは呼び出し側が ``str.startswith``
    で見る（クエリ不要）。
    """
    excluded: set[str] = set()
    types_list = ", ".join(f"<{t}>" for t in _PROV_TYPES)

    if graphs:
        from_clause = canonical_from_clauses(graphs)
        query = (
            "SELECT DISTINCT ?class\n"
            f"{from_clause}"
            f"WHERE {{ ?s a ?class ; a ?provType . FILTER(?provType IN ({types_list})) }}"
        )
        rows = await _run_select(client, query)
        excluded.update(c for row in rows if (c := _cell(row, "class")))

    from_clause = canonical_from_clauses([ontology_graph])
    query = (
        "SELECT DISTINCT ?class\n"
        f"{from_clause}"
        "WHERE { ?class <http://www.w3.org/2000/01/rdf-schema#subClassOf> ?provType . "
        f"FILTER(?provType IN ({types_list})) }}"
    )
    rows = await _run_select(client, query)
    excluded.update(c for row in rows if (c := _cell(row, "class")))

    return excluded


async def _class_count(client: SupportsSparql, graphs: list[str], class_iri: str) -> int:
    """``asterism.subject_tools.set_count`` と同じ形（``where`` 節が空の場合）:
    このデータセットの版グラフに限定した ``COUNT(DISTINCT ?s)``。"""
    ref = _ref(class_iri)
    if ref is None or not graphs:
        return 0
    from_clause = canonical_from_clauses(graphs)
    query = f"SELECT (COUNT(DISTINCT ?s) AS ?value)\n{from_clause}WHERE {{ ?s a {ref} }}"
    rows = await _run_select(client, query)
    if not rows:
        return 0
    try:
        return int(float(_cell(rows[0], "value") or 0))
    except (TypeError, ValueError):
        return 0


def _label_unit_counts(properties: list[Any]) -> tuple[int, int, int]:
    """``(properties, with_label, with_unit)`` — ``class_schema`` が返す
    ``properties[]`` をそのまま数える（契約メモ §3.1: 「properties / with_label
    / with_unit は class_schema の properties から数える」の文字どおりの実装。
    ``class_schema`` は名前を判定できなかった性質にも常に何らかの表示名
    （IRI のローカル名を人間化したもの）を返す設計のため、"名前あり" は「AI/
    設計が本当に名づけたか」までは区別しない——その区別は class_schema 自身
    （このモジュールの担当外）が properties[] に印を持たない限りできない）。"""
    total = 0
    with_label = 0
    with_unit = 0
    for prop in properties:
        if not isinstance(prop, dict):
            continue
        total += 1
        label = prop.get("label")
        if isinstance(label, str) and label.strip():
            with_label += 1
        if prop.get("unit"):
            with_unit += 1
    return total, with_label, with_unit


async def _class_entry(
    client: SupportsSparql,
    registry_root: Path | str | None,
    graphs: list[str],
    class_iri: str,
) -> dict[str, Any]:
    schema = await class_schema_mod.class_schema(client, registry_root, class_iri)
    if isinstance(schema, dict):
        label = schema.get("label") or None
        properties = schema.get("properties")
        properties = properties if isinstance(properties, list) else []
    else:
        label = None
        properties = []
    if not label:
        label = await class_schema_mod.class_label(client, registry_root, class_iri)
    total, with_label, with_unit = _label_unit_counts(properties)
    count = await _class_count(client, graphs, class_iri)
    return {
        "class_iri": class_iri,
        "label": label,
        "count": count,
        "properties": total,
        "with_label": with_label,
        "with_unit": with_unit,
    }


async def dataset_summary(
    client: SupportsSparql, registry_root: Path | str | None, dataset_id: str
) -> dict[str, Any] | None:
    """契約メモ §3.1 の JSON を組み立てる。不正な ``dataset_id`` /
    存在しないデータセットは ``None``（api 層はどちらも 404 にする）。"""
    safe_id = subjects_mod.valid_dataset_id(dataset_id)
    if safe_id is None:
        return None
    meta = _read_meta(registry_root, safe_id)
    if meta is None:
        return None

    stage = _stage_of(meta)
    root = Path(registry_root) if registry_root is not None else None

    classes: list[dict[str, Any]] = []
    if stage == "promoted":
        all_graphs = await canonical_graphs(client)
        graphs = [g for g in all_graphs if dataset_id_of_canonical_graph(g) == safe_id]
        if graphs:
            excluded = await _prov_excluded_classes(client, graphs, ontology_graph_iri(safe_id))
            for class_iri in await _classes_in_dataset(client, graphs):
                if class_iri.startswith(_PROV_NS) or class_iri in excluded:
                    continue
                classes.append(await _class_entry(client, registry_root, graphs, class_iri))

    license_value: str | None = None
    if root is not None:
        license_value, _redistributable = licenses_mod.dataset_license(root, safe_id)

    return {
        "dataset_id": safe_id,
        "label": subjects_mod.resolve_dataset_label(registry_root, safe_id),
        "origin": _origin_of(meta),
        "stage": stage,
        "license": license_value,
        "snapshot": _snapshot_of(meta),
        "source_note": _source_note(root, safe_id) if root is not None else None,
        "classes": classes,
        "is_demo": safe_id == DEMO_DATASET_ID,
    }
