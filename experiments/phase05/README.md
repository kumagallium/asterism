# Phase 0.5 — 依存技術の素振り

設計プラン §10 Phase 0.5 / handoff §4 に沿った独立サブディレクトリ群。

- [`togopackage/`](togopackage) — `ghcr.io/dbcls/togopackage:latest` を起動し、複数 source / reload API / MIE YAML / LICENSE を確認
- [`oxigraph/`](oxigraph) — `ghcr.io/oxigraph/oxigraph` を起動し、SPARQL 1.1 Update での部分追記を確認
- [`morph-kgc/`](morph-kgc) — `morph-kgc` を pip install し、starrydata papers の JSON 埋め込み列を YARRRML で展開できるか試す

成果物は各サブディレクトリ配下の `README.md` / `compose.yaml` / `run.log` 等に残し、最終結論は [`../../docs/architecture/phase05-decisions.md`](../../docs/architecture/phase05-decisions.md) にまとめる。

## 共通 subset

starrydata の全件 (320 MB) はロード時間を食うため、`papers.csv` の先頭 100 行を切り出した小さな subset (`data/papers_100.csv` および `data/papers_100.ttl`) を使う。subset 生成スクリプトは [`scripts/make_subset.py`](scripts/make_subset.py) を参照。
