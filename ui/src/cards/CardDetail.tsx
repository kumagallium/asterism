// カード詳細（試作 画面 3）。パンくず（1 件/絞り込みのラベル ／ カード）→
// 見出し（カード title）→ タブ「結果／材料／作りかた」。契約メモ §6.3。
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { runCard } from './cardsApi'
import type { CardRef, CardToolResult, SubjectKey } from './cardsApi'
import { resolveCardTitle } from './cardTitle'
import { withFieldLabels } from './builtinFields'
import { defaultViewFor } from './defaultView'
import { GraphView } from './GraphView'
import './pages.css'
import { TableView } from './TableView'
import type { GraphSpec, TableSpec, VegaLiteSpec } from './viewSpec'
import { VegaLiteView } from './VegaLiteView'

export interface CardDetailProps {
  subject: SubjectKey
  /** パンくず・「<ラベル> に聞く」に使う、1 件/絞り込みの表示名（K4: 生の
   *  識別子ではなく、呼び出し側が resolve 済みのラベル）。 */
  breadcrumbLabel: string
  card: CardRef
  onBack: () => void
  onAsk: (question: string) => void
  /** 材料タブの「定義を直す」→ `#/datasets/<dataset_id>/design`。 */
  onEditDefinition: (datasetId: string) => void
}

type DetailTabId = 'result' | 'materials' | 'recipe'
type Translate = (key: string, options?: Record<string, unknown>) => string

export function CardDetail({ subject, breadcrumbLabel, card, onBack, onAsk, onEditDefinition }: CardDetailProps) {
  const { t } = useTranslation('cards')
  const depKey = JSON.stringify({ subject, tool: card.tool, params: card.params })
  // 結果は depKey で紐づけ、then/catch でだけ書き込む — effect の本体で同期的に
  // setState しない（react-hooks/set-state-in-effect。ProvenanceTrace.tsx／
  // CardTile.tsx と同じ流儀）。
  const [fetched, setFetched] = useState<{ key: string; result: CardToolResult | null; error: boolean }>({
    key: '',
    result: null,
    error: false,
  })
  // タブはカードが変わったら 'result' に戻す（React の「prop が変わったら
  // state を調整する」パターン — effect を使わない）。
  const [tab, setTab] = useState<DetailTabId>('result')
  const [tabFor, setTabFor] = useState(card.card_id)
  if (tabFor !== card.card_id) {
    setTabFor(card.card_id)
    setTab('result')
  }
  const [askText, setAskText] = useState('')

  useEffect(() => {
    let cancelled = false
    runCard(subject, card.tool, card.params)
      .then((r) => {
        if (!cancelled) setFetched({ key: depKey, result: r, error: false })
      })
      .catch(() => {
        if (!cancelled) setFetched({ key: depKey, result: null, error: true })
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [depKey])

  const loaded = fetched.key === depKey
  const result = loaded ? fetched.result : null
  const error = loaded && fetched.error

  const titleInfo = resolveCardTitle(card.title)
  const titleText = titleInfo.isKey ? t(titleInfo.value) : titleInfo.value
  const editDatasetId = result?.materials[0]?.dataset_id

  return (
    <div className="cardpage-body">
      <div className="cardpage-head">
        <div>
          <div className="cardpage-crumb">
            <button type="button" className="link-btn" onClick={onBack}>
              {breadcrumbLabel}
            </button>
            {' / '}
            {t('detail.tab_result')}
          </div>
          <h2 className="cardpage-title">{titleText}</h2>
        </div>
        {result && result.shareable !== null && (
          <span className={result.shareable ? 'pill-share pill-share--ok' : 'pill-share pill-share--warn'}>
            {t(result.shareable ? 'page.shareable_yes' : 'page.shareable_no')}
          </span>
        )}
      </div>
      <div className="cardpage-tabs" role="tablist">
        {(['result', 'materials', 'recipe'] as const).map((id) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={tab === id}
            className={tab === id ? 'cardpage-tab cardpage-tab--on' : 'cardpage-tab'}
            onClick={() => setTab(id)}
          >
            {t(id === 'result' ? 'detail.tab_result' : id === 'materials' ? 'materials' : 'recipe')}
          </button>
        ))}
      </div>
      <div className="cardpage-tab-body">
        {error && <p className="ds-empty-note">{t('render_error')}</p>}
        {!error && !result && <p className="ds-empty-note">{t('page.loading')}</p>}
        {!error && result && tab === 'result' && renderResultTab(card, result, titleText, t)}
        {!error && result && tab === 'materials' && renderMaterialsTab(subject, result, t)}
        {!error && result && tab === 'recipe' && renderRecipeTab(card, result, t)}
        {!error && result && tab === 'materials' && editDatasetId && (
          <button
            type="button"
            className="btn btn--ghost btn--sm cardpage-edit-definition"
            onClick={() => onEditDefinition(editDatasetId)}
          >
            {t('detail.edit_definition')}
          </button>
        )}
      </div>
      <div className="cardpage-bar">
        <span className="cardpage-bar-who">{t('page.ask_who', { label: breadcrumbLabel })}</span>
        <input
          className="cardpage-bar-input"
          value={askText}
          onChange={(e) => setAskText(e.target.value)}
          placeholder={t('ask_placeholder')}
        />
        <button
          type="button"
          className="btn btn--soft btn--sm"
          disabled={!askText.trim()}
          onClick={() => {
            onAsk(t('ask.compose', { label: breadcrumbLabel, text: askText }))
            setAskText('')
          }}
        >
          {t('page.ask_submit')}
        </button>
      </div>
    </div>
  )
}

function renderResultTab(card: CardRef, result: CardToolResult, ariaLabel: string, t: Translate) {
  if (card.output_kind === 'flow') {
    // `result.graph` は cardsApi.ts の CardToolResult に合わせて緩い型
    // （nodes/edges: unknown[]）— subject_flow は prov_graph.graph をそのまま
    // 返す契約（契約メモ §3.1）なので、実体は GraphSpec の形をしている。
    const graph = (result.graph ?? { nodes: [], edges: [] }) as GraphSpec
    return <GraphView graph={graph} ariaLabel={ariaLabel} maxHeight={360} />
  }
  // カード詳細は事実カードでも件数を切らない（全件・§2(a)）。
  const view = defaultViewFor(
    { name: card.tool, title: card.title, output_kind: result.output_kind, item: withFieldLabels(card.tool, result.item, t) },
    result.items,
  )
  if (view.lang === 'vega-lite') {
    return <VegaLiteView spec={view.spec as VegaLiteSpec} ariaLabel={ariaLabel} height={360} />
  }
  if (view.lang === 'table') {
    return <TableView spec={view.spec as TableSpec} rows={result.items} ariaLabel={ariaLabel} />
  }
  return null
}

function renderMaterialsTab(subject: SubjectKey, result: CardToolResult, t: Translate) {
  if (result.materials.length === 0) return <p className="ds-empty-note">{t('detail.materials_empty')}</p>
  // Phase 1: materials は個体ごとの出典 IRI を持たない（契約メモ §3.3）ので、
  // 「出典を見る」は 1 件ページのときだけ、その主語の /describe へ向ける。
  const describeHref = subject.kind === 'individual' ? `/describe?iri=${encodeURIComponent(subject.iri)}` : null
  return (
    <div className="table-wrap">
      <table className="jobs-table">
        <thead>
          <tr>
            <th>{t('detail.materials_table_material')}</th>
            <th>{t('detail.materials_table_source')}</th>
            <th>{t('detail.materials_table_snapshot')}</th>
            <th>{t('detail.materials_table_license')}</th>
            <th aria-hidden="true" />
          </tr>
        </thead>
        <tbody>
          {result.materials.map((m, i) => (
            <tr key={`${m.dataset_id}-${m.snapshot ?? ''}-${i}`}>
              <td>{t('detail.materials_row_count', { count: m.count })}</td>
              <td>{m.dataset_id}</td>
              <td>{m.snapshot ?? '—'}</td>
              <td>{m.license ?? '—'}</td>
              <td>
                {describeHref && (
                  <a className="link-btn" href={describeHref} target="_blank" rel="noreferrer">
                    {t('detail.materials_table_link')}
                  </a>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function renderRecipeTab(card: CardRef, result: CardToolResult, t: Translate) {
  // K4: 生の識別子・クエリは「技術情報」として折る。
  return (
    <details className="cardpage-recipe">
      <summary>{t('detail.recipe_tech_info')}</summary>
      <p className="cardpage-recipe-line">
        <b>{t('detail.recipe_tool')}</b>: <code>{card.tool}</code>
      </p>
      <pre className="cardpage-recipe-pre">{JSON.stringify(card.params, null, 2)}</pre>
      <p className="cardpage-recipe-line">
        <b>{t('detail.recipe_sparql')}</b>
      </p>
      <pre className="cardpage-recipe-pre">{result.sparql}</pre>
    </details>
  )
}
