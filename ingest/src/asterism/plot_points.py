"""図の点を、x の全範囲を覆ったまま上限の数に減らす（推移・散らばり・曲線の共通規則）。

図（``series`` / ``pairs`` / 曲線の x・y 配列）の点を上限で減らすとき、**先頭から
切らない**。先頭から切ると、x の前のほうだけが描かれて「データはそこで終わっている」
ように見える（実例: XRD 20〜80° の 3001 点が、一覧の件数の上限 20 で 20.00〜20.38°
だけになった）。ここでは次の規則で減らす。

- 上限以下なら、全点をそのまま返す（並びも変えない）。
- 超えたら、x の昇順に並べ（x が数でない・曲線は元の並びのまま）、両端の点を必ず
  残し、間を同じ点数の区間に分けて、各区間で y が最小の点と最大の点を残す（ピーク
  と谷が消えない）。点を黙って捨てない。
- 残すのは**実在の点だけ**。平均や補間で新しい点を作らない（引用できる事実の原則）。
- 系列（``series`` の役割）があれば、系列ごとに点数に比例して上限を配る。

純関数のみ・store アクセスなし。決定論（同じ入力 → 同じ出力）。
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import Any, TypeVar

__all__ = [
    "PLOT_MAX_POINTS",
    "PLOT_OUTPUT_KINDS",
    "PLOT_READ_CAP",
    "plot_result",
    "thin_items",
    "thin_points",
    "thin_xy",
]

#: 1 枚の図に描く点の上限。XRD・スペクトル・時系列の 1 本（数千点）は間引かずに
#: そのまま描ける大きさにしてある。
PLOT_MAX_POINTS = 5000

#: 図のために store から読む行の上限。これを超えたら、読めた範囲で間引き、
#: 元の点数は「以上」（下限）として返す。
PLOT_READ_CAP = 100_000

#: この規則を当てる出口の型（:data:`asterism.query_tools.OUTPUT_KINDS` の値）。
PLOT_OUTPUT_KINDS: frozenset[str] = frozenset({"series", "pairs"})

T = TypeVar("T")


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _thin_ordered(points: Sequence[T], budget: int, y_of: Callable[[T], float | None]) -> list[T]:
    """並んだ ``points`` を ``budget`` 点以下に減らす（両端＋区間ごとの最小・最大）。

    区間は並びの上で同じ点数に切る。並びは呼び出し側が決める（x の昇順、または
    曲線の元の並び）。出力は元の並びの順。"""
    n = len(points)
    if n <= budget:
        return list(points)
    if budget <= 2:
        return [points[0], points[-1]][:budget] if n > 1 else list(points)
    buckets = (budget - 2) // 2
    keep: set[int] = {0, n - 1}
    inner_start, inner_end = 1, n - 1  # [1, n-1) が内側
    inner = inner_end - inner_start
    for b in range(buckets):
        lo = inner_start + (inner * b) // buckets
        hi = inner_start + (inner * (b + 1)) // buckets
        if lo >= hi:
            continue
        lo_i = hi_i = None
        lo_y = hi_y = None
        for i in range(lo, hi):
            y = y_of(points[i])
            if y is None:
                continue
            if lo_y is None or y < lo_y:
                lo_i, lo_y = i, y
            if hi_y is None or y > hi_y:
                hi_i, hi_y = i, y
        if lo_i is None:
            keep.add(lo)  # y が数でない区間でも、範囲の目印として 1 点は残す
            continue
        keep.add(lo_i)
        keep.add(hi_i)  # type: ignore[arg-type]
    return [points[i] for i in sorted(keep)]


def thin_points(
    points: Sequence[T],
    *,
    max_points: int,
    x_of: Callable[[T], float | None],
    y_of: Callable[[T], float | None],
    group_of: Callable[[T], Any] | None = None,
    sort_by_x: bool = True,
) -> list[T]:
    """``points`` を ``max_points`` 点以下に減らす（モジュールの規則どおり）。

    - 上限以下なら入力をそのまま（並びも変えずに）返す。
    - **点は捨てない**（x が数でない点も、間引きの対象にはなるが黙って落とさない）。
    - ``sort_by_x`` かつ系列の全点の x が数のときだけ、x の昇順に並べてから間引く。
      それ以外（x が日付・分類の文字列、往復する曲線 = ``sort_by_x=False``）は
      入力の並びのまま、並びの上で同じ点数の区間に切って間引く。
    - 出力は系列ごと（``group_of`` の値の初出順）。各系列に最低 2 点（両端）を配る
      ので、系列の数が上限の半分より多いときだけ、上限を超えることがある。"""
    max_points = max(1, int(max_points))
    if len(points) <= max_points:
        return list(points)
    groups: dict[Any, list[T]] = {}
    for p in points:
        groups.setdefault(group_of(p) if group_of else None, []).append(p)
    total = len(points)
    out: list[T] = []
    for members in groups.values():
        if sort_by_x and all(x_of(p) is not None for p in members):
            members.sort(key=lambda p: (x_of(p), y_of(p) is None, y_of(p) or 0.0))
        budget = max(min(2, max_points), (max_points * len(members)) // total)
        out.extend(_thin_ordered(members, budget, y_of))
    return out


def _role_keys(item_spec: dict[str, dict[str, Any]]) -> dict[str, str]:
    """役割 ``x``/``y``/``series`` → その役割を持つ ``item`` のキー。"""
    return {
        role: key
        for key, spec in item_spec.items()
        if isinstance(spec, dict) and (role := spec.get("role")) in ("x", "y", "series")
    }


def thin_items(
    items: list[dict[str, Any]],
    item_spec: dict[str, dict[str, Any]],
    *,
    max_points: int = PLOT_MAX_POINTS,
) -> list[dict[str, Any]]:
    """query tool の結果の ``items``（``item`` の役割 ``x``/``y``/``series`` つき）を減らす。

    役割 ``x`` か ``y`` の項目が見つからなければ、図の規則を当てられないので
    先頭から ``max_points`` 件（従来どおり）。"""
    keys = _role_keys(item_spec)
    x_key, y_key = keys.get("x"), keys.get("y")
    if x_key is None or y_key is None:
        return items[:max_points]
    s_key = keys.get("series")
    return thin_points(
        items,
        max_points=max_points,
        x_of=lambda r: _as_float(r.get(x_key)),
        y_of=lambda r: _as_float(r.get(y_key)),
        group_of=(lambda r: r.get(s_key)) if s_key else None,
    )


def thin_xy(
    xs: Sequence[float], ys: Sequence[float], *, max_points: int
) -> tuple[list[float], list[float]]:
    """曲線の x・y 配列を ``max_points`` 点以下に減らす（長さが違えば短いほうに揃える）。"""
    pairs = list(zip(xs, ys, strict=False))
    # 曲線は点の並びそのものが意味を持つ（昇温→降温の往復など）ので並べ替えない。
    kept = thin_points(
        pairs,
        max_points=max_points,
        x_of=lambda p: _as_float(p[0]),
        y_of=lambda p: _as_float(p[1]),
        sort_by_x=False,
    )
    return [p[0] for p in kept], [p[1] for p in kept]


def plot_result(
    items: list[dict[str, Any]],
    item_spec: dict[str, dict[str, Any]],
    *,
    max_points: int = PLOT_MAX_POINTS,
    read_cap: int | None = PLOT_READ_CAP,
) -> dict[str, Any]:
    """図の結果の ``count``/``items``/``truncated`` と、何点のうち何点かの印を作る。

    - ``total``: 元の点数（``read_cap`` を超えて読めたときは ``read_cap`` — 下限）
    - ``total_is_lower_bound``: 元の点数が ``read_cap`` を超えていた（読み切れていない）
    - ``thinned``: 全範囲を覆ったまま間引いた
    - ``truncated``: ``items`` が元の点の全部ではない（間引いた・読み切れていない・
      役割が無く先頭で切った、のどれか）

    呼び出し側は ``read_cap + 1`` 行まで読んで渡す（超えたかを見分けるため）。"""
    lower = read_cap is not None and len(items) > read_cap
    if lower:
        items = items[:read_cap]
    total = len(items)
    kept = thin_items(items, item_spec, max_points=max_points)
    has_xy = {"x", "y"} <= set(_role_keys(item_spec))
    return {
        "count": len(kept),
        "items": kept,
        "truncated": lower or len(kept) < total,
        "total": total,
        "total_is_lower_bound": lower,
        "thinned": has_xy and len(kept) < total,
    }
