# Asterism UI ガイドライン（実装の正）

Web UI（`ui/`・React + TS + Vite + react-i18next）の**実装側デザインシステム**。一貫性のため、新しい画面・コンポーネントは本書のトークンとパターンに従う。
元の UX 方向性は `docs/design/asterism-ux/`（ハンドオフ）と Claude Design の v2 を参照。**本書はそれを実装に落とした「現在の正」**で、矛盾する場合は本書が優先。

トークンの定義元は **`ui/src/index.css` の `:root`**（CSS 変数）。色・余白・角丸・影・タイプはすべてここ。**生の値をコンポーネントに直書きせず、必ず CSS 変数を使う。**

---

## 1. 原則

- **forest 単一テーマ**（緑）。カラー探索や他テーマは持たない。
- **意味色は固定**：データ＝`--entity`（緑）／ 処理・つながり＝`--activity`（=`--link`、青）。PROV-DM 準拠で全画面一貫。
- **日本語ファースト**。英語は小さな補助ラベル（mono・faint）。専門語は消さず `details`／点線ヒントで必要な人にだけ。
- **視認性優先**：本文は薄いグレーにしない。段落は `--body`、見出しは `--fg`。`--muted`/`--faint` はラベル・補助のみ。
- **引用できる事実**が主役。決定論・型付きツールが事実を出し、AI は補助（ルーティング等）。

## 2. IA / ナビ

- フラットなオブジェクト軸ナビ（グループ見出し無し・**グローバル作成ボタン無し**）：
  **ホーム / データセット / つながり / 質問する / 共通の言葉 / アクティビティ**（＋最下部に SPARQL「開発者向け」）。
- 作成はインライン（ホームの大アクション、データセット一覧の「新しいデータセット」タイル）。
- 「データを追加（workbench）」「全体像（map）」はナビに出さない（前者はホーム/一覧から、後者は「つながり」から）。

## 3. 色トークン（`index.css`）

| 用途 | 変数 | 値 |
|---|---|---|
| 画面背景 | `--bg` | #eef3ec |
| カード/パネル | `--surface` | #ffffff |
| 薄い面（入力/コード） | `--surface-alt` | #f4f8f1 |
| 沈んだ面（タブ列/表頭） | `--surface-sink` | #eaf0e8 |
| 見出し・強調 | `--fg` | #16241a |
| **本文（段落）** | `--body` | #33453a |
| 補助テキスト | `--muted` | #54695b |
| キャプション/プレースホルダ | `--faint` | #869a8c |
| ボーダー / 強ボーダー | `--border` / `--border-strong` | #dde6da / #c7d4c4 |
| 主要アクション/選択 | `--primary` (+`-soft`/`-softer`/`-fg`) | #3f6f49 |
| アクセント（注意/eyebrow） | `--accent` (+`-soft`) | #b3722b |
| データ系（PROV entity） | `--entity` (+`-soft`) | #3a7a4c |
| 処理・つながり（PROV activity / link） | `--activity`＝`--link` (+`-soft`) | #356794 |

## 4. スペーシング（4px ベース）

スケール：`--space-1`=4 / `-2`=8 / `-3`=12 / `-4`=16 / `-5`=20 / `-6`=24 / `-8`=32 / `-12`=48。

**適用ルール（守る）**
- ページ外周（`.app-content`）= `var(--space-6) var(--space-8) var(--space-12)`（24/32/下48）
- 画面のブロック間（各 view ルートの `gap`）= **`var(--space-4)`（16）**
- カード内（`.card`）= `var(--space-5)`（20）
- サブセクション間 = `var(--space-3)`（12）／ インライン要素間 = `var(--space-2)`（8）／ 細かな間 = `var(--space-1)`（4）
- セクション見出し（`.ds-subhead`）= `margin: var(--space-6) 0 var(--space-2)`

新規の画面ルートは `display:flex; flex-direction:column; gap: var(--space-4)` を基本形に。

## 5. 角丸・影・タイプ

- 角丸：カード `--radius`(12) / ボタン・入力・小カード `--radius-sm`(8) / 大アイコンチップ `--radius-lg`(16) / ピル `999px`。
- 影：`--shadow-soft`（カード常用）/ `--shadow`（浮かせる/選択）。
- フォント：`--font-ui`（Hanken Grotesk＋Zen Kaku/Noto Sans JP のゴシック）。**数値・ID・コード・CURIE は `--font-mono`**（IBM Plex Mono）。明朝は使わない。
- 実寸の目安：画面大見出し 1.5rem/700、セクション見出し 0.9rem/700(`--fg`)、本文 0.8–0.9rem(`--body`)、回答本文 1.12rem/1.85、キャプション 0.66–0.76rem、英語補助 0.62–0.66rem(mono/faint)。

## 6. コンポーネント / パターン

- **画面ヘッダ（topbar）**：eyebrow（accent・小）＋ 大見出し（display 1.5rem/700・`white-space:nowrap`）＋ 右に補足（`--body`）＋ 言語トグル。i18n は `common.view.<tab>.{eyebrow,title,sub}`。
- **カード**：`.card`（surface/border/`--radius`/`--shadow-soft`/`--space-5`）。セクションを束ねて**メリハリ**を出す単位。
- **セクション見出し**：`.ds-subhead`（`--fg`・0.9rem・上 `--space-6`）。カード先頭の見出しは自動で上マージン 0。
- **ボタン**：
  - 主要＝緑塗り（既定の `<button>`）。`.btn--primary/--accent/--ghost/--soft/--link/--danger`、`.btn--sm`。
  - **戻る/二次ナビは「静かなテキストリンク」**（`.vocab-back`：bg/border 無し・`--muted`、hover で `--fg`）。濃い緑のボタンにしない。
  - 詳細画面の戻りは**カードの外**に置く（`.ds-detail-wrap`）。
- **チップ/ピル**：`.status-pill--{pub,draft,design}`、用途別 chip（entity/link/accent/neutral）。
- **タブ**：`.ds-tabs`（`--surface-sink` のピル列）＋ `.ds-tab`（選択＝白＋`--shadow-soft`）。
- **StdToken**（外部標準）：平易語 + 標準名 + mono CURIE。確定=実線 / AI候補=accent 点線。
- **意味色の使い分け**：データ/件数=entity、つながり/橋=link、注意/AI候補=accent。

## 7. i18n

- すべての文言は `ui/src/i18n/locales/{ja,en}/<ns>.json`。**ja を主**に、en を必ず併記。新文字列は両方へ。
- 照合はロケール非依存 key で。`.ts` 内で i18n を使う時はシングルトンを関数内で取得。
- 用語：カタログ→**データセット** / 共有の語彙→**共通の言葉** / クロスウォーク→**つながり**（英 nav は "Crosswalk" 可）/ 接地→**外部の標準に合わせる**。

## 8. 落とし穴（必読）

- **グローバル `button:hover` の緑漏れ**：`button:hover:not(:disabled){background:#356040}` の詳細度(0,2,1)が、custom `<button>` の `:hover`(0,2,0) に勝つ。custom ボタンは `:hover:not(:disabled)` か `.x.active:hover`（≥0,3,0）で背景を明示し緑漏れを防ぐ。
- **全幅**：`.app-content` に `max-width` を付けない（右に余白が出る）。一覧グリッドは `repeat(auto-fill, minmax(300px,1fr))` で埋める。
- **mock/live**：`isMockMode`（`VITE_DEMO_MODE`）でゲート。本番は `live`＋demo-agent 稼働で「mock」表記が消える。Claude Preview は常に mock 強制。
- 生成コードを実行しない／決定論・型付きツール優先（プロダクト原則。`CLAUDE.md` 参照）。

---
*更新時はこのファイルと `index.css` の `:root` を同時に。トークンを増やしたら必ずここに追記する。*
