# デモ同梱データの出典・帰属・ライセンス

このディレクトリの `starrydata-demo.ttl` / `answers.json` は、
[Starrydata](https://www.starrydata2.org/) の熱電材料データから生成した
**最小限の公開サブセット**です（決定論・サーバ不要・AI 不要の静的デモ用）。

- 出典: Starrydata（熱電材料の論文・サンプル・曲線データ）
- 帰属: 各事実は IRI 経由で原典論文（DOI）まで辿れます（来歴トレース参照）。
- このサブセットは `datasets/starrydata/seed/`（gitignore 済の作業用 seed）とは別に、
  **デモ配信のため意図的にコミットする** curated データです。

## 規模

- papers: 45
- samples: 318
- curves: 1228
- 点列（x/y JSON 配列）を保持した featured 曲線: 10
  （プロット表示用。他 1218 曲線は点列を除去しスカラのみ保持）
- 既定の組成検索クエリ: `Ba8Ga16`

## 横断結合データ (Materials Project)

`mp-links.ttl` は、各 starrydata サンプルの**母相結晶構造**を
[Materials Project](https://next-gen.materialsproject.org/) から解決したリンク
（同じ `sd:sample/...` IRI に追加した別グラフ `https://kumagallium.github.io/asterism/starrydata/graph/mp-links`）です。生成は
`experiments/mp-linking-poc/link_mp.py`（母相正規化 → MP 照合 → PROV 付きリンク）。

- 取得元: Materials Project（mode = live (Materials Project REST)・解決 11 行）
- ライセンス/帰属: Materials Project のデータは **CC-BY 4.0**。本デモは最小の事実
  （mp-id・空間群・結晶系・prototype・還元式）のみを帰属付きで同梱します。
  引用: A. Jain et al., "Commentary: The Materials Project", *APL Materials* 1, 011002 (2013).
  各 mp-id は `https://next-gen.materialsproject.org/materials/<mp-id>` で解決できます。
- 設計: 実サンプル(ドープ)と MP の計算相は別物なので `owl:sameAs` を使わず、
  `sd:idealizedFrom`（`prov:wasDerivedFrom` のサブプロパティ）で「母相参照」に留め、
  ドープは `sd:PointDefect`、リンク自体は `sd:StructureMatchActivity`（方法・一致度）で由来づけ。

## 再生成

```bash
# 1) seed サブセット生成（ローカルの ../starrydata_dataset が必要）
python scripts/make_demo_subset.py --src ../starrydata_dataset --n-papers 40 \
  --include-sids 3,9,20,120,869
# 2) MP 構造リンク（横断結合用・要 MP_API_KEY で実 mp-id）
MP_API_KEY=... python experiments/mp-linking-poc/link_mp.py \
  --csv datasets/starrydata/seed/csv/samples.csv \
  --out experiments/mp-linking-poc/out/sample_mp_links.live.ttl --limit 100000 --mode live
# 3) 静的デモアセット生成（このディレクトリを再生成）
python scripts/build_demo_assets.py
```

`answers.json` は本番の typed ツール（`asterism_mcp.tools` の
`property_ranking` / `sample_search` / `provenance_of` / `template_curve_fetch`）を
同梱 Turtle に対して実行した結果です。ブラウザは同じ SPARQL を oxigraph-wasm で
再実行します（このファイルはフォールバック）。
