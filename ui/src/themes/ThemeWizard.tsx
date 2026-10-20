import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useLlmSettings } from '../settings/context'
import { type SharedTerm, listTerms } from '../vocabApi'
import { ThemeWorkspace } from './ThemeWorkspace'
import {
  type CommitResult,
  ThemesApiError,
  commitTheme,
  createDraft,
  deleteDraft,
  draftWithAi,
  getDraft,
  matchTerms,
  saveDraft,
} from './themesApi'
import {
  type AnswerRow,
  type AnswerWay,
  type ConceptRow,
  type RelationRow,
  type Step,
  type ThemeDraft,
  ANSWER_WAYS,
  EXAMPLE_QUESTIONS_EN,
  EXAMPLE_QUESTIONS_JA,
  addQuestion,
  answerComplete,
  answerHost,
  answerState,
  applyDraftResponse,
  applyMatches,
  buildCommitBody,
  canStart,
  conceptMints,
  conceptName,
  conceptsFor,
  confirmCounts,
  defaultLabel,
  idList,
  knowledgeEstimate,
  matchSignature,
  nextStep,
  parseQuestionLines,
  placeBlocker,
  prevStep,
  rowProblems,
  sentConcepts,
  sentRelations,
  setAnswerTarget,
  setWay,
  startManual,
  syncAnswers,
  unlinkedKeys,
  wayNeeds,
  wayOf,
} from './themeDraft'
import {
  NO_PENDING,
  type Pending,
  loadPending,
  pendingOf,
  pendingTotal,
  realDraft,
  savePending,
} from './workspaceState'
import './themes.css'

// 「知りたいことから作る」3 段（design.md §15.2・§15.4）:
//   ① 書く → ② 下書きをたしかめる → ③ 置く
// ②は上＝問いごとの答え方・左＝知識の図（ConceptMap）・右＝文の一覧・下＝手で足す＋
// 「N 件をまとめて確定」。✕ は却下を保存して下書きから消す（次の下書きで出さない）。
// 手順バーは かんたんの RecipeCard と同じ見た目（.kz-recipe）。途中で閉じても下書きが残って続きから開ける。
// AI は閉じた JSON だけ（api が検証・機械の後処理まで済ませて返す）。未設定なら「手で書く」へ。

const STEP_KEYS = ['write', 'review', 'place'] as const
const CIRCLED = ['①', '②', '③']

function blankDraft(extend: string | undefined, question: string): ThemeDraft {
  return {
    id: '',
    label: '',
    step: 1,
    ...(extend ? { extend } : {}),
    questions: question ? [question] : [],
    concepts: [],
    relations: [],
    answers: [],
    suggested_questions: [],
    standards: {},
    updated_at: '',
  }
}

type AiState = { status: 'idle' | 'running' | 'ok' | 'unconfigured' | 'failed'; detail?: string }

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

  const [draft, setDraft] = useState<ThemeDraft>(() => blankDraft(extend?.id, initialQuestion))
  // ①の入力欄。段を進めるときに draft へ整える。
  const [qText, setQText] = useState(initialQuestion)
  const [labelText, setLabelText] = useState(extend?.label ?? '')
  const [loaded, setLoaded] = useState(!resumeId)
  const [loadErr, setLoadErr] = useState('')
  const [existing, setExisting] = useState<SharedTerm[]>([])
  const [ai, setAi] = useState<AiState>({ status: 'idle' })
  const [saveErr, setSaveErr] = useState('')
  const [placing, setPlacing] = useState(false)
  // ①の主ボタンを押してから下書きを保存し終えるまで（二重の下書きを作らない）
  const [starting, setStarting] = useState(false)
  const startingRef = useRef(false)
  const [placeErr, setPlaceErr] = useState('')
  // ②
  const [actionErr, setActionErr] = useState('')
  const [openAnswer, setOpenAnswer] = useState<number | null>(null)
  // ②に入った時点の AI の下書きは「提案（薄い）」。「そう」で本物になる（本物だけを確定に送る）
  const [pending, setPendingState] = useState<Pending>(NO_PENDING)
  const [firstDraft, setFirstDraft] = useState<{ concepts: number; relations: number } | null>(null)
  // AI の下書きを読み直すたびに増える（作業場の会話の 1 枚目を作り直す）
  const [draftRun, setDraftRun] = useState(0)
  const setPending = useCallback((f: (p: Pending) => Pending) => setPendingState(f), [])
  // 機械の照合に出した時点の行の中身（key → matchSignature）。同じ中身は出し直さない。
  const matchedSigs = useRef<Record<string, string>>({})

  useEffect(() => {
    if (!resumeId) return
    let off = false
    getDraft(resumeId)
      .then((d) => {
        if (off) return
        setDraft(d)
        setQText(d.questions.join('\n'))
        setLabelText(d.label)
        // 保存してある照合は、そのまま使う（開き直すたびに照合し直さない）
        matchedSigs.current = Object.fromEntries(d.concepts.map((r) => [r.key, matchSignature(r)]))
        // 薄いままの提案は、この端末に残してある（続きから見る）
        const left = loadPending(d.id)
        setPendingState(left)
        if (pendingTotal(left) > 0) setFirstDraft({ concepts: left.concepts.length, relations: left.relations.length })
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

  // 提案（薄い）の状態は、下書きごとにこの端末へ置く（再開で続きから）
  useEffect(() => {
    if (draft.id) savePending(draft.id, pending)
  }, [draft.id, pending])

  // 作業場の会話がこの画面の相談役: 右下の「AI に相談」（全画面）は、開いている間は隠す（二重にしない）
  useEffect(() => {
    document.body.classList.add('thm-wizard-open')
    return () => document.body.classList.remove('thm-wizard-open')
  }, [])

  const existingSlugs = useMemo(() => new Set(existing.map((e) => e.slug)), [existing])
  const answering = useMemo(
    () => Object.fromEntries(existing.map((e) => [e.slug, e.answering_datasets])),
    [existing],
  )
  const step = draft.step
  const concepts = draft.concepts

  // ②: 手で足した行の名前・別名・種類が変わったら、400ms 待って機械の照合（完全一致・AI は使わない）に出す。
  // 失敗は黙って今のまま。`use` は人が触っていない行だけ機械の勧めに寄る。
  useEffect(() => {
    if (step !== 2) return
    const timer = window.setTimeout(() => {
      const sent: Record<string, string> = {}
      const batch: ConceptRow[] = []
      for (const r of concepts) {
        if (r.by !== 'human') continue
        const sig = matchSignature(r)
        if (matchedSigs.current[r.key] === sig) continue
        matchedSigs.current[r.key] = sig
        if (!r.label_ja.trim() && !r.label_en.trim() && r.aliases.length === 0) continue
        sent[r.key] = sig
        batch.push(r)
      }
      if (batch.length === 0) return
      matchTerms(
        batch.map((r) => ({
          key: r.key,
          label_ja: r.label_ja,
          label_en: r.label_en,
          aliases: r.aliases,
          kind: r.kind,
        })),
      )
        .then((results) => setDraft((d) => ({ ...d, concepts: applyMatches(d.concepts, results, sent) })))
        .catch(() => {})
    }, 400)
    return () => window.clearTimeout(timer)
  }, [step, concepts])

  // 確定（③・件数）に使うのは本物だけ（提案（薄い）は含めない）
  const real = useMemo(() => realDraft(draft, pending), [draft, pending])
  const problems = rowProblems(sentConcepts(real), existingSlugs)
  const problemCount = Object.keys(problems).length
  const unlinked = useMemo(() => unlinkedKeys(real), [real])
  const counts = useMemo(() => confirmCounts(real), [real])
  const thin = pendingTotal(pending)

  /** 下書きを保存する（まだ id が無ければ作る）。失敗しても進める — 置くときに id が要る。 */
  const persist = useCallback(async (next: ThemeDraft): Promise<ThemeDraft> => {
    setSaveErr('')
    try {
      if (!next.id) {
        const created = await createDraft(next.label, next.questions, next.extend)
        next = { ...next, id: created.id, label: next.label.trim() ? next.label : created.label }
      }
      const saved = await saveDraft(next)
      next = { ...next, updated_at: saved.updated_at }
    } catch (e) {
      setSaveErr(e instanceof Error ? e.message : String(e))
    }
    setDraft(next)
    return next
  }, [])

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
      const res = await draftWithAi(base.questions, labelText.trim(), lang, getActiveCredentials())
      const applied = applyDraftResponse({ ...base, label: labelText.trim() }, res)
      matchedSigs.current = Object.fromEntries(applied.concepts.map((r) => [r.key, matchSignature(r)]))
      setAi({ status: 'ok' })
      if (!labelText.trim()) setLabelText(applied.label)
      // 下書きを先に置いてから、全部「提案（薄い）」にする（先に薄くすると、まだ空の下書きに合わせて外される）
      await persist({ ...applied, step: 2 })
      setPendingState(pendingOf(applied))
      setFirstDraft({ concepts: applied.concepts.length, relations: applied.relations.length })
      setDraftRun((n) => n + 1)
    } catch (e) {
      if (e instanceof ThemesApiError && e.status === 503) setAi({ status: 'unconfigured' })
      else setAi({ status: 'failed', detail: e instanceof Error ? e.message : String(e) })
    }
  }

  // ① → ②。すでに下書きの概念があれば、そのまま見せる（読み直しは②の「もう一度読ませる」）。
  async function fromWrite(useAi: boolean) {
    if (startingRef.current) return
    const questions = questionsFromText()
    if (!canStart(questions)) return
    startingRef.current = true
    setStarting(true)
    try {
      const label = labelText.trim()
      const base: ThemeDraft = {
        ...draft,
        label,
        questions,
        answers: syncAnswers(draft.answers, questions),
      }
      if (!useAi) {
        await go(2, startManual({ ...base, label: label || defaultLabel(questions) }))
        return
      }
      const saved = await persist({ ...base, step: 2 })
      if (saved.concepts.length === 0 && saved.relations.length === 0) void runAi({ ...saved, label })
    } finally {
      startingRef.current = false
      setStarting(false)
    }
  }

  // ---- ②の編集 ----

  const nameOf = (key: string): string => {
    const row = concepts.find((r) => r.key === key)
    if (row) return conceptName(row, lang)
    // 標準の語は表示名で（生の curie は出さない）。情報が無い古い下書きは curie の名前の部分だけ
    const std = draft.standards[key]
    if (std) return t('themes:rel.stdName', { name: std.label || key.slice(key.indexOf(':') + 1) })
    const i = key.indexOf(':')
    return i > 0 ? t('themes:rel.stdName', { name: key.slice(i + 1) }) : key
  }

  function updateAnswer(index: number, fn: (a: AnswerRow) => AnswerRow) {
    setDraft((d) => ({ ...d, answers: d.answers.map((a, i) => (i === index ? fn(a) : a)) }))
  }

  function updateSlug(key: string, slug: string) {
    setDraft((d) => ({ ...d, concepts: d.concepts.map((c) => (c.key === key ? { ...c, slug } : c)) }))
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
      const result = await commitTheme(saved.id, buildCommitBody(real, lang))
      onPlaced(result)
    } catch (e) {
      const msg = e instanceof ThemesApiError ? e.detail || e.message : e instanceof Error ? e.message : String(e)
      setPlaceErr(msg)
      // 置けない組み合わせ（422・問いにつながらない概念など）は、戻った②にも理由を残す
      if (e instanceof ThemesApiError && e.status === 422) setActionErr(msg)
    } finally {
      setPlacing(false)
    }
  }

  async function close() {
    const questions = questionsFromText()
    const label = labelText.trim()
    if (draft.id) await persist(step === 1 ? { ...draft, questions, label: label || draft.label } : draft)
    else if (questions.length > 0) await persist({ ...draft, questions, label })
    onClose()
  }

  // 「閉じる」を押すと、下書きを残すか捨てるかをここで聞く（捨てる操作は閉じるの中に畳んだ）。
  const [closing, setClosing] = useState(false)

  async function discard() {
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

  const relationSentence = (rel: RelationRow): string =>
    t(`themes:rel.sentence.${rel.rel}`, { subject: nameOf(rel.from), object: nameOf(rel.to) })

  /** 「熱伝導率 を下げるもの」「格子 の件数」…（答え方の言葉）。 */
  const wayText = (a: AnswerRow): string => {
    const way = wayOf(a)
    if (a.mode === 'memo' || !answerComplete(a)) return t('themes:answer.way.none')
    const target = nameOf(a.target)
    const kind = nameOf(a.kind)
    const property = nameOf(a.property)
    if (way === 'range' || way === 'top') {
      return t(`themes:answer.way.${way}${a.kind ? '' : 'Only'}`, { kind, property })
    }
    return t(`themes:answer.way.${way}`, { target, kind, property })
  }

  const goBack = () => void go(prevStep(step))

  // ---- 各段 --------------------------------------------------------------------

  let body: ReactNode

  if (step === 1) {
    const questions = questionsFromText()
    const count = questions.length
    const examples = lang === 'en' ? EXAMPLE_QUESTIONS_EN : EXAMPLE_QUESTIONS_JA
    const suggestions = draft.suggested_questions.filter((q) => !questions.includes(q))
    body = (
      <>
        <h2 className="thm-h">
          {CIRCLED[0]} {t('themes:steps.write')}
        </h2>
        <p className="thm-lead">{t('themes:write.lead')}</p>
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
        <div>
          <p className="vocab-field-hint">{t('themes:write.examples')}</p>
          <div className="thm-chips">
            {examples
              .filter((q) => !questions.includes(q))
              .map((q) => (
                <button
                  key={q}
                  type="button"
                  className="thm-chip"
                  onClick={() => setQText(addQuestion(questions, q).join('\n'))}
                >
                  ＋ {q}
                </button>
              ))}
            {suggestions.map((q) => (
              <button
                key={q}
                type="button"
                className="thm-chip"
                title={t('themes:write.suggestNote')}
                onClick={() => setQText(addQuestion(questions, q).join('\n'))}
              >
                ＋ {q}
              </button>
            ))}
          </div>
        </div>
        <label className="vocab-field">
          <span>{t('themes:write.themeName')}</span>
          <input
            type="text"
            value={labelText}
            readOnly={!!extend}
            placeholder={t('themes:write.themeNamePlaceholder')}
            onChange={(e) => setLabelText(e.target.value)}
          />
        </label>
        {!isReady && <p className="vocab-field-hint">{t('themes:write.aiOff')}</p>}
        {saveErr && <p className="promote-err">{t('themes:wizard.saveErr', { detail: saveErr })}</p>}
        <div className="kz-actions thm-nav">
          {isReady ? (
            <>
              <button
                type="button"
                className="btn btn--accent"
                disabled={count === 0 || starting || ai.status === 'running'}
                onClick={() => void fromWrite(true)}
              >
                {t('themes:write.toRead')}
              </button>
              <button
                type="button"
                className="btn btn--ghost"
                disabled={count === 0 || starting || ai.status === 'running'}
                onClick={() => void fromWrite(false)}
              >
                {t('themes:write.manual')}
              </button>
            </>
          ) : (
            <button
              type="button"
              className="btn btn--accent"
              disabled={count === 0 || starting}
              onClick={() => void fromWrite(false)}
            >
              {t('themes:write.manual')}
            </button>
          )}
        </div>
      </>
    )
  } else if (step === 2) {
    const hasMap = concepts.length > 0
    const byAi = concepts.some((c) => c.by === 'ai')
    const top = (
      <>
        {/* 説明は 1 行だけ。AI が未設定のときは「ここを広げる」が使えない旨に差し替える（2 行にしない） */}
        <p className="thm-lead">
          {t(
            !hasMap
              ? 'themes:review.leadEmpty'
              : !isReady
                ? 'themes:review.aiOff'
                : byAi || thin > 0
                  ? 'themes:review.lead'
                  : 'themes:review.leadHand',
          )}
        </p>

        {ai.status === 'running' && (
          <div className="thm-try" role="status" aria-live="polite">
            <p className="thm-q">
              <span className="spinner" /> {t('themes:read.running')}
            </p>
          </div>
        )}
        {ai.status === 'unconfigured' && (
          <section className="kz-card kz-stop" role="alert" data-testid="theme-ai-unconfigured">
            <h3 className="kz-title">{t('themes:read.unconfigured.title')}</h3>
            <p className="kz-note">{t('themes:read.unconfigured.body')}</p>
            <div className="kz-actions">
              <button type="button" className="btn btn--ghost" onClick={() => openSettings()}>
                {t('themes:read.openSettings')}
              </button>
              <button type="button" className="btn btn--ghost" onClick={() => setAi({ status: 'idle' })}>
                {t('themes:read.dismiss')}
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
              <button type="button" className="btn btn--ghost" onClick={() => setAi({ status: 'idle' })}>
                {t('themes:read.manual')}
              </button>
            </div>
          </section>
        )}

        {/* 上 1 行: 問いごとの答え方 */}
        <section className="thm-aqs" aria-label={t('themes:review.questionsAria')} data-testid="theme-answers">
          {draft.answers.map((a, i) => {
            const state = answerState(a)
            const way = wayOf(a)
            const host = answerHost(a)
            const hostRow = concepts.find((c) => c.key === host)
            const dataN = hostRow && !conceptMints(hostRow) ? (answering[hostRow.slug] ?? 0) : 0
            const open = openAnswer === i
            const needs = wayNeeds(way)
            return (
              <div className="thm-aq" key={a.question} data-testid="theme-answer">
                <div className="thm-aq-head">
                  <span className="thm-aq-text">{a.question}</span>
                  {state === 'memo' ? (
                    <span className="pill wait">{t('themes:review.still')}</span>
                  ) : (
                    <span className="pill ok">
                      {t('themes:review.counts', {
                        k: state === 'knowledge' ? knowledgeEstimate(draft, a) : 0,
                        d: dataN,
                      })}
                    </span>
                  )}
                  {a.by === 'ai' && state !== 'memo' && <span className="thm-ai">{t('themes:aiMark')}</span>}
                </div>
                <div className="thm-aq-way">
                  <span className="vocab-field-hint">{t('themes:answer.label')}</span>{' '}
                  <button
                    type="button"
                    className="link-btn"
                    aria-expanded={open}
                    onClick={() => setOpenAnswer(open ? null : i)}
                  >
                    {wayText(a)} ▾
                  </button>
                </div>
                {a.wanted_data && (
                  <div className="thm-aq-want">{t('themes:review.wanted', { text: a.wanted_data })}</div>
                )}
                {open && (
                  <div className="thm-aq-edit">
                    <label>
                      {t('themes:answer.pickWay')}{' '}
                      <select
                        value={way}
                        onChange={(e) => updateAnswer(i, (x) => setWay(x, e.target.value as AnswerWay))}
                      >
                        {ANSWER_WAYS.map((w) => (
                          <option key={w} value={w}>
                            {t(`themes:answer.option.${w}`)}
                          </option>
                        ))}
                      </select>
                    </label>
                    {needs.target && (
                      <ConceptSelect
                        label={t('themes:answer.pickTarget')}
                        value={a.target}
                        options={conceptsFor(draft, way, 'target')}
                        lang={lang}
                        onChange={(v) => updateAnswer(i, (x) => setAnswerTarget(x, { target: v }))}
                      />
                    )}
                    {needs.kind && (
                      <ConceptSelect
                        label={t('themes:answer.pickKind')}
                        value={a.kind}
                        options={conceptsFor(draft, way, 'kind')}
                        lang={lang}
                        optional={needs.kindOptional}
                        onChange={(v) => updateAnswer(i, (x) => setAnswerTarget(x, { kind: v }))}
                      />
                    )}
                    {needs.property && (
                      <ConceptSelect
                        label={t('themes:answer.pickProperty')}
                        value={a.property}
                        options={conceptsFor(draft, way, 'property')}
                        lang={lang}
                        onChange={(v) => updateAnswer(i, (x) => setAnswerTarget(x, { property: v }))}
                      />
                    )}
                    {concepts.length === 0 && way !== 'memo' && (
                      <span className="vocab-field-hint">{t('themes:answer.noConcepts')}</span>
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </section>

      </>
    )
    const bottom = (
      <>
        {saveErr && <p className="promote-err">{t('themes:wizard.saveErr', { detail: saveErr })}</p>}
        {thin > 0 && <p className="vocab-field-hint">{t('themes:review.thinNote', { n: thin })}</p>}
        <div className="kz-actions thm-nav">
          <button type="button" className="btn btn--ghost" onClick={goBack}>
            {t('themes:wizard.back')}
          </button>
          {isReady && (
            <button
              type="button"
              className="btn btn--ghost"
              disabled={ai.status === 'running'}
              onClick={() => void runAi({ ...draft, label: labelText.trim() })}
            >
              {t('themes:read.again')}
            </button>
          )}
          <button
            type="button"
            className="btn btn--accent"
            disabled={ai.status === 'running' || draft.questions.length === 0}
            data-testid="confirm-all"
            title={unlinked.size > 0 ? t('themes:review.unlinkedNote', { n: unlinked.size }) : undefined}
            onClick={() => void go(nextStep(step))}
          >
            {counts.total > 0 ? t('themes:review.confirm', { n: counts.total }) : t('themes:review.confirmNone')}
          </button>
        </div>
      </>
    )
    body = (
      <>
        <h2 className="thm-h">
          {CIRCLED[1]} {t('themes:steps.review')}
        </h2>
        <ThemeWorkspace
          key={draftRun}
          draft={draft}
          setDraft={setDraft}
          persist={persist}
          lang={lang}
          pending={pending}
          setPending={setPending}
          firstDraft={firstDraft}
          top={top}
          bottom={bottom}
          notice={actionErr}
        />
      </>
    )
  } else {
    const ids = idList(real)
    const sent = sentRelations(real)
    const blocker = placeBlocker(real, existingSlugs)
    const label = labelText.trim() || draft.label || defaultLabel(draft.questions)
    body = (
      <>
        <h2 className="thm-h">
          {CIRCLED[2]} {t('themes:steps.place')}
        </h2>
        <p className="thm-lead" data-testid="theme-place-lead">
          {t(extend ? 'themes:place.leadExtend' : 'themes:place.lead', {
            label,
            concepts: counts.concepts,
            relations: counts.relations,
            questions: draft.questions.length,
          })}
        </p>
        {!extend && (
          <label className="vocab-field">
            <span>{t('themes:write.themeName')}</span>
            <input
              type="text"
              value={draft.label}
              onChange={(e) => {
                setLabelText(e.target.value)
                setDraft((d) => ({ ...d, label: e.target.value }))
              }}
            />
          </label>
        )}
        <div className="thm-effect" data-testid="theme-place-list">
          {ids.length > 0 && (
            <>
              <div className="thm-sub-h">{t('themes:place.concepts')}</div>
              <div className="thm-ids">
                {ids.map((l) => (
                  <div key={l.key} className="thm-id">
                    <span>{l.label}</span>
                    <label className="thm-idfield">
                      <span className="vocab-field-hint">{t('themes:place.id')}</span>
                      <input
                        type="text"
                        aria-label={t('themes:place.idAria', { label: l.label })}
                        className={problems[l.key] ? 'thm-input--bad' : ''}
                        value={l.slug}
                        onChange={(e) => updateSlug(l.key, e.target.value)}
                      />
                    </label>
                    {problems[l.key] === 'slug' && <span className="promote-err">{t('themes:place.idBad')}</span>}
                    {problems[l.key] === 'taken' && <span className="promote-err">{t('themes:place.idTaken')}</span>}
                    {problems[l.key] === 'duplicate' && (
                      <span className="promote-err">{t('themes:place.idDuplicate')}</span>
                    )}
                  </div>
                ))}
              </div>
            </>
          )}
          {sent.length > 0 && (
            <>
              <div className="thm-sub-h">{t('themes:place.relations')}</div>
              {sent.map((r, i) => (
                <div key={`${i}-${r.from}|${r.rel}|${r.to}`}>
                  ・{relationSentence(r)}
                  {r.reason && <span className="thm-reason"> — {r.reason}</span>}
                </div>
              ))}
            </>
          )}
          <div className="thm-sub-h">{t('themes:place.questions')}</div>
          {draft.answers.map((a) => (
            <div key={a.question}>
              ・{a.question}
              <span className="vocab-field-hint">
                {' '}
                — {answerState(a) === 'memo' ? t('themes:place.memoQuestion') : wayText(a)}
              </span>
              {a.wanted_data && <div className="thm-reason">{t('themes:review.wanted', { text: a.wanted_data })}</div>}
            </div>
          ))}
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
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => setClosing(true)}>
            {t('themes:wizard.close')}
          </button>
        </span>
      </header>
      {closing && (
        <div className="thm-closing" role="group" aria-label={t('themes:wizard.closeAsk')}>
          <span>{t('themes:wizard.closeAsk')}</span>
          <button type="button" className="btn btn--sm" onClick={() => void close()}>
            {t('themes:wizard.keepClose')}
          </button>
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => void discard()}>
            {t('themes:wizard.discardClose')}
          </button>
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => setClosing(false)}>
            {t('themes:wizard.keepGoing')}
          </button>
        </div>
      )}
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

/** 答え方の対象を選ぶ（概念の一覧から）。 */
function ConceptSelect({
  label,
  value,
  options,
  lang,
  optional,
  onChange,
}: {
  label: string
  value: string
  options: ConceptRow[]
  lang: string
  optional?: boolean
  onChange: (key: string) => void
}) {
  const { t } = useTranslation()
  return (
    <label>
      {label}{' '}
      <select value={value} onChange={(e) => onChange(e.target.value)} aria-label={label}>
        <option value="">{optional ? t('themes:answer.none') : t('themes:pick')}</option>
        {options.map((c) => (
          <option key={c.key} value={c.key}>
            {conceptName(c, lang)}
          </option>
        ))}
      </select>
    </label>
  )
}
