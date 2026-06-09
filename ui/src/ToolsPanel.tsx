import { useEffect, useState } from 'react'
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

// Shared with Ask (same user-brought key, sessionStorage, never persisted to
// disk). Listing/saving/deleting tools need no key; the AI draft (propose) does.
const API_KEY_STORAGE = 'asterism.apiKey'

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
  const [tools, setTools] = useState<QueryTool[] | null>(null)
  const [loadError, setLoadError] = useState('')
  const [busyDelete, setBusyDelete] = useState('')

  // Authoring (draft) state.
  const [intent, setIntent] = useState('')
  const [apiKey, setApiKey] = useState(() => sessionStorage.getItem(API_KEY_STORAGE) ?? '')
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

  function onApiKeyChange(v: string) {
    setApiKey(v)
    if (v) sessionStorage.setItem(API_KEY_STORAGE, v)
    else sessionStorage.removeItem(API_KEY_STORAGE)
  }

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

  function startEmpty() {
    setDraft({ ...EMPTY_FORM })
    setDraftValid(null)
    setDraftGateError(null)
    setDirty(false)
    setSaveError('')
    setNotice('')
  }
  function startEdit(t: QueryTool) {
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
    if (!intent.trim() || !apiKey.trim()) return
    setProposing(true)
    setProposeError('')
    setNotice('')
    try {
      const res = await proposeTool(datasetId, intent.trim(), apiKey.trim())
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
      setSaveError('name と query は必須です。')
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
      setNotice(`「${tool.name}」を保存しました。Ask の検証済みツールとして使えます。`)
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  async function del(name: string) {
    if (!window.confirm(`ツール「${name}」を削除しますか？\nこのデータセットの Ask から外れます。`))
      return
    setBusyDelete(name)
    setLoadError('')
    try {
      setTools(await deleteTool(datasetId, name))
      setNotice(`「${name}」を削除しました。`)
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusyDelete('')
    }
  }

  return (
    <div className="ds-tab-body tools-panel">
      <div className="ds-section-head">
        <span className="ds-section-title">ツール（このデータの検証済み Ask 操作）</span>
        <span className="ds-section-note">{tools ? `${tools.length} 件` : '—'}</span>
      </div>
      <p className="tools-intro">
        このデータセットに、型付き・決定論の<strong>読み取り専用 SPARQL ツール</strong>を足せます。
        保存したツールは PR なしで <strong>Ask の検証済みツール</strong>になります（保存＝人による確定）。
      </p>

      {loadError && <pre className="error">{loadError}</pre>}
      {notice && <p className="tools-notice">✓ {notice}</p>}

      {/* --- existing tools --- */}
      {tools === null && !loadError && (
        <p className="loading-row">
          <span className="spinner" />
          ツールを読み込み中…
        </p>
      )}
      {tools && tools.length === 0 && (
        <p className="ds-empty-note">まだツールはありません。下の「AI で下書き」から作れます。</p>
      )}
      {tools && tools.length > 0 && (
        <div className="tool-list">
          {tools.map((t) => (
            <div key={t.name} className="tool-card">
              <div className="tool-card-head">
                <code className="tool-name">{t.name}</code>
                {t.title && <span className="tool-title">{t.title}</span>}
                <span className="tool-card-actions">
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm"
                    onClick={() => startEdit(t)}
                  >
                    編集
                  </button>
                  <button
                    type="button"
                    className="btn btn--danger btn--sm"
                    disabled={busyDelete === t.name}
                    onClick={() => del(t.name)}
                  >
                    {busyDelete === t.name ? '削除中…' : '削除'}
                  </button>
                </span>
              </div>
              {t.description && <p className="tool-desc">{t.description}</p>}
              {(t.parameters?.length ?? 0) > 0 && (
                <div className="tool-params">
                  {t.parameters!.map((p) => (
                    <span key={p.name} className="param-chip" title={p.description}>
                      <code>{p.name}</code>
                      <span className="param-chip-type">{p.type}</span>
                      {p.required && <span className="param-chip-req">必須</span>}
                    </span>
                  ))}
                </div>
              )}
              <details className="tool-sparql-details">
                <summary>SPARQL を見る</summary>
                <pre className="sparql-block">{t.query}</pre>
              </details>
            </div>
          ))}
        </div>
      )}

      {/* --- author a new tool --- */}
      <div className="tool-author">
        <div className="ds-subhead">AI で下書き（やりたいことから 1 つ作る）</div>
        <p className="tools-hint">
          「やりたいこと」を書くと、このデータの語彙をもとに <strong>AI が読み取り専用 SPARQL ツールを下書き</strong>します。
          下書きは<strong>その場で編集</strong>でき、<strong>保存して初めて確定</strong>します（保存＝人による検証ゲート）。
          キーはこのタブ内のみ保持し、保存しません（Ask と共通）。
        </p>
        <textarea
          className="tool-intent-input"
          rows={2}
          value={intent}
          placeholder="例: 組成を渡すと結晶構造（空間群・結晶系）を返す"
          onChange={(e) => setIntent(e.target.value)}
        />
        <div className="tool-author-row">
          <input
            type="password"
            className="ask-key-input tool-key-input"
            value={apiKey}
            placeholder="sk-ant-…（AI 下書きに必要）"
            autoComplete="off"
            onChange={(e) => onApiKeyChange(e.target.value)}
          />
          <button
            type="button"
            onClick={propose}
            disabled={proposing || !intent.trim() || !apiKey.trim()}
          >
            {proposing ? (
              <>
                <span className="spinner" />
                下書き中…
              </>
            ) : (
              'AI で下書き'
            )}
          </button>
          <button type="button" className="btn btn--ghost btn--sm" onClick={startEmpty}>
            空から手で作る
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
  // Validity banner: the AI-draft gate is meaningful only until the user edits;
  // after an edit (or for a hand-built draft) the server re-checks on save.
  const banner =
    valid !== null && !dirty ? (
      valid ? (
        <p className="draft-gate draft-gate--ok">✓ AI 下書き: 検証 OK（そのまま保存できます）</p>
      ) : (
        <p className="draft-gate draft-gate--bad">
          要修正: {gateError}（編集して直してから保存してください）
        </p>
      )
    ) : (
      <p className="draft-gate draft-gate--note">
        保存時にサーバが検証します（読み取り専用 SELECT/ASK・安全な束縛）。
      </p>
    )

  return (
    <div className="draft-editor">
      <div className="draft-editor-head">
        <span className="draft-editor-title">下書きを確認・編集</span>
      </div>
      {banner}

      <label className="draft-field">
        <span className="draft-label">名前 (snake_case)</span>
        <input
          className="draft-text"
          value={draft.name}
          placeholder="structure_lookup"
          onChange={(e) => patch({ name: e.target.value })}
        />
      </label>
      <label className="draft-field">
        <span className="draft-label">タイトル</span>
        <input
          className="draft-text"
          value={draft.title}
          placeholder="人が読む短い名前"
          onChange={(e) => patch({ title: e.target.value })}
        />
      </label>
      <label className="draft-field">
        <span className="draft-label">説明</span>
        <textarea
          className="draft-text"
          rows={2}
          value={draft.description}
          onChange={(e) => patch({ description: e.target.value })}
        />
      </label>

      <div className="draft-subhead">
        パラメータ
        <button type="button" className="btn btn--ghost btn--sm" onClick={addParam}>
          + 追加
        </button>
      </div>
      {draft.parameters.length === 0 && <p className="ds-empty-note">パラメータはありません。</p>}
      {draft.parameters.map((p, i) => (
        <div key={i} className="param-edit">
          <div className="param-edit-row">
            <input
              className="draft-text param-name"
              value={p.name}
              placeholder="param 名"
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
              必須
            </label>
            <button
              type="button"
              className="btn btn--danger btn--sm param-del"
              onClick={() => removeParam(i)}
            >
              削除
            </button>
          </div>
          <input
            className="draft-text param-desc"
            value={p.description ?? ''}
            placeholder="説明（任意）"
            onChange={(e) => updateParam(i, { description: e.target.value })}
          />
          <div className="param-edit-extra">
            <input
              className="draft-text param-default"
              value={p.default === undefined ? '' : String(p.default)}
              placeholder="既定値（任意）"
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
                placeholder="enum 値（カンマ区切り）"
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
        <span className="draft-label">SPARQL（読み取り専用・{'{{param}}'} で束縛）</span>
        <textarea
          className="draft-text draft-query"
          rows={8}
          spellCheck={false}
          value={draft.query}
          onChange={(e) => patch({ query: e.target.value })}
        />
      </label>

      <div className="draft-subhead">
        出力の対応づけ（任意・列名 → SPARQL 変数）
        <button type="button" className="btn btn--ghost btn--sm" onClick={addRow}>
          + 追加
        </button>
      </div>
      {draft.resultRows.length === 0 && (
        <p className="ds-empty-note">未指定なら変数名がそのまま列名になります。</p>
      )}
      {draft.resultRows.map((r, i) => (
        <div key={i} className="result-edit-row">
          <input
            className="draft-text result-key"
            value={r.key}
            placeholder="出力の列名 (例: space_group)"
            onChange={(e) => updateRow(i, { key: e.target.value })}
          />
          <span className="result-arrow">→</span>
          <input
            className="draft-text result-var"
            value={r.var}
            placeholder="SPARQL 変数 (例: sg)"
            onChange={(e) => updateRow(i, { var: e.target.value })}
          />
          <label className="param-req">
            <input
              type="checkbox"
              checked={r.number}
              onChange={(e) => updateRow(i, { number: e.target.checked })}
            />
            数値
          </label>
          <button
            type="button"
            className="btn btn--danger btn--sm"
            onClick={() => removeRow(i)}
          >
            削除
          </button>
        </div>
      ))}

      {saveError && <pre className="error">{saveError}</pre>}
      <div className="draft-actions">
        <button type="button" className="promote-btn" onClick={onSave} disabled={saving}>
          {saving ? '保存中…' : '保存（確定してツールに追加）'}
        </button>
        <button type="button" className="btn btn--ghost btn--sm" onClick={onDiscard} disabled={saving}>
          破棄
        </button>
      </div>
    </div>
  )
}
