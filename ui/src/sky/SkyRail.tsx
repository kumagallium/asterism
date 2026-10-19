// 星図の右の欄: 1 枚だけ。点（星・丸・用語・標準用語）を選んでいれば「この星について」、
// 選んでいなければ「知りたいこと」（問いの輪＋題＋札）。「ひとこと決める」は星の欄の下。
import { useTranslation } from 'react-i18next'
import { useEffect, useState } from 'react'
import { getAlignments } from '../crosswalkApi'
import { DecidePanel } from '../decide/DecidePanel'
import { decideCandidatesOf, decideSubjectOf } from './skyDecide'
import type { SkyData, SkyQuestion } from './skyApi'
import {
  type DatasetNode,
  type HubNode,
  type KindNode,
  type SkyLayout,
  type StdNode,
  type TermNode,
} from './skyLayout'

export interface RailProps {
  sky: SkyData
  layout: SkyLayout
  lang: 'ja' | 'en'
  /** 決めたあと・公開したあとに、答えられる問いが増えたとき（欄の上に 1 行・数秒だけ）。 */
  grown: { from: number; to: number } | null
  selectedId: string | null
  selectedQuestion: string | null
  onSelectNode: (id: string | null) => void
  onSelectQuestion: (tool: string | null) => void
  onOpenKind: (iri: string) => void
  onOpenDataset: (id: string) => void
  onOpenTerm: (slug: string) => void
  onOpenQuestions: () => void
  /** 「そう決める」のあと: 星図を取り直し、用語と星の輪を 1 度だけ出す。 */
  onDecided: (iris: string[]) => void
}

const fmt = (n: number): string => n.toLocaleString('en-US')

export function SkyRail(props: RailProps) {
  const { t } = useTranslation('sky')
  const node = props.selectedId ? props.layout.nodes.get(props.selectedId) : undefined
  return (
    <aside className="sky-rail">
      {props.grown && (
        <p className="sky-grown" role="status">
          {t('grown', { from: props.grown.from, to: props.grown.to })}
        </p>
      )}
      {node ? <AboutCard {...props} node={node} /> : <QuestionsCard {...props} />}
    </aside>
  )
}

// ── この星について ──────────────────────────────────────────────
function AboutCard(p: RailProps & { node: NonNullable<ReturnType<SkyLayout['nodes']['get']>> }) {
  const node = p.node
  switch (node.type) {
    case 'kind':
      return <KindAbout {...p} node={node} />
    case 'dataset':
      return <DatasetAbout {...p} node={node} />
    case 'term':
      return <TermAbout {...p} node={node} />
    case 'std':
      return <StdAbout {...p} node={node} />
    case 'hub':
      return <HubAbout {...p} node={node} />
  }
}

/** 点（星・丸）につながる用語の札。 */
function termsOfPoint(layout: SkyLayout, id: string): TermNode[] {
  const out: TermNode[] = []
  for (const e of layout.edges) {
    if (e.kind === 'member' && e.from === id) {
      const n = layout.nodes.get(e.to)
      if (n?.type === 'term') out.push(n)
    }
  }
  return out
}

function stdText(p: RailProps, iri: string | null | undefined): string | null {
  if (!iri) return null
  const s = p.sky.standards.find((x) => x.iri === iri)
  return s ? `≡ ${s.prefix}:${s.label}` : null
}

function TermPills({ terms, onSelect }: { terms: TermNode[]; onSelect: (id: string) => void }) {
  const { t } = useTranslation('sky')
  if (terms.length === 0) return <span className="sky-pill sky-pill--wait">{t('about.noTerm')}</span>
  return (
    <span className="sky-pills">
      {terms.map((n) => (
        <button key={n.id} type="button" className="sky-pill sky-pill--term" onClick={() => onSelect(n.id)}>
          {n.label}
        </button>
      ))}
    </span>
  )
}

/** 星の横の「決めたこと N 件 →」。この種類に掛かる決めたことが 1 件でもあるときだけ出す。 */
function DecisionsLink({ iri, sky }: { iri: string; sky: SkyData }) {
  const { t } = useTranslation('sky')
  const [n, setN] = useState(0)
  useEffect(() => {
    let off = false
    getAlignments()
      .then((r) => !off && setN(r.alignments.filter((a) => a.source === iri || a.target === iri).length))
      .catch(() => !off && setN(0))
    return () => {
      off = true
    }
    // 星図が取り直されたら（決めたあと）数え直す。
  }, [iri, sky])
  if (n === 0) return null
  return (
    <a className="link-btn sky-decisions-link" href="#/decisions">
      {t('about.decisions', { n })}
    </a>
  )
}

function KindAbout(p: RailProps & { node: KindNode }) {
  const { t } = useTranslation('sky')
  const { node } = p
  const ds = p.sky.datasets.find((d) => d.id === node.datasetId)
  const kind = ds?.kinds.find((k) => k.iri === node.iri)
  const std = stdText(p, kind?.standard)
  return (
    <section className="sky-card" aria-live="polite">
      <h3>{t('about.titleKind')}</h3>
      <div className="sky-about-name">{node.label}</div>
      <div className="sky-about-actions">
        <button type="button" className="btn btn--primary btn--sm" onClick={() => p.onOpenKind(node.iri)}>
          {t('about.viewKind', { name: node.label })}
        </button>
        <DecisionsLink iri={node.iri} sky={p.sky} />
      </div>
      <dl className="sky-kv">
        <dt>{t('about.dataset')}</dt>
        <dd>
          {ds ? (
            <button type="button" className="sky-linkrow" onClick={() => p.onOpenDataset(ds.id)}>
              {ds.label}
            </button>
          ) : (
            node.datasetId
          )}
        </dd>
        <dt>{t('about.count')}</dt>
        <dd>{t('unit.count', { n: fmt(node.count) })}</dd>
        <dt>{t('about.terms')}</dt>
        <dd>
          <TermPills terms={termsOfPoint(p.layout, node.id)} onSelect={p.onSelectNode} />
        </dd>
        {std && (
          <>
            <dt>{t('about.standard')}</dt>
            <dd className="sky-std">{std}</dd>
          </>
        )}
      </dl>
      <DecideSlot key={node.id} {...p} node={node} />
    </section>
  )
}

/** 星（種類）の「ひとこと決める」。用語が無ければ案内、「あとで決める」で畳む（選択は保つ）。 */
function DecideSlot(p: RailProps & { node: KindNode }) {
  const { t } = useTranslation('sky')
  const [folded, setFolded] = useState(false)
  const subject = decideSubjectOf(p.node)
  if (!subject || folded) return <div className="sky-decide-slot" data-slot="decide" />
  const candidates = decideCandidatesOf(p.sky.terms, subject, p.lang)
  return (
    <div className="sky-decide-slot" data-slot="decide">
      {candidates.length === 0 ? (
        <>
          <p className="sky-hint">{t('decide.noTerms')}</p>
          <a className="link-btn" href="#/questions">
            {t('decide.openQuestions')}
          </a>
        </>
      ) : (
        <DecidePanel
          subject={subject}
          candidates={candidates}
          onDecided={(info) => p.onDecided([info.target.iri, info.subject.iri])}
          onLater={() => setFolded(true)}
        />
      )}
    </div>
  )
}

function DatasetAbout(p: RailProps & { node: DatasetNode }) {
  const { t } = useTranslation('sky')
  const { node } = p
  const ds = p.sky.datasets.find((d) => d.id === node.datasetId)
  const shown = (ds?.kinds ?? []).slice(0, 12)
  return (
    <section className="sky-card" aria-live="polite">
      <h3>{t('about.titleDataset')}</h3>
      <div className="sky-about-name">{node.label}</div>
      <div className="sky-about-actions">
        <button type="button" className="btn btn--primary btn--sm" onClick={() => p.onOpenDataset(node.datasetId)}>
          {t('about.viewDataset', { name: node.label })}
        </button>
      </div>
      <dl className="sky-kv">
        <dt>{t('about.kinds')}</dt>
        <dd>{t('unit.kinds', { n: node.kindCount })}</dd>
        <dt>{t('about.count')}</dt>
        <dd>{t('unit.count', { n: fmt(node.count) })}</dd>
        <dt>{t('about.terms')}</dt>
        <dd>
          <TermPills terms={termsOfPoint(p.layout, node.id)} onSelect={p.onSelectNode} />
        </dd>
        <dt>{t('about.kindsIn')}</dt>
        <dd>
          {shown.map((k) => (
            <button key={k.iri} type="button" className="sky-linkrow" onClick={() => p.onOpenKind(k.iri)}>
              {k.label} <span className="sky-meta">{t('unit.count', { n: fmt(k.count) })}</span>
            </button>
          ))}
          {(ds?.kinds.length ?? 0) > shown.length && <span className="sky-meta">…</span>}
        </dd>
      </dl>
    </section>
  )
}

function TermAbout(p: RailProps & { node: TermNode }) {
  const { t } = useTranslation('sky')
  const { node } = p
  const term = p.sky.terms.find((x) => x.iri === node.iri)
  const qs = term ? p.sky.questions.filter((q) => q.needs.includes(term.iri)) : []
  const std = stdText(p, node.stdId ? node.stdId.slice(2) : null)
  const empty = node.memberIds.length === 0
  return (
    <section className="sky-card" aria-live="polite">
      <h3>{t('about.titleTerm')}</h3>
      <div className="sky-about-name">
        {node.label}
        <span className={`sky-pill ${empty ? 'sky-pill--wait' : 'sky-pill--term'}`}>
          {empty ? t('about.termNoStar') : t('about.termStars', { n: node.memberIds.length })}
        </span>
        {node.termKind === 'property' && <span className="sky-meta">{t('bands.property')}</span>}
      </div>
      {term && (
        <div className="sky-about-actions">
          <button type="button" className="btn btn--primary btn--sm" onClick={() => p.onOpenTerm(term.slug)}>
            {t('about.viewTerm', { name: node.label })}
          </button>
        </div>
      )}
      <dl className="sky-kv">
        <dt>{t('about.kindsIn')}</dt>
        <dd>
          {node.memberIds.length === 0 && <span className="sky-meta">{t('about.termNoStarHint')}</span>}
          {node.memberIds.map((id) => {
            const m = p.layout.nodes.get(id)
            if (!m) return null
            const dsLabel =
              m.type === 'kind' ? p.sky.datasets.find((d) => d.id === m.datasetId)?.label : undefined
            return (
              <button key={id} type="button" className="sky-pill sky-pill--data" onClick={() => p.onSelectNode(id)}>
                {m.label}
                {dsLabel ? `（${dsLabel}）` : ''}
              </button>
            )
          })}
        </dd>
        <dt>{t('about.questions')}</dt>
        <dd>
          {qs.length === 0 && t('about.noQuestion')}
          {qs.map((q) => (
            <button key={q.tool_name} type="button" className="sky-linkrow" onClick={() => p.onSelectQuestion(q.tool_name)}>
              {q.title}{' '}
              <span className="sky-meta">
                {q.answering > 0 ? t('about.answeredBy', { n: q.answering }) : t('about.notYet')}
              </span>
            </button>
          ))}
        </dd>
        {std && (
          <>
            <dt>{t('about.standard')}</dt>
            <dd className="sky-std">{std}</dd>
          </>
        )}
      </dl>
    </section>
  )
}

function StdAbout(p: RailProps & { node: StdNode }) {
  const { t } = useTranslation('sky')
  const { node } = p
  const bound = [...p.layout.edges]
    .filter((e) => (e.kind === 'termStd' || e.kind === 'kindStd') && e.to === node.id)
    .map((e) => p.layout.nodes.get(e.from))
    .filter((n): n is NonNullable<typeof n> => !!n)
  return (
    <section className="sky-card" aria-live="polite">
      <h3>{t('about.titleStd')}</h3>
      <div className="sky-about-name">
        <span className="sky-std">
          {node.prefix}:{node.label}
        </span>
      </div>
      <p className="sky-hint">{t('about.stdHint')}</p>
      {bound.length > 0 && (
        <dl className="sky-kv">
          <dt>{t('about.stdBound')}</dt>
          <dd className="sky-pills">
            {bound.map((n) => (
              <button
                key={n.id}
                type="button"
                className={`sky-pill ${n.type === 'term' ? 'sky-pill--term' : 'sky-pill--data'}`}
                onClick={() => p.onSelectNode(n.id)}
              >
                {n.label}
              </button>
            ))}
          </dd>
        </dl>
      )}
    </section>
  )
}

function HubAbout(p: RailProps & { node: HubNode }) {
  const { t } = useTranslation('sky')
  const { node } = p
  return (
    <section className="sky-card" aria-live="polite">
      <h3>{t('about.titleHub')}</h3>
      <div className="sky-about-name">{node.label}</div>
      <p className="sky-hint">{t('about.hubHint')}</p>
      <dl className="sky-kv">
        <dt>{t('about.members')}</dt>
        <dd className="sky-pills">
          {node.memberIds.map((id) => {
            const m = p.layout.nodes.get(id)
            if (!m) return null
            return (
              <button key={id} type="button" className="sky-pill sky-pill--data" onClick={() => p.onSelectNode(id)}>
                {m.label}
              </button>
            )
          })}
        </dd>
      </dl>
    </section>
  )
}

// ── 知りたいこと ────────────────────────────────────────────────
function Ring({ q, large }: { q: SkyQuestion; large?: boolean }) {
  const { t } = useTranslation('sky')
  const cls = `sky-ring${large ? ' sky-ring--lg' : ''}`
  if (q.answering <= 0 || q.total_datasets <= 0) {
    return <span className={`${cls} sky-ring--none`} title={t('questions.ringNone')} />
  }
  const pct = Math.min(100, Math.round((100 * q.answering) / q.total_datasets))
  return (
    <span
      className={cls}
      style={{ '--p': pct } as React.CSSProperties}
      title={t('questions.ring', { n: q.answering, total: q.total_datasets })}
    />
  )
}

function QuestionsCard(p: RailProps) {
  const { t } = useTranslation('sky')
  const qs = p.sky.questions
  return (
    <section className="sky-card">
      <h3>{t('questions.head')}</h3>
      {qs.length === 0 ? (
        <div className="sky-meta">{t('questions.empty')}</div>
      ) : (
        <div>
          {qs.map((q) => (
            <div key={q.tool_name}>
              <button
                type="button"
                className={`sky-qrow${p.selectedQuestion === q.tool_name ? ' sky-qrow--on' : ''}`}
                aria-pressed={p.selectedQuestion === q.tool_name}
                onClick={() => p.onSelectQuestion(p.selectedQuestion === q.tool_name ? null : q.tool_name)}
              >
                <Ring q={q} />
                <span className="sky-qrow-title">{q.title}</span>
                <span className={`sky-pill ${q.answering > 0 ? 'sky-pill--term' : 'sky-pill--wait'}`}>
                  {q.answering > 0 ? t('about.answeredBy', { n: q.answering }) : t('about.notYet')}
                </span>
              </button>
              {/* 選んだ問いが要る用語の札（押すとその用語の星へ）。 */}
              {p.selectedQuestion === q.tool_name && (
                <div className="sky-qneeds">
                  <TermPills
                    terms={q.needs
                      .map((iri) => p.layout.nodes.get(`t|${iri}`))
                      .filter((n): n is TermNode => n?.type === 'term')}
                    onSelect={p.onSelectNode}
                  />
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      <div style={{ marginTop: 'var(--space-2)' }}>
        <button type="button" className="btn btn--sm" onClick={p.onOpenQuestions}>
          {t('questions.all')}
        </button>
      </div>
    </section>
  )
}
