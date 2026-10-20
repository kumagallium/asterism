// 作業場（②の左に図・右に会話）の状態の純関数。描画（ThemeWorkspace・GraphCanvas・GraphChat）から
// 切り離してあるので、「提案（薄い）」と本物の区別・薄い差分の重ね描き・人の直しの差分・
// 落とされた操作の読みを vitest でそのまま固定できる。
//
// 設計の芯（design.md §16・契約 pr7b）:
//   - サーバの下書きの形は変えない。②に入った時点の AI の下書き（概念・関係すべて）は UI の状態
//     （`Pending`）で「提案（薄い）」として扱い、「そう」で本物にする。③に進むときは**本物だけ**を送る。
//   - 本物の関係は両端も本物（関係を受けると両端の概念も本物になる）。
//   - 人の直接の編集（プルダウン・ドラッグ・手で足す）は AI の提案と同じ形の差分（`GraphOp`）。
import type { GraphDropped, GraphOp } from './consultGraphApi'
import type { ConceptRow, RelKind, RelationRow, TermKind, ThemeDraft } from './themeDraft'

// ---- 提案（薄い）と本物 -------------------------------------------------------------------

/** 「提案（薄い）」のまま、まだ本物になっていない行。概念は key・関係は `from|rel|to`。 */
export interface Pending {
  concepts: string[]
  relations: string[]
}

export const NO_PENDING: Pending = { concepts: [], relations: [] }

export const relId = (r: { from: string; rel: string; to: string }): string => `${r.from}|${r.rel}|${r.to}`

/** ②に入った時点の AI の下書き: すべての概念・関係が提案（薄い）。 */
export function pendingOf(draft: Pick<ThemeDraft, 'concepts' | 'relations'>): Pending {
  return { concepts: draft.concepts.map((c) => c.key), relations: draft.relations.map(relId) }
}

export const pendingTotal = (p: Pending): number => p.concepts.length + p.relations.length

/** 概念を本物にする。 */
export function acceptConcepts(p: Pending, keys: Iterable<string>): Pending {
  const drop = new Set(keys)
  return { ...p, concepts: p.concepts.filter((k) => !drop.has(k)) }
}

/** 関係を本物にする（両端の概念も本物になる — 本物の関係の端が薄いままだと確定に出ない）。 */
export function acceptRelations(p: Pending, ids: Iterable<string>, draft: Pick<ThemeDraft, 'relations'>): Pending {
  const set = new Set(ids)
  const ends = draft.relations.filter((r) => set.has(relId(r))).flatMap((r) => [r.from, r.to])
  return acceptConcepts({ ...p, relations: p.relations.filter((id) => !set.has(id)) }, ends)
}

/** 下書きから消えた行は、提案の状態からも外す。 */
export function pruneToDraft(p: Pending, draft: Pick<ThemeDraft, 'concepts' | 'relations'>): Pending {
  const keys = new Set(draft.concepts.map((c) => c.key))
  const ids = new Set(draft.relations.map(relId))
  return { concepts: p.concepts.filter((k) => keys.has(k)), relations: p.relations.filter((id) => ids.has(id)) }
}

/** 差分が名指す概念の key（標準の語の curie が混じっても概念ではないので無害）。 */
export function opKeys(op: GraphOp): string[] {
  const fields: Record<string, string[]> = {
    add_concept: ['key'],
    add_relation: ['from', 'to'],
    retarget: ['from', 'to', 'new_to'],
    remove: ['key', 'from', 'to'],
    rename: ['key'],
    add_question: ['target', 'kind', 'property'],
  }
  const out: string[] = []
  for (const f of fields[op.op] ?? []) {
    const v = op[f]
    if (typeof v === 'string' && v) out.push(v)
  }
  return out
}

/**
 * 差分が当たったあとの提案の状態。当たった差分が触れた概念・線は本物になる
 * （人が「そう」と言った・自分で直した、ということ）。触れた概念どうしの間の薄い線も本物にする。
 * 下書きから消えた行は外す。
 */
export function settlePending(p: Pending, applied: GraphOp[], after: Pick<ThemeDraft, 'concepts' | 'relations'>): Pending {
  const keys = applied.flatMap(opKeys)
  const rels: string[] = []
  for (const op of applied) {
    if (op.op === 'add_relation' && typeof op.from === 'string') rels.push(relId({ from: op.from, rel: String(op.rel), to: String(op.to) }))
    if (op.op === 'retarget' && typeof op.from === 'string') {
      rels.push(relId({ from: op.from, rel: String(op.rel), to: String(op.new_to) }))
    }
  }
  // 触れた概念どうしを結ぶ、まだ薄い線も本物にする（触れた 2 つの間の線だけ薄いままだと、図がつながらない）
  const touched = new Set(keys)
  for (const r of after.relations) if (touched.has(r.from) && touched.has(r.to)) rels.push(relId(r))
  return pruneToDraft(acceptRelations(acceptConcepts(p, keys), rels, after), after)
}

/** 確定（③・件数）に使う下書き: 提案（薄い）を除く。本物の関係の端はすべて本物。 */
export function realDraft(draft: ThemeDraft, p: Pending): ThemeDraft {
  if (pendingTotal(p) === 0) return draft
  const pc = new Set(p.concepts)
  const pr = new Set(p.relations)
  return {
    ...draft,
    concepts: draft.concepts.filter((c) => !pc.has(c.key)),
    relations: draft.relations.filter((r) => !pr.has(relId(r)) && !pc.has(r.from) && !pc.has(r.to)),
  }
}

// ---- 提案の保存（この端末・下書きごと） -----------------------------------------------------

const STORE_PREFIX = 'asterism.themePending.'

/** 再開で続きから見られるよう、提案（薄い）の状態を下書きの id ごとに端末へ置く。壊れていれば無し。 */
export function loadPending(draftId: string, storage: Pick<Storage, 'getItem'> | null = safeStorage()): Pending {
  if (!draftId || !storage) return NO_PENDING
  try {
    const raw = JSON.parse(storage.getItem(STORE_PREFIX + draftId) ?? 'null') as Partial<Pending> | null
    if (!raw) return NO_PENDING
    const strs = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [])
    return { concepts: strs(raw.concepts), relations: strs(raw.relations) }
  } catch {
    return NO_PENDING
  }
}

export function savePending(
  draftId: string,
  p: Pending,
  storage: Pick<Storage, 'setItem' | 'removeItem'> | null = safeStorage(),
): void {
  if (!draftId || !storage) return
  try {
    if (pendingTotal(p) === 0) storage.removeItem(STORE_PREFIX + draftId)
    else storage.setItem(STORE_PREFIX + draftId, JSON.stringify(p))
  } catch {
    /* 置けなくても進める（再開で全部本物として見えるだけ） */
  }
}

function safeStorage(): Storage | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage
  } catch {
    return null
  }
}

// ---- 図に重ねる薄い差分 ---------------------------------------------------------------------

export interface CanvasConcept {
  row: ConceptRow
  /** `add`＝提案（薄い塗り＋「提案」の印）・`remove`＝外す提案（打ち消し）。 */
  ghost?: 'add' | 'remove'
}

export interface CanvasRelation {
  row: RelationRow
  /** `add`＝足す提案・`retarget`＝付け替えの新しい線（薄い線＋「→」）・`moved`＝付け替えで離れる元の線・`remove`＝外す提案。 */
  ghost?: 'add' | 'retarget' | 'moved' | 'remove'
  /** 下書きの関係の添字（提案の線は null）。 */
  draftIndex: number | null
}

export interface CanvasModel {
  concepts: CanvasConcept[]
  relations: CanvasRelation[]
}

const REL_KIND_LIST: RelKind[] = ['sub', 'eq', 'quantity', 'part', 'lowers', 'raises', 'affects']
const isRel = (v: unknown): v is RelKind => REL_KIND_LIST.includes(v as RelKind)
const sv = (v: unknown): string => (typeof v === 'string' ? v : '')

/**
 * 図に描く行。下書きの概念・関係に、提案（薄い）の状態と、まだ返事をしていない提案カードの
 * 差分（`ghostOps`）を重ねる。`ghostOps` は下書きを変えない — 「そう」で本物に・「違う」で消える。
 */
export function buildCanvas(draft: ThemeDraft, pending: Pending, ghostOps: GraphOp[]): CanvasModel {
  const pc = new Set(pending.concepts)
  const pr = new Set(pending.relations)
  const concepts: CanvasConcept[] = draft.concepts.map((row) => ({ row, ...(pc.has(row.key) ? { ghost: 'add' as const } : {}) }))
  const relations: CanvasRelation[] = draft.relations.map((row, i) => ({
    row,
    draftIndex: i,
    ...(pr.has(relId(row)) || pc.has(row.from) || pc.has(row.to) ? { ghost: 'add' as const } : {}),
  }))
  const conceptAt = (key: string) => concepts.findIndex((c) => c.row.key === key)
  const relAt = (id: string) => relations.findIndex((r) => r.draftIndex !== null && relId(r.row) === id)
  for (const op of ghostOps) {
    if (op.op === 'add_concept') {
      const key = sv(op.key)
      if (!key || conceptAt(key) >= 0) continue
      concepts.push({
        ghost: 'add',
        row: {
          key,
          label_ja: sv(op.label_ja),
          label_en: sv(op.label_en),
          aliases: Array.isArray(op.aliases) ? op.aliases.filter((a): a is string => typeof a === 'string') : [],
          kind: op.kind === 'property' ? 'property' : 'class',
          use: sv(op.use) || 'new',
          slug: '',
          reason: sv(op.reason),
          match: null,
          by: 'ai',
        },
      })
    } else if (op.op === 'add_relation' && isRel(op.rel)) {
      relations.push({
        ghost: 'add',
        draftIndex: null,
        row: { from: sv(op.from), rel: op.rel, to: sv(op.to), reason: sv(op.reason), problem: null, by: 'ai' },
      })
    } else if (op.op === 'retarget' && isRel(op.rel)) {
      const i = relAt(relId({ from: sv(op.from), rel: op.rel, to: sv(op.to) }))
      if (i >= 0) relations[i] = { ...relations[i], ghost: 'moved' }
      relations.push({
        ghost: 'retarget',
        draftIndex: null,
        row: { from: sv(op.from), rel: op.rel, to: sv(op.new_to), reason: sv(op.reason), problem: null, by: 'ai' },
      })
    } else if (op.op === 'remove') {
      if (op.target === 'concept') {
        const key = sv(op.key)
        const i = conceptAt(key)
        if (i >= 0) concepts[i] = { ...concepts[i], ghost: 'remove' }
        relations.forEach((r, j) => {
          if (r.row.from === key || r.row.to === key) relations[j] = { ...r, ghost: 'remove' }
        })
      } else if (op.target === 'relation' && isRel(op.rel)) {
        const i = relAt(relId({ from: sv(op.from), rel: op.rel, to: sv(op.to) }))
        if (i >= 0) relations[i] = { ...relations[i], ghost: 'remove' }
      }
    }
  }
  return { concepts, relations }
}

// ---- 人の直接編集 → 同じ形の差分 -------------------------------------------------------------

export function retargetOp(rel: Pick<RelationRow, 'from' | 'rel' | 'to'>, newTo: string): GraphOp {
  return { op: 'retarget', from: rel.from, rel: rel.rel, to: rel.to, new_to: newTo, reason: '' }
}

export function addRelationOp(rel: { from: string; rel: RelKind; to: string }): GraphOp {
  return { op: 'add_relation', from: rel.from, rel: rel.rel, to: rel.to, reason: '' }
}

export function removeRelationOp(rel: Pick<RelationRow, 'from' | 'rel' | 'to'>): GraphOp {
  return { op: 'remove', target: 'relation', from: rel.from, rel: rel.rel, to: rel.to, reason: '' }
}

export function addConceptOp(c: {
  key: string
  label_ja: string
  label_en: string
  aliases: string[]
  kind: TermKind
}): GraphOp {
  return { op: 'add_concept', ...c, use: 'new', reason: '' }
}

/**
 * 線の A・関係・B を直す。B だけ変えるなら 1 つの `retarget`。A や関係を変えるなら、新しい線を足してから
 * 古い線を外す 2 段（足せなかったら外さない — 線を失わない）。同じなら空。
 */
export function relationEditSteps(
  old: Pick<RelationRow, 'from' | 'rel' | 'to'>,
  next: { from: string; rel: RelKind; to: string },
): GraphOp[][] {
  if (old.from === next.from && old.rel === next.rel && old.to === next.to) return []
  if (old.from === next.from && old.rel === next.rel) return [[retargetOp(old, next.to)]]
  return [[addRelationOp(next)], [removeRelationOp(old)]]
}

let seq = 0
/** UI が一意に作る提案の ID（`[A-Za-z0-9_.:-]{1,60}`）。 */
export function newProposalId(prefix = 'h'): string {
  seq += 1
  const rand = Math.random().toString(36).slice(2, 8)
  return `${prefix}-${Date.now().toString(36)}-${seq.toString(36)}${rand}`.slice(0, 60)
}

/** 手で足す概念の key（`n` + 連番。AI の `n1…` とぶつからないよう既存の key を見て選ぶ）。 */
export function nextConceptKey(existing: Iterable<string>): string {
  const used = new Set(existing)
  let n = 1
  while (used.has(`h${n}`)) n += 1
  return `h${n}`
}

/** 「ここを広げる」が会話に送る一言。 */
export function expandMessage(name: string, lang: string): string {
  return lang.startsWith('en') ? `Expand around ${name}` : `${name} の周りを広げて`
}

// ---- 適用の返りの読み -----------------------------------------------------------------------

/** 落とされた理由（api の reason）。これ以外は `unsupported` の言い回しに寄せる。 */
export const DROP_REASONS = [
  'unknown_key',
  'same',
  'mixed',
  'cycle',
  'quantity_target',
  'target_not_property',
  'dismissed',
  'duplicate',
  'not_found',
  'limit',
  'not_renamable',
  'dataset_only',
  'unsupported',
] as const

export function dropReason(reason: string): (typeof DROP_REASONS)[number] {
  return (DROP_REASONS as readonly string[]).includes(reason) ? (reason as (typeof DROP_REASONS)[number]) : 'unsupported'
}

/** 送った ops の部分集合の返りを、元の ops の添字に戻す（`sent[i]` = 元の添字）。 */
export function dropsByOriginal(dropped: GraphDropped[], sent: number[]): Map<number, GraphDropped> {
  const out = new Map<number, GraphDropped>()
  for (const d of dropped) {
    const orig = sent[d.index]
    if (orig !== undefined) out.set(orig, d)
  }
  return out
}

/**
 * 「1 件ずつ」で ✓ したとき、その操作が使う新しい概念（同じ提案の `add_concept`）も一緒に送る
 * （概念を足す前の関係は unknown_key で落ちる）。まだ決めていない add_concept だけ。
 * 返りは元の添字（昇順）。
 */
export function withDependencies(ops: GraphOp[], index: number, decided: readonly (string | null)[]): number[] {
  const need = new Set(opKeys(ops[index]))
  const out = new Set<number>([index])
  ops.forEach((o, i) => {
    if (i !== index && o.op === 'add_concept' && need.has(sv(o.key)) && decided[i] === null) out.add(i)
  })
  return [...out].sort((a, b) => a - b)
}
