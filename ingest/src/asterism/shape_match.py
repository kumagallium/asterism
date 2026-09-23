"""形の一致 — 「データを置く」画面が使う決定論（契約メモ contract_pr_c.md §4）。

アップロードされた表の列名を、promoted 済みデータセットが既に持っている「型」
（class 1 つぶんの TriplesMap）の列と突き合わせて、どの型に置けるかを言い当てる。
設計 (propose/materialize) も LLM も通さない — mapping.yaml をそのまま読むだけ。

分野固有の語彙は一切書かない。列名・ラベル・IRI はすべて読んだ mapping.yaml /
ストアからそのまま出てくる文字列。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from asterism.mapping_ir_read import (
    BUILTIN_PREFIXES,
    MappingIRReadError,
    TriplesMapView,
    read_mapping_ir,
    template_placeholders,
)
from asterism.subjects import label_union_clause, pick_label
from asterism.substrate import SupportsSparql, canonical_merge_query

__all__ = [
    "ColumnSignature",
    "MissingKeyColumnsError",
    "ShapeMatch",
    "TypeSignature",
    "match_shape",
    "match_subjects",
    "prune_mapping_ir_yaml",
    "type_signatures",
]

_ID_RE = re.compile(r"[a-z0-9-]{1,128}")
_META_FILE = "meta.json"
_MAPPING_FILE = "mapping.yaml"

# 列名/ラベルの正規化: 末尾の単位表記 `[unit]`/`(unit)` を外す・英数以外を落とす。
_UNIT_SUFFIX = re.compile(r"[\[\(][^\[\]\(\)]*[\]\)]\s*$")
_NON_ALNUM = re.compile(r"[^0-9a-z]+")

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _normalize(name: str) -> str:
    """列名/ラベルの正規化（§4.1）: 小文字化・末尾の単位表記を外す・英数以外を落とす。"""
    s = (name or "").strip().lower()
    s = _UNIT_SUFFIX.sub("", s).strip()
    return _NON_ALNUM.sub("", s)


def _expand(term: str, prefixes: dict[str, str]) -> str:
    """CURIE → 完全 IRI（すでに展開済みならそのまま）。"""
    prefix, sep, rest = term.partition(":")
    if sep and prefix in prefixes:
        return prefixes[prefix] + rest
    return term


def _local_name(iri: str) -> str:
    for sep in (":", "/", "#"):
        if sep in iri:
            iri = iri.rsplit(sep, 1)[-1]
    return iri


def _humanize(local: str) -> str:
    if not local:
        return ""
    spaced = _CAMEL_BOUNDARY.sub(" ", local.replace("_", " ").replace("-", " "))
    return " ".join(spaced.split())


def _fallback_label(iri: str) -> str:
    return _humanize(_local_name(iri)) or _local_name(iri)


# ---------------------------------------------------------------------------
# TypeSignature — 引き継ぎ書 §5.6 / 契約メモ §4.1
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ColumnSignature:
    column: str
    label: str | None
    unit: str | None


@dataclass(frozen=True)
class TypeSignature:
    type_id: str
    dataset_id: str
    key_columns: tuple[str, ...]
    columns: tuple[ColumnSignature, ...]
    template: str
    # 契約メモ §4.1 の形には無いが、match_subjects が「述語のある照合」をする
    # ために必要（key 列を properties[] にも書いている map からだけ拾える）。
    # 無いことは正常（フォールバックで template 埋めの照合になる）— 逸脱として
    # notes に明記。
    key_predicate: str | None = None


@dataclass(frozen=True)
class ShapeMatch:
    type_id: str | None
    dataset_id: str | None
    matched_columns: tuple[str, ...]
    unmatched_columns: tuple[str, ...]
    confidence: float


def _map_to_signature(
    dataset_id: str, m: TriplesMapView, prefixes: dict[str, str]
) -> TypeSignature | None:
    if not m.subject_template or not m.subject_classes:
        return None
    key_columns = tuple(dict.fromkeys(template_placeholders(m.subject_template)))
    if not key_columns:
        return None
    columns: list[ColumnSignature] = []
    seen: set[str] = set()
    key_predicate: str | None = None
    for p in m.properties:
        if p.column and p.column not in seen:
            seen.add(p.column)
            columns.append(ColumnSignature(column=p.column, label=p.label, unit=p.unit))
        if p.column == key_columns[0] and p.predicate and key_predicate is None:
            key_predicate = _expand(p.predicate, prefixes)
    return TypeSignature(
        type_id=_expand(m.subject_classes[0], prefixes),
        dataset_id=dataset_id,
        key_columns=key_columns,
        columns=tuple(columns),
        # CURIE プレフィクスを展開した完全 IRI テンプレート（``{col}`` プレース
        # ホルダはそのまま残る — ``_expand`` は先頭の ``prefix:`` だけを置き換える
        # 単純な文字列置換）。展開し忘れると match_subjects のフォールバック経路や
        # commit の「棚とつながらなかった行に新しい IRI を鋳造する」処理が
        # "exr:plant/P-01" のような CURIE 文字列を IRI として使ってしまう。
        template=_expand(m.subject_template, prefixes),
        key_predicate=key_predicate,
    )


def type_signatures(registry_root: Path) -> list[TypeSignature]:
    """promoted データセットの ``mapping.yaml`` から型の形の一覧を作る（決定論）。

    データセット id の辞書順に読むので、同じ入力なら常に同じ順で返る
    （:func:`match_shape` の同点タイブレークが安定する）。
    """
    out: list[TypeSignature] = []
    if not registry_root.is_dir():
        return out
    for child in sorted(registry_root.iterdir(), key=lambda p: p.name):
        if not child.is_dir() or not _ID_RE.fullmatch(child.name):
            continue
        meta_path = child / _META_FILE
        mapping_path = child / _MAPPING_FILE
        if not meta_path.is_file() or not mapping_path.is_file():
            continue
        try:
            meta: Any = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(meta, dict) or not meta.get("promoted"):
            continue
        try:
            text = mapping_path.read_text(encoding="utf-8")
        except OSError:
            continue
        if not text.strip():
            continue
        try:
            ir = read_mapping_ir(text)
        except MappingIRReadError:
            continue
        prefixes = dict(BUILTIN_PREFIXES) | dict(ir.prefixes)
        for m in ir.maps:
            sig = _map_to_signature(child.name, m, prefixes)
            if sig is not None:
                out.append(sig)
    return out


class MissingKeyColumnsError(MappingIRReadError):
    """置いたファイルに、この型の ID を組み立てる列が無い（§4.3 の「置く」が
    参照する ``prune_mapping_ir_yaml`` 専用の例外 — 呼び出し側はこれを 400 の
    「この形では ID の列が足りません」に変換する）。"""

    def __init__(self, columns: tuple[str, ...]):
        self.columns = columns
        super().__init__(f"missing id column(s) for this shape: {', '.join(columns)}")


def _property_refs(prop: dict[str, Any]) -> set[str]:
    """1 property 行が参照する列名の集合（``column`` / ``columns`` /
    ``object_template`` の ``{列名}`` / ``args`` の値の中の ``{列名}``）。
    どの列も参照しない行（定数リテラル等）は空集合 — 常に残る。"""
    refs: set[str] = set()
    column = prop.get("column")
    if isinstance(column, str) and column:
        refs.add(column)
    for c in prop.get("columns") or []:
        if isinstance(c, str) and c:
            refs.add(c)
    refs.update(template_placeholders(str(prop.get("object_template") or "")))
    args = prop.get("args")
    if isinstance(args, dict):
        for v in args.values():
            refs.update(template_placeholders(str(v)))
    return refs


def prune_mapping_ir_yaml(text: str, map_name_or_class: str, columns: list[str]) -> str:
    """署名の TriplesMap だけを残し、参照する列がすべて ``columns``（置いた
    ファイルの列）に含まれる property 行だけを残した ``mapping.yaml`` テキストを
    返す（O5 の部分一致 = 一致した列だけで置く）。純関数・決定論。キーの順序と
    ``maps``/``properties`` 以外のトップレベルのキーはそのまま保つ。

    ``map_name_or_class`` は map の ``name`` か、その ``subject.classes`` の
    どれか（CURIE のままでも展開後の完全 IRI でもよい）と一致するもの 1 つを選ぶ。
    一致する map が無ければ :class:`MappingIRReadError`。選んだ map の subject
    template が要求する列（ID の列）が ``columns`` に無ければ
    :class:`MissingKeyColumnsError`。

    列名の一致は :func:`_normalize`（§4.1 と同じ規則: 小文字化・末尾の単位表記を
    外す・英数以外を落とす）で判定する。
    """
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise MappingIRReadError(f"invalid YAML: {exc}") from exc
    if not isinstance(doc, dict):
        raise MappingIRReadError("mapping.yaml must be a mapping at the top level")
    maps_raw = doc.get("maps")
    if not isinstance(maps_raw, list):
        raise MappingIRReadError("maps must be a list")
    prefixes = dict(BUILTIN_PREFIXES) | {
        str(k): str(v) for k, v in (doc.get("prefixes") or {}).items()
    }

    target: dict[str, Any] | None = None
    for m in maps_raw:
        if not isinstance(m, dict):
            continue
        if m.get("name") == map_name_or_class:
            target = m
            break
        subject = m.get("subject") or {}
        classes = [c for c in (subject.get("classes") or []) if isinstance(c, str)]
        if map_name_or_class in classes or map_name_or_class in {
            _expand(c, prefixes) for c in classes
        }:
            target = m
            break
    if target is None:
        raise MappingIRReadError(f"map {map_name_or_class!r} not found in mapping.yaml")

    input_norms = {_normalize(c) for c in columns} - {""}

    subject = target.get("subject") or {}
    subject_cols = template_placeholders(str(subject.get("template") or ""))
    missing_id_columns = tuple(c for c in subject_cols if _normalize(c) not in input_norms)
    if missing_id_columns:
        raise MissingKeyColumnsError(missing_id_columns)

    kept_properties = [
        prop
        for prop in (target.get("properties") or [])
        if isinstance(prop, dict)
        and all(_normalize(r) in input_norms for r in _property_refs(prop))
    ]

    new_map = dict(target)
    new_map["properties"] = kept_properties
    new_doc = dict(doc)
    new_doc["maps"] = [new_map]
    return yaml.safe_dump(new_doc, sort_keys=False, allow_unicode=True)


def match_shape(columns: list[str], candidates: list[TypeSignature]) -> ShapeMatch:
    """アップロードされた表の列名と、既知の型の候補群を突き合わせる（§4.1）。

    ``type_id`` を返す条件: key_columns が全部一致 ∧ matched が 2 列以上。
    複数が条件を満たせば confidence 最大、同点は dataset_id の辞書順。
    部分一致（unmatched あり）でも一致列だけで置ける ``type_id`` を返す。

    「一致」は §4.1 の通り**列名と label の両方**で判定する（key 列の判定も同じ
    ものさし — でなければ「key 列は label でしか出てこない表」が絶対に置けなく
    なる）。正規化後に空文字列になる列名/label（絵文字だけ・非英数字だけ、等）は
    互いに無関係な列を偶然一致させてしまうので、一致の根拠として使わない。
    """
    input_norms = {_normalize(c) for c in columns} - {""}
    best: TypeSignature | None = None
    best_matched: list[str] = []
    best_unmatched: list[str] = []
    best_confidence = -1.0
    for sig in candidates:
        target_norms: dict[str, str] = {}
        for cs in sig.columns:
            col_norm = _normalize(cs.column)
            if col_norm:
                target_norms.setdefault(col_norm, cs.column)
            if cs.label:
                label_norm = _normalize(cs.label)
                if label_norm:
                    target_norms.setdefault(label_norm, cs.column)
        matched = [c for c in columns if _normalize(c) and _normalize(c) in target_norms]
        unmatched = [c for c in columns if c not in matched]
        confidence = round(len(matched) / len(columns), 2) if columns else 0.0
        # key 列は「入力に同名の列がある」か「入力のどれかの列/label が正規化して
        # その key 列に一致した (matched に含まれる)」のどちらかで満たされる —
        # 前者は key 列が properties[] に出てこない（label が無い）場合のフォール
        # バック、後者が label 経由の一致。
        matched_target_columns = {target_norms[_normalize(c)] for c in matched}
        key_ok = all(
            _normalize(k) in input_norms or k in matched_target_columns
            for k in sig.key_columns
        )
        if not (sig.key_columns and key_ok and len(matched) >= 2):
            continue
        if (
            best is None
            or confidence > best_confidence
            or (confidence == best_confidence and sig.dataset_id < best.dataset_id)
        ):
            best = sig
            best_matched = matched
            best_unmatched = unmatched
            best_confidence = confidence
    if best is None:
        return ShapeMatch(
            type_id=None,
            dataset_id=None,
            matched_columns=(),
            unmatched_columns=tuple(columns),
            confidence=0.0,
        )
    return ShapeMatch(
        type_id=best.type_id,
        dataset_id=best.dataset_id,
        matched_columns=tuple(best_matched),
        unmatched_columns=tuple(best_unmatched),
        confidence=best_confidence,
    )


# ---------------------------------------------------------------------------
# match_subjects — 1 件の照合（§4.2、SubjectItem の 3 状態）
# ---------------------------------------------------------------------------


def _norm_value(s: str) -> str:
    return " ".join(s.strip().lower().split())


# SPARQL 1.1 の IRIREF 文法 (`<...>`) はこの集合の文字を一切許さない —
# `<`/`>`/`"`/`{`/`}`/`|`/`^`/`\` に加えて空白・制御文字（0x00-0x20）。この
# どれか 1 文字でも生きたまま `<...>` の中に文字列連結で埋めれば、IRIREF を
# 早期に閉じて任意の SPARQL 節を注入できる（契約メモ §0「文字列連結で生の値を
# 埋めない」）。単純な `<`/`>` だけのストリップ（``asterism.subject_tools._safe_iri``
# と同じ流儀）では防ぎきれない — 例えば `}` や `#`（コメント開始）が残る。
_UNSAFE_IRI_CHARS = re.compile(r'[<>"{}|^`\\\x00-\x20]')


def _safe_iri(iri: str) -> str | None:
    """``iri`` がそのまま ``<...>`` に埋め込んでよければそれを、危険な文字を
    含んでいれば ``None`` を返す。

    key_predicate 経路の ``iri`` は store 自身が返した（すでに妥当な）IRI か
    registry の mapping.yaml 由来の述語 IRI なので理屈上いつも安全なはずだが、
    フォールバック経路の ``iri`` はアップロードされた（信頼できない）CSV セル値を
    subject template に埋め込んだもの — こちらが本題。呼び出し側は ``None`` を
    「この候補はクエリに含めない（= 一致しないものとして扱う）」の意味で使う。
    """
    return None if _UNSAFE_IRI_CHARS.search(iri) else iri


def _safe_iris(iris: set[str]) -> list[str]:
    """危険な文字を含む候補を黙って除外した、決定論の並び（辞書順）。"""
    return sorted(iri for iri in iris if _safe_iri(iri) is not None)


async def _run_select(client: SupportsSparql, query: str) -> list[dict[str, dict[str, Any]]]:
    scoped = await canonical_merge_query(client, query)
    data = await client.sparql_select(scoped)
    results = data.get("results", {}) if isinstance(data, dict) else {}
    return results.get("bindings", []) if isinstance(results, dict) else []


async def _subject_triple_counts(client: SupportsSparql, iris: set[str]) -> dict[str, int]:
    safe = _safe_iris(iris)
    if not safe:
        return {}
    values_clause = " ".join(f"<{iri}>" for iri in safe)
    query = (
        "SELECT ?s (COUNT(*) AS ?n) WHERE { "
        f"VALUES ?s {{ {values_clause} }} "
        "?s ?p ?o . "
        "} GROUP BY ?s"
    )
    out: dict[str, int] = {}
    for b in await _run_select(client, query):
        s = b.get("s", {})
        n = b.get("n", {})
        if s.get("type") != "uri" or "value" not in s:
            continue
        try:
            out[s["value"]] = int(float(n.get("value", 0)))
        except (TypeError, ValueError):
            out[s["value"]] = 0
    return out


async def _labels_of(client: SupportsSparql, iris: set[str]) -> dict[str, str]:
    """place/subjects の ambiguous 候補ラベル — ``asterism.subjects`` の共通
    優先順位（§2）を使う（以前は rdfs:label／http の schema:name の 2 つしか
    見ておらず、https の schema:name／dcterms:title／skos:prefLabel／
    foaf:name しか無い候補がラベル無し＝IRI 末尾になっていた）。"""
    safe = _safe_iris(iris)
    if not safe:
        return {}
    values_clause = " ".join(f"<{iri}>" for iri in safe)
    optional = label_union_clause(
        "?s", label_var="?label", predicate_var="?__lp", rank_var="?__rank"
    )
    query = (
        f"SELECT ?s ?label ?__rank (LANG(?label) AS ?__lang) WHERE {{ "
        f"VALUES ?s {{ {values_clause} }} {optional} }}"
    )
    candidates: dict[str, list[tuple[str | None, int | None, str | None]]] = {i: [] for i in safe}
    for b in await _run_select(client, query):
        s = b.get("s", {}).get("value")
        if s is None or s not in candidates:
            continue
        rank_raw = b.get("__rank", {}).get("value")
        rank = int(rank_raw) if rank_raw is not None else None
        lang = b.get("__lang", {}).get("value")
        candidates[s].append((b.get("label", {}).get("value"), rank, lang))
    out: dict[str, str] = {}
    for s, cands in candidates.items():
        label = pick_label(cands)
        if label:
            out[s] = label
    return out


def _classify(
    value: str, iris: list[str], labels: dict[str, str], counts: dict[str, int]
) -> dict[str, Any]:
    if not iris:
        return {"value": value, "match": "own_only", "iri": None, "candidates": []}
    if len(iris) == 1:
        return {"value": value, "match": "linked", "iri": iris[0], "candidates": []}
    candidates = [
        {"iri": iri, "label": labels.get(iri) or _fallback_label(iri), "count": counts.get(iri, 0)}
        for iri in iris
    ]
    return {"value": value, "match": "ambiguous", "iri": None, "candidates": candidates}


async def match_subjects(
    client: SupportsSparql, signature: TypeSignature, values: list[str]
) -> list[dict[str, Any]]:
    """1 件の照合（引き継ぎ書 §5.4 の 3 状態: linked / ambiguous / own_only）。

    戻り値は入力 ``values``（先頭 500 件・重複除去）と同じ順の
    ``[{value, match, iri, candidates}]``。

    ``signature.key_predicate`` があれば「``?s <predicate> ?v``」で既存データの
    同じ列の値と突き合わせる（複数 ``?s`` が同じ値を持てば ambiguous）。無ければ
    （§4.2 のフォールバック）subject template に値を埋めた IRI が既に棚にあるか
    どうかだけを見る — この経路は 1 対 1 なので ambiguous を検出できない。
    """
    dedup = list(dict.fromkeys(v for v in values if v and v.strip()))[:500]
    if not dedup:
        return []

    safe_key_predicate = (
        _safe_iri(signature.key_predicate) if signature.key_predicate is not None else None
    )
    if signature.key_predicate is not None and safe_key_predicate is None:
        # 述語 IRI 自体が壊れていて安全に埋め込めない（registry の mapping.yaml が
        # 壊れている場合のみ起こりうる）— 誤って何かにヒットするより、判定不能を
        # own_only として安全側に倒す。
        return [{"value": v, "match": "own_only", "iri": None, "candidates": []} for v in dedup]

    if safe_key_predicate is not None:
        query = f"SELECT ?s ?v WHERE {{ ?s <{safe_key_predicate}> ?v . }}"
        by_norm: dict[str, list[str]] = {}
        for b in await _run_select(client, query):
            s = b.get("s", {})
            v = b.get("v", {})
            if s.get("type") != "uri" or "value" not in v:
                continue
            by_norm.setdefault(_norm_value(str(v["value"])), []).append(s["value"])
        all_iris = {iri for iris in by_norm.values() for iri in iris}
        counts = await _subject_triple_counts(client, all_iris)
        # ラベルが要るのは ambiguous（候補が 2 つ以上）の値だけ。
        ambiguous_iris = {
            iri
            for value in dedup
            for iri in {i for i in by_norm.get(_norm_value(value), []) if counts.get(i, 0) > 0}
            if len({i for i in by_norm.get(_norm_value(value), []) if counts.get(i, 0) > 0}) > 1
        }
        labels = await _labels_of(client, ambiguous_iris)
        return [
            _classify(
                value,
                sorted({i for i in by_norm.get(_norm_value(value), []) if counts.get(i, 0) > 0}),
                labels,
                counts,
            )
            for value in dedup
        ]

    placeholders = template_placeholders(signature.template)
    if len(placeholders) != 1:
        # 複合キーの subject template は Phase 1 では照合できない（逸脱として
        # notes に明記）— own_only 扱いにして、棚とはつなげず自分のデータとして
        # 置けるようにする。
        return [{"value": v, "match": "own_only", "iri": None, "candidates": []} for v in dedup]
    slot = "{" + placeholders[0] + "}"
    iri_of = {v: signature.template.replace(slot, v) for v in dedup}
    counts = await _subject_triple_counts(client, set(iri_of.values()))
    results: list[dict[str, Any]] = []
    for v in dedup:
        iri = iri_of[v]
        if counts.get(iri, 0) > 0:
            results.append({"value": v, "match": "linked", "iri": iri, "candidates": []})
        else:
            results.append({"value": v, "match": "own_only", "iri": None, "candidates": []})
    return results
