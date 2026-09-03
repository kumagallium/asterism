"""表の形をととのえる — 配列セル・値としての物性名・入れ子 JSON を、決定論で派生表にする。

配列セル（並行する2列以上を添字で対応づけて初めて「点」になる）・値としての物性名
（ラベル列の値ごとに単位と値が変わる long/EAV 形）・入れ子 JSON（1セルの中に構造化
データが入っている）の3つを、宣言経路（RML）の手前で「1行=1記録・1セル=値1つ」の
表に直す。LLM はこの層に一切関与しない: 検出・既定の提案・適用はすべて決定論の
純関数で、人が変えられるのは判断表（ReshapeSpec の ops）だけ。

決定の記録: ``docs/architecture/source-reshape.md``（R1〜R20・§4）。
"""

from __future__ import annotations

import copy
import csv
import dataclasses
import hashlib
import json
import math
import re
import shutil
import tempfile
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

from asterism.dialect import DEFAULT_DIALECT, SourceDialect, dialect_rows
from asterism.text import slugify

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

# R4: 検出は「ファイル全体から等間隔に取った 20,000 行」で行う（先頭だけだと分布が
# 偏る — 実データでは先頭が熱電だけで prop_x が1種）。§4.3 の flatten フィールド選定
# も同じ母集団を使う。stride = ceil(N / MAX_DETECT_ROWS) で index % stride == 0 の行を
# 取る（決定論）。
MAX_DETECT_ROWS = 20000

# R5: pivot の既定で有効にする群の上限（行数上位。同数は初出順）。
_ENABLED_GROUPS_MAX = 12

# R8: 「ID らしい列」= 名前が id/sid/key/index/no で終わる（大文字小文字を区別しない）。
# ``(?:^|_)`` により列名全体がこの語そのもの、または "_" の後に続く場合だけにマッチし、
# "grid" のような偶然の部分一致は拾わない。
_ID_RE = re.compile(r"(?:^|_)(id|sid|key|index|no)$", re.IGNORECASE)

# R8: 「決まった書き方の列」= 非空値の 90% 以上が空白を含まず 40 文字以内。
_FIXED_MAX_LEN = 40
_FIXED_MIN_RATE = 0.9
_CARRY_MAX_COLUMNS = 12

# R4 の「値としての物性名」検出パラメータ。distinct 2〜200 は ADR そのまま。
# 「ユニーク率」は、この列の値が(繰り返しの少ない)自由記述かどうかの判定に使う —
# ここでは「その値がこの列内で1回しか出てこない行の割合」(singleton rate) として実装
# する。distinct/total（カーディナリティ比）は数百件規模のコーパス向けで、本パッケージ
# のテストフィクスチャ(22行)のような小さな表では成立しないため、実装上の判断として
# singleton rate を採用した（詳細は PR の説明を参照）。
# 主ラベル（および partner の無い standalone）は distinct >= 2 が必須。partner は
# distinct == 1 でも成立させる（例: x 軸が全部 "Temperature" でも partner になる）。
_LABEL_MIN_DISTINCT = 2
_PARTNER_MIN_DISTINCT = 1
_LABEL_MAX_DISTINCT = 200
_LABEL_MAX_SINGLETON_RATE = 0.6

# 単位候補の関数従属: forward（ラベル→単位）は 90% 以上一致、reverse（単位→ラベル）は
# 70% 以上一致を要求する。forward だけでは、単位列が全体的に強く偏っている（例:
# 22行中21行が "K"）場合にどんな列とも「見かけ上」高い一致率になってしまうため、
# reverse も要求して真に対応する列だけを選ぶ（実データで検証済み）。
_UNIT_FWD_MIN_FD = 0.9
_UNIT_REV_MIN_FD = 0.7

_WS_RE = re.compile(r"\s+")


class ReshapeError(ValueError):
    """保存則違反・spec 不正。"""


# ---------------------------------------------------------------------------
# 行の読み方（R15: reshape は常に dialect を通して読む）
# ---------------------------------------------------------------------------


def read_rows(path: Path | str, dialect: SourceDialect) -> Iterator[dict[str, str]]:
    """``dialect`` 経由で ``path`` を読み、行ごとに ``{列名: 値}`` を返す。

    トークン化そのものは :func:`asterism.dialect.dialect_rows`（design/ingest 両側で
    共有される唯一の tokenizer）に委ねる — ここではヘッダ行を列名として辞書化するだけ。
    行の幅がヘッダと食い違う場合はヘッダ幅に揃える（欠損セルは空文字、超過セルは
    切り捨て）。
    """
    rows = dialect_rows(path, dialect)
    header = next(rows, None)
    if header is None:
        return
    width = len(header)
    for tokens in rows:
        fitted = list(tokens[:width]) + [""] * (width - len(tokens))
        yield dict(zip(header, fitted, strict=True))


# ---------------------------------------------------------------------------
# 小さな道具（JSON 判定・命名）
# ---------------------------------------------------------------------------

_PARSE_FAIL = object()


def _cell_json(cell: str) -> object:
    try:
        return json.loads(cell)
    except json.JSONDecodeError:
        return _PARSE_FAIL


def _parse_array_tokens(cell: str) -> list:
    """R16: 配列セルを ``parse_int=str, parse_float=str`` で読み、元トークンをそのまま
    要素として返す（float を経由しないので 20 桁整数などが壊れない）。JSON として
    壊れている・配列でない場合は空リスト（呼び出し側で L=M=0 として扱われる）。
    """
    if not cell or not cell.strip():
        return []
    try:
        data = json.loads(cell, parse_int=str, parse_float=str)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _is_numeric_token(token: object) -> bool:
    """R16: 数値かどうかの判定だけ float で行う。真偽値は int のサブクラスだが数値とは
    見なさない。"""
    if isinstance(token, bool):
        return False
    if isinstance(token, str):
        try:
            float(token)
        except ValueError:
            return False
        return True
    return False


def _col_is_numeric_array(col: str, rows: list[dict[str, str]]) -> bool:
    """R4: 非空セルの全部が JSON の数値配列か。"""
    any_nonempty = False
    for row in rows:
        v = row.get(col, "")
        if not v.strip():
            continue
        any_nonempty = True
        data = _cell_json(v)
        if not isinstance(data, list):
            return False
        for e in data:
            if isinstance(e, bool) or not isinstance(e, (int, float)):
                return False
    return any_nonempty


def _col_is_json_list(col: str, rows: list[dict[str, str]]) -> bool:
    """非空セルの全部が JSON 配列（要素の型は問わない）か。project_names のような
    JSON 文字列配列を、pivot のラベル候補から除外するために使う。"""
    any_nonempty = False
    for row in rows:
        v = row.get(col, "")
        if not v.strip():
            continue
        any_nonempty = True
        if not isinstance(_cell_json(v), list):
            return False
    return any_nonempty


def _col_is_mostly_json(col: str, rows: list[dict[str, str]]) -> bool:
    """R8: 非空セルの多数(過半数)が JSON 配列またはオブジェクトの列。carry 候補から
    除外するために使う（``project_names`` = ``["…"]`` のような列）。"""
    vals = [row[col] for row in rows if row.get(col, "").strip()]
    if not vals:
        return False
    json_like = sum(1 for v in vals if isinstance(_cell_json(v), (list, dict)))
    return json_like / len(vals) > 0.5


def _col_is_plain_number(col: str, rows: list[dict[str, str]]) -> bool:
    """非空セルの全部が（配列でない）単一の数値か。"""
    any_nonempty = False
    for row in rows:
        v = row.get(col, "")
        if not v.strip():
            continue
        any_nonempty = True
        try:
            float(v)
        except ValueError:
            return False
    return any_nonempty


def _unwrap_json_object(cell: str) -> dict | None:
    """R4/§4.3: JSON 文字列を最大2段までほどき、JSON オブジェクトなら返す。
    直接オブジェクトの場合(1段)も、二重符号化(文字列の中に文字列化されたオブジェクト、
    2段)の場合も拾う。R16 と同じく ``parse_int=str, parse_float=str`` で読み、
    数値フィールドの元トークンをそのまま保つ（float を経由すると精度が壊れる）。"""
    value: object = cell
    for _ in range(2):
        if isinstance(value, dict):
            return value
        if not isinstance(value, str):
            return None
        try:
            value = json.loads(value, parse_int=str, parse_float=str)
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def _normalize_label(s: str) -> str:
    """R5: 空白正規化（前後 strip + 内部の連続空白を1つに）+ 大文字小文字の同一視。
    pivot のラベル・partner の綴り畳み（R5/R8）専用 — flatten のキーには使わない。"""
    return _WS_RE.sub(" ", s.strip()).casefold()


def _normalize_key(s: str) -> str:
    """§4.3: flatten のキー正規化（前後 strip + 内部の連続空白を1つに）。大文字小文字
    は変えない ＝ 人の付けたキーの綴りをそのまま保つ（列名にもなるため）。"""
    return _WS_RE.sub(" ", s.strip())


def _normalize_obj_keys(obj: dict, wanted: set[str] | None = None) -> tuple[dict, int]:
    """§4.3: flatten(wide) 用に、オブジェクトの生キーを ``_normalize_key`` で正規化
    する（空白正規化のみ・大文字小文字は変えない）。1 行の中で 2 つの生キーが同じ
    正規化後 key を取り合ったら初出の生キーが勝ち、負けた側は捨てる（long 表には
    key_raw のまま残るので消えない）。``wanted`` を渡すとその集合に属する key での
    衝突だけを数える（wide 表の列にならない key の衝突は ``wide_key_collisions`` に
    数えない）。負けた側の値が空（``_entry_is_empty``）なら黙って捨てるだけで衝突に
    数えない — 実データでは骨だけのキーどうしの「衝突」が大半で、値を持つ本物の
    衝突だけを人に知らせたい。戻り値は (正規化後 dict, 衝突件数)。"""
    out: dict = {}
    collisions = 0
    for key_raw, val in obj.items():
        key = _normalize_key(key_raw)
        if key not in out:
            out[key] = val
        elif (wanted is None or key in wanted) and not _entry_is_empty(val):
            collisions += 1
    return out, collisions


def _slugify_or_hash(text: str) -> str:
    """R9/R10: ``slugify`` が非ASCIIだけの入力で "unknown" にまとめてしまうと、
    "σ" と "κ" のような別々のラベルが同じ slug に衝突する。"unknown" になる場合だけ、
    元のテキスト由来のハッシュで一意化した ``label-<sha8>`` を返す（決定論。異なる
    テキストは異なるハッシュになるので、以後の重複解消は素通りする分だけで済む）。"""
    s = slugify(text)
    if s == "unknown":
        h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
        return f"label-{h}"
    return s


def _table_slug(text: str) -> str:
    """R10: 表名の slug（ハイフンを含んでよい — tabularize の sheet slug と同じ流儀）。"""
    return _slugify_or_hash(text)


def _col_slug(text: str) -> str:
    """R9: 列名の slug。RML の識別子として使うため、ハイフンをアンダースコアに変える。"""
    return _slugify_or_hash(text).replace("-", "_")


def _dedupe_table_name(name: str, used: set[str]) -> str:
    """R10: 表名の衝突は名前のハッシュで解く。ハッシュ付きの候補も衝突しうる
    （綴り違いが複数群あるとき）ので、``used`` に無くなるまで決定論的に候補を
    ずらして再チェックする。"""
    if name not in used:
        used.add(name)
        return name
    stem = name[:-4] if name.endswith(".csv") else name
    attempt = 1
    while True:
        h = hashlib.sha256(f"{name}:{attempt}".encode()).hexdigest()[:8]
        candidate = f"{stem}-{h}.csv"
        if candidate not in used:
            used.add(candidate)
            return candidate
        attempt += 1


def _unique_token(candidate: str, used: set[str], seed: str, *, sep: str = "-") -> str:
    """R9/R10: ``candidate`` が ``used`` と衝突したら、``seed``（元のラベル等）由来の
    8桁ハッシュを付けて一意にする（``_dedupe_table_name`` と同じ流儀。ハッシュ付きの
    候補もまた衝突しうるので attempt を進めて再ハッシュする）。"""
    if candidate not in used:
        used.add(candidate)
        return candidate
    attempt = 1
    while True:
        h = hashlib.sha256(f"{seed}:{attempt}".encode()).hexdigest()[:8]
        cand = f"{candidate}{sep}{h}"
        if cand not in used:
            used.add(cand)
            return cand
        attempt += 1


def _group_value_slug(g: dict) -> str:
    """R9: 群の値列名。propose が衝突回避で置いた ``value_slug`` があればそれを使い、
    無ければラベルから導出する（手書き spec との後方互換）。"""
    return g.get("value_slug") or _col_slug(g["label"])


def _group_partner_slug(g: dict) -> str | None:
    """R9: 群の partner 値列名（partner が無ければ None）。"""
    partner = g.get("partner")
    if not partner:
        return None
    return partner.get("value_slug") or _col_slug(partner["label"])


def _dialect_to_dict(dialect: SourceDialect) -> dict:
    """既定と異なるフィールドだけを辞書化する（emit-only-non-default。dialect.py の
    流儀に合わせる）。"""
    if dialect == DEFAULT_DIALECT:
        return {}
    out: dict = {}
    for f in dataclasses.fields(SourceDialect):
        default = f.default_factory() if f.default is dataclasses.MISSING else f.default
        val = getattr(dialect, f.name)
        if val != default:
            out[f.name] = val
    return out


def _dialect_from_op(op: dict) -> SourceDialect:
    raw = op.get("dialect") or {}
    return SourceDialect(**raw) if raw else DEFAULT_DIALECT


# ===========================================================================
# detect() — R4: 証拠が揃ったときだけ提案する
# ===========================================================================


def _stride_sample_rows(
    all_rows: list[dict[str, str]], max_rows: int
) -> list[dict[str, str]]:
    """R4: ``all_rows``（行数 N）から等間隔に最大 ``max_rows`` 件を抽出する。
    stride = max(1, ceil(N / max_rows)) で index % stride == 0 の行を取る（決定論。
    先頭だけを見ると分布が偏る — 実データでは先頭 20,000 行が熱電だけだった）。
    """
    total = len(all_rows)
    if total <= max_rows:
        return list(all_rows)
    stride = max(1, math.ceil(total / max_rows))
    return [row for i, row in enumerate(all_rows) if i % stride == 0]


def detect(
    path: Path | str, *, dialect: SourceDialect | None = None, max_rows: int = MAX_DETECT_ROWS
) -> list[dict]:
    """ファイル全体から等間隔に取った ``max_rows`` 行で、explode/pivot/flatten の
    候補を返す（R4: 母集団はファイル先頭ではなく等間隔サンプル）。

    証拠が揃わなければ何も返さない（G7: 沈黙が既定）。同じ入力からは常に同じ結果
    （list の順序も含めて決定論）。
    """
    dialect = dialect or DEFAULT_DIALECT
    source = Path(path).name
    all_rows = list(read_rows(path, dialect))
    if not all_rows:
        return []
    rows = _stride_sample_rows(all_rows, max_rows)
    columns = list(rows[0].keys())

    detections: list[dict] = []
    detections.extend(_detect_explode(columns, rows, source))
    explode_groups = [d["columns"]["arrays"] for d in detections]
    detections.extend(_detect_pivot(columns, rows, source, explode_groups))
    detections.extend(_detect_flatten(columns, rows, source))
    return detections


def _detect_explode(columns: list[str], rows: list[dict[str, str]], source: str) -> list[dict]:
    """R4: 配列セル。並行列は行ごとの長さが 95% 以上一致するもの同士を1群にまとめる
    （連結成分。union-find）。"""
    array_cols = [c for c in columns if not _ID_RE.search(c) and _col_is_numeric_array(c, rows)]
    if len(array_cols) < 2:
        return []

    parsed = {
        c: [_cell_json(row.get(c, "")) if row.get(c, "").strip() else None for row in rows]
        for c in array_cols
    }
    parent = {c: c for c in array_cols}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(array_cols):
        for b in array_cols[i + 1 :]:
            total = match = 0
            for pa, pb in zip(parsed[a], parsed[b], strict=True):
                if pa is None or pb is None:
                    continue
                total += 1
                if len(pa) == len(pb):
                    match += 1
            if total and match / total >= 0.95:
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[ra] = rb

    groups_by_root: dict[str, list[str]] = {}
    for c in array_cols:
        groups_by_root.setdefault(find(c), []).append(c)
    groups = [g for g in groups_by_root.values() if len(g) >= 2]
    groups.sort(key=lambda g: min(columns.index(c) for c in g))

    n = len(rows)
    detections = []
    for members in groups:
        members_sorted = sorted(members, key=lambda c: columns.index(c))
        total = match = 0
        for idx in range(n):
            arrs = [parsed[c][idx] for c in members_sorted]
            if any(a is None for a in arrs):
                continue
            total += 1
            if len({len(a) for a in arrs}) == 1:
                match += 1
        rate = match / total if total else 0.0
        detections.append(
            {
                "kind": "explode",
                "source": source,
                "columns": {"arrays": members_sorted},
                "evidence": {"rows": n, "length_agreement": round(rate, 6)},
            }
        )
    return detections


def _distinct_count(col: str, rows: list[dict[str, str]]) -> int:
    return len({row[col] for row in rows if row.get(col, "").strip()})


def _singleton_rate(col: str, rows: list[dict[str, str]]) -> float:
    vals = [row[col] for row in rows if row.get(col, "").strip()]
    if not vals:
        return 1.0
    counts = Counter(vals)
    return sum(1 for _v, n in counts.items() if n == 1) / len(vals)


def _fd_rate(label_col: str, unit_col: str, rows: list[dict[str, str]]) -> float:
    """行の重み付き関数従属率: label の値が同じ行のうち、unit がその label の最頻値と
    一致する行の割合。"""
    by_label: dict[str, Counter] = {}
    for row in rows:
        label = row.get(label_col, "")
        if not label.strip():
            continue
        by_label.setdefault(label, Counter())[row.get(unit_col, "")] += 1
    match = total = 0
    for row in rows:
        label = row.get(label_col, "")
        if not label.strip():
            continue
        mode_unit, _n = by_label[label].most_common(1)[0]
        total += 1
        if row.get(unit_col, "") == mode_unit:
            match += 1
    return match / total if total else 0.0


def _detect_pivot(
    columns: list[str],
    rows: list[dict[str, str]],
    source: str,
    explode_groups: list[list[str]],
) -> list[dict]:
    """R4: 値としての物性名。ラベル列 L・単位列 U・値列 V の三つ組を構造的に探す。

    - U は L に対し forward FD >= 0.9 かつ reverse FD >= 0.7、distinct(U) <= distinct(L)
      を満たす候補の中から最良のものを選ぶ（forward だけでは、単位列が全体的に偏って
      いる場合にどのラベルでも高い一致率が出てしまうため、reverse も要求する）。
      distinct(U) <= distinct(L) は partner が distinct(L) == 1（例: x 軸が全部
      "Temperature"）の場合でも成立するよう「<」ではなく「<=」にしている。
    - V は「L の名前の最後の "_" トークンが、ある値列の名前と完全一致する」列
      （例: "prop_y" → "y"）。これは starrydata の命名規約に限らず、long/EAV 形の表で
      よく使われる一般的な命名慣習を使った紐付けであり、一致しない場合は L を採用
      しない（G7: 証拠がなければ黙る）。
    - 主ラベル（および partner の無い standalone）は distinct >= 2 が必須。partner
      候補は distinct == 1 でも triple の候補にする（後段でグループ化してから主ラベル
      を distinct の大きい方に決め、そちらが 2 未満なら群ごと捨てる）。
    """
    id_like = {c for c in columns if _ID_RE.search(c)}
    json_list_cols = {c for c in columns if _col_is_json_list(c, rows)}

    value_cols: set[str] = set()
    for g in explode_groups:
        value_cols |= set(g)
    for c in columns:
        if c in value_cols or c in id_like or c in json_list_cols:
            continue
        if _col_is_plain_number(c, rows):
            value_cols.add(c)

    label_candidates = []
    for c in columns:
        if c in id_like or c in json_list_cols or c in value_cols:
            continue
        vals = [row[c] for row in rows if row.get(c, "").strip()]
        if not vals:
            continue
        distinct = len(set(vals))
        if not (_PARTNER_MIN_DISTINCT <= distinct <= _LABEL_MAX_DISTINCT):
            continue
        if _singleton_rate(c, rows) > _LABEL_MAX_SINGLETON_RATE:
            continue
        label_candidates.append(c)

    triples = []
    for label in label_candidates:
        d_label = _distinct_count(label, rows)
        best = None
        for unit in columns:
            if unit == label or unit in id_like or unit in json_list_cols or unit in value_cols:
                continue
            u_vals = [row[unit] for row in rows if row.get(unit, "").strip()]
            if not u_vals:
                continue
            d_unit = len(set(u_vals))
            if d_unit > d_label:
                continue
            fwd = _fd_rate(label, unit, rows)
            if fwd < _UNIT_FWD_MIN_FD:
                continue
            rev = _fd_rate(unit, label, rows)
            if rev < _UNIT_REV_MIN_FD:
                continue
            rank = (-fwd, -rev, d_unit, columns.index(unit))
            if best is None or rank < best[0]:
                best = (rank, unit, fwd)
        if best is None:
            continue
        _rank, unit, fwd = best
        token = label.rsplit("_", 1)[-1]
        matches = [v for v in value_cols if v == token]
        if len(matches) != 1:
            continue
        triples.append(
            {"label": label, "unit": unit, "value": matches[0], "distinct": d_label, "fd": fwd}
        )

    group_index_of: dict[str, int] = {}
    for gi, members in enumerate(explode_groups):
        for c in members:
            group_index_of[c] = gi
    by_group: dict[int, list[dict]] = {}
    standalone: list[dict] = []
    for t in triples:
        gi = group_index_of.get(t["value"])
        if gi is None:
            standalone.append(t)
        else:
            by_group.setdefault(gi, []).append(t)

    detections = []
    for ts in by_group.values():
        ts.sort(key=lambda t: (-t["distinct"], columns.index(t["label"])))
        primary = ts[0]
        # 主ラベルは distinct >= 2 が必須（R4）。群の中で最大 distinct のものが
        # それに満たなければ、群ごと候補から外す（G7: 証拠がなければ黙る）。
        if primary["distinct"] < _LABEL_MIN_DISTINCT:
            continue
        # partner は primary と別の値列を指すものだけ（同じ値列を指す候補は「もう1組の
        # ラベル・値」ではなく、たまたま命名規約が一致しただけの別物）。
        partner = next((t for t in ts[1:] if t["value"] != primary["value"]), None)
        detections.append(_pivot_detection(source, primary, partner, rows))
    for t in standalone:
        # standalone（partner の無いラベル）は distinct >= 2 が必須。
        if t["distinct"] < _LABEL_MIN_DISTINCT:
            continue
        detections.append(_pivot_detection(source, t, None, rows))

    detections.sort(key=lambda d: columns.index(d["columns"]["label"]))
    return detections


def _pivot_detection(
    source: str, primary: dict, partner: dict | None, rows: list[dict[str, str]]
) -> dict:
    return {
        "kind": "pivot",
        "source": source,
        "columns": {
            "label": primary["label"],
            "unit": primary["unit"],
            "value": primary["value"],
            "partner": (
                {"label": partner["label"], "unit": partner["unit"], "value": partner["value"]}
                if partner
                else None
            ),
        },
        "evidence": {
            "distinct": primary["distinct"],
            "uniqueness": round(_singleton_rate(primary["label"], rows), 6),
            "unit_dependency": round(primary["fd"], 6),
        },
    }


def _detect_flatten(columns: list[str], rows: list[dict[str, str]], source: str) -> list[dict]:
    """R4: 入れ子 JSON。非空セルの全部が(最大2段までほどいた)JSON オブジェクトの列。

    キーが1種類だけで、その値がスカラでもオブジェクトでもない（配列だけ）なら黙る
    （例: ``issued`` = ``{"date_parts":[[2014,4,15]]}`` のような、事実上フラットな
    構造化データ。flatten しても壊すだけの1本道キーには証拠が無い）。
    """
    n = len(rows)
    detections = []
    for c in columns:
        vals = [row[c] for row in rows if row.get(c, "").strip()]
        if not vals:
            continue
        parsed = [_unwrap_json_object(v) for v in vals]
        if not all(obj is not None for obj in parsed):
            continue
        # 空オブジェクト（"{}"）は「値が無い」だけで、単一キーという証拠を否定しない
        # ので、all_keys とスカラ/配列判定はキーを持つオブジェクトだけで評価する
        # （実データの papers.csv issued に "{}" の行が混ざっていた）。
        non_empty = [obj for obj in parsed if obj]
        all_keys = {k for obj in non_empty for k in obj}
        if non_empty and len(all_keys) == 1:
            sole_key = next(iter(all_keys))
            if all(isinstance(obj.get(sole_key), list) for obj in non_empty):
                continue
        detections.append(
            {
                "kind": "flatten",
                "source": source,
                "columns": {"column": c},
                "evidence": {
                    "rows": n,
                    "object_rate": round(len(vals) / n, 6) if n else 0.0,
                },
            }
        )
    return detections


# ===========================================================================
# propose() — R5/R8/§4.3: 既定の判断表
# ===========================================================================


def propose(
    path: Path | str, detections: list[dict], *, dialect: SourceDialect | None = None
) -> list[dict]:
    """``detections``（:func:`detect` の出力）から、既定の判断表を持つ op の list を作る。

    群・持ち回り列・flatten のフィールドは全行（flatten のフィールド選定のみ
    ``MAX_DETECT_ROWS`` 行の接頭辞、§4.3）を走査して決める。人が判断表を編集した
    ものはこの既定を単に置き換える — この関数自体は毎回同じ入力から同じ出力を返す。
    """
    dialect = dialect or DEFAULT_DIALECT
    source = Path(path).name
    rows = list(read_rows(path, dialect))
    columns = list(rows[0].keys()) if rows else []
    dialect_dict = _dialect_to_dict(dialect)
    used_tables: set[str] = set()

    # pivot・flatten を先に作り、pivot が内包する explode（value 列 + partner 値列）が
    # 消費する配列集合を集める。R7: 同じ配列集合の単独 explode はそれと重ねて出さない
    # （人のオプトイン）。
    pivot_ops: list[dict] = []
    flatten_ops: list[dict] = []
    for det in detections:
        if det.get("source") != source:
            continue
        if det["kind"] == "pivot":
            pivot_ops.append(_propose_pivot(det, rows, columns, used_tables, dialect_dict))
        elif det["kind"] == "flatten":
            flatten_ops.append(_propose_flatten(det, rows, columns, used_tables, dialect_dict))

    consumed_arrays = {
        frozenset(op["explode"]["arrays"]) for op in pivot_ops if op.get("explode")
    }
    explode_ops: list[dict] = []
    for det in detections:
        if det.get("source") != source or det["kind"] != "explode":
            continue
        if frozenset(det["columns"]["arrays"]) in consumed_arrays:
            continue
        explode_ops.append(_propose_explode(det, rows, columns, used_tables, dialect_dict))

    return explode_ops + pivot_ops + flatten_ops


def _default_carry(
    columns: list[str], exclude: set[str], rows: list[dict[str, str]]
) -> list[str]:
    """R8: ID らしい列 + 決まった書き方の列（op が消費する列・JSON 配列/オブジェクトの
    列は除く）。合計 12 列超なら ID らしい列だけにする。"""
    id_like = [c for c in columns if c not in exclude and _ID_RE.search(c)]
    fixed = []
    for c in columns:
        if c in exclude or c in id_like:
            continue
        if _col_is_mostly_json(c, rows):
            continue
        vals = [row[c].strip() for row in rows if row.get(c, "").strip()]
        if not vals:
            continue
        ok = sum(1 for v in vals if not _WS_RE.search(v) and len(v) <= _FIXED_MAX_LEN)
        if ok / len(vals) >= _FIXED_MIN_RATE:
            fixed.append(c)
    carry = id_like + fixed
    if len(carry) > _CARRY_MAX_COLUMNS:
        return id_like
    return carry


def _propose_explode(
    det: dict,
    rows: list[dict[str, str]],
    columns: list[str],
    used_tables: set[str],
    dialect_dict: dict,
) -> dict:
    arrays = det["columns"]["arrays"]
    carry = _default_carry(columns, set(arrays), rows)
    stem = Path(det["source"]).stem
    slug = "-".join(_table_slug(a) for a in arrays)
    table = _dedupe_table_name(f"{stem}__{slug}.csv", used_tables)
    return {
        "kind": "explode",
        "source": det["source"],
        "dialect": dialect_dict,
        "table": table,
        "arrays": list(arrays),
        "index": "point_index",
        "carry": carry,
        "source_rows": len(rows),
    }


def _default_groups(
    rows: list[dict[str, str]], label_col: str, unit_col: str
) -> list[dict]:
    """R5: 既定の判断表。ラベルは空白正規化 + casefold だけで畳む。

    まず群の単位を、正規化ラベルが一致する行「全部」の単位の最頻値として決める
    （R5: 群の単位は最頻の単位1つ）。次に、その単位を持つ行**だけ**を母集団に、
    代表表記を最頻の生綴りとして決める — こうすると代表表記は常にそれ自身が
    members の一員になり（単位不一致で弾かれた綴りが代表になることがない）、
    members は同じ単位を持つ他の綴り（例: 二重空白の変種）も拾う。``Counter`` は
    挿入順を保つ性質を持つため、同数の tie はファイル内で先に現れた方が選ばれる
    （明示的な安定ソートを重ねていない — 意図的に Counter の挙動に依っている）。
    単位が群の代表と違う行は members に入らない（R6）。

    群には ``rows``（members に一致した全行数。ゲートが人に見せる数字）と
    ``enabled`` を持たせる（R5/R7）。既定で有効なのは行数上位 ``_ENABLED_GROUPS_MAX``
    群（同数はファイル内の初出順 = groups のここまでの並び順）。

    群には ``other_units``（同じ正規化ラベルで members に入らなかった (label, unit)
    の対。rows 降順・同数は初出順）も持たせる。単位の綴りが違うだけで同じ物性の行が
    どこにも見えなくなるのを防ぐ — 人が「同じ単位だ」と判断すれば members に移せる。
    apply は other_units を無視する（members だけが表の対象）。
    """
    units: dict[str, Counter] = {}
    first_index: dict[str, int] = {}
    for idx, row in enumerate(rows):
        label = row.get(label_col, "")
        if not label.strip():
            continue
        key = _normalize_label(label)
        units.setdefault(key, Counter())[row.get(unit_col, "")] += 1
        first_index.setdefault(key, idx)

    groups = []
    for key in sorted(first_index, key=lambda k: first_index[k]):
        rep_unit, _n = units[key].most_common(1)[0]
        spellings: Counter = Counter()
        seen: list[str] = []
        other_counts: Counter = Counter()
        other_first: dict[tuple, int] = {}
        for idx, row in enumerate(rows):
            label = row.get(label_col, "")
            if not label.strip() or _normalize_label(label) != key:
                continue
            unit = row.get(unit_col, "")
            if unit != rep_unit:
                # R5/R6: 同じ物性で単位の綴りが違う行。members には入れないが、
                # 人が「同じ単位だ」と足せるよう other_units に残す（apply は無視する）。
                pair = (label, unit)
                other_counts[pair] += 1
                other_first.setdefault(pair, idx)
                continue
            spellings[label] += 1
            if label not in seen:
                seen.append(label)
        rep_label, _n2 = spellings.most_common(1)[0]
        members = [{"label": lbl, "unit": rep_unit, "rows": spellings[lbl]} for lbl in seen]
        other_units = [
            {"label": lbl, "unit": unit, "rows": other_counts[(lbl, unit)]}
            for lbl, unit in sorted(
                other_counts, key=lambda pair: (-other_counts[pair], other_first[pair])
            )
        ]
        groups.append(
            {
                "slug": _table_slug(rep_label),
                "label": rep_label,
                "unit": rep_unit,
                "rows": sum(spellings.values()),
                "members": members,
                "other_units": other_units,
            }
        )

    # R5/R7: 既定で有効なのは行数上位 _ENABLED_GROUPS_MAX 群。同数は初出順（groups は
    # 既に first_index の昇順で並んでいるので、そのままのインデックス i が tie-break
    # になる）。
    order = sorted(range(len(groups)), key=lambda i: (-groups[i]["rows"], i))
    enabled_idx = set(order[:_ENABLED_GROUPS_MAX])
    for i, g in enumerate(groups):
        g["enabled"] = i in enabled_idx
    return groups


def _default_partner(
    rows: list[dict[str, str]],
    group: dict,
    label_col: str,
    unit_col: str,
    partner_label_col: str,
    partner_unit_col: str,
) -> dict | None:
    """1群の partner 既定（§4.2）: その群の members に一致する行だけを母集団に、
    最頻の (partner ラベル正規化, 単位) ペアを選び、その正規化ラベルに畳まる**綴り
    違いだけ**（空白正規化 + 大文字小文字の同一視）を members に集める。単位が同じと
    いうだけで別の語（"T" と "Temperature"）は畳まない — それは人が足す（R5/R8）。
    """
    member_set = {(m["label"], m["unit"]) for m in group["members"]}
    matching = [
        row for row in rows if (row.get(label_col, ""), row.get(unit_col, "")) in member_set
    ]

    pair_counts: Counter = Counter()
    pair_first: dict[tuple, int] = {}
    for idx, row in enumerate(matching):
        label = row.get(partner_label_col, "")
        if not label.strip():
            continue
        unit = row.get(partner_unit_col, "")
        key = (_normalize_label(label), unit)
        pair_counts[key] += 1
        pair_first.setdefault(key, idx)
    if not pair_counts:
        return None
    # 最頻の (正規化ラベル, 単位) を選ぶ。同数はファイル内で先に現れた方。
    best_key = max(pair_counts, key=lambda k: (pair_counts[k], -pair_first[k]))
    norm_label, best_unit = best_key

    spellings: Counter = Counter()
    seen_labels: list[str] = []
    for row in matching:
        label = row.get(partner_label_col, "")
        if not label.strip() or _normalize_label(label) != norm_label:
            continue
        if row.get(partner_unit_col, "") != best_unit:
            continue
        spellings[label] += 1
        if label not in seen_labels:
            seen_labels.append(label)
    if not spellings:
        return None
    rep_label, _n2 = spellings.most_common(1)[0]
    members = [{"label": lbl, "unit": best_unit} for lbl in seen_labels]
    return {
        "slug": _table_slug(rep_label),
        "label": rep_label,
        "unit": best_unit,
        "members": members,
    }


def _propose_pivot(
    det: dict,
    rows: list[dict[str, str]],
    columns: list[str],
    used_tables: set[str],
    dialect_dict: dict,
) -> dict:
    label_col = det["columns"]["label"]
    unit_col = det["columns"]["unit"]
    value_col = det["columns"]["value"]
    partner_cfg = det["columns"].get("partner")

    consumed = {label_col, unit_col, value_col}
    if partner_cfg:
        consumed |= {partner_cfg["label"], partner_cfg["unit"], partner_cfg["value"]}
    carry = _default_carry(columns, consumed, rows)
    stem = Path(det["source"]).stem

    groups = _default_groups(rows, label_col, unit_col)
    # R9/R10: 群 slug は spec 内（このピボットの groups 全部・enabled かどうかに関わらず
    # — validate_spec は無効な群も検証するので）で一意にする。非ASCIIだけのラベルは
    # _table_slug が既に "label-<sha8>" にしているので通常はここで衝突しない — それでも
    # 衝突したら _dedupe_table_name と同じ流儀でハッシュを付ける。
    used_group_slugs: set[str] = set()
    for g in groups:
        g["slug"] = _unique_token(g["slug"], used_group_slugs, g["label"], sep="-")
        g["table"] = _dedupe_table_name(f"{stem}__{g['slug']}.csv", used_tables)
        if partner_cfg:
            partner = _default_partner(
                rows, g, label_col, unit_col, partner_cfg["label"], partner_cfg["unit"]
            )
            if partner is not None:
                g["partner"] = partner

    # R9: 値列・partner列・carry の名前衝突を propose 側で回避する（validate_spec が
    # 拒否する前に）。衝突したら値列側にハッシュを付ける（partner・carry は変えない —
    # partner が carry と衝突する稀なケースだけ partner 側にハッシュを付ける）。
    for g in groups:
        reserved: set[str] = set(carry) | {"point_index"}
        partner_label = g.get("partner", {}).get("label") if g.get("partner") else None
        partner_slug = _col_slug(partner_label) if partner_label else None
        if partner_slug and partner_slug in reserved:
            g["partner"]["value_slug"] = _unique_token(
                partner_slug, reserved, partner_label, sep="_"
            )
        elif partner_slug:
            reserved.add(partner_slug)
        value_slug = _col_slug(g["label"])
        if value_slug in reserved:
            g["value_slug"] = _unique_token(value_slug, reserved, g["label"], sep="_")

    op: dict = {
        "kind": "pivot",
        "source": det["source"],
        "dialect": dialect_dict,
        "carry": carry,
        "label": label_col,
        "unit": unit_col,
        "value": value_col,
        "groups": groups,
        "source_rows": len(rows),
    }
    if _col_is_numeric_array(value_col, rows):
        arrays = [value_col] + ([partner_cfg["value"]] if partner_cfg else [])
        arrays.sort(key=columns.index)
        op["explode"] = {"arrays": arrays, "index": "point_index"}
    if partner_cfg:
        op["partner"] = {
            "label": partner_cfg["label"],
            "unit": partner_cfg["unit"],
            "value": partner_cfg["value"],
        }
    return op


def _entry_is_empty(val: object) -> bool:
    """§4.3: 値が空("", "{}", "[]", null)、または骨だけのオブジェクト
    （``{"category":"","comment":"","extracted":""}`` のようにキーはあるが全部の値が
    空）のエントリ。実データの sample_info はキー自体は全レコードに存在するので、
    トップレベルの ``{}`` チェックだけでは「充足率」が意味を持たない — 中の値まで
    再帰的に空かどうかを見る。"""
    if val is None or val == "" or val == []:
        return True
    if isinstance(val, dict):
        return all(_entry_is_empty(v) for v in val.values())
    return False


def _is_nonblank(val: object) -> bool:
    if val is None:
        return False
    if isinstance(val, str):
        return bool(val.strip())
    if isinstance(val, (dict, list)):
        return bool(val)
    return True


def _stringify_flat(val: object) -> str:
    if val is None:
        return ""
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (dict, list)):
        return json.dumps(val, ensure_ascii=False)
    return str(val)


def _propose_flatten(
    det: dict,
    rows: list[dict[str, str]],
    columns: list[str],
    used_tables: set[str],
    dialect_dict: dict,
) -> dict:
    """§4.3: long/wide の既定フィールドは等間隔に取った MAX_DETECT_ROWS 行で見えた
    field の充足率順・上位8。wide は充足率25%以上のキー上位12個（F' = 選ばれたキーの
    中で最頻のフィールド1つ）。母集団は detect() と同じ等間隔サンプル（R4/§4.3）。"""
    column = det["columns"]["column"]
    carry = _default_carry(columns, {column}, rows)
    stem = Path(det["source"]).stem
    scan_rows = _stride_sample_rows(rows, MAX_DETECT_ROWS)

    field_counts: Counter = Counter()
    field_first: dict[str, int] = {}
    key_counts: Counter = Counter()
    key_first: dict[str, int] = {}
    seq = 0
    for row in scan_rows:
        cell = row.get(column, "")
        if not cell.strip():
            continue
        obj = _unwrap_json_object(cell)
        if obj is None:
            continue
        for key_raw, val in obj.items():
            # R11/§4.3: K の選定は long と同じ空白正規化（大文字小文字は変えない）を
            # 通した key で行う。
            key = _normalize_key(key_raw)
            key_first.setdefault(key, seq)
            seq += 1
            if not _entry_is_empty(val):
                key_counts[key] += 1
            if isinstance(val, dict):
                for field, fval in val.items():
                    field_first.setdefault(field, seq)
                    seq += 1
                    if _is_nonblank(fval):
                        field_counts[field] += 1

    long_fields = sorted(field_counts, key=lambda f: (-field_counts[f], field_first[f]))[:8]

    total = len(scan_rows)
    wide_keys = [
        k
        for k in sorted(key_counts, key=lambda k: (-key_counts[k], key_first[k]))
        if total and key_counts[k] / total >= 0.25
    ][:12]

    wide_keys_set = set(wide_keys)
    wide_field_counts: Counter = Counter()
    wide_field_first: dict[str, int] = {}
    seq = 0
    for row in scan_rows:
        cell = row.get(column, "")
        if not cell.strip():
            continue
        obj = _unwrap_json_object(cell)
        if obj is None:
            continue
        norm_obj, _ = _normalize_obj_keys(obj, wide_keys_set)
        for key in wide_keys:
            val = norm_obj.get(key)
            if isinstance(val, dict):
                for field, fval in val.items():
                    wide_field_first.setdefault(field, seq)
                    seq += 1
                    if _is_nonblank(fval):
                        wide_field_counts[field] += 1
    wide_fields = sorted(
        wide_field_counts, key=lambda f: (-wide_field_counts[f], wide_field_first[f])
    )[:1]

    table_long = _dedupe_table_name(f"{stem}__{_table_slug(column)}.csv", used_tables)
    table_wide = _dedupe_table_name(f"{stem}__{_table_slug(column)}-wide.csv", used_tables)
    return {
        "kind": "flatten",
        "source": det["source"],
        "dialect": dialect_dict,
        "column": column,
        "carry": carry,
        "long": {"table": table_long, "fields": long_fields},
        "wide": {"table": table_wide, "keys": wide_keys, "fields": wide_fields},
        "source_rows": len(rows),
    }


# ===========================================================================
# validate_spec() / check_op_against_header()
# ===========================================================================


def validate_spec(spec: dict) -> list[str]:
    """spec の構造検証と R6（同じ (label, unit) が2群に属したら拒否）。表名の重複・
    source の欠落も検査する。違反があれば理由文字列の list を返す（空なら妥当）。"""
    if not isinstance(spec, dict):
        return ["reshape.invalid_spec: spec is not an object"]
    ops = spec.get("ops")
    if not isinstance(ops, list):
        return ["reshape.invalid_spec: ops is not a list"]

    errors: list[str] = []
    seen_tables: dict[str, int] = {}

    def _claim_table(table: object, idx: int) -> None:
        if not table:
            return
        if table in seen_tables:
            errors.append(f"reshape.invalid_spec: duplicate table {table!r}")
        else:
            seen_tables[table] = idx

    for idx, op in enumerate(ops):
        if not isinstance(op, dict):
            errors.append(f"reshape.invalid_spec: ops[{idx}] is not an object")
            continue
        kind = op.get("kind")
        if kind not in ("explode", "pivot", "flatten"):
            errors.append(f"reshape.invalid_spec: ops[{idx}].kind is invalid ({kind!r})")
            continue
        if not op.get("source"):
            errors.append(f"reshape.invalid_spec: ops[{idx}].source is missing")

        if kind == "explode":
            for field in ("table", "arrays", "carry"):
                if field not in op:
                    errors.append(f"reshape.invalid_spec: ops[{idx}] missing {field}")
            _claim_table(op.get("table"), idx)
        elif kind == "pivot":
            for field in ("label", "unit", "value", "carry", "groups"):
                if field not in op:
                    errors.append(f"reshape.invalid_spec: ops[{idx}] missing {field}")
            carry = op.get("carry") or []
            member_owner: dict[tuple, int] = {}
            for g in op.get("groups", []):
                _claim_table(g.get("table"), idx)
                # R9: value 列(slug) と partner 列(slug) は carry・point_index を含めて
                # 同一表内で名前が衝突してはいけない（衝突すると _write_csv で片方が
                # 無言で上書きされ、値が消える）。
                value_slug = _group_value_slug(g) if g.get("label") else None
                partner_slug = _group_partner_slug(g)
                col_names = [
                    *carry,
                    "point_index",
                    *([partner_slug] if partner_slug else []),
                    *([value_slug] if value_slug else []),
                ]
                dupes = sorted({c for c in col_names if col_names.count(c) > 1})
                if dupes:
                    errors.append(
                        f"reshape.invalid_spec: ops[{idx}] group {g.get('slug')!r} "
                        f"column name collision {dupes!r}"
                    )
                member_keys: set[tuple] = set()
                for m in g.get("members", []):
                    key = (m.get("label"), m.get("unit"))
                    member_keys.add(key)
                    if key in member_owner:
                        errors.append(
                            f"reshape.invalid_spec: (label, unit) {key!r} in two groups "
                            f"(ops[{idx}])"
                        )
                    else:
                        member_owner[key] = idx
                # R5/R6: other_units は members に入らなかった (label, unit) の残り
                # であるはず — 同じ対が両方に載っていたら判断表として矛盾している。
                for o in g.get("other_units", []):
                    okey = (o.get("label"), o.get("unit"))
                    if okey in member_keys:
                        errors.append(
                            f"reshape.invalid_spec: ops[{idx}] group {g.get('slug')!r} "
                            f"(label, unit) {okey!r} in both members and other_units"
                        )
        elif kind == "flatten":
            for field in ("column", "carry", "long", "wide"):
                if field not in op:
                    errors.append(f"reshape.invalid_spec: ops[{idx}] missing {field}")
            for sub in ("long", "wide"):
                cfg = op.get(sub)
                if isinstance(cfg, dict):
                    _claim_table(cfg.get("table"), idx)

    return errors


def check_op_against_header(op: dict, header: list[str]) -> str | None:
    """R14: op が参照する列が ``header`` に無ければ理由文字列を返す。無効化されない
    場合は None。"""
    header_set = set(header)
    source = op.get("source", "?")
    ref_cols: list[str] = []
    kind = op.get("kind")
    if kind == "explode":
        ref_cols += op.get("arrays", [])
    elif kind == "pivot":
        ref_cols += [op.get("label"), op.get("unit"), op.get("value")]
        partner = op.get("partner")
        if partner:
            ref_cols += [partner.get("label"), partner.get("unit"), partner.get("value")]
    elif kind == "flatten":
        ref_cols.append(op.get("column"))
    ref_cols += op.get("carry", [])

    for col in ref_cols:
        if col and col not in header_set:
            return f"reshape.op_stale: column {col} missing from {source}"
    return None


# ===========================================================================
# apply() — R11: 保存則
# ===========================================================================


def derived_tables(spec: dict) -> list[str]:
    """spec.ops が作る派生表名の順序付き list。R5/R7: pivot の無効な群（``enabled``
    が false）は表を作らないので含めない（省略時は既定で有効 = true）。"""
    names: list[str] = []
    for op in spec.get("ops", []):
        kind = op.get("kind")
        if kind == "explode":
            names.append(op["table"])
        elif kind == "pivot":
            for g in op.get("groups", []):
                if not g.get("enabled", True):
                    continue
                names.append(g["table"])
        elif kind == "flatten":
            names.append(op["long"]["table"])
            names.append(op["wide"]["table"])
    return names


def _verify_conservation(kind: str, counts: dict) -> None:
    """R11: op ごとの保存則を検査する。破れていれば :class:`ReshapeError`。

    ``apply`` から内部的に呼ばれるほか、テストからも直接呼べる（不正な counts を
    与えて違反を再現するため）。
    """
    if kind == "explode":
        lhs = counts["elements_in"]
        rhs = (
            counts["rows_out"]
            + counts["dropped_non_numeric"]
            + counts["truncated_length_mismatch"]
        )
        if lhs != rhs:
            raise ReshapeError(
                f"reshape.conservation_violation: explode elements_in={lhs} != {rhs} ({counts})"
            )
    elif kind == "pivot":
        lhs = counts["source_rows"]
        rhs = sum(counts["rows_matched"].values()) + counts["rows_unmatched"]
        if lhs != rhs:
            raise ReshapeError(
                f"reshape.conservation_violation: pivot source_rows={lhs} != {rhs} ({counts})"
            )
        elements_matched = (
            sum(counts["tables"].values())
            + counts["dropped_non_numeric"]
            + counts["truncated_length_mismatch"]
        )
        if elements_matched != counts["elements_matched"]:
            raise ReshapeError(
                "reshape.conservation_violation: pivot elements_matched="
                f"{counts['elements_matched']} != {elements_matched} ({counts})"
            )
    elif kind == "flatten":
        if counts["entries_in"] != counts["rows_out"] + counts["entries_empty"]:
            raise ReshapeError(
                "reshape.conservation_violation: flatten entries_in="
                f"{counts['entries_in']} != {counts['rows_out'] + counts['entries_empty']} "
                f"({counts})"
            )
        if counts["wide_rows_out"] != counts["source_rows"]:
            raise ReshapeError(
                "reshape.conservation_violation: flatten wide_rows_out="
                f"{counts['wide_rows_out']} != {counts['source_rows']} ({counts})"
            )
    else:
        raise ReshapeError(f"reshape.conservation_violation: unknown op kind {kind!r}")


def _apply_explode(op: dict, rows: list[dict[str, str]]) -> tuple[dict, dict]:
    """R4.1: explode。返り値は ``({表名: (列, 行のlist)}, counts)``。"""
    arrays = op["arrays"]
    carry = op["carry"]
    index_col = op.get("index", "point_index")
    cols = [*carry, index_col, *arrays]

    out_rows: list[dict[str, str]] = []
    elements_in = 0
    dropped_non_numeric = 0
    truncated = 0
    for row in rows:
        parsed = {a: _parse_array_tokens(row.get(a, "")) for a in arrays}
        lengths = [len(v) for v in parsed.values()]
        length_l = max(lengths) if lengths else 0
        length_m = min(lengths) if lengths else 0
        elements_in += length_l
        for i in range(length_l):
            if i >= length_m:
                truncated += 1
                continue
            tokens = {a: parsed[a][i] for a in arrays}
            if not all(_is_numeric_token(t) for t in tokens.values()):
                dropped_non_numeric += 1
                continue
            out = {c: row.get(c, "") for c in carry}
            out[index_col] = str(i)
            out.update(tokens)
            out_rows.append(out)

    counts = {
        "source_rows": len(rows),
        "parent_rows_in": len(rows),
        "elements_in": elements_in,
        "rows_out": len(out_rows),
        "dropped_non_numeric": dropped_non_numeric,
        "truncated_length_mismatch": truncated,
    }
    return {op["table"]: (cols, out_rows)}, counts


def _apply_pivot(op: dict, rows: list[dict[str, str]]) -> tuple[dict, dict]:
    """§4.2: pivot（value 列が配列なら explode を内包する）。"""
    label_col, unit_col, value_col = op["label"], op["unit"], op["value"]
    partner_cfg = op.get("partner")
    carry = op["carry"]
    index_col = "point_index"
    has_explode = bool(op.get("explode"))

    # R5/R7: enabled=false（省略時は既定 true）の群は表を作らない。その群の
    # (label, unit) は member_to_group に載らないので、行は自然に rows_unmatched に
    # 落ちる。
    member_to_group: dict[tuple, dict] = {}
    for g in op["groups"]:
        if not g.get("enabled", True):
            continue
        for m in g["members"]:
            member_to_group[(m["label"], m["unit"])] = g

    produced: dict[str, list[dict[str, str]]] = {}
    table_cols: dict[str, list[str]] = {}
    for g in op["groups"]:
        if not g.get("enabled", True):
            continue
        value_slug = _group_value_slug(g)
        partner_slug = _group_partner_slug(g)
        cols = [
            *carry,
            index_col,
            *([partner_slug] if partner_slug else []),
            value_slug,
        ]
        table_cols[g["table"]] = cols
        produced[g["table"]] = []

    rows_matched: Counter = Counter()
    rows_unmatched = 0
    dropped_non_numeric: Counter = Counter()
    truncated: Counter = Counter()
    source_rows = 0

    for row in rows:
        source_rows += 1
        key = (row.get(label_col, ""), row.get(unit_col, ""))
        group = member_to_group.get(key)
        if group is None:
            rows_unmatched += 1
            continue

        partner_slug = None
        if partner_cfg and group.get("partner"):
            partner_slug = _group_partner_slug(group)
            pkey = (row.get(partner_cfg["label"], ""), row.get(partner_cfg["unit"], ""))
            partner_members = {(m["label"], m["unit"]) for m in group["partner"]["members"]}
            if pkey not in partner_members:
                rows_unmatched += 1
                continue

        rows_matched[group["slug"]] += 1
        value_slug = _group_value_slug(group)
        out_list = produced[group["table"]]

        if has_explode:
            varr = _parse_array_tokens(row.get(value_col, ""))
            parr = (
                _parse_array_tokens(row.get(partner_cfg["value"], ""))
                if (partner_cfg and partner_slug)
                else None
            )
            length_l = max(len(varr), len(parr)) if parr is not None else len(varr)
            length_m = min(len(varr), len(parr)) if parr is not None else len(varr)
            for i in range(length_l):
                if i >= length_m:
                    truncated[group["slug"]] += 1
                    continue
                vtok = varr[i]
                ptok = parr[i] if parr is not None else None
                ok = _is_numeric_token(vtok) and (ptok is None or _is_numeric_token(ptok))
                if not ok:
                    dropped_non_numeric[group["slug"]] += 1
                    continue
                out = {c: row.get(c, "") for c in carry}
                out[index_col] = str(i)
                if partner_slug:
                    out[partner_slug] = ptok
                out[value_slug] = vtok
                out_list.append(out)
        else:
            vtok = row.get(value_col, "")
            ptok = row.get(partner_cfg["value"], "") if (partner_cfg and partner_slug) else None
            ok = _is_numeric_token(vtok) and (ptok is None or _is_numeric_token(ptok))
            if not ok:
                dropped_non_numeric[group["slug"]] += 1
                continue
            out = {c: row.get(c, "") for c in carry}
            out[index_col] = "0"
            if partner_slug:
                out[partner_slug] = ptok
            out[value_slug] = vtok
            out_list.append(out)

    tables_counts = {t: len(v) for t, v in produced.items()}
    counts = {
        "source_rows": source_rows,
        "rows_unmatched": rows_unmatched,
        "rows_matched": dict(rows_matched),
        "tables": tables_counts,
        "dropped_non_numeric": sum(dropped_non_numeric.values()),
        "truncated_length_mismatch": sum(truncated.values()),
        "elements_matched": (
            sum(tables_counts.values())
            + sum(dropped_non_numeric.values())
            + sum(truncated.values())
        ),
    }
    result_tables = {t: (table_cols[t], out_rows) for t, out_rows in produced.items()}
    return result_tables, counts


def _apply_flatten(op: dict, rows: list[dict[str, str]]) -> tuple[dict, dict]:
    """§4.3: flatten（long + wide の2表）。"""
    column = op["column"]
    carry = op["carry"]
    long_fields = op["long"]["fields"]
    wide_keys = op["wide"]["keys"]
    wide_fields = op["wide"]["fields"]
    source_columns = list(rows[0].keys()) if rows else []

    long_cols = [*carry, "key", "key_raw", "value", *long_fields, "value_json"]
    long_rows: list[dict[str, str]] = []
    entries_in = 0
    entries_empty = 0

    for row in rows:
        cell = row.get(column, "")
        obj = (_unwrap_json_object(cell) or {}) if cell.strip() else {}
        for key_raw, val in obj.items():
            entries_in += 1
            if _entry_is_empty(val):
                entries_empty += 1
                continue
            out = {c: row.get(c, "") for c in carry}
            out["key"] = _normalize_key(key_raw)
            out["key_raw"] = key_raw
            if isinstance(val, dict):
                out["value"] = ""
                for f in long_fields:
                    out[f] = _stringify_flat(val.get(f))
                extra = {k: v for k, v in val.items() if k not in long_fields}
                out["value_json"] = json.dumps(extra, ensure_ascii=False) if extra else ""
            else:
                out["value"] = _stringify_flat(val)
                for f in long_fields:
                    out[f] = ""
                out["value_json"] = ""
            long_rows.append(out)

    # wide: T の写し + 選ばれたキーの列。key は long と同じ正規化を通した後の値
    # （op["wide"]["keys"] は propose 時点で正規化済み）。1 行の中で 2 つの生キーが
    # 同じ key を取り合ったら初出が勝ち、負けた側は wide_key_collisions に数える。
    wide_keys_set = set(wide_keys)
    key_shapes: dict[str, str] = {}
    for key in wide_keys:
        is_object = False
        for row in rows:
            cell = row.get(column, "")
            obj = (_unwrap_json_object(cell) or {}) if cell.strip() else {}
            norm_obj, _ = _normalize_obj_keys(obj, wide_keys_set)
            if isinstance(norm_obj.get(key), dict):
                is_object = True
                break
        key_shapes[key] = "object" if is_object else "scalar"

    wide_cols = list(source_columns)
    for key in wide_keys:
        if key_shapes[key] == "object":
            if wide_fields:
                wide_cols.append(f"{key}__{wide_fields[0]}")
        else:
            wide_cols.append(key)

    wide_rows: list[dict[str, str]] = []
    wide_key_collisions = 0
    for row in rows:
        cell = row.get(column, "")
        obj = (_unwrap_json_object(cell) or {}) if cell.strip() else {}
        norm_obj, collisions = _normalize_obj_keys(obj, wide_keys_set)
        wide_key_collisions += collisions
        out = {c: row.get(c, "") for c in source_columns}
        for key in wide_keys:
            val = norm_obj.get(key)
            if key_shapes[key] == "object":
                if wide_fields:
                    out[f"{key}__{wide_fields[0]}"] = (
                        _stringify_flat(val.get(wide_fields[0])) if isinstance(val, dict) else ""
                    )
            else:
                out[key] = _stringify_flat(val) if not isinstance(val, dict) else ""
        wide_rows.append(out)

    counts = {
        "source_rows": len(rows),
        "entries_in": entries_in,
        "rows_out": len(long_rows),
        "entries_empty": entries_empty,
        "wide_rows_out": len(wide_rows),
        "wide_key_collisions": wide_key_collisions,
    }
    produced = {
        op["long"]["table"]: (long_cols, long_rows),
        op["wide"]["table"]: (wide_cols, wide_rows),
    }
    return produced, counts


def _columns_meta(op: dict, table_name: str, cols: list[str]) -> list[dict]:
    """R9: 列メタ（unit・出自）。pivot は群の情報から、flatten は long のフィールドから
    埋める。それ以外（explode・flatten の他の列）は unit/origin とも None。"""
    if op["kind"] == "pivot":
        group = next((g for g in op.get("groups", []) if g["table"] == table_name), None)
        if group is not None:
            value_slug = _group_value_slug(group)
            partner_slug = _group_partner_slug(group)
            out = []
            for c in cols:
                if c == value_slug:
                    spellings = list(dict.fromkeys(m["label"] for m in group["members"]))
                    out.append(
                        {
                            "name": c,
                            "unit": group["unit"],
                            "origin": f"{op['label']} = " + ", ".join(spellings),
                        }
                    )
                elif partner_slug and c == partner_slug:
                    spellings = list(
                        dict.fromkeys(m["label"] for m in group["partner"]["members"])
                    )
                    out.append(
                        {
                            "name": c,
                            "unit": group["partner"]["unit"],
                            "origin": f"{op['partner']['label']} = " + ", ".join(spellings),
                        }
                    )
                else:
                    out.append({"name": c, "unit": None, "origin": None})
            return out
    if op["kind"] == "flatten":
        long_fields = set(op["long"]["fields"])
        out = []
        for c in cols:
            if c in long_fields:
                out.append({"name": c, "unit": None, "origin": f"{op['column']} field {c}"})
            else:
                out.append({"name": c, "unit": None, "origin": None})
        return out
    return [{"name": c, "unit": None, "origin": None} for c in cols]


def _write_csv(path: Path, cols: list[str], rows: list[dict[str, str]]) -> None:
    """UTF-8・カンマ・LF・ヘッダ付きで書く（決定論: 行順は呼び出し側の list 順そのまま）。"""
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in cols})


def apply(
    spec: dict,
    src_dir: Path | str,
    dest_dir: Path | str,
    *,
    only_sources: set[str] | None = None,
) -> dict:
    """全 op を適用し、派生表を ``dest_dir`` に書く。

    まず一時ディレクトリに書き、op ごとに R11 の保存則を検査してから ``dest_dir`` へ
    移す。途中で違反があれば :class:`ReshapeError` を投げ、``dest_dir`` には何も
    残らない。返り値は ``spec`` のコピーに ``tables``/``counts`` を埋めたもの。
    """
    src_dir = Path(src_dir)
    dest_dir = Path(dest_dir)

    errors = validate_spec(spec)
    if errors:
        raise ReshapeError("; ".join(errors))

    ops = spec.get("ops", [])
    tables_out: dict[str, dict] = dict(spec.get("tables", {}))
    counts_out: dict[str, dict] = dict(spec.get("counts", {}))
    table_rows_accum: dict[str, list[dict[str, str]]] = {}
    table_columns: dict[str, list[str]] = {}

    for idx, op in enumerate(ops):
        if only_sources is not None and op.get("source") not in only_sources:
            continue
        src_path = src_dir / op["source"]
        dialect = _dialect_from_op(op)
        rows = list(read_rows(src_path, dialect))

        kind = op["kind"]
        if kind == "explode":
            produced, counts = _apply_explode(op, rows)
        elif kind == "pivot":
            produced, counts = _apply_pivot(op, rows)
        elif kind == "flatten":
            produced, counts = _apply_flatten(op, rows)
        else:  # pragma: no cover — validate_spec が先に拒否する
            raise ReshapeError(f"reshape.invalid_spec: unknown op kind {kind!r}")

        _verify_conservation(kind, counts)

        counts_out[str(idx)] = counts
        for table_name, (cols, out_rows) in produced.items():
            table_columns[table_name] = cols
            table_rows_accum[table_name] = out_rows
            tables_out[table_name] = {
                "from": op["source"],
                "op": idx,
                "columns": _columns_meta(op, table_name, cols),
            }

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for table_name, out_rows in table_rows_accum.items():
            _write_csv(tmp_path / table_name, table_columns[table_name], out_rows)

        dest_dir.mkdir(parents=True, exist_ok=True)
        for table_name in table_rows_accum:
            shutil.move(str(tmp_path / table_name), str(dest_dir / table_name))

    result = copy.deepcopy(spec)
    result["tables"] = tables_out
    result["counts"] = counts_out
    return result
