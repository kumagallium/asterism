import { type ReactNode, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useLlmSettings } from '../settings/context'
import { type SharedTerm, listTerms } from '../vocabApi'
import {
  type CommitResult,
  ThemesApiError,
  commitTheme,
  createDraft,
  deleteDraft,
  describeSelections,
  draftWithAi,
  getDraft,
  saveDraft,
} from './themesApi'
import {
  type RelationKind,
  type RelationRow,
  type SelectionRow,
  type Step,
  type TermKind,
  type TermRow,
  type ThemeDraft,
  FITTING_OPS,
  SELECTION_OPS,
  acceptedRelations,
  addGroup,
  addQuestion,
  answeringNow,
  applyDraftResponse,
  buildCommitBody,
  canAdvance,
  chooseUse,
  draftGroups,
  draftStandards,
  draftSuggestions,
  idList,
  implicitRelations,
  isMemo,
  kindOptional,
  memoReason,
  needsKind,
  needsProperty,
  needsValue,
  newTermRow,
  nextStep,
  parseQuestionLines,
  placeBlocker,
  placeSummary,
  prevStep,
  relationApplies,
  removeRow,
  renameRow,
  rowProblems,
  selectionPayload,
  setRelation,
  setSelection,
  startManual,
  suggestRowSlug,
  syncSelections,
  termMints,
  upperStructure,
  termUseOptions,
  validateDraftResponse,
  visibleGroups,
} from './themeDraft'
import './themes.css'

// 「知りたいことから作る」6 段（モック index.html の openWiz / renderWiz）:
//   ① 知りたいことを書く → ② AI に読ませる → ③ 用語をたしかめる → ④ 関係をたしかめる
//   → ⑤ ためす → ⑥ 置く
// 手順バーは かんたんの RecipeCard と同じ見た目（.kz-recipe）。1 問 1 画面にはしない（③は用語の
// 表、⑤は問いの一覧が 1 枚に並ぶ）。途中で閉じても、下書きが残って続きから開ける。
// AI は閉じた JSON だけ（api が検証・機械の後処理まで済ませて返す）。未設定なら「手で書く」へ。

const STEP_KEYS = ['write', 'read', 'terms', 'relations', 'try', 'place'] as const
const CIRCLED = ['①', '②', '③', '④', '⑤', '⑥']

function blankDraft(label: string, extend: string | undefined, question: string): ThemeDraft {
  return {
    id: '',
    label,
    step: 1,
    ...(extend ? { extend } : {}),
    questions: question ? [question] : [],
    ai: null,
    terms: [],
    relations: [],
    selections: [],
    updated_at: '',
  }
}

export function ThemeWizard({
  initialQuestion = '',
  resumeId,
  extend,
  onClose,
  onPlaced,
}: {
  /** `#/questions?new=1&q=<題>`・質問するから持ち込まれた題（①の最初の 1 行）。 */
  initialQuestion?: string
  /** 続きの下書きの id。 */
  resumeId?: string
  /** 「問いを足す」: 置くと、このテーマに問いが加わる。 */
  extend?: { id: string; label: string }
  onClose: () => void
  onPlaced: (result: CommitResult) => void
}) {
  const { t, i18n } = useTranslation()
  const lang = i18n.language.startsWith('en') ? 'en' : 'ja'
  const { isReady, getActiveCredentials, openSettings } = useLlmSettings()

  const [draft, setDraft] = useState<ThemeDraft>(() =>
    blankDraft(extend?.label ?? '', extend?.id, initialQuestion),
  )
  // ①の入力欄（1 行 1 問）。段を進めるときに draft.questions へ整える。
  const [qText, setQText] = useState(initialQuestion)
  const [loaded, setLoaded] = useState(!resumeId)
  const [loadErr, setLoadErr] = useState('')
  const [existing, setExisting] = useState<SharedTerm[]>([])
  const [ai, setAi] = useState<{ status: 'idle' | 'running' | 'ok' | 'unconfigured' | 'failed'; detail?: string }>({
    status: 'idle',
  })
  const [saveErr, setSaveErr] = useState('')
  const [sentences, setSentences] = useState<string[]>([])
  const [placing, setPlacing] = useState(false)
  const [placeErr, setPlaceErr] = useState('')
  // ③の別名欄は打っている途中の文字列を持つ（カンマの途中で整えてしまわないため）。
  const [aliasText, setAliasText] = useState<Record<string, string>>({})
  const [relForm, setRelForm] = useState({ subject: '', relation: 'sub' as RelationKind, object: '' })
  const [groupName, setGroupName] = useState<Record<string, string>>({})

  useEffect(() => {
    if (!resumeId) return
    let off = false
    getDraft(resumeId)
      .then((d) => {
        if (off) return
        setDraft(d)
        setQText(d.questions.join('\n'))
        setAliasText(Object.fromEntries(d.terms.map((r) => [r.key, r.aliases.join(', ')])))
        setLoaded(true)
      })
      .catch((e) => {
        if (off) return
        setLoadErr(e instanceof Error ? e.message : String(e))
        setLoaded(true)
      })
    return () => {
      off = true
    }
  }, [resumeId])

  useEffect(() => {
    let off = false
    listTerms()
      .then((ts) => !off && setExisting(ts))
      .catch(() => !off && setExisting([]))
    return () => {
      off = true
    }
  }, [])

  const existingSlugs = useMemo(() => new Set(existing.map((e) => e.slug)), [existing])
  const answering = useMemo(
    () => Object.fromEntries(existing.map((e) => [e.slug, e.answering_datasets])),
    [existing],
  )
  const ctx = useMemo(() => ({ existingSlugs }), [existingSlugs])
  const step = draft.step
  const rows = draft.terms
  const standards = draftStandards(draft)

  // ⑤⑥: 実行文は機械が組む（api）。選択か用語の名前が変わるたび組み直す。
  useEffect(() => {
    if (step < 5) return
    let off = false
    describeSelections(
      draft.selections.map((s) => selectionPayload(s, draft.terms, lang)),
      lang,
    )
      .then((s) => !off && setSentences(s))
      .catch(() => !off && setSentences([]))
    return () => {
      off = true
    }
  }, [step, draft.selections, draft.terms, lang])

  const problems = rowProblems(rows, existingSlugs)
  const problemCount = Object.keys(problems).length

  /** 下書きを保存する（まだ id が無ければ作る）。失敗しても進める — 置くときに id が要る。 */
  async function persist(next: ThemeDraft): Promise<ThemeDraft> {
    setSaveErr('')
    try {
      if (!next.id) {
        const created = await createDraft(next.label, next.questions, next.extend)
        next = { ...next, id: created.id }
      }
      const saved = await saveDraft(next)
      next = { ...next, updated_at: saved.updated_at }
    } catch (e) {
      setSaveErr(e instanceof Error ? e.message : String(e))
    }
    setDraft(next)
    return next
  }

  async function go(to: Step, base: ThemeDraft = draft) {
    await persist({ ...base, step: to })
  }

  function questionsFromText(): string[] {
    return parseQuestionLines(qText)
  }

  async function runAi(base: ThemeDraft) {
    if (!isReady) {
      setAi({ status: 'unconfigured' })
      return
    }
    setAi({ status: 'running' })
    try {
      const raw = await draftWithAi(base.questions, lang, getActiveCredentials())
      const res = validateDraftResponse(raw)
      if (!res) {
        setAi({ status: 'failed', detail: t('themes:read.badShape') })
        return
      }
      const applied = applyDraftResponse(base, res)
      setAliasText(Object.fromEntries(applied.terms.map((r) => [r.key, r.aliases.join(', ')])))
      setAi({ status: 'ok' })
      await persist({ ...applied, step: 2 })
    } catch (e) {
      if (e instanceof ThemesApiError && e.status === 503) setAi({ status: 'unconfigured' })
      else setAi({ status: 'failed', detail: e instanceof Error ? e.message : String(e) })
    }
  }

  // ① → ②（AI に読ませる）／③（手で書く）。すでに読ませた下書きなら、そのまま見せる
  // （読み直しは②のボタン）。手で書くときも、ここまでの用語・関係は消さない。
  async function fromWrite(useAi: boolean) {
    const questions = questionsFromText()
    const base: ThemeDraft = {
      ...draft,
      questions,
      selections: draft.selections.length > 0 ? syncSelections(draft.selections, questions) : [],
    }
    if (!useAi) {
      setAliasText({})
      await go(3, base.selections.length > 0 ? base : startManual(base))
      return
    }
    const saved = await persist({ ...base, step: 2 })
    if (saved.ai === null && saved.selections.length === 0) void runAi(saved)
  }

  function updateRow(key: string, patch: Partial<TermRow>) {
    setDraft((d) => ({ ...d, terms: renameRow(d.terms, key, patch) }))
  }

  function addRow(kind: TermKind) {
    setDraft((d) => ({ ...d, terms: [...d.terms, newTermRow(d.terms, kind)] }))
  }

  function setLabelEn(row: TermRow, value: string) {
    // ID が空か、前の英語名からの既定のままなら、新しい英語名に合わせて作り直す。
    const others = rows.filter((r) => r.key !== row.key).map((r) => r.slug)
    const wasAuto = row.slug === '' || row.slug === suggestRowSlug(row, others)
    const patch: Partial<TermRow> = { label_en: value }
    if (wasAuto && termMints(row)) patch.slug = suggestRowSlug({ ...row, label_en: value }, [...others, ...existingSlugs])
    updateRow(row.key, patch)
  }

  function setAliases(key: string, text: string) {
    setAliasText((a) => ({ ...a, [key]: text }))
    updateRow(
      key,
      { aliases: text.split(/[,、]/).map((s) => s.trim()).filter(Boolean) },
    )
  }

  function addRelation() {
    const { subject, relation, object } = relForm
    if (!subject || !object || subject === object) return
    setDraft((d) => ({
      ...d,
      relations: [
        ...d.relations,
        { subject, object, relation, problem: null, by: 'human', ok: true } satisfies RelationRow,
      ],
    }))
    setRelForm({ subject: '', relation: 'sub', object: '' })
  }

  function updateSelection(index: number, patch: Partial<SelectionRow>) {
    setDraft((d) => ({
      ...d,
      selections: d.selections.map((s, i) => (i === index ? setSelection(s, patch) : s)),
    }))
  }

  async function place() {
    if (placing) return
    setPlacing(true)
    setPlaceErr('')
    try {
      const saved = draft.id ? draft : await persist(draft)
      if (!saved.id) {
        setPlaceErr(saveErr || t('themes:place.noDraft'))
        return
      }
      const result = await commitTheme(saved.id, buildCommitBody(draft, lang))
      onPlaced(result)
    } catch (e) {
      setPlaceErr(e instanceof ThemesApiError ? e.detail || e.message : e instanceof Error ? e.message : String(e))
    } finally {
      setPlacing(false)
    }
  }

  async function close() {
    const questions = questionsFromText()
    if (draft.id) await persist(step === 1 ? { ...draft, questions } : draft)
    else if (draft.label.trim() && questions.length > 0) await persist({ ...draft, questions })
    onClose()
  }

  async function discard() {
    if (!window.confirm(t('themes:wizard.discardConfirm'))) return
    if (draft.id) {
      try {
        await deleteDraft(draft.id)
      } catch {
        /* 消せなくても閉じる（一覧に残る下書きから、もう一度捨てられる） */
      }
    }
    onClose()
  }

  if (!loaded) {
    return (
      <section className="thm-wizard" aria-busy="true">
        <p className="loading-row">
          <span className="spinner" />
          {t('themes:loading')}
        </p>
      </section>
    )
  }
  if (loadErr) {
    return (
      <section className="thm-wizard">
        <p className="error">{loadErr}</p>
        <button type="button" className="btn btn--ghost btn--sm" onClick={onClose}>
          {t('themes:wizard.close')}
        </button>
      </section>
    )
  }

  // ---- 部品 --------------------------------------------------------------------

  const nameOf = (ref: string): string => {
    const row = rows.find((r) => r.key === ref)
    if (row) return lang === 'en' ? row.label_en || row.label_ja : row.label_ja
    return ref
  }

  function relationSentence(rel: { subject: string; object: string; relation: RelationKind }): string {
    const std = standards[rel.object]
    const object = rows.some((r) => r.key === rel.object)
      ? nameOf(rel.object)
      : std
        ? `${rel.object}（${std.label}）`
        : rel.object
    return t(`themes:rel.sentence.${rel.relation}`, { subject: nameOf(rel.subject), object })
  }

  const nav = (opts: {
    prev?: () => void
    next?: { label: string; onClick: () => void; disabled?: boolean }
  }) => (
    <div className="kz-actions thm-nav">
      {opts.prev && (
        <button type="button" className="btn btn--ghost" onClick={opts.prev}>
          {t('themes:wizard.back')}
        </button>
      )}
      {opts.next && (
        <button
          type="button"
          className="btn btn--accent"
          disabled={opts.next.disabled}
          onClick={opts.next.onClick}
        >
          {opts.next.label}
        </button>
      )}
    </div>
  )

  const goBack = () => void go(prevStep(step))
  const goNext = () => void go(nextStep(step))

  // ---- 各段 --------------------------------------------------------------------

  let body: ReactNode

  if (step === 1) {
    const suggestions = draftSuggestions(draft)
    const count = questionsFromText().length
    body = (
      <>
        <h2 className="thm-h">
          {CIRCLED[0]} {t('themes:steps.write')}
        </h2>
        <p className="thm-lead">{t('themes:write.lead')}</p>
        <label className="vocab-field">
          <span>{t('themes:write.themeName')}</span>
          <input
            type="text"
            value={draft.label}
            readOnly={!!extend}
            placeholder={t('themes:write.themeNamePlaceholder')}
            onChange={(e) => setDraft((d) => ({ ...d, label: e.target.value }))}
          />
        </label>
        {extend && <p className="vocab-field-hint">{t('themes:write.extendNote', { label: extend.label })}</p>}
        <label className="vocab-field">
          <span>{t('themes:write.questions')}</span>
          <textarea
            className="thm-questions"
            rows={5}
            value={qText}
            placeholder={t('themes:write.questionsPlaceholder')}
            onChange={(e) => setQText(e.target.value)}
          />
        </label>
        {suggestions.length > 0 && (
          <div>
            <p className="vocab-field-hint">{t('themes:write.suggestNote')}</p>
            <div className="thm-chips">
              {suggestions
                .filter((q) => !questionsFromText().includes(q))
                .map((q) => (
                  <button
                    key={q}
                    type="button"
                    className="thm-chip"
                    onClick={() => setQText(addQuestion(questionsFromText(), q).join('\n'))}
                  >
                    ＋ {q}
                  </button>
                ))}
            </div>
          </div>
        )}
        {!isReady && <p className="vocab-field-hint">{t('themes:write.aiOff')}</p>}
        {saveErr && <p className="promote-err">{t('themes:wizard.saveErr', { detail: saveErr })}</p>}
        <div className="kz-actions thm-nav">
          <button
            type="button"
            className={`btn ${isReady ? 'btn--accent' : 'btn--ghost'}`}
            disabled={!draft.label.trim() || count === 0}
            onClick={() => void fromWrite(true)}
          >
            {t('themes:write.toRead')}
          </button>
          <button
            type="button"
            className={`btn ${isReady ? 'btn--ghost' : 'btn--accent'}`}
            disabled={!draft.label.trim() || count === 0}
            onClick={() => void fromWrite(false)}
          >
            {t('themes:write.manual')}
          </button>
        </div>
      </>
    )
  } else if (step === 2) {
    const summary = t('themes:read.done', {
      questions: draft.questions.length,
      terms: rows.length,
      relations: draft.relations.length,
    })
    body = (
      <>
        <h2 className="thm-h">
          {CIRCLED[1]} {t('themes:steps.read')}
        </h2>
        <p className="thm-lead">{t('themes:read.lead')}</p>
        {ai.status === 'running' && (
          <div className="thm-try" role="status" aria-live="polite">
            <p className="thm-q">
              <span className="spinner" /> {t('themes:read.running')}
            </p>
          </div>
        )}
        {ai.status !== 'running' && ai.status !== 'unconfigured' && ai.status !== 'failed' && draft.ai !== null && (
          <div className="thm-try" data-testid="theme-ai-done">
            <p className="thm-ans">{summary}</p>
          </div>
        )}
        {ai.status === 'unconfigured' && (
          <section className="kz-card kz-stop" role="alert" data-testid="theme-ai-unconfigured">
            <h3 className="kz-title">{t('themes:read.unconfigured.title')}</h3>
            <p className="kz-note">{t('themes:read.unconfigured.body')}</p>
            <div className="kz-actions">
              <button type="button" className="btn btn--accent" onClick={() => void go(3, startManual(draft))}>
                {t('themes:read.manual')}
              </button>
              <button type="button" className="btn btn--ghost" onClick={() => openSettings()}>
                {t('themes:read.openSettings')}
              </button>
            </div>
          </section>
        )}
        {ai.status === 'failed' && (
          <section className="kz-card kz-stop" role="alert" data-testid="theme-ai-failed">
            <h3 className="kz-title">{t('themes:read.failed.title')}</h3>
            <p className="kz-note">{t('themes:read.failed.body')}</p>
            <details className="kz-stop-detail">
              <summary>{t('themes:read.failed.detail')}</summary>
              <pre className="error">{ai.detail}</pre>
            </details>
            <div className="kz-actions">
              <button type="button" className="btn btn--accent" onClick={() => void runAi(draft)}>
                {t('themes:read.retry')}
              </button>
              <button type="button" className="btn btn--ghost" onClick={() => void go(3, startManual(draft))}>
                {t('themes:read.manual')}
              </button>
            </div>
          </section>
        )}
        <p className="vocab-field-hint">{t('themes:read.note')}</p>
        {nav({
          prev: goBack,
          next: { label: t('themes:read.next'), onClick: goNext, disabled: !canAdvance(draft, 2, ctx) || ai.status === 'running' },
        })}
        {ai.status === 'ok' && (
          <p className="vocab-field-hint">
            <button type="button" className="link-btn" onClick={() => void runAi(draft)}>
              {t('themes:read.again')}
            </button>
          </p>
        )}
        {ai.status === 'idle' && draft.ai !== null && (
          <p className="vocab-field-hint">
            <button type="button" className="link-btn" onClick={() => void runAi(draft)}>
              {t('themes:read.again')}
            </button>
          </p>
        )}
      </>
    )
  } else if (step === 3) {
    const newCount = rows.filter(termMints).length
    body = (
      <>
        <h2 className="thm-h">
          {CIRCLED[2]} {t('themes:steps.terms')}
        </h2>
        <p className="thm-lead">{t('themes:terms.lead')}</p>
        {rows.length === 0 ? (
          <p className="ds-empty-note">{t('themes:terms.empty')}</p>
        ) : (
          <div className="thm-table-wrap">
            <table className="thm-table" data-testid="theme-terms">
              <thead>
                <tr>
                  <th>{t('themes:terms.colJa')}</th>
                  <th>{t('themes:terms.colEn')}</th>
                  <th>{t('themes:terms.colKind')}</th>
                  <th>{t('themes:terms.colMatch')}</th>
                  <th>{t('themes:terms.colUse')}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.key} data-key={r.key} className={problems[r.key] === 'label' ? 'thm-row--bad' : ''}>
                    <td>
                      <input
                        type="text"
                        aria-label={t('themes:terms.colJa')}
                        value={r.label_ja}
                        onChange={(e) => updateRow(r.key, { label_ja: e.target.value })}
                      />
                      {r.by === 'ai' && <span className="thm-ai">{t('themes:aiMark')}</span>}
                    </td>
                    <td>
                      <input
                        type="text"
                        aria-label={t('themes:terms.colEn')}
                        value={r.label_en}
                        onChange={(e) => setLabelEn(r, e.target.value)}
                      />
                      <input
                        type="text"
                        className="thm-alias"
                        aria-label={t('themes:terms.aliases')}
                        placeholder={t('themes:terms.aliasesPlaceholder')}
                        value={aliasText[r.key] ?? r.aliases.join(', ')}
                        onChange={(e) => setAliases(r.key, e.target.value)}
                      />
                    </td>
                    <td>
                      {r.by === 'human' ? (
                        <select
                          aria-label={t('themes:terms.colKind')}
                          value={r.kind}
                          onChange={(e) => updateRow(r.key, { kind: e.target.value as TermKind })}
                        >
                          <option value="class">{t('themes:kind.class')}</option>
                          <option value="property">{t('themes:kind.property')}</option>
                        </select>
                      ) : (
                        t(`themes:kind.${r.kind}`)
                      )}
                    </td>
                    <td>
                      {r.match ? (
                        <>
                          <b>{r.match.kind === 'existing' ? r.match.label : r.match.ref}</b>
                          <br />
                          <span className="vocab-field-hint">
                            {t(`themes:terms.match.${r.match.kind}`)}
                            {r.match.matched_by === 'alias' ? t('themes:terms.match.byAlias') : ''}
                          </span>
                        </>
                      ) : (
                        <span className="vocab-field-hint">{t('themes:terms.none')}</span>
                      )}
                    </td>
                    <td>
                      <select
                        aria-label={t('themes:terms.colUse')}
                        value={r.use}
                        onChange={(e) =>
                          setDraft((d) => ({ ...d, terms: chooseUse(d.terms, r.key, e.target.value) }))
                        }
                      >
                        {termUseOptions(r).map((o) => (
                          <option key={o.value} value={o.value}>
                            {t(`themes:terms.use.${o.kind}`, { ref: r.match?.ref ?? '' })}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <button
                        type="button"
                        className="btn btn--ghost btn--sm"
                        aria-label={t('themes:terms.remove')}
                        title={t('themes:terms.remove')}
                        onClick={() => setDraft((d) => removeRow(d, r.key))}
                      >
                        ✕
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="kz-actions">
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => addRow('class')}>
            {t('themes:terms.addClass')}
          </button>
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => addRow('property')}>
            {t('themes:terms.addProperty')}
          </button>
        </div>
        <p className="vocab-field-hint">{t('themes:terms.summary', { n: newCount, total: rows.length })}</p>
        {nav({
          prev: goBack,
          next: { label: t('themes:terms.next'), onClick: goNext, disabled: !canAdvance(draft, 3, ctx) },
        })}
      </>
    )
  } else if (step === 4) {
    const structure = upperStructure(draft)
    const groups = visibleGroups(draftGroups(draft), rows)
    const subjects = rows.filter(termMints)
    const objects = rows
    const implicit = implicitRelations(rows)
    body = (
      <>
        <h2 className="thm-h">
          {CIRCLED[3]} {t('themes:steps.relations')}
        </h2>
        <p className="thm-lead">{t('themes:relations.lead')}</p>
        {draft.relations.length === 0 && implicit.length === 0 && (
          <p className="ds-empty-note">{t('themes:relations.empty')}</p>
        )}
        {implicit.map((r) => (
          <div className="thm-rel" key={`i-${r.subject}`}>
            <span>
              {relationSentence({ subject: r.subject, object: r.curie, relation: r.quantity ? 'quantity' : 'eq' })}
            </span>
            <span className="vocab-field-hint">{t('themes:relations.fromTerms')}</span>
          </div>
        ))}
        {draft.relations.map((r, i) => {
          const applies = relationApplies(r, rows)
          return (
            <div
              className={`thm-rel${applies ? '' : ' thm-rel--off'}`}
              key={`${i}-${r.subject}|${r.object}|${r.relation}`}
            >
              <span>{relationSentence(r)}</span>
              {r.by === 'ai' && <span className="thm-ai">{t('themes:aiMark')}</span>}
              {r.problem && <span className="promote-err">{t(`themes:relations.problem.${r.problem}`)}</span>}
              {!r.problem && !applies && (
                <span className="vocab-field-hint">{t('themes:relations.notPlaced')}</span>
              )}
              {applies && (
                <span className="thm-yn" role="group" aria-label={t('themes:relations.ask')}>
                  <button
                    type="button"
                    className={r.ok === true ? 'on' : ''}
                    aria-pressed={r.ok === true}
                    onClick={() => setDraft((d) => ({ ...d, relations: setRelation(d.relations, i, true) }))}
                  >
                    {t('themes:relations.yes')}
                  </button>
                  <button
                    type="button"
                    className={r.ok === false ? 'on' : ''}
                    aria-pressed={r.ok === false}
                    onClick={() => setDraft((d) => ({ ...d, relations: setRelation(d.relations, i, false) }))}
                  >
                    {t('themes:relations.no')}
                  </button>
                </span>
              )}
            </div>
          )
        })}
        {subjects.length > 0 && (
          <div className="thm-addrel">
            <span className="vocab-field-hint">{t('themes:relations.add')}</span>
            <select
              aria-label={t('themes:relations.addSubject')}
              value={relForm.subject}
              onChange={(e) => setRelForm((f) => ({ ...f, subject: e.target.value }))}
            >
              <option value="">{t('themes:pick')}</option>
              {subjects.map((r) => (
                <option key={r.key} value={r.key}>
                  {nameOf(r.key)}
                </option>
              ))}
            </select>
            <select
              aria-label={t('themes:relations.addKind')}
              value={relForm.relation}
              onChange={(e) => setRelForm((f) => ({ ...f, relation: e.target.value as RelationKind }))}
            >
              <option value="sub">{t('themes:relations.kindSub')}</option>
              <option value="eq">{t('themes:relations.kindEq')}</option>
            </select>
            <select
              aria-label={t('themes:relations.addObject')}
              value={relForm.object}
              onChange={(e) => setRelForm((f) => ({ ...f, object: e.target.value }))}
            >
              <option value="">{t('themes:pick')}</option>
              {objects
                .filter((r) => r.key !== relForm.subject)
                .map((r) => (
                  <option key={r.key} value={r.key}>
                    {nameOf(r.key)}
                  </option>
                ))}
            </select>
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              disabled={!relForm.subject || !relForm.object}
              onClick={addRelation}
            >
              {t('themes:relations.addGo')}
            </button>
          </div>
        )}
        {groups.length > 0 && (
          <div>
            <p className="vocab-field-hint">{t('themes:relations.groupNote')}</p>
            <div className="thm-chips">
              {groups.map((g) => (
                <span key={g.label_ja} className="thm-group">
                  <input
                    type="text"
                    aria-label={t('themes:relations.groupName')}
                    value={groupName[g.label_ja] ?? g.label_ja}
                    onChange={(e) => setGroupName((n) => ({ ...n, [g.label_ja]: e.target.value }))}
                  />
                  <button
                    type="button"
                    className="thm-chip"
                    onClick={() =>
                      setDraft((d) =>
                        addGroup(d, { ...g, label_ja: (groupName[g.label_ja] ?? g.label_ja).trim() || g.label_ja }),
                      )
                    }
                  >
                    {t('themes:relations.groupAdd', { names: g.children.map(nameOf).join('・') })}
                  </button>
                </span>
              ))}
            </div>
          </div>
        )}
        <div className="thm-effect" data-testid="theme-structure">
          <div>{t('themes:relations.structure')}</div>
          {structure.length === 0 ? (
            <div className="vocab-field-hint">{t('themes:relations.structureNone')}</div>
          ) : (
            structure.map((l) => (
              <div key={`${l.ref}-${l.depth}`} style={{ paddingLeft: `${l.depth * 1.2}rem` }}>
                {l.depth > 0 ? '└ ' : ''}
                {standards[l.ref] ? l.ref : l.label}
                {l.tag === 'existing' && <span className="vocab-field-hint">{t('themes:relations.tagExisting')}</span>}
                {l.notes.map((n) => (
                  <span key={`${n.relation}-${n.curie}`} className="vocab-field-hint">
                    {' '}
                    ─ {t(`themes:relations.note.${n.relation}`)} {n.curie}
                  </span>
                ))}
              </div>
            ))
          )}
          <div className="vocab-field-hint">{t('themes:relations.structureNote')}</div>
        </div>
        {nav({
          prev: goBack,
          next: { label: t('themes:relations.next'), onClick: goNext, disabled: !canAdvance(draft, 4, ctx) },
        })}
      </>
    )
  } else if (step === 5) {
    const classRows = rows.filter((r) => r.kind === 'class')
    const propRows = rows.filter((r) => r.kind === 'property')
    body = (
      <>
        <h2 className="thm-h">
          {CIRCLED[4]} {t('themes:steps.try')}
        </h2>
        <p className="thm-lead">{t('themes:try.lead')}</p>
        {draft.selections.map((s, i) => {
          const memo = isMemo(s)
          const sentence = sentences[i] ?? ''
          const n = answeringNow(s, rows, answering)
          return (
            <div className="thm-try" key={s.question} data-testid="theme-try">
              <div className="thm-q">{s.question}</div>
              <div className="thm-shape">
                <label>
                  {t('themes:try.op')}{' '}
                  <select
                    aria-label={t('themes:try.op')}
                    value={s.op}
                    onChange={(e) => updateSelection(i, { op: e.target.value as SelectionRow['op'] })}
                  >
                    <option value="">{t('themes:pick')}</option>
                    {SELECTION_OPS.map((o) => (
                      <option key={o} value={o}>
                        {t(`themes:try.opName.${o}`)}
                      </option>
                    ))}
                  </select>
                </label>
                {(needsKind(s.op) || kindOptional(s.op) || s.op === '') && (
                  <label>
                    {t('themes:try.kind')}{' '}
                    <select
                      aria-label={t('themes:try.kind')}
                      value={s.kind}
                      onChange={(e) => updateSelection(i, { kind: e.target.value })}
                    >
                      <option value="">{t('themes:pick')}</option>
                      {classRows.map((r) => (
                        <option key={r.key} value={r.key}>
                          {nameOf(r.key)}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                {(needsProperty(s.op) || s.op === '') && (
                  <label>
                    {t('themes:try.property')}{' '}
                    <select
                      aria-label={t('themes:try.property')}
                      value={s.property}
                      onChange={(e) => updateSelection(i, { property: e.target.value })}
                    >
                      <option value="">{t('themes:pick')}</option>
                      {propRows.map((r) => (
                        <option key={r.key} value={r.key}>
                          {nameOf(r.key)}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                {needsValue(s.op) && (
                  <label>
                    {t('themes:try.value')}{' '}
                    <input
                      type="text"
                      value={s.value}
                      onChange={(e) => updateSelection(i, { value: e.target.value })}
                    />
                  </label>
                )}
                {s.by === 'ai' && s.op !== '' && <span className="thm-ai">{t('themes:aiMark')}</span>}
              </div>
              <div className="thm-ans" data-testid="theme-exec">
                {sentence ? (
                  <>
                    {t('themes:try.exec', { sentence })}
                    <br />
                    {memo ? null : (
                      <span>{t('themes:try.answeringNow', { n })}</span>
                    )}
                  </>
                ) : (
                  <span className="vocab-field-hint">{t('themes:try.execNone')}</span>
                )}
              </div>
              {memo && (
                <div className="thm-memo" data-testid="theme-memo">
                  <span className="pill wait">{t('themes:try.memoBadge')}</span>{' '}
                  {t(`themes:try.memoReason.${memoReason(s)}`)}
                  {s.reason && s.memo === 'ai' ? ` ${s.reason}` : ''}
                </div>
              )}
              <label className="thm-memo-toggle">
                <input
                  type="checkbox"
                  checked={s.memo !== null}
                  onChange={(e) => updateSelection(i, { memo: e.target.checked ? 'human' : null })}
                />{' '}
                {t('themes:try.keepMemo')}
              </label>
            </div>
          )
        })}
        <p className="vocab-field-hint">{t('themes:try.note', { ops: FITTING_OPS.map((o) => t(`themes:try.opName.${o}`)).join('・') })}</p>
        {nav({
          prev: goBack,
          next: { label: t('themes:try.next'), onClick: goNext, disabled: !canAdvance(draft, 5, ctx) },
        })}
      </>
    )
  } else {
    const summary = placeSummary(draft)
    const ids = idList(rows)
    const accepted = acceptedRelations(draft.relations, rows)
    const implicit = implicitRelations(rows)
    const blocker = placeBlocker(draft, ctx)
    const today = new Date().toISOString().slice(0, 10)
    body = (
      <>
        <h2 className="thm-h">
          {CIRCLED[5]} {t('themes:steps.place')}
        </h2>
        <p className="thm-lead">
          {t(extend ? 'themes:place.leadExtend' : 'themes:place.lead', {
            label: draft.label,
            terms: summary.terms,
            lines: summary.lines,
            questions: summary.published,
            memo: summary.memo,
          })}
        </p>
        <div className="thm-effect" data-testid="theme-place-list">
          <div>{t('themes:place.listTitle')}</div>
          {ids.length > 0 && (
            <div className="thm-ids">
              {ids.map((l) => (
                <div key={l.key} className="thm-id">
                  <span>{t('themes:place.newTerm', { label: l.label })}</span>
                  <label className="thm-idfield">
                    <span className="vocab-field-hint">{t('themes:place.id')}</span>
                    <input
                      type="text"
                      aria-label={t('themes:place.idAria', { label: l.label })}
                      className={problems[l.key] ? 'thm-input--bad' : ''}
                      value={l.slug}
                      onChange={(e) => updateRow(l.key, { slug: e.target.value })}
                    />
                  </label>
                  {problems[l.key] === 'slug' && (
                    <span className="promote-err">{t('themes:place.idBad')}</span>
                  )}
                  {problems[l.key] === 'taken' && (
                    <span className="promote-err">{t('themes:place.idTaken')}</span>
                  )}
                  {problems[l.key] === 'duplicate' && (
                    <span className="promote-err">{t('themes:place.idDuplicate')}</span>
                  )}
                </div>
              ))}
              <div className="vocab-field-hint">{t('themes:place.idNote')}</div>
            </div>
          )}
          {[...implicit.map((r) => ({ subject: r.subject, object: r.curie, relation: (r.quantity ? 'quantity' : 'eq') as RelationKind })), ...accepted].map((r) => (
            <div key={`${r.subject}|${r.object}|${r.relation}`}>
              ・{t('themes:place.relation', { sentence: relationSentence(r) })}
            </div>
          ))}
          {draft.selections.map((s, i) => (
            <div key={s.question}>
              ・{t(isMemo(s) ? 'themes:place.memoQuestion' : 'themes:place.question', { title: s.question })}
              {sentences[i] ? <span className="vocab-field-hint"> — {sentences[i]}</span> : null}
            </div>
          ))}
          {summary.aiDrafted && (
            <div className="vocab-field-hint" data-testid="theme-ai-mark">
              {t('themes:place.aiMark', { date: today })}
            </div>
          )}
        </div>
        {saveErr && <p className="promote-err">{t('themes:wizard.saveErr', { detail: saveErr })}</p>}
        {placeErr && (
          <p className="promote-err" role="alert">
            {placeErr}
          </p>
        )}
        {blocker && <p className="vocab-field-hint">{t(`themes:place.blocker.${blocker}`)}</p>}
        <div className="kz-actions thm-nav">
          <button type="button" className="btn btn--ghost" disabled={placing} onClick={goBack}>
            {t('themes:wizard.back')}
          </button>
          <button
            type="button"
            className="btn btn--activity"
            disabled={placing || problemCount > 0}
            onClick={() => void place()}
          >
            {placing ? t('themes:place.placing') : t('themes:place.go')}
          </button>
        </div>
      </>
    )
  }

  return (
    <section className="thm-wizard" aria-label={t('themes:wizard.aria')} data-testid="theme-wizard">
      <header className="thm-wizard-head">
        <h2 className="thm-title">{t('themes:wizard.title')}</h2>
        <span className="thm-wizard-actions">
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => void discard()}>
            {t('themes:wizard.discard')}
          </button>
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => void close()}>
            {t('themes:wizard.close')}
          </button>
        </span>
      </header>
      <ol className="kz-recipe thm-recipe" aria-label={t('themes:wizard.recipe')}>
        {STEP_KEYS.map((key, i) => {
          const n = i + 1
          const active = n === step
          const done = n < step
          return (
            <li
              key={key}
              className={`kz-recipe-step${active ? ' active' : ''}${done ? ' done' : ''}`}
              aria-current={active ? 'step' : undefined}
            >
              <span className="kz-recipe-num" aria-hidden="true">
                {done ? '✓' : CIRCLED[i]}
              </span>
              <span className="kz-recipe-text">{t(`themes:steps.${key}`)}</span>
              {active && <span className="kz-recipe-here">{t('themes:wizard.here')}</span>}
            </li>
          )
        })}
      </ol>
      <div className="kz-card thm-card">{body}</div>
    </section>
  )
}
