# Morph-KGC + RML/YARRRML 素振り (Phase 0.5)

設計プラン §10 / handoff §4.3 に基づく素振り。

> **問い**: starrydata の papers.csv にある **JSON 埋め込み列** (`author`,
> `issued`, `project_names`) を **宣言的 (YARRRML/RML)** に展開できるか?

## 環境

- パッケージ: `morph-kgc` (pip; 依存先に rdflib, pandas, ruamel-yaml ほか)
- 入力: `data/papers_100.csv` (100 papers の subset)
- Python: 3.11 (../.venv)

## 実行手順

```bash
cd ..
. .venv/bin/activate
uv pip install morph-kgc pyyaml

cd morph-kgc

# Run 1: プレーンな列だけ宣言的に変換 (author / issued は無視)
python -m morph_kgc config.ini
# -> knowledge-graph.nt: 1,279 triples (mapping.ttl, 1.1 秒)

# Run 2: FnO 経由で issued を年に変換しようとする
python -m morph_kgc config_json.ini
# -> KeyError: 'object_map' で Morph-KGC 内部破綻
#   (FnO サポートは partial で、grel:string_substring の構文では通らない)

# Run 3: pre-process で CSV を JSON 配列に展開してから RML を当てる
python preprocess_to_json.py data/papers_100.csv data/authors.json
python -c "import json; rs=[json.loads(l) for l in open('data/authors.jsonl')] if False else 0"  # skip
# (preprocess_to_json.py が JSON Lines -> JSON 配列を生成)
python -m morph_kgc config_authors.ini
# -> knowledge-graph-authors.nt: 2,336 triples (584 authors × 4 triples) 0.67 秒
```

## 計測結果

| Run | 入力 | 出力 triples | 所要時間 | 備考 |
|---|---|---|---|---|
| 1: CSV 直接 (プレーン列) | papers_100.csv | 1,279 | 1.1 s | author / issued / project_names を無視 |
| 2: FnO で issued 抽出 | papers_100.csv + mapping_json_attempt.ttl | (失敗) | - | `KeyError: 'object_map'` (Morph-KGC 内部のパース失敗) |
| 3: 前処理 JSON 経由 | authors.json (584 records) | 2,336 | 0.67 s | preprocess_to_json.py で Python 前処理が必要 |

参考: 同じ subset を Python rdflib で書き直した `csv_to_ttl.py` は **すべての列**
(author/issued/title/publisher/journal を含む) を扱って **3,715 triples** を出力。

## 検証項目の所感 (handoff §4.3)

- [x] **`morph-kgc` を `uv pip install morph-kgc` で導入**: 1 コマンドで完了。依存は十数個。
- [x] **starrydata の `papers.csv` の JSON 埋め込み列を YARRRML/RML で展開できるか試す**: 
  - **平易な列はできる** (SID / DOI / URL / title / volume / issue / page / publisher / container_title)
  - **JSON 埋め込み列は直接展開できない**。RML の `rml:referenceFormulation` は **logical source 単位**で固定 (CSV か JSONPath か XPath か SQL のいずれか 1 つ) なので、CSV の "セル内 JSON" をネスト iterate する書式が **標準に存在しない**。
  - **回避策 1: FnO (Function Ontology)**: `grel:string_substring` など。**Morph-KGC のサポートは partial** で、本素振りでは `mapping_json_attempt.ttl` を流したところ `KeyError: 'object_map'` で内部破綻した。idlab-fn 系の最小セットは動くらしいが、安定して使える保証がない。
  - **回避策 2: 前処理で CSV を分割**: papers.csv → authors.json + issued.json + project_names.json に Python で展開してから morph-kgc を当てる。**動く**が、「宣言的」のメリットの大半は失われる。前処理 Python が必須なら、そのまま Python rdflib で全部書いた方が短くデバッグしやすい。
- [x] **うまくいかない箇所がどこかを記録 (Python rdflib に倒す判断材料)**:
  - 上記の通り、**JSON 埋め込み列が登場した時点で「declarative-only」は実質不可能**。
  - starrydata は papers (author, issued, project_names) / samples (sample_info) / curves (x, y arrays) のすべてに JSON 列があり、**RML 単独で完結する経路がない**。
  - 一方で、starrydata 以外の "プレーンな CSV" (例: NIMS MDR の温度依存物性表のような) は RML/YARRRML で綺麗に書ける可能性が高い。

## 結論 (詳細は [`../../../docs/internal/phase05-decisions.md`](../../../docs/internal/phase05-decisions.md))

- **Phase 1 の starrydata ingester は Python (rdflib) で書く**。`csv_to_ttl.py` がほぼそのまま叩き台になる。
- **Morph-KGC は Phase 3 の汎用 CSV 対応で再評価**する。`manifest.yaml` ベースの schema 推論を Phase 3 で導入する際に、JSON 列を持たない平易な CSV であれば Morph-KGC + 生成 YARRRML が現実的な選択肢になる。
- **ハイブリッド**もあり得る: 「Python ingester は JSON 列だけ処理して中間 CSV/JSON を吐き、それを Morph-KGC が宣言的マッピングで RDF 化」。ただし Phase 1 では Python rdflib 単独で十分。
