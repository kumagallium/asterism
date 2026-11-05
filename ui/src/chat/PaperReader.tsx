// 論文の本文（13b・design.md §22）。根拠の文を押すと、会話の列の代わりに出る。
// 節ごとの題名（無ければ「節 n」）・段落ごとに文をつなげて読める形。根拠になっている文は薄い下線、
// 開いたきっかけの文（focus）は背景色で強調して中央へ送る。根拠の文を押すと、その文を根拠に持つ関係を
// 知識グラフで濃くする（論文の点 `paper:<id>` の文から引く）。取得はこの部品の中（失敗・未公開は呼び手へ）。
import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ThemesApiError } from '../themes/themesApi'
import { getPaper, getPoint, type PaperText, type Point } from './chatApi'
import type { Highlight } from './chatState'

export interface PaperReaderProps {
  datasetId: string
  /** 強調して送る文の IRI（無ければ先頭のまま）。 */
  focus: string | null
  lang: 'ja' | 'en'
  onBack: () => void
  onHighlight: (h: Highlight | null) => void
  /** 公開されていない（404）。呼び手が閉じて、札の文を押せない見た目にする。 */
  onUnavailable: (datasetId: string) => void
}

type Load = { status: 'loading' } | { status: 'error'; message: string } | { status: 'ok'; paper: PaperText }

export function PaperReader({ datasetId, focus, lang, onBack, onHighlight, onUnavailable }: PaperReaderProps) {
  const [load, setLoad] = useState<Load>({ status: 'loading' })
  const unavailableRef = useRef(onUnavailable)
  useEffect(() => {
    unavailableRef.current = onUnavailable
  })

  useEffect(() => {
    const ac = new AbortController()
    getPaper(datasetId, lang, ac.signal)
      .then((paper) => setLoad({ status: 'ok', paper }))
      .catch((e: unknown) => {
        if (ac.signal.aborted) return
        if (e instanceof ThemesApiError && e.status === 404) unavailableRef.current(datasetId)
        else setLoad({ status: 'error', message: e instanceof Error ? e.message : String(e) })
      })
    return () => ac.abort()
  }, [datasetId, lang])

  const paper = load.status === 'ok' ? load.paper : null
  const pointOnce = useRef<Promise<Point> | null>(null)
  useEffect(() => {
    pointOnce.current = null
  }, [datasetId])

  /** 根拠の文を押す: その文を根拠に持つ関係の線を知識グラフで濃くする（論文の点 `paper:<id>` の文から引く）。 */
  const showRelation = (iri: string) => {
    pointOnce.current ??= getPoint({ iri: `paper:${datasetId}` }, lang)
    pointOnce.current.then(
      (pt) => {
        const s = pt.sentences.find((x) => x.source && x.target && (x.evidence ?? []).some((e) => e.iri === iri))
        if (s) onHighlight({ edge: { from: s.source, to: s.target } })
      },
      () => {
        pointOnce.current = null
      },
    )
  }
  return <PaperReaderView paper={paper} loading={load.status === 'loading'} error={load.status === 'error' ? load.message : null} focus={focus} onBack={onBack} onSentence={showRelation} />
}

export interface PaperReaderViewProps {
  paper: PaperText | null
  loading?: boolean
  error?: string | null
  focus: string | null
  onBack: () => void
  /** 根拠になっている文を押した。 */
  onSentence: (iri: string) => void
}

/** 本文の描画だけ（取得は :func:`PaperReader`）。 */
export function PaperReaderView({ paper, loading, error, focus, onBack, onSentence: showRelation }: PaperReaderViewProps) {
  const { t } = useTranslation('chat')
  const focusRef = useRef<HTMLSpanElement | null>(null)
  const evidence = useMemo(() => new Set(paper?.evidence ?? []), [paper])
  useEffect(() => {
    if (paper) focusRef.current?.scrollIntoView?.({ block: 'center' })
  }, [paper, focus])
  return (
    <div className="chat-reader" data-testid="paper-reader">
      <div className="chat-reader-head">
        <button type="button" className="link-btn" data-testid="reader-back" onClick={onBack}>
          {t('reader.back')}
        </button>
        <h2 className="chat-reader-title">{paper?.label ?? ''}</h2>
      </div>
      <div className="chat-reader-body">
        {loading && <p className="chat-note-line">{t('reader.loading')}</p>}
        {error && (
          <p className="promote-err" role="alert">
            {t('reader.failed')}: {error}
          </p>
        )}
        {paper && paper.sections.length === 0 && <p className="chat-note-line">{t('reader.empty')}</p>}
        {paper?.sections.map((sec, i) => {
          const Heading = sec.depth === 0 ? 'h3' : 'h4'
          return (
            <section key={sec.iri} className="chat-reader-sec" data-depth={sec.depth} style={sec.depth > 0 ? { marginLeft: `${Math.min(sec.depth, 3) * 0.75}rem` } : undefined}>
              <Heading className="chat-reader-sec-title" data-testid="reader-section-title">
                {sec.title ?? t('reader.sectionN', { n: i + 1 })}
              </Heading>
              {sec.paragraphs.map((p) => (
                <p key={p.iri} className="chat-reader-para">
                  {p.sentences.map((s, j) => {
                    const isEv = evidence.has(s.iri)
                    const isFocus = s.iri === focus
                    const cls = `chat-reader-sent${isEv ? ' is-evidence' : ''}${isFocus ? ' is-focus' : ''}`
                    return (
                      <span key={s.iri}>
                        {j > 0 ? ' ' : ''}
                        {isEv ? (
                          <span
                            ref={isFocus ? focusRef : undefined}
                            className={cls}
                            role="button"
                            tabIndex={0}
                            data-testid="reader-sentence"
                            data-evidence="true"
                            data-focus={isFocus ? 'true' : undefined}
                            title={t('reader.seeRelation')}
                            onClick={() => showRelation(s.iri)}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter' || e.key === ' ') {
                                e.preventDefault()
                                showRelation(s.iri)
                              }
                            }}
                          >
                            {s.text}
                          </span>
                        ) : (
                          <span ref={isFocus ? focusRef : undefined} className={cls} data-testid="reader-sentence" data-focus={isFocus ? 'true' : undefined}>
                            {s.text}
                          </span>
                        )}
                      </span>
                    )
                  })}
                </p>
              ))}
            </section>
          )
        })}
        {paper?.truncated && <p className="chat-note-line">{t('reader.truncated', { n: paper.sections.reduce((n, s) => n + s.paragraphs.reduce((m, p) => m + p.sentences.length, 0), 0) })}</p>}
      </div>
    </div>
  )
}
