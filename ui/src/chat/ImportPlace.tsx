// 「取り込み」の場所の中央（PR 14d-1・14d-2b）。窓口 2 枚（Graphium とつなぐ・ファイルを置く）→ 進み →
// 結果の 1 枚 → 取り込んだものの一覧。状態の動きは importPlace/importJob.ts、部品は importPlace/ImportViews.tsx。
import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { isAppSettingsServerMode } from '../appSettings'
import type { LlmCredentials } from '../settings/store'
import type { SkyDataset } from '../sky/skyApi'
import { SourcePicker } from './SourcePicker'
import { ATTACH_EXTS } from './sourcePickerUtil'
import { importRows, type ImportRow } from './importRows'
import { graphiumState, prepareGraphium, registeredServers, savedGraphiumInput, type ConnectFailure } from './importPlace/graphiumConnect'
import { GraphiumCard, ImportNotice, ImportProgress, ImportResultCard, ImportSources } from './importPlace/ImportViews'
import { startMcpImport } from './importPlace/knowledgeImportApi'
import { useKnowledgeImport } from './importPlace/useKnowledgeImport'
import { GRAPHIUM_PRESET } from '../settings/mcpPresets'
import './importPlace/importPlace.css'

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
  /** 「作る AI」の座標（関係を選ぶ段だけが使う）。 */
  getCreds?: () => LlmCredentials | null
  /** 取り込み・外すで知識が変わった（ChatApp が図を読み直す）。 */
  onChanged?: () => void
  /** 結果の問いを押した: チャットに移ってその文を送る。 */
  onAsk?: (text: string) => void
  /** 「知識グラフを見る」。 */
  onGraph?: () => void
  onOpenSettings?: () => void
}

const noop = () => {}

export function ImportPlace(p: ImportPlaceProps) {
  const { t } = useTranslation('importPlace')
  const { t: tc } = useTranslation('chat')
  const fileInput = useRef<HTMLInputElement>(null)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [, setSettingsTick] = useState(0)
  const [form, setForm] = useState({ script: '', root: '' })
  const [failure, setFailure] = useState<ConnectFailure | null>(null)
  const [confirming, setConfirming] = useState<string | null>(null)
  const imp = useKnowledgeImport(p.onChanged ?? noop)
  const onOpenSettings = p.onOpenSettings ?? noop

  const sources = imp.sources ?? []
  const copied = new Set(sources.map((s) => s.dataset_id).filter((x): x is string => !!x))
  const rows = importRows(
    p.datasets.filter((d) => !copied.has(d.id)),
    p.proposed.filter((d) => !copied.has(d.id)),
  )
  const running = imp.job.phase === 'running' || imp.working
  const state = graphiumState()
  const connected = state === 'on'

  const begin = (server: string) => async () => startMcpImport(server, p.lang, p.getCreds?.() ?? null)

  async function goGraphium() {
    setFailure(null)
    const fail = await prepareGraphium(state, form)
    setSettingsTick((n) => n + 1) // 「つなぐ」で設定が変わった
    if (fail) {
      setFailure(fail)
      return
    }
    await imp.start(GRAPHIUM_PRESET.serverName, begin(GRAPHIUM_PRESET.serverName))
  }

  async function rerun(server: string) {
    setFailure(null)
    const fail = await prepareGraphium('on', form)
    if (fail) {
      setFailure(fail)
      return
    }
    await imp.start(server, begin(server))
  }

  const failureText = (f: ConnectFailure): string =>
    f.code === 'http' ? f.detail : t(`graphium.fail.${f.code}`, { tool: f.tool ?? '' })
  const errorLine = failure ? failureText(failure) : imp.startError

  const result = imp.job.phase === 'done' ? imp.job.result : null
  const empty = sources.length === 0 && rows.length === 0

  return (
    <div className="import-place" data-testid="import-place">
      <h1 className="import-title">{t('title')}</h1>
      <p className="import-lead">{t('lead')}</p>

      <div className="imp-cards">
        {isAppSettingsServerMode() && (
          <GraphiumCard
            connected={connected}
            busy={running}
            error={errorLine}
            form={!connected && savedGraphiumInput() === null ? form : null}
            onForm={(patch) => setForm((f) => ({ ...f, ...patch }))}
            onGo={() => void goGraphium()}
            onOpenSettings={onOpenSettings}
          />
        )}
        <section className="imp-card" data-testid="import-attach">
          <h2 className="imp-card-title">{t('attach.title')}</h2>
          {p.noKey ? (
            <p className="chat-note-line" data-testid="import-nokey">
              {tc('list.noKey')}
            </p>
          ) : (
            <>
              <p className="imp-card-body">{t('attach.body')}</p>
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
                <button type="button" className="btn btn--sm btn--ghost" data-testid="import-attach-local" disabled={p.busy} onClick={() => fileInput.current?.click()}>
                  {tc('compose.attachLocal')}
                </button>
                {p.sourceHome && (
                  <button type="button" className="btn btn--sm btn--ghost" data-testid="import-attach-source" disabled={p.busy} onClick={() => setPickerOpen(true)}>
                    {tc('compose.attachFrom', { server: p.sourceHome })}
                  </button>
                )}
                {p.busy && (
                  <span className="chat-note-line" role="status" data-testid="import-reading">
                    {tc('attach.reading')}
                  </span>
                )}
              </div>
              {pickerOpen && p.sourceHome && (
                <SourcePicker server={p.sourceHome} onAttach={p.onAttachFromSource} onClose={() => setPickerOpen(false)} />
              )}
            </>
          )}
        </section>
      </div>

      {imp.job.phase === 'running' && <ImportProgress job={imp.job} canCancel={imp.jobId !== null} onCancel={imp.cancel} />}
      {(imp.job.phase === 'failed' || imp.job.phase === 'cancelled') && <ImportNotice job={imp.job} onDismiss={imp.dismiss} />}
      {result && (
        <ImportResultCard
          result={result}
          name={result.title || result.source}
          lang={p.lang}
          onAsk={p.onAsk ?? noop}
          onGraph={p.onGraph ?? noop}
          onClose={imp.dismiss}
          onOpenSettings={onOpenSettings}
        />
      )}

      <h2 className="import-datasets-title">{t('sources.title')}</h2>
      {(sources.length > 0 || (p.status === 'ok' && rows.length > 0)) && (
        <ImportSources
          sources={sources}
          lang={p.lang}
          jobSource={imp.job.phase === 'running' ? imp.job.source : null}
          rerunnable={registeredServers()}
          confirming={confirming}
          busy={running}
          rows={p.status === 'ok' ? rows : []}
          onOpen={p.onOpenDataset}
          onRerun={(s) => void rerun(s)}
          onAskRemove={setConfirming}
          onRemove={(s) => {
            setConfirming(null)
            void imp.remove(s)
          }}
        />
      )}
      {imp.listError && (
        <p className="promote-err" role="alert" data-testid="import-list-error">
          {imp.listError}
        </p>
      )}
      {p.status === 'loading' ? (
        <p className="chat-note-line">{tc('first.loading')}</p>
      ) : p.status === 'error' ? (
        <p className="promote-err" role="alert">
          {tc('chart.failed')}{' '}
          <button type="button" className="btn btn--sm btn--ghost" onClick={p.onRetry}>
            {tc('chart.retry')}
          </button>
        </p>
      ) : (
        empty && (
          <p className="chat-note-line" data-testid="import-empty">
            {t('sources.empty')}
          </p>
        )
      )}
    </div>
  )
}

/** 一覧の行。行そのものがボタンで、押すとそのデータセットの id を渡す（行に別のボタンは置かない）。 */
export function ImportRowList({ rows, lang, onOpen }: { rows: ImportRow[]; lang: 'ja' | 'en'; onOpen: (id: string) => void }) {
  const { t: tc } = useTranslation('chat')
  const num = (n: number) => n.toLocaleString(lang)
  return (
    <ul className="import-rows">
      {rows.map((r) => (
        <li key={r.id}>
          <button type="button" className="import-row" data-testid="import-dataset-row" onClick={() => onOpen(r.id)}>
            <span className="import-row-name">{r.name}</span>
            {r.paper && <span className="import-row-tag">{tc('import.paper')}</span>}
            {!r.published && <span className="import-row-tag import-row-tag--soft">{tc('import.unpublished')}</span>}
            <span className="import-row-meta">
              {r.kinds !== null && r.rows !== null && tc('import.row', { kinds: num(r.kinds), rows: num(r.rows) })}
              {r.created && <span className="import-row-date">{r.created.toLocaleDateString(lang)}</span>}
            </span>
          </button>
        </li>
      ))}
    </ul>
  )
}
