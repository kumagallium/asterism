import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { dismiss } from '../decide/decideApi'
import { effectAfterPublish } from '../decide/decide'
import { type PublishQuestion, questionsOfTerm } from '../decide/publishEffect'
import { fit as fetchFit, type FitCandidate } from '../vocabApi'
import {
  type KindFitPick,
  type KindRelation,
  kindFitCandidates,
  loadClassIris,
} from './kindFit'

// ⑤ 種類の箱の下に出す、決定論の**当てはめ提案**（表示のみ）。
// ADR upper-structure-shared-terms §2.5.2・§2.5.3: 種類の名前が既にある語・他データの
// 種類と完全一致したときだけ「既にある『〇〇』の一種にしますか」と出す。受けても線は
// 書かない（公開のときに、人が受けたものだけ線になる）。候補が無ければ何も出さない。

const DEBOUNCE_MS = 400
const MAX_SHOWN = 2

// 種類の IRI の集合は、種類ごとの箱が何枚あっても短い間は 1 回だけ引く。「ことば」画面で
// 語を足して戻ってきたときに古いままにならないよう、少しで捨てる。
const CLASS_IRIS_TTL_MS = 30_000
let classIrisCache: { at: number; value: Promise<Set<string>> } | null = null
function classIris(): Promise<Set<string>> {
  const now = Date.now()
  if (!classIrisCache || now - classIrisCache.at > CLASS_IRIS_TTL_MS) {
    classIrisCache = { at: now, value: loadClassIris() }
  }
  return classIrisCache.value
}

export function KindFitSuggestion({
  label,
  ownIri,
  pick,
  onPick,
}: {
  /** 人が付けた種類の表示名。 */
  label: string
  /** この種類自身の（公開される）IRI。自分自身は候補に出さない。 */
  ownIri: string | null
  /** 人が既に受けた当てはめ。 */
  pick: KindFitPick | undefined
  onPick: (pick: KindFitPick | null) => void
}) {
  const { t } = useTranslation()
  // 候補は、引いたときの入力（key）といっしょに持つ。入力が変わった瞬間に前の候補を
  // 出さなくする（打ち直しの間、前の名前への「一種にする」が押せてしまうのを防ぐ）。
  const inputKey = `${label.trim()}\u0000${ownIri ?? ''}`
  const [found, setFound] = useState<{ key: string; list: FitCandidate[] }>({ key: '', list: [] })
  // 外した提案（この画面の中で）。次に引くときは API が除く。
  const [dropped, setDropped] = useState<ReadonlySet<string>>(new Set())
  const candidates = (found.key === inputKey ? found.list : []).filter((c) => !dropped.has(c.term))
  // 主語の鍵: 公開される IRI（無ければ名前）。「当てはまるものは無い」を覚えるのに使う。
  const subjectKey = ownIri ?? `kind:${label.trim()}`
  const [dismissErr, setDismissErr] = useState('')
  // 結ぶ先の問い（公開すると答えるデータセットが 1 つ増える見込みを言う）。
  const effectTerm = pick?.term ?? ''
  const [effects, setEffects] = useState<Record<string, PublishQuestion[]>>({})
  const wanted = (pick ? [pick.term] : candidates.slice(0, MAX_SHOWN).map((c) => c.term)).join('|')
  useEffect(() => {
    let stale = false
    for (const term of wanted ? wanted.split('|') : []) {
      void questionsOfTerm(term).then((list) => {
        if (!stale) setEffects((prev) => ({ ...prev, [term]: list }))
      })
    }
    return () => {
      stale = true
    }
  }, [wanted])
  const effectLine = (term: string) =>
    term in effects ? effectAfterPublish(effects[term], t) : null

  async function onDismiss(c: FitCandidate) {
    setDismissErr('')
    try {
      await dismiss(subjectKey, c.term)
      setDropped((prev) => new Set([...prev, c.term]))
    } catch (e) {
      setDismissErr(e instanceof Error ? e.message : String(e))
    }
  }

  useEffect(() => {
    const l = label.trim()
    if (!l) return
    const key = `${l}\u0000${ownIri ?? ''}`
    let stale = false
    const timer = setTimeout(() => {
      void Promise.all([fetchFit(l, '', subjectKey), classIris()])
        .then(([list, iris]) => {
          if (!stale) setFound({ key, list: kindFitCandidates(list, iris, ownIri) })
        })
        .catch(() => {
          // 提案は補助。引けなければ何も出さない（行き止まりにしない）。
          if (!stale) setFound({ key, list: [] })
        })
    }, DEBOUNCE_MS)
    return () => {
      stale = true
      clearTimeout(timer)
    }
  }, [label, ownIri, subjectKey])

  if (pick) {
    return (
      <p className="skeleton-evidence-line skeleton-evidence-muted" data-testid="kind-fit-accepted">
        <span role="status">
          {t(
            pick.relation === 'equivalentClass'
              ? 'skeletongate:kindFit.acceptedSame'
              : 'skeletongate:kindFit.acceptedSub',
            { label: pick.label },
          )}
        </span>{' '}
        <button type="button" className="btn btn--ghost btn--sm" onClick={() => onPick(null)}>
          {t('skeletongate:kindFit.clear')}
        </button>
        {effectLine(effectTerm) && (
          <>
            <br />
            <span data-testid="kind-fit-effect">{effectLine(effectTerm)}</span>
          </>
        )}
      </p>
    )
  }
  if (!label.trim() || candidates.length === 0) return null
  const accept = (c: FitCandidate, relation: KindRelation) =>
    onPick({ term: c.term, relation, label: c.label })
  return (
    <div className="skeleton-evidence-line skeleton-evidence-muted" data-testid="kind-fit">
      {candidates.slice(0, MAX_SHOWN).map((c) => (
        <div key={c.term}>
          <span>{t('skeletongate:kindFit.ask', { kind: label.trim(), label: c.label })}</span>{' '}
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => accept(c, 'subClassOf')}
          >
            {t('skeletongate:kindFit.sub')}
          </button>{' '}
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => accept(c, 'equivalentClass')}
          >
            {t('skeletongate:kindFit.same')}
          </button>{' '}
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => void onDismiss(c)}>
            {t('skeletongate:kindFit.none')}
          </button>
          {effectLine(c.term) && (
            <div data-testid="kind-fit-effect">{effectLine(c.term)}</div>
          )}
        </div>
      ))}
      {dismissErr && <p role="alert">{dismissErr}</p>}
      <p>{t('skeletongate:kindFit.note')}</p>
    </div>
  )
}
