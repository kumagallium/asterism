# Changelog

## [v0.30.3](https://github.com/kumagallium/asterism/compare/v0.30.2...v0.30.3) - 2026-08-31

- chore(ci): tagpr ブランチの PR では run を作らない by @kumagallium in https://github.com/kumagallium/asterism/pull/478
- feat: 本番デプロイをプル型（Dewy）で自動化する PoC — api 実機一周 by @kumagallium in https://github.com/kumagallium/asterism/pull/475

## [v0.30.2](https://github.com/kumagallium/asterism/compare/v0.30.1...v0.30.2) - 2026-08-31

- fix(ui): 同じ列をキーにした双子の種類を、図と選択肢から消さない by @kumagallium in https://github.com/kumagallium/asterism/pull/476

## [v0.30.1](https://github.com/kumagallium/asterism/compare/v0.30.0...v0.30.1) - 2026-08-31

- docs: ADR local-first-distribution §7 を完了状態に更新 — 署名/公証/updater は完了済 by @kumagallium in https://github.com/kumagallium/asterism/pull/472

## [v0.30.0](https://github.com/kumagallium/asterism/compare/v0.29.0...v0.30.0) - 2026-08-31

- fix(step0/ui): 行の種類しか無い骨格を、言う・直せるようにする（missing_card_kind） by @kumagallium in https://github.com/kumagallium/asterism/pull/471

## [v0.29.0](https://github.com/kumagallium/asterism/compare/v0.28.0...v0.29.0) - 2026-08-31

- chore(ui/api): 材料科学固有の例を抜き、分野特化の境界を ADR にする by @kumagallium in https://github.com/kumagallium/asterism/pull/469

## [v0.28.0](https://github.com/kumagallium/asterism/compare/v0.27.0...v0.28.0) - 2026-08-31

- docs(adr): 種類を分ける — 重複判定の絞り込みと、相談チャットからの提案 by @kumagallium in https://github.com/kumagallium/asterism/pull/466
- feat(ui/api): 種類を分ける — 重複判定の絞り込みと、相談チャットからの提案（D1〜D4） by @kumagallium in https://github.com/kumagallium/asterism/pull/467

## [v0.27.0](https://github.com/kumagallium/asterism/compare/v0.26.0...v0.27.0) - 2026-08-29

- fix(ui): 図に触ると揺れるのを直し、押せなくなっていた節を通す by @kumagallium in https://github.com/kumagallium/asterism/pull/464

## [v0.26.0](https://github.com/kumagallium/asterism/compare/v0.25.0...v0.26.0) - 2026-08-29

- docs(manual): かんたんモードの 6 ステップを実 AI で一周して全画面を撮る by @kumagallium in https://github.com/kumagallium/asterism/pull/460
- feat(kantan): 形の図を React Flow にする — 触れる図にして④と⑤で同じ部品を使う by @kumagallium in https://github.com/kumagallium/asterism/pull/459
- test(manual): 画面名の陳腐化と生成物の未更新を、機械で捕まえる by @kumagallium in https://github.com/kumagallium/asterism/pull/462
- feat(ui): 図をぜんぶ React Flow に揃える — 同じものを 2 つの絵の言葉で見せない by @kumagallium in https://github.com/kumagallium/asterism/pull/463

## [v0.25.0](https://github.com/kumagallium/asterism/compare/v0.24.0...v0.25.0) - 2026-08-29

- docs(manual): 実画面のスクリーンショットを入れる — データ登録の流れが見えるように by @kumagallium in https://github.com/kumagallium/asterism/pull/456
- feat(kantan): ⑤に「できあがった形」を出し、AI の名づけを標準の綴りに寄せる by @kumagallium in https://github.com/kumagallium/asterism/pull/458

## [v0.23.1](https://github.com/kumagallium/asterism/compare/v0.23.0...v0.23.1) - 2026-08-28

- feat(kantan): 重複列を「どちらに残しますか」でその場で決める — AI に投げても解けない判断を人に渡す by @kumagallium in https://github.com/kumagallium/asterism/pull/449
- feat(kantan): 「AI にもう一度考えさせる」で S4 の編集を捨てない — 作り直しではなく修理にする by @kumagallium in https://github.com/kumagallium/asterism/pull/436
- feat(ui): かんたん層の文に強弱をつける — 判断・操作・補足・警告を同じ声で並べない by @kumagallium in https://github.com/kumagallium/asterism/pull/429
- refactor: 生成 ingester を撤去する — 実行しないコードは、書かせない by @kumagallium in https://github.com/kumagallium/asterism/pull/452
- feat(kantan): 意味を先に、ID を後に — かんたんモードの順序を入れ替える by @kumagallium in https://github.com/kumagallium/asterism/pull/453
- docs(manual): つながったデータの入門 2 章を足し、マニュアルを GitHub Pages で公開する by @kumagallium in https://github.com/kumagallium/asterism/pull/454

## [v0.23.0](https://github.com/kumagallium/asterism/compare/v0.22.4...v0.23.0) - 2026-08-28

- feat: アプリ本体が MCP を出す（/mcp）＋設定から各 AI へ配る by @kumagallium in https://github.com/kumagallium/asterism/pull/447

## [v0.22.4](https://github.com/kumagallium/asterism/compare/v0.22.3...v0.22.4) - 2026-08-27

- fix(kantan): 公開後の「データの数えかたに戻る」を、行数を読めない画面でも出す by @kumagallium in https://github.com/kumagallium/asterism/pull/441
- fix(step0): 連続値の列を ID 候補から外す — 幅広い表で構造解析が LLM に渡らなくなっていた by @kumagallium in https://github.com/kumagallium/asterism/pull/446
- feat(datasets/mp): starrydata の母相を MP API で解決し 2,402 材料の実データに — 空間群を持つ唯一のソース by @kumagallium in https://github.com/kumagallium/asterism/pull/444

## [v0.22.3](https://github.com/kumagallium/asterism/compare/v0.22.2...v0.22.3) - 2026-08-27

- fix(desktop): 同梱コマンドがビルド機のパスを指したまま配布されていた by @kumagallium in https://github.com/kumagallium/asterism/pull/442

## [v0.22.2](https://github.com/kumagallium/asterism/compare/v0.22.1...v0.22.2) - 2026-08-27

- feat(ui): 右下のボタンを「AI に相談」にする — 誰に相談するのかを押す前に示す by @kumagallium in https://github.com/kumagallium/asterism/pull/427
- feat(desktop): バックエンドが止まったら気づかせて、その場で再起動できるようにする by @kumagallium in https://github.com/kumagallium/asterism/pull/430
- fix(local): SIGTERM で Oxigraph / demo-agent が孤児として残るのを直す by @kumagallium in https://github.com/kumagallium/asterism/pull/431
- fix(log): backend.log を読めるものにする — 死活確認のノイズ・世代・インスタンスの境目 by @kumagallium in https://github.com/kumagallium/asterism/pull/433
- chore(ci): ui のユニットテストを CI で走らせる by @kumagallium in https://github.com/kumagallium/asterism/pull/432
- feat(mcp): どのデータセットがあるかを機械が答える — find_datasets と、届いていなかった registry by @kumagallium in https://github.com/kumagallium/asterism/pull/438
- feat: 公開したあとに「データの数えかた」を直せるようにする — 前の ID は引っ越し先を指す by @kumagallium in https://github.com/kumagallium/asterism/pull/437
- feat(tier0): 単一 object のドットパス取得と Python リテラル repr の受理 — pandas の dict 列が展開できなかった by @kumagallium in https://github.com/kumagallium/asterism/pull/439
- docs: ID の引っ越しを本番デプロイ済みに更新 — 既存データ無傷を確認 by @kumagallium in https://github.com/kumagallium/asterism/pull/440
- fix(infra): Crucible から素直に deploy できるようにする — 内部ポートと subdir の mcp.json by @kumagallium in https://github.com/kumagallium/asterism/pull/434
- fix(prod): ホームの「事実の数」が出ない — api の露出プロファイルを開ける by @kumagallium in https://github.com/kumagallium/asterism/pull/435

## [v0.22.1](https://github.com/kumagallium/asterism/compare/v0.22.0...v0.22.1) - 2026-08-26

- fix(infra/desktop): 配布物に manual/ を同梱 — 相談チャットの知識源の欠落 by @kumagallium in https://github.com/kumagallium/asterism/pull/423
- fix(infra): 本番 api の manual を ASTERISM_MANUAL_DIR で明示指定 by @kumagallium in https://github.com/kumagallium/asterism/pull/425
- fix: 検出できていた文字コードが、設計に記録されるまでに消えていた by @kumagallium in https://github.com/kumagallium/asterism/pull/426

## [v0.22.0](https://github.com/kumagallium/asterism/compare/v0.21.2...v0.22.0) - 2026-08-26

- docs(manual): ユーザー向けマニュアルを全機能に拡充する — 9 章 + 実画面スクリーンショット by @kumagallium in https://github.com/kumagallium/asterism/pull/412
- feat(ui/settings): 設定「このアプリ」から更新を入れられるようにする — 「あとで」が案内する先にボタンが無かった by @kumagallium in https://github.com/kumagallium/asterism/pull/421
- feat(step0): label 取りこぼし行だけを 1 回聞き直す — 「読み取った意味」のブレ対策 by @kumagallium in https://github.com/kumagallium/asterism/pull/422

## [v0.21.2](https://github.com/kumagallium/asterism/compare/v0.21.1...v0.21.2) - 2026-08-26

- fix(ui/kantan): 自動修正のたびに下書きが増殖するのを止める — ④の判断済み表示と見直しバナーも改善 by @kumagallium in https://github.com/kumagallium/asterism/pull/417
- fix(ui/kantan): 見直しの判断反映後に④を再表示せず⑤ためすへ直行する by @kumagallium in https://github.com/kumagallium/asterism/pull/419

## [v0.21.1](https://github.com/kumagallium/asterism/compare/v0.21.0...v0.21.1) - 2026-08-25

- fix(step0/api/ui): 「No」列を持つ設計で意味の保存が消える — YAML bool 化を根絶 by @kumagallium in https://github.com/kumagallium/asterism/pull/415

## [v0.21.0](https://github.com/kumagallium/asterism/compare/v0.20.0...v0.21.0) - 2026-08-25

- feat(ui/api): 相談 AI に「いま画面に見えている列」を自動添付する by @kumagallium in https://github.com/kumagallium/asterism/pull/413

## [v0.20.0](https://github.com/kumagallium/asterism/compare/v0.19.0...v0.20.0) - 2026-08-25

- feat(ui/api): 設計中に相談できる AI チャットドロワーを付ける by @kumagallium in https://github.com/kumagallium/asterism/pull/409

## [v0.19.0](https://github.com/kumagallium/asterism/compare/v0.18.0...v0.19.0) - 2026-08-25

- feat(kantan): 「まだ取り込んでいない項目」に一括操作を付ける by @kumagallium in https://github.com/kumagallium/asterism/pull/408
- fix(kantan): 単位欄の型語は機械が掃き、直しの連打を待たせない by @kumagallium in https://github.com/kumagallium/asterism/pull/402

## [v0.18.0](https://github.com/kumagallium/asterism/compare/v0.17.5...v0.18.0) - 2026-08-25

- fix(settings): 「このサーバ」タブの生キー表示を解消 — 消えた serverKeys ラベルを復元 by @kumagallium in https://github.com/kumagallium/asterism/pull/403
- chore(ui): i18n キーの「参照はあるが定義が無い」を lint と CI で検出する by @kumagallium in https://github.com/kumagallium/asterism/pull/405
- feat(kantan): 「項目の意味」を AI 再試行から人間の判断へ分離する by @kumagallium in https://github.com/kumagallium/asterism/pull/406
- feat(step0/ui): 危険な ID は人に見せる前に機械が置き換える — 骨格ゲートの安全キー差し替え by @kumagallium in https://github.com/kumagallium/asterism/pull/407

## [v0.17.5](https://github.com/kumagallium/asterism/compare/v0.17.4...v0.17.5) - 2026-08-21

- refactor(gallery): タブを「答える問い」で切り直し、直す入口をカードに出す by @kumagallium in https://github.com/kumagallium/asterism/pull/398
- feat(grounding): 「この列は何を測っているか」を標準に接地する by @kumagallium in https://github.com/kumagallium/asterism/pull/400

## [v0.17.4](https://github.com/kumagallium/asterism/compare/v0.17.3...v0.17.4) - 2026-08-20

- 欠陥の停止カードは機械が押す — 人間のクリックを無くす by @kumagallium in https://github.com/kumagallium/asterism/pull/387
- docs: appdata の検証レポートを閉じる — 配布版で最後の経路まで一周した by @kumagallium in https://github.com/kumagallium/asterism/pull/391
- fix(ingest): regex_extract のエンジン不在を無言の空文字から例外へ — 列が丸ごと消えるのを止める by @kumagallium in https://github.com/kumagallium/asterism/pull/394
- chore(ci): ingest のテストを substrate extra ありで回す — 実経路の e2e が一度も走っていなかった by @kumagallium in https://github.com/kumagallium/asterism/pull/396
- fix(ingest): lookup も「読めない表」を無言の "" から例外へ — 同じ silent drop の二つ目の扉 by @kumagallium in https://github.com/kumagallium/asterism/pull/395
- feat: 全画面を「かんたん」にする — 16 面の網羅監査と 605 件の修正 by @kumagallium in https://github.com/kumagallium/asterism/pull/389
- 保存先を変えたらデータも移す — 起動時、サイドカーを立てる前に by @kumagallium in https://github.com/kumagallium/asterism/pull/392
- feat(kantan): 標準のことばに合わせる入口と、単位が標準に届いたかの表示 by @kumagallium in https://github.com/kumagallium/asterism/pull/393
- fix(settings): 初回の AI 設定から、頼む相手のいない依頼を消す by @kumagallium in https://github.com/kumagallium/asterism/pull/397
- chore: リポジトリ直下の残骸ファイル `-volnamex` を削除 by @kumagallium in https://github.com/kumagallium/asterism/pull/390

## [v0.17.3](https://github.com/kumagallium/asterism/compare/v0.17.2...v0.17.3) - 2026-08-18

- デスクトップは手元のディスクに保存する — チャットと設定をブラウザから出す by @kumagallium in https://github.com/kumagallium/asterism/pull/385

## [v0.17.2](https://github.com/kumagallium/asterism/compare/v0.17.1...v0.17.2) - 2026-08-18

- .dmg 作成の "Resource busy" でリリースが資産ゼロになるのを止める by @kumagallium in https://github.com/kumagallium/asterism/pull/382
- ループが「値が 1 つも出ない設計」を合格にしていた — 検査を結果基準に変える by @kumagallium in https://github.com/kumagallium/asterism/pull/384

## [v0.17.1](https://github.com/kumagallium/asterism/compare/v0.17.0...v0.17.1) - 2026-08-18

- 「AI に直してもらう」が 5 回押しても直らない 4 つの構造欠陥を潰す by @kumagallium in https://github.com/kumagallium/asterism/pull/379
- YAML 1.2 化の巻き添えで `collapse: no` / `fallback: yes` が新たな設計問題になっていた by @kumagallium in https://github.com/kumagallium/asterism/pull/381

## [v0.17.0](https://github.com/kumagallium/asterism/compare/v0.16.0...v0.17.0) - 2026-08-17

- feat(desktop): 更新を画面上部のバナーからワンクリックに — Graphium と同じ形 by @kumagallium in https://github.com/kumagallium/asterism/pull/377

## [v0.16.0](https://github.com/kumagallium/asterism/compare/v0.15.0...v0.16.0) - 2026-08-17

- feat(ui): ソースファイルをリロード越しに保持する — IndexedDB に置く by @kumagallium in https://github.com/kumagallium/asterism/pull/369
- feat: Ask に最後の砦 ＋ データの事実を毎ラウンド再主張する by @kumagallium in https://github.com/kumagallium/asterism/pull/372
- feat(step0/ui): 共有概念を別の種類に分ける — 実測を見せた先に押すものを置く by @kumagallium in https://github.com/kumagallium/asterism/pull/374
- feat: 設計中のソースをサーバに置く — ドロップした瞬間から by @kumagallium in https://github.com/kumagallium/asterism/pull/376

## [v0.15.0](https://github.com/kumagallium/asterism/compare/v0.14.6...v0.15.0) - 2026-08-17

- 空の入れ物を検出 — 行ごとの種類が、行の値を 1 つも持たない (ADR G14) by @kumagallium in https://github.com/kumagallium/asterism/pull/370
- feat(ask): 質問する をチャットにする — 履歴一覧＋会話＋入力欄、スレッドは手元・エージェントは無状態 by @kumagallium in https://github.com/kumagallium/asterism/pull/373

## [v0.14.6](https://github.com/kumagallium/asterism/compare/v0.14.5...v0.14.6) - 2026-08-16

- 「AI に直してもらう」連打の 2 段目の真因 — splice 不能と既知修正の LLM 依存 by @kumagallium in https://github.com/kumagallium/asterism/pull/367

## [v0.14.5](https://github.com/kumagallium/asterism/compare/v0.14.4...v0.14.5) - 2026-08-16

- 「確かめる」のやり直しを機械が回す — トラップ検証を自己修正ループのオラクルに合流 by @kumagallium in https://github.com/kumagallium/asterism/pull/364
- 列の帰属を「薄さ」ではなく帯と境界行で示す（親側にも行き先を出す） by @kumagallium in https://github.com/kumagallium/asterism/pull/365

## [v0.14.4](https://github.com/kumagallium/asterism/compare/v0.14.3...v0.14.4) - 2026-08-15

- feat: 数値列に型を付ける — 型のない数は SPARQL が文字列として比較する by @kumagallium in https://github.com/kumagallium/asterism/pull/362

## [v0.14.3](https://github.com/kumagallium/asterism/compare/v0.14.2...v0.14.3) - 2026-08-14

- feat(ui): 骨格ゲートを可逆にする／使えない機能は理由ごと出す by @kumagallium in https://github.com/kumagallium/asterism/pull/360

## [v0.14.2](https://github.com/kumagallium/asterism/compare/v0.14.1...v0.14.2) - 2026-08-14

- feat(step0/ui): 行の置き場が無いことを検出して 1 クリックで直す／ゲートを読ませない形に by @kumagallium in https://github.com/kumagallium/asterism/pull/358

## [v0.14.1](https://github.com/kumagallium/asterism/compare/v0.14.0...v0.14.1) - 2026-08-14

- feat(step0/ui): 列の所有権と成長プレビュー — 「この列は誰のもの」「次の1枚で何が起きる」 by @kumagallium in https://github.com/kumagallium/asterism/pull/352
- ci(desktop): 更新フィードを Pages へ — リリース直後の 502 窓を閉じる by @kumagallium in https://github.com/kumagallium/asterism/pull/356
- feat(desktop): 更新フィードを Pages の固定 URL に切り替える by @kumagallium in https://github.com/kumagallium/asterism/pull/357

## [v0.14.0](https://github.com/kumagallium/asterism/compare/v0.13.2...v0.14.0) - 2026-08-14

- 取り込んだ実データが設計どおりか検査する（shape 検査） by @kumagallium in https://github.com/kumagallium/asterism/pull/351
- docs(shapes): shape 検査の未確認 2 点を実測で埋める by @kumagallium in https://github.com/kumagallium/asterism/pull/354

## [v0.13.2](https://github.com/kumagallium/asterism/compare/v0.13.1...v0.13.2) - 2026-08-14

- ci(tagpr): actions:write を付与 — desktop-release の dispatch 403 を修正 by @kumagallium in https://github.com/kumagallium/asterism/pull/344
- feat(desktop): 同梱バイナリを deep-sign して Apple 公証を通す（app + dmg） by @kumagallium in https://github.com/kumagallium/asterism/pull/346
- docs(roadmap): デスクトップの署名/公証完了を反映 by @kumagallium in https://github.com/kumagallium/asterism/pull/347
- ci(desktop): PR チェックは常に無署名 — 署名 secrets を release 専用に閉じ込める by @kumagallium in https://github.com/kumagallium/asterism/pull/349
- fix(desktop): 固定ポートで設定消失を根治 — 再起動のたびに登録 AI が消えるバグ by @kumagallium in https://github.com/kumagallium/asterism/pull/348
- feat(settings): 設定画面を Graphium の形へ — 接続先の再利用と「このアプリ」タブ by @kumagallium in https://github.com/kumagallium/asterism/pull/350

## [v0.13.1](https://github.com/kumagallium/asterism/compare/v0.13.0...v0.13.1) - 2026-08-13

- ci(tagpr): リリース発番時に Desktop Release を明示 dispatch by @kumagallium in https://github.com/kumagallium/asterism/pull/342

## [v0.13.0](https://github.com/kumagallium/asterism/compare/v0.12.0...v0.13.0) - 2026-08-13

- feat(desktop): デスクトップシェル v1 — Tauri v2 でネイティブアプリ化（Phase 2） by @kumagallium in https://github.com/kumagallium/asterism/pull/326
- feat(desktop): 自己完結バンドル — repo/Python/Docker 不要の .app（Phase 2 v2） by @kumagallium in https://github.com/kumagallium/asterism/pull/328
- ci(desktop): Desktop Build workflow — desktop/** PR と dispatch で .app/.dmg by @kumagallium in https://github.com/kumagallium/asterism/pull/329
- ci(desktop): ui は npm install に — repo 規約に合わせる by @kumagallium in https://github.com/kumagallium/asterism/pull/330
- ci(desktop): 署名 env は secrets 実在時のみ注入 by @kumagallium in https://github.com/kumagallium/asterism/pull/332
- fix(api): togomcp の DB 名正規化に合わせ配信名を canonical 化 (discovery 消失の根治) by @kumagallium in https://github.com/kumagallium/asterism/pull/331
- feat(api): snapshot exchange v1 — インスタンス間のデータセット受け渡し（Phase 3） by @kumagallium in https://github.com/kumagallium/asterism/pull/333
- fix(desktop): アプリアイコンが白紙 — qlmanage の SVG 非拡大レンダを根治 by @kumagallium in https://github.com/kumagallium/asterism/pull/334
- fix(desktop): バンドルの editable 混入根治 — 配布 .dmg の起動即死を修正 by @kumagallium in https://github.com/kumagallium/asterism/pull/335
- fix(ui): 空状態の「新しいデータセット」をグリッドと同じ追加タイルに by @kumagallium in https://github.com/kumagallium/asterism/pull/337
- fix(ui): 初回起動時にデフォルトモデル（Claude Opus 4.7）をシードしない by @kumagallium in https://github.com/kumagallium/asterism/pull/336
- feat(desktop): 自動アップデート (native) — 更新確認/DL/再起動を Rust 側で完結 by @kumagallium in https://github.com/kumagallium/asterism/pull/338
- ci(desktop): Desktop Release workflow — 署名付き .app.tar.gz + latest.json 生成 by @kumagallium in https://github.com/kumagallium/asterism/pull/339
- docs(roadmap): デスクトップ自動アップデート実装を反映 by @kumagallium in https://github.com/kumagallium/asterism/pull/340
- feat(step0,ui): 骨格ゲートに帰結プレビュー — エンティティカード・潰れ分類・参照リスク (K14) by @kumagallium in https://github.com/kumagallium/asterism/pull/341

## [v0.12.0](https://github.com/kumagallium/asterism/compare/v0.11.0...v0.12.0) - 2026-08-12

- fix(ui): かんたん「弱さ」カードの「AI に直してもらう」が無反応 — kind ガード漏れ by @kumagallium in https://github.com/kumagallium/asterism/pull/319
- fix(build): hatchling 最新のプロジェクト外 readme 拒否で CI が全滅 — readme 指定を削除 by @kumagallium in https://github.com/kumagallium/asterism/pull/324
- feat(step0): T10 — MIE 例示 SPARQL を実行検証 (parse は常時・--draft-ttl で 0 行検出) by @kumagallium in https://github.com/kumagallium/asterism/pull/321
- feat(api): promote 時に MIE を togomcp カタログへ決定論投影 (ASTERISM_TOGOMCP_DIR opt-in) by @kumagallium in https://github.com/kumagallium/asterism/pull/322
- feat(api): ローカルモード asterism-local — Docker なし 1 コマンドのループバック起動 by @kumagallium in https://github.com/kumagallium/asterism/pull/323
- feat(api): ローカルモード追補 — Ask 実接続（demo-agent 子プロセス+同一オリジン /demo 中継） by @kumagallium in https://github.com/kumagallium/asterism/pull/325

## [v0.11.0](https://github.com/kumagallium/asterism/compare/v0.10.0...v0.11.0) - 2026-07-24

- fix(ui/caddy): デプロイ跨ぎ stale chunk で図が「表示できません」に化ける事故を根治 by @kumagallium in https://github.com/kumagallium/asterism/pull/312
- 列の二重転記 advisory＋実データによる帰属裁定（per-map 丸写しの検出と決定論の作業指示書化） by @kumagallium in https://github.com/kumagallium/asterism/pull/311
- 設計図の対応表が api 経路で失われる事故を根治 — diagram.md の形を 1 関数に集約 by @kumagallium in https://github.com/kumagallium/asterism/pull/314
- 本番デプロイ手順の実態合わせ — 確認コマンドが「偽障害」を作る問題の除去 by @kumagallium in https://github.com/kumagallium/asterism/pull/315
- fix(ui): クラス図の属性行が折り返して崩れる二重の原因を根治 by @kumagallium in https://github.com/kumagallium/asterism/pull/316
- feat(api/ui): 設計の「弱さ」を欠陥と分けて、消えない場所に平易な日本語で出す by @kumagallium in https://github.com/kumagallium/asterism/pull/317
- feat: つながりを「候補を1つえらぶ」だけにする（LLM 不要の候補検出＋作成導線の集約） by @kumagallium in https://github.com/kumagallium/asterism/pull/318

## [v0.10.0](https://github.com/kumagallium/asterism/compare/v0.9.0...v0.10.0) - 2026-07-23

- feat(dialect): preamble モード keyvalue_cells — ZEM 系「key=value セル」メタ行を列化 by @kumagallium in https://github.com/kumagallium/asterism/pull/302
- feat(step0/ui): 骨格ゲートに「クラス名=数値列名」警告 — ZEM 誤命名を設計時に前倒し検出 by @kumagallium in https://github.com/kumagallium/asterism/pull/304
- feat(ui): かんたん S2/S4 改善 — プリアンブル実物・AI にもう一度考えさせる・骨格図 by @kumagallium in https://github.com/kumagallium/asterism/pull/306
- fix(kantan): コンパイル不能設計の S5 素通りを停止＋RML なし ingest を平易な 422 に by @kumagallium in https://github.com/kumagallium/asterism/pull/305
- fix(validate): 接続判定が transform 済み template を見えず false DISCONNECTED で修正ループが収束不能 by @kumagallium in https://github.com/kumagallium/asterism/pull/307
- fix(step0): 弱モデルの unit 暴走をサニタイズ+値ソース無し行に貼るだけ誘導 by @kumagallium in https://github.com/kumagallium/asterism/pull/308
- chore: 参照ゼロの残骸を削除（step0/dogfood と word-pandoc スパイク） by @kumagallium in https://github.com/kumagallium/asterism/pull/309
- 命名の決定論化 — prefix を判断から消す (ADR K13) by @kumagallium in https://github.com/kumagallium/asterism/pull/310

## [v0.9.0](https://github.com/kumagallium/asterism/compare/v0.8.0...v0.9.0) - 2026-07-23

- feat(ui/api): かんたんモード S7-S9 — ためす/公開/できあがり（ウィザード完結） by @kumagallium in https://github.com/kumagallium/asterism/pull/296
- feat(ui): K11 翻訳テーブル本体 — T1-T9 の平易文を design 停止カードに表示 by @kumagallium in https://github.com/kumagallium/asterism/pull/298
- feat(ui): かんたん見直し — カタログ「見直す」を S6 から再確認で開く by @kumagallium in https://github.com/kumagallium/asterism/pull/299
- feat(ui): 共有画面を K4 準拠化 — 見る動線の 120 箇所を かんたんのことばに統一 by @kumagallium in https://github.com/kumagallium/asterism/pull/300
- feat(step0): 設計図(diagram.md)を Mapping IR から決定論コンパイル — 空のクラス箱問題の根治 by @kumagallium in https://github.com/kumagallium/asterism/pull/301

## [v0.8.0](https://github.com/kumagallium/asterism/compare/v0.7.0...v0.8.0) - 2026-07-22

- docs(adr): かんたん/詳細 二層モード — データセット追加 UX 再設計の決定記録 by @kumagallium in https://github.com/kumagallium/asterism/pull/285
- feat(step0/api/ui): Mapping IR に label/unit — レビュー画面「列の意味」の出所 by @kumagallium in https://github.com/kumagallium/asterism/pull/286
- feat(api/ingest/ui): .xlsx 受理 — サーバ側で決定論 CSV 変換（かんたんモード K6） by @kumagallium in https://github.com/kumagallium/asterism/pull/287
- feat(ui): かんたんモード S1-S4 — 二層モードの骨組み（かんたん既定・詳細に現行全機能温存） by @kumagallium in https://github.com/kumagallium/asterism/pull/288
- feat(ui/api/step0): かんたんモード S5/S6 — 自動連結+列の意味ゲート+測定値キー注意 by @kumagallium in https://github.com/kumagallium/asterism/pull/290
- feat(ui): かんたん停止カードに「AI に直してもらう」— 修正ループをかんたん内で完結 by @kumagallium in https://github.com/kumagallium/asterism/pull/291
- fix(llm-settings): モデル選択で明示エンドポイントを尊重＋取得ボタンの無効化を解消 by @kumagallium in https://github.com/kumagallium/asterism/pull/292
- feat(ui): かんたん停止カードに脱出口+エラー平易化（実dogfoodで詰んだ2件） by @kumagallium in https://github.com/kumagallium/asterism/pull/293
- feat(step0/api): 列名括弧の単位を決定論抽出して IR に自動補完（S6 の単位欄を確実に） by @kumagallium in https://github.com/kumagallium/asterism/pull/294
- fix(step0/api): 弱モデルの transform 発明を封じる（per-map 自己修正の配線＋修復家系） by @kumagallium in https://github.com/kumagallium/asterism/pull/295

## [v0.7.0](https://github.com/kumagallium/asterism/compare/v0.6.0...v0.7.0) - 2026-07-14

- feat(ui): v2 UX redesign — visibility, object-axis IA, dataset tabs, ScreenGround/Map by @kumagallium in https://github.com/kumagallium/asterism/pull/232
- feat: マルチプロバイダ LLM + 設定UI + API使用量計測 by @kumagallium in https://github.com/kumagallium/asterism/pull/234
- infra: 本番デプロイ構成（Private 完結・1箱バンドル） by @kumagallium in https://github.com/kumagallium/asterism/pull/235
- infra(caddy): 認証済みリクエストに書き込みトークン注入（#235 follow-up） by @kumagallium in https://github.com/kumagallium/asterism/pull/236
- 設計品質ループの改善を main へ追従（検証/再設計/自動修正/RML 堅牢化） by @kumagallium in https://github.com/kumagallium/asterism/pull/237
- 設計検証を新規設計でも効かせる（attach 後の read-only 検証エンドポイント） by @kumagallium in https://github.com/kumagallium/asterism/pull/238
- propose 自己修正ループ（設計品質の底上げ・TODO ④） by @kumagallium in https://github.com/kumagallium/asterism/pull/239
- feat(settings): モデルピッカー — プロバイダから利用可能モデル一覧を取得 by @kumagallium in https://github.com/kumagallium/asterism/pull/240
- feat(settings): サーバ側運用者 LLM キー（opt-in）＋ APIキー欄を endpoint 直下へ by @kumagallium in https://github.com/kumagallium/asterism/pull/242
- fix(settings): モデルピッカーで一覧から選び直せない問題を修正 by @kumagallium in https://github.com/kumagallium/asterism/pull/243
- fix(workbench): リトライのたびに重複データセットが増えるバグ — 初回保存の id を採用して in-place 更新に by @kumagallium in https://github.com/kumagallium/asterism/pull/241
- feat(settings): 共有サーバ側 LLM キーを UI から登録 by @kumagallium in https://github.com/kumagallium/asterism/pull/245
- fix(append): make retried appends idempotent via a content-derived batch id by @kumagallium in https://github.com/kumagallium/asterism/pull/246
- feat(propose/refine): follow the UI language for proposal prose by @kumagallium in https://github.com/kumagallium/asterism/pull/244
- fix(ingest): reclaim orphaned version graphs (unbounded storage leak) by @kumagallium in https://github.com/kumagallium/asterism/pull/248
- feat(tool/crosswalk propose): follow the UI language for draft prose by @kumagallium in https://github.com/kumagallium/asterism/pull/247
- fix(documents): adopt the created id so a failed doc upload retries in place by @kumagallium in https://github.com/kumagallium/asterism/pull/249
- feat(jobs/llm): cancellable propose jobs (timeout + liveness) and weak-model robustness by @kumagallium in https://github.com/kumagallium/asterism/pull/250
- feat(llm): stream OpenAI-compatible completions by default (fix provider 504 on long thinking-model generations) by @kumagallium in https://github.com/kumagallium/asterism/pull/251
- Mapping IR: propose の §9 を決定論 RML コンパイルに全面移行（E 案） by @kumagallium in https://github.com/kumagallium/asterism/pull/252
- fix: mapping spec 併存時の迷子 turtle フェンスで保存が誤ブロックされる by @kumagallium in https://github.com/kumagallium/asterism/pull/253
- fix: function: str(型キャスト発明)に的を射た修正誘導を出す by @kumagallium in https://github.com/kumagallium/asterism/pull/254
- Mapping IR Phase 2a: guided JSON + §9 外科的修復 by @kumagallium in https://github.com/kumagallium/asterism/pull/255
- feat(query-tools): 保存時 lint ゲート+寛容ロード+実行エラーの原因開示 by @kumagallium in https://github.com/kumagallium/asterism/pull/256
- feat(ask): starrydata 優遇の撤去 — 全データセットが宣言ツールで平等にルーティング by @kumagallium in https://github.com/kumagallium/asterism/pull/257
- feat(tool-propose): RML 閉集合オラクル+決定論の自己修正ループ by @kumagallium in https://github.com/kumagallium/asterism/pull/260
- feat(design-quality): 孤立エンティティ advisory+propose に質問可能性の設計原則 by @kumagallium in https://github.com/kumagallium/asterism/pull/259
- fix(validate): 壊れた MIE YAML を 500 でなく検証 finding に by @kumagallium in https://github.com/kumagallium/asterism/pull/261
- feat(validate): 列エラーに「リンクは子側から」の方向ヒント+propose に方向規則 by @kumagallium in https://github.com/kumagallium/asterism/pull/262
- feat(validate): 孤立 advisory を作業指示書に — join キー候補列挙+削除逃げ防止 by @kumagallium in https://github.com/kumagallium/asterism/pull/263
- feat(validate): rr:constant 内の {placeholder} を取り込み前に 422 で拒否 by @kumagallium in https://github.com/kumagallium/asterism/pull/264
- fix(substrate): {__run_id__} を rr:template 以外(constant/IRI)でも置換 by @kumagallium in https://github.com/kumagallium/asterism/pull/265
- feat(validate): 未マップ列の review notes(materialize 表示・人間判断層) by @kumagallium in https://github.com/kumagallium/asterism/pull/266
- fix(validate/ui): guard against Mermaid diagram.md that fails to parse by @kumagallium in https://github.com/kumagallium/asterism/pull/267
- feat(tools): 保存/下書き時の advisory dry-run — 0行ツール族を保存時に可視化 by @kumagallium in https://github.com/kumagallium/asterism/pull/269
- Mapping IR Phase 2b: round-0 段階分割 + ウィザード UI（骨格の早期人間ゲート） by @kumagallium in https://github.com/kumagallium/asterism/pull/268
- fix(ui): AI の classDiagram 方言(A -- label --> B)を描画前に正規化 by @kumagallium in https://github.com/kumagallium/asterism/pull/270
- feat(catalog): 取り込みルール（生成物）の人間可読化 — 投影・原文ビューア・再設計履歴 by @kumagallium in https://github.com/kumagallium/asterism/pull/271
- UI/UX 網羅監査に基づく一括改善（特異度リーク恒久修正・書き込みトークンUI・a11y・用語統一） by @kumagallium in https://github.com/kumagallium/asterism/pull/272
- feat: source dialect layer — legacy instrument files ingest as-is by @kumagallium in https://github.com/kumagallium/asterism/pull/273
- UI: hash ルーティング + 一覧⇄詳細の状態保持 + フォーカストラップ + ピルコントラスト by @kumagallium in https://github.com/kumagallium/asterism/pull/274
- UI 監査フォローアップ2: faint 可読性 / 作業状態保持 / カード nested-interactive 解消 by @kumagallium in https://github.com/kumagallium/asterism/pull/275
- feat(dialect): append support + wizard read-settings for legacy instrument files by @kumagallium in https://github.com/kumagallium/asterism/pull/276
- feat(dialect): capture header/preamble metadata via broadcast by @kumagallium in https://github.com/kumagallium/asterism/pull/277
- fix(rml-summary): 取り込みルール投影で rml:constant（新RML名前空間）を認識 by @kumagallium in https://github.com/kumagallium/asterism/pull/278
- feat: ingest ジョブの協調キャンセル + リロード復旧 + アクティビティ記録（監査④⑦） by @kumagallium in https://github.com/kumagallium/asterism/pull/279
- feat(skeleton-gate): 骨格ゲートに「データの証拠」— 主キーを研究者が判断できる形へ by @kumagallium in https://github.com/kumagallium/asterism/pull/280
- feat(tools): serve only the catalog's datasets by default — bundled examples opt-in by @kumagallium in https://github.com/kumagallium/asterism/pull/281
- feat(validate): every failing trap now issues a deterministic fix recipe by @kumagallium in https://github.com/kumagallium/asterism/pull/282
- feat: instance IRI base — 新規設計データセットの名前空間をインスタンス所有に(example.org 根絶+404 説明ページ) by @kumagallium in https://github.com/kumagallium/asterism/pull/283
- feat: IRI dereference Phase 2 — /describe(引用 IRI を開くと公開データが返る・ブラウザで辿れる) by @kumagallium in https://github.com/kumagallium/asterism/pull/284

## [v0.6.0](https://github.com/kumagallium/asterism/compare/v0.5.0...v0.6.0) - 2026-06-16

- feat(documents): ingest born-digital PDF via an isolated Docling sidecar by @kumagallium in https://github.com/kumagallium/asterism/pull/227
- feat(documents,catalog): multi-file document upload + dataset rename by @kumagallium in https://github.com/kumagallium/asterism/pull/231

## [v0.5.0](https://github.com/kumagallium/asterism/compare/v0.4.0...v0.5.0) - 2026-06-15

- docs(crosswalk): design ADR for compound keys (join on N attributes) by @kumagallium in https://github.com/kumagallium/asterism/pull/208
- feat(documents): Word (.docx) ingest via pandoc — upload a contract, cite a clause by @kumagallium in https://github.com/kumagallium/asterism/pull/210
- feat(crosswalk): compound keys phase-1a — tuple join keys in the pure builder by @kumagallium in https://github.com/kumagallium/asterism/pull/211
- feat(api): POST /api/documents — create a document dataset (no schema design) by @kumagallium in https://github.com/kumagallium/asterism/pull/212
- feat(ui): 文書を追加 mode — upload a JATS/Word document, query it with citations by @kumagallium in https://github.com/kumagallium/asterism/pull/213
- fix(ui): document as a source kind (not a separate mode); drop JATS jargon by @kumagallium in https://github.com/kumagallium/asterism/pull/214
- feat(crosswalk): compound keys phase-1b — runtime store-gather + config + API by @kumagallium in https://github.com/kumagallium/asterism/pull/215
- fix(documents): 箇条書き・見出し無し本文も文として取り込む by @kumagallium in https://github.com/kumagallium/asterism/pull/216
- feat(ui): compound keys — 追加の一致条件 (AND) in the crosswalk builder by @kumagallium in https://github.com/kumagallium/asterism/pull/217
- feat(documents): 1データセットに文書を追記していく（定例ミーティング型） by @kumagallium in https://github.com/kumagallium/asterism/pull/218
- feat(tools): ツール結果セルをクリックで全体コピー by @kumagallium in https://github.com/kumagallium/asterism/pull/220
- feat(ui): ontology map — bird's-eye view of ontologies and how they link by @kumagallium in https://github.com/kumagallium/asterism/pull/221
- feat(ui): i18n (ja/en, 日本語ファースト) を UI 全面に導入 by @kumagallium in https://github.com/kumagallium/asterism/pull/219
- feat(ui): ontology map — layer for existing standard ontologies (reused) by @kumagallium in https://github.com/kumagallium/asterism/pull/222
- feat(ui): curated starter pack of known ontologies + grounding direction ADR by @kumagallium in https://github.com/kumagallium/asterism/pull/223
- feat(grounding): closed-set search foundation for external-standard LINK by @kumagallium in https://github.com/kumagallium/asterism/pull/225
- feat(grounding): adopt UI + ontology-map 整合 edges (external-standard LINK) by @kumagallium in https://github.com/kumagallium/asterism/pull/226
- feat(grounding): propose-time standard candidate suggestions by @kumagallium in https://github.com/kumagallium/asterism/pull/228

## [v0.4.0](https://github.com/kumagallium/asterism/compare/v0.3.0...v0.4.0) - 2026-06-14

- feat(documents): runtime JATS-document ingest — uploaded docs citable to the sentence by @kumagallium in https://github.com/kumagallium/asterism/pull/205
- feat(crosswalk): user-composable normalizer recipes (declarative, no code) by @kumagallium in https://github.com/kumagallium/asterism/pull/207

## [v0.3.0](https://github.com/kumagallium/asterism/compare/v0.2.0...v0.3.0) - 2026-06-13

- docs(reports): verification-report practice + Tier0 sufficiency milestone by @kumagallium in https://github.com/kumagallium/asterism/pull/180
- feat(tier0): object-array multi-value — json_array / json_pluck (+ split None fix) by @kumagallium in https://github.com/kumagallium/asterism/pull/179
- feat(watcher): per-dataset append watcher — drop a CSV, the live feed grows by @kumagallium in https://github.com/kumagallium/asterism/pull/178
- docs(#19): B-2 — real-LLM JSON propose dogfood (AI-design path) by @kumagallium in https://github.com/kumagallium/asterism/pull/183
- feat(crosswalk): productize ② runtime + per-link provenance by @kumagallium in https://github.com/kumagallium/asterism/pull/182
- feat(ui): crosswalk authoring (データを追加) + management (カタログ) by @kumagallium in https://github.com/kumagallium/asterism/pull/184
- feat(crosswalk): element-canonical normalizer (Bi2Te3 ≡ Te3Bi2) by @kumagallium in https://github.com/kumagallium/asterism/pull/185
- feat(ui): crosswalk normalizer selector + hide hub from Home by @kumagallium in https://github.com/kumagallium/asterism/pull/186
- feat(ui): catalog "ライブに追記" control — grow a promoted feed by appending by @kumagallium in https://github.com/kumagallium/asterism/pull/187
- spike(docs-layer): JATS→RDF via ql:XPath — de-risk the document-ontology layer by @kumagallium in https://github.com/kumagallium/asterism/pull/188
- feat(crosswalk): multi-perspective Phase 1 — the upper ontology is plural by @kumagallium in https://github.com/kumagallium/asterism/pull/189
- feat(substrate): ingest native-JSON nested arrays via tabularize → Tier 0 explode by @kumagallium in https://github.com/kumagallium/asterism/pull/190
- feat(ui): crosswalk perspectives — list + author a named lens (Phase 3 partial) by @kumagallium in https://github.com/kumagallium/asterism/pull/191
- chore(coverage): re-measure after tabularize — …Raw 11.1% → 0.0%, tighten gate 15% → 5% by @kumagallium in https://github.com/kumagallium/asterism/pull/192
- refactor(materials_project): migrate mp.rml.ttl JSONPath → CSV+tabularize by @kumagallium in https://github.com/kumagallium/asterism/pull/193
- fix(substrate): guard direct-CSV reserved columns (subject/predicate) — silent 0-triples bug by @kumagallium in https://github.com/kumagallium/asterism/pull/194
- feat(tabularize): auto-detect wrapped record arrays ({"docs":[...]}) without record_path by @kumagallium in https://github.com/kumagallium/asterism/pull/195
- feat(crosswalk): Phase 2 — schema alignment between perspectives by @kumagallium in https://github.com/kumagallium/asterism/pull/196
- chore(step0): pin inspect's flatten/reserved mirror to asterism.tabularize via sync test by @kumagallium in https://github.com/kumagallium/asterism/pull/199
- feat(substrate): enable .xml/JATS source path for the document-ontology layer by @kumagallium in https://github.com/kumagallium/asterism/pull/197
- feat(papers): document-ontology layer MVP — structure a real paper + data↔text fusion by @kumagallium in https://github.com/kumagallium/asterism/pull/201
- spike(docs-layer): unstructured PDF → the same citable document graph (Docling) by @kumagallium in https://github.com/kumagallium/asterism/pull/203
- spike(docs-layer): Word (.docx) → the same citable document graph (pandoc → JATS) by @kumagallium in https://github.com/kumagallium/asterism/pull/204
- feat(ui): crosswalk Phase 3 — 視点をつなぐ schema alignment surface by @kumagallium in https://github.com/kumagallium/asterism/pull/200
- feat(ui): generic crosswalk authoring — any concept + generic join keys by @kumagallium in https://github.com/kumagallium/asterism/pull/202

## [v0.2.0](https://github.com/kumagallium/asterism/compare/v0.1.1...v0.2.0) - 2026-06-11

- feat(api): JSON source ingest + source persistence (#19 part 3) by @kumagallium in https://github.com/kumagallium/asterism/pull/153
- feat(tools): AI-assisted query-tool draft (P2, key-gated) by @kumagallium in https://github.com/kumagallium/asterism/pull/154
- feat(ui): part5 safe-replace — re-ingest + re-promote for promoted/ingested datasets by @kumagallium in https://github.com/kumagallium/asterism/pull/152
- feat(ui): wire the JSON data source (#19 part 4) by @kumagallium in https://github.com/kumagallium/asterism/pull/156
- docs(#19): Materials Project JSON-source dogfood + ADR + ROADMAP by @kumagallium in https://github.com/kumagallium/asterism/pull/157
- fix(ui): ReingestControl source picker follows source_kind (#19) by @kumagallium in https://github.com/kumagallium/asterism/pull/158
- feat(ui): per-dataset tools workbench — grow verified Ask tools without a PR (P3) by @kumagallium in https://github.com/kumagallium/asterism/pull/159
- feat(exposure): controlled-exposure profile + MCP-front topology by @kumagallium in https://github.com/kumagallium/asterism/pull/160
- fix(ui): tools result-mapping arrow direction (列名 → 変数) by @kumagallium in https://github.com/kumagallium/asterism/pull/161
- feat(propose): ground AI draft in the dataset's RML (real vocabulary, no example.org) by @kumagallium in https://github.com/kumagallium/asterism/pull/162
- feat(tools): run a verified tool deterministically — key-free, no LLM by @kumagallium in https://github.com/kumagallium/asterism/pull/163
- feat(ui): run verified tools key-free from the Ask view too by @kumagallium in https://github.com/kumagallium/asterism/pull/164
- chore(ui): keep Ask for NL Q&A only — verified-tool execution lives in the Catalog by @kumagallium in https://github.com/kumagallium/asterism/pull/165
- feat(ui): make Ask require an API key (retire the key-free path from the UX) by @kumagallium in https://github.com/kumagallium/asterism/pull/166
- docs+spike(crosswalk): a thin, growing crosswalk hub across datasets by @kumagallium in https://github.com/kumagallium/asterism/pull/169
- Track C: Tier 0 coverage measurement over a diverse corpus by @kumagallium in https://github.com/kumagallium/asterism/pull/168
- feat(tier0): parameterized primitives (lookup / regex_extract / template) + seed tables by @kumagallium in https://github.com/kumagallium/asterism/pull/167
- feat(tier0): core function expansion — numeric / date / string / bool / id / value+unit (Track A) by @kumagallium in https://github.com/kumagallium/asterism/pull/170
- feat(crosswalk): tested, multi-concept crosswalk hub builder (asterism.crosswalk) by @kumagallium in https://github.com/kumagallium/asterism/pull/171
- chore(coverage): recalibrate Tier0 "enough" gate after A/B — …Raw 63.6% → 36.8% by @kumagallium in https://github.com/kumagallium/asterism/pull/172
- feat(tier0): multi-value "easy wins" — json_array_single / array_at / split by @kumagallium in https://github.com/kumagallium/asterism/pull/173
- feat(ingest): incremental append — grow a live feed in place (装置 CSV をライブに育てる) by @kumagallium in https://github.com/kumagallium/asterism/pull/174
- feat(security)!: critical hardening — fail-closed defaults (ingest/exposure/auth/network) by @kumagallium in https://github.com/kumagallium/asterism/pull/175
- chore(coverage): recalibrate Tier0 gate after multi-value — …Raw 36.8% → 11.1% (PASS) by @kumagallium in https://github.com/kumagallium/asterism/pull/176
- fix(tagpr): minor/major label config in git-config format by @kumagallium in https://github.com/kumagallium/asterism/pull/177

## [v0.1.1](https://github.com/kumagallium/asterism/compare/v0.1.0...v0.1.1) - 2026-06-09

- [feat] ワークベンチ: 提案/再生成の進捗カードを追加 (経過時間+不定バー) by @kumagallium in https://github.com/kumagallium/asterism/pull/69
- [feat] 検証パネルを日本語化 + 凡例追加 + 自動翻訳の抑止 by @kumagallium in https://github.com/kumagallium/asterism/pull/70
- [feat] ワークベンチ: ジョブ再開 (リロード/落ち/接続断で提案を失わない) by @kumagallium in https://github.com/kumagallium/asterism/pull/72
- [Phase 5] 設計→Ask 連結: 検証済み関数ライブラリ v0 + e2e スパイク + 統治 ADR by @kumagallium in https://github.com/kumagallium/asterism/pull/73
- [Phase 5 #14] Tier0 に 2 入力 float_array_count を追加 (curve pointCount) by @kumagallium in https://github.com/kumagallium/asterism/pull/74
- [Phase 5 #14] step0 materialize が宣言 RML を任意 artifact として出力 (案B) by @kumagallium in https://github.com/kumagallium/asterism/pull/75
- [Phase 5 #14] step0 propose が宣言 RML を生成 + T9 閉集合検証 (案C) by @kumagallium in https://github.com/kumagallium/asterism/pull/76
- [Phase 5 #14] validate に T9 (RML 閉集合) を統合 by @kumagallium in https://github.com/kumagallium/asterism/pull/77
- [Phase 5 #15] 宣言 substrate 投入コア + 人間ゲート ADR (S1) by @kumagallium in https://github.com/kumagallium/asterism/pull/78
- [Phase 5 #15] 人間ゲート投入エンドポイント + RML 永続化 (S2) by @kumagallium in https://github.com/kumagallium/asterism/pull/79
- [Phase 5 #15] ワークベンチに投入ゲート + Gallery ingested 表示 (S3 UI) by @kumagallium in https://github.com/kumagallium/asterism/pull/80
- Asterism 改名（IRI 名前空間ごと）＋ ROADMAP/CLAUDE/README by @kumagallium in https://github.com/kumagallium/asterism/pull/82
- [Phase 5 #15 S4] draft→canonical 昇格 + alignment（人間ゲートの昇格側） by @kumagallium in https://github.com/kumagallium/asterism/pull/83
- [#15] ローカル substrate スタック起動を再現可能に（検証用・Asterism） by @kumagallium in https://github.com/kumagallium/asterism/pull/84
- [fix] SSE の一時切断で propose/refine の進捗を失わない by @kumagallium in https://github.com/kumagallium/asterism/pull/85
- [fix] 宣言 RML の FnO 名前空間を正規化 + ingest エラーを明確化 (#15) by @kumagallium in https://github.com/kumagallium/asterism/pull/86
- [fix] Gallery のデータセット状態表示と昇格ラベルを分かりやすく (#15) by @kumagallium in https://github.com/kumagallium/asterism/pull/87
- [docs] ROADMAP: 6/3 ドッグフード結果と #18 を反映 by @kumagallium in https://github.com/kumagallium/asterism/pull/88
- [feat] #18 汎用 Ask 層の土台: スキーマ非依存の schema_summary + sparql_query by @kumagallium in https://github.com/kumagallium/asterism/pull/89
- [feat] #18 LLM NL→SPARQL escape: 型付き優先＋自動フォールバックの Ask by @kumagallium in https://github.com/kumagallium/asterism/pull/90
- [feat] #18 Ask-view UX: 一般質問用キー欄＋使用 SPARQL 開示パネル by @kumagallium in https://github.com/kumagallium/asterism/pull/91
- [docs] ROADMAP: #18 完了（実 LLM dogfood 実証） by @kumagallium in https://github.com/kumagallium/asterism/pull/92
- [docs] ADR: オントロジー/canonical ライフサイクル + starrydata 脱結合（ドラフト） by @kumagallium in https://github.com/kumagallium/asterism/pull/93
- [feat] #20 P2-1: 汎用ヘルパを asterism.text に抽出（starrydata 脱結合の第一歩） by @kumagallium in https://github.com/kumagallium/asterism/pull/94
- [feat] #20 P2-2a: starrydata identity を datasets/ に宣言＋汎用 dataset ローダ by @kumagallium in https://github.com/kumagallium/asterism/pull/95
- UI Phase 1: forest 再設計（基盤 + 新IA + Ask 全面刷新） by @kumagallium in https://github.com/kumagallium/asterism/pull/96
- #20 P2-2b(1/3): api/mcp 定数 import 撤去 + datasets/ を image 同梱 by @kumagallium in https://github.com/kumagallium/asterism/pull/97
- #20 P2-2b(2/3): QUDT 表を datasets/ の唯一の正へ by @kumagallium in https://github.com/kumagallium/asterism/pull/98
- #20 P2-2b(3/3): seed 物理移動 + watcher descriptor 化 + ADR/ROADMAP by @kumagallium in https://github.com/kumagallium/asterism/pull/99
- fix(step0): 不完全 refine 出力ガード（前の完全版を保持して警告） by @kumagallium in https://github.com/kumagallium/asterism/pull/101
- docs(ROADMAP): refine ガード(#101)＋#20 P2 完了を反映 by @kumagallium in https://github.com/kumagallium/asterism/pull/102
- docs(ADR): #20 P3 実装設計（ライフサイクル CRUD/版・提案/未実装） by @kumagallium in https://github.com/kumagallium/asterism/pull/103
- UI Phase 2: ホーム + カタログ データセット主役化 + 共有の語彙 + データ追加ソース切替 by @kumagallium in https://github.com/kumagallium/asterism/pull/100
- UI: 旧 demo/gallery 由来の dead CSS 整理 by @kumagallium in https://github.com/kumagallium/asterism/pull/104
- #20 P3(1): dataset version モデル + lifecycle graph IRI 基盤 by @kumagallium in https://github.com/kumagallium/asterism/pull/105
- #20 P3(2): canonical 読み取りスコープ（default ∪ canonical 個室・draft 除外） by @kumagallium in https://github.com/kumagallium/asterism/pull/106
- #20 P3 step1 完了: promote を per-dataset canonical graph へ切替 by @kumagallium in https://github.com/kumagallium/asterism/pull/107
- #20 P3 step3: retract/reinstate（引っ込める・tombstone・引用安定） by @kumagallium in https://github.com/kumagallium/asterism/pull/108
- #20 P3 step4: delete（消す）— force ゲート + tombstone 痕跡 by @kumagallium in https://github.com/kumagallium/asterism/pull/109
- #20 横断参照(cross-dataset)=FROM-merge の基盤（部品・未配線） by @kumagallium in https://github.com/kumagallium/asterism/pull/110
- [feat] #20 ① 横断参照(cross-dataset)=FROM-merge 配線 by @kumagallium in https://github.com/kumagallium/asterism/pull/111
- [feat] #20 ② P4-1 宣言 typed ツールエンジン (content 宣言・additive) by @kumagallium in https://github.com/kumagallium/asterism/pull/112
- [feat] #20 ② P4-2a 宣言ツールの MCP 動的公開 by @kumagallium in https://github.com/kumagallium/asterism/pull/113
- [feat] #20 ③ step5 オントロジー graph 投影 + schema_summary label 補強 by @kumagallium in https://github.com/kumagallium/asterism/pull/114
- [feat] UI: 共有の語彙に「実データの語彙」live パネル (#20 step5 可視化) by @kumagallium in https://github.com/kumagallium/asterism/pull/115
- [feat] UI: 共有の語彙を完全に live 化（ハードコード fixture 撤去） by @kumagallium in https://github.com/kumagallium/asterism/pull/116
- [feat] UI: 共有の語彙の件数に凡例（クラス=インスタンス数 / 述語=使用回数） by @kumagallium in https://github.com/kumagallium/asterism/pull/118
- [feat] UI: ライフサイクル操作（撤回/復帰/削除）配線 + canonical 集計の明示 by @kumagallium in https://github.com/kumagallium/asterism/pull/119
- [fix] UI: ライフサイクル操作（昇格/撤回/削除）を両タブで表示 by @kumagallium in https://github.com/kumagallium/asterism/pull/120
- [feat] UI: カタログ/ホームの fixture を完全撤去し live 化 (#20) by @kumagallium in https://github.com/kumagallium/asterism/pull/121
- [fix] UI: データセット詳細のライフサイクル操作のパディング揃え by @kumagallium in https://github.com/kumagallium/asterism/pull/122
- [perf] #20 FROM-merge: graph 列挙を空パターン化（triple 走査を回避） by @kumagallium in https://github.com/kumagallium/asterism/pull/123
- docs: ROADMAP 更新 — UI カタログ/ホーム live 化 (#121) + FROM-merge perf (#123) by @kumagallium in https://github.com/kumagallium/asterism/pull/124
- docs: 静的「引用できる事実」デモ（サーバ/AI 不要・GitHub Pages・MP 横断結合） by @kumagallium in https://github.com/kumagallium/asterism/pull/117
- docs(readme): デモ導線・バッジ追加＋UI 記述更新 by @kumagallium in https://github.com/kumagallium/asterism/pull/125
- feat(catalog): ingest design-stage datasets from the catalog (Task E) by @kumagallium in https://github.com/kumagallium/asterism/pull/126
- [feat] #15 運用化: 本番 docker api に morph-kgc（+ step0 ビルドバグ修正） by @kumagallium in https://github.com/kumagallium/asterism/pull/127
- fix(ui): prevent white-screen render crash (error boundary + translate opt-out) by @kumagallium in https://github.com/kumagallium/asterism/pull/128
- fix(ingest): graceful 504/502 + longer write timeout for large ingests by @kumagallium in https://github.com/kumagallium/asterism/pull/129
- docs(adr): scalable declarative ingestion (streaming + background job) by @kumagallium in https://github.com/kumagallium/asterism/pull/130
- feat(substrate): streaming N-Triples materialize + chunked load (scalable ingest S1) by @kumagallium in https://github.com/kumagallium/asterism/pull/131
- feat(ingest): background job + live SSE progress (scalable ingest S2+S3) by @kumagallium in https://github.com/kumagallium/asterism/pull/132
- docs(roadmap): mark scalable ingestion S1-S3 complete by @kumagallium in https://github.com/kumagallium/asterism/pull/133
- fix(ingest): generous timeout for large-graph DROP/MOVE by @kumagallium in https://github.com/kumagallium/asterism/pull/134
- docs(roadmap): MOVE-OOM root cause + memory-bounded promote next by @kumagallium in https://github.com/kumagallium/asterism/pull/135
- feat(promote): memory-bounded promotion — flag-based, no MOVE GRAPH by @kumagallium in https://github.com/kumagallium/asterism/pull/136
- #19: onboard Materials Project as a second, non-starrydata dataset (content backbone) by @kumagallium in https://github.com/kumagallium/asterism/pull/137
- test(promote): disposable-Oxigraph memory-bounded promote validation harness by @kumagallium in https://github.com/kumagallium/asterism/pull/138
- docs: record #19 path B-1 (production-ingest dogfood) by @kumagallium in https://github.com/kumagallium/asterism/pull/139
- feat(promote): part5 versioned graphs — replace/delete off the critical path by @kumagallium in https://github.com/kumagallium/asterism/pull/140
- fix(promote): chunked delete for part5 reclaim — a single DROP GRAPH OOMs by @kumagallium in https://github.com/kumagallium/asterism/pull/141
- fix(ask): generic questions fall through to the LLM escape (not a canned list) by @kumagallium in https://github.com/kumagallium/asterism/pull/142
- fix(ui/ask): IME-safe Enter + render the answer as Markdown by @kumagallium in https://github.com/kumagallium/asterism/pull/144
- fix(ask): cross-dataset / crystal-structure questions reach the LLM escape by @kumagallium in https://github.com/kumagallium/asterism/pull/145
- feat(ask): LLM picks among typed tools + SPARQL (P4-2b) — drop the keyword router by @kumagallium in https://github.com/kumagallium/asterism/pull/146
- docs(roadmap): Ask-layer fixes + P4-2b done (LLM picks tools) by @kumagallium in https://github.com/kumagallium/asterism/pull/147
- feat(ask): route every dataset's verified tools + per-answer provenance badge by @kumagallium in https://github.com/kumagallium/asterism/pull/148
- feat(step0): JSON source inspector (#19 part 1) by @kumagallium in https://github.com/kumagallium/asterism/pull/149
- feat(tools): per-dataset query-tools store — grow verified tools without a repo PR (P1) by @kumagallium in https://github.com/kumagallium/asterism/pull/150
- feat(step0): propose emits JSON RML logicalSource (#19 part 2) by @kumagallium in https://github.com/kumagallium/asterism/pull/151

## [v0.1.0](https://github.com/kumagallium/asterism/commits/v0.1.0) - 2026-06-02

- Fix Sample/Curve IRI collision (composite keys for starrydata raw ids) by @kumagallium in https://github.com/kumagallium/asterism/pull/8
- Make sd: / sdr: namespace dereferenceable via GitHub Pages by @kumagallium in https://github.com/kumagallium/asterism/pull/9
- Phase 3 基盤: AI-assisted Step 0 workflow を文書化 by @kumagallium in https://github.com/kumagallium/asterism/pull/11
- Phase 2: drop CSV → auto reindex (watcher + upload API) by @kumagallium in https://github.com/kumagallium/asterism/pull/10
- Phase 3 #1 + #2: AI-assisted Step 0 の prompt 集 と design-rationale by @kumagallium in https://github.com/kumagallium/asterism/pull/12
- Phase 3 #3: LinkML vs rdf-config 比較実験 + 採用判断 by @kumagallium in https://github.com/kumagallium/asterism/pull/13
- Phase 3 #4 (part 1): step0 package — CSV inspection module by @kumagallium in https://github.com/kumagallium/asterism/pull/15
- Phase 3 #4 (part 2): propose_schema with Anthropic SDK + prompt caching by @kumagallium in https://github.com/kumagallium/asterism/pull/16
- Phase 2 #3: self-built MCP with template_curve_fetch by @kumagallium in https://github.com/kumagallium/asterism/pull/14
- Phase 3 #6: validate_schema — 8-trap static + dynamic validator by @kumagallium in https://github.com/kumagallium/asterism/pull/17
- Phase 3 #5: refine_schema — process review comments, sync update 4 artifacts by @kumagallium in https://github.com/kumagallium/asterism/pull/19
- Phase 3 #7: csv2rdf-ttl2mermaid — auto-gen Mermaid from TBox + --check CI mode by @kumagallium in https://github.com/kumagallium/asterism/pull/20
- Phase 2 #2: QUDT quantity/unit normalization (synonym unification) by @kumagallium in https://github.com/kumagallium/asterism/pull/18
- Phase 3: materialize_schema — split proposal Markdown into 4 artifact files by @kumagallium in https://github.com/kumagallium/asterism/pull/22
- Phase 2 #6: DigitizationActivity (WebPlotDigitizer provenance) by @kumagallium in https://github.com/kumagallium/asterism/pull/21
- fix: propose/refine stream output (16k→32k) + dogfood feedback by @kumagallium in https://github.com/kumagallium/asterism/pull/23
- fix: validate T1 false positive on anti_patterns IRI examples by @kumagallium in https://github.com/kumagallium/asterism/pull/24
- Phase 2 #5: full-scale benchmark (12M triples) + invalid-IRI URL fix by @kumagallium in https://github.com/kumagallium/asterism/pull/25
- [fix] Phase 2: load into default graph (MIE-compatible) by @kumagallium in https://github.com/kumagallium/asterism/pull/26
- docs: Phase 3 dogfood Round 3 — propose to refine to materialize to validate full loop by @kumagallium in https://github.com/kumagallium/asterism/pull/27
- [infra] make upload-api host port configurable by @kumagallium in https://github.com/kumagallium/asterism/pull/28
- [docs] sync Pages ontology with canonical (QUDT + DigitizationActivity) by @kumagallium in https://github.com/kumagallium/asterism/pull/29
- [feat] Phase 3: validate T1 verifies uniqueness from the ingester IRI builder by @kumagallium in https://github.com/kumagallium/asterism/pull/30
- [feat] Phase 3: validate を CI に統合 (starrydata-min fixture) by @kumagallium in https://github.com/kumagallium/asterism/pull/31
- [docs] Phase 3 feedback: 項目6 (validate CI 統合) を完了マーク by @kumagallium in https://github.com/kumagallium/asterism/pull/32
- [fix] ingest: drop scheme-less URL placeholders (e.g. "unknown") by @kumagallium in https://github.com/kumagallium/asterism/pull/33
- [docs] README: Roadmap/Status を実態に更新 (Phase 2 完了・Phase 3 step0) by @kumagallium in https://github.com/kumagallium/asterism/pull/35
- [ops] full-dataset loader into the default graph (in place) by @kumagallium in https://github.com/kumagallium/asterism/pull/34
- [docs] Phase 4 UI 設計ドラフト (合意待ち) by @kumagallium in https://github.com/kumagallium/asterism/pull/36
- [feat] Phase 4 M0a: POST /api/inspect (step0 構造解析を API 化) by @kumagallium in https://github.com/kumagallium/asterism/pull/37
- [feat] Phase 4 M0b: React+Vite フロント足場 + inspect 画面 by @kumagallium in https://github.com/kumagallium/asterism/pull/38
- [fix] Phase 4 M0b: vite proxy を 127.0.0.1 に + GFM テーブル描画 by @kumagallium in https://github.com/kumagallium/asterism/pull/39
- [docs] Phase 4: オントロジー層/マッピング層の分離を設計に追加 (D8) by @kumagallium in https://github.com/kumagallium/asterism/pull/41
- [feat] Phase 4 M1a: POST /api/propose + SSE ジョブ基盤 by @kumagallium in https://github.com/kumagallium/asterism/pull/42
- [docs] MIE: add answer_grounding guidance + refresh data_statistics by @kumagallium in https://github.com/kumagallium/asterism/pull/40
- [docs] MIE: add "highest ZT" query example + ZT anti-pattern by @kumagallium in https://github.com/kumagallium/asterism/pull/43
- [feat] Phase 4 M1b: フロント propose 画面 (SSE購読 + Mermaid描画) by @kumagallium in https://github.com/kumagallium/asterism/pull/44
- [feat] Phase 4: propose のドメインヒント任意化 + 定型チェックリスト (案A) by @kumagallium in https://github.com/kumagallium/asterism/pull/45
- [feat] Phase 4 D0: Ask+来歴の契約 (demoApi) + デザイントークン + 設計追記 by @kumagallium in https://github.com/kumagallium/asterism/pull/46
- [feat] Phase 4 D1: Ask ビュー (根拠付き回答 + 引用カード + 品質注記) by @kumagallium in https://github.com/kumagallium/asterism/pull/48
- [feat] Phase 4 D2: 来歴トレース (引用クリック→鎖の描画) by @kumagallium in https://github.com/kumagallium/asterism/pull/49
- [experiments] RDF data-quality auditor (ZT outliers + invariants) by @kumagallium in https://github.com/kumagallium/asterism/pull/47
- [fix] Phase 4 demo: 来歴トレースにも mock バッジを表示 by @kumagallium in https://github.com/kumagallium/asterism/pull/51
- [feat] Phase 4 M1c: refine (レビューコメント→再生成) by @kumagallium in https://github.com/kumagallium/asterism/pull/52
- [feat] Phase 4 M1d: materialize + validate(8罠) 表示 + artifacts DL by @kumagallium in https://github.com/kumagallium/asterism/pull/53
- [feat] Phase 4 D3: demo-agent 結線 (base URL 分離 + 実データ端ケース耐性) by @kumagallium in https://github.com/kumagallium/asterism/pull/54
- [feat] Phase 4: タブの導線を2フェーズに整理 (ワークベンチ vs 活用) by @kumagallium in https://github.com/kumagallium/asterism/pull/55
- [feat] Phase 4 M4a: Ontologies ギャラリー (共有 TBox の俯瞰・変更危険度を明示) by @kumagallium in https://github.com/kumagallium/asterism/pull/56
- [feat] Phase 4 M4b: Mappings ギャラリー (目的タグを目立たせる) by @kumagallium in https://github.com/kumagallium/asterism/pull/57
- [feat] Phase 4 U1: アプリシェル化 (サイドナビ + トップバー + コンテンツ) by @kumagallium in https://github.com/kumagallium/asterism/pull/58
- [feat] Phase 4 V1a: materialize 結果を永続化 + dataset 一覧 API by @kumagallium in https://github.com/kumagallium/asterism/pull/59
- [feat] Phase 4 V1b: Gallery が materialize 済み dataset を表示 (連結ループ) by @kumagallium in https://github.com/kumagallium/asterism/pull/60
- [feat] Phase 4 U2: ワークベンチをステッパー化 (手順が見える設計フロー) by @kumagallium in https://github.com/kumagallium/asterism/pull/61
- [feat] Phase 4 U3: 仕上げ (file input・スピナー・レスポンシブ) by @kumagallium in https://github.com/kumagallium/asterism/pull/62
- [feat] Phase 4 M2: 取り込み履歴ビュー (GET /jobs を表で表示) by @kumagallium in https://github.com/kumagallium/asterism/pull/63
- [feat] Phase 4 M3: 読み取り専用 SPARQL エディタ (脱出ハッチ) by @kumagallium in https://github.com/kumagallium/asterism/pull/64
- [feat] Phase 4: Ask⇄Gallery 結合 (引用→対応語彙クラスへ飛ぶ grounding 導線) by @kumagallium in https://github.com/kumagallium/asterism/pull/65
- [feat] Phase 4 仕上げ: Gallery に QUDT 再利用を明示 + 来歴ステップから語彙リンク by @kumagallium in https://github.com/kumagallium/asterism/pull/66
- [fix] ワークベンチの提案がタブ切替/リロードで消える問題 (U2 リグレッション) by @kumagallium in https://github.com/kumagallium/asterism/pull/67
- [feat] ワークベンチ: 構造解析を必須ステップから任意化 (案 b) by @kumagallium in https://github.com/kumagallium/asterism/pull/68
