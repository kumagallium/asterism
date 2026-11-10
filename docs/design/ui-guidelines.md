# Asterism UI ガイドライン（実装の正）

Web UI（`ui/`・React + TS + Vite + react-i18next）の**実装側デザインシステム**。一貫性のため、新しい画面・コンポーネントは本書のトークンとパターンに従う。
元の UX 方向性は `docs/design/asterism-ux/`（ハンドオフ）と Claude Design の v2 を参照。**本書はそれを実装に落とした「現在の正」**で、矛盾する場合は本書が優先。

トークンの定義元は **`ui/src/index.css` の `:root`**（CSS 変数）。色・余白・角丸・影・タイプはすべてここ。**生の値をコンポーネントに直書きせず、必ず CSS 変数を使う。**

ロゴの正本は [`brand/source/`](../../brand/source/)。左右対称の三つ星 A とロゴタイプ 01 を使用し、本文フォントは既存のままにする。配布用素材と UI 用パスは [`scripts/sync_brand.py`](../../scripts/sync_brand.py) で同期する。編集・生成手順は [`brand/README.md`](../../brand/README.md) を参照。

---

## 1. 原則

- **紺色 `#002869` の単一ブランド**。アプリとマニュアルでロゴと共通の紺色を使い、カラー探索や他テーマは持たない。
- **役割と見分け方は固定**：データ＝`--entity`（紺）／処理・つながり＝`--activity`（=`--link`、青灰）。成功も紺系とし、警告・エラーは専用の注意色を維持する。グラフは色に加えて形・文字を使う（§6）。
- **日本語ファースト**。英語は小さな補助ラベル（mono・faint）。専門語は消さず `details`／点線ヒントで必要な人にだけ。
- **視認性優先**：本文は薄いグレーにしない。段落は `--body`、見出しは `--fg`。`--muted`/`--faint` はラベル・補助のみ。
- **引用できる事実**が主役。決定論・型付きツールが事実を出し、AI は補助（ルーティング等）。

## 2. 現行の画面構成（ChatApp）

| 領域 | 役割・寸法 |
|---|---|
| 左 | チャット一覧、新しいチャット、設定。幅 220px、折りたたみ時 56px |
| 中央 | 会話・添付・承認・入力。白い作業面を基本とする |
| 右 | 知識グラフと問いのタブ、選んだ点の関係とデータ |

- 左右の上部は 64px を共通の整列基準とする。ロゴはマーク 26px、ロゴタイプ高さ 14.5px。本文より強く目立たせない。
- 幅 1100px 以下では左を初期状態で折りたたみ（保存した開閉状態を優先）、980px 以下では「チャット／知識グラフ／問い」のタブに切り替える。左一覧はシートで開く。
- 設定は左下の ⚙ からオーバーレイで開く。現行の主画面に旧 5 項目ナビを戻さない。
- 画面の名詞は **データセット／テーマ／問い／用語**（標準のものは「標準用語」）。関係は「A は B の一種」など文で示す。
- 旧ホームの 2 入口やオブジェクト別ナビの経緯は ADR [`sky-chart-two-entries.md`](../architecture/sky-chart-two-entries.md) に残す。以下の `.app-content`・`.card` などは詳細画面・旧画面の共通部品規約であり、ChatApp の主ナビを定義しない。

## 3. 色トークン（`index.css`）

| 用途 | 変数 | 値 |
|---|---|---|
| 画面背景 | `--bg` | #f5f7fa |
| カード/パネル | `--surface` | #ffffff |
| 薄い面（入力/コード） | `--surface-alt` | #f7f9fc |
| 沈んだ面（タブ列/表頭） | `--surface-sink` | #edf1f6 |
| 星図の地（紙の星図・canvas の背景） | `--paper` | #fbfcfe |
| 見出し・強調 | `--fg` | #16243b |
| **本文（段落）** | `--body` | #34445e |
| 補助テキスト | `--muted` | #566781 |
| キャプション/プレースホルダ | `--faint` | #60728a |
| ボーダー / 強ボーダー | `--border` / `--border-strong` | #dce4ef / #c1cdde |
| 主要アクション/選択 | `--primary` | #002869 |
| 主要アクションのホバー | `--primary-hover` | #001e52 |
| 選択背景/薄いボタン | `--primary-soft` | #e5ecfa |
| さらに薄いブランド背景 | `--primary-softer` | #f0f4fc |
| 主要アクション上の文字 | `--primary-fg` | #ffffff |
| 濃色の星空/その上の文字 | `--sky` / `--sky-fg` | #132744 / #edf2fc |
| アクセント（注意/eyebrow） | `--accent` (+`-soft`) | #b3722b |
| データ系（PROV entity） | `--entity` / `--entity-soft` | #002869 / #e9eff8 |
| 処理・つながり（PROV activity / link） | `--activity`＝`--link` / `--activity-soft` | #526f9a / #edf1f7 |
| 成功（文字 / 背景 / 枠） | `--success-fg` / `--success-bg` / `--success-border` | #254b70 / #edf3f9 / #c1d2e4 |
| グラフの用語 / 種類 / データセット | `--graph-term` / `--graph-kind` / `--graph-dataset` | #526f9a / #002869 / #68778e |
| グラフの関係 / 地 | `--graph-edge` / `--graph-paper` | #687e9b / #fbfcfe |

## 4. スペーシング（4px ベース）

主画面では **8 / 16 / 24px** を基本のリズムとし、関連する要素を近づけ、別のまとまりを離す。左右ペインの見出し・操作を同じ基準線に整列させる。

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
- 影：共通の RGB は `15, 35, 66`。`--shadow-soft`（カード常用）=`0 1px 2px rgba(15, 35, 66, 0.045)`、`--shadow`（浮かせる/選択）=`0 1px 2px rgba(15, 35, 66, 0.05), 0 10px 28px rgba(15, 35, 66, 0.07)`。
- フォント：`--font-ui`（Hanken Grotesk＋Zen Kaku/Noto Sans JP のゴシック）。**数値・ID・コード・CURIE は `--font-mono`**（IBM Plex Mono）。明朝は使わない。
- 実寸の目安：本文 14–15px、補足・英語補助は 12px 以上。`--text-xs`=12 / `--text-sm`=13 / `--text-md`=14 / `--text-lg`=16 / `--text-title`=20px（16px 基準）。見出しは大きさ・太さ・余白で序列を作る。
- 操作の高さは通常 36px（`--control-height`）、アイコン操作 32px（`--control-height-sm`）。粗いポインターでは 44px 以上へ広げる。
- **かんたん層（`.kz-*`）だけは強弱を 1 段ずつ上げる**：カード見出し 1.25rem/700、画面の 1 文 0.95rem(`--body`)、ゾーン見出し 0.8rem/700(`--accent-strong`)、注記 0.875rem。行長は列（880px）で決め、段落ごとの max-width は足さない。理由と全体像は ADR [`kantan-mode-two-tier-ux.md`](../architecture/kantan-mode-two-tier-ux.md) の K23。

## 6. コンポーネント / パターン

- **詳細・旧画面のヘッダ（topbar）**：大見出し（display 1.5rem/700・`white-space:nowrap`）＋ 言語トグル**だけ**（eyebrow・補足・ボタンは置かない — 2026-10-20。「見る」の戻る・パンくずは例外）。i18n は `common.view.<tab>.title`。
- **カード**：`.card`（surface/border/`--radius`/`--shadow-soft`/`--space-5`）。セクションを束ねて**メリハリ**を出す単位。
- **セクション見出し**：`.ds-subhead`（`--fg`・0.9rem・上 `--space-6`）。カード先頭の見出しは自動で上マージン 0。
- **ボタン**：
  - 主要＝紺塗り（既定の `<button>`）。`.btn--primary/--accent/--ghost/--soft/--link/--danger`、`.btn--sm`。
  - **戻る/二次ナビは「静かなテキストリンク」**（`.vocab-back`：bg/border 無し・`--muted`、hover で `--fg`）。濃い紺のボタンにしない。
  - 詳細画面の戻りは**カードの外**に置く（`.ds-detail-wrap`）。
- **チップ/ピル**：`.status-pill--{pub,draft,design}`、用途別 chip（entity/link/accent/neutral）。
- **タブ**：`.ds-tabs`（`--surface-sink` のピル列）＋ `.ds-tab`（選択＝白＋`--shadow-soft`）。
- **StdToken**（外部標準）：平易語 + 標準名 + mono CURIE。確定=実線 / AI候補=accent 点線。
- **意味色の使い分け**：データ/件数=entity、つながり/橋=link、注意/AI候補=accent。
- **チャット**：現行は `ui/src/chat/ChatApp.tsx` と `chat/chat.css` の 3 領域（§2）。会話・一覧・グラフのスクロールをそれぞれの領域内に閉じる。
- **知識グラフ**：○ 輪郭円＝用語（青灰）、● 塗り円＝データの種類（紺）、⊙ 中心点のある輪郭円＝データセット（灰青）。凡例も同じ記号で示す。未承認は淡さに加え、名前に「(提案)」（英語は `(proposed)`）を添える。
- **グラフの選択**：選択点の上に小さな紺色の星を一つ添え、点と直結する線を少し強める。星は選択状態を示す。元の種類の記号を残し、発光・点滅は使わない。星の位置は拡大・移動に追従し、選択を解除すると消える。
- **値のつながり（NetworkView）**：最大 8 種類を紺・青灰の配色と番号 ①〜⑧で示し、点の名前と凡例で同じ番号を使う。残りは灰色。選択・絞り込み・ハブ・束の操作は維持する。

### 判断の根拠

| 原則 | 適用 |
|---|---|
| 視覚的序列 | 小さなロゴ、読みやすい本文、静かな補助領域で会話・データを主役にする |
| 近接・整列 | 8 / 16 / 24px の余白と 64px の上部基準でまとまりと対応関係を示す |
| 反復 | 同じ役割は同じトークン・操作寸法・記号で表示する |
| 色以外の手がかり | 輪郭・塗り・中心点・番号・「提案」を併記する（[W3C: Use of Color](https://www.w3.org/WAI/WCAG22/Understanding/use-of-color.html)） |
| 文字のコントラスト | 通常の本文・補足は背景との 4.5:1 以上を基準に確認する（[W3C: Contrast Minimum](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html)） |

この方針は設計・検査の基準であり、製品全体の WCAG 適合を表明するものではない。

## 7. i18n

- すべての文言は `ui/src/i18n/locales/{ja,en}/<ns>.json`。**ja を主**に、en を必ず併記。新文字列は両方へ。
- 照合はロケール非依存 key で。`.ts` 内で i18n を使う時はシングルトンを関数内で取得。
- 用語：カタログ→**データセット** / 共有の語彙→**用語**（英 Term）/ 外部標準の語→**標準用語** / クロスウォーク→**つながり**（英 Connection）/ 接地→**標準用語に合わせる** / 線→**関係**（文で言う）。

## 8. 落とし穴（必読）

- **グローバル `button:hover` の塗り漏れ**：広い `button:hover:not(:disabled){background:var(--primary-hover)}` は詳細度(0,2,1)が custom `<button>` の `:hover`(0,2,0) に勝ち、静かなボタンまで紺色に塗る。現行のグローバル規則は `button:not([class]):hover:not(:disabled)` に限定する。`.btn:hover:not(:disabled)` は `var(--primary-hover)` を使い、変種ボタンは後続の各規則で自分の背景を明示する。
- **全幅**：`.app-content` に `max-width` を付けない（右に余白が出る）。一覧グリッドは `repeat(auto-fill, minmax(300px,1fr))` で埋める。
- **mock/live**：`isMockMode`（`VITE_DEMO_MODE`）でゲート。本番は `live`＋demo-agent 稼働で「mock」表記が消える。Claude Preview は常に mock 強制。
- 生成コードを実行しない／決定論・型付きツール優先（プロダクト原則。`CLAUDE.md` 参照）。

---
*更新時はこのファイルと `index.css` の `:root` を同時に。トークンを増やしたら必ずここに追記する。*

提案の点は `--graph-proposed: #8193ab`、提案の線は `--graph-edge-proposed: #8193ab` と細い線で示し、触れたときの名前に「提案」を添える。補助説明や提案の本文を opacity で薄くせず、読みやすい文字色と状態ラベルを使う。
