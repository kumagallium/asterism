# 実験の記録に要る関係 — 閉じた集合を広げ、語に役割の札を足す

状態: 決定（2026-11-17・利用者裁定 5 問）。実装 = PR 14a-1。設計の経緯は AI for Science 側
`ui-redesign-2026-10-19/design.md` §23。前提 = [`upper-structure-shared-terms.md`](upper-structure-shared-terms.md) §2.2
（述語の閉集合）と [`sky-chart-two-entries.md`](sky-chart-two-entries.md) §2.4（9 述語）。

## 背景

実験の記録から知識グラフを作ると、「この手法でこの材料を作る」「この手法でこの物性を測る」
「この相がこの相に変わる」「この 2 つの相は競合する」「この材料のこの物性」が大半を占める。
今の閉集合（一種・同じ・量・一部・下げる・上げる・変える）には、どれも入らない。

## 決定

### 1. 関係を 5 文足す（文 7 → 12・述語 9 → 15）

| 文 | 下書きの名前 | 確定側の名前 | 述語 | from → to | 向き |
|---|---|---|---|---|---|
| A で B を作る | `makes` | `makes` | RO `has output`（`obo:RO_0002234`） | 種類 → 種類 | あり |
| A で B を測る | `measures` | `measuresProperty` | RO `assay measures characteristic`（`obo:RO_0009006`） | 種類 → 項目 | あり |
| 同上 | `measures` | `measuresMaterial` | SOSA `hasFeatureOfInterest`（`sosa:hasFeatureOfInterest`） | 種類 → 種類 | あり |
| A が B に変わる | `becomes` | `becomes` | RO `derives into`（`obo:RO_0001001`） | 種類 → 種類 | あり |
| A と B は競合する | `competes` | `competesWith` | **自前** `rel:competesWith` | 種類 ― 種類 | なし |
| A の B（物性） | `has_property` | `hasProperty` | RO `has characteristic`（`obo:RO_0000053`） | 種類 → 項目 | あり |

- `rel:` = `https://kumagallium.github.io/asterism/vocab/relation#`（新設。関係の述語だけを置く）。
  `sv:` は用語の slug の名前空間のまま、`xw:` は視点の語と来歴のまま。
- 「測る」は相手の種別で述語が分かれる（「一種」が `subClassOf`／`subPropertyOf` に分かれるのと同じ形）。
  画面の文は 1 つ。
- 既存の 9 つの名前・IRI・検査は変えない。「下げる・上げる・変える」の相手は項目だけのまま
  （材料の生成を妨げる・促すは意味が違うので、要ると分かったら別の関係として足す）。
- 循環は 6 つとも許す（可逆な変化 A ⇄ B は正当）。自分自身に結ぶことは 6 つとも拒む。
- **向きの無い関係**（`competesWith`）は、両端を IRI の文字列順に並べてから置く。A→B と B→A は
  同じ 1 本（トリプル 1 つ・来歴ノード 1 つ）になる。取り消し・根拠の追加・却下も同じ並べ替えを通す。

### 2. 標準の述語の照合（2026-11-17）

正本: RO 2026-09-04 版（`http://purl.obolibrary.org/obo/ro.owl`）・W3C SOSA／SSN
（`https://www.w3.org/ns/sosa/`・`…/ssn/`）・OBI（OLS4 経由）・OLS4 の全オントロジー横断検索。

| 関係 | 見たもの | 採否 | 理由 |
|---|---|---|---|
| 作る | `RO_0002234` has output（別名 produces） | 採用 | 過程 p の終わりに在り、始めには同じ状態で無かった参加者。手法（過程）→ 材料に合う |
| 作る | `RO_0003000` produces | 不採用 | 両端が material entity（細胞株が抗体を作る）。主語が手法でない |
| 測る（→ 物性） | `RO_0009006` assay measures characteristic | 採用 | 定義域 = OBI assay（材料を調べて情報を得る計画された過程）・値域 = 特性。そのまま合う |
| 測る（→ 物性） | `sosa:observes` | 不採用 | 主語が Sensor（装置）。手法ではない |
| 測る（→ 材料） | `sosa:hasFeatureOfInterest` | 採用 | 「観測と、その質を観測された実体の関係」。手法の語を「その手法での測定」と読めば定義が合う（RO_0009006 の assay と同じ読み） |
| 測る（→ 材料） | `OBI_0000293` has specified input | 不採用 | 計画に書かれた入力一般（試薬も含む）。測られる対象より広い |
| 測る（→ 材料） | CHMO `has analyte` | 不採用 | analyte は量を測られる成分。試料そのものとは違う |
| 変わる | `RO_0001001` derives into | 採用 | 古い実体が無くなるとき新しい実体が始まり、物質の大半を受け継ぐ。相の変化に合う。両端は別の実体 |
| 変わる | `RO_0002494` transformation of | 不採用 | `develops from` の下位（生物の発生）で、向きも逆 |
| 競合する | RO・BFO・SOSA／SSN・OBI・IAO・CHMO・SIO・PATO・ENVO | **無し** | ラベル・定義とも該当なし。OLS 全体でも schema.org `competitor`（スポーツの出場者）だけ |
| 物性 | `RO_0000053` has characteristic（旧 bearer of） | 採用 | 担い手 → それに依存する特性。値域 = specifically dependent continuant |

設計時の候補（記憶からの IRI）3 つは、どれも正本と一致した。差し替えは無い。

### 3. 自前の述語を許す条件（[`sky-chart-two-entries.md`](sky-chart-two-entries.md) の「自前の述語は置かない」を改める）

利用者裁定（2026-11-17）: 自前を許す。ただし

1. 標準を探して**無かったものだけ**。意味の近い標準で代用しない。
2. 「探したが無かった」（探した語彙と日付）を、この ADR とコード（`crosswalk_runtime.OWN_RELATIONS`）に残す。
3. 後で標準が見つかれば、`owl:equivalentProperty` で結ぶ（IRI は変えない）。
4. 置き場は `rel:` だけ。`sv:` に述語は置かない。

今ある自前の述語は `rel:competesWith` の 1 つ。定義: 「2 つの材料（相）が、同じ条件のもとで
一方が出来ると他方が出来にくい関係にある。向きは無い」。

### 4. 役割の札

- 種類（class）の用語に、任意の札 `material`（材料）・`method`（手法）・`concept`（概念）・`other`（そのほか）
  を付けられる。項目には付けない（物性は項目という種別で分かる）。
- 保存は共有語彙 graph（`xw:role`・誰が付けたか `xw:roleBy`・いつ `xw:roleAt`）。人が後から直せる
  （`PUT /api/vocab/terms/{slug}/role`）。
- 検査は**全部の入口**で同じ（`assert_alignment` と、下書き → 確定の層）。新しい 6 つの述語にだけ当てる。

| 述語 | from の札 | to の札 |
|---|---|---|
| `makes` | 材料以外 | 材料 |
| `measuresProperty` | 材料以外 | — |
| `measuresMaterial` | 材料以外 | 材料 |
| `becomes` | 材料 | 材料 |
| `competesWith` | 材料 | 材料 |
| `hasProperty` | 材料 | — |

- 札のある端が表に合わなければ拒む。**札の無い端は通す**（札は任意で、取り込み元の種別は粗い）。
- 札を変えても、既にある関係は調べ直さない。
- AI の下書きの JSON に札の欄は足さない。札を付けるのは取り込み元（14b）と人。

### 5. 問いの型（知識で答える・PR 14a-2）

置ける関係は、どちらの端からも問える。型は閉じた集合で、答えは決定論のテンプレート
（`asterism.knowledge`）。確定した線だけを読む。新しい型は直接の線だけを引く（閉包なし）。

| 型（op） | 文 | 引く線 | 対象 X の位置 |
|---|---|---|---|
| `makers` | X を作る手法 | `makes` | to |
| `products` | X で作るもの | `makes` | from |
| `measurers` | X を測る手法 | `measuresMaterial`・`measuresProperty` | to |
| `measured` | X で測るもの | 同上 | from |
| `sources` | X に変わるもの | `becomes` | to |
| `outcomes` | X が変わる先 | `becomes` | from |
| `competitors` | X と競合するもの | `competesWith` | どちらでも |
| `properties` | X の物性 | `hasProperty` | from |
| `bearers` | X を持つ材料 | `hasProperty` | to |
| `missing` | X でまだ無い物性 | `hasProperty`（否定と比較） | — |

- 既存の 3 つ（`factors`＋向き・`parts`・`kinds`）は変えない。型は op として足し、`direction` は `factors` 専用のまま。
- 「まだ無い物性」＝ X が持たない項目のうち、X 以外の 2 つ以上の語が持つもの。答えの行に、持っている語（最大 5）と数を添える。
- 対象の種別を検査する（`bearers` は項目・`measurers` はどちらでも・ほかは種類）。
- 論文からの提案（`paper_propose`）も新しい 5 つの関係を出す。問いは関係の相手（to）を対象に機械が組む
  （作る → `makers`・測る → `measurers`・変わる → `sources`・競合する → `competitors`・物性 → `bearers`）。
- 退けた案: `factors` の向きとして足す（向きは「値を下げる／上げる」の意味で、関係の種類ではない。AI が 2 つの欄を
  そろえて書く必要が出る）／設計の 5 つだけを足す（置けるのに問えない端が残り、入った関係から問いを機械で作れない）。

## 退けた案

| 案 | 理由 |
|---|---|
| 種別を 5 値（材料・物性・手法・概念・そのほか）にする | 種別は `rdfs:Class`／`rdf:Property` の宣言から引く 2 値で、述語ごとの表・循環・地図が前提にしている。役割は別の軸にした |
| 役割の検査を下書き → 確定の層だけに置く | `/api/crosswalk/align` などの直接の入口が抜け道になる |
| 「測る」を自前の述語 1 つにする | 物性の側にぴったりの標準（`RO_0009006`）がある |
| 「下げる・上げる・変える」の相手を材料に広げる | 値を下げるのと生成を妨げるのは別の意味。「X を下げるもの」の答えが濁る |
| 意味を言えない組に「関連する」を置く | 問いで役に立たない線が大半になる（design.md §23.8） |

## 残した穴

- RO に `regulates characteristic`／`positively …`／`negatively …`（`obo:RO_0019000`〜`0019002`・過程 → 質）がある。
  既存の「変える・上げる・下げる」（`RO_0002211`〜`0002213`・過程 → 過程）より意味が近い。既存の IRI は
  確定済みの関係の同一性なので変えていない。移すなら、引っ越しの手順を別の ADR で決める。
- `sosa:hasFeatureOfInterest`・`RO_0000053` は、データセットの行のデータにも使われうる述語。alignment graph は
  FROM-merge で合流するので、種類どうしの線と行どうしの線が同じ述語で並ぶことがある（`BFO part of` と同じ事情）。
- 新しい型の閉包は無い（「X の物性」は X の一種・一部の物性を含めない）。要ると分かってから足す。
- 未確認の関係を答えに使う（印つき）のは 14e。今の答えは確定した線だけ。
- 役割の札を画面で見る・直すのは 14e。今は API だけ。
- `rel:` の IRI を引いても、説明のページは無い（`sv:`・`xw:` と同じ）。
- 試しで集合に無くて付けられなかった関係（同じ結晶構造・比較・併用・条件や手段になる・説明する・
  合成を妨げる／促す）は、実データで要ると分かってから足す。

## 更新 log

- 2026-11-17: 起案・決定（PR 14a-1）。
- 2026-11-18: §5 問いの型を追記（PR 14a-2）。
