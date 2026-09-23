// カード詳細（試作 画面 3）。パンくず（1 件/絞り込みのラベル ／ カード）→
// 見出し（カード title）→ タブ「結果／材料／作りかた」。契約メモ §6.3。
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { KNOWN_LICENSE_IDS, putDatasetLicense, runCard } from './cardsApi'
import type { CardRef, CardMaterial, CardToolResult, SubjectKey } from './cardsApi'
import { resolveCardTitle } from './cardTitle'
import { withFieldLabels } from './builtinFields'
import { defaultViewFor } from './defaultView'
import { GraphView } from './GraphView'
import './pages.css'
import { formatShareReasons } from './shareReasons'
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
  // 「ライセンスを書く」の入力欄（材料タブ・licenses が不明の最初の材料に
  // 対して）。card_id が変わったら忘れる — 下の tab リセットと同じ流儀。
  const [licenseDraft, setLicenseDraft] = useState('')
  const [licenseBusy, setLicenseBusy] = useState(false)
  const [licenseError, setLicenseError] = useState<string | null>(null)

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
  const editMaterial: CardMaterial | undefined = result?.materials[0]
  const editDatasetId = editMaterial?.dataset_id
  const licenseUnknown = !!editMaterial && editMaterial.license == null

  async function handleWriteLicense() {
    if (!editDatasetId || !licenseDraft.trim()) return
    setLicenseBusy(true)
    setLicenseError(null)
    try {
      await putDatasetLicense(editDatasetId, licenseDraft.trim())
      // ライセンスは registry（mie.yaml/metadata.ttl）の正本を書き換えただけ
      // なので、カードを再実行して materials/shareable を作り直させる
      // （runCard は毎回 materials_for で読み直す — ここではキャッシュしない）。
      const refreshed = await runCard(subject, card.tool, card.params)
      setFetched({ key: depKey, result: refreshed, error: false })
      setLicenseDraft('')
    } catch {
      setLicenseError(t('license.write_error'))
    } finally {
      setLicenseBusy(false)
    }
  }

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
          <div className="cardpage-materials-actions">
            <button
              type="button"
              className="btn btn--ghost btn--sm cardpage-edit-definition"
              onClick={() => onEditDefinition(editDatasetId)}
            >
              {t('detail.edit_definition')}
            </button>
            {licenseUnknown && (
              <div className="cardpage-license-form">
                <label className="cardpage-license-label" htmlFor="cardpage-license-input">
                  {t('license.write_label')}
                </label>
                <input
                  id="cardpage-license-input"
                  className="cardpage-license-input"
                  list="cardpage-known-license-ids"
                  value={licenseDraft}
                  onChange={(e) => setLicenseDraft(e.target.value)}
                  placeholder={t('license.write_placeholder')}
                />
                <datalist id="cardpage-known-license-ids">
                  {KNOWN_LICENSE_IDS.map((id) => (
                    <option key={id} value={id} />
                  ))}
                </datalist>
                <button
                  type="button"
                  className="btn btn--soft btn--sm"
                  disabled={!licenseDraft.trim() || licenseBusy}
                  onClick={handleWriteLicense}
                >
                  {t('license.write_submit')}
                </button>
                {licenseError && <span className="cardpage-license-error">{licenseError}</span>}
              </div>
            )}
          </div>
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

/** `material.kind`（'own' | 'open' | 'unknown'）の文言キー。契約メモ §2 の
 *  固定語彙どおり — サーバが返す以外の値は来ない想定だが、来ても保守側で
 *  「不明」に倒す（UI を落とさない）。 */
function materialKindLabel(kind: string, t: Translate): string {
  if (kind === 'own' || kind === 'open') return t(`detail.materials_kind_${kind}`)
  return t('detail.materials_kind_unknown')
}

function renderMaterialsTab(subject: SubjectKey, result: CardToolResult, t: Translate) {
  // Phase 1: materials は個体ごとの出典 IRI を持たない（契約メモ §3.3）ので、
  // 「出典を見る」は 1 件ページのときだけ、その主語の /describe へ向ける。
  const describeHref = subject.kind === 'individual' ? `/describe?iri=${encodeURIComponent(subject.iri)}` : null
  const reasons = result.shareable_reasons ?? []
  return (
    <div className="cardpage-materials">
      {result.materials.length === 0 ? (
        <p className="ds-empty-note">{t('detail.materials_empty')}</p>
      ) : (
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
                  <td>
                    {m.dataset_label}
                    <span className="cardpage-materials-count">
                      {' '}
                      {t('detail.materials_row_count', { count: m.count })}
                    </span>
                  </td>
                  <td>{materialKindLabel(m.kind, t)}</td>
                  <td>{m.snapshot ?? t('detail.materials_unknown')}</td>
                  <td>{m.license ?? t('license.unknown')}</td>
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
      )}
      {result.shareable !== null && (
        <p className={result.shareable ? 'cardview-verdict cardview-verdict--ok' : 'cardview-verdict cardview-verdict--warn'}>
          {result.shareable ? t('detail.verdict_ok') : t('detail.verdict_no', { reasons: formatShareReasons(reasons, t) })}
        </p>
      )}
    </div>
  )
}

function renderRecipeTab(card: CardRef, result: CardToolResult, t: Translate) {
  return (
    <div className="cardpage-recipe-tab">
      <div className="cardpage-bundle">
        <p className="cardpage-bundle-title">{t('bundle.title')}</p>
        <ul className="tree">
          <li>{t('bundle.facts')}</li>
          <li>{t('bundle.tools')}</li>
          <li>{t('bundle.cards')}</li>
          <li>{t('bundle.agent_md')}</li>
          <li>{t('bundle.mcp_json')}</li>
        </ul>
      </div>
      {/* K4: 生の識別子・クエリは「技術情報」として折る。 */}
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
    </div>
  )
}
