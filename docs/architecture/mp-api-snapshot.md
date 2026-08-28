# ADR: Materials Project — API スナップショットで starrydata の母相を埋める

決定: 2026-08-27 / status: **実装中**（branch `feat/mp-api-snapshot`）

関連: [`non-csv-sources.md`](non-csv-sources.md) §7（ライブ API は *fetch→JSON スナップショット*）/
[`id-move-after-publish.md`](id-move-after-publish.md)（ID の引っ越し・転送台帳）/
[`instance-iri-base.md`](instance-iri-base.md)（IRI 不変）/
[`tier0-object-path-and-python-literals.md`](tier0-object-path-and-python-literals.md)（dict/list セルの取り込み）

## 1. 文脈 — なぜ API でなければならないか

`materials_project` データセットの存在理由は `dataset.toml` にこう書いてある:

> Idealized crystal structures (space group / crystal system) from the Materials
> Project, keyed by reduced host formula — the structure dimension Starrydata lacks.

**空間群と結晶系がこのデータセットの主役**である。ところが実体は手書き seed の
**11 材料**だけだった。

MP のデータをどう入れるかで 3 つのソースを実測比較した（ROADMAP 2026-08-27）:

| ソース | 判定 |
|---|---|
| HDF5（materials-toolkits・133,420 件） | ✕ バッチ化フラット配列＋prefix-sum オフセットで RML がレコード境界を作れない |
| figshare JSON 3.8 GB（MEGNet） | ✕ 1 行巨大配列で OOM。`structure` は CIF 文字列で Tier 0 では解けない |
| mp_all.csv 841 MB（84k 件） | △ 取り込みは通る（実証済み）が、**空間群が入っていない** |

3 つとも主役を欠くか、そもそも入らない。**MP API が唯一の正しいソース**である。

## 2. 決定

| # | 論点 | 決定 |
|---|---|---|
| D1 | 範囲 | **starrydata に現れる母相組成だけ**。実測 6,057 件＝全 15 万件ミラーの 1/25。全件取得は「MP のミラーを作る」別プロジェクトであり、横断結合という目的には要らない |
| D2 | 経路 | ライブ接続ではなく **API → JSON スナップショット → 既存 JSON 経路**（`non-csv-sources.md` §7 の方針そのもの）。スナップショットが永続・引用可能なソース |
| D3 | 出力形 | 既存 `mp.json` と同じレコード形（`mp_id` / `formula` / `mp_page` / `structure{…}`）＋追加フィールド ⇒ **`mp.rml.ttl` は無改修** |
| D4 | 取得フィールド | `_all_fields=true` で取り、**除外リスト**で落とす。許可リストでは MP が足したフィールドに追随できない。現状 **51 取り込み / 18 除外** |
| D5 | 座標は入れない | MP の `structure`（全原子座標）は除外。mp_all.csv で確認したとおり、座標は SPARQL で問う対象にならず、トリプルを膨らませるだけ |
| D6 | 母相正規化 | `experiments/mp-linking-poc/link_mp.py` を動的 import して再利用。「母相とは何か」の定義を二重に持たない |
| D7 | 略号フィルタ | starrydata の `composition` には `LMB` / `RMB` / `SIB` のような電池系の略記が混じり、`parse_formula` がそれを L+M+B と読む。**全トークンが元素記号**かつ **「全大文字・数字なし・3 トークン以上」でない**ことを要求する。実測で 89 種・9,922 行を除外。単元素の `C`/`B`/`O` は通す |
| D8 | **ID 体系の変更** | MP が `mp-34202` → `mp-aaaabypm` に刷新済み。**新 ID を採用し、旧 IRI は `id_move` の転送台帳で繋ぐ**（§3） |

## 3. ID 体系の変更（D8）

### 事実

- 新 API は `material_id` に `mp-aaaabypm` 形式を返す。**旧 ID はレスポンスのどこにも
  含まれない**（`"34202"` を全フィールドで検索して 0 件）。
- 旧 ID での**問い合わせ**は通る（`material_ids=mp-34202` → `mp-aaaabypm` が返る）。
  つまり **旧→新は引けるが、新→旧は引けない**。
- `builder_meta.batch_id = "production-r2scan"` ＝ 汎関数を r2SCAN に移した再計算バッチ。
  ID 刷新はこれに伴うものと見られ、**値そのものも更新されている**。

### 同一性の確認

既存 seed の 11 材料すべてを旧 ID で引き、空間群を突き合わせた:

```
Ba4Au2Si21 mp-1228313 → mp-aaacrxav   Ama2   = Ama2   ✓
Bi2Te3     mp-34202   → mp-aaaabypm   R-3m   = R-3m   ✓
PbTe       mp-19717   → mp-aaaabdej   Fm-3m  = Fm-3m  ✓
…                                              11/11 一致
```

**ID だけが変わり、材料の同定は変わっていない。** 転送台帳で繋ぐのが正当である。

### 台帳に載せるための前提

`id-move-after-publish.md` の機構は「**同じソースから旧 IRI と新 IRI の両方を作れる**」
ことを前提にしている（`{SID}` → `{SID}-{sample_id}` のような subject template の変更）。
今回は旧 ID が別ファイル（seed CSV）にあるので、そのままでは乗らない。

**決定**: スナップショットの各レコードに `mp_id_legacy` を持たせ、同一ソース化する。
seed の 11 材料に該当するものだけが値を持ち、残りは欠損＝台帳に載らない（Morph-KGC は
参照フィールドが null の行を落とすので、これは自然に効く）。台帳 RML は
`{mp_id_legacy}` を subject、`{mp_id}` を object に置いた形になる。

## 4. データの形（取り込み側から見て）

1 レコードは **51〜52 キー**。Bi2Te3 の実測:

| 分類 | 例 |
|---|---|
| 同定 | `mp_id` `formula` `formula_anonymous` `chemsys` `nelements` `nsites` `composition_reduced_Bi` |
| 対称性（`structure` 内） | `space_group_symbol` `space_group_number` `crystal_system` `point_group` `hall` |
| 安定性 | `energy_above_hull` `formation_energy_per_atom` `is_stable` `theoretical` `decomposes_to` |
| 電子 | `band_gap` `is_gap_direct` `is_metal` `efermi` `cbm` `vbm` |
| 磁性 | `total_magnetization` `ordering` `is_magnetic` `num_magnetic_sites` |
| 力学 | `bulk_modulus_{voigt,reuss,vrh}` `shear_modulus_{…}` `universal_anisotropy` `homogeneous_poisson` |
| 誘電・光学 | `e_total` `e_ionic` `e_electronic` `n` |
| 外部 ID | `database_IDs_icsd`（ICSD 識別子の配列） |
| 由来 | `host_formula`（starrydata 側の母相）・`starrydata_samples`（その母相のサンプル数） |

**ネストの畳み方**は取り込み側の制約に合わせてある:

- dict → サブキーごとに 1 列（`bulk_modulus` → `bulk_modulus_vrh`）。`structure` だけは
  既存 RML 互換のためネストのまま残す。
- list → **JSON 文字列**。`fn:json_array`（`elements`・`database_IDs_icsd`）や
  `fn:json_pluck`（`decomposes_to` の object 配列）で展開できる形。
- null → **キーごと省く**。Morph-KGC は参照フィールドが null の行を丸ごと落とすため、
  欠損を null で埋めると疎なフィールドが行全体を消す。

## 5. 再現性

- 取得スクリプト `datasets/materials_project/json/fetch_mp_snapshot.py` をコミットし、
  出力 `mp.json` もコミットする（既存 `build_json_snapshot.py` と同じ作法＝
  **content-authoring tool**。取り込み経路はこれを実行しない）。
- API キーは `MP_API_KEY` / gitignore 済み `.env` から読む。**スナップショットにも
  ログにも出さない**。
- レジューム: `mp.cache.json` に生レスポンスを貯める。中断しても続きから走る。
  同じキャッシュから `mp.json` を決定論的に組み直せる。
- レート: 実測 **0.6 秒/リクエスト**（20 件試行）。6,057 件で約 60 分。
  429/5xx は `Retry-After` を見て指数バックオフ。

## 6. 残課題

- **UI からの API 取得は未実装**（`SUPPORTED_SOURCES` に `api` は無く「近日」表示）。
  本 ADR は CLI でスナップショットを作る経路だけを決める。UI 化はスナップショットの
  形が固まってからの方が設計を間違えない。
- **更新の追随**: MP は再計算で値も ID も変える（今回がまさにそれ）。スナップショットを
  取り直すたびに ID が変わりうるなら、転送台帳が積み重なる。取り直しの頻度と、
  台帳の推移的解決の深さは別途決める。
- `link_mp.py` は `experiments/` のスパイクのまま。本番の content-authoring がそこに
  依存している状態なので、いずれ `datasets/` 側へ移す。

## 7. 更新ログ

- 2026-08-27: 初版。3 ソースの実測比較を経て API を採用。ID 体系の刷新を発見し、
  転送台帳での移行を決定。
