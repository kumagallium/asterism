# ワークベンチ materialize の人間ゲート（#15 設計メモ）

決定母体: [`phase5-declarative-substrate.md`](phase5-declarative-substrate.md)（§4 関数ライブラリ・§5 統治）/ [`step0-rml-emission.md`](step0-rml-emission.md)（#14 RML 出力）
status: 設計確定（決定事項あり）→ 実装は段階的（backend → api → UI）

> **改訂（2026-07-21）**: D1（materialize と投入の 2 段人間ゲート）は [`kantan-mode-two-tier-ux.md`](kantan-mode-two-tier-ux.md) K3 により**かんたんモードでは自動連結に改訂**。D1 制定時に投入承認が守っていたリスク（未検証 RML の実行・引用面の汚染）は、その後の 422 ハードゲート（実行前検証）・draft 隔離+promoted フラグ・rollback finally 化・孤児 version graph 回収で構造的に消えたため。**詳細モードの明示「投入」ボタンと D2–D4 は不変**。人間ゲートの本体は「行の数えかた・列の意味・公開」の 3 問に移る。

## 0. 何を閉じるか

Phase 4 で「ワークベンチで設計したもの → Gallery → Ask」が**未結線**だった。#14 で step0 が宣言 RML を出せるようになった（propose §RML 生成 → materialize 抽出 → T9 閉集合検証）。#15 は、**人間が承認した RML を実際に実行して RDF を生成し Oxigraph に載せ、Ask が消費する**ところまでを繋ぎ、authoring → catalog → consumption のループを閉じる。

実行は宣言経路のみ（Morph-KGC が RML を解釈）。生成コードは走らない＝RCE 面なし。呼べる関数は閉じた Tier 0 集合（`asterism.functions`）だけで、step0 の T9 がそれを検証済み。

## 1. 決定事項

| # | 論点 | 決定 | 理由 |
|---|---|---|---|
| D1 | ゲートの粒度 | **2 段**: ① `materialize`＝ドラフト保存（RML をレジストリに、実行しない）/ ② 明示的な「Oxigraph へ投入」承認アクションで初めて実行 | 「人間ゲート」に忠実。人間は RML を見て承認してから実行。誤投入を防ぐ |
| D2 | 投入先の隔離 | **draft 専用 named graph に隔離**（`.../graph/draft/{dataset_id}`）。Ask（引用できる事実）は既定で **canonical のみ**参照 | 未レビュー値が引用面を汚さない。citable-facts 方針（[product 方向性](product_direction_citable_facts.md)）を守る |
| D3 | Morph-KGC 実行プロセス | **api プロセス内（`asyncio.to_thread`）でまず実装**。将来は別ワーカーが理想 | 後から変更可能。1 リクエスト 1 materialize で負荷は軽微。最小構成で縦串を通す |
| D4 | source 解決の thread 安全性 | `chdir` を使わず **`rml:source` を絶対パスに書き換え**てから Morph-KGC に渡す | `chdir` はプロセス全体の状態でスレッドプールと競合する。絶対化なら無害 |

## 2. データフロー（縦串）

```
[ワークベンチ ④materialize]  RML をレジストリに保存（ドラフト・未実行）
        │
        ▼  ← ②「投入」承認ボタン（人間ゲート）
[api]  persist 済み CSV + RML を取得
        │  absolutize_rml_sources（chdir 回避）
        ▼
[substrate]  Morph-KGC が RML を解釈実行（Tier 0 関数のみ）→ rdflib.Graph
        │
        ▼  post_turtle_bytes(payload, graph_iri=draft/{id})
[Oxigraph]  draft named graph に投入（隔離）
        │
        ▼  GRAPH ?g UNION（MCP tools は対応済）だが Ask は既定 canonical のみ
[Ask]  （昇格後に）draft も引用対象に
```

## 3. draft → canonical 昇格と「既存オントロジーとのマージ」

ここが運用上の肝で、隔離（D2）はこの昇格パスを安全にするための前提。**「ワークベンチ投入＝即引用可能」にしない**ことで、間に**人間のアラインメント判断**を挟める。

昇格時に起きること（[D8 の Reuse/Extend/Align/New スペクトラム](phase4-ui-architecture.md) と接続）:

1. **語彙の突き合わせ（alignment）**: draft グラフが使う述語・クラスを、canonical の共有オントロジー（TBox）と照合する。
   - **Reuse**: 既存語彙にそのまま乗る（例: `schema:name`, `qudt:*`）→ そのまま昇格
   - **Align**: 別名・同義の述語を既存へ写像（`owl:equivalentProperty` / `skos:exactMatch` を貼る or マッピング修正）
   - **Extend**: 既存クラスのサブクラス・新プロパティを**共有 TBox に追加**（これは Tier 0 関数追加と同じく**信頼アンカーが一度 vet** する行為＝ガバナンス点）
   - **New**: 既存に無い概念。新規 mint（同上、vet 対象）
2. **IRI 衝突・重複の解決**: draft と canonical で同じ実体（同じ paper/sample）が別 IRI になっていないか。複合キー戦略（T1）で同形 IRI なら Oxigraph の set 意味で自然にマージ。ズレていれば写像か IRI 規則の修正。
3. **来歴の付与**: 昇格は「この draft を canonical に取り込んだ」という PROV 活動（`prov:wasGeneratedBy` / ingestion activity）として記録。誰が・いつ・どの RML から、を辿れる。

**重要な切り分け**: マージは**グラフのコピーではなく、語彙の一致を確認してから canonical グラフに同じ IRI で載せ直す**操作。隔離された draft があることで、「マージ前に差分を見て、語彙のズレを直し、承認する」という安全な手順が踏める。これが無いと「投入した瞬間に語彙の不整合が canonical に混ざる」ので、D2 の隔離が効いてくる。

> 昇格 UI・alignment 提案（どの述語が Reuse/Align/Extend/New か）は #15 の後半 or 次フェーズ。まずは「draft に隔離投入できる」ところまでを作り、昇格は手順（人手 + SPARQL）から始めて段階的に UI 化する。

## 4. 実装の分割（段階）

- ✅ **S1 substrate コア**（本コミット）: `asterism.substrate` = `draft_graph_iri` / `absolutize_rml_sources`（thread-safe）/ `materialize_to_graph`（Morph-KGC ラップ・optional dep・閉集合 udfs 登録）/ `ingest_graph_to_oxigraph`（Graph Store Protocol で draft graph 投入）/ `run_substrate_ingest`（一気通貫）。morph-kgc 非依存の部分は単体テスト済、Morph-KGC 実行は spike が実証。
- ⬜ **S2 CSV 永続化 + api エンドポイント**: materialize 時に CSV をワークスペースに保存（現状 tmpdir 破棄）。`POST /api/datasets/{id}/ingest`（②投入）= persist 済み CSV + RML → `run_substrate_ingest` を `to_thread` で。draft graph IRI と triple count を返す。
- ⬜ **S3 UI ゲート**: MaterializePanel に RML プレビュー + 「投入」承認ボタン。Gallery に draft/ingested ステータス。投入後に Ask が（昇格すれば）見えることを導線で明示。
- ✅ **S4 昇格 + alignment**: `asterism.substrate` に `alignment_report`（draft の述語/クラスを canonical=default graph と照合し Reuse/New に分類）/ `promote_draft_to_canonical`（`MOVE GRAPH <draft> TO DEFAULT`）。api `GET /api/datasets/{id}/alignment`（昇格前プレビュー）+ `POST /api/datasets/{id}/promote`。Gallery の draft カードに「語彙の差分を確認」+「canonical へ昇格」。`OxigraphClient.sparql_update` 追加。**実 Oxigraph で MOVE・隔離解消を検証**。Align/Extend（同義写像・TBox 拡張）は人間 vet に委ねる（Reuse/New を機械提示）。

## 5. 制約（守る）

1. 実行は宣言経路のみ・生成コード無し。Tier 0（REGISTRY）の関数だけ（T9 で担保）。
2. ワークベンチ投入は **draft 隔離**。canonical（引用面）は人間の昇格承認を経たものだけ。
3. Morph-KGC 実行は thread-safe（`chdir` 不使用）。`morph-kgc` は optional `substrate` extra。
4. VERSION は tagpr 管理（手で上げない）。
