# 上位構造 — 星座の名前: 人が鋳造する共有のことばと、種類どうしの線、問いから始める流れ

Status: **proposed (2026-10-09・利用者裁定 6 点済み・checker 19 件＋反証 10 件＋再検証 4 視点を反映・実装前)**
owner: kumagallium

前提 ADR（本書はこれらを**覆さず**積む。例外は §3.1 に明記）:
[`crosswalk-hub.md`](crosswalk-hub.md)（値の一致で共有実体を鋳造する薄いハブ・F15 自動つなぎ・⑤ つながり画面）、
[`crosswalk-multi-perspective.md`](crosswalk-multi-perspective.md)（上位は複数の視点＋後から引く対応づけ・alignment graph）、
[`shared-vocab-graph.md`](shared-vocab-graph.md)（ことばの地図・決定論・読み取り専用）、
[`external-standard-alignment.md`](external-standard-alignment.md)（標準への接地・直接再利用第一・右サイズの形式化）、
[`ontology-canonical-lifecycle.md`](ontology-canonical-lifecycle.md)（データセットの TBox は per-dataset・脱結合）、
[`ontology-mapping-boundary-and-provenance.md`](ontology-mapping-boundary-and-provenance.md)（共有オントロジーを投機的に先出ししない）、
[`deterministic-design-assembly.md`](deterministic-design-assembly.md)（決定論）、
[`meaning-before-identity.md`](meaning-before-identity.md)（作る UI ③ 意味をつける・K47 ④の 2 問・K48 handles）、
[`skeleton-from-easy-judgments.md`](skeleton-from-easy-judgments.md)（D1 測る・聞く・導く、D2 ④を独立した段に・AI の事前チェックは置かない）、
[`design-consult-chat.md`](design-consult-chat.md)（D5 AI は代行しない）、
[`ask-quality-and-generality.md`](ask-quality-and-generality.md)（D1 壊れたツールを Ask／MCP に出さない）、
[`object-cards-ui.md`](object-cards-ui.md)（O54 観点＝宣言ツール・言葉は 1 つ）、
[`kantan-mode-two-tier-ux.md`](kantan-mode-two-tier-ux.md)（K2・K9・K10 公開ダイアログ・K35・K63）。
関連する並行作業: `global-network-view.md`（別セッション・全体グラフ・branch `feat/network-view-*` にだけあり、この worktree には無い。§2.6 の差し替え口を参照）。

## 1. きっかけ — 線は値でしか引けず、ことばどうしの関係を人が書いて育てる場が無い

Asterism の「作る UI」が作るのは**データセット 1 つ分のオントロジー**（種類・項目・意味・
単位・ID 規則）で、公開時に `…/graph/ontology/{id}` へ投影される。データセットを**またぐ**
構造は crosswalk（値の一致で共有実体を鋳造するハブ）だけで、ことばどうしの関係は
「視点（xw:）どうし」と「データセットの語 → 標準語（≡ のみ）」しか書けない。

### 1.1 棚卸し（2026-10-09・v0.51.0）

| 何を | どの画面 | 保存先 | 述語 | 両端に許すもの |
|---|---|---|---|---|
| 視点の種類 ↔ 視点の種類 | つながり › 詳細 › つながりどうしを対応づける | `…/canonical/crosswalk/alignment` | `owl:equivalentClass` / `rdfs:subClassOf` | xw: のハブ種類どうしだけ |
| 視点の項目 ↔ 視点の項目 | 同上 | 同上 | `owl:equivalentProperty` / `rdfs:subPropertyOf` | xw: の link predicate どうしだけ |
| データセットの語 → 標準語 | データセット詳細 › 外部の標準に合わせる | 同上 | `owl:equivalentClass`（既定固定） | 目的語は `known_vocabs.yaml` の実在 term だけ |
| 項目 → 量の種類 | データセット詳細 › 外部の標準に合わせる（量の種類の行） | 同上 | `qudt:hasQuantityKind` | 目的語は QUDT の QuantityKind（個体） |

事実として、`POST /api/crosswalk/align` は任意の絶対 IRI を両端に受けるので、データセットの
種類どうしの `subClassOf` は**保存できるのに書く画面が無い**。`ontology_projection.py` は
`subClassOf` を出さない。`schema_summary` は ontology graph のラベルは読むが alignment graph の線を
**意味として**は読まない（FROM-merge に入っているので件数には混ざる）ので、線を書いても Ask
（NL→SPARQL）には「上位で問える」こととして見えない。地図は読み取り専用。作る UI
③④は既存の語を提案しない（Reuse/New 判定は IRI の完全一致だけ）。

### 1.2 実データの症状（デスクトップ版・XRD 実験／XRD 参照カード／Gapminder・約 21,800 トリプル）

| 症状 | 中身 |
|---|---|
| ① 同じ概念の種類が別々に立つ | 「Record」が 2 つ（回折の測定点 3,001 件・参照カードのピーク 47 件）、「Card」も 2 つ。地図でも凡例でも別物。まとめる名前を置く場が無い |
| ② 同じものが値でもつながらない | 試料名「Al3V_bulk」と化学式「Al3 V」は同じ物質だが、別項目の別文字列。上位の**項目**（組成）で合流させる手段が無い |
| ③ データセットをつなぐ手段がハブだけ | ハブの無い 3 データセットは地図の上で互いに無関係に見える |

### 1.3 作る UI の流れへの違和感（利用者・2026-10-09）

現行の 7 段は ① 入れる → ② AI に読ませる → ③ 意味をつける → ④ つながりを選ぶ → ⑤ かたちを
たしかめる → ⑥ ためす → ⑦ 公開する。利用者の評価は「③（項目の意味）までは良い。その後の
④で**とりあえず他のデータセットに使いそうな項目を選ばせる**段が中途半端」。

④の中身は 2 問（K47）: 問①「この 1 件を名指す番号はどれ」（候補が 2 つ以上のときだけ）、
問②「他のデータとつながる手がかりはどれ」（他のファイルや他人のデータにも同じ表記で出てくる
値の列に ☑）。違和感の正体は問②で、「**この列は何か**」ではなく「**将来誰かがこれを使うか**」を、
相手も問いも無い 1 データセットの中で聞いている。☑ は `handles.json` に残り、相手側も ☑ して
値が重なったときだけ自動でつながる（F15）ので、1 つ目のデータセットでは何も起きない。
AI の事前 ☑ 推薦は「精度が悪いと惑わす」として退けられており（D2）、材料が無い問いである。

利用者の問題意識: **本当に重要なのは上位構造**。「A 種類は B 種類の一種」「この 2 つの
『試料』は同じ概念」を**人が書いて育てる**場が要る。また**データが無い状態でも問い
（コンピテンシー質問）から語を作れ、データを見てからも問いを作れる**双方向の流れが欲しい。
ただしトップダウンの単一オントロジーは作らない（主軸は「引用できる事実」。上位も**星をつなぐ線**）。

## 2. 決定

### 2.0 利用者裁定（2026-10-09・6 点）

| 判断点 | 裁定 |
|---|---|
| 骨格 | **B 星座の名前**: 人が共有のことば（種類・項目）を鋳造でき、データセットの種類を `subClassOf` で吊るし、標準にも `≡` で結ぶ。A（線だけ）と C（標準だけ）を含む |
| CQ の縛り | **共有語の鋳造に CQ 1 つ以上を必須**。種類どうしの線だけなら任意（勧めるだけ） |
| 共有語の IRI | **共通名前空間 + slug**: `https://kumagallium.github.io/asterism/vocab/shared#<slug>`。どのインストールでも同じ slug は同じ語（視点 `xw:` と同じ流儀） |
| 地図の操作 | **地図で丸を 2 つ選んで始め、確定はフォーム**。配置は決定論のまま |
| ナビ | **「作る」の作る場所を データセット／つながり／ことば の 3 つにする**（ホームとアクティビティはそのまま残る）。「共通の言葉」を「ことば」に改名して拡張 |
| 流れ | **双方向・④問②を③に吸収**: 「ことば」で問いから語を置ける（データ無しでも可）。⑥ ためす に自分の問いを足せる。④の問②は③へ移し、④は問①だけにする（§2.5） |

裁定後の反証（10 件・§7）で、「問②を**消す**」形は K48 の opt-in・骨格の受け口・コールドスタート・
見直し時の上書きを壊すと確認した。よって吸収は「**☑ を③の列ごとの行へ移す**」形で行う（§2.5.2）。

### 2.1 共有のことば（星座の名前）

- **定義**: 人が鋳造する、実体を持たない **TBox だけの語**。種類（`rdfs:Class`）か項目
  （`rdf:Property`）のどちらか一方。`rdfs:label`（必須・日本語）、`rdfs:label@en`（任意・標準語の
  照合に使う）、`rdfs:comment`（任意）。
- **視点（ハブ）との違い**: ハブは値の一致から**実体（ABox）を鋳造**する（`xw:Composition` の
  個体が生まれる）。共有のことばは**名前だけ**で、個体を生まない。問いは「ことば ⊂ 線 ⊂ 事実」の
  順に下りて、事実は常に各データセットの graph にある。
- **IRI と slug**: `sv:<slug>`（`sv:` = `https://kumagallium.github.io/asterism/vocab/shared#`）。
  slug は `^[a-z][a-z0-9_]*$` で**人が ASCII で入力する**（日本語 label から決定論に作れないため。
  英語 label か標準語の local name があるときだけ、それを既定値として提案する）。同じ slug の
  再鋳造は `409`（既存の語を使う）。同じ slug = 同じ語（インストールをまたいでも）。これは
  「同じ名前を付けた 2 人は同じ概念を指している」という**検証を要する主張**で、composition の
  正規化と同じ扱い（違うなら別 slug に直す）。今の snapshot 交換はデータセット単位で
  `vocab/shared` も alignment graph も運ばない（`exchange.build_snapshot`）ので、「交換で合流」は
  manifest に両 graph を載せる将来の作業（§5）。
- **保存先**: 新しい named graph `https://kumagallium.github.io/asterism/graph/vocab/shared`
  （TBox 専用。`…/graph/ontology/{id}` と同じ扱いで、`substrate.readable_graph_iris` と
  `canonical_merge_query` の allowlist と `schema_summary` に**明示的に足す**（今の
  `ontology_graphs()` は `…/graph/ontology/` の前方一致しか見ないため、足さないと label を
  どこからも引けない）。allowlist は「指定されたときだけ FROM に入れてよい」集合で、既定の
  FROM-merge・`kind_counts`・`classes_index` には入れない — `?s a ?c` の件数集計に `rdfs:Class` の
  個体として混ざるのを避ける）。線は §2.2 のとおり既存の alignment graph。
  `sv:` は 1 つの名前空間・1 つの graph だが、語どうしは独立で後から線で結ぶ（§3.1 (d)）。
- **鋳造の入口は標準語を先に出す**（`external-standard-alignment.md` §2「直接再利用が第一」）。
  フォームは英語 label（任意）か、③から来たときは列名（ASCII）で `ground_terms` を引き、exact 級の
  標準語があれば「標準の語をそのまま使う」を既定にする。共有語の鋳造は人がそれを退けたときだけ
  （退けた標準語と理由を `xw:declinedStandard` / `xw:declinedReason` として語に記録する。`rdfs:comment`
  は人の説明のためにとっておく）。英語 label も列名も無ければ標準語の提示は出ない（日本語
  label だけでは `ground_terms` が照合できない。§5）。
- **鋳造の条件（ゲート）**: CQ を 1 つ以上**確認して保存する**（§2.3）。鋳造と最初の CQ の保存は
  **1 操作**（`POST /api/vocab/shared` の body に `cqs` を 1 件以上含める。無ければ `422`）。
  問いが先でも語が先でも、保存の瞬間は同じ。
- **「まだ線になっていない」（孤立）の定義**: **CQ に答えるデータセットが 0**（= 上位パス §2.2 で
  届く `dataset` kind の子孫に、公開済みの実データが無い）。共有語どうしの線は数えない（共有語だけの
  ピラミッドは孤立のまま＝木を上から作っても「線」にはならない）。孤立の語は一覧と地図では
  見えるが、**Ask／MCP／`schema_summary` には出さない**（`ask-quality-and-generality.md` D1 の
  「壊れたツールを出さない」に倣い、本書が決める）。孤立の語が 20 を超えたら一覧の上に注意を出す
  （上限ではない）。これはハブの「singleton は鋳造しない」より**弱い門**で、意図的な緩和である:
  ハブは値という証拠が要るが、共有語は問いが証拠で、データが来るまで待てる（§3.1 (c)）。
- **削除**: 線が 1 本でも残っていれば拒む。線を外してから語を外す。その語だけを `for_terms` に持つ
  CQ は一緒に消し、複数の語を持つ CQ からはその語を外す（追加のみ・可逆・順序が来歴に残る）。

### 2.2 線（種類どうし・項目どうし・標準へ）

- **述語は既存の閉集合のまま**: `owl:equivalentClass` / `rdfs:subClassOf` /
  `owl:equivalentProperty` / `rdfs:subPropertyOf` / `qudt:hasQuantityKind`
  （`crosswalk_runtime.ALIGN_RELATIONS`）。増やさない。
- **両端の kind**（IRI から決定論で判定する。記録ではなく読み取り時に計算）:

  | kind | 判定 | 例 |
  |---|---|---|
  | `dataset` | いずれかの `…/graph/ontology/{id}` に `rdfs:Class`／`rdf:Property` として現れる | `xrd-cbde8062` の Record |
  | `perspective` | `xw:` 名前空間 | `xw:Composition` |
  | `shared` | `sv:` 名前空間 **かつ** `vocab/shared` graph に鋳造済み（未鋳造なら `422`） | `sv:diffraction_point` |
  | `standard` | `known_vocabs.yaml` の名前空間（QUDT の `quantitykind/` も含める） | `cmso:Material` |
  | `unknown` | 上のどれでもない | — |

  `unknown` は**拒まない**（`POST /api/crosswalk/align` が任意の絶対 IRI を受ける既存契約を守る）。
  画面が出す候補は 4 kind だけ。種類は種類と、項目は項目とだけ結べる（今の「視点をつなぐ」の
  kind 制約を踏襲。混在は `422`）。`hasQuantityKind` だけは項目 → 量の種類（個体）。
- **保存先は既存の `…/canonical/crosswalk/alignment`**（promoted・FROM-merge に入る・
  dated・可逆）。来歴ノード（`xw:Alignment`）に両端の kind と、`dataset` ならデータセット id を
  **記録として**足す（`xw:alignSourceKind` / `xw:alignTargetKind` / `xw:alignSourceDataset` /
  `xw:alignTargetDataset`。既存の `alignSource` / `alignTarget` の命名に揃える）。
  一覧 API が返す kind は読み取り時の計算が正（既存の行には来歴が無い）。既存の `fromPerspective` /
  `toPerspective` はそのまま。一覧の `?scope=`: `perspective` = 両端が perspective（今の
  「つながりどうしを対応づける」と同じ集合）、`standard` = target が standard（今の「外部の標準に
  合わせる」と同じ）、`shared` = どちらかが shared、`dataset` = どちらかが dataset かつ target が
  standard でない。scope は重なりうる（画面ごとのフィルタ）。`perspective` は「両端が読み込めた
  視点の語」で、設定が読めない視点の線は今と同じく「その他」に落ちる。`unknown` 端の線は scope
  無しの全件にだけ出る。記録された kind が `dataset`／`shared` なのに今は解決できない線は
  `broken: true` で返し（記録の無い旧行は `unknown` のまま）、地図は描かず一覧が「切れている」と
  示す（消さない）。
- **データセットの ontology graph には書かない**。線はすべて外に置く（per-dataset TBox の
  脱結合を守る。データセットを作り直しても線は残る）。
- **上位パス（共通の定義）**: 種類は `(rdfs:subClassOf|owl:equivalentClass|^owl:equivalentClass)*`、
  項目は `(rdfs:subPropertyOf|owl:equivalentProperty|^owl:equivalentProperty)*`。`≡` は片方向 1 本の
  triple で保存されるので、辿るときは逆向き（`^`）も含める。CQ（§2.3）・循環検査・孤立の判定・
  `upper_map`（§2.6）はすべてこの 1 つの定義を使う。
- **循環の禁止**: `subClassOf` / `subPropertyOf` を書く前に「target から上位パスで source に
  届くか」を `ASK` で確かめ、真なら `409`（決定論）。
- **CQ は任意**（§2.0）。付けるなら §2.3 の CQ を線の来歴ノードから `xw:answersCq` で参照する
  （`sv:` は slug の名前空間なので述語を置かない）。

### 2.3 問い（CQ）— 線を引く理由と検査

確立した手法（Grüninger & Fox）を**双方向に**使う。語を 1 つ鋳造するたびに、その語があることで
答えられる問いを 1 つ添える（語が先）。あるいは問いを先に書き、答えに要る語をそのあと置く
（問いが先・データ無しでも可）。どちらも「問いのない語は作らない」というゲートは同じで、
保存は語と問いを**同時に 1 操作**で行う（§2.1）。語の無い問いは画面の下書きに留め、保存しない。

- **問い＝観点＝宣言ツール**（O54）。問いは `query_tools.yaml` の 1 項目で、Ask の道具・MCP の
  tool と同じ物。利用者向けの言葉は「**問い**」に統一し、種類のページの「この種類の観点」は
  「この種類への問い」に改名する（§3.1 (f)）。
- **問いは 2 つの場所で生まれる**:

  | 場所 | 対象 | 保存先 | 公開 |
  |---|---|---|---|
  | 「ことば」画面 | 共有語（横断の問い） | `registry/vocab-shared/query_tools.yaml`（`for_terms: [<sv:IRI>…]`・名前 `cq_<slug>_<op>`・`op` は種類＝`count`、項目＝`values`） | `for_terms` のうち 1 つでも孤立でない語があれば Ask／MCP に出す |
  | ⑥ ためす | 1 データセット（自分の問い） | 下書きの間は registry artifact `questions.json`。公開時に `registry/<id>/query_tools.yaml` へ名前 `q_<hash8>` で upsert | 公開後に Ask／MCP に出る（既存の自動 3 本と同列） |

  1 データセットの問いが横断の問いに育つ（「このデータセットの試料は何件」→「どのデータセットにも
  試料は何件」）ときは、⑥か種類のページの「ことばへ写す」で**写す**（移さない）。写すときは
  その問いの種類／項目を `upper_map`（§2.6）で上位の共有語に置き換え、上位が無ければ鋳造フォームへ
  誘導する。元はデータセットのツールとして残る。既定の CQ は語ごとに 1 本（`for_terms` は 1 要素）。
  `for_terms` が複数になるのは「写す」で複数の語に置き換わったときだけで、SPARQL は各語の
  テンプレートの `UNION`。
- **形は閉じた選択で作る（LLM は使わない）**: 問いの文（題）は人が書く。SPARQL は「種類／項目／
  集計（件数・範囲・上位の値）」を**画面の選択肢から選んで**決定論のテンプレートに流し込む。
  自由記述の SPARQL は作らない（作りたい人は既存の typed tool の経路へ）。
  `lint_query_tool` を通らなければ保存しない（新規）。再設計で IRI が変わった既存の問いは選択から
  再生成し、再生成が lint で落ちたものだけ `lint_error` 付きで下書きに残して公開時はツール化しない。
- **既定のテンプレート（共有語の鋳造時に自動下書き・人が題を直せる）**:

  | 語の種類 | CQ | SPARQL の骨 |
  |---|---|---|
  | 種類 | 「〈label〉は、どのデータセットに何件あるか」 | `?k (rdfs:subClassOf\|owl:equivalentClass\|^owl:equivalentClass)* <sv:X> . GRAPH ?g { ?e a ?k }` を `?g` で集計 |
  | 項目 | 「〈label〉の値は、どのデータセットにいくつあるか」 | `?p (rdfs:subPropertyOf\|owl:equivalentProperty\|^owl:equivalentProperty)* <sv:P> . GRAPH ?g { ?e ?p ?v }` を `?g` で集計 |

  `?g` はデータセットの版 graph に限る（ハブ graph を除く。旧形式の `…/canonical/crosswalk`（末尾
  スラッシュ無し）も含めて、`substrate.hub_perspective_name` が `None` 以外を返す graph と alignment
  graph を除く。xw: の種類を共有語に吊るしたときにハブが「データセット」として数えられるのを防ぐ）。
  数えるときは `?g` を `dataset_id_of_canonical_graph` でデータセット id に畳む（版 graph の二重数えを防ぐ）。
  推論器は使わない。SPARQL 1.1 の property path が FROM-merge 上の alignment graph の線を辿る
  （§2.2 の上位パス）。これで「上位で問う」が**決定論・引用可能**なまま成立する。
- **データが無いときも答える**: 公開済み graph が 0 件のときは SPARQL を投げず「0 データセット」と
  返す（`canonical_merge_query` が空の FROM を拒む／空の `FROM NAMED` が draft まで読む既知の罠を
  避ける。`kind_counts` と同じ守り）。
- **答えるデータセット数の計算**: 1 本の集計 SPARQL（`VALUES ?t { <sv:…> … }` と上位パスで、語ごとの
  データセット id の `COUNT(DISTINCT)`）で全語分を一度に出す。結果は api が `registry/vocab-shared/
  wired.json`（語 → 答えるデータセット数・決定論の派生ファイル）に書き、鋳造・削除・線の追加／
  取り消し・公開（名前だけの公開を含む）・データセットの削除／公開取り消しのたびに作り直す
  （runtime の `recompute_wired(client, root)` を api が呼ぶ。契機の一覧はここが正）。CQ の答える
  データセット数は `for_terms` の語の**最大値**。MCP の `load_all_query_tools` は store を引かず
  このファイルで孤立の CQ（最大値が 0）を除く。
- **検査としての CQ**: 札に「N データセットが答える」を出す。鋳造直後は 0、線を 1 本引くと 1、
  2 本目で 2 になる。この差が線が効いた証拠。画面の札は表示のみ。**実 Oxigraph の統合テストでは
  件数を assert する**（0 → 1 → 2 の遷移）。
- **⑥ の問いの規律**（反証で確定）: 名前は `q_<hash8>`。予約名 `counts_by_kind` / `value_range` /
  `top_value` と `q_` 接頭辞は人の保存経路（`POST /api/datasets/{id}/tools`）で `422`。公開時の
  書き込みは**名前による upsert と削除**（既存の「予約 3 本だけ置換・他は保持」の規則は変えない。
  別の関数で書く）。公開の経路は `promote_dataset` と**名前だけの公開**（`ontology-canonical-
  lifecycle.md` §3.2・意味だけの見直し）の両方で、どちらも `questions.json` の upsert／削除と
  `upper.json` の消費を同じ関数で行う。⑥ の画面では staged の draft graph に対して走らせ
  （trial-queries と同じ経路）、公開後は canonical で走ることを画面に書く。`query_tools.yaml` の
  書き込み（新関数と、既存の `write_registry_query_tools`・`registry._write_tools`）は tmp → replace の
  atomic にし、既存ファイルが読めないときは上書きせず中止する（既存関数には atomic 化と中止処理だけを
  足し、保持の規則のテストは維持する）。
- **`vocab-shared` は registry の項目だがデータセットではない**（meta `is_shared_vocab: true`・
  `promoted: true`。`classes_index`・データセット一覧・discover・autolink・視点一覧は共通の判定で除く。§5）。
  各ツールに `for_terms: [<sv:IRI>…]` を持たせる（`QueryTool` に欄を足す。今の `parse_query_tools` は
  未知キーを落とす）。

### 2.4 Ask／SPARQL への効き方

- `schema_summary` に `shared_terms` を足す: `[{iri, label, comment, kind: class|property,
  narrower: [{iri, dataset_id, label}], standards: [iri], cqs: [tool_name]}]`。
  **孤立の語（§2.1）は入れない**。`cqs` は答えるデータセットが 1 以上のものだけ。語の数に上限は
  置かず、`narrower` だけ `max_classes` と同じ上限。`…/graph/vocab/shared` と alignment graph から組む。
  あわせて `dataset` ↔ `dataset` の線（種類どうし）も `lines` として平らに返す。
- NL→SPARQL のプロンプトに 1 行足す: 「上位の種類／項目で問うときは §2.2 の上位パスを使う
  （推論器は無い）」。線をクエリに**自動適用**するのではなく、LLM が書くときの手がかりに留める
  （`crosswalk-multi-perspective.md` Phase 2 の caveat と同じ）。
- 線は生 SPARQL と MCP で何も変えずに辿れる（既に FROM-merge の中）。共有語の label／comment を
  生 SPARQL から引くには `readable_graph_iris` の allowlist に `vocab/shared` を足す（§2.1）。

### 2.5 UI — 作る場所を 2 つにし、流れを双方向にする

利用者の理解をそのまま骨格にする: **「データセットごとに事実から作る場所」と「事実と独立に
ことばを作る場所」**。

| 左ナビ「作る」の作る場所（ホーム・アクティビティはそのまま） | 何をする | 事実との関係 | 保存先 |
|---|---|---|---|
| データセット | 作る UI 7 段（1 データセット分の種類・項目・意味・単位・番号）＋取り込み | 列・値が無ければ作れない | `ontology/{id}`・`canonical/{id}` |
| つながり | 値の一致でハブを鋳造（視点） | 値から実体を作る | `canonical/crosswalk/<id>` |
| **ことば**（旧「共通の言葉」） | 問いを書く・共有のことばを鋳造・線を引く・標準への接地・地図 | **事実と独立に作れる**。線が事実につながって初めて意味を持つ（孤立は表示） | `vocab/shared`・`canonical/crosswalk/alignment` |

#### 2.5.1 「ことば」画面（問いが先でも、語が先でも）

1. **問いを書く**（任意の入口）: 題を 1 文書き、答えに要る語（種類・項目）をその場で置く
   （標準語が先・§2.1）。保存は語と問いで 1 操作。Ask で横断の問いに答えられなかったとき、
   「この問いに答えられるようにことばをつなぐ」でここへ持ち込める（題だけ query string で持ち越す。
   解釈に LLM は使わない）。
2. **地図が主役**（`shared-vocab-graph.md` §1 を踏襲）。帯を 1 つ足す: 標準の帯の上に
   **共有のことばの帯**（「種類まで」の段と「詳しく」。「データセットごと」の段では描かない）。
   種類の丸 → 共有語へ `⊂` の線（向きあり）、共有語 → 標準へ `≡`。共有語の丸の大きさは子の件数の
   合計（派生値と明記）。配置は決定論のまま。**データセットが 0 件でも帯は描く**（今の
   「データセット 0 件なら描かない」をやめる）。
3. **地図で丸を 2 つ選ぶと「線を引く」**が出る。確定は小さなフォーム（関係は閉集合から
   kind で絞る・CQ は任意）。配置は変えない（読み取り専用の原則は「配置」について維持し、
   「線の起点」だけ地図に置く）。
4. **共有のことばの一覧**（label・slug・子の数・問いと答えるデータセット数・「まだ線になっていない」札・
   孤立が 20 を超えたら注意）と「共有のことばを作る」フォーム（label・英語 label（任意）・slug（ASCII・
   既定値は英語 label か標準語から）・comment・標準語の提示と「標準の語をそのまま使う」既定・
   問い（既定の下書き・必須）・退けた理由）。
5. **線の一覧**（両端の kind・関係・日付・`broken`・取り消し）。今の「つながりどうしを対応づける」
   （`PerspectiveAlignment`）と「外部の標準に合わせる」（`DatasetGrounding`・量の種類の行を含む）
   はここへ移す。元の場所には「ことばで対応づける →」の導線だけ残す（§3.1 (b)）。

#### 2.5.2 データセットの 7 段（③に吸収・④は番号だけ・⑥に自分の問い）

| 段 | 変更 | 何が起きるか |
|---|---|---|
| ① 入れる・② AI に読ませる | 変えない | — |
| **③ 意味をつける** | **④問②の ☑ をここへ移す**。列ごとの行に「意味・単位・取り込む」に加えて「**つながる手がかり ☑**」を置く（④の番号の列（自動で決まったものも）は従来どおり ☑ 済みと見せて外せないが、`handles.json` には入れない — K48。骨格の `linkable` には入る） | ☑ は従来どおり**人が自分で付ける**（K48 の opt-in はそのまま）。隣に決定論の**当てはめ提案**を表示だけする（§2.5.3）。測定値の列は従来どおり ☑ 不可（判定を④から③へ移す）。当てはめ先が無い列も ☑ できる（コールドスタートの約束「付けておくと取り込むだけでつながる」を保ち、文言も③へ移す）。一般語（§2.5.3）は提案を出さないが ☑ はできる |
| **④ 番号を選ぶ**（旧「つながりを選ぶ」） | **問①だけ**。候補が 2 つ以上のときだけ出す。候補が 1 つなら段を畳んで手順バーに「番号は自動で決まりました」、候補が 0 なら畳んで「番号は仮置き（⑤で確認）」（今の仮置きと⑤の ⚠ はそのまま） | 名前は「ID を決める」にしない（⑤ かたちをたしかめる が ID の検査を自称しており衝突する）。K47 の「2 問に分ける」は維持し、「別画面にする」を改める。手順バーの番号は 7 段のまま、畳んだ段は薄く表示 |
| ⑤ かたちをたしかめる | **種類の当てはめ提案**を箱に表示だけする（「既にある『回折点』の一種にしますか」） | ③の行は列（項目）なので、種類の提案はここ。受けると `upper.json`。☑ の列は従来どおり骨格の受け口（値のカタログ・K33／K63）になる（`linkable` の算出元は ③ の ☑ ＋ ④ の番号の 1 か所） |
| **⑥ ためす** | **「自分の問い」を 1 行足せる**（閉じた選択で作る・§2.3） | 自動の問いと同じく draft graph で答えを見せ、「公開後は公開済みのデータで答えます」と書く。「ことばへ写す」は人が押す（条件は設けない） |
| ⑦ 公開する | 内側が増える | 公開ダイアログ（K10）に「線 N 本・問い M 件を書きます」を足す。投影 → `upper.json` の線（1 回だけ消費）→ `questions.json` のツール化（upsert）→ handles からの自動つなぎ（F15・従来どおり）。名前だけの公開でも `upper.json` の消費と `questions.json` のツール化は走る（自動つなぎは値が変わらないので走らない） |

#### 2.5.3 当てはめ提案（③・⑤）の規則

- **決定論・表示のみ**。列の label（③の意味）と列名を正規化し、共有語の label（ja/en）・標準語
  （`ground_terms` の exact 級。列名（ASCII）と英語 label で照合）・他データセットの項目 label と
  **完全一致**したときだけ出す。部分一致は出さない。正規化は K63 の `_meaning_key`（NFKC・casefold・
  空白の畳み）と**同じ規則**を 1 つの関数にし、両方の package でテストで同値を固定する。
  候補は孤立の語も含めて全共有語（`wired` で絞らない）。
- **出さない列**: 測定値（④の `noMeasure` と同じ判定）、一般語（`name` `id` `type` `date` `value`
  `no` と、それに当たる日本語。閉じた除外表 `GENERIC_LABELS`）。
- **D2 との関係**: 「AI の事前チェックは置かない」の理由は「精度が悪いと惑わす」。完全一致の
  決定論提案は誤りの型が違う（同名異義）ので、**提案は 1 行の表示に留め、☑ を自動で付けない**ことで
  同じ懸念に答える。受けるかどうかは人の指で決まる（D5 と同じ線引き）。
- **受けた提案の行き先**: 項目の当てはめは `fit`（`column-meanings.json` に `{term, kind, matched_by}`）と、
  公開時に書く線（`upper.json` に `{subject, term, relation: subPropertyOf|equivalentProperty}`）。
  種類の当てはめ（⑤）は `upper.json` に `{subject, term, relation: subClassOf|equivalentClass}`。
  `upper.json` は `handles.json` と同じ運び方で、公開で線を書き `applied_at` を付けて **1 回だけ消費**
  （人が「ことば」で線を取り消しても再公開で復活しない）。
- **☑ との関係**: 提案の横の「値でもつなぐ」を人が押すと ☑ が付く。これは**人が付けた ☑**で、
  `handles.json` には `{source, column, via: "fit", term}` と出どころを記録する（`via` 無しの旧形式と
  人が直接付けた ☑ は `tick`）。機械は `handles.json` を書かない（K48 維持）。
- **見直しで ☑ が消えない根拠**: API の意味は今のまま（`handles` を送れば置換・省略すれば保持）。
  見直しの開始時に `handles.json` を③の ☑ に**読み戻してから**送る（今の `hydrateLinkedHandles` と
  同じ）。読めていなければ `handles` を省略する。永続化された状態としての正は、☑ が `handles.json`、
  `fit` が `column-meanings.json`（UI は読み戻して編集し、送れば置換。二重に持たない）。骨格の
  `linkable` は ☑ ∪ ④の番号、`handles.json` は ☑ だけ（番号は入れない・K48）。
- **問いが先の場合の効き方**: 「ことば」で置いた語（孤立）は、次にデータが来たとき③⑤の当てはめ
  候補になる。語を置いた人がその列を見れば一致するので、問い → 語 → 列 の順でつながる。

### 2.6 全体グラフ（別セッション）との接点

`global-network-view.md`（branch `feat/network-view-*`）は値の点を「同じ述語 IRI の同じ値」で
合流させている。上位構造ができたら、合流の鍵を「上位の項目」（`rdfs:subPropertyOf*` で畳んだ
述語）に、点の色を「上位の種類」（`rdfs:subClassOf*` で畳んだ種類）に差し替えられるよう、
**読み口を 1 つ**用意する: `GET /api/vocab/upper` が「種類／項目 IRI → 最上位の共有語（無ければ
自分）」の対応表を返す（決定論・alignment graph を §2.2 の上位パスで畳む。複数の上位に当たるときは
slug の辞書順で先頭）。組み立て関数は `asterism.shared_vocab.upper_map(client)`。全体グラフ側は
これを鍵に使う（2026-10-09 に合意済み）。

## 3. なぜ哲学と整合するか

- **星座の名前であって、太陽ではない。** 共有語は「線の束に付けた名前」。答えるデータセットが
  0 の語は「まだ線になっていない」と表示され、Ask には出ず、共有語どうしを積んでも線にならない。
  木を上から作る画面は作らない（`crosswalk-hub.md` が「究極オントロジー」を退けたのと同じ線上）。
- **追加のみ・可逆・人がゲート。** 語の鋳造も線も新しい triple の追加で、既存の graph を
  書き換えない。取り消しは来歴ごと消す。削除順（線 → 語）が構造を守る。☑ も当てはめも
  「人が押したものだけ」が残る（K48 の opt-in をそのまま使う）。
- **決定論。** kind 判定・循環検査・CQ テンプレート・③⑤の提案・地図の配置、すべて乱数も LLM も
  使わない。**問いの解釈にも LLM を使わない**（題は人が書き、SPARQL は閉じた選択から組む）。
  語の `comment` を AI 相談に下書きさせることは本書の範囲外（既存の相談チャットで足りる）。
- **推論器を入れない。** OWL の意味論を実行しない。線は「人が検証した、引用できる事実」で、
  辿るのは SPARQL の property path。`external-standard-alignment.md` §5「右サイズの形式化」。
- **データセットの脱結合。** per-dataset TBox には触れない。線は外にある。
- **「直接再利用が第一」との関係**（`external-standard-alignment.md` §2）。共有語は自前 mint ＋
  橋渡しだが、同 ADR が過渡手段として許す 3 条件（外部標準が未成熟・流動的／粒度・モデリングが
  事実に合わない／段階導入）のうち、後の 2 つに当たる。標準が無い分野（Gapminder の年ごとの記録）は
  「粒度が合わない」の極端な場合として扱う。鋳造フォームは標準語を先に出し、人が退けたときだけ
  鋳造する。共有語 `≡` 標準 の線がある語は、再評価の時点で標準へ寄せられる（線は残す）。
- **地図の「読み取り専用」との関係**（`shared-vocab-graph.md` §3）。配置は読み取り専用のまま。
  地図に置くのは「線の起点（2 つ選ぶ）」だけで、確定はフォーム。同 ADR §7 に改訂を記す。

### 3.1 前提 ADR への改訂（例外として明記）

| 改める決定 | 改め方 | なぜ |
|---|---|---|
| (a) K47（`meaning-before-identity.md`）「④を 2 問の別画面にする」、D2（`skeleton-from-easy-judgments.md`）「③に 4 つ目の判断を足さず独立した段にする（利用者裁定）」 | 問②の ☑ は③の列ごとの行へ、④は問①だけ・候補が 2 つ未満なら畳む。K48「`handles.json` は人が自分で付けた ☑ だけ」と D2「AI の事前チェックは置かない」は**維持** | 利用者が 2026-10-09 に前の裁定を改めた（§1.3）。「1 画面 1 判断」の趣旨は、③の行が「この列は何か＋つなぐか」の 1 判断になることで保つ。同 ADR の末尾に本書への指し先を追記 |
| (b) `crosswalk-hub.md` ⑤「視点の対応づけはつながり画面の詳細の中に保つ」、`external-standard-alignment.md` §8「接地はデータセット詳細に置く」 | どちらも「ことば」の線の一覧へ移し、元の場所に導線 1 行を残す | 名前の線を 1 か所にまとめる（裁定: ナビ） |
| (c) `ontology-mapping-boundary-and-provenance.md`「共有オントロジーを投機的に先出ししない」 | データ無しの語を**問いの付いた予約**として許す。ただし孤立の間は Ask／MCP／`schema_summary`／snapshot に出さない | 括り出し（線）はデータが再発したときにだけ起きる、という本旨は保つ。先出しされるのは名前と問いだけで、利用側には見えない |
| (d) `crosswalk-multi-perspective.md`「視点は 1 つずつ別の graph・別の registry 項目」 | 共有語は 1 つの graph・1 つの名前空間に置く | 視点は値から実体を鋳造する装置で、build／retract の単位として graph が要る。共有語は名前だけで、独立性は「語どうしは線でしか結ばれない」ことで保てる |
| (e) `shared-vocab-graph.md` §3「読み取り専用」 | 配置は読み取り専用のまま。線の起点だけ地図に置く | 裁定: 地図の操作 |
| (f) `object-cards-ui.md` O54「観点」 | 利用者向けの言葉を「問い」に統一し、「この種類の観点」を「この種類への問い」に改名する（宣言ツールという実体は同じ。ui の i18n・`PageChatDrawer`・api の相談／会話プロンプト・manual の全章。英語の viewpoint も） | 問い＝観点＝ツールで 1 つの物に 2 つの言葉を見せない（O54 の原則）。利用者の語は「問い（CQ）」 |

## 4. 退けた案

| 案 | なぜ退けたか |
|---|---|
| A 線だけ（新語を作らない） | 「回折点」に当たる既存の語が無いと、片方のデータセットの種類の下にもう片方を吊るす非対称な形しか書けない。症状 ① が解けない |
| C 標準だけ（上位は curated 標準語に限る） | EMMO は不透明 IRI で引けず、分野外（Gapminder の年ごとの記録）には標準が無い。利用者の語が育たない |
| データセットの ontology graph に `subClassOf` を書く | per-dataset TBox の脱結合を破る。作り直しで線が消える |
| 上位語を「視点」として鋳造する | 視点は値から実体を作る装置。名前だけの語を混ぜると「ハブ＝実体の置き場」の意味が濁る |
| OWL 推論器（RDFS 閉包の materialize） | 決定論・引用可能の主軸と衝突。property path で足りる |
| CQ をすべてに必須 | 「同じもの」の線では問いが自明で重い。語にだけ必須にする（裁定） |
| ④問②を消す | 反証で退けた: ☑ は骨格の受け口（K33／K63）の材料でもあり、消すと骨格が変わる。語も標準も無いコールドスタートで手がかりが無くなり約束が消える。前書きの無い表で④が空画面になる |
| 当てはめから handle を自動で導く | K48 の「人が自分で付けた ☑ だけ」と F15 の両側 opt-in に反する。日本語 label は `ground_terms` で 0 点。見直しで既存の ☑ が上書きで消える |
| 問いの自由記述 SPARQL を LLM が下書き | 裁定(6)「問いの解釈に LLM を使わない」に反する。閉じた選択から決定論で組む |
| ⑥ の問いを直接 `query_tools.yaml` に書く | 予約名 3 本の置換規則に乗らず二重登録・削除漏れが起きる。公開前に MCP に見える。下書きは `questions.json`、公開時に名前で upsert |
| 孤立の判定を「子孫 2 つ未満」にする | 「線 1 本で答えるデータセットが 1」と食い違う。「答えるデータセットが 0」の 1 条件にする |

## 5. 影響・リスク

- **同名 slug = 同じ語**は主張であり、検証を要する。違う概念に同じ slug を付けた 2 つの
  インストールの語を将来 snapshot で運ぶと誤って合流する。対策: label と comment を必ず表示し、
  manifest に `vocab/shared` と alignment graph を載せる際（将来・`local-first-distribution.md` §5）に
  「同じ slug の語」を一覧で見せる。今の snapshot はどちらも運ばない（孤立の語は既定で交換しない）。
- **registry の `vocab-shared` がデータセットに混ざる**: `classes_index._promoted_datasets` は registry
  直下を走査し、`registry.list_datasets` は `meta.json` があれば全部返し、discover／autolink／視点一覧は
  `is_crosswalk` しか見ない。対策: `registry.is_system_entry(meta)`（`is_crosswalk or is_shared_vocab`）を
  1 つ置き、`list_datasets` の呼び出し側すべてと `autolink`・`crosswalk_runtime.list_perspectives`・
  `classes_index` で除く。
- **日本語の照合**: `ground_terms` の正規化は ASCII 専用で、日本語 label は 0 点になる。共有語・列の
  突き合わせには K63 と同じ正規化を使い、標準語は列名（ASCII）と英語 label で引く。英語 label も
  列名も無い語は標準語の提示が出ない（鋳造フォームに「英語名を入れると標準の語を探せます」と書く）。
  `known_vocabs.yaml` には日本語 label を足さない（権威 RDF からの検証採録という方針を守る）。
- **見直しで ☑ が消える**: 読み戻しを先に走らせる今の規則で防ぐ（§2.5.3）。☑ の正は `handles.json` 1 つ。
- **⑥ の問いの名前衝突と取り残し**: 予約名と `q_` 接頭辞は人の保存経路で拒む。人が⑥で消した問いは
  公開時の削除でツールからも消す。再設計で IRI が変われば選択から再生成し、lint 不合格は画面に出す。
  yaml の書き込みは tmp → replace、読めないときは中止（既存の `registry._write_tools` も同じ扱い）。
- **0 件ツール**: 孤立の語の CQ と、答えるデータセットが 0 の CQ は Ask／MCP／`schema_summary` に
  出さない（「ことば」画面だけ）。判定は `wired.json`（派生ファイル）で、MCP の読み込み時に store を引かない。
- **既存画面への波及**（移設が終わるまで）: `kindOverview` はデータセット種類どうしの `⊂` を向きなしの
  「対応」として描く。`CrosswalkView` の「その他」にデータセット↔共有語の線が混ざる。`DatasetGrounding` は
  標準への `⊂` も「接地済み」と数える（これは正しい）。段 2 で解消する。
- **④を畳むときの導線**: ④が畳まれていれば、③の「この意味で進む」が④の「形を組み立てる」の役を
  引き継いで骨格を組み立て、⑤の「戻る」は③へ戻す。手順バーの 7 段の番号は変えない（畳んだ段は薄く表示）。
- **改名の掃除**: 「つながりを選ぶ」「ID のつけかた」「観点」「共通の言葉」の古い文字列を ui（i18n・
  コメント・`skeletongate.json`・`SkeletonGate.tsx` の自称）・api（相談／会話プロンプト・`describe.py`）・
  manual から全件 grep で掃除する（画面名として使っている箇所だけ。履歴の行は残す）。`test_design_consult.py`
  は部分一致なので、ui 側を先に直す。
- **循環**は書く前に `ASK` で拒む。既存データに循環は無い（今は dataset 間の線が無いため）。
- **両端が解決できない線**（データセットを削除・作り直し）は `broken` で返し、描かず、一覧で
  「切れている」と示す。消さない（可逆・来歴）。
- **`kind_counts` への混入**: `vocab/shared` を canonical 集合に入れないことで避ける。
  `dataset_id_of_canonical_graph` は影響を受けない（graph base が違う）。`classes_index` は上の
  registry 側の対策が要る。
- **i18n**: 共有語の label は日本語必須・英語任意。画面名の変更は ja/en の `recipe.step4` と
  `links.title` を同時に直す（`recipeStepNames.test.ts`）。

## 6. 段階

| 段 | 中身 | 出口 |
|---|---|---|
| 1 runtime + api（語・線・問いの土台） | `asterism.shared_vocab`（鋳造＋CQ の 1 操作・一覧・削除・CQ テンプレ・`for_terms`・registry scaffold `vocab-shared`・`wired.json`・`upper_map`・正規化・`GENERIC_LABELS`）、`assert_alignment` の kind 来歴＋循環検査（上位パス）、`list_alignments` の読み取り時 kind・`scope`・`broken`、`readable_graph_iris`／allowlist に `vocab/shared`、`schema_summary.shared_terms`（孤立は除く）、`registry.is_system_entry` と利用側の除外、`GET /api/vocab/{shared,upper,fit}` | pytest green・実 Oxigraph で CQ が 0 → 1 → 2 と遷移しハブ graph を数えない・公開 0 件で 0・CQ 無しの鋳造が `422` |
| 2 UI「ことば」 | ナビ 3 項目・改名・問いを書く入口・共有語の一覧とフォーム（標準語が先・slug 入力）・線の一覧（移設・`broken`）・地図の帯（データセット 0 件でも描く）と「線を引く」・既存画面の波及の解消 | 症状 ① を手元の 3 データセットで解消（Record ×2 → 回折点 1 つ）。データ無しで問いと語を置けて孤立の札が出て Ask に出ない |
| 3 作る UI の流れ（api を含む） | ③ に「つながる手がかり ☑」と当てはめ提案、⑤ に種類の提案、④ を問①だけ・条件付きで畳む、⑥ に自分の問い、`questions.json`／`upper.json`／`handles.json` の `via`、promote と名前だけの公開での消費、公開ダイアログの一文、「観点」→「問い」の改名 | 2 つ目の XRD データセットで提案が出て、人が受けて公開すると線が引かれ、取り消した線が再公開で戻らない。見直しで既存の ☑ が消えない。前書きの無い表で④が畳まれる。⑥の問いが公開後に Ask の道具に出て、消すと消える |
| 4 連携 | 全体グラフの鍵差し替え（別セッション）、`subPropertyOf` の線からハブの参加者を提案（名前の線が値の線を誘う） | 症状 ② |

## 7. 更新 log

- 2026-10-09: 起草。棚卸し・症状・3 案比較・利用者裁定 5 点。checker（sonnet）の指摘 19 件を反映
  （上位パスに `≡` を含める・`upper.json`・registry 除外・kind は読み取り時に計算・`vocab/shared` を
  読む経路・ナビの文言・`xw:answersCq`・snapshot の現状）。
- 2026-10-09（同日・2 回目）: 利用者の「④の問②が中途半端」「データ無しで問いから作れないか」を受け、
  流れを双方向にする裁定（6 点目）。反証 10 件（5 主張 × ADR 整合／コード実現性）で「問②を消す」形を
  退け、「☑ を③の行へ移す・④は問①だけで条件付き・⑥に自分の問い・問いは閉じた選択で作る・
  孤立の語は Ask に出さない・日本語の正規化・見直しで ☑ を消さない」に直した。
- 2026-10-09（同日・3 回目）: 再検証 4 視点（条件充足・handoff との整合・参照の実在・哲学との矛盾）を
  反映。孤立を「答えるデータセットが 0」に一本化、鋳造と CQ を 1 操作に、`via:"fit"` を「人が提案ボタンで
  付けた ☑」と定義、☑ の正は `handles.json` 1 つ、④候補 0 は仮置きのまま、種類の提案は⑤、問い＝観点、
  `wired.json`、名前だけの公開、K10 の一文、§3.1 に前提 ADR への改訂 6 件を明記（D2 の「④は独立した段」は
  利用者の前の裁定を今日の裁定で改めたもの）。`meaning-before-identity.md` の指し先は §12。
  handoff = `handoff_to_claude_code_upper_structure_shared_terms.md`。
- 2026-10-09（同日・4 回目）: 最終確認 2 視点（整合・参照）を反映。`linkable = ☑ ∪ ④の番号`／`handles = ☑ だけ`
  を 1 関数の 2 つの戻り値として定義（K48）、allowlist は「指定されたときだけ FROM に入れてよい」集合、
  `broken` は記録された kind がある行だけ、CQ の答える数は `for_terms` の最大値、ハブ除外は旧形式
  `…/canonical/crosswalk` を含む、来歴の述語名を `xw:alignSource*` に揃える、改名の掃除範囲を api と
  manual 全章に広げる、④が畳まれたときは③の「この意味で進む」が骨格を組み立てる。
