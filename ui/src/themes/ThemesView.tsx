import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { MoreMenu } from '../MoreMenu'
import { SV_NS } from '../lineChoice'
import { homeHash } from '../sky/skyRoute'
import { parseVocabHash } from '../vocabQuestion'
import { ThemeWizard } from './ThemeWizard'
import {
  type CommitResult,
  type DraftSummary,
  type ThemeQuestion,
  type ThemeSummary,
  type ThemesListing,
  deleteDraft,
  type QuestionAnswer,
  deleteTheme,
  getThemeAnswers,
  listThemes,
  templateUrl,
  themeLabel,
} from './themesApi'
import '../vocabTerms.css'
import './themes.css'

// 「知りたいこと」（#/questions）— テーマごとの問いの帳。UI 再設計 PR 4（design.md §3）。
//
// テーマ（問いの束）ごとに カード: 問いの輪（答えるデータセット数／全体）・題・実行文・要る用語・
// 畳んだメモの問い。主ボタンは「問いを足す」だけで、答えを見る・型紙・テーマを外す は「…」。
// 右上の「？ 新しいテーマを作る」で 3 段（ThemeWizard）が開く。用語はテーマの持ち物ではないので、
// テーマを外しても用語・関係・問いは残る。用語の地図は「データ › データセットの詳細 › 中身」の末尾へ、
// 用語の一覧は無くした（用語は札 → 用語のページ）。

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

/** 知識の関係の名前 → 文（`themes:rel.sentence.*`）の鍵。 */
const SENTENCE_KEY: Record<string, string> = {
  isPartOf: 'part',
  lowers: 'lowers',
  raises: 'raises',
  affects: 'affects',
  subClassOf: 'sub',
  subPropertyOf: 'sub',
}

/** 知識の問いの答え（誰が・いつ・何をまとめて・なぜ）。「答えを見る」で開く。 */
function KnowledgeAnswer({ answer }: { answer: QuestionAnswer | undefined }) {
  const { t } = useTranslation()
  const items = answer?.knowledge?.items ?? []
  if (!answer) {
    return (
      <p className="loading-row thm-kanswer">
        <span className="spinner" />
        {t('themes:loading')}
      </p>
    )
  }
  if (items.length === 0) return <p className="ds-empty-note thm-kanswer">{t('themes:card.knowledgeNone')}</p>
  return (
    <ul className="thm-kanswer" data-testid="knowledge-answer">
      {items.map((it) => (
        <li key={`${it.iri}|${it.relation}|${it.via}`}>
          <div>
            {t(`themes:rel.sentence.${SENTENCE_KEY[it.relation] ?? 'sub'}`, { subject: it.label, object: it.via_label })}
          </div>
          <div className="thm-reason">
            {t(it.drafted_by === 'ai' ? 'themes:card.byAi' : 'themes:card.byHuman')}
            {it.at ? ` · ${it.at.slice(0, 10)}` : ''}
            {it.reason ? ` · ${it.reason}` : ''}
          </div>
        </li>
      ))}
    </ul>
  )
}

function QuestionRow({
  q,
  total,
  answers,
  onSeeAnswer,
}: {
  q: ThemeQuestion
  total: number
  /** 知識の問いの「答えを見る」（このテーマの答え。取れたら入る）。 */
  answers?: QuestionAnswer[] | null
  onSeeAnswer?: () => void
}) {
  const { t } = useTranslation()
  const knowledge = q.mode === 'knowledge'
  const [open, setOpen] = useState(false)
  const n = q.knowledge_count ?? 0
  const good = knowledge ? n > 0 || q.answering_datasets > 0 : q.answering_datasets > 0
  return (
    <div className="thm-qrow-wrap">
      <div className="thm-qrow" data-testid="theme-question">
        <Ring n={q.answering_datasets} total={total} />
        <span className="thm-qtitle">
          {q.title}
          {q.shape && <span className="thm-qshape">{q.shape}</span>}
        </span>
        <span className={`pill ${good ? 'ok' : 'wait'}`}>
          {knowledge
            ? t('themes:card.counts', { k: n, d: q.answering_datasets })
            : q.answering_datasets > 0
              ? t('themes:card.answering', { n: q.answering_datasets })
              : t('themes:card.notYet')}
        </span>
        {knowledge && (
          <button
            type="button"
            className="link-btn thm-see"
            aria-expanded={open}
            onClick={() => {
              if (!open) onSeeAnswer?.()
              setOpen(!open)
            }}
          >
            {t('themes:card.seeKnowledge')}
          </button>
        )}
      </div>
      {q.wanted_data && <div className="thm-qwant">{t('themes:review.wanted', { text: q.wanted_data })}</div>}
      {knowledge && open && <KnowledgeAnswer answer={answers?.find((a) => a.tool_name === q.tool_name)} />}
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
  onRemoved: (note: string) => void
}) {
  const { t, i18n } = useTranslation()
  const lang = i18n.language.startsWith('en') ? 'en' : 'ja'
  const [err, setErr] = useState('')
  // 知識の問いの答え（誰が・いつ・理由）。「答えを見る」を押したときだけ 1 度取る。
  const [answers, setAnswers] = useState<QuestionAnswer[] | null | undefined>(undefined)
  const n = theme.questions.length

  function loadAnswers() {
    if (answers !== undefined) return
    setAnswers(null)
    getThemeAnswers(theme.id, lang)
      .then(setAnswers)
      .catch((e) => {
        setAnswers([])
        setErr(e instanceof Error ? e.message : String(e))
      })
  }

  async function remove() {
    if (!window.confirm(t('themes:card.removeConfirm', { label: themeLabel(theme, t) }))) return
    setErr('')
    try {
      const res = await deleteTheme(theme.id)
      onRemoved(
        res
          ? t('themes:card.removed', {
              label: themeLabel(theme, t),
              concepts: res.removed_concepts.length,
              relations: res.removed_relations.length,
              kept: res.kept_concepts.length,
            })
          : '',
      )
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
      <div className="thm-card-h">
        <h3>{themeLabel(theme, t)}</h3>
        <span className={`pill ${n > 0 && theme.answerable === n ? 'ok' : 'wait'}`}>
          {t('themes:card.answerable', { ok: theme.answerable, n })}
        </span>
        {(theme.knowledge_total ?? 0) > 0 && (
          <span className="pill ok" data-testid="theme-knowledge-total">
            {t('themes:card.knowledgeTotal', { n: theme.knowledge_total })}
          </span>
        )}
        <MoreMenu
          label={t('themes:card.more')}
          items={[
            {
              key: 'answer',
              label: t('themes:card.seeAnswer'),
              disabled: !firstAnswerable,
              onSelect: () => firstAnswerable && onAsk(firstAnswerable.title),
            },
            { key: 'template', label: t('themes:card.template'), href: templateUrl(theme.id), download: true },
            { key: 'remove', label: t('themes:card.remove'), onSelect: () => void remove() },
          ]}
        />
      </div>
      {theme.questions.map((q) => (
        <QuestionRow key={q.tool_name} q={q} total={total} answers={answers} onSeeAnswer={loadAnswers} />
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
              {term.kind === 'property' && (
                <span className="vocab-field-hint"> {t('themes:kind.property')}</span>
              )}
            </a>
          ))}
        </p>
      )}
      {theme.memo_questions.length > 0 && (
        <details className="thm-memos" data-testid="theme-memos">
          <summary>{t('themes:card.memoTitle', { n: theme.memo_questions.length })}</summary>
          {theme.memo_questions.map((m) => (
            <div key={m.id} className="thm-memo-row">
              <span className="pill wait">{t('themes:card.memoBadge')}</span> {m.title}
              {m.reason && <span className="vocab-field-hint"> — {m.reason}</span>}
              {m.wanted_data && <div className="thm-qwant">{t('themes:review.wanted', { text: m.wanted_data })}</div>}
            </div>
          ))}
        </details>
      )}
      <div className="thm-card-actions">
        <button type="button" className="btn btn--sm" onClick={() => onAddQuestion(theme)}>
          {t('themes:card.addQuestion')}
        </button>
      </div>
      {err && <p className="promote-err">{err}</p>}
    </div>
  )
}

type WizardOpen = { resumeId?: string; extend?: { id: string; label: string } }

export function ThemesView({
  newQuestion,
  focusTheme,
  onCloseNew,
  onOpenAsk,
}: {
  /** `#/questions?new=1[&q=題]` — 3 段を開く（上部の「？ 知りたいことを書く」・質問するから）。 */
  newQuestion?: boolean
  /** `#/questions/<theme>` — そのテーマのカードを見せる。 */
  focusTheme?: string
  /** `new` を hash から外す（3 段を閉じた・置いたとき）。 */
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
  // 3 段は、hash の `new=1`（題つき）か、この画面で開いた下書き・「問いを足す」で開く。
  const fromHash = newQuestion
    ? (({ q, resume }) => ({ question: q, resume }))(parseVocabHash(window.location.hash))
    : null
  const wizardOpen = fromHash !== null || local !== null

  function closeWizard() {
    setLocal(null)
    if (newQuestion) onCloseNew()
    reload()
  }

  // 置いたら星図へ（置いた概念の札を 1 度だけ灯す）。
  function placed(result: CommitResult) {
    setLocal(null)
    if (newQuestion) onCloseNew()
    goTo(homeHash({ newIris: result.minted.map((slug) => `${SV_NS}${slug}`) }))
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
        <p className="lifecycle-ok" role="status" data-testid="theme-removed">
          {note}
        </p>
      )}

      {wizardOpen && (
        <div className="thm-overlay" role="dialog" aria-modal="true" aria-label={t('themes:wizard.aria')}>
          <div className="thm-panel">
            <ThemeWizard
              key={`${local?.resumeId ?? fromHash?.resume ?? ''}|${local?.extend?.id ?? ''}|${fromHash ? 'h' : ''}`}
              initialQuestion={local ? '' : (fromHash?.question ?? '')}
              resumeId={local?.resumeId ?? (fromHash?.resume || undefined)}
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
              onRemoved={(msg) => {
                setNote(msg)
                reload()
              }}
            />
          ))}
          {listing.unassigned_questions.length > 0 && (
            <div className="card thm-card-theme" data-testid="unassigned-card">
              <h3 className="thm-card-h">{t('themes:unassigned.title')}</h3>
              {listing.unassigned_questions.map((q) => (
                <QuestionRow key={q.tool_name} q={q} total={total} />
              ))}
            </div>
          )}
        </div>
      )}

    </div>
  )
}
