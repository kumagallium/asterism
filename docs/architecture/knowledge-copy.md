# ADR: 取り込み元の知識を 1 つのデータセットに写す（知識の束）

状態: **決定**（2026-11-18）。実装 = PR 14b。設計の経緯は AI for Science 側
`ui-redesign-2026-10-19/design.md` §23（段 1「写す」・段 2 のうち語を作るところ）。

関連: [`source-switch-mcp-client.md`](source-switch-mcp-client.md)（呼ぶ側・取り込み元プロトコル）/
[`relations-for-experiments.md`](relations-for-experiments.md)（関係の集合と役割の札）/
[`../examples/graphium/README.md`](../examples/graphium/README.md)（Graphium とつなぐ見本）。

## 1. 文脈

ノートのアプリ（Graphium）に溜まった知見を Asterism に入れても、知識グラフは点が数個出るだけ
だった。型の付いた知見だけを行として写していたからで、手元の実データでは全体の 3.6% にしか
ならない。入れた直後に知識グラフが出るようにするには、まず**全部**（未確認の知見も）を写し、
そこから語と関係を作る必要がある。この ADR は最初の段 —「写す」— を決める。

| 段 | 何をするか | どこで |
|---|---|---|
| **写す**（この ADR） | 知見・トピック・資料を 1 つのデータセットに。語の候補を束ね、語が知見に出るかを見る | PR 14b |
| 関係を選ぶ | 語の組ごとに、閉じた集合から関係を 1 つ（AI）。未確認の用語と関係として置く | 14c |
| 入口と返事・見せ方・育つ | チャットから 1 操作・地図・確認 | 14d〜14f |

## 2. 決定

### 2.1 「知識の束」は Asterism の約束で、取り込み元はそれを実装する

束（`format: "asterism-knowledge/1"`）は JSON 1 本:

```jsonc
{
  "format": "asterism-knowledge/1",
  "title": "<取り込み元の表示名>",
  "exported_at": "<ISO 8601>",
  "bases": {"claim": "https://…/claim/", "source": "https://…/source/", "term": "https://…/term/"},
  "files": {
    "claims.json": [...], "topics.json": [...], "sources.json": [...],
    "term_candidates.json": [...], "term_claims.json": [...], "term_relations.json": [...]
  }
}
```

| ファイル | 1 行 | 主な列 |
|---|---|---|
| `claims.json` | 知見（根拠の文） | `id`・`iri`・`source_url`・`title`・`review_state`・`epistemic_status`・`modal_qualifier`・`level`・`source_iri[]`・`generated_at`・`exported_at` |
| `topics.json` | トピック | `id`・`iri`・`source_url`・`title`・`topic_type`・`material`・`property`・`claim_iri[]`・`source_iri[]`・`generated_at`・`exported_at` |
| `sources.json` | 資料 | `id`・`iri`・`name`・`kind`・`exported_at` |
| `term_candidates.json` | 語の候補（取り込み元が見たまま） | `id`・`name`・`kind`（`class`／`property`）・`role`（`material`／`method`／`concept`／`other`／null）・`aliases[]`・`origin_id`・`origin_iri`・`source_url` |
| `term_claims.json` | 取り込み元が知っている 語 ↔ 知見 | `term_id`・`claim_id`・`claim_iri` |
| `term_relations.json` | 取り込み元が知っている語どうしの関係 | `from_term`・`rel`（下書きの関係の名前）・`to_term`・`origin_id`・`origin_iri` |

- 行は 1 件 1 オブジェクトで、キーは全行に出す（無い値は `null`・配列は `[]`）。`id` は
  `[A-Za-z0-9._~-]+`。本文は出さない（人の文章の正本は取り込み元）。
- **Graphium 固有のコードは Asterism に置かない**（`source-switch-mcp-client.md` §2 と同じ線引き）。
  道具名は設定、IRI の基底は束、題の割り方は取り込み元の仕事。
- 取り込み元プロトコルに 4 つ目の道具名 `sourceProvider.knowledge` を足す（既定は空）。入力は無し、
  返りは上の束。`/call` の allowlist には入れない — 呼ぶのは写す口だけ。

### 2.2 仕事の分け方（利用者の裁定 2026-11-17 ③）

| 仕事 | 誰が | 理由 |
|---|---|---|
| 題を割る（何の材料の・何の物性か）・種類を役割に写す | 取り込み元 | 題の付け方と種類の札は、その道具の決まり |
| 表記ゆれを束ねる | Asterism（`asterism.knowledge_bundle.merge_terms`） | 論文の語にも同じ部品が効く |
| 語が知見に出るかを見る | Asterism（`asterism.term_match`） | 同上。論文の道（根拠の文に両端の語が現れるか）と同じ規則 |

- 束ねる鍵は `term_match.merge_key`（全角半角・大文字小文字・ハイフン・空白・括弧・英語の単複）。
  **括弧の中の略称（「X (ABC)」の ABC）では束ねない** — 同じ略称が別の物を指すことがある。
  略称は照合の別名にだけ使い、同じ物かどうかは関係「同じもの」で根拠つきに決める（次の段）。
- 種別は、候補に項目（`property`）が 1 つでもあれば項目。種類（`class`）の役割は多い方。
- 照合は**長い名前が勝つ**（「格子熱伝導率」の中の「熱伝導率」は数えない）。日本語は語の境目を
  見ない部分一致なので、これが無いと偽の共起が増える。ラテン文字だけで 3 文字未満・それ以外で
  2 文字未満の名前、文字を 1 つも含まない名前（数字だけ・記号だけ）、200 文字を超える名前は
  照合に使わない。
- 括弧の外と中を別名に使うのは、文字を含む 3 文字以上のものだけ（「Si(100)」の 100、
  「(n型)」の n型 は、どの文にも当たるので使わない）。
- `asterism.term_match` の `term_key`・`mentions`・`prep_text` は、論文の道
  （`asterism_api.chat_paper`）と同じ返りを返す（api のテストが 2 つを固定する）。`chat_paper` を
  こちらの import に寄せるのは後の PR。

### 2.3 設計（Mapping IR）は Asterism が機械で組む

束の形が Asterism の約束なので、それを読む設計も Asterism が持つ
（`knowledge_bundle.design_document`）。束が名乗る 3 つの基底を検査して（http(s)・末尾 `/` か
`#`・プレースホルダのホストでない）主語のテンプレートに差し込むだけで、**AI は呼ばず、人の設計も
挟まない**。出来るのは宣言的な Mapping IR で、生成コードは実行しない。

| map | 主語 | 種類 | 表示名 |
|---|---|---|---|
| `claim` | `<claim>{id}` | `kn:Claim` | 知見 |
| `topic` | `<claim>{id}` | `skos:Concept` | トピック |
| `source` | `<source>{id}` | `kn:Source` | 資料 |
| `term` | `<term>{id}` | `kn:Term` | 語 |
| `term_claim` | `<claim>{claim_id}` | — | `kn:mentions` → 語 |
| `term_relation` | `<term>{id}` | `kn:TermRelation` | 語の関係の候補 |

- `kn:` = `https://kumagallium.github.io/asterism/vocab/knowledge#`（新設。写しの行の種類と述語だけ）。
  標準があるものは標準を使う（`dcterms:identifier`・`rdfs:label`・`skos:prefLabel`／`altLabel`・
  `prov:wasDerivedFrom`・`prov:generatedAtTime`）。
- 束ねた語の id は `t-` ＋鍵のハッシュ。名前が同じなら上げ直しても同じ IRI になる。

### 2.3a 束は信頼できない入力として読む

束を作るのは外のアプリで、中の文字は第三者の文書から来ていることがある。

| 見るもの | 扱い |
|---|---|
| 形（`format`・`files`・基底） | 合わなければ束ごと断る（422） |
| 基底 | http(s)・末尾 `/` か `#`・空白・制御文字・`<>"{}\|\^\`` なし・`%` の後ろは 16 進 2 桁・利用者名なし・プレースホルダのホストでない・3 つとも別の値 |
| id | `[A-Za-z0-9._~-]{1,200}` で、`.` だけでないもの。合わない行・同じ id の 2 行目は落とす |
| 行の `iri` | 信じない。基底 + id で作り直す |
| IRI を取る列（`source_iri`・`claim_iri`・`origin_iri`・`source_url`） | 絶対 IRI の形のものだけ残す。悪い値は落として `dropped["<ファイル>:bad_iri"]` に数える（そのまま置くと、ストアがその版を丸ごと受け付けない） |
| 文字の値 | 制御文字を空白に・対にならないサロゲートを除く |
| 知見と同じ id のトピック | トピックを落とす（同じ基底なので同じ主語になる） |
| 束の大きさ | 64 MiB まで |

悪い行・悪い値は落として `dropped` に数え、束は通す — 1 行の細工で写しが丸ごと作れなくなるのを防ぐ。
設計の YAML に入るのは検査を通った基底と、JSON の文字列として入れたデータセット名だけ
（`source:` の向き先・述語・関数は固定）。

### 2.4 写しは取り込み元ごとに 1 つ・毎回の全量で作り直す

- データセットの `meta.knowledge_source = {name, format, title, exported_at, copied_at, counts}`。
  `name`（取り込み元の名前＝MCP のサーバ名）が同じ写しは 1 つ。2 回目からは同じデータセットを
  上げ直す（設計を置き直す → source を全置換して取り込む → 公開）。
- 知見の家は取り込み元。Asterism の行は写しで、直さない（`docs/examples/graphium/README.md`
  「守っていること」と同じ）。
- 知見が 0 件の束は断る（公開済みの写しを空で置き換えない）。
- 同じ取り込み元の写しを同時に 2 つ作らない（2 つ目は 409）。待ち手が消えても公開まで走る。
- **途中で落ちたとき**: 作り直しは「設計を置き直す → source を全置換して取り込む → 公開」の順で、
  既存の取り込みの口は source を先に置き換える。取り込みや公開が落ちると、公開中の版は前のまま
  （壊れない）だが、`source/` は新しい束になっている。この間 `knowledge_source.syncing` が立ち、
  `version`（＝写しが出来た版）と `copied_at`・`counts` は前の成功のまま残る。`source/` を読む側は
  `knowledge_copy.copy_is_current(meta)`（`syncing` でなく、`version` が公開中の版と同じ）が偽の
  あいだは読まない。次に写しが通れば直る。新しく作る場合も、名前の印（`name`）は取り込みの前に
  付ける — 途中で落ちても、次の回が同じデータセットを見つけて作り直せる。
- 束の検査・語の束ねと照合は、スレッドに出して行う（知見 2,000 × 語 900 で十数秒。その間ほかの
  要求を止めない）。

### 2.5 口は 2 つ

| 口 | 使いどころ |
|---|---|
| `POST /api/knowledge-copies` `{source, bundle, lang?}` | 束を本文で受ける。スクリプト・試験・MCP を使わない取り込み元 |
| `POST /api/mcp-servers/{name}/knowledge-copy` | 登録した MCP サーバから束を取って写す。単一ユーザーモード限定・有効なサーバだけ・道具は設定の 1 つ・引数は渡さない |

どちらも書き込みの認証。束は機械どうしで運ぶ — 外の AI に数千行を引数で運ばせない
（`resync_rows` は小さな上げ直しのための口のまま）。

### 2.6 語は用語にしない（この段では）

束ねた語は写しの行（種類「語」）で、共有語（`sv:`）にはしない。用語として知識グラフに置くのは
次の段で、根拠のある関係が 1 本以上ある語だけ（問い、または根拠のある関係でつながらない用語は
作らない）。写しを外したとき・作り直したときに、確認済みの用語を巻き込まないためでもある。

## 3. 退けた案

| 案 | 理由 |
|---|---|
| 今の送り口（型つきの知見）を全部に広げるだけ | 行は入るが語が無く、知識グラフは点のまま |
| 外の AI が束を運ぶ（`resync_rows` に全ファイル） | 約 1 MB を AI の引数で運ぶことになる。機械どうしで足りる |
| 設計を人が承認する | 入れた直後に出す、が成り立たない。束の形は固定なので、設計も固定で検査できる |
| 語を機械でそのまま用語にする | 関係の無い用語が数百できる。確認の単位も決まらない |
| 題を割る規則を Asterism に置く | 取り込み元ごとの決まりが Asterism に溜まる |
| 略称で語を束ねる | 同じ略称が別の物を指す例があった（試しの抜き取り） |

## 4. 残した穴

- 外の AI（MCP）も、写しに入った未確認の知見を読める（各行の `kn:reviewState` で見分けられる）。
  用語と関係は確認済みだけを出す決まりだが、写しの行には当てていない。絞るなら、取り込み元が
  持ち主のデータセットを MCP の一覧と問いから外す切り替えを入口の段で。
- 入口（設定のプリセットに `knowledge` を足す・チャットから選ぶ）と、つながりを外したときの
  後始末は次の段。
- 取り込み元の値が、表の読み手の「値なし」の語（`NA`・`null`・`nan` など）とちょうど同じ文字列だと、
  その項目は写らない（JSON を表にして読む既存の取り込みの性質）。知見の題がその語だけ、という
  ことはまず無いが、札の値には使わないこと。
- 取り込み元の語どうしの関係（`term_relations.json`）は、関係の名前が閉じた集合にあることだけを
  見る。両端の種別と役割の検査は、関係として置く段（次の段）が行う。
- デスクトップ版が stdio で相手を起こすとき、相手の保存フォルダを読む許可（macOS の「書類」
  フォルダ）は Asterism のアプリに付く。許可が無いと相手の道具が読めずに失敗する。
- `chat_paper` の照合を `asterism.term_match` に寄せる。
