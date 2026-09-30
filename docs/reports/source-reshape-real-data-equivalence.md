# 表の形をととのえる層 — Starrydata 実データでの同値検証

日付: 2026-09-03 ／ 対象: `asterism.reshape`（ADR [`source-reshape.md`](../architecture/source-reshape.md)）／ 入力: Starrydata 公開 CSV 3 本（2026-05-27 snapshot）

## Q（問い）

`asterism.reshape` の「検出 → 既定の提案 → 人の判断表の編集 → 適用」は、同じ判断を手で書いた決定論スクリプト（`starrydata_dataset/tidy/build_tidy.py`・別名表 `aliases.csv`。曲線 233,103 本を 9 本の型付き点表に展開したもの）と**同じ派生表**を作るか。値は 1 桁も変わらないか。

## Method（方法）

1. `detect()` を 3 CSV に回す（全体から等間隔の 20,000 行）。
2. `propose()` で既定の判断表を作る（全行走査。綴りだけ畳む）。
3. 判断表を、`aliases.csv` と同じ判断で人が編集する（機械が黙ってやらない部分）:
   - 語の同一視: `thermopower` → Seebeck coefficient、`total thermal conductivity` → Thermal conductivity
   - 単位の綴りの同一視: Electrical conductivity の `S*m^(-1)` を `ohm^(-1)*m^(-1)` の群へ（`other_units` から移す）、Seebeck の `V/K` を `V*K^(-1)` の群へ
   - partner の略記: `T`（K）を `Temperature` の partner に足す
   - 有効な群を熱電の 9 物性に絞る（既定は行数上位 12 群で、電池・磁性・誘電が含まれていた）
4. `apply()` で派生表を作り、tidy 束の `points_*.csv` と行数を突き合わせる。ZT は先頭 200,000 行の (SID, figure_id, sample_id, point_index) で結合し、温度と値を**数値として**比較する。
5. 20 桁の整数（キャリア濃度 curve 7833-25148-31313 の 96895790000000000000）が元トークンのまま出るか見る。

## Result（結果）

| 項目 | 値 |
|---|---|
| 検出 | curves: explode(x, y) と pivot(prop_y / unit_y / y、partner prop_x / unit_x / x)。samples: flatten(sample_info)。papers: 沈黙（`issued` はキー 1 つで値が配列） |
| 既定の提案 | 群 163、有効 12（行数上位）。carry = SID, sample_id, figure_id, DOI, composition, figure_name |
| 所要時間 | detect 6.1 s ／ propose 30.6 s ／ apply 9.5 s（有効 9 群）・21 s（既定 12 群） |
| 保存則 | `elements_matched` 2,603,946 = 派生表 9 本の行数合計。`dropped_non_numeric` 0、`truncated_length_mismatch` 0 |

派生表の行数（人の編集後）:

| 群 | reshape | tidy 束 | 一致 |
|---|---|---|---|
| zt | 285,296 | 285,296 | ✓ |
| seebeck-coefficient | 721,906 | 721,906 | ✓ |
| electrical-conductivity | 276,798 | 276,798 | ✓ |
| electrical-resistivity | 542,775 | 542,775 | ✓ |
| thermal-conductivity | 394,421 | 394,421 | ✓ |
| lattice-thermal-conductivity | 90,294 | 90,294 | ✓ |
| power-factor | 251,620 | 251,620 | ✓ |
| carrier-concentration | 26,127 | 26,127 | ✓ |
| carrier-mobility | 14,709 | 14,709 | ✓ |

ZT の値: 先頭 200,000 行で数値の不一致 0。文字列としては tidy 側が `7.27595e-05`（float の repr）、reshape 側が `0.0000727595`（元トークン）で、reshape の方が原資料に忠実。20 桁の整数は `96895790000000000000` のまま。

## Conclusion（結論）

- 判断を人が同じように与えれば、汎用の reshape 層は Starrydata 専用に手で書いた変換と**同じ表**を作る。データセット固有コードは要らない。
- 機械の既定（綴りだけ畳む）と人の判断（語・単位・略記の同一視）の線引きは、実データで 5 件の編集で済んだ。編集の候補（`other_units`）が判断表に見えていることが前提で、これは検証の途中で発見して足した。
- 数値は元トークンで写す方が手元スクリプトより忠実だった。

## Limitations（限界）

- 既定で有効な 12 群は行数順なので、熱電以外（電池・磁性・誘電）の群も含まれる。絞るのは人。
- flatten の wide 表は充足率上位 12 キーに凍結される。選ばれなかったキーは long 表と raw に残る。
- この検証は手元の実データで行った（リポジトリの `ingest/tests/fixtures/reshape/` は同じ形の 22 行の抜粋）。

## Reproduce（再現）

```bash
# 1. Starrydata の公開 CSV 3 本を src/ に curves.csv / samples.csv / papers.csv として置く
# 2. 既定の提案と適用
cd ingest && uv run python - <<'EOF'
from pathlib import Path
from asterism import reshape
src = Path("src"); det = {n: reshape.detect(src / n) for n in ("curves.csv", "samples.csv", "papers.csv")}
ops = [op for n, d in det.items() if d for op in reshape.propose(src / n, d)]
spec = {"version": 1, "ops": ops}
assert not reshape.validate_spec(spec)
res = reshape.apply(spec, src, Path("derived"))
print(res["counts"])
EOF
# 3. 判断表の編集は Method の 3. のとおり（groups[].members / other_units / partner.members / enabled）
```

リポジトリ内では `ingest/tests/test_reshape.py` が同じ契約を 22 行の抜粋で検査する。
