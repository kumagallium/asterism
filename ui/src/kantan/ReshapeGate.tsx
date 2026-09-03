import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type {
  ReshapeExplodeOp,
  ReshapeFlattenOp,
  ReshapeOpCounts,
  ReshapePivotOp,
  ReshapeSpec,
} from '../api'
import {
  adoptOtherUnit,
  mergeGroupInto,
  opSummary,
  setCarry,
  toggleGroup,
  toggleWideKey,
  totalSummary,
} from './reshapeSpec'

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
  /** 持ち回り列の候補（元の表の列名、R8）。 */
  sourceColumns: string[]
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
  const total = totalSummary(spec, counts)
  const ops = spec.ops
  const activeOp = ops[Math.min(tab, ops.length - 1)]
  const activeIndex = Math.min(tab, ops.length - 1)

  if (!activeOp) return null

  function tabWarn(opIndex: number): boolean {
    const s = opSummary(spec, opIndex, counts)
    return (s.unresolvedOtherUnits ?? 0) > 0 || (s.disabledGroups ?? 0) > 0
  }

  return (
    <>
      <p className="kz-lead">{t('kantan:s12.lead')}</p>

      {/* 常時見える件数の帯（K23 採用デザイン・S4 と同じ見た目）。 */}
      <div className="kz-map-card">
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
          const kindLabel = t(`kantan:s12.tabKind.${op.kind}`)
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
          sourceColumns={sourceColumns}
          busy={busy}
          onChange={onChange}
        />
      )}
      {activeOp.kind === 'explode' && (
        <ExplodeTab
          op={activeOp}
          opIndex={activeIndex}
          sourceColumns={sourceColumns}
          busy={busy}
          onChange={onChange}
          spec={spec}
        />
      )}
      {activeOp.kind === 'flatten' && (
        <FlattenTab
          op={activeOp}
          opIndex={activeIndex}
          sourceColumns={sourceColumns}
          busy={busy}
          onChange={onChange}
          spec={spec}
        />
      )}

      <div className="kz-actions">
        <button type="button" onClick={onProceedApply} disabled={busy}>
          {t('kantan:s12.proceedApply')}
        </button>
        <button type="button" className="btn btn--ghost" onClick={onProceedSkip} disabled={busy}>
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

/** 持ち回る列（R8）: op が自分で消費する列を除いた元の表の列名から選ぶ。 */
function CarryPicker({
  spec,
  opIndex,
  carry,
  consumed,
  sourceColumns,
  busy,
  onChange,
}: {
  spec: ReshapeSpec
  opIndex: number
  carry: string[]
  consumed: Set<string>
  sourceColumns: string[]
  busy: boolean
  onChange: (next: ReshapeSpec) => void
}) {
  const { t } = useTranslation()
  // 候補 = 元の表の列名から、この op 自身が使う列を除いたもの。すでに carry に
  // 入っている列（機械の既定）は候補に無くても必ず出す — 判断表を裏切らない。
  const candidates = [...new Set([...carry, ...sourceColumns.filter((c) => !consumed.has(c))])]
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
              )
            }
          >
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
  sourceColumns,
  busy,
  onChange,
}: {
  spec: ReshapeSpec
  op: ReshapePivotOp
  opIndex: number
  sourceColumns: string[]
  busy: boolean
  onChange: (next: ReshapeSpec) => void
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
      <div className="kz-preview-tablewrap">
        <table className="kz-preview-table kz-cols-table">
          <thead>
            <tr>
              <th>{t('kantan:s12.colUse')}</th>
              <th>{t('kantan:s12.colLabel')}</th>
              <th>{t('kantan:s12.colUnit')}</th>
              <th>{t('kantan:s12.colRows')}</th>
              <th>{t('kantan:s12.colSpellings')}</th>
              <th>{t('kantan:s12.colOtherUnits')}</th>
              <th>{t('kantan:s12.mergeLabel')}</th>
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
              return (
                <tr key={g.slug} className={enabled ? undefined : 'kz-cols-dropped'}>
                  <td>
                    <label className="kz-cols-keep" title={mergedAway ? t('kantan:s12.mergedAwayTitle') : undefined}>
                      <input
                        type="checkbox"
                        checked={enabled}
                        disabled={busy || mergedAway}
                        onChange={() => onChange(toggleGroup(spec, opIndex, g.slug, !enabled))}
                      />
                    </label>
                  </td>
                  <td>{g.label}</td>
                  <td>{g.unit}</td>
                  <td>{(g.rows ?? 0).toLocaleString()}</td>
                  <td>
                    {g.members.length > 1
                      ? g.members.map((m) => m.label).join(', ')
                      : t('kantan:s12.singleSpelling')}
                  </td>
                  <td>
                    {g.other_units && g.other_units.length > 0 ? (
                      <div className="kz-q-options">
                        {g.other_units.map((o) => (
                          <button
                            key={`${o.label} ${o.unit}`}
                            type="button"
                            className="btn btn--ghost btn--sm"
                            disabled={busy}
                            title={t('kantan:s12.adoptOtherUnitTitle', {
                              label: o.label,
                              unit: o.unit,
                            })}
                            onClick={() => onChange(adoptOtherUnit(spec, opIndex, g.slug, o))}
                          >
                            {t('kantan:s12.adoptOtherUnit', { unit: o.unit, rows: o.rows ?? 0 })}
                          </button>
                        ))}
                      </div>
                    ) : (
                      <span className="kz-note">{t('kantan:s12.noOtherUnits')}</span>
                    )}
                  </td>
                  <td>
                    {others.length > 0 ? (
                      <select
                        className="kz-cols-input"
                        disabled={busy || !enabled}
                        value=""
                        aria-label={t('kantan:s12.mergeAria', { label: g.label })}
                        onChange={(e) => {
                          const into = e.target.value
                          if (into) onChange(mergeGroupInto(spec, opIndex, g.slug, into))
                        }}
                      >
                        <option value="">{t('kantan:s12.mergePlaceholder')}</option>
                        {others.map((o) => (
                          <option key={o.slug} value={o.slug}>
                            {o.label}（{o.unit}）
                          </option>
                        ))}
                      </select>
                    ) : (
                      '—'
                    )}
                  </td>
                </tr>
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
        sourceColumns={sourceColumns}
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
  sourceColumns: string[]
  busy: boolean
  onChange: (next: ReshapeSpec) => void
}) {
  const { t } = useTranslation()
  const consumed = new Set(op.arrays)
  return (
    <>
      <p className="kz-note">{t('kantan:s12.explodeIntro', { columns: op.arrays.join(', ') })}</p>
      <CarryPicker
        spec={spec}
        opIndex={opIndex}
        carry={op.carry}
        consumed={consumed}
        sourceColumns={sourceColumns}
        busy={busy}
        onChange={onChange}
      />
    </>
  )
}

function FlattenTab({
  spec,
  op,
  opIndex,
  sourceColumns,
  busy,
  onChange,
}: {
  spec: ReshapeSpec
  op: ReshapeFlattenOp
  opIndex: number
  sourceColumns: string[]
  busy: boolean
  onChange: (next: ReshapeSpec) => void
}) {
  const { t } = useTranslation()
  const consumed = new Set([op.column])
  // wide にできる項目の候補は「機械がいま提案している keys」だけ（サーバは
  // それ以外の候補を返さない、ADR §4.3）。チェックを外しても候補自体は
  // 消えない — 外した項目は long（細長い表）にはそのまま残る。
  const [universe] = useState(op.wide.keys)
  return (
    <>
      <p className="kz-note">{t('kantan:s12.flattenIntro', { column: op.column })}</p>
      <p className="kz-note">{t('kantan:s12.longNote')}</p>
      {universe.length > 0 && (
        <div className="kz-q">
          <p className="kz-q-text">{t('kantan:s12.wideKeysTitle')}</p>
          <p className="kz-note">{t('kantan:s12.wideKeysNote')}</p>
          <div className="kz-q-options">
            {universe.map((key) => (
              <button
                key={key}
                type="button"
                className={`kz-pill${op.wide.keys.includes(key) ? ' selected' : ''}`}
                disabled={busy}
                onClick={() => onChange(toggleWideKey(spec, opIndex, key))}
              >
                {key}
              </button>
            ))}
          </div>
        </div>
      )}
      <CarryPicker
        spec={spec}
        opIndex={opIndex}
        carry={op.carry}
        consumed={consumed}
        sourceColumns={sourceColumns}
        busy={busy}
        onChange={onChange}
      />
    </>
  )
}
