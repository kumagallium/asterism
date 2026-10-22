// 「中身」（PR 11k）: 地図（TBox）の下。押した点の実データ（ABox）を表で出す。
// 折りたたみは localStorage に覚える（既定は開く）。表は ReplyCard の ReplyTableView（チャットの表と同じ見え方）。
import { lazy, Suspense, useCallback, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronIcon } from '../icons'
import type { Highlight } from './chatState'
import { readOpen, writeOpen } from './localOpen'
import { ReplyTableView } from './ReplyCard'
import { UNPUBLISHED, type ContentsTarget } from './contentsKey'
import type { ContentsState } from './useContents'

// sigma（WebGL）は「中身まで見る」を開くときだけ読む。
const NetworkView = lazy(() => import('../NetworkView').then((m) => ({ default: m.NetworkView })))

export interface ContentsPanelProps {
  /** 押したもの（null＝何も押さず、📎 も無い）。 */
  target: ContentsTarget | null
  state: ContentsState
  /** 押した点の名前（見出し用。無ければ API の title）。 */
  name?: string
  onHighlight?: (h: Highlight | null) => void
}

function bodyOf(t: (k: string, o?: Record<string, unknown>) => string, loading: string, target: ContentsTarget | null, state: ContentsState) {
  if (target === UNPUBLISHED) return <p className="chat-note-line">{t('contents.unpublished')}</p>
  if (!target || state.status === 'idle') return <p className="chat-note-line">{t('contents.hint')}</p>
  if (state.status === 'loading') return <p className="chat-note-line" role="status">{loading}</p>
  if (state.status === 'error') return <p className="chat-note-line" role="alert">{t('chart.failed')}</p>
  return null
}

export function ContentsPanel({ target, state, name, onHighlight }: ContentsPanelProps) {
  const { t } = useTranslation('chat')
  const { t: tsky } = useTranslation('sky')
  const [open, setOpen] = useState(readOpen)
  const [deep, setDeep] = useState(false)
  const toggle = useCallback(() => {
    setOpen((cur) => {
      writeOpen(!cur)
      return !cur
    })
  }, [])
  const data = state.status === 'ok' ? state.data : null
  const title = name || data?.title || ''
  const label = t('contents.toggle')
  const shown = target !== UNPUBLISHED && !!target && !!data
  const rowsTotal = data ? data.tables.reduce((n, x) => n + x.table.rows.length, 0) : 0

  if (deep) {
    return (
      <section className="chat-contents" data-testid="contents-panel">
        <div className="chat-contents-head">
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => setDeep(false)}>
            ← {t('chart.back')}
          </button>
        </div>
        <Suspense fallback={null}>
          <NetworkView />
        </Suspense>
      </section>
    )
  }

  return (
    <section className="chat-contents" data-testid="contents-panel">
      <div className="chat-contents-head">
        <h3>{title ? t('contents.of', { name: title }) : t('contents.title')}</h3>
        <button type="button" className="link-btn chat-contents-full" data-testid="contents-full" onClick={() => setDeep(true)}>
          {t('contents.full')}
        </button>
        <button type="button" className="chat-icon-btn" data-testid="contents-toggle" aria-expanded={open} aria-label={label} title={label} onClick={toggle}>
          <ChevronIcon size={16} className={open ? 'chat-contents-chevron is-open' : 'chat-contents-chevron'} />
        </button>
      </div>
      <div hidden={!open} className="chat-contents-body">
        {shown && data ? (
          rowsTotal === 0 ? (
            <p className="chat-note-line">{data.note ?? (target && typeof target === 'object' && 'session' in target ? t('contents.hint') : t('contents.empty'))}</p>
          ) : (
            <>
              {data.tables.map((x) => (
                <div key={`${x.dataset.id}|${x.table.kind.iri}`} className="chat-contents-table" data-testid="contents-table">
                  <h4>{x.dataset.label}</h4>
                  <ReplyTableView table={x.table} onHighlight={onHighlight} />
                </div>
              ))}
              {data.more > 0 && <p className="chat-note-line">{t('contents.moreTables', { n: data.more })}</p>}
            </>
          )
        ) : (
          bodyOf(t, tsky('state.loading'), target, state)
        )}
      </div>
    </section>
  )
}
