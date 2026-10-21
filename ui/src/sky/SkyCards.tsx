// 会話の先頭に差し込む札（契約 pr8b §1）。星・丸・用語・標準用語を選ぶと「この星について」の札が出て、
// 提案カードと同じ見た目で「◯◯ を見る →」「手で決める ▸」を持つ。右の欄は無くした。
// 問いの輪の一覧は星図の下の 1 行の帯（SkyView）に畳んだ。
import { useTranslation } from 'react-i18next'
import { type ReactNode, useState } from 'react'
import type { Alignment } from '../crosswalkApi'
import { decisionsHash, involves } from '../decide/decisionsModel'
import { resumeDraftHash } from '../vocabQuestion'
import { DecidePanel } from '../decide/DecidePanel'
import { SkySuggestions } from './SkySuggestions'
import type { Suggestion, SuggestionKey } from './suggestApi'
import { decideCandidatesOf, decideSubjectOf } from './skyDecide'
import type { DraftSky, SkyData, SkyQuestion } from './skyApi'
import {
  type DatasetNode,
  type HubNode,
  type KindNode,
  type SkyLayout,
  type StdNode,
  type TermNode,
  termLabel,
} from './skyLayout'

export interface CardProps {
  sky: SkyData
  layout: SkyLayout
  lang: 'ja' | 'en'
  /** 決めたあと・公開したあとに、答えられる問いが増えたとき（欄の上に 1 行・数秒だけ）。 */
  grown: { from: number; to: number } | null
  /** 人が決めた関係（星の横の「決めたこと N 件 →」の数え元。ホームが 1 度だけ取る）。 */
  alignments: readonly Alignment[]
  /** 開いていない下書き（会話の先頭の「たしかめるもの N 件 →」に使う）。 */
  drafts: readonly DraftSky[]
  selectedId: string | null
  selectedQuestion: string | null
  onSelectNode: (id: string | null) => void
  onSelectQuestion: (tool: string | null) => void
  onOpenKind: (iri: string) => void
  onOpenDataset: (id: string) => void
  onOpenTerm: (slug: string) => void
  /** 「手で決める」で決めたあと: 星図を取り直し、用語と星の輪を 1 度だけ出す。 */
  onDecided: (iris: string[]) => void
  /** 公開のあとに機械が数えた「結べそうな線」（名前の完全一致・鍵が無ければ空）。 */
  suggestions: readonly Suggestion[]
  /** 候補を線にした（星図を取り直す・一覧から外す）。 */
  onSuggestionsApplied: (keys: SuggestionKey[]) => void
  /** 候補を外した（一覧から外す）。 */
  onSuggestionsDismissed: (keys: SuggestionKey[]) => void
}

const fmt = (n: number): string => n.toLocaleString('en-US')

export interface PinnedProps extends CardProps {
  /** 下書きの札・提案の線を選んでいるときの札（あれば「この星について」の代わりに出す）。 */
  focusCard: ReactNode | null
  /** 「AI に結ばせる」などの会話の操作が使えるか（手で決める ▸ を開いた状態で出すかの判断）。 */
  aiReady: boolean
}

/** 会話の先頭に差し込む札の束。選んでいるものが無ければ空（何も出さない）。 */
export function SkyPinned(props: PinnedProps) {
  const { t } = useTranslation('sky')
  const node = props.selectedId ? props.layout.nodes.get(props.selectedId) : undefined
  // 「結べそうな線が N 件 →」を押すと、札が一覧に替わる（選択は保つ）。0 件になったら戻る。
  const [showSuggest, setShowSuggest] = useState(false)
  const suggestOpen = showSuggest && props.suggestions.length > 0
  const question = props.selectedQuestion ? props.sky.questions.find((q) => q.tool_name === props.selectedQuestion) : undefined
  return (
    <>
      {props.grown && (
        <p className="sky-grown" role="status">
          {t('grown', { from: props.grown.from, to: props.grown.to })}
        </p>
      )}
      {props.suggestions.length > 0 && (
        <p className="sky-review" data-testid="suggest-link">
          <button type="button" className="link-btn" onClick={() => setShowSuggest(!suggestOpen)}>
            {t('suggest.link', { n: props.suggestions.length })}
          </button>
        </p>
      )}
      {suggestOpen ? (
        <SkySuggestions
          suggestions={props.suggestions}
          onClose={() => setShowSuggest(false)}
          onApplied={props.onSuggestionsApplied}
          onDismissed={props.onSuggestionsDismissed}
        />
      ) : props.focusCard ? (
        props.focusCard
      ) : node && node.type !== 'draft' ? (
        <AboutCard {...props} node={node} />
      ) : question ? (
        <QuestionCard {...props} q={question} />
      ) : null}
      {props.drafts.length > 0 && !props.focusCard && !node && !question && (
        <p className="sky-review" data-testid="draft-review">
          <a className="link-btn" href={props.drafts.length === 1 ? resumeDraftHash(props.drafts[0].draft_id) : '#/questions'}>
            {t('drafts.review', { n: props.drafts.length })}
          </a>
        </p>
      )}
      {/* 手で決める ▸ は星のときだけ。AI 未設定なら開いた状態 */}
      {node?.type === 'kind' && !suggestOpen && !props.focusCard && (
        <ManualDecide key={node.id} {...props} node={node} aiReady={props.aiReady} />
      )}
    </>
  )
}

// ── この星について ──────────────────────────────────────────────
function AboutCard(p: CardProps & { node: NonNullable<ReturnType<SkyLayout['nodes']['get']>> }) {
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
    case 'draft':
      return null
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

function stdText(p: CardProps, iri: string | null | undefined): string | null {
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

/** 星の横の「決めたこと N 件 →」。この種類に掛かる決めたことが 1 件でもあるときだけ出す。
 *  押すと、その種類に掛かるものだけの一覧（件数と同じ範囲）に着く。 */
function DecisionsLink({ iri, alignments }: { iri: string; alignments: readonly Alignment[] }) {
  const { t } = useTranslation('sky')
  const n = alignments.filter((a) => involves(a, iri)).length
  if (n === 0) return null
  return (
    <a className="link-btn sky-decisions-link" href={decisionsHash('all', iri)}>
      {t('about.decisions', { n })}
    </a>
  )
}

function KindAbout(p: CardProps & { node: KindNode }) {
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
        <DecisionsLink iri={node.iri} alignments={p.alignments} />
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
    </section>
  )
}

/** 星（種類）の「手で決める ▸」（6b の「ひとこと決める」を畳んだもの）。相談（会話）の下に置く。
 *  AI が未設定のときは相談が出ないので、開いた状態で出す。用語が無ければ案内。「あとで決める」で閉じる。 */
function ManualDecide(p: CardProps & { node: KindNode; aiReady: boolean }) {
  const { t } = useTranslation('sky')
  const [open, setOpen] = useState(!p.aiReady)
  const subject = decideSubjectOf(p.node)
  if (!subject) return null
  const candidates = decideCandidatesOf(p.sky.terms, subject, p.lang)
  return (
    <details
      className="sky-manual"
      data-testid="manual-decide"
      open={open}
      onToggle={(e) => setOpen(e.currentTarget.open)}
    >
      <summary>{t('decide.manual')}</summary>
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
            onLater={() => setOpen(false)}
            hideTitle
          />
        )}
      </div>
    </details>
  )
}

function DatasetAbout(p: CardProps & { node: DatasetNode }) {
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

/** 問いの答えの印。知識の問いは「知識 N」（データで答えるものがあれば「N が答える」も）。データの問いは従来どおり。 */
function AnswerPills({ q }: { q: SkyQuestion }) {
  const { t } = useTranslation('sky')
  if (q.mode === 'knowledge') {
    const k = q.knowledge ?? 0
    return (
      <>
        <span
          className={`sky-pill ${k > 0 ? 'sky-pill--term' : 'sky-pill--wait'}`}
          title={t('questions.knowledgeTitle', { n: k })}
          data-testid="knowledge-pill"
        >
          {t('about.answeredByKnowledge', { n: k })}
        </span>
        {q.answering > 0 && <span className="sky-pill sky-pill--term">{t('about.answeredBy', { n: q.answering })}</span>}
      </>
    )
  }
  return (
    <span className={`sky-pill ${q.answering > 0 ? 'sky-pill--term' : 'sky-pill--wait'}`}>
      {q.answering > 0 ? t('about.answeredBy', { n: q.answering }) : t('about.notYet')}
    </span>
  )
}

/** この用語がつながる知識の関係を文で（「粒界散乱 は 格子熱伝導率 を下げる」）。 */
function termKnowledgeSentences(sky: SkyData, iri: string, lang: 'ja' | 'en'): { key: string; relation: string; subject: string; object: string; reason: string | null }[] {
  const label = new Map(sky.terms.map((x) => [x.iri, termLabel(x, lang)]))
  return (sky.edges ?? [])
    .filter((e) => (e.from === iri || e.to === iri) && label.has(e.from) && label.has(e.to))
    .map((e) => ({
      key: `${e.from}|${e.relation}|${e.to}`,
      relation: e.relation,
      subject: label.get(e.from) as string,
      object: label.get(e.to) as string,
      reason: e.reason,
    }))
}

function TermAbout(p: CardProps & { node: TermNode }) {
  const { t } = useTranslation('sky')
  const { node } = p
  const term = p.sky.terms.find((x) => x.iri === node.iri)
  const qs = term ? p.sky.questions.filter((q) => q.needs.includes(term.iri)) : []
  const known = term ? termKnowledgeSentences(p.sky, term.iri, p.lang) : []
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
              {q.title} <AnswerPills q={q} />
            </button>
          ))}
        </dd>
        {known.length > 0 && (
          <>
            <dt>{t('about.knowledgeLines')}</dt>
            <dd data-testid="term-knowledge">
              {known.map((k) => (
                <div key={k.key} title={k.reason ?? undefined}>
                  {t(`line.sentence.${k.relation}`, { subject: k.subject, object: k.object })}
                  {k.reason && <span className="sky-meta"> · {k.reason}</span>}
                </div>
              ))}
            </dd>
          </>
        )}
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

function StdAbout(p: CardProps & { node: StdNode }) {
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

function HubAbout(p: CardProps & { node: HubNode }) {
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

// ── 選んだ問い ───────────────────────────────────────────────────
/** 問いを選んだ（用語の札の「答える問い」から）。その問いが要る用語の札を押せる。 */
function QuestionCard(p: CardProps & { q: SkyQuestion }) {
  const { t } = useTranslation('sky')
  return (
    <section className="sky-card" aria-live="polite" data-testid="question-card">
      <h3>{t('about.titleQuestion')}</h3>
      <div className="sky-about-name">{p.q.title}</div>
      <div className="sky-about-actions">
        <AnswerPills q={p.q} />
        <button type="button" className="link-btn" onClick={() => p.onSelectQuestion(null)}>
          {t('about.questionBack')}
        </button>
      </div>
      <dl className="sky-kv">
        <dt>{t('about.questionNeeds')}</dt>
        <dd>
          <TermPills
            terms={p.q.needs.map((iri) => p.layout.nodes.get(`t|${iri}`)).filter((n): n is TermNode => n?.type === 'term')}
            onSelect={p.onSelectNode}
          />
        </dd>
      </dl>
    </section>
  )
}
