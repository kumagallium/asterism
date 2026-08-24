# 骨格ゲートの帰結プレビュー — エンティティカード・潰れ分類・参照リスク

Status: accepted (2026-08-13)

Related: [`kantan-mode-two-tier-ux.md`](kantan-mode-two-tier-ux.md)（K7/K14）,
[`mapping-ir-phase2b-skeleton-wizard.md`](mapping-ir-phase2b-skeleton-wizard.md),
[`instance-iri-base.md`](instance-iri-base.md)

## 0. 問題 — 証拠は機械、判断は人間丸投げ

実話（2026-08-13、XRD リファレンスカードの dogfood）。1 ファイル = 1 枚の参照カード
（No: 03-065-2664）+ 47 ピークという典型的な「メタデータ部 + データ部」構造で、
**開発者本人が骨格ゲートの判定に迷った**:

- `sample/{No}` の「⚠ 47 行のうち 46 行が潰れます」は**正解の挙動**（メタデータ部が
  1 エンティティに合流する）なのに、事故の見た目で提示された。
- `peak/{2theta}` の正解 `peak/{No}/{(hkl)}` は、候補チップに**存在すらしなかった**
  （{(hkl)} / {(hkl)}+{2theta} / {(hkl)}+{d} が並列提示され、どれも追記に不安全）。

このとき必要だった 3 つの判断 — ①全行→1 件の潰れは正常か事故か、②測定値キーの
将来リスク、③親スコープ列の前置 — は、**すべて既に機械が持っている情報から決定論で
導出可能**だった。ゲートは実データの証拠（PR #280）を見せていたが、その証拠は
「ID の URL 構文」の語彙で書かれていた。人間が持っている知識は世界の側
（「Al3V は 1 材料、ピークは 47 本」）であって ID 構文ではない。検査トラップの
教訓（`inspection-trap-fix-recipes.md`）の人間版: **機械的要件は、レシピまで機械が
用意しないと着地しない。**

## 1. 決定事項

| # | 論点 | 決定 | 理由 |
|---|------|------|------|
| C1 | 潰れの分類 | `collapse_kind` = `unique` / `singleton` / `partial` をサーバが判定。**singleton（全行→1 件）は緑の正常系**「N 行すべてが同じ 1 件に合流します」で提示し、⚠ と続行時 confirm は **partial のみ** | 全行が 1 つに潰れる = ファイルスコープのメタデータエンティティ（参照カード・実験ヘッダ）で、合流こそが目的。正解を事故に見せたのが混乱の直接原因 |
| C2 | エンティティカード | 「実際の ID の例」（URL 構文）を**カード（帰結）**で置換: 代表 1 エンティティの実プロパティ値を表形式で描く。partial は**最大衝突グループ**を選び、食い違う値を実値 + 行番号で表示（上書き合戦の可視化）。singleton では行ごとに変わる列を conflict ではなく「別の種類の領分」として名前列挙 | URL が 3 本同じでは事故にしか見えない。カード 1 枚なら「情報が集まる」が直感で読める。「この設計だと何が何件できるか」の件数見出し付き |
| C3 | 参照リスク | `reference_risks[]`（機械可読 kind、文言はフロント i18n）: `measurement-id` = 値が修正されると別 ID が生まれ引用が宙に浮く / `scope-missing` = 同一ソースの singleton マップのキー列（= ファイルの名前空間）を含まないため、追記時に別親の行が同じ ID に混入 | 「ID の作り方」は答えられなくても「この参照は 3 年後も生きていてほしいか」なら答えられる。K7 の抽象文言を引用の帰結の言葉に置換 |
| C4 | scoped 候補 | scope-missing 検出時、候補チップを**親キー前置形に書き換え**（現キー + 実証済み候補すべて）。ランキングは（全列測定値か, 測定値列数, 列数）昇順 — `{No}/{(hkl)}` が `{No}/{2theta}` に勝つ。`scoped: true` を付けチップを強調 | 一意キーの上位集合は一意（親列が空の行は考慮から落ちるだけ）なので再検査不要。正解がワンクリック第 1 候補になる |
| C5 | singleton の候補チップ | 折り畳み `details`「本当は 1 件ではなく、行ごとに分かれるべきですか？」の中に退避 | singleton が事故（本当は行ごと）の場合の救済は残しつつ、正常系を騒がせない。C1 と対 |
| C6 | 親の一意決定 | scope 注入は同一ソースに singleton マップが**ちょうど 1 つ**のときのみ。0 または 2 以上は沈黙 | 決定論の証拠は推測しない（骨格 evidence の既存原則） |

## 2. 実装

- **step0** `skeleton_annotate.py`: `_collapse_kind()` / `_entity_preview()`（キー groupby、
  同値列 = プロパティ、割れ列 = partial なら conflict・singleton なら varying、キー列優先
  8 列上限・conflict は上限に食われない）/ `_scoped_candidates()` / `_inject_scope_risks()`
  （全マップ注釈後の 2 パス目）。既存フィールドは不変更（後方互換）。
- **UI** `SkeletonGate.tsx`: collapse_kind 分岐（旧サーバは `is_unique` にフォールバック）、
  カード描画、リスク文言、`confirmCollides` を partial 限定に。`workbench.json`（ja/en）
  `skeleton.evidence.*` に追加キー。`App.css` `.skeleton-entity-card*` /
  `.skeleton-candidate-chip--scoped`。
- 互換性: 旧フロント × 新サーバ = 追加フィールド無視で従来表示。新フロント × 旧サーバ =
  フォールバックで従来表示。

## 3. 実データ検証（xrd_参考文献.txt, PDF card 03-065-2664, 47 peaks）

`sample/{No}` + `peak/{2theta}` の骨格（ユーザーが実際に迷った状態）に対して:

- sample → `singleton`。カード = No / CSD / Name / Chemical Formula / Space Group / Cell
  など定数 8 列 + 「ほか 10 列」+ varying 4 列（2theta, d, I, (hkl)）。
- peak → `unique` + リスク 2 件（measurement-id: 2theta / scope-missing: 親 Material, 列 No）
  + scoped 候補の先頭 = **`{No} + {(hkl)}`**。

**ユーザーが迷った 3 判断すべてが、機械の第 1 出力として提示される**ことを確認。
チップ 1 クリックで `peak/{No}/{(hkl)}` に置換されることをブラウザで実証。

## 4. 非目標（今回やらない）

- ID 欄を隠して件数承認だけにする UI（「Material 1 件・Peak 47 件、合っていますか？」）—
  カード + 件数見出しまでで一旦様子見。完全形は将来の検討。
- unique マップのカードから親マップ帰属列（No, Name…）を除く整理 — 列の帰属裁定は
  per-map 段の関数従属 advisory（PR #311）の領分。骨格段階では「行が実際に持つ値」を
  正直に見せる。
- JSON / XML ソースのカード — 骨格 evidence 全体が tabular 限定（既存の `checkable: false`
  規約に従う）。

## 5. 改訂（2026-08-24）— 危険な ID は人に見せる前に置き換える

C3 の `measurement-id` は「この ID は測定値の列だけでできています」という警告だが、
**同じ注釈パスが同時に安全な代替候補（`key_candidates` の `measurement_only: false`）
も証明している**。警告と解決策を両方持っているのに、警告だけを人間に見せて「候補から
選び直してください」と言うのは、機械が答えを知っている問題を人間に丸投げしている。

### 決定事項

| # | 論点 | 決定 | 理由 |
|---|------|------|------|
| C7 | 自動置換の範囲 | `key_measurement_caution: true` かつ `key_candidates` に非 measurement_only な候補が 1 つ以上あるときだけ、**先頭の（最上位ランクの）非 measurement_only 候補**で subject template の key を機械的に置換する。安全な候補が無ければ何もしない（警告のみ従来通り） | key_candidates は既に「全列測定値か・列数・実証済み一意性」で決定論ランキング済み。人間が選んでも機械が選んでも同じ第 1 候補になる場面で、選ばせるだけ無駄 |
| C8 | scope-missing は対象外 | `key_measurement_caution` が false（`scope-missing` だけが出ている状態を含む）では**絶対に置換しない** | measurement-id は「この ID は将来ほぼ確実に安全でない」という事実の指摘で、代替候補も同じ理由（測定値でない）で優劣が付く。一方 scope-missing は「今のファイル 1 つでは分からないが、データセットがどう育つ予定か」という**人間の運用知識**に依存する判断（複数ファイルを跨いで永続 ID にするのか、単一ファイルの使い捨て取り込みなのか）。機械が親キーを勝手に前置すると、育てるつもりのないデータセットの ID まで不必要に長くしてしまう。C7 と違い「機械が選んでも人間が選んでも同じ答え」という前提が成り立たない |
| C9 | 適用タイミング | 機械の骨格提案直後（`/api/propose/skeleton`）にのみ適用。**人間編集の再検査（`/api/propose/skeleton/validate`）には絶対に適用しない** | 人間が明示的に選んだキーを機械が黙って書き換えたら、K1-K20 が積み上げてきた「機械は証拠を出す・人間が決める」の一線を越える。置換は「AI の一次提案を、AI 自身の証拠でさらに機械的に磨く」工程であって、人間の裁定に介入する工程ではない |
| C10 | 可視性 | 置換したら `applied_key_fix: {from, to, reason, template_from, template_to}` を注釈に付け、ゲート画面に "AI は〈from〉を選びましたが…機械が〈to〉に置き換えました" の 1 行を常設表示する | 「黙ってやらない」（プロジェクトの UI 方針）。置換後は多くの場合 key_candidates 自体が空になる（C11 参照）ので、この 1 行が置換が起きた事実を伝える唯一の手段になる |
| C11 | 元に戻す動線 | `applied_key_fix` の直下に専用の「元に戻す」ボタンを常設する。ハンドラは候補チップと**同じ** `onApplyCandidate(applied_key_fix.from)` を呼ぶだけ（`applyCandidate` が head を保ったままテンプレートを組み直し、その後の `/skeleton/validate` 再検査で警告が正しく戻る） | 実装時に判明: 置換後の新キーが caution なしで一意になる場合、`_annotate_map` の既存ルール（`key_candidates: [] if (report.is_unique and not caution) else …`）により候補チップ自体が空になり、**「元の選択も候補から選び直せます」という案内は成立しない**（C10 初版の文言はこの点で不正確だった）。候補チップに頼らず、`applied_key_fix` が持つ `from` から直接戻せる専用ボタンを置くことで、機械が黙って書き換えず・人間の選択が機械に勝つ、という設計を保つ |
| C12 | 所属を肯定形で常設表示 | 各マップの評価行に、そのマップが**どの親の中で数えられるか**を毎回言い切る 1 文（「{{parents}} の中で数えます」）を常設する。判定は見取り図の辺とまったく同じ包含規則（自マップのテンプレート変数が、別マップのテンプレート変数を真に包含するか）を共有の 1 関数で評価し、**辺が出る場面と文が出ない場面、その逆が絶対に起きないようにする**。親が無い（単独マップ、または誰の鍵も包含しない）場合は**何も表示しない**（scope-missing 警告が「無いときに困る」側を既に担っている）。`applied_key_fix` が新たに親を成立させた場合（差し替え前の from では成立せず to で成立した親がある場合）だけ、押印文の直後に「これで {{child}} は {{parents}} の中で数えられます。」を追加する | ユーザーの問い（2026-08-24「かんたんモードで、`③データの数えかた` の段階で親の決定や構造が出なくても良いのか」）が起点。所属は独立した質問ではなく **ID 設計の帰結**（どの列をキーに選ぶかが自動的にどの親の中で数えるかを決める）だが、帰結だからこそ**warning や図だけでは「構造が正しいときに何も見えない」**——scope-missing は壊れているときにしか光らず、見取り図は折りたたみの中でしか見えない。肯定形の常設文にすることで、safe-key 差し替え（C7-C11）が生んだ親子構造がその場で言語化される |
| C13 | 検査できない骨格は「未確認」を警告として明示し、回復導線をその場に置く | (1) `source-not-found`（このマップ自身のソースが見当たらない）と `notChecked`（旧サーバ由来の注釈欠落フォールバック——意味は同じ「検査不能」）を、他の risk 行（`riskMeasurementId` 等）と同じ警告トーン（⚠・`skeleton-evidence-caution`）に格上げし、「この ID の重複・安全性は未確認です」を明記する。(2) 検査不能（`canRevalidate` が偽、またはそのマップの `reason` が `source-not-found`）かつ複数マップかつ C12 の所属が成立しないときに限り、「どの種類の ID にも含まれていません（紐づけの確認はファイルを置き直してから）」を keySentence 直下に出す（骨格だけで計算できる C12 は縮退時も出る一方、確認が要る側の答えが空白になるのを防ぐ）。(3) `SkeletonGate` に新 prop `onReattach?: () => void` を追加し、渡されたときだけ sourceNotFound 警告の直後・ゲート下部の filesGone 行の 2 か所に「ファイルを置き直す」ボタンを出す（既存の onDiscard＝最初からやり直す、は残す——別の操作）。(4) **見つからない、は探している名前と置かれた名前を言う**：警告に `sourceName`（そのマップの `m.source` の basename）を差し込み「設計が読むファイル「{{name}}」が見つかりません」まで言い切る（`notChecked` も名前が取れれば同様に named 版へ）。手持ちファイル名（`presentFileNames`）を渡し、source-not-found かつ名前が一致しないときは「置かれたファイル「{{present}}」は…と名前が一致しません」を追加する。(5) **名前だけ違う同一データはワンタップで充て直せる**：手持ちがちょうど 1 つで名前不一致のときだけ、その File を期待名で包み直して既存の `onGateFilesDropped` に渡す「このファイルを「{{name}}」として読み直す」ボタンを出す——中身の裏取りは既存の列検査（missing-columns）に任せ、ここでは名前しか信用しない。(6) **(4)(5) は複数ソース設計の道具**：名指し警告とワンタップは、複数ソースの設計で「どのファイルがどこへ行くか」を名前しか教えてくれない状況のためのもの。**単一ソース設計に単一ファイルを置くときは、名前照合は何の情報も持たない**（行き先は 1 つしかない）——ブラウザのダウンロード連番（`xrd_card (1).csv`）のような無意味な不一致でユーザーを二度目の壁に当てた（2026-08-24 再発）ため、単一ソース×単一ファイルは `onGateFilesDropped` が**確認なしで自動的に**期待名へ包み直してから通常の検査に流す。正しさの担保は名前ではなく中身（missing-columns）——ワンタップボタンは自動化により実質不要になり出なくなるが、複数ファイルを 1 つずつ置くようなケースのため `onAdoptRename` 自体は残す | ユーザーの 2026-08-24 の指摘（縮退画面を 2 回踏んだ・別名ファイルを置いて source-not-found が出続けた・その後ワンタップの存在を知らずダウンロード連番ファイルで再度詰まった）が起点：カード・警告・safe-key 自動差し替え（C7-C11）は**すべて検査の産物**なので、検査が走れないと全部消え、危険な ID（測定値単独キー等）が無警告で座り続ける。かつ「ファイルを置き直すと検査できます」と言いながら、その場に置き直す手段が無かった（既存導線は画面最下部の破棄のみ）うえ、判定は正しくても**探している名前も置かれた名前も言わない**ため、置いた本人には意味不明だった。検査不能時に何も言わないことは「問題なし」と誤読される——沈黙は答えではなく欠落であることを、警告と導線の両方で言い切り、さらに「何が違うのか」まで名指しする。ただし**名前照合そのものが情報を持たない場面（単一ソース×単一ファイル）まで人間に確認させるのは過剰**——そこは列検査という既存の裏取りに委ねて自動化する |

### 実装

- **step0** `skeleton_annotate.py`: 純関数 `apply_key_safety_fix(skeleton, annotations)` を
  追加。テンプレート書き換え規則は `SkeletonGate.tsx` の `applyCandidate`（一意候補チップの
  ワンクリック適用）と**同一**（`{` より前を head、無ければ `template + "/"`）——人間の
  ワンクリックと機械の自動適用が同じ変換であることを保証。**空キーは明示的に拒否**（今の
  候補生成器は空キーを一意と証明しないので理論上到達しないが、万一置換されると全行が
  1 つの定数 ID に潰れる＝この関数が起こしうる最悪の帰結なので、"起きないはず" に頼らず
  ガードする）。
- **api** `main.py` の skeleton ジョブ: (1) 通常どおり注釈 → (2) `apply_key_safety_fix` →
  (3) 置換が発生したときだけ**置換後の骨格で再注釈**（一意性・ID プレビュー・候補は新キー
  基準でないと証拠として意味を持たない）→ (4) 再注釈後の該当マップ注釈に
  `applied_key_fix` を再付与（再注釈で失われるため）。best-effort（例外は握りつぶし元の
  骨格のまま進む）は既存の注釈パスの方針を継承。**except 節では骨格も `result.skeleton`
  （AI の元の骨格）へ戻す**——(3) の再注釈だけが失敗すると `skeleton` 変数は置換後のまま
  `annotations` だけ捨てられ、**証拠も説明も無い置換済み骨格が人間に届く**事故になり得た
  ため（レビューで発見・修正）。`applied_key_fix` を出せないなら置換も出荷しない、という
  不変条件をテストで固定。`/api/propose/skeleton/validate` はこの処理を一切呼ばない（C9）。
- **UI** `SkeletonGate.tsx`：`applied_key_fix` があれば `skeleton-evidence-muted` トーンで
  1 行表示（既存の `vocabFix` 表示と同じ「機械が直したことを控えめに明示する」パターン）＋
  その直下に控えめな二次ボタン（`btn btn--ghost btn--sm skeleton-evidence-revert`・
  `canRevalidate` で他の編集系ボタンと同様に disabled 制御・クリックで
  `onApplyCandidate(applied_key_fix.from)`）。
- **UI（C12）** `skeletonDiagram.ts`：`embedsKey`（包含規則そのもの）と `templateVars` を
  非 export のモジュール内共有ヘルパーに括り出し、`skeletonMermaid`（図の辺）・
  `containmentParents(skeleton, mapName)`（あるマップが現テンプレートで数えられる親の一覧）・
  `containmentParentsForColumns(skeleton, columns, excludeMapName?)`（**仮の**列リストに対して
  同じ判定を行う——`applied_key_fix` の `from`/`to` はどちらもスケルトン中のどの map の現テンプレ
  レートとも一致しないことがあるため、マップ経由でなく列リスト直接評価が要る）の 3 関数が
  同じ規則を共有。`SkeletonGate.tsx` は各行で `containmentParents` を呼んで常設文を組み、
  `applied_key_fix` がある行では `containmentParentsForColumns` を `from`/`to` それぞれに
  適用して差分（`to` が新たに獲得した親）を `SkeletonEvidence` へ `containedInAfterFix`（表示名
  配列）・`ownMapLabel`（自マップの表示名）の 2 新規プロップとして渡す（既存プロップの意味は
  不変）。
- **UI（C13）** `SkeletonGate.tsx`：`ann.checkable===false` ブロックで `reason` が
  `source-not-found`／`notChecked`（フォールバック）のときだけ `skeleton-evidence-caution` の
  ⚠ 行に格上げ（他の reason＝constant/missing-columns/no-template/unsupported は muted のまま、
  対象外）。`SkeletonEvidence` に `onReattach?: () => void` を追加し、`source-not-found` のとき
  だけ警告直下に `btn btn--ghost btn--sm` を出す。`SkeletonGate` 本体にも同名 prop を追加し、
  ゲート下部の filesGone 行（`!canRevalidate` ブロック）にも同じボタンを追加（既存の discard
  ボタンはそのまま残す）。degraded（`!canRevalidate` または当該マップの `reason` が
  `source-not-found`）×複数マップ×C12 の所属が不成立、のときだけ keySentence 直下に
  「どの種類の ID にも含まれていません」を追加（C12 の所属文とは排他）。`filesGoneText` は
  `onReattach` の有無で文言を出し分け（無ければ discard ボタンを指す従来文、あれば reattach
  ボタンを指す新文）。**`ui/src/kantan/KantanWizard.tsx` 側**：新規の状態遷移は発明せず、
  既存の `onGateFilesDropped`（S4 の可視 DropZone が既に使っている、ドラフト骨格を保ったまま
  ファイルを差し替えて `recheckEvidence` を再実行する関数）をそのまま呼ぶ隠し
  `<input type="file">` を追加し、`SkeletonGate` の `onReattach` からその `input` をクリックする
  だけの薄い関数（`onReattach`）を渡す——ボタンの見た目だけが増え、遷移ロジックは 1 か所
  （`onGateFilesDropped`）のまま。
- **UI（C13 追補）** `SkeletonGate.tsx`：`SkeletonEvidence` に `sourceName?: string`（呼び出し側が
  `m.source` の basename＝`basename()` を渡す、`ownMapLabel` と同じ流儀）・
  `presentFileNames?: string[]`・`onAdoptRename?: (expectedName: string) => void` を追加。
  `source-not-found`／`notChecked` の本文キーを、`sourceName` があるときだけ named 版
  （`sourceNotFoundNamed`／`notCheckedNamed`）に差し替え。`mismatch`（source-not-found かつ
  `presentFileNames` が 1 件以上かつ `sourceName` を含まない）のときだけ警告直下に
  `sourceMismatch` 行を追加し、**さらに `presentFileNames.length === 1` のときだけ**
  `adoptRename`（「このファイルを「{{name}}」として読み直す」）ボタンを追加（`skeleton-evidence-revert`
  流用——全幅化トラップの再発防止コメントも既存箇所に倣って明記）。**`KantanWizard.tsx` 側**：
  `presentFileNames` は `files.length > 0 ? files.map(f=>f.name) : sourceNames.map(s=>s.name)`
  （手持ちがあればそれ、restore 直後で `files` が空なら snapshot の `sourceNames`）。
  `onAdoptRename(expectedName)` は `files.length !== 1` なら何もしない（安全側）。1 件のときは
  `new File([files[0]], expectedName, { type: files[0].type })` で**名前だけ**を期待名に
  包み直し、既存の `onGateFilesDropped`（型を `FileList | File[] | null` に拡張——配列を渡せる
  ようにしただけで内部ロジックは不変）へそのまま渡す。中身の裏取りはしない——別物なら
  `onGateFilesDropped` 経由の再検査が `missing-columns` で正しく落とす。
- **UI（C13 最終）** `basename()` を `SkeletonGate.tsx`（コンポーネント専用ファイル・非コンポーネント
  export は react-refresh 制約に抵触）から `skeletonDiagram.ts`（非コンポーネント共有ヘルパー置き場、
  `containmentParents` 等と同居）へ export 付きで移設し、両ファイルから import。`KantanWizard.tsx`
  の `onGateFilesDropped`（`let arr = Array.from(list ?? [])` の直後、kind/mismatch チェックより前）
  に自動充当を追加：`arr.length === 1 && skeleton` かつ `new Set(skeleton.maps.map(m =>
  basename(m.source)))` が**ちょうど 1 種類**（単一ソース設計）で `arr[0].name` がそれと不一致なら、
  確認なしで `arr = [new File([arr[0]], expected, { type: arr[0].type })]` に差し替えてから以降の
  処理へ進む。**この経路は「名前が snapshot の `sourceNames` と違えば確認ダイアログ」を発火させない**
  ——理由はコードの順序そのもの：ダイアログの判定は自動充当より**後**にあり、`before`（過去の
  `sourceNames`）と比較する `after` は自動充当**後**の `arr` の名前（＝設計の期待名）を使うため、
  単一ソース設計で以前も正しく読めていたケース（`sourceNames` が既に期待名を持つ）は
  `before === after` になり自然に一致する。複数ソース設計は `sources.size !== 1` で自動充当が
  発動せず、従来どおり名指し警告＋ワンタップ（`onAdoptRename`）が機能する。 | ユーザーの再発報告
  （2026-08-24：正しい CSV をダウンロードしたらブラウザが `xrd_card (1).csv` と付番し、置き直しても
  不一致→ワンタップを押させられ「入れたばかりなのに意味がわからない」）が起点：単一ソース設計に
  単一ファイルという場面では、名前一致は**そもそも何も分別していない**（行き先の候補が 1 つしか
  ない以上、名前が何であれ結論は変わらない）。分別する力を持つのは中身（列検査）だけなので、
  持たない力を使って人間に確認を求めるのをやめ、機械的に充ててから中身に語らせる。

### 確認したこと（実装で確認・C11 で対応）

再注釈後、置換後のキーが caution なしで一意な場合、`_annotate_map` の既存ルール
（`key_candidates: [] if (report.is_unique and not caution) else …`）により
**`key_candidates` は空になり、AI の元のキーは候補チップとして戻ってこない**。
一意性が保てない・caution が残るケースでは通常どおり候補が出るため、そちらでは元のキーが
`measurement_only: true` 付きで再度候補に現れうる場合もあるが、それに依存しない専用の
「元に戻す」ボタン（C11）で、状態によらず常に戻せるようにした。
