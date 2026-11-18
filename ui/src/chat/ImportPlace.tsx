// 「取り込み」の場所の中央（PR 14d-1・14d-2b）。窓口 2 枚（Graphium とつなぐ・ファイルを置く）→ 進み →
// 結果の 1 枚 → 取り込んだものの一覧。状態の動きは importPlace/importJob.ts、部品は importPlace/ImportViews.tsx。
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { isAppSettingsServerMode } from '../appSettings'
import type { LlmCredentials } from '../settings/store'
import type { SkyDataset } from '../sky/skyApi'
import { SourcePicker } from './SourcePicker'
import { ATTACH_EXTS } from './sourcePickerUtil'
import { importRows, type ImportRow } from './importRows'
import { isRegistered, prepareGraphium, rerunnableServers, type ConnectFailure } from './importPlace/graphiumConnect'
import { GraphiumCard, ImportListNotice, ImportNotice, ImportProgress, ImportResultCard, ImportSources } from './importPlace/ImportViews'
import { importStore, isBusy, useImportStore } from './importPlace/importStore'
import { startMcpImport } from './importPlace/knowledgeImportApi'
import { GRAPHIUM_PRESET, locatePreset } from '../settings/mcpPresets'
import './importPlace/importPlace.css'

/** 走っている作業が一覧にあるとき、読み直す間隔（ms）。 */
export const POLL_MS = 4000

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
  /** 結果の問いを押した: チャットに移って、AI を呼ばずに台帳で答える（入口 1）。 */
  onAsk?: (q: { source: string; name: string; text: string }) => void
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
  const [form, setForm] = useState<{ script: string | null; root: string | null }>({ script: null, root: null })
  const [userHome, setUserHome] = useState('')
  // 起動ファイル・vault を機械で探した結果（undefined=探している最中・null=探せなかった）
  const [loc, setLoc] = useState<{ script: string | null; root: string | null } | null | undefined>(undefined)
  const [editing, setEditing] = useState(false)
  const [confirming, setConfirming] = useState<string | null>(null)
  const st = useImportStore()
  const onOpenSettings = p.onOpenSettings ?? noop
  const registered = isRegistered()
  const serverMode = isAppSettingsServerMode()

  // 取り込みの置き場は部品の外にある。ここでは「最後に渡された関数」を渡し、一覧を読み、走っている間は読み直すだけ。
  useEffect(() => {
    importStore.setChanged(p.onChanged ?? noop)
  }, [p.onChanged])
  useEffect(() => {
    void importStore.reload()
  }, [])
  const sources = st.sources ?? []
  const anyRunning = sources.some((s) => s.running !== null)
  const jobLive = st.job.phase === 'running' || st.job.phase === 'preparing'
  useEffect(() => {
    if (!anyRunning || jobLive) return
    const id = setInterval(() => void importStore.poll(), POLL_MS)
    return () => clearInterval(id)
  }, [anyRunning, jobLive])
  // 起動ファイル・vault を機械で探す（設定に登録が無いときだけ）。ホームは入力欄の `~/` のためにも使う
  useEffect(() => {
    if (registered || !serverMode) return
    let live = true
    locatePreset(GRAPHIUM_PRESET)
      .then((r) => {
        if (!live) return
        setUserHome(r.userHome)
        setLoc({ script: r.script, root: r.root })
      })
      .catch(() => live && setLoc(null))
    return () => {
      live = false
    }
  }, [registered, serverMode])

  const copied = new Set(sources.map((s) => s.dataset_id).filter((x): x is string => !!x))
  const rows = importRows(
    p.datasets.filter((d) => !copied.has(d.id)),
    p.proposed.filter((d) => !copied.has(d.id)),
  )
  const busy = isBusy(st)
  const scriptShown = form.script ?? loc?.script ?? ''
  const rootShown = form.root ?? loc?.root ?? (userHome ? GRAPHIUM_PRESET.defaultRoot(userHome) : '')
  const searching = !registered && loc === undefined && serverMode
  const foundBoth = !!loc?.script && !!loc?.root
  const showScript = editing || !loc?.script
  const showRoot = editing || !loc?.root

  /** 準備（設定・道具の確認）→ POST。失敗は理由（画面が文にする）。 */
  const begin = (server: string) => async () => {
    const fail = await prepareGraphium({ script: scriptShown, root: rootShown }, userHome)
    if (fail) return { ok: false as const, failure: fail }
    return { ok: true as const, jobId: await startMcpImport(server, p.lang, p.getCreds?.() ?? null) }
  }
  const startGraphium = (server: string) => void importStore.start(server, begin(server))

  const failureText = (f: ConnectFailure): string => (f.code === 'http' ? f.detail : t(`graphium.fail.${f.code}`, { tool: f.tool ?? '' }))
  const errorLine = st.startError ? failureText(st.startError) : null
  const job = st.job
  const shownJob =
    job.phase === 'preparing' ? ({ phase: 'running', source: job.source, stage: null, done: null, total: null } as const) : job.phase === 'running' ? job : null

  const result = job.phase === 'done' ? job.result : null
  const empty = sources.length === 0 && rows.length === 0

  return (
    <div className="import-place" data-testid="import-place">
      <h1 className="import-title">{t('title')}</h1>
      <p className="import-lead">{t('lead')}</p>

      <div className="imp-cards">
        {isAppSettingsServerMode() && (
          <GraphiumCard
            connected={registered}
            busy={busy}
            error={errorLine}
            searching={searching}
            form={registered || (!editing && foundBoth) ? null : { script: scriptShown, root: rootShown }}
            showScript={showScript}
            showRoot={showRoot}
            scriptMissing={!loc?.script}
            found={!editing && foundBoth ? { script: loc?.script ?? '', root: loc?.root ?? '' } : null}
            onEdit={() => setEditing(true)}
            onForm={(patch) => setForm((f) => ({ ...f, ...patch }))}
            onGo={() => startGraphium(GRAPHIUM_PRESET.serverName)}
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
                    {t('attach.from', { name: p.sourceHome === GRAPHIUM_PRESET.serverName ? 'Graphium' : p.sourceHome })}
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

      {shownJob && <ImportProgress job={shownJob} canCancel={st.jobId !== null} aiSet={!!p.getCreds?.()} onCancel={importStore.cancel} />}
      {(job.phase === 'failed' || job.phase === 'cancelled') && <ImportNotice job={job} onDismiss={importStore.dismiss} />}
      {result && (
        <ImportResultCard
          result={result}
          name={result.title || result.source}
          lang={p.lang}
          onAsk={p.onAsk ?? noop}
          onGraph={p.onGraph ?? noop}
          onClose={importStore.dismiss}
          onOpenSettings={onOpenSettings}
        />
      )}

      <h2 className="import-datasets-title">{t('sources.title')}</h2>
      {st.notice && <ImportListNotice notice={st.notice} onDismiss={importStore.dismissNotice} />}
      {(sources.length > 0 || (p.status === 'ok' && rows.length > 0)) && (
        <ImportSources
          sources={sources}
          lang={p.lang}
          jobSource={job.phase === 'running' || job.phase === 'preparing' ? job.source : null}
          rerunnable={rerunnableServers()}
          confirming={confirming}
          busy={busy}
          rows={p.status === 'ok' ? rows : []}
          onOpen={p.onOpenDataset}
          onRerun={startGraphium}
          onAskRemove={setConfirming}
          onRemove={(s) => {
            setConfirming(null)
            void importStore.remove(s)
          }}
        />
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
