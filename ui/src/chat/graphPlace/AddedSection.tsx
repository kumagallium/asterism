// 候補のタブの節「足した関係 N」（R3c・契約 §2）。人が足した関係の一覧・直す・使うのをやめる・削除・「関係を足す」（空の入力欄）。
// 組み込みの 12 種はここには出さない。開閉・入力欄・確かめは節の中で持ち、書いたあとは親へ知らせる（目録と一覧の読み直し）。
import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { AI_LIMIT, addFailureOf, deleteRelation, failureText, nameOf, patchRelation, retireRelation, type AddedRelation } from './relationAddApi'
import { buildPatch, editOptional, otherLangHint, valuesOf, type RelationFormValues } from './relationFormModel'
import { RelationForm } from './RelationForm'
import type { AddedLoad } from './useAddedRelations'

type Lang = 'ja' | 'en'
const dateOf = (iso: string | null): string => (iso ? iso.slice(0, 10) : '')

export function UsageTag({ r }: { r: AddedRelation }) {
  const { t } = useTranslation('graphPlace')
  if (r.retired) return <em className="gp-tag gp-tag--retired" data-testid="gp-added-retired">{t('added.retired')}</em>
  if (r.used === null) return <span className="gp-ev" data-testid="gp-added-usage-unknown">{t('added.usageUnknown')}</span>
  return r.used === 0 ? <span className="gp-ev" data-testid="gp-added-usage">{t('added.unused')}</span> : <span className="gp-ev" data-testid="gp-added-usage">{t('added.used', { n: r.used })}</span>
}

type Confirm = 'delete' | 'retire' | null

export interface AddedRowProps {
  r: AddedRelation
  lang: Lang
  open: boolean
  onToggle: () => void
  /** 書いた（直す・やめる・削除）あと、または 404/409 で一覧が古いと分かったときに親へ（目録と一覧の読み直し）。`msg` は親の 1 行に出す文。 */
  onChanged: (msg?: string) => void
  /** 操作の始まり（親の知らせを消す）。 */
  onStart?: () => void
  /** 読み直しの途中。ボタンを無効にする。 */
  busy?: boolean
}

export function AddedRow({ r, lang, open, onToggle, onChanged, onStart, busy = false }: AddedRowProps) {
  const { t } = useTranslation('graphPlace')
  const [editing, setEditing] = useState<RelationFormValues | null>(null)
  const [confirm, setConfirm] = useState<Confirm>(null)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const unused = r.used === 0
  const locked = pending || busy

  /** `ctx`: 409 の意味（削除＝使用中）。404・409 は一覧が古いので、確かめを閉じて読み直す（使用中なら [使うのをやめる] 側の行になる）。 */
  const run = async (job: () => Promise<unknown>, after: () => void, ctx: 'add' | 'delete' | 'other') => {
    onStart?.()
    setPending(true)
    setError(null)
    try {
      await job()
      after()
      onChanged()
    } catch (e) {
      const f = addFailureOf(e, ctx)
      const text = failureText(t, f)
      setError(text)
      if (f.kind === 'notFound' || f.kind === 'inUse') {
        setConfirm(null)
        onChanged(f.kind === 'notFound' ? text : undefined)
      }
    } finally {
      setPending(false)
    }
  }

  return (
    <li className={`gp-item${open ? ' open' : ''}${r.retired ? ' gp-item--retired' : ''}`} data-testid="gp-added-row" data-name={r.name}>
      <button type="button" className="gp-item-h" aria-expanded={open} data-testid="gp-added-toggle" onClick={onToggle}>
        <span className="gp-sent">{nameOf(r, lang)}</span>
        <span className="gp-meta">
          <span className="gp-sym" aria-label={r.symmetric ? t('added.symmetric') : t('added.directed')} data-testid="gp-added-dir">{r.symmetric ? '↔' : '→'}</span>
          {r.toPropertyOnly && <em className="gp-tag" data-testid="gp-added-toprop">{t('added.toProperty')}</em>}
          <UsageTag r={r} />
        </span>
        <span className="gp-chev" aria-hidden="true">›</span>
      </button>
      {open && (
        <div className="gp-item-b">
          {editing ? (
            <RelationForm
              testPrefix="gp-edit"
              values={editing}
              onChange={setEditing}
              editing
              optional={editOptional(r, lang)}
              hints={otherLangHint(r, lang)}
              pending={locked}
              error={error}
              submitLabel={t('act.save')}
              onCancel={() => { setEditing(null); setError(null) }}
              onSubmit={() => void run(() => patchRelation(r.name, buildPatch(editing, lang, r)), () => setEditing(null), 'add')}
            />
          ) : (
            <>
              <p className="gp-lbl">{t('added.definition')}</p>
              <p className="gp-body" data-testid="gp-added-definition">{r.definition[lang] || r.definition.ja || r.definition.en}</p>
              {r.exactMatch && (
                <p className="gp-ev" data-testid="gp-added-exact">{t('added.exact')} {r.exactMatch}</p>
              )}
              {r.addedAt && <p className="gp-ev" data-testid="gp-added-date">{t('added.addedAt', { date: dateOf(r.addedAt) })}</p>}
              {confirm ? (
                <div className="gp-confirm" data-testid="gp-added-confirm" role="group">
                  <p className="gp-warn">{confirm === 'delete' ? t('added.confirmDelete') : t('added.confirmRetire')}</p>
                  <div className="gp-acts">
                    <button type="button" className="btn btn--sm btn--accent" disabled={locked} data-testid="gp-added-yes" onClick={() => void run(() => (confirm === 'delete' ? deleteRelation(r.name) : retireRelation(r.name)), () => setConfirm(null), confirm === 'delete' ? 'delete' : 'other')}>{t('act.yes')}</button>
                    <button type="button" className="btn btn--sm btn--ghost" data-testid="gp-added-no" onClick={() => { setConfirm(null); setError(null) }}>{t('act.keep')}</button>
                  </div>
                </div>
              ) : (
                <div className="gp-acts">
                  <button type="button" className="btn btn--sm btn--ghost" data-testid="gp-added-edit" disabled={locked} onClick={() => { setEditing(valuesOf(r, lang)); setError(null) }}>{t('act.edit')}</button>
                  {!r.retired && unused && <button type="button" className="btn btn--sm btn--ghost" data-testid="gp-added-delete" disabled={locked} onClick={() => setConfirm('delete')}>{t('act.delete')}</button>}
                  {!r.retired && !unused && <button type="button" className="btn btn--sm btn--ghost" data-testid="gp-added-retire" disabled={locked} onClick={() => setConfirm('retire')}>{t('act.retire')}</button>}
                </div>
              )}
              {error && !editing && <p className="gp-err" role="alert" data-testid="gp-added-error">{error}</p>}
            </>
          )}
        </div>
      )}
    </li>
  )
}

export interface AddedSectionProps {
  load: AddedLoad
  lang: Lang
  openName: string | null
  onToggle: (name: string) => void
  onChanged: (msg?: string) => void
  onStart?: () => void
  /** 空の入力欄（「関係を足す」）を出す部品。親が持つ（種類の行からの入力欄と同じ作り）。 */
  adder: ReactNode
  adding: boolean
  onAdd: () => void
}

/** 0 件でも見出しと「関係を足す」は出す。読み込み中・失敗は 1 行。 */
export function AddedSection({ load, lang, openName, onToggle, onChanged, onStart, adder, adding, onAdd }: AddedSectionProps) {
  const { t } = useTranslation('graphPlace')
  const { status, relations, unavailable } = load
  const active = relations.filter((r) => !r.retired).length
  return (
    <section className="gp-un gp-added" data-testid="gp-added" aria-label={t('added.title', { n: relations.length })}>
      <h3 className="gp-un-h">{t('added.title', { n: relations.length })}</h3>
      {status === 'loading' && relations.length === 0 && <p className="gp-note" role="status" data-testid="gp-added-loading">{t('added.loading')}</p>}
      {status === 'error' && <p className="gp-note" role="alert" data-testid="gp-added-failed">{unavailable ? t('added.unavailable') : t('added.failed')}</p>}
      {active > AI_LIMIT && <p className="gp-note" data-testid="gp-added-limit">{t('added.limit', { n: AI_LIMIT })}</p>}
      {relations.length > 0 && (
        <ul className="gp-list" data-testid="gp-added-list">
          {relations.map((r) => (
            <AddedRow key={r.name} r={r} lang={lang} open={openName === r.name} onToggle={() => onToggle(r.name)} onChanged={onChanged} onStart={onStart} busy={load.busy} />
          ))}
        </ul>
      )}
      {status === 'ok' && relations.length === 0 && <p className="gp-note" data-testid="gp-added-empty">{t('added.empty')}</p>}
      {adding ? adder : !unavailable && (
        <div className="gp-acts">
          <button type="button" className="btn btn--sm btn--ghost" data-testid="gp-added-add" disabled={load.busy} onClick={onAdd}>{t('act.addRelation')}</button>
        </div>
      )}
    </section>
  )
}
