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

## 4.5 追補（2026-10-05）— 置いた名前を覚える

表のファイル名は `rml:source` と一致しなければならないので、日本語だけの名前は
受け取った時点で英数字の保存名に直る（`価格表.csv` → `source-3637d45e.csv`。
`_sanitize_tabular_name`・[`source-dialect.md`](source-dialect.md)）。直すのは
`raw/` に置く**前**なので、利用者の置いた名前はどこにも残らなかった —— staging の
`raw/`・`meta.json` の `sources`、データセットの `source_files`、設計の `source`、
引っ越しの記録（`id_move.*.source`）はすべて保存名である。残っていたのは、画面が
元の名前から作った種類の表示名（「価格表 の 1 行」・K64）だけで、これは人が
書き換えられ、表示言語で形も変わるので、ファイル名の代わりにはならない。

その結果、人に見せる文に保存名が出ていた（「source-3637d45e.csv: 前の ID を作るのに
使っていた列（番号）が、いまのファイルにありません。」）。

| # | 論点 | 決定 | 理由 |
|---|------|------|------|
| N1 | 何を持つか | `{保存名: 置いた名前}` の対応表を 1 つ。staging の `meta.json` に `names`、データセットの `meta.json` に `source_names` | 保存名・設計・IRI・`source_files` は 1 文字も変えない（`rml:source` との一致は保存名の仕事のまま）。足すのは「人に見せるときの呼び名」だけ |
| N2 | どこで書くか | サーバが元の名前を受け取る入口（`POST /api/staging`・`_persist_source_uploads`）。staging を消費する側（attach・`/api/place/commit`）は staging の `names` を引き継ぎ、数え直し（recount）は保存済みの対応を staging に写す | 元の名前を知っているのは入口だけ。あとから復元はできない |
| N3 | 恒等は持たない | 置いた名前と保存名が同じ組は記録しない | 英数字の名前のデータセットには何も増えない |
| N4 | 入れ直し | 保存名のままの名前で入れ直したとき（見直し・数え直しは保存済みのファイルを写して使う）は、前に覚えた名前を消さない。ファイルの組が変わって `source_files` から消えた名前は落とす | 「前に置いた名前」を知っているのは記録だけ。保存名での入れ直しは、名前について何も新しいことを言っていない |
| N5 | 変換したもの | Excel は派生した各 CSV **と、残しておく原本**（保存名の .xlsx）→ 置いたブックの名前、Word は JATS → 置いた .docx の名前。staging の `names` も、`raw/` の原本と派生した表の両方を持つ | 利用者が置いたのはブックであって、派生 CSV ではない。原本は `source/` に残り、形を直したあとの読み直しや追記で `source_files` に載る —— そのとき原本だけが保存名で出ないように。staging を読む側（置く経路）が見るのは派生した表だけ |
| N5b | 推測は上書きしない | 保存名のまま届き、staging も名前を知らない原本（見直しは保存済みの組を Excel の原本ごと写して変換し直す）から派生したぶんは**推測**として扱い、今回わかった名前・前から覚えている名前を上書きしない。派生したファイル自身が同じ回に届いているとき（＝写しの入れ直し）は、推測そのものをしない | 上書きすると、見直しのたびに「価格表.xlsx」が「source-a5e26419.xlsx」に戻る（テストで再現）。推測をやめないと、この仕組みより前に作ったデータセットに、保存名が「置いた名前」として書き込まれる |
| N6 | 読む側 | 記録そのもの（`id_move`・取り込みの記録）は書き換えず、**返すときに**足す: `GET /api/datasets/{id}/id-move` の各要素に `source_label`、`GET /jobs` の各行に `file_label`。画面は `source_label ?? source` を出す。詳細の「取り込み・公開」タブは meta の `source_names` を直接引く（`placedSourceNames`） | 呼び名の正本を対応表 1 つに保つ。記録に写しを持つと、写しが古くなる |
| N7 | 表示だけに使う | 置いた名前は人に見せる文にだけ出す。ファイルの読み書き・`rml:source` との一致・件数・IRI には保存名を使い続ける | 置いた名前は利用者の入力そのもの（空白・記号・長さに制約が無い）。パスや一致判定に入れない |
| N8 | 同じ名前は 1 回 | ブックとその表は同じ名前で呼ばれるので、一覧・知らせ・取り込みの記録では同じ名前（知らせは同じ文）を 1 回だけ出す。「この名前で置き直してください」と案内する文（追記の注記・名前違いの案内）だけは、変換してできたファイルを保存名のまま出す | 読む人には同じ行が並ぶだけになる。置き直しの案内は、ブックの名前では CSV を置き直せないので、嘘にしない |

**既存のデータセットには付かない。** 元の名前はどこにも残っていないので、さかのぼっては
作れない。もう一度その名前のファイルを置いたときから付く（それまでは保存名のまま出る）。

**やっていないこと**: インスタンス間の書き出し（`exchange.py` の `_META_KEEP`）と見本の入れ替え
（`demo_sample.py`）は `source_names` を運ばない／追記で足したぶん（`appends[].batch_files`・
文書を足していくデータセットの 2 件め以降）は保存名のまま（表を最初に置いたファイルと同じ名前で
足すぶんには、最初の対応がそのまま当たる）／一覧の「N ファイル」は保存名で数えたまま
（Excel は表の数＋原本）／
設計中の画面（「⑤ かたちをたしかめる」で複数ファイルのときのファイル名・詳細モードのルール一覧）は
保存名のまま／IRI の名前空間に入っている `source-3637d45e` は変えない（IRI は公開した瞬間に
動かせなくなる）。

## 5. 非目標

- **文書（docx/pdf/xml）の staging** — 文書は AI 設計を通らず即データセット化されるので、消える窓が無い。
- **staging の中身を一覧・閲覧する UI** — 下書きは設計セッションの内部状態。必要になれば `_staging/` の管理画面は別件。
- **旧 api への staging 追加要求** — クライアントは 404/503 を黙って従来経路に落とす。
