"""☑「他のデータとつながる手がかり」の読み手（契約メモ contract_pr_f15.md §1.2。
担当 api-handles）。

S4 で人が ☑ した列は ``registry/<id>/handles.json`` にそのまま残る
（``{"source": <IR の source 名>, "column": <列名>}`` の配列 — 書き込みは
``handles_routes.py`` の ``PUT`` と materialize の 2 経路が行う。ここは純粋な
読み手のみ）。この module は決定論・LLM ゼロ・分野固有の語彙を一切書かない。
"""

from __future__ import annotations

import json
from typing import Any

from asterism_step0.spec_yaml import load_spec_yaml

__all__ = ["handle_slots", "load_handles"]


def load_handles(artifacts: dict[str, str]) -> list[dict]:
    """``artifacts["handles.json"]`` → ``[{"source", "column"}, ...]``。

    無い／空／壊れた JSON／期待した形でない場合は静かに ``[]``（handles.json は
    無くても構わない任意の artifact — 契約を満たさないデータは「無かった」
    ことにするのが安全側）。
    """
    text = (artifacts.get("handles.json") or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []
    raw = data.get("handles") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    handles: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        source = item.get("source")
        column = item.get("column")
        if isinstance(source, str) and source and isinstance(column, str) and column:
            handles.append({"source": source, "column": column})
    return handles


def _expand(prefixes: dict[str, str], term: Any) -> str | None:
    """CURIE（``prefix:local``）→ 完全 IRI。展開できなくても文字列ならそのまま
    返す（既に完全 IRI の場合の既定動作）。文字列でなければ ``None``。"""
    if not isinstance(term, str) or not term:
        return None
    prefix, sep, rest = term.partition(":")
    if sep and prefix in prefixes:
        return prefixes[prefix] + rest
    return term


def _property_columns(prop: dict) -> list[str]:
    """1 predicate-object 行が使う列名（``column`` 単数形／``columns`` 複数形の
    どちらか）。値のカタログ実体の述語（``columns`` で複合キーを組むことがある）
    も記録側の述語（単一 ``column``）も同じ形で拾う。"""
    column = prop.get("column")
    columns = prop.get("columns")
    out: list[str] = []
    if isinstance(column, str) and column:
        out.append(column)
    if isinstance(columns, list):
        out.extend(c for c in columns if isinstance(c, str) and c)
    return out


def handle_slots(mapping_ir_yaml: str, handles: list[dict]) -> list[dict]:
    """Mapping IR を歩き、☑ された各 (source, column) を実際に使っている述語
    すべてを ``{"class_iri", "predicate", "source", "column"}`` で返す。

    同じ source を持つ複数の TriplesMap（値のカタログ実体の map と、それを
    参照する記録側の map の両方があり得る — ADR crosswalk-hub.md）を横断して
    探す。subject template にその列が入っているだけで述語（
    ``properties[].column``/``columns``）が無い map は数えない。述語が 1 つも
    見つからない handle（IR が変わった・列名が変わった）は黙って落とす。並びは
    決定論（``class_iri`` → ``predicate`` → ``source`` → ``column``）。壊れた
    IR（YAML でない／``maps`` が無い）は空を返す。
    """
    try:
        data = load_spec_yaml(mapping_ir_yaml or "")
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    prefixes_raw = data.get("prefixes")
    prefixes = (
        {str(k): str(v) for k, v in prefixes_raw.items()} if isinstance(prefixes_raw, dict) else {}
    )
    maps_raw = data.get("maps")
    if not isinstance(maps_raw, list):
        return []

    slots: list[dict] = []
    seen: set[tuple[str | None, str, str, str]] = set()
    for handle in handles:
        source = handle.get("source")
        column = handle.get("column")
        if not isinstance(source, str) or not isinstance(column, str):
            continue
        found_any = False
        for raw_map in maps_raw:
            if not isinstance(raw_map, dict):
                continue
            if raw_map.get("source") != source:
                continue
            subject_raw = raw_map.get("subject")
            classes_raw = subject_raw.get("classes") if isinstance(subject_raw, dict) else None
            class_iri: str | None = None
            if isinstance(classes_raw, list) and classes_raw:
                class_iri = _expand(prefixes, classes_raw[0])
            properties_raw = raw_map.get("properties")
            if not isinstance(properties_raw, list):
                continue
            for prop in properties_raw:
                if not isinstance(prop, dict):
                    continue
                if column not in _property_columns(prop):
                    continue
                predicate = _expand(prefixes, prop.get("predicate"))
                if not predicate:
                    continue
                key = (class_iri, predicate, source, column)
                if key in seen:
                    continue
                seen.add(key)
                slots.append(
                    {
                        "class_iri": class_iri,
                        "predicate": predicate,
                        "source": source,
                        "column": column,
                    }
                )
                found_any = True
        if not found_any:
            continue

    slots.sort(
        key=lambda s: (
            s["class_iri"] or "",
            s["predicate"],
            s["source"],
            s["column"],
        )
    )
    return slots
