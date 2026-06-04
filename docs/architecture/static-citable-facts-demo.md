# 静的「引用できる事実」デモ（サーバ／AI 不要・GitHub Pages）設計

起案: 2026-06-04 / 設計セッション（人間 kumagallium + Claude）
status: **合意済み**（2026-06-04 ユーザー確定）— Q1–Q3 確定（下記）。oxigraph-wasm 実エンジン採用 + ローカル `../starrydata_dataset` 前提で実装着手。

前提 ADR / 参照:
- [`ontology-mapping-boundary-and-provenance.md`](ontology-mapping-boundary-and-provenance.md) — engine（Read 基盤）vs app（Act 層）境界。**回答生成は consuming 層**であり asterism core には置かない。
- [`phase5-workbench-materialize-gate.md`](phase5-workbench-materialize-gate.md) — canonical = 引用適格層。draft 隔離。
- `docs/ROADMAP.md` の北極星「信頼でき・引用でき・再現できる RDF」と製品方向（決定論・型付きを主役、LLM は escape）。
- 既存デモ: [`demo-agent/DEMO.md`](../../demo-agent/DEMO.md)（FastAPI・要サーバ）。本デモはこれの **サーバ／AI 不要の双子**。

---

## 背景（なぜ書くか）

現状の体感デモ（demo-agent）は Oxigraph + FastAPI を docker で立てる必要がある。一方で製品の主軸は **「決定論・型付き・引用できる事実」**（LLM は escape）。この主軸そのものは、サーバも AI も無しに「本物の RDF を本物の SPARQL で引く」だけで示せる。**GitHub Pages にビルド済みアセットだけで載る小さな starrydata デモ**があれば、

- いつでも URL ひとつで「引用できる事実」を見せられる（ユーザー要望「デモとして示せるものは常に欲しい」）。
- セットアップ障壁ゼロ（docker / API キー / シード生成が不要）。
- 主軸の差別化点（決定論・honest なデータ品質・来歴トレース・引用 IRI 開示）を最短で伝える。

## ユーザー確定事項（2026-06-04）

| # | 論点 | 回答 |
|---|------|------|
| Q1 | ブラウザ内クエリ方式 | **おすすめに一任**（→ §「決定」で推奨を採用） |
| Q2 | ページ構成 | **軽量で良い。見た目（UI）は forest と揃える** |
| Q3 | データ品質の除外演出 | **クリーンな 40 論文サブセットのみ（除外件数 0）** |

---

## TL;DR（結論）

1. **配信**: `docs/demo/` に純静的アセットとして置く。`docs/` は既に GitHub Pages のルート（`https://kumagallium.github.io/asterism/` で namespace を配信中）なので、デモは **`https://kumagallium.github.io/asterism/demo/`** で公開される。**ビルド不要・新規ワークフロー不要**。引用 IRI（`…/asterism/starrydata/resource/…`）と **同一オリジン**。
2. **クエリ方式（推奨採用）**: 小さな **TTL を同梱**し、**oxigraph-wasm（本番 Oxigraph と同一エンジン）でブラウザ内 SPARQL を実際に実行**。typed ツールと同一の SPARQL をその場で走らせ、**結果・IRI・使った SPARQL を開示**。加えて **事前計算 `answers.json` を graceful fallback** として同梱し、wasm 読み込み失敗時も「常に示せるもの」を保証。
3. **ページ構成**: 依存最小の **vanilla HTML/CSS/JS 単一ページ**。`ui/src/index.css` の **forest デザイントークンだけを流用**し、CitationCard / ProvenanceTrace の見た目を CSS で再現（React ビルドはしない）。
4. **見せる型付き問い（決定論・AI 無し）**: ① ZT ランキング（`property_ranking`）② 組成検索（`sample_search`）③ 来歴トレース curve→sample→paper→digitization→ingestion（`provenance_of`）④ 任意で ZT–T 曲線プロット（`template_curve_fetch`）。各々に **使った SPARQL** と **引用 IRI** を併記。
5. **データ品質**: クリーン 40 論文サブセット。除外件数 0 を **honest に「0 件除外（このサブセットは全て妥当域）」と明示**し、`max_plausible` 機構の存在自体は説明として残す。
6. **ライセンス**: starrydata 本体開発者（ユーザー本人）の判断で、**最小限の事実だけ**を同梱。`docs/demo/data/` に出典・帰属・ライセンス注記を同梱。gitignore 済の `datasets/starrydata/seed/` とは別物（こちらは意図的にコミットする curated サブセット）。

---

## 1. 配信構成 — `docs/demo/`（同一オリジン・ビルド不要）

### Decision
デモを以下のレイアウトで `docs/demo/` に純静的に置く:

```
docs/demo/
├── index.html              # 単一ページ（forest トークン流用）
├── app.js                  # クエリ・描画ロジック（ESM, バンドル無し）
├── style.css               # forest トークン + CitationCard/ProvenanceTrace 再現
├── lib/                    # oxigraph wasm をベンダリング（CDN 依存を避ける）
│   ├── oxigraph.js
│   └── oxigraph_bg.wasm
└── data/
    ├── starrydata-demo.ttl # 同梱サブセット（papers+samples+curves 統合）
    ├── answers.json        # 事前計算フォールバック（数問ぶん）
    └── PROVENANCE.md       # 出典・帰属・ライセンス・生成コマンド
```

### Why
- `docs/index.html` が既に Pages ルートとして稼働中（`kumagallium.github.io/asterism/`）。**`docs/demo/` に置くだけで公開**でき、Vite ビルドも `gh-pages` ワークフローも不要 →「ビルド済み静的アセットのみ・要サーバなし」を最小構成で満たす。
- 引用 IRI（`…/asterism/starrydata/resource/curve/…`）と **同一ホスト**。将来 resource dereferencing が入れば IRI クリックがそのまま解決する（現状 Phase 2 deferred でも、少なくとも本物のホストに見える）。
- wasm を **ベンダリング**（CDN ではなくリポジトリ同梱）することで、オフライン・CDN 障害でも動く＝「常に示せる」。

### Alternatives
- **A. 別リポジトリ / gh-pages ブランチ**: IRI と別オリジンになり「引用 IRI が同一ホスト」の利点を失う。却下。
- **B. `ui/` に静的ルートを足し dist を Pages へ**: 実コンポーネント（CitationCard 等）を完全再利用できるが、アプリ全体のビルド・配信が必要で「軽量」要望に反する。Q2 回答により却下。
- **C. `docs/demo/`（採用）**: 同一オリジン・ビルド不要・軽量。

### Trade-offs
- 実 React コンポーネントを再利用せず、見た目を CSS で再現するぶん **二重メンテ**（forest トークン更新時に追従が要る）。→ トークンは CSS 変数として `ui/src/index.css` から **値をコピーして注記**し、乖離時に気付けるようにする。
- vanilla JS なので型・テストの恩恵が薄い。→ クエリロジックは小さく保ち、`answers.json` と突き合わせる軽い自己検証を起動時に走らせる。

### Re-evaluation triggers
- Pages の配信元が `docs/` から変わる（その時はパスを追従）。
- デモが増殖して単一ページで苦しくなる → その時に小型 Vite 化（Alternative C′）を再検討。

---

## 2. クエリ方式 — oxigraph-wasm 実 SPARQL + JSON フォールバック（推奨採用）

### Decision
**小さな TTL を同梱し、oxigraph-wasm でブラウザ内で実 SPARQL を実行**する。表示する SPARQL は typed ツール（`property_ranking` / `sample_search` / `provenance_of` / `template_curve_fetch`）が実際に投げるクエリと同一にする。事前計算 `answers.json`（既定の数問ぶんの結果）も同梱し、**wasm 初期化失敗時はそれにフォールバック**（UI 上「事前計算結果を表示中」と明示）。

### Why
- **製品理想に最も近い**（メモ: 設計推奨は実装コスト度外視・理想で）。「**本物の RDF を本物の SPARQL で、サーバ無しに、結果と使った SPARQL を開示**」は、製品主軸「決定論・引用できる事実・SPARQL 開示」をそのまま体現する。
- **oxigraph-wasm は本番と同一エンジン**（Oxigraph, SPARQL 1.1）。typed ツールの SPARQL がそのまま同じ意味で走る ＝ デモと本番の乖離が無い。precompute だけだと「SPARQL は説明用テキスト」に留まり、主軸の説得力が落ちる。
- フォールバック JSON により **「デモとして示せるものは常に欲しい」を保証**（wasm 非対応環境・読込失敗でも壊れない）。

### Alternatives
- **A. 事前計算 JSON のみ + JS 再実装**: 最軽量・即応・完全決定論。だが SPARQL が「実行された証跡」でなく説明テキストになり、主軸の核（引用できる事実を本物のクエリで）を弱める。→ フォールバックとして取り込む形で採用。
- **B. 実 SPARQL via wasm のみ**: 主軸に最も忠実だが、初回ロード失敗時に何も出せない。「常に示せる」に反する。
- **C. ハイブリッド（採用）**: 実 SPARQL を主、JSON を保険。authenticity と堅牢性を両取り。実装は最大だがメモの方針（理想優先）に合致。
- エンジン選択: oxigraph-wasm を採用（本番一致）。代替 rdflib.js / Comunica は SPARQL 実装・サイズ・本番一致の面で劣後。

### Trade-offs
- wasm 初回ロード ~1–2MB（ベンダリングで自ホスト）。→ 初回のみ。`answers.json` で体感即応。
- TTL に曲線の x/y JSON 配列を全部入れると肥大。→ §3 で **featured 曲線のみ点列を保持／その他は間引き or 省略**。
- ロジックを「SPARQL（wasm 経路）」と「answers.json（フォールバック経路）」の二経路持つ。→ `answers.json` は **ビルド時に同じ SPARQL を Oxigraph に投げて生成**し、二経路が同値であることを担保（手書きしない）。

### Re-evaluation triggers
- TTL/wasm 合計が重く感じる（初回ロード体験が悪い）→ JSON 主・wasm を「自分で実行」ボタンの段階強化に降格。
- 別 dataset のデモを足す → クエリの dataset 非依存化（#18 汎用 Ask 層の知見）を流用。

---

## 3. 同梱データ — 形・規模・生成

### Decision
- `scripts/make_demo_subset.py --n-papers 40`（**outlier 無し**, Q3）で生成した `papers.ttl` / `samples.ttl` / `curves.ttl` を **1 ファイル `starrydata-demo.ttl` に統合**（単一グラフでロード）。
- 曲線の `sd:xValuesJSON` / `sd:yValuesJSON`（大きい点列）は、**プロット表示に使う featured 曲線（数本）だけ保持**し、他は間引き（例: 先頭・末尾＋等間隔 ~30 点）または省略。ランキング／来歴／検索は `yMax`/`yMin`/`propertyY`/`compositionString` 等のスカラだけで成立するので影響なし。
- `answers.json` は **同じ TTL を Oxigraph に投入し typed ツール SPARQL を実行した結果**をビルド時にダンプ（手書き禁止）。生成スクリプトを `scripts/` に追加（例 `scripts/build_demo_assets.py`）。
- 目標バンドル: TTL + JSON 合計 **概ね 500KB 以下**（wasm 別）。

### Why
- 40 論文・ZT 曲線ありに限定済みで「最大 ZT」問いが常に成立（`make_demo_subset.py` の選定基準そのもの）。
- スカラ中心なので軽量化と機能が両立。featured 曲線だけ点列を残せば視覚的な曲線プロットも出せる。

### Re-evaluation triggers
- 別プロパティ（Seebeck 等）も主役にしたくなったら featured 曲線・サブセット基準を拡張。

---

## 4. 見せる型付き問い（決定論・AI 無し）

既存 typed ツール（`mcp/src/asterism_mcp/tools.py`）のロジックをブラウザ内 SPARQL として忠実に再現する。各カードに **回答 / 引用 IRI / 使った SPARQL / データ品質ノート** を併記。

1. **ZT ランキング**（`property_ranking`, `property_y="ZT"`, `max_plausible=3.5`）
   `?curve a sd:Curve ; sd:propertyY "ZT" ; sd:yMax ?ymax ; sd:ofSample ?s . ?s sd:fromPaper ?p …` を `ORDER BY DESC(?ymax) LIMIT 10`。
   品質ノート: **「ZT>3.5 を 0 件除外（このサブセットは全て妥当域）」** と honest 表示。
2. **組成検索**（`sample_search`, 例 `composition="Bi2Te3"`）
   `FILTER(CONTAINS(LCASE(?compositionString), "bi2te3"))`、任意で `propertyY` 一致曲線を持つサンプルに限定。
3. **来歴トレース**（`provenance_of`, ランキング上位の curve IRI）
   `sd:ofSample`→`sd:fromPaper` を辿り、各 `prov:wasGeneratedBy`（DigitizationActivity = WebPlotDigitizer / IngestionActivity = asterism pipeline）を chain 表示。**curve→sample→paper→digitization→ingestion** を ProvenanceTrace 風に縦に描く。
4. **（任意）曲線プロット**（`template_curve_fetch`, featured 曲線）
   `sd:xValuesJSON`/`sd:yValuesJSON` を取得し ZT–T を軽量に描画（依存追加せず canvas / inline SVG）。

UI 上の語り口は demo-agent と揃える: 「記録上の最大 ZT は … の約 N。すべて引用 IRI で辿れる。物理的にあり得ない値は除外（今回 0 件）」。

---

## 5. 見た目 — forest トークン流用（React ビルド無し）

### Decision
`ui/src/index.css` の CSS 変数（`--bg`/`--surface`/`--primary #3f6f49`/`--accent #c08b3e`/PROV 配色 `--entity #3f7a4e`・`--activity #3d6f96` 等）と、タイポ（Hanken Grotesk / Zen Kaku Gothic New / IBM Plex Mono）、角丸・影の規約を `docs/demo/style.css` に **値をコピー**して流用。CitationCard（左 5px カラーバー＋kind バッジ＋mono フィールド）と ProvenanceTrace（縦ドット＋コネクタ＋kind 配色）の見た目を CSS で再現。フォントは CDN（Google Fonts）または system fallback。

### Why
Q2「見た目は揃える／React ビルドはしない」。トークンだけ流用すれば forest の語彙で一貫しつつ軽量。

### Trade-offs / Re-evaluation
トークン二重管理（§1 Trade-offs と同じ）。乖離検知のため値の出所（`ui/src/index.css`）をコメント明記。将来コンポーネント共有が要るなら Alternative B/C′ を再検討。

---

## 6. ライセンス / 帰属

### Decision
- 同梱は **最小限の事実**（40 論文ぶんの papers/samples/curves スカラ＋ featured 曲線点列）。
- `docs/demo/data/PROVENANCE.md` に **出典（Starrydata）・帰属・ライセンス・生成コマンド（`make_demo_subset.py --n-papers 40` + `build_demo_assets.py`）** を明記。各引用は IRI で原典 paper（DOI）に辿れる。
- `datasets/starrydata/seed/`（gitignore 済）とは別の、**意図的にコミットする curated 公開サブセット**である旨を注記。

### Why
ユーザーは Starrydata 本体開発者であり再配布権限を持つ。最小事実 + 帰属 + 出典明記で「引用できる」を体現しつつ配布範囲を最小化。

### Re-evaluation triggers
Starrydata の配布条件変更・帰属要件変更時に注記を更新。

---

## 7. 横断結合カード — Starrydata × Materials Project（追加・ユーザー確定 2026-06-04）

### 背景 / Decision
ユーザー指摘:「starrydata 単体だと『DB でよくない？なぜオントロジーか』になる」。単一データセットでは「来歴で引用できる」までしか示せず、オントロジーの本命**＝異種データの横断結合（ETL 無し）**が見えない。そこで **既存 PoC `experiments/mp-linking-poc/` を再利用**し、starrydata サンプルの**母相結晶構造**を Materials Project から解決したリンクを**別の名前付きグラフ**として同梱し、**2 ソースを 1 SPARQL で結合**するカードを追加する。

- グラフ: `…/starrydata/graph/mp-links`（MP リンク ABox ＋ `mp_link_tbox.ttl` を同梱＝自己記述的）。starrydata は既定グラフ、MP は名前付きグラフ → クエリの `GRAPH <…/mp-links>` が「2 ソース」を可視化。
- 結合キー: **同じ `sdr:sample/{SID}-{sample_id}` IRI**（PoC が既存 ABox と同一 IRI に追加出力する設計）。ETL 不要。
- 表示: 母相結晶構造ごとに 1 行（最大 ZT・試料数・空間群・結晶系・mp-id→MP リンク）。行展開で **「結合の仕組み（オントロジーの橋）」**＝述語パス `sample —sd:hasHostStructure→ CrystalStructure —sd:idealizedFrom→ MP`、TBox 意味（`sd:idealizedFrom ⊑ prov:wasDerivedFrom`・**owl:sameAs ではない**＝ドープ実サンプル≠純計算相、ドープ=`sd:PointDefect`）、リンクの来歴（`sd:StructureMatchActivity`: 方法・一致度・日時）。

### Why（なぜ MP・なぜ「オントロジー的にどう繋いだか」を見せる）
MP は**権威ある独立 DB**なので「DB でよくない？」への直接の反証になる（QUDT＝標準語彙より「2 つの DB」感が強い）。PoC が難所（母相正規化・no-sameAs・PROV 付きリンク）を解決済で再利用性が高い。「オントロジー的にどう繋いだか」を前面に出すことで、外部キー結合との差＝**関係自体が型付きの意味と来歴を持つ**ことを示せる。

### サブセット / データ生成
- カバレッジ確保のため `make_demo_subset.py --include-sids 3,9,20,120,869`（Bi2Te3/ZnO/PbTe/SnSe/PbSe＝MP で確実に解ける象徴的熱電材料・いずれも ZT 曲線あり・除外0 維持）。
- `link_mp.py --mode live`（要 `MP_API_KEY`・ビルド時一度）で**実 mp-id/空間群**を解決。demo モードは `mp-DEMO-*` プレースホルダ＝引用不可なので**配信は live で焼き込む**（de-risk のため統合実装は demo モードで先行検証→live 差し替え）。

### ライセンス / 帰属
MP データは **CC-BY 4.0**。同梱は最小事実（mp-id・空間群・結晶系・prototype・還元式）のみ＋帰属＋引用（Jain et al., *APL Materials* 1, 011002 (2013)）。mp-id は `next-gen.materialsproject.org/materials/<id>` で解決。`PROVENANCE.md` に明記。

### Trade-offs / Re-evaluation
- live 実行に MP キーが要る（make_demo_subset と同じ「ビルド時一度」）。
- 母相一致は confidence 付き（high/medium/low）。低信頼の行も**一致度を開示**して honest に。
- 母相は **最安定相（e_above_hull 最小の多形）** に限定（近似）。`idealizedFrom`＋一致度で「理想化された参照」であることを明示。
- mp_link_tbox は canonical 未昇格（PoC の提案形）。本デモはグラフに同梱するだけで canonical に依存しない。将来 #19/linker で昇格時に追従。
- 本デモの結合は **インスタンス層（共有 IRI）のみ**で、CMSO/PODO/EMMO への schema 層 owl 整合は未実施。その整合方針＝ [`external-standard-alignment.md`](external-standard-alignment.md)（2層モデル・直接再利用優先・基盤としての役割境界・右サイズ形式化）。

---

## 実装タスク（合意後）

1. `scripts/make_demo_subset.py --n-papers 40` で seed 生成（ローカル、要 `../starrydata_dataset`）。
2. `scripts/build_demo_assets.py` 追加: 3 TTL 統合 → 点列間引き → `docs/demo/data/starrydata-demo.ttl`、Oxigraph に投入し typed SPARQL を実行して `answers.json` 生成、`PROVENANCE.md` 出力。
3. oxigraph-wasm を `docs/demo/lib/` にベンダリング。
4. `docs/demo/{index.html,app.js,style.css}` 実装（forest トークン流用・4 問・SPARQL 開示・フォールバック）。
5. `docs/index.html` からデモへのリンク追加。
6. ローカル検証（`python -m http.server docs/` 等で Pages 相当を確認、wasm 経路と JSON フォールバック両方）。
7. `docs/ROADMAP.md` に状態反映（CLAUDE.md 作法）。

## 未解決 / 確認したい点
- なし（Q1–Q3 確定）。本書合意をもって実装着手。
