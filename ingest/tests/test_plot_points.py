"""Tests for asterism.plot_points — 図の点を全範囲を覆ったまま減らす共通規則。

実例（固定）: XRD 20〜80°・0.02° 刻みの 3001 点が、一覧の件数の上限（20）で
先頭の 20.00〜20.38° だけ描かれていた。上限以下なら全点、超えたら両端と各区間の
最小・最大の実在点を残す。
"""

from __future__ import annotations

from asterism.plot_points import (
    PLOT_MAX_POINTS,
    plot_result,
    thin_items,
    thin_points,
    thin_xy,
)

_ITEM = {
    "x": {"var": "x", "role": "x", "number": True},
    "y": {"var": "y", "role": "y", "number": True},
}


def _scan(n: int = 3001, start: float = 20.0, step: float = 0.02) -> list[dict]:
    """XRD のような 1 本の走査。中ほどに鋭いピークを 1 本置く。"""
    out = []
    for i in range(n):
        x = round(start + step * i, 6)
        y = 1000.0 + (i % 7)
        if i == n // 3:
            y = 37500.0  # ピーク
        out.append({"x": x, "y": y})
    return out


def test_xrd_scan_fits_whole_under_the_default_cap() -> None:
    scan = _scan()
    assert len(scan) <= PLOT_MAX_POINTS
    out = plot_result(scan, _ITEM)
    assert out["count"] == 3001
    assert out["items"][0]["x"] == 20.0
    assert out["items"][-1]["x"] == 80.0
    assert out["truncated"] is False
    assert out["thinned"] is False
    assert out["total"] == 3001


def test_under_the_cap_keeps_input_order_and_identity() -> None:
    scan = list(reversed(_scan(50)))
    assert thin_points(scan, max_points=50, x_of=lambda p: p["x"], y_of=lambda p: p["y"]) == scan


def test_thinning_covers_the_whole_x_range_and_keeps_the_peak() -> None:
    scan = _scan()
    kept = thin_items(scan, _ITEM, max_points=200)
    assert len(kept) <= 200
    assert kept[0]["x"] == 20.0  # 先頭から切らない: 両端が残る
    assert kept[-1]["x"] == 80.0
    assert max(p["y"] for p in kept) == 37500.0  # ピークが消えない
    assert [p["x"] for p in kept] == sorted(p["x"] for p in kept)
    # 実在の点だけ（新しい点を作らない）
    real = {(p["x"], p["y"]) for p in scan}
    assert all((p["x"], p["y"]) in real for p in kept)


def test_thinning_spreads_points_across_the_range() -> None:
    kept = thin_items(_scan(), _ITEM, max_points=100)
    xs = [p["x"] for p in kept]
    # 範囲を 10 等分したどの区間にも点がある（前のほうに偏らない）
    for k in range(10):
        lo, hi = 20.0 + 6.0 * k, 20.0 + 6.0 * (k + 1)
        assert any(lo <= x <= hi for x in xs), (lo, hi)


def test_plot_result_reports_thinned_and_total() -> None:
    out = plot_result(_scan(), _ITEM, max_points=100)
    assert out["thinned"] is True
    assert out["truncated"] is True
    assert out["total"] == 3001
    assert out["count"] == len(out["items"]) <= 100
    assert out["total_is_lower_bound"] is False


def test_plot_result_read_cap_marks_total_as_lower_bound() -> None:
    out = plot_result(_scan(), _ITEM, max_points=5000, read_cap=1000)
    assert out["total"] == 1000
    assert out["total_is_lower_bound"] is True
    assert out["truncated"] is True


def test_series_role_splits_the_budget_per_series() -> None:
    item = {**_ITEM, "s": {"var": "s", "role": "series"}}
    rows = [{**p, "s": "a"} for p in _scan(1000)] + [{**p, "s": "b"} for p in _scan(1000, 30.0)]
    kept = thin_items(rows, item, max_points=100)
    for name, start, end in (("a", 20.0, 39.98), ("b", 30.0, 49.98)):
        mine = [p for p in kept if p["s"] == name]
        assert mine[0]["x"] == start and mine[-1]["x"] == end
        assert 2 <= len(mine) <= 50


def test_without_x_y_roles_falls_back_to_the_leading_rows() -> None:
    item = {"v": {"var": "v", "number": True}}
    rows = [{"v": i} for i in range(10)]
    out = plot_result(rows, item, max_points=3)
    assert out["items"] == rows[:3]
    assert out["truncated"] is True
    assert out["thinned"] is False


def test_thin_xy_keeps_both_ends() -> None:
    xs = [float(i) for i in range(1000)]
    ys = [0.0] * 1000
    ys[500] = 9.0
    tx, ty = thin_xy(xs, ys, max_points=20)
    assert tx[0] == 0.0 and tx[-1] == 999.0
    assert 9.0 in ty
    assert len(tx) == len(ty) <= 20


def test_non_numeric_x_is_thinned_in_input_order_not_dropped() -> None:
    """x が日付の文字列でも点を捨てない（checker の指摘: 全点が消えて 0 件になった）。"""
    rows = [{"x": f"2020-01-{i + 1:02d}", "y": float(i % 4)} for i in range(30)]
    out = plot_result(rows, _ITEM, max_points=5)
    assert 1 <= out["count"] <= 5
    assert out["items"][0] == rows[0] and out["items"][-1] == rows[-1]
    assert out["thinned"] is True


def test_max_points_one_returns_one_point() -> None:
    out = plot_result(_scan(10), _ITEM, max_points=1)
    assert out["count"] == 1


def test_thin_xy_keeps_a_round_trip_curve_in_its_order() -> None:
    """昇温→降温の往復のような曲線は、間引いても点の並びを変えない。"""
    xs = [float(i) for i in range(500)] + [float(499 - i) for i in range(500)]
    ys = [float(i % 13) for i in range(1000)]
    tx, _ = thin_xy(xs, ys, max_points=40)
    assert tx[0] == 0.0 and tx[-1] == 0.0
    peak = tx.index(max(tx))
    assert tx[: peak + 1] == sorted(tx[: peak + 1])
    assert tx[peak:] == sorted(tx[peak:], reverse=True)
