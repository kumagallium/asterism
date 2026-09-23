"""最小限の Mapping IR 読み手（``asterism.class_schema`` §2 専用）。

``asterism_step0.mapping_ir.parse_mapping_ir`` は本来 ingest 層が読む「レビュー済み
mapping.yaml」に対する唯一の解釈者だが、``asterism-step0`` パッケージ自体は
``asterism-ingest`` に依存している（``asterism_step0.mapping_ir.catalog_from_registry``
が ``asterism.functions`` を import する等）。逆方向に ingest が
``asterism_step0`` を import すると循環依存になり、`uv pip install -e
'.[dev,substrate]'`（``[tool.uv.sources]`` を読まない CI の ingest ジョブ）では
``asterism-step0`` が解決できず ingest 全体のテスト収集が ImportError で落ちる。

この module は ingest 層の中だけで完結する、独立した最小限の読み手 — 厳格な
検証・関数カタログ照合・self-correction 用のエラー整形（それは propose/materialize
＝ step0 の責務）は一切行わず、``asterism.class_schema`` が §2 の優先順位①で
必要とするフィールドだけを ``yaml.safe_load`` で読む純関数。壊れた YAML や
mapping-IR でない形は :class:`MappingIRReadError`。

分野固有の語彙は一切書かない — 読んだ mapping.yaml の文字列をそのまま返すだけ。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import yaml

__all__ = [
    "BUILTIN_PREFIXES",
    "MappingIRReadError",
    "MappingIRView",
    "PropertyView",
    "TriplesMapView",
    "expand",
    "read_mapping_ir",
    "template_placeholders",
]

#: 宣言が無くても使える組み込みプレフィクス（コンパイラが常に宣言するもの）—
#: ``asterism_step0.mapping_ir.BUILTIN_PREFIXES`` と同じ 1 エントリ。
BUILTIN_PREFIXES: dict[str, str] = {
    "xsd": "http://www.w3.org/2001/XMLSchema#",
}

# ``{column}`` プレースホルダ（エスケープされた ``\{`` は対象外）— 同じ形を
# ``asterism_step0.mapping_ir._PLACEHOLDER`` / ``asterism.substrate._TEMPLATE_REF``
# も使う。
_PLACEHOLDER = re.compile(r"(?<!\\)\{([^{}]+)\}")


class MappingIRReadError(ValueError):
    """``mapping.yaml`` テキストが壊れた YAML、または mapping-IR の形でない。"""


@dataclass(frozen=True)
class PropertyView:
    """1 predicate-object 行のうち、class_schema が必要とするフィールドだけ。"""

    predicate: str | None
    column: str | None = None
    columns: tuple[str, ...] = ()
    object_template: str | None = None
    object_type: str | None = None
    datatype: str | None = None
    label: str | None = None
    unit: str | None = None


@dataclass(frozen=True)
class TriplesMapView:
    """1 TriplesMap のうち、class_schema が必要とするフィールドだけ。"""

    name: str
    source: str | None
    subject_template: str | None
    subject_classes: tuple[str, ...]
    subject_columns: tuple[str, ...]
    """``subject_template`` の ``{col}`` プレースホルダ -- 出現順・重複除去。"""
    properties: tuple[PropertyView, ...]


@dataclass(frozen=True)
class MappingIRView:
    prefixes: dict[str, str]
    maps: tuple[TriplesMapView, ...]


def expand(prefixes: dict[str, str], term: str) -> str:
    """CURIE（``prefix:local``）→ 完全 IRI。展開できない／既に完全 IRI ならそのまま
    （``asterism_step0.mapping_ir`` の同名の展開規則と同じ）。"""
    if not isinstance(term, str):
        return term
    prefix, sep, rest = term.partition(":")
    if sep and prefix in prefixes:
        return prefixes[prefix] + rest
    return term


def _template_columns(template: str | None) -> tuple[str, ...]:
    if not template:
        return ()
    seen: dict[str, None] = {}
    for m in _PLACEHOLDER.finditer(template):
        seen.setdefault(m.group(1), None)
    return tuple(seen)


def template_placeholders(template: str | None) -> tuple[str, ...]:
    """Public alias of :func:`_template_columns` — the ``{col}`` placeholders
    of a subject/object template, in first-occurrence order, duplicates
    removed. Same name/shape as
    ``asterism_step0.mapping_ir.template_placeholders`` (kept in sync by
    hand — this module does not import step0, see the module docstring),
    so ``asterism.shape_match`` can call it without depending on step0."""
    return _template_columns(template)


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _str_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(v for v in value if isinstance(v, str))


def _read_property(raw: Any, *, map_index: int, prop_index: int) -> PropertyView:
    if not isinstance(raw, dict):
        raise MappingIRReadError(f"maps[{map_index}].properties[{prop_index}] must be a mapping")
    return PropertyView(
        predicate=_str_or_none(raw.get("predicate")),
        column=_str_or_none(raw.get("column")),
        columns=_str_tuple(raw.get("columns")),
        object_template=_str_or_none(raw.get("object_template")),
        object_type=_str_or_none(raw.get("object_type")),
        datatype=_str_or_none(raw.get("datatype")),
        label=_str_or_none(raw.get("label")),
        unit=_str_or_none(raw.get("unit")),
    )


def _read_map(raw: Any, *, index: int) -> TriplesMapView:
    if not isinstance(raw, dict):
        raise MappingIRReadError(f"maps[{index}] must be a mapping")
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise MappingIRReadError(f"maps[{index}].name is required")
    subject_raw = raw.get("subject")
    if not isinstance(subject_raw, dict):
        raise MappingIRReadError(f"maps[{index}].subject is required")
    template = _str_or_none(subject_raw.get("template"))
    properties_raw = raw.get("properties") or []
    if not isinstance(properties_raw, list):
        raise MappingIRReadError(f"maps[{index}].properties must be a list")
    properties = tuple(
        _read_property(p, map_index=index, prop_index=i) for i, p in enumerate(properties_raw)
    )
    return TriplesMapView(
        name=name,
        source=_str_or_none(raw.get("source")),
        subject_template=template,
        subject_classes=_str_tuple(subject_raw.get("classes")),
        subject_columns=_template_columns(template),
        properties=properties,
    )


def read_mapping_ir(text: str) -> MappingIRView:
    """``mapping.yaml`` テキスト → :class:`MappingIRView`。

    厳密な mapping-IR 検証（``asterism_step0.mapping_ir.parse_mapping_ir``）は
    行わない — 既にレビューを通って ``registry/<id>/mapping.yaml`` に置かれた
    ものを読むだけ。壊れた YAML、トップレベルが mapping でない、``maps`` が
    無い/リストでない、``maps[].name``/``.subject`` が無い、のいずれも
    :class:`MappingIRReadError`（``ValueError`` — 呼び出し側は best-effort で
    ``except Exception`` を使う既存の流儀にそのまま乗る）。
    """
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise MappingIRReadError(f"invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise MappingIRReadError("mapping.yaml must be a mapping at the top level")
    prefixes_raw = data.get("prefixes") or {}
    if not isinstance(prefixes_raw, dict):
        raise MappingIRReadError("prefixes must be a mapping")
    prefixes = {str(k): str(v) for k, v in prefixes_raw.items()}
    maps_raw = data.get("maps")
    if not isinstance(maps_raw, list):
        raise MappingIRReadError("maps must be a list")
    maps = tuple(_read_map(m, index=i) for i, m in enumerate(maps_raw))
    return MappingIRView(prefixes=prefixes, maps=maps)
