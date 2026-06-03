# オントロジー / マッピング境界・来歴・エージェント表面 (設計決定)

決定: 2026-06-01 / 設計セッション (人間 + Claude)
status: **合意済み** (実装の着手判断は本書 §7・§8 を参照。本書は「どう作るか」を固定するもので、「今作る」とは別)

本書は asterism の責務境界を確定する。具体的には (a) asterism と下流の利用側アプリケーション (例: Graphium) の責務分担、(b) 来歴 (PROV) の層構造、(c) オントロジー・マッピング・エンジンのリポジトリ境界、(d) エージェント向けアクセス表面、(e) rdf-config / SPARQL の位置づけ、を扱う。背景の整理は Palantir の ontology 哲学 (目的スコープ + 既存再利用 + 意思決定中心 + アクション層) との対照から出発しているが、本書は asterism 自身の技術境界のみを記述する。

---

## TL;DR

### 結論

1. **asterism = 意味 + 事実来歴の substrate (Read)**。動詞 = アクション層 (write-back / 引用 / Asset / 意思決定) は持たせず、利用側プロダクトに置く。
2. **来歴は1層ではなく2層**。asterism = データ来歴 (取り込み lineage)、利用側 = 活動来歴 (発見・推論 lineage)。両方 PROV-O。別ストア・同語彙・IRI で連結・クエリ時に合成。来歴を asterism へ集約「しない」。
3. **リポジトリ境界は ontology-vs-mapping ではなく engine-vs-content**。共有 OSS = エンジン (step0) + 配信 infra + 規約 + validator。content = per-dataset の {TBox, MIE, ingester, tests} を `datasets/{name}/` に co-locate し、ラボが所有できる (sovereign)。
4. **エージェント表面は typed MCP tool**。SPARQL は substrate として維持するが、「エンドポイントを渡して生 SPARQL を書かせる」から「よく使う形を typed MCP tool として出す」へ寄せる。生 SPARQL は人間・上級者の脱出ハッチに格下げ。
5. **rdf-config は非 load-bearing**。硬化すべき durable asset は「MIE という契約」と「8-trap validator」。generator (rdf-config / LinkML / AI 直書き) は validator の後ろの swappable detail。

### 採用しない案

- **ontology(TBox) と mapping(MIE) を別リポジトリに割る** — design triangle (TBox / Mermaid / MIE / ingester は同時更新) の最も強い結合線を物理的に切ってしまうため不採用。
- **共有オントロジーを投機的に先出しする** — 複数 dataset に実際に再発した語彙だけを後から括り出す。

---

## 1. 責務境界 (asterism vs 利用側)

asterism は意味層 (TBox + ABox) と事実来歴層を提供する **Read substrate** に徹する。利用側プロダクトが持つ動詞 = アクション層 (ユーザー / エージェントの判断による副作用つき操作) は asterism に取り込まない。

| 層 | 担当 | 例 |
|---|---|---|
| 運動 / 意思決定 (Act) | 利用側プロダクト | 引用ノート化、Asset 管理、write-back、採否 |
| 意味 + 事実来歴 (Read) | **asterism** | CSV → RDF、SPARQL / MCP での参照、取り込み lineage |

この線を引く理由は、asterism が「全部入りオントロジー」へ膨らむ scope creep を防ぎ、world-grounding の足腰という役割に集中させるため。取り込み (ingestion) は pipeline activity であって、利用側から見た asterism は Read である点に注意 (取り込み自体は triple を書くが、ユーザー / エージェント向けの kinetic 操作ではない)。

## 2. 来歴の2層 (PROV)

来歴はどちらか一方が所有するのではなく、**両方が別スコープで生む**。共通基盤は PROV (PROV-O) で揃える。

| | データ来歴 | 活動来歴 |
|---|---|---|
| 担当 | asterism | 利用側プロダクト |
| 主体 | 事実 (公開 / ラボ / 測定データ) | 思考 (選択・引用・発想・採否) |
| 向き | 後ろ向き (何から来たか) | 前向き (どう生まれたか) |
| 語彙 | PROV-O | PROV-O |
| ストア | substrate (ラボ) | 個人 / ローカル |
| 連結 | — | asterism の IRI を引用 |

利用側のノートが asterism の IRI を引用することで、**事実の来歴 ← 引用 ← 思考の来歴**が一本の鎖として queryable になる。ストアは分け (主権を守るため個人の思考来歴を substrate に吸い上げない)、語彙を揃え、IRI で連結し、クエリ時に合成する。README の「PROV-O is the lingua franca」はこの合成可能性を指す。

**前提条件**: 両者の PROV プロファイルと IRI 規約を一致させること (§8 残課題)。揃っていないと鎖が連結しない。

## 3. リポジトリ境界 (engine vs content)

オントロジー / マッピングの概念的分離 (共有資産 / 使い捨て) は正しいが、**リポジトリ境界をその線で引かない**。理由は2つ。

1. design triangle の最強結合を切る。`ai-assisted-step0-workflow.md` §3 の「TBox / Mermaid / MIE / ingester は必ず同時更新」は、TBox と MIE がこの結合の両端であることを意味する。ここに repo 境界を入れると、最も同期が厳しい所でクロスリポ同期が発生する。
2. TBox 自体も大半は per-dataset で使い捨て側。各 dataset は固有の最小 TBox を持ち、本当に共有なのは「外部上位語彙 (schema.org / PROV-O / QUDT、当方の所有でない)」と「エンジン + 規約 (8 traps / IRI scheme / design triangle、当方の所有)」。「自分の TBox を共有オントロジー repo に切り出す」は、共有でないものを切り出すことになる。

筋の良い切断面は **engine-vs-content**:

- **core (公開 OSS・1 repo)**: step0 ビルダー (inspect / propose / refine / materialize / validate / ttl2mermaid) + 配信 infra (Oxigraph / togomcp / upload / watcher) + 規約 + validator。再利用可能な本体。
- **content (per-dataset bundle)**: `datasets/{name}/` に {TBox, MIE, ingester, tests} を co-locate。将来 dataset が増え所有者が分かれた時点で別 repo (各ラボの private repo を含む) へ "graduate"。

推奨レイアウト (repo 内で先に整える。repo 分割は後でよい):

```
datasets/
  starrydata/
    ontology.ttl        # 旧 docs/ontology/starrydata.ttl
    diagram.md          # 旧 docs/ontology/diagram.md
    mapping.mie.yaml    # 旧 data/togomcp/mie/starrydata.yaml
    ingester.py         # 旧 ingest/src/asterism/starrydata.py
    tests/
step0/                  # エンジン (content 非依存)
infra/ , api/ , mcp/    # 配信 (content 非依存)
```

これで (a) 同時更新が物理的に自明になり、(b) 将来の repo 分割が「`datasets/{name}/` を move するだけ」になる。今コミットせずに将来の分割可能性だけ確保できる。

## 4. content の所有 (sovereign)

dataset のマッピング (content) は **各ラボが所有できる**ようにする。共有されるのは engine + 上位語彙のみ。各ラボの content (CSV マッピング、および将来 §7 で記録から立ち上がる emergent な語彙) は、そのラボの境界を越えない。公開アーカイブへの graduation は明示的かつ PROV-tracked (Design principle 1)。

帰結として substrate は「所有による multi-tenant」になる: 共有 engine + 共有上位語彙 + per-lab content。各ラボの emergent ontology は「そのラボの digital twin」であり、組織境界にスコープした意思決定中心オントロジーを sovereign RDF + MCP で表現した形になる。

## 5. エージェント表面 (typed MCP over SPARQL)

SPARQL は **substrate / engine としては維持** (sovereign / self-host / federation / PROV-O が queryable / Oxigraph・QLever 互換)。ただしエージェント向けの表面は、生 SPARQL から **typed MCP tool** へ寄せる:

- object resolve (IRI → entity)
- link traverse (entity → 関連 entity)
- よく使う形の parameterized query (MIE の `sparql_query_examples` を tool 化)

これは「エージェントには typed / governed な操作を呼ばせる」方針。既に togomcp と自作 `template_curve_fetch` MCP がこの方向に半歩来ている。生 SPARQL は人間・上級利用の脱出ハッチに格下げする。

## 6. rdf-config / SPARQL の位置づけ

`linkml-vs-rdf-config.md` の結論 (rdf-config 採用) は維持するが、位置づけを明確化する。

- **rdf-config は低リスクだが load-bearing ではない**。仕事は「小さな model.yaml → ShEx」変換で薄く、型推論が弱く (date → string)、複数 rdf:type を表現できない。Phase 3 では LLM が TBox / MIE / ingester を直接提案するため、ShEx も AI 直書きで代替しうる。
- **硬化すべき durable asset は2つ**: (1) MIE という契約 (artifact の schema)、(2) full CSV に対する 8-trap validator。これらは tool 非依存。shape が rdf-config 由来か LinkML 由来か AI 直書きかは、validator の後ろの実装詳細にする。
- **再評価トリガ**: 「4 artifact 同時更新」の痛みが支配的になったら、問いは「rdf-config か LinkML か (出力スタイル)」ではなく「single source model を持つか否か」。LinkML の「1 モデル → 多 projection」は single-source-of-truth として再評価対象になる。現時点は互換性で rdf-config が正しい (n=1)。

## 7. 将来の不変条件 (今は作らない、ただし塞がない)

利用側に蓄積する記録は、最終的にオントロジーの源になる content になりうる (語彙 / synonym、繰り返し引かれる関係 = emergent な object / link 候補、取り込み優先度)。これは Phase 4+ の方向であり、今作るものではない。今日の決定で将来を foreclose しないために、以下を不変条件とする。

1. **共有するのは content ではなく engine** (§3 のまま)。
2. **capture は schema-light に保ち、構造化は後からの opt-in projection** にする。capture 時にオントロジーを強制すると発見価値が死ぬ (唯一の実リスク)。
3. **step0 の入力抽象を「CSV」ではなく「inspect 可能な構造を持つソース」に保つ**。将来、非表形式の provenance-bearing ソース (利用側の活動ログ / ノートのエクスポート等) を同じ propose → refine → validate に通せる余地を残す。`phase4-ui-architecture.md` §6.6 の「step0 の隠れた starrydata 前提を炙り出す」dogfood は既にこの方向の地ならし。

## 8. 残課題

- [ ] PROV プロファイル + IRI 規約を asterism と利用側で一致させる (鎖の連結条件。§2)。
- [ ] `datasets/{name}/` レイアウトへの移行 (§3。starrydata を最初の対象に)。
- [ ] typed MCP tool の最小セット (object resolve / link traverse / parameterized) の定義 (§5)。
- [ ] MIE schema を「契約」として明文化し、generator から独立にバージョン管理する (§6)。
- [ ] step0 入力抽象の source-agnostic 化の可否を、非 CSV ソースで素振りする (§7-3)。

## 9. 関連

- [`ai-assisted-step0-workflow.md`](ai-assisted-step0-workflow.md) — design triangle と 8 traps の出典 (§3 / §6)
- [`linkml-vs-rdf-config.md`](linkml-vs-rdf-config.md) — generator 選定 (§6 の前提)
- [`phase4-ui-architecture.md`](phase4-ui-architecture.md) — §6.6 オントロジー / マッピングギャラリーの分離 (§3 / §7 の出典)
- [`option-b.md`](option-b.md) — 全体アーキ
- [`../../README.md`](../../README.md) — Design principles (sovereign / PROV-O lingua franca)

## 10. 更新ログ

- 2026-06-01: 初版 (設計セッション)。Palantir ontology 哲学との対照から、責務境界 / 来歴2層 / engine-vs-content / typed MCP 表面 / rdf-config 非 load-bearing を確定。
