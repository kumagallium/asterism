# 外部標準オントロジーへの整合・異種データ結合の方針 (設計決定)

決定: 2026-06-05 / 設計セッション (人間 kumagallium + Claude)
status: **合意済み** (2026-06-05 ユーザー確定)。本書は「どう繋ぐか」の方針を固定する。具体の整合実装の着手判断 (今どの標準にどこまで寄せるか) は §7 と #19/#20 を参照。

前提 ADR (本書はこれらを**覆さず**補強する):
- [`ontology-mapping-boundary-and-provenance.md`](ontology-mapping-boundary-and-provenance.md) — asterism = Read 基盤 / 利用側 = Act 層という責務境界、外部上位語彙の再利用、per-dataset TBox。
- [`ontology-canonical-lifecycle.md`](ontology-canonical-lifecycle.md) — TBox/ABox × draft/canonical の2軸、starrydata の core からの降格、外部語彙再利用の位置づけ。
- 契機 / 実例: [`static-citable-facts-demo.md`](static-citable-facts-demo.md) §7 (Starrydata × Materials Project 横断結合) と PoC `experiments/mp-linking-poc/`。

---

## 背景 (なぜ書くか)

開発者から問い:「`sd:CrystalStructure → CMSO`、`sd:PointDefect → PODO`、上位 `EMMO` に `owl` で整合し、**その上で** starrydata と異種データ (Materials Project) を結合する ── これが Asterism 本来の理想的な進め方という理解で合っているか？」

方向性 (共有/標準セマンティクスに収斂して異種データを相互運用する) は北極星「starrydata に閉じない汎用基盤」と前提 ADR に合致する。一方で「**owl で整合してから結合**」という*順序・手段*の理解は精密化が要る。本書はその精密版を決定として固定する。

---

## TL;DR

### 結論
1. **理想は「共有/標準セマンティクスへの収斂」**であって、手段 (直接再利用 か mint+owl 整合 か) はそれ自体が目的ではない。
2. **結合は2層**: (A) **インスタンス層** = 同じ IRI が複数グラフに現れることで繋がる (TBox 整合 *不要*・今のデモが実証)。(B) **スキーマ層** = 外部標準への `owl` 整合で語彙そのものが相互運用可能になる (将来データ・外部ツール・推論が噛み合う)。両方持つのが最終形だが、**順序は「まず A で繋いで価値を出し、B で一般化」**。「整合してからでないと結合できない」わけではない。
3. **語彙の作り方は「直接再利用」を第一候補**とする。自前 mint + `owl` 橋渡しは、外部標準が未成熟/粒度不一致/ガバナンス制御が要る場合の**過渡手段**。
4. **Asterism の役割は基盤**: 整合 (reuse/align) を first-class に*しやすくし*、決定論的取り込み・来歴・横断クエリ・引用を保証する。**ドメインオントロジーの著者・権威ではない** (どの標準が正しいかは分野が決める)。
5. **右サイズの形式化**: EMMO 等の重い上位への最大限整合が理想ではない。「必要十分な共有セマンティクス + 来歴 + 一意 IRI + 引用」を基本とし、**payoff のある所 (成熟・パートナーが使う標準) に整合を効かせる**。過剰形式化は採用を阻害する。

---

## 1. 結合の2層モデル — インスタンス結合とスキーマ整合

### Decision
異種データの「繋がり」を **(A) インスタンス層** と **(B) スキーマ層** の2層で扱い、混同しない。
- **(A) インスタンス層**: 同一の IRI (例 `sdr:sample/{SID}-{sample_id}`、MP の `materialsproject.org/materials/{mp-id}`) が複数グラフに現れることで結合が成立。**TBox の owl 整合は不要**。
- **(B) スキーマ層**: ローカル語彙を外部標準 (CMSO/PODO/EMMO 等) に `owl:equivalentClass` / `rdfs:subClassOf` / `subPropertyOf` で整合させ、**クラス/述語レベル**で相互運用可能にする。

### Why
今の Starrydata × MP デモは (A) のみで実際に動いている (共有 `sample` IRI で 1 クエリ結合)。(B) は「*まだ見ぬ*別データセット・外部ツール・推論器」が同じ語彙で噛み合うための、より深く再利用可能な統合。2層を分けることで「整合が済むまで結合できない」という誤解と過剰な前倒し投資を避けられる。

### Trade-offs
(A) だけでは語彙の相互運用は得られない (各データセットが別語彙だと横断クエリは IRI 共有に依存)。(B) を足して初めて「同じ語彙を話す N データセットが自動で噛み合う」。

---

## 2. 整合の手段 — 直接再利用を第一候補、mint+owl 橋渡しは過渡

### Decision
外部標準の項を**最初から直接再利用する** (例: `sd:CrystalStructure` を建てず `cmso:CrystalStructure` を使う) ことを第一候補とする。自前語彙を mint して後から `owl` で橋渡しする方式は、次のいずれかが成り立つ時の**過渡手段**に限定する:
- 外部標準が未成熟/流動的で直接依存するとデータ同一性 (IRI 不変) が脅かされる、
- 外部標準の粒度・モデリングが自分の事実に合わない、
- 段階導入のため一旦ローカルで固め、後から寄せたい (ガバナンス制御)。

### Why
mint + 橋渡しは並行語彙を恒久的に保守し続けるコストを生む。semantic web の素直な理想は既存項の再利用。ただし IRI = データ同一性 (不変条件) なので、未成熟な外部 IRI に直接依存して後で壊れるより、過渡的にローカルで固める判断もありうる ── そのための過渡手段として明示的に許容する。

### Alternatives
- **A. 常に直接再利用**: 最も相互運用的だが、未成熟標準への依存リスク。
- **B. 常に mint + 後で owl 整合** (PoC `mp_link_tbox.ttl` の現状): 制御しやすいが二重保守。
- **C. 状況で選ぶ (採用)**: 既定は再利用、不安定/不一致/段階導入時のみ mint+整合。

### Re-evaluation triggers
対象標準 (CMSO/PODO/EMMO) の成熟度・採用度が上がれば、過渡の mint をやめ直接再利用 (または `owl:equivalentClass` で完全同一視) へ寄せる。

---

## 3. 順序 — まずインスタンスで繋ぎ、標準整合で一般化

### Decision
新しい異種ソースを迎える時は **(A) インスタンス層の結合を先に成立**させ、具体的な横断の価値 (引用できる事実) を出す。**(B) スキーマ層の標準整合は、その後の一般化フェーズ**で、payoff のある語彙から段階的に進める。

### Why
価値の早期検証と、標準選定の過剰な前倒しを避けるため。デモはこの順序を体現 (instance 結合済 / formal alignment は次段階)。

---

## 4. Asterism の役割境界 — 基盤であってドメインオントロジーの著者ではない

### Decision
Asterism は **Read 基盤**として、(a) per-dataset の語彙宣言、(b) **外部標準への整合 (reuse/align) を first-class に容易化**、(c) 決定論的取り込み + 来歴 (PROV)、(d) 横断クエリ・引用、を提供する。**どの外部標準が正しいか・ドメインのオントロジーをどうモデル化するかの権威にはならない** ── それは分野コミュニティ (材料なら EMMO/CMSO 等) が決め、Asterism はそれを*載せて・効かせて・辿れるように*する。

### Why
[`ontology-mapping-boundary-and-provenance.md`](ontology-mapping-boundary-and-provenance.md) の engine/content 境界・「starrydata に閉じない」北極星と整合。Asterism が特定ドメインのオントロジー著者を兼ねると汎用基盤と矛盾する。

---

## 5. 右サイズの形式化 — payoff 主義

### Decision
形式整合の量は**目的に対して必要十分**に留める。基本線は「**共有セマンティクス (再利用/整合) + 来歴 + グローバル一意 IRI + 引用**」。EMMO のような重く厳密な上位への最大限整合を一律の理想とはしない。**整合は payoff のある所 (成熟し、パートナー/ツールが実際に使う標準) に集中**する。

### Why
EMMO 等は学習・整合コストが高く、過剰形式化は導入・採用を阻害する。製品主軸は「引用できる事実」であり、推論の網羅的健全性そのものではない。

### Re-evaluation triggers
外部ツール連携・推論要求・規制等で、より厳密な上位整合の payoff が明確になった領域から整合を深める。

---

## 6. 適用例 — Starrydata × Materials Project

- **現状 (本リポジトリ)**: (A) インスタンス層のみ。starrydata サンプル → MP material IRI へ `sd:idealizedFrom` (`prov:wasDerivedFrom` のサブプロパティ・`owl:sameAs` ではない) で**参照**を張り、構造の事実 (空間群・結晶系・prototype・還元式) を **starrydata 自身の `sd:` 語彙**で記述。リンク自体を `sd:StructureMatchActivity` (方法・一致度) で来歴づけ。母相は **最安定相 (e_above_hull 最小の多形)** に限定 (近似)。MP は独自オントロジー化/連合 (federation) していない。
- **理想形 (将来・#19/#20)**: (B) スキーマ層を足す。`sd:CrystalStructure` ~ CMSO、`sd:PointDefect` ~ PODO、上位 EMMO へ `owl` 整合 (§2 の判断で「直接再利用」か「mint+橋渡し」を選ぶ)。これで CMSO/EMMO を話す**他の材料データセットや外部ツール**が同じ語彙で噛み合う。
- **「Python で一度 join すれば同じでは?」への位置づけ**: 2 者を一度結合するだけなら実質同等 (PoC 自体 Python で突き合わせている)。Asterism の価値は結合を*使い捨てコード*でなく**型付き・来歴つきの再利用できるデータ**として残し、**監査・引用・多数ソースへのスケール**を得る点。単一 CSV より**複数ソース統合でこそ効く**。

---

## まとめ (素朴な理解 vs 本書の決定)

| 素朴な理解 | 本書の決定 |
|---|---|
| CMSO/PODO/EMMO に `owl` 整合するのが理想 | 方向は正しい。ただし理想は「共有/標準セマンティクスへの収斂」で、**直接再利用**も同格の手段 (§2)。 |
| 整合した上で結合する | 結合は**2層**。インスタンス結合 (共有 IRI) は整合なしで成立 (§1)。`owl` 整合は schema 層の一般化で、**順序は「まず繋ぐ→標準で広げる」** (§3)。 |
| これが Asterism 本来の理想 | ほぼ。ただし Asterism は**整合を容易化し来歴・引用を保証する基盤**であり、ドメインオントロジーの著者ではない (§4)。**過剰形式化は避ける** (§5)。 |

---

## 7. 実装・次の一手 (本書は方針・着手は別判断)

- 本書は「どう繋ぐか」を固定するもので、「今どこまで整合するか」は #19 (2 件目の非 starrydata データセット投入) / #20 (外部上位語彙の再利用・per-dataset TBox・ライフサイクル) の進行に合わせる。
- 静的デモ (§6・[`static-citable-facts-demo.md`](static-citable-facts-demo.md) §7) は現状 (A) インスタンス結合のままで本書と矛盾しない。formal alignment は #19/#20 の深掘りに置く。
- 最初の (B) 着手候補: PoC の `mp_link_tbox.ttl` の `sd:CrystalStructure`/`sd:PointDefect` を CMSO/PODO へ `rdfs:seeAlso` から `owl` 整合へ昇格 (§2 の判断で再利用 or 橋渡しを選ぶ)。canonical 昇格はプロジェクト規約 (ingester + ttl + Mermaid の 3 点セット) に従う。**ただし §3/§5 に従い、整合を消費する相手 (2 件目データセット = #19) ができるまでは `seeAlso` 据え置きで延期** (2026-06-05 決定)。
- **LLM による外部語彙の再利用を信頼できる形にするには grounding/検索が要る**: 現状 step0 propose は外部 IRI を LLM の記憶から書く (有名語彙限定・捏造リスク)。OLS/LOV 等を引く検索ツールで実在 term に接地する案を ROADMAP に起案 (本書 §2「直接再利用」を実務で効かせる手段)。

---

## 8. 標準接地を「一級」にする — curated スターターパック (2026-06-15 方向決定)

### Trigger (待っていた消費者が来た)
ユーザー指摘: 「材料の人が Asterism を使うとき、**材料の有名オントロジー (CMSO/EMMO/QUDT 等) に紐づかない**と体験が悪い。**既存標準にデータが乗ること**こそ Asterism の良さでは?」。これは §2/§3/§5 が延期理由としていた「**整合を消費する相手**」がまさに現れたということ＝再評価トリガー。**方向に同意し、外部標準接地を“あれば良い”から“一級の体験”へ引き上げる。**

### Decision
Asterism は **有名・基盤オントロジーの curated スターターパック**を標準同梱し、**2つの意味で**使う:
1. **認識 (RECOGNIZE)** — `ui/src/vocab.ts` の `KNOWN_VOCABS` が「Asterism が知っている標準語彙」のリスト。地図・再利用表示が検出に使う。**本決定で汎用 (FOAF/DCAT/SOSA を追加) ＋材料 (QUDT/EMMO/CMSO) に拡充済**（名前空間は実在を検証: EMMO=`https://w3id.org/emmo#`・CMSO=`https://purls.helmholtz-metadaten.de/cmso/`）。
2. **接地 (LINK)** — データが実際にその標準の **実在 term IRI を reuse/align** する。これは **retrieval + 人 vet** の grounding ワークストリーム (下記・本書 §2「直接再利用」の実務化)。

**重要**: 1 を増やしても、2 (データが term を使う) が無ければ地図には出ない。今あるデータは汎用 (schema/dcterms/PROV) のみ参照＝材料標準への線はまだ無い。2 を入れて初めて「材料の人がデータを足すと自然に CMSO/QUDT に乗る」体験になる。

### 「キリがない?」への答え — No、curated に有限
全語彙 (LOV 約700・BioPortal 数百) を網羅する必要はない。**有名・基盤のものを稼働ドメイン毎に数個**で十分 (QUDT 単位・Tier-0・normalizer ライブラリと同じ「**手入れして育つ共有資産**」)。汎用 (schema/dcterms/PROV/SKOS/FOAF/DCAT/SOSA) ＋材料 (QUDT/EMMO/CMSO/PODO/ChEBI…) のように**ドメインパック**で curated に増やす。OBO 系 (ChEBI 等) は `obo/<ONT>_NNN` 形式で名前空間が共有され namespace 検出が効かない (個別対応が要る) ＝既知の制約。

### 実装の段階 (次の一手は別 PR)
- **(済) 認識層**: `KNOWN_VOCABS` curated 拡充 (本決定)。
- **(済) SoT 昇格 + 検索基盤** (`feat/external-grounding-search`): `KNOWN_VOCABS` を **backend と共有する SoT** `ingest/src/asterism/grounding/known_vocabs.yaml` に昇格 — namespaces に加え、各語彙の**実在 term** (CMSO/QUDT/schema.org/PROV/dcterms/SKOS/FOAF/DCAT/SOSA/bibo・全 term を**権威 RDF から検証**して採録・provenance つき)。`asterism.grounding.ground_terms` が**決定論クローズドセット検索** (クラス/述語名 → 実在 term IRI 候補・LLM/網/乱数なし・結果は必ず catalog 内＝捏造不可) を提供し、read-only `GET /api/ground`・`GET /api/vocabularies` で公開 (MCP/propose/UI が同一一覧を使える)。**EMMO は不透明 IRI** (`emmo#EMMO_<uuid>`) で名前/ラベル検索が効かず term 保留＝既知の制約。**発見**: CMSO の権威 term IRI は **http://** (https:// PURL は HTML docs へ 303 のみ・term identity でない)＝`vocab.ts` の cmso を http に修正 (RECOGNIZE==LINK)。
- **(次・本丸) propose/UI 接地導線**: step0 propose の出力クラス/述語に grounding 検索の**候補を添付** (下書き・確定は人)。propose/refine/作成 UI に「このクラス = `cmso:X`」と**外部標準を採用する導線**を追加。**書き込みは既存 `/api/crosswalk/align`** (任意絶対 IRI を target に・promoted alignment graph＝FROM-merge・dated/reversible/citable) で実現可能＝新 mutation 不要。OLS/LOV/BioPortal はネット依存・後段。
- **(地図) 整合エッジ**: 「再利用」エッジに加え、`owl:equivalentClass` 等の**整合エッジ** (データセット/概念 → 外部 term) を `OntologyMapView` に描けるよう拡張 (今は perspective 間のみ)。
- **(個別) OBO 検出**: ChEBI 等は `obo/<ONT>_NNN` で名前空間共有＝`namespaceOf` 検出が効かない。`vocab.ts` の検出を `obo/<ONT>_` パターン対応に。
