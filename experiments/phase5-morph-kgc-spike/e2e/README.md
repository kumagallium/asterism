# 設計 → Ask の連結（Phase 5 スパイク）

ワークベンチで「設計」したものが、Ask でそのまま使えることを、**安全な宣言経路**で end-to-end に実証する。

```
設計  : mappings.rml.ttl（宣言的マッピング）+ udfs.py（検証済み関数）
        ↓  ← 生成コードなし。Morph-KGC が解釈実行（RCE 面なし）
substrate: Morph-KGC → RDF（papers/samples/curves。同じ IRI・述語）
        ↓
Ask   : property_ranking（typed tool）+ demo-agent の回答整形
        ↓
回答  : 根拠（引用 IRI）＋ 来歴（curve → sample → paper）付き
```

## 何を証明したか

- 手続き型 ingester（Python）を**書かずに**、宣言マッピングと検証済み関数だけで
  starrydata の RDF（6686 triples: Curve 1091 / Paper 40 / Sample 286）を生成できる。
- 「宣言で書けない難所」だけを関数に寄せる方針が成立する:
  - `parse_date` … 雑多な日付 → ISO（既存 `parse_issued` を再利用）
  - `json_array_max` … curve の x/y JSON 配列セル → 最大値（既存 `parse_float_array` を再利用）
- 生成 RDF は手続き型 ingester と**同形**なので、Ask 側（typed tools / demo-agent）は
  無改造でそのまま答える。回答も実データの値で一致（クラスレート ZT ≈ 1.45）。

## 実行

```bash
# 1. 実データから小さな seed を作る（ライセンス元データはリポジトリ外）
python scripts/make_demo_subset.py --src ../starrydata_dataset --n-papers 40
# 2. 設計 → substrate → Ask を一気通貫で
python experiments/phase5-morph-kgc-spike/e2e/e2e_design_to_ask.py
```

## ファイル

| ファイル | 役割 |
|---|---|
| `mappings.rml.ttl` | 設計成果（宣言）。将来 step0 が出力する RML 像 |
| `udfs.py` | 検証済み関数ライブラリ v0（FnO で呼ばれる）。既存 vetted 実装の薄い露出 |
| `e2e_design_to_ask.py` | 設計 → substrate → Ask を通すランナー（実証・回帰） |

## このスパイクが示す「次に製品化すべきもの」

1. **関数ライブラリ v0 を core（`asterism`）へ昇格** — `parse_date` / `json_array_max` ほか
   （`sanitize_iri` / `qudt_quantity_iri` / `qudt_unit_iri` / MP PoC の `normalize_host`）を
   1 つの検証済みモジュールに集約。手続き経路と宣言経路が同じ関数を共有する。
2. **step0 が RML を出力** — 設計ステップが Python ingester に加えて（最終的には代わりに）
   宣言的 RML を出力。人間が承認するのは宣言成果になる。
3. **ワークベンチの materialize アクション（人間ゲート）** — 承認済み RML を substrate に
   流して Oxigraph に載せる。そこに Ask が答える。
```
