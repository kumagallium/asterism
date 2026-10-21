import {
  type Dispatch,
  type ReactNode,
  type SetStateAction,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { useTranslation } from 'react-i18next'
import { dismiss } from '../decide/decideApi'
import { useLlmSettings } from '../settings/context'
import {
  type GraphApplyResult,
  type GraphOp,
  applyGraph,
  dismissGraph,
  proposeGraph,
} from './consultGraphApi'
import { GraphCanvas } from './GraphCanvas'
import { GraphChat } from './GraphChat'
import { DraftCard, ProposalCard } from './ProposalCard'
import {
  type RelKind,
  type TermKind,
  type ThemeDraft,
  REL_KINDS,
  conceptDismissKey,
  conceptName,
  dismissEndKey,
  hasConceptNamed,
  relationPredicate,
  removeConcept,
  removeRelation,
  unlinkedKeys,
} from './themeDraft'
import { ThemesApiError } from './themesApi'
import { useNarrow } from './useNarrow'
import {
  type ChatItem,
  type Pending,
  type ProposalItem,
  acceptConcepts,
  acceptRelations,
  addConceptOp,
  buildCanvas,
  dropReason,
  dropsByOriginal,
  expandMessage,
  ghostOpsOf,
  historyOf,
  newProposalId,
  newProposalItem,
  nextConceptKey,
  nextItemId,
  openRows,
  pendingTotal,
  pruneToDraft,
  realDraft,
  relId,
  relationEditSteps,
  removeRelationOp,
  restoreItems,
  settlePending,
  withDependencies,
} from './workspaceState'
import { nameOfKey, opSentence, relationSentenceOf } from './workspaceText'

// ②「下書きをたしかめる」の作業場 — 左に図・右に会話（design.md §16）。
//   上＝問い（原文・答え方・知識 N・データ N・欲しいデータ）／左＝図（GraphCanvas）／
//   右＝会話（GraphChat・たためる・375px では下からのシート）／下＝「戻る」「N 件を置く」。
// 状態の持ち方:
//   - 下書き（draft）はサーバのもの。AI の提案も人の直接編集も、同じ形の差分（GraphOp）を
//     POST /api/consult/graph/apply に送り、返った draft で図と文を更新する。
//   - ②に入った時点の AI の下書きは、UI の状態（pending）で「提案（薄い）」として扱い、
//     「全部図に入れる」で本物にする。確定（③）に送るのは本物だけ。
//   - 却下（違う・✕）は dismiss に保存し、次の提案に出さない。
//   - AI は SPARQL・コード・IRI を書かず、データの値も見ない。確定は人（来歴は適用した差分だけ）。

export interface ThemeWorkspaceProps {
  draft: ThemeDraft
  setDraft: Dispatch<SetStateAction<ThemeDraft>>
  /** 下書きを保存する（まだ id が無ければ作る）。返りは保存後の下書き。 */
  persist: (d: ThemeDraft) => Promise<ThemeDraft>
  lang: string
  pending: Pending
  /** 更新関数で受ける（非同期の続きから古い値を上書きしない）。 */
  setPending: (f: (p: Pending) => Pending) => void
  /** 最初の AI の下書きの大きさ（会話の 1 枚目のカード）。無ければ出さない。 */
  firstDraft: { concepts: number; relations: number; resumed?: boolean } | null
  /** 上の 1 行（問い・答え方）と、AI の下書きの状態の表示。 */
  top: ReactNode
  /** 下（戻る・まとめて確定）。 */
  bottom: ReactNode
  /** 戻ってきたときに残す理由（置けない組み合わせ・422 など）。 */
  notice?: string
}

type AiState = 'ok' | 'unconfigured'

export function ThemeWorkspace(p: ThemeWorkspaceProps) {
  const { t } = useTranslation()
  const { draft, lang, pending } = p
  const { isReady, getActiveCredentials, openSettings } = useLlmSettings()
  const tf = t as unknown as (k: string, o?: Record<string, unknown>) => string
  const nameOf = useCallback((key: string) => nameOfKey(draft, key, lang, tf), [draft, lang, tf])
  const sentenceOfRel = (r: { from: string; rel: string; to: string }) => relationSentenceOf(draft, r, lang, tf)

  // 非同期の続きから最新の下書きを読む（二段の適用で古い下書きを上書きしない）
  const draftRef = useRef(draft)
  useEffect(() => {
    draftRef.current = draft
  }, [draft])

  const [items, setItems] = useState<ChatItem[]>(() => [
    ...restoreItems(draft.chat, (op, labels) => opSentence(draft, op, lang, tf, labels)),
  ])
  const [thinking, setThinking] = useState(false)
  const [applying, setApplying] = useState(false)
  const [ai, setAi] = useState<AiState>('ok')
  const [actionErr, setActionErr] = useState('')
  const [draftPer, setDraftPer] = useState(false)
  // 375px では会話は下からのシート。開くのは「相談を開く」と、新しい提案カードが来たときだけ
  // （札や線を押したり、ドラッグしたりしても開かない）
  const narrow = useNarrow()
  const [open, setOpen] = useState(!narrow)
  const firstNarrow = useRef(narrow)
  useEffect(() => {
    if (firstNarrow.current === narrow) return
    firstNarrow.current = narrow
    setOpen(!narrow)
  }, [narrow])
  // 選んだ札・線・文に重ねたポインタ（線は from|rel|to で持つ — 下書きが変わっても同じ線を指す）
  const [selectedKey, setSelectedKey] = useState<string | null>(null)
  const [selectedEdge, setSelectedEdge] = useState<string | null>(null)
  const [litRel, setLitRel] = useState<number | null>(null)
  const [edit, setEdit] = useState<{ id: string; from: string; rel: RelKind; to: string } | null>(null)
  const [hand, setHand] = useState({ name: '', kind: 'class' as TermKind, en: '', aliases: '' })
  // 前の適用から今までの、人の発言（適用のときに送って下書きの会話に残す）
  const unsaid = useRef<string[]>([])

  const aiReady = isReady && ai === 'ok'
  const busy = thinking || applying

  // 提案の状態は、下書きから消えた行を外しておく
  useEffect(() => {
    const next = pruneToDraft(pending, draft)
    if (next.concepts.length !== pending.concepts.length || next.relations.length !== pending.relations.length) {
      p.setPending(() => next)
    }
    // 下書きが変わったときだけ（pending 自体の変化では回さない）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft])

  const real = useMemo(() => realDraft(draft, pending), [draft, pending])
  const unlinked = useMemo(() => unlinkedKeys(real), [real])
  const ghostOps = useMemo(() => ghostOpsOf(items), [items])
  const model = useMemo(() => buildCanvas(draft, pending, ghostOps), [draft, pending, ghostOps])

  const say = (kind: 'note' | 'error', text: string) =>
    setItems((l) => [...l, { kind, id: nextItemId(), text }])
  const errText = (e: unknown): string =>
    e instanceof ThemesApiError ? e.detail || e.message : e instanceof Error ? e.message : String(e)

  // ---- 適用（AI の提案も人の直接編集も、同じ口） ----------------------------------------------

  async function applyOps(
    ops: GraphOp[],
    proposalId: string,
    by: 'ai' | 'human',
    said: string[],
  ): Promise<GraphApplyResult> {
    const saved = await p.persist(draftRef.current)
    if (!saved.id) throw new Error(t('themes:place.noDraft'))
    const res = await applyGraph({ context: { draft_id: saved.id }, ops, proposalId, draftedBy: by, messages: said })
    if (!res.draft) throw new ThemesApiError(t('themes:read.badShape'), 502, '')
    const next = res.draft
    draftRef.current = next
    p.setDraft(next)
    const gone = new Set(res.dropped.map((d) => d.index))
    // もう当たっている再送（duplicate）は何も変えない — 薄い線は薄いまま
    const applied = res.duplicate ? [] : ops.filter((_, i) => !gone.has(i))
    if (applied.length > 0) p.setPending((q) => settlePending(q, applied, next))
    return res
  }

  const patch = (id: string, fn: (it: ProposalItem) => ProposalItem) =>
    setItems((l) => l.map((it) => (it.kind === 'proposal' && it.id === id ? fn(it) : it)))

  // ---- 人の一言 → 提案 --------------------------------------------------------------------------

  async function send(text: string) {
    if (busy) return
    setActionErr('')
    const history = [...historyOf(items), { role: 'user' as const, content: text }]
    setItems((l) => [...l, { kind: 'user', id: nextItemId(), text }])
    unsaid.current.push(text)
    setThinking(true)
    try {
      const saved = await p.persist(draftRef.current)
      if (!saved.id) throw new Error(t('themes:place.noDraft'))
      const prop = await proposeGraph('theme', { draft_id: saved.id }, history, lang, getActiveCredentials())
      if (prop.ops.length > 0) setOpen(true) // 新しい提案カードが来たら、シートも開く
      setItems((l) => [
        ...l,
        ...(prop.reply ? [{ kind: 'ai' as const, id: nextItemId(), text: prop.reply }] : []),
        ...(prop.ops.length > 0
          ? [newProposalItem({ proposalId: prop.proposal_id, ops: prop.ops, sentences: prop.sentences, by: 'ai' })]
          : []),
      ])
    } catch (e) {
      if (e instanceof ThemesApiError && e.status === 503) setAi('unconfigured')
      else say('error', t('themes:chat.failed', { detail: errText(e) }))
    } finally {
      setThinking(false)
    }
  }

  // ---- 提案カードの返事 ----------------------------------------------------------------------------

  async function proposalYes(item: ProposalItem, rows: number[]) {
    if (rows.length === 0 || busy) return
    setActionErr('')
    patch(item.id, (it) => ({ ...it, busy: true }))
    setApplying(true)
    try {
      const said = [...unsaid.current]
      const res = await applyOps(
        rows.map((i) => item.ops[i]),
        item.proposalId,
        'ai',
        said,
      )
      unsaid.current = unsaid.current.slice(said.length)
      const drops = dropsByOriginal(res.dropped, rows)
      if (res.duplicate) for (const i of rows) if (!drops.has(i)) drops.set(i, { index: -1, op: '', reason: 'duplicate', ref: '' })
      patch(item.id, (it) => ({
        ...it,
        busy: false,
        decided: it.decided.map((d, i) => (rows.includes(i) ? (drops.has(i) ? 'dropped' : 'yes') : d)),
        notes: it.notes.map((n, i) => (drops.has(i) ? dropReason(drops.get(i)!.reason) : n)),
      }))
    } catch (e) {
      patch(item.id, (it) => ({ ...it, busy: false }))
      if (e instanceof ThemesApiError && e.status === 503) setAi('unconfigured')
      setActionErr(errText(e))
    } finally {
      setApplying(false)
    }
  }

  async function proposalNo(item: ProposalItem, rows: number[]) {
    if (rows.length === 0 || busy) return
    setActionErr('')
    patch(item.id, (it) => ({ ...it, busy: true }))
    try {
      await dismissGraph(
        { draft_id: draftRef.current.id },
        rows.map((i) => item.ops[i]),
      )
      patch(item.id, (it) => ({
        ...it,
        busy: false,
        decided: it.decided.map((d, i) => (rows.includes(i) ? 'no' : d)),
      }))
    } catch (e) {
      patch(item.id, (it) => ({ ...it, busy: false }))
      setActionErr(errText(e))
    }
  }

  // ---- 人の直接編集（同じ形の差分で適用） -------------------------------------------------------------

  /**
   * `rollback`: あとの段が失敗したとき、すでに当たった段を戻す差分（線の直しで、足した線を外す —
   * 二重の線を残さない）。
   */
  async function applyHuman(steps: GraphOp[][], rollback?: GraphOp[]): Promise<boolean> {
    if (busy || steps.length === 0) return false
    setActionErr('')
    setApplying(true)
    const done: GraphOp[] = []
    let failed = false
    try {
      for (const ops of steps) {
        const res = await applyOps(ops, newProposalId(), 'human', [])
        if (res.dropped.length > 0) {
          setActionErr(t(`themes:chat.dropped.${dropReason(res.dropped[0].reason)}`))
          failed = true
          break
        }
        done.push(...ops)
      }
    } catch (e) {
      failed = true
      if (e instanceof ThemesApiError && e.status === 404) setActionErr(t('themes:chat.noDraft'))
      else setActionErr(errText(e))
    }
    if (failed && rollback && done.length > 0) {
      try {
        await applyOps(rollback, newProposalId(), 'human', [])
        done.length = 0 // 足した線は戻した — 記録に残す直しは無い
      } catch {
        /* 戻せなかったときは、足した線が残っていることを記録のカードで見せる */
      }
    }
    setApplying(false)
    if (done.length === 0) return false
    const after = draftRef.current
    setItems((l) => [
      ...l,
      newProposalItem({
        proposalId: 'human',
        ops: done,
        sentences: done.map((op) => opSentence(after, op, lang, tf)),
        by: 'human',
        decided: 'yes',
      }),
    ])
    return !failed
  }

  /** 関係の A・関係・B を直す（B だけなら retarget）。ドラッグでも同じ。 */
  async function editRelation(index: number, next: { from: string; rel: RelKind; to: string }) {
    const old = draftRef.current.relations[index]
    if (!old) return
    const steps = relationEditSteps(old, next)
    // 足してから外す 2 段のとき、外すが失敗したら足した線を戻す
    const ok = await applyHuman(steps, steps.length > 1 ? [removeRelationOp(next)] : undefined)
    if (ok) {
      setEdit(null)
      setSelectedEdge(draftRef.current.relations.some((r) => relId(r) === relId(next)) ? relId(next) : null)
    }
  }

  async function addHand() {
    const name = hand.name.trim()
    if (!name || hasConceptNamed(draftRef.current.concepts, name) || busy) return
    const aliases = hand.aliases
      .split(/[,、]/)
      .map((s) => s.trim())
      .filter(Boolean)
    const key = nextConceptKey(draftRef.current.concepts.map((c) => c.key))
    const ok = await applyHuman([
      [addConceptOp({ key, label_ja: name, label_en: hand.en.trim(), aliases, kind: hand.kind })],
    ])
    if (ok) setHand((h) => ({ ...h, name: '', en: '', aliases: '' }))
  }

  // ---- 外す（下書きだけ変える。AI の新しい概念・関係は次の下書きで出さない） -----------------------------

  async function dismissSafely(subject: string, term: string, relation?: string) {
    try {
      await dismiss(subject, term, relation)
    } catch (e) {
      // 却下を保存できなくても、下書きからは外す（次の下書きでまた出ることがあるだけ）
      setActionErr(errText(e))
    }
  }

  async function removeConceptRow(key: string) {
    const d = draftRef.current
    const row = d.concepts.find((r) => r.key === key)
    if (!row) return
    setActionErr('')
    setSelectedKey(null)
    p.setDraft((x) => removeConcept(x, key))
    if (row.by === 'ai' && row.use === 'new' && row.match === null) {
      await dismissSafely(conceptDismissKey(row.label_ja), 'concept')
    }
  }

  async function removeRelationRow(index: number) {
    const d = draftRef.current
    const rel = d.relations[index]
    if (!rel) return
    setActionErr('')
    setSelectedEdge(null)
    p.setDraft((x) => removeRelation(x, index))
    const from = d.concepts.find((r) => r.key === rel.from)
    if (rel.by === 'ai' && from) {
      await dismissSafely(
        dismissEndKey(from, rel.from, d.standards),
        dismissEndKey(
          d.concepts.find((r) => r.key === rel.to),
          rel.to,
          d.standards,
        ),
        relationPredicate(rel.rel, from.kind),
      )
    }
  }

  // ---- 会話の中身 ------------------------------------------------------------------------------------

  const draftCardConcepts = pending.concepts.flatMap((k) => {
    const c = draft.concepts.find((r) => r.key === k)
    return c ? [{ key: k, name: conceptName(c, lang), kind: c.kind }] : []
  })
  const draftCardRelations = pending.relations.flatMap((id) => {
    const index = draft.relations.findIndex((r) => relId(r) === id)
    const r = draft.relations[index]
    return r ? [{ id, index, sentence: sentenceOfRel(r), reason: r.reason }] : []
  })

  const bubbles = items.map((it) => {
    if (it.kind === 'user') {
      return (
        <div key={it.id} className="consult-msg consult-msg--user">
          <div className="consult-bubble">{it.text}</div>
        </div>
      )
    }
    if (it.kind === 'ai') {
      return (
        <div key={it.id} className="consult-msg consult-msg--assistant">
          <div className="consult-bubble thm-bubble-ai">{it.text}</div>
        </div>
      )
    }
    if (it.kind === 'note' || it.kind === 'error') {
      return (
        <div key={it.id} className="consult-msg consult-msg--assistant">
          <div className={`consult-bubble${it.kind === 'error' ? ' consult-bubble--error' : ' thm-bubble-ai'}`} role={it.kind === 'error' ? 'alert' : undefined}>
            {it.text}
          </div>
        </div>
      )
    }
    if (it.kind === 'proposal') {
      return (
        <ProposalCard
          key={it.id}
          item={it}
          disabled={busy}
          onYes={() => void proposalYes(it, openRows(it))}
          onNo={() => void proposalNo(it, openRows(it))}
          onPer={() => patch(it.id, (x) => ({ ...x, per: true }))}
          onRowYes={(i) => void proposalYes(it, withDependencies(it.ops, i, it.decided))}
          onRowNo={(i) => void proposalNo(it, [i])}
        />
      )
    }
    return null
  })

  const history: ReactNode[] = []
  if (p.firstDraft) {
    history.push(
      <DraftCard
        key="draft"
        conceptNames={draftCardConcepts}
        relationSentences={draftCardRelations}
        per={draftPer}
        total={p.firstDraft}
        left={pendingTotal(pending)}
        onYesAll={() => p.setPending(() => ({ concepts: [], relations: [] }))}
        onPer={() => setDraftPer(true)}
        onRowYes={(row) =>
          p.setPending((q) =>
            row.type === 'concept' ? acceptConcepts(q, [row.key]) : acceptRelations(q, [row.id], draftRef.current),
          )
        }
        onRowNo={(row) => (row.type === 'concept' ? void removeConceptRow(row.key) : void removeRelationRow(row.index))}
      />,
    )
  }
  history.push(...bubbles)

  // ---- 選んだ札・線の欄 -------------------------------------------------------------------------------

  const edgeIndex = selectedEdge !== null ? draft.relations.findIndex((r) => relId(r) === selectedEdge) : -1
  const edgeRow = edgeIndex >= 0 ? draft.relations[edgeIndex] : undefined
  const keyRow = selectedKey ? draft.concepts.find((c) => c.key === selectedKey) : undefined
  let focus: ReactNode = null
  if (edgeRow) {
    const id = relId(edgeRow)
    const cur = edit && edit.id === id ? edit : { id, from: edgeRow.from, rel: edgeRow.rel, to: edgeRow.to }
    const changed = relId(cur) !== id
    const stds = Object.keys(draft.standards)
    focus = (
      <div className="thm-focus" data-testid="focus-edge">
        <p className="thm-focus-sentence">{sentenceOfRel(edgeRow)}</p>
        <div className="thm-focus-edit">
          <label>
            <span>{t('themes:chat.edgeA')}</span>
            <select
              value={cur.from}
              aria-label={t('themes:chat.edgeA')}
              data-testid="edge-from"
              onChange={(e) => setEdit({ ...cur, from: e.target.value })}
            >
              {draft.concepts.map((c) => (
                <option key={c.key} value={c.key}>
                  {conceptName(c, lang)}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>{t('themes:chat.edgeRel')}</span>
            <select
              value={cur.rel}
              aria-label={t('themes:chat.edgeRel')}
              data-testid="edge-rel"
              onChange={(e) => setEdit({ ...cur, rel: e.target.value as RelKind })}
            >
              {REL_KINDS.map((r) => (
                <option key={r} value={r}>
                  {t(`themes:map.pillTitle.${r}`)}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>{t('themes:chat.edgeB')}</span>
            <select
              value={cur.to}
              aria-label={t('themes:chat.edgeB')}
              data-testid="edge-to"
              onChange={(e) => setEdit({ ...cur, to: e.target.value })}
            >
              {draft.concepts.map((c) => (
                <option key={c.key} value={c.key}>
                  {conceptName(c, lang)}
                </option>
              ))}
              {stds.map((k) => (
                <option key={k} value={k}>
                  {nameOf(k)}
                </option>
              ))}
              {!draft.concepts.some((c) => c.key === cur.to) && !stds.includes(cur.to) && (
                <option value={cur.to}>{nameOf(cur.to)}</option>
              )}
            </select>
          </label>
        </div>
        <div className="thm-focus-actions">
          <button
            type="button"
            className="btn btn--sm"
            disabled={!changed || busy || cur.from === cur.to}
            data-testid="edge-apply"
            onClick={() => void editRelation(edgeIndex, cur)}
          >
            {t('themes:chat.edgeApply')}
          </button>
          <button type="button" className="btn btn--ghost btn--sm" disabled={busy} onClick={() => void removeRelationRow(edgeIndex)}>
            {t('themes:chat.edgeRemove')}
          </button>
          <span className="vocab-field-hint">{t('themes:chat.edgeDragHint')}</span>
        </div>
      </div>
    )
  } else if (keyRow) {
    const touching = draft.relations.flatMap((r, i) => (r.from === keyRow.key || r.to === keyRow.key ? [{ r, i }] : []))
    focus = (
      <div className="thm-focus" data-testid="focus-concept">
        <p className="thm-focus-name">
          {conceptName(keyRow, lang)}
          <span className="thm-card-p-kind"> {t(`themes:kind.${keyRow.kind}`)}</span>
        </p>
        {touching.length === 0 ? (
          <p className="vocab-field-hint">{t('themes:chat.noRelations')}</p>
        ) : (
          <ul className="thm-focus-rels">
            {touching.map(({ r, i }) => (
              <li key={`${i}-${relId(r)}`}>
                <button type="button" className="link-btn" onClick={() => { setSelectedKey(null); setSelectedEdge(relId(r)) }}>
                  {sentenceOfRel(r)}
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className="thm-focus-actions">
          <button
            type="button"
            className="btn btn--sm"
            disabled={!aiReady || busy}
            title={aiReady ? undefined : t('themes:map.expandOff')}
            data-testid="focus-expand"
            onClick={() => void send(expandMessage(conceptName(keyRow, lang), lang))}
          >
            {t('themes:map.expand')}
          </button>
          <button type="button" className="btn btn--ghost btn--sm" disabled={busy} onClick={() => void removeConceptRow(keyRow.key)}>
            {t('themes:map.remove')}
          </button>
        </div>
      </div>
    )
  }

  // ---- 文の一覧・手で足す -------------------------------------------------------------------------------

  const tools = (
    <>
      <details className="thm-chat-more">
        <summary>{t('themes:review.sentences', { n: draft.relations.length })}</summary>
        {draft.relations.length === 0 ? (
          <p className="ds-empty-note">{t('themes:review.noSentences')}</p>
        ) : (
          <ul className="thm-sentences" data-testid="theme-sentences">
            {draft.relations.map((r, i) => {
              const lit = (!!selectedKey && (r.from === selectedKey || r.to === selectedKey)) || selectedEdge === relId(r)
              return (
                <li
                  key={`${i}-${relId(r)}`}
                  className={`thm-sentence${lit ? ' is-lit' : ''}${r.problem ? ' is-bad' : ''}`}
                  onMouseEnter={() => setLitRel(i)}
                  onMouseLeave={() => setLitRel(null)}
                >
                  <button
                    type="button"
                    className="thm-sentence-text"
                    onClick={() => {
                      setSelectedKey(null)
                      setSelectedEdge(selectedEdge === relId(r) ? null : relId(r))
                    }}
                  >
                    {sentenceOfRel(r)}
                  </button>
                  <button
                    type="button"
                    className="thm-x"
                    aria-label={t('themes:review.dismissAria', { sentence: sentenceOfRel(r) })}
                    title={t('themes:review.dismiss')}
                    onClick={() => void removeRelationRow(i)}
                  >
                    ✕
                  </button>
                  {r.problem && <span className="promote-err thm-sentence-note">{t(`themes:review.problem.${r.problem}`)}</span>}
                  {r.reason && <span className="thm-reason">{r.reason}</span>}
                </li>
              )
            })}
          </ul>
        )}
      </details>
      <details className="thm-chat-more">
        <summary>{t('themes:review.add')}</summary>
        <div className="thm-hand-row">
          <input
            type="text"
            aria-label={t('themes:review.addName')}
            placeholder={t('themes:review.addNamePlaceholder')}
            value={hand.name}
            onChange={(e) => setHand((h) => ({ ...h, name: e.target.value }))}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
                e.preventDefault()
                void addHand()
              }
            }}
          />
          <select
            aria-label={t('themes:review.addKind')}
            value={hand.kind}
            onChange={(e) => setHand((h) => ({ ...h, kind: e.target.value as TermKind }))}
          >
            <option value="class">{t('themes:kind.class')}</option>
            <option value="property">{t('themes:kind.property')}</option>
          </select>
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            disabled={busy || !hand.name.trim() || hasConceptNamed(draft.concepts, hand.name)}
            onClick={() => void addHand()}
          >
            {t('themes:review.addGo')}
          </button>
        </div>
        <div className="thm-hand-row">
          <input
            type="text"
            aria-label={t('themes:review.addEn')}
            placeholder={t('themes:review.addEn')}
            value={hand.en}
            onChange={(e) => setHand((h) => ({ ...h, en: e.target.value }))}
          />
          <input
            type="text"
            aria-label={t('themes:review.addAliases')}
            placeholder={t('themes:review.addAliasesPlaceholder')}
            value={hand.aliases}
            onChange={(e) => setHand((h) => ({ ...h, aliases: e.target.value }))}
          />
        </div>
      </details>
    </>
  )

  const thinCount = pendingTotal(pending)

  return (
    <div className={`thm-ws${open ? '' : ' is-folded'}${narrow && focus ? ' has-band' : ''}`} data-testid="theme-workspace">
      <div className="thm-ws-top">{p.top}</div>
      <div className="thm-ws-fig">
        <GraphCanvas
          model={model}
          standards={draft.standards}
          lang={lang}
          selectedKey={selectedKey}
          selectedEdge={selectedEdge}
          litRelation={litRel}
          unlinked={unlinked}
          onSelect={(k) => {
            setSelectedKey(k)
            if (k && !narrow) setOpen(true) // 375px ではシートを開かない（欄は図の下の帯）
          }}
          onSelectEdge={(id) => {
            setSelectedEdge(id)
            if (id !== null && !narrow) setOpen(true)
          }}
          onRetarget={(i, to) => void editRelation(i, { from: draft.relations[i].from, rel: draft.relations[i].rel, to })}
        />
        {narrow && focus && (
          <div className="thm-ws-band" data-testid="focus-band">
            {focus}
          </div>
        )}
        {!open && (
          <button type="button" className="btn thm-ws-open" onClick={() => setOpen(true)} data-testid="chat-open">
            {t('themes:chat.open')}
            {thinCount > 0 && <span className="thm-ws-badge">{t('themes:chat.openBadge', { n: thinCount })}</span>}
          </button>
        )}
      </div>
      {open && (
        <GraphChat
          focus={narrow ? null : focus}
          tools={tools}
          aiReady={aiReady}
          thinking={thinking}
          locked={applying}
          onSend={(text) => void send(text)}
          onOpenSettings={() => openSettings('ai')}
          onFold={() => setOpen(false)}
        >
          {history}
        </GraphChat>
      )}
      <div className="thm-ws-bottom">
        {(actionErr || p.notice) && (
          <p className="promote-err" role="alert">
            {actionErr || p.notice}
          </p>
        )}
        {p.bottom}
      </div>
    </div>
  )
}
