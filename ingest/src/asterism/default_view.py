"""出口の意味型（``OutputKind``）→ 既定ビュー（``ViewSpec``）の決定論（契約メモ
§3・ADR object-cards-ui.md O17/O18）。

``ui/src/cards/defaultView.ts`` の**逐語移植**（1 関数 1 関数、同じ分岐・同じ
優先順位）。両言語が同じ入力（``ToolContract`` + ``rows``）から同じ JSON を
返すことは、共有フィクスチャ ``ui/src/cards/fixtures/default_view_cases.json``
（``ingest/tests/test_default_view.py`` と ui 側テストの両方が読む）で固定する
— このモジュール自身は分野語の辞書を持たない（列の見出しはキーの機械整形だけ）。

LLM 呼び出し・生成コード実行は無い（§0）。TS 版の ``undefined`` は、ここでは
「キーを立てない」（``None`` を値に入れない）ことで表す — JSON 化したとき
``JSON.stringify`` が ``undefined`` プロパティを落とすのと同じ見た目にするため。
"""
from __future__ import annotations

from typing import Any

__all__ = ["default_view_for", "unit_label"]

ItemMap = dict[str, dict[str, Any]]
Row = dict[str, Any]


def unit_label(unit: str | None) -> str:
    """``unit:K`` -> ``K``、フル IRI は末尾の局所名。値が無ければ空文字
    （``ui/src/cards/unitLabel.ts`` の逐語移植 — QUDT の記号表はまだ引かない）。
    """
    if not unit:
        return ""
    import re

    parts = re.split(r"[:#/]", unit)
    local = parts[-1] if parts else ""
    return local or unit


def _humanize_key(key: str) -> str:
    stripped = key[:-4] if key.endswith("_iri") else key
    return stripped.replace("_", " ")


def _all_by_role(item: ItemMap, role: str) -> list[dict[str, Any]]:
    return [spec for spec in item.values() if spec.get("role") == role]


def _find_role(item: ItemMap, role: str) -> dict[str, Any] | None:
    hits = _all_by_role(item, role)
    return hits[0] if hits else None


def _find_subject(item: ItemMap) -> dict[str, Any] | None:
    """1 件のページを指す IRI 列（K4: 列としては出さず行クリックの遷移先に）。
    ``role: 'subject'`` を優先し、組み込みツールの契約キー ``subject_iri``
    へフォールバックする。"""
    role_hit = _find_role(item, "subject")
    if role_hit is not None:
        return role_hit
    for spec in item.values():
        if spec.get("var") == "subject_iri":
            return spec
    return None


def _field_title(item_spec: dict[str, Any]) -> str:
    label = item_spec.get("label")
    if label is None:
        label = _humanize_key(item_spec["var"])
    unit = item_spec.get("unit")
    if unit is not None:
        return f"{label} [{unit_label(unit)}]"
    return label


def _column_for(item_spec: dict[str, Any]) -> dict[str, Any]:
    var = item_spec["var"]
    is_iri = var.endswith("_iri") or item_spec.get("role") == "subject"
    label = item_spec.get("label")
    if label is None:
        label = _humanize_key(var)
    col: dict[str, Any] = {
        "field": var,
        "label": label,
        "format": "iri" if is_iri else ("number" if item_spec.get("number") else "text"),
    }
    unit = item_spec.get("unit")
    if unit is not None:
        col["unit"] = unit_label(unit)
    if item_spec.get("number"):
        col["align"] = "right"
    return col


def _x_encoding(item_spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "field": item_spec["var"],
        "type": "ordinal" if item_spec.get("number") is False else "quantitative",
        "title": _field_title(item_spec),
    }


def _y_encoding(item_spec: dict[str, Any]) -> dict[str, Any]:
    return {"field": item_spec["var"], "type": "quantitative", "title": _field_title(item_spec)}


def _quantity_view(tool: dict[str, Any]) -> dict[str, Any]:
    item: ItemMap = tool["item"]
    columns: list[dict[str, Any]] = []
    value = _find_role(item, "value")
    if value is not None:
        columns.append(_column_for(value))
    for role in ("at", "label", "subject"):
        for spec in _all_by_role(item, role):
            columns.append(_column_for(spec))
    return {"lang": "table", "spec": {"variant": "figure", "columns": columns}}


def _series_view(tool: dict[str, Any], rows: list[Row]) -> dict[str, Any]:
    item: ItemMap = tool["item"]
    x = _find_role(item, "x")
    y = _find_role(item, "y")
    series = _find_role(item, "series")
    encoding: dict[str, Any] = {}
    if x is not None:
        encoding["x"] = _x_encoding(x)
    if y is not None:
        encoding["y"] = _y_encoding(y)
    if series is not None:
        encoding["color"] = {
            "field": series["var"],
            "type": "nominal",
            "title": _field_title(series),
        }
    spec = {"mark": {"type": "line", "point": True}, "encoding": encoding, "data": {"values": rows}}
    return {"lang": "vega-lite", "spec": spec}


def _pairs_view(tool: dict[str, Any], rows: list[Row]) -> dict[str, Any]:
    item: ItemMap = tool["item"]
    x = _find_role(item, "x")
    y = _find_role(item, "y")
    encoding: dict[str, Any] = {}
    if x is not None:
        encoding["x"] = {"field": x["var"], "type": "quantitative", "title": _field_title(x)}
    if y is not None:
        encoding["y"] = {"field": y["var"], "type": "quantitative", "title": _field_title(y)}
    spec = {"mark": "point", "encoding": encoding, "data": {"values": rows}}
    return {"lang": "vega-lite", "spec": spec}


def _ranked_view(tool: dict[str, Any]) -> dict[str, Any]:
    item: ItemMap = tool["item"]
    columns: list[dict[str, Any]] = []
    label = _find_role(item, "label")
    if label is not None:
        columns.append(_column_for(label))
    value = _find_role(item, "value")
    if value is not None:
        columns.append(_column_for(value))
    subject = _find_subject(item)
    spec: dict[str, Any] = {"variant": "ranked", "columns": columns}
    if subject is not None:
        spec["subject_field"] = subject["var"]
    if value is not None:
        spec["sort"] = {"field": value["var"], "dir": "desc"}
    return {"lang": "table", "spec": spec}


def _breakdown_view(tool: dict[str, Any], rows: list[Row]) -> dict[str, Any]:
    item: ItemMap = tool["item"]
    category = _find_role(item, "category")
    count = _find_role(item, "count")
    encoding: dict[str, Any] = {}
    if category is not None:
        encoding["y"] = {
            "field": category["var"],
            "type": "nominal",
            "sort": "-x",
            "axis": {"title": None},
        }
    if count is not None:
        encoding["x"] = {
            "field": count["var"],
            "type": "quantitative",
            "title": _field_title(count),
            "axis": {"tickMinStep": 1, "format": "d"},
        }
    spec = {"mark": "bar", "encoding": encoding, "data": {"values": rows}}
    return {"lang": "vega-lite", "spec": spec}


def _facts_view(tool: dict[str, Any]) -> dict[str, Any]:
    item: ItemMap = tool["item"]
    all_entries = list(item.values())
    by_var = {entry["var"]: entry for entry in all_entries}
    subject = _find_subject(item)
    entries = [e for e in all_entries if e is not subject] if subject is not None else all_entries
    normal = [e for e in entries if not e["var"].endswith("_iri")]
    orphan_iri = [
        e
        for e in entries
        if e["var"].endswith("_iri") and e["var"][:-4] not in by_var
    ]
    columns: list[dict[str, Any]] = []
    for entry in [*normal, *orphan_iri]:
        col = _column_for(entry)
        if not entry["var"].endswith("_iri"):
            sibling = by_var.get(f"{entry['var']}_iri")
            if sibling is not None and sibling is not subject:
                col["href_field"] = sibling["var"]
        columns.append(col)
    spec: dict[str, Any] = {"variant": "grid", "columns": columns}
    if subject is not None:
        spec["subject_field"] = subject["var"]
    return {"lang": "table", "spec": spec}


def _flow_view() -> dict[str, Any]:
    return {"lang": "graph", "spec": {"nodes": [], "edges": []}}


def default_view_for(tool: dict[str, Any], rows: list[Row]) -> dict[str, Any]:
    """型 -> 既定ビュー。同じ ``tool``/``rows`` に対して常に同じ ``ViewSpec``
    を返す（決定論）。分野語の辞書は持たない。返り値に ``custom`` は付かない
    （LLM が書いたものではない — キー自体を立てない）。"""
    kind = tool["output_kind"]
    if kind == "quantity":
        return _quantity_view(tool)
    if kind == "series":
        return _series_view(tool, rows)
    if kind == "pairs":
        return _pairs_view(tool, rows)
    if kind == "ranked":
        return _ranked_view(tool)
    if kind == "breakdown":
        return _breakdown_view(tool, rows)
    if kind == "facts":
        return _facts_view(tool)
    if kind == "flow":
        return _flow_view()
    raise ValueError(f"unknown output_kind: {kind!r}")
