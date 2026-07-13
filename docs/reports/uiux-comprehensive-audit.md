# 検証レポート: UI/UX 網羅監査（10 次元コード監査 + 実ブラウザ監査）

2026-07-11 / 関連: [`docs/design/ui-guidelines.md`](../design/ui-guidelines.md)（実装の正）

## Question

Asterism Web UI は、デザインガイドライン（forest 単一テーマ・日本語ファースト・視認性優先・
「引用できる事実」主役）に **実装レベルで一貫して従えているか** — 従えていない箇所はどこで、
ユーザー体験を実際に損なうものはどれか。

## Method

2 系統の監査を統合した（対象コミット: main bb1fbc5 時点の `ui/src/` 全体）。

1. **多次元コード監査（マルチエージェント）**: 10 次元（デザイントークン / i18n / a11y /
   コンポーネント一貫性 / UX ライティング / ローディング・エラー・空状態 / レスポンシブ /
   dead CSS / IA・ナビゲーション / フォーム操作）× 各 1 ファインダ →
   全所見を低コスト検証エージェントが個別に反証チェック（refuted なら破棄）→
   網羅性チェッカーが見落としを補完。raw 86 件 → 検証通過 + 補完 = **確定 102 件**
   （高 4 / 中 46 / 低 52。dead CSS・コンポーネント次元は初回失敗のため単独再走）。
2. **実ブラウザ監査**: 実データを投入した実機スタック（Oxigraph 2.1M triples +
   registry 3 データセット: 公開 2 / 設計中 1）で全画面を巡回し、コード監査の主要所見を
   目視・DOM 検査で確認。ja / en 両言語・デスクトップ / 375px 幅。

## Result（主要所見と対応）

### 修正済み（本 PR）— 抜粋

**正しさ / データ忠実性**
- データセット改名入力に IME 確定 Enter のガードが無く、日本語名が変換途中で確定される
  （`GalleryView`・isComposing ガード追加）。
- アクティビティの時刻がバックエンド UTC の接尾辞を削って生表示され、JST で 9 時間ずれる
  （ローカル時刻へ変換）。
- 設計段階（未取り込み）のソースファイルに「✓ 取り込み済み」と表示（ステージ対応ラベルへ）。
- ホーム/一覧の日付ラベルが公開済みでも常に「設計を保存」（公開 · promoted_at 等へ）。
- Ask / SPARQL の実行が待機中の Enter / Cmd+Enter で並行二重送信される（ガード追加）。
- API 障害が「まだデータセット/記録がありません」という **誤った空状態** に丸められる
  （galleryApi / jobsApi を throw に変更し、エラー表示へ配線）。

**系統的 CSS 特異度リーク（今回の最大発見）**
- グローバル `button:hover:not(:disabled)`（濃緑塗り・特異度 (0,2,1)）が、背景を宣言しない
  ほぼ全ての「静かなボタン」のホバーに勝ち、**選択中ナビ・引用カード・設定タブ・モード切替
  ピル・⋯メニュー・セルコピー等 15 箇所超**で「ホバーすると濃緑に塗り潰されて文字が沈む」
  事故を起こしていた。恒久修正としてグローバル hover を `button:not([class])` に限定し、
  primary 塗りを保つ `.btn` 等は自前の hover を持たせた。
- 同型の `.controls label`（column）が `.hint-check`（row）に勝ち、ワークベンチの
  ヒントチェックボックスが縦積みに崩れていた（label 修飾で解消）。
- `.btn--danger` の二重定義（SettingsModal.css が import 順で全画面に勝つ）を撤去し、
  設定モーダルのボタンを `.btn` システムへ一本化。`.secondary-btn` も廃止・統合。

**a11y**
- ファイル選択 input が `display:none` でキーボード/SR から到達不能（sr-only 化 +
  focus-within リング）。検索 / Ask 入力のフォーカスリング全消し（復活）。
- ナビ / タブに aria-current、SPARQL テキストエリア・crosswalk の 5 select に
  aria-label、Ask 回答領域に aria-live、アイコンレール時のナビに title/aria-label。
- `.field-error` 未定義（エラー文が最薄グレーで埋没）→ 定義追加。

**機能ギャップ**
- **書き込みトークンを UI から設定する手段が存在しなかった**（`setApiToken` が未使用。
  トークン保護配備では取り込み・公開・統計がすべて沈黙 503）。設定モーダルに
  「書き込みトークン（このブラウザ）」セクションを新設（保存 / クリア / 接続を確認 =
  実サーバ検証。実機で 200 → 「トークンは有効です」を確認）。
- ホーム統計が取れないとき「—」を黙って出す → 原因への手がかり（トークン / 公開設定）を
  一言添えるように。

**状態 / 導線**
- Ask の回答・引用が **タブ遷移で完全消失**（LLM 再課金）→ モジュール保持で復元。
  SPARQL の書きかけクエリ / 結果も同様に保持。
- タブ切替でスクロール位置が持ち越される（共有スクロールコンテナのリセット追加）。
- 全体像の「戻る」が常に「つながり」固定 → 入ってきた画面（データセット詳細等）へ。
- 破壊的操作の確認: 作業内容クリア / ツール下書きの上書き / SPARQL 例に戻す。
- 引用 IRI（来歴トレース）と Ask の開示 SPARQL にコピー手段を追加。

**ライティング / 用語（ja・en 両方）**
- ナビに存在しない旧称の排除: 「ワークベンチ」→「データを追加」、「Ask / Gallery」→
  「質問する」「データセット」。開発者ジャーゴンの平易化: 「PR なしで」「脱出ハッチ」
  「§RML」「8 罠 / exit 0」「O(新規)」「FK 列」「propose」など。
- 表記統一: 投入/取り込み → 取り込む、draft グラフ → 下書きグラフ、triples → 件、
  スキーマ → 設計図、取消/やめる → キャンセル、DS → データセット。
- 誤案内の修正: Ask の「先に API キーを入力」（入力欄はもう画面に無い）、アクティビティ
  空状態の「データを追加で取り込むと記録される」（実際は watcher/追記経路のみ記録）、
  ファイル重複検知の過大主張（en の "same name or contents… never wonder"）。

**視覚一貫性**
- Mermaid クラス図がデフォルト紫テーマ → forest トークン（base + themeVariables）。
- 未定義 CSS トークン参照 6 箇所（--surfaceAlt / --surface-2 / --text / --card /
  --danger）を正トークンへ。SkeletonGate（Phase 2b 人間ゲート）の CSS 欠落を新設。
- dead CSS 約 430 行（57 クラス・旧カタログ 2 ペイン / shared-band / ask-key 残骸等）を
  スクリプト再検証つきで削除。
- 狭幅崩れ: ソース切替の折返し・ds-grid の `minmax(min(300px,100%),1fr)`・ステッパー
  折返し・モード切替ピルの語中折れ・長いデータセット名の溢れ・メニューの z-index。

### フォローアップで対応済み（本レポート後の 2 PR）

- **URL ルーティング不在**（旧 2）→ 対応済み（PR #274）: hash ルータ（`#/datasets/<id>/<tab>`・
  リロード/戻る/ディープリンク）。
- **一覧⇄詳細の状態破棄ファミリー**（旧 3）→ 対応済み（#274 + `ui/audit-followup-2`）:
  GalleryView 選択を App ルートへ引き上げ、ホーム「最近」行→詳細直行、保存完了→データ
  セットへの直リンク、CrosswalkBuilder / DocumentPanel の作業状態を sessionStorage 永続化。
- **カード nested-interactive**（旧 5）→ 対応済み（`ui/audit-followup-2`）: ストレッチ
  ドリンク方式（外側 div 非対話化・開くリーフ button の全面オーバーレイ・アクションは
  z-index で前面・ルーティング挙動不変）。
- **設定モーダルのフォーカストラップ**（旧 6）→ 対応済み（#274）: Tab 循環トラップ。
- **パレットのコントラスト**（旧 1・部分）→ 一部対応: status-pill / .link-btn の文字色を
  AA 化（#274）、`--faint` を 2.99:1→3.4:1+（`#728579`・`ui/audit-followup-2`）。`--accent`
  の塗り/罫線用途は据え置き（文字用途は `--accent-strong` に分離済み）。

### 未修正（別途・API 変更を伴う）

- **ingest / 文書取り込みのキャンセル・リロード復旧**（中）→ 対応済み
  （`feat/ingest-job-cancel-replay`）: ingest_job に協調キャンセル・チェックポイント
  （文書毎 / PDF 毎 / Morph-KGC subprocess poll / アップロードチャンク毎 / コミット直前
  ゲート）を挿入し、キャンセルは既存の失敗パス（chunked_drop_graph）で部分 version
  graph を即時回収。UI は job_id を sessionStorage に永続化し、リロード後に SSE replay
  で再購読（IngestControl / ReingestControl / IngestGate / DocumentPanel）＋キャンセル
  ボタン＋heartbeat 生存表示。実機で「取り込み中リロード→busy 復元→キャンセル→
  部分グラフ回収」まで実証。
- **共有の語彙の consumers に設計中 DS が並ぶ**（低・要確認）: 集計は canonical のみ
  注記済みだが、リスト対象の整合は再確認したい。
- アクティビティに Workbench 取り込みも記録する（jobs.jsonl へ kind:"ingest"）→
  対応済み（同上）: ingest ジョブの ok / error / cancelled を記録し、手動 append・
  文書 append も kind:"append" で記録。アクティビティ画面は ingest/append レコードの
  ファイル名・triples を表示（行数なしは「—」）。

## Conclusion

ガイドライン自体は明確で、大半の画面は準拠している。一方で**「グローバル既定スタイル
（primary 塗りボタン・.controls label）と、静かなコンポーネントの特異度衝突」という
系統的な欠陥ファミリー**が視認性事故の大半を生んでいた（ホバー濃緑化 15 箇所超・
チェックボックス縦積み）。個別対処ではなくグローバル側を絞る恒久修正を入れた。
機能面の最大ギャップは**書き込みトークンの UI 欠落**（トークン保護配備で書き込み系が
全滅する）で、本 PR で解消した。ライティングは「ワークベンチ / Gallery / draft /
triples」等の旧称・内部語の残存が主で、用語表に沿って統一した。
残る大物は「URL ルーティング」「状態破棄ファミリー」「パレットのコントラスト」の 3 つで、
いずれも構造 / デザイン判断を伴うため別作業とした。

## Limitations

- コントラスト計算は WCAG 2.x の相対輝度式によるスポット計算で、全ペア網羅ではない。
- 実ブラウザ監査は 1440px / 375px の 2 幅・Chromium のみ。実スクリーンリーダー
  （VoiceOver 等）での読み上げ検証は未実施（aria 属性はコードレベルで確認）。
- 実データは監査用にシードした 3 データセット（レジストリ直書き）で、Workbench の
  LLM propose 実行は監査対象外（mock で画面確認）。
- dead CSS 削除は「.tsx/.ts 内の文字列・テンプレート断片との突き合わせ + 動的接頭辞の
  保守的除外」で 2 重に検証したが、外部からの className 注入があれば漏れうる。

## Reproduce

```bash
# コード監査所見（確定 102 件）の一次資料はセッションの workflow 出力に保存:
#   scratchpad/findings.json（file/line/summary/evidence/severity/fix_hint/verify_reason）
# 実ブラウザ監査のスタック:
docker compose -f compose.yaml up -d oxigraph
python3 datasets/materials_project/seed/load.py http://127.0.0.1:7878 datasets/materials_project/seed
python3 scripts/make_demo_subset.py --src ../starrydata_dataset --n-papers 40
python3 datasets/starrydata/seed/load.py http://127.0.0.1:7878 datasets/starrydata/seed
API_PORT=8085 ASTERISM_EXPOSE_RAW_SPARQL=1 ASTERISM_API_TOKEN=<token> \
  scripts/run_local_substrate_stack.sh
VITE_API_PROXY=http://127.0.0.1:8085 VITE_API_TOKEN=<token> npm --prefix ui run dev
# 検証: npm --prefix ui run build && npm --prefix ui run lint
```
