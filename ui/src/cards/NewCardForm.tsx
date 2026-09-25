// 「＋ グラフを足す」フォーム（契約メモ §1-1〜§1-3・分担 §2 ui-form）。
// ①見せ方のタイル 6 つ → ②その形に要る項目だけ → ③条件、の順で 1 画面に
// 出す（モーダルにしない・K17: 1 工程 1 画面 — 呼び出し側が同じページの上部に
// 挿し込む）。「作る」で `runCard` を 1 回試し、成功したら `cardStore` に
// 保存して `onCreated` を呼ぶ。失敗（400 等）は帰結の文で示す（K39・専用の
// 文言は増やさず既存の `render_error` を使う）。「言葉で頼む」は出さない
// （契約メモ §1-7）。
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { CardRunSubject, CardSpec, LinkingKind, MeasureAgg, MeasureShape, MeasureWhereClause, SetWhereClause } from './cardsApi'
import { classSchema, linkingKinds, runCard } from './cardsApi'
import { addCard } from './cardStore'
import {
  candidates,
  fieldsFor,
  needsAgg,
  MEASURE_AGGS,
  shapes,
  toCardSpec,
  type MeasureCandidate,
  type MeasureCardLabels,
  type MeasureSchemaLike,
} from './measureCardFields'
import { pathLabel } from './viewpoints'
import './pages.css'
import './newcard.css'
import { SetForm } from './SetForm'
import { unitLabel } from './unitLabel'

export interface NewCardFormProps {
  /** カードを実行する主語。`runCard`/`CardSpec.subject_key` の両方に使う
   *  （呼び出し側 — SubjectPage/SetPage — が既に持っている値をそのまま渡す）。 */
  subject: CardRunSubject
  /** 契約メモ §1 の文字列表現 `i:<iri>` | `s:<set_id>`（呼び出し側が既に
   *  組み立てているので、ここでは組み立て直さない）。 */
  subjectKey: string
  /** 呼び出し側（SubjectPage/SetPage）が既に解決しているデータセット id。
   *  この PR の時点では ui-form 側のロジックはこの id を必要としない
   *  （class_schema・linkingKinds だけで完結する）が、呼び出し側の型と揃える
   *  ために受け取る — deviations 参照。 */
  datasetId: string
  onCancel: () => void
  onCreated: (card: CardSpec) => void
  /** ドロワー（`PageChatDrawer.tsx`・PR F12）に埋め込むとき true。見出し
   *  （`newcard.lead`）を省き、余白を詰めた見た目にする（契約メモ PR F12
   *  §1-4「フォーム（NewCardForm）をドロワーの中に埋め込む」）。ロジックは
   *  変えない — 見た目だけの分岐。 */
  embedded?: boolean
}

interface FieldValues {
  x?: MeasureCandidate
  y?: MeasureCandidate
  item?: MeasureCandidate
  category?: MeasureCandidate
  items: MeasureCandidate[]
}

const EMPTY_FIELDS: FieldValues = { items: [] }

function isComplete(shape: MeasureShape, values: FieldValues, agg: MeasureAgg | null): boolean {
  switch (shape) {
    case 'series':
    case 'pairs':
      return !!values.x && !!values.y
    case 'ranked':
      return !!values.item
    case 'quantity':
      return !!values.item && !!agg
    case 'breakdown':
      return !!values.category
    case 'facts':
      return values.items.length > 0
  }
}

/** ②で選んだ候補 → `titleFor` に渡す人向けラベル（K4: property IRI ではなく
 *  `label`）。 */
function labelsFrom(values: FieldValues, agg: MeasureAgg | null): MeasureCardLabels {
  return {
    x: values.x?.label,
    y: values.y?.label,
    item: values.item?.label,
    category: values.category?.label,
    items: values.items.map((i) => i.label),
    agg: agg ?? undefined,
  }
}

export function NewCardForm({ subject, subjectKey, datasetId, onCancel, onCreated, embedded }: NewCardFormProps) {
  const { t } = useTranslation('cards')
  // 呼び出し側の型と揃えるためだけに受け取る（上記コメント参照 — 現時点では
  // ui-form 側のロジックは使わない）。
  void datasetId

  // ---- ①見せ方 ---------------------------------------------------------------
  const [shape, setShape] = useState<MeasureShape | null>(null)

  // ---- 「この 1 件を指す種類」（個体のときだけ・契約メモ §1-3） ----------------
  const isIndividual = subject.kind === 'individual'
  const [kindsState, setKindsState] = useState<{ iri: string; items: LinkingKind[] | null; error: boolean }>({
    iri: '',
    items: null,
    error: false,
  })
  useEffect(() => {
    if (!isIndividual) return
    let cancelled = false
    linkingKinds(subject.iri)
      .then((items) => {
        if (!cancelled) setKindsState({ iri: subject.iri, items, error: false })
      })
      .catch(() => {
        if (!cancelled) setKindsState({ iri: subject.iri, items: null, error: true })
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isIndividual, isIndividual ? subject.iri : ''])
  const kinds = isIndividual && kindsState.iri === subject.iri ? kindsState.items : isIndividual ? null : []
  // `linking_kinds`（PR F14 §1.1）は同じ `class_iri`+`property` でも `anchor`
  // （親）が異なる行を複数返しうる（`sibling`/`sibling_child` は親ごとに別行 —
  // ingest 側の重複排除キーは `(class_iri, where)`）。キーを `class_iri`+
  // `property` だけで組むと、そういう行が同じキーに潰れて React の `key` が
  // 重複し、かつ `chosenKind` の検索が常に先頭の行を返す（=2 つ目以降のラジオ
  // を選んでも先頭の `where` が使われる）。`where` は行ごとに完成形で一意に
  // 決まる（サーバの重複排除と同じ粒度）ので、これを含めてキーにする。
  const kindKey = (k: LinkingKind) => `${k.class_iri}\u0000${k.property}\u0000${JSON.stringify(k.where)}`
  const [chosenKindKey, setChosenKindKey] = useState<string | null>(null)
  const chosenKind = kinds?.find((k) => kindKey(k) === chosenKindKey) ?? (kinds?.length === 1 ? kinds[0] : null)

  // ①→③まで通して使う「対象の種類（class）」。絞り込みページの主語なら
  // spec.class そのもの、1 件のページなら選んだ種類。
  const targetClassIri = subject.kind === 'set' ? subject.spec.class : (chosenKind?.class_iri ?? null)

  // ---- class_schema（②の候補づくりの元） --------------------------------------
  const [schemaState, setSchemaState] = useState<{ classIri: string; schema: MeasureSchemaLike | null; error: boolean }>({
    classIri: '',
    schema: null,
    error: false,
  })
  useEffect(() => {
    if (!targetClassIri) return
    let cancelled = false
    classSchema(targetClassIri)
      .then((s) => {
        if (!cancelled) setSchemaState({ classIri: targetClassIri, schema: s, error: !s })
      })
      .catch(() => {
        if (!cancelled) setSchemaState({ classIri: targetClassIri, schema: null, error: true })
      })
    return () => {
      cancelled = true
    }
  }, [targetClassIri])
  const schema = schemaState.classIri === targetClassIri ? schemaState.schema : null
  const schemaError = schemaState.classIri === targetClassIri && schemaState.error

  // ---- ②項目 -------------------------------------------------------------
  const [fields, setFields] = useState<FieldValues>(EMPTY_FIELDS)
  const [agg, setAgg] = useState<MeasureAgg>('avg')
  // shape が変わったら②の選択を忘れる（役割が変わるので前の選択は意味を
  // 失う — React の「prop/選択が変わったら state を調整する」パターン）。
  const [fieldsFor_, setFieldsFor_] = useState<MeasureShape | null>(null)
  if (fieldsFor_ !== shape) {
    setFieldsFor_(shape)
    setFields(EMPTY_FIELDS)
  }

  const candSet = useMemo(() => (schema && shape ? candidates(schema, shape) : null), [schema, shape])

  // ---- ③条件 ---------------------------------------------------------------
  const defaultWhere: MeasureWhereClause[] = useMemo(() => {
    if (subject.kind === 'set') return subject.spec.where
    // PR F14 §1.3: 組み立て直さず、サーバが完成形で返した `where` をそのまま使う
    // （`direct`/`child_child`/`sibling`/`sibling_child` のどの形でも同じ扱い）。
    if (chosenKind) return chosenKind.where
    return []
  }, [subject, chosenKind])
  // `SetForm.onSubmit` は常に既存の値条件（`SetWhereClause[]`）しか返さない
  // ので、customWhere はこちらの狭い型で持つ（`SetSpec.where` にそのまま渡せる）。
  const [customWhere, setCustomWhere] = useState<SetWhereClause[] | null>(null)
  const [editingWhere, setEditingWhere] = useState(false)
  // 対象の種類が変わったら「条件を変える」の下書きも忘れる。
  const [whereFor, setWhereFor] = useState(targetClassIri)
  if (whereFor !== targetClassIri) {
    setWhereFor(targetClassIri)
    setCustomWhere(null)
    setEditingWhere(false)
  }
  const effectiveWhere: MeasureWhereClause[] = customWhere ?? defaultWhere

  // ---- 「作る」 ---------------------------------------------------------------
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState(false)

  async function handleSubmit() {
    if (!shape || !targetClassIri) return
    setSubmitting(true)
    setSubmitError(false)
    const spec = toCardSpec(
      {
        classIri: targetClassIri,
        shape,
        where: effectiveWhere,
        x: fields.x?.property,
        y: fields.y?.property,
        item: fields.item?.property,
        category: fields.category?.property,
        items: fields.items.map((i) => i.property),
        agg: shape === 'quantity' ? agg : undefined,
      },
      labelsFrom(fields, shape === 'quantity' ? agg : null),
      subjectKey,
      t,
    )
    try {
      await runCard(subject, spec.tool, spec.params)
      addCard(spec)
      onCreated(spec)
    } catch {
      setSubmitError(true)
    } finally {
      setSubmitting(false)
    }
  }

  const complete = shape ? isComplete(shape, fields, shape === 'quantity' ? agg : null) : false
  const canSubmit = complete && !editingWhere && !submitting && !!targetClassIri

  return (
    <div className={embedded ? 'cardpage-setform newcard-form newcard-form--embedded' : 'cardpage-setform newcard-form'}>
      {/* 契約メモ contract_pr_f9.md §1 決定 5・§5 実装順(4): フォームの先頭に
          「この種類の観点として足す」ことを 1 行で示す。ドロワーに埋め込む
          ときは、ドロワー側の案内文と重複するので省く（PR F12 §1-4）。 */}
      {!embedded && <p className="newcard-lead">{t('newcard.lead')}</p>}
      <div className="newcard-step">
        <div className="cardpage-setform-label">{t('newcard.step_shape')}</div>
        <div className="newcard-shapes">
          {shapes().map((s) => (
            <button
              key={s}
              type="button"
              className={s === shape ? 'newcard-shape-tile newcard-shape-tile--active' : 'newcard-shape-tile'}
              onClick={() => setShape(s)}
            >
              {t(`newcard.shape_${s}`)}
            </button>
          ))}
        </div>
      </div>

      {shape && isIndividual && kinds === null && !kindsState.error && <p className="ds-empty-note">{t('page.loading')}</p>}
      {shape && isIndividual && (kindsState.error || kinds?.length === 0) && <p className="ds-empty-note">{t('empty')}</p>}
      {shape && isIndividual && kinds && kinds.length > 1 && (
        <div className="newcard-step">
          <div className="cardpage-setform-label">{t('newcard.kind_pick')}</div>
          <div className="cardpage-setform-choices">
            {kinds.map((k) => (
              <label className="cardpage-setform-check" key={kindKey(k)}>
                <input
                  type="radio"
                  name="newcard-kind"
                  checked={chosenKindKey === kindKey(k)}
                  onChange={() => setChosenKindKey(kindKey(k))}
                />
                {k.class_label}
                {pathLabel(k, t) ? `（${pathLabel(k, t)}）` : ''}
              </label>
            ))}
          </div>
        </div>
      )}

      {shape && targetClassIri && !schema && !schemaError && <p className="ds-empty-note">{t('page.loading')}</p>}
      {shape && targetClassIri && schemaError && <p className="ds-empty-note">{t('render_error')}</p>}

      {shape && schema && candSet && (
        <div className="newcard-step">
          <div className="cardpage-setform-label">{t('newcard.step_items')}</div>
          <div className="cardpage-setform-row">
            {fieldsFor(shape).map((field) => (
              <FieldPicker
                key={field.role}
                role={field.role}
                multiple={field.multiple}
                options={candSet[field.role]}
                value={fields}
                onChange={setFields}
                t={t}
              />
            ))}
            {needsAgg(shape) && (
              // 契約メモ §3 の newcard.* に見出し語のキーが無い（agg_avg 等は
              // 選択肢自体の言葉）。禁止語「集計」の新規キーは作らず、見出しを
              // 出さずセレクタだけを置く（選択肢の日本語だけで意味が通る）。
              <label className="cardpage-setform-check">
                <select value={agg} onChange={(e) => setAgg(e.target.value as MeasureAgg)}>
                  {MEASURE_AGGS.map((a) => (
                    <option key={a} value={a}>
                      {t(`newcard.agg_${a}`)}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>
        </div>
      )}

      {shape && schema && targetClassIri && (
        <div className="newcard-step">
          <div className="cardpage-setform-label">{t('newcard.step_where')}</div>
          <div className="cardpage-setform-choices">
            <label className="cardpage-setform-check">
              <input
                type="radio"
                name="newcard-where"
                checked={!customWhere && !editingWhere}
                onChange={() => {
                  setCustomWhere(null)
                  setEditingWhere(false)
                }}
              />
              {t(subject.kind === 'set' ? 'newcard.where_this_page' : 'newcard.where_this_one')}
            </label>
            <label className="cardpage-setform-check">
              <input
                type="radio"
                name="newcard-where"
                checked={!!customWhere || editingWhere}
                onChange={() => setEditingWhere(true)}
                onClick={() => setEditingWhere(true)}
              />
              {t('set.change_condition')}
            </label>
          </div>
          {editingWhere && (
            <SetForm
              classIri={targetClassIri}
              initialSpec={
                customWhere ? { class: targetClassIri, where: customWhere, order_by: null, limit: 20, source_scope: 'all' } : null
              }
              onCancel={() => setEditingWhere(false)}
              onSubmit={(result) => {
                setCustomWhere(result.spec.where)
                setEditingWhere(false)
              }}
            />
          )}
        </div>
      )}

      {submitError && <p className="ds-empty-note">{t('render_error')}</p>}
      <div className="cardpage-setform-actions">
        <button type="button" className="btn btn--ghost btn--sm" onClick={onCancel}>
          {t('newcard.cancel')}
        </button>
        <button type="button" className="btn btn--soft btn--sm" disabled={!canSubmit} onClick={handleSubmit}>
          {t('newcard.submit')}
        </button>
      </div>
    </div>
  )
}

interface FieldPickerProps {
  role: 'x' | 'y' | 'item' | 'category' | 'items'
  multiple: boolean
  options: MeasureCandidate[]
  value: FieldValues
  onChange: (next: FieldValues) => void
  t: (key: string, options?: Record<string, unknown>) => string
}

const ROLE_LABEL_KEY: Record<FieldPickerProps['role'], string> = {
  x: 'newcard.axis_x',
  y: 'newcard.axis_y',
  item: 'newcard.item',
  category: 'newcard.category',
  items: 'newcard.items',
}

function candidateLabel(c: MeasureCandidate): string {
  const unit = unitLabel(c.unit)
  return unit ? `${c.label}（${unit}）` : c.label
}

/** ②の 1 項目ぶんの入力（単一選択は `<select>`、facts の複数選択だけ
 *  チェックボックス列 — `SetForm.tsx` の分類フィルタと同じ見せ方）。 */
function FieldPicker({ role, multiple, options, value, onChange, t }: FieldPickerProps) {
  if (multiple) {
    const selected = new Set(value.items.map((i) => i.property))
    return (
      <div className="cardpage-setform-label-col">
        <div className="cardpage-setform-label">{t(ROLE_LABEL_KEY[role])}</div>
        <div className="cardpage-setform-choices">
          {options.length === 0 && <span className="cardpage-setform-empty">{t('empty')}</span>}
          {options.map((o) => (
            <label className="cardpage-setform-check" key={o.property}>
              <input
                type="checkbox"
                checked={selected.has(o.property)}
                onChange={(e) => {
                  const items = e.target.checked
                    ? [...value.items, o]
                    : value.items.filter((i) => i.property !== o.property)
                  onChange({ ...value, items })
                }}
              />
              {candidateLabel(o)}
            </label>
          ))}
        </div>
      </div>
    )
  }
  const current = value[role as 'x' | 'y' | 'item' | 'category']
  return (
    <label className="cardpage-setform-check">
      {t(ROLE_LABEL_KEY[role])}
      <select
        value={current?.property ?? ''}
        onChange={(e) => {
          const next = options.find((o) => o.property === e.target.value)
          onChange({ ...value, [role]: next })
        }}
      >
        <option value="">{t('setform.order_none')}</option>
        {options.map((o) => (
          <option key={o.property} value={o.property}>
            {candidateLabel(o)}
          </option>
        ))}
      </select>
    </label>
  )
}
