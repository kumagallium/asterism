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
- **(済) UI 接地導線 + 地図整合エッジ** (`feat/external-grounding-adopt-ui`): カタログのデータセット詳細に「外部標準に接地 (ground)」を新設 — DS 独自のクラス/述語を grounding 検索 (`GroundingPicker`) で実在 term に対応づけ、既存 `/api/crosswalk/align` (任意絶対 IRI を target に・promoted alignment graph＝FROM-merge・dated/reversible/citable) で `owl:equivalentClass`/`owl:equivalentProperty` を assert (新 mutation 不要)。`OntologyMapView` は target ∈ `KNOWN_VOCABS` の alignment を**整合エッジ** (DS/perspective → 外部 term ノード) で描画。**実機実証**: materials_project を `cmso:CrystalStructure`/`cmso:Material` に接地 → 地図に整合線が点灯。
- **(済) propose 接地候補** (`feat/propose-grounding-suggestions`): AI 設計 (propose) の review に「標準オントロジーの候補」パネルを追加 — `asterism.grounding.ground_model_yaml` が rdf-config `model.yaml` の**新規 mint クラス/述語**を抜き、**決定論検索**で実在候補を提示 (`POST /api/ground/schema`・候補は LLM の記憶でなく閉集合検索＝捏造ゼロ・既知 ns の語は再利用済として除外・弱い overlap はスコアで除去)。**提示のみ・確定/採用は別** (= 上の接地 UI でカタログ取り込み後にアラインメント)。これで「AI が設計しながら標準を先出し」する体験になる。OLS/LOV/BioPortal はネット依存・後段。
- **(個別) OBO 検出**: ChEBI 等は `obo/<ONT>_NNN` で名前空間共有＝`namespaceOf` 検出が効かない。`vocab.ts` の検出を `obo/<ONT>_` パターン対応に。

---

## 9. 単位は「もう一つの属性」ではない — 専用カタログを持たせる (2026-08-20)

### Trigger
かんたん S6 (列の意味の確認) で人が単位を直せるようになった (#389) が、**打った綴りが標準に届いたかどうかを誰も言わない**。`Ω·m` と打っても `Ohm m` と打っても保存は成功し、QUDT IRI が付かないときは**黙って付かない**だけ。「機械が確かめて人に見せる」というかんたんモードの原則に対して、ここだけ確かめていなかった。

### Decision
**単位は用語 (class/property) とは別の閉集合カタログを持ち、別の経路で解決する。**

- **カタログ**: `ingest/src/asterism/grounding/qudt_units.yaml` — QUDT unit 語彙 (CC-BY 4.0) の **MIRROR** (2,745 単位・生成物)。生成は `scripts/build_qudt_units.py`。
- **綴り表**: `ingest/src/asterism/grounding/unit_spellings.yaml` — 実ファイルが使うが QUDT に無い書き方 (`W*m^(-1)*K^(-1)`, `ohm*m`, `a.u.`) の**人手キュレーション**。QUDT が自力で答えられる綴りは**入れない** (テストが冗長行で落ちる)。
- **解決**: `asterism.grounding.resolve_unit` / `GET /api/units/resolve` — `resolved` / `ambiguous` / `unknown` の 3 状態 ＋ 近い候補。
- **UI**: S6 の単位入力の下に結果を出す。**ゲートではない** — 標準側に無い単位は実在する。

### なぜ known_vocabs.yaml に入れないのか
`known_vocabs.yaml` は「**CURATION, not mirroring**」を不変条件に持つ。その理由は「どの class/property を再利用するか」が**設計判断**で、近い語が大量にあると判断の質が落ちるから。**単位はそうではない** — `V/K` の答えは 1 つしかなく、収録漏れは黙って開く穴になるだけ。判断が増えないので、丸ごと写して構わない。よって**別ファイル・別モジュール・別エンドポイント**とし、用語カタログの不変条件は保つ。

### なぜ単位を特別扱いしてよいのか (一般化との関係)
- 「300」だけでは**引用できる事実にならない**。値と単位で 1 つの事実であり、他の属性 (名前・コメント) とは階層が違う。
- RDF の型システムで表せない (`xsd:double` は kelvin を語らない)。だから QUDT / UCUM / OM のような**単位専用の語彙体系**が国際的に別途作られている。schema.org `QuantitativeValue`・OBOE も同じ扱い。
- むしろ**従来のほうが一般化に反していた**: `asterism/qudt.py` は `_MAP_DATASET = "starrydata"` 固定で、単位表が 1 データセットの持ち物になっていた。今回それを core に引き上げた (starrydata マップは overlay として残置＝既存の契約を壊さない)。

### 記号の衝突は SI で解く
2,510 記号のうち **66 が複数の単位に共有**される (`K` = kelvin / kayser、`S` = siemens / solar mass)。QUDT の `qudt:applicableSystem sou:SI` を採録し、**SI が 1 つだけ該当するときはそれを採る**。SI で決まらないもの (`L` が 5 単位、`$` が 11 通貨) は `ambiguous` のまま人に返す。

### 意図的に埋めない穴
**µV/K (ゼーベック係数の日常単位) は QUDT 3.1.0 に term が無い** (`MicroV-PER-M` と `MicroVA-PER-K` はあるのに)。`V-PER-K` に寄せると値が 10⁶ 倍ずれるので、**未解決のまま報告する**。テストで固定済み — 善意の「修正」で塞がれないように。

### やっていないこと (別 PR)
- **ingest への配線**: `asterism/qudt.py` は starrydata 専用のまま。core カタログへフォールバックさせると、これまで IRI が付かなかった単位に付き始める (additive で望ましい) が、既存テストが「表が無ければ None」を保証しており契約変更になる。よって S6 のバッジは「**標準にこの単位が在るか**」の情報提供であって、「IRI が付く」とは言っていない。
- **µV/K のような欠落を QUDT へ上流報告する経路**。

---

## 10. 単位だけでは半分 — 「何の量か」を接地する (2026-08-21)

### Trigger
§9 で単位が標準に届くようになった直後、実データで叩いて分かった: **物性名は 1 つも接地できなかった**。

```
temperature           0 候補
thermal conductivity  0 候補
resistivity           0 候補
seebeck coefficient   0 候補
```

原因は `known_vocabs.yaml` の qudt が **11 term しかなく、それが全部スキーマ語彙**（`Quantity` `QuantityKind` `Unit` `hasUnit` …）だったこと。QUDT には `quantitykind:ThermalConductivity` が実在するのに、カタログに無いだけだった。**単位が届いて物性名が届かないのは片肺** — 人が横断で探すのは「熱伝導率を測った人」であって、「W/(m·K) と書いた人」ではない。

### Decision
**量種別も専用の閉集合カタログを持たせ、単位と同じ mirror 方式で扱う。**

- **カタログ**: `ingest/src/asterism/grounding/qudt_quantitykinds.yaml` — QUDT quantitykind 語彙 (CC-BY 4.0) の **MIRROR** (1,164 件・生成物)。生成は `scripts/build_qudt_quantitykinds.py`（単位側と共通の土台は `scripts/_qudt_mirror.py`）。
- **解決**: `asterism.grounding.resolve_quantity_kind` / `GET /api/quantitykinds/resolve`
- **UI**: 中身タブの接地欄に「この列は何を測っているか」の節を追加（項目名の接地とは**別の問い**なので同じ行に同居させない）。

`known_vocabs.yaml` の「CURATION, not mirroring」不変条件は §9 と同じ理由で保たれる — QuantityKind は class でも property でもないので、そもそも用語カタログの対象外。

### ⭐ 述語の選択 — なぜ `qudt:hasQuantityKind` か
`quantitykind:Temperature` は QUDT では **individual** (`a qudt:QuantityKind`) で、class でも property でもない。一方 Asterism の `te:temperature` は property。既存の alignment 述語 4 つ（`equivalentClass` / `subClassOf` / `equivalentProperty` / `subPropertyOf`）は**どれも型が合わず**、`owl:equivalentProperty` で結ぶのは端的に誤り。

検討した案:

| 案 | 判断 |
|---|---|
| **`qudt:hasQuantityKind` を述語に付ける** | **採用**。QUDT 自身の述語がちょうどこの意味を言う。新しい語彙を発明しない |
| `ast:` の述語を自前 mint | 型の誤用はないが独自語彙が増える（既存再利用が方針） |
| 中間ノード `qudt:QuantityValue` | 最も正確だが §9 の additive 決定を覆し、既存データの構造が変わる |

**厳密には punning** である（QUDT 自身は `hasQuantityKind` を値の側に付ける）。読みは「この述語が運ぶ値はこの量種別である」。値ごとに QuantityValue ノードを作らない代わりに述語側へ付ける、という意図的な取引。

### 単位が最大の手がかり
列名が `S` や `rho` の 1〜3 文字では何も分からないが、**`V/K` で測る量は QUDT が数えるほどしかない**（`qudt:applicableUnit`）。そこで解決器は列の単位を受け取り、

- 名前と単位が**両方**合えば最高スコア（`+unit`）
- 名前が読めなくても**単位だけ**で候補を出す（`match: "unit"`）— ただし名前マッチより必ず下
- 単位が分かっているとき、その単位で測れない量は**落とす**（ohm·m の列に thermal resistivity は出さない）。ただし**名前が exact なら残す** — 名前と単位が食い違う設計は隠さず見せる

実測: `S` + `V-PER-K` → Seebeck / Thomson の 2 件、`rho` + `OHM-M` → Resistivity / ResidualResistivity の 2 件。

### 曖昧さは曖昧なまま返す
ケルビンで測る量は **23 種類**あり、QUDT に順位を決める材料は無い（`applicableUnit` 数も 9 で同数、`symbol` は 573/1164 しか無く LaTeX 記法で 57 件が衝突）。**1 つに見せかけず全部返して人に選ばせる**。逆に名前が exact に当たったときは**その 1 件だけ**返す — 「Thermal Conductivity」の隣に「Conductivity」と「Thermal Resistivity」を並べるのは、決着済みの判断をクイズに変える行為。

### 短い名前は曖昧マッチを信じない
`rho` は "wate**rho**rsepower" の部分文字列で、`S` の 1 トークンは "Henry**'s** Law" がアポストロフィの後に残す `s` と一致する。**どちらも実際に 1 位を取っていた**。4 文字未満のクエリは exact 以外を無効にし、単位（実証拠）だけを信じる。

### gloss の LaTeX
QUDT の説明文には数式が埋まっている（`$k$ (also denoted as $\lambda$)`）。切り取ると足場が残り（`conductivity, (also denoted as ), is`）、括弧掃除では英文は直らない。**数式を含む記述は採らない**（次の候補を試し、無ければ gloss なし）。791/1164 に gloss が付く。

### 実証
熱電データ 4 列で全列が 1 対 1 に解決。`thermalConductivity` を接地したうえで、**述語名を一切使わない** SPARQL が値を返した:

```sparql
GRAPH ?ag { ?pred qudt:hasQuantityKind qk:ThermalConductivity }
GRAPH ?dg { ?m ?pred ?value ; te:sampleName ?sample }
→ Bi2Te3-A = 1.28 / 1.42, Bi2Te3-B = 1.35 / 1.51
```

これが接地の目的そのもの。データセットが増えれば、列名が `kappa` でも `thermal_cond` でも同じクエリが拾う。
