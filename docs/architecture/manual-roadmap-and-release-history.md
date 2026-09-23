# ADR: マニュアルの「更新を知る」— 機能ロードマップ・リリース履歴・リリース時の確認

- 状態: 採用（2026-09-24）
- 関連: `design-consult-chat.md`（D8: manual/ が単一の真実源）、`workflow-pr-ci-gating.md`、
  Graphium の `manual/roadmap.md` / `scripts/check-manual.mjs` / `tagpr.yml`（移植元）

## 文脈

リリースは tagpr が `CHANGELOG.md` に PR 題名を積む形で残るが、利用者が「いまの版で
何ができるようになったか」を知る場所が無かった。`docs/ROADMAP.md` は開発者向けの
実行状態（580KB）で、利用者には読めない。Graphium は同じ問題を、マニュアル内の
**機能ロードマップ**（人が選んだ節目）＋**リリース履歴**（CHANGELOG の取り込み）＋
各章の **「vX.Y.Z で追加」バッジ**＋**リリース PR への nudge コメント**＋
**CI の drift check** で解いている。asterism にも同じ形を入れる。

## 決定

| 要素 | 置き場所 | 誰が書くか |
|---|---|---|
| 機能ロードマップ | `manual/ja/roadmap.md` | **人**。CHANGELOG から自動生成しない（節目を選ぶ編集判断がページの価値） |
| リリース履歴 | `manual/ja/release-history.md` | 前書きだけ人。本文は `<!--@include: ../../CHANGELOG.md{3,}-->` でビルド時に取り込む |
| 追加バッジ | 各章の見出し `<Badge type="tip" text="vX.Y.Z (YYYY-MM-DD) で追加" />` | 人（節目を足すときに一緒に） |
| 検査 | `scripts/check_manual.py` ← `api/tests/test_manual_drift.py` | 機械（CI） |
| 催促 | `.github/workflows/tagpr.yml` がリリース PR に前回タグ以降の PR 一覧をコメント | 機械（リリース時） |

### D1. ロードマップは人が書き、機械は材料と検査だけを出す

CHANGELOG の PR 題名を機械で並べ直しても「できることが変わった節目」は選べない。
tagpr の job が前回タグ以降の PR をリリース PR にコメントし（1 本を書き換え）、人が
そこから節目を拾う。Graphium と同じ。

### D2. 「置き去り」はマイナー版の距離ではなく、リリースした「日」の数で見る

Graphium は「最新の節目から 2 マイナー以上遅れたら落とす」。asterism は feature PR
ごとに minor を上げ、1 日に 10 マイナー出る日がある（2026-09-02）ので、その規則だと
忙しい日の途中で CI が赤くなる。**最新の節目より新しいリリースの日付が 3 日分を
超えたら落とす**（`MAX_RELEASE_DAYS_LAG = 3`）。節目を載せる判断はリリース日ごとに
一度で足りる、という cadence に合わせた。

### D3. バッジ・ロードマップの版と日付は CHANGELOG と一致していなければならない

写し間違いは目視で見つからない。`check_manual.py` が版の実在と日付の一致を検査する。
アンカー（`./x.md#見出し`）の実在は、生成器 `build_manual_site.heading_slug` を import
して同じ計算で確かめる（規則を二重に持たない）。

### D4. release-history は「タグを切った直後に main で」再生成する

`docs/manual/` はコミットされた生成物で、`test_manual_site_fresh.py` が同期を検査する。
CHANGELOG を取り込む release-history は、tagpr が CHANGELOG を書き換えるたびに古くなる。
選択肢は 3 つあった。

| 案 | 内容 | 不採用の理由 |
|---|---|---|
| リリース PR ブランチ上で再生成 | tagpr がリリース PR を更新するたびに `docs/manual/` を作り直して同じブランチへ commit | tagpr（v1.20.2 `tagpr.go`）は rc ブランチ上の自分以外のコミットを**次回 cherry-pick で再適用する**。再生成コミットが積み上がり、その間に別の docs PR が main で `release-history.html` を触ると cherry-pick が衝突してリリースが止まる |
| tagpr の `command` / `postVersionCommand` | 版の決定後にビルドを回す | どちらも CHANGELOG が書かれる**前**に走る（`tagpr.go` の順序: command → versionFile → postVersionCommand → … → CHANGELOG）。最新版の節が載らない |
| **タグを切った直後に main へ commit（採用）** | tagpr job の `steps.tagpr.outputs.tag != ''` の run で `build_manual_site.py` を回し、差分を `[chore] manual: …` として main へ push | 更新フィード `docs/updater/latest.json` が `desktop-release.yml` で既にこの形（GITHUB_TOKEN・`git pull --rebase` で押し合いを吸収） |

ただしリリース PR のマージ push で走る CI（`ci.yml` の `push: main`）は、この commit より**前**に
`test_manual_site_fresh.py` を回す。そこで生成器の `--check` は、CHANGELOG から派生する
`release-history.html` の**内容のズレだけは見ない**（ファイルが無いのは見る）。人が
`build_manual_site.py` を回せば毎回作り直されるので、古いままなのはリリース直後の数分か、
main への push が 3 回とも失敗したときだけ。

### D5. workflow の変更は別 PR

`.github/workflows/` を触る PR には Actions run がスケジュールされない（ADR
`workflow-pr-ci-gating`）。ページ・生成器・検査（CI で検証できる側）と tagpr.yml
（検証できない側）を分ける。

## 結果

- 利用者は `…/manual/ja/roadmap.html` で節目を、`…/release-history.html` で全変更を読める。
- 章の見出しのバッジで「自分の版で使えるか」が分かる。
- リリースのたびに PR コメントで催促され、放置すれば CI が落ちる。
- リリース履歴は、タグを切った直後に機械が main で作り直す（人の PR には同期を要求しない）。
- 費用: 節目があるリリース日に、ロードマップ 1 行＋バッジ数個の docs PR が 1 本増える
  （Graphium と同じ運用）。
