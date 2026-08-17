# 設計中のソースはサーバに置く — ドロップした瞬間から

Status: accepted (2026-08-17)

Related: [`local-first-distribution.md`](local-first-distribution.md),
[`kantan-mode-two-tier-ux.md`](kantan-mode-two-tier-ux.md),
[`column-ownership-and-growth.md`](column-ownership-and-growth.md)（G11: 使えない機能は理由ごと出す）,
[`source-dialect.md`](source-dialect.md)

## 0. 問題 — 「ソースの正本はどこか」が段階で変わっていた

| 段階 | ブラウザ | サーバ |
|---|---|---|
| S1〜S4（ドロップ〜骨格ゲート） | メモリのみ（sessionStorage は File を持てない） | ジョブごとに一時 dir へ上げ、**終わると削除** |
| S5 保存以降 | — | `registry/<dataset>/source/` に永続 |

設計フェーズだけ、ファイルの持ち主がブラウザ 1 タブだった。だからリロード・復元でファイルだけが
消え、骨格ゲートは半死で開いた（再検査 skip・「行ごとの種類を作る」が効かない・「AI にもう一度」が
消える・再取り込みが同じファイルを要求 — 2026-08-14 の実 dogfood）。加えて、同じ 47 行の
ファイルを skeleton → 再検査 → continue のたびに上げ直していた。

これは**プラットフォームの制約ではなかった**。デスクトップは「サーバ＝自分のディスク」で、web
インスタンスにもディスクはある。コードの書き方がそうなっていただけで、コード内コメントも
「Files are not persistable — restore is best-effort by design」と、原則ではなく受け入れた制約
として書いていた。#369（IndexedDB）は症状をブラウザ側で閉じたが、正本の所在は変えていない。

ユーザーの問い: 「そもそもデータソースが消えてしまう構造だったのですか？データソースは
永続化されるべきではないのですか」。答えは Yes — 保存以降と同じく、**最初から**サーバが正本を持つ。

## 1. 決定事項

| # | 論点 | 決定 | 理由 |
|---|------|------|------|
| P1 | 置き場 | `POST /api/staging`（write-gated）が uploads を `registry_root/_staging/<uuid4>/` に **1 回だけ**書く。`raw/` に受け取ったまま、ルートに設計用の正規形（xlsx 展開・名前 slug 済み）。`meta.json` に順序つき正規名 | 設計が読むものと、後で `source/` に昇格するものを両方持つ。`raw/` があるので attach は**新規アップロードとまったく同じ変換器**を通る（xlsx/docx の原本同梱も従来どおり） |
| P2 | 参照 | inspect / propose / skeleton / skeleton-validate / continue は `staging_id`（Form）を `files` の代わりに受け付ける。`_design_sources()` が `(work_dir, paths, owned)` を返し、staging なら `owned=False`＝呼び出し側は削除しない | 再アップロード消滅。ジョブは staging dir を直接読む（validate_rml_design の `source_dir` も staging dir） |
| P3 | 昇格 | `POST /api/datasets/{id}/source` が `staging_id` を受け、`raw/` を `UploadFile` に包み直して `_persist_source_uploads` に流す → `source/` が正本になり staging は**消費**（削除） | 変換経路を 2 本にしない。「保存以降はサーバが正本」という既存の筋に、設計中を接続する |
| P4 | 寿命 | クライアントが `DELETE`（やり直し）／attach が消費／**TTL 7 日**の sweep（create のたび）。id は uuid4 を厳密に検証＝path になり得ない capability | 放置分の掃除と、id が唯一のクライアント制御パス成分であることの防御 |
| P5 | クライアント | drop 直後に `stageSources()`（失敗は無視＝ `stagingId=null` で従来経路）。以降の設計呼び出しは `stagingId` を渡し **files を送らない**。`hasSource = files.length>0 || !!stagingId` が全ゲートの基準。snapshot に id（文字列）を保存し、mount で `stagingAlive` を確認（死んでいれば捨てる）。真のやり直しで `unstageSources` | 段階的: 旧サーバ・閉じた write gate では 503/404 → 黙って従来経路（#369 の IndexedDB 複製が効く）。**新サーバでは IndexedDB を消しても S4 に戻れる**（実証） |
| P6 | 二重保持 | #369 の IndexedDB 複製は**残す** | staging が使えない環境の受け皿。両方あるとき mount は staging を優先し、再ステージしない（重複レコードを作らない） |

## 2. 形態ごとの意味

- **デスクトップ**: 「サーバ」＝自分の PC。ドロップした瞬間に `~/Library/Application Support/Asterism/sources/registry/_staging/` に入る＝ローカルファーストそのもの。
- **Web インスタンス**: authgate 配下。staging id は uuid4 capability・7 日 TTL。他人の下書きは id を知らない限り見えない（設計 API は元々トークン不要で、構造とサンプル値を返す＝従来と同じ露出）。

## 3. 実装

- **api** `staging.py`（新規）: `new_id / valid_id / dir_for / write_meta / load / raw_paths / delete / sweep / expires_at`
- **api** `main.py`: `POST/GET/DELETE /api/staging[/{id}]`・`_uploads_from_dir`・`_design_sources`・5 エンドポイントの `staging_id` 受理と `owned` 付き cleanup・attach の staging 消費
- **ui** `api.ts`: `appendSources()`（files or staging_id）・各設計呼び出しの末尾引数 `stagingId?`・`stageSources / stagingAlive / unstageSources`
- **ui** `KantanWizard.tsx` / `WorkbenchView.tsx`: `stagingId` state＋snapshot・drop で stage・`hasSource`・mount で alive 確認（Kantan は #369 の復元と 1 効果に統合）・やり直しで unstage・attach に staging を渡し消費後 null

## 4. 検証

- api 389 緑（+6: 生成→GET→DELETE／id は capability（traversal・非 uuid・未知 uuid は 404）／inspect・skeleton-validate が staging を読み **record が残る**／attach が消費し `source/` に入り 404 に／TTL sweep／write gate）
- **実 api（`asterism_api.local`）＋実ブラウザで一周**: 実 XRD カードを drop → `POST /api/staging` 200（正規名 `xrd-17961dd6.txt`＝実データセットの source 名と一致）→ 骨格を snapshot に置き **IndexedDB を削除して**リロード → **S4 に直行**、再検査が `staging_id` だけで走り証拠（合流 1 件・行の置き場・候補チップ）が全部出る・AI 相談あり → 「戻ってやり直す」は S2（staging 保持）→ 「ファイルを選び直す」で **staging が 404**（消去）

## 5. 非目標

- **文書（docx/pdf/xml）の staging** — 文書は AI 設計を通らず即データセット化されるので、消える窓が無い。
- **staging の中身を一覧・閲覧する UI** — 下書きは設計セッションの内部状態。必要になれば `_staging/` の管理画面は別件。
- **旧 api への staging 追加要求** — クライアントは 404/503 を黙って従来経路に落とす。
