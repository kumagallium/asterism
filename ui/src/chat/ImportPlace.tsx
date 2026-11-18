// 「取り込み」の場所の中央（PR 14d-1・契約 §5.1）。入口の札（ファイルを添える）＋データセットの一覧。
// 進み・結果の 1 枚・Graphium とつなぐは置かない（14d-2）。行にボタンは足さない。
import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { SkyDataset } from '../sky/skyApi'
import { SourcePicker } from './SourcePicker'
import { ATTACH_EXTS } from './sourcePickerUtil'
import { importRows, type ImportRow } from './importRows'

export interface ImportPlaceProps {
  /** 公開済み。 */
  datasets: SkyDataset[]
  /** 取り込み済みで未公開。 */
  proposed: SkyDataset[]
  /** 図（/api/sky）の読み取り。 */
  status: 'loading' | 'error' | 'ok'
  onRetry: () => void
  /** 送信中はボタンを止める。 */
  busy: boolean
  /** 鍵が無い: 入口の代わりに 1 行。 */
  noKey: boolean
  /** 取り込み元の MCP サーバ名（設定済みのときだけ 2 つ目の入口）。 */
  sourceHome: string | null
  onAttach: (files: File[]) => void
  onAttachFromSource: (ids: string[], names: string[]) => Promise<boolean>
  onOpenDataset: (id: string) => void
  lang: 'ja' | 'en'
}

export function ImportPlace(p: ImportPlaceProps) {
  const { t } = useTranslation('chat')
  const fileInput = useRef<HTMLInputElement>(null)
  const [pickerOpen, setPickerOpen] = useState(false)
  const rows = importRows(p.datasets, p.proposed)

  return (
    <div className="import-place" data-testid="import-place">
      <h1 className="import-title">{t('import.title')}</h1>
      <p className="import-lead">{t('import.lead')}</p>

      {p.noKey ? (
        <p className="chat-note-line" data-testid="import-nokey">
          {t('list.noKey')}
        </p>
      ) : (
        <section className="import-entry">
          <h2 className="import-entry-title">{t('import.attachTitle')}</h2>
          <p className="import-entry-body">{t('import.attachBody')}</p>
          <div className="import-entry-actions">
            <input
              ref={fileInput}
              type="file"
              multiple
              hidden
              data-testid="import-attach-input"
              accept={ATTACH_EXTS.join(',')}
              onChange={(e) => {
                const files = Array.from(e.target.files ?? [])
                e.target.value = ''
                if (files.length > 0) p.onAttach(files)
              }}
            />
            <button type="button" className="btn btn--sm" data-testid="import-attach-local" disabled={p.busy} onClick={() => fileInput.current?.click()}>
              {t('compose.attachLocal')}
            </button>
            {p.sourceHome && (
              <button type="button" className="btn btn--sm" data-testid="import-attach-source" disabled={p.busy} onClick={() => setPickerOpen(true)}>
                {t('compose.attachFrom', { server: p.sourceHome })}
              </button>
            )}
            {p.busy && (
              <span className="chat-note-line" role="status" data-testid="import-reading">
                {t('attach.reading')}
              </span>
            )}
          </div>
          {pickerOpen && p.sourceHome && (
            <SourcePicker server={p.sourceHome} onAttach={p.onAttachFromSource} onClose={() => setPickerOpen(false)} />
          )}
        </section>
      )}

      <h2 className="import-datasets-title">{t('import.datasets')}</h2>
      {p.status === 'loading' ? (
        <p className="chat-note-line">{t('first.loading')}</p>
      ) : p.status === 'error' ? (
        <p className="promote-err" role="alert">
          {t('chart.failed')}{' '}
          <button type="button" className="btn btn--sm" onClick={p.onRetry}>
            {t('chart.retry')}
          </button>
        </p>
      ) : rows.length === 0 ? (
        <p className="chat-note-line" data-testid="import-empty">
          {t('import.empty')}
        </p>
      ) : (
        <ImportRowList rows={rows} lang={p.lang} onOpen={p.onOpenDataset} />
      )}
    </div>
  )
}

/** 一覧の行。行そのものがボタンで、押すとそのデータセットの id を渡す（行に別のボタンは置かない）。 */
export function ImportRowList({ rows, lang, onOpen }: { rows: ImportRow[]; lang: 'ja' | 'en'; onOpen: (id: string) => void }) {
  const { t } = useTranslation('chat')
  const num = (n: number) => n.toLocaleString(lang)
  return (
    <ul className="import-rows">
      {rows.map((r) => (
        <li key={r.id}>
          <button type="button" className="import-row" data-testid="import-dataset-row" onClick={() => onOpen(r.id)}>
            <span className="import-row-name">{r.name}</span>
            {r.paper && <span className="import-row-tag">{t('import.paper')}</span>}
            {!r.published && <span className="import-row-tag import-row-tag--soft">{t('import.unpublished')}</span>}
            <span className="import-row-meta">
              {r.kinds !== null && r.rows !== null && t('import.row', { kinds: num(r.kinds), rows: num(r.rows) })}
              {r.created && <span className="import-row-date">{r.created.toLocaleDateString(lang)}</span>}
            </span>
          </button>
        </li>
      ))}
    </ul>
  )
}
