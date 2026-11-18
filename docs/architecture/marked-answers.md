# 印つきの答え（台帳の未確認の関係もチャットの答えに使う）

状態: 決定（2026-11-18）。実装 = PR 14e-2。設計の経緯は AI for Science 側
`ui-redesign-2026-10-19/design.md` §23.3・§23.7・§23.11、契約は `contracts/pr14e2-contract.md`。

関連: [`pair-relations.md`](pair-relations.md)（台帳・確認）/
[`relations-for-experiments.md`](relations-for-experiments.md)（知識で答える型）/
[`knowledge-copy.md`](knowledge-copy.md)（写し）。

## 背景

取り込んだ直後の関係は、AI・人・取り込み元の未確認の行で、確認済みでなければストアに無い。
今の「知識で答える」走者（`asterism.knowledge`）はストアの確定した線だけを読むので、取り込み直後の
チャットは答えられない。確認を待たせずに答えたいが、未確認を確認済みのように見せてはいけない。
**答えは出す。ただし未確認を使ったら印を付け、根拠を見せ、その場で確認できる**ようにする。

## 決定

### 1. 台帳の走者（`asterism.ledger_answer`・新規）

| 部品 | 中身 |
|---|---|
| `ledger_questions(ledger, copy)` | `pair_relations.questions` と同じ数え方の全件に、機械が名前 `lq_<op>_<語 id の英数字>[_<direction>]` を付ける |
| `answer(ledger, copy, question)` | `knowledge_answer` と同じ形（`found`・`count`・`items`）。項目に `confirmed`・`tentative`・`context`・`evidence`・`evidence_total`・`uses` |
| `summarize_use(answers)` | 使った関係のうち未確認の鍵（重複なし）。「N 件」＝確認できる鍵の数 |
| `reply_block(answers)` | `reply.answer.ledger` の形（項目は最大 50） |
| `find_ledgers(root)`・`load_source` | registry から台帳と写しを**読むだけ**（取り下げ・削除済みの写しと、壊れた台帳は飛ばす・退避もしない） |

- 読むのは台帳の「見せる関係」（AI・人・取り込み元の未確認を含む・「違う」は含まない）と写しだけ。
  **SPARQL も AI も使わず、ストアを読まない**（確認済みをストアに置く 14e-1b に依らない）。
- 数え方は `questions` と同じ規則を同じ順に踏む（`factors` は X への線と X の一部への線の 1 段・
  `missing` の定義・向きの無い関係はどちらの端にも・種別が型に合うか）。**全型・全語で
  `questions` の件数＝`answer` の項目数**を、種つきの乱数の台帳で固定する。
- `uses` の `key` は確認の口がそのまま受ける鍵（組の鍵／取り込み元の行は `o:<from>|<rel>|<to>`）。
  `factors` の経由（線＋一部）や `missing`（持つ側の全部）のように複数の関係が絡む項目は、
  絡む関係を全部入れる。項目が確認済みなのは絡む関係が全部確認済みのとき。
- 名前に語 id の記号が含まれるときは、id の指紋（sha1 の 6 桁）を付けて別の id と重ねない。

### 2. 2 つの入口

| 入口 | 誰が問いを決める | AI |
|---|---|---|
| 1 押した問い | 機械（取り込みの結果の `questions[]`） | **呼ばない**（api が走者を直に呼ぶ・AI 未設定でも答える） |
| 2 書いた問い | 答える AI が道具 `answer_ledger_question`（引数は名前だけ）で名前を選ぶ | 道具の選択だけ。値・IRI・型・語は書かせない |

- 入口 1: `POST /api/chat/sessions/{id}/turns` の本文に `ask_ledger: {source, name, text?}`
  （「ちょうど 1 つ」の検査に入る）。手番は `by: machine`・`route: ask`。返事の `text` は機械の 1 文
  （「「問い」として、次の N 件が記録されています。」）。台帳・写しが読めない／名前が今の台帳に無い
  ときは断り文（エラーにしない・`answer` は無し・`notes.ask_ledger_declined`）。取り込み中でも読めれば答える。
- 入口 2: 道具の説明に並べる問いは機械が絞る（利用者の文に語の名前・別名が出る問いを先に、残りは
  件数の多い順、最大 40）。取り込み元が複数なら名前に `s1`・`s2` の印を機械が付けて一意にする。
  AI に返す結果は文と印だけ（鍵・根拠は渡さない）。`finalize` が使った答えを**機械が集めて**
  `answer.ledger` に入れる（`verified_used` と同じ作法・AI の文には頼らない）。道具を呼ばなかったら
  欄ごと付けない（今までどおり・印も付かない）。
- 台帳の答えは `verified_tools` に入れない（決定論の道具の緑の印を付けないため）。

### 3. MCP は確認済みだけ

`mcp/` は台帳を読まず、この走者を import しない（`mcp/` の下に `ledger_answer`・
`answer_ledger_question`・`lq_` が現れないことをテストで固定）。外の AI が未確認の関係を
確認済みのように受け取らないため。`answer_knowledge_question` の返りは変えない。

### 4. 取り込みの結果の「答えられる問い」

結果の 1 枚の `questions[]` を `ledger_questions` に替え、`name`・`source` を足した（数と並びは
`pair_relations.questions` と同じ）。押すと入口 1 で送る。

## 守ること

- 未確認を使った答えは、必ず印（`unconfirmed_count`）と根拠（`evidence`）を持つ。
- 確認は答えに使った鍵だけ（全部まとめては置かない）。確認の口は 14e-1（`/relations/review`）。
- 台帳は変えない（確認は別の口）。この走者は読むだけ。

## 残した穴

- 取り込み元が複数のとき、入口 1 の名前は取り込み元ごと（`source` と `name` の組）。入口 2 の
  `s1`・`s2` の印はその 1 回の問いの中だけの名前（並びが変わっても答えには `source` が付く）。
- 項目が 50 件を超える問いは、答えの `ledger.items` を先頭 50 件で切る（`items_total` は全数・
  確認の対象も載せた分だけ）。
- 台帳の語の問いは日本語か英語の文で作る（demo-agent は問いの文字で ja／en を決める）。
