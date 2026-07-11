import { useEffect, useState } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import { ToolRunner } from './ToolRunner'
import { useLlmSettings } from './settings/context'
import { LlmGate } from './settings/LlmGate'
import {
  deleteTool,
  listTools,
  PARAM_TYPES,
  type ParamType,
  proposeTool,
  type QueryTool,
  type QueryToolParam,
  saveTool,
} from './toolsApi'

// Listing/saving/deleting tools need no key; the AI draft (propose) uses the
// active model from Settings (shared across the app, never persisted to disk).

interface ResultRow {
  key: string
  var: string
  number: boolean
}
/** The editable form behind a draft tool (decoupled from the wire toolDict). */
interface ToolForm {
  name: string
  title: string
  description: string
  query: string
  parameters: QueryToolParam[]
  resultRows: ResultRow[]
}

const EMPTY_FORM: ToolForm = {
  name: '',
  title: '',
  description: '',
  query: '',
  parameters: [],
  resultRows: [],
}

function toForm(t: QueryTool): ToolForm {
  const resultRows: ResultRow[] = []
  for (const [key, spec] of Object.entries(t.result?.item ?? {})) {
    if (typeof spec === 'string') resultRows.push({ key, var: spec, number: false })
    else resultRows.push({ key, var: spec.var, number: !!spec.number })
  }
  return {
    name: t.name ?? '',
    title: t.title ?? '',
    description: t.description ?? '',
    query: t.query ?? '',
    parameters: (t.parameters ?? []).map((p) => ({
      name: p.name ?? '',
      type: (PARAM_TYPES.includes(p.type) ? p.type : 'string') as ParamType,
      required: !!p.required,
      description: p.description ?? '',
      default: p.default,
      minimum: p.minimum,
      maximum: p.maximum,
      enum: p.enum,
    })),
    resultRows,
  }
}

function fromForm(f: ToolForm): QueryTool {
  const parameters: QueryToolParam[] = f.parameters.map((p) => {
    const out: QueryToolParam = { name: p.name.trim(), type: p.type }
    if (p.required) out.required = true
    if (p.description?.trim()) out.description = p.description.trim()
    if (p.default !== undefined && p.default !== '') {
      const numeric = p.type === 'number' || p.type === 'integer'
      out.default = numeric && !Number.isNaN(Number(p.default)) ? Number(p.default) : p.default
    }
    if (p.minimum !== undefined) out.minimum = p.minimum
    if (p.maximum !== undefined) out.maximum = p.maximum
    if (p.type === 'enum' && p.enum?.length) out.enum = p.enum
    return out
  })
  const item: NonNullable<QueryTool['result']>['item'] = {}
  for (const r of f.resultRows) {
    if (!r.key.trim() || !r.var.trim()) continue
    item[r.key.trim()] = r.number ? { var: r.var.trim(), number: true } : r.var.trim()
  }
  return {
    name: f.name.trim(),
    title: f.title.trim(),
    description: f.description.trim(),
    parameters,
    query: f.query,
    result: Object.keys(item).length ? { item } : {},
  }
}

/**
 * P3 — the per-dataset *tools* surface inside the catalog dataset detail. Lets a
 * researcher grow this dataset's set of verified, deterministic Ask tools without
 * a repo PR: list/delete saved tools, and the authoring loop intent → AI 下書き →
 * 編集 → 保存. Saving IS the human-vet gate (the backend re-validates read-only
 * SELECT/ASK + safe binding); a saved tool routes into Ask immediately. Real data
 * only — every call goes through /api (no fixture fallback).
 */
export function ToolsPanel({ datasetId }: { datasetId: string }) {
  const { t } = useTranslation()
  const [tools, setTools] = useState<QueryTool[] | null>(null)
  const [loadError, setLoadError] = useState('')
  const [busyDelete, setBusyDelete] = useState('')

  // Authoring (draft) state.
  const { isReady, getActiveCredentials } = useLlmSettings()
  const [intent, setIntent] = useState('')
  const [proposing, setProposing] = useState(false)
  const [proposeError, setProposeError] = useState('')
  const [draft, setDraft] = useState<ToolForm | null>(null)
  // The propose-time validity gate (null = manual draft / no AI check yet).
  const [draftValid, setDraftValid] = useState<boolean | null>(null)
  const [draftGateError, setDraftGateError] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')
  const [notice, setNotice] = useState('')

  useEffect(() => {
    let cancelled = false
    listTools(datasetId)
      .then((t) => !cancelled && setTools(t))
      .catch((e) => !cancelled && setLoadError(e instanceof Error ? e.message : String(e)))
    return () => {
      cancelled = true
    }
  }, [datasetId])


  // Any edit to the draft invalidates the AI-draft gate → re-checked on save.
  function patch(p: Partial<ToolForm>) {
    setDraft((d) => (d ? { ...d, ...p } : d))
    setDirty(true)
    setSaveError('')
  }
  function updateParam(i: number, patchP: Partial<QueryToolParam>) {
    setDraft((d) =>
      d ? { ...d, parameters: d.parameters.map((p, j) => (j === i ? { ...p, ...patchP } : p)) } : d,
    )
    setDirty(true)
    setSaveError('')
  }
  function addParam() {
    patch({
      parameters: [...(draft?.parameters ?? []), { name: '', type: 'string', required: false }],
    })
  }
  function removeParam(i: number) {
    patch({ parameters: (draft?.parameters ?? []).filter((_, j) => j !== i) })
  }
  function updateRow(i: number, patchR: Partial<ResultRow>) {
    setDraft((d) =>
      d ? { ...d, resultRows: d.resultRows.map((r, j) => (j === i ? { ...r, ...patchR } : r)) } : d,
    )
    setDirty(true)
    setSaveError('')
  }
  function addRow() {
    patch({ resultRows: [...(draft?.resultRows ?? []), { key: '', var: '', number: false }] })
  }
  function removeRow(i: number) {
    patch({ resultRows: (draft?.resultRows ?? []).filter((_, j) => j !== i) })
  }

  // 編集中の下書きを黙って上書き/破棄しない（保存前の編集は 1 操作で消える）
  function confirmDropDirty(): boolean {
    return !dirty || window.confirm(t('tools:panel.author.dirtyConfirm'))
  }

  function startEmpty() {
    if (!confirmDropDirty()) return
    setDraft({ ...EMPTY_FORM })
    setDraftValid(null)
    setDraftGateError(null)
    setDirty(false)
    setSaveError('')
    setNotice('')
  }
  function startEdit(t: QueryTool) {
    if (!confirmDropDirty()) return
    setDraft(toForm(t))
    setDraftValid(null)
    setDraftGateError(null)
    setDirty(false)
    setSaveError('')
    setNotice('')
  }
  function discard() {
    setDraft(null)
    setDraftValid(null)
    setDraftGateError(null)
    setDirty(false)
    setSaveError('')
  }

  async function propose() {
    if (!intent.trim() || !isReady) return
    if (!confirmDropDirty()) return
    setProposing(true)
    setProposeError('')
    setNotice('')
    try {
      const res = await proposeTool(datasetId, intent.trim(), getActiveCredentials())
      setDraft(toForm(res.draft))
      setDraftValid(res.valid)
      setDraftGateError(res.error)
      setDirty(false)
      setSaveError('')
    } catch (e) {
      setProposeError(e instanceof Error ? e.message : String(e))
    } finally {
      setProposing(false)
    }
  }

  async function save() {
    if (!draft) return
    const tool = fromForm(draft)
    if (!tool.name || !tool.query.trim()) {
      setSaveError(t('tools:panel.save.required'))
      return
    }
    setSaving(true)
    setSaveError('')
    try {
      const next = await saveTool(datasetId, tool)
      setTools(next)
      setDraft(null)
      setDraftValid(null)
      setDraftGateError(null)
      setDirty(false)
      setNotice(t('tools:panel.notice.saved', { name: tool.name }))
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  async function del(name: string) {
    if (!window.confirm(t('tools:panel.deleteConfirm', { name }))) return
    setBusyDelete(name)
    setLoadError('')
    try {
      setTools(await deleteTool(datasetId, name))
      setNotice(t('tools:panel.notice.deleted', { name }))
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusyDelete('')
    }
  }

  return (
    <div className="ds-tab-body tools-panel">
      <div className="ds-section-head">
        <span className="ds-section-title">{t('tools:panel.sectionTitle')}</span>
        <span className="ds-section-note">
          {tools ? t('tools:panel.count', { n: tools.length }) : '—'}
        </span>
      </div>
      <p className="tools-intro">
        <Trans i18nKey="tools:panel.intro">
          このデータセットに、型付き・決定論の<strong>読み取り専用 SPARQL ツール</strong>を足せます。
          保存したツールは PR なしで <strong>Ask の検証済みツール</strong>になります（保存＝人による確定）。
        </Trans>
      </p>

      {loadError && <pre className="error">{loadError}</pre>}
      {notice && <p className="tools-notice">✓ {notice}</p>}

      {/* --- existing tools --- */}
      {tools === null && !loadError && (
        <p className="loading-row">
          <span className="spinner" />
          {t('tools:panel.loading')}
        </p>
      )}
      {tools && tools.length === 0 && (
        <p className="ds-empty-note">{t('tools:panel.emptyList')}</p>
      )}
      {tools && tools.length > 0 && (
        <div className="tool-list">
          {tools.map((t) => (
            <ToolCard
              key={t.name}
              tool={t}
              datasetId={datasetId}
              deleting={busyDelete === t.name}
              onEdit={() => startEdit(t)}
              onDelete={() => del(t.name)}
            />
          ))}
        </div>
      )}

      {/* --- author a new tool --- */}
      <div className="tool-author">
        <div className="ds-subhead">{t('tools:panel.author.subhead')}</div>
        <p className="tools-hint">
          <Trans i18nKey="tools:panel.author.hint">
            「やりたいこと」を書くと、このデータの語彙をもとに <strong>AI が読み取り専用 SPARQL ツールを下書き</strong>します。
            下書きは<strong>その場で編集</strong>でき、<strong>保存して初めて確定</strong>します（保存＝人による検証ゲート）。
            キーはこのタブ内のみ保持し、保存しません（Ask と共通）。
          </Trans>
        </p>
        <textarea
          className="tool-intent-input"
          rows={2}
          value={intent}
          placeholder={t('tools:panel.author.intentPlaceholder')}
          onChange={(e) => setIntent(e.target.value)}
        />
        <LlmGate />
        <div className="tool-author-row">
          <button
            type="button"
            onClick={propose}
            disabled={proposing || !intent.trim() || !isReady}
          >
            {proposing ? (
              <>
                <span className="spinner" />
                {t('tools:panel.author.proposing')}
              </>
            ) : (
              t('tools:panel.author.propose')
            )}
          </button>
          <button type="button" className="btn btn--ghost btn--sm" onClick={startEmpty}>
            {t('tools:panel.author.startEmpty')}
          </button>
        </div>
        {proposeError && <pre className="error">{proposeError}</pre>}
      </div>

      {/* --- draft editor --- */}
      {draft && (
        <DraftEditor
          draft={draft}
          dirty={dirty}
          valid={draftValid}
          gateError={draftGateError}
          saving={saving}
          saveError={saveError}
          patch={patch}
          updateParam={updateParam}
          addParam={addParam}
          removeParam={removeParam}
          updateRow={updateRow}
          addRow={addRow}
          removeRow={removeRow}
          onSave={save}
          onDiscard={discard}
        />
      )}
    </div>
  )
}

/**
 * One saved tool: its metadata + a deterministic, KEY-FREE 実行 panel. Running a
 * verified tool needs no LLM and no API key — the server binds the typed args
 * safely and runs the fixed template over the canonical FROM-merge (the same path
 * MCP exposes). Results come back as rows + the exact read-only SPARQL (citable).
 */
function ToolCard({
  tool,
  datasetId,
  deleting,
  onEdit,
  onDelete,
}: {
  tool: QueryTool
  datasetId: string
  deleting: boolean
  onEdit: () => void
  onDelete: () => void
}) {
  const { t } = useTranslation()
  const params = tool.parameters ?? []
  const [open, setOpen] = useState(false)

  return (
    <div className="tool-card">
      <div className="tool-card-head">
        <code className="tool-name">{tool.name}</code>
        {tool.title && <span className="tool-title">{tool.title}</span>}
        <span className="tool-card-actions">
          <button
            type="button"
            className="btn btn--soft btn--sm"
            onClick={() => setOpen((o) => !o)}
          >
            {open ? t('tools:card.close') : t('tools:card.run')}
          </button>
          <button type="button" className="btn btn--ghost btn--sm" onClick={onEdit}>
            {t('tools:card.edit')}
          </button>
          <button
            type="button"
            className="btn btn--danger btn--sm"
            disabled={deleting}
            onClick={onDelete}
          >
            {deleting ? t('tools:card.deleting') : t('tools:card.delete')}
          </button>
        </span>
      </div>
      {tool.description && <p className="tool-desc">{tool.description}</p>}
      {params.length > 0 && (
        <div className="tool-params">
          {params.map((p) => (
            <span key={p.name} className="param-chip" title={p.description}>
              <code>{p.name}</code>
              <span className="param-chip-type">{p.type}</span>
              {p.required && <span className="param-chip-req">{t('tools:card.required')}</span>}
            </span>
          ))}
        </div>
      )}
      <details className="tool-sparql-details">
        <summary>{t('tools:card.sparqlSummary')}</summary>
        <pre className="sparql-block">{tool.query}</pre>
      </details>

      {open && <ToolRunner datasetId={datasetId} tool={tool} />}
    </div>
  )
}

function DraftEditor({
  draft,
  dirty,
  valid,
  gateError,
  saving,
  saveError,
  patch,
  updateParam,
  addParam,
  removeParam,
  updateRow,
  addRow,
  removeRow,
  onSave,
  onDiscard,
}: {
  draft: ToolForm
  dirty: boolean
  valid: boolean | null
  gateError: string | null
  saving: boolean
  saveError: string
  patch: (p: Partial<ToolForm>) => void
  updateParam: (i: number, p: Partial<QueryToolParam>) => void
  addParam: () => void
  removeParam: (i: number) => void
  updateRow: (i: number, r: Partial<ResultRow>) => void
  addRow: () => void
  removeRow: (i: number) => void
  onSave: () => void
  onDiscard: () => void
}) {
  const { t } = useTranslation()
  // Validity banner: the AI-draft gate is meaningful only until the user edits;
  // after an edit (or for a hand-built draft) the server re-checks on save.
  const banner =
    valid !== null && !dirty ? (
      valid ? (
        <p className="draft-gate draft-gate--ok">{t('tools:draft.gate.ok')}</p>
      ) : (
        <p className="draft-gate draft-gate--bad">
          {t('tools:draft.gate.bad', { error: gateError })}
        </p>
      )
    ) : (
      <p className="draft-gate draft-gate--note">{t('tools:draft.gate.note')}</p>
    )

  return (
    <div className="draft-editor">
      <div className="draft-editor-head">
        <span className="draft-editor-title">{t('tools:draft.title')}</span>
      </div>
      {banner}

      <label className="draft-field">
        <span className="draft-label">{t('tools:draft.nameLabel')}</span>
        <input
          className="draft-text"
          value={draft.name}
          placeholder="structure_lookup"
          onChange={(e) => patch({ name: e.target.value })}
        />
      </label>
      <label className="draft-field">
        <span className="draft-label">{t('tools:draft.titleLabel')}</span>
        <input
          className="draft-text"
          value={draft.title}
          placeholder={t('tools:draft.titlePlaceholder')}
          onChange={(e) => patch({ title: e.target.value })}
        />
      </label>
      <label className="draft-field">
        <span className="draft-label">{t('tools:draft.descriptionLabel')}</span>
        <textarea
          className="draft-text"
          rows={2}
          value={draft.description}
          onChange={(e) => patch({ description: e.target.value })}
        />
      </label>

      <div className="draft-subhead">
        {t('tools:draft.params.head')}
        <button type="button" className="btn btn--ghost btn--sm" onClick={addParam}>
          {t('tools:draft.params.add')}
        </button>
      </div>
      {draft.parameters.length === 0 && (
        <p className="ds-empty-note">{t('tools:draft.params.empty')}</p>
      )}
      {draft.parameters.map((p, i) => (
        <div key={i} className="param-edit">
          <div className="param-edit-row">
            <input
              className="draft-text param-name"
              value={p.name}
              placeholder={t('tools:draft.params.namePlaceholder')}
              onChange={(e) => updateParam(i, { name: e.target.value })}
            />
            <select
              className="draft-select"
              value={p.type}
              onChange={(e) => updateParam(i, { type: e.target.value as ParamType })}
            >
              {PARAM_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <label className="param-req">
              <input
                type="checkbox"
                checked={!!p.required}
                onChange={(e) => updateParam(i, { required: e.target.checked })}
              />
              {t('tools:draft.params.required')}
            </label>
            <button
              type="button"
              className="btn btn--danger btn--sm param-del"
              onClick={() => removeParam(i)}
            >
              {t('tools:draft.params.delete')}
            </button>
          </div>
          <input
            className="draft-text param-desc"
            value={p.description ?? ''}
            placeholder={t('tools:draft.params.descriptionPlaceholder')}
            onChange={(e) => updateParam(i, { description: e.target.value })}
          />
          <div className="param-edit-extra">
            <input
              className="draft-text param-default"
              value={p.default === undefined ? '' : String(p.default)}
              placeholder={t('tools:draft.params.defaultPlaceholder')}
              onChange={(e) =>
                updateParam(i, { default: e.target.value === '' ? undefined : e.target.value })
              }
            />
            {(p.type === 'number' || p.type === 'integer') && (
              <>
                <input
                  className="draft-text param-num"
                  type="number"
                  value={p.minimum ?? ''}
                  placeholder="min"
                  onChange={(e) =>
                    updateParam(i, {
                      minimum: e.target.value === '' ? undefined : Number(e.target.value),
                    })
                  }
                />
                <input
                  className="draft-text param-num"
                  type="number"
                  value={p.maximum ?? ''}
                  placeholder="max"
                  onChange={(e) =>
                    updateParam(i, {
                      maximum: e.target.value === '' ? undefined : Number(e.target.value),
                    })
                  }
                />
              </>
            )}
            {p.type === 'enum' && (
              <input
                className="draft-text param-enum"
                value={(p.enum ?? []).join(', ')}
                placeholder={t('tools:draft.params.enumPlaceholder')}
                onChange={(e) =>
                  updateParam(i, {
                    enum: e.target.value
                      .split(',')
                      .map((s) => s.trim())
                      .filter(Boolean),
                  })
                }
              />
            )}
          </div>
        </div>
      ))}

      <label className="draft-field">
        <span className="draft-label">{t('tools:draft.queryLabel', { placeholder: '{{param}}' })}</span>
        <textarea
          className="draft-text draft-query"
          rows={8}
          spellCheck={false}
          value={draft.query}
          onChange={(e) => patch({ query: e.target.value })}
        />
      </label>

      <div className="draft-subhead">
        {t('tools:draft.result.head')}
        <button type="button" className="btn btn--ghost btn--sm" onClick={addRow}>
          {t('tools:draft.result.add')}
        </button>
      </div>
      {draft.resultRows.length === 0 && (
        <p className="ds-empty-note">{t('tools:draft.result.empty')}</p>
      )}
      {draft.resultRows.map((r, i) => (
        <div key={i} className="result-edit-row">
          <input
            className="draft-text result-key"
            value={r.key}
            placeholder={t('tools:draft.result.keyPlaceholder')}
            onChange={(e) => updateRow(i, { key: e.target.value })}
          />
          <span className="result-arrow">→</span>
          <input
            className="draft-text result-var"
            value={r.var}
            placeholder={t('tools:draft.result.varPlaceholder')}
            onChange={(e) => updateRow(i, { var: e.target.value })}
          />
          <label className="param-req">
            <input
              type="checkbox"
              checked={r.number}
              onChange={(e) => updateRow(i, { number: e.target.checked })}
            />
            {t('tools:draft.result.number')}
          </label>
          <button
            type="button"
            className="btn btn--danger btn--sm"
            onClick={() => removeRow(i)}
          >
            {t('tools:draft.result.delete')}
          </button>
        </div>
      ))}

      {saveError && <pre className="error">{saveError}</pre>}
      <div className="draft-actions">
        <button type="button" className="promote-btn" onClick={onSave} disabled={saving}>
          {saving ? t('tools:draft.saving') : t('tools:draft.save')}
        </button>
        <button type="button" className="btn btn--ghost btn--sm" onClick={onDiscard} disabled={saving}>
          {t('tools:draft.discard')}
        </button>
      </div>
    </div>
  )
}
