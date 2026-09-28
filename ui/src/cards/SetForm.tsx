// 絞り込みフォーム（決定 8）。`class_schema` から `setFormFields.ts`（純関数）で
// 項目を組み立て、分類の選択肢だけ `set_breakdown`（上位 12）から取る。
// 「絞り込む」で `sets/resolve` を呼び、正規化された spec + set_id + title を
// 呼び出し側へ渡す（棚への追加・画面遷移は呼び出し側 — SubjectRail/SetPage —
// の責任）。
//
// `classSchema`（契約メモ §2）は本来 C1-schema 担当だが、cardsApi.ts
// （c2-shell）が薄いラッパを足してくれている（cardsApi.ts 冒頭のコメント参照）
// ので、ここでは import するだけでよい。
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { classSchema, resolveSet, runCard } from './cardsApi'
import type { CardToolResult, SetResolveResult, SetSpec, SetWhereClause } from './cardsApi'
import {
  buildSetFormSpec,
  CATEGORY_VISIBLE_LIMIT,
  categoryOptionsView,
  type CategoryFilterField,
  type QuantityFilterField,
  type SetFilterOp,
  type SetFormSpec,
} from './setFormFields'
import './pages.css'

export interface SetFormProps {
  classIri: string
  /** 既存の絞り込みを直すとき（SetPage の「条件を変える」）の初期値。 */
  initialSpec?: SetSpec | null
  onCancel?: () => void
  onSubmit: (result: SetResolveResult) => void
}

interface QuantityFieldState {
  active: boolean
  op: SetFilterOp
  value: string
  min: string
  max: string
}

interface CategoryFieldState {
  selected: Set<string>
}

function initialQuantityState(field: QuantityFilterField, spec?: SetSpec | null): QuantityFieldState {
  const clause = spec?.where.find((w) => w.property === field.property && (w.op === 'gt' || w.op === 'lt' || w.op === 'between'))
  if (!clause) return { active: false, op: 'gt', value: '', min: '', max: '' }
  if (clause.op === 'between') {
    const v = clause.value as { min?: unknown; max?: unknown }
    return { active: true, op: 'between', value: '', min: String(v?.min ?? ''), max: String(v?.max ?? '') }
  }
  // find の述語で op を 'gt'|'lt'|'between' に絞っているが、TS は predicate
  // 関数からは narrow しない（'between' はここまでに弾いた）ので明示する。
  return { active: true, op: clause.op as 'gt' | 'lt', value: String(clause.value ?? ''), min: '', max: '' }
}

function initialCategoryState(field: CategoryFilterField, spec?: SetSpec | null): CategoryFieldState {
  const clause = spec?.where.find((w) => w.property === field.property && w.op === 'in')
  const values = clause && Array.isArray(clause.value) ? (clause.value as unknown[]).map(String) : []
  return { selected: new Set(values) }
}

/** `set_breakdown` の結果から、role: 'category' の列の distinct 値を全件拾う
 *  （どれだけ見せるかは `categoryOptionsView`（setFormFields.ts・契約 §4）が
 *  検索欄／「さらに表示」込みで決める — ここでは切り詰めない）。 */
// eslint-disable-next-line react-refresh/only-export-components -- テスト容易性のため意図して許容（FirstScreen.tsx と同じ理由）
export function categoryValuesFrom(result: CardToolResult): string[] {
  // 行のキーは `result.item` の**キー名**であって `ItemSpec.var` ではない
  // （`defaultView.ts` の KeyedItem コメント参照）。
  const categoryKey = Object.entries(result.item).find(([, spec]) => spec.role === 'category')?.[0]
  if (!categoryKey) return []
  const values: string[] = []
  for (const row of result.items) {
    const v = row[categoryKey]
    if (typeof v === 'string' && !values.includes(v)) values.push(v)
  }
  return values
}

/** class_schema の読み込み結果。`classIri` で紐づけ、then/catch でだけ書き込む
 *  — effect の本体で同期的に setState しない（react-hooks/set-state-in-effect。
 *  ProvenanceTrace.tsx／CardTile.tsx と同じ流儀）。 */
interface SchemaLoadState {
  classIri: string
  formSpec: SetFormSpec | null
  error: boolean
}

const EMPTY_SCHEMA_LOAD: SchemaLoadState = { classIri: '', formSpec: null, error: false }

export function SetForm({ classIri, initialSpec, onCancel, onSubmit }: SetFormProps) {
  const { t } = useTranslation('cards')
  const [loaded, setLoaded] = useState<SchemaLoadState>(EMPTY_SCHEMA_LOAD)
  const [categoryOptions, setCategoryOptions] = useState<Record<string, string[]>>({})
  const [quantityState, setQuantityState] = useState<Record<string, QuantityFieldState>>({})
  const [categoryState, setCategoryState] = useState<Record<string, CategoryFieldState>>({})
  // 分類ごとの「見せかた」だけの状態（選択そのものではない — 契約 §4「12 を
  // 超えるときは検索欄つきの一覧にし、上位 12 だけ見せて『さらに表示』」）。
  const [categoryUi, setCategoryUi] = useState<Record<string, { search: string; showAll: boolean }>>({})
  const [orderProperty, setOrderProperty] = useState('')
  const [orderDir, setOrderDir] = useState<'desc' | 'asc'>('desc')
  const [limit, setLimit] = useState(initialSpec?.limit ?? 20)
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState(false)

  useEffect(() => {
    let cancelled = false
    classSchema(classIri)
      .then((schema) => {
        if (cancelled) return
        if (!schema) {
          setLoaded({ classIri, formSpec: null, error: true })
          return
        }
        const spec = buildSetFormSpec(schema)
        setLoaded({ classIri, formSpec: spec, error: false })
        const nextQuantity: Record<string, QuantityFieldState> = {}
        const nextCategory: Record<string, CategoryFieldState> = {}
        for (const field of spec.filters) {
          if (field.kind === 'quantity') nextQuantity[field.property] = initialQuantityState(field, initialSpec)
          else nextCategory[field.property] = initialCategoryState(field, initialSpec)
        }
        setQuantityState(nextQuantity)
        setCategoryState(nextCategory)
        setCategoryUi({})
        if (initialSpec?.order_by) {
          setOrderProperty(initialSpec.order_by.property)
          setOrderDir(initialSpec.order_by.dir)
        }
      })
      .catch(() => {
        if (!cancelled) setLoaded({ classIri, formSpec: null, error: true })
      })
    return () => {
      cancelled = true
    }
    // initialSpec はマウント時の初期値としてだけ使う（入力中に呼び出し側が
    // 別オブジェクトを渡し直しても組み直さない）。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [classIri])

  const isCurrent = loaded.classIri === classIri
  const formSpec = isCurrent ? loaded.formSpec : null
  const loadError = isCurrent && loaded.error

  // 分類フィルタの選択肢（上位 12）を set_breakdown から読む。where は空
  // （その class 全体からの分布 — フォームの選択肢はまだ絞り込んでいない）。
  useEffect(() => {
    if (!formSpec) return
    const categoryFields = formSpec.filters.filter((f): f is CategoryFilterField => f.kind === 'category')
    if (categoryFields.length === 0) return
    let cancelled = false
    const baseSpec: SetSpec = { class: classIri, where: [], order_by: null, limit: 20, source_scope: 'all' }
    Promise.all(
      categoryFields.map((f) =>
        runCard({ kind: 'set', spec: baseSpec }, 'set_breakdown', { property: f.property }).then(
          (r): [string, string[]] => [f.property, categoryValuesFrom(r)],
        ),
      ),
    )
      .then((entries) => {
        if (cancelled) return
        const map: Record<string, string[]> = {}
        for (const [property, values] of entries) map[property] = values
        setCategoryOptions(map)
      })
      .catch(() => {
        // 選択肢が読めなくてもフォーム自体は使える（数量側の絞り込みはできる）。
      })
    return () => {
      cancelled = true
    }
  }, [formSpec, classIri])

  const canSubmit = useMemo(() => !!formSpec && !submitting, [formSpec, submitting])

  function buildWhere(): SetWhereClause[] {
    const where: SetWhereClause[] = []
    for (const [property, state] of Object.entries(quantityState)) {
      if (!state.active) continue
      if (state.op === 'between') {
        const min = Number(state.min)
        const max = Number(state.max)
        if (!Number.isFinite(min) || !Number.isFinite(max)) continue
        where.push({ property, op: 'between', value: { min, max } })
      } else {
        const value = Number(state.value)
        if (!Number.isFinite(value)) continue
        where.push({ property, op: state.op, value })
      }
    }
    for (const [property, state] of Object.entries(categoryState)) {
      if (state.selected.size === 0) continue
      where.push({ property, op: 'in', value: Array.from(state.selected) })
    }
    return where
  }

  async function handleSubmit() {
    if (!formSpec) return
    setSubmitting(true)
    setSubmitError(false)
    const spec: SetSpec = {
      class: classIri,
      where: buildWhere(),
      order_by: orderProperty ? { property: orderProperty, dir: orderDir } : null,
      limit: Math.min(Math.max(1, limit), formSpec.maxLimit),
      source_scope: initialSpec?.source_scope ?? 'all',
    }
    try {
      const result = await resolveSet(spec)
      onSubmit(result)
    } catch {
      setSubmitError(true)
    } finally {
      setSubmitting(false)
    }
  }

  if (loadError) return <p className="ds-empty-note">{t('render_error')}</p>
  if (!formSpec) return <p className="ds-empty-note">{t('page.loading')}</p>

  const quantityFields = formSpec.filters.filter((f): f is QuantityFilterField => f.kind === 'quantity')
  const categoryFields = formSpec.filters.filter((f): f is CategoryFilterField => f.kind === 'category')

  return (
    <div className="cardpage-setform">
      {quantityFields.map((field) => {
        const state = quantityState[field.property] ?? { active: false, op: 'gt', value: '', min: '', max: '' }
        return (
          <div className="cardpage-setform-row" key={field.property}>
            <label className="cardpage-setform-check">
              <input
                type="checkbox"
                checked={state.active}
                onChange={(e) =>
                  setQuantityState((prev) => ({ ...prev, [field.property]: { ...state, active: e.target.checked } }))
                }
              />
              {field.label}
            </label>
            {state.active && (
              <span className="cardpage-setform-controls">
                <select
                  value={state.op}
                  onChange={(e) =>
                    setQuantityState((prev) => ({
                      ...prev,
                      [field.property]: { ...state, op: e.target.value as SetFilterOp },
                    }))
                  }
                >
                  <option value="gt">{t('setform.op_gt')}</option>
                  <option value="lt">{t('setform.op_lt')}</option>
                  <option value="between">{t('setform.op_between')}</option>
                </select>
                {state.op === 'between' ? (
                  <>
                    <input
                      className="cardpage-setform-number"
                      type="number"
                      value={state.min}
                      onChange={(e) =>
                        setQuantityState((prev) => ({ ...prev, [field.property]: { ...state, min: e.target.value } }))
                      }
                    />
                    <span aria-hidden="true">〜</span>
                    <input
                      className="cardpage-setform-number"
                      type="number"
                      value={state.max}
                      onChange={(e) =>
                        setQuantityState((prev) => ({ ...prev, [field.property]: { ...state, max: e.target.value } }))
                      }
                    />
                  </>
                ) : (
                  <input
                    className="cardpage-setform-number"
                    type="number"
                    value={state.value}
                    onChange={(e) =>
                      setQuantityState((prev) => ({ ...prev, [field.property]: { ...state, value: e.target.value } }))
                    }
                  />
                )}
                {field.unit && <span className="cardpage-setform-unit">{field.unit}</span>}
              </span>
            )}
          </div>
        )
      })}
      {categoryFields.map((field) => {
        const options = categoryOptions[field.property] ?? []
        const state = categoryState[field.property] ?? { selected: new Set<string>() }
        const ui = categoryUi[field.property] ?? { search: '', showAll: false }
        const view = categoryOptionsView(options, ui)
        // 検索欄は「12 を超えるとき」だけ出す（契約 §4） — 元の総数（検索前）で
        // 判定する。件数が少ないうちは無音のまま。
        const showSearch = options.length > CATEGORY_VISIBLE_LIMIT
        return (
          <div className="cardpage-setform-row" key={field.property}>
            <div className="cardpage-setform-label-col">
              <div className="cardpage-setform-label">{field.label}</div>
              {options.length > 0 && <p className="cardpage-setform-hint">{t('setform.add_condition_hint')}</p>}
              {showSearch && (
                <input
                  type="search"
                  className="cardpage-setform-search"
                  value={ui.search}
                  placeholder={t('setform.search_placeholder')}
                  onChange={(e) =>
                    setCategoryUi((prev) => ({ ...prev, [field.property]: { ...ui, search: e.target.value } }))
                  }
                />
              )}
            </div>
            <div className="cardpage-setform-choices">
              {options.length === 0 && <span className="cardpage-setform-empty">{t('empty')}</span>}
              {view.visible.map((value) => (
                <label className="cardpage-setform-check" key={value}>
                  <input
                    type="checkbox"
                    checked={state.selected.has(value)}
                    onChange={(e) => {
                      const next = new Set(state.selected)
                      if (e.target.checked) next.add(value)
                      else next.delete(value)
                      setCategoryState((prev) => ({ ...prev, [field.property]: { selected: next } }))
                    }}
                  />
                  {value}
                </label>
              ))}
              {view.hasMore && (
                <button
                  type="button"
                  className="link-btn cardpage-setform-more"
                  onClick={() =>
                    setCategoryUi((prev) => ({ ...prev, [field.property]: { ...ui, showAll: true } }))
                  }
                >
                  {t('setform.show_more')}
                </button>
              )}
            </div>
          </div>
        )
      })}
      {formSpec.orderOptions.length > 0 && (
        <div className="cardpage-setform-row">
          <div className="cardpage-setform-label">{t('setform.order_label')}</div>
          <span className="cardpage-setform-controls">
            <select value={orderProperty} onChange={(e) => setOrderProperty(e.target.value)}>
              <option value="">{t('setform.order_none')}</option>
              {formSpec.orderOptions.map((o) => (
                <option key={o.property} value={o.property}>
                  {o.label}
                </option>
              ))}
            </select>
            {orderProperty && (
              <select value={orderDir} onChange={(e) => setOrderDir(e.target.value as 'desc' | 'asc')}>
                <option value="desc">{t('setform.order_desc')}</option>
                <option value="asc">{t('setform.order_asc')}</option>
              </select>
            )}
          </span>
        </div>
      )}
      <div className="cardpage-setform-row">
        <label className="cardpage-setform-label" htmlFor="cardpage-setform-limit">
          {t('setform.limit_label')}
        </label>
        <input
          id="cardpage-setform-limit"
          className="cardpage-setform-number"
          type="number"
          min={1}
          max={formSpec.maxLimit}
          value={limit}
          onChange={(e) => setLimit(Number(e.target.value))}
        />
      </div>
      {submitError && <p className="ds-empty-note">{t('render_error')}</p>}
      <div className="cardpage-setform-actions">
        {onCancel && (
          <button type="button" className="btn btn--ghost btn--sm" onClick={onCancel}>
            {t('setform.cancel')}
          </button>
        )}
        <button type="button" className="btn btn--soft btn--sm" disabled={!canSubmit} onClick={handleSubmit}>
          {t('setform.submit')}
        </button>
      </div>
    </div>
  )
}
