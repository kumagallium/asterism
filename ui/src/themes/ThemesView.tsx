import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { getSchema, type SchemaSummary } from '../demoApi'
import { type CatalogDataset, getCatalogDatasets } from '../galleryApi'
import { VocabMapSection } from '../VocabMapSection'
import { VocabTermsSection } from '../VocabTermsSection'
import { parseVocabHash } from '../vocabQuestion'
import { ThemeWizard } from './ThemeWizard'
import {
  type CommitResult,
  type DraftSummary,
  type ThemeQuestion,
  type ThemeSummary,
  type ThemesListing,
  deleteDraft,
  deleteTheme,
  listThemes,
  templateUrl,
  themeLabel,
} from './themesApi'
import './themes.css'

// 「知りたいこと」（#/questions）— テーマごとの問いの帳。UI 再設計 PR 4（design.md §3）。
//
// テーマ（問いの束）ごとに カード: 問いの輪（答えるデータセット数／全体）・題・実行文・要る用語・
// メモの問い。右上の「？ 新しいテーマを作る」で 6 段（ThemeWizard）が開く。用語はテーマの持ち物では
// ないので、テーマを外しても用語・関係・問いは残る。下の「用語の地図」「用語の一覧」は折りたたみ。

function goTo(hash: string): void {
  if (window.location.hash !== hash) window.location.hash = hash
}

/** 問いの輪: 答えるデータセット数 / 全体。まだ誰も答えないときは空の輪。 */
function Ring({ n, total }: { n: number; total: number }) {
  const { t } = useTranslation()
  if (n <= 0 || total <= 0) {
    return <span className="thm-ring thm-ring--none" title={t('themes:card.ringNone')} aria-hidden="true" />
  }
  const pct = Math.min(100, Math.round((100 * n) / total))
  return (
    <span
      className="thm-ring"
      style={{ ['--p' as string]: pct }}
      title={t('themes:card.ringTitle', { n, total })}
      aria-hidden="true"
    />
  )
}

function QuestionRow({
  q,
  total,
  onAsk,
}: {
  q: ThemeQuestion
  total: number
  onAsk: (title: string) => void
}) {
  const { t } = useTranslation()
  return (
    <div className="thm-qrow" data-testid="theme-question">
      <Ring n={q.answering_datasets} total={total} />
      <span className="thm-qtitle">
        {q.title}
        {q.shape && <span className="thm-qshape">{q.shape}</span>}
      </span>
      <span className={`pill ${q.answering_datasets > 0 ? 'ok' : 'wait'}`}>
        {q.answering_datasets > 0
          ? t('themes:card.answering', { n: q.answering_datasets })
          : t('themes:card.notYet')}
      </span>
      <button type="button" className="link-btn" onClick={() => onAsk(q.title)}>
        {t('themes:card.ask')}
      </button>
    </div>
  )
}

function ThemeCard({
  theme,
  total,
  focused,
  onAsk,
  onAddQuestion,
  onRemoved,
}: {
  theme: ThemeSummary
  total: number
  focused: boolean
  onAsk: (title: string) => void
  onAddQuestion: (theme: ThemeSummary) => void
  onRemoved: () => void
}) {
  const { t } = useTranslation()
  const [err, setErr] = useState('')
  const n = theme.questions.length

  async function remove() {
    if (!window.confirm(t('themes:card.removeConfirm', { label: themeLabel(theme, t) }))) return
    setErr('')
    try {
      await deleteTheme(theme.id)
      onRemoved()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }

  const firstAnswerable = theme.questions.find((q) => q.answering_datasets > 0) ?? theme.questions[0]

  return (
    <div
      className={`card thm-card-theme${focused ? ' thm-card-theme--focus' : ''}`}
      id={`theme-${theme.id}`}
      data-testid="theme-card"
    >
      <h3 className="thm-card-h">
        {themeLabel(theme, t)}
        <span className={`pill ${n > 0 && theme.answerable === n ? 'ok' : 'wait'}`}>
          {t('themes:card.answerable', { ok: theme.answerable, n })}
        </span>
      </h3>
      {theme.questions.map((q) => (
        <QuestionRow key={q.tool_name} q={q} total={total} onAsk={onAsk} />
      ))}
      {n === 0 && <p className="ds-empty-note">{t('themes:card.noQuestions')}</p>}
      {theme.terms.length > 0 && (
        <p className="thm-needs">
          <span className="vocab-field-hint">{t('themes:card.needs')}</span>{' '}
          {theme.terms.map((term) => (
            <a
              key={term.slug}
              className="thm-term-chip"
              href={`#/questions/term/${encodeURIComponent(term.slug)}`}
              title={term.slug}
            >
              {term.label}
              <span className="vocab-field-hint">
                {' '}
                {t(`themes:kind.${term.kind}`)}
              </span>
            </a>
          ))}
        </p>
      )}
      {theme.memo_questions.length > 0 && (
        <div className="thm-memos" data-testid="theme-memos">
          <div className="vocab-field-hint">{t('themes:card.memoTitle')}</div>
          {theme.memo_questions.map((m) => (
            <div key={m.id} className="thm-memo-row">
              <span className="pill wait">{t('themes:try.memoBadge')}</span> {m.title}
              {m.reason && <span className="vocab-field-hint"> — {m.reason}</span>}
            </div>
          ))}
        </div>
      )}
      <div className="thm-card-actions">
        <button type="button" className="btn btn--sm" onClick={() => onAddQuestion(theme)}>
          {t('themes:card.addQuestion')}
        </button>
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          disabled={!firstAnswerable}
          onClick={() => firstAnswerable && onAsk(firstAnswerable.title)}
          title={t('themes:card.seeAnswerHint')}
        >
          {t('themes:card.seeAnswer')}
        </button>
        <a className="btn btn--ghost btn--sm" href={templateUrl(theme.id)} download title={t('themes:card.templateHint')}>
          {t('themes:card.template')}
        </a>
        <button type="button" className="btn btn--ghost btn--sm" onClick={() => void remove()}>
          {t('themes:card.remove')}
        </button>
      </div>
      {err && <p className="promote-err">{err}</p>}
    </div>
  )
}

type WizardOpen = { resumeId?: string; extend?: { id: string; label: string } }

/** 地図と一覧は、開いたときに読む（初期の読み込みを重くしない）。 */
function Folds({ version }: { version: number }) {
  const { t } = useTranslation()
  const [mapOpen, setMapOpen] = useState(false)
  const [datasets, setDatasets] = useState<CatalogDataset[]>([])
  const [schema, setSchema] = useState<SchemaSummary | null>(null)
  const [ready, setReady] = useState(false)
  // 用語の一覧で用語を外す・問いを足したとき、一覧だけ読み直す（地図は開き直したときに読む）。
  const [termsKey, setTermsKey] = useState(0)

  useEffect(() => {
    if (!mapOpen) return
    let off = false
    Promise.allSettled([getCatalogDatasets(), getSchema()]).then(([ds, sc]) => {
      if (off) return
      if (ds.status === 'fulfilled') setDatasets(ds.value)
      if (sc.status === 'fulfilled') setSchema(sc.value)
      setReady(true)
    })
    return () => {
      off = true
    }
  }, [mapOpen])

  return (
    <div className="thm-folds">
      <details onToggle={(e) => setMapOpen((e.currentTarget as HTMLDetailsElement).open)}>
        <summary>{t('themes:folds.map')}</summary>
        {mapOpen && !ready && (
          <p className="loading-row">
            <span className="spinner" />
            {t('themes:loading')}
          </p>
        )}
        {mapOpen && ready && (
          <VocabMapSection
            datasets={datasets}
            schema={schema}
            onOpenDataset={(id) => goTo(`#/datasets/${encodeURIComponent(id)}`)}
            onOpenKind={(iri) => goTo(`#/cards/k/${encodeURIComponent(iri)}`)}
            onOpenCrosswalk={() => goTo('#/crosswalk')}
            reloadKey={version}
          />
        )}
      </details>
      <details>
        <summary>{t('themes:folds.terms')}</summary>
        <VocabTermsSection reloadKey={version + termsKey} onChanged={() => setTermsKey((k) => k + 1)} />
      </details>
    </div>
  )
}

export function ThemesView({
  newQuestion,
  focusTheme,
  onCloseNew,
  onOpenAsk,
}: {
  /** `#/questions?new=1[&q=題]` — 6 段を開く（上部の「？ 知りたいことを書く」・質問するから）。 */
  newQuestion?: boolean
  /** `#/questions/<theme>` — そのテーマのカードを見せる。 */
  focusTheme?: string
  /** `new` を hash から外す（6 段を閉じた・置いたとき）。 */
  onCloseNew: () => void
  /** 「答えを見る」: 質問するへ題を持ち越す。 */
  onOpenAsk: (question: string) => void
}) {
  const { t, i18n } = useTranslation()
  const lang = i18n.language.startsWith('en') ? 'en' : 'ja'
  const [listing, setListing] = useState<ThemesListing | null>(null)
  const [err, setErr] = useState('')
  const [reloadKey, setReloadKey] = useState(0)
  const [local, setLocal] = useState<WizardOpen | null>(null)
  const [note, setNote] = useState('')

  useEffect(() => {
    let off = false
    listThemes(lang)
      .then((l) => {
        if (off) return
        setErr('')
        setListing(l)
      })
      .catch((e) => {
        if (off) return
        setListing(null)
        setErr(e instanceof Error ? e.message : String(e))
      })
    return () => {
      off = true
    }
  }, [lang, reloadKey])

  // `#/questions/<theme>`: そのカードまで運ぶ（読み込めたあと 1 回）。
  useEffect(() => {
    if (!focusTheme || !listing) return
    document.getElementById(`theme-${focusTheme}`)?.scrollIntoView?.({ block: 'start' })
  }, [focusTheme, listing])

  const reload = () => setReloadKey((k) => k + 1)
  // 6 段は、hash の `new=1`（題つき）か、この画面で開いた下書き・「問いを足す」で開く。
  const fromHash = newQuestion ? { question: parseVocabHash(window.location.hash).q } : null
  const wizardOpen = fromHash !== null || local !== null

  function closeWizard() {
    setLocal(null)
    if (newQuestion) onCloseNew()
    reload()
  }

  function placed(result: CommitResult) {
    setNote(
      t('themes:placedNote', {
        label: themeLabel(result.theme, t),
        terms: result.minted.length,
        lines: result.lines,
        questions: result.questions.length,
      }),
    )
    setLocal(null)
    if (newQuestion) onCloseNew()
    goTo(`#/questions/${encodeURIComponent(result.theme.id)}`)
    reload()
  }

  async function discardDraft(d: DraftSummary) {
    if (!window.confirm(t('themes:draft.discardConfirm', { label: d.label }))) return
    try {
      await deleteDraft(d.id)
    } catch {
      /* 一覧を読み直せば残っているかが分かる */
    }
    reload()
  }

  const total = listing?.total_datasets ?? 0

  return (
    <div className="vocab thm-view">
      <div className="thm-head">
        <p className="vocab-sec-sub">{t('themes:lead')}</p>
        <button
          type="button"
          className="btn btn--activity"
          onClick={() => setLocal({})}
          data-testid="new-theme"
        >
          {t('themes:newTheme')}
        </button>
      </div>

      {note && (
        <p className="lifecycle-ok" role="status" data-testid="theme-placed">
          {note}
        </p>
      )}

      {wizardOpen && (
        <div className="thm-overlay" role="dialog" aria-modal="true" aria-label={t('themes:wizard.aria')}>
          <div className="thm-panel">
            <ThemeWizard
              key={`${local?.resumeId ?? ''}|${local?.extend?.id ?? ''}|${fromHash ? 'h' : ''}`}
              initialQuestion={local ? '' : (fromHash?.question ?? '')}
              resumeId={local?.resumeId}
              extend={local?.extend}
              onClose={closeWizard}
              onPlaced={placed}
            />
          </div>
        </div>
      )}

      {!listing && !err && (
        <p className="loading-row">
          <span className="spinner" />
          {t('themes:loading')}
        </p>
      )}
      {err && (
        <>
          <p className="error">{t('themes:loadError')}</p>
          <pre className="error">{err}</pre>
          <p>
            <button type="button" className="btn btn--sm" onClick={reload}>
              {t('themes:reload')}
            </button>
          </p>
        </>
      )}

      {listing && listing.drafts.length > 0 && (
        <div className="card thm-drafts" data-testid="theme-drafts">
          <h3 className="thm-card-h">{t('themes:draft.title')}</h3>
          {listing.drafts.map((d) => (
            <div key={d.id} className="thm-draft-row">
              <span>{d.label}</span>
              <span className="vocab-field-hint">{t('themes:draft.step', { n: d.step })}</span>
              <button type="button" className="btn btn--sm" onClick={() => setLocal({ resumeId: d.id })}>
                {t('themes:draft.continue')}
              </button>
              <button type="button" className="btn btn--ghost btn--sm" onClick={() => void discardDraft(d)}>
                {t('themes:draft.discard')}
              </button>
            </div>
          ))}
        </div>
      )}

      {listing && listing.themes.length === 0 && listing.unassigned_questions.length === 0 && (
        <p className="ds-empty-note" data-testid="themes-empty">
          {t('themes:empty')}
        </p>
      )}

      {listing && (
        <div className="thm-grid">
          {listing.themes.map((theme) => (
            <ThemeCard
              key={theme.id}
              theme={theme}
              total={total}
              focused={focusTheme === theme.id}
              onAsk={onOpenAsk}
              onAddQuestion={(th) => setLocal({ extend: { id: th.id, label: th.label } })}
              onRemoved={reload}
            />
          ))}
          {listing.unassigned_questions.length > 0 && (
            <div className="card thm-card-theme" data-testid="unassigned-card">
              <h3 className="thm-card-h">{t('themes:unassigned.title')}</h3>
              <p className="vocab-field-hint">{t('themes:unassigned.note')}</p>
              {listing.unassigned_questions.map((q) => (
                <QuestionRow key={q.tool_name} q={q} total={total} onAsk={onOpenAsk} />
              ))}
            </div>
          )}
        </div>
      )}

      <Folds version={reloadKey} />
    </div>
  )
}
