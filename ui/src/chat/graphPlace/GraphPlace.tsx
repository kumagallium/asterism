// 「知識グラフ」の場所（PR 14e-1・設計 §23.11）: 中央の絞り込み（GraphFilters）と、右の「関係・用語・候補」の一覧（GraphPlaceSide）。
// 図は ChatApp の mapBlock が描く（台帳の下書きを足して 3 つの場所で同じ）。ここは台帳の絞り込み・一覧・確認と「違う」。
// 確認の意味: 台帳に「人が確認した」と書くだけ（ストアに置くのは 14e-1b）。「全部まとめて確認」は置かない（語ごとの確認だけ）。
import { useMemo, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { reloadRelationCatalog, useCatalog } from '../../relations/catalog'
import { draftRelationLabel } from '../map/mapModel'
import { KnowledgeApiError, type ReviewAction, type ReviewResult, type StoreReport } from './knowledgeRelationsApi'
import { useStoreText } from './storeText'
import { ALL, filterCandidates, filterRelations, firstRows, PAGE_SIZE, relKinds, pinFirst, summarizeTerms, unconfirmedKeys, type LedgerFilter, type StatusFilter, type TermSummary } from './ledgerView'
import { CandidateRow, RelationRow, TermRow } from './LedgerRows'
import { UnstatedSection } from './UnstatedSection'
import { unstatedArgs, useUnstated } from './useUnstated'
import { AddedSection } from './AddedSection'
import { AddRelation } from './AddRelation'
import { useAddedRelations } from './useAddedRelations'
import { rowId, type AssignForm } from './rowFns'
import { writeFailureOf, type LedgersApi } from './useLedgers'
import './graphPlace.css'

type Lang = 'ja' | 'en'

/** 「言えなかった関係の種類」を 1 度に出す群の数（「もっと見る」で増やす）。 */
const UNSTATED_PAGE = 10
/** 「足した関係」の空の入力欄を開いているときの鍵（種類の鍵とぶつからない）。 */
const NEW_KEY = '\u0000new'

const STATUSES: StatusFilter[] = ['all', 'unconfirmed', 'confirmed', 'rejected']

/** 図の凡例（1 つ・3 つの場所すべてで同じ）: 用語の 4 色／データの種類／データセット／確認済み（濃い線）／未確認（薄い点・線）。 */
export function MapLegend({ datasets = true }: { datasets?: boolean }) {
  const { t } = useTranslation('graphPlace')
  return (
    <div className="gp-legend" role="group" aria-label={t('legend.aria')} data-testid="gp-legend">
      <span><i className="gp-dot gp-tone--m" aria-hidden="true" />{t('legend.material')}</span>
      <span><i className="gp-dot gp-tone--p" aria-hidden="true" />{t('legend.property')}</span>
      <span><i className="gp-dot gp-tone--t" aria-hidden="true" />{t('legend.method')}</span>
      <span><i className="gp-dot gp-tone--c" aria-hidden="true" />{t('legend.concept')}</span>
      {datasets && <span><i className="gp-sym gp-sym--kind" aria-hidden="true">●</i>{t('legend.kind')}</span>}
      {datasets && <span><i className="gp-sym gp-sym--dataset" aria-hidden="true">⊙</i>{t('legend.dataset')}</span>}
      <span><i className="gp-ln" aria-hidden="true" />{t('legend.confirmed')}</span>
      <span><i className="gp-ln gp-ln--u" aria-hidden="true" />{t('legend.unconfirmed')}</span>
      <span><i className="gp-sym" aria-hidden="true">→</i>{t('legend.arrow')}</span>
    </div>
  )
}
/** 以前の名前（役割の凡例）。同じ部品。 */
export const RoleLegend = MapLegend

/** 中央の図の上: 取り込み元・状態・関係の種類の絞り込みと「候補も出す」。図と右の一覧の両方に効く。 */
/** `datasets`: 「データセットも出す」の切り替え（無ければ出さない）。 */
export function GraphFilters({ api, lang, datasets }: { api: LedgersApi; lang: Lang; datasets?: { checked: boolean; onChange: (on: boolean) => void } }) {
  const { t } = useTranslation('graphPlace')
  const f = api.filter
  const set = (patch: Partial<LedgerFilter>) => api.setFilter({ ...f, ...patch })
  const nCandidates = filterCandidates(api.ledgers, f).length
  return (
    <div className="gp-filters-wrap" data-testid="gp-filters">
      {api.failed.length > 0 && (
        <p className="gp-note" role="status" data-testid="gp-failed">{t('state.failedSources', { names: api.failed.join('、') })}</p>
      )}
      {api.status === 'error' && <p className="gp-note" role="alert">{t('state.loadFailed')}</p>}
      <div className="gp-filters" role="group" aria-label={t('filters.aria')}>
        <label className="gp-sel">
          {t('filters.source')}
          <select data-testid="gp-filter-source" value={f.source} onChange={(e) => set({ source: e.target.value })}>
            <option value={ALL}>{t('filters.all')}</option>
            {api.ledgers.map((l) => (
              <option key={l.source.source} value={l.source.source}>{l.source.title}</option>
            ))}
          </select>
        </label>
        <label className="gp-sel">
          {t('filters.status')}
          <select data-testid="gp-filter-status" value={f.status} onChange={(e) => set({ status: e.target.value as StatusFilter })}>
            {STATUSES.map((s) => (
              <option key={s} value={s}>{t(`status.${s}`)}</option>
            ))}
          </select>
        </label>
        <label className="gp-sel">
          {t('filters.rel')}
          <select data-testid="gp-filter-rel" value={f.rel} onChange={(e) => set({ rel: e.target.value })}>
            <option value={ALL}>{t('filters.all')}</option>
            {relKinds(api.ledgers, f.status).map((r) => (
              <option key={r} value={r}>{draftRelationLabel(r, lang)}</option>
            ))}
          </select>
        </label>
      </div>
      <div className="gp-togs">
        <label className="gp-tog">
          <input type="checkbox" data-testid="gp-filter-candidates" checked={f.candidates} onChange={(e) => set({ candidates: e.target.checked })} />
          {t('candidates.toggle')} <span className="gp-cnt">{t('candidates.count', { n: nCandidates })}</span>
        </label>
        {datasets && (
          <label className="gp-tog">
            <input type="checkbox" data-testid="gp-filter-datasets" checked={datasets.checked} onChange={(e) => datasets.onChange(e.target.checked)} />
            {t('filters.datasets')}
          </label>
        )}
      </div>
    </div>
  )
}

type Tab = 'relations' | 'terms' | 'candidates'

export interface GraphPlaceSideProps {
  api: LedgersApi
  lang: Lang
  /** 図で押した台帳の語（`nonce` が変わるたびに「用語」のタブでその語を開く）。 */
  focus: { source: string; termId: string; nonce: number } | null
  onOpenImport: () => void
  /** 今ある本物の点（ストアの用語・データセット）を押したときの 1 枚と「関係・データ・問い」。無ければ null＝一覧だけ。 */
  point: ReactNode | null
  onClosePoint: () => void
}

/** 右の一覧（知識グラフの場所だけ）。「関係」「用語」「候補」の 3 タブ。 */
export function GraphPlaceSide({ api, lang, focus, onOpenImport, point, onClosePoint }: GraphPlaceSideProps) {
  const { t } = useTranslation('graphPlace')
  useCatalog() // API の目録が重なったら、関係の表示名・選択肢を引き直す
  const storeText = useStoreText()
  const [tab, setTab] = useState<Tab>('relations')
  const [open, setOpen] = useState<string | null>(null)
  const [shown, setShown] = useState<Record<Tab, number>>({ relations: PAGE_SIZE, terms: PAGE_SIZE, candidates: PAGE_SIZE })
  /** 「言えなかった関係の種類」の節: 開いている群・見せている群の数（候補の行の開閉とは別に持つ）。 */
  const [unOpen, setUnOpen] = useState<string | null>(null)
  const [unShown, setUnShown] = useState(UNSTATED_PAGE)
  const [pending, setPending] = useState(false)
  const [msg, setMsg] = useState<{ id: string; text: string } | null>(null)
  /** 確認の返りの `store` が「置けなかった」「置く段が落ちた」を言うとき、その場の 1 行（次の操作で消える）。 */
  const [notice, setNotice] = useState<string | null>(null)
  const [form, setForm] = useState<AssignForm>({ rel: '', subject: 'a' })
  // 図で台帳の語を押した: 「用語」のタブでその語を開く（描画中に前の値と比べて合わせる）。別の場所から移ってきて作られた直後も、残っている focus で開く
  const [seenFocus, setSeenFocus] = useState(0)
  if (focus && focus.nonce !== seenFocus) {
    setSeenFocus(focus.nonce)
    setTab('terms')
    setOpen(`${focus.source}|${focus.termId}`)
  }

  const f = api.filter
  const relations = filterRelations(api.ledgers, f)
  // 図で押した語は先頭に（上の 1 枚のすぐ下で開いて見える。並びはそのほかは変えない）
  const terms = pinFirst(summarizeTerms(relations), focus ? `${focus.source}|${focus.termId}` : null)
  const candidates = filterCandidates(api.ledgers, f)

  // 節は取り込み元の絞り込みだけ効く（状態・関係の種類は効かせない）。台帳を読み直したら一緒に引き直す
  const { source: unSource, enabled: unEnabled } = unstatedArgs(api, tab)
  // 関係を足す・直す・やめる・消すたびに `relVersion` を進め、目録と「言えなかった関係の種類」「足した関係」を読み直す（R3c）
  const [relVersion, setRelVersion] = useState(0)
  const unTrigger = useMemo(() => ({}), [api.ledgers, relVersion]) // eslint-disable-line react-hooks/exhaustive-deps
  const unstated = useUnstated(unTrigger, lang, unSource, unEnabled)
  const added = useAddedRelations(unTrigger, tab === 'candidates')
  const [adding, setAdding] = useState<string | null>(null) // 入力欄を開いている種類の鍵。空の入力欄は NEW_KEY
  const [addedOpen, setAddedOpen] = useState<string | null>(null)
  const [relNotice, setRelNotice] = useState<string | null>(null)
  const relationsChanged = () => {
    void reloadRelationCatalog().then(() => setRelVersion((v) => v + 1))
  }
  // 取り込み元の絞り込みを変えたら、開いている群・見せている数を戻す（描画中に前の値と比べて合わせる）
  const [seenUnSource, setSeenUnSource] = useState(unSource)
  if (seenUnSource !== unSource) {
    setSeenUnSource(unSource)
    setUnShown(UNSTATED_PAGE)
    setUnOpen(null)
  }

  const toggle = (id: string) => {
    setOpen((cur) => (cur === id ? null : id))
    setMsg(null)
  }
  const errorText = (e: unknown): string => {
    const w = writeFailureOf(e)
    // 409: 裏側が「別の確認の操作が終わってから」「取り込みが終わってから」を分けて返す。detail をそのまま出す
    if (w.kind === 'busy') return e instanceof KnowledgeApiError && e.message && !/^HTTP \d+$/.test(e.message) ? e.message : t('error.busy')
    if (w.kind === 'rejected') return t('error.rejected', { reason: w.reason })
    return t('error.other', { message: w.message })
  }
  const run = async (id: string, job: () => Promise<void>) => {
    setPending(true)
    setMsg(null)
    setNotice(null)
    try {
      await job()
    } catch (e) {
      setMsg({ id, text: errorText(e) })
    } finally {
      setPending(false)
    }
  }
  /** review の `skipped`（変えられなかった分）を、行のそばの 1 行で知らせる。黙って捨てない。 */
  const skippedNote = (res: ReviewResult | void): string | null => {
    if (!res || res.skipped.length === 0) return null
    const r = res.skipped[0].reason
    if (r === 'pair_has_relation' || r === 'cycle' || r === 'unknown') return t(`skipped.${r}`)
    if (/^(already_|not_)/.test(r)) return t('skipped.stale')
    return t('skipped.other', { n: res.skipped.length })
  }
  const noteStore = (store: StoreReport | null | undefined) => setNotice(storeText.noteText(store))
  const review = (id: string, source: string, action: ReviewAction, keys: string[]) =>
    run(id, async () => {
      const res = await api.review(source, action, keys)
      const note = skippedNote(res)
      if (note) setMsg({ id, text: note })
      noteStore(res ? res.store : null)
    })
  const act = (id: string, source: string, key: string, action: ReviewAction) => review(id, source, action, [key])
  const confirmTerm = (term: TermSummary) => review(term.id, term.source, 'confirm', unconfirmedKeys(term))
  const submitAssign = (id: string, source: string, pair: string) =>
    run(id, async () => {
      noteStore((await api.assign(source, pair, form.rel, form.subject)) ?? null)
      setOpen(null)
    })

  const more = (which: Tab, total: number) =>
    total > shown[which] ? (
      <button type="button" className="btn btn--sm btn--ghost gp-more" data-testid="gp-more" onClick={() => setShown((s) => ({ ...s, [which]: s[which] + PAGE_SIZE }))}>
        {t('act.more', { n: total - shown[which] })}
      </button>
    ) : null

  let body: ReactNode
  if (api.status === 'loading') body = <p className="gp-note" role="status">{t('state.loading')}</p>
  else if (api.status === 'error') body = <p className="gp-note" role="alert" data-testid="gp-load-failed">{t('state.loadFailed')}</p>
  else if (api.ledgers.length === 0 && api.failed.length > 0) body = <p className="gp-note" role="alert" data-testid="gp-load-failed">{t('state.failedSources', { names: api.failed.join('、') })}</p>
  else if (api.ledgers.length === 0) {
    body = (
      <div className="gp-empty" data-testid="gp-empty">
        <p>{t('empty.title')}</p>
        <button type="button" className="btn btn--sm btn--accent" data-testid="gp-empty-import" onClick={onOpenImport}>{t('empty.link')}</button>
      </div>
    )
  } else if (tab === 'relations') {
    body =
      relations.length === 0 ? (
        <p className="gp-note">{t('state.none')}</p>
      ) : (
        <>
          <ul className="gp-list" data-testid="gp-relations">
            {firstRows(relations, shown.relations).map((r) => {
              const id = rowId(r)
              return (
                <RelationRow
                  key={id}
                  row={r}
                  lang={lang}
                  open={open === id}
                  onToggle={() => toggle(id)}
                  onAct={(a) => r.key && void act(id, r.source, r.key, a)}
                  pending={pending}
                  error={msg?.id === id ? msg.text : null}
                />
              )
            })}
          </ul>
          {more('relations', relations.length)}
        </>
      )
  } else if (tab === 'terms') {
    body =
      terms.length === 0 ? (
        <p className="gp-note">{t('state.none')}</p>
      ) : (
        <>
          <ul className="gp-list" data-testid="gp-terms">
            {firstRows(terms, shown.terms).map((term) => (
              <TermRow
                key={term.id}
                term={term}
                lang={lang}
                open={open === term.id}
                onToggle={() => toggle(term.id)}
                onConfirmAll={() => void confirmTerm(term)}
                pending={pending}
                error={msg?.id === term.id ? msg.text : null}
              />
            ))}
          </ul>
          {more('terms', terms.length)}
        </>
      )
  } else {
    const onAdded = (label: string) => {
      setAdding(null)
      setRelNotice(t('added.done', { name: label }))
      relationsChanged()
    }
    const section = (
      <>
        {relNotice && <p className="gp-note gp-store-note" role="status" data-testid="gp-added-notice">{relNotice}</p>}
        <UnstatedSection
          load={unstated}
          lang={lang}
          openKey={unOpen}
          onToggle={(k) => setUnOpen((cur) => (cur === k ? null : k))}
          shown={unShown}
          onMore={() => setUnShown((n) => n + UNSTATED_PAGE)}
          add={{
            adding,
            onAdd: (k) => { setAdding(k); setRelNotice(null) },
            adder: (g) => <AddRelation group={g} lang={lang} onAdded={onAdded} onCancel={() => setAdding(null)} />,
          }}
        />
        <AddedSection
          load={added}
          lang={lang}
          openName={addedOpen}
          onToggle={(n) => setAddedOpen((cur) => (cur === n ? null : n))}
          onChanged={relationsChanged}
          adding={adding === NEW_KEY}
          onAdd={() => { setAdding(NEW_KEY); setRelNotice(null) }}
          adder={<AddRelation group={null} lang={lang} onAdded={onAdded} onCancel={() => setAdding(null)} />}
        />
      </>
    )
    body =
      candidates.length === 0 ? (
        <>
          {section}
          <p className="gp-note">{t('state.none')}</p>
        </>
      ) : (
        <>
          {section}
          <p className="gp-note">{t('candidates.note')}</p>
          <ul className="gp-list" data-testid="gp-candidates">
            {firstRows(candidates, shown.candidates).map((r) => {
              const id = `${r.source}|c|${r.pair}`
              return (
                <CandidateRow
                  key={id}
                  row={r}
                  lang={lang}
                  open={open === id}
                  form={form}
                  onOpen={() => {
                    setForm({ rel: '', subject: 'a' })
                    setOpen(id)
                    setMsg(null)
                  }}
                  onForm={setForm}
                  onSubmit={() => void submitAssign(id, r.source, r.pair)}
                  onCancel={() => setOpen(null)}
                  pending={pending}
                  error={msg?.id === id ? msg.text : null}
                />
              )
            })}
          </ul>
          {more('candidates', candidates.length)}
        </>
      )
  }

  const tabBtn = (id: Tab, n: number) => (
    <button type="button" role="tab" data-testid={`gp-tab-${id}`} aria-selected={tab === id} className={tab === id ? 'on' : ''} onClick={() => { setTab(id); setOpen(null); setMsg(null) }}>
      {t(`tab.${id}`, { n })}
    </button>
  )

  return (
    <div className="gp-side" data-testid="gp-side">
      {point && (
        <div data-testid="gp-point">
          <div className="gp-point-head">
            <button type="button" className="btn btn--sm btn--ghost" data-testid="gp-point-close" onClick={onClosePoint}>{t('act.close')}</button>
          </div>
          {point}
        </div>
      )}
      {notice && <p className="gp-note gp-store-note" role="status" data-testid="gp-store-note">{notice}</p>}
      <div className="gp-tabs" role="tablist" aria-label={t('tab.aria')}>
        {tabBtn('relations', relations.length)}
        {tabBtn('terms', terms.length)}
        {tabBtn('candidates', candidates.length)}
      </div>
      {body}
    </div>
  )
}
