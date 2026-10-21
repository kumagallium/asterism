import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { type RelKind, type ThemeDraft, REL_KINDS, conceptName } from '../themes/themeDraft'
import { relId } from '../workspace/workspaceState'
import { nameOfKey, relationSentenceOf } from '../workspace/workspaceText'
import type { DraftSky } from './skyApi'
import type { HomeWorkspace } from './useHomeWorkspace'

// 会話の先頭に差し込む札のうち、下書き（提案の札・提案の線）のもの（契約 pr8b §2）。7b の作業場の
// 「選んだ札・線の欄」（A・関係・B のプルダウン・ここを広げる・外す）をそのまま移した。
//   ・線を押す → A・関係・B の札（直す／この関係を外す）。線の端のドラッグは星図の上でも効く
//   ・提案の札を押す → その概念の文（ここを広げる／外す）
//   ・開いていない下書きの札を押す → 続きから開く

type TFn = (k: string, o?: Record<string, unknown>) => string

/** 線の A・関係・B を直す札。`index` は下書きの関係の添字。 */
export function EdgeCard({
  ws,
  draft,
  index,
  lang,
  onSelectEdge,
}: {
  ws: HomeWorkspace
  draft: ThemeDraft
  index: number
  lang: string
  /** 直したあと、直した線を選び直す（線の ID は A・関係・B で決まるので、直すと変わる）。 */
  onSelectEdge: (id: string) => void
}) {
  const { t } = useTranslation()
  const tf = t as unknown as TFn
  const row = draft.relations[index]
  const [edit, setEdit] = useState<{ id: string; from: string; rel: RelKind; to: string } | null>(null)
  if (!row) return null
  const id = relId(row)
  const cur = edit && edit.id === id ? edit : { id, from: row.from, rel: row.rel, to: row.to }
  const changed = relId(cur) !== id
  const stds = Object.keys(draft.standards)
  const nameOf = (key: string) => nameOfKey(draft, key, lang, tf)
  const sentence = relationSentenceOf(draft, row, lang, tf)
  return (
    <section className="sky-card sky-card--session thm-focus" data-testid="focus-edge">
      <p className="thm-focus-sentence">{sentence}</p>
      {row.reason && <p className="thm-reason">{row.reason}</p>}
      <div className="thm-focus-edit">
        <label>
          <span>{t('themes:chat.edgeA')}</span>
          <select value={cur.from} aria-label={t('themes:chat.edgeA')} data-testid="edge-from" onChange={(e) => setEdit({ ...cur, from: e.target.value })}>
            {draft.concepts.map((c) => (
              <option key={c.key} value={c.key}>
                {conceptName(c, lang)}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>{t('themes:chat.edgeRel')}</span>
          <select value={cur.rel} aria-label={t('themes:chat.edgeRel')} data-testid="edge-rel" onChange={(e) => setEdit({ ...cur, rel: e.target.value as RelKind })}>
            {REL_KINDS.map((r) => (
              <option key={r} value={r}>
                {t(`themes:map.pillTitle.${r}`)}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>{t('themes:chat.edgeB')}</span>
          <select value={cur.to} aria-label={t('themes:chat.edgeB')} data-testid="edge-to" onChange={(e) => setEdit({ ...cur, to: e.target.value })}>
            {draft.concepts.map((c) => (
              <option key={c.key} value={c.key}>
                {conceptName(c, lang)}
              </option>
            ))}
            {stds.map((k) => (
              <option key={k} value={k}>
                {nameOf(k)}
              </option>
            ))}
            {!draft.concepts.some((c) => c.key === cur.to) && !stds.includes(cur.to) && <option value={cur.to}>{nameOf(cur.to)}</option>}
          </select>
        </label>
      </div>
      <div className="thm-focus-actions">
        <button
          type="button"
          className="btn btn--sm"
          disabled={!changed || ws.busy || cur.from === cur.to}
          data-testid="edge-apply"
          onClick={() =>
            void ws.editRelation(index, cur).then((ok) => {
              if (!ok) return
              setEdit(null)
              onSelectEdge(relId(cur))
            })
          }
        >
          {t('themes:chat.edgeApply')}
        </button>
        <button type="button" className="btn btn--ghost btn--sm" disabled={ws.busy} onClick={() => void ws.removeRelationRow(index)}>
          {t('themes:chat.edgeRemove')}
        </button>
      </div>
      <span className="vocab-field-hint">{t('themes:chat.edgeDragHint')}</span>
    </section>
  )
}

/** 提案の札を押したときの札: その概念に触れる関係の文（押すと線が選ばれる）・ここを広げる・外す。 */
export function ConceptCard({
  ws,
  draft,
  conceptKey,
  lang,
  onSelectEdge,
}: {
  ws: HomeWorkspace
  draft: ThemeDraft
  conceptKey: string
  lang: string
  onSelectEdge: (id: string) => void
}) {
  const { t } = useTranslation()
  const tf = t as unknown as TFn
  const row = draft.concepts.find((c) => c.key === conceptKey)
  if (!row) return null
  const touching = draft.relations.flatMap((r, i) => (r.from === row.key || r.to === row.key ? [{ r, i }] : []))
  const thin = ws.pending.concepts.includes(row.key)
  return (
    <section className="sky-card sky-card--session thm-focus" data-testid="focus-concept">
      <p className="thm-focus-name">
        {conceptName(row, lang)}
        <span className="thm-card-p-kind"> {t(`themes:kind.${row.kind}`)}</span>
        {thin && <span className="thm-card-p-kind"> · {t('themes:map.tagProposal')}</span>}
      </p>
      {row.reason && <p className="thm-reason">{row.reason}</p>}
      {touching.length === 0 ? (
        <p className="vocab-field-hint">{t('themes:chat.noRelations')}</p>
      ) : (
        <ul className="thm-focus-rels">
          {touching.map(({ r, i }) => (
            <li key={`${i}-${relId(r)}`}>
              <button type="button" className="link-btn" onClick={() => onSelectEdge(relId(r))}>
                {relationSentenceOf(draft, r, lang, tf)}
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="thm-focus-actions">
        {thin && (
          <button type="button" className="btn btn--sm" disabled={ws.busy} data-testid="focus-accept" onClick={() => ws.acceptConcept(row.key)}>
            {t('themes:chat.rowYes')}
          </button>
        )}
        <button
          type="button"
          className="btn btn--sm"
          disabled={!ws.aiReady || ws.busy}
          title={ws.aiReady ? undefined : t('themes:map.expandOff')}
          data-testid="focus-expand"
          onClick={() => ws.expand(conceptName(row, lang))}
        >
          {t('themes:map.expand')}
        </button>
        <button type="button" className="btn btn--ghost btn--sm" disabled={ws.busy} onClick={() => void ws.removeConceptRow(row.key)}>
          {t('themes:map.remove')}
        </button>
      </div>
    </section>
  )
}

/** 開いていない下書きの札を押したときの札: 続きから開く・捨てる（確かめてから。古い下書きをここから片づけられる）。 */
export function OtherDraftCard({
  draft,
  name,
  onOpen,
  onDiscard,
}: {
  draft: DraftSky
  name: string
  onOpen: () => void
  onDiscard: () => void
}) {
  const { t } = useTranslation('sky')
  const [discarding, setDiscarding] = useState(false)
  return (
    <section className="sky-card sky-card--session" aria-live="polite" data-testid="draft-about">
      <h3>{t('about.titleProposal')}</h3>
      <div className="sky-about-name">
        {name}
        <span className="sky-pill sky-pill--wait">{t('about.draftMark')}</span>
      </div>
      <dl className="sky-kv">
        <dt>{t('about.draftTheme')}</dt>
        <dd>{draft.theme_label}</dd>
        <dt>{t('about.draftConcepts')}</dt>
        <dd>{t('about.draftCount', { n: draft.concepts.length })}</dd>
        {draft.questions.length > 0 && (
          <>
            <dt>{t('about.draftQuestions')}</dt>
            <dd>
              {draft.questions.map((q) => (
                <div key={q}>{q}</div>
              ))}
            </dd>
          </>
        )}
      </dl>
      {discarding ? (
        <div className="sky-about-actions" role="group" aria-label={t('about.discardAsk')}>
          <span className="sky-hint">{t('about.discardAsk')}</span>
          <button type="button" className="btn btn--sm" onClick={onDiscard} data-testid="draft-discard-yes">
            {t('about.discardYes')}
          </button>
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => setDiscarding(false)}>
            {t('about.discardNo')}
          </button>
        </div>
      ) : (
        <div className="sky-about-actions">
          <button type="button" className="btn btn--primary btn--sm" onClick={onOpen} data-testid="draft-open">
            {t('about.draftOpen')}
          </button>
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => setDiscarding(true)} data-testid="draft-discard">
            {t('about.draftDiscard')}
          </button>
        </div>
      )}
    </section>
  )
}
