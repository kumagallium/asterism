// 話の状態の純関数（vitest で検査する）。画面は fetch の返りをここに通して話を更新する。
import type { Binding, BindingDecision, Decided, ConfirmResponse, RevertResponse, Session, SessionBrief, Turn, TurnResponse } from './chatApi'

export type TurnKind = 'theme' | 'table' | 'machine'

/** 手番の種類: 表の手番（表を添えた・ID の選び）／機械の手番（名前の一致）／話（テーマ）の提案。 */
export function turnKind(t: Turn): TurnKind {
  if (t.attachment || t.reply.notes.some((n) => n.kind === 'choice_id')) return 'table'
  if (t.reply.suggestions.length > 0) return 'machine'
  return 'theme'
}

/** 選び待ち: 選択のある注記で、まだ決めていないものがある。✓ は出さず「決める」だけ。 */
export function awaitingChoice(t: Turn): boolean {
  return t.reply.notes.some((n) => n.choice !== null && n.choice.chosen === undefined)
}

/**
 * ✓ を出す手番の id。話（テーマ）の提案は最後の 1 つだけ、表の手番と機械の手番はそれぞれに。
 * 選び待ちの手番には出さない。
 */
export function confirmTargets(turns: readonly Turn[]): Set<string> {
  const out = new Set<string>()
  let lastTheme: string | null = null
  for (const t of turns) {
    if (t.state !== 'proposed' || awaitingChoice(t)) continue
    if (turnKind(t) === 'theme') lastTheme = t.id
    else out.add(t.id)
  }
  if (lastTheme) out.add(lastTheme)
  return out
}

/** ✓ で置く差分の数（話（テーマ）の未確認の手番の ops の合計）。表・機械の手番は数えない。 */
export function proposedCount(turns: readonly Turn[]): number {
  let n = 0
  for (const t of turns) if (t.state === 'proposed' && turnKind(t) === 'theme') n += t.reply.ops.length
  return n
}

/** 「機械」の印を出すか: 名前の一致や「戻して」などの機械の返事だけ。表の手番には付けない。 */
export function showMachineMark(t: Turn): boolean {
  if (t.by !== 'machine') return false
  if (t.attachment) return false
  return !t.reply.notes.some((n) => n.kind === 'choice_id' || n.kind === 'attach_failed' || n.kind === 'unsupported')
}

/** 一覧と見出しに出す題。空なら「新しい話」。 */
export function sessionTitle(s: { title: string }, untitled: string): string {
  return s.title.trim() || untitled
}

function withTurn(turns: readonly Turn[], turn: Turn): Turn[] {
  const i = turns.findIndex((t) => t.id === turn.id)
  if (i < 0) return [...turns, turn]
  const next = [...turns]
  next[i] = turn
  return next
}

function withBrief(s: Session, b: SessionBrief): Session {
  return { ...s, title: b.title, theme_id: b.theme_id, draft_id: b.draft_id, updated_at: b.updated_at }
}

/** 手番の返りを話に足す（手番を足し、題・テーマ・下書きの欄を更新）。 */
export function applyTurnResponse(s: Session, res: TurnResponse): Session {
  return { ...withBrief(s, res.session), turns: withTurn(s.turns, res.turn) }
}

/** ✓ の返り。置けた手番（confirmed）は全部確認済みに（同じ commit を共有・before は捨てる）。 */
export function applyConfirm(s: Session, res: ConfirmResponse): Session {
  const ids = new Set(res.confirmed ?? [])
  const commit = res.commit ?? res.turn.commit
  let turns = withTurn(s.turns, res.turn).map((t) =>
    ids.has(t.id) && t.id !== res.turn.id ? { ...t, state: 'confirmed' as const, before: null, commit } : t,
  )
  if (res.followup) turns = withTurn(turns, res.followup)
  return { ...withBrief(s, res.session), turns }
}

/** 戻すの返り。取り消された手番（reverted）は全部取り消し済みに。 */
export function applyRevert(s: Session, res: RevertResponse): Session {
  const ids = new Set(res.reverted)
  const turns = withTurn(s.turns, res.turn).map((t) =>
    ids.has(t.id) && t.id !== res.turn.id ? { ...t, state: 'reverted' as const, before: null } : t,
  )
  return { ...withBrief(s, res.session), turns }
}

/** 返りを当てた話より、読み直した話を優先する（サーバ側で他の手番の状態や下書きが変わるため）。読み直せなければ返りのまま。 */
export function preferReloaded(applied: Session, reloaded: Session | null): Session {
  return reloaded ?? applied
}

/** チップの送信結果を true/false に畳む（失敗・例外は false ＝ 送った印を戻す）。 */
export async function settleAsk(run: () => Promise<boolean> | boolean | void): Promise<boolean> {
  try {
    return (await run()) !== false
  } catch {
    return false
  }
}

/** 残した語の名前: 名前の表から引けたらその名前、引けなければ slug のまま。 */
export function keptName(slug: string, names: ReadonlyMap<string, string> | undefined): string {
  return names?.get(slug) || slug
}

/** 図の語と下書きの札から、slug（key・existing:<slug>）→名前の表を作る。 */
export function nameTable(
  terms: readonly { slug: string; label: string }[],
  draft: { concepts: readonly { key: string; label: string; use?: string }[] } | null | undefined,
): Map<string, string> {
  const m = new Map<string, string>()
  for (const t of terms) if (t.slug && t.label) m.set(t.slug, t.label)
  for (const c of draft?.concepts ?? []) {
    if (!c.label) continue
    m.set(c.key, c.label)
    const u = /^existing:(.+)$/.exec(c.use ?? '')
    if (u) m.set(u[1], c.label)
  }
  return m
}

/** 手で足す概念の次の key（`h1`, `h2` …）。 */
export function nextHandKey(keys: readonly string[]): string {
  let max = 0
  for (const k of keys) {
    const m = /^h(\d+)$/.exec(k)
    if (m) max = Math.max(max, Number(m[1]))
  }
  return `h${max + 1}`
}

/**
 * 手で足す欄を出すか: 下書きがあり、AI が設定されていないあいだ（最後の AI の返事より後に「AI 未設定」の注記がある）。
 * 手で足した手番の返事には注記が付かないので、注記は話のどこにあってもよい。
 */
export function handFormShown(s: Pick<Session, 'draft_id' | 'turns'>): boolean {
  if (!s.draft_id) return false
  for (let i = s.turns.length - 1; i >= 0; i--) {
    const t = s.turns[i]
    if (t.state === 'reverted') continue
    if (t.by === 'ai') return false
    if (t.reply.notes.some((n) => n.kind === 'ai_unset')) return true
  }
  return false
}

/** 読んでいる最中の手番（ジョブの id があるもの）。話を開き直したとき、これを購読し直す。 */
export function runningJobs(turns: readonly Turn[]): { turnId: string; jobId: string }[] {
  const out: { turnId: string; jobId: string }[] = []
  for (const t of turns) if (t.state === 'running' && t.job_id) out.push({ turnId: t.id, jobId: t.job_id })
  return out
}

/** 結ぶ／結ばないの選び: 人が変えたもの（`picked`・並び順の番号が key）を既定（`decided`）に重ねる。✓ のときに送る。 */
export function bindingDecisions(bindings: readonly Binding[], picked: Readonly<Record<string, Decided>>): BindingDecision[] {
  return bindings.map((b, i) => ({ column: b.column, subject: b.subject, decided: picked[String(i)] ?? b.decided }))
}
