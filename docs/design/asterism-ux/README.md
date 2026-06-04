# Handoff: Asterism — UX 再設計（情報設計・用語平易化・カタログ再構成）

## Overview
Asterism は、研究者が手元の散らばったデータ（CSV / API / JSON / DB）を、出どころ（来歴）まで遡れる「つながったデータ（RDF 知識グラフ）」に変えるツールです。本ハンドオフは、既存 Web UI（`csv2rdf-mcp/ui`、React + TypeScript + Vite）が「使いにくい」課題に対する **UX 全面再設計** の実装用リファレンスです。

再設計の3本柱:
1. **情報設計（IA）の作り変え** — パイプラインの内部構造をそのまま並べたナビ → ユーザーの動詞（入れる → 問う/確かめる → 見渡す）に沿った平易なナビへ。
2. **専門用語の平易化** — RDF / TBox / MIE / materialize 等を日本語の平易語に。専門語は「消さず」点線ヒント＋英語併記で必要な人にだけ見せる。
3. **カタログ（旧 Gallery）の再構成** — 抽象的な Ontologies / Mappings の並列 → **データセットを主役**にし、語彙とマッピングは各データセット内の2タブへ「再配置」。共有部分のみ「共有の語彙」へ昇格。

加えて、登録元を **CSV に限定せず API / JSON / DB に拡張**（入口だけ違い、以降の「接続 → AI設計 → 確認 → 保存」は共通）。

---

## About the Design Files
`prototype/` の各ファイルは、**HTML/React(JSX, Babel standalone) で作った“デザインリファレンス”** です。本番にそのまま貼るコードではありません。狙いは、**この見た目・挙動を既存コードベース（`csv2rdf-mcp/ui` の React + TS + Vite + 既存スタイル）の流儀で再実装する**こと。

- プロトタイプは **3つのビジュアル方向（forest / clean / constellation）** を1つの「デザインキャンバス」に並べて比較できる形になっていますが、**採用は `forest`（森）方向**です。実装では forest のトークンのみ使ってください（下記 Design Tokens 参照）。clean / constellation は不採用、無視して構いません。
- プロトタイプ内の `design-canvas.jsx` と `context.jsx` は **比較・説明のための足場** で、製品機能ではありません。実装不要。
- すべてインラインスタイル＋テーマオブジェクト駆動で書かれています。本番では既存の CSS 変数（`csv2rdf-mcp/ui/src/index.css` の forest green トークン）や既存コンポーネントに置き換えてください。

## Fidelity
**High-fidelity（hifi）。** 色・タイポ・余白・状態・コピーまで最終意図で作り込んであります。レイアウトとビジュアルはこの忠実度で再現してください。ただしデータはすべてモック（starrydata 熱電 / NIMS Supercon 等）で、文言は日本語確定・専門語は英語併記。

---

## 既存コードベースとの対応（どのファイルを差し替えるか）
| 再設計の画面 | 既存ファイル（参考/置換先） | 変更の方向 |
|---|---|---|
| アプリの枠（サイドバー/ヘッダ/ナビ） | `ui/src/App.tsx` | ナビ項目とラベルを新IA（下記）に差し替え。SPARQL は「開発者向け」へ降格。 |
| ホーム（新設） | （なし・新規） | 起点画面を新規作成。 |
| データを追加 | `ui/src/WorkbenchView.tsx` + `MaterializePanel.tsx` | 3ステップ化、データソース切替、AI提案を「読める設計」表示に。 |
| 質問する | `ui/src/AskView.tsx` + `CitationCard.tsx` + `ProvenanceTrace.tsx` | ほぼ踏襲・整理。来歴トレースを右パネル常設に。 |
| カタログ | `ui/src/GalleryView.tsx` + `galleryApi.ts` | データセット主役へ全面再構成。 |
| 共有の語彙（新設） | `GalleryView.tsx` の ontology 部分を昇格 | 「どのデータがどう使うか」ビューを新規。 |
| アクティビティ | `ui/src/JobsView.tsx` + `jobsApi.ts` | 「取り込み履歴」を平易名に。 |
| SPARQL | `ui/src/SparqlView.tsx` | 残すが「開発者向け」へ。 |

---

## 新・情報設計（ナビ構成）
サイドバー幅 248px。ブランドマーク（3つの星を線でつないだ asterism）＋ "asterism" ロゴ＋サブ「研究データ → つながったデータ」。

ナビ（各項目：日本語ラベル＋小さい英語タグ＋アイコン）:
- **ホーム** `Home`（グループなし）
- グループ **つくる** → **データを追加** `Add data`
- グループ **つかう** → **質問する** `Ask` / **カタログ** `Catalog`
- グループ **管理** → **アクティビティ** `Activity`
- 最下部に分離して **SPARQL**（ラベル右に「開発者向け」）＋ グラフ稼働インジケータ（緑ドット＋ `1.2M` 件）

ヘッダ（各画面）: eyebrow（accent 色・大文字英字風の小ラベル）＋ 大見出し（display フォント）＋ 右に補足文。

### 用語の翻訳表（画面表記は左→右へ）
| 旧（専門語・消さない） | 新（画面の主表記） | 英語併記 |
|---|---|---|
| RDF / 知識グラフ | つながったデータ | knowledge graph |
| オントロジー / TBox | データの設計図（語彙） | vocabulary |
| MIE / ingester / RML | 取り込みルール | mapping |
| 8つの罠 (traps) | 品質チェック（8項目） | validation |
| materialize | 保存（確定） | save |
| canonical / promote | 正式データに反映 ↔ 下書き | publish |
| provenance / 来歴 | 出どころ（来歴） | provenance |
| citation | 根拠（引用） | citation |

専門語は `Term` パターンで提示：平易語に **点線アンダーライン**（1.5px dotted, faint 色）＋ 右上に小さな `?`（mono, 9px, faint）、`title` 属性で英語を表示。

---

## Design Tokens（forest 方向＝採用）
プロトタイプ `kit.jsx` の `FOREST` オブジェクトが正。既存 `ui/src/index.css` の forest green トークンに合わせて CSS 変数化してください。

### Colors
```
bg            #f3f6f0   画面背景
surface       #ffffff   カード/パネル
surfaceAlt    #f7faf4   薄い面・入力・コードブロック
fg            #1b2a1d   本文
muted         #5f7263   サブテキスト
faint         #90a392   キャプション/プレースホルダ
border        #dde6dc   標準ボーダー
borderStrong  #cad7c8   入力枠など強めボーダー
primary       #3f6f49   主要アクション/選択
primaryFg     #ffffff
primarySoft   #e7f0e6   選択背景/ソフトボタン
accent        #c08b3e   warm amber（eyebrow・注意・ハイライト）
accentSoft    #f6ecd8
sky           #1c2a1f   暗い面（控えめに使用）
skyFg         #eef4ec
entity        #3f7a4e   PROV: データ系（緑） — 試料/曲線/論文
entitySoft    #e6f0e6
activity      #3d6f96   PROV: 処理系（青） — 取り込み/デジタル化
activitySoft  #e4eef5
```
意味色のルール: **データ＝entity（緑）/ 処理・アクティビティ＝activity（青）**。PROV-DM に準拠。全画面で一貫させること。

### Typography（ゴシック必須）
```
fontUI / fontDisplay : "Hanken Grotesk", "Zen Kaku Gothic New", "Noto Sans JP", system-ui, sans-serif
fontMono             : "IBM Plex Mono", monospace
display              : { weight: 700, letterSpacing: -0.02em }
```
- 和文は **ゴシック体（Zen Kaku Gothic New / Noto Sans JP）**。明朝は使わない（constellation 案でのみ使っていたが不採用）。
- 主な実寸: ヘッダ大見出し 23px/700、画面内 h2 19px/700、h3 13–14.5px/700、本文 12.5–14px、回答本文 16.5px/line-height 1.85、キャプション 10.5–11.5px、mono ラベル 9.5–11px。
- 数値・ID・コード・件数は **mono**（fontMono）で表示。

### Shape / Elevation
```
radius     13   （カード）
radiusSm   8    （ボタン/入力/小カード）
radiusLg   18   （大アイコンチップ）
chips/pills: borderRadius 20–999（角丸ピル）
shadow      0 1px 2px rgba(20,35,22,.05), 0 8px 24px rgba(20,35,22,.06)
shadowSoft  0 1px 2px rgba(20,35,22,.04)
```
余白の目安: 画面外周 main padding 24px 30px。カード内 16–20px。要素間 gap 8–16px。

### 共通アトム
- **Btn**: kinds = `primary`(緑塗り) / `accent`(amber塗り) / `ghost`(白地・borderStrong枠) / `soft`(primarySoft地・primary字)。size = `md`(10/16px) / `sm`(7/12px)。アイコン＋ラベル、gap 7、角丸 radiusSm、whiteSpace nowrap。
- **Card**: surface 地、border、radius、shadowSoft、padding 既定20。
- **Term**: 上記用語ヒント。
- **アイコン**: 20–24 viewBox のストロークアイコン（icon ライブラリ非依存、currentColor 継承、strokeWidth 1.7）。home/add/ask/catalog/activity/code/spark/check/arrow/chevron/upload/file/search/trace/doc/layers/link/dot 等。既存コードベースのアイコンセットがあればそれに置換可。
- **ブランドマーク**: 3つの円（星）を3本の線でつないだ図（asterism）。星＝primary、線＝borderStrong。

---

## Screens / Views

### 1. ホーム（Home, 新設）
**Purpose**: 専門語の前に「今あるもの＋次の一手」を平易に提示する起点。
**Layout**: 縦 flex, gap 20。
- **ステータスバンド**（surface カード, padding 18/22）: 見出し「今ある『つながったデータ』」＋ 4つの統計（mono 26px）: `1.2M` 事実の数/triples、`3` データセット、`5` 語彙のクラス（primary色）、`100%` 出どころを追える（entity色）。
- **2つの大アクション**（横 flex, gap 16, 各 minHeight 132）:
  - 「データを追加」= primary（緑塗り、白字、右下に矢印、左上に40pxアイコンチップ）。サブ「CSV・API などをつなぐと、AI が設計を下書きします」。
  - 「質問する」= surface（白地）。サブ「取り込んだデータに、根拠つきで答えます」。
- **最近のデータセット**（Card, flex:1）: 見出し＋右に「カタログで全部見る →」。行: 34pxアイコン＋名前/種別(mono)＋件数群(mono太字＋ラベル)＋ステータスピル＋chevron。
  - 行データ: `Starrydata 熱電データ / starrydata · CSV 3種 / 1.2M事実 45k試料 12k論文 / 公開済み(entity)`、`NIMS Supercon / 超伝導体 · API 連携 / 8.2k 320 88 / 下書き(accent)`、`実験ノート 2026Q1 / measurement · JSON / —/54/— / 設計中(muted)`。

### 2. データを追加（旧 Workbench）
**Purpose**: 任意ソースから AI と一緒に知識グラフを作る。専門語を隠した3ステップ。
**Layout**: 縦 flex, gap 16。
- **データソースバー**（surfaceAlt, radius）: ラベル「データソース」＋ ピル型トグル群 `表計算 / CSV`(file,選択) `JSON`(code) `API`(link) `DB`(layers)。補足「あらゆる構造化ソースに対応（順次拡大）」。右に「構造を見る (AI不要)」。下段: ファイルチップ `papers.csv` `samples.csv` `curves.csv` ＋「3 ファイル · 全ステップで共有」＋「ソースを変更」ボタン。
- **ステッパー**: ① AI が設計(design, 完了✓・entity) → ② 確認・修正(review, アクティブ・primary) → ③ 保存(save)。丸番号26px(mono)＋日本語ラベル＋英語タグ。右に「かかった時間 4分12秒 · 設計の下書きができました」。
- **2カラム（1.55fr / 1fr）**:
  - 左 **AI が提案した設計**（Card, ヘッダに spark アイコン＋タブ `設計図`/`取り込みルール`）: 説明文＋クラス図（surfaceAlt 枠の `ClassDiagram`）＋ `details` で「詳しい設計図（TBox/ontology/RML/MIE のコード）を見る」折りたたみ＋「項目の対応（取り込みルールの一部）」テーブル（ソースの項目 → つなぐ先 → メモ）。
  - 右 縦 flex:
    - **品質チェック**（Card）: 「8項目」「7 / 8 合格」。2列グリッドで各項目（✓ entity / ⚠ accent / ✗ 赤）。項目: IDの重複なし, 文字コード安全, 空のノードなし, 探索メタ 5項目+, 図ラベル安全, 実在する行から, 設計理由つき(warn), 幻覚チェック。
    - **ここを直したい**（Card, flex:1）: 説明＋プレースホルダ入力（例文）＋ ボタン「作り直す(ghost,spark)」＋「確認した — 保存へ(primary,check)」。
**State**: タブ（設計図/取り込みルール）、ソース種別、品質チェック結果配列、refine 入力テキスト、ステップ状態。

### 3. データソースを広げる（API 接続フロー, 追加詳細）
**Purpose**: CSV 以外（API）を具体化。「入口だけ違い、以降は共通」を示す。
**Layout**: ソースバー（API 選択状態）＋ 2カラム（1fr / 1.1fr）。
- 左 **API に接続**（Card）: フィールド `エンドポイント URL`(mono, https://api.starrydata.org/v2/curves)、行2列で `認証方式`(Bearer トークン・chevron)＋`APIキー`(マスク表示, mono)。成功バンド（entitySoft, ✓「接続できました — 342 件のサンプルを検出」＋ `120ms`）。`取得タイミング` ピル: 一度だけ / 毎日(選択) / 毎週 / 手動。フッタ「キーは暗号化して保存されます」＋「AI 設計へ進む(primary)」。
- 右 **取得サンプル（先頭1件）**（Card, "AI不要・そのまま表示"）: JSON プレビュー（mono, pre）＋ 検出フィールドのチップ群（sample_id, composition, temperature_K, ZT, seebeck_uV_K, doi）＋ 「次のステップで AI が、この構造を既存の共有語彙に自動でつなぎます」（spark, dashed枠）。
**State**: endpoint, authType, apiKey, 接続テスト結果（成功/失敗/件数/レイテンシ）, schedule, 検出フィールド配列。

### 4. 質問する（Ask）＋ 来歴トレース
**Purpose**: 取り込み済みデータに根拠つきで回答。来歴を常設右パネルで提示（デモの見せ場）。
**Layout**: グリッド 1.5fr / 1fr。
- **左**: 縦 flex。
  - **質問バー**: primary 1.5px枠の入力（ask アイコン＋例文＋点滅カーソル風）＋「質問する(primary)」。下に例チップ: `ZT が最も高い熱電材料は？` `SnSe を含む試料は？` `新しく設計したデータには何がある？`。
  - **回答カード**（Card, flex:1）: バッジ「✓ 根拠つきの回答(entity)」＋「取り込み済みのデータに基づく」。回答本文（display, 16.5px/1.85）: 「記録上の最大は **SnSe の約 2.6**［1］。ただし `ZT > 3.5` の極端な値が数件ありましたが、軸ラベルの誤りの可能性として除外しています。」 ＋ 「根拠（引用）クリックで出どころを表示」＋ 引用カード（CitationCard）×2 ＋ データ品質メモ（accentSoft バンド）。
  - **引用カード**: 左5px色帯（kind色）＋ KINDバッジ(mono, 大文字)＋ ラベル＋右に「出どころ(trace)」＋ フィールド群（key=mono faint, value=fg太字）。種別→色は `KIND_TO_CLASS`（既存 galleryApi）に従い、データ系=entity緑/処理系=activity青。
- **右 来歴トレース**（常設パネル, surface, shadow）: ヘッダ eyebrow「出どころ · provenance」＋「来歴をたどる」＋説明。縦チェーン（ノード＝色丸＋4pxソフトリング、コネクタ線）。各ノード: 種別バッジ(色塗り)＋ラベル＋詳細＋ `resource/<en>/…`(mono faint)。
  - チェーン: 測定曲線 `Fig.3 ZT vs T`(yMax=2.6, entity) → 試料 `SnSe`(composition=SnSe, entity) → 論文 `Snyder et al. (2014)`(DOI…, entity) → デジタル化 `WebPlotDigitizer`(activity) → 取り込み `取り込み記録`(2026-05-31, activity)。
  - フッタ: 凡例（■データ=entity / ■処理=activity）＋「語彙を見る →」。
**State**: 質問テキスト、回答（本文/引用配列/品質メモ）、選択中引用、来歴チェーン配列、ローディング状態。
> 注: constellation 案では来歴を「夜空に星をつなぐ」表現にしていたが**不採用**。forest では明るいレール＋PROV色ノードで実装。

### 5. カタログ（旧 Gallery, 全面再構成）
**Purpose**: 所有する**データセット**を主役に「答えられる問い」で見渡す。語彙/マッピングは各データセット内に再配置。
**Layout**: オリエンテーション1行 ＋ グリッド（300px / 1fr）＋ 下部「共有の語彙」バンド。
- **左 データセット一覧**: 見出し「データセット 3」＋「追加(soft)」。カード（選択時 primary枠＋左3px帯＋shadow）: 名前＋ステータスピル＋種別(mono)＋件数群。
  - `Starrydata 熱電データ / starrydata · CSV 3種 / 公開済み(pub) / 1.2M事実 45k試料 12k論文`（選択）、`NIMS Supercon / API 連携 · superconductors / 下書き(draft) / 8.2k 320`、`実験ノート 2026Q1 / JSON · measurement / 設計中(design) / —未取込`。
- **右 データセット詳細**（Card）: ヘッダに名前＋ステータス＋**タブ切替** `設計図`(ontology) / `取り込みルール`(mapping)。直下「このデータが答えられる問い」＝purpose タグ群（熱電性能の探索 / 組成検索 / 単位の正規化 (QUDT) / 来歴トレース / 論文メタ参照）。
  - **タブ＝設計図**: 「設計図（中身の構造）5 クラス · すべて出どころ付き」＋ `TTL` リンク。`ClassDiagram`（surfaceAlt 枠）。下に「他から借りている語彙（再発明しない）」＝reuse チップ `qudt:`物性名・単位 / `schema:`論文メタ / `prov:`来歴 / `dcterms:`ID。
  - **タブ＝取り込みルール**: 「取り込みルール（項目の対応）」＋ `ingester` リンク。テーブル（ソース項目 → つなぐ先(entity) → 変換(activityピル)）: `SID + sample_id → 試料のID / 複合キー`、`composition → 組成 / そのまま`、`Seebeck_coef → ゼーベック係数 / QUDT単位`、`Seebeck_unit → 単位 / 表記ゆれ正規化`、`temperature → 測定温度 / °C→K`、`doi → 論文 / schema:同定`。下に成果物チップ: `MIE mapping.json`（機械可読の対応表）、`CODE ingester.py`（実際の取り込み処理）。
- **共有の語彙バンド**（surface, 左 activity 3px帯）: linkアイコン＋「共有の語彙 / shared vocabulary」＋ 警告ピル「変更は全体に影響 · 要注意(accent)」＋説明。右に「2 データセットが利用」＋「開く →」。クリックで画面6へ。
**State**: 選択データセット、詳細タブ（design/rules）。
**重要な概念整理（ユーザーからの質問への答え）**: 「データセットに絞った」のではなく「**語彙(ontology)とマッピング(mapping)を各データセットの中に再配置**」した。両概念は消えていない。共有部分のみ画面6へ昇格。

### 6. 共有の語彙（新設）
**Purpose**: 複数データセットが共通利用する語彙を独立提示し、「どのデータが・どのクラスを・どう使うか」を可視化。
**Layout**: 回答バナー ＋ グリッド（1fr / 1.2fr）。
- **回答バナー**（surfaceAlt, 左 activity 帯）: 「『設計図（語彙）』は無くなりません — **共有**されるだけ」＋説明。
- **左 共有クラス**（Card）: 「5 · materials-core v1.2」。各クラス行（上 3px entity 帯）: 日本語名＋英語(mono)＋説明＋「2 利用」。クラス: 試料 Sample / 測定曲線 Curve / 論文 Paper / 記述子 Descriptor / 取り込み記録 IngestionActivity。
- **右 どのデータが、どう使っているか**（Card）: ヘッダ右に**束縛戦略の凡例**＝4種チップ。データセットごとにカード＋束縛チップ群。
  - **束縛戦略（4種・色）**: `そのまま使う`(reuse, entity) / `広げる`(extend, activity) / `つなぐ`(map-into, accent) / `新規`(new, muted)。
  - `Starrydata 熱電データ`(CSV·1.2M): 試料=reuse, 測定曲線=reuse, 論文=reuse, 記述子=map。
  - `NIMS Supercon`(API·8.2k): 試料=reuse, 記述子=extend, 臨界温度Tc=new。
  - 末尾に注意ボックス（accentSoft）「なぜ『要注意』？ 共有クラスを書き換えると、それを使う 2 データセットすべての検索・回答に波及します。変更は影響範囲のプレビューを見てから確定します。」
**State**: クラス一覧、利用データセット＋各束縛戦略、影響範囲。

### 7. アクティビティ（旧 取り込み履歴）
**Purpose**: いつ・何が取り込まれたか。既存 `JobsView.tsx` を平易名で踏襲。ステータス→意味色（成功=entity, 失敗=赤, 実行中=activity）。

### 8. SPARQL（開発者向け・据え置き）
既存 `SparqlView.tsx` を維持。サイドバー最下部から「開発者向け」として遷移。

---

## States（ローディング / 空 / エラー）
プロトタイプ `ScreenStates`（`screen-extras.jsx`）に3状態の見本あり。全リスト系画面で実装すること。
- **読み込み中**: スケルトン（高さ11pxの角丸バー＋シマーグラデーション `linear-gradient(90deg, surfaceAlt 25%, border 37%, surfaceAlt 63%)`）をカード形に数本。下にスピナー（13px, border 2px, top=primary）＋「根拠を集めています…」。
- **空（はじめて）**: 54pxアイコンチップ（primarySoft）＋「最初のデータを追加しましょう」＋説明＋「データを追加(primary)」＋「サンプルデータで試す →」。
- **エラー**: 54px 赤チップ（#f6e3df / #b4453a, `!`）＋見出し「接続がタイムアウトしました」＋説明＋エラーコード（mono, `ETIMEDOUT · req_8f21c`）＋「設定を見直す(ghost)」「再試行(primary, activity)」。

---

## Interactions & Behavior
- **ナビ**: 選択項目は primarySoft 背景＋primary 字＋太字、アイコンも primary。ホバーで微妙な背景。
- **タブ切替**（データを追加／カタログ詳細）: 選択タブ＝primarySoft ピル。クリックで本文差し替え（カタログは React state で実装済み挙動を再現）。
- **データソース切替**: ピルトグル。選択＝primary 塗り。切替で入力エリアが該当ソース用フォーム（CSV=アップロード / API=接続フォーム）に変化。
- **引用カード**: クリックで右の来歴トレースに該当チェーンを表示（ハイライト連動）。
- **AI 作り直し（refine）**: テキスト入力 →「作り直す」で AI が設計を再生成（既存 demoApi のモック境界に注意：回答生成 LLM は別ランタイム）。
- **共有語彙の編集**: 「影響範囲のプレビュー」を挟んでから確定（破壊的変更の安全策）。
- **Term ヒント**: ホバーで `title`（英語）表示。`cursor: help`。
- **空/読込/エラー**: 上記 States を必ず用意。

## State Management
- グローバル: 現在画面（home/add/ask/catalog/vocab/activity/sparql）、グラフ統計（triples 件数等）。
- データを追加: source種別, アップロード/接続情報, ステップ(design/review/save), AI提案(設計図/ルール/品質チェック配列), refine入力。
- 質問する: 質問テキスト, 回答（本文/引用配列/品質メモ）, 選択引用, 来歴チェーン, loading。
- カタログ: 選択データセット, 詳細タブ(design/rules)。
- 共有の語彙: クラス一覧, 利用データセット＋束縛戦略, 影響範囲プレビュー。
- データ取得は既存 `demoApi.ts` / `galleryApi.ts` / `jobsApi.ts` / `provenance` の境界を踏襲（モック/実APIの切替 `isMockMode` を維持）。

## Assets
- 画像アセットなし。全アイコンはインライン SVG（ストローク）。既存コードベースにアイコンセットがあればそれへ置換可。
- ブランドマーク（asterism: 3星＋3線）は `shell.jsx` の `BrandMark` 参照。
- フォントは Google Fonts: Hanken Grotesk / Zen Kaku Gothic New / Noto Sans JP / IBM Plex Mono。既存の読み込み方法に合わせる。

## Files（このバンドル内）
```
prototype/
  Asterism UX 再設計.html   … エントリ（デザインキャンバス。forest が採用）
  kit.jsx                  … トークン(FOREST/CLEAN/CONSTELLATION)・アイコン・ClassDiagram・原子コンポーネント
  shell.jsx                … AppFrame(サイドバー/ヘッダ)・Btn・Term・Card・BrandMark・NAV
  screen-home.jsx          … ホーム
  screen-add.jsx           … データを追加（3ステップ・ソース切替）
  screen-ask.jsx           … 質問する＋来歴トレース
  screen-catalog.jsx       … カタログ（データセット主役・設計図/取り込みルール タブ）
  screen-vocab.jsx         … 共有の語彙
  screen-extras.jsx        … API接続フロー(ScreenConnect)・状態(ScreenStates)
  context.jsx              … 設計意図の説明ボード（実装不要・参考）
  design-canvas.jsx        … 比較用キャンバスの足場（実装不要）
```
> 実装の起点は `kit.jsx`(FOREST)＝トークン、`shell.jsx`＝IA/原子。各 `screen-*.jsx` が画面仕様の正本。`context.jsx` は「なぜそう変えたか」の根拠として一読を推奨。

---
*この README 単体で実装着手できるように記述しています。プロトタイプはあくまで見た目・挙動の正本であり、本番は `csv2rdf-mcp/ui` の既存パターン（React + TS + Vite ＋ forest green CSS トークン）で再現してください。*
