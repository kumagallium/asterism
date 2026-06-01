# Phase 4 — Web UI 設計 (研究者向けスキーマ設計レビュー + データ管理)

> ステータス: **draft (合意待ち)**。Phase 1-3 と同じく、実装前に設計を凍結するための文書。
> 各設計判断は Decision / Why / Alternatives / Trade-offs で記述し、未決事項は §9 Open Questions に集約する。

## 0. 背景

Phase 3 で「CSV → AI がスキーマ案 → 人間レビュー → 検証」の **頭脳 (step0 の 6 CLI)** は完成し、dogfood で end-to-end 動作した。しかし触る手段は CLI のみで、**GUI が無い**。本 Phase は step0 を「他の研究者が使える Web UI」として包み、あわせて rdf 化済みデータの管理画面を提供する。

## 1. 対象ユーザと前提

- **主対象**: 熊谷さん以外の研究者 (NIMS / 共同研究者など)。CLI や Python に不慣れでも、自分の CSV からスキーマ案を作り、対話レビューで詰められること。
- **デプロイ前提**: Design principle 「self-hostable, single deployment (`docker compose up`)、マルチテナント SaaS ではない」を踏襲。**1 デプロイ = 1 研究室/個人**。研究室内で複数人が同じインスタンスを共有する程度。
- **private ホスティング前提 (確定)**: public 公開は当面しない。よって認証要件は最小で可、外部公開向けのセキュリティ強化は将来必要時に再検討する。
- **主軸機能 (a)**: スキーマ設計の対話レビュー (propose → refine)。
- **見せ場機能 (c)**: 作成したオントロジーを **視覚的に俯瞰できるギャラリー** (ユーザ要望・デモ映え)。step0 が生成する Mermaid 図を活かす。
- **副次機能 (b)**: rdf 化済みデータの管理 (取り込み履歴。Oxigraph 統計 / SPARQL は後続)。

## 2. スコープ (MVP 段階分け)

UI は大物なので段階に切る。各段階は単独で価値が出る単位。

| 段階 | 内容 | 価値 |
|---|---|---|
| **M0 足場** | FastAPI に step0 をライブラリ統合。`/api/inspect` (同期)。React 足場 + CSV アップロード画面。compose に `ui` サービス | 配線が通る |
| **M1 設計レビュー core (★主軸)** | propose を SSE streaming 表示 → 提案 Markdown 表示 (**Mermaid 図はその場でレンダリング**) → refine コメント入力 → materialize → validate (8罠) 結果表示 → 4 artifacts ダウンロード | 研究者が対話レビューを回せる |
| **Ask + 来歴トレース (★デモの中心)** | 自然言語の質問 → **根拠付き回答 + クリック可能な引用カード + データ品質注記**。引用クリック → 来歴の鎖 (curve→sample→paper→digitization→ingestion) を描画。回答生成 LLM は **core の外** (消費層 demo-agent) にあり、UI は契約 (§6.7) を叩くだけ | grounding の payoff = 「問い→根拠付き回答+引用+来歴」を見せる |
| **M2 取り込み連携** | 確定した bundle を既存 upload/watcher 経由で Oxigraph に取り込む (※ ingester は人手確認後、§7 D4) | 設計→取り込みが一気通貫 |
| **M3 データ管理 (最小)** | 取り込み履歴 (`/jobs`) の一覧表示のみ。Oxigraph 統計 / SPARQL エディタは後続 Phase へ送る (ユーザ確定: M1 優先・管理は最小) | 取り込み状況の把握 |
| **M4 ギャラリー (見せ場・2 本立て)** | **Ontologies ギャラリー** (共有語彙=TBox のクラス図) と **Mappings ギャラリー** (dataset→語彙の束縛=MIE+ingester、**目的タグを目立たせる**) を**別ビュー**で提示。Mermaid を mermaid.js で描画。将来 TBox を WebVOWL | オントロジー(共有資産)とマッピング(使い捨て)を分けて俯瞰 (§6.6) |

**実装順 (デモ優先・確定)**: **M0 → M1 → [Ask+来歴] → M4 → M2 → M3**。ARiSE 採択デモの wow が「問い→根拠付き回答+引用+来歴」なので、**Ask+来歴を M1 直後に前倒し**する。SPARQL エディタは主役にしない (後送り・上級者の脱出ハッチ)。

**並行作業**: M1 が動いたら、別 dataset (NIMS Supercon 等) を UI 経由で流して汎用性を検証する (ユーザ要望)。step0 の隠れた starrydata 前提を炙り出す dogfood も兼ねる。

## 3. アーキテクチャ全体図

```
┌─────────────────────────────────────────────────────────┐
│  Browser (React + Vite + TS SPA)                          │
│   - アップロード / inspection 表示                         │
│   - propose (SSE で逐次表示) / refine                      │
│   - validate レポート / artifacts DL                       │
│   - データ管理 (jobs / graph stats / SPARQL)               │
└───────────────┬───────────────────────────────────────────┘
                │ REST + SSE (/api/*)
┌───────────────▼───────────────────────────────────────────┐
│  FastAPI (api/ を拡張)                                      │
│   - step0 を **ライブラリとして import** (CLI ではなく)      │
│   - 長時間 LLM ジョブ: 起動 → SSE stream → jobs.jsonl 永続化 │
│   - 既存: /upload/{kind}, /jobs, /health                    │
└───────┬───────────────────────────────┬───────────────────┘
        │ step0 (propose/refine/...)     │ HTTP
        ▼                                ▼
   Anthropic API                    Oxigraph (SPARQL, 別コンテナ)
```

## 4. バックエンド設計 (FastAPI 拡張)

### D1. step0 を CLI ではなくライブラリとして呼ぶ

- **Decision**: API は `subprocess` で `csv2rdf-propose` を叩くのではなく、`csv2rdf_step0.propose` 等を **import して関数呼び出し**する。
- **Why**: streaming トークンを直接 SSE に流せる / 例外をハンドリングできる / プロセス起動コスト無し。step0 は既に `LLMClient` Protocol 抽象があり library 利用しやすい。
- **Alternatives**: subprocess + stdout parse — 疎結合だが streaming と進捗取得が面倒。
- **Trade-offs**: api が step0 に依存 (pyproject に step0 を path dependency 追加)。許容。

### D2. 長時間 LLM ジョブ + SSE streaming

propose/refine は 5-6 分かかる。同期リクエストはタイムアウト・再接続不可。

- **Decision**: `POST /api/datasets/{id}/propose` は **ジョブを起動して `job_id` を即返す**。フロントは `GET /api/jobs/{job_id}/stream` (Server-Sent Events) で `token` / `progress` / `done` / `error` イベントを受ける。ジョブ状態と最終成果物は **`jobs.jsonl` (既存) + 作業ディレクトリ**に永続化し、再接続時は途中から / 完了結果を返せる。
- **Why**: SSE は単方向 (サーバ→クライアント) で十分・HTTP のみ・実装が WebSocket より軽い。step0 の `AnthropicLLMClient` は既に streaming 対応。
- **Alternatives**: WebSocket (双方向だが今回不要)、ポーリング (UX 劣・トークン逐次表示できない)。
- **Trade-offs**: SSE はプロキシ設定 (バッファリング無効化) が要る。ジョブ実行は MVP では in-process (`asyncio.create_task`) で開始し、永続キュー (Celery/RQ) は M3 以降に必要なら導入。

### D3. エンドポイント案

| メソッド | パス | 役割 |
|---|---|---|
| `POST` | `/api/datasets` | CSV を 1 つ以上アップロード → `dataset_id` |
| `GET` | `/api/datasets/{id}/inspection` | inspect 結果 (型/JSON/uniqueness) |
| `POST` | `/api/datasets/{id}/propose` | propose ジョブ起動 (domain hint, fk) → `job_id` |
| `GET` | `/api/jobs/{job_id}/stream` | **SSE**: token / progress / done / error |
| `POST` | `/api/datasets/{id}/refine` | review comments で refine ジョブ起動 → `job_id` |
| `POST` | `/api/datasets/{id}/materialize` | 提案 Markdown → 4 artifacts |
| `POST` | `/api/datasets/{id}/validate` | 8 罠 validate → レポート (exit code 含む) |
| `GET` | `/api/datasets/{id}/artifacts/{name}` | artifact ダウンロード |
| `GET` | `/api/graph/stats` | Oxigraph: triple 数 / 名前付きグラフ一覧 (M3) |
| `POST` | `/api/sparql` | SPARQL プロキシ (read-only, M3) |
| 既存 | `/upload/{kind}`, `/jobs`, `/health` | Phase 2 のまま |

作業ディレクトリ: `/data/step0/{dataset_id}/` に CSV・inspection・proposal・refined・artifacts を置く。

## 5. フロントエンド設計 (React + Vite + TypeScript)

- **Decision**: React + Vite + TypeScript。サーバ状態は React Query (TanStack Query)、UI は最初は最小 (Tailwind か素の CSS)。SSE は `EventSource` で受信。
- **Why**: SPA で本格的な拡張に耐える (ユーザ選択)。Vite は開発体験が速い。React Query が「ジョブの非同期状態」を素直に扱える。
- **Alternatives**: Vue/Svelte (好み次第・エコシステムは React が最大)、Next.js (SSR は今回不要・過剰)。
- **Trade-offs**: フロントのビルド/状態管理の工数増。研究者向け内製ツールにはやや重いが、将来の管理画面 (M3) まで見据えると妥当。

### 画面 / ルート

- `/` — dataset 一覧 / 新規アップロード
- `/datasets/:id` — **設計レビュー ワークベンチ** (M1 主画面): inspection タブ / proposal (SSE 逐次表示) / refine コメント / validate レポート / artifacts DL
- `/data` — データ管理 (M3): jobs 一覧 / graph stats / SPARQL エディタ

## 6. データ管理画面 (M3, 最小)

- **MVP**: 取り込み履歴 — 既存 `/jobs` (jobs.jsonl) を表で表示するのみ。
- **後続 Phase**: Oxigraph 統計 (`SELECT (COUNT(*) AS ?c)` + 名前付きグラフ一覧、`/api/graph/stats`)、read-only SPARQL エディタ (`/api/sparql` がサーバ側で Oxigraph に中継)。ユーザ確定「管理は最小」により MVP からは外す。

## 6.5 オントロジー可視化ギャラリー (ユーザ要望・見せ場)

「作成したオントロジー一覧を視覚的に確認できると良い (キャッチー)」を反映。step0 は既に Mermaid 図を生成する (`ttl2mermaid` / proposal の Mermaid セクション) ため、**表示側は薄く実装できる**のが利点。

- **M1 で芽出し (低コスト)**: 設計中 dataset の Mermaid クラス図を、フロントの **mermaid.js** でその場レンダリング。レビュー画面に組み込み、「視覚確認」をすぐ提供。
- **M4 で本格ギャラリー**: 生成済み全 dataset をカード一覧 (オントロジー名 / クラス数 / サムネ) → クリックで拡大クラス図。「作成したオントロジーを俯瞰」する見せ場。
- **リッチ可視化 (将来)**: TBox (TTL) を [WebVOWL](http://vowl.visualdataweb.org/webvowl.html) でインタラクティブ表示 (README 既出の手法)。Mermaid は静的・軽量で MVP 向き、WebVOWL は後続の発展。
- **Decision**: 可視化エンジンは **MVP は mermaid.js** (フロント npm、step0 出力をそのまま描画)。**Why**: 追加のサーバ処理不要・既存成果物を再利用・実装が薄い。**Alternatives**: WebVOWL (リッチだが TTL→VOWL 変換と埋め込みが要る・後続)、Graphviz/D3 自前 (過剰)。**Trade-offs**: Mermaid のレイアウトは大規模オントロジーで窮屈になりうる → その時 WebVOWL へ。

## 6.6 オントロジー層 / マッピング層の分離 (Palantir 的発想)

> ユーザ提起: 「オントロジーを管理・修正する場所」と「目的ベースのマッピング (YAML) を管理・修正する場所」を別にすべきでは。Palantir Foundry の "共有オントロジーに各パイプラインが map-into する" 思想に対応する。

### 現状の棚卸し: 既に 3 層に分かれて置かれている

| 層 | 実体 (starrydata) | 性質 |
|---|---|---|
| **Ontology (語彙)** | `docs/ontology/starrydata.ttl` + `diagram.md` | クラス/プロパティ (Sample, Curve, fromPaper…)。**遅く変わる・共有・壊すと下流全体に波及** |
| **Mapping (束縛)** | `ingest/src/csv2rdf/starrydata.py` (CSV→triples) + `data/togomcp/mie/<name>.yaml` (探索メタ + ShEx) | この CSV をどう語彙に結びつけるか。**速く変わる・dataset/目的ごと・ローカル** |

置き場所は半分分かれているが、**step0 は今この 3 つを「1 dataset = 1 束」として一緒に co-design 生成する**。共有オントロジーへ *map-into* する構造になっていない (= Palantir 的価値が出ていない) のがギャップ。

### map-into スペクトラム (北極星): dataset 単位の 3 択ではなく**概念単位**

新 dataset の各概念 (sample, measurement, …) ごとに判断する。混在が普通 (例: Supercon は sample=Reuse / criticalTemperature=Extend / crystal_structure=New)。

| 判断 | 意味 | 起きること | Ontology への影響 |
|---|---|---|---|
| **Reuse** | そのまま既存語彙へ | 既存 `sd:Sample` IRI に map-into | 変更なし (安全)。**横断 SPARQL が成立** |
| **Extend** | 部分的に使う/成長 | `XSample rdfs:subClassOf sd:Sample` / 既存クラスに新プロパティ | **オントロジーが育つ → レビュー必須** |
| **Align** | 別語彙のまま橋渡し | `skos:closeMatch` / `equivalentClass` 等で関係宣言 | 本体不変・関係のみ追加 (安全、後付け可) |
| **New** | 使わず新規 | 新クラスを mint | 新語彙が増える |

運用は「まず Align で繋ぎ、共通化が固まったら Extend」が安全。**意味の誤り (構文は正しいが間違った map-into) は 8 罠より厄介** → ここも `propose` の "AI 提案 → 人間確認" co-design を踏襲する。既にプロジェクトは **QUDT (単位の共有語彙再利用) = Reuse の種**を持つ。これを語彙全体へ広げるのが本筋。

### D8. 段階的分離 (Decision)

- **Decision**: **(a) UI と概念モデルでは Ontology 層と Mapping 層を分離して提示する** (M4 ギャラリーを「Ontologies / Mappings」2 本立てに、§6.6 冒頭表の性質差—変更危険度—を UI で明示)。**(b) 物理ストレージの完全分離と、既存オントロジーへの map-into / alignment 機構の実装は、2 個目の実 dataset (NIMS Supercon 等) 投入時まで遅延する**。
- **Why**: (a) は「語彙=共有資産 / マッピング=使い捨て」のメンタルモデルをほぼレイアウトコストだけで定着できる。(b) の map-into が実価値を生むのは概念の再利用先が現れる 2 個目以降であり、1 dataset の今ストレージまで割るのは来ない未来への過剰投資。真の難所は UI 分割ではなく「他 dataset の sample は既存 Sample と同概念か/部分集合か/別物か」を判定・束縛する alignment 機構。
- **Alternatives**: ① 今すぐ物理リポジトリ分割 + 中央オントロジーレジストリ → 過剰設計 (却下)。② 現状維持の 1 束生成のまま → 共有語彙が育たず dataset ごとに別 `Sample` が乱立 (将来の横断クエリを破壊、却下)。
- **Trade-offs**: 段階的なので 2 個目投入時に Mapping が Ontology を IRI/バージョン参照する形へ refactor が要る。だが 1 個目で完全な統治機構を作るより総コストは低い。
- **Phase 4 への落とし込み**: M4 を 2 ギャラリー化 (本決定の (a))。`propose` への "既存オントロジーを対象に map-into 提案する" モード追加は **Phase 5 候補** ((b)、Supercon 着手とセット)。

## 6.7 Ask + 来歴トレース (採択デモの中心)

「問い → 根拠付き回答 + 引用 + 来歴」を見せる、grounding の payoff を体現するビュー。ADR [`ontology-mapping-boundary-and-provenance.md`](ontology-mapping-boundary-and-provenance.md) の責務境界 (substrate=Read / アクション層=利用側) と来歴 2 層に準拠する。

### D9. 回答生成 LLM は core の外。UI は契約だけを叩く

- **Decision**: Ask の回答生成は **csv2rdf core API に入れない**。回答を組み立てるのは core の外の **消費層 (demo-agent)** で、UI はその 2 つの HTTP 契約だけを叩いて描画する。core の typed query tools は Claude 非依存のまま保つ (ADR §1/§5)。
- **契約** (UI が叩く。typed MCP tools は直接触らない):
  - `POST /demo/ask` — `{ question }` → `{ answer, citations[], notes[] }`。citation は `{ iri, kind, label, fields }`。notes はデータ品質注記 (例: 物理的にあり得ない ZT>3.5 を除外)。
  - `GET /demo/provenance?iri=<iri>` — → `{ iri, chain[] }`。chain は `curve → sample → paper → digitization → ingestion` の各 `{ step, iri, label, detail }`。
- **先行開発**: 契約に対する **fixture mock を UI 内 (`ui/src/demoApi.ts`)** に置き、`VITE_DEMO_MODE` で mock/live を切替。core の demo-agent 完成を待たずに描画を作り、完成後はエンドポイント差し替えだけで動く。**mock は fixture を返すだけで回答生成ロジックを持たない** (境界の遵守)。
- **Why**: 回答 LLM を core に入れると substrate が Claude 依存になり、別環境への移植性 (ADR の逆輸入性条件) と「substrate は Read」原則が崩れる。
- **Trade-offs**: UI とデモは demo-agent の契約に結合する。が、契約は薄く (2 endpoint)、mock で先行できるので結合コストは低い。

### 描画方針

- **引用カード**: `kind` ごとに色分け (PROV-DM 系: curve/sample/paper=green 系、digitization/ingestion=blue 系)、`label` + 主要 `fields` を表示。クリックで来歴トレースを開く。
- **データ品質注記 (notes)**: 回答の信頼性を示す一級要素として明示 (「あり得ない値を除外した」等)。grounding の誠実さがデモの説得力。
- **来歴トレース**: 鎖を縦/横のステップ列で描画。各ステップは PROV-DM の色 (activity=blue / entity=green) を踏襲。

### 境界の遵守 (このリポジトリは公開予定)

- 特定の下流製品の固有名はコード・コメント・docs に書かない。下流に触れる場合は **Graphium まで** (README 既出) か一般名「**利用側アプリケーション / 消費層**」で。
- 取り込み済みデータは sovereign (self-host・1 デプロイ=1 ラボ)。外向き経路は明示的に。

## 7. 重大な設計判断

### D4. 生成 ingester の実行は MVP では行わない (任意コード実行リスク)

- **Decision**: LLM が生成した `ingester.py` を **サーバが自動実行して取り込むことは MVP ではしない**。UI は artifacts のダウンロードと validate までを提供し、実際の Oxigraph 取り込みは「人間が ingester を確認 → 既存 watcher/upload の所定パスに配置」する運用にする (M2)。
- **Why**: 生成コードの無検証実行は **任意コード実行 (RCE) 脆弱性**そのもの。研究者が他人の/AI 生成のコードをワンクリックで server 上実行できると危険。
- **Alternatives**: サンドボックス実行 (別コンテナ・seccomp・ネットワーク遮断) — 安全だが構築コスト大、将来課題。AST allowlist で「rdflib + csv のみ」に制限 — 部分的緩和。
- **Trade-offs**: 一気通貫の自動取り込みは犠牲。だが安全側に倒すのが Design principle (sovereign) と整合。validate の T1-T8 + 人間レビューが gate。

### D5. 認証は MVP では最小

- **Decision**: MVP は認証なし (または環境変数の共有トークン 1 個)。
- **Why**: self-host 単一デプロイ・研究室内共有が前提で、マルチテナントはしない (Design principle)。
- **Open**: 研究室内で「誰が作った dataset か」を区別したい場合の簡易ユーザ識別は §9 で PI 確認。

### D6. デプロイ統合

- **Decision**: `compose.yaml` に `ui` サービスを追加。フロントは Vite で静的ビルド → FastAPI が静的配信 (単一オリジンで CORS 回避) もしくは nginx で配信。API は既存 api コンテナを拡張。
- **Trade-offs**: 単一オリジン配信は簡単だがフロントのホットリロードは開発時のみ別ポート。

### D7. LLM API キーはユーザ持ち込み (Graphium 流) [確定]

- **Decision**: サーバ共通キーは持たず、**各ユーザが自分の Anthropic API キーを UI で入力**する (Graphium と同様の鍵持ち込み方式)。propose/refine ジョブ起動時に鍵をリクエストで受け、サーバは **メモリ上 (リクエストスコープ) でのみ使用し、ログ・永続ストレージ・`jobs.jsonl` に一切残さない**。
- **Why**: コストが各自負担で持続可能。Design principle (sovereign / self-host) と整合し、運用者が全員分の課金を負わない。ユーザ確定事項。
- **セキュリティ要件**: 鍵が平文で流れるため **HTTPS 必須** (self-host の localhost / 社内 TLS)。ブラウザ側は sessionStorage 等に保持し、サーバはリクエスト処理中のみ保持してすぐ破棄。鍵をエラーメッセージや構造化ログに出さない。
- **Trade-offs**: 鍵入力 UX と「鍵を漏らさない」配線が要る。認証 (D5) は実質「鍵を持つ人が使える」で代替でき、別途ログインは MVP 不要。
- **Alternatives**: サーバ共通キー (運用者にコスト集中・却下)、両対応 (将来余地)。

## 8. MVP 実装計画 (チケット分解の素案)

- **M0**: ① api/ に step0 を path dependency 追加 + `/api/inspect` ② React+Vite 足場 + アップロード画面 ③ compose に ui サービス
実装順は **M0 → M1 → [Ask+来歴] → M4 → M2 → M3** (デモ優先・確定):

- **M0**: ① api/ に step0 を path dependency 追加 + `/api/inspect` ② React+Vite 足場 + アップロード画面 ③ compose に ui サービス
- **M1**: ④ propose ジョブ + SSE stream ⑤ proposal 逐次表示 UI (Mermaid を mermaid.js でレンダリング) ⑥ refine ⑦ materialize + artifacts DL ⑧ validate レポート表示
- **Ask + 来歴 (★デモ中心・前倒し §6.7/D9)**:
  - **D0**: 設計追記 + `ui/src/demoApi.ts` (契約型 + fixture mock + `VITE_DEMO_MODE` 切替) + デザイントークン (Crucible/provnote 由来の forest green palette / Inter / PROV-DM 色) を index.css/App.css へ
  - **D1**: Ask ビュー (質問 → `/demo/ask` mock → 回答 + 引用カード + データ品質注記)
  - **D2**: 来歴トレース (引用クリック → `/demo/provenance` mock → curve→sample→paper→digitization→ingestion の鎖を描画)
- **M4 (見せ場・前倒し・2 本立て §6.6/D8)**: ⑨ **Ontologies ギャラリー** (TBox クラス図のカード一覧 + 拡大) ⑩ **Mappings ギャラリー** (MIE+ingester を目的タグ付きで一覧、**目的タグを目立たせる**、Ontology 層と視覚的に分離)
- **M2**: ⑪ 確定 bundle を upload/watcher へ受け渡し (ingester は人手確認フロー)
- **M3 (最小)**: ⑫ jobs 一覧
- **後続**: graph stats / SPARQL エディタ (主役にしない・脱出ハッチ) / WebVOWL リッチ可視化
- **Phase 5 候補**: `propose` の map-into モード (既存オントロジーへ Reuse/Extend/Align/New を提案) + 物理ストレージ分離 (D8(b))。Supercon 投入とセット
- **並行**: 別 dataset (Supercon 等) を M1 で流して検証 (map-into 機構の実価値検証も兼ねる)

各段階の終わりに CI (ruff + pytest、フロントは型チェック + build) を緑に保つ。

## 9. Open Questions (実装前に確定したい / PI 確認)

1. ~~LLM API キーの所有者~~ ✅ 確定: **ユーザ持ち込み** (Graphium 流、D7)。
2. **認証/ユーザ識別** — D7 により「鍵を持つ人が使える」で MVP は代替。研究室内で「誰の dataset か」を区別したい場合のみ簡易識別を後続検討。
3. **生成 ingester 取り込みの将来形** — サンドボックス実行をどこまで投資するか (D4)。
4. ~~フレームワーク最終確認~~ ✅ 確定: **React + Vite + TypeScript** (FastAPI + SPA)。
5. ~~管理画面 (M3) の範囲~~ ✅ 確定: **M1 優先・管理は最小 (jobs 一覧のみ)**。Oxigraph 統計 / SPARQL は後続 Phase。
6. ~~公開範囲~~ ✅ 確定: **private ホスティング前提** (public 公開は当面しない)。認証は最小、外部公開向けセキュリティ強化は将来必要時に再検討。D7 の HTTPS 前提は private 運用でも (鍵を流すため) 維持。

---

## 次アクション

この draft にコメント・修正をいただいた上で確定し、M0 (足場) から実装に入る。§9 の 1 (API キー) と 4 (フレームワーク確定) は M0 着手前に決めたい。
