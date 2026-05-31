# Phase 4 — Web UI 設計 (研究者向けスキーマ設計レビュー + データ管理)

> ステータス: **draft (合意待ち)**。Phase 1-3 と同じく、実装前に設計を凍結するための文書。
> 各設計判断は Decision / Why / Alternatives / Trade-offs で記述し、未決事項は §9 Open Questions に集約する。

## 0. 背景

Phase 3 で「CSV → AI がスキーマ案 → 人間レビュー → 検証」の **頭脳 (step0 の 6 CLI)** は完成し、dogfood で end-to-end 動作した。しかし触る手段は CLI のみで、**GUI が無い**。本 Phase は step0 を「他の研究者が使える Web UI」として包み、あわせて rdf 化済みデータの管理画面を提供する。

## 1. 対象ユーザと前提

- **主対象**: 熊谷さん以外の研究者 (NIMS / 共同研究者など)。CLI や Python に不慣れでも、自分の CSV からスキーマ案を作り、対話レビューで詰められること。
- **デプロイ前提**: Design principle 「self-hostable, single deployment (`docker compose up`)、マルチテナント SaaS ではない」を踏襲。**1 デプロイ = 1 研究室/個人**。研究室内で複数人が同じインスタンスを共有する程度。
- **主軸機能 (a)**: スキーマ設計の対話レビュー (propose → refine)。
- **副次機能 (b)**: rdf 化済みデータの管理 (取り込み履歴 / Oxigraph 統計 / SPARQL)。

## 2. スコープ (MVP 段階分け)

UI は大物なので段階に切る。各段階は単独で価値が出る単位。

| 段階 | 内容 | 価値 |
|---|---|---|
| **M0 足場** | FastAPI に step0 をライブラリ統合。`/api/inspect` (同期)。React 足場 + CSV アップロード画面。compose に `ui` サービス | 配線が通る |
| **M1 設計レビュー core (★主軸)** | propose を SSE streaming 表示 → 提案 Markdown 表示 → refine コメント入力 → materialize → validate (8罠) 結果表示 → 4 artifacts ダウンロード | 研究者が対話レビューを回せる |
| **M2 取り込み連携** | 確定した bundle を既存 upload/watcher 経由で Oxigraph に取り込む (※ ingester は人手確認後、§7 D4) | 設計→取り込みが一気通貫 |
| **M3 データ管理 (副次)** | 取り込み履歴 (`/jobs`)、Oxigraph 統計 (triple 数 / 名前付きグラフ)、SPARQL クエリ画面 | rdf 化済みデータの可視化・管理 |

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

## 6. データ管理画面 (M3, 副次)

- 取り込み履歴: 既存 `/jobs` (jobs.jsonl) を表で表示。
- Oxigraph 統計: `SELECT (COUNT(*) AS ?c)` と名前付きグラフ一覧を `/api/graph/stats` 経由で。
- SPARQL: read-only クエリ実行 + 結果テーブル。`/api/sparql` がサーバ側で Oxigraph に中継 (CORS/認可を一元化)。

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

## 8. MVP 実装計画 (チケット分解の素案)

- **M0**: ① api/ に step0 を path dependency 追加 + `/api/inspect` ② React+Vite 足場 + アップロード画面 ③ compose に ui サービス
- **M1**: ④ propose ジョブ + SSE stream ⑤ proposal 逐次表示 UI ⑥ refine ⑦ materialize + artifacts DL ⑧ validate レポート表示
- **M2**: ⑨ 確定 bundle を upload/watcher へ受け渡し (ingester は人手確認フロー)
- **M3**: ⑩ jobs 一覧 ⑪ graph stats ⑫ SPARQL エディタ
- **並行**: 別 dataset (Supercon 等) を M1 で流して検証

各段階の終わりに CI (ruff + pytest、フロントは型チェック + build) を緑に保つ。

## 9. Open Questions (実装前に確定したい / PI 確認)

1. **LLM API キーの所有者** — サーバ共通キー (運用者負担・コスト集中) か、ユーザが自分のキーを UI で入力 (持ち込み) か。コストと鍵管理に直結。
2. **認証/ユーザ識別** — 研究室内共有で「誰の dataset か」区別が要るか。要れば簡易ログイン。
3. **生成 ingester 取り込みの将来形** — サンドボックス実行をどこまで投資するか (D4)。
4. **フレームワーク最終確認** — React + Vite + TS で確定か (D1/D5 はこれ前提)。
5. **管理画面 (M3) の優先度** — 「rdf 化したものの管理」をどこまで MVP に含めるか (最小は jobs 一覧のみ)。
6. **公開範囲** — このインスタンスは研究室内のみか、将来的に外部公開もありうるか (認証・セキュリティ要件が変わる)。

---

## 次アクション

この draft にコメント・修正をいただいた上で確定し、M0 (足場) から実装に入る。§9 の 1 (API キー) と 4 (フレームワーク確定) は M0 着手前に決めたい。
