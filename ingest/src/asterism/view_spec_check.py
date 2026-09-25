"""AI が書いた「見せ方」の検証（契約メモ contract_pr_f13.md §1-2）— 純関数のみ。

「書く」で AI が出す 3 言語（Vega-Lite の JSON／表仕様／Mermaid flowchart の
テキスト）を、決定論の許可リストで検証する。store にもレジストリにも触れず
（I/O なし）、どの言語も **データを持ち込めない**（Vega-Lite の ``data``・
表仕様の行データ・Mermaid のデータ埋め込みはいずれも許可リストに無い —
データはカードの実行結果から画面側/サーバ側が差し込む、契約 §1 の決定 1）。

いずれの ``check_*`` も ``(ok, reason)`` を返す。``reason`` は
:class:`asterism.measure_spec.MeasureSpecError` と同じ「AI 向けの言い直し
指示にだけ使う生の理由」（K4: 人向けの文言には混ぜない — 呼び出し側の役目）。

対応する UI 側の型/文法（この module はそれをそのまま Python で写しただけで
独自の語彙は増やさない）:

- Vega-Lite: ``ui/src/cards/viewSpec.ts`` の ``VegaLiteSpec``（型自体は
  ``Record<string, unknown>`` — 許可リストはこの module 側にしか無い）。
- 表仕様: ``ui/src/cards/viewSpec.ts`` の ``TableSpec`` の**部分集合**
  （AI が書けるのは ``variant``/``columns[].{key,label,unit}``/``sort`` だけ
  — ``href_field``/``highlight``/``subject_field`` のような「サーバ/UI だけ
  が知っている行キー」は AI には書かせない）。
- Mermaid: ``ui/src/cards/mermaidFlow.ts`` の ``parseMermaidFlowchart`` が
  読める文法の**さらに部分集合**（``flowchart LR``/``TD`` のみ・節は
  ``id``/``id[label]``/``id(label)``・辺は ``-->``/``-->|label|`` だけ —
  subgraph・classDef・class・click・style・href は全部拒否）。
"""

from __future__ import annotations

import json
import re
from typing import Any

__all__ = ["check_mermaid", "check_table", "check_vega_lite"]

#: 契約メモ §1-2「大きさ上限 16KB」— Mermaid にも同じ理由(§1-2 の Mermaid の
#: 段)で同じ上限を課す。表仕様は契約に明記が無いが、Vega-Lite/Mermaid と
#: 同じ理由(壊れた/攻撃的な巨大 JSON を弾く)で同じ上限を流用する(緩めては
#: いないので契約からの逸脱ではない)。
_MAX_SPEC_BYTES = 16 * 1024


def _encoded_size(obj: Any) -> int:
    return len(json.dumps(obj, ensure_ascii=False).encode("utf-8"))


# ----------------------------------------------------------------------------
# Vega-Lite
# ----------------------------------------------------------------------------

#: トップレベルの許可キー(契約 §1-2)。``data``/``params``/``usermeta`` は
#: 明示的に不可(``data`` はサーバ/UI が差し込む・``params`` は Vega-Lite の
#: シグナル/式機能・``usermeta`` は仕様上何にでも使える抜け道)。``transform``
#: は許可するが中身をさらに絞る(:func:`_valid_transform`)。
_ALLOWED_TOP_KEYS = {
    "$schema",
    "mark",
    "encoding",
    "width",
    "height",
    "title",
    "config",
    "layer",
    "transform",
}
_KNOWN_MARKS = {"line", "bar", "point", "area", "circle", "rect", "text", "tick"}
_KNOWN_CHANNELS = {"x", "y", "color", "size", "shape", "tooltip", "order", "row", "column"}
_KNOWN_CHANNEL_FIELDS = {"field", "type", "title", "axis", "scale", "legend", "sort"}
#: transform の中で明示的に拒否する種類 — ``lookup``(他データへの結合)・
#: ``loader``(外部読み込み)・``calculate``(任意の式文字列 ``expr`` を評価)。
_DENIED_TRANSFORM_KEYS = {"lookup", "loader", "calculate"}
#: transform で許可する、副作用の無い構造的な変形だけ。
_ALLOWED_TRANSFORM_KEYS = {
    "filter",
    "aggregate",
    "bin",
    "timeUnit",
    "sort",
    "window",
    "joinaggregate",
    "stack",
    "fold",
    "flatten",
    "pivot",
}
#: 深さを問わず、どこに出てきても拒否するキー(契約 §1-2)。``data`` は
#: トップだけでなく ``layer`` の中の子 spec にも書けてしまうため再帰で見る。
_DENIED_ANYWHERE_KEYS = {"data", "params", "usermeta", "expr", "signal", "datum", "loader"}


def _find_denied_key(obj: Any) -> str | None:
    """``obj`` のどこかに :data:`_DENIED_ANYWHERE_KEYS` のキーがあれば、その
    キー名を返す(再帰・dict のキーだけを見る — 値の中の同名文字列は見ない)。"""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in _DENIED_ANYWHERE_KEYS:
                return str(key)
            found = _find_denied_key(value)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_denied_key(item)
            if found is not None:
                return found
    return None


def _looks_like_url(text: str) -> bool:
    lowered = text.lower()
    return "url" in lowered or "http://" in lowered or "https://" in lowered


def _find_url_like_string(obj: Any) -> str | None:
    """``obj`` のどこかに url らしき文字列値(``"url"`` という語を含む・
    ``http(s)://`` で始まる)があれば、それを返す(契約 §1-2「url を含む
    文字列」— 外部参照/追跡の抜け道を機械的に潰す)。"""
    if isinstance(obj, dict):
        for value in obj.values():
            found = _find_url_like_string(value)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_url_like_string(item)
            if found is not None:
                return found
    elif isinstance(obj, str) and _looks_like_url(obj):
        return obj
    return None


def _valid_mark(mark: Any) -> bool:
    if isinstance(mark, str):
        return mark in _KNOWN_MARKS
    if isinstance(mark, dict):
        return mark.get("type") in _KNOWN_MARKS
    return False


def _valid_channel_value(value: Any) -> bool:
    if isinstance(value, list):
        return all(_valid_channel_value(v) for v in value)
    if not isinstance(value, dict):
        return False
    return all(k in _KNOWN_CHANNEL_FIELDS for k in value)


def _valid_encoding(encoding: Any) -> tuple[bool, str | None]:
    if not isinstance(encoding, dict):
        return False, "encoding must be an object"
    for channel, value in encoding.items():
        if channel not in _KNOWN_CHANNELS:
            return False, f"unknown encoding channel: {channel!r}"
        if not _valid_channel_value(value):
            return False, f"unknown key in encoding.{channel}"
    return True, None


def _valid_transform(transform: Any) -> tuple[bool, str | None]:
    if not isinstance(transform, list):
        return False, "transform must be an array"
    for step in transform:
        if not isinstance(step, dict):
            return False, "each transform step must be an object"
        keys = set(step)
        denied = keys & _DENIED_TRANSFORM_KEYS
        if denied:
            return False, f"transform type not allowed: {sorted(denied)[0]!r}"
        if not keys & _ALLOWED_TRANSFORM_KEYS:
            return False, "unknown transform type"
    return True, None


def _check_vega_lite_obj(spec: dict[str, Any]) -> tuple[bool, str | None]:
    denied_key = _find_denied_key(spec)
    if denied_key is not None:
        return False, f"key not allowed: {denied_key!r}"
    url_like = _find_url_like_string(spec)
    if url_like is not None:
        return False, "spec must not contain a string referencing a url"
    for key in spec:
        if key not in _ALLOWED_TOP_KEYS:
            return False, f"key not allowed: {key!r}"
    mark = spec.get("mark")
    if mark is not None and not _valid_mark(mark):
        return False, f"unknown mark: {mark!r}"
    encoding = spec.get("encoding")
    if encoding is not None:
        ok, reason = _valid_encoding(encoding)
        if not ok:
            return False, reason
    transform = spec.get("transform")
    if transform is not None:
        ok, reason = _valid_transform(transform)
        if not ok:
            return False, reason
    layer = spec.get("layer")
    if layer is not None:
        if not isinstance(layer, list) or not layer:
            return False, "layer must be a non-empty array"
        for item in layer:
            if not isinstance(item, dict):
                return False, "each layer item must be an object"
            ok, reason = _check_vega_lite_obj(item)
            if not ok:
                return False, reason
    return True, None


def check_vega_lite(spec: Any) -> tuple[bool, str | None]:
    """契約メモ §1-2 の許可リストで Vega-Lite の JSON を検証する。``data``・
    ``params``・``usermeta``・``expr``/``signal``/``datum``・``lookup``/
    ``loader``/``calculate``・url らしき文字列・未知の ``mark``/encoding
    チャネル/キー・16KB 超のいずれかがあれば拒否する。"""
    if not isinstance(spec, dict):
        return False, "spec must be an object"
    if _encoded_size(spec) > _MAX_SPEC_BYTES:
        return False, "spec is over the 16KB limit"
    return _check_vega_lite_obj(spec)


# ----------------------------------------------------------------------------
# 表仕様(TableSpec の部分集合 — AI が書けるのはこの形だけ)
# ----------------------------------------------------------------------------

_TABLE_TOP_KEYS = {"variant", "columns", "sort"}
_TABLE_VARIANTS = {"grid", "figure", "ranked"}
_TABLE_COLUMN_KEYS = {"key", "label", "unit"}
_TABLE_SORT_KEYS = {"field", "dir"}
_TABLE_SORT_DIRS = {"asc", "desc"}


def check_table(spec: Any) -> tuple[bool, str | None]:
    """契約メモ §1-2 の表仕様を検証する。``columns`` は非空の配列で、各要素は
    ``key``/``label`` が必須の文字列・``unit`` は任意の文字列。``sort`` は
    任意で ``{field, dir}``(``dir`` は ``asc``/``desc``)。それ以外のキー
    (``href_field``/``highlight``/``subject_field`` のような UI 内部専用の
    行キーを含む)はすべて拒否する。"""
    if not isinstance(spec, dict):
        return False, "spec must be an object"
    if _encoded_size(spec) > _MAX_SPEC_BYTES:
        return False, "spec is over the 16KB limit"
    for key in spec:
        if key not in _TABLE_TOP_KEYS:
            return False, f"key not allowed: {key!r}"
    variant = spec.get("variant")
    if variant is not None and variant not in _TABLE_VARIANTS:
        return False, f"unknown variant: {variant!r}"
    columns = spec.get("columns")
    if not isinstance(columns, list) or not columns:
        return False, "columns must be a non-empty array"
    for column in columns:
        if not isinstance(column, dict):
            return False, "each column must be an object"
        for key in column:
            if key not in _TABLE_COLUMN_KEYS:
                return False, f"key not allowed in column: {key!r}"
        key_value = column.get("key")
        if not isinstance(key_value, str) or not key_value:
            return False, "column.key is required"
        label_value = column.get("label")
        if not isinstance(label_value, str) or not label_value:
            return False, "column.label is required"
        unit_value = column.get("unit")
        if unit_value is not None and not isinstance(unit_value, str):
            return False, "column.unit must be a string"
    sort = spec.get("sort")
    if sort is not None:
        if not isinstance(sort, dict):
            return False, "sort must be an object"
        for key in sort:
            if key not in _TABLE_SORT_KEYS:
                return False, f"key not allowed in sort: {key!r}"
        field_value = sort.get("field")
        if not isinstance(field_value, str) or not field_value:
            return False, "sort.field is required"
        if sort.get("dir") not in _TABLE_SORT_DIRS:
            return False, "sort.dir must be 'asc' or 'desc'"
    return True, None


# ----------------------------------------------------------------------------
# Mermaid flowchart(``mermaidFlow.ts`` が読める文法のさらに部分集合)
# ----------------------------------------------------------------------------

_MERMAID_HEADER_RE = re.compile(r"^flowchart\s+(LR|TD)\s*;?$", re.IGNORECASE)
#: 節 1 つ: ``id``・``id[label]``・``id(label)`` だけ(:mod:`mermaidFlow.ts`
#: の ``NODE_RE`` が受ける形のうち、契約 §1-2 で許した 2 種類の括弧だけに
#: 絞ったもの — ``{}``/``[[ ]]``/``(( ))``/``([ ])`` は AI には書かせない)。
_MERMAID_NODE_RE = re.compile(r"^[A-Za-z0-9_-]+(?:\[[^\[\]]*\]|\([^()]*\))?$")
#: 辺の区切り: ``-->`` または ``-->|label|`` だけ(``---``/``-.->``/``==>``
#: は許さない — :mod:`mermaidFlow.ts` の ``ARROW_RE`` の部分集合)。
_MERMAID_ARROW_SPLIT_RE = re.compile(r"(-->\|[^|]*\||-->)")
#: 契約 §1-2「click/href/style/classDef は拒否」。
_MERMAID_DENIED_RE = re.compile(r"\b(click|href|style|classDef)\b", re.IGNORECASE)


def _is_allowed_flow_line(line: str) -> bool:
    parts = _MERMAID_ARROW_SPLIT_RE.split(line)
    if len(parts) == 1:
        return bool(_MERMAID_NODE_RE.match(parts[0].strip()))
    # 偶数インデックスが節、奇数インデックスが矢印の区切り文字列。
    return all(_MERMAID_NODE_RE.match(parts[i].strip()) for i in range(0, len(parts), 2))


def check_mermaid(text: Any) -> tuple[bool, str | None]:
    """契約メモ §1-2 の Mermaid flowchart の部分集合を検証する。1 行目は
    ``flowchart LR``/``flowchart TD`` のみ、それ以降は節(``id``/``id[label]``/
    ``id(label)``)か辺(``-->``/``-->|label|``)の行だけ。``click``/``href``/
    ``style``/``classDef``・16KB 超はいずれも拒否。"""
    if not isinstance(text, str):
        return False, "view text must be a string"
    if len(text.encode("utf-8")) > _MAX_SPEC_BYTES:
        return False, "view text is over the 16KB limit"
    if _MERMAID_DENIED_RE.search(text):
        return False, "view text must not use click/href/style/classDef"
    lines = [ln.strip() for ln in text.replace("\r\n", "\n").split("\n")]
    lines = [ln for ln in lines if ln]
    if not lines or not _MERMAID_HEADER_RE.match(lines[0]):
        return False, "view text must start with 'flowchart LR' or 'flowchart TD'"
    for line in lines[1:]:
        if not _is_allowed_flow_line(line):
            return False, f"unsupported mermaid line: {line!r}"
    return True, None
