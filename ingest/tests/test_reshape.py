"""表の形をととのえる（asterism.reshape）: 検出・既定の提案・適用・保存則。

決定の記録: ``docs/architecture/source-reshape.md``。フィクスチャは Starrydata の
実データから抜いた小さな3 CSV（``tests/fixtures/reshape/``）。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from asterism.dialect import DEFAULT_DIALECT
from asterism.reshape import (
    ReshapeError,
    apply,
    check_op_against_header,
    derived_tables,
    detect,
    propose,
    read_rows,
    validate_spec,
)

FIXTURES = Path(__file__).parent / "fixtures" / "reshape"
CURVES = FIXTURES / "starrydata_curves.csv"
SAMPLES = FIXTURES / "starrydata_samples.csv"
PAPERS = FIXTURES / "starrydata_papers.csv"


def _find(detections: list[dict], kind: str, **col_filters) -> dict | None:
    for d in detections:
        if d["kind"] != kind:
            continue
        if all(d["columns"].get(k) == v for k, v in col_filters.items()):
            return d
    return None


def _pivot_group(ops: list[dict], slug: str) -> dict:
    op = next(o for o in ops if o["kind"] == "pivot")
    return next(g for g in op["groups"] if g["slug"] == slug)


# ===========================================================================
# read_rows()
# ===========================================================================


def test_read_rows_dict_per_row_matches_header() -> None:
    rows = list(read_rows(CURVES, DEFAULT_DIALECT))
    assert len(rows) == 22
    assert rows[0]["SID"] == "6"
    assert rows[0]["prop_y"] == "Seebeck coefficient"
    assert set(rows[0].keys()) == {
        "SID", "DOI", "composition", "sample_id", "figure_id", "figure_name",
        "prop_x", "prop_y", "unit_x", "unit_y", "x", "y", "created_at", "updated_at",
        "project_names", "comments",
    }


# ===========================================================================
# R4: 等間隔サンプル（_stride_sample_rows）
# ===========================================================================


def test_stride_sample_rows_picks_equally_spaced_rows() -> None:
    """R4: 母集団は先頭からの接頭辞ではなく、等間隔に取った max_rows 行
    (stride = ceil(N / max_rows) 、index % stride == 0)。"""
    from asterism.reshape import _stride_sample_rows

    all_rows = [{"idx": str(i)} for i in range(45)]
    sampled = _stride_sample_rows(all_rows, 10)
    # stride = ceil(45/10) = 5 -> index 0,5,10,...,40 (9件)。
    assert [r["idx"] for r in sampled] == [str(i) for i in range(0, 45, 5)]


def test_stride_sample_rows_returns_all_when_within_max() -> None:
    from asterism.reshape import _stride_sample_rows

    all_rows = [{"idx": str(i)} for i in range(5)]
    assert _stride_sample_rows(all_rows, 10) == all_rows


def test_detect_uses_stride_sample_not_head_prefix(tmp_path: Path) -> None:
    """R4: 先頭 max_rows 行だけでは拾えない証拠が、等間隔サンプルなら拾える。先頭
    45 行は prop_value が単一値（先頭だけを見る旧方式なら先頭 30 行が全部 "Only" =
    distinct=1 で pivot が出ない）、末尾 15 行だけ別ラベル "Other" にする。
    stride=ceil(60/30)=2 の等間隔サンプルは末尾からも行を拾うので distinct>=2 になり
    pivot が検出される。"""
    csv_path = tmp_path / "stride.csv"
    fieldnames = ["sid", "prop_value", "unit", "value"]
    rows = []
    for i in range(60):
        label = "Only" if i < 45 else "Other"
        rows.append({"sid": str(i), "prop_value": label, "unit": "U", "value": str(i)})
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    dets = detect(csv_path, max_rows=30)
    assert _find(dets, "pivot", label="prop_value") is not None


# ===========================================================================
# 1. detect()
# ===========================================================================


def test_detect_curves_explode_x_y() -> None:
    dets = detect(CURVES)
    d = _find(dets, "explode")
    assert d is not None
    assert d["columns"]["arrays"] == ["x", "y"]
    assert d["evidence"]["rows"] == 22
    assert d["evidence"]["length_agreement"] == 1.0


def test_detect_curves_pivot_prop_y_with_partner_prop_x() -> None:
    dets = detect(CURVES)
    d = _find(dets, "pivot", label="prop_y")
    assert d is not None
    assert d["columns"]["unit"] == "unit_y"
    assert d["columns"]["value"] == "y"
    partner = d["columns"]["partner"]
    assert partner == {"label": "prop_x", "unit": "unit_x", "value": "x"}
    assert d["evidence"]["distinct"] >= 2


def test_detect_pivot_partner_with_distinct_one(tmp_path: Path) -> None:
    """R4: partner（もう1組のラベル・単位・値）は distinct が1でも成立する（主ラベル
    側は distinct >= 2 のまま）。実データでは prop_x が先頭サンプルで全部
    "Temperature" になり、partner が落ちていた。"""
    csv_path = tmp_path / "single_axis.csv"
    fieldnames = ["sid", "prop_x", "unit_x", "prop_y", "unit_y", "x", "y"]
    labels = ["ZT", "Seebeck coefficient", "Power factor"]
    units = ["-", "V*K^(-1)", "W*m^(-1)*K^(-2)"]
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for i in range(9):
            writer.writerow(
                {
                    "sid": str(i), "prop_x": "Temperature", "unit_x": "K",
                    "prop_y": labels[i % 3], "unit_y": units[i % 3],
                    "x": "[1,2]", "y": "[0.1,0.2]",
                }
            )
    dets = detect(csv_path)
    d = _find(dets, "pivot", label="prop_y")
    assert d is not None
    partner = d["columns"]["partner"]
    assert partner == {"label": "prop_x", "unit": "unit_x", "value": "x"}


def test_detect_samples_flatten_sample_info() -> None:
    dets = detect(SAMPLES)
    d = _find(dets, "flatten", column="sample_info")
    assert d is not None
    assert d["evidence"]["rows"] > 0
    assert 0 < d["evidence"]["object_rate"] <= 1.0


def test_detect_curves_flatten_comments_double_encoded() -> None:
    """curves.csv の comments は JSON 文字列の中に JSON オブジェクトが入っている
    (二重符号化)。2段までほどく flatten 検出がこれを候補として拾う。"""
    dets = detect(CURVES)
    d = _find(dets, "flatten", column="comments")
    assert d is not None
    assert d["evidence"]["object_rate"] > 0


def test_detect_silent_for_project_names_json_array_string() -> None:
    """project_names は JSON 配列の文字列 — pivot のラベル候補にならず沈黙する。"""
    dets = detect(CURVES)
    assert _find(dets, "pivot", label="project_names") is None
    for d in dets:
        if d["kind"] == "pivot":
            partner = d["columns"].get("partner")
            assert d["columns"]["label"] != "project_names"
            if partner:
                assert partner["label"] != "project_names"


def test_detect_silent_for_category_without_unit_column(tmp_path: Path) -> None:
    """単位列の無いカテゴリ列（繰り返しのある文字列列だが、対になる単位列が無い）は
    pivot を出さない（G7: 証拠が無ければ黙る）。"""
    csv_path = tmp_path / "no_unit.csv"
    rows = [
        {"id": str(i), "category": cat, "value": str(i * 1.5)}
        for i, cat in enumerate(["alpha", "beta", "alpha", "beta", "gamma"] * 4)
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["id", "category", "value"])
        writer.writeheader()
        writer.writerows(rows)
    dets = detect(csv_path)
    assert _find(dets, "pivot") is None


def test_detect_silent_for_single_array_key_object() -> None:
    """R4: papers.csv の issued = {"date_parts":[[2014,4,15]]} はキーが1種類だけで、
    その値がスカラでもオブジェクトでもない（配列だけ）なので flatten を出さない。"""
    dets = detect(PAPERS)
    assert _find(dets, "flatten", column="issued") is None


def test_detect_silent_for_single_array_key_object_with_empty_cells(tmp_path: Path) -> None:
    """実データの回帰: date_parts 形の行に混じって "{}"（空オブジェクト）の行がある
    と、空オブジェクトはキーを持たないので "全部が配列" 判定から誤って除外されて
    しまい沈黙条件が効かなかった。空オブジェクトは「値が無いだけ」として無視して
    判定する。100行中数行が "{}" の人工データで確認する。"""
    csv_path = tmp_path / "issued_with_empty.csv"
    fieldnames = ["sid", "issued"]
    rows = []
    for i in range(100):
        cell = "{}" if i % 17 == 0 else json.dumps({"date_parts": [[2014, (i % 12) + 1, 1]]})
        rows.append({"sid": str(i), "issued": cell})
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    dets = detect(csv_path)
    assert _find(dets, "flatten", column="issued") is None


# ===========================================================================
# 2. propose()
# ===========================================================================


def test_propose_zt_and_seebeck_groups_thermopower_separate() -> None:
    dets = detect(CURVES)
    ops = propose(CURVES, dets)
    zt = _pivot_group(ops, "zt")
    assert zt["label"] == "ZT"
    assert zt["unit"] == "-"

    seebeck = _pivot_group(ops, "seebeck-coefficient")
    assert seebeck["label"] == "Seebeck coefficient"
    assert seebeck["unit"] == "V*K^(-1)"

    # thermopower は語の同一視をしないので Seebeck とは別群（人の仕事）。
    thermopower = _pivot_group(ops, "thermopower")
    assert thermopower["label"] == "thermopower"
    assert thermopower["slug"] != seebeck["slug"]


def test_propose_double_space_seebeck_folds_wrong_unit_excluded() -> None:
    """二重空白の 'Seebeck  coefficient' は 'Seebeck coefficient' の群に畳まれる
    (空白正規化のみ)。単位が V / V/K の行は群の単位 (V*K^(-1)) と違うので members
    に入らない。"""
    dets = detect(CURVES)
    ops = propose(CURVES, dets)
    seebeck = _pivot_group(ops, "seebeck-coefficient")
    labels_in_group = {m["label"] for m in seebeck["members"]}
    units_in_group = {m["unit"] for m in seebeck["members"]}
    assert labels_in_group == {"Seebeck coefficient"}
    assert units_in_group == {"V*K^(-1)"}
    assert "V" not in units_in_group
    assert "V/K" not in units_in_group


def test_propose_seebeck_other_units_holds_excluded_spelling_variants() -> None:
    """R5/R6: members に入らなかった (label, unit) は other_units に残るので、
    人が「同じ単位だ」と足せる（curves.csv の 'Seebeck  coefficient' は unit 'V' の
    行と 'V/K' の行が2つとも他の群には吸収されず other_units に見える）。"""
    dets = detect(CURVES)
    ops = propose(CURVES, dets)
    seebeck = _pivot_group(ops, "seebeck-coefficient")
    other_pairs = {(o["label"], o["unit"]) for o in seebeck["other_units"]}
    assert ("Seebeck  coefficient", "V") in other_pairs
    assert ("Seebeck  coefficient", "V/K") in other_pairs
    for o in seebeck["other_units"]:
        assert o["rows"] >= 1
    # members とは重ならない。
    member_pairs = {(m["label"], m["unit"]) for m in seebeck["members"]}
    assert member_pairs.isdisjoint(other_pairs)


def test_apply_after_moving_other_unit_into_members_includes_its_points(
    tmp_path: Path,
) -> None:
    """人が other_units の1件を members に移した spec で apply すると、その行の点が
    表に入る。"""
    dets = detect(CURVES)
    ops = propose(CURVES, dets)
    seebeck = _pivot_group(ops, "seebeck-coefficient")

    before_spec = {"version": 1, "ops": ops}
    before_result = apply(before_spec, FIXTURES, tmp_path / "before")
    pivot_idx = next(i for i, op in enumerate(ops) if op["kind"] == "pivot")
    before_rows_matched = before_result["counts"][str(pivot_idx)]["rows_matched"].get(
        "seebeck-coefficient", 0
    )

    # "V" 単位の行 (SID 18790) は partner (prop_x) が "Temperture Difference" で
    # デフォルトの partner (Temperature) と一致しないので、それだけを移しても点は
    # 増えない。"V/K" 単位の行 (SID 34545) は partner が "Temperature"/"K" と一致
    # するので、これを選ぶ。
    moved = next(o for o in seebeck["other_units"] if o["unit"] == "V/K")
    seebeck["other_units"] = [o for o in seebeck["other_units"] if o is not moved]
    seebeck["members"].append(
        {"label": moved["label"], "unit": moved["unit"], "rows": moved["rows"]}
    )

    after_spec = {"version": 1, "ops": ops}
    assert validate_spec(after_spec) == []
    after_result = apply(after_spec, FIXTURES, tmp_path / "after")
    after_rows_matched = after_result["counts"][str(pivot_idx)]["rows_matched"].get(
        "seebeck-coefficient", 0
    )
    assert after_rows_matched == before_rows_matched + 1

    with (tmp_path / "after" / seebeck["table"]).open(
        encoding="utf-8", newline=""
    ) as fh:
        rows = list(csv.DictReader(fh))
    assert any(r["SID"] == "34545" for r in rows)


def test_validate_spec_rejects_pair_in_both_members_and_other_units() -> None:
    spec = {
        "version": 1,
        "ops": [
            {
                "kind": "pivot",
                "source": "curves.csv",
                "dialect": {},
                "carry": [],
                "label": "prop_y",
                "unit": "unit_y",
                "value": "y",
                "groups": [
                    {
                        "slug": "zt",
                        "label": "ZT",
                        "unit": "-",
                        "table": "curves__zt.csv",
                        "members": [{"label": "ZT", "unit": "-"}],
                        "other_units": [{"label": "ZT", "unit": "-", "rows": 1}],
                    }
                ],
            }
        ],
    }
    errors = validate_spec(spec)
    assert errors
    assert any("both members and other_units" in e for e in errors)


def test_propose_partner_default_is_temperature_kelvin() -> None:
    dets = detect(CURVES)
    ops = propose(CURVES, dets)
    zt = _pivot_group(ops, "zt")
    assert zt["partner"]["label"] == "Temperature"
    assert zt["partner"]["unit"] == "K"


def test_propose_partner_does_not_fold_different_words_sharing_unit(tmp_path: Path) -> None:
    """partner の既定は「群の中で最頻の (正規化ラベル, 単位)」を選び、その正規化
    ラベルに畳まる**綴り違いだけ**を members に集める。単位が同じというだけで
    別の語（'T' と 'Temperature'）は畳まない — それは人が足す（R5/R8）。"""
    # detect() のラベル候補には distinct >= 2 が要る（label 列は distinct 2〜200）ので、
    # ZT 以外にもう2つの単発ラベルを混ぜて prop_y の distinct を3にし、unit_y の
    # distinct(2: "-"/"V") がそれより小さくなるようにする。
    csv_path = tmp_path / "partner_fold.csv"
    fieldnames = ["sid", "prop_x", "unit_x", "prop_y", "unit_y", "x", "y"]
    body = [
        {
            "sid": "1", "prop_x": "Temperature", "unit_x": "K", "prop_y": "ZT", "unit_y": "-",
            "x": "[1,2]", "y": "[0.1,0.2]",
        },
        {
            "sid": "2", "prop_x": "T", "unit_x": "K", "prop_y": "ZT", "unit_y": "-",
            "x": "[3,4]", "y": "[0.3,0.4]",
        },
        {
            "sid": "3", "prop_x": "Temperature", "unit_x": "K", "prop_y": "ZT", "unit_y": "-",
            "x": "[5,6]", "y": "[0.5,0.6]",
        },
        {
            "sid": "4", "prop_x": "Temperature", "unit_x": "K", "prop_y": "Seebeck coefficient",
            "unit_y": "V", "x": "[7,8]", "y": "[0.7,0.8]",
        },
        {
            "sid": "5", "prop_x": "Temperature", "unit_x": "K", "prop_y": "Dielectric loss",
            "unit_y": "-", "x": "[9,10]", "y": "[0.9,1.0]",
        },
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(body)
    dets = detect(csv_path)
    ops = propose(csv_path, dets)
    zt = _pivot_group(ops, "zt")
    partner_labels = {m["label"] for m in zt["partner"]["members"]}
    # "T" は最頻ではない別の綴り = 別の語なので畳まれない（Temperature の2件が最頻）。
    assert partner_labels == {"Temperature"}
    assert all(m["unit"] == "K" for m in zt["partner"]["members"])


def test_propose_partner_folds_whitespace_spelling_variant(tmp_path: Path) -> None:
    """partner の既定は綴り違い（空白正規化 + 大文字小文字の同一視）だけを畳む。
    'Ambient Temperature' と内部が二重空白の 'Ambient  Temperature' 変種は同じ語
    なので畳まれる（前後の空白は dialect 層で既に strip される — R15 — ので、
    区別できるのは内部の連続空白だけ）。"""
    csv_path = tmp_path / "partner_spelling.csv"
    fieldnames = ["sid", "prop_x", "unit_x", "prop_y", "unit_y", "x", "y"]
    body = [
        {
            "sid": "1", "prop_x": "Ambient Temperature", "unit_x": "K", "prop_y": "ZT",
            "unit_y": "-", "x": "[1,2]", "y": "[0.1,0.2]",
        },
        {
            "sid": "2", "prop_x": "Ambient  Temperature", "unit_x": "K", "prop_y": "ZT",
            "unit_y": "-", "x": "[3,4]", "y": "[0.3,0.4]",
        },
        {
            "sid": "3", "prop_x": "Ambient Temperature", "unit_x": "K",
            "prop_y": "Seebeck coefficient", "unit_y": "V", "x": "[7,8]", "y": "[0.7,0.8]",
        },
        {
            "sid": "4", "prop_x": "Ambient Temperature", "unit_x": "K", "prop_y": "Dielectric loss",
            "unit_y": "-", "x": "[9,10]", "y": "[0.9,1.0]",
        },
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(body)
    dets = detect(csv_path)
    ops = propose(csv_path, dets)
    zt = _pivot_group(ops, "zt")
    partner_labels = {m["label"] for m in zt["partner"]["members"]}
    assert partner_labels == {"Ambient Temperature", "Ambient  Temperature"}


def test_propose_carry_columns() -> None:
    dets = detect(CURVES)
    ops = propose(CURVES, dets)
    pivot_op = next(o for o in ops if o["kind"] == "pivot")
    carry = pivot_op["carry"]
    for col in ("SID", "figure_id", "sample_id", "composition", "DOI"):
        assert col in carry, f"{col} は carry に入るはず: {carry}"
    for col in ("comments", "created_at", "updated_at"):
        assert col not in carry, f"{col} は op が消費/除外されるべき: {carry}"


def test_propose_carry_excludes_json_array_column() -> None:
    """R8: project_names（``["ThermoelectricMaterials"]`` のような JSON 配列の文字列）
    は「決まった書き方」の見かけ（空白なし・40文字以内）を満たしても carry から除外
    する。"""
    dets = detect(CURVES)
    ops = propose(CURVES, dets)
    pivot_op = next(o for o in ops if o["kind"] == "pivot")
    assert "project_names" not in pivot_op["carry"]


def test_propose_pivot_group_has_enabled_and_rows() -> None:
    """R5/R7: 群には enabled と rows（全行走査での一致行数）が付く。members にも
    rows が付く。"""
    dets = detect(CURVES)
    ops = propose(CURVES, dets)
    zt = _pivot_group(ops, "zt")
    assert zt["enabled"] is True
    assert zt["rows"] == sum(m["rows"] for m in zt["members"])
    assert all("rows" in m for m in zt["members"])


def test_propose_pivot_enables_only_top_12_groups_by_rows(tmp_path: Path) -> None:
    """R5/R7: 13 群以上あると行数上位12群だけ enabled になり、残りは無効化される。"""
    csv_path = tmp_path / "many_groups.csv"
    fieldnames = ["id", "test_metric", "unit", "metric"]
    rows = []
    rid = 0
    for i in range(13):
        label, unit = f"L{i}", f"U{i}"
        for _ in range(20 - i):
            rows.append(
                {"id": str(rid), "test_metric": label, "unit": unit, "metric": str(rid)}
            )
            rid += 1
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    dets = detect(csv_path)
    ops = propose(csv_path, dets)
    pivot_op = next(o for o in ops if o["kind"] == "pivot")
    groups = pivot_op["groups"]
    assert len(groups) == 13

    enabled = [g for g in groups if g["enabled"]]
    disabled = [g for g in groups if not g["enabled"]]
    assert len(enabled) == 12
    assert len(disabled) == 1
    assert disabled[0]["slug"] == "l12"
    assert disabled[0]["rows"] == 8
    assert all(g["rows"] >= 9 for g in enabled)

    spec = {"version": 1, "ops": ops}
    result = apply(spec, tmp_path, tmp_path / "out")
    # 無効な群は表を作らない。
    assert not (tmp_path / "out" / disabled[0]["table"]).exists()
    for g in enabled:
        assert (tmp_path / "out" / g["table"]).exists()

    pivot_idx = next(i for i, op in enumerate(ops) if op["kind"] == "pivot")
    counts = result["counts"][str(pivot_idx)]
    # 無効な群 (L12, 8行) は rows_unmatched に数えられる。
    assert counts["rows_unmatched"] == 8
    assert "l12" not in counts["rows_matched"]


def test_propose_non_ascii_labels_get_unique_slugs(tmp_path: Path) -> None:
    """slugify は非ASCIIだけのラベル(ギリシャ文字など)を "unknown" にまとめるため、
    複数の非ASCIIラベルがあると群の slug・表名・値列名が衝突して validate_spec が
    拒否していた（実データ: curves.csv の "\u03c3"/"\u03ba"）。propose がラベル由来の
    ハッシュで一意な slug を割り当てることを確認する。"""
    csv_path = tmp_path / "non_ascii.csv"
    fieldnames = ["id", "test_metric", "unit", "metric"]
    rows = []
    rid = 0
    for label, unit in [("\u03c3", "U0"), ("\u03ba", "U1"), ("ZT", "U2")]:
        for _ in range(5):
            rows.append({"id": str(rid), "test_metric": label, "unit": unit, "metric": str(rid)})
            rid += 1
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    dets = detect(csv_path)
    ops = propose(csv_path, dets)
    spec = {"version": 1, "ops": ops}
    assert validate_spec(spec) == []

    pivot_op = next(o for o in ops if o["kind"] == "pivot")
    slugs = [g["slug"] for g in pivot_op["groups"]]
    assert len(slugs) == len(set(slugs))
    tables = [g["table"] for g in pivot_op["groups"]]
    assert len(tables) == len(set(tables))
    zt = next(g for g in pivot_op["groups"] if g["label"] == "ZT")
    assert zt["slug"] == "zt"
    non_ascii_slugs = [g["slug"] for g in pivot_op["groups"] if g["label"] != "ZT"]
    assert all(s.startswith("label-") for s in non_ascii_slugs)
    assert len(set(non_ascii_slugs)) == 2

    apply(spec, tmp_path, tmp_path / "out")
    for g in pivot_op["groups"]:
        assert (tmp_path / "out" / g["table"]).exists()


# ===========================================================================
# 3. apply(): 保存則・参照実装との一致
# ===========================================================================


def _reference_zt_points() -> list[tuple[str, str, str]]:
    """独立した参照実装: curves.csv を素の csv+json で読み、prop_y=='ZT' かつ
    unit_y=='-' かつ prop_x=='Temperature' かつ unit_x=='K' の行を zip して
    (curve_key, temperature_token, zt_token) を返す。reshape の内部関数は一切
    使わない。"""
    out = []
    with CURVES.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if row["prop_y"] != "ZT" or row["unit_y"] != "-":
                continue
            if row["prop_x"] != "Temperature" or row["unit_x"] != "K":
                continue
            xs = json.loads(row["x"], parse_int=str, parse_float=str)
            ys = json.loads(row["y"], parse_int=str, parse_float=str)
            for xt, yt in zip(xs, ys, strict=True):
                out.append((row["SID"], xt, yt))
    return out


def test_apply_zt_matches_independent_reference(tmp_path: Path) -> None:
    dets = detect(CURVES)
    ops = propose(CURVES, dets)
    spec = {"version": 1, "ops": ops}
    result = apply(spec, FIXTURES, tmp_path)

    zt = _pivot_group(ops, "zt")
    with (tmp_path / zt["table"]).open(encoding="utf-8", newline="") as fh:
        applied_rows = list(csv.DictReader(fh))

    reference = _reference_zt_points()
    assert len(applied_rows) == len(reference)
    applied_triples = [(r["SID"], r["temperature"], r["zt"]) for r in applied_rows]
    assert applied_triples == reference

    pivot_idx = next(i for i, op in enumerate(ops) if op["kind"] == "pivot")
    counts = result["counts"][str(pivot_idx)]
    assert counts["tables"][zt["table"]] == len(reference)


def test_apply_preserves_20_digit_integer(tmp_path: Path) -> None:
    dets = detect(CURVES)
    ops = propose(CURVES, dets)
    spec = {"version": 1, "ops": ops}
    apply(spec, FIXTURES, tmp_path)
    cc = _pivot_group(ops, "carrier-concentration")
    text = (tmp_path / cc["table"]).read_text(encoding="utf-8")
    assert "96895790000000000000" in text


def test_apply_empty_array_row_counted_not_dropped(tmp_path: Path) -> None:
    """thermopower の行 (x=[], y=[]) は elements 0 の行として扱われ、保存則の勘定
    から落ちない(counts に現れる)が、出力表には点が0行 = ヘッダのみになる。"""
    dets = detect(CURVES)
    ops = propose(CURVES, dets)
    spec = {"version": 1, "ops": ops}
    result = apply(spec, FIXTURES, tmp_path)

    pivot_idx = next(i for i, op in enumerate(ops) if op["kind"] == "pivot")
    counts = result["counts"][str(pivot_idx)]
    assert counts["rows_matched"]["thermopower"] == 1

    thermopower = _pivot_group(ops, "thermopower")
    lines = (tmp_path / thermopower["table"]).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1  # ヘッダのみ


def test_apply_determinism(tmp_path: Path) -> None:
    dets = detect(CURVES)
    ops = propose(CURVES, dets)
    spec = {"version": 1, "ops": ops}
    dest1, dest2 = tmp_path / "run1", tmp_path / "run2"
    apply(spec, FIXTURES, dest1)
    apply(spec, FIXTURES, dest2)

    files1 = sorted(p.name for p in dest1.iterdir())
    files2 = sorted(p.name for p in dest2.iterdir())
    assert files1 == files2
    for name in files1:
        assert (dest1 / name).read_bytes() == (dest2 / name).read_bytes()


def test_propose_curves_omits_standalone_explode_consumed_by_pivot() -> None:
    """R7: pivot が内包する explode（value 列 + partner 値列 = x, y）と同じ配列集合の
    単独 explode は、curves.csv の propose では提案しない（pivot だけになる）。"""
    dets = detect(CURVES)
    ops = propose(CURVES, dets)
    assert not any(o["kind"] == "explode" for o in ops)
    pivot_op = next(o for o in ops if o["kind"] == "pivot")
    assert pivot_op.get("explode", {}).get("arrays") == ["x", "y"]


def test_propose_standalone_explode_without_pivot(tmp_path: Path) -> None:
    """explode 単体のテスト: pivot 候補が無い表では、単独 explode がそのまま提案
    される（pivot と重ならない限り消えない）。"""
    csv_path = tmp_path / "arrays_only.csv"
    fieldnames = ["id", "x", "y"]
    rows = [
        {"id": str(i), "x": json.dumps([i, i + 1]), "y": json.dumps([i * 2, i * 2 + 1])}
        for i in range(5)
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    dets = detect(csv_path)
    ops = propose(csv_path, dets)
    assert any(o["kind"] == "explode" for o in ops)


# ===========================================================================
# 5. R6: validate_spec
# ===========================================================================


def test_validate_spec_rejects_same_label_unit_in_two_groups() -> None:
    spec = {
        "version": 1,
        "ops": [
            {
                "kind": "pivot",
                "source": "curves.csv",
                "dialect": {},
                "carry": [],
                "label": "prop_y",
                "unit": "unit_y",
                "value": "y",
                "groups": [
                    {
                        "slug": "zt",
                        "label": "ZT",
                        "unit": "-",
                        "table": "curves__zt.csv",
                        "members": [{"label": "ZT", "unit": "-"}],
                    },
                    {
                        "slug": "zt-dup",
                        "label": "ZT",
                        "unit": "-",
                        "table": "curves__zt-dup.csv",
                        "members": [{"label": "ZT", "unit": "-"}],
                    },
                ],
            }
        ],
    }
    errors = validate_spec(spec)
    assert errors
    assert any("two groups" in e for e in errors)


def test_validate_spec_accepts_proposed_spec() -> None:
    dets = detect(CURVES)
    ops = propose(CURVES, dets)
    spec = {"version": 1, "ops": ops}
    assert validate_spec(spec) == []


def test_validate_spec_rejects_duplicate_table_name() -> None:
    spec = {
        "version": 1,
        "ops": [
            {
                "kind": "explode",
                "source": "curves.csv",
                "dialect": {},
                "table": "curves__x-y.csv",
                "arrays": ["x", "y"],
                "carry": [],
            },
            {
                "kind": "flatten",
                "source": "samples.csv",
                "dialect": {},
                "column": "sample_info",
                "carry": [],
                "long": {"table": "curves__x-y.csv", "fields": []},
                "wide": {"table": "samples__sample_info-wide.csv", "keys": [], "fields": []},
            },
        ],
    }
    errors = validate_spec(spec)
    assert any("duplicate table" in e for e in errors)


def test_validate_spec_rejects_pivot_value_partner_slug_collision() -> None:
    """R9: 群のラベルと partner ラベルが別綴りでも slug が同じ列名になると、
    _write_csv が片方を無言で上書きしてしまう。validate_spec が拒否する。"""
    spec = {
        "version": 1,
        "ops": [
            {
                "kind": "pivot",
                "source": "curves.csv",
                "dialect": {},
                "carry": [],
                "label": "prop_y",
                "unit": "unit_y",
                "value": "y",
                "groups": [
                    {
                        "slug": "zt",
                        "label": "ZT",
                        "unit": "-",
                        "table": "curves__zt.csv",
                        "members": [{"label": "ZT", "unit": "-"}],
                        "partner": {
                            "label": "ZT",
                            "unit": "K",
                            "members": [{"label": "ZT", "unit": "K"}],
                        },
                    }
                ],
            }
        ],
    }
    errors = validate_spec(spec)
    assert errors
    assert any("column name collision" in e for e in errors)


# ===========================================================================
# 6. R14: check_op_against_header
# ===========================================================================


def test_check_op_against_header_missing_column() -> None:
    dets = detect(CURVES)
    ops = propose(CURVES, dets)
    pivot_op = next(o for o in ops if o["kind"] == "pivot")
    full_header = list(next(csv.reader(CURVES.open(encoding="utf-8-sig"))))
    stale_header = [c for c in full_header if c != "unit_y"]
    reason = check_op_against_header(pivot_op, stale_header)
    assert reason is not None
    assert reason.startswith("reshape.op_stale")
    assert "unit_y" in reason


def test_check_op_against_header_ok_when_all_present() -> None:
    dets = detect(CURVES)
    ops = propose(CURVES, dets)
    full_header = list(next(csv.reader(CURVES.open(encoding="utf-8-sig"))))
    for op in ops:
        assert check_op_against_header(op, full_header) is None


# ===========================================================================
# 7. 未知ラベル: 既存 spec を無関係な CSV に当てても派生表のスキーマは変わらない
# ===========================================================================


def test_apply_unknown_labels_yields_empty_tables_same_schema(tmp_path: Path) -> None:
    dets = detect(CURVES)
    ops = propose(CURVES, dets)
    spec = {"version": 1, "ops": ops}

    # curves.csv と同じ列だが、prop_y/prop_x に判断表が知らないラベルだけを持つ CSV。
    with CURVES.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        fieldnames = reader.fieldnames
        rows = list(reader)
    unknown_csv = tmp_path / "src" / "starrydata_curves.csv"
    unknown_csv.parent.mkdir(parents=True)
    with unknown_csv.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            row = dict(row)
            row["prop_y"] = "Some Unknown Property"
            row["prop_x"] = "Some Unknown Axis"
            writer.writerow(row)

    dest = tmp_path / "dest"
    result = apply(spec, unknown_csv.parent, dest)

    zt = _pivot_group(ops, "zt")
    lines = (dest / zt["table"]).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1  # ヘッダのみ
    assert lines[0].split(",") == [
        "SID", "sample_id", "figure_id", "DOI", "composition", "figure_name",
        "point_index", "temperature", "zt",
    ]

    pivot_idx = next(i for i, op in enumerate(ops) if op["kind"] == "pivot")
    counts = result["counts"][str(pivot_idx)]
    assert counts["rows_unmatched"] == counts["source_rows"]
    assert sum(counts["rows_matched"].values()) == 0


# ===========================================================================
# 8. flatten: samples.csv の sample_info と curves.csv の comments
# ===========================================================================


def test_apply_flatten_samples_long_table(tmp_path: Path) -> None:
    dets = detect(SAMPLES)
    ops = propose(SAMPLES, dets)
    spec = {"version": 1, "ops": ops}
    result = apply(spec, FIXTURES, tmp_path)

    flatten_op = next(o for o in ops if o["kind"] == "flatten")
    with (tmp_path / flatten_op["long"]["table"]).open(encoding="utf-8", newline="") as fh:
        long_rows = list(csv.DictReader(fh))
    header = long_rows[0].keys() if long_rows else []
    with (tmp_path / flatten_op["long"]["table"]).open(encoding="utf-8", newline="") as fh:
        header = next(csv.reader(fh))
    assert "category" in header
    assert "comment" in header
    assert any(r["category"] for r in long_rows)

    idx = next(i for i, op in enumerate(ops) if op["kind"] == "flatten")
    counts = result["counts"][str(idx)]
    assert counts["entries_empty"] > 0
    assert counts["entries_in"] == counts["rows_out"] + counts["entries_empty"]


def test_apply_flatten_curves_comments_condition_keys(tmp_path: Path) -> None:
    dets = detect(CURVES)
    ops = propose(CURVES, dets)
    spec = {"version": 1, "ops": ops}
    apply(spec, FIXTURES, tmp_path)

    flatten_op = next(o for o in ops if o["kind"] == "flatten" and o["column"] == "comments")
    with (tmp_path / flatten_op["long"]["table"]).open(encoding="utf-8", newline="") as fh:
        long_rows = list(csv.DictReader(fh))
    keys = {r["key_raw"] for r in long_rows}
    assert "AC frequency (Hz)" in keys


def test_apply_flatten_wide_row_count_equals_input(tmp_path: Path) -> None:
    dets = detect(SAMPLES)
    ops = propose(SAMPLES, dets)
    spec = {"version": 1, "ops": ops}
    apply(spec, FIXTURES, tmp_path)

    flatten_op = next(o for o in ops if o["kind"] == "flatten")
    with SAMPLES.open(encoding="utf-8-sig", newline="") as fh:
        input_rows = list(csv.DictReader(fh))
    with (tmp_path / flatten_op["wide"]["table"]).open(encoding="utf-8", newline="") as fh:
        wide_rows = list(csv.DictReader(fh))
    assert len(wide_rows) == len(input_rows)


def test_propose_flatten_wide_keys_preserve_original_case() -> None:
    """§4.3: wide キー選定・列名は空白正規化だけで casefold しない。実データで
    "MaterialFamily" が "materialfamily" になっていた欠陥の回帰テスト。"""
    dets = detect(SAMPLES)
    ops = propose(SAMPLES, dets)
    flatten_op = next(o for o in ops if o["kind"] == "flatten")
    wide_keys = flatten_op["wide"]["keys"]
    assert "MaterialFamily" in wide_keys
    assert "Form" in wide_keys
    assert "materialfamily" not in wide_keys


def test_apply_flatten_wide_columns_preserve_original_case(tmp_path: Path) -> None:
    dets = detect(SAMPLES)
    ops = propose(SAMPLES, dets)
    spec = {"version": 1, "ops": ops}
    apply(spec, FIXTURES, tmp_path)

    flatten_op = next(o for o in ops if o["kind"] == "flatten")
    with (tmp_path / flatten_op["wide"]["table"]).open(encoding="utf-8", newline="") as fh:
        header = next(csv.reader(fh))
    assert any(c.startswith("MaterialFamily") for c in header)
    assert not any("materialfamily" in c for c in header)


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def test_apply_flatten_preserves_numeric_token_precision(tmp_path: Path) -> None:
    """R16/§4.3: flatten の対象セルがオブジェクトのときも、数値フィールドは元の
    トークンをそのまま書く（float を経由して桁が変わってはいけない）。"""
    src = tmp_path / "src.csv"
    cell = '{"conc":{"precise":0.12345678901234567890,"n":98765432109876543210}}'
    _write_csv(src, ["sid", "info"], [{"sid": "1", "info": cell}])
    op = {
        "kind": "flatten",
        "source": "src.csv",
        "dialect": {},
        "column": "info",
        "carry": ["sid"],
        "long": {"table": "src__info.csv", "fields": ["precise", "n"]},
        "wide": {"table": "src__info-wide.csv", "keys": [], "fields": []},
    }
    spec = {"version": 1, "ops": [op]}
    apply(spec, tmp_path, tmp_path / "out")
    with (tmp_path / "out" / op["long"]["table"]).open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["precise"] == "0.12345678901234567890"
    assert rows[0]["n"] == "98765432109876543210"


def test_apply_flatten_survives_malformed_json_cell(tmp_path: Path) -> None:
    """R11: 壊れた JSON セルが混ざっていても apply() は例外を出さず、そのセルは
    空のオブジェクトとして扱われる（AttributeError でクラッシュしない）。"""
    src = tmp_path / "src.csv"
    _write_csv(
        src,
        ["sid", "info"],
        [
            {"sid": "1", "info": "{not valid json"},
            {"sid": "2", "info": '{"a":{"category":"y"}}'},
        ],
    )
    op = {
        "kind": "flatten",
        "source": "src.csv",
        "dialect": {},
        "column": "info",
        "carry": ["sid"],
        "long": {"table": "src__info.csv", "fields": ["category"]},
        "wide": {"table": "src__info-wide.csv", "keys": ["a"], "fields": ["category"]},
    }
    spec = {"version": 1, "ops": [op]}
    result = apply(spec, tmp_path, tmp_path / "out")
    counts = result["counts"]["0"]
    assert counts["source_rows"] == 2
    assert counts["entries_in"] == 1
    assert counts["wide_rows_out"] == 2


def test_dedupe_table_name_avoids_reusing_hashed_candidate() -> None:
    """R10: 3 綴り以上が同じ表名 slug に畳まれても、衝突解消のハッシュ付き候補
    どうしがまた衝突してはいけない（同名を返すと validate_spec が重複表名で拒否する）。"""
    from asterism.reshape import _dedupe_table_name

    used: set[str] = set()
    names = [_dedupe_table_name("x__power-factor.csv", used) for _ in range(4)]
    assert len(names) == len(set(names))


def test_apply_flatten_wide_normalizes_and_counts_key_collisions(tmp_path: Path) -> None:
    """R11/§4.3: 正規化後に同じ key になる生キーが複数あれば wide 表では 1 列に
    まとまり（初出が勝つ）、負けた側は wide_key_collisions に数える。"""
    src = tmp_path / "src.csv"
    rows = [
        {
            "sid": str(i),
            "info": json.dumps(
                {
                    "coercivity": {"category": "A"},
                    " coercivity": {"category": "B"},
                }
            ),
        }
        for i in range(10)
    ]
    _write_csv(src, ["sid", "info"], rows)

    dets = detect(src)
    ops = propose(src, dets)
    spec = {"version": 1, "ops": ops}
    assert validate_spec(spec) == []

    flatten_op = next(o for o in ops if o["kind"] == "flatten")
    assert flatten_op["wide"]["keys"].count("coercivity") == 1

    result = apply(spec, tmp_path, tmp_path / "out")
    idx = next(i for i, op in enumerate(ops) if op["kind"] == "flatten")
    counts = result["counts"][str(idx)]
    assert counts["wide_key_collisions"] == 10

    with (tmp_path / "out" / flatten_op["wide"]["table"]).open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        wide_rows = list(reader)
    assert header.count("coercivity__category") == 1
    assert wide_rows[0]["coercivity__category"] == "A"


def test_propose_flatten_wide_excludes_keys_with_all_empty_values(tmp_path: Path) -> None:
    """R11/§4.3: 充足率は ``_entry_is_empty`` で空でないエントリの割合（long の
    entries_empty と同じ判定）。実データの sample_info は全キーが
    ``{"category":"","comment":"","extracted":""}`` として存在するため、単なる
    「キーの存在率」では 'coercivity' のようなほぼ空のキーまで wide に選ばれて
    しまう — 全行にキーは存在するが値が空のキーは wide.keys に選ばれない。"""
    src = tmp_path / "samples.csv"
    rows = []
    for i in range(20):
        category = "Bi2Te3" if i < 10 else ""
        rows.append(
            {
                "sid": str(i),
                "info": json.dumps(
                    {
                        "AlwaysBlank": {"category": "", "comment": "", "extracted": ""},
                        "SometimesFilled": {
                            "category": category,
                            "comment": "",
                            "extracted": "",
                        },
                    }
                ),
            }
        )
    _write_csv(src, ["sid", "info"], rows)

    dets = detect(src)
    ops = propose(src, dets)
    flatten_op = next(o for o in ops if o["kind"] == "flatten")
    assert "SometimesFilled" in flatten_op["wide"]["keys"]
    assert "AlwaysBlank" not in flatten_op["wide"]["keys"]


def test_apply_flatten_wide_key_collision_ignored_when_losing_value_is_empty(
    tmp_path: Path,
) -> None:
    """R11/§4.3: 負けた側の生キーの値が空なら黙って捨てるだけで衝突に数えない。"""
    src = tmp_path / "src.csv"
    row = {
        "sid": "1",
        "info": json.dumps(
            {
                "coercivity": {"category": "A"},
                " coercivity": {"category": "", "comment": "", "extracted": ""},
            }
        ),
    }
    _write_csv(src, ["sid", "info"], [row])

    op = {
        "kind": "flatten",
        "source": "src.csv",
        "dialect": {},
        "column": "info",
        "carry": ["sid"],
        "long": {"table": "src__info.csv", "fields": ["category"]},
        "wide": {"table": "src__info-wide.csv", "keys": ["coercivity"], "fields": ["category"]},
    }
    spec = {"version": 1, "ops": [op]}
    result = apply(spec, tmp_path, tmp_path / "out")
    counts = result["counts"]["0"]
    assert counts["wide_key_collisions"] == 0
    with (tmp_path / "out" / op["wide"]["table"]).open(encoding="utf-8", newline="") as fh:
        wide_rows = list(csv.DictReader(fh))
    assert wide_rows[0]["coercivity__category"] == "A"


def test_apply_flatten_wide_key_collision_counted_when_losing_value_is_nonempty(
    tmp_path: Path,
) -> None:
    """R11/§4.3: 負けた側の生キーに値があれば（空でなければ）衝突として数える。"""
    src = tmp_path / "src.csv"
    row = {
        "sid": "1",
        "info": json.dumps(
            {
                "coercivity": {"category": "A"},
                " coercivity": {"category": "B"},
            }
        ),
    }
    _write_csv(src, ["sid", "info"], [row])

    op = {
        "kind": "flatten",
        "source": "src.csv",
        "dialect": {},
        "column": "info",
        "carry": ["sid"],
        "long": {"table": "src__info.csv", "fields": ["category"]},
        "wide": {"table": "src__info-wide.csv", "keys": ["coercivity"], "fields": ["category"]},
    }
    spec = {"version": 1, "ops": [op]}
    result = apply(spec, tmp_path, tmp_path / "out")
    counts = result["counts"]["0"]
    assert counts["wide_key_collisions"] == 1


# ===========================================================================
# 9. 保存則違反: ReshapeError かつ dest_dir に何も残らない
# ===========================================================================


def test_verify_conservation_rejects_bad_explode_counts() -> None:
    from asterism.reshape import _verify_conservation

    bad_counts = {
        "elements_in": 10,
        "rows_out": 3,
        "dropped_non_numeric": 0,
        "truncated_length_mismatch": 0,
    }
    with pytest.raises(ReshapeError, match=r"reshape\.conservation_violation"):
        _verify_conservation("explode", bad_counts)


def test_verify_conservation_rejects_bad_pivot_counts() -> None:
    from asterism.reshape import _verify_conservation

    bad_counts = {
        "source_rows": 10,
        "rows_unmatched": 1,
        "rows_matched": {"zt": 3},  # 3+1=4 != 10
        "tables": {"curves__zt.csv": 3},
        "dropped_non_numeric": 0,
        "truncated_length_mismatch": 0,
        "elements_matched": 3,
    }
    with pytest.raises(ReshapeError, match=r"reshape\.conservation_violation"):
        _verify_conservation("pivot", bad_counts)


def test_apply_invalid_spec_raises_and_writes_nothing(tmp_path: Path) -> None:
    """R6 違反の spec は validate_spec で拒否され、apply は ReshapeError を投げて
    dest_dir には何も残らない。"""
    spec = {
        "version": 1,
        "ops": [
            {
                "kind": "pivot",
                "source": "starrydata_curves.csv",
                "dialect": {},
                "carry": [],
                "label": "prop_y",
                "unit": "unit_y",
                "value": "y",
                "groups": [
                    {
                        "slug": "zt",
                        "label": "ZT",
                        "unit": "-",
                        "table": "curves__zt.csv",
                        "members": [{"label": "ZT", "unit": "-"}],
                    },
                    {
                        "slug": "zt-dup",
                        "label": "ZT",
                        "unit": "-",
                        "table": "curves__zt-dup.csv",
                        "members": [{"label": "ZT", "unit": "-"}],
                    },
                ],
            }
        ],
    }
    dest = tmp_path / "dest"
    with pytest.raises(ReshapeError):
        apply(spec, FIXTURES, dest)
    assert not dest.exists() or list(dest.iterdir()) == []


# ===========================================================================
# derived_tables()
# ===========================================================================


def test_derived_tables_lists_all_output_tables_in_order() -> None:
    dets = detect(CURVES)
    ops = propose(CURVES, dets)
    spec = {"version": 1, "ops": ops}
    names = derived_tables(spec)
    # R7: curves.csv の x/y は pivot が内包する explode が消費するので、単独 explode
    # は propose() に出てこない — pivot が ops の先頭になる。
    pivot_op = next(o for o in ops if o["kind"] == "pivot")
    flatten_op = next(o for o in ops if o["kind"] == "flatten")
    assert names[0] == pivot_op["groups"][0]["table"]
    assert flatten_op["long"]["table"] in names
    assert flatten_op["wide"]["table"] in names


def test_derived_tables_excludes_disabled_pivot_groups() -> None:
    """R5/R7: enabled=false の群は derived_tables に含まれない（表を作らないため）。"""
    spec = {
        "version": 1,
        "ops": [
            {
                "kind": "pivot",
                "source": "curves.csv",
                "dialect": {},
                "carry": [],
                "label": "prop_y",
                "unit": "unit_y",
                "value": "y",
                "groups": [
                    {
                        "slug": "zt", "label": "ZT", "unit": "-", "table": "curves__zt.csv",
                        "rows": 10, "enabled": True,
                        "members": [{"label": "ZT", "unit": "-", "rows": 10}],
                    },
                    {
                        "slug": "rare", "label": "Rare", "unit": "-", "table": "curves__rare.csv",
                        "rows": 1, "enabled": False,
                        "members": [{"label": "Rare", "unit": "-", "rows": 1}],
                    },
                ],
            }
        ],
    }
    names = derived_tables(spec)
    assert names == ["curves__zt.csv"]
