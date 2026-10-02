import { Fragment, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type {
  ReshapeExplodeOp,
  ReshapeFlattenOp,
  ReshapeOp,
  ReshapeOpCounts,
  ReshapePivotOp,
  ReshapeSpec,
  ReshapeUnnestOp,
} from '../api'
import {
  adoptOtherUnit,
  groupDerivedRows,
  groupIsZeroAfterApply,
  groupSourceRows,
  mergeGroupInto,
  opSummary,
  setCarry,
  spellingDisplay,
  toggleGroup,
  toggleUnnestField,
  toggleWideField,
  toggleWideKey,
  totalSummary,
} from './reshapeSpec'

/** 判断表を変えたことを呼び出し側へ伝える。`key` は「どのボタンを押したか」
 *  （処理中にそのボタン自体へ spinner を出すため）。 */
type OnChange = (next: ReshapeSpec, key?: string) => void

/** 押したボタンの中に出す小さなくるくる。 */
function BtnSpin({ on }: { on: boolean }) {
  return on ? <span className="spinner" aria-hidden="true" /> : null
}

/** タブの名前に添える「対象の列名」（人が付けた名前・K4 が禁じる機械の識別子
 *  ではない）。explode は並行列をまとめて示す。 */
function tabTargetColumn(op: ReshapeOp): string {
  if (op.kind === 'explode') return op.arrays.join(', ')
  if (op.kind === 'pivot') return op.label
  // flatten / unnest はどちらも元の列名
  return op.column
}

/** タブの表示名: 「種類 — 列名」。同じ種類の op が複数の元表にまたがるとき
 *  だけ、列名の前に元の表名（ユーザーが付けたファイル名。機械の派生表名では
 *  ない）を添えて区別する（例: comments の flatten と sample_info の flatten
 *  が両方 curves.csv/samples.csv にあると同じ列名でも表が違いうる）。 */
function tabColumnLabel(op: ReshapeOp, multiSource: boolean): string {
  const column = tabTargetColumn(op)
  return multiSource ? `${op.source}: ${column}` : column
}

// 「表の形」画面（ADR source-reshape.md R12・KantanWizard の step 12）。
//
// K17（1 工程 = 1 画面）どおり①「入れる」の内側にとどまり、K4（識別子を生で
// 見せない）どおり派生表は代表表記（label）と単位だけで語る — 表名・slug は
// この画面のどこにも出さない。タブと帯は kantan-mode-two-tier-ux.md の
// 2026-08-27 決定（「タブ自身が ⚠/✓ を持ち、常時見える件数の帯」）をそのまま
// 再利用する。判断はすべて `onChange` を通じて呼び出し側（KantanWizard）へ
// 返し、この関数自身はサーバを呼ばない — 再適用のタイミングは呼び出し側が
// 決める（連打対策の busy も呼び出し側が持つ）。

export function ReshapeGate({
  spec,
  counts,
  sourceColumns,
  busy,
  errorText,
  onChange,
  onProceedApply,
  onProceedSkip,
}: {
  spec: ReshapeSpec
  /** 適用後の実測（R11）。まだ適用していなければ空。 */
  counts: Record<string, ReshapeOpCounts>
  /** 持ち回り列の候補（R8）: 生ソース名 → その列名一覧（サーバが自分の
   *  ソースだけを読んで返す、他ファイルの列は絶対に混ざらない）。 */
  sourceColumns: Record<string, string[]>
  /** 直前の判断（toggle/merge/…）をサーバへ再適用している間。 */
  busy: boolean
  /** 422 の平易文（呼び出し側が `plainError` で組み立て済み）。 */
  errorText?: string
  /** 判断表を変えるたびに呼ぶ — 呼び出し側が再適用する。 */
  onChange: (next: ReshapeSpec) => void
  onProceedApply: () => void
  onProceedSkip: () => void
}) {
  const { t } = useTranslation()
  const [tab, setTab] = useState(0)
  // 最後に押した操作（busy の間だけ、そのボタン自体に spinner を出す）。
  const [pending, setPending] = useState<string | null>(null)
  // busy が false の間は pending を見ない（各ボタンが `busy && pending === key` で
  // 判定する）ので、クリアの effect は要らない。「進む」を押したら必ず外す。
  const act: OnChange = (next, key) => {
    setPending(key ?? null)
    onChange(next)
  }
  const clearThen = (fn: () => void) => () => {
    setPending(null)
    fn()
  }
  const total = totalSummary(spec, counts)
  const ops = spec.ops
  const activeOp = ops[Math.min(tab, ops.length - 1)]
  const activeIndex = Math.min(tab, ops.length - 1)
  const multiSource = new Set(ops.map((op) => op.source)).size > 1

  if (!activeOp) return null

  function tabWarn(opIndex: number): boolean {
    const s = opSummary(spec, opIndex, counts)
    return (
      (s.unresolvedOtherUnits ?? 0) > 0 ||
      (s.disabledGroups ?? 0) > 0 ||
      (s.zeroRowGroups ?? 0) > 0 ||
      (s.notList ?? 0) > 0
    )
  }

  return (
    <>
      <p className="kz-lead">{t('kantan:s12.lead')}</p>

      {/* 常時見える件数の帯（K23 採用デザイン・S4 と同じ見た目）。 */}
      <div className="kz-map-card kz-map-card--sticky">
        <span className="kz-stat">
          <span className="kz-stat-num">{total.sourceRows.toLocaleString()}</span>
          <span className="kz-stat-unit">{t('kantan:s12.unitRows')}</span>
        </span>
        <span className="kz-map-arrow" aria-hidden="true">
          →
        </span>
        <span className="kz-stat kz-stat--kind">
          <span className="kz-stat-label">{t('kantan:s12.derivedLabel')}</span>
          <span className="kz-stat-num">{total.derivedRows.toLocaleString()}</span>
          <span className="kz-stat-unit">{t('kantan:s12.unitRows')}</span>
        </span>
        <span className="kz-map-note">
          {t('kantan:s12.droppedTruncated', { dropped: total.dropped, truncated: total.truncated })}
        </span>
        {busy && (
          <span className="kz-map-busy" role="status">
            <span className="spinner" aria-hidden="true" />
            {t('kantan:s12.recalculating')}
          </span>
        )}
      </div>

      {errorText && (
        <p className="kz-note" role="alert">
          {errorText}
        </p>
      )}

      {/* タブ = 検出された op 1 つにつき 1 つ。⚠/✓ はタブ自身が持つ（裏に隠さない）。 */}
      <div className="kz-q-options" role="tablist" aria-label={t('kantan:s12.tabsLabel')}>
        {ops.map((op, i) => {
          const warn = tabWarn(i)
          const kindLabel = t(`kantan:s12.tabKind.${op.kind}`, {
            column: tabColumnLabel(op, multiSource),
          })
          return (
            <button
              key={i}
              type="button"
              role="tab"
              aria-selected={i === activeIndex}
              aria-label={`${t(warn ? 'kantan:s12.tabWarn' : 'kantan:s12.tabOk')} ${kindLabel}`}
              className={`kz-pill${i === activeIndex ? ' selected' : ''}`}
              onClick={() => setTab(i)}
            >
              <span aria-hidden="true">
                {warn ? '⚠ ' : '✓ '}
                {kindLabel}
              </span>
            </button>
          )
        })}
      </div>

      {activeOp.kind === 'pivot' && (
        <PivotTab
          spec={spec}
          op={activeOp}
          opIndex={activeIndex}
          counts={counts}
          sourceColumns={sourceColumns}
          busy={busy}
          pending={pending}
          onChange={act}
        />
      )}
      {activeOp.kind === 'explode' && (
        <ExplodeTab
          op={activeOp}
          opIndex={activeIndex}
          sourceColumns={sourceColumns}
          busy={busy}
          onChange={act}
          spec={spec}
        />
      )}
      {activeOp.kind === 'flatten' && (
        // key=activeIndex: 2 つ以上の flatten op（例: comments と sample_info）を
        // タブで切り替えても、React が同じ位置の同じコンポーネント型とみなして
        // FlattenTab を使い回してしまうと、内部の `useState(op.wide.keys)` の
        // 初期化が再実行されず、前の op の候補（少ないほう）が残ったままになる
        // （実機: samples タブに wide キーのチェックが出なかった）。key を変えて
        // op ごとに確実に作り直す。
        <FlattenTab
          key={activeIndex}
          op={activeOp}
          opIndex={activeIndex}
          sourceColumns={sourceColumns}
          busy={busy}
          pending={pending}
          onChange={act}
          spec={spec}
        />
      )}
      {activeOp.kind === 'unnest' && (
        // key=activeIndex: FlattenTab と同じ理由（内部 state を op ごとに作り直す）。
        <UnnestTab
          key={activeIndex}
          op={activeOp}
          opIndex={activeIndex}
          counts={counts}
          sourceColumns={sourceColumns}
          busy={busy}
          pending={pending}
          onChange={act}
          spec={spec}
        />
      )}

      <div className="kz-actions">
        <button type="button" onClick={clearThen(onProceedApply)} disabled={busy}>
          {t('kantan:s12.proceedApply')}
        </button>
        <button type="button" className="btn btn--ghost" onClick={clearThen(onProceedSkip)} disabled={busy}>
          {t('kantan:s12.proceedSkip')}
        </button>
        {busy && (
          <span className="kz-note" role="status">
            <span className="spinner" />
            {t('kantan:s12.applying')}
          </span>
        )}
      </div>
    </>
  )
}

/** 持ち回る列（R8）: op が自分で消費する列を除いた「op 自身のソース」の列名
 *  から選ぶ — 他ファイルの列は決して候補に出さない（選ぶと reshape.apply は
 *  そのソースしか読まないので、選んだ列は黙って全行空のまま足される）。 */
function CarryPicker({
  spec,
  opIndex,
  carry,
  consumed,
  columns,
  busy,
  pending,
  onChange,
}: {
  spec: ReshapeSpec
  opIndex: number
  carry: string[]
  consumed: Set<string>
  /** この op 自身のソースの列名（サーバの `source_columns[op.source]`）。
   *  まだ取得できていない／読めなかった場合は空 — そのときは現在の carry に
   *  入っている列だけを出す（他ファイルの列を出すくらいなら何も出さない）。 */
  columns: string[]
  busy: boolean
  /** 最後に押した操作のキー（busy の間だけ意味を持つ）。 */
  pending?: string | null
  onChange: OnChange
}) {
  const { t } = useTranslation()
  // 候補 = op 自身のソースの列名から、この op 自身が使う列を除いたもの。すでに
  // carry に入っている列（機械の既定）は候補に無くても必ず出す — 判断表を
  // 裏切らない。columns が空（未取得／読めない）なら carry だけを出す。
  const candidates = [...new Set([...carry, ...columns.filter((c) => !consumed.has(c))])]
  if (candidates.length === 0) return null
  return (
    <div className="kz-q">
      <p className="kz-q-text">{t('kantan:s12.carryTitle')}</p>
      <p className="kz-note">{t('kantan:s12.carryNote')}</p>
      <div className="kz-q-options">
        {candidates.map((col) => (
          <button
            key={col}
            type="button"
            className={`kz-pill${carry.includes(col) ? ' selected' : ''}`}
            disabled={busy}
            onClick={() =>
              onChange(
                setCarry(
                  spec,
                  opIndex,
                  carry.includes(col) ? carry.filter((c) => c !== col) : [...carry, col],
                ),
                `carry:${col}`,
              )
            }
          >
            <BtnSpin on={busy && pending === `carry:${col}`} />
            {col}
          </button>
        ))}
      </div>
    </div>
  )
}

function PivotTab({
  spec,
  op,
  opIndex,
  counts,
  sourceColumns,
  busy,
  pending,
  onChange,
}: {
  spec: ReshapeSpec
  op: ReshapePivotOp
  opIndex: number
  /** 適用後の実測（R11）。0 行になった群を見分けるために使う。 */
  counts: Record<string, ReshapeOpCounts>
  sourceColumns: Record<string, string[]>
  busy: boolean
  /** 最後に押した操作のキー（busy の間だけ意味を持つ）。 */
  pending?: string | null
  onChange: OnChange
}) {
  const { t } = useTranslation()
  const enabledGroups = op.groups.filter((g) => g.enabled !== false)
  const consumed = new Set(
    [op.label, op.unit, op.value, op.partner?.label, op.partner?.unit, op.partner?.value].filter(
      (c): c is string => !!c,
    ),
  )
  return (
    <>
      <p className="kz-note">{t('kantan:s12.pivotIntro')}</p>
      <div className="kz-preview-tablewrap" aria-busy={busy}>
        <table
          className={`kz-preview-table kz-cols-table kz-reshape-groups${busy ? ' kz-reshape-busy' : ''}`}
        >
          <thead>
            <tr>
              <th>{t('kantan:s12.colUse')}</th>
              <th>{t('kantan:s12.colProperty')}</th>
              <th>{t('kantan:s12.colRows')}</th>
              <th>{t('kantan:s12.colActions')}</th>
            </tr>
          </thead>
          <tbody>
            {op.groups.map((g) => {
              const enabled = g.enabled !== false
              const others = enabledGroups.filter((o) => o.slug !== g.slug)
              // 「別の表に合流」で members を空にした群は、使うに戻せない
              // （戻すと一致しようのない空の派生表ができる — reshapeSpec.ts の
              // toggleGroup がサーバ呼び出し以前に拒むが、押せてしまうこと
              // 自体を避ける）。
              const mergedAway = !enabled && g.members.length === 0
              // 「行数」セルは常に元の表で一致した行数（全群で同じ意味）。
              // 派生表の実際の行数（展開後の点の数、単位が違う）は求まる
              // ときだけ小さく添える — 同じセルに単位の違う数を混在させない。
              const sourceRows = groupSourceRows(g)
              const derivedRows = groupDerivedRows(g, opIndex, counts)
              const zeroAfterApply = groupIsZeroAfterApply(g, opIndex, counts)
              const hasOthers = !!g.other_units && g.other_units.length > 0
              return (
                <Fragment key={g.slug}>
                  <tr className={enabled ? undefined : 'kz-cols-dropped'}>
                    <td>
                      <label
                        className="kz-cols-keep"
                        title={mergedAway ? t('kantan:s12.mergedAwayTitle') : undefined}
                      >
                        <BtnSpin on={busy && pending === `toggle:${g.slug}`} />
                        <input
                          type="checkbox"
                          checked={enabled}
                          disabled={busy || mergedAway}
                          onChange={() =>
                            onChange(toggleGroup(spec, opIndex, g.slug, !enabled), `toggle:${g.slug}`)
                          }
                        />
                      </label>
                    </td>
                    <td className="kz-reshape-prop">
                      <strong>{g.label}</strong>
                      <span className="kz-reshape-unit">{g.unit}</span>
                      {g.members.length > 1 && (
                        <span className="kz-note kz-reshape-spellings">
                          {t('kantan:s12.spellingsNote', {
                            spellings: g.members.map((m) => spellingDisplay(m, g.unit)).join(', '),
                          })}
                        </span>
                      )}
                    </td>
                    <td>
                      {sourceRows.toLocaleString()}
                      {derivedRows !== undefined && (
                        <span className="kz-note" style={{ display: 'block' }}>
                          {t('kantan:s12.derivedRowsNote', { count: derivedRows })}
                        </span>
                      )}
                    </td>
                    <td className="kz-reshape-actions">
                      {hasOthers && (
                        <div className="kz-q-options">
                          {g.other_units?.map((o) => {
                            const key = `adopt:${g.slug}:${o.label}:${o.unit}`
                            return (
                              <button
                                key={key}
                                type="button"
                                className="btn btn--ghost btn--sm"
                                disabled={busy}
                                title={t('kantan:s12.adoptOtherUnitTitle', {
                                  label: o.label,
                                  unit: o.unit,
                                })}
                                onClick={() => onChange(adoptOtherUnit(spec, opIndex, g.slug, o), key)}
                              >
                                <BtnSpin on={busy && pending === key} />
                                {t('kantan:s12.adoptOtherUnit', { unit: o.unit, rows: o.rows ?? 0 })}
                              </button>
                            )
                          })}
                        </div>
                      )}
                      {others.length > 0 && (
                        <label className="kz-reshape-merge">
                          <span className="kz-note">{t('kantan:s12.mergeLabel')}</span>
                          <BtnSpin on={busy && pending === `merge:${g.slug}`} />
                          <select
                            className="kz-cols-input"
                            disabled={busy || !enabled}
                            value=""
                            aria-label={t('kantan:s12.mergeAria', { label: g.label })}
                            onChange={(e) => {
                              const into = e.target.value
                              if (into)
                                onChange(mergeGroupInto(spec, opIndex, g.slug, into), `merge:${g.slug}`)
                            }}
                          >
                            <option value="">{t('kantan:s12.mergePlaceholder')}</option>
                            {others.map((o) => (
                              <option key={o.slug} value={o.slug}>
                                {o.label}（{o.unit}）
                              </option>
                            ))}
                          </select>
                        </label>
                      )}
                      {!hasOthers && others.length === 0 && '—'}
                    </td>
                  </tr>
                  {zeroAfterApply && (
                    <tr className="kz-cols-dropped">
                      <td colSpan={4}>
                        <span className="kz-note" role="alert">
                          ⚠ {t('kantan:s12.groupZeroRows')}
                        </span>
                      </td>
                    </tr>
                  )}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      </div>
      {op.partner && (
        <p className="kz-note">
          {t('kantan:s12.partnerLabel', { label: op.partner.label, unit: op.partner.unit })}
        </p>
      )}
      <CarryPicker
        spec={spec}
        opIndex={opIndex}
        carry={op.carry}
        consumed={consumed}
        columns={sourceColumns[op.source] ?? []}
        busy={busy}
        onChange={onChange}
      />
    </>
  )
}

function ExplodeTab({
  spec,
  op,
  opIndex,
  sourceColumns,
  busy,
  onChange,
}: {
  spec: ReshapeSpec
  op: ReshapeExplodeOp
  opIndex: number
  sourceColumns: Record<string, string[]>
  busy: boolean
  onChange: OnChange
}) {
  const { t } = useTranslation()
  const consumed = new Set(op.arrays)
  return (
    <div aria-busy={busy} className={busy ? 'kz-reshape-busy' : undefined}>
      <p className="kz-note">{t('kantan:s12.explodeIntro', { columns: op.arrays.join(', ') })}</p>
      <CarryPicker
        spec={spec}
        opIndex={opIndex}
        carry={op.carry}
        consumed={consumed}
        columns={sourceColumns[op.source] ?? []}
        busy={busy}
        onChange={onChange}
      />
    </div>
  )
}

function FlattenTab({
  spec,
  op,
  opIndex,
  sourceColumns,
  busy,
  pending,
  onChange,
}: {
  spec: ReshapeSpec
  op: ReshapeFlattenOp
  opIndex: number
  sourceColumns: Record<string, string[]>
  busy: boolean
  /** 最後に押した操作のキー（busy の間だけ意味を持つ）。 */
  pending?: string | null
  onChange: OnChange
}) {
  const { t } = useTranslation()
  const consumed = new Set([op.column])
  // R23: 候補は op.wide.candidates（値が 1 件でもある全キー）。25% は「既定の
  // チェック」であって足切りではない。古い spec（candidates 無し）は keys だけを
  // 割合不明で出す。外した項目は long（細長い表）にはそのまま残る。
  const [showAll, setShowAll] = useState(false)
  type Cand = { key: string; rate?: number }
  const candidates: Cand[] =
    op.wide.candidates && op.wide.candidates.length > 0
      ? op.wide.candidates
      : op.wide.keys.map((key): Cand => ({ key }))
  // 選択済みなのに候補に無いキー（手で書いた spec）も落とさず出す。
  const known = new Set(candidates.map((c) => c.key))
  const all: Cand[] = [...candidates, ...op.wide.keys.filter((k) => !known.has(k)).map((key): Cand => ({ key }))]
  const isDefault = (c: Cand) =>
    op.wide.keys.includes(c.key) || (c.rate ?? 0) >= 0.25
  const shown = all.filter((c) => showAll || isDefault(c))
  const hiddenCount = all.length - all.filter(isDefault).length
  const fieldCands = op.wide.field_candidates ?? []
  const selectedFields = op.wide.fields ?? []
  return (
    <div aria-busy={busy} className={busy ? 'kz-reshape-busy' : undefined}>
      <p className="kz-note">{t('kantan:s12.flattenIntro', { column: op.column })}</p>
      <p className="kz-note">{t('kantan:s12.longNote')}</p>
      {all.length > 0 && (
        <div className="kz-q">
          <p className="kz-q-text">{t('kantan:s12.wideKeysTitle')}</p>
          <p className="kz-note">{t('kantan:s12.wideKeysNote')}</p>
          {candidates.some((c) => c.rate !== undefined) && (
            <p className="kz-note">{t('kantan:s12.wideKeysNoteRate')}</p>
          )}
          <div className="kz-q-options">
            {shown.map((c) => {
              const key = `wide:${c.key}`
              return (
                <button
                  key={c.key}
                  type="button"
                  className={`kz-pill${op.wide.keys.includes(c.key) ? ' selected' : ''}`}
                  disabled={busy}
                  onClick={() => onChange(toggleWideKey(spec, opIndex, c.key), key)}
                >
                  <BtnSpin on={busy && pending === key} />
                  {c.rate === undefined
                    ? c.key
                    : t('kantan:s12.wideKeyPill', {
                        key: c.key,
                        percent: c.rate > 0 && c.rate < 0.01 ? '<1' : Math.round(c.rate * 100),
                      })}
                </button>
              )
            })}
          </div>
          {hiddenCount > 0 && (
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              style={{ alignSelf: 'flex-start', width: 'auto', marginTop: 8 }}
              aria-expanded={showAll}
              onClick={() => setShowAll((v) => !v)}
            >
              {showAll
                ? t('kantan:s12.hideMoreKeys')
                : t('kantan:s12.showMoreKeys', { count: hiddenCount })}
            </button>
          )}
        </div>
      )}
      {fieldCands.length >= 2 && (
        <div className="kz-q">
          <p className="kz-q-text">{t('kantan:s12.wideFieldsTitle')}</p>
          <p className="kz-note">{t('kantan:s12.wideFieldsNote')}</p>
          <div className="kz-q-options">
            {fieldCands.map((f) => {
              const key = `field:${f.field}`
              const selected = selectedFields.includes(f.field)
              return (
                <button
                  key={f.field}
                  type="button"
                  className={`kz-pill${selected ? ' selected' : ''}`}
                  // 最後の 1 つは外せないが、disabled にすると「選ばれていない」ように
                  // 薄く見えてしまう — 選択の見た目は保ったまま押しても何もしない。
                  disabled={busy}
                  aria-disabled={selected && selectedFields.length <= 1}
                  title={selected && selectedFields.length <= 1 ? t('kantan:s12.wideFieldsKeepOne') : undefined}
                  onClick={() => {
                    if (selected && selectedFields.length <= 1) return
                    onChange(toggleWideField(spec, opIndex, f.field), key)
                  }}
                >
                  <BtnSpin on={busy && pending === key} />
                  {f.field}
                </button>
              )
            })}
          </div>
        </div>
      )}
      <CarryPicker
        spec={spec}
        opIndex={opIndex}
        carry={op.carry}
        consumed={consumed}
        columns={sourceColumns[op.source] ?? []}
        busy={busy}
        onChange={onChange}
      />
    </div>
  )
}

function UnnestTab({
  spec,
  op,
  opIndex,
  counts,
  sourceColumns,
  busy,
  pending,
  onChange,
}: {
  spec: ReshapeSpec
  op: ReshapeUnnestOp
  opIndex: number
  /** 適用後の実測（読めなかったセル・空の要素の注記に使う）。 */
  counts: Record<string, ReshapeOpCounts>
  sourceColumns: Record<string, string[]>
  busy: boolean
  /** 最後に押した操作のキー（busy の間だけ意味を持つ）。 */
  pending?: string | null
  onChange: OnChange
}) {
  const { t } = useTranslation()
  const consumed = new Set([op.column])
  const [showAll, setShowAll] = useState(false)
  // shape 省略（古い spec）は mixed 扱い。scalar のときは「列にする項目」を出さない。
  const hasFields = (op.shape ?? 'mixed') !== 'scalar'
  // 候補は field_candidates（割合つき）、無ければ fields だけを割合不明で出す。
  // 5% は「既定のチェック」であって足切りではない（flatten と同じ考え方）。
  type Cand = { field: string; rate?: number }
  const candidates: Cand[] =
    op.field_candidates && op.field_candidates.length > 0
      ? op.field_candidates.map((c): Cand => ({ field: c.field, rate: c.rate }))
      : op.fields.map((field): Cand => ({ field }))
  // 選択済みなのに候補に無い項目（手で書いた spec）も落とさず出す。
  const known = new Set(candidates.map((c) => c.field))
  const all: Cand[] = [
    ...candidates,
    ...op.fields.filter((f) => !known.has(f)).map((field): Cand => ({ field })),
  ]
  const isDefault = (c: Cand) => op.fields.includes(c.field) || (c.rate ?? 0) >= 0.05
  const shown = all.filter((c) => showAll || isDefault(c))
  const hiddenCount = all.length - all.filter(isDefault).length
  const c = counts[String(opIndex)]
  const notList = c?.cells_not_list ?? 0
  const emptyElements = c?.elements_empty ?? 0
  return (
    <div aria-busy={busy} className={busy ? 'kz-reshape-busy' : undefined}>
      <p className="kz-note">
        {t(
          hasFields ? 'kantan:s12.unnestIntroObject' : 'kantan:s12.unnestIntroScalar',
          { column: op.column },
        )}
      </p>
      {hasFields && all.length > 0 && (
        <div className="kz-q">
          <p className="kz-q-text">{t('kantan:s12.unnestFieldsTitle')}</p>
          <p className="kz-note">{t('kantan:s12.unnestFieldsNote')}</p>
          {candidates.some((x) => x.rate !== undefined) && (
            <p className="kz-note">{t('kantan:s12.unnestFieldsNoteRate')}</p>
          )}
          <div className="kz-q-options">
            {shown.map((cand) => {
              const key = `unnest:${cand.field}`
              return (
                <button
                  key={cand.field}
                  type="button"
                  className={`kz-pill${op.fields.includes(cand.field) ? ' selected' : ''}`}
                  disabled={busy}
                  onClick={() => onChange(toggleUnnestField(spec, opIndex, cand.field), key)}
                >
                  <BtnSpin on={busy && pending === key} />
                  {cand.rate === undefined
                    ? cand.field
                    : t('kantan:s12.wideKeyPill', {
                        key: cand.field,
                        percent:
                          cand.rate > 0 && cand.rate < 0.01 ? '<1' : Math.round(cand.rate * 100),
                      })}
                </button>
              )
            })}
          </div>
          {hiddenCount > 0 && (
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              style={{ alignSelf: 'flex-start', width: 'auto', marginTop: 8 }}
              aria-expanded={showAll}
              onClick={() => setShowAll((v) => !v)}
            >
              {showAll
                ? t('kantan:s12.hideMoreKeys')
                : t('kantan:s12.showMoreKeys', { count: hiddenCount })}
            </button>
          )}
        </div>
      )}
      {notList > 0 && (
        <p className="kz-note" role="alert">
          ⚠ {t('kantan:s12.unnestNotList', { count: notList })}
        </p>
      )}
      {emptyElements > 0 && (
        <p className="kz-note">{t('kantan:s12.unnestEmptyElements', { count: emptyElements })}</p>
      )}
      <CarryPicker
        spec={spec}
        opIndex={opIndex}
        carry={op.carry}
        consumed={consumed}
        columns={sourceColumns[op.source] ?? []}
        busy={busy}
        pending={pending}
        onChange={onChange}
      />
    </div>
  )
}
