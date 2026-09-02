# ADR: つながりの参加者は (データセット, 種類, 述語) — 項目は種類が付いて初めて項目になる

Status: Accepted (2026-09-03) · Supersedes nothing（`crosswalk-hub.md` の参加規則
`Rule(dataset, predicate)` に、任意の **種類（subject class）** を足す追補）。

## Context

値のカタログ（Doi / Composition / PropX / UnitY …）は、どれも自分の値を **同じ述語
`rdfs:label`** で持つ（受け口のラベル保証・v0.31.10）。つながり（crosswalk）の参加者は
これまで `(dataset, predicate)` だったので、この述語を選ぶと DOI・組成・単位の値が
**1 つの項目として**集められ、画面にも設計の言葉が付けられず（#554/#556 の規則で
「食い違う述語には黙る」）、素の `label` が候補に並んだ（利用者報告 2026-09-03
「データにそんなのない」）。発見（discover）も述語単位で値を読むため、同じ混ざり方を
していた。

## Decision

- **参加者に任意の `subject_class`（種類の IRI）を持たせる。** 意味は「この種類の
  実体が、この述語で値を持つ」。無ければ従来どおり「どの実体でも」（旧 config は
  1 バイトも変わらず読める・書ける）。
- **構築**は `?e a <種類> . ?e <述語> ?v` で値を集める（単一値・複合キーとも）。
- **発見**は述語を **(種類, 述語)** 単位でプロファイルし、スロットも候補の
  `build_config` も種類を運ぶ。汎用の名前述語（`rdfs:label` / `skos:prefLabel` /
  `schema:name` / `dcterms:title` / `foaf:name`）は概念名を **種類の名前**から起こす
  （`rdfs:label` on Composition → `composition`。「label」という概念は作らない）。
- **言葉**は種類ごとに引く: 設計（Mapping IR）の map ＝ 種類なので、その map の
  authored `label`、無ければ列見出しが「この種類のこの項目」の言葉になる
  （`_ir_field_labels`）。述語だけの解決（`_crosswalk_predicate_labels`）は従来どおり
  食い違えば黙る — 種類が分かる場面ではそもそも黙る必要がない。
- **画面**は項目を「種類 › 項目名」で呼ぶ（`Composition › 試料化学組成`）。手動の
  つながり作成は `GET /api/crosswalk/fields/{dataset_id}` で、AI 無しでも種類ごとの
  項目（実例つき）を出す。AI 提案は候補行に種類を添えて見せ、返答の種類を検証して
  受け取る（無ければその述語が最初に載った種類）。

## Consequences

- 混ざりが消える: 組成でつないだつながりに DOI や単位の偶然一致が入らない。
- 発見の走査は (種類, 述語) の組が単位になる。同じ述語を N 種類が共有すれば N 組。
  上限（`max_predicates_per_dataset`）はそのまま組の数に効く。
- 型の無い実体（`rdf:type` 無し）は種類なしのスロットとして従来どおり扱う。
- 既存のつながり config は `subject_class` 無しのまま有効。種類で絞りたいものは
  作り直す（発見の候補は最初から種類つき）。
