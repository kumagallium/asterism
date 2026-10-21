// 話の状態の純関数（vitest で検査する）。画面は fetch の返りをここに通して話を更新する。
import type { Binding, BindingDecision, Decided, ConfirmResponse, RevertResponse, Session, SessionBrief, Turn, TurnResponse } from './chatApi'

/** ✓ を出す手番の id（最後の未確認の返事）。無ければ null。 */
export function confirmTarget(turns: readonly Turn[]): string | null {
  for (let i = turns.length - 1; i >= 0; i--) if (turns[i].state === 'proposed') return turns[i].id
  return null
}

/** ✓ で置く差分の数（未確認の手番の ops の合計）。 */
export function proposedCount(turns: readonly Turn[]): number {
  let n = 0
  for (const t of turns) if (t.state === 'proposed') n += t.reply.ops.length
  return n
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

/** 結ぶ／結ばないの選び: 人が変えたもの（`picked`）を既定（`decided`）に重ねる。✓ のときに送る。 */
export function bindingDecisions(bindings: readonly Binding[], picked: Readonly<Record<string, Decided>>): BindingDecision[] {
  return bindings.map((b) => ({ column: b.column, decided: picked[b.column] ?? b.decided }))
}
