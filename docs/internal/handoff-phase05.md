# Claude Code への Handoff — Phase 0.5（依存検証）

このドキュメントは、csv2rdf-mcp プロジェクトを Claude Code に引き継ぐための指示書です。Claude Code のセッションでこのファイルを最初に読んでもらうことを想定しています。

---

## 0. ゴール

1. GitHub に `csv2rdf-mcp` リポジトリを新規作成する（Apache-2.0、public）
2. 設計プラン §10 Phase 0.5「依存技術の素振り」を実施する
3. 結果を `docs/internal/phase05-decisions.md` にレポートし、採用判断を確定させる
4. Phase 1（本実装）には **このセッションでは進まない**。Phase 0.5 完了でセッション終了

---

## 1. Source of truth

**設計プラン本体**: `/Users/masayakumagai/Documents/Claude/Projects/AI for Science/csv2rdf-mcp_design_plan.md`

最初に必ず Read で全体を読むこと。特に以下のセクションを把握:

- §0「設計の前提（ソブリン制約）」と §0.1「マルチスコープ運用」
- §3「アーキテクチャ」
- §4「RDF スキーマ設計」と §4.0「IRI 永続化戦略」
- §5「CSV → RDF 変換の実装」
- §10「段階的ロードマップ」（特に Phase 0.5 の項）
- §11「既知のリスクと未確定事項」

設計プランと矛盾する判断をする必要が出た場合は、理由を `docs/internal/decisions.md` にメモを残してから進めること。

---

## 2. 制約・前提

| 項目 | 値 |
|---|---|
| GitHub user | `m-kumagai` |
| リポジトリ名 | `csv2rdf-mcp`（変える場合はユーザに確認） |
| 可視性 | public |
| License | Apache-2.0 |
| 言語 | 応答・コメント・コミットメッセージ・ドキュメントは日本語可（README は将来 OSS 公開を見据えて英語推奨） |
| Python | 3.11 以上、`uv` を推奨 |
| パッケージマネージャ | `uv`（Python）、`pnpm`（もし将来 TS 側を足すとき） |
| Lint / 型 | `ruff`, `mypy`（strict） |
| Docker | Compose v2、`docker compose` |

---

## 3. Step 1 — リポジトリ作成

以下の構造で空のリポジトリを作る。

### 3.1 GitHub にリポジトリを作成

```bash
gh repo create m-kumagai/csv2rdf-mcp \
  --public \
  --license apache-2.0 \
  --description "CSV → RDF → SPARQL/MCP. PROV-O first-class. Self-hostable, sovereignty-first." \
  --clone
cd csv2rdf-mcp
```

### 3.2 初期ディレクトリ構造

設計プラン §2 を踏襲しつつ、Phase 0.5 段階では以下の最小構造でよい:

```
csv2rdf-mcp/
├── README.md                  # Quickstart（英語）
├── LICENSE                    # Apache-2.0（gh が生成済み）
├── .gitignore                 # Python / Docker / .DS_Store / data/
├── .github/
│   └── workflows/ci.yml       # ruff / mypy / pytest（中身は Phase 1 で埋める）
├── docs/
│   ├── architecture.md        # スタブのみ（Phase 1 で埋める）
│   └── internal/              # .gitignore せずコミットする方針
│       ├── design-plan.md     # 設計プランをコピー
│       ├── handoff-phase05.md # このファイルをコピー
│       ├── phase05-decisions.md   # 本タスクの最終成果物
│       └── decisions.md       # 設計プランから逸脱した判断のログ
├── ingest/
│   └── pyproject.toml         # スケルトンのみ
├── data/
│   └── .gitkeep
└── experiments/
    └── phase05/               # 素振りのコード・ログをここに置く
        ├── togopackage/
        ├── oxigraph/
        └── morph-kgc/
```

### 3.3 初期 commit

```
[infra] リポジトリ初期化、Apache-2.0 + 基本ディレクトリ構造
[docs] 設計プラン handoff を docs/internal/ に取り込み
```

---

## 4. Step 2 — Phase 0.5 素振り

設計プラン §10 Phase 0.5 と §11「致命的リスク」を実装の起点として、以下 3 つを実際に動かして比較する。**各素振りは独立したサブディレクトリ**（`experiments/phase05/*`）で行い、Dockerfile / compose / 実行ログ / 計測結果をコミットすること。

### 4.1 togopackage 素振り（1〜2 時間）

設計プランの §10 で挙げた確認事項:

- [ ] `ghcr.io/dbcls/togopackage:latest` を pull して起動できるか
- [ ] 公式 README の sample データで `/sparql` が叩けるか
- [ ] `config.yaml` で **複数 source** を扱えるか（実際に 2 source 並列で登録してみる）
- [ ] **reload / 部分再インデックス** API があるか（無ければ docker compose restart になる）
- [ ] togomcp の **MIE YAML 書式**（`togo-mcp-admin` でスケルトン生成し中身を確認）
- [ ] **LICENSE ファイル**の存在と内容

starrydata の **小さな subset**（例: `papers.csv` の先頭 100 行を切り出した `papers_100.csv`）を使い、Python rdflib で簡易に Turtle に変換して投入してみること。

### 4.2 Oxigraph 素振り（30 分〜1 時間）

- [ ] `ghcr.io/oxigraph/oxigraph` を pull → 起動
- [ ] 同じ subset の Turtle を `oxigraph load` で投入
- [ ] SPARQL 1.1 Update で **追記が効くか**（`INSERT DATA { ... }`）
- [ ] 同じ subset の追加投入で **再構築不要**で済むか（差分追記）
- [ ] 検索性能（ざっくり LIMIT 100 のクエリで体感）

### 4.3 Morph-KGC + YARRRML 素振り（1〜2 時間）

- [ ] `morph-kgc` を `uv pip install morph-kgc` で導入
- [ ] starrydata の `papers.csv` の **JSON 埋め込み列**（`author`, `issued`）を YARRRML で展開できるか試す
- [ ] うまくいかない箇所がどこかを記録（Python rdflib に倒す判断材料）

### 4.4 比較メトリクス

3 つの素振りに共通で以下を計測・記録:

| 項目 | 計測方法 |
|---|---|
| 初回ロード時間 | `time` で wall clock |
| 100 行 subset の SPARQL レイテンシ（5 クエリの平均） | スクリプトで計測 |
| 追記コスト（既存に 100 行追加） | wall clock |
| Docker image サイズ | `docker images` |
| 設定の複雑度 | 行数と感想を 2-3 行 |

---

## 5. Step 3 — `docs/internal/phase05-decisions.md` を書く

以下の構造で。**結論を冒頭に書く**こと。

```markdown
# Phase 0.5 採用判断

## 結論

- **SPARQL backend**: <Oxigraph | QLever | Fuseki> を採用
- **Ingester**: <Python rdflib | Morph-KGC + YARRRML | ハイブリッド> を採用
- **togopackage**: <採用継続 | 部分採用 | 撤退> 。理由は後述
- **次の Phase 1 への影響**: <設計プランから逸脱する点があれば列挙>

## 比較結果

（4.4 のメトリクス表をそのまま貼る）

## 詳細な検証メモ

### togopackage
- 起動できたか: ...
- 複数 source: ...
- reload API: ...
- MIE 書式の所感: ...
- LICENSE: ...

### Oxigraph
- ...

### Morph-KGC
- ...

## 採用判断の根拠

- なぜそのバックエンドか（3 つの観点で）
- なぜその ingester 方式か（特に JSON 列対応の体感）
- togopackage を切る判断をした場合、何を代わりに使うか

## 設計プランへの修正提案

設計プラン本体（design-plan.md）を更新すべき箇所:
- §3 …
- §5 …
- §6 …

## 残ったリスク

- ...
```

---

## 6. Acceptance criteria

このセッションの完了条件:

- [ ] GitHub に `m-kumagai/csv2rdf-mcp` が public で存在し、Apache-2.0 LICENSE がある
- [ ] README.md に最低限の Quickstart（依存・clone 手順・「Phase 0.5 を経て決定中」の旨）がある
- [ ] `docs/internal/design-plan.md` と `docs/internal/handoff-phase05.md` がコミットされている
- [ ] `experiments/phase05/{togopackage,oxigraph,morph-kgc}/` にそれぞれ動かしたコード・compose・実行ログが残っている
- [ ] `docs/internal/phase05-decisions.md` が上記 §5 の構造で書かれており、**冒頭に結論が明記されている**
- [ ] commit history が論理的に分かれている（`[infra]` `[docs]` `[experiment]` 等のプレフィックス）
- [ ] starrydata の小さな subset が **少なくとも 1 つのバックエンド**で実際に RDF 化されて SPARQL で引けたログがある
- [ ] push 済み（main ブランチに直接でなく、`infra/phase05-bootstrap` 等のブランチ＋ PR で）

---

## 7. Out of scope（このセッションでは触らない）

- Phase 1 以降の本実装（starrydata 全件 ingest, MCP gateway 本体, Vault, Graphium 連携）
- w3id.org への PR
- starrydata 全件のロード（時間を食う、subset で十分）
- CI の本格設定（スケルトンの YAML を置くだけ）
- 命名変更（`csv2rdf-mcp` の名前を変えたい場合はユーザに確認）

---

## 8. Tips

### worktree 運用
このリポジトリは Crucible / Graphium とは別ですが、ユーザの作業習慣（`Crucible/CLAUDE.md`）に合わせて main 直接コミットは避け、`infra/phase05-bootstrap` のようなブランチで作業して PR を出すこと。

### Skill 活用
Claude Code の skill が利用可能なら、特に以下が役立つかも:
- skill-creator（必要なら csv2rdf 特化のスキルを後で作る）
- 既存の docker / python 系スキル

### 困ったとき
- 設計プランで矛盾を見つけたら `docs/internal/decisions.md` に記録
- 大きな判断は **ユーザに確認**してから進める（特に命名・License・公開範囲）

---

## 9. 終了時のメッセージ

セッション終了時、ユーザに以下を伝える:

1. GitHub リポジトリの URL
2. Phase 0.5 の結論（採用バックエンド / ingester）
3. 設計プランに修正提案がある場合はその要約
4. 次のセッションで Phase 1 のどこから着手すべきかの提案（papers ingester から、など）
