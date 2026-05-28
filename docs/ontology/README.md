# Starrydata Ontology (Phase 1)

csv2rdf-mcp の Phase 1 で **starrydata の RDF 表現**を支えるオントロジー。

3 段階で可視化・検証できる:

| Level | 入力 | ツール | 用途 |
|---|---|---|---|
| **1. Mermaid class diagram** ([diagram.md](diagram.md)) | Markdown | GitHub / VSCode / 任意の Markdown viewer | **一目で全体構造**を把握 (このディレクトリの README から見える) |
| **2. RDFS/OWL TBox** ([starrydata.ttl](starrydata.ttl)) | Turtle | Protégé / WebVOWL / rdflib | クラス・プロパティ定義の **正規ファイル**。SHACL/ShEx 検証や rdfs:subClassOf 推論の根拠 |
| **3. WebVOWL レンダリング** | starrydata.ttl を upload | [service.tib.eu/webvowl/](https://service.tib.eu/webvowl/) | **インタラクティブな円形グラフ**で他人と共有 |

## Phase 1 のスコープ

**ABox (instance) のみ Phase 1 で本格運用**。本ディレクトリの TBox (`starrydata.ttl`) は Phase 1 後半に追加した **後付け定義**で、ABox を読み解くための reference として機能する。Phase 2 で:

- ShEx 検証 (rdf-config 経由) を CI に組み込み
- QUDT QuantityKind / Unit を `sd:propertyY` / `sd:unitYString` の正規 IRI として導入
- EMMO や Materials Project などの上位 ontology に subclassOf を張る

## 編集とレビューのフロー

- **新しいクラス/プロパティを足す** → `starrydata.ttl` に追記 → diagram.md の Mermaid を手動更新 (Phase 2 で auto-gen ツール導入予定)
- **ingester の変更で TBox に反映が要る** → 同じ PR 内で 3 ファイル (ingester + ttl + Mermaid) を揃える
- **AI からの検索精度**は MIE の `shape_expressions` が握っている。TBox はあくまで人間/外部ツール向け

## 関連

- [`../architecture/option-b.md`](../architecture/option-b.md) — 全体アーキテクチャ
- スキーマ設計の前提は本リポジトリの README と [`option-b.md`](../architecture/option-b.md) §3 を参照
- [`../../data/togomcp/mie/starrydata.yaml`](../../data/togomcp/mie/starrydata.yaml) — togomcp が AI に渡す MIE (ShEx を含む)
