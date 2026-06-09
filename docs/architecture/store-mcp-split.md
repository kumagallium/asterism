# ADR: Store / MCP split + exposure profile (controlled exposure)

状態: **採択** — 露出プロファイル（controlled exposure 軸）を実装済み。トポロジ B の
完全運用（リモートストアのセキュリティ作り込み・ingest 分離）は本 ADR を設計の正と
して段階的に進める。

関連: [`option-b.md`](option-b.md)（同居スタック=トポロジ A の役割分担）/
[`product_direction`](../../README.md)（決定論・型付きを主役、生 SPARQL は escape）。
出所: ARiSE 提案（DBCLS/TogoMCP と構造を揃える）。

## 1. 文脈

プラットフォーム上の MCP を **「薄い型付きクエリ窓口（MCP フロント）＋その後ろのストア」**
という DBCLS/TogoMCP と同じ構造に揃え、**ストアの所在を配備時に選べる**ようにする。

- **トポロジ A（同居）**: ストアをフロントと同じ場所に置く小規模オンランプ。
  CSV だけの個人でも `docker compose up` 一発（[`compose.yaml`](../../compose.yaml)）。
- **トポロジ B（リモートストア）**: ストアを提供者の機関環境（国内・主権が強い）に置き、
  フロントだけをプラットフォームに登録（[`compose.mcp-front.yaml`](../../compose.mcp-front.yaml)）。

両者の違いは原則 **`CSV2RDF_OXIGRAPH_URL` の向き先だけ**。MCP フロントはすでにストアを
URL で参照しており（volume 非依存）、トポロジ切替に大改造は不要。

## 2. 3つの直交する軸（混同しない）

| 軸 | 何で解くか | 本 ADR での扱い |
|---|---|---|
| 国内完結（場所） | デプロイ先 / トポロジ | A/B の compose（運用） |
| custody・主権（誰が握るか） | 誰がストアを運用するか | B では提供者が運用（運用） |
| **controlled exposure（抽出）** | **露出ツール集合 + read/write 分離** | **本 ADR で実装（コード）** |

新規にコードが要るのは **抽出制御** だけ。場所と主権は運用・デプロイで解ける。

注意（過大主張の回避）:
- **国内完結は runtime（型付きツール・Ask は LLM-free・推論はさくら AI Engine）に限る。**
  設計時（propose/refine）は Anthropic クラウド API を使うため、design-time の経路は
  別に語る（提供者が経路を選択／さくら AI Engine 等への差し替えは将来課題）。
- 本 ADR は **store relocation**（ストアを1個移す）であって **federation**
  （機関を跨いだストア横断 join）ではない。FROM-merge は「1フロント→1エンドポイントが
  全 canonical graph を見る」前提。cross-institution join は SPARQL federation
  （`SERVICE`）の別課題。

## 3. 決定: 露出プロファイル（exposure profile）

製品方針「決定論・型付きを主役、生 SPARQL は escape」を配備単位のスイッチにする。

- **型付きツール**（`datasets/*/query_tools.yaml` 由来＋`template_curve_fetch` /
  `provenance_of` / `schema_summary`）は **常に公開**。人間が vet したテンプレートに
  型安全に引数を束縛するだけで、任意 SPARQL を流せない（`query_tools.py`）。
- **生 SPARQL の escape**（任意 read-only クエリ）は **配備で切替**。機微ストア（B）は
  これを閉じ、型付きツールのみを公開＝根こそぎ抽出の面を無くす。

### スイッチ（config）

単一の環境変数。既定は **開**（後方互換／トポロジ A）。

```
ASTERISM_EXPOSE_RAW_SPARQL=false   # 型付きのみ（トポロジ B / 機微）
```

falsy（`0`/`false`/`no`/`off`・大小無視）で無効、それ以外（未設定含む）は有効。
実体は [`asterism.exposure.raw_sparql_enabled`](../../ingest/src/asterism/exposure.py)。

### 一括で閉じる4つの口（半閉じ＝裏口を防ぐ）

生 SPARQL は複数面に露出している。**1スイッチが全部を殺す**。

| 生 SPARQL の口 | 場所 | OFF の挙動 |
|---|---|---|
| MCP `sparql_query` ツール | `asterism_mcp.server.build_server` | ツール未登録 |
| Ask の LLM ツール `run_sparql` | `demo-agent/app.py` `_llm_answer` | LLM に渡さない（型付きのみ＋プロンプト明示） |
| demo-agent passthrough | `POST /demo/sparql` | 403 |
| api relay | `POST /api/sparql` | 403 |

> togomcp の generic `run_sparql` は**この型付きフロントには存在しない**。togomcp は
> 別サービス（汎用 escape サーフェス）であり、機微配備では **togomcp を登録せず、型付き
> フロント（:8002）だけを登録**する＝[`compose.mcp-front.yaml`](../../compose.mcp-front.yaml)。

### 型付き集合の拡張（ボトルネック対策）

「型付きしか答えられない」制約は、提供者が `query_tools.yaml` にテンプレートを足して拡張
できる。**起草は AI 可・承認だけ人間**（既存の draft→人間ゲート→canonical 昇格と同型）。
安全な文脈で escape を ON にしておけば、LLM が成功させた SPARQL が新しい型付きツールの
候補になる（escape を「型付きツールの鉱脈」として使う）。`query_tools.yaml` は実行コード
でなく vet 済みデータなので、no-codegen 原則と矛盾しない。

## 4. セキュリティ（リモートストア時）

Oxigraph 自体の認証は薄い。リモートの SPARQL endpoint は **TLS＋認証＋ネットワーク制限**
（リバースプロキシ／トンネル）の前段保護を推奨構成とする。フロントは **読むだけ**、
書き込み（ingest）は提供者側だけが到達できるようにし、read/write をネットワーク層でも分離
（アプリの read-only 契約＝`/api/sparql` と二重）。

## 5. 検証

- 単体: `asterism.exposure`（既定開・falsy で閉）／MCP は OFF で `sparql_query` のみ落ち
  型付きは残る／型付きの結果は ON/OFF で同一／api・demo-agent は OFF で 403。
- デモ: [`scripts/demo_exposure_profile.py`](../../scripts/demo_exposure_profile.py)
  ＝同一フロントを ON/OFF で建て、ツール面の差分と `property_ranking(ZT)` の答えが
  ON==OFF であることを表示（mock 既定・`CSV2RDF_OXIGRAPH_URL` 設定で実ストア）。

## 6. スコープと残

- **本 ADR で実装**: 露出プロファイル（4面ゲート）＋`compose.mcp-front.yaml`＋デモ。
- **設計として記述（実装は実機関パートナー時）**: リモートストアのハードニング詳細、
  ingest（書込）の機関側分離の運用、中間モード「ガード付き escape」（行数上限／グラフ
  allowlist／一括ダンプ禁止）。
- **別課題**: env 名の `CSV2RDF_*`→`ASTERISM_*` 統一（新規 `ASTERISM_EXPOSE_RAW_SPARQL`
  は Asterism 名で導入済）／cross-institution federation。

## 7. 露出スイッチの UI 上の住処（決定・実装は将来）

**決定**: 露出 on/off の最終的なユーザー接点は **Asterism 発の「外部公開（publish）」UI**
に置く。理由と前提:

- **DB ユーザー（データ提供者）に Crucible 画面を見せない。** Crucible は build/deploy/
  SSE 公開を担う**裏方インフラ**であり、提供者は Asterism の UI だけを触る。設計→取り込み
  →昇格の延長で「外部に公開」する。
- したがって publish は **Asterism → Crucible のデプロイ橋渡し**になる: Asterism backend が
  Crucible の deploy/登録 API を呼び、`ASTERISM_EXPOSE_RAW_SPARQL` を**ユーザー選択値**で
  立て、ストア URL を指して front をデプロイ → 返る MCP/SSE エンドポイントを UI に表示。
  現状の [`crucible-registration.md`](crucible-registration.md) は URL 手貼りの**手動レシピ**
  ＝プログラム連携（Crucible 側が deploy API を出す前提）は**未実装・将来課題**。
- 本 ADR で実装した env スイッチは、その publish UI が立てる**メカニズム（土台）**。
  スイッチがユーザーに見えるのは **publish の瞬間だけ**。プライベート本体 UI（SPARQL
  ページ・ホーム統計）は常時 ON のまま＝本体 UI は触らない（§3 と矛盾しない）。

**2つの「公開」を混同しない**: 内部の「昇格（promote: draft→canonical・ストア内で引用可能化）」
と、外部の「publish（MCP フロントを外部に出す）」は別軸。露出スイッチは **publish 側**にだけ
紐づく。

**未決の設計分岐＝公開の単位（per-dataset vs per-front）**:

- **per-dataset 公開（理想・ユーザー直感）**: 「このデータセットを型付きのみで外部公開」。
  実装は重め — front を複数立てる or 1 front が dataset スコープでフィルタし、スイッチを
  「公開レコード（dataset×公開先×ポリシー）」の属性へ格上げ。
- **per-front 公開（最小・現状）**: ストアまるごと 1 ポリシー（= 今の env スイッチ）。

[pref: 実装コスト度外視・プロダクト理想] に従えば **per-dataset 公開が目標**。着手時に
per-dataset/per-front を確定する。詳細は ROADMAP の「Asterism→Crucible publish UI」
ワークストリーム参照。
