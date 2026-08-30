// 種類を分けるための決定論（ADR `kind-splitting-and-consult-suggestions.md`）。
//
// 骨格ゲート（`SkeletonGate.tsx`）と相談ドロワーの提案反映（`consult/`）は、
// 「同じキーの種類が二重記録なのか、意図した分割なのか」という**同じ判断**を
// する。式を 2 か所に書くと必ず食い違うので、ここに 1 つだけ置く。
//
// LLM は 1 回も呼ばない。ここにあるのは全部、骨格 JSON だけを見る純関数。

import type { DatasetNamespaceInfo, MappingSkeleton, SkeletonMap } from './api'
import { expandClass } from './datasetNamespace'

/** テンプレートの `{列名}` を並び順のまま取り出す（ID を決めている列）。 */
export function keyColumnsOf(map: SkeletonMap): string[] {
  const template = map.subject.template
  return template ? [...template.matchAll(/\{([^{}]+)\}/g)].map((x) => x[1]) : []
}

/** 同じソースを同じ鍵で数える種類のかたまりを見分ける指紋。 */
function twinSignature(map: SkeletonMap): string {
  const key = keyColumnsOf(map).slice().sort().join('\u0000')
  return [map.source, map.iterator ?? '', key, map.subject.template === undefined].join('\u0001')
}

/** この種類が**項目として**持つ列。`owns` を宣言していればその列だけ（G15 の
 *  宣言は世界知識で、機械の推測に勝つ）。宣言していなければ「ほかの種類が
 *  宣言していない残り全部」= `null`（REST）。
 *
 *  キー列は引かれる。**キーの共有は二重記録ではない** — G6 が禁じているのは
 *  同じ事実が 2 箇所にあることで、`a column another map owns appears here ONLY
 *  as a link/join key` のとおりキーの共有は最初から許されている。 */
function carriedColumns(map: SkeletonMap, keyColumns: Set<string>): Set<string> | null {
  const owns = (map.owns ?? []).map(String)
  if (owns.length === 0) return null
  return new Set(owns.filter((c) => !keyColumns.has(c)))
}

/** 2 つの種類が**同じ項目**を持つか。`null`（REST）どうしは、どちらも残り全部を
 *  持つので必ず重なる。宣言と REST は定義上ばらばら（宣言された列は「残り」から
 *  抜けている）。宣言どうしは素直に共通部分を見る。 */
function carriesTheSame(a: Set<string> | null, b: Set<string> | null): boolean {
  if (a === null && b === null) return true
  if (a === null || b === null) return false
  for (const column of a) if (b.has(column)) return true
  return false
}

/** 同じソースを同じ鍵で数え、**かつ持つ項目が重なる**種類の名前
 *  （ADR `kind-splitting-and-consult-suggestions.md` D1）。
 *
 * ```
 * 同じキーで種類が 2 つ
 *   ├ 持つ項目が重なる    → 二重記録。止める
 *   └ 項目が分かれている  → 意図的な分割。何も言わない
 * ```
 *
 *  もとの判定は**キーの同一性しか見ておらず**、カード（No・Name・化学式…）と
 *  結晶（Cell・Volume・Z value）のように**同じ `No` を使うが別のもの**という
 *  正当な分割まで止めていた（実測 2026-08-30）。項目の重なりまで見て絞る。
 *
 *  **警告を全部消しはしない。** 同じ実測で、AI は放っておくと `Sample` と
 *  `DiffractionPattern` が同じ列で同じ 1 件を作る本物の二重記録を出した。宣言が
 *  片方にも無い＝どちらも全部持つ、はいまどおり止まる。
 *
 *  機械は統合しない — どちらを残すかは設計の判断（G18）。見えるようにするだけ。 */
export function twinKindNames(skeleton: MappingSkeleton): Set<string> {
  const twins = new Set<string>()
  const groups = new Map<string, SkeletonMap[]>()
  for (const map of skeleton.maps) {
    const signature = twinSignature(map)
    groups.set(signature, [...(groups.get(signature) ?? []), map])
  }
  for (const group of groups.values()) {
    if (group.length < 2) continue
    const keyColumns = new Set(keyColumnsOf(group[0]).map(String))
    const carried = group.map((m) => carriedColumns(m, keyColumns))
    for (let i = 0; i < group.length; i += 1) {
      for (let j = i + 1; j < group.length; j += 1) {
        if (!carriesTheSame(carried[i], carried[j])) continue
        twins.add(group[i].name)
        twins.add(group[j].name)
      }
    }
  }
  return twins
}

/** `host` と**同じ鍵で数える**同じソースの兄弟（D4 で足した種類、AI が出した
 *  二重記録の片割れ）。列を起点にした種類とは別の生きもので、①の表のどの行にも
 *  属さない — 属さないからこそ「載せる種類」の行き先になる。
 *
 *  実機 2026-08-31: これを「列 → 種類」の対応表に混ぜていたため、登録先が
 *  カードの鍵の列になり、同じ鍵の兄弟が 2 つあると後勝ちで片方が消えた
 *  （D4 で足した種類が「載せる種類」の候補に出なかった）。 */
export function sameIdSiblings(skeleton: MappingSkeleton, hostName: string): string[] {
  const host = skeleton.maps.find((m) => m.name === hostName)
  if (!host) return []
  const key = keyColumnsOf(host).slice().sort().join('\u0000')
  return skeleton.maps
    .filter(
      (m) =>
        m.name !== host.name &&
        m.source === host.source &&
        keyColumnsOf(m).slice().sort().join('\u0000') === key,
    )
    .map((m) => m.name)
}

/** 新しい種類の住所の頭。親の下ではなくデータセットの根に置く — 最後の
 *  リテラル区間を 1 つ落として接頭辞（`xrr:` / `…/resource/`）まで戻す。
 *  （`SkeletonGate` の `splitConcept` から移設。つなぐための種類が「親の下」に
 *  見える住所を持つのを避けるための処理で、複数の入口が同じ形を作る要がある。） */
export function subjectHead(template: string): string {
  const beforeSlot = template.includes('{') ? template.slice(0, template.indexOf('{')) : template
  const trimmed = beforeSlot.replace(/\/$/, '')
  const cutAt = Math.max(trimmed.lastIndexOf('/'), trimmed.lastIndexOf(':'))
  return cutAt >= 0 ? trimmed.slice(0, cutAt + 1) : ''
}

/** 骨格スキーマが map の名前に課す形（`^[A-Za-z][\w-]*$`・
 *  `mapping_ir_schema.py`）に収まる名前を作る。ASCII に落ちない名前
 *  （日本語の種類名など）は `fallback` に逃がす。 */
export function slugMapName(raw: string, taken: Set<string>, fallback = 'kind'): string {
  const ascii = raw
    .toLowerCase()
    .replace(/[^0-9a-z]+/g, '_')
    .replace(/^[^a-z]+|_+$/g, '')
  const base = ascii || fallback
  let name = base
  for (let i = 2; taken.has(name); i += 1) name = `${base}${i}`
  return name
}

/** 親と**同じ鍵**で数える兄弟の種類を 1 つ作る（住所の区間だけが違う）。
 *
 *  `owns` にキー列を入れておくのが要点: ①D1 の判定で「項目が分かれている」側に
 *  立つので、足した瞬間に二重記録の警告が出ない ②`_parent_singleton` の
 *  `originals` から外れるのでカードが親のまま（成長プレビュー・行マップの
 *  scope 検査が沈黙しない） — サーバの `inferred_owns` が同じ列を推測するので、
 *  骨格に書くのは推測を明示にするだけで、意味は変わらない。 */
export function sameIdKind(
  parent: SkeletonMap,
  mapName: string,
  classes: string[],
): SkeletonMap {
  const template = parent.subject.template ?? ''
  const slots = template.includes('{') ? template.slice(template.indexOf('{')) : ''
  return {
    name: mapName,
    source: parent.source,
    ...(parent.iterator === undefined ? {} : { iterator: parent.iterator }),
    subject: { template: `${subjectHead(template)}${mapName}/${slots}`, classes },
    owns: keyColumnsOf(parent),
  }
}

/** 列 `column` を種類 `target` に載せ替える（1 つの列が属性として載るのは
 *  1 箇所だけ・G6）。`target` がそのソースのカード（既定の置き場）なら、宣言
 *  そのものを消す＝既定に戻る。 */
export function assignColumnOwner(
  skeleton: MappingSkeleton,
  source: string,
  column: string,
  target: string,
): MappingSkeleton {
  const maps = skeleton.maps.map((m) => {
    if (m.source !== source) return m
    const before = (m.owns ?? []).map(String)
    const kept = before.filter((c) => c !== column)
    const next = m.name === target ? [...kept, column] : kept
    if (next.length === before.length && next.every((c, i) => c === before[i])) return m
    if (next.length > 0) return { ...m, owns: next }
    // 宣言そのものを消す＝既定（カード）に戻す。`owns: []` は「何も持たない
    // 宣言」として読まれてしまうので、キーごと落とす。
    const rest = { ...m }
    delete rest.owns
    return rest
  })
  return { ...skeleton, maps }
}

// ---------------------------------------------------------------------------
// 相談チャットの提案（D3）を骨格に反映する 3 つの操作。
//
// どれも**決定論** — UI のチェック／ドロップダウンを機械が動かすのと同じで、
// LLM の再実行は要らない（`own` を決定論化した G18 と同じ筋）。採否は人が決める
// （提案は候補であって、反映を押すのは人）。
// ---------------------------------------------------------------------------

/** 同じキーから種類を分ける。`from` の項目のうち `columns` を新しい種類に移す。 */
export interface KindSplit {
  /** 分け元の map 名。 */
  from: string
  /** 新しい種類の名前（人が読む名前。`subject.classes` になる）。 */
  name: string
  /** 新しい種類に移す列。 */
  columns: string[]
}

/** 帰属の移動 — S4 の「載せる種類」と同じ操作。 */
export interface ColumnOwner {
  column: string
  /** 載せ先の map 名。 */
  map: string
}

/** ID を与える候補。`reason` は**必須**（K22: 押させるのではなく、選ばせる）。 */
export interface IdentifierPick {
  column: string
  reason: string
}

/** 反映の結果。`applied` は骨格が実際に変わった件数、`skipped` は画面には
 *  あるが変える所が無かった件数（既に済んでいる／制約で動かせない）。 */
export interface ApplyOutcome {
  skeleton: MappingSkeleton
  applied: number
  skipped: number
}

/** D3 `splits` — 同じキーで種類を分ける。
 *
 *  新しい種類は親と同じ鍵で数え、`owns` に指定された列を持つ。親の側は
 *  `assignColumnOwner` と同じ経路でその列を手放すので、D1 の判定では
 *  「項目が分かれている」= 警告なしになる。 */
export function applySplits(
  skeleton: MappingSkeleton,
  splits: KindSplit[],
  /** このデータセットの語彙。種類名は②の名前欄と**同じ経路**で CURIE にする
   *  （`kinds` 提案の反映と揃える）。無ければ書かれたままを使う。 */
  ns: Pick<DatasetNamespaceInfo, 'ontology_prefix'> | null = null,
): ApplyOutcome {
  let applied = 0
  let skipped = 0
  let next = skeleton
  for (const split of splits) {
    const parent = next.maps.find((m) => m.name === split.from)
    if (!parent) {
      skipped += 1
      continue
    }
    // その種類が実際に手放せる列だけを移す。キー列は動かせない（動かすと ID の
    // 作り方そのものが変わる — そこは「ID の作り方を自分で書く」の領分）。
    const keyColumns = new Set(keyColumnsOf(parent).map(String))
    const columns = split.columns.map(String).filter((c) => c && !keyColumns.has(c))
    if (columns.length === 0 || !split.name.trim()) {
      skipped += 1
      continue
    }
    const taken = new Set(next.maps.map((m) => m.name))
    const mapName = slugMapName(split.name, taken)
    const added = sameIdKind(parent, mapName, [expandClass(split.name.trim(), ns)])
    added.owns = [...new Set([...(added.owns ?? []), ...columns])]
    const idx = next.maps.findIndex((m) => m.name === parent.name)
    let withKind: MappingSkeleton = {
      ...next,
      maps: [...next.maps.slice(0, idx + 1), added, ...next.maps.slice(idx + 1)],
    }
    // 移した列は、ほかの種類の宣言から外す（G6: 属性として載るのは 1 箇所だけ）。
    for (const column of columns) {
      withKind = assignColumnOwner(withKind, parent.source, column, mapName)
    }
    next = withKind
    applied += 1
  }
  return { skeleton: next, applied, skipped }
}

/** D3 `owners` — 帰属の移動。**同じ分散クラス内**に限る
 *  （`meaning-before-identity.md`: ファイル全体の値を行の種類に移すと、同じ値が
 *  全行に写って G6 違反になる）。`classOf` はその map の分散クラス
 *  （サーバの `collapse_kind`）を返す。片方でも分からなければ動かさない。 */
export function applyOwners(
  skeleton: MappingSkeleton,
  owners: ColumnOwner[],
  classOf: (mapName: string) => string | undefined,
): ApplyOutcome {
  let applied = 0
  let skipped = 0
  let next = skeleton
  for (const owner of owners) {
    const target = next.maps.find((m) => m.name === owner.map)
    if (!target || !owner.column) {
      skipped += 1
      continue
    }
    const current = currentOwnerOf(next, target.source, owner.column)
    if (current === owner.map) {
      skipped += 1
      continue
    }
    const targetClass = classOf(owner.map)
    const currentClass = current === null ? undefined : classOf(current)
    if (!targetClass || !currentClass || targetClass !== currentClass) {
      skipped += 1
      continue
    }
    // キー列は動かさない（動かすと ID の作り方が変わる）。
    if (keyColumnsOf(target).includes(owner.column)) {
      skipped += 1
      continue
    }
    next = assignColumnOwner(next, target.source, owner.column, owner.map)
    applied += 1
  }
  return { skeleton: next, applied, skipped }
}

/** いまその列を**項目として**持っている種類。宣言が無ければ、そのソースの
 *  カード（宣言を 1 つも持たない最初の種類）が既定の置き場。 */
export function currentOwnerOf(
  skeleton: MappingSkeleton,
  source: string,
  column: string,
): string | null {
  const here = skeleton.maps.filter((m) => m.source === source)
  const declared = here.find((m) => (m.owns ?? []).map(String).includes(column))
  if (declared) return declared.name
  const card = here.find((m) => (m.owns ?? []).length === 0)
  return card ? card.name : null
}

/** D3 `identifiers` — その値自体を ID を持つ 1 件にする（①のチェックと同じ）。
 *  `host` はその列がいま載っているカード。列そのものをキーにした種類を作る。 */
export function applyIdentifiers(
  skeleton: MappingSkeleton,
  identifiers: IdentifierPick[],
  host: string,
): ApplyOutcome {
  let applied = 0
  let skipped = 0
  let next = skeleton
  for (const pick of identifiers) {
    const parent = next.maps.find((m) => m.name === host)
    const column = String(pick.column ?? '')
    if (!parent || !column) {
      skipped += 1
      continue
    }
    // 既にその列で数える種類があるなら、何もしない（同じ種類を二度作らない）。
    const exists = next.maps.some(
      (m) =>
        m.source === parent.source &&
        keyColumnsOf(m).length === 1 &&
        keyColumnsOf(m)[0] === column,
    )
    if (exists || keyColumnsOf(parent).includes(column)) {
      skipped += 1
      continue
    }
    next = promoteColumnToKind(next, host, column)
    applied += 1
  }
  return { skeleton: next, applied, skipped }
}

/** 共有された概念を、`key` 列で数える別の種類に切り出す（G15 の split）。
 *  新しい種類は `columns` を持ち、親のすぐ下に入る。返り値は新しい骨格と、
 *  足した map（呼び出し側が「どこに増えたか」を見せるのに使う）。
 *
 *  ①のチェック（`promoteColumnToKind`）・成長プレビューの一括切り出し・
 *  `identifiers` の反映が、全部この 1 本を通る。 */
export function splitSharedConcept(
  skeleton: MappingSkeleton,
  hostName: string,
  columns: string[],
  key: string,
): { skeleton: MappingSkeleton; added: SkeletonMap | null } {
  const idx = skeleton.maps.findIndex((m) => m.name === hostName)
  const parent = skeleton.maps[idx]
  if (!parent || columns.length === 0 || !key) return { skeleton, added: null }
  const template = parent.subject.template ?? ''
  const taken = new Set(skeleton.maps.map((m) => m.name))
  const mapName = slugMapName(key, taken, 'shared')
  const parentClass = parent.subject.classes?.[0] ?? ''
  const classPrefix = parentClass.includes(':')
    ? parentClass.slice(0, parentClass.indexOf(':') + 1)
    : ''
  const pascal = mapName
    .split('_')
    .filter(Boolean)
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join('')
  const added: SkeletonMap = {
    name: mapName,
    source: parent.source,
    subject: {
      template: `${subjectHead(template)}${mapName}/{${key}}`,
      classes: classPrefix ? [`${classPrefix}${pascal}`] : [],
    },
    owns: columns,
  }
  return {
    skeleton: {
      ...skeleton,
      maps: [...skeleton.maps.slice(0, idx + 1), added, ...skeleton.maps.slice(idx + 1)],
    },
    added,
  }
}

/** 列そのものをキーにした種類を 1 つ作る（`owns` はその列だけ = 値のカタログ）。 */
export function promoteColumnToKind(
  skeleton: MappingSkeleton,
  hostName: string,
  column: string,
): MappingSkeleton {
  return splitSharedConcept(skeleton, hostName, [column], column).skeleton
}
